# -*- coding: utf-8 -*-
"""E4：失败牌号的可绕过路径验证 —— 显式 density 兜底 / save_custom_material。

用法: python workaround_one.py <牌号>
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4", "workaround")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import swapi  # noqa: E402
import material_db as M  # noqa: E402

G = sys.argv[1]
_tag = G.replace("#", "").replace("-", "_").replace("/", "_")
_m = M.get_material(G) or {}
_rho = _m.get("density_kg_m3")
rec = {"grade": G, "expected_density": _rho}

# ── 路线 ①：显式 density 兜底 ────────────────────────────────────
P1 = os.path.join(OUT, "A_%s.SLDPRT" % _tag)
try:
    m = swapi.new_part(material=G)
    m.begin_sketch("Front Plane")
    m.polyline([(0, 0), (60, 0), (60, 30), (0, 30)])
    m.end_sketch()
    m.extrude(8)
    rec["A_set_material_density"] = m.set_material(G, density=_rho)
    rec["A_massprops"] = m.massprops(safe=True)
    rec["A_material_result"] = getattr(m, "material_result", None)
    m.save(P1)
    rec["A_saved"] = os.path.exists(P1)
    _ap = P1 + ".material.json"
    if os.path.exists(_ap):
        _a = json.load(open(_ap, "r", encoding="utf-8"))
        rec["A_att"] = {k: _a.get(k) for k in
                        ("ok", "density_kg_m3", "expected_density_kg_m3",
                         "attested_density_kg_m3", "cross_standard", "applied_name")}
except Exception as e:
    rec["A_exc"] = repr(e)

# ── 路线 ②：先登记自定义材料再赋材 ────────────────────────────────
P2 = os.path.join(OUT, "B_%s.SLDPRT" % _tag)
try:
    rec["B_save_custom"] = swapi.save_custom_material(
        G, {"density": _rho, "yield_mpa": _m.get("yield_mpa"),
            "uts_mpa": _m.get("uts_mpa"), "e_mpa": _m.get("E_mpa"),
            "poissons_ratio": _m.get("nu")})
    m2 = swapi.new_part(material=G)
    m2.begin_sketch("Front Plane")
    m2.polyline([(0, 0), (60, 0), (60, 30), (0, 30)])
    m2.end_sketch()
    m2.extrude(8)
    rec["B_set_material"] = m2.set_material(G)
    rec["B_massprops"] = m2.massprops(safe=True)
    rec["B_material_result"] = getattr(m2, "material_result", None)
    m2.save(P2)
    rec["B_saved"] = os.path.exists(P2)
    _ap2 = P2 + ".material.json"
    if os.path.exists(_ap2):
        _b = json.load(open(_ap2, "r", encoding="utf-8"))
        rec["B_att"] = {k: _b.get(k) for k in
                        ("ok", "density_kg_m3", "expected_density_kg_m3",
                         "attested_density_kg_m3", "cross_standard", "applied_name")}
except Exception as e:
    rec["B_exc"] = repr(e)

json.dump(rec, open(os.path.join(OUT, "W_%s.json" % _tag), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("A(显式density) massprops密度 =", (rec.get("A_massprops") or {}).get("density_kg_m3"),
      "| set_ok =", (rec.get("A_material_result") or {}).get("ok"))
print("B(自定义材料) massprops密度 =", (rec.get("B_massprops") or {}).get("density_kg_m3"),
      "| set_ok =", (rec.get("B_material_result") or {}).get("ok"))
print("A_att =", json.dumps(rec.get("A_att"), ensure_ascii=False))
print("B_att =", json.dumps(rec.get("B_att"), ensure_ascii=False))
__RESULT__ = {"grade": G}
