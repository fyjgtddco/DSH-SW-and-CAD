"""测试：用最简单的矩形+中心线测试 revolve"""
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

# 画矩形+中心线
skm.InsertSketch(True)
skm.CreateCornerRectangle(0.001, 0.05, 0, 0.05, 0.001, 0)  # 瘦矩形
skm.CreateCenterLine(0, -0.06, 0, 0, 0.06, 0)  # Y轴中心线
skm.InsertSketch(True)
time.sleep(0.5)

# 尝试 revolve
print("Trying FeatureRevolve2...")
try:
    feat = fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, 6.283185307179586, 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True)
    print(f"  Result: {feat!r}")
    if feat:
        print(f"  Type: {feat.GetTypeName}, Name: {feat.Name}")
except Exception as e:
    print(f"  Error: {e}")

# 保存
model.SaveAs3(os.path.join(out_dir, "test_revolve.SLDPRT"), 0, 2)
print(f"Saved, {os.path.getsize(os.path.join(out_dir, 'test_revolve.SLDPRT'))//1024}KB")

print("Done")