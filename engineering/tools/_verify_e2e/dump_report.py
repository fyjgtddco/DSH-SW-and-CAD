# -*- coding: utf-8 -*-
"""从 optimize 报告 JSON 里抽取关键证据字段。"""
import io, json, sys

p = sys.argv[1]
raw = io.open(p, encoding="utf-8-sig").read()
# 去掉前置的非 JSON 行（进度输出可能混入 stdout）
i = raw.find("{")
j = json.loads(raw[i:])

fr = j.get("final_report") or {}
print("=" * 90)
print("文件:", p)
print("=" * 90)
print("[顶层] ok=%s converged=%s iterations=%s reason=%s"
      % (j.get("ok"), j.get("converged"), j.get("iterations"), j.get("reason")))
print("       run_dir =", j.get("run_dir"))

m = fr.get("material") or {}
print()
print("[报告 material 块]")
for k in ("id", "name", "yield_strength_mpa", "uts_mpa", "youngs_modulus_mpa",
          "poissons_ratio", "density_kg_m3", "standard", "source",
          "values_source", "values_from_gb", "values_pending",
          "identity_source", "equivalent_name", "equivalent_id"):
    print("   %-22s = %s" % (k, m.get(k)))

print()
print("[material_provenance]")
print("  ", json.dumps(fr.get("material_provenance"), ensure_ascii=False))

print()
print("[material_resolution]")
print("  ", json.dumps(fr.get("material_resolution"), ensure_ascii=False))

f = fr.get("fea_result") or {}
print()
print("[fea_result]")
for k in ("ok", "method", "backend", "safety_factor", "max_von_mises_mpa",
          "max_displacement_mm", "volume_mm3", "mass_kg", "overall"):
    print("   %-22s = %s" % (k, f.get(k)))
print("   load_model          =", json.dumps(f.get("load_model"), ensure_ascii=False))
print("   geometry_used       =", json.dumps(f.get("geometry_used"), ensure_ascii=False))
print("   gates               =", json.dumps(f.get("gates"), ensure_ascii=False))

print()
print("[fatigue]")
print("  ", json.dumps(fr.get("fatigue"), ensure_ascii=False)[:1200])

print()
print("[顶层验收]")
for k in ("actual_safety_factor", "required_min_safety_factor",
          "required_max_safety_factor", "overall_status",
          "passed_gates", "failed_gates", "not_evaluated"):
    print("   %-28s = %s" % (k, fr.get(k)))
print("   warnings =", fr.get("warnings"))
print("   release_readiness =", json.dumps(fr.get("release_readiness"), ensure_ascii=False))

print()
print("[geometry_gate] status=%s" % ((fr.get("geometry_gate") or {}).get("status")))
print("   gates =", json.dumps((fr.get("geometry_gate") or {}).get("gates"), ensure_ascii=False))

# 手算 SF 复核
sy = m.get("yield_strength_mpa")
smax = f.get("max_von_mises_mpa")
if sy and smax:
    print()
    print("[手算复核] SF = σy/σmax = %s / %s = %.4f ; 报告 safety_factor = %s"
          % (sy, smax, float(sy) / float(smax), f.get("safety_factor")))
