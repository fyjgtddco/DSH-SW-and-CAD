# -*- coding: utf-8 -*-
"""【阶段 C 回归测试】mode_gate.room-board：unbound / waiting_sw / dead 三态。

── 这份测试守的是什么 ────────────────────────────────────────────────────
用户会话实测（session-31c56a28）：主对话手动跑 while(true) platform-sync 造假
心跳；subagent-assign 从没调用过；于是 recover 把还在跑的房间判成"僵尸残留"，
紧接着 reset 把三个正在建模的子代理全杀了。另外 SW 锁排队中的小屋和"死了"
看起来一模一样。

因此 room-board 的判定必须是【代码 + 平台事实】决定的，本测试固定三条断言：
  ① 平台 running + 房间没绑 subagent → state=unbound 且 safe_to_restart=False
  ② 平台 running + 房间在 SW 队列里      → state=waiting_sw 且 safe_to_restart=False
  ③ 平台 disposed + 无证据 + 超宽限      → state=dead（唯一允许重启的死亡态）

隔离方式：临时目录 + DSH_STATE_DIR（_store.state_dir() 的第一优先级），
绝不读写真实状态目录。运行：
    python engineering/tools/test_regression_room_board.py
退出码 0=通过。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = tempfile.mkdtemp(prefix="dsh_roomboard_")
# ── 隔离：所有状态落在这个临时目录（必须先设环境变量，再 import mode_gate）──
os.environ["DSH_STATE_DIR"] = STATE
os.environ.pop("DSH_ENGINEERING_ROOT", None)
sys.path.insert(0, HERE)

import mode_gate as mg  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)[:400]) if (detail and not cond) else ""))


def write_json(name, obj):
    p = os.path.join(STATE, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return p


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def fresh_mode_state(rooms=None, subs=None, sw=None):
    return {
        "mode": "2",
        "mode_name": mg.MODE_NAMES.get("2"),
        "rooms": rooms or {},
        "subagents": subs or {},
        "sw_lock": sw or {"owner": None, "queue": [], "acquired_at": None,
                          "prev_owner": None, "sw_by_room": False},
    }


def platform_registry(agents):
    return {
        "updatedAt": iso(time.time()),
        "source": "dsh-engineering-ui",
        "agents": agents,
    }


def only(board, room):
    rows = [r for r in (board.get("rooms") or []) if r.get("room") == room]
    return rows[0] if rows else {}


# ── 测试确定性：产物证据按"无"固定 ──────────────────────────────────────────
# 真实 output/ 目录里可能残留历史 .SLDPRT（mtime 落在窗口内就会变成"活着证据"）。
# 本测试只验证状态推导本身，故把磁盘证据这一路固定为"无"，避免外部文件干扰。
mg._recent_artifact_evidence = lambda room, window=None, since=None: {
    "found": False, "newest": None, "count": 0, "scanned_dirs": []}

ROOM_A = "结构件"
ROOM_B = "传动机构"
SID_A = "sid-aaaa-0001"

print("=== ① 平台 running + 房间没绑 subagent → unbound（不是僵尸！）===")
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_A: {"active": True, "started_at": time.time() - 40}},
    subs={}))
write_json("platform_agents.json", platform_registry([{
    "id": SID_A,
    "label": "结构件房间",
    "parentId": "root-0001",
    "createdAt": iso(time.time() - 120),
    "status": "running",
    "lastEventAt": iso(time.time()),
}]))
board = mg.cmd_room_board()
row = only(board, ROOM_A)
check("board.ok=True", board.get("ok") is True, board)
check("① state=unbound", row.get("state") == "unbound", row)
check("① 不是僵尸/dead", row.get("state") not in ("dead",), row.get("state"))
check("① safe_to_restart=False", row.get("safe_to_restart") is False, row)
check("① next_action 指向 subagent-assign",
      "subagent-assign" in str(row.get("next_action") or ""), row.get("next_action"))
check("① platform_status=running", row.get("platform_status") == "running", row)
check("① subagent_label 从注册表回填",
      row.get("subagent_label") == "结构件房间", row.get("subagent_label"))
# residue-check 也必须把它归到 unbound，而不是 zombie（误杀链的第二环）
res = mg.cmd_residue_check(quiet=True)
check("① residue-check: unbound 列表含该房间",
      ROOM_A in [x.get("room") for x in (res.get("unbound") or [])], res.get("unbound"))
check("① residue-check: 不在 zombie 里",
      ROOM_A not in [x.get("room") for x in (res.get("zombie") or [])], res.get("zombie"))
check("① residue-check: unbound 不计入 has_residue",
      res.get("has_residue") is False, res.get("has_residue"))
_note = ""
for _x in (res.get("unbound") or []):
    if _x.get("room") == ROOM_A:
        _note = _x.get("note") or ""
check("① unbound note 写明禁止 reset + 补 assign",
      ("禁止 reset" in _note) and ("subagent-assign" in _note), _note)

print("\n=== ② 平台 running + 在 SW 队列里 → waiting_sw（正常排队，禁判死）===")
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_B: {"active": True, "started_at": time.time() - 300}},
    subs={ROOM_B: {"id": "sid-bbbb-0002", "assigned_at": time.time() - 200}},
    sw={"owner": ROOM_A, "queue": [ROOM_B], "acquired_at": time.time() - 8,
        "prev_owner": ROOM_A, "sw_by_room": True}))
write_json("platform_agents.json", platform_registry([{
    "id": "sid-bbbb-0002",
    "label": "传动机构房间",
    "parentId": "root-0001",
    "createdAt": iso(time.time() - 400),
    "status": "running",
    "lastEventAt": iso(time.time() - 30),
}]))
board = mg.cmd_room_board()
row = only(board, ROOM_B)
check("② state=waiting_sw", row.get("state") == "waiting_sw", row)
check("② safe_to_restart=False", row.get("safe_to_restart") is False, row)
check("② 阻断理由写明'排队等 SolidWorks 锁'",
      "排队" in str(row.get("restart_block_reason") or ""),
      row.get("restart_block_reason"))
check("② sw_queue_pos=1", row.get("sw_queue_pos") == 1, row.get("sw_queue_pos"))
check("② 顶层 sw_lock 暴露队列",
      (board.get("sw_lock") or {}).get("queue") == [ROOM_B], board.get("sw_lock"))
# least-surprise：room-status 的旧字段仍兼容，且 state 与 board 一致
rs = mg.cmd_room_status()
_row_rs = [r for r in (rs.get("rooms") or []) if r.get("room") == ROOM_B]
check("② room-status 保留旧字段 status/safe_to_restart",
      bool(_row_rs) and ("status" in _row_rs[0])
      and (_row_rs[0].get("safe_to_restart") is False), _row_rs)
check("② room-status.state 与 board 一致",
      bool(_row_rs) and _row_rs[0].get("state") == "waiting_sw", _row_rs)

print("\n=== ③ 平台 disposed + 无证据 + 超宽限 → dead（唯一可重启的死亡态）===")
ROOM_C = "壳体机架"
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_C: {"active": True, "started_at": time.time() - 3600}},
    subs={ROOM_C: {"id": "sid-cccc-0003", "assigned_at": time.time() - 3600}}))
write_json("platform_agents.json", platform_registry([{
    "id": "sid-cccc-0003",
    "label": "壳体机架房间",
    "parentId": "root-0001",
    "createdAt": iso(time.time() - 3700),
    "status": "disposed",
    "lastEventAt": iso(time.time() - 1800),
}]))
board = mg.cmd_room_board()
row = only(board, ROOM_C)
check("③ state=dead", row.get("state") == "dead", row)
check("③ platform_status=inactive（disposed 的映射）",
      row.get("platform_status") == "inactive", row.get("platform_status"))
check("③ safe_to_restart=True（平台已确认不在运行）",
      row.get("safe_to_restart") is True, row)
check("③ 产物/报告证据为空",
      row.get("artifact_newest") is None and row.get("report_age_sec") is None, row)

print("\n=== ④ 契约自检：board JSON 字段齐全 ===")
board = mg.cmd_room_board()
for _k in ("ok", "generated_at", "rooms", "sw_lock", "summary", "hint"):
    check("顶层字段 %s" % _k, _k in board, sorted(board.keys()))
_row = only(board, ROOM_C)
need = ("room", "type", "state", "status_legacy", "detail", "subagent_id",
        "subagent_label", "platform_status", "report_stage", "report_age_sec",
        "artifact_newest", "sw_queue_pos", "safe_to_restart",
        "restart_block_reason", "next_action")
for _k in need:
    check("房间字段 %s" % _k, _k in _row, sorted(_row.keys()))
check("state 取值合法",
      _row.get("state") in mg.ROOM_BOARD_STATES, _row.get("state"))

print("\n=== ⑤ CLI 分派：room-board / board / --room / --json ===")
env = dict(os.environ)
env["DSH_STATE_DIR"] = STATE
_mg_path = os.path.join(HERE, "mode_gate.py")
_ok_dispatch = True
for _argv in (["room-board", "--json"], ["board"], ["room-board", "--room", ROOM_C]):
    try:
        _p = subprocess.run([sys.executable, _mg_path] + _argv,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=120, env=env)
        _out = (_p.stdout or "").strip()
        _j = json.loads(_out) if _out.startswith("{") else {}
        _ok = (_p.returncode == 0 and _j.get("ok") is True)
        if not _ok:
            _ok_dispatch = False
        check("CLI %s 退出码0且输出 JSON" % " ".join(_argv),
              _ok, {"rc": _p.returncode, "err": (_p.stderr or "")[:300],
                    "out": _out[:300]})
    except Exception as _e:
        _ok_dispatch = False
        check("CLI %s 可运行" % " ".join(_argv), False, repr(_e))
check("--room 只返回一个房间",
      len(json.loads(subprocess.run(
          [sys.executable, _mg_path, "room-board", "--room", ROOM_C, "--json"],
          capture_output=True, text=True, encoding="utf-8", timeout=120,
          env=env).stdout or "{}").get("rooms") or []) == 1)

print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
