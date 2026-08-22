# -*- coding: utf-8 -*-
import os, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
from swapi import MM

print("========== Final Verification ==========")

sw = swapi.get_sw()
m = swapi.new_part()
fm = m.fm
skm = m.skm
ext = m.ext
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# BASE
try: ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
skm.CreateCornerRectangle(-50*MM, 30*MM, 0, 50*MM, -30*MM, 0)
skm.InsertSketch(True)
feat1 = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.030, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"1. Base: {'OK' if feat1 else 'FAIL'}")
mp = m.massprops()
print(f"   Volume: {mp}")

# FACE selection on top face (z=30)
print("\n2. Test FACE selection")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except Exception as e:
    print(f"   Error: {e}")

skm.InsertSketch(True)
print(f"   ActiveSketch: {skm.ActiveSketch is not None}")
print(f"   AddToDB: {skm.AddToDB}")

if skm.ActiveSketch is not None:
    print("   Sketch ACTIVE!")
    r = skm.CreateCornerRectangle(-50*MM, 30*MM, 0, 50*MM, 80*MM, 0)
    print(f"   Rect: {len(r) if r else 0} segs")
    skm.InsertSketch(True)
    feat2 = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.050, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
    print(f"   Extrude: {'OK' if feat2 else 'FAIL'}")

# FEATURE CUT test
print("\n3. Test FeatureCut3 with FACE selection")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0.030, 0.055, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
print(f"   ActiveSketch: {skm.ActiveSketch is not None}")

if skm.ActiveSketch is not None:
    skm.CreateLine(-50*MM, 30*MM, 0, 50*MM, 30*MM, 0)
    skm.CreateLine(50*MM, 30*MM, 0, 0, 80*MM, 0)
    skm.CreateLine(0, 80*MM, 0, -50*MM, 30*MM, 0)
    skm.InsertSketch(True)
    
    feat_cut = fm.FeatureCut3(
        True, False, False, 0, 0, 0.060, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, False, False, True,
        False, False, False, 0, 0, False)
    print(f"   Cut: {'OK' if feat_cut else 'FAIL'}")

print("\n========== Done ==========")
