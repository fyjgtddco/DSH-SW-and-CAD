"""直接用 COM API 画矩形+拉伸，用中文基准面名"""
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

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 选中文基准面
model.ClearSelection2(True)
r1 = ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"Select 前视基准面: {r1}")

# 开始草图
r2 = skm.InsertSketch(True)
print(f"InsertSketch start: {r2!r}")

# 画矩形 (单位：米)
w, h = 0.1, 0.1
rect = skm.CreateCornerRectangle(-w/2, h/2, 0, w/2, -h/2, 0)
print(f"CreateCornerRectangle: {rect is not None}")

# 结束草图
r3 = skm.InsertSketch(True)
print(f"InsertSketch end: {r3!r}")

# 重建
model.ForceRebuild3(True)
time.sleep(0.3)

# 特征列表
print("\nFeatures after sketch:")
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

# 拉伸
depth = 0.02
feat2 = fm.FeatureExtrusion3(
    True, False, False, 0, 0, depth, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"\nFeatureExtrusion3: {feat2}")

if feat2:
    try:
        print(f"  Type: {feat2.GetTypeName}")
        print(f"  Name: {feat2.Name}")
    except:
        pass

# 最终特征
print("\nFinal features:")
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

# 保存
out_path = os.path.join(out_dir, "DSH_rect.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "rect_part.png"))

print("Done")