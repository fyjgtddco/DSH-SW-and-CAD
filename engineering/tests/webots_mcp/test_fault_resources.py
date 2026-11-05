"""Real Windows process faults; these are fixtures, never Webots/physics evidence."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
RECORDS = []


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("Fixture deadline expired")


class OwnedFixture:
    """Only test-created suspended processes enter this private Job before resume."""
    def __init__(self, code, cwd):
        import win32api
        import win32con
        import win32file
        import win32job
        import win32pipe
        import win32process
        import win32security

        self.job = win32job.CreateJobObject(None, "S05-fixture-" + uuid4().hex)
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        self.process = None
        self.stdin = None
        self.readers = []
        self.buffers = [bytearray(), bytearray()]
        self.counts = [0, 0]
        self.errors = []
        self.closed = False
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.bInheritHandle = True
        pipes = [win32pipe.CreatePipe(attributes, 0) for _ in range(3)]
        child_ends = [pipes[0][0], pipes[1][1], pipes[2][1]]
        parent_ends = [pipes[0][1], pipes[1][0], pipes[2][0]]
        for handle in parent_ends:
            win32api.SetHandleInformation(handle, win32con.HANDLE_FLAG_INHERIT, 0)
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = child_ends
        try:
            self.process, thread, self.pid, _ = win32process.CreateProcess(
                None, subprocess.list2cmdline([sys.executable, "-u", "-c", code]), None,
                None, True, win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW,
                None, str(cwd), startup)
            try:
                win32job.AssignProcessToJobObject(self.job, self.process)
                self.creation_time = str(win32process.GetProcessTimes(self.process)["CreationTime"])
                win32process.ResumeThread(thread)
            except BaseException:
                win32api.TerminateProcess(self.process, 90)
                raise
            finally:
                thread.Close()
        except BaseException:
            for handle in parent_ends:
                handle.Close()
            self.job.Close()
            if self.process:
                self.process.Close()
            raise
        finally:
            for handle in child_ends:
                handle.Close()
        self.stdin = parent_ends[0]
        for index, read in enumerate(parent_ends[1:]):
            def drain(index=index, read=read):
                try:
                    while True:
                        try:
                            _, chunk = win32file.ReadFile(read, 16384)
                        except win32api.error as error:
                            if error.winerror == 109:
                                break
                            raise
                        if not chunk:
                            break
                        self.counts[index] += len(chunk)
                        self.buffers[index].extend(chunk)
                        del self.buffers[index][:-4096]
                except BaseException as error:
                    self.errors.append(repr(error))
                finally:
                    read.Close()
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            self.readers.append(reader)

    def send(self, command):
        import win32file
        win32file.WriteFile(self.stdin, command.encode("ascii") + b"\n")

    def disconnect(self):
        self.stdin.Close()
        self.stdin = None

    def active(self):
        import win32job
        return win32job.QueryInformationJobObject(
            self.job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]

    def exit_code(self):
        import win32process
        return win32process.GetExitCodeProcess(self.process)

    def close(self):
        import win32event
        import win32job
        if self.closed:
            return
        try:
            if self.stdin:
                self.disconnect()
            win32job.TerminateJobObject(self.job, 91)
            eventually(lambda: self.active() == 0)
            self.cleanup_active = self.active()
        finally:
            self.job.Close()  # KILL_ON_JOB_CLOSE also protects a failing cleanup assertion.
            win32event.WaitForSingleObject(self.process, 1000)
            self.process.Close()
            self.closed = True
        for reader in self.readers:
            reader.join(5)
            if reader.is_alive():
                raise AssertionError("Fixture pipe reader did not exit")
        if self.errors:
            raise AssertionError(self.errors)
        RECORDS.append({"event": "fixture-cleanup", "provenance": "real-process-fixture",
                        "pid": self.pid, "cleanup_active": self.cleanup_active,
                        "readers_alive": sum(r.is_alive() for r in self.readers),
                        "physical_verdict": "unknown"})


class ResourceAssertions(unittest.TestCase):
    def api(self):
        self.assertTrue((TOOLS / "webots_mcp" / "fault_resources.py").is_file(),
                        "S05 resource sampler and fault assertions are not implemented")
        return importlib.import_module("webots_mcp.fault_resources")

    def test_budget_rejects_missing_nonfinite_and_unsupported_values(self):
        api = self.api()
        result = api.budget_check({"wall_time_s": 2, "private_bytes": None},
                                  {"wall_time_s": 1, "private_bytes": 10})
        self.assertEqual(result, {"within_budget": False, "exceeded": ["wall_time_s"],
                                  "unknown": ["private_bytes"]})
        self.assertEqual(api.budget_check({"wall_time_s": 1}, {"wall_time_s": 1}),
                         {"within_budget": True, "exceeded": [], "unknown": []})
        self.assertIsNone(api.budget_check({"gpu_percent": None}, {"gpu_percent": 90})["within_budget"])
        for invalid in (-1, float("nan"), float("inf"), True, "2"):
            with self.assertRaises(ValueError):
                api.budget_check({"wall_time_s": 1}, {"wall_time_s": invalid})
        for invalid in (-1, float("nan"), float("inf"), True, "2"):
            self.assertEqual(api.budget_check({"wall_time_s": invalid}, {"wall_time_s": 2})["unknown"],
                             ["wall_time_s"])
        with self.assertRaises(ValueError):
            api.budget_check({}, {})
        with self.assertRaises(ValueError):
            api.budget_check({}, {"typo": 2})

    def test_uncertain_action_assertion_rejects_exitcode_and_cancel_as_success(self):
        api = self.api()
        result = {"ok": False, "error_code": "OUTCOME_UNKNOWN", "execution_state": "unknown",
                  "operation_id": "step-1", "session_generation": 1,
                  "completed_steps": 3, "sim_time": .096}
        api.assert_unknown_action(result, operation_id="step-1", session_generation=1,
                                  completed_steps=3, sim_time=.096)
        for patch in ({"ok": True}, {"ok": 0}, {"error_code": None}, {"execution_state": "completed"},
                      {"operation_id": "step-2"}, {"session_generation": 2},
                      {"completed_steps": 0}, {"sim_time": 0},
                      {"session_generation": True}, {"sim_time": float("nan")}):
            with self.assertRaises(AssertionError):
                api.assert_unknown_action({**result, **patch}, operation_id="step-1",
                                          session_generation=1, completed_steps=3, sim_time=.096)
        with self.assertRaises(AssertionError):
            api.assert_unknown_action({"ok": False, "error_code": "OUTCOME_UNKNOWN", "execution_state": "unknown",
                "operation_id": "step-1", "session_generation": 1}, operation_id="step-1",
                session_generation=1, completed_steps=None, sim_time=None)


@unittest.skipUnless(sys.platform == "win32", "Windows Job fixture requires Windows and pywin32")
class RealProcessFaults(unittest.TestCase):
    api = ResourceAssertions.api

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, code):
        fixture = OwnedFixture(code, self.root)
        self.addCleanup(fixture.close)
        return fixture

    def record(self, event, fixture, **data):
        RECORDS.append({"event": event, "provenance": "real-process-fixture",
                        "core_ready": False, "physical_verdict": "unknown",
                        "pid": fixture.pid, "creation_time": fixture.creation_time, **data})

    def test_job_sampler_accounts_for_live_tree_and_reports_cpu_interval(self):
        api = self.api()
        fixture = self.fixture("import time; data=bytearray(8*1024*1024); print('ready',flush=True); "
                               "deadline=time.monotonic()+20\nwhile time.monotonic()<deadline: pass")
        eventually(lambda: b"ready" in fixture.buffers[0])
        first = api.sample_job(fixture.job)
        time.sleep(.15)
        second = api.sample_job(fixture.job, previous=first)
        self.assertEqual(second["process_count"], len(second["processes"]))
        self.assertGreaterEqual(second["process_count"], 1)
        self.assertTrue(second["complete"])
        self.assertGreater(second["working_set_bytes"], 8*1024*1024)
        self.assertGreater(second["private_bytes"], 8*1024*1024)
        self.assertGreater(second["cpu_time_s"], first["cpu_time_s"])
        self.assertGreater(second["cpu_percent_one_core"], 0)
        self.assertIsNone(first["cpu_percent_one_core"])
        self.assertIsNone(second["gpu_percent"])
        launcher = next(p for p in second["processes"] if p["pid"] == fixture.pid)
        self.assertEqual(launcher["creation_time"], fixture.creation_time)
        self.assertEqual(Path(launcher["exe"]).name.lower(), "python.exe")
        self.assertTrue(api.budget_check(second, {"private_bytes": 2**40})["within_budget"])
        with self.assertRaises(ValueError):
            api.sample_job(fixture.job, previous={**first, "job_handle": first["job_handle"] + 1})
        with self.assertRaises(ValueError):
            api.sample_job(fixture.job, previous={**first, "cpu_time_s": 1e12})
        with self.assertRaises(ValueError):
            api.sample_job(None)
        with self.assertRaises(ValueError):
            api.sample_job(0)
        self.record("resource-sample", fixture, samples=[first, second])

    def test_launcher_exit_zero_does_not_hide_child_or_kill_foreign_instance(self):
        api = self.api()
        foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def close_foreign():
            foreign.terminate()
            foreign.wait(5)
        self.addCleanup(close_foreign)
        child = "import time; print('child-ready',flush=True); time.sleep(30)"
        fixture = self.fixture("import subprocess,sys; subprocess.Popen([sys.executable,'-u','-c'," + repr(child) + "])")
        eventually(lambda: fixture.exit_code() == 0 and b"child-ready" in fixture.buffers[0])
        sample = api.sample_job(fixture.job)
        self.assertGreaterEqual(sample["process_count"], 1)
        self.assertNotIn(fixture.pid, [p["pid"] for p in sample["processes"]])
        self.assertNotIn(foreign.pid, [p["pid"] for p in sample["processes"]])
        fixture.close()
        self.assertEqual(fixture.cleanup_active, 0)
        self.assertIsNone(foreign.poll())
        self.record("launcher-exit-zero-child-alive", fixture, launcher_exit_code=0,
                    sample=sample, cleanup_active=0, foreign_survived=True)

    def test_timeout_and_cancel_ack_do_not_rollback_dispatched_action(self):
        api = self.api()
        code = ("import sys,time; from pathlib import Path; "
                "Path('actions').write_text('step-1\\n'); print('dispatched',flush=True); "
                "sys.stdin.readline(); print('cancel-ack',flush=True); time.sleep(30)")
        fixture = self.fixture(code)
        eventually(lambda: b"dispatched" in fixture.buffers[0])
        started = time.monotonic()
        time.sleep(.05)  # Explicit fixture deadline, no worker response is fabricated.
        fixture.send("cancel")
        eventually(lambda: b"cancel-ack" in fixture.buffers[0])
        sample = api.sample_job(fixture.job)
        self.assertGreaterEqual(sample["process_count"], 1)
        self.assertEqual((self.root / "actions").read_text(), "step-1\n")
        self.assertLess(time.monotonic() - started, 5)
        self.record("timeout-cancel-ack-action-still-live", fixture, sample=sample,
                    action_count=1, cancellation_requested=True, action_outcome="unknown")

    def test_pipe_disconnect_preserves_side_effect_and_unknown_outcome(self):
        api = self.api()
        fixture = self.fixture("import sys,time; from pathlib import Path; "
            "Path('actions').write_text('step-1\\n'); print('dispatched',flush=True); "
            "sys.stdin.read(); print('eof',flush=True); time.sleep(30)")
        eventually(lambda: b"dispatched" in fixture.buffers[0])
        fixture.disconnect()
        eventually(lambda: b"eof" in fixture.buffers[0])
        sample = api.sample_job(fixture.job)
        self.assertGreaterEqual(sample["process_count"], 1)
        self.assertEqual((self.root / "actions").read_text(), "step-1\n")
        self.record("pipe-disconnect-after-dispatch", fixture, sample=sample,
                    action_count=1, action_outcome="unknown")

    def test_crash_and_dual_pipe_flood_are_bounded_and_observable(self):
        self.api()
        fixture = self.fixture("import os,sys; print('x'*200000,flush=True); "
            "print('y'*200000,file=sys.stderr,flush=True); os._exit(37)")
        eventually(lambda: fixture.exit_code() == 37 and all(n > 200000 for n in fixture.counts))
        self.assertTrue(all(len(buffer) <= 4096 for buffer in fixture.buffers))
        eventually(lambda: fixture.active() == 0)
        self.record("crash-dual-pipe-flood", fixture, exit_code=37,
                    drained_bytes=fixture.counts.copy(), retained_bytes=[len(b) for b in fixture.buffers])


if __name__ == "__main__":
    record_path = None
    if "--record" in sys.argv:
        index = sys.argv.index("--record")
        record_path = Path(sys.argv[index + 1])
        del sys.argv[index:index + 2]
    program = unittest.main(exit=False, verbosity=2)
    if record_path:
        record_path.write_text(json.dumps({"provenance": "real-process-fixture",
            "physical_verdict": "unknown", "records": RECORDS,
            "tests_run": program.result.testsRun, "failures": len(program.result.failures),
            "errors": len(program.result.errors), "skipped": len(program.result.skipped)},
            ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    sys.exit(not program.result.wasSuccessful())
