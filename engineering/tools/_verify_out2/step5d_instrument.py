# -*- coding: utf-8 -*-
"""Step5d: 插桩定位 ZL104 兜底行为差异"""
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

print("=" * 100)
print("Step5d  插桩：直接手工走一遍 _apply_equivalent 的逻辑")
print("=" * 100)
raw = mdb.GB_MATERIALS["GB_ZL104"]
print("  raw GB_MATERIALS['GB_ZL104']:")
for f in ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"):
    print("     %-15s = %r" % (f, raw.get(f)))
print("  raw equivalent_id = %r" % raw.get("equivalent_id"))
eid = raw.get("equivalent_id")
src = mdb.ALL_MATERIALS.get(str(eid).upper())
print("  ALL_MATERIALS.get(%r) -> %s" % (str(eid).upper(),
      "dict" if isinstance(src, dict) else repr(src)))
if isinstance(src, dict):
    print("     src['name']      = %r" % src.get("name"))
    print("     src['yield_mpa'] = %r" % src.get("yield_mpa"))
    print("     src['E_mpa']     = %r" % src.get("E_mpa"))
    print("     src id()         = %s" % id(src))

print()
print("  --- 现在手工调 _apply_equivalent(dict(raw)) ---")
out = mdb._apply_equivalent(dict(raw))
for f in ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"):
    print("     %-15s = %r" % (f, out.get(f)))
print("     equivalent_filled = %r" % out.get("equivalent_filled"))
print("     values_source     = %r" % out.get("values_source"))

print()
print("  --- 对比 get_material('GB_ZL104') ---")
g = mdb.get_material("GB_ZL104")
for f in ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"):
    print("     %-15s = %r" % (f, g.get(f)))
print("     equivalent_filled = %r" % g.get("equivalent_filled"))

print()
print("=" * 100)
print("Step5d-2  关键分支检查：get_material 是否走了 _apply_equivalent？")
print("=" * 100)
key = "GB_ZL104"
hit = mdb.ALL_MATERIALS.get(key)
print("  ALL_MATERIALS.get('GB_ZL104') 存在? ", hit is not None)
print("  'GB_ZL104' in GB_MATERIALS ?     ", key in mdb.GB_MATERIALS)
print("  hit.get('equivalent_id') = %r" % hit.get("equivalent_id"))
print("  → 应走 _apply_equivalent(dict(_hit))")

print()
print("=" * 100)
print("Step5d-3  _apply_equivalent 的 _missing 到底含哪些字段")
print("=" * 100)
_FIELDS = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
_missing = [f for f in _FIELDS if raw.get(f) is None]
print("  _missing =", _missing)
print("  _src 有的字段 =", [f for f in _FIELDS if isinstance(src, dict) and src.get(f) is not None])
