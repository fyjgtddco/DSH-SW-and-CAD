# -*- coding: utf-8 -*-
"""E3 Step4b: inspect the generated reports (material actually used + provenance)."""
import sys, json, io, glob, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main"
runs = sorted(glob.glob(os.path.join(ROOT, "output", "physics_runs", "opt_E3_*")))
print("runs:", runs)
for r in runs:
    for rep in glob.glob(os.path.join(r, "iteration_*", "*_report.json")):
        d = json.load(open(rep, encoding="utf-8"))
        print()
        print("=" * 100)
        print("REPORT:", rep)
        print("=" * 100)
        print("  run_id          =", d.get("run_id"))
        print("  material_resolution =", json.dumps(d.get("material_resolution"), ensure_ascii=False))
        lcs = d.get("load_case_summary", {})
        print("  load_case_summary.material      =", lcs.get("material"))
        print("  load_case_summary.material_id   =", lcs.get("material_id"))
        print("  load_case_summary.yield_strength=", lcs.get("yield_strength_mpa"))
        print("  load_case_summary.E_mpa         =", lcs.get("E_mpa"))
        print("  load_case_summary.density       =", lcs.get("density_kg_m3"))
        m = d.get("material") or {}
        print("  material.id=%s E=%s nu=%s sy=%s sb=%s rho=%s" % (
            m.get("id"), m.get("youngs_modulus_mpa"), m.get("poissons_ratio"),
            m.get("yield_strength_mpa"), m.get("uts_mpa"), m.get("density_kg_m3")))
        print("  material.values_source   =", m.get("values_source"))
        print("  material.values_from_gb  =", m.get("values_from_gb"))
        print("  material.gb_sourced_fields=", m.get("gb_sourced_fields"))
        print("  material.consistency_guard=", m.get("consistency_guard"))
        print("  material_provenance =", json.dumps(d.get("material_provenance"), ensure_ascii=False))
        fr = d.get("fea_result", {})
        print("  fea max_stress  =", fr.get("max_von_mises_mpa") or fr.get("max_stress_mpa"))
        print("  fea max_disp_mm =", fr.get("max_displacement_mm"))
        print("  fea safety_fac  =", fr.get("safety_factor"))
        print("  fea overall     =", fr.get("overall"))
        # find geometry used
        rg = d.get("real_geometry")
        print("  real_geometry   =", json.dumps(rg, ensure_ascii=False)[:400])
