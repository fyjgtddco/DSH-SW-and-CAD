# -*- coding: utf-8 -*-
"""补充：13/5 分界语义核对 + 13 个等效兜底牌号是否真能拿到数值。"""
import sys, os, json

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as mdb
import workflow_gate as wg

print("=" * 78)
print("### gb_materials_status 的 ready_count 语义核对")
print("=" * 78)
st = mdb.gb_materials_status()
print("  identity_count = %s" % st["identity_count"])
print("  ready_count    = %s   <- has_props(): JSON 里 E/yield/density 是否齐全" % st["ready_count"])
print("  pending_count  = %s" % st["pending_count"])
_n_eq = sum(1 for v in mdb.GB_MATERIALS.values() if v.get("equivalent_id"))
_n_noeq = len(mdb.GB_MATERIALS) - _n_eq
print()
print("  JSON 里 equivalent_id 非空的牌号数 = %d  (即『可算』的)" % _n_eq)
print("  JSON 里 equivalent_id 为空的牌号数 = %d  (即『故意留空』的)" % _n_noeq)
print("  说明：ready_count 数的是『国标原生数值是否齐全』，")
print("        而任务描述里的『13 可算』指的是『有 equivalent_id 可兜底』。")
print("        两者语义不同 —— 18 个条目的 E_mpa 在 JSON 里全部为 null。")

print()
print("=" * 78)
print("### 13 个有 equivalent_id 的牌号：经 _material_for 后是否真能拿到 E/sy/rho")
print("=" * 78)
_names = []
for k, v in mdb.GB_MATERIALS.items():
    _cn = str(v.get("name_cn") or "")
    _disp = _cn.split("（")[0].strip()
    if "/" in _disp:
        _disp = _disp.split("/")[0].strip()
    _names.append((k, _disp, bool(v.get("equivalent_id"))))

_ok_cnt = _fail_cnt = 0
for k, disp, has_eq in _names:
    m = wg._material_for(disp, "")
    E, sy, rho = (m or {}).get("youngs_modulus_mpa"), (m or {}).get("yield_strength_mpa"), (m or {}).get("density_kg_m3")
    _usable = bool(E and sy and rho)
    if _usable:
        _ok_cnt += 1
    else:
        _fail_cnt += 1
    print("  %-14s %-14s eq=%-5s E=%-8s sy=%-7s rho=%-8s usable=%s"
          % (k, disp, has_eq, E, sy, rho, _usable))
print()
print("  汇总：usable=%d / not-usable=%d （共 %d）" % (_ok_cnt, _fail_cnt, len(_names)))
print("  注意：usable 包含了『等效兜底』与『SW 密度反推』等非国标数值来源。")
