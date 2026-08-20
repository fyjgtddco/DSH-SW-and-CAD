"""测试：用 CreateArcBy3Point 画半圆"""
import os, sys, time, math, pythoncom, win32com.client
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

R = 0.05
# 用 CreateArcBy3Point 画上半圆
# 起点 (R, 0), 中间点 (0, R), 终点 (-R, 0)
# 但这样会穿过中心线... 让我试试不接触中心线的
# 起点 (R+0.001, 0), 中间点 (0.001, R), 终点 (-R-0.001, 0) - 不对
# 让我用正确的方式：半圆从 (0.05, -0.05) 到 (0.05, 0.05)，经过 (0, 0)
# 但 (0,0) 在中心线上

# 方案：用 CreateArcByRadius
# 圆心 (0.05, 0), 半径 0.05, 从 -90度 到 +90度
# 这会创建一个半圆弧，从 (0.05, -0.05) 到 (0.05, 0.05)，经过 (0.1, 0)
# 这个半圆弧完全在中心线右侧
try:
    # CreateArcByRadius: 圆心X, 圆心Y, 半径, 起始角(弧度), 终止角(弧度)
    skm.CreateArcByRadius(0.05, 0, R, -math.pi/2, math.pi/2)
    print("Arc created")
except Exception as e:
    print(f"CreateArcByRadius error: {e}")
    # 备选：用多个线段
    n = 32
    for i in range(n):
        a1 = -math.pi/2 + (math.pi * i / n)
        a2 = -math.pi/2 + (math.pi * (i+1) / n)
        x1 = 0.05 + R * math.cos(a1)
        y1 = R * math.sin(a1)
        x2 = 0.05 + R * math.cos(a2)
        y2 = R * math.sin(a2)
        skm.CreateLine(x1, y1, 0, x2, y2, 0)
        time.sleep(0.005)
    print("Fallback: line segments created")

# 中心线（Y轴）
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 结束草图
skm.InsertSketch(True)
time.sleep(0.5)

# 旋转
print("\nRevolve...")
feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Result: {feat!r}")

if feat:
    print(f"  Type: {feat.GetTypeName}, Name: {feat.Name}")
    # 体积检查
    mp = model.GetMassProperties
    vol = mp[3] * 1e9
    expected = 4/3 * math.pi * 50**3
    print(f"Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
    print(f"Match: {abs(vol - expected) / expected < 0.05}")

# 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"Saved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_test.png"))

print("Done!")