# -*- coding: utf-8 -*-
"""E4 任务三.3：安全系数手算复核 + BUG-C 量化（bbox 等效实心梁 vs 真实 L 形件）。"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
D = os.path.join(TOOLS, "_verify_e4")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
from mesh_adapter import analytical_beam_cantilever  # noqa: E402

p = os.path.join(D, "opt_45_out.json")
t = open(p, encoding="utf-8").read()
j, _ = json.JSONDecoder().raw_decode(t[t.find("{"):])
fr = j["final_report"]
fea = fr["fea_result"]
gu = fea["geometry_used"]
rg = fr["real_geometry"]
mat = fr["material"]

sy = mat["yield_strength_mpa"]
smax = fea["max_von_mises_mpa"]
sf = fea["safety_factor"]

print("=" * 84)
print("1) 安全系数手算复核")
print("=" * 84)
print("   报告 σy (材料屈服)      = %s MPa" % sy)
print("   报告 σmax (最大 von Mises) = %s MPa" % smax)
print("   报告 SF                 = %s" % sf)
print("   手算 σy / σmax          = %.4f" % (sy / smax))
print("   绝对误差                = %.6f" % abs(sy / smax - sf))
print("   => %s" % ("一致（SF 就是 σy/σmax）" if abs(sy / smax - sf) < 0.02
                     else "★不一致"))

print("\n" + "=" * 84)
print("2) BUG-C 量化：等效实心悬臂梁 vs 真实 L 形件")
print("=" * 84)
L, W, H = gu["L_mm"], gu["W_mm"], gu["H_mm"]
print("   geometry_used.source = %s" % gu["source"])
print("   geometry_used.warning = %s" % gu["warning"])
print("   等效梁尺寸 L×W×H = %s × %s × %s mm" % (L, W, H))
_v_bbox = L * W * H
_v_real = rg["volume_mm3"]
print("   bbox 实心体积  = %.0f mm³" % _v_bbox)
print("   真实零件体积   = %.0f mm³" % _v_real)
print("   => bbox 高估体积 %.2f 倍（L 形件不是实心块）" % (_v_bbox / _v_real))
print("   真实零件体积占 bbox 比例 = %.1f%%" % (_v_real / _v_bbox * 100))

# 用同一载荷重算解析解
lm = fea.get("load_model") or {}
print("\n   load_model = %s" % json.dumps(lm, ensure_ascii=False))
_F = lm.get("design_force_n")
print("\n   设计载荷 design_force_n = %s N" % _F)
_r = analytical_beam_cantilever(L, W, H, float(_F), mat["youngs_modulus_mpa"])
print("   解析等效梁: σmax=%.3f MPa  δmax=%.5f mm  V=%.0f mm³"
      % (_r["max_stress_mpa"], _r["max_deflection_mm"], _r["volume_mm3"]))
print("   解析等效梁 SF = %.2f" % (sy / _r["max_stress_mpa"]))

print("\n   ── 真实 L 形件的【粗略】手算（悬臂，固定端在 x=140，载荷在立臂顶）──")
# L 形：底座 140x12，立臂 12x90（宽 80）。等效：以立臂为悬臂梁，长 90，截面 80x12
_L2, _b2, _h2 = 90.0, 80.0, 12.0
_r2 = analytical_beam_cantilever(_L2, _b2, _h2, float(_F), mat["youngs_modulus_mpa"])
print("   立臂 90 长、截面 80(宽)×12(厚): σmax=%.3f MPa  SF=%.2f"
      % (_r2["max_stress_mpa"], sy / _r2["max_stress_mpa"]))
print("   => 与工具 bbox 法 (SF=%.2f) 之比 = %.2f 倍"
      % (sf, sf / (sy / _r2["max_stress_mpa"])))
print("\n   ⚠️ 说明：上面两种手算都是【等效梁】口径，都不是 L 形件的真实解；")
print("      真实 L 形件需 3D FEA。此处仅用于量化『bbox 等效』的偏差量级。")

print("\n" + "=" * 84)
print("3) 报告是否【明确说明】了 bbox 等效实心梁这一局限？")
print("=" * 84)
_lims = fea.get("limitations") or []
print("   fea_result.limitations 共 %d 条：" % len(_lims))
for n, l in enumerate(_lims, 1):
    print("     %d) %s" % (n, l))
_kw = ["bbox", "包围盒", "等效", "实心", "悬臂", "cantilever", "solid"]
_hit = [k for k in _kw if any(k.lower() in str(l).lower() for l in _lims)]
print("\n   命中关键词: %s" % (_hit or "★无 —— limitations 里【没有】说明按 bbox 等效实心梁估算"))
print("   geometry_used 里是否有说明: source=%r warning=%r" % (gu["source"], gu["warning"]))
print("   全文出现 '等效' 次数 = %d ；'悬臂' 次数 = %d ；'实心' 次数 = %d"
      % (t.count("等效"), t.count("悬臂"), t.count("实心")))
