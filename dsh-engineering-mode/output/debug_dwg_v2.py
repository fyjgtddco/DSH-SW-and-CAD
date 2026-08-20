# -*- coding: utf-8 -*-
"""尝试多种 DWG 导出方式"""
import os, json, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
import pythoncom, win32com.client
sw = sw_bridge.get_sw()

dwg_path = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output\DSH_triangular_pyramid.slddrw"
out_dwg  = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output\DSH_triangular_pyramid.dwg"

result = {"step": "", "ok": False}

try:
    doc_type = 3
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(dwg_path, doc_type, 1, "", errs, warns)

    # 等待工程图加载
    import time; time.sleep(2)

    doc = sw.ActiveDoc
    result["step"] = "opened drawing"

    # 获取 Extension
    ext = doc.Extension

    # 方法1: ExportFile with explicit DWG translator settings
    # SW API: ExportFile(filePath, fileFormat, pData, pErrors, pWarnings)
    # fileFormat: 0=DWG, 1=DXF
    errors_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

    try:
        ok = ext.ExportFile(out_dwg, 0, None, errors_var, warnings_var)
        result["exportFile_ok"] = bool(ok)
        result["exportFile_errors"] = int(errors_var)
        result["exportFile_warnings"] = int(warnings_var)
        if ok:
            result["ok"] = True
            result["dwg_path"] = out_dwg
            result["dwg_size"] = os.path.getsize(out_dwg) if os.path.exists(out_dwg) else 0
    except Exception as e:
        result["exportFile_except"] = str(e)

    # 方法2: SaveAs with .dwg extension (trick)
    if not result["ok"]:
        try:
            rc = doc.SaveAs3(out_dwg, 0, 2)
            result["saveAs3_dwg"] = {"rc": rc, "exists": os.path.exists(out_dwg)}
            if os.path.exists(out_dwg):
                result["ok"] = True
                result["dwg_size"] = os.path.getsize(out_dwg)
        except Exception as e:
            result["saveAs3_err"] = str(e)

    # 方法3: PDF export (fallback - user can convert PDF to DWG manually)
    if not result["ok"]:
        try:
            pdf_path = out_dwg.replace('.dwg', '.pdf')
            rc = doc.SaveAs3(pdf_path, 0, 2)
            result["pdf_fallback"] = {"rc": rc, "exists": os.path.exists(pdf_path), "path": pdf_path}
            if os.path.exists(pdf_path):
                result["pdf_path"] = pdf_path
        except Exception as e:
            result["pdf_err"] = str(e)

    doc.CloseDoc()

except Exception as e:
    result["error"] = str(e)
    result["trace"] = __import__('traceback').format_exc()

print(json.dumps(result, ensure_ascii=False, indent=2))