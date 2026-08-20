# -*- coding: utf-8 -*-
"""
画一个三角锥（正四面体风格的三角锥）：
  - 底面：正三角形，边长 60mm
  - 高：50mm
  - 方法：拉伸正三角形 + 拔模角度，使顶部收敛为一点

坐标：
  A (0, 34.641)   上顶点
  B (-30, 0)      左下
  C (30, 0)       右下
"""
import math
import os
import swapi

# ── 新建零件 ──────────────────────────────────────────────
m = swapi.new_part()

# ── 在 Front Plane 上画底面正三角形 ───────────────────────
m.begin_sketch("Front Plane")
# 正三角形，边长 60mm，y轴向上，x轴向右
# 重心在 (0, 0)，内切圆半径 r = 60 * sqrt(3) / 6 ≈ 17.32
# 顶点坐标：
#   A: (0, 34.641)   —— 上顶点
#   B: (-30, 0)      —— 左下
#   C: (30, 0)       —— 右下
m.polyline([(0, 34.641), (-30, 0), (30, 0), (0, 34.641)])
m.end_sketch()

# ── 拉伸 + 拔模 ───────────────────────────────────────────
# 计算拔模角度：使三角锥顶高 50mm
# 底面内切圆半径 = 60 * sqrt(3) / 6 = 17.3205 mm
# 拔模角 = atan(17.3205 / 50) ≈ 19.107°（向内收缩为正方向）
r_in = 60 * math.sqrt(3) / 6
draft_deg = math.degrees(math.atan(r_in / 50))
print(f"底面内切圆半径: {r_in:.3f} mm")
print(f"所需拔模角度: {draft_deg:.3f}°")

# 注意：swapi.extrude 的 draft_deg 参数被定义了但未传入 FeatureExtrusion3！
# 这里直接用 COM 调用，确保 draft 生效。
# FeatureExtrusion3 签名（关键参数）：
#   Add, MirrorInOnePlane, ThinFeature,
#   EndCond1, Angle1(draft), Depth1,
#   StartOffset, StartOffsetFlip, ...
#   True, False, False, 0, draft_deg, 50, 0, False, ...
feat = m.fm.FeatureExtrusion3(
    True, False, False,          # Add, Mirror, Thin
    0, draft_deg, 50, 0,        # EndCond=Blind, Draft=19.107°, Depth=50mm
    False, False, False, False, 0, 0,
    False, False, False, False, True, False, True,
    0, 0, False
)
m._visual_step("extrude+draft")

# ── 保存零件 ──────────────────────────────────────────────
_out_dir = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output"
os.makedirs(_out_dir, exist_ok=True)
out_path = os.path.join(_out_dir, "DSH_三角锥.sldprt")
rc = m.save(out_path)
print(f"零件已保存: {out_path}")
print(f"save result: {rc}")

# ── 截图展示 ──────────────────────────────────────────────
m.set_view_iso()
shot = m.screenshot(os.path.join(_out_dir, "三角锥_iso.png"))
print(f"ISO截图: {shot}")
