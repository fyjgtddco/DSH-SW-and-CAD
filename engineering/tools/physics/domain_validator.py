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
