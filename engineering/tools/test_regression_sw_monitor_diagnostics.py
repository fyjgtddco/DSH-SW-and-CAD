# -*- coding: utf-8 -*-
"""监控器诊断日志（monitor_diagnostics）回归测试。

覆盖 2026-11-26 接入的诊断链路：
  ① 建表（monitor_diagnostics）+ 启动记录（MONITOR_STARTED_COM）；
  ② since_seq/limit 分页严格收敛，非法游标/越界 limit 被拒；
  ③ 任务隔离：只能读到自己任务的记录，未登记/伪造任务被拒；
  ④ 业务拒绝可见（room/part/details 白名单），且不泄漏 lease；
  ⑤ 写入失败 → incomplete=true 且不改变监控器状态；
  ⑥ 真实 HTTP 栈：Bearer 鉴权、/v1/diagnostics 路由、错误游标 409。

全部用临时目录与假 COM runner，不触碰任何既有数据库、不启动 SolidWorks。
用法：python test_regression_sw_monitor_diagnostics.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent / "sw_monitor"
sys.path.insert(0, str(HERE))

from monitor import Coordinator, Fault  # noqa: E402


class Api:
    def __init__(self, monitor):
        self.monitor = monitor

    def __call__(self, op, **payload):
        return {"ok": True} | self.monitor.call(op, payload)


class DiagnosticsAcceptance(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.monitor = Coordinator(self.root / "state.db", self.workspace,
                                   com_runner=lambda _: None, com_probe=lambda: None)
        self.addCleanup(self.monitor.close)
        self.api = Api(self.monitor)

    def call(self, op, **payload):
        return self.api(op, **{"epoch": "HOST"} | payload)

    def add_task(self, task="A"):
        self.call("attach", drained=True)
        # 监控器要求 root 唯一绑定一个 task（TASK_ROOT_ALREADY_BOUND）
        ws = self.workspace / task
        ws.mkdir(exist_ok=True)
        self.call("plan", task=task, root=str(ws), rooms=[
            {"name": "Room", "type": "model", "parts": ["Model"]}])
        data = {"task": task, "room": "Room", "session": "S", "ticket": "TICKET-" + task}
        self.call("request", **data)
        return data | {"lease": self.call("claim", **data)["lease"],
                       "command": "C", "part": "Model"}

    # ── 1. 建表 + 启动记录 ─────────────────────────────────────────────
    def test_diagnostics_table_and_start_record(self):
        tabs = [r[0] for r in self.monitor.db.execute(
            "select name from sqlite_master where type='table'")]
        self.assertIn("monitor_diagnostics", tabs)
        cols = [d[1] for d in self.monitor.db.execute("pragma table_info(monitor_diagnostics)")]
        self.assertEqual(cols, ["seq", "op", "code", "task", "room", "part", "ts", "details"])
        got = self.call("diagnostics")
        self.assertEqual(got["events"][0]["code"], "MONITOR_STARTED_COM")
        self.assertIsNone(got["events"][0]["task"])
        self.assertFalse(got["incomplete"])

    # ── 2. 分页 ────────────────────────────────────────────────────────
    def test_pagination(self):
        self.add_task()
        total = self.call("diagnostics", limit=500)["events"]
        self.assertGreaterEqual(len(total), 1)
        seen, cursor, pages = [], 0, 0
        while True:
            page = self.call("diagnostics", since_seq=cursor, limit=1)
            pages += 1
            seen.extend(page["events"])
            cursor = page["next_since_seq"]
            if not page["has_more"]:
                break
            self.assertLess(pages, 100, "分页没有收敛")
        self.assertEqual([e["seq"] for e in seen], [e["seq"] for e in total])
        self.assertEqual(cursor, total[-1]["seq"])
        # limit 越界必须拒绝
        for bad in (0, 501, -1):
            with self.assertRaises(Fault):
                self.call("diagnostics", limit=bad)
        with self.assertRaises(Fault):
            self.call("diagnostics", since_seq=10 ** 9)

    # ── 3. 任务隔离 + epoch ────────────────────────────────────────────
    def test_task_isolation(self):
        req = self.add_task("A")
        # A 产生一条本任务事件（业务拒绝）
        with self.assertRaises(Fault):
            self.call("com-build", **req, gen=0, file="foo.SLDPRT", material="Steel",
                      steps=[{"op": "BAD"}])
        # 让出 SW 所有权，登记 B 并让 B 也产生一条事件
        self.call("release", **req, drained=True)
        req_b = self.add_task("B")
        with self.assertRaises(Fault):
            self.call("com-build", **req_b, gen=0, file="bar.SLDPRT", material="Steel",
                      steps=[{"op": "BAD"}])
        with self.assertRaises(Fault):
            self.call("recover", drained=False, task="FORGED")
        rows = self.call("diagnostics", task="A")["events"]
        self.assertTrue(all(r["task"] is None or r["task"] == "A" for r in rows), rows)
        self.assertNotIn("B", {r["task"] for r in rows if r["task"]})
        rows_b = self.call("diagnostics", task="B")["events"]
        self.assertIn("B", {r["task"] for r in rows_b})
        self.assertNotIn("FORGED", json.dumps(rows, ensure_ascii=False))
        self.assertNotIn(req["lease"], json.dumps(rows, ensure_ascii=False))
        with self.assertRaises(Fault):
            self.monitor.call("diagnostics", {"epoch": "OTHER", "task": "A"})

    # ── 4. 业务拒绝 → 可见事件（含详细信息且不含原始路径/租约）────────
    def test_rejection_event_visible(self):
        req = self.add_task()
        with self.assertRaises(Fault):
            self.call("com-build", **req, gen=0, file="foo.SLDPRT", material="Steel",
                      steps=[{"op": "BAD"}])
        page = self.call("diagnostics", task="A")
        matching = [x for x in page["events"] if x["task"] == "A"]
        self.assertTrue(matching, page)
        self.assertEqual(matching[-1]["room"], "Room")
        self.assertEqual(matching[-1]["part"], "Model")
        details = matching[-1].get("details") or {}
        self.assertLessEqual(set(details), {"stage", "step", "hresult", "command", "checks"})
        # 租约不能出现在诊断里
        with self.assertRaises(Fault):
            self.call("com-build", **(req | {"lease": "WRONG_LEASE"}), gen=0,
                      file="foo.SLDPRT", material="Steel", steps=[{"op": "BAD"}])
        after = self.call("diagnostics", task="A", since_seq=page["next_since_seq"])
        self.assertEqual(after["events"], [])

    # ── 5. 写入失败 → incomplete（且不改状态）──────────────────────────
    def test_incomplete_flag(self):
        req = self.add_task()
        rev = self.monitor.state["rev"]
        self.monitor.db.execute("""CREATE TRIGGER stop_log BEFORE INSERT ON monitor_diagnostics
                                   BEGIN SELECT RAISE(ABORT, 'test'); END""")
        # 有效租约 + 非法步骤：业务拒绝会尝试写诊断，而写入被触发器拒绝
        with self.assertRaises(Fault):
            self.call("com-build", **req, gen=0, file="foo.SLDPRT", material="Steel",
                      steps=[{"op": "BAD"}])
        self.assertTrue(self.call("diagnostics", task="A")["incomplete"])
        self.assertEqual(self.monitor.state["rev"], rev)


class HttpAcceptance(unittest.TestCase):
    """真实 HTTP 栈：Bearer 鉴权 + /v1/diagnostics 路由。"""

    def test_http_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            monitor = Coordinator(root / "state.db", ws,
                                  com_runner=lambda _: None, com_probe=lambda: None)
            from monitor import HTTP, Handler
            with HTTP(("127.0.0.1", 0), Handler) as server:
                server.token, server.monitor = "t" * 48, monitor
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    port = server.server_port
                    url = "http://127.0.0.1:%d/v1/diagnostics" % port

                    def post(payload, token="t" * 48):
                        req = urllib.request.Request(
                            url, data=json.dumps(payload).encode(),
                            headers={"Authorization": "Bearer " + token,
                                     "Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=10) as r:
                            return r.status, json.loads(r.read())

                    status, body = post({"epoch": "H", "since_seq": 0, "limit": 100})
                    self.assertEqual(status, 200)
                    self.assertTrue(body["ok"])
                    self.assertEqual(body["events"][0]["code"], "MONITOR_STARTED_COM")
                    self.assertIn("next_since_seq", body)
                    self.assertFalse(body["has_more"])
                    self.assertFalse(body["incomplete"])
                    try:
                        post({"epoch": "H"}, token="bad")
                        self.fail("错误 token 必须 401")
                    except urllib.error.HTTPError as e:
                        self.assertEqual(e.code, 401)
                    # 未登记任务的诊断读取必须被拒（任务隔离），不能静默返回全局
                    monitor.call("attach", {"epoch": "H", "drained": True})
                    try:
                        post({"epoch": "H", "task": "A", "since_seq": 0})
                        self.fail("未登记任务必须被拒绝")
                    except urllib.error.HTTPError as e:
                        self.assertEqual(e.code, 409)
                        self.assertIn("TASK", json.loads(e.read().decode())["error"])
                    # 非法 cursor 必须拒绝
                    try:
                        post({"epoch": "H", "since_seq": 10 ** 9})
                        self.fail("越界游标必须被拒绝")
                    except urllib.error.HTTPError as e:
                        self.assertEqual(e.code, 409)
                        self.assertIn("DIAGNOSTICS_CURSOR_INVALID",
                                      json.loads(e.read().decode())["error"])
                finally:
                    server.shutdown()
                    monitor.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)