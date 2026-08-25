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

    # 综合判定
    overall = fea_result.get("overall", "UNKNOWN")
    if geometry_check.get("status") == "FAIL":
        overall = "FAIL"
    elif geometry_check.get("status") == "WARNING" and overall != "FAIL":
        overall = "REVIEW"

    report = {
        "schema_version": "1.0",
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
        },

        "design_params": design_params,

        "geometry_gate": geometry_check,

        "fea_result": fea_result,

        "acceptance_criteria": {
            "min_safety_factor": acc.get("min_safety_factor", 2.0),
            "max_safety_factor": acc.get("target_safety_factor_max", 5.0),
            "max_displacement_mm": acc.get("max_displacement_mm"),
            "analysis_type": acc.get("analysis_type", "linear_static"),
        },

        "overall_status": overall,
        "passed_gates": _extract_passed(geometry_check, fea_result),
        "failed_gates": _extract_failed(geometry_check, fea_result),
        "not_evaluated": _extract_not_evaluated(geometry_check, fea_result),

        "release_readiness": {
            "status": _release_status(overall, fea_result, acc),
            "human_review_required": overall != "PASS",
            "limitations": fea_result.get("limitations", []),
        },
    }
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
        if min_sf <= sf <= max_sf:
            return "RELEASE_CANDIDATE"
        return "OVER_ENGINEERED"
    elif overall == "FAIL":
        return "NEEDS_REVISION"
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
        "",
        "## Release Readiness",
        f"- Status: {report['release_readiness']['status']}",
        f"- Human Review Required: {report['release_readiness']['human_review_required']}",
    ]
    if report["failed_gates"]:
        lines.append("\n### Failed Gates")
        for g in report["failed_gates"]:
            lines.append(f"- ❌ {g.get('id', '?')}: {g.get('note', '')}")
    if report["not_evaluated"]:
        lines.append("\n### Not Evaluated")
        for g in report["not_evaluated"]:
            lines.append(f"- ⏭ {g}")
    return "\n".join(lines)
