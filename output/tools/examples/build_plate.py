# -*- coding: utf-8 -*-
"""
示例1：带孔板（拉伸 + 圆孔贯穿切除 + 圆角 + 倒角）
====================================================
设计需求: 120×80×10mm 底座板，中心 D20 通孔，四条竖直棱边 R5 圆角，
顶面外边 C1 倒角。

执行: python sw_bridge.py run examples\build_plate.py
（或在 DSH 里说："用 SolidWorks 建模一个 120×80×10 的板，中心直径 20 的
  通孔，四条竖直棱边倒圆角 R5，保存为 DSH_带孔板"）

本示例完全通用：不依赖任何本机路径，保存到脚本所在目录。
"""
import os

# sw_bridge 的 run 会把包目录加入 sys.path，因此可 import swapi
import swapi

# ---- 1. 新建零件（自动探测模板）----
m = swapi.new_part()
print("STEP1 new part:", m.title)

# ---- 2. 前视基准面画 120x80 矩形，拉伸 10mm ----
m.begin_sketch("Front Plane")
m.rect(0, 0, 120, 80)
m.end_sketch()
m.extrude(10)
print("STEP2 extrude 120x80x10 OK")

# ---- 3. 顶面 (z=10mm) 画 D20 圆，完全贯穿切除 ----
m.begin_sketch_on_face(0, 0, 10)
m.circle(0, 0, 10)
m.end_sketch()
m.cut(through=True)
print("STEP3 cut D20 through OK")

# ---- 4. 四条竖直棱边倒圆角 R5 ----
# 竖直棱边: x=±60, y=±40, z 从 0 到 10；取棱边中点 z=5
corners = [
    (60, 40, 5), (-60, 40, 5), (-60, -40, 5), (60, -40, 5),
]
m.clear_selection()
r = m.fillet(5, corners)
print("STEP4 fillet R5 ->", "OK" if r is not None else "FAIL")

# ---- 5. 顶面四条外边倒角 C1 ----
# 顶面边: z=10 处的四条边，取各边中点
top_edges = [
    (0, 40, 10), (0, -40, 10), (60, 0, 10), (-60, 0, 10),
]
m.clear_selection()
c = m.chamfer(1, top_edges, angle_deg=45)
print("STEP5 chamfer C1 ->", "OK" if c is not None else "FAIL")

# ---- 6. 保存（不校验；用户约定：建模后跳过 massprops 检验）----
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "DSH_带孔板.sldprt")
print("STEP6 save:", m.save(out))
