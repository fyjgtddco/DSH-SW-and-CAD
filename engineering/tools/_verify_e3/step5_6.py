# -*- coding: utf-8 -*-
"""E3 Step5.6: 定位 swapi 侧 values_from_gb 误报 True 的【真实范围】(READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb
import swapi

raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]

print("=" * 118)
print("STEP 5.6: swapi.COMMON_MATERIALS 的 values_from_gb vs material_db 的 values_from_gb")
print("=" * 118)
print(f"{'grade':<12}{'mdb.vfgb':<10}{'swapi.vfgb':<12}{'mdb.gb_fields':<28}{'swapi.gb_fields':<20}{'swapi.values_source':<22}判定")
print("-" * 118)
wrong = []
for mid, m in raw.items():
    disp = None
    for a in (m.get("aliases") or []):
        if a and a in swapi.COMMON_MATERIALS:
            disp = a; break
    if disp is None:
        # fall back to the id
        for k in swapi.COMMON_MATERIALS:
            if k.upper().startswith("GB_"):
                pass
    e = swapi.COMMON_MATERIALS.get(disp) if disp else None
    md = mdb.get_material(mid) or {}
    if not isinstance(e, dict):
        print(f"{mid:<12}{'-':<10}{'NOT FOUND':<12}")
        continue
    mv, sv = md.get("values_from_gb"), e.get("values_from_gb")
    mg, sg = md.get("gb_sourced_fields"), e.get("gb_sourced_fields")
    verdict = "OK"
    if bool(sv) != bool(mv):
        verdict = "★★ SWAPI 与 material_db 口径矛盾"
        wrong.append((mid, disp, mv, sv))
    print(f"{mid:<12}{str(mv):<10}{str(sv):<12}{str(mg):<28}{str(sg):<20}{str(e.get('values_source'))[:20]:<22}{verdict}")

print()
print("=" * 118)
print("矛盾牌号汇总 (共 %d 个):" % len(wrong))
print("=" * 118)
for mid, disp, mv, sv in wrong:
    print(f"  {mid} (显示名 {disp}): material_db values_from_gb={mv} 但 swapi={sv}")

print()
print("=" * 118)
print("STEP 5.6b: 复现 —— 这条错误标记如何进入 physics 报告")
print("=" * 118)
import physics_bridge as pb
geo = {"part_material_name": "solidworks materials|AISI 1020|2",
       "part_material_density_kg_m3": 7900.0,
       "part_path": ""}
case = json.load(open(ROOT + r"\_verify_e3\case_45.json", encoding="utf-8"))
mat, meta = pb._resolve_part_material(geo, case)
print("  _resolve_part_material 结果:")
print("    meta =", json.dumps(meta, ensure_ascii=False))
print("    material.id              =", mat.get("id"))
print("    yield_strength_mpa       =", mat.get("yield_strength_mpa"))
print("    values_source            =", mat.get("values_source"))
print("    values_from_gb           =", mat.get("values_from_gb"))
print("    gb_sourced_fields        =", mat.get("gb_sourced_fields"))
print("    field_provenance         =", mat.get("field_provenance"))
print("    source                   =", mat.get("source"))
print()
print("  → material_db 对同一牌号的正确口径:")
m = mdb.get_material("Q235")
print("    values_source     =", m.get("values_source"))
print("    values_from_gb    =", m.get("values_from_gb"))
print("    gb_sourced_fields =", m.get("gb_sourced_fields"))
print("    handbook_fields   =", m.get("handbook_fields"))
print()
print("  报告层判定 simulation_report._material_provenance_note(mat):")
from physics.simulation_report import _material_provenance_note
print("   ", json.dumps(_material_provenance_note(mat), ensure_ascii=False))
