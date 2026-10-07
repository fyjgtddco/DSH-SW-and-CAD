# -*- coding: utf-8 -*-
"""
示例2：五段阶梯轴（旋转成型 + 倒角 + 键槽）—— 教学重点
========================================================
设计需求（单位 mm，以左端面 x=0 为基准，轴沿 +X 方向）:
  轴段1: Φ30 × 30     (x: 0 ~ 30)
  轴段2: Φ36 × 6      (x: 30 ~ 36)
  轴段3: Φ46.494 × 40 (x: 36 ~ 76)，两端开倒角 C1×45°
  轴段4: Φ35 × 74     (x: 76 ~ 150)
  轴段5: Φ30 × 36     (x: 150 ~ 186)
  总长 = 186 mm

附加特征:
  A. 轴最左端 (x=0) 倒角 C1×45°
  B. 轴最右端 (x=186) 倒角 C1×45°
  C. 第四段右端 (x=150) 台阶倒角 C1×45°
  D. 第四段右端往左 20mm 处 (中心 x=130) 开键槽:
       直槽口 总长 35、宽 10（半圆半径 5）
       深度: 键槽底面到过轴心的平行平面距离 12.5

【通用版改动】选中键槽草图不再写死中文名"草图2"，
改用 swapi.select_sketch_by_index 按特征树序号选择（与语言无关）。

执行: python sw_bridge.py run examples\build_axis.py
（或在 DSH 里说："建模一个五段阶梯轴，Φ30×30 / Φ36×6 / Φ46.494×40 /
  Φ35×74 / Φ30×36，总长186，第四段右侧开键槽宽10圆心距25、
  右侧圆心距右端面7.5，保存为 DSH_五段阶梯轴"）
"""
import os
import math

import win32com.client
import pythoncom

import swapi

# ---- 半径表（直径/2）----
R1 = 30 / 2.0      # 15
R2 = 36 / 2.0      # 18
R3 = 46.494 / 2.0  # 23.247
R4 = 35 / 2.0      # 17.5
R5 = 30 / 2.0      # 15

X1, X2, X3, X4, X5 = 30, 36, 76, 150, 186

# ---- 1. 新建零件 ----
m = swapi.new_part()
print("STEP1 new part:", m.title)

# ---- 2. 前视基准面：画旋转轮廓 + 中心线 ----
m.begin_sketch("Front Plane")
m.polyline([
    (0, R1), (X1, R1), (X1, R2), (X2, R2), (X2, R3), (X3, R3),
    (X3, R4), (X4, R4), (X4, R5), (X5, R5), (X5, 0), (0, 0),
])
m.line(0, 0, 0, R1)
m.centerline(-20, 0, 206, 0)
m.end_sketch()

# ---- 3. 旋转 360° 成阶梯轴 ----
r = m.revolve(360)
print("STEP2 revolve ->", "OK" if r is not None else "FAIL")
if r is None:
    raise RuntimeError("revolve failed")

# ---- 4. 轴段3 两端圆环棱边倒角 C1×45° ----
m.clear_selection()
c1 = m.chamfer(1, [(X2, R3, 0)], angle_deg=45)
print("STEP3 chamfer seg3 left  C1 ->", "OK" if c1 is not None else "FAIL")
m.clear_selection()
c2 = m.chamfer(1, [(X3, R3, 0)], angle_deg=45)
print("STEP4 chamfer seg3 right C1 ->", "OK" if c2 is not None else "FAIL")

# ---- 5. 轴最左端 (x=0) 端面棱边倒角 C1 ----
m.clear_selection()
c3 = m.chamfer(1, [(0, R1, 0)], angle_deg=45)
print("STEP5 chamfer left end  C1 ->", "OK" if c3 is not None else "FAIL")

# ---- 8. 第四段键槽 ----
# 用户要求（实测确认的 API 语义）:
#   "键槽长度25" = 圆心距(中心距) 25
#   "右侧圆心距离右端面 7.5" → 右圆心 x = 150 - 7.5 = 142.5
#   左圆心 x = 142.5 - 25 = 117.5
#   宽 10 → 半径 5；底面距过轴心平面 12.5
#   全长 = 圆心距 + 2R = 25 + 10 = 35
#
# 实测结论（读草图段验证）:
#   CreateSketchSlot(center_line=1, FullLength=1, 宽, X1, X2) 中:
#     X1 = 槽中心，X2 = 右圆弧**圆心**（不是最外端点！）
#     传 (中心=130, 右圆心=142.5) → 圆弧圆心落在 117.5 和 142.5
#     → 圆心距 25 OK，右圆心距右端面 7.5 OK，全长 35 OK
#   注意: CenterCenter 模式(0) 的 X1 被解释为槽左端，会生成错误几何 FAIL
KEY_W = 10.0                # 宽度（半径 R=5）
KEY_R = KEY_W / 2.0         # 5
KEY_CC = 25.0               # 圆心距（用户要求"长度25"）
KEY_GAP_RIGHT = 7.5         # 右侧圆心距第四段右端面 7.5（用户明确）
KEY_RIGHT_C = X4 - KEY_GAP_RIGHT        # 右圆心 x = 150 - 7.5 = 142.5
KEY_CENTER = KEY_RIGHT_C - KEY_CC / 2.0   # 槽中心 x = 130
KEY_OFFSET = 12.5           # 键槽底面距过轴心平面的距离

m.clear_selection()
m.begin_sketch("Front Plane")
MM = swapi.MM
m.skm.CreateSketchSlot(
    1,                        # swSketchSlotCreationType_center_line
    1,                        # swSketchSlotLengthType_FullLength（实测：X2=右圆心）
    KEY_W * MM,
    KEY_CENTER * MM, 0, 0,   # 槽中心 (x, y, z)
    KEY_RIGHT_C * MM, 0, 0,  # 右圆弧圆心（实测语义）
    0, 0, 0,
    1, False)
m.end_sketch()
# 选中键槽草图（第二个草图）—— 通用版：按序号选，不依赖中文/英文名
m.model.ClearSelection2(True)
sel = swapi.select_sketch_by_index(m.sw, m.model, 2)
print("STEP8 select keyway sketch ->", "OK" if sel else "FAIL")
if not sel:
    raise RuntimeError("cannot select keyway sketch by index 2")
k = m.fm.FeatureCut3(
    True, False, False, 1, 0, 0.0, 0.0,          # 单向, 完全贯穿
    False, False, False, False, 0, 0,
    False, False, False, False, False, False, True,
    False, False, False,                          # 自动选择
    3, KEY_OFFSET * MM, True)                     # 起始=等距12.5, 方向向外
print("STEP8 keyway: 圆心距%d 右圆心x=%.1f(距右端面%.1f) 中心x=%.1f ->"
      % (KEY_CC, KEY_RIGHT_C, X4 - KEY_RIGHT_C, KEY_CENTER),
      "OK" if k is not None else "FAIL")

# ---- 6. 轴最右端 (x=186) 端面棱边倒角 C1 ----
m.clear_selection()
c4 = m.chamfer(1, [(X5, R5, 0)], angle_deg=45)
print("STEP6 chamfer right end C1 ->", "OK" if c4 is not None else "FAIL")

# ---- 7. 第四段右端 (x=150) 台阶棱边倒角 C1 ----
m.clear_selection()
c5 = m.chamfer(1, [(X4, R4, 0)], angle_deg=45)
print("STEP7 chamfer seg4 right C1 ->", "OK" if c5 is not None else "FAIL")

# ---- 9. 保存 ----
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "DSH_五段阶梯轴.sldprt")
print("STEP9 save:", m.save(out))
