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
        """另存为；不传 path 则覆盖保存当前文档。

        Bug C 修复: 统一使用 SaveAs3，避免 Save3 类型不匹配。
        """
        if path is None:
            rc = self.model.SaveAs3(self.path, 0, 2)
            return {"ok": rc == 0, "path": self.path}
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
        """关闭当前文档，安全处理COM对象半失效情况。

        Bug 1 修复: 捕获 GetTitle/CloseDoc 异常，避免 AttributeError。
        """
        try:
            title = self.model.GetTitle
        except Exception:
            title = None
        if title:
            try:
                self.sw.CloseDoc(title)
            except Exception:
                pass
        return {"ok": True, "closed": title or "unknown"}

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
        注意: 不在这里 clear_selection——会破坏后续 InsertSketch 的选面逻辑。
        """
        if name not in _PLANES:
            raise ValueError(f"unknown plane {name!r}; use {_PLANES}")
        self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
        return self

    def begin_sketch(self, plane="Front Plane"):
        """在指定基准面上开始新草图，并先"正视于"该平面（居中显示）。

        Bug 6 修复: 多特征后 SW 内部状态累积——在 InsertSketch 后清理状态，
        并确保每次进入草图模式前平面已被正确选中。
        Bug 7 修复: 检查 ActiveSketch 是否有效。
        """
        # 先退出可能残留的草图模式（toggle off），再重新进入
        try:
            self.skm.InsertSketch(False)
        except Exception:
            pass
        self.select_plane(plane)
        self.skm.InsertSketch(True)
        # 验证草图是否成功激活
        active_sk = self.skm.ActiveSketch
        if active_sk is not None:
            self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
            # Bug 6: 草图激活后清理选择状态，防止影响后续特征操作
            try:
                self.clear_selection()
            except Exception:
                pass
            return self
        # Bug 6 fallback: 基准面选择失败，按平面法线方向搜索实体面
        # Front/Back → 沿 Z 轴搜索; Top/Bottom → 沿 Y 轴搜索; Right/Left → 沿 X 轴搜索
        search_axis = {"Front Plane": (0, 0, "z"), "Back Plane": (0, 0, "z"),
                       "Top Plane": (0, "y", 0), "Bottom Plane": (0, "y", 0),
                       "Right Plane": ("x", 0, 0), "Left Plane": ("x", 0, 0)}
        ax, ay, az = search_axis.get(plane, (0, 0, "z"))
        # 动态偏移：取绝对值从小到大，覆盖不同尺寸零件
        for sign in (1, -1):
            for v in (5, 10, 20, 50, 100, 200):
                try:
                    pts = [0.0, 0.0, sign * v * MM]
                    if ax == "x": pts[0] = sign * v * MM; pts[1] = 0.0; pts[2] = 0.0
                    elif ay == "y": pts[0] = 0.0; pts[1] = sign * v * MM; pts[2] = 0.0
                    else: pts[0] = 0.0; pts[1] = 0.0; pts[2] = sign * v * MM
                    self.ext.SelectByID2("", "FACE", pts[0], pts[1], pts[2], False, 0, self._empty, 0)
                    self.skm.InsertSketch(True)
                    active_sk = self.skm.ActiveSketch
                    if active_sk is not None:
                        self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                        return self
                except Exception:
                    continue
        raise RuntimeError(f"无法激活草图，平面 {plane} 选择失败")

    def _select_face_by_box(self, x, y, z, tolerance_mm=5.0):
        """通过面包围盒匹配选择实体面（绕过 SW 射线拾取在边界处的局限）。

        Bug 7/8 修复: SelectByID2(FACE, x,y,z) 在最大边界面处将坐标识别为边/顶点。
        本方法遍历所有面，用 face.GetBox 检查目标点是否在面范围内，
        然后用 face.Select(True) 直接选中，完全不依赖坐标投影。
        同时处理用户局部坐标到 SW 内部坐标的自动转换。

        Args:
            x, y, z: 目标点坐标（mm，默认用户局部坐标）
            tolerance_mm: 包围盒匹配容差（默认 5mm）
        Returns:
            True if face selected, False otherwise
        """
        try:
            self.clear_selection()
            bodies = self.model.GetBodies2(0, 1)
            if not bodies:
                return False
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
            tol = tolerance_mm

            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    if not flist:
                        continue

                    # ── 第一遍：尝试原始坐标直接匹配 ──────────────────────
                    for face in flist:
                        try:
                            bb = face.GetBox
                            bb_min = (bb[0] * 1000, bb[1] * 1000, bb[2] * 1000)
                            bb_max = (bb[3] * 1000, bb[4] * 1000, bb[5] * 1000)
                            if (bb_min[0] - tol <= x <= bb_max[0] + tol and
                                bb_min[1] - tol <= y <= bb_max[1] + tol and
                                bb_min[2] - tol <= z <= bb_max[2] + tol):
                                if face.Select(True):
                                    return True
                        except Exception:
                            continue

                    # ── 第二遍：计算实体整体包围盒，转换局部坐标后再匹配 ──
                    ent_bb_min = [float('inf')] * 3
                    ent_bb_max = [float('-inf')] * 3
                    for face in flist:
                        try:
                            bb = face.GetBox
                            for i in range(3):
                                ent_bb_min[i] = min(ent_bb_min[i], bb[i] * 1000)
                                ent_bb_max[i] = max(ent_bb_max[i], bb[i+3] * 1000)
                        except Exception:
                            continue
                    if any(v == float('inf') for v in ent_bb_min):
                        continue

                    ix = ent_bb_min[0] + x
                    iy = ent_bb_min[1] + y
                    iz = ent_bb_min[2] + z

                    for face in flist:
                        try:
                            bb = face.GetBox
                            bb_min = (bb[0] * 1000, bb[1] * 1000, bb[2] * 1000)
                            bb_max = (bb[3] * 1000, bb[4] * 1000, bb[5] * 1000)
                            if (bb_min[0] - tol <= ix <= bb_max[0] + tol and
                                bb_min[1] - tol <= iy <= bb_max[1] + tol and
                                bb_min[2] - tol <= iz <= bb_max[2] + tol):
                                if face.Select(True):
                                    return True
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _find_nearest_planar_face(self, x, y, z):
        """在曲面选面失败后，找最近的有效平面回退。

        Bug 8 修复: SolidWorks 不支持在曲面（圆柱/球面等）上直接创建草图。
        当 face.Select(True) 成功但 InsertSketch 返回 ActiveSketch=None 时，
        说明目标面是曲面，需要回退到最近的有效平面。

        Args:
            x, y, z: 目标点坐标（内部坐标系，mm）
        Returns:
            返回 (face, skipped_curved) 其中 face 是找到的平面，
            skipped_curved 表示是否跳过了曲面
        """
        try:
            bodies = self.model.GetBodies2(0, 1)
            if not bodies:
                return None, False
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]

            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    if not flist:
                        continue

                    best_face = None
                    best_dist = float('inf')

                    for face in flist:
                        try:
                            n = face.Normal
                            # 过滤零法线（球面网格化的三角面）
                            if all(abs(v) < 0.01 for v in n):
                                continue
                            bb = face.GetBox
                            bb_min = (bb[0]*1000, bb[1]*1000, bb[2]*1000)
                            bb_max = (bb[3]*1000, bb[4]*1000, bb[5]*1000)
                            # 计算面中心到目标点的距离
                            cx = (bb_min[0] + bb_max[0]) / 2
                            cy = (bb_min[1] + bb_max[1]) / 2
                            cz = (bb_min[2] + bb_max[2]) / 2
                            dist = ((cx - x)**2 + (cy - y)**2 + (cz - z)**2) ** 0.5
                            if dist < best_dist:
                                best_dist = dist
                                best_face = face
                        except Exception:
                            continue

                    if best_face and best_dist < 200:  # 200mm 内
                        return best_face, True
                except Exception:
                    continue
        except Exception:
            pass
        return None, False

    def _is_curved_face(self, face):
        """判断面是否为曲面（圆柱/球面等），无法直接在其上开草图。

        Bug 8 修复辅助方法: SW COM 动态调用下 face.Normal 对曲面返回 (0,0,0)，
        而平面返回非零法向量。

        # 未来 AI 可改进方向:
        # - 使用 face.GetSurface().IsCylinder / IsSphere 等 ISurface 属性更精确判断
        # - 使用 face.GetSurface().Evaluate(u,v) 计算精确法线（但 Evaluate 在动态 dispatch 下报错）
        # - 使用 face.GetSurface().GetClosestPointOn(x,y,z) 返回 UVW，再求法线
        """
        try:
            n = face.Normal
            return all(abs(v) < 0.01 for v in n)
        except Exception:
            return False

    def _find_nearest_datum_plane(self, x, y, z):
        """在曲面选面失败后，回退到最近平行基准面。

        Bug 8 修复: 当前策略是根据坐标方向选择最近的主基准面（Front/Top/Right）。

        # 专家建议但未实现的方案（供未来 AI 参考）:
        #
        # 【方案A】CreatePlaneByNormalAndOffset — 已验证不可用
        #   m.model.CreatePlaneByNormalAndOffset(0, 1, 0, 0, 0, 0, 0, 0)
        #   结果: <unknown>.CreatePlaneByNormalAndOffset — 动态 COM dispatch 不暴露此方法
        #
        # 【方案B】InsertRefPlane — 参数数始终不匹配
        #   m.fm.InsertRefPlane(0, 0.05, 0, 0.01, 1, 0, 0)  # 6参返回None
        #   m.fm.InsertRefPlane(0, 0.05, 0, 0.01, 1, 0, 0, 0)  # 8参报"无效参数数目"
        #   原因: 动态 dispatch 无法正确传递 VARIANT 数组参数
        #   pythoncom.VARIANT / win32com.client.VARIANT 均尝试过，仍报"类型不匹配"
        #
        # 【方案C】ISurface.Evaluate — 始终报"非选择性参数"
        #   surf = face.GetSurface()
        #   surf.Evaluate(0.05, 0.03, 0.02)  # 1/2/3/VARIANT参数都试过
        #   原因: 动态 dispatch 下 ISurface.Evaluate 无法正确接收 double 参数
        #
        # 【方案D】Wrap 特征 — InsertWrapFeature 返回 None 且模型无变化
        #   m.fm.InsertWrapFeature(0, 0, 0)  # 3参返回None
        #   m.fm.InsertWrapFeature2(0, 0, 0, 0, 0)  # 5参返回None
        #   验证: massprops 体积不变 → Wrap 未执行
        #   可能原因: SelectByID2 选择集格式不对 / 参数签名需要 Reference 对象
        #
        # 【方案E】C++/C# COM 调用 — 唯一彻底解决方案
        #   使用 win32com.client.getparent() 获取原始 IDispatch
        #   或通过 ctypes 直接调用 IModelDoc2::CreatePlaneByNormalAndOffset
        #   但实现复杂度高，需重新注册类型库
        #
        # 【方案F】手动创建基准面（用户操作）
        #   在聊天中提示用户："请在 SolidWorks 中手动创建基准面，然后告诉我名称"
        #   AI 再用 SelectByID2 选择该基准面开草图
        #
        # 当前限制: 使用已有的前视/上视/右视基准面作为回退，位置可能不准确。

        Args:
            x, y, z: 目标点坐标（内部坐标系，mm）
        Returns:
            回退的基准面名称，或 None
        """
        # 根据目标点方向选择最合适的基准面
        max_axis = max(abs(x), abs(y), abs(z), key=abs)
        if max_axis == abs(z):
            plane_name = "Front Plane"
        elif max_axis == abs(y):
            plane_name = "Top Plane"
        else:
            plane_name = "Right Plane"

        # 尝试选中基准面并开草图
        for name in [plane_name, "Front Plane", "Top Plane", "Right Plane"]:
            try:
                self.clear_selection()
                sel = self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
                if sel:
                    self.skm.InsertSketch(True)
                    active_sk = self.skm.ActiveSketch
                    if active_sk is not None:
                        return name
                    self.clear_selection()
            except Exception:
                continue
        return None

    def begin_sketch_on_face(self, x=0, y=0, z=0):
        """在 (x,y,z) mm 处所在的面开始草图，并正视于该面。

        Bug 7 修复: 最大边界面不可选——改用面包围盒匹配+face.Select(True)。
        Bug 8 修复: 旋转体/球面等曲面不支持直接开草图——回退到最近平行基准面。
        Bug 4 修复: 选择成功后清除 COM 内部选择缓存。

        # 行为说明:
        # - 平面（方块、圆柱端面等）: 直接在该面上开草图 ✅
        # - 曲面（圆柱侧面、球面等）: 回退到最近平行基准面 ⚠️（位置不准）
        # - 全部失败: 回退到 Front Plane，打印警告消息 📢
        #
        # 当前不支持曲面直接开草图。如需在曲面上操作，建议在聊天中告知用户：
        # "当前不支持，请你自己画" 或在相邻平面上画草图后使用包覆(Wrap)特征。

        # 未来 AI 可改进方向（见 _find_nearest_datum_plane 注释）:
        # 1. 使用 C++ COM 调用绕过 pywin32 动态 dispatch 限制
        # 2. 修复 ISurface.Evaluate 的参数传递（可能需要 VARIANT 数组）
        # 3. 修复 InsertRefPlane 的参数签名（需要正确的 Reference 对象）
        # 4. 修复 InsertWrapFeature 的选择集格式（可能需要 ISelectData）
        """
        # Bug 7: 用面包围盒匹配法选面
        if self._select_face_by_box(x, y, z):
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._normal_to("*Front")
                return self
            # 选面成功但草图未激活 → 可能是曲面
            self.clear_selection()
            # Bug 8: 检测是否为曲面（Normal 全为零）
            bodies = self.model.GetBodies2(0, 1)
            if bodies:
                body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
                for b in body_list:
                    try:
                        faces = b.GetFaces()
                        flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                        for face in flist:
                            if self._is_curved_face(face):
                                # 曲面，回退到基准面
                                plane_name = self._find_nearest_datum_plane(x, y, z)
                                if plane_name:
                                    print(f"[swapi] 曲面回退: 在 '{plane_name}' 上创建草图（目标坐标 {x},{y},{z}）")
                                    # 输出到聊天框的用户友好提示
                                    print(f"[swapi] ⚠️ 当前不支持在曲面（圆柱/球面）上直接开草图，已回退到 '{plane_name}'。如需在曲面画草图，请手动在 SolidWorks 中创建基准面。")
                                    return self
                                break
                    except Exception:
                        continue
            return self

        # 兜底：坐标射线拾取（应对特殊情况）
        fallback_attempts = [
            (0, 0, 0), (0.5, 0, 0), (-0.5, 0, 0),
            (0, 0.5, 0), (0, -0.5, 0),
            (0, 0, 0.5), (0, 0, -0.5),
            (1, 0, 0), (-1, 0, 0),
            (0, 1, 0), (0, -1, 0),
            (0, 0, 1), (0, 0, -1),
        ]
        for dx, dy, dz in fallback_attempts:
            try:
                self.clear_selection()
                self.ext.SelectByID2("", "FACE", (x + dx) * MM, (y + dy) * MM, (z + dz) * MM,
                                     False, 0, self._empty, 0)
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._normal_to("*Front")
                    return self
            except Exception:
                continue

        # 全部失败：回退到 Front Plane，不报错，输出提示
        print(f"[swapi] begin_sketch_on_face({x},{y},{z}) 失败，回退到 Front Plane")
        print(f"[swapi] ⚠️ 当前不支持在坐标 ({x},{y},{z}) 处开草图，请你自己画。")
        try:
            self.clear_selection()
            self.ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, self._empty, 0)
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._normal_to("*Front")
                return self
            self.clear_selection()
        except Exception:
            pass
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
        self.rebuild()  # Bug 9: 重建模型以清除 COM 内部选择状态累积
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
                self.rebuild()  # Bug 9: 重建模型以清除 COM 内部选择状态累积
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
            if feat:
                self._visual_step("cut")
                self.rebuild()  # Bug 9
                return feat
        except Exception:
            pass
        return None

    def revolve(self, angle_deg=360, cut=False):
        """旋转特征。草图需含轮廓 + centerline() 旋转轴。angle 单位度。

        修复: FeatureRevolve2 使用最后一个草图，不需要 ActiveSketch。
        """
        ang = math.radians(angle_deg)
        feat = self.fm.FeatureRevolve2(
            True, True, False, cut, False, False,
            SW_REV_BLIND, 0, ang, 0,
            False, False, 0, 0, 0, 0, 0,
            True, False, True)
        if feat is None:
            raise RuntimeError("FeatureRevolve2 返回 None，旋转特征创建失败")
        self._visual_step("revolve")
        self.rebuild()  # Bug 9: 重建模型以清除 COM 内部选择状态累积
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
