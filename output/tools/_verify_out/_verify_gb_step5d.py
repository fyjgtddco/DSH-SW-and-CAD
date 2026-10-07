# -*- coding: utf-8 -*-
"""核查可疑解析：ZL104（铸铝）为何拿到钢的数值？"""
import sys, os, json

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import workflow_gate as wg
import material_db as mdb

print("=" * 78)
print("### ZL104 解析链路逐级排查")
print("=" * 78)
print("[a] material_db.get_material('ZL104') —— 库层给什么")
_m = mdb.get_material("ZL104")
print("   ", json.dumps({k: _m.get(k) for k in
      ("id", "E_mpa", "yield_mpa", "density_kg_m3", "equivalent_id",
       "equivalent_name", "values_source")}, ensure_ascii=False))

print()
print("[b] _GB_KEYWORD_TO_GRADE 里有没有能匹配 'zl104' 的关键词")
_hay = "zl104"
_hit = None
for keys, disp in wg._GB_KEYWORD_TO_GRADE:
    if any(k in _hay for k in keys):
        _hit = (keys, disp); break
print("    命中 =", _hit)

print()
print("[c] _MATERIAL_LOOKUP 里有没有能匹配 'zl104' 的关键词")
_hit2 = None
for _keys, _mid, _E, _nu, _sy, _rho, _disp in wg._MATERIAL_LOOKUP:
    if any(str(k) in _hay for k in _keys):
        _hit2 = (_keys, _mid, _E, _sy, _rho, _disp); break
print("    命中 =", _hit2)

print()
print("[d] 最终 _material_for('ZL104') 返回的完整来源标注")
_r = wg._material_for("ZL104", "")
print("   ", json.dumps(_r, ensure_ascii=False, indent=2))

print()
print("=" * 78)
print("### 对照：ZL104 的 equivalent_id 指向 AL_6061_T6，本应是铝")
print("=" * 78)
_a = mdb.get_material("AL_6061_T6")
print("   AL_6061_T6 ->", json.dumps({k: _a.get(k) for k in
      ("name", "E_mpa", "yield_mpa", "density_kg_m3")}, ensure_ascii=False))
print("   ZL104 经门禁实际拿到 ->", json.dumps(
    {k: _r.get(k) for k in ("youngs_modulus_mpa", "yield_strength_mpa",
                            "density_kg_m3", "source")}, ensure_ascii=False))
print()
print("   ⚠️ 结论：ZL104 是【铸造铝合金】(ρ≈2700)，却被解析成钢 (ρ=7850)。")

print()
print("=" * 78)
print("### 全部 18 个牌号：门禁解析结果的 source 与数值来源一览")
print("=" * 78)
for k, v in mdb.GB_MATERIALS.items():
    _cn = str(v.get("name_cn") or "")
    _disp = _cn.split("（")[0].strip()
    if "/" in _disp:
        _disp = _disp.split("/")[0].strip()
    _mm = wg._material_for(_disp, "")
    print("  %-14s %-12s E=%-8s sy=%-7s rho=%-8s src=%s"
          % (k, _disp, _mm.get("youngs_modulus_mpa"),
             _mm.get("yield_strength_mpa"), _mm.get("density_kg_m3"),
             _mm.get("source")))
    print("  %-14s %-12s values_source=%s"
          % ("", "", _mm.get("values_source")))
