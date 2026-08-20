"""穷举所有球体创建方法"""
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

results = []

# ============================================================
# 方法1: FeatureSphere
# ============================================================
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)
fm = model.FeatureManager
try:
    feat = fm.FeatureSphere(0.05, 0, 0, 0, False)
    results.append(("FeatureSphere", feat is not None, str(feat) if feat else ""))
    print(f"M1 FeatureSphere: {'OK' if feat else 'FAIL'} - {feat}")
except Exception as e:
    results.append(("FeatureSphere", False, str(e)[:100]))
    print(f"M1 FeatureSphere: ERROR - {e}")

# ============================================================
# 方法2: Modeler.CreateSphere
# ============================================================
try:
    modeler = model.Modeler
    body = modeler.CreateSphere(0, 0, 0, 0.05, 16, 16)
    results.append(("Modeler.CreateSphere", body is not None, str(body) if body else ""))
    print(f"M2 Modeler.CreateSphere: {'OK' if body else 'FAIL'}")
except Exception as e:
    results.append(("Modeler.CreateSphere", False, str(e)[:100]))
    print(f"M2 Modeler.CreateSphere: ERROR - {e}")

# ============================================================
# 方法3: 半圆弧旋转（弧不接触中心线）
# ============================================================
# 关重新开
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

model = sw.NewDocument(swapi.get_part_template(sw), 0, 0.1, 0.1)
time.sleep(1)
skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)

R = 0.05
# 半圆弧：圆心在(R, 0)，从底部到顶部，完全不接触y轴
# 用线段逼近半圆，每段都在x>0区域
n = 32
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    x1 = R + R * math.cos(a1)  # x从R到R
    y1 = R * math.sin(a1)
    x2 = R + R * math.cos(a2)
    y2 = R * math.sin(a2)
    skm.CreateLine(x1, y1, 0, x2, y2, 0)
    time.sleep(0.005)

# 中心线在x=-0.001，让半圆弧完全在其右侧
skm.CreateCenterLine(-0.001, -0.065, 0, -0.001, 0.065, 0)
skm.InsertSketch(True)
time.sleep(0.5)

try:
    feat = fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, 6.283185307179586, 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True)
    ok = feat is not None
    results.append(("Revolve半圆弧(不接触轴)", ok, feat.Name if feat else ""))
    print(f"M3 Revolve半圆弧(不接触轴): {'OK' if ok else 'FAIL'}")
except Exception as e:
    results.append(("Revolve半圆弧(不接触轴)", False, str(e)[:100]))
    print(f"M3 Revolve半圆弧(不接触轴): ERROR - {e}")

# ============================================================
# 方法4: 圆旋转（生成环面，非球体，但确认revolve本身可用）
# ============================================================
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

model = sw.NewDocument(swapi.get_part_template(sw), 0, 0.1, 0.1)
time.sleep(1)
skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0.06, 0, 0, 0.02)
skm.CreateCenterLine(0, -0.065, 0, 0, 0.065, 0)
skm.InsertSketch(True)
time.sleep(0.3)

feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
ok = feat is not None
results.append(("Revolve圆(环面)", ok, "torus created" if ok else ""))
print(f"M4 Revolve圆(环面): {'OK' if ok else 'FAIL'}")

# ============================================================
# 方法5: Loft (放样)
# ============================================================
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

model = sw.NewDocument(swapi.get_part_template(sw), 0, 0.1, 0.1)
time.sleep(1)
skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 草图1: 底部小圆
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, -0.049, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, 0.001)
skm.InsertSketch(True)
time.sleep(0.2)

# 草图2: 赤道大圆
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, R)
skm.InsertSketch(True)
time.sleep(0.2)

# 草图3: 顶部小圆
model.ClearSelection2(True)
ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0.049, False, 0, empty, 0)
skm.InsertSketch(True)
skm.CreateCircleByRadius(0, 0, 0, 0.001)
skm.InsertSketch(True)
time.sleep(0.2)

# 尝试选中所有草图
try:
    sk = model.GetFirstSketch
    while sk:
        try:
            sk.Select2(True, 0)
        except:
            pass
        try:
            sk = sk.GetNextSketch
        except:
            break
    print(f"M5 Loft: selected sketches")
    
    feat = fm.FeatureLoft(
        True, False, False, False, False, 0, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, False, False, 0, 0, False)
    ok = feat is not None
    results.append(("Loft放样", ok, feat.Name if feat else ""))
    print(f"M5 Loft: {'OK' if ok else 'FAIL'}")
except Exception as e:
    results.append(("Loft放样", False, str(e)[:100]))
    print(f"M5 Loft: ERROR - {e}")

# ============================================================
# 方法6: FeatureRevolveCut (旋转切除)
# ============================================================
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except Exception:
    pass
    
    model = sw.NewDocument(swapi.get_part_template(sw), 0, 0.1, 0.1)
    time.sleep(1)
    
    # 先建一个圆柱
    skm = model.SketchManager
    fm = model.FeatureManager
    ext = model.Extension
    empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    
    model.ClearSelection2(True)
    ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
    skm.InsertSketch(True)
    skm.CreateCircleByRadius(0, 0, 0, R)
    skm.InsertSketch(True)
    time.sleep(0.3)
    
    feat_cyl = fm.FeatureExtrusion3(
        True, False, False, 0, 0, 0.1, 0, False, False, False, False,
        0, 0, False, False, False, False, True, True, True, 0, 0, False)
    
    # 在圆柱顶面画半圆轮廓做切除
    model.ClearSelection2(True)
    ext.SelectByID2("上视基准面", "PLANE", 0, 0, 0.05, False, 0, empty, 0)
    skm.InsertSketch(True)
    
    # 画半圆（从右边到左边，通过中心）
    n = 32
    for i in range(n):
        a1 = (math.pi * i / n)
        a2 = (math.pi * (i+1) / n)
        x1 = R * math.cos(a1)
        y1 = R * math.sin(a1)
        x2 = R * math.cos(a2)
        y2 = R * math.sin(a2)
        skm.CreateLine(x1, y1, 0, x2, y2, 0)
        time.sleep(0.005)
    
    skm.CreateCenterLine(-0.001, 0, 0, 0.001, 0, 0)
    skm.InsertSketch(True)
    time.sleep(0.3)
    
    feat_cut = fm.FeatureRevolve2(
        True, True, False, True, False, False,
        0, 0, 6.283185307179586, 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True)
    ok = feat_cut is not None
    results.append(("RevolveCut旋转切除", ok, feat_cut.Name if feat_cut else ""))
    print(f"M6 RevolveCut: {'OK' if ok else 'FAIL'}")
    
    if ok:
        mp = model.GetMassProperties
        vol = mp[3] * 1e9
        expected = 4/3 * math.pi * R**3 * 1e9
        print(f"  Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
    
except Exception as e:
    results.append(("RevolveCut旋转切除", False, str(e)[:100]))
    print(f"M6 RevolveCut: ERROR - {e}")

# ============================================================
# 方法7: 先画圆拉伸成球状（用多个截面）
# ============================================================
# 这个方法本质上和loft一样，跳过

# ============================================================
# 方法8: 检查是否有内置的球体模板
# ============================================================
tmpl_dir = r"C:\ProgramData\SolidWorks\SOLIDWORKS 2020\templates"
import glob
sldprt_tmpls = glob.glob(os.path.join(tmpl_dir, "*.prtdot"))
sphere_templates = [t for t in sldprt_tmpls if 'sphere' in t.lower() or 'ball' in t.lower()]
results.append(("球体模板", len(sphere_templates) > 0, f"{len(sphere_templates)} templates found"))
print(f"M8 球体模板: {'OK' if sphere_templates else 'None found'} - {sphere_templates}")

# ============================================================
# 汇总
# ============================================================
print("\n" + "="*60)
print("测试结果汇总:")
print("="*60)
for name, ok, detail in results:
    status = "✅ OK" if ok else "❌ FAIL"
    print(f"  {status}  {name}: {detail}")

# 保存最终文件
model.SaveAs3(os.path.join(out_dir, "DSH_sphere_test.SLDPRT"), 0, 2)
print(f"\nSaved test result: {os.path.join(out_dir, 'DSH_sphere_test.SLDPRT')}")