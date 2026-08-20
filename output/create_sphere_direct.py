# -*- coding: utf-8 -*-
"""直接COM API创建球体，绕过swapi的草图封装"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 1. 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(0.5)
print(f"New part created: {model is not None}")

# 2. 获取SketchManager和FeatureManager
skMgr = model.SketchManager
featMgr = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 3. 选择前视基准面
model.ClearSelection2(True)
sel = ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"Select Front Plane: {sel}")

# 4. 开始草图
skMgr.InsertSketch(True)
time.sleep(0.3)

# 5. 画半圆弧 + 直线 + 中心线
r = 50
n = 24
# 半圆弧
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    x1, y1 = r * math.cos(a1), r * math.sin(a1)
    x2, y2 = r * math.cos(a2), r * math.sin(a2)
    skMgr.CreateLine(x1*0.001, y1*0.001, 0, x2*0.001, y2*0.001, 0)
    time.sleep(0.02)

# 直径线闭合轮廓
skMgr.CreateLine(0, 0.050, 0, 0, -0.050, 0)

# 中心线
skMgr.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 6. 结束草图
skMgr.InsertSketch(True)
time.sleep(0.3)
print("Sketch created")

# 7. 重建
model.ForceRebuild3(True)
time.sleep(0.3)

# 8. 检查特征
feat = model.FirstFeature
while feat:
    try:
        print(f"  Feature: {feat.Name} ({feat.GetTypeName})")
    except:
        print(f"  Feature: <unknown>")
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 9. 旋转
feat2 = featMgr.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,  # 2*PI
    False, False, 0, 0, 0, 0, 0,
    True, False, True
)
print(f"Revolve: {feat2}")

# 10. 再次检查特征
feat = model.FirstFeature
while feat:
    try:
        print(f"  After revolve: {feat.Name} ({feat.GetTypeName})")
    except:
        print(f"  After revolve: <unknown>")
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 11. 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"Saved: {out_path}")

# 12. 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
# 使用截图工具
import swapi
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_direct.png"))

print("Done")