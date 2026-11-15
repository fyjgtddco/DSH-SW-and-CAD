"""COM 门卫的隔离验收。使用模拟 COM 对象。"""
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest

from monitor import Coordinator, Fault
import com_gate
from com_gate import build_part
from unittest.mock import patch


class Model:
    def __init__(self, app):
        self.app = app
        self.model = self
        self.path = ""
        self.dirty = True

    def GetPathName(self):
        return self.path

    def GetTitle(self):
        return "Part1"

    def GetSaveFlag(self):
        return self.dirty

    def _has_solid_body(self):
        return True

    def begin_sketch(self, *_):
        pass

    def circle(self, *_):
        pass

    def end_sketch(self):
        pass

    def extrude(self, *_):
        pass

    def save(self, path):
        if self.app.fail_save:
            return {"ok": False}
        Path(path).write_bytes(b"synthetic SW file")
        self.path, self.dirty = path, False
        return {"ok": True, "saved": True}


class App:
    def __init__(self):
        self.doc = None
        self.fail_save = False
        self.ignore_close = False

    def GetDocumentCount(self):
        return int(self.doc is not None)

    @property
    def ActiveDoc(self):
        return self.doc

    def GetProcessID(self):
        return 102

    def CloseDoc(self, title):
        assert self.doc.GetTitle() == title
        if not self.ignore_close:
            self.doc = None

    def new(self, app, material):
        assert app is self and not self.doc
        self.doc = Model(self)
        return self.doc


STEPS = [{"op": "sketch", "args": ["Top Plane"]}, {"op": "circle", "args": [0, 0, 5]},
         {"op": "end_sketch", "args": []}, {"op": "extrude", "args": [2]}]


class ComTests(unittest.TestCase):
    def test_save_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App()
            output = Path(tmp) / "one.SLDPRT"
            report = build_part(app, app.new, lambda: [102], output, "Q235", STEPS)
            self.assertEqual(report["documents"], 0)
            self.assertIsNone(app.doc)
            for failure, code in (("fail_save", "SAVE_FAILED"), ("ignore_close", "CLOSE_NOT_CONFIRMED")):
                app = App()
                setattr(app, failure, True)
                with self.assertRaisesRegex(Fault, code):
                    build_part(app, app.new, lambda: [102], Path(tmp) / (failure + ".SLDPRT"), "Q235", STEPS)
                self.assertIsNotNone(app.doc)

    def test_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "blocked.py"
            script.write_text("import time; time.sleep(60)", encoding="utf-8")
            start = time.monotonic()
            with patch.object(com_gate, "__file__", str(script)):
                with self.assertRaisesRegex(Fault, "COM_TIMEOUT"):
                    com_gate.subprocess_worker({"root": tmp}, tmp, 0.25)
            self.assertLess(time.monotonic() - start, 5)

    def test_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = {}
            recovered = Event()
            def worker(req):
                if result.get("fail"):
                    raise Fault("COM_TIMEOUT")
                out = Path(req["file"])
                out.write_bytes(b"synthetic SW file")
                from monitor import digest
                return {"pid": 102, "documents": 0, "sha256": digest(out), "closed": True}

            worker.recover_owned = lambda _: recovered.set()
            m = Coordinator(root / "db", root, com_runner=worker, com_probe=lambda: None)
            try:
                call = lambda op, **d: m.call(op, {"epoch": "e"} | d)
                call("attach", drained=True)
                call("plan", task="T", root=tmp, rooms=[
                    {"name": r, "type": "model", "parts": [r]} for r in ("A", "B")])
                a, b = [dict(task="T", room=r, session=r, ticket=r) for r in ("A", "B")]
                call("request", **a)
                call("request", **b)
                a.update(lease=call("claim", **a)["lease"])
                with self.assertRaisesRegex(Fault, "COM_GATE_REQUIRED"):
                    call("begin", **a, command="direct")
                payload = dict(part="A", gen=0, command="part1", file="A.SLDPRT", material="Q235", steps=STEPS)
                with self.assertRaisesRegex(Fault, "COM_OPERATION_DENIED"):
                    call("com-build", **a, **(payload | {"steps": [{"op": "Dispatch", "args": []}]}))
                def settled(scope, command):
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        out = call("com-status", **scope, command=command, timeout=1)
                        if out["state"] != "running":
                            return out
                        time.sleep(0.01)
                    self.fail("worker did not settle")

                self.assertEqual(call("com-build", **a, **payload)["state"], "running")
                out = settled(a, "part1")
                self.assertTrue(out["closed"])
                self.assertEqual(call("com-build", **a, **payload), out)
                self.assertEqual(call("wait-lock", **b, timeout=0)["state"], "reserved")
                b.update(lease=call("claim", **b)["lease"])
                result["fail"] = True
                call("com-build", **b, **(payload | dict(part="B", command="part2", file="B.SLDPRT")))
                failed = settled(b, "part2")
                self.assertEqual(failed["state"], "failed")
                self.assertTrue(recovered.is_set() and failed["recovered"])
                self.assertIsNone(call("board", task="T")["sw"]["owner"])

                def unsafe(_):
                    raise Fault("UNMANAGED_SW_RUNNING")
                worker.recover_owned = unsafe
                b = b | {"ticket": "B2"}
                call("request", **b)
                b.update(lease=call("claim", **b)["lease"])
                call("com-build", **b, **(payload | dict(part="B", gen=1, command="part3", file="B2.SLDPRT")))
                self.assertEqual(settled(b, "part3")["state"], "quarantined")
                self.assertTrue(call("board", task="T")["quarantined"])
            finally:
                m.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
