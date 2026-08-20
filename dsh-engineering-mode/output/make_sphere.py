"""创建球体：半圆弧+直径线+中心线→旋转360°"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

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
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. 选中前视基准面
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)

# 3. 开始草图
skm.InsertSketch(True)

# 4. 画半圆弧（从 -90° 到 90°）
r = 0.05  # 50mm 转米
n = 24
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    skm.CreateLine(r*math.cos(a1), r*math.sin(a1), 0,
                   r*math.cos(a2), r*math.sin(a2), 0)
    time.sleep(0.01)

# 5. 直径线闭合轮廓
skm.CreateLine(0, r, 0, 0, -r, 0)

# 6. 中心线（旋转轴）
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 7. 结束草图
skm.InsertSketch(True)
time.sleep(0.5)

# 8. 旋转360°
feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True
)
print(f"Revolve: {feat is not None}")

# 9. 保存
out_part = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_part, 0, 2)
print(f"Saved: {out_part}, {os.path.getsize(out_part)//1024}KB")

# 10. 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_iso.png"))

# 11. 生成工程图
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
        v = doc.CreateDrawViewFromModelView(out_part, view_name, x, y, 0)
        print(f"  {label}: {v!r}")
    except Exception as e:
        print(f"  {label}: ERROR {e}")
    time.sleep(0.5)

time.sleep(1)
doc.ViewZoomtofit2()
time.sleep(1)

# 保存工程图
dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
doc.SaveAs3(dwg_path, 0, 2)
print(f"Drawing saved: {os.path.exists(dwg_path)}")

# 导出DWG
out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")
doc.SaveAs3(out_dwg, 0, 2)
print(f"DWG saved: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")

print("Done!")