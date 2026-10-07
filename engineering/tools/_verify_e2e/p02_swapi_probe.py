# -*- coding: utf-8 -*-
"""探针2：swapi 侧的国标牌号解析与来源标注（对比 material_db 口径）。"""
import os, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import swapi, material_db as mdb

print("=" * 96)
print("swapi.is_valid_material  vs  material_db.get_material（同一牌号，两条口径）")
print("=" * 96)
hdr = ("%-11s | %-9s %-9s %-8s | %-9s %-9s %-8s | %s"
       % ("牌号", "sw σy", "sw σb", "sw from_gb",
          "db σy", "db σb", "db from_gb", "sw values_source"))
print(hdr)
print("-" * 96)
for n in ("Q235", "45#", "65Mn", "Q355", "40Cr", "GCr15", "ZL104", "HT200",
          "QT500-7", "ZCuSn10P1", "H62", "6061-T6", "304", "20CrMnTi"):
    ok, i, why = swapi.is_valid_material(n)
    d = mdb.get_material(n) or {}
    print("%-11s | %-9s %-9s %-8s | %-9s %-9s %-8s | %s"
          % (n, (i or {}).get("yield_mpa"), (i or {}).get("uts_mpa"),
             (i or {}).get("values_from_gb"),
             d.get("yield_mpa"), d.get("uts_mpa"), d.get("values_from_gb"),
             ((i or {}).get("values_source") or "")[:52]))

print()
print("=" * 96)
print("一致性判定：swapi 侧 σy 是否等于 material_db 侧（GB 数值）")
print("=" * 96)
for n in ("Q235", "45#", "65Mn", "Q355", "40Cr", "GCr15", "ZL104", "HT200",
          "QT500-7", "ZCuSn10P1", "H62", "6061-T6", "304", "20CrMnTi"):
    ok, i, why = swapi.is_valid_material(n)
    d = mdb.get_material(n) or {}
    a, b = (i or {}).get("yield_mpa"), d.get("yield_mpa")
    same = (a == b)
    print("  %-11s sw=%-8s db=%-8s %s"
          % (n, a, b, "一致" if same else ">>> 不一致 <<<"))
