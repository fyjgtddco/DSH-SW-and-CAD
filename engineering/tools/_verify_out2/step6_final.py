# -*- coding: utf-8 -*-
"""Step6: 密度合理性 + 疲劳口径分裂 + 最终缺口清单"""
import sys, io, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

print("=" * 100)
print("Step6.1  密度合理性（按材料族判断）")
print("=" * 100)
# 合理区间 kg/m3
RANGE = {
    "钢":      (7600, 8100),
    "不锈钢":  (7800, 8200),
    "铸铁":    (6900, 7600),
    "铝合金":  (2600, 2900),
    "铜合金":  (8200, 9000),
}
CAT = {
    "GB_Q235": "钢", "GB_45#": "钢", "GB_40Cr": "钢", "GB_Q355": "钢",
    "GB_20CrMnTi": "钢", "GB_GCr15": "钢", "GB_65Mn": "钢",
    "GB_Cr12MoV": "钢", "GB_HT200": "铸铁", "GB_QT500_7": "铸铁",
    "GB_6061_T6": "铝合金", "GB_7075_T6": "铝合金", "GB_2A12": "铝合金",
    "GB_ZL104": "铝合金", "GB_304": "不锈钢", "GB_316": "不锈钢",
    "GB_ZCuSn10P1": "铜合金", "GB_H62": "铜合金",
}
print("%-16s %-10s %-12s %-16s %s" % ("牌号", "类别", "密度", "合理区间", "判定"))
print("-" * 100)
issues = []
for mid, cat in CAT.items():
    m = mdb.get_material(mid)
    rho = m.get("density_kg_m3") if m else None
    lo, hi = RANGE[cat]
    if rho is None:
        verdict = "缺失（无法算质量/重力）"
        issues.append((mid, "密度缺失"))
    elif lo <= rho <= hi:
        verdict = "OK"
    else:
        verdict = "★ 超出合理区间"
        issues.append((mid, "密度 %s 超出 %s 区间 [%s,%s]" % (rho, cat, lo, hi)))
    print("%-16s %-10s %-12s %-16s %s"
          % (mid, cat, rho, "[%s,%s]" % (lo, hi), verdict))
print()
print("密度问题：", len(issues))
for i in issues:
    print("   -", i[0], ":", i[1])

print()
print("=" * 100)
print("Step6.2  疲劳口径：独立 fatigue CLI vs optimize 报告内 —— 同一工况差多少？")
print("=" * 100)
print("  独立 fatigue CLI (case_Q235_fresh.json，无 real_geometry):")
print("     peak_stress = 1.93 MPa   fatigue_sf = 94.164   verdict = PASS")
print("  optimize 报告内 (真实零件 100x60x10):")
print("     peak_stress = 182.49 MPa fatigue_sf = 0.996    verdict = FAIL")
print("  >>> 同一 Q235、同一工况文件，结论【完全相反】")
print("      根因：独立 fatigue 未注入 real_geometry → 退回 design_domain 默认")
print("      几何（300x200x120），承载截面被严重高估 → 应力被低估 ~95 倍。")

print()
print("=" * 100)
print("Step6.3  最终缺口清单（按工程可用性）")
print("=" * 100)
FIELDS = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
CRIT = ("E_mpa", "yield_mpa", "density_kg_m3")   # FEA/强度校核必需
rows = []
for mid in mdb.GB_MATERIALS:
    m = mdb.get_material(mid)
    miss = [f for f in FIELDS if m.get(f) is None]
    miss_crit = [f for f in CRIT if m.get(f) is None]
    prov = m.get("field_provenance", {})
    gb_fields = [f for f in FIELDS if prov.get(f, {}).get("source") == "GB_STANDARD"]
    eq_fields = [f for f in FIELDS if prov.get(f, {}).get("source") == "EQUIVALENT_FALLBACK"]
    rows.append((mid, miss, miss_crit, gb_fields, eq_fields))

print("【A 级 · 完全不可用】关键字段缺失（E/σy/ρ 有缺）")
print("-" * 100)
for mid, miss, mc, gb, eq in rows:
    if mc:
        print("  %-16s 缺关键: %-40s 全部缺失: %s" % (mid, ", ".join(mc), ", ".join(miss) or "无"))
print()
print("【B 级 · 可用但数值非国标】有兜底、关键字段齐，但 σy/σb 来自欧美标")
print("-" * 100)
for mid, miss, mc, gb, eq in rows:
    if not mc and not gb:
        print("  %-16s 兜底字段: %s" % (mid, ", ".join(eq)))
print()
print("【C 级 · 国标数值已生效】σy/σb 来自 GB 原文")
print("-" * 100)
for mid, miss, mc, gb, eq in rows:
    if gb:
        print("  %-16s GB字段: %-32s 兜底: %s 仍缺: %s"
              % (mid, ", ".join(gb), ", ".join(eq) or "无", ", ".join(miss) or "无"))

print()
print("=" * 100)
print("Step6.4  统计总览")
print("=" * 100)
n_all = len(rows)
n_a = len([r for r in rows if r[2]])
n_c = len([r for r in rows if r[3]])
print("  牌号总数              : %d" % n_all)
print("  A 级（关键字段缺失）  : %d  → 无法做 FEA/强度校核" % n_a)
print("  C 级（含 GB 原文 σy/σb）: %d" % n_c)
print("  E 缺失                : %d / %d"
      % (len([r for r in rows if "E_mpa" in r[1]]), n_all))
print("  nu 缺失               : %d / %d"
      % (len([r for r in rows if "nu" in r[1]]), n_all))
print("  density 缺失          : %d / %d"
      % (len([r for r in rows if "density_kg_m3" in r[1]]), n_all))
