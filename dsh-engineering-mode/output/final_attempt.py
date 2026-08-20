# -*- coding: utf-8 -*-
"""最终尝试：用CreateDrawViewFromModelView3创建工程图视图"""
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

# 1. 用extrude创建圆柱体（已验证可工作）
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
print("Part created")

# 2. 确认零件已打开且在SW中
# 先关闭其他文档，只保留零件
try:
    while sw.ActiveDoc:
        doc_title = sw.ActiveDoc.GetTitle
        if doc_title and "cylinder" in doc_title.lower():
            break
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

# 确保零件是激活文档
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
print(f"New drawing type: {doc.GetType}")
print(f"Drawing title: {doc.GetTitle}")

# 5. 尝试多种方法创建视图
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 方法1: CreateDrawViewFromModelView3 (新版API)
for view_name, x, y, label in [
    ("*Front", 0.150, 0.180, "前视图"),
    ("*Top", 0.150, 0.070, "俯视图"),
    ("*Right", 0.280, 0.180, "右视图"),
    ("*Isometric", 0.280, 0.070, "等轴测"),
]:
    try:
        # 方法1: CreateDrawViewFromModelView3
        v = doc.CreateDrawViewFromModelView3(out_part, view_name, x, y, 0, 0, "")
        print(f"  Method3 {label}: {v}")
    except Exception as e:
        print(f"  Method3 {label}: ERROR {e}")
    
    if v is None or v == False:
        try:
            # 方法2: 原始CreateDrawViewFromModelView
            v = doc.CreateDrawViewFromModelView(out_part, view_name, x, y, 0)
            print(f"  Method1 {label}: {v}")
        except Exception as e:
            print(f"  Method1 {label}: ERROR {e}")
    
    time.sleep(0.5)

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

print(f"Done. DWG size: {os.path.getsize(out_dwg)//1024}KB")