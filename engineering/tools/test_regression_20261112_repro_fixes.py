# -*- coding: utf-8 -*-
"""【2026-11-12 回归】减速器任务两个误杀缺陷：residue-check --force 误清活房间 + dead 误判。

── 现场（task_id=20261111_224045_56A62E32，「建造一个减速器」）──────────────
BUG-3：
    `mode_gate.py residue-check --clean --force` 输出
        【force】清除活动房间时必须保留在世房间
        【force】清除活动房间: 附件件
    这两个房间当时都还活着（总理在监工、附件件已 room-start 在等指令）。
    清完之后 room-board 变空、done 记录丢失，workflow 认为 wave1 未完成，
    被迫重新 room-start + room-end 写回 —— 白跑一轮门禁。
    根因：--force 既表示"清残留"，又顺带表示"清活动房间"，与直觉相反。

BUG-4：
    箱体件子代理 b932c79d 全程活跃（写脚本、发消息、持 SW 锁建模），
    room-board 却报 `state=dead / platform=missing / subagent_id=null`；
    手动补一次 subagent-assign 立刻翻回 running —— 死的是绑定，不是进程。
    根因：判死只看"平台注册表 + 是否有 .SLDPRT 产出"，而该房间正在写脚本，
    还没产出零件 → 证据为空 → 判死。"没产出"被当成了"人没了"。

本测试固定的契约：
  ① `residue-check --clean --force`（不带 --purge-alive）**绝不**清活动房间
  ② `--purge-alive` 单独出现 → 直接拒绝（PURGE_ALIVE_REQUIRES_CLEAN_FORCE）
  ③ `--clean --force --purge-alive` → 才清，且动作留痕写明 purge-alive
  ④ 平台 missing + 无成品产出 + 磁盘上有活人痕迹 → unbound/idle，**不是 dead**
  ⑤ 真死（平台 disposed + 无任何痕迹 + 超宽限）→ 仍然 dead（不能为了修 bug 废掉判死）

隔离：DSH_STATE_DIR 指向临时目录，绝不读写真实状态。运行：
    python engineering/tools/test_regression_20261112_repro_fixes.py
退出码 0=通过。
"""
import json
import os
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = tempfile.mkdtemp(prefix="dsh_20261112_")
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


def platform_registry(agents, status_pulse=True):
    d = {"updatedAt": iso(time.time()), "source": "dsh-engineering-ui", "agents": agents}
    if status_pulse:
        d["pulse"] = {"seq": 1, "at": int(time.time()), "atMs": int(time.time() * 1000),
                      "intervalMs": 10000, "staleMs": 35000, "alive": True}
    return d


def only(board, room):
    rows = [r for r in (board.get("rooms") or []) if r.get("room") == room]
    return rows[0] if rows else {}


# ── 产物证据固定为"无"：BUG-4 的场景正是「没产出零件」 ──────────────────────
mg._recent_artifact_evidence = lambda room, window=None, since=None: {
    "found": False, "newest": None, "count": 0, "scanned_dirs": []}

# 【监控器接入】"总理子代理"已删除；这里换成一个同样"活着但没在建零件"的
#   协调类房间，验证 force 清场时【不得误杀在世房间】的行为不变。
ROOM_PRIME = "协调房间"
ROOM_ATT = "附件件"

# ══════════════════════════════════════════════════════════════════════════
# BUG-3：--force 不该清活动房间
# ══════════════════════════════════════════════════════════════════════════
print("=== BUG-3 ① --clean --force（不带 --purge-alive）不得清活动房间 ===")
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_PRIME: {"active": True, "started_at": time.time() - 600},
           ROOM_ATT: {"active": True, "started_at": time.time() - 300}},
    subs={ROOM_PRIME: {"id": "sid-prime", "assigned_at": time.time() - 500},
          ROOM_ATT: {"id": "sid-att", "assigned_at": time.time() - 200}}))
write_json("platform_agents.json", platform_registry([
    {"id": "sid-prime", "label": ROOM_PRIME, "parentId": "root-1",
     "createdAt": iso(time.time() - 700), "status": "running",
     "lastEventAt": iso(time.time())},
    {"id": "sid-att", "label": ROOM_ATT, "parentId": "root-1",
     "createdAt": iso(time.time() - 400), "status": "running",
     "lastEventAt": iso(time.time())},
]))

res = mg.cmd_residue_check(clean=True, force=True, quiet=True)
_acts = " | ".join(res.get("actions") or [])
check("① 输出里没有「清除活动房间」动作",
      "清除活动房间" not in _acts, _acts)
check("① 输出明确保留活动房间",
      ("保留" in _acts) and (ROOM_PRIME in _acts or ROOM_ATT in _acts), _acts)
check("① protected_alive 列出两个活房间",
      sorted(res.get("protected_alive") or []) == sorted([ROOM_PRIME, ROOM_ATT]),
      res.get("protected_alive"))
# 关键：清完之后房间记录必须还在
_ms = json.load(open(os.path.join(STATE, "mode_state.json"), encoding="utf-8"))
check("① mode_state 里两个房间仍在（没被误清）",
      ROOM_PRIME in (_ms.get("rooms") or {}) and ROOM_ATT in (_ms.get("rooms") or {}),
      sorted((_ms.get("rooms") or {}).keys()))
check("① subagent 映射仍在",
      ROOM_PRIME in (_ms.get("subagents") or {}) and ROOM_ATT in (_ms.get("subagents") or {}),
      sorted((_ms.get("subagents") or {}).keys()))
# room-board 仍能看见它们（BUG-3 的直接症状是"看板变空"）
_board = mg.cmd_room_board()
check("① room-board 仍列出这两个房间",
      bool(only(_board, ROOM_PRIME)) and bool(only(_board, ROOM_ATT)),
      [r.get("room") for r in (_board.get("rooms") or [])])

print("\n=== BUG-3 ② --purge-alive 单独使用 → 拒绝 ===")
res2 = mg.cmd_residue_check(clean=True, force=False, quiet=True, purge_alive=True)
check("② 拒绝并给出错误码",
      res2.get("ok") is False and res2.get("error") == "PURGE_ALIVE_REQUIRES_CLEAN_FORCE",
      res2)
check("② 提示里给出正确用法",
      "--clean --force --purge-alive" in str(res2.get("message") or ""),
      res2.get("message"))
_ms2 = json.load(open(os.path.join(STATE, "mode_state.json"), encoding="utf-8"))
check("② 拒绝时不产生任何清理（房间还在）",
      ROOM_PRIME in (_ms2.get("rooms") or {}), sorted((_ms2.get("rooms") or {}).keys()))

print("\n=== BUG-3 ③ 显式 --clean --force --purge-alive → 才清，且留痕 ===")
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_ATT: {"active": True, "started_at": time.time() - 300}},
    subs={ROOM_ATT: {"id": "sid-att", "assigned_at": time.time() - 200}}))
res3 = mg.cmd_residue_check(clean=True, force=True, quiet=True, purge_alive=True)
_acts3 = " | ".join(res3.get("actions") or [])
check("③ 动作里明确写 purge-alive",
      "purge-alive" in _acts3, _acts3)
_ms3 = json.load(open(os.path.join(STATE, "mode_state.json"), encoding="utf-8"))
check("③ 房间确实被清掉（用户显式要求时仍要给能力）",
      ROOM_ATT not in (_ms3.get("rooms") or {}), sorted((_ms3.get("rooms") or {}).keys()))

# ══════════════════════════════════════════════════════════════════════════
# BUG-4：有活人痕迹就不判死
# ══════════════════════════════════════════════════════════════════════════
print("\n=== BUG-4 ④ 平台 missing + 无零件产出 + 有活人痕迹 → 不是 dead ===")
ROOM_BOX = "箱体件"
_t0 = time.time()
# 工作目录里有一个该房间的建模脚本，刚被写过（BUG-4 现场就是这个状态）
_wd = os.path.join(STATE, "workdir_box")
os.makedirs(_wd, exist_ok=True)
_script = os.path.join(_wd, "%s_建模.py" % ROOM_BOX)
with open(_script, "w", encoding="utf-8") as f:
    f.write("# build script\n")
os.utime(_script, (_t0, _t0))
write_json("workflow_state.json", {"step": "context_asked",
                                   "task_id": "t-bug4",
                                   "work_dir": _wd})
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_BOX: {"active": True, "started_at": _t0 - 3600}},
    subs={ROOM_BOX: {"id": "sid-b932c79d", "assigned_at": _t0 - 900}}))
# 平台注册表里【没有】这个 agent → _plat=missing（BUG-4 的原始症状）
write_json("platform_agents.json", platform_registry([]))

board4 = mg.cmd_room_board()
row4 = only(board4, ROOM_BOX)
check("④ 有活人痕迹时【不是 dead】", row4.get("state") != "dead", row4.get("state"))
# 允许 unbound / idle / running：
#   · unbound  → 平台查不到 + 房间没绑，正确动作是补 subagent-assign
#   · idle     → 平台说不在了，但有人在写脚本
#   · running  → 旧状态字段仍带 running_unverified，看板沿用（比 idle 更乐观，同属安全区）
# 三者共同的硬约束是：不判死、不许重启（下一条断言）。
check("④ 降级为 unbound / idle / running（都不是 dead）",
      row4.get("state") in ("unbound", "idle", "running"), row4.get("state"))
check("④ safe_to_restart=False（严禁杀活进程）",
      row4.get("safe_to_restart") is False, row4)
_tr = mg._recent_live_trace(ROOM_BOX, state=mg.load_state(),
                            subs=(mg.load_state().get("subagents") or {}),
                            now=time.time())
check("④ _recent_live_trace 能看见脚本", _tr.get("found") is True, _tr)
check("④ 痕迹理由里点名脚本/日志",
      any("脚本" in x for x in (_tr.get("reasons") or [])), _tr.get("reasons"))

print("\n=== BUG-4 ⑤ 反证：真死仍判 dead（不能把判死废掉）===")
ROOM_DEAD = "真死房"
write_json("workflow_state.json", {"step": "design", "task_id": "t-dead",
                                   "work_dir": os.path.join(STATE, "no_such_dir")})
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_DEAD: {"active": True, "started_at": time.time() - 7200}},
    subs={ROOM_DEAD: {"id": "sid-dead", "assigned_at": time.time() - 7200}}))
write_json("platform_agents.json", platform_registry([
    {"id": "sid-dead", "label": ROOM_DEAD, "parentId": "root-1",
     "createdAt": iso(time.time() - 7300), "status": "disposed",
     "lastEventAt": iso(time.time() - 3600)},
]))
_board5 = mg.cmd_room_board()
_row5 = only(_board5, ROOM_DEAD)
check("⑤ 无任何痕迹 + 平台 disposed + 超宽限 → dead",
      _row5.get("state") == "dead", _row5.get("state"))
check("⑤ dead 时 safe_to_restart=True（唯一允许重启的状态）",
      _row5.get("safe_to_restart") is True, _row5)

print("\n=== BUG-4 ⑥ 持 SW 锁 = 活人痕迹（强证据）===")
ROOM_SW = "建模房"
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_SW: {"active": True, "started_at": time.time() - 1800}},
    subs={ROOM_SW: {"id": "sid-sw", "assigned_at": time.time() - 1200}},
    # 【监控器接入】owner/queue 必须标明来源才被采信（见 _sw_lock_snapshot）。
    #   旧进程内锁的残留行会被当作"不存在占用"。
    sw={"owner": ROOM_SW, "queue": [], "acquired_at": time.time() - 60,
        "source": "sw_monitor", "available": True}))
write_json("platform_agents.json", platform_registry([]))
_tr6 = mg._recent_live_trace(ROOM_SW, state=mg.load_state(),
                             subs=(mg.load_state().get("subagents") or {}),
                             now=time.time())
check("⑥ 持锁被识别为活人痕迹", _tr6.get("found") is True, _tr6)
check("⑥ 理由写明'正持有 SW 锁'",
      any("SW 锁" in x for x in (_tr6.get("reasons") or [])), _tr6.get("reasons"))
_row6 = only(mg.cmd_room_board(), ROOM_SW)
check("⑥ 不判 dead", _row6.get("state") != "dead", _row6.get("state"))

print("\n=== BUG-7 外购件房间：豁免建模防线，但必须交非空 BOM ===")
ROOM_BUY = "附件件"
_wd7 = os.path.join(STATE, "workdir_buy")
os.makedirs(os.path.join(_wd7, "reports"), exist_ok=True)
write_json("workflow_state.json", {"step": "user_selected", "task_id": "t-buy",
                                   "work_dir": _wd7})
write_json("mode_state.json", fresh_mode_state(
    rooms={ROOM_BUY: {"active": True, "started_at": time.time() - 300,
                      "type": "purchased", "task_id": "t-buy"}},
    subs={ROOM_BUY: {"id": "sid-buy", "assigned_at": time.time() - 200}}))
check("⑦ is_purchased_room 认得 type=purchased",
      mg.is_purchased_room(ROOM_BUY) is True)
check("⑦ is_purchased_room 认得房间名带「外购」",
      mg.is_purchased_room("外购标准件") is True)
check("⑦ 普通建模房间不被误判为外购",
      mg.is_purchased_room("箱体件") is False)

# 无 BOM → 必须拦，且错误码明确（不是三大防线那种含糊的"缺凭据"）
_end7 = mg.cmd_room_end(ROOM_BUY)
check("⑦ 无 BOM 时拒绝下线",
      _end7.get("ok") is False and _end7.get("gate") == "PURCHASED_BOM_REQUIRED", _end7)
check("⑦ 提示里给出期望的 BOM 路径与写法",
      "bom.json" in str(_end7.get("message") or "")
      and "items" in str(_end7.get("message") or ""), _end7.get("message"))
check("⑦ 提示不再让用户 --force（豁免不是绕过）",
      "--force" not in str(_end7.get("message") or ""), _end7.get("message"))

# 空 BOM → 也拦（同样写到任务目录的 reports/）
_bomf = os.path.join(_wd7, "reports", "%s.bom.json" % ROOM_BUY)
with open(_bomf, "w", encoding="utf-8") as _f:
    json.dump({"room": ROOM_BUY, "items": []}, _f, ensure_ascii=False)
_end7b = mg.cmd_room_end(ROOM_BUY)
check("⑦ 空清单时拒绝下线",
      _end7b.get("ok") is False and _end7b.get("gate") == "PURCHASED_BOM_EMPTY", _end7b)

# 有非空 BOM → 放行，且不再要求 physics/domain/看证据
with open(_bomf, "w", encoding="utf-8") as _f:
    json.dump({
        "room": ROOM_BUY,
        "items": [
            {"name": "油标", "qty": 1, "spec": "M16×1.5", "standard": "JB/T 7941.1",
             "supplier": "外购"},
            {"name": "放油螺塞", "qty": 1, "spec": "M12×1.5", "standard": "JB/T 7940"},
            {"name": "通气器", "spec": "M16×1.5"},          # 故意不给 qty
            {"name": "视孔盖", "qty": 1, "spec": "120×80"},
            {"name": "定位销", "qty": 2, "spec": "φ8×30", "standard": "GB/T 117"},
        ]}, _f, ensure_ascii=False, indent=2)
_g7 = mg._purchased_bom_gate(ROOM_BUY)
check("⑦ 非空 BOM 通过校验", _g7.get("ok") is True, _g7)
check("⑦ 统计到 5 项", _g7.get("count") == 5, _g7.get("count"))
check("⑦ 缺 qty 的项被点名提醒（不拦截）",
      _g7.get("missing_qty") == ["通气器"], _g7.get("missing_qty"))
_end7c = mg.cmd_room_end(ROOM_BUY)
check("⑦ 有 BOM 即放行（豁免三大防线/看图/自检）",
      _end7c.get("ok") is True, {k: _end7c.get(k) for k in ("ok", "gate", "error", "message")})
check("⑦ BOM 校验结果不再是 defense 缺口报错",
      "三大防线" not in str(_end7c.get("message") or ""), _end7c.get("message"))

print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
