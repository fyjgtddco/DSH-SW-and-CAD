"""创建球体：正确的方式 - 半圆弧+直径+中心线"""
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

# 开始草图
skm.InsertSketch(True)

# 画半圆弧（从 (0,-50) 到 (0,50)，经过 (50,0)）
r = 0.05
n = 30  # 段数
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    x1, y1 = r * math.cos(a1), r * math.sin(a1)
    x2, y2 = r * math.cos(a2), r * math.sin(a2)
    skm.CreateLine(x1, y1, 0, x2, y2, 0)
    time.sleep(0.005)

# 直径线闭合轮廓
skm.CreateLine(0, r, 0, 0, -r, 0)

# 中心线（旋转轴）
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 结束草图
skm.InsertSketch(True)
time.sleep(0.5)

# 旋转360°
print("Revolve...")
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
print(f"Saved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_iso.png"))

# 生成工程图
tmpl_drw = sw_bridge._find_drawing_template(sw) if 'sw_bridge' in dir() else None
if tmpl_drw is None:
    import sw_bridge
    tmpl_drw = sw_bridge._find_drawing_template(sw)

doc = sw.NewDocument(tmpl_drw, 0, 0.420, 0.297)
time.sleep(2)

for view_name, x, y, label in [
    ("*Front", 0.150, 0.180, "前视图"),
    ("*Top", 0.150, 0.070, "俯视图"),
    ("*Right", 0.280, 0.180, "右视图"),
    ("*Isometric", 0.280, 0.070, "等轴测"),
]:
    try:
        v = doc.CreateDrawViewFromModelView(out_path, view_name, x, y, 0)
        print(f"  {label}: {v!r}")
    except Exception as e:
        print(f"  {label}: ERROR {e}")
    time.sleep(0.5)

time.sleep(1)
doc.ViewZoomtofit2()
time.sleep(1)

# 保存工程图和DWG
dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
doc.SaveAs3(dwg_path, 0, 2)
print(f"Drawing saved: {os.path.exists(dwg_path)}")

out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")
doc.SaveAs3(out_dwg, 0, 2)
print(f"DWG saved: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")

print("Done!")