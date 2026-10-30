# -*- coding: utf-8 -*-
r"""回归测试：Bug② 十阶段产物【写在用户答复之前】且答复后不回写

════════════════════════════════════════════════════════════════════════════
现场缺陷（2026-10-19 循迹小车，见 DSH插件Bug记录.md「三、流程级 · Bug②」）
--------------------------------------------------------------------------
现象：mode2_plan.json 的十阶段 artifacts 保留过期值 ——
      早期快照「底盘 100×100 / 材料 PLA」；用户确认值「150×120 / PETG」。
根因：十阶段 artifacts 在 workflow_gate init 后由 AI 自行填，**在用户实际
      答复之前**就已写盘；之后 answer-question 只更新题目应答，从不回写
      十阶段产物 → 下游房间读到过期尺寸/材料，只能靠主代理在 prompt 里硬写。

本测试锁住的修复（全部为代码级强制，不是文档约定）
--------------------------------------------------
【A】登记依赖：record_question 之后，plan 里 stage_question_deps 必须记下
     「该阶段产物依赖第 N 题」。
【B】拒写侧：该阶段的题目还没作答时，mark_stage_done 必须拒绝（gate=
     STAGE_AWAITING_ANSWER）—— 这正是"产物写在答复前面"的根因。
【C】答复即过期：answer-question 写入答复后，该阶段必须在 stale_stages 里，
     并带原因「题目 X 已作答，产物需重算」。
【D】查询可见：stage_status 输出 stale 阶段（stale/stale_count/行内 stale
     标记与 stale_reason）。
【E】fail-closed：require_stages_complete 把 stale 阶段视为未完成
     （gate=ANALYSIS_STALE），set-subagent-plan / 开工被挡住。
【F】重做入口：refresh-stage 让阶段重新变为未完成；mark-stage-done 必须
     交回【新产物】，与过期前相同的产物被拒（防"写回旧文本"）。
【G】清 stale：只有真的重做成功（新产物）后才清除 stale，并留审计痕迹。
【H】CLI 全路径：refresh-stage / redo-stage 子命令可构建且能真正跑通。

运行：python test_regression_bug2_stage_stale.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 【测试隔离修复】必须隔离状态目录，否则会被【真实运行环境】干扰 ──────────
# 实测（2026-10-29，DSH 正在运行时）：本测试会读到
#   <state_dir>/platform_agents.json
# 那是【宿主实时写入】的子代理注册表（几秒前刚刷新），里面全是真实子代理 id；
# 而本测试用的是固定身份 "test-analyst"，于是 analyst_bind 判定
#   "你不在子代理注册表里" → set_subagent_plan 返回 MODE2_ANALYST_REQUIRED
#   → 一串断言连带失败。
# 这不是代码缺陷，是【测试被污染】：同目录的
#   test_regression_20261029_liveness.py 早就用 _isolate() 做了同样的事。
# 这里补齐同款隔离：把 DSH_STATE_DIR 指向临时目录。
_ISO_DIR = tempfile.mkdtemp(prefix="iso_")
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


# 【2026-10-28 重构】先绑定总计子代理身份（测试固定身份）
os.environ["DSH_SESSION_ID"] = "test-analyst"


def fresh():
    d = tempfile.mkdtemp(prefix="m2bug2_")
    try:
        mf.analyst_bind("test-analyst", d)
    except Exception:
        pass
    return d


def walk_to_stage(d, stop_idx):
    """按序 begin + mark done 到 stop_idx（含）；每阶段给一个"初始产物"。"""
    for i, sid in enumerate(mf.STAGE_IDS[:stop_idx + 1]):
        r = mf.begin_stage(sid, d)
        assert r["ok"], (sid, r)
        r = mf.mark_stage_done(sid, artifact="初始产物-%s" % sid, work_dir=d)
        assert r["ok"], (sid, r)


def main():
    # ══════════════════════════════════════════════════════════════════
    section("A. 登记依赖：题目→阶段的依赖必须落盘")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        r = mf.record_question("底盘尺寸多少", "TaskUnderstanding",
                               "决定机构布局", d)
        check("ask 登记成功", r["ok"], r.get("error"))
        plan = mf.load_plan(d)
        deps = plan.get("stage_question_deps") or {}
        check("★ stage_question_deps 记下该阶段依赖第 1 题",
              deps.get("TaskUnderstanding") == [1], deps)
        check("record_question 回传依赖台账", r.get("stage_question_deps"), r)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("B. 拒写侧：题目未作答时，该阶段产物不得定稿")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸多少", "TaskUnderstanding", "决定布局", d)
        r = mf.mark_stage_done("TaskUnderstanding", "TaskSpec:底盘100×100/PLA", d)
        check("★ 依赖未答题目 → mark-stage-done 被拒", not r["ok"], r)
        check("★ gate = STAGE_AWAITING_ANSWER", r.get("gate") == "STAGE_AWAITING_ANSWER", r)
        check("拒绝时点名未答题目", bool(r.get("unanswered")), r.get("unanswered"))
        st = mf.stage_status(d)
        row = [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ stage_status 标记 awaiting_answer=[1]", row["awaiting_answer"] == [1], row)
        check("★ 该阶段计入 blocking", row["blocking"] is True, row)
        check("★ stage_status.complete=False", st["complete"] is False, st)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("C. 答复即过期：answer-question 把该阶段标 stale（带原因）")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        # 完整走完十阶段（每阶段先答复其题目，才能定稿）
        for i, sid in enumerate(mf.STAGE_IDS):
            mf.begin_stage(sid, d)
            q = mf.record_question("题目-%s" % sid, sid, "该阶段需要该参数", d)
            assert q["ok"], q
            ans = mf.answer_question(q["question"]["seq"], "答复-%s" % sid, d)
            assert ans["ok"], ans
            mk = mf.mark_stage_done(sid, "产物-%s-v1" % sid, d)
            assert mk["ok"], (sid, mk)
        st = mf.stage_status(d)
        check("先决条件：十阶段全部完成", st["complete"] and st["done_count"] == 10, st)
        check("先决条件：此刻没有 stale", st["stale_count"] == 0, st.get("stale"))

        # 用户改答第 1 题（= 输入变了）→ TaskUnderstanding 必须过期
        r = mf.answer_question(1, "底盘改为 150×120，材料 PETG", d)
        check("answer-question 成功", r["ok"], r.get("error"))
        check("★ 答复回传 stale_marked", r.get("stale_marked") == ["TaskUnderstanding"], r)
        _reason = " ".join(str(x.get("reason")) for x in (r.get("stale_stages") or []))
        check("★ stale 原因含「题目 1 已作答，产物需重算」",
              ("题目 1" in _reason) and ("产物需重算" in _reason), _reason)
        plan = mf.load_plan(d)
        stale = plan.get("stale_stages") or []
        check("★ plan.stale_stages 落盘", len(stale) == 1, stale)
        check("★ stale 记录带 questions（题号+答复原文）",
              bool(stale and stale[0].get("questions")), stale)
        check("★ stale 记录保留旧产物（便于比对是否真重算）",
              bool(stale and stale[0].get("old_artifact")), stale)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("D. 查询可见：stage_status 能看到 stale 阶段")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(1, "150×120", d)
        # 此时该阶段从未完成 → 不该被判 stale（还没产物可过期）
        st = mf.stage_status(d)
        check("从未产出过的阶段：不标 stale（本来就待做）", st["stale_count"] == 0, st.get("stale"))
        check("但仍在 missing / blocking 里",
              "任务理解" in st["missing"] and st["complete"] is False, st)

        mf.mark_stage_done("TaskUnderstanding", "TaskSpec:150×120/PETG", d)
        r = mf.answer_question(1, "改成 180×140", d)
        check("改答后触发 stale", bool(r.get("stale_marked")), r)
        st = mf.stage_status(d)
        row = [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ stage_status.stale 列出该阶段", st["stale"] == ["任务理解"], st.get("stale"))
        check("★ stale_count = 1", st["stale_count"] == 1, st.get("stale_count"))
        check("★ 行内 stale=True 且带 stale_reason",
              row["stale"] is True and bool(row["stale_reason"]), row)
        check("★ 行内 stale_questions 含题号 1", row["stale_questions"] == [1], row)
        check("★ stale 阶段计入 blocking", row["blocking"] is True, row)
        check("★ stage_status.complete=False（stale 视为未完成）",
              st["complete"] is False, st)
        check("有 stale 时给出口径说明", bool(st.get("note")), st.get("note"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("E. fail-closed：require_stages_complete 把 stale 当未完成")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        for sid in mf.STAGE_IDS:
            mf.begin_stage(sid, d)
            q = mf.record_question("题目-%s" % sid, sid, "需要参数", d)
            mf.answer_question(q["question"]["seq"], "答复", d)
            mf.mark_stage_done(sid, "产物-%s-v1" % sid, d)
        gate = mf.require_stages_complete(d)
        check("先决条件：全部完成后放行", gate["ok"], gate)

        mf.answer_question(2, "改成新值（用户改答）", d)
        gate = mf.require_stages_complete(d)
        check("★ stale 后 require_stages_complete 拒绝", not gate["ok"], gate)
        check("★ gate = ANALYSIS_STALE", gate.get("gate") == "ANALYSIS_STALE", gate)
        check("★ 拒绝时列出 stale 阶段", bool(gate.get("stale")), gate.get("stale"))
        check("★ 给出 refresh-stage 可执行指引",
              any("refresh-stage" in str(h) for h in (gate.get("hint") or [])),
              gate.get("hint"))

        # 连带：set-subagent-plan 必须被挡住（否则带着过期产物就开工）
        rooms = [{"name": "结构件", "type": "structural", "parts": ["底盘"],
                  "why": "承载", "depends_on": []}]
        r = mf.set_subagent_plan(rooms, "按功能分解", "总计子代理", d)
        check("★ stale 状态下 set-subagent-plan 被拒", not r["ok"], r)
        check("  拒绝 gate 指向分析未就绪",
              r.get("gate") in ("ANALYSIS_STALE", "ANALYSIS_INCOMPLETE"), r.get("gate"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("F. 重做入口：refresh-stage + 必须交回新产物")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(1, "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "TaskSpec:100×100/PLA", d)
        mf.answer_question(1, "150×120 / PETG", d)

        # 未做过的阶段不能 refresh（避免用 refresh 绕过正常推进）
        r = mf.refresh_stage("PhysicsReasoning", d)
        check("未做过的阶段 → refresh 被拒", not r["ok"], r)

        r = mf.refresh_stage("TaskUnderstanding", d, reason="用户改答底盘尺寸与材料")
        check("★ refresh-stage 成功", r["ok"], r.get("error"))
        check("★ refresh 回传 stale_before=True", r.get("stale_before") is True, r)
        check("★ refresh 回传旧产物（供比对）",
              r.get("old_artifact") == "TaskSpec:100×100/PLA", r.get("old_artifact"))
        check("★ refresh 回传需应用的题目", bool(r.get("questions_to_apply")), r)
        check("refresh 给出下一步命令", "mark-stage-done" in str(r.get("next")), r.get("next"))

        st = mf.stage_status(d)
        row = [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ refresh 后阶段重新变为未完成", row["done"] is False, row)
        check("★ refresh 后 stale 仍保留（清除必须靠真重做）",
              row["stale"] is True, row)

        # F1: 不交产物 → 拒绝
        r = mf.mark_stage_done("TaskUnderstanding", "", d)
        check("★ 重做不带产物 → 拒绝", not r["ok"], r)
        check("  gate = STAGE_REDO_ARTIFACT_REQUIRED",
              r.get("gate") == "STAGE_REDO_ARTIFACT_REQUIRED", r.get("gate"))

        # F2: 交回与旧产物完全相同的文本 → 拒绝（防"写回旧文本就清掉 stale"）
        r = mf.mark_stage_done("TaskUnderstanding", "TaskSpec:100×100/PLA", d)
        check("★ 重做但产物与过期前相同 → 拒绝", not r["ok"], r)
        check("  gate = STAGE_REDO_NOT_REFRESHED",
              r.get("gate") == "STAGE_REDO_NOT_REFRESHED", r.get("gate"))

        # F3: 交新产物 → 通过，且 stale 被清除
        r = mf.mark_stage_done("TaskUnderstanding", "TaskSpec:150×120/PETG", d)
        check("★ 交回新产物 → mark-stage-done 通过", r["ok"], r)
        check("★ 回传 stale_cleared=True", r.get("stale_cleared") is True, r)
        plan = mf.load_plan(d)
        check("★ stale_stages 已清空", not (plan.get("stale_stages") or []),
              plan.get("stale_stages"))
        hist = plan.get("stale_history") or []
        check("★ 清除留审计痕迹（stale_history）",
              len(hist) == 1 and hist[0].get("cleared_by") == "mark_stage_done", hist)
        check("★ 审计痕迹保留旧产物与新产物",
              hist[0].get("old_artifact") == "TaskSpec:100×100/PLA"
              and hist[0].get("cleared_artifact") == "TaskSpec:150×120/PETG", hist)
        st = mf.stage_status(d)
        row = [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ 重做完成后阶段重新 done，且产物是新的",
              row["done"] is True and row["artifact"] == "TaskSpec:150×120/PETG", row)
        check("★ 重做完成后不再 stale", row["stale"] is False, row)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("G. refresh-stage 一步到位（--artifact 同时提交新产物）")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(1, "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "old-artifact", d)
        mf.answer_question(1, "150×120", d)
        r = mf.refresh_stage("TaskUnderstanding", d, reason="改答", artifact="new-artifact")
        check("★ refresh --artifact 一步完成", r["ok"], r)
        plan = mf.load_plan(d)
        check("★ stale 已清除", not (plan.get("stale_stages") or []), plan.get("stale_stages"))
        row = [x for x in mf.stage_status(d)["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ 产物已更新为新产物", row["artifact"] == "new-artifact", row)
        # 一步到位也不能拿旧产物蒙混
        mf.answer_question(1, "180×140", d)
        r = mf.refresh_stage("TaskUnderstanding", d, artifact="new-artifact")
        check("★ 一步到位但产物与过期前相同 → 拒绝", not r["ok"], r)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("H. 旧计划文件兼容（无 stale_stages / 无 deps 字段也不崩）")
    d = fresh()
    try:
        mf.set_depth("lite", d)
        p = mf.plan_path(d)
        old = {"schema": "dsh-engineering/mode2-plan@1", "param_depth": "lite",
               "stages": [{"stage": "TaskUnderstanding", "status": "done",
                           "artifact": "旧产物"}],
               "stage_index": 0, "questions": [{"seq": 1, "stage": "TaskUnderstanding",
                                                "topic": "t", "answered": False}]}
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False)
        st = mf.stage_status(d)
        check("★ 旧文件缺字段不崩，且能读出 done", st["ok"] and st["done_count"] == 1, st)
        check("★ 旧文件：未答题目被识别为 awaiting",
              [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
              ["awaiting_answer"] == [1], st["rows"][0])
        r = mf.answer_question(1, "答复", d)
        check("★ 旧文件上 answer-question 仍能把阶段标 stale",
              r["ok"] and r.get("stale_marked") == ["TaskUnderstanding"], r)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("I. CLI 全路径（子命令可构建 + 真跑一条会写盘的命令）")
    _subs = ["refresh-stage", "redo-stage", "stage-status", "answer-question"]
    _bad = []
    for _s in _subs:
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                mf.main([_s, "--help"])
        except SystemExit as _e:
            if _e.code not in (0, None):
                _bad.append((_s, _e.code))
        except Exception as _e:
            _bad.append((_s, repr(_e)))
    check("★ 新子命令 argparse 全部可构建（无 NameError）", not _bad, _bad)

    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(1, "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "cli-old", d)
        mf.answer_question(1, "150×120", d)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mf.main(["refresh-stage", "TaskUnderstanding",
                            "--artifact", "cli-new", "--work-dir", d])
        out = buf.getvalue()
        check("★ CLI refresh-stage --artifact 返回 0", code == 0, out[-300:])
        try:
            j = json.loads(out)
        except Exception:
            j = {}
        check("★ CLI 输出合法 JSON 且 ok=True", j.get("ok") is True, out[-300:])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 依赖注入：cli 的 redo-stage 别名必须真的走 refresh（不能只是注册了字符串）
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(1, "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "alias-old", d)
        mf.answer_question(1, "150×120", d)
        with contextlib.redirect_stdout(io.StringIO()):
            code = mf.main(["redo-stage", "TaskUnderstanding",
                            "--artifact", "alias-new", "--work-dir", d])
        row = [x for x in mf.stage_status(d)["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ CLI redo-stage 别名真的执行重做", code == 0 and row["artifact"] == "alias-new", row)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("J. 存量数据：产物落盘【之后】才收到答复 → 只读推断为 stale")
    # Bug② 的现场就是这种存量数据：十阶段产物 20:30~20:33 写盘，
    # 用户答复 20:55~20:57 才到 —— 计划文件里躺的全是过期文本。
    # 若只认"向前生效"的落盘标记，这些历史计划会被判成"已完成"。
    d = fresh()
    try:
        mf.set_depth("lite", d)
        # 构造：先写产物，再把答复时间改到之后（模拟历史现场）
        mf.begin_stage("TaskUnderstanding", d)
        q = mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(q["question"]["seq"], "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "TaskSpec:100×100/PLA", d)
        plan = mf.load_plan(d)
        # 把 artifact 的 done_at 调到答复之前
        for s in plan["stages"]:
            if s.get("stage") == "TaskUnderstanding":
                s["done_at"] = "2026-10-19 20:30:23"
        for qq in plan["questions"]:
            qq["answered_at"] = "2026-10-19 20:55:43"
        plan["stale_stages"] = []       # 存量文件没有落盘标记
        mf.save_plan(plan, d)

        st = mf.stage_status(d)
        row = [x for x in st["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("★ 存量数据：答复晚于产物 → 只读推断 stale",
              row["stale"] is True, row)
        check("★ 推断原因点名题号",
              "题目 1" in str(row["stale_reason"]), row["stale_reason"])
        check("★ 推断出 stale 后 complete=False", st["complete"] is False, st)
        gate = mf.require_stages_complete(d)
        check("★ 存量过期数据同样挡住 require_stages_complete",
              not gate["ok"] and gate.get("gate") == "ANALYSIS_STALE", gate)
        # 只读推断不得写脏落盘台账
        plan2 = mf.load_plan(d)
        check("★ 只读推断不写入 stale_stages（落盘台账仍为空）",
              not (plan2.get("stale_stages") or []), plan2.get("stale_stages"))

        # 时间戳缺失/非法时不得误判（fail-safe）
        plan3 = mf.load_plan(d)
        for s in plan3["stages"]:
            if s.get("stage") == "TaskUnderstanding":
                s.pop("done_at", None)
        mf.save_plan(plan3, d)
        st3 = mf.stage_status(d)
        row3 = [x for x in st3["rows"] if x["stage"] == "TaskUnderstanding"][0]
        check("时间戳缺失 → 不做推断（不误判 stale）", row3["stale"] is False, row3)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 交付前总闸必须把 stale 说清楚（不能输出"缺少产出："这种空信息）
    d = fresh()
    try:
        mf.set_depth("lite", d)
        mf.begin_stage("TaskUnderstanding", d)
        q = mf.record_question("底盘尺寸", "TaskUnderstanding", "决定布局", d)
        mf.answer_question(q["question"]["seq"], "100×100", d)
        mf.mark_stage_done("TaskUnderstanding", "v1", d)
        mf.answer_question(q["question"]["seq"], "150×120", d)
        r = mf.pre_delivery_check(d)
        _b = " | ".join(r.get("blockers") or [])
        check("★ 总闸 blocker 点名「产物过期」", "产物过期" in _b, _b[:400])
        check("★ 总闸 blocker 给出 refresh-stage 指引", "refresh-stage" in _b, _b[:400])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    section("K. 端到端：stale 阶段真的会挡住 workflow_gate.select 开工")
    # 单模块测试只证明"函数被拒"，证明不了"流程真的停下"。这里真跑两个 CLI：
    #   mode2_flow 标 stale → workflow_gate.select 必须拒绝（MODE2_STAGES_STALE）
    #   → refresh-stage 重做 → select 才放行。
    # 状态目录用 DSH_STATE_DIR 隔离，不碰本机真实任务状态。
    import subprocess
    _sd = tempfile.mkdtemp(prefix="bug2e2e_state_")
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
            _wf = json.load(f) or {}
        _wd = _wf.get("work_dir")
        check("端到端：拿到本任务 work_dir", bool(_wd), _wd)

        _run(_M2, "analyst-bind", "--work-dir", _wd)
        _run(_M2, "set-depth", "full", "--work-dir", _wd)
        for _sid in mf.STAGE_IDS:
            _run(_M2, "begin-stage", _sid, "--work-dir", _wd)
            _q = _run(_M2, "ask", "题目-" + _sid, "--stage", _sid,
                      "--reason", "该阶段需要该参数", "--work-dir", _wd)
            _seq = (_q.get("question") or {}).get("seq")
            _run(_M2, "answer-question", str(_seq), "答复-" + _sid, "--work-dir", _wd)
            _run(_M2, "mark-stage-done", _sid, "--artifact", "产物-" + _sid + "-v1",
                 "--work-dir", _wd)
        _st = _run(_M2, "stage-status", "--work-dir", _wd)
        check("端到端：十阶段完成", _st.get("done_count") == 10, _st.get("done_count"))

        _rooms = [
            {"name": "臂体结构", "type": "structural", "parts": ["臂管"],
             "why": "承载", "depends_on": []},
            {"name": "关节传动", "type": "transmission", "parts": ["减速器"],
             "why": "传动", "depends_on": ["臂体结构"]},
            {"name": "总装验证", "type": "assembly", "parts": ["总装"],
             "why": "装配", "depends_on": []},
            {"name": "工程图输出", "type": "drafting", "parts": ["图纸"],
             "why": "出图", "depends_on": []},
        ]
        _rj = os.path.join(_sd, "rooms.json")
        with io.open(_rj, "w", encoding="utf-8") as f:
            json.dump(_rooms, f, ensure_ascii=False)
        _sp = _run(_M2, "set-subagent-plan", "--rooms-json", "@" + _rj,
                   "--rationale", "按功能分解", "--decided-by", "总计子代理",
                   "--work-dir", _wd)
        check("端到端：编制登记成功", _sp.get("ok") is True, _sp.get("error"))

        # ── 用户改答 → 阶段过期 → select 必须拒绝 ──
        _r = _run(_M2, "answer-question", "2", "改答：150×120 / PETG",
                  "--work-dir", _wd)
        check("★ 端到端：改答触发 stale", bool(_r.get("stale_marked")), _r)
        _sel = _run(_WG, "select", "B,D")
        check("★ 端到端：select 被拒绝（不拿过期产物开工）",
              _sel.get("ok") is not True, _sel.get("ok"))
        check("★ 端到端：gate = MODE2_STAGES_STALE",
              _sel.get("gate") == "MODE2_STAGES_STALE", _sel.get("gate"))
        check("★ 端到端：拒绝时点名过期阶段",
              "功能分解" in str(_sel.get("error")), _sel.get("error"))
        check("★ 端到端：拒绝时给出 refresh-stage 指引",
              any("refresh-stage" in str(h) for h in (_sel.get("how") or [])),
              _sel.get("how"))

        # ── 重做 → select 放行 ──
        _rr = _run(_M2, "refresh-stage", "FunctionalDecomposition",
                   "--reason", "用户改答后重算", "--artifact", "FunctionTree-v2",
                   "--work-dir", _wd)
        check("★ 端到端：refresh-stage 一步重做成功", _rr.get("ok") is True, _rr.get("error"))
        _sel2 = _run(_WG, "select", "B,D")
        check("★ 端到端：重做后 select 放行", _sel2.get("ok") is True,
              {"gate": _sel2.get("gate"), "error": _sel2.get("error")})
        check("★ 端到端：放行时带出总理子代理",
              "总理子代理" in str((_sel2.get("subagent_config") or {}).get("rooms")),
              (_sel2.get("subagent_config") or {}).get("rooms"))
    finally:
        shutil.rmtree(_sd, ignore_errors=True)
        if _wd:
            shutil.rmtree(_wd, ignore_errors=True)

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
