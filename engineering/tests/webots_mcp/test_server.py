"""Run with unittest; only the explicitly enabled worker is synthetic."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))


class PeripheralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "simple.wbt").write_text("#VRML_SIM R2025a utf8\n")
        self.ctx = {"task_id": "task-1", "room_id": "room-1", "request_id": "req-1"}

    def server(self, fake=None):
        self.assertTrue((TOOLS / "webots_mcp" / "server.py").is_file(),
                        "MCP peripheral server has not been implemented")
        return importlib.import_module("webots_mcp.server").PeripheralServer(
            self.root, input_root=self.root, fake_worker=fake)

    def call(self, server, tool, **args):
        return asyncio.run(server.dispatch(tool, {**self.ctx, **args}))

    def launch(self, server):
        return self.call(server, "sim_launch", operation_id="launch-1", world_path="simple.wbt")

    def test_default_never_launches_or_claims_observations(self):
        server = self.server()
        result = self.launch(server)
        self.assertEqual(result["error_code"], "CORE_NOT_IMPLEMENTED")
        self.assertEqual(result["execution_state"], "not_started")
        self.assertEqual(result["provenance"], "unavailable")
        self.assertFalse(result["core_ready"])
        for field in ("run_id", "job_id", "session_generation", "sim_time", "completed_steps"):
            self.assertIsNone(result[field])
        self.assertFalse((self.root / "webots_jobs.sqlite3").exists())

    def test_input_boundaries_reject_before_fake_dispatch(self):
        server = self.server("complete")
        for patch in ({"task_id": "../escape"}, {"room_id": ""}, {"request_id": "a/b"},
                      {"operation_id": ".."}, {"world_path": "../outside.wbt"},
                      {"world_path": str(self.root / "simple.wbt")},
                      {"world_path": "missing.wbt"}, {"unexpected": True}):
            args = {**self.ctx, "operation_id": "launch-2", "world_path": "simple.wbt", **patch}
            denied = asyncio.run(server.dispatch("sim_launch", args))
            self.assertFalse(denied["ok"], patch)
            self.assertEqual(denied["execution_state"], "not_started")
        self.assertFalse(self.call(server, "sim_no_such_tool")["ok"])

    def test_fake_jobs_preserve_identity_without_real_ready_or_pass(self):
        server = self.server("complete")
        launched = self.launch(server)
        self.assertTrue(launched["ok"])
        self.assertEqual(launched["provenance"], "fake")
        self.assertFalse(launched["core_ready"])
        self.assertIsNone(launched["sim_time"])
        self.assertIsNone(launched["completed_steps"])
        identity = {key: launched[key] for key in ("run_id", "session_generation")}
        for patch in ({"task_id": "other"}, {"room_id": "other"},
                      {"run_id": "no-such-run"}, {"session_generation": 2}):
            result = self.call(server, "sim_step", **{**identity, "operation_id": "step-1", "steps": 1, **patch})
            self.assertFalse(result["ok"], patch)
        for patch in ({"job_id": "missing"}, {"room_id": "other"}):
            result = self.call(server, "sim_job", **{**identity, "job_id": launched["job_id"], **patch})
            self.assertFalse(result["ok"], patch)
        step = self.call(server, "sim_step", **identity, operation_id="step-1", steps=2)
        self.assertEqual(step["execution_state"], "completed")
        self.assertNotEqual(step["job_state"], "READY")
        self.assertIsNone(step["completed_steps"])
        retry = self.call(server, "sim_step", **identity, operation_id="step-1", steps=3)
        self.assertEqual(retry["error_code"], "OPERATION_REUSED")
        cancel = self.call(server, "sim_cancel", **identity, job_id=step["job_id"], operation_id="cancel-1")
        self.assertEqual(cancel["error_code"], "CORE_NOT_IMPLEMENTED")
        foreign_job = self.call(server, "sim_cancel", **identity, job_id="no-such-job", operation_id="cancel-2")
        self.assertEqual(foreign_job["error_code"], "JOB_NOT_FOUND")

    def test_error_timeout_unknown_are_distinct_and_do_not_hide_uncertainty(self):
        for mode, code, state in (("error", "WORKER_ERROR", "not_started"),
                                  ("timeout", "OUTCOME_UNKNOWN", "unknown"),
                                  ("unknown", "OUTCOME_UNKNOWN", "unknown")):
            result = self.launch(self.server(mode))
            self.assertFalse(result["ok"])
            self.assertEqual((result["error_code"], result["execution_state"]), (code, state))
            self.assertEqual(result["provenance"], "fake")
            self.assertIsNotNone(result["job_id"])
            self.assertIsNone(result["completed_steps"])
            self.assertFalse(result["core_ready"])

    def test_limits_generation_nonfinite_and_unknown_arguments(self):
        server = self.server("complete")
        launched = self.launch(server)
        identity = {key: launched[key] for key in ("run_id", "session_generation")}
        for steps in (0, 1001, True, 1.5):
            self.assertFalse(self.call(server, "sim_step", **identity, operation_id="s-1", steps=steps)["ok"])
        self.assertFalse(self.call(server, "sim_motor", **identity, operation_id="motor-1",
                                   device="motor", velocity=float("nan"))["ok"])
        for generation in (0, True, -1):
            self.assertFalse(self.call(server, "sim_scene", **{**identity, "session_generation": generation})["ok"])
        encoded = json.dumps(self.call(server, "sim_status"), allow_nan=False)
        self.assertNotIn('"READY"', encoded)

    def test_invalid_identity_is_not_reflected_as_valid_or_non_json(self):
        result = self.call(self.server(), "sim_scene", run_id="../escape", session_generation=float("nan"))
        self.assertEqual(result["error_code"], "INVALID_ARGUMENT")
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["session_generation"])
        json.dumps(result, allow_nan=False)

    def test_log_page_limit_rejects_too_small_before_evidence_access(self):
        result = self.call(self.server(), "sim_evidence", run_id="run-1", limit=1)
        self.assertEqual(result["error_code"], "INVALID_ARGUMENT")

    def test_all_identity_fields_reject_a_trailing_newline_before_dispatch(self):
        server = self.server("complete")
        for field in ("task_id", "room_id", "request_id", "operation_id"):
            args = {**self.ctx, "operation_id": "launch-1", "world_path": "simple.wbt", field: "id\n"}
            result = asyncio.run(server.dispatch("sim_launch", args))
            self.assertEqual(result["error_code"], "INVALID_ARGUMENT", field)
        launched = self.launch(server)
        for field in ("run_id", "job_id"):
            args = {**self.ctx, "run_id": launched["run_id"], "job_id": launched["job_id"],
                    "session_generation": 1, field: "id\n"}
            result = asyncio.run(server.dispatch("sim_job", args))
            self.assertEqual(result["error_code"], "INVALID_ARGUMENT", field)

    def test_resolved_link_cannot_escape_the_input_root(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)
            (target / "foreign.wbt").write_text("#VRML_SIM R2025a utf8\n")
            link = self.root / "escape"
            if os.name == "nt":
                command = "New-Item -ItemType Junction -Path $env:S03_TEST_LINK -Target $env:S03_TEST_TARGET | Out-Null"
                subprocess.run(["powershell", "-NoProfile", "-Command", command],
                    env={**os.environ, "S03_TEST_LINK": str(link), "S03_TEST_TARGET": str(target)},
                    capture_output=True, check=True, timeout=10)
            else:
                link.symlink_to(target, target_is_directory=True)
            try:
                result = self.call(self.server("complete"), "sim_launch", operation_id="escape-1", world_path="escape/foreign.wbt")
                self.assertEqual(result["error_code"], "INVALID_ARGUMENT")
                self.assertTrue((target / "foreign.wbt").is_file())
            finally:
                link.rmdir() if os.name == "nt" else link.unlink()


if __name__ == "__main__":
    unittest.main()
