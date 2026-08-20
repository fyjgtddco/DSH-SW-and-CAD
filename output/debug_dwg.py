# -*- coding: utf-8 -*-
"""诊断 DWG 导出问题"""
import os, json, traceback, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()

part_path = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output\DSH_triangular_pyramid.sldprt"
dwg_path  = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output\DSH_triangular_pyramid.slddrw"
out_dwg   = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output\DSH_triangular_pyramid.dwg"

result = {"part_exists": os.path.exists(part_path), "dwg_exists": os.path.exists(dwg_path)}

try:
    # 打开工程图
    doc_type = 3  # swDrawing
    errs = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
    warns = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(dwg_path, doc_type, 1, "", errs, warns)
    result["open_ok"] = doc is not None
    result["open_errs"] = int(errs.Value) if doc else None
    result["open_warns"] = int(warns.Value) if doc else None

    if doc is not None:
        ext = doc.Extension

        # 列出可用翻译器
        try:
            translators = ext.GetTranslatedFileNames(out_dwg, 0)
            result["translators"] = str(translators)
        except Exception as e:
            result["translators_err"] = str(e)

        # 方法1: ExportFile (fileFormat=0 DWG, 1 DXF, 2 PDF)
        for fmt, name in [(0, "DWG"), (1, "DXF")]:
            try:
                e = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
                w = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
                out = out_dwg if fmt == 0 else out_dwg.replace('.dwg', '.dxf')
                ok = ext.ExportFile(out, fmt, None, e, w)
                result[f"export_fmt{fmt}_{name}"] = {"ok": bool(ok), "errs": int(e.Value) if ok else int(e.Value)}
                if ok:
                    result["exported_path"] = out
                    result["exported_exists"] = os.path.exists(out)
                    break
            except Exception as ex:
                result[f"export_fmt{fmt}_{name}_except"] = str(ex)

        # 方法2: SaveAs3 直接指定 .dwg 扩展名
        try:
            rc = doc.SaveAs3(out_dwg, 0, 2)
            result["saveas3_dwg"] = {"rc": rc, "exists": os.path.exists(out_dwg)}
        except Exception as e:
            result["saveas3_err"] = str(e)

        # 方法3: SaveAs3 指定 .slddrw 然后改名
        try:
            rc2 = doc.SaveAs3(dwg_path, 0, 2)
            result["saveas3_slddrw"] = {"rc": rc2, "exists": os.path.exists(dwg_path)}
        except Exception as e:
            result["saveas3_err2"] = str(e)

        doc.CloseDoc()

except Exception as e:
    result["error"] = str(e)

print(json.dumps(result, ensure_ascii=False, indent=2))