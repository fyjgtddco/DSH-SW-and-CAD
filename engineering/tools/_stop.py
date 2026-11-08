# -*- coding: utf-8 -*-
"""_stop.py — 工程模式【全局停机开关】单一事实源（P2 / S2 修复）

═══════════════════════════════════════════════════════════════════════════
【要解决的问题】停止指令只停了"对话"，没停"进程"
───────────────────────────────────────────────────────────────────────────
实测事故（2026-11-05 减速机任务，见《减速机设计过程回溯报告》§7 P2 / §12）：
  用户下达"停止任务"，主对话调 interrupt_agent；但子代理手里的下一条
  sw_bridge / mode_gate 命令**照样执行** —— 因为每个命令入口都不检查
  "是否已被要求停机"。结果：停止后仍有后台 python 建模进程在跑，
  SLDWORKS.EXE 也还在，用户只能再单独 kill 一遍。

根因：interrupt_agent 只是"请求"，不是"强杀"；而工具链没有全局停机令牌。

【本模块的解法】两道闸，任一命中即停机（fail-closed）
───────────────────────────────────────────────────────────────────────────
  ① 文件闸：<STATE_DIR>/STOP_ALL 存在（内容可为 JSON，也可为空）；
  ② 环境变量：DSH_KILL_SWITCH=1（兼容 true/yes/on/stop 等写法）。
  <STATE_DIR> 与 mode_gate/workflow_gate/defense_gate 同源（_store.state_dir），
  因此工作区/安装副本不会各看各的。

【命令入口的接入约定】
───────────────────────────────────────────────────────────────────────────
  · sw_bridge.py / mode_gate.py / physics_bridge.py / visual_check.py
    在解析出命令名之后、任何副作用之前调用 `guard(cmd, allow=...)`；
  · guard 命中时返回带 gate="TASK_STOPPED" 的 dict，调用方打印后以非零码退出；
  · 只读/清理类命令（status / room-board / close-all / sw-kill / resume）
    必须放在 allow 里，否则"停机后连清理都做不了"会形成死结。

【CLI】
───────────────────────────────────────────────────────────────────────────
  python mode_gate.py stop-all [原因]     # 落 STOP_ALL（停机）
  python mode_gate.py resume              # 清除 STOP_ALL（恢复）
  python -c "import _stop; print(_stop.status())"
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import os
import time

ENV_KILL = "DSH_KILL_SWITCH"
STOP_FILE = "STOP_ALL"

_TRUE = ("1", "true", "yes", "on", "stop", "stopped", "kill", "halt")


def _state_dir() -> str:
    """状态目录：与 _store 保持一致；_store 不可用时退回本文件目录。"""
    try:
        import _store
        return _store.state_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def stop_path() -> str:
    """STOP_ALL 标记文件的绝对路径。"""
    return os.path.join(_state_dir(), STOP_FILE)


def _env_on() -> bool:
    v = (os.environ.get(ENV_KILL) or "").strip().lower()
    return v in _TRUE


def _read_file() -> dict:
    """读取 STOP_ALL。文件不存在返回 {}；存在但内容不可解析也返回非空 dict。"""
    p = stop_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            txt = f.read().strip()
        if not txt:
            return {"reason": "STOP_ALL 文件存在（空内容）"}
        try:
            d = json.loads(txt)
            return d if isinstance(d, dict) else {"reason": str(txt)}
        except Exception:
            return {"reason": txt}
    except Exception as e:
        # 文件存在但读不了：仍然按【已停机】处理（fail-closed），不能因为读失败就放行
        return {"reason": "STOP_ALL 文件存在但读取失败: %r" % (e,)}


def status() -> dict:
    """返回停机状态快照（只读、零副作用）。"""
    info = _read_file()
    env = _env_on()
    stopped = bool(info) or env
    out = {
        "stopped": stopped,
        "env": env,
        "env_name": ENV_KILL,
        "file": stop_path(),
        "file_exists": bool(info),
        "state_dir": _state_dir(),
    }
    if info:
        for k in ("reason", "by", "at", "source"):
            if k in info:
                out[k] = info[k]
        out.setdefault("source", "file")
    if env and not info:
        out["reason"] = "环境变量 %s 已置位" % ENV_KILL
        out["source"] = "env"
    if not stopped:
        out["reason"] = None
        out["source"] = None
    return out


def is_stopped() -> bool:
    """是否已要求停机（文件闸或环境变量任一命中）。"""
    return bool(status().get("stopped"))


def request_stop(reason: str = "", by: str = "user") -> dict:
    """落 STOP_ALL（停机）。内容用 JSON，便于事后审计谁、何时、为什么。"""
    p = stop_path()
    payload = {
        "reason": str(reason or "用户要求停止任务"),
        "by": str(by or "user"),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "file",
    }
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        return {"ok": False, "error": "写入 STOP_ALL 失败: %r" % (e,), "file": p}
    out = {"ok": True, "stopped": True, "file": p}
    out.update(payload)
    out["how"] = ("后续 sw_bridge/mode_gate/physics_bridge/visual_check 命令入口"
                  "会返回 TASK_STOPPED；恢复用 python mode_gate.py resume")
    return out


def clear_stop(by: str = "user") -> dict:
    """清除 STOP_ALL（恢复）。注意：环境变量 DSH_KILL_SWITCH 不会被本函数清除。"""
    p = stop_path()
    removed = False
    try:
        if os.path.isfile(p):
            os.remove(p)
            removed = True
    except Exception as e:
        return {"ok": False, "error": "删除 STOP_ALL 失败: %r" % (e,), "file": p}
    return {
        "ok": True,
        "stopped": _env_on(),
        "file": p,
        "removed": removed,
        "by": str(by or "user"),
        "note": ("停机文件已清除；若环境变量 %s 仍为真，停机仍生效，"
                 "请在调用方取消该环境变量。" % ENV_KILL),
    }


def guard(cmd, allow=()) -> dict | None:
    """命令入口闸门。

    Args:
        cmd: 命令名（用于错误信息 / allow 匹配）。
        allow: 停机期间仍允许的命令名集合（只读/清理/恢复类）。

    Returns:
        已停机且 cmd 不在 allow 中 → 返回 TASK_STOPPED 结构；
        否则返回 None（调用方继续正常执行）。
    """
    if not is_stopped():
        return None
    _c = str(cmd or "").strip().lower()
    _allow = {str(x).strip().lower() for x in (allow or ())}
    if _c in _allow:
        return None
    st = status()
    return {
        "ok": False,
        "gate": "TASK_STOPPED",
        "cmd": cmd,
        "stopped": True,
        "reason": st.get("reason") or "用户已要求停止任务",
        "source": st.get("source"),
        "file": st.get("file"),
        "error": ("【停机开关已生效】%s 被拒绝执行：用户已要求停止任务。"
                  % (cmd or "本命令")),
        "how": [
            "停止期间只允许 status / room-board / close-all / sw-kill 等只读或清理命令；",
            "要恢复任务：python mode_gate.py resume（或删除 STOP_ALL / 取消 DSH_KILL_SWITCH）；",
            "新任务开始前必须 resume，否则所有建模命令都会 fail-closed。",
        ],
    }
