"""详细调试：看 select_plane 和 InsertSketch 的实际行为"""
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

print(f"New part created: {model is not None}")
print(f"Title: {model.GetTitle}")
print(f"Type: {model.GetType}")

# 测试 select_plane
m = swapi.SWModel(sw, model)
print(f"\nTesting begin_sketch('前视基准面'):")
try:
    m.begin_sketch("前视基准面")
    print("  SUCCESS!")
except Exception as e:
    print(f"  FAILED: {e}")

print("\nDone")