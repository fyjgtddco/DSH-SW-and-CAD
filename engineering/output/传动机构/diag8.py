# -*- coding: utf-8 -*-
"""诊断8: revolve verify=False + Front面键槽cut"""
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
m.line(0, 15, 120, 15)
m.centerline(0, 0, 120, 0)
m.end_sketch()
m.revolve(360, verify=False)
print("REVOLVE_DONE")

m.begin_sketch("Front Plane")
m.rect(60, 11.5, 30, 7)
try:
    m.cut(depth=4, through=False, through_both=True)
    print("KEYWAY_CUT_OK")
except Exception as e:
    print("KEYWAY_CUT_FAIL:", str(e)[:80])
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag8.png"))
print("DONE8")
