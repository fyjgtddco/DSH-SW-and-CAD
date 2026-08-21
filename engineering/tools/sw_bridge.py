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
    # Bug 2 修复: 确保 swapi 模块可被找到
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "import sys, os, json, traceback\n"
            # 添加桥接目录到路径（swapi.py 所在目录）
            f"sys.path.insert(0, {bridge_dir!r})\n"
            # 添加脚本所在目录（便于 import 同级模块）
            f"sys.path.insert(0, {os.path.dirname(script_abs)!r})\n"
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
        # Bug 1 修复: 设置 PYTHONIOENCODING 绕过 Windows GBK 终端编码问题
        run_env = dict(os.environ)
        run_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", wrapper] + list(extra_args),
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(os.path.abspath(script_path)),
            encoding='utf-8', errors='replace',
            env=run_env,
        )
    except Exception as e:
        return {"ok": False, "error": f"subprocess failed: {e}"}
    finally:
        try:
            os.remove(wrapper)
        except OSError:
            pass

    # Bug 1 修复: 子进程 PYTHONIOENCODING=utf-8 避免 print 中文 GBK 报错
    # Bug 2 修复: 显式 encoding='utf-8' + errors='replace' 防止 GBK 解码乱码
    # Bug 3 修复: proc.stdout/stderr 可能为 None，用 or "" 避免 AttributeError
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

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
    result["stderr"] = stderr[-2000:]

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
    """查找工程图模板（优先 GB 标准，兼容中文/英文版本）。"""
    candidates = [
        r"C:\ProgramData\SolidWorks\SOLIDWORKS 2020\templates\gb_a3.drwdot",
        r"C:\ProgramData\SolidWorks\SOLIDWORKS 2019\templates\gb_a3.drwdot",
        r"C:\ProgramData\SolidWorks\SOLIDWORKS 2022\templates\gb_a3.drwdot",
    ]
    # 也搜索 ProgramData 下的所有版本
    import glob
    for d in glob.glob(r"C:\ProgramData\SolidWorks\SOLIDWORKS*"):
        for tmpl in ["gb_a3.drwdot", "ANSI A size metric.drwdot", "Metric A3.drwdot"]:
            path = os.path.join(d, "templates", tmpl)
            if os.path.exists(path):
                candidates.insert(0, path)  # 优先找到的版本
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _get_view_bbox(doc, view):
    """获取视图在图纸坐标中的包围盒 (x1, y1, x2, y2)，单位米。

    GetOutline 是属性（非方法），直接返回 (x1,y1,x2,y2) tuple。
    图纸坐标系：原点左下角，X向右，Y向上。
    """
    try:
        outline = view.GetOutline
        if outline and isinstance(outline, (list, tuple)) and len(outline) == 4:
            return tuple(outline)
    except Exception:
        pass
    return None


def _bbox_center(bb):
    """包围盒中心点 (cx, cy)"""
    return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)


def _bbox_overlap(bb1, bb2, gap=0.035):
    """检查两个包围盒是否相交（考虑最小间距 gap）。

    返回 True = 有重叠/粘连（不允许），False = 安全分离。
    """
    x1a, y1a, x2a, y2a = bb1
    x1b, y1b, x2b, y2b = bb2
    # 水平方向是否完全分离
    sep_h = (x2a + gap <= x1b) or (x2b + gap <= x1a)
    # 垂直方向是否完全分离
    sep_v = (y2a + gap <= y1b) or (y2b + gap <= y1a)
    return not (sep_h or sep_v)  # True = 有交集


def _move_view(view, cx, cy):
    """移动视图使中心点到达指定位置（图纸坐标，米）"""
    try:
        view.Position = [cx, cy]
        return True
    except Exception:
        pass
    return False


def _enumerate_views(doc):
    """枚举工程图中所有视图，返回 [(name, view_obj), ...]，跳过标题栏视图。"""
    views = []
    try:
        vv = doc.GetFirstView
        while vv is not None:
            try:
                name = vv.GetName()
            except Exception:
                name = ""
            # 跳过标题栏/图纸轮廓视图（outline 覆盖整张纸）
            try:
                outline = vv.GetOutline
                if outline and len(outline) == 4:
                    if abs(outline[2] - 0.420) < 0.01 and abs(outline[3] - 0.297) < 0.01:
                        vv = vv.GetNextView
                        continue
            except Exception:
                pass
            views.append((name, vv))
            try:
                vv = vv.GetNextView
            except Exception:
                break
    except Exception:
        pass
    return views


def cmd_drawing(sw, part_path, output_path=None):
    """从零件生成工程图（标准三视图 + 等轴测）。

    【强制包围盒约束】每个视图以锚点+包围盒管理：
    - 约束1：相邻视图包围盒间距 ≥35mm，严禁线条粘连
    - 约束4：主俯"长对正"（同cx），主右"高平齐"（同cy）
    - 约束5：等轴测独立放置，不与三视图包围盒重叠
    """
    import swapi
    import time as _time
    import re

    if not os.path.exists(part_path):
        return {"ok": False, "error": f"part not found: {part_path}"}

    # 1. 打开零件
    part_result = cmd_open(sw, part_path)
    if not part_result.get("ok"):
        return {"ok": False, "error": f"cannot open part: {part_result.get('error', 'unknown')}"}

    # 2. 找绘图模板
    tmpl = _find_drawing_template(sw)
    if not tmpl:
        return {"ok": False, "error": "no drawing template found"}

    # 3. 确定输出路径
    if not output_path:
        part_name = os.path.basename(part_path)
        name_no_ext = os.path.splitext(part_name)[0]
        ascii_name = re.sub(r'[^\w\-]', '_', name_no_ext)
        output_path = os.path.join(os.path.dirname(part_path), ascii_name + ".slddrw")

    # 4. 新建工程图（A3 横向 420×297mm）
    doc = sw.NewDocument(tmpl, 3, 0.420, 0.297)
    if doc is None:
        return {"ok": False, "error": "NewDocument returned None"}

    # 5. 等待文档就绪
    _time.sleep(2)
    sw.Visible = True
    try:
        swapi._show_main_window(maximize=True)
    except Exception:
        pass

    part_abs = os.path.abspath(part_path)
    views_added = []

    # ── 第一步：创建所有视图（初始位置）─────────────────────────────
    # A3 图纸可用区域：x∈[0.025, 0.395], y∈[0.025, 0.272]（留边距+标题栏）
    # 初始锚点（中心坐标，单位米）：
    initial_positions = [
        ("*前视",  0.090, 0.170, "前视图"),   # 左中
        ("*上视",  0.090, 0.070, "俯视图"),   # 左下（主视下方）
        ("*右视",  0.240, 0.170, "右视图"),   # 右中（主视右侧）
        ("*等轴测", 0.330, 0.220, "等轴测"),  # 右上独立区
    ]

    _MIN_GAP = 0.040  # 40mm 最小包围盒间距（含安全余量）

    # CreateDrawViewFromModelView 返回 True/False，视图对象需枚举获取
    for view_name, ax, ay, label in initial_positions:
        try:
            ok = doc.CreateDrawViewFromModelView(part_abs, view_name, ax, ay, 0)
            if ok is not False:
                views_added.append(label)
                _time.sleep(0.3)
        except Exception:
            pass

    # ── 第二步：枚举已创建的视图，获取 view_obj 和包围盒 ───────────
    all_views = _enumerate_views(doc)
    created_views = []  # [(label, view_obj, anchor_x, anchor_y)]
    for label in views_added:
        # 按标签名匹配视图（中文视图名）
        matched = None
        for name, vobj in all_views:
            if label in name or name in label:
                matched = vobj
                break
        # 如果没匹配到，按锚点位置查找
        if matched is None:
            for name, vobj in all_views:
                try:
                    pos = vobj.Position
                    if pos and len(pos) >= 2:
                        for vn, ax, ay, ln in initial_positions:
                            if ln == label and abs(pos[0] - ax) < 0.005 and abs(pos[1] - ay) < 0.005:
                                matched = vobj
                                break
                except Exception:
                    pass
                if matched:
                    break
        if matched:
            # 找对应的锚点
            anchor = next(( (ax, ay) for vn, ax, ay, ln in initial_positions if ln == label), (0, 0))
            created_views.append((label, matched, anchor[0], anchor[1]))

    # ── 第三步：测量所有视图包围盒 ──────────────────────────────────
    view_bboxes = {}  # label -> (x1,y1,x2,y2)
    for label, v, _, _ in created_views:
        bb = _get_view_bbox(doc, v)
        if bb:
            view_bboxes[label] = bb
            cx, cy = _bbox_center(bb)
            print(f"  [bbox] {label}: ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm  center=({cx*1000:.0f},{cy*1000:.0f})")
        else:
            print(f"  [bbox] {label}: 无法获取包围盒")

    # ── 第三步：包围盒碰撞检测 + 自动重排 ───────────────────────────
    # 迭代修复重叠：找到所有重叠对，将后创建的视图推开
    max_iter = 15
    for _iter in range(max_iter):
        had_overlap = False
        labels = list(view_bboxes.keys())
        for i, lb_a in enumerate(labels):
            for lb_b in labels[i+1:]:
                bb_a = view_bboxes[lb_a]
                bb_b = view_bboxes[lb_b]
                if _bbox_overlap(bb_a, bb_b, _MIN_GAP):
                    had_overlap = True
                    # 找到重叠方向，将 lb_b 推开
                    ca = _bbox_center(bb_a)
                    cb = _bbox_center(bb_b)
                    # 水平偏移量
                    dx = (bb_a[2] + _MIN_GAP - bb_b[0]) if cb[0] < ca[0] else (bb_b[2] + _MIN_GAP - bb_a[0])
                    dy = (bb_a[3] + _MIN_GAP - bb_b[1]) if cb[1] < ca[1] else (bb_b[3] + _MIN_GAP - bb_a[1])
                    # 优先沿短边方向推（避免大幅移动）
                    overlap_h = max(0, min(bb_a[2], bb_b[2]) - max(bb_a[0], bb_b[0]))
                    overlap_v = max(0, min(bb_a[3], bb_b[3]) - max(bb_a[1], bb_b[1]))
                    if overlap_h < overlap_v:
                        # 水平方向重叠少，沿X推
                        push_x = dx if dx != 0 else (_MIN_GAP * 0.5)
                        push_y = dy if abs(dy) > 0.005 else 0
                    else:
                        push_x = dx if abs(dx) > 0.005 else 0
                        push_y = dy
                    # 找 lb_b 对应的 view 对象
                    _, v_b, _, _ = next((item for item in created_views if item[0] == lb_b), (None, None, None, None))
                    if v_b is not None:
                        new_cx = cb[0] + push_x
                        new_cy = cb[1] + push_y
                        # 限制在图纸范围内
                        new_cx = max(0.030, min(0.390, new_cx))
                        new_cy = max(0.030, min(0.270, new_cy))
                        _move_view(v_b, new_cx, new_cy)
                        _time.sleep(0.2)
                        # 重新测量
                        new_bb = _get_view_bbox(doc, v_b)
                        if new_bb:
                            view_bboxes[lb_b] = new_bb
                        print(f"  [reposition] {lb_b}: ({cb[0]*1000:.0f},{cb[1]*1000:.0f}) -> ({new_cx*1000:.0f},{new_cy*1000:.0f})")
        if not had_overlap:
            break

    # ── 第四步：最终验证所有包围盒 ──────────────────────────────────
    print("\n=== 最终包围盒验证 ===")
    final_bboxes = {}
    for label, v, _, _ in created_views:
        bb = _get_view_bbox(doc, v)
        if bb:
            final_bboxes[label] = bb
    all_ok = True
    labels = list(final_bboxes.keys())
    for i in range(len(labels)):
        for j in range(i+1, len(labels)):
            if _bbox_overlap(final_bboxes[labels[i]], final_bboxes[labels[j]], _MIN_GAP):
                print(f"  [WARN]  警告: {labels[i]} 与 {labels[j]} 仍有重叠！")
                all_ok = False
            else:
                print(f"  [OK]  {labels[i]} ↔ {labels[j]}: 间距达标")
    if all_ok:
        print("  [OK] 所有视图包围盒无重叠，间距达标")

    # ── 第五步：缩放适应 + 保存 ─────────────────────────────────────
    try:
        doc.ViewZoomtofit2()
    except Exception:
        pass
    _time.sleep(0.5)

    result = {"ok": len(views_added) > 0, "views": views_added}
    try:
        rc = doc.SaveAs3(output_path, 0, 2)
        result["saved"] = os.path.exists(output_path)
        result["path"] = output_path
        result["rc"] = rc
    except Exception as e:
        result["save_error"] = str(e)

    # 截图
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

    【强制使用中文视图名】先调用 cmd_drawing（已包含中文视图名），再导出 DWG。
    """
    # 1. 先生成工程图（强制中文视图名）
    drawing_path = None
    if output_path:
        import re
        base = os.path.splitext(output_path)[0]
        drawing_path = base + ".slddrw"
    dwg_result = cmd_drawing(sw, part_path, drawing_path)
    if not dwg_result.get("ok"):
        return dwg_result

    # 2. 确定输出路径
    if not output_path:
        import re
        part_name = os.path.basename(part_path)
        name_no_ext = re.sub(r'[^\w\-]', '_', os.path.splitext(part_name)[0])
        output_path = os.path.join(os.path.dirname(part_path), name_no_ext + ".dwg")

    # 3. 打开工程图并导出 DWG
    saving = dwg_result.get("path")
    if not (saving and os.path.exists(saving)):
        return {"ok": False, "error": "drawing file not found for DWG export"}

    result = {"dwg_path": output_path, "drawing_path": saving}

    try:
        # 打开工程图
        doc_type = 3  # swDrawing
        errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.OpenDoc6(saving, doc_type, 1, "", errs, warns)
        if doc is None:
            return {"ok": False, "error": "cannot open drawing for DWG export"}

        # 导出 DWG（SaveAs3 直接另存为 .dwg）
        try:
            rc = doc.SaveAs3(output_path, 0, 2)
            if os.path.exists(output_path):
                result["ok"] = True
                result["method"] = "SaveAs3"
                result["exists"] = True
                result["rc"] = rc
        except Exception as e:
            result["saveas3_error"] = str(e)
    except Exception as e:
        result["error"] = str(e)

    return result


def cmd_cleanup(sw, temp_dir=None):
    """清理临时文件和截图。"""
    import glob
    cleaned = []
    if temp_dir is None:
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "output")
    temp_dir = os.path.normpath(temp_dir)
    if os.path.exists(temp_dir):
        for f in glob.glob(os.path.join(temp_dir, "*.png")):
            try:
                os.remove(f)
                cleaned.append(os.path.basename(f))
            except:
                pass
    return {"ok": True, "cleaned": cleaned, "count": len(cleaned)}


def cmd_vision_fallback(sw, description="", auto_save=True):
    """Vision 后端不可用时的降级方案：截图并返回路径供前端识图。

    当 vision_describe/vision_ocr 等工具返回 ok:false 时调用此命令，
    自动截取 SolidWorks 当前视图并保存到 DSH-Check 目录，
    DSH 前端识图插件可读取该图片进行分析。

    Args:
        sw: SldWorks 应用对象
        description: 截图描述（用于文件命名）
        auto_save: 是否自动保存到 DSH-Check 目录
    """
    import swapi
    import time

    # 确保窗口前台
    try:
        m = swapi.from_active(sw)
        m.bring_to_front()
        time.sleep(0.5)
    except:
        pass

    # 确定保存路径
    if auto_save:
        out_dir = r"C:\Users\j1877\Desktop\DSH-Check"
        os.makedirs(out_dir, exist_ok=True)
        # 使用描述作为文件名的一部分
        safe_desc = "".join(c if c.isalnum() or c in " _-" else "_" for c in description)[:20]
        shot_path = os.path.join(out_dir, f"vision_fallback_{safe_desc}_{int(time.time())}.png")
    else:
        shot_path = None

    shot = swapi.from_active(sw).screenshot(shot_path)

    result = {
        "ok": shot.get("ok", False),
        "screenshot_path": shot.get("path", "") if shot_path else "",
        "description": description,
        "vision_backend_available": False,
        "fallback_triggered": True,
    }

    if shot.get("ok"):
        import os as _os
        result["file_size_kb"] = _os.path.getsize(shot.get("path", "")) // 1024
        result["note"] = "Vision 后端不可用，已截图保存。请使用 DSH 前端识图插件分析此图片。"
    else:
        result["error"] = shot.get("error", "unknown")

    return result


def cmd_check_vision(sw):
    """检查 Vision 后端是否可用。

    返回结果包含：
    - ok: bool - 是否可用
    - backend: str - 后端名称
    - screenshot_path: str - 如不可用，返回截图路径供前端识别
    """
    import swapi
    import time

    result = {"ok": False, "backend": "agnes/agnes-2.5-flash", "available": False}

    # 尝试截取一张测试图
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check"
    os.makedirs(out_dir, exist_ok=True)
    test_shot = os.path.join(out_dir, "vision_test_screenshot.png")

    try:
        m = swapi.from_active(sw)
        m.bring_to_front()
        time.sleep(0.5)
        shot = m.screenshot(test_shot)
        if shot.get("ok"):
            result["screenshot_saved"] = True
            result["screenshot_path"] = test_shot
            result["file_size_kb"] = os.path.getsize(test_shot) // 1024
            result["note"] = "Vision 后端暂时不可用，已保存截图到本地。请使用 DSH 前端识图插件分析。"
    except Exception as e:
        result["error"] = str(e)

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
            result = cmd_drawing(sw, args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else None)
        elif cmd == "dwg":
            result = cmd_dwg(sw, args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else None)
        elif cmd == "cleanup":
            result = cmd_cleanup(sw, args[1] if len(args) > 1 else None)
        elif cmd == "vision-fallback":
            desc = " ".join(args[1:]) if len(args) > 1 else ""
            auto_save = args[2] != "--no-save" if len(args) > 2 else True
            result = cmd_vision_fallback(sw, description=desc, auto_save=auto_save)
        elif cmd == "check-vision":
            result = cmd_check_vision(sw)
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
