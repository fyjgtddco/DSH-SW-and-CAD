"""测试：简单圆旋转（环面），然后思考球体方案"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

# 关闭所有
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 选前视基准面
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)

# 开始草图
skm.InsertSketch(True)

# 画一个圆（圆心在(0.06,0)，半径0.02）- 不接触中心线
skm.CreateCircleByRadius(0.06, 0, 0, 0.02)

# 中心线（Y轴）
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)

# 结束草图
skm.InsertSketch(True)
time.sleep(0.5)

# 旋转360°
feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Torus revolve: {feat is not None}")

# 保存中间件
model.SaveAs3(os.path.join(out_dir, "test_torus2.SLDPRT"), 0, 2)
mp = model.GetMassProperties
vol = mp[3] * 1e9
print(f"Volume: {vol:.1f} mm3")
# Torus: outer_r=0.06, inner_r=0.04, tube_r=0.02
# Vol = 2*pi^2 * R * r^2 = 2*9.87*0.06*0.0004 = 0.000473 m3 = 473000 mm3
expected = 2 * math.pi**2 * 0.06 * 0.02**2 * 1e9
print(f"Expected: {expected:.1f} mm3")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "torus2_iso.png"))

print("Done!")