"""测试：草图创建 + ForceRebuild 提交"""
import os, sys, time, pythoncom, win32com.client
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
time.sleep(1)

skm = model.SketchManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. 选中前视基准面
model.ClearSelection2(True)
sel = ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"Plane selected: {sel}")

# 3. 开始草图
skm.InsertSketch(True)
print("Sketch started")

# 4. 画圆（单位：米）
skm.CreateCircleByRadius(0, 0, 0, 0.05)
print("Circle drawn")

# 5. 结束草图（不切换模式）
skm.InsertSketch(False)
print("Sketch ended")

# 6. 强制重建
model.ForceRebuild3(True)
time.sleep(0.5)

# 7. 检查特征
print("\nFeatures after rebuild:")
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

# 8. 拉伸
print("\nTrying extrude...")
feat2 = model.FeatureManager.FeatureExtrusion2(
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

# 9. 再次重建
model.ForceRebuild3(True)
time.sleep(0.5)

# 10. 最终特征列表
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

# 11. 保存
out_path = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

print("Done")