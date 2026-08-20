"""用已验证可用的 swapi.extrude() 创建零件 + 生成工程图"""
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

# 1. 用 swapi 创建圆柱体（已验证可工作）
m = swapi.new_part()
m.begin_sketch("前视基准面")
m.circle(0, 0, 50)
m.end_sketch()
m.extrude(20)  # 带 auto_select=True，可以工作
m._visual_step("extrude")

out_part = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
m.save(out_part)
print(f"Part saved: {os.path.exists(out_part)}, {os.path.getsize(out_part)//1024}KB")

# 2. 检查零件特征
m2 = swapi.from_active(sw)
feat = m2.model.FirstFeature
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
m2.set_view_iso()
m2.zoom_to_fit()
time.sleep(1)
m2.screenshot(os.path.join(out_dir, "cylinder_part.png"))
print("Part screenshot saved")

# 4. 找绘图模板
tmpl = sw_bridge._find_drawing_template(sw)
print(f"Template: {tmpl}")

# 5. 新建工程图
doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
time.sleep(2)
print(f"Drawing: type={doc.GetType}, title={doc.GetTitle}")

# 6. 确保零件是打开的
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(out_part, 1, 1, "", errs, warns)
time.sleep(1)

# 7. 创建视图 - 测试不同的参数组合
views_added = 0
for view_name, x, y, label in [
    ("*Front", 0.150, 0.180, "前视图"),
    ("*Top", 0.150, 0.070, "俯视图"),
    ("*Right", 0.280, 0.180, "右视图"),
    ("*Isometric", 0.280, 0.070, "等轴测"),
]:
    try:
        # 尝试 CreateDrawViewFromModelView3 (新API)
        v = doc.CreateDrawViewFromModelView3(out_part, view_name, x, y, 0, 0, "")
        print(f"  {label} (v3): {v!r}")
        if v:
            views_added += 1
    except Exception as e:
        print(f"  {label} (v3): ERROR {e}")
        try:
            # 回退到原始方法
            v = doc.CreateDrawViewFromModelView(out_part, view_name, x, y, 0)
            print(f"  {label} (v1): {v!r}")
            if v:
                views_added += 1
        except Exception as e2:
            print(f"  {label} (v1): ERROR {e2}")
    time.sleep(0.5)

print(f"\nViews created: {views_added}/4")

# 8. 截图工程图
time.sleep(1)
doc.ViewZoomtofit2()
time.sleep(1)
m3 = swapi.from_active(sw)
m3.zoom_to_fit()
time.sleep(1)
m3.screenshot(os.path.join(out_dir, "cylinder_drawing.png"))

# 9. 保存
dwg_path = os.path.join(out_dir, "DSH_cylinder.SLDDRW")
doc.SaveAs3(dwg_path, 0, 2)
out_dwg = os.path.join(out_dir, "DSH_cylinder.dwg")
doc.SaveAs3(out_dwg, 0, 2)
print(f"\nDrawing saved: {os.path.exists(dwg_path)}")
print(f"DWG saved: {os.path.exists(out_dwg)}, {os.path.getsize(out_dwg)//1024}KB")

print("\nDone!")