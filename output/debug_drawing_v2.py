# -*- coding: utf-8 -*-
"""诊断：检查工程图视图 - 方法2"""
import os, json, sys, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()

# 直接用 sw_bridge 的 _draw_view_names 函数
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
        
        # 方法1: 用 sw_bridge 的 _draw_view_names
        names = sw_bridge._draw_view_names(doc)
        result["view_names"] = names
        
        # 方法2: 直接遍历视图树
        v = doc.GetFirstView
        view_list = []
        while v is not None:
            info = {"ptr": str(v)}
            try:
                info["name"] = str(v.GetName)
            except Exception as e:
                info["name_err"] = str(e)
            try:
                info["children"] = []
                cv = v.GetFirstSubView
                while cv is not None:
                    try:
                        info["children"].append(str(cv.GetName))
                    except:
                        info["children"].append(str(cv))
                    try:
                        cv = cv.GetNextView
                    except:
                        break
            except:
                pass
            view_list.append(info)
            try:
                v = v.GetNextView
            except:
                break
        result["view_tree"] = view_list
        
        # 截个图看看
        try:
            import swapi
            m = swapi.from_active(sw)
            shot = m.screenshot(os.path.join(os.path.dirname(dwg_path), "drawing_debug.png"))
            result["screenshot"] = shot.get("path")
        except Exception as e:
            result["screenshot_err"] = str(e)
        
        doc.CloseDoc()
except Exception as e:
    result["error"] = str(e)
    import traceback
    result["trace"] = traceback.format_exc()[-2000:]

print(json.dumps(result, ensure_ascii=False, indent=2))