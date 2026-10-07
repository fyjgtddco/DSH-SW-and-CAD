# -*- coding: utf-8 -*-
"""阶段2 前置探针：material_db 对国标牌号的解析结果（只读，不改任何数据）。"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from physics import material_db as mdb

print("=" * 78)
print("gb_materials_status()")
st = mdb.gb_materials_status()
for k in ("loaded", "count", "identity_count", "ready_count",
          "equivalent_ready_count", "pending_count", "file"):
    print("  %-24s = %s" % (k, st.get(k)))
print("  native_ready_ids        =", st.get("native_ready_ids"))
print("  equivalent_ready_ids    =", st.get("equivalent_ready_ids"))
print("  error                   =", st.get("error"))

print("=" * 78)
print("逐牌号逐字段：值 / 来源")
FIELDS = ("yield_mpa", "uts_mpa", "E_mpa", "nu", "density_kg_m3")
for nm in ("Q235", "45#", "65Mn", "Q355", "40Cr", "GCr15", "ZL104", "HT200",
           "QT500-7", "ZCuSn10P1", "H62", "6061-T6", "304"):
    m = mdb.get_material(nm)
    print("-" * 78)
    if not m:
        print("[%s] 未收录 (get_material -> None)" % nm)
        continue
    print("[%s] id=%s name=%s" % (nm, m.get("id"), m.get("name")))
    print("    yield=%-8s uts=%-8s E=%-9s nu=%-6s rho=%s"
          % (m.get("yield_mpa"), m.get("uts_mpa"), m.get("E_mpa"),
             m.get("nu"), m.get("density_kg_m3")))
    print("    values_source  = %s" % (m.get("values_source"),))
    print("    values_from_gb = %s | values_pending = %s | equivalent = %s"
          % (m.get("values_from_gb"), m.get("values_pending"),
             m.get("equivalent_name")))
    print("    consistency_guard = %s" % (m.get("consistency_guard"),))
    fp = m.get("field_provenance") or {}
    for f in FIELDS:
        e = fp.get(f) or {}
        print("      %-16s value=%-10s source=%s%s"
              % (f, e.get("value"), e.get("source"),
                 (" (equiv=%s)" % e.get("equivalent")) if e.get("equivalent") else ""))
    rdy, _e, why = mdb.gb_material_ready(nm)
    print("    gb_material_ready -> ok=%s reason=%s" % (rdy, why))
