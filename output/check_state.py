"""检查当前零件状态"""
import sys, time
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

m = swapi.from_active(sw)
print(f"Active doc: {m.title}, type: {m.model.GetType}")

# 特征列表
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

# 截图
m.set_view_iso()
time.sleep(1)
m.screenshot(r"C:\Users\j1877\Desktop\DSH-Check\SW\current_state.png")
print("\nScreenshot saved")