# -*- coding: utf-8 -*-
"""Bug已修，重新生成CAD图"""
import os, sys, time, pythoncom, win32com.client
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

# 1. 创建圆柱体
m = swapi.new_part()
m.begin_sketch("Front Plane")
m.circle(0, 0, 50)
m.end_sketch()
m.extrude(20)
out_part = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
m.save(out_part)
m.set_view_iso()
m.zoom_to_fit()
time.sleep(1)
print("Part created")

# 2. 确保零件是激活文档
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(out_part, 1, 1, "", errs, warns)
time.sleep(1)
print(f"Active doc: {sw.ActiveDoc.GetTitle}")

# 3. 找绘图模板
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Template: {tmpl}")

# 4. 新建工程图
doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
time.sleep(2)
print(f"Drawing type: {doc.GetType}, title: {doc.GetTitle}")

# 5. 创建视图 - 用原始方法，不加异常处理让bug直接暴露
views_ok = 0
for view_name, x, y, label in [
    ("*Front", 0.150, 0.180, "前视图"),
    ("*Top", 0.150, 0.070, "俯视图"),
    ("*Right", 0.280, 0.180, "右视图"),
    ("*Isometric", 0.280, 0.070, "等轴测"),
]:
    v = doc.CreateDrawViewFromModelView(out_part, view_name, x, y, 0)
    print(f"  {label}: {v!r}")
    if v:
        views_ok += 1
    time.sleep(0.5)

print(f"Views created: {views_ok}/4")

time.sleep(1)
doc.ViewZoomtofit2()
time.sleep(1)

# 6. 截图
m2 = swapi.from_active(sw)
m2.zoom_to_fit()
time.sleep(1)
m2.screenshot(os.path.join(out_dir, "drawing_final.png"))

# 7. 保存
dwg_path = os.path.join(out_dir, "DSH_cylinder.SLDDRW")
doc.SaveAs3(dwg_path, 0, 2)
out_dwg = os.path.join(out_dir, "DSH_cylinder.dwg")
doc.SaveAs3(out_dwg, 0, 2)
print(f"DWG: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")