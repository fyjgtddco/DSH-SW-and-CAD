# -*- coding: utf-8 -*-
"""Step6b: 验证疲劳口径分裂的根因（是否 real_geometry 缺失）"""
import sys, json, io, os
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import physics_bridge as pb

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out2"
CASE = os.path.join(OUT, "case_Q235_fresh.json")
d = json.load(io.open(CASE, encoding="utf-8-sig"))
print("=" * 100)
print("Step6b  根因验证：case 里有没有 real_geometry？")
print("=" * 100)
print("  case['real_geometry'] =", d.get("real_geometry"))
print("  case['design_domain']['bounds'] =", json.dumps(
    d.get("design_domain", {}).get("bounds"), ensure_ascii=False))

print()
print("  --- 无 real_geometry 时 fatigue ---")
r1 = pb.cmd_fatigue(case_path=CASE)
print("     verdict=%s fatigue_sf=%s peak_stress=%s"
      % (r1.get("verdict"), r1.get("fatigue_sf"), r1.get("peak_stress_mpa")))

print()
print("  --- 注入真实几何(100x60x10) 后再 fatigue ---")
d2 = json.loads(json.dumps(d))
d2["real_geometry"] = {
    "volume_mm3": 60000.0,
    "surface_area_mm2": 15200.0,
    "mass_kg": 0.474,
    "density_kg_m3": 7900.0,
    "bbox_mm": [-50.0, 50.0, -30.0, 30.0, 0.0, 10.0],
    "source": "swapi.massprops(真实实体体积)",
}
CASE2 = os.path.join(OUT, "case_Q235_with_geo.json")
io.open(CASE2, "w", encoding="utf-8").write(
    json.dumps(d2, ensure_ascii=False, indent=2))
r2 = pb.cmd_fatigue(case_path=CASE2)
print("     verdict=%s fatigue_sf=%s peak_stress=%s"
      % (r2.get("verdict"), r2.get("fatigue_sf"), r2.get("peak_stress_mpa")))

print()
print("=" * 100)
print("结论")
print("=" * 100)
print("  无真实几何 : sf=%-10s peak=%-10s verdict=%s"
      % (r1.get("fatigue_sf"), r1.get("peak_stress_mpa"), r1.get("verdict")))
print("  有真实几何 : sf=%-10s peak=%-10s verdict=%s"
      % (r2.get("fatigue_sf"), r2.get("peak_stress_mpa"), r2.get("verdict")))
if r1.get("verdict") != r2.get("verdict"):
    print("  >>> 根因确认：独立 fatigue CLI 不注入真实几何 → 退回 design_domain")
    print("      默认几何，结论与 optimize（真实零件）相反。")
    print("      optimize 报告内 sf=0.996 与此处『有真实几何』结果一致 = %s"
          % r2.get("fatigue_sf"))
else:
    print("  >>> 二者一致，疲劳口径分裂假设【不成立】")
