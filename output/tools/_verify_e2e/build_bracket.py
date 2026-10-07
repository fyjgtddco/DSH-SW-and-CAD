# -*- coding: utf-8 -*-
"""阶段1② 设计：SolidWorks 真实建【L 型承重支架】并赋国标材质。

零件：底座 140×12，立臂 12×90（立臂在 x=0..12），宽度 80（z=-40..+40）
包络 140 × 90 × 80 mm，与载荷工况 design_domain 一致。

由 sw_bridge.py run 执行。
"""
import os, sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
import swapi

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e2e"
GRADE = os.environ.get("E2E_GRADE", "45#")
_TAG = GRADE.replace("#", "").replace("-", "_").replace("/", "_")
PART = os.path.join(OUT, "DSH_承重支架_%s.SLDPRT" % _TAG)

print("=" * 70)
print("STEP1  new_part(material=%r)" % GRADE)
m = swapi.new_part(material=GRADE)
print("  title           =", m.title)
print("  pending_material=", getattr(m, "_pending_material", None))
print("  material_source =", getattr(m, "_material_source", None))

print("=" * 70)
print("STEP2  Front Plane 画 L 形轮廓 -> 对称拉伸 80mm（宽）")
m.begin_sketch("Front Plane")
m.polyline([(0, 0), (140, 0), (140, 12), (12, 12), (12, 90), (0, 90)])
m.end_sketch()
_r = m.extrude(80, symmetric=True)
print("  extrude ->", _r)
_v1 = m._body_volume_mm3()
print("  拉伸后体积 =", _v1, "mm^3  (理论 L 面积 2616 × 80 = 209280)")

print("=" * 70)
print("STEP3  内角 R8 圆角")
try:
    _f = m.fillet(8, [(12, 12, -40), (12, 12, 40)])
    print("  fillet ->", _f)
except Exception as _e:
    print("  fillet EXC:", repr(_e))
_v2 = m._body_volume_mm3()
print("  圆角后体积 =", _v2)

print("=" * 70)
print("STEP4  底座两个 Ø12 安装通孔（沿 Z，x=120）")
for _cx in (40.0, 120.0):
    try:
        _b = m.bore(dia_mm=12, axis="Z", through=True, cx=_cx, cy=6.0)
        print("  bore cx=%.0f -> ok=%s feat=%s err=%s"
              % (_cx, _b.get("ok"), bool(_b.get("feature")), _b.get("error")))
    except Exception as _e:
        print("  bore cx=%.0f EXC: %r" % (_cx, _e))
    print("     体积 =", m._body_volume_mm3())

print("=" * 70)
print("STEP5  赋材质（实体已存在，真正写入）")
_sm = m.set_material(GRADE)
print("  set_material ->", json.dumps(_sm, ensure_ascii=False, default=str))
print("  material_result =", json.dumps(getattr(m, "material_result", None),
                                      ensure_ascii=False, default=str))

print("=" * 70)
print("STEP6  读取真实几何/质量属性")
_mp = m.massprops(safe=True)
print("  massprops ->", json.dumps(_mp, ensure_ascii=False, default=str))
_bb = m.body_box_mm()
print("  body_box_mm ->", _bb)
_gm = m.get_material()
print("  get_material ->", json.dumps(_gm, ensure_ascii=False, default=str))

print("=" * 70)
print("STEP7  保存")
_res = m.save(PART)
if not (_res or {}).get("ok"):
    print("  第1次保存失败:", (_res or {}).get("error"), "-> 重建后重试")
    try:
        m.rebuild()
    except Exception as _e:
        print("  rebuild EXC:", repr(_e))
    _res = m.save(PART)
print("  save ->", json.dumps(_res, ensure_ascii=False, default=str))
print("  exists =", os.path.exists(PART))

try:
    m.set_view_iso()
except Exception as _e:
    print("  set_view_iso EXC:", repr(_e))

__RESULT__ = {
    "ok": bool(os.path.exists(PART)),
    "part": PART,
    "grade": GRADE,
    "volume_after_extrude": _v1,
    "volume_after_fillet": _v2,
    "massprops": _mp,
    "body_box_mm": _bb,
    "get_material": _gm,
    "set_material": _sm,
    "material_result": getattr(m, "material_result", None),
    "save": _res,
}
