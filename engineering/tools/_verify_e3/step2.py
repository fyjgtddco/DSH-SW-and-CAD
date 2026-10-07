# -*- coding: utf-8 -*-
"""E3 Step2: claimability judgement (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb
from physics import simulation_report as srep

raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))
ids = list(raw["materials"].keys())

FIELDS = ["E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"]

print("=" * 100)
print("STEP 2a: get_material() -> field_provenance / values_from_gb / gb_sourced_fields / handbook_fields")
print("=" * 100)
bad_claim = []
for mid in ids:
    m = mdb.get_material(mid)
    if not m:
        print(f"{mid}: get_material -> None"); continue
    prov = m.get("field_provenance", {})
    print(f"\n--- {mid}  ({m.get('name_cn')})")
    print(f"    values_from_gb   = {m.get('values_from_gb')}")
    print(f"    gb_sourced_fields= {m.get('gb_sourced_fields')}")
    print(f"    handbook_fields  = {m.get('handbook_fields')}")
    print(f"    native_fields    = {m.get('native_fields')}")
    print(f"    fallback_fields  = {m.get('fallback_fields')}")
    print(f"    equivalent_filled= {m.get('equivalent_filled')}  equivalent_name={m.get('equivalent_name')}")
    print(f"    values_pending   = {m.get('values_pending')}")
    print(f"    consistency_guard= {m.get('consistency_guard')}")
    print(f"    values_source    = {m.get('values_source')}")
    for f in FIELDS:
        p = prov.get(f, {})
        print(f"      {f:<15} value={str(p.get('value')):>10}  source={p.get('source'):<20} layer={p.get('layer')}")
    if m.get("values_from_gb"):
        bad_claim.append(mid)

print()
print("=" * 100)
print("STEP 2b: values_from_gb==True list (should be EMPTY or only truly all-GB grades)")
print("=" * 100)
print("values_from_gb=True:", bad_claim)
print("(NOTE: 45#/304/Q355/65Mn/316/ZCuSn10P1/H62 have GB_ORIGINAL sigma, but E/nu/rho=HANDBOOK")
print(" -> values_from_gb MUST be False for them)")

print()
print("=" * 100)
print("STEP 2c: simulation_report._material_provenance_note() claimable_as_gb")
print("=" * 100)
import inspect
print("SIGNATURE:", inspect.signature(srep._material_provenance_note))
print()
print(inspect.getsource(srep._material_provenance_note))
