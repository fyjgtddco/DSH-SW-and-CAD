"""Allowlisted SolidWorks worker. The monitor owns and launches this process."""
import contextlib
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from monitor import Fault, digest, need


OPERATIONS = {
    "sketch": ("begin_sketch", (str,)),
    "circle": ("circle", (float, float, float)),
    "rectangle": ("rect", (float, float, float, float)),
    "line": ("line", (float, float, float, float)),
    "end_sketch": ("end_sketch", ()),
    "extrude": ("extrude", (float,)),
    "cut": ("cut", (float,)),
    "gear": ("gear", (float, int, float, float)),
    "helix_gear": ("helix_gear", (float, int, float, float, float, float)),
}


def validated_steps(steps):
    need(isinstance(steps, list) and 1 <= len(steps) <= 500, "COM_STEPS_INVALID")
    sketch = False
    features = 0
    for step in steps:
        need(isinstance(step, dict) and set(step) == {"op", "args"}
             and isinstance(step["op"], str) and step["op"] in OPERATIONS, "COM_OPERATION_DENIED")
        op, args = step["op"], step["args"]
        types = OPERATIONS[op][1]
        need(isinstance(args, list) and len(args) == len(types), "COM_ARGUMENTS_INVALID")
        for val, kind in zip(args, types):
            if kind is str:
                need(val in ("Top Plane", "Front Plane", "Right Plane"), "COM_PLANE_DENIED")
            elif kind is int:
                need(type(val) is int and 6 <= val <= 200, "GEAR_TEETH_INVALID")
            else:
                need(type(val) in (int, float) and -1_000_000 <= val <= 1_000_000
                     and math.isfinite(val), "COM_NUMBER_INVALID")
        if op == "sketch":
            need(not sketch, "SKETCH_ALREADY_OPEN")
            sketch = True
        elif op == "end_sketch":
            need(sketch, "SKETCH_NOT_OPEN")
            sketch = False
        elif op in ("circle", "rectangle", "line"):
            need(sketch, "SKETCH_NOT_OPEN")
            if op != "line":
                need(all(v > 0 for v in args[2:]), "COM_DIMENSION_INVALID")
        elif op in ("gear", "helix_gear"):
            need(not sketch and args[0] > 0 and args[2] > 0 and args[-1] >= 0, "COM_GEAR_INVALID")
            if op == "helix_gear":
                need(0 < abs(args[3]) < 60 and 10 <= args[4] <= 35, "COM_GEAR_ANGLE_INVALID")
            features += 1
        else:
            need(not sketch and args[0] > 0, "COM_FEATURE_INVALID")
            features += int(op == "extrude")
    need(not sketch and features > 0, "COM_SOLID_FEATURE_REQUIRED")
    return steps


def member(obj, attr):
    value = getattr(obj, attr)
    return value() if callable(value) else value


def same_instance(app, list_pids, pid):
    need(list_pids() == [pid] and int(member(app, "GetProcessID")) == pid,
         "SW_INSTANCE_CHANGED_OR_MULTIPLE")


def build_part(app, new, list_pids, output, material, steps):
    validated_steps(steps)
    pid = int(member(app, "GetProcessID"))
    same_instance(app, list_pids, pid)
    need(member(app, "GetDocumentCount") == 0 and app.ActiveDoc is None, "SW_DOCUMENTS_ALREADY_OPEN")
    need(not os.path.lexists(output), "OUTPUT_ALREADY_EXISTS")
    model = new(app, material=material)
    doc = model.model
    title = member(doc, "GetTitle")
    need(isinstance(title, str) and bool(title.strip()), "SW_DOCUMENT_TITLE_MISSING")

    def sole_document(expected_path):
        same_instance(app, list_pids, pid)
        active = app.ActiveDoc
        need(active is not None and member(app, "GetDocumentCount") == 1
             and member(active, "GetTitle") == member(doc, "GetTitle")
             and member(active, "GetPathName") == expected_path
             and member(doc, "GetPathName") == expected_path, "SW_DOCUMENT_SCOPE_CHANGED")

    for step in steps:
        sole_document("")
        method = getattr(model, OPERATIONS[step["op"]][0])
        result = method(*step["args"])
        need(result is not False and (not isinstance(result, dict) or result.get("ok") is True),
             "COM_OPERATION_FAILED: " + step["op"])
        del result
    sole_document("")
    need(model._has_solid_body(), "SW_NO_SOLID_BODY")
    need(not os.path.lexists(output), "OUTPUT_ALREADY_EXISTS")
    result = model.save(str(output))
    need(result.get("ok") is True and result.get("saved") is True and not output.is_symlink(),
         "SAVE_FAILED")
    sha = digest(output)
    path = member(doc, "GetPathName")
    need(bool(path) and Path(path).resolve() == output.resolve() and not member(doc, "GetSaveFlag"),
         "SAVE_NOT_CONFIRMED")
    sole_document(path)
    title = member(doc, "GetTitle")
    need(isinstance(title, str) and bool(title.strip()), "SW_DOCUMENT_TITLE_MISSING")
    app.CloseDoc(title)
    # The broker sets UserControl before opening a document so the session stays observable.
    same_instance(app, list_pids, pid)
    need(member(app, "GetDocumentCount") == 0 and app.ActiveDoc is None, "CLOSE_NOT_CONFIRMED")
    need(digest(output) == sha, "MODEL_CHANGED_DURING_CLOSE")
    return {"pid": pid, "documents": 0, "sha256": sha, "closed": True}


def list_sw_pids():
    from ctypes import Structure, WinDLL, POINTER, sizeof
    from ctypes.wintypes import BOOL, DWORD, HANDLE, LONG, WCHAR
    from ctypes import byref, get_last_error, c_void_p, c_size_t
    class Entry(Structure):
        _fields_ = [("size", DWORD), ("usage", DWORD), ("pid", DWORD), ("heap", c_size_t),
                    ("module", DWORD), ("threads", DWORD), ("parent", DWORD), ("priority", LONG),
                    ("flags", DWORD), ("exe", WCHAR * 260)]
    k = WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = (DWORD, DWORD)
    k.CreateToolhelp32Snapshot.restype = HANDLE
    for fn in (k.Process32FirstW, k.Process32NextW):
        fn.argtypes = (HANDLE, POINTER(Entry))
        fn.restype = BOOL
    k.CloseHandle.argtypes = (HANDLE,)
    handle = k.CreateToolhelp32Snapshot(2, 0)
    need(handle not in (None, c_void_p(-1).value), "SW_PROCESS_SCAN_FAILED")
    entry = Entry()
    entry.size = sizeof(entry)
    pids = []
    try:
        need(k.Process32FirstW(handle, byref(entry)), "SW_PROCESS_SCAN_FAILED")
        while True:
            if entry.exe.casefold() == "sldworks.exe":
                pids.append(int(entry.pid))
            if not k.Process32NextW(handle, byref(entry)):
                need(get_last_error() == 18, "SW_PROCESS_SCAN_FAILED")
                return sorted(pids)
    finally:
        k.CloseHandle(handle)


@contextlib.contextmanager
def machine_mutex(key=r"Global\DSH_SW_COM_Gate_v1"):
    from ctypes import WinDLL
    from ctypes.wintypes import HANDLE, BOOL, DWORD, LPCWSTR
    k = WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = (HANDLE, BOOL, LPCWSTR)
    k.CreateMutexW.restype = HANDLE
    k.WaitForSingleObject.argtypes = (HANDLE, DWORD)
    k.WaitForSingleObject.restype = DWORD
    k.CloseHandle.argtypes = k.ReleaseMutex.argtypes = (HANDLE,)
    handle = k.CreateMutexW(None, False, key)
    need(handle, "SW_MACHINE_MUTEX_UNAVAILABLE")
    acquired = False
    try:
        acquired = k.WaitForSingleObject(handle, 0) in (0, 0x80)
        need(acquired, "SW_MACHINE_BUSY")
        yield
    finally:
        if acquired:
            k.ReleaseMutex(handle)
        k.CloseHandle(handle)


def real_worker(req, tools):
    need(os.name == "nt", "COM_REQUIRES_WINDOWS")
    with machine_mutex():
        pids = list_sw_pids()
        need(len(pids) <= 1, "MULTIPLE_SW_PROCESSES")
        pid = req["pid"]
        need(pids == [pid], "SW_INSTANCE_CHANGED_OR_MULTIPLE")
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            while True:
                need(list_sw_pids() == [pid], "SW_INSTANCE_CHANGED_OR_MULTIPLE")
                try:
                    app = win32com.client.GetActiveObject("SldWorks.Application")
                    break
                except pythoncom.com_error:
                    time.sleep(0.25)
            same_instance(app, list_sw_pids, pid)
            need(member(app, "GetDocumentCount") == 0 and app.ActiveDoc is None, "SW_DOCUMENTS_ALREADY_OPEN")
            app.UserControl = True
            sys.path.insert(0, tools)
            os.environ.update(DSH_ROOM=req["room"], DSH_STATE_DIR=tools,
                              DSH_TASK_WORK_DIR=req["root"], DSH_WORK_DIR=req["root"],
                              DSH_ENGINEERING_ROOT=str(Path(tools).parent))
            import swapi
            return build_part(app, swapi.new_part, list_sw_pids, Path(req["file"]),
                              req["material"], req["steps"])
        finally:
            pythoncom.CoUninitialize()


def subprocess_worker(req, tools, timeout):
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as result:
        try:
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), tools],
                                  input=json.dumps(req, ensure_ascii=False), stdout=result,
                                  stderr=log, cwd=req.get("root") or tools, encoding="utf-8",
                                  timeout=timeout, env=os.environ | {"PYTHONUTF8": "1",
                                                                      "PYTHONIOENCODING": "utf-8"},
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise Fault("COM_TIMEOUT: SW 端状态待人工核对") from None
        log.seek(max(0, log.seek(0, os.SEEK_END) - 2500))
        tail = log.read().decode("utf-8", "replace").strip()
        result.seek(0)
        try:
            out = json.loads(result.read(65536))
        except ValueError:
            raise Fault("COM_WORKER_PROTOCOL: " + tail) from None
    need(proc.returncode == 0 and out.get("ok") is True, str(out.get("error") or tail))
    return out["report"]


class OwnedSolidWorks:
    """Only the service-owned process handle can be used to terminate SolidWorks."""
    def __init__(self, exe, tools, timeout):
        self.exe, self.tools, self.timeout = exe, tools, timeout
        self.process = None

    def _check(self):
        need(os.name == "nt", "COM_REQUIRES_WINDOWS")
        pids = list_sw_pids()
        if self.process is not None and self.process.poll() is None:
            need(pids == [self.process.pid], "SW_INSTANCE_CHANGED_OR_MULTIPLE")
        else:
            need(not pids, "UNMANAGED_SW_RUNNING")

    def probe(self):
        self._check()
        need(self.process is None or self.process.poll() is not None, "OWNED_SW_STILL_RUNNING")

    def recover_owned(self, error):
        if not error.startswith("COM_TIMEOUT:"):
            # A failed save or unexpected document may still be recoverable by an operator.
            self.probe()
            return
        # Never terminate by image name or by a PID supplied in a request.
        if self.process is not None and self.process.poll() is None:
            self._check()
            self.process.kill()
            self.process.wait(timeout=15)
        self.probe()

    def __call__(self, req):
        import importlib.util
        need(importlib.util.find_spec("win32com") is not None, "PYWIN32_REQUIRED")
        self._check()
        if self.process is None or self.process.poll() is not None:
            self.process = subprocess.Popen([self.exe], stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            cwd=str(Path(self.exe).parent))
        result = subprocess_worker(req | {"pid": self.process.pid}, self.tools, self.timeout)
        self._check()
        need(result["pid"] == self.process.pid, "SW_INSTANCE_CHANGED_OR_MULTIPLE")
        return result


if __name__ == "__main__":
    try:
        req = json.load(sys.stdin)
        with contextlib.redirect_stdout(sys.stderr):
            report = real_worker(req, sys.argv[1])
        print(json.dumps({"ok": True, "report": report}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e) or type(e).__name__}, ensure_ascii=False))
        sys.exit(1)
