"""创建球体：圆+中心线→旋转360°"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

# 关闭所有文档
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 1. new part
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. select plane
try:
    ext.SelectByID2("Front Plane", "PLANE", 0.0, 0.0, 0.0, False, 0, empty, 0)
except Exception:
    pass

# 3. begin sketch
skm.InsertSketch(True)

# 4. draw circle - but offset from centerline so revolve works
# Circle center at (0.025, 0), radius 0.025
# This creates a circle that touches the centerline at origin
# For a proper sphere, we need the circle to NOT touch the axis
# So center at (0.03, 0), radius 0.025 -> sphere will have R=0.025, centered at (0.03, 0, 0)
# Wait, that's not a sphere centered at origin...
#
# Actually, for a sphere, we need a half-circle profile revolved around centerline
# Let me use a circle centered at (0.025, 0) with radius 0.025
# The circle goes from (0,0) to (0.05, 0) - it touches the axis at origin
# Revolving this won't work (we proved that earlier)
#
# Alternative: use two arcs to create a proper half-circle profile
# Or: create a circle at (0.06, 0) with radius 0.05 -> revolve creates a torus
# Then cut the inner part...
#
# Simplest approach: create a circle NOT touching the axis, revolve to get a sphere
# Wait, a circle revolved around an axis that doesn't intersect it creates a TORUS, not a sphere!
#
# To create a sphere by revolve, we need a SEMI-CIRCLE profile:
# The half-circle arc from bottom to top, with the diameter as a line, and centerline on the left
# The half-circle must be entirely on one side of the centerline
#
# Let me use: semicircle arc from (0, -0.05) to (0, 0.05), passing through (0.05, 0)
# Centerline at x=-0.001 (just to the left of the arc)
# This way the arc is always to the right of the centerline

R = 0.05  # sphere radius 50mm

# Draw semicircle arc using multiple line segments
n = 32
for i in range(n):
    a1 = -math.pi/2 + (math.pi * i / n)
    a2 = -math.pi/2 + (math.pi * (i+1) / n)
    x1 = R * math.cos(a1) * 0.999  # slightly offset to avoid touching axis
    y1 = R * math.sin(a1)
    x2 = R * math.cos(a2) * 0.999
    y2 = R * math.sin(a2)
    skm.CreateLine(x1, y1, 0, x2, y2, 0)
    time.sleep(0.005)

# Close the profile with a vertical line
skm.CreateLine(R * 0.999, -R, 0, R * 0.999, R, 0)

# Centerline (rotation axis) - offset slightly to the left
skm.CreateCenterLine(-0.001, -0.065, 0, -0.001, 0.065, 0)

# 5. end sketch
skm.InsertSketch(True)
time.sleep(0.5)

# 6. check features
print("\nFeatures after sketch:")
feat = model.FirstFeature
while feat:
    try:
        print(f"  {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 7. revolve
print("\nRevolve...")
feat = fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 6.283185307179586, 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True)
print(f"Result: {feat!r}")
if feat:
    print(f"  Type: {feat.GetTypeName}, Name: {feat.Name}")

# 8. save
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# Check volume
mp = model.GetMassProperties
vol = mp[3] * 1e9
expected = 4/3 * math.pi * 50**3
print(f"Volume: {vol:.1f} mm3 (expected: {expected:.1f} mm3)")
print(f"Match: {abs(vol - expected) / expected < 0.01}")

# 9. screenshot
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "sphere_iso.png"))

print("Done!")