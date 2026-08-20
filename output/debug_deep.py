# -*- coding: utf-8 -*-
"""深度诊断：创建球体后立即检查特征树"""
import os, sys, time, math, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

m = swapi.new_part()
m.begin_sketch("Front Plane")

r = 50
n = 24
pts = []
for i in range(n + 1):
    angle = -math.pi/2 + (math.pi * i / n)
    pts.append((r * math.cos(angle), r * math.sin(angle)))
m.polyline(pts)
m.line(0, 50, 0, -50)
m.centerline(0, -65, 0, 65)
m.end_sketch()

# 检查当前特征数
try:
    print(f"Before revolve - Doc type: {m.model.GetType}")
except Exception as e:
    print(f"Before revolve - GetType error: {e}")

# 枚举特征
def list_features(model, label):
    print(f"\n--- {label} ---")
    feat = model.FirstFeature
    while feat:
        try:
            print(f"  {feat.Name} ({feat.GetTypeName})")
        except:
            print(f"  <unknown>")
        try:
            feat = feat.GetNextFeature()
        except:
            feat = None

list_features(m.model, "Before revolve")

# 尝试 revolve
feat = m.fm.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, math.radians(360), 0,
    False, False, 0, 0, 0, 0, 0,
    True, False, True
)
print(f"\nFeatureRevolve2 returned: {feat}")
if feat:
    try:
        print(f"  Type: {feat.GetTypeName}")
        print(f"  Name: {feat.Name}")
    except Exception as e:
        print(f"  Name error: {e}")

# 再次枚举特征
list_features(m.model, "After revolve")

# 重建
try:
    m.model.ForceRebuild3(True)
    print("ForceRebuild3 OK")
except Exception as e:
    print(f"ForceRebuild3 error: {e}")

list_features(m.model, "After rebuild")

# 保存
out_path = os.path.join(out_dir, "DSH_sphere.SLDPRT")
m.save(out_path)
print(f"\nSaved: {out_path}")

# 截图
m.set_view_iso()
m.zoom_to_fit()
time.sleep(1)
m.screenshot(os.path.join(out_dir, "sphere_debug2.png"))

print("Done")