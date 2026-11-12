#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部监控器接入的回归测试（2026-11-12）。

覆盖四件事，每一件都对应一条【已删除的旧行为】，防止它们被悄悄改回来：
  ① 旧锁命令真的没了：sw-request / sw-wait / lock-doctor / _reclaim_lock /
     _lock_owner_alive / _purge_orphan_lock 在 mode_gate 里必须查无此人；
  ② 未接入时建模命令【一律拒绝】，且拒绝是可读的（不是 AttributeError）；
  ③ 接入后：监控器发许可 → sw_bridge 才能动 COM（用替身服务验证）；
  ④ parts-board 的数据源是监控器，不是 prime_reports（后者已删除）。

用法：python test_regression_sw_monitor.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK] " + name)
    else:
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))
        FAIL.append(name)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class StubBridge(BaseHTTPRequestHandler):
    """假的宿主桥：记录收到什么，并按预设回应。

    为什么用替身而不是真起 DSH：这个测试要在 CI/任何机器上可跑，
    而真宿主需要 Electron/插件装配。我们验证的是【契约】—— Python 侧
    发什么、怎么解释回答；宿主与监控器自身的正确性由 sw_monitor/selfcheck.py
    和插件测试覆盖。
    """
    calls = []
    mode = {"permit": "running"}

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        StubBridge.calls.append((self.path, body))
        op = body.get("op")
        out = {"ok": True}
        if self.path.endswith("/sw-monitor-permit"):
            if StubBridge.mode["permit"] == "queued":
                out = {"ok": True, "state": "queued", "job_id": "job-stub-1",
                       "position": 2, "owner": "别的房间",
                       "hint": "排队中"}
            elif StubBridge.mode["permit"] == "denied":
                out = {"ok": False, "code": "MONITOR_NOT_CONFIGURED",
                       "error": "未接入"}
            else:
                out = {"ok": True, "state": "running", "command": "cmd-1"}
        elif self.path.endswith("/sw-monitor-done"):
            out = {"ok": True, "state": "ended"}
        elif self.path.endswith("/sw-monitor-op"):
            if op == "board":
                out = {"ok": True, "task": "T", "revision": 3,
                       "pulse_alive": True, "quarantined": False,
                       "parts": [{"name": "DSH_齿轮", "room": "传动",
                                  "room_type": "model", "state": "done",
                                  "label": "完成", "detail": "验收通过",
                                  "generation": 1, "file": "x.SLDPRT",
                                  "has_report": True, "checks": {"材料": {"status": "PASS"}},
                                  "safe_to_restart": False}],
                       "rooms": {}, "summary": {"counts": {"done": 1},
                                                "total": 1, "done": 1},
                       "sw": {"owner": None, "queue": []}}
            elif op == "wait-rooms":
                out = {"ok": True, "state": "ready", "rooms": {}}
            elif op == "verify-task":
                out = {"ok": True, "passed": True, "status": "passed",
                       "checks": {}, "blockers": []}
            else:
                out = {"ok": True, "state": "reserved"}
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    print("=" * 70)
    print("外部监控器接入回归")
    print("=" * 70)

    # ── ① 旧锁实现必须彻底消失 ─────────────────────────────────────────
    print("\n① 旧锁实现已删除")
    import mode_gate as mg
    for gone in ("_reclaim_lock", "_lock_owner_alive", "_purge_orphan_lock",
                 "_lock_ident", "SW_FAIR_CMDS", "SW_MAX_HOLD", "SW_CMD_HOLD",
                 "SW_STALE_KILL", "cmd_sw_monitor_bg", "_monitor_bg_impl"):
        check("mode_gate 不再有 %s" % gone, not hasattr(mg, gone))
    import inspect as _inspect
    src = _inspect.getsource(mg)
    # 注释里提到旧命令名是允许的（那是防止回归的说明文字），
    #   要禁的是【分发分支】： elif cmd == "xxx":
    for gone_str in ("sw-request", "sw-wait", "lock-doctor"):
        _branch = 'cmd == "%s"' % gone_str
        check("mode_gate 不再分发 %s" % gone_str, _branch not in src,
              [l.strip() for l in src.split("\n") if _branch in l][:2])

    import mode2_flow as m2
    m2src = _inspect.getsource(m2)
    for gone in ("prime_board", "prime_reports", "prime_verify_all",
                 "record_prime_report", "check_prime_consistency"):
        check("mode2_flow 不再有 %s" % gone, not hasattr(m2, gone))
    # 允许在"已删除/旧实现/迁移说明"这类注释里提到它（那是防止回归的说明文字），
    # 但不允许还有【读它】的代码：赋值/索引/for 三种形态都查。
    import re as _re
    _live = [ln for ln in m2src.split("\n")
             if "prime_reports" in ln and not ln.strip().startswith("#")
             and "已删除" not in ln and "旧实现" not in ln]
    check("mode2_flow 不再【读取】prime_reports", not _live, _live[:3])

    # ── ② 未接入：建模命令必须被拒绝（fail-closed）────────────────────
    print("\n② 未接入时必须拒绝，且错误可读")
    tmp = tempfile.mkdtemp(prefix="swmon_")
    old_state = os.environ.get("DSH_STATE_DIR")
    # 测试用【最高优先级】的 DSH_SW_MONITOR_HOST 指向替身，
    #   而不是 DSH_WEB_PORT —— 后者在真实环境里不被注入，
    #   靠它注入在优先级表里排在 DSH_WEB_URL 之后，会被真机地址遮蔽。
    old_port = os.environ.get("DSH_SW_MONITOR_HOST")
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        # 确保没有配置文件
        cfg = os.path.join(tmp, "sw_monitor.json")
        check("测试起点没有 sw_monitor.json", not os.path.exists(cfg))
        r = mg.cmd_sw_status()
        check("sw-status 在未接入时 ok=true（查询本身可用）", r.get("ok") is True)
        check("sw-status 明确 enabled=false",
              (r.get("monitor") or {}).get("enabled") is False)
        w = mg.cmd_wait_rooms(["房间"], timeout=1)
        check("wait-rooms 未接入时 ok=false", w.get("ok") is False)
        check("wait-rooms 给出可执行的 hint",
              "sw-monitor attach" in str(w.get("hint") or ""), w.get("hint"))

        # ── ③ 接入后：许可 → 命令可执行；排队 → 给 job_id ─────────────
        print("\n③ 接入后由监控器发许可")
        port = _free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", port), StubBridge)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        os.environ["DSH_SW_MONITOR_HOST"] = "http://127.0.0.1:%d" % port
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "url": "http://127.0.0.1:18779",
                       "tokenFile": os.path.join(tmp, "host.token")}, f)

        import importlib
        import sw_monitor_client as mc
        importlib.reload(mc)
        check("接入后 enabled() 为真", mc.enabled() is True)

        importlib.reload(mg)
        # 通过 sw_bridge 的取锁函数走一遍（它就是真实入口）
        import sw_bridge as sb
        importlib.reload(sb)
        got = sb._gate_sw_lock("传动", command="run")
        check("拿到许可时返回 permit=True", got.get("permit") is True, got)
        check("许可带命令 ID", bool(got.get("command")), got)

        # 排队：必须返回 job_id，且绝不 sleep
        StubBridge.mode["permit"] = "queued"
        t0 = time.time()
        busy = sb._gate_sw_lock("传动", command="run")
        spent = time.time() - t0
        check("排队时返回 busy", busy.get("busy") is True, busy)
        check("排队时带 job_id", busy.get("job_id") == "job-stub-1", busy)
        check("排队时立刻返回（<2s，说明没有 sleep 轮询）", spent < 2.0, spent)

        StubBridge.mode["permit"] = "denied"
        denied = sb._gate_sw_lock("传动", command="run")
        check("监控器拒绝时 denied=True", denied.get("denied") is True, denied)
        check("拒绝时带可读 code",
              bool(denied.get("code")), denied)

        # ── ④ parts-board 数据源 ─────────────────────────────────────
        print("\n④ parts-board 数据源 = 监控器")
        StubBridge.mode["permit"] = "running"
        importlib.reload(m2)
        board = m2.parts_board(work_dir=tmp)
        check("parts-board ok=True", board.get("ok") is True, board.get("error"))
        check("source=sw_monitor", board.get("source") == "sw_monitor")
        check("有零件行", len(board.get("parts") or []) == 1, board.get("parts"))
        row = (board.get("parts") or [{}])[0]
        check("零件状态来自监控器（done/完成）",
              row.get("state") == "done" and row.get("label") == "完成", row)
        check("看板带 pulse_alive", board.get("pulse_alive") is True)

        srv.shutdown()
    finally:
        if old_state is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old_state
        if old_port is None:
            os.environ.pop("DSH_SW_MONITOR_HOST", None)
        else:
            os.environ["DSH_SW_MONITOR_HOST"] = old_port
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    if FAIL:
        print("FAILED %d:" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
