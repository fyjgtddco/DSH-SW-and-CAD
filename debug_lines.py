# -*- coding: utf-8 -*-
import os, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
from swapi import MM

print("========== Test Closed Sketch ==========")

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
print("Base OK")

# TEST 1: Rectangle with explicit CloseSketch
print("\n=== Test 1: Rectangle + CloseSketch ===")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
r = skm.CreateCornerRectangle(-20*MM, -10*MM, 0, 20*MM, 10*MM, 0)
print(f"  Rect: {len(r) if r else 0} segs")
skm.InsertSketch(True)
feat = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.010, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"  Extrude: {'OK' if feat else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

# TEST 2: Triangle with MergePoints
print("\n=== Test 2: Triangle + MergePoints ===")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
r1 = skm.CreateLine(-20*MM, -10*MM, 0, 20*MM, -10*MM, 0)
r2 = skm.CreateLine(20*MM, -10*MM, 0, 0*MM, 20*MM, 0)
r3 = skm.CreateLine(0*MM, 20*MM, 0, -20*MM, -10*MM, 0)
print(f"  Lines: r1={r1 is not None}, r2={r2 is not None}, r3={r3 is not None}")

# Try MergePoints
try:
    akt = skm.ActiveSketch
    if akt is not None:
        merged = akt.MergePoints(0.0005)
        print(f"  MergePoints: {merged}")
except Exception as e:
    print(f"  MergePoints error: {e}")

skm.InsertSketch(True)
feat = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.010, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"  Extrude: {'OK' if feat else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

# TEST 3: Triangle using CreatePolygon
print("\n=== Test 3: CreatePolygon ===")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
try:
    r = skm.CreatePolygon(0*MM, 0*MM, 0, 20*MM, 3, 0)
    print(f"  Polygon: {r}")
except Exception as e:
    print(f"  Polygon error: {e}")

# TEST 4: Use CreateLine with exact same start/end points
print("\n=== Test 4: Exact connection points ===")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
# Use exact same coordinates for connected points
x1, y1 = -20*MM, -10*MM
x2, y2 = 20*MM, -10*MM
x3, y3 = 0*MM, 20*MM
r1 = skm.CreateLine(x1, y1, 0, x2, y2, 0)
r2 = skm.CreateLine(x2, y2, 0, x3, y3, 0)
r3 = skm.CreateLine(x3, y3, 0, x1, y1, 0)
print(f"  Lines: {r1 is not None}, {r2 is not None}, {r3 is not None}")

# Check if sketch has segments
try:
    akt = skm.ActiveSketch
    if akt is not None:
        segs = akt.GetSketchSegments
        print(f"  Segments: {len(segs) if segs else 0}")
        for i, s in enumerate(segs or []):
            print(f"    Seg[{i}]: {s}")
except Exception as e:
    print(f"  Segments error: {e}")

skm.InsertSketch(True)
feat = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.010, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"  Extrude: {'OK' if feat else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

# TEST 5: Use polyline to create closed shape
print("\n=== Test 5: Polyline ===")
m.clear_selection()
try: ext.SelectByID2("", "FACE", 0, 0, 0.030, False, 0, empty, 0)
except: pass
skm.InsertSketch(True)
try:
    r = skm.CreatePolyline([(-20*MM, -10*MM, 0), (20*MM, -10*MM, 0), (0*MM, 20*MM, 0), (-20*MM, -10*MM, 0)])
    print(f"  Polyline: {r}")
except Exception as e:
    print(f"  Polyline error: {e}")

skm.InsertSketch(True)
feat = fm.FeatureExtrusion3(True, False, False, 0, 0, 0.010, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"  Extrude: {'OK' if feat else 'FAIL'}, vol={m.massprops()['volume_mm3']:.0f}")

print("\n========== Done ==========")
