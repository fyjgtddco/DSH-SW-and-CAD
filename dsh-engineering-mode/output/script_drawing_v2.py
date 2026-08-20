# -*- coding: utf-8 -*-
"""生成工程图并截图验证"""
import os, json, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
part_path = os.path.join(out_dir, "DSH_sphere.sldprt")
dwg_path = os.path.join(out_dir, "DSH_sphere.slddrw")
out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")

# 1. 打开零件
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(1)

# 2. 截图零件（确认是3D球体）
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)
m = swapi.from_active(sw)
m.set_view_iso()
m.zoom_to_fit()
time.sleep(0.5)
m.screenshot(os.path.join(out_dir, "sphere_3d.png"))

# 3. 找绘图模板
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Drawing template: {tmpl}")

# 4. 新建工程图
doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
if doc is not None:
    # 添加视图
    for view_name, x, y, label in [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]:
        try:
            v = doc.CreateDrawViewFromModelView(part_path, view_name, x, y, 0)
            print(f"  {label}: {v is not None}")
            time.sleep(0.3)
        except Exception as e:
            print(f"  {label}: ERROR {e}")
    
    time.sleep(1)
    doc.ViewZoomtofit2()
    time.sleep(0.5)
    
    # 截图工程图
    try:
        m2 = swapi.from_active(sw)
        m2.screenshot(os.path.join(out_dir, "sphere_drawing.png"))
        print("Drawing screenshot saved")
    except Exception as e:
        print(f"Drawing screenshot error: {e}")
    
    # 保存工程图
    rc = doc.SaveAs3(dwg_path, 0, 2)
    print(f"Drawing saved: rc={rc}, exists={os.path.exists(dwg_path)}")
    
    # 导出 DWG
    try:
        rc2 = doc.SaveAs3(out_dwg, 0, 2)
        print(f"DWG saved: rc={rc2}, exists={os.path.exists(out_dwg)}, size={os.path.getsize(out_dwg) if os.path.exists(out_dwg) else 0}")
    except Exception as e:
        print(f"DWG save error: {e}")

print("Done")