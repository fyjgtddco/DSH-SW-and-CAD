# -*- coding: utf-8 -*-
"""Step5b: 定位 ZL104 σy 时有时无 —— 是否 order-dependent / 被 swapi 合并污染"""
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

def snap(tag):
    a = mdb.get_material("ZL104")
    b = mdb.get_material("GB_ZL104")
    raw = mdb.GB_MATERIALS["GB_ZL104"]
    print("[%s]" % tag)
    print("   get_material('ZL104')    σy=%-8s σb=%-8s E=%-8s fallback_filled=%s"
          % (a.get("yield_mpa"), a.get("uts_mpa"), a.get("E_mpa"),
             a.get("equivalent_filled")))
    print("   get_material('GB_ZL104') σy=%-8s σb=%-8s E=%-8s fallback_filled=%s"
          % (b.get("yield_mpa"), b.get("uts_mpa"), b.get("E_mpa"),
             b.get("equivalent_filled")))
    print("   GB_MATERIALS['GB_ZL104']  σy=%-8s σb=%-8s E=%-8s  (原始表)"
          % (raw.get("yield_mpa"), raw.get("uts_mpa"), raw.get("E_mpa")))

print("=" * 100)
print("Step5b  ZL104 查询顺序敏感性")
print("=" * 100)
snap("A 初始状态（未调 swapi）")

print()
print("--- 现在 import swapi 并触发 GB 合并 ---")
import swapi
try:
    r = swapi._ensure_gb_merged()
    print("   swapi._ensure_gb_merged() ->", {k: r.get(k) for k in ("ok", "count", "file", "error")})
except Exception as e:
    print("   err:", repr(e))

snap("B swapi 合并之后")

print()
print("--- 再调 material_db.reload_gb_materials() ---")
rr = mdb.reload_gb_materials()
print("   reload ->", {k: rr.get(k) for k in ("ok", "count", "file")})
snap("C reload 之后")

print()
print("=" * 100)
print("Step5b-2  根因：swapi 合并是否【就地改写】了 material_db.GB_MATERIALS 的条目？")
print("=" * 100)
raw = mdb.GB_MATERIALS["GB_ZL104"]
print("  GB_MATERIALS['GB_ZL104'] 的键:", sorted(raw.keys()))
print("  含 swapi 风格键 e_mpa/density ? ",
      ("e_mpa" in raw), ("density" in raw))
print("  equivalent_id =", raw.get("equivalent_id"))
print("  values_source =", raw.get("values_source"))

print()
print("=" * 100)
print("Step5b-3  其它牌号是否也有同样问题（顺序敏感性全扫描）")
print("=" * 100)
# 全新子进程无法在这里做；改为对比 ALL_MATERIALS 是否被污染
for mid in sorted(mdb.GB_MATERIALS):
    r = mdb.GB_MATERIALS[mid]
    extra = [k for k in r if k in ("e_mpa", "density", "poissons_ratio")
             or k.startswith("_swapi")]
    if extra:
        print("   [被污染] %-14s 多出键: %s" % (mid, extra))
print("  （无输出 = 未发现就地污染）")
