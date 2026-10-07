# -*- coding: utf-8 -*-
"""Step5c: 精确复现 ZL104 σy 填充的不确定性"""
import sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

def show(tag):
    src = mdb.ALL_MATERIALS.get("AL_6061_T6")
    print("  [%s] ALL_MATERIALS['AL_6061_T6']['yield_mpa'] = %s  (id()=%s)"
          % (tag, src.get("yield_mpa") if src else "MISSING", id(src) if src else "-"))

print("=" * 100)
print("Step5c  顺序敏感性精确复现")
print("=" * 100)
show("启动后")

print("\n-- 场景A：先查 'GB_ZL104'（Step1 的调用方式） --")
a = mdb.get_material("GB_ZL104")
print("   σy=%s fallback_filled=%s" % (a.get("yield_mpa"), a.get("equivalent_filled")))
show("查完 GB_ZL104")

print("\n-- 场景B：先查 'ZL104' 别名 --")
b = mdb.get_material("ZL104")
print("   σy=%s fallback_filled=%s" % (b.get("yield_mpa"), b.get("equivalent_filled")))
show("查完 ZL104")

print()
print("=" * 100)
print("Step5c-2  关键：GB_6061_T6 与 AL_6061_T6 是否共享同一个 dict 对象？")
print("=" * 100)
g = mdb.ALL_MATERIALS.get("GB_6061_T6")
al = mdb.ALL_MATERIALS.get("AL_6061_T6")
print("  GB_6061_T6 is AL_6061_T6 ?", g is al)
print("  GB_6061_T6 id=%s   AL_6061_T6 id=%s" % (id(g), id(al)))
print("  GB_6061_T6 yield_mpa=%s  E_mpa=%s" % (g.get("yield_mpa"), g.get("E_mpa")))
print("  AL_6061_T6 yield_mpa=%s  E_mpa=%s" % (al.get("yield_mpa"), al.get("E_mpa")))
print("  GB_6061_T6 equivalent_id=%s" % g.get("equivalent_id"))

print()
print("=" * 100)
print("Step5c-3  是否 get_material 返回了【活引用】并被调用方改写？")
print("=" * 100)
r1 = mdb.get_material("AL_6061_T6")
print("  get_material('AL_6061_T6') is ALL_MATERIALS['AL_6061_T6'] ?",
      r1 is mdb.ALL_MATERIALS["AL_6061_T6"])
r2 = mdb.get_material("GB_6061_T6")
print("  get_material('GB_6061_T6') is ALL_MATERIALS['GB_6061_T6'] ?",
      r2 is mdb.ALL_MATERIALS["GB_6061_T6"])
print("  → 返回活引用意味着调用方（如 _apply_equivalent/报告）的改动会污染全局表")

print()
print("=" * 100)
print("Step5c-4  最终 ZL104 逐字段（当前进程）")
print("=" * 100)
z = mdb.get_material("GB_ZL104")
for f, p in z["field_provenance"].items():
    print("   %-15s = %-10s %s" % (f, p.get("value"), p.get("source")))
print("   values_source =", z.get("values_source"))
