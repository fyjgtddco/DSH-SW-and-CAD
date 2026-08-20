# -*- coding: utf-8 -*-
import os, json, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()

dwg_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.slddrw"
out_dwg  = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.dwg"

try:
    doc_type = 3
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(dwg_path, doc_type, 1, "", errs, warns)
    import time; time.sleep(2)
    doc = sw.ActiveDoc
    rc = doc.SaveAs3(out_dwg, 0, 2)
    result = {"ok": os.path.exists(out_dwg), "size_kb": os.path.getsize(out_dwg)//1024 if os.path.exists(out_dwg) else 0}
except Exception as e:
    result = {"ok": False, "error": str(e)}
print(json.dumps(result, ensure_ascii=False))