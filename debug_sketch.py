# -*- coding: utf-8 -*-
import os, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
from swapi import MM

print("========== Debug Sketch State ==========")

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
print(f"Base: OK")

# Check what happens AFTER the base is created
print("\n=== After base creation ===")
print(f"  ActiveSketch: {skm.ActiveSketch}")
print(f"  AddToDB: {skm.AddToDB}")

# Try: clear selection and immediately start new sketch WITHOUT selecting plane
print("\n=== Try without explicit plane select ===")
m.clear_selection()
# Just call InsertSketch directly
r = skm.InsertSketch(True)
print(f"  InsertSketch(True) returned: {r}")
print(f"  ActiveSketch: {skm.ActiveSketch is not None}")
print(f"  AddToDB: {skm.AddToDB}")

if skm.ActiveSketch is not None:
    print("  Sketch is ACTIVE - creating rectangle")
    r = skm.CreateCornerRectangle(-50*MM, 30*MM, 0, 50*MM, 80*MM, 0)
    print(f"  Rect: {len(r) if r else 0} segs")
    skm.InsertSketch(True)
    feat2 = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.050, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
    print(f"  Extrude: {feat2}")
    vol = m.massprops()["volume_mm3"]
    print(f"  Volume: {vol:.0f}")
else:
    print("  Sketch NOT active")
    
# Try yet another approach: use InsertSketch(False) then True
print("\n=== Try InsertSketch(False) first ===")
m.clear_selection()
try:
    r1 = skm.InsertSketch(False)
    print(f"  InsertSketch(False): {r1}")
    print(f"  ActiveSketch: {skm.ActiveSketch is not None}")
    r2 = skm.InsertSketch(True)
    print(f"  InsertSketch(True): {r2}")
    print(f"  ActiveSketch: {skm.ActiveSketch is not None}")
except Exception as e:
    print(f"  Error: {e}")
