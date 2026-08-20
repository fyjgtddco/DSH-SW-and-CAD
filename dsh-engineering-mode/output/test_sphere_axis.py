"""测试：圆穿过中心线的旋转"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 测试：圆心在轴上（圆穿过轴）
model = sw.NewDocument(swapi.get_part_template(sw), 0, 0.1, 0.1)
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

# 画圆（圆心在原点，半径0.025）- 圆穿过中心线
skm.CreateCircleByRadius(0, 0, 0, 0.025)

# 中心线（Y轴）
skm.CreateCenterLine(0, -0.06, 0, 0, 0.06, 0)

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

# 旋转
print("\nRevolve...")
try:
    feat = fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, 6.283185307179586, 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True)
    print(f"Result: {feat!r}")
    if feat:
        print(f"  Type: {feat.GetTypeName}, Name: {feat.Name}")
except Exception as e:
    print(f"Error: {e}")

# 保存
out_path = os.path.join(out_dir, "test_sphere_axis.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"Saved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_axis.png"))

print("Done!")