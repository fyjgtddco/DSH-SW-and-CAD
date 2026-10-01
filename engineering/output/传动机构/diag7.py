# -*- coding: utf-8 -*-
"""诊断7: 简单2点线 revolve 轴 + Front面键槽 cut"""
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
m.line(0, 15, 120, 15)     # 母线
m.centerline(0, 0, 120, 0) # 轴线
m.end_sketch()
m.revolve(360, expect_cylinder=(30, 120))

# 键槽: Front Plane 草图, 圆柱键槽切向下
m.begin_sketch("Front Plane")
m.rect(60, 11.5, 30, 7)
try:
    m.cut(depth=4, through=False, through_both=True)
    print("KEYWAY_CUT_OK")
except Exception as e:
    print("KEYWAY_CUT_FAIL:", str(e)[:80])
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag7.png"))
print("DONE7")
