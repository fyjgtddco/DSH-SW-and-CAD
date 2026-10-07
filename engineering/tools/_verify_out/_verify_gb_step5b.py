# -*- coding: utf-8 -*-
"""定位：国标标记（values_source/values_from_gb/values_pending）在哪一步丢失。"""
import sys, os, json, copy

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))

import material_db as mdb
import workflow_gate as wg
import physics_bridge as pb
import swapi
import simulation_report as sim_report

MARK = ("values_source", "values_from_gb", "values_pending",
        "identity_source", "equivalent_name", "standard")


def show(tag, d):
    if not isinstance(d, dict):
        print("  %-42s -> %r" % (tag, d)); return
    print("  %-42s -> %s" % (tag, json.dumps({k: d.get(k) for k in MARK if k in d},
                                             ensure_ascii=False)))


print("=" * 78)
print("### 链路：标记在各环节是否存活")
print("=" * 78)
print("[1] material_db.get_material('Q235') 原始输出")
_m = mdb.get_material("Q235")
show("material_db.get_material", _m)

print("[2] workflow_gate._material_for('Q235 碳钢')")
_g = wg._material_for("Q235 碳钢", "")
show("workflow_gate._material_for", _g)

print("[3] swapi.is_valid_material('Q235')  (零件赋材/读回路径)")
_ok, _i, _msg = swapi.is_valid_material("Q235")
show("swapi.is_valid_material", _i)

print("[4] physics_bridge._resolve_part_material(...)  ← 覆盖载荷材料的那一步")
# 模拟：零件读回名 = AISI 1020（SW 实际生效），凭据名 = Q235
_geo = {"part_material_name": "solidworks materials|AISI 1020|2",
        "part_material_density_kg_m3": 7900.0,
        "part_path": os.path.join(TOOLS, "_verify_out",
                                  "DSH_验证板_100x60x10.SLDPRT")}
_case = {"material": dict(_g)}
_mf, _mm = pb._resolve_part_material(_geo, _case)
print("  resolve meta.source =", _mm.get("source"))
show("_resolve_part_material 返回的 material", _mf)

print("[5] _props_from_name 直接测（这是丢标记的嫌疑点）")
# 通过闭包不可直接调，改为验证 _from_swapi 形态：比较 swapi 原始 vs 转换后
_orig = _i
_conv = {k: _orig.get(k) for k in MARK if k in _orig}
print("  swapi 原始里存在的标记字段:", list(_conv.keys()))
_missing = [k for k in ("values_source", "values_from_gb", "values_pending")
            if k not in (_mf or {})]
print("  _resolve_part_material 结果里【缺失】的标记:", _missing)

print()
print("=" * 78)
print("### 报告构建：simulation_report 是否读 material 里的这些标记")
print("=" * 78)
import inspect
_src = inspect.getsource(sim_report)
for k in ("values_source", "values_from_gb", "values_pending",
          "identity_source", "equivalent_name", "等效"):
    print("  simulation_report.py 中出现 %-16s : %s" % (k, k in _src))

print()
print("=" * 78)
print("### 全局搜索：谁在【消费】这些标记（除 material_db/workflow_gate 自身）")
print("=" * 78)
for _f in ("physics_bridge.py", "swapi.py", "sw_bridge.py", "defense_gate.py",
           "physics/simulation_report.py", "physics/fatigue.py",
           "physics/domain_validator.py", "physics/geometry_gate.py",
           "physics/load_case.py"):
    _p = os.path.join(TOOLS, _f)
    try:
        _t = open(_p, encoding="utf-8").read()
    except Exception as e:
        print("  %-34s 读取失败 %r" % (_f, e)); continue
    _hits = [k for k in ("values_source", "values_from_gb", "values_pending")
             if k in _t]
    print("  %-34s 命中标记: %s" % (_f, _hits or "无"))
