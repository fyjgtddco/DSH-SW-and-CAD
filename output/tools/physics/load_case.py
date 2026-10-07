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
    # ── 【BUG-02 修复】疲劳 / 设计寿命校核参数（默认按 30 年长寿命设备）──
    "design_life_years": 30.0,        # 目标设计寿命（年）
    "operating_cycles_per_year": 200000.0,   # 年动作/载荷循环次数
    "load_type": "cyclic",            # static / cyclic / impact
    "impact_factor": 1.0,             # 动载系数
    "fatigue_check_required": True,   # 是否强制疲劳校核
    "surface_finish": "machined",     # ground/machined/hot_rolled/as_forged
    "characteristic_diameter_mm": None,
    "reliability": 0.99,              # 疲劳可靠度
    "service_temperature_c": 25.0,
    "fatigue_criterion": "goodman",   # goodman / soderberg / gerber
}

VALID_LOAD_TYPES = {
    "distributed_force", "concentrated_force", "moment",
    "pressure", "thermal", "gravity",
}
VALID_BC_TYPES = {
    "fixed_displacement", "pinned", "roller", "slider",
    "symmetry", "coupled_displacement",
}

# ── 【P1 修复】载荷/材料合理性上限（防"篡改载荷致 SF 失真"）───────────────
# 实测（子代理3）：magnitude_n 写 100000N 也通过校验 → SF=0 →
#   下游除零崩溃；且这是"篡改载荷绕过物理防线"的攻击面。
# 上限取工程常见量级：单件载荷 1e7 N（1000 吨）已远超任何竞赛/常规机械；
#   超过即判"疑似单位错误或恶意放大"，fail-closed 拒绝而非静默接受。
_MAX_LOAD_N = 1.0e7
_MAX_PRESSURE_MPA = 1.0e5
# 材料密度合理区间（kg/m³）：低于 900 或高于 9000 已超出工程材料范围。
#   密度=1000（水）是 SolidWorks 未赋材的默认值，属最典型的"材料没赋"信号。
_MIN_DENSITY = 500.0
_MAX_DENSITY = 10000.0


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

        # ── 【分层材料校验】显式处理 None，绝不写 `mat.get(k, 0) <= 0` ──────
        # 实测缺陷（子代理发现）：`.get(key, default)` 只在【键不存在】时用
        #   默认值；当键存在而值为 None（国标牌号"标准身份已确认、力学数值
        #   待补全"就是这种形态）时返回 None，于是 `None <= 0` 抛
        #     TypeError: '<=' not supported between instances of 'NoneType' and 'int'
        #   用户看到的是 Python 堆栈，而不是"该牌号数值待补全、去哪补"。
        #   更糟的是 FEA 层的 fail-closed 被这里抢先崩溃掩盖了。
        # 现在：先判 None/类型，再判数值范围；缺数值时给出可执行的补全指引。
        _mat_ready_hint = None
        try:
            import sys as _sys
            _pd = os.path.dirname(os.path.abspath(__file__))
            if _pd not in _sys.path:
                _sys.path.insert(0, _pd)
            import material_db as _mdb
            _nm = mat.get("name") or mat.get("id") or mat.get("material_id")
            if _nm:
                _ok, _entry, _why = _mdb.gb_material_ready(_nm)
                if not _ok and _why:
                    _mat_ready_hint = _why
        except Exception:
            _mat_ready_hint = None

        # ── 【P2-1 修复·脆性材料判据】σy 缺失不必然是错误 ─────────────────
        # 实测缺陷（子代理发现）：HT200（灰铸铁）**无屈服平台**，标准只给
        #   抗拉强度 σb≥200 —— 这是【材料特性】，不是数据缺失。
        #   旧实现把"缺 σy"一律判 error，于是 HT200 直接卡死在 validate-case
        #   入口，根本走不到应力判据；而灰铸铁用 von Mises + σy 判据在物理上
        #   本来就是错的（正解是最大主应力/莫尔判据 + σb）。
        # 现在：识别【脆性材料】（灰铸铁/球墨铸铁/铸造铝/陶瓷等），
        #   允许其以 σb 作为强度判据基准，并明确标注判据类型；
        #   非脆性材料缺 σy 仍按错误处理。
        _BRITTLE_KW = ("灰铸铁", "球墨铸铁", "铸铁", "cast iron", "gray iron",
                       "ductile iron", "铸造铝", "铸铝", "陶瓷", "ceramic",
                       "concrete", "混凝土")
        _mat_name = str(mat.get("name") or mat.get("id") or "").lower()
        _is_brittle = any(k.lower() in _mat_name for k in _BRITTLE_KW)
        # 材料族兜底：密度 7000~7600 且无 σy，很可能是铸铁
        if not _is_brittle:
            try:
                _rho_v = float(mat.get("density_kg_m3") or 0)
                if mat.get("yield_strength_mpa") is None and 6900 <= _rho_v <= 7700:
                    _is_brittle = True
            except Exception:
                pass
        if _is_brittle and mat.get("yield_strength_mpa") is None \
                and mat.get("uts_mpa"):
            warnings.append(
                "材料 %s 判定为【脆性材料】：无屈服平台，"
                "强度判据将改用抗拉强度 σb=%.0f MPa（最大主应力/莫尔判据），"
                "而非 von Mises + σy —— 这是脆性材料的正确判据。"
                % (_mat_name or "?", float(mat.get("uts_mpa"))))
            mat["strength_basis"] = "uts_brittle"
            mat["brittle"] = True
            # ⚠️ 必须【写回 data["material"]】：mat 可能是 _safe_get 返回的副本，
            #   只改 mat 的话，返回体里的 case 拿不到 brittle 标记 →
            #   FEA 仍按 von Mises+σy 判据，脆性支持等于没生效（实测踩过）。
            try:
                if isinstance(data.get("material"), dict):
                    data["material"]["brittle"] = True
                    data["material"]["strength_basis"] = "uts_brittle"
            except Exception:
                pass

        for _field, _label in (("youngs_modulus_mpa", "弹性模量 E"),
                               ("yield_strength_mpa", "屈服强度 σy")):
            _fv = mat.get(_field)
            # 脆性材料允许缺 σy（已改用 σb 判据）
            if _field == "yield_strength_mpa" and _is_brittle \
                    and mat.get("uts_mpa"):
                continue
            if _fv is None:
                if _mat_ready_hint:
                    errors.append(
                        "material.%s（%s）缺失 —— %s"
                        % (_field, _label, _mat_ready_hint))
                else:
                    errors.append(
                        "material.%s（%s）缺失（None）—— 无法计算；"
                        "请确认该牌号力学数值已补全，或材料已真正赋到零件上"
                        % (_field, _label))
                continue
            if isinstance(_fv, bool):
                errors.append("material.%s 是布尔值，不是数值" % _field)
                continue
            try:
                _fnum = float(_fv)
            except Exception:
                errors.append("material.%s=%r 不是有效数值" % (_field, _fv))
                continue
            if _fnum != _fnum or _fnum in (float("inf"), float("-inf")):
                errors.append("material.%s=%r 不是有限数" % (_field, _fv))
                continue
            if _fnum <= 0:
                errors.append("material.%s must be positive" % _field)

        # ── 【P1 修复】材料密度校验 ──────────────────────────────────────
        # 实测（子代理3）：密度 = 1000(水) / 0 / "abc" 全部通过 validate_case。
        #   密度是质量与重力的计算基准，非法值必须拦下：
        #     · "abc"/非数值 → 下游 TypeError 或静默取默认，属隐藏错误；
        #     · ≤0 → 质量算出负值/零，无物理意义；
        #     · 不在工程材料区间 → 多为单位错误（g/cm³ 当成 kg/m³）；
        #     · 恰为 1000（水）→ SolidWorks 未赋材的默认值，属材料防线
        #       明确禁止的情形（这里也提前拦一道，双保险）。
        if "density_kg_m3" in mat:
            _dv = mat.get("density_kg_m3")
            if _dv is None:
                errors.append(
                    "material.density_kg_m3 缺失（None）—— 密度是质量与重力的"
                    "计算基准，无法计算；%s"
                    % (_mat_ready_hint or "请确认该牌号数值已补全"))
                _dnum = None
            elif isinstance(_dv, bool):
                _dnum = None
                errors.append("material.density_kg_m3 是布尔值，不是数值")
            else:
                try:
                    _dnum = float(_dv)
                except Exception:
                    _dnum = None
                    errors.append(
                        "material.density_kg_m3=%r 不是有效数值（材料未真正赋值）"
                        % (_dv,))
            if _dnum is not None:
                if _dnum != _dnum or _dnum in (float("inf"), float("-inf")):
                    errors.append("material.density_kg_m3=%r 不是有限数" % (_dv,))
                elif _dnum <= 0:
                    errors.append(
                        "material.density_kg_m3=%.4g 非法：密度必须 > 0" % _dnum)
                elif abs(_dnum - 1000.0) < 1.0:
                    errors.append(
                        "material.density_kg_m3=1000 kg/m³ 等同【水】—— 这是 "
                        "SolidWorks 未赋材质的默认值，属材料防线禁止情形；"
                        "请用 swapi.new_part(material='Q235') 显式赋材后重新导出工况")
                elif not (_MIN_DENSITY <= _dnum <= _MAX_DENSITY):
                    errors.append(
                        "material.density_kg_m3=%.6g 超出工程材料区间 [%.0f, %.0f] —— "
                        "疑似单位错误（g/cm³ 当成 kg/m³）或材料名与实际不符"
                        % (_dnum, _MIN_DENSITY, _MAX_DENSITY))

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

        # ── 【P1 修复】载荷【范围】校验 ──────────────────────────────────
        # 实测（子代理3）：magnitude_n = 100000N / 0N / 负值 全部通过校验，
        #   其中 100000N 直接导致 SF=0 → 下游 refine_rules 除零崩溃。
        #   载荷是校核的输入基准，非法值必须在入口拦下（fail-closed），
        #   否则"篡改载荷让 SF 变成无穷大/零"就能绕过物理防线。
        if load.get("type") in ("distributed_force", "concentrated_force"):
            _mag = load.get("magnitude_n")
            _lid = load.get("id", "?")
            if _mag is not None:
                try:
                    _mv = float(_mag)
                    if _mv <= 0:
                        errors.append(
                            f"载荷 {_lid!r} 的 magnitude_n={_mag!r} 非法：必须 > 0"
                            f"（0/负值没有物理意义，且会让安全系数失真）")
                    elif _mv > _MAX_LOAD_N:
                        errors.append(
                            f"载荷 {_lid!r} 的 magnitude_n={_mv} N 超出合理上限 "
                            f"{_MAX_LOAD_N:.0f} N —— 疑似单位错误（N/kN 混用）或"
                            f"恶意放大；请确认后重新提交")
                except Exception:
                    errors.append(
                        f"载荷 {_lid!r} 的 magnitude_n={_mag!r} 不是有效数值")
            # 压强载荷同样校验
            _p = load.get("magnitude_n_mm2")
            if _p is not None:
                try:
                    if float(_p) <= 0:
                        errors.append(
                            f"载荷 {_lid!r} 的 magnitude_n_mm2={_p!r} 非法：必须 > 0")
                    elif float(_p) > _MAX_PRESSURE_MPA:
                        errors.append(
                            f"载荷 {_lid!r} 的 magnitude_n_mm2={_p} N/mm² 超出合理上限 "
                            f"{_MAX_PRESSURE_MPA} N/mm² —— 疑似单位错误或恶意放大")
                except Exception:
                    errors.append(
                        f"载荷 {_lid!r} 的 magnitude_n_mm2={_p!r} 不是有效数值")

        # ── 【P1 修复】载荷【方向】校验 ─────────────────────────────────
        # 实测：全零 / 反向 / 非单位 / 字符串 "down" 全部通过校验。
        #   · 全零向量：力的方向未定义，FEA 结果无意义；
        #   · 非数值：下游会抛异常或静默取默认值，属隐藏错误；
        #   · 非单位向量：本系统按"方向 + 幅值"分离建模，长度≠1 会让
        #     幅值被重复计入（等效于放大载荷）。
        # 注：不做"必须指向某方向"的判断 —— 反向载荷是合法的（如反向冲击）。
        _d = load.get("direction")
        if _d is not None:
            _lid = load.get("id", "?")
            if not isinstance(_d, (list, tuple)) or len(_d) != 3:
                errors.append(
                    f"载荷 {_lid!r} 的 direction 必须是长度 3 的数值数组，"
                    f"实际为 {_d!r}")
            else:
                try:
                    _dv = [float(x) for x in _d]
                except Exception:
                    _dv = None
                    errors.append(
                        f"载荷 {_lid!r} 的 direction 含非数值分量：{_d!r}")
                if _dv is not None:
                    _norm = sum(x * x for x in _dv) ** 0.5
                    if _norm <= 1e-12:
                        errors.append(
                            f"载荷 {_lid!r} 的 direction 是全零向量 —— 力的方向未定义，"
                            f"FEA 结果无意义；请给出单位方向向量")
                    elif abs(_norm - 1.0) > 1e-3:
                        # 非单位向量：只警告（幅值可能已被正确分离），但必须提示
                        warnings.append(
                            f"载荷 {_lid!r} 的 direction 不是单位向量（模长={_norm:.4f}）"
                            f"—— 请确认 magnitude_n 已按总力给出，否则载荷会被重复放大")

    # ── 【观察点4 修复】多工况载荷组合校验 ─────────────────────────────
    # 原缺陷：loads 只支持单一 -Z 重力工况，竞速小车的过弯侧向力/纵向驱动力
    #   完全没有覆盖，physics 只按重力校核会漏掉最关键的失效模式。
    #   门禁现在会落盘 load_cases（rated/lateral/longitudinal/combined），
    #   这里做校验：组合引用的 load_id 必须真实存在。
    load_cases = data.get("load_cases")
    if load_cases is not None:
        if not isinstance(load_cases, list):
            errors.append("load_cases must be a JSON array (list)")
        else:
            _load_ids = {l.get("id") for l in loads
                         if isinstance(l, dict) and l.get("id")}
            _seen_case_ids = set()
            for lc in load_cases:
                if not isinstance(lc, dict):
                    errors.append("each load_case must be a JSON object (dict)")
                    continue
                _cid = lc.get("id")
                if not _cid:
                    errors.append("each load_case must have an 'id'")
                elif _cid in _seen_case_ids:
                    errors.append(f"duplicate load_case id: {_cid!r}")
                else:
                    _seen_case_ids.add(_cid)
                _refs = lc.get("load_ids")
                if not isinstance(_refs, list) or not _refs:
                    errors.append(f"load_case {_cid!r} must have a non-empty load_ids list")
                    continue
                for _rid in _refs:
                    if _rid not in _load_ids:
                        errors.append(
                            f"load_case {_cid!r} references unknown load id {_rid!r}")
            # required_load_cases 必须都在 load_cases 里
            _req = (data.get("acceptance") or {}).get("required_load_cases") \
                if isinstance(data.get("acceptance"), dict) else None
            if isinstance(_req, list):
                for _rn in _req:
                    if _rn not in _seen_case_ids:
                        warnings.append(
                            f"acceptance.required_load_cases 引用了未定义的工况 {_rn!r}")

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

    # ── 【BUG-02 修复】疲劳/寿命字段校验 ────────────────────────────────
    # 用户核心需求是"30 年寿命校核"，这些字段缺失/非法会让疲劳结论失真，
    # 因此必须显式校验（缺失时补默认值并给 warning，非法值给 error）。
    if "design_life_years" in acc:
        try:
            _ly = float(acc["design_life_years"])
            if _ly <= 0:
                errors.append("acceptance.design_life_years must be positive")
        except (TypeError, ValueError):
            errors.append("acceptance.design_life_years must be a number")
    if "operating_cycles_per_year" in acc:
        try:
            _cy = float(acc["operating_cycles_per_year"])
            if _cy <= 0:
                errors.append("acceptance.operating_cycles_per_year must be positive")
        except (TypeError, ValueError):
            errors.append("acceptance.operating_cycles_per_year must be a number")
    _lt = acc.get("load_type")
    if _lt is not None and str(_lt).lower() not in ("static", "cyclic", "impact"):
        warnings.append("acceptance.load_type %r 非标准值（static/cyclic/impact），"
                        "疲劳载荷谱将按 cyclic 处理" % (_lt,))
    if not acc.get("fatigue_check_required", True):
        warnings.append("acceptance.fatigue_check_required=False：疲劳/寿命校核被显式关闭，"
                        "交付前请确认这是有意为之")

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


def design_load_factor(case: dict) -> tuple:
    """【Bug-03 修复】返回设计校核应乘的动载系数 (factor, meta)。

    ── 为什么需要 ────────────────────────────────────────────────────────
    门禁（workflow_gate.estimate_mechanics）明确区分两个量：
      · nominal_load_n ：额定（工作）载荷 —— 写进工况的 magnitude_n；
      · design_load_n  ：设计（等效）载荷 = nominal × impact_factor。
    工况文件里 magnitude_n 存的是【额定值】，另在 acceptance 里记录
      load_type / impact_factor，并注明"设计校核另乘 impact_factor"。
    但 physics 侧原先【从未读取 impact_factor】—— 冲击工况
      （impact_factor=2.0）的校核载荷少算一半，安全系数因此虚高。
      实测：本任务 magnitude_n=500N、impact_factor=2.0，设计应校核 1000N，
      实际按 500N 计算。

    Returns: (factor: float, meta: dict)
      factor 为 1.0 表示静态或未声明动载系数。
    """
    acc = (case or {}).get("acceptance") or {}
    lt = str(acc.get("load_type") or "static").strip().lower()
    try:
        f = float(acc.get("impact_factor") or 1.0)
    except Exception:
        f = 1.0
    if f < 1.0:
        f = 1.0
    meta = {
        "load_type": lt,
        "impact_factor": f,
        "applied": bool(f > 1.0),
        "note": ("设计校核载荷 = 额定 × 动载系数 %.2f（load_type=%s）" % (f, lt)
                 if f > 1.0 else "静态工况，动载系数 1.0"),
    }
    return f, meta


def part_load_share(case: dict) -> tuple:
    """【Bug-D 修复】返回本零件应承担的载荷比例 (share, meta)。

    ── 为什么需要 ────────────────────────────────────────────────────────
    实测缺陷：竞赛小车的《整机》额定载荷（500N，组合工况 1224.7N）被
      【原样施加到单个小零件】上。对 30×20×10mm 的小块按悬臂梁端部集中力
      加载 1224.7N，必然得到 SF=0.2 的失真结论（"零件在载荷下必坏"），
      而实际该零件只承担整机载荷的一小部分。
    这属于【载荷工况的适用范围】问题：load_case 里的载荷是整机级的，
      单件校核必须乘一个"分担系数"。

    系数来源（按优先级）：
      ① acceptance.part_load_share（显式给定，最权威）
      ② acceptance.load_share
      ③ 任务级默认：单件按"整机载荷 / 预估主要承力件数"折算
         —— 由 acceptance.parallel_load_paths 指定，默认 1.0（不折算，
            保持向后兼容：显式给了整机载荷就按整机校核）
      ④ design_domain 之外的场景一律 1.0

    Returns: (share: float, meta: dict)
      meta 含 share / source / note；share 已保证 >0。
    """
    acc = (case or {}).get("acceptance") or {}
    # ① 显式给定
    for _k in ("part_load_share", "load_share"):
        if _k in acc:
            try:
                _s = float(acc.get(_k))
                if _s > 0:
                    return _s, {
                        "share": _s, "source": "acceptance.%s" % _k,
                        "note": "使用显式给定的零件载荷分担系数",
                    }
            except Exception:
                pass
    # ② 按承力路径数折算（多件并联分担）
    try:
        _paths = int(acc.get("parallel_load_paths") or 0)
        if _paths > 1:
            _s = 1.0 / float(_paths)
            return _s, {
                "share": _s, "source": "acceptance.parallel_load_paths=%d" % _paths,
                "note": ("整机载荷由 %d 条并联承力路径分担，单件按 1/%d 折算"
                          % (_paths, _paths)),
            }
    except Exception:
        pass
    # ③ 兜底：不折算（保持与旧行为一致，避免静默改变既有任务结论）
    return 1.0, {
        "share": 1.0, "source": "default",
        "note": ("未声明零件载荷分担系数（acceptance.part_load_share / "
                  "parallel_load_paths）—— 按整机载荷直接校核本零件。"
                  "若本零件只是整机中的一个分件，应在工况里显式给出分担系数，"
                  "否则小零件会被整机载荷压出失真的低安全系数（Bug-D）。"),
    }


def governing_design_force(case: dict) -> tuple:
    """【Bug-03 修复】选出【最恶劣工况】的设计校核载荷。

    背景（两个实测缺陷，共同导致 SF 虚高/失真）：
      ① 原实现 `sum(magnitude_n for all loads)` —— 把各工况、各方向的载荷
         【代数相加】。正交分量（-Z 重力 / +X 侧向 / +Y 纵向）相加没有物理
         意义，会把"分别校核"错算成"同时叠加"。
      ② 动载系数（acceptance.impact_factor）原先【从未被施加】，而门禁又把
         侧向载荷预先乘过一次 —— 两边口径不一致，既可能漏算也可能重复计算。

    规则（与 README / 门禁的文档契约一致：magnitude_n = 额定载荷，
    设计校核另乘一次 impact_factor）：
      ① 按 load_cases 枚举工况；无 load_cases 时把所有载荷视为单一工况；
      ② 工况内按【方向矢量合成】（正交分量平方和开根），不代数相加；
      ③ 取各工况合成力的【最大值】作为额定校核力；
      ④ 对该额定校核力乘【一次】动载系数。

    Returns:
        (force_n: float, meta: dict)
        meta 含 rated_force_n / governing_case / impact_factor /
        load_type / impact_applied / cases（各工况明细）。
    """
    loads = [l for l in ((case or {}).get("loads") or []) if isinstance(l, dict)]
    bounds = ((case or {}).get("design_domain") or {}).get("bounds") or {}
    by_id = {}
    for _l in loads:
        _lid = str(_l.get("id") or "")
        if _lid:
            by_id[_lid] = _l

    def _mag(l):
        """单条载荷的力大小（N）：magnitude_n 优先，其次压强 × 面积。"""
        if l.get("type") not in ("distributed_force", "concentrated_force"):
            return 0.0
        try:
            m = float(l.get("magnitude_n") or 0)
        except Exception:
            m = 0.0
        if m <= 0:
            try:
                p = float(l.get("magnitude_n_mm2") or 0)
            except Exception:
                p = 0.0
            if p > 0:
                sel = l.get("face_selector") or ""
                if sel:
                    m = p * _face_area_from_selector(bounds, sel)
        return max(m, 0.0)

    def _vec_sum(ls):
        """把一个工况内的载荷按【方向矢量】合成，返回合成力大小。"""
        vx = vy = vz = 0.0
        for _l in ls:
            m = _mag(_l)
            if m <= 0:
                continue
            d = _l.get("direction") or [0.0, 0.0, -1.0]
            try:
                dx, dy, dz = float(d[0]), float(d[1]), float(d[2])
            except Exception:
                dx, dy, dz = 0.0, 0.0, -1.0
            n = (dx * dx + dy * dy + dz * dz) ** 0.5
            if n <= 0:
                dx, dy, dz, n = 0.0, 0.0, -1.0, 1.0
            vx += m * dx / n
            vy += m * dy / n
            vz += m * dz / n
        return (vx * vx + vy * vy + vz * vz) ** 0.5

    # ① 枚举工况
    groups = []
    for _lc in ((case or {}).get("load_cases") or []):
        if not isinstance(_lc, dict):
            continue
        _ids = _lc.get("load_ids") or []
        _ls = [by_id[i] for i in _ids if i in by_id]
        if _ls:
            groups.append((str(_lc.get("id") or "case"), _ls))
    if not groups:
        groups = [("all_loads", loads)]

    # ②③ 逐工况矢量合成，取最大值
    cases_detail = []
    gov = None
    for _gid, _ls in groups:
        _r = _vec_sum(_ls)
        cases_detail.append({"id": _gid, "resultant_n": round(_r, 3),
                             "load_ids": [str(x.get("id")) for x in _ls]})
        if gov is None or _r > gov[1]:
            gov = (_gid, _r)

    if gov is None:
        return 0.0, {"rated_force_n": 0.0, "governing_case": None,
                     "impact_factor": 1.0, "impact_applied": False,
                     "cases": cases_detail}

    rated = gov[1]
    # ④ 动载系数只施加一次
    _f, _dyn = design_load_factor(case)
    design = rated * _f
    # ⑤ 【Bug-D 修复】整机载荷 → 本零件载荷的分担折算
    #   工况里的 magnitude_n 是【整机】额定载荷；单件校核须乘分担系数，
    #   否则小零件会被整机载荷压出 SF=0.2 这类失真结论（实测 30×20×10 小块）。
    _share, _share_meta = part_load_share(case)
    _design_before_share = design
    design = design * _share
    return design, {
        "rated_force_n": round(rated, 3),
        "design_force_n": round(design, 3),
        # 分担系数与其口径来源必须回显，便于判断"载荷是否施加得当"
        "part_load_share": _share,
        "load_share_source": _share_meta.get("source"),
        "load_share_note": _share_meta.get("note"),
        "design_force_before_share_n": round(_design_before_share, 3),
        "governing_case": gov[0],
        "impact_factor": _f,
        "load_type": _dyn.get("load_type"),
        "impact_applied": bool(_f > 1.0),
        "cases": cases_detail,
    }


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
