# -*- coding: utf-8 -*-
"""E4：验证 sw_bridge run 的【防线①材料】是否在产物声明后阻断。

对同一零件做两次：
  A) __RESULT__ 不含 artifacts  —— 看防线是否触发
  B) __RESULT__ 含 artifacts    —— 看防线是否触发
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4")
sys.path.insert(0, TOOLS)
import swapi  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "A"
G = "65Mn"          # 已知赋材失败的牌号
PART = os.path.join(OUT, "defense_probe_%s.SLDPRT" % MODE)

m = swapi.new_part(material=G)
m.begin_sketch("Front Plane")
m.polyline([(0, 0), (60, 0), (60, 30), (0, 30)])
m.end_sketch()
m.extrude(8)
m.set_material(G)
m.save(PART)
print("saved:", os.path.exists(PART))
print("mode:", MODE)

if MODE == "A":
    __RESULT__ = {"ok": True, "part": PART}          # 不声明 artifacts
else:
    __RESULT__ = {"ok": True, "part": PART, "artifacts": [PART]}   # 声明 artifacts
