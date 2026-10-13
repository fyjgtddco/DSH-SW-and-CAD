# -*- coding: utf-8 -*-
"""回归测试：模式二新流程的【代码级强制约束】

★ 本测试的核心目的：证明这些约束是【真的会拦住】的，不是文档约定。
   因此每条都同时验证「合法输入通过」与「违规输入被拒」两个方向。

覆盖：
  · 提问数量硬约束：强 [10,50]、弱 [5,10]，下界闭区间（可以等于）
  · 按需提问：必须先分析到该阶段、必须写理由、题数不能超限
  · 十阶段：必须按序，跳阶段被拒；只有 DesignIteration 能回退
  · 子代理编制：只能由"总计子代理"决定；必须写 parts/why；依赖不能悬空
  · 总理子代理：上报必填 名字/尺寸/文件/三道防线凭据/相邻件，缺一即拒
  · 交付前总闸：任一不满足即禁止交付

运行：python test_regression_mode2_flow.py
"""
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
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def fresh():
    """每个用例独立工作目录，避免互相污染。"""
    d = tempfile.mkdtemp(prefix="mode2test_")
    return d


def main():
    # ═══════════════════════════════════════════════════════════════
    section("1. 提问数量硬约束（用户口径：强 10~50 / 弱 5~10，下界可等于）")
    # ── 边界值：恰好取到上下界必须【合法】 ──
    check("强需求 恰好 10 题 → 合法（可以等于下界）",
          mf.validate_question_budget("full", 10)["ok"])
    check("强需求 恰好 50 题 → 合法（可以等于上界）",
          mf.validate_question_budget("full", 50)["ok"])
    check("弱需求 恰好 5 题 → 合法",
          mf.validate_question_budget("lite", 5)["ok"])
    check("弱需求 恰好 10 题 → 合法",
          mf.validate_question_budget("lite", 10)["ok"])
    # ── 越界必须【被拒】 ──
    r = mf.validate_question_budget("full", 51)
    check("强需求 51 题 → 拒绝", not r["ok"], r.get("error"))
    r = mf.validate_question_budget("lite", 11)
    check("弱需求 11 题 → 拒绝", not r["ok"], r.get("error"))
    r = mf.validate_question_budget("full", 9, final=True)
    check("强需求 收尾时只有 9 题 → 拒绝（未达下界）", not r["ok"], r.get("error"))
    r = mf.validate_question_budget("lite", 4, final=True)
    check("弱需求 收尾时只有 4 题 → 拒绝", not r["ok"], r.get("error"))
    # ── 过程校验 vs 收尾校验的差别 ──
    check("过程中只有 3 题 → 允许（还没分析完，不能误杀）",
          mf.validate_question_budget("full", 3, final=False)["ok"])
    check("收尾时 3 题 → 拒绝",
          not mf.validate_question_budget("full", 3, final=True)["ok"])
    # ── 非法深度必须 fail-closed ──
    check("未知深度 → 拒绝", not mf.validate_question_budget("bogus", 20)["ok"])
    check("深度为空 → 拒绝", not mf.validate_question_budget(None, 20)["ok"])

    # ═══════════════════════════════════════════════════════════════
    section("2. 按需提问（取消一股脑全问）")
    d = fresh()
    try:
        # 未定深度就提问 → 拒
        r = mf.record_question("载荷多大", "PhysicsReasoning", "算应力需要", d)
        check("未 set_depth 就提问 → 拒绝", not r["ok"], r.get("error"))

        check("set_depth(full) → 通过", mf.set_depth("full", d)["ok"])
        check("非法深度被拒", not mf.set_depth("x", d)["ok"])

        # ★ 核心：还没分析到该阶段就提问 → 必须拒（这就是"取消一股脑全问"）
        r = mf.record_question("载荷多大", "PhysicsReasoning", "算应力需要", d)
        check("★ 阶段未 begin 就提问 → 拒绝（禁止开头全抛问题）",
              not r["ok"], r.get("error"))

        # 空理由 → 拒
        mf.begin_stage("TaskUnderstanding", d)
        r = mf.record_question("用途", "TaskUnderstanding", "   ", d)
        check("无理由提问 → 拒绝", not r["ok"], r.get("error"))
        r = mf.record_question("   ", "TaskUnderstanding", "有理由", d)
        check("空主题提问 → 拒绝", not r["ok"], r.get("error"))

        # 合法：挂到已开始的阶段 + 有理由
        r = mf.record_question("使用场合", "TaskUnderstanding", "决定材料与防护等级", d)
        check("★ 阶段已 begin + 有理由 → 通过", r["ok"], r.get("error"))

        # 非法阶段名 → 拒
        r = mf.record_question("某问题", "NoSuchStage", "理由", d)
        check("非法阶段名 → 拒绝", not r["ok"], r.get("error"))

        # ★ 题数上界：连问到超过 50 必须被拦
        for i in range(60):
            mf.begin_stage("TaskUnderstanding", d)
            r = mf.record_question("批量问题%d" % i, "TaskUnderstanding", "压力测试", d)
            if not r["ok"]:
                break
        qr = mf.question_report(d)
        check("★ 提问达到上界后被拦（asked=%d ≤ 50）" % qr.get("asked"),
              qr.get("asked") == 50, "实际 %s" % qr.get("asked"))
        # 收尾校验：50 题 ≥ 下界 10 → 通过
        check("50 题收尾校验 → 通过", mf.question_report(d, final=True)["ok"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("3. 十阶段必须按序（跳阶段被拒）")
    d = fresh()
    try:
        r = mf.begin_stage("MechanismSynthesis", d)   # 跳过前 4 个
        check("★ 直接跳到第 5 阶段 → 拒绝", not r["ok"], r.get("error"))
        check("第 1 阶段 → 通过", mf.begin_stage("TaskUnderstanding", d)["ok"])
        r = mf.begin_stage("PhysicsReasoning", d)
        check("跳到第 3 阶段（跳过功能分解）→ 拒绝", not r["ok"], r.get("error"))
        check("第 2 阶段 → 通过", mf.begin_stage("FunctionalDecomposition", d)["ok"])
        check("第 3 阶段 → 通过", mf.begin_stage("PhysicsReasoning", d)["ok"])
        # 未 begin 就 mark done → 拒
        r = mf.mark_stage_done("MechanismSynthesis", "x", d)
        check("未 begin 就标记完成 → 拒绝", not r["ok"], r.get("error"))
        # 回退：没有 DesignIteration 时不许回退
        r = mf.begin_stage("TaskUnderstanding", d)
        check("★ 未经 DesignIteration 就回退 → 拒绝", not r["ok"], r.get("error"))
        # 重复进入【当前】阶段是允许的
        check("重复进入当前阶段 → 通过", mf.begin_stage("PhysicsReasoning", d)["ok"])
        # mark done
        r = mf.mark_stage_done("TaskUnderstanding", "TaskSpec.json", d)
        check("mark_stage_done → 通过", r["ok"], r.get("error"))
        st = mf.stage_status(d)
        check("stage_status 反映 1/10 完成", st["done_count"] == 1, st)
        check("stage_status 列出缺失阶段", len(st["missing"]) == 9, st["missing"])
        check("stage_status complete=False", not st["complete"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("4. 子代理编制：只能由【总计子代理】决定")
    d = fresh()
    try:
        rooms = [{"name": "结构件", "type": "structural", "parts": ["大臂", "小臂"],
                  "why": "承载主载荷", "depends_on": []},
                 {"name": "传动机构", "type": "transmission", "parts": ["齿轮轴"],
                  "why": "传递扭矩", "depends_on": ["结构件"]}]
        # ★ 越权：别人决定 → 拒
        r = mf.set_subagent_plan(rooms, "按功能分解", "主对话", d)
        check("★ 非总计子代理决定编制 → 拒绝", not r["ok"], r.get("error"))
        r = mf.set_subagent_plan(rooms, "按功能分解", "总计子代理", d)
        check("★ 总计子代理决定编制 → 通过", r["ok"], r.get("error"))
        check("编制数量不再固定为 3（此处 2 也合法）", r["count"] == 2, r.get("count"))
        # 无理由 → 拒
        check("缺 rationale → 拒绝",
              not mf.set_subagent_plan(rooms, "", "总计子代理", d)["ok"])
        # 空 rooms → 拒
        check("空 rooms → 拒绝",
              not mf.set_subagent_plan([], "理由", "总计子代理", d)["ok"])
        # 缺 parts → 拒
        check("子代理缺 parts → 拒绝",
              not mf.set_subagent_plan(
                  [{"name": "A", "why": "w"}], "r", "总计子代理", d)["ok"])
        # 缺 why → 拒
        check("子代理缺 why → 拒绝",
              not mf.set_subagent_plan(
                  [{"name": "A", "parts": ["p"]}], "r", "总计子代理", d)["ok"])
        # 重名 → 拒
        check("房间重名 → 拒绝",
              not mf.set_subagent_plan(
                  [{"name": "A", "parts": ["p"], "why": "w"},
                   {"name": "A", "parts": ["q"], "why": "w"}], "r", "总计子代理", d)["ok"])
        # 悬空依赖 → 拒
        check("依赖不存在的房间 → 拒绝",
              not mf.set_subagent_plan(
                  [{"name": "A", "parts": ["p"], "why": "w", "depends_on": ["Zzz"]}],
                  "r", "总计子代理", d)["ok"])
        # 读取
        gp = mf.get_subagent_plan(d)
        check("get_subagent_plan 读回编制", gp["ok"] and gp["count"] == 2, gp)
        # 总理子代理定义固定存在
        pr = mf.prime_room_definition(d)
        check("总理子代理固定存在且 fixed=True",
              pr["name"] == "总理子代理" and pr["fixed"] is True, pr)
        check("总理子代理职责含一致性校验与 docx 汇总",
              any("一致" in d0 for d0 in pr["duties"]) and
              any("docx" in d0 for d0 in pr["duties"]), pr["duties"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("5. 总理子代理上报：必填字段缺一即拒")
    d = fresh()
    try:
        good = {"part_name": "大臂", "room": "结构件",
                "file": "C:/x/DSH_大臂.SLDPRT",
                "dims": {"length_mm": 300, "width_mm": 40, "height_mm": 40},
                "material": "6061-T6",
                "credentials": {"material": "C:/x/大臂.material.json",
                                "physics": "C:/x/reports/结构件.physics.json",
                                "domain": "C:/x/reports/结构件.domain.json"},
                "adjacent": ["关节座"]}
        r = mf.record_prime_report(good, d)
        check("完整上报 → 通过", r["ok"], r.get("error"))
        # 逐字段缺失都必须被拒
        for f in mf.REQUIRED_REPORT_FIELDS:
            bad = dict(good)
            bad.pop(f, None)
            rr = mf.record_prime_report(bad, d)
            check("缺 %s → 拒绝" % f, not rr["ok"], rr.get("error"))
        # 三道防线缺一条 → 拒
        for k in mf.REQUIRED_CREDENTIAL_KEYS:
            bad = dict(good)
            cred = dict(good["credentials"])
            cred.pop(k)
            bad["credentials"] = cred
            rr = mf.record_prime_report(bad, d)
            check("三道防线缺 %s → 拒绝" % k, not rr["ok"], rr.get("error"))
        # 空尺寸 → 拒
        bad = dict(good); bad["dims"] = {}
        check("空 dims → 拒绝", not mf.record_prime_report(bad, d)["ok"])
        # adjacent 非数组 → 拒
        bad = dict(good); bad["adjacent"] = "关节座"
        check("adjacent 非数组 → 拒绝", not mf.record_prime_report(bad, d)["ok"])
        # adjacent 空数组是合法的（确实不接触任何件）
        bad = dict(good); bad["adjacent"] = []; bad["part_name"] = "独立件"
        check("adjacent=[] → 允许（确实无接触件）",
              mf.record_prime_report(bad, d)["ok"])
        # 重复上报同一零件 → 覆盖，不重复累积
        for _ in range(3):
            mf.record_prime_report(good, d)
        prs = mf.prime_reports(d)
        check("同零件重复上报 → 覆盖（不累积）", prs["count"] == 2, prs["count"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("6. 交付前总闸：任一不满足即禁止交付")
    d = fresh()
    try:
        r = mf.pre_delivery_check(d)
        check("★ 空白状态 → 总闸拒绝交付", not r["ok"], r)
        check("总闸列出多个 blocker", len(r["blockers"]) >= 3, r["blockers"])
        joined = " | ".join(r["blockers"])
        for kw in ("十阶段", "子代理编制", "总理子代理", "一致性"):
            check("blocker 覆盖「%s」" % kw, kw in joined, joined)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("7. 一致性校验：无 part_registry 时 fail-closed（不假 PASS）")
    d = fresh()
    try:
        r = mf.check_prime_consistency(d)
        # part_registry 若尚未就绪，必须 ok=False 且 evaluated=False（绝不静默通过）
        check("★ 无法评估时 ok=False（不假 PASS）", not r["ok"], r)
        check("★ 无法评估时 evaluated=False", r.get("evaluated") is False, r)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("8. 原子写与读取健壮性")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        p = mf.plan_path(d)
        check("计划文件已落盘", os.path.exists(p), p)
        with io.open(p, encoding="utf-8") as f:
            data = json.load(f)
        check("落盘 schema 正确",
              data.get("schema") == "dsh-engineering/mode2-plan@1", data.get("schema"))
        # 损坏文件不得静默变成"空计划"（那会让"未做"看起来像"没要求"）
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        lp = mf.load_plan(d)
        check("★ 损坏的计划文件 → 显式标记 _load_error", bool(lp.get("_load_error")), lp)
        r = mf.set_depth("full", d)
        check("★ 读坏后写操作 fail-closed", not r["ok"], r)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("9. ★ CLI 全路径冒烟（教训：只测函数不测 CLI 会漏掉 NameError）")
    # 背景：单元测试曾全绿，但 CLI 的 argparse default 里留了一处
    #   SUBPAN_AUTHORITY 拼写错误（应为 SUBPLAN_AUTHORITY），
    #   函数测试完全覆盖不到 —— 一跑真命令就 NameError。
    #   因此这里把每个子命令都真正调一遍（用 --help 至少走完 argparse 构建）。
    _subs = ["set-depth", "begin-stage", "mark-stage-done", "stage-status", "ask",
             "questions", "set-subagent-plan", "subagent-plan", "prime-room",
             "prime-report", "prime-reports", "check-consistency", "pre-delivery", "plan"]
    _bad = []
    import contextlib
    for _s in _subs:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                mf.main([_s, "--help"])
        except SystemExit as _e:
            # argparse --help 正常会以 0 退出；非 0 说明子命令构建有问题
            if _e.code not in (0, None):
                _bad.append((_s, _e.code))
        except Exception as _e:
            _bad.append((_s, repr(_e)))
    check("★ 14 个子命令的 argparse 全部可构建（无 NameError）",
          not _bad, _bad)

    # 真正跑一条会写盘的 CLI（走 argparse default，能抓出 default 里的拼写错）
    _d9 = fresh()
    try:
        _code = mf.main(["set-subagent-plan",
                         "--rooms-json", json.dumps(
                             [{"name": "A", "parts": ["p"], "why": "w"}]),
                         "--rationale", "r",
                         "--work-dir", _d9])
        check("★ CLI set-subagent-plan 用默认 --decided-by 能成功", _code == 0,
              "exit=%s" % _code)
        _p = mf.load_plan(_d9)
        check("CLI 写入的编制 decided_by 正确",
              (_p.get("subagent_plan") or {}).get("decided_by") == "总计子代理",
              _p.get("subagent_plan"))
    finally:
        shutil.rmtree(_d9, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
