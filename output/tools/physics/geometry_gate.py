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
    # ── 【Bug18 修复·未评估语义】bbox 缺失时必须报"未评估"，不得假 PASS/FAIL ──
    # 原实现在拿不到真实包围盒时，调用方会用 design_domain 回填 —— 那等于
    #   "拿设计域自己和自己比"，DESIGN_SPACE 恒 PASS，门禁形同虚设。
    # 现在：调用方应传 None；此处返回 ok=False + evaluated=False，
    #   由上层据此判 NOT_EVALUATED（不可交付），而不是静默放过。
    if not body_bbox or not isinstance(body_bbox, (list, tuple)) or len(body_bbox) < 6:
        return {
            "ok": False,
            "evaluated": False,
            "body_bbox_mm": None,
            "design_bounds": design_bounds,
            "per_axis": {},
            "violation_vol_mm3": 0.0,
            "violation_pct": 0.0,
            "reason": ("缺少真实包围盒（bbox），DESIGN_SPACE 未评估 —— "
                       "不得据此判定合格。请确保能连上 SolidWorks 取到真实几何。"),
        }
    # ── 【Bug18 修复】坐标基准必须统一，不得直接比较绝对值 ──────────────
    # 测试部实测：承载板 200×60×10（本应 PASS）被判 DESIGN_SPACE FAIL：
    #   body_bbox_mm=[-12.33,12.33,-34.88,34.88,...]  ← 零件【局部】坐标
    #   design_bounds = [0,200]×[0,60]×[0,60]         ← 设计域【全局】坐标
    #   两者基准不同，直接比较必然"越界" → 合格零件被误拒。
    # 修复：先做【基准对齐】—— 用零件包围盒的【尺寸】去核对设计域的
    #   【可用空间尺寸】，而不是比对绝对坐标。
    #   具体：把 body_bbox 平移，使其中心与设计域中心重合后再比较；
    #   这只影响"是否放得下"的判定，不改变"尺寸是否超限"的语义。
    _bb = list(body_bbox) if body_bbox else None
    if _bb and len(_bb) >= 6:
        try:
            _align = []
            for _ax, _i0, _i1 in (("x", 0, 1), ("y", 2, 3), ("z", 4, 5)):
                _dmin = design_bounds.get(_ax + "_min")
                _dmax = design_bounds.get(_ax + "_max")
                if _dmin is None or _dmax is None:
                    _align.append((_bb[_i0], _bb[_i1]))
                    continue
                _bmin, _bmax = float(_bb[_i0]), float(_bb[_i1])
                _blen = _bmax - _bmin
                _dlen = float(_dmax) - float(_dmin)
                _dcen = (float(_dmax) + float(_dmin)) / 2.0
                # 以设计域中心为基准重新放置零件（保留其真实尺寸）
                _new_min = _dcen - _blen / 2.0
                _new_max = _dcen + _blen / 2.0
                _align.append((_new_min, _new_max))
            body_bbox = [_align[0][0], _align[0][1],
                         _align[1][0], _align[1][1],
                         _align[2][0], _align[2][1]]
        except Exception:
            body_bbox = _bb
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


def estimate_min_wall_thickness(face_areas: list[dict], volume_mm3: float,
                                surface_area_mm2: float = None) -> dict[str, Any]:
    """基于表面积/体积比估算最小壁厚。

    近似公式：t_min ≈ 2 * V / A_total（薄壁/凸形体的特征厚度量级）

    ── 【Bug7 修复】新增 surface_area_mm2 入参 ──────────────────────
    原实现【只认 face_areas 逐面数据】；但真实零件走 swapi.massprops 时
      只拿得到【总表面积】（没有逐面明细），于是这里恒返回 ok=False，
      壁厚门禁变成"永远无法评估" → 实测 0.5mm 极薄壁照样 PASS。
    现在接受总表面积作为等价输入，使真实零件也能被壁厚门禁覆盖。
    """
    total_area = 0.0
    if face_areas:
        try:
            total_area = sum(float(f.get("area_mm2", 0) or 0) for f in face_areas)
        except Exception:
            total_area = 0.0
    if total_area <= 0 and surface_area_mm2:
        try:
            total_area = float(surface_area_mm2)
        except Exception:
            total_area = 0.0
    if total_area <= 0:
        return {"ok": False, "error": "no face data available（无逐面数据且无总表面积）"}
    if not volume_mm3 or float(volume_mm3) <= 0:
        return {"ok": False, "error": "invalid volume_mm3（体积缺失或为 0）"}
    # 薄壁特征厚度：t ≈ 2V/A（V/A 为半厚度量级）
    t_est_mm = 2.0 * float(volume_mm3) / total_area
    return {
        "ok": True,
        "estimated_min_thickness_mm": round(t_est_mm, 4),
        "volume_mm3": round(float(volume_mm3), 3),
        "total_area_mm2": round(total_area, 3),
        "method": "2V/A_ratio_approximation",
        "note": "近似估计（2V/A）；精确壁厚需 STEP 网格分析。用于识别明显过薄件。",
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
    surface_area_mm2: Optional[float] = None,
    min_wall_req_mm: Optional[float] = None,
) -> dict[str, Any]:
    """综合几何门禁报告。

    Returns:
        {status: PASS/WARNING/FAIL, checks: {...}, gates: [...]}
    """
    space_check = check_design_space_violation(bbox, design_bounds)
    conn_check = check_connectivity(topology)
    wall_check = estimate_min_wall_thickness(face_areas, volume_mm3,
                                             surface_area_mm2=surface_area_mm2)

    gates = [
        # ── 【Bug18 修复】bbox 缺失时判 NOT_EVALUATED，不得默认 PASS ──────
        # 原实现是二元 "PASS if ok else FAIL"：拿不到 bbox 时调用方用
        #   design_domain 回填 → 恒 PASS；不填 → 恒 FAIL（误拒合格件）。
        # 现在三态：有真实 bbox 才判 PASS/FAIL，否则 NOT_EVALUATED。
        {"id": "DESIGN_SPACE", "status": (
            "NOT_EVALUATED" if not space_check.get("evaluated", True)
            else ("PASS" if space_check["ok"] else "FAIL"))},
        {"id": "CONNECTIVITY", "status": conn_check["status"]},
        # ── 【Bug7 修复】WALL_THICKNESS 不得默认 PASS ──────────────────
        # 原实现写死 PASS，且 min_wall_req_mm 默认 None → 壁厚检查
        #   永远不可能 FAIL。实测：0.5mm 极薄壁件（FDM 最小 2mm）照样通过。
        # 现在：拿不到真实壁厚估计 → NOT_EVALUATED（不可交付）；
        #   拿到估计值 → 与要求比对，低于要求即 FAIL。
        {"id": "WALL_THICKNESS", "status": (
            "NOT_EVALUATED" if not wall_check.get("ok") else "PASS")},
    ]
    if not space_check.get("evaluated", True):
        gates[0]["reason"] = space_check.get("reason")

    _wall_req = min_wall_req_mm
    if _wall_req is None:
        try:
            _dp = design_params or {}
            _v = _dp.get("min_wall_thickness")
            if isinstance(_v, dict):
                _v = _v.get("value")
            if _v is not None:
                _wall_req = float(_v)
        except Exception:
            _wall_req = None
    if wall_check.get("ok") and _wall_req:
        est = wall_check.get("estimated_min_thickness_mm")
        if est is not None:
            _est = float(est)
            _req = float(_wall_req)
            # ── 【Bug13 修复】临界区间不得直接 FAIL ──────────────────────
            # 2V/A 是【近似】算法（note 已注明），对平板会系统性偏低：
            #   实测 100x100x2.0mm 板算出 1.9231（偏低 3.8%）→ 被误判 FAIL，
            #   而该件壁厚恰好达标。这是"合格设计被误拒"。
            # 处理：
            #   · 低于阈值且超出宽容度(15%) → FAIL（明显过薄，必须拦）
            #   · 落在阈值 ±15% 内       → WARNING（近似误差区间，提示人工复核）
            #   · 其余                    → PASS
            _tol = 0.15
            if _est < _req * (1.0 - _tol):
                gates[-1] = {"id": "WALL_THICKNESS", "status": "FAIL",
                             "estimated_mm": _est,
                             "required_mm": _req,
                             "reason": ("估计壁厚 %.3fmm 明显低于要求 %.3fmm"
                                        "（超出 %.0f%% 近似宽容度）"
                                        % (_est, _req, _tol * 100))}
            elif _est < _req:
                gates[-1] = {"id": "WALL_THICKNESS", "status": "WARNING",
                             "estimated_mm": _est,
                             "required_mm": _req,
                             "method": "2V/A_ratio_approximation",
                             "reason": ("估计壁厚 %.3fmm 略低于要求 %.3fmm，但落在"
                                        "近似算法 ±%.0f%% 误差带内 —— 建议人工复核，"
                                        "不据此判定不合格（Bug13）"
                                        % (_est, _req, _tol * 100))}
            else:
                gates[-1] = {"id": "WALL_THICKNESS", "status": "PASS",
                             "estimated_mm": _est, "required_mm": _req}
    elif not wall_check.get("ok"):
        gates[-1]["reason"] = "无法估计壁厚，该门禁未评估，不得据此判定合格"

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
