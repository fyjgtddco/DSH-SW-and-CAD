# -*- coding: utf-8 -*-
"""mode2_flow —— 模式二（大型复杂器械装配）新流程的【代码级强制契约】

═══════════════════════════════════════════════════════════════════════════
本模块存在的唯一理由
═══════════════════════════════════════════════════════════════════════════
用户明确要求：

    「所有改动都要写在代码里面，强制执行的」
    「不能是写在提示词里面的」

因此本模块把原先只存在于 SKILL.md / persona 自然语言里的流程约束，
全部升级为**可以被调用、会返回失败、能阻断流程**的代码契约：

  ① 提问数量硬约束 —— 强需求 [10,50]、弱需求 [5,10]；
     超界/未达下限一律拒绝，不再"问几题都行"。
  ② 按需提问（取消"一股脑全问"）—— 每道题必须挂在一个**已开始的
     分析阶段**上，并写明**为什么需要它**；不挂阶段 = 拒绝登记。
     这就是"AI 思考到那里，才问对应的问题"的机器判据。
  ③ 十阶段流水线 —— 阶段必须按序推进，跳阶段 = 拒绝。
  ④ 子代理数量由【总计子代理】决定 —— 不再固定 3 个；
     数量与依据必须落盘，且必须来自 `decided_by == 总计子代理`。
  ⑤ 总理子代理 —— 固定存在；建模小屋必须向其上报
     三道防线凭据 / 尺寸 / 名称 / 相邻配合零件，缺字段 = 拒绝。
  ⑥ 相邻零件尺寸一致性 —— 不一致时【必须】升级给父代理干涉。
  ⑦ 零件数据持久台账 —— 可反查（见 part_registry.py）。

设计原则：**fail-closed**。任何"信息不足"的情形都返回 ok=False 并说明原因，
绝不静默放行。这样上游（门禁/主对话）无法"假装做过了"。

═══════════════════════════════════════════════════════════════════════════
原子写入
═══════════════════════════════════════════════════════════════════════════
本文件会被多个小屋并发读取（各自上报），因此落盘一律走 `os.replace`
原子替换，避免读到半个 JSON —— 这与 defense_gate.py 的处理保持一致。
"""
from __future__ import annotations

try:
    import sys as _sys_utf8
    for _stream_name in ("stdout", "stderr"):
        _st = getattr(_sys_utf8, _stream_name, None)
        if _st is not None and hasattr(_st, "reconfigure"):
            _st.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#: 流程状态文件（与 part_registry.json 同目录，便于一并归档）
PLAN_FILE = "mode2_plan.json"


# ═══════════════════════════════════════════════════════════════════════════
# 一、十阶段设计流水线（来自用户给定的方法论文件）
# ═══════════════════════════════════════════════════════════════════════════
# 顺序即契约：`begin_stage` 只允许推进到【下一个】阶段，禁止跳跃。
# 每个阶段带 `needs`（该阶段要产出什么），用于校验阶段真的做完了。
STAGES = [
    ("TaskUnderstanding", "任务理解", "结构化 TaskSpec：目标/环境/载荷/约束/未知量"),
    ("FunctionalDecomposition", "功能分解", "FunctionTree：子功能 + 输入输出 + 依赖"),
    ("PhysicsReasoning", "物理推导", "PhysicsModel：运动/力/转矩/能量/自由度 + 假定"),
    ("PrincipleSynthesis", "工作原理探索", "多个候选原理 + 淘汰理由"),
    ("MechanismSynthesis", "机构综合", "MechanismGraph：构件 + 连接 + 自由度"),
    ("RecursivePartReasoning", "递归零件需求推导", "PartRequirementGraph：每个零件为何存在"),
    ("PartSynthesis", "零件设计与选型", "标准件/自定义件 + 几何与接口定义"),
    ("EngineeringValidation", "工程验证", "PASS/FAIL/UNKNOWN + 证据"),
    ("DesignIteration", "自主迭代", "回退位置 + 版本 + 未解决问题"),
    ("ExplainableDesignOutput", "可解释输出", "完整设计报告 + 可追溯性"),
]

STAGE_IDS = [s[0] for s in STAGES]
STAGE_LABELS = {s[0]: s[1] for s in STAGES}
STAGE_NEEDS = {s[0]: s[2] for s in STAGES}


# ═══════════════════════════════════════════════════════════════════════════
# 一·补充、【Bug② 修复】阶段产物新鲜度（stale）基础判据
# ═══════════════════════════════════════════════════════════════════════════
# 缺陷回顾（2026-10-19 循迹小车实测）：
#   十阶段 artifacts 在【用户答复之前】就落盘了，答复之后从不回写 ——
#   mode2_plan.json 里仍是「底盘 100×100 / PLA」，用户确认的是
#   「150×120 / PETG」。下游房间读到的全是过期值，只能靠主代理在 prompt 里
#   手工硬写才纠正。
#
# 本段的判据（全部落盘，不靠模型自觉）：
#   · 每个阶段的"最新一条记录"决定它到底完成没有 —— 重做会用新记录覆盖旧状态；
#   · 阶段产物若依赖【未答题目】，就在 stage_status 里显式暴露；
#   · 题目一旦被作答，该题目挂载的阶段产物立刻标记 stale（带原因）；
#   · stale 阶段一律视为【未完成】（fail-closed），必须重做后才能继续；
#   · 重做必须给出【新的 artifact】，与过期前完全相同的产物被拒绝（防走过场）。
def _stage_records(plan):
    """按阶段汇总 stage 记录（保持落盘顺序）。"""
    out = {}
    for s in (plan or {}).get("stages", []) or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("stage")
        if sid not in STAGE_LABELS:
            continue
        out.setdefault(sid, []).append(s)
    return out


def _stage_latest(plan):
    """每个阶段的【最新一条】记录。

    为什么要"最新一条"而不是"任意一条 done"：
      重做一个早期阶段时，旧记录仍是 done；若只看"存在 done"，重做永远
      无法让该阶段回到未完成，stale 也就永远清不掉。取最新记录才反映真实状态。
    """
    return {sid: recs[-1] for sid, recs in _stage_records(plan).items() if recs}


def _stage_done_map(plan):
    """阶段 → 已登记的 artifact（仅"最新记录为 done"的阶段）。"""
    out = {}
    for sid, rec in _stage_latest(plan).items():
        if rec.get("status") == "done":
            out[sid] = rec.get("artifact") or ""
    return out


def _stage_question_seqs(plan):
    """阶段 → 挂在它上面的题号（台账 + 依赖登记两处合并，兼容旧计划文件）。"""
    out = {}
    for st, seqs in ((plan or {}).get("stage_question_deps") or {}).items():
        for s in (seqs or []):
            try:
                out.setdefault(st, []).append(int(s))
            except Exception:
                continue
    for q in ((plan or {}).get("questions") or []):
        st = q.get("stage")
        if st not in STAGE_LABELS:
            continue
        try:
            seq = int(q.get("seq"))
        except Exception:
            continue
        if seq not in out.setdefault(st, []):
            out[st].append(seq)
    for st in out:
        out[st] = sorted(set(out[st]))
    return out


def _unanswered_by_stage(plan):
    """阶段 → 该阶段上【尚未作答】的题目列表（"产物依赖未答题目"的判据）。"""
    qmap = {q.get("seq"): q for q in ((plan or {}).get("questions") or [])}
    out = {}
    for st, seqs in _stage_question_seqs(plan).items():
        pend = [qmap[s] for s in seqs if s in qmap and not qmap[s].get("answered")]
        if pend:
            out[st] = pend
    return out


def _stale_map(plan):
    """阶段 → stale 记录（同一阶段只保留一条，后写覆盖前写）。"""
    out = {}
    for e in ((plan or {}).get("stale_stages") or []):
        if isinstance(e, dict) and e.get("stage") in STAGE_LABELS:
            out[e["stage"]] = e
    return out


def _mark_stage_stale(plan, stage, questions, old_artifact, reason):
    """把某阶段标记为【产物过期】。同阶段多次标记 → 合并题号、刷新原因。"""
    stale = [e for e in (plan.get("stale_stages") or [])
             if isinstance(e, dict) and e.get("stage") != stage]
    prev = _stale_map(plan).get(stage) or {}
    qs = []
    for q in (prev.get("questions") or []) + list(questions or []):
        if not isinstance(q, dict):
            continue
        if all(int(x.get("seq", -1)) != int(q.get("seq", -2)) for x in qs):
            qs.append(q)
    entry = {
        "stage": stage,
        "label": STAGE_LABELS[stage],
        "reason": reason,
        "questions": qs,
        "old_artifact": old_artifact,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "since": prev.get("since") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "redo_started_at": prev.get("redo_started_at"),
    }
    stale.append(entry)
    plan["stale_stages"] = stale
    return entry


def _clear_stale(plan, stage, cleared_by, artifact=None):
    """清除某阶段的 stale，并留审计痕迹（stale_history 只增不改）。"""
    keep = []
    hit = None
    for e in (plan.get("stale_stages") or []):
        if isinstance(e, dict) and e.get("stage") == stage:
            hit = e
            continue
        keep.append(e)
    plan["stale_stages"] = keep
    if hit is not None:
        hist = dict(hit)
        hist["cleared_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        hist["cleared_by"] = cleared_by
        hist["cleared_artifact"] = artifact
        plan.setdefault("stale_history", []).append(hist)
    return hit


def _parse_ts(s):
    """把 'YYYY-mm-dd HH:MM:SS' 解析成 epoch；解析不了返回 None（fail-safe 不误判）。"""
    try:
        return time.mktime(time.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def _stale_implied(plan):
    """【Bug② 修复 · 存量数据判定】从时间戳推断"产物过期"（只读，不落盘）。

    为什么还要这一层：
      answer_question 的 stale 标记是【向前生效】的 —— 它只能拦住"之后"的改答。
      而 bug 记录里的现场是【已经发生】的：十阶段产物 20:30~20:33 落盘，
      用户答复 20:55~20:57 才到，mode2_plan.json 里躺的就是过期文本。
      若只认落盘标记，这些历史/存量计划文件依旧会被判"完成"。

    判据（纯数据，不靠模型自述）：
      阶段最新一条 done 记录的 done_at  <  该阶段上某道题的 answered_at
      → 产物写完【之后】用户才给出答案 → 产物必然没吸收这个答案 → 过期。

    只做只读推断；真正清除仍必须走 refresh-stage + 新产物（见 mark_stage_done）。
    """
    out = {}
    done = _stage_done_map(plan)
    latest = _stage_latest(plan)
    q_by_seq = {q.get("seq"): q for q in (plan.get("questions") or [])}
    for st, seqs in _stage_question_seqs(plan).items():
        if st not in done:
            continue
        _art_at = _parse_ts((latest.get(st) or {}).get("done_at"))
        if _art_at is None:
            continue
        _late = []
        for s in seqs:
            q = q_by_seq.get(s)
            if not q or not q.get("answered"):
                continue
            _ans_at = _parse_ts(q.get("answered_at"))
            if _ans_at is not None and _ans_at > _art_at:
                _late.append({"seq": s, "topic": q.get("topic"),
                              "answered_at": q.get("answered_at")})
        if _late:
            out[st] = {
                "stage": st,
                "label": STAGE_LABELS[st],
                "artifact_at": (latest.get(st) or {}).get("done_at"),
                "questions": _late,
                "reason": ("题目 %s 在产物落盘【之后】才被作答，产物未吸收该答复"
                           % "、".join(str(q["seq"]) for q in _late)),
                "source": "timestamp_inference",
            }
    return out


def _redo_watch(plan):
    """重做监视表：refresh-stage 之后，必须交回【不同的新产物】才算重做完成。"""
    return plan.setdefault("stale_redo_watch", {})


def _redo_expectation(plan, stage):
    """取某阶段"必须重做"的期望（stale 记录或 refresh 后的监视项，任一存在即可）。"""
    st = _stale_map(plan).get(stage)
    if st:
        return st, "stale"
    w = (_redo_watch(plan) or {}).get(stage)
    if isinstance(w, dict):
        return w, "redo_watch"
    return None, None


# ═══════════════════════════════════════════════════════════════════════════
# 二、提问数量硬约束（用户口径）
# ═══════════════════════════════════════════════════════════════════════════
# 用户原话：
#   「强需求的18题限制取消，但是最少不能少于10但是可以等于10，不能超过50题，
#     弱需求取消5题，但不能少于5题或者可以等于5题，但不能超过10题。」
#
# 注意"可以等于"：下界是闭区间 —— 恰好 10 题（强）/ 恰好 5 题（弱）都是合法的。
QUESTION_LIMITS = {
    "full": {"min": 10, "max": 50, "label": "强需求"},
    "lite": {"min": 5, "max": 10, "label": "弱需求"},
}


def limits_for(depth):
    """取某深度的题数上下界；深度非法时返回 None（由调用方 fail-closed）。"""
    return QUESTION_LIMITS.get(str(depth or "").strip().lower())


def validate_question_budget(depth, asked_count, *, final=False):
    """检查"已问/将问题数"是否落在该深度的合法区间内。

    Args:
        depth: "full" / "lite"
        asked_count: 已登记的问题数量（整数）
        final: True 表示这是收尾校验 —— 此时【必须】达到下界；
               False 表示过程校验 —— 只禁止超过上界（还没问完是正常的）。

    Returns:
        {"ok": bool, "depth":..., "min":..., "max":..., "asked":...,
         "remaining":..., "error":...}

    为什么区分 final：
        过程里随时可能只问了 3 题（正常，还没分析到需要更多的地方），
        此时用下界去卡会误杀。只有"分析结束、准备交付"时才要求达到下界。
    """
    lim = limits_for(depth)
    if not lim:
        return {"ok": False, "error": "未知的参数需求深度: %r（合法值 full/lite）" % (depth,)}
    try:
        n = int(asked_count)
    except Exception:
        return {"ok": False, "error": "asked_count 不是整数: %r" % (asked_count,)}
    if n < 0:
        return {"ok": False, "error": "asked_count 不能为负: %d" % n}
    out = {"depth": depth, "min": lim["min"], "max": lim["max"],
           "asked": n, "label": lim["label"],
           "remaining_to_min": max(0, lim["min"] - n),
           "remaining_to_max": max(0, lim["max"] - n)}
    if n > lim["max"]:
        out.update({
            "ok": False,
            "error": ("【提问超限】%s 最多 %d 题，当前已问 %d 题（超出 %d 题）。"
                      "禁止继续提问 —— 请用已有信息推进，或把问题合并。"
                      % (lim["label"], lim["max"], n, n - lim["max"])),
        })
        return out
    if final and n < lim["min"]:
        out.update({
            "ok": False,
            "error": ("【提问不足】%s 至少需要 %d 题，当前只问了 %d 题（还差 %d 题）。"
                      "参数不足以支撑建模与校核，禁止进入下一阶段。"
                      % (lim["label"], lim["min"], n, lim["min"] - n)),
        })
        return out
    out["ok"] = True
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 三、流程状态读写（原子写）
# ═══════════════════════════════════════════════════════════════════════════
def _plan_dir(work_dir=None):
    """计划文件所在目录。

    优先级：显式 work_dir → workflow_state 里当前任务的 work_dir → tools/load_cases。

    为什么要加中间这一层：
      set-subagent-plan 不带 --work-dir 时曾直接落到 load_cases，
      而 workflow_gate.select / mode_gate.room-start 读的是【任务目录】，
      于是"登记成功"却"门禁看不见"—— 表现为编制 task_id 对不上、
      room-start 被 SUBAGENT_PLAN_STALE 误拦。
      现在默认对齐到当前任务目录，两边永远是同一份文件。
    """
    base = work_dir
    if not base:
        # 从 workflow_state 取当前任务目录 —— 必须与门禁【同一份】状态文件。
        # 直接问 workflow_gate 要（它知道真实的 STATE_DIR），
        # 不要自己拼路径：工程模式有"仓库 / 安装副本"两套 tools，
        # 自己拼会读到另一套里的旧任务，导致编制 task_id 永远对不上。
        # 注意：不能直接 `import workflow_gate` —— 工程模式有"仓库 /
        # 安装副本"两套 tools，sys.path 里可能先命中另一套，
        # 于是读到【另一个任务】的 work_dir，编制就永远对不上。
        # 这里改为按【本文件同目录】加载，保证与调用方是同一份状态。
        # ── 直接读状态文件，不 import 任何同族模块 ─────────────────────
        # 踩过两次的坑：sys.path 里同时存在「仓库 tools」与「安装副本 tools」，
        # import workflow_gate 可能命中另一套 → 读到另一个任务的 work_dir。
        # 状态目录的权威裁决者是 _store.state_dir()，直接用它最稳。
        try:
            _sd = None
            try:
                import _store as _st_mod
                _sd = _st_mod.state_dir()
            except Exception:
                _sd = None
            if not _sd:
                _sd = os.path.join(os.path.dirname(os.path.abspath(__file__)))
            _wf = os.path.join(_sd, "workflow_state.json")
            if os.path.exists(_wf):
                with open(_wf, "r", encoding="utf-8") as _f:
                    _stj = json.load(_f) or {}
                base = _stj.get("work_dir") or None
        except Exception:
            base = None
    if not base:
        try:
            import _root  # noqa: F401  （仅为触发根目录解析，失败则忽略）
        except Exception:
            pass
        base = os.path.join(BASE_DIR, "load_cases")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = BASE_DIR
    return base


def plan_path(work_dir=None):
    return os.path.join(_plan_dir(work_dir), PLAN_FILE)


def _atomic_write_json(path, data):
    """原子写：先写临时文件再 os.replace，避免并发读到半个 JSON。"""
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fresh_plan():
    return {
        "schema": "dsh-engineering/mode2-plan@1",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": None,
        "param_depth": None,
        "stages": [],          # 已开始的阶段（含 status）
        "stage_index": -1,     # 已推进到的阶段下标（-1 = 未开始）
        "questions": [],       # 【按需提问】台账，见 record_question
        # ══ 【Bug② 修复】十阶段产物与用户答复的依赖 / 过期（stale）台账 ══
        # 原缺陷：十阶段 artifacts 在用户答复【之前】就落盘，答复后从不回写，
        #   导致 mode2_plan.json 里仍是过期值（实测：底盘 100×100/PLA，
        #   而用户确认的是 150×120/PETG），后续房间只能靠主代理手工覆盖。
        # 现在：
        #   stage_question_deps —— 哪个阶段的产物依赖哪些题目（含未答标记）；
        #   stale_stages        —— 因用户答复而【产物过期】、必须重算的阶段；
        #   stale_history       —— 已清除的过期记录（审计用，只增不改）。
        "stage_question_deps": {},
        "stale_stages": [],
        "stale_history": [],
        # ── 【2026-10-28 重构】总计子代理的真实身份（analyst-bind 写入）──
        # 没有它 = 十阶段/编制都不合法（主对话不能再自己走完流程）。
        "analyst": None,
        "subagent_plan": None, # 由【总计子代理】写入
        # ══ 【Bug③ 修复】rooms 快照（与门禁同一口径）══
        # 原缺陷：set-subagent-plan 只改 subagent_plan，不刷新 mode2_plan 的
        #   rooms 快照，于是 mode_gate / defense_gate / workflow_gate 三处
        #   room_type 各说各话（drivetrain/sensor/drawing vs transmission/...）。
        "subagent_config": None,
        "prime_reports": [],   # 建模小屋 → 总理子代理 的上报
        "prime_consistency": None,
        "notes": [],
    }


def load_plan(work_dir=None):
    p = plan_path(work_dir)
    if not os.path.exists(p):
        return _fresh_plan()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        # 老文件缺字段时补齐，避免下游 KeyError
        base = _fresh_plan()
        for k, v in base.items():
            d.setdefault(k, v)
        return d
    except Exception:
        # 读坏时【不静默返回空计划】——空计划会让"未做"看起来像"没要求"。
        # 这里显式标记，交由调用方 fail-closed。
        d = _fresh_plan()
        d["_load_error"] = "计划文件读取失败: %s" % p
        return d


def _state_dir():
    """状态目录权威裁决者（_store.state_dir()），与门禁读同一份状态文件。

    ── 为什么不能按"脚本所在目录"拼路径 ────────────────────────────────
    工程模式在磁盘上有两份 tools（仓库副本 + ~/.dsh 安装副本）。按
    __file__ 拼 workflow_state.json 会拿到【自己这份】的状态 —— 而门禁
    / 守卫读的是权威目录。实测后果：plan 的 task_id 取自另一份，
    编制 task_id 与当前任务永远对不上 → select 报 MODE2_SUBAGENT_PLAN_STALE，
    Bug③ 的"rooms 快照同步"也就整条链路失效。
    """
    try:
        import _store as _st
        _d = _st.state_dir()
        if _d:
            return _d
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def current_task_id(work_dir=None):
    """当前任务 task_id（权威目录里的 workflow_state）。读不到返回 None。"""
    try:
        _p = os.path.join(_state_dir(), "workflow_state.json")
        if os.path.exists(_p):
            with open(_p, "r", encoding="utf-8") as _f:
                return (json.load(_f) or {}).get("task_id") or None
    except Exception:
        pass
    return None


def save_plan(plan, work_dir=None):
    plan = dict(plan or {})
    plan["schema"] = "dsh-engineering/mode2-plan@1"
    plan["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # task_id 一律以【权威目录】的 workflow_state 为准 ——
    # 否则编制与任务对不上，room-start 会被 SUBAGENT_PLAN_STALE 误拦。
    if not plan.get("task_id"):
        try:
            plan["task_id"] = current_task_id()
        except Exception:
            pass
    p = plan_path(work_dir)
    try:
        _atomic_write_json(p, plan)
        return {"ok": True, "path": p}
    except Exception as e:
        return {"ok": False, "error": "写入计划失败: %r" % (e,), "path": p}


# ═══════════════════════════════════════════════════════════════════════════
# 四、参数深度
# ═══════════════════════════════════════════════════════════════════════════
def set_depth(depth, work_dir=None):
    """登记参数需求强度（强需求 full / 弱需求 lite）。"""
    if not limits_for(depth):
        return {"ok": False, "error": "参数需求强度必须是 full（强需求）或 lite（弱需求），收到 %r" % (depth,)}
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    old = plan.get("param_depth")
    if old and old != depth and plan.get("questions"):
        # 已经按某个口径问过题了就【不允许】改口径 —— 否则 10~50 的约束会被绕过
        return {"ok": False,
                "error": ("已按【%s】口径登记过 %d 道题，禁止再改成【%s】"
                          "（改口径会绕过题数硬约束）。如需重来请先 reset。"
                          % (QUESTION_LIMITS[old]["label"], len(plan["questions"]),
                             QUESTION_LIMITS[depth]["label"]))}
    plan["param_depth"] = depth
    lim = limits_for(depth)
    save_plan(plan, work_dir)
    return {"ok": True, "depth": depth, "label": lim["label"],
            "min": lim["min"], "max": lim["max"],
            "path": plan_path(work_dir)}


# ═══════════════════════════════════════════════════════════════════════════
# 五、十阶段按序推进
# ═══════════════════════════════════════════════════════════════════════════
def begin_stage(stage, work_dir=None, note="", actor=None):
    """开始一个分析阶段。**必须按序**，跳阶段直接拒绝。

    ── 【2026-10-28 重构】只能由【总计子代理】调用 ────────────────────
    analyst-bind 之后，主对话（sessionId 不同）直接调会被
    ANALYST_ACTOR_REQUIRED 拒绝。

    这条约束是"按需提问"的前提：只有真的推进到某个阶段，
    才允许登记挂在那个阶段上的问题（见 record_question）。
    """
    _guard = _require_analyst_actor("begin-stage", actor, work_dir)
    if _guard:
        return _guard
    if stage not in STAGE_IDS:
        return {"ok": False,
                "error": "未知阶段 %r；合法阶段：%s" % (stage, " → ".join(STAGE_IDS))}
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}

    idx = STAGE_IDS.index(stage)
    cur = int(plan.get("stage_index", -1))
    # 允许"重复进入当前阶段"（迭代会回到同一阶段），但不允许跳跃
    if idx > cur + 1:
        _skipped = STAGE_IDS[cur + 1:idx]
        return {"ok": False,
                "error": ("【跳阶段】不允许从【%s】直接跳到【%s】—— 中间跳过了：%s。"
                          "十阶段必须按序推进（DesignIteration 可回退到任意更早阶段）。"
                          % (STAGE_LABELS[STAGE_IDS[cur]] if cur >= 0 else "（未开始）",
                             STAGE_LABELS[stage], "、".join(STAGE_LABELS[s] for s in _skipped))),
                "stage_index": cur, "expected": STAGE_IDS[cur + 1] if cur + 1 < len(STAGE_IDS) else None}
    # 回退：只允许 DesignIteration 发起
    if idx < cur:
        _done = {s.get("stage") for s in plan.get("stages", [])}
        if "DesignIteration" not in _done:
            return {"ok": False,
                    "error": ("【非法回退】不允许从【%s】退回【%s】—— "
                              "只有 DesignIteration（自主迭代）阶段可以发起回退。"
                              % (STAGE_LABELS[STAGE_IDS[cur]], STAGE_LABELS[stage]))}

    plan["stage_index"] = max(cur, idx)
    rec = {"stage": stage, "label": STAGE_LABELS[stage], "needs": STAGE_NEEDS[stage],
           "status": "in_progress", "note": note,
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time()}
    # 同一阶段重复进入 → 追加一条（保留迭代痕迹），不覆盖历史
    plan.setdefault("stages", []).append(rec)
    save_plan(plan, work_dir)
    nxt = STAGE_IDS[idx + 1] if idx + 1 < len(STAGE_IDS) else None
    return {"ok": True, "stage": stage, "label": STAGE_LABELS[stage],
            "needs": STAGE_NEEDS[stage], "stage_index": idx,
            "next_stage": nxt, "next_label": STAGE_LABELS.get(nxt),
            "progress": "%d/%d" % (idx + 1, len(STAGE_IDS))}


def mark_stage_done(stage, artifact="", work_dir=None, actor=None):
    """把某阶段标记为完成，并登记它的产出物（artifact）。

    【2026-10-28 重构】只能由【总计子代理】调用（见 begin_stage 注释）。

    为什么要登记 artifact：没有产出的"阶段完成"就是走过场。
    最终交付时会校验关键阶段是否真的留下了产出。

    ── 【Bug② 修复】重做 / stale 语义（代码强制）─────────────────────────
    ① 只认【最新一条】未完成记录为落点：重做一个早期阶段时，新记录会被
       mark 成 done，旧记录保持原样 —— stage_status 取最新记录，所以重做
       是"重新开始"而不是"补一条 done 骗过去"。
    ② 若该阶段是 stale（题目已作答、产物需重算），必须交回【新产物】：
       与过期前的 artifact 完全相同 → 拒绝（否则模型写回旧文本就清掉 stale）。
    ③ 成功后才清 stale（清不掉就说明重做没发生）。
    ④ 阶段产物若依赖【仍未作答】的题目，直接拒收 —— 这正是"产物写在用户
       答复之前"的根因，必须堵在写入侧。
    """
    _guard = _require_analyst_actor("mark-stage-done", actor, work_dir)
    if _guard:
        return _guard
    if stage not in STAGE_IDS:
        return {"ok": False,
                "error": "未知阶段 %r；合法阶段：%s" % (stage, " → ".join(STAGE_IDS))}
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    hit = None
    for s in reversed(plan.get("stages", [])):
        if s.get("stage") == stage and s.get("status") != "done":
            hit = s
            break
    if hit is None:
        return {"ok": False,
                "error": ("阶段【%s】尚未 begin_stage，无法标记完成。"
                          "必须先 begin_stage 再 mark_stage_done。"
                          % STAGE_LABELS.get(stage, stage)),
                "hint": "先 begin-stage %s（重做也要重新 begin-stage）再 mark-stage-done。" % stage}

    # ④ 依赖未答题目 → 拒收（fail-closed）
    _pend = _unanswered_by_stage(plan).get(stage) or []
    if _pend:
        return {
            "ok": False,
            "gate": "STAGE_AWAITING_ANSWER",
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "unanswered": [{"seq": q.get("seq"), "topic": q.get("topic")} for q in _pend],
            "error": ("阶段【%s】的产物依赖尚未作答的题目（%s）—— "
                      "禁止在用户答复之前把产物落成最终态。"
                      % (STAGE_LABELS[stage],
                         "、".join("第%s题" % q.get("seq") for q in _pend))),
            "hint": ("先让用户作答：mode2_flow.py answer-question <seq> \"<答复>\"，"
                     "然后重做该阶段（refresh-stage）再 mark-stage-done。"),
        }

    # ②③ 重做必须交新产物
    _exp, _src = _redo_expectation(plan, stage)
    if _exp:
        _old = (_exp or {}).get("old_artifact")
        _new = str(artifact or "").strip()
        if not _new:
            return {
                "ok": False, "gate": "STAGE_REDO_ARTIFACT_REQUIRED", "stage": stage,
                "error": ("阶段【%s】的产物已过期/已登记重做，mark-stage-done 必须带 "
                          "--artifact 提交【重算后的新产物】（空产物不算重做）。"
                          % STAGE_LABELS[stage]),
                "old_artifact": _old,
                "hint": ("python mode2_flow.py mark-stage-done %s "
                         "--artifact \"<重算后的产物>\"" % stage),
            }
        if _old and _new == str(_old).strip():
            return {
                "ok": False, "gate": "STAGE_REDO_NOT_REFRESHED", "stage": stage,
                "error": ("阶段【%s】的产物与过期前【完全相同】—— 这不是重做。"
                          "用户答复已改变输入，必须真的重算后再提交。"
                          % STAGE_LABELS[stage]),
                "old_artifact": _old,
                "reason": (_exp or {}).get("reason"),
            }

    hit["status"] = "done"
    hit["artifact"] = artifact
    hit["done_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    hit["artifact_ts"] = time.time()
    _cleared = None
    if _exp:
        hit["redone_at"] = hit["done_at"]
        hit["redone_from_artifact"] = (_exp or {}).get("old_artifact")
        _cleared = _clear_stale(plan, stage, cleared_by="mark_stage_done",
                                artifact=artifact)
        _redo_watch(plan).pop(stage, None)
    # 依赖台账同步刷新：新产物 >= 当前已答题目
    plan.setdefault("stage_question_deps", {})[stage] = _stage_question_seqs(plan).get(stage, [])
    save_plan(plan, work_dir)
    out = {"ok": True, "stage": stage, "artifact": artifact}
    if _cleared is not None:
        out["stale_cleared"] = True
        out["redone"] = True
        out["note"] = ("阶段【%s】已按用户答复重做，stale 已清除。" % STAGE_LABELS[stage])
    return out


def stage_status(work_dir=None):
    """返回十阶段的推进情况，含"哪些阶段有产出、哪些是 stale、哪些在等答复"。

    ── 【Bug② 修复】输出必须能一眼看到：
      · done / artifact：按【最新一条记录】判定（重做后旧 done 不算数）；
      · stale：产物因用户作答而过期，必须重做；
      · awaiting_answer：产物依赖未答题目（还没到可交付的状态）；
      · blocking：会真正挡住 require_stages_complete 的阶段标签。
    """
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    done = _stage_done_map(plan)
    stale = dict(_stale_map(plan))
    # 存量数据：answer 早于产物落盘的历史计划文件，也要在查询里暴露出来
    # （只读推断，不污染 stale_stages 落盘台账 —— 清除仍必须走真重做）
    _implied = _stale_implied(plan)
    for _sid, _rec in _implied.items():
        if _sid not in stale:
            stale[_sid] = _rec
    pending = _unanswered_by_stage(plan)
    rows = []
    for _id, _lab, _need in STAGES:
        _q = _stage_question_seqs(plan).get(_id, [])
        _st = stale.get(_id)
        _pend = pending.get(_id) or []
        rows.append({
            "stage": _id, "label": _lab, "needs": _need,
            "done": _id in done,
            "artifact": done.get(_id, ""),
            "stale": bool(_st),
            "stale_reason": (_st or {}).get("reason"),
            "stale_questions": [q.get("seq") for q in ((_st or {}).get("questions") or [])],
            "awaiting_answer": [q.get("seq") for q in _pend],
            "questions": _q,
            "blocking": (bool(_st) or bool(_pend) or _id not in done),
        })
    _missing = [r["label"] for r in rows if not r["done"]]
    _stale_labels = [r["label"] for r in rows if r["stale"]]
    _await_labels = [r["label"] for r in rows if r["awaiting_answer"]]
    _blocking = [r["label"] for r in rows if r["blocking"]]
    return {"ok": True, "stage_index": plan.get("stage_index", -1),
            "total": len(STAGES), "done_count": len(done),
            "rows": rows, "missing": _missing,
            "stale": _stale_labels, "stale_count": len(_stale_labels),
            "awaiting_answer": _await_labels,
            "blocking": _blocking,
            "complete": not _blocking,
            "counted_done": not _missing,
            "note": ("stale 阶段视为未完成（fail-closed）：用户答复改变了输入，"
                     "必须 refresh-stage 重做后才能继续。") if _stale_labels else None}


# ═══════════════════════════════════════════════════════════════════════════
# 六、★ 按需提问（取消"一股脑全问"）
# ═══════════════════════════════════════════════════════════════════════════
def record_question(topic, stage, reason, work_dir=None, actor=None):
    """登记一道"按需提问"。**这是"AI 思考到那里才问"的机器判据。**

    强制点（任一不满足即拒绝）：
      1. 必须先 set_depth（否则算不出题数上界）；
      2. 必须属于十阶段之一；
      3. 该阶段必须已 begin_stage（= 真的分析到那里了）；
      4. 必须写明 reason（= 为什么现在需要这个信息）；
      5. 题数不得超过该深度上界。

    第 3 条正是"取消一股脑全问"的落实：还没分析到那个阶段，
    就不允许问那个阶段才会产生的问题 —— 无法再"开头把 50 题全抛出去"。
    """
    _guard = _require_analyst_actor("ask", actor, work_dir)
    if _guard:
        return _guard
    topic = str(topic or "").strip()
    reason = str(reason or "").strip()
    if not topic:
        return {"ok": False, "error": "必须给出问题主题（topic）—— 空问题不登记。"}
    if not reason:
        return {"ok": False,
                "error": ("必须写明【为什么现在需要这个问题】（reason）。"
                          "拒绝「无理由提问」—— 这正是「一股脑全问」的特征。")}
    if stage not in STAGE_IDS:
        return {"ok": False,
                "error": ("问题必须挂在某个分析阶段上；%r 不是合法阶段。"
                          "合法阶段：%s" % (stage, " → ".join(STAGE_IDS)))}

    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    depth = plan.get("param_depth")
    if not depth:
        return {"ok": False,
                "error": "尚未确定参数需求强度（强需求/弱需求）—— 请先 set_depth 再提问。"}

    # ③ 该阶段必须已经开始
    _begun = {s.get("stage") for s in plan.get("stages", [])}
    if stage not in _begun:
        return {"ok": False,
                "error": ("【尚未分析到该阶段】问题挂在【%s】，但该阶段还没 begin_stage。"
                          "按需提问要求先分析、再提问；禁止在分析之前就把问题全抛出来。"
                          % STAGE_LABELS[stage]),
                "stage_index": plan.get("stage_index", -1),
                "hint": "先 begin_stage %s，分析到确实缺信息时再 record_question。" % stage}

    # ⑤ 题数上界（过程校验：只卡上界，不卡下界）
    lim = limits_for(depth)
    n_now = len(plan.get("questions", []))
    chk = validate_question_budget(depth, n_now + 1, final=False)
    if not chk["ok"]:
        return dict(chk, blocked_by="question_budget")

    q = {"seq": n_now + 1, "topic": topic, "stage": stage, "answered": False,
         "stage_label": STAGE_LABELS[stage], "reason": reason,
         "at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time()}
    plan.setdefault("questions", []).append(q)
    # ── 【Bug② 修复】登记「该阶段产物依赖这道题」───────────────────────
    # 这是"产物依赖未答题目"的落盘判据：answer_question 会据此把该阶段标 stale，
    # mark_stage_done 会据此拒收"用户答复前就定稿"的产物。
    deps = plan.setdefault("stage_question_deps", {})
    _seqs = [int(x) for x in (deps.get(stage) or [])]
    if q["seq"] not in _seqs:
        deps[stage] = sorted(_seqs + [q["seq"]])
    plan["stage_question_deps"] = deps
    save_plan(plan, work_dir)
    _answered_map = {qq.get("seq"): bool(qq.get("answered"))
                     for qq in plan.get("questions", [])}
    return {"ok": True, "question": q, "asked": len(plan["questions"]),
            "min": lim["min"], "max": lim["max"],
            "remaining_to_max": max(0, lim["max"] - len(plan["questions"])),
            "stage_question_deps": deps,
            "note": ("已登记按需提问 %d/%d（%s）。"
                     "该题答复后，阶段【%s】的产物会被标记为过期（stale），"
                     "必须重做。"
                     % (len(plan["questions"]), lim["max"], lim["label"],
                        STAGE_LABELS[stage])),
            "answered_map": _answered_map}


def answer_question(seq, answer, work_dir=None, actor=None):
    """记录某道已登记问题的【用户真实答复】。

    为什么必须有这一步：record_question 只登记"我要问什么"，
      模型完全可以登记完就当问过了（实测把 21 条拼成"汇总答复"直接
      调 provide_context）。这里强制把答复逐条落到对应题目上，
      未答的题在收尾校验时会让交付直接失败。
    """
    _guard = _require_analyst_actor("answer-question", actor, work_dir)
    if _guard:
        return _guard
    ans = str(answer or "").strip()
    if not ans:
        return {"ok": False, "error": "答复内容为空 —— 空答复不算问过。"}
    try:
        seq = int(seq)
    except Exception:
        return {"ok": False, "error": "seq 必须是整数题号。"}
    plan = load_plan(work_dir)
    qs = plan.get("questions") or []
    hit = None
    for q in qs:
        if q.get("seq") == seq:
            hit = q
            break
    if hit is None:
        return {"ok": False, "error": "没有第 %d 题（请先 record_question）。" % seq}
    _prev_answer = hit.get("answer")
    hit["answered"] = True
    hit["answer"] = ans
    hit["answered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if _prev_answer is not None and str(_prev_answer) != ans:
        hit["reanswered_count"] = int(hit.get("reanswered_count") or 0) + 1
    plan["questions"] = qs
    _stage = hit.get("stage")

    # ── 【Bug② 修复】答复落盘即把该阶段产物标为 stale（题目 X 已作答，产物需重算）
    _affected = []
    if _stage in STAGE_LABELS:
        _done_art = _stage_done_map(plan).get(_stage)
        _reason = ("题目 %d（%s）已作答，产物需重算：%s"
                   % (seq, hit.get("topic") or "", ans[:60]))
        deps = plan.setdefault("stage_question_deps", {})
        _seqs = [int(x) for x in (deps.get(_stage) or [])]
        if seq not in _seqs:
            deps[_stage] = sorted(_seqs + [seq])
        plan["stage_question_deps"] = deps
        # 只有"该阶段真的产出过东西"才需要过期：
        #   · 从没 done 过（只是 begin 了）→ 本来就待做，靠 awaiting_answer 拦截即可；
        #   · 已经 stale（refresh 后又改答）→ 保留 stale，不能因刷新而洗白。
        _had_done = any(s.get("status") == "done"
                        for s in (plan.get("stages") or [])
                        if isinstance(s, dict) and s.get("stage") == _stage)
        if _done_art is not None or _had_done or _stage in _stale_map(plan):
            _stale = _mark_stage_stale(
                plan, _stage,
                [{"seq": seq, "topic": hit.get("topic"), "answer": ans}],
                old_artifact=_done_art, reason=_reason)
            _affected.append({"stage": _stage, "label": STAGE_LABELS[_stage],
                              "reason": _stale.get("reason"),
                              "old_artifact": _done_art,
                              "redo_command": ("python mode2_flow.py refresh-stage %s"
                                               % _stage)})
    save_plan(plan, work_dir)
    _done = len([q for q in qs if q.get("answered")])
    out = {"ok": True, "seq": seq, "answered": _done, "total": len(qs),
           "note": "已记录第 %d 题答复（%d/%d）。" % (seq, _done, len(qs))}
    if _affected:
        out["stale_stages"] = _affected
        out["stale_marked"] = [a["stage"] for a in _affected]
        out["note"] += (" 阶段【%s】的产物已标为过期（stale），"
                        "必须 refresh-stage 重做后才能继续。"
                        % "、".join(a["label"] for a in _affected))
    return out


def import_answers(answers, work_dir=None, actor=None, overwrite=False):
    """【BUG-05 修复】批量登记用户答复，一次性写完，避免逐个 CLI 调用。

    要解决的实测缺陷（2026-10-30 四工位转台任务）：
    总计子代理收齐 11 题答案后，需要把它们逐条同步进 mode2_flow 的台账。
    但 answer-question 一次只能答一题 → 它要连续调 11 次 CLI；
    更糟的是它无法确认到底同步成功了没有，于是陷入
    反复 ask 同一批题 的循环，最终把上下文烧光（turn/end=max-tokens），
    编制没能登记，整个任务卡在 select 之前。

    用户的建议（原话）：提供批量登记问题/答案的入口，
    避免逐个 CLI 调用消耗上下文。

    Args:
        answers: [{seq: int, answer: str}, ...] 或 {seq: answer} 映射
        work_dir: 计划目录（默认按任务）
        actor: 调用者（默认取 DSH_SESSION_ID，必须是已绑定的总计子代理）
        overwrite: 已答过的题是否用新答复覆盖（默认 False，只补未答的）

    Returns:
        {"ok":bool, "imported":[seq...], "skipped":[seq...],
         "failed":[{seq, error}...], "summary":str}
    """
    _guard = _require_analyst_actor("import-answers", actor, work_dir)
    if _guard:
        return _guard

    # 规整输入：list[dict] 或 dict 都接受
    pairs = []
    if isinstance(answers, dict):
        for k, v in answers.items():
            pairs.append((k, v))
    elif isinstance(answers, list):
        for it in answers:
            if isinstance(it, dict):
                pairs.append((it.get("seq"), it.get("answer")))
    if not pairs:
        return {"ok": False,
                "error": "没有可导入的答复。传 [{seq,answer},...] 或 {seq: answer}。",
                "how": "python mode2_flow.py import-answers --json-file answers.json"}

    imported, skipped, failed = [], [], []
    for seq, ans in pairs:
        try:
            _seq = int(seq)
        except Exception:
            failed.append({"seq": seq, "error": "seq 不是整数"})
            continue
        if not str(ans or "").strip():
            failed.append({"seq": _seq, "error": "答复为空（空答复不算问过）"})
            continue
        plan = load_plan(work_dir)
        hit = next((q for q in (plan.get("questions") or [])
                    if q.get("seq") == _seq), None)
        if hit is None:
            failed.append({"seq": _seq, "error": "该题号未登记（先 record_question）"})
            continue
        if hit.get("answered") and not overwrite:
            skipped.append(_seq)          # 已答过，默认不覆盖
            continue
        # 复用单题逻辑，保证 stale 标记等副作用完全一致
        r = answer_question(_seq, ans, work_dir, actor)
        if r.get("ok"):
            imported.append(_seq)
        else:
            failed.append({"seq": _seq, "error": r.get("error") or "失败"})

    rep = question_report(work_dir)
    return {
        "ok": not failed,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "answered": rep.get("answered"),
        "unanswered": rep.get("unanswered"),
        "param_depth": rep.get("param_depth"),
        "depth_source": rep.get("depth_source"),
        "summary": ("导入 %d 条，跳过 %d 条（已答），失败 %d 条；"
                    "当前已答 %s / 未答 %s"
                    % (len(imported), len(skipped), len(failed),
                       rep.get("answered"), rep.get("unanswered"))),
        "note": ("一次性写入，避免逐个 answer-question 消耗上下文。"
                 "已答过的题默认跳过；确需改答传 --overwrite。"),
    }


def refresh_stage(stage, work_dir=None, reason="", artifact="", actor=None):
    """【Bug② 修复】显式重做某阶段：进入重做态，并要求交回【新产物】。

    为什么需要这个显式入口：
      stale 只是"标记"。若没有"开始重做"这个动作，模型可以继续说
      "我已经重做了"却什么都不写。这里把重做变成一次**状态变更**：
        ① 只接受已做过（或 stale）的阶段 —— 没做过的直接 begin-stage 即可；
        ② 立刻追加一条 in_progress 记录（最新记录不再是 done → 阶段重新变成未完成）；
        ③ 登记 stale_redo_watch：后续 mark-stage-done 必须带来【新产物】，
           与过期前完全相同的产物会被拒（防"写回旧文本就清掉 stale"）；
        ④ stale 记录**此时不清除** —— 只有真的重做成功后才清除。

    Args:
        stage: 阶段 id
        work_dir: 计划文件目录
        reason: 为什么要重做（留痕；为空时沿用 stale 原因）
        artifact: 可选的新产物 —— 给出即等价于 refresh + mark-stage-done 一步完成

    Returns: {"ok","stage","redone","stale_before","old_artifact","questions_to_apply",...}
    """
    _guard = _require_analyst_actor("refresh-stage", actor, work_dir)
    if _guard:
        return _guard
    if stage not in STAGE_IDS:
        return {"ok": False,
                "error": "未知阶段 %r；合法阶段：%s" % (stage, " → ".join(STAGE_IDS))}
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    _latest = _stage_latest(plan).get(stage)
    _stale = _stale_map(plan).get(stage)
    if _latest is None and _stale is None:
        return {"ok": False,
                "error": ("阶段【%s】还没做过，无需 refresh —— 直接 begin-stage "
                          "正常推进即可。" % STAGE_LABELS[stage]),
                "hint": "python mode2_flow.py begin-stage %s" % stage}

    _reason = (str(reason or "").strip()
               or (_stale or {}).get("reason") or "手工请求重做该阶段")
    _old_artifact = (_stale or {}).get("old_artifact")
    if _old_artifact is None and _latest:
        _old_artifact = _latest.get("artifact")

    idx = STAGE_IDS.index(stage)
    plan["stage_index"] = max(int(plan.get("stage_index", -1)), idx)
    plan.setdefault("stages", []).append({
        "stage": stage, "label": STAGE_LABELS[stage], "needs": STAGE_NEEDS[stage],
        "status": "in_progress", "note": "redo: " + _reason,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
        "redo_of_artifact": _old_artifact,
    })
    _redo_watch(plan)[stage] = {
        "stage": stage,
        "old_artifact": _old_artifact,
        "reason": _reason,
        "questions": ((_stale or {}).get("questions") or []),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_plan(plan, work_dir)

    out = {"ok": True, "stage": stage, "label": STAGE_LABELS[stage],
           "redone": True, "stale_before": bool(_stale),
           "old_artifact": _old_artifact, "reason": _reason,
           "requires": STAGE_NEEDS[stage],
           "questions_to_apply": [q for q in ((_stale or {}).get("questions") or [])],
           "next": ("重算后提交【新】产物：python mode2_flow.py mark-stage-done %s "
                    "--artifact \"<新产物>\"（与旧产物相同会被拒）" % stage)}
    if str(artifact or "").strip():
        _mk = mark_stage_done(stage, artifact, work_dir)
        out["mark"] = _mk
        out["ok"] = bool(_mk.get("ok"))
        out["artifact"] = artifact if _mk.get("ok") else None
        if not _mk.get("ok"):
            out["error"] = _mk.get("error")
            out["gate"] = _mk.get("gate")
    return out


def question_report(work_dir=None, final=False):
    """汇总提问情况。final=True 时【强制】校验下界是否达到。"""
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    depth = plan.get("param_depth")
    # ── 【BUG-02 修复】depth 读不到时先回退 workflow_state，再判错 ─────────
    # 实测（2026-10-30 转台任务）：连续两次调 questions，第一次正常返回
    #   asked=10/answered=6，第二次却返回「尚未确定参数需求强度」。
    # 根因：refresh-stage / 阶段重做期间 mode2_plan 会被重写，
    #   param_depth 有窗口期读不到；而真正的判定其实在 workflow_state 里。
    # 修复：plan 里没有时回退到权威 workflow_state 的 param_depth，
    #   仍读不到才报错；回退会在返回体里带 depth_source 便于排查。
    _depth_src = "plan"
    if not depth:
        try:
            _st = _read_state_json("workflow_state.json") or {}
            _d2 = _st.get("param_depth")
            if _d2:
                depth = _d2
                _depth_src = "workflow_state"
        except Exception:
            pass
    if not depth:
        return {"ok": False,
                "error": ("尚未确定参数需求强度（强需求/弱需求）—— "
                          "mode2_plan 与 workflow_state 里都没有 param_depth。"),
                "hint": "先 set_depth（或重跑 provide_context #2）再查询。"}
    qs = plan.get("questions", [])
    chk = validate_question_budget(depth, len(qs), final=final)
    # 登记 ≠ 问过：只有 answered=True 才算真的问到了用户。
    _answered = [q for q in qs if q.get("answered")]
    _unanswered = [q for q in qs if not q.get("answered")]
    if final and _unanswered:
        chk = dict(chk)
        chk["ok"] = False
        chk["error"] = ("有 %d 题只登记、未记录用户答复（等于没问）：%s"
                        % (len(_unanswered),
                           "、".join(str(q.get("topic")) for q in _unanswered[:6])))
    by_stage = {}
    for q in qs:
        by_stage.setdefault(q.get("stage_label") or q.get("stage"), []).append(q.get("topic"))
    out = dict(chk)
    out["questions"] = qs
    out["answered"] = len(_answered)
    out["unanswered"] = len(_unanswered)
    out["by_stage"] = by_stage
    # 【BUG-02】说明 param_depth 来自哪里（plan / workflow_state 回退）
    out["depth_source"] = _depth_src
    out["param_depth"] = depth
    return out


SUBPLAN_AUTHORITY = "总计子代理"

#: 每个子代理（房间）必须被总计子代理说明"为什么需要它"。
MIN_SUBAGENTS = 1
MAX_SUBAGENTS = 24


# ═══════════════════════════════════════════════════════════════════════════
# 七·零、【2026-10-28 重构】总计子代理必须是一个【真实子代理】
# ═══════════════════════════════════════════════════════════════════════════
# 实测缺陷（会话 session-31c56a28）：
#   主对话自己在主会话里把十阶段全跑完了，然后调 set-subagent-plan 时
#   只写了 --decided-by "总计子代理" 这个【字符串】。门禁只校验字符串，
#   于是"总计子代理"从头到尾没有被创建过 —— 用户原话：
#     「总计子代理十阶段流水线不是要开总计子代理的吗？你在干嘛」
#
# 现在（代码强制，不靠提示词）：
#   ① 总计子代理开工第一件事必须跑 analyst-bind：
#        它从环境变量 DSH_SESSION_ID 取到【自己】的 sessionId 并落盘；
#   ② 十阶段命令（begin-stage / mark-stage-done / ask / answer-question /
#      refresh-stage / set-subagent-plan）一律要求【当前调用者】就是
#      被绑定的那个 sessionId；主对话直接调 → ANALYST_ACTOR_REQUIRED；
#   ③ 未绑定 analyst 时：set-subagent-plan 直接拒绝；
#      workflow_gate.select 也会拦（见 workflow_gate 的 MODE2_ANALYST_REQUIRED）。
ANALYST_ROOM_NAME = "总计子代理"
ANALYST_ROOM_TYPE = "analyst"
ANALYST_SESSION_ENV = "DSH_SESSION_ID"


def _current_actor(actor=None):
    """当前调用者身份：显式 --actor 优先，其次宿主注入的 DSH_SESSION_ID。"""
    v = str(actor or "").strip()
    if v:
        return v
    try:
        return str(os.environ.get(ANALYST_SESSION_ENV) or "").strip() or None
    except Exception:
        return None


def analyst_bind(session_id=None, work_dir=None, force=False):
    """把【总计子代理】的 sessionId 绑定到当前任务（由它在自己会话内调用）。

    只在总计子代理内部跑：它会从 DSH_SESSION_ID 读到自己的 id。
    重复绑定同一 id → 幂等；绑定到另一个 id 而未 force → 拒绝（防止主对话
    抢着绑一个假的）。
    """
    sid = str(session_id or "").strip() or _current_actor()
    if not sid:
        return {"ok": False,
                "error": ("无法确定总计子代理的 sessionId：请在本子代理会话内运行，"
                          "或显式传 --session-id。"),
                "env_checked": ANALYST_SESSION_ENV}
    # ── 【2026-10-28 重构】只允许【真实子代理】绑定自己 ──────────────────
    # 否则主对话可以拿自己的 DSH_SESSION_ID 抢先 analyst-bind，
    #   把"总计子代理"重新退回成一个身份字符串。
    # 判定：插件注册表存在且非空时，本会话 id 必须是其中一条；
    #   注册表不存在（测试/老宿主）则放行（不阻塞可测试性）。
    # 插件登记有 300ms 节流，子代理刚创建时注册表可能还没写入 → 最多重试 6 次
    # （共约 3 秒）。主对话永远等不到自己的 id 出现，最终仍会失败。
    _agents = []
    _registry_seen = False
    for _attempt in range(6):
        try:
            _reg_p = os.path.join(_state_dir(), "platform_agents.json")
            if os.path.exists(_reg_p):
                with open(_reg_p, "r", encoding="utf-8-sig") as _rf:
                    _reg = json.load(_rf) or {}
                _agents = [a for a in (_reg.get("agents") or [])
                           if isinstance(a, dict) and a.get("id")]
                # 注册表过旧（>10 分钟没刷新）= 上一个会话的残留，不能当权威
                _upd = _reg.get("updatedAt")
                if isinstance(_upd, str):
                    try:
                        import datetime as _dt
                        _upd = _dt.datetime.fromisoformat(
                            _upd.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        _upd = None
                else:
                    try:
                        _upd = float(_upd) if isinstance(_upd, (int, float)) else None
                    except Exception:
                        _upd = None
                if _upd and _upd > 1e11:
                    _upd = _upd / 1000.0
                if _upd and (time.time() - _upd) > 600:
                    _agents = []
                _registry_seen = bool(_agents)
        except Exception:
            _agents, _registry_seen = [], False
        if not _registry_seen:
            break                      # 无注册表（测试/老宿主）→ 放行
        if any(str(a.get("id")) == sid for a in _agents):
            break                      # 已在注册表里 → 放行
        if _attempt < 5:
            time.sleep(0.5)
    if _registry_seen and not any(str(a.get("id")) == sid for a in _agents):
        return {"ok": False,
                "gate": "ANALYST_BIND_REQUIRES_SUBAGENT",
                "session_id": sid,
                "error": ("analyst-bind 只能在【子代理会话】里执行："
                          "当前 sessionId 不在平台子代理注册表中。"
                          "主对话不能把自己绑成总计子代理 —— "
                          "请先用 subagent_fork 创建总计子代理，"
                          "由它自己运行本命令。"),
                "known_subagents": [{"id": a.get("id"), "label": a.get("label")}
                                    for a in _agents]}
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    old = plan.get("analyst") or {}
    _old_sid = str(old.get("session_id") or "").strip()
    # ── 【2026-10-29 G05】旧 analyst 已失联时，允许【免 --force】接管 ──────
    # 实测：第一任总计子代理跑完十阶段 + 13 题，但在 set-subagent-plan 前
    #   因 ask_user 阻塞超时中断；续跑子代理必须 --force 才能绑，否则
    #   ANALYST_ALREADY_BOUND —— 分析成果明明在盘上，却被"身份"挡住。
    # 判据（全部满足才自动放行，其余仍要求 --force）：
    #   ① 旧 sessionId 不在当前平台注册表里（已 disposed / 崩溃丢失）；
    #   ② 十阶段已全部完成（分析成果在盘上，接管不会覆盖半成品）。
    # ── 【2026-10-29 修复】旧 analyst 还活着 → 拒绝抢身份 ────────────────
    # 会话 ceac3fff 事故：三个总计子代理互相 analyst-bind --force 抢了 9 次，
    #   每个都以为自己是唯一的总计子代理，各自提问 → 用户看到重复选项。
    # 现在：旧 analyst 心跳/平台证据显示仍存活时，改绑一律拒绝（除非 --force）。
    if _old_sid and _old_sid != sid and not force:
        _live_old = analyst_liveness(work_dir)
        if _live_old.get("alive") and str(_live_old.get("session_id") or "") == _old_sid:
            return {"ok": False,
                    "gate": "ANALYST_STILL_ALIVE",
                    "bound_session_id": _old_sid,
                    "session_id": sid,
                    "liveness": _live_old,
                    "error": ("【拒绝抢身份】已绑定的总计子代理 %s 仍在运行（%s）。"
                              % (_old_sid, _live_old.get("detail"))),
                    "why": ("2026-10-29 实测：多个总计子代理同时 analyst-bind --force "
                            "互相覆盖身份，导致三方并行提问、用户看到重复选项。"),
                    "next": ("不要抢。若你是被误开的重复子代理 → 立刻结束回合（不要"
                             "再调任何阶段命令）；确需接管 → 父代理先确认旧会话已结束。")}

    _auto_rebind = False
    _auto_rebind_why = None
    if _old_sid and _old_sid != sid and not force:
        try:
            _reg_agents = []
            _regp = os.path.join(_state_dir(), "platform_agents.json")
            if os.path.exists(_regp):
                with open(_regp, "r", encoding="utf-8-sig") as _rf2:
                    _reg2 = json.load(_rf2) or {}
                _reg_agents = [a for a in (_reg2.get("agents") or [])
                               if isinstance(a, dict) and a.get("id")]
            _old_still_alive = any(
                str(a.get("id")) == _old_sid
                and str(a.get("status") or "").lower() != "disposed"
                for a in _reg_agents)
            _all_done = bool(stage_status(work_dir).get("complete"))
            if _reg_agents and not _old_still_alive and _all_done:
                _auto_rebind = True
                _auto_rebind_why = (
                    "旧总计子代理 %s 已不在平台注册表（disposed/失联），"
                    "且十阶段已全部完成 —— 自动允许新子代理接管身份，无需 --force。"
                    % _old_sid)
        except Exception:
            _auto_rebind = False
    if _old_sid and _old_sid != sid and not force and not _auto_rebind:
        return {"ok": False, "gate": "ANALYST_ALREADY_BOUND",
                "session_id": _old_sid,
                "error": ("本任务的总计子代理已绑定为 %s，拒绝改绑到 %s。"
                          "确需改绑（例如上一个子代理确实挂了）请加 --force。"
                          % (_old_sid, sid)),
                "how": ("若旧子代理确实已失联，加 --force；"
                        "现在也支持'旧 analyst 已 disposed + 十阶段已完成'时自动接管。"),
                "bound_at": old.get("bound_at")}
    if _auto_rebind and not force:
        force = True       # 走下面的统一改绑分支，并在返回里说明原因
    if not _old_sid or force:
        plan["analyst"] = {
            "session_id": sid,
            "room_name": ANALYST_ROOM_NAME,
            "type": ANALYST_ROOM_TYPE,
            "bound_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
            "bound_by": "analyst-bind",
        }
        save_plan(plan, work_dir)
        try:
            plan = load_plan(work_dir)
        except Exception:
            pass
    _out = {"ok": True, "session_id": sid, "room_name": ANALYST_ROOM_NAME,
            "task_id": plan.get("task_id"),
            "note": ("总计子代理已绑定。现在【只有这个会话】能推进十阶段 / 登记编制；"
                     "主对话直接调这些命令会被 ANALYST_ACTOR_REQUIRED 拒绝。"),
            "next": "python mode2_flow.py begin-stage TaskUnderstanding"}
    if _auto_rebind:
        _out["auto_rebind"] = True
        _out["auto_rebind_reason"] = _auto_rebind_why
        _out["note"] = _auto_rebind_why + " " + _out["note"]
    return _out




# ═══════════════════════════════════════════════════════════════════════════
# 七·零·补、【2026-10-29 修复】总计子代理【存活心跳】—— 防止重复 fork
# ═══════════════════════════════════════════════════════════════════════════
# 现场事故（会话 ceac3fff，三个总计子代理同时在跑）：
#   18:20:52 fork #1 (fff32ea0)
#   18:21:10 lead 调 wait_agent → noProgress(no-active-peer)
#   18:21:22 lead 调 list_agents → 只看到自己 → 判定"子代理已死"
#   18:23:24 fork #2 (11de1d27)   ← 此时 #1 还在跑
#   18:25:40 fork #3 (6bf2ef59)   ← 此时 #1 #2 都还在跑
#   三个子代理互相 analyst-bind --force 抢身份 9 次，各自 ask_user.py 提问
#   → 用户看到已经答过的选项反复弹出。
#
# 根因有两条，缺一不可：
#   ① 【平台层】subagent_fork 出来的子代理【不是 Team member】：
#      list_agents 只返回 lead，wait_agent 返回 noProgress(no-active-peer)。
#      主对话因此【没有任何手段】判断 fork 出去的子代理是否还活着。
#   ② 【门禁层】analyst-bind 只记 sessionId，不记"它还活着吗"。
#      绑定存在 ≠ 子代理在跑；反过来"绑定存在"也没能阻止再 fork 一个。
#
# 修复（本段）：
#   · 总计子代理在每个阶段动作时自动刷新心跳（无需模型额外调用）；
#   · analyst-status 回传 alive / heartbeat_age_sec / alive_source；
#   · 新增 analyst-guard：主对话 fork 之前【必须】先问它，
#     活着的 analyst 会被拒绝重复创建（gate=ANALYST_ALREADY_ALIVE）；
#   · analyst-bind 在"已绑定且心跳新鲜"时拒绝改绑（除非 --force）。
ANALYST_HEARTBEAT_FILE = "analyst_heartbeat.json"
#: 心跳多久没刷新算"可能已死"。留足余量：子代理跑一段长分析不刷新是常态。
ANALYST_ALIVE_SEC = 600


def _analyst_hb_path(work_dir=None):
    """心跳文件路径（与 plan 同目录，跟随当前任务）。"""
    try:
        return os.path.join(_plan_dir(work_dir), ANALYST_HEARTBEAT_FILE)
    except Exception:
        return os.path.join(BASE_DIR, ANALYST_HEARTBEAT_FILE)


def analyst_heartbeat(session_id=None, work_dir=None, note=""):
    """总计子代理刷新存活心跳（阶段动作会自动调用，也可手动调）。

    只认【已绑定的那个 sessionId】—— 别的会话调它会被拒绝，
    避免主对话替子代理伪造存活。
    """
    sid = str(session_id or "").strip() or _current_actor()
    if not sid:
        return {"ok": False, "error": "无法确定 sessionId（不在 DSH 会话内？）"}
    plan = load_plan(work_dir)
    bound = str((plan.get("analyst") or {}).get("session_id") or "").strip()
    if bound and bound != sid:
        return {"ok": False, "gate": "ANALYST_HEARTBEAT_WRONG_ACTOR",
                "bound_session_id": bound, "session_id": sid,
                "error": ("心跳只能由【已绑定的总计子代理】刷新；当前会话 %s "
                          "不是绑定的 %s。" % (sid, bound))}
    rec = {"session_id": sid, "ts": time.time(),
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": str(note or "")}
    p = _analyst_hb_path(work_dir)
    try:
        _atomic_write_json(p, rec)
    except Exception as e:
        return {"ok": False, "error": "写心跳失败: %r" % (e,)}
    return {"ok": True, "session_id": sid, "path": p,
            "ts": rec["ts"], "alive_sec": ANALYST_ALIVE_SEC}


def _read_analyst_hb(work_dir=None):
    try:
        p = _analyst_hb_path(work_dir)
        if not os.path.exists(p):
            return {}
        with open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def analyst_liveness(work_dir=None):
    """判断【已绑定的总计子代理】现在是否还活着。

    判据（从严到宽）：
      ① 心跳新鲜（< ANALYST_ALIVE_SEC）—— 最可靠，阶段动作会自动刷新；
      ② 平台注册表里该 sessionId 仍是 running（插件自动登记）；
      ③ 都拿不到 → alive=False 但 source="unknown"（**不是**"确认已死"）。
    """
    plan = load_plan(work_dir)
    sid = str((plan.get("analyst") or {}).get("session_id") or "").strip()
    out = {"bound": bool(sid), "session_id": sid or None, "alive": False,
           "age_sec": None, "source": "none", "detail": None}
    if not sid:
        out["detail"] = "尚未绑定总计子代理"
        return out
    hb = _read_analyst_hb(work_dir)
    _hb_ok = False
    if str(hb.get("session_id") or "") == sid:
        try:
            _age = time.time() - float(hb.get("ts") or 0)
        except Exception:
            _age = None
        out["age_sec"] = round(_age, 1) if _age is not None else None
        if _age is not None and _age <= ANALYST_ALIVE_SEC:
            _hb_ok = True

    # ── 【2026-10-29 修复】心跳不能覆盖"平台已 disposed" ────────────────
    # 坑：子代理被 disposed 之后，心跳文件里还留着它最后写的 ts（很新），
    #   若只看心跳就会判"还活着" → 新子代理永远接不了班（ANALYST_STILL_ALIVE）。
    # 判据优先级：**平台 disposed 是终态**，心跳只能证明"曾经活过"。
    _plat_disposed = False
    try:
        _regp0 = os.path.join(_state_dir(), "platform_agents.json")
        if os.path.exists(_regp0):
            with open(_regp0, "r", encoding="utf-8-sig") as _rf0:
                _reg0 = json.load(_rf0) or {}
            for _a0 in (_reg0.get("agents") or []):
                if (isinstance(_a0, dict)
                        and str(_a0.get("id") or "") == sid
                        and str(_a0.get("status") or "").lower() == "disposed"):
                    _plat_disposed = True
                    break
    except Exception:
        pass
    if _plat_disposed:
        out["alive"] = False
        out["source"] = "platform_disposed"
        out["detail"] = ("平台已 disposed（终态）→ 判定【已结束】，"
                         "心跳仅是历史残留（%ss 前）"
                         % (round(out["age_sec"]) if out["age_sec"] else "?"))
        return out

    if _hb_ok:
        out["alive"] = True
        out["source"] = "heartbeat"
        out["detail"] = ("心跳 %ss 前刷新（阈值 %ss）→ 判定【活着】"
                         % (round(out["age_sec"]), ANALYST_ALIVE_SEC))
        return out
    # ② 平台注册表 + 主进程脉冲（2026-10-29：这是 99% 可靠的判据）
    #    pulse 由【DSH 主进程的 setInterval】写，与子代理在干什么无关：
    #      子代理跑 SW / 等锁 / 发呆 → 脉冲照跳；
    #      agent 被 disposed        → 从脉冲台账移除；
    #      DSH 进程没了             → 脉冲停（此时所有子代理也都没了，判死正确）。
    try:
        _regp = os.path.join(_state_dir(), "platform_agents.json")
        if os.path.exists(_regp):
            with open(_regp, "r", encoding="utf-8-sig") as _rf:
                _reg = json.load(_rf) or {}
            # 先看脉冲还活着吗
            _pulse = _reg.get("pulse") if isinstance(_reg.get("pulse"), dict) else {}
            _pulse_ok = False
            _pulse_age = None
            if _pulse:
                _pts = _pulse.get("atMs") or _pulse.get("at")
                try:
                    if isinstance(_pts, str):
                        import datetime as _dt
                        _psec = _dt.datetime.fromisoformat(
                            _pts.replace("Z", "+00:00")).timestamp()
                    else:
                        _psec = float(_pts) / 1000.0 if float(_pts) > 1e11 else float(_pts)
                    _pulse_age = time.time() - _psec
                    _pulse_ok = _pulse_age <= 35
                except Exception:
                    _pulse_ok = False
            for _a in (_reg.get("agents") or []):
                if not isinstance(_a, dict):
                    continue
                if str(_a.get("id") or "") != sid:
                    continue
                _st = str(_a.get("status") or "").lower()
                if _st == "disposed":
                    out["source"] = "platform_registry"
                    out["detail"] = "平台已 disposed → 判定【已结束】"
                    return out
                if _pulse_ok:
                    out["alive"] = True
                    out["source"] = "pulse+registry"
                    out["detail"] = ("主进程脉冲 %ss 前跳动 + agent 在册(status=%s) "
                                     "→ 判定【活着】（与子代理忙不忙无关）"
                                     % (round(_pulse_age or 0), _st or "running"))
                    return out
                # agent 在册但脉冲停了 → 进程可能没了，判"未知"而非"活着"
                out["source"] = "registry_only"
                out["detail"] = ("agent 在册但主进程脉冲已停（%ss 未更新）—— "
                                 "【未知】：DSH 进程可能已退出。"
                                 % (round(_pulse_age) if _pulse_age else "?"))
                return out
    except Exception:
        pass
    out["source"] = "unknown"
    if out["age_sec"] is not None:
        out["detail"] = ("心跳已 %ss 未刷新（阈值 %ss），且平台注册表无该 id —— "
                         "【未知】，不等于确认死亡。"
                         % (round(out["age_sec"]), ANALYST_ALIVE_SEC))
    else:
        out["detail"] = ("既无心跳也无平台登记 —— 【未知】。旧版本/刚绑定属正常；"
                         "建议先 analyst-guard 再决定是否重开。")
    return out


def analyst_guard(work_dir=None):
    """【fork 前置闸门】主对话在 subagent_fork 之前必须先问它。

    活着的总计子代理 → 拒绝重复创建（ceac3fff 事故的直接修复点）。
    """
    live = analyst_liveness(work_dir)
    if not live.get("bound"):
        return {"ok": True, "may_fork": True,
                "reason": "尚未绑定，可以创建总计子代理",
                "liveness": live,
                "next": "subagent_fork(description=总计子代理（十阶段分析）)"}
    if live.get("alive"):
        return {
            "ok": False,
            "gate": "ANALYST_ALREADY_ALIVE",
            "may_fork": False,
            "liveness": live,
            "error": ("【禁止重复创建】总计子代理仍在运行（session %s，%s）。"
                      % (live.get("session_id"), live.get("detail"))),
            "why": ("2026-10-29 实测事故：主对话因 list_agents 看不到 fork 的子代理，",
                    "误判已死并连续 fork 了 3 个，导致三方抢身份、题目重复弹出。"),
            "next": ("不要 fork。改用：① 等它（sleep 后重查 analyst-status）；",
                     "② 需要催它 → send_message 给它；",
                     "③ 只有 analyst-status 连续 2 次显示 source=platform_registry",
                     "   且 status=disposed，才允许 analyst-bind --force 后重开。"),
        }
    return {
        "ok": True,
        "may_fork": True,
        "liveness": live,
        "caution": ("绑定存在但存活证据不足（%s）—— 允许重开，但请先确认："
                    "① 再查一次 analyst-status；② 若十阶段已完成，直接补 "
                    "set-subagent-plan，不必重跑分析。" % (live.get("source"),)),
        "next": "subagent_fork(description=总计子代理（接管/续跑）)",
    }

def analyst_status(work_dir=None):
    """查看总计子代理是否已真实绑定 + 是否还活着（只读）。"""
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "bound": False, "error": plan["_load_error"]}
    rec = plan.get("analyst") or {}
    sid = str(rec.get("session_id") or "").strip()
    # ── 【2026-10-29 修复】必须回传"还活着吗" ──────────────────────────
    # 旧实现只说 bound=True/False。而"绑定存在"≠"子代理在跑"：
    #   会话 ceac3fff 里 lead 看到 bound=true 却看不到进程，只能猜；
    #   一猜错就 fork 出 3 个总计子代理同时抢身份。
    live = analyst_liveness(work_dir) if sid else {"alive": False,
                                                   "source": "none",
                                                   "detail": "未绑定"}
    return {
        "ok": True,
        "bound": bool(sid),
        "session_id": sid or None,
        "room_name": ANALYST_ROOM_NAME,
        "bound_at": rec.get("bound_at"),
        # ── 存活三件套（主对话据此判断"该等还是该重开"）──
        "alive": bool(live.get("alive")),
        "alive_source": live.get("source"),
        "heartbeat_age_sec": live.get("age_sec"),
        "alive_sec_threshold": ANALYST_ALIVE_SEC,
        "liveness_detail": live.get("detail"),
        "current_actor": _current_actor(),
        "is_current_actor": bool(sid and _current_actor() == sid),
        "authority": SUBPLAN_AUTHORITY,
        "how_to_bind": ("在【总计子代理】里运行：python mode2_flow.py analyst-bind"
                        "（自动取它自己的 DSH_SESSION_ID）"),
        "how_to_check": ("fork 之前先跑 analyst-guard；不要用 list_agents 判断 "
                         "fork 出来的子代理死活（它看不到）。"),
        "note": (None if sid else
                 "⚠️ 总计子代理尚未创建/绑定 —— 十阶段与子代理编制都不合法。"),
    }


def _require_analyst_actor(op, actor=None, work_dir=None):
    """校验"当前调用者 == 已绑定的总计子代理"。未绑定时返回 None（由调用方决定）。

    返回错误 dict 或 None。这条把"总计子代理"从字符串变成真实身份。

    ── 【2026-10-29 修复】校验通过时【顺手刷新心跳】────────────────────
    这样总计子代理只要在干活（跑阶段/提问/登记编制），心跳就自动新鲜，
    主对话随时能 analyst-status 看到 alive=true，不会再误判"已死"。
    不需要模型额外记得调任何保活命令。
    """
    plan = load_plan(work_dir)
    rec = plan.get("analyst") or {}
    sid = str(rec.get("session_id") or "").strip()
    if not sid:
        return None
    cur = _current_actor(actor)
    if cur and cur == sid:
        # 身份吻合 = 它正在干活 → 自动保活
        try:
            analyst_heartbeat(cur, work_dir, note="auto:%s" % op)
        except Exception:
            pass
        return None
    return {
        "ok": False,
        "gate": "ANALYST_ACTOR_REQUIRED",
        "op": op,
        "bound_session_id": sid,
        "current_actor": cur,
        "error": ("【越权】%s 只能由【%s】执行（绑定会话 %s），"
                  "当前调用者是 %s。十阶段分析必须发生在总计子代理内部，"
                  "主对话直接调是违规的。"
                  % (op, ANALYST_ROOM_NAME, sid, cur or "（未识别到会话）")),
        "how": [
            "1) 主对话用 subagent_fork 创建【总计子代理】；",
            "2) 在该子代理里运行：python \"<根>\\tools\\mode2_flow.py\" analyst-bind",
            "3) 之后所有十阶段 / 提问 / 编制命令都在该子代理里执行。",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 七·补充、【Bug③ 修复】房间类型规范化 + rooms 快照（三处一致性）
# ═══════════════════════════════════════════════════════════════════════════
# 缺陷回顾（2026-10-19 实测）：
#   总计子代理在 set-subagent-plan 里把房间类型写成 drivetrain / sensor /
#   drawing，门禁只认 transmission / housing / drafting，于是：
#     · workflow_gate.select 用【新类型】路由波次 → 按 drafting 放行；
#     · 而 mode2_plan.json 的 rooms 快照从未刷新 → mode_gate.room-status /
#       defense_gate.check-room 读到旧类型 drivetrain/sensor → require_physics
#       被算成 false → 物理/领域凭据被静默豁免（虚假 CRITICAL 也一起放行）；
#     · 更早还有 drawing 导致 CONFIG_MISSING_FINAL_WAVES。
#
# 现在（代码强制）：
#   ① 类型必须能被规范化到【规范类型表】；规范失败 = 明确报错，不静默放过；
#   ② set-subagent-plan 落盘时同步写 mode2_plan.json 的 subagent_config.rooms
#      （与 workflow_state.subagent_config.rooms 同构：[房间名, 类型] 对）；
#   ③ 只读命令 room-types 输出三处 room_type 的一致性自检。

#: 门禁认可的规范房间类型（与 workflow_gate.WAVE1_TYPES / defense_gate
#: .MODELING_ROOM_TYPES / mode_gate 波次路由对齐；不在此表 = 必然漏启动或免校核）。
CANONICAL_ROOM_TYPES = (
    "structural", "transmission", "housing", "support", "spring", "thermal",
    "corrosion", "assembly", "drafting", "prime",
)

#: 历史遗留 / 同义写法 → 规范类型。
#: 这里列出的每条都对应一次真实踩坑（见 DSH插件Bug记录.md Bug③/⑥）。
ROOM_TYPE_ALIASES = {
    # Bug③ 实测：drivetrain 是历史遗留类型，不在波1类型表 → require_physics=false
    "drivetrain": "transmission",
    "drive": "transmission",
    "gear": "transmission",
    "传动": "transmission",
    # Bug⑥ 实测：sensor 不在白名单 → 物理/领域凭据被静默豁免
    "sensor": "housing",
    "sensors": "housing",
    "electronics": "housing",
    "electronic": "housing",
    "外壳": "housing",
    "壳体": "housing",
    # Bug③ 实测：drawing 导致 CONFIG_MISSING_FINAL_WAVES（门禁只认 drafting）
    "drawing": "drafting",
    "drawings": "drafting",
    "dwg": "drafting",
    "工程图": "drafting",
    "出图": "drafting",
    # 其它常见同义写法
    "structure": "structural",
    "frame": "structural",
    "结构": "structural",
    "机架": "structural",
    "assembly_validation": "assembly",
    "assembly-validation": "assembly",
    "总装": "assembly",
    "验证": "assembly",
    "prime_agent": "prime",
    "总理": "prime",
}


def normalize_room_type(raw):
    """把房间类型规范化到 CANONICAL_ROOM_TYPES。**失败即报错**（不静默兜底）。

    Args:
        raw: 原始类型字符串（大小写 / 空白 / 连字符 / 同义词都容忍）

    Returns:
        {"ok": True, "type": <规范类型>, "raw":..., "normalized": bool, "alias":...}
        或 {"ok": False, "error": "...", "raw":..., "allowed": [...]}

    为什么不做"猜一个最接近的"兜底：
      静默兜底正是 Bug③/⑥ 的来源 —— drivetrain 被悄悄当成未知类型，
      门禁于是 require_physics=false，凭据被静默豁免。宁可报错让人改。
    """
    _raw = raw
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return {"ok": False, "raw": _raw,
                "error": ("房间类型为空 —— 必须显式给 type（规范类型：%s）。"
                          % "/".join(CANONICAL_ROOM_TYPES)),
                "allowed": list(CANONICAL_ROOM_TYPES)}
    if s in CANONICAL_ROOM_TYPES:
        return {"ok": True, "type": s, "raw": _raw, "normalized": (str(_raw) != s),
                "alias": (str(_raw) if str(_raw) != s else None)}
    if s in ROOM_TYPE_ALIASES:
        canon = ROOM_TYPE_ALIASES[s]
        return {"ok": True, "type": canon, "raw": _raw, "normalized": True,
                "alias": str(_raw),
                "note": ("类型 %r 不在门禁规范表内，已规范化为 %r"
                         "（否则会漏启动或被免校核）。" % (str(_raw), canon))}
    return {"ok": False, "raw": _raw,
            "error": ("房间类型 %r 无法规范化到门禁认可的规范类型（%s）。"
                      "未知类型会让波次路由漏启动、或 require_physics 直接豁免 —— "
                      "拒绝登记。请改用规范类型，或先在此处补别名并写回归测试。"
                      % (str(_raw), "/".join(CANONICAL_ROOM_TYPES))),
            "allowed": list(CANONICAL_ROOM_TYPES),
            "known_aliases": sorted(ROOM_TYPE_ALIASES)}


def room_type_snapshot(rooms):
    """把编制里的 rooms 规范化为门禁可直接消费的快照（[(name, type), ...]）。

    单独抽出来是为了让 set_subagent_plan / room_types_report 共用同一套
    规范化逻辑 —— 两处各写一份正是"口径分裂"的来源。
    """
    out, seen = [], set()
    for r in (rooms or []):
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        nz = normalize_room_type(r.get("type"))
        if not nm or not nz.get("ok"):
            continue
        if nm in seen:
            continue
        seen.add(nm)
        out.append([nm, nz["type"]])
    return out


def require_stages_complete(work_dir=None):
    """【强制点】编制作出 / 开工前，十阶段必须全部完成。

    为什么必须有这条：
      set_subagent_plan 原本只校验 decided_by == "总计子代理" 这个**字符串**，
      模型只要在参数里写上这几个字就能通过 —— 实测就是这样绕过的：
      手写 4 间房、6 间房直接过 select，而 stage-status 还停在开头。
      字符串是自证的，分析产物不是。所以这里验的是 stage 的 done 状态。

    Returns: {"ok": True, ...} 或 {"ok": False, "gate": "ANALYSIS_INCOMPLETE", ...}
    """
    st = stage_status(work_dir)
    if not st.get("ok"):
        return st
    # ── 【Bug② 修复】stale / 等答复的阶段一律视为【未完成】（fail-closed）──
    # 原实现只看"有没有 done 记录"，于是"产物早就写死在用户答复之前"也能过闸：
    #   阶段 7/10 里写的还是「底盘 100×100 / PLA」，用户确认的是「150×120 / PETG」。
    # 现在 stale 阶段（题目已作答、产物需重算）与"还挂着未答题目"的阶段
    # 都算 blocking —— 必须重做、把用户答复落进产物，才允许登记编制/开工。
    _missing = list(st.get("missing") or [])
    _stale = list(st.get("stale") or [])
    _awaiting = list(st.get("awaiting_answer") or [])
    _blocking = list(st.get("blocking") or [])
    if _blocking:
        _hints = []
        if _missing:
            _hints.append("未完成：%s" % "、".join(_missing))
        if _stale:
            _hints.append("产物过期（必须重做）：%s" % "、".join(_stale))
        if _awaiting:
            _hints.append("产物依赖未答题目：%s" % "、".join(_awaiting))
        _how = []
        for _lbl in _stale + _awaiting:
            _sid = next((r["stage"] for r in (st.get("rows") or [])
                         if r.get("label") == _lbl), None)
            if _sid:
                _how.append("python mode2_flow.py refresh-stage %s" % _sid)
        if _missing:
            _how.append("python mode2_flow.py begin-stage <阶段> → mark-stage-done <阶段>")
        return {
            "ok": False,
            "gate": "ANALYSIS_STALE" if (_stale or _awaiting) else "ANALYSIS_INCOMPLETE",
            "stage_index": st.get("stage_index"),
            "done_count": st.get("done_count"),
            "total": st.get("total"),
            "missing": _missing,
            "stale": _stale,
            "awaiting_answer": _awaiting,
            "blocking": _blocking,
            "error": ("十阶段分析尚未达到可交付状态（已完成 %d/%d，"
                      "其中 %d 个阶段产物过期、%d 个阶段在等用户答复），"
                      "禁止登记编制或开工。"
                      % (st.get("done_count") or 0, st.get("total") or 10,
                         len(_stale), len(_awaiting))),
            "hint": tuple(_hints + ["—— " + h for h in _how]),
        }
    return {"ok": True, "done_count": st.get("done_count"),
            "total": st.get("total")}


def set_subagent_plan(rooms, rationale, decided_by, work_dir=None, actor=None):
    """由【总计子代理】登记"本轮该开几个子代理、分别做什么"。

    ★ 强制点：`decided_by` 必须是"总计子代理"。
      早期实现把房间表写死在代码里（固定 3 个 + 总装 + 出图），
      此处改为"必须由总计子代理按分析结果决定"并落盘留痕。

    Args:
        rooms: [{"name": 房间名, "type": 类型, "parts": [零件名...],
                 "why": 为什么需要这个子代理, "depends_on": [房间名...]}]
        rationale: 总体分解理由（对应功能分解/机构综合的结论）
        decided_by: 必须 == "总计子代理"

    Returns: {"ok":bool, ...}
    """
    # ── 【2026-10-28 重构】编制必须来自【真实的总计子代理】，不是字符串 ──
    # 实测缺陷：主对话自己调 set-subagent-plan 并手写 --decided-by "总计子代理"，
    #   门禁只校验字符串 → 总计子代理从未被创建过。
    _an = analyst_status(work_dir)
    if not _an.get("bound"):
        return {"ok": False,
                "gate": "MODE2_ANALYST_REQUIRED",
                "error": ("【模式二强制】总计子代理尚未创建/绑定（plan 里没有 analyst.session_id）。"
                          "十阶段与子代理编制必须由【真实运行的总计子代理】完成，"
                          "不接受主对话自己写 --decided-by \"总计子代理\"。") ,
                "how": [
                    "1) 主对话用 subagent_fork 创建【总计子代理】；",
                    "2) 在它的会话里运行：python \"<根>\\tools\\mode2_flow.py\" analyst-bind",
                    "3) 由它跑十阶段、提问、再执行本命令登记编制。",
                ],
                "analyst": _an}
    _guard = _require_analyst_actor("set-subagent-plan", actor, work_dir)
    if _guard:
        return _guard
    if str(decided_by or "").strip() != SUBPLAN_AUTHORITY:
        return {"ok": False,
                "error": ("【越权】子代理编制只能由【%s】决定，收到 decided_by=%r。"
                          "固定房间表已被取消 —— 编制必须来自总计子代理的分析结论。"
                          % (SUBPLAN_AUTHORITY, decided_by))}
    # ── 强制点 A：编制作出之前，十阶段必须真的走完 ──────────────────────
    # 只校验 decided_by 字符串是**不够的** —— 模型完全可以自己写上
    # "总计子代理"却根本没做分析（实测：手写 4 间房、6 间房绕过 select）。
    # 因此这里验的是**分析产物**：十阶段必须全部 mark_stage_done。
    _gate = require_stages_complete(work_dir)
    if not _gate.get("ok"):
        return _gate
    # ── 【BUG-3 修复】兼容 {"rooms": [...]} 这层包装 ────────────────────
    # 现场：用户照直觉写成 {"rooms":[...]} → 报"rooms 必须是非空数组"，
    #   而改写成裸数组后又报缺 type —— 两步都踩坑，很难一次写对。
    # 处理：识别到"对象里只有 rooms 键"时【自动解包】，并如实回显。
    _unwrapped = False
    if isinstance(rooms, dict):
        for _k in ("rooms", "subagents", "plan"):
            if isinstance(rooms.get(_k), list):
                rooms = rooms[_k]
                _unwrapped = True
                break
    if not isinstance(rooms, list) or not rooms:
        return {"ok": False,
                "error": ("rooms 必须是非空【JSON 数组】（至少 1 个子代理）。"
                          '注意：直接传数组，不要再包一层 {"rooms": [...]}；'
                          "若确实包了，本命令会自动解包，但推荐按示例写。"),
                "example": [{"name": "结构件", "type": "structural",
                             "parts": ["DSH_车架底板"],
                             "why": "承载整车与电池",
                             "depends_on": []}],
                "types": ["structural", "transmission", "housing", "support",
                          "spring", "thermal", "corrosion", "assembly",
                          "drafting", "prime"]}
    if not str(rationale or "").strip():
        return {"ok": False,
                "error": ("必须给出 rationale（为什么是这个编制）—— "
                          "没有分解理由的编制无法审计，拒绝登记。")}
    if not (MIN_SUBAGENTS <= len(rooms) <= MAX_SUBAGENTS):
        return {"ok": False,
                "error": ("子代理数量 %d 超出允许范围 [%d,%d]。"
                          % (len(rooms), MIN_SUBAGENTS, MAX_SUBAGENTS))}

    norm, seen = [], set()
    for i, r in enumerate(rooms):
        if not isinstance(r, dict):
            return {"ok": False, "error": "rooms[%d] 不是对象" % i}
        name = str(r.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "rooms[%d] 缺少 name" % i}
        if name in seen:
            return {"ok": False, "error": "rooms 里出现重复房间名: %r" % name}
        seen.add(name)
        parts = r.get("parts") or []
        if not isinstance(parts, list) or not parts:
            # ── 【BUG-6 修复】总装/出图等非建模房间也要写占位零件名 ────────
            return {"ok": False,
                    "error": ("子代理【%s】没有列出 parts（该屋负责哪些零件）。"
                              "编制必须写明零件范围，否则无法做归属校验。" % name),
                    "hint": ("即使本屋【不建零件】（总装 / 出图 / 总理），"
                             "也要写至少一个占位名：总装写 DSH_XX装配体，"
                             "出图写 DSH_XX图纸，总理写 全任务零件台账。"
                             "空数组 [] 会被拒绝。"),
                    "example_parts": ["DSH_XX装配体"]}
        if not str(r.get("why") or "").strip():
            return {"ok": False,
                    "error": ("子代理【%s】缺少 why（为什么需要它）。"
                              "用户要求每个零件都能追溯到存在的理由，编制同理。" % name)}
        # ── 【Bug③ 修复】类型必须规范化；规范失败 = 明确报错（不静默放过）──
        # 实测：drivetrain/sensor/drawing 这些历史类型不在门禁波1类型表里，
        #   会导致波1漏启动、require_physics=false（凭据被静默豁免）。
        _nz = normalize_room_type(r.get("type"))
        if not _nz.get("ok"):
            # ── 【BUG-4 修复】缺 type 是最常见的第一次踩坑，必须一次说清 ──
            _raw_t = r.get("type")
            _miss = (_raw_t is None or not str(_raw_t).strip())
            _allowed = _nz.get("allowed") or [
                "structural", "transmission", "housing", "support", "spring",
                "thermal", "corrosion", "assembly", "drafting", "prime"]
            _out = {
                "ok": False,
                "error": ("子代理【%s】%s：%s"
                          % (name,
                             "缺少 type 字段" if _miss else "的房间类型无法规范化",
                             _nz.get("error"))),
                "room": name, "raw_type": _raw_t,
                "allowed": _allowed,
                "example": {"name": name, "type": "structural",
                            "parts": ["DSH_某零件"],
                            "why": "该屋为什么存在",
                            "depends_on": []},
                "hint": ("每个房间必须显式写 type（白名单：%s）。"
                         "常见映射：drivetrain→transmission、sensor→housing、"
                         "drawing→drafting；总装用 assembly，出图用 drafting。"
                         "非建模房间也要列 parts（可写装配体名/图纸名作占位）。"
                         % " / ".join(_allowed)),
            }
            if _miss:
                _out["hint"] = ("【最常见原因】忘写 type。请给每个房间补一个："
                                + " / ".join(_allowed) + "。") + _out["hint"]
            return _out
        _item = {
            "name": name,
            "type": _nz["type"],
            "parts": [str(p).strip() for p in parts if str(p).strip()],
            "why": str(r.get("why") or "").strip(),
            "depends_on": [str(d).strip() for d in (r.get("depends_on") or []) if str(d).strip()],
        }
        if _nz.get("normalized"):
            _item["type_raw"] = str(r.get("type"))
            _item["type_normalized"] = True
        norm.append(_item)

    # ══ 【BUG-07 修复·自动补齐收尾房间】总装 + 出图 ══════════════════════
    # 实测（2026-10-30 四工位转台任务）：
    #   总计子代理按"功能分解"只登记了 3 个建模房间，没意识到门禁还要求
    #   【总装与验证】(assembly) 与【工程图输出】(drafting) 两个收尾房间。
    #   随后 workflow_gate.select 直接返回：
    #     {"gate":"CONFIG_MISSING_FINAL_WAVES","has_assembly":false,
    #      "has_drafting":false,...}
    #   任务就此卡死，必须回去让总计子代理"再补两个房间"——
    #   而它可能已经上下文耗尽（同一次任务里就发生了），只能重新 fork。
    #
    # 这两个房间不属于"设计决策"：它们由【流程本身】决定（任何 Mode 2 任务
    #   都必须装配 + 出图），和固定的【总理子代理】同一性质。
    #   让模型去记住这件事，属于把流程约束放在提示词里——正是本项目一直
    #   在消灭的模式。因此这里【代码级自动补齐】：
    #     · 已有同 type 房间 → 不重复添加（尊重总计子代理自己的命名）；
    #     · 没有 → 按标准名补一条，并标注 auto_added 便于溯源；
    #     · 补出来的房间 parts 写占位描述（非建模房间不需要零件清单）。
    _AUTO_FINAL_ROOMS = (
        ("总装与验证", "assembly",
         "全车装配 + 干涉检查 + 整体物理/领域验证（自动补齐）"),
        ("工程图输出", "drafting",
         "各零件三视图 + 装配图 + DWG/PDF 导出（自动补齐）"),
    )
    _auto_added = []
    for _nm, _tp, _why in _AUTO_FINAL_ROOMS:
        if any(r["type"] == _tp for r in norm):
            continue
        # 名称冲突（已有同名房间但类型不同）→ 换个后缀，避免依赖/绑定时撞名
        _use = _nm
        _exist = {r["name"] for r in norm}
        _i = 2
        while _use in _exist:
            _use = "%s%d" % (_nm, _i)
            _i += 1
        norm.append({
            "name": _use,
            "type": _tp,
            "parts": [],
            "why": _why,
            "depends_on": [],
            "auto_added": True,
        })
        _auto_added.append({"name": _use, "type": _tp, "why": _why})

    # 依赖必须指向存在的房间（防止总计子代理写出悬空依赖）
    _names = {r["name"] for r in norm}
    for r in norm:
        _bad = [d for d in r["depends_on"] if d not in _names]
        if _bad:
            return {"ok": False,
                    "error": ("子代理【%s】依赖了不存在的房间: %s"
                              % (r["name"], "、".join(_bad)))}

    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    # task_id 必须来自当前任务本身 —— 编制与任务绑定，
    # 否则 room-start 会因"编制不属于本任务"被误拦。
    # task_id 必须来自【权威状态目录】的 workflow_state ——
    # 不能 import workflow_gate（可能命中另一份 tools，读到另一个任务），
    # 也不能沿用 plan 里可能残留的旧 task_id（上一轮任务的编制不允许复用）。
    _tid = current_task_id(work_dir) or plan.get("task_id")
    plan["subagent_plan"] = {
        "decided_by": SUBPLAN_AUTHORITY,
        "task_id": _tid,
        "rationale": str(rationale).strip(),
        "count": len(norm),
        "rooms": norm,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # ══ 【Bug③ 修复】同步刷新 mode2_plan.json 的 rooms 快照 ══════════════
    # 原缺陷：set-subagent-plan 只写 subagent_plan，不碰 rooms 快照，
    #   于是 mode2_plan 里长期留着旧类型（drivetrain/sensor/drawing），
    #   而 workflow_gate.select 用的是编制里的新类型 →
    #   mode_gate.room-status / defense_gate.check-room 读到旧类型 →
    #   require_physics 被判 false → 物理/领域凭据被静默豁免。
    # 现在：落盘时把【规范化后的】[(房间名, 类型), ...] 原样写进
    #   subagent_config.rooms，与 workflow_state.subagent_config.rooms 同构。
    _snap = room_type_snapshot(norm)
    _norm_changed = [{"room": r["name"], "raw": r.get("type_raw"),
                      "type": r["type"]}
                     for r in norm if r.get("type_normalized")]
    plan["subagent_config"] = {
        "rooms": _snap,
        "total_rooms": len(_snap),
        "rooms_source": "总计子代理（mode2_flow.set-subagent-plan）",
        "rooms_rationale": str(rationale).strip(),
        "room_parts": {r["name"]: r.get("parts") or [] for r in norm},
        "room_types_normalized": _norm_changed,
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_from": "subagent_plan",
        "task_id": _tid,
    }
    save_plan(plan, work_dir)
    return {"ok": True, "count": len(norm), "rooms": norm,
            "decided_by": SUBPLAN_AUTHORITY,
            # 把同步结果回传给调用方（workflow_gate.select 会据此写 workflow_state）
            "rooms_snapshot": _snap,
            "room_types_normalized": _norm_changed,
            # 【BUG-07】自动补齐的收尾房间（null = 无需补齐，编制本来就全）
            "auto_added_final_rooms": (_auto_added or None),
            "plan_file": plan_path(work_dir),
            "note": ("编制已落盘，并已同步 rooms 快照到 plan 文件"
                     "（房间类型已规范化，三处门禁口径一致）；"
                     "建模小屋必须严格按此编制创建，数量不得固定。"
                     + (("【BUG-07】已自动补齐收尾房间：%s —— 它们由流程决定，"
                         "不需要总计子代理再登记。"
                         % "、".join(x["name"] for x in _auto_added))
                        if _auto_added else ""))}


def _read_state_json(name):
    """只读：从状态目录（与门禁同一份）读某个 json；读不到返回 (None, 原因)。

    刻意【不 import workflow_gate / mode_gate】：
      工程模式有"仓库 / 安装副本"两套 tools，import 可能命中另一套，
      于是读到另一个任务的状态 —— 这里与 _plan_dir 一样按 _store 权威目录直读。
    """
    try:
        _sd = None
        try:
            import _store as _st
            _sd = _st.state_dir()
        except Exception:
            _sd = None
        if not _sd:
            _sd = BASE_DIR
        _p = os.path.join(_sd, name)
        if not os.path.exists(_p):
            return None, "文件不存在: %s" % _p
        with open(_p, "r", encoding="utf-8") as _f:
            return json.load(_f) or {}, _p
    except Exception as e:
        return None, "读取失败: %r" % (e,)


def _rooms_as_pairs(raw):
    """把任意形态的 rooms 列表统一成 {房间名: 类型}（None 表示没有类型信息）。

    兼容三种历史写法：
      · [["结构件","structural"], ...]      ← workflow_state 现行写法
      · [{"name":"结构件","type":"structural"}, ...]
      · {"结构件": {...}}                    ← mode_state.rooms（只有房间状态，无类型）
    """
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            _t = None
            if isinstance(v, dict):
                _t = v.get("type") or v.get("room_type")
            out[str(k)] = (str(_t) if _t else None)
        return out
    for row in (raw or []):
        if isinstance(row, (list, tuple)) and len(row) >= 1:
            out[str(row[0])] = (str(row[1]) if len(row) >= 2 and row[1] else None)
        elif isinstance(row, dict):
            _n = row.get("name") or row.get("room")
            if _n:
                _t = row.get("type") or row.get("room_type")
                out[str(_n)] = (str(_t) if _t else None)
    return out


def sync_rooms_snapshot(rooms, work_dir=None, source="workflow_gate.select",
                        task_id=None):
    """【Bug③ 修复】把门禁侧的权威房间表回写进 mode2_plan.json 的 rooms 快照。

    为什么还需要"反向同步"：
      set-subagent-plan 写的是【编制】的快照（不含固定追加的总理子代理）；
      而 workflow_gate.select 会追加固定的【总理子代理】并可能重建配置。
      若只单向同步，plan 快照与 workflow_state 仍会差一个房间 → 三处一致性
      永远判不一致（假告警），真不一致反而被淹没。
      因此 select 落盘后调用本函数，把【最终生效的】房间表写回 plan 快照。

    Args:
        rooms: [(房间名, 类型), ...] 或 [{"name","type"}, ...]
        work_dir: 计划文件目录
        source: 写入来源（留痕，默认 workflow_gate.select）
        task_id: 归属任务；给出时同时校正 plan.task_id（避免跨任务污染）

    Returns: {"ok": bool, "rooms": [...], "changes": [...]}
    """
    pairs = []
    seen = set()
    for row in (rooms or []):
        if isinstance(row, (list, tuple)) and len(row) >= 1:
            _n, _t = str(row[0]), (row[1] if len(row) >= 2 else None)
        elif isinstance(row, dict):
            _n, _t = str(row.get("name") or ""), row.get("type")
        else:
            continue
        _n = _n.strip()
        if not _n or _n in seen:
            continue
        _nz = normalize_room_type(_t)
        if not _nz.get("ok"):
            return {"ok": False, "room": _n, "raw_type": _t,
                    "error": ("房间【%s】的类型 %r 无法规范化：%s"
                              % (_n, _t, _nz.get("error"))),
                    "allowed": _nz.get("allowed")}
        seen.add(_n)
        pairs.append([_n, _nz["type"]])
    if not pairs:
        return {"ok": False, "error": "rooms 为空 —— 拒绝把空房间表写进快照。"}

    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    cfg = plan.get("subagent_config") or {}
    old = [list(x) for x in (cfg.get("rooms") or []) if isinstance(x, (list, tuple))]
    changes = []
    _old_map = {x[0]: (x[1] if len(x) > 1 else None) for x in old}
    for n, t in pairs:
        if _old_map.get(n) != t:
            changes.append({"room": n, "from": _old_map.get(n), "to": t})
    for n in _old_map:
        if n not in {x[0] for x in pairs}:
            changes.append({"room": n, "from": _old_map[n], "to": None,
                            "note": "已从快照移除（不在最终房间表里）"})
    cfg.update({
        "rooms": pairs,
        "total_rooms": len(pairs),
        "rooms_source": (cfg.get("rooms_source")
                         or "总计子代理（mode2_flow.set-subagent-plan）"),
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_from": source,
    })
    if task_id:
        cfg["task_id"] = task_id
        plan["task_id"] = task_id
    plan["subagent_config"] = cfg
    save_plan(plan, work_dir)
    return {"ok": True, "rooms": pairs, "changes": changes,
            "path": plan_path(work_dir), "synced_from": source}


def room_types_report(work_dir=None):
    """【Bug③ 修复】三处 room_type 一致性【只读】自检。

    背景（实测）：同一个房间在三个工具里 room_type 各不相同 ——
      mode_gate.room-status → drivetrain（读 mode2_plan 的旧快照）
      defense_gate.check-room → sensor（读 workflow_state）
      workflow_gate.select → transmission（读本次编制）
    结果是传动房按 drivetrain 判 → require_physics=false → 真实 CREDIBLE 问题
    被静默豁免。

    本命令只读、不修数据，把四处来源并列出来 + 判定一致性 + 指出每个房间
    规范化后会走哪条门禁（require_physics 是否强制），供调用方定位差异。

    Returns:
        {"ok": 一致性通过, "consistent": bool, "sources": {...},
         "by_room": [...], "conflicts": [...], "warnings": [...]}
    """
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"], "checked": False}

    sp = plan.get("subagent_plan") or {}
    src_plan_rooms = _rooms_as_pairs(sp.get("rooms") or [])       # 编制本身
    src_snapshot = _rooms_as_pairs(
        (plan.get("subagent_config") or {}).get("rooms") or [])    # plan 快照
    _wf, _wf_note = _read_state_json("workflow_state.json")
    _ms, _ms_note = _read_state_json("mode_state.json")
    src_wf = _rooms_as_pairs(((_wf or {}).get("subagent_config") or {}).get("rooms") or [])
    src_ms = _rooms_as_pairs((_ms or {}).get("rooms") or {})       # 无类型，仅房间名
    # ── 任务隔离：workflow_state 若属于【另一个任务】，它的 rooms 与本编制无关 ──
    # 不隔离的话，上一轮残留的房间会被算进"三处一致性"，把正常状态误报成冲突。
    _wf_task = (_wf or {}).get("task_id")
    _plan_task = plan.get("task_id")
    _wf_foreign = bool(_wf_task and _plan_task and str(_wf_task) != str(_plan_task))
    if _wf_foreign:
        _wf_note = ("workflow_state 属于另一个任务（%s），本任务为 %s —— "
                    "不参与本次一致性比对。" % (_wf_task, _plan_task))
        src_wf = {}
    elif _wf is None and _wf_note:
        pass

    # 门禁实际会怎么判：defense_gate 读 workflow_state 的 rooms；
    # 波1 建模类类型（workflow_gate.WAVE1_TYPES / defense_gate.MODELING_ROOM_TYPES）。
    _wave1 = ("structural", "transmission", "housing", "support", "spring",
              "thermal", "corrosion")
    _finite = ("assembly", "drafting", "prime")

    # 参与比对的房间 = 编制 / 快照 / workflow_state 的并集。
    # mode_state.rooms 是【历史遗留台账】（可能还是上一轮任务的房间），
    # 只能作为"额外告警"列出，不能算进三处一致性 —— 否则会拿旧任务房间
    # 去比对本任务的编制，永远假报冲突。
    names = []
    for _d in (src_plan_rooms, src_snapshot, src_wf):
        for _n in _d:
            if _n not in names:
                names.append(_n)
    names.sort()
    _ms_only = sorted(n for n in src_ms if n not in names)

    by_room, conflicts, warnings = [], [], []
    for n in names:
        raw = {k: v.get(n) for k, v in (
            ("subagent_plan", src_plan_rooms),
            ("plan_snapshot", src_snapshot),
            ("workflow_state", src_wf),
            ("mode_state", src_ms))}
        # 规范化每个来源
        norm = {}
        bad = {}
        for k, v in raw.items():
            if v is None:
                norm[k] = None
                continue
            _nz = normalize_room_type(v)
            if _nz.get("ok"):
                norm[k] = _nz["type"]
            else:
                norm[k] = "<非法:%s>" % v
                bad[k] = _nz.get("error")
        _eff = None
        for k in ("subagent_plan", "plan_snapshot", "workflow_state"):
            if norm.get(k) and not str(norm[k]).startswith("<非法"):
                _eff = norm[k]
                break
        _distinct = sorted({v for v in norm.values()
                            if v and not str(v).startswith("<非法")})
        _ok = len(_distinct) <= 1 and not bad
        row = {"room": n, "raw": raw, "normalized": norm,
               "effective_type": _eff,
               "require_physics": bool(_eff in _wave1),
               "wave": ("wave1" if _eff in _wave1 else
                        ("final" if _eff in _finite else None)),
               "consistent": _ok}
        if bad:
            row["type_errors"] = bad
        if not _ok:
            conflicts.append({"room": n, "distinct_types": _distinct,
                              "raw": raw, "normalized": norm})
        if _eff and _eff not in CANONICAL_ROOM_TYPES:
            warnings.append("房间【%s】的类型 %r 不是规范类型。" % (n, _eff))
        by_room.append(row)

    # 快照 vs 编制：这是 Bug③ 的直接判据
    def _norm_or_none(v):
        return normalize_room_type(v).get("type") if v else None

    # 快照 vs 编制 —— Bug③ 的直接判据（含 raw，便于一眼看出旧类型）
    # 例外：固定的总理子代理只存在于快照（select 追加），编制里没有它属正常，
    #   不算冲突 —— 否则每次都会假告警，把真不一致淹没。
    _snap_diff, _snap_fixed_only = [], []
    for n in sorted(set(src_plan_rooms) | set(src_snapshot)):
        a, b = _norm_or_none(src_plan_rooms.get(n)), _norm_or_none(src_snapshot.get(n))
        if a == b:
            continue
        if a is None and b == PRIME_ROOM_TYPE:
            _snap_fixed_only.append(n)
            continue
        _snap_diff.append({"room": n,
                           "subagent_plan": a,
                           "subagent_plan_raw": src_plan_rooms.get(n),
                           "plan_snapshot": b,
                           "plan_snapshot_raw": src_snapshot.get(n)})
    if _snap_diff:
        conflicts.append({"scope": "plan_snapshot_vs_subagent_plan",
                          "detail": _snap_diff,
                          "fix": ("重新执行 set-subagent-plan 同步快照，"
                                  "或 workflow_gate.py select 重建配置。")})

    # workflow_state 侧要分两种情况 —— 混在一起会把"还没 select"误报成"不一致"：
    #   · 编制里有、workflow_state 还没有 → 正常时序（set-subagent-plan 在 select 之前），
    #     只做提示，不算冲突；
    #   · 两边都有但类型不同 → 真不一致，报警告（select 后应自动对齐）。
    _wf_diff, _wf_pending = [], []
    for n in sorted(set(src_plan_rooms) | set(src_wf)):
        a, b = _norm_or_none(src_plan_rooms.get(n)), _norm_or_none(src_wf.get(n))
        if a != b and b is None and a is not None:
            _wf_pending.append({"room": n, "subagent_plan": a})
        elif a != b:
            _wf_diff.append({"room": n, "subagent_plan": a,
                             "subagent_plan_raw": src_plan_rooms.get(n),
                             "workflow_state": b,
                             "workflow_state_raw": src_wf.get(n)})
    if _ms_only:
        warnings.append("mode_state.json 里有 %d 个本任务编制里没有的房间（%s）—— "
                        "属历史残留台账，未参与一致性比对；如确认无关可用 "
                        "mode_gate.py rooms-reset 清理。" % (len(_ms_only), "、".join(_ms_only)))
    if _wf_foreign:
        warnings.append(_wf_note)
    if _wf_pending:
        warnings.append("有 %d 个房间尚未写入 workflow_state.subagent_config.rooms —— "
                        "执行 workflow_gate.py select 后会自动同步。"
                        % len(_wf_pending))
    if _wf_diff:
        warnings.append("workflow_state.subagent_config.rooms 与编制不一致（%d 个房间）—— "
                        "请重新执行 workflow_gate.py select 重建配置。"
                        % len(_wf_diff))

    if not sp and not src_snapshot:
        return {"ok": False, "checked": True,
                "error": ("尚无子代理编制与 rooms 快照 —— 先由总计子代理登记编制"
                          "（set-subagent-plan 会同步写快照）。"),
                "sources": {"workflow_state_file": _wf_note,
                            "mode_state_file": _ms_note},
                "by_room": [], "conflicts": [], "warnings": warnings}

    return {"ok": not conflicts, "checked": True,
            "consistent": not conflicts,
            "plan_file": plan_path(work_dir),
            "sources": {
                "subagent_plan": src_plan_rooms,
                "plan_snapshot": src_snapshot,
                "workflow_state": src_wf,
                "mode_state_rooms": sorted(src_ms),
                "workflow_state_file": _wf_note,
                "mode_state_file": _ms_note,
            },
            "snapshot_synced": not _snap_diff,
            "snapshot_fixed_only_rooms": _snap_fixed_only,
            "workflow_state_synced": not _wf_diff,
            "workflow_state_pending": _wf_pending,
            "by_room": by_room,
            "conflicts": conflicts,
            "warnings": warnings,
            "normalized_types": [{"room": r["name"], "raw": r.get("type_raw"),
                                  "type": r["type"]}
                                 for r in (sp.get("rooms") or [])
                                 if r.get("type_normalized")],
            "wave1_types": list(_wave1),
            "note": ("三处 room_type 必须一致：不一致会让 defense_gate 用旧类型"
                     "判 require_physics（drivetrain/sensor 直接被豁免校核）。")}


def get_subagent_plan(work_dir=None):
    """读回子代理编制，并附带【阶段新鲜度】结论（只读，不改变 ok 语义）。

    ── 【Bug② 修复】为什么这里也要带 stale 信息 ────────────────────────
    编制是在"十阶段分析完成"时登记的。但用户【之后】仍可能改答题目 →
    阶段产物过期 → 编制所依据的结论已失效。此时若只看 ok=True 就开房间，
    等于拿着过期结论去建模。
    本函数把 stages_stale / stages_ready / stale_hint 一并给出，让 mode_gate /
    workflow_gate 的调用方（以及人）一眼看到"编制还成立吗"；真正的拦截由
    require_stages_complete（set-subagent-plan 的强制前置）负责。
    """
    plan = load_plan(work_dir)
    sp = plan.get("subagent_plan")
    if not sp:
        return {"ok": False,
                "error": ("尚无子代理编制 —— 必须先由【总计子代理】调 "
                          "set-subagent-plan 登记，禁止使用固定房间表。")}
    out = {"ok": True, **sp}
    try:
        st = stage_status(work_dir)
        _stale = st.get("stale") or []
        _awaiting = st.get("awaiting_answer") or []
        out.update({
            "stages_ready": bool(st.get("complete")),
            "stages_stale": _stale,
            "stages_awaiting_answer": _awaiting,
            "stale_hint": (("编制所依据的十阶段产物已过期（%s）—— "
                            "必须 refresh-stage 重做后才能据此开工。"
                            % "、".join(_stale)) if _stale else None),
        })
    except Exception as _e:
        # 阶段信息读不出来时不得假装"没问题"
        out["stages_ready"] = None
        out["stages_status_error"] = repr(_e)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 八、★ 总理子代理：固定存在 + 上报契约
# ═══════════════════════════════════════════════════════════════════════════
PRIME_ROOM_NAME = "总理子代理"
PRIME_ROOM_TYPE = "prime"

#: 建模小屋向总理子代理上报时【必须】携带的字段。
#: 用户原话：「都要给总理指代理发三道防线验证的文件链接，尺寸，名字，
#:          上下左右等会直接接触的零件配合的零件。」
REQUIRED_REPORT_FIELDS = (
    "part_name",     # 名字
    "room",          # 哪个小屋上报的
    "file",          # 零件文件路径（链接）
    "dims",          # 尺寸
    "credentials",   # 三道防线凭据（材料/物理/领域）
    "adjacent",      # 上下左右等直接接触/配合的零件
)

#: 三道防线凭据的三条线（缺一条即不合格）
REQUIRED_CREDENTIAL_KEYS = ("material", "physics", "domain")


def prime_room_definition(work_dir=None):
    """总理子代理的房间定义（固定存在，不参与"数量由总计子代理决定"）。"""
    return {
        "name": PRIME_ROOM_NAME,
        "type": PRIME_ROOM_TYPE,
        "fixed": True,
        "why": ("【固定角色】统一规划尺寸、校验相邻零件尺寸一致性、"
                "汇总全部零件数据为 docx 交付；不一致时升级父代理干涉。"),
        "duties": [
            "① 统一规划关键尺寸（给各建模小屋下发同一套尺寸口径）",
            "② 接收各建模小屋上报：三道防线凭据链接 / 尺寸 / 名称 / 相邻配合零件",
            "③ 校验相邻零件尺寸是否一致（调 part_registry.check_adjacency_consistency）",
            "④ 发现不一致 → 标记 escalate_to_parent=true，交父代理做干涉处理",
            "⑤ 持久维护零件数据台账（part_registry），支持按零件反查关联",
            "⑥ 全部完成后汇总导出 docx，交主对话",
            # ── 【2026-10-28 重构】用户要求："总理子代理要一直盯着其他子代理"──
            "⑦ 【监工】建模期间每轮读 mode_gate.py room-board --json，盯住每个建模小屋的 state："
            "running/waiting_sw/idle 都算活着（严禁判死）；unbound 让父代理补 subagent-assign；"
            "dead 才上报父代理走 recover；done 则核对它的 prime-report 是否齐全。",
            "⑧ 【收件缺口】用 mode2_flow.py prime-board 看还有哪些零件没上报，"
            "缺口必须催办，不能等到 room-end 才发现。",
        ],
        "spawn_timing": ("与 wave1 全部建模小屋【同一条回复内同时 fork】（不得等建模完成后再开）；"
                         "长生命周期，不阻塞波次推进；wave3 出图完成后 room-end。"),
        "watch_commands": [
            "python \"<根>\\tools\\mode_gate.py\" room-board --json",
            "python \"<根>\\tools\\mode2_flow.py\" prime-board --json",
        ],
    }


def record_prime_report(report, work_dir=None):
    """建模小屋 → 总理子代理 的上报登记。**缺字段即拒绝。**

    这是"三道防线凭据 + 尺寸 + 名字 + 相邻配合零件必须上报"的强制点：
    任何一个字段缺失，本函数返回 ok=False，房间无法完成上报闭环。
    """
    if not isinstance(report, dict):
        return {"ok": False, "error": "上报必须是对象"}
    # ── 【语义修正】必填校验必须区分"字段缺失"与"字段为空值" ──────────────
    # 坑：`adjacent` 的合法取值【包含空数组】—— 它表示"该零件确实不与任何
    #   零件接触"。若用 `not report.get(k)` 一把梭，空数组会被当成"缺字段"，
    #   于是"独立件"永远上报不了，流程被误锁死。
    # 现在：adjacent 只要求"键存在且是 list"（空 list 合法）；
    #   其余字段要求"非空"。
    missing = []
    for k in REQUIRED_REPORT_FIELDS:
        if k == "adjacent":
            if k not in report:
                missing.append(k)
            continue
        if not report.get(k):
            missing.append(k)
    if missing:
        return {"ok": False,
                "error": ("【上报不合格】缺少必填字段：%s。"
                          "总理子代理需要：名字 / 尺寸 / 三道防线凭据链接 / "
                          "直接接触配合的零件。" % "、".join(missing)),
                "required": list(REQUIRED_REPORT_FIELDS),
                "got": sorted(report.keys())}

    # 三道防线凭据必须三条线齐全（允许值为"未产出"，但要显式说明，不能凭空缺）
    cred = report.get("credentials") or {}
    if not isinstance(cred, dict):
        return {"ok": False, "error": "credentials 必须是对象（含 material/physics/domain）"}
    _miss_cred = [k for k in REQUIRED_CREDENTIAL_KEYS if k not in cred]
    if _miss_cred:
        return {"ok": False,
                "error": ("【三道防线凭据不全】缺少：%s。"
                          "三条线（材料/物理/领域）都必须给出链接或明确标注未产出。"
                          % "、".join(_miss_cred)),
                "required": list(REQUIRED_CREDENTIAL_KEYS)}

    # 尺寸必须有内容
    dims = report.get("dims")
    if not dims:
        return {"ok": False, "error": "dims（尺寸）为空 —— 上报必须带尺寸。"}
    if isinstance(dims, dict) and not dims:
        return {"ok": False, "error": "dims 是空对象 —— 上报必须带具体尺寸。"}

    # 相邻零件必须是数组（可以为空数组，表示"确实不与任何零件接触"，
    # 但要显式给出；缺失已在上面被 missing 拦掉）
    adj = report.get("adjacent")
    if not isinstance(adj, list):
        return {"ok": False, "error": "adjacent 必须是数组（无接触件时写 []）"}

    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    rec = {
        "part_name": str(report.get("part_name")).strip(),
        "room": str(report.get("room")).strip(),
        "file": str(report.get("file")).strip(),
        "dims": dims,
        "material": report.get("material"),
        "credentials": {k: cred.get(k) for k in REQUIRED_CREDENTIAL_KEYS},
        "adjacent": [str(a).strip() for a in adj if str(a).strip()],
        "interfaces": report.get("interfaces") or [],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }
    # 同一零件重复上报 → 覆盖（保留最新），保证总理看到的是终态
    lst = [x for x in plan.get("prime_reports", [])
           if not (isinstance(x, dict) and x.get("part_name") == rec["part_name"])]
    lst.append(rec)
    plan["prime_reports"] = lst
    save_plan(plan, work_dir)
    return {"ok": True, "part_name": rec["part_name"],
            "total_reports": len(lst),
            "note": "已登记到总理子代理台账。"}


def prime_reports(work_dir=None):
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    reps = plan.get("prime_reports", [])
    return {"ok": True, "count": len(reps), "reports": reps}

def prime_board(work_dir=None):
    """【2026-10-28 重构】总理子代理的监工看板（只读）。

    用户要求："总理子代理要一直盯着第二的其他子代理，去干活"。
    本命令把"该盯什么"变成一份可直接判断的清单：
      · rooms：每个建模小屋的实时 state（来自 mode_gate.room-board，权威）
      · report_gaps：编制里列了零件、但还没 prime-report 的零件
      · consistency：最近一次相邻尺寸一致性结论
      · actions：总理现在就该做的事（催报 / 等锁 / 报死 / 收尾）
    """
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    sp = plan.get("subagent_plan") or {}
    rooms = sp.get("rooms") or []
    reports = plan.get("prime_reports") or []
    reported = {str(r.get("part_name") or "").strip() for r in reports}
    expected = []
    for r in rooms:
        for p in (r.get("parts") or []):
            p = str(p).strip()
            if p and p not in expected:
                expected.append(p)
    gaps = [p for p in expected if p not in reported]

    # 房间实时状态：委托 mode_gate 的权威看板（拿不到就显式降级，不猜）
    live = None
    live_error = None
    try:
        import mode_gate as _mg
        if hasattr(_mg, "cmd_room_board"):
            live = _mg.cmd_room_board(as_json=True)
        else:
            live_error = "mode_gate.cmd_room_board 不存在（版本不一致）"
    except Exception as e:
        live_error = repr(e)

    actions = []
    if live and isinstance(live, dict):
        for room in (live.get("rooms") or []):
            st = str(room.get("state") or "")
            nm = room.get("room")
            if st in ("waiting_sw", "running", "idle", "starting"):
                continue
            if st == "unbound":
                actions.append({"room": nm, "action": "父代理补 subagent-assign",
                                "why": "平台侧存活但未绑定，禁止 reset/restart"})
            elif st == "dead":
                actions.append({"room": nm, "action": "上报父代理走 recover/restart",
                                "why": "平台确认已停止且无近期产物"})
            elif st == "failed":
                actions.append({"room": nm, "action": "待父代理重开该房间", "why": "房间已 fail"})
    if gaps:
        actions.append({"room": "*", "action": "催办未上报零件", "parts": gaps[:20],
                        "why": "编制里有、总理台账没有"})
    return {
        "ok": True,
        "prime_room": PRIME_ROOM_NAME,
        "rooms": (live or {}).get("rooms") or [],
        "live_error": live_error,
        "expected_parts": expected,
        "reported_parts": sorted(p for p in reported if p),
        "report_gaps": gaps,
        "consistency": plan.get("prime_consistency"),
        "actions": actions,
        "hint": ("state=waiting_sw/running/idle/starting 都是活着，严禁判死；"
                 "只有 dead 才允许 recover/restart。"),
    }



# ═══════════════════════════════════════════════════════════════════════════
# 九、★ 相邻尺寸一致性 → 不一致必须升级父代理
# ═══════════════════════════════════════════════════════════════════════════
def check_prime_consistency(work_dir=None, registry_work_dir=None):
    """总理子代理的一致性校验入口。

    优先委托 part_registry.check_adjacency_consistency（几何/接口级比对），
    并把结果**固化到计划文件**，供交付前总闸复查。

    ★ 强制点：只要存在 mismatch，就返回 ok=False 且
      `escalate_to_parent=True` —— 不允许"发现了不一致还继续往下走"。
    """
    out = {"ok": False, "checked_pairs": 0, "mismatches": [],
           "escalate_to_parent": False, "source": None}

    reg = None
    try:
        import part_registry as _pr
        reg = _pr
    except Exception as e:
        out["error"] = ("part_registry 不可用，无法做相邻尺寸一致性校验: %r。"
                        "按 fail-closed 处理，视为【未评估】。" % (e,))
        out["evaluated"] = False
        return out

    try:
        res = reg.check_adjacency_consistency(work_dir=registry_work_dir or work_dir)
    except TypeError:
        # 兼容旧签名（无 work_dir 关键字）
        try:
            res = reg.check_adjacency_consistency()
        except Exception as e:
            out["error"] = "一致性校验抛异常: %r" % (e,)
            out["evaluated"] = False
            return out
    except Exception as e:
        out["error"] = "一致性校验抛异常: %r" % (e,)
        out["evaluated"] = False
        return out

    out["source"] = "part_registry.check_adjacency_consistency"
    out["checked_pairs"] = res.get("checked_pairs", 0)
    out["mismatches"] = res.get("mismatches", []) or []
    out["evaluated"] = bool(res.get("evaluated", True))
    out["summary"] = res.get("summary")

    if not out["evaluated"]:
        # 没数据可查 ≠ 通过。保持 ok=False 且不给 escalate（不是"不一致"，是"查不了"）
        out["error"] = ("无法评估相邻尺寸一致性（缺少邻接或接口数据）—— "
                        "不得当作通过。请先补齐零件台账/接口信息。")
        _persist_consistency(out, work_dir)
        return out

    if out["mismatches"]:
        out["ok"] = False
        out["escalate_to_parent"] = True
        out["error"] = ("【相邻零件尺寸不一致】共 %d 处，必须交父代理做干涉处理，"
                        "不得继续进入总装/交付。" % len(out["mismatches"]))
    else:
        out["ok"] = True
        out["summary"] = out.get("summary") or ("共检查 %d 对相邻零件，尺寸一致。"
                                                % out["checked_pairs"])
    _persist_consistency(out, work_dir)
    return out


def _persist_consistency(out, work_dir):
    try:
        plan = load_plan(work_dir)
        if plan.get("_load_error"):
            return
        plan["prime_consistency"] = dict(out, at=time.strftime("%Y-%m-%d %H:%M:%S"))
        save_plan(plan, work_dir)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 九·补充、★ 总理子代理【统一核实】闸门（用户新增要求）
# ═══════════════════════════════════════════════════════════════════════════
# 用户原话：
#   「总理子代理要统一核实过数据没有错误了，才能结束」
#   「其总理子代理也要核实的相关数据是否一致，必须要保证真实」
#
# 关键点：**"核实"不能是自我声明**。若只让总理子代理调用一句
#   `confirm(verified=True)`，那谁都能写 True —— 等于没有核实。
#
# 因此本闸门【自己从磁盘重算】，不采信任何调用方传入的"我已核实"：
#   ① 真读台账文件（part_registry.json），算出每个零件与其邻接件的尺寸一致性；
#   ② 用规范化 JSON 的 SHA-256 作为"当时到底核对了哪些数据"的指纹；
#   ③ 与 part_registry 的 audit_verification 交叉验证 —— 证明数据没被绕过模块手改；
#   ④ 只要还有 mismatch / open 的 unresolved / 指纹失配 → **拒绝结束**。
#
# 「才能结束」的落地：pre_delivery_check 会把本闸门作为**硬性前置**，
#   未通过则 workflow_gate._archive_finished 拒绝归档（拒绝宣告完成）。

#: 核实结论的落盘键（写在计划文件里，便于审计）
PRIME_VERIFY_KEY = "prime_verification"


def _sha256_json(obj):
    """稳定的 SHA-256（键排序、紧凑分隔符）——同数据必得同指纹。"""
    import hashlib
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def prime_verify_all(work_dir=None, registry_work_dir=None, verified_by=None):
    """★ 总理子代理统一核实：**从磁盘重算**，不采信自我声明。

    做四件事（任一不过 → ok=False，禁止结束）：
      1. 读真实台账，做相邻零件尺寸一致性比对；
      2. 检查 unresolved 台账里是否还有 open 的已知不一致；
      3. 用 part_registry.audit_verification 验证数据未被绕过模块手改；
      4. 用当前磁盘数据算指纹并落盘（供事后重算复现）。

    Args:
        work_dir: 模式二计划文件目录
        registry_work_dir: 零件台账目录（默认同 work_dir）
        verified_by: 核实者身份；默认 PRIME_ROOM_NAME（"总理子代理"）

    Returns:
        {"ok","verified","verified_by","verified_at","digest",
         "parts_count","checked_pairs","mismatches","open_unresolved",
         "tampered","blockers", ...}
    """
    _by = str(verified_by or PRIME_ROOM_NAME).strip() or PRIME_ROOM_NAME
    out = {
        "ok": False, "verified": False, "verified_by": _by,
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "digest": None, "parts_count": 0, "checked_pairs": 0,
        "mismatches": [], "open_unresolved": [], "tampered": [],
        "blockers": [], "enforced_by": "mode2_flow.prime_verify_all",
    }
    _reg_wd = registry_work_dir or work_dir

    # ── 载入台账模块（不可用则 fail-closed，不得当作"已核实"）────────────
    reg = None
    try:
        import part_registry as _pr
        reg = _pr
    except Exception as e:
        out["blockers"].append(
            "part_registry 不可用（%r）—— 无法核实数据真实性，按 fail-closed "
            "视为【未核实】。" % (e,))
        out["error"] = out["blockers"][0]
        _persist_verify(out, work_dir)
        return out

    # ── ① 真读磁盘：零件总数 ─────────────────────────────────────────────
    parts = {}
    try:
        lp = reg.list_parts(work_dir=_reg_wd)
        if isinstance(lp, dict) and lp.get("ok"):
            _items = lp.get("parts") or lp.get("items") or []
            if isinstance(_items, dict):
                parts = _items
            else:
                for _p in _items:
                    if isinstance(_p, dict) and _p.get("name"):
                        parts[_p["name"]] = _p
    except Exception as e:
        out["blockers"].append("读取零件台账失败：%r" % (e,))
    out["parts_count"] = len(parts)
    if not parts:
        out["blockers"].append(
            "零件台账为空 —— 没有任何可核实的数据（"
            "总理子代理必须先收到各小屋上报并落账）。")

    # ── ② 真读磁盘：相邻尺寸一致性 ───────────────────────────────────────
    try:
        cons = reg.check_adjacency_consistency(work_dir=_reg_wd)
    except Exception as e:
        cons = {"ok": False, "evaluated": False,
                "error": "一致性校验异常：%r" % (e,)}
    out["checked_pairs"] = cons.get("checked_pairs", 0)
    out["mismatches"] = cons.get("mismatches") or []
    out["consistency_evaluated"] = bool(cons.get("evaluated", True))
    if out["mismatches"]:
        out["blockers"].append(
            "存在 %d 处相邻零件尺寸不一致，必须处理（改一致或用 "
            "allow_mismatch + 理由显式放行并记入 unresolved）："
            % len(out["mismatches"]))
        for _m in out["mismatches"][:5]:
            _d = _m.get("detail") if isinstance(_m, dict) else str(_m)
            out["blockers"].append("    · %s" % _d)
    elif not out["consistency_evaluated"]:
        # 没数据可查 ≠ 通过（与全仓 fail-closed 口径一致）
        out["blockers"].append(
            "相邻尺寸一致性【未评估】（缺邻接或接口数据）—— 不得当作通过。"
            " 请补齐各零件的 adjacent 与 interfaces（nominal_mm）。")

    # ── ③ unresolved：还有 open 的已知不一致 → 不许结束 ──────────────────
    try:
        ul = reg.list_unresolved(work_dir=_reg_wd)
        if isinstance(ul, dict) and ul.get("ok"):
            out["open_unresolved"] = ul.get("items") or []
    except Exception as e:
        out["blockers"].append("读取 unresolved 台账失败：%r" % (e,))
    if out["open_unresolved"]:
        out["blockers"].append(
            "unresolved 台账仍有 %d 处【未决】的已知不一致 —— "
            "必须改一致，或显式 resolve-unresolved 并写明理由。"
            % len(out["open_unresolved"]))

    # ── ④ 防篡改：数据是否被绕过模块手改 ─────────────────────────────────
    # ⚠️ 字段名以 part_registry 的**实际返回**为准（实测）：
    #    parts_with_fingerprint / intact_count / tampered / parts_without_fingerprint，
    #    而不是早期设计的 checked / intact / orphans。用错字段会导致
    #    "明明有指纹却报没有指纹"的假阴性（实测踩到）。
    # ⚠️ 特别注意：`orphans` 是 part_registry【函数内部】的局部变量名，
    #    **不是**返回体的键；返回体的对应键是 `parts_without_fingerprint`。
    #    写 av.get("orphans") 会静默拿到 None（审计脚本帮我抓到了这一处）。
    try:
        av = reg.audit_verification(work_dir=_reg_wd)
        if isinstance(av, dict) and av.get("ok"):
            out["tampered"] = av.get("tampered") or []
            out["audit_checked"] = av.get("parts_with_fingerprint", 0)
            out["audit_intact"] = av.get("intact_count", 0)
            # 容错读：新键优先，旧/别名键兜底，两者都无则空表（不静默当"没问题"）
            out["audit_orphans"] = (av.get("parts_without_fingerprint")
                                    or av.get("records_without_digest")
                                    or [])
            if out["tampered"]:
                out["blockers"].append(
                    "★ 检出 %d 处数据在核实之后被改动（疑似绕过模块直接编辑 "
                    "JSON）—— 真实性无法保证，必须重新走模块登记。"
                    % len(out["tampered"]))
                for _t in out["tampered"][:5]:
                    _nm = _t.get("part") if isinstance(_t, dict) else str(_t)
                    out["blockers"].append(
                        "    · %s（记录指纹 %s… 现算 %s…）"
                        % (_nm,
                           str((_t or {}).get("digest"))[:10] if isinstance(_t, dict) else "",
                           str((_t or {}).get("digest_now"))[:10] if isinstance(_t, dict) else ""))
            elif not out["audit_checked"]:
                out["blockers"].append(
                    "台账里没有任何核实指纹 —— 无法证明数据真实性。"
                    "请通过 part_registry 正常路径登记（会自动留下指纹）。")
    except Exception as e:
        out["blockers"].append("审计核实指纹失败：%r" % (e,))

    # ── ⑤ 指纹落盘（可事后重算复现）─────────────────────────────────────
    _payload = {
        "parts": parts,
        "mismatches": out["mismatches"],
        "open_unresolved": out["open_unresolved"],
        "registry": getattr(reg, "registry_path", lambda **k: None)(work_dir=_reg_wd)
        if hasattr(reg, "registry_path") else None,
    }
    out["digest"] = _sha256_json(_payload)
    out["verified"] = not out["blockers"]
    out["ok"] = out["verified"]
    if out["verified"]:
        out["summary"] = (
            "已核实：%d 个零件、%d 对邻接、无尺寸不一致、无未决记录、"
            "指纹 %s…（%s）"
            % (out["parts_count"], out["checked_pairs"], out["digest"][:12], _by))
    else:
        out["error"] = ("【总理子代理未通过统一核实】%d 项问题，禁止结束任务。"
                        % len(out["blockers"]))
    _persist_verify(out, work_dir)
    return out


def _persist_verify(out, work_dir):
    """把核实结论固化到计划文件（供交付总闸与事后审计复查）。"""
    try:
        plan = load_plan(work_dir)
        if plan.get("_load_error"):
            return
        plan[PRIME_VERIFY_KEY] = dict(out, at=time.strftime("%Y-%m-%d %H:%M:%S"))
        save_plan(plan, work_dir)
    except Exception:
        pass


def prime_verify_status(work_dir=None):
    """读回最近一次核实结论（不重算）。"""
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    rec = plan.get(PRIME_VERIFY_KEY)
    if not rec:
        return {"ok": False,
                "error": ("尚无核实记录 —— 总理子代理必须统一核实数据后才能结束"
                          "（调 prime-verify）。")}
    return {"ok": True, **rec}


# ═══════════════════════════════════════════════════════════════════════════
# 十、交付前总闸
# ═══════════════════════════════════════════════════════════════════════════
def pre_delivery_check(work_dir=None, registry_work_dir=None, require_primes=True):
    """宣告任务完成之前的**强制总闸**。任一不满足 → ok=False，禁止交付。

    检查项：
      ① 十阶段是否都留下产出；
      ② 提问数是否达到该深度的下界（强 ≥10 / 弱 ≥5）；
      ③ 子代理编制是否存在且由总计子代理决定；
      ④ 总理子代理是否存在；
      ⑤ 报告数量是否 ≥ 编制里的零件数（每个零件都要上报）；
      ⑥ 相邻尺寸一致性是否通过（不一致必须已升级处理）；
      ⑦ ★【总理子代理统一核实】是否已通过（用户要求"核实过数据没有错误
         了，才能结束"）—— 见 prime_verify_all，**从磁盘重算**而非自我声明。
    """
    blockers = []
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "blockers": [plan["_load_error"]], "checked": False}

    st = stage_status(work_dir)
    if not st.get("complete"):
        # 【Bug② 修复】不能只说"缺少产出" —— stale 阶段的 missing 是空的，
        # 那样会给出"十阶段未走完，缺少产出："这种空信息，排障时无从下手。
        _why = []
        if st.get("missing"):
            _why.append("缺少产出：%s" % "、".join(st.get("missing")))
        if st.get("stale"):
            _why.append("产物过期（用户答复后未重算，必须 refresh-stage）：%s"
                        % "、".join(st.get("stale")))
        if st.get("awaiting_answer"):
            _why.append("产物依赖未答题目：%s" % "、".join(st.get("awaiting_answer")))
        blockers.append("十阶段未达到可交付状态 —— " +
                        ("；".join(_why) if _why else "十阶段未走完"))

    qr = question_report(work_dir, final=True)
    if not qr.get("ok"):
        blockers.append(qr.get("error") or "提问数量不达标")

    # ── 【2026-10-28 重构】总计子代理必须是真实的（analyst-bind）──
    _an = plan.get("analyst") or {}
    if not str(_an.get("session_id") or "").strip():
        blockers.append("总计子代理从未真实创建/绑定（analyst-bind 未执行）——\n"
                        "十阶段与子代理编制必须由它完成，主对话自写 --decided-by 不算。")

    sp = plan.get("subagent_plan")
    if not sp:
        blockers.append("缺少子代理编制（必须由总计子代理决定，禁止固定房间表）")
    elif sp.get("decided_by") != SUBPLAN_AUTHORITY:
        blockers.append("子代理编制不是由【%s】决定的（实际 %r）"
                        % (SUBPLAN_AUTHORITY, sp.get("decided_by")))

    reports = plan.get("prime_reports", [])
    if require_primes:
        if not reports:
            blockers.append("总理子代理没有任何零件上报（三道防线凭据/尺寸/相邻件未上报）")
        else:
            # 每个零件都要上报：编制里列出的零件名应能在上报里找到
            want = set()
            for r in (sp or {}).get("rooms", []):
                for p in r.get("parts", []):
                    want.add(p)
            got = {r.get("part_name") for r in reports}
            missing = sorted(want - got)
            if missing:
                blockers.append("以下零件未向总理子代理上报：%s" % "、".join(missing))

    cons = plan.get("prime_consistency")
    if cons is None:
        blockers.append("未做相邻零件尺寸一致性校验（总理子代理必须执行）")
    elif cons.get("mismatches"):
        blockers.append("存在未处理的相邻尺寸不一致 %d 处（必须由父代理干涉处理）"
                        % len(cons["mismatches"]))
    elif not cons.get("evaluated", True):
        blockers.append("相邻尺寸一致性【未评估】（缺数据），不得当作通过")

    # ── ⑦ ★ 总理子代理统一核实（用户新增要求："核实过数据没有错误了，才能结束"）──
    # 这里**重算**一遍（不采信之前落盘的结论），确保结束时的数据就是被核实的数据。
    # 若磁盘数据在核实之后被改过，重算会立刻发现并拒绝结束。
    _pv = None
    if require_primes:
        try:
            _pv = prime_verify_all(work_dir=work_dir,
                                   registry_work_dir=registry_work_dir)
        except Exception as e:
            blockers.append("总理子代理统一核实执行异常：%r（按 fail-closed 拒绝结束）"
                            % (e,))
        if _pv is not None and not _pv.get("ok"):
            blockers.append("★ 总理子代理【尚未通过统一核实】—— 数据可能有误，禁止结束任务：")
            for _b in (_pv.get("blockers") or [])[:8]:
                blockers.append("    · %s" % _b)

    # ── ⑧ ★ 总理子代理必须汇总出 docx 交付物 ─────────────────────────────
    # 用户原话（需求2）：「最终汇总为 docx 等文档在最后结束的时候，再发给主对话」。
    #
    # 【为什么补这一条】原先 `part_registry.export_docx` 虽然有实现（三条降级路径
    # 都验证过），却**没有任何地方强制它被调用** —— 与用户"所有改动都要写在代码里
    # 强制执行"的要求不符：不导出 docx 也能宣告完成。
    # 现在：交付前总闸会**核实磁盘上真的存在一份由本任务台账导出的 docx**
    # （存在 + 非空 + 是合法 zip 且含 word/document.xml），否则拒绝结束。
    _docx = None
    if require_primes:
        _docx = _check_docx_deliverable(work_dir=work_dir,
                                        registry_work_dir=registry_work_dir)
        if not _docx.get("ok"):
            blockers.append("★ 缺少总理子代理的 docx 汇总交付物 —— 禁止结束任务：")
            blockers.append("    · %s" % (_docx.get("error") or "未找到 docx"))
            blockers.append("    · 导出方式："
                            "python part_registry.py export-docx --out \"<交付目录>/零件台账.docx\""
                            " --work-dir \"<交付目录>\"")

    return {"ok": not blockers, "blockers": blockers, "checked": True,
            "stages": {"done": st.get("done_count"), "total": st.get("total")},
            "questions": {"asked": qr.get("asked"), "min": qr.get("min"),
                          "max": qr.get("max")},
            "subagents": ((sp or {}).get("count")),
            "prime_reports": len(reports),
            "prime_verification": (None if _pv is None else {
                "verified": _pv.get("verified"),
                "verified_by": _pv.get("verified_by"),
                "digest": _pv.get("digest"),
                "parts_count": _pv.get("parts_count"),
                "checked_pairs": _pv.get("checked_pairs"),
            }),
            "docx_deliverable": _docx}


def _check_docx_deliverable(work_dir=None, registry_work_dir=None):
    """核实"总理子代理的 docx 汇总"真的存在且合法。

    判定（缺一不可）：
      ① 在交付目录（及台账目录）下能找到 **.docx**；
      ② 文件非空（> MIN_DOCX_BYTES）；
      ③ 是**合法 zip**（docx 本质是 zip）且**含 word/document.xml** ——
         这一条能挡住"随便写个空文件改名成 .docx"的假交付。

    为什么在校验里做 zip 检查而不是只看扩展名：
      只看扩展名等于"改名即通过"，与全仓 fail-closed 的口径不符。
    """
    MIN_DOCX_BYTES = 1024   # 真 docx 至少有 1KB；纯 stdlib 降级版约 5KB
    out = {"ok": False, "path": None, "size_bytes": 0, "candidates": [],
           "checked_dirs": [], "error": None}
    _roots = []
    for _d in (work_dir, registry_work_dir):
        if _d:
            _roots.append(os.path.abspath(str(_d)))
    # ══ ★【实测缺陷修复 · 2026-10-13】先认【导出时记录的路径】══════════════
    # 现象：`export-docx --out <任意路径>` 成功后，`check-docx` 却报
    #   "在 <work_dir>、<load_cases> 下未找到任何 .docx" → 交付总闸误拦。
    # 根因：本函数只在【固定目录】里 walk，而总理子代理完全可以把 docx
    #   导出到别处（--out 是允许的），于是"真导出了却查不到"。
    # 修法：把 export_docx_and_record 记录在计划文件里的路径优先纳入检查，
    #   但仍**自己重查磁盘**（不采信记录本身），保持 fail-closed 语义不变。
    _recorded = None
    try:
        _plan = load_plan(work_dir)
        _de = (_plan or {}).get("docx_export") or {}
        _rp = _de.get("path")
        if _rp and os.path.isfile(str(_rp)):
            _recorded = os.path.abspath(str(_rp))
    except Exception:
        _recorded = None
    if _recorded:
        _roots.insert(0, os.path.dirname(_recorded))
        out["recorded_path"] = _recorded
    try:
        import part_registry as _pr
        try:
            _p = _pr.registry_path(work_dir=registry_work_dir or work_dir)
            if _p:
                _roots.append(os.path.dirname(os.path.abspath(_p)))
        except Exception:
            pass
    except Exception:
        pass
    # 去重保序
    _seen = set()
    _roots = [r for r in _roots
              if not (os.path.normcase(r) in _seen or _seen.add(os.path.normcase(r)))]
    out["checked_dirs"] = _roots

    _cands = []
    # ★ 记录路径本身作为第一候选（已 isfile，且随后仍要过 zip 校验）
    if _recorded:
        try:
            _cands.append((os.path.getmtime(_recorded), _recorded))
        except Exception:
            pass
    for _r in _roots:
        if not os.path.isdir(_r):
            continue
        try:
            for _dirpath, _dirs, _files in os.walk(_r):
                # 跳过明显的无关目录，避免把测试产物当交付物
                if any(_s in _dirpath for _s in ("__pycache__", ".git")):
                    continue
                for _f in _files:
                    if _f.lower().endswith(".docx"):
                        _fp = os.path.join(_dirpath, _f)
                        try:
                            _cands.append((os.path.getmtime(_fp), _fp))
                        except Exception:
                            _cands.append((0.0, _fp))
        except Exception:
            continue
    _cands.sort(reverse=True)
    out["candidates"] = [_p for _m, _p in _cands][:20]
    if not _cands:
        out["error"] = ("在 %s 下未找到任何 .docx —— 总理子代理必须汇总导出。"
                        % "、".join(_roots) or "交付目录")
        return out

    # 逐个候选校验，取第一个合法的（最新的优先）
    for _m, _fp in _cands:
        try:
            _sz = os.path.getsize(_fp)
        except Exception:
            continue
        if _sz < MIN_DOCX_BYTES:
            out["error"] = ("找到的 docx 过小（%s，%d 字节 < %d）—— 疑似空文件。"
                            % (os.path.basename(_fp), _sz, MIN_DOCX_BYTES))
            continue
        # ★ 必须是真的 zip 且含 word/document.xml
        try:
            import zipfile
            with zipfile.ZipFile(_fp) as _z:
                _names = _z.namelist()
            if "word/document.xml" not in _names:
                out["error"] = ("%s 不是合法 docx（zip 内缺少 word/document.xml）。"
                                % os.path.basename(_fp))
                continue
        except Exception as e:
            out["error"] = ("%s 不是合法 zip/docx（%r）—— 疑似改名冒充。"
                            % (os.path.basename(_fp), e))
            continue
        out.update({"ok": True, "path": _fp, "size_bytes": _sz,
                    "error": None,
                    "note": "docx 汇总交付物已核实（合法 zip + 含 word/document.xml）。"})
        return out

    if not out["error"]:
        out["error"] = "找到 .docx 但都不合格（详见 candidates）。"
    return out


def export_docx_and_record(out_path=None, work_dir=None, registry_work_dir=None):
    """总理子代理的 docx 汇总入口：导出并回写计划文件（供总闸核实）。

    这是"把 docx 变成流程一等公民"的落点：
      · 导出成功后把路径/大小/时间写进计划文件；
      · 交付前总闸会**自己重查磁盘**（不采信这里的记录），
        因此即使有人手改计划文件也骗不过去。
    """
    try:
        import part_registry as _pr
    except Exception as e:
        return {"ok": False, "error": "part_registry 不可用：%r" % (e,)}
    _wd = registry_work_dir or work_dir
    _out = out_path
    if not _out:
        _base = os.path.abspath(str(work_dir or _wd or "."))
        _out = os.path.join(_base, "零件台账_总理汇总.docx")
    try:
        r = _pr.export_docx(_out, work_dir=_wd)
    except TypeError:
        r = _pr.export_docx(_out)
    except Exception as e:
        return {"ok": False, "error": "导出 docx 异常：%r" % (e,)}
    _existed = os.path.exists(_out)
    _sz = os.path.getsize(_out) if _existed else 0
    _res = {"ok": bool(r.get("ok")) if isinstance(r, dict) else bool(r),
            "path": _out, "size_bytes": _sz,
            "detail": r if isinstance(r, dict) else None}
    # 回写计划文件（仅作记录；总闸仍会自己重查磁盘）
    try:
        plan = load_plan(work_dir)
        if not plan.get("_load_error"):
            plan["docx_export"] = {"path": _out, "size_bytes": _sz,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_plan(plan, work_dir)
    except Exception:
        pass
    return _res


# ═══════════════════════════════════════════════════════════════════════════
# 十一、CLI
# ═══════════════════════════════════════════════════════════════════════════
def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _main_body(argv):
    import argparse
    ap = argparse.ArgumentParser(
        prog="mode2_flow.py",
        description="模式二（大型器械装配）流程强制契约：动态题数 / 按需提问 / "
                    "十阶段 / 总计子代理编制 / 总理子代理上报")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("set-depth", help="登记参数需求强度（full 强需求 / lite 弱需求）")
    p.add_argument("depth", choices=["full", "lite"])
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("begin-stage", help="开始一个分析阶段（必须按序）")
    p.add_argument("stage", choices=STAGE_IDS)
    p.add_argument("--note", default="")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("mark-stage-done", help="标记阶段完成并登记产出")
    p.add_argument("stage", choices=STAGE_IDS)
    p.add_argument("--artifact", default="")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("stage-status", help="查看十阶段推进情况")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("ask", help="登记一道按需提问（必须挂阶段 + 写理由）")
    p.add_argument("topic")
    p.add_argument("--stage", required=True, choices=STAGE_IDS)
    p.add_argument("--reason", required=True)
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("answer-question", help="★记录某题的用户真实答复")
    p.add_argument("seq", type=int, help="题号（record_question 返回的 seq）")
    p.add_argument("answer", help="用户答复原文")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("import-answers",
                       help="★【BUG-05】批量导入用户答复（一次写多条，省上下文）")
    p.add_argument("--json", default="", help="内联 JSON：[{seq,answer},...] 或 {seq:answer}")
    p.add_argument("--json-file", default="", help="JSON 文件路径（推荐，免转义）")
    p.add_argument("--overwrite", action="store_true", help="覆盖已答过的题")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("questions", help="查看提问台账与题数校验")
    p.add_argument("--final", action="store_true", help="按收尾口径校验下界")
    p.add_argument("--work-dir", default=None)

    # ── 【Bug② 修复】重做过期阶段（清除 stale 的唯一合法入口）──────────
    p = sub.add_parser("refresh-stage",
                       help="★重做某阶段：进入重做态（产物过期/用户改答后必须执行）")
    p.add_argument("stage", choices=STAGE_IDS)
    p.add_argument("--reason", default="", help="为什么重做（留痕；默认沿用 stale 原因）")
    p.add_argument("--artifact", default="",
                   help="可选：同时提交重算后的新产物（= refresh + mark-stage-done）")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)
    # 别名（用户口径里的 redo-stage 与 refresh-stage 等价）
    p = sub.add_parser("redo-stage", help="★refresh-stage 的别名")
    p.add_argument("stage", choices=STAGE_IDS)
    p.add_argument("--reason", default="")
    p.add_argument("--artifact", default="")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    # ── 【Bug③ 修复】三处 room_type 一致性自检（只读）──────────────────
    p = sub.add_parser("room-types",
                       help="★只读自检：编制 / plan 快照 / workflow_state 的 room_type 是否一致")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("sync-rooms",
                       help="★把门禁侧最终房间表回写 plan 快照（后置对账；select 已自动调用）")
    p.add_argument("--rooms-json", required=True, help="JSON 数组或 @文件路径")
    p.add_argument("--source", default="cli.sync-rooms")
    p.add_argument("--task-id", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("set-subagent-plan", help="★由总计子代理登记子代理编制")
    p.add_argument("--rooms-json", required=True, help="JSON 数组或 @文件路径")
    p.add_argument("--rationale", required=True)
    p.add_argument("--decided-by", default=SUBPLAN_AUTHORITY)
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("subagent-plan", help="查看当前子代理编制")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-room", help="查看总理子代理定义")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-report", help="★建模小屋向总理子代理上报")
    p.add_argument("--json", required=True, help="JSON 对象或 @文件路径")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-reports", help="列出总理子代理收到的全部上报")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("check-consistency", help="★相邻零件尺寸一致性（不一致→升级父代理）")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-verify",
                       help="★★总理子代理统一核实数据（从磁盘重算，未通过禁止结束）")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--verified-by", default=None,
                   help="核实者身份，默认「总理子代理」")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-verify-status", help="读回最近一次核实结论")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("export-docx",
                       help="★总理子代理汇总导出 docx（交付前总闸会核实它存在）")
    p.add_argument("--out", default=None, help="输出路径（默认 <交付目录>/零件台账_总理汇总.docx）")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("check-docx", help="核实 docx 交付物是否存在且合法")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("pre-delivery", help="★交付前总闸")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("plan", help="打印完整计划文件")
    p.add_argument("--work-dir", default=None)

    # ── 【2026-10-28 重构】总计子代理身份 + 总理监工看板 ──────────────
    p = sub.add_parser("analyst-bind",
                       help="★在【总计子代理】里运行：绑定它自己的 sessionId")
    p.add_argument("--session-id", default=None,
                   help="显式指定；默认读 DSH_SESSION_ID")
    p.add_argument("--force", action="store_true", help="已有绑定也要改绑")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("analyst-status",
                       help="查看总计子代理是否已绑定 + 是否还活着（alive）")
    p.add_argument("--work-dir", default=None)

    # ── 【2026-10-29 修复】fork 前置闸门 + 心跳 ──────────────────────
    p = sub.add_parser("analyst-guard",
                       help="★fork 之前必须先问：总计子代理是否还活着（活着则拒绝重复创建）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("analyst-heartbeat",
                       help="刷新总计子代理存活心跳（阶段动作已自动刷新，一般不用手调）")
    p.add_argument("--note", default="")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("prime-board", help="★总理子代理监工看板（房间状态+上报缺口+待办）")
    p.add_argument("--json", action="store_true", help="强制 JSON 输出（默认即 JSON）")
    p.add_argument("--work-dir", default=None)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    wd = getattr(args, "work_dir", None)

    def _rooms_json_error(exc):
        """--rooms-json 解析失败的【可操作】报错（BUG-2）。"""
        return {
            "ok": False,
            "error": "rooms-json 解析失败: %r" % (exc,),
            "hint": ("三种写法都支持，请在 PowerShell 里优先用 ②：",
                     " ① 内联 JSON：--rooms-json '[{\"name\":\"结构件\","
                     "\"type\":\"structural\",\"parts\":[\"DSH_x\"],"
                     "\"why\":\"…\"}]'",
                     " ② 文件路径（推荐）：--rooms-json C:\\path\\rooms.json"
                     "（不带 @，避免 PowerShell 把 @ 当 splatting 吃掉）",
                     " ③ 兼容写法：--rooms-json \"@C:\\path\\rooms.json\""
                     "（bash / cmd / Python 下可用）"),
            "note": "文件内容必须是【JSON 数组】，不要包一层 {\"rooms\": [...]}。",
        }

    def _load_json_arg(v):
        r"""支持 --rooms-json '[{...}]' 或 --rooms-json @file.json（便于长内容）。

        ── 【BUG-2 修复】PowerShell 的 @ 会被吃掉 ─────────────────────────
        现场：在 PowerShell 里写 --rooms-json "@C:\path
ooms.json"，
          @ 被 PS 当成 splatting/数组符号解释，参数被拆散，命令直接失败。
        处理（三重，任一即可用）：
          ① 仍支持 @file.json（bash / cmd / Python 直接调用时最自然）；
          ② 支持不带 @ 的 .json 文件路径（推荐 PS 用户这样写）；
          ③ 支持把 JSON 直接内联传入。
        另外把"解析失败"的报错换成可操作的：告诉用户三种写法都行。
        """
        s = v
        _is_path = False
        if isinstance(v, str):
            _raw = v.strip()
            # ① @file.json
            if _raw.startswith("@"):
                _is_path = True
                _raw = _raw[1:].strip().strip("'\"")
            # ② 裸路径：以 .json 结尾且文件真的存在（PS 下 @ 被吃掉后就是这个形态）
            elif _raw.lower().endswith(".json") and os.path.isfile(_raw):
                _is_path = True
            if _is_path:
                if not os.path.isfile(_raw):
                    raise IOError("文件不存在: %s" % _raw)
                with open(_raw, "r", encoding="utf-8-sig") as f:
                    s = f.read()
        elif isinstance(v, (list, dict)):
            return v
        return json.loads(s)

    _actor = getattr(args, "actor", None)
    if args.cmd == "set-depth":
        r = set_depth(args.depth, wd)
    elif args.cmd == "analyst-bind":
        r = analyst_bind(getattr(args, "session_id", None), wd,
                         force=bool(getattr(args, "force", False)))
    elif args.cmd == "analyst-guard":
        r = analyst_guard(wd)
    elif args.cmd == "analyst-heartbeat":
        r = analyst_heartbeat(getattr(args, "actor", None), wd,
                              note=getattr(args, "note", ""))
    elif args.cmd == "analyst-status":
        r = analyst_status(wd)
    elif args.cmd == "prime-board":
        r = prime_board(wd)
    elif args.cmd == "begin-stage":
        r = begin_stage(args.stage, wd, args.note, actor=_actor)
    elif args.cmd == "mark-stage-done":
        r = mark_stage_done(args.stage, args.artifact, wd, actor=_actor)
    elif args.cmd == "stage-status":
        r = stage_status(wd)
    elif args.cmd == "ask":
        r = record_question(args.topic, args.stage, args.reason, wd, actor=_actor)
    elif args.cmd == "answer-question":
        r = answer_question(args.seq, args.answer, wd, actor=_actor)
    elif args.cmd == "import-answers":
        _payload = None
        if getattr(args, "json_file", ""):
            try:
                with open(args.json_file, "r", encoding="utf-8-sig") as _f:
                    _payload = json.load(_f)
            except Exception as _e:
                r = {"ok": False, "error": "读取 --json-file 失败: %r" % (_e,)}
                _payload = None
        elif getattr(args, "json", ""):
            try:
                _payload = json.loads(args.json)
            except Exception as _e:
                r = {"ok": False, "error": "解析 --json 失败: %r" % (_e,)}
                _payload = None
        if _payload is not None:
            r = import_answers(_payload, wd, actor=_actor,
                               overwrite=bool(getattr(args, "overwrite", False)))
    elif args.cmd == "questions":
        r = question_report(wd, final=args.final)
    elif args.cmd in ("refresh-stage", "redo-stage"):
        r = refresh_stage(args.stage, wd, getattr(args, "reason", ""),
                          getattr(args, "artifact", ""), actor=_actor)
    elif args.cmd == "room-types":
        r = room_types_report(wd)
    elif args.cmd == "sync-rooms":
        try:
            _rooms = _load_json_arg(args.rooms_json)
        except Exception as e:
            _print(_rooms_json_error(e))
            return 1
        r = sync_rooms_snapshot(_rooms, wd, source=args.source,
                                task_id=getattr(args, "task_id", None))
    elif args.cmd == "set-subagent-plan":
        try:
            rooms = _load_json_arg(args.rooms_json)
        except Exception as e:
            _print({"ok": False, "error": "rooms-json 解析失败: %r" % (e,)})
            return 1
        r = set_subagent_plan(rooms, args.rationale, args.decided_by, wd, actor=_actor)
    elif args.cmd == "subagent-plan":
        r = get_subagent_plan(wd)
    elif args.cmd == "prime-room":
        r = {"ok": True, **prime_room_definition(wd)}
    elif args.cmd == "prime-report":
        try:
            rep = _load_json_arg(args.json)
        except Exception as e:
            _print({"ok": False, "error": "json 解析失败: %r" % (e,)})
            return 1
        r = record_prime_report(rep, wd)
    elif args.cmd == "prime-reports":
        r = prime_reports(wd)
    elif args.cmd == "check-consistency":
        r = check_prime_consistency(wd, getattr(args, "registry_work_dir", None))
    elif args.cmd == "prime-verify":
        r = prime_verify_all(wd, getattr(args, "registry_work_dir", None),
                             getattr(args, "verified_by", None))
    elif args.cmd == "prime-verify-status":
        r = prime_verify_status(wd)
    elif args.cmd == "export-docx":
        r = export_docx_and_record(getattr(args, "out", None), wd,
                                   getattr(args, "registry_work_dir", None))
    elif args.cmd == "check-docx":
        r = _check_docx_deliverable(wd, getattr(args, "registry_work_dir", None))
    elif args.cmd == "pre-delivery":
        r = pre_delivery_check(wd)
    elif args.cmd == "plan":
        r = load_plan(wd)
    else:
        ap.print_help()
        return 2

    _print(r)
    return 0 if r.get("ok") else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main_body(argv)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        _print({"ok": False, "error": "内部异常: %r" % (e,),
                "traceback": traceback.format_exc()[-1200:]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
