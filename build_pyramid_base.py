# -*- coding: utf-8 -*-
"""
三角锥 + 长方体底座 - 最终完整脚本
关键技巧:
  1. 第一个特征用 PLANE 基准面
  2. 后续草图用 FACE 选择进入模式
  3. CreateLine 创建的开放轮廓需要 MergePoints 闭合才能拉伸
"""
import os, sys, pythoncom, win32com.client

sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
from swapi import MM

print("========== 三角锥+底座 建模 ==========")
print("规格: 底座100x60x30mm, 三角锥底100x60高50mm, 总高80mm")

sw = swapi.get_sw()
m = swapi.new_part()
fm = m.fm
skm = m.skm
ext = m.ext
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

def make_triangle_pad(face_type, px, py, pz, local_pts, depth_mm):
    """在指定面上画三角形并拉伸成Pad"""
    m.clear_selection()
    try:
        ext.SelectByID2("", face_type, px*MM, py*MM, pz*MM, False, 0, empty, 0)
    except:
        return None
    skm.InsertSketch(True)
    if skm.ActiveSketch is None:
        return None
    
    # Draw triangle lines
    for i in range(len(local_pts)):
        x1, y1 = local_pts[i]
        x2, y2 = local_pts[(i+1) % len(local_pts)]
        skm.CreateLine(x1*MM, y1*MM, 0, x2*MM, y2*MM, 0)
    
    # Merge points to close the sketch
    try:
        skm.ActiveSketch.MergePoints(0.0005)
    except:
        pass
    
    skm.InsertSketch(True)
    
    # Extrude
    feat = fm.FeatureExtrusion3(
        True, False, False, 0, 0, depth_mm*MM, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, True, True, True, 0, 0, False)
    return feat

# ============================================================
# STEP 1: 底座 100x60x30mm
# ============================================================
print("\n[STEP 1] 底座")
try: ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
skm.CreateCornerRectangle(-50*MM, 30*MM, 0, 50*MM, -30*MM, 0)
skm.InsertSketch(True)
feat_base = fm.FeatureExtrusion3(
    True, False, False, 0, 0, 0.030, 0,
    False, False, False, False, 0, 0,
    False, False, False, False, True, True, True, 0, 0, False)
print(f"  底座: {'OK' if feat_base else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

# ============================================================
# STEP 2: 三角锥 - 用4个三角面Pad合并
# ============================================================
print("\n[STEP 2] 前三角面 Pad (Front, Y=+30)")
feat_front = make_triangle_pad(
    "FACE", 0, 0.030, 0.055,   # on front face
    [(-50, 0), (50, 0), (0, 50)],  # triangle in local coords
    60)  # extrude depth = 60mm (full width)
print(f"  前三角面: {'OK' if feat_front else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

print("\n[STEP 3] 后三角面 Pad (Back, Y=-30)")
feat_back = make_triangle_pad(
    "FACE", 0, -0.030, 0.055,
    [(-50, 0), (50, 0), (0, 50)],
    60)
print(f"  后三角面: {'OK' if feat_back else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

print("\n[STEP 4] 右三角面 Pad (Right, X=+50)")
feat_right = make_triangle_pad(
    "FACE", 0.050, 0, 0.055,
    [(-30, 0), (30, 0), (0, 50)],
    100)
print(f"  右侧三角面: {'OK' if feat_right else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

print("\n[STEP 5] 左三角面 Pad (Left, X=-50)")
feat_left = make_triangle_pad(
    "FACE", -0.050, 0, 0.055,
    [(-30, 0), (30, 0), (0, 50)],
    100)
print(f"  左侧三角面: {'OK' if feat_left else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

# ============================================================
# SAVE
# ============================================================
print("\n[FINAL] 最终结果")
mp = m.massprops()
vol = mp['volume_mm3']
print(f"  体积: {vol:.0f} mm3")
print(f"  期望: 280000 mm3 (底座180000 + 锥体100000)")

out_dir = r"C:\Users\j1877\Desktop\DSH-Check"
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "DSH_三角锥底座.sldprt")
result = m.save(out_path)
print(f"  零件: {result['path']}")

m.set_view_iso()
shot = m.screenshot(os.path.join(out_dir, "DSH_三角锥底座_iso.png"))
print(f"  截图: {shot['path']}")

print("\n========== 建模完成 ==========")
