"""SDK boundary check against S04's real carrier, in a temporary combined tree.

Before integration, set WEBOTS_MCP_EVIDENCE_MODULE to the reviewed S04 evidence.py.
After integration the package-local module is used. No source module is modified.
"""
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOOLS = Path(__file__).resolve().parents[2] / "tools"
CARRIER = Path(os.environ.get("WEBOTS_MCP_EVIDENCE_MODULE", str(TOOLS / "webots_mcp/evidence.py")))


@unittest.skipUnless(CARRIER.is_file(), "S04 carrier is not integrated; supply WEBOTS_MCP_EVIDENCE_MODULE")
class EvidenceBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_reports_and_utf8_pages_use_the_real_carrier_over_stdio(self):
        spec = importlib.util.spec_from_file_location("s04_carrier", CARRIER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composite = root / "tools"
            package = composite / "webots_mcp"
            package.mkdir(parents=True)
            for source in (TOOLS / "webots_mcp").glob("*.py"):
                shutil.copy2(source, package / source.name)
            shutil.copy2(CARRIER, package / "evidence.py")
            shutil.copy2(TOOLS / "_store.py", composite / "_store.py")
            state = root / "state"
            state.mkdir()
            store = module.EvidenceStore(state, task_id="task-1", room_id="room-1")
            report = store.new_report("experiment_report", "run-1", provenance="synthetic")
            store.write_report(report)
            log_text = "中文🙂\n第二行\n"
            store.append_log("run-1", log_text)
            params = StdioServerParameters(command=sys.executable,
                args=["-B", "-m", "webots_mcp.server", "--input-root", str(state)], cwd=str(composite),
                env={"DSH_STATE_DIR": str(state), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            with (root / "stderr.log").open("w+", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                        await session.initialize()
                        context = {"task_id": "task-1", "room_id": "room-1", "request_id": "bridge-1", "run_id": "run-1"}
                        result = (await session.call_tool("sim_report", {**context, "report_type": "experiment_report"})).structuredContent
                        self.assertTrue(result["ok"])
                        self.assertEqual(result["data"], report)
                        self.assertFalse(result["core_ready"])
                        self.assertIsNone(result["sim_time"])
                        denied = (await session.call_tool("sim_report", {**context, "room_id": "foreign",
                            "report_type": "experiment_report"})).structuredContent
                        self.assertEqual(denied["error_code"], "OWNERSHIP_MISMATCH")
                        offset, texts, digest = 0, [], None
                        for unused in range(20):
                            args = {**context, "offset": offset, "limit": 4}
                            if digest:
                                args["expected_sha256"] = digest
                            result = (await session.call_tool("sim_evidence", args)).structuredContent
                            self.assertTrue(result["ok"])
                            page = result["data"]
                            texts.append(page["text"])
                            self.assertGreater(page["next_offset"], offset)
                            offset, digest = page["next_offset"], page["sha256"]
                            if page["eof"]:
                                break
                        self.assertEqual("".join(texts), log_text)
                        store.append_log("run-1", "new")
                        stale = (await session.call_tool("sim_evidence", {**context, "expected_sha256": digest})).structuredContent
                        self.assertEqual(stale["error_code"], "EVIDENCE_INVALID")
                        missing = (await session.call_tool("sim_evidence", {**context, "run_id": "missing"})).structuredContent
                        self.assertEqual(missing["error_code"], "EVIDENCE_NOT_FOUND")
                        path = state / "webots_runs/run-1/experiment_report.json"
                        corrupt = {**report, "status": {}}
                        path.write_text(json.dumps(corrupt), encoding="utf-8")
                        invalid = (await session.call_tool("sim_report", {**context, "report_type": "experiment_report"})).structuredContent
                        self.assertEqual(invalid["error_code"], "EVIDENCE_INVALID")
                        self.assertFalse(invalid["core_ready"])
            if os.environ.get("S03_EVIDENCE_DIR"):
                (Path(os.environ["S03_EVIDENCE_DIR"]) / "evidence-bridge.json").write_text(json.dumps({
                    "carrier_source": str(CARRIER), "report_round_trip": True, "ownership_denied": True,
                    "utf8_text": "".join(texts), "stale_page_code": stale["error_code"],
                    "missing_code": missing["error_code"], "malformed_report_code": invalid["error_code"],
                    "real_webots_started": False, "core_ready": False}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
