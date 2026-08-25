# -*- coding: utf-8 -*-
"""
mesh_adapter.py — STEP 导出与网格数据生成适配层
=================================================
负责将 SolidWorks 模型导出为 STEP，并生成可供 FEA 求解器使用的网格数据。

支持模式：
  - Level 0: 解析解（无需网格，直接计算）
  - Level 1: 杆系网格（2D truss/beam 元素）
  - Level 2: Gmsh STEP → MSH（需安装 gmsh）
  - Level 3: SolidWorks Simulation 原生（需 SW API）

本模块优先使用 Level 0/1，降级到 Level 2/3 仅在可用时。
"""
import os
import json
import subprocess
from typing import Any, Optional


# ==================== Level 0: 解析解（无网格） ====================

def analytical_beam_cantilever(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    force_n: float,
    E_mpa: float,
) -> dict[str, Any]:
    """悬臂梁解析解：端部集中力。

    Returns:
        {max_stress_mpa, max_deflection_mm, safety_factor, volume_mm3}
    """
    L = length_mm / 1000.0  # m
    b = width_mm / 1000.0
    h = height_mm / 1000.0
    F = force_n
    E = E_mpa  # Pa = N/m²

    # 截面惯性矩 I = b*h³/12
    I = b * h**3 / 12.0
    # 截面模量 W = b*h²/6
    W = b * h**2 / 6.0
    # 最大弯矩 M = F*L（固定端）
    M = F * L
    # 最大应力 σ = M/W
    sigma_max = M / W  # Pa
    # 最大挠度 δ = F*L³/(3EI)
    delta_max = F * L**3 / (3.0 * E * I)  # m

    return {
        "method": "analytical_cantilever_beam",
        "max_stress_mpa": round(sigma_max * 1e-6, 2),
        "max_deflection_mm": round(delta_max * 1000, 4),
        "volume_mm3": round(length_mm * width_mm * height_mm, 2),
        "notes": "first-order Euler-Bernoulli beam theory; ignore stress concentrations",
    }


def analytical_simply_supported_beam(
    span_mm: float,
    width_mm: float,
    height_mm: float,
    force_n: float,
    E_mpa: float,
) -> dict[str, Any]:
    """简支梁解析解：跨中集中力。"""
    L = span_mm / 1000.0
    b = width_mm / 1000.0
    h = height_mm / 1000.0
    F = force_n
    E = E_mpa

    I = b * h**3 / 12.0
    W = b * h**2 / 6.0
    M = F * L / 4.0  # 跨中最大弯矩
    sigma_max = M / W
    delta_max = F * L**3 / (48.0 * E * I)

    return {
        "method": "analytical_simply_supported_beam",
        "max_stress_mpa": round(sigma_max * 1e-6, 2),
        "max_deflection_mm": round(delta_max * 1000, 4),
        "volume_mm3": round(span_mm * width_mm * height_mm, 2),
    }


# ==================== Level 1: 杆系网格（简单框架） ====================

def build_truss_mesh(
    nodes: list[tuple[float, float, float]],
    elements: list[tuple[int, int]],
    E_mpa: float,
    area_mm2: float,
) -> dict[str, Any]:
    """构建 2D/3D 杆系网格数据（返回结构矩阵摘要）。

    仅用于简单 truss 结构的快速评估，不生成完整 MSH 文件。

    Returns:
        {num_nodes, num_elements, E_mpa, area_mm2, ready: bool}
    """
    return {
        "method": "truss_mesh_summary",
        "num_nodes": len(nodes),
        "num_elements": len(elements),
        "E_mpa": E_mpa,
        "cross_section_area_mm2": area_mm2,
        "ready": True,
        "note": "truss solver not yet implemented; use this as input for external solver",
    }


# ==================== Level 2: Gmsh STEP → MSH ====================

def check_gmsh_available() -> bool:
    """检查 Gmsh 是否已安装。"""
    try:
        result = subprocess.run(
            ["gmsh", "--version"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def export_step_from_swapi(part_path: str, step_path: str) -> dict[str, Any]:
    """通过 sw_bridge.py 导出 STEP。

    依赖：sw_bridge.py run 执行一个临时脚本导出 STEP。
    """
    import sys
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    bridge_script = os.path.join(tools_dir, "sw_bridge.py")

    script_content = f'''
import sys, os, win32com.client, pythoncom
sys.path.insert(0, r"{tools_dir}")
import swapi
sw = swapi.get_sw()
# Open part if not already open
for i in range(sw.GetDocumentCount):
    try:
        d = sw.GetDocument(i)
        if d and d.GetPathName == r"{part_path}":
            break
    except: pass
else:
    sw.OpenDoc(r"{part_path}", 1)
doc = sw.ActiveDoc
step_path = r"{step_path}"
try:
    rc = doc.SaveAs(step_path)
    print(f"STEP exported: {{step_path}} (rc={{rc}})")
except Exception as e:
    print(f"STEP export error: {{e}}", file=sys.stderr)
'''
    script_file = os.path.join(tools_dir, "_tmp_step_export.py")
    try:
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_content)
        result = subprocess.run(
            [sys.executable, script_file],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr or "SW export failed"}
        if os.path.exists(step_path):
            return {"ok": True, "step_path": step_path, "size_bytes": os.path.getsize(step_path)}
        return {"ok": False, "error": "STEP file not created"}
    finally:
        if os.path.exists(script_file):
            os.remove(script_file)


def generate_mesh_gmsh(step_path: str, msh_path: str, order: int = 1) -> dict[str, Any]:
    """使用 Gmsh 生成网格。

    Args:
        step_path: 输入的 STEP 文件路径
        msh_path: 输出的 MSH 文件路径
        order: 单元阶次（1=线性，2=二次）

    Returns:
        {ok, num_elements, num_nodes, element_types}
    """
    if not check_gmsh_available():
        return {"ok": False, "error": "Gmsh not found; install from https://gmsh.info"}

    gmsh_script = f'''
import gmsh
import sys

gmsh.initialize()
gmsh.model.add("mesh")
gmsh.merge(r"{step_path}")
gmsh.model.mesh.generate(3)  # 3D mesh
gmsh.option.setNumber("Mesh.Order", {order})
gmsh.write(r"{msh_path}")
gmsh.finalize()
print(f"Mesh generated: {{msh_path}}")
'''
    script_file = os.path.join(tools_dir, "_tmp_gmsh.py") if 'tools_dir' in dir() else None
    # Inline approach: write a temporary Python script
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(gmsh_script)
        tmp_script = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_script],
            capture_output=True, text=True, timeout=120
        )
        os.unlink(tmp_script)
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr, "stdout": result.stdout}
        if not os.path.exists(msh_path):
            return {"ok": False, "error": "MSH file not created"}
        return {"ok": True, "msh_path": msh_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==================== Level 3: SolidWorks Simulation API ====================

def check_sw_simulation_available(sw_app) -> bool:
    """检查 SolidWorks Simulation 插件是否可用。"""
    try:
        # Simulation 插件 GUID
        sim_guid = "{E3C2B4A0-6A1F-4B5E-9D8F-1A2B3C4D5E6F}"  # 占位符，实际需查询
        sw_app.GetAddInClient2(sim_guid)
        return True
    except Exception:
        pass
    # 尝试通过 COM 类名检测
    try:
        sw_app.GetAddInClient2("{3B0FF6F0-4B6E-11D4-B5E3-00C04F68DDB5}")
        return True
    except Exception:
        return False


def create_simulation_study(sw, model, study_name: str, material_id: str,
                            boundary_conditions: list, loads: list) -> dict[str, Any]:
    """创建 SolidWorks Simulation 静态分析研究。

    注意：此函数需要 SolidWorks Simulation 已安装且授权。
    """
    if not check_sw_simulation_available(sw):
        return {"ok": False, "error": "SolidWorks Simulation not available"}

    # 实际实现需要大量 COM API 调用，此处返回降级提示
    return {
        "ok": False,
        "error": "SolidWorks Simulation API implementation pending; "
                 "use Level 0/1/2 analysis instead",
        "fallback": "analytical or gmsh-based solver",
    }


# ==================== 统一接口 ====================

def adapt_mesh(
    part_path: str,
    load_case: dict,
    output_dir: str,
    level: int = 0,
) -> dict[str, Any]:
    """统一网格适配入口。

    Args:
        part_path: SolidWorks 零件路径
        load_case: 载荷工况字典（来自 load_case.py）
        output_dir: 输出目录
        level: 0=解析, 1=杆系, 2=Gmsh, 3=SW Simulation

    Returns:
        {ok, method, mesh_path, stats}
    """
    os.makedirs(output_dir, exist_ok=True)
    mat = load_case.get("material", {})
    E = mat.get("youngs_modulus_mpa", 200000)

    if level == 0:
        # 解析解模式
        domain = load_case.get("design_domain", {}).get("bounds", {})
        loads = load_case.get("loads", [])
        total_force = sum(l.get("magnitude_n", 0) for l in loads
                          if l.get("type") in ("distributed_force", "concentrated_force"))
        result = analytical_beam_cantilever(
            length_mm=domain.get("x_max", 200),
            width_mm=domain.get("y_max", 60),
            height_mm=domain.get("z_max", 60),
            force_n=total_force,
            E_mpa=E,
        )
        result["load_case"] = load_case.get("meta", {}).get("problem_id", "unknown")
        return {"ok": True, "method": "analytical", "result": result}

    elif level == 1:
        # 杆系模式（暂不支持自动网格生成）
        return {"ok": False, "error": "truss mesh not yet fully implemented"}

    elif level == 2:
        # Gmsh 模式
        step_path = os.path.join(output_dir, "model.step")
        msh_path = os.path.join(output_dir, "model.msh")

        step_result = export_step_from_swapi(part_path, step_path)
        if not step_result.get("ok"):
            return step_result

        return generate_mesh_gmsh(step_path, msh_path, order=1)

    else:
        return {"ok": False, "error": f"unknown mesh level: {level}"}
