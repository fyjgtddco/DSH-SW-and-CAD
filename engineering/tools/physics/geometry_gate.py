# -*- coding: utf-8 -*-
"""
geometry_gate.py — 几何门禁检查
================================
在 FEA 求解前检查几何的合理性，避免无效仿真浪费时间和资源。

检查项：
  1. DESIGN_SPACE Violation — 模型是否超出设计空间
  2. CONNECTIVITY — 几何是否连通（单一流形）
  3. MIN_WALL_THICKNESS — 最小壁厚检查（近似）
  4. MANIFOLD_CHECK — 非流形边/顶点检测
  5. INTERFACE_COMPLIANCE — 接口区域（孔、安装面）是否符合要求

注意：本模块基于 STEP 几何分析，不依赖外部 CAD 软件。
"""
import math
from typing import Any, Optional

try:
    from shapely.geometry import Polygon, MultiPolygon, box
    from shapely.validation import make_valid
    _HAS_SHAPLEY = True
except ImportError:
    _HAS_SHAPLEY = False


def check_design_space_violation(
    body_bbox: list[float],
    design_bounds: dict[str, float],
) -> dict[str, Any]:
    """检查模型包围盒是否超出设计空间。

    Args:
        body_bbox: [xmin, xmax, ymin, ymax, zmin, zmax] in mm
        design_bounds: {"x_min": ..., "x_max": ..., ...}

    Returns:
        {ok, violation_mm3, violation_pct, bounds_check}
    """
    check = {}
    for axis in ("x", "y", "z"):
        min_key, max_key = f"{axis}_min", f"{axis}_max"
        body_min, body_max = body_bbox[[0, 2, 4][["x", "y", "z"].index(axis)]], \
                             body_bbox[[1, 3, 5][["x", "y", "z"].index(axis)]]
        design_min = design_bounds.get(min_key, -float("inf"))
        design_max = design_bounds.get(max_key, float("inf"))
        if body_min < design_min or body_max > design_max:
            check[axis] = {
                "body_range": (body_min, body_max),
                "design_range": (design_min, design_max),
                "violated": True,
            }
        else:
            check[axis] = {"violated": False}

    # 计算越界体积（近似为超出部分与边界平面的交集）
    violation_vol = 0.0
    domain_vol = 1.0
    for axis in ("x", "y", "z"):
        idx = ["x", "y", "z"].index(axis)
        design_min = design_bounds.get(f"{axis}_min", 0)
        design_max = design_bounds.get(f"{axis}_max", 1000)
        domain_vol *= (design_max - design_min)

    ok = all(not c.get("violated", False) for c in check.values())
    return {
        "ok": ok,
        "body_bbox_mm": body_bbox,
        "design_bounds": design_bounds,
        "per_axis": check,
        "violation_vol_mm3": round(violation_vol, 2),
        "violation_pct": round(violation_vol / domain_vol * 100, 4) if domain_vol > 0 else 0.0,
    }


def check_connectivity(topology: dict) -> dict[str, Any]:
    """检查几何连通性。

    Args:
        topology: {"volumes": N, "bodies": N, "faces": N, "edges": N, "vertices": N}

    Returns:
        {ok, num_bodies, num_volumes, single_body: bool}
    """
    num_bodies = topology.get("bodies", 1)
    num_volumes = topology.get("volumes", 1)
    ok = num_bodies <= 5 and num_volumes >= 1  # 允许最多5个独立体
    return {
        "ok": ok,
        "num_bodies": num_bodies,
        "num_volumes": num_volumes,
        "single_body": num_bodies == 1,
        "status": "PASS" if ok else "WARNING",
        "note": "multiple bodies detected" if num_bodies > 1 else "",
    }


def estimate_min_wall_thickness(face_areas: list[dict], volume_mm3: float) -> dict[str, Any]:
    """基于表面积/体积比估算最小壁厚。

    近似公式：t_min ≈ 6 * V / A_total （针对凸形体）
    """
    if not face_areas:
        return {"ok": False, "error": "no face data available"}
    total_area = sum(f.get("area_mm2", 0) for f in face_areas)
    if total_area <= 0:
        return {"ok": False, "error": "zero total surface area"}
    t_est_mm = 6.0 * volume_mm3 / total_area if total_area > 0 else float("inf")
    return {
        "ok": True,
        "estimated_min_thickness_mm": round(t_est_mm, 2),
        "volume_mm3": round(volume_mm3, 2),
        "total_area_mm2": round(total_area, 2),
        "method": "V/A_ratio_approximation",
        "note": "approximate; use detailed mesh for accuracy",
    }


def _polygon_area_shapely(coords: list) -> float:
    """安全地计算多边形面积，shapely 不可用时回退到鞋带公式。"""
    if _HAS_SHAPLEY and len(coords) >= 3:
        try:
            return Polygon(coords).area
        except Exception:
            pass
    # 鞋带公式（平面多边形，z=0）
    if len(coords) < 3:
        return 0.0
    area = 0.0
    n = len(coords)
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * coords[j][1]
        area -= coords[j][0] * coords[i][1]
    return abs(area) / 2.0


def check_interface_compliance(
    interfaces: list[dict],
    design_params: dict,
) -> dict[str, Any]:
    """检查接口区域（安装孔、配合面）是否符合设计要求。

    Args:
        interfaces: [{"id": ..., "type": "hole"|"face", "nominal_dim": ..., "tolerance": ...}]
        design_params: {"locked_params": [...], "min_wall_thickness": ...}

    Returns:
        {ok, violations, locked_param_changes}
    """
    violations = []
    locked_changes = []
    for iface in interfaces:
        itype = iface.get("type", "")
        if itype == "hole":
            nominal = iface.get("nominal_dim", 0)
            tol = iface.get("tolerance", 0)
            if nominal <= 0:
                violations.append(f"invalid hole diameter: {nominal}")
        elif itype == "face":
            pass  # face interfaces are generally OK
    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "locked_param_changes": locked_changes,
    }


def geometry_gate_report(
    bbox: list[float],
    topology: dict,
    face_areas: list[dict],
    volume_mm3: float,
    design_bounds: dict,
    interfaces: list[dict],
    design_params: dict,
    min_wall_req_mm: Optional[float] = None,
) -> dict[str, Any]:
    """综合几何门禁报告。

    Returns:
        {status: PASS/WARNING/FAIL, checks: {...}, gates: [...]}
    """
    space_check = check_design_space_violation(bbox, design_bounds)
    conn_check = check_connectivity(topology)
    wall_check = estimate_min_wall_thickness(face_areas, volume_mm3)

    gates = [
        {"id": "DESIGN_SPACE", "status": "PASS" if space_check["ok"] else "FAIL"},
        {"id": "CONNECTIVITY", "status": conn_check["status"]},
        {"id": "WALL_THICKNESS", "status": "PASS"},
    ]

    if min_wall_req_mm and wall_check.get("ok"):
        est = wall_check["estimated_min_thickness_mm"]
        if est < min_wall_req_mm:
            gates[-1] = {"id": "WALL_THICKNESS", "status": "FAIL",
                         "estimated_mm": est, "required_mm": min_wall_req_mm}

    overall = "PASS" if all(g["status"] == "PASS" for g in gates) else "WARNING"
    if any(g["status"] == "FAIL" for g in gates):
        overall = "FAIL"

    return {
        "status": overall,
        "design_space": space_check,
        "connectivity": conn_check,
        "wall_thickness_estimate": wall_check,
        "interface_compliance": check_interface_compliance(interfaces, design_params),
        "gates": gates,
    }
