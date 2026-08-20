# -*- coding: utf-8 -*-
"""验证球体特征 + 生成工程图"""
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
part_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")

# 1. 打开零件
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(1)
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)

# 2. 检查特征
m = swapi.from_active(sw)
feat = m.model.FirstFeature
while feat:
    try:
        print(f"  Feature: {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 3. 截图零件
m.set_view_iso()
m.zoom_to_fit()
time.sleep(1)
m.screenshot(os.path.join(out_dir, "sphere_final.png"))

# 4. 找绘图模板
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Template: {tmpl}")

# 5. 新建工程图
doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
time.sleep(1)
if doc:
    for view_name, x, y, label in [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]:
        v = doc.CreateDrawViewFromModelView(part_path, view_name, x, y, 0)
        print(f"  {label}: view={v}")
        time.sleep(0.5)
    
    time.sleep(1)
    doc.ViewZoomtofit2()
    time.sleep(1)
    
    # 截图工程图
    m2 = swapi.from_active(sw)
    m2.zoom_to_fit()
    time.sleep(1)
    m2.screenshot(os.path.join(out_dir, "drawing_final.png"))
    
    # 保存
    dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
    doc.SaveAs3(dwg_path, 0, 2)
    print(f"Drawing saved: {os.path.exists(dwg_path)}")
    
    out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")
    doc.SaveAs3(out_dwg, 0, 2)
    print(f"DWG saved: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")

print("Done")