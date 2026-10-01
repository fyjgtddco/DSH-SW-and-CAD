# -*- coding: utf-8 -*-
"""
fatigue.py — 疲劳强度与设计寿命（30 年）校核
==============================================
【BUG-02 修复】原工具链只有线性静力（2D 平面应力），完全没有疲劳/寿命校核，
  用户"30 年使用寿命"这一核心需求无法验证。本模块补齐该能力。

方法链（工程标准做法，纯 Python + numpy，无外部依赖）：

  1) S-N 曲线（Basquin 幂律）
       sigma_a = sigma_f' * (2N)^b
     钢材：由 UTS 估算疲劳强度系数 sigma_f' 与指数 b
     铝合金：无真正疲劳极限，取 5×10^8 次为条件疲劳极限

  2) 疲劳极限修正（Marin 系数，Shigley 体系）
       Se = ka·kb·kc·kd·ke·Se'
       ka 表面加工  kb 尺寸  kc 载荷型式  kd 温度  ke 可靠度

  3) 平均应力修正（三种准则可选）
       Goodman : sigma_a/Se + sigma_m/Su = 1/n      （保守，默认）
       Soderberg: sigma_a/Se + sigma_m/Sy = 1/n     （最保守）
       Gerber  : n·sigma_a/Se + (n·sigma_m/Su)^2 = 1（延性材料较贴合）

  4) 载荷谱 → Miner 线性累积损伤
       D = Σ (n_i / N_i)
       30 年寿命判定：D_30y < 1 → 通过；否则给出可承受年限 T = 1 / (D/年)

  5) 输出结构
       {ok, method, endurance_limit_mpa, fatigue_sf, damage_30y,
        life_years_allowable, verdict, gates, details, limitations}

设计原则：宁可保守也不虚高。所有输入缺失时用明确的默认值，并在结果里
标注 default_used，绝不静默假设。
"""
from typing import Any, Optional

# ── 可靠度系数 ke（Shigley 表，对应 90%~99.99% 可靠度）──────────────────
_RELIABILITY_KE = {
    0.50: 1.000, 0.90: 0.897, 0.95: 0.868, 0.99: 0.814,
    0.999: 0.753, 0.9999: 0.702,
}

# ── 表面加工系数 ka 的经验参数（ka = a * Su^b，Su 单位 MPa）──────────────
_SURFACE_AB = {
    # 加工方式: (a, b)
    "ground":     (1.58, -0.085),   # 磨削
    "machined":   (4.51, -0.265),   # 机加工/冷拔
    "cold_drawn": (4.51, -0.265),
    "hot_rolled": (57.7, -0.718),   # 热轧
    "as_forged":  (272.0, -0.995),  # 锻造
}

# ── 铝合金（无真实疲劳极限，用 5e8 次条件极限）────────────────────────────
_ALUMINUM_IDS = ("AL_", "ALUMINUM", "6061", "7075", "5052", "1060")


def _is_aluminum(mat: dict) -> bool:
    hay = (str(mat.get("id", "")) + " " + str(mat.get("name", ""))).upper()
    return any(k in hay for k in _ALUMINUM_IDS)


def _surface_factor(finish: str, uts_mpa: float) -> tuple[float, str]:
    """表面加工修正系数 ka。"""
    key = str(finish or "machined").strip().lower().replace(" ", "_")
    # 中文别名
    alias = {"磨削": "ground", "机加工": "machined", "车削": "machined",
             "热轧": "hot_rolled", "锻造": "as_forged", "铸造": "as_forged",
             "铸件": "as_forged", "钣金": "cold_drawn"}
    key = alias.get(key, key)
    a, b = _SURFACE_AB.get(key, _SURFACE_AB["machined"])
    ka = a * (max(uts_mpa, 1.0) ** b)
    ka = min(max(ka, 0.05), 1.0)     # 物理上不超过 1
    return ka, key


def _size_factor(diameter_mm: Optional[float]) -> tuple[float, str]:
    """尺寸修正系数 kb（Shigley 旋转弯曲）。"""
    if diameter_mm is None or diameter_mm <= 0:
        return 0.85, "default(未给特征尺寸，取 0.85)"
    d = float(diameter_mm)
    if d <= 8.0:
        return 1.0, "d<=8mm → 1.0"
    if d <= 51.0:
        return round(0.869 * (d ** -0.097), 4), "8<d<=51mm 公式"
    if d <= 254.0:
        return round(1.189 * (d ** -0.097), 4), "51<d<=254mm 公式"
    return 0.60, "d>254mm → 0.60（保守）"


def _load_factor(load_type: str, mode: str = "bending") -> tuple[float, str]:
    """载荷型式修正系数 kc。"""
    lt = str(load_type or "static").lower()
    if lt in ("impact", "冲击"):
        return 1.0, "冲击/弯曲 → 1.0"
    if mode == "axial":
        return 0.85, "轴向 → 0.85"
    if mode == "torsion":
        return 0.59, "扭转 → 0.59"
    return 1.0, "弯曲 → 1.0"


def _reliability_factor(reliability: float) -> tuple[float, str]:
    """可靠度系数 ke（取不超过给定可靠度的档位）。"""
    try:
        r = float(reliability)
    except Exception:
        r = 0.99
    if r <= 0 or r >= 1:
        r = 0.99
    best = 0.50
    for k in sorted(_RELIABILITY_KE):
        if k <= r + 1e-9:
            best = k
    return _RELIABILITY_KE[best], "可靠度 %.4g → ke=%.3f" % (r, _RELIABILITY_KE[best])


def endurance_limit(mat: dict, surface: str = "machined",
                    diameter_mm: Optional[float] = None,
                    load_type: str = "static", mode: str = "bending",
                    reliability: float = 0.99,
                    temperature_c: float = 25.0) -> dict:
    """计算修正后的疲劳极限 Se（MPa）。

    钢材：Se' = 0.5·Su（Su <= 1400MPa）；Su > 1400 时 Se' = 700MPa
    铝合金：无真实疲劳极限 → 以 5×10^8 次的条件强度作 Se'（保守取 0.4·Su）
    """
    uts = float(mat.get("uts_mpa") or 0) or float(mat.get("yield_strength_mpa") or 0) * 1.2
    sy = float(mat.get("yield_strength_mpa") or 0)
    defaults = []
    if uts <= 0:
        uts = 400.0
        defaults.append("uts_mpa(默认 400)")
    if sy <= 0:
        sy = 250.0
        defaults.append("yield_strength_mpa(默认 250)")

    alu = _is_aluminum(mat)
    if alu:
        se_prime = 0.4 * uts            # 铝合金保守条件疲劳极限
        basis = "铝合金：Se'=0.4·Su（无真实疲劳极限，按 5e8 次条件极限）"
        # 铝合金对表面/尺寸更敏感，ka 使用更保守下限
    else:
        se_prime = 0.5 * uts if uts <= 1400.0 else 700.0
        basis = "钢材：Se'=0.5·Su（Su<=1400MPa）" if uts <= 1400.0 else "钢材：Su>1400 → Se'=700MPa"

    ka, ka_note = _surface_factor(surface, uts)
    kb, kb_note = _size_factor(diameter_mm)
    kc, kc_note = _load_factor(load_type, mode)
    ke, ke_note = _reliability_factor(reliability)

    # 温度系数 kd：钢在 <450°C 取 1.0；铝在 >150°C 明显下降
    try:
        T = float(temperature_c)
    except Exception:
        T = 25.0
    if alu and T > 100.0:
        kd = max(0.5, 1.0 - (T - 100.0) * 0.002)
        kd_note = "铝 %.0f°C → kd=%.3f" % (T, kd)
    elif (not alu) and T > 450.0:
        kd = max(0.5, 1.0 - (T - 450.0) * 0.001)
        kd_note = "钢 %.0f°C → kd=%.3f" % (T, kd)
    else:
        kd, kd_note = 1.0, "常温 → kd=1.0"

    se = se_prime * ka * kb * kc * kd * ke
    # 疲劳极限不应超过屈服强度（否则静强度先失效）
    capped = False
    if sy > 0 and se > sy:
        se = sy * 0.95
        capped = True

    return {
        "Se_prime_mpa": round(se_prime, 2),
        "Se_mpa": round(se, 2),
        "uts_mpa": round(uts, 1),
        "yield_mpa": round(sy, 1),
        "is_aluminum": alu,
        "factors": {"ka": round(ka, 4), "kb": round(kb, 4), "kc": round(kc, 4),
                    "kd": round(kd, 4), "ke": round(ke, 4)},
        "notes": {"basis": basis, "ka": ka_note, "kb": kb_note,
                  "kc": kc_note, "kd": kd_note, "ke": ke_note},
        "capped_by_yield": capped,
        "default_used": defaults,
    }


def _sn_life(sigma_a: float, mat: dict, se_info: dict) -> float:
    """由 Basquin 幂律求给定应力幅下的失效循环数 N。

    sigma_a = sigma_f' * (2N)^b
      → N = 0.5 * (sigma_a / sigma_f')^(1/b)
    sigma_a <= Se（且为钢材）→ 视为无限寿命（返回 inf）。
    铝合金无无限寿命平台，即使低于 Se 也按 5e8 次封顶。
    """
    if sigma_a <= 0:
        return float("inf")
    se = se_info["Se_mpa"]
    uts = se_info["uts_mpa"]
    is_alu = se_info["is_aluminum"]
    # 钢材：应力幅低于疲劳极限 → 无限寿命
    if (not is_alu) and sigma_a <= se:
        return float("inf")
    # 铝合金条件疲劳极限同样视为寿命上限 5e8
    if is_alu and sigma_a <= se:
        return 5e8
    if sigma_a >= uts:
        return 1e3            # 静强度即失效，给最小寿命 1e3 次
    # sigma_f' 估算（Shigley）：钢 sigma_f' ≈ Su + 345MPa；铝 ≈ 1.5·Su
    if is_alu:
        sigma_f_prime = 1.5 * uts
        b = -0.12
    else:
        sigma_f_prime = uts + 345.0
        b = -0.085
    try:
        ratio = sigma_a / sigma_f_prime
        if ratio <= 0:
            return float("inf")
        n = 0.5 * (ratio ** (1.0 / b))
    except Exception:
        return 1e3
    # 数值边界：循环数不应小于 1e3、不应大于 1e12
    if n < 1e3:
        return 1e3
    if n > 1e12:
        return 1e12
    # 铝合金封顶 5e8
    if is_alu:
        n = min(n, 5e8)
    return n


def _mean_stress_sf(sigma_a: float, sigma_m: float, se: float, sy: float,
                    uts: float, criterion: str = "goodman") -> tuple[float, str]:
    """平均应力修正后的疲劳安全系数 n。"""
    if sigma_a <= 0:
        # 纯静载：用屈服/强度判据
        if sigma_m <= 0:
            return float("inf"), "无交变应力"
        return (sy / sigma_m if sigma_m > 0 else float("inf")), "纯静载（按屈服）"
    crit = str(criterion or "goodman").lower()
    try:
        if crit == "soderberg":
            if se <= 0 or sy <= 0:
                return 0.0, "参数不足"
            return 1.0 / (sigma_a / se + sigma_m / sy), "Soderberg"
        if crit == "gerber":
            if se <= 0 or uts <= 0:
                return 0.0, "参数不足"
            # n·σa/Se + (n·σm/Su)^2 = 1 → 解一元二次
            A = (sigma_m / uts) ** 2
            B = sigma_a / se
            C = -1.0
            if abs(A) < 1e-12:
                return (1.0 / B if B > 0 else float("inf")), "Gerber(退化)"
            disc = B * B - 4 * A * C
            if disc < 0:
                return 0.0, "Gerber 无实数解"
            n = (-B + disc ** 0.5) / (2 * A)
            return n, "Gerber"
        # 默认 Goodman
        if se <= 0 or uts <= 0:
            return 0.0, "参数不足"
        return 1.0 / (sigma_a / se + sigma_m / uts), "Goodman"
    except Exception:
        return 0.0, "计算异常"


def analyze_fatigue(
    max_von_mises_mpa: float,
    mat: dict,
    design_life_years: float = 30.0,
    cycles_per_year: float = 200000.0,
    load_type: str = "cyclic",
    impact_factor: float = 1.0,
    surface: str = "machined",
    diameter_mm: Optional[float] = None,
    reliability: float = 0.99,
    temperature_c: float = 25.0,
    criterion: str = "goodman",
    required_life_years: Optional[float] = None,
    apply_impact_to_amplitude: bool = False,
) -> dict[str, Any]:
    """疲劳强度 + 设计寿命（默认 30 年）完整校核。

    Args:
        max_von_mises_mpa: 静力 FEA 得到的最大 von Mises 应力（额定载荷下）
        mat: 材料字典（需 uts_mpa / yield_strength_mpa）
        design_life_years: 目标设计寿命（年），默认 30
        cycles_per_year: 每年动作次数（载荷循环数）
        load_type: static / cyclic / impact
        impact_factor: 动载系数（用于载荷谱峰值）
        criterion: goodman / soderberg / gerber
        apply_impact_to_amplitude: True 时峰值应力已含动载系数

    Returns:
        dict —— 见模块 docstring 的结构说明
    """
    req_years = float(required_life_years if required_life_years is not None
                      else (design_life_years or 30.0))
    cpy = float(cycles_per_year or 200000.0)
    if cpy <= 0:
        cpy = 200000.0

    se_info = endurance_limit(mat, surface=surface, diameter_mm=diameter_mm,
                              load_type=load_type, reliability=reliability,
                              temperature_c=temperature_c)
    se = se_info["Se_mpa"]
    sy = se_info["yield_mpa"]
    uts = se_info["uts_mpa"]

    # ── 应力幅 / 平均应力分解 ────────────────────────────────────────────
    # 工程惯例：脉动循环（0→峰值）时 σa = σm = σ_peak/2；
    #   对称循环时 σa = σ_peak、σm = 0。
    # 这里采用【较保守的脉动循环假设】（多数机械往复工况），并在结果中标注。
    sigma_peak = float(max_von_mises_mpa or 0.0)
    if apply_impact_to_amplitude and impact_factor and impact_factor > 1.0:
        sigma_peak *= float(impact_factor)
    sigma_a = sigma_peak / 2.0
    sigma_m = sigma_peak / 2.0
    assumption = ("脉动循环假设: σa=σm=σ_peak/2（0→峰值往复，工程保守取法）"
                  if load_type in ("cyclic", "impact", "动态", "循环", "冲击")
                  else "静态工况: 以 σ_peak/2 作等效交变幅做保守疲劳评估")

    sf, crit_note = _mean_stress_sf(sigma_a, sigma_m, se, sy, uts, criterion)

    # ── 载荷谱与 Miner 累积损伤 ──────────────────────────────────────────
    # 谱型（工程简化，五级）：各级应力幅系数 × 循环占比
    SPECTRUM = (
        (1.00, 0.05),    # 满负荷 5%
        (0.80, 0.15),    # 80%  15%
        (0.60, 0.30),    # 60%  30%
        (0.40, 0.30),    # 40%  30%
        (0.20, 0.20),    # 20%  20%
    )
    levels = []
    damage_per_year = 0.0
    for frac, share in SPECTRUM:
        sa_i = sigma_a * frac
        sm_i = sigma_m * frac
        # 平均应力修正到等效应力幅（Goodman 折算），再做 S-N
        if uts > 0:
            sa_eq = sa_i / max(1.0 - sm_i / uts, 1e-6) if sm_i > 0 else sa_i
        else:
            sa_eq = sa_i
        n_fail = _sn_life(sa_eq, mat, se_info)
        n_i = cpy * share
        if n_fail == float("inf") or n_fail <= 0:
            d_i = 0.0
        else:
            d_i = n_i / n_fail
        damage_per_year += d_i
        levels.append({
            "amplitude_mpa": round(sa_i, 3),
            "mean_mpa": round(sm_i, 3),
            "equivalent_amplitude_mpa": round(sa_eq, 3),
            "cycles_per_year": round(n_i, 1),
            "allowable_cycles": (None if n_fail == float("inf") else round(n_fail, 1)),
            "infinite_life": n_fail == float("inf"),
            "damage_per_year": round(d_i, 10),
        })

    damage_req = damage_per_year * req_years
    # 可承受年限：D=1 时的年限
    if damage_per_year <= 0:
        life_allowable = float("inf")
    else:
        life_allowable = 1.0 / damage_per_year

    # ── 判定 ────────────────────────────────────────────────────────────
    min_sf_fatigue = 1.5          # 疲劳安全系数下限（工程惯例 1.3~2.0）
    d_limit = 1.0                 # Miner 判据
    sf_ok = sf >= min_sf_fatigue
    life_ok = damage_req < d_limit
    static_ok = (sigma_peak < sy) if sy > 0 else True
    # ── 疲劳强度硬判据：Goodman/Soderberg/Gerber 图上 n<1 = 越过设计线 ──
    # 注意：sf<1 与"寿命够"可以并存（应力幅略高于疲劳极限时仍可能有有限寿命），
    #   但工程上 n<1 表示疲劳强度不足，**不得判 PASS**（宁可保守，杜绝虚高）。
    sf_hard_fail = sf < 1.0

    if not static_ok:
        verdict = "FAIL"
        reason = "静强度已不足：σ_peak=%.2fMPa ≥ σy=%.1fMPa" % (sigma_peak, sy)
    elif not life_ok:
        verdict = "FAIL"
        reason = ("疲劳寿命不足：%d 年累积损伤 D=%.4g ≥ 1，可承受约 %.1f 年"
                  % (int(req_years), damage_req, life_allowable))
    elif sf_hard_fail:
        verdict = "FAIL"
        reason = ("疲劳强度不足：安全系数 %.2f < 1.0（%s 判据下已越过设计线）；"
                  "尽管 Miner 寿命 %.0f 年尚可，仍不得判为合格"
                  % (sf, crit_note, life_allowable if life_allowable != float("inf") else 0))
    elif not sf_ok:
        verdict = "REVIEW"
        reason = ("疲劳安全系数 %.2f < %.1f（寿命尚可，但裕度偏低，建议加强/换材）"
                  % (sf, min_sf_fatigue))
    else:
        verdict = "PASS"
        reason = ("疲劳安全系数 %.2f ≥ %.1f 且 %d 年累积损伤 D=%.4g < 1"
                  % (sf, min_sf_fatigue, int(req_years), damage_req))

    gates = {
        "FATIGUE_STRENGTH": {
            "status": ("PASS" if sf_ok else "FAIL"),
            "actual_sf": round(sf, 3),
            "required_min": min_sf_fatigue,
            "endurance_limit_mpa": round(se, 2),
            "criterion": crit_note,
            "below_unity": sf_hard_fail,
        },
        "FATIGUE_LIFE": {
            "status": ("PASS" if life_ok else "FAIL"),
            "required_years": req_years,
            "damage_at_required_life": round(damage_req, 6),
            "damage_limit": d_limit,
            "allowable_life_years": (None if life_allowable == float("inf")
                                     else round(life_allowable, 2)),
        },
        "STATIC_STRENGTH": {
            "status": ("PASS" if static_ok else "FAIL"),
            "peak_stress_mpa": round(sigma_peak, 3),
            "yield_mpa": round(sy, 1),
        },
    }

    return {
        "ok": verdict != "FAIL",
        "method": "fatigue_sn_miner",
        "backend": "basquin_sn_marin_miner",
        "verdict": verdict,
        "reason": reason,
        "design_life_years": req_years,
        "cycles_per_year": cpy,
        "total_cycles_design_life": round(cpy * req_years, 1),
        "endurance_limit_mpa": round(se, 2),
        "fatigue_sf": round(sf, 3),
        "min_required_fatigue_sf": min_sf_fatigue,
        "damage_design_life": round(damage_req, 6),
        "life_years_allowable": (None if life_allowable == float("inf")
                                 else round(life_allowable, 2)),
        "stress_amplitude_mpa": round(sigma_a, 3),
        "mean_stress_mpa": round(sigma_m, 3),
        "peak_stress_mpa": round(sigma_peak, 3),
        "criterion": criterion,
        "endurance_detail": se_info,
        "load_spectrum": levels,
        "assumption": assumption,
        "gates": gates,
        "limitations": [
            "S-N 曲线由 UTS 经验估算（非实测材料数据）",
            "Miner 线性累积损伤忽略载荷顺序与过载迟滞效应",
            "未计入焊接接头/缺口应力集中系数 Kt（保守起见请另乘 Kt）",
            "脉动循环假设（σa=σm=σpeak/2）；对称循环工况偏保守",
            "尺寸/表面/可靠度系数取自 Shigley 经验表",
        ],
    }


def analyze_fatigue_from_case(load_case: dict, fea_result: dict) -> dict:
    """从载荷工况 + FEA 结果直接做疲劳校核（供 fea_solver 统一调用）。

    读取：
      load_case.acceptance.design_life_years      设计寿命（默认 30）
      load_case.acceptance.operating_cycles_per_year  年动作次数
      load_case.acceptance.load_type              工况性质
      load_case.acceptance.impact_factor          动载系数
    """
    mat = load_case.get("material", {}) or {}
    acc = load_case.get("acceptance", {}) or {}
    stress = (fea_result.get("max_von_mises_mpa")
              or fea_result.get("max_stress_mpa") or 0.0)
    if not stress:
        return {"ok": False, "error": "缺少静力应力结果，无法做疲劳校核",
                "verdict": "NOT_EVALUATED"}
    return analyze_fatigue(
        max_von_mises_mpa=stress,
        mat=mat,
        design_life_years=acc.get("design_life_years", 30.0),
        cycles_per_year=acc.get("operating_cycles_per_year", 200000.0),
        load_type=acc.get("load_type", "cyclic"),
        impact_factor=acc.get("impact_factor", 1.0),
        surface=acc.get("surface_finish", "machined"),
        diameter_mm=acc.get("characteristic_diameter_mm"),
        reliability=acc.get("reliability", 0.99),
        temperature_c=acc.get("service_temperature_c", 25.0),
        criterion=acc.get("fatigue_criterion", "goodman"),
    )
