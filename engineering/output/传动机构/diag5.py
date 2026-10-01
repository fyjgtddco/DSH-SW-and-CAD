# -*- coding: utf-8 -*-
"""诊断5: 轴类键槽用 begin_sketch_on_face + 立即 cut"""
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
pts = [(0,0),(0,20),(10,20),(10,15),(110,15),(110,20),(120,20),(120,0)]
m.polyline(pts)
m.centerline(0, 0, 120, 0)
m.end_sketch()
m.revolve(360, expect_cylinder=(40, 120))

# 键槽: 在圆柱 +Y 表面 X45..75 处开方槽
# 圆柱面半径15, 键槽深7 -> 槽在表面下. 用 begin_sketch_on_face
m.begin_sketch_on_face(0, 15, 60)   # (x,y,z) 在 +Y 母线附近
print("ACTIVE:", m.skm.ActiveSketch is not None, "RIGHT_MODE:", m._right_plane_mode, "OFFSET:", m._surface_sketch_offset)
# 在草图里画矩形: X 45..75, 沿圆周长向(草图Y) 宽≈ 弧长. 简化: 矩形宽30(X) 高8(Y)
m.rect(60, 0, 30, 8)
try:
    m.cut(depth=8, through=False, through_both=True)
    print("CUT_OK")
except Exception as e:
    print("CUT_ERR:", repr(e))
v = m._body_volume_mm3()
print("VOL:", v)
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag5.png"))
print("DONE5")
