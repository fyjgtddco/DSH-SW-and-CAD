"""检查零件实体"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
part_path = os.path.join(out_dir, "DSH_cylinder.SLDPRT")

# 打开零件
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(1)

d = sw.ActiveDoc

# 检查实体
try:
    body = d.FirstBody
    while body:
        print(f"Body: {body.Name}")
        try:
            body = body.GetNextBody
        except:
            break
except Exception as e:
    print(f"Body error: {e}")

# 检查草图
try:
    sk = d.GetFirstSketch
    while sk:
        print(f"Sketch: {sk.Name}")
        try:
            sk = sk.GetNextSketch
        except:
            break
except Exception as e:
    print(f"Sketch error: {e}")

# 质量属性
try:
    mp = d.GetMassProperties
    if mp:
        vol = mp[3] * 1e9  # m3 to mm3
        print(f"\nVolume: {vol:.1f} mm3")
        print(f"Expected (R50, H20): {3.14159 * 2500 * 20:.1f} mm3")
except Exception as e:
    print(f"MassProps error: {e}")

# 截图
d.ShowNamedView2("*Isometric", 0)
d.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(os.path.join(out_dir, "cylinder_verify2.png"))

print("\nDone")