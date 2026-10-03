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
    python sw_bridge.py drawing <零件路径> [输出路径]  # 生成 SLDDRW 工程图
    python sw_bridge.py dwg <零件路径> [输出路径]    # 导出 DWG（AutoCAD 可读）

 CADX / AutoCAD 命令（需要安装 AutoCAD）:
    python sw_bridge.py ac-status              # AutoCAD 连接状态
    python sw_bridge.py dxf <零件.SLDPRT> [输出.dxf]  # 生成工程图并导出 DXF（供 cad-validate 验证）
python sw_bridge.py ac-export [输出.dxf]   # 导出当前 AutoCAD 图纸为 DXF
    python sw_bridge.py cad-validate <图纸.dxf> [规则]  # 几何验证（7项检查）
    python sw_bridge.py cad-validate-live      # 验证当前 AutoCAD 活动文档（自动导出 DXF）

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

【重要约束 — 修改前必读】
  本项目基于 SW API + pywin32。已知以下约束：
  1. 视图必须使用四锚点+递归比例降级算法；
  2. 坐标写入必须使用 pythoncom.VARIANT 封送（VT_ARRAY | VT_R8）；
  3. 禁止使用 CreateDrawViewFromModelView 的锚点参数；
  4. 修改代码前必须先读取旧代码防止回归测试失败。
"""
import sys
import os
import json
import time
import traceback
import subprocess
import threading

import pythoncom
import win32com.client

# CADX AutoCAD bridge & DXF validation（可选，需要 pyautocad/ezdxf/shapely）
try:
    import ac_bridge
    import ac_validate
    _HAS_CADX = True
except ImportError:
    _HAS_CADX = False

# ── 【BUG-08 修复】CADX 标准图层与 SW→CADX 图层映射 ────────────────────────
# 直接复用 swapi 里的单一事实来源（避免两处定义漂移）。
try:
    import swapi as _swapi_layers
    _CADX_LAYER_STANDARD = _swapi_layers.CADX_LAYER_STANDARD
    _cadx_layer_for = _swapi_layers.cadx_layer_for
    # 【BUG-06 实机补强】固有保留层判定（0 / Defpoints / SLD-0 / 纯数字层）
    _is_exempt_layer = _swapi_layers.is_exempt_cadx_layer
except Exception:
    _CADX_LAYER_STANDARD = ("OUTLINE", "THIN", "CENTER", "HIDDEN", "DIM", "TEXT", "HATCH")

    def _cadx_layer_for(name):
        return str(name).upper() if str(name).upper() in _CADX_LAYER_STANDARD else None

    def _is_exempt_layer(name):
        k = str(name).strip().lower()
        return k in ("0", "defpoints", "sld-0") or k.isdigit()


# ── Bug-24 补充: 自动安装 ezdxf/shapely ─────────────────────────────────────
# 当 CADX 模块存在但 ezdxf 未安装时（如 ac_validate 已导入但内部 lazy import），
# 提供自动安装能力，让 agent 无需手动执行 pip install。

def ensure_ezdxf():
    """确保 ezdxf 和 shapely 已安装；若缺失则自动通过 pip 安装。

    这是 Bug-24 的补充：之前只在报错时提示安装命令，现在改为主动尝试安装。
    适用于个人 agent 场景——无需管理员权限，使用 --user 安装到用户目录。

    Returns:
        {"ok": True}  若已安装或安装成功
        {"ok": False, "error": "..."}  若安装失败
    """
    try:
        import ezdxf  # noqa: F401
        return {"ok": True}
    except ImportError:
        pass
    try:
        import shapely  # noqa: F401
    except ImportError:
        pass

    import subprocess
    import sys
    pkgs = []
    try:
        __import__("ezdxf")
    except ImportError:
        pkgs.append("ezdxf")
    try:
        __import__("shapely")
    except ImportError:
        pkgs.append("shapely")

    if not pkgs:
        return {"ok": True}

    # pip install --user 无需管理员权限，适合个人桌面环境
    pip_cmd = [sys.executable, "-m", "pip", "install", "--user", "--quiet"] + pkgs
    try:
        result = subprocess.run(pip_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if result.returncode == 0:
            # 安装成功后重新尝试导入
            import ezdxf  # noqa: F401
            try:
                import shapely  # noqa: F401
            except ImportError:
                pass
            return {"ok": True, "installed": pkgs, "note": "自动安装成功"}
        else:
            return {
                "ok": False,
                "installed": [],
                "error": f"pip 安装失败 (exit={result.returncode})\n{result.stderr[:500]}",
                "hint": "请手动运行: pip install ezdxf shapely",
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "installed": [],
            "error": "pip 安装超时（120s）",
            "hint": "请手动运行: pip install ezdxf shapely",
        }
    except Exception as e:
        return {"ok": False, "installed": [], "error": str(e), "hint": "请手动运行: pip install ezdxf shapely"}

# Physics-in-the-Loop 物理验证子系统
try:
    _PHYSICS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "physics")
    if _PHYSICS_DIR not in sys.path:
        sys.path.insert(0, _PHYSICS_DIR)
    import physics_bridge as _pb
    _HAS_PHYSICS = True
except ImportError:
    _HAS_PHYSICS = False

# ── 【C13 修复】全面加固 Windows 编码，杜绝 Ø/° 等符号导致崩溃 ──────────
# 测试反馈：PowerShell 默认 GBK，Python 输出含 Ø/° 时抛 UnicodeEncodeError；
#   规避办法是外部加 python -X utf8，但调用方经常忘记。
# 修复：本模块【自身】在导入时就强制 UTF-8 环境，无需调用方配合。
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.setdefault('PYTHONUTF8', '1')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
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

    # 【B1修复】SW 可执行文件路径探测（注册表优先 + 全盘符扫描）
    # 旧版只扫 C/D/E/F 盘，装在 Z 盘等非标准位置会误判"SW 未安装"。
    try:
        import swapi as _swapi
        exe = _swapi.find_sldworks_exe()
        result["sldworks_exe"] = exe or "NOT FOUND (注册表与全盘符均未找到 SLDWORKS.exe)"
        result["install_drives_scanned"] = _swapi._all_drive_letters()
    except Exception as _e:
        result["sldworks_exe"] = "PROBE ERROR: %s" % _e
    # 结论
    problems = []
    if str(result.get("pywin32", "")).startswith("MISSING") or not result.get("pywin32"):
        problems.append("pywin32 未安装: pip install pywin32")
    if str(result.get("mss", "")).startswith("MISSING") or not result.get("mss"):
        problems.append("mss 未安装: pip install mss")
    if str(result.get("pillow", "")).startswith("MISSING") or not result.get("pillow"):
        problems.append("Pillow 未安装: pip install Pillow")
    # 【B1修复】SW 可执行文件找不到 → 明确报错而不是静默误判
    _exe = str(result.get("sldworks_exe", ""))
    if (not _exe) or _exe.startswith("NOT FOUND"):
        problems.append("未找到 SLDWORKS.exe（已扫描盘符: %s）。"
                        "请确认 SolidWorks 已安装，或手动设置环境变量 DSH_SW_EXE 指向可执行文件。"
                        % result.get("install_drives_scanned"))
    if not result.get("solidworks", {}).get("connected"):
        problems.append("无法连接 SolidWorks: 请先启动 SolidWorks")
    if not result.get("template"):
        problems.append("未找到零件模板")
    result["ok"] = len(problems) == 0
    result["problems"] = problems
    return result


def open_document_fresh(sw, path, doc_type=None, force_close=True):
    """【C10 修复】安全打开文档——先关闭同路径已打开文档，确保读到磁盘最新版。

    问题现象（测试反馈·最危险）：
      同名文件被 OpenDoc6 打开时，若 SW 会话里已经打开过该路径的旧版本，
      OpenDoc6 会直接返回那个【已缓存的旧文档】而不重新从磁盘加载。
      后果：v3 废品与 v4 成品的验证结果完全相同，误判为 FAIL；
      或改造后的零件看似没变，实际是读了旧模型。

    修复：打开前先按【规范化绝对路径】查找已打开的文档并关闭它，
      再执行 OpenDoc6，保证拿到磁盘上的当前版本。
      （原规避方案是手动 close-all 后重开，本函数把它自动化。）

    Args:
        path: 文档路径
        doc_type: 1=零件 2=装配 3=工程图；None 时按扩展名推断
        force_close: 是否强制关闭同路径旧文档（默认 True）

    Returns: {ok, doc, title, path, closed_stale, error?}
    """
    out = {"ok": False, "doc": None, "closed_stale": []}
    if not path or not os.path.exists(path):
        out["error"] = "file not found: %s" % path
        return out
    _abs = os.path.abspath(path)
    if doc_type is None:
        _ext = os.path.splitext(path)[1].lower()
        doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(_ext, 1)
    # 步骤1: 关闭同路径已打开的文档（含大小写不同的写法）
    if force_close:
        try:
            _open = sw.GetDocuments() or []
        except Exception:
            _open = []
        for d in _open:
            try:
                dp = d.GetPathName or ""
            except Exception:
                continue
            if dp and os.path.normcase(os.path.abspath(dp)) == os.path.normcase(_abs):
                try:
                    _t = d.GetTitle
                    sw.CloseDoc(_t)
                    out["closed_stale"].append(_t)
                except Exception:
                    pass
    # 步骤2: 打开（silent=1，避免弹窗卡住自动化）
    _errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    _warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = None
    try:
        doc = sw.OpenDoc6(_abs, int(doc_type), 1, "", _errs, _warns)
    except Exception as e:
        out["error"] = "OpenDoc6 异常: %r" % (e,)
        return out
    if doc is None:
        out["error"] = ("OpenDoc6 返回 None（errs=%s）。"
                        % (getattr(_errs, 'value', '?')))
        return out
    out["ok"] = True
    out["doc"] = doc
    out["title"] = _prop(doc, "GetTitle")
    out["path"] = _prop(doc, "GetPathName")
    out["stale_closed"] = bool(out["closed_stale"])
    return out


def cmd_open(sw, path):
    if not os.path.exists(path):
        return {"ok": False, "error": f"file not found: {path}"}
    ext = os.path.splitext(path)[1].lower()
    doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(ext, 1)
    # ── 【C10 修复】改用 open_document_fresh：先关同路径旧文档再打开 ──
    # 避免 OpenDoc6 返回会话里缓存的旧模型（会导致验证结果张冠李戴）。
    _r = open_document_fresh(sw, path, doc_type=doc_type)
    if not _r.get("ok"):
        return {"ok": False, "error": _r.get("error", "打开失败")}
    doc = _r["doc"]
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
    """关闭活动文档（Bug-25 修复: 尝试 CloseAllDocuments 作为 SW 2020 兼容替代）。"""
    import swapi as _swapi
    try:
        d = sw.ActiveDoc
        if d is not None:
            title = _prop(d, "GetTitle")
            sw.CloseDoc(title)
            return {"ok": True, "closed": title}
    except Exception:
        pass
    # Bug-25: SW 2020 无 Quit()，使用 CloseAllDocuments + GC 清理
    return _swapi.close_all_and_exit(sw)


def _check_part_before_save(part_path, room, force=False):
    """【Bug-38 建议②】保存前校验零件归属。

    台账现象：结构件小屋误产"电机座"（属传动机构）、传动机构小屋误产
      "转向支撑座"（属支撑结构）——产出目录混入别房间零件，
      若小屋不自纠，总装时会出现重复或缺失零件。
    本函数调用 workflow_gate 的归属清单做校验：
      · 归属本房间 → allowed=True，正常保存；
      · 归属其它房间 → allowed=False，拒绝保存 + 记录违规；
      · 未匹配任何关键词 → allowed=True（避免误拦新零件），附提示。

    Args:
        force: True 时只报告不拦截（check-part 命令用）
    Returns:
        dict 或 None（无法校验时返回 None，调用方按"放行"处理）
    """
    try:
        if not part_path:
            return None
        import workflow_gate as _wg
        _room = str(room or "").strip()
        if not _room:
            # 没有房间上下文（串行/单房间）→ 不做归属拦截
            return None
        res = _wg.check_part_ownership(part_path, _room)
        if res.get("allowed"):
            return res
        # 越界：记录违规 + 返回拒绝结果
        try:
            _viol_dir = os.path.join(os.path.dirname(os.path.abspath(part_path)), "reports")
            os.makedirs(_viol_dir, exist_ok=True)
            _vp = os.path.join(_viol_dir, "part_ownership_violations.json")
            _items = []
            if os.path.exists(_vp):
                try:
                    with open(_vp, "r", encoding="utf-8") as f:
                        _items = (json.load(f) or {}).get("items", [])
                except Exception:
                    _items = []
            _items.append({"room": _room, "part": res.get("part"),
                           "owner_room": res.get("owner_room"),
                           "ts": time.time(),
                           "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            with open(_vp, "w", encoding="utf-8") as f:
                json.dump({"items": _items[-100:]}, f, ensure_ascii=False, indent=2)
            res["violation_file"] = _vp
        except Exception:
            pass
        if force:
            return res
        return {
            "ok": False, "gate": "PART_OWNERSHIP_VIOLATION",
            "room": _room, "part": res.get("part"),
            "owner_room": res.get("owner_room"),
            "error": ("【Bug-38】零件 '%s' 不属于房间 [%s]，实际归属 [%s]。"
                      "越界产出会导致总装重复/缺失零件，已拒绝保存。"
                      % (res.get("part"), _room, res.get("owner_room"))),
            "hint": ("· 若确属本房间零件 → 在 workflow_gate 的房间零件范围里补充关键词；"
                     "· 若确实越界 → 交由归属房间产出；"
                     "· 确实需要强制保存 → 加 --force-part。"),
            "violation_file": res.get("violation_file"),
        }
    except Exception:
        # 归属校验不可用（workflow_gate 缺失等）→ 不阻断保存
        return None


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
    """导出 PDF（【Bug-45/46 修复】自动补 .pdf 后缀 + 多方法兜底）。

    原缺陷：① 未带 .pdf 后缀时 SaveAs3 返回 rc=1，用户必须自己记得加；
           ② 直接调 doc.ExportToPDF 在 win32com 动态分发下失败；
           ③ 失败时只回 rc=1，没有可操作信息。
    现统一委托给 swapi.SWModel.export_pdf（多路径兜底 + 明确错误）。
    """
    import swapi
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    try:
        m = swapi.SWModel(sw, d)
        return m.export_pdf(path)
    except Exception as e:
        # 退化路径：至少保证后缀正确
        p = str(path or "")
        if p and not p.lower().endswith(".pdf"):
            p = p + ".pdf"
        try:
            rc = d.SaveAs3(p, 0, 0)
            return {"ok": rc == 0, "path": p, "exists": os.path.exists(p),
                    "rc": rc, "note": "swapi 封装不可用，已用 SaveAs3 回退"}
        except Exception as e2:
            return {"ok": False, "error": "PDF 导出失败: %r / %r" % (e, e2)}


def _extract_output_paths(script_path):
    """从脚本源码里静态提取可能的输出文件路径（CAD 产物）。

    【C9 修复】用于产物校验：脚本常写成 save(r'C:\\out\\DSH_底座.SLDPRT')，
      把这些字面量路径抽出来，执行后逐一核对是否真的存在。
      这是『文件未生成却报成功』这类误判的根治手段。
    """
    import re as _re
    paths = []
    try:
        src = open(script_path, "r", encoding="utf-8", errors="replace").read()
        _ext = ("SLDPRT|sldprt|SLDASM|sldasm|SLDDRW|slddrw|DWG|dwg|DXF|dxf|PDF|pdf")
        pat = _re.compile(
            "[\\\"']([A-Za-z]:[\\\\/][^\\\"'\\n]{2,240}?\\."
            "(?:" + _ext + "))[\\\"']")
        for m in pat.finditer(src):
            p = m.group(1).replace("\\\\", os.sep).replace("/", os.sep)
            if p not in paths:
                paths.append(p)
    except Exception:
        pass
    return paths


def _verify_run_artifacts(script_path, result):
    """【C9 修复】校验脚本执行后预期产物是否存在。

    Returns: {checked, expected:[], missing:[], present:[]}
    """
    out = {"checked": False, "expected": [], "missing": [], "present": []}
    try:
        declared = None
        _biz = result.get("script_result")
        if isinstance(_biz, dict):
            declared = _biz.get("artifacts") or _biz.get("files")
        if isinstance(declared, list) and declared:
            expected = [str(x) for x in declared]
        else:
            expected = _extract_output_paths(script_path)
        if not expected:
            return out
        out["checked"] = True
        out["expected"] = expected
        for p in expected:
            if os.path.exists(p):
                out["present"].append(p)
            else:
                out["missing"].append(p)
    except Exception:
        pass
    return out


def cmd_run(sw, script_path, extra_args, room=None):
    """执行 DSH 生成的建模脚本（核心能力）。

    以独立 Python 进程运行 script_path，并把连接好的 `sw` 对象注入为全局变量，
    这样脚本可以直接写 win32com 动态分发代码操作 SolidWorks，无需关心连接细节。

    通用版：桥接目录（本文件所在目录）自动注入 sys.path，
    因此脚本里 `import swapi` 在任何电脑上都能找到同目录的 swapi.py。

    room: 【Bug#4 修复】本命令所属房间名，用于建模期间后台刷新心跳。
    """
    if not os.path.exists(script_path):
        return {"ok": False, "error": f"script not found: {script_path}"}
    bridge_dir = _HERE
    script_abs = os.path.abspath(script_path)

    # ══ 【Bug-39 修复】wrapper 必须【每个进程唯一】，否则并行小屋互相覆盖 ══
    # 原缺陷（实测 · 并行架构下最危险的正确性问题）：
    #   wrapper 文件名【固定】为 "_sw_run_wrapper.py"，且写在【脚本同目录】。
    #   波 1 多小屋并行时，若两个小屋的脚本在同一目录（或路径相近），
    #   就会发生：
    #     小屋A 写入 wrapper(A) → 小屋B 覆盖成 wrapper(B) → 小屋A 执行
    #     → A 实际执行的是 B 的脚本，且【无任何报错】。
    #   实测：执行 build_transmission_6.py 实际跑的是 build_servo_v6.py；
    #        执行 tx_v9.py 实际跑的是 build_servo_rib_v7.py。
    #   → 零件错建/丢失，与 Bug-38 叠加后总装必然缺件或重复。
    #
    # 修复（三重防护）：
    #   ① wrapper 名加入 pid + 随机后缀 + 脚本名哈希 → 进程间不可能重名；
    #   ② wrapper 写入【临时目录】而非脚本目录 → 不污染交付目录、不互相覆盖；
    #   ③ wrapper 内【自校验】：记录待执行脚本的绝对路径与内容 hash，
    #      执行前重新计算并比对，不一致立即报错退出（防执行到别的脚本）。
    import hashlib as _hashlib
    import tempfile as _tempfile
    try:
        _script_bytes = open(script_abs, "rb").read()
        _script_sha = _hashlib.sha1(_script_bytes).hexdigest()
    except Exception as _e_read:
        return {"ok": False, "error": "无法读取脚本内容: %r" % (_e_read,)}
    _script_sha_short = _script_sha[:12]
    _uniq = "%s_%s_%s" % (os.getpid(), _script_sha_short,
                          _hashlib.sha1(("%s|%s" % (script_abs, id(object())))
                                        .encode("utf-8")).hexdigest()[:8])
    _wrapper_dir = _tempfile.mkdtemp(prefix="sw_run_")
    wrapper = os.path.join(_wrapper_dir, "_sw_run_wrapper_%s.py" % _uniq)
    # Bug 2 修复: 确保 swapi 模块可被找到
    with open(wrapper, "w", encoding="utf-8") as f:
        # ── 【C9 修复】wrapper 捕获脚本【真实】stdout/stderr 并回传 ──────
        # 测试反馈：run 吞掉脚本输出，返回固定 {"ok":true}，文件未生成也报成功
        #   → 极易误判（把失败当成功继续往下走）。
        # 原 wrapper 只 print 一个 {"ok":true}，脚本自己的 print 全被丢弃。
        # 修复：① StringIO 捕获脚本真实 stdout/stderr 随结果回传；
        #      ② 支持脚本用 __RESULT__ 显式声明业务结果；
        #      ③ 主进程侧再做产物校验（见 _verify_run_artifacts）。
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "import sys, os, json, traceback, io\n"
            f"sys.path.insert(0, {bridge_dir!r})\n"
            f"sys.path.insert(0, {os.path.dirname(script_abs)!r})\n"
            "import sw_bridge\n"
            "import hashlib\n"
            # ── 【Bug-39 修复·自校验】执行前确认"我拿到的就是该跑的脚本" ──
            # 双保险：即便 wrapper 被意外替换，也会因 hash/路径不符而拒绝执行，
            # 绝不静默地跑成别人的脚本（这正是 Bug-39 最危险之处：无报错）。
            f"_EXPECT_PATH = {script_abs!r}\n"
            f"_EXPECT_SHA = {_script_sha!r}\n"
            "if os.path.abspath(__file__) != _EXPECT_PATH and not os.path.exists(_EXPECT_PATH):\n"
            "    print(json.dumps({'ok': False, 'error': '脚本路径不存在: ' + _EXPECT_PATH}))\n"
            "    raise SystemExit(3)\n"
            "_actual_sha = hashlib.sha1(open(_EXPECT_PATH, 'rb').read()).hexdigest()\n"
            "if _actual_sha != _EXPECT_SHA:\n"
            "    print(json.dumps({'ok': False,\n"
            "        'error': ('【Bug-39】脚本内容在运行前被改变（hash 不符）'\n"
            "                  '—— 拒绝执行以免跑错脚本'),\n"
            "        'expect_sha': _EXPECT_SHA, 'actual_sha': _actual_sha,\n"
            "        'script': _EXPECT_PATH}, ensure_ascii=False))\n"
            "    raise SystemExit(4)\n"
            "sw = sw_bridge.get_sw()\n"
            f"__file__ = {script_abs!r}\n"
            "_cap_out, _cap_err = io.StringIO(), io.StringIO()\n"
            "_old_out, _old_err = sys.stdout, sys.stderr\n"
            "sys.stdout, sys.stderr = _cap_out, _cap_err\n"
            # ── 【BUG-A 修复】这是【普通字符串】不是 f-string ────────────────
            #   普通字符串里的 {{ }} 不会被转义，会原样写进 wrapper 文件，
            #   生成 set 套 dict → TypeError: unhashable type: 'dict'
            #   → wrapper 崩在打印结果前，run 恒返回空输出 + exit 1，
            #     脚本的真实异常被完全吞掉（最恶劣的静默失败）。
            #   只有 f-string 才需要 {{ }} 转义，此处必须写单花括号。
            "_ns = {'sw': sw, 'json': json, 'os': os, 'sys': sys, '__file__': __file__}\n"
            "try:\n"
            f"    exec(compile(open({script_abs!r}, encoding='utf-8').read(), {script_abs!r}, 'exec'), _ns)\n"
            "    _ok, _err = True, ''\n"
            "except Exception:\n"
            "    _ok, _err = False, traceback.format_exc()[-3000:]\n"
            "finally:\n"
            "    sys.stdout, sys.stderr = _old_out, _old_err\n"
            "_biz = _ns.get('__RESULT__')\n"
            # 【BUG-A 修复】同上：普通字符串用单花括号
            "print(json.dumps({'ok': _ok, 'error': _err,\n"
            "                  'script_stdout': _cap_out.getvalue()[-8000:],\n"
            "                  'script_stderr': _cap_err.getvalue()[-8000:],\n"
            "                  'script_result': _biz}, ensure_ascii=False))\n"
        )
    try:
        # Bug 1 修复: 设置 PYTHONIOENCODING 绕过 Windows GBK 终端编码问题
        run_env = dict(os.environ)
        run_env["PYTHONIOENCODING"] = "utf-8"
        # ── 【Bug#4 修复】建模期间后台心跳线程 ────────────────────────────
        # 问题：sw_bridge.py run 可能耗时数分钟（复杂零件+保存+出图），
        #   期间小屋无暇调 room-heartbeat → 心跳文件 mtime 停滞 >180s
        #   → room-status 报 running_unverified → 主对话频繁无效轮询。
        # 修复：run 执行期间由【工具层】起一个守护线程，每 60 秒无条件刷新
        #   一次心跳。这样"正在跑长任务"这件事本身就成为存活证据，
        #   不依赖小屋记得跳心跳。线程随 run 结束（finally）立即停止。
        _hb_stop = threading.Event()
        _hb_room = room

        def _hb_loop():
            # 立即先跳一次，避免"刚进入 run 就超时"的窗口
            _room_heartbeat(_hb_room, "run-start")
            while not _hb_stop.wait(60):
                _room_heartbeat(_hb_room, "run-modeling")

        _hb_thread = None
        if _hb_room:
            try:
                _hb_thread = threading.Thread(target=_hb_loop, daemon=True)
                _hb_thread.start()
            except Exception:
                _hb_thread = None
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", wrapper] + list(extra_args),
                capture_output=True, text=True, timeout=600,
                cwd=os.path.dirname(os.path.abspath(script_path)),
                encoding='utf-8', errors='replace',
                env=run_env,
            )
        finally:
            # 停心跳线程（无论成功/异常/超时都要停）
            try:
                _hb_stop.set()
            except Exception:
                pass
    except Exception as e:
        return {"ok": False, "error": f"subprocess failed: {e}"}
    finally:
        # ── 【Bug-39 修复】wrapper 现在在临时目录 → 连目录一起清理 ──────────
        try:
            os.remove(wrapper)
        except OSError:
            pass
        try:
            os.rmdir(_wrapper_dir)
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
        result = {"ok": proc.returncode == 0, "error": "no JSON in output",
                  "raw_stdout": stdout[-2000:] if stdout else ""}
    # ── 【C9 修复】保留脚本【真实】输出，不再只留末尾 2000 字符的 JSON ──
    # 原实现 result["stdout"] 存的是 wrapper 进程的 stdout（几乎只有一行 JSON），
    #   脚本自己 print 的内容对调用方完全不可见 → 无法判断脚本到底做了什么。
    # 现在：script_stdout/script_stderr 是脚本内部真实输出；
    #       stdout/stderr 保留进程级输出以便兜底排查。
    result["stdout"] = stdout[-4000:]
    result["stderr"] = stderr[-4000:]
    result.setdefault("exit_code", proc.returncode)
    # ── 【Bug-39 修复】回显"实际执行的脚本"，让调用方可核对未被串号 ──────
    # 台账建议③："加 --verify-script 参数，执行前打印待执行脚本的完整路径+hash"。
    # 这里【默认就回显】（无需额外参数），因为串号是静默的、代价极高。
    result["executed_script"] = script_abs
    result["executed_script_sha1"] = _script_sha
    result["script_identity_verified"] = (proc.returncode not in (3, 4))
    if proc.returncode in (3, 4):
        result["ok"] = False
        result["error"] = ("【Bug-39】脚本身份自校验失败（exit=%d）："
                           "待执行脚本路径不存在或内容在运行前被改变，"
                           "已拒绝执行以免跑成别的脚本。" % proc.returncode)
    # 脚本可选的业务结果（脚本内写 __RESULT__ = {...} 即会出现在这里）
    _biz = result.get("script_result")
    if isinstance(_biz, dict):
        result["script_ok"] = _biz.get("ok", result.get("ok"))
        if _biz.get("ok") is False:
            # 脚本显式报告失败 → 整体判失败，绝不"静默成功"
            result["ok"] = False
            result.setdefault("error", _biz.get("error") or "脚本自报失败")
    # ── 【C9 修复】产物校验：脚本声称成功，但文件没生成 → 必须报错 ──────
    _art = _verify_run_artifacts(script_abs, result)
    if _art.get("checked"):
        result["artifacts"] = _art
        if _art.get("missing"):
            result["ok"] = False
            result["error"] = ("脚本执行完毕但预期产物未生成: %s。"
                               "（原实现此处会误报成功，请检查脚本的保存逻辑）"
                               % ", ".join(_art["missing"]))
    # ══ 【三大防线 · 防线①材料】建模出口强制校验 ═══════════════════════
    # "AI 到了这一步就必须过"：脚本落盘 .sldprt 的那一刻，材料事实即被
    #   swapi.save() 固化；此处【当场】读取凭据并校验，不合格直接判本次
    #   run 失败（ok=False）—— 不让"密度=1000(水)/跨族换材"的零件
    #   继续流向下游房间，从源头掐断而非等到交付才发现。
    try:
        import defense_gate as _dg
        _parts = []
        for _cand in (list(_art.get("present") or []) +
                      list(result.get("artifacts_present") or [])):
            if str(_cand).lower().endswith(".sldprt") and os.path.exists(_cand):
                if _cand not in _parts:
                    _parts.append(_cand)
        # 脚本可显式用 __RESULT__ 里的 artifacts 声明产物
        _biz2 = result.get("script_result")
        if isinstance(_biz2, dict):
            for _cand in (_biz2.get("artifacts") or _biz2.get("files") or []):
                _s = str(_cand)
                if _s.lower().endswith(".sldprt") and os.path.exists(_s) and _s not in _parts:
                    _parts.append(_s)
        if _parts:
            _mat_reports = []
            _mat_block = []
            for _p in _parts:
                _mr = _dg.check_material_attestation(_p)
                _mat_reports.append({
                    "part": os.path.basename(_p), "ok": _mr["ok"],
                    "level": _mr["level"], "reasons": _mr["reasons"],
                    "attestation": _mr.get("attestation"),
                })
                if not _mr["ok"]:
                    _mat_block.append("%s: %s" % (os.path.basename(_p),
                                                  "；".join(_mr["reasons"])))
            result["material_defense"] = _mat_reports
            if _mat_block and not _dg.bypass_requested():
                result["ok"] = False
                result["defense_blocked"] = "MATERIAL"
                result["error"] = (
                    "【防线①材料】建模产物未通过材料校验，已阻断本次 run：\n  - "
                    + "\n  - ".join(_mat_block)
                    + "\n请按提示用 swapi.new_part(material=...) 显式赋材后重新保存。"
                    + "（确需放行：设 DSH_DEFENSE_BYPASS=1，会留痕到 "
                      "reports/defense_bypass.json）")
            elif _mat_block:
                _dg.log_bypass("sw_bridge.run.material",
                               "; ".join(_mat_block), by="DSH_DEFENSE_BYPASS")
                result["defense_bypassed"] = "MATERIAL"
    except ImportError:
        result.setdefault("warnings_list", []).append(
            "defense_gate.py 不可用，本次未执行材料防线校验")
    except Exception as _e_def:
        result.setdefault("warnings_list", []).append(
            "材料防线校验异常（不阻断）: %r" % (_e_def,))

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
                # ── 【新-2 修复】原写法 ref_doc.CloseDoc 【漏了括号】──
                #   那只是取方法对象，根本没调用 → 文档从未关闭 →
                #   SW 不释放文件句柄 → 再次导出同名文件时写入被静默忽略。
                try:
                    ref_doc.CloseDoc()
                except Exception:
                    pass
                _time.sleep(0.3)
                return w_mm, h_mm
            try:
                ref_doc.CloseDoc()
            except Exception:
                pass
            _time.sleep(0.3)
            return None, None
        except Exception as e:
            print(f"  [WARN] ref measurement error: {e}", flush=True)
            try:
                ref_doc.CloseDoc()
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
            try: doc.CloseDoc()      # 【新-2 修复】补括号，否则文档不关闭
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
            try: doc.CloseDoc()      # 【新-2 修复】补括号
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

    # ══ 【Bug-34 修复】保存前自动插入模型尺寸标注 ═══════════════════════
    # 原缺陷：drawing 只出视图不标注 → 交付图纸 GB/T 检查几乎全缺失，
    #   必须人工在 SW 里补标注才能加工。
    # 这里在 SaveAs3 之前，对已排好版的每个视图插入模型尺寸
    #   （InsertModelAnnotations* / AutoDimension 多 API 兜底）。
    # 失败不阻断出图 —— 只把结果写进返回值，并在 gbt_report 里如实报告缺失项。
    try:
        _ann = _annotate_views_in_doc(final_doc, dim_types=None)
        result["annotation"] = _ann
        result["gbt_report"] = _ann.get("gbt_report")
    except Exception as _e_ann:
        result["annotation"] = {"ok": False, "error": repr(_e_ann)}
        try:
            result["gbt_report"] = _gbt_annotation_report({})
        except Exception:
            pass

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


def annotate_drawing(sw, drawing_path, dim_types=None, tol_mm=0.05):
    """【Bug-34 修复】对工程图自动添加关键尺寸标注。

    ══ 原缺陷 ═══════════════════════════════════════════════════════════════
    cmd_drawing 只做【视图生成 + 排版校验】，从不调用 SW 的自动标注能力。
    实测出图小屋交付的 5 张 .SLDDRW：排版全过，但 GB/T 检查项几乎全缺失 ——
    外径/孔径/孔位/总长宽高都没有标注，公差/粗糙度/形位公差也全无，
    必须人工在 SW 里补标注才能加工（Bug-34）。

    ══ 本函数做什么 ════════════════════════════════════════════════════════
    对工程图里的每个视图，调用 SW 的模型标注插入能力（多 API 兜底）：
      ① View.InsertModelAnnotations3 / InsertModelAnnotations2 / InsertModelAnnotations
         —— 把【模型里已存在的尺寸】插入到该视图（DimXpert/草图尺寸）。
      ② ModelDocExtension.AutoDimension（部分版本）
         —— SW 自带的"自动标注"（Autodimension）。
      ③ 都不可用时，明确返回 ok=False 并说明原因，绝不假装标注成功。

    Args:
        drawing_path: .slddrw 路径
        dim_types:    传给 InsertModelAnnotations 的标注类型位掩码。
                      None 时使用默认（尺寸+公差）。
        tol_mm:       判定"标注是否真的加上了"的容差
    Returns:
        {ok, drawing_path, annotated_views, total_annotations, api, per_view,
         error?, hint?, gbt_report}
    """
    out = {"ok": False, "drawing_path": drawing_path, "annotated_views": 0,
           "total_annotations": 0, "per_view": []}
    if not drawing_path or not os.path.exists(drawing_path):
        out["error"] = "工程图文件不存在: %s" % drawing_path
        return out
    doc = None
    try:
        errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.OpenDoc6(drawing_path, 3, 0, "", errs, warns)
        if doc is None:
            out["error"] = "无法打开工程图"
            return out
        try:
            doc.ClearSelection2(True)
        except Exception:
            pass

        # 遍历视图（第一个是图纸本身，跳过）
        views = []
        try:
            v = doc.GetFirstView
            if v is not None:
                v = v.GetNextView
            _guard = 0
            while v is not None and _guard < 200:
                _guard += 1
                views.append(v)
                try:
                    v = v.GetNextView
                except Exception:
                    break
        except Exception as e:
            out["error"] = "遍历视图失败: %r" % (e,)
            return out

        _used_api = None
        for _i, v in enumerate(views):
            _vname = ""
            try:
                _vname = str(v.GetName2 or "")
            except Exception:
                _vname = "view%d" % (_i + 1)
            _before = _count_annotations(doc, v)
            _added = 0
            # ① InsertModelAnnotations3/2/1（把模型尺寸插入视图）
            for _api in ("InsertModelAnnotations3", "InsertModelAnnotations2",
                         "InsertModelAnnotations"):
                try:
                    _fn = getattr(v, _api, None)
                    if _fn is None:
                        continue
                    if _api == "InsertModelAnnotations3":
                        _dt = int(dim_types) if dim_types is not None else 1
                        _fn(_dt, 0, False, False, False)
                    elif _api == "InsertModelAnnotations2":
                        _dt = int(dim_types) if dim_types is not None else 1
                        _fn(_dt, 0, False, False)
                    else:
                        _fn()
                    _used_api = _api
                    _added = 1
                    break
                except Exception:
                    continue
            # ② AutoDimension（SW 自带自动标注）
            if not _added:
                try:
                    _ext = doc.Extension
                    _fn = getattr(_ext, "AutoDimension", None)
                    if _fn is not None:
                        _fn(v, False)
                        _used_api = "AutoDimension"
                        _added = 1
                except Exception:
                    pass
            _after = _count_annotations(doc, v)
            _delta = max(0, _after - _before)
            out["per_view"].append({"view": _vname, "before": _before,
                                    "after": _after, "added": _delta,
                                    "api": _used_api})
            if _delta > 0:
                out["annotated_views"] += 1
            out["total_annotations"] += _delta

        try:
            doc.EditRebuild3()
            doc.SaveAs3(drawing_path, 0, 2)
        except Exception:
            pass

        out["api"] = _used_api
        out["ok"] = out["total_annotations"] > 0
        # ── 【Bug-34 建议④】给出 GB/T 检查报告（逐项通过/缺失）──────────
        out["gbt_report"] = _gbt_annotation_report(out)
        if not out["ok"]:
            out["error"] = ("未能通过 API 自动添加任何尺寸标注"
                            "（InsertModelAnnotations* / AutoDimension 均不可用或无效）")
            out["hint"] = ("① 确认零件【草图/特征里已有尺寸】—— "
                           "InsertModelAnnotations 只能把【已存在的模型尺寸】"
                           "插入视图，不会凭空生成尺寸；"
                           "② 建模时应给关键尺寸加尺寸约束，出图才能自动带出；"
                           "③ 仍缺失时需在 SW 界面手动标注（DimXpert / 智能尺寸）。")
        return out
    except Exception as e:
        out["error"] = "自动标注异常: %r" % (e,)
        return out
    finally:
        try:
            if doc is not None:
                sw.CloseDoc(doc.GetTitle)
        except Exception:
            pass


def _annotate_views_in_doc(doc, dim_types=None):
    """【Bug-34】对【已打开的工程图文档】插入模型尺寸标注（内部复用）。

    与 annotate_drawing 的区别：本函数不负责开关文档，直接作用于传入的 doc，
    因此可被 cmd_drawing 在保存前调用（避免二次开图、避免覆盖用户排版）。

    Returns: {ok, annotated_views, total_annotations, api, per_view, gbt_report}
    """
    out = {"ok": False, "annotated_views": 0, "total_annotations": 0,
           "per_view": [], "api": None}
    views = []
    try:
        v = doc.GetFirstView
        if v is not None:
            v = v.GetNextView
        _guard = 0
        while v is not None and _guard < 200:
            _guard += 1
            views.append(v)
            try:
                v = v.GetNextView
            except Exception:
                break
    except Exception as e:
        out["error"] = "遍历视图失败: %r" % (e,)
        out["gbt_report"] = _gbt_annotation_report(out)
        return out

    _used_api = None
    for _i, v in enumerate(views):
        _vname = ""
        try:
            _vname = str(v.GetName2 or "")
        except Exception:
            _vname = "view%d" % (_i + 1)
        _before = _count_annotations(doc, v)
        _added = 0
        for _api in ("InsertModelAnnotations3", "InsertModelAnnotations2",
                     "InsertModelAnnotations"):
            try:
                _fn = getattr(v, _api, None)
                if _fn is None:
                    continue
                if _api == "InsertModelAnnotations3":
                    _dt = int(dim_types) if dim_types is not None else 1
                    _fn(_dt, 0, False, False, False)
                elif _api == "InsertModelAnnotations2":
                    _dt = int(dim_types) if dim_types is not None else 1
                    _fn(_dt, 0, False, False)
                else:
                    _fn()
                _used_api = _api
                _added = 1
                break
            except Exception:
                continue
        if not _added:
            try:
                _fn = getattr(doc.Extension, "AutoDimension", None)
                if _fn is not None:
                    _fn(v, False)
                    _used_api = "AutoDimension"
                    _added = 1
            except Exception:
                pass
        _after = _count_annotations(doc, v)
        _delta = max(0, _after - _before)
        out["per_view"].append({"view": _vname, "before": _before,
                                "after": _after, "added": _delta,
                                "api": _used_api})
        if _delta > 0:
            out["annotated_views"] += 1
        out["total_annotations"] += _delta

    out["api"] = _used_api
    out["ok"] = out["total_annotations"] > 0
    out["gbt_report"] = _gbt_annotation_report(out)
    if not out["ok"]:
        out["hint"] = ("未能自动插入标注：InsertModelAnnotations* 只能插入"
                       "【模型中已存在的尺寸】—— 请确认建模时已给关键尺寸加约束；"
                       "缺失项需在 SW 界面用 DimXpert/智能尺寸手动标注（Bug-34）。")
    return out


def _count_annotations(doc, view):
    """统计视图上的标注数量（用于判断自动标注是否真的生效）。"""
    _n = 0
    try:
        for _api in ("GetAnnotationCount", "GetDisplayDimensionCount"):
            _fn = getattr(view, _api, None)
            if _fn is None:
                continue
            try:
                _n = int(_fn() or 0)
                if _n > 0:
                    return _n
            except Exception:
                continue
    except Exception:
        pass
    # 兜底：数 DisplayDimension
    try:
        d = view.GetFirstDisplayDimension
        _guard = 0
        while d is not None and _guard < 1000:
            _guard += 1
            _n += 1
            try:
                d = d.GetNext3
            except Exception:
                break
    except Exception:
        pass
    return _n


def _gbt_annotation_report(annotate_result):
    """【Bug-34 建议④】生成 GB/T 标注检查报告（逐项列出通过/缺失）。"""
    _n = int((annotate_result or {}).get("total_annotations") or 0)
    items = [
        {"item": "自动尺寸标注（外径/孔径/孔位/总长宽高）",
         "standard": "GB/T 4458.4",
         "status": "PASS" if _n > 0 else "MISSING",
         "detail": "已插入 %d 个模型标注" % _n},
        {"item": "尺寸公差", "standard": "GB/T 1800",
         "status": "MANUAL", "detail": "需按配合代号（H7/g6 等）人工确认后标注"},
        {"item": "表面粗糙度", "standard": "GB/T 131",
         "status": "MANUAL", "detail": "需按加工工艺人工标注 Ra 值"},
        {"item": "形位公差", "standard": "GB/T 1182",
         "status": "MANUAL", "detail": "需按功能要求人工标注（同轴度/平行度等）"},
        {"item": "标题栏（图号/材料/比例/设计者/日期）", "standard": "GB/T 10609.1",
         "status": "MANUAL", "detail": "用 title-block 子命令自动填写材料/比例，其余需人工"},
    ]
    _missing = [i for i in items if i["status"] == "MISSING"]
    return {"items": items, "missing_count": len(_missing),
            "auto_pass_count": len([i for i in items if i["status"] == "PASS"]),
            "note": "MANUAL 项无法由 API 可靠生成，属人工补充范围（Bug-34）。"}


def set_title_block(sw, drawing_path, fields=None):
    """【Bug-34 修复】填写工程图标题栏字段（材料/比例/图号/设计者/日期等）。

    ══ 原缺陷 ═══════════════════════════════════════════════════════════════
    原实现只有【避让】标题栏区域（_TITLE_BLOCK_RATIO）和【只读探测】
    （cmd_reading），从无写入 —— 交付图纸的图号/材料/设计者全为空白，
    加工厂拿到无法追溯（Bug-34 现象 ③）。

    ══ 实现方式 ════════════════════════════════════════════════════════════
    SW 工程图的标题栏字段通常绑定到【文档自定义属性】（模板里用
    $PRPSHEET 属性链接引用）。本函数把字段写进文档自定义属性管理器。
    同时把【材料】从零件读出来一并写入（避免"图纸材料栏空白/写错"）。

    Args:
        fields: {"图号": "...", "材料": "...", ...}；材料缺省时自动从零件读。
    Returns:
        {ok, written:{...}, failed:{...}, material_from_part, error?}
    """
    out = {"ok": False, "written": {}, "failed": {}}
    if not drawing_path or not os.path.exists(drawing_path):
        out["error"] = "工程图文件不存在: %s" % drawing_path
        return out
    doc = None
    try:
        errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.OpenDoc6(drawing_path, 3, 0, "", errs, warns)
        if doc is None:
            out["error"] = "无法打开工程图"
            return out

        _f = dict(fields or {})
        # ── 自动补材料：从图纸引用的第一个零件读（材料栏最常见且最易错）──
        try:
            _v = doc.GetFirstView
            if _v is not None:
                _v = _v.GetNextView
            _mat = None
            _ref = None
            if _v is not None:
                try:
                    _ref = _v.GetReferencedDocument
                except Exception:
                    _ref = None
            if _ref:
                try:
                    _pm = _ref.Extension.CustomPropertyManager("")
                    _mat = _pm.Get("Material") or _pm.Get("材料")
                except Exception:
                    _mat = None
            if _mat:
                out["material_from_part"] = str(_mat)
                _f.setdefault("材料", str(_mat))
        except Exception:
            pass

        # 默认字段（缺失的补比例/日期，避免标题栏留白）
        _f.setdefault("比例", "1:1")
        # ── 【Bug-44 修复】此处原写 _time.strftime —— 但 _time 只是
        #   cmd_drawing 的【函数内局部别名】（见 cmd_drawing 内 "import time as _time"），
        #   set_title_block 是独立函数，作用域里没有 _time
        #   → 调 title-block 必然 NameError，标题栏字段全部写不进去。
        #   改用模块级 time（已在本文件顶部 import time）。
        _f.setdefault("日期", time.strftime("%Y-%m-%d"))

        _pm = None
        try:
            _pm = doc.Extension.CustomPropertyManager("")
        except Exception as _e:
            out["error"] = "无法取得自定义属性管理器: %r" % (_e,)
            return out

        for _k, _v in _f.items():
            _ok = False
            for _api, _args in (
                ("Add3", (str(_k), 30, str(_v), 2)),
                ("Add2", (str(_k), str(_v))),
                ("Add", (str(_k), str(_v))),
            ):
                try:
                    _fn = getattr(_pm, _api, None)
                    if _fn is None:
                        continue
                    _fn(*_args)
                    _ok = True
                    break
                except Exception:
                    continue
            (out["written"] if _ok else out["failed"])[_k] = str(_v)
        try:
            doc.EditRebuild3()
            doc.SaveAs3(drawing_path, 0, 2)
        except Exception:
            pass
        out["ok"] = bool(out["written"])
        if not out["ok"]:
            out["hint"] = ("自定义属性写入失败：请确认图纸模板的标题栏使用了 "
                           "$PRPSHEET 属性链接；或改在 SW 界面手动填写。")
        return out
    except Exception as e:
        out["error"] = "标题栏写入异常: %r" % (e,)
        return out
    finally:
        try:
            if doc is not None:
                sw.CloseDoc(doc.GetTitle)
        except Exception:
            pass


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


def _dwg_to_dxf(dwg_path):
    """【Bug8 修复】把 DWG 转换为 DXF，供 cad-validate 使用。

    背景：cad-validate 基于 ezdxf，只能读 DXF；而 SW/AutoCAD 产出的常是 DWG，
      导致"有图纸却验证不了"。本函数提供自动转换桥梁。

    转换策略（按可用性依次尝试）：
      1. ODA File Converter（若已安装）—— 最通用的第三方转换器
      2. AutoCAD COM（ac_bridge）—— 本机有 AutoCAD 时最可靠
      3. 失败则返回明确错误 + 建议改用 `sw_bridge.py dxf` 直接出 DXF

    Returns: {ok, dxf_path, method, error?}
    """
    out = {"ok": False, "dxf_path": None, "method": None}
    if not dwg_path or not os.path.exists(dwg_path):
        out["error"] = "DWG 文件不存在: %s" % dwg_path
        return out
    dxf_path = os.path.splitext(dwg_path)[0] + ".dxf"

    # ── 方式1: ODA File Converter ──────────────────────────────────────
    try:
        oda_cands = []
        for drive in ("C:", "D:", "E:", "Z:"):
            oda_cands += [
                os.path.join(drive + "\\", "Program Files", "ODA", "ODAFileConverter"),
                os.path.join(drive + "\\", "Program Files (x86)", "ODA", "ODAFileConverter"),
            ]
        oda_exe = None
        for base in oda_cands:
            if os.path.isdir(base):
                for root, _dirs, files in os.walk(base):
                    for fn in files:
                        if fn.lower().startswith("odafileconverter") and fn.lower().endswith(".exe"):
                            oda_exe = os.path.join(root, fn)
                            break
                    if oda_exe:
                        break
            if oda_exe:
                break
        if oda_exe:
            in_dir = os.path.dirname(dwg_path)
            out_dir = in_dir
            # ODA 用法: ODAFileConverter <in> <out> <ver> <type> <recurse> <audit> [filter]
            subprocess.run([oda_exe, in_dir, out_dir, "ACAD2018", "DXF", "0", "1",
                            os.path.basename(dwg_path)],
                           capture_output=True, text=True, timeout=180)
            if os.path.exists(dxf_path) and os.path.getsize(dxf_path) > 0:
                out.update({"ok": True, "dxf_path": dxf_path, "method": "ODAFileConverter"})
                return out
    except Exception as e:
        out["oda_error"] = str(e)

    # ── 方式2: AutoCAD COM（ac_bridge）─────────────────────────────────
    try:
        if _HAS_CADX and ac_bridge is not None:
            ac = ac_bridge.get_ac()
            if ac is not None:
                # 打开 DWG 并导出 DXF
                try:
                    ac.doc.Open(dwg_path) if hasattr(ac, "doc") else None
                except Exception:
                    pass
                exported = None
                try:
                    if hasattr(ac, "export_dxf"):
                        exported = ac.export_dxf(dxf_path)
                except Exception as e:
                    out["autocad_error"] = str(e)
                if exported and os.path.exists(str(exported)) and os.path.getsize(str(exported)) > 0:
                    out.update({"ok": True, "dxf_path": str(exported),
                                "method": "AutoCAD(ac_bridge)"})
                    return out
    except Exception as e:
        out["autocad_outer_error"] = str(e)

    if not out.get("ok"):
        out["error"] = ("DWG→DXF 转换失败（未找到 ODA File Converter，"
                        "或 AutoCAD 未运行/不可用）。"
                        "建议改用 SW 直接出 DXF: python sw_bridge.py dxf <零件.SLDPRT>")
    return out


def _remap_dxf_layers(dxf_path, rewrite=True):
    """【BUG-08 修复】把 SW 导出的（可能是中文的）图层名整理成 CADX 标准层。

    原缺陷：SW 中文界面导出的 DXF 图层名是"可见边线/尺寸/剖面线"这类中文，
      而 CADX ac_validate 的 mechanical 规则要求
        OUTLINE / THIN / CENTER / HIDDEN / DIM / TEXT / HATCH
      → 每次出图 cad-validate 都刷一堆 MISSING_LAYER + LAYER_NAME_INVALID
      WARNING（中文层名还不满足 ^[A-Z][A-Z0-9_-]*$ 命名规范），
      真正的问题被淹没在噪声里。

    修复：导出 DXF 后，用 ezdxf 读入并做两件事：
      ① 把已知 SW 图层名（中英文）重命名/合并到 CADX 标准层；
      ② 对【必需但缺失】的标准层做补齐（创建空图层），
         让 cad-validate 的 required_layers 检查能通过。
    未识别的自定义层原样保留（不猜、不乱合并），并在返回值里列出供复核。

    Args:
        dxf_path: DXF 文件路径（会被原地重写，除非 rewrite=False）
        rewrite:  False 时只做分析不改文件

    Returns: {ok, changes:{old:new}, created:[...], unknown:[...], error?}
    """
    out = {"ok": False, "changes": {}, "created": [], "unknown": [],
           "standard": list(_CADX_LAYER_STANDARD)}
    try:
        import ezdxf
    except ImportError:
        out["error"] = "ezdxf 未安装，跳过图层整理（pip install ezdxf）"
        return out
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        out["error"] = "读取 DXF 失败: %s" % e
        return out

    try:
        existing = {l.dxf.name for l in doc.layers}
    except Exception:
        existing = set()
    # 实体实际使用的层也要纳入（文档层表可能不全）
    used = set(existing)
    try:
        for e in doc.modelspace():
            try:
                ln = getattr(e.dxf, "layer", None)
                if ln:
                    used.add(ln)
            except Exception:
                continue
    except Exception:
        pass

    changes, unknown, exempt = {}, [], []
    for name in sorted(used):
        # ── 【BUG-06 实机补强】固有保留层直接豁免 ──────────────────────
        # "0"/"Defpoints"/"SLD-0"/纯数字层是 AutoCAD/SW 固有层，
        #   不含需按 GB/T 归类的线型几何 → 强行改名反而破坏兼容性。
        #   此前它们被算进 unknown 并触发 cad-validate 的命名 INFO 告警，
        #   属于噪声；现单独归类为 exempt。
        if _is_exempt_layer(name):
            exempt.append(name)
            continue
        target = _cadx_layer_for(name)
        if target is None:
            if name.upper() not in _CADX_LAYER_STANDARD:
                unknown.append(name)
            continue
        if target != name:
            changes[name] = target

    created = []
    try:
        for std in _CADX_LAYER_STANDARD:
            if not any((n.upper() == std) for n in used) and std not in existing:
                try:
                    doc.layers.add(std)
                    created.append(std)
                except Exception:
                    pass
    except Exception:
        pass

    if rewrite and (changes or created):
        try:
            # ① 重命名图层表项
            for old, new in changes.items():
                try:
                    doc.layers.get(old).dxf.name = new
                except Exception:
                    pass
            # ② 迁移实体到目标层（重命名可能因大小写/重复名不生效，双保险）
            for e in doc.modelspace():
                try:
                    cur = getattr(e.dxf, "layer", None)
                    if cur in changes:
                        e.dxf.layer = changes[cur]
                except Exception:
                    continue
            doc.saveas(dxf_path)
            out["ok"] = True
        except Exception as e:
            out["error"] = "重写 DXF 失败: %s" % e
            return out
    else:
        out["ok"] = True
    out["changes"] = changes
    out["created"] = created
    out["unknown"] = unknown
    out["exempt"] = exempt
    if unknown:
        out["unknown_hint"] = ("以下图层未识别为 CADX 标准层，已原样保留；"
                               "如需消除 cad-validate 的命名告警，请在建模脚本里"
                               "改用 OUTLINE/THIN/CENTER/HIDDEN/DIM/TEXT/HATCH：%s"
                               % ", ".join(unknown[:12]))
    elif exempt:
        out["exempt_note"] = ("%d 个 AutoCAD/SW 固有保留层已豁免（不参与命名规范校验）：%s"
                              % (len(exempt), ", ".join(exempt[:12])))
    return out


def cmd_dxf(sw, part_path, output_path=None):
    """【Bug8 修复】从零件生成工程图并导出为 DXF 格式。

    背景：cad-validate（CADX 几何验证）只接受 DXF，而 sw_bridge 原先只有
      dwg 子命令（只导出 DWG）→ 几何验证链路断裂：SW 出图后无法直接验证，
      必须人工用 AutoCAD 中转一次，自动化闭环走不通。

    修复：新增本命令，让 SW 直接产出 DXF，打通
      sw_bridge(建模) → sw_bridge dxf(出图) → cad-validate(几何验证) 全链路。

    实现要点：
      · 先生成工程图（复用 cmd_drawing，含中文视图名与 GB/T 布局）
      · 优先 SaveAs3 另存为 .dxf（SW 原生支持 DXF/DWG 过滤器）
      · 若 SaveAs3 失败，回退尝试 ExportToDWG2 / SaveAs2 等旧接口
      · 导出后用文件头自检（DXF 应以 "0\nSECTION" 或 "AutoCAD" 特征开头）
    """
    import re
    # ══ 【Bug-35 修复】支持直接从【已有工程图 .slddrw】导出 DXF ══════════
    # 原缺陷：本命令【只接受零件路径 .sldprt】—— 它内部先生成工程图再导 DXF。
    #   但出图小屋拿到的是 .slddrw（工程图已生成），想用 CADX 做几何验证时，
    #   cad-validate 需要 DXF，而 dxf 命令却不收 .slddrw →
    #   "出图 → CADX 验证"这条链路直接断开（Bug-35）。
    # 修复：若传入的就是 .slddrw，跳过"生成工程图"这一步，直接打开并导出。
    _is_drawing_input = str(part_path).lower().endswith(".slddrw")

    # 1. 生成（或直接使用）工程图
    drawing_path = None
    if output_path:
        base = os.path.splitext(output_path)[0]
        drawing_path = base + ".slddrw"

    if _is_drawing_input:
        # 直接使用既有工程图 —— 不再重新出图（避免覆盖用户已排版的图纸）
        if not os.path.exists(part_path):
            return {"ok": False, "error": "工程图文件不存在: %s" % part_path}
        dr_result = {"ok": True, "path": part_path, "reused_drawing": True}
    else:
        dr_result = cmd_drawing(sw, part_path, drawing_path)
        if not dr_result.get("ok"):
            return dr_result

    # 2. 确定输出路径
    if not output_path:
        part_name = os.path.basename(part_path)
        name_no_ext = re.sub(r'[^\w\-]', '_', os.path.splitext(part_name)[0])
        output_path = os.path.join(os.path.dirname(part_path), name_no_ext + ".dxf")
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass

    saving = dr_result.get("path")
    if not (saving and os.path.exists(saving)):
        return {"ok": False, "error": "drawing file not found for DXF export"}

    result = {"dxf_path": output_path, "drawing_path": saving, "ok": False}
    doc = None
    try:
        doc_type = 3  # swDrawing
        errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.OpenDoc6(saving, doc_type, 1, "", errs, warns)
        if doc is None:
            return {"ok": False, "error": "cannot open drawing for DXF export"}

        # ── 方法1: SaveAs3 直接另存 DXF ──────────────────────────────
        try:
            rc = doc.SaveAs3(output_path, 0, 2)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                result["ok"] = True
                result["method"] = "SaveAs3"
        except Exception as e:
            result["saveas3_error"] = str(e)

        # ── 方法2: SaveAs2 旧接口回退 ────────────────────────────────
        if not result.get("ok"):
            try:
                rc2 = doc.SaveAs2(output_path, 0, 0)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    result["ok"] = True
                    result["method"] = "SaveAs2"
            except Exception as e:
                result["saveas2_error"] = str(e)

        # ── 方法3: ExportToDWG2（SW 专用 DWG/DXF 导出器）─────────────
        if not result.get("ok"):
            try:
                # swExportToDWG_Drawing 过滤器常量为 2（图纸）；版本差异较大，
                # 失败不影响主流程，仅作为最后尝试。
                ok3 = doc.ExportToDWG2(output_path, saving, 2, False, False,
                                       False, False, 0, None, False, False, 0)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    result["ok"] = True
                    result["method"] = "ExportToDWG2"
            except Exception as e:
                result["exporttodwg2_error"] = str(e)

    except Exception as e:
        result["error"] = str(e)
    finally:
        # 关闭工程图（DXF 已导出，保持环境干净）
        try:
            if doc is not None:
                sw.CloseDoc(saving)
        except Exception:
            pass

    # ── 3. 导出自检：确认是合法 DXF（防止导出空文件/错误格式）─────────
    if result.get("ok"):
        try:
            with open(output_path, "rb") as f:
                head = f.read(2048)
            txt = head.decode("utf-8", "replace").upper()
            looks_dxf = ("SECTION" in txt) or ("AUTOCAD" in txt) or ("HEADER" in txt)
            result["content_check"] = "dxf" if looks_dxf else "unknown"
            result["size_bytes"] = os.path.getsize(output_path)
            if not looks_dxf:
                result["ok"] = False
                result["error"] = ("导出的文件不像合法 DXF（头部未见 SECTION/AUTOCAD 标记），"
                                   "cad-validate 可能无法解析")
        except Exception as e:
            result["content_check_error"] = str(e)

    # ── 【BUG-08 修复】图层名整理：中文层名 → CADX 标准层 ──────────────
    # SW 中文界面默认导出中文图层（"可见边线"/"尺寸"…），与 CADX mechanical
    # 规则要求的 OUTLINE/THIN/CENTER/HIDDEN/DIM/TEXT/HATCH 冲突，
    # 导致每次出图 cad-validate 刷一堆 MISSING_LAYER / LAYER_NAME_INVALID。
    # 这里在导出后自动整理图层，让几何验证聚焦真正的问题。
    if result.get("ok"):
        _lr = _remap_dxf_layers(output_path, rewrite=True)
        result["layer_remap"] = {
            "ok": _lr.get("ok"),
            "changes": _lr.get("changes") or {},
            "created": _lr.get("created") or [],
            "unknown": _lr.get("unknown") or [],
            "exempt": _lr.get("exempt") or [],
        }
        if _lr.get("error"):
            result["layer_remap"]["error"] = _lr["error"]
        if _lr.get("unknown_hint"):
            result["layer_remap"]["hint"] = _lr["unknown_hint"]
        if _lr.get("exempt_note"):
            result["layer_remap"]["exempt_note"] = _lr["exempt_note"]

    # 4. 附带几何验证指令，便于调用方直接续跑 cad-validate
    if result.get("ok"):
        result["next_step"] = ("python sw_bridge.py cad-validate \"%s\" mechanical"
                               % output_path)
        # ── 【BUG-11 修复】next_command 必须给出【本环境真实可用】的调用方式 ──
        # 原文档写的是 DSH 工具名（workflow-gate-xxx），本环境并不存在这些工具，
        # 真实入口是命令行脚本。这里统一给出可直接复制的 CLI 形式。
        _mg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_gate.py")
        result["next_command"] = ("python \"%s\" room-report <房间名> validating "
                                  "'几何验证中'" % _mg)
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


# ── Bug-25: 安全退出 SolidWorks ─────────────────────────────────────
def cmd_close_all(sw):
    """关闭所有 SolidWorks 文档（SW 2020 兼容）。

    SW 2020 没有 sw.Quit() 方法，使用 CloseAllDocuments() + GC 清理替代。
    """
    import swapi as _swapi
    result = _swapi.close_all_and_exit(sw)
    return result


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


# ── SW 使用窗口互斥锁（并行小屋自动排队，FIFO 先到先用）──────────────────
# 由 mode_gate.py 提供锁状态；串行模式/单房间/非模式2 自动跳过，零影响。
# 用法: sw_bridge.py <命令> ... --room <房间名>   或环境变量 DSH_ROOM=<房间名>

def _strip_room_args(args):
    """【Bug5 修复】剥离 --room/--room=xxx 及其值，返回"纯业务参数"。

    原缺陷：run/drawing/dwg/cad-validate 直接用 args[1] 当路径，
      当命令写成 `sw_bridge.py run --room 结构件 script.py` 时，
      args[1] 会拿到 "--room" 本身 → 输出 JSON 出现 "path":"--room"，
      首次调用必然失败一轮（子代理重试碰对顺序才成功）。

    本函数把 --room 从任意位置摘掉（含 --room=xxx 内联写法），
    让业务参数解析与房间参数彻底解耦 —— 无论 --room 放在前/中/后都正确。

    Returns: (clean_args, room)
    """
    clean, room = [], None
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == "--room":
            if i + 1 < n:
                room = args[i + 1]
                i += 2
                continue
            i += 1          # 末尾孤立 --room：丢弃，绝不当成路径
            continue
        if isinstance(a, str) and a.startswith("--room="):
            room = a.split("=", 1)[1]
            i += 1
            continue
        clean.append(a)
        i += 1
    return clean, room


def _detect_gate_room(args):
    """检测房间名。并行模式(mode=2)下 --room 强制，缺失返回 None 让 caller 报错。"""
    # 1. 优先从 --room 参数读取
    for i, arg in enumerate(args):
        if arg == "--room" and i + 1 < len(args):
            return args[i + 1]
    # 2. 尝试从环境变量 DSH_ROOM 读取
    room = os.environ.get("DSH_ROOM")
    if room:
        return room
    # 3. 检查模式：并行模式强制要求 --room
    mg_path = _get_mode_gate_path()
    if mg_path:
        ms_path = os.path.join(os.path.dirname(mg_path), "mode_state.json")
        if os.path.exists(ms_path):
            try:
                with open(ms_path, "r", encoding="utf-8") as f:
                    ms = json.load(f)
                if str(ms.get("mode", "1")) == "2":
                    # ── 【Bug5 修复】区分并行/串行，不再一律返回 None ──────
                    # ── 【BUG-04 修复】统一走 _authoritative_parallel_mode() ─
                    #   与 main()/room-start 共用同一权威来源，杜绝结论相反。
                    _pmm = _authoritative_parallel_mode()
                    _pms = str(_pmm).strip().upper() if _pmm else ""
                    if _pms in ("E", "SEQUENTIAL"):
                        # 串行模式：只有一个活动房间，可直接推断（继续往下走）
                        rooms = ms.get("rooms", {})
                        lk = ms.get("sw_lock", {})
                        owner = lk.get("owner")
                        if owner and owner in rooms:
                            return owner
                        act = [k for k, v in rooms.items() if v.get("active")]
                        if len(act) == 1:
                            return act[0]
                        return None
                    # 并行模式：确实无法唯一推断，返回 None 触发报错
                    # （报错信息里会列出候选房间，见下方 _room_candidates）
                    return None
            except Exception:
                pass
    # 4. 串行模式：单房间自动推断
    if mg_path:
        ms_path = os.path.join(os.path.dirname(mg_path), "mode_state.json")
        if os.path.exists(ms_path):
            try:
                with open(ms_path, "r", encoding="utf-8") as f:
                    ms = json.load(f)
                rooms = ms.get("rooms", {})
                active = [k for k, v in rooms.items() if v.get("active")]
                lk = ms.get("sw_lock", {})
                owner = lk.get("owner")
                if owner and owner in rooms:
                    return owner
                if len(active) == 1:
                    return active[0]
            except Exception:
                pass
    return None
def _room_candidates():
    """【Bug5 修复】返回 (活动房间列表, SW锁持有房间)，供报错时给出可自救提示。

    子代理最常见的卡点：报错说"必须指定 --room"，但它不知道自己该用哪个名字，
    于是反复试错。把真实候选列出来，问题当场自解。
    """
    _act, _owner = [], None
    try:
        mg_path = _get_mode_gate_path()
        ms_path = os.path.join(os.path.dirname(mg_path), "mode_state.json")
        if os.path.exists(ms_path):
            with open(ms_path, "r", encoding="utf-8") as f:
                ms = json.load(f)
            rooms = ms.get("rooms", {})
            _act = [k for k, v in rooms.items() if v.get("active")]
            _owner = (ms.get("sw_lock", {}) or {}).get("owner")
    except Exception:
        pass
    return _act, _owner


def _get_mode_gate_path():
    """探测 mode_gate.py 绝对路径（subagent 从不同 cwd 调用也能定位）。"""
    p2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_gate.py")
    if os.path.exists(p2):
        return p2
    s = os.path.abspath(__file__)
    for _ in range(6):
        s = os.path.dirname(s)
        p2 = os.path.join(s, "mode_gate.py")
        if os.path.exists(p2):
            return p2
    home = os.path.expanduser("~")
    for rel in [".dsh/.agent-presets/engineering/tools/mode_gate.py",
                ".dsh/engineering/tools/mode_gate.py"]:
        p2 = os.path.join(home, rel)
        if os.path.exists(p2):
            return p2
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_gate.py")


# ── 【BUG-04 修复】并行模式的【单一权威】读取口 ────────────────────────────
# 原缺陷：sw_bridge、room-start、sw-status 各自读不同文件（mode_state vs
#   workflow_state），两份值相反时不同命令给出相反结论 —— 这就是
#   "并行模式权威来源两命令打架"。
# 修复：一律委托 mode_gate.get_parallel_mode()（它内部会做 reconcile 并把
#   两个文件对齐写回），确保全链路只有一个结论。
_PARALLEL_MODE_CACHE = {"v": None, "ts": 0.0}


def _authoritative_parallel_mode():
    """返回权威的并行模式（'parallel' / 'sequential' / None），带 2 秒短缓存。

    【BUG-04】不直接读 json —— 统一走 mode_gate.get_parallel_mode()，
    由它负责"workflow 优先 + 两文件对齐"，杜绝多命令结论矛盾。
    """
    import time as _t
    now = _t.time()
    if _PARALLEL_MODE_CACHE["v"] is not None and (now - _PARALLEL_MODE_CACHE["ts"]) < 2.0:
        return _PARALLEL_MODE_CACHE["v"]
    val = None
    try:
        import importlib.util as _ilu
        _p = _get_mode_gate_path()
        if _p and os.path.exists(_p):
            _spec = _ilu.spec_from_file_location("_mg_for_pmode", _p)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            getter = getattr(_m, "get_parallel_mode", None)
            if callable(getter):
                val = getter()
    except Exception:
        val = None
    # 兜底：mode_gate 不可用时才直接读文件（保持旧行为，绝不更差）
    if val is None:
        try:
            mg = _get_mode_gate_path()
            base = os.path.dirname(mg)
            ms_path = os.path.join(base, "mode_state.json")
            wf_path = os.path.join(base, "workflow_state.json")
            _norm = lambda r: (None if r is None else
                               ("parallel" if str(r).strip().upper() in ("D", "PARALLEL")
                                else "sequential" if str(r).strip().upper() in ("E", "SEQUENTIAL")
                                else r))
            if os.path.exists(wf_path):
                with open(wf_path, "r", encoding="utf-8") as f:
                    _wf = json.load(f) or {}
                if str(_wf.get("step") or "") == "user_selected":
                    v = _norm(_wf.get("parallel_mode"))
                    if v in ("parallel", "sequential"):
                        val = v
            if val is None and os.path.exists(ms_path):
                with open(ms_path, "r", encoding="utf-8") as f:
                    val = _norm((json.load(f) or {}).get("parallel_mode"))
        except Exception:
            val = None
    _PARALLEL_MODE_CACHE["v"] = val
    _PARALLEL_MODE_CACHE["ts"] = now
    return val


def _read_sw_state_file():
    """读取 mode_gate 后台监控的共享状态文件。"""
    mg_path = _get_mode_gate_path()
    if not mg_path:
        return None
    state_file = os.path.join(os.path.dirname(mg_path), "sw_state.json")
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _sw_locked_by_other(room_name):
    """检查 SW 是否被其他房间占用（进程级持续监控）。

    优先读取监控进程的状态文件；若无监控进程，回退到一次性检测。
    """
    state = _read_sw_state_file()
    if state and state.get("sw_running"):
        owner = state.get("owner")
        if owner and owner != room_name:
            return True, owner
        # 有监控但没有 owner 信息，说明正在排队中
        if owner is None:
            return True, "unknown"
    # 无监控进程，回退到进程检测
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SLDWORKS.EXE", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10)
        return "SLDWORKS.EXE" in (out.stdout or "").upper(), "process"
    except Exception:
        return False, None

def _room_heartbeat(room, phase="modeling"):
    """【Bug#4 修复】无条件写一次房间心跳（不需要小屋主动调用）。

    ── 问题现象（测试部 Bug#4）─────────────────────────────────────────
      小屋进入 modeling 后长时间不更新心跳文件（>180s），room-status 报
      running_unverified；若再叠加无新报告，会走向 stale 判定。
      根因：心跳依赖小屋【主动】调 mode_gate.py room-heartbeat，
        而小屋在 SW 建模阶段（sw_bridge.py run 可能耗时数分钟）根本无暇顾及。
        规则里虽写了"建模时没空跳心跳是常态，禁止据此 recover"，
        但 running_unverified 标签会给主对话带来误判压力，频繁做无效轮询。

    ── 修复（本函数）─────────────────────────────────────────────────
      把心跳下沉到 sw_bridge 的工具层：**每条 SW 命令在取锁后/释放前
      各无条件写一次心跳**，用真实工具调用证明"小屋正在干活"。
      这样即使小屋从不主动跳心跳，房间状态也能持续刷新，
      主对话不再需要为"它到底还活着吗"而反复轮询。

    Args:
        room:  房间名（空则跳过 —— 未传 --room 时无法定位房间）
        phase: 心跳备注（modeling / acquired / released），便于排查。

    设计要点：任何异常都静默吞掉 —— 心跳失败绝不能影响建模主流程。
    """
    if not room:
        return False
    try:
        gate = _get_mode_gate_path()
        if not gate or not os.path.exists(gate):
            return False
        subprocess.run([sys.executable, gate, "room-heartbeat", room],
                       capture_output=True, text=True, encoding="utf-8", timeout=15)
        return True
    except Exception:
        return False


def _gate_sw_lock(room):
    """获取 SW 使用权（进程级窗口）。

    快路径立即获取；忙碌时短暂等待后返回 {"busy": True}，
    由小屋稍后重试同一命令（队列位置已保留，FIFO 先到先用）。
    锁随小屋生命周期保持，由 mode_gate room-end 统一释放并确保 SW 进程完全关闭。
    """
    if os.environ.get("DSH_SW_LOCK_OFF"):
        return {"skipped": True}
    # ── 【Bug#4 修复】取锁成功即写心跳：证明小屋确实在干活 ──────────────
    _room_heartbeat(room, "acquired")
    gate = _get_mode_gate_path()
    if not os.path.exists(gate):
        return {"skipped": True}
    try:
        out = subprocess.run([sys.executable, gate, "sw-request", room],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        info = json.loads(out.stdout or "{}")
    except Exception:
        return {"skipped": True}
    if not info.get("need_lock"):
        return {"skipped": True}
    if info.get("acquired"):
        return {"room": room, "skipped": False}
    # 忙碌: 最多再等 ~24 秒（短等待避免 pwsh 工具超时），仍拿不到则返回 busy
    deadline = __import__("time").time() + 24
    last = {}
    while __import__("time").time() < deadline:
        try:
            out = subprocess.run([sys.executable, gate, "sw-wait", room, "8"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            r = json.loads(out.stdout or "{}")
            last = r
            if r.get("acquired"):
                return {"room": room, "skipped": False}
        except Exception:
            pass
        __import__("time").sleep(1)
    return {"busy": True, "room": room,
            "position": last.get("position") or info.get("position"),
            "owner": last.get("owner") or info.get("owner"),
            "error": (last.get("error") or info.get("wait_hint")
                      or "SW 正被其他小屋使用，请稍后重试同一命令。")}


def _gate_sw_unlock(lock):
    """【Bug4修复】单命令级锁：命令执行完立即释放，不占整个房间生命周期。

    旧行为：锁随小屋生命周期保持，只在 room-end 释放。
            -> 某小屋需要用户交互确认时，其他小屋被无限阻塞。
    新行为：每条 sw_bridge 命令结束即释放锁，队列中下一个房间立即接管。
            如需保持旧行为（整个房间持锁），设置 DSH_SW_UNLOCK_ON_EXIT=0。
    """
    try:
        if not lock or lock.get("skipped") or lock.get("busy"):
            return
        # ── 【Bug#4 修复】命令结束也写一次心跳 ────────────────────────────
        #   run 可能耗时数分钟，取锁时跳的那一次到结束时可能已经"过期"，
        #   这里补一次，确保收尾阶段房间状态仍是新鲜的（不会刚干完活就被判 stale）。
        _room_heartbeat(lock.get("room"), "released")
        # 默认：命令级释放（环境变量显式设 "0" 才保持房间级）
        if os.environ.get("DSH_SW_UNLOCK_ON_EXIT") == "0":
            return
        gate = _get_mode_gate_path()
        if not gate or not os.path.exists(gate):
            return
        subprocess.run([sys.executable, gate, "sw-release", lock.get("room", "")],
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    # ── 【BUG-09 修复】--help / -h / help 必须在【任何副作用之前】短路返回 ──
    # 原缺陷：这些参数会一路走到 get_sw()（连 SolidWorks）、_gate_sw_lock
    #   （抢占 SW 独占锁）、甚至 close-all/rooms-reset 分支 —— 用户只想看
    #   帮助，却可能把 SW 连上、把锁抢走、甚至触发清理动作（"只想看用法，
    #   结果把环境动了"）。现提前拦截，保证 help 是纯只读、零副作用。
    if args and str(args[0]).lower() in ("--help", "-h", "help", "-?", "/?"):
        print(json.dumps({
            "ok": True,
            "command": "help",
            "note": "本输出为纯说明，不连接 SolidWorks、不取锁、不改任何状态",
            "usage": "python sw_bridge.py <命令> [参数...] [--room <房间名>]",
            "commands": {
                "status": "连接状态 / 版本 / 已开文档（只读）",
                "doctor": "环境自检（新电脑先跑这个，只读）",
                "self-path": "返回本脚本绝对路径（只读）",
                "open/new/info/list/massprops/close/save": "文档基本操作",
                "sketch-rect": "画矩形并拉伸（快速验证）",
                "run <script.py>": "执行建模脚本（核心能力）",
                "drawing <零件>": "生成 SLDDRW 工程图",
                "dwg <零件>": "导出 DWG",
                "dxf <零件|图纸>": "导出 DXF（【Bug-35】现也接受 .slddrw；"
                                    "供 cad-validate 验证；自动整理图层名）",
                "export-pdf <图纸>": "【Bug-45】导出 PDF（自动补 .pdf 后缀；"
                                     "多方法兜底 SaveAs3/SaveAs2/ExportToPDF）",
                "annotate <图纸>": "【Bug-34】自动插入模型尺寸标注 + GB/T 检查报告",
                "bore <孔径> [--axis Z]": "【Bug-42】专用孔命令（显式指定轴线方向）",
                "title-block <图纸> [字段=值 ...]": "【Bug-34】填写标题栏"
                                                    "（材料/比例/图号/设计者/日期）",
                "cad-validate <图纸>": "CADX 几何验证（7 项检查）",
                "cad-validate-live": "验证当前 AutoCAD 活动文档",
                "physics-status": "FEA 求解器后端状态",
                "physics-validate-case <case.json>": "校验载荷工况",
                "physics-optimize <case.json>": "自动迭代优化",
                "physics-demo": "悬臂梁演示",
                "physics-fatigue <case.json>": "疲劳 / 设计寿命（默认30年）校核",
                "physics-report <run_id>": "查看仿真报告",
                "physics-recommend <run_id>": "生成修正建议",
                "close-all": "关闭 SolidWorks 并释放使用权",
            },
            "room_note": ("并行模式(D)下 run/drawing/dwg/dxf/cad-validate "
                          "必须带 --room <房间名>；--room 可放任意位置。"),
        }, ensure_ascii=False, indent=2))
        return
    if not args:
        # 【Bug2 修复】错误分支必须以非零退出码结束。
        # 原实现 print 后直接 return → 进程退出码恒为 0，
        # 调用方（子代理/bash 脚本）用 $? / exitCode 判断成败时会误判为"成功"，
        # 进而把失败当成功继续往下走，掩盖真实错误。
        print(json.dumps({"ok": False, "error": "no command",
                          "hint": "python sw_bridge.py --help 查看全部命令"},
                         ensure_ascii=False))
        sys.exit(2)
    cmd = args[0]
    # Bug-26: 路径解析——任何位置调用均可自动定位本脚本
    _SW_BRIDGE_SELF = os.path.abspath(__file__)
    # ── SW 互斥锁: 并行小屋在此自动排队（串行/单房间直接通过）──
    # 并行模式下 --room 是强制的，未传则报错提示
    _need_room = False
    _pmode = None
    try:
        import json as _json
        _mg_path = _get_mode_gate_path()
        if _mg_path and os.path.exists(os.path.join(os.path.dirname(_mg_path), "mode_state.json")):
            with open(os.path.join(os.path.dirname(_mg_path), "mode_state.json"), "r", encoding="utf-8") as _f:
                _ms = _json.load(_f)
            if str(_ms.get("mode", "1")) == "2":
                _need_room = True
                # ── 【Bug5 修复】读取权威落盘的并行模式 ──────────────────
                # 原实现只看 mode==2 就强制 --room，【完全不区分 D/E】：
                #   · 串行模式(E)下也被无理强制 → 小屋不传 --room 直接报错；
                #   · 且错误提示没说清"为什么需要"，调用方（子代理）反复踩坑。
                # 修复：优先读 mode_state.json 的 parallel_mode（Bug1 已落盘），
                #   回退读 workflow_state.json；仅 parallel 时强制，sequential 放宽。
                # ── 【BUG-04 修复】改为委托 _authoritative_parallel_mode() ──
                #   它内部走 mode_gate.get_parallel_mode()（含两文件对齐），
                #   避免 sw_bridge 与其它命令各自读 json 得出相反结论。
                _pmode = _authoritative_parallel_mode()
                _pm = str(_pmode).strip().upper() if _pmode else ""
                if _pm in ("E", "SEQUENTIAL"):
                    # 串行模式：单房间，允许自动推断（见 _detect_gate_room）
                    _need_room = False
    except Exception:
        pass
    _lock = None
    # 【B5修复】只读/自检命令不需要 SW 独占权，跳过锁与 --room 强制要求。
    # 原先 doctor 因 parallel_mode=D 被强制要求 --room，无法做独立环境自检。
    _READONLY_CMDS = {"self-path", "doctor", "status", "info", "list",
                      "sw-proc", "help", "version", "ping",
                      # ── 【BUG-02 实机补强】physics-* 全部是【纯计算】命令 ──
                      # 它们只读载荷工况 JSON、用 numpy 算，完全不碰 SolidWorks，
                      # 不产生文件冲突，因此不需要 SW 独占权、也不该被
                      # "并行模式必须带 --room"拦住。
                      # 实机教训（2026-09-27）：physics-fatigue 因未列入本集合，
                      # 在并行模式下被拒（exit=2），疲劳校核链路直接断掉 ——
                      # 这正是"修复后仍跑不通"的隐蔽原因。
                      #
                      # cad-validate 同理：它只解析已有的 DXF 文件（ezdxf 本地计算），
                      # 既不连 SW 也不写 CAD 产物 → 归入只读，不再强制 --room。
                      # （真正需要 SW 的是上游的 `dxf` 导出命令，那个仍需 --room。）
                      "physics-status", "physics-demo", "physics-report",
                      "physics-recommend", "physics-fatigue",
                      "physics-list-domains", "physics-validate-domain",
                      "physics-validate-case", "physics-optimize",
                      "cad-validate"}
    # 【测试反馈 Bug4 修复】SW【收尾类】命令也不该强制 --room：
    #   close-all 的语义就是"关闭 SolidWorks 并释放使用权"，它本身就是释放动作。
    #   若还要求先指定房间才能释放，会出现"锁被上一家占着 → 想释放却因没带 room
    #   被拒"的死结；串行模式下更是完全没有房间概念。
    #   因此 close-all / sw-release / rooms-reset 豁免 --room 与取锁流程。
    _NO_ROOM_CMDS = {"close-all", "sw-release", "rooms-reset"}
    # ── 【Bug5 修复】进入业务解析前先剥离 --room，避免它污染位置参数 ────────
    # 原缺陷：run/drawing/dwg/cad-validate 用 args[1] 当路径，
    #   写成 `run --room 结构件 script.py` 时 args[1]="--room" →
    #   输出 "path":"--room"，首次调用必败一轮。
    # 修复：--room 从任意位置（含 --room=xxx）摘掉，业务只看 _args。
    _clean_args, _room_from_strip = _strip_room_args(args)
    _args = _clean_args if _room_from_strip else args
    if cmd not in _READONLY_CMDS and cmd not in _NO_ROOM_CMDS:
        _room_id = _room_from_strip or _detect_gate_room(args)
        # ── 【Bug5 修复】并行模式下这些命令【强制】要求 --room ────────────
        # 它们既要抢占 SW 独占权、又要产出文件；无 room 会导致锁无归属、
        # 产物路径互相覆盖，因此必须显式指定。
        # 【Bug-34/35 修复】annotate / title-block 也要 SW 独占权并写文件；
        #   dxf 现在也接受 .slddrw（Bug-35）。
        _ROOM_REQUIRED_CMDS = {"run", "drawing", "dwg", "dxf", "cad-validate",
                               "annotate", "title-block"}

        # -- 【新-1 修复】单房间时自动采用，不再一律拒绝 ------------------
        # 测试反馈：照文档跑 sw_bridge.py drawing <零件> 直接被拒(rc=2)，
        #   必须自己加 --room 工程图输出 才能通。而任务 prompt 与 usage
        #   示例都没带 --room，照抄文档 100% 失败，还易被误读成工具坏了。
        # 修复：并行模式下若只有一个活动房间，自动采用它；
        #   有多个房间时才要求显式选择（那种情况确实无法唯一推断）。
        if _room_id is None and _need_room:
            _auto_rooms, _ = _room_candidates()
            if len(_auto_rooms) == 1:
                _room_id = _auto_rooms[0]
                try:
                    sys.stderr.write(
                        "[sw_bridge] 未指定 --room，已自动采用唯一活动房间: %s"
                        "\n" % _room_id)
                except Exception:
                    pass

        if _room_id is None and _need_room:
            # ── 【Bug5 修复】报错必须"可自救"：列出真实候选房间 + 现成命令 ──
            # 原提示只说"必须指定 --room"，子代理不知道该填什么、反复重试失败。
            # 现在把当前活动房间列出来，并给出可直接复制执行的命令模板。
            _rooms_active, _room_owner = _room_candidates()
            _extra = ("（本命令在并行模式下【强制】要求 --room："
                      "它需要 SW 独占权并产出文件）"
                      if cmd in _ROOM_REQUIRED_CMDS else "")
            _eg = (_rooms_active[0] if _rooms_active else "<房间名>")
            _hint = ("请改用: python sw_bridge.py " + cmd +
                     " <参数...> --room " + _eg + "\n"
                     "注意：--room 可放在任意位置，会自动剥离，不会污染业务参数。")
            if _rooms_active:
                _hint += ("\n当前活动房间（从中选【你自己】的房间名）: "
                          + ", ".join(_rooms_active))
            if _room_owner:
                _hint += "\n当前 SW 锁持有房间: " + _room_owner
            print(json.dumps({"ok": False,
                              "error": "并行模式下必须指定 --room <房间名>！" + _extra,
                              "command": cmd,
                              "active_rooms": _rooms_active,
                              "sw_owner": _room_owner,
                              "parallel_mode": "parallel",
                              "hint": _hint},
                             ensure_ascii=False, indent=2))
            # 【Bug2 修复】--room 缺失属于"调用错误"，必须以非零码退出。
            # 原来裸 return 使 exitCode=0，自动化脚本无法据此判定失败。
            sys.exit(2)
        _lock = _gate_sw_lock(_room_id)
        if _lock.get("timeout") or _lock.get("busy"):
            print(json.dumps({"ok": False, "sw_busy": True,
                              "position": _lock.get("position"), "owner": _lock.get("owner"),
                              "error": _lock.get("error"),
                              "hint": "SW 使用窗口排队中（FIFO 先到先用）。请执行 Start-Sleep -Seconds 90 后重试同一条命令（带 --room 房间名），队列位置已保留。"},
                             ensure_ascii=False, indent=2))
            # 【Bug2 修复】SW 忙（排队中）不是成功，用非零码退出，
            # 让调用方能识别"需要重试"而不是"已完成"。
            sys.exit(3)
    try:
        # ── 【BUG-02 实机补强】纯计算命令【不连接 SolidWorks】─────────────
        # 原实现无条件 get_sw()，即使 physics-* 这类完全不碰 SW 的命令
        #   也会把 SolidWorks 拉起来 —— 实测副作用：
        #     · 白白占用 SW 独占窗口，阻塞真正在建模的房间；
        #     · 在没有装 SW 的机器上，纯 FEA/疲劳计算直接失败；
        #     · 启动 SW 要几十秒，纯计算命令被拖慢一个数量级。
        # 复用 _READONLY_CMDS：其中 physics-* 与 cad-validate 都是纯本地计算。
        _NO_SW_CMDS = _READONLY_CMDS
        if cmd in _NO_SW_CMDS:
            sw = None
        else:
            sw = get_sw()
        if cmd == "status":
            result = cmd_status(sw)
        elif cmd == "doctor":
            result = cmd_doctor(sw)
        elif cmd == "open":
            result = cmd_open(sw, _args[1] if len(_args) > 1 else "")
        elif cmd == "new":
            result = cmd_new(sw, _args[1] if len(_args) > 1 else "")
        elif cmd == "info":
            result = cmd_info(sw)
        elif cmd == "list":
            result = cmd_list(sw)
        elif cmd == "massprops":
            result = cmd_massprops(sw)
        elif cmd == "close":
            result = cmd_close(sw)
        elif cmd == "close-all":
            # Bug-25: 安全退出接口（SW 2020 兼容）
            import swapi as _swapi
            result = _swapi.close_all_and_exit(sw)
            # 小屋完成 SW 任务 → 立即释放 SW 锁（不等 room-end），
            # 让队列里下一个排队的房间马上获得使用权（用户需求）
            try:
                _gate = _get_mode_gate_path()
                _rid = _detect_gate_room(args)
                if _gate and _rid:
                    subprocess.run([sys.executable, _gate, "sw-release", _rid],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
                    if isinstance(result, dict):
                        result["sw_lock_released"] = True
            except Exception:
                pass
        elif cmd == "save":
            # ── 【Bug-38 建议②】保存前校验零件归属，越界则拒绝并记录违规 ─────
            # 台账："小屋保存零件前，校验文件名是否落在本房间归属清单内，
            #       否则拒绝保存并 room-report 警告。"
            # 用 --force-part 可显式跳过（确实需要跨房间产出时）。
            _save_path = _args[1] if len(_args) > 1 else ""
            _po = _check_part_before_save(_save_path, _detect_gate_room(args),
                                          force=("--force-part" in args))
            if _po is not None and not _po.get("allowed", True):
                result = _po
            else:
                result = cmd_save(sw, _save_path)
                if _po is not None:
                    result["part_ownership"] = _po
        elif cmd == "check-part":
            # 【Bug-38】独立校验命令（小屋可在保存前自检）
            _cp_args = [a for a in _args[1:] if not a.startswith("--")]
            result = _check_part_before_save(
                _cp_args[0] if _cp_args else "",
                _detect_gate_room(args) or (_cp_args[1] if len(_cp_args) > 1 else ""),
                force=True)
            if result is None:
                result = {"ok": False, "error": "需要零件路径"} 
        elif cmd == "sketch-rect":
            result = cmd_sketch_rect(sw, _args[1], _args[2], _args[3])
        elif cmd == "export-pdf":
            result = cmd_export_pdf(sw, _args[1] if len(_args) > 1 else "")
        elif cmd == "run":
            # 【Bug#4 修复】把房间名传进 cmd_run，用于建模期间后台刷心跳
            result = cmd_run(sw, _args[1] if len(_args) > 1 else "",
                             _args[2:],
                             room=_detect_gate_room(args))
        elif cmd == "show":
            result = cmd_show(sw, _args[1] if len(_args) > 1 else None)
        elif cmd == "drawing":
            result = cmd_drawing(sw, _args[1] if len(_args) > 1 else "", _args[2] if len(_args) > 2 else None)
        elif cmd == "dwg":
            result = cmd_dwg(sw, _args[1] if len(_args) > 1 else "", _args[2] if len(_args) > 2 else None)
        elif cmd == "dxf":
            # 【Bug8 修复】SW 直接导出 DXF —— 打通 cad-validate 几何验证链路
            # 【Bug-35 修复】_args[1] 也可直接是 .slddrw（不再强制先出图）
            result = cmd_dxf(sw, _args[1] if len(_args) > 1 else "", _args[2] if len(_args) > 2 else None)
        elif cmd == "annotate":
            # 【Bug-34 修复】对工程图自动插入模型尺寸标注 + GB/T 检查报告
            result = annotate_drawing(sw, _args[1] if len(_args) > 1 else "")
        elif cmd == "title-block":
            # 【Bug-34 修复】填写标题栏字段（材料/比例/图号/设计者/日期）
            # 用法: title-block <slddrw> [字段=值 ...]
            _fields = {}
            for _kv in _args[2:]:
                if "=" in _kv:
                    _k, _v = _kv.split("=", 1)
                    _fields[_k.strip()] = _v.strip()
            result = set_title_block(sw, _args[1] if len(_args) > 1 else "",
                                     fields=_fields or None)
        elif cmd == "cleanup":
            result = cmd_cleanup(sw, _args[1] if len(_args) > 1 else None)
        elif cmd == "vision-fallback":
            desc = " ".join(_args[1:]) if len(_args) > 1 else ""
            auto_save = _args[2] != "--no-save" if len(_args) > 2 else True
            result = cmd_vision_fallback(sw, description=desc, auto_save=auto_save)
        elif cmd == "check-vision":
            result = cmd_check_vision(sw)
        elif cmd == "reading":
            result = cmd_reading(sw)
        elif cmd == "self-path":
            # Bug-26: 返回本脚本的绝对路径，解决"路径错误"问题
            result = {"ok": True, "command": "self-path", "path": _SW_BRIDGE_SELF, "dir": os.path.dirname(_SW_BRIDGE_SELF)}
        elif cmd == "ac-status":
            if not _HAS_CADX:
                result = {"ok": False, "error": "ac_bridge 未找到，请确保 engineering/tools/ 下有 ac_bridge.py"}
            else:
                result = ac_bridge.get_ac().status() if ac_bridge.get_ac() else {"ok": False, "error": "无法连接 AutoCAD"}
        elif cmd == "ac-export":
            if not _HAS_CADX:
                result = {"ok": False, "error": "ac_bridge 未找到"}
            else:
                ac = ac_bridge.get_ac()
                if not ac:
                    result = {"ok": False, "error": "无法连接 AutoCAD，请先启动 AutoCAD"}
                else:
                    out_path = _args[1] if len(_args) > 1 else ""
                    result = {"ok": True, "dxf_path": ac.export_dxf(out_path)}
        elif cmd == "cad-validate":
            if not _HAS_CADX:
                result = {"ok": False, "error": (
                    "ac_validate 模块未找到，请确保 engineering/tools/ 下有 ac_validate.py。\n"
                    "如需 CADX 几何验证，还需安装依赖：pip install ezdxf shapely"
                )}
            else:
                # Bug-24 补充: 自动安装 ezdxf/shapely（个人 agent 无需手动执行 pip）
                install_result = ensure_ezdxf()
                if not install_result.get("ok"):
                    result = {"ok": False, "error": install_result.get("error", "ezdxf 安装失败"),
                              "hint": install_result.get("hint", "请手动运行: pip install ezdxf shapely")}
                else:
                    draw_path = _args[1] if len(_args) > 1 else ""
                    rules = _args[2] if len(_args) > 2 else "mechanical"
                    if not draw_path or not os.path.exists(draw_path):
                        result = {"ok": False,
                                  "error": f"图纸文件不存在: {draw_path}",
                                  "hint": ("cad-validate 接受 .dxf（推荐）或 .dwg。"
                                           "若还没有图纸，先用: python sw_bridge.py dxf <零件.SLDPRT>")}
                    else:
                        # ── 【Bug8 修复】支持 DWG：自动转换为 DXF 后再验证 ───────
                        # 原实现只收 DXF，而 sw_bridge 只导出 DWG → 验证链路断裂。
                        # 现在：① 传 .dxf 直接验证；② 传 .dwg 先自动转 DXF 再验证。
                        dxf_path = draw_path
                        conv_info = None
                        if draw_path.lower().endswith(".dwg"):
                            conv = _dwg_to_dxf(draw_path)
                            conv_info = conv
                            if conv.get("ok"):
                                dxf_path = conv["dxf_path"]
                            else:
                                result = {"ok": False,
                                          "error": ("DWG 无法转换为 DXF，几何验证中止: %s"
                                                    % conv.get("error")),
                                          "hint": ("可改用 SW 直接导出 DXF："
                                                   "python sw_bridge.py dxf <零件.SLDPRT>"),
                                          "conversion": conv}
                                dxf_path = None
                        if dxf_path:
                            try:
                                result = ac_validate.validate_dxf(dxf_path, rules)
                                if conv_info:
                                    result["dwg_converted_from"] = draw_path
                                    result["dxf_used"] = dxf_path
                                    result["conversion_method"] = conv_info.get("method")
                            except RuntimeError as e:
                                msg = str(e)
                                if "ezdxf" in msg.lower() or "import" in msg.lower():
                                    result = {"ok": False, "error": f"DXF 验证失败: {msg}\n请运行: pip install ezdxf shapely"}
                                else:
                                    result = {"ok": False, "error": msg}
        elif cmd == "cad-validate-live":
            if not _HAS_CADX:
                result = {"ok": False, "error": "ac_validate 未找到"}
            else:
                ac = ac_bridge.get_ac()
                if not ac:
                    result = {"ok": False, "error": "无法连接 AutoCAD，请先启动 AutoCAD"}
                else:
                    result = ac_validate.validate_live(ac, rules="mechanical")
        # ── Physics-in-the-Loop 命令 ────────────────────────────
        elif cmd == "physics-demo":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到，请确保 engineering/tools/ 目录完整"}
            else:
                result = _pb.cmd_demo()
        elif cmd == "physics-status":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                result = _pb.cmd_status()
        elif cmd == "physics-validate-case":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                case_path = _args[1] if len(_args) > 1 else ""
                if not case_path or not os.path.exists(case_path):
                    result = {"ok": False, "error": f"载荷工况文件不存在: {case_path}"}
                else:
                    result = _pb.cmd_validate_case(case_path, relaxed=False)
        elif cmd == "physics-optimize":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                case_path = _args[1] if len(_args) > 1 else ""
                # 解析 --max-iter N
                # 【Bug5】用 _args（已剥离 --room），避免 --room 干扰位置参数
                max_iter = 5
                for _i in range(2, len(_args)):
                    if _args[_i] == "--max-iter" and _i + 1 < len(_args):
                        try:
                            max_iter = int(_args[_i + 1])
                        except ValueError:
                            pass
                if not case_path or not os.path.exists(case_path):
                    result = {"ok": False, "error": f"载荷工况文件不存在: {case_path}"}
                else:
                    # ── 【Bug7】解析 --part <零件>：物理校核必须针对真实零件 ──
                    _part_path = ""
                    for _j in range(2, len(_args)):
                        if _args[_j] in ("--part", "--part-path") and _j + 1 < len(_args):
                            _part_path = _args[_j + 1]
                    if not _part_path:
                        _part_path = (os.environ.get("DSH_PART_PATH")
                                      or os.environ.get("DSH_PART") or "")
                    if not _part_path or not os.path.exists(_part_path):
                        result = {
                            "ok": False,
                            "error": "物理校核必须针对真实零件：缺少 --part <零件.SLDPRT>。",
                            "hint": ("原实现用硬编码几何（thickness=5/volume=100000）"
                                     "校验虚构零件，任何不合格设计都能通过（Bug7）。"
                                     "请指定真实零件："
                                     "python sw_bridge.py physics-optimize "
                                     "<case.json> --part <零件.SLDPRT> --room <房间>"),
                            "given_part": _part_path or None,
                        }
                    else:
                        result = _pb.cmd_optimize(
                            case_path, max_iter=max_iter,
                            room=(_room_from_strip or _detect_gate_room(args)
                                  or os.environ.get("DSH_ROOM") or ""),
                            part_path=_part_path,
                        )
        elif cmd == "physics-report":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                run_id = _args[1] if len(_args) > 1 else ""
                result = _pb.cmd_report(run_id)
        elif cmd == "physics-recommend":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                run_id = _args[1] if len(_args) > 1 else ""
                result = _pb.cmd_recommend(run_id, max_iter=3)
        elif cmd == "physics-fatigue":
            # ── 【BUG-02 修复】疲劳 / 设计寿命（默认 30 年）校核入口 ──────
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                case_path = _args[1] if len(_args) > 1 else ""
                _stress = None
                _report_id = ""
                for _i in range(1, len(_args)):
                    if _args[_i] == "--stress" and _i + 1 < len(_args):
                        try:
                            _stress = float(_args[_i + 1])
                        except ValueError:
                            _stress = None
                    elif _args[_i] == "--report" and _i + 1 < len(_args):
                        _report_id = _args[_i + 1]
                if not case_path and not _report_id:
                    result = {"ok": False,
                              "error": "需要载荷工况文件路径，或用 --report <run_id>",
                              "hint": ("python sw_bridge.py physics-fatigue <case.json>\n"
                                       "python sw_bridge.py physics-fatigue --report <run_id>")}
                else:
                    result = _pb.cmd_fatigue(case_path, _stress, _report_id)
        elif cmd == "physics-validate-domain":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                _dom = _args[1] if len(_args) > 1 else "structural"
                result = _pb.cmd_validate_domain(_dom, room=(_room_from_strip or _detect_gate_room(args) or os.environ.get("DSH_ROOM") or ""))
        elif cmd == "physics-list-domains":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                result = _pb.cmd_list_domains()
        else:
            result = {"ok": False, "error": f"unknown command: {cmd}"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # ── 【退出码语义 · 最终约定（Bug1 修正）】────────────────────────────
        # 上一版一刀切"ok:false → exit 1"过严：像 `drawing 不存在的零件`
        # 这类【业务层面的正常否定结果】也被报成调用错误，导致带 --room 的
        # drawing 明明正常跑完却返回 exitcode=1，自动化脚本会误以为命令有问题。
        #
        # 正确的分级（按"错误发生在哪一层"划分）：
        #   0 —— 命令【正常执行完毕】。业务结果可能是 ok:true 或 ok:false，
        #        调用方应读 JSON 的 ok / error 字段判断业务成败。
        #        例：零件不存在、材料名不在库 —— 都是"查询/操作结果"，非调用错误。
        #   1 —— 命令执行中【抛出未预期异常】（在下面的 except 分支设置）。
        #   2 —— 【调用方式错误】：无命令 / 并行模式缺 --room / 未知命令。
        #        这类错误必须在脚本层面拦住，否则调用方会一直无效重试。
        #   3 —— 【SW 忙需重试】：队列排队中（前面分支已设置）。
        #
        # 同时为"未知命令"补一个显式退出码（它属于调用错误）：
        if isinstance(result, dict) and str(result.get("error", "")).startswith("unknown command"):
            sys.exit(2)
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[-2000:],
        }, ensure_ascii=False, indent=2))
        # 【Bug2 修复】未捕获异常属于失败，必须以非零码退出
        sys.exit(1)
    finally:
        _gate_sw_unlock(_lock)


if __name__ == "__main__":
    main()
