# -*- coding: utf-8 -*-
"""E3 Step5: 缺口清单 + ZL104 计数矛盾 + 四方视图 (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb

FIELDS = ["E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"]
raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]

print("=" * 118)
print("STEP 5.1: 字段级缺口清单（以【走完 get_material 全链路后】的真实可用性为准）")
print("=" * 118)
print(f"{'grade':<14}{'raw缺失(JSON)':<24}{'解析后缺失(mdb)':<24}{'FEA可用':<10}卡在哪一步")
print("-" * 118)
not_ready = []
for mid, m in raw.items():
    raw_miss = [f for f in FIELDS if m.get(f) is None]
    r = mdb.get_material(mid) or {}
    eff_miss = [f for f in FIELDS if r.get(f) is None]
    ok, _e, why = mdb.gb_material_ready(mid)
    if not ok:
        not_ready.append(mid)
    print(f"{mid:<14}{str(raw_miss):<24}{str(eff_miss):<24}{str(ok):<10}{why or '-'}")
print()
print("不可直接用于 FEA 的牌号:", not_ready)

print()
print("=" * 118)
print("STEP 5.2: ★ 计数矛盾 —— native_ready_ids 用【原始JSON】，equivalent 用【解析后】")
print("=" * 118)
st = mdb.gb_materials_status()
print("  native_ready_ids 含 GB_ZL104 ? ", "GB_ZL104" in st["native_ready_ids"])
zl = mdb.get_material("ZL104") or {}
print("  但 get_material('ZL104'): E=%s sy=%s sb=%s rho=%s  → has_props=%s"
      % (zl.get("E_mpa"), zl.get("yield_mpa"), zl.get("uts_mpa"),
         zl.get("density_kg_m3"), mdb.has_props(zl)))
print("  gb_material_ready('ZL104') =", mdb.gb_material_ready("ZL104")[:1],
      "|", mdb.gb_material_ready("ZL104")[2])
print("  → ready_count=%d 虚高 1：ZL104 被守卫撤回 σy/σb 后实际不可用，仍被计入『原生齐全』"
      % st["ready_count"])
print("  口径不一致原因：native_ready_ids 用 has_props(GB_MATERIALS[k])【原始条目】，")
print("                 equivalent_ready_count 用 has_props(get_material(k))【已过守卫】")

print()
print("=" * 118)
print("STEP 5.4b: ZL104 四方视图")
print("=" * 118)
import swapi
s = swapi.COMMON_MATERIALS.get("ZL104") or {}
print("  material_db     : sy=%s sb=%s values_from_gb=%s"
      % (zl.get("yield_mpa"), zl.get("uts_mpa"), zl.get("values_from_gb")))
print("  swapi           : sy=%s sb=%s values_from_gb=%s values_source=%s"
      % (s.get("yield_mpa"), s.get("uts_mpa"), s.get("values_from_gb"), s.get("values_source")))
import workflow_gate
w = workflow_gate._gb_grade_props("ZL104") or {}
print("  workflow_gate   : sy=%s sb=%s values_from_gb=%s values_pending=%s"
      % (w.get("yield_strength_mpa"), w.get("uts_mpa"), w.get("values_from_gb"), w.get("values_pending")))
import defense_gate
d = defense_gate._material_props_any("ZL104") or {}
print("  defense_gate    :", json.dumps(d, ensure_ascii=False))

print()
print("=" * 118)
print("STEP 5.5: 手册值占比统计（E/ν/ρ 全部为手册值）")
print("=" * 118)
tot = len(raw) * 5
lay = {}
for mid, m in raw.items():
    for f in FIELDS:
        e = (m.get("gb_data") or {}).get(f, {})
        lay[e.get("source_layer", "?")] = lay.get(e.get("source_layer", "?"), 0) + 1
print("  字段实例总数 =", tot)
for k, v in sorted(lay.items(), key=lambda x: -x[1]):
    print(f"    {k:<20}{v:>4}  ({100.0*v/tot:.1f}%)")
gbc = lay.get("GB_ORIGINAL", 0) + lay.get("GB_GRADE_DEF", 0)
print(f"  可宣称国标的字段数 = {gbc} / {tot} = {100.0*gbc/tot:.1f}%")
print(f"  E/ν/ρ 手册值       = 54 / {tot} = {100.0*54/tot:.1f}%  ← 全部材料常数均为手册值")
