"""模仿 cmd_sketch_rect 的流程画圆+拉伸"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 1. new
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

skm = model.SketchManager
fm = model.FeatureManager
ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 2. select plane (same as cmd_sketch_rect)
try:
    ext.SelectByID2("Front Plane", "PLANE", 0.0, 0.0, 0.0, False, 0, empty, 0)
except Exception:
    pass

# 3. begin sketch (toggle)
skm.InsertSketch(True)

# 4. draw circle (mm -> m)
r = 50
skm.CreateCircleByRadius(0, 0, 0, r / 1000.0)

# 5. end sketch (toggle)
skm.InsertSketch(True)

# 6. extrude (same as cmd_sketch_rect)
depth = 20
feat = fm.FeatureExtrusion3(
    True, False, False, 0, 0, depth / 1000.0, 0, False, False, False, False,
    0, 0, False, False, False, False, True, True, True, 0, 0, False)
print(f"Extrude: {feat is not None}")

# 7. check features
print("\nFeatures:")
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

# 8. save
out_path = os.path.join(out_dir, "DSH_cylinder.SLDPRT")
model.SaveAs3(out_path, 0, 2)
print(f"\nSaved: {out_path}, {os.path.getsize(out_path)//1024}KB")

# 9. screenshot
model.ShowNamedView2("*Isometric", 0)
model.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "cylinder_sw.png"))

print("Done!")