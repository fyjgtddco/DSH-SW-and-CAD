"""创建圆柱体：先new，再画圆+拉伸"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

# 确保有激活文档
try:
    if sw.ActiveDoc is None:
        # 创建新零件
        tmpl = swapi.get_part_template(sw)
        sw.NewDocument(tmpl, 0, 0.1, 0.1)
        time.sleep(1)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

d = sw.ActiveDoc
if d is None:
    print("No active document")
    sys.exit(1)

skm = d.SketchManager
fm = d.FeatureManager
ext = d.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 选中前视基准面
try:
    ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
except:
    pass

# 开始草图
skm.InsertSketch(True)

# 画圆（单位：米）
r = 0.05
skm.CreateCircleByRadius(0, 0, 0, r)

# 结束草图
skm.InsertSketch(True)

# 拉伸
depth = 0.02
feat = fm.FeatureExtrusion3(
    True, False, False, 0, 0, depth, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"Extrude: {feat is not None}")

# 特征列表
print("\nFeatures:")
feat = d.FirstFeature
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
out_path = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
d.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 截图
d.ShowNamedView2("*Isometric", 0)
d.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "cylinder_part.png"))

print("Done")