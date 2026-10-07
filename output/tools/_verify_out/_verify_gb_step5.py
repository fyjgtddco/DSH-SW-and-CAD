# -*- coding: utf-8 -*-
"""第5步：边界与诚实性检查。
A. 5 个无数值牌号是否被明确拒绝，错误信息是否说清缺哪些字段/去哪补
B. 报告/结果里能否区分「国标数值」与「等效兜底值」
"""
import sys, os, json, copy, traceback, glob

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))

import material_db as mdb
import workflow_gate as wg
import load_case as lc_mod
import fea_solver

OUT = os.path.join(TOOLS, "_verify_out")
EMPTY5 = ("GCr15", "HT200", "QT500-7", "ZCuSn10P1", "H62")

print("=" * 78)
print("### A1. material_db 层的诚实拒绝（gb_material_ready）")
print("=" * 78)
for nm in EMPTY5:
    ok, entry, reason = mdb.gb_material_ready(nm)
    print("  %-12s ok=%-5s" % (nm, ok))
    print("      reason = %s" % reason)

print()
print("=" * 78)
print("### A2. 门禁层解析（_material_for）—— 数值是否为 None")
print("=" * 78)
for nm in EMPTY5:
    m = wg._material_for(nm, "")
    print("  %-12s E=%-8s sy=%-8s rho=%-8s values_source=%r"
          % (nm, m.get("youngs_modulus_mpa"), m.get("yield_strength_mpa"),
             m.get("density_kg_m3"), m.get("values_source")))
    print("               values_from_gb=%s values_pending=%s"
          % (m.get("values_from_gb"), m.get("values_pending")))

print()
print("=" * 78)
print("### A3. 把无数值牌号喂给 FEA 求解器 —— 是否 fail-closed 明确拒绝")
print("=" * 78)
base = json.load(open(os.path.join(TOOLS, "load_cases", "gate_load_case.json"),
                      encoding="utf-8"))
for nm in EMPTY5:
    c = copy.deepcopy(base)
    c["material"] = wg._material_for(nm, "")
    try:
        r = fea_solver.solve_fea(c, output_dir=os.path.join(OUT, "fea_empty_" + nm))
        print("  %-12s -> ok=%s" % (nm, r.get("ok")))
        print("      error = %s" % r.get("error"))
        if r.get("ok"):
            print("      !! 警告：竟然算出了结果 SF=%s" % r.get("safety_factor"))
    except Exception as e:
        print("  %-12s -> !! 抛异常 %s: %s" % (nm, type(e).__name__, e))

print()
print("=" * 78)
print("### A4. 完整工况校验链（load_case.validate_load_case）")
print("=" * 78)
for nm in EMPTY5:
    c = {"schema_version": "1.0",
         "meta": {"problem_id": "PROBE", "title": "probe"},
         "design_domain": {"bounds": {"x_min": 0, "x_max": 100, "y_min": 0,
                                      "y_max": 60, "z_min": 0, "z_max": 10}},
         "material": wg._material_for(nm, ""),
         "spatial_selectors": [], "boundary_conditions": [], "loads": [],
         "acceptance": {}}
    try:
        r = lc_mod.validate_load_case(c, strict=True)
        print("  %-12s ok=%-5s errors=%s" % (nm, r.get("ok"),
              json.dumps(r.get("errors"), ensure_ascii=False)))
    except Exception as e:
        print("  %-12s !! 崩溃 %s: %s" % (nm, type(e).__name__, e))

print()
print("=" * 78)
print("### B1. 报告里能否区分「国标数值」与「等效兜底值」")
print("=" * 78)
_rpts = sorted(glob.glob(os.path.join(
    TOOLS, "..", "..", "output", "physics_runs", "*", "iteration_*", "*_report.json")))
print("  找到报告 %d 份，检查最近一份：" % len(_rpts))
if _rpts:
    _p = _rpts[-1]
    print("  %s" % _p)
    _r = json.load(open(_p, encoding="utf-8"))
    print("  report.material = %s" % json.dumps(_r.get("material"), ensure_ascii=False))
    _txt = json.dumps(_r, ensure_ascii=False)
    for _k in ("values_source", "values_from_gb", "values_pending",
               "identity_source", "equivalent_name", "待补全", "等效兜底"):
        print("   报告全文是否出现 %-16s : %s" % (_k, _k in _txt))

print()
print("=" * 78)
print("### B2. swapi 材料查询是否携带 GB/等效标记")
print("=" * 78)
try:
    import swapi
    for nm in ("Q235", "GCr15"):
        ok, info, msg = swapi.is_valid_material(nm)
        print("  is_valid_material(%r) ok=%s" % (nm, ok))
        print("     info = %s" % json.dumps(info, ensure_ascii=False)[:700])
        print("     msg  = %s" % (str(msg)[:200] if msg else None))
except Exception as e:
    print("  !! swapi 不可用:", repr(e))

print()
print("=" * 78)
print("### B3. _material_for 返回值经 load_case 落盘后，标记是否还在")
print("=" * 78)
for tag in ("Q235", "GCr15"):
    p = os.path.join(OUT, "case_%s.json" % tag)
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        mm = d.get("material") or {}
        print("  case_%s.json material 标记: values_source=%r values_from_gb=%s values_pending=%s"
              % (tag, mm.get("values_source"), mm.get("values_from_gb"),
                 mm.get("values_pending")))
