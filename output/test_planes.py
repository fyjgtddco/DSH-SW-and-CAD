"""直接测试 SelectByID2 和各基准面名称"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

# 关闭所有文档
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

ext = model.Extension
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 测试各种基准面名称
plane_names = [
    "Front Plane", "前视基准面",
    "Top Plane", "上视基准面",
    "Right Plane", "右视基准面",
]

print("Testing SelectByID2 for planes:")
for name in plane_names:
    model.ClearSelection2(True)
    try:
        result = ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, empty, 0)
        print(f"  '{name}': {result}")
    except Exception as e:
        print(f"  '{name}': ERROR {e}")

# 尝试直接用 skmgr 开始草图
skm = model.SketchManager
print("\nTrying InsertSketch(True):")
try:
    skm.InsertSketch(True)
    print("  InsertSketch started OK")
    # 画个圆试试
    skm.CreateCircle(0, 0, 0, 50, 0, 0)  # mm
    print("  Circle created")
    skm.InsertSketch(True)
    print("  Sketch ended OK")
except Exception as e:
    print(f"  ERROR: {e}")

# 检查特征
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

print("Done")