# -*- coding: utf-8 -*-
"""完整排查：关闭所有文档→打开球体→检查→建工程图"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
part_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")

# 1. 关闭所有文档
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.5)
except:
    pass
print("All docs closed")

# 2. 打开球体零件
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(2)
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)
m = swapi.from_active(sw)
m.set_view_iso()
m.zoom_to_fit()
time.sleep(1)
m.screenshot(os.path.join(out_dir, "part_verify.png"))
print("Part screenshot saved")

# 3. 检查特征
feat = m.model.FirstFeature
feat_list = []
while feat:
    try:
        feat_list.append(feat.Name)
    except:
        feat_list.append("?")
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None
print(f"Features: {feat_list}")

# 4. 找绘图模板并建工程图
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Template: {tmpl}")

doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
time.sleep(1)
if doc:
    print("New drawing created")
    print(f"Doc type: {doc.GetType}")
    
    # 添加视图
    for view_name, x, y, label in [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]:
        try:
            v = doc.CreateDrawViewFromModelView(part_path, view_name, x, y, 0)
            print(f"  {label}: view={v}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  {label}: ERROR {e}")
    
    time.sleep(1)
    doc.ViewZoomtofit2()
    time.sleep(1)
    
    # 截图工程图
    m2 = swapi.from_active(sw)
    m2.zoom_to_fit()
    time.sleep(1)
    m2.screenshot(os.path.join(out_dir, "drawing_verify.png"))
    print("Drawing screenshot saved")
    
    # 保存
    dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
    doc.SaveAs3(dwg_path, 0, 2)
    print(f"Drawing saved: {os.path.exists(dwg_path)}")
    
    out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")
    doc.SaveAs3(out_dwg, 0, 2)
    print(f"DWG saved: {os.path.exists(out_dwg)}, size={os.path.getsize(out_dwg)//1024}KB")

print("Done")