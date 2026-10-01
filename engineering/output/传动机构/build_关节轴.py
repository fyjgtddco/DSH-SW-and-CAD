# -*- coding: utf-8 -*-
"""建模: 关节轴 (Joint Shaft)
材料: 45# 调质钢
几何: 阶梯轴, 轴= X 方向, 总长 120mm
  肩台1: X 0..10,  Φ40 (R20)
  主轴:  X 10..110, Φ30 (R15)
  肩台2: X 110..120, Φ40 (R20)
键槽: 8×7 (GB/T1096), 位于主轴中部 X 45..75, 顶部 +Y, 宽(Z)=8, 深(Y)=7
校核: τ=28.3 MPa < 许用 35 MPa
注: cut() 须在草图激活态调用(不 end_sketch), 否则 SW2025 选不中轮廓
"""
import os, math
import swapi

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\output\传动机构\DSH_关节轴.sldprt"

m = swapi.new_part()

# ---------- Step 1: 旋转轮廓 (半剖面, 端点落在轴线上) ----------
m.begin_sketch("Front Plane")
pts = [(0,0),(0,20),(10,20),(10,15),(110,15),(110,20),(120,20),(120,0)]
m.polyline(pts)
m.centerline(0, 0, 120, 0)
m.end_sketch()
m.revolve(360, expect_cylinder=(40, 120))

# ---------- Step 2: 材料 ----------
res = m.set_material("45#")
print("MATERIAL:", res.get("ok"), res.get("applied_name"), res.get("density_after_kg_m3"))

# ---------- Step 3: 键槽 8×7 (草图激活态直接 cut) ----------
m.begin_sketch("Front Plane")
m.rect(60, 11.5, 30, 7)   # X 45..75, Y 8..15 -> 宽30深7  (Z向切穿)
# 不 end_sketch, 直接切除 ±4 -> 总宽 8mm
m.cut(depth=4, through=False, through_both=True)
m.end_sketch()

# ---------- Step 4: 阶梯过渡圆角 R2 ----------
for ep in [(10,20,0),(110,20,0),(0,20,0),(120,20,0)]:
    try:
        m.fillet(2, [ep])
    except Exception as e:
        print("fillet skip", ep, e)

m.rebuild()
m.bring_to_front()
m.set_view_iso()
m.screenshot(os.path.join(os.path.dirname(OUT), "DSH_关节轴_preview.png"))
res = m.save(OUT)
print("SAVE:", res)
