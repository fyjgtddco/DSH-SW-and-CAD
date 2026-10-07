# -*- coding: utf-8 -*-
"""E4 冒烟测试：确认 swapi 建模+赋材链路可用（单牌号 65Mn）。"""
import json, os, sys
TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4", "bugA_parts")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, TOOLS)
import swapi
G = "65Mn"
PART = os.path.join(OUT, "DSH_smoke_65Mn.SLDPRT")
m = swapi.new_part(material=G)
print("new_part ok, title =", m.title)
m.begin_sketch("Front Plane")
m.polyline([(0, 0), (80, 0), (80, 40), (0, 40)])
m.end_sketch()
print("extrude ->", m.extrude(10))
print("set_material ->", json.dumps(m.set_material(G), ensure_ascii=False, default=str))
print("massprops ->", json.dumps(m.massprops(safe=True), ensure_ascii=False, default=str))
print("save ->", json.dumps(m.save(PART), ensure_ascii=False, default=str))
print("part exists =", os.path.exists(PART))
print("att exists  =", os.path.exists(PART + ".material.json"))
__RESULT__ = {"ok": os.path.exists(PART)}
