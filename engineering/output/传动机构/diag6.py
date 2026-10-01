# -*- coding: utf-8 -*-
"""诊断6: 比较 extrude 体 vs revolve 体 对后续 cut 的影响"""
import os
import swapi

def test(tag, build_fn):
    m = swapi.new_part()
    build_fn(m)
    # 在 Front Plane 开矩形草图并 cut
    m.begin_sketch("Front Plane")
    m.rect(0, 0, 20, 20)
    try:
        m.cut(depth=5, through=False, through_both=True)
        print(tag, "CUT_OK")
    except Exception as e:
        print(tag, "CUT_FAIL:", str(e)[:60])
    m.close()

# A: 纯拉伸方块
test("A_extrude_box", lambda m: (m.begin_sketch("Front Plane"), m.rect(0,0,40,40), m.end_sketch(), m.extrude(20)))
# B: 旋转轴
def build_revolve(m):
    m.begin_sketch("Front Plane")
    m.polyline([(0,0),(0,20),(40,20),(40,0)])
    m.centerline(0,0,40,0)
    m.end_sketch()
    m.revolve(360, expect_cylinder=(40,40))
test("B_revolve", build_revolve)
print("DONE6")
