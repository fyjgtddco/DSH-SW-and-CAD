"""用 Loft 创建球体近似：3层圆（下点→赤道→上点）"""
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

R = 0.05  # 球半径 50mm

def make_circle(z_height):
    """在指定Z高度创建圆形草图"""
    model.ClearSelection2(True)
    # 尝试选前视基准面（对于水平圆，需要选实体表面或构造平面）
    # 用 Z 坐标选择平面
    try:
        ext.SelectByID2("前视基准面", "PLANE", 0, 0, z_height, False, 0, empty, 0)
    except:
        try:
            ext.SelectByID2("Front Plane", "PLANE", 0, 0, z_height, False, 0, empty, 0)
        except:
            pass
    skm.InsertSketch(True)
    skm.CreateCircleByRadius(0, 0, 0, R * math.sqrt(1 - (z_height / R) ** 2) if abs(z_height) < R else 0)
    skm.InsertSketch(True)
    time.sleep(0.2)

# 实际上，对于球体，每个水平截面的半径是 R*sin(θ)
# 让我用不同的方法：创建多个水平圆草图，然后用 Loft

# 草图1: 底部点 (z=-50mm, r=0)
print("Creating sketch 1 (bottom point)...")
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, -0.05, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, 0.001)  # 极小圆作为点
skm.InsertSketch(True)
time.sleep(0.2)

# 草图2: 赤道 (z=0, r=50mm)
print("Creating sketch 2 (equator)...")
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, R)
skm.InsertSketch(True)
time.sleep(0.2)

# 草图3: 顶部点 (z=50mm, r=0)
print("Creating sketch 3 (top point)...")
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0.05, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, 0.001)
skm.InsertSketch(True)
time.sleep(0.2)

# 尝试 Loft
print("\nTrying Loft...")
try:
    # 先选中所有草图
    model.ClearSelection2(True)
    sk = model.GetFirstSketch
    sketches = []
    while sk:
        try:
            sk.Select2(True, 0)
            sketches.append(sk.Name)
        except:
            pass
        try:
            sk = sk.GetNextSketch
        except:
            break
    
    print(f"Selected sketches: {sketches}")
    
    # Loft
    feat = fm.FeatureLoft(
        True, False, False, False, False, 0, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, False, False, 0, 0, False)
    print(f"Loft: {feat is not None}")
    if feat:
        print(f"  Type: {feat.GetTypeName()}, Name: {feat.Name}")
except Exception as e:
    print(f"Loft error: {e}")

# 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 体积
try:
    mp = model.GetMassProperties
    vol = mp[3] * 1e9
    expected = 4/3 * math.pi * R**3 * 1e9
    print(f"Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
except:
    print("Volume: N/A")

# 截图
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_loft.png"))

print("Done!")