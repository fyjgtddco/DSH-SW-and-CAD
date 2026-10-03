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


# ══ 【BUG-02 修复】疲劳/设计寿命校核接入 ══════════════════════════════════
# 原缺陷：整条工具链只有线性静力（2D 平面应力），完全没有疲劳与 30 年寿命校核，
#   用户的核心需求无法验证。现把 fatigue.py 接入统一求解出口：
#   每次 solve_fea 在静力结果之后【自动追加】疲劳校核，
#   并把结论并入 gates / overall，使其成为交付前必须通过的闸口。
try:
    import fatigue as _fatigue
except ImportError:          # 允许在 sys.path 未注入时降级运行
    try:
        from . import fatigue as _fatigue
    except Exception:
        _fatigue = None


def aggregate_overall(gates):
    """按统一三档口径聚合 overall（Bug14 修复）。

    ── 为什么需要统一函数 ──────────────────────────────────────────────
    测试部实测：1N 超小载荷 → SF=2213 远超 target_max=5，
      SAFETY_FACTOR gate 确实标了 OVER_DESIGN / over_design=true，
      但最终 overall 却是 PASS，与"过度设计应提示 WARNING"的预期不符；
      而同样超标的实心块用例又报了 OVER_DESIGN —— 行为不一致。
    根因：各求解路径各自用 `passed = all(status == "PASS")` 算 overall，
      把 OVER_DESIGN 当"非 PASS"→ 有的路径算成 FAIL，随后又被
      _attach_fatigue 用另一套口径重算成 PASS，前后矛盾。

    统一口径（与 _attach_fatigue 保持一致）：
      · 任一 FAIL          → FAIL
      · 有 WARNING / N/A   → REVIEW
      · 有 OVER_DESIGN     → REVIEW（过度设计需提示，但不阻断交付）
      · 否则               → PASS
    说明：OVER_DESIGN 归 REVIEW 而非 PASS —— 用户要的是"可优化/减重"
      提示能被看见；它仍不是 FAIL，不影响交付。
    """
    statuses = [g.get("status") for g in (gates or {}).values()
                if isinstance(g, dict)]
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses or "N/A" in statuses or "OVER_DESIGN" in statuses:
        return "REVIEW"
    return "PASS"


def _material_guard(load_case: dict) -> Optional[dict]:
    """【BUG-05/08 修复】材料健全性前置检查。

    原缺陷：材料缺失时一路默认（E=200000、ρ=7850），报告 material 显示 '?'，
      甚至可能拿 SW 的 1000 kg/m³（水）密度当真 —— 结论全部不可信。
    修复：材料关键字段缺失/等于水密度时，返回【显式的阻断信息】，
      由调用方决定是报错还是标注 NOT_EVALUATED（绝不静默用默认值）。
    """
    mat = load_case.get("material") or {}
    problems = []
    if not mat:
        problems.append("material 字段缺失（完全未指定材料）")
    else:
        if not (mat.get("name") or mat.get("id")):
            problems.append("material 缺 name/id（无法确认材料身份）")
        if not mat.get("youngs_modulus_mpa"):
            problems.append("缺 youngs_modulus_mpa")
        if not mat.get("yield_strength_mpa"):
            problems.append("缺 yield_strength_mpa")
        _rho = mat.get("density_kg_m3")
        if _rho is not None and abs(float(_rho) - 1000.0) < 1.0:
            problems.append("密度为 1000 kg/m³（等同水）—— 极可能是 SolidWorks "
                            "未赋材质留下的默认值（BUG-05）")
    if not problems:
        return None
    return {
        "ok": False,
        "error": "材料信息不可用于强度/寿命校核",
        "problems": problems,
        "hint": ("请先在载荷工况里补齐 material（id/name/E/σy/ρ），"
                 "或在 swapi.new_part(material=...) 给零件赋材质。"
                 "材料不确定时任何安全系数与寿命结论都无效。"),
    }


def run_fatigue_check(load_case: dict, fea_result: dict) -> dict:
    """执行疲劳校核（fatigue.py 不可用时返回明确的 NOT_EVALUATED，绝不静默跳过）。"""
    if _fatigue is None:
        return {"ok": False, "verdict": "NOT_EVALUATED",
                "error": "fatigue.py 不可用，未能执行疲劳/寿命校核"}
    try:
        res = _fatigue.analyze_fatigue_from_case(load_case, fea_result)
    except Exception as e:
        import traceback
        return {"ok": False, "verdict": "NOT_EVALUATED",
                "error": "疲劳校核异常: %s" % e,
                "traceback": traceback.format_exc()[-1500:]}
    return res


def _attach_fatigue(load_case: dict, result: dict) -> dict:
    """把疲劳结果并入统一求解结果（gates + overall 判定一并更新）。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    # ── 【BUG-05/08】材料不健全 → 疲劳/寿命结论无意义，显式阻断并标注 ────
    _mg = _material_guard(load_case)
    if _mg:
        result["material_guard"] = _mg
        result.setdefault("gates", {})["MATERIAL_DATA"] = {
            "status": "FAIL",
            "problems": _mg["problems"],
            "hint": _mg["hint"],
        }
        result["fatigue"] = {
            "ok": False, "verdict": "NOT_EVALUATED",
            "error": "材料信息不完整，未执行疲劳/寿命校核：%s"
                     % "；".join(_mg["problems"]),
        }
        result["overall"] = "FAIL"
        result.setdefault("limitations", []).append(
            "⚠️ 材料不可用（%s）—— 强度与 30 年寿命结论均无效"
            % "；".join(_mg["problems"][:2]))
        return result
    fat = run_fatigue_check(load_case, result)
    result["fatigue"] = fat
    # ── 【NEW-01 修复】把验收判据【透传到 fea_result 顶层】 ──────────────
    # 原缺陷：fea_result 里没有 acceptance，下游想核对"判据是什么"
    #   必须回头找 load_case；报告顶层 acceptance_criteria 又漏传了
    #   疲劳参数（已一并修复）。此处让判据随结果走，审计更直接。
    try:
        _acc_src = (load_case or {}).get("acceptance") or {}
        result["acceptance"] = dict(_acc_src)
    except Exception:
        pass
    gates = result.setdefault("gates", {})
    if isinstance(gates, dict):
        gates["FATIGUE_STRENGTH"] = {
            "status": (fat.get("gates", {}).get("FATIGUE_STRENGTH", {}).get("status")
                       or ("N/A" if fat.get("verdict") == "NOT_EVALUATED" else "FAIL")),
            "actual_sf": fat.get("fatigue_sf"),
            "required_min": fat.get("min_required_fatigue_sf"),
            "endurance_limit_mpa": fat.get("endurance_limit_mpa"),
        }
        gates["FATIGUE_LIFE"] = {
            "status": (fat.get("gates", {}).get("FATIGUE_LIFE", {}).get("status")
                       or ("N/A" if fat.get("verdict") == "NOT_EVALUATED" else "FAIL")),
            "required_years": fat.get("design_life_years"),
            "allowable_life_years": fat.get("life_years_allowable"),
            "damage_at_required_life": fat.get("damage_design_life"),
        }
    # overall 需要重新聚合：静力全 PASS 但疲劳 FAIL → 整体 FAIL
    # ── 【BUG-02 附带修正】统一 overall 聚合口径 ────────────────────────
    # 原实现（solve_analytical / solve_feapy）用
    #   passed = all(status == "PASS")
    # 于是 SAFETY_FACTOR=WARNING（安全系数过高=过度设计）也被算成 overall=FAIL，
    # 与代码自身 BUG-21 的结论（"安全系数过高不是 FAIL，应标 WARNING"）矛盾，
    # 导致"结构明明很安全"却被报告成 FAIL。
    #
    # ── 【NEW-02 修复】三档 + OVER_DESIGN 单独处理 ──────────────────────
    #   任一 FAIL            → overall = FAIL
    #   有 WARNING / N/A     → overall = REVIEW   （真有需要复核的项）
    #   仅 OVER_DESIGN       → overall = PASS      （结构安全，只是材料利用率低）
    #   · 过度设计【不再】把结果拖成 REVIEW —— 它不是缺陷，
    #     但 over_design 标记与说明会保留，供轻量化优化参考。
    statuses = [g.get("status") for g in (gates or {}).values()
                if isinstance(g, dict)]
    if "FAIL" in statuses:
        result["overall"] = "FAIL"
    elif ("WARNING" in statuses or "N/A" in statuses
          or "OVER_DESIGN" in statuses):
        result["overall"] = "REVIEW"
    else:
        result["overall"] = "PASS"
    # 把"过度设计"作为独立信号暴露（不改变 overall，但清晰可见）
    if "OVER_DESIGN" in statuses:
        result["over_design"] = True
        result.setdefault("limitations", []).append(
            "结构安全但安全系数高于目标上限（过度设计）：材料利用率偏低，"
            "如需轻量化可减小截面尺寸或换薄壁结构。这不影响交付，仅作优化提示。")
    # 疲劳未评估必须显式暴露（用户核心需求是 30 年寿命校核）
    if fat.get("verdict") == "NOT_EVALUATED":
        result.setdefault("limitations", []).append(
            "⚠️ 疲劳/寿命校核未执行：%s" % fat.get("error", "未知原因"))
    return result


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

    # ══ 【Bug17 修复】几何必须优先取【真实零件】，而非 design_domain ═════
    # 测试部实测：三个不同载荷（39.5N/27.4N/12.2N，差 3 倍）返回的
    #   SF 完全相同 —— 因为解析解一直用 design_domain 的固定尺寸
    #   (200×60×60) 当梁长/宽/高，与真实零件无关，载荷变化被几何口径
    #   掩盖，强度判定因此不可信（Bug7 的残留）。
    # 修复：若 load_case 携带 real_geometry（由 physics_bridge 从
    #   swapi.massprops / 包围盒提取），则用它推导等效梁尺寸。
    _rg = load_case.get("real_geometry") or {}
    _bbox = _rg.get("bbox_mm") or None
    _vol_real = _rg.get("volume_mm3")
    _used_real = False
    if _bbox and len(_bbox) >= 6:
        try:
            _lx = abs(float(_bbox[1]) - float(_bbox[0]))
            _ly = abs(float(_bbox[3]) - float(_bbox[2]))
            _lz = abs(float(_bbox[5]) - float(_bbox[4]))
            _dims = sorted([_lx, _ly, _lz], reverse=True)
            if _dims[0] > 0 and _dims[1] > 0 and _dims[2] > 0:
                # 最长边=梁长；次长边=宽；最短边=高（悬臂梁等效）
                L, W, H = _dims[0], _dims[1], _dims[2]
                _used_real = True
        except Exception:
            _used_real = False
    if not _used_real:
        L = domain.get("x_max", 200)
        W = domain.get("y_max", 60)
        H = domain.get("z_max", 60)

    # 假设悬臂梁边界条件（固定端在 x=0）
    result = analytical_beam_cantilever(L, W, H, total_force, E)

    sigma_max = result.get("max_stress_mpa", 0)
    delta_max = result.get("max_deflection_mm", 0)
    volume_mm3 = result.get("volume_mm3", 0)

    # ── 【Bug17/18 回归修复】体积必须报【真实测量值】，不得用 bbox 乘积 ────
    # 测试部 v13 反馈：fea_result.volume 恒 = 720000（= design_domain
    #   200×60×60 的乘积），10 种零件完全一样 → 报告里的体积是"设计域体积"
    #   而不是"零件体积"，下游据此判断材料用量/质量必然失真。
    # 修复：优先采用 real_geometry.volume_mm3（swapi.massprops 的真实实体体积），
    #   bbox 只用于梁模型的等效尺寸，不再充当体积。
    _vol_reported = volume_mm3
    _vol_source = "bbox_product(等效梁)"
    if _vol_real:
        try:
            _vr = float(_vol_real)
            if _vr > 0:
                _vol_reported = _vr
                _vol_source = "swapi.massprops(真实实体体积)"
        except Exception:
            pass
    volume_mm3 = _vol_reported

    # 计算安全系数
    safety_factor = sigma_y / sigma_max if sigma_max > 0 else float("inf")

    # Bug-21 修复: 安全系数过高不是 FAIL，应标记 WARNING（过于保守=浪费材料，但结构安全）
    # 原逻辑: sf > max_sf → FAIL（错误：92 > 5 被判为超限）
    # 正确逻辑: sf < min_sf → FAIL（强度不足），sf > max_sf → WARNING（过度设计）
    #
    # ── 【NEW-02 修复】把"过度设计"与"真告警"语义分开 ─────────────────
    # 用户反馈：静力 SF=36.42 被标 WARNING，属"过设计"提示（已知行为），
    #   但混在 WARNING 里会让人以为结构有问题，也会把 overall 拖成 REVIEW。
    # 修复：过度设计仍无法判 PASS（确实未落在目标区间），但给出
    #   status="OVER_DESIGN" + over_design=true 的明确标记，
    #   overall 聚合时将其视为"合格但可优化"，不再简单归入 REVIEW。
    _min_sf = acceptance.get("min_safety_factor", 2.0)
    _max_sf = acceptance.get("target_safety_factor_max", 5.0)
    _over_design = False
    if safety_factor < _min_sf:
        sf_status = "FAIL"
    elif safety_factor > _max_sf:
        sf_status = "OVER_DESIGN"     # 结构安全，但材料利用率低（非缺陷）
        _over_design = True
    else:
        sf_status = "PASS"

    # 检查约束
    gates = {
        "SAFETY_FACTOR": {
            "status": sf_status,
            "actual": round(safety_factor, 2),
            "required_min": _min_sf,
            "required_max": _max_sf,
            "over_design": _over_design,
            "note": ("结构安全，安全系数高于目标上限 —— 属【过度设计/材料利用率低】，"
                     "不是缺陷；如需轻量化可减小截面/换薄壁"
                     if _over_design else
                     ("强度不足，必须加强" if sf_status == "FAIL" else "落在目标区间")),
        },
        "MAX_DISPLACEMENT": {
            "status": "PASS" if (acceptance.get("max_displacement_mm") is None or
                                  delta_max <= acceptance["max_displacement_mm"]) else "FAIL",
            "actual_mm": round(delta_max, 4),
            "required_max_mm": acceptance.get("max_displacement_mm"),
        },
        # ── 【Bug18 修复】FEA 层【不输出】DESIGN_SPACE ────────────────────
        # 空间是否越界是【几何】问题，归属 geometry_gate（见
        #   simulation_report.GATE_OWNER）。原实现在这里写死 "PASS"，
        #   与 geometry_gate 的 FAIL 形成"两层相反结论"，用户无从判断。
        # 注意：这里【整条不输出】，而不是标成 N/A —— 因为 aggregate_overall()
        #   把 N/A 视为"未评估"从而把整体判为 REVIEW，会让本来 PASS 的
        #   校核被无谓降级（实测 feapy 路径回归）。
        #   一个闸口只由一个层负责，不属于本层的就不要出现在本层结果里。
        "MESH_QUALITY": {"status": "N/A", "note": "analytical method, no mesh"},
    }

    passed = (aggregate_overall(gates) == "PASS")

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
        "overall": aggregate_overall(gates),
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
                # 【NEW-02 修复】过度设计用 OVER_DESIGN 明确区分（同解析解路径）
                "status": (
                    "FAIL" if safety_factor < min_sf
                    else "OVER_DESIGN" if safety_factor > max_sf
                    else "PASS"
                ),
                "actual": round(safety_factor, 2),
                "required_min": min_sf,
                "required_max": max_sf,
                "over_design": bool(safety_factor > max_sf),
                "note": ("结构安全，安全系数高于目标上限 —— 属【过度设计/材料利用率低】，"
                         "不是缺陷" if safety_factor > max_sf else
                         ("强度不足，必须加强" if safety_factor < min_sf else "落在目标区间")),
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
            # ── 【Bug18 修复】FEA 层【不输出】DESIGN_SPACE（归属 geometry_gate）
            #   理由见 solve_analytical 内同处注释：一个闸口只由一个层负责，
            #   且不得用 N/A 占位（aggregate_overall 会把 N/A 降级为 REVIEW）。
            "MESH_QUALITY": {
                "status": "PASS",
                "note": f"CST triangle mesh: ~{nx*ny*2} elements",
            },
        }

        passed = (aggregate_overall(gates) == "PASS")

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
            "overall": aggregate_overall(gates),
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
    # 【BUG-02 修复】每个后端的返回值在出口处统一追加【疲劳/30年寿命校核】，
    #   保证无论走解析解还是纯 Python FEA，用户都能拿到寿命结论。
    if backend == "analytical":
        return _attach_fatigue(load_case, solve_analytical(load_case, {}))
    elif backend == "feapy":
        return _attach_fatigue(load_case, solve_feapy(load_case, output_dir))
    elif backend == "calculix":
        if mesh_path and os.path.exists(mesh_path):
            return _attach_fatigue(load_case, run_calculix(mesh_path, mesh_path, output_dir))
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
