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

# 基准面名称（多语言支持：英文 / 中文）
_PLANES_EN = ("Front Plane", "Top Plane", "Right Plane")
_PLANES_ZH = ("前视基准面", "上视基准面", "右视基准面")
_PLANES = _PLANES_EN + _PLANES_ZH


def _detect_plane_names(sw):
    """检测 SolidWorks 语言版本，返回对应的基准面名称列表。

    通过遍历特征树查找基准面名称来判断语言。
    """
    try:
        swlang = sw.LanguageName
        if "Chinese" in swlang or "Simplified" in swlang:
            return _PLANES_ZH
    except Exception:
        pass
    # 默认使用英文，同时尝试中文
    return _PLANES_EN


def _get_plane_names(sw):
    """获取当前 SW 版本的基准面名称（带缓存）。"""
    global _PLANE_NAMES_CACHE
    if _PLANE_NAMES_CACHE:
        return _PLANE_NAMES_CACHE
    _PLANE_NAMES_CACHE = _detect_plane_names(sw)
    return _PLANE_NAMES_CACHE


_PLANE_NAMES_CACHE = None


def _auto_detect_planes(sw):
    """自动探测基准面名称：先试英文，失败再试中文。"""
    for names in [_PLANES_EN, _PLANES_ZH]:
        # 尝试选中前视基准面
        dummy = sw.GetActiveDoc
        try:
            if dummy is not None:
                ext = dummy.Extension
                empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
                if ext.SelectByID2(names[0], "PLANE", 0, 0, 0, False, 0, empty, 0):
                    return names
        except Exception:
            pass
    # 回退：返回英文，兼容英文版
    return _PLANES_EN


# 可视化建模模式

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
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id']
        ).decode().strip().split()
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
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id']
        ).decode().strip().split()
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
    """新建零件，返回 SWModel。"""
    if sw is None:
        sw = get_sw()
    _disable_snapping(sw)
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
        """质量属性（谨慎使用）。

        ⚠️ 警告：GetMassProperties 可能导致 SolidWorks 卡死。
        建模完成后不要调用此方法（用户明确约定：跳过校验）。

        质量属性数组顺序（2022 实测）:
        [cogX, cogY, cogZ, volume, surface_area, mass, Ixx, Iyy, Izz, Ixy, Ixz, Iyz]
        """
        try:
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
        except Exception as e:
            return {"ok": False, "error": f"massprops failed: {e}"}

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
             '(Get-Process sldworks -ErrorAction SilentlyContinue).Id']
        ).decode().strip().split()
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

    def screenshot(self, path=None):
        """对 SolidWorks 主窗口截图保存为 PNG。"""
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "solidworks_live.png")
        try:
            import mss
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
            with mss.mss() as sct:
                shot = sct.grab({'left': rect.left, 'top': rect.top,
                                 'width': w, 'height': h})
                mss.tools.to_png(shot.rgb, shot.size, output=path)
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
        """选中基准面，自动尝试中英文名称。"""
        name_map = {
            "Front Plane": "前视基准面",
            "Top Plane": "上视基准面",
            "Right Plane": "右视基准面",
            "前视基准面": "Front Plane",
            "上视基准面": "Top Plane",
            "右视基准面": "Right Plane",
        }
        # 先检查是否已经是已知的英文名
        if name in _PLANES_EN:
            # 英文名在中文SW上可能失败，尝试中文名回退
            try:
                self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
            except Exception:
                pass
            # 回退：试中文名
            cn_name = name_map.get(name)
            if cn_name:
                try:
                    if self.ext.SelectByID2(cn_name, "PLANE", 0, 0, 0, False, 0, self._empty, 0):
                        return self
                except Exception:
                    pass
            return self
        if name in _PLANES_ZH:
            # 中文名在英文SW上可能失败，尝试英文名回退
            try:
                self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
            except Exception:
                pass
            en_name = name_map.get(name)
            if en_name:
                try:
                    if self.ext.SelectByID2(en_name, "PLANE", 0, 0, 0, False, 0, self._empty, 0):
                        return self
                except Exception:
                    pass
            return self
        if name in name_map:
            # 先试原名，再试翻译名
            for try_name in [name, name_map[name]]:
                try:
                    if self.ext.SelectByID2(try_name, "PLANE", 0, 0, 0, False, 0, self._empty, 0):
                        return self
                except Exception:
                    pass
            raise ValueError(f"无法选中基准面 {name!r}，请确认 SolidWorks 语言版本")
        raise ValueError(f"未知基准面 {name!r}，支持: {_PLANES_EN} / {_PLANES_ZH}")

    def begin_sketch(self, plane="Front Plane"):
        """在指定基准面上开始新草图，并先"正视于"该平面（居中显示）。

        返回 True 表示成功，False 表示失败。
        """
        ok = self.select_plane(plane)
        result = self.skm.InsertSketch(True)
        # InsertSketch(True) 返回 None（成功）或 False（失败），不能直接用 not 判断
        if result is False:
            raise RuntimeError(f"无法在基准面 {plane!r} 上开始草图")
        self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
        return self

    def begin_sketch_on_face(self, x=0, y=0, z=0):
        """在 (x,y,z) mm 处所在的面开始草图，并正视于该面。"""
        self.ext.SelectByID2("", "FACE", x * MM, y * MM, z * MM, False, 0,
                             self._empty, 0)
        self.skm.InsertSketch(True)
        self._normal_to("*Front")
        return self

    def end_sketch(self, merge=True):
        """结束草图并提交到特征树。merge=True 时合并微小间隙的端点，确保轮廓封闭。

        返回 self 以支持链式调用。
        """
        if merge:
            try:
                self.skm.ActiveSketch.MergePoints(0.0005)
            except Exception:
                pass
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            pass
        # 关键：InsertSketch(True) 是 toggle 模式，会正确结束草图并添加到特征树
        # InsertSketch(False) 只退出草图模式，不创建草图特征
        try:
            self.skm.InsertSketch(True)  # toggle 结束草图，添加到特征树
        except Exception:
            pass
        return self

    # ---------- 草图图元（坐标单位 mm）----------
    def rect(self, cx, cy, w, h):
        """中心矩形：中心 (cx,cy)，宽 w，高 h。"""
        x1, y1 = (cx - w / 2) * MM, (cy + h / 2) * MM
        x2, y2 = (cx + w / 2) * MM, (cy - h / 2) * MM
        self.skm.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
        return self

    def circle(self, cx, cy, r):
        """圆心 (cx,cy)，半径 r。"""
        self.skm.CreateCircleByRadius(cx * MM, cy * MM, 0, r * MM)
        return self

    def line(self, x1, y1, x2, y2):
        self.skm.CreateLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
        return self

    def polyline(self, points):
        """折线：points = [(x1,y1), (x2,y2), ...]，自动连成连续折线。"""
        pts = [(x * MM, y * MM) for x, y in points]
        for i in range(len(pts) - 1):
            self.skm.CreateLine(pts[i][0], pts[i][1], 0,
                                pts[i + 1][0], pts[i + 1][1], 0)
        return self

    def centerline(self, x1, y1, x2, y2):
        """中心线（旋转特征的旋转轴）。"""
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
        """拉伸凸台。depth 单位 mm；symmetric=True 两侧对称；draft_deg 拔模角度（度）。

        注意：两侧对称枚举值随版本不同（2022=6, 2018=5），自动适配。
        拔模角度 draft_deg 会启用拉伸的拔模选项（Dchk1=True, Dang1=角度弧度）。
        """
        T1 = _get_midplane_enum(self.sw) if symmetric else SW_END_BLIND
        d = depth * MM
        # 拔模参数：draft_deg != 0 时启用拔模
        draft_enabled = draft_deg != 0
        draft_angle_rad = math.radians(draft_deg) if draft_enabled else 0.0
        feat = self.fm.FeatureExtrusion3(
            True, False, False, T1, 0, d, 0,
            draft_enabled, False, False, False, draft_angle_rad, 0,
            False, False, False, False, True, False, auto_select,
            0, 0, False)
        self._visual_step("extrude")
        return feat

    def cut(self, depth=10, through=False, flip=False, auto_select=True):
        """切除。through=True 完全贯穿；否则切除 depth mm。"""
        T1 = SW_END_THROUGH if through else SW_END_BLIND
        d = depth * MM
        feat = self.fm.FeatureCut3(
            True, bool(flip), False, T1, 0, d, 0,
            False, False, False, False, 0, 0,
            False, False, False, False, False, False, auto_select,
            False, False, False, 0, 0, False)
        self._visual_step("cut")
        return feat

    def revolve(self, angle_deg=360, cut=False):
        """旋转特征。草图需含轮廓 + centerline() 旋转轴。angle 单位度。

        返回特征对象；若创建失败返回 None。
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
