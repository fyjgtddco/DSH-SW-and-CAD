# -*- coding: utf-8 -*-
"""第4步：在 SolidWorks 里真实建一个 100×60×10mm 板，赋材质 Q235，保存。

由 sw_bridge.py run 执行（注入 sw 全局对象；同目录 swapi 可 import）。
"""
import os
import swapi

OUT_DIR = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_out"
PART = os.path.join(OUT_DIR, "DSH_验证板_100x60x10.SLDPRT")

print("STEP1 新建零件 + 赋材 Q235 ...")
m = swapi.new_part(material="Q235")
print("  new_part title =", m.title)
print("  material_result =", getattr(m, "material_result", None))

print("STEP2 前视基准面 100x60 矩形，拉伸 10mm ...")
m.begin_sketch("Front Plane")
m.rect(0, 0, 100, 60)
m.end_sketch()
_r = m.extrude(10)
print("  extrude ->", _r)

print("STEP3 赋材质 Q235（实体已存在，可真正写入）...")
_sm = m.set_material("Q235")
print("  set_material ->", _sm)

print("STEP4 读取真实几何/质量属性 ...")
_mp = m.massprops(safe=True)
print("  massprops ->", _mp)
_bb = None
try:
    _bb = m.body_box_mm()
except Exception as _e:
    print("  body_box_mm err:", repr(_e))
print("  body_box_mm ->", _bb)
_gm = m.get_material()
print("  get_material ->", _gm)

print("STEP5 保存 ...")
_res = m.save(PART)
print("  save ->", _res)
print("  exists =", os.path.exists(PART))

__RESULT__ = {
    "ok": bool(os.path.exists(PART)),
    "part": PART,
    "massprops": _mp,
    "body_box_mm": _bb,
    "get_material": _gm,
    "set_material": _sm,
    "save": _res,
}
