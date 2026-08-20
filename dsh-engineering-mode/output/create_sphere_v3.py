# -*- coding: utf-8 -*-
"""正确创建球体：闭合半圆弧（弧+直径线）+ 中心线→旋转"""
import os, json, sys, time, math, pythoncom, win32com.client
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

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    
    r = 50
    n = 24
    # 半圆弧：从 (0,-50) 经过 (50,0) 到 (0,50)
    pts = []
    for i in range(n + 1):
        angle = -math.pi/2 + (math.pi * i / n)
        pts.append((r * math.cos(angle), r * math.sin(angle)))
    m.polyline(pts)  # 画弧
    
    # 直径线：从 (0,50) 回到 (0,-50) 闭合轮廓
    m.line(0, 50, 0, -50)
    
    # 中心线：旋转轴
    m.centerline(0, -65, 0, 65)
    
    m.end_sketch()
    
    # 旋转
    feat = m.fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, math.radians(360), 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True
    )
    print(f"FeatureRevolve2: {feat}")
    
    if feat is not None:
        m._visual_step("revolve")
        out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
        m.save(out_path)
        m.set_view_iso()
        m.zoom_to_fit()
        m.screenshot(os.path.join(out_dir, "sphere_final.png"))
        print(f"Sphere saved: {out_path}")
        print(json.dumps({"ok": True, "feat_ok": True}))
    else:
        print(json.dumps({"ok": False, "error": "FeatureRevolve2 returned None"}))
        
except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-2000:]}))