"""创建球体：先画圆拉伸成圆柱，再用旋转切除切出球体"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

# 关闭所有文档
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
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. 先用 sketch-rect 方式创建方块
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)
# 画大圆
skm.CreateCircleByRadius(0, 0, 0, 0.05)
skm.InsertSketch(True)
# 拉伸成圆柱
cyl = fm.FeatureExtrusion3(
    True, False, False, 0, 0, 0.1, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"Cylinder extrude: {cyl is not None}")
if cyl:
    print(f"  Type: {cyl.GetTypeName}, Name: {cyl.Name}")

# 保存中间结果
model.SaveAs3(os.path.join(out_dir, "DSH_cylinder_temp.SLDPRT"), 0, 2)

# 3. 在右视基准面上画半圆切除
model.ClearSelection2(True)
ext.SelectByID2("右视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)

# 画半圆（从 -90° 到 90°）
r = 0.05
n = 20
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    skm.CreateLine(r*math.cos(a1), r*math.sin(a1), 0,
                   r*math.cos(a2), r*math.sin(a2), 0)
# 直径线闭合
skm.CreateLine(0, r, 0, 0, -r, 0)
# 中心线
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)
skm.InsertSketch(True)
time.sleep(0.5)

# 4. 旋转切除
cut = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Revolve cut: {cut is not None}")

# 5. 保存
out_part = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_part, 0, 2)
print(f"Saved: {out_part}, {os.path.getsize(out_part)//1024}KB")

# 6. 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_iso.png"))

print("Done")