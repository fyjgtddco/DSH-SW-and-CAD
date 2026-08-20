# -*- coding: utf-8 -*-
"""用extrude做圆柱体→生成CAD工程图，验证流程通不通"""
import os, sys, time, math, pythoncom, win32com.client
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

# === 1. 用swapi创建圆柱体 (extrude已证实可工作) ===
m = swapi.new_part()
m.begin_sketch("Front Plane")
m.circle(0, 0, 50)
m.end_sketch()
m.extrude(20)
m._visual_step("extrude")

out_part = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
m.save(out_part)
m.set_view_iso()
m.zoom_to_fit()
time.sleep(1)
m.screenshot(os.path.join(out_dir, "cylinder_iso.png"))
print("Part created")

# === 2. 生成工程图 ===
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Template: {tmpl}")

doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
time.sleep(1)

if doc:
    views_added = []
    for view_name, x, y, label in [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]:
        try:
            v = doc.CreateDrawViewFromModelView(out_part, view_name, x, y, 0)
            print(f"  {label}: view={v} (type={type(v).__name__})")
            if v is not None and v != False:
                views_added.append(label)
            time.sleep(0.5)
        except Exception as e:
            print(f"  {label}: ERROR {e}")
    print(f"Views added: {views_added}")
    
    time.sleep(1)
    doc.ViewZoomtofit2()
    time.sleep(1)
    
    # 截图工程图
    m2 = swapi.from_active(sw)
    m2.zoom_to_fit()
    time.sleep(1)
    m2.screenshot(os.path.join(out_dir, "cylinder_drawing.png"))
    print("Drawing screenshot saved")
    
    # 保存
    dwg_path = os.path.join(out_dir, "DSH_cylinder.SLDDRW")
    doc.SaveAs3(dwg_path, 0, 2)
    print(f"Drawing saved: {os.path.exists(dwg_path)}")
    
    out_dwg = os.path.join(out_dir, "DSH_cylinder.dwg")
    doc.SaveAs3(out_dwg, 0, 2)
    print(f"DWG saved: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")

print("Done")