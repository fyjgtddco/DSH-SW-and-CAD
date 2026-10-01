# -*- coding: utf-8 -*-
"""
domain_validator.py — 领域特定验证代理
======================================
DSH 工程模式的"领域特定验证代理"实现。

【设计理念】
DSVA 不是"插件"，而是约束生成器的"宪法"。
在逻辑上，必须先定义"什么是好（DSVA）"，才能让AI去"生成"。
把它放在这里，意味着所有下游工作（仿真、优化）都要向它看齐。

【架构】
1. rules/<domain>_rules.json  — 领域规则文件（声明式）
2. domain_validator.py         — 规则加载与执行引擎（本文件）
3. refine_rules.py             — 修正建议生成（调用本文件的验证）

【支持领域】
- structural: 结构件（梁/板/壳）
- transmission: 传动件（齿轮/轴/轴承）
- housing: 壳体（箱体/盖板）
- mold: 模具（型腔/型芯）
"""
import json
import os
import glob as _glob
from typing import Any, Optional

# 规则目录位置（项目内：engineering/tools/physics/rules/）
_RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")


def list_domains() -> list[dict[str, str]]:
    """列出所有可用的领域。

    Returns:
        [{"id": "structural", "name": "结构件", "path": "..."}, ...]
    """
    if not os.path.isdir(_RULES_DIR):
        return []
    domains = []
    for json_path in _glob.glob(os.path.join(_RULES_DIR, "*_rules.json")):
        # 排除标准元数据文件，只保留领域专用 json（structural/transmission/housing/mold）
        _base = os.path.basename(json_path)
        if _base.startswith("gb_") or _base == "manifest.json":
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            domains.append({
                "id": data.get("domain_id", os.path.basename(json_path).replace("_rules.json", "")),
                "name": data.get("domain_name", "?"),
                "version": data.get("version", "?"),
                "rule_count": len(data.get("rules", [])),
                "path": json_path,
            })
        except Exception:
            pass
    return domains


def load_domain_rules(domain_id: str) -> dict:
    """加载指定领域的规则。

    Args:
        domain_id: 领域 ID（如 "structural"）

    Returns:
        {"ok": True, "domain": {...}, "rules": [...]} 或 {"ok": False, "error": ...}
    """
    json_path = os.path.join(_RULES_DIR, f"{domain_id}_rules.json")
    if not os.path.isfile(json_path):
        # 尝试模糊匹配
        for p in _glob.glob(os.path.join(_RULES_DIR, "*_rules.json")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("domain_id") == domain_id:
                    json_path = p
                    break
            except Exception:
                continue
        else:
            return {
                "ok": False,
                "error": f"domain not found: {domain_id!r}",
                "available": [d["id"] for d in list_domains()],
            }

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "ok": True,
            "domain": data.get("domain_id", "?"),
            "name": data.get("domain_name", "?"),
            "version": data.get("version", "1.0"),
            "rules": data.get("rules", []),
            "path": json_path,
        }
    except Exception as e:
        return {"ok": False, "error": f"failed to load {json_path}: {e}"}


def validate_with_domain(
    design_params: dict,
    fea_result: dict,
    domain_id: str = "structural",
) -> dict:
    """用领域特定规则验证设计参数和仿真结果。

    Args:
        design_params: 设计参数字典（如 {"thickness_mm": 5, "fillet_mm": 2}）
        fea_result: FEA 求解结果（来自 fea_solver.solve_*）
        domain_id: 领域 ID

    Returns:
        {
            "ok": True,
            "domain": "structural",
            "passed": [...],  # 通过的规则
            "warnings": [...],  # 警告的规则
            "violations": [...],  # 违反的规则（CRITICAL）
            "score": 0.0~1.0,  # 合规分数
        }
    """
    loaded = load_domain_rules(domain_id)
    if not loaded.get("ok"):
        return loaded

    rules = loaded.get("rules", [])
    passed, warnings, violations = [], [], []

    for rule in rules:
        rid = rule.get("id", "?")
        rtype = rule.get("type", "")
        target = rule.get("target", "")
        condition = rule.get("condition", {})
        severity = rule.get("severity", "WARNING")
        message = rule.get("message", "")

        # 获取设计参数值
        if target in design_params:
            value = design_params[target]
        elif target == "max_von_mises_mpa":
            value = fea_result.get("max_von_mises_mpa", 0)
        elif target == "max_displacement_mm":
            value = fea_result.get("max_displacement_mm", 0)
        elif target == "safety_factor":
            value = fea_result.get("safety_factor", 0)
        elif target == "mass_kg":
            value = fea_result.get("mass_kg", 0)
        else:
            warnings.append({
                "rule_id": rid,
                "target": target,
                "severity": "INFO",
                "message": f"未识别的验证目标: {target}",
            })
            continue

        # 应用条件检查
        violation = _check_condition(value, condition)
        if violation is None:
            passed.append({"rule_id": rid, "target": target, "value": value})
        else:
            entry = {
                "rule_id": rid,
                "target": target,
                "value": value,
                "severity": severity,
                "message": message or f"违反规则 {rid}: {violation}",
                "expected": condition,
            }
            if severity == "CRITICAL":
                violations.append(entry)
            else:
                warnings.append(entry)

    total = len(passed) + len(warnings) + len(violations)
    score = (len(passed) / total) if total > 0 else 1.0

    return {
        "ok": len(violations) == 0,
        "domain": domain_id,
        "domain_name": loaded.get("name", "?"),
        "version": loaded.get("version", "1.0"),
        "passed": passed,
        "warnings": warnings,
        "violations": violations,
        "score": round(score, 3),
        "summary": {
            "total_rules": total,
            "passed": len(passed),
            "warnings": len(warnings),
            "violations": len(violations),
        },
    }



# ══ 【C16 修复】单位一致性校验 ═════════════════════════════════════════
# 测试反馈（真实数学 bug）：壳体机架房间抗倾覆校核【单位错误导致虚高 1000 倍】，
#   单件 K=33.6 虚高，整机总装核算 K=0.24 FAIL。
# 根因：N 与 kN、mm 与 m 混用（1000 因子），且没有任何机制捕获它。
# 修复：提供通用单位校验器，对力学量做【量级合理性】检查；
#   同时给出抗倾覆安全系数 K 的标准算法（统一 N / mm），杜绝口径混乱。

# 单位换算到基准单位（力=N，长度=mm，力矩=N·mm，质量=kg）
_UNIT_SCALE = {
    # 力 → N
    "n": 1.0, "N": 1.0, "kn": 1000.0, "kN": 1000.0,
    "kgf": 9.80665, "tf": 9806.65, "lbf": 4.4482216152605,
    # 长度 → mm
    "mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4,
    # 力矩 → N·mm（覆盖工程里各种写法，避免因写法不同而"误报未知单位"）
    "nmm": 1.0, "N·mm": 1.0, "Nmm": 1.0, "N.mm": 1.0, "n-mm": 1.0,
    "n.m": 1000.0, "Nm": 1000.0, "N·m": 1000.0, "N*m": 1000.0, "n-m": 1000.0,
    "knm": 1000000.0, "kN·m": 1000000.0, "kN.m": 1000000.0, "kNm": 1000000.0,
    # 应力/压强 → MPa
    "pa": 1e-6, "kpa": 1e-3, "mpa": 1.0, "gpa": 1000.0,
    "n/mm2": 1.0, "N/mm2": 1.0, "mpa_nmm2": 1.0,
}


def _norm_unit(u: str) -> str:
    """单位归一化：统一大小写与常见分隔符写法。

    【C16】工程里同一个单位有大量写法（N.m / N·m / Nm / n-m、MPa / N/mm2 …），
      若严格区分大小写，会把"N.m"和"n.m"当成两个不同单位，
      既可能误报"未知单位"，也可能掩盖真正的单位不一致 —— 必须归一化。
    """
    s = str(u).strip()
    s = s.replace("·", ".").replace("*", ".").replace("×", ".")
    s = s.replace(" ", "").replace("_", "")
    return s.lower()


# 归一化后的单位表（启动时构建一次）
_UNIT_NORM = {}
for _k, _v in _UNIT_SCALE.items():
    _UNIT_NORM.setdefault(_norm_unit(_k), _v)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """通用单位换算（大小写不敏感）。任一单位未知时抛 ValueError。

    【C16】静默返回原值是单位 bug 的温床 —— 必须显式报错，
      但报错前应先做归一化，避免因写法差异产生假报错。
    """
    fu, tu = _norm_unit(from_unit), _norm_unit(to_unit)
    if fu not in _UNIT_NORM:
        raise ValueError("未知源单位: %r（可用示例: %s）"
                         % (from_unit, ", ".join(sorted(set(_UNIT_SCALE))[:20])))
    if tu not in _UNIT_NORM:
        raise ValueError("未知目标单位: %r" % (to_unit,))
    return float(value) * _UNIT_NORM[fu] / _UNIT_NORM[tu]


def check_unit_consistency(quantities: dict, domain: str = "structural") -> dict:
    """【C16 修复】单位一致性 + 量级合理性校验。

    用途：任何力学计算结果上报前先过一遍本函数，能拦下绝大多数
      N/kN、mm/m 混用造成的 1000 倍错误（正是抗倾覆校核翻车的根因）。

    Args:
        quantities: {名称: {"value": 数值, "unit": 单位, "expected_unit": 期望单位}}
                    也接受简写 {"名称": (数值, 单位)}
        domain: structural / transmission / thermal ... 用于选择量级参考

    Returns:
        {ok, issues:[], checked:int, hint}
    """
    issues = []
    checked = 0
    # 各物理量的"合理量级"参考区间（在基准单位下）
    _SANE = {
        "force_n":        (1e-3, 1e7),      # 1 mN ~ 10 MN
        "length_mm":      (1e-3, 1e5),      # 1 µm ~ 100 m
        "moment_nmm":     (1e-3, 1e10),
        "stress_mpa":     (1e-3, 1e4),      # 1 kPa ~ 10 GPa
        "mass_kg":        (1e-6, 1e6),
        "safety_factor":  (0.01, 1e4),
        "stiffness_n_mm": (1e-4, 1e10),
    }
    for name, spec in (quantities or {}).items():
        checked += 1
        try:
            if isinstance(spec, (tuple, list)) and len(spec) >= 2:
                val, unit = float(spec[0]), str(spec[1])
                exp_unit = None
            elif isinstance(spec, dict):
                val = float(spec.get("value", 0))
                unit = str(spec.get("unit", ""))
                exp_unit = spec.get("expected_unit")
            else:
                val = float(spec)
                unit, exp_unit = "", None
        except Exception as e:
            issues.append({"name": name, "level": "error",
                           "message": "无法解析数值: %r" % (e,)})
            continue
        # 1) 单位是否已知（归一化后判断）
        if unit and _norm_unit(unit) not in _UNIT_NORM:
            issues.append({"name": name, "level": "warn",
                           "message": "单位 %r 不在已知表内，无法校验一致性" % unit})
        # 2) 与期望单位比对（给出换算建议）
        if unit and exp_unit and _norm_unit(unit) != _norm_unit(exp_unit):
            try:
                conv = convert(val, unit, exp_unit)
                issues.append({"name": name, "level": "error",
                               "message": ("单位不一致：得到 %s %s，期望 %s %s。"
                                           "按换算应为 %s %s（差了 %.6g 倍）")
                                           % (val, unit, exp_unit, exp_unit,
                                              round(conv, 6), exp_unit,
                                              (conv / val) if val else 0)})
            except Exception:
                pass
        # 3) 量级合理性（抓 1000 倍这类错误）
        _key = {"force": "force_n", "length": "length_mm",
                "moment": "moment_nmm", "stress": "stress_mpa",
                "mass": "mass_kg", "safety_factor": "safety_factor"}.get(name.replace("_n", "").replace("_mm", ""), None)
        _rng = _SANE.get(_key)
        if _rng and (val < _rng[0] or val > _rng[1]):
            issues.append({"name": name, "level": "warn",
                           "message": ("量级异常：%s %s 超出常见范围 [%g, %g]"
                                       " —— 请检查是否存在 N/kN 或 mm/m 混用")
                                       % (val, unit or "?", _rng[0], _rng[1])})
    _errs = [i for i in issues if i.get("level") == "error"]
    return {"ok": len(_errs) == 0, "checked": checked, "issues": issues,
            "errors": len(_errs),
            "hint": ("发现单位不一致，必须先统一到 N / mm / MPa / N·mm 再计算。"
                     "抗倾覆安全系数 K 的百万倍/千倍错误即源于此类混用。")
                     if _errs else "单位一致性检查通过。"}


def overturning_safety_factor(resisting_moment_nmm: float,
                              overturning_moment_nmm: float) -> dict:
    """抗倾覆安全系数 K（统一 N·mm 口径，杜绝 1000 倍错误）。

    【C16 修复】标准定义：K = M_抗倾覆 / M_倾覆，二者必须是【同一力矩单位】。
      常见错误：一侧用 N·m、另一侧用 N·mm → K 直接差 1000 倍
      （测试中单件 K=33.6 虚高、整机 K=0.24 FAIL 即为此类）。
      本函数强制要求传入 N·mm，并在返回值里附带两者数值供复核。

    判据（工程惯例，可依据具体标准调整）：
      K >= 2.0  通过（一般机械）
      1.5 <= K < 2.0  偏低，需复核
      K < 1.5   不通过，必须加大配重/加宽支撑/降低重心

    Args: 抗倾覆力矩与倾覆力矩，单位均为 N·mm
    Returns: {ok, K, resisting_nmm, overturning_nmm, judgement, hint}
    """
    try:
        mr = float(resisting_moment_nmm)
        mo = float(overturning_moment_nmm)
    except Exception as e:
        return {"ok": False, "error": "力矩必须为数值: %r" % (e,)}
    if mo == 0:
        return {"ok": False, "error": "倾覆力矩为 0，无法计算 K（请检查载荷输入）"}
    K = mr / mo
    if K >= 2.0:
        judgement, ok = "通过（K>=2.0）", True
    elif K >= 1.5:
        judgement, ok = "偏低，建议复核（1.5<=K<2.0）", False
    else:
        judgement, ok = "不通过（K<1.5），需加大配重/加宽支撑/降低重心", False
    return {"ok": ok, "K": round(K, 4),
            "resisting_nmm": mr, "overturning_nmm": mo,
            "K_over_1": round(1.0 / K, 4),
            "judgement": judgement,
            "hint": ("⚠️ 若 K 与手算相差整数倍（1000/1e6），"
                     "几乎一定是两侧力矩单位不一致：统一为 N·mm 后重算。")}

def _check_condition(value: Any, condition: dict) -> Optional[str]:
    """检查值是否满足条件。

    支持的操作符: min, max, eq, ne, in, not_in, between
    """
    if value is None:
        return "value is None"

    for op, expected in condition.items():
        try:
            if op == "min" and not (value >= expected):
                return f"value {value} < min {expected}"
            if op == "max" and not (value <= expected):
                return f"value {value} > max {expected}"
            if op == "eq" and value != expected:
                return f"value {value} != {expected}"
            if op == "ne" and value == expected:
                return f"value {value} == {expected}"
            if op == "in" and value not in expected:
                return f"value {value} not in {expected}"
            if op == "not_in" and value in expected:
                return f"value {value} in {expected}"
            if op == "between":
                lo, hi = expected[0], expected[1]
                if not (lo <= value <= hi):
                    return f"value {value} not in [{lo}, {hi}]"
        except Exception as e:
            return f"condition check error: {e}"

    return None


# 兼容旧 API: refine_rules.py 可能使用 generate_recommendations
def get_validator_for_domain(domain_id: str = "structural"):
    """返回指定领域的验证器函数。供 refine_rules.py 调用。"""
    def validator(design_params: dict, fea_result: dict) -> dict:
        return validate_with_domain(design_params, fea_result, domain_id)
    return validator


if __name__ == "__main__":
    # 简单自测
    domains = list_domains()
    print(f"已加载 {len(domains)} 个领域:")
    for d in domains:
        print(f"  - {d['id']}: {d['name']} (规则数: {d['rule_count']})")

    if domains:
        first = domains[0]["id"]
        result = validate_with_domain(
            design_params={"thickness_mm": 5, "fillet_mm": 2},
            fea_result={"max_von_mises_mpa": 50, "max_displacement_mm": 0.5, "safety_factor": 3.0, "mass_kg": 1.5},
            domain_id=first,
        )
        print(f"\n{first} 验证结果:")
        print(f"  合规分: {result.get('score', 0):.2%}")
        print(f"  通过: {result.get('summary', {}).get('passed', 0)}")
        print(f"  警告: {result.get('summary', {}).get('warnings', 0)}")
        print(f"  违反: {result.get('summary', {}).get('violations', 0)}")
