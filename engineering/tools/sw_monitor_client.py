# -*- coding: utf-8 -*-
"""sw_monitor_client.py — 外部监控器（DSH-SW-Monitor）的 Python 侧接入点。

为什么需要这个文件
────────────────────────────────────────────────────────────────────────────
"谁来分配 SolidWorks 使用权"必须只有【一个】权威。旧实现把这件事放在
mode_gate.py 的 sw_lock（mode_state.json 里的 owner/queue），有两个结构性缺陷：

  ① 等待是【命令型】的：sw_bridge 遇到忙就返回 sw_busy，让子代理 sleep 后
     重试同一条命令 —— 于是"等锁"必须一直占着一个回合（图一：主对话挂在
     wait-rooms --timeout 900 上）。
  ② 判定是【进程存在性】的：SW 没在跑 = 锁空闲，两个房间可以同时拿到使用权
     （2026-11-11 实测 8 对构建区间重叠，最长 216 秒）。

外部监控器把这两件事都换掉了：排队由 HTTP 服务持有（FIFO，票据按
epoch/task/room/session 绑定），等待由 DSH 后台 job 承担（图一变成 job id），
"能不能动手"由服务发放的一次性命令许可决定。

本模块只做三件事
────────────────────────────────────────────────────────────────────────────
  A. 把"取锁 / 释放 / 等待 / 记账"翻译成对【宿主桥】的调用。
     Python 不持有 host.token，也不直连外部服务 —— 凭据只在宿主进程内存里
     （教程 3.1：「禁止使用代理可自行填写的 room、lease 字符串来模拟该身份」）。
  B. 提供 require_ready()：没有接管配置就【硬失败】并给出可执行的下一步。
     旧锁实现已删除，不存在"监控器不在就退回旧队列"这条路
     （教程 6：「禁止双轨同时放行」）。
  C. 把监控器的结构化拒绝翻译成可直接回显给模型的错误对象。

⚠️ 本模块【不】复制任何排队逻辑：不维护 owner、不维护队列、不做超时接管。
   这里出现第二份判定就是双权威，而双权威正是这次改造要根治的问题。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CONFIG_NAME = "sw_monitor.json"

# ── 宿主桥地址解析：**不要写死端口** ─────────────────────────────────────
# 现场（2026-11-12 真机测试，会话 630e2e4d）：
#   监控器已装好、sw_monitor.json 也写了，但 sw-monitor attach 报
#     MONITOR_BRIDGE_UNREACHABLE: 连不上 DSH 宿主桥（127.0.0.1:19387）
#   而当时宿主实际监听 57496。DSH 的 Web GUI 端口【每次启动都变】，
#   DSH_WEB_PORT 这个环境变量在真实会话里根本没被注入 —— 写死 19387
#   等于让整条链路永久不可达。
# ask_user.py 早就踩过同一个坑（它默认 3080，实测全部 WinError 10061），
# 修法是读 DSH_WEB_URL（宿主注入的真实 GUI 地址）。这里沿用同一套口径：
#   ① 显式环境变量 DSH_SW_MONITOR_HOST（便于测试/多实例隔离）
#   ② DSH_WEB_URL / DSH_WEB_URL_ALT（宿主注入，最可靠）
#   ③ DSH_WEB_PORT（老部署可能注入）
#   ④ 最后才回落到固定端口（并记录，便于排障时看出"是不是回落的"）
ENV_HOST = "DSH_SW_MONITOR_HOST"
ENV_URL = "DSH_WEB_URL"
ENV_URL_ALT = "DSH_WEB_URL_ALT"
ENV_PORT = "DSH_WEB_PORT"
#: 历史固定端口（仅作最后兜底；DSH 真实端口每次启动都变）
FALLBACK_PORTS = ("19387",)
BRIDGE_PATH = "/dsh-engineering-ui/sw-monitor-op"
PERMIT_PATH = "/dsh-engineering-ui/sw-monitor-permit"
DONE_PATH = "/dsh-engineering-ui/sw-monitor-done"

#: 宿主桥单次 HTTP 等待窗上限（与 host-adapter.mjs 的 rpc 超时一致）。
CALL_TIMEOUT_S = 30


class MonitorError(RuntimeError):
    """监控器拒绝 / 不可达 / 未配置。message 可直接回显给模型。"""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra

    def as_dict(self):
        d = {"ok": False, "error": str(self), "code": self.code, "source": "sw_monitor"}
        d.update(self.extra)
        return d


def _state_dir():
    """工程状态目录。优先级与 _store.state_dir() 一致，但【不缓存】。

    为什么不直接复用 _store.state_dir()：它把结果缓存在模块级 _cached 里。
    而本模块的配置文件是"接入/断开"的开关，运行中可能被改（教程 4：
    配置变更应该在下一轮生效，而不是重启 DSH 才生效）；一旦被缓存，
    切换接入状态就要重启进程，那与"热生效"的承诺不符。
    这里每次现读环境变量，与 _store 的最高优先级规则保持一致。
    """
    v = (os.environ.get("DSH_STATE_DIR") or "").strip()
    if v:
        return os.path.abspath(v)
    try:
        import _store
        return _store.state_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(_state_dir(), CONFIG_NAME)


def load_config():
    """读接入配置。缺失 / 损坏 → None（= 未接管）。"""
    try:
        with open(config_path(), "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    if not cfg.get("url") or not cfg.get("tokenFile"):
        return None
    return cfg


def enabled():
    """本轮是否由监控器接管 SW 使用权（判据只有这一条：配置存在且 enabled）。"""
    return load_config() is not None


def require_ready():
    """没有接管配置就硬失败，并给出可执行的下一步。"""
    cfg = load_config()
    if cfg:
        return cfg
    raise MonitorError(
        "MONITOR_NOT_CONFIGURED",
        "本轮尚未接入外部监控器：缺少 %s。"
        "旧 SW 锁实现已删除，不能再退回进程内队列。"
        "请先启动 DSH-SW-Monitor（python -B monitor.py --data <运行数据> "
        "--workspace-root <交付根> --dsh-tools <tools 目录>），"
        "再执行 python mode_gate.py sw-monitor attach --drained。"
        % config_path())


def _origin():
    """返回宿主桥的 origin，例如 http://127.0.0.1:57496。

    顺序见上面注释；找不到任何可信来源时回落到 FALLBACK_PORTS[0]，
    并把 source 记进 status（排障时能一眼看出"是不是写了固定端口"）。
    """
    v = (os.environ.get(ENV_HOST) or "").strip()
    if v.startswith("http://") or v.startswith("https://"):
        return v.rstrip("/"), "env:" + ENV_HOST
    for env in (ENV_URL, ENV_URL_ALT):
        v = (os.environ.get(env) or "").strip()
        if v.startswith("http://") or v.startswith("https://"):
            return v.rstrip("/"), "env:" + env
    p = (os.environ.get(ENV_PORT) or "").strip()
    if p.isdigit():
        return "http://127.0.0.1:%s" % p, "env:" + ENV_PORT
    return "http://127.0.0.1:%s" % FALLBACK_PORTS[0], "fallback"


def _port():
    """兼容旧调用：从 origin 里取端口（仅供日志/提示使用）。"""
    try:
        return _origin()[0].rsplit(":", 1)[-1]
    except Exception:
        return FALLBACK_PORTS[0]


def _post(path, payload, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = _origin()[0] + path
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # ⚠️ 409 不是"桥坏了"，而是【业务拒绝】的载体：
        #   monitor.py 把 Fault 映射成 409 + {ok:false, error:"CODE"}。
        #   如果这里直接报 HTTP 409，调用方拿到的就是"HTTP Error 409: Conflict"
        #   这种毫无信息量的文本，真实原因（比如是 ROOM_NOT_SW_ELIGIBLE 还是
        #   HOST_OFFLINE_OR_QUARANTINED）全被吃掉 —— 实测接入时就是这样
        #   把"房间不在编制里"误报成桥故障，排查了一轮。
        _body = e.read()[:800].decode("utf-8", "replace")
        try:
            _j = json.loads(_body)
        except ValueError:
            _j = None
        if isinstance(_j, dict) and _j.get("error"):
            raise MonitorError(
                str(_j.get("error")), "宿主桥拒绝了该操作：%s" % _j["error"],
                http_status=e.code) from None
        raise MonitorError("MONITOR_BRIDGE_HTTP",
                           "宿主桥返回 HTTP %s：%s" % (e.code, _body)) from None
    except (urllib.error.URLError, OSError):
        raise MonitorError(
            "MONITOR_BRIDGE_UNREACHABLE",
            "连不上 DSH 宿主桥（%s）。监控器接管期间这是硬失败："
            "请确认 DSH 正在运行且工程 UI 插件已装配，不要改用旧锁路径。"
            "若端口不对，设置 DSH_WEB_URL=http://127.0.0.1:<实际端口> 后重试。"
            % (_origin()[0],)) from None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise MonitorError("MONITOR_BRIDGE_BAD_JSON", "宿主桥返回的不是 JSON") from None


def permit(room, command, part=None, wait_s=0.0, session=""):
    """为一条 SW 命令取【一次性许可】。

    排队票、claim、lease 全部发生在宿主侧（教程 3.1：许可证必须与真实会话
    和宿主绑定，不能用代理自填的 room/lease 字符串模拟）。Python 只消费结论。

    返回 {"state": "running", "command": …}
    排队中抛 MonitorError(code="MONITOR_SW_BUSY", job_id=…, position=…)。
    """
    out = _post(PERMIT_PATH, {"room": room, "command": command, "part": part,
                              "wait_s": float(wait_s or 0), "session": session},
                timeout=max(CALL_TIMEOUT_S, float(wait_s or 0) + 10))
    if out.get("ok") is True and out.get("state") == "running":
        return out
    code = str(out.get("code") or "MONITOR_REJECTED")
    if out.get("state") == "queued":
        raise MonitorError(
            code, str(out.get("error") or "SW 使用权在排队"),
            job_id=out.get("job_id"), position=out.get("position"),
            owner=out.get("owner"), hint=out.get("hint"), queued=True)
    raise MonitorError(code, str(out.get("error") or "监控器拒绝了该命令"),
                       **{k: v for k, v in out.items()
                          if k not in ("ok", "error", "code", "state")})


def done(room, command, release=False, drained=True):
    """命令收尾记账。release=True 时一并归还使用权（房间 SW 阶段结束）。"""
    out = _post(DONE_PATH, {"room": room, "command": command,
                            "release": bool(release), "drained": bool(drained)},
                timeout=CALL_TIMEOUT_S)
    if out.get("ok") is not True:
        raise MonitorError(str(out.get("code") or "MONITOR_REJECTED"),
                           str(out.get("error") or "监控器拒绝了收尾"))
    return out


def call(op, payload=None, timeout=CALL_TIMEOUT_S):
    """调用宿主桥。返回 dict；失败抛 MonitorError。

    这里【不】重试：取锁/释放都不是幂等语义（重放会多占一次队列），
    重试策略归调用方（sw_bridge 的单命令调用 vs mode_gate 的阻塞等待）。
    """
    body = json.dumps({"op": op, "payload": payload or {}}, ensure_ascii=False).encode("utf-8")
    url = _origin()[0] + BRIDGE_PATH
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # 同上：409 = 业务拒绝，必须把 error 码原样往上抛。
        _body = e.read()[:800].decode("utf-8", "replace")
        try:
            _j = json.loads(_body)
        except ValueError:
            _j = None
        if isinstance(_j, dict) and _j.get("error"):
            raise MonitorError(str(_j.get("error")),
                               "宿主桥拒绝了该操作：%s" % _j["error"],
                               http_status=e.code) from None
        raise MonitorError("MONITOR_BRIDGE_HTTP",
                           "宿主桥返回 HTTP %s：%s" % (e.code, _body)) from None
    except (urllib.error.URLError, OSError):
        raise MonitorError(
            "MONITOR_BRIDGE_UNREACHABLE",
            "连不上 DSH 宿主桥（%s）。监控器接管期间这是硬失败："
            "请确认 DSH 正在运行且工程 UI 插件已装配，不要改用旧锁路径。"
            "若端口不对，设置 DSH_WEB_URL=http://127.0.0.1:<实际端口> 后重试。"
            % (_origin()[0],)) from None
    try:
        out = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise MonitorError("MONITOR_BRIDGE_BAD_JSON", "宿主桥返回的不是 JSON") from None
    if not isinstance(out, dict):
        raise MonitorError("MONITOR_BRIDGE_BAD_JSON", "宿主桥返回的不是对象")
    if out.get("ok") is not True:
        raise MonitorError(str(out.get("code") or "MONITOR_REJECTED"),
                           str(out.get("error") or "监控器拒绝了该操作"),
                           **{k: v for k, v in out.items()
                              if k not in ("ok", "error", "code")})
    return out


# ── 业务封装：只暴露门禁真正需要的几个动作 ────────────────────────────────

def task_of():
    """当前任务的稳定 ID（来自 workflow_state.json）。没有就返回 None。"""
    try:
        path = os.path.join(_state_dir(), "workflow_state.json")
        with open(path, "r", encoding="utf-8-sig") as f:
            st = json.load(f)
        tid = st.get("task_id")
        return str(tid) if tid else None
    except Exception:
        return None


def acquire(room, session="", wait_s=0.0):
    """申请 SW 使用权。

    wait_s<=0：只请求一次。队首立刻预留则返回 reserved，否则返回 queued
              （调用方据此决定要不要挂后台 job 等，绝不能 sleep 轮询）。
    wait_s>0 ：阻塞等待，最多 wait_s 秒；拿不到抛 MonitorError。
    """
    return call("acquire", {"room": room, "session": session or "",
                            "wait_s": float(wait_s or 0)},
                timeout=max(CALL_TIMEOUT_S, float(wait_s or 0) + 10))


def release(room, drained=True):
    """归还使用权。drained=True 表示宿主已核实 worker / 子进程排空。"""
    return call("release", {"room": room, "drained": bool(drained)})


def begin(room, command, part=None):
    """为一条具体 SW 命令取一次性许可。没有许可不得启动 CAD 子进程。"""
    return call("begin", {"room": room, "command": command, "part": part})


def end(room, command):
    return call("end", {"room": room, "command": command})


def part_start(room, part, command, gen):
    return call("part-start", {"room": room, "part": part,
                               "command": command, "gen": gen})


def part_saved(room, part, command, gen, file_path):
    return call("part-saved", {"room": room, "part": part, "command": command,
                               "gen": gen, "file": file_path})


def part_failed(room, part, command, gen, reason):
    return call("part-failed", {"room": room, "part": part, "command": command,
                                "gen": gen, "reason": reason})


def board():
    """逐零件看板（数据源 = 监控器，不再是 mode2_plan + prime_reports）。"""
    return call("board", {})


def wait_rooms(rooms, timeout=20.0):
    """等房间收尾。state 取 ready / attention / paused / waiting。"""
    return call("wait-rooms", {"rooms": list(rooms or []), "timeout": float(timeout)},
                timeout=float(timeout) + 10)


def status_summary():
    """只读摘要，供 status / 自检 / 看板降级展示。绝不抛错。"""
    out = {"enabled": enabled(), "config": config_path(),
           "bridge": _origin()[0] + "/dsh-engineering-ui/sw-monitor-op",
           "bridge_source": _origin()[1]}
    if not out["enabled"]:
        out["hint"] = ("未接入监控器：缺少 %s。旧 SW 锁已删除，"
                       "建模命令在接入前一律拒绝。" % config_path())
        return out
    try:
        b = board()
        out.update({"task": b.get("task"), "revision": b.get("revision"),
                    "pulse_alive": b.get("pulse_alive"),
                    "quarantined": b.get("quarantined"),
                    "summary": b.get("summary"), "sw": b.get("sw")})
    except MonitorError as e:
        out.update({"error": str(e), "code": e.code, "pulse_alive": False})
    return out
