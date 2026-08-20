"""测试：不同距离的圆与中心线的旋转"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 测试不同偏移距离
distances = [0.06, 0.05, 0.04, 0.03, 0.025, 0.02]

for dist in distances:
    # 关闭所有文档，创建新零件
    try:
        while sw.ActiveDoc:
            sw.ActiveDoc.CloseDoc()
            time.sleep(0.2)
    except:
        pass
    
    tmpl = swapi.get_part_template(sw)
    model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
    time.sleep(0.5)
    
    skm = model.SketchManager
    fm = model.FeatureManager
    ext = model.Extension
    empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    
    # 选前视基准面
    model.ClearSelection2(True)
    ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
    
    # 开始草图
    skm.InsertSketch(True)
    
    # 画圆（距离中心线不同距离）
    skm.CreateCircleByRadius(dist, 0, 0, 0.02)
    
    # 中心线
    skm.CreateCenterLine(0, -0.06, 0, 0, 0.06, 0)
    
    # 结束草图
    skm.InsertSketch(True)
    time.sleep(0.3)
    
    # 旋转
    try:
        feat = fm.FeatureRevolve2(
            True, True, False, False, False, False,
            0, 0, 6.283185307179586, 0,
            False, False, 0, 0, 0, 0, 0,
            True, False, True)
        ok = feat is not None
    except Exception as e:
        ok = False
        print(f"  dist={dist}: ERROR {e}")
    
    print(f"  dist={dist}: {'OK' if ok else 'FAIL'}")
    
    if ok:
        model.SaveAs3(os.path.join(out_dir, f"sphere_dist_{dist}.SLDPRT"), 0, 2)

print("\nDone!")