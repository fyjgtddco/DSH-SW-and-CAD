#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mode Gate -- 代码级强制关卡
==========================
在 sw_bridge.py 入口被自动调用。核心目的:
- 当任务声明为"大型复杂器械装配"时,必须先登记小屋才能执行建模。
- 未登记小屋就尝试建模 = 直接拒绝。
- 串行模式(E)：同一时间只允许一个房间 active，其他房间必须等待。
- 并行模式(D)：多个房间同时 active；SW 使用窗口互斥（FIFO 排队，先到先用）。

命令:
    python mode_gate.py declare <1|2|3>      # 声明任务模式
    python mode_gate.py room-start <房间名>   # 小屋启动时登记
    python mode_gate.py room-end <房间名>     # 小屋完成/失败后清理
    python mode_gate.py room-fail <房间名>    # 错误回退：清除完成标记，重新排队
    python mode_gate.py sw-request <房间名>   # 加入 SW 使用队列（sw_bridge 自动调用）
    python mode_gate.py sw-wait <房间名> [秒] # 阻塞等待 SW 使用权
    python mode_gate.py sw-release <房间名>   # 释放 SW 使用权
    python mode_gate.py sw-status             # 查看 SW 锁状态
    python mode_gate.py status                # 查看当前关卡状态
    python mode_gate.py check                 # 检查当前是否允许建模
"""
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_state.json")
WORKFLOW_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_state.json")
MODE_NAMES = {"1": "基础零件搭建", "2": "大型复杂器械装配", "3": "原图解分析"}
NL = chr(10)
SW_MAX_HOLD = 3600  # SW 锁最长占用秒数（防死锁，超时后等待方可接管）


def _fresh_state():
    return {"mode": "1", "rooms": {}, "declared_at": None, "mode_name": MODE_NAMES["1"],
            "sw_lock": {"owner": None, "queue": [], "acquired_at": None}}


def load_state():
    if not os.path.exists(STATE_PATH):
        return _fresh_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("sw_lock", {"owner": None, "queue": [], "acquired_at": None})
        return state
    except Exception:
        return _fresh_state()


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _norm_pmode(raw):
    if raw is None:
        return None
    r = str(raw).strip().upper()
    if r in ("D", "PARALLEL"):
        return "parallel"
    if r in ("E", "SEQUENTIAL"):
        return "sequential"
    return raw


def get_parallel_mode():
    """读取并行策略并归一化: D -> parallel, E -> sequential。"""
    if not os.path.exists(WORKFLOW_STATE_PATH):
        return None
    try:
        with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
            ws = json.load(f)
        return _norm_pmode(ws.get("parallel_mode"))
    except Exception:
        return None


def cmd_declare(mode):
    state = load_state()
    mode = str(mode)
    if mode not in MODE_NAMES:
        return {"ok": False, "error": "模式必须为 1/2/3,收到 " + str(mode)}
    state["mode"] = mode
    state["mode_name"] = MODE_NAMES[mode]
    state["declared_at"] = time.time()
    save_state(state)
    return {"ok": True, "mode": mode, "mode_name": MODE_NAMES[mode],
            "requires_subagent": mode == "2"}


def cmd_room_start(name):
    state = load_state()
    if str(state.get("mode")) != "2":
        err = "当前不是大型复杂器械装配模式,无需登记小屋。"
        err += "若确认为装配任务请先: mode_gate.py declare 2"
        return {"ok": False, "error": err}
    pmode = get_parallel_mode()
    rooms = state.setdefault("rooms", {})
    active_rooms = [r for r, v in rooms.items() if v.get("active")]
    if pmode == "sequential" and active_rooms:
        active = active_rooms[0]
        err = "【串行模式锁定】房间 [%s] 登记被拒绝。" % name
        err += NL + "当前已有活动房间: %s" % active
        err += NL + "必须先完成当前房间（调用 room-end）后，才能开启新房间。"
        err += NL + "调用: python mode_gate.py room-end %s" % active
        return {"ok": False, "error": err}
    rooms[name] = {"active": True, "started_at": time.time()}
    save_state(state)
    msg = "小屋 [%s] 已登记,可开始建模" % name
    if pmode == "parallel" and len([r for r, v in rooms.items() if v.get("active")]) > 1:
        msg += NL + "【并行模式】SW 使用窗口互斥已启用: 你的 sw_bridge 调用将自动排队(FIFO, 先到先用)。"
    return {"ok": True, "room": name, "message": msg}


def cmd_room_end(name):
    state = load_state()
    rooms = state.get("rooms", {})
    released_sw = False
    if name in rooms:
        rooms[name]["active"] = False
        rooms[name]["ended_at"] = time.time()
    lk = state.get("sw_lock", {})
    if lk.get("owner") == name:
        lk["owner"] = None
        lk["acquired_at"] = None
        released_sw = True
    save_state(state)
    msg = "小屋 [%s] 已清理" % name
    if released_sw:
        msg += "，并自动释放了 SW 使用锁"
    return {"ok": True, "room": name, "sw_lock_released": released_sw, "message": msg}


def cmd_room_fail(name):
    """错误回退：清除房间的完成标记，使其重新回到待处理队列。"""
    state = load_state()
    rooms = state.setdefault("rooms", {})
    if name in rooms:
        rooms[name]["active"] = False
        rooms[name]["ended_at"] = None
        rooms[name]["failed_at"] = time.time()
    else:
        rooms[name] = {"active": False, "ended_at": None, "failed_at": time.time()}
    lk = state.get("sw_lock", {})
    if lk.get("owner") == name:
        lk["owner"] = None
        lk["acquired_at"] = None
    save_state(state)
    return {"ok": True, "room": name,
            "message": "房间 [%s] 已回退到待处理状态。重新调用 workflow_gate.py select 可再次获得该房间。" % name}


# ── SW 使用窗口互斥锁（并行模式多房间时生效，FIFO 先到先用）──────────────

def _sw_lock(state):
    return state.setdefault("sw_lock", {"owner": None, "queue": [], "acquired_at": None})


def _lock_needed(state):
    """只有 模式2 + 并行 + 多房间active 时才需要锁；串行/单房间自动跳过。"""
    if str(state.get("mode")) != "2":
        return False
    if get_parallel_mode() != "parallel":
        return False
    active = [r for r, v in state.get("rooms", {}).items() if v.get("active")]
    return len(active) > 1


def cmd_sw_request(name):
    state = load_state()
    lk = _sw_lock(state)
    if not _lock_needed(state):
        save_state(state)
        return {"ok": True, "need_lock": False, "room": name}
    if lk.get("owner") == name:
        return {"ok": True, "need_lock": True, "acquired": True, "room": name}
    if name not in lk.get("queue", []):
        lk.setdefault("queue", []).append(name)
    pos = lk["queue"].index(name) + 1
    save_state(state)
    return {"ok": True, "need_lock": True, "queued": True, "position": pos,
            "owner": lk.get("owner"), "wait_timeout": 1800}


def cmd_sw_wait(name, timeout=300):
    """阻塞轮询直到获得 SW 使用权（FIFO: 队首且锁空闲时上位）。"""
    deadline = time.time() + int(timeout)
    state = load_state()
    lk = _sw_lock(state)
    if lk.get("owner") != name and name not in lk.get("queue", []):
        lk["queue"].append(name)
        save_state(state)
    while True:
        state = load_state()
        lk = _sw_lock(state)
        if lk.get("owner") == name:
            return {"ok": True, "acquired": True, "room": name}
        q = lk.get("queue", [])
        held = time.time() - lk.get("acquired_at", 0) if lk.get("acquired_at") else 0
        if not lk.get("owner") and q and q[0] == name:
            lk["owner"] = name
            lk["acquired_at"] = time.time()
            lk["queue"] = q[1:]
            save_state(state)
            return {"ok": True, "acquired": True, "room": name, "waited": True}
        if lk.get("owner") and held > SW_MAX_HOLD:
            # 防死锁：占用超时，强制接管
            old = lk.get("owner")
            lk["owner"] = name
            lk["acquired_at"] = time.time()
            lk["queue"] = [x for x in q if x != name]
            save_state(state)
            return {"ok": True, "acquired": True, "room": name, "took_over": True,
                    "took_over_from": old, "note": "原占用者超过%d秒未释放，已强制接管" % SW_MAX_HOLD}
        if time.time() >= deadline:
            pos = (q.index(name) + 1) if name in q else None
            return {"ok": False, "acquired": False, "room": name, "owner": lk.get("owner"),
                    "position": pos,
                    "error": "SW 正被 [%s] 占用(已 %.0f 秒)，等待超时。请稍后重试。" % (lk.get("owner"), held)}
        time.sleep(1.0)


def cmd_sw_release(name):
    state = load_state()
    lk = _sw_lock(state)
    if lk.get("owner") == name:
        lk["owner"] = None
        lk["acquired_at"] = None
        save_state(state)
        nxt = lk.get("queue", [None])[0] if lk.get("queue") else None
        return {"ok": True, "released": name, "next_in_queue": nxt,
                "message": "SW 使用权已释放，队列下一个: %s" % nxt}
    return {"ok": True, "released": None, "owner": lk.get("owner"),
            "note": "该房间未持有 SW 锁"}


def cmd_sw_status():
    state = load_state()
    lk = state.get("sw_lock", {})
    held = 0
    if lk.get("acquired_at"):
        held = time.time() - lk["acquired_at"]
    return {"ok": True, "owner": lk.get("owner"), "queue": lk.get("queue", []),
            "held_seconds": round(held, 1), "parallel_mode": get_parallel_mode(),
            "lock_needed": _lock_needed(state)}


def cmd_status():
    state = load_state()
    active = [k for k, v in state.get("rooms", {}).items() if v.get("active")]
    return {
        "ok": True,
        "mode": state.get("mode"),
        "mode_name": state.get("mode_name"),
        "requires_subagent": str(state.get("mode")) == "2",
        "active_rooms": active,
        "room_count": len(active),
        "parallel_mode": get_parallel_mode() or "unknown",
    }


def cmd_check():
    state = load_state()
    mode = str(state.get("mode", "1"))
    if mode != "2":
        return {"ok": True, "gate": "OPEN", "mode": mode}
    active = [k for k, v in state.get("rooms", {}).items() if v.get("active")]
    pmode = get_parallel_mode()
    if pmode == "sequential":
        if len(active) == 1:
            return {"ok": True, "gate": "OPEN", "mode": mode, "active_rooms": active}
        err = "【串行模式锁定】当前无活动房间或房间状态异常。"
        err += NL + "请确保先调用 room-end 结束上一个房间，再开启下一个。"
        err += NL + "当前 active: %s" % active
        return {"ok": False, "gate": "BLOCKED", "mode": mode, "error": err}
    else:
        if active:
            return {"ok": True, "gate": "OPEN", "mode": mode, "active_rooms": active}
        err = "【强制关卡】当前声明为大型复杂器械装配模式,必须先创建子代理小屋才能建模。"
        err += NL + "正确流程:1) 用 subagent/subagent_fork 创建小屋 -> "
        err += "2) 运行 mode_gate.py room-start <房间名> 登记 -> 3) 小屋内执行建模。"
        err += NL + "禁止在主对话中直接建模!"
        return {"ok": False, "gate": "BLOCKED", "mode": mode, "error": err}


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"ok": False, "error": "用法: mode_gate.py <declare|room-start|room-end|room-fail|sw-request|sw-wait|sw-release|sw-status|status|check> [...]",
                          "modes": MODE_NAMES}, ensure_ascii=False, indent=2))
        return
    cmd = args[0]
    if cmd == "declare":
        result = cmd_declare(args[1] if len(args) > 1 else "1")
    elif cmd == "room-start":
        result = cmd_room_start(args[1] if len(args) > 1 else "小屋")
    elif cmd == "room-end":
        result = cmd_room_end(args[1] if len(args) > 1 else "小屋")
    elif cmd == "room-fail":
        result = cmd_room_fail(args[1] if len(args) > 1 else "小屋")
    elif cmd == "sw-request":
        result = cmd_sw_request(args[1] if len(args) > 1 else "unknown")
    elif cmd == "sw-wait":
        result = cmd_sw_wait(args[1] if len(args) > 1 else "unknown",
                             int(args[2]) if len(args) > 2 else 300)
    elif cmd == "sw-release":
        result = cmd_sw_release(args[1] if len(args) > 1 else "unknown")
    elif cmd == "sw-status":
        result = cmd_sw_status()
    elif cmd == "status":
        result = cmd_status()
    elif cmd == "check":
        result = cmd_check()
    else:
        result = {"ok": False, "error": "未知命令: " + cmd}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()