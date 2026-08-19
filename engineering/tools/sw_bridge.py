# -*- coding: utf-8 -*-
"""
SolidWorks Bridge — DeepSeek Harness 连接 SolidWorks 的桥接脚本（通用版）
========================================================================
本文件是【通用版】：不硬编码任何本机路径或版本号。
- 模板位置自动探测（见 swapi.get_part_template）
- 版本相关枚举自动适配（见 swapi）
- 任何安装了 SolidWorks 的电脑都可用

DSH 通过 pwsh 工具调用本脚本，输出 JSON。

命令模式（封装好的常用操作）:
    python sw_bridge.py status                 # 连接状态 / 版本 / 已开文档
    python sw_bridge.py doctor                 # 环境自检（新电脑先跑这个）
    python sw_bridge.py open <文件路径>          # 打开模型
    python sw_bridge.py new <模板?>             # 新建零件
    python sw_bridge.py info                    # 当前活动文档信息
    python sw_bridge.py list                    # 列出已打开文档
    python sw_bridge.py massprops               # 活动文档质量属性
    python sw_bridge.py close                   # 关闭活动文档
    python sw_bridge.py save <路径>              # 另存为
    python sw_bridge.py sketch-rect <w> <h> <depth>  # 画矩形并拉伸
    python sw_bridge.py export-pdf <路径>        # 导出 PDF

脚本执行模式（DSH 自动生成的建模代码 —— 核心能力）:
    python sw_bridge.py run <script.py> [参数...]
        - 以独立 Python 解释器执行 script.py
        - 脚本中可直接用 `sw` 全局变量（已连好的 SldWorks 对象）
        - 脚本的 stdout / 异常 会被捕获并打包成 JSON 返回

原理:
    SolidWorks 通过 COM (SldWorks.Application) 暴露自动化 API。
    PowerShell 原生 COM 可能因类型库注册不完整而失败（TYPE_E_ELEMENTNOTFOUND），
    但 win32com 的 IDispatch 后期绑定不依赖类型库，因此通用可用。
    pywin32 动态分发下，无参 COM 成员按属性访问（如 sw.RevisionNumber）。

依赖（新电脑安装）:
    pip install pywin32 mss Pillow
"""
import sys
import os
import json
import glob
import traceback
import subprocess

import pythoncom
import win32com.client

# 让本文件所在目录的 swapi.py 可被 import（无论从哪个目录调用本脚本）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _doc_type_name(t):
    return {1: "PART", 2: "ASSEMBLY", 3: "DRAWING"}.get(t, str(t))


def get_sw():
    """连接运行中的 SolidWorks；若未运行则启动它（LocalServer32）。

    通用版：连接后立即禁用草图吸附（不同版本枚举值自动适配）。
    """
    pythoncom.CoInitialize()
    sw = win32com.client.dynamic.Dispatch('SldWorks.Application')
    try:
        import swapi
        swapi._disable_snapping(sw)
    except Exception:
        pass
    return sw


def _prop(obj, name):
    """无参 COM 成员在 dynamic dispatch 下按属性访问。"""
    return getattr(obj, name)


def cmd_status(sw):
    docs = []
    try:
        dl = sw.GetDocuments
        if dl:
            for i, d in enumerate(dl):
                if d:
                    docs.append({
                        "index": i,
                        "title": _prop(d, "GetTitle"),
                        "path": _prop(d, "GetPathName") or "",
                        "type": _doc_type_name(_prop(d, "GetType")),
                    })
    except Exception as e:
        docs = [{"error": str(e)}]
    return {
        "connected": True,
        "revision": sw.RevisionNumber,
        "visible": sw.Visible,
        "pid": sw.GetProcessID,
        "doc_count": sw.GetDocumentCount,
        "docs": docs,
    }


def cmd_doctor(sw):
    """环境自检：新电脑第一次拿到本包时先跑这个。

    检查项：Python 版本 / 依赖库 / SolidWorks 连接 / 模板自动探测 / 截图依赖。
    """
    import platform
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pywin32": None,
        "mss": None,
        "pillow": None,
    }
    try:
        import win32com
        result["pywin32"] = win32com.__file__
    except Exception as e:
        result["pywin32"] = f"MISSING: {e}"
    try:
        import mss
        result["mss"] = getattr(mss, "__version__", "installed")
    except Exception as e:
        result["mss"] = f"MISSING: {e}"
    try:
        import PIL
        result["pillow"] = getattr(PIL, "__version__", "installed")
    except Exception as e:
        result["pillow"] = f"MISSING: {e}"

    # SolidWorks 连接与版本
    try:
        result["solidworks"] = {
            "connected": True,
            "revision": str(sw.RevisionNumber),
            "visible": bool(sw.Visible),
        }
        import swapi
        result["solidworks"]["version_major"] = swapi._version_major(sw)
        tmpl = swapi.get_part_template(sw)
        result["template"] = tmpl or "NOT FOUND (请检查 SolidWorks 模板目录)"
    except Exception as e:
        result["solidworks"] = {"connected": False, "error": str(e)}
    # 结论
    problems = []
    if str(result.get("pywin32", "")).startswith("MISSING") or not result.get("pywin32"):
        problems.append("pywin32 未安装: pip install pywin32")
    if str(result.get("mss", "")).startswith("MISSING") or not result.get("mss"):
        problems.append("mss 未安装: pip install mss")
    if str(result.get("pillow", "")).startswith("MISSING") or not result.get("pillow"):
        problems.append("Pillow 未安装: pip install Pillow")
    if not result.get("solidworks", {}).get("connected"):
        problems.append("无法连接 SolidWorks: 请先启动 SolidWorks")
    if not result.get("template"):
        problems.append("未找到零件模板")
    result["ok"] = len(problems) == 0
    result["problems"] = problems
    return result


def cmd_open(sw, path):
    if not os.path.exists(path):
        return {"ok": False, "error": f"file not found: {path}"}
    ext = os.path.splitext(path)[1].lower()
    doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(ext, 1)
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(path, doc_type, 1, "", errs, warns)
    if doc is None:
        return {"ok": False, "error": "OpenDoc6 returned None"}
    # 打开文件后最大化窗口（用户约定）
    try:
        import swapi
        swapi._show_main_window(maximize=True)
    except Exception:
        pass
    return {
        "ok": True,
        "title": _prop(doc, "GetTitle"),
        "path": _prop(doc, "GetPathName"),
        "type": _doc_type_name(_prop(doc, "GetType")),
    }


def cmd_new(sw, template):
    """新建零件。模板参数可选；不传则自动探测（通用版核心改进）。"""
    cands = [template] if template else []
    if not cands or not os.path.exists(cands[0]):
        import swapi
        auto = swapi.get_part_template(sw)
        cands = [auto] if auto else []
    tmpl = next((c for c in cands if c and os.path.exists(c)), None)
    if tmpl is None:
        return {"ok": False, "error": "no part template found; run 'doctor' to debug"}
    model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
    if model is None:
        return {"ok": False, "error": "NewDocument returned None"}
    return {"ok": True, "title": _prop(model, "GetTitle"), "template": tmpl}


def cmd_info(sw):
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    return {
        "ok": True,
        "title": _prop(d, "GetTitle"),
        "path": _prop(d, "GetPathName") or "",
        "type": _doc_type_name(_prop(d, "GetType")),
        "saved": d.GetSaveFlag if hasattr(d, "GetSaveFlag") else None,
    }


def cmd_list(sw):
    try:
        docs = sw.GetDocuments
        out = []
        if docs:
            for i, d in enumerate(docs):
                if d:
                    out.append({
                        "index": i,
                        "title": _prop(d, "GetTitle"),
                        "path": _prop(d, "GetPathName") or "",
                        "type": _doc_type_name(_prop(d, "GetType")),
                    })
        return {"ok": True, "count": len(out), "docs": out}
    except Exception:
        n = sw.GetDocumentCount
        out = []
        for i in range(n):
            try:
                d = sw.GetDocumentByIndex(i)
            except Exception:
                break
            if d:
                out.append({
                    "index": i,
                    "title": _prop(d, "GetTitle"),
                    "path": _prop(d, "GetPathName") or "",
                    "type": _doc_type_name(_prop(d, "GetType")),
                })
        return {"ok": True, "count": n, "docs": out}


def cmd_massprops(sw):
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    try:
        mp = d.GetMassProperties
        if mp is None or not isinstance(mp, tuple):
            return {"ok": False, "error": f"GetMassProperties returned {mp!r}"}
        vals = [float(x) for x in mp]
        return {
            "ok": True,
            "volume_m3": vals[0],
            "surface_area_m2": vals[1],
            "mass_kg": vals[2],
            "density_kg_m3": vals[3],
            "center_of_mass_m": [vals[4], vals[5], vals[6]],
            "moments_of_inertia": [vals[7], vals[8], vals[9], vals[10], vals[11]],
            "raw": vals,
        }
    except Exception as e:
        return {"ok": False, "error": f"massprops failed: {e}"}


def cmd_close(sw):
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    title = _prop(d, "GetTitle")
    sw.CloseDoc(title)
    return {"ok": True, "closed": title}


def cmd_save(sw, path):
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    rc = d.SaveAs3(path, 0, 2)  # returns 0 on success
    return {"ok": rc == 0, "path": path, "saved": os.path.exists(path), "rc": rc}


def cmd_sketch_rect(sw, w, h, depth):
    """在前视基准面画矩形并拉伸成方块。"""
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document; run 'new' first"}
    skm = d.SketchManager
    fm = d.FeatureManager
    ext = d.Extension
    w, h, depth = float(w), float(h), float(depth)
    try:
        empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        ext.SelectByID2("Front Plane", "PLANE", 0.0, 0.0, 0.0, False, 0, empty, 0)
    except Exception:
        pass
    skm.InsertSketch(True)
    rect = skm.CreateCornerRectangle(-w / 2, h / 2, 0.0, w / 2, -h / 2, 0.0)
    skm.InsertSketch(True)
    feat = fm.FeatureExtrusion3(
        True, False, False, 0, 0, depth, 0, False, False, False, False,
        0, 0, False, False, False, False, True, True, True, 0, 0, False
    )
    return {"ok": feat is not None, "rect": rect is not None, "extrude": feat is not None}


def cmd_export_pdf(sw, path):
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    rc = d.SaveAs3(path, 0, 0)
    return {"ok": rc == 0, "path": path, "exists": os.path.exists(path), "rc": rc}


def cmd_run(sw, script_path, extra_args):
    """执行 DSH 生成的建模脚本（核心能力）。

    以独立 Python 进程运行 script_path，并把连接好的 `sw` 对象注入为全局变量，
    这样脚本可以直接写 win32com 动态分发代码操作 SolidWorks，无需关心连接细节。

    通用版：桥接目录（本文件所在目录）自动注入 sys.path，
    因此脚本里 `import swapi` 在任何电脑上都能找到同目录的 swapi.py。
    """
    if not os.path.exists(script_path):
        return {"ok": False, "error": f"script not found: {script_path}"}
    wrapper = os.path.join(os.path.dirname(os.path.abspath(script_path)),
                           "_sw_run_wrapper.py")
    bridge_dir = _HERE
    script_abs = os.path.abspath(script_path)
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "import sys, os, json, traceback\n"
            f"sys.path.insert(0, {bridge_dir!r})\n"
            "import sw_bridge\n"
            "sw = sw_bridge.get_sw()\n"
            f"__file__ = {script_abs!r}\n"
            "try:\n"
            f"    exec(compile(open({script_abs!r}, encoding='utf-8').read(), {script_abs!r}, 'exec'), {{'sw': sw, 'json': json, 'os': os, 'sys': sys, '__file__': __file__}})\n"
            "    print(json.dumps({'ok': True}, ensure_ascii=False))\n"
            "except Exception:\n"
            "    print(json.dumps({'ok': False, 'error': traceback.format_exc()[-3000:]}, ensure_ascii=False))\n"
        )
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", wrapper] + list(extra_args),
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(os.path.abspath(script_path)),
        )
    finally:
        try:
            os.remove(wrapper)
        except OSError:
            pass
    stdout = proc.stdout.strip()
    # 取最后一行 JSON（脚本自己的 print 可能会混入）
    lines = [l for l in stdout.splitlines() if l.strip().startswith("{")]
    result = None
    if lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            result = None
    if result is None:
        result = {"ok": proc.returncode == 0, "error": "no JSON in output"}
    result["stdout"] = stdout[-2000:]
    result["stderr"] = proc.stderr[-2000:]

    # 建模完成后自动展示：固定等轴测视角 + 中等缩放 + 截图（用户约定）
    try:
        import swapi
        m = swapi.from_active(sw)
        m.set_view_iso()
        shot = m.screenshot()
        result["screenshot"] = shot
        if shot.get("ok"):
            result["screenshot_path"] = shot["path"]
    except Exception as e:
        result["screenshot"] = {"ok": False, "error": str(e)}
    return result


def cmd_show(sw, screenshot_path=None):
    """窗口前台 + 等轴测视图 + 截图（展示给用户看成品）。"""
    import swapi
    m = swapi.from_active(sw)
    m.set_view_iso()
    m.bring_to_front()
    shot = m.screenshot(screenshot_path)
    return {"ok": shot.get("ok", False), "screenshot": shot}


def _find_drawing_template(sw):
    """自动探测工程图模板路径。"""
    cands = []
    # 1) API 查默认模板目录
    try:
        for pref in (108, 109, 110, 111):
            try:
                d = sw.GetUserPreferenceStringValue(pref)
                if d and os.path.exists(d):
                    cands.append(d)
            except Exception:
                pass
    except Exception:
        pass
    # 2) 运行中版本对应的 ProgramData 模板目录
    try:
        import swapi
        year = swapi._version_year(swapi._version_major(sw))
        d = r"C:\ProgramData\SolidWorks\SOLIDWORKS %d\templates" % year
        if os.path.isdir(d):
            cands.append(d)
    except Exception:
        pass
    # 3) 扫描所有 SOLIDWORKS 目录
    for ver_dir in glob.glob(r"C:\ProgramData\SolidWorks\SOLIDWORKS*"):
        cands.append(os.path.join(ver_dir, "templates"))
    for drive in ("C:", "D:", "E:", "F:"):
        for sw_dir in glob.glob(drive + r"\*SOLIDWORKS*"):
            cands.append(os.path.join(sw_dir, "templates"))
    # 4) 匹配绘图模板（.drwdot 格式）
    tmpl_names = ["gb_a3.drwdot", "gb_a2.drwdot", "gb_a4.drwdot",
                  "A3.drwdot", "A2.drwdot", "A4.drwdot",
                  "drawing.drwdot", "gb_a1.drwdot"]
    for d in cands:
        if not d or not os.path.isdir(d):
            continue
        for n in tmpl_names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
        # 也尝试 .prtdot（某些版本）
        found = glob.glob(os.path.join(d, "*.drwdot"))
        if found:
            return found[0]
        found = glob.glob(os.path.join(d, "*.prtdot"))
        if found:
            return found[0]
    return None


def _draw_view_names(doc):
    """获取工程图中已有视图名称列表。"""
    views = []
    try:
        v = doc.GetFirstView
        while v is not None:
            try:
                views.append(v.GetName)
            except Exception:
                pass
            try:
                v = v.GetNextView
            except Exception:
                break
    except Exception:
        pass
    return views


def cmd_drawing(sw, part_path, output_path=None):
    """从零件生成工程图（标准三视图 + 等轴测）。
    
    Args:
        part_path: 零件文件路径 (.sldprt)
        output_path: 输出工程图路径 (.slddrw)，不传则自动生成
    Returns:
        JSON with drawing info
    """
    import swapi
    import time
    
    # 1. 打开零件
    if not os.path.exists(part_path):
        return {"ok": False, "error": f"part not found: {part_path}"}
    part_result = cmd_open(sw, part_path)
    if not part_result.get("ok"):
        return {"ok": False, "error": f"cannot open part: {part_result.get('error', 'unknown')}"}
    
    # 2. 找绘图模板
    tmpl = _find_drawing_template(sw)
    if not tmpl:
        return {"ok": False, "error": "no drawing template found"}
    
    # 3. 确定输出路径（使用英文文件名避免编码问题）
    if not output_path:
        part_name = os.path.basename(part_path)
        # 移除中文扩展名，改用英文命名
        name_no_ext = os.path.splitext(part_name)[0]
        # 保留数字和字母，移除中文
        import re
        ascii_name = re.sub(r'[^\w\-]', '_', name_no_ext)
        output_path = os.path.join(os.path.dirname(part_path), ascii_name + ".slddrw")
    
    # 4. 新建工程图（A3 横向 420x297）
    doc = sw.NewDocument(tmpl, 0, 0.420, 0.297)
    if doc is None:
        return {"ok": False, "error": "NewDocument returned None for drawing"}
    
    # 5. 让 SW 窗口可见
    sw.Visible = True
    try:
        swapi._show_main_window(maximize=True)
    except Exception:
        pass
    
    # 6. 添加标准三视图
    empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    part_abs = os.path.abspath(part_path)
    
    views_added = []
    view_positions = [
        ("*Front", 0.150, 0.180, "前视图"),
        ("*Top", 0.150, 0.070, "俯视图"),
        ("*Right", 0.280, 0.180, "右视图"),
        ("*Isometric", 0.280, 0.070, "等轴测"),
    ]
    
    for view_name, x, y, label in view_positions:
        try:
            # 使用旧版 API CreateDrawViewFromModelView（传文件路径字符串）
            v = doc.CreateDrawViewFromModelView(
                part_abs, view_name, x, y, 0)
            if v is not None:
                views_added.append(label)
                time.sleep(0.3)
        except Exception as e:
            pass
    
    # 7. 缩放适应
    try:
        doc.ViewZoomtofit2()
    except Exception:
        pass
    
    # 8. 保存
    result = {"ok": len(views_added) > 0, "views": views_added}
    try:
        rc = doc.SaveAs3(output_path, 0, 2)
        result["saved"] = os.path.exists(output_path)
        result["path"] = output_path
        result["rc"] = rc
    except Exception as e:
        result["save_error"] = str(e)
    
    # 9. 截图
    try:
        m = swapi.from_active(sw)
        shot = m.screenshot()
        if shot.get("ok"):
            result["screenshot_path"] = shot["path"]
    except Exception:
        pass
    
    return result


def cmd_dwg(sw, part_path, output_path=None):
    """从零件生成工程图并导出为 DWG 格式。
    
    Args:
        part_path: 零件文件路径 (.sldprt)
        output_path: 输出 DWG 路径 (.dwg)，不传则自动生成
    Returns:
        JSON with DWG info
    """
    import swapi
    
    # 1. 先生成工程图
    drawing_path = output_path.replace(".dwg", ".slddrw") if output_path else None
    dwg_result = cmd_drawing(sw, part_path, drawing_path)
    if not dwg_result.get("ok"):
        return dwg_result
    
    # 2. 确定输出路径
    if not output_path:
        base = os.path.splitext(part_path)[0]
        output_path = base + ".dwg"
    
    # 3. 打开工程图并另存为 DWG
    saving = dwg_result.get("path") or drawing_path
    if saving and os.path.exists(saving):
        try:
            # 打开工程图
            ext = os.path.splitext(saving)[1].lower()
            doc_type = 3  # swDwgDrawing
            errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            doc = sw.OpenDoc6(saving, doc_type, 1, "", errs, warns)
            if doc is not None:
                # 导出为 DWG（dwgExport 格式）
                rc = doc.SaveAs3(output_path, 0, 0)
                result = {
                    "ok": rc == 0 or os.path.exists(output_path),
                    "dwg_path": output_path,
                    "drawing_path": saving,
                    "exists": os.path.exists(output_path),
                    "rc": rc,
                }
            else:
                result = {"ok": False, "error": "cannot open drawing for DWG export"}
        except Exception as e:
            result = {"ok": False, "error": f"DWG export failed: {e}"}
    else:
        result = {"ok": False, "error": "drawing file not found for DWG export"}
    
    # 4. 截图
    try:
        m = swapi.from_active(sw)
        m.set_view_iso()
        shot = m.screenshot()
        if shot.get("ok"):
            result["screenshot_path"] = shot["path"]
    except Exception:
        pass
    
    return result


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"ok": False, "error": "no command"}, ensure_ascii=False))
        return
    cmd = args[0]
    try:
        sw = get_sw()
        if cmd == "status":
            result = cmd_status(sw)
        elif cmd == "doctor":
            result = cmd_doctor(sw)
        elif cmd == "open":
            result = cmd_open(sw, args[1] if len(args) > 1 else "")
        elif cmd == "new":
            result = cmd_new(sw, args[1] if len(args) > 1 else "")
        elif cmd == "info":
            result = cmd_info(sw)
        elif cmd == "list":
            result = cmd_list(sw)
        elif cmd == "massprops":
            result = cmd_massprops(sw)
        elif cmd == "close":
            result = cmd_close(sw)
        elif cmd == "save":
            result = cmd_save(sw, args[1] if len(args) > 1 else "")
        elif cmd == "sketch-rect":
            result = cmd_sketch_rect(sw, args[1], args[2], args[3])
        elif cmd == "export-pdf":
            result = cmd_export_pdf(sw, args[1] if len(args) > 1 else "")
        elif cmd == "run":
            result = cmd_run(sw, args[1] if len(args) > 1 else "", args[2:])
        elif cmd == "show":
            result = cmd_show(sw, args[1] if len(args) > 1 else None)
        elif cmd == "drawing":
            part = args[1] if len(args) > 1 else ""
            out = args[2] if len(args) > 2 else None
            result = cmd_drawing(sw, part, out)
        elif cmd == "dwg":
            part = args[1] if len(args) > 1 else ""
            out = args[2] if len(args) > 2 else None
            result = cmd_dwg(sw, part, out)
        else:
            result = {"ok": False, "error": f"unknown command: {cmd}"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[-2000:],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
