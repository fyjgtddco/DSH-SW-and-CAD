# -*- coding: utf-8 -*-
"""
示例3：杯子（拉伸外壁 + 切除内腔 + 杯口倒角）
==============================================
设计（单位 mm）:
  杯身外径 D80, 高 100
  内腔直径 D70, 深 95（杯底厚 5）
  杯口内外倒角 C2

执行: python sw_bridge.py run examples\build_cup.py
（或在 DSH 里说："建模一个马克杯，外径 80、高 100、壁厚 3、底部厚 4，
  保存为 DSH_杯子"）

本示例完全通用：不依赖任何本机路径，保存到脚本所在目录。
"""
import os
import swapi

D_OUT = 80.0    # 杯身外径
H = 100.0       # 杯高
D_IN = 70.0     # 内腔直径
BOTTOM = 5.0    # 杯底厚
DEPTH = H - BOTTOM  # 内腔深度 95

m = swapi.new_part()
print("STEP1 new part:", m.title)

# ---- 2. 杯身外圆柱 D80×100 ----
m.begin_sketch("Front Plane")
m.circle(0, 0, D_OUT / 2.0)
m.end_sketch()
m.extrude(H)
print("STEP2 cup body D%.0f x %.0f OK" % (D_OUT, H))

# ---- 3. 切除内腔 D70×95（顶面画圆，向下切 95，留底 5）----
m.begin_sketch_on_face(0, 0, H)
m.circle(0, 0, D_IN / 2.0)
m.end_sketch()
m.cut(depth=DEPTH)
print("STEP3 cavity D%.0f x %.0f OK" % (D_IN, DEPTH))

# ---- 4. 杯口外沿倒角 C2（顶面外圆棱边 z=H）----
# 顶面外棱边: 半径 40 的圆, 取 y 方向极值点
m.clear_selection()
c1 = m.chamfer(2, [(0, D_OUT / 2.0, H)], angle_deg=45)
print("STEP4 rim outer chamfer C2 ->", "OK" if c1 is not None else "FAIL")

# ---- 5. 杯口内沿倒角 C2（顶面内圆棱边 z=H）----
m.clear_selection()
c2 = m.chamfer(2, [(0, D_IN / 2.0, H)], angle_deg=45)
print("STEP5 rim inner chamfer C2 ->", "OK" if c2 is not None else "FAIL")

# ---- 6. 保存（按用户提供的名称命名；不校验）----
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "DSH_杯子.sldprt")
print("STEP6 save:", m.save(out))
