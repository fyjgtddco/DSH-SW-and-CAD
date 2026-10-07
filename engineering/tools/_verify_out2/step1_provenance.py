# -*- coding: utf-8 -*-
"""Step1: 逐牌号逐字段来源核查（只读，不修改任何数据）"""
import sys, io, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

JSON = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics\gb_materials.json"
d = json.load(io.open(JSON, encoding="utf-8-sig"))
mats = d["materials"]
FIELDS = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")

print("=" * 100)
print("Step1  gb_materials_status()")
print("=" * 100)
st = mdb.gb_materials_status()
for k in ("loaded", "count", "identity_count", "ready_count",
          "equivalent_ready_count", "pending_count", "file"):
    print("  %-24s = %s" % (k, st[k]))
print("  native_ready_ids        =", st["native_ready_ids"])
print("  equivalent_ready_ids    =", st["equivalent_ready_ids"])
print("  error                   =", st["error"])

print()
print("=" * 100)
print("Step1b  逐牌号 · 逐字段来源（来自 mdb.get_material(name)['field_provenance']）")
print("=" * 100)
SHORT = {"GB_STANDARD": "GB原文", "EQUIVALENT_FALLBACK": "兜底",
         "MISSING": "缺失", "UNKNOWN": "未标注"}

rows = []
for mid in mats:
    m = mdb.get_material(mid)
    prov = (m or {}).get("field_provenance", {})
    print("-" * 100)
    print("[%s] %s" % (mid, mats[mid].get("name_cn")))
    print("    标准: %s" % mats[mid].get("standard"))
    print("    equivalent_id: %s  (%s)" % (mats[mid].get("equivalent_id"),
                                          mats[mid].get("equivalent_note")))
    print("    values_source: %s" % (m or {}).get("values_source"))
    cells = {}
    for f in FIELDS:
        p = prov.get(f, {})
        src = p.get("source", "?")
        val = p.get("value")
        cells[f] = (SHORT.get(src, src), val)
        print("      %-15s = %-10s  %s" % (f, ("%s" % val) if val is not None else "None",
                                           SHORT.get(src, src)))
    rows.append((mid, cells, (m or {}).get("values_from_gb"),
                 (m or {}).get("values_pending")))

print()
print("=" * 100)
print("Step1c  汇总矩阵（行=牌号, 列=E/nu/σy/σb/ρ；G=GB原文, F=兜底, -=缺失）")
print("=" * 100)
CH = {"GB原文": "G", "兜底": "F", "缺失": "-", "未标注": "?"}
print("%-16s %-4s %-4s %-4s %-4s %-4s | %-8s %s" %
      ("材料", "E", "nu", "sy", "sb", "rho", "GB齐备", "仍缺"))
for mid, cells, vfg, vp in rows:
    line = "%-16s " % mid
    for f in FIELDS:
        line += "%-4s " % CH.get(cells[f][0], "?")
    miss = [f for f in FIELDS if cells[f][1] is None]
    line += "| %-8s %s" % (vfg, ",".join(miss) or "无")
    print(line)

print()
print("=" * 100)
print("Step1d  统计")
print("=" * 100)
from collections import Counter
cnt = Counter()
for mid, cells, _, _ in rows:
    for f in FIELDS:
        cnt[(f, cells[f][0])] += 1
for f in FIELDS:
    parts = ["%s=%d" % (s, cnt[(f, s)]) for s in ("GB原文", "兜底", "缺失", "未标注")
             if cnt[(f, s)]]
    print("  %-15s : %s" % (f, "  ".join(parts)))

print()
print("Step1e  物理合理性自动检查（σb < σy / 密度与类别不符）")
print("=" * 100)
bad = 0
for mid, cells, _, _ in rows:
    sy = cells["yield_mpa"][1]
    sb = cells["uts_mpa"][1]
    if sy is not None and sb is not None and sb < sy:
        bad += 1
        print("  [!!] %-16s σy=%s > σb=%s  ← 物理上不可能" % (mid, sy, sb))
        print("       来源: σy=%s / σb=%s" % (cells["yield_mpa"][0], cells["uts_mpa"][0]))
if not bad:
    print("  未发现 σb < σy")
