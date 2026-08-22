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

# Fix GBK encoding on Windows terminals
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

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
    # 打开文件后激活并最大化窗口（用户约定）
    try:
        sw.ActivateDoc(doc.GetTitle)
    except Exception:
        pass
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


def _find_drawing_template(sw, paper_name=None):
    """查找工程图模板（优先 GB 标准，按图纸尺寸选择对应模板）。

    Args:
        sw: SldWorks 应用对象（保留参数兼容性）
        paper_name: 图纸名称 (A1/A2/A3)，None 则使用默认 A3
    """
    import glob
    _TEMPLATE_DIR = r"C:\ProgramData\SolidWorks"
    _PAPER_TO_TEMPLATE = {"A1": "gb_a1.drwdot", "A2": "gb_a2.drwdot", "A3": "gb_a3.drwdot", "A4": "gb_a4.drwdot"}
    target = _PAPER_TO_TEMPLATE.get(paper_name, "gb_a3.drwdot")
    candidates = []
    for sw_dir in sorted(glob.glob(os.path.join(_TEMPLATE_DIR, "SOLIDWORKS *"))):
        tmpl_path = os.path.join(sw_dir, "templates", target)
        if os.path.exists(tmpl_path):
            candidates.append(tmpl_path)
    # 回退：目标尺寸模板不存在时尝试 A3
    if not candidates and paper_name != "A3":
        for sw_dir in sorted(glob.glob(os.path.join(_TEMPLATE_DIR, "SOLIDWORKS *"))):
            tmpl_path = os.path.join(sw_dir, "templates", "gb_a3.drwdot")
            if os.path.exists(tmpl_path):
                candidates.append(tmpl_path)
                break
    return candidates[0] if candidates else None


def _get_bbox(view):
    """获取单个视图的包围盒 (x1,y1,x2,y2)，单位米。

    重要：必须直接调用 view.GetOutline，不能通过迭代器缓存的对象读取。
    """
    try:
        # 直接用 view 对象读取（不经过列表迭代）
        outline = view.GetOutline
        if outline and isinstance(outline, (list, tuple)) and len(outline) == 4:
            return tuple(outline)
    except Exception:
        pass
    return None


def _enumerate_view_bboxes(doc):
    """枚举工程中所有视图，返回 [(label_or_idx, bb), ...] 跳过图纸轮廓。

    使用链式调用避免 win32com 缓存问题。
    """
    result = []
    try:
        v = doc.GetFirstView
        idx = 0
        while v is not None and idx < 20:
            try:
                bb = _get_bbox(v)
                if bb:
                    # 跳过图纸轮廓视图
                    if abs(bb[2] - _paper_w) < 0.01 and abs(bb[3] - _paper_h) < 0.01:
                        v = v.GetNextView
                        idx += 1
                        continue
                    result.append((idx, bb))
            except Exception:
                pass
            try:
                v = v.GetNextView
            except Exception:
                break
            idx += 1
    except Exception:
        pass
    return result


def _bbox_center(bb):
    """包围盒中心点 (cx, cy)"""
    return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)


def _bbox_spacing(bb1, bb2):
    """计算两个包围盒之间的最小间距（米）。返回 0 表示有重叠。"""
    x1a, y1a, x2a, y2a = bb1
    x1b, y1b, x2b, y2b = bb2
    dx = max(0, max(x1b - x2a, x1a - x2b))
    dy = max(0, max(y1b - y2a, y1a - y2b))
    return (dx**2 + dy**2) ** 0.5


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


def cmd_drawing(sw, part_path, output_path=None):
    """从零件生成工程图（标准三视图 + 等轴测）。

    【强制包围盒约束】每个视图以锚点+包围盒管理：
    - 约束1：相邻视图包围盒间距 >=15mm，严禁线条粘连
    - 约束4：主俯"长对正"（同cx），主右"高平齐"（同cy）
    - 约束5：等轴测独立放置，不与三视图包围盒重叠
    - 自适应：A2@1:2 默认，超界自动升级 A1@1:2
    """
    import swapi
    import time as _time
    import re

    if not os.path.exists(part_path):
        return {"ok": False, "error": f"part not found: {part_path}"}

    # 检查零件是否已打开（避免重复打开导致卡死）
    part_abs = os.path.abspath(part_path)
    part_already_open = False
    try:
        for i in range(sw.GetDocumentCount):
            try:
                d = sw.GetDocument(i)
                if d is not None:
                    try:
                        doc_path = d.GetPathName
                        if doc_path and os.path.abspath(doc_path) == part_abs:
                            part_already_open = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    if not part_already_open:
        part_result = cmd_open(sw, part_path)
        if not part_result.get("ok"):
            return {"ok": False, "error": f"cannot open part: {part_result.get('error', 'unknown')}"}
    else:
        print(f"  [part] already open: {part_result.get('title', 'unknown') if 'part_result' in dir() else part_abs}", flush=True)

    tmpl = _find_drawing_template(sw)  # 默认 A3 模板
    if not tmpl:
        return {"ok": False, "error": "no drawing template found"}

    if not output_path:
        part_name = os.path.basename(part_path)
        name_no_ext = os.path.splitext(part_name)[0]
        ascii_name = re.sub(r"[^\w\-]", "_", name_no_ext)
        output_path = os.path.join(os.path.dirname(part_path), ascii_name + ".slddrw")

    part_abs = os.path.abspath(part_path)

    _MIN_GAP = 0.015
    MARGIN = 0.025
    _SCALE = 0.5  # 1:2 scale

    # 边距和禁区（mm）
    _MARGIN_MM = {"left": 15, "right": 15, "top": 15, "bottom": 10}
    # 标题栏禁区：右下角，包含消息框区域，预留更大空间
    _TITLE_BLOCK_RATIO = {"width": 0.30, "height": 0.25}  # 宽30%、高25%（含消息框）

    # Paper sizes (meters, landscape) — 按从小到大的顺序，自动向上兼容
    _PAPERS = [
        ("A3", 0.420, 0.297),
        ("A2", 0.594, 0.420),
        ("A1", 0.841, 0.594),
    ]

    # ── 标准比例列表（从小到大尝试，大的先放不进去就缩小）─────────────
    # 1:1, 1:2, 1:5, 1:10, 2:1, 2:5
    _STANDARD_SCALES = [0.1, 0.2, 0.5, 1.0, 2.0]

    # ── 参考图纸测量替换预测公式 ──────────────────────────────────────
    # SW 自动缩放规律：通过实测发现，各纸尺寸的视图尺寸与参考值的比例如下：
    #   A3: 1.0x (基准), A2: ~1.93x, A1: ~2.10x
    # 不能用 sqrt(面积比) 预测，必须在目标纸上实测参考视图尺寸。
    _ISO_W_RATIO = 1.37   # 等轴测宽度 / 正视图宽度（实测 A3: 125/92≈1.36）
    _ISO_H_RATIO = 1.77   # 等轴测高度 / 正视图高度（实测 A3: 126/72≈1.75）

    def _measure_ref_on_paper(sw, paper_w, paper_h, paper_name="A3"):
        """在目标纸尺寸上创建临时参考图纸，测量正视图实际尺寸（mm），关闭后返回。

        返回 (ref_w_mm, ref_h_mm) 或 (None, None) 如果失败。
        """
        try:
            tmpl = _find_drawing_template(sw, paper_name)
            if not tmpl:
                return None, None
            ref_doc = sw.NewDocument(tmpl, 3, paper_w, paper_h)
            _time.sleep(2)
            ref_doc.CreateDrawViewFromModelView(part_abs, "*前视", paper_w/2, paper_h/2, 0)
            _time.sleep(0.5)
            v = ref_doc.GetFirstView
            try:
                v = v.GetNextView
            except Exception:
                v = None
            if v:
                bb = v.GetOutline
                w_mm = (bb[2] - bb[0]) * 1000
                h_mm = (bb[3] - bb[1]) * 1000
                try:
                    ref_doc.CloseDoc
                except Exception:
                    pass
                _time.sleep(0.3)
                return w_mm, h_mm
            try:
                ref_doc.CloseDoc
            except Exception:
                pass
            _time.sleep(0.3)
            return None, None
        except Exception as e:
            print(f"  [WARN] ref measurement error: {e}", flush=True)
            try:
                ref_doc.CloseDoc
            except Exception:
                pass
            _time.sleep(0.3)
            return None, None

    def _compute_zones(pw_mm, ph_mm, ortho_w, ortho_h, iso_w, iso_h):
        """计算四区锚点信息，返回区域数据和标准比例列表。

        返回：(zones_info, safe_zone, STANDARD_SCALES)
          zones_info: [{"label":, "sw_name":, "zone":(x1,y1,x2,y2), "center":(cx,cy), "raw_w":, "raw_h":}, ...]
          safe_zone: (x1, y1, x2, y2) mm
          STANDARD_SCALES: 标准比例列表 [1.0, 0.5, 0.2, 0.1]
        """
        GAP_MM = 10
        STANDARD_SCALES = [1.0, 0.5, 0.2, 0.1]

        ml, mr, mt, mb = _MARGIN_MM["left"], _MARGIN_MM["right"], _MARGIN_MM["top"], _MARGIN_MM["bottom"]
        tb_h = ph_mm * _TITLE_BLOCK_RATIO["height"]
        tb_w = pw_mm * _TITLE_BLOCK_RATIO["width"]
        safe_x1, safe_y1 = ml, mb + tb_h
        safe_x2, safe_y2 = pw_mm - max(mr, tb_w), ph_mm - mt

        mid_x = (safe_x1 + safe_x2) / 2
        mid_y = (safe_y1 + safe_y2) / 2

        zones_raw = [
            ("前视图", "*前视", (safe_x1, mid_y, mid_x, safe_y2), ortho_w, ortho_h),
            ("俯视图", "*上视", (safe_x1, safe_y1, mid_x, mid_y), ortho_w, ortho_h),
            ("右视图", "*右视", (mid_x, mid_y, safe_x2, safe_y2), ortho_w, ortho_h),
            ("等轴测", "*等轴测", (mid_x, safe_y1, safe_x2, mid_y), iso_w, iso_h),
        ]

        zones_info = []
        for label, sw_name, (zx1, zy1, zx2, zy2), rw, rh in zones_raw:
            cx = (zx1 + zx2) / 2
            cy = (zy1 + zy2) / 2
            zones_info.append({
                "label": label,
                "sw_name": sw_name,
                "zone": (zx1, zy1, zx2, zy2),
                "center": (cx, cy),
                "raw_w": rw,
                "raw_h": rh,
            })

        return zones_info, (safe_x1, safe_y1, safe_x2, safe_y2), STANDARD_SCALES




    def _get_part_bbox_mm(sw, path):
        """读取零件的包围盒尺寸（mm）。
        先尝试在已打开文档中查找（避免重复打开），找不到再打开。
        多个 fallback：IGetBoundingBox → GetBoundingBox → 默认值。
        """
        default_w, default_h = 100, 100
        path_abs = os.path.abspath(path)

        # 先检查是否已打开
        for i in range(sw.GetDocumentCount):
            try:
                d = sw.GetDocument(i)
                if d is None:
                    continue
                try:
                    doc_path = d.GetPathName
                    if doc_path and os.path.abspath(doc_path) == path_abs:
                        # 已打开，直接用
                        try:
                            bbox = d.IGetBoundingBox
                            if bbox and len(bbox) == 6:
                                w_mm = (bbox[3] - bbox[0]) * 1000
                                h_mm = (bbox[4] - bbox[1]) * 1000
                                return {"w": max(w_mm, 50), "h": max(h_mm, 50)}
                        except Exception:
                            pass
                        try:
                            bbox = d.GetBoundingBox
                            if bbox:
                                w_mm = (bbox[3] - bbox[0]) * 1000
                                h_mm = (bbox[4] - bbox[1]) * 1000
                                return {"w": max(w_mm, 50), "h": max(h_mm, 50)}
                        except Exception:
                            pass
                        return {"w": default_w, "h": default_h}
                except Exception:
                    pass
            except Exception:
                continue

        # 未打开，打开后读取
        try:
            errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            d = sw.OpenDoc6(path, 1, 1, "", errs, warns)
            if d is None:
                return {"w": default_w, "h": default_h}
            try:
                bbox = d.IGetBoundingBox
                if bbox and len(bbox) == 6:
                    w_mm = (bbox[3] - bbox[0]) * 1000
                    h_mm = (bbox[4] - bbox[1]) * 1000
                    sw.CloseDoc(d.GetTitle)
                    return {"w": max(w_mm, 50), "h": max(h_mm, 50)}
            except Exception:
                pass
            try:
                bbox = d.GetBoundingBox
                if bbox:
                    w_mm = (bbox[3] - bbox[0]) * 1000
                    h_mm = (bbox[4] - bbox[1]) * 1000
                    sw.CloseDoc(d.GetTitle)
                    return {"w": max(w_mm, 50), "h": max(h_mm, 50)}
            except Exception:
                pass
            sw.CloseDoc(d.GetTitle)
        except Exception:
            pass
        return {"w": default_w, "h": default_h}

    def _get_bbox(vobj):
        try:
            o = vobj.GetOutline
            if o and isinstance(o, (list, tuple)) and len(o) == 4:
                return tuple(o)
        except Exception:
            pass
        return None

    def _enumerate_views(doc, pw, ph):
        result = []
        try:
            v = doc.GetFirstView
            while v is not None:
                try:
                    bb = _get_bbox(v)
                    if bb:
                        # Skip sheet outline: match current paper OR any known standard size
                        # (stale COM objects from previous docs may have different sizes)
                        known_sizes = [
                            (0.420, 0.297),  # A3
                            (0.594, 0.420),  # A2
                            (0.841, 0.594),  # A1
                        ]
                        is_outline = False
                        for kw, kh in known_sizes:
                            if abs(bb[2]-kw)<0.01 and abs(bb[3]-kh)<0.01:
                                is_outline = True
                                break
                        if is_outline:
                            v = v.GetNextView
                            continue
                        result.append((v, bb))
                except Exception:
                    pass
                try:
                    v = v.GetNextView
                except Exception:
                    break
        except Exception:
            pass
        return result

    def _check_bounds(bboxes, pw, ph):
        """Returns (ok, fail_label_or_None). 使用实际安全区边界检查。

        bb 坐标单位是米（SW GetOutline 返回），安全区边界也用米比较。
        """
        # 计算实际安全区（与 _compute_layout 一致，全部用米）
        tb_h_m = ph * _TITLE_BLOCK_RATIO["height"]   # 标题栏高度（米）
        tb_w_m = pw * _TITLE_BLOCK_RATIO["width"]    # 标题栏宽度（米）
        ml_m = _MARGIN_MM["left"] / 1000
        mr_m = _MARGIN_MM["right"] / 1000
        mt_m = _MARGIN_MM["top"] / 1000
        mb_m = _MARGIN_MM["bottom"] / 1000
        safe_x1_m = ml_m
        safe_y1_m = mb_m + tb_h_m                     # 标题栏上方
        safe_x2_m = pw - max(mr_m, tb_w_m)            # 标题栏右侧
        safe_y2_m = ph - mt_m                          # 上边距
        print(f"  [debug] _check_bounds: paper={pw*1000:.0f}x{ph*1000:.0f}mm safe=({safe_x1_m*1000:.0f},{safe_y1_m*1000:.0f})-({safe_x2_m*1000:.0f},{safe_y2_m*1000:.0f})", flush=True)
        for label, bb in bboxes.items():
            # bb 是米，直接比较
            ok = (bb[0] >= safe_x1_m and bb[1] >= safe_y1_m and
                  bb[2] <= safe_x2_m and bb[3] <= safe_y2_m)
            print(f"  [debug]   {label}: ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm -> {'OK' if ok else 'FAIL'}", flush=True)
            if not ok:
                return False, label
        return True, None

    def _check_spacing(bboxes, labels):
        """Returns (ok, label_i_or_None, label_j_or_None)."""
        for i in range(len(labels)):
            for j in range(i+1, len(labels)):
                bb_i, bb_j = bboxes.get(labels[i]), bboxes.get(labels[j])
                if bb_i and bb_j:
                    x1a,y1a,x2a,y2a = bb_i
                    x1b,y1b,x2b,y2b = bb_j
                    sep_h = (x2a+_MIN_GAP<=x1b) or (x2b+_MIN_GAP<=x1a)
                    sep_v = (y2a+_MIN_GAP<=y1b) or (y2b+_MIN_GAP<=y1a)
                    if not (sep_h or sep_v):
                        return False, labels[i], labels[j]
        return True, None, None

    def _match_view_by_position(sw_name, x1, y1, x2, y2, expected_labels):
        """根据视图位置和名称匹配标签。"""
        # 尝试从 SW 名称中提取
        if sw_name:
            for label in expected_labels:
                if label in sw_name or sw_name in label:
                    return label
        # 基于位置匹配（前/右在上方，俯/等在下方）
        # 这是启发式匹配，用于辅助
        return None

    # ── 参考测量 + 四区锚点布局 ──────────────────────────────────────
    print("=== Creating engineering drawing ===", flush=True)

    final_labels = None
    final_bboxes = None
    final_paper = None
    final_doc = None
    used_scale = _SCALE
    GAP_MM = 10
    _VT_ARRAY = pythoncom.VT_ARRAY | pythoncom.VT_R8
    SW_NAME_ORDER = [("*前视", "前视图"), ("*上视", "俯视图"), ("*右视", "右视图"), ("*等轴测", "等轴测")]
    STANDARD_SCALES = [1.0, 0.5, 0.2, 0.1]

    for paper_name, paper_w, paper_h in _PAPERS:
        pw_mm = paper_w * 1000
        ph_mm = paper_h * 1000
        print(f"\n--- Trying {paper_name} ({pw_mm:.0f}x{ph_mm:.0f}mm) ---", flush=True)

        # 测量参考视图尺寸
        ref_w_mm, ref_h_mm = _measure_ref_on_paper(sw, paper_w, paper_h, paper_name)
        if ref_w_mm is None:
            print(f"  [WARN] ref measurement failed, skipping {paper_name}", flush=True)
            continue
        ortho_w = ref_w_mm
        ortho_h = ref_h_mm
        iso_w = ortho_w * _ISO_W_RATIO
        iso_h = ortho_h * _ISO_H_RATIO
        print(f"  [ref] ortho={ortho_w:.0f}x{ortho_h:.0f} iso={iso_w:.0f}x{iso_h:.0f}mm", flush=True)

        # 计算四区锚点信息
        zones_info, safe_zone, STANDARD_SCALES = _compute_zones(pw_mm, ph_mm, ortho_w, ortho_h, iso_w, iso_h)
        sx1, sy1, sx2, sy2 = safe_zone
        print(f"  [safe] ({sx1:.0f},{sy1:.0f})-({sx2:.0f},{sy2:.0f})mm", flush=True)
        for z in zones_info:
            zx1, zy1, zx2, zy2 = z["zone"]
            cx, cy = z["center"]
            print(f"  [zone] {z['label']}: ({zx1:.0f},{zy1:.0f})-({zx2:.0f},{zy2:.0f}) center=({cx:.0f},{cy:.0f})", flush=True)

        # 创建图纸
        paper_tmpl = _find_drawing_template(sw, paper_name) or tmpl
        print(f"  [template] {paper_tmpl.split(chr(92))[-1]}", flush=True)
        doc = sw.NewDocument(paper_tmpl, 3, paper_w, paper_h)
        if doc is None:
            print(f"  [FAIL] NewDocument returned None", flush=True)
            continue
        final_doc = doc
        final_paper = paper_name
        _time.sleep(2)

        # ===== 第一步：盲放视图（图纸中心，最小比例 1:20）=====
        print("  [step1] creating views at center...", flush=True)
        for sw_name, label in SW_NAME_ORDER:
            try:
                ok = doc.CreateDrawViewFromModelView(part_abs, sw_name, paper_w / 2, paper_h / 2, 0.05)
                if ok is False:
                    print(f"    [FAIL] create {label}", flush=True)
            except Exception as e:
                print(f"    [FAIL] create {label}: {e}", flush=True)
            _time.sleep(0.3)

        # ===== 第二步：获取视图对象（按索引硬分配标签）=====
        print("  [step2] getting view objects...", flush=True)
        enum_result = _enumerate_views(doc, paper_w, paper_h)
        view_objects = []
        for idx, (vobj, bb) in enumerate(enum_result):
            if idx < len(zones_info):
                label = zones_info[idx]["label"]
                view_objects.append((vobj, label, bb))
                print(f"    view[{idx}] = {label} bbox=({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm", flush=True)

        if len(view_objects) < 4:
            print(f"  [FAIL] only got {len(view_objects)} views, expected 4", flush=True)
            try: doc.CloseDoc
            except: pass
            _time.sleep(0.5)
            continue

        # ===== 第三步：反向计算缩放并强制设置 ScaleRatio =====
        print("  [step3] forcing scale ratios...", flush=True)
        chosen_scale = 1.0
        for vobj, label, bb in view_objects:
            zi = None
            for z in zones_info:
                if z["label"] == label:
                    zi = z
                    break
            if zi is None:
                continue
            zone_w = zi["zone"][2] - zi["zone"][0]
            zone_h = zi["zone"][3] - zi["zone"][1]
            cur_w = (bb[2] - bb[0]) * 1000
            cur_h = (bb[3] - bb[1]) * 1000
            avail_w = zone_w - GAP_MM
            avail_h = zone_h - GAP_MM
            if cur_w > 0 and cur_h > 0:
                sx = avail_w / cur_w
                sy = avail_h / cur_h
            else:
                sx = sy = 1.0
            required = min(sx, sy, 1.0)
            std_scale = 1.0
            for s in STANDARD_SCALES:
                if s <= required + 0.001:
                    std_scale = s
                    break
            # 转为 (分子, 分母)
            if std_scale >= 1:
                num, den = int(std_scale), 1
            else:
                num, den = 1, int(1 / std_scale)
            # 强制设置 ScaleRatio
            try:
                variant = win32com.client.VARIANT(_VT_ARRAY, [float(num), float(den)])
                vobj.ScaleRatio = variant
                _time.sleep(0.2)
                print(f"    {label}: cur={cur_w:.0f}x{cur_h:.0f} -> {num}:{den} scale", flush=True)
            except Exception as e:
                print(f"    [FAIL] {label} ScaleRatio: {e}", flush=True)
            chosen_scale = min(chosen_scale, std_scale)

        # ===== 第四步：强制移动到区域中心 =====
        print("  [step4] forcing positions...", flush=True)
        for vobj, label, bb in view_objects:
            zi = None
            for z in zones_info:
                if z["label"] == label:
                    zi = z
                    break
            if zi is None:
                continue
            cx, cy = zi["center"]
            target_x = cx / 1000
            target_y = cy / 1000
            try:
                variant = win32com.client.VARIANT(_VT_ARRAY, [target_x, target_y])
                vobj.Position = variant
                _time.sleep(0.2)
                actual = vobj.Position
                ok = abs(actual[0] - target_x) < 0.001 and abs(actual[1] - target_y) < 0.001
                print(f"    {label}: ({target_x:.4f},{target_y:.4f}) -> ({actual[0]:.4f},{actual[1]:.4f}) {'OK' if ok else 'FAIL'}", flush=True)
            except Exception as e:
                print(f"    [FAIL] {label} Position: {e}", flush=True)

        # ===== 第五步：验证结果 =====
        print("  [step5] verifying...", flush=True)
        _time.sleep(0.5)
        enum_result = _enumerate_views(doc, paper_w, paper_h)
        view_bboxes = {}
        for idx, (vobj, bb) in enumerate(enum_result):
            if idx < len(zones_info):
                label = zones_info[idx]["label"]
                view_bboxes[label] = bb
                print(f"    [bbox] {label}: ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm", flush=True)

        fits, fail_label = _check_bounds(view_bboxes, paper_w, paper_h)
        spaced, lv, lj = _check_spacing(view_bboxes, [z["label"] for z in zones_info])
        print(f"  bounds: {'OK' if fits else 'FAIL '+str(fail_label)}", flush=True)
        print(f"  spacing: {'OK' if spaced else 'FAIL '+str(lv)+' vs '+str(lj)}", flush=True)

        if fits and spaced:
            scale_str = f"1:{int(1/chosen_scale)}" if chosen_scale < 1 else f"{int(chosen_scale)}:1"
            print(f"  [SUCCESS] {paper_name} works at {scale_str}!", flush=True)
            final_labels = [z["label"] for z in zones_info]
            final_bboxes = view_bboxes
            used_scale = chosen_scale
            break
        else:
            try: doc.CloseDoc
            except: pass
            _time.sleep(0.5)
            print(f"  [SKIP] {paper_name} fails verification", flush=True)

    if not final_labels:
        return {"ok": False, "error": "no paper size fits with required spacing"}

    # ── Final verification ────────────────────────────────────────
    print("\n=== Final verification ===")
    all_ok = True
    for i in range(len(final_labels)):
        for j in range(i+1, len(final_labels)):
            bb_i = final_bboxes.get(final_labels[i])
            bb_j = final_bboxes.get(final_labels[j])
            if bb_i and bb_j:
                x1a,y1a,x2a,y2a = bb_i
                x1b,y1b,x2b,y2b = bb_j
                sep_h = (x2a+_MIN_GAP<=x1b) or (x2b+_MIN_GAP<=x1a)
                sep_v = (y2a+_MIN_GAP<=y1b) or (y2b+_MIN_GAP<=y1a)
                if not (sep_h or sep_v):
                    print(f"  [WARN] {final_labels[i]} overlaps {final_labels[j]}")
                    all_ok = False
                else:
                    dx = max(0, x1b-x2a, x1a-x2b)
                    dy = max(0, y1b-y2a, y1a-y2b)
                    sp = (dx*dx+dy*dy)**0.5
                    status = "OK" if sp >= _MIN_GAP else "WARN"
                    print(f"  [{status}] {final_labels[i]} <-> {final_labels[j]}: {sp*1000:.0f}mm")

                # Bounds - 使用实际安全区检查
                _pmap = {"A1": (0.841, 0.594), "A2": (0.594, 0.420), "A3": (0.420, 0.297)}
                paper_w_m, paper_h_m = _pmap.get(final_paper, (0.420, 0.297))
                tb_h_m = paper_h_m * _TITLE_BLOCK_RATIO["height"]
                tb_w_m = paper_w_m * _TITLE_BLOCK_RATIO["width"]
                ml_m = _MARGIN_MM["left"] / 1000
                mr_m = _MARGIN_MM["right"] / 1000
                mt_m = _MARGIN_MM["top"] / 1000
                mb_m = _MARGIN_MM["bottom"] / 1000
                safe_x1_m = ml_m
                safe_y1_m = mb_m + tb_h_m
                safe_x2_m = paper_w_m - max(mr_m, tb_w_m / 1000)
                safe_y2_m = paper_h_m - mt_m
                for lbl, bb in [(final_labels[i], bb_i), (final_labels[j], bb_j)]:
                    if bb[0] < safe_x1_m or bb[1] < safe_y1_m or bb[2] > safe_x2_m or bb[3] > safe_y2_m:
                        print(f"  [WARN] {lbl} out of bounds: ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm")
                        all_ok = False

    if all_ok:
        print("  [OK] All views within bounds and properly spaced!")

    result = {"ok": all_ok, "views": final_labels, "paper": final_paper, "scale": used_scale}
    # 强制设置图纸大小为算法选定的尺寸（覆盖模板默认值）
    _pmap = {"A1": (0.841, 0.594), "A2": (0.594, 0.420), "A3": (0.420, 0.297)}
    target_w, target_h = _pmap.get(final_paper, (0.420, 0.297))
    try:
        sheet = final_doc.GetFirstView
        if sheet is not None:
            outline = sheet.GetOutline
            if outline:
                cur_w = (outline[2] - outline[0]) * 1000
                cur_h = (outline[3] - outline[1]) * 1000
                if abs(cur_w - target_w * 1000) > 1 or abs(cur_h - target_h * 1000) > 1:
                    # 图纸大小不对，尝试通过属性设置
                    try:
                        # SW 2020+: SetPaperSize2(paperWidth, paperHeight, landscape)
                        final_doc.SetPaperSize(target_w, target_h, True)
                        _time.sleep(1)
                        print(f"  [paper] resized to {target_w*1000:.0f}x{target_h*1000:.0f}mm", flush=True)
                    except Exception as e:
                        print(f"  [WARN] SetPaperSize failed: {e}", flush=True)
    except Exception as e:
        print(f"  [WARN] paper resize check failed: {e}", flush=True)

    try:
        rc = final_doc.SaveAs3(output_path, 0, 2)
        result["saved"] = os.path.exists(output_path)
        result["path"] = output_path
        result["rc"] = rc
    except Exception as e:
        result["save_error"] = str(e)

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


def cmd_reading(sw):
    """方法1：读取当前活动工程图的实际图纸大小、有效绘图区、标题栏区、所有视图包围盒。

    通过 GetFirstView/GetNextView 链式遍历，用 GetOutline 获取每个视图的
    包围盒（单位：米），从而推算出图纸尺寸和安全区。
    返回 JSON，供 AI 在生成新图纸前确认边界，避免视图出界。
    """
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active drawing document; run 'open' or 'drawing' first"}

    result = {
        "ok": True,
        "doc_title": _prop(d, "GetTitle"),
        "doc_path": _prop(d, "GetPathName") or "",
    }

    # ── 1. 枚举所有视图，收集包围盒 ────────────────────────────
    all_outlines = []  # [(idx, bb_in_meters), ...]
    try:
        v = d.GetFirstView
        idx = 0
        while v is not None and idx < 30:
            try:
                outline = v.GetOutline
                if outline and isinstance(outline, (list, tuple)) and len(outline) == 4:
                    all_outlines.append((idx, outline))
                else:
                    all_outlines.append((idx, None))
            except Exception:
                all_outlines.append((idx, None))
            try:
                v = v.GetNextView
            except Exception:
                break
            idx += 1
    except Exception as e:
        result["enum_views_error"] = str(e)

    # ── 2. 从第一个视图（图纸轮廓）推断图纸尺寸 ─────────────────
    paper_w_mm = paper_h_mm = None
    sheet_bb_m = None
    for idx, bb in all_outlines:
        if bb is None:
            continue
        w_m = bb[2] - bb[0]
        h_m = bb[3] - bb[1]
        w_mm = w_m * 1000
        h_mm = h_m * 1000
        # 图纸轮廓：包围盒接近标准纸张尺寸（≥200mm宽）
        if w_mm >= 200 and h_mm >= 140:
            # 如果是最大的那个，认为是图纸轮廓
            if paper_w_mm is None or w_mm > paper_w_mm:
                paper_w_mm = w_mm
                paper_h_mm = h_mm
                sheet_bb_m = bb
                result["sheet_outline_idx"] = idx
                break  # 第一个符合的通常是图纸轮廓（从左上角开始）

    if paper_w_mm and paper_h_mm:
        orientation = "landscape" if paper_w_mm > paper_h_mm else "portrait"
        result["sheet_size_mm"] = {"width": round(paper_w_mm, 1), "height": round(paper_h_mm, 1),
                                   "orientation": orientation}
        result["paper_name_guess"] = _guess_paper_name(paper_w_mm, paper_h_mm)
    else:
        result["sheet_size_mm"] = None
        result["paper_name_guess"] = None

    # ── 3. 计算安全区 ───────────────────────────────────────────
    if paper_w_mm and paper_h_mm:
        # 默认边距（mm）
        margin_l = 15   # 左边距
        margin_r = 15   # 右边距
        margin_t = 15   # 上边距
        margin_b = 10   # 下边距
        # 标题栏禁区：右下角，宽约 25% 图纸宽，高约 20% 图纸高
        tb_w = paper_w_mm * 0.25
        tb_h = paper_h_mm * 0.20
        # 安全区 = 图纸左下角 (margin_l, margin_b+tb_h) 到右上角 (paper_w-margin_r, paper_h-margin_t)
        safe_x1 = margin_l
        safe_y1 = margin_b + tb_h
        safe_x2 = paper_w_mm - margin_r
        safe_y2 = paper_h_mm - margin_t
        result["safe_zone_mm"] = {
            "x1": safe_x1, "y1": safe_y1,
            "x2": safe_x2, "y2": safe_y2,
            "width":  round(safe_x2 - safe_x1, 1),
            "height": round(safe_y2 - safe_y1, 1),
        }
        result["title_block_zone_mm"] = {
            "x1": safe_x2, "y1": 0,
            "x2": paper_w_mm, "y2": tb_h,
            "desc": "右下角禁区，严禁放任何视图",
        }
        result["margins_mm"] = {"left": margin_l, "right": margin_r,
                                "top": margin_t, "bottom": margin_b}
    else:
        result["safe_zone_mm"] = None
        result["title_block_zone_mm"] = None

    # ── 4. 各视图详情 ───────────────────────────────────────────
    views = []
    for idx, bb in all_outlines:
        if bb is None:
            continue
        # 跳过图纸轮廓
        if sheet_bb_m and abs(bb[0] - sheet_bb_m[0]) < 0.001 and abs(bb[1] - sheet_bb_m[1]) < 0.001:
            continue
        if abs(bb[2] - sheet_bb_m[2]) < 0.001 and abs(bb[3] - sheet_bb_m[3]) < 0.001:
            continue
        cx_mm = (bb[0] + bb[2]) / 2 * 1000
        cy_mm = (bb[1] + bb[3]) / 2 * 1000
        vw_mm = (bb[2] - bb[0]) * 1000
        vh_mm = (bb[3] - bb[1]) * 1000
        in_safe = True
        reason = ""
        sz = result.get("safe_zone_mm")
        tb = result.get("title_block_zone_mm")
        if sz:
            if bb[0] * 1000 < sz["x1"] or bb[1] * 1000 < sz["y1"]:
                in_safe = False
                reason = f"越左/下界 (x={bb[0]*1000:.0f},y={bb[1]*1000:.0f})"
            elif bb[2] * 1000 > sz["x2"] or bb[3] * 1000 > sz["y2"]:
                in_safe = False
                reason = f"越右/上界 (x={bb[2]*1000:.0f},y={bb[3]*1000:.0f})"
            if tb and bb[2] * 1000 >= tb["x1"] and bb[3] * 1000 >= tb["y1"]:
                in_safe = False
                reason = "进入标题栏禁区"
        views.append({
            "index": idx,
            "bbox_mm": {"x1": round(bb[0]*1000,1), "y1": round(bb[1]*1000,1),
                        "x2": round(bb[2]*1000,1), "y2": round(bb[3]*1000,1)},
            "center_mm": {"x": round(cx_mm, 1), "y": round(cy_mm, 1)},
            "size_mm": {"w": round(vw_mm, 1), "h": round(vh_mm, 1)},
            "in_safe_zone": in_safe,
            "reason": reason,
        })

    result["views"] = views
    result["out_of_bound_count"] = sum(1 for v in views if not v.get("in_safe_zone", True))
    result["total_views"] = len(views)
    return result


def _guess_paper_name(w_mm, h_mm):
    """根据尺寸猜测纸张名称。"""
    candidates = [
        ("A4",   297, 210),
        ("A3",   420, 297),
        ("A2",   594, 420),
        ("A1",   841, 594),
        ("A0",  1189, 841),
    ]
    for name, w, h in candidates:
        if abs(w - w_mm) < 10 and abs(h - h_mm) < 10:
            return name
    # 也接受横向/纵向
    for name, w, h in candidates:
        if abs(h - w_mm) < 10 and abs(w - h_mm) < 10:
            return name + "-portrait"
    return f"unknown ({w_mm:.0f}x{h_mm:.0f}mm)"


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
        elif cmd == "reading":
            result = cmd_reading(sw)
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
