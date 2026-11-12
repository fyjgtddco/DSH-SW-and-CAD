"""脱离 DSH 的可重复验收，运行 python -B selfcheck.py。"""
import hashlib
import json
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
from threading import Thread
import unittest

from monitor import Coordinator, Fault, Handler, HTTP


class Scenario(unittest.TestCase):
    def test_fifo_wait_and_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            m = Coordinator(root / "state.db", root)
            host = {"epoch": "host-1"}
            call = lambda op, **data: m.call(op, host | data)
            call("attach", drained=True)
            call("plan", task="T", root=directory, rooms=[
                {"name": "甲", "type": "model", "parts": ["齿轮", "轴"]},
                {"name": "乙", "type": "model", "parts": ["箱体"]}])
            a = dict(task="T", room="甲", session="a", ticket="A")
            b = dict(task="T", room="乙", session="b", ticket="B")
            self.assertEqual(call("request", **a)["state"], "reserved")
            self.assertEqual(call("request", **b)["position"], 1)
            lease = call("claim", **a)["lease"]
            call("begin", **a, lease=lease, command="cmd")
            with self.assertRaises(Fault):
                call("release", **a, lease=lease, drained=True)
            received = []
            t = Thread(target=lambda: received.append(call("wait-lock", **b, timeout=5)))
            t.start()
            call("end", **a, lease=lease, command="cmd")
            call("release", **a, lease=lease, drained=True)
            t.join(2)
            self.assertEqual(received[0]["state"], "reserved")
            call("claim", **b)
            m.close()
            m = Coordinator(root / "state.db", root)
            try:
                self.assertEqual(call("attach", drained=True)["state"], "quarantined")
                with self.assertRaises(Fault):
                    call("begin", **b, lease=lease, command="other")
                self.assertEqual(call("recover", drained=True, reason="独立确认 SW 和 worker 已停止")["state"], "ready")
            finally:
                m.close()

    def test_progress_failure_bom_and_recheck(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(directory)
        m = Coordinator(root / "state.db", root)
        self.addCleanup(m.close)
        host = {"epoch": "h"}
        call = lambda op, **d: m.call(op, host | d)
        call("attach", drained=True)
        call("plan", task="T", root=directory, rooms=[
            {"name": "齿轮房", "type": "model", "parts": ["齿轮", "轴"]},
            {"name": "采购房", "type": "purchased", "parts": ["轴承"]}])
        t = dict(task="T", room="齿轮房", session="a", ticket="A")
        call("request", **t)
        t.update(lease=call("claim", **t)["lease"])
        command = t | {"command": "C"}
        call("begin", **command)
        with self.assertRaisesRegex(Fault, "COMMAND_ALREADY_STARTED"):
            call("begin", **command)
        call("part-start", **command, part="齿轮", gen=0)
        rows = call("board", task="T")["parts"]
        self.assertEqual([x["state"] for x in rows], ["building", "not_started", "not_started"])
        with self.assertRaisesRegex(Fault, "STALE_GENERATION"):
            call("part-saved", **command, part="齿轮", gen=0, file="齿轮.SLDPRT")
        with self.assertRaisesRegex(Fault, "TICKET_SCOPE"):
            call("claim", **(t | {"session": "other"}))
        model = root / "齿轮.SLDPRT"
        model.write_bytes(b"synthetic model")
        call("part-saved", **command, part="齿轮", gen=1, file=model.name)
        call("part-start", **command, part="轴", gen=0)
        call("end", **command)
        call("release", **t, drained=True)
        result = call("review-part", task="T", room="齿轮房", part="齿轮")
        self.assertFalse(result["passed"])
        self.assertIn("DSH_TOOLS_UNCONFIGURED", result["blockers"])
        self.assertEqual(call("wait-rooms", task="T", rooms=["齿轮房"], timeout=0)["state"], "attention")
        bom = root / "bom.json"
        room = dict(task="T", room="采购房")
        bom.write_text('{"items":[]}', encoding="utf-8")
        self.assertFalse(call("review-room", **room, receipts={"bom": bom.name})["passed"])
        bom.write_text('{"items":[{"name":"轴承","qty":2,"spec":"6205"}]}', encoding="utf-8")
        self.assertTrue(call("review-room", **room, receipts={"bom": bom.name})["passed"])
        call("close-room", **room)
        self.assertEqual(call("wait-rooms", task="T", rooms=["采购房"], timeout=0)["state"], "ready")
        bom.write_text('{"items":[]}', encoding="utf-8")
        self.assertEqual(call("wait-rooms", task="T", rooms=["采购房"], timeout=0)["state"], "attention")

    def test_expiry_and_late_claim(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(directory)
        m = Coordinator(root / "state.db", root)
        self.addCleanup(m.close)
        host = {"epoch": "host"}
        call = lambda op, **data: m.call(op, host | data)
        call("attach", drained=True)
        call("plan", task="T", root=directory, rooms=[
            {"name": r, "type": "model", "parts": [r]} for r in ("A", "B")])
        a, b = [dict(task="T", room=x, session=x, ticket=x) for x in ("A", "B")]
        call("request", **a)
        call("request", **b)
        with m.cv:
            s = m._snapshot()
            s["tickets"]["A"]["claim_by"] = time.time() - 1
            m._commit(s)
        self.assertEqual(call("wait-lock", **b, timeout=0)["state"], "reserved")
        with self.assertRaisesRegex(Fault, "NOT_GRANTED"):
            call("claim", **a)
        lease = call("claim", **b)["lease"]
        m.heartbeat = time.monotonic() - 16
        self.assertEqual(call("heartbeat")["state"], "quarantined")
        with self.assertRaisesRegex(Fault, "HOST_OFFLINE_OR_QUARANTINED"):
            call("begin", **b, lease=lease, command="late")

    def test_revision_bound_evidence_with_mock_verifiers(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(directory)
        tools = root / "tools"
        tools.mkdir()
        work = root / "task"
        work.mkdir()

        def write(path, data):
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        write(tools / "workflow_state.json", {"task_id": "T", "work_dir": str(work)})
        # 合成验签替身：仅覆盖外部程序的传参和证据绑定，不能证明 DSH 签名。
        (tools / "defense_gate.py").write_text(
            "def attestation_path(part): return part + '.material.json'\n"
            "def check_material_attestation(part): return {'ok': True, 'level': 'PASS'}\n"
            "def resolve_room_credential(*a): return '', []\n"
            "def check_physics_attestation(room):\n"
            "  assert resolve_room_credential(room,'.physics.json')[0].endswith('physics.json')\n"
            "  return {'ok': True, 'level': 'PASS', 'backed_by_real_run': True}\n"
            "def check_domain_attestation(room): return {'ok': True, 'level': 'PASS'}\n", encoding="utf-8")
        (tools / "part_registry.py").write_text(
            "def audit_verification(**kw): return {'ok': True, 'intact_count': 1}\n"
            "def list_unresolved(**kw): return {'ok': True, 'items': []}\n", encoding="utf-8")
        m = Coordinator(root / "state.db", root, str(tools))
        self.addCleanup(m.close)
        call = lambda op, **d: m.call(op, {"epoch": "h"} | d)
        call("attach", drained=True)
        call("plan", task="T", root=str(work), rooms=[
            {"name": "Room", "type": "model", "parts": ["轴"]}])
        t = dict(task="T", room="Room", session="a", ticket="A")
        call("request", **t)
        t.update(lease=call("claim", **t)["lease"])
        c = t | {"command": "c"}
        call("begin", **c)
        call("part-start", **c, part="轴", gen=0)
        model = work / "轴.SLDPRT"
        model.write_bytes(b"only synthetic model")
        call("part-saved", **c, part="轴", gen=1, file=model.name)
        call("end", **c)
        call("release", **t, drained=True)
        write(work / (model.name + ".material.json"), {"synthetic": True})
        report = work / "result.json"
        write(report, {"synthetic": True})
        (work / "load_cases").mkdir()
        registry = work / "load_cases" / "part_registry.json"
        write(registry, {"task_id": "T", "parts": {"轴": {"room": "Room", "file": str(model)}}})
        r = {"physics": "physics.json", "domain": "domain.json"}
        data = {"room": "Room", "run_id": "synthetic-run", "_ts": time.time() * 1000}
        phys = data | {"part_sha256": "wrong", "part_path": str(model), "report_path": str(report),
                       "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
        write(work / r["physics"], phys)
        write(work / r["domain"], data)
        subject = {"task": "T", "room": "Room", "part": "轴", "receipts": r}
        self.assertFalse(call("review-part", **subject)["passed"])
        phys["part_sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
        write(work / r["physics"], phys)
        self.assertTrue(call("review-part", **subject)["passed"])
        call("close-room", task="T", room="Room")
        self.assertTrue(call("verify-task", task="T")["passed"])
        write(registry, {"task_id": "T", "parts": {"轴": {"room": "Room", "file": str(model), "rev": 2}}})
        self.assertEqual(call("board", task="T")["parts"][0]["state"], "stale")
        with self.assertRaisesRegex(Fault, "ROOMS_NOT_VERIFIED"):
            call("verify-task", task="T")

    def test_http_and_mock_dsh_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HTTP(("127.0.0.1", 0), Handler) as server:
                server.monitor = Coordinator(root / "state.db", root)
                token = root / "host.token"
                server.token = secrets.token_urlsafe(40)
                token.write_text(server.token, encoding="ascii")
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                url = "http://127.0.0.1:" + str(server.server_port)
                try:
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(Request(url + "/v1/board", b"{}"), timeout=3)
                    self.assertEqual(rejected.exception.code, 401)
                    report = subprocess.run(["node", str(Path(__file__).with_name("selfcheck-host.mjs")),
                                             url, str(token), directory],
                                            capture_output=True, encoding="utf-8", timeout=12)
                    self.assertEqual(report.returncode, 0, report.stderr)
                    print(report.stdout.strip())
                finally:
                    server.shutdown()
                    worker.join()
                    server.monitor.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
