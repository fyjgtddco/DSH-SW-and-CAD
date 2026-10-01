# -*- coding: utf-8 -*-
"""诊断9: 同样 keyway 草图, 分别切 extrude圆柱 与 revolve轴, 看谁成功"""
import os
import swapi

def build_cyl(m):
    m.begin_sketch("Right Plane")   # 法线=X
    m.circle(0, 0, 15)
    m.end_sketch()
    m.extrude(120)                  # 沿X轴 120
    print("CYL built via extrude")

def build_rev(m):
    m.begin_sketch("Front Plane")
    m.line(0, 15, 120, 15)
    m.centerline(0, 0, 120, 0)
    m.end_sketch()
    m.revolve(360, verify=False)
    print("REV built")

for tag, bf in [("CYL", build_cyl), ("REV", build_rev)]:
    m = swapi.new_part()
    bf(m)
    m.begin_sketch("Front Plane")
    m.rect(60, 11.5, 30, 7)
    try:
        m.cut(depth=4, through=False, through_both=True)
        print(tag, "KEYWAY_OK")
    except Exception as e:
        print(tag, "KEYWAY_FAIL:", str(e).split(chr(10))[0][:60])
    m.close()
print("DONE9")
