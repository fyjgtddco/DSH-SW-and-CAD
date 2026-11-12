#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：真实 monitor.py + 模拟宿主桥 → 验证 Python 侧接入契约。

为什么需要它（和 test_regression_sw_monitor.py 的分工）：
  · 那个测的是"Python 侧怎么解释回答"（宿主桥用替身）；
  · 这个测的是"真实监控器的操作名/参数是否被 Python 侧用对了"。

后者是本次改造里【真踩到过的坑】：宿主桥最初发 op="acquire"，
而 monitor.py 只认 request/claim —— 单元测试全绿，接入后永远拿不到许可。
所以这里真起一个 monitor.py 子进程，再用一个最小的假宿主做 op 翻译，
把 sw_bridge 的取锁路径完整跑一遍。

用法：python test_regression_sw_monitor_e2e.py
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
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def post(url, payload, token, timeout=20):
    """POST 并按监控器约定解析：409 是业务拒绝（ok:false + error），不是网络错误。"""
    req = Request(url, data=json.dumps(payload).encode(),
                  headers={"Authorization": "Bearer " + token,
                           "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"ok": False, "error": raw[:300]}

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        FAILS.append(name)
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeHost(BaseHTTPRequestHandler):
    """最小假宿主：把 Python 的 permit/done 翻译成监控器的 request/claim/begin/end。"""

    monitor_url = ""
    token = ""
    epoch = "e2e-epoch"
    ticket = "tk-e2e"
    log = []
    trace = []

    def _rpc(self, op, payload):
        body = dict(payload)
        # 诊断：把真正发出去的 payload 记进 trace，出错时能看出是哪个字段被拒
        FakeHost.trace.append((op, dict(body)))
        body["epoch"] = self.epoch
        if op in ("begin", "end", "part-start", "part-saved", "part-failed",
                  "claim", "release"):
            body.setdefault("ticket", self.ticket)
        req = Request(self.monitor_url + "/v1/" + op,
                      data=json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + self.token,
                               "Content-Type": "application/json"},
                      method="POST")
        # 监控器用 409 表达业务拒绝；这里必须解析出真实原因，
        #   否则假宿主会把"房间不在编制里"报成 "HTTP 409 Conflict"。
        try:
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except ValueError:
                return {"ok": False, "error": raw[:200]}

    def _fail(self, code):
        raise RuntimeError(code)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(n) or b"{}")
        op = req.get("op")
        payload = req.get("payload") or {}
        FakeHost.log.append((op, payload))
        try:
            if self.path.endswith("/sw-monitor-permit"):
                # 注意：Python 客户端把参数放在【顶层】（与真实 index.js 的
                #   body.room 读取一致），不是包在 payload 里。早期替身按
                #   payload 取，导致 room=None → 监控器报 INVALID_NAME，
                #   把"接入契约对了"误判成"接入坏了"。
                room = payload.get("room") or req.get("room")
                # request（建票）→ 队首立刻 reserved
                st = self._rpc("request", {"task": "T", "room": room,
                                           "session": "s1", "ticket": self.ticket})
                if not st.get("ok", True) or st.get("error"):
                    out = {"ok": False, "code": "MONITOR_REJECTED",
                           "error": "request: %s" % st.get("error")}
                    _send = json.dumps(out, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(_send)))
                    self.end_headers()
                    self.wfile.write(_send)
                    return
                if st.get("state") in ("reserved", "claimed"):
                    self._rpc("claim", {"task": "T", "room": room,
                                        "session": "s1", "ticket": self.ticket})
                    self._rpc("begin", {"task": "T", "room": room,
                                        "command": payload.get("command") or req.get("command")})
                    out = {"ok": True, "state": "running",
                           "command": payload.get("command") or req.get("command")}
                else:
                    out = {"ok": True, "state": "queued", "ticket": self.ticket,
                           "job_id": "job-e2e", "position": st.get("position")}
            elif self.path.endswith("/sw-monitor-done"):
                self._rpc("end", {"task": "T",
                                  "room": payload.get("room") or req.get("room"),
                                  "command": payload.get("command") or req.get("command")})
                out = {"ok": True, "state": "ended"}
            else:
                out = {"ok": True}
        except Exception as e:  # noqa: BLE001 - 要把监控器的拒绝原样回传
            out = {"ok": False, "code": "MONITOR_REJECTED", "error": str(e)}
        data = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    print("=" * 70)
    print("外部监控器 · 端到端接入（真实 monitor.py + 假宿主）")
    print("=" * 70)

    tmp = tempfile.mkdtemp(prefix="swmon_e2e_")
    root = os.path.join(tmp, "ws")
    os.makedirs(root, exist_ok=True)
    tools = HERE
    port = free_port()
    data = os.path.join(tmp, "data")
    proc = subprocess.Popen(
        [sys.executable, "-B", os.path.join(HERE, "sw_monitor", "monitor.py"),
         "--data", data, "--workspace-root", tmp, "--port", str(port),
         "--dsh-tools", tools],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")
    fake = None
    try:
        token_file = os.path.join(data, "host.token")
        for _ in range(100):
            if os.path.exists(token_file):
                break
            time.sleep(0.1)
        check("监控器已生成 host.token", os.path.exists(token_file))
        token = open(token_file, encoding="ascii").read().strip()
        check("token 长度 >= 40", len(token) >= 40, len(token))
        mon_url = "http://127.0.0.1:%d" % port

        # 冒烟：未 attach 时 request 必须被拒（证明服务真的活着且鉴权生效）
        body = post(mon_url + "/v1/request",
                    {"task": "T", "room": "r", "session": "s",
                     "ticket": "t", "epoch": "x"}, token)
        check("未 attach 时 request 被拒", body.get("ok") is False, body)

        # 假宿主
        hport = free_port()
        FakeHost.monitor_url = mon_url
        FakeHost.token = token
        srv = ThreadingHTTPServer(("127.0.0.1", hport), FakeHost)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        fake = srv

        # 指向假宿主
        # 用最高优先级的 DSH_SW_MONITOR_HOST 指向假宿主。
        #   不要用 DSH_WEB_PORT：它在真实环境里不被注入，且优先级最低，
        #   用它注入恰好会掩盖"端口写死"这个真实缺陷（第一次就是这么漏的）。
        os.environ["DSH_SW_MONITOR_HOST"] = "http://127.0.0.1:%d" % hport
        old_state = os.environ.get("DSH_STATE_DIR")
        state_dir = os.path.join(tmp, "state")
        os.makedirs(state_dir, exist_ok=True)
        os.environ["DSH_STATE_DIR"] = state_dir
        with open(os.path.join(state_dir, "sw_monitor.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"enabled": True, "url": mon_url,
                       "tokenFile": token_file}, f)
        with open(os.path.join(state_dir, "workflow_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"task_id": "T", "step": "user_selected",
                       "work_dir": root}, f)

        # 先让监控器 attach（否则 request 会被 HOST_EPOCH 拒）
        atr = post(mon_url + "/v1/attach",
                   {"epoch": FakeHost.epoch, "drained": True}, token)
        check("监控器 attach 成功", atr.get("state") == "ready", atr)
        # 登记任务与房间（监控器要求先 plan）
        plr = post(mon_url + "/v1/plan",
                   {"epoch": FakeHost.epoch, "task": "T", "root": root,
                    "rooms": [{"name": "房间甲", "type": "model",
                               "parts": ["DSH_测试件"]}],
                    "simulation_required": False}, token)
        check("监控器 plan 成功", plr.get("ok") is True, plr)

        import importlib
        import sw_monitor_client as mc
        importlib.reload(mc)
        check("客户端已启用", mc.enabled() is True)
        check("客户端读到 task_id", mc.task_of() == "T", mc.task_of())

        import sw_bridge as sb
        importlib.reload(sb)
        lock = sb._gate_sw_lock("房间甲", command="run")
        check("★ 真实链路拿到许可（permit=True）", lock.get("permit") is True, lock)
        check("许可带 command id", bool(lock.get("command")), lock)
        # FakeHost.log 记的是【Python 侧发来的路由】，FakeHost.trace 记的才是
        #   真正转给监控器的 op —— 后者才是"监控器有没有收到 begin"的证据。
        check("★ 监控器侧记录了 begin",
              any(op == "begin" for op, _ in FakeHost.trace),
              [o for o, _ in FakeHost.trace])

        # 收尾：end 之后监控器状态应回到无在途命令
        sb._gate_sw_unlock(lock)
        check("收尾后 end 已记账", any(op == "end" for op, _ in FakeHost.trace),
              [o for o, _ in FakeHost.trace])
        brd = post(mon_url + "/v1/board",
                   {"epoch": FakeHost.epoch, "task": "T"}, token)
        check("★ 监控器看板可用且有零件行",
              brd.get("ok") is True and isinstance(brd.get("parts"), list),
              brd)
        check("看板零件名与编制一致",
              any(p.get("name") == "DSH_测试件" for p in (brd.get("parts") or [])),
              brd.get("parts"))

        if old_state is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old_state
    finally:
        if fake is not None:
            fake.shutdown()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("DSH_SW_MONITOR_HOST", None)

    print("\n" + "=" * 70)
    print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILS:
        for f in FAILS:
            print("  - " + f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
