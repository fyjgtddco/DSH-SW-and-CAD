# -*- coding: utf-8 -*-
"""E3 final: 四方口径矩阵 + ZL104 守卫绕过 + 疲劳/领域链 (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb, swapi, workflow_gate, defense_gate, physics_bridge as pb
from physics.simulation_report import _material_provenance_note

raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]
case = {"material": {"name": "placeholder", "id": "x"}}

print("=" * 122)
print("四方口径矩阵：σy / σb / values_from_gb  (mdb | swapi | workflow_gate | physics_bridge显式路径)")
print("=" * 122)
print(f"{'grade':<13}{'mdb sy/sb':<18}{'swapi sy/sb':<18}{'wgate sy/sb':<18}{'pb sy/sb':<18}{'vfgb mdb/sw/wg/pb'}")
print("-" * 122)
issues = []
for mid, m in raw.items():
    disp = next((a for a in (m.get("aliases") or []) if a in swapi.COMMON_MATERIALS), None)
    md = mdb.get_material(mid) or {}
    sw = swapi.COMMON_MATERIALS.get(disp) or {} if disp else {}
    wg = workflow_gate._gb_grade_props(disp or mid) or {}
    pbm, _meta = pb._resolve_part_material({}, case, explicit_material=disp or mid)
    pbm = pbm or {}
    def ss(d, a, b):
        return "%s/%s" % (d.get(a), d.get(b))
    mdb_ss = ss(md, "yield_mpa", "uts_mpa")
    sw_ss = ss(sw, "yield_mpa", "uts_mpa")
    wg_ss = ss(wg, "yield_strength_mpa", "uts_mpa")
    pb_ss = ss(pbm, "yield_strength_mpa", "uts_mpa")
    vf = "%s/%s/%s/%s" % (md.get("values_from_gb"), sw.get("values_from_gb"),
                          wg.get("values_from_gb"), pbm.get("values_from_gb"))
    mism = []
    if mdb_ss != sw_ss: mism.append("swapi数值≠mdb")
    if mdb_ss != wg_ss: mism.append("wgate数值≠mdb")
    if mdb_ss != pb_ss: mism.append("pb数值≠mdb")
    print(f"{mid:<13}{mdb_ss:<18}{sw_ss:<18}{wg_ss:<18}{pb_ss:<18}{vf}  {'★★'+'、'.join(mism) if mism else ''}")
    if mism: issues.append((mid, mism, mdb_ss, sw_ss, wg_ss, pb_ss))

print()
print("数值不一致牌号:", [(i[0], i[1]) for i in issues])

print()
print("=" * 122)
print("★ ZL104 一致性守卫是否在所有入口生效？")
print("=" * 122)
print("  material_db.get_material('ZL104')      : sy=%-6s sb=%-6s  guard生效=%s"
      % ((mdb.get_material('ZL104') or {}).get('yield_mpa'),
         (mdb.get_material('ZL104') or {}).get('uts_mpa'),
         bool((mdb.get_material('ZL104') or {}).get('consistency_guard'))))
zsw = swapi.COMMON_MATERIALS.get('ZL104') or {}
print("  swapi.COMMON_MATERIALS['ZL104']        : sy=%-6s sb=%-6s  → σb<σy ? %s  ★ 守卫被绕过"
      % (zsw.get('yield_mpa'), zsw.get('uts_mpa'),
         (zsw.get('uts_mpa') is not None and zsw.get('yield_mpa') is not None
          and zsw['uts_mpa'] < zsw['yield_mpa'])))
zpb, _ = pb._resolve_part_material({}, case, explicit_material='ZL104')
zpb = zpb or {}
print("  physics_bridge 显式路径 ZL104          : sy=%-6s sb=%-6s  → σb<σy ? %s  ★ 守卫被绕过"
      % (zpb.get('yield_strength_mpa'), zpb.get('uts_mpa'),
         (zpb.get('uts_mpa') is not None and zpb.get('yield_strength_mpa') is not None
          and zpb['uts_mpa'] < zpb['yield_strength_mpa'])))
zwg = workflow_gate._gb_grade_props('ZL104') or {}
print("  workflow_gate._gb_grade_props('ZL104') : sy=%-6s sb=%-6s  → 守卫生效"
      % (zwg.get('yield_strength_mpa'), zwg.get('uts_mpa')))

print()
print("=" * 122)
print("swapi 分支绕过守卫的根因（swapi.py 1022-1065 逻辑）")
print("=" * 122)
print("""  _eid = 'AL_6061_T6' ; _missing = [f for f in (density,e_mpa,yield_mpa,uts_mpa,poissons_ratio) if _entry.get(f) is None]
  ZL104 的 JSON 原生已含全部 5 字段 → _missing == [] → `if _eid and _missing:` 为 False
  → 走 `elif _entry.get('e_mpa'):` 分支 → setdefault('values_source','GB 标准数值'),
    setdefault('values_from_gb', True)
  → ① 来源标注被无条件写成『GB 标准数值 / values_from_gb=True』
    ② material_db 的 _apply_equivalent()（含一致性守卫 + 逐字段台账）整段被跳过
  → ZL104 的 σy=160/σb=150（物理不可能）在 swapi 侧复活，报告却写 values_from_gb=True""")
