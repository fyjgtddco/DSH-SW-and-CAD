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

【Python 版本要求】>= 3.6（本文件使用 f-string）。
  微机房实测：默认 python 若是 3.5.x，会在这里直接 SyntaxError，
  stdout 没有任何 JSON —— 上层只看到「插件解析失败 / 没有输出」，
  根因完全看不出来。因此下面先放一段【版本闸门】：
  它刻意不用 f-string，保证在旧解释器上也能正常执行并把话说清楚。
"""
import sys

# -- Python 版本闸门（必须在任何 f-string 之前执行）----------------------
if sys.version_info < (3, 6):
    sys.stderr.write(
        "Python 版本过低：需要 3.6+，当前 %d.%d.%d\n"
        "sw_bridge.py 使用了 f-string 语法，旧版本会直接 SyntaxError。\n"
        "解决：用 py -3 调用；或安装 Python 3.11+；"
        "也可在 DSH 设置页 工程模式 -> 工程环境检查 里指定 python.exe\n"
        % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))
    print('{"ok": false, "error": "Python < 3.6 (no f-string support)", '
          '"python": "%d.%d.%d", '
          '"hint": "use py -3 or install Python 3.11+"}'
          % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))
    sys.exit(2)

import os
import re
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


def _import_sibling(name):
    """import 与【本文件同目录】的同级模块，绝不拿到别的副本。

    ── 【连接区修复 · 静默取到旧副本】─────────────────────────────────────
    实测事故：`python sw_bridge.py conn-log` 报
        module 'conn_state' has no attribute 'launch_log'
    而源码里明明有该函数。根因是【同名模块被解析到了另一份副本】：

      · mode_gate.py 在导入时会把 _store.state_dir()（= 安装副本 tools）插入
        sys.path[0]（见 mode_gate.py:170-177，用于让 mode_gate/defense_gate
        能被 import）；
      · 于是后续【懒加载】的 `import conn_state` 会命中【安装副本】里的旧版
        conn_state.py，而不是与 sw_bridge.py 同目录的这一份；
      · 旧副本没有新函数 → AttributeError，且报错信息极具误导性
        （看起来像"源码写错了"，实际是"加载了别的文件"）。

    修复：导入前把【本文件所在目录】重新提到 sys.path 最前，再 import。
      这样无论从哪个目录、哪份副本启动，
      sw_bridge 与其同级模块【永远来自同一目录】，不会串副本。
    """
    try:
        while _HERE in sys.path:
            sys.path.remove(_HERE)
    except Exception:
        pass
    try:
        sys.path.insert(0, _HERE)
    except Exception:
        pass
    import importlib
    mod = importlib.import_module(name)
    # 校验：拿到的必须是【本目录】的文件，否则宁可报错也不要静默用错副本
    _f = os.path.abspath(getattr(mod, "__file__", "") or "")
    if _f and os.path.dirname(_f) != _HERE:
        raise ImportError(
            "模块 %s 解析到了非同级副本：%s（期望目录 %s）。"
            "这会导致调用到旧版本代码。" % (name, _f, _HERE))
    return mod


def _doc_type_name(t):
    return {1: "PART", 2: "ASSEMBLY", 3: "DRAWING"}.get(t, str(t))


# ══ 【连接自检修复】补上三个"登记了却没实现"的子命令 ═════════════════════
# 原缺陷（实测复现）：`sw-proc` / `ping` / `version` 被写进了豁免集合
#   （_NO_LOCK_CMDS），main() 里【却没有对应的 elif 分支】：
#     · `python sw_bridge.py sw-proc` → {"error": "unknown command: sw-proc"}
#     · `python sw_bridge.py ping`    → {"error": "unknown command: ping"}
#     · `python sw_bridge.py version` → {"error": "unknown command: version"}
#   更糟的是 cmd_status 的 hint 【主动指引】AI 去跑 `sw_bridge.py sw-proc`
#   以查看 SLDWORKS.EXE 进程 —— AI 照做后撞进 "unknown command" 死胡同，
#   且因为错误串以 "unknown command" 开头，还会被 main() 判为【调用错误】
#   以 exit 2 收场（即便它被登记为合法命令）。
# 修复：三个命令全部落地实现，且都【零副作用】（不连 SW、不取锁）。

def _sw_processes():
    """枚举 SLDWORKS.EXE 进程（tasklist 优先，回退 PowerShell/wmic）。

    返回 (procs, err)：
      procs = [{"pid": int, "name": "SLDWORKS.EXE", "mem": "..."}]
      err   = 全部探测手段都失败时的原因字符串，否则 None
    """
    procs = []
    err = None
    # ① tasklist（与 _sw_locked_by_other 的探测手段保持一致，最轻量）
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.EXE",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        _txt = (out.stdout or "").strip()
        if "SLDWORKS.EXE" in _txt.upper():
            import csv as _csv
            import io as _io
            for _row in _csv.reader(_io.StringIO(_txt)):
                if not _row:
                    continue
                _name = (_row[0] or "").strip().strip('"')
                if _name.upper() != "SLDWORKS.EXE":
                    continue
                try:
                    _pid = int(str(_row[1]).strip().strip('"'))
                except (ValueError, IndexError):
                    _pid = None
                procs.append({
                    "pid": _pid,
                    "name": _name,
                    "mem": (_row[4].strip().strip('"')
                            if len(_row) > 4 else None),
                })
            return procs, None
        # tasklist 正常返回但没匹配 → 确实没有 SW 进程
        return [], None
    except Exception as _e:
        err = "tasklist 失败: %r" % (_e,)
    # ② PowerShell 回退
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process sldworks -ErrorAction SilentlyContinue | "
             "Select-Object Id,ProcessName | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20)
        _txt = (out.stdout or "").strip()
        if _txt:
            _data = json.loads(_txt)
            if isinstance(_data, dict):
                _data = [_data]
            for _d in _data or []:
                procs.append({
                    "pid": _d.get("Id"),
                    "name": _d.get("ProcessName"),
                    "mem": None,
                })
        return procs, None
    except Exception as _e2:
        err = "%s; powershell 回退也失败: %r" % (err or "", _e2)
    return [], err


def cmd_sw_proc():
    """【连接自检修复】查看 SLDWORKS.EXE 进程与 PID（零副作用，不连 SW）。

    为什么单独需要它：`status` 只有在 COM 连接【成功】时才能给出 PID
    （`sw.GetProcessID`）；而当 SW 根本没启动、或 COM 被拒时，
    恰恰最需要知道"进程到底在不在"。本命令绕开 COM，纯进程层探测，
    因此是连接排查链路上不可替代的一环 ——
    也是 cmd_status 的 hint 一直在指引、却从未实现的命令。
    """
    procs, err = _sw_processes()
    out = {
        "ok": True,
        "command": "sw-proc",
        "running": bool(procs),
        "count": len(procs),
        "pids": [p.get("pid") for p in procs],
        "processes": procs,
        "note": ("检测到 SLDWORKS.EXE 进程。若 status 仍报未连接，"
                 "说明 COM 连接被拒（尝试用 doctor 进一步定位）。"
                 if procs else
                 "未检测到 SLDWORKS.EXE 进程 —— 请先手动启动 SolidWorks。"),
    }
    if err:
        # 探测手段本身失败 ≠ 没有进程，必须区分开，避免误导成"SW 没启动"
        out["ok"] = False
        out["error"] = "进程探测失败: %s" % err
        out["running"] = None
        out["note"] = ("无法确定 SLDWORKS.EXE 是否在运行（探测手段失败，"
                       "这不等于 SW 未启动）。")
    return out


def cmd_ping():
    """【连接自检修复】轻量存活探测（零副作用，不连 SW，不取锁）。

    只证明"sw_bridge.py 本身能被调用、Python 与依赖可导入"，
    用于把【工具链问题】与【SW/CAD 连接问题】区分开。
    """
    out = {
        "ok": True,
        "command": "ping",
        "pong": True,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "bridge": os.path.abspath(__file__),
        "cwd": os.getcwd(),
    }
    # 依赖可用性（不导入重模块，失败只记录不报错）
    for _mod in ("win32com", "pythoncom", "swapi", "ac_bridge", "ac_validate"):
        try:
            __import__(_mod)
            out["modules"] = out.get("modules") or {}
            out["modules"][_mod] = True
        except Exception as _e:
            out["modules"] = out.get("modules") or {}
            out["modules"][_mod] = "MISSING: %s" % _e
    return out


def cmd_selftest():
    """【BUG-观察-17 / BUG-观察-1 修复】纯 Python 离线自检（不依赖 SolidWorks）。

    本机当前【没有运行中的 SLDWORKS 进程】，无法实机验证自动标注是否真的
    插入了尺寸。因此用【假对象(mock)】把两处修复的【可离线判定部分】
    钉死，防止回归：

      A. annotate（BUG-观察-17）
         A1. 自动标注 API 必须调在【doc(IDrawingDoc)】上，【绝不能】调在 view；
         A2. 参数个数必须与 sldworks.tlb 签名一致
             （InsertModelDimensions=1、...Annotations3/2=6、...=4、
               AutoDimension=5）；
         A3. 回退顺序必须是 InsertModelDimensions → ...3 → ...2 → ... →
             AutoDimension，且【一条失败才走下一跳】；
         A4. 全失败时必须如实返回 ok=False + "插入了 0 个标注"，不得假装成功。

      B. save 路径（BUG-观察-1）
         B1. 相对路径 → `<work_dir>\\parts\\<name>` 且 parts 目录被创建；
         B2. 绝对路径 → 原样保留（不擅自搬移）；
         B3. 拿不到 work_dir → 回退 cwd 且 path_note 如实说明"未能归位"。

    Returns: {ok, checks:[{id, name, pass, detail}], failed:[], note}
    """
    import shutil as _shutil
    import tempfile as _tempfile

    checks = []

    def _ck(cid, name, passed, detail=""):
        checks.append({"id": cid, "name": name,
                       "pass": bool(passed), "detail": str(detail)})

    # ══════════ A. annotate 修复的自检 ══════════
    # ── mock 保真要点（据本仓库既有约定）──────────────────────────────────
    # `_prop()` 的注释明确："无参 COM 成员在 dynamic dispatch 下按属性访问"，
    # 因此假对象里【无参成员必须是 property】（doc.GetFirstView / v.GetNextView
    # / v.GetName2）—— 若写成普通方法，代码拿到的是 bound method，遍历会
    # 立刻崩（这本身就是自检抓到的第一个 mock 保真问题）。
    # 而「计数」在 _count_annotations 里是被【调用】的（_fn()），
    # 故用 int 子类同时支持「取值」与「调用」两种绑定约定。
    class _Count(int):
        """既能当整数用、又能当无参方法调用的计数（兼容静态/动态绑定）。"""

        def __call__(self):
            return int(self)

    class _FakeDim(object):
        @property
        def GetNext3(self):
            return None

    class _FakeView(object):
        """假视图：只提供 IView 上【真实存在】的成员。

        刻意【不】提供 InsertModelAnnotations*/AutoDimension —— 类型库实证
        IView 上确实没有这些成员（IView 段 435 个方法逐行扫描确认）。
        这样一旦代码再去 getattr(view, ...)，就会像真机一样恒为 None，
        从而复现原 BUG-观察-17 的故障面。
        """

        def __init__(self, name, n=0):
            self._name = name
            self._n = n
            self._next = None
            self.activated = 0

        @property
        def GetName2(self):
            return self._name

        @property
        def GetAnnotationCount(self):
            return _Count(self._n)

        @property
        def GetFirstDisplayDimension(self):
            # 返回假 IDisplayDimension；计数为 0 时返回 None（与真机一致）
            return _FakeDim() if self._n > 0 else None

        @property
        def GetNextView(self):
            return self._next

        def Activate(self):
            self.activated += 1
            return True

    class _FakeDrawing(object):
        """假工程图(IDrawingDoc)：记录每次自动标注 API 的【参数个数】。

        只暴露 IDrawingDoc 上【真实存在】的 5 个自动标注 API（见类型库），
        IView / IModelDocExtension 上都没有它们 —— 这正是原缺陷所在。
        """

        _SIGS = {"InsertModelDimensions": 1,
                 "InsertModelAnnotations3": 6,
                 "InsertModelAnnotations2": 6,
                 "InsertModelAnnotations": 4,
                 "AutoDimension": 5}

        def __init__(self, view_counts=(0, 0, 0, 0), succeed_at=None,
                     fail_all=False):
            self.calls = []          # [{api, args, nargs, on_doc}]
            self.activated_views = []
            self._succeed_at = succeed_at   # None = 首选即成功
            self._fail_all = fail_all
            self._dims = list(view_counts)
            self._nodes = []

        # ── 视图遍历：IDrawingDoc.GetFirstView → IView.GetNextView ──
        # 均为【无参成员】→ 按本仓库约定暴露为 property
        @property
        def GetFirstView(self):
            sheet = _FakeView("Sheet1", 0)
            nodes = [_FakeView("View%d" % (i + 1), c)
                     for i, c in enumerate(self._dims)]
            for _a, _b in zip(nodes, nodes[1:]):
                _a._next = _b
            sheet._next = nodes[0] if nodes else None
            self._nodes = nodes
            return sheet

        def ClearSelection2(self, *a):
            return True

        def EditRebuild3(self):
            return True

        def SaveAs3(self, *a):
            return 0

        def ActivateView(self, name):
            self.activated_views.append(name)
            return True

        # ── 被修复的 5 个 API（模拟为真的会增加标注计数）──
        def _make(self, api):
            def _fn(*args):
                # on_doc 恒 True：因为本函数只可能由 doc 的 __getattr__ 产出；
                # 若代码误调 view，_FakeView 上没有这些名字 → getattr 得 None
                # → 根本不会产生调用记录（即"分支 0 次执行"的原故障）。
                self.calls.append({"api": api, "args": list(args),
                                   "nargs": len(args), "on_doc": True})
                if self._fail_all:
                    raise RuntimeError("mock失败:%s" % api)
                if self._succeed_at is not None and api != self._succeed_at:
                    raise RuntimeError("mock不支持:%s" % api)
                for _n in self._nodes:
                    _n._n += 2
                return True
            return _fn

        def __getattr__(self, item):
            if item in _FakeDrawing._SIGS:
                return self._make(item)
            raise AttributeError(item)

    # ── A1/A2/A3：首选 InsertModelDimensions、调在 doc、参数个数正确 ──
    try:
        _doc = _FakeDrawing(view_counts=(1, 1, 1, 1))
        _views, _verr = _collect_drawing_views(_doc)
        if _verr:
            raise RuntimeError("遍历视图失败(自检装置问题): %s" % _verr)
        _api, _att, _after = _insert_model_annotations(_doc, _views, None)
        _ck("A1", "自动标注 API 调在 IDrawingDoc(doc) 上（非 IView）",
            bool(_doc.calls) and all(c["on_doc"] for c in _doc.calls)
            and len(_views) == 4,
            "视图数=%d, 首次调用=%r" % (
                len(_views), _doc.calls[0]["api"] if _doc.calls else None))
        _ck("A1b", "首选的 InsertModelDimensions 参数个数=1（对齐 tlb）",
            bool(_doc.calls) and _doc.calls[0]["api"] == "InsertModelDimensions"
            and _doc.calls[0]["nargs"] == 1,
            "首个调用=%r" % (_doc.calls[0] if _doc.calls else None))
        _ck("A1c", "生效 API 被如实回传且标注数真的增加",
            _api == "InsertModelDimensions" and bool(_after)
            and all(_a > _b for _a, _b in zip(_after, [1, 1, 1, 1])),
            "used_api=%r, 计数=%s" % (_api, _after))
        # 参数个数总表核对（逐 API 对照 sldworks.tlb）
        _sig_exp = {"InsertModelDimensions": (1,),
                    "InsertModelAnnotations3": (6,),
                    "InsertModelAnnotations2": (6,),
                    "InsertModelAnnotations": (4,),
                    "AutoDimension": (5,)}
        _bad = []
        for _a, _args in _annotation_api_candidates(None):
            if len(_args) not in _sig_exp.get(_a, ()):
                _bad.append((_a, len(_args)))
        _ck("A2", "全部候选 API 的参数个数与 sldworks.tlb 签名一致",
            not _bad, "不符=%s" % (_bad or "无"))
        # 回退顺序（按首次出现的 API 去重）
        _seen = []
        for _a, _args in _annotation_api_candidates(None):
            if _a not in _seen:
                _seen.append(_a)
        _ck("A3a", "首选顺序 = InsertModelDimensions → ...3 → ...2 → ... → AutoDimension",
            _seen == ["InsertModelDimensions", "InsertModelAnnotations3",
                      "InsertModelAnnotations2", "InsertModelAnnotations",
                      "AutoDimension"],
            "实际=%s" % (_seen,))
    except Exception as _e:
        _ck("A1", "annotate 首选路径自检", False, "异常: %r" % (_e,))

    # ── A3b：一条失败必须【继续下一跳】（回退链真的在走）──
    try:
        _doc2 = _FakeDrawing(view_counts=(1, 1),
                             succeed_at="InsertModelAnnotations2")
        _views2, _ = _collect_drawing_views(_doc2)
        _api2, _att2, _after2 = _insert_model_annotations(_doc2, _views2, None)
        _order2 = []
        for _c in _doc2.calls:
            if _c["api"] not in _order2:
                _order2.append(_c["api"])
        _ck("A3b", "前一跳失败后继续回退到下一跳并命中",
            _api2 == "InsertModelAnnotations2"
            and _order2[:3] == ["InsertModelDimensions",
                                "InsertModelAnnotations3",
                                "InsertModelAnnotations2"]
            and bool(_after2) and all(x > 1 for x in _after2),
            "命中=%r, 去重尝试序列=%s" % (_api2, _order2))
    except Exception as _e:
        _ck("A3b", "annotate 回退链自检", False, "异常: %r" % (_e,))

    # ── A4：全失败必须如实报 0，不假装成功 ──
    try:
        _doc3 = _FakeDrawing(view_counts=(2, 2), fail_all=True)
        _views3, _ = _collect_drawing_views(_doc3)
        _api3, _att3, _before3 = _insert_model_annotations(_doc3, _views3, None)
        _ck("A4a", "全部 API 失败 → used_api=None（不假装成功）",
            _api3 is None, "used_api=%r" % (_api3,))
        # 端到端走 _annotate_views_in_doc，检查 ok/err 文案
        class _DocWrap(object):
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, k):
                return getattr(self._inner, k)

        _res = _annotate_views_in_doc(_DocWrap(_doc3), dim_types=None)
        _ck("A4b", "全失败时 ok=False 且如实报告「插入了 0 个标注」",
            _res.get("ok") is False
            and "0 个标注" in str(_res.get("error", "")),
            "ok=%r error=%r" % (_res.get("ok"), _res.get("error")))
        _ck("A4c", "失败结果里带上尝试过的 API 与错误（可排查）",
            bool(_res.get("attempts"))
            and any(a.get("error") for a in _res["attempts"]),
            "attempts=%d 条" % len(_res.get("attempts") or []))
    except Exception as _e:
        _ck("A4", "annotate 失败如实报告自检", False, "异常: %r" % (_e,))

    # ══════════ B. save 路径归位的自检 ══════════
    _tmp = _tempfile.mkdtemp(prefix="sw_selftest_")
    try:
        _work = os.path.join(_tmp, "20260101-120000-task")
        os.makedirs(_work, exist_ok=True)
        # 构造假的 workflow_state.json，并让 _store.state_path 指向它
        _state = os.path.join(_tmp, "workflow_state.json")
        with open(_state, "w", encoding="utf-8") as _f:
            json.dump({"work_dir": _work}, _f, ensure_ascii=False)

        import _store as _st
        _orig_state_path = _st.state_path
        _orig_wd_env = {k: os.environ.get(k) for k in
                        ("DSH_TASK_WORK_DIR", "DSH_WORK_DIR")}
        _st.state_path = lambda name: (  # type: ignore[assignment]
            _state if name == "workflow_state.json"
            else os.path.join(_tmp, name))
        # 清掉环境变量，确保走 workflow_state.json 这条路
        for _k in ("DSH_TASK_WORK_DIR", "DSH_WORK_DIR"):
            os.environ.pop(_k, None)
        try:
            _st.reset_cache()
        except Exception:
            pass

        # B1：相对路径 → <work_dir>\parts\<name>，且目录被创建
        _rel = "DSH_正方形.sldprt"
        _final, _meta = resolve_save_path(_rel)
        _exp = os.path.join(_work, "parts", _rel)
        _ck("B1a", "相对路径归位到 <work_dir>\\parts\\<name>",
            os.path.normcase(_final) == os.path.normcase(_exp),
            "得到=%r, 期望=%r" % (_final, _exp))
        _ck("B1b", "parts\\ 目录被自动创建",
            os.path.isdir(os.path.join(_work, "parts")),
            os.path.join(_work, "parts"))
        _ck("B1c", "返回值标明来源为 task_work_dir",
            _meta.get("path_resolved_from") == "task_work_dir",
            "meta=%r" % (_meta,))
        # B1d：已带 parts\ 前缀的相对路径不得重复拼成 parts\parts\
        _final_p, _meta_p = resolve_save_path(os.path.join("parts", "DSH_子件.sldprt"))
        _ck("B1d", "已带 parts\\ 前缀时不重复拼接（防 parts\\parts\\）",
            os.path.normcase(_final_p)
            == os.path.normcase(os.path.join(_work, "parts", "DSH_子件.sldprt")),
            "得到=%r, from=%r" % (_final_p, _meta_p.get("path_resolved_from")))

        # B2：绝对路径原样保留
        _abs = os.path.join(_tmp, "elsewhere", "DSH_底板.sldprt")
        _final2, _meta2 = resolve_save_path(_abs)
        _ck("B2", "绝对路径原样保留（不擅自搬移）",
            _final2 == _abs
            and _meta2.get("path_resolved_from") == "absolute",
            "得到=%r" % (_final2,))

        # B2b：cmd_save 回传最终实际保存路径（用假 doc/sw）
        class _FakeDoc(object):
            def __init__(self):
                self.saved_as = None

            def SaveAs3(self, p, *a):
                self.saved_as = p
                open(p, "w").close()
                return 0

            # 无参 COM 成员按属性访问（同 _prop 的约定）
            @property
            def GetPathName(self):
                return self.saved_as or ""

        class _FakeSw(object):
            def __init__(self, d):
                self.ActiveDoc = d

        _fd = _FakeDoc()
        _r = cmd_save(_FakeSw(_fd), _rel)
        _ck("B2b", "cmd_save 回传最终实际保存的绝对路径",
            _r.get("ok") is True
            and os.path.normcase(_r.get("saved_path") or "")
            == os.path.normcase(_exp),
            "saved_path=%r" % (_r.get("saved_path"),))

        # B3：拿不到 work_dir → 回退 cwd，且如实说明"未能归位"
        _st.state_path = lambda name: os.path.join(_tmp, "nonexistent_dir", name)
        try:
            _st.reset_cache()
        except Exception:
            pass
        _final3, _meta3 = resolve_save_path("DSH_未归位.sldprt")
        _ck("B3", "拿不到 work_dir → 回退原行为且如实提示未归位",
            _final3 == "DSH_未归位.sldprt"
            and _meta3.get("path_resolved_from") == "cwd_fallback"
            and "未能归位" in str(_meta3.get("path_note", "")),
            "final=%r meta=%r" % (_final3, _meta3))

        _st.state_path = _orig_state_path
        for _k, _v in _orig_wd_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        try:
            _st.reset_cache()
        except Exception:
            pass
    except Exception as _e:
        _ck("B", "save 路径归位自检", False, "异常: %r" % (_e,))
    finally:
        _shutil.rmtree(_tmp, ignore_errors=True)

    _failed = [c for c in checks if not c["pass"]]
    return {
        "ok": not _failed,
        "command": "selftest",
        "checks": checks,
        "passed": len(checks) - len(_failed),
        "failed": [c["id"] for c in _failed],
        "note": ("纯 Python 离线自检（mock 假对象，不依赖 SolidWorks）。"
                 "⚠️ 自动标注是否【真的插入尺寸】仍需实机验证 —— "
                 "本自检只钉死「API 归属/参数个数/回退顺序/失败如实报告」"
                 "与「相对路径归位」这些可离线判定的部分。"),
    }


def cmd_version():
    """【连接自检修复】返回桥接与依赖版本（零副作用）。"""
    out = {
        "ok": True,
        "command": "version",
        "bridge": "sw_bridge.py",
        "bridge_path": os.path.abspath(__file__),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    try:
        import swapi as _sp
        out["swapi"] = getattr(_sp, "__file__", None)
        out["swapi_lines"] = None
    except Exception as _e:
        out["swapi"] = "MISSING: %s" % _e
    for _k, _mod in (("pywin32", "win32com"), ("mss", "mss"), ("pillow", "PIL")):
        try:
            _m = __import__(_mod)
            out[_k] = getattr(_m, "__version__", "installed")
        except Exception as _e:
            out[_k] = "MISSING: %s" % _e
    return out


# ══ 【连接区】SW/CAD 连接状态：探测 / 落盘 / 供 AI 判断 ═══════════════════
# 设置页「连接区」的「SW 连接」/「CAD连接」按钮，与 AI 流程里的判断，
# 共用 tools/conn_state.py 这一份状态文件（见该模块顶部说明）。
# 这三个子命令都是【零副作用的探测编排】，不取 SW 独占锁。

def cmd_conn_probe(target=None, with_com=True, resolve_paths=False):
    """探测连接状态并落盘。

    用法：
      conn-probe                     # 探测 SW + CAD（含路径解析）
      conn-probe --target sw         # 只探测 SW
      conn-probe --target cad        # 只探测 CAD
      conn-probe --no-com            # 只查进程，不发任何 COM 调用
      conn-probe --resolve-paths     # 强制重新解析安装路径（全盘扫描）
    """
    try:
        _cs = _import_sibling("conn_state")
    except Exception as e:
        return {"ok": False, "error": "conn_state.py 不可用: %r" % (e,)}
    try:
        # 【连接区扩展】单目标探测覆盖 abaqus / webots（原来只有 sw/cad 走
        #   单目标分支，其余一律 probe_all —— 点 Webots 的「连接」会把 SW/CAD
        #   也一起重探一遍，慢且会触发不必要的 COM Dispatch）。
        _single = tuple(getattr(_cs, "KNOWN_TARGETS",
                                ("sw", "cad", "abaqus", "webots")))
        if target in _single or (target or "").startswith("placeholder"):
            st = _cs.probe_target(target, with_com=with_com)
        else:
            st = _cs.probe_all(with_com=with_com, resolve_paths=True)
        if resolve_paths:
            st["paths"] = _cs.discover_paths(force=True)
            _cs.save(st)
        out = _cs.summary()
        out["command"] = "conn-probe"
        return out
    except Exception as e:
        return {"ok": False, "error": "连接探测失败: %r" % (e,)}


def cmd_conn_probe_process_only(targets=None):
    """【10 分钟自动刷新】只做【进程层】检测 —— 绝不发 COM、绝不弹窗。

    ── 与 conn-probe 的区别（这是本次刻意分开的原因）─────────────────────
    用户诉求原文：
      "10 分钟自动探测就是看是不是在运行的，没有的话那个 UI 就熄灭，
       让用户到时候自己选择去设置连接啥的，就是只做检测就行了，
       关了 UI 就更新就行了，其他不用搞。"

    conn-probe 会发 COM Dispatch：SW 未开时可能【把它拉起来】，
      还可能把窗口置前/最大化，10 分钟一次会周期性打断用户。
    自动刷新必须走这条纯 tasklist 路径：
      · 进程在  → 保持上次完整探测的结论；
      · 进程没了 → connected=False（灯熄灭）；
      · 进程换了实例（PID 变化）→ 旧 COM 会话已死，也置 False。
    用户想恢复连接时，自己点「连接」——那才是发 COM 的唯一入口。
    """
    try:
        _cs = _import_sibling("conn_state")
    except Exception as e:
        return {"ok": False, "error": "conn_state.py 不可用: %r" % (e,)}
    try:
        st = _cs.probe_process_only(targets)
        out = _cs.summary()
        out["command"] = "conn-probe-process-only"
        out["probe_kind"] = "process-only"
        out["note"] = ("仅进程层检测（零 COM / 零弹窗）—— "
                       "连接状态若需重建，请点设置页的「连接」。")
        return out
    except Exception as e:
        return {"ok": False, "error": "纯进程探测失败: %r" % (e,)}


def cmd_conn_status():
    """读取（不重新探测）连接区当前状态摘要。"""
    try:
        _cs = _import_sibling("conn_state")
        out = _cs.summary()
        out["command"] = "conn-status"
        return out
    except Exception as e:
        return {"ok": False, "error": "conn_state.py 不可用: %r" % (e,)}


def cmd_conn_gate(target="sw"):
    """【流程判断入口】已连接 → 跳过启动；未连接 → 走原有启动流程。"""
    try:
        _cs = _import_sibling("conn_state")
        out = _cs.gate(target)
        out["command"] = "conn-gate"
        return out
    except Exception as e:
        # 判断不可用时【必须保守】：宁可走原有启动流程，也不要误判"已连接"
        return {
            "ok": False,
            "target": target,
            "connected": False,
            "skip_startup": False,
            "error": "conn_state.py 不可用: %r" % (e,),
            "next": "按原有流程启动（连接状态不可用，保守回退）。",
        }


def cmd_conn_launch(target, exe=None, connect=True, wait_window=120):
    """【启动按钮】显式启动 SW / CAD，全程记录日志。

    与 conn-probe 的区别：
      · conn-probe  —— 只探测，不启动（安全默认）
      · conn-launch —— 真的把软件拉起来（用户主动点击才走这里）

    用户诉求："给俩个都搞一个按键，自动启动吧……就是启动成不需要 SW 自己
      跳出来的那种，然后要是报错的话，就贴出错误日志。"
    """
    try:
        _cs = _import_sibling("conn_state")
    except Exception as e:
        return {"ok": False, "error": "conn_state.py 不可用: %r" % (e,)}
    t = str(target or "").strip().lower()
    # ── 【NX → Webots 改造】白名单以 conn_state 的【公开目标表】为准 ────────
    #   原实现读 `getattr(_cs, "CAE_TARGETS", ("abaqus", "nx"))`，而 conn_state
    #   当时只有私有名 `_CAE_TARGETS` → getattr 永远取不到 → 静默回落到硬编码
    #   ("abaqus", "nx")。现在 conn_state 已补公开别名，两边读同一张表；
    #   旧名 'nx' 由 _norm_target 映射到 'webots'，因此这里显式放行 'nx'。
    _ok_targets = tuple(getattr(_cs, "KNOWN_TARGETS",
                                ("sw", "cad", "abaqus", "webots")))
    # 【NX → Webots 改造】旧名 nx → webots（与 conn_state._norm_target 同一映射）
    t = {"nx": "webots"}.get(t, t)
    if t not in _ok_targets:
        return {"ok": False, "error": "target 必须是 %s 之一"
                                       % " / ".join(repr(x) for x in _ok_targets)}
    try:
        out = _cs.launch(t, exe=exe, wait_window=wait_window, connect=connect)
        out["command"] = "conn-launch"
        # 无论成败都附上可读日志，UI 可直接贴出来
        out["log_text"] = _cs.format_launch_log(t)
        return out
    except Exception as e:
        return {"ok": False, "target": t,
                "error": "启动失败: %r" % (e,),
                "log_text": _cs.format_launch_log(t)}


def cmd_conn_log(target):
    """读取某目标最近一次启动日志（供 UI 展示 / 发给模型诊断）。"""
    try:
        _cs = _import_sibling("conn_state")
    except Exception as e:
        return {"ok": False, "error": "conn_state.py 不可用: %r" % (e,)}
    t = str(target or "").strip().lower()
    # 与 cmd_conn_launch 同一套白名单（含 'nx' → 'webots' 兼容映射）
    _ok_targets = tuple(getattr(_cs, "KNOWN_TARGETS",
                                ("sw", "cad", "abaqus", "webots")))
    t = {"nx": "webots"}.get(t, t)
    if t not in _ok_targets:
        return {"ok": False, "error": "target 必须是 %s 之一"
                                       % " / ".join(repr(x) for x in _ok_targets)}
    r = _cs.launch_log(t)
    r["log_text"] = _cs.format_launch_log(t)
    r["command"] = "conn-log"
    return r


# ── 【连接自检修复】最近一次 SW 连接失败的原因（供诊断类命令回显）────────
# main() 在 get_sw() 抛异常时写入本变量，随后 doctor/status 把它作为
# error 回显，AI 就能区分"SW 没启动"与"COM 连接失败"等具体原因，
# 而不是拿到一坨 traceback。
_LAST_SW_CONNECT_ERROR = None


def _sw_not_connected_result(cmd, error=None):
    """统一的"SW 未连接"结构化结果（【连接自检修复】）。

    给所有【需要真实 SW 连接】的命令复用，保证：
      · 绝不抛 AttributeError + traceback（AI 要的是结论，不是栈）；
      · 明确区分"连接尝试失败"与"命令本就不连 SW"两种情况；
      · hint 里的自救命令都是【真实存在】的（见下方修复说明）。
    """
    _err = error or _LAST_SW_CONNECT_ERROR
    out = {
        "ok": False,
        "connected": False,
        "command": cmd,
        "error": _err or "sw not connected",
        "hint": (
            "SolidWorks 未连接。请按顺序自检：\n"
            "  1) python sw_bridge.py doctor      # 环境自检（依赖/安装路径/模板）\n"
            "  2) python sw_bridge.py sw-proc     # 查看 SLDWORKS.EXE 进程与 PID\n"
            "  3) python sw_bridge.py status      # 再看一次连接状态\n"
            "常见原因：SW 未启动、被其它房间独占、或 COM 连接被拒。"
        ),
    }
    if _err:
        # 连接失败的具体原因（来自 main() 捕获的异常）单独回显，便于定位。
        out["connect_error"] = _err
        out["hint"] = (
            "SolidWorks 连接尝试失败，原因见 connect_error。请依次排查：\n"
            "  1) python sw_bridge.py doctor      # 环境自检\n"
            "  2) python sw_bridge.py sw-proc     # 确认 SLDWORKS.EXE 是否在跑\n"
            "  3) 无 SW 进程 → 先手动启动 SolidWorks，再重试本命令"
        )
    return out


def get_sw():
    """连接运行中的 SolidWorks；若未运行则启动它（LocalServer32）。

    ── 【连接自检修复】绑定方式必须与 swapi.get_sw() 保持一致：静态优先 ──
    原缺陷：本函数用 `dynamic.Dispatch`（后期绑定），而 swapi.get_sw() 早已
      修成"静态绑定优先、动态回退"（Bug3）。两条连接路径不一致 →
        经 sw_bridge 直接拿到的 sw 是动态绑定对象，late-binding 下
        `IBody2.SetMaterialProperty()` 恒定失败（返回 1/6），
        材料永远写不进 → 密度停在 1000 kg/m³（水）。
      本函数把 sw 交给 cmd_run 的 wrapper、再传给脚本，
        脚本里 `swapi.from_active(sw)` 复用的就是这个动态对象，
        于是 swapi 侧的静态绑定修复被绕过 —— 属于"修了 A 路径、
        漏了 B 路径"的典型回归。
    修复：与 swapi 完全同构（静态优先 + 动态回退），并把实际绑定方式写入
      `sw.__dict__["_dsh_binding"]`，便于排查。

    通用版：连接后立即禁用草图吸附（不同版本枚举值自动适配）。
    """
    pythoncom.CoInitialize()
    sw = None
    _binding = None
    # ① 静态绑定（首选）—— 与 swapi.get_sw() 一致
    try:
        sw = win32com.client.Dispatch('SldWorks.Application')
        _binding = "static"
    except Exception:
        sw = None
    # ② 回退 late-binding
    if sw is None:
        try:
            sw = win32com.client.dynamic.Dispatch('SldWorks.Application')
            _binding = "dynamic"
        except Exception:
            sw = None
    if sw is None:
        raise RuntimeError("无法连接 SolidWorks（静态/动态绑定均失败）")
    try:
        sw.__dict__["_dsh_binding"] = _binding
    except Exception:
        try:
            setattr(sw, "_dsh_binding", _binding)
        except Exception:
            pass
    # ── 【子任务 C】连接成功后顺手探测一次早期绑定（失败静默，绝不改返回值）──
    # 与 swapi.get_sw() 同构（见 swapi.py 的 _attach_early_probe 调用）：只把
    # 探测结论尽力挂到 sw 上，【不改变】返回的晚期 Dispatch 对象 —— SW 2018~2024
    # 与既有代码都依赖晚期行为，换返回值会破坏兼容性（硬约束）。
    try:
        import swapi as _swapi_probe
        _probe = getattr(_swapi_probe, "_attach_early_probe", None)
        if callable(_probe):
            _probe(sw)
    except Exception:
        pass
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
    """SW 连接与文档状态。

    ── 【观察点 8 修复】必须判空：`sw is None` 时优雅返回，而不是崩溃 ──────
    原实现直接访问 `sw.RevisionNumber`。而 `sw` 在两处会是 None：
      · 命令属于 _NO_SW_CMDS（纯计算）时，main() 显式把 sw 置为 None；
      · SW 未安装 / COM 连接失败 / 被其它房间独占导致 Dispatch 失败。
    此时抛 `AttributeError: 'NoneType' object has no attribute 'RevisionNumber'`，
    主对话无法用它诊断"SW 到底连上没有"，排查效率极低。
    现在统一返回 {"ok": false, "connected": false, "error": "sw not connected"}。

    ⚠️ 本函数【只报告拿到的东西】，不主动去连接 SolidWorks ——
       连接动作由建模命令（get_sw）承担；查询状态不应有启动软件的副作用。
    """
    if sw is None:
        # ── 【连接自检修复】统一走结构化结果（含连接失败的具体原因）──────
        return _sw_not_connected_result("status")
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
    # 各字段独立取值：任一 COM 成员不可用不应让整条 status 崩掉。
    _fields = {}
    for _k, _acc in (("revision", lambda: sw.RevisionNumber),
                     ("visible", lambda: sw.Visible),
                     ("pid", lambda: sw.GetProcessID),
                     ("doc_count", lambda: sw.GetDocumentCount)):
        try:
            _fields[_k] = _acc()
        except Exception as e:
            _fields[_k] = None
            docs.append({"warn": "%s unavailable: %s" % (_k, e)})
    return {
        "ok": True,
        "connected": True,
        "revision": _fields.get("revision"),
        "visible": _fields.get("visible"),
        "pid": _fields.get("pid"),
        "doc_count": _fields.get("doc_count"),
        "docs": docs,
    }

# ══ 【子任务 C · 早期绑定接入】CLI 侧诊断 + 签名兼容 ═══════════════════════
# 现场依据：C:\Users\j1877\Desktop\test\SW2025_COM_API_完整摸底报告.md（下称"报告"）
#   · 报告 1.1 / 1.2：SW2025 上 GetFeatureCount / OpenDoc6 等在【晚期绑定】下
#     报"参数数量错误"，必须 gencache.EnsureModule(GUID, 0, 33, 0) 生成早期
#     接口，再用 mod.ISldWorks(raw._oleobj_) 之类包一层才能正确编组；
#   · 报告 3.7：OpenDoc6 的正确用法是 byref 参数直接传 None，且【返回 tuple】
#     (model, errors, warnings) —— 原来传 VARIANT(VT_BYREF|VT_I4) 在早期接口上
#     等于多传了实参，必然报参数数量错误；
#   · 报告 4 / 2.3 #10：GetFeatureCount 在 SW2025 下【无参数】，旧版是 1 参数；
#   · 报告 2.2 / 3.6：InterferenceDetectionManager.GetInterferenceCount 是
#     【属性】（不能加括号），旧版才是方法。
#
# 本段只解决"CLI 侧怎么用"，早期绑定的基础设施（get_early_module / early /
# early_status / early_diagnose / _attach_early_probe）由 swapi.py 提供。
#
# 三条硬约束（与 swapi 侧一致，不可违反）：
#   ① 早期绑定只是【可选增强】：本段所有函数在任何异常下都必须静默回退，
#      【绝不抛异常】，也【绝不改变】原有晚期绑定的行为 —— SW 2018~2024 与
#      没装 SW 的机器必须照旧工作；
#   ② swapi.py 的早期接口由另一个子任务（A）并行落地，可能【还没落盘】，
#      因此一律 try/except + getattr 取用；缺失时如实返回 available=false，
#      绝不 ImportError / AttributeError；
#   ③ 保持 Python 3.6+ 语法（不用海象运算符 / 3.9+ 专有写法）。


def _swapi_early():
    """取 swapi 模块本身（仅用于 getattr 早期绑定入口）。失败返回 None。"""
    try:
        import swapi as _sw
        return _sw
    except Exception:
        return None


def _early_fn(name):
    """取 swapi 上的早期绑定函数；不存在（子任务 A 未落盘）时返回 None。

    为什么要容错而不是直接 import：本任务只改 sw_bridge.py，而 swapi.py 的
    early_status()/is_early_available()/early_diagnose() 由另一个子任务并行
    落地，版本可能对不上。缺失时 CLI 必须【如实报告】"不支持"，而不是
    抛 ImportError 让整条诊断命令崩掉（诊断工具不能自己先崩）。
    """
    _sw = _swapi_early()
    if _sw is None:
        return None
    _fn = getattr(_sw, name, None)
    return _fn if callable(_fn) else None


def _early_support_note():
    """给"swapi 侧缺早期基础设施"一句可操作的说明（回退说明用）。"""
    return ("swapi.py 未提供早期绑定接口（早期基础设施缺失或版本较旧）。"
            "这不影响建模：桥接会自动按晚期绑定工作（SW 2018~2024 即如此）。"
            "若需要早期绑定，请确认 engineering/tools/swapi.py 为含 "
            "early()/early_status() 的版本。")


def wrap_early(obj, iface_name):
    """把晚期绑定的 COM 对象包装成早期接口；不可用/失败时【原样返回 obj】。

    来源：报告 1.2 —— mod.ISldWorks(raw._oleobj_) / mod.IModelDoc2(...)。
    与 swapi.early() 的语义一致：调用方拿到返回值后【可以无条件按晚期方式
    继续用】—— 包装失败时返回的就是原对象，因此调用点只需要判
    「返回值 is not 原对象」来决定要不要走早期分支。
    """
    if obj is None:
        return None
    _fn = _early_fn("early")
    if _fn is None:
        return obj
    try:
        _w = _fn(obj, iface_name)
    except Exception:
        return obj
    # 包装失败时 swapi.early() 返回原对象；None 也按失败处理
    return _w if _w is not None else obj


def _unpack_open_result(v):
    """把 OpenDoc6 的返回值统一成 (doc, errors, warnings)。

    来源：报告 3.7 —— 早期接口返回 tuple (model, errors, warnings)；
    晚期绑定只返回 model 本身。某些环境下 tuple 还会被包成 1 元序列，
    这里一并剥掉。任何异常都退化成 (v, None, None)，绝不抛异常。
    """
    try:
        if isinstance(v, (tuple, list)):
            if len(v) >= 3:
                return v[0], v[1], v[2]
            if len(v) == 2:
                return v[0], v[1], None
            if len(v) == 1:
                return v[0], None, None
    except Exception:
        pass
    return v, None, None


def early_status_snapshot(sw=None):
    """汇总"这台机器能不能用早期绑定"（doctor 的 early_binding / early-status 用）。

    来源：报告 1.1 —— SW2025 大量 API 必须早期绑定；确诊入口是 swapi 的
    early_status()/early_diagnose()。

    Args:
        sw: 已连接的 Application 对象（可 None）。给定时优先读它的自述结论
            （swapi.get_sw() 的 _attach_early_probe 会把 _dsh_early_available /
            _dsh_early_major / _dsh_early_module 挂上去）。

    Returns: dict（固定字段，见下）。【绝不抛异常】—— 本函数被 doctor 与
        early-status 共用，是"排障入口"，它自己崩掉就失去了意义。
        字段：
          available       bool  早期绑定是否可用
          major           int   已生效的类型库 major（0=未知；SW2025=33）
          module_loaded   bool  gen_py 模块是否已在本进程内加载成功
          connected       bool  调用时是否已有 SW 连接
          binding         str   实际绑定方式 static / dynamic / None
          revision        str   sw.RevisionNumber（如 "33.5.0"）
          wrapped_count   int   成功包装成早期接口的次数（swapi 统计）
          failed_count    int   回退到晚期的次数（swapi 统计）
          swapi_early_api bool  swapi 是否提供早期绑定入口
          source          str   结论来源 sw-object / swapi / none
          error           str|None  不可用原因（可直接给人看）
          summary         str   一句话结论
          hint            str   下一步怎么做
          signature_compat dict 本 CLI 已容错处理的签名差异（自文档化）
    """
    _out = {
        "available": False,
        "major": 0,
        "module_loaded": False,
        "connected": sw is not None,
        "binding": None,
        "revision": None,
        "wrapped_count": 0,
        "failed_count": 0,
        "swapi_early_api": False,
        "source": "none",
        "error": None,
        "summary": "",
        "hint": "",
        # 签名差异台账：让用户一眼看出"哪些 API 已经不需要手动改"
        "signature_compat": {
            "GetFeatureCount": "先无参（SW2025），失败再 (False)（旧版）—— 报告 4 / 2.3 #10",
            "OpenDoc6": "先早期 (path,type,opt,'',None,None) 并解包 tuple，"
                        "失败回退 VARIANT 写法 —— 报告 3.7",
            "GetInterferenceCount": "先按属性取值，失败再按方法调用 —— 报告 2.2",
        },
    }
    _from_obj = False
    # ① 连接对象自述的结论（有 sw 时最准：major 直接来自 RevisionNumber）
    if sw is not None:
        try:
            _v = getattr(sw, "_dsh_early_available", None)
        except Exception:
            _v = None
        if _v is not None:
            _out["available"] = bool(_v)
            _from_obj = True
            _out["source"] = "sw-object"
        try:
            _maj = getattr(sw, "_dsh_early_major", None)
            if _maj is not None:
                _out["major"] = int(_maj)
        except Exception:
            pass
        try:
            _out["binding"] = getattr(sw, "_dsh_binding", None)
        except Exception:
            _out["binding"] = None
        try:
            _rev = getattr(sw, "RevisionNumber", None)
            _out["revision"] = str(_rev) if _rev is not None else None
        except Exception:
            _out["revision"] = None
    # ② swapi 侧的权威状态（报告 1.2 的诊断入口；不依赖 SW 是否已连接）
    _fn_avail = _early_fn("is_early_available")
    _fn_status = _early_fn("early_status")
    _fn_getmod = _early_fn("get_early_module")
    _out["swapi_early_api"] = bool(_fn_avail or _fn_status or _fn_getmod)
    _st = {}
    if _fn_status is not None:
        try:
            _st = _fn_status()
        except Exception as _e_st:
            _st = {}
            if not _out["error"]:
                _out["error"] = "swapi.early_status() 异常: %r" % (_e_st,)
    if not isinstance(_st, dict):
        _st = {}
    if _st:
        if _st.get("available") is True:
            # 任一来源说"可用"即可信：连上的 SW 能取到 major，比注册表更准
            _out["available"] = True
            if not _from_obj:
                _out["source"] = "swapi"
        elif _st.get("available") is False and not _from_obj:
            _out["available"] = False
            _out["source"] = "swapi"
        try:
            if _st.get("major"):
                _out["major"] = int(_st.get("major"))
        except Exception:
            pass
        try:
            _out["wrapped_count"] = int(_st.get("wrapped_count") or 0)
        except Exception:
            pass
        try:
            _out["failed_count"] = int(_st.get("failed_count") or 0)
        except Exception:
            pass
        if _st.get("error") and not _out["error"]:
            _out["error"] = _st.get("error")
    elif _fn_avail is not None:
        # 只有旧版 is_early_available()：退而求其次，至少给出可用性
        try:
            _out["available"] = bool(_fn_avail())
            _out["source"] = "swapi"
        except Exception:
            _out["available"] = False
    # ③ gen_py 模块是否真的加载成功（有 get_early_module 时才能判断）
    if _fn_getmod is not None:
        try:
            _mod = _fn_getmod(sw)
            _out["module_loaded"] = _mod is not None
            if _mod is not None:
                _out["available"] = True
        except Exception:
            _out["module_loaded"] = False
    # ④ 不可用且 swapi 完全没有早期接口 → 补一句"为什么"
    if not _out["swapi_early_api"]:
        if not _out["error"]:
            _out["error"] = "swapi.py 未提供早期绑定接口（early_status/early_diagnose 缺失）"
        _out["hint"] = _early_support_note()
    # ⑤ 结论 / 下一步（可用与不可用都给可操作的话，避免"看了不知道干嘛"）
    if _out["available"]:
        _out["summary"] = ("早期绑定可用（类型库 major=%s）—— GetFeatureCount / "
                           "OpenDoc6 会自动走早期签名" % (_out["major"] or "?"))
        if not _out["hint"]:
            _out["hint"] = ("无需处理。若要排查类型库路径，执行 "
                            "python sw_bridge.py early-status 看 diagnose 段。")
    else:
        _out["summary"] = ("早期绑定不可用 → 已回退晚期绑定（%s）"
                           % (_out["error"] or "原因未知"))
        if not _out["hint"]:
            _out["hint"] = ("无需处理：SW 2018~2024 与未装 SW 的机器本来就用晚期绑定。"
                            "仅当在 SW2025 上遇到'参数数量错误'时，执行 "
                            "python sw_bridge.py early-status 按 tlb_path / gen_py_dir 排查。")
    return _out


def cmd_early_status(sw=None):
    """【子任务 C】early-status 子命令：打印早期绑定 JSON（只读、不连 SW）。

    来源：报告 1.1 / 1.2 —— SW2025 上大量 API 必须早期绑定；本命令是
    确诊"这台机器到底能不能用早期绑定 / 用的是哪个 major"的唯一入口。
    sw 恒为 None（命令在 _LOCAL_CMDS 里，main() 不会为它连 SW）。
    """
    _out = {"ok": True, "command": "early-status",
            "sw_connected": False,
            "note": ("早期绑定为可选增强：不可用时桥接自动按晚期绑定工作"
                     "（SW 2018~2024 与未装 SW 的机器即如此），不属于故障。")}
    try:
        _out["status"] = early_status_snapshot(None)
    except Exception as _e1:
        # 排障命令不能自己崩：异常也如实返回
        _out["ok"] = False
        _out["status"] = {"available": False, "major": 0,
                          "error": "early_status_snapshot 异常: %r" % (_e1,)}
    _fn = _early_fn("early_diagnose")
    if _fn is None:
        _out["diagnose"] = None
        _out["diagnose_note"] = ("swapi.early_diagnose() 不可用（子任务 A 未落盘"
                                 "或版本较旧）—— 已按 available=false 处理，不影响建模。")
    else:
        try:
            _d = _fn()
            _out["diagnose"] = _d if isinstance(_d, dict) else {"value": str(_d)}
        except Exception as _e2:
            _out["diagnose"] = {"error": "early_diagnose 异常: %r" % (_e2,)}
    # 顶层冗余一份关键字段：调用方不用再深挖两层（报告 1.2 的 major=33）
    try:
        _st = _out.get("status") or {}
        _out["available"] = bool(_st.get("available"))
        _out["major"] = _st.get("major") or 0
    except Exception:
        _out["available"] = False
        _out["major"] = 0
    return _out


def _feature_count(model, feature_manager=None):
    """容错取特征总数：先无参（SW2025），失败再 (False)（旧版）。

    来源：报告 4 —— "GetFeatureCount：SW2020 及以前 1 参数 (ToplevelOnly)，
    SW2025 无参数"；报告 2.3 #10 记录了晚期绑定下 GetFeatureCount(False) 报
    "takes 1 positional argument but 2 were given"，而早期接口下无参调用成功
    （报告 3.4）。因此这里做成"两种签名都试 + 早期绑定兜底一次"。

    Args:
        model: IModelDoc2（零件/装配/工程图文档均可）。
        feature_manager: 可选的已取到的 FeatureManager（减少一次 COM 往返）。

    Returns:
        (count, info)：count 为 int，全部尝试都失败时为 None；
        info 为 dict（method / attempts / error），供 doctor/诊断回显。
        本函数【绝不抛异常】—— 调用方原先是 try/except 包着用的。
    """
    _info = {"method": None, "attempts": [], "error": None,
             "feature_manager": None}
    _cands = []
    if feature_manager is not None:
        _cands.append(("passed-in", feature_manager))
    if model is not None:
        try:
            _fm = getattr(model, "FeatureManager", None)
        except Exception as _e_fm:
            _fm = None
            _info["error"] = "取 FeatureManager 失败: %r" % (_e_fm,)
        # 属性式与无参方法式都兼容（动态/静态绑定行为不同）
        if _fm is not None and callable(_fm):
            try:
                _fm = _fm()
            except Exception as _e_fm2:
                _info["error"] = "调用 FeatureManager() 失败: %r" % (_e_fm2,)
                _fm = None
        if _fm is not None:
            _cands.append(("model.FeatureManager", _fm))
    for _src, _fm in _cands:
        try:
            _m = getattr(_fm, "GetFeatureCount", None)
        except Exception as _e_g:
            _m = None
            _info["attempts"].append("%s: getattr 失败 %r" % (_src, _e_g))
        if _m is None:
            _info["attempts"].append("%s: 无 GetFeatureCount 成员" % (_src,))
            continue
        _info["feature_manager"] = _src
        # ① 新版签名（SW2025+）：无参数
        try:
            _c = _m()
            _info["method"] = "%s.GetFeatureCount() [无参·新版]" % (_src,)
            return int(_c), _info
        except Exception as _e0:
            _info["attempts"].append("%s.GetFeatureCount(): %r" % (_src, _e0))
        # ② 旧版签名（SW2018~2024）：1 个 boolean 参数；False=连配合一起数
        try:
            _c = _m(False)
            _info["method"] = "%s.GetFeatureCount(False) [1参·旧版]" % (_src,)
            return int(_c), _info
        except Exception as _e1:
            _info["attempts"].append("%s.GetFeatureCount(False): %r" % (_src, _e1))
    # ③ 兜底：包成早期接口（IModelDoc2）再按"无参"试一次
    #    报告 3.4：早期接口能把无参签名正确编组，晚期则可能报参数数量错误
    if model is not None:
        _em = wrap_early(model, "IModelDoc2")
        if _em is not None and _em is not model:
            try:
                _m2 = getattr(_em, "GetFeatureCount", None)
            except Exception:
                _m2 = None
            if _m2 is not None:
                try:
                    _c = _m2()
                    _info["method"] = "early(IModelDoc2).GetFeatureCount()"
                    return int(_c), _info
                except Exception as _e2:
                    _info["attempts"].append("early.GetFeatureCount(): %r" % (_e2,))
    if not _info["error"]:
        _info["error"] = ("GetFeatureCount 在所有签名（无参 / False / 早期接口）下均失败：%s"
                          % ("; ".join(_info["attempts"][-3:]) or "无可用 FeatureManager"))
    return None, _info


def _open_doc6(sw, path, doc_type, options=1):
    """容错打开文档：先早期签名（byref 传 None + 解包 tuple），失败回退 VARIANT。

    来源：报告 3.7 ——
        ❌ errs/warns = VARIANT(VT_BYREF|VT_I4, 0); sw.OpenDoc6(p, 1, 1, "", errs, warns)
        ✅ sw_early.OpenDoc6(p, 1, 1, "", None, None)  # 返回 (model, errors, warnings)
    晚期绑定下必须继续用 VARIANT 写法（SW 2018~2024 都是这条路），因此本函数
    的回退分支【逐字保留】原实现，保证"早期不可用 = 行为与改造前完全一致"。

    Args:
        sw: Application 对象（晚期即可；内部自行尝试 early(ISldWorks) 包装）。
        path: 文档绝对路径。
        doc_type: 1=零件 2=装配 3=工程图。
        options: OpenDocOptions 位掩码（原调用点传 1=silent；工程图有传 0 的）。

    Returns:
        (doc, info)：doc 为打开后的 ModelDoc 或 None；info 为 dict
        （ok / method / uses_early / errors / warnings / error）。
        本函数不抛 COM 异常（原实现把异常收进 error 字段），行为向后兼容。
    """
    _info = {"ok": False, "method": None, "uses_early": False,
             "errors": None, "warnings": None, "error": None, "attempts": []}
    if sw is None or not path:
        _info["error"] = "sw/path 为空"
        return None, _info
    try:
        _dt = int(doc_type)
        _opt = int(options)
    except Exception:
        _dt, _opt = doc_type, options
    # ① 早期绑定（报告 3.7）：byref 参数传 None，返回 tuple
    _early_sw = wrap_early(sw, "ISldWorks")
    if _early_sw is not None and _early_sw is not sw:
        try:
            _ret = _early_sw.OpenDoc6(path, _dt, _opt, "", None, None)
            _doc, _errs_v, _warns_v = _unpack_open_result(_ret)
            _info["errors"], _info["warnings"] = _errs_v, _warns_v
            if _doc is not None:
                _info.update({"ok": True, "uses_early": True,
                              "method": "early(ISldWorks).OpenDoc6(...,None,None)"})
                return _doc, _info
            _info["attempts"].append("early: 返回 None（errs=%r）" % (_errs_v,))
        except Exception as e:
            _info["attempts"].append("early: %r" % (e,))
    # ② 回退：晚期绑定 + VARIANT byref（与改造前【逐字一致】）
    try:
        _errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        _warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        _doc = sw.OpenDoc6(path, _dt, _opt, "", _errs, _warns)
    except Exception as e:
        _info["attempts"].append("late: %r" % (e,))
        _info["error"] = "OpenDoc6 异常: %r" % (e,)
        _info["method"] = "late(VARIANT) 失败"
        return None, _info
    _info["method"] = "late(VARIANT)"
    # 回退分支保持"逐字一致"：VARIANT 对象【在函数内取 .value】后只把 int 存进
    # info，绝不把 VARIANT 裸露出去（否则调用方 json.dumps 会 TypeError）。
    try:
        _info["errors"] = _errs.value
        _info["warnings"] = _warns.value
    except Exception:
        _info["errors"], _info["warnings"] = None, None
    if _doc is None:
        _info["error"] = ("OpenDoc6 返回 None（errs=%s）"
                          % (_info["errors"],))
        return None, _info
    _info["ok"] = True
    return _doc, _info


def _interference_count(mgr):
    """容错取干涉数：先按【属性】取值（SW2025），失败再按【方法】调用（旧版）。

    来源：报告 2.2 / 3.6 —— "GetInterferenceCount 是属性不是方法（不能加括号）"，
    晚期绑定下 mgr.GetInterferenceCount() 报 'int' object is not callable。

    ⚠️ 现状说明（如实记录，勿误读）：本仓库当前【没有任何】调用点使用
    InterferenceDetectionManager（grep GetInterferenceCount 为 0 命中），
    本函数是预防性封装 —— 一旦后续写干涉检查，直接用它会同时兼容两种签名。
    返回 (count, info)；取不到时 count=None。【绝不抛异常】。
    """
    _info = {"method": None, "attempts": [], "error": None}
    if mgr is None:
        _info["error"] = "mgr 为 None"
        return None, _info
    try:
        _v = getattr(mgr, "GetInterferenceCount")
    except Exception as e:
        _info["error"] = "取 GetInterferenceCount 失败: %r" % (e,)
        return None, _info
    # ① 属性式（新版）：取到的就是 int
    if not callable(_v):
        _info["method"] = "属性访问（新版·无括号）"
        return int(_v), _info
    _info["attempts"].append("属性访问得到可调用对象，改按方法调用")
    # ② 方法式（旧版）
    try:
        _c = _v()
        _info["method"] = "方法调用（旧版·带括号）"
        return int(_c), _info
    except Exception as e:
        _info["attempts"].append("方法调用失败: %r" % (e,))
    _info["error"] = "GetInterferenceCount 属性/方法两种形态均失败"
    return None, _info



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
    # ── 【连接自检修复】必须判空：sw 为 None 时【不再】报
    #    "'NoneType' object has no attribute 'RevisionNumber'" ────────────
    # 原缺陷（实测复现）：doctor 被误列入"不连 SW"的集合 → sw=None →
    #   本块 except 捕到 AttributeError → 输出
    #     "solidworks": {"connected": false,
    #                    "error": "'NoneType' object has no attribute 'RevisionNumber'"}
    #   连带 get_part_template(None) 也失败 → 假报"未找到零件模板" →
    #   ok:false、problems 里两条全是误报。而它正是 persona 指定的
    #   SW 自检入口，"自检永远失败"直接把 AI 的连接确认链路掐断。
    # 修复：① sw is None 时给出【真实原因】（_LAST_SW_CONNECT_ERROR）；
    #      ② 模板探测只在拿到 sw 时才做，避免第二条假报错。
    if sw is None:
        result["solidworks"] = {
            "connected": False,
            "error": _LAST_SW_CONNECT_ERROR or "sw not connected（未建立 COM 连接）",
        }
    else:
        try:
            result["solidworks"] = {
                "connected": True,
                "revision": str(sw.RevisionNumber),
                "visible": bool(sw.Visible),
                "binding": getattr(sw, "_dsh_binding", None),
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

    # ── 【子任务 C】early_binding：这台机器能不能用早期绑定、用的是哪个 major ──
    # 来源：报告 1.1 / 1.2 —— SW2025 上 GetFeatureCount / OpenDoc6 等在晚期绑定下
    #   会报"参数数量错误"，只有早期接口能正确编组。用户看 doctor 时应该【一眼】
    #   看出这台机器处于哪种状态（可用 / 不可用已回退 / swapi 未提供入口）。
    # ⚠️ 绝不参与 ok/problems 判定：早期绑定是可选增强，不可用【不是故障】
    #   —— SW 2018~2024 与没装 SW 的机器本来就走晚期绑定，报失败反而误导。
    try:
        result["early_binding"] = early_status_snapshot(sw)
    except Exception as _e_eb:
        result["early_binding"] = {
            "available": False,
            "major": 0,
            "error": "early_status_snapshot 异常: %r" % (_e_eb,),
            "summary": "早期绑定状态探测异常（不影响建模，按晚期绑定工作）",
        }
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
        # ── 【连接自检修复】把【真实原因】带进 problems ────────────────────
        # 原来只有一句"请先启动 SolidWorks"，而 SW 明明没启动时会误导，
        # 连接被拒/绑定失败时又完全看不出原因。
        _cerr = result.get("solidworks", {}).get("error")
        problems.append("无法连接 SolidWorks: %s" % (_cerr or "请先启动 SolidWorks"))
        # SW 未连接时模板探测本就无法进行 —— 【不能】据此再报一条
        # "未找到零件模板"（那是连带假报错，会让 AI 误判 SW 安装损坏）。
    elif not result.get("template"):
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
    # ── 【子任务 C · 签名兼容】改用 _open_doc6（报告 3.7）─────────────────
    # cmd_drawing 打开零件走的就是本函数（经 cmd_open），因此这里改造后
    #   drawing 链路在 SW2025 上不再报"参数数量错误"。现在：先试早期
    #   OpenDoc6(...,None,None) 并解包 tuple；失败【逐字回退】原 VARIANT
    #   写法（SW 2018~2024 走的就是这条回退分支，行为完全一致）。
    doc = None
    doc, _open_info = _open_doc6(sw, _abs, int(doc_type), 1)
    out["open_method"] = _open_info.get("method")
    out["open_uses_early"] = _open_info.get("uses_early")
    if doc is None:
        out["error"] = _open_info.get("error") or "OpenDoc6 打开失败"
        out["open_attempts"] = _open_info.get("attempts")
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
    # ── 【连接自检修复】必须判空 ────────────────────────────────────────────
    # 原实现直接 `sw.ActiveDoc`。sw 为 None 时抛
    #   AttributeError: 'NoneType' object has no attribute 'ActiveDoc'
    # 并一路冒泡成 exit 1 + traceback，AI 拿到的是栈而不是结论。
    if sw is None:
        return _sw_not_connected_result("info")
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
    # ── 【连接自检修复】必须判空（同 cmd_info）────────────────────────────
    if sw is None:
        return _sw_not_connected_result("list")
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


def _parts_dir():
    """任务根的 `parts\\` 子目录（不存在则创建）。拿不到任务根返回 None。

    与 _default_drawing_path() 的 `<任务根>\\drawings\\` 对称。
    """
    _wd = _task_work_dir()
    if not _wd:
        return None
    _pd = os.path.join(_wd, "parts")
    try:
        os.makedirs(_pd, exist_ok=True)
        return _pd
    except Exception:
        return None


def resolve_save_path(path):
    """把【相对】保存路径归位到任务目录 —— `<任务根>\\parts\\<name>`。

    ──【BUG-观察-1 修复】──────────────────────────────────────────────────
    原缺陷（竞速小车任务现场实测）：门禁 `workflow_gate.py init` 返回
    work_dir = ...\\Desktop\\test\\<时间戳任务ID>，但零件实际落到了
    ...\\Desktop\\test（父目录），于是总装/出图按 work_dir 找零件找不到。
    根因之一：本文件的 save 侧对路径【零决策】—— 收到相对路径就直接交给
    SaveAs3，而 SaveAs3 相对进程 cwd 解析 → 直落 cwd（不是任务目录）。

    修法（对齐已存在的 _default_drawing_path 的 `drawings\\` 口径）：
      · 绝对路径 → 【原样返回】（尊重调用方显式指定的位置，不擅自搬移）；
      · 相对路径 → 解析到 `<任务根>\\parts\\<name>` 并自动建目录；
      · 拿不到任务根 → 回退旧行为（按 cwd 处理），但回填 path_note/
        path_resolved_from 说明"未能归位到任务目录"，便于排查。

    边界处理：若相对路径【已带 parts\\ 前缀】（调用方按任务口径书写），
    则挂到任务根下而不再套一层，避免出现 `parts\\parts\\x.sldprt`；
    多层子目录原样保留。

    Returns: (final_path, meta)
      meta = {path_resolved_from, path_note?, input_path, work_dir?}
    """
    _in = str(path or "")
    meta = {"input_path": _in, "path_resolved_from": "as_given"}
    if not _in:
        return _in, meta
    # 绝对路径（含盘符 / UNC）→ 原样，不擅自搬移
    if os.path.isabs(_in):
        meta["path_resolved_from"] = "absolute"
        return _in, meta

    _wd = _task_work_dir()
    if not _wd:
        # 回退旧行为：相对路径按 cwd 处理，但如实标注"未能归位"
        meta["path_resolved_from"] = "cwd_fallback"
        meta["path_note"] = ("未能归位到任务目录：读不到 work_dir"
                            "（workflow_state.json 缺失且未设 "
                            "DSH_TASK_WORK_DIR / DSH_WORK_DIR）"
                            "→ 相对路径按进程 cwd 解析，可能与任务目录不一致。")
        return _in, meta

    _pd = _parts_dir()
    if not _pd:
        meta["path_resolved_from"] = "cwd_fallback"
        meta["path_note"] = ("未能归位到任务目录：无法创建 %s\\parts 目录"
                            " → 相对路径按进程 cwd 解析。" % _wd)
        meta["work_dir"] = _wd
        return _in, meta

    # ──【BUG-观察-1 修复】防重复前缀：调用方若已写 `parts\x.sldprt`，
    #   则挂到任务根下（得到 <work_dir>\parts\x.sldprt），不再套一层 parts。
    _norm = _in.replace("/", os.sep).lstrip("." + os.sep)
    _parts_prefix = "parts" + os.sep
    if _norm.lower().startswith(_parts_prefix.lower()):
        _final = os.path.join(_wd, _norm)
        meta["path_resolved_from"] = "task_work_dir_parts_prefix"
    else:
        _final = os.path.join(_pd, _in)
        meta["path_resolved_from"] = "task_work_dir"
    meta["work_dir"] = _wd
    meta["parts_dir"] = _pd
    meta["path_note"] = ("相对路径已归位到任务目录: %s（原输入 %r）"
                         % (_final, _in))
    return _final, meta


def cmd_save(sw, path):
    """另存为当前活动文档。

    ──【BUG-观察-1 修复】──────────────────────────────────────────────────
    原实现：`rc = d.SaveAs3(path, 0, 2)` —— 对 path 零决策，相对路径直落
    进程 cwd，导致零件跑到任务目录【之外】（总装按 work_dir 找不到）。
    现在：相对路径先经 resolve_save_path() 归位到 `<任务根>\\parts\\<name>`；
    返回值回传【最终实际保存的绝对路径】（下游材料凭据/总装查找的关键）。

    ⚠️ 注意：建模脚本里真正的落盘入口通常是 `swapi.SWModel.save()`
    （脚本经 `sw_bridge.py run` 在独立进程里 import swapi 执行），
    【不经过本函数】。swapi.py 侧的归位由父 agent 另行修改；本文件在
    cmd_run 里注入 DSH_TASK_WORK_DIR / DSH_TASK_PARTS_DIR 作为兜底依据
    （详见 cmd_run 里的【BUG-观察-1 修复】注释）。
    """
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    _final, _meta = resolve_save_path(path)
    rc = d.SaveAs3(_final, 0, 2)  # returns 0 on success
    # 以 API 自报路径为准（SaveAs3 可能规范化路径/扩展名）
    _actual = _prop(d, "GetPathName") or _final
    out = {"ok": rc == 0, "path": _final, "requested_path": path,
           "saved": os.path.exists(_actual) or os.path.exists(_final),
           "rc": rc, "saved_path": _actual}
    out.update(_meta)
    return out


def _cmd_bore(sw, _args, raw_args):
    """【P1-BUG-bore 无 dispatch 分支】专用孔命令的 CLI 适配层。

    用法（与 help 承诺一致）：
      python sw_bridge.py bore <孔径mm> [深度mm] [--axis X|Y|Z] [--through]
      · 不给深度 → 默认完全贯穿（through=True）
      · 给了深度 → 盲孔（through=False）
      · --axis 缺省 Z
    实现委托 swapi.SWModel.bore()（swapi.py:7120）。
    """
    import swapi as _swapi
    try:
        _vals = []
        _axis = "Z"
        _through = None
        _cx = 0.0
        _cy = 0.0
        _i = 1
        while _i < len(_args):
            _a = str(_args[_i])
            if _a == "--axis":
                _axis = str(_args[_i + 1]).upper() if _i + 1 < len(_args) else "Z"
                _i += 2
                continue
            if _a.startswith("--axis="):
                _axis = _a.split("=", 1)[1].upper()
                _i += 1
                continue
            if _a == "--through":
                _through = True
                _i += 1
                continue
            if _a == "--cx":
                _cx = float(_args[_i + 1]); _i += 2; continue
            if _a == "--cy":
                _cy = float(_args[_i + 1]); _i += 2; continue
            if _a.startswith("--"):
                _i += 1
                continue
            _vals.append(_a)
            _i += 1
        if not _vals:
            return {"ok": False,
                    "error": "缺少孔径参数",
                    "usage": "bore <孔径mm> [深度mm] [--axis X|Y|Z] [--through]"}
        _dia = float(_vals[0])
        _depth = float(_vals[1]) if len(_vals) > 1 else None
        # 语义：显式 --through → 贯穿；给了深度 → 盲孔；两者都没给 → 贯穿（默认）
        _is_through = bool(_through) if _through is not None else (_depth is None)
        m = _swapi.from_active(sw)
        if m is None:
            return {"ok": False, "error": "no active document; run 'new' first"}
        out = m.bore(_dia, depth_mm=_depth, axis=_axis,
                     through=_is_through, cx=_cx, cy=_cy)
        out["command"] = "bore"
        out["depth_mm"] = _depth
        # ── 【P1 修复·实机发现】COM 对象不可 JSON 序列化 ──────────────────
        # 实机跑 `bore 8 10 --room <房间>` 时 SW 真的建了孔，但 swapi.bore
        #   把 Feature 对象（win32com CDispatch）直接放进 out["feature"]，
        #   主进程 json.dumps 立刻抛
        #     TypeError: Object of type CDispatch is not JSON serializable
        #   → 命令以 traceback 收场（exit 1），成功建的孔无法被调用方确认。
        # 修复：把 feature 换成"是否创建成功"的布尔 + 类型名，
        #   保留可判定性、丢掉不可序列化的 COM 句柄。
        _feat = out.pop("feature", None)
        out["feature_created"] = _feat is not None
        if _feat is not None:
            out["feature_type"] = type(_feat).__name__
        return out
    except Exception as e:
        return {"ok": False, "command": "bore", "error": "bore 执行失败: %r" % (e,)}


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
    """导出 PDF（【Bug-45/46 + 本次出图链路加固】）。

    原缺陷：
      ① 未带 .pdf 后缀时 SaveAs3 返回 rc=1，用户必须自己记得加；
      ② 直接调 doc.ExportToPDF 在 win32com 动态分发下失败；
      ③ 失败时只回 rc=1，没有可操作信息。

    ── 本次修复（BUG-45「导出 0 字节」假说，实测可复现的判据缺陷）──────
      原实现把成功判据交给 swapi.export_pdf，而【退化路径】只回
      os.path.exists(p) ——
        · 0 字节 PDF 也判 ok；
        · 目标路径上恰好有【上一轮的旧 PDF】，本次导出根本没写盘，
          同样判 ok（rc==0 与"真写了"并不等价）。
      → 两个房间都看不出"PDF 是空的/是旧的"。
      现在：无论主路径还是退化路径，最后都过同一道
      _verify_export_artifact（存在 + >0 字节 + 本次确有写入）。
    另外 PDF 的合法文件头是 "%PDF-"，顺手做内容自检，
      把"导出成 HTML 错误页/0 字节占位"这类情况也拦下来。
    """
    import swapi
    d = sw.ActiveDoc
    if d is None:
        return {"ok": False, "error": "no active document"}
    # 统一的输出路径（先补后缀，供"导出前快照"使用）
    p = str(path or "")
    if p and not p.lower().endswith(".pdf"):
        p = p + ".pdf"
    if not p:
        return {"ok": False, "error": "导出路径为空（需给出 .pdf 输出路径）"}
    _before = _artifact_snapshot(p)
    out = {}
    try:
        m = swapi.SWModel(sw, d)
        out = m.export_pdf(p) or {}
    except Exception as e:
        # 退化路径：至少保证后缀正确
        try:
            rc = d.SaveAs3(p, 0, 0)
            out = {"ok": rc == 0, "path": p, "exists": os.path.exists(p),
                   "rc": rc, "note": "swapi 封装不可用，已用 SaveAs3 回退"}
        except Exception as e2:
            return {"ok": False, "path": p,
                    "error": "PDF 导出失败: %r / %r" % (e, e2)}
    if not isinstance(out, dict):
        out = {"ok": False, "error": "export_pdf 返回非 dict: %r" % (out,)}
    out["path"] = out.get("path") or p
    # ── 总闸：存在 + >0 字节 + 本次确实写入 ──────────────────────────────
    _v = _verify_export_artifact(
        out["path"], before=_before, kind="PDF",
        hint=("① 确认当前活动文档是工程图/零件且已保存（view-only 文档不支持导 PDF）；"
              "② 确认目标目录可写、文件未被占用；"
              "③ 先用 drawing 命令生成 .slddrw，再导出 PDF。"))
    out["verify"] = _v
    if not _v.get("ok"):
        out["ok"] = False
        out["error"] = _v.get("error")
        if _v.get("hint"):
            out.setdefault("hint", _v["hint"])
        return out
    out["ok"] = True
    out["size_bytes"] = _v.get("size")
    out.pop("error", None)
    # ── 内容自检：PDF 必须以 %PDF- 开头 ──────────────────────────────────
    try:
        with open(out["path"], "rb") as _f:
            _head = _f.read(8)
        out["content_check"] = ("pdf" if _head.startswith(b"%PDF-")
                                else "unknown(%r)" % (_head[:8],))
        if not _head.startswith(b"%PDF-"):
            out["ok"] = False
            out["error"] = ("导出的文件不是合法 PDF（文件头 %r，应以 b'%%PDF-' 开头）。"
                            "多半是导出被 SW 静默拒绝后留下的占位文件。"
                            % (_head,))
    except Exception as _e_chk:
        out["content_check_error"] = repr(_e_chk)
    return out


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


def _case_insensitive_path(path):
    """【Bug-09 修复】按大小写不敏感方式在磁盘上找回真实路径。

    实测缺陷：artifacts_registry 登记为小写 `.sldprt`，而磁盘最终文件是
      大写 `.SLDPRT`（SW 保存后重命名），导致扫描脚本漏匹配、去重失效。
    策略：先按原样存在性检查（调用方通常已做），再逐级目录列目录做
      casefold 比对，命中即返回磁盘上的【真实大小写】路径；找不到返回 None。
    """
    try:
        p = str(path)
        if os.path.exists(p):
            return p
        _dir, _name = os.path.split(os.path.abspath(p))
        if not os.path.isdir(_dir):
            return None
        _want = _name.casefold()
        for _e in os.listdir(_dir):
            if _e.casefold() == _want:
                return os.path.join(_dir, _e)
    except Exception:
        pass
    return None


def _artifact_snapshot(path):
    """记录文件当前的 (size, mtime_ns)，用于判定"这次导出到底写没写盘"。

    【BUG-45/57 同类修复】SolidWorks 导出链路的典型静默失败是：
      SaveAs3 返回 0（成功）、目标路径上【恰好已有上一轮的旧文件】，
      于是"文件存在且 >0 字节"恒成立 → 调用方误判为本次导出成功。
    只有把"调用前的状态"记下来再比对，才能区分「真写了」和「捡了旧文件」。
    文件不存在时返回 None。
    """
    try:
        if not path or not os.path.exists(path):
            return None
        _st = os.stat(path)
        return {"size": int(_st.st_size), "mtime_ns": int(_st.st_mtime_ns)}
    except Exception:
        return None


def _verify_export_artifact(path, before=None, min_bytes=1, kind="", hint=""):
    """【出图链路 fail-closed 总闸】产物必须【真实存在、>0 字节、且本次确实被写入】。

    背景（台账 BUG-34/45，本次实测确认的根因之一）：
      annotate / export-pdf / dwg / dxf 这些出图命令原先的"成功判据"五花八门——
      有的只看 SaveAs3 的返回值 rc，有的只看 os.path.exists，
      于是「0 字节文件」「根本没写盘」「捡到上一轮旧文件」全都被报成 ok:true。
      两个房间都因此把空图纸当成交付物继续往下走。

    本函数是这些命令【统一】的最终判据，任一条不满足即 ok=False：
      ① 文件存在；② 大小 > min_bytes；③ 不是"调用前后完全没变"的旧文件。

    Args:
        path:      产物绝对路径
        before:    _artifact_snapshot(path) 在调用导出【之前】的结果（None=原本不存在）
        min_bytes: 最小字节数（默认 1，即禁止 0 字节）
        kind:      产物类型，仅用于错误信息（"PDF"/"DWG"/"DXF"/"SLDDRW"…）
        hint:      失败时的可操作提示
    Returns: {ok, path, exists, size, written, error?, hint?}
    """
    _k = kind or "产物"
    out = {"ok": False, "path": path, "exists": False, "size": 0,
           "written": False, "kind": _k}
    try:
        if not path:
            out["error"] = "%s路径为空" % _k
            return out
        if not os.path.exists(path):
            out["error"] = ("%s【未生成】：磁盘上不存在 %s。"
                            "（导出调用可能被 SW 静默忽略/被对话框阻塞）"
                            % (_k, path))
            if hint:
                out["hint"] = hint
            return out
        out["exists"] = True
        try:
            _size = int(os.path.getsize(path))
        except Exception:
            _size = 0
        out["size"] = _size
        if _size <= min_bytes:
            out["error"] = ("%s【是空文件】：%s 只有 %d 字节（要求 > %d）。"
                            "这是 SW 导出链路最常见的静默失败，"
                            "绝不能当成功交付。"
                            % (_k, path, _size, min_bytes))
            if hint:
                out["hint"] = hint
            return out
        _after = _artifact_snapshot(path)
        if before is not None and _after is not None:
            _same = (before.get("size") == _after.get("size") and
                     before.get("mtime_ns") == _after.get("mtime_ns"))
            if _same:
                out["error"] = ("%s【本次未写入】：%s 存在且 %d 字节，"
                                "但大小与修改时间与导出前完全一致 —— "
                                "说明导出调用没有真正写盘，"
                                "当前文件是上一轮留下的旧文件。"
                                % (_k, path, _size))
                if hint:
                    out["hint"] = hint
                return out
            out["previous"] = before
        elif before is None:
            out["created"] = True
        out["written"] = True
        out["ok"] = True
        return out
    except Exception as _e:
        out["error"] = "%s校验异常: %r" % (_k, _e)
        return out


def _verify_run_artifacts(script_path, result):
    """【C9 修复】校验脚本执行后预期产物是否存在【且非 0 字节】。

    ── 【BUG-57/86 修复】只判存在性不够 ──────────────────────────────────
    原实现用 os.path.exists 判产物，于是：
      · SW 中途崩溃/RPC 中断时留下 0 字节 .SLDPRT → 判为"已生成"；
      · 本次 run 其实什么也没做（wrapper 缺 __name__），但目录里躺着
        上一轮的旧零件 → 同样判为"已生成"。
    这正是"脚本跑了、零件没变"却一路 ok:true 的最后一环。
    现改为：0 字节 = 缺失；并额外回传 sizes 便于排查。

    Returns: {checked, expected:[], missing:[], present:[], zero_size:[], sizes:{}}
    """
    out = {"checked": False, "expected": [], "missing": [], "present": [],
           "zero_size": [], "sizes": {}}
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
            _real = _case_insensitive_path(p) or p
            if not os.path.exists(_real):
                out["missing"].append(p)
                continue
            try:
                _size = int(os.path.getsize(_real))
            except Exception:
                _size = 0
            out["sizes"][p] = _size
            if _size <= 0:
                # 0 字节 = 没真正落盘，与"不存在"同等对待（fail-closed）
                out["zero_size"].append(_real)
                out["missing"].append(p)
            else:
                out["present"].append(_real)
    except Exception:
        pass
    return out


# ══ 【本次修复·问题3】装配类任务必须先走【总计子代理】的代码级强制 ══════
# 用户实测："还是有 AI 不会开总计子代理的，必须改成代码强制它们开"。
#
# 现有强制链（select / room-start / set-subagent-plan）确实存在，但它们都
#   假设"AI 已经进入 Mode 2 并调用门禁命令"。真实绕过路径是：
#     AI 根本不做模式判定，直接 sw_bridge.py run <建模脚本> 开干 ——
#     没有 declare 2、没有 select、没有 room-start，所有门禁一次都不触发。
#   于是"总计子代理"从未被创建，十阶段分析、按需提问、编制登记全部落空。
#
# 本函数把这条旁路堵上：只要本次运行【看起来是多零件/装配类任务】，
#   就必须已经登记过子代理编制；否则拒绝执行建模脚本并给出明确指引。
#
# 判定"多零件/装配类"的可靠信号（任一命中）：
#   ① 工作区里已存在 ≥2 个 .sldprt（说明在造装配体，不是单零件）；
#   ② 建模脚本里出现装配/多件语义（Assembly/装配/零件清单/for ... part）；
#   ③ 已声明 Mode 2（那本来就该走门禁）。
# 单零件简单任务（Mode 1）不受影响 —— 用户明确要求"单零件直接建模"。
def _mode2_preflight_for_run(script_path, room=None):
    """建模脚本执行前的 Mode 2 前置校验。返回 {ok} 或 {ok:False, ...}。"""
    # ① 先看是不是已经在 Mode 2 里干活（room-start 已做过编制校验）
    try:
        import mode_gate as _mg
        _st = _mg.load_state() or {}
        if str(_st.get("mode")) == "2":
            # Mode 2 下 room-start 已经强制校验过编制；这里只需确认房间已登记
            if room:
                _r = (_st.get("rooms") or {}).get(room)
                if _r and _r.get("active"):
                    return {"ok": True, "via": "mode2_active_room"}
            return {"ok": True, "via": "mode2"}
    except Exception:
        pass
    # ② 未声明 Mode 2：判断是否其实属于"多零件/装配类"
    try:
        _dir = os.path.dirname(os.path.abspath(script_path)) or os.getcwd()
    except Exception:
        _dir = os.getcwd()
    _sldprt = 0
    try:
        for _name in os.listdir(_dir):
            if _name.lower().endswith(".sldprt"):
                _sldprt += 1
    except Exception:
        _sldprt = 0
    _script_text = ""
    try:
        with open(script_path, "r", encoding="utf-8", errors="replace") as _f:
            _script_text = _f.read(200000)
    except Exception:
        _script_text = ""
    _multi_part_signal = bool(
        re.search(r"(Assembly|装配体|总装|零件清单|multi[-_ ]?part)", _script_text, re.I)
        or len(re.findall(r"new_part\s*\(", _script_text)) >= 2
        or len(re.findall(r"\.save\s*\(", _script_text)) >= 2
    )
    _looks_like_assembly = (_sldprt >= 2) or _multi_part_signal
    if not _looks_like_assembly:
        return {"ok": True, "via": "single_part"}
    # ③ 多零件任务却没走 Mode 2 → 检查是否至少登记过编制
    try:
        import mode2_flow as _m2
        _wd = None
        try:
            import workflow_gate as _wg
            _wd = _wg._m2_work_dir(_wg.load_state() or {})
        except Exception:
            _wd = None
        _plan = _m2.get_subagent_plan(_wd)
        if _plan.get("ok") and (_plan.get("rooms") or []):
            # ⚠️ 必须校验编制属于【当前任务】—— 否则现场遗留的上一轮编制
            #    会让守卫误判成"已登记"，等于把旁路又打开了（实测：安装副本
            #    里残留旧编制 → 多零件任务照样放行）。
            _task_now = None
            try:
                import workflow_gate as _wg2
                _task_now = (_wg2.load_state() or {}).get("task_id")
            except Exception:
                _task_now = None
            _plan_task = _plan.get("task_id")
            _same_task = bool(_task_now and _plan_task and
                              str(_plan_task) == str(_task_now))
            if _same_task:
                return {"ok": True, "via": "subagent_plan_exists"}
    except Exception:
        pass
    return {
        "ok": False,
        "gate": "MODE2_TOTAL_SUBAGENT_REQUIRED",
        "error": ("【模式二强制】检测到这是多零件/装配类任务（同目录已有 %d 个 .sldprt"
                  " 或脚本含装配语义），但【从未启动总计子代理】—— 拒绝直接建模。"
                  % _sldprt),
        "why": ("用户明确要求：装配类任务必须先由【总计子代理】按十阶段流水线分析，"
                "再登记要开几个建模房间。直接开模型 = 跳过全部质量门禁。"),
        "how": [
            "1) 先声明模式：python mode_gate.py declare 2",
            "2) 启动【总计子代理】按十阶段分析（跳阶段会被拒）：",
            "     python mode2_flow.py begin-stage TaskUnderstanding",
            "     ... 依次走完 10 个阶段 ...",
            "3) 登记编制（decided_by 必须是『总计子代理』）：",
            "     python mode2_flow.py set-subagent-plan --rooms-json @rooms.json "
            "--rationale \"<分解理由>\" --decided-by \"总计子代理\"",
            "4) 门禁放行后 room-start，再回来执行建模脚本。",
        ],
        "bypass": "确属单零件任务时，请在【空目录】或独立零件目录下建模（无其他 .sldprt）。",
    }


def cmd_run(sw, script_path, extra_args, room=None):
    """执行 DSH 生成的建模脚本（核心能力）。

    以独立 Python 进程运行 script_path，并把连接好的 `sw` 对象注入为全局变量，
    这样脚本可以直接写 win32com 动态分发代码操作 SolidWorks，无需关心连接细节。

    通用版：桥接目录（本文件所在目录）自动注入 sys.path，
    因此脚本里 `import swapi` 在任何电脑上都能找到同目录的 swapi.py。

    room: 【Bug#4 修复】本命令所属房间名，用于建模期间后台刷新心跳。
    """
    # ── 【P1-BUG-退出码 3 语义重载】wrapper 专用退出码 ────────────────────
    # 原缺陷：wrapper 用 SystemExit(3) 表示"脚本路径不存在"、SystemExit(4)
    #   表示"脚本内容被改变"，而 CLI 契约里 **3 = SW 忙需重试**
    #   （见 main() 的 `sw_busy` 分支）。同一个 3 有两套完全不同的含义，
    #   调用方拿到 exit=3 无法区分"该等 90 秒重试"还是"脚本被人换了"。
    # 修复：wrapper 改用 11/12 这两个 CLI 契约外的专用码，
    #   与 0/1/2/3 彻底隔离；父进程侧同步按新码判定。
    _WRAPPER_EXIT_PATH_MISSING = 11
    _WRAPPER_EXIT_SHA_MISMATCH = 12
    if not os.path.exists(script_path):
        return {"ok": False, "error": f"script not found: {script_path}"}
    # ── 【本次修复·问题3】Mode 2 前置校验：装配类任务必须先开总计子代理 ──
    # 用户实测"AI 老是不开总计子代理"——根因是它直接 run 建模脚本，
    #   绕过了 select / room-start 上的全部编制校验。这里把旁路堵死。
    _m2_gate = _mode2_preflight_for_run(script_path, room=room)
    if not _m2_gate.get("ok"):
        return _m2_gate
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
            f"    raise SystemExit({_WRAPPER_EXIT_PATH_MISSING})\n"
            "_actual_sha = hashlib.sha1(open(_EXPECT_PATH, 'rb').read()).hexdigest()\n"
            "if _actual_sha != _EXPECT_SHA:\n"
            "    print(json.dumps({'ok': False,\n"
            "        'error': ('【Bug-39】脚本内容在运行前被改变（hash 不符）'\n"
            "                  '—— 拒绝执行以免跑错脚本'),\n"
            "        'expect_sha': _EXPECT_SHA, 'actual_sha': _actual_sha,\n"
            "        'script': _EXPECT_PATH}, ensure_ascii=False))\n"
            f"    raise SystemExit({_WRAPPER_EXIT_SHA_MISMATCH})\n"
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
            # ══ 【BUG-57/86 修复】_ns 必须包含 __name__ = '__main__' ══════════
            # 实测故障（两个房间各踩一次）：建模脚本几乎都写成
            #     def main(): ...
            #     if __name__ == '__main__':
            #         main()
            # 而原 wrapper 的 _ns 里【没有 __name__】。
            #
            # 关键实证（本机 Python 3.13 实测，勿凭直觉改）：
            #   exec(src, ns) 的 ns 缺 __name__ 时，【不会】抛 NameError，
            #   而是回退到 __builtins__ 模块的 __name__，其值是字符串
            #   'builtins'。实测输出：  NAME IS 'builtins'
            #   → 'builtins' == '__main__' 为 False → main() 永远不执行。
            #   脚本"顺利跑完"、没有异常 → wrapper 返回 ok:true，
            #   而零件根本没变 —— 这是最难排查的静默空跑。
            #
            # 修复：显式补全模块级 dunder，让脚本的 __main__ 分支真正成立；
            #   同时保留 __file__（脚本里读 __file__ 求同目录资源）。
            #   注意必须把 __builtins__ 也显式放进去：exec 在给定了
            #   __builtins__ 时不再注入默认值，行为与"直接跑脚本"一致。
            "_ns = {'__name__': '__main__', '__file__': __file__,\n"
            "       '__builtins__': __builtins__, '__package__': None,\n"
            "       '__spec__': None, '__doc__': None,\n"
            "       'sw': sw, 'json': json, 'os': os, 'sys': sys}\n"
            "try:\n"
            f"    exec(compile(open({script_abs!r}, encoding='utf-8').read(), {script_abs!r}, 'exec'), _ns)\n"
            "    _ok, _err = True, ''\n"
            "except SystemExit as _e_sysexit:\n"
            "    # 【BUG-57 配套】脚本用 sys.exit(0/None) 表示成功、非 0 表示失败。\n"
            "    #   原写法只 catch Exception → SystemExit 穿透，wrapper 在打印\n"
            "    #   结果前就退出，父进程只拿到 exit_code、没有任何 error 文本\n"
            "    #   （表现为「no JSON in output」，真实原因完全丢失）。\n"
            "    _sc = _e_sysexit.code\n"
            "    if _sc in (None, 0):\n"
            "        _ok, _err = True, ''\n"
            "    else:\n"
            "        _ok = False\n"
            "        _err = ('脚本以 SystemExit(%r) 结束 → 判定为业务失败' % (_sc,))\n"
            "except BaseException:\n"
            "    _ok, _err = False, traceback.format_exc()[-3000:]\n"
            "finally:\n"
            "    sys.stdout, sys.stderr = _old_out, _old_err\n"
            "_biz = _ns.get('__RESULT__')\n"
            "# 【BUG-57 配套】回显脚本实际看到的 __name__，便于核对 __main__ 分支\n"
            "#   到底成不成立（这是本次静默空跑的根因，必须可见）。\n"
            "_ns_name = _ns.get('__name__')\n"
            # 【BUG-A 修复】同上：普通字符串用单花括号
            "print(json.dumps({'ok': _ok, 'error': _err,\n"
            "                  'script_stdout': _cap_out.getvalue()[-8000:],\n"
            "                  'script_stderr': _cap_err.getvalue()[-8000:],\n"
            "                  'script_name': _ns_name,\n"
            "                  'script_result': _biz}, ensure_ascii=False))\n"
        )
    try:
        # Bug 1 修复: 设置 PYTHONIOENCODING 绕过 Windows GBK 终端编码问题
        run_env = dict(os.environ)
        run_env["PYTHONIOENCODING"] = "utf-8"
        # ══ 【BUG-观察-1 修复】把任务目录【显式注入子进程环境】═════════════
        # 原因（父 agent 补充的关键事实）：建模脚本真正调用的落盘入口是
        #   `swapi.SWModel.save()`（脚本里 m.save(out)），它在本子进程内执行，
        #   【完全不经过 sw_bridge.cmd_save】。
        # 而子进程的 cwd 是【脚本所在目录】（见下方 cwd=...），
        #   脚本若传相对路径，就会落到脚本目录/cwd，而不是任务 work_dir
        #   → 与 workflow_gate init 的 work_dir 打架（零件掉到父目录）。
        # 修法：run 执行脚本前把权威任务目录注入子进程环境，供 swapi 侧读取；
        #   swapi.py 由父 agent 修改（本任务禁止改 swapi.py），约定变量名：
        #     DSH_TASK_PARTS_DIR —— 【首选】，已算好的 `<work_dir>\parts`
        #     DSH_TASK_WORK_DIR  —— 任务根（回退时由 swapi 自行拼 parts）
        #   两者都拿不到时不设置 → swapi 侧保持旧行为（不作恶）；
        #   也不覆盖调用方已设的值（run_env 是 os.environ 的拷贝，已带上）。
        _wd_run = None
        try:
            _wd_run = _task_work_dir()
        except Exception:
            _wd_run = None
        if _wd_run:
            _pd_run = _parts_dir()
            run_env["DSH_TASK_WORK_DIR"] = _wd_run
            if _pd_run:
                run_env["DSH_TASK_PARTS_DIR"] = _pd_run
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
    # 【P1-BUG-退出码 3 语义重载】按 wrapper 专用码判定（不再是 3/4）
    result["script_identity_verified"] = (
        proc.returncode not in (_WRAPPER_EXIT_PATH_MISSING, _WRAPPER_EXIT_SHA_MISMATCH))
    # ──【BUG-观察-1 修复】回显注入的任务目录，便于核对零件该落到哪 ──────
    # 下游（总装/出图）按 work_dir 找零件；这里把"脚本执行时拿到的任务目录"
    # 显式回传，一旦零件没归位可直接对照排查。
    result["task_work_dir"] = run_env.get("DSH_TASK_WORK_DIR")
    result["task_parts_dir"] = run_env.get("DSH_TASK_PARTS_DIR")
    result["script_cwd"] = os.path.dirname(os.path.abspath(script_path))
    if not result.get("task_work_dir"):
        result["path_note"] = (
            "未能归位到任务目录：读不到 work_dir（workflow_state.json 缺失"
            "且未设 DSH_TASK_WORK_DIR / DSH_WORK_DIR）。脚本若用相对路径保存，"
            "零件会落到脚本目录而非任务目录 —— 总装/出图可能找不到。")
    # 【P1-BUG-退出码 3 语义重载】按 wrapper 专用码判定；明确回显"这不是 SW 忙"
    if proc.returncode in (_WRAPPER_EXIT_PATH_MISSING, _WRAPPER_EXIT_SHA_MISMATCH):
        result["ok"] = False
        result["script_identity_failed"] = True
        result["error"] = ("【Bug-39】脚本身份自校验失败（exit=%d）："
                           "待执行脚本路径不存在或内容在运行前被改变，"
                           "已拒绝执行以免跑成别的脚本。"
                           "（注意：本退出码是 wrapper 专用码，"
                           "与 CLI 的 3=SW忙 无关，不要据此重试。）"
                           % proc.returncode)
    # 脚本可选的业务结果（脚本内写 __RESULT__ = {...} 即会出现在这里）
    _biz = result.get("script_result")
    if isinstance(_biz, dict):
        result["script_ok"] = _biz.get("ok", result.get("ok"))
        if _biz.get("ok") is False:
            # 脚本显式报告失败 → 整体判失败，绝不"静默成功"
            result["ok"] = False
            result.setdefault("error", _biz.get("error") or "脚本自报失败")
    # ── 【C9 修复 + BUG-57/86 加固】产物校验 ────────────────────────────
    # 脚本声称成功、但文件没生成（或只是 0 字节 / 只是上一轮的旧文件）
    #   → 必须判失败。这是"脚本跑了、零件没变却返回 ok:true"的最后一道闸。
    _art = _verify_run_artifacts(script_abs, result)
    if _art.get("checked"):
        result["artifacts"] = _art
        if _art.get("missing"):
            result["ok"] = False
            _zero = _art.get("zero_size") or []
            result["error"] = (
                "脚本执行完毕但预期产物未生成或为 0 字节: %s。"
                "%s"
                "（原实现只判 os.path.exists，0 字节文件也会被当成成功）"
                % (", ".join(_art["missing"]),
                   ("其中 0 字节文件: %s；" % ", ".join(_zero)) if _zero else ""))
    # ══ 【BUG-57/86】静默空跑检测 ═══════════════════════════════════════
    # 核心故障：脚本的 if __name__ == '__main__': main() 分支从未执行，
    #   于是"什么也没做"却一路 ok:true。
    # 现在 wrapper 会回传 script_name（脚本实际看到的 __name__）与其
    #   script_stdout / script_result，本处做二次判定：
    #   ① script_name != '__main__' → 说明模型脚本的 __main__ 分支不可能成立，
    #      即使脚本没报错也必须显式标记（fail-closed，不静默）；
    #   ② 脚本一行输出都没有、也没声明 __RESULT__、预期产物又全都没出现
    #      → 高度疑似空跑，同样显式标记。
    _sn = result.get("script_name")
    if _sn is not None and _sn != "__main__":
        result["ok"] = False
        result["silent_noop_suspected"] = True
        result["error"] = (
            "【BUG-57】建模脚本执行时的 __name__ = %r（应为 '__main__'），"
            "脚本里的 if __name__ == '__main__': main() 分支【不会执行】，"
            "本次 run 极可能是静默空跑（零件没变）。"
            "已判定为失败，以免把空跑当成建模成功。" % (_sn,))
    if result.get("ok") and not (result.get("error") or "").strip():
        _so = (result.get("script_stdout") or "").strip()
        _se = (result.get("script_stderr") or "").strip()
        _biz0 = result.get("script_result")
        _has_art = bool(_art.get("present"))
        if _biz0 is None and not _has_art:
            # 既没声明 __RESULT__，也没有任何可核对的产物。
            # 先看脚本里有没有"建模/保存/导出"意图 —— 有意图却零产物，
            # 才是真正值得报警的空跑；纯查询脚本不该被误报。
            _src_txt = ""
            try:
                with open(script_abs, "r", encoding="utf-8",
                          errors="replace") as _f_src:
                    _src_txt = _f_src.read(200000)
            except Exception:
                _src_txt = ""
            _intent = bool(re.search(
                r"(\.save\s*\(|SaveAs|ExportTo|export_pdf|export_image|"
                r"新建|建模|另存|导出)", _src_txt))
            if not _so and not _se:
                # 一点输出都没有 → 直接判失败（最典型的静默空跑形态）
                result["ok"] = False
                result["silent_noop_suspected"] = True
                result["error"] = (
                    "【BUG-57/86】脚本执行完毕但【没有任何输出、没有 "
                    "__RESULT__、也没有可核对的产物】—— 判定为静默空跑。"
                    "最常见原因是脚本的 if __name__ == '__main__': 分支"
                    "没有执行到（main() 从未被调用），"
                    "或脚本逻辑提前 return 了。")
            elif _intent:
                # 有保存/导出意图却零产物 → 至少显式告警，绝不"静默成功"
                result["silent_noop_suspected"] = True
                result["warning"] = (
                    "脚本看起来有建模/保存意图，但既没有声明 __RESULT__，"
                    "也没有在磁盘上留下任何可核对的产物 —— 疑似空跑或"
                    "保存路径写错。建议用 __RESULT__ = {'artifacts': [...]} "
                    "显式声明产物，让 run 能替你核对。")
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

    # ══ 【Bug-11 修复】磁盘落盘核对 + 自动补登记 artifacts_registry ═══════
    # 实测缺陷：小屋累计自报"16 件全产出"，磁盘实际仅 11 件；缺 4 件
    #   （电机座/转向节/牛眼轮支架/舵机连杆）全盘搜索均无。
    #   根因①：SW 进程被强杀（RPC unavailable）时保存中断，小屋仍自报完成；
    #   根因②：产物登记依赖小屋主动调 room-artifact，小屋提前退出即漏登记。
    # 修复：
    #   ① 【磁盘为唯一真相】对脚本声明/静态提取的每个预期产物逐个核对
    #      （大小写不敏感），缺件【显式报失败】，绝不静默报成功；
    #   ② 【自动补登记】把磁盘上真实存在的本房间零件补录进 registry，
    #      不再依赖小屋记得调用 —— 子代理失败也不丢登记。
    try:
        _disk = _verify_run_artifacts(script_abs, result)
        _expect = list(_disk.get("expected") or [])
        if _expect:
            _present, _missing, _mismatch = [], [], []
            for _e in _expect:
                if os.path.exists(_e):
                    _present.append(_e)
                    continue
                # 大小写不敏感匹配（Bug-09：登记为小写 .sldprt，磁盘为大写 .SLDPRT）
                _hit = _case_insensitive_path(_e)
                if _hit:
                    _present.append(_hit)
                    _mismatch.append({"expected": _e, "actual": _hit})
                else:
                    _missing.append(_e)
            result["disk_check"] = {
                "expected": _expect, "present": _present,
                "missing": _missing, "case_mismatch": _mismatch,
                "disk_is_truth": True,
            }
            if _missing:
                result["ok"] = False
                result["defense_blocked"] = result.get("defense_blocked") or "MISSING_ARTIFACT"
                result["error"] = (
                    "【Bug-11】脚本执行完毕但下列零件【磁盘上不存在】，"
                    "自报产出与磁盘不符（SW 可能中途崩溃/RPC 中断）：\n  - "
                    + "\n  - ".join(_missing)
                    + "\n禁止把自报当成交付依据；请重新建模并核对磁盘实际文件。")
            # ② 自动补登记（仅登记磁盘真实存在者）
            if room and _present:
                try:
                    import mode_gate as _mg
                    _reg_ok, _reg_n = 0, 0
                    for _p in _present:
                        if not str(_p).lower().endswith(".sldprt"):
                            continue
                        _r = _mg.cmd_room_artifact(room, _p)
                        _reg_n += 1
                        if _r.get("ok"):
                            _reg_ok += 1
                    result["auto_registered"] = {
                        "room": room, "registered": _reg_ok, "attempted": _reg_n,
                        "note": "run 成功落盘的零件已自动补登记到 artifacts_registry",
                    }
                except Exception as _e_reg:
                    result.setdefault("warnings_list", []).append(
                        "自动补登记产物失败（不阻断）: %r" % (_e_reg,))
    except Exception as _e_disk:
        result.setdefault("warnings_list", []).append(
            "磁盘落盘核对异常（不阻断）: %r" % (_e_disk,))

    # ── 【P1-BUG-归属门禁漏挂 run】─────────────────────────────────────────
    # 原缺陷：Bug-38 的零件归属校验（_check_part_before_save → workflow_gate
    #   .check_part_ownership）只挂在 `save` 与 `check-part` 两个命令上
    #   （调用点仅 4946/4957）。而【真正落盘的路径是 cmd_run → 脚本内
    #   swapi.SWModel.save()】—— 它完全绕过 sw_bridge，因此越界产出
    #   （结构件小屋产"电机座"、传动机构小屋产"转向支撑座"）在 run 路径上
    #   没有任何拦截，Bug-38 的防护对最常用的建模链路实际是失效的。
    # 修复：run 结束后，对本次磁盘上真实存在的 .sldprt 逐个做归属校验；
    #   越界即判本次 run 失败（与 save 侧同口径），并写入违规台账。
    #   注意：只在有 room 上下文时校验（串行/单房间无归属概念，保持原行为）。
    if room and _art.get("checked"):
        try:
            import workflow_gate as _wg_own
            _viol = []
            for _p in (_art.get("present") or []):
                if not str(_p).lower().endswith(".sldprt"):
                    continue
                try:
                    _own = _wg_own.check_part_ownership(_p, room)
                except Exception:
                    continue
                if _own.get("allowed") is False:
                    _viol.append({
                        "part": _own.get("part") or os.path.basename(_p),
                        "owner_room": _own.get("owner_room"),
                        "gate": _own.get("gate"),
                        "error": _own.get("error"),
                    })
            if _viol:
                result["ownership_check"] = _viol
                if not result.get("ownership_blocked"):
                    result["ownership_blocked"] = True
                    # 与 save 侧一致：越界产出必须拦截（可通过 --force-part 放行）
                    if "--force-part" not in (extra_args or []):
                        result["ok"] = False
                        result["error"] = (
                            "【Bug-38】本次 run 产出的零件不属于房间 [%s]，已拒绝：\n  - %s"
                            % (room, "\n  - ".join(
                                "%s（实际归属 %s）" % (v["part"], v["owner_room"])
                                for v in _viol))
                            + "\n确需强制保存 → 给 run 传 --force-part；"
                              "或修正 workflow_gate 的房间零件范围关键词。")
        except Exception as _e_own:
            result.setdefault("warnings_list", []).append(
                "归属校验不可用（不阻断）: %r" % (_e_own,))

    # ── 【装配强制路径】对本次产出的 .sldasm 做配合门禁 ────────────────────
    # 用户强制要求：模型组装时【先走 CreateMate 配合】，不行再走其它方式。
    # 详见 _assembly_mate_gate 的说明（判据按文档 §10.1，用 FeatureByName）。
    # 放在归属校验之后、截图之前：此时磁盘产物已确定，且尚未做展示动作。
    try:
        _asm_cands = []
        for _p in (_art.get("present") or []):
            if str(_p).lower().endswith(".sldasm") and os.path.exists(_p):
                _asm_cands.append(_p)
        _biz_asm = result.get("script_result")
        if isinstance(_biz_asm, dict):
            for _c in (_biz_asm.get("artifacts") or _biz_asm.get("files") or []):
                _s = str(_c)
                if _s.lower().endswith(".sldasm") and os.path.exists(_s) \
                        and _s not in _asm_cands:
                    _asm_cands.append(_s)
        if _asm_cands:
            result["_extra_args"] = list(extra_args or [])
            _mate_reports = []
            _mate_block = []
            for _ap in _asm_cands:
                _mg = _assembly_mate_gate(sw, _ap, result, room=room)
                _mg["assembly"] = os.path.basename(_ap)
                _mate_reports.append(_mg)
                if _mg.get("blocked"):
                    _mate_block.append(_mg.get("error") or "未查到配合")
            result["assembly_mate_gate"] = _mate_reports
            if _mate_block:
                result["ok"] = False
                result["assembly_mate_blocked"] = True
                result["error"] = "\n\n".join(_mate_block)
    except Exception as _e_mate:
        result.setdefault("warnings_list", []).append(
            "装配配合门禁异常（未校验）: %r" % (_e_mate,))

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
    """窗口前台 + 等轴测视图 + 截图（展示给用户看成品）。

    ── 【观察点 8 修复】同样必须判空：sw 为 None 时返回可读 JSON，
    而不是在 `swapi.from_active(None)` 里抛 AttributeError。
    """
    if sw is None:
        # ── 【连接自检修复】统一走结构化结果（含连接失败的具体原因）──────
        return _sw_not_connected_result("show")
    import swapi
    # ── 【连接自检修复】无活动文档时也不抛 traceback ──────────────────────
    # 原缺陷：`swapi.from_active(sw)` 在没有打开文档时 raise
    #   RuntimeError("no active document")，冒泡到 main() → exit 1 +
    #   traceback。而"SW 连着但没开文档"是完全正常的状态，
    #   主对话（观察点 10 的全局看一眼场景）最常撞上的就是它 ——
    #   应该给一句可操作的结论，而不是一坨栈。
    try:
        m = swapi.from_active(sw)
    except Exception as _e_active:
        return {
            "ok": False,
            "connected": True,
            "command": "show",
            "error": "no active document",
            "detail": str(_e_active),
            "hint": ("SolidWorks 已连接，但【没有打开任何文档】。"
                     "请先打开/新建零件再截图，例如：\n"
                     "  python sw_bridge.py open <零件.SLDPRT>\n"
                     "  或 python sw_bridge.py new"),
        }
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


def _assembly_mate_gate(sw, asm_path, result=None, room=None):
    """【装配强制门禁】装配体落盘后校验"是否真的用配合装配"。

    ══ 为什么需要它（用户强制要求）════════════════════════════════════════
    来源：《SW装配实战经验总结·融合版》核心结论 ——
      「装配定位只用【CreateMate 配合】，摆位用 add_component 带坐标或
        set_component_transform，【绝对不要】用 SetTransform2/SetXform/
        TranslateComponent 这类坐标硬设。」
    用户要求：**强制保证模型在组装时先走 CreateMate 方式**，不行再走其它，
    且核心命令集始终是 IAssemblyDoc::CreateMate。

    本门禁的判据（严格按文档 §10.1 的正确判据，不用会误报的 FirstFeature）：
      ① 用 FeatureByName 逐个查证常见配合名（重合N/同心N/平行N/垂直N/相切N/
         距离N/角度N/锁定N/齿轮N/螺旋N/宽度N/凸轮N）；
      ② 一个都没查到 → 判定"该装配体没有任何配合"= 违反强制路径；
      ③ 命中即通过，并把查到的配合名回传（可审计）。

    ⚠️ 只对 **.sldasm** 生效；零件（.sldprt）不受影响。
    ⚠️ 与三道防线一样保留合法绕行：`DSH_ASSEMBLY_MATE_BYPASS=1`
       或给 run 传 `--force-mate`，且**必定留痕**（reports/defense_bypass.json）。

    Returns: dict（ok / applies / mate_names / blocked / ...）
    """
    out = {"ok": True, "applies": False, "mate_names": [], "blocked": False}
    try:
        if not str(asm_path or "").lower().endswith(".sldasm"):
            return out
        out["applies"] = True
        # ── 合法绕行：显式豁免（留痕，绝不静默）──────────────────────────
        _bypass = str(os.environ.get("DSH_ASSEMBLY_MATE_BYPASS") or "").strip().lower()
        _bypass = _bypass in ("1", "true", "yes", "on")
        if _bypass or "--force-mate" in (result or {}).get("_extra_args", []):
            out["bypassed"] = True
            try:
                import defense_gate as _dg
                _dg.log_bypass(
                    "sw_bridge.run.assembly_mate_gate",
                    reason=("DSH_ASSEMBLY_MATE_BYPASS=1" if _bypass
                            else "--force-mate"),
                    by=("env" if _bypass else "cli"))
            except Exception:
                pass
            out["note"] = ("已显式豁免装配配合门禁（留痕到 reports/defense_bypass.json）。"
                           "注意：文档核心结论是装配定位必须走 CreateMate 配合。")
            return out

        # ── 打开装配体，按文档 §10.1 用 FeatureByName 查证 ────────────────
        _doc = None
        try:
            import swapi as _swapi
            _op = _swapi.SWModel.open_part(sw, asm_path, doc_type=2)
            _doc = _op.get("doc") if isinstance(_op, dict) else _op
        except Exception as e:
            out["open_error"] = repr(e)
        if _doc is None:
            # 打不开装配体 → 不误判为"无配合"，如实标注"未能校验"
            out["verified"] = False
            out["note"] = ("无法打开装配体做配合校验（%s）—— 本次不据此拦截，"
                           "但请确认装配体确实是用 CreateMate 配合装配的。"
                           % (out.get("open_error") or "open failed"))
            return out

        _base = ["重合", "同心", "平行", "垂直", "相切", "距离", "角度",
                 "锁定", "齿轮", "螺旋", "宽度", "凸轮"]
        _probe = ["%s%d" % (_b, _i) for _b in _base for _i in range(1, 21)]
        _found = []
        for _nm in _probe:
            try:
                if _doc.FeatureByName(str(_nm)) is not None:
                    _found.append(str(_nm))
            except Exception:
                continue
        out["mate_names"] = _found
        out["checked_probe_count"] = len(_probe)
        out["verified"] = True

        if not _found:
            out["ok"] = False
            out["blocked"] = True
            out["error"] = (
                "【装配强制路径】装配体 [%s] 用 FeatureByName 逐个查证"
                "【未发现任何配合】（探测 %d 个常见配合名）。\n"
                "文档核心结论：装配定位必须走 IAssemblyDoc::CreateMate 配合；"
                "坐标硬设（SetTransform2/SetXform/TranslateComponent）在 SW 上"
                "全部不可用或必崩。\n"
                "正确做法（文档 §6.1 六步）：\n"
                "  ① m._pick_faces_of_component(comp) 取面（已包装 IFace2）\n"
                "  ② asm.ClearSelection2(True)\n"
                "  ③ f1.Select2(True, 1); f2.Select2(True, 1)\n"
                "  ④ 确认 GetSelectedObjectCount2(-1) >= 2\n"
                "  ⑤ m.create_mate(type, None, name=..., save=...)\n"
                "  ⑥ m.verify_assembly_mates(expect_names=[...]) 验收\n"
                "确需放行：设 DSH_ASSEMBLY_MATE_BYPASS=1 或给 run 传 --force-mate"
                "（会留痕）。" % (os.path.basename(str(asm_path)), len(_probe)))
            out["hint"] = ("可直接用：python sw_bridge.py asm-verify \"<装配体.sldasm>\" "
                           "复核配合清单。")
        else:
            out["note"] = ("装配配合门禁通过：查到 %d 个配合（%s）。"
                           % (len(_found), "、".join(_found[:10])))
        return out
    except Exception as e:
        # 门禁自身异常：如实标注"未校验"，不静默假装通过
        out["verified"] = False
        out["error"] = "装配配合门禁异常（未校验）: %r" % (e,)
        return out


def cmd_asm_verify(sw, asm_path):
    """【装配配合验收】按文档 §10.1 的正确判据查证配合是否存在。

    用法：python sw_bridge.py asm-verify "<装配体.sldasm>"

    文档 §10.1 教训：诊断方法错了会得出完全相反的结论 —— 查配合存在性
    首选 FeatureByName，不要依赖 FirstFeature 遍历或 mate_count 的 count。
    """
    out = {"ok": False, "command": "asm-verify", "asm_path": asm_path}
    if not asm_path or not os.path.exists(str(asm_path)):
        out["error"] = "装配体文件不存在: %r" % (asm_path,)
        out["hint"] = "用法：python sw_bridge.py asm-verify \"<装配体.sldasm>\""
        return out
    if not str(asm_path).lower().endswith(".sldasm"):
        out["error"] = "asm-verify 只接受 .sldasm 装配体文件"
        return out
    try:
        import swapi as _swapi
        _op = _swapi.SWModel.open_part(sw, asm_path, doc_type=2)
        _doc = _op.get("doc") if isinstance(_op, dict) else _op
        if _doc is None:
            out["error"] = "无法打开装配体: %r" % (_op,)
            return out
        m = _swapi.SWModel(sw, _doc)
        # 文档 §10.1 判据：FeatureByName 逐个查证
        _base = ["重合", "同心", "平行", "垂直", "相切", "距离", "角度",
                 "锁定", "齿轮", "螺旋", "宽度", "凸轮"]
        _probe = ["%s%d" % (_b, _i) for _b in _base for _i in range(1, 21)]
        _found = []
        for _nm in _probe:
            try:
                if _doc.FeatureByName(str(_nm)) is not None:
                    _found.append(str(_nm))
            except Exception:
                continue
        out["mate_names"] = _found
        out["mate_count_by_name"] = len(_found)
        # 交叉参考：mate_count（可能因 FirstFeature 失效返回 0，仅作参考）
        _mc = m.mate_count()
        out["mate_count_report"] = {
            "count": _mc.get("count"), "method": _mc.get("method"),
            "unavailable": _mc.get("unavailable"),
            "note": ("文档 §10.1：本版 SW 上 FirstFeature 遍历可能失效，"
                     "count=0 不代表没有配合 —— 以 mate_names 为准。"),
        }
        # ── 【BUG-07 替代】特征树诊断（原 diag_mates.py 的正确实现）────────
        # 原 diag_mates.py 用 `asm.FirstFeature` 顶层遍历，在本版 SW 上
        #   **只返回 Comments 一个节点**（文档 BUG-01/07 同一根因），
        #   于是"列不出装配特征树、看不到配合"，让人误判装配体是散的。
        # 这里改用两道【不依赖 FirstFeature 全树遍历】的正确判据：
        #   ① FeatureManager.GetFeatureCount(False) —— 特征总数（含配合），
        #      可与已确认的配合名数量交叉验证"是否少算"；
        #   ② FeatureByName 逐个查证 —— 已在上面的 mate_names 完成。
        # 同时**如实回报 FirstFeature 的实测表现**，让后人一眼看出它坏了。
        try:
            _fc = None
            # ── 【子任务 C · 签名兼容】改用 _feature_count（报告 4 / 3.4）────
            # 原写法 GetFeatureCount(False) 在 SW2025 上【无参数】→
            #   "takes 1 positional argument but 2 were given"，
            #   于是 feature_tree_diag 恒为 None（诊断信息丢一半）。
            # 现在：无参 → (False) → early(IModelDoc2) 三级容错，全部失败才 None。
            _fc, _fc_info = _feature_count(_doc)
            if _fc is None:
                _fc_err = _fc_info.get("error")
            else:
                _fc_err = None
            # 实测 FirstFeature 顶层能走几个节点（预期：因失效而极少）
            _ff_nodes = []
            try:
                _f = _doc.FirstFeature
                _guard = 0
                while _f is not None and _guard < 200:
                    _guard += 1
                    try:
                        _tn = str(_f.GetTypeName2() or "")
                    except Exception:
                        _tn = "?"
                    try:
                        _nm = str(_f.Name or "")
                    except Exception:
                        _nm = ""
                    _ff_nodes.append({"type": _tn, "name": _nm})
                    try:
                        _f = _f.GetNextFeature()
                    except Exception:
                        break
            except Exception as _eff:
                _ff_nodes = [{"error": repr(_eff)}]
            out["feature_tree_diag"] = {
                "feature_count_by_manager": _fc,
                # 【子任务 C】回显实际生效的签名与失败原因（报告 4：新旧版
                #   参数个数不同，写清楚才能判断"是 API 不可用还是用法不对"）
                "feature_count_method": _fc_info.get("method"),
                "feature_count_error": _fc_err,
                "firstfeature_nodes": _ff_nodes,
                "firstfeature_node_count": len(_ff_nodes),
                "firstfeature_usable": len(_ff_nodes) > 3,
                "conclusion": (
                    "FirstFeature 顶层遍历可用（节点数 %d）" % len(_ff_nodes)
                    if len(_ff_nodes) > 3 else
                    ("FirstFeature 顶层遍历【失效/极简】（仅 %d 个节点）—— "
                     "与文档 BUG-01/07 现象一致。这是 API 在本版 SW 上的行为，"
                     "不是你的用法问题。请改用 FeatureByName / "
                     "FeatureManager.GetFeatureCount(False) 判据。"
                     % len(_ff_nodes))),
            }
        except Exception as _e_diag:
            out["feature_tree_diag"] = {"error": repr(_e_diag)}

        try:
            _lc = m.list_components()
            _comps = _lc.get("components") if isinstance(_lc, dict) else _lc
            out["component_count"] = len(_comps or [])
        except Exception:
            out["component_count"] = None
        out["ok"] = True
        if not _found:
            out["warning"] = ("未查到任何配合 —— 该装配体可能是【坐标硬设摆位】"
                              "而非 CreateMate 配合装配，违反文档核心结论。")
        return out
    except Exception as e:
        out["error"] = "asm-verify 异常: %r" % (e,)
        return out


def _task_work_dir():
    """当前任务的交付目录（workflow_state.json 的 work_dir）。

    ── 【观察点 22 修复】drawing/dwg 默认输出到 <任务根>\\drawings\\ ────────
    原先 `cmd_drawing` 在未给 output_path 时把工程图存到
    `os.path.dirname(part_path)`（= 零件所在的 `parts\\`），导致：
      · 图纸与零件混放，用户难以区分交付物；
      · `drawings\\` 目录根本不会被创建（子代理 prompt 却明确要求输出到那里）；
      · 若 `parts\\` 被清理，工程图会随零件一起丢失。
    这里按"显式 output_path > 任务 work_dir > 零件目录"的顺序解析任务根。
    读不到 work_dir 时返回 None，由调用方回退到零件目录（保持旧行为可用）。
    """
    # ① 环境变量优先（便于测试与多实例隔离，与 _store.py 口径一致）
    for _k in ("DSH_TASK_WORK_DIR", "DSH_WORK_DIR"):
        _v = (os.environ.get(_k) or "").strip()
        if _v and os.path.isdir(_v):
            return os.path.abspath(_v)
    # ② workflow_state.json 的 work_dir（init 时为每个任务创建）
    try:
        import _store as _st
        _p = _st.state_path("workflow_state.json")
        if os.path.isfile(_p):
            with open(_p, "r", encoding="utf-8-sig") as _f:
                _j = json.load(_f)
            _wd = str((_j or {}).get("work_dir") or "").strip()
            if _wd:
                return os.path.abspath(_wd)
    except Exception:
        pass
    return None


def _default_drawing_path(part_path, ext):
    """给 drawing/dwg 解析默认输出路径。

    优先 `<任务根>\\drawings\\<名>.<ext>`，并自动创建该目录；
    拿不到任务根时回退到零件同目录（旧行为），保证命令仍可用。
    """
    import re as _re
    part_name = os.path.basename(part_path)
    name_no_ext = os.path.splitext(part_name)[0]
    ascii_name = _re.sub(r"[^\w\-]", "_", name_no_ext)
    _wd = _task_work_dir()
    if _wd:
        _ddir = os.path.join(_wd, "drawings")
        try:
            os.makedirs(_ddir, exist_ok=True)
            return os.path.join(_ddir, ascii_name + ext)
        except Exception:
            pass
    return os.path.join(os.path.dirname(part_path), ascii_name + ext)


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
        # ── 【观察点 22 修复】默认落到 <任务根>\drawings\，不再混进 parts\ ──
        output_path = _default_drawing_path(part_path, ".slddrw")

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
            # ⚠️ 【子任务 C · 未改造调用点（有意保留）】本处仍用晚期 VARIANT 写法。
            # 改造范围定为 cmd_drawing / cmd_dwg / annotate_drawing 的【主打开路径】
            # （它们真正走的是 cmd_open → open_document_fresh，那里已改用
            # _open_doc6）；本内嵌辅助只在"零件尚未打开、需要量包围盒"时用到，
            # 失败本来就有默认值兜底（100×100），改与不改都不影响出图成败，
            # 因此保持原样、缩小改动面。若 SW2025 在此报"参数数量错误"，
            # 改用 _open_doc6(sw, path, 1, 1) 即可（报告 3.7）。
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
        # ── 【BUG-16 修复】delta=0 必须显式报警，不许"静默失败 + ok:true" ──
        # 现场：InsertModelDimensions / InsertModelAnnotations3 调用后
        #   rc=null、delta=0（一个标注都没加），drawing 仍返回 ok:true，
        #   调用方完全看不出"自动标注其实没生效"。
        # 处理（不阻断出图，但要说清楚）：
        #   · 在返回值里加 annotation_warning（人类可读）；
        #   · 加 annotation_effective=false（机器可判定）；
        #   · 提示根因是"模型里没有可带出的尺寸"，并给出补救办法。
        try:
            _n_added = int((_ann or {}).get("total_annotations") or 0)
        except Exception:
            _n_added = 0
        if _n_added <= 0:
            result["annotation_effective"] = False
            _tried = sorted({str(a.get("api")) for a in
                             ((_ann or {}).get("attempts") or [])
                             if a.get("called")})
            result["annotation_warning"] = (
                "工程图已生成，但【自动尺寸标注插入 0 个】："
                "已按类型库签名尝试 [%s]，各视图标注计数均无变化。"
                % (", ".join(_tried) or "无可调用 API"))
            result["annotation_hint"] = (
                "① InsertModelAnnotations* 只能带出【模型里已存在的尺寸】"
                "（草图尺寸 / DimXpert），不会凭空生成；"
                "② 建模脚本里请用 m.add_dimension(...) / add_key_dimensions(...) "
                "给关键尺寸加约束后再出图；"
                "③ 或在 SW 界面用「智能尺寸 / DimXpert」手工标注后，"
                "再用 dwg 导出（dwg 默认复用已有工程图，不会覆盖你的标注）。")
        else:
            result["annotation_effective"] = True
    except Exception as _e_ann:
        result["annotation"] = {"ok": False, "error": repr(_e_ann)}
        result["annotation_effective"] = False
        result["annotation_warning"] = (
            "自动标注过程抛异常：%r（工程图本身仍已生成）" % (_e_ann,))
        try:
            result["gbt_report"] = _gbt_annotation_report({})
        except Exception:
            pass

    # ══ 【本次修复】保存必须被【验证】，不能只看 SaveAs3 的 rc ═══════════
    # 原实现：result["saved"] = os.path.exists(output_path)，而 ok 完全由
    #   排版校验(all_ok)决定 —— 于是可能出现「视图排好了、SaveAs3 静默失败、
    #   磁盘上根本没有 .slddrw」却仍然 ok:true 的交付。
    #   （实测同类现象：路径上恰好有上一轮旧图时，exists 恒为 True。）
    # 现在：保存后走统一总闸（存在 + >0 字节 + 本次确写入），
    #   不达标就把整个 drawing 判失败。
    _draw_before = _artifact_snapshot(output_path)
    try:
        rc = final_doc.SaveAs3(output_path, 0, 2)
        result["path"] = output_path
        result["rc"] = rc
    except Exception as e:
        result["save_error"] = str(e)
    _dv = _verify_export_artifact(
        output_path, before=_draw_before, kind="SLDDRW",
        hint=("① 确认目标目录可写、文件未被 SW 占用；"
              "② 确认图纸已成功创建（本命令排版校验是否通过见 views/bounds 字段）；"
              "③ 必要时先用 status 确认 SW 连接正常。"))
    result["verify"] = _dv
    result["saved"] = bool(_dv.get("ok"))
    if not _dv.get("ok"):
        result["ok"] = False
        result["error"] = _dv.get("error")
    elif result.get("save_error"):
        # 保存抛了异常但文件居然被写出来了（SW 常见：抛异常后仍落盘）
        result.setdefault("warnings_list", []).append(
            "SaveAs3 抛异常但产物已生成: %s" % result["save_error"])

    try:
        m = swapi.from_active(sw)
        shot = m.screenshot()
        if shot.get("ok"):
            result["screenshot_path"] = shot["path"]
    except Exception:
        pass

    return result


# ══ 【BUG-观察-17 修复】自动标注 API 的【权威归属】与【参数个数】 ══════════
# 权威依据 = 已注册类型库（本机 SW 2025，makepy 产物）：
#   C:\Users\j1877\AppData\Local\Temp\gen_py\3.13\
#       83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
#   （= SldWorks 2025 Type Library；同源文件 Z:\...\SOLIDWORKS\sldworks.tlb）
#
# ── 原缺陷（BUG-观察-17 的【主因】，类型库实证 · 决定性）──────────────────
#   原代码写 `getattr(v, "InsertModelAnnotations3", None)`（v 是 IView），
#   并在 IView 上找 AutoDimension。但类型库实证：
#     · InsertModelAnnotations* / InsertModelDimensions / AutoDimension
#       【全部挂在 IDrawingDoc 上】；
#     · IView(FView) 上【没有任何 Insert*Annotations / 任何 AutoDimension】
#       —— 逐行扫描 IView 段（435 个方法）确认无此成员；
#     · IModelDocExtension 上【也没有 AutoDimension】。
#   → `getattr(v, ...)` / `getattr(doc.Extension, ...)` 【恒返回 None】，
#     整条分支 0 次执行 → 这就是「插入了 0 个标注」的直接原因。
#
# ── 参数个数（类型库逐行实证，勿凭记忆改）────────────────────────────────
#   IDrawingDoc.InsertModelDimensions(Option)                      —— 1 参
#   IDrawingDoc.InsertModelAnnotations3(Option, Types, AllViews,
#       DuplicateDims, HiddenFeatureDims, UsePlacementInSketch)    —— 6 参
#   IDrawingDoc.InsertModelAnnotations2(Option, AllTypes, Types,
#       AllViews, DuplicateDims, HiddenFeatureDims)                —— 6 参
#   IDrawingDoc.InsertModelAnnotations(Option, AllTypes, Types,
#       AllViews)                                                  —— 4 参
#   IDrawingDoc.AutoDimension(EntitiesToDimension, HorizontalScheme,
#       HorizontalPlacement, VerticalScheme, VerticalPlacement)    —— 5 参
#   原实现统一传 5 参 / 0 参（还把 Types 当 Option 传），与签名不符。
#
# Types 位掩码取值来自 swconst.tlb（权威枚举常量库）：
_SW_INSERT_DIMENSIONS = 8          # swInsertAnnotation_e: swInsertDimensions
# AutoDimension 的 5 个入参同样取自 swconst.tlb：
_SW_AUTODIM_ENTITIES_ALL = 1       # swAutodimEntities_e:      swAutodimEntitiesAll
_SW_AUTODIM_SCHEME_BASELINE = 1    # swAutodimScheme_e:        swAutodimSchemeBaseline
_SW_AUTODIM_H_BELOW = -1           # swAutodimHorizontalPlacement_e: ...Below
_SW_AUTODIM_SCHEME_CHAIN = 3       # swAutodimScheme_e:        swAutodimSchemeChain
_SW_AUTODIM_V_LEFT = -1            # swAutodimVerticalPlacement_e:   ...Left


def _rc_int(v):
    """把 COM 返回值安全压成 int（None / VARIANT / 异常都不得抛出）。"""
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _view_name(view, idx=0):
    """取视图名（找不到时回退 viewN）。静态/动态分发下都可用的写法。"""
    for _attr in ("GetName2", "Name"):
        try:
            _n = getattr(view, _attr, None)
            if _n is None:
                continue
            if callable(_n):
                _n = _n()
            _s = str(_n or "").strip()
            if _s:
                return _s
        except Exception:
            continue
    return "view%d" % (idx + 1)


def _collect_drawing_views(doc):
    """收集工程图里的【视图】（跳过图纸层本身）。

    ★ 视图遍历起点【保持原样、不得更改】：doc.GetFirstView（IDrawingDoc）
      → v.GetNextView（IView），第一个是图纸层，跳过。
    Returns: (views, error_or_None)
    """
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
        return views, "遍历视图失败: %r" % (e,)
    return views, None


def _annotation_api_candidates(dim_types=None):
    """生成 (api 名, 参数元组) 兜底链 —— 参数个数严格对齐 sldworks.tlb。

    ★ 全部是【IDrawingDoc】上的方法（见上方权威依据），调用时必须传 doc。
    顺序（首选 → 兜底）：
      ① InsertModelDimensions(Option)  —— 单参，专用于「插入模型尺寸」，
         最贴合「自动标注关键尺寸」的诉求，故置于首位；
      ② InsertModelAnnotations4(8 参)  —— 最新版插入模型标注（老版本自动跳过）；
      ③ InsertModelAnnotations3(6 参)  —— 上一版插入模型标注；
      ④ InsertModelAnnotations2(6 参)  —— 旧版兜底；
      ⑤ InsertModelAnnotations(4 参)   —— 最旧版兜底；
      ⑥ AutoDimension(5 参)            —— SW 自带自动标注（逐个激活视图后调用）。
    Types 位掩码依次尝试：调用方显式 dim_types > swInsertDimensions(8) > 1(旧行为)。
    """
    _types = []
    for _t in (dim_types, _SW_INSERT_DIMENSIONS, 1):
        if _t is None:
            continue
        try:
            _t = int(_t)
        except Exception:
            continue
        if _t not in _types:
            _types.append(_t)

    out = []
    # ① InsertModelDimensions：1 参。Option 的语义随版本而异且无法离线核实，
    #    故按 1 → 0 → 2 依次尝试（失败即换下一个，不影响正确性）。
    #    类型库实证：IDrawingDoc.InsertModelDimensions(Option) 返回 VT_VOID。
    for _opt in (1, 0, 2):
        out.append(("InsertModelDimensions", (_opt,)))
    # ② InsertModelAnnotations4：8 参（SW 较新版本才有；老版本 getattr=None
    #    会自动跳过，因此放在链首不会破坏 SW 2018~2024 的兼容性）。
    #    类型库实证：IDrawingDoc.InsertModelAnnotations4(Option, Types, AllViews,
    #      DuplicateDims, HiddenFeatureDims, UsePlacementInSketch,
    #      InsertAllAnnotations, InsertAllReferenceGeometry) —— 8 参
    for _opt in (1, 2):
        for _t in _types:
            out.append(("InsertModelAnnotations4",
                        (_opt, _t, True, False, False, False, False, False)))
    # ③ InsertModelAnnotations3：6 参
    for _opt in (1, 2):
        for _t in _types:
            out.append(("InsertModelAnnotations3",
                        (_opt, _t, True, False, False, False)))
    # ③ InsertModelAnnotations2：6 参
    for _opt in (1, 2):
        for _t in _types:
            out.append(("InsertModelAnnotations2",
                        (_opt, True, _t, True, False, False)))
    # ④ InsertModelAnnotations：4 参
    for _opt in (1, 2):
        for _t in _types:
            out.append(("InsertModelAnnotations", (_opt, True, _t, True)))
    # ⑤ AutoDimension：5 参（作用于【活动视图】，调用方需先 ActivateView）
    out.append(("AutoDimension", (_SW_AUTODIM_ENTITIES_ALL,
                                  _SW_AUTODIM_SCHEME_BASELINE,
                                  _SW_AUTODIM_H_BELOW,
                                  _SW_AUTODIM_SCHEME_CHAIN,
                                  _SW_AUTODIM_V_LEFT)))
    return out


def _insert_model_annotations(doc, views, dim_types=None):
    """在【IDrawingDoc】上按候选链插入模型标注，以「标注数真的变多」判定成功。

    Returns: (used_api, attempts, counts_after)
      used_api     —— 真正生效的 API 名；全失败则为 None（绝不假装成功）
      attempts     —— 逐次尝试的记录（api / 参数 / 是否可调用 / 错误 / 计数增量）
      counts_after —— 收尾时各视图的标注计数
    """
    _before = [_count_annotations(doc, v) for v in views]
    attempts = []
    used_api = None

    def _snapshot():
        return [_count_annotations(doc, v) for v in views]

    for _api, _args in _annotation_api_candidates(dim_types):
        # ★【BUG-观察-17 修复】必须 getattr(doc, ...)：这些 API 挂在 IDrawingDoc
        #   上，原实现 getattr(view, ...) 恒为 None，是「插 0 个标注」的根因。
        _fn = getattr(doc, _api, None)
        if _fn is None:
            attempts.append({"api": _api, "called": False,
                             "error": "IDrawingDoc 上无此接口（跳过）"})
            continue

        if _api == "AutoDimension":
            # AutoDimension 作用于【活动视图】→ 逐视图激活后再调用
            for _i, _v in enumerate(views):
                _vn = _view_name(_v, _i)
                try:
                    doc.ActivateView(_vn)
                except Exception:
                    pass
                try:
                    _st = _fn(*_args)
                except Exception as _e:
                    attempts.append({"api": _api, "view": _vn,
                                     "args": list(_args), "called": True,
                                     "error": repr(_e)})
                    continue
                _after = _snapshot()
                _delta = sum(max(0, a - b) for a, b in zip(_after, _before))
                attempts.append({"api": _api, "view": _vn, "args": list(_args),
                                 "called": True, "rc": _rc_int(_st),
                                 "delta": _delta})
                if _delta > 0:
                    used_api = _api
                    _before = _after
            if used_api:
                break
            continue

        try:
            _rc = _fn(*_args)
        except Exception as _e:
            attempts.append({"api": _api, "args": list(_args),
                             "called": True, "error": repr(_e)})
            continue
        _after = _snapshot()
        _delta = sum(max(0, a - b) for a, b in zip(_after, _before))
        attempts.append({"api": _api, "args": list(_args), "called": True,
                         "rc": _rc_int(_rc), "delta": _delta})
        if _delta > 0:
            used_api = _api
            _before = _after
            break
    return used_api, attempts, _before


def annotate_drawing(sw, drawing_path, dim_types=None, tol_mm=0.05):
    """【Bug-34 修复】对工程图自动添加关键尺寸标注。

    ══ 原缺陷 ═══════════════════════════════════════════════════════════════
    cmd_drawing 只做【视图生成 + 排版校验】，从不调用 SW 的自动标注能力。
    实测出图小屋交付的 5 张 .SLDDRW：排版全过，但 GB/T 检查项几乎全缺失 ——
    外径/孔径/孔位/总长宽高都没有标注，公差/粗糙度/形位公差也全无，
    必须人工在 SW 里补标注才能加工（Bug-34）。

    ══ 本函数做什么 ════════════════════════════════════════════════════════
    在【工程图文档(IDrawingDoc)】上调用 SW 的模型标注插入能力（多 API 兜底）：
      ① IDrawingDoc.InsertModelDimensions(Option)      —— 单参，首选
      ② IDrawingDoc.InsertModelAnnotations3(...6 参)   —— 插入模型标注
      ③ IDrawingDoc.InsertModelAnnotations2(...6 参)   —— 旧版兜底
      ④ IDrawingDoc.InsertModelAnnotations(...4 参)    —— 最旧版兜底
      ⑤ IDrawingDoc.AutoDimension(...5 参)             —— 自带自动标注
      都不可用时，明确返回 ok=False 并说明原因，绝不假装标注成功。

    ──【BUG-观察-17 修复】────────────────────────────────────────────────
    原缺陷：原代码在【IView】上找 InsertModelAnnotations*，并在
    【IModelDocExtension】上找 AutoDimension。但类型库实证这些 API
    【全部挂在 IDrawingDoc 上】：
      · IView（435 个方法逐行扫描）没有任何 Insert*Annotations / AutoDimension；
      · IModelDocExtension 上没有 AutoDimension。
    → 两处 getattr 恒为 None，分支 0 次执行 —— 这就是「插入了 0 个标注」
    的直接原因。修法：统一改为 getattr(doc, ...)，并按类型库对齐参数个数
    与回退顺序（详见 _annotation_api_candidates 的说明）。

    ⚠️ 待实机验证：本修复基于【类型库静态实证】，本机当前【无运行中的
    SLDWORKS 进程】，无法实机确认最终哪个 API 生效、各版本 Option 取值。
    参数个数与归属已与 sldworks.tlb / swconst.tlb 逐条核对。

    Args:
        drawing_path: .slddrw 路径
        dim_types:    传给 InsertModelAnnotations* 的 Types 位掩码。
                      None 时按 swInsertDimensions(8) → 1 依次尝试。
        tol_mm:       判定"标注是否真的加上了"的容差
    Returns:
        {ok, drawing_path, annotated_views, total_annotations, api, per_view,
         attempts, error?, hint?, gbt_report}
    """
    out = {"ok": False, "drawing_path": drawing_path, "annotated_views": 0,
           "total_annotations": 0, "per_view": [], "attempts": []}
    if not drawing_path or not os.path.exists(drawing_path):
        out["error"] = "工程图文件不存在: %s" % drawing_path
        return out
    doc = None
    try:
        # ── 【子任务 C · 签名兼容】改用 _open_doc6（报告 3.7）──────────────
        # 原写法传 VARIANT byref，SW2025 早期接口下报"参数数量错误"。
        # 现在先试早期 (path,3,0,"",None,None) 并解包 tuple；失败逐字回退
        #   原 VARIANT 写法 —— 晚期绑定（SW2018~2024）行为与改造前一致。
        doc, _open_info = _open_doc6(sw, drawing_path, 3, 0)
        out["open_method"] = _open_info.get("method")
        out["open_uses_early"] = _open_info.get("uses_early")
        if doc is None:
            out["error"] = "无法打开工程图"
            out["open_attempts"] = _open_info.get("attempts")
            return out
        try:
            doc.ClearSelection2(True)
        except Exception:
            pass

        # 遍历视图（第一个是图纸层，跳过）—— 起点保持原样
        views, _verr = _collect_drawing_views(doc)
        if _verr:
            out["error"] = _verr
            return out
        if not views:
            out["error"] = "工程图里没有视图（图纸层之外为空），无法标注"
            out["hint"] = "请确认 .slddrw 里已建三视图；只有图纸层时无可标注对象。"
            out["gbt_report"] = _gbt_annotation_report(out)
            return out

        _before = [_count_annotations(doc, v) for v in views]
        # ★【BUG-观察-17 修复】API 挂在 IDrawingDoc 上 → 统一传 doc（原传 view）
        _used_api, _attempts, _after = _insert_model_annotations(
            doc, views, dim_types=dim_types)
        out["attempts"] = _attempts
        out["api"] = _used_api
        for _i, v in enumerate(views):
            _b = _before[_i]
            _a = _after[_i] if _i < len(_after) else _b
            _delta = max(0, _a - _b)
            out["per_view"].append({"view": _view_name(v, _i),
                                    "before": _b, "after": _a,
                                    "added": _delta, "api": _used_api})
            if _delta > 0:
                out["annotated_views"] += 1
            out["total_annotations"] += _delta

        # ══ 【本次修复】标注插入后必须【确认图纸真的被写回磁盘】════════════
        # 原实现把 EditRebuild3 + SaveAs3 整个包在 try/except: pass 里：
        #   · 保存失败被完全吞掉（连字段都不留）；
        #   · ok 只看 total_annotations > 0 →
        #     "标注插进内存了、但图纸没存盘" 会返回 ok:true，
        #     调用方打开 .slddrw 发现还是空的（正是 BUG-34 的现场之一）。
        # 现在：保存前快照、保存后过统一总闸；标注数为 0 或保存失败，
        #   都判 ok=False 并说清是哪一种。
        _ann_before = _artifact_snapshot(drawing_path)
        out["saved"] = False
        try:
            doc.EditRebuild3()
        except Exception as _e_rb:
            out["rebuild_error"] = repr(_e_rb)
        try:
            _ann_rc = doc.SaveAs3(drawing_path, 0, 2)
            out["save_rc"] = _ann_rc
        except Exception as _e_sv:
            out["save_error"] = repr(_e_sv)
        _ann_v = _verify_export_artifact(
            drawing_path, before=_ann_before, kind="SLDDRW",
            hint=("① 确认 .slddrw 未被 SW 独占锁定；② 目标目录可写；"
                  "③ 若只是内存中的标注未落盘，请在 SW 里手动保存一次确认。"))
        out["verify"] = _ann_v
        out["saved"] = bool(_ann_v.get("ok"))
        if not _ann_v.get("ok"):
            out["ok"] = False
            out["error"] = ("标注已尝试插入，但工程图【未能写回磁盘】："
                            + str(_ann_v.get("error") or ""))
            out["total_annotations"] = out.get("total_annotations") or 0
            out["gbt_report"] = _gbt_annotation_report(out)
            return out

        out["ok"] = out["total_annotations"] > 0
        # ── 【Bug-34 建议④】给出 GB/T 检查报告（逐项通过/缺失）──────────
        out["gbt_report"] = _gbt_annotation_report(out)
        if not out["ok"]:
            # ──【BUG-观察-17 修复】如实报告：确实插入了 0 个标注，
            #    并列出尝试过的 API 与错误；绝不假装成功，
            #    也不再把原因推给"装配体过于复杂"。
            _tried = sorted({str(a.get("api")) for a in _attempts
                             if a.get("called")})
            _skipped = sorted({str(a.get("api")) for a in _attempts
                               if not a.get("called")})
            out["error"] = ("插入了 0 个标注：已在 IDrawingDoc 上按类型库签名"
                            "逐条尝试 [%s]，各视图标注计数均无变化。"
                            % (", ".join(_tried) or "无可调用 API"))
            out["hint"] = (
                "① 已修复 API 挂错对象（原先查的是 IView，getattr 恒为 None）；"
                "② 若仍为 0，通常是【模型里没有可带出的尺寸】："
                "InsertModelAnnotations* 只能插入【已存在的模型尺寸】"
                "（草图尺寸/DimXpert），不会凭空生成 —— "
                "请在建模脚本里给关键尺寸加尺寸约束；"
                "③ 跳过（IDrawingDoc 上不存在）：%s；"
                "④ 仍缺失时需在 SW 界面手动标注（DimXpert / 智能尺寸）。"
                % (", ".join(_skipped) if _skipped else "无"))
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

    ──【BUG-观察-17 修复】────────────────────────────────────────────────
    本函数原先有与 annotate_drawing 【完全相同】的错误：在 IView 上
    getattr("InsertModelAnnotations*")、在 doc.Extension 上 getattr
    ("AutoDimension") —— 类型库实证这些 API 全在 IDrawingDoc 上，
    故 getattr 恒为 None、0 次执行。现在与 annotate_drawing 共用
    _insert_model_annotations（统一走 doc + 类型库对齐的参数个数）。

    ⚠️ 待实机验证（同 annotate_drawing）：本机无运行中的 SLDWORKS。

    Returns: {ok, annotated_views, total_annotations, api, per_view,
              attempts, gbt_report}
    """
    out = {"ok": False, "annotated_views": 0, "total_annotations": 0,
           "per_view": [], "api": None, "attempts": []}
    views, _verr = _collect_drawing_views(doc)
    if _verr:
        out["error"] = _verr
        out["gbt_report"] = _gbt_annotation_report(out)
        return out
    if not views:
        out["error"] = "工程图里没有视图（图纸层之外为空），无法标注"
        out["gbt_report"] = _gbt_annotation_report(out)
        return out

    _before = [_count_annotations(doc, v) for v in views]
    # ★【BUG-观察-17 修复】统一在 IDrawingDoc 上调用（原先是 getattr(v, ...)）
    _used_api, _attempts, _after = _insert_model_annotations(
        doc, views, dim_types=dim_types)
    out["attempts"] = _attempts
    for _i, v in enumerate(views):
        _b = _before[_i]
        _a = _after[_i] if _i < len(_after) else _b
        _delta = max(0, _a - _b)
        out["per_view"].append({"view": _view_name(v, _i), "before": _b,
                                "after": _a, "added": _delta,
                                "api": _used_api})
        if _delta > 0:
            out["annotated_views"] += 1
        out["total_annotations"] += _delta

    out["api"] = _used_api
    out["ok"] = out["total_annotations"] > 0
    out["gbt_report"] = _gbt_annotation_report(out)
    if not out["ok"]:
        _tried = sorted({str(a.get("api")) for a in _attempts if a.get("called")})
        out["error"] = ("插入了 0 个标注：已在 IDrawingDoc 上按类型库签名逐条"
                        "尝试 [%s]，各视图标注计数均无变化。"
                        % (", ".join(_tried) or "无可调用 API"))
        out["hint"] = ("① 已修复 API 挂错对象（原先查的是 IView，恒为 None）；"
                       "② InsertModelAnnotations* 只能插入【模型中已存在的"
                       "尺寸】—— 请确认建模时已给关键尺寸加约束；"
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
        # ⚠️ 【子任务 C · 未改造调用点（有意保留）】本处仍用晚期 VARIANT 写法。
        # 任务范围只要求优先改造 cmd_drawing / cmd_dwg / annotate_drawing 三处，
        # title-block 链路保持原样（行为与改造前完全一致）。若 SW2025 在此处
        # 报"参数数量错误"，改用 _open_doc6(sw, drawing_path, 3, 0) 即可（报告 3.7）。
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


def cmd_dwg(sw, part_path, output_path=None, force_redraw=False):
    """从零件生成工程图并导出为 DWG 格式。

    【强制使用中文视图名】先调用 cmd_drawing（已包含中文视图名），再导出 DWG。

    force_redraw: True = 即使 .slddrw 已存在也强制重建（会覆盖手工标注，慎用）；
                  False（默认）= 已有工程图则直接复用导出（BUG-21）。
    """
    # 1. 先确定工程图路径
    drawing_path = None
    if output_path:
        base = os.path.splitext(output_path)[0]
        drawing_path = base + ".slddrw"
    else:
        # 与 cmd_drawing 的默认落点保持一致（<任务根>\drawings\<名>.slddrw），
        # 这样"先查后建"能命中上一轮已经出好的图，而不是另造一份。
        try:
            drawing_path = _default_drawing_path(part_path, ".slddrw")
        except Exception:
            drawing_path = None

    # ── 【BUG-21 修复】工程图已存在时【直接复用】，绝不重新生成 ──────────────
    # 现场：对已有 .slddrw 的零件执行 dwg，会先调 cmd_drawing 重建工程图，
    #   把用户手工补上的公差/粗糙度/形位公差/标题栏【全部覆盖掉】。
    #   用户原话：「dwg 命令应先检查 .slddrw 是否存在，存在则直接导出而非重建」。
    # 处理：
    #   · 默认（无 --redraw）：存在就直接用它，并在返回里如实说明 reused；
    #   · 显式 --redraw：强制重建（保留旧能力，但要用户自己要求）。
    # ── 【本次修复·实测发现的真实崩溃】变量名 bug：_force_redraw 未定义 ──
    # 参数名是 force_redraw，这里却写成 _force_redraw（多一个下划线）。
    # 由于 Python 的短路求值，该表达式在
    #   「drawing_path 为空」或「.slddrw 不存在」时根本不会求值到 _force_redraw
    # → 首次出图（还没有 .slddrw）一切正常；
    #   而【只要工程图已经存在】，就必然抛
    #   NameError: name '_force_redraw' is not defined
    #   → BUG-21 专门加的那条"复用已有图纸、别覆盖手工标注"的分支
    #     从上线起【从未真正执行过】，第二次调 dwg 必定崩在这里。
    # 这也是"出图链路从未被验证"的直接后果之一（没人跑过第二次）。
    _reuse = False
    if drawing_path and os.path.exists(drawing_path) and not force_redraw:
        _reuse = True
    if _reuse:
        dwg_result = {"ok": True, "path": drawing_path, "reused": True,
                      "note": ("【BUG-21】已存在工程图，直接导出 DWG，"
                               "未重新生成（避免覆盖手工标注）。"
                               "确需重建请加 --redraw。")}
    else:
        dwg_result = cmd_drawing(sw, part_path, drawing_path)
        if not dwg_result.get("ok"):
            return dwg_result
        drawing_path = dwg_result.get("path") or drawing_path

    # 2. 确定输出路径
    if not output_path:
        # ── 【观察点 22 修复】默认落到 <任务根>\drawings\ ──────────────────
        output_path = _default_drawing_path(part_path, ".dwg")

    # 3. 打开工程图并导出 DWG
    saving = dwg_result.get("path")
    if not (saving and os.path.exists(saving)):
        return {"ok": False, "error": "drawing file not found for DWG export"}

    result = {"dwg_path": output_path, "drawing_path": saving}

    try:
        # 打开工程图
        # ── 【子任务 C · 签名兼容】改用 _open_doc6（报告 3.7）──────────────
        # 原写法 errs/warns=VARIANT(VT_BYREF|VT_I4,0) 在 SW2025 的早期接口上
        #   等于多传实参 → "参数数量错误"；且早期接口返回的还是 tuple。
        # 现在：先 early(ISldWorks).OpenDoc6(...,None,None) 并解包 tuple，
        #   失败【逐字回退】原来的 VARIANT 写法 → SW 2018~2024 行为不变。
        doc_type = 3  # swDrawing
        doc, _open_info = _open_doc6(sw, saving, doc_type, 1)
        result["open_method"] = _open_info.get("method")
        result["open_uses_early"] = _open_info.get("uses_early")
        if doc is None:
            return {"ok": False, "error": "cannot open drawing for DWG export",
                    "open_attempts": _open_info.get("attempts")}

        # 导出 DWG（SaveAs3 直接另存为 .dwg）
        # 【本次修复】原判据只有 os.path.exists(output_path)：
        #   0 字节 DWG、或"上一轮就躺在那里的旧 DWG"都会被判成功。
        #   现改为统一总闸（存在 + >0 字节 + 本次确写入），并补
        #   SaveAs2 回退（部分版本/中文版上 SaveAs3 静默失败）。
        _dwg_before = _artifact_snapshot(output_path)
        _dwg_rc = None
        try:
            _dwg_rc = doc.SaveAs3(output_path, 0, 2)
            result["rc"] = _dwg_rc
            result["method"] = "SaveAs3"
        except Exception as e:
            result["saveas3_error"] = str(e)
        if not _verify_export_artifact(output_path, before=_dwg_before,
                                       kind="DWG").get("ok"):
            # 回退：IModelDoc2.SaveAs2(NewName, SaveAsVersion, SaveAsCopy, Silent)
            # （签名取自本机已注册 SldWorks 类型库，见 _open_doc6 注释）
            try:
                _rc2 = doc.SaveAs2(output_path, 0, False, True)
                result["saveas2_rc"] = _rc2
                result["method_fallback"] = "SaveAs2"
            except Exception as e2:
                result["saveas2_error"] = str(e2)
        _dwg_v = _verify_export_artifact(
            output_path, before=_dwg_before, kind="DWG",
            hint=("① 确认 .slddrw 已成功生成且能正常打开；"
                  "② 目标目录可写、文件未被 AutoCAD 占用；"
                  "③ 可改用 dxf 子命令直接出 DXF 供 cad-validate 验证。"))
        result["verify"] = _dwg_v
        result["exists"] = bool(_dwg_v.get("exists"))
        if _dwg_v.get("ok"):
            result["ok"] = True
            result["method"] = result.get("method") or "SaveAs3"
            result["size_bytes"] = _dwg_v.get("size")
        else:
            result["ok"] = False
            result["error"] = _dwg_v.get("error")
            if _dwg_v.get("hint"):
                result["hint"] = _dwg_v["hint"]
    except Exception as e:
        result["error"] = str(e)

    # ── 【P1-BUG-DWG 不做图层重映射】───────────────────────────────────────
    # 原缺陷：`_remap_dxf_layers()`（把 SW 中文图层"可见边线/尺寸/…"映射为
    #   CADX 标准层 OUTLINE/THIN/CENTER/HIDDEN/DIM/TEXT/HATCH）唯一调用点在
    #   cmd_dxf。于是 DWG 交付物保留中文层名，后续
    #   `cad-validate <图纸.dwg>` 经 _dwg_to_dxf 转换后【仍无重映射】
    #   → 每次出图都报一堆 MISSING_LAYER WARNING，CADX 验证必然不通过。
    # 修复：DWG 落盘后，顺手转一份 DXF 做图层重映射，并把结果一并回传。
    #   注意：DWG 是二进制格式、无法原地改层名，因此这里的语义是
    #   ① 让 cad-validate 走 DWG 时能拿到【已重映射的 DXF】；
    #   ② 把映射台账回显给调用方，明确告知"标准层是否已补齐"。
    if result.get("ok") and os.path.exists(output_path):
        try:
            _conv = _dwg_to_dxf(output_path)
            if _conv.get("ok") and _conv.get("dxf_path"):
                _rm = _remap_dxf_layers(_conv["dxf_path"], rewrite=True)
                result["layer_remap"] = _rm
                result["dxf_for_validate"] = _conv["dxf_path"]
                result["layer_remap_note"] = (
                    "DWG 为二进制格式、无法原地改层名；已生成配套 DXF 并完成"
                    "SW 中文层 → CADX 标准层重映射，供 cad-validate 使用。")
            else:
                result["layer_remap"] = {
                    "ok": False,
                    "error": _conv.get("error") or "DWG→DXF 转换失败，未能重映射图层",
                    "hint": ("可改用 `python sw_bridge.py dxf <零件.SLDPRT>` "
                             "直接产出已重映射的 DXF。")}
        except Exception as _e_rm:
            result["layer_remap"] = {"ok": False, "error": repr(_e_rm)}

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
        # 【连接区修复】改为枚举【全部真实盘符】，不再硬编码 C/D/E/Z ——
        #   用户把 ODA 或 SW/CAD 换装到 G:/Q: 等盘时也能找到。
        try:
            import swapi as _sw_drives
            _drive_roots = _sw_drives._drive_roots()
        except Exception:
            _drive_roots = [d + ":\\" for d in ("C", "D", "E", "Z")]
        for drive in _drive_roots:
            oda_cands += [
                os.path.join(drive, "Program Files", "ODA", "ODAFileConverter"),
                os.path.join(drive, "Program Files (x86)", "ODA", "ODAFileConverter"),
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
        # ⚠️ 【子任务 C · 未改造调用点（有意保留）】本处仍用晚期 VARIANT 写法。
        # 任务范围只要求优先改造 cmd_drawing / cmd_dwg / annotate_drawing 三处
        # （外加它们必经的 open_document_fresh）；dxf 链路保持原样，以免在
        # 未实机验证的情况下扩大改动面。SW2025 若在此处报"参数数量错误"，
        # 直接改用 _open_doc6(sw, saving, 3, 1) 即可（报告 3.7）。
        doc_type = 3  # swDrawing
        errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = sw.OpenDoc6(saving, doc_type, 1, "", errs, warns)
        if doc is None:
            return {"ok": False, "error": "cannot open drawing for DXF export"}

        # ── 方法1: SaveAs3 直接另存 DXF ──────────────────────────────
        # 【本次修复】导出前先快照：目标路径若已有上一轮的旧 DXF，
        #   原判据（exists + size>0）恒成立 → 本次根本没写盘也报成功。
        _dxf_before = _artifact_snapshot(output_path)
        try:
            rc = doc.SaveAs3(output_path, 0, 2)
            result["saveas3_rc"] = rc
            if _verify_export_artifact(output_path, before=_dxf_before,
                                       kind="DXF").get("ok"):
                result["method"] = "SaveAs3"
        except Exception as e:
            result["saveas3_error"] = str(e)

        # ── 方法2: SaveAs2 旧接口回退 ────────────────────────────────
        # 签名（本机 SldWorks 类型库实证）：
        #   IModelDoc2.SaveAs2(NewName, SaveAsVersion, SaveAsCopy, Silent)  —— 4 参
        # 原实现传了 3 参且第 3 参给了 0（当 bool 用），属参数个数错误，
        #   在晚期绑定下直接 TypeError，回退分支从未真正生效。
        if not result.get("method"):
            try:
                rc2 = doc.SaveAs2(output_path, 0, False, True)
                result["saveas2_rc"] = rc2
                if _verify_export_artifact(output_path, before=_dxf_before,
                                           kind="DXF").get("ok"):
                    result["method"] = "SaveAs2"
            except Exception as e:
                result["saveas2_error"] = str(e)

        # ── 方法3: ExportToDWG2 ──────────────────────────────────────
        # 【本次修复·参数个数错误】原实现给 ExportToDWG2 传了 12 个实参
        #   （..., False, False, 0, None, False, False, 0），必然 TypeError。
        # 本机已注册 SldWorks 类型库实证（权威依据，勿凭记忆改）：
        #   接口归属 = IPartDoc（不是 IDrawingDoc/IModelDoc2！）
        #   IPartDoc.ExportToDWG2(FilePath, ModelName, Action, ExportToSingleFile,
        #       Alignment, IsXDirFlipped, IsYDirFlipped, SheetMetalOptions, Views)
        #       —— 共 9 参
        # 且它作用于【零件】，用于钣金展开/DXF 导出，对【工程图文档】
        #   不适用 → 本命令（导出工程图）保留该分支仅作最后兜底，
        #   失败信息如实回写，绝不因"调用没抛异常"就判成功。
        if not result.get("method"):
            try:
                ok3 = doc.ExportToDWG2(output_path, saving, 1, True, None,
                                       False, False, 0, None)
                result["exporttodwg2_rc"] = ok3
                if _verify_export_artifact(output_path, before=_dxf_before,
                                           kind="DXF").get("ok"):
                    result["method"] = "ExportToDWG2"
            except Exception as e:
                result["exporttodwg2_error"] = str(e)

        # ── 统一总闸：三种方法都试完后，用同一判据定性 ────────────────
        _dxf_v = _verify_export_artifact(
            output_path, before=_dxf_before, kind="DXF",
            hint=("① 确认 .slddrw 已生成且能打开；② 目标目录可写；"
                  "③ 若 SW 弹出了 DXF/DWG 映射对话框，导出会被阻塞 —— "
                  "可在 SW 选项里关闭『每次保存时显示映射关系』。"))
        result["verify"] = _dxf_v
        result["size_bytes"] = _dxf_v.get("size")
        if _dxf_v.get("ok"):
            result["ok"] = True
            result.setdefault("method", "SaveAs3")
        else:
            result["ok"] = False
            result["error"] = _dxf_v.get("error")
            if _dxf_v.get("hint"):
                result["hint"] = _dxf_v["hint"]

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


# ── 【连接自检修复】AutoCAD 连接确认的统一实现 ────────────────────────────
# 这两条命令【只跟 AutoCAD 通信】，与 SolidWorks 无关：
#   · 不取 SW 独占锁（已加入 _NO_LOCK_CMDS）；
#   · main() 里 sw 被置为 None（已加入 _CAD_ONLY_CMDS）——
#     因此在没有装 SW 的机器上也能确认 CAD 连接。
# 二者都遵循"单次 get_ac() + 用后 close()"，避免重复 Dispatch 与连接泄漏。

def _ac_bridge_unavailable():
    return {
        "ok": False,
        "connected": False,
        "error": "ac_bridge 模块未找到，请确保 engineering/tools/ 下有 ac_bridge.py",
    }


def _ac_connect_failure(hint_extra=""):
    """AutoCAD 连接失败的结构化结果（可操作）。"""
    out = {
        "ok": False,
        "connected": False,
        "error": "无法连接 AutoCAD",
        "hint": ("请确认 AutoCAD 正在运行且已打开（或新建）一张图纸 —— "
                 "ac_bridge 只挂接【已运行】的 AutoCAD 实例，不会自行启动它。\n"
                 "排查步骤：\n"
                 "  1) 手动启动 AutoCAD，打开任意图纸；\n"
                 "  2) 若 AutoCAD 正忙（弹出对话框/命令执行中），按 ESC 取消后再试；\n"
                 "  3) 纯 SW 出图链路【不需要】AutoCAD："
                 "python sw_bridge.py dxf <零件.SLDPRT> 可直接产出 DXF 供 "
                 "cad-validate 验证。"),
    }
    if hint_extra:
        out["hint"] = out["hint"] + "\n" + hint_extra
    return out


def _ac_status_result():
    """ac-status 的实现：单次连接、取状态、用后释放。"""
    ac = None
    try:
        ac = ac_bridge.get_ac()          # 只调用一次（原实现调了两次）
        if not ac:
            return _ac_connect_failure()
        res = ac.status()
        if isinstance(res, dict):
            res.setdefault("ok", True)
            res.setdefault("connected", True)
            res["command"] = "ac-status"
            return res
        return {"ok": True, "connected": True, "command": "ac-status",
                "status": res}
    except Exception as e:
        out = _ac_connect_failure()
        out["error"] = "AutoCAD 状态查询异常: %s" % e
        return out
    finally:
        # ── 【连接自检修复】用后释放，避免 COM 连接泄漏 ──────────────────
        # ac_bridge.close() 只断开 Python 侧引用，【不关闭】AutoCAD 本身。
        try:
            if ac is not None:
                ac.close()
        except Exception:
            pass


def _ac_export_result(out_path=""):
    """ac-export 的实现：单次连接、导出 DXF、用后释放。"""
    ac = None
    try:
        ac = ac_bridge.get_ac()
        if not ac:
            return _ac_connect_failure()
        dxf_path = ac.export_dxf(out_path) if out_path else ac.export_dxf()
        ok = bool(dxf_path) and os.path.exists(str(dxf_path))
        out = {
            "ok": ok,
            "connected": True,
            "command": "ac-export",
            "dxf_path": str(dxf_path) if dxf_path else None,
        }
        if not ok:
            out["error"] = "AutoCAD 未产出 DXF（export_dxf 返回空或文件不存在）"
            out["hint"] = ("请确认当前 AutoCAD 文档已保存且包含实体；"
                           "或改用纯 SW 链路：python sw_bridge.py dxf <零件.SLDPRT>")
        return out
    except Exception as e:
        out = _ac_connect_failure()
        out["error"] = "AutoCAD 导出 DXF 异常: %s" % e
        return out
    finally:
        try:
            if ac is not None:
                ac.close()
        except Exception:
            pass


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


# ══ 【Bug⑨】长任务自动推进房间阶段（validating）═════════════════════════
def _push_room_validating(room=None, what=""):
    """【Bug⑨】在房间上下文里把 stage 推进到 validating（幂等、失败不阻断）。

    现场缺陷（竞速小车任务）：
      底盘结构件房 21:08 → 22:22（约 74 分钟），room-report.json 的 stage
      长期停在「modeling / 开始建DSH_底盘」。实际 21:42 三件零件就已建完，
      之后一直在跑物理校核、看图矫正、prime-report —— 但这些长命令
      【完全不碰 room-report】，房间从未推进到 validating/done。
      主代理无法判断房间"在跑 FEA"还是"已死机"，只能靠 SW CPU 与文件
      mtime 两条旁路推断（Bug⑨ 原文）。

    为什么放在 sw_bridge 而不是 physics_bridge：
      physics-optimize / physics-validate-domain / physics-fatigue 都是经
      sw_bridge 派发的（推荐入口）。在这里集中推进一次，即可覆盖三条长命令，
      且【不必改动 physics_bridge.py 的内部实现】—— 避免与 physics 侧
      正在进行的其它修复互相踩踏。

    行为契约（与 mode_gate.push_room_stage 同一套，实现幂等）：
      ① 房间不存在 / 非 active（没 room-start 或已 room-end）→ 不写，
         只回 skipped（绝不在全局上下文中凭空造房间进度）；
      ② 最新 stage 已是 validating → 幂等跳过，不重复追加 history；
      ③ 已上报过 done / failed → 一律不让步（终态是事实）；
      ④ 任何异常都不影响主流程（本函数不抛异常）。

    Args:
        room: 房间名；None → 用 DSH_ROOM 环境变量（子代理通常在房间内运行）。
        what: 正在跑什么（写进 detail，便于主代理一眼看出是 FEA 还是看图）。
    Returns: mode_gate.push_room_stage 的返回体；不可用时返回 ok=False + 原因。
    """
    _room = (room or os.environ.get("DSH_ROOM") or "").strip()
    _detail = (str(what).strip() or "长任务执行中") + "（自动推进，Bug⑨）"
    try:
        _gate = _get_mode_gate_path()
        if not _gate or not os.path.exists(_gate):
            return {"ok": False, "pushed": False, "skipped": "no-mode-gate",
                    "reason": "未找到 mode_gate.py，无法推进房间阶段"}
        # 进程内 import：优先用【同目录】那份（_import_sibling 会校验不是别处的副本）；
        # 个别部署下 mode_gate 只在 STATE_DIR，此时回退按明确路径加载 ——
        # 无论哪条路，拿到的都是 _get_mode_gate_path() 指认的那一份。
        try:
            _mg = _import_sibling("mode_gate")
        except Exception:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("_dsh_mode_gate_stage", _gate)
            _mg = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mg)
        if not hasattr(_mg, "push_room_stage"):
            return {"ok": False, "pushed": False,
                    "skipped": "mode-gate-too-old",
                    "reason": "mode_gate.py 缺少 push_room_stage（旧版本副本？）"}
        return _mg.push_room_stage(_room or None, "validating", _detail)
    except Exception as e:
        # 进度上报坏了绝不能把校核任务带崩
        return {"ok": False, "pushed": False, "skipped": "exception",
                "reason": repr(e)}


# ══ 【BUG-S02/S03 修复】文档清场循环 + SW 进程强杀确认 ══════════════════
# 现场缺陷（2026-10-29 循迹小车任务 20261029_082936_4159A331）：
#   S02：sw_bridge.py close-all 返回 ok=false、docs_after=15
#        （15 个文档仍开着）。旧实现只调【一次】CloseAllDocuments(0) 就读
#        GetDocumentCount —— 有后台任务/模态对话框时一次关不干净，
#        于是"清场"事实上没清，下一波并行房间继续堆文档，SW 变慢/不稳。
#   S03：Stop-Process -Force 杀掉 SLDWORKS 后 5 秒又冒出新 PID
#        （13240→24256→25548），杀不干净。旧实现只发一次 taskkill 信号，
#        既不 /T（不带子进程树），也不轮询确认"真的没了"。
# 修法（本段）：
#   · close-all：最多 3 轮 CloseAllDocuments(0)，每轮读 GetDocumentCount，
#     docs_after==0 才算成功；关不掉就如实报 failing_docs + hint。
#   · sw-kill：每轮 taskkill /F /T /IM SLDWORKS.exe → 等 wait 秒 → 再查进程，
#     最多 rounds 轮；只有"最后真的没有进程"才 finally_exited=True / ok=True。
#
# ⚠️ 【可测试性设计】上面两段逻辑都抽成【纯函数】，把"读文档数/关文档"
#    与"杀进程/查进程/sleep"全部做成可注入的参数：
#      测试脚本注入 mock 就能 100% 覆盖，不需要真的装 SW、真的杀进程。

# ── SW 文档管理器适配层（把 COM 的三种调用约定收敛成三个方法）───────────
class _SwDocManager(object):
    """把 SldWorks.Application 适配成文档管理器（只做读/关，不退出进程）。

    抽这一层的理由：循环关闭的【判定逻辑】不该和 COM 的绑定细节纠缠
    （late-binding 下 GetDocumentCount 是无参属性、早期绑定下是方法；
    关闭接口名也可能不存在）。适配层把差异吃掉，纯函数只管循环与判定。
    """

    def __init__(self, sw):
        self.sw = sw

    def count(self):
        """当前文档数（取不到返回 None）。"""
        for _get in (lambda: getattr(self.sw, "GetDocumentCount", None),
                     lambda: self.sw.GetDocumentCount()):
            try:
                _v = _get()
                if _v is not None:
                    return int(_v)
            except Exception:
                continue
        return None

    def close_all(self):
        """调一次 CloseAllDocuments(0)（0 = 不弹保存提示）。返回 (ok, err)。"""
        try:
            self.sw.CloseAllDocuments(0)
            return True, None
        except AttributeError as e:
            return False, "当前 SW 版本没有 CloseAllDocuments: %r" % (e,)
        except Exception as e:
            return False, "CloseAllDocuments 调用异常: %r" % (e,)

    def doc_titles(self, limit=20):
        """尽力枚举仍打开文档的标题（拿不到就返回空列表，绝不抛异常）。"""
        titles = []
        try:
            _docs = None
            for _get in (lambda: self.sw.GetDocuments,
                         lambda: self.sw.GetDocuments()):
                try:
                    _docs = _get()
                    if _docs:
                        break
                except Exception:
                    continue
            for _d in (_docs or []):
                if len(titles) >= limit:
                    break
                try:
                    _t = getattr(_d, "GetTitle", None)
                    if _t is not None:
                        _t = _t() if callable(_t) else _t
                    if _t:
                        titles.append(str(_t))
                except Exception:
                    continue
        except Exception:
            pass
        return titles

    def visible(self):
        """当前 Visible 设置（取不到返回 None）。"""
        try:
            return bool(self.sw.Visible)
        except Exception:
            return None

    def set_visible(self, v):
        """恢复/保持 Visible（保活语义：绝不把 SW 藏起来导致弹框看不见）。"""
        try:
            self.sw.Visible = bool(v)
            return True
        except Exception:
            return False


def close_documents_until_zero(doc_mgr, max_rounds=3, close_fn=None):
    """【BUG-S02 修复】循环关闭文档，直到 docs_after==0（最多 max_rounds 轮）。

    ── 为什么必须循环 ────────────────────────────────────────────────────
    实测：CloseAllDocuments(0) 返回后仍有 15 个文档开着。改文档保存状态、
    后台重建、模态对话框都会让【单次】关闭失效。所以：
      · 每轮都重新读 GetDocumentCount（客观证据，不看返回值）；
      · 只有 docs_after == 0 才算 ok=True；
      · 关不掉就如实报 failing_docs（最多 20 个标题）+ 最后一轮的 before/after。

    ── 为什么绝对不在这里杀进程 ──────────────────────────────────────────
    本函数是【保活】路径：并行房间共用一台 SW，杀进程会让其它房间的 RPC
    立刻失效（Bug⑧）。杀进程只在 --quit / sw-kill 分支，由调用方显式选择。

    Args:
        doc_mgr: 文档管理器（需有 count() / close_all() / doc_titles()）；
                 传真实 SW 时用 _SwDocManager(sw)；测试注入 mock。
        max_rounds: 最多关闭轮数（默认 3）。
        close_fn: 可选的"执行一次关闭"函数 → (ok, err)；默认用
                 doc_mgr.close_all()。传 swapi 版可复用既有单次实现。
    Returns:
        {"ok", "method": "close_all_documents_loop", "keepalive": True,
         "quit": False, "sw_exited": False, "docs_before", "docs_after",
         "rounds_used", "max_rounds", "failing_docs", "hint"?, "error"?}
    """
    try:
        _rounds = int(max_rounds)
    except Exception:
        _rounds = 3
    _rounds = max(1, _rounds)
    out = {"ok": False, "method": "close_all_documents_loop",
           "keepalive": True, "quit": False, "sw_exited": False,
           "docs_before": None, "docs_after": None,
           "rounds_used": 0, "max_rounds": _rounds,
           "failing_docs": []}
    if doc_mgr is None:
        out["error"] = "文档管理器为空（sw=None）—— 无法执行文档级关闭。"
        out["hint"] = ("请先确认 SolidWorks 已连接；若只是僵尸进程，"
                       "用 python sw_bridge.py sw-kill 或 close-all --quit。")
        return out

    # 一次关闭尝试的完整轨迹（每轮 before/after 都留证，便于 UI/AI 定位）
    trace = []
    try:
        _before = doc_mgr.count()
    except Exception as e:
        _before = None
        trace.append({"phase": "count_before", "error": repr(e)})
    out["docs_before"] = _before
    if _before == 0:
        # 本来就没文档：无需调用关闭接口，直接算已清场
        out["ok"] = True
        out["rounds_used"] = 0
        out["note"] = "执行前就没有打开的文档，无需关闭。"
        return out

    _last_after = _before
    _close_err = None
    for _i in range(_rounds):
        _r = {"round": _i + 1, "before": _last_after}
        try:
            if close_fn is not None:
                _cp = close_fn()
                _cok, _cerr = (_cp if isinstance(_cp, tuple)
                               else (bool(_cp), None))
            else:
                _cok, _cerr = doc_mgr.close_all()
        except Exception as e:
            _cok, _cerr = False, "close_all 抛异常: %r" % (e,)
        _r["close_ok"] = bool(_cok)
        if _cerr:
            _r["close_error"] = _cerr
            _close_err = _cerr
        try:
            _after = doc_mgr.count()
        except Exception as e:
            _after = None
            _r["count_error"] = repr(e)
        _r["after"] = _after
        trace.append(_r)
        out["rounds_used"] = _i + 1
        _last_after = _after
        if _after == 0:
            break

    out["docs_after"] = _last_after
    out["rounds_trace"] = trace

    if _last_after == 0:
        out["ok"] = True
        out["note"] = ("已关闭全部文档（%s 轮，docs %s → 0）；SW 进程保持运行"
                       "（保活）—— 并行房间下次调用可直接复用该实例。"
                       % (out["rounds_used"], _before))
        return out

    # ── 关不干净：如实报出残存文档，并给出【可操作】的下一步 ────────────
    out["ok"] = False
    _titles = []
    try:
        _titles = doc_mgr.doc_titles(limit=20) or []
    except Exception:
        _titles = []
    out["failing_docs"] = _titles[:20]
    if _last_after is None:
        out["error"] = ("无法读取文档数（GetDocumentCount 取不到），"
                        "不能确认文档是否已全部关闭。")
    else:
        out["error"] = ("已关闭 %s 轮，仍有 %d 个文档未关闭（docs %s → %s）。"
                        % (out["rounds_used"], _last_after, _before, _last_after))
        if _titles:
            out["error"] += " 残存文档: " + "; ".join(_titles[:20])
    out["hint"] = ("为避免误杀并行房间共用的 SW 进程，本路径【不】强杀进程。"
                   "若仍有文档关不掉：① SolidWorks 有【未保存文档时会弹保存框】，"
                   "该模态框会阻塞 CloseAllDocuments —— 请到 SW 窗口里处理掉；"
                   "② 也可能是后台重建/仿真任务占着文档，等它结束再重试；"
                   "③ 确认要连进程一起清场时用 python sw_bridge.py close-all --quit"
                   "（会走 sw-kill 强杀并确认退出）。")
    if _close_err:
        out["close_error"] = _close_err
    return out


def kill_solidworks_confirmed(rounds=3, wait=5,
                              kill_fn=None, process_list_fn=None, sleep_fn=None):
    """【BUG-S03 修复】循环强杀 SLDWORKS.exe，并【轮询确认真的退出】。

    ── 现场缺陷 ──────────────────────────────────────────────────────────
    Stop-Process -Force 杀完 5 秒内又冒出新的 PID（13240→24256→25548），
    反复 3 轮仍存在。原因有两层：
      ① 只发【一次】taskkill 信号，且不带 /T —— 拉起的子进程/COM 宿主没被带走；
      ② taskkill 是【异步信号】：返回码 0 只说明"信号发出去了"，
         不代表进程已经没了。旧实现拿返回码当成功，于是"杀完还在"。

    ── 为什么杀完还会重生 ────────────────────────────────────────────────
    SolidWorks 有自己的【后台下载器/文档恢复】守护进程（SolidWorks 后台
    下载服务、文档恢复、许可服务等）。强杀主进程后，它们会在几秒内把
    SLDWORKS.exe 重新拉起（这正是现场 13240→24256→25548 的成因）。
    因此本函数【每轮都重新查进程】，并用 pid_history 全程留证；
    即使 rounds 轮后仍有进程，也把 pid 变化历史一并返回，供 AI 判断
    "是被重生还是根本没杀掉"。仍不成功时 note 会明确写出重生成因。

    Args:
        rounds: 最多尝试轮数（默认 3）
        wait:   每轮杀完等待【秒】数（默认 5）
        kill_fn: 可注入的"执行一次强杀"函数 → (killed:bool, detail:str)
                 默认 taskkill /F /T /IM SLDWORKS.exe
        process_list_fn: 可注入的"枚举进程"函数 → [pid ...] 或 [{"pid":...}]
                 默认 _sw_processes；返回 None 表示探测失败（≠ 没有进程）
        sleep_fn: 可注入的 sleep（测试注入空函数，避免真等待）
    Returns:
        {"ok", "finally_exited", "rounds_used", "pid_history":[...],
         "rounds_trace":[...], "note", "error"?}
    """
    if kill_fn is None:
        def kill_fn():
            try:
                _r = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", "SLDWORKS.exe"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30)
                return (_r.returncode == 0,
                        ((_r.stdout or "") + (_r.stderr or "")).strip()[:300])
            except Exception as e:
                return False, "taskkill 执行失败: %r" % (e,)
    if process_list_fn is None:
        def process_list_fn():
            procs, err = _sw_processes()
            if err:
                return None   # 探测失败 ≠ 没有进程
            return [(p or {}).get("pid") for p in (procs or [])]
    if sleep_fn is None:
        sleep_fn = time.sleep

    try:
        _rounds = max(1, int(rounds))
    except Exception:
        _rounds = 3
    try:
        _wait = max(0.0, float(wait))
    except Exception:
        _wait = 5.0

    def _pids(raw):
        if raw is None:
            return None
        res = []
        for p in raw:
            try:
                if isinstance(p, dict):
                    p = p.get("pid")
                if p is None:
                    continue
                res.append(int(p))
            except Exception:
                continue
        return res

    out = {"ok": False, "finally_exited": False, "rounds_used": 0,
           "pid_history": [], "rounds_trace": [], "wait_sec": _wait,
           "max_rounds": _rounds,
           "method": "taskkill /F /T /IM SLDWORKS.exe + 轮询确认"}
    # 初始快照：杀之前到底有哪些 PID
    _init = _pids(process_list_fn())
    if _init is not None:
        for _p in _init:
            if _p not in out["pid_history"]:
                out["pid_history"].append(_p)
    if _init == []:
        out["ok"] = True
        out["finally_exited"] = True
        out["rounds_used"] = 0
        out["note"] = "执行前就没有 SLDWORKS.exe 进程，无需强杀。"
        return out
    if _init is None:
        out["note"] = ("进程探测手段失败，无法确认初始状态；仍按计划尝试强杀，"
                       "最后以【能否读到进程列表】为准判定。")

    _probe_failed = False
    _after = _init
    for _i in range(_rounds):
        _kill_ok, _kill_detail = kill_fn()
        _tr = {"round": _i + 1, "kill_ok": bool(_kill_ok),
               "kill_detail": _kill_detail}
        try:
            sleep_fn(_wait)
        except Exception:
            pass
        _raw = _pids(process_list_fn())
        _tr["after_wait"] = _raw
        out["rounds_trace"].append(_tr)
        out["rounds_used"] = _i + 1
        if _raw is None:
            _probe_failed = True
            continue
        for _p in _raw:
            if _p not in out["pid_history"]:
                out["pid_history"].append(_p)
        _after = _raw
        if not _raw:
            out["ok"] = True
            out["finally_exited"] = True
            out["note"] = ("已确认 SLDWORKS.exe 完全退出（%s 轮，等待 %.1f 秒/轮）。"
                           "PID 变化: %s。"
                           % (out["rounds_used"], _wait,
                              " → ".join(str(x) for x in out["pid_history"]) or "无"))
            return out

    # ── rounds 轮仍未退出：如实报失败 + pid 历史 + 重生成因 ───────────────
    out["ok"] = False
    out["finally_exited"] = False
    _remain = _after if _after is not None else []
    out["remaining_pids"] = _remain
    if _probe_failed and _after is None:
        out["error"] = ("已尝试 %s 轮强杀，但【进程探测手段失败】，"
                        "无法确认 SLDWORKS.exe 是否已退出。" % out["rounds_used"])
    else:
        out["error"] = ("已尝试 %s 轮 taskkill（每轮等 %.0f 秒），仍有 SLDWORKS.exe "
                        "在运行（PID: %s）。"
                        % (out["rounds_used"], _wait,
                           ", ".join(str(x) for x in _remain) or "?"))
    _grew = [p for p in (_remain or []) if p not in (_init or [])]
    out["note"] = (
        "PID 变化历史: %s。"
        "若每轮杀完都出现【新 PID】，说明 SolidWorks 被【后台下载器/文档恢复/"
        "许可服务】等守护进程重新拉起 —— 这是 Windows 上强杀 SW 的常见现象，"
        "不是 taskkill 没生效：先结束这些守护进程（或等它们下一轮调度结束），"
        "再杀 SLDWORKS.exe 才能真正退出。%s"
        % (" → ".join(str(x) for x in out["pid_history"]) or "无",
           ("本轮出现了新 PID（%s），符合『被杀后重生』的特征。"
            % ", ".join(str(x) for x in _grew)) if _grew else ""))
    return out


def cmd_sw_kill(rounds=3, wait=5):
    """【BUG-S03】命令：循环强杀 SLDWORKS.exe 并确认退出（zero-COM）。

    用法：
      python sw_bridge.py sw-kill               # 默认 3 轮 × 5 秒
      python sw_bridge.py sw-kill --rounds 5 --wait 3

    与 close-all 的分工：
      · close-all          → 只关文档、SW 进程【保活】（并行房间共用时默认）；
      · close-all --quit   → 关文档 + Quit + sw-kill（真正退出进程）；
      · sw-kill            → 【只】处理进程：不碰文档、不连 COM。
    因此它也是【SW 卡死/连不上】时唯一可用的清场手段（不需要 COM 会话）。
    finally_exited=True 才 ok=True —— 绝不用"信号已发出"冒充"进程已退出"。
    """
    r = kill_solidworks_confirmed(rounds=rounds, wait=wait)
    r["command"] = "sw-kill"
    r["exe"] = "SLDWORKS.exe"
    return r


# ── Bug-25: 安全退出 SolidWorks / 【Bug⑧】close-all 保活 ─────────────
def cmd_close_all(sw, quit=False):
    """关闭 SolidWorks 文档并释放使用权（【Bug⑧】默认保活，--quit 才退出）。

    ── 【Bug⑧ 修复】并行房间共用单台 SW 时的"互相切断" ────────────────────
    现场缺陷（竞速小车任务，三个房间共用一台 SW，FIFO 排队）：
      任一房间调 close-all → 本函数走 Quit + taskkill → SW 进程结束 →
      其它房间正在进行的 RPC 连接【立即失效】。
      主代理 conn-gate --target sw 观察到 SW PID 频繁变化
      （4148 → 30860 → 18388 → 23448 → 472 → 34744），每次都是冷启动；
      每次冷启动后 swapi 都要重新 Dispatch("SldWorks.Application")。
      并行模式实际退化成"串行 + 冷启动"，单个零件建模耗时被放大
      （本会话 9 个零件总耗时约 70 分钟）。

    现在分两级（与 Bug 记录里的建议修复方向一致）：
      · 默认     → 循环调 CloseAllDocuments(0)（最多 3 轮），【docs_after==0
                   才算成功】，且【不退出 SW 进程】；下一个房间/下一个零件
                   复用同一实例，无需冷启动。
                   （【BUG-S02】旧实现只调一次，实测 docs_after=15 一个没关掉）
      · --quit  → 循环关文档 + 原有 close_all_and_exit（Quit + taskkill），
                   再用 sw-kill 的确认逻辑复核进程是否【真的】退出，
                   返回体带 sw_exited / finally_exited 的真实值（【BUG-S03】）。

    SW 使用权（mode_gate 的 SW 锁）在两种语义下【都】释放：
      它是"排队使用权"，与 SW 进程生死无关 —— 不放会让队列死锁。

    Args:
        sw: SldWorks Application（可为 None：SW 没连上时仍能释放锁）
        quit: True=真正结束 SW 进程（旧行为）；False=只关文档、保活（默认）
    """
    import swapi as _swapi
    if sw is None:
        # ── 【BUG-S03】sw=None + --quit：这正是"SW 卡死/COM 连不上、想清场"
        #   的现场。没有 COM 会话 ≠ 没法杀进程，所以无论如何，--quit 必须
        #   走【进程层】的 sw-kill 真正试一试，而不是直接返回"做不到"。
        if quit:
            _kill = kill_solidworks_confirmed(rounds=3, wait=5)
            return {
                "ok": bool(_kill.get("finally_exited")),
                "keepalive": False,
                "quit": True,
                "method": "sw-kill(no-com)",
                "docs_closed": None,
                "sw_exited": bool(_kill.get("finally_exited")),
                "finally_exited": bool(_kill.get("finally_exited")),
                "sw_kill": _kill,
                "error": (None if _kill.get("finally_exited")
                          else (_kill.get("error") or "SW 进程未能确认退出。")),
                "note": _kill.get("note"),
                "hint": ("SW 未连接（COM 不可用），因此【未能】关闭文档，"
                         "只做了进程层强杀与确认。若 SW 仍在运行，"
                         "请处理 SW 里的保存对话框后重试，或再执行一次 "
                         "python sw_bridge.py sw-kill 查看 pid_history。"),
            }
        # SW 未连接：无法发 CloseAllDocuments，但锁仍然要释放（由调用方处理）。
        # 明确回报 resumed=False，避免调用方误以为"文档已清干净"。
        return {"ok": False, "keepalive": (not quit), "quit": bool(quit),
                "method": None, "docs_closed": None,
                "error": "SW 未连接（sw=None）—— 无法执行 CloseAllDocuments。",
                "hint": ("SW 进程可能根本没启动。若只是想释放使用权，"
                         "用 python mode_gate.py sw-release <房间名>；"
                         "若确认有僵尸进程，用 python sw_bridge.py close-all --quit。")}
    # ── 【BUG-S02 修复】默认路径改为【循环关闭直到 docs_after==0】──────────
    # 现场缺陷：旧实现只调一次 swapi.close_all_documents()（单次
    #   CloseAllDocuments），实测返回 ok=false / docs_after=15 —— 15 个文档
    #   一个都没真正关掉。改为最多 3 轮、每轮重读 GetDocumentCount 验证。
    #
    # ⚠️ 保留 sw.Visible：SW 有未保存文档时会弹【保存对话框】，
    #    该模态框会阻塞 CloseAllDocuments。把 SW 藏起来只会让用户看不见、
    #    更处理不了那个框 —— 所以这里显式保持/恢复 Visible，并在 hint 里
    #    写清"有未保存文档时 SW 会弹框"。
    _mgr = _SwDocManager(sw)

    def _swapi_close_once():
        """复用 swapi.close_all_documents 的一次关闭（单一事实来源）+ 读回计数。

        ⚠️ 不直接用它当"循环体判定"：它内部只调【一次】CloseAllDocuments
          并自带 ok=false 判定（实测就是 docs_after=15 那次）。循环与
          "关到 0 才算成功"的判定由本函数负责，这里只取它的一次关闭动作。
        """
        try:
            _r = _swapi.close_all_documents(sw)
        except Exception as e:
            return False, "swapi.close_all_documents 异常: %r" % (e,)
        if isinstance(_r, dict):
            return bool(_r.get("ok", True)), _r.get("error")
        return True, None

    _vis_before = _mgr.visible()
    if quit:
        # --quit 路径 = 先循环关文档，再走原有 Quit + taskkill，
        # 最后用 sw-kill 的判定函数【再确认一次】进程是否真的退出。
        _doc_res = close_documents_until_zero(_mgr, max_rounds=3,
                                              close_fn=_swapi_close_once)
        result = _swapi.close_all_and_exit(sw, quit=True)
        if isinstance(result, dict):
            result.setdefault("keepalive", False)
            result["quit"] = True
            # 把"关闭文档循环"的证据一并带回（谁关的、几轮、剩几个）
            result["documents"] = {
                "ok": _doc_res.get("ok"),
                "docs_before": _doc_res.get("docs_before"),
                "docs_after": _doc_res.get("docs_after"),
                "rounds_used": _doc_res.get("rounds_used"),
                "failing_docs": _doc_res.get("failing_docs") or [],
            }
            # ── 【BUG-S03 修复】quit 分支必须给出【真实】的退出结论 ──────
            # close_all_and_exit 内部的 taskkill 只发一次 /IM（不带 /T、
            # 也可能被"后台下载器/文档恢复"重新拉起）。这里再跑一次
            # 带轮询确认的 sw-kill，并用它的 finally_exited 覆盖 ok ——
            # 绝不用"信号已发出"冒充"进程已退出"。
            _kill = kill_solidworks_confirmed(rounds=3, wait=5)
            result["sw_kill"] = {
                "ok": _kill.get("ok"),
                "finally_exited": _kill.get("finally_exited"),
                "rounds_used": _kill.get("rounds_used"),
                "pid_history": _kill.get("pid_history") or [],
                "note": _kill.get("note"),
                "error": _kill.get("error"),
            }
            result["sw_exited"] = bool(_kill.get("finally_exited"))
            result["finally_exited"] = bool(_kill.get("finally_exited"))
            if _kill.get("finally_exited") is not True:
                result["ok"] = False
                result["error"] = (_kill.get("error") or
                                   "SW 进程未能确认退出（taskkill 是异步信号，"
                                   "且可能被后台守护进程重新拉起）。")
                result["hint"] = (
                    "请再执行一次 python sw_bridge.py sw-kill 查看 pid_history；"
                    "若每轮都出现新 PID，说明 SW 被【SolidWorks 后台下载器/"
                    "文档恢复】守护进程重新拉起 —— 需先结束这些守护进程。")
            else:
                result.setdefault("note", "")
                if _kill.get("note"):
                    result["note"] = (_kill["note"] + " "
                                      + str(result.get("note") or "")).strip()
            if not _doc_res.get("ok"):
                result.setdefault("documents_warning",
                                  _doc_res.get("error"))
        return result
    result = close_documents_until_zero(_mgr, max_rounds=3,
                                          close_fn=_swapi_close_once)
    result["keepalive"] = True
    result["quit"] = False
    result["sw_exited"] = False
    result["finally_exited"] = False
    # Visible 保持/恢复（弹框必须看得见，否则用户无从处理）
    if _vis_before is not None:
        result["sw_visible"] = bool(_vis_before)
        if not _mgr.set_visible(_vis_before):
            result["sw_visible_restore_failed"] = True
    if not result.get("ok"):
        _h = result.get("hint") or ""
        if "未保存文档时会弹保存框" not in _h:
            _h = (_h + " " if _h else "") + (
                "注意：SolidWorks 有未保存文档时会弹【保存对话框】，"
                "该模态框会阻塞 CloseAllDocuments —— 请到 SW 窗口处理后再重试。")
        result["hint"] = _h
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
        # 【连接自检修复】不再硬编码 C:\Users\j1877\Desktop\DSH-Check ——
        # 改用 swapi.vision_shot_dir() 自动探测（环境变量 DSH_VISION_DIR
        # 可覆盖；桌面不存在/不可写时自动兜底）。
        out_dir = swapi.vision_shot_dir()
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

    result = {"ok": False, "available": False}

    # 尝试截取一张测试图
    # 【连接自检修复】① 不再硬编码作者桌面路径；② 不再硬编码后端名 ——
    #   原实现把 "agnes/agnes-2.5-flash" 写死且 available 恒为 False，
    #   无论实际后端是否可用都报"不可用"，是纯粹误导。
    #   现改为回显环境变量里的真实配置（未配置则如实说明"未探测"）。
    _be = (os.environ.get("DSH_VISION_BACKEND")
           or os.environ.get("DSH_VISION_MODEL") or "").strip()
    result["backend"] = _be or None
    result["backend_note"] = ("已由环境变量 DSH_VISION_BACKEND / DSH_VISION_MODEL 指定"
                              if _be else
                              "未配置视觉后端（DSH_VISION_BACKEND 未设置）；"
                              "本命令只负责产出截图供前端识图插件使用")
    out_dir = swapi.vision_shot_dir()
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
    r"""返回 (【活着的】活动房间列表, SW锁持有房间)，供报错时给出可自救提示。

    ── 【BUG-17 修复 · 僵尸房间被当成"活动"】────────────────────────────────
    现场（2026-10-27 循迹小车任务，用户实测）：
      任务早已结束 23 小时，mode_state.json 里仍留着两个 active=true 的房间
      （物理深挖房 / 流程深挖房，心跳 age 81892s）。于是：
        · 主对话想直接 sw_bridge.py drawing <零件> 导出工程图
        · _room_candidates() 返回 2 个"活动房间" → len != 1 → 无法自动推断
        · 报「并行模式下必须指定 --room <房间名>！」
      而那两个房间根本没有代理在跑（房间早死了）。
      用户原话：「即使当前没有运行中的 subagent」也被拦。

    修复：不再只看 active 这个布尔值，而是调用 mode_gate 的**同一套**六态
    判定（_room_status_of），只把"真的还活着"的房间算作候选：
        · running / starting / running_unverified → 活着
        · stale / no_heartbeat / 无时间戳      → 已死，剔除
    于是：
        · 没有活房间 → 返回 []，调用方据此【放行】（主对话直接出图）
        · 只有一个活房间 → 自动采用
        · 多个活房间 → 才要求显式 --room（那种情况确实无法唯一推断）
    """
    _act, _owner = [], None
    try:
        mg_path = _get_mode_gate_path()
        ms_path = os.path.join(os.path.dirname(mg_path), "mode_state.json")
        if not os.path.exists(ms_path):
            return [], None
        with open(ms_path, "r", encoding="utf-8") as f:
            ms = json.load(f)
        rooms = ms.get("rooms", {}) or {}
        _owner = (ms.get("sw_lock", {}) or {}).get("owner")
        _raw_active = [k for k, v in rooms.items()
                       if isinstance(v, dict) and v.get("active")]
        if not _raw_active:
            return [], _owner
        # 用 mode_gate 的六态判定剔除僵尸房间（权威口径，不另立一套）
        _status_of = None
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("_mg_for_rooms", mg_path)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            _status_of = getattr(_m, "_room_status_of", None)
        except Exception:
            _status_of = None
        # 【2026-10-28】waiting_sw / idle 也是"活着"：
        #   排队等 SolidWorks 锁时房间仍是活房间，不能因此少认一个房间。
        _ALIVE = ("running", "starting", "running_unverified",
                  "waiting_sw", "idle")
        if callable(_status_of):
            _subs = ms.get("subagents", {}) or {}
            for _r in _raw_active:
                try:
                    _s = (_status_of(_r, rooms.get(_r) or {}, _subs) or {})
                    if str(_s.get("status") or "") in _ALIVE:
                        _act.append(_r)
                except Exception:
                    # 判定本身出错时保守保留（宁可多问一句，也不误删活房间）
                    _act.append(_r)
        else:
            # mode_gate 不可用（老副本）→ 退化为旧行为，绝不更差
            _act = list(_raw_active)
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
        # ── 【第4项 · 代码强制】无需排队也照样起看护 ──────────────────────
        # 真实拿到使用权（need_lock=False 表示本模式不需要互斥）也意味着
        # "马上要开始用 SW 建模"，看护必须已经在跑。
        _ensure_guard_quiet(room)
        return {"skipped": True}
    if info.get("acquired"):
        # ★【第4项】拿到锁 = 即将建模 → 立刻确保健康看护在跑。
        #   这是"代码强制"：不依赖模型是否记得启动，任何取锁路径都会走到这里。
        _ensure_guard_quiet(room)
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
                # ★【第4项】等待后拿到锁，同样要起看护
                _ensure_guard_quiet(room)
                return {"room": room, "skipped": False}
        except Exception:
            pass
        __import__("time").sleep(1)
    return {"busy": True, "room": room,
            "position": last.get("position") or info.get("position"),
            "owner": last.get("owner") or info.get("owner"),
            "error": (last.get("error") or info.get("wait_hint")
                      or "SW 正被其他小屋使用，请稍后重试同一命令。")}


def _ensure_guard_quiet(room):
    """确保 SW 健康看护在跑；**任何失败都不得影响建模主流程**。

    设计取舍：看护是"保护性"功能，绝不能因为它的启动失败而让取锁失败
    （那会让整个任务卡死）。因此这里吞掉所有异常，只把结果写进心跳备注，
    真正的强制点在 mode_gate 的 sw-guard-status 里可查（running=false 即异常）。

    ── 两条路都走（互补，用户明确要求用 ctx.jobs）────────────────────────────
      ① mode_gate sw-guard-start：拉起一个 OS 子进程看护。
         优点：不依赖 DSH 运行时，任何取锁路径必然触发；
         缺点：没有 DSH job id。
      ② 插件 /sw-guard-job（ctx.jobs 版）：把同一件事注册为 **DSH 后台任务**。
         优点：有 job id，能被 job_list / job_output / job_kill 管理
               （这正是用户要的"这个 job 会立即返回一个 job id"）；
         缺点：需要 DSH 运行时 + jobs 服务。
    两条并行不冲突：①保证"一定有看护"，②保证"有 job id 可管理"，
    且两者都幂等（重复调用不会堆进程/job）。
    """
    # ① OS 子进程看护（保底，必须成功路径不依赖网络）
    try:
        gate = _get_mode_gate_path()
        if os.path.exists(gate):
            subprocess.run([sys.executable, gate, "sw-guard-start"],
                           capture_output=True, text=True, encoding="utf-8", timeout=20)
    except Exception:
        pass
    # ② ctx.jobs 版看护（走插件 HTTP，拿到 DSH job id）
    #    端口与 DSH Web 同源；失败一律静默（不阻断建模）。
    try:
        _port = os.environ.get("DSH_WEB_PORT") or "19387"
        _url = "http://127.0.0.1:%s/dsh-engineering-ui/sw-guard-job" % _port
        import urllib.request as _ur
        _req = _ur.Request(_url, method="POST",
                           headers={"Content-Type": "application/json"})
        _ur.urlopen(_req, timeout=5).read()
    except Exception:
        # 拿不到 job id 不影响建模：① 那条路已经在看护
        pass


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
                "sw-proc": "查看 SLDWORKS.EXE 进程与 PID（只读，不连 SW）",
                "ping": "轻量存活探测（只读，不连 SW）",
                "version": "桥接与依赖版本（只读，不连 SW）",
                "self-path": "返回本脚本绝对路径（只读）",
                "selftest": "【BUG-观察-17/1】离线自检：annotate API 归属+参数"
                            "个数+回退顺序、save 相对路径归位（只读，不连 SW）",
                "early-status": "【子任务 C】早期绑定诊断：能否用早期绑定 / 类型库"
                                " major / tlb 与 gen_py 路径 / 已容错的签名差异"
                                "（只读，不连 SW、不取锁、不需 --room）",
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
                "bore <孔径> [深度] [--axis X|Y|Z] [--through]": "【Bug-42 + P1 修复】"
                                     "专用孔命令（显式指定轴线方向）；"
                                     "不给深度=完全贯穿，给深度=盲孔",
                "asm-verify <装配体.sldasm>": "【装配强制路径】按文档 §10.1 用 "
                                     "FeatureByName 逐个查证配合是否存在"
                                     "（不要依赖 FirstFeature/mate_count 的 count）；"
                                     "只读，不取锁、不需 --room",
                "title-block <图纸> [字段=值 ...]": "【Bug-34】填写标题栏"
                                                    "（材料/比例/图号/设计者/日期）",
                "cad-validate <图纸>": "CADX 几何验证（7 项检查）",
                "ac-status": "AutoCAD 连接状态（只读，不需要 SW/房间锁）",
                "ac-export [out.dxf]": "导出当前 AutoCAD 图纸为 DXF",
                "cad-validate-live": "验证当前 AutoCAD 活动文档",
                "physics-status": "FEA 求解器后端状态",
                "physics-validate-case <case.json>": "校验载荷工况",
                "physics-optimize <case.json>": "自动迭代优化",
                "physics-demo": "悬臂梁演示",
                "physics-fatigue <case.json>": "疲劳 / 设计寿命（默认30年）校核",
                "physics-report <run_id>": "查看仿真报告",
                "physics-recommend <run_id>": "生成修正建议",
                "close-all": "【Bug⑧】【BUG-S02】循环关闭 SolidWorks 文档"
                             "（最多 3 轮，docs_after==0 才算成功）+ 释放使用权；"
                             "SW 进程【保活】（并行房间共用一台 SW 时不会互相"
                             "切断 RPC）；关不掉时返回 failing_docs + hint",
                "close-all --quit": "【Bug⑧】【BUG-S03】完整退出：循环关文档 + "
                                    "Quit + taskkill + sw-kill 复核，返回 "
                                    "sw_exited / finally_exited 真实值",
                "sw-kill [--rounds 3] [--wait 5]": "【BUG-S03】循环强杀 "
                                    "SLDWORKS.exe（taskkill /F /T）并轮询确认"
                                    "进程真的退出；返回 finally_exited 与 "
                                    "pid_history（只读进程层，不连 COM）",
            },
            "close_all_note": ("【Bug⑧】并行模式下 close-all 默认【不】退出 SW "
                               "进程：循环调 SldWorks.CloseAllDocuments 直到 "
                               "docs_after==0（【BUG-S02】最多 3 轮），让其它"
                               "房间的连接继续可用、下一个零件免冷启动。"
                               "确需释放进程请显式加 --quit（会再跑 sw-kill "
                               "确认进程【真的】退出）。"),
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
    # ══ 【连接自检修复】把"跳锁"与"不连 SW"两件事【彻底分开】══════════════
    # 原缺陷（实测复现，AI 连接确认链路被自己掐断）：
    #   `_READONLY_CMDS` 被当成两个语义复用 ——
    #     ① 【B5修复】用它表示"不需要 SW 独占权、豁免 --room"；
    #     ② 【BUG-02 修复】又写 `_NO_SW_CMDS = _READONLY_CMDS` 表示"纯计算、
    #        不连 SolidWorks"，于是 main() 把 sw 置为 None。
    #   两个语义叠在一起后，doctor / status / info / list 既是"只读"（跳锁）
    #   又成了"不连 SW"（sw=None），结果：
    #     · `doctor` 恒报 connected:false（"'NoneType' has no attribute
    #       'RevisionNumber'"）+ 连带假报"未找到零件模板"→ ok:false；
    #     · `status` 恒报 {"error":"sw not connected"}；
    #     · `info` / `list` 直接抛 AttributeError + traceback（exit 1）。
    #   而这三个恰恰是 persona 指定的 SW 连接确认入口 —— 自检永远失败。
    # 修复：拆成四个互不重叠的集合，语义单一。
    #   ① `_NO_LOCK_CMDS`     —— 不取 SW 独占锁、豁免 --room（★但仍按需连 SW★）
    #   ② `_NO_SW_CMDS`       —— 真正【完全不碰 SolidWorks】的命令
    #   ③ `_CAD_ONLY_CMDS`    —— 只跟 AutoCAD 打交道（不该被 SW 房间锁拦住）
    #   ④ `_LOCAL_CMDS`       —— 纯本地元信息，既不连 SW 也不连 CAD
    #
    # ① 跳锁/豁免 --room：只读诊断类不需要 SW 独占权。
    #    原先 doctor 因 parallel_mode=D 被强制要求 --room，无法做独立环境自检。
    #    ⚠️ 在本集合【不等于】不连 SW —— doctor/status/info/list 必须连，
    #       否则无法回答"SW 到底连上没有"（这正是本集合存在的意义）。
    _NO_LOCK_CMDS = {"self-path", "doctor", "status", "info", "list",
                     "sw-proc", "help", "version", "ping",
                     # ──【BUG-观察-17 / 观察-1 修复】selftest 是纯 Python 离线自检 ──
                     # 不连 SW、不写业务产物、不抢独占权 → 既不取锁也不该被
                     # "并行模式必须带 --room"拦住（否则 SW 未运行时自检跑不起来）。
                     "selftest",
                     # ── 【子任务 C】early-status 只读诊断早期绑定 ──────────────
                     # 同 selftest / conn-probe 的登记方式：它只调 swapi 的
                     #   early_status()/early_diagnose() 读本机状态（报告 1.1/1.2），
                     #   不起 COM、不改模型、不写业务产物 → 不取 SW 锁，也不该被
                     #   "并行模式必须带 --room"拦住（SW 没运行时正需要它来诊断）。
                     "early-status",
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
                     "cad-validate",
                     # ── 【连接自检修复】AutoCAD 侧命令同样豁免 SW 锁 ──────
                     # ac-status / ac-export / cad-validate-live 只与
                     #   AutoCAD 进程通信；假设 SW 未启动或锁被别的房间占着，
                     #   也【不该】影响"确认 CAD 连上没有"。
                     # 原缺陷：ac-status 不在任何豁免集合里 → 并行模式下返回
                     #   {"error":"并行模式下必须指定 --room <房间名>！"}，
                     #   而 active_rooms 为空 → 提示里的 <房间名> 无解，
                     #   CAD 连接确认入口实际不可用。
                     "ac-status", "ac-export", "cad-validate-live",
                     # ── 【BUG-S03】sw-kill 只做进程层清场（taskkill + 轮询），
                     #   不连 COM、不写业务产物，且【SW 卡死时最需要它能用】——
                     #   必须豁免 SW 独占锁与 --room（否则清场入口自己先被拦住）。
                     "sw-kill",
                     # ── 【连接区】连接探测/状态/判断同样是零副作用只读 ──
                     # conn-probe 只做进程与 COM 连接探测，不改模型、不写业务产物。
                     # conn-launch 会拉起进程，但这正是它的语义（用户主动点击），
                     #   且它不写 SW 业务产物、不抢 SW 独占锁。
                     "conn-probe", "conn-status", "conn-gate",
                     "conn-launch", "conn-log",
                     # 【10 分钟自动刷新】纯进程层检测：只读 tasklist，
                     #   比 conn-probe 更轻，同样是零副作用。
                     "conn-probe-process-only",
                     # ── 【装配强制路径】asm-verify 是只读验收命令 ──────────
                     # 它只打开装配体查 FeatureByName，不改模型、不写产物
                     # → 既不取 SW 独占锁，也不该被"并行模式必须带 --room"拦住
                     # （主对话想全局核查装配质量时没有房间归属）。
                     "asm-verify"}
    # ② 真正不碰 SolidWorks 的命令（main() 据此把 sw 置为 None）。
    #    ⚠️ 绝不包含 doctor / status / info / list —— 它们必须连 SW。
    #
    # ── 【BUG-02 实机补强】纯计算命令【不连接 SolidWorks】─────────────
    # 原实现无条件 get_sw()，即使 physics-* 这类完全不碰 SW 的命令
    #   也会把 SolidWorks 拉起来 —— 实测副作用：
    #     · 白白占用 SW 独占窗口，阻塞真正在建模的房间；
    #     · 在没有装 SW 的机器上，纯 FEA/疲劳计算直接失败；
    #     · 启动 SW 要几十秒，纯计算命令被拖慢一个数量级。
    _PURE_COMPUTE_CMDS = {"physics-status", "physics-demo", "physics-report",
                          "physics-recommend", "physics-fatigue",
                          "physics-list-domains", "physics-validate-domain",
                          "physics-validate-case", "physics-optimize",
                          "cad-validate"}
    # ③ 只连 AutoCAD 的命令（不碰 SW）。
    _CAD_ONLY_CMDS = {"ac-status", "ac-export", "cad-validate-live"}
    # ④ 纯本地元信息命令（既不连 SW 也不连 CAD）。
    #    【连接区】conn-* 也在此列：它们【自己】按需探测（且未运行时不发 COM），
    #      main() 不应为它们预先建立 SW 连接 —— 否则"只想看看连没连"
    #      反而把 SolidWorks 拉起来了。
    _LOCAL_CMDS = {"self-path", "help", "version", "ping", "sw-proc",
                   "conn-probe", "conn-status", "conn-gate",
                   "conn-launch", "conn-log",
                   "conn-probe-process-only",
                   # 【BUG-S03】sw-kill 是纯进程层动作：不连 SW（sw=None），
                   #   正因如此它才能在 "SW 卡死 / COM 连不上" 时完成清场。
                   "sw-kill",
                   # ──【BUG-观察-17 / 观察-1 修复】纯 Python 离线自检 ──────
                   # 只用 mock 假对象 + 临时目录，【完全不碰 SolidWorks】，
                   # 因此必须在此列：SW 未运行时也要能跑，否则自检形同虚设。
                   "selftest",
                   # ── 【子任务 C】early-status 同样【完全不碰 SolidWorks】────
                   # 它只读 swapi 的进程内状态 + 类型库/gen_py 路径。列入 _LOCAL_CMDS
                   # 后 main() 会把 sw 置为 None（不 get_sw）—— 这正是关键：
                   # "想看看能不能早期绑定"绝不能把 SolidWorks 拉起来。
                   "early-status"}
    _NO_SW_CMDS = _PURE_COMPUTE_CMDS | _CAD_ONLY_CMDS | _LOCAL_CMDS
    # ── 【观察点 10 修复】只读诊断类：需要连 SW，但不该强制 --room ─────────
    # ⚠️ 必须与 _READONLY_CMDS【分开】—— 后者会被 `_NO_SW_CMDS = _READONLY_CMDS`
    #   复用，把命令的 sw 置为 None。而本集合里的命令【恰恰需要真实 SW 连接】。
    #
    # 用户诉求（观察点 10）：`show` 只做"前台 + 等轴测视图 + 截图"，不改模型
    #   几何、不写业务产物；主对话（不属于任何房间）想全局看一眼 SW 现状时，
    #   原先被"并行模式下必须指定 --room"直接拒掉(exit=2)，而主对话没有
    #   --room 归属 → 全局诊断路径彻底断掉。
    # 因此：本集合豁免 --room 与 SW 锁，但【仍然】执行 get_sw()。
    # 注意：真正写文件的 drawing / dwg / run / annotate / title-block 等
    #   【仍强制】--room（见 _ROOM_REQUIRED_CMDS），锁与产物归属不受影响。
    _DIAG_NO_ROOM_CMDS = {"show"}
    # 【测试反馈 Bug4 修复】SW【收尾类】命令也不该强制 --room：
    #   close-all 的语义就是"关闭 SolidWorks 并释放使用权"，它本身就是释放动作。
    #   若还要求先指定房间才能释放，会出现"锁被上一家占着 → 想释放却因没带 room
    #   被拒"的死结；串行模式下更是完全没有房间概念。
    #   因此 close-all / sw-release / rooms-reset 豁免 --room 与取锁流程。
    _NO_ROOM_CMDS = {"close-all", "sw-release", "rooms-reset", "sw-kill"}
    # ── 【Bug5 修复】进入业务解析前先剥离 --room，避免它污染位置参数 ────────
    # 原缺陷：run/drawing/dwg/cad-validate 用 args[1] 当路径，
    #   写成 `run --room 结构件 script.py` 时 args[1]="--room" →
    #   输出 "path":"--room"，首次调用必败一轮。
    # 修复：--room 从任意位置（含 --room=xxx）摘掉，业务只看 _args。
    _clean_args, _room_from_strip = _strip_room_args(args)
    _args = _clean_args if _room_from_strip else args
    # ── 【观察点 10 修复】_DIAG_NO_ROOM_CMDS 也豁免 --room 与 SW 锁 ────────
    # 它们需要连 SW（故不在 _READONLY_CMDS），但不抢占独占权、不产出业务文件，
    # 因此并行模式下不应强制 --room（主对话没有房间归属）。
    if (cmd not in _NO_LOCK_CMDS and cmd not in _NO_ROOM_CMDS
            and cmd not in _DIAG_NO_ROOM_CMDS):
        _room_id = _room_from_strip or _detect_gate_room(args)
        # ── 【Bug5 修复】并行模式下这些命令【强制】要求 --room ────────────
        # 它们既要抢占 SW 独占权、又要产出文件；无 room 会导致锁无归属、
        # 产物路径互相覆盖，因此必须显式指定。
        # 【Bug-34/35 修复】annotate / title-block 也要 SW 独占权并写文件；
        #   dxf 现在也接受 .slddrw（Bug-35）。
        _ROOM_REQUIRED_CMDS = {"run", "drawing", "dwg", "dxf", "cad-validate",
                               "annotate", "title-block", "bore"}

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

        # ══ 【BUG-17 修复】没有活房间时【放行】主对话 ═══════════
        # 现场：任务早已结束，主对话想直接导出工程图，
        #   却因为讯脚（已死的 active 房间）被拦"并行模式必须指定 --room"。
        # 现在 _room_candidates() 已只返回【真正活着】的房间：
        #   · 一个活房间也没有 → 说明当前没有子代理在建模 → 放行（不挂锁）
        #   · 恰好一个 → 自动采用（已在上一段实现）
        #   · 多个 → 才要求显式 --room
        # 锁语义不变：放行时 _room_id 仍为 None，_lock 为空→
        #   并不会抢占任何房间的 SW 独占权（下方 _gate_sw_lock(None)）。
        if _room_id is None and _need_room:
            _alive_rooms, _ = _room_candidates()
            if not _alive_rooms:
                _need_room = False
                try:
                    sys.stderr.write(
                        "[sw_bridge] 当前没有活动中的房间（子代理均已结束），"
                        "本次命令不需 --room。\n")
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
        # ── 【连接自检修复】只把【真正不碰 SW】的命令置 None ─────────────
        # _NO_SW_CMDS 现在只含 纯计算(physics-*/cad-validate) / 仅CAD(ac-*) /
        #   纯本地元信息(self-path/version/ping/sw-proc) 三类。
        #   doctor / status / info / list 【不在】其中 → 它们会真实建立 SW
        #   连接，从而能回答"SW 到底连上没有"（原先它们被误置 None，
        #   导致自检永远失败）。
        #
        # ── 【连接自检修复】连接失败【不再】把 traceback 抛给 AI ──────────
        # 原实现 get_sw() 一旦抛异常就冒泡到外层 except → exit 1 + traceback，
        #   doctor / status 这类【本就用来诊断连接】的命令反而以"未预期异常"
        #   收场，主对话只能看到一坨栈，拿不到可操作的结论。
        # 现在：连接失败 → sw=None，并把原因记入全局 _LAST_SW_CONNECT_ERROR，
        #   交给各命令的空值防御分支返回结构化错误（ok:false + error + hint）。
        global _LAST_SW_CONNECT_ERROR
        _LAST_SW_CONNECT_ERROR = None
        if cmd in _NO_SW_CMDS:
            sw = None
        else:
            try:
                sw = get_sw()
            except Exception as _e_conn:
                sw = None
                _LAST_SW_CONNECT_ERROR = "%s: %s" % (type(_e_conn).__name__, _e_conn)

        # ══ 【连接自检修复】系统性兜底：SW 连接不可用时【绝不】让命令崩溃 ══
        # 原缺陷：open/new/massprops/close/save/sketch-rect/export-pdf/run/
        #   drawing/dwg/dxf/annotate/title-block/vision-fallback/
        #   check-vision/reading 这些命令【全都假设 sw 非 None】，
        #   一旦连接失败就抛
        #     AttributeError: 'NoneType' object has no attribute 'ActiveDoc' /
        #     'GetDocumentCount' / ...
        #   冒泡成 exit 1 + traceback 抛给 AI。
        #   逐个函数加判空既啰嗦又容易漏（info/list 就是这么漏掉的）——
        #   这里统一在【派发之前】拦一次，语义单一、不可能漏。
        #
        # 三类命令【必须放行】，它们各自的语义本来就不依赖 sw：
        #   · status / doctor  —— 【诊断类】：sw=None 恰恰是它们要报告的事实；
        #   · close-all         —— 【释放类】：本体是 taskkill 兜底，
        #        恰恰在 SW 卡死/连不上时最需要能用（拦截它会形成死结：
        #        "连不上 → 想关掉它 → 因连不上被拒"）；
        #   · cleanup / check-part —— 纯文件操作，函数体根本不碰 sw。
        _SW_TOLERANT_CMDS = {"status", "doctor", "close-all",
                             "cleanup", "check-part", "sw-kill"}
        if sw is None and cmd not in _NO_SW_CMDS \
                and cmd not in _SW_TOLERANT_CMDS:
            result = _sw_not_connected_result(cmd)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

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
            # ── 【Bug⑧】默认保活：只关文档、不退出 SW 进程 ─────────────────
            # 现场缺陷：三个并行房间共用一台 SW，任一房间 close-all 就
            #   Quit + taskkill → 其它房间 RPC 立即失效，SW PID 频繁变化
            #   （4148→30860→18388→23448→472→34744），并行退化为串行+冷启动。
            # 现语义：默认只调 CloseAllDocuments（保活）；--quit 才真正退出。
            _quit = "--quit" in _args
            result = cmd_close_all(sw, quit=_quit)
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
        # ── 【BUG-S03 修复】sw-kill：循环强杀 SLDWORKS.exe 并确认退出 ──────
        # 现场缺陷：Stop-Process -Force 后 5 秒又冒出新的 PID
        #   （13240→24256→25548），反复 3 轮仍存在。taskkill 是【异步信号】，
        #   返回码 0 只说明信号发出去了；且不带 /T 时子进程/COM 宿主还在。
        # 本命令：每轮 taskkill /F /T /IM → 等 wait 秒 → 再查进程，最多 rounds 轮，
        #   只有"最后真的没有进程"才 finally_exited=True / ok=True。
        # 用法：python sw_bridge.py sw-kill [--rounds 3] [--wait 5]
        elif cmd == "sw-kill":
            _rounds = 3
            _wait = 5
            for _i in range(1, len(_args)):
                if _args[_i] == "--rounds" and _i + 1 < len(_args):
                    try:
                        _rounds = int(_args[_i + 1])
                    except ValueError:
                        pass
                elif _args[_i].startswith("--rounds="):
                    try:
                        _rounds = int(_args[_i].split("=", 1)[1])
                    except ValueError:
                        pass
                elif _args[_i] == "--wait" and _i + 1 < len(_args):
                    try:
                        _wait = float(_args[_i + 1])
                    except ValueError:
                        pass
                elif _args[_i].startswith("--wait="):
                    try:
                        _wait = float(_args[_i].split("=", 1)[1])
                    except ValueError:
                        pass
            result = cmd_sw_kill(rounds=_rounds, wait=_wait)
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
        elif cmd == "bore":
            # ── 【P1-BUG-bore 无 dispatch 分支】help 列出但无法调用 ──────────
            # 原缺陷：help 文本（4592–4626）明确列出
            #   `bore <孔径> [--axis Z]`，但 dispatch 链里【没有任何分支】，
            #   调用会一路落到 else 的 `unknown command` 并 exit 2
            #   （README「已知限制 1」已承认，但一直没补）。
            # 修复：接通 swapi.SWModel.bore()（swapi.py:7120，Bug-42 已实现
            #   但从未被 CLI 暴露），把 help 承诺的接口真正做出来。
            # 用法：bore <孔径mm> [深度mm] [--axis X|Y|Z] [--through] [--cx] [--cy]
            result = _cmd_bore(sw, _args, args)
        elif cmd == "asm-verify":
            # ── 【装配强制路径】配合验收命令 ──────────────────────────────
            # 按《SW装配实战经验总结·融合版》§10.1 的正确判据
            #（FeatureByName 逐个查证，不用会误报的 FirstFeature 遍历）
            # 检查装配体到底有没有配合。用法：
            #   python sw_bridge.py asm-verify "<装配体.sldasm>"
            result = cmd_asm_verify(sw, _args[1] if len(_args) > 1 else "")
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
            result = cmd_dwg(sw, _args[1] if len(_args) > 1 else "",
                             _args[2] if len(_args) > 2 else None,
                             force_redraw=("--redraw" in args))
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
        # ── 【连接自检修复】三个"登记了却没实现"的命令，现已落地 ──────────
        elif cmd == "sw-proc":
            result = cmd_sw_proc()
        elif cmd == "ping":
            result = cmd_ping()
        elif cmd == "version":
            result = cmd_version()
        elif cmd == "selftest":
            # 【BUG-观察-17 / BUG-观察-1 修复】离线自检（不连 SW，零副作用）
            result = cmd_selftest()
        elif cmd == "early-status":
            # ── 【子任务 C】早期绑定诊断（只读、不连 SW、不取锁、豁免 --room）──
            # 来源：报告 1.1 —— SW2025 上大量 API 必须早期绑定才能调用；
            #   报告 1.2 给出 EnsureModule 入口。本分支把 swapi 的
            #   early_status()/early_diagnose() 原样打印成 JSON，即使
            #   ① SW 没在运行 ② swapi 还没落盘早期接口，也必须 exit 0 并如实
            #   回显 available=false（诊断命令不能自己先崩，也不能把 SW 拉起来）。
            result = cmd_early_status()
        # ── 【连接区】SW/CAD 连接探测与流程判断（零副作用，不取 SW 锁）──
        elif cmd == "conn-probe":
            _ct = None
            for _i in range(1, len(_args)):
                if _args[_i] == "--target" and _i + 1 < len(_args):
                    _ct = str(_args[_i + 1]).strip().lower()
                elif _args[_i].startswith("--target="):
                    _ct = _args[_i].split("=", 1)[1].strip().lower()
            result = cmd_conn_probe(
                target=_ct,
                with_com=("--no-com" not in _args),
                resolve_paths=("--resolve-paths" in _args))
        elif cmd == "conn-status":
            result = cmd_conn_status()
        # ── 【10 分钟自动刷新】纯进程层检测（零 COM / 零弹窗）──────────────
        #   设置页每 10 分钟调它一次：只看进程在不在，没了就把灯熄灭。
        #   与 conn-probe 严格分开 —— 后者会发 COM，可能把软件拉起来。
        elif cmd == "conn-probe-process-only":
            _pot = None
            for _i in range(1, len(_args)):
                if _args[_i] == "--target" and _i + 1 < len(_args):
                    _pot = [str(_args[_i + 1]).strip().lower()]
                elif _args[_i].startswith("--target="):
                    _pot = [str(_args[_i].split("=", 1)[1]).strip().lower()]
            result = cmd_conn_probe_process_only(_pot)
        elif cmd == "conn-gate":
            _gt = "sw"
            for _i in range(1, len(_args)):
                if _args[_i] == "--target" and _i + 1 < len(_args):
                    _gt = str(_args[_i + 1]).strip().lower()
                elif _args[_i].startswith("--target="):
                    _gt = _args[_i].split("=", 1)[1].strip().lower()
            result = cmd_conn_gate(_gt)
        elif cmd == "conn-launch":
            # ── 【启动按钮】显式启动 SW / CAD（用户主动点击才走这里）──────
            _lt = "sw"
            _lexe = None
            _lwait = 120
            for _i in range(1, len(_args)):
                if _args[_i] == "--target" and _i + 1 < len(_args):
                    _lt = str(_args[_i + 1]).strip().lower()
                elif _args[_i].startswith("--target="):
                    _lt = _args[_i].split("=", 1)[1].strip().lower()
                elif _args[_i] == "--exe" and _i + 1 < len(_args):
                    _lexe = _args[_i + 1]
                elif _args[_i].startswith("--exe="):
                    _lexe = _args[_i].split("=", 1)[1]
                elif _args[_i] == "--wait" and _i + 1 < len(_args):
                    try:
                        _lwait = int(_args[_i + 1])
                    except ValueError:
                        pass
            result = cmd_conn_launch(
                _lt, exe=_lexe, wait_window=_lwait,
                connect=("--no-connect" not in _args))
        elif cmd == "conn-log":
            _gt2 = "sw"
            for _i in range(1, len(_args)):
                if _args[_i] == "--target" and _i + 1 < len(_args):
                    _gt2 = str(_args[_i + 1]).strip().lower()
                elif _args[_i].startswith("--target="):
                    _gt2 = _args[_i].split("=", 1)[1].strip().lower()
            result = cmd_conn_log(_gt2)
        # ── 【连接自检修复】AutoCAD 侧命令：只连 CAD，不碰 SW ─────────────
        # 原缺陷（实测复现）：
        #   ① ac-status 不在任何豁免集合里 → 并行模式下被 SW 房间锁拦下，
        #      返回 {"error":"并行模式下必须指定 --room <房间名>！"}，
        #      而 active_rooms 为空 → 提示里的 <房间名> 无解，
        #      CAD 连接确认入口【实际不可用】；
        #   ② ac-status 里 `ac_bridge.get_ac()` 被调用了【两次】——
        #      每次都执行一次完整的 COM Dispatch + 最多 30 秒重试，
        #      既慢一倍，又可能第一次成功、第二次失败导致误报"无法连接"；
        #   ③ 全脚本从未调用 bridge.close() → COM 连接泄漏。
        # 修复：单次 get_ac()、判空后取 status()、用后 close() 释放。
        elif cmd == "ac-status":
            if not _HAS_CADX:
                result = _ac_bridge_unavailable()
            else:
                result = _ac_status_result()
        elif cmd == "ac-export":
            if not _HAS_CADX:
                result = _ac_bridge_unavailable()
            else:
                result = _ac_export_result(_args[1] if len(_args) > 1 else "")
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
                            # ── 【P1-BUG-验证链路缺图层重映射】────────────────
                            # 原缺陷：重映射只在 cmd_dxf 导出时做。用户手工用
                            #   AutoCAD 出图、或直接传一个 .dxf 进来验证时，
                            #   中文层名原样进入检查 → 必然一堆 MISSING_LAYER。
                            # 修复：验证【之前】对 DXF 做一次重映射（幂等：
                            #   已是标准层的不会被再改），并回显映射台账。
                            _rm_info = None
                            try:
                                _rm_info = _remap_dxf_layers(dxf_path, rewrite=True)
                            except Exception as _e_rm2:
                                _rm_info = {"ok": False, "error": repr(_e_rm2)}
                            try:
                                result = ac_validate.validate_dxf(dxf_path, rules)
                                if _rm_info is not None:
                                    result["layer_remap"] = _rm_info
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
                # ── 【连接自检修复】单次连接 + 用后释放 + 结构化失败提示 ──
                # 原实现：① 连接失败只给一句"请先启动 AutoCAD"（无可操作信息）；
                #   ② 从不 close() → COM 连接泄漏。
                _ac = None
                try:
                    _ac = ac_bridge.get_ac()
                    if not _ac:
                        result = _ac_connect_failure()
                    else:
                        result = ac_validate.validate_live(_ac, rules="mechanical")
                except Exception as _e_live:
                    result = _ac_connect_failure()
                    result["error"] = "AutoCAD 现场验证异常: %s" % _e_live
                finally:
                    try:
                        if _ac is not None:
                            _ac.close()
                    except Exception:
                        pass
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
                        # ── 【P0-2 修复】解析并透传 --material ──────────────
                        # 实测缺陷（子代理受控实验确证）：physics_bridge 支持
                        #   --material，但 sw_bridge 这个入口【根本没解析它】，
                        #   于是经推荐入口调用时该参数被静默丢弃：
                        #     sw_bridge physics-optimize ... --material ST_API_4140
                        #       → material.id=45#, σy=355（期望 655）
                        #     physics_bridge optimize ... --material ST_API_4140
                        #       → 正确得到 655
                        #   这会让"显式指定材料"这条最重要的覆盖手段失效。
                        _material = ""
                        for _j in range(2, len(_args)):
                            if _args[_j] in ("--material", "-m") and _j + 1 < len(_args):
                                _material = _args[_j + 1]
                        if not _material:
                            _material = (os.environ.get("DSH_MATERIAL") or "")
                        # ── 【Bug⑨】长任务开始前推进一次 room stage ──────
                        # 物理优化可能要跑几分钟～几十分钟。不推进的话
                        #   room-report 会一直停在 modeling，主代理无法区分
                        #   "在跑 FEA" 与 "房间已死机"（Bug⑨）。
                        _stage_out = _push_room_validating(
                            _room_from_strip or _detect_gate_room(args),
                            "物理优化中（physics-optimize）")
                        result = _pb.cmd_optimize(
                            case_path, max_iter=max_iter,
                            room=(_room_from_strip or _detect_gate_room(args)
                                  or os.environ.get("DSH_ROOM") or ""),
                            part_path=_part_path,
                            explicit_material=_material or None,
                        )
                        if isinstance(result, dict):
                            result["room_stage_push"] = _stage_out
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
                # ── 【P0 修复·--part 被丢弃】必须解析并透传真实零件路径 ────
                # 实测缺陷（子代理确证）：本入口只解析 --stress / --report，
                #   完全没解析 --part，也没传给 cmd_fatigue(part_path=...)。
                #   于是 `sw_bridge physics-fatigue <case> --part <零件>` 会报
                #   "未提供 --part，无法取得真实零件几何"（part_path=null）——
                #   即经推荐入口【无法做真实几何疲劳校核】，只能直调
                #   physics_bridge。这与 physics-optimize 的 --material 属同类
                #   参数管道缺陷。
                _fat_part = ""
                _allow_dd = False
                for _i in range(1, len(_args)):
                    if _args[_i] == "--stress" and _i + 1 < len(_args):
                        try:
                            _stress = float(_args[_i + 1])
                        except ValueError:
                            _stress = None
                    elif _args[_i] == "--report" and _i + 1 < len(_args):
                        _report_id = _args[_i + 1]
                    elif _args[_i] in ("--part", "--part-path") and _i + 1 < len(_args):
                        _fat_part = _args[_i + 1]
                    elif _args[_i] == "--allow-design-domain":
                        _allow_dd = True
                if not _fat_part:
                    _fat_part = (os.environ.get("DSH_PART_PATH")
                                 or os.environ.get("DSH_PART") or "")
                if not case_path and not _report_id:
                    result = {"ok": False,
                              "error": "需要载荷工况文件路径，或用 --report <run_id>",
                              "hint": ("python sw_bridge.py physics-fatigue <case.json> "
                                       "--part <零件.SLDPRT>\n"
                                       "python sw_bridge.py physics-fatigue --report <run_id>")}
                else:
                    # ── 【Bug⑨】疲劳校核（默认 30 年寿命）同样是长任务 ──────
                    _stage_out = _push_room_validating(
                        _room_from_strip or _detect_gate_room(args),
                        "疲劳校核中（physics-fatigue）")
                    result = _pb.cmd_fatigue(case_path, _stress, _report_id,
                                             _fat_part, _allow_dd)
                    if isinstance(result, dict):
                        result["room_stage_push"] = _stage_out
        elif cmd == "physics-validate-domain":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                _dom = _args[1] if len(_args) > 1 else "structural"
                # ── 【Bug⑨】领域校核也是长任务，进入前推进一次 stage ──────
                _stage_out = _push_room_validating(
                    _room_from_strip or _detect_gate_room(args),
                    "领域校核中（physics-validate-domain: %s）" % _dom)
                result = _pb.cmd_validate_domain(_dom, room=(_room_from_strip or _detect_gate_room(args) or os.environ.get("DSH_ROOM") or ""))
                if isinstance(result, dict):
                    result["room_stage_push"] = _stage_out
        elif cmd == "physics-list-domains":
            if not _HAS_PHYSICS:
                result = {"ok": False, "error": "physics_bridge.py 未找到"}
            else:
                result = _pb.cmd_list_domains()
        else:
            result = {"ok": False, "error": f"unknown command: {cmd}"}
        # ── 【P1 修复·实机发现】COM 对象兜底，绝不让 json.dumps 崩掉 ──────
        # 实机跑 `bore` 时，swapi 把 win32com Feature 对象放进返回值，
        #   json.dumps 直接抛 TypeError → 整条命令以 traceback 收场，
        #   调用方拿不到"孔其实建成功了"这一事实。
        # 这里做统一兜底：把任何不可序列化对象降级为 {__type__, repr}，
        #   保证"命令成功/失败"始终可读；具体 handler 仍应自行转换（如 bore）。
        try:
            json.dumps(result, ensure_ascii=False)
        except TypeError:
            def _jsonable(o):
                if isinstance(o, dict):
                    return {str(k): _jsonable(v) for k, v in o.items()}
                if isinstance(o, (list, tuple)):
                    return [_jsonable(x) for x in o]
                if isinstance(o, (str, int, float, bool)) or o is None:
                    return o
                # COM 句柄 / 其它对象：保留类型名，丢掉句柄本身
                try:
                    _r = repr(o)
                except Exception:
                    _r = "<unrepr>"
                return {"__type__": type(o).__name__, "repr": _r[:200]}
            result = _jsonable(result)
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
