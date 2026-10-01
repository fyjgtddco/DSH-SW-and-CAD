# -*- coding: utf-8 -*-
"""诊断4: 复现 build 流程(revolve + Front平面键槽) 选活动草图问题"""
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
pts = [(0,0),(0,20),(10,20),(10,15),(110,15),(110,20),(120,20),(120,0)]
m.polyline(pts)
m.centerline(0, 0, 120, 0)
m.end_sketch()
m.revolve(360, expect_cylinder=(40, 120))
print("AFTER_REVOLVE active:", m.skm.ActiveSketch is not None)

m.begin_sketch("Front Plane")
print("AFTER_BEGIN active:", m.skm.ActiveSketch is not None)
m.rect(60, 11.5, 30, 7)
print("AFTER_RECT active:", m.skm.ActiveSketch is not None)
ok, info = m.select_all_sketch_segments()
print("SELECT_SEG_OK:", ok, info)
try:
    m.cut(depth=4, through=False, through_both=True)
    print("CUT_OK")
except Exception as e:
    print("CUT_ERR:", repr(e))
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag4.png"))
print("DONE4")
