# -*- coding: utf-8 -*-
r"""回归测试：Bug③ 房间类型未同步进 mode2_plan.json 的 rooms 快照

════════════════════════════════════════════════════════════════════════════
现场缺陷（2026-10-19 循迹小车，见 DSH插件Bug记录.md「三、流程级 · Bug③」）
--------------------------------------------------------------------------
现象：总计子代理把房间类型从 drivetrain/sensor/drawing 改成
      transmission/housing/drafting（否则门禁拒绝），但 mode2_plan.json 的
      subagent_config.rooms 快照从未同步：
        · mode_gate.py room-status  读 mode2_plan → 仍是旧类型 drivetrain/sensor
        · defense_gate.py check-room 读同一份快照 → room_type=drivetrain
        · workflow_gate.py select    用新类型 → 按 transmission 放行
      结果：传动房按 drivetrain 判 → require_physics=false →
            物理/领域凭据被静默豁免（虚假 CRITICAL 也一起放行）。
      更早还有 drawing 导致 CONFIG_MISSING_FINAL_WAVES。

════════════════════════════════════════════════════════════════════════════
【2026-11-18 · 十阶段 → 七阶段】本测试改造
--------------------------------------------------------------------------
'set-subagent-plan' 与 'analyst-bind' 已随十阶段引擎删除。编制现在只能由
七阶段插件 read_design 的房间草案经 register-design 落盘。因此：

  · C/D/E/F/G 改为直接驱动 _normalize_rooms_from_design + 手工写 plan
    （不依赖 workflow_state / 七阶段凭证文件，保持本测试的自足性）；
  · G3 端到端改为【反向锁定】：没有七阶段凭证时 select 必须 fail-closed
    （这正是七阶段版最关键的拒绝行为）；
  · 快照同步（sync-rooms）与三处一致性（room-types）的断言原样保留 —— 
    它们与引擎无关，是被 Bug③ 直接打坏的那部分。

本测试锁住的修复（全部代码级）
------------------------------
【A】类型规范化表：drivetrain→transmission / sensor→housing /
     drawing→drafting 必须成立，且大小写/空白/连字符都容忍。
【B】规范化失败必须明确报错（不静默放过）。
【C】登记编制后，mode2_plan.json 必须出现 subagent_config.rooms，
     且内容 = 规范化后的 [(房间名, 类型), ...]，与 workflow_state 同构。
【D】旧类型被真正改写：提交 drivetrain/sensor/drawing，快照里必须是
     transmission/housing/drafting（不许把旧值原样写进快照）。
【E】只读自检命令 room-types：能读出三处并判一致；制造不一致时报 conflict。
【F】select 在没有七阶段凭证时必须拒绝（fail-closed，不放行建模）。

运行：python test_regression_bug3_roomtypes_sync.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 【测试隔离】必须隔离状态目录，否则会被【真实运行环境】干扰 ──────────────
_ISO_DIR = tempfile.mkdtemp(prefix="iso_bug3_")
os.environ["DSH_STATE_DIR"] = _ISO_DIR

import mode2_flow as mf  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, extra))


def section(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def fresh():
    return tempfile.mkdtemp(prefix="m2bug3_")


def write_plan(d, **kv):
    """直接写计划文件（不经凭证 -> 不依赖 workflow_state / 七阶段状态）。"""
    plan = mf.load_plan(d)
    plan.update(kv)
    mf.save_plan(plan, d)
    return plan


ROOMS_LEGACY = [
    {"name": "底盘结构件", "type": "structural",
     "parts": ["DSH_底盘"], "why": "承载骨架", "depends_on": []},
    # 实测：drivetrain 不在门禁波1类型表 → require_physics=false
    {"name": "传动与轮组", "type": "drivetrain",
     "parts": ["DSH_后轮_左"], "why": "传递动力", "depends_on": ["底盘结构件"]},
    # 实测：sensor 不在白名单 → 物理/领域凭据被静默豁免
    {"name": "传感器与电子件安装", "type": "sensor",
     "parts": ["DSH_传感器支架"], "why": "承载感知", "depends_on": ["底盘结构件"]},
    {"name": "总装验证", "type": "assembly",
     "parts": ["总装"], "why": "装配验证", "depends_on": ["传动与轮组"]},
    # 实测：drawing 导致 CONFIG_MISSING_FINAL_WAVES（门禁只认 drafting）
    {"name": "工程图输出", "type": "drawing",
     "parts": ["工程图"], "why": "出图交付", "depends_on": ["总装验证"]},
]


def register_like(d, rooms=None, rationale="按功能分解与机构综合划房间"):
    """模拟 register-design 的落盘部分（规范化 + 写 plan），不碰凭证。"""
    _n = mf._normalize_rooms_from_design(rooms or ROOMS_LEGACY, rationale)
    if not _n.get("ok"):
        return _n
    norm = _n["rooms"]
    _snap = mf.room_type_snapshot(norm)
    plan = mf.load_plan(d)
    plan["seven_stage"] = {"task_id": "T-BUG3", "ticket": "fake-ticket",
                           "revision": 1, "analyst_session_id": "analyst-1"}
    plan["subagent_plan"] = {
        "decided_by": mf.SUBPLAN_AUTHORITY, "task_id": "T-BUG3",
        "rationale": rationale, "count": len(norm), "rooms": norm,
        "seven_stage_ticket": "fake-ticket", "seven_stage_revision": 1,
        "analyst_session_id": "analyst-1",
    }
    plan["subagent_config"] = {
        "rooms": _snap, "total_rooms": len(_snap),
        "rooms_source": "七阶段插件 read_design（roomProposal）",
        "rooms_rationale": rationale,
        "room_parts": {r["name"]: r.get("parts") or [] for r in norm},
        "room_types_normalized": [{"room": r["name"], "raw": r.get("type_raw"),
                                   "type": r["type"]}
                                  for r in norm if r.get("type_normalized")],
        "synced_from": "seven_stage", "task_id": "T-BUG3",
    }
    mf.save_plan(plan, d)
    return {"ok": True, "rooms": norm, "rooms_snapshot": _snap,
            "auto_added_final_rooms": _n.get("auto_added_final_rooms"),
            "room_types_normalized": plan["subagent_config"]["room_types_normalized"]}


def main():
    # ══════════════════════════════════════════════════════════════════
    section("A. 类型规范化表（三个实测踩坑类型 + 大小写/连字符容忍）")
    _cases = [("drivetrain", "transmission"),
              ("sensor", "housing"),
              ("drawing", "drafting")]
    for raw, want in _cases:
        r = mf.normalize_room_type(raw)
        check("★ %s → %s" % (raw, want), r.get("ok") and r.get("type") == want, r)
        check("  %s 被标记为 normalized=True" % raw, r.get("normalized") is True, r)
    for raw, want in [("DRIVETRAIN", "transmission"),
                      ("  Sensor  ", "housing"),
                      ("drawing", "drafting"),
                      ("Drawing", "drafting")]:
        r = mf.normalize_room_type(raw)
        check("大小写/空白容忍：%r → %s" % (raw, want),
              r.get("ok") and r.get("type") == want, r)
    for canon in mf.CANONICAL_ROOM_TYPES:
        r = mf.normalize_room_type(canon)
        check("规范类型 %s 原样通过" % canon,
              r.get("ok") and r.get("type") == canon and not r.get("normalized"), r)

    # ══════════════════════════════════════════════════════════════════
    section("B. 规范化失败必须明确报错（不静默放过）")
    for bad in ("bogus_type", "", None, "chassis_magic"):
        r = mf.normalize_room_type(bad)
        check("★ 非法类型 %r → 明确失败" % (bad,), not r.get("ok"), r)
        check("  失败时给出规范类型表", bool(r.get("allowed")), r.get("allowed"))

    # ══════════════════════════════════════════════════════════════════
    section("C. 登记编制后必须同步 rooms 快照到 plan 文件")
    d = fresh()
    try:
        plan_before = mf.load_plan(d)
        check("先决条件：同步前 plan 里没有 rooms 快照",
              not ((plan_before.get("subagent_config") or {}).get("rooms")),
              plan_before.get("subagent_config"))

        r = register_like(d)
        check("登记成功", r.get("ok") is True, r.get("error"))
        check("回传 rooms_snapshot", bool(r.get("rooms_snapshot")), r)
        check("回传 room_types_normalized（3 个被规范化）",
              len(r.get("room_types_normalized") or []) == 3,
              r.get("room_types_normalized"))

        plan = mf.load_plan(d)
        cfg = plan.get("subagent_config") or {}
        snap = cfg.get("rooms") or []
        check("★ plan.subagent_config.rooms 已同步", bool(snap), cfg)
        check("★ 快照是 [(房间名, 类型)] 结构（与 workflow_state 同构）",
              all(isinstance(x, list) and len(x) == 2 for x in snap), snap)
        check("★ 快照房间数 = 编制房间数（5）", len(snap) == 5, snap)
        names = [x[0] for x in snap]
        check("★ 快照房间名与编制一致",
              names == [x["name"] for x in ROOMS_LEGACY], names)
        check("快照带 rooms_source 溯源（指向七阶段）",
              "七阶段" in str(cfg.get("rooms_source")), cfg.get("rooms_source"))
        check("快照带 room_parts（零件范围）",
              "DSH_底盘" in ((cfg.get("room_parts") or {}).get("底盘结构件") or []),
              cfg.get("room_parts"))

        # ★ 核心：旧类型必须被真正改写
        types = {x[0]: x[1] for x in snap}
        check("★ drivetrain 在快照里变成 transmission",
              types.get("传动与轮组") == "transmission", types)
        check("★ sensor 在快照里变成 housing",
              types.get("传感器与电子件安装") == "housing", types)
        check("★ drawing 在快照里变成 drafting",
              types.get("工程图输出") == "drafting", types)
        check("★ 快照里不含任何历史类型",
              not any(t in ("drivetrain", "sensor", "drawing") for t in types.values()),
              types)
        check("编制自身也存的是规范类型",
              all(x["type"] in mf.CANONICAL_ROOM_TYPES
                  for x in (plan.get("subagent_plan") or {}).get("rooms", [])),
              [x["type"] for x in (plan.get("subagent_plan") or {}).get("rooms", [])])
        check("编制保留了原始类型（type_raw，便于审计）",
              any(x.get("type_raw") == "drivetrain"
                  for x in (plan.get("subagent_plan") or {}).get("rooms", [])),
              (plan.get("subagent_plan") or {}).get("rooms"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("D. 未知类型 → 登记编制明确拒绝（不静默放过）")
    d = fresh()
    try:
        bad_rooms = [{"name": "神秘房间", "type": "quantum_chassis",
                      "parts": ["P"], "why": "测试", "depends_on": []}]
        r = register_like(d, bad_rooms, "测试非法类型")
        check("★ 未知房间类型 → 拒绝登记", not r.get("ok"), r)
        check("★ 报错点名房间与原始类型",
              "神秘房间" in str(r.get("error"))
              and "quantum_chassis" in str(r.get("error")), r.get("error"))
        check("★ 报错回传规范类型表", bool(r.get("allowed")), r.get("allowed"))
        plan = mf.load_plan(d)
        check("★ 拒绝时不落任何编制/快照",
              plan.get("subagent_plan") is None
              and not (plan.get("subagent_config") or {}),
              plan.get("subagent_config"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("E. 只读自检 room-types：三处一致性")
    d = fresh()
    try:
        register_like(d)
        rep = mf.room_types_report(d)
        check("room-types 只读命令返回 ok", rep.get("ok") is True, rep.get("error"))
        check("★ 自检判一致（编制 = 快照 = 本次登记）",
              rep.get("consistent") is True, rep.get("conflicts"))
        check("★ snapshot_synced=True", rep.get("snapshot_synced") is True, rep)
        check("★ by_room 覆盖全部 5 个房间", len(rep.get("by_room") or []) == 5,
              rep.get("by_room"))
        _row = [x for x in rep["by_room"] if x["room"] == "传动与轮组"][0]
        check("★ 传动房被识别为 transmission（require_physics=True）",
              _row["effective_type"] == "transmission"
              and _row["require_physics"] is True, _row)
        check("★ 传动房落入 wave1", _row.get("wave") == "wave1", _row)
        _row2 = [x for x in rep["by_room"] if x["room"] == "工程图输出"][0]
        check("★ 出图房被识别为 drafting（final 波次，非 wave1）",
              _row2["effective_type"] == "drafting" and _row2.get("wave") == "final",
              _row2)
        check("有规范类型改动时回传 normalized_types",
              len(rep.get("normalized_types") or []) == 3,
              rep.get("normalized_types"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("F. 制造不一致 → 自检必须报 conflict（不得静默说一致）")
    d = fresh()
    try:
        register_like(d)
        plan = mf.load_plan(d)
        # 手工把 plan 快照改回旧类型（模拟 Bug③ 的"快照没刷新"现场）
        plan["subagent_config"]["rooms"] = [["传动与轮组", "sensor"],
                                            ["底盘结构件", "structural"]]
        mf.save_plan(plan, d)
        rep = mf.room_types_report(d)
        check("★ 快照与编制不一致 → ok=False", rep.get("ok") is False, rep.get("ok"))
        check("★ consistent=False", rep.get("consistent") is False, rep)
        check("★ snapshot_synced=False", rep.get("snapshot_synced") is False, rep)
        _c = [x for x in (rep.get("conflicts") or [])
              if x.get("scope") == "plan_snapshot_vs_subagent_plan"]
        check("★ conflicts 明确指出 plan 快照与编制的差异", bool(_c),
              rep.get("conflicts"))
        _cj = json.dumps(_c, ensure_ascii=False)
        check("★ 差异里能看到快照的原始旧值 sensor", "sensor" in _cj, _c)
        check("★ 差异里同时给出规范化后的类型（transmission vs housing）",
              "transmission" in _cj and "housing" in _cj, _c)

        # 再制造"同一房间在不同来源类型不同"的现场
        d2 = fresh()
        try:
            register_like(d2)
            p2 = mf.load_plan(d2)
            p2["subagent_config"]["rooms"] = [["传动与轮组", "housing"]]
            mf.save_plan(p2, d2)
            rep2 = mf.room_types_report(d2)
            _row = [x for x in (rep2.get("by_room") or [])
                    if x["room"] == "传动与轮组"][0]
            check("★ 同一房间来源类型冲突 → 行内 consistent=False",
                  _row["consistent"] is False, _row)
            check("★ 冲突行列出各来源的规范类型",
                  set(_row["normalized"].values()) >= {"transmission", "housing"},
                  _row["normalized"])
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G. 未登记编制时 room-types 必须明确说「无编制」（不假通过）")
    d = fresh()
    try:
        rep = mf.room_types_report(d)
        check("★ 无编制 → ok=False", rep.get("ok") is False, rep.get("ok"))
        check("★ 明确指出需先登记编制",
              "编制" in str(rep.get("error")), rep.get("error"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G2. select 后置对账：sync-rooms 把最终房间表回写快照")
    d = fresh()
    try:
        register_like(d)
        _final = ([x["name"], mf.normalize_room_type(x["type"])["type"]]
                  for x in ROOMS_LEGACY)

        r = mf.sync_rooms_snapshot(_final, d, source="test.sync",
                                   task_id="T-SYNC-1")
        check("★ sync-rooms 成功", r["ok"], r.get("error"))
        plan = mf.load_plan(d)
        snap = (plan.get("subagent_config") or {}).get("rooms") or []
        check("★ 快照房间数 == 编制房间数", len(snap) == len(ROOMS_LEGACY), snap)
        check("★ 快照 synced_from 标注来源",
              (plan.get("subagent_config") or {}).get("synced_from") == "test.sync",
              plan.get("subagent_config"))
        check("★ task_id 一并校正（避免跨任务污染）",
              plan.get("task_id") == "T-SYNC-1", plan.get("task_id"))
        rep = mf.room_types_report(d)
        check("★ 对账后 snapshot_synced=True",
              rep.get("snapshot_synced") is True, rep.get("conflicts"))
        # 非法类型必须明确报错（不静默写一半）
        bad = mf.sync_rooms_snapshot([["房间A", "quantum"]], d, source="test")
        check("★ sync-rooms 非法类型 → 明确报错", not bad["ok"], bad)
        check("  报错回传允许的规范类型表", bool(bad.get("allowed")),
              bad.get("allowed"))
        check("★ 空房间表 → 拒绝（不清空快照）",
              not mf.sync_rooms_snapshot([], d)["ok"])
        _after = (mf.load_plan(d).get("subagent_config") or {}).get("rooms") or []
        check("★ 拒绝后快照保持原样（仍 5 个）",
              len(_after) == len(ROOMS_LEGACY), _after)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G3. 端到端：没有七阶段凭证时 select 必须 fail-closed")
    # 十阶段版这里是"跑完十阶段 → select 放行"。七阶段版没有可伪造的
    # 十阶段产物，真正要锁的是【拒绝路径】：凭证不存在 → 绝不放行建模。
    import subprocess
    _sd = tempfile.mkdtemp(prefix="bug3e2e_state_")
    _wd = None
    _env = dict(os.environ)
    _env["DSH_STATE_DIR"] = _sd
    _env["DSH_SESSION_ID"] = "e2e-root-session"
    _env["PYTHONIOENCODING"] = "utf-8"
    _env["DSH_SEVEN_STAGE_STATE_DIR"] = tempfile.mkdtemp(prefix="sev_e2e_")
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _WG = os.path.join(_HERE, "workflow_gate.py")

    def _run(script, *args):
        _p = subprocess.run([sys.executable, script, *args],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=_HERE, env=_env)
        try:
            return json.loads(_p.stdout or "{}")
        except Exception:
            return {"_raw": (_p.stdout or "")[-400:],
                    "_stderr": (_p.stderr or "")[-400:]}

    try:
        _run(_WG, "reset")
        _init = _run(_WG, "init", "端到端回归：六轴机械臂")
        check("★ init 固定 analysis_engine=seven_v03",
              _init.get("analysis_engine") == "seven_v03",
              _init.get("analysis_engine"))
        check("★ init 记录真实根会话 ID",
              _init.get("seven_stage_root_session_id") == "e2e-root-session",
              _init.get("seven_stage_root_session_id"))
        _run(_WG, "provide_context", "D 并行, B 部分自主")
        _run(_WG, "provide_context", "强需求")
        _run(_WG, "provide_context", "载荷 100N，臂展 800mm")
        with io.open(os.path.join(_sd, "workflow_state.json"), encoding="utf-8") as f:
            _wd = (json.load(f) or {}).get("work_dir")
        check("端到端：拿到本任务 work_dir", bool(_wd), _wd)

        _sel = _run(_WG, "select", "B,D")
        check("★ 无七阶段凭证 → select 拒绝",
              _sel.get("ok") is False, _sel.get("ok"))
        check("★ 拒绝码是 SEVEN_STAGE_HANDOFF_REQUIRED",
              _sel.get("gate") == "SEVEN_STAGE_HANDOFF_REQUIRED",
              _sel.get("gate"))
        check("★ 拒绝体给出可操作步骤（attach → launch → propose_rooms）",
              any("attach" in str(x) for x in (_sel.get("how") or [])),
              _sel.get("how"))
        check("★ 拒绝时不生成任何房间（不放行建模）",
              not (_sel.get("subagent_config") or {}).get("rooms"), _sel)
    finally:
        shutil.rmtree(_sd, ignore_errors=True)
        if _wd:
            shutil.rmtree(_wd, ignore_errors=True)
        shutil.rmtree(_env["DSH_SEVEN_STAGE_STATE_DIR"], ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("H. CLI 全路径（七阶段子命令可构建并真跑）")
    _bad = []
    for _s in ["room-types", "rooms-template", "register-design",
               "recheck-design", "seven-status", "subagent-plan"]:
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                mf.main([_s, "--help"])
        except SystemExit as _e:
            if _e.code not in (0, None):
                _bad.append((_s, _e.code))
        except Exception as _e:
            _bad.append((_s, repr(_e)))
    check("★ 相关子命令 argparse 全部可构建", not _bad, _bad)

    d = fresh()
    try:
        register_like(d)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            code2 = mf.main(["room-types", "--work-dir", d])
        out2 = buf2.getvalue()
        check("★ CLI room-types 返回 0（一致时）", code2 == 0, out2[-300:])
        try:
            j2 = json.loads(out2)
        except Exception:
            j2 = {}
        check("★ CLI room-types 输出合法 JSON 且 consistent=True",
              j2.get("consistent") is True, out2[-300:])
        _snap_out = (j2.get("sources") or {}).get("plan_snapshot") or {}
        if isinstance(_snap_out, dict):
            _types = dict(_snap_out)
        else:
            _types = {x[0]: x[1] for x in _snap_out}
        check("★ CLI 输出里快照类型已规范化",
              _types.get("传动与轮组") == "transmission"
              and _types.get("工程图输出") == "drafting", _types)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 74)
    print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
