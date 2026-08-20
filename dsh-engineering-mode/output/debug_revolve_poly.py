# -*- coding: utf-8 -*-
"""尝试用半圆弧polyline + 中心线画球"""
import os, json, sys, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    
    # 画半圆：从 (0, -50) 到 (0, 50)，经过 (50, 0)
    # 用 polyline 近似半圆（用多个小线段）
    n_segments = 20
    r = 50
    pts = []
    for i in range(n_segments + 1):
        angle = -math.pi/2 + (math.pi * i / n_segments)  # -90° to 90°
        x = r * math.cos(angle)
        y = r * math.sin(angle)
        pts.append((x, y))
    m.polyline(pts)
    m.centerline(0, -60, 0, 60)  # Y轴为旋转轴
    m.end_sketch()
    
    # 尝试 revolve
    feat = m.fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, math.radians(360), 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True
    )
    print(f"FeatureRevolve2 result: {feat}")
    
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_sphere_poly.sldprt")
    rc = m.save(out_path)
    m.set_view_iso()
    m.zoom_to_fit()
    shot = m.screenshot(os.path.join(out_dir, "sphere_poly.png"))
    print(f"Save: {rc}")
    print(f"Shot: {shot}")
    print(json.dumps({"ok": True, "feat": feat is not None}))
except Exception as e:
    import traceback
    print(f"ERROR: {e}")
    print(traceback.format_exc())
    print(json.dumps({"ok": False, "error": str(e)}))