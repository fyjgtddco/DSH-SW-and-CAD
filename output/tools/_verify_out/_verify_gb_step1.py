# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

print("=== [1.1] gb_materials_status() ===")
st = mdb.gb_materials_status()
for k in ("loaded", "count", "identity_count", "ready_count", "pending_count",
          "file", "source", "error"):
    print("  %-16s = %r" % (k, st.get(k)))
print("  searched:")
for p in st.get("searched") or []:
    print("     -", p)

print()
print("=== [1.2] 全量牌号清单（id / 数值是否齐全）===")
for i, m in enumerate(mdb.list_materials("gb"), 1):
    has = mdb.has_props(m)
    print("  %2d. %-12s %-28s has_props=%-5s equivalent_id=%r"
          % (i, m.get("id"), (m.get("name_cn") or m.get("name") or "")[:28],
             has, m.get("equivalent_id")))

print()
print("=== [1.3] 抽查 get_material ===")
for nm in ("Q235", "40Cr", "GCr15"):
    m = mdb.get_material(nm)
    print("--- get_material(%r) ---" % nm)
    if not m:
        print("    None  <== 未收录")
        continue
    for k in ("id", "name", "name_cn", "standard", "standard_year", "standard_url",
              "E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3",
              "equivalent_id", "equivalent_name", "equivalent_filled",
              "values_source", "values_from_gb", "values_pending", "values_status"):
        if k in m:
            print("    %-18s = %r" % (k, m.get(k)))
    print("    [has_props]        =", mdb.has_props(m))
    ok, entry, reason = mdb.gb_material_ready(nm)
    print("    [gb_material_ready]= ok=%s reason=%r" % (ok, reason))
