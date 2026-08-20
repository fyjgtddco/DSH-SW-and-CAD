# -*- coding: utf-8 -*-
"""截图工程图，看看实际内容"""
import os, json, sys, pythoncom, win32com.client, time
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

dwg_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.slddrw"
out_dir  = r"C:\Users\j1877\Desktop\DSH-Check\SW"

# 1. 先打开零件
part_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.sldprt"
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(part_path, 1, 1, "", errs, warns)
time.sleep(1)

# 2. 截图零件
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)
m = swapi.from_active(sw)
m.set_view_iso()
m.zoom_to_fit()
time.sleep(0.5)
part_shot = m.screenshot(os.path.join(out_dir, "part_check.png"))
print(f"Part screenshot: {part_shot}")

# 3. 打开工程图
doc = sw.OpenDoc6(dwg_path, 3, 1, "", errs, warns)
time.sleep(1)
doc = sw.ActiveDoc

# 4. 截图工程图
try:
    doc.ViewZoomtofit2()
    time.sleep(0.5)
    m2 = swapi.from_active(sw)
    drawing_shot = m2.screenshot(os.path.join(out_dir, "drawing_check.png"))
    print(f"Drawing screenshot: {drawing_shot}")
except Exception as e:
    print(f"Drawing screenshot error: {e}")

# 5. 尝试用另一种方式获取视图 - 通过 Sheet
try:
    sheet = doc.GetCurrentSheet
    print(f"Sheet name: {sheet.GetName}")
    # 获取图纸上的视图
    v = sheet.GetFirstView
    while v is not None:
        try:
            name = v.Name
            print(f"  View: {name}")
        except:
            try:
                name = v.GetName
                print(f"  View (GetName): {name}")
            except:
                print(f"  View: <unknown>")
        try:
            v = v.GetNextView
            if v is None:
                break
        except:
            break
except Exception as e:
    print(f"Sheet access error: {e}")

print("Done")