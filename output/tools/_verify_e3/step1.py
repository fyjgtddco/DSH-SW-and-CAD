# -*- coding: utf-8 -*-
"""E3 Step1: data completeness + source-layer correctness (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\physics")

import material_db as mdb

print("=" * 78)
print("STEP 1a: gb_materials_status()")
print("=" * 78)
st = mdb.gb_materials_status()
print(json.dumps(st, ensure_ascii=False, indent=2)[:6000])

print()
print("=" * 78)
print("STEP 1b: per-grade per-field raw dump from gb_materials.json")
print("=" * 78)
with open(ROOT + r"\physics\gb_materials.json", encoding="utf-8") as f:
    raw = json.load(f)

FIELDS = ["E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"]
layer_tally = {}
print(f"{'material':<22}{'field':<16}{'value':>12}  {'status':<9}{'layer':<18}{'top_level_layer'}")
for mid, m in raw["materials"].items():
    gd = m.get("gb_data", {})
    tl = m.get("source_layers", {})
    for fld in FIELDS:
        e = gd.get(fld, {})
        v = e.get("value", None)
        stt = e.get("status", "?")
        lay = e.get("source_layer", "?")
        tll = tl.get(fld, "?")
        flag = "" if lay == tll else "   <<< MISMATCH(gb_data vs source_layers)"
        if lay != tll:
            flag = "   <<<MISMATCH"
        print(f"{mid:<22}{fld:<16}{str(v):>12}  {stt:<9}{lay:<18}{tll}{flag}")
        layer_tally[lay] = layer_tally.get(lay, 0) + 1
    print("-" * 78)

print()
print("LAYER TALLY (field-instances):", json.dumps(layer_tally, ensure_ascii=False))
print("EXPECTED if E/nu/rho all HANDBOOK: HANDBOOK = 18*3 = 54")
