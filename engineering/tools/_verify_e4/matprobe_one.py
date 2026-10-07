# -*- coding: utf-8 -*-
"""E4：SW 赋材可用性矩阵 —— 找出哪些 GB 牌号能真正赋上（密度正确）。

对每个牌号：new_part → 建模 → set_material → 读回 get_material + massprops。
每个牌号用【新的 SW 会话】避免 COM 状态污染（由外层 PowerShell 逐次调用）。
用法: python matprobe_one.py <牌号>
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4", "matprobe")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, TOOLS)
import swapi  # noqa: E402

G = sys.argv[1]
_tag = G.replace("#", "").replace("-", "_").replace("/", "_")
PART = os.path.join(OUT, "P_%s.SLDPRT" % _tag)
rec = {"grade": G}
try:
    m = swapi.new_part(material=G)
    m.begin_sketch("Front Plane")
    m.polyline([(0, 0), (60, 0), (60, 30), (0, 30)])
    m.end_sketch()
    m.extrude(8)
    rec["set_material"] = m.set_material(G)
    rec["material_result"] = getattr(m, "material_result", None)
    rec["get_material"] = m.get_material()
    rec["massprops"] = m.massprops(safe=True)
    _r = m.save(PART)
    rec["save_ok"] = (_r or {}).get("ok")
except Exception as e:
    rec["exception"] = repr(e)
rec["part_exists"] = os.path.exists(PART)
_ap = PART + ".material.json"
if os.path.exists(_ap):
    rec["attestation"] = json.load(open(_ap, "r", encoding="utf-8"))

_out = os.path.join(OUT, "R_%s.json" % _tag)
json.dump(rec, open(_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
_mr = rec.get("material_result") or {}
_mp = rec.get("massprops") or {}
print("RESULT %s | set_ok=%s applied=%s density=%s expected=%s"
      % (G, _mr.get("ok"), _mr.get("applied_name"),
         _mp.get("density_kg_m3"), _mr.get("expected_density_kg_m3")))
__RESULT__ = {"grade": G, "set_ok": _mr.get("ok"),
              "density": _mp.get("density_kg_m3"),
              "applied": _mr.get("applied_name")}
