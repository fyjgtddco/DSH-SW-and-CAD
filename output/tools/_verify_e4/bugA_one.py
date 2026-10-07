# -*- coding: utf-8 -*-
"""E4 BUG-A：单牌号赋材 + 落盘，输出精简 JSON（避免超长日志）。

用法: python bugA_one.py <牌号>
由 sw_bridge.py run 执行（每个牌号独立一次，避免 SW COM 不稳定）。
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4", "bugA_parts")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, TOOLS)
import swapi  # noqa: E402

G = sys.argv[1] if len(sys.argv) > 1 else "65Mn"
_tag = G.replace("#", "").replace("-", "_").replace("/", "_")
PART = os.path.join(OUT, "DSH_bugA_%s.SLDPRT" % _tag)
rec = {"grade": G, "part": PART}

m = swapi.new_part(material=G)
m.begin_sketch("Front Plane")
m.polyline([(0, 0), (80, 0), (80, 40), (0, 40)])
m.end_sketch()
rec["extrude"] = m.extrude(10)
_sm = m.set_material(G)
rec["set_material"] = _sm
rec["material_result"] = getattr(m, "material_result", None)
rec["massprops"] = m.massprops(safe=True)
_res = m.save(PART)
if not (_res or {}).get("ok"):
    try:
        m.rebuild()
    except Exception:
        pass
    _res = m.save(PART)
# 精简 save（去掉超大字段）
rec["save"] = {k: v for k, v in (_res or {}).items()
               if k not in ("material",)}
rec["save_material_summary"] = {
    "ok": ((_res or {}).get("material") or {}).get("ok"),
    "error": ((_res or {}).get("material") or {}).get("error"),
    "density_before_kg_m3": ((_res or {}).get("material") or {}).get("density_before_kg_m3"),
    "expected_density_kg_m3": ((_res or {}).get("material") or {}).get("expected_density_kg_m3"),
}
rec["part_exists"] = os.path.exists(PART)

# ── 独立读盘：凭据 ──────────────────────────────────────────────
_ap = PART + ".material.json"
rec["attestation_exists"] = os.path.exists(_ap)
if os.path.exists(_ap):
    with open(_ap, "r", encoding="utf-8") as f:
        rec["attestation"] = json.load(f)

_out = os.path.join(TOOLS, "_verify_e4", "bugA_one_%s.json" % _tag)
with open(_out, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=1, default=str)
print("WROTE", _out)
print("massprops density =", (rec.get("massprops") or {}).get("density_kg_m3"))
print("att ok/density/expected =",
      (rec.get("attestation") or {}).get("ok"),
      (rec.get("attestation") or {}).get("density_kg_m3"),
      (rec.get("attestation") or {}).get("expected_density_kg_m3"))
__RESULT__ = {"ok": rec["part_exists"], "out": _out, "grade": G}
