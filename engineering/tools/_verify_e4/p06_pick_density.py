# -*- coding: utf-8 -*-
"""E4：直接单元级复验 _pick_attested_density（BUG-A 的核心函数）。只读调用。"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
import swapi  # noqa: E402

CASES = [
    ("赋材成功（density_kg_m3=7850）",
     {"ok": True, "density_kg_m3": 7850.0}, "65Mn", 7850.0),
    ("赋材失败（仅有 density_before=1000，无 density_kg_m3）",
     {"ok": False, "density_before_kg_m3": 1000.0,
      "expected_density_kg_m3": 7850.0}, "65Mn", 1000.0),
    ("赋材失败（density_kg_m3 显式为 1000）",
     {"ok": False, "density_kg_m3": 1000.0}, "65Mn", 1000.0),
    ("赋材失败（density_after_kg_m3=1000）",
     {"ok": False, "density_after_kg_m3": 1000.0}, "65Mn", 1000.0),
    ("赋材失败（无任何密度字段）",
     {"ok": False}, "65Mn", None),
    ("赋材失败（density_before=1000 + density_after=1000）",
     {"ok": False, "density_before_kg_m3": 1000.0,
      "density_after_kg_m3": 1000.0}, "65Mn", 1000.0),
]

print("=" * 84)
print("_pick_attested_density 行为（期望：如实返回实测值，1000 就返回 1000）")
print("=" * 84)
rows = []
for label, mr, name, expect in CASES:
    d, exp, suspect = swapi._pick_attested_density(mr, name)
    _ok = (d == expect) if expect is not None else (d is None)
    print("\n-- %s" % label)
    print("   input material_result = %s" % json.dumps(mr, ensure_ascii=False))
    print("   -> density_kg_m3=%s  expected_density_kg_m3=%s  suspect=%s"
          % (d, exp, suspect))
    print("   期望实测密度=%s  => %s" % (expect, "符合" if _ok else "★不符合（应为实测值）"))
    rows.append({"label": label, "in": mr, "density": d, "expected": exp,
                 "suspect": suspect, "expect_density": expect, "ok": _ok})

# 真实失败样本（来自本次实机 material_result）
print("\n" + "=" * 84)
print("实机失败样本（从 _verify_e4/bugA_one_*.json 读取真实 material_result）")
print("=" * 84)
_d = os.path.join(TOOLS, "_verify_e4")
for fn in sorted(os.listdir(_d)):
    if fn.startswith("bugA_one_") and fn.endswith(".json"):
        j = json.load(open(os.path.join(_d, fn), "r", encoding="utf-8"))
        mr = j.get("material_result") or {}
        g = j.get("grade")
        real = (j.get("massprops") or {}).get("density_kg_m3")
        dd, ee, ss = swapi._pick_attested_density(mr, g)
        print("\n-- %s" % g)
        print("   零件真实密度(massprops) = %s" % real)
        print("   material_result 里的密度字段 = %s"
              % json.dumps({k: v for k, v in mr.items() if "dens" in k.lower()},
                               ensure_ascii=False))
        print("   _pick_attested_density -> density=%s expected=%s suspect=%s"
              % (dd, ee, ss))
        print("   => 凭据将写 density_kg_m3=%s ；零件真实是 %s → %s"
              % (dd, real, "一致" if dd == real else "★不一致（凭据未如实记录实测值）"))

with open(os.path.join(_d, "out_pick_density.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_pick_density.json")
