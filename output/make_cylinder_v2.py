"""跳过误报，直接用 COM API 画图"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

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
ext = model.Extension
fm = model.FeatureManager
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. 选中前视基准面
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)

# 3. 开始草图
skm.InsertSketch(True)

# 4. 画圆（单位：米）
skm.CreateCircleByRadius(0, 0, 0, 0.05)  # R=50mm
print("Circle drawn")

# 5. 结束草图（toggle 模式）
skm.InsertSketch(False)
time.sleep(0.5)

# 6. 检查特征
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

# 7. 拉伸（需要选中草图）
print("\nTrying extrude...")
try:
    # 先选中草图
    model.ClearSelection2(True)
    sk = model.GetFirstSketch
    while sk:
        try:
            sk.Select2(True, 0)
        except:
            pass
        try:
            sk = sk.GetNextSketch
        except:
            sk = None
    
    # 拉伸
    feat2 = fm.FeatureExtrusion2(
        True, False, False, 0, 0, 0.02, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, True, False, 0, 0, False)
    print(f"Extrude result: {feat2}")
    
    if feat2:
        try:
            print(f"  Type: {feat2.GetTypeName}")
            print(f"  Name: {feat2.Name}")
        except:
            pass
except Exception as e:
    print(f"Extrude error: {e}")

# 8. 再次检查特征
print("\nFeatures after extrude:")
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

# 9. 保存
out_path = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 10. 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "cylinder_part.png"))

print("Done")