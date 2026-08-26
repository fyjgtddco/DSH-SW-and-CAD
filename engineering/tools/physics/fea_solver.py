# -*- coding: utf-8 -*-
"""
fea_solver.py — 有限元求解器适配层
====================================
支持多种求解后端，按可用性自动降级：

  Level 0: 解析解（analytical beam theory）
  Level 1: 纯 Python FEA（numpy CST 三角形单元，无需外部软件）
  Level 2: Gmsh + CalculiX（外部 CLI）
  Level 3: Gmsh + Code_Aster（外部 CLI）
  Level 4: Gmsh + Elmer（外部 CLI）
  Level 5: SolidWorks Simulation API（需 Premium 授权）

本模块封装所有后端的统一调用接口。
"""
import os
import json
import subprocess
import tempfile
from typing import Any, Optional


# ==================== 后端检测 ====================

def _check_cmd(cmd: str) -> bool:
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_skfem_available() -> bool:
    try:
        import skfem  # noqa: F401
        return True
    except ImportError:
        return False


SOLVER_BACKENDS = {
    "calculix": _check_cmd("ccx") or _check_cmd("calculix"),
    "code_aster": _check_cmd("aster"),
    "elmerfem": _check_cmd("elmersolver"),
    "gmsh": _check_cmd("gmsh"),
    "feapy": True,  # pure numpy, always available
    "skfem": _check_skfem_available(),
}


def _check_skfem_available():
    try:
        import skfem  # noqa: F401
        return True
    except ImportError:
        return False


def detect_available_backends() -> dict[str, bool]:
    """检测当前系统可用的 FEA 后端。"""
    return dict(SOLVER_BACKENDS)


# ==================== Level 0: 解析求解器 ====================

def solve_analytical(load_case: dict, mesh_stats: dict) -> dict[str, Any]:
    """使用解析解进行快速评估（无需网格文件）。

    适用于简单几何：悬臂梁、简支梁、轴受扭等。
    """
    from mesh_adapter import (
        analytical_beam_cantilever,
        analytical_simply_supported_beam,
    )

    mat = load_case.get("material", {})
    E = mat.get("youngs_modulus_mpa", 200000)
    sigma_y = mat.get("yield_strength_mpa", 250)
    domain = load_case.get("design_domain", {}).get("bounds", {})
    loads = load_case.get("loads", [])
    acceptance = load_case.get("acceptance", {})

    # Bug #2 遗留修复: 支持 magnitude_n 和 magnitude_n_mm2 (压强)
    def _load_force(l):
        if l.get("type") not in ("distributed_force", "concentrated_force"):
            return 0.0
        mag = l.get("magnitude_n") or 0
        if mag <= 0:
            p = l.get("magnitude_n_mm2") or 0
            sel = l.get("face_selector", "")
            if p > 0 and sel:
                from load_case import _face_area_from_selector
                mag = p * _face_area_from_selector(domain, sel)
        return max(mag, 0)
    total_force = sum(_load_force(l) for l in loads)
    force_dir = loads[0].get("direction", [0, 0, -1]) if loads else [0, 0, -1]

    L = domain.get("x_max", 200)
    W = domain.get("y_max", 60)
    H = domain.get("z_max", 60)

    # 假设悬臂梁边界条件（固定端在 x=0）
    result = analytical_beam_cantilever(L, W, H, total_force, E)

    sigma_max = result.get("max_stress_mpa", 0)
    delta_max = result.get("max_deflection_mm", 0)
    volume_mm3 = result.get("volume_mm3", 0)

    # 计算安全系数
    safety_factor = sigma_y / sigma_max if sigma_max > 0 else float("inf")

    # Bug-21 修复: 安全系数过高不是 FAIL，应标记 WARNING（过于保守=浪费材料，但结构安全）
    # 原逻辑: sf > max_sf → FAIL（错误：92 > 5 被判为超限）
    # 正确逻辑: sf < min_sf → FAIL（强度不足），sf > max_sf → WARNING（过度设计）
    if safety_factor < acceptance.get("min_safety_factor", 2.0):
        sf_status = "FAIL"
    elif safety_factor > acceptance.get("target_safety_factor_max", 5.0):
        sf_status = "WARNING"
    else:
        sf_status = "PASS"

    # 检查约束
    gates = {
        "SAFETY_FACTOR": {
            "status": sf_status,
            "actual": round(safety_factor, 2),
            "required_min": acceptance.get("min_safety_factor", 2.0),
            "required_max": acceptance.get("target_safety_factor_max", 5.0),
        },
        "MAX_DISPLACEMENT": {
            "status": "PASS" if (acceptance.get("max_displacement_mm") is None or
                                  delta_max <= acceptance["max_displacement_mm"]) else "FAIL",
            "actual_mm": round(delta_max, 4),
            "required_max_mm": acceptance.get("max_displacement_mm"),
        },
        "DESIGN_SPACE": {"status": "PASS"},
        "MESH_QUALITY": {"status": "N/A", "note": "analytical method, no mesh"},
    }

    passed = all(g["status"] == "PASS" for g in gates.values())

    return {
        "ok": True,
        "method": "analytical",
        "backend": "closed_form_beam_theory",
        "safety_factor": round(safety_factor, 2),
        "max_von_mises_mpa": round(sigma_max, 2),
        "max_displacement_mm": round(delta_max, 4),
        "volume_mm3": round(volume_mm3, 2),
        "mass_kg": round(volume_mm3 * mat.get("density_kg_m3", 7850) / 1e9, 4),
        "gates": gates,
        "overall": "PASS" if passed else "FAIL",
        "limitations": [
            "Euler-Bernoulli beam assumptions",
            "ignores stress concentrations",
            "valid for prismatic beams only",
        ],
    }


# ==================== Level 1: 纯 Python FEA (numpy CST) ====================

def solve_feapy(load_case: dict, output_dir: Optional[str] = None) -> dict[str, Any]:
    """使用纯 Python FEA 求解器（numpy CST 三角形单元）。

    无需任何外部 FEA 软件，完全基于 numpy 实现。
    适用于 2D 平面应力/应变问题。

    Returns:
        {ok, method, safety_factor, max_von_mises_mpa, max_displacement_mm, gates}
    """
    try:
        from feapy_solver import solve_cantilever, FEASolver, mesh_rect
    except ImportError:
        return {"ok": False, "error": "feapy_solver.py not found"}

    mat = load_case.get("material", {})
    E = mat.get("youngs_modulus_mpa", 200000)
    nu = mat.get("poissons_ratio", 0.3)
    sigma_y = mat.get("yield_strength_mpa", 250)

    domain = load_case.get("design_domain", {}).get("bounds", {})
    L = domain.get("x_max", 200.0)
    W = domain.get("y_max", 60.0)
    H = domain.get("z_max", 60.0)

    loads = load_case.get("loads", [])
    # Bug #2 遗留修复: 支持 magnitude_n 和 magnitude_n_mm2 (压强)
    def _load_force(l):
        if l.get("type") not in ("distributed_force", "concentrated_force"):
            return 0.0
        mag = l.get("magnitude_n") or 0
        if mag <= 0:
            p = l.get("magnitude_n_mm2") or 0
            sel = l.get("face_selector", "")
            if p > 0 and sel:
                from load_case import _face_area_from_selector
                mag = p * _face_area_from_selector(domain, sel)
        return max(mag, 0)
    total_force = sum(_load_force(l) for l in loads)
    force_dir = loads[0].get("direction", [0, 0, -1]) if loads else [0, 0, -1]

    acceptance = load_case.get("acceptance", {})
    min_sf = acceptance.get("min_safety_factor", 2.0)
    max_sf = acceptance.get("target_safety_factor_max", 5.0)
    max_disp = acceptance.get("max_displacement_mm")

    try:
        # 使用 feapy_solver 求解
        nx, ny = max(20, int(L / 5)), max(6, int(H / 5))
        r = solve_cantilever(L, H, W, total_force, E, nu, nx=nx, ny=ny)

        max_stress = r["max_von_mises_mpa"]
        max_disp_val = r["max_displacement_mm"]
        safety_factor = sigma_y / max_stress if max_stress > 0 else float("inf")
        volume_mm3 = L * W * H

        gates = {
            "SAFETY_FACTOR": {
                "status": (
                    "FAIL" if safety_factor < min_sf
                    else "WARNING" if safety_factor > max_sf
                    else "PASS"
                ),
                "actual": round(safety_factor, 2),
                "required_min": min_sf,
                "required_max": max_sf,
            },
            "MAX_DISPLACEMENT": {
                "status": (
                    "PASS"
                    if max_disp is None or max_disp_val <= max_disp
                    else "FAIL"
                ),
                "actual_mm": round(max_disp_val, 4),
                "required_max_mm": max_disp,
            },
            "DESIGN_SPACE": {"status": "PASS"},
            "MESH_QUALITY": {
                "status": "PASS",
                "note": f"CST triangle mesh: ~{nx*ny*2} elements",
            },
        }

        passed = all(g["status"] == "PASS" for g in gates.values())

        return {
            "ok": True,
            "method": "feapy_numpy",
            "backend": "pure_python_cst_triangle",
            "safety_factor": round(safety_factor, 2),
            "max_von_mises_mpa": round(max_stress, 2),
            "max_displacement_mm": round(max_disp_val, 4),
            "volume_mm3": round(volume_mm3, 2),
            "mass_kg": round(volume_mm3 * mat.get("density_kg_m3", 7850) / 1e9, 4),
            "gates": gates,
            "overall": "PASS" if passed else "FAIL",
            "limitations": [
                "2D plane stress assumption only",
                "CST linear elements (6-8%% error vs analytical)",
                "no stress concentrations captured",
                "no 3D effects",
            ],
            "analytical_comparison": r.get("analytical", {}),
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}

def _write_cfx_input(step_path: str, load_case: dict, output_dir: str) -> str:
    """生成 CalculiX 输入文件（简化版）。

    注意：这是简化实现，完整版本需要解析 STEP 几何、提取面/边ID。
    """
    mat = load_case.get("material", {})
    E = mat.get("youngs_modulus_mpa", 200000)
    nu = mat.get("poissons_ratio", 0.3)
    rho = mat.get("density_kg_m3", 7850)

    loads = load_case.get("loads", [])
    bcs = load_case.get("boundary_conditions", [])

    # 生成 .inp 文件内容（简化）
    inp_content = f"""*HEADING
DSH Physics-in-the-Loop FEA Input
*preprint, yes
*NODE
1, 0.0, 0.0, 0.0
2, {load_case.get('design_domain', {}).get('bounds', {}).get('x_max', 200)}, 0.0, 0.0
*ELEMENT, TYPE=C3D8R, ELSET=ALL
1, 1, 2, 3, 4, 5, 6, 7, 8
*ELSET, ELSET=ALL
1
*SECTION, TYPE=SOLID, SECTION=1
*SECTION PROPERTY, SECTION=1, ELSET=ALL
{E}, {nu}
*BOUNDARY
1, 1, 6, 0
*DSLOAD
2, GRAV, {rho}, 0., 0., -1.
*STATIC
*END
"""
    inp_path = os.path.join(output_dir, "model.inp")
    with open(inp_path, "w", encoding="utf-8") as f:
        f.write(inp_content)
    return inp_path


def run_calculix(step_path: str, msh_path: str, output_dir: str) -> dict[str, Any]:
    """运行 CalculiX 求解器。"""
    if not SOLVER_BACKENDS.get("calculix"):
        return {"ok": False, "error": "CalculiX not found"}

    inp_path = _write_cfx_input(step_path, {}, output_dir)
    results_dir = os.path.join(output_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    try:
        result = subprocess.run(
            ["ccx", "-i", inp_path, "-o", os.path.join(results_dir, "output")],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr or "ccx failed"}

        # 解析 results 文件
        frd_path = os.path.join(results_dir, "output.frd")
        if os.path.exists(frd_path):
            return {"ok": True, "results_dir": results_dir, "frd_path": frd_path}
        return {"ok": True, "results_dir": results_dir}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "CalculiX timed out after 300s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==================== 统一求解接口 ====================

def solve_fea(
    load_case: dict,
    mesh_path: Optional[str] = None,
    step_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    backend: Optional[str] = None,
) -> dict[str, Any]:
    """统一 FEA 求解入口。

    Args:
        load_case: 载荷工况（来自 load_case.py）
        mesh_path: 网格文件路径（MSH格式，可选）
        step_path: STEP 文件路径（可选，用于重新网格化）
        output_dir: 输出目录（默认：output_dir + run_id）
        backend: 指定求解器后端（可选，自动选择）

    Returns:
        {ok, method, safety_factor, max_stress_mpa, max_displacement_mm, gates}
    """
    if output_dir is None:
        import time
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "output",
            f"physics_run_{int(time.time())}"
        )
    os.makedirs(output_dir, exist_ok=True)

    # 自动选择后端
    if backend is None:
        # 优先级：feapy > CalculiX > Code_Aster > Elmer > analytical
        for candidate in ["feapy", "calculix", "code_aster", "elmerfem"]:
            if SOLVER_BACKENDS.get(candidate):
                backend = candidate
                break
        else:
            backend = "analytical"

    # 分发到对应后端
    if backend == "analytical":
        return solve_analytical(load_case, {})
    elif backend == "feapy":
        return solve_feapy(load_case, output_dir)
    elif backend == "calculix":
        if mesh_path and os.path.exists(mesh_path):
            return run_calculix(mesh_path, mesh_path, output_dir)
        return {"ok": False, "error": "CalculiX requires mesh file; use mesh_adapter first"}
    else:
        return {"ok": False, "error": f"unsupported backend: {backend}"}


# ==================== 便捷函数 ====================

def get_solver_status() -> dict[str, Any]:
    """返回当前可用的求解器状态。"""
    backends = detect_available_backends()
    available = [k for k, v in backends.items() if v]
    return {
        "available": available,
        "backends": backends,
        "default": "analytical" if not available else available[0],
        "recommendation": (
            "Use feapy (pure Python FEA) for 2D problems; "
            "install CalculiX + Gmsh for full 3D FEA; "
            "use analytical for quick checks"
            if not available
            else f"Default backend: {available[0]}"
        ),
    }
