# -*- coding: utf-8 -*-
"""Step5e: 逐字段来源【误标】核查 —— JSON 原生字段是否被误报为『兜底』"""
import sys, io, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

JSON = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics\gb_materials.json"
raw = json.load(io.open(JSON, encoding="utf-8-sig"))["materials"]
F = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")

print("=" * 100)
print("Step5e  原生字段 vs 台账来源（不一致 = 误标）")
print("=" * 100)
print("判定规则：若某字段在 JSON 里【原生就有值】，就不该被标成 EQUIVALENT_FALLBACK")
print()
bad = []
for mid in raw:
    v = raw[mid]
    m = mdb.get_material(mid)
    prov = m.get("field_provenance", {})
    gbd = v.get("gb_data") or {}
    for f in F:
        native = v.get(f) is not None
        src = prov.get(f, {}).get("source")
        gbmark = (gbd.get(f) or {}).get("status")
        if native and src == "EQUIVALENT_FALLBACK":
            bad.append((mid, f, v.get(f), gbmark, prov[f].get("equivalent")))
        # 反向：字段为空但被标 GB_STANDARD
        if (not native) and src == "GB_STANDARD":
            bad.append((mid, f + "(空)", None, gbmark, "标成GB但JSON为空"))

print("%-16s %-15s %-10s %-14s %s" % ("牌号", "字段", "JSON原值", "gb_data状态", "台账来源"))
print("-" * 100)
for mid, f, val, gbmark, eq in bad:
    print("%-16s %-15s %-10s %-14s %s"
          % (mid, f, val, gbmark, "EQUIVALENT_FALLBACK(equivalent=%s)" % eq))
if not bad:
    print("（无）")
print()
print("误标总数 =", len(bad))

print()
print("=" * 100)
print("Step5e-2  逐个细看（受影响牌号）")
print("=" * 100)
for mid in sorted(set(b[0] for b in bad)):
    v = raw[mid]
    m = mdb.get_material(mid)
    print("-" * 100)
    print("[%s] equivalent_id=%s  equivalent_name=%s"
          % (mid, v.get("equivalent_id"), m.get("equivalent_name")))
    for f in F:
        native = v.get(f)
        src = m["field_provenance"][f]["source"]
        flag = ""
        if native is not None and src == "EQUIVALENT_FALLBACK":
            flag = "  <<< 误标：JSON 原生有值却报兜底"
        print("   %-15s JSON=%-10s 台账=%-22s%s" % (f, native, src, flag))
    print("   values_source = %s" % m.get("values_source"))
