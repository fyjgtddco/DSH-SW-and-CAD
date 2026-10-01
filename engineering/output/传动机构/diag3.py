# -*- coding: utf-8 -*-
"""诊断3: 切除时保持草图激活(不 end_sketch) 是否可打通"""
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
m.rect(0, 0, 40, 40)
m.end_sketch()
m.extrude(20)

v0 = m._body_volume_mm3()
print("VOL0:", v0)

# 顶面开槽草图, 但不 end_sketch, 直接 cut
m.begin_sketch_on_face(0, 0, 20)
m.rect(0, 0, 20, 20)
# 关键: 不调用 end_sketch, 直接 cut
sk = m.skm.ActiveSketch
print("ACTIVE_SKETCH:", sk is not None)
ok, info = m.select_all_sketch_segments()
print("SELECT_SEG_OK:", ok, "INFO:", info)
try:
    m.cut(depth=10, through=False, through_both=True)
    print("CUT_OK")
except Exception as e:
    print("CUT_ERR:", repr(e))
v1 = m._body_volume_mm3()
print("VOL1:", v1, "REMOVED%:", round((v0 - v1) / v0 * 100, 2))
m.end_sketch()
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag3.png"))
print("DONE3")
