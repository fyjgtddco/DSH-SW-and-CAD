# -*- coding: utf-8 -*-
"""为球体生成CAD工程图"""
import os, json, sys, time, pythoncom, win32com.client, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
part_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
out_dwg = os.path.join(out_dir, "DSH_sphere.dwg")

result = {}

# 1. 打开零件
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(1)
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)

# 2. 找绘图模板
tmpl = sw_bridge._find_drawing_template(sw)
result["template"] = tmpl
print(f"Drawing template: {tmpl}")

# 3. 新建工程图（新文档会成为激活文档）
doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
if doc is None:
    result["error"] = "NewDocument failed"
else:
    time.sleep(1)
    
    # 4. 添加视图
    views_added = []
    for view_name, x, y, label in [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]:
        try:
            v = doc.CreateDrawViewFromModelView(part_path, view_name, x, y, 0)
            if v is not None:
                views_added.append(label)
            time.sleep(0.3)
        except Exception as e:
            print(f"  {label}: ERROR {e}")
    result["views_added"] = views_added
    print(f"Views added: {views_added}")
    
    time.sleep(1)
    doc.ViewZoomtofit2()
    time.sleep(0.5)
    
    # 5. 截图工程图（此时激活文档应该是工程图）
    try:
        # 获取激活文档截图
        shot_path = os.path.join(out_dir, "sphere_dwg_drawing.png")
        # 用 win32com 截图
        sw.SendMessageToSW(1)  # 重绘
        time.sleep(0.5)
        # 截屏
        m = swapi.from_active(sw)
        m.screenshot(shot_path)
        result["drawing_screenshot"] = shot_path
        print("Drawing screenshot saved")
    except Exception as e:
        print(f"Screenshot error: {e}")
    
    # 6. 保存工程图
    rc = doc.SaveAs3(dwg_path, 0, 2)
    result["drawing_save"] = {"rc": rc, "exists": os.path.exists(dwg_path)}
    print(f"Drawing saved: rc={rc}")
    
    # 7. 导出 DWG
    try:
        rc2 = doc.SaveAs3(out_dwg, 0, 2)
        result["dwg_export"] = {"rc": rc2, "exists": os.path.exists(out_dwg), "size_kb": os.path.getsize(out_dwg)//1024 if os.path.exists(out_dwg) else 0}
        print(f"DWG exported: rc={rc2}")
    except Exception as e:
        result["dwg_error"] = str(e)

print(json.dumps(result, ensure_ascii=False, indent=2))
print("Done")