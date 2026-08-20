"""创建圆柱体：画圆+拉伸"""
import sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

# 确保新零件是激活的
d = sw.ActiveDoc
print(f"Active: {d.GetTitle}")

skm = d.SketchManager
fm = d.FeatureManager
ext = d.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 选中前视基准面
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
print("Plane selected")

# 开始草图
skm.InsertSketch(True)
print("Sketch started")

# 画圆（单位：米）
skm.CreateCircleByRadius(0, 0, 0, 0.05)
print("Circle drawn")

# 结束草图
skm.InsertSketch(True)
print("Sketch ended")

# 拉伸
depth = 0.02
feat = fm.FeatureExtrusion3(
    True, False, False, 0, 0, depth, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"Extrude: {feat is not None}")

if feat:
    print(f"  Type: {feat.GetTypeName}")
    print(f"  Name: {feat.Name}")

# 保存
out_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_cylinder.SLDPRT"
d.SaveAs3(out_path, 0, 2)
print(f"Saved: {out_path}")

# 截图
d.ShowNamedView2("*Isometric", 0)
d.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(r"C:\Users\j1877\Desktop\DSH-Check\SW\cylinder_iso.png")
print("Screenshot saved")