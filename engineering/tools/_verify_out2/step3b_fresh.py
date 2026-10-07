# -*- coding: utf-8 -*-
"""Step3b: 用【当前】material_db 造新鲜工况 + 看 workflow_gate 会注入什么材料"""
import sys, json, io, os
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb
import workflow_gate as wg

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out2"

GRADES = ["Q235", "45#", "Q355", "65Mn", "GCr15", "HT200", "QT500-7",
          "ZCuSn10P1", "H62", "ZL104", "40Cr", "20CrMnTi", "Cr12MoV",
          "304", "316", "6061-T6", "7075", "2A12"]

print("=" * 100)
print("Step3b  workflow_gate._gb_grade_props() —— 门禁实际会注入 load_case 的材料值")
print("=" * 100)
print("%-12s %-10s %-10s %-9s %-7s %-9s %-8s %s"
      % ("牌号", "E", "σy", "σb", "ν", "ρ", "from_gb", "values_source"))
for g in GRADES:
    try:
        m = wg._gb_grade_props(g)
    except Exception as e:
        print("%-12s [EXCEPTION] %r" % (g, e)); continue
    if not m:
        print("%-12s [门禁返回 None —— 无法注入]" % g); continue
    print("%-12s %-10s %-10s %-9s %-7s %-9s %-8s %s"
          % (g, m.get("youngs_modulus_mpa"), m.get("yield_strength_mpa"),
             m.get("uts_mpa"), m.get("poissons_ratio"), m.get("density_kg_m3"),
             m.get("values_from_gb"), (m.get("values_source") or "")[:60]))

print()
print("=" * 100)
print("Step3c  造【新鲜】工况（用当前 material_db 的 Q235）")
print("=" * 100)
src = json.load(io.open(os.path.join(
    r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out",
    "case_Q235.json"), encoding="utf-8-sig"))
mat = wg._gb_grade_props("Q235")
print("  新注入 material =", json.dumps(mat, ensure_ascii=False, indent=2))
src["material"] = mat
src["meta"]["problem_id"] = "VERIFY_Q235_FRESH"
src["meta"]["generated_at"] = "verify_out2"
fresh = os.path.join(OUT, "case_Q235_fresh.json")
io.open(fresh, "w", encoding="utf-8").write(
    json.dumps(src, ensure_ascii=False, indent=2))
print("  已写出:", fresh)

# GCr15 新鲜工况
src2 = json.load(io.open(os.path.join(
    r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out",
    "case_GCr15.json"), encoding="utf-8-sig"))
mat2 = wg._gb_grade_props("GCr15")
print()
print("  新注入 GCr15 material =", json.dumps(mat2, ensure_ascii=False, indent=2))
if mat2:
    src2["material"] = mat2
    src2["meta"]["problem_id"] = "VERIFY_GCr15_FRESH"
    fresh2 = os.path.join(OUT, "case_GCr15_fresh.json")
    io.open(fresh2, "w", encoding="utf-8").write(
        json.dumps(src2, ensure_ascii=False, indent=2))
    print("  已写出:", fresh2)

print()
print("=" * 100)
print("Step3d  对比：_verify_out 里的旧工况 vs 当前 material_db")
print("=" * 100)
old = json.load(io.open(os.path.join(
    r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out",
    "case_Q235.json"), encoding="utf-8-sig"))["material"]
print("  旧工况(文件) : σy=%s σb=%s values_from_gb=%s values_source=%s"
      % (old.get("yield_strength_mpa"), old.get("uts_mpa"),
         old.get("values_from_gb"), old.get("values_source")))
print("  当前 DB      : σy=%s σb=%s values_from_gb=%s values_source=%s"
      % (mat.get("yield_strength_mpa"), mat.get("uts_mpa"),
         mat.get("values_from_gb"), mat.get("values_source")))
print("  >>> 旧工况文件是【陈旧快照】，未随 gb_materials.json 更新而刷新"
      if old.get("uts_mpa") != mat.get("uts_mpa") else "  >>> 一致")
