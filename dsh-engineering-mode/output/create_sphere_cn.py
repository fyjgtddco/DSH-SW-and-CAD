# -*- coding: utf-8 -*-
"""用中文基准面名创建球体"""
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

tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(0.5)

skMgr = model.SketchManager
featMgr = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 用中文基准面名
model.ClearSelection2(True)
sel = ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"Select 前视基准面: {sel}")

# 开始草图
skMgr.InsertSketch(True)
time.sleep(0.3)

# 画半圆弧 + 直径线 + 中心线
r = 50
n = 24
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    skMgr.CreateLine(r*math.cos(a1)*0.001, r*math.sin(a1)*0.001, 0,
                     r*math.cos(a2)*0.001, r*math.sin(a2)*0.001, 0)
    time.sleep(0.02)

# 直径线闭合轮廓
skMgr.CreateLine(0, 0.050, 0, 0, -0.050, 0)
# 中心线
skMgr.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 结束草图
skMgr.InsertSketch(True)
time.sleep(0.3)
print("Sketch done")

# 检查特征
feat = model.FirstFeature
while feat:
    try:
        print(f"  Feat: {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 旋转
feat2 = featMgr.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True
)
print(f"Revolve: {feat2} ({type(feat2)})")

# 重建
model.ForceRebuild3(True)
time.sleep(0.3)

# 再次检查特征
feat = model.FirstFeature
while feat:
    try:
        print(f"  After: {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"Saved: {out_path}")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_final.png"))

print("Done")