# -*- coding: utf-8 -*-
import os
import swapi

BASE = os.path.dirname(os.path.abspath(__file__))

# BUG绕行: swapi.cut() 同一零件第2次调用必失败 -> 每个零件只调用1次cut。
# 关节通轴孔在臂中心轴线上, 故一次贯穿(从近端 z=0 面切)即可打通近端+远端。

# ============ 大臂 (实心箱梁 320mm, 70x80) ============
m = swapi.new_part()
m.begin_sketch("Front Plane")
m.polyline([(-35, -40), (35, -40), (35, 40), (-35, 40), (-35, -40)])
m.end_sketch()
m.extrude(320)
# 中心通轴孔 Φ40：从近端 z=0 面一次贯穿打穿两端
m.begin_sketch_on_face(0, 0, 0)
m.circle(0, 0, 20)
m.cut(through=True)
m.fillet(6, [(35, 40, 160), (-35, 40, 160), (35, -40, 160), (-35, -40, 160)])
m.rebuild()
m.set_view_iso()
m.screenshot(os.path.join(BASE, "DSH_大臂_preview.png"))
res1 = m.save(os.path.join(BASE, "DSH_大臂.sldprt"))
print("SAVE 大臂:", res1)

# ============ 小臂 (实心箱梁 260mm, 56x64) ============
m2 = swapi.new_part()
m2.begin_sketch("Front Plane")
m2.polyline([(-28, -32), (28, -32), (28, 32), (-28, 32), (-28, -32)])
m2.end_sketch()
m2.extrude(260)
# 中心通轴孔 Φ40：近端 z=0 一次贯穿
m2.begin_sketch_on_face(0, 0, 0)
m2.circle(0, 0, 20)
m2.cut(through=True)
m2.fillet(5, [(28, 32, 130), (-28, 32, 130), (28, -32, 130), (-28, -32, 130)])
m2.rebuild()
m2.set_view_iso()
m2.screenshot(os.path.join(BASE, "DSH_小臂_preview.png"))
res2 = m2.save(os.path.join(BASE, "DSH_小臂.sldprt"))
print("SAVE 小臂:", res2)
print("STRUCTURAL_DONE")
