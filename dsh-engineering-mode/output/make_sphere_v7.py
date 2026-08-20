"""创建球体：用正确的方法——分段弧线+中心线"""
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

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 选前视基准面
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)

# 开始草图
skm.InsertSketch(True)

R = 0.05  # 50mm
# 画上半圆弧（从右侧到左侧，不接触中心线）
n = 32
for i in range(n):
    a1 = -math.pi/2 + 0.001 + (math.pi * i / n)
    a2 = -math.pi/2 + 0.001 + (math.pi * (i+1) / n)
    x1 = R * math.cos(a1)
    y1 = R * math.sin(a1)
    x2 = R * math.cos(a2)
    y2 = R * math.sin(a2)
    skm.CreateLine(x1, y1, 0, x2, y2, 0)
    time.sleep(0.005)

# 左侧垂线（连接弧的端点，形成闭合轮廓）
skm.CreateLine(R * math.cos(-math.pi/2 + 0.001), R * math.sin(-math.pi/2 + 0.001), 0,
               R * math.cos(math.pi/2 - 0.001), R * math.sin(math.pi/2 - 0.001), 0)

# 中心线（旋转轴，在轮廓左侧）
skm.CreateCenterLine(-0.002, -0.065, 0, -0.002, 0.065, 0)

# 结束草图
skm.InsertSketch(True)
time.sleep(0.5)

# 检查特征
print("Features after sketch:")
feat = model.FirstFeature
while feat:
    try:
        print(f"  {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 旋转360°
print("\nRevolve...")
feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Result: {feat!r}")
if feat:
    print(f"  Type: {feat.GetTypeName}, Name: {feat.Name}")

# 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 体积检查
mp = model.GetMassProperties
vol = mp[3] * 1e9
expected = 4/3 * math.pi * 50**3
print(f"Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
print(f"Match: {abs(vol - expected) / expected < 0.05}")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_final.png"))

print("Done!")