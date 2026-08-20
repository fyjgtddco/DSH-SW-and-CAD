# -*- coding: utf-8 -*-
"""诊断 revolve 是否生效"""
import os, json, sys, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    m.circle(50, 0, 50)
    m.centerline(0, -60, 0, 60)
    m.end_sketch()
    
    # 检查草图状态
    sk = m.skm.ActiveSketch
    print(f"After end_sketch - ActiveSketch: {sk}")
    
    # 调用 revolve
    feat = m.fm.FeatureRevolve2(
        True, True, False, False, False, False,
        0, 0, math.radians(360), 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True
    )
    print(f"FeatureRevolve2 result: {feat}")
    print(f"Feature is None: {feat is None}")
    
    # 检查特征数量
    try:
        feat_count = m.model.FeatureCount
        print(f"Feature count: {feat_count}")
    except Exception as e:
        print(f"FeatureCount error: {e}")
    
    # 检查第一个特征
    try:
        first_feat = m.model.FirstFeature
        print(f"First feature: {first_feat}")
        if first_feat:
            print(f"  Name: {first_feat.Name}")
            print(f"  Type: {first_feat.GetTypeName}")
    except Exception as e:
        print(f"FirstFeature error: {e}")
    
    # 列出所有特征
    try:
        feat = m.model.FirstFeature
        while feat is not None:
            try:
                print(f"  Feature: {feat.Name} ({feat.GetTypeName})")
            except:
                print(f"  Feature: <unknown>")
            try:
                feat = feat.GetNextFeature
            except:
                feat = None
    except Exception as e:
        print(f"Feature enumeration error: {e}")
    
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_sphere_debug.sldprt")
    rc = m.save(out_path)
    print(f"Save: {rc}")
    
    m.set_view_iso()
    m.zoom_to_fit()
    shot = m.screenshot(os.path.join(out_dir, "sphere_debug.png"))
    print(f"Screenshot: {shot}")
    
    print(json.dumps({"ok": True, "feat_created": feat is not None}))
except Exception as e:
    import traceback
    print(f"ERROR: {e}")
    print(traceback.format_exc())
    print(json.dumps({"ok": False, "error": str(e)}))