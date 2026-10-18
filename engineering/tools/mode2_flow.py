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
        "subagent_plan": None, # 由【总计子代理】写入
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


def save_plan(plan, work_dir=None):
    plan = dict(plan or {})
    plan["schema"] = "dsh-engineering/mode2-plan@1"
    plan["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # task_id 一律以 workflow_state 为准，保证新旧任务的编制不会混用。
    if not plan.get("task_id"):
        try:
            import json as _json
            import os as _os
            _sp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                "workflow_state.json")
            if _os.path.exists(_sp):
                with open(_sp, "r", encoding="utf-8") as _f:
                    plan["task_id"] = (_json.load(_f) or {}).get("task_id")
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
def begin_stage(stage, work_dir=None, note=""):
    """开始一个分析阶段。**必须按序**，跳阶段直接拒绝。

    这条约束是"按需提问"的前提：只有真的推进到某个阶段，
    才允许登记挂在那个阶段上的问题（见 record_question）。
    """
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


def mark_stage_done(stage, artifact="", work_dir=None):
    """把某阶段标记为完成，并登记它的产出物（artifact）。

    为什么要登记 artifact：没有产出的"阶段完成"就是走过场。
    最终交付时会校验关键阶段是否真的留下了产出。
    """
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
                          % STAGE_LABELS.get(stage, stage))}
    hit["status"] = "done"
    hit["artifact"] = artifact
    hit["done_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_plan(plan, work_dir)
    return {"ok": True, "stage": stage, "artifact": artifact}


def stage_status(work_dir=None):
    """返回十阶段的推进情况，含"哪些阶段有产出、哪些还是空"。"""
    plan = load_plan(work_dir)
    done = {}
    for s in plan.get("stages", []):
        if s.get("status") == "done":
            done[s["stage"]] = s.get("artifact") or ""
    rows = []
    for _id, _lab, _need in STAGES:
        rows.append({"stage": _id, "label": _lab, "needs": _need,
                     "done": _id in done, "artifact": done.get(_id, "")})
    _missing = [r["label"] for r in rows if not r["done"]]
    return {"ok": True, "stage_index": plan.get("stage_index", -1),
            "total": len(STAGES), "done_count": len(done),
            "rows": rows, "missing": _missing,
            "complete": not _missing}


# ═══════════════════════════════════════════════════════════════════════════
# 六、★ 按需提问（取消"一股脑全问"）
# ═══════════════════════════════════════════════════════════════════════════
def record_question(topic, stage, reason, work_dir=None):
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
    save_plan(plan, work_dir)
    return {"ok": True, "question": q, "asked": len(plan["questions"]),
            "min": lim["min"], "max": lim["max"],
            "remaining_to_max": max(0, lim["max"] - len(plan["questions"])),
            "note": ("已登记按需提问 %d/%d（%s）。"
                     % (len(plan["questions"]), lim["max"], lim["label"]))}


def answer_question(seq, answer, work_dir=None):
    """记录某道已登记问题的【用户真实答复】。

    为什么必须有这一步：record_question 只登记"我要问什么"，
      模型完全可以登记完就当问过了（实测把 21 条拼成"汇总答复"直接
      调 provide_context）。这里强制把答复逐条落到对应题目上，
      未答的题在收尾校验时会让交付直接失败。
    """
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
    hit["answered"] = True
    hit["answer"] = ans
    hit["answered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    plan["questions"] = qs
    save_plan(plan, work_dir)
    _done = len([q for q in qs if q.get("answered")])
    return {"ok": True, "seq": seq, "answered": _done, "total": len(qs),
            "note": "已记录第 %d 题答复（%d/%d）。" % (seq, _done, len(qs))}


def question_report(work_dir=None, final=False):
    """汇总提问情况。final=True 时【强制】校验下界是否达到。"""
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    depth = plan.get("param_depth")
    if not depth:
        return {"ok": False, "error": "尚未确定参数需求强度（强需求/弱需求）。"}
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
    return out


SUBPLAN_AUTHORITY = "总计子代理"

#: 每个子代理（房间）必须被总计子代理说明"为什么需要它"。
MIN_SUBAGENTS = 1
MAX_SUBAGENTS = 24


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
    _missing = st.get("missing") or []
    if _missing:
        return {
            "ok": False,
            "gate": "ANALYSIS_INCOMPLETE",
            "stage_index": st.get("stage_index"),
            "done_count": st.get("done_count"),
            "total": st.get("total"),
            "missing": _missing,
            "error": ("十阶段分析尚未完成（已完成 %d/%d），禁止登记编制或开工。"
                      % (st.get("done_count") or 0, st.get("total") or 10)),
            "hint": ("请让总计子代理按序推进并逐阶段 mark-stage-done："
                     "mode2_flow.py begin-stage <阶段> → mark-stage-done <阶段>。",
                     "未完成的阶段：%s" % "、".join(_missing)),
        }
    return {"ok": True, "done_count": st.get("done_count"),
            "total": st.get("total")}


def set_subagent_plan(rooms, rationale, decided_by, work_dir=None):
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
    if not isinstance(rooms, list) or not rooms:
        return {"ok": False, "error": "rooms 必须是非空数组（至少 1 个建模子代理）。"}
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
            return {"ok": False,
                    "error": ("子代理【%s】没有列出 parts（该屋负责哪些零件）。"
                              "编制必须写明零件范围，否则无法做归属校验。" % name)}
        if not str(r.get("why") or "").strip():
            return {"ok": False,
                    "error": ("子代理【%s】缺少 why（为什么需要它）。"
                              "用户要求每个零件都能追溯到存在的理由，编制同理。" % name)}
        norm.append({
            "name": name,
            "type": str(r.get("type") or "structural"),
            "parts": [str(p).strip() for p in parts if str(p).strip()],
            "why": str(r.get("why") or "").strip(),
            "depends_on": [str(d).strip() for d in (r.get("depends_on") or []) if str(d).strip()],
        })

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
    _tid = plan.get("task_id")
    if not _tid:
        try:
            import workflow_gate as _wg
            _tid = (_wg.load_state() or {}).get("task_id")
        except Exception:
            _tid = None
    plan["task_id"] = _tid
    plan["subagent_plan"] = {
        "decided_by": SUBPLAN_AUTHORITY,
        "task_id": _tid,
        "rationale": str(rationale).strip(),
        "count": len(norm),
        "rooms": norm,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_plan(plan, work_dir)
    return {"ok": True, "count": len(norm), "rooms": norm,
            "decided_by": SUBPLAN_AUTHORITY,
            "note": "编制已落盘；建模小屋必须严格按此编制创建，数量不得固定。"}


def get_subagent_plan(work_dir=None):
    plan = load_plan(work_dir)
    sp = plan.get("subagent_plan")
    if not sp:
        return {"ok": False,
                "error": ("尚无子代理编制 —— 必须先由【总计子代理】调 "
                          "set-subagent-plan 登记，禁止使用固定房间表。")}
    return {"ok": True, **sp}


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
        blockers.append("十阶段未走完，缺少产出：%s" % "、".join(st.get("missing") or []))

    qr = question_report(work_dir, final=True)
    if not qr.get("ok"):
        blockers.append(qr.get("error") or "提问数量不达标")

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
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("mark-stage-done", help="标记阶段完成并登记产出")
    p.add_argument("stage", choices=STAGE_IDS)
    p.add_argument("--artifact", default="")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("stage-status", help="查看十阶段推进情况")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("ask", help="登记一道按需提问（必须挂阶段 + 写理由）")
    p.add_argument("topic")
    p.add_argument("--stage", required=True, choices=STAGE_IDS)
    p.add_argument("--reason", required=True)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("answer-question", help="★记录某题的用户真实答复")
    p.add_argument("seq", type=int, help="题号（record_question 返回的 seq）")
    p.add_argument("answer", help="用户答复原文")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("questions", help="查看提问台账与题数校验")
    p.add_argument("--final", action="store_true", help="按收尾口径校验下界")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("set-subagent-plan", help="★由总计子代理登记子代理编制")
    p.add_argument("--rooms-json", required=True, help="JSON 数组或 @文件路径")
    p.add_argument("--rationale", required=True)
    p.add_argument("--decided-by", default=SUBPLAN_AUTHORITY)
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

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    wd = getattr(args, "work_dir", None)

    def _load_json_arg(v):
        """支持 --rooms-json '[{...}]' 或 --rooms-json @file.json（便于长内容）。"""
        s = v
        if isinstance(v, str) and v.startswith("@"):
            with open(v[1:], "r", encoding="utf-8") as f:
                s = f.read()
        return json.loads(s)

    if args.cmd == "set-depth":
        r = set_depth(args.depth, wd)
    elif args.cmd == "begin-stage":
        r = begin_stage(args.stage, wd, args.note)
    elif args.cmd == "mark-stage-done":
        r = mark_stage_done(args.stage, args.artifact, wd)
    elif args.cmd == "stage-status":
        r = stage_status(wd)
    elif args.cmd == "ask":
        r = record_question(args.topic, args.stage, args.reason, wd)
    elif args.cmd == "answer-question":
        r = answer_question(args.seq, args.answer, wd)
    elif args.cmd == "questions":
        r = question_report(wd, final=args.final)
    elif args.cmd == "set-subagent-plan":
        try:
            rooms = _load_json_arg(args.rooms_json)
        except Exception as e:
            _print({"ok": False, "error": "rooms-json 解析失败: %r" % (e,)})
            return 1
        r = set_subagent_plan(rooms, args.rationale, args.decided_by, wd)
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
