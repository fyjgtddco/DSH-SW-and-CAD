"""调试：看看 select_plane 为什么失败"""
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
skm = model.SketchManager
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 测试 select_plane 的内部逻辑
name = "前视基准面"
print(f"Testing: {name}")
print(f"  _PLANES_ZH = {swapi._PLANES_ZH}")
print(f"  name in _PLANES_ZH: {name in swapi._PLANES_ZH}")

# 直接调用 SelectByID2
model.ClearSelection2(True)
result = ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"  SelectByID2 result: {result}")

# 尝试 InsertSketch
skm.InsertSketch(True)
print("  InsertSketch(True) called")

# 检查当前是否有活动草图
try:
    active = skm.ActiveSketch
    print(f"  ActiveSketch: {active}")
except Exception as e:
    print(f"  ActiveSketch error: {e}")

# 结束草图
skm.InsertSketch(False)
print("  InsertSketch(False) called")

# 检查特征
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

print("Done")