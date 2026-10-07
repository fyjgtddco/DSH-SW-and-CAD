# -*- coding: utf-8 -*-
"""收尾核查：ZL104 变体 + is_valid_material 对空数值牌号的诚实性。"""
import sys, os, json

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import workflow_gate as wg
import material_db as mdb
import swapi

print("=" * 78)
print("### ZL104 不同写法下的解析结果")
print("=" * 78)
for s in ("ZL104", "ZL104 铸铝", "ZL104 铸造铝合金", "铸铝", "ZL104 铝合金"):
    r = wg._material_for(s, "")
    print("  _material_for(%-16r) -> id=%-10s E=%-8s sy=%-7s rho=%-8s src=%s"
          % (s, r.get("id"), r.get("youngs_modulus_mpa"),
             r.get("yield_strength_mpa"), r.get("density_kg_m3"), r.get("source")))
    print("  %-24s values_source=%r values_pending=%r"
          % ("", r.get("values_source"), r.get("values_pending")))

print()
print("=" * 78)
print("### is_valid_material 对『无数值』牌号是否报 ok=True（诚实性）")
print("=" * 78)
for nm in ("GCr15", "HT200", "QT500-7", "ZCuSn10P1", "H62", "Q235"):
    ok, info, msg = swapi.is_valid_material(nm)
    _has = bool(info and info.get("e_mpa") and info.get("yield_mpa")
                and info.get("density"))
    print("  %-12s ok=%-5s 力学数值齐全=%-5s msg=%-22s values_pending=%s"
          % (nm, ok, _has, msg, (info or {}).get("values_pending")))

print()
print("=" * 78)
print("### swapi 是否有『国标数值待补全』的显式拒绝入口")
print("=" * 78)
for _fn in ("gb_materials_status", "gb_material_ready", "is_valid_material",
            "lookup_material", "default_part_material_name"):
    print("  swapi.%-28s 存在=%s" % (_fn, hasattr(swapi, _fn)))
try:
    st = swapi.gb_materials_status()
    print("  swapi.gb_materials_status() = %s"
          % json.dumps({k: v for k, v in st.items() if k != "searched"},
                       ensure_ascii=False))
except Exception as e:
    print("  swapi.gb_materials_status() 调用失败:", repr(e))

print()
print("=" * 78)
print("### 门禁 gb_data_status 是否会被写进下游报告")
print("=" * 78)
_st = wg._gb_data_status()
print("  _gb_data_status().ready_count =", _st.get("ready_count"),
      " pending_count =", _st.get("pending_count"))
import inspect
_src = inspect.getsource(wg)
print("  workflow_gate 中 _gb_data_status 被调用处:")
for _i, _l in enumerate(_src.splitlines(), 1):
    if "_gb_data_status()" in _l and "def " not in _l:
        print("    L%-6d %s" % (_i, _l.strip()[:110]))
