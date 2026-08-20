# -*- coding: utf-8 -*-
"""正确方式创建球体：半圆弧polyline + 中心线→旋转"""
import os, json, sys, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    
    # 半圆弧：从 (0, -50) 到 (0, 50)，经过 (50, 0)
    # 用多段线近似半圆
    r = 50
    n = 24  # 24段，足够平滑
    pts = []
    for i in range(n + 1):
        angle = -math.pi/2 + (math.pi * i / n)  # -90° to 90°
        pts.append((r * math.cos(angle), r * math.sin(angle)))
    m.polyline(pts)
    m.centerline(0, -65, 0, 65)  # Y轴旋转轴
    m.end_sketch()
    
    # 旋转360°生成球体
    feat = m.fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, math.radians(360), 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True
    )
    m._visual_step("revolve")
    
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_sphere.sldprt")
    m.save(out_path)
    m.set_view_iso()
    m.zoom_to_fit()
    m.screenshot(os.path.join(out_dir, "sphere_iso.png"))
    print(json.dumps({"ok": True, "path": out_path, "feat_ok": feat is not None}, ensure_ascii=False))
except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-2000:]}, ensure_ascii=False))