"""创建球体：圆柱+旋转切除半圆"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

# 关闭所有
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

# 2. 选前视基准面
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)

# 3. 开始草图
skm.InsertSketch(True)

# 4. 画大圆（直径100mm）作为基础轮廓
skm.CreateCircleByRadius(0, 0, 0, 0.05)

# 5. 结束草图
skm.InsertSketch(True)
time.sleep(0.3)

# 6. 拉伸成圆柱（高100mm）
feat1 = fm.FeatureExtrusion3(
    True, False, False, 0, 0, 0.1, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"Base cylinder: {feat1 is not None}")

# 7. 在上视基准面画半圆（用于切除）
model.ClearSelection2(True)
ext.SelectByID2("上视基准面", "PLANE", 0, 0, 0.05, False, 0, empty, 0)
skm.InsertSketch(True)

# 画半圆（从右侧到左侧，穿过中心）
# 用线段近似半圆
R = 0.05
n = 20
for i in range(n):
    a1 = (math.pi * i / n)
    a2 = (math.pi * (i+1) / n)
    x1 = R * math.cos(a1)
    y1 = R * math.sin(a1)
    x2 = R * math.cos(a2)
    y2 = R * math.sin(a2)
    skm.CreateLine(x1, y1, 0, x2, y2, 0)
    time.sleep(0.005)

# 中心线
skm.CreateCenterLine(-0.06, 0, 0, 0.06, 0, 0)

skm.InsertSketch(True)
time.sleep(0.3)

# 8. 旋转切除上半部分（cut=True）
print("\nRevolve cut...")
feat2 = fm.FeatureRevolve2(
    True, True, False, True, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Revolve cut: {feat2 is not None}")

if feat2:
    print(f"  Type: {feat2.GetTypeName}, Name: {feat2.Name}")

# 9. 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 检查体积
mp = model.GetMassProperties
vol = mp[3] * 1e9
expected = 4/3 * math.pi * 50**3
print(f"Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
print(f"Match: {abs(vol - expected) / expected < 0.05}")

# 10. 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_final.png"))

print("Done!")