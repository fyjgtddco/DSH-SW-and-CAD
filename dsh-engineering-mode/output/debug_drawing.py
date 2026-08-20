# -*- coding: utf-8 -*-
"""诊断：检查工程图里实际有什么视图"""
import os, json, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()

dwg_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.slddrw"
result = {}

try:
    doc_type = 3
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(dwg_path, doc_type, 1, "", errs, warns)
    if doc is None:
        result["error"] = "cannot open drawing"
    else:
        import time; time.sleep(1)
        doc = sw.ActiveDoc
        
        # 列出所有视图
        views_info = []
        v = doc.GetFirstView
        while v is not None:
            try:
                name = v.GetName
                # 获取视图包含的草图/几何信息
                n_sketches = 0
                try:
                    sk = v.GetFirstSketch
                    while sk is not None:
                        n_sketches += 1
                        sk = sk.GetNextSketch
                except:
                    pass
                views_info.append({"name": name, "sketches": n_sketches})
            except Exception as ex:
                views_info.append({"name": "?", "error": str(ex)})
            try:
                v = v.GetNextView
                if v is None:
                    break
                if v.GetName == views_info[0]["name"] if views_info else False:
                    break
            except:
                break
        result["views"] = views_info
        result["n_views"] = len(views_info)
        
        # 检查文档类型
        result["doc_type"] = doc.GetType
        
        # 检查模板信息
        try:
            tmpl_path = doc.GetTemplateName
            result["template"] = tmpl_path
        except:
            result["template"] = "unknown"
        
        doc.CloseDoc()
except Exception as e:
    result["error"] = str(e)

print(json.dumps(result, ensure_ascii=False, indent=2))