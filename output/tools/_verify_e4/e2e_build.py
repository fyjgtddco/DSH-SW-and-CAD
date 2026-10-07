# -*- coding: utf-8 -*-
"""E4 任务二 · ② 设计：SolidWorks 真实建【L 型承重支架】并赋国标材质。

零件：底座 140×12，立臂 12×90（立臂在 x=0..12），宽度 80（z=-40..+40）
包络 140 × 90 × 80 mm，与载荷工况 design_domain 一致。
由 sw_bridge.py run 执行。环境变量 E4_GRADE 指定牌号。
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4")
sys.path.insert(0, TOOLS)
import swapi  # noqa: E402

GRADE = os.environ.get("E4_GRADE", "45#")
_TAG = GRADE.replace("#", "").replace("-", "_").replace("/", "_")
PART = os.path.join(OUT, "E4_承重支架_%s.SLDPRT" % _TAG)
rec = {"grade": GRADE, "part": PART}

m = swapi.new_part(material=GRADE)
rec["new_part_title"] = m.title
m.begin_sketch("Front Plane")
m.polyline([(0, 0), (140, 0), (140, 12), (12, 12), (12, 90), (0, 90)])
m.end_sketch()
rec["extrude"] = m.extrude(80, symmetric=True)
rec["vol_after_extrude"] = m._body_volume_mm3()

# 内角圆角（真实工艺）
try:
    rec["fillet"] = m.fillet(8, [(12, 12, -40), (12, 12, 40)])
except Exception as e:
    rec["fillet_exc"] = repr(e)
rec["vol_after_fillet"] = m._body_volume_mm3()

# 底座安装通孔 Ø12（沿 Z）
rec["bores"] = []
for _cx in (40.0, 120.0):
    try:
        _b = m.bore(dia_mm=12, axis="Z", through=True, cx=_cx, cy=6.0)
        rec["bores"].append({"cx": _cx, "ok": _b.get("ok"), "error": _b.get("error")})
    except Exception as e:
        rec["bores"].append({"cx": _cx, "exc": repr(e)})
    rec["vol_after_bore_%s" % int(_cx)] = m._body_volume_mm3()

# 赋材质（实体已存在 → 真正写入）
rec["set_material"] = m.set_material(GRADE)
rec["material_result"] = getattr(m, "material_result", None)
rec["massprops"] = m.massprops(safe=True)
rec["body_box_mm"] = m.body_box_mm()
rec["get_material"] = m.get_material()

_r = m.save(PART)
if not (_r or {}).get("ok"):
    try:
        m.rebuild()
    except Exception:
        pass
    _r = m.save(PART)
rec["save_ok"] = (_r or {}).get("ok")
rec["save"] = {k: v for k, v in (_r or {}).items() if k != "material"}
rec["part_exists"] = os.path.exists(PART)

_ap = PART + ".material.json"
if os.path.exists(_ap):
    rec["attestation"] = json.load(open(_ap, "r", encoding="utf-8"))

json.dump(rec, open(os.path.join(OUT, "e2e_build_%s.json" % _TAG), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("PART =", PART, "| exists =", rec["part_exists"])
print("volume =", rec.get("vol_after_bore_120"), "| bbox =", rec.get("body_box_mm"))
print("massprops density =", (rec.get("massprops") or {}).get("density_kg_m3"))
print("set_material ok =", (rec.get("material_result") or {}).get("ok"))
__RESULT__ = {"ok": rec["part_exists"], "part": PART, "grade": GRADE}
