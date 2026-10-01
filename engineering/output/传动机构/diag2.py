# -*- coding: utf-8 -*-
"""诊断2: 枚举特征类型名, 并测试选中矩形段做切除"""
import os, time
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
m.rect(0, 0, 40, 40)
m.end_sketch()
m.extrude(20)

m.begin_sketch_on_face(0, 0, 20)
m.rect(0, 0, 20, 20)
m.end_sketch()

# 枚举特征类型
f = m.model.FirstFeature()
types = []
guard = 0
while f is not None and guard < 200:
    guard += 1
    try:
        tn = f.GetTypeName2()
        nm = f.Name
        types.append((tn, nm))
    except Exception as e:
        types.append(("ERR", repr(e)))
    try:
        f = f.GetNextFeature()
    except Exception:
        break
print("FEATURES:", types)

# 尝试: 直接进入最后一个草图编辑, 选中段
last = None
f = m.model.FirstFeature()
while f is not None:
    try:
        tn = f.GetTypeName2()
        if "ketch" in tn or "Profile" in tn:
            last = f
    except Exception:
        pass
    try:
        f = f.GetNextFeature()
    except Exception:
        break

print("LAST_SKETCH_FEAT:", last.Name if last else None, last.GetTypeName2() if last else None)

if last:
    m.model.ClearSelection2(True)
    try:
        last.Select2(False, 0)
        print("SELECT2_FEAT_OK")
    except Exception as e:
        print("SELECT2_ERR:", repr(e))
    try:
        m.model.EditSketch()
        print("EDITSKETCH_OK")
    except Exception as e:
        print("EDITSKETCH_ERR:", repr(e))
    ok, info = m.select_all_sketch_segments()
    print("SELECT_SEG_OK:", ok, "INFO:", info)
    try:
        c = m.model.SelectionManager.GetSelectedObjectCount2(-1)
        print("COUNT:", c)
    except Exception as e:
        print("CNT_ERR:", repr(e))
    # 尝试 cut
    try:
        feat = m.fm.FeatureCut4(True, False, False, 0, 0, 0.1, 0, False, False, False, False, 0, 0, False, False, False, False, False, False, True, False, False, False, 0, 0, False)
        print("CUT4_FEAT:", feat)
    except Exception as e:
        print("CUT4_ERR:", repr(e))
    try:
        vol = m._body_volume_mm3()
        print("VOL_AFTER:", vol)
    except Exception as e:
        print("VOL_ERR:", repr(e))
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag2.png"))
print("DONE")
