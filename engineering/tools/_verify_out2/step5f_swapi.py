# -*- coding: utf-8 -*-
"""Step5f: swapi 平行合并链的口径问题（SW 报告实际走的路）"""
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb
import swapi

print("=" * 100)
print("Step5f-1  swapi 里 GB 牌号的【实际键名】（我上一步查错键了，先纠正）")
print("=" * 100)
cm = swapi.COMMON_MATERIALS
keys = sorted(k for k in cm if any(c.isdigit() for c in str(k)))
print("  COMMON_MATERIALS 含数字的键（前 60）:")
for k in keys[:60]:
    print("     %r" % k)

print()
print("=" * 100)
print("Step5f-2  按【正确键名】对比 mdb vs swapi")
print("=" * 100)
# 用 name_cn 推导 swapi 键（与 swapi 内部 _disp 逻辑一致）
print("%-14s %-22s %-24s %s" % ("GB id", "swapi键(_disp)", "mdb(σy/σb)", "swapi(σy/σb/dens)"))
print("-" * 100)
for mid, v in mdb.GB_MATERIALS.items():
    cn = str(v.get("name_cn") or "")
    head = cn.split("（")[0].strip()
    if "/" in head:
        head = head.split("/")[0].strip()
    m = mdb.get_material(mid)
    sw = cm.get(head)
    a = "%s/%s" % (m.get("yield_mpa"), m.get("uts_mpa")) if m else "—"
    b = ("%s/%s/%s" % (sw.get("yield_mpa"), sw.get("uts_mpa"), sw.get("density"))
         if sw else "（无此键）")
    flag = ""
    if m and sw and m.get("yield_mpa") != sw.get("yield_mpa"):
        flag = "  <<< σy 不一致"
    print("%-14s %-22s %-24s %s%s" % (mid, head, a, b, flag))

print()
print("=" * 100)
print("Step5f-3  swapi 是否复现 σb < σy 物理不可能（material_db 已修，swapi 未修？）")
print("=" * 100)
bad = []
for k, e in cm.items():
    sy, sb = e.get("yield_mpa"), e.get("uts_mpa")
    if sy is not None and sb is not None and float(sb) < float(sy):
        bad.append((k, sy, sb, e.get("values_source"), e.get("values_pending")))
for k, sy, sb, vs, vp in bad:
    print("  [!!] swapi COMMON_MATERIALS[%r]: σy=%s > σb=%s  ← 物理不可能" % (k, sy, sb))
    print("        values_source=%s  values_pending=%s" % (vs, vp))
if not bad:
    print("  （swapi 中未发现 σb < σy）")

print()
print("=" * 100)
print("Step5f-4  swapi 的 values_source 标记质量（是否整体化误标）")
print("=" * 100)
print("%-22s %-10s %-9s %-8s %s" % ("swapi键", "σy", "σb", "from_gb", "values_source"))
for k, e in sorted(cm.items()):
    if not isinstance(e, dict):
        continue
    if e.get("standard") and ("GB" in str(e.get("standard")) or "gb_source" in e):
        print("%-22s %-10s %-9s %-8s %s"
              % (k, e.get("yield_mpa"), e.get("uts_mpa"),
                 e.get("values_from_gb"), (e.get("values_source") or "")[:60]))
