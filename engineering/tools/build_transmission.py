# -*- coding: utf-8 -*-
import os, math
import swapi

BASE = os.path.dirname(os.path.abspath(__file__))

# 传动机构: 用旋转(revolve)特征建旋转体, 自带中心孔, 绕开 cut bug
# 关节轴承 GE40(内径40)匹配; 轴/轴承套/腕法兰/电机筒

# ============ 关节轴 (阶梯轴, 总长340) ============
m = swapi.new_part()
m.begin_sketch("Front Plane")
# 旋转轮廓 (绕X轴: x=轴向, y=半径), y>=0
m.polyline([
    (0, 0), (340, 0),        # 轴线
    (340, 18),               # 端轴肩 R18
    (300, 18),
    (300, 20),               # 轴承位 R20 (GE40 内径40 -> 轴径40, 取 R20)
    (40, 20),
    (40, 18),
    (0, 18),                 # 左端轴肩
    (0, 0),
])
m.centerline(0, 0, 340, 0)
m.end_sketch()
m.revolve(360)
m.fillet(2, [(40, 20, 0), (300, 20, 0)])
m.rebuild()
m.set_view_iso()
m.screenshot(os.path.join(BASE, "DSH_关节轴_preview.png"))
res1 = m.save(os.path.join(BASE, "DSH_关节轴.sldprt"))
print("SAVE 关节轴:", res1)

# ============ 腕部法兰 (Φ80, 厚20, 中心孔Φ30) ============
m2 = swapi.new_part()
m2.begin_sketch("Front Plane")
m2.polyline([
    (0, 0), (20, 0),
    (20, 40), (15, 40),
    (15, 0), (0, 0),   # 退化为环? 需闭合环形截面
])
# 上面写法错误, 改用标准环形轮廓:
m2.end_sketch()
m2 = swapi.new_part()
m2.begin_sketch("Front Plane")
m2.circle(0, 0, 40)   # 外 R40
m2.circle(0, 0, 15)   # 内 R15 (腕部孔 Φ30)
m2.end_sketch()
m2.extrude(20)        # 法兰厚20; 这里环形轮廓 extrude 得空心环
m2.rebuild()
m2.set_view_iso()
m2.screenshot(os.path.join(BASE, "DSH_腕法兰_preview.png"))
res2 = m2.save(os.path.join(BASE, "DSH_腕法兰.sldprt"))
print("SAVE 腕法兰:", res2)

# ============ 关节电机壳 (Φ90 筒, 长110, 中心孔Φ40) ============
m3 = swapi.new_part()
m3.begin_sketch("Front Plane")
m3.circle(0, 0, 45)   # 外 R45
m3.circle(0, 0, 20)   # 内 R20 (轴孔 Φ40)
m3.end_sketch()
m3.extrude(110)
m3.fillet(3, [(45, 0, 55), (-45, 0, 55)])
m3.rebuild()
m3.set_view_iso()
m3.screenshot(os.path.join(BASE, "DSH_关节电机壳_preview.png"))
res3 = m3.save(os.path.join(BASE, "DSH_关节电机壳.sldprt"))
print("SAVE 关节电机壳:", res3)
print("TRANSMISSION_DONE")
