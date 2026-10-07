# -*- coding: utf-8 -*-
"""Step2: 验证『国标数值优先于兜底』（只读）"""
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

CASES = [
    # (查询写法, 期望 σy, 兜底材料的 σy, 说明)
    ("45#",        355.0, 655, "GB/T 699-2015 =355  vs  AISI4140 兜底 =655"),
    ("Q355",       355.0, 235, "GB/T 1591-2018 =355  vs  S235JR 兜底 =235"),
    ("65Mn",       785.0, 655, "GB/T 1222-2025 =785  vs  AISI4140 兜底 =655"),
    ("Q235",       235.0, 235, "GB/T 700-2006 =235  vs  S235JR 兜底 =235（同值，不可区分）"),
]

print("=" * 100)
print("Step2  国标数值 vs 兜底数值 —— 实际生效的是哪个？")
print("=" * 100)
for q, want_sy, fb_sy, note in CASES:
    m = mdb.get_material(q)
    print("-" * 100)
    print("查询: %-10s   %s" % (q, note))
    if not m:
        print("   [!!] get_material 返回 None —— 查不到")
        continue
    print("  命中 id           = %s" % m.get("id"))
    print("  实际 σy           = %s MPa" % m.get("yield_mpa"))
    print("  实际 σb           = %s MPa" % m.get("uts_mpa"))
    print("  实际 E            = %s MPa" % m.get("E_mpa"))
    print("  实际 ν            = %s" % m.get("nu"))
    print("  实际 ρ            = %s kg/m³" % m.get("density_kg_m3"))
    print("  equivalent_name   = %s" % m.get("equivalent_name"))
    print("  equivalent_filled = %s" % m.get("equivalent_filled"))
    print("  gb_sourced_fields = %s" % m.get("gb_sourced_fields"))
    print("  fallback_fields   = %s" % m.get("fallback_fields"))
    print("  values_from_gb    = %s" % m.get("values_from_gb"))
    print("  values_pending    = %s" % m.get("values_pending"))
    print("  values_source     = %s" % m.get("values_source"))
    got = m.get("yield_mpa")
    if got == want_sy:
        verdict = "PASS  用的是 GB 值 %s" % want_sy
    elif got == fb_sy:
        verdict = "FAIL!! 用的是【兜底】值 %s（GB 值 %s 被覆盖/忽略）" % (fb_sy, want_sy)
    else:
        verdict = "?? 既非 GB(%s) 也非兜底(%s)，实得 %s" % (want_sy, fb_sy, got)
    print("  >>> %s" % verdict)

print()
print("=" * 100)
print("Step2b  别名/写法鲁棒性（图纸上可能出现的写法）")
print("=" * 100)
for q in ["45#", "45 #", "Q 235", "q235", "GB_Q235", "Q345", "Q345B", "Q355B",
          "HT200", "灰铸铁", "304", "06Cr19Ni10", "S30408", "0Cr18Ni9",
          "AISI 1045", "1045", "LY12", "2A12-T4", "GCr15", "H62", "ZCuSn10P1",
          "ZL104", "7075", "6061-T6", "不锈钢", "碳钢", "铝合金"]:
    m = mdb.get_material(q)
    if m:
        print("  %-14s -> id=%-18s σy=%-8s σb=%-8s src=%s"
              % (q, m.get("id"), m.get("yield_mpa"), m.get("uts_mpa"),
                 (m.get("values_source") or "")[:44]))
    else:
        print("  %-14s -> [未收录]" % q)

print()
print("=" * 100)
print("Step2c  关键回归：GB 值是否会被兜底【反向覆盖】？")
print("=" * 100)
# 直接检查 _apply_equivalent 的行为：GB 有值时必须保持
for mid, gb_sy in [("GB_45#", 355.0), ("GB_Q355", 355.0), ("GB_65Mn", 785.0)]:
    m1 = mdb.get_material(mid)
    m2 = mdb.get_material(mid.lower())
    raw = mdb.GB_MATERIALS[mid]
    print("  %-10s get_material(精确id).σy=%-8s  get_material(小写).σy=%-8s  "
          "GB_MATERIALS[raw].σy=%-8s  期望=%s"
          % (mid, m1.get("yield_mpa") if m1 else None,
             m2.get("yield_mpa") if m2 else None, raw.get("yield_mpa"), gb_sy))
    # 验证原始 GB_MATERIALS 未被就地修改
    print("      原始 raw['E_mpa']=%s (应为 None，未被就地写入)"
          % raw.get("E_mpa"))
