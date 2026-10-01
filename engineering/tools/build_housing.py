# -*- coding: utf-8 -*-
import os, math
import swapi

BASE = os.path.dirname(os.path.abspath(__file__))

# 壳体机架: 底座法兰 + 底座立柱 + 回转台 + 末端夹爪
# 避开 revolve/cut 选中bug: 全部用 "双圆 extrude 空心盘/筒" (已验证可靠)

# ============ 底座法兰盘 (Φ260外 Φ50内, 厚40) ============
m = swapi.new_part()
m.begin_sketch("Front Plane")
m.circle(0, 0, 130)   # 外 R130
m.circle(0, 0, 25)    # 内 R25 (Φ50 中心回转孔)
m.end_sketch()
m.extrude(40)
m.fillet(4, [(130, 0, 20), (-130, 0, 20)])
m.rebuild()
m.set_view_iso()
m.screenshot(os.path.join(BASE, "DSH_底座法兰_preview.png"))
res1 = m.save(os.path.join(BASE, "DSH_底座法兰.sldprt"))
print("SAVE 底座法兰:", res1)

# ============ 底座立柱 (Φ100外 Φ50内, 高170) ============
m1 = swapi.new_part()
m1.begin_sketch("Front Plane")
m1.circle(0, 0, 50)   # 外 R50
m1.circle(0, 0, 25)   # 内 R25
m1.end_sketch()
m1.extrude(170)
m1.fillet(3, [(50, 0, 85), (-50, 0, 85)])
m1.rebuild()
m1.set_view_iso()
m1.screenshot(os.path.join(BASE, "DSH_底座立柱_preview.png"))
res1b = m1.save(os.path.join(BASE, "DSH_底座立柱.sldprt"))
print("SAVE 底座立柱:", res1b)

# ============ 回转台 (J1 偏航盘 Φ120, 厚30, 中心孔Φ50) ============
m2 = swapi.new_part()
m2.begin_sketch("Front Plane")
m2.circle(0, 0, 60)   # 外 R60
m2.circle(0, 0, 25)   # 内 R25 (Φ50)
m2.end_sketch()
m2.extrude(30)
m2.rebuild()
m2.set_view_iso()
m2.screenshot(os.path.join(BASE, "DSH_回转台_preview.png"))
res2 = m2.save(os.path.join(BASE, "DSH_回转台.sldprt"))
print("SAVE 回转台:", res2)

# ============ 末端二指夹爪 ============
m3 = swapi.new_part()
m3.begin_sketch("Front Plane")
m3.rect(0, 0, 60, 40)
m3.end_sketch()
m3.extrude(20)
m3.begin_sketch_on_face(0, 0, 20)
m3.rect(-24, 0, 12, 30)   # 左指
m3.end_sketch()
m3.extrude(30)
m3.begin_sketch_on_face(0, 0, 20)
m3.rect(12, 0, 12, 30)    # 右指
m3.end_sketch()
m3.extrude(30)
m3.rebuild()
m3.set_view_iso()
m3.screenshot(os.path.join(BASE, "DSH_夹爪_preview.png"))
res3 = m3.save(os.path.join(BASE, "DSH_夹爪.sldprt"))
print("SAVE 夹爪:", res3)
print("HOUSING_DONE")
