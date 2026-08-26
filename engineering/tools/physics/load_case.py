# -*- coding: utf-8 -*-
"""
load_case.py — 载荷工况 JSON 解析、校验与标准化
=================================================
将用户的自然语言需求转化为结构化的 load_case JSON，并对每一字段做语义校验。
"""
import json
import os
import math
from typing import Any, Optional

import material_db


# ==================== 默认值 ====================
DEFAULT_ACCEPTANCE = {
    "analysis_type": "linear_static",
    "min_safety_factor": 2.0,
    "target_safety_factor_max": 5.0,
    "max_displacement_mm": None,      # 可选
    "min_wall_thickness_mm": None,    # 可选
    "max_mass_kg": None,              # 可选
    "must_remain_in_design_domain": True,
    "mesh_quality_min": 0.1,          # 仅 Level 2/3
}

VALID_LOAD_TYPES = {
    "distributed_force", "concentrated_force", "moment",
    "pressure", "thermal", "gravity",
}
VALID_BC_TYPES = {
    "fixed_displacement", "pinned", "roller", "slider",
    "symmetry", "coupled_displacement",
}


def default_load_case(problem_id: str = "DEMO_BEAM", title: str = "示例悬臂梁") -> dict:
    """生成一个最小可用的悬臂梁工况，用于回归测试和演示。"""
    return {
        "schema_version": "1.0",
        "meta": {
            "problem_id": problem_id,
            "title": title,
            "revision": "A",
        },
        "units": {"length": "mm", "force": "N", "stress": "MPa", "mass": "kg"},
        "design_domain": {
            "bounds": {"x_min": 0.0, "x_max": 200.0,
                        "y_min": 0.0, "y_max": 60.0,
                        "z_min": 0.0, "z_max": 60.0},
            "keep_in_regions": [],
            "keep_out_regions": [],
        },
        "material": {
            "id": "AL_6061_T6",
            "name": "Aluminum 6061-T6",
            "youngs_modulus_mpa": 68900.0,
            "poissons_ratio": 0.33,
            "yield_strength_mpa": 276.0,
            "density_kg_m3": 2700.0,
            "source": "user_confirmed",
        },
        "spatial_selectors": [
            {
                "id": "fixed_end",
                "type": "box",
                "bounds": {"x_min": 0.0, "x_max": 10.0,
                            "y_min": -10.0, "y_max": 70.0,
                            "z_min": -10.0, "z_max": 70.0},
                "selection_rule": "faces_intersecting_region",
            },
            {
                "id": "load_face",
                "type": "box",
                "bounds": {"x_min": 190.0, "x_max": 200.0,
                            "y_min": -10.0, "y_max": 70.0,
                            "z_min": -10.0, "z_max": 70.0},
                "selection_rule": "faces_intersecting_region",
            },
        ],
        "boundary_conditions": [
            {
                "id": "bc_fixed",
                "spatial_selector_id": "fixed_end",
                "type": "fixed_displacement",
                "dof_lock": {"x": True, "y": True, "z": True,
                              "rx": True, "ry": True, "rz": True},
            }
        ],
        "loads": [
            {
                "id": "load_force",
                "spatial_selector_id": "load_face",
                "type": "distributed_force",
                "magnitude_n": 500.0,
                "direction": [0.0, 0.0, -1.0],
            }
        ],
        "acceptance": dict(DEFAULT_ACCEPTANCE),
        "optimization": {
            "primary": "minimize_mass",
            "secondary": ["minimize_max_displacement"],
        },
    }


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    """安全获取 dict 字段，若 d 不是 dict 则返回 default。"""
    if isinstance(d, dict):
        return d.get(key, default)
    return default


def validate_load_case(data: dict, strict: bool = True) -> dict:
    """校验载荷工况 JSON，返回 {ok, errors, warnings, case}。"""
    errors, warnings = [], []

    # 类型检查：data 必须是 dict
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["root element must be a JSON object (dict)"], "warnings": [], "case": None}

    # --- schema_version ---
    ver = _safe_get(data, "schema_version", "")
    if ver != "1.0":
        warnings.append(f"unsupported schema_version: {ver!r}")

    # --- meta ---
    meta = _safe_get(data, "meta", {})
    if not isinstance(meta, dict):
        errors.append("meta must be a JSON object (dict), got " + type(meta).__name__)
        meta = {}
    if not meta.get("problem_id"):
        errors.append("meta.problem_id is required")
    if not meta.get("title"):
        warnings.append("meta.title recommended")

    # --- units ---
    units = _safe_get(data, "units", {})
    if not isinstance(units, dict):
        errors.append("units must be a JSON object (dict), got " + type(units).__name__)
        units = {}
    for k, v in units.items():
        if k not in ("length", "force", "stress", "mass"):
            warnings.append(f"unknown unit key: {k!r}")
    if units.get("length") != "mm":
        warnings.append("non-mm length unit detected; convert to mm for consistency")

    # --- design_domain ---
    dd = _safe_get(data, "design_domain", {})
    if not isinstance(dd, dict):
        errors.append("design_domain must be a JSON object (dict), got " + type(dd).__name__)
        dd = {}
    bounds = _safe_get(dd, "bounds", {})
    if not isinstance(bounds, dict):
        errors.append("design_domain.bounds must be a JSON object (dict), got " + type(bounds).__name__)
        bounds = {}
    required_bounds = ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]
    for b in required_bounds:
        if b not in bounds:
            errors.append(f"design_domain.bounds.{b} is required")
    if all(k in bounds for k in required_bounds):
        for k1, k2 in [("x_min", "x_max"), ("y_min", "y_max"), ("z_min", "z_max")]:
            if bounds[k1] >= bounds[k2]:
                errors.append(f"design_domain.bounds.{k1} >= bounds.{k2} (invalid domain)")

    # --- material ---
    mat = _safe_get(data, "material", {})
    if not isinstance(mat, dict):
        errors.append("material must be a JSON object (dict), got " + type(mat).__name__)
        mat = {}
    if mat:
        for field in ("youngs_modulus_mpa", "yield_strength_mpa", "density_kg_m3"):
            if field not in mat:
                if strict:
                    errors.append(f"material.{field} is required")
                else:
                    warnings.append(f"material.{field} missing; using approximate default")
        if mat.get("youngs_modulus_mpa", 0) <= 0:
            errors.append("material.youngs_modulus_mpa must be positive")
        if mat.get("yield_strength_mpa", 0) <= 0:
            errors.append("material.yield_strength_mpa must be positive")

    # --- spatial_selectors ---
    selectors = data.get("spatial_selectors", [])
    if not isinstance(selectors, list):
        errors.append("spatial_selectors must be a JSON array (list)")
        selectors = []
    sel_ids = {s.get("id") for s in selectors if isinstance(s, dict) and "id" in s}
    for s in selectors:
        if not isinstance(s, dict):
            errors.append("each spatial_selector must be a JSON object (dict)")
            continue
        if "id" not in s:
            errors.append("each spatial_selector must have an 'id'")
        if "bounds" not in s and "type" not in s:
            warnings.append(f"spatial_selector {s.get('id', '?')} has no geometry")

    # --- boundary_conditions ---
    bcs = data.get("boundary_conditions", [])
    if not isinstance(bcs, list):
        errors.append("boundary_conditions must be a JSON array (list)")
        bcs = []
    for bc in bcs:
        if not isinstance(bc, dict):
            errors.append("each boundary_condition must be a JSON object (dict)")
            continue
        if bc.get("type") not in VALID_BC_TYPES:
            errors.append(f"invalid BC type: {bc.get('type')!r}")
        sid = bc.get("spatial_selector_id")
        if sid and sid not in sel_ids:
            errors.append(f"BC references unknown selector '{sid}'")
        dof = bc.get("dof_lock", {})
        if not isinstance(dof, dict):
            errors.append(f"boundary_condition.dof_lock must be a JSON object (dict)")
            dof = {}
        # Bug-22 修复: fixed_displacement DOF 检查改为 warning（非硬性要求）
        if bc.get("type") == "fixed_displacement" and not any(dof.values()):
            warnings.append("fixed_displacement BC has no locked DOFs; consider adding at least one")

    # --- loads ---
    loads = data.get("loads", [])
    if not isinstance(loads, list):
        errors.append("loads must be a JSON array (list)")
        loads = []
    for load in loads:
        if not isinstance(load, dict):
            errors.append("each load must be a JSON object (dict)")
            continue
        if load.get("type") not in VALID_LOAD_TYPES:
            errors.append(f"invalid load type: {load.get('type')!r}")
        sid = load.get("spatial_selector_id")
        if sid and sid not in sel_ids:
            errors.append(f"load references unknown selector '{sid}'")
        if load.get("type") in ("distributed_force", "concentrated_force") and not load.get("magnitude_n"):
            # Bug-22 修复: force load missing magnitude_n 改为 warning
            warnings.append(f"force load {load.get('id', '?')} missing magnitude_n; using default 1.0N")
        if "direction" not in load:
            warnings.append(f"load {load.get('id', '?')} missing direction vector")

    # --- acceptance ---
    acc = data.get("acceptance", dict(DEFAULT_ACCEPTANCE))
    if not isinstance(acc, dict):
        errors.append("acceptance must be a JSON object (dict)")
        acc = dict(DEFAULT_ACCEPTANCE)
    min_sf = acc.get("min_safety_factor", 2.0)
    if min_sf <= 0:
        errors.append("acceptance.min_safety_factor must be positive")
    max_sf = acc.get("target_safety_factor_max", 5.0)
    if max_sf <= min_sf:
        warnings.append("target_safety_factor_max <= min_safety_factor; range may be empty")
    max_disp = acc.get("max_displacement_mm")
    if max_disp is not None and max_disp <= 0:
        errors.append("acceptance.max_displacement_mm must be positive")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "case": data if len(errors) == 0 else None,
    }


def _face_area_from_selector(bounds: dict, selector: str) -> float:
    """根据 face_selector 返回对应面的面积 (mm²)。

    Bug #2 遗留修复: 支持将 magnitude_n_mm2 (压强) 转换为 magnitude_n (总力)。
    """
    if not isinstance(selector, str):
        return 0.0
    s = selector.lower().replace("_", "")
    if s in ("xmax", "xmin"):
        return max(bounds.get("y_max", 0) - bounds.get("y_min", 0), 0) * \
               max(bounds.get("z_max", 0) - bounds.get("z_min", 0), 0)
    if s in ("ymax", "ymin"):
        return max(bounds.get("x_max", 0) - bounds.get("x_min", 0), 0) * \
               max(bounds.get("z_max", 0) - bounds.get("z_min", 0), 0)
    if s in ("zmax", "zmin"):
        return max(bounds.get("x_max", 0) - bounds.get("x_min", 0), 0) * \
               max(bounds.get("y_max", 0) - bounds.get("y_min", 0), 0)
    return 0.0


def _normalize_force_loads(case: dict) -> None:
    """归一化载荷：处理 magnitude_n / magnitude_n_mm2 / 缺失值。

    Bug #2 遗留修复:
    1. magnitude_n_mm2 (压强 N/mm²) → 乘以 face 面积 → magnitude_n (总力 N)
    2. magnitude_n 缺失/≤0 → 默认 1.0N
    """
    bounds = case.get("design_domain", {}).get("bounds", {})
    for load in case.get("loads", []):
        if load.get("type") not in ("distributed_force", "concentrated_force"):
            continue
        if load.get("magnitude_n") and load["magnitude_n"] > 0:
            continue
        p = load.get("magnitude_n_mm2")
        if p and p > 0:
            selector = load.get("face_selector", "")
            area = _face_area_from_selector(bounds, selector)
            if area > 0:
                load["magnitude_n"] = p * area
                continue
        load["magnitude_n"] = 1.0


def load_from_file(path: str, strict: bool = True) -> dict:
    """从文件加载并校验载荷工况。"""
    if not os.path.isfile(path):
        return {"ok": False, "errors": [f"file not found: {path}"], "warnings": [], "case": None}
    try:
        # utf-8-sig 兼容带 BOM 的文件（PowerShell Set-Content -Encoding UTF8 会写 BOM）
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"JSON parse error: {e}"], "warnings": [], "case": None}
    result = validate_load_case(data, strict=strict)
    result["path"] = path
    # Bug #2 遗留修复: 归一化载荷（None/缺失的 magnitude_n → 1.0N）
    if result["ok"] and result.get("case"):
        _normalize_force_loads(result["case"])
    return result


def load_case_to_summary(case: dict) -> dict:
    """将工况转为简短摘要，用于 Agent 上下文和日志。"""
    mat = case.get("material", {})
    acc = case.get("acceptance", {})
    loads = case.get("loads", [])
    bcs = case.get("boundary_conditions", [])
    bounds = case.get("design_domain", {}).get("bounds", {})

    total_force_n = sum(
        max(l.get("magnitude_n") or 0, 0)
        for l in loads
        if l.get("type") in ("distributed_force", "concentrated_force")
    )

    return {
        "problem_id": case.get("meta", {}).get("problem_id", "?"),
        "title": case.get("meta", {}).get("title", "?"),
        "material": mat.get("name", "?"),
        "yield_strength_mpa": mat.get("yield_strength_mpa", 0),
        "E_mpa": mat.get("youngs_modulus_mpa", 0),
        "domain_mm": [
            bounds.get("x_min", 0), bounds.get("x_max", 0),
            bounds.get("y_min", 0), bounds.get("y_max", 0),
            bounds.get("z_min", 0), bounds.get("z_max", 0),
        ],
        "domain_volume_mm3": _safe_volume(bounds),
        "total_force_n": round(total_force_n, 2),
        "load_count": len(loads),
        "bc_count": len(bcs),
        "min_safety_factor": acc.get("min_safety_factor", 2.0),
        "max_safety_factor": acc.get("target_safety_factor_max", 5.0),
        "max_displacement_mm": acc.get("max_displacement_mm"),
        "analysis_type": acc.get("analysis_type", "linear_static"),
    }


def _safe_volume(bounds: dict) -> float:
    try:
        dx = max(bounds.get("x_max", 0) - bounds.get("x_min", 0), 0)
        dy = max(bounds.get("y_max", 0) - bounds.get("y_min", 0), 0)
        dz = max(bounds.get("z_max", 0) - bounds.get("z_min", 0), 0)
        return dx * dy * dz
    except Exception:
        return 0.0
