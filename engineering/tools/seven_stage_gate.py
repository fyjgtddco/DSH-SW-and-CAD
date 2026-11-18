"""七阶段凭证的只读读闸（DSH 宿主是唯一写入方）。

════════════════════════════════════════════════════════════════════════════
它是什么
════════════════════════════════════════════════════════════════════════════
七阶段插件 `dsh-engineering-seven-stage`（宿主进程里的 Cordis 插件）把每个任务的
分析状态写进 `DSH_SEVEN_STAGE_STATE_DIR/<task_id>.json`。那份文件由宿主写、由
ACL 保护，模型工具碰不到 —— 这就是凭证的信任根。

Python 侧（门禁 / 建模 / 交付）要判断「这次设计能不能开工」，只能读这份文件，
**不能**采信模型自报的任何字段（ticket / revision / 分析者会话 ID 都一样）。

════════════════════════════════════════════════════════════════════════════
调用约定（合并交接文档「关键读闸调用」）
════════════════════════════════════════════════════════════════════════════
登记编制（真实根会话，只调一次）：

    from seven_stage_gate import read_design
    draft = read_design(current_task_id, trusted_root_session_id)
    draft["ok"] 才允许把 draft["rooms"] 交给编制校验；把 draft["ticket"] 存下来。

重查（select / 恢复开工 / 建模放权 / 交付前）：

    current = read_design(current_task_id, trusted_root_session_id, saved_ticket)
    # saved_ticket 非空，current["ok"] 才能继续调用原本的下游门禁。
    # 需要完整设计记录时传 include_records=True，写进受控交付目录。

════════════════════════════════════════════════════════════════════════════
【2026-11-18 本仓库改动】
════════════════════════════════════════════════════════════════════════════
① 状态目录默认值必须与 Node 侧【逐字一致】，否则会出现"插件写了、门禁读不到"
   的错配（这是本仓库踩过多次的坑：同一份状态被两套路径劈成两半）。
   两边同一条规则：
       ① DSH_SEVEN_STAGE_STATE_DIR（绝对路径，显式配置时优先）
       ② <workflow_state.json 所在目录>/seven_stage
   其中 ② 的 Node 侧由 `dirname(config.workflowFile) + '/seven_stage'` 推出，
   Python 侧由 `dirname(_store.state_path('workflow_state.json'))` 推出 ——
   两者指向同一个目录，无需任何额外配置。
   任务隔离靠文件名 `<task_id>.json`（task_id 全局唯一）。

② 判据严格度不变（这是文档要求的，不许放宽）：
   · phase 必须 ready，且没有待答问题 / 待办 job / 失败角色；
   · 63 个小步必须按序齐全（缺一即拒，乱序即拒）；
   · 每步必须有本版本的知识、设计、复审三件套，且复审 verdict ∈ {pass, conditional}；
   · 末步复审必须 pass（"最终设计规格没有独立审查通过"直接拒）；
   · 四个角色（knowledge/design/review/planner/root）必须是四个不同的真实会话，
     且各自登记了 kind=complete 的结束理由；
   · 房间草案必须属于当前 revision。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

#: steps.json 的版本指纹。改 steps.json 必须同步改这里 ——
#: 否则「设计依据版本不匹配」会让所有存量任务失效（这是有意的硬约束）。
_SPEC_HASH = "65d8374b61aed6e7f011060e7136b4d3289a34835b030a86f90aa8a4d83113ba"
_ROLES = ("knowledge", "design", "review", "planner", "root")
_STEPS = [f"{stage}.{n}" for stage, count in enumerate((6, 6, 8, 10, 9, 12, 12), 1)
          for n in range(1, count + 1)]


def _workflow_state_path():
    """权威 workflow_state.json 的绝对路径（读不到返回 None）。

    只读、失败即 None —— 绝不猜路径。与 mode2_flow / workflow_gate 同源，
    都走 _store.state_dir()。
    """
    try:
        import _store as _st
        _sd = _st.state_dir()
    except Exception:
        return None
    if not _sd:
        return None
    return os.path.join(_sd, "workflow_state.json")


def state_dir_for(task_id=None):
    """解析七阶段状态目录。**必须与 Node 侧逐字一致**（见模块头 ①）。

    优先级：
      ① DSH_SEVEN_STAGE_STATE_DIR（部署方显式配置，必须是绝对路径）
      ② <workflow_state.json 所在目录>/seven_stage
      ③ None（调用方 fail-closed）

    Returns: (path_or_None, source)
    """
    raw = os.environ.get("DSH_SEVEN_STAGE_STATE_DIR", "")
    if raw and Path(raw).is_absolute():
        return raw, "env:DSH_SEVEN_STAGE_STATE_DIR"
    wf = _workflow_state_path()
    if wf:
        _d = os.path.join(os.path.dirname(wf), "seven_stage")
        if Path(_d).is_absolute():
            return _d, "sibling-of-workflow_state/seven_stage"
    return None, "unconfigured"


def read_design(task_id, root_session_id, sealed_ticket=None, *, include_records=False):
    """重查七阶段凭证。任何一项不满足都返回 ok=False（fail-closed）。"""
    def denied(reason, **extra):
        out = {"ok": False, "gate": "SEVEN_STAGE_HANDOFF_REQUIRED", "error": reason}
        out.update(extra)
        return out

    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", task_id):
        return denied("任务 ID 不合法。")
    if not root_session_id:
        return denied("缺少由 DSH 宿主取得的真实根会话 ID。")
    state_dir, source = state_dir_for(task_id)
    if not state_dir:
        return denied("七阶段状态目录未配置：请设置绝对路径 DSH_SEVEN_STAGE_STATE_DIR，"
                      "或确保本任务交付目录可用（会回落到 <交付目录>/seven_stage）。",
                      state_dir_source=source)
    _dir = Path(state_dir)
    if not _dir.is_absolute():
        return denied("必须配置绝对路径 DSH_SEVEN_STAGE_STATE_DIR 并与 DSH 宿主一致。")
    try:
        state = json.loads((_dir / f"{task_id}.json").read_text(encoding="utf-8"))
        if (state["schema"] != "dsh-engineering/seven-stage-state@1"
                or state["taskId"] != task_id or state["root"] != root_session_id
                or state["specHash"] != _SPEC_HASH):
            return denied("任务、根会话或设计依据版本不匹配。",
                          state_dir_source=source)
        if (state["phase"] != "ready" or state["question"] or state["jobs"] or state["failures"]
                or any(not x.get("interpretation") for x in state["facts"])):
            return denied("分析仍在运行、缺证或只有部分规格。",
                          phase=state.get("phase"),
                          steps_done=len(state.get("records") or []),
                          open_question=(state.get("question") or {}).get("id"),
                          pending_jobs=sorted((state.get("jobs") or {}).keys()),
                          failures=sorted((state.get("failures") or {}).keys()),
                          state_dir_source=source)
        records = state["records"]
        if [x["step"] for x in records] != _STEPS or state["cursor"] != len(_STEPS):
            return denied("七阶段小步记录缺失或无序。",
                          steps_done=len(records), steps_total=len(_STEPS),
                          state_dir_source=source)
        for row in records:
            review, design, knowledge = row["review"], row["design"], row["knowledge"]
            if (not row["outputs"] or not knowledge["searches"] or not knowledge["sources"]
                    or review["verdict"] not in ("pass", "conditional") or review["kind"] != "review"
                    or review["revision"] != row["revision"] or review["designId"] != design["id"]
                    or review["knowledgeId"] != knowledge["id"]
                    or any(x["blocking"] for x in design["gaps"] + knowledge["gaps"])
                    or any(x["kind"] in ("hard", "unknown") for x in review["issues"])):
                return denied(f"步骤 {row['step']} 缺当前版知识、设计或复审证据。",
                              step=row["step"], state_dir_source=source)
        if records[-1]["review"]["verdict"] != "pass":
            return denied("最终设计规格没有独立审查通过。",
                          final_verdict=records[-1]["review"]["verdict"])
        members = state["members"] | {"planner": state["planner"], "root": state["root"]}
        if set(members) != set(_ROLES) or len(set(members.values())) != len(_ROLES):
            return denied("会话身份链不完整。", members=sorted(members))
        for role in _ROLES:
            row = state["ended"][role]
            if row["actor"] != members[role] or row["kind"] != "complete" or not row["reason"].strip():
                return denied(f"{role} 缺本会话的完成理由。", role=role)
        proposal = state["roomProposal"]
        if (proposal["actor"] != members["planner"] or proposal["revision"] != state["revision"]
                or not proposal["rooms"]):
            return denied("房间草案不属于当前已审设计版本。")
        proof = {
            "task_id": task_id, "root": members["root"], "analyst": members["planner"],
            "revision": state["revision"], "spec_hash": _SPEC_HASH, "members": members,
            "requirements": state["requirements"], "facts": state["facts"],
            "records": records, "proposal": proposal,
        }
        ticket = hashlib.sha256(json.dumps(
            proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if sealed_ticket is not None and ticket != sealed_ticket:
            return denied("已登记的分析凭证与当前设计不同，必须重新登记建模编制。",
                          current_ticket=ticket, saved_ticket=sealed_ticket,
                          revision=state["revision"])
        result = {
            "ok": True, "task_id": task_id, "ticket": ticket, "revision": state["revision"],
            "analyst_session_id": members["planner"], "rooms": proposal["rooms"],
            "rationale": proposal["rationale"], "scope": proposal["scope"],
            "final_spec": records[-1]["outputs"],
            "state_dir_source": source,
        }
        if include_records:
            result["design_record"] = proof
            result["end_reasons"] = state["ended"]
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as err:
        return denied(f"当前七阶段凭证不可读取或不完整（{type(err).__name__}）。",
                      state_dir=state_dir, state_dir_source=source, detail=str(err)[:200])
