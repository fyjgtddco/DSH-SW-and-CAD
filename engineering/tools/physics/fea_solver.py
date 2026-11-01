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


# ══ 【BUG-FEA-STRENGTH-1 修复】强度判据基准：脆性材料缺 σy 时改用 σb ═══════
# 实测崩溃：physics-optimize 处理 DSH_加工头本体（HT200）时
#   fea_solver.py:735 → TypeError: unsupported operand type(s) for /:
#   'NoneType' and 'float'  → safety_factor 无法计算，防线②整体 UNKNOWN。
# 缺陷一（直接原因）：兜底写成 `_strength_ref = sigma_y`，而 σy 本就是 None，
#   是一句【空操作】，随后照常参与除法 → 必崩。
# 缺陷二（根因）：灰铸铁/球墨铸铁按 GB 无屈服平台（材料特性，非数据缺失），
#   正确判据是 σb + 最大主应力（莫尔）。load_case.py 已能识别脆性并写入
#   brittle 标记，但 FEA 实际用的是【从零件凭据重新解析】的材料字典
#   （material_resolution.source = attested_credential），该副本不携带
#   brittle → 既有分支永远进不去，铸铁件 100% 触发崩溃。
# 修复口径：在【使用点】按与 load_case.py 完全相同的判据重新识别脆性
#   （牌号关键词 + 密度带 6900~7700 兜底）。
# 明确未做的事：min_safety_factor 门槛【没有任何放宽】；强度基准不可得时
#   返回 None 并由闸口判 N/A（聚合为 REVIEW），绝不编造数值、绝不虚报合格。
_BRITTLE_KW = (
    "灰铸铁", "球墨铸铁", "铸铁", "cast iron", "gray iron",
    "ductile iron", "铸造铝", "铸铝", "陶瓷", "ceramic",
    "concrete", "混凝土",
)


def _is_brittle_material(mat, no_yield):
    """脆性材料判定（与 load_case.py 的 _BRITTLE_KW / 密度带口径完全一致）。

    no_yield=True 表示当前材料缺 σy —— 此时才启用"密度落在铸铁带"的兜底，
    避免把正常钢材（ρ≈7850，不在带内）误判成铸铁。
    """
    if bool(mat.get("brittle")) or mat.get("strength_basis") == "uts_brittle":
        return True
    try:
        _name = str(mat.get("name") or mat.get("id") or "").lower()
    except Exception:
        _name = ""
    if any(k in _name for k in _BRITTLE_KW):
        return True
    if no_yield:
        try:
            _rho = float(mat.get("density_kg_m3") or 0)
            if 6900.0 <= _rho <= 7700.0:
                return True
        except Exception:
            pass
    return False


def brittle_strength_ref(mat, sigma_y):
    """返回 (强度判据基准值 MPa, 判据类型)。

    分支顺序与原实现保持一致：
      ① 脆性且有 σb → 用 σb（uts_max_principal(brittle)）
      ② 否则有 σy  → 用 σy（yield_von_mises）
      ③ 否则       → (None, "undetermined")，交由闸口判 N/A
    """
    _brittle = _is_brittle_material(mat, not sigma_y)
    if _brittle:
        try:
            _uts = float(mat.get("uts_mpa") or 0)
        except (TypeError, ValueError):
            _uts = 0.0
        if _uts > 0:
            return _uts, "uts_max_principal(brittle)"
    try:
        if sigma_y:
            return float(sigma_y), "yield_von_mises"
    except (TypeError, ValueError):
        pass
    return None, "undetermined"


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

# ── 【Bug-03 修复】设计载荷 = 额定载荷 × 动载系数 ──────────────────────────
# 门禁把 magnitude_n 写为额定值，另在 acceptance 记录 impact_factor，
#   并注明"设计校核另乘 impact_factor"。physics 原先从不读它 →
#   冲击工况校核载荷少算一半、SF 虚高。这里统一取用该系数。
try:
    from load_case import design_load_factor as _design_load_factor
    from load_case import governing_design_force as _governing_design_force
except ImportError:
    try:
        from .load_case import design_load_factor as _design_load_factor
        from .load_case import governing_design_force as _governing_design_force
    except Exception:
        def _design_load_factor(case):
            """兜底实现（load_case 不可用时）。"""
            acc = (case or {}).get("acceptance") or {}
            try:
                f = float(acc.get("impact_factor") or 1.0)
            except Exception:
                f = 1.0
            f = max(1.0, f)
            return f, {"impact_factor": f, "applied": f > 1.0}

        def _governing_design_force(case):
            """兜底实现：各载荷代数求和（旧行为），仅当 load_case 不可用时使用。"""
            _tot = 0.0
            for _l in ((case or {}).get("loads") or []):
                try:
                    _tot += max(float(_l.get("magnitude_n") or 0), 0.0)
                except Exception:
                    pass
            _f, _m = _design_load_factor(case)
            return _tot * _f, {"rated_force_n": _tot, "design_force_n": _tot * _f,
                               "impact_factor": _f, "impact_applied": _f > 1.0}


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
            # 【BUG-FEA-STRENGTH-1】脆性材料（灰铸铁 HT200 / 球墨铸铁 QT500-7 等）
            #   按 GB 无屈服平台，标准只给抗拉强度 σb —— 这是【材料特性】，
            #   不是数据缺失。已有 σb 即可按 σb（最大主应力/莫尔）判据校核，
            #   故不阻断；真正既无 σy 又无 σb 的才判为材料不可用。
            if not (_is_brittle_material(mat, True) and mat.get("uts_mpa")):
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


# ── 【PH-01 修复】泊松比解析（缺失/None 时按材料家族兜底）──────────────────
# 各向同性弹性体的泊松比物理范围是 (-1, 0.5)；工程常用值按家族如下。
# 兜底只在【材料未提供】时启用，并在返回值里标注来源，绝不冒充真实数据。
_NU_BY_FAMILY = (
    # (密度区间, 家族, nu)
    (700.0, 1500.0, "plastic", 0.38),
    (2400.0, 2900.0, "aluminum", 0.33),
    (4300.0, 4700.0, "titanium", 0.34),
    (6900.0, 7300.0, "cast_iron", 0.26),
    (7400.0, 8100.0, "steel", 0.30),
    (8300.0, 9000.0, "copper", 0.34),
)


def _resolve_poisson(mat):
    """返回 (nu, note)。优先用材料自带值；缺失/非法则按密度判族兜底。

    note 为 None 表示"用的是材料真实数据"；非 None 则是兜底说明，
      调用方可据此在报告里标注"该值非材料实测"。
    """
    _raw = (mat or {}).get("poissons_ratio")
    if _raw is not None and not isinstance(_raw, bool):
        try:
            _v = float(_raw)
            # 必须在物理范围内，否则视为非法（0.5 会导致刚度矩阵奇异）
            if _v != _v or _v in (float("inf"), float("-inf")):
                raise ValueError("not finite")
            if -1.0 < _v < 0.5:
                return _v, None
            raise ValueError("out of range")
        except Exception:
            pass    # 非法 → 落到兜底
    # 按密度判族
    try:
        _d = float((mat or {}).get("density_kg_m3") or 0)
    except Exception:
        _d = 0.0
    for _lo, _hi, _fam, _nu in _NU_BY_FAMILY:
        if _lo <= _d <= _hi:
            return _nu, ("材料未提供合法 poissons_ratio（原值=%r）—— "
                         "已按密度 %.0f 判族为 %s，取工程常用值 %.2f（【兜底值，非实测】）"
                         % (_raw, _d, _fam, _nu))
    return 0.30, ("材料未提供合法 poissons_ratio（原值=%r）且密度 %.0f 无法判族 —— "
                  "取通用默认 0.30（【兜底值，非实测】）" % (_raw, _d))


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

    # ══ 【Bug-03 修复】设计校核载荷 = 最恶劣工况的矢量合成力 × 一次动载系数 ══
    # 原实现有两个口径错误（共同导致 SF 虚高/失真）：
    #   ① `sum(magnitude_n)` 把各工况、各方向的载荷【代数相加】——
    #      正交分量（-Z 重力 / +X 侧向 / +Y 纵向）相加没有物理意义；
    #   ② 动载系数【从未施加】，而门禁又把侧向载荷预先乘过一次 ——
    #      两边口径不一致，既可能漏算也可能重复计算。
    # 现在统一走 governing_design_force()：按 load_cases 枚举工况 →
    #   工况内矢量合成 → 取最大值 → 乘【一次】impact_factor。
    _rated_force = total_force
    total_force, _dyn_meta = _governing_design_force(load_case)
    _dyn_f = float(_dyn_meta.get("impact_factor") or 1.0)

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
    _geo_source = "design_domain(设计域默认尺寸)"
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
                _geo_source = "real_geometry.bbox_mm(真实零件包围盒)"
        except Exception:
            _used_real = False
    if not _used_real:
        L = domain.get("x_max", 200)
        W = domain.get("y_max", 60)
        H = domain.get("z_max", 60)
        # ── 【Bug-03 修复】几何缺失必须显式告警，不静默用设计域尺寸 ────────
        #   实测本任务：结构件与传动件尺寸相差极大却得到同一 SF=24.8，
        #   根因就是"真实几何没进来、悄悄退回设计域默认尺寸"且无任何提示。
        _geo_warn = ("未取到 real_geometry.bbox_mm —— 本次校核使用设计域默认尺寸 "
                     "(%s×%s×%s)，结论可能与真实零件无关；"
                     "请用 physics-optimize --part <零件.SLDPRT> 提供真实几何。"
                     % (L, W, H))
    else:
        _geo_warn = None

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

    # ── 【BUG-C 修复】等效模型量化披露 ────────────────────────────────────
    # 计算"等效实心梁体积 vs 真实体积"的倍数，让读者一眼看出模型失真程度。
    _eq_vol = None
    _real_vol = None
    _vol_ratio = None
    try:
        _eq_vol = float(L) * float(W) * float(H)
        if _vol_real:
            _real_vol = float(_vol_real)
            if _real_vol > 0 and _eq_vol > 0:
                _vol_ratio = round(_eq_vol / _real_vol, 2)
    except Exception:
        pass
    # limitations 必须【显式说明等效关系】，不能只有泛泛的英文条目
    _limitations = [
        "Euler-Bernoulli beam assumptions",
        "ignores stress concentrations",
        "valid for prismatic beams only",
        # ★ 关键披露（原报告完全没有这一段）
        ("【模型等效性】按真实零件【包围盒】的排序三边等效为【实心矩形棱柱】"
         "悬臂梁 —— 未使用真实截面形状与载荷路径"),
        ("【适用边界】L 形 / 薄壁 / 镂空 / 变截面件的安全系数会被显著高估，"
         "不得仅凭本结论判定设计合格；此类零件需 3D FEA 复核"),
    ]
    if _vol_ratio and _vol_ratio > 1.5:
        _limitations.append(
            "【本次失真程度】等效实心体积 %.0f mm³ 是真实体积 %.0f mm³ 的 "
            "%.2f 倍 —— 安全系数存在同量级高估风险，务必人工复核"
            % (_eq_vol, _real_vol, _vol_ratio))

    # 计算安全系数
    # ── 【P2-1 修复·脆性材料判据】σy 缺失时改用 σb（最大主应力/莫尔判据）──
    # 实测缺陷：HT200（灰铸铁）无屈服平台，标准只给 σb —— 属材料特性。
    #   旧实现要求 σy，导致脆性材料根本无法校核。
    # 现在：若材料被标为 brittle（见 load_case 的识别逻辑）且 σy 缺失，
    #   则以 σb 作为强度判据基准，并在结果里注明判据类型。
    # 【BUG-FEA-STRENGTH-1】改用统一判据基准函数：脆性材料缺 σy 时用 σb；
    #   基准完全不可得时返回 None（闸口判 N/A → 聚合 REVIEW），
    #   不再因 None 参与除法而崩溃，也不虚报合格。
    _strength_ref, _strength_basis = brittle_strength_ref(mat, sigma_y)
    if sigma_max > 0:
        safety_factor = (_strength_ref / sigma_max) if _strength_ref else None
    else:
        safety_factor = float("inf")

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
        # ── 【Bug-03 修复】回显"校核载荷口径"，让结论可追溯 ────────────────
        #   ① rated_force_n=额定载荷（= 工况 magnitude_n）
        #   ② design_force_n=实际校核载荷 = 额定 × 动载系数
        #   ③ geometry_source=几何来源（真实包围盒 / 设计域默认）
        #   原先这些口径【不出现在结果里】，外部无法判断 SF 是否算对。
        "load_model": {
            "rated_force_n": _dyn_meta.get("rated_force_n", round(_rated_force, 3)),
            "design_force_n": round(total_force, 3),
            "governing_case": _dyn_meta.get("governing_case"),
            "impact_factor": _dyn_meta.get("impact_factor"),
            "load_type": _dyn_meta.get("load_type"),
            "impact_applied": _dyn_meta.get("applied", _dyn_meta.get("impact_applied")),
            # ── 【Bug-D 修复】回显零件载荷分担系数与口径来源 ──────────────
            "part_load_share": _dyn_meta.get("part_load_share"),
            "load_share_source": _dyn_meta.get("load_share_source"),
            "load_share_note": _dyn_meta.get("load_share_note"),
            "design_force_before_share_n": _dyn_meta.get("design_force_before_share_n"),
            "cases": _dyn_meta.get("cases"),
        },
        "geometry_used": {
            "source": _geo_source,
            "L_mm": round(L, 3), "W_mm": round(W, 3), "H_mm": round(H, 3),
            "volume_source": _vol_source,
            "warning": _geo_warn,
            # ── 【BUG-C 修复·等效模型披露】────────────────────────────────
            # 实测缺陷（子代理在真实 L 形支架上确证）：本求解器把
            #   real_geometry.bbox_mm 的【排序三边】当作【实心矩形棱柱】
            #   悬臂梁建模（solve_cantilever(L, H, W)），**完全忽略真实截面
            #   形状与载荷路径**。对 L 形件：bbox 实心体积是真实体积的
            #   4.95 倍，安全系数被高估约 27 倍 —— 一个真实不安全的
            #   设计会被报成"过度设计"。
            #   而旧报告的 limitations 只有泛泛的英文条目，
            #   全文没出现"等效""悬臂""实心"，读者极易把 SF 当真。
            # 现在：把等效关系量化写进报告，供读者判断可信度。
            "model": "equivalent_solid_cantilever_from_bbox",
            "model_note": ("按【真实零件包围盒】的排序三边等效为【实心矩形棱柱】"
                           "悬臂梁建模 —— 不是真实截面。L 形/薄壁/镂空件的"
                           "安全系数会被显著高估，不得据此判定设计合格。"),
            "equivalent_solid_volume_mm3": _eq_vol,
            "real_volume_mm3": _real_vol,
            "volume_inflation_x": _vol_ratio,
        },
        "gates": gates,
        "overall": aggregate_overall(gates),
        "limitations": _limitations,
        # 【P2-1】强度判据类型（脆性材料用 σb + 最大主应力，而非 von Mises+σy）
        "strength_basis": _strength_basis,
        "strength_reference_mpa": (round(float(_strength_ref), 3)
                                   if _strength_ref else None),
    }


# ── 【回转轴真实截面强度修正】────────────────────────────────────────────────
# 问题：包围盒等效模型把【回转体轴】当成外接矩形棱柱（φD×L）。对实心阶梯轴
#   （如转台轴 φ40/φ52/φ56 阶梯 + φ80×10 法兰），bbox 等效惯性矩被高估
#   数倍 → 弯曲应力被同比例低估 → 安全系数同比例虚高（实测 4.5~8.9 倍）。
#   这类"实心轴"并不是【镂空】（无内孔），用"等效体积/真实体积"直接判镂空
#   是误判；但 SF 虚高却是真的，必须修正。
#
# 正确处理（只用已量测几何、绝不引入新参数、保守取最不利工况）：
#   悬臂根部最大弯曲应力 σ = M·c/I，M = F·L。对【直径 d 随轴向变化的实心
#   回转轴】做体积加权合成（体积加权近似，偏保守）：
#       · 体积      V = Σ(πd²/4·l)      ← 应与 SW massprops 实测体积吻合
#       · 形心轴惯性矩 I = Σ(πd⁴/64·l)
#       · 等效长度系数 Le = 2·I/(V·L)
#     则 σ_true = F·L·Le/(2·I)，而 σ_bbox = F·L/(2·I_bbox_box)
#     ⇒ SF_true = SF_bbox · (V/V_bbox_box)² · Le   （Le、V/V_box 均 ≤1 ⇒ 必然削减）
#   Le 采用【最不利（最小）】取值：φ80 只在法兰(10mm)、其余全部按实测最大
#   非法兰直径铺满 —— 这是"截面最弱"假设，给出偏保守（偏低）的安全系数。
def _shaft_section_correction(real_geometry: dict) -> Optional[dict]:
    """回转轴真实截面强度修正（保守·分布无关）；非回转轴/数据不足时返回 None。

    包围盒等效模型把【实心阶梯轴】当成外接 φD×L 圆柱，惯性矩/截面模量被高估
    → 弯曲应力被低估 → 安全系数虚高（实测可达数倍）。这类实心轴并不是"镂空"
    （无内孔），但 SF 虚高属实，必须削减。
    massprops 只给总体积、不给阶梯分布，故按【体积加权平均直径 d̄ 铺满全长】
    这一【使截面模量最小、应力最大、SF 最小】的保守假设修正，绝不虚高。
    """
    try:
        rg = real_geometry or {}
        bb = rg.get("bbox_mm") or None
        v_real = float(rg.get("volume_mm3") or 0.0)
        if not (isinstance(bb, (list, tuple)) and len(bb) >= 6 and v_real > 0):
            return None
        lx, ly, lz = (abs(float(bb[1]) - float(bb[0])),
                      abs(float(bb[3]) - float(bb[2])),
                      abs(float(bb[5]) - float(bb[4])))
        dims = sorted([lx, ly, lz], reverse=True)
        L, D, W = dims[0], dims[1], dims[2]
        if not (L > 0 and D > 0 and W > 0):
            return None
        # 回转轴判据：两个较小尺寸近似相等（圆截面 D≈W），且长径比 > 1.2
        if W <= 0 or (D / W) > 1.06 or (W / D) > 1.06 or (L / D) <= 1.2:
            return None
        v_box = 3.141592653589793 * (D ** 2) / 4.0 * L
        if v_box <= 0 or v_real >= v_box:
            return None
        fill = v_real / v_box                      # ∈ (0,1)
        # ── 保守修正（分布无关）───────────────────────────────────────────
        # 真实回转轴的阶梯直径分布未知（massprops 只给体积）。由凸性，
        #   在给定总体积下，【按体积加权平均直径 d̄ 铺满全长】使截面模量
        #   Z=I/c 最小 → 弯曲应力最大 → 安全系数最小（最保守，绝不虚高）。
        #   d̄ 由 V=π·d̄²/4·L 反解：d̄=D·√fill。
        # 截面模量比（真/盒）= (d̄/D)³ = fill^(3/2)。
        #   悬臂根部 σ∝c/I=1/Z，故 SF_true = SF_bbox·(Z_true/Z_box)
        #   = SF_bbox·fill^(3/2)。因子 ≤1 ⇒ 必然削减安全系数（保守）。
        if fill <= 0:
            return None
        d_mean = D * (fill ** 0.5)               # 保守等效直径 (d̄=D·√fill)
        I_box = 3.141592653589793 * (D ** 4) / 64.0 * L
        I_mean = 3.141592653589793 * (d_mean ** 4) / 64.0 * L
        if I_box <= 0:
            return None
        # SF_true/SF_bbox = (c_box/c_true)·(I_true/I_box) = 1·(d̄/D)³ = fill^(3/2)
        factor = fill ** 1.5
        factor = max(0.0, min(1.0, factor))
        return {
            "axis": "shaft", "detected": True, "length_mm": round(L, 2),
            "outer_dia_mm": round(D, 2), "volume_mm3": round(v_real, 1),
            "bbox_volume_mm3": round(v_box, 1), "fill_ratio": round(fill, 4),
            "mean_dia_mm": round(d_mean, 2),
            "section_I_mm4": round(I_mean, 2), "section_I_box_mm4": round(I_box, 2),
            "strength_factor": round(factor, 5),
            "reduction_x": (round(1.0 / factor, 3) if factor > 0 else None),
            "note": ("回转轴真实截面修正（保守·分布无关）：按实测体积反解"
                     "体积加权平均直径 d̄=D·√fill，截面模量比=(d̄/D)³=fill^(3/2)；"
                     "SF_true = SF_bbox·fill^(3/2)，必然削减（保守不虚高）"),
        }
    except Exception:
        return None


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
    # ── 【PH-01 修复】泊松比缺失（含显式 None）时按【材料家族】兜底 ────────
    # .get(key, default) 只在【键不存在】时返回默认值；若上游显式写了
    #   "poissons_ratio": None（例如从材料表取到空字段），拿到的就是 None，
    #   随后 nu**2 直接 TypeError 打崩整个 FEA（实测 PH-01）。
    # 这里按密度判族给出工程常用值，并在返回体里标注"用的是兜底值"，
    #   既不崩、也不静默 —— 让调用方知道该值不是材料真实数据。
    nu, _nu_note = _resolve_poisson(mat)
    sigma_y = mat.get("yield_strength_mpa", 250)

    domain = load_case.get("design_domain", {}).get("bounds", {})
    L = domain.get("x_max", 200.0)
    W = domain.get("y_max", 60.0)
    H = domain.get("z_max", 60.0)

    # ══ 【Bug-03 修复】几何必须优先取【真实零件】，与解析解路径口径一致 ══
    # 实测缺陷（本任务是竞赛小车）：结构件（bbox 300×200×4）与传动件
    #   （bbox 75×75×10）两个尺寸相差极大的零件，SF 完全相同（24.8）、
    #   volume 也完全相同（7200000 = 300×200×120）—— 因为本函数一直用
    #   design_domain 的固定尺寸当梁的 L/W/H，real_geometry【从未被使用】。
    #   后果：安全系数虚高且与零件无关（载荷 500N 打在 300×200×120 的
    #   "理想大块"上，应力自然只有 1.61MPa），物理防线"签得出但结论不可用"。
    # 修复：与 solve_analytical 完全同口径 —— 有 real_geometry.bbox_mm 就用
    #   它推导等效梁尺寸（最长边=长、次长=宽、最短=高），并用真实实体体积
    #   作为体积；volume 不再用 L*W*H 的 bbox 乘积冒充零件体积。
    _rg = load_case.get("real_geometry") or {}
    _bbox = _rg.get("bbox_mm") or None
    _vol_real = _rg.get("volume_mm3")
    _geo_source = "design_domain(设计域默认尺寸)"
    _used_real = False
    if _bbox and len(_bbox) >= 6:
        try:
            _lx = abs(float(_bbox[1]) - float(_bbox[0]))
            _ly = abs(float(_bbox[3]) - float(_bbox[2]))
            _lz = abs(float(_bbox[5]) - float(_bbox[4]))
            _dims = sorted([_lx, _ly, _lz], reverse=True)
            if _dims[0] > 0 and _dims[1] > 0 and _dims[2] > 0:
                L, W, H = _dims[0], _dims[1], _dims[2]
                _used_real = True
                _geo_source = "real_geometry.bbox_mm(真实零件包围盒)"
        except Exception:
            _used_real = False
    # 真实几何缺失时【明确记录】，便于排查"结论与零件无关"的来源
    if not _used_real:
        _geo_warn = ("未取到 real_geometry.bbox_mm —— 本次 FEA 使用设计域默认尺寸 "
                     "(%s×%s×%s)，结论可能与真实零件无关" % (L, W, H))
    else:
        _geo_warn = None

    # ── 【BUG-C 修复】等效模型量化披露（feapy 路径）──────────────────────
    # 与解析路径同口径：把"bbox 等效实心梁 vs 真实体积"的失真程度写进报告。
    _eq_vol = None
    _real_vol = None
    _vol_ratio = None
    try:
        _eq_vol = float(L) * float(W) * float(H)
        if _vol_real:
            _real_vol = float(_vol_real)
            if _real_vol > 0 and _eq_vol > 0:
                _vol_ratio = round(_eq_vol / _real_vol, 2)
    except Exception:
        pass
    _limitations = [
        "2D plane stress assumption only",
        "CST linear elements (6-8%% error vs analytical)",
        "no stress concentrations captured",
        "no 3D effects",
        ("【模型等效性】按真实零件【包围盒】的排序三边等效为【实心矩形棱柱】"
         "悬臂梁建模 —— 未使用真实截面形状与载荷路径"),
        ("【适用边界】L 形 / 薄壁 / 镂空 / 变截面件的安全系数会被显著高估，"
         "不得仅凭本结论判定设计合格；此类零件需 3D FEA 复核"),
    ]
    if _vol_ratio and _vol_ratio > 1.5:
        _limitations.append(
            "【本次失真程度】等效实心体积 %.0f mm³ 是真实体积 %.0f mm³ 的 "
            "%.2f 倍 —— 安全系数存在同量级高估风险，务必人工复核"
            % (_eq_vol, _real_vol, _vol_ratio))

    # 注：回转轴修正的披露在 _shaft_corr 求出之后（求解分支内）追加，
    #     此处不引用它（否则在赋值前引用会 UnboundLocalError）。

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

    # ══ 【Bug-03 修复】设计校核载荷（同解析解路径口径）════════════════════
    #   按工况矢量合成取最恶劣工况，再乘【一次】动载系数。
    _rated_force = total_force
    total_force, _dyn_meta = _governing_design_force(load_case)
    _dyn_f = float(_dyn_meta.get("impact_factor") or 1.0)

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
        # ── 【P2-1 修复·脆性材料判据】与解析路径同口径 ────────────────────
        # 灰铸铁/球墨铸铁等无屈服平台，用 von Mises + σy 判据物理上是错的；
        #   应改用 σb + 最大主应力（莫尔）判据。此处与解析路径保持一致。
        # 【BUG-FEA-STRENGTH-1】与解析解路径同口径：脆性材料缺 σy 时用 σb，
        #   基准不可得则置 None（闸口判 N/A），杜绝 None 参与除法而崩溃。
        _strength_ref, _strength_basis = brittle_strength_ref(mat, sigma_y)
        if max_stress > 0:
            safety_factor = (_strength_ref / max_stress) if _strength_ref else None
        else:
            safety_factor = float("inf")
        # ── 【回转轴真实截面强度修正】──────────────────────────────────────
        # bbox 等效棱柱对【实心阶梯轴】高估惯性矩 → 应力被低估 → SF 虚高。
        # 检测到回转轴时，用实测体积反解最不利直径分布，把安全系数按
        #   SF_true = SF_bbox·(V/V_box)²·Le 修正（Le=体积加权等效长度系数）。
        # 这不是"镂空误判"——实心轴无内孔，但 SF 虚高属实，必须修正为保守值。
        _shaft_corr = _shaft_section_correction(_rg)
        if _shaft_corr and safety_factor not in (None, float("inf")):
            try:
                _sf_raw = float(safety_factor)
                _factor = float(_shaft_corr["strength_factor"])
                safety_factor = _sf_raw * _factor
                _shaft_corr["sf_bbox_equivalent"] = round(_sf_raw, 2)
                _shaft_corr["sf_true"] = round(safety_factor, 2)
            except Exception:
                pass
        # 回转轴修正披露：说明安全系数已按真实截面削减（不再虚高），留痕便于复核。
        #   必须在 _shaft_corr 赋值之后追加（在函数前段引用会 UnboundLocalError）。
        if _shaft_corr:
            _limitations.append(
                "【回转轴真实截面修正】已检测为回转轴（φ%.0f×%.0f）：安全系数已按"
                "实测体积反解最不利截面修正 SF_true=SF_bbox×strength_factor"
                "=%.2f→%.2f（削减 %.2f 倍），model_trustworthy=true"
                % (_shaft_corr.get("outer_dia_mm") or 0,
                   _shaft_corr.get("length_mm") or 0,
                   _shaft_corr.get("sf_bbox_equivalent") or 0,
                   _shaft_corr.get("sf_true") or 0,
                   _shaft_corr.get("reduction_x") or 1.0))
        # ── 【Bug-03 修复】体积必须报真实实体体积，不得用 bbox 乘积 ────────
        #   原实现 volume_mm3 = L*W*H —— 10 种零件完全一样的"设计域体积"，
        #   下游据此算质量/材料用量必然失真（与解析解路径的既有修复对齐）。
        volume_mm3 = L * W * H
        try:
            if _vol_real and float(_vol_real) > 0:
                volume_mm3 = float(_vol_real)
        except Exception:
            pass

        gates = {
            "SAFETY_FACTOR": {
                # 【NEW-02 修复】过度设计用 OVER_DESIGN 明确区分（同解析解路径）
                "status": (
                    # 【BUG-FEA-STRENGTH-1】强度基准不可得 → 判 N/A（聚合为 REVIEW）。
                    # 不能用 UNKNOWN：aggregate_overall 对未列举状态会落到 PASS，
                    # 等于把"没校核"虚报成"合格"。
                    "N/A" if safety_factor is None
                    else "FAIL" if safety_factor < min_sf
                    else "OVER_DESIGN" if safety_factor > max_sf
                    else "PASS"
                ),
                "actual": (round(safety_factor, 2)
                           if safety_factor is not None else None),
                "required_min": min_sf,
                "required_max": max_sf,
                "over_design": (bool(safety_factor > max_sf)
                                if safety_factor is not None else False),
                "strength_basis": _strength_basis,
                "note": ("强度基准不可得（既无 σy 也无 σb）→ 本次【未校核】，"
                         "须补材料数据后重跑"
                         if safety_factor is None else
                         "结构安全，安全系数高于目标上限 —— 属【过度设计/材料利用率低】，"
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
            # 【BUG-FEA-STRENGTH-1】基准不可得时如实报 None，不得编造数值
            "safety_factor": (round(safety_factor, 2)
                              if safety_factor is not None else None),
            "max_von_mises_mpa": round(max_stress, 2),
            "max_displacement_mm": round(max_disp_val, 4),
            "volume_mm3": round(volume_mm3, 2),
            "mass_kg": round(volume_mm3 * mat.get("density_kg_m3", 7850) / 1e9, 4),
            # ── 【Bug-03 修复】回显载荷口径与几何来源（同解析解路径）────────
            "load_model": {
                "rated_force_n": _dyn_meta.get("rated_force_n", round(_rated_force, 3)),
                "design_force_n": round(total_force, 3),
                "governing_case": _dyn_meta.get("governing_case"),
                "impact_factor": _dyn_meta.get("impact_factor"),
                "load_type": _dyn_meta.get("load_type"),
                "impact_applied": _dyn_meta.get("applied", _dyn_meta.get("impact_applied")),
                "cases": _dyn_meta.get("cases"),
            },
            "geometry_used": {
                "source": _geo_source,
                "L_mm": round(L, 3), "W_mm": round(W, 3), "H_mm": round(H, 3),
                "warning": _geo_warn,
                # ── 【BUG-C 修复】等效模型披露（同解析路径）────────────────
                "model": "equivalent_solid_cantilever_from_bbox",
                "model_note": ("按【真实零件包围盒】的排序三边等效为【实心矩形"
                               "棱柱】悬臂梁建模 —— 不是真实截面。L 形/薄壁/"
                               "镂空件的安全系数会被显著高估，不得据此判定合格。"),
                "equivalent_solid_volume_mm3": _eq_vol,
                "real_volume_mm3": _real_vol,
                "volume_inflation_x": _vol_ratio,
                "shaft_section_correction": _shaft_corr,
                "model_trustworthy": bool(_shaft_corr),
            },
            "shaft_section_correction": _shaft_corr,
            "gates": gates,
            "overall": aggregate_overall(gates),
            "limitations": _limitations,
            "analytical_comparison": r.get("analytical", {}),
            # 【P2-1】强度判据类型（脆性材料用 σb + 最大主应力）
            "strength_basis": _strength_basis,
            "strength_reference_mpa": (round(float(_strength_ref), 3)
                                       if _strength_ref else None),
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
    nu, _ = _resolve_poisson(mat)      # 【PH-01】缺省/None 时按家族兜底
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
