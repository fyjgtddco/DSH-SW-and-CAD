"""Real official-SDK client/server subprocess round trips; no Webots launched."""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOOLS = Path(__file__).resolve().parents[2] / "tools"


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_call_and_fixture_uncertainty_over_real_sdk(self):
        records = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "simple.wbt").write_text("#VRML_SIM R2025a utf8\n")
            for mode in (None, "complete", "error", "timeout", "unknown"):
                args = ["-B", "-m", "webots_mcp.server", "--input-root", str(root)]
                if mode:
                    args += ["--fake-worker", mode]
                params = StdioServerParameters(command=sys.executable, args=args, cwd=str(TOOLS),
                    env={"DSH_STATE_DIR": str(root), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
                with (root / "stderr.log").open("w+", encoding="utf-8") as errlog:
                    async with stdio_client(params, errlog=errlog) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                            initialized = await session.initialize()
                            discovered = await session.list_tools()
                            names = [item.name for item in discovered.tools]
                            self.assertEqual(len(names), 18)
                            self.assertEqual(len(set(names)), 18)
                            self.assertIsNone(initialized.capabilities.tasks)
                            context = {"task_id": "task-1", "room_id": "room-1", "request_id": "sdk-1"}
                            status = (await session.call_tool("sim_status", context)).structuredContent
                            self.assertTrue(status["ok"])
                            launched = await session.call_tool("sim_launch", {**context,
                                "operation_id": "launch-1", "world_path": "simple.wbt"})
                            result = launched.structuredContent
                            self.assertEqual(launched.isError, mode != "complete")
                            self.assertFalse(result["core_ready"])
                            self.assertEqual(result["provenance"], "fake" if mode else "unavailable")
                            self.assertIsNone(result["sim_time"])
                            self.assertIsNone(result["completed_steps"])
                            if mode:
                                queried = (await session.call_tool("sim_job", {**context,
                                    "run_id": result["run_id"], "session_generation": 1,
                                    "job_id": result["job_id"]})).structuredContent
                                self.assertEqual(queried["execution_state"], result["execution_state"])
                            invalid = (await session.call_tool("sim_step", {**context,
                                "run_id": "x", "session_generation": True,
                                "operation_id": "bad-1", "steps": 1})).structuredContent
                            self.assertEqual(invalid["error_code"], "INVALID_ARGUMENT")
                            self.assertIsNone(invalid["session_generation"])
                            records.append({"mode": mode or "default", "protocol_version": initialized.protocolVersion,
                                "tool_count": len(names), "tools": names,
                                "schemas_utf8_bytes": len(json.dumps([item.inputSchema for item in discovered.tools],
                                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                                "tools_utf8_bytes": len(json.dumps([item.model_dump(mode="json", exclude_none=True)
                                    for item in discovered.tools], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                                "instructions_utf8_bytes": len((initialized.instructions or "").encode("utf-8")),
                                "result": result, "invalid_result": invalid})
                    errlog.seek(0)
                    logs = errlog.read()
                    self.assertIn("core_ready=false", logs)
                    self.assertNotIn("Traceback", logs)
            if os.environ.get("S03_EVIDENCE_DIR"):
                (Path(os.environ["S03_EVIDENCE_DIR"]) / "sdk-round-trips.json").write_text(
                    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    async def test_nonexistent_explicit_state_refuses_to_fall_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-B", "-m", "webots_mcp.server", "--input-root", temporary],
                cwd=TOOLS, env={**os.environ, "DSH_STATE_DIR": str(Path(temporary) / "absent")},
                capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("DSH_STATE_DIR", result.stderr)


if __name__ == "__main__":
    unittest.main()

