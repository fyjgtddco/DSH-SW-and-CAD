# -*- coding: utf-8 -*-
"""
physics_bridge.py — Physics-in-the-Loop 命令行入口
====================================================
DSH 工程模式物理验证子系统的统一命令入口。

用法（直接运行）：
  python physics_bridge.py validate-case <load_case.json>
  python physics_bridge.py optimize <load_case.json> [--max-iter N]
  python physics_bridge.py demo
  python physics_bridge.py status

用法（通过 sw_bridge.py 路由）：
  python sw_bridge.py physics-status
  python sw_bridge.py physics-validate-case <case.json>
  python sw_bridge.py physics-demo
  python sw_bridge.py physics-optimize <case.json> --max-iter N
  python sw_bridge.py physics-report <run_id>
  python sw_bridge.py physics-recommend <run_id>

所有命令返回 dict（直接运行时会 JSON 打印），供 sw_bridge.py 路由调用。
"""
import sys
import os
import json
import argparse
import time

# Fix Windows GBK encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保 physics/ 子目录可导入
_HERE = os.path.dirname(os.path.abspath(__file__))
_physics_dir = os.path.join(_HERE, "physics")
if _physics_dir not in sys.path:
    sys.path.insert(0, _physics_dir)

import load_case
import material_db
import geometry_gate
import mesh_adapter
import fea_solver
import simulation_report as sim_report
import design_state
import refine_rules


# ═══════════════════════════════════════════════════════════════
#  命令实现 — 全部返回 dict，由调用方决定 print / 路由
# ═══════════════════════════════════════════════════════════════

def cmd_status(args=None) -> dict:
    """查询求解器后端状态。"""
    status = fea_solver.get_solver_status()
    status["command"] = "status"
    return status


def cmd_validate_case(case_path: str, relaxed: bool = False) -> dict:
    """校验载荷工况 JSON。"""
    result = load_case.load_from_file(case_path, strict=not relaxed)
    result["command"] = "validate-case"
    return result


def cmd_build(case_path: str) -> dict:
    """加载工况并初始化仿真环境摘要。"""
    lc_result = load_case.load_from_file(case_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    mat_resolve = material_db.resolve_material(lc_result["case"])
    if not mat_resolve["ok"]:
        return {"ok": False, "error": "material resolution failed", "details": mat_resolve}

    summary = load_case.load_case_to_summary(lc_result["case"])
    solver_status = fea_solver.get_solver_status()

    return {
        "ok": True,
        "command": "build",
        "load_case_summary": summary,
        "material": mat_resolve["material"],
        "solver_status": solver_status,
        "next_step": "run physics-simulate after model is built",
    }


def cmd_simulate(run_dir: str) -> dict:
    """在指定 run_dir 运行一轮仿真。"""
    state_file = os.path.join(run_dir, "_state.json")
    if not os.path.exists(state_file):
        return {"ok": False, "error": f"run directory not found: {run_dir}"}

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    lc_path = state.get("load_case_path", "")
    lc_result = load_case.load_from_file(lc_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    run_id = f"{os.path.basename(run_dir)}_iter_{len(state.get('runs', [])) + 1}"
    output_dir = os.path.join(run_dir, f"iteration_{len(state.get('runs', [])) + 1}")
    os.makedirs(output_dir, exist_ok=True)

    fea_result = fea_solver.solve_fea(lc_result["case"], output_dir=output_dir)

    domain = lc_result["case"].get("design_domain", {}).get("bounds", {})
    gc_result = geometry_gate.geometry_gate_report(
        bbox=[domain.get("x_min", 0), domain.get("x_max", 200),
              domain.get("y_min", 0), domain.get("y_max", 60),
              domain.get("z_min", 0), domain.get("z_max", 60)],
        topology={"volumes": 1, "bodies": 1, "faces": 6, "edges": 12, "vertices": 8},
        face_areas=[], volume_mm3=100000,
        design_bounds=domain, interfaces=[], design_params={},
    )

    report = sim_report.build_report(
        run_id=run_id, load_case=lc_result["case"], design_params={},
        geometry_check=gc_result, fea_result=fea_result,
        iteration=len(state.get("runs", [])) + 1,
        parent_run_id=state.get("current_run_id"),
    )
    report_path = sim_report.save_report(report, output_dir)
    report["report_path"] = report_path

    ds = design_state.DesignState(run_dir, lc_path)
    ds.new_run(run_id)
    ds.update_run(run_id, {
        "fea_result": fea_result, "geometry_check": gc_result,
        "report_path": report_path, "status": report["overall_status"],
    })

    return report


def _find_output_base() -> str:
    """定位 output 目录（兼容 DSH preset 和直接运行两种场景）。"""
    candidates = [
        os.path.join(_HERE, "..", "..", "output"),          # tools/../../output
        os.path.join(_HERE, "..", "output"),                # physics/../output (when run from physics/)
    ]
    for c in candidates:
        p = os.path.abspath(c)
        if os.path.isdir(p):
            return p
    # fallback: same dir as this file
    return os.path.abspath(os.path.join(_HERE, "..", "output"))


def _find_reports(run_id: str) -> list[str]:
    """在 output 目录下搜索匹配 run_id 的报告文件。"""
    out_base = _find_output_base()
    candidates = []
    for subdir in ("", design_state.STATE_DIR_NAME, "demo_run"):
        search_dir = os.path.join(out_base, subdir) if subdir else out_base
        if not os.path.isdir(search_dir):
            continue
        for root, _, files in os.walk(search_dir):
            for fn in files:
                if fn.endswith("_report.json") and run_id in fn:
                    candidates.append(os.path.join(root, fn))
    return candidates


def cmd_report(run_id: str) -> dict:
    """查找并返回指定 run_id 的仿真报告。"""
    # 先检查是否是直接文件路径
    if os.path.isfile(run_id):
        report_file = run_id
    else:
        report_file = None
        for path in _find_reports(run_id):
            if os.path.exists(path):
                report_file = path
                break

    if not report_file:
        return {"ok": False, "error": f"report not found for: {run_id}"}

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)
    md = sim_report.report_to_markdown(report)
    return {"ok": True, "command": "report", "report": report, "markdown_summary": md}


def cmd_recommend(run_id: str, max_iter: int = 3) -> dict:
    """为指定 run_id 生成修正建议。"""
    report_data = cmd_report(run_id)
    if not report_data.get("ok"):
        return report_data

    report = report_data["report"]
    design_params = report.get("design_params", {
        "thickness_mm": {"id": "thickness_mm", "value": 5.0, "min": 2, "max": 20},
        "height_mm": {"id": "height_mm", "value": 60.0, "min": 20, "max": 200},
        "fillet_mm": {"id": "fillet_mm", "value": 2.0, "min": 0.5, "max": 15},
    })
    locked = report.get("locked_params", [])

    result = refine_rules.generate_recommendations(
        report=report, design_params=design_params,
        locked_params=locked, max_iterations=max_iter,
    )
    result["command"] = "recommend"
    return result


def cmd_validate_domain(domain_id: str = "structural",
                        design_params: dict = None,
                        fea_result: dict = None) -> dict:
    """领域特定验证（DSVA）入口。

    调用示例:
        python physics_bridge.py validate-domain structural
        python sw_bridge.py physics-validate-domain transmission

    详见 engineering/tools/physics/domain_validator.py
    """
    try:
        import domain_validator
    except ImportError:
        return {"ok": False, "error": "domain_validator.py not found"}

    if design_params is None:
        design_params = {}
    if fea_result is None:
        fea_result = {}

    result = domain_validator.validate_with_domain(
        design_params=design_params,
        fea_result=fea_result,
        domain_id=domain_id,
    )
    result["command"] = "validate-domain"
    result["available_domains"] = [d["id"] for d in domain_validator.list_domains()]
    return result


def cmd_list_domains() -> dict:
    """列出所有可用的验证领域。"""
    try:
        import domain_validator
    except ImportError:
        return {"ok": False, "error": "domain_validator.py not found"}
    return {
        "ok": True,
        "command": "list-domains",
        "domains": domain_validator.list_domains(),
    }


def cmd_optimize(case_path: str, max_iter: int = 5) -> dict:
    """自动迭代优化：generate → simulate → check → refine 直到通过或达到 max_iter。"""
    lc_result = load_case.load_from_file(case_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    problem_id = lc_result["case"].get("meta", {}).get("problem_id", "UNKNOWN")
    run_dir = os.path.join(
        _HERE, "..", "..", "output", design_state.STATE_DIR_NAME,
        f"opt_{problem_id}_{int(time.time())}",
    )
    os.makedirs(run_dir, exist_ok=True)

    ds = design_state.DesignState(run_dir, case_path)
    design_params = {
        "thickness_mm": {"id": "thickness_mm", "value": 5.0, "min": 2, "max": 20},
        "height_mm": {"id": "height_mm", "value": 60.0, "min": 20, "max": 200},
        "width_mm": {"id": "width_mm", "value": 40.0, "min": 10, "max": 100},
        "fillet_mm": {"id": "fillet_mm", "value": 2.0, "min": 0.5, "max": 15},
    }
    locked_params = []

    final_report = None
    for i in range(1, max_iter + 1):
        run_id = f"opt_iter_{i}"
        output_dir = os.path.join(run_dir, f"iteration_{i}")
        os.makedirs(output_dir, exist_ok=True)

        fea_result = fea_solver.solve_fea(lc_result["case"], output_dir=output_dir)

        domain = lc_result["case"].get("design_domain", {}).get("bounds", {})
        gc_result = geometry_gate.geometry_gate_report(
            bbox=[domain.get("x_min", 0), domain.get("x_max", 200),
                  domain.get("y_min", 0), domain.get("y_max", 60),
                  domain.get("z_min", 0), domain.get("z_max", 60)],
            topology={"volumes": 1, "bodies": 1, "faces": 6, "edges": 12, "vertices": 8},
            face_areas=[], volume_mm3=100000,
            design_bounds=domain, interfaces=[], design_params={},
        )

        report = sim_report.build_report(
            run_id=run_id, load_case=lc_result["case"], design_params=design_params,
            geometry_check=gc_result, fea_result=fea_result, iteration=i,
            parent_run_id=f"opt_iter_{i-1}" if i > 1 else None,
        )
        report_path = sim_report.save_report(report, output_dir)
        report["report_path"] = report_path

        ds.new_run(run_id)
        ds.update_run(run_id, {
            "fea_result": fea_result, "geometry_check": gc_result,
            "design_params": design_params, "status": report["overall_status"],
        })

        final_report = report
        print(f"  [iter {i}] status={report['overall_status']} "
              f"SF={fea_result.get('safety_factor', 'N/A')} "
              f"stress={fea_result.get('max_von_mises_mpa', 'N/A')}MPa",
              file=sys.stderr, flush=True)

        if report["overall_status"] == "PASS":
            return {
                "ok": True, "command": "optimize",
                "converged": True, "iterations": i,
                "final_report": report, "run_dir": run_dir,
            }

        rec = refine_rules.generate_recommendations(
            report=report, design_params=design_params,
            locked_params=locked_params, max_iterations=max_iter - i,
        )
        if rec["ok"] and rec["actions"]:
            design_params = rec["next_params"]
        else:
            return {
                "ok": True, "command": "optimize",
                "converged": False, "iterations": i,
                "reason": "no valid refinement actions; design space exhausted",
                "final_report": report, "run_dir": run_dir,
            }

    return {
        "ok": True, "command": "optimize",
        "converged": False, "iterations": max_iter,
        "reason": f"reached max iterations ({max_iter})",
        "final_report": final_report, "run_dir": run_dir,
    }


def cmd_demo(args=None) -> dict:
    """运行悬臂梁演示：自动生成工况 → 解析解 → 报告。"""
    lc = load_case.default_load_case("DEMO_CANTILEVER", "演示悬臂梁")
    lc_result = load_case.validate_load_case(lc)
    if not lc_result["ok"]:
        return {"ok": False, "errors": lc_result["errors"]}

    run_dir = os.path.join(_HERE, "..", "..", "output", "demo_run")
    os.makedirs(run_dir, exist_ok=True)
    fea_result = fea_solver.solve_fea(lc, output_dir=run_dir)

    domain = lc.get("design_domain", {}).get("bounds", {})
    gc = geometry_gate.geometry_gate_report(
        bbox=[domain.get("x_min", 0), domain.get("x_max", 200),
              domain.get("y_min", 0), domain.get("y_max", 60),
              domain.get("z_min", 0), domain.get("z_max", 60)],
        topology={"volumes": 1, "bodies": 1, "faces": 6, "edges": 12, "vertices": 8},
        face_areas=[], volume_mm3=200 * 60 * 60,
        design_bounds=domain, interfaces=[], design_params={},
    )

    report = sim_report.build_report(
        run_id="demo_001", load_case=lc, design_params={},
        geometry_check=gc, fea_result=fea_result, iteration=1,
    )
    report_path = sim_report.save_report(report, run_dir)
    report["report_path"] = report_path

    md = sim_report.report_to_markdown(report)
    return {
        "ok": True, "command": "demo",
        "report": report, "markdown_summary": md,
        "report_path": report_path,
        "notes": (
            "这是解析解（Euler-Bernoulli 梁理论），仅用于快速估算。\n"
            "实际零件请使用 physics-optimize 配合完整载荷工况文件。"
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  CLI 入口（直接运行 physics_bridge.py 时使用）
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Physics-in-the-Loop CAD Engineering Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python physics_bridge.py demo
  python physics_bridge.py validate-case case.json
  python physics_bridge.py optimize case.json --max-iter 5
  python physics_bridge.py report opt_iter_3
  python physics_bridge.py recommend opt_iter_3
  python physics_bridge.py status
""",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="运行悬臂梁解析解演示")
    sub.add_parser("status", help="显示求解器后端状态")

    p = sub.add_parser("validate-case", help="校验载荷工况 JSON")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")
    p.add_argument("--relaxed", action="store_true", help="宽松校验（仅 warning 不报错）")

    p = sub.add_parser("build", help="加载工况并初始化仿真环境")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")

    p = sub.add_parser("simulate", help="运行仿真（需先有 run_dir）")
    p.add_argument("run_dir", help="运行目录路径")

    p = sub.add_parser("report", help="查看仿真报告")
    p.add_argument("run_id", help="运行 ID 或报告文件路径")

    p = sub.add_parser("recommend", help="生成修正建议")
    p.add_argument("run_id", help="运行 ID")
    p.add_argument("--max-iter", type=int, default=3, help="剩余最大迭代次数")

    p = sub.add_parser("optimize", help="自动迭代优化（generate→simulate→refine）")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")
    p.add_argument("--max-iter", type=int, default=5, help="最大迭代次数")

    args = parser.parse_args()
    cmd_map = {
        "demo": cmd_demo,
        "status": cmd_status,
        "validate-case": lambda a: cmd_validate_case(a.case_path, getattr(a, "relaxed", False)),
        "build": lambda a: cmd_build(a.case_path),
        "simulate": lambda a: cmd_simulate(a.run_dir),
        "report": lambda a: cmd_report(a.run_id),
        "recommend": lambda a: cmd_recommend(a.run_id, getattr(a, "max_iter", 3)),
        "optimize": lambda a: cmd_optimize(a.case_path, getattr(a, "max_iter", 5)),
    }
    result = cmd_map[args.cmd](args)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
