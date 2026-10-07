# -*- coding: utf-8 -*-
"""
refine_rules.py — 自动修正规则引擎
====================================
根据仿真反馈，生成可执行的参数修改建议。

策略原则：
  1. 只修改设计参数白名单中的参数
  2. 不触碰用户锁定参数（locked_params）
  3. 优先局部修正，不改变整体拓扑
  4. 每次修正有明确的物理依据
  5. 不超过最大迭代次数
"""
import math
from typing import Any, Optional


# ── 修正动作类型 ──────────────────────────────────────
ACTION_INCREASE_THICKNESS = "increase_thickness"
ACTION_INCREASE_FILLET = "increase_fillet"
ACTION_ADD_RIB = "add_rib"
ACTION_REDUCE_VOLUME = "reduce_volume"
ACTION_CHANGE_SHAPE = "change_shape"
ACTION_LOCK_PARAM = "lock_param"
ACTION_FLAG_TBD = "flag_tbd"


def generate_recommendations(
    report: dict,
    design_params: dict,
    locked_params: list[str],
    max_iterations: int = 5,
) -> dict[str, Any]:
    """基于仿真报告生成修正建议。

    Args:
        report: simulation_report.build_report 的输出
        design_params: 当前设计参数字典
        locked_params: 不可修改的参数名列表
        max_iterations: 最大剩余迭代次数

    Returns:
        {ok, actions, next_params, reason, risk}
    """
    actions = []
    fea = report.get("fea_result", {})
    acc = report.get("acceptance_criteria", {})
    missing_params = {}

    # ── 安全系数过低 → 增加壁厚/圆角/加筋 ──
    sf = fea.get("safety_factor")
    min_sf = acc.get("min_safety_factor", 2.0)
    max_sf = acc.get("target_safety_factor_max", 5.0)

    # ══ 【PH-02~06 修复】SF 必须【先校验类型与量级】，再参与判定 ═══════════
    # 实测（对抗测试物理深挖房）暴露 5 个绕过/崩溃点，全部源于"直接拿 sf 比较"：
    #   PH-02 NaN    : `nan < min_sf` 恒为 False → 跳过整个强度分支，
    #                  被当成"设计已最优"放行（n_actions=0），绕过强度防线；
    #   PH-03 字符串 : `'abc' < 2.0` 抛 TypeError（未捕获）→ 整个 optimize 崩；
    #   PH-04 布尔   : `True < 2.0` 为 True（float(True)=1.0）→ 误判强度不足；
    #   PH-05 None   : 被 `sf is not None` 排除 → 落入"已最优"放行，
    #                  掩盖了 FEA 失败的真相；
    #   PH-06 极小值 : SF=1e-6 只判 `< min_sf` 就放行，并算 sqrt(min/sf)≈1414
    #                  倍盲加厚 —— 实际该值已意味着"结果不可信"。
    # 统一口径：SF 必须是【有限的正数且不低于最低可信下限】。
    #   否则一律 fail-closed（拒绝给出修正建议），并如实说明原因 ——
    #   绝不在非法/不可信的校核结果上继续迭代。
    _SF_MIN_CREDIBLE = 0.5
    _sf_raw = sf
    _sf_num = None
    _sf_problem = None
    if _sf_raw is None:
        _sf_problem = "缺失（None）—— FEA 未产出安全系数，可能是求解失败"
    elif isinstance(_sf_raw, bool):
        _sf_problem = "是布尔值 %r（不是数值）" % (_sf_raw,)
    else:
        try:
            _sf_num = float(_sf_raw)
        except Exception:
            _sf_problem = "不是数值（%r）" % (_sf_raw,)
        else:
            if _sf_num != _sf_num:
                _sf_problem = "是 NaN（非数值）—— 通常意味着除零/无效输入"
            elif _sf_num in (float("inf"), float("-inf")):
                # 正无穷：应力为 0（无载荷/几何异常），交由下方"过度设计"分支处理
                if _sf_num < 0:
                    _sf_problem = "是负无穷（非法）"
            elif _sf_num <= 0:
                _sf_problem = "≤ 0（材料已进入破坏区，或载荷输入不合理）"
            elif _sf_num < _SF_MIN_CREDIBLE:
                _sf_problem = ("=%.3g 低于可信下限 %.1f —— 结果不可信，"
                               "不应据此给出加厚建议"
                               % (_sf_num, _SF_MIN_CREDIBLE))
    if _sf_problem:
        _actions = [{
            "action": "REJECT_UNRELIABLE_SAFETY_FACTOR",
            "param_id": None,
            "from": _sf_raw,
            "to": None,
            "reason": ("安全系数不可信：%s。拒绝在非法/不可信的校核结果上"
                       "继续迭代（fail-closed）；请先复核载荷数值与单位、"
                       "零件几何与材料属性，再重新校核。" % _sf_problem),
            "priority": 0,
            "blocking": True,
        }]
        return {
            "ok": False,
            "actions": _actions,
            "next_params": {},
            "reason": ("安全系数不可用（%s）—— 已 fail-closed，拒绝给出修正建议。"
                       % _sf_problem),
            "risk": "high",
            "rejected": True,
            "reject_reason": _sf_problem,
        }
    # 到这里 sf 已是【有限正数且 ≥ 可信下限】，可安全参与后续比较与开方
    sf = _sf_num

    if sf < min_sf:
        # SF 的类型/量级校验已在函数开头统一完成（PH-02~06 修复）：
        #   到这里 sf 必定是【有限正数且 ≥ 可信下限】，可安全开方。
        # 强度不足：增加壁厚
        thickness_param = design_params.get("thickness_mm")
        if thickness_param and thickness_param["id"] not in locked_params:
            current = thickness_param.get("value", 5)
            min_val = thickness_param.get("min", 2)
            # 按应力平方根比例估算所需厚度增量
            scale = math.sqrt(min_sf / sf)
            new_thickness = max(current * scale, current + 1.0, min_val)
            new_thickness = min(new_thickness, thickness_param.get("max", 50))
            actions.append({
                "action": ACTION_INCREASE_THICKNESS,
                "param_id": thickness_param["id"],
                "from": current,
                "to": round(new_thickness, 1),
                "reason": f"safety factor {sf:.2f} < {min_sf}, scale ~{scale:.2f}",
                "priority": 1,
            })
        else:
            # 尝试增加圆角
            fillet_param = design_params.get("fillet_mm")
            if fillet_param and fillet_param.get("id") not in locked_params:
                current_f = fillet_param.get("value", 2)
                new_f = min(current_f * 1.5, fillet_param.get("max", 15))
                actions.append({
                    "action": ACTION_INCREASE_FILLET,
                    "param_id": fillet_param["id"],
                    "from": current_f,
                    "to": round(new_f, 1),
                    "reason": f"safety factor {sf:.2f} too low; try larger fillet at stress concentration",
                    "priority": 2,
                })

    # ── 安全系数过高 → 减材优化 ──
    if sf is not None and sf > max_sf:
        thickness_param = design_params.get("thickness_mm")
        if thickness_param and thickness_param.get("id") not in locked_params:
            # [Bug7 连带修复] value 可能为 None（真实几何未取得壁厚时）。
            # 上游改为从真实零件取几何后，拿不到壁厚则 value=None；
            # 原实现直接 current * scale 会 TypeError 打崩整个 optimize。
            _cur = thickness_param.get("value")
            try:
                _cur = float(_cur) if _cur is not None else None
            except Exception:
                _cur = None
            _min_t = thickness_param.get("min")
            try:
                _min_t = float(_min_t) if _min_t is not None else 2.0
            except Exception:
                _min_t = 2.0
            if _cur is not None and _cur > 0:
                # 安全系数与厚度的平方近似成反比（梁弯曲）
                scale = math.sqrt(max_sf / sf)
                new_thickness = max(_cur * scale, _min_t)
                new_thickness = min(new_thickness, _cur - 0.5)
                if new_thickness > 0:
                    actions.append({
                        "action": ACTION_REDUCE_VOLUME,
                        "param_id": thickness_param["id"],
                        "from": _cur,
                        "to": round(new_thickness, 1),
                        "reason": f"safety factor {sf:.2f} > {max_sf}, reduce material",
                        "priority": 1,
                    })

    # ── 位移超限 → 增大截面惯性矩 ──
    max_disp = fea.get("max_displacement_mm")
    max_disp_req = acc.get("max_displacement_mm")
    if max_disp is not None and max_disp_req is not None and max_disp > max_disp_req:
        height_param = design_params.get("height_mm")
        if height_param and height_param.get("id") not in locked_params:
            current_h = height_param.get("value", 60)
            # 悬臂梁挠度 ∝ 1/h³，所需高度增量 ≈ (disp/ratio)^(1/3)
            scale = (max_disp / max_disp_req) ** (1.0 / 3.0)
            new_h = min(current_h * scale, height_param.get("max", 200))
            actions.append({
                "action": ACTION_CHANGE_SHAPE,
                "param_id": height_param["id"],
                "from": current_h,
                "to": round(new_h, 1),
                "reason": f"displacement {max_disp:.3f}mm > {max_disp_req}mm; increase height by ~{scale:.2f}x",
                "priority": 1,
            })

    # ── 几何门禁失败 ──
    geom = report.get("geometry_gate", {})
    for gate in geom.get("gates", []):
        if gate.get("status") == "FAIL":
            gid = gate.get("id", "")
            if gid == "DESIGN_SPACE":
                actions.append({
                    "action": ACTION_FLAG_TBD,
                    "reason": f"design space violation detected: {gate}",
                    "priority": 0,
                })
            elif gid == "WALL_THICKNESS":
                actions.append({
                    "action": ACTION_INCREASE_THICKNESS,
                    "reason": f"wall thickness below minimum: {gate}",
                    "priority": 1,
                })

    # ── 排序并裁剪到最大迭代 ──
    actions.sort(key=lambda a: a.get("priority", 99))
    remaining_iters = max_iterations - report.get("iteration", 1)
    actions = actions[:remaining_iters]

    # ── 构建 next_params ──
    next_params = dict(design_params)
    for act in actions:
        pid = act.get("param_id")
        if pid and pid in next_params:
            next_params[pid]["value"] = act.get("to", next_params[pid].get("value"))

    if not actions:
        return {
            "ok": True,
            "actions": [],
            "next_params": next_params,
            "reason": "no actionable recommendations; design may already be optimal or outside parameter space",
            "risk": "NONE",
        }

    return {
        "ok": True,
        "actions": actions,
        "next_params": next_params,
        "reason": f"{len(actions)} recommendation(s) generated for iteration {report.get('iteration', 1) + 1}",
        "risk": "MEDIUM" if len(actions) > 2 else "LOW",
    }


def validate_action(action: dict, design_params: dict, locked_params: list[str]) -> dict:
    """校验单个修正动作是否合法。"""
    errors = []
    pid = action.get("param_id", "")
    if pid in locked_params:
        errors.append(f"param '{pid}' is locked and cannot be modified")
    if pid not in design_params:
        errors.append(f"param '{pid}' not found in design_params")
    param = design_params.get(pid, {})
    new_val = action.get("to")
    if new_val is not None:
        mn = param.get("min", -float("inf"))
        mx = param.get("max", float("inf"))
        if new_val < mn:
            errors.append(f"new value {new_val} < min {mn}")
        if new_val > mx:
            errors.append(f"new value {new_val} > max {mx}")
    return {"ok": len(errors) == 0, "errors": errors}
