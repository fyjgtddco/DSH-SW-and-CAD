# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import workflow_gate as wg

print("=== [2.1] wg._material_for(...) ===")
for s in ("Q235 碳钢", "40Cr 齿轮", "6061 铝", "GCr15 轴承"):
    print("--- _material_for(%r, '') ---" % s)
    try:
        r = wg._material_for(s, "")
    except Exception as e:
        print("    !! EXCEPTION:", repr(e))
        continue
    if not isinstance(r, dict):
        print("    ->", repr(r)); continue
    for k in ("id", "name", "youngs_modulus_mpa", "poissons_ratio",
              "yield_strength_mpa", "uts_mpa", "density_kg_m3",
              "standard", "source", "identity_source", "values_source",
              "values_from_gb", "values_pending", "equivalent_name"):
        if k in r:
            print("    %-20s = %r" % (k, r.get(k)))

print()
print("=== [2.2] wg._gb_data_status() ===")
st = wg._gb_data_status()
print(json.dumps({k: v for k, v in st.items() if k != "searched"},
                 ensure_ascii=False, indent=2))
