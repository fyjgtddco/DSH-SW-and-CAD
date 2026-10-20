# -*- coding: utf-8 -*-
r"""回归测试：Bug③ set-subagent-plan 不刷新 mode2_plan.json 的 rooms 快照

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

本测试锁住的修复（全部代码级）
------------------------------
【A】类型规范化表：drivetrain→transmission / sensor→housing /
     drawing→drafting 必须成立，且大小写/空白/连字符都容忍。
【B】规范化失败必须明确报错（不静默放过）：未知类型 → set-subagent-plan
     拒绝，且报错里给出规范类型表。
【C】快照同步：set-subagent-plan 落盘后，mode2_plan.json 必须出现
     subagent_config.rooms，且内容 = 规范化后的 [(房间名, 类型), ...]，
     与 workflow_state.subagent_config.rooms 同构。
【D】旧类型被真正改写：提交 drivetrain/sensor/drawing，快照里必须是
     transmission/housing/drafting（不许把旧值原样写进快照）。
【E】只读自检命令 room-types：能读出三处（编制/快照/workflow_state）并
     判一致；制造不一致时必须报 conflict（不能静默说一致）。
【F】CLI 全路径：room-types 子命令可构建并真跑。

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


def prime_all_stages(d):
    """把十阶段按序走完（set_subagent_plan 的前置门禁）。"""
    mf.set_depth("lite", d)
    n = 0
    for sid in mf.STAGE_IDS:
        assert mf.begin_stage(sid, d)["ok"], sid
        q = mf.record_question("题目-%s" % sid, sid, "需要该参数", d)
        assert q["ok"], q
        n = q["question"]["seq"]
        assert mf.answer_question(n, "答复-%s" % sid, d)["ok"]
        assert mf.mark_stage_done(sid, "产物-%s" % sid, d)["ok"], sid


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
    section("C. set-subagent-plan 必须同步 rooms 快照到 plan 文件")
    d = fresh()
    try:
        prime_all_stages(d)
        plan_before = mf.load_plan(d)
        check("先决条件：同步前 plan 里没有 rooms 快照",
              not ((plan_before.get("subagent_config") or {}).get("rooms")),
              plan_before.get("subagent_config"))

        r = mf.set_subagent_plan(ROOMS_LEGACY, "按功能分解与机构综合划房间",
                                 "总计子代理", d)
        check("set-subagent-plan 成功", r["ok"], r.get("error"))
        check("回传 rooms_snapshot", bool(r.get("rooms_snapshot")), r)
        check("回传 room_types_normalized（3 个被规范化）",
              len(r.get("room_types_normalized") or []) == 3, r.get("room_types_normalized"))

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
        check("快照带 rooms_source 溯源",
              "总计子代理" in str(cfg.get("rooms_source")), cfg.get("rooms_source"))
        check("快照带 room_parts（零件范围）",
              "DSH_底盘" in ((cfg.get("room_parts") or {}).get("底盘结构件") or []),
              cfg.get("room_parts"))
        check("快照记录 synced_at", bool(cfg.get("synced_at")), cfg)

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
              plan.get("subagent_plan", {}).get("rooms"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("D. 未知类型 → set-subagent-plan 明确拒绝（不静默放过）")
    d = fresh()
    try:
        prime_all_stages(d)
        bad_rooms = [{"name": "神秘房间", "type": "quantum_chassis",
                      "parts": ["P"], "why": "测试", "depends_on": []}]
        r = mf.set_subagent_plan(bad_rooms, "测试非法类型", "总计子代理", d)
        check("★ 未知房间类型 → 拒绝登记", not r["ok"], r)
        check("★ 报错点名房间与原始类型",
              "神秘房间" in str(r.get("error")) and "quantum_chassis" in str(r.get("error")),
              r.get("error"))
        check("★ 报错回传规范类型表", bool(r.get("allowed")), r.get("allowed"))
        plan = mf.load_plan(d)
        check("★ 拒绝时不落任何编制/快照",
              plan.get("subagent_plan") is None and not (plan.get("subagent_config") or {}),
              plan.get("subagent_config"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("E. 只读自检 room-types：三处一致性")
    d = fresh()
    try:
        prime_all_stages(d)
        mf.set_subagent_plan(ROOMS_LEGACY, "按功能分解划房间", "总计子代理", d)
        rep = mf.room_types_report(d)
        check("room-types 只读命令返回 ok", rep.get("ok") is True, rep.get("error"))
        check("★ 自检判一致（编制 = 快照 = 本次登记）",
              rep.get("consistent") is True, rep.get("conflicts"))
        check("★ snapshot_synced=True", rep.get("snapshot_synced") is True, rep)
        check("★ by_room 覆盖全部 5 个房间", len(rep.get("by_room") or []) == 5,
              rep.get("by_room"))
        _row = [x for x in rep["by_room"] if x["room"] == "传动与轮组"][0]
        check("★ 传动房被识别为 transmission（require_physics=True）",
              _row["effective_type"] == "transmission" and _row["require_physics"] is True,
              _row)
        check("★ 传动房落入 wave1", _row.get("wave") == "wave1", _row)
        _row2 = [x for x in rep["by_room"] if x["room"] == "工程图输出"][0]
        check("★ 出图房被识别为 drafting（final 波次，非 wave1）",
              _row2["effective_type"] == "drafting" and _row2.get("wave") == "final",
              _row2)
        check("有规范类型改动时回传 normalized_types",
              len(rep.get("normalized_types") or []) == 3, rep.get("normalized_types"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("F. 制造不一致 → 自检必须报 conflict（不得静默说一致）")
    d = fresh()
    try:
        prime_all_stages(d)
        mf.set_subagent_plan(ROOMS_LEGACY, "按功能分解划房间", "总计子代理", d)
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
        check("★ conflicts 明确指出 plan 快照与编制的差异", bool(_c), rep.get("conflicts"))
        _cj = json.dumps(_c, ensure_ascii=False)
        check("★ 差异里能看到快照的原始旧值 sensor", "sensor" in _cj, _c)
        check("★ 差异里同时给出规范化后的类型（transmission vs housing）",
              "transmission" in _cj and "housing" in _cj, _c)

        # 再制造"同一房间在不同来源类型不同"的现场
        d2 = fresh()
        try:
            prime_all_stages(d2)
            mf.set_subagent_plan(ROOMS_LEGACY, "按功能分解划房间", "总计子代理", d2)
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
        prime_all_stages(d)
        rep = mf.room_types_report(d)
        check("★ 无编制 → ok=False", rep.get("ok") is False, rep.get("ok"))
        check("★ 明确指出需先登记编制",
              "编制" in str(rep.get("error")), rep.get("error"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G2. select 后置对账：sync-rooms 把最终房间表（含总理）回写快照")
    # set-subagent-plan 写的快照不含"固定追加的总理子代理"；select 追加后必须回写，
    # 否则 plan 快照与 workflow_state 永远差一个房间 → 假告警掩盖真不一致。
    d = fresh()
    try:
        prime_all_stages(d)
        mf.set_subagent_plan(ROOMS_LEGACY, "按功能分解划房间", "总计子代理", d)
        _final = ([x["name"], mf.normalize_room_type(x["type"])["type"]]
                  for x in ROOMS_LEGACY)
        _final = list(_final) + [["总理子代理", "prime"]]
        r = mf.sync_rooms_snapshot(_final, d, source="test.sync",
                                   task_id="T-SYNC-1")
        check("★ sync-rooms 成功", r["ok"], r.get("error"))
        plan = mf.load_plan(d)
        snap = (plan.get("subagent_config") or {}).get("rooms") or []
        check("★ 快照含总理子代理（6 个房间）", len(snap) == 6, snap)
        check("★ 快照写了总理子代理类型 prime",
              ["总理子代理", "prime"] in snap, snap)
        check("★ 快照 synced_from 标注来源",
              (plan.get("subagent_config") or {}).get("synced_from") == "test.sync", plan.get("subagent_config"))
        check("★ task_id 一并校正（避免跨任务污染）", plan.get("task_id") == "T-SYNC-1", plan.get("task_id"))
        rep = mf.room_types_report(d)
        check("★ 对账后 snapshot_synced=True", rep.get("snapshot_synced") is True, rep.get("conflicts"))
        # 非法类型必须明确报错（不静默写一半）
        bad = mf.sync_rooms_snapshot([["房间A", "quantum"]], d, source="test")
        check("★ sync-rooms 非法类型 → 明确报错", not bad["ok"], bad)
        check("  报错回传允许的规范类型表", bool(bad.get("allowed")), bad.get("allowed"))
        # 空表必须拒绝（不能把快照清空）
        check("★ 空房间表 → 拒绝（不清空快照）",
              not mf.sync_rooms_snapshot([], d)["ok"])
        _after = (mf.load_plan(d).get("subagent_config") or {}).get("rooms") or []
        check("★ 拒绝后快照保持原样（仍 6 个）", len(_after) == 6, _after)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G3. 端到端：select 把最终房间表（含总理）回写 plan 快照")
    # Bug③ 的现场是"编制改了、快照没改"，且 select 后还追加了固定的总理子代理。
    # 这里真跑 workflow_gate.select，验证 plan 快照被写回、三处一致。
    import subprocess
    _sd = tempfile.mkdtemp(prefix="bug3e2e_state_")
    _wd = None
    _env = dict(os.environ)
    _env["DSH_STATE_DIR"] = _sd
    _env["PYTHONIOENCODING"] = "utf-8"
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _WG = os.path.join(_HERE, "workflow_gate.py")
    _M2 = os.path.join(_HERE, "mode2_flow.py")

    def _run(script, *args):
        _p = subprocess.run([sys.executable, script, *args],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=_HERE, env=_env)
        try:
            return json.loads(_p.stdout or "{}")
        except Exception:
            return {"_raw": (_p.stdout or "")[-400:], "_stderr": (_p.stderr or "")[-400:]}

    try:
        _run(_WG, "reset")
        _run(_WG, "init", "端到端回归：六轴机械臂")
        _run(_WG, "provide_context", "D 并行, B 部分自主")
        _run(_WG, "provide_context", "强需求")
        _run(_WG, "provide_context", "载荷 100N，臂展 800mm")
        with io.open(os.path.join(_sd, "workflow_state.json"), encoding="utf-8") as f:
            _wd = (json.load(f) or {}).get("work_dir")
        check("端到端：拿到本任务 work_dir", bool(_wd), _wd)

        _run(_M2, "set-depth", "full", "--work-dir", _wd)
        for _sid in mf.STAGE_IDS:
            _run(_M2, "begin-stage", _sid, "--work-dir", _wd)
            _q = _run(_M2, "ask", "题目-" + _sid, "--stage", _sid,
                      "--reason", "该阶段需要该参数", "--work-dir", _wd)
            _seq = (_q.get("question") or {}).get("seq")
            _run(_M2, "answer-question", str(_seq), "答复-" + _sid, "--work-dir", _wd)
            _run(_M2, "mark-stage-done", _sid, "--artifact", "产物-" + _sid,
                 "--work-dir", _wd)
        _rj = os.path.join(_sd, "rooms.json")
        with io.open(_rj, "w", encoding="utf-8") as f:
            json.dump(ROOMS_LEGACY, f, ensure_ascii=False)
        _sp = _run(_M2, "set-subagent-plan", "--rooms-json", "@" + _rj,
                   "--rationale", "按功能分解", "--decided-by", "总计子代理",
                   "--work-dir", _wd)
        check("端到端：编制登记成功", _sp.get("ok") is True, _sp.get("error"))

        _sel = _run(_WG, "select", "B,D")
        check("端到端：select 放行", _sel.get("ok") is True,
              {"gate": _sel.get("gate"), "error": _sel.get("error")})
        _cfg = _sel.get("subagent_config") or {}
        check("★ 端到端：select 回传 rooms_synced_to_plan=True",
              _cfg.get("rooms_synced_to_plan") is True, _cfg.get("rooms_sync_error"))

        _rt = _run(_M2, "room-types", "--work-dir", _wd)
        check("★ 端到端：select 后三处 room_type 一致",
              _rt.get("consistent") is True, _rt.get("conflicts"))
        check("★ 端到端：snapshot_synced=True", _rt.get("snapshot_synced") is True, _rt)
        _snap2 = (_rt.get("sources") or {}).get("plan_snapshot") or {}
        if not isinstance(_snap2, dict):
            _snap2 = {x[0]: x[1] for x in _snap2}
        check("★ 端到端：快照含固定追加的总理子代理",
              _snap2.get("总理子代理") == "prime", _snap2)
        check("★ 端到端：快照里传动房是 transmission（不是 drivetrain）",
              _snap2.get("传动与轮组") == "transmission", _snap2)
        _rows = {x["room"]: x for x in (_rt.get("by_room") or [])}
        check("★ 端到端：传动房 require_physics=True（不再被豁免）",
              _rows.get("传动与轮组", {}).get("require_physics") is True,
              _rows.get("传动与轮组"))
    finally:
        shutil.rmtree(_sd, ignore_errors=True)
        if _wd:
            shutil.rmtree(_wd, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("H. CLI 全路径（room-types 子命令可构建并真跑）")
    _bad = []
    for _s in ["room-types", "set-subagent-plan", "subagent-plan"]:
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
        prime_all_stages(d)
        rooms_file = os.path.join(d, "rooms.json")
        with io.open(rooms_file, "w", encoding="utf-8") as f:
            json.dump(ROOMS_LEGACY, f, ensure_ascii=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mf.main(["set-subagent-plan", "--rooms-json", "@" + rooms_file,
                            "--rationale", "CLI 回归", "--work-dir", d])
        check("★ CLI set-subagent-plan 返回 0", code == 0, buf.getvalue()[-300:])
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
