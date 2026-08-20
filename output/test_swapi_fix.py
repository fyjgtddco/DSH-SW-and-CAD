"""用 swapi API 创建圆柱体测试修复"""
import os, sys, time
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
m = swapi.new_part()
print(f"New part: {m.title}")

# 画圆并拉伸
m.begin_sketch("Front Plane")
m.circle(0, 0, 50)  # R=50mm
m.end_sketch()

feat = m.extrude(20)
print(f"Extrude: {feat is not None}")

# 检查特征
print("\nFeatures:")
feat = m.model.FirstFeature
while feat:
    try:
        print(f"  {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

# 保存
out_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_cylinder.SLDPRT"
m.save(out_path)
print(f"\nSaved: {out_path}")

# 截图
m.set_view_iso()
time.sleep(1)
m.screenshot(os.path.join(r"C:\Users\j1877\Desktop\DSH-Check\SW", "cylinder_swapi.png"))

print("Done!")