# -*- coding: utf-8 -*-
"""
simulation_report.py — 结构化仿真报告生成
==========================================
将 FEA 求解结果、几何门禁结果、设计参数汇总为标准 JSON 报告。
报告可作为 Agent 上下文输入，也可导出为 Markdown 摘要。
"""
import json
import os
import time
from typing import Any, Optional


def build_report(
    run_id: str,
    load_case: dict,
    design_params: dict,
    geometry_check: dict,
    fea_result: dict,
    iteration: int = 1,
    parent_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """构建完整仿真报告。

    Args:
        run_id: 本次运行唯一标识
        load_case: 载荷工况字典
        design_params: 当前设计参数
        geometry_check: geometry_gate 输出
        fea_result: fea_solver 输出
        iteration: 当前迭代轮次
        parent_run_id: 上一轮运行 ID（用于追踪链）

    Returns:
        完整报告字典，可 JSON 序列化保存
    """
    mat = load_case.get("material", {})
    acc = load_case.get("acceptance", {})

    # ── 【NEW-01 修复】acceptance_criteria 必须【完整透传】全部验收参数 ────
    # 原缺陷：只透传 SF 上下限 / 位移 / 分析类型 5 个字段，把 30 年寿命、
    #   年循环次数、负载类型、冲击系数、可靠度、表面加工等【疲劳相关参数
    #   全部丢掉】。后果：报告顶层 acceptance_criteria 看不到 30 年寿命，
    #   下游工具（refine_rules / 第三方消费方）无从得知判据 ——
    #   虽然 fatigue 子模块自己去读 load_case 算对了，但"判据"与"结论"
    #   分处两地，审计与复现都困难。
    # 修复：以 load_case.acceptance 为基准【整表透传】，再补默认值兜底。
    _acc_out = dict(acc) if isinstance(acc, dict) else {}
    _acc_out.setdefault("min_safety_factor", 2.0)
    _acc_out.setdefault("max_safety_factor", _acc_out.get("target_safety_factor_max", 5.0))
    _acc_out.setdefault("max_displacement_mm", None)
    _acc_out.setdefault("analysis_type", "linear_static")

    # 综合判定
    overall = fea_result.get("overall", "UNKNOWN")
    if geometry_check.get("status") == "FAIL":
        overall = "FAIL"
    elif geometry_check.get("status") == "WARNING" and overall != "FAIL":
        overall = "REVIEW"
    # ── 【BUG-02 修复】疲劳/寿命结论必须参与整体判定 ─────────────────────
    # 原实现完全不看疲劳，导致"静力过了但 30 年寿命不够"的零件被误判为 PASS。
    _fat = fea_result.get("fatigue") or {}
    _fat_verdict = _fat.get("verdict")
    if _fat_verdict == "FAIL":
        overall = "FAIL"
    elif _fat_verdict in ("REVIEW", "NOT_EVALUATED") and overall == "PASS":
        overall = "REVIEW"

    report = {
        "schema_version": "1.1",
        "run_id": run_id,
        "iteration": iteration,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parent_run_id": parent_run_id,

        "load_case_summary": {
            "problem_id": load_case.get("meta", {}).get("problem_id", "?"),
            "title": load_case.get("meta", {}).get("title", "?"),
            "material": mat.get("name", "?"),
            "yield_strength_mpa": mat.get("yield_strength_mpa", 0),
            "E_mpa": mat.get("youngs_modulus_mpa", 0),
            # ── 【BUG-05/08 修复】material 缺失时必须显式报错，而不是显示 '?' ──
            "material_resolved": bool(mat.get("name") or mat.get("id")),
            "density_kg_m3": mat.get("density_kg_m3", 0),
        },

        "design_params": design_params,

        "geometry_gate": geometry_check,

        "fea_result": fea_result,

        # ── 【BUG-02 修复】疲劳/设计寿命独立成块，便于直接读取结论 ─────────
        "fatigue": _fat,

        "acceptance_criteria": _acc_out,

        "overall_status": overall,
        "passed_gates": _extract_passed(geometry_check, fea_result),
        "failed_gates": _extract_failed(geometry_check, fea_result),
        "not_evaluated": _extract_not_evaluated(geometry_check, fea_result),

        "release_readiness": {
            "status": _release_status(overall, fea_result, acc),
            "human_review_required": overall != "PASS",
            "limitations": fea_result.get("limitations", []),
            # 【BUG-02】交付前必须明确"寿命是否已验证"
            "fatigue_verified": _fat_verdict in ("PASS", "FAIL", "REVIEW"),
            "fatigue_verdict": _fat_verdict,
        },
    }
    # ── 【BUG-05 修复】材料未赋/密度虚标（1000=水）必须作为显式告警 ───────
    warnings = []
    if not report["load_case_summary"]["material_resolved"]:
        warnings.append("材料未解析（material 字段为空）—— 强度/寿命/质量结论均不可信")
    _rho = mat.get("density_kg_m3") or 0
    if _rho and abs(_rho - 1000.0) < 1.0:
        warnings.append("材料密度为 1000 kg/m³（等同水）—— 极可能是 SolidWorks "
                        "未赋材质导致的默认值，质量属性与强度校核全部失真")
    if _fat_verdict == "NOT_EVALUATED":
        warnings.append("疲劳/寿命校核未执行：%s" % _fat.get("error", "未知原因"))
    if warnings:
        report["warnings"] = warnings
    return report


def _extract_passed(gc: dict, fr: dict) -> list[str]:
    passed = []
    for gate in gc.get("gates", []):
        if gate.get("status") == "PASS":
            passed.append(gate["id"])
    for gid, val in fr.get("gates", {}).items():
        if isinstance(val, dict) and val.get("status") == "PASS":
            passed.append(gid)
    return list(dict.fromkeys(passed))


def _extract_failed(gc: dict, fr: dict) -> list[dict]:
    failed = []
    for gate in gc.get("gates", []):
        if gate.get("status") == "FAIL":
            failed.append(gate)
    for gid, val in fr.get("gates", {}).items():
        if isinstance(val, dict) and val.get("status") == "FAIL":
            failed.append({"id": gid, **val})
    return failed


def _extract_not_evaluated(gc: dict, fr: dict) -> list[str]:
    not_eval = []
    for gate in gc.get("gates", []):
        if gate.get("status") == "N/A":
            not_eval.append(gate["id"])
    return not_eval


def _release_status(overall: str, fr: dict, acc: dict) -> str:
    if overall == "PASS":
        sf = fr.get("safety_factor", 0)
        min_sf = acc.get("min_safety_factor", 2.0)
        max_sf = acc.get("target_safety_factor_max", 5.0)
        # Bug-21 修复: 安全系数在范围内 → RELEASE_CANDIDATE；超出上限 → OVER_ENGINEERED
        if min_sf <= sf <= max_sf:
            return "RELEASE_CANDIDATE"
        return "OVER_ENGINEERED"
    elif overall == "FAIL":
        return "NEEDS_REVISION"
    # Bug-21 修复: 安全系数过高但整体 PASS 时，返回 OVER_ENGINEERED 而非 RELEASE_CANDIDATE
    if overall == "WARNING":
        sf = fr.get("safety_factor", 0)
        max_sf = acc.get("target_safety_factor_max", 5.0)
        if sf > max_sf:
            return "OVER_ENGINEERED"
        return "REVIEW_NEEDED"
    return "PENDING"


def save_report(report: dict, output_dir: str) -> str:
    """保存报告为 JSON 文件，并返回文件路径。"""
    os.makedirs(output_dir, exist_ok=True)
    run_id = report["run_id"]
    path = os.path.join(output_dir, f"{run_id}_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def report_to_markdown(report: dict) -> str:
    """将报告转为 Markdown 摘要。"""
    lines = [
        f"# Simulation Report: {report['load_case_summary']['problem_id']}",
        f"**Run ID:** {report['run_id']}  **Iteration:** {report['iteration']}",
        f"**Time:** {report['timestamp']}",
        "",
        "## Load Case",
        f"- Problem: {report['load_case_summary']['title']}",
        f"- Material: {report['load_case_summary']['material']}",
        f"- Yield Strength: {report['load_case_summary']['yield_strength_mpa']} MPa",
        "",
        "## Results",
        f"- **Overall Status:** {report['overall_status']}",
        f"- Safety Factor: {report['fea_result'].get('safety_factor', 'N/A')}",
        f"- Max Stress: {report['fea_result'].get('max_von_mises_mpa', 'N/A')} MPa",
        f"- Max Displacement: {report['fea_result'].get('max_displacement_mm', 'N/A')} mm",
        f"- Volume: {report['fea_result'].get('volume_mm3', 'N/A')} mm³",
        f"- Mass: {report['fea_result'].get('mass_kg', 'N/A')} kg",
    ]
    # ── 【BUG-02 修复】Markdown 摘要必须包含疲劳/寿命结论 ─────────────────
    _fat = report.get("fatigue") or (report.get("fea_result") or {}).get("fatigue") or {}
    if _fat:
        lines += [
            "",
            "## Fatigue / Design Life",
            f"- Verdict: **{_fat.get('verdict', 'N/A')}**",
            f"- Endurance Limit Se: {_fat.get('endurance_limit_mpa', 'N/A')} MPa",
            f"- Fatigue Safety Factor: {_fat.get('fatigue_sf', 'N/A')}"
            f" (min {_fat.get('min_required_fatigue_sf', 'N/A')})",
            f"- Design Life: {_fat.get('design_life_years', 'N/A')} years"
            f" @ {_fat.get('cycles_per_year', 'N/A')} cycles/yr",
            f"- Miner Damage @ Design Life: {_fat.get('damage_design_life', 'N/A')} (< 1 通过)",
            f"- Allowable Life: {_fat.get('life_years_allowable', 'N/A')} years",
        ]
        if _fat.get("reason"):
            lines.append(f"- Reason: {_fat['reason']}")
        if _fat.get("verdict") == "NOT_EVALUATED":
            lines.append(f"- ⚠️ 未评估原因: {_fat.get('error', '未知')}")
    lines += [
        "",
        "## Release Readiness",
        f"- Status: {report['release_readiness']['status']}",
        f"- Human Review Required: {report['release_readiness']['human_review_required']}",
        f"- Fatigue Verified: {report['release_readiness'].get('fatigue_verified')}",
    ]
    if report.get("warnings"):
        lines.append("\n### Warnings")
        for w in report["warnings"]:
            lines.append(f"- ⚠️ {w}")
    if report["failed_gates"]:
        lines.append("\n### Failed Gates")
        for g in report["failed_gates"]:
            lines.append(f"- ❌ {g.get('id', '?')}: {g.get('note', '')}")
    if report["not_evaluated"]:
        lines.append("\n### Not Evaluated")
        for g in report["not_evaluated"]:
            lines.append(f"- ⏭ {g}")
    # Bug-23 修复: 确保末尾换行，防止 markdown 截断
    return "\n".join(lines) + "\n"
