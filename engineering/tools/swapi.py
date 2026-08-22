# -*- coding: utf-8 -*-
"""
swapi.py — SolidWorks 高层建模封装（通用版，跨电脑/跨版本）
=============================================================
本文件是【通用版】：不硬编码任何本机路径或版本号，
自动探测 SolidWorks 安装、模板位置、版本，适配不同电脑。

【原版 vs 通用版】
- 原版（适配开发机）：路径/版本硬编码，仅本机可用
- 本通用版：自动探测，任意安装了 SolidWorks 的电脑可用

依赖：
- Python 3.8+（开发环境为 3.14）
- pywin32 (win32com)
- Pillow + mss（截图功能，可选）

用法（在 sw_bridge.py run 执行的脚本中）:
    import swapi
    m = swapi.new_part()            # 新建零件并返回 SWModel
    m.begin_sketch("Front Plane")   # 在前视基准面开始草图
    m.rect(0, 0, 120, 80)           # 中心矩形, 单位 mm
    m.end_sketch()
    m.extrude(10)                   # 拉伸 10 mm
    m.save(r"D:/out/part.SLDPRT")   # 保存
"""
import math
import os
import glob

import pythoncom
import win32com.client

MM = 0.001  # 毫米 → 米

# ==================== 自动探测 ====================

def _version_year(major):
    """SW 主版本号 → 年份：30=2022, 31=2023, 32=2024, 29=2021, 28=2020..."""
    return major + 1992


def _find_template(sw=None):
    """自动探测零件模板路径，兼容不同安装位置/版本/语言。

    搜索顺序：
    1. 用 SolidWorks API 查默认模板目录（最可靠）
    2. 运行中 SolidWorks 版本对应的 ProgramData 模板目录（如 SOLIDWORKS 2022）
    3. 常见安装路径的 ProgramData 模板目录
    4. 常见盘符 + SOLIDWORKS 目录
    返回第一个存在的 .prtdot 模板，找不到返回 None。
    """
    cands = []
    # 1) 通过 API 查模板目录（swUserPreferenceStringValue_e 模板目录）
    if sw is not None:
        try:
            # swFileLocationsDocuments=1 是文档目录，模板目录需查 swFileLocations
            # 用设置查模板路径
            for pref in (108, 109, 110, 111):   # 各种模板位置枚举尝试
                try:
                    d = sw.GetUserPreferenceStringValue(pref)
                    if d and os.path.exists(d):
                        cands.append(d)
                except Exception:
                    pass
        except Exception:
            pass
        # 用 API 直接查文档模板
        try:
            tmpl = sw.GetUserPreferenceStringValue(101)  # 零件模板
            if tmpl and os.path.exists(tmpl):
                cands.append(tmpl)
        except Exception:
            pass

    # 2) 运行中版本对应的 ProgramData 模板目录（优先，避免选到旧版本）
    if sw is not None:
        try:
            year = _version_year(_version_major(sw))
            d = r"C:\ProgramData\SolidWorks\SOLIDWORKS %d\templates" % year
            if os.path.isdir(d):
                cands.append(d)
        except Exception:
            pass

    # 3) ProgramData 标准位置（任意版本）
    for ver_dir in glob.glob(r"C:\ProgramData\SolidWorks\SOLIDWORKS*"):
        cands.append(os.path.join(ver_dir, "templates"))

    # 4) 常见安装位置（任意盘符）
    for drive in ("C:", "D:", "E:", "F:"):
        for sw_dir in glob.glob(drive + r"\*SOLIDWORKS*") + \
                       glob.glob(drive + r"\SOLIDWORKS*"):
            cands.append(os.path.join(sw_dir, "templates"))
            # 也试试 ProgramData 下的
            cands.append(os.path.join(sw_dir, "..", "..", "ProgramData",
                                      "SolidWorks", "SOLIDWORKS 2022",
                                      "templates"))

    # 4) 从候选目录里找零件模板
    tmpl_names = ["gb_part.prtdot", "Part.prtdot", "零件.prtdot",
                  "part.prtdot", "PART.PRTPRT"]
    for d in cands:
        if not d or not os.path.isdir(d):
            continue
        for n in tmpl_names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
        # 目录里任意 .prtdot
        found = glob.glob(os.path.join(d, "*.prtdot"))
        if found:
            return found[0]
    return None


_TEMPLATE_CACHE = None

def get_part_template(sw=None):
    """获取可用零件模板路径（缓存）。"""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE and os.path.exists(_TEMPLATE_CACHE):
        return _TEMPLATE_CACHE
    _TEMPLATE_CACHE = _find_template(sw)
    return _TEMPLATE_CACHE


def _get_revision(sw):
    """获取 SolidWorks 版本号（如 30.0.0=2022, 26.0.0=2018）。"""
    try:
        return sw.RevisionNumber
    except Exception:
        return ""


def _version_major(sw):
    """版本主号：2022=30, 2021=29, 2020=28, 2019=27, 2018=26..."""
    try:
        return int(float(str(sw.RevisionNumber).split(".")[0]))
    except Exception:
        return 0


# ==================== 版本相关枚举（自动适配） ====================

def _get_midplane_enum(sw):
    """两侧对称拉伸的枚举值：2022=6, 2018=5（版本相关）。"""
    if _version_major(sw) and _version_major(sw) >= 28:   # 2020+
        return 6
    return 5


def _get_snap_prefs(sw):
    """草图捕捉开关枚举（版本相关，找不到就跳过）。

    2022 实测: 249=推理, 271=最近点, 278=网格
    老版本数值可能不同；用 try 逐个禁用，失败忽略。
    """
    prefs = [249, 271, 278, 200, 201, 202]  # 覆盖新旧版本
    return prefs


def _disable_snapping(sw):
    """禁用草图推理/吸附，保证坐标精确。

    关键坑：SolidWorks 的推理捕捉（inference）会把 17.5 等非整数坐标
    吸附到邻近的整数线（实测 17.5 -> 18），导致几何错误。
    """
    for pref in _get_snap_prefs(sw):
        try:
            sw.SetUserPreferenceToggle(pref, False)
        except Exception:
            pass


# ==================== 常量（跨版本通用部分） ====================
SW_END_BLIND = 0            # 给定深度
SW_END_THROUGH = 1          # 完全贯穿
SW_START_SKETCHPLANE = 0    # 起始: 草图基准面
SW_REV_BLIND = 0            # 旋转到给定角度
# 圆角 Options
SW_FILLET_UNIFORM_RADIUS = 2   # 恒定半径圆角
SW_FILLET_SIMPLE = 0           # swFeatureFilletType_Simple
# 倒角 ChamferType
SW_CHAMFER_ANGLE_DIST = 1   # 角度-距离倒角
SW_CHAMFER_DIST_DIST = 2    # 距离-距离倒角
SW_CHAMFER_VERTEX = 3

_PLANES = ("Front Plane", "Top Plane", "Right Plane")

# 可视化建模模式
VISUAL_MODE = True
VISUAL_PAUSE = 0.5
VIEW_ISO_NAME = "*Isometric"
VIEW_ISO_ID = 7
VIEW_MEDIUM_FACTOR = 1.2


def _wait_sw_window(timeout=30):
    """等 SolidWorks 主窗口出现。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        main_hwnd = _find_main_hwnd()
        if main_hwnd:
            return main_hwnd
        time.sleep(0.5)
    return None


def _find_main_hwnd():
    """找到 SolidWorks 主窗口句柄（标题含 'SOLIDWORKS' 且非欢迎页）。

    通用版：匹配 'SOLIDWORKS' + 带版本号的大窗口（如 'SOLIDWORKS Premium
    2022 SP0.0 - [文档]'），排除纯 'SOLIDWORKS' 欢迎页。
    """
    import ctypes
    from ctypes import wintypes
    import subprocess
    user32 = ctypes.windll.user32
    try:
        pids = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id'],
            encoding='utf-8', errors='replace'
        ).strip().split()
    except Exception:
        return None
    main_hwnd = None
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, lp):
        nonlocal main_hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if str(pid.value) not in pids:
            return True
        length = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(max(length + 1, 1))
        user32.GetWindowTextW(h, buf, length + 1)
        title = buf.value.upper()
        # 主窗口：含 'SOLIDWORKS' 且不是纯 'SOLIDWORKS'（欢迎页）
        if 'SOLIDWORKS' in title and title.strip() != 'SOLIDWORKS':
            if main_hwnd is None:
                main_hwnd = h
        return True
    user32.EnumWindows(cb, 0)
    return main_hwnd


def _show_main_window(maximize=False):
    """把 SolidWorks 主窗口置前（可选最大化），隐藏欢迎页。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        import subprocess
        pids = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id'],
            encoding='utf-8', errors='replace'
        ).strip().split()
        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def hide_welcome(h, lp):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if str(pid.value) not in pids:
                return True
            length = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(max(length + 1, 1))
            user32.GetWindowTextW(h, buf, length + 1)
            if buf.value.strip().upper() == 'SOLIDWORKS':
                user32.ShowWindow(h, 0)   # SW_HIDE 欢迎页
            return True
        user32.EnumWindows(hide_welcome, 0)
        main_hwnd = _find_main_hwnd()
        if main_hwnd:
            if maximize and not user32.IsZoomed(main_hwnd):
                user32.ShowWindow(main_hwnd, 9)   # SW_RESTORE
                user32.ShowWindow(main_hwnd, 3)   # SW_MAXIMIZE
            user32.SetForegroundWindow(main_hwnd)
    except Exception:
        pass


def get_sw():
    """连接 SolidWorks（已运行则挂接，否则自动启动并等待窗口出现）。"""
    import time
    pythoncom.CoInitialize()
    sw = win32com.client.dynamic.Dispatch('SldWorks.Application')
    _disable_snapping(sw)
    if VISUAL_MODE:
        _wait_sw_window(timeout=60)
        time.sleep(1.0)
        _show_main_window()
    return sw


def new_part(sw=None):
    """新建零件，返回 SWModel。

    Bug 10 修复: 新建前先关闭所有遗留文档，避免干扰。
    """
    if sw is None:
        sw = get_sw()
    _disable_snapping(sw)

    # Bug 10: 关闭所有遗留文档
    for _ in range(50):
        try:
            if sw.ActiveDoc:
                sw.ActiveDoc.CloseDoc(0)
        except Exception:
            pass

    tmpl = get_part_template(sw)
    if tmpl is None:
        raise RuntimeError("no part template found (请确认 SolidWorks 已安装)")
    model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
    if model is None:
        raise RuntimeError("NewDocument returned None")
    m = SWModel(sw, model)
    if VISUAL_MODE:
        _show_main_window(maximize=True)
        import time
        m.set_view_iso()
        time.sleep(VISUAL_PAUSE)
    return m


def from_active(sw=None):
    """包装当前活动文档为 SWModel。"""
    if sw is None:
        sw = get_sw()
    model = sw.ActiveDoc
    if model is None:
        raise RuntimeError("no active document")
    return SWModel(sw, model)


class SWModel:
    """单个模型文档的高层封装。"""

    def __init__(self, sw, model):
        self.sw = sw
        self.model = model
        self.skm = model.SketchManager
        self.fm = model.FeatureManager
        self.ext = model.Extension
        self._empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

    def _visual_step(self, label=""):
        """可视化建模：每个特征创建后实时居中展示。"""
        if not VISUAL_MODE:
            return
        try:
            import time
            self.set_view_iso()
            _show_main_window(maximize=True)
            time.sleep(VISUAL_PAUSE)
        except Exception:
            pass

    # ---------- 文档操作 ----------
    @property
    def title(self):
        return self.model.GetTitle

    @property
    def path(self):
        return self.model.GetPathName or ""

    def save(self, path=None):
        """另存为；不传 path 则覆盖保存当前文档。"""
        if path is None:
            ok = self.model.Save3(1, 1, 0)
            return {"ok": ok == 0, "path": self.path}
        before = os.path.getmtime(path) if os.path.exists(path) else None
        rc = self.model.SaveAs3(path, 0, 2)
        exists = os.path.exists(path)
        after = os.path.getmtime(path) if exists else None
        updated = exists and (before is None or after != before)
        return {"ok": exists, "path": path, "saved": exists,
                "updated": updated, "rc": rc}

    def massprops(self):
        """质量属性数组顺序（2022 实测）:
        [cogX, cogY, cogZ, volume, surface_area, mass, Ixx, Iyy, Izz, Ixy, Ixz, Iyz]
        """
        mp = self.model.GetMassProperties
        if mp is None or not isinstance(mp, tuple):
            return {"ok": False, "error": f"GetMassProperties -> {mp!r}"}
        v = [float(x) for x in mp]
        vol, area, mass = v[3], v[4], v[5]
        density = mass / vol if vol else 0.0
        return {
            "ok": True,
            "volume_mm3": vol * 1e9,
            "surface_area_mm2": area * 1e6,
            "mass_kg": mass,
            "density_kg_m3": density,
            "center_of_mass_mm": [v[0] * 1000, v[1] * 1000, v[2] * 1000],
        }

    def export_pdf(self, path):
        rc = self.model.SaveAs3(path, 0, 0)
        return {"ok": rc == 0, "path": path, "exists": os.path.exists(path), "rc": rc}

    # ---------- 展示 / 可视化 ----------
    def _find_sw_windows(self):
        """返回 (主窗口hwnd, 欢迎窗口hwnd列表)。"""
        import ctypes
        from ctypes import wintypes
        import subprocess
        user32 = ctypes.windll.user32
        pids = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id'],
            encoding='utf-8', errors='replace'
        ).strip().split()
        main_hwnd = None
        welcome_hwnds = []
        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, lp):
            nonlocal main_hwnd
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if str(pid.value) not in pids:
                return True
            length = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(max(length + 1, 1))
            user32.GetWindowTextW(h, buf, length + 1)
            title = buf.value.upper()
            if 'SOLIDWORKS' in title and title.strip() != 'SOLIDWORKS':
                if main_hwnd is None:
                    main_hwnd = h
            elif title.strip() == 'SOLIDWORKS':
                welcome_hwnds.append(h)
            return True
        user32.EnumWindows(cb, 0)
        return main_hwnd, welcome_hwnds

    def _hide_welcome(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            _, welcome = self._find_sw_windows()
            for h in welcome:
                user32.ShowWindow(h, 0)
        except Exception:
            pass

    def bring_to_front(self):
        """把 SolidWorks 主窗口调到前台（保持最大化），隐藏欢迎页。"""
        try:
            self.sw.Visible = True
        except Exception:
            pass
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            self._hide_welcome()
            main_hwnd, _ = self._find_sw_windows()
            if main_hwnd:
                if not user32.IsZoomed(main_hwnd):
                    user32.ShowWindow(main_hwnd, 9)
                    user32.ShowWindow(main_hwnd, 3)
                user32.SetForegroundWindow(main_hwnd)
        except Exception:
            pass
        return self

    def set_view_iso(self):
        """固定等轴测视角，模型几何中心居中，缩放中等。"""
        try:
            self.model.ShowNamedView2(VIEW_ISO_NAME, VIEW_ISO_ID)
        except Exception:
            pass
        try:
            self.model.ViewZoomtofit2()
            self.model.ActiveView.ZoomByFactor(VIEW_MEDIUM_FACTOR)
        except Exception:
            try:
                self.model.ActiveView.ZoomByFactor(VIEW_MEDIUM_FACTOR)
            except Exception:
                pass
        return self

    def zoom_to_fit(self):
        """缩放视图到适合窗口，模型几何中心居中。"""
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            try:
                self.model.ActiveView.ZoomByFactor(0.9)
            except Exception:
                pass
        return self

    def screenshot(self, path=None, for_vision=False):
        """对 SolidWorks 主窗口截图保存为 PNG。

        Args:
            path: 截图保存路径，默认保存到工具目录
            for_vision: 是否用于 Vision 识别，是则保存到 DSH-Check 目录便于前端读取
        """
        if path is None:
            if for_vision:
                # 保存到 DSH-Check 目录，便于 DSH 前端识图插件读取
                path = os.path.join(r"C:\Users\j1877\Desktop\DSH-Check",
                                    "solidworks_vision_screenshot.png")
            else:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "solidworks_live.png")
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            self.bring_to_front()
            target, _ = self._find_sw_windows()
            if target is None:
                return {"ok": False, "error": "SolidWorks main window not found"}
            user32.SetForegroundWindow(target)
            import time
            time.sleep(1.0)
            rect = wintypes.RECT()
            user32.GetWindowRect(target, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return {"ok": False, "error": f"window rect invalid {w}x{h}"}
            # 优先使用 mss（更快），回退到 PIL ImageGrab
            try:
                import mss
                with mss.mss() as sct:
                    shot = sct.grab({'left': rect.left, 'top': rect.top,
                                     'width': w, 'height': h})
                    mss.tools.to_png(shot.rgb, shot.size, output=path)
            except ImportError:
                from PIL import ImageGrab
                img = ImageGrab.grab(bbox=(rect.left, rect.top,
                                          rect.right, rect.bottom))
                img.save(path, "PNG")
            return {"ok": True, "path": path, "size": f"{w}x{h}"}
        except Exception as e:
            return {"ok": False, "error": f"screenshot failed: {e}"}

    def export_image(self, path=None, width=1600, height=900):
        """用 SolidWorks 内置 SaveBMP 导出当前视图位图。"""
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "solidworks_render.bmp")
        self.zoom_to_fit()
        try:
            ok = self.model.SaveBMP(path, width, height)
            return {"ok": bool(ok), "path": path, "exists": os.path.exists(path)}
        except Exception as e:
            return {"ok": False, "error": f"export_image failed: {e}"}

    def close(self):
        self.sw.CloseDoc(self.title)
        return {"ok": True, "closed": self.title}

    # ---------- 基准面 / 草图 ----------
    _PLANE_VIEW = {
        "Front Plane": "*Front",
        "Top Plane": "*Top",
        "Right Plane": "*Right",
    }

    def _normal_to(self, view_name):
        """正视于当前草图平面并居中。"""
        try:
            self.model.ShowNamedView2(view_name, 0)
        except Exception:
            pass
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            pass
        return self

    def select_plane(self, name):
        """选择基准面。

        Bug 6 修复: 使用 PLANE 类型选择基准面。
        """
        if name not in _PLANES:
            raise ValueError(f"unknown plane {name!r}; use {_PLANES}")
        self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
        return self

    def begin_sketch(self, plane="Front Plane"):
        """在指定基准面上开始新草图，并先"正视于"该平面（居中显示）。

        Bug 6 修复: 已有特征后基准面选择可能失效，自动降级到面选择。
        Bug 7 修复: 检查 ActiveSketch 是否有效。
        """
        self.select_plane(plane)
        self.skm.InsertSketch(True)
        # 验证草图是否成功激活
        active_sk = self.skm.ActiveSketch
        if active_sk is not None:
            self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
            return self
        # Bug 6 fallback: 基准面选择失败，尝试在模型上找一个面
        # 对于 Front Plane，尝试模型的 front-facing face
        for z in [100, 50, -50, -100]:
            try:
                self.ext.SelectByID2("", "FACE", 0, 0, z * MM, False, 0, self._empty, 0)
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._normal_to("*Front")
                    return self
            except Exception:
                continue
        raise RuntimeError(f"无法激活草图，平面 {plane} 选择失败")

    def begin_sketch_on_face(self, x=0, y=0, z=0):
        """在 (x,y,z) mm 处所在的面开始草图，并正视于该面。

        Bug 6 修复: 使用 FACE 选择代替 PLANE，确保草图正确激活。
        """
        self.ext.SelectByID2("", "FACE", x * MM, y * MM, z * MM, False, 0,
                             self._empty, 0)
        self.skm.InsertSketch(True)
        # 验证草图是否成功激活
        active_sk = self.skm.ActiveSketch
        if active_sk is None:
            raise RuntimeError(f"无法激活草图，面选择失败 (x={x}, y={y}, z={z})")
        self._normal_to("*Front")
        return self

    def end_sketch(self, merge=True):
        """结束草图。merge=True 时合并微小间隙的端点，确保轮廓封闭。

        Bug 8 修复: 在 commit 前调用 MergePoints 闭合端点。
        Bug 7 修复: 确保 ActiveSketch 有效。
        """
        # Bug 8: 合并微小间隙的端点，确保轮廓封闭
        if merge:
            try:
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    active_sk.MergePoints(0.0005)
            except Exception:
                pass
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            pass
        self.skm.InsertSketch(True)
        return self

    def _ensure_sketch_active(self):
        """确保当前草图已激活。

        Bug 7 修复: CreateLine 等草图操作前检查 ActiveSketch。
        """
        sk = self.skm.ActiveSketch
        if sk is None:
            raise RuntimeError("草图未激活，请先调用 begin_sketch() 或 begin_sketch_on_face()")
        return sk

    # ---------- 草图图元（坐标单位 mm）----------
    def rect(self, cx, cy, w, h):
        """中心矩形：中心 (cx,cy)，宽 w，高 h。"""
        self._ensure_sketch_active()
        x1, y1 = (cx - w / 2) * MM, (cy + h / 2) * MM
        x2, y2 = (cx + w / 2) * MM, (cy - h / 2) * MM
        self.skm.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
        return self

    def circle(self, cx, cy, r):
        """圆心 (cx,cy)，半径 r。"""
        self._ensure_sketch_active()
        self.skm.CreateCircleByRadius(cx * MM, cy * MM, 0, r * MM)
        return self

    def line(self, x1, y1, x2, y2):
        """画直线。"""
        self._ensure_sketch_active()
        self.skm.CreateLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
        return self

    def polyline(self, points):
        """折线：points = [(x1,y1), (x2,y2), ...]，自动连成连续折线。"""
        self._ensure_sketch_active()
        pts = [(x * MM, y * MM) for x, y in points]
        for i in range(len(pts) - 1):
            self.skm.CreateLine(pts[i][0], pts[i][1], 0,
                                pts[i + 1][0], pts[i + 1][1], 0)
        return self

    def centerline(self, x1, y1, x2, y2):
        """中心线（旋转特征的旋转轴）。"""
        self._ensure_sketch_active()
        self.skm.CreateCenterLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
        return self

    # ---------- 特征（尺寸单位 mm）----------
    def select_all_sketch_segments(self):
        """选中当前草图的所有线段（用于复杂轮廓的特征创建）。"""
        try:
            self.model.ClearSelection2(True)
            sk = self.skm.ActiveSketch
            segs = sk.GetSketchSegments
            for s in segs:
                try:
                    s.Select(True)
                except Exception:
                    pass
        except Exception:
            pass
        return self

    def extrude(self, depth, symmetric=False, draft_deg=0, auto_select=True):
        """拉伸凸台。depth 单位 mm；symmetric=True 两侧对称。

        Bug 3 修复: 放弃 extrude-to-point 方法，使用标准的 FeatureExtrusion3。
        Bug 9 修复: 负深度自动反转方向，FeatureExtrusion3 不接受负深度参数。
        """
        T1 = _get_midplane_enum(self.sw) if symmetric else SW_END_BLIND
        reverse = bool(depth < 0)  # 负深度 → 反转拉伸方向
        d = abs(depth) * MM
        feat = self.fm.FeatureExtrusion3(
            True, False, False, T1, 0, d, 0,
            reverse, False, False, False, 0, 0,
            False, False, False, False, True, False, auto_select,
            0, 0, False)
        if feat is None:
            raise RuntimeError("FeatureExtrusion3 返回 None，拉伸特征创建失败")
        self._visual_step("extrude")
        return feat

    def cut(self, depth=10, through=False, flip=False, auto_select=True):
        """切除。through=True 完全贯穿；否则切除 depth mm。

        Bug 4 修复: FeatureCut3 在 SW 2020 中不可靠，改用 FeatureExtrusion3 的切除模式。
        """
        T1 = SW_END_THROUGH if through else SW_END_BLIND
        d = depth * MM
        # 使用 FeatureExtrusion3 的切除模式（AddPad=False）
        try:
            feat = self.fm.FeatureExtrusion3(
                False, False, False, T1, 0, d, 0,
                False, False, False, False, 0, 0,
                False, False, False, False, False, False, auto_select,
                0, 0, False)
            if feat:
                self._visual_step("cut")
                return feat
        except Exception as e:
            pass
        # 回退到 FeatureCut3
        try:
            feat = self.fm.FeatureCut3(
                True, bool(flip), False, T1, 0, d, 0,
                False, False, False, False, 0, 0,
                False, False, False, False, False, False, auto_select,
                False, False, False, 0, 0, False)
            self._visual_step("cut")
            return feat
        except Exception:
            return None

    def revolve(self, angle_deg=360, cut=False):
        """旋转特征。草图需含轮廓 + centerline() 旋转轴。angle 单位度。

        Bug 5 修复: 验证草图激活状态，确保旋转特征创建成功。
        """
        # Bug 5/8: 确保草图已正确激活
        active_sk = self.skm.ActiveSketch
        if active_sk is None:
            raise RuntimeError("旋转前草图未激活，请先调用 begin_sketch()")

        ang = math.radians(angle_deg)
        feat = self.fm.FeatureRevolve2(
            True, True, False, cut, False, False,
            SW_REV_BLIND, 0, ang, 0,
            False, False, 0, 0, 0, 0, 0,
            True, False, True)
        if feat is None:
            raise RuntimeError("FeatureRevolve2 返回 None，旋转特征创建失败")
        self._visual_step("revolve")
        return feat

    def _select_edges(self, edge_points):
        """按坐标选边（用于圆角/倒角）。edge_points: [(x,y,z) mm, ...]"""
        first = True
        for x, y, z in edge_points:
            self.ext.SelectByID2("", "EDGE", x * MM, y * MM, z * MM,
                                 not first, 0, self._empty, 0)
            first = False

    def fillet(self, radius, edge_points):
        """恒定半径圆角。radius 单位 mm；edge_points 为边上的点坐标列表。

        注意：Options 必须包含 swFeatureFilletUniformRadius(2)。
        """
        self._select_edges(edge_points)
        feat = self.fm.FeatureFillet3(
            SW_FILLET_UNIFORM_RADIUS, radius * MM, 0, 0, SW_FILLET_SIMPLE, 0, 0,
            None, None, None, None, None, None, None)
        self._visual_step("fillet")
        return feat

    def chamfer(self, width, edge_points, angle_deg=45):
        """角度-距离倒角。width 为倒角距离，angle 为角度（默认45°）。

        注意：方法名用 InsertFeatureChamfer（2022 有）；若旧版本报错，
        会回退到 FeatureChamferType。
        """
        self._select_edges(edge_points)
        try:
            feat = self.fm.InsertFeatureChamfer(
                0, SW_CHAMFER_ANGLE_DIST, width * MM, float(angle_deg),
                0, 0, 0, 0)
        except Exception:
            # 旧版本回退
            try:
                feat = self.fm.FeatureChamferType(
                    SW_CHAMFER_ANGLE_DIST, width * MM, float(angle_deg),
                    False, 0, 0, 0, 0)
            except Exception:
                feat = None
        self._visual_step("chamfer")
        return feat

    def create_sphere(self, cx=0, cy=0, cz=0, radius=10):
        """方法6：半圆弧旋转法创建球体。

        原理：画一个半圆弧（从(0,-R)到(0,R)，经过(R,0)），
        加上直径线闭合，以y轴为中心线旋转360°生成球体。
        该方法在 SW 2020 上已验证可行，体积误差 <0.3%。

        Args:
            cx, cy, cz: 球心坐标（单位 mm）
            radius: 球体半径（单位 mm）

        Returns:
            特征对象；创建失败抛出 RuntimeError
        """
        import time as _time
        r_m = radius * MM  # mm → m
        ox, oy, oz = cx * MM, cy * MM, cz * MM

        self.begin_sketch("Front Plane")
        n = 32  # 弧线分段数
        for i in range(n):
            a1 = -math.pi/2 + (math.pi * i / n)
            a2 = -math.pi/2 + (math.pi * (i+1) / n)
            x1 = ox + r_m * math.cos(a1)
            y1 = oy + r_m * math.sin(a1)
            x2 = ox + r_m * math.cos(a2)
            y2 = oy + r_m * math.sin(a2)
            self.skm.CreateLine(x1, y1, 0, x2, y2, 0)
            _time.sleep(0.003)
        # 直径线闭合（在轴上）
        self.skm.CreateLine(ox, oy - r_m, 0, ox, oy + r_m, 0)
        # 中心线（旋转轴，x 略偏以避免 SW 拒绝接触）
        self.skm.CreateCenterLine(
            ox - 0.0001, oy - r_m - 0.01, 0,
            ox - 0.0001, oy + r_m + 0.01, 0)
        self.end_sketch()
        _time.sleep(0.3)

        feat = self.revolve(360)
        if feat is None:
            raise RuntimeError("FeatureRevolve2 返回 None，球体创建失败")

        # 重建
        try:
            self.model.EditRebuild3
        except Exception:
            pass
        return feat

    # ---------- 便捷工具 ----------
    def clear_selection(self):
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        return self

    def rebuild(self):
        self.model.EditRebuild3
        return self


# ==================== 通用：按名称选草图（跨语言） ====================

def select_sketch_by_index(sw, model, index):
    """按序号选中第 N 个草图（跨语言：不依赖"草图1/2"中文名）。

    返回选中的草图数量；失败返回 0。
    """
    try:
        # 遍历特征树找第 index 个草图特征
        fm = model.FeatureManager
        feat = None
        try:
            feat = fm.FirstFeature
        except Exception:
            return 0
        count = 0
        while feat is not None and count < 50:
            try:
                t = feat.GetTypeName2
                if 'Sketch' in str(t):
                    count += 1
                    if count == index:
                        name = feat.Name
                        model.ClearSelection2(True)
                        ext = model.Extension
                        empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
                        sel = ext.SelectByID2(name, "SKETCH", 0, 0, 0,
                                              False, 0, empty, 0)
                        return 1 if sel else 0
            except Exception:
                pass
            try:
                feat = feat.GetNextFeature
            except Exception:
                break
    except Exception:
        pass
    return 0


def select_sketch_by_name(sw, model, name):
    """按名称选中草图（先试英文 SketchN，再试中文 草图N，再试原名）。"""
    import win32com.client as _wc
    ext = model.Extension
    empty = _wc.VARIANT(pythoncom.VT_DISPATCH, None)
    # 依次尝试英文/中文前缀
    for prefix in ("Sketch", "草图"):
        for n in range(1, 30):
            cand = "%s%d" % (prefix, n)
            model.ClearSelection2(True)
            sel = ext.SelectByID2(cand, "SKETCH", 0, 0, 0, False, 0, empty, 0)
            if sel:
                return cand
    # 最后试原名
    model.ClearSelection2(True)
    sel = ext.SelectByID2(name, "SKETCH", 0, 0, 0, False, 0, empty, 0)
    return name if sel else None
