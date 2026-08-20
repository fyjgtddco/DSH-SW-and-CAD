"""检查零件的实际几何体"""
import os, sys, time
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_cylinder.SLDPRT"

# 关闭所有文档
try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

# 打开零件
errs = __import__('win32com.client').client.VARIANT(__import__('pythoncom').VT_BYREF | __import__('pythoncom').VT_I4, 0)
warns = __import__('win32com.client').client.VARIANT(__import__('pythoncom').VT_BYREF | __import__('pythoncom').VT_I4, 0)
sw.OpenDoc6(path, 1, 1, "", errs, warns)
time.sleep(1)

d = sw.ActiveDoc
print(f"Doc: {d.GetTitle}")

# 检查实体
print("\nBodies:")
try:
    body = d.FirstBody
    while body:
        print(f"  Body: {body.Name}")
        try:
            body = body.GetNextBody
        except:
            break
except Exception as e:
    print(f"  Error: {e}")

# 检查草图
print("\nSketches:")
try:
    sk = d.GetFirstSketch
    while sk:
        print(f"  Sketch: {sk.Name}")
        try:
            sk = sk.GetNextSketch
        except:
            break
except Exception as e:
    print(f"  Error: {e}")

# 质量
try:
    mp = d.GetMassProperties
    vol = mp[3] * 1e9
    print(f"\nVolume: {vol:.1f} mm3")
except Exception as e:
    print(f"\nMassProps error: {e}")

# 截图
d.ShowNamedView2("*Isometric", 0)
d.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(r"C:\Users\j1877\Desktop\DSH-Check\SW\cylinder_check.png")

print("Done")