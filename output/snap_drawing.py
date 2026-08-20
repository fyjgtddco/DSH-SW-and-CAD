# -*- coding: utf-8 -*-
"""截图工程图内容"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
import sw_bridge
sw = sw_bridge.get_sw()
out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
dwg_path = os.path.join(out_dir, "DSH_sphere.SLDDRW")
errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.OpenDoc6(dwg_path, 3, 1, "", errs, warns)
time.sleep(2)
sw.Visible = True
swapi._show_main_window(maximize=True)
time.sleep(1)
m = swapi.from_active(sw)
m.zoom_to_fit()
time.sleep(1)
m.screenshot(os.path.join(out_dir, "drawing_final.png"))
print("Screenshot saved")