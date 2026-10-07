# -*- coding: utf-8 -*-
"""第3步：真实跑物理校核 —— Q235（有数值）vs GCr15（无数值）。"""
import sys, os, json, copy

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))

import workflow_gate as wg
import load_case as lc_mod
import fea_solver
import material_db as mdb

BASE = os.path.join(TOOLS, "load_cases", "gate_load_case.json")
with open(BASE, "r", encoding="utf-8") as f:
    base_case = json.load(f)

OUT = os.path.join(TOOLS, "_verify_out")
os.makedirs(OUT, exist_ok=True)


def make_case(mat_text, tag):
    c = copy.deepcopy(base_case)
    c["meta"]["problem_id"] = "VERIFY_%s" % tag
    c["meta"]["title"] = "国标材料验证：%s" % mat_text
    m = wg._material_for(mat_text, "")
    c["material"] = m
    p = os.path.join(OUT, "case_%s.json" % tag)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    return p, c


for mat_text, tag in (("Q235 碳钢", "Q235"), ("GCr15 轴承", "GCr15")):
    print("=" * 78)
    print("### 材料 %s  (tag=%s)" % (mat_text, tag))
    print("=" * 78)
    path, case = make_case(mat_text, tag)
    print("[写出工况] %s" % path)
    print("[工况材料] %s" % json.dumps(
        {k: case["material"].get(k) for k in
         ("id", "name", "youngs_modulus_mpa", "poissons_ratio",
          "yield_strength_mpa", "density_kg_m3", "values_source",
          "values_from_gb", "values_pending")}, ensure_ascii=False))

    print()
    print("--- validate-case ---")
    r = lc_mod.load_from_file(path, strict=True)
    print("  ok=%s" % r.get("ok"))
    print("  errors=%s" % json.dumps(r.get("errors"), ensure_ascii=False))
    print("  warnings=%s" % json.dumps(r.get("warnings"), ensure_ascii=False)[:600])

    print()
    print("--- solve_fea (核心静力校核) ---")
    od = os.path.join(OUT, "fea_%s" % tag)
    os.makedirs(od, exist_ok=True)
    try:
        res = fea_solver.solve_fea(r.get("case") or case, output_dir=od)
    except Exception as e:
        import traceback
        print("  !! solve_fea 抛异常:", repr(e))
        print(traceback.format_exc()[-2000:])
        continue
    for k in ("ok", "method", "error", "safety_factor", "max_von_mises_mpa",
              "max_displacement_mm", "volume_mm3", "mass_kg", "overall",
              "material_guard", "limitations"):
        if k in res:
            print("  %-22s = %s" % (k, json.dumps(res.get(k), ensure_ascii=False)))
    print("  gates:")
    for gk, gv in (res.get("gates") or {}).items():
        print("     %-20s %s" % (gk, json.dumps(gv, ensure_ascii=False)))
    print("  fatigue = %s" % json.dumps(res.get("fatigue"), ensure_ascii=False)[:400])
    print("  geometry_used = %s" % json.dumps(res.get("geometry_used"), ensure_ascii=False))
    print()
