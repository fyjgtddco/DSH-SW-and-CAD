"""详细调试：逐步检查每个步骤"""
import os, sys, time, pythoncom, win32com.client
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

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

ext = model.Extension
skm = model.SketchManager
empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

# 步骤1: 检查当前文档状态
print(f"1. Doc: {model.GetTitle}, Type: {model.GetType}")

# 步骤2: 清除选择
model.ClearSelection2(True)
print("2. Cleared selection")

# 步骤3: 选中前视基准面
result = ext.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, empty, 0)
print(f"3. SelectByID2('前视基准面'): {result}")

# 步骤4: 检查激活的草图
try:
    active = skm.ActiveSketch
    print(f"4. ActiveSketch before InsertSketch: {active}")
except Exception as e:
    print(f"4. ActiveSketch error: {e}")

# 步骤5: 开始草图
print("5. Calling InsertSketch(True)...")
try:
    result = skm.InsertSketch(True)
    print(f"   InsertSketch returned: {result}")
except Exception as e:
    print(f"   InsertSketch error: {e}")

# 步骤6: 检查激活的草图
try:
    active = skm.ActiveSketch
    print(f"6. ActiveSketch after InsertSketch: {active}")
except Exception as e:
    print(f"6. ActiveSketch error: {e}")

# 步骤7: 检查特征
print("7. Features:")
feat = model.FirstFeature
while feat:
    try:
        print(f"   {feat.Name} ({feat.GetTypeName})")
    except:
        pass
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

print("\nDone")