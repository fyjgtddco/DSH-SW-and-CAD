# -*- coding: utf-8 -*-
"""第3步补充：定位 GCr15「如实拒绝 vs 崩溃」的真实行为。"""
import sys, os, json, copy, traceback

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))

import load_case as lc_mod
import fea_solver
import physics_bridge as pb
import workflow_gate as wg

OUT = os.path.join(TOOLS, "_verify_out")

print("=" * 78)
print("### A. GCr15 直接喂给 solve_fea（绕过 load_case 校验）")
print("    目的：看 FEA 层自己有没有『材料数值待补全』的拦截")
print("=" * 78)
with open(os.path.join(OUT, "case_GCr15.json"), "r", encoding="utf-8") as f:
    gcase = json.load(f)
print("material =", json.dumps(gcase["material"], ensure_ascii=False))
try:
    res = fea_solver.solve_fea(gcase, output_dir=os.path.join(OUT, "fea_GCr15_direct"))
    print("  solve_fea 未抛异常，返回：")
    for k in ("ok", "method", "error", "safety_factor", "max_von_mises_mpa",
              "overall", "material_guard", "limitations", "gates"):
        if k in res:
            print("    %-18s = %s" % (k, json.dumps(res.get(k), ensure_ascii=False)[:500]))
except Exception as e:
    print("  !! solve_fea 抛异常:", repr(e))
    print(traceback.format_exc()[-1200:])

print()
print("=" * 78)
print("### B. GCr15 --relaxed（宽松校验）")
print("=" * 78)
try:
    r = lc_mod.load_from_file(os.path.join(OUT, "case_GCr15.json"), strict=False)
    print("  ok=%s" % r.get("ok"))
    print("  errors=%s" % json.dumps(r.get("errors"), ensure_ascii=False))
    print("  warnings=%s" % json.dumps(r.get("warnings"), ensure_ascii=False)[:400])
except Exception as e:
    print("  !! 抛异常:", repr(e))
    print("  ", traceback.format_exc().strip().splitlines()[-1])

print()
print("=" * 78)
print("### C. 逐个牌号：把 material 直接写进工况 → load_case 校验是否崩溃")
print("=" * 78)
for nm in ("Q235", "40Cr", "6061 铝", "GCr15", "HT200", "QT500-7",
           "ZCuSn10P1", "H62", "304 不锈钢", "65Mn"):
    m = wg._material_for(nm, "")
    c = {"schema_version": "1.0",
         "meta": {"problem_id": "PROBE", "title": "probe"},
         "design_domain": {"bounds": {"x_min": 0, "x_max": 100, "y_min": 0,
                                      "y_max": 60, "z_min": 0, "z_max": 10}},
         "material": m,
         "spatial_selectors": [], "boundary_conditions": [], "loads": [],
         "acceptance": {}}
    try:
        r = lc_mod.validate_load_case(c, strict=True)
        print("  %-12s -> ok=%-5s errors=%s" % (nm, r.get("ok"),
              json.dumps(r.get("errors"), ensure_ascii=False)[:220]))
    except Exception as e:
        print("  %-12s -> !! CRASH %s: %s" % (nm, type(e).__name__, e))

print()
print("=" * 78)
print("### D. GCr15 走 physics_bridge.cmd_optimize 全链路")
print("=" * 78)
try:
    r = pb.cmd_optimize(os.path.join(OUT, "case_GCr15.json"), max_iter=1, room="")
    print("  返回:", json.dumps(r, ensure_ascii=False)[:900])
except Exception as e:
    print("  !! cmd_optimize 抛异常:", type(e).__name__, e)
    print("  ", traceback.format_exc().strip().splitlines()[-1])

print()
print("=" * 78)
print("### E. GCr15 走 cmd_build 全链路")
print("=" * 78)
try:
    r = pb.cmd_build(os.path.join(OUT, "case_GCr15.json"))
    print("  返回:", json.dumps(r, ensure_ascii=False)[:900])
except Exception as e:
    print("  !! cmd_build 抛异常:", type(e).__name__, e)
    print("  ", traceback.format_exc().strip().splitlines()[-1])
