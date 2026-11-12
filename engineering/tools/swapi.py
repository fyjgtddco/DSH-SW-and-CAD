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
import time
import sys
import glob
import json
import contextlib
import subprocess

import pythoncom
import win32com.client

# 静默子进程（Windows 下不弹窗）
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _drive_roots():
    """枚举系统上【真实存在】的所有盘符根（如 "C:\\\\", "Z:\\\\", "G:\\\\"）。

    ── 【连接区修复】为什么必须有这个函数 ──────────────────────────────────
    用户诉求原文："以后这个 SW 关了也好，我重新下到其他的 C，D，G，Q
      任意一个盘也好，点那个连接就可以直接找到地方的哦，CAD 也是。"

    原缺陷：若干处把盘符【硬编码】成 ("C:","D:","E:","F:") 或加上 "Z:"。
    后果：用户把 SolidWorks / AutoCAD 换装到 G:、Q: 等未列出的盘 →
      全盘扫描逻辑直接跳过该盘 → "找不到安装位置"，
      而且报错信息会误导（看起来像"没装"）。

    本函数【枚举 A~Z 全部盘符并逐个探测其是否存在】，因此对任意盘符都成立。
    注意：A/B 通常是软驱位（不存在会被 os.path.exists 过滤掉），
      网络盘/虚拟盘只要已挂载且可访问也会被纳入。

    Returns: ["C:\\\\", "D:\\\\", ...]；一个都没有时退回 ["C:\\\\"]。
    """
    roots = []
    try:
        import string
        for letter in string.ascii_uppercase:
            root = letter + ":\\"
            try:
                if os.path.exists(root):
                    roots.append(root)
            except Exception:
                continue
    except Exception:
        pass
    if not roots:
        roots = ["C:\\"]
    return roots

# SolidWorks 类型库标识（Bug5：用于生成/取用前期绑定缓存）
SW_TLB_IID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
SW_TLB_VER = (0, 33, 0)   # (lcid, major, minor) —— 实机 sldworks.tlb 为 33.0
def _find_sldworks_tlb():
    """定位 sldworks.tlb（SolidWorks 类型库文件）。

    ── 【Bug5 修复】SW2025 下 gencache.EnsureDispatch 会报
    #    'This COM object can not automate the makepy process'，
    #    而 EnsureModule(IID, ...) 又报 '库没有注册'。
    #    唯一可靠路径是【直接加载 .tlb 文件】再生成缓存，
    #    因此这里负责把该文件找出来。
    #
    #    查找顺序：注册表登记的路径 → SW 常见安装位置 → 环境变量。
    #"""
    import os as _os
    # ① 注册表：HKCR\\TypeLib\\<IID>\\<ver>\\0\\win64
    try:
        import winreg
        _iid = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
        for _pfx in ("", "WOW6432Node\\"):
            for _ver in ("33.0", "21.0", "1.0"):
                _base = _pfx + "TypeLib\\" + _iid + "\\" + _ver
                try:
                    _k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _base)
                except Exception:
                    continue
                for _flag in ("0", "1"):
                    for _arch in ("win64", "win32"):
                        try:
                            _ak = winreg.OpenKey(_k, _flag + "\\" + _arch)
                            _p = winreg.QueryValueEx(_ak, "")[0]
                            winreg.CloseKey(_ak)
                            if _p and _os.path.exists(_p):
                                winreg.CloseKey(_k)
                                return _p
                        except Exception:
                            continue
                winreg.CloseKey(_k)
    except Exception:
        pass
    # ② 常见安装位置（【全盘符】—— 用户机器可能装在 Z / G / Q 等任意盘）
    # 【连接区修复】原实现硬编码 ("C:","D:","E:","Z:","F:") 五个盘符，
    #   用户把 SW 换装到 G:/Q: 等盘就【永远找不到 sldworks.tlb】→
    #   类型库缓存生成失败 → MathUtility 相关功能挂掉。
    #   改为枚举系统上【真实存在】的所有盘符。
    #
    # ── 【连接区加固 · 2026-10-04】再补两条更可靠的路子 ──────────────────
    #   上面的 glob 仍假设安装形态是 `...\Program Files\SOLIDWORKS*\SOLIDWORKS`。
    #   若 SW 装在 `Z:\7-Zip\SolidWorks 2025` 这类目录（本机 AutoCAD 就是这种
    #   装法），glob 依然找不到。因此：
    #     ① 先从注册表拿到【真实安装目录】，直接拼 sldworks.tlb（最可靠）；
    #     ② 再从 SLDWORKS.exe 所在目录反推（同目录通常就有 .tlb）。
    _cands = []
    # ① 注册表安装目录 → 拼 .tlb（支持 SOLIDWORKS 子目录与直接安装两种形态）
    try:
        for _d in _sw_install_from_registry():
            for _sub in ("sldworks.tlb",
                         _os.path.join("SOLIDWORKS", "sldworks.tlb")):
                _p = _os.path.join(_d, _sub)
                if _os.path.isfile(_p):
                    return _p
                _cands.append(_p)
    except Exception:
        pass
    # ② 从 SLDWORKS.exe 所在目录反推
    try:
        _exe = find_sldworks_exe()
        if _exe:
            _dir = _os.path.dirname(_exe)
            for _p in (_os.path.join(_dir, "sldworks.tlb"),
                       _os.path.join(_dir, "..", "sldworks.tlb")):
                if _os.path.isfile(_p):
                    return _os.path.abspath(_p)
    except Exception:
        pass
    # ③ 全盘符常见目录 glob（兜底，保持原有行为）
    try:
        import glob as _glob
        for _root in _drive_roots():
            _cands.extend(_glob.glob(_os.path.join(
                _root, "Program Files", "SOLIDWORKS*", "SOLIDWORKS", "sldworks.tlb")))
            _cands.extend(_glob.glob(_os.path.join(
                _root, "Program Files", "SOLIDWORKS Corp*", "SOLIDWORKS",
                "sldworks.tlb")))
            # 放宽：盘符根下任意含 SOLIDWORKS 的一级目录
            _cands.extend(_glob.glob(_os.path.join(
                _root, "*SOLIDWORKS*", "sldworks.tlb")))
            _cands.extend(_glob.glob(_os.path.join(
                _root, "*SOLIDWORKS*", "*", "sldworks.tlb")))
    except Exception:
        pass
    for _c in _cands:
        try:
            if _os.path.isfile(_c):
                return _os.path.abspath(_c)
        except Exception:
            continue
    return None


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

MM = 0.001  # 毫米 → 米


def vision_shot_dir():
    """返回"供 DSH 前端识图插件读取"的截图目录（自动探测，可覆盖）。

    ── 【连接自检修复】消除硬编码的【作者本机桌面路径】──────────────────
    原缺陷：三处把路径写死成 `C:\\Users\\j1877\\Desktop\\DSH-Check`
      （swapi.screenshot(for_vision=True)、sw_bridge.cmd_vision_fallback、
      cmd_check_vision）。后果：
        · 换一台电脑 / 换用户名 → 目录不存在或不可写，
          识图降级链路静默失效（截图存到了别人的桌面）；
        · 同步到其它预设副本时还会在别人机器上凭空造目录。

    探测顺序（第一个可写者胜出）：
      1) 环境变量 DSH_VISION_DIR（显式覆盖，最高优先）
      2) <用户桌面>\\DSH-Check（沿用历史语义，但跟随真实用户名）
      3) <工具目录>\\vision_shots（桌面不可写时的兜底）

    Returns: 绝对路径（已确保存在）。
    """
    import tempfile as _tempfile
    cands = []
    _env = (os.environ.get("DSH_VISION_DIR") or "").strip()
    if _env:
        cands.append(_env)
    # ② 用户桌面（用 USERPROFILE 推导，不再写死用户名）
    try:
        _home = os.path.expanduser("~")
        if _home and _home != "~":
            cands.append(os.path.join(_home, "Desktop", "DSH-Check"))
    except Exception:
        pass
    # ③ 工具目录兜底（一定存在且通常可写）
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "vision_shots"))
    for c in cands:
        try:
            os.makedirs(c, exist_ok=True)
            # 实地写测试：目录存在但无写权限时要能发现
            _probe = os.path.join(c, ".dsh_write_probe")
            with open(_probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(_probe)
            return os.path.abspath(c)
        except Exception:
            continue
    # 全失败 → 退回系统临时目录（绝不返回 None）
    _fallback = os.path.join(_tempfile.gettempdir(), "DSH-Check")
    try:
        os.makedirs(_fallback, exist_ok=True)
    except Exception:
        pass
    return os.path.abspath(_fallback)

# ==================== 自动探测 ====================

def _version_year(major):
    """SW 主版本号 → 年份：30=2022, 31=2023, 32=2024, 29=2021, 28=2020..."""
    return major + 1992


def _sw_install_from_registry():
    r"""【B1修复 + 连接区加固】从注册表读取 SolidWorks 安装路径（跨盘符）。

    ── 【连接区加固 · 2026-10-04】原实现读的是【空键】──────────────────
    实测（本机 SW2025 装在 Z 盘）：
        `_sw_install_from_registry()` 返回 []（一条都读不到）
      因为原实现只读 `HKLM\SOFTWARE\SolidWorks\Applications`，
      而该键在本机【只有 (default)、没有任何值】。
      ⇒ SW 的路径发现一直【完全依赖全盘扫描】，注册表这条路是死的。
        一旦 SW 装在扫描白名单之外的目录（如 CAD 那样装在
        `Z:\7-Zip\AutoCAD 2020`），就会"装没装都找不到"。

    实测找到的【真正权威】位置（递归搜索得知）：
      · `HKLM\SOFTWARE\SolidWorks\SOLIDWORKS 2025\Setup`
            「SolidWorks Folder」= Z:\Program Files\SOLIDWORKS Corp2025\SOLIDWORKS\
        —— 这是安装程序写的实际安装目录，最权威。
      · `HKLM\SOFTWARE\SolidWorks\IM`
            「InstallDir 2025」  = Z:\Program Files\SOLIDWORKS Corp2025
        —— 安装管理程序记录的安装根。
      · 各版本子键下的 `InstallDir` / `InstallLocation` / `Path`。

    加固策略（按可靠性从高到低）：
      ① 递归遍历 `SOFTWARE\SolidWorks`（深度受限），
         凡值名含 Folder/InstallDir/InstallLocation/Path/Location 且值像路径的，
         一律收集 —— 不预设层级，避免再次"少读一层就全盘失效"。
      ② 保留原 Applications 读取（老版本 SW 该键是有值的，不能删）。
      ③ 额外显式补几个已知权威键，作为递归之外的双保险。

    返回：候选路径/目录字符串列表（调用方负责判断存在性并拼 SLDWORKS.exe）。
    """
    cands = []
    if sys.platform != "win32":
        return cands
    try:
        import winreg
    except Exception:
        return cands

    def _add(v):
        """收集看起来像"安装路径"的字符串。"""
        try:
            if not isinstance(v, str):
                return
            v = v.strip().strip('"')
            if not v or len(v) < 4:
                return
            # 只收绝对路径（盘符或 UNC），排除 URL / 纯命令
            if not ((":" in v and ("\\" in v or "/" in v))
                    or v.startswith("\\\\")):
                return
            if v.lower().startswith(("http://", "https://")):
                return
            if v not in cands:
                cands.append(v)
        except Exception:
            pass

    # ── ① 递归遍历（主力）：不预设层级，任何深度都能命中 ──────────────
    #   值名关键字：本机实测是「SolidWorks Folder」「InstallDir 2025」
    _NAME_KEYS = ("folder", "installdir", "installlocation", "install path",
                  "path", "location")
    _MAX_DEPTH = 4

    def _walk(hive, path, depth):
        if depth > _MAX_DEPTH:
            return
        try:
            k = winreg.OpenKey(hive, path)
        except Exception:
            return
        try:
            i = 0
            while True:
                try:
                    name, value, _t = winreg.EnumValue(k, i)
                    i += 1
                except OSError:
                    break
                _ln = str(name).lower()
                if any(kk in _ln for kk in _NAME_KEYS):
                    _add(value)
                elif isinstance(value, str) and \
                        value.lower().endswith(("sldworks.exe", "sldworks")):
                    _add(value)
            j = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, j)
                    j += 1
                except OSError:
                    break
                _walk(hive, path + "\\" + sub, depth + 1)
        finally:
            try:
                winreg.CloseKey(k)
            except Exception:
                pass

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\SolidWorks",
                     r"SOFTWARE\WOW6432Node\SolidWorks"):
            try:
                _walk(hive, base, 0)
            except Exception:
                continue

    # ── ② 原 Applications 读取（老版本 SW 该键有值，保留兼容）──────────
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SolidWorks\Applications"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\SolidWorks\Applications"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\SolidWorks\Applications"),
    ]
    for hive, key_path in reg_paths:
        try:
            with winreg.OpenKey(hive, key_path) as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                        i += 1
                    except OSError:
                        break
                    if isinstance(value, str) and value.lower().endswith(".exe"):
                        _add(value)
        except Exception:
            continue

    # ── ③ 双保险：显式补已知权威键（递归之外再钉一遍）────────────────
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\SolidWorks", r"SOFTWARE\WOW6432Node\SolidWorks"):
            try:
                with winreg.OpenKey(hive, base) as k:
                    j = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, j)
                            j += 1
                        except OSError:
                            break
                        # 版本子键（SOLIDWORKS 2025 / SolidWorks 2024 …）
                        for subsub in ("", "\\Setup", "\\General"):
                            for vname in ("SolidWorks Folder", "InstallDir",
                                          "InstallLocation", "Path",
                                          "Location"):
                                try:
                                    with winreg.OpenKey(
                                            hive,
                                            base + "\\" + sub + subsub) as sk:
                                        val, _ = winreg.QueryValueEx(sk, vname)
                                        _add(val)
                                except Exception:
                                    pass
            except Exception:
                continue

    # ── ④ 按"可靠性"重排（避免把 C:\SOLIDWORKS Data 这类无关目录排在前面）──
    #   判据（越靠前越可能是真正的安装根）：
    #     a) 该目录下确实存在 SLDWORKS.exe        —— 铁证，排最前
    #     b) 值本身以 sldworks.exe 结尾           —— 直接就是可执行文件
    #     c) 路径形如 ...\SOLIDWORKS CorpXXXX\SOLIDWORKS —— 典型安装形态
    #     d) 明显无关的（Data/Downloads/IM/Posts/TBM/lang/templates）往后压
    def _rank(v):
        lv = str(v).lower()
        s = 50
        try:
            for _n in ("SLDWORKS.exe", "sldworks.exe"):
                if os.path.isfile(os.path.join(v, _n)):
                    s -= 60
                    break
        except Exception:
            pass
        if lv.endswith("sldworks.exe"):
            s -= 40
        if lv.rstrip("\\/").endswith("solidworks"):
            s -= 25
        if "program files" in lv or "corp" in lv:
            s -= 10
        if any(x in lv for x in ("data\\", "downloads", "\\im\\", "sldim",
                                 "posts", "tbm", "camera", "lang\\",
                                 "templates", "toolbox")):
            s += 40
        return s

    try:
        cands.sort(key=_rank)
    except Exception:
        pass
    return cands


def walk_drive_for_exe(drive, exe_names, keyword="solidworks", max_depth=3):
    """在【单个盘符】内做有界深度遍历，寻找指定的可执行文件。

    ── 【连接区加固 · 2026-10-04】为什么需要它 ──────────────────────────
    实测缺口：本机 AutoCAD 装在 `Z:\\7-Zip\\AutoCAD 2020\\acad.exe`。
    `glob` 模式（如 `Z:\\*SOLIDWORKS*`、`Z:\\*\\*`）只能覆盖 1~2 层，
    这种"盘符根 → 中继目录 → 软件目录"的嵌套结构【扫不到】；
    实测 4 种布局里，`盘根/7-Zip/SolidWorks 2025` 与更深的都 glob 不中。

    做法（代价可控，不会全盘暴力遍历）：
      · 深度限制 max_depth（默认 3）；
      · 【剪枝】：只有目录名含 keyword（solidworks / autocad）才继续下钻；
        浅层（<=1）的目录也下钻，以穿透 Program Files / 7-Zip 这类中继目录；
      · 跳过 Windows/Users/ProgramData/$Recycle 等无关大目录。

    Args:
        drive: 形如 "Z:" 或 "Z:\\"
        exe_names: 目标文件名元组，如 ("SLDWORKS.exe",)
        keyword: 深钻白名单关键字
        max_depth: 最大目录深度

    Returns: 命中的绝对路径，或 None。
    """
    _kw = str(keyword or "").lower()
    _names = tuple(str(n).lower() for n in (exe_names or ()))
    _skip = ("$recycle", "system volume", "windows", "programdata",
             "users", "appdata", "program files\\windows")
    try:
        base = drive if str(drive).endswith("\\") else str(drive) + "\\"
        if not os.path.isdir(base):
            return None
        for root, dirs, _files in os.walk(base):
            try:
                _depth = root[len(base):].count(os.sep)
            except Exception:
                _depth = 0
            if _depth >= max_depth:
                dirs[:] = []
                continue
            _bl = os.path.basename(root).lower()
            if any(_bl.startswith(_s) for _s in _skip):
                dirs[:] = []
                continue
            for _n in _names:
                _cand = os.path.join(root, _n)
                if os.path.isfile(_cand):
                    return os.path.abspath(_cand)
            # 剪枝：含关键字的目录继续下钻；浅层目录也下钻（穿透中继层）
            _keep = []
            for _d in dirs:
                _dl = _d.lower()
                if _kw and _kw in _dl:
                    _keep.append(_d)
                elif _depth <= 1 and not any(_dl.startswith(_s) for _s in _skip):
                    _keep.append(_d)
            dirs[:] = _keep
    except Exception:
        return None
    return None


def _all_drive_letters():
    """枚举系统上所有存在的盘符（含 Z 等网络/虚拟盘）。"""
    drives = []
    if sys.platform == "win32":
        try:
            import string
            for letter in string.ascii_uppercase:
                root = letter + ":\\"
                if os.path.exists(root):
                    drives.append(letter + ":")
        except Exception:
            pass
    if not drives:
        drives = ["C:"]
    return drives


def find_sldworks_exe():
    """【B1修复】定位 SLDWORKS.exe 真实路径。

    搜索顺序（从最可靠到兜底）：
      0. 环境变量 DSH_SW_EXE（用户显式指定，最高优先）
      1. 注册表 FullName（安装程序写入的绝对路径）
      2. 注册表 InstallDir 下的 SLDWORKS.exe
      3. 所有盘符的常见安装目录（含 Program Files\\SOLIDWORKS Corp*）
      4. 运行中进程的可执行文件路径
    返回第一个存在的绝对路径；找不到返回 None。

    ── 【连接区修复 · 兑现环境变量承诺】──────────────────────────────────
    实测事故：连接区启动失败的提示写着
      "可设置环境变量 DSH_SW_EXE 显式指定路径"，
    但全代码库【没有任何地方读取该变量】—— 用户照做也无效，属空头承诺。
    现真正支持：DSH_SW_EXE 可指向 exe 本身或其所在目录。
    """
    # 0) 本机设置 settings.json 的 sw_exe（设置页「工程环境检查」里指定）
    #    ── 【换机事故修复】设置里存的值带"机器指纹"，换电脑自动忽略；
    #       因此这条路径不会把 A 电脑的 Z: 盘带到 B 电脑上。
    try:
        import settings as _settings_mod
        _sv = _settings_mod.sw_exe()
        if _sv and os.path.isfile(_sv):
            return os.path.abspath(_sv)
    except Exception:
        pass
    # 0b) 环境变量显式指定（最高优先，用于非常规安装位置）
    _env = (os.environ.get("DSH_SW_EXE") or "").strip().strip('"')
    if _env:
        for _c in (_env,
                   os.path.join(_env, "SLDWORKS.exe"),
                   os.path.join(_env, "sldworks.exe")):
            try:
                if os.path.isfile(_c):
                    return os.path.abspath(_c)
            except Exception:
                pass
    seen = set()
    # 1) 注册表直给的可执行文件
    for v in _sw_install_from_registry():
        if not v:
            continue
        low = v.lower()
        if low.endswith("sldworks.exe") and os.path.isfile(v):
            return v
        # 注册表给的是目录 → 拼上可执行名
        for sub in ("SLDWORKS.exe", "sldworks.exe",
                    "SOLIDWORKS\\SLDWORKS.exe", "SolidWorks\\SLDWORKS.exe"):
            cand = os.path.join(v, sub)
            if os.path.isfile(cand):
                return cand
        # 逐级上溯找 SLDWORKS.exe
        d = v
        for _ in range(4):
            if not d or not os.path.isdir(d):
                break
            cand = os.path.join(d, "SLDWORKS.exe")
            if os.path.isfile(cand):
                return cand
            d = os.path.dirname(d)

    # 2) 全盘符扫描常见安装目录
    exe_names = ("SLDWORKS.exe", "sldworks.exe")
    sub_patterns = [
        r"Program Files\SOLIDWORKS Corp*\SOLIDWORKS",
        r"Program Files\SOLIDWORKS Corp*",
        r"SOLIDWORKS Corp*\SOLIDWORKS",
        r"SOLIDWORKS Corp*",
        r"Program Files\SolidWorks Corp*\SolidWorks",
        r"Program Files (x86)\SOLIDWORKS Corp*\SOLIDWORKS",
        # ── 【连接区加固】放宽到"盘符根下任何含 SOLIDWORKS 的目录" ──────
        #   本机 AutoCAD 装在 `Z:\7-Zip\AutoCAD 2020`（非 Program Files），
        #   SW 也可能被这样安装。仅靠上面几条 Program Files 模式会漏。
        r"*SOLIDWORKS*",
        r"*SOLIDWORKS*\SOLIDWORKS",
        r"*SOLIDWORKS*\*",
        r"*SolidWorks*",
        r"*SolidWorks*\SolidWorks",
    ]
    for drive in _all_drive_letters():
        for pat in sub_patterns:
            try:
                for d in glob.glob(os.path.join(drive + "\\", pat)):
                    for exe in exe_names:
                        cand = os.path.join(d, exe)
                        if cand not in seen:
                            seen.add(cand)
                            if os.path.isfile(cand):
                                return cand
            except Exception:
                continue

    # 2b) 【连接区加固】有界深度遍历：兜住 glob 覆盖不到的【多层】自定义目录
    #   实测缺口：`盘符根\7-Zip\SolidWorks 2025\SLDWORKS.exe`（正是本机
    #     AutoCAD 的装法 `Z:\7-Zip\AutoCAD 2020`）—— glob 模式只能覆盖
    #     1~2 层，这种 2 层以上嵌套【扫不到】。
    #   做法：对每个盘符做【深度受限】的遍历（见 walk_drive_for_exe），
    #     只在目录名含 solidworks 时继续下钻，命中即返回。
    for drive in _all_drive_letters():
        try:
            _hit = walk_drive_for_exe(drive, exe_names,
                                      keyword="solidworks", max_depth=3)
            if _hit:
                return _hit
        except Exception:
            continue

    # 3) 运行中进程的可执行路径（已启动时最准）
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process sldworks -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True, text=True, timeout=20,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        p = (out.stdout or "").strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


# ==================== 【Bug3】材料库：跨盘符全盘搜索 ====================
# 背景：零件密度恒为 1000 kg/m³（水），是因为从未给零件指定材料，
#       SW 就用默认密度。40Cr 应约 7850 kg/m³ —— 差 7.85 倍，
#       直接让质量属性、重心、工程图材料栏、以及"30年寿命强度校核"全部失真。
#
# 根因：SolidWorks 装在 Z 盘，材质库(.sldmat)也在 Z 盘：
#       Z:\Program Files\SOLIDWORKS Corp2025\SOLIDWORKS\lang\<lang>\sldmaterials\*.sldmat
#       而 DEFAULT_MATERIAL_DB 之类的常量若写死 C 盘，就永远找不到 → 设置材料失败。
#
# 修复原则（按用户要求）：不限定 C 盘，每个盘都找一次。

_MATERIAL_DB_CACHE = {"path": None, "scanned": False}


def _all_drive_letters_safe():
    """返回所有存在的盘符（如 ['C:', 'D:', 'Z:']）。"""
    try:
        return list(_all_drive_letters())
    except Exception:
        pass
    out = []
    if sys.platform == "win32":
        try:
            import string
            for letter in string.ascii_uppercase:
                d = letter + ":\\"
                if os.path.isdir(d):
                    out.append(letter + ":")
        except Exception:
            pass
    return out


def find_material_db(force_rescan=False):
    """【Bug3 修复】跨盘符全盘搜索 SolidWorks 材质库目录（sldmaterials）。

    搜索顺序（从最可靠到兜底）：
      1. 从 SW 安装目录（注册表/全盘探测得到的）推导 lang\\<lang>\\sldmaterials
      2. 每个盘符下的常见路径：
           <盘>:\\Program Files\\SOLIDWORKS Corp*\\SOLIDWORKS\\lang\\<lang>\\sldmaterials
           <盘>:\\SOLIDWORKS Data\\...
           <盘>:\\SOLIDWORKS Corp*\\...
      3. 每盘递归浅扫描（限制深度，避免全盘遍历过慢）兜底

    返回材质库目录绝对路径（含 .sldmat 的目录）；找不到返回 None。
    结果带缓存，避免重复扫描。
    """
    if _MATERIAL_DB_CACHE["scanned"] and not force_rescan:
        return _MATERIAL_DB_CACHE["path"]

    found = None
    # ── 1) 从 SW 安装目录推导 ──────────────────────────────────────────
    try:
        exe = find_sldworks_exe()
        if exe:
            sw_root = os.path.dirname(exe)          # ...\\SOLIDWORKS
            for lang in ("chinese-simplified", "english", "chinese-traditional"):
                cand = os.path.join(sw_root, "lang", lang, "sldmaterials")
                if os.path.isdir(cand):
                    found = cand
                    break
            if not found:
                lg = os.path.join(sw_root, "lang")
                if os.path.isdir(lg):
                    for sub in os.listdir(lg):
                        cand = os.path.join(lg, sub, "sldmaterials")
                        if os.path.isdir(cand):
                            found = cand
                            break
    except Exception:
        pass

    # ── 2) 每个盘符的常见路径 ──────────────────────────────────────────
    if not found:
        patterns = [
            r"Program Files\\SOLIDWORKS Corp*\\SOLIDWORKS\\lang\\*\\sldmaterials",
            r"Program Files\\SOLIDWORKS Corp*\\SOLIDWORKS\\lang\\sldmaterials",
            r"SOLIDWORKS Corp*\\SOLIDWORKS\\lang\\*\\sldmaterials",
            r"Program Files\\SolidWorks Corp*\\SolidWorks\\lang\\*\\sldmaterials",
            r"SOLIDWORKS Data\\lang\\*\\sldmaterials",
            r"SOLIDWORKS Data\\sldmaterials",
        ]
        for drive in _all_drive_letters_safe():
            for pat in patterns:
                try:
                    for d in glob.glob(os.path.join(drive + "\\", pat)):
                        if os.path.isdir(d):
                            found = d
                            break
                except Exception:
                    continue
                if found:
                    break
            if found:
                break

    # ── 3) 兜底：每盘浅扫描（深度受限）──────────────────────────────────
    if not found:
        for drive in _all_drive_letters_safe():
            # 常见一级目录
            for top in ("Program Files", "Program Files (x86)", "SOLIDWORKS Data", ""):
                base = os.path.join(drive + "\\", top) if top else drive + "\\"
                if not os.path.isdir(base):
                    continue
                try:
                    for entry in os.listdir(base):
                        if "SOLIDWORKS" not in entry.upper() and "SOLIDWORKS" not in entry:
                            continue
                        # 在该目录下浅搜 sldmaterials（深度<=4）
                        root = os.path.join(base, entry)
                        for depth, (dirpath, dirnames, _fns) in enumerate(os.walk(root)):
                            if depth > 4:
                                dirnames[:] = []
                                continue
                            for dn in list(dirnames):
                                if dn.lower() == "sldmaterials":
                                    found = os.path.join(dirpath, dn)
                                    break
                            if found:
                                break
                        if found:
                            break
                except Exception:
                    continue
                if found:
                    break
            if found:
                break

    _MATERIAL_DB_CACHE["path"] = found
    _MATERIAL_DB_CACHE["scanned"] = True
    return found


def list_material_databases():
    """列出所有找到的 .sldmat 文件（跨盘符），供设置材料时选用。"""
    out = []
    seen = set()
    dirs = []
    d = find_material_db()
    if d:
        dirs.append(d)
    # 补充常见位置
    for drive in _all_drive_letters_safe():
        for pat in (r"Program Files\\SOLIDWORKS Corp*\\SOLIDWORKS\\lang\\*\\sldmaterials",
                    r"SOLIDWORKS Data\\lang\\*\\sldmaterials"):
            try:
                dirs.extend(glob.glob(os.path.join(drive + "\\", pat)))
            except Exception:
                pass
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith(".sldmat"):
                    p = os.path.join(d, fn)
                    if p not in seen:
                        seen.add(p)
                        out.append(p)
        except Exception:
            continue
    return out


# 常用材料 → 密度 (kg/m³) 与 sldmat 中的英文库名
# 用途：① AddMaterial 失败时用 SetMaterialPropertyName 直接写密度兜底；
#      ② 校核密度是否合理（识别"恒为1000"的水密度异常）。
# ── 【Bug-13/29 修复】补全 3D 打印常用材料 ────────────────────────────────
# 原缺陷：本表只有金属 + ABS/POM，【没有 PETG / PLA / Nylon / TPU】。
#   而"3D打印"是本插件明确支持的任务类型（文档多处提到）：
#     · 小屋只能用 ABS 近似 PETG、手动设密度 1130 冒充 PA6（实测 Bug-29）；
#     · lookup_material("PETG") 拿不到密度 → set_material 兜底回退 1000（水）；
#     · 工程图材料栏写 ABS 而非真实打印材料，加工者会打错料。
# 修复：补齐常用打印材料的密度（并给出 E/屈服/UTS，供自定义材质直写）。
#   密度来源为公开供应商技术数据（Prusament / Bambu / SLS 粉末规格），
#   实际设计请以所用耗材的技术数据书为准。
# ══ 【国标材料 · 外部数据源】══════════════════════════════════════════
# 【重要】本表【不再内置任何国标（GB/T）材料数值】。
#
# 为什么撤销内置数值：
#   GB/T 是【国家统一标准】，材料的 E/屈服/抗拉/密度必须来自标准原文或
#   权威手册，不能由代码作者按经验填写。此前版本在源码里硬编码了一套
#   GB 数值 —— 既不可审计（无法追溯来源），也有"私造标准"的风险。
#   现在改为【运行时从外部 JSON 合并】，与 physics/material_db 共用同一份
#   数据源（tools/physics/gb_materials.json），保证两处口径永远一致。
#
# 数据来源：tools/physics/gb_materials.json（或环境变量 DSH_GB_MATERIALS）
# 未导入时：国标牌号查不到 -> 如实报"未收录"，绝不猜数值。
COMMON_MATERIALS = {
    # ── 国标牌号（【原有数据，恢复】只登记密度与别名）────────────────────
    # 说明：这些条目是项目原有内容，仅用于"认出用户说的牌号 + 提供密度"。
    #   它们的【力学属性（E/屈服/抗拉/泊松比）不在此写死】——
    #   GB/T 是国家统一标准，数值由外部 gb_materials.json 提供（见文件末尾
    #   的 _load_gb_into_common_materials）。导入后这些牌号会自动补齐属性；
    #   未导入时只能做密度/名称匹配，强度类判定会如实报"属性缺失"。
    "Q235": {"density": 7850, "aliases": ["Q235", "AISI 1020", "Plain Carbon Steel", "碳钢"]},
    "45#": {"density": 7850, "aliases": ["45", "AISI 1045", "1045"]},
    "40Cr": {"density": 7850, "aliases": ["40Cr", "AISI 5140", "5140", "Chromium Steel"]},
    "304": {"density": 8000, "aliases": ["304", "AISI 304", "Stainless Steel", "不锈钢"]},
    "316": {"density": 8000, "aliases": ["316", "AISI 316"]},
    "6061-T6": {"density": 2700, "aliases": ["6061", "6061-T6", "Aluminum Alloy 6061", "铝合金"]},
    "7075": {"density": 2810, "aliases": ["7075", "7075-T6"]},
    "Cr12MoV": {"density": 7700, "aliases": ["Cr12MoV", "D2", "Tool Steel"]},
    "HT200": {"density": 7200, "aliases": ["HT200", "Gray Cast Iron", "灰铸铁"]},
    # ── 3D 打印材料（【Bug-13/29】新增，原有数据）──────────────────────
    # 注意：不把 PETG-CF / PETG-GF 列为别名 —— 碳纤/玻纤增强的弹性模量
    #   (~6.5GPa) 与普通 PETG (2.0GPa) 差 3 倍，别名会让增强料被解析成普通料。
    #   增强料应由 set_custom_material 显式登记（custom_materials.json）。
    "PETG": {"density": 1270, "e_mpa": 2000, "yield_mpa": 40, "uts_mpa": 50,
             "poissons_ratio": 0.37,
             "aliases": ["PETG", "PET-G", "Copolyester"]},
    "PLA": {"density": 1240, "e_mpa": 3500, "yield_mpa": 55, "uts_mpa": 60,
             "poissons_ratio": 0.36,
            "aliases": ["PLA", "PLA+", "Polylactic Acid", "聚乳酸"]},
    "ABS": {"density": 1050, "e_mpa": 2400, "yield_mpa": 45, "uts_mpa": 50,
             "poissons_ratio": 0.4,
            "aliases": ["ABS", "Acrylonitrile Butadiene Styrene"]},
    "ASA": {"density": 1070, "e_mpa": 2300, "yield_mpa": 45, "uts_mpa": 50,
             "poissons_ratio": 0.4,
            "aliases": ["ASA"]},
    "Nylon": {"density": 1140, "e_mpa": 2800, "yield_mpa": 80, "uts_mpa": 85,
             "poissons_ratio": 0.39,
              "aliases": ["Nylon", "PA", "PA6", "PA66", "Nylon 6/6", "尼龙"]},
    "PA12": {"density": 1010, "e_mpa": 1700, "yield_mpa": 48, "uts_mpa": 50,
             "poissons_ratio": 0.39,
             "aliases": ["PA12", "PA-12", "Nylon 12", "尼龙12"]},
    "TPU": {"density": 1210, "e_mpa": 50, "yield_mpa": 30, "uts_mpa": 40,
             "poissons_ratio": 0.48,
            "aliases": ["TPU", "TPE", "Flexible", "热塑性聚氨酯"]},
    "PC": {"density": 1200, "e_mpa": 2400, "yield_mpa": 65, "uts_mpa": 70,
             "poissons_ratio": 0.37,
           "aliases": ["PC", "Polycarbonate", "聚碳酸酯"]},
    "POM": {"density": 1410, "e_mpa": 3100, "yield_mpa": 70, "uts_mpa": 75,
             "poissons_ratio": 0.35,
            "aliases": ["POM", "Delrin", "POM-C", "聚甲醛"]},
    "PEEK": {"density": 1320, "e_mpa": 3700, "yield_mpa": 100, "uts_mpa": 110,
             "poissons_ratio": 0.38,
             "aliases": ["PEEK"]},
    # ── 【通用结构钢兜底】非国标牌号，供 GB 数据未导入时使用 ──────────────
    # 说明：这里刻意【不使用 GB/T 牌号名】（如 Q235/40Cr），避免在数据未导入
    #   时"借用国标名义"给出数值。用的是国际通行的通用钢种命名，
    #   属性为工程手册通用值，可在没有国标数据时维持系统可用。
    #   一旦用户导入 gb_materials.json，国标牌号会被合并进来并【优先命中】。
    "PlainCarbonSteel": {
        "density": 7850, "e_mpa": 206000, "yield_mpa": 250, "uts_mpa": 400,
        "poissons_ratio": 0.30,
        "standard": "通用碳素结构钢（非 GB 牌号）",
        "aliases": ["PlainCarbonSteel", "Carbon Steel", "普通碳钢", "碳素钢",
                    "AISI 1020", "S235JR"],
    },
    "AlloySteel4140": {
        "density": 7850, "e_mpa": 205000, "yield_mpa": 655, "uts_mpa": 760,
        "poissons_ratio": 0.29,
        "standard": "通用合金结构钢（非 GB 牌号）",
        "aliases": ["AlloySteel4140", "AISI 4140", "42CrMo", "合金钢"],
    },
    "StainlessSteel304": {
        "density": 7930, "e_mpa": 193000, "yield_mpa": 205, "uts_mpa": 520,
        "poissons_ratio": 0.30,
        "standard": "通用奥氏体不锈钢（非 GB 牌号）",
        "aliases": ["StainlessSteel304", "AISI 304", "S30408", "Type 304"],
    },
    "Aluminum6061": {
        "density": 2700, "e_mpa": 68900, "yield_mpa": 276, "uts_mpa": 310,
        "poissons_ratio": 0.33,
        "standard": "通用铝合金（非 GB 牌号）",
        "aliases": ["Aluminum6061", "Aluminum 6061-T6", "Al 6061"],
    },
}


# ══ 【国标材料】从外部 JSON 合并进 COMMON_MATERIALS ══════════════════════
# 字段名转换：material_db 用 E_mpa/nu/density_kg_m3，
#   swapi 用 e_mpa/poissons_ratio/density -> 这里统一映射。
# 合并进来的条目【同样不写死在本文件】，来源可查（见 gb_materials_status）。
def _gb_json_candidates():
    """国标材料 JSON 的候选路径（与 physics/material_db 同序）。"""
    _here = os.path.dirname(os.path.abspath(__file__))
    _c = []
    _env = (os.environ.get("DSH_GB_MATERIALS") or "").strip()
    if _env:
        _c.append(_env)
    _c.append(os.path.join(_here, "physics", "gb_materials.json"))
    _c.append(os.path.join(_here, "gb_materials.json"))
    _c.append(os.path.join(_here, "physics", "rules", "gb_materials.json"))
    return _c


# 合并状态缓存（避免每次查表都读盘）
_GB_MERGED = {}


def _load_gb_into_common_materials(force=False):
    """把外部国标材料 JSON 合并进 COMMON_MATERIALS（幂等）。

    Returns: {ok, count, file, source, error}
    """
    _res = {"ok": False, "count": 0, "file": None, "source": None, "error": None}
    if _GB_MERGED.get("done") and not force:
        return dict(_GB_MERGED)
    for _p in _gb_json_candidates():
        try:
            if not _p or not os.path.isfile(_p):
                continue
            with open(_p, "r", encoding="utf-8-sig") as _f:
                _d = json.load(_f)
            if not isinstance(_d, dict):
                _res["error"] = "顶层不是对象: %s" % _p
                continue
            _mats = _d.get("materials")
            if not isinstance(_mats, dict) or not _mats:
                _res["error"] = "缺少 materials 段或为空: %s" % _p
                continue
            _n = 0
            for _k, _v in _mats.items():
                if not isinstance(_v, dict):
                    continue
                _key = str(_k)
                # ── 键的选择：必须用【图纸上的真实牌号】，不能从 ID 推导 ──
                # 踩坑：GB_6061_T6 去掉前缀得到 "6061_T6"（下划线），
                #   而内置键是 "6061-T6"（连字符）→ 同一牌号在表里出现两个
                #   键，外部条目的兜底数值填到了错键上，查询时反而取不到。
                # 正确做法：优先用 name_cn 里的牌号（斜杠/中文括号前），
                #   其次用 aliases 首个，最后才退回 ID 去前缀。
                _disp = None
                _cn = str(_v.get("name_cn") or "").strip()
                if _cn:
                    _head = _cn.split("（")[0].strip()
                    if "/" in _head:
                        _head = _head.split("/")[0].strip()
                    if _head:
                        _disp = _head
                if not _disp:
                    _al0 = [a for a in (_v.get("aliases") or []) if a]
                    if _al0:
                        _disp = str(_al0[0]).strip()
                if not _disp:
                    _disp = _key[3:] if _key.upper().startswith("GB_") else _key
                _entry = {}
                _al = list(_v.get("aliases") or [])
                if _v.get("density_kg_m3") is not None:
                    _entry["density"] = _v.get("density_kg_m3")
                if _v.get("E_mpa") is not None:
                    _entry["e_mpa"] = _v.get("E_mpa")
                if _v.get("nu") is not None:
                    _entry["poissons_ratio"] = _v.get("nu")
                if _v.get("yield_mpa") is not None:
                    _entry["yield_mpa"] = _v.get("yield_mpa")
                if _v.get("uts_mpa") is not None:
                    _entry["uts_mpa"] = _v.get("uts_mpa")
                if _v.get("standard"):
                    _entry["standard"] = _v.get("standard")
                if _v.get("name"):
                    _entry["name"] = _v.get("name")
                # 别名：保证牌号本身一定可被查到
                for _a in (_disp, _key):
                    if _a and _a not in _al:
                        _al.append(_a)
                _entry["aliases"] = _al
                _entry["gb_source"] = _d.get("source") or _p
                # ── 合并而非整体替换：外部缺的字段，沿用内置已有的值 ──────
                # 踩坑风险：外部 JSON 现阶段只有【标准身份 + 密度】，力学数值
                #   待补全。若直接整体覆盖，会把内置已有的 e_mpa / yield_mpa
                #   抹掉，导致本来能算的牌号反而算不了。
                _old = COMMON_MATERIALS.get(_disp)
                if not isinstance(_old, dict):
                    # 兜底：牌号写法有后缀差异时（"7075-T6" vs "7075"），
                    #   按"去后缀/去分隔符"再找一次，避免漏掉既有条目。
                    _nk = "".join(ch for ch in str(_disp).lower()
                                  if ch.isalnum())
                    for _ck, _cv in list(COMMON_MATERIALS.items()):
                        _ckn = "".join(ch for ch in str(_ck).lower()
                                       if ch.isalnum())
                        if _ckn and (_ckn == _nk or _ckn == _nk.split("-")[0]
                                     or _nk == _ckn.split("-")[0]):
                            _old = _cv
                            break
                if isinstance(_old, dict):
                    for _f in ("density", "e_mpa", "yield_mpa", "uts_mpa",
                               "poissons_ratio"):
                        if _entry.get(_f) is None and _old.get(_f) is not None:
                            _entry[_f] = _old.get(_f)
                    _old_al = list(_old.get("aliases") or [])
                    for _a in _old_al:
                        if _a and _a not in _al:
                            _al.append(_a)
                    _entry["aliases"] = _al
                    if not _entry.get("name") and _old.get("name"):
                        _entry["name"] = _old.get("name")
                # ── 【数值兜底 + 来源标注】无条件走 material_db，绝不自己判 ──
                # ⚠️ 【BUG 修复·平行路径绕过全部修复】实测（子代理独立验证）：
                #   旧实现只在 `_missing`（JSON 原生字段有空）时才调 material_db；
                #   一旦原生 5 字段齐全（本轮导入后 17/18 都是），`_missing == []`
                #   → 整段跳过 → 落进 `elif _entry.get("e_mpa")` 分支，
                #   被【无条件】写成 values_source="GB 标准数值"、
                #   values_from_gb=True。后果：
                #     · 17/18 牌号来源标注错误（E/ν/ρ 明明是手册值）
                #     · gb_sourced_fields / field_provenance / handbook_fields
                #       全部缺失 → 报告退化成 level=UNKNOWN
                #     · 【一致性守卫被整段绕过】→ ZL104 被撤回的
                #       σy=160/σb=150 在这里复活（σb<σy，物理不可能）
                # 根因是"同一判断写两遍"。现在改为【无条件】调用
                #   material_db.get_material() 并以它的结果为唯一真相，
                #   swapi 只做字段名映射，不再自行推导任何来源标记。
                _resolved = None
                try:
                    _pd = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "physics")
                    if _pd not in sys.path:
                        sys.path.insert(0, _pd)
                    import material_db as _mdb
                    _resolved = (_mdb.get_material(_key)
                                 or _mdb.get_material(_disp))
                except Exception:
                    _resolved = None

                if isinstance(_resolved, dict):
                    # ① 数值：material_db 是唯一真相（含守卫后的结果）
                    _map = {"density": "density_kg_m3", "e_mpa": "E_mpa",
                            "yield_mpa": "yield_mpa", "uts_mpa": "uts_mpa",
                            "poissons_ratio": "nu"}
                    for _f, _src_f in _map.items():
                        _sv = _resolved.get(_src_f)
                        if _sv is not None:
                            _entry[_f] = _sv
                        elif _f in _entry and _resolved.get(_src_f) is None:
                            # material_db 判定该字段不可用（如守卫撤回）→
                            #   必须一并清空，否则旧值会"复活"
                            if _f in ("yield_mpa", "uts_mpa"):
                                _entry.pop(_f, None)
                    # ② 来源标记：全部透传（含 None 也要覆盖，避免残留旧值）
                    for _k in ("values_source", "values_from_gb", "values_pending",
                               "gb_sourced_fields", "field_provenance",
                               "consistency_guard", "missing_fields",
                               "handbook_fields", "native_fields",
                               "fallback_fields", "equivalent_name",
                               "equivalent_filled"):
                        if _k in _resolved:
                            _entry[_k] = _resolved.get(_k)
                    if _v.get("equivalent_id"):
                        _entry["equivalent_id"] = _v.get("equivalent_id")
                else:
                    # material_db 不可用（极端情况）→ 保守标注，绝不宣称国标
                    if not _entry.get("values_source"):
                        _entry["values_source"] = (
                            "未知来源（material_db 不可用）—— 不得宣称为国标值")
                        _entry["values_from_gb"] = False
                        _entry["values_pending"] = True
                COMMON_MATERIALS[_disp] = _entry
                _n += 1
            _res.update({"ok": bool(_n), "count": _n, "file": _p,
                         "source": _d.get("source"), "error": None})
            break
        except Exception as _e:
            _res["error"] = "读取失败 %s: %r" % (_p, _e)
            continue
    if not _res["ok"] and not _res["error"]:
        _res["error"] = ("未找到国标材料数据文件；请把它放到 %s"
                         "（或设 DSH_GB_MATERIALS 指向它）"
                         % os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "physics", "gb_materials.json"))
    _res["done"] = True
    _GB_MERGED.clear()
    _GB_MERGED.update(_res)
    return dict(_res)


def gb_materials_status():
    """国标材料数据源状态（供 doctor / 报告展示"数值从哪来"）。

    ── 【口径与 material_db 对齐】────────────────────────────────────────
    两个 gb_materials_status 必须给出同一套字段，否则排障时会被
    "两边数字不一样"误导（实测已出现：一处 error 有内容、另一处 null）。
    因此这里直接以 material_db 的口径为准，再补上 swapi 侧特有的信息。
    """
    _load_gb_into_common_materials()
    _out = {
        "loaded": bool(_GB_MERGED.get("ok")),
        "count": _GB_MERGED.get("count") or 0,
        "file": _GB_MERGED.get("file"),
        "source": _GB_MERGED.get("source"),
        "error": _GB_MERGED.get("error"),
        "searched": _gb_json_candidates(),
    }
    # 复用 material_db 的分层计数（原生齐 / 经兜底可用 / 真待补全）
    try:
        _pd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "physics")
        if _pd not in sys.path:
            sys.path.insert(0, _pd)
        import material_db as _mdb
        _st = _mdb.gb_materials_status()
        for _k in ("identity_count", "ready_count", "equivalent_ready_count",
                   "pending_count", "native_ready_ids", "equivalent_ready_ids"):
            _out[_k] = _st.get(_k)
        # error 也统一（material_db 会写明"哪些牌号待补全"）
        if not _out.get("error"):
            _out["error"] = _st.get("error")
    except Exception as _e:
        _out["status_error"] = repr(_e)
    return _out


def reload_gb_materials():
    """重新载入国标材料（导入新数据后调用，无需重启进程）。"""
    return _load_gb_into_common_materials(force=True)


# 启动时自动合并一次（失败不影响内置的欧美标/塑料可用）
try:
    _load_gb_into_common_materials()
except Exception:
    pass

# ── 【BUG-05/08 修复】新建零件的默认材料 ───────────────────────────────────
# 原缺陷：从不赋材质 → 密度恒为 1000 kg/m³（水）。
# ⚠️ 【BUG-05 二次修复 · 重要】默认值不能想当然取"最常见"的材料：
#   实机事故：默认 Q235 → SW 库里没有 "Q235" → 回退别名 "AISI 1020"(钢,7900)，
#   而任务要求的是 6061-T6 铝(2700) → "图纸写铝、零件是钢"。
#   现在：
#     ① 默认仍取 Q235（钢），但它是【可被 env/参数覆盖】的兜底，
#        且赋材质失败时会【明确报错】，不再静默降级成别的材料；
#     ② 真正的正确做法是让调用方按设计意图显式指定（见 new_part(material=)）；
#     ③ 若设计工况已落盘（gate_load_case.json），
#        swapi 会自动读取其中的材料作为默认值（见 _material_from_load_case）。
DEFAULT_PART_MATERIAL = "Q235"


def default_part_material_name():
    """【GB 外部数据源配套】返回当前【可用】的默认零件材料名。

    ── 语义（F-05 重定义）──────────────────────────────────────────────
    返回【当前真的可用的】默认零件材料名；不可用时返回 None。

    ══ 【F-05 修复·核心】默认材料必须是【防线合法牌号】，禁止静默降级 ═══
    原缺陷：Q235 不在表里时，按顺序降级到 PlainCarbonSteel /
      AlloySteel4140 / StainlessSteel304 / Aluminum6061 —— 全是【欧美牌号】；
      最后还给了一个「任意首项」兜底（字典里第一项是什么就返回什么，
      可能直接返回个 3D 打印材料 PLA）。
      后果不是「能用」，而是【悄悄换材料】：
        · 图纸/工况写 Q235（GB 碳素结构钢，σy=235MPa）；
        · 零件里却是 PlainCarbonSteel（σy=250）或更离谱的 4140（σy=655）；
        · 强度校核基准整体错位，而返回值看起来一切正常。

    现在：
      · Q235 可用（在库 + 有密度 + 防线认）→ 返回 "Q235"；
      · 否则 → 返回 None。这是一个【明确的信号】，不是「没有默认值」：
        调用方（resolve_default_material_or_raise / new_part）据此
        显式报错并给出可执行路径，绝不让非法名流到下游去炸。
    """
    if is_default_material_available():
        return "Q235"
    return None


def is_default_material_available():
    """【F-05 新增】默认材料 Q235 当前是否【真的可写、且合法】。

    判据（两条都必须成立）：
      ① 在 COMMON_MATERIALS 中（内置/GB JSON 已导入）；
      ② is_valid_material("Q235") 通过 —— 即材料防线【认这个名字】。
         这两条不是一回事：表里有条目但缺密度时，set_material 仍写不进去，
         防线仍会判 material_invalid。

    Returns: bool
    """
    try:
        if "Q235" not in COMMON_MATERIALS:
            return False
    except Exception:
        return False
    try:
        _ok, _info, _ = is_valid_material("Q235")
        if not _ok or not _info:
            return False
        # 必须有密度 —— 没有密度就写不进 SW，读回会掉回 1000（水）
        return bool(_info.get("density") or _info.get("density_kg_m3"))
    except Exception:
        return False


def default_material_unavailable_error():
    """【F-05 新增】默认材料不可用时的【可执行】错误说明（给 new_part 用）。

    原则：绝不静默降级成欧美牌号（那会让「图纸写 Q235、零件是别的钢」），
    而是把「怎么修」直接写清楚，让上层能照做。
    """
    try:
        _st = gb_materials_status()
    except Exception:
        _st = {}
    _files = (" / ".join(str(x) for x in (_st.get("searched") or [])[:3])
              or "tools/physics/gb_materials.json")
    return (
        "默认材料 Q235 当前【不可用】：COMMON_MATERIALS 中没有该牌号，"
        "或它缺少密度/力学属性。\n"
        "本仓库【绝不】为默认值静默降级成欧美牌号（PlainCarbonSteel 等）——"
        "那会造成「图纸写国标、零件是另一种材料」，强度校核基准整体错位。\n"
        "可执行路径（任选其一）：\n"
        "  ① 导入国标数据（推荐，一次到位）：把 gb_materials.json 放到 "
        "%s 之一，或设环境变量 DSH_GB_MATERIALS 指向它，"
        "然后调用 swapi.gb_materials_status() 确认 loaded=True；\n"
        "  ② 显式登记该牌号：swapi.save_custom_material('Q235', "
        "density_kg_m3=7850, e_mpa=206000, yield_mpa=235, uts_mpa=375)；\n"
        "  ③ 显式指定其它【合法】牌号：new_part(material='6061-T6', "
        "density=2700) —— 但要与图纸一致；\n"
        "  ④ 显式指定密度兜底：new_part(material='Q235', density=7850)"
        "（牌号仍需可解析，否则密度也救不回材料身份）。\n"
        "当前国标数据状态：loaded=%s, count=%s, error=%s"
        % (_files, _st.get("loaded"), _st.get("count"), _st.get("error")))


def _material_standard_of(name):
    """【Bug-C 修复】判定材料名所属的【标准体系】。

    Returns: "GB" / "ASTM" / "EN" / "JIS" / "DIN" / "ISO" / None（无法判定）

    判据（按牌号命名特征）：
      · GB    —— Q235 / Q355 / 45# / 40Cr / 20CrMnTi / GCr15 / Cr12MoV /
                 HT200 / QT500-7 / 2A12 / LY12 / ZCuSn10 / H62 / ZL104 /
                 06Cr19Ni10 / 022Cr17Ni12Mo2（GB/T 新牌号）
      · ASTM  —— AISI/ASTM 数字牌号（1020/1045/4140/4340）、A36、Grade 5
      · EN    —— S235JR / S355 / 1.4301 等
      · JIS   —— SUS304 / S45C 等
      · ISO   —— ISO 前缀
    """
    s = str(name or "").strip()
    if not s:
        return None
    sl = s.lower()
    # ── GB 特征（优先级最高：这些牌号形态独一无二）──────────────────
    import re as _re
    if _re.search(r"(^|[^a-z])q\d{3}", sl):                    # Q235 / Q355
        return "GB"
    if _re.search(r"\b(ht|qt|yt|zt)\d{3}", sl):                 # HT200 / QT500-7
        return "GB"
    if _re.search(r"\b(gcr|zcusn|znal|zal|zcu)", sl):           # GCr15 / ZCuSn10
        return "GB"
    if _re.search(r"\b(cr12mov|20crmnti|40cr|65mn|38crmoala)\b", sl):
        return "GB"
    if _re.search(r"\b(2a12|ly12|5a02|7a04|2a11)\b", sl):       # 硬铝旧/新牌号
        return "GB"
    if _re.search(r"^\d{2,3}#?$", sl):                          # 45 / 45#
        return "GB"
    if _re.search(r"^(06cr|022cr|12cr|10cr|0cr|1cr|2cr|3cr)", sl):
        return "GB"                                             # GB/T 20878 不锈钢
    if _re.search(r"\bh62\b|\bh59\b|\bh68\b|\bzl\d{3}\b", sl):
        return "GB"
    # ── 欧美日标准 ──────────────────────────────────────────────────
    if _re.search(r"\baisi\b|\bastm\b|\bsae\b|\ba36\b", sl):
        return "ASTM"
    if _re.search(r"^s\d{3}(jr|j0|j2|mc|ml)?$|\b1\.\d{4}\b", sl):
        return "EN"
    if _re.search(r"^sus\d|\bs\d{2}c\b|\bsk\d\b", sl):
        return "JIS"
    if _re.search(r"^iso\b|\biso\d", sl):
        return "ISO"
    if _re.search(r"^din\b|\bck\d{2}\b|\b42crmo4\b", sl):
        return "DIN"
    # 纯数字牌号（1020/1045/4140/4340/304/316）→ 按 AISI/通用不锈钢处理
    if _re.search(r"^(10|11|12|41|43|86)\d{2}$", sl):
        return "ASTM"
    if _re.search(r"^(304|316|316l|321|410|420|430)$", sl):
        return "ASTM"
    return None


# 标准体系的"国家/地区"归类：GB 与欧美日属【不同体系】，互换需显式确认
_STANDARD_FAMILY = {
    "GB": "CN",
    "ASTM": "US", "AISI": "US", "SAE": "US",
    "EN": "EU", "DIN": "EU",
    "JIS": "JP",
    "ISO": "INTL",
}


def _resolve_material_props_any(name):
    """【Bug-C 修复】查任意材料名的屈服/密度属性（内置表 + 材料库）。

    仅供跨标准替代的"属性差异佐证"使用；查不到返回 None（不阻断判定）。
    """
    try:
        _ok, _info, _ = is_valid_material(name)
        if _ok and _info:
            _sy = _info.get("yield_mpa") or _info.get("yield_strength_mpa")
            if _sy:
                return {"yield_mpa": _sy,
                        "density_kg_m3": _info.get("density_kg_m3") or _info.get("density")}
    except Exception:
        pass
    # 兜底：physics 侧材料库（含 GB 系列与欧美标）
    try:
        import sys as _sys
        _pd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "physics")
        if _pd not in _sys.path:
            _sys.path.insert(0, _pd)
        import material_db as _mdb
        _m = _mdb.get_material(name)
        if _m:
            return {"yield_mpa": _m.get("yield_mpa"),
                    "density_kg_m3": _m.get("density_kg_m3")}
    except Exception:
        pass
    return None


# 等价命名对：同一钢种在不同标准下的官方命名（成分与力学性能等价），
#   属"同一材料的两种叫法"，不算跨标准替代，检测时直接放行。
#   与"等级不同"的伪别名（Q235↔AISI 1020，σy 差 49%）严格区分。
_EQUIVALENT_NAMES = (
    # 不锈钢：GB/T 20878 与 AISI 数字牌号一一对应
    ("304", "aisi 304"), ("s30408", "aisi 304"), ("06cr19ni10", "aisi 304"),
    ("304", "s30408"), ("06cr19ni10", "304"), ("0cr18ni9", "304"),
    ("316", "aisi 316"), ("s31603", "aisi 316"), ("316l", "aisi 316"),
    ("022cr17ni12mo2", "316l"), ("022cr17ni12mo2", "316"),
    ("316", "316l"), ("316", "s31603"),
    # 铝合金：GB/T 3190 与 ANSI 牌号同名
    ("6061", "6061-t6"), ("7075", "7075-t6"), ("2a12", "2024"),
    # 钛合金：GB/T 3620 与 ASTM 同名
    ("tc4", "ti-6al-4v"), ("ta2", "grade 2"),
    # 结构钢：Q235 与 S235JR 属"设计等效"（屈服同为 235MPa）
    ("q235", "s235jr"), ("q355", "s355jr"),
)


def _norm_name_key(s):
    """归一化材料名用于比较：剥 BOM、全角转半角、小写、去分隔符。

    ── 【P2 修复】必须处理 BOM 与全角 ────────────────────────────────────
    实测（子代理3）：\\ufeffQ235（带 BOM）与 Ｑ２３５（全角）都会被判
      "属性查不到" → 走到 fail-closed 的"需人工确认"，用户看到的是
      "材料名不对"却查不出原因（实际只是字符形态差异）。
    现在统一归一化：
      · 剥 UTF-8 BOM（\\ufeff）与零宽字符；
      · 全角字母/数字/符号转半角（Ｑ２３５ → Q235）；
      · 小写、去空格/连字符/下划线/括号/中点。
    """
    import re as _re
    _t = str(s or "")
    # ① 剥 BOM 与零宽字符
    _t = _t.replace("\ufeff", "").replace("\u200b", "").replace("\u200e", "")
    # ② 全角 → 半角（全角字符码位 = 半角 + 0xFEE0；全角空格 U+3000 → 半角）
    _out = []
    for _ch in _t:
        _cp = ord(_ch)
        if _cp == 0x3000:
            _out.append(" ")
        elif 0xFF01 <= _cp <= 0xFF5E:
            _out.append(chr(_cp - 0xFEE0))
        else:
            _out.append(_ch)
    _t = "".join(_out).strip().lower()
    # ③ 去常见分隔符
    _t = _re.sub(r"[\s\-_（）()·.]+", "", _t)
    return _t


def _is_equivalent_naming(a, b):
    """【Bug-C 修复】判定两个名称是否为【等价命名】（同一材料的不同标准写法）。

    用途：避免把 "304" 与 "AISI 304" 这类同一钢种的等价写法误报为
      跨标准替代，同时仍然拦截 Q235→AISI 1020 那类真正不等价的替代。
    """
    _a = _norm_name_key(a)
    _b = _norm_name_key(b)
    if not _a or not _b:
        return False
    if _a == _b:
        return True
    for _x, _y in _EQUIVALENT_NAMES:
        _nx, _ny = _norm_name_key(_x), _norm_name_key(_y)
        if (_a == _nx and _b == _ny) or (_a == _ny and _b == _nx):
            return True
    return False


def _gb_material_advice(name):
    """【R9 修复】缺国标牌号时，给出**可执行**的落地路径。

    台账 BUG-30/56 的核心痛点：SolidWorks 官方材质库
    （SolidWorks Materials / DIN Materials）**没有 GB 牌号**
    （Q235 / 45 / 40Cr / 20CrMnTi / GCr15 / HT200 / ZL104 …）。
    于是 set_material("Q235") 必然找不到 → 旧实现回退到最像的
    AISI 1020 / 普通碳钢，把"国标"悄悄变成"美标"。

    正确落地路径只有两条（都能让 .sldprt 里真的是那个牌号）：
      ① 用 set_custom_material() 把该牌号连同 E/ν/σy/σb/ρ 登记到
         custom_materials.json，再赋材 —— 本仓库的凭据/防线认这条路；
      ② 在 SolidWorks 里新建自定义材质（.sldmat），把牌号写进
         Custom Materials 库，再用 set_material(name, database="Custom Materials")。
    这里把两条都写清楚，并**明确禁止**"近似成欧美标"。

    Args:
        name: 用户请求的材料名
    Returns:
        str: 可操作建议（可直接放进 result["hint"]）
    """
    _nm = str(name or "").strip() or "<该牌号>"
    _std = _material_standard_of(_nm)
    _is_gb = (_std == "GB")
    _NL = chr(10)
    _head = (
        ("【国标牌号缺失】%r 属于 %s 标准体系，SolidWorks 官方材质库"
         "（SolidWorks Materials / DIN Materials）【不收录 GB 牌号】—— "
         "这不是调用方式错了，是库本身没有。"
         % (_nm, _std or "未知")) + _NL)
    _paths = _NL.join((
        "可执行路径（二选一，都能让 .sldprt 里真的是该牌号）：",
        "  ① 登记自定义材料（推荐，本仓库凭据/防线认这条路）：",
        "     m.set_custom_material(%r, e_mpa=..., yield_mpa=..., uts_mpa=..., "
        "density_kg_m3=...)" % (_nm,),
        "     它会写入 custom_materials.json 并再次尝试赋材；",
        "  ② 在 SolidWorks 内新建自定义材质库（.sldmat）并把该牌号加进 "
        "Custom Materials，然后：",
        "     m.set_material(%r, database='Custom Materials')" % (_nm,),
    )) + _NL
    _warn = _NL.join((
        "⛔ 严禁【近似成欧美标】：把 %r 写成 AISI 1020 / 普通碳钢 之类，"
        "会让图纸与零件材料不一致（屈服强度可能差 40%%+），"
        "质量/强度/寿命校核全部失效且无人察觉。" % (_nm,),
        "   如确需跨标准替代，必须显式传 allow_cross_standard=True "
        "并在报告中如实标注，由人工确认。",
    ))
    if _is_gb:
        return _head + _paths + _warn
    return (_paths + _warn)


# ── 【A-30 修复】SW 回读的三段式复合名分隔符 ────────────────────────────────
# 实测：SW2025 的 GetMaterialPropertyName2 在"库/类别/材料名"三段式配置下回读为
#   "SolidWorks Materials|钢|普通碳钢"（管道符作分隔，也可能是 "\\" 或 "/"）。
# 旧实现一律取【最后一段】——对上面这条恰好正确；但对
#   "SolidWorks Materials|普通碳钢|Q235"  这类"库名|材料名|规格后缀"的写法，
#   取最后一段会得到 "Q235"，而请求名是 "普通碳钢"，逐字比对必判不一致。
# 正确的做法不是猜"哪一段是名字"，而是【枚举所有候选段】，只要任一段与
#   请求名归一化后相等，就认定是同一种材料（见 _material_name_variants /
#   _material_names_match）。这样既保住"逐字比对、不做同义词替换"的不变量，
#   又不会因为 SW 的拼接顺序不同而误判为"材料被替换"。
_MATERIAL_NAME_SEPS = ("|", "\\", ":", "：")


def _material_name_variants(name):
    """把一个材质名拆成【全部可比较的候选写法】（顺序无关、去重、已归一化）。

    例：
      "SolidWorks Materials|普通碳钢"      → {"solidworks materials|普通碳钢", "普通碳钢"}
      "SolidWorks Materials|钢|Q235"       → {..., "solidworks materials|钢|q235", "钢|q235", "q235"}
    【不做同义词替换】，只做"拆段 + 折叠空白 + 忽略大小写"。
    """
    s = str(name or "").strip()
    if not s:
        return []
    out = []
    def _add(x):
        x = " ".join(str(x or "").split())
        if x:
            k = x.casefold()
            if k not in out:
                out.append(k)
    _add(s)
    # 逐级剥掉"最左边一段"，把所有后缀都作为候选（顺序无关）
    cur = s
    for _sep in _MATERIAL_NAME_SEPS:
        if _sep in cur:
            _parts = [p.strip() for p in cur.split(_sep)]
            _parts = [p for p in _parts if p]
            # 所有后缀（"A|B|C" → "B|C", "C"）+ 所有单段（"A","B","C"）
            for _i in range(1, len(_parts)):
                _add(_sep.join(_parts[_i:]))
            for _p in _parts:
                _add(_p)
    # 常见库名前缀（"SolidWorks Materials - xxx" 之类）
    for sep in (" - ", " – ", " — "):
        if sep in s:
            _add(s.split(sep)[-1])
    return out


def _normalize_material_name(name):
    """【R9 修复】归一化材质名，供"读回校验/跨标准判定"逐字比对。

    为什么需要：SW 的材质回读格式在不同版本/语言下不统一，实测见过：
      · "普通碳钢"                          （中文库显示名）
      · "SolidWorks Materials|普通碳钢"     （库名|材料名）
      · "solidworks materials|AISI 1020"   （大小写/空格不一）
      · "AISI 1020"                        （纯材料名）
    若直接字符串相等比较，上面四种写法会被判成"四个不同材料"——
    这正是 33/35 件 sw_internal_matches_request=false 的一类假阴性。
    这里只做**无损归一化**（去库名前缀、折叠空白、忽略大小写），
    【绝不做同义词替换】—— 那是跨标准替代，必须显式判定（见
    _detect_cross_standard），不能藏在归一化里偷偷放过。

    ── 【A-30 修复】不再"只取最后一段" ────────────────────────────────────
    旧实现 `s.split("|")[-1]` 假设材料名恒在末段；三段式（库|类别|材料）或
    "库|材料|规格" 的写法会让它取错段，导致【已正确赋材】被判成不一致。
    现在取"最长的那一段"只作为【显示用】归一化（排序稳定），真正的判定走
    _material_names_match() 的候选集合比对。
    """
    _vs = _material_name_variants(name)
    if not _vs:
        return ""
    # 显示口径：优先取"不含任何分隔符的那个完整段"里最短的一个
    #   （"普通碳钢" 优于 "solidworks materials|普通碳钢"），仅为可读性，
    #    判定不依赖它。
    _bare = [v for v in _vs if not any(_s in v for _s in _MATERIAL_NAME_SEPS)]
    # 不再保留 return 之后的旧实现 —— 那是归一化的死代码，
    #   留着会让后来者以为"这里还会再拆一次库名前缀"。
    return (_bare[0] if _bare else _vs[-1])


def _material_names_match(actual, requested):
    """【R9 修复】判断"SW 内部回读的材质名"是否就是"我们请求/写入的名字"。

    只做**归一化后逐字比对**：
      · 相同 → True（真的是同一个材料）
      · 不同 → False（哪怕是"看起来相近"的牌号，也不放过）

    ★ 关键设计：**不允许任何模糊匹配**。
      历史实现用 startswith/包含关系做"近似匹配"，结果 Q235 → AISI 1020
      这种跨标准替换被当成匹配通过 —— 就是 BUG-55「跨标准替代被误判」。
      跨标准替代必须走 _detect_cross_standard 显式标记并由上层拒绝，
      不能在这里被悄悄吸收。
    """
    # ── 【A-30 修复】按【候选集合】求交集，而不是比"各自最后一段" ──────────
    # 旧实现：两侧各取 split("|")[-1] 再逐字比较。三段式 / 前后段顺序不同的
    #   复合名（"SolidWorks Materials|普通碳钢|Q235" vs "普通碳钢"）会取到不同段，
    #   于是【真实一致】的材料被判成不一致 → sw_internal_matches_request=false
    #   假阳性 → 防线①对已正确赋材的零件反复报"材料被替换"。
    # 现在：两侧各展开成"全部可能写法"的集合，【只要有任一写法相同】即为同一材料。
    #   仍然不做同义词替换、不做 startswith/包含匹配 —— 跨标准替代依旧不会
    #   在这里被悄悄吸收（Q235 与 AISI 1020 的候选集合没有任何交集）。
    _av = _material_name_variants(actual)
    _rv = _material_name_variants(requested)
    if not _av or not _rv:
        return False
    return bool(set(_av) & set(_rv))


def _read_back_material(model):
    """【R9 修复】写入材质后，**独立回读** SW 内部真实生效的材质名。

    为什么必须回读（台账 BUG-30/55/56 的核心）：
      SetMaterialPropertyName2 返回 None（void），SW 不会告诉你"写没写成"，
      更不会告诉你"是不是被替换成了别的材料"。实测 33/35 件
      sw_internal_matches_request=false —— 凭据与实物脱节，而所有调用都
      "看起来成功"。唯一可靠的办法是写完之后**再读一遍**。

    回读顺序（全部有类型库依据，见文件顶部 R11 段的行号表）：
      ① IPartDoc.GetMaterialPropertyName2(ConfigName, Database)
         —— 类型库 L47356，**方法**，2 参（第 2 参是 out 参数）。
         这是最权威的"SW 认为当前是什么材料"。
      ② IPartDoc.MaterialIdName / MaterialUserName
         —— 类型库 L47915/L47917，**属性**，绝不能加括号。
         ① 失败时用它兜底。

    Args:
        model: SWModel 实例
    Returns:
        {"name": str|None, "method": str|None, "error": str|None}
    """
    out = {"name": None, "method": None, "error": None}
    _md = getattr(model, "model", None)
    if _md is None:
        out["error"] = "model 为 None"
        return out
    # ① GetMaterialPropertyName2(ConfigName, Database) —— 方法，2 参
    try:
        _nm = _com_get(_md, "GetMaterialPropertyName2", "", "")
        if _nm is not None and str(_nm).strip():
            out["name"] = str(_nm).strip()
            out["method"] = "GetMaterialPropertyName2"
            return out
    except ComMemberError as _e1:
        out["error"] = str(_e1)
    except Exception as _e1b:
        out["error"] = repr(_e1b)
    # ② 属性兜底：MaterialIdName（注意：是属性，不加括号）
    for _prop in ("MaterialIdName", "MaterialUserName"):
        try:
            _v = _com_get_soft(_md, _prop, default=None)
            if _v is not None and str(_v).strip():
                out["name"] = str(_v).strip()
                out["method"] = _prop
                return out
        except Exception:
            continue
    return out


def _detect_cross_standard(requested, applied):
    """【Bug-C 修复】判定"请求材料 → 实际生效材料"是否为跨标准体系替代。

    实测缺陷：请求 Q235（GB/T 700），SW 库无此牌号时回退命中 AISI 1020
      （美标），而凭据只标 approximate_match=false —— 报告看起来像精确匹配，
      用户拿到的校核结论与图纸牌号不是一回事。两者屈服强度差约 49%。

    Returns: (is_cross: bool, note: str)
      · 两者属不同标准体系（如 GB vs US）→ True + 说明
      · 同体系或无法判定 → False + ""（不干扰）
    """
    _rq = str(requested or "").strip()
    _ap = str(applied or "").strip()
    if not _rq or not _ap or _rq.lower() == _ap.lower():
        return False, ""
    # ── 【Bug-C 修复】等价命名（如 304 ↔ AISI 304）不算跨标准替代 ────────
    if _is_equivalent_naming(_rq, _ap):
        return False, ""
    _s1 = _material_standard_of(_rq)
    _s2 = _material_standard_of(_ap)
    if not _s1 or not _s2:
        return False, ""
    _f1 = _STANDARD_FAMILY.get(_s1)
    _f2 = _STANDARD_FAMILY.get(_s2)
    if _f1 and _f2 and _f1 != _f2:
        # 附带属性级差异佐证（若两者都能查到属性）
        _diff = ""
        try:
            _p1 = _resolve_material_props_any(_rq)
            _p2 = _resolve_material_props_any(_ap)
            if _p1 and _p2 and _p1.get("yield_mpa") and _p2.get("yield_mpa"):
                _d = abs(_p1["yield_mpa"] - _p2["yield_mpa"]) / float(_p1["yield_mpa"]) * 100
                _diff = ("屈服强度 %s→%s MPa（差 %.0f%%）"
                          % (_p1["yield_mpa"], _p2["yield_mpa"], _d))
        except Exception:
            _diff = ""
        return True, (
            "【跨标准替代】请求 %s（%s 体系），实际生效 %s（%s 体系）—— "
            "两者属不同标准体系%s，必须人工确认后方可采用（材料防线会据此拦截）。"
            % (_rq, _s1, _ap, _s2, ("，" + _diff) if _diff else ""))
    return False, ""


def _material_from_load_case():
    """【BUG-05 二次修复】从门禁落盘的载荷工况里读设计材料。

    门禁 select/provide_context 阶段已把"任务要求的材料"写进
    tools/load_cases/gate_load_case.json（如 Aluminum 6061-T6）。
    建模时若调用方没显式指定材料，用它是【最贴合设计意图】的默认值 ——
    从根本上避免"门禁要求铝、零件默认成钢"这类事故。

    Returns: 材料名（str）或 None
    """
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "load_cases", "gate_load_case.json")
        if not os.path.isfile(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        mat = d.get("material") or {}
        # 优先用库内可解析的牌号：id 映射回常用名，其次 name
        mid = str(mat.get("id") or "")
        _ID_TO_NAME = {
            # ── 【国标材料修复】GB 牌号 id → SW 中文材料库常用名 ──────────
            #   门禁现在落盘的是 GB id（GB_Q235 / GB_40CR / GB_HT200…），
            #   这里必须能把它换回 SW 材质库里真实存在的牌号名，
            #   否则赋材质会找不到、密度又掉回 1000（水）。
            "GB_Q235": "Q235", "GB_Q355": "Q355", "GB_45": "45",
            "GB_40CR": "40Cr", "GB_20CRMNTI": "20CrMnTi",
            "GB_GCr15": "GCr15", "GB_CR12MOV": "Cr12MoV",
            "GB_65MN": "65Mn", "GB_HT200": "HT200", "GB_QT500_7": "QT500-7",
            "GB_2A12": "2A12", "GB_6061": "6061-T6", "GB_7075": "7075",
            "GB_ZL104": "ZL104",
            "GB_06CR19NI10": "304", "GB_022CR17NI12MO2": "316",
            "GB_ZCUSN10": "ZCuSn10P1", "GB_H62": "H62",
            # ── 兼容历史欧美标 id（旧工况文件仍可能带这些）──────────────
            "AL_6061_T6": "6061-T6", "AL_6061": "6061-T6", "AL_7075": "7075",
            "AL_5052": "5052", "AL_1060": "1060",
            "ST_API_S235": "Q235", "ST_API_1020": "Q235",
            "ST_API_4140": "40Cr", "ST_API_4340": "40Cr",
            "ST_STAINLESS_304": "304", "ST_STAINLESS_316": "316",
            "TI_GRADE5": "Ti-6Al-4V", "TI_GRADE1": "Ti",
            "ABS": "ABS", "PC_POLYCARBONATE": "PC", "NYLON_6_6": "Nylon",
        }
        if mid in _ID_TO_NAME:
            return _ID_TO_NAME[mid]
        # ── 【F-05 修复·核心】只允许返回【防线合法牌号】，绝不返回标准文件全名 ──
        # 事故（两小屋独立复现）：工况文件里
        #     {"id": "Q235", "name": "GB/T 700-2006 碳素结构钢"}
        #   id 是 "Q235"（已合法），但不在上面的 _ID_TO_NAME 表里（表里只有
        #   "GB_Q235" 这类 GB_ 前缀 id）→ 继续往下走，拿 name 做正则匹配。
        #   而 name 是【标准文件的名字】("GB/T 700-2006 碳素结构钢")，里面
        #   根本没有 "Q235" 子串 → 正则不中 → **原样 return nm**。
        #   于是 new_part 默认材料 = "GB/T 700-2006 碳素结构钢"，
        #   is_valid_material() 判 False → 凭据 material_invalid=true、
        #   不签发 → 材料防线直接拦下，正常建模路径必然走不通。
        # 修复：按【可靠性从高到低】三条路取牌号，且每条都必须过
        #   is_valid_material() 校验 —— 交不出合法名就返回 None，
        #   由调用方回落到 DEFAULT_PART_MATERIAL（Q235），绝不把标准名当牌号。
        def _as_legal_grade(_cand):
            """把一个候选名规整成合法牌号；不合法返回 None。"""
            _c = str(_cand or "").strip()
            if not _c:
                return None
            try:
                _ok, _info, _ = is_valid_material(_c)
                if _ok and _info:
                    # 命中就用 canonical（"GB_Q235" → "Q235"），保证是牌号口径
                    return str(_info.get("canonical") or _c)
            except Exception:
                pass
            return None

        nm = str(mat.get("name") or "").strip()
        # ① id 本身可能就是牌号（"Q235" / "Q355" / "6061-T6" / "ABS"）
        _g = _as_legal_grade(mid)
        if _g:
            return _g
        # ② name 可能正好是某个牌号的【标准全名】——
        #    COMMON_MATERIALS 每条都有 "name" 字段（如 Q235 的
        #    "GB/T 700-2006 碳素结构钢"），这里做一次【反向查表】。
        #    这是"标准名 → 牌号"最准的一条路：表内是精确串比对，不猜。
        if nm:
            try:
                for _canon, _info in COMMON_MATERIALS.items():
                    if str((_info or {}).get("name") or "").strip() == nm:
                        return _canon
            except Exception:
                pass
        # ③ 最后才用正则从 name 里抠牌号（如 "Q235 碳素结构钢 (GB/T 700)"）
        if nm:
            import re as _re
            m = _re.search(
                r"(6061(?:-T\d)?|7075|5052|1060|2024|2A12|LY12|304|316|316L|"
                r"06Cr19Ni10|022Cr17Ni12Mo2|Q235|Q355|Q345|40Cr|20CrMnTi|"
                r"GCr15|Cr12MoV|65Mn|HT200|HT250|QT500-7|ZCuSn10P1|H62|"
                r"ZL104|Ti-6Al-4V)", nm)
            if m:
                _g = _as_legal_grade(m.group(1))
                if _g:
                    return _g
    except Exception:
        pass
    return None


def default_part_material():
    """【BUG-05 二次修复】解析本次建模应使用的默认材料。

    优先级：
      ① 环境变量 DSH_PART_MATERIAL（用户/编排显式指定，最高优先）
      ② 门禁落盘工况里的设计材料（gate_load_case.json）
      ③ 内置兜底 DEFAULT_PART_MATERIAL（Q235 钢）
    """
    try:
        env = os.environ.get("DSH_PART_MATERIAL")
        if env and str(env).strip():
            return str(env).strip()
    except Exception:
        pass
    lc = _material_from_load_case()
    if lc:
        return lc
    # 【GB 外部数据源配套】默认值必须【当前真的可用】：
    #   未导入国标数据时 Q235 不在库中，硬用会导致赋材质失败。
    # 【F-05 修复】default_part_material_name() 现在【只会】返回合法牌号或 None，
    #   不再降级成 PlainCarbonSteel 之类的欧美牌号 —— 见该函数 docstring。
    #   若它返回 None，这里【必须】返回 None 而不是硬塞一个名字：
    #   调用方（new_part）据此显式报错并给出可执行路径，
    #   绝不让一个"防线不认识的材料名"流到下游去炸。
    return default_part_material_name()


def resolve_default_material_or_raise(material=None, density=None):
    """【F-05 新增】确定本次建模要用的默认材料（并给出可用性诊断）。

    这是 new_part 与 default_part_material 之间的【唯一裁定点】：
      · 返回 {"ok": True, "material": <合法牌号>, "density": <kg/m3>, "source": ...}
      · 无法给出【合法】默认材料时，返回 ok=False + error（含可执行路径），
        **绝不**返回一个"看起来能用、实际防线不认"的名字。

    优先级（与既有文档一致）：
      显式 material 参数 > DSH_PART_MATERIAL 环境变量
      > gate_load_case.json 材料 > DEFAULT_PART_MATERIAL(Q235)

    Args:
        material: 调用方显式指定的材料名（可为 None）
        density:  调用方显式指定的密度（可为 None）
    Returns:
        dict {ok, material, density, source, error?, hint?}
    """
    _d = None
    try:
        _d = float(density) if density is not None else None
    except Exception:
        _d = None
    out = {"ok": False, "material": None, "density": _d, "source": None}
    # ① 显式参数（最高优先，且同样要求合法 —— 非法名照样显式报错）
    _nm = None
    _src = None
    if material and str(material).strip():
        _nm = str(material).strip()
        _src = "explicit(new_part material=)"
    else:
        try:
            _env = (os.environ.get("DSH_PART_MATERIAL") or "").strip()
        except Exception:
            _env = ""
        if _env:
            _nm, _src = _env, "env(DSH_PART_MATERIAL)"
        else:
            _lc = _material_from_load_case()
            if _lc:
                _nm, _src = _lc, "load_case(gate_load_case.json)"
            else:
                _nm, _src = default_part_material_name(), "default(DEFAULT_PART_MATERIAL)"
    if not _nm:
        # 走不到"名称"这一步 —— 默认材料根本不可用
        out["error"] = default_material_unavailable_error()
        out["hint"] = ("调用方无法拿到合法默认材料。最省事的修法："
                       "new_part(material='Q235', density=7850) 显式指定，"
                       "或先导入 gb_materials.json。")
        out["source"] = _src
        return out
    # ② 合法性校验：不合法就【显式失败】，绝不静默换成别的材料
    _ok, _info, _why = is_valid_material(_nm)
    if not _ok:
        out["material"] = _nm
        out["source"] = _src
        out["error"] = ("材料 %r 不是合法牌号（%s）。"
                        "本仓库不允许用非法材料名建模 —— 否则 .sldprt 里"
                        "没有可信的材质身份，材料防线会判 material_invalid。\n%s"
                        % (_nm, _why,
                           _gb_material_advice(_nm)
                           if _material_standard_of(_nm) == "GB"
                           else ("可执行路径：① 用内置库中的合法牌号"
                                 "（见 swapi.list_materials()）；"
                                 "② 或 swapi.save_custom_material(%r, ...) 显式登记。"
                                 % (_nm,))))
        out["hint"] = ("显式指定一个防线认识的牌号，例如 "
                       "new_part(material='Q235', density=7850)。"
                       "自定义材料请先用 save_custom_material() 登记。")
        return out
    # ③ 密度【随牌号表带出】—— 这是"读回 1000（水）"的直接解法
    #    显式 density 优先；否则取牌号表里的密度（Q235 → 7850）。
    #    以前 new_part 不传 density，set_material 只能靠 SW 库近似值，
    #    库里没这个牌号时读回就是 1000。
    if out["density"] is None:
        try:
            out["density"] = float((_info or {}).get("density")
                                   or (_info or {}).get("density_kg_m3") or 0.0) or None
        except Exception:
            out["density"] = None
    out["material"] = _nm
    out["source"] = _src
    out["canonical"] = (_info or {}).get("canonical") or _nm
    out["ok"] = True
    return out

# ── 【BUG-08 修复】SW 导出 DXF 的标准图层名（与 CADX mechanical 规则一致）──
# CADX ac_validate 的 mechanical 规则要求图层:
#   OUTLINE / THIN / CENTER / HIDDEN / DIM / TEXT / HATCH
# 而 SW 中文界面默认导出中文图层名（如"可见边线""尺寸"）→ 每次出图都刷
# 一堆 MISSING_LAYER / LAYER_NAME_INVALID WARNING。
# 本表给出"SW 图层名（含中英文） → CADX 标准层"的映射，
# 供 dxf 导出后自动整理图层（见 sw_bridge.py 的 _remap_dxf_layers）。
CADX_LAYER_STANDARD = ("OUTLINE", "THIN", "CENTER", "HIDDEN", "DIM", "TEXT", "HATCH")

# SW 默认/中文图层名 → CADX 标准图层
SW_TO_CADX_LAYER = {
    # 可见轮廓 / 粗实线
    "可见边线": "OUTLINE", "可见轮廓线": "OUTLINE", "轮廓线": "OUTLINE",
    "粗实线": "OUTLINE", "外形线": "OUTLINE",
    # ── 实机补录（2026-09-27 真实出图实测发现的层名）──────────────────
    # SW 中文版工程图导出时的实际层名，此前未收录 → 残留 INFO 告警：
    #   "轮廓实线层"（外轮廓）、"细线层"（细实线）
    "轮廓实线层": "OUTLINE", "轮廓实线": "OUTLINE", "实线层": "OUTLINE",
    "可见轮廓": "OUTLINE",
    "visible": "OUTLINE", "visible edges": "OUTLINE", "outline": "OUTLINE",
    "object line": "OUTLINE", "continuous": "OUTLINE",
    # 细实线 / 过渡线 / 波浪线 / 断裂线
    "细实线": "THIN", "过渡线": "THIN", "波浪线": "THIN", "断裂线": "THIN",
    "细线层": "THIN", "细线": "THIN", "细实线层": "THIN",
    "thin": "THIN", "thin line": "THIN", "phantom": "THIN",
    # 点划线 / 中心线
    "点划线": "CENTER", "中心线": "CENTER", "轴线": "CENTER",
    "中心线层": "CENTER", "点划线层": "CENTER", "双点划线层": "CENTER",
    "center": "CENTER", "centerline": "CENTER", "centre line": "CENTER",
    "center line": "CENTER",
    # 虚线 / 不可见边线
    "虚线": "HIDDEN", "不可见边线": "HIDDEN", "隐藏线": "HIDDEN",
    "虚线层": "HIDDEN", "隐藏线层": "HIDDEN",
    "hidden": "HIDDEN", "hidden edges": "HIDDEN", "dashed": "HIDDEN",
    # 尺寸标注
    "尺寸": "DIM", "尺寸线": "DIM", "标注": "DIM", "尺寸标注": "DIM",
    "标注层": "DIM", "尺寸层": "DIM", "符号标注层": "DIM",
    "dim": "DIM", "dimension": "DIM", "dimensions": "DIM",
    # 文字 / 注释
    "文字": "TEXT", "注释": "TEXT", "技术要求": "TEXT", "文本": "TEXT",
    "标题栏": "TEXT", "文字层": "TEXT", "注释层": "TEXT",
    "text": "TEXT", "note": "TEXT", "notes": "TEXT",
    "annotation": "TEXT",
    # 剖面线 / 填充
    "剖面线": "HATCH", "填充": "HATCH", "图案填充": "HATCH",
    "剖面线层": "HATCH", "填充层": "HATCH", "剖面层": "HATCH",
    "hatch": "HATCH", "section": "HATCH", "section line": "HATCH",
}

# ── 【BUG-06 修复】无需整理、也不应报警的"非图形"图层 ────────────────────
# AutoCAD/SW 固有层：不是视图线型层，强行改名反而破坏兼容性
#   "0"         AutoCAD 默认层
#   "Defpoints" AutoCAD 定义点层（不可打印，标准存在）
#   "SLD-0"     SolidWorks 内部默认层
#   "9"/"10"/"5" 等纯数字层：SW 工程图内部用于视图/标注承载的保留层
# 这些层不含"需要按 GB/T 线型归类"的几何，故 CADX 的命名规范告警对它们
# 属于噪声。此处显式豁免（记录为 ignored，不再计入 unknown）。
_CADX_LAYER_EXEMPT = {"0", "defpoints", "sld-0"}


def _is_exempt_layer(name):
    """判断图层是否为 AutoCAD/SW 固有保留层（无需改名、不报警）。"""
    if not name:
        return True
    k = str(name).strip().lower()
    if k in _CADX_LAYER_EXEMPT:
        return True
    # 纯数字层（SW 工程图内部保留层）
    if k.isdigit():
        return True
    return False


def cadx_layer_for(sw_layer_name):
    """把 SW（可能是中文的）图层名映射到 CADX 标准图层名。

    未识别时返回 None（调用方据此决定是否原样保留 + 记 warning）。
    """
    if not sw_layer_name:
        return None
    key = str(sw_layer_name).strip()
    kl = key.lower()
    if key.upper() in CADX_LAYER_STANDARD:
        return key.upper()
    if kl in SW_TO_CADX_LAYER:
        return SW_TO_CADX_LAYER[kl]
    # 模糊匹配：包含关系（如 "可见边线(ISO)" → OUTLINE）
    # 【实机补强】先做"最长关键词优先"，避免 "细线层" 被 "线层" 之类的
    #   短词先命中而误判；同长度时按更具体的语义优先。
    hits = [(k, v) for k, v in SW_TO_CADX_LAYER.items() if k in kl]
    if hits:
        hits.sort(key=lambda kv: -len(kv[0]))
        return hits[0][1]
    return None


def is_exempt_cadx_layer(name):
    """是否为 AutoCAD/SW 固有保留层（无需改名，也不应计入 unknown）。"""
    return _is_exempt_layer(name)


# ── 【Bug3】材质库/材料名解析辅助 ──────────────────────────────────────────

# 材质库"显示名"映射：路径 basename（小写）→ SW 界面显示名
# 实测：SetMaterialProperty 的 Database 参数必须用【显示名】而非路径，
#   传 "SolidWorks Materials" 成功，传完整路径失败（错误码 1/6）。
_SW_DB_DISPLAY = {
    "solidworks materials": "SolidWorks Materials",
    "solidworks din materials": "SolidWorks DIN Materials",
    "sustainability extras": "Sustainability Extras",
    "自定义材料": "自定义材料",
    "custom materials": "Custom Materials",
}


def _sw_db_display_name(base):
    """把材质库文件名（去扩展名）转成 SW 界面显示的库名。

    SW 的 GetMaterialDatabases 返回的是小写路径，例如
      z:\\...\\sldmaterials\\solidworks materials.sldmat
    而 SetMaterialProperty 需要的是界面显示名 "SolidWorks Materials"
    （首字母大写）。本函数做这个映射，未收录的库做智能首字母大写。
    """
    if not base:
        return base
    key = str(base).strip().lower()
    if key in _SW_DB_DISPLAY:
        return _SW_DB_DISPLAY[key]
    # 智能大写：每个单词首字母大写
    return " ".join(w[:1].upper() + w[1:] for w in str(base).split())


_MATERIAL_LIST_CACHE = {}


def list_materials(database=None, force=False):
    """【Bug3】列出材质库中的全部材料名（供调用方选名，避免拼错）。

    Args:
        database: 库名（如 "SolidWorks Materials"）或 .sldmat 路径；
                  None 则用自动探测到的主库。
    Returns:
        list[str] 材料名列表（失败返回 []）
    """
    path = None
    if database:
        d = str(database)
        if os.path.isfile(d):
            path = d
        else:
            # 按库名反查路径
            try:
                exe = find_sldworks_exe()
                if exe:
                    lang_root = os.path.join(os.path.dirname(exe), "lang")
                    for lang in ("chinese-simplified", "english"):
                        cand = os.path.join(lang_root, lang, "sldmaterials",
                                            d.strip().lower() + ".sldmat")
                        if os.path.isfile(cand):
                            path = cand
                            break
            except Exception:
                pass
            if not path:
                dbdir = find_material_db()
                if dbdir:
                    try:
                        for fn in os.listdir(dbdir):
                            if fn.lower().startswith(d.strip().lower()):
                                path = os.path.join(dbdir, fn)
                                break
                    except Exception:
                        pass
    else:
        dbdir = find_material_db()
        if dbdir:
            try:
                for fn in os.listdir(dbdir):
                    if fn.lower() == "solidworks materials.sldmat":
                        path = os.path.join(dbdir, fn)
                        break
            except Exception:
                pass
    if not path or not os.path.isfile(path):
        return []
    ck = path.lower()
    if ck in _MATERIAL_LIST_CACHE and not force:
        return _MATERIAL_LIST_CACHE[ck]
    out = []
    try:
        import re as _re
        txt = open(path, "r", encoding="utf-16", errors="replace").read()
        out = _re.findall(r'<material name="([^"]+)"', txt)
    except Exception:
        try:
            txt = open(path, "r", encoding="utf-8", errors="replace").read()
            out = _re.findall(r'<material name="([^"]+)"', txt)
        except Exception:
            out = []
    _MATERIAL_LIST_CACHE[ck] = out
    return out


def _resolve_material_names(database_name, wanted, with_level=False):
    """把用户给的材料名解析成材质库中【真实存在】的名字列表。

    用于容忍：空格差异、全半角、大小写、以及用户用常用牌号
    （如 "40Cr" / "Q235" / "6061"）而库里只有工程名的情况。

    【重要】返回结果区分匹配级别，因为"用错材料"比"报错"更危险 ——
      一个 6061 铝件若被静默设成钢，密度差 3 倍，强度校核直接失效。
      调用方必须能知道"是不是精确匹配"，并据此决定是否提示用户。

    匹配级别（level）：
      "exact"  —— 与库中名字完全一致（最可信）
      "loose"  —— 子串互相包含（如 "6061" ⊂ "6061 合金"）
      "keyword"—— 牌号关键词命中（如 "40Cr" 命中 "AISI 5140 钢(40Cr)"）
      "none"   —— 库中找不到，原样返回（由 SW 决定成败）

    Args:
        with_level: True 时返回 [(name, level), ...]，否则只返回 [name, ...]
    """
    try:
        lib = list_materials(database_name)
    except Exception:
        lib = []
    w = str(wanted).strip()
    if not lib:
        return [(w, "none")] if with_level else [w]
    wl = w.lower()
    exact, loose, kw = [], [], []
    for n in lib:
        nl = n.strip().lower()
        if n.strip() == w:
            exact.append(n)
        elif wl and (wl in nl or nl in wl):
            loose.append(n)
        else:
            # 牌号关键词匹配：如 40Cr→含 "40Cr"；6061→含 "6061"
            for tok in (w, w.rstrip("钢"), w.replace("钢", "")):
                if tok and len(tok) >= 3 and tok.lower() in nl:
                    kw.append(n)
                    break
    # 组装：原样优先，然后 loose，再 keyword，最后 exact（exact 放前面更合理）
    seq = []
    for n in exact:
        seq.append((n, "exact"))
    for n in loose:
        seq.append((n, "loose"))
    for n in kw:
        seq.append((n, "keyword"))
    if not seq:
        seq.append((w, "none"))
    # 去重（按名字），保持顺序，并把用户原样名放最前（若它确实在库里）
    seen, out = set(), []
    for n, lv in seq:
        if n in seen:
            continue
        seen.add(n)
        out.append((n, lv))
    out = out[:12]
    return out if with_level else [n for n, _ in out]


# ── 【BUG-05 二次修复】材料家族判定 ────────────────────────────────────────
# 用途：禁止"因名字匹配上就把铝换成钢"这类跨族静默替换。
#   密度量级是最可靠的家族指纹（铝≈2700、钢≈7850、钛≈4500、塑料≈1000-1400）。
#   实机证据：AISI 1020 库内密度 0.79E+04=7900，而 6061-T6 应为 2700。
_FAMILY_BY_DENSITY = (
    # (下限, 上限, 家族名)
    (2000.0, 3000.0, "aluminum"),      # 铝及铝合金
    (4300.0, 4700.0, "titanium"),      # 钛合金
    (7000.0, 8100.0, "steel"),         # 碳钢/合金钢/不锈钢/铸铁
    (700.0, 1500.0, "plastic"),        # 工程塑料
    (8300.0, 9000.0, "copper"),        # 铜合金（青铜/黄铜）
)

# 名字关键词 → 家族（优先于密度，用于名字明确指向某族的场合）
_FAMILY_BY_KEYWORD = (
    ("aluminum", "aluminum"), ("aluminium", "aluminum"), ("铝", "aluminum"),
    ("6061", "aluminum"), ("7075", "aluminum"), ("5052", "aluminum"), ("1060", "aluminum"),
    ("titanium", "titanium"), ("ti-6al", "titanium"), ("钛", "titanium"),
    ("steel", "steel"), ("钢", "steel"), ("iron", "steel"), ("铸铁", "steel"),
    ("stainless", "steel"), ("不锈", "steel"),
    ("q235", "steel"), ("45#", "steel"), ("40cr", "steel"), ("cr12mov", "steel"),
    ("1020", "steel"), ("1045", "steel"), ("4140", "steel"), ("4340", "steel"),
    ("abs", "plastic"), ("nylon", "plastic"), ("pom", "plastic"),
    ("polycarbonate", "plastic"), ("塑料", "plastic"),
    ("copper", "copper"), ("brass", "copper"), ("bronze", "copper"), ("铜", "copper"),
)


def _material_family(name_or_density):
    """判定材料家族：'aluminum' / 'steel' / 'titanium' / 'plastic' / 'copper' / None。

    接受材料名（str）或密度（number）。名字优先（更明确），
    否则按密度量级归类。无法判定返回 None（调用方应视为"未知，不阻断"）。
    """
    if name_or_density is None:
        return None
    # 数值 → 按密度区间
    if isinstance(name_or_density, (int, float)):
        d = float(name_or_density)
        for lo, hi, fam in _FAMILY_BY_DENSITY:
            if lo <= d <= hi:
                return fam
        return None
    s = str(name_or_density).strip().lower()
    if not s:
        return None
    for kw, fam in _FAMILY_BY_KEYWORD:
        if kw in s:
            return fam
    return None


def material_family(name):
    """对外暴露的材料家族查询（先按内置表密度，再按名字关键词）。"""
    info = lookup_material(name)
    if info and info.get("density"):
        fam = _material_family(info["density"])
        if fam:
            return fam
    return _material_family(name)


def lookup_material(name):
    """按名字/别名查材料表（【Bug-13】现在也覆盖 PETG/PLA/Nylon/TPU 等打印材料）。

    返回 dict：canonical / density / e_mpa / yield_mpa / uts_mpa（打印材料有）
    或 None（未收录）。
    """
    if not name:
        return None
    key = str(name).strip()
    kl = key.lower()
    for canon, info in COMMON_MATERIALS.items():
        if canon.lower() == kl:
            return {"canonical": canon, **info}
        for a in info.get("aliases", []):
            if a.lower() == kl:
                return {"canonical": canon, **info}
    for canon, info in COMMON_MATERIALS.items():
        for a in info.get("aliases", []):
            if a.lower() in kl or kl in a.lower():
                return {"canonical": canon, **info}
    return None


# ══ 【Bug-29 修复】自定义材质直写（PETG/PLA/Nylon 在 SW 官方库中无牌号）══════
# 问题：SW 2025 中文材质库没有 PETG/Nylon，小屋只能用 ABS 近似 + 手改密度，
#   导致 ① 材料属性不一致；② 工程图材料栏写错；③ physics 校核按 ABS 算。
# 方案：提供 set_custom_material(name, e_mpa, yield_mpa, density_kg_m3)，
#   直接对零件写入"自定义材料"名称与密度（E/屈服写进 result 供 physics 用），
#   不再依赖 SW 库是否存在该牌号。
_CUSTOM_MATERIAL_JSON = "custom_materials.json"


def _custom_material_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        _CUSTOM_MATERIAL_JSON)


def load_custom_materials():
    """读取用户自定义材料库（不存在返回 {}）。

    ── 【观察点 12 修复 · 阻断级】必须用 `utf-8-sig` 兼容 BOM ──────────────
    原实现用 `encoding="utf-8"` 读取。若该文件被 PowerShell
    （`[System.IO.File]::WriteAllText(..., UTF8Encoding)`）或某些编辑器
    写成 **UTF-8 with BOM**，`json.load` 会抛
    `JSONDecodeError: Unexpected UTF-8 BOM`，而原来的 `except: pass`
    把它【静默吞掉】→ 返回 `{}` → `is_valid_material()` 对所有自定义材料
    一律返回 False → 所有零件材料凭据判 `material_invalid` → 物理防线 FAIL。

    这是**阻断级且极其隐蔽**的缺陷：文件内容看起来完全正常，只是开头多了
    3 个字节（EF BB BF）。`utf-8-sig` 解码时自动剥离 BOM，对无 BOM 文件
    行为与 `utf-8` 完全一致，因此是零风险替换。

    同时不再静默吞错：解析失败会打 stderr 提示（见下），避免"文件坏了却
    毫无线索"。
    """
    p = _custom_material_path()
    if not os.path.isfile(p):
        return {}
    # ① 首选 utf-8-sig（兼容 BOM 与无 BOM）
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
        _warn_stderr("custom_materials.json 顶层不是对象，已忽略: %s" % p)
        return {}
    except Exception as e:
        # ② 回退：极少数情况下文件可能是 UTF-16（记事本"另存为 Unicode"）
        try:
            with open(p, "r", encoding="utf-16") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _warn_stderr("custom_materials.json 以 UTF-16 读取成功: %s" % p)
                return d
        except Exception:
            pass
        # ③ 两种编码都失败 → 必须让调用方知道（原实现静默返回 {}）
        _warn_stderr("custom_materials.json 解析失败（已按空库处理）: %s :: %r"
                     % (p, e))
        return {}


def _warn_stderr(msg):
    """把非致命告警打到 stderr（不抛异常、不影响主流程）。"""
    try:
        sys.stderr.write("[swapi][WARN] " + str(msg) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def save_custom_material(name, e_mpa=None, yield_mpa=None, uts_mpa=None,
                         density_kg_m3=None, poissons_ratio=None, note=None):
    """把自定义材料登记到 custom_materials.json（幂等，同名覆盖）。

    【Bug-29】让"PETG / Nylon PA12"等 SW 库里没有的打印材料有一个
    可复用、可审计的落点，而不是每次手工近似。
    """
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "error": "材料名不能为空"}
    db = load_custom_materials()
    rec = dict(db.get(nm) or {})
    for k, v in (("e_mpa", e_mpa), ("yield_mpa", yield_mpa),
                 ("uts_mpa", uts_mpa), ("density_kg_m3", density_kg_m3),
                 ("poissons_ratio", poissons_ratio)):
        if v is not None:
            try:
                rec[k] = float(v)
            except Exception:
                pass
    if note:
        rec["note"] = str(note)
    rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    db[nm] = rec
    try:
        with open(_custom_material_path(), "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": _custom_material_path(), "material": rec}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ══ 【Bug① 修复·落盘层】SW 内部材质事实的【旁挂记录】══════════════════
# 为什么需要单独一份文件：
#   材料凭据（<part>.material.json）由【宿主】签发，宿主的 /defense/sign 只会
#   把【白名单里】的字段纳入待签体（见 plugins/dsh-engineering-ui/lib/index.js
#   的 material 分支）。请求体里多带的字段会被静默丢弃 —— 实测：
#     spec 里带 sw_internal_material="PET" → 签回来的凭据里【没有】该键。
#   而凭据是 HMAC 签过的：签完再往里补字段必然导致验签失败（防线① 全灭），
#   这是绝对不能做的。
#   ⇒ 因此"SW 内部实际材质"不能塞进已签名凭据，改为写在【零件旁的旁挂记录】
#     <part>.material.json.sw_internal.json（不参与验签，纯事实留痕），
#     并与 return 体、part_registry 台账联动，保证可见、可审计。
SW_INTERNAL_SUFFIX = ".sw_internal.json"


def sw_internal_record_path(part_path):
    """旁挂记录的路径：<零件>.SLDPRT → <零件>.SLDPRT.material.json.sw_internal.json。"""
    if not part_path:
        return None
    return str(part_path) + ".material.json" + SW_INTERNAL_SUFFIX


def read_sw_internal_record(part_path):
    """读取旁挂的"SW 内部材质"事实；不存在/损坏返回 None（绝不抛异常）。

    ★ 纯读函数、无副作用 —— defense_gate 会在【每次校验】时调用它（A-17）。
    """
    if not part_path:
        return None
    _s = str(part_path)
    _cands = ([_s] if _s.endswith(SW_INTERNAL_SUFFIX)
              else [_s + ".material.json" + SW_INTERNAL_SUFFIX,
                    sw_internal_record_path(_s)])
    for p in _cands:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def _write_sw_internal_record(part_path, info):
    """把"请求材质 vs SW 内部实际材质"写成旁挂记录（失败只 warn，不阻断）。"""
    try:
        p = sw_internal_record_path(part_path)
        if not p:
            return {"ok": False, "error": "part_path 为空"}
        rec = {
            "schema": "dsh-engineering/sw-internal-material@1",
            "part": os.path.abspath(str(part_path)),
            "part_name": os.path.splitext(os.path.basename(str(part_path)))[0],
            "note": ("本文件记录 SolidWorks 零件内部【实际生效】的材质，"
                     "用于与 .material.json 里 requested_material 对照。"
                     "它不参与验签，也不能替代材料凭据。"),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
        }
        for k, v in (info or {}).items():
            rec[k] = v
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": p, "record": rec}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _pick_attested_density(material_result, req_name):
    """选出凭据里该记录的【密度】。

    ── 【P0 修复·凭据伪造密度】绝不用期望值掩盖"赋材没生效" ────────────────
    实测缺陷（子代理在真实流程中复现，18 个牌号里 8 个中招）：
      `set_material` 失败时，零件密度【停在 1000（水）】，但旧实现会在
      写入值=1000 时"回退到内置期望密度"，于是凭据写成 ok=true + 期望密度
      （如 65Mn 写 7850），材料防线①据此判定"密度吻合"→ **PASS**。
      后果链：
        real_geometry.density_kg_m3 = 1000（零件真实，仍是水）
        fea_result.mass_kg          = 1.5993 kg（按伪造的 7850 算）
        报告 warnings               = None（"密度=1000 等同水"告警未触发）
      → 6 个"根本没赋上材"的零件全部通过防线，质量结论也是错的。

    正确做法：密度是【客观事实】，必须如实记录零件实测值。
      期望密度另存为 expected_density_kg_m3 供防线比对，
      绝不冒充实测密度。
    """
    _mr = material_result if isinstance(material_result, dict) else {}
    # ── 【P0 修复·取不到实测值】失败时只有 density_before_kg_m3 ─────────────
    # 实测（子代理受控实验）：set_material 失败时 _mr 里【没有】
    #   density_kg_m3 / density_after_kg_m3，只有 density_before_kg_m3=1000。
    #   旧实现只读前两者 → 得到 None → 凭据写 null，既没做到"如实记录 1000"，
    #   也让防线只能以"凭据里没有密度"为由拦截（理由不准确）。
    # 现在按优先级取：写入后密度 → 写入前密度 → 通用 density 键。
    #   注意 density_before 是【写入前】的值：赋材失败时它就是零件当前真实
    #   密度（通常 1000=水），如实记录它才是正确做法。
    _d = None
    for _k in ("density_kg_m3", "density_after_kg_m3",
               "density_before_kg_m3", "density"):
        _v = _mr.get(_k)
        if _v is None:
            continue
        try:
            _d = float(_v)
            break
        except Exception:
            continue
    _d_source = None
    for _k in ("density_kg_m3", "density_after_kg_m3",
               "density_before_kg_m3", "density"):
        if _mr.get(_k) is not None:
            _d_source = _k
            break
    # 期望密度（来自材料库）单独保留，不覆盖实测值
    _expected = None
    try:
        _ok, _info, _why = is_valid_material(req_name)
        if _ok and _info and _info.get("density"):
            _expected = float(_info["density"])
    except Exception:
        _expected = None
    # 1000 视为"未生效默认值"（与 _material_guard 的判据一致）→ 如实保留
    _suspect = (_d is None) or (abs(_d - 1000.0) < 1.0)
    if _suspect:
        return _d, _expected, True      # (实测密度, 期望密度, 是否疑似未生效)
    return _d, _expected, False


def is_valid_material(name, allow_custom=True):
    """材料名是否【合法】（Bug16 修复：非法材料不得走验签流程）。

    ── 为什么需要严格判定 ──────────────────────────────────────────────
    测试部实测：new_part(material="INVALID_MAT") 只报 WARN，却照常保存
      并写出带 _sig/_kid 的 .material.json —— 任何乱写的材料名都能通过
      验签，材料防线①形同虚设。
    根因：lookup_material() 带【子串模糊匹配】（`a in kl or kl in a`），
      于是任意字符串都可能"匹配"到某个材料；且 save() 写凭据前
      从不校验材料是否合法。

    本函数只做【精确】判定（canonical 或 alias 全等，忽略大小写），
      并额外接受用户显式登记的自定义材料。

    Args:
        name: 材料名。
        allow_custom: 是否接受 custom_materials.json 中登记的材料。
    Returns:
        (ok: bool, info: dict|None, reason: str)
    """
    _nm = str(name or "").strip()
    if not _nm:
        return False, None, "材料名为空"
    _kl = _nm.lower()
    # ── 【P2 修复】归一化键（剥 BOM / 全角转半角）────────────────────────
    # 实测：\\ufeffQ235 与 Ｑ２３５ 会因字符形态差异被判"不在库中"。
    _nk = _norm_name_key(_nm)
    # ① 内置材料库：canonical 或 alias 全等（含归一化比较）
    # ── 【P1-C 修复·桩条目遮蔽】同名多条目时必须选【数值最全】的那个 ──────
    # 实测缺陷：COMMON_MATERIALS 里同时存在
    #     "7075"    -> {"density": 2810, "aliases": [..., "7075-T6"]}  ← 只有密度
    #     "7075-T6" -> {"density":..., "e_mpa":71700, "yield_mpa":503} ← 数值齐全
    #   旧实现【按字典顺序返回首个命中】，于是查 "7075-T6" 时先命中 "7075"
    #   这个桩条目 → canonical="7075" 且 yield=None。
    #   后果：图纸写 7075-T6 时拿不到强度（静默为 None），必须写 GB_7075_T6
    #   才行；同一牌号在 material_db 与 swapi 两处结论不一致。
    # 现在：收集所有命中项，按"关键力学字段齐全度"排序后返回最优者。
    _cands = []
    for _canon, _info in COMMON_MATERIALS.items():
        _score = -1
        if _canon.lower() == _kl or _norm_name_key(_canon) == _nk:
            _score = 0
        else:
            for _a in (_info.get("aliases") or []):
                if str(_a).lower() == _kl or _norm_name_key(_a) == _nk:
                    _score = 1
                    break
        if _score < 0:
            continue
        # 数值齐全度：有 e_mpa / yield_mpa / density 各计 1 分
        _rich = sum(1 for _f in ("e_mpa", "yield_mpa", "density")
                    if _info.get(_f) is not None)
        # 排序优先级：canonical 精确命中 > alias 命中；同档再看数值齐全度
        _cands.append((0 if _score == 0 else 1, -_rich, _canon, _info))
    if _cands:
        _cands.sort(key=lambda x: (x[0], x[1], x[2]))
        _, _negrich, _canon, _info = _cands[0]
        return True, {"canonical": _canon, **_info}, "builtin(canonical)"
    # ② 用户自定义材料（显式登记才算合法）
    if allow_custom:
        try:
            _cm = load_custom_materials() or {}
            for _k, _v in _cm.items():
                if str(_k).lower() == _kl or _norm_name_key(_k) == _nk:
                    return True, {"canonical": _k, "custom": True, **(_v or {})}, \
                           "custom_materials.json"
        except Exception:
            pass
    # ③ GB 牌号别名索引（含 45#/Q235/LY12 等图纸常用写法）
    try:
        _gb = lookup_gb(_nm)
        if _gb:
            return True, {"canonical": _gb.get("id"), **_gb}, "gb_alias_index"
    except Exception:
        pass
    return False, None, ("材料 %r 不在合法材料库中（内置库 + custom_materials.json）"
                         % _nm)


def lookup_material_exact(name):
    """【Bug-29】只做【精确】匹配（canonical 或 alias 完全相等），不做子串模糊。

    为什么需要：lookup_material 的第三段是双向子串匹配，
    "SomeRandomPlastic" 会命中 "PLA"（'pla' ⊂ 'plastic'），
    "PETG-CF" 也会先命中 "PETG"。在"解析材料属性"这种要求准确性的场景下，
    子串匹配会把完全无关的名字解析成错误的材料。精确版用于属性解析。
    """
    if not name:
        return None
    kl = str(name).strip().lower()
    if not kl:
        return None
    for canon, info in COMMON_MATERIALS.items():
        if canon.lower() == kl:
            return {"canonical": canon, **info}
        for a in info.get("aliases", []):
            if a.lower() == kl:
                return {"canonical": canon, **info}
    return None


def resolve_material_props(name):
    """统一解析材料属性：内置表(精确) → 自定义库 → 内置表(模糊)。

    【Bug-29】给 set_custom_material / physics 校核提供"同一份属性"，
    避免"SW 里是 ABS、physics 里是 Nylon"的分裂。

    优先级说明：
      ① 内置表【精确】匹配（PETG / 尼龙 / 6061-T6 …）；
      ② 自定义库【精确】匹配（用户在 custom_materials.json 登记的材料，
         例如 "PETG-CF" —— 必须优先于内置表的模糊命中）；
      ③ 内置表【模糊】匹配（兜底，兼容 "6061" ⊂ "Aluminum 6061-T6" 这类写法）。

    Returns: {"name","density_kg_m3","e_mpa","yield_mpa","uts_mpa","source"} 或 None
    """
    nm = str(name or "").strip()
    if not nm:
        return None
    # ① 内置精确
    hit = lookup_material_exact(nm)
    if hit and hit.get("density"):
        return {"name": hit.get("canonical") or nm,
                "density_kg_m3": hit.get("density"),
                "e_mpa": hit.get("e_mpa"), "yield_mpa": hit.get("yield_mpa"),
                "uts_mpa": hit.get("uts_mpa"), "source": "COMMON_MATERIALS"}
    # ② 自定义库精确（优先于模糊）
    db = load_custom_materials()
    for k, v in db.items():
        if k.lower() == nm.lower():
            return {"name": k, "density_kg_m3": v.get("density_kg_m3"),
                    "e_mpa": v.get("e_mpa"), "yield_mpa": v.get("yield_mpa"),
                    "uts_mpa": v.get("uts_mpa"), "source": "custom_materials.json"}
    # ③ 内置模糊兜底
    hit = lookup_material(nm)
    if hit and hit.get("density"):
        return {"name": hit.get("canonical") or nm,
                "density_kg_m3": hit.get("density"),
                "e_mpa": hit.get("e_mpa"), "yield_mpa": hit.get("yield_mpa"),
                "uts_mpa": hit.get("uts_mpa"),
                "source": "COMMON_MATERIALS(loose)"}
    return None


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

    # 4) 安装目录下的模板（【全盘符 + Program Files】—— 见 _drive_roots）
    #    ── 【连接区修复】原 glob 写成 `drive + r"*SOLIDWORKS*"`，
    #       实际展开为 `C:\*SOLIDWORKS*` —— 只匹配【盘符根目录】下的文件夹，
    #       而 SW 的真实安装是 `Z:\Program Files\SOLIDWORKS Corp2025\...`，
    #       藏在 Program Files 里 → 这条扫描【从来没命中过】用户的真实安装，
    #       一直靠 ProgramData 兜着。一旦 ProgramData 模板缺失
    #       （换电脑/换盘/重装），就会误报"未找到零件模板"。
    #       现改为按 _sw_template_dirs() 统一枚举（含 Program Files 各变体）。
    for _d in _sw_template_dirs():
        cands.append(_d)

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


def _sw_template_dirs():
    """枚举【所有盘符】下可能的 SOLIDWORKS 模板目录（单一事实来源）。

    ── 【连接区修复】为什么单独抽出来 ──────────────────────────────────────
    用户诉求："以后这个 SW 关了也好，我重新下到其他的 C，D，G，Q 任意一个
      盘也好，点那个连接就可以直接找到地方的。"

    原实现有两个叠加缺陷：
      ① 盘符硬编码成 ("C:","D:","E:","F:") → 换到 G:/Q: 盘直接找不到；
      ② glob 写成 `drive + r"*SOLIDWORKS*"`（展开为 `C:\\*SOLIDWORKS*`），
         只扫【盘符根目录】，而真实安装位于
         `Z:\\Program Files\\SOLIDWORKS Corp2025\\SOLIDWORKS\\...`，
         藏在 Program Files 里 → 【从来没命中过】真实安装。

    本函数按"盘符 × 常见安装前缀 × SOLIDWORKS 变体"三层展开并去重，
    供零件模板 / 装配体模板 / 材质库等共用。

    Returns: 去重后的目录候选列表（不保证存在，由调用方判断）。
    """
    out = []
    seen = set()

    def _add(p):
        try:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        except Exception:
            pass

    # 安装前缀：Program Files 各变体 + 盘符根（兼容绿色版/自定义安装）
    prefixes = []
    for _root in _drive_roots():
        prefixes.append(os.path.join(_root, "Program Files"))
        prefixes.append(os.path.join(_root, "Program Files (x86)"))
        prefixes.append(_root)          # 兜底：有的装法直接落在盘符根

    # SOLIDWORKS 目录名变体（不同年份/语言的安装命名）
    sw_names = [
        "SOLIDWORKS Corp*", "SolidWorks Corp*", "SOLIDWORKS*", "SolidWorks*",
        "SOLIDWORKS Corp*\\SOLIDWORKS", "SolidWorks Corp*\\SolidWorks",
    ]
    for pre in prefixes:
        for nm in sw_names:
            try:
                for d in glob.glob(os.path.join(pre, nm)):
                    _add(os.path.join(d, "templates"))
                    # 有些安装把模板放在子版本目录下
                    _add(os.path.join(d, "SOLIDWORKS", "templates"))
            except Exception:
                continue
    return out


_TEMPLATE_DIRS_CACHE = None


def _sw_template_dirs_cached():
    """_sw_template_dirs() 的缓存版（全盘扫描较慢，同一进程内复用）。"""
    global _TEMPLATE_DIRS_CACHE
    if _TEMPLATE_DIRS_CACHE is None:
        try:
            _TEMPLATE_DIRS_CACHE = _sw_template_dirs()
        except Exception:
            _TEMPLATE_DIRS_CACHE = []
    return _TEMPLATE_DIRS_CACHE


_TEMPLATE_CACHE = None

_ASM_TEMPLATE_CACHE = None


def _find_asm_template(sw=None):
    """探测装配体模板(.asmdot)。【C8 修复】原项目只有零件模板探测，
    导致 new_assembly 无模板可用。此处复用 _find_template 的候选目录策略。"""
    cands = []
    # 1) 从 SW 自身设置读默认模板（最权威）
    try:
        if sw is not None:
            p = sw.GetUserPreferenceStringValue(8)   # swDefaultTemplateAssembly
            if p and os.path.exists(p):
                return p
    except Exception:
        pass
    # 2) 用户文档目录
    try:
        home = os.path.expanduser("~")
        cands.append(os.path.join(home, "Documents", "SOLIDWORKS"))
    except Exception:
        pass
    # 3) ProgramData 标准位置
    for ver_dir in glob.glob(r"C:\ProgramData\SolidWorks\SOLIDWORKS*"):
        cands.append(os.path.join(ver_dir, "templates"))
    # 4) 安装目录下的模板（【全盘符 + Program Files】—— 见 _sw_template_dirs）
    for _d in _sw_template_dirs():
        cands.append(_d)
    for drive in _drive_roots():
        cands.append(os.path.join(drive, "ProgramData", "SolidWorks"))
    tmpl_names = ["gb_assembly.asmdot", "Assembly.asmdot", "装配体.asmdot",
                  "assembly.asmdot", "ASM.asmdot"]
    for d in cands:
        if not d or not os.path.isdir(d):
            continue
        for n in tmpl_names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
        found = glob.glob(os.path.join(d, "*.asmdot"))
        if found:
            return found[0]
    return None


def get_asm_template(sw=None):
    """获取可用装配体模板路径（缓存）。"""
    global _ASM_TEMPLATE_CACHE
    if _ASM_TEMPLATE_CACHE and os.path.exists(_ASM_TEMPLATE_CACHE):
        return _ASM_TEMPLATE_CACHE
    _ASM_TEMPLATE_CACHE = _find_asm_template(sw)
    return _ASM_TEMPLATE_CACHE


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



# ══════════════════════════════════════════════════════════════════════════
# 早期绑定（early binding）核心基础设施
# ──────────────────────────────────────────────────────────────────────────
# 为什么需要（来源：SW2025_COM_API_完整摸底报告 1.1 / 1.2 / 2.3 / 4）：
#   实机 SW 2025 SP5.0 (Rev 33.5.0) 上，win32com 的【晚期绑定】只能做少数
#   简单属性访问，大量 API 直接失败：
#       GetModelDoc2    晚期"找不到成员"     → 早期成功
#       SelectByID2     晚期"类型不匹配"     → 早期成功（Callout 必须传 None）
#       CreateMate      晚期"参数数量错误"   → 早期成功（新版仅 1 参数）
#       AddMate5        晚期 float() 报错     → 早期成功（15 参数全必需）
#       GetFeatureCount 晚期"参数数量错误"   → 早期成功（新版无参数）
#   报告 1.2 给出的正规做法：
#       mod = gencache.EnsureModule(GUID, 0, <major>, 0)
#       sw  = mod.ISldWorks(raw._oleobj_)
#       im  = mod.IModelDoc2(model._oleobj_)
#       ia  = mod.IAssemblyDoc(model._oleobj_)
#       ic  = mod.IComponent2(component._oleobj_)
#
# 本层的【硬约束】（不可违反）：
#   ① 早期绑定只是【可选增强】。任何一步失败都必须静默回退到晚期绑定，
#      不得抛异常、不得让 import 阶段崩掉 —— 仓库仍要能在 SW 2018~2024
#      以及【没装 SW 的机器】上照旧工作。
#   ② major 版本号【不写死 33】：先从 sw.RevisionNumber 推导，再退到注册表，
#      最后才依次试 33/32/31/30/29/28。
#   ③ 失败结论必须缓存（_EARLY_STATE["failed"]），避免每次调用都重试
#      拖慢启动（未注册类型库时 gencache.EnsureModule 会抛 com_error）。
#   ④ 源码保持 Python 3.6 兼容（不用海象运算符 / 3.9+ 语法）。
# ══════════════════════════════════════════════════════════════════════════

# SolidWorks 类型库 GUID（报告 1.2 原文常量；与既有 SW_TLB_IID 同值，
# 这里用报告里的名字，便于和现场文档一一对照）
_SLDWORKS_TLB_GUID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"

# 推导不出 major 时的兜底尝试序列（报告 1.2：SW2025 = 33，逐版本向前回退）。
# 注意：这是【最后手段】，正常情况下走 sw.RevisionNumber / 注册表推导。
_EARLY_MAJOR_FALLBACK = (33, 32, 31, 30, 29, 28)

# 早期绑定进程内状态（模块级单例，只为诊断统计，不做线程同步）。
#   module        : 已解析的 gen_py 模块（成功时缓存，None=尚未成功）
#   major         : 实际生效的类型库 major（0=未知）
#   error         : 最近一次"不可用"的原因（人类可读，供 early_status 展示）
#   failed        : 失败结论缓存标志 —— True 时不再无谓重试
#   tried_majors  : 已尝试过的 major 列表（避免对同一 major 反复重试）
#   wrapped_count : 成功包装次数（诊断用）
#   failed_count  : 包装失败/回退次数（诊断用）
#   last_wrap_error: 最近一次包装失败原因
_EARLY_STATE = {
    "module": None,
    "major": 0,
    "error": None,
    "failed": False,
    "tried_majors": [],
    "wrapped_count": 0,
    "failed_count": 0,
    "last_wrap_error": None,
}


def _early_major_from_sw(sw):
    """从 sw.RevisionNumber 推导类型库 major（报告 1.2：33.5.0 → 33）。

    为什么优先用它：类型库的 major 号跟随 SolidWorks 版本，从已连接的
    Application 上读是最准的（注册表可能残留旧版本或其它产品的同名键）。
    任何异常（未连接/属性不存在/格式异常）都返回 0，由上层继续退。
    """
    if sw is None:
        return 0
    # ── 【R11 修复】ISldWorks.RevisionNumber 在类型库里是【方法】
    #   （gen_py L60473：`def RevisionNumber(self):`，invkind=METHOD，
    #   无参）。旧代码按属性 getattr 拿到方法对象，再用 callable() 去猜
    #   —— 这正是台账 BUG-77「属性/方法混淆」的典型写法：
    #   callable() 对"恰好可调用"的值（类、函数、部分 COM 代理）恒为 True，
    #   猜错就把一个函数对象当字符串去 int(float(str(...)))，静默返回 0。
    #   现在：类型库判定为方法 → 直接加括号调用；失败给出可读原因。
    rev = None
    try:
        rev = _com_get(sw, "RevisionNumber")
    except ComMemberError as _e_rev:
        _warn_stderr("读取 sw.RevisionNumber 失败（按 0 处理）: %s" % (_e_rev,))
        rev = None
    except Exception:
        rev = None
    try:
        if rev is None:
            return 0
        _major = int(float(str(rev).split(".")[0]))
        return _major if _major > 0 else 0
    except Exception:
        return 0


def _early_major_from_registry():
    """注册表兜底推导 major：SOLIDWORKS <年份> → major = 年份 - 1992。

    为什么需要这条退路：报告 1.1 的结论是"早期绑定必需"，但很多场景下
    get_early_module() 会在【还没有 sw 对象】时被调用（例如 is_early_available()
    的启动自检）。此时没有 RevisionNumber 可读，只能从注册表找已安装版本。
    失败（无注册表/无权限/非 Windows）一律返回 0，不抛异常。
    """
    if sys.platform != "win32":
        return 0
    try:
        import winreg
    except Exception:
        return 0
    _years = []
    for _hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for _base in (r"SOFTWARE\SolidWorks",
                      r"SOFTWARE\WOW6432Node\SolidWorks"):
            _k = None
            try:
                _k = winreg.OpenKey(_hive, _base)
            except Exception:
                continue
            try:
                _i = 0
                while True:
                    try:
                        _sub = winreg.EnumKey(_k, _i)
                        _i += 1
                    except OSError:
                        break
                    _s = str(_sub).upper().replace("-", " ").replace("_", " ")
                    if "SOLIDWORKS" not in _s:
                        continue
                    for _tok in _s.split():
                        if len(_tok) == 4 and _tok.isdigit():
                            _y = int(_tok)
                            # 允许 2010~2100：2018=26 ... 2025=33
                            if 2010 <= _y <= 2100:
                                _years.append(_y)
            finally:
                try:
                    winreg.CloseKey(_k)
                except Exception:
                    pass
    if not _years:
        return 0
    try:
        # 取【最高】年份：多版本共存时，类型库 major 也以新版本为准
        return int(max(_years)) - 1992
    except Exception:
        return 0


def _early_major_candidates(sw=None):
    """给出待尝试的 major 列表：sw 版本 → 注册表 → 兜底序列。

    Returns: list[int]（有序去重；兜底序列始终追加，因此非空）。
    """
    _out = []
    try:
        _m = _early_major_from_sw(sw)
        if not _m:
            _m = _early_major_from_registry()
        if _m and _m not in _out:
            _out.append(_m)
    except Exception:
        pass
    for _m in _EARLY_MAJOR_FALLBACK:
        if _m not in _out:
            _out.append(_m)
    return _out


def get_early_module(sw=None):
    """取 SolidWorks 类型库的 gen_py 模块（早期绑定入口）；失败返回 None。

    来源：报告 1.2 —— SW2025 必须 ``gencache.EnsureModule(GUID, 0, 33, 0)``。

    Args:
        sw: 晚期绑定的 Application 对象（可选）。给了就从 sw.RevisionNumber
            推导类型库 major（33.5.0 → 33），避免把版本号写死成 33。

    Returns:
        gen_py 模块对象；不可用（类型库未注册 / 版本不符 / gen_py 目录无写
        权限 / 没装 pywin32）时返回 None。【任何情况下都不抛异常】。

    失败缓存（很重要）：
        gencache.EnsureModule 在"类型库未注册"的机器上会抛 com_error
        （实机 -2147319779 '库没有注册'）；逐个候选 major 试完是本层最慢的
        分支。因此把"不可用"结论连同已试过的 major 一起缓存，后续调用直接
        返回 None，不再重试 —— 满足"不要每次重试拖慢启动"的硬性要求。
        唯一的例外：调用方后来给了一个【没试过】的 major（例如先无 sw
        自检失败，之后连上 SW 2023 得到 31），则允许再试一次。
    """
    _st = _EARLY_STATE
    if _st["module"] is not None:
        return _st["module"]
    try:
        _majors = _early_major_candidates(sw)
    except Exception as _e_cand:
        _st["failed"] = True
        _st["error"] = "推导类型库 major 失败: %r" % (_e_cand,)
        return None
    # ── 失败缓存：没有"没试过的 major"就直接返回 None，不再重试 ────────
    if _st["failed"]:
        _pending = [m for m in _majors if m not in _st["tried_majors"]]
        if not _pending:
            return None
        _majors = _pending
    # ── gencache 依赖：导入失败就给结论、缓存、回退 ────────────────────
    try:
        from win32com.client import gencache as _gencache
    except Exception as _e_imp:
        _st["failed"] = True
        _st["error"] = "无法导入 win32com.client.gencache: %r" % (_e_imp,)
        return None
    # COM 未初始化时 EnsureModule 也可能失败，这里尽力初始化（重复调用安全）
    try:
        import pythoncom as _pyc
        try:
            _pyc.CoInitialize()
        except Exception:
            pass
    except Exception:
        pass
    _errors = []
    for _maj in _majors:
        try:
            _st["tried_majors"].append(_maj)
        except Exception:
            pass
        try:
            _mod = _gencache.EnsureModule(_SLDWORKS_TLB_GUID, 0, _maj, 0)
        except Exception as _e_em:
            _errors.append("major %s: %r" % (_maj, _e_em))
            continue
        if _mod is None:
            _errors.append("major %s: EnsureModule 返回 None" % (_maj,))
            continue
        # ── 成功：缓存模块与 major，清掉失败标记 ──────────────────────
        _st["module"] = _mod
        _st["major"] = _maj
        _st["error"] = None
        _st["failed"] = False
        return _mod
    _st["failed"] = True
    _st["major"] = 0
    # 只保留最近 3 条原因，避免错误串过长；完整现象见 last_wrap_error
    _st["error"] = ("; ".join(_errors[-3:]) if _errors
                    else "所有候选 major 的 EnsureModule 均失败")
    return None


def early(obj, iface_name="IDispatch"):
    """把晚期绑定 COM 对象包装成【指定接口】的早期绑定对象（失败原样返回）。

    来源：报告 1.2 —— ``mod.ISldWorks(raw._oleobj_)`` /
    ``mod.IModelDoc2(model._oleobj_)`` / ``mod.IAssemblyDoc(model._oleobj_)`` /
    ``mod.IComponent2(component._oleobj_)``。

    Args:
        obj: 晚期绑定对象（CDispatch / <unknown> 等）。
        iface_name: gen_py 模块里的接口类名，如 'ISldWorks'、'IModelDoc2'、
            'IAssemblyDoc'、'IComponent2'。

    Returns:
        包装后的早期绑定对象；模块不可用、接口名不存在、包装抛异常时
        【原样返回 obj】—— 调用方永远可以继续按晚期绑定方式使用它，
        因此本函数是"可失败的增强"，绝不中断建模流程。
    """
    if obj is None:
        return None
    _st = _EARLY_STATE
    _mod = None
    try:
        _mod = get_early_module(None)
    except Exception:
        _mod = None
    if _mod is None:
        # 模块不可用 → 回退（计入 failed_count，诊断时能看出回退发生频率）
        try:
            _st["failed_count"] = int(_st.get("failed_count") or 0) + 1
            _st["last_wrap_error"] = ("%s: 早期绑定模块不可用（已回退晚期绑定）"
                                      % (iface_name,))
        except Exception:
            pass
        return obj
    try:
        _cls = getattr(_mod, iface_name, None)
        if _cls is None:
            raise AttributeError("类型库中没有接口 %s" % (iface_name,))
        _raw = getattr(obj, "_oleobj_", None)
        if _raw is None:
            raise AttributeError("对象没有 _oleobj_（不是 COM 对象？）")
        _wrapped = _cls(_raw)
        if _wrapped is None:
            raise RuntimeError("接口构造返回 None")
        try:
            _st["wrapped_count"] = int(_st.get("wrapped_count") or 0) + 1
        except Exception:
            pass
        return _wrapped
    except Exception as _e_wrap:
        try:
            _st["failed_count"] = int(_st.get("failed_count") or 0) + 1
            _st["last_wrap_error"] = "%s: %r" % (iface_name, _e_wrap)
        except Exception:
            pass
        # ★ 硬性要求：包装失败必须静默回退到晚期绑定对象
        return obj


def is_early_available():
    """当前进程内早期绑定是否可用（bool）。【绝不抛异常】。"""
    try:
        return get_early_module(None) is not None
    except Exception:
        return False


def early_status():
    """早期绑定状态摘要（供日志/自检/CLI 直接打印）。

    Returns: dict，字段固定为
        available (bool) / major (int) / error (str|None) /
        wrapped_count (int) / failed_count (int)
    """
    try:
        _avail = is_early_available()
    except Exception:
        _avail = False
    _st = _EARLY_STATE
    try:
        _major = int(_st.get("major") or 0)
    except Exception:
        _major = 0
    try:
        _wrapped = int(_st.get("wrapped_count") or 0)
    except Exception:
        _wrapped = 0
    try:
        _failed = int(_st.get("failed_count") or 0)
    except Exception:
        _failed = 0
    return {
        "available": bool(_avail),
        "major": _major,
        "error": _st.get("error"),
        "wrapped_count": _wrapped,
        "failed_count": _failed,
    }


def early_diagnose():
    """早期绑定诊断详情（CLI 友好：一键看"为什么不可用"）。

    Returns: dict —— 在 early_status() 的五个字段之外，补充：
        guid / major_candidates / tried_majors / last_wrap_error /
        tlb_path / gen_py_dir / summary / hint
    任何子项取不到都置 None，【绝不抛异常】（排障工具不能自己先崩）。
    """
    try:
        _status = early_status()
    except Exception:
        _status = {"available": False, "major": 0, "error": "early_status 异常",
                   "wrapped_count": 0, "failed_count": 0}
    _tlb = None
    try:
        _tlb = _find_sldworks_tlb()
    except Exception:
        _tlb = None
    _cache_dir = None
    try:
        from win32com.client import gencache as _g
        try:
            _cache_dir = _g.GetGeneratePath()
        except Exception:
            _cache_dir = None
    except Exception:
        _cache_dir = None
    _d = dict(_status)
    _d["guid"] = _SLDWORKS_TLB_GUID
    _d["major_candidates"] = list(_EARLY_MAJOR_FALLBACK)
    _d["tried_majors"] = list(_EARLY_STATE.get("tried_majors") or [])
    _d["last_wrap_error"] = _EARLY_STATE.get("last_wrap_error")
    _d["tlb_path"] = _tlb
    _d["gen_py_dir"] = _cache_dir
    if _d.get("available"):
        _d["summary"] = ("早期绑定可用（类型库 major=%s）"
                         % (_d.get("major") or "?",))
        _d["hint"] = "可直接用 early(obj, 'IModelDoc2'/'IAssemblyDoc' 等) 包装。"
    else:
        _d["summary"] = ("早期绑定不可用，已回退晚期绑定：%s"
                         % (_d.get("error") or "原因未知"))
        _d["hint"] = ("检查 ① 是否安装 SolidWorks 与 pywin32；"
                      "② sldworks.tlb 是否注册（看 tlb_path）；"
                      "③ gen_py 目录是否可写（看 gen_py_dir）。")
    return _d


def _attach_early_probe(sw):
    """连接成功后探测一次早期绑定，并把结论尽力挂到 sw 上（挂不上就忽略）。

    为什么【不改变 get_sw 的返回值】：get_sw 必须继续返回晚期 Dispatch 对象
    —— SW 2018~2024 上大量既有代码依赖晚期绑定行为（swapi 内大量 VARIANT
    传参），把返回值换成 mod.ISldWorks 会破坏兼容性。
    本函数只做"探测 + 诊断"，供 B/C 子任务按需 early() 包装。

    Args:
        sw: get_sw() 刚连上的 Application 对象。
    Returns: bool（早期绑定是否可用）。绝不抛异常。
    """
    _mod = None
    try:
        _mod = get_early_module(sw)
    except Exception:
        _mod = None
    _ok = _mod is not None
    # "属性可能挂不上"：pywin32 的 CDispatch 一般有 __dict__，但不同绑定方式
    # 下可能拒绝赋值 —— 一律 try/except，挂不上就静默忽略，绝不影响连接。
    try:
        sw.__dict__["_dsh_early_available"] = _ok
        sw.__dict__["_dsh_early_module"] = _mod
        sw.__dict__["_dsh_early_major"] = _EARLY_STATE.get("major", 0)
    except Exception:
        pass
    return _ok


def _early_reset_cache():
    """清空早期绑定缓存（仅排障/自测用，正常运行无需调用）。

    为什么留这个口子：失败结论是【进程内永久缓存】的（见 get_early_module），
    修好环境（补 gen_py 写权限 / 注册类型库 / 先启动 SW）后需要能重新探测。
    """
    _st = _EARLY_STATE
    _st["module"] = None
    _st["major"] = 0
    _st["error"] = None
    _st["failed"] = False
    _st["tried_majors"] = []
    _st["wrapped_count"] = 0
    _st["failed_count"] = 0
    _st["last_wrap_error"] = None
    return early_status()


# ==================== 版本相关枚举（自动适配） ====================

def _get_midplane_enum(sw):
    """两侧对称拉伸的枚举值 swEndCondMidPlane。

    ══ 【枚举订正 · 2026-11-11】原实现按版本返回 2022=6 / 2018=5 ═══════
    原实现（已删除）按主版本号分支：2020+ 给 6，更老的版本给
    swEndCondOffsetFromSurface 的数值 —— 那个数值是"从曲面偏移"，
    与"两侧对称"不是一回事（具体数值见下面的官方成员表）。

    ── 判据从哪来（如实标注证据强度）────────────────────────────────────
    ① 【官方文档·确定】swEndConditions_e 从 2018 到 2025 的成员表完全一致：
         swEndCondBlind=0 / ThroughAll=1 / ThroughNext=2 / UpToVertex=3 /
         UpToSurface=4 / OffsetFromSurface=5 / MidPlane=6 / UpToBody=7 /
         ThroughAllBoth=9 / UpToSelection=10 / UpToNext=11
       所以 5【不是】MidPlane，是 OffsetFromSurface（从曲面偏移）。
       参考：https://help.solidworks.com/2018/English/api/swconst/
             SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swEndConditions_e.html
    ② 【推断·未真机验证】"老版本上 symmetric=True 生成的不是对称实体"
       这一点【没有】实机复现记录 —— 本轮验收会话（20261111_131815）跑的是
       SW2025（_version_major>=28，原实现照样给 6），从未进入老版本分支。
       也就是说：这是"按官方表反推出的推断"，不是现场观测。
       标注清楚是为了不把推断当证据用（本仓库的一贯要求）。

    ── 为什么仍然改 ────────────────────────────────────────────────────
    即使推断未经实测，"5 = 从曲面偏移"是官方事实：把它当对称用，只要该值
    被 SW 接受，几何就是错的、而且【不会报错】—— 属于最危险的静默错误。
    恒返 6 在所有版本上都与官方表一致，比"按版本猜"严格更优，且
    2020+ 路径（返回 6）行为逐字节不变，无回归面。

    保留 sw 形参：兼容 swapi.py:10012（extrude 的 symmetric 分支）等调用点。
    """
    return SW_END_MID_PLANE


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
# ── 【F 修复·第四轮】swEndConditions_e 完整枚举 ────────────────────────
# 测试反馈：FeatureCut3 双向传 T1=SW_END_THROUGH 时只去料 1.96%，
#   说明该枚举值【没有被识别为贯穿】，而是被当成了极小盲孔。
# 根因：SolidWorks 的 swEndConditions_e 里 "完全贯穿" 与 "双向贯穿" 是两个
#   不同枚举（本段下方 2026-11-11 起以官方原文为准，此前"各版本取值不同"
#   的猜测已被推翻 —— 官方 2018/2019/2025 的表完全一致）。
#   原实现把 T1/T2 都设为 1，在需要"双向贯穿"的场合语义不符，
#   部分版本会退化成盲孔。
#   ⚠️ 历史误值：本段曾写 swEndCondThroughAllBoth = 2，2 实为
#      swEndCondThroughNext（切到下一面就停）—— 详见下方 SW_END_* 块。
# ══ 【枚举订正 · 2026-11-11】按官方 swEndConditions_e 原文重写 ═══════════
# 现场现象：cut(through=True) 在双环辐板（DSH_大齿轮）上孔【不贯穿】，体积
#   实测 1214042.68 mm³ vs 期望 809621.02 mm³（偏差 49.95%），self_check FAIL。
#   证据：C:\Users\j1877\Desktop\test\20261111_131815_580E38FA\reports\
#         DSH_大齿轮.self_check.json
# 根因：本块原把「双向贯穿」写成 2，而官方 2 是 swEndCondThroughNext
#   （沿方向切到【下一个面】就停）。辐板是双环薄壁，"到下一面就停"正好
#   停在环间空腔，于是孔只切掉一小段 —— 结构上完全解释得通。
#   同时原表把 UpToNext/UpToVertex/UpToSurface/OffsetFromSurface 整体错位
#   一位（3/4/5/6 实际分别是 UpToVertex/UpToSurface/OffsetFromSurface/
#   MidPlane），属于同一处凭猜测填表的连带错误。
# 修复判据（官方原文，多份 swEndConditions_e 文档一致，2018/2019/2025 同表）：
#   swEndCondBlind=0 / swEndCondThroughAll=1 / swEndCondThroughNext=2 /
#   swEndCondUpToVertex=3 / swEndCondUpToSurface=4 / swEndCondOffsetFromSurface=5 /
#   swEndCondMidPlane=6 / swEndCondUpToBody=7 / swEndCondThroughAllBoth=9 /
#   swEndCondUpToSelection=10 / swEndCondUpToNext=11
#   参考：https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swEndConditions_e.html
# 注意：官方另注 UpToVertex(3)/UpToSurface(4) 已 "Do not use，superseded by
#   swEndCondUpToSelection"；这里仍按原值保留，仅为让枚举表可读、可排错。
SW_END_BLIND = 0                # swEndCondBlind          给定深度
SW_END_THROUGH = 1              # swEndCondThroughAll     完全贯穿（单向）
SW_END_THROUGH_BOTH = 9         # swEndCondThroughAllBoth 完全贯穿-双向
# ↑ 旧值 2 = swEndCondThroughNext（切到下一面就停），曾被误用导致孔不贯穿；
#   见本块顶部 2026-11-11 证据。千万别再改回 2。
SW_END_THROUGH_NEXT = 2         # swEndCondThroughNext    切到下一面
SW_END_UP_TO_VERTEX = 3         # swEndCondUpToVertex     到顶点（官方标注 Do not use）
SW_END_UP_TO_SURFACE = 4        # swEndCondUpToSurface    到曲面（官方标注 Do not use）
SW_END_OFFSET_FROM_SURFACE = 5  # swEndCondOffsetFromSurface 从曲面偏移
SW_END_MID_PLANE = 6            # swEndCondMidPlane       两侧对称（到中面）
SW_END_UP_TO_BODY = 7           # swEndCondUpToBody       到实体
SW_END_UP_TO_SELECTION = 10     # swEndCondUpToSelection  到选中项（官方推荐替代 3/4）
SW_END_UP_TO_NEXT = 11          # swEndCondUpToNext       到下一个面（顶点式）
# 贯穿兜底深度（mm）：当贯穿枚举不生效时，用极大的盲孔深度模拟贯穿。
#   这是测试部建议的兜底策略 —— 比依赖枚举值更可靠、跨版本一致。
THROUGH_FALLBACK_DEPTH_MM = 9999.0

# ══ 【save_as Options 修复 · 2026-11-11】官方 swSaveAsOptions_e 位掩码 ═════
# 官方原文（Bitmask）：
#   swSaveAsOptions_Silent            = 1 or 0x1
#   swSaveAsOptions_Copy              = 2 or 0x2  存副本并【继续编辑当前文档】
#   swSaveAsOptions_SaveReferenced    = 4 or 0x4  连带保存组件/外部参考
#   swSaveAsOptions_AvoidRebuildOnSave= 8 or 0x8
#   其余：UpdateInactiveViews=0x10 / OverrideSaveEmodel=0x20 /
#         IgnoreBiography=0x100 / CopyAndOpen=0x200 / IncludeVirtualSubAsmComps=0x400 /
#         ExportTo2DPdfFromInspection=0x800 / PropagateVisualProperties=0x1000
#   参考：https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swSaveAsOptions_e.html
# 现场现象：装配体 save_as 返回 ok:false、v2/v3 均未落盘
#   （intermediate\assemble_result.json 第 144-148 行）；零件侧另有 3 条
#   「SaveAs3 未抛异常但目标文件完全未变化」（parts_rejects\rejected.json）。
# 根因：原实现 Options=2 是 Copy —— 只把内容写成副本，文档【不绑定】到新路径
#   （GetPathName 仍为空），对装配体尤其容易不落盘；且与 save() 的 Options=1
#   口径不一致。Copy 不是"覆盖保存"，从来不是本方法想要的语义。
SW_SAVE_AS_SILENT = 1
SW_SAVE_AS_COPY = 2
SW_SAVE_AS_SAVE_REFERENCED = 4
SW_SAVE_AS_AVOID_REBUILD_ON_SAVE = 8
# 主路径选项 = Silent|SaveReferenced。用位或显式表达"位掩码"，不用魔数。
SW_SAVE_AS_OPTIONS_PRIMARY = SW_SAVE_AS_SILENT | SW_SAVE_AS_SAVE_REFERENCED   # 5
# 回退路径选项 = Silent（绝不用 Copy=2：见上）。
SW_SAVE_AS_OPTIONS_FALLBACK = SW_SAVE_AS_SILENT                               # 1

# swFileSaveError_e 位掩码 → 中文可读（官方原文节选，用于 error 文案点名）。
# 参考：https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swFileSaveError_e.html
_SW_FILE_SAVE_ERROR_BITS = (
    (0x1, "swGenericSaveError", "通用保存错误"),
    (0x2, "swReadOnlySaveError", "文档只读，无法写入"),
    (0x4, "swFileNameEmpty", "文件名为空"),
    (0x8, "swFileNameContainsAtSign", "文件名不能含 @"),
    (0x10, "swFileLockError", "文件被锁（被杀毒/同步盘/另一进程占用）"),
    (0x20, "swFileSaveFormatNotAvailable", "目标另存类型不可用"),
    (0x80, "swFileSaveAsDoNotOverwrite", "目标已存在且选择了不覆盖"),
    (0x100, "swFileSaveAsInvalidFileExtension", "扩展名与文档类型不符"),
    (0x200, "swFileSaveAsNoSelection", "另存仅选中实体：不是 SaveAs 的合法选项"),
    (0x400, "swFileSaveAsBadEDrawingsVersion", "eDrawings 版本不匹配"),
    (0x800, "swFileSaveAsNameExceedsMaxPathLength", "路径/文件名超过 255 字符"),
    (0x1000, "swFileSaveAsNotSupported", "该另存操作不被支持（如 SW 处于隐藏态）"),
    (0x2000, "swFileSaveRequiresSavingReferences", "装配体改名后必须先保存引用"),
    (0x4000, "swFileSaveAsDetachedDrawingsNotSupported", "不支持另存为分离工程图"),
    (0x8000, "swFileSaveDoNotUpgradeError", "启用了不升级策略，版本跨度过大"),
    (0x10000, "swFileSaveIncompatibleItemsError", "存为旧版本时有特性不兼容"),
)
# 出现这些位时，即使 SW 返回"成功"也必须按失败处理（磁盘指纹之外的硬证据）。
_SW_FILE_SAVE_FATAL_BITS = 0x10 | 0x100 | 0x2000 | 0x1 | 0x2 | 0x1000


def _decode_sw_save_mask(value):
    """把 swFileSaveError_e / swFileSaveWarning_e 位掩码拆成结构化条目。

    返回 [{"bit": int, "hex": "0x10", "name": "...", "text": "..."}]；
    value 为 None / 0 / 非整数一律返回 []（"没有错误位"与"读不到"由调用方
    用 mask 字段区分，绝不把读不到当没错误）。
    """
    try:
        _v = int(value)
    except Exception:
        return []
    if _v == 0:
        return []
    out = []
    for _bit, _name, _text in _SW_FILE_SAVE_ERROR_BITS:
        if _v & _bit:
            out.append({"bit": _bit, "hex": hex(_bit),
                        "name": _name, "text": _text})
    return out


def _sw_save_error_hint(value):
    """把错误位掩码变成一句可直接排障的中文提示（空掩码返回 ""）。"""
    _bits = _decode_sw_save_mask(value)
    if not _bits:
        return ""
    # 常见三码优先点名，便于在小屋日志里一眼看到
    _common = {0x10: "0x10 文件被锁", 0x100: "0x100 扩展名与文档类型不符",
               0x2000: "0x2000 装配体改名后需先保存引用"}
    _hit = [_common[b["bit"]] for b in _bits if b["bit"] in _common]
    _all = "；".join("%s %s" % (b["hex"], b["text"]) for b in _bits)
    if _hit:
        return ("常见码命中：%s。全部命中位：%s"
                % ("，".join(_hit), _all))
    return "全部命中位：%s" % _all
SW_START_SKETCHPLANE = 0    # 起始: 草图基准面
SW_REV_BLIND = 0            # 旋转到给定角度
# 圆角 Options
SW_FILLET_UNIFORM_RADIUS = 2   # 恒定半径圆角
SW_FILLET_SIMPLE = 0           # swFeatureFilletType_Simple
# 倒角 ChamferType
SW_CHAMFER_ANGLE_DIST = 1   # 角度-距离倒角
SW_CHAMFER_DIST_DIST = 2    # 距离-距离倒角
SW_CHAMFER_VERTEX = 3

# ══ 【R5 修复 · 2026-10-31】官方形参表：FeatureCut3/4 · FeatureExtrusion3 ══
# 依据 = 本机 SolidWorks【自己注册的类型库】（比任何网页都权威：形参名、
#   个数、顺序都是 SW 写进去的，不会错）：
#     C:\Users\<用户>\AppData\Local\Temp\gen_py\3.13\
#         83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
#     · IFeatureManager.FeatureCut3       L22427-22443 → 26 参
#     · IFeatureManager.FeatureCut4       L22446-22462 → 27 参
#                                              （= Cut3 + 末尾 OptimizeGeometry）
#     · IFeatureManager.FeatureExtrusion3 L22597-22611 → 23 参
#
# 三条【推翻历史误判】的硬结论（本任务的核心依据）：
#   ① FeatureExtrusion3 根本【没有切除参数】。第 18 位是 Merge（合并结果），
#      第 19 位 UseFeatScope，第 20 位 UseAutoSelect。类型库里【不存在】
#      "AddPad" 这个形参 —— 历史注释「改用 FeatureExtrusion3 的切除模式
#      (AddPad=False)」是错的：它只能增料、不可能去料。故 cut() 里的
#      FeatureExtrusion3 兜底已被删除（它只会在零件上留一个多余的凸台）。
#   ② FeatureCut 第 2 位 Flip =「反转切除侧 / flip side to cut」＝切掉
#      轮廓【外侧】的材料；真正反转【方向】的是第 3 位 Dir。
#      历史实现把"用户 flip"与"自动重试"都接到 Flip 上 → 直接切补集
#      （实测 -30520.6 mm³），而"体积大幅减小"还能通过体积校验 ——
#      属于最危险的静默毁件。现在 flip→Dir，Flip 只由 flip_side_to_cut 显式控制。
#   ③ FeatureCut4 必须 27 参，Cut3 是 26 参，两者【不能共用同一元组】；
#      少 1 个 → com_error -2147352561「非选择性的参数」（SW 把个数错报成
#      选择性错误，极难定位）。
CUT_PARAM_NAMES = (
    "Sd", "Flip", "Dir", "T1", "T2", "D1", "D2", "Dchk1", "Dchk2",
    "Ddir1", "Ddir2", "Dang1", "Dang2", "OffsetReverse1", "OffsetReverse2",
    "TranslateSurface1", "TranslateSurface2", "NormalCut", "UseFeatScope",
    "UseAutoSelect", "AssemblyFeatureScope", "AutoSelectComponents",
    "PropagateFeatureToParts", "T0", "StartOffset", "FlipStartOffset",
)
CUT_PARAM_NAMES_4 = CUT_PARAM_NAMES + ("OptimizeGeometry",)
EXTRUDE_PARAM_NAMES = (
    "Sd", "Flip", "Dir", "T1", "T2", "D1", "D2", "Dchk1", "Dchk2",
    "Ddir1", "Ddir2", "Dang1", "Dang2", "OffsetReverse1", "OffsetReverse2",
    "TranslateSurface1", "TranslateSurface2", "Merge", "UseFeatScope",
    "UseAutoSelect", "T0", "StartOffset", "FlipStartOffset",
)

# 草图合并容差（米）。end_sketch() 用它调 ISketchManager.MergePoints。
# ── 【R5 修复】原值 0.0005（= 0.5 mm）会把"两个闭合环"粘成一个连通轮廓 ──
# 实测（本机 SW2025 SP5.0，同一草图两个环）：
#   净距 0.328 mm → FeatureExtrusion3 返回 None（no_body），FeatureCut 亦失败；
#   净距 0.522 / 0.716 / 0.911 mm → 全部 PASS。
#   分界线恰好落在 0.5 mm —— 即旧容差本身。所以 0.328mm 那次失败不是"几何
#   不合法"，而是【我们自己的 MergePoints 把两个环合并掉了】。
# 现降到 0.1 mm：足够合并手画线的端点毛刺（通常 <0.05mm），又不会吃掉
#   0.1mm 以上的真实净距。
SKETCH_MERGE_TOL_M = 0.0001
SKETCH_MERGE_TOL_MM = SKETCH_MERGE_TOL_M * 1000.0


def _args_in_official_order(param_names, values):
    """按【官方形参名顺序】把 dict 里的值展开成位置参数元组。

    ── 【R5 修复】为什么要这个 ────────────────────────────────────────
    历史实现手写 26/27 元的位置元组，只要有人插一行/删一行就整体错位，
    而 SW 对错位【不报错】—— 表现只是"去料=0"或"返回 None"，是最难查的
    一类缺陷（台账里 10 次独立观测）。改成"按名字取值、按官方顺序展开"后，
    顺序错不了；名字写错会当场抛 ValueError 而不是静默错位。
    """
    missing = [n for n in param_names if n not in values]
    extra = [k for k in values if k not in param_names]
    if missing or extra:
        raise ValueError(
            "特征形参与官方表不符（缺失=%s / 多余=%s）—— 说明参数名拼错或"
            "SW 版本形参表变了，请对照类型库 IFeatureManager 更新"
            % (missing, extra))
    return tuple(values[n] for n in param_names)


def _cut_none_hint(model, api="FeatureCut3"):
    """【R5 修复】FeatureCut* 返回 None 时，拼一段【可操作】的原因说明。

    为什么要它：台账里"FeatureCut4 有时返回 CDispatch 但去料=0，有时直接
    返回 None"，小屋拿到的只有一句"返回 None"，无法区分是轮廓问题、
    几何问题还是多实体歧义，只能盲试 12 种签名（100 分钟零产物）。

    本函数把能当场读到的事实归成 5 类，逐条给出对应处置：
      ① 草图环净距 < 合并容差 → 轮廓会被 MergePoints 粘连（实测 0.328mm 必挂）
      ② 轮廓未选中 / 只选中草图特征 → 选段链路问题
      ③ 零件已分裂为多实体 → 切除结果归属歧义，SW 直接拒绝
      ④ 无实体 → 根本没东西可切
      ⑤ 其它 → 轮廓不闭合/自相交，或切除区与已有切除重叠（无料可切）
    """
    hints = []
    try:
        _gap = _ring_clearance_mm(getattr(model, "_sketch_circles_local", None))
        if _gap is not None and _gap < SKETCH_MERGE_TOL_MM:
            hints.append(
                "① 草图内两个圆形环净距仅 %.3f mm（< 合并容差 %.3f mm）："
                "MergePoints 会把两环粘连成一个连通区域 → SW 判定 no_body。"
                "处置：把环间距加大到 >%.3f mm，或分两次建模。"
                % (_gap, SKETCH_MERGE_TOL_MM, SKETCH_MERGE_TOL_MM * 5))
    except Exception:
        pass
    try:
        _n = int(model.model.SelectionManager.GetSelectedObjectCount2(-1))
        if _n <= 0:
            hints.append("② 选择集为 0：轮廓没被选中。处置：先 end_sketch() 再"
                         " select_all_sketch_segments()，或改用 "
                         "begin_sketch_on_face() 在实体面上重画轮廓。")
    except Exception:
        pass
    try:
        _b = model.model.GetBodies2(0, 1)
        _bl = list(_b) if isinstance(_b, tuple) else ([_b] if _b else [])
        if len(_bl) == 0:
            hints.append("④ 零件没有实体：切除无从作用。处置：先 extrude/revolve "
                         "生成基体。")
        elif len(_bl) > 1:
            hints.append(
                "③ 零件已分裂为 %d 个实体：SW 会因「切除结果归属歧义」直接拒绝。"
                "处置：先合并实体（FeatureManager.Combine），或调整轮廓"
                "避免产生悬空体。" % len(_bl))
    except Exception:
        pass
    if not hints:
        hints.append(
            "⑤ 常见原因：轮廓不闭合/自相交；切除区域与已有切除重叠（无料可切）；"
            "或草图位于法向背离材料的底面。处置：polyline() 会自动闭合，"
            "手工画线时确认末点回到起点；底面切除可加 flip=True 反转方向。")
    return "；".join(hints)


def _ring_clearance_mm(circles):
    """同一草图内【圆形闭合环之间】的最小净距（mm）。

    ── 【R5 实证】SW 两环同草图：
         净距 0.328 mm → FeatureExtrusion3 返回 none/no_body（必然失败）
         净距 0.522 / 0.716 / 0.911 mm → 全部 PASS
       分界线 = end_sketch() 的 MergePoints 容差（旧值 0.0005 m = 0.5 mm）。
    本函数用于在调特征 API【之前】就把这种"注定失败"的情形拦下来，
    并给出可读原因，而不是让 SW 返回 None 让上层瞎猜。

    Args:
        circles: [(cx, cy, r), ...]（mm，草图局部坐标）
    Returns:
        最小净距（mm，可能为负 = 两环相交）；不足两个环/数据非法 → None
    """
    try:
        pts = [(float(c[0]), float(c[1]), float(c[2])) for c in (circles or [])]
    except Exception:
        return None
    if len(pts) < 2:
        return None
    import math as _m
    best = None
    for i in range(len(pts)):
        x1, y1, r1 = pts[i]
        for j in range(i + 1, len(pts)):
            x2, y2, r2 = pts[j]
            _d = _m.hypot(x2 - x1, y2 - y1)
            # == [A-42 / BUG-ZZ-7 修复] 同心圆环不是"两个分离圆" ============
            # 原实现一律用【分离圆】间隙公式 gap = 圆心距 - r1 - r2：
            #   同心圆（圆心距 = 0）→ gap = -(r1 + r2)，恒为负。
            #   例：端盖密封槽 r=24 / r=18 → 0 - 24 - 18 = -42.0mm
            #       → 被判"净距 -42.000mm < 合并容差 0.100mm"→ 拒绝。
            # 但【同心圆环(annulus)】恰恰是轴承座/端盖/轴套的标准合法剖面，
            #   内圈是孔、外圈是外形，二者"相交"是设计意图而不是错误。
            # 实测（2026-11-01 底座支承室）：端盖密封槽因此永远切不出来；
            #   加工头室 4 同心圆轴承也在这条预检上被反复拦截。
            # 正确判据：
            #   · 同心（圆心距 < 容差）→ 两环是【嵌套】，净距应为 |r1 - r2|
            #     （壁厚方向的实际间距）；只有壁厚 < 容差才算真粘连。
            #   · 非同心 → 分离圆公式（圆心距 - r1 - r2）仍然正确。
            if _d <= 1e-6:
                gap = abs(r1 - r2)
            else:
                gap = _d - (r1 + r2)
            if best is None or gap < best:
                best = gap
    return best

# ══ 【R11 修复 · 2026-10-31】COM 晚绑定：属性/方法必须分清 ══════════════
# 台账 BUG-71/74/77/85/92：代码里到处 try/except 猜"这是属性还是方法"，
#   猜错的表现是 com_error「参数个数不正确 / 找不到成员 / 类型不匹配」，
#   而 try/except 一吞就变成"静默取到 None"，极难定位。
#
# 权威判据 = 本机 SolidWorks 自己注册的类型库：
#   %LOCALAPPDATA%\Temp\gen_py\3.13\
#       83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
#   规则（类型库的两套表）：
#     · **方法** → 生成 `def Foo(self, ...):`      （走 InvokeTypes / _ApplyTypes_）
#     · **属性** → 只出现在 `_prop_map_get_` / `_prop_map_put_` 字典里
#   也就是说：在 def 里出现 ⇒ 必须加括号；只在 _prop_map_ 里出现 ⇒ 不能加括号。
#
# 本文件已逐个核对过（行号 = 上述 gen_py 缓存）：
#   ┌ 方法（必须 ()）────────────────────────────────────────────────┐
#   │ IModelDoc2.GetMassProperties      L38295  （无参！）           │
#   │ IModelDoc2.FirstFeature           L34149 附近                 │
#   │ IModelDoc2.GetTitle / GetPathName                            │
#   │ IFeature.GetTypeName2 / GetNextFeature                        │
#   │ IFace2.GetBox L20604 / GetSurface L20604 区段 / GetEdges      │
#   │ ISurface.IsPlane L62439 / IsCylinder L62423 / Identity L62394 │
#   │ ISldWorks.RevisionNumber L60473 / GetMaterialDatabases L59525 │
#   │ ISldWorks.GetMathUtility / IGetMathUtility                    │
#   │ IPartDoc.GetMaterialPropertyName2 L47356                      │
#   │ IPartDoc.SetMaterialPropertyName2 L47898                      │
#   └───────────────────────────────────────────────────────────────┘
#   ┌ 属性（绝不能 ()）─────────────────────────────────────────────┐
#   │ IFace2.Normal                     L21163 (_prop_map_get_)    │
#   │ IFace2.MaterialIdName             L21160                     │
#   │ IPartDoc.MaterialIdName           L47915                     │
#   │ IComponent2.Name2/Transform2      L10416/L10426              │
#   └───────────────────────────────────────────────────────────────┘
#
# 注意：ISurface.IsPlane / IsCylinder / Identity 在类型库里是**方法**
#   （`def IsPlane(self):`，invkind=METHOD），而本文件此前的注释写的是
#   "pywin32 静态绑定下是属性，加括号会 TypeError" —— 该注释是错的，
#   真机之所以"属性写法也能用"，是因为 late-binding 下 pywin32 对
#   无参方法做了隐式调用。可靠做法是【显式加括号】，别赌绑定差异。
# ══ 【D4 修复】COM 忙 / 调用被拒绝的指数退避重试 ═════════════════════════
# 现场（回溯报告 §12.2）：SW 窗口可见且可被人工操作 —— 用户点一下 SW、
#   弹出保存提示、或切走窗口，COM 调用就会抛 "调用被拒绝"(RPC_E_CALL_REJECTED)
#   或 "服务器忙"(RPC_E_SERVERCALL_RETRYLATER)。原代码【全仓没有】任何
#   RPC_E 处理，只有锁层的 sw_busy —— 于是小屋拿到异常 → 改脚本重跑 →
#   即观测到的同一零件 4 版脚本重建（build_room1_gear_shaft v1→v4）。
# 修复：在【调用点】做退避重试（300ms → 5s，最多 5 次），而不是让上层重跑
#   整个脚本。只有明确的"COM 忙/被拒"才重试；业务性错误（轮廓无效等）
#   绝不重试，避免把真错误拖成长等待。
COM_RETRY_MAX = 5
COM_RETRY_BASE_SEC = 0.3
COM_RETRY_CAP_SEC = 5.0
# RPC_E_CALL_REJECTED=0x8001010A, RPC_E_SERVERCALL_RETRYLATER=0x80010001,
# RPC_E_SERVERCALL_RETRYLATER, RPC_E_DISCONNECTED=0x80010108,
# 以及 SW 常见的 "server busy"/"调用被拒绝" 文本
# ══ 数值与名称对照（依 [MS-ERREF] HRESULT Values，微软权威定义）══════════
#   ⚠️ 这两个码的【名字】极易记反，务必按此表核对：
#   0x80010001 = 2147549185 → signed -2147418111  = RPC_E_CALL_REJECTED
#                原文："Call was rejected by callee."
#   0x8001010A = 2147549450 → signed -2147417846  = RPC_E_SERVERCALL_RETRYLATER
#                原文："The message filter indicated that the application is busy."
#   0x80010109 = 2147549449 → signed -2147417847  = RPC_E_RETRY
#                原文："The object invoked chose not to process the call now."
#   0x8001010B = 2147549451 → signed -2147417845  = RPC_E_SERVERCALL_REJECTED
#   0x80010105 = 2147549445 → signed -2147417851  = RPC_E_SERVERFAULT
#   0x80010108 = 2147549448 → signed -2147417848  = RPC_E_DISCONNECTED
# 出处：https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/
#      705fb797-2175-4a90-b5a3-3918024b10b8
# 说明：SolidWorks 官方 API 文档【未收录】这些码；此处按微软权威定义 +
#   IMessageFilter::RetryRejectedCall 的官方重试语义处理。
_COM_BUSY_CODES = frozenset((
    -2147418111,   # 0x80010001 RPC_E_CALL_REJECTED（被呼叫方拒绝）
    -2147417846,   # 0x8001010A RPC_E_SERVERCALL_RETRYLATER（应用忙）
    -2147417847,   # 0x80010109 RPC_E_RETRY（稍后重试）
    -2147417845,   # 0x8001010B RPC_E_SERVERCALL_REJECTED（消息过滤器拒绝）
    -2147417851,   # 0x80010105 RPC_E_SERVERFAULT
    -2147417848,   # 0x80010108 RPC_E_DISCONNECTED
))
_COM_BUSY_TEXTS = (
    "call was rejected", "调用被拒绝", "服务器忙", "server busy",
    "retry later", "重试", "rpc_e_call_rejected", "rpc_e_servercall_retrylater",
    "被呼叫方拒绝接收呼叫", "com 对象与其基础 rcw 分开",
)


def _com_error_code(exc):
    """从 com_error / 任意异常里取 HRESULT（取不到返回 None）。"""
    for attr in ("hresult", "args"):
        try:
            v = getattr(exc, attr, None)
            if attr == "args" and v:
                v = v[0]
            if isinstance(v, int):
                return int(v)
        except Exception:
            continue
    return None


def is_com_busy_error(exc):
    """该异常是否属于"COM 忙 / 调用被拒绝 / 可重试"这一类。

    只认明确的 RPC_E_* 码与已知文案 —— 业务性失败（轮廓无效、特征返回 None）
    绝不算忙，否则会把真错误拖成 5 次长等待。
    """
    try:
        code = _com_error_code(exc)
        if code is not None:
            # pywin32 有时给 unsigned，统一按 signed 比较
            if code > 0x7FFFFFFF:
                code -= 0x100000000
            if code in _COM_BUSY_CODES:
                return True
        text = ("%s %s" % (type(exc).__name__, exc)).lower()
        for t in _COM_BUSY_TEXTS:
            if t in text:
                return True
    except Exception:
        pass
    return False


def com_retry(fn, *args, **kwargs):
    """调用 COM 成员；命中"忙/被拒"时指数退避重试（300ms→5s，最多 5 次）。

    用法：
        feat = com_retry(self.fm.FeatureExtrusion3, True, False, ...)
    或带说明：
        feat = com_retry(lambda: self.fm.FeatureExtrusion3(...), _what="拉伸")

    业务性异常【原样抛出】，不做任何重试。
    """
    _what = kwargs.pop("_what", None) or getattr(fn, "__name__", "COM 调用")
    _log = kwargs.pop("_log", None)
    delay = COM_RETRY_BASE_SEC
    last = None
    for attempt in range(COM_RETRY_MAX):
        try:
            return fn(*args, **kwargs)
        except SystemExit:
            raise
        except Exception as e:
            last = e
            if not is_com_busy_error(e):
                raise
            if attempt >= COM_RETRY_MAX - 1:
                break
            try:
                if callable(_log):
                    _log("COM 忙（%s）：%s 第 %d 次重试，等待 %.2fs"
                         % (type(e).__name__, _what, attempt + 1, delay))
            except Exception:
                pass
            try:
                time.sleep(delay)
            except Exception:
                pass
            delay = min(delay * 2.0, COM_RETRY_CAP_SEC)
    raise last


class ComMemberError(Exception):
    """【R11 修复】COM 成员访问失败（属性/方法误用、签名不符、对象缺失）。

    单独定义成异常类型而不是 RuntimeError，是为了让上层能精确区分
    "COM 访问问题"与"业务逻辑失败" —— 前者应修复调用方式，
    后者应重试/换策略，混为一谈正是台账里反复踩坑的原因。
    """


_COM_METHOD_NAMES = frozenset((
    # 高频且历史上被当属性误用的
    "GetMassProperties", "GetBox", "GetSurface", "GetEdges", "GetFaces",
    "GetSketchSegments", "GetTitle", "GetPathName", "GetName",
    "FirstFeature", "GetNextFeature", "GetTypeName2",
    "RevisionNumber", "GetMaterialDatabases", "GetMaterialDatabaseCount",
    "GetMathUtility", "IGetMathUtility",
    "IsPlane", "IsCylinder", "IsCone", "IsSphere", "IsTorus", "Identity",
    "GetMaterialPropertyName2", "SetMaterialPropertyName2",
    "GetClosestPointOn", "GetSelectedObject6", "GetBodies2",
    "GetMassProperties2", "GetMaterialIdName", "GetMaterialUserName",
))


def _com_get(obj, name, *args, **kwargs):
    """【R11 修复】按【类型库判定】取 COM 成员，属性/方法不再靠猜。

    为什么需要它（BUG-71/74/77/85/92 的共同根因）：
      同一份代码在"早期绑定(gencache)"与"晚绑定(dynamic.Dispatch)"下，
      无参方法可能表现为可调用对象也可能被隐式调用；而**属性**加了括号
      必然 TypeError。历史代码用
          v = getattr(obj, "Foo", None); if callable(v): v = v()
      去猜 —— 猜错的代价是 com_error「参数个数不正确」，被 except 吞掉后
      变成静默 None，故障现场只剩"密度恒为 1000"这类间接症状。

    现在：
      · 名字在 _COM_METHOD_NAMES（= 类型库里是 def 的）→ **一定** 调用；
      · 其余（= 只在 _prop_map_ 里的）→ **一定** 不加括号；
      · 取不到时抛 ComMemberError（可读），由调用方显式决定要不要吞。

    Args:
        obj:   COM 对象（也可为 None）
        name:  成员名（类型库里的拼写）
        *args: 仅当该成员是方法时使用的实参
    Returns:
        成员值 / 方法返回值
    Raises:
        ComMemberError: obj 为 None，或成员不存在，或调用失败
    """
    if obj is None:
        raise ComMemberError("对象为 None，无法取成员 %r" % (name,))
    try:
        member = getattr(obj, name)
    except Exception as e:
        raise ComMemberError("对象上不存在成员 %r（%s: %s）"
                             % (name, type(e).__name__, e))
    _is_method = name in _COM_METHOD_NAMES
    if not _is_method:
        # 类型库判定为属性 → 绝不能加括号
        return member
    if not callable(member):
        # 晚绑定下无参方法可能已被隐式求值，直接返回即可
        if args or kwargs:
            raise ComMemberError(
                "成员 %r 在类型库里是方法，但取到的是非可调用值 %r —— "
                "无法传入实参（这通常意味着该版本签名不同）"
                % (name, type(member).__name__))
        return member
    try:
        return member(*args, **kwargs)
    except TypeError as e:
        raise ComMemberError(
            "成员 %r 是方法但实参不匹配（%s）—— 请对照类型库核对该成员的"
            "形参个数" % (name, e))
    except Exception as e:
        raise ComMemberError("调用方法 %r 失败：%s: %s"
                             % (name, type(e).__name__, e))


def _com_get_soft(obj, name, default=None, *args, **kwargs):
    """_com_get 的"取不到就给默认值"版本（用于非关键路径）。

    与历史写法的区别：**只有真正取不到才返回 default**；
    类型库判定错误（如把属性当方法）会抛 ComMemberError 而不是静默 None。
    """
    try:
        return _com_get(obj, name, *args, **kwargs)
    except ComMemberError:
        return default


# ── 【新-4 修复】基准面白名单必须同时收【英文名 + 中文名】──────────────
# 测试反馈：中文版 SolidWorks 下用 "上视基准面"/"前视基准面" 开草图必崩。
#   根因：白名单只收英文名，中文名先被 ValueError 拒之门外，
#   导致 select_plane 里的中文别名映射表【永远到不了】。
# 修复：白名单直接纳入中文标准译名（SW 中文版实际使用的名称）。
_PLANES = ("Front Plane", "Top Plane", "Right Plane",
           "Bottom Plane", "Back Plane", "Left Plane",
           # 中文版标准译名
           "前视基准面", "上视基准面", "右视基准面",
           "后视基准面", "下视基准面", "左视基准面")

# ── 【D2 修复】建模期可见性开关（回溯报告 §12.5 D2 / §12.2）─────────────
# 默认保持用户偏好"实时看到建模过程"（solidworks-modeling.md §0.2）。
# 但多房间/排队建模时，可见窗口 + 人工操作会打断 COM（用户点 SW、弹保存框
#   → RPC_E_CALL_REJECTED → 小屋改脚本重跑）；此时可 opt-in 无头模式：
#       DSH_SW_VISIBLE=0   （或 false/no/off/hidden/headless）
# 无头模式下：连接后隐藏窗口、不做每步置前/可视化、bring_to_front 不再弹窗。
# 注意：这是"可配置"，不是强制隐藏 —— 用户明确要求可视化时保持默认。
_VIS_ENV = (os.environ.get("DSH_SW_VISIBLE") or "").strip().lower()
HEADLESS_MODE = _VIS_ENV in ("0", "false", "no", "off", "hidden", "headless")
VISUAL_MODE = not HEADLESS_MODE
# ── 无头模式的【官方正确姿势】（重要，别再自己发明）────────────────────
# 依据 help.solidworks.com 三处原文：
#  ① ISldWorks::Visible —— "the SOLIDWORKS Design application can only be
#     hidden if ISldWorks::UserControl is false and there are no visible
#     documents open."  → 直接 sw.Visible=False 在【有可见文档时会被忽略】，
#     所以必须先 UserControl=False。
#  ② IModelDoc2::Visible —— 官方警告："User-interface resources are not
#     released by setting this property to false. Opening large numbers of
#     models and then hiding them ... will eventually cause critical resource
#     shortages and instability."  → 绝不用它批量藏文档。
#  ③ IFrame::KeepInvisible —— 官方无头示例（Keep SOLIDWORKS Invisible While
#     Activating Documents）的唯一正确做法：UserControl=False + Visible=False
#     + Frame.KeepInvisible=True，操作完再复原。
# 另：SelectByID2 硬性前提 "must be an open and visible document"，
#   本模块 65 处 SelectByID2 依赖它 → 只做【应用级】隐藏，文档保持可见。
HEADLESS_DOCS_VISIBLE = True   # 恒为 True：文档级 Visible 一律不动（见 ②）
# 无头模式的实际生效状态（供 status / 排障读取，绝不用它假装成功）
_HEADLESS_STATE = {"app_visible": None, "frame_keep_invisible": False,
                   "note": None}
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



# ══ 【C04 修复】BMP → PNG 转换（SaveBMP 只产出 BMP）══════════════════════
def _bmp_to_png(bmp_path, png_path):
    """把 SaveBMP 产出的 BMP 转成目标图片格式（默认 PNG）。

    真机实测（SW2025）：SaveBMP 【忽略扩展名】—— 即使传 .png 也写出 BMP 字节
    （文件头 42 4D = "BM"）。所以必须显式转换，否则前端/识图插件按 PNG 解析会失败。

    依赖 Pillow；没装就返回 False，调用方保留 .bmp（Vision 也能直接读 BMP）。
    """
    try:
        from PIL import Image
    except Exception:
        return False
    try:
        _fmt = "PNG"
        _ext = os.path.splitext(str(png_path))[1].lower()
        if _ext in (".jpg", ".jpeg"):
            _fmt = "JPEG"
        elif _ext == ".webp":
            _fmt = "WEBP"
        with Image.open(bmp_path) as im:
            if _fmt == "JPEG" and im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            im.save(png_path, _fmt)
        return os.path.exists(png_path) and os.path.getsize(png_path) > 0
    except Exception:
        return False

def _window_title(hwnd):
    """读取窗口标题（用于截图可信度佐证：确认抓到的是 SolidWorks 窗口）。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 1))
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _force_activate_sw_window(hwnd=None, tries=3):
    """【Bug7 修复】强制把 SolidWorks 窗口真正激活到前台，并【验证】是否成功。

    背景问题：
      sw_bridge 报 screenshot.ok=true，但截图内容其实是 DSH Web GUI 的旧画面 ——
      因为 SetForegroundWindow 在 Windows 上会因"前台锁"(foreground lock)
      静默失败：调用不报错，但窗口根本没到前台，于是抓屏抓到的是屏幕上
      原本摆在前面的 DSH 窗口。截图"成功"但不可作验收依据。

    修复做法（多手段组合 + 结果校验）：
      1. 若窗口最小化先 SW_RESTORE；
      2. 用 AttachThreadInput 绕过前台锁（把本线程输入附到当前前台线程）；
      3. SetForegroundWindow + BringWindowToTop + SetActiveWindow；
      4. 用 GetForegroundWindow() 【校验】前台窗口是否已是目标；
      5. 失败则重试 tries 次，每次之间短暂等待；
      6. 返回 dict 明确告知激活是否成功 —— 调用方可据此拒绝出图，
         而不是把"抓错的画面"当成验收证据。

    Args:
        hwnd:  目标窗口句柄；None 则自动查找 SW 主窗口
        tries: 重试次数
    Returns:
        dict {ok: bool, hwnd: int, foreground: int, attempts: int, reason: str}
    """
    out = {"ok": False, "hwnd": None, "foreground": None, "attempts": 0, "reason": ""}
    try:
        import ctypes
        from ctypes import wintypes
        import time as _t
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if hwnd is None:
            try:
                hwnd, _ = _find_sw_windows()
            except Exception:
                hwnd = None
            if hwnd is None:
                try:
                    hwnd = _find_main_hwnd()
                except Exception:
                    hwnd = None
        if not hwnd:
            out["reason"] = "未找到 SolidWorks 窗口"
            return out
        out["hwnd"] = int(hwnd)

        # 最小化则先还原
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                _t.sleep(0.3)
        except Exception:
            pass

        for attempt in range(1, max(1, int(tries)) + 1):
            out["attempts"] = attempt
            try:
                # ① AttachThreadInput 绕过前台锁
                fg = user32.GetForegroundWindow()
                tid_cur = kernel32.GetCurrentThreadId()
                tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
                tid_tgt = user32.GetWindowThreadProcessId(hwnd, None)
                attached = []
                for tid in (tid_fg, tid_tgt):
                    try:
                        if tid and tid != tid_cur and user32.AttachThreadInput(tid_cur, tid, True):
                            attached.append(tid)
                    except Exception:
                        pass
                try:
                    # ② 三连：置顶 + 提升 + 激活
                    try:
                        user32.ShowWindow(hwnd, 3)      # SW_MAXIMIZE（可选，失败无害）
                    except Exception:
                        pass
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    try:
                        user32.SetActiveWindow(hwnd)
                    except Exception:
                        pass
                finally:
                    for tid in attached:
                        try:
                            user32.AttachThreadInput(tid_cur, tid, False)
                        except Exception:
                            pass

                _t.sleep(0.45)
                # ③ 校验：前台窗口是否就是目标
                now_fg = user32.GetForegroundWindow()
                out["foreground"] = int(now_fg) if now_fg else None
                if now_fg and int(now_fg) == int(hwnd):
                    out["ok"] = True
                    out["reason"] = "已激活并校验通过"
                    return out
                # 前台是目标的子/父窗口也算成功（SW 有多层窗口）
                try:
                    if now_fg and (user32.IsChild(hwnd, now_fg) or user32.IsChild(now_fg, hwnd)):
                        out["ok"] = True
                        out["reason"] = "已激活（前台为 SW 关联窗口）"
                        return out
                except Exception:
                    pass
                out["reason"] = "第%d次尝试后前台窗口仍非 SW (fg=%s)" % (attempt, out["foreground"])
            except Exception as e:
                out["reason"] = "第%d次激活异常: %r" % (attempt, e)
            _t.sleep(0.35)
        return out
    except Exception as e:
        out["reason"] = "激活流程异常: %r" % (e,)
        return out


def get_sw():
    """连接 SolidWorks（已运行则挂接，否则自动启动并等待窗口出现）。

    【Bug3 根因修复 · 关键】绑定方式优先用【静态绑定】(win32com.client.Dispatch)，
    而不是 dynamic.Dispatch。

    为什么这很关键（实测于 SW2025 Rev 33.5.0）：
      dynamic.Dispatch 是 late-binding(IDispatch::Invoke 靠名字猜类型)，
      对 SW 里大量"要求特定类型/VARIANT"的方法处理不正确，表现为：
        · IBody2.SetMaterialProperty(...) 恒返回 1/6（材料永远设不上，密度恒为1000）
        · Extension.SelectByID2(...) 抛 "类型不匹配 (None, 8)"
      换成静态绑定后，同样调用直接返回 0（成功），密度立刻 1000→7800。

    因此这里：
      1) 优先静态绑定（可用则用，所有 API 都按正确类型编组）；
      2) 静态绑定不可用时回退 dynamic（保证兼容性不下降）。
    """
    import time
    pythoncom.CoInitialize()
    sw = None
    _binding = None
    # ① 静态绑定（首选）
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
    # ── 【早期绑定】连接成功后顺手探测一次（失败不影响连接）─────────────
    # 来源：报告 1.1 / 1.2 —— SW2025 大批 API（GetModelDoc2 / SelectByID2 /
    #   CreateMate / AddMate5 / GetFeatureCount）必须早期绑定才能调用。
    # 注意：这里【不改变 get_sw 的返回值】，仍然返回晚期 Dispatch 对象
    #   （SW 2018~2024 及既有代码依赖它）；只把探测结论尽力挂到 sw 上，
    #   挂不上（属性不可写）也静默忽略。探测本身绝不抛异常。
    try:
        _attach_early_probe(sw)
    except Exception:
        pass
    _disable_snapping(sw)
    # ── 【D2 修复】无头模式：连接后隐藏 SW 窗口 ──────────────────────────
    # ⚠️ 已知约束（官方语义）：SelectByID2 要求 "open and visible document"。
    #   本模块 65 处用到 SelectByID2（选基准面/选面/选草图段），把文档也设为
    #   invisible 会让这些路径失败。因此这里只做"应用窗口隐藏"，并允许
    #   HEADLESS_DOCS_VISIBLE=False 时才真正把文档隐藏（默认不隐藏）。
    #   若某机器上无头仍导致选面失败 → 设 DSH_SW_VISIBLE=1 恢复可见即可。
    if HEADLESS_MODE:
        # ── 【D2 修复·官方配方】按 Keep SOLIDWORKS Invisible 示例的顺序：
        #   ① UserControl=False（否则 Visible=False 会被 SW 直接忽略）
        #   ② Visible=False
        #   ③ Frame.KeepInvisible=True（防止后续 ActivateDoc 把窗口又拉出来）
        # 文档级 Visible 一律【不动】：SelectByID2 要求 open and visible
        #   document，且官方警告 IModelDoc2.Visible=False 会导致资源耗尽。
        try:
            sw.UserControl = False
        except Exception:
            pass
        try:
            sw.Visible = False
            _headless_visible_result = bool(sw.Visible)
        except Exception:
            _headless_visible_result = None
        try:
            _frame = sw.Frame()
            if _frame is not None:
                _frame.KeepInvisible = True
                _HEADLESS_STATE["frame_keep_invisible"] = True
        except Exception:
            pass
        _HEADLESS_STATE["app_visible"] = _headless_visible_result
        # 若隐藏没生效（有可见文档 / 被策略阻止），如实记一条告警，
        #   而不是假装"已经无头了"（假成功是这套系统最忌讳的）。
        if _headless_visible_result is True:
            _HEADLESS_STATE["note"] = (
                "sw.Visible=False 未生效（官方限制：必须有 UserControl=False "
                "且无可见文档）。窗口仍可见，但已开 KeepInvisible —— "
                "ActivateDoc 时不会再弹到前台。")
    if VISUAL_MODE:
        _wait_sw_window(timeout=60)
        time.sleep(1.0)
        _show_main_window()
    return sw


# ══ 【Bug⑪ 修复】零件 origin（原点）约定：落盘 + 可被 part_registry 读取 ══
# 现场缺陷（竞速小车任务，装配前必须人工换算）：
#   · 后轮 / 电机座：origin 在轮/电机轴心，z=0 为轴面；
#   · 万向轮支架：origin 在【件角】（非中心），顶面 z=0 为装配面，叉口向下；
#   · 底盘：origin 在底盘中心，z=3 为顶面。
#   三种约定并存 → 总装房必须为每个零件单独换算坐标，按"中心对称"直觉装配
#   会全部错位（配合的 distance/coincident 基准面都要按 origin 重定）。
#
# 本模块给出的【标准约定】= 底面中心（origin_mode = "center_bottom"）：
#   XY 原点在零件底面轮廓的中心，Z=0 在底面，+Z 朝上。
#   理由：装配时"把零件坐到某个平面上"是最常见的约束，底面中心让
#   配合只需一次 coincident（底面）+ 两次 distance（X/Y），无需换算。
#
# ⚠️ 本模块【不强行改已建零件的几何】（那会破坏既有图纸/装配），
#   只做两件事：
#     ① 建模时把 origin 约定记进 <零件>.origin.json（与 <零件>.material.json 同目录）；
#     ② 提供 read_origin_record()，供 part_registry 等下游读取。
ORIGIN_SCHEMA = "dsh-engineering/part-origin@1"
ORIGIN_STANDARD_MODE = "center_bottom"
ORIGIN_MODES = {
    # mode -> (中文说明, 装配含义)
    "center_bottom": ("底面中心（标准约定）",
                      "XY 原点=底面轮廓中心，Z=0 在底面，+Z 朝上"),
    "center_center": ("几何中心", "origin 在包围盒中心"),
    "corner_bottom": ("底角", "origin 在底面某个角点，Z=0 在底面"),
    "corner_top": ("顶角", "origin 在顶面某个角点"),
    "axis_center": ("轴心", "origin 在回转轴轴心，z=0 为轴端面"),
    "custom": ("自定义", "由建模者显式说明"),
}
ORIGIN_RECORD_SUFFIX = ".origin.json"


def origin_record_path(part_path):
    """零件 origin 约定文件的路径：<零件>.SLDPRT → <零件>.SLDPRT.origin.json。

    与材料凭据（defense_gate.attestation_path → <part>.material.json）
    同一命名约定：直接追加后缀，保证与零件【同目录、同名基】。
    """
    if not part_path:
        return None
    return str(part_path) + ORIGIN_RECORD_SUFFIX


def write_origin_record(part_path, origin_mode=ORIGIN_STANDARD_MODE,
                        origin_mm=None, bbox_mm=None, note=None,
                        extra=None):
    """【Bug⑪】把零件的 origin 约定落到 <零件>.origin.json（幂等，失败不致命）。

    Args:
        part_path:  零件落盘路径（.SLDPRT）。None/空 → 不写，返回 ok=False。
        origin_mode: 见 ORIGIN_MODES，默认标准约定 center_bottom。
        origin_mm:  origin 在【全局坐标】中的位置 [x,y,z]（mm），可选。
        bbox_mm:    零件真实包围盒 [xmin,xmax,ymin,ymax,zmin,zmax]（mm），可选。
        note:       自由说明（如"万向轮支架为历史遗留件，origin 在件角"）。
        extra:      额外字段（dict），原样并入记录。

    Returns:
        {"ok", "path", "record", "error"?}
        写失败只返回 ok=False，【绝不抛异常】—— 记录 origin 不是建模的前置条件。
    """
    out = {"ok": False, "path": None, "record": None}
    try:
        p = origin_record_path(part_path)
        if not p:
            out["error"] = "part_path 为空，无法写 origin 记录"
            return out
        out["path"] = p
        _mode = str(origin_mode or ORIGIN_STANDARD_MODE).strip()
        _desc = ORIGIN_MODES.get(_mode, (None, None))
        rec = {
            "schema": ORIGIN_SCHEMA,
            "part": os.path.abspath(str(part_path)),
            "part_name": os.path.splitext(os.path.basename(str(part_path)))[0],
            "origin_mode": _mode,
            "origin_mode_cn": _desc[0],
            "assembly_note": _desc[1],
            "standard_mode": ORIGIN_STANDARD_MODE,
            "is_standard": (_mode == ORIGIN_STANDARD_MODE),
            "note": (str(note) if note else None),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
        }
        # 幂等：文件已存在时保留首次记录的 created_at，只更新本次字段
        _old = read_origin_record(part_path)
        if _old:
            rec["created_at"] = _old.get("created_at") or _old.get("at")
            rec["revision"] = int(_old.get("revision") or 1) + 1
        else:
            rec["created_at"] = rec["at"]
            rec["revision"] = 1
        if origin_mm is not None:
            try:
                rec["origin_mm"] = [float(x) for x in origin_mm]
            except Exception:
                rec["origin_mm"] = None
        if bbox_mm is not None:
            try:
                rec["bbox_mm"] = [round(float(x), 4) for x in bbox_mm]
            except Exception:
                rec["bbox_mm"] = None
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in rec:
                    rec[k] = v
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        out["ok"] = True
        out["record"] = rec
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


def read_origin_record(part_path):
    """【Bug⑪】读取 <零件>.origin.json；不存在/损坏返回 None（绝不抛异常）。

    part_path 可以直接传【零件路径】，也可以传 origin.json 路径本身
    （part_registry 拿到的是零件 file 字段，两种都容忍）。
    """
    if not part_path:
        return None
    _cands = []
    _s = str(part_path)
    if _s.lower().endswith(ORIGIN_RECORD_SUFFIX):
        _cands.append(_s)
    _cands.append(_s + ORIGIN_RECORD_SUFFIX)
    for p in _cands:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def _reject_failed_part(target, out, stage="save"):
    """【2026-11-07】保存失败 → 半成品零件挪进 <任务>/parts_rejects/（收尾会删）。

    用户要求（原文）：「假设建模失败的文件，直接不用保存即可」。
    与 sw_bridge._reject_failed_save 同一策略：
      · 不 unlink（SW 可能还持有句柄 → WinError 32；且失败件是排障证据）；
      · 挪进 parts_rejects/，保证 parts/ 里不会出现"看着像交付物、实际打不开"的件；
      · 任何异常只往 out 里写一行 note，绝不影响保存本身的返回语义。
    """
    try:
        import task_workspace as _tw
        _wd = None
        for _k in ("DSH_TASK_WORK_DIR", "DSH_WORK_DIR"):
            _v = (os.environ.get(_k) or "").strip()
            if _v and os.path.isdir(_v):
                _wd = _v
                break
        if not _wd:
            try:
                import _store as _st
                _p = _st.state_path("workflow_state.json")
                if os.path.isfile(_p):
                    with open(_p, "r", encoding="utf-8-sig") as _f:
                        _wd = str((json.load(_f) or {}).get("work_dir") or "").strip() or None
            except Exception:
                _wd = None
        if not _wd:
            out["rejected_note"] = "读不到任务目录，失败件保留原处（不擅自删除）"
            return
        _r = _tw.reject_part(_wd, target,
                             reason=str((out or {}).get("error") or stage))
        if _r.get("ok"):
            out["rejected"] = {"moved": bool(_r.get("to")), "to": _r.get("to")}
            out["rejected_note"] = ("失败件已移入 parts_rejects/（收尾会整目录删除）")
        else:
            out["rejected_note"] = "移入 parts_rejects 失败: %s" % (_r.get("error") or "未知")
    except Exception as e:
        out["rejected_note"] = "移入 parts_rejects 异常: %r" % (e,)


def _attach_origin_record(model_obj, target, out):
    """【Bug⑪】保存成功后在零件旁写 origin 记录，并把结果挂到返回体。

    - origin_mode 从未显式设置过 → 用标准约定 center_bottom，并标注
      inferred=True（如实说明"这是规范默认值，不是实测推断"）；
    - 模型的包围盒读得到就一并记上（供装配换算/一致性核对）；
    - 任何异常只写 warning，绝不影响保存结果本身。
    """
    try:
        _mode = getattr(model_obj, "_origin_mode", None) or ORIGIN_STANDARD_MODE
        _inferred = not bool(getattr(model_obj, "_origin_mode", None))
        _note = getattr(model_obj, "_origin_note", None)
        if _inferred:
            _note = (_note or "") + (
                "" if _note else
                "未显式声明 origin 约定，按标准约定（底面中心）记录。")
        _bbox = None
        try:
            _bbox = model_obj.body_box_mm()
        except Exception:
            _bbox = None
        _res = write_origin_record(
            target, origin_mode=_mode,
            bbox_mm=_bbox,
            note=_note or None,
            extra={"inferred": bool(_inferred),
                   "origin_source": ("explicit" if not _inferred
                                     else "standard_default"),
                   "standard_mode": ORIGIN_STANDARD_MODE})
        out["origin_record"] = {
            "ok": bool(_res.get("ok")),
            "file": _res.get("path"),
            "origin_mode": _mode,
            "inferred": bool(_inferred),
            "error": _res.get("error"),
        }
        if not _res.get("ok"):
            try:
                model_obj._warn("写 origin 记录失败（不阻断保存）: %s"
                                % _res.get("error"))
            except Exception:
                pass
    except Exception as e:
        try:
            out["origin_record"] = {"ok": False, "error": repr(e)}
        except Exception:
            pass
    return out


# ======================================================================
# 【P1-⑥ 修复】朝向声明 <零件>.SLDPRT.pr.json —— 保存后【强制】落盘
# ======================================================================
# 现场（回溯报告 7 P1 / 12.1）：
#   小屋只能靠试错发现 "Top Plane 草图局部 y = 世界 -Z"、"cut() 默认切向 -Y"；
#   零件建完只 save() 出 .SLDPRT，【没有任何朝向声明】；
#   总装时无从断言"这个零件摆正了没有" —— 组件零旋转、坐标也对得上，
#   但零件本身就是躺着的。
# 测试员核查结论（核查报告 二 P1 / 三 第 6 行）：
#   「未找到：没有 pr.json 相关代码」—— 当时只写在 SKILL.md 与提示词里。
# 用户原则：「所有约束都要写在代码里强制执行，不能只写在提示词」。
#
# 因此：save() 与 save_as() 在【材料凭据 / origin 记录】之后，
#   再落一份 pr.json。三者同基同目录，装配侧可直接读。
#
# 注意：写声明【绝不改变保存结果】—— 任何异常只记 warning（与 origin 同一纪律），
#   否则"写声明失败"会把一个已经成功落盘的零件判成失败。
def _attach_pr_record(model_obj, target, out):
    """保存后写 <零件>.SLDPRT.pr.json（幂等；失败不阻断）。

    取值优先级：
      (1) 建模脚本显式声明的 self.set_part_pose(...) -> source="explicit"；
      (2) 都没声明 -> 按全局约定（up=Z,+1）推断，但 source="inferred" 如实标注，
          总装断言遇到 inferred 会降级为 WARNING（不假装它被确认过）。
    bbox_mm 一并记上，供日后 validate_pr_record 做"声明 vs 几何"自洽检查。
    """
    try:
        import part_pose as _pp
    except Exception as e:
        try:
            out["pr_record"] = {"ok": False, "error": "part_pose 不可用: %r" % (e,)}
        except Exception:
            pass
        return out
    # 装配体不写朝向声明（与材料凭据同一判据：朝向声明是零件级的）
    try:
        if os.path.splitext(str(target))[1].lower() in (".sldasm",):
            out["pr_record"] = {"skipped": "assembly",
                                "note": "装配体不承载零件级朝向声明"}
            return out
    except Exception:
        pass
    try:
        _decl = getattr(model_obj, "_part_pose", None) or {}
        # 回读几何之前先确认"当前文档确实绑定在 target 上"。
        # 实测依据（SW2025 rev 33.5）：SaveAs3 用 Options=2(Copy) 时
        #   GetPathName 会是空串，文档并未绑到新路径；此时 body_box_mm()
        #   读到的是【另一个文档】的包围盒，写进 pr.json 就是错的证据。
        # 这里做一次核对：路径不一致就【不写 bbox】，并在返回里标明原因，
        #   宁可少一个字段，也不写一条错的几何。
        _path_ok = True
        _path_note = None
        try:
            _mdl = getattr(model_obj, "model", None)
            _gp = None
            if _mdl is not None:
                _gp = getattr(_mdl, "GetPathName", None)
                if callable(_gp):
                    _gp = _gp()
            if _gp is not None and str(_gp).strip():
                if os.path.normcase(os.path.abspath(str(_gp))) != \
                        os.path.normcase(os.path.abspath(str(target))):
                    _path_ok = False
                    _path_note = ("当前文档绑定路径(%s)与目标(%s)不一致，"
                                  "已跳过 bbox 回读以免写入别人的几何"
                                  % (_gp, target))
            # GetPathName 为空：说明文档未绑定（如用了 Copy 选项）-> 同样不采信
            elif _mdl is not None and _gp is not None:
                _path_ok = False
                _path_note = ("当前文档 GetPathName 为空（很可能用了 "
                              "SaveAsOptions_Copy=2），已跳过 bbox 回读")
        except Exception as _e_p:
            _path_note = "路径核对异常（不阻断）: %r" % (_e_p,)
        _bbox = None
        if _path_ok:
            try:
                _bbox = model_obj.body_box_mm()
            except Exception:
                _bbox = None
        _res = _pp.write_pr_record(
            target,
            up_axis=_decl.get("up_axis"),
            up_sign=_decl.get("up_sign", 1),
            main_axis=_decl.get("main_axis"),
            base_plane=_decl.get("base_plane"),
            sketch_y_sign=_decl.get("sketch_y_sign"),
            bbox_mm=_bbox,
            source=("explicit" if _decl else "inferred"),
            note=_decl.get("note"))
        out["pr_record"] = {
            "ok": bool(_res.get("ok")),
            "file": _res.get("path"),
            "source": (_res.get("record") or {}).get("source"),
            "inferred": bool(_res.get("inferred")),
            "bbox_read": bool(_path_ok),
            "error": _res.get("error"),
        }
        if _path_note:
            out["pr_record"]["path_note"] = _path_note
        if not _res.get("ok"):
            try:
                model_obj._warn("写 pr.json 朝向声明失败（不阻断保存）: %s"
                                % _res.get("error"))
            except Exception:
                pass
    except Exception as e:
        try:
            out["pr_record"] = {"ok": False, "error": repr(e)}
        except Exception:
            pass
    return out


@contextlib.contextmanager
def batch_mode(sw, label="建模批处理"):
    """【D4 配套】把一串 API 调用包进 ISldWorks::CommandInProgress。

    官方语义（ISldWorks::CommandInProgress，SW 2006 SP2 / Rev 14.2 起）：
      原文："Improves performance of out-of-process applications by informing
      SOLIDWORKS that a sequence of API calls will be made by the out-of-process
      application." / "Set to True before calling the sequence of API calls,
      then set to false after calling the sequence of API calls."
      / "SOLIDWORKS then reduces the number of updates it makes during these
      calls. Use of this property only effects out-of-process applications."
    对我们的价值（回溯报告 §12.2）：
      · 本仓库正是 out-of-process（python 经 COM 驱动 SW），每次特征后 SW 都要
        刷新 UI —— 那既是性能损耗，也是"窗口一直在动、用户容易去点"的诱因；
      · 置 True 期间 SW 减少刷新，建模更快、被人工打断的窗口也更小。
    ⚠️ 它不是"忙检测"、不是重试机制（官方页无 busy/retry 字样）。
      忙/被拒的重试仍由 com_retry 负责。

    用法：
        with swapi.batch_mode(sw):
            m.extrude(10); m.cut(through=True)
    异常安全：finally 里一定复位为 False，绝不把 SW 留在批处理态。
    """
    _ok = False
    try:
        if sw is not None:
            sw.CommandInProgress = True
            _ok = True
    except Exception:
        _ok = False
    try:
        yield _ok
    finally:
        if _ok:
            try:
                sw.CommandInProgress = False
            except Exception:
                pass


def _stop_guard(action=""):
    """【P2/S2 修复】建模动作入口的停机检查。

    与 sw_bridge / mode_gate 共用 _stop 单一事实源。_stop 不可用（例如
    swapi 被单独复制到别处）时静默放行 —— 命令入口闸门仍在 sw_bridge main()。
    """
    try:
        import _stop as _stop_mod
        hit = _stop_mod.guard(action or "modeling")
        if hit:
            raise RuntimeError(
                "TASK_STOPPED: %s —— %s"
                % (action or "modeling",
                   hit.get("error") or "用户已要求停止任务"))
    except RuntimeError:
        raise
    except Exception:
        pass


# == 【v2 回归复测 12.1】本进程创建过的【未命名】文档标题 ================
# 用途见 new_part() 里的"文档身份锁"：只有自己建的未命名文档才允许关。
# 这是一个进程级集合（同一 python 进程内有效）——正是需要的粒度：
#   跨房间的隔离靠的是【不同 python 进程】，同进程内的新建归自己。
_MY_UNNAMED_DOCS = set()


def new_part(sw=None, material=None, density=None):
    """新建零件，返回 SWModel。

    Bug 10 修复: 新建前先关闭所有遗留文档，避免干扰。

    ── 【BUG-05/08 修复】新建时必须赋材质，杜绝"密度恒为 1000（水）" ──────
    原缺陷：new_part 从不赋材质 → SW 用默认密度 1000 kg/m³（水）→
      质量/重心/转动惯量全错、工程图材料栏空白、physics 报告 material 显示 "?"、
      强度与寿命校核基准不可信（"30 年寿命校核"直接失效）。
    修复：新建后立即尝试赋材质（默认 Q235 碳钢，可用 material= 指定）；
      赋材质结果写入 m.material_result，失败时进 warnings 而非静默。
      density= 可强制写入密度（材质库找不到时的兜底）。
    """
    # ══ 【P2/S2 修复】停机开关：新建零件是建模的起点，必须 fail-closed ══
    _stop_guard("new_part")
    if sw is None:
        sw = get_sw()
    _disable_snapping(sw)

    # == [A-105 修复] 只关【无名残留文档】，绝不关别人的已命名文档 ======
    # 原实现（Bug 10 修复）在 new_part 开头循环 50 次
    #   sw.ActiveDoc.CloseDoc(0)
    # —— 无归属判断、无未保存判断、失败一律 except: pass 静默。
    # 实测事故（2026-11-01，共享单 SW 实例 + 多房间并行）：
    #   · 任一房间跑含 new_part() 的脚本（含诊断探针），就会把【其他房间
    #     正在编辑、尚未保存】的文档一起关掉 → 未保存建模直接丢失；
    #   · 这份代码藏在【每个建模脚本的第一行】，是全线最高频的破坏源；
    #   · 日志实证：夹紧机构室诊断探针 new_part() 运行期间，SW 活动文档
    #     连续出现 零件4/5/6/7，至少 3 组 python 建模进程并存，双方互踩
    #     导致 extrude 静默失败（BUG-JG-LS-3 / BUG-MT-1）。
    # 另：CloseDoc(0) 的参数是【错的】—— 真实签名要文档标题，传 0 会让 SW
    #   弹"是否保存"模态框并阻塞 CloseAllDocuments（KI-22 实测）。
    # 正确语义：
    #   · 带文件路径的文档 = 别人（或本脚本之前）的成果 → 【绝不关闭】；
    #   · 无路径的未命名文档（零件1/零件2…）= 本轮被中断留下的垃圾 → 关掉，
    #     但即使关闭失败也只告警，不中断新建。
    # 需要旧行为（单线程独占 SW）时设 DSH_SW_CLOSE_ALL_ON_NEW=1。
    import os as _os_np
    _close_all = str(_os_np.environ.get("DSH_SW_CLOSE_ALL_ON_NEW") or "").strip().lower()
    _keep_named = not (_close_all in ("1", "true", "yes", "on"))
    _closed = []
    _kept = []
    for _ in range(50):
        try:
            _ad = sw.ActiveDoc
        except Exception:
            _ad = None
        if _ad is None:
            break
        _title = ""
        _path = ""
        try:
            _t = getattr(_ad, "GetTitle", None)
            _title = (_t() if callable(_t) else _t) or ""
            _p = getattr(_ad, "GetPathName", None)
            _path = (_p() if callable(_p) else _p) or ""
        except Exception:
            _title, _path = "", ""
        if _keep_named and str(_path).strip():
            # 已命名的文档属于别的房间/别的轮次 —— 保留，不再往下关
            _kept.append(_title or _path)
            break
        # == 【v2 回归复测 12.1 修复】未命名文档也要看【持有者是谁】=====
        # 复测报告 v2 第5节现场：
        #   B 的 new_part 对 A 的【未命名】文档调 CloseDoc，抛
        #   AttributeError('<unknown>.CloseDoc')（被吞掉，只告警）。
        #   A 这次侥幸保存成功，但结构上仍可能关掉别房间未保存的 doc。
        # 判据：只有【本进程自己创建】的未命名文档才可以关。
        #   怎么判断"是不是我建的"：
        #     · 本模块在本进程里记了一个 _MY_UNNAMED_DOCS 集合（见下），
        #       NewDocument 成功后把标题登记进去；
        #     · 不在集合里 = 别人建的（可能是另一个房间正在建模的半成品）
        #       -> 绝不关，记入 _kept 让调用方知道。
        if _title and _title not in _MY_UNNAMED_DOCS:
            _kept.append(_title + "（未命名但非本进程创建）")
            break
        if not _title:
            # ── 【D3 修复】取不到标题就绝不用 "" 调 CloseDoc ──────────────
            # 官方语义：CloseDoc("") = 关掉【当前活动文档】。并发时会关错人。
            break
        try:
            # CloseDoc 必须传标题（传 0 会触发模态"是否保存"框）
            sw.ActiveDoc.CloseDoc(_title)
            _closed.append(_title)
        except Exception as _e_cd:
            try:
                SWModel(sw, _ad)._warn("关闭残留文档失败（不阻断）: %r" % (_e_cd,))
            except Exception:
                pass
            break
    if _kept:
        # 只要还有已命名文档开着，就在新建前明确告知调用方（不静默）
        try:
            print(u"[swapi][WARN] new_part: 检测到 %d 个已命名文档仍在 SW 中打开，按共享会话保护策略【未关闭】它们：%s"
                  % (len(_kept), ", ".join(str(x) for x in _kept[:5])))
        except Exception:
            pass

    tmpl = get_part_template(sw)
    if tmpl is None:
        raise RuntimeError("no part template found (请确认 SolidWorks 已安装)")
    model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
    if model is None:
        raise RuntimeError("NewDocument returned None")
    m = SWModel(sw, model)
    # == 【v2 回归复测 12.1】把自己建的未命名文档登记下来 ==
    # 这样后续 new_part 的清理循环只关【自己的】未命名文档，
    # 不会误关别的房间正在建模的半成品（跨进程时集合为空 = 一律不关）。
    try:
        _t_new = getattr(model, "GetTitle", None)
        _t_new = (_t_new() if callable(_t_new) else _t_new) or ""
        if _t_new:
            _MY_UNNAMED_DOCS.add(str(_t_new))
    except Exception:
        pass
    # ══ 【Bug-40 修复】新零件必须从干净的基准面上下文起步 ═════════════
    # 多零件单脚本场景下，上一个零件的面/草图 COM 上下文会残留在 SW
    #   进程里，导致本零件的第一次 begin_sketch 落到旧面、或首次 cut 失败
    #   （台账 3.1："结构件舵机支架、支撑结构加强筋、壳体上罩首次均失败"）。
    # 修复：new_part 返回前强制重置一次（退出草图 + 清选择 + 重建），
    #   语义等价于"每个零件都在独立脚本里新建"，把台账的变通内化。
    try:
        m._reset_plane_context(None, reason="new_part")
    except Exception:
        pass
    # ── 【BUG-05/08 修复】登记待赋材质（延迟到有实体后真正写入）──────────
    # ⚠️ 实机教训（2026-09-27）：材质【不能】在 new_part 里立即写入 ——
    #   此刻零件还是空的，没有任何 IBody2，set_material 必然报
    #   "零件没有实体（IBody2）"，密度仍停在 1000 kg/m³（水）。
    #   而模型通常写完第一个特征（extrude/revolve）才产生实体，
    #   所以正确做法是：这里只【记住要赋什么】，等第一个实体出现后
    #   由 _try_apply_pending_material() 自动补上。
    #   另：save() 是最后一道保险，若到保存时仍未赋成功会在返回值里显式告警。
    # ── 【Bug-14 修复】显式传参必须最高优先，并记录来源供审计 ────────────
    # 原缺陷：材质来源不透明 —— 调用方无法区分"我显式传的 PETG 生效了"还是
    #   "被 load_case 里的 6061-T6 覆盖了"。实测需要这种可追溯性来排查
    #   "图纸材料 vs 实际材料"不一致。
    # 优先级（与文档一致）：显式 material 参数 > DSH_PART_MATERIAL 环境变量
    #   > gate_load_case.json 材料 > DEFAULT_PART_MATERIAL。
    # ── 【F-05 修复】统一由 resolve_default_material_or_raise 裁定 ──────────
    # 原实现分三路各自写 _pending_material，且【不校验合法性】：
    #   · load_case 路径会把标准文件全名 "GB/T 700-2006 碳素结构钢" 当材料名；
    #   · 默认路径硬塞 DEFAULT_PART_MATERIAL，不管它在不在库里；
    #   · 三条路都【不带密度】—— 这是"读回 1000（水）"的直接成因。
    # 现在：合法性 + 密度解析集中在一处裁决；非法即显式报错，绝不静默降级。
    _mres = resolve_default_material_or_raise(material=material,
                                              density=density)
    if not _mres.get("ok"):
        # 材料身份不可信 → 明确失败（不建出一个"没有材质身份"的零件）
        raise RuntimeError(
            "new_part 无法确定合法的默认材料：\n%s\n%s"
            % (_mres.get("error"), _mres.get("hint") or ""))
    m._pending_material = _mres.get("material")
    m._material_source = _mres.get("source")
    # ★ 密度【随牌号表带出】（Q235 → 7850），不再依赖 SW 库近似值。
    #   显式 density 参数优先（已在 resolve_default_material_or_raise 内处理）。
    m._pending_density = _mres.get("density")
    m._material_canonical = _mres.get("canonical")
    # ── 【观察点 13 修复】单独记录【用户传入的原始材料名】，且永不清空 ──────
    # `_pending_material` 在材质成功写入后会被置 None（见
    # _try_apply_pending_material）。于是 save() 生成凭据时只能回退到从 SW
    # 读回的【映射名】（如 PETG → SW 无此牌号 → 近似成 PET），导致：
    #   · 凭据写 requested_material = "PET"（而非用户要的 "PETG"）；
    #   · `set_custom_material("PETG", ...)` 登记的 PETG 属性对校验【不生效】
    #     （因为校验用的是映射名 PET）；
    #   · 只要映射名不在合法库中，凭据就判 material_invalid —— 用户明明
    #     登记了 PETG 也无济于事。
    # 这里保存一份原始名，供 save() 优先按【用户本意】校验与记录。
    m._requested_material = m._pending_material
    m._requested_material_source = m._material_source
    m.material_result = {"ok": False, "method": None, "pending": True,
                         "material": m._pending_material,
                         "applied_material": None,
                         "source": m._material_source,
                         "note": "待首个实体生成后自动赋材质（BUG-05）"}
    if VISUAL_MODE:
        _show_main_window(maximize=True)
        import time
        m.set_view_iso()
        time.sleep(VISUAL_PAUSE)
    return m


def doc_identity(model):
    """【D3 修复】文档身份三元组：(完整路径, 标题, 文档类型)。

    官方语义依据（ISldWorks / IModelDoc2 API Help）：
      · IModelDoc2::GetPathName — "if the document has not yet been saved,
        then the string is empty"（未保存新零件拿不到路径，只能回退标题）；
      · IModelDoc2::GetTitle — 标题受"是否显示扩展名"设置影响
        （Part1.sldprt vs Part1），故不能单独作为唯一标识；
      · IModelDoc2::GetType — 返回 swDocumentTypes_e，用于消解同名
        sldprt/sldasm 撞车（GetOpenDocument 官方也警告过这点）。
    结论：身份判定必须用【路径优先 + 标题 + 类型】三元组，
      并且【不存在】IModelDoc2::GetID 这种唯一 id（官方成员表里没有），
      所以不要用 GetID 做校验。
    """
    def _call(obj, name, default=None):
        try:
            v = getattr(obj, name, None)
            if v is None:
                return default
            return v() if callable(v) else v
        except Exception:
            return default
    if model is None:
        return (None, None, None)
    return (_call(model, "GetPathName", "") or "",
            _call(model, "GetTitle", "") or "",
            _call(model, "GetType", None))


def same_document(a, b):
    """两个 IModelDoc2 是否指向同一份文档（路径优先，未保存时比标题）。

    【D3 修复】用于"当前 ActiveDoc 到底是不是我要操作的那一个"的判定，
    替代把 ActiveDoc 当身份的做法（ActiveDoc 是进程级全局状态，
    多房间并发时随时被别人切换 —— 那正是零件串写的根因）。
    """
    pa, ta, ty_a = doc_identity(a)
    pb, tb, ty_b = doc_identity(b)
    if a is None or b is None:
        return False
    # ① 双方都有路径 → 比路径（最可靠）
    if pa and pb:
        try:
            return os.path.normcase(os.path.abspath(pa)) ==                    os.path.normcase(os.path.abspath(pb))
        except Exception:
            return str(pa).lower() == str(pb).lower()
    # ② 至少一方未保存 → 比标题（注意扩展名设置会变，做去扩展名容错）
    if not ta or not tb:
        return False

    def _stem(t):
        t = str(t).strip().lower()
        for ext in (".sldprt", ".sldasm", ".slddrw"):
            if t.endswith(ext):
                return t[: -len(ext)]
        return t
    return _stem(ta) == _stem(tb)


def find_open_document(sw, path):
    """【D3 修复】按完整路径查找已打开文档，返回 IModelDoc2 或 None。

    官方语义依据：
      · ISldWorks::GetOpenDocumentByName(DocumentName) —— 参数要求
        "Fully qualified name (path, filename, and file extension)"，失败返回 NULL；
      · 这是【显式句柄】的官方来源之一：它返回的对象与 ActiveDoc 无关，
        不需要文档处于激活状态即可拿到指针。
      · 对照 ISldWorks::OpenDoc6 官方说明："OpenDoc6 does not activate and
        display the document if the file is already open in memory...
        calling ISldWorks::ActiveDoc will not return a pointer to this document."
        —— 即"显式指针"与"ActiveDoc"是两条独立通路，这正是本模块要走的路线。
    """
    if sw is None or not path:
        return None
    _p = str(path)
    try:
        m = sw.GetOpenDocumentByName(_p)
        if m is not None:
            return m
    except Exception:
        pass
    # 退路：遍历已打开文档逐个比路径（GetDocuments 官方返回数组）
    try:
        docs = sw.GetDocuments()
        try:
            docs = list(docs) if docs is not None else []
        except TypeError:
            docs = [docs] if docs is not None else []
        for d in docs:
            _dp, _dt, _ = doc_identity(d)
            if not _dp:
                continue
            try:
                if os.path.normcase(os.path.abspath(_dp)) == os.path.normcase(os.path.abspath(_p)):
                    return d
            except Exception:
                if str(_dp).lower() == _p.lower():
                    return d
    except Exception:
        pass
    return None


def from_active(sw=None):
    """包装当前活动文档为 SWModel。

    ⚠️ 【D3 说明】本函数仍然返回 ActiveDoc —— 它是【进程级全局状态】，
    多房间并发时随时可能被别的房间切走。新代码请优先用：
        from_path(sw, 路径)       # 按路径取显式句柄（推荐）
        get_model(sw, expect=...) # 取 ActiveDoc 并校验身份（带警告）
    这里保留 from_active 仅为向后兼容（单房间/串行场景等价安全）。
    """
    if sw is None:
        sw = get_sw()
    model = sw.ActiveDoc
    if model is None:
        raise RuntimeError("no active document")
    return SWModel(sw, model)


def from_path(sw, path):
    """【D3 修复】按【文件路径】包装一个已打开的文档为 SWModel。

    与 from_active 的区别：不依赖进程级 ActiveDoc，因此多个房间/多个
    建模进程交错执行时，A 的操作不会落到 B 的文档上。
    文档没打开时返回 None（调用方应显式 open_part 后再取）。
    """
    if sw is None:
        sw = get_sw()
    model = find_open_document(sw, path)
    if model is None:
        return None
    return SWModel(sw, model)


def get_model(sw=None, expect=None, strict=False):
    """【D3 修复】取当前活动文档，并【校验它是不是我要的那一个】。

    Args:
        sw:     ISldWorks（可空，空则自动连接）
        expect: 期望的文档路径（str）或 IModelDoc2。
                - 传具体期望 → 只做身份校验；
                - 传 "any" / None → 不校验，直接返回（保持旧语义）。
        strict: True 时身份不符直接抛异常；False 时只记警告
                （warnings 挂在返回的 SWModel 上）。
    Returns:
        SWModel 或 None。
    Raises:
        RuntimeError: strict=True 且身份不符。
    """
    if sw is None:
        sw = get_sw()
    model = sw.ActiveDoc
    if model is None:
        if strict:
            raise RuntimeError("no active document")
        return None
    # ── 【D3 修复·顺序】先校验身份，再构造 SWModel ──────────────────────
    # 原顺序先 SWModel(sw, model) 再比对，导致"身份不符"时先炸在构造里
    #   （模型对象缺 SketchManager 等），真正的身份错误被掩盖。
    _ap, _at, _ = doc_identity(model)
    if expect in (None, "", "any"):
        return SWModel(sw, model)
    _norm = lambda s: os.path.normcase(os.path.abspath(s))
    ok = False
    try:
        if isinstance(expect, str):
            if _ap:
                try:
                    ok = (_norm(_ap) == _norm(expect))
                except Exception:
                    ok = (str(_ap).lower() == str(expect).lower())
            else:
                _exp_doc = find_open_document(sw, expect)
                ok = (_exp_doc is not None and same_document(model, _exp_doc))
        else:
            ok = same_document(model, expect)
    except Exception:
        ok = False
    if ok:
        return SWModel(sw, model)
    msg = ("【D3 文档身份不符】当前 ActiveDoc = %r（路径=%r），"
           "而本次操作期望 %r。ActiveDoc 是进程级全局状态，"
           "多半是别的房间切换了活动文档 —— 继续操作会写到别人的零件上。"
           % (_at, _ap, expect))
    if strict:
        raise RuntimeError(msg)
    m = SWModel(sw, model)
    try:
        m._warn(msg + "（已按非严格模式继续，风险自担）")
    except Exception:
        pass
    return m


# ══ 【Bug-20/24/28 修复】特征创建失败诊断助手 ═══════════════════════════════
# 原缺陷：FeatureCut3/FeatureExtrusion3 失败时，小屋只能"暴力穷举 12 种签名
#   （flip × dir × 贯穿枚举/盲孔）"，全部返回 None 或
#   com_error(-2147352561 '非选择性的参数')，却不知道到底卡在哪一步 ——
#   是选择集为空？签名错？还是草图轮廓不闭合？浪费大量轮次且给不出修复建议。
# 修复：提供统一诊断函数，把失败归因到【可操作】的几类，并给出建议命令。
_DIAG_ADVICE = {
    "no_selection": "选择集为空 —— 草图轮廓没有被选中。"
                    "建议：先 end_sketch()，再用 select_all_sketch_segments() 选段；"
                    "或改用 begin_sketch_on_face() 在实体面上重画轮廓。",
    "sketch_open": "草图轮廓【未闭合】—— 拉伸/切除需要封闭轮廓。"
                   "建议：polyline() 会自动闭合（Bug-26）；手工画线时确认末点回到起点。",
    "not_closed_loop": "轮廓是开放折线或自相交 —— 无法生成实体。"
                       "建议：检查点序，用 circle()/rect() 等封闭图元，"
                       "或让 polyline() 自动补回起点的边。",
    "com_signature": "COM 调用签名不匹配（com_error '非选择性的参数'）—— "
                     "多为 FeatureCut3 参数布局跨版本差异。"
                     "建议：改用高层封装 cut(through=True) / extrude()，"
                     "它们内部已做多签名尝试与体积校验。",
    "feature_failed": "特征创建返回 None —— SW 拒绝该操作。"
                      "建议：检查是否在错误的文档/编辑模式、草图是否已被消费；"
                      "必要时 rebuild() 后重试。",
    "no_body": "零件当前没有实体（IBody2）—— 切除/材质等操作无从作用。"
               "建议：先 extrude/revolve 生成基体，再做切除。",
}


def diagnose_feature_failure(model, exc=None, api=None, extra=None):
    """【Bug-20/24/28】把特征失败归因到可操作的类别，返回诊断结果。

    Args:
        model: SWModel 实例（可为 None）
        exc:   捕获到的异常（com_error / RuntimeError / None）
        api:   尝试调用的 API 名（如 "FeatureCut3"）
        extra: 额外上下文 dict（如 {"selection_count": 0}）
    Returns:
        {"ok": bool, "cause": str, "detail": str, "advice": str, "checks": {...}}
    """
    checks = {}
    cause = "unknown"
    detail = ""

    # ── ① 选择集数量 ────────────────────────────────────────────────
    try:
        if model is not None:
            _n = model.model.SelectionManager.GetSelectedObjectCount2(-1)
            checks["selection_count"] = int(_n)
    except Exception:
        checks["selection_count"] = None
    if extra and "selection_count" in extra:
        checks["selection_count"] = extra["selection_count"]

    # ── ② 实体数量 ──────────────────────────────────────────────────
    try:
        if model is not None:
            _b = model.model.GetBodies2(0, False)
            if _b is None:
                checks["body_count"] = 0
            else:
                try:
                    checks["body_count"] = len(_b)
                except TypeError:
                    checks["body_count"] = 1
    except Exception:
        checks["body_count"] = None

    # ── ③ 草图是否闭合 / 是否处于草图模式 ───────────────────────────
    try:
        if model is not None:
            _sk = model.skm.ActiveSketch
            checks["active_sketch"] = _sk is not None
            if _sk is not None:
                try:
                    checks["sketch_closed"] = bool(_sk.IsClosed())
                except Exception:
                    checks["sketch_closed"] = None
    except Exception:
        checks["active_sketch"] = None

    _emsg = ""
    if exc is not None:
        try:
            _emsg = str(exc)
        except Exception:
            _emsg = repr(exc)
    checks["exception"] = _emsg or None
    checks["api"] = api

    # ── 归因（按可操作性排序）──────────────────────────────────────
    if checks.get("body_count") == 0 and (api or "").startswith("Feature"):
        cause = "no_body"
        detail = "零件无实体，特征操作无从作用"
    elif checks.get("selection_count") == 0:
        cause = "no_selection"
        detail = "选择集为 0，轮廓未被选中"
    elif checks.get("sketch_closed") is False:
        cause = "sketch_open"
        detail = "草图 IsClosed() 返回 False，轮廓未闭合"
    elif "非选择性" in _emsg or "non-selective" in _emsg.lower() \
            or "-2147352561" in _emsg:
        cause = "com_signature"
        detail = "com_error 非选择性的参数（签名/选择集问题）"
    elif _emsg:
        cause = "feature_failed"
        detail = _emsg[:300]
    else:
        cause = "feature_failed"
        detail = "特征 API 返回 None，无异常信息"

    return {
        "ok": True,
        "cause": cause,
        "detail": detail,
        "advice": _DIAG_ADVICE.get(cause, "请检查草图轮廓与选择集后重试。"),
        "checks": checks,
    }


# ══ 【装配定位修复 R2 · 2026-10-31】MathTransform 的 16 元数组必须按【官方布局】══
# 事故背景（2026-10-30 四工位转台 15 小时长跑）：
#   n_planned=51 / n_ok=51 / n_coord_applied=1 —— 51 件全部"插入成功"，
#   只有 1 件真正带坐标；AddComponent5 的 X/Y/Z 被忽略，SetTransform 补救
#   也失败（期望 [0,0,90] 实际 [0,0,10]）→ 764KB 的 .SLDASM 是【假装配】，
#   干涉检查 / 联锁验证 / 12 张工程图全部作废。
#
# 根因①（本文件）：平移矩阵按 .NET 行优先 4x4 排布（平移写在索引 3/7/11），
#   而 SolidWorks 的 IMathTransform.ArrayData 是：
#       0..8   = 旋转子矩阵（3x3，按行铺开）
#       9..11  = 平移 (x, y, z)  ← 米
#       12..15 = (0, 0, 0, 1)
#   于是"写进 SW 的矩阵"和"从 SW 读回的矩阵"双向错位 —— 读回校验靠
#   arr[3]/arr[7]/arr[11] 取位置，在 SW 里恒读到旋转矩阵里的垃圾值，
#   任何组件都会被判成"坐标未生效"，进而触发一堆错误的补救动作。
#
# 权威依据：
#   【类型库】C:\Users\j1877\AppData\Local\Temp\gen_py\3.13\
#            83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
#            · IMathUtility.CreateTransform(ArrayDataIn) / ICreateTransform(ArrayDataIn)
#              —— 只收【一维 16 个 double】，不存在 (Units, IMathArray) 这种两参重载
#            · IComponent2.GetTotalTransform(IncludePresentationXform) -> IMathTransform
#            · IComponent2.Transform2（ArrayData 可读）/ SetXformAndSolve3
#            · IMathTransform.ArrayData / IArrayData / GetData / SetData
#            · IAssemblyDoc.AddComponent5(CompName, ConfigOption, NewConfigName,
#              UseConfigForPartReferences, ExistingConfigName, X, Y, Z) （8 参，类型库确认）
#   【网页】CodeStack《Using transformations in SOLIDWORKS API》
#            https://www.codestack.net/solidworks-api/geometry/transformation/
#            「0-8 - rotational part of the matrix / 9-11 - translation part
#              of matrix (x, y, z)」
#   【网页】CadBooster《Understanding math and MathTransform》
#            https://cadbooster.com/understanding-math-and-mathtransform-in-the-solidworks-api/
#            「0-8 / a-i / rotation submatrix」同结论。
SW_MATRIX_LAYOUT = ("IMathTransform.ArrayData: 0..8=旋转子矩阵, "
                    "9..11=平移(x,y,z), 12..15=(0,0,0,1)")


def _transform_array(mt):
    """取 IMathTransform 的 16 元数组（兼容 ArrayData / IArrayData / 裸序列）。

    【装配定位修复 R2】读数不统一是历史缺陷之一：有的地方读 mt.ArrayData，
    有的地方根本不读。统一收口到本函数，读不到就【明确返回 None】，
    绝不返回一个"看着像位置"的假值。
    """
    if mt is None:
        return None
    data = None
    if hasattr(mt, "ArrayData"):
        try:
            data = mt.ArrayData
        except Exception:
            data = None
    if data is None and hasattr(mt, "IArrayData"):
        try:
            data = mt.IArrayData
        except Exception:
            data = None
    if data is None and isinstance(mt, (list, tuple)):
        data = mt
    if data is None:
        return None
    try:
        arr = [float(v) for v in list(data)]
    except Exception:
        return None
    return arr if len(arr) >= 12 else None


def _translation_matrix_m16(x=0.0, y=0.0, z=0.0, k=0.001):
    """构造【纯平移】MathTransform 的 16 元数组（官方 ArrayData 布局）。

    ── 【装配定位修复 R2】本仓库唯一允许的平移矩阵构造入口 ───────────────
    历史实现写的是 [1,0,0,x, 0,1,0,y, 0,0,1,z, 1,0,0,0]（.NET 行优先 4x4），
    SolidWorks 不认这个排法 —— 见文件上方 SW_MATRIX_LAYOUT 的说明与依据。

    Args:
        x, y, z: 平移量，单位【毫米】（装配脚本的设计坐标口径）
        k: mm→SW 内部单位（米）的换算系数；units="m" 时传 1.0
    Returns:
        list[16]，可直接喂 IMathUtility.CreateTransform / ICreateTransform
    """
    _tx = float(x) * float(k)
    _ty = float(y) * float(k)
    _tz = float(z) * float(k)
    return [1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
            _tx, _ty, _tz,
            0.0, 0.0, 0.0, 1.0]


def _position_from_transform_mm(mt, units="mm", k=None):
    """从 IMathTransform 读出平移分量（mm）。索引【必须】是 9/10/11。

    ── 【装配定位修复 R2】历史缺陷：全仓 4 处读回位置都写 arr[3]/arr[7]/arr[11]
    （.NET 行优先布局），而 SW 的平移在 9/10/11 —— 于是"读回来的位置"永远是
    旋转子矩阵里的 0/1 拼接值（典型 [0, 0, 1]），用它做坐标校验必然失败，
    而且会把【已经放对】的组件误判成没放对（反向假阴性）。

    Args:
        mt: IMathTransform（也接受 16 元数组 / 裸序列）
        units: "mm"（默认，内部按 0.001 换算）或 "m"
        k: 直接给换算系数（优先于 units）
    Returns:
        [x, y, z]（mm）；读不到/数组非法 → None（调用方必须按"未验证"处理）
    """
    if k is None:
        k = 0.001 if str(units).lower() == "mm" else 1.0
    arr = _transform_array(mt)
    if arr is None:
        return None
    try:
        return [arr[9] / k, arr[10] / k, arr[11] / k]
    except Exception:
        return None


def _components_list(comps):
    """把 IAssemblyDoc.GetComponents 的返回值统一成【组件列表】。

    ── 【装配定位修复 R2】踩坑记录 ─────────────────────────────────────
    GetComponents 在 late-binding 下返回 VARIANT 数组（pywin32 解成 tuple），
    但也可能返回单个对象或 list。历史写法
        clist = list(comps) if isinstance(comps, tuple) else ([comps] if comps else [])
    对 list 输入会变成 [[c1, c2]] —— 外层多套一层，后续按名字找不到任何组件，
    表现为"组件明明在，却报未找到"（假阴性）。这里统一收口：
      tuple/list  → 逐个元素
      单对象      -> [对象]
      None/空     -> []
    """
    if not comps:
        return []
    if isinstance(comps, (list, tuple)):
        return [c for c in comps]
    return [comps]


def _rotation_from_transform(mt):
    """从 IMathTransform 读出 3x3 旋转子矩阵（行优先 9 元素）；失败 None。

    【装配定位修复 R2】SW ArrayData 的 0..8 就是旋转子矩阵（按行铺开）。
    用途：本仓库装配定位全是纯平移（没有旋转输入）→ 组件旋转应≈单位阵。
    若偏离单位阵，说明该组件位姿被别的路径改过（PresentationTransform /
    配合求解 / 手工拖动），此时"坐标对得上"也只能算参考，必须报出来。
    """
    arr = _transform_array(mt)
    if arr is None:
        return None
    try:
        return [arr[0], arr[1], arr[2], arr[3], arr[4], arr[5], arr[6], arr[7], arr[8]]
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════
#  【BUG-9 · 装配配合门禁】COM 参数封送工具（纯函数，无 SW 依赖）
# --------------------------------------------------------------------------
#  权威依据：docs/SW2025_AddMate_win32com_技术结论.md（下称"报告"）
#    报告 1  ：AddMate5 第 15 参 ErrorStatus 是 ByRef System.Int32，传裸 0 会
#              被打包成值传递 → DISP_E_TYPEMISMATCH (-2147352571)。
#    报告 3  ：SelectByID2 第 8 参 Callout 传 None 会变成 VT_NULL 被拒，
#              必须 VARIANT(VT_DISPATCH, None)。
#    报告 3b ：GetSelectedObjectCount2 的 Mark：-1=全部、0=只数"没有 mark"
#              的选择 —— 用 Mark=1 选的东西拿 0 去数必然是 0。
#    报告 3  ：MateData.EntitiesToMate 是 Object 变体数组，Python list 不认。
#  这些函数被刻意抽成模块级纯函数：它们不碰 COM，可在无 SolidWorks 的机器上
#  直接单测（见 test_regression_bug9_mate.py），也避免每个调用点各抄一份。
# ══════════════════════════════════════════════════════════════════════════

# swAddMateError_e（报告 1 / 三-7）：成功是 1，不是 0！
SW_ADD_MATE_ERROR_NO_ERROR = 1
SW_ADD_MATE_ERROR_UNKNOWN = 0


def _byref_i4(value=0):
    """构造 ByRef System.Int32 输出参数（给 AddMate5/RunMacro2 等用）。

    报告 1：晚期绑定必须传 VARIANT(VT_BYREF|VT_I4, 0)；传裸 int 等于值传递，
    SW 类型库期待引用 → DISP_E_TYPEMISMATCH(-2147352571)。
    """
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, value)


def _null_dispatch():
    """构造 IDispatch* 空指针（给 SelectByID2 的 Callout / SelectData 用）。

    报告 3：传 Python None 会被打包成 VT_NULL，SW 只认 VT_DISPATCH 空指针。
    """
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def _dispatch_array(objs):
    """把若干 COM 对象打包成 Object 变体数组（给 MateData.EntitiesToMate 用）。

    报告 3：EntitiesToMate 是 System.Object 变体数组，传 Python list 不认；
    必须 SAFEARRAY(VT_DISPATCH)。空列表也返回有类型的空数组，绝不返回 None。
    """
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(objs or ()))


def _byref_value(var, default=None):
    """读回 ByRef VARIANT 的 .value（任何异常都返回 default，绝不抛）。"""
    try:
        return var.value
    except Exception:
        return default


def mate_error_status_text(code):
    """swAddMateError_e 数值 → 可读文本（报告 1 / swAddMateError_e 官方页）。"""
    _legend = {
        0: "swAddMateError_ErrorUnknown（未知错误）",
        1: "swAddMateError_NoError（成功）",
        2: "swAddMateError_IncorrectMateType（配合类型不对）",
        3: "swAddMateError_IncorrectAlignment（对齐方式不对）",
        4: "swAddMateError_IncorrectSelections（选择集不对）",
        5: "swAddMateError_OverDefinedAssembly（过定义）",
        6: "swAddMateError_IncorrectGearRatios（齿轮比不对）",
    }
    try:
        return _legend.get(int(code), "未定义错误码(%r)" % (code,))
    except Exception:
        return "未定义错误码(%r)" % (code,)


def mate_ok_by_error_status(mate, error_status):
    """判一次 AddMate5 是否真的成功。

    报告 三-7：判断成功请看 `Mate2 Is Nothing` 与 `err == NoError(1)` 两个都查
    —— 只拿到对象就宣布成功是本仓库历史上的静默失败源之一。
    这里对 ErrorStatus 采取保守口径：读到 None（没填进去）时降级为"仅看 Mate2"，
    但把 error_status_known=False 的事实回传，供上层如实记录。
    Returns: (ok: bool, error_status_known: bool)
    """
    if mate is None:
        return False, error_status in (SW_ADD_MATE_ERROR_UNKNOWN,
                                       SW_ADD_MATE_ERROR_NO_ERROR)
    if error_status is None:
        return True, False
    try:
        return int(error_status) == SW_ADD_MATE_ERROR_NO_ERROR, True
    except Exception:
        return True, False


# ── 配合类型归一化（create_mate / add_mate / 门禁共用口径）──────────────
# 说明：只有这两个"关键词 → 枚举"的映射会影响本门禁的判定口径，
# 真正的枚举表仍是 SWModel._MATE_TYPE_E（swconst.tlb 实测）。
MATE_TYPE_INT_MIN = 0
MATE_TYPE_INT_MAX = 24          # swMateType_e 现有最大成员 profilecenter=24
MATE_ALIGN_INT_MIN = 0
MATE_ALIGN_INT_MAX = 2          # ALIGNED=0 / ANTI_ALIGNED=1 / CLOSEST=2


def mate_align_is_valid(align):
    """校验 swMateAlign_e 取值合法性（0/1/2）。"""
    try:
        return MATE_ALIGN_INT_MIN <= int(align) <= MATE_ALIGN_INT_MAX
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
#  【BUG-9】配合参考名构造：<实体名>@<零部件实例名>@<装配体名>
# --------------------------------------------------------------------------
#  报告 3：装配体里 SelectByID2 的 Name 必须是这个三段式，且实例名带 -1/-2
#  后缀（不是零件文件名）。现场脚本经常只给"零件名"或"实体名"，直接拼进去
#  必然选不中 → 选择集 0 个 → 上层只看到"配合建不起来"。
#  这两个纯函数就是那层"补全 + 校验"，可独立单测。
# ══════════════════════════════════════════════════════════════════════════

def mate_entity_name(entity, component=None, assembly=None):
    """构造装配体 SelectByID2 用的实体名 `<实体>@<实例>@<装配体>`。

    Args:
        entity: 实体名（面名/基准面名，如 "Face2" / "Front Plane"）。
        component: 零部件实例名（"DSH_底座-1"）或装配体名之一段。
        assembly: 装配体名（可省略 → 退化成两段式，SW 亦接受）。
    Returns: str（去空白；缺 entity 时返回空串，让调用方判空而不是传 "None@"）。
    """
    _e = str(entity or "").strip()
    if not _e:
        return ""
    _parts = [_e]
    _c = str(component or "").strip()
    if _c:
        _parts.append(_c)
        _a = str(assembly or "").strip()
        if _a:
            _parts.append(_a)
    return "@".join(_parts)


def mate_name_segments(name):
    """把 `<实体>@<实例>@<装配体>` 拆成段列表（不合法时返回 []）。

    用途：门禁与诊断回显"这个名字到底几段"，帮助判断是否漏了实例名段。
    """
    _n = str(name or "").strip()
    if not _n:
        return []
    return [s for s in _n.split("@")]


def mate_name_is_qualified(name):
    """名字是否是装配体可用的"合格"配合参考名（报告 3）。

    合格 = 至少两段（实体@实例），且每段非空。
    单段名（"Face2" / "DSH_底座"）在装配体里【不保证】选得中 —— 门禁据此
    给出可操作提示，而不是等到选择集为 0 才一头雾水。
    """
    _segs = mate_name_segments(name)
    if len(_segs) < 2:
        return False
    return all(bool(s.strip()) for s in _segs)


class SWModel:
    """单个模型文档的高层封装。"""

    def __init__(self, sw, model):
        self.sw = sw
        self.model = model
        self.skm = model.SketchManager
        self.fm = model.FeatureManager
        self.ext = model.Extension
        self._empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        # 曲面草图偏移量（mm）：在圆柱侧面选面时，选低z点激活草图，后续画图Y坐标需偏移
        self._surface_sketch_offset = 0.0
        # Right Plane 穿透孔模式：后半侧圆柱面无法直接选面，改用 Right Plane 草图
        self._right_plane_mode = False
        # 【Bug1 加固】非致命告警收集器：吞错改为"记录 + 打印"，
        # 建模流程可继续，但调用方能从 warnings 里看到隐患（避免静默失败）。
        self.warnings = []
        # ══ 【Bug T1/T2 修复】草图状态追踪 ═══════════════════════════════
        # _active_sketch_name: begin_sketch / begin_sketch_on_face 成功激活的
        #   草图名。end_sketch 退出后 SW 会清空选择与 ActiveSketch，此后
        #   cut() 若只靠"重新探测最后草图"，在同零件多次 cut（T1）或
        #   复杂轮廓（T3）时必然拿错/拿不到轮廓段 —— 用显式名字消除歧义。
        self._active_sketch_name = None
        # _last_cut_feature_name: 最近一次成功 cut 所在的草图名。
        #   该草图已被"消费"（轮廓已转成切除特征），后续 cut 绝不能再
        #   回落到它 —— 这是 T1（第二次 cut 必失败）的直接根因。
        self._last_cut_feature_name = None
        # 已被特征消费的草图名集合（防沿用旧轮廓，T1 代码级强制）
        self._consumed_sketches = set()
        # 【Bug T1 修复·验收判据】当前草图的封闭轮廓面积记录（mm²）。
        # 由 circle()/rect() 画图时累加 —— 比事后从 COM 读草图几何可靠
        # （不依赖 GetType 枚举值，跨 SW 版本一致）。cut() 用它计算
        # 期望去料体积 = 面积 × 贯穿长度/深度。
        self._sketch_area_mm2 = 0.0
        # 面积记录所属草图名：画图入口检测到"换了草图"时自动清零面积，
        # 这是防跨草图累积的唯一收口点（begin_* 的 return 分支太多，必漏）。
        self._area_sketch_name = None
        # 【底面切除迁移】草图平面在法向轴上的位置（mm）：Z 轴草图=z，
        # X/Y 轴草图=0（暂不支持迁移，仅 Z 向）。
        self._sketch_axis_offset_mm = 0.0
        # 草图法向轴索引（0=X, 1=Y, 2=Z）：begin_sketch 按基准面名设定，
        # begin_sketch_on_face 按所选面法向设定。cut() 用它取包围盒在
        # 贯穿方向上的真实长度作为 H（实测：包围盒最大边会把直径 60
        # 当成长度 40 用，期望体积虚高 1.5 倍导致完美切除被误拒）。
        self._sketch_normal_axis = 2
        # ── 【BUG-05/08 修复】待赋材质（由 new_part 登记，首个实体出现后写入）
        self._pending_material = None
        self._pending_density = None
        self.material_result = None
        # ══ 【Bug-40 修复】基准面上下文追踪 ═══════════════════════════
        # 原缺陷：多零件单脚本跨基准面（Top→Front/Right）后，或基体已叠加
        #   多个特征再 cut 时，SW 内部仍持有【上一次基准面/草图的 COM 选择
        #   上下文】，导致后续 InsertSketch 落到错的面、FeatureCut3 返回 None
        #   或抛 com_error（结构件舵机支架/支撑结构加强筋/壳体上罩首次均失败）。
        # 修复：显式记录"上次开草图的基准面"与"本零件已开草图代数"，
        #   在跨面或重置点强制清上下文（见 _reset_plane_context）。
        self._last_begin_plane = None
        self._plane_generation = 0
        self._sketch_open_count = 0
        # ══ 【F-04 修复】end_sketch 的轮廓预选证据（诊断用，不参与判定）══
        # end_sketch 退出前会在激活态预选草图段（Bug T2 的修复，见 :7609）。
        # extrude 需要的轮廓是【草图特征本身】而非段，所以它不复用这份预选
        #   （_reset_selection_state 清选择后由 _select_extrude_profile 按名重选）。
        # 这里只把"预选过几段"记成可观测事实：若 extrude 仍然失败，
        #   这条记录能立刻区分"草图根本没闭合/没段"与"选择被清掉了"。
        self._end_sketch_preselect = None
        # 最近一次 extrude 实际使用的轮廓草图名（诊断/测试可断言）
        self._extrude_profile_used = None

    def early(self, iface_name="IModelDoc2"):
        """把 self.model 包装成【早期绑定接口】对象（失败原样返回 self.model）。

        供 B/C 子任务使用（来源：报告 1.1 / 1.2）：
            im = m.early("IModelDoc2")      # 之后可正常调 GetFeatureCount()
            asm = m.early("IAssemblyDoc")   # 装配配合必须走这个
        为什么是方法而不是属性：包装要能失败回退，调用方需要知道拿到的是
        "可能没绑定成功"的对象 —— 回退时就是原 model，旧代码照旧可用。
        实现完全委托给模块级 early()，不在 SWModel 里重复一套缓存逻辑。
        """
        try:
            return early(getattr(self, "model", None), iface_name)
        except Exception:
            # 极端情况下连 early() 本身都异常：必须回退，不能中断建模
            return getattr(self, "model", None)

    def _has_solid_body(self):
        """零件当前是否已有实体（IBody2）。材质必须等到这时才能写入。"""
        try:
            _b = self.model.GetBodies2(0, False)   # 0 = swSolidBody
            if not _b:
                return False
            try:
                return len(_b) > 0
            except TypeError:
                return True          # 单个 body 对象（不可 len）
        except Exception:
            return False

    def _try_apply_pending_material(self, force=False):
        """【BUG-05/08 修复】实体出现后自动补赋材质。

        实机教训（2026-09-27 真实流程暴露）：new_part 阶段零件是空的，
        没有 IBody2 → set_material 必然报"零件没有实体" → 密度停在 1000（水）。
        因此把赋材质【延迟】到"第一个实体生成之后"。
        每个会产出实体的特征（extrude/revolve）末尾调用本方法，
        成功一次即清空 pending，不重复写 COM。

        Args:
            force: True 时即使无 pending 也再校验一次（供 save 阶段兜底）
        Returns:
            dict|None —— 实际执行了赋值时返回 set_material 的结果
        """
        name = self._pending_material
        if not name and not force:
            return None
        if not force and self.material_result and self.material_result.get("ok"):
            return None
        if not self._has_solid_body():
            return None
        try:
            res = self.set_material(name, density=self._pending_density)
            # ══ 【F-05 修复】SW 内置库写不进去 → 走【自定义材质登记路径】══════
            # 事故：new_part(material="Q235") 正常建模路径必然失败。
            #   SW 官方材质库【不收录 GB 牌号】，set_material 走遍
            #   (库 × 候选名) 仍写不进 → 读回密度掉回 1000（水）→ 防线拦下。
            #   唯一跑得通的路径是手工 set_custom_material(...) 登记。
            # 现在把它固化进默认链路：内置库失败后【自动】按
            #   custom_materials.json + 牌号表属性登记并重写，
            #   属性（E/σy/σb/ρ）取自 resolve_material_props（Q235→7850）。
            #   —— 这不是"降级成别的材料"，而是"用同一个牌号换一条写入通道"。
            if not res.get("ok"):
                _cf = self._apply_material_via_custom_registry(
                    name, density=self._pending_density)
                if _cf and _cf.get("ok"):
                    # 保留内置库失败的证据，便于审计"为什么走了自定义通道"
                    _cf["sw_library_attempt"] = {
                        "ok": False,
                        "error": (res.get("error") or "")[:400],
                        "method": res.get("method"),
                    }
                    res = _cf
            # == [A-25 修复] 不得用【失败】结果覆盖已成功的 material_result ==
            # 原实现无条件 self.material_result = res。实测（2026-11-01）：
            #   [r4] set_custom_material: ok=True（40Cr / ρ7820 已写入）
            #   [r4] sidecar: ok=False density=7849.99
            #   AssertionError: material ok != True
            # 链路：save() 内部会【再跑一次】_try_apply_pending_material(force)
            #   （见 save 的"材质兜底赋值"段），这次 SW 库候选全部 ρ=1000 被拒
            #   -> res.ok=False -> 把已经签好 ok=True/ρ7820 的结论覆盖成
            #   "水"记录 -> 侧车 ok=false -> 防线①拒收 -> 小屋删件重建死循环。
            # 语义：material_result 是"这个零件当前的材质事实"，
            #   一次【未成功】的尝试不应该降级已有的事实；但成功的结果
            #   永远可以覆盖（例如换了材料）。
            _prev_mr = self.material_result if isinstance(self.material_result, dict) else {}
            if res.get("ok") or not _prev_mr.get("ok"):
                self.material_result = res
            else:
                # 保留成功结论，但把这次失败尝试留痕（可审计，不静默）
                _keep = dict(_prev_mr)
                _try = _keep.get("rejected_attempts")
                if not isinstance(_try, list):
                    _try = []
                _try.append({
                    "ok": False,
                    "requested": name,
                    "error": (res.get("error") or "")[:400],
                    "method": res.get("method"),
                    "note": "本次重新赋材失败，已保留先前成功结论（A-25）",
                })
                _keep["rejected_attempts"] = _try[-10:]
                self.material_result = _keep
                res = _keep
            if res.get("ok"):
                self._pending_material = None
                self._pending_density = None
            else:
                self._warn("赋材质失败(%s): %s"
                           % (name, res.get("error") or res.get("method")))
            return res
        except Exception as e:
            self.material_result = {"ok": False, "error": repr(e)}
            self._warn("赋材质异常(%s): %r" % (name, e))
            return self.material_result

    def _apply_material_via_custom_registry(self, name, density=None):
        """【F-05 新增】用【自定义材质登记路径】写入材料（GB 牌号的正规出路）。

        ══ 为什么需要它 ══════════════════════════════════════════════════════
        SolidWorks 官方材质库（SolidWorks Materials / DIN Materials）
        【不收录 GB/T 牌号】（Q235 / 45# / 40Cr …），这是库的事实，不是
        调用方式错了。所以对国标牌号，唯一能既保住"图纸牌号"又保住
        "零件里真的是这个材料"的路径是：
          ① 把该牌号的属性登记进 custom_materials.json（可审计、可复用）；
          ② 用 SetMaterialPropertyName2 把【该牌号名 + 密度】写进零件。
        本方法就是这么做的，并且和 set_custom_material 共用同一份属性来源
        （resolve_material_props），保证"台账 / SW / 凭据"三处口径一致。

        Args:
            name:    材料牌号（如 "Q235"）
            density: 显式密度；None 则取牌号表（Q235 → 7850）
        Returns: dict（形如 set_material 的返回体）
        """
        out = {"ok": False, "method": None, "material": name,
               "warnings": [], "error": None}
        nm = str(name or "").strip()
        if not nm:
            out["error"] = "材料名为空"
            return out
        _ok, _info, _why = is_valid_material(nm)
        if not _ok:
            out["error"] = "材料 %r 非法（%s）—— 不登记、不写入" % (nm, _why)
            out["hint"] = _gb_material_advice(nm)
            return out
        props = resolve_material_props(nm) or {}
        _canon = (_info or {}).get("canonical") or props.get("name") or nm
        _d = density
        if _d is None:
            _d = ((_info or {}).get("density")
                  or (_info or {}).get("density_kg_m3")
                  or props.get("density_kg_m3"))
        _e = props.get("e_mpa") or (_info or {}).get("e_mpa")
        _y = props.get("yield_mpa") or (_info or {}).get("yield_mpa")
        _u = props.get("uts_mpa") or (_info or {}).get("uts_mpa")
        _nu = props.get("poissons_ratio") or (_info or {}).get("poissons_ratio")
        # ① 登记（幂等）—— 让材料身份可审计、可复用
        try:
            reg = save_custom_material(nm, e_mpa=_e, yield_mpa=_y, uts_mpa=_u,
                                       density_kg_m3=_d, poissons_ratio=_nu,
                                       note="F-05: new_part 默认链路自动登记"
                                            "（SW 官方库不收录 GB 牌号）")
            out["registry"] = reg
        except Exception as _e_reg:
            out.setdefault("warnings", []).append("登记自定义材料失败: %r" % (_e_reg,))
        # ② 写入 SW（先把密度打头试，让"读回 1000"尽快消失）
        try:
            _wr = self.set_material(nm, density=(float(_d) if _d else None))
        except Exception as _e_w:
            _wr = {"ok": False, "error": repr(_e_w)}
        if _wr.get("ok"):
            out.update(_wr)
            out["method"] = "custom_registry(%s)" % (_wr.get("method") or "?")
            out["custom_material"] = True
            out["density_kg_m3"] = _wr.get("density_kg_m3") or _d
            out["applied_name"] = _wr.get("applied_name") or nm
            out["canonical"] = _canon
            return out
        # ③ 名字写不进 SW → 如实失败，但把【已完成的登记】和【真实原因】交出去
        # ── 【F-05 · 类型库核查结论】SolidWorks 没有"直接写密度"的 API ──────
        # 核查（本机类型库 83A33D31-…x0x33x0.py）：
        #   · IPartDoc.SetMaterialPropertyName2(ConfigName, Database, Name) —— 3 参
        #     只能按【材质库里的材料名】写，没有密度参数（L47899）；
        #   · IPartDoc.MaterialPropertyValues 是【外观颜色】9 元组，不是密度
        #     （L47917，可写但语义是可视属性）；
        #   · IMassProperty.Density / IMassProperty2.Density 都是【只读】
        #     （L30525 / L30591 只出现在 _prop_map_get_，_prop_map_put_ 里
        #      没有任何 Density 条目）；
        #   · IMassProperty.SetOverrideMassValue 覆盖的是【质量】而非密度
        #     （L30650），用它假造密度会让质心/惯量一起错，不可接受。
        # ⇒ 密度只能【跟随材料身份】进入 SW。所以本方法不谎称"写了密度"：
        #   · 登记成功 + 命名失败 → ok=False，但明确告知"牌号已登记、可审计"；
        #   · 同时把牌号表密度原样带出（density_kg_m3），供 physics / 凭据使用。
        out["ok"] = False
        out["custom_material"] = True
        out["canonical"] = _canon
        out["applied_name"] = nm
        out["density_kg_m3"] = _d
        out["density_source"] = "COMMON_MATERIALS/牌号表（未写入 SW）"
        out["sw_internal_material"] = None
        out["sw_internal_matches_request"] = None
        out["error"] = (
            "SW 材质库无法按名写入 %r（%s）。\n"
            "【已完成的处置】已把该牌号登记进 custom_materials.json"
            "（E=%s / σy=%s / σb=%s / ρ=%s），材料身份可审计、可复用；"
            "但 .sldprt 内部的密度/材质仍是 SW 库里的默认值。\n"
            "【为什么不能只写密度】SolidWorks 没有「直接设置密度」的 API"
            "（类型库：IMassProperty2.Density 只读，无 put；"
            "IPartDoc.SetMaterialPropertyName2 无密度参数）—— "
            "密度只能跟随材料身份进入 SW。"
            % (nm, (_wr.get("error") or "SW 库无此牌号"),
               _e, _y, _u, _d))
        out["hint"] = _gb_material_advice(nm)
        return out

    def _warn(self, msg):
        """记录一条非致命告警（不抛异常，不影响建模流程）。

        用途：替换原先的 `except Exception: pass` 静默吞错 ——
        把"失败被无声吞掉"变成"失败被记录并可见"，供调用方与验收环节复核。
        """
        entry = {"msg": str(msg), "ts": time.time() if "time" in globals() else None}
        self.warnings.append(entry)
        try:
            print("[swapi][WARN] " + str(msg))
        except Exception:
            pass
        return entry

    def _migrate_cut_to_opposite_face(self, cx, cy, z_opp, circles):
        """【底面切除迁移】在 z=z_opp 的对侧面重开草图并重画同位圆轮廓。

        仅支持 Z 向草图（机械件底面孔位场景）。局部 (cx,cy) 在对侧 Z 向
        面上与全局 (x,y) 同向（_normal_to("*Front") 保证视角一致）。
        """
        _ = self.begin_sketch_on_face(cx, cy, z_opp)
        for (lx, ly, lr) in circles:
            self.circle(lx, ly, lr)
        return self

    def _make_select_data(self, mark=0):
        """【测试部反馈修复】创建 SelectionMgr.SelectData 对象。

        SW2025 下 ISketchSegment/Feature/Component 的 Select4(AppendFlag, Data)
        的 Data 参数【必须】是 SelectionMgr.CreateSelectData() 创建的对象，
        传 None 抛 com_error -2147352561 '非选择性的参数'。
        全模块所有 Select4 调用统一走本辅助。
        """
        try:
            sd = self.model.SelectionManager.CreateSelectData()
            try:
                sd.Mark = int(mark)
            except Exception:
                pass
            return sd
        except Exception:
            return None

    def _ensure_area_context(self):
        """【Bug T1 修复】画图入口调用：若当前激活草图已切换，则重置面积记录。

        防止上一草图的轮廓面积累积进本次切除的期望体积
        （实测：452.4 + 78.5 = 530.9 mm² 串联导致期望虚高 6 倍）。
        """
        try:
            sk = self.skm.ActiveSketch
            if sk is not None:
                nm = None
                for _how in (lambda: sk.Name, lambda: sk.GetName()):
                    try:
                        v = _how()
                        if v:
                            nm = str(v)
                            break
                    except Exception:
                        continue
                if nm and nm != self._area_sketch_name:
                    self._area_sketch_name = nm
                    self._sketch_area_mm2 = 0.0
        except Exception:
            pass
        return self

    def _remember_sketch_name(self):
        """【Bug T1/T2 修复】记录当前激活草图的名字（失败返回 None）。

        在 begin_sketch / begin_sketch_on_face 成功激活草图后调用，
        为 cut() 提供【显式、无歧义】的轮廓来源，不再依赖"特征树里
        最后一个 ProfileFeature"这种在多次 cut 后必然指错的启发式。
        """
        try:
            sk = self.skm.ActiveSketch
            if sk is not None:
                nm = None
                for _how in (lambda: sk.Name, lambda: sk.GetName()):
                    try:
                        v = _how()
                        if v:
                            nm = str(v)
                            break
                    except Exception:
                        continue
                if nm:
                    self._active_sketch_name = nm
                    # 【Bug T1 修复】每次成功激活新草图，重置轮廓面积记录
                    # （放在这里覆盖 begin_sketch / begin_sketch_on_face
                    #   的所有成功分支，避免漏分支导致面积累积）
                    self._sketch_area_mm2 = 0.0
                    self._sketch_circles_local = []
                    self._sketch_normal_axis = 2  # 默认 Z 向（法向轴）
                    return nm
        except Exception:
            pass
        return None

    def _reset_plane_context(self, plane=None, reason=""):
        """【Bug-40 修复】强制重置 SW 的基准面/草图 COM 上下文。

        为什么需要（台账 3.1 Bug-40）：
          多零件单脚本里跨基准面（Top→Front/Right）后文档切换，或基体建好后
          叠加多个特征再 cut 时，SW 内部仍持有上一次的"面选择 + 草图激活"
          上下文。表现：
            · begin_sketch 落到【上一个基准面】而不是指定的面；
            · cut 时轮廓来自旧草图/错误面 → FeatureCut3 返回 None；
            · 或直接抛 com_error（-2147352561 非选择性的参数）。
          实测：结构件舵机支架、支撑结构加强筋、壳体上罩首次均失败，
          只能靠"每零件独立脚本 + 单次拉伸基体"变通绕过。

        本方法做三件事（幂等、可重复调用）：
          1) 退出任何激活中的草图编辑态（InsertSketch(True) 收尾）；
          2) 清空选择集（ClearSelection2(True)）并重建模型刷新 COM 缓存；
          3) 记录新的基准面上下文代数，供诊断输出。

        Args:
            plane: 即将使用的基准面名（仅用于记录/日志，可选）
            reason: 重置原因（写入 warnings，便于事后定位是哪个环节触发）
        Returns:
            self（链式调用）
        """
        try:
            # 1) 退出残留的草图编辑态（toggle 语义：True=结束当前草图）
            try:
                if self.skm.ActiveSketch is not None:
                    self.skm.InsertSketch(True)
            except Exception:
                pass
            # 2) 清选择集 + 重建，让 SW 丢弃缓存的选择/面引用
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
            try:
                self.rebuild()
            except Exception:
                pass
            # 3) 更新上下文代数（诊断用）
            self._plane_generation = int(getattr(self, "_plane_generation", 0)) + 1
            if plane is not None:
                self._last_begin_plane = str(plane)
        except Exception as _e:
            try:
                self._warn("_reset_plane_context 异常（已忽略）: %r" % (_e,))
            except Exception:
                pass
        return self

    def _finalize_new_sketch(self, plane="Front Plane"):
        """【Bug T3 修复】草图画布激活后的统一收尾：记录草图名 + 法向轴。

        背景（真机实测根因）：
          begin_sketch() 有【多个成功返回分支】—— 主路径(按名选基准面)一处，
          以及两套降级路径(按轴向外扩的包围盒选面 / 固定坐标射线拾取)共 4 处。
          原实现只在主路径调用 _remember_sketch_name()，降级路径直接 return，
          导致 _active_sketch_name 恒为 None。
          后果：revolve() 的"路径②按显式草图名 EditSketch 后选段"必然失效
          （_cand=None 直接跳过），复杂轮廓 revolve 100% 失败（T3）；
          cut() 的同类按名重选路径也一并失效。

        实测证据（SW2025 SP5.0）：
          · begin_sketch 后 ActiveSketch 有效、sk.Name='草图8' 可读；
          · 但 _active_sketch_name 仍为 None → 说明走的不是主路径；
          · 同级 _warn("按名选择基准面失败，降级到实体面策略") 亦印证降级。

        修复：把"记录草图名 + 重置面积/圆记录 + 设定法向轴"抽成本方法，
        供【所有】成功分支统一调用，杜绝漏分支。
        """
        try:
            self._remember_sketch_name()
        except Exception:
            pass
        try:
            self._sketch_area_mm2 = 0.0
        except Exception:
            pass
        try:
            self._sketch_circles_local = []
        except Exception:
            pass
        # 法向轴：Front/Back→Z(2)，Top/Bottom→Y(1)，Right/Left→X(0)
        try:
            _pl = str(plane)
            self._sketch_normal_axis = (
                1 if ("Top" in _pl or "Bottom" in _pl or "俯" in _pl)
                else 0 if ("Right" in _pl or "Left" in _pl or "右" in _pl or "左" in _pl)
                else 2)
        except Exception:
            self._sketch_normal_axis = 2
        return self

    def _mark_sketch_consumed(self):
        """【Bug T1 修复】把当前草图标记为"已被特征消费"。

        调用时机：cut/extrude 成功把轮廓转成特征之后。
        效果：该草图名进入 _consumed_sketches 集合，后续任何"重新选中
        轮廓"的尝试都会跳过它 —— 杜绝"沿用上一次 cut 的轮廓"。
        """
        nm = self._active_sketch_name
        if nm:
            self._last_cut_feature_name = nm
            try:
                self._consumed_sketches.add(nm)
            except Exception:
                pass
            self._active_sketch_name = None

    def _try_edit_sketch_by_name(self, sk_name):
        """【Bug T1/T2 修复】按名进入草图编辑态并选中其全部轮廓段。

        【T2 实测修正】若该草图已处于编辑态，绝不能再 EditSketch ——
        那会把它【切换/退出】（InsertSketch/EditSketch 均为 toggle 语义），
        直接选段即可。

        Returns: (ok, selected_count)
        """
        # 已激活且同名 → 直接选段，不做任何 toggle
        try:
            _cur = self.skm.ActiveSketch
            if _cur is not None:
                _cur_name = None
                for _how in (lambda: _cur.Name, lambda: _cur.GetName()):
                    try:
                        _v = _how()
                        if _v:
                            _cur_name = str(_v)
                            break
                    except Exception:
                        continue
                if _cur_name and str(_cur_name) == str(sk_name):
                    ok, n = self.select_all_sketch_segments()
                    return bool(ok), int(n or 0)
        except Exception:
            pass
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        try:
            if not self.ext.SelectByID2(sk_name, "SKETCH", 0, 0, 0,
                                        False, 0, self._empty, 0):
                return False, 0
        except Exception:
            return False, 0
        try:
            self.model.EditSketch()
        except Exception:
            return False, 0
        try:
            if self.skm.ActiveSketch is None:
                return False, 0
        except Exception:
            return False, 0
        try:
            ok, n = self.select_all_sketch_segments()
            try:
                print(u"[swapi] EditSketch@%s -> 选段=%s ok=%s"
                      % (sk_name, n, bool(ok)))
            except Exception:
                pass
            return bool(ok), int(n or 0)
        except Exception:
            return False, 0

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

        ── 【BUG-E 修复】SaveAs 返回值双向不可靠，必须以磁盘为真相 ──────────
        测试反馈：SaveAs2/SaveAs3 的返回值【两个方向都不可信】——
          · 目标文件【已存在】时，可能返回 True 却【静默不写】（旧文件原样留着）；
          · 也可能写入成功却返回非 0 错误码。
        后果：上层看到 ok=True 就继续走，实际磁盘上还是旧模型 ——
          属于最危险的"静默失败"（表面成功、实则无效）。

        修复：不再依赖返回码判定，改为【以磁盘为唯一真相】：
          1) 记录保存前的大小 + mtime（文件不存在则记 None）
          2) 调用 SaveAs3
          3) 校验：文件存在 且 (大小变化 或 mtime 变化 或 内容哈希变化)
          4) 对"目标已存在且未变化"的情况【明确报失败】并给出原因，
             绝不静默返回成功。

        Returns: {ok, path, saved, updated, size, rc, error?, hint?}
        """
        # ══ 【P2/S2 修复】停机开关：保存是落盘的最后一步，必须 fail-closed ══
        _stop_guard("save")
        def _fingerprint(p):
            """返回文件指纹 (size, mtime, md5)。不存在返回 None。

            用三元组而非仅 (size, mtime)：mtime 在某些文件系统精度不足，
            同秒内重写会被误判为"未变化"；md5 作为最终裁决。
            """
            try:
                if not os.path.exists(p):
                    return None
                st = os.stat(p)
                import hashlib as _hl
                h = _hl.md5()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                return (st.st_size, st.st_mtime, h.hexdigest())
            except Exception:
                return None

        target = self.path if path is None else path
        # ── 【BUG-05/08 修复】保存前最后一道材质兜底 ────────────────────
        # 若因任何原因（建模路径未触发特征钩子、手工 from_active 等）
        # 材质仍未赋成功，则在此强制补一次。补不上就在返回值里显式告警，
        # 绝不让"密度=1000(水)"的零件静默保存出去。
        _mat_warn = None
        try:
            if self._pending_material or not (
                    self.material_result and self.material_result.get("ok")):
                _r = self._try_apply_pending_material(force=True)
                if _r is None and self._pending_material:
                    _mat_warn = ("零件已有实体但材质仍未写入：%s"
                                 % self._pending_material)
        except Exception as e:
            _mat_warn = "材质兜底赋值异常: %r" % (e,)
        before = _fingerprint(target)
        rc = None
        # == 【P1-6 关键·本机实测修正】SaveAs3 的 Options 必须用 1(Silent) ==
        # 子代理在 SW2025 rev 33.5.0.53 上实测四组 Options：
        #   Options=2（swSaveAsOptions_Copy）：rc=0、文件确实落盘，
        #     但 model.GetPathName 保持空串、GetTitle 仍是"零件N"
        #     -- 文档并没有绑定到新路径，后续任何"按路径回读"都会错位；
        #   Options=0/1/4：GetPathName 正常更新。
        #
        # 为什么这对本模块是致命的（不是洁癖）：
        #   save() 成功之后紧接着要写 .material.json / .origin.json /
        #   .pr.json 三份旁挂文件，还要调 body_box_mm() 回读真实几何。
        #   若文档没绑定到 target，self.path 仍指向旧位置 --
        #   于是 pr.json 的 bbox_mm 可能取自另一个文档，
        #   而 .pr.json 声明的 part 字段与实际几何对不上。
        #   这类"看着写成功了、其实张冠李戴"的缺陷最难查。
        # 因此改用 Options=1（Silent：不弹"是否覆盖"对话框，但正常绑定路径）。
        try:
            rc = self.model.SaveAs3(target, 0, 1)
        except Exception as e:
            return {"ok": False, "path": target, "saved": False, "updated": False,
                    "rc": rc, "error": "SaveAs3 调用异常: %r" % (e,)}

        after = _fingerprint(target)

        if after is None:
            _bad = {"ok": False, "path": target, "saved": False, "updated": False,
                    "rc": rc,
                    "error": "SaveAs3 返回后目标文件【不存在】，保存实际未生效。",
                    "hint": "检查路径是否可写、目录是否存在、文件名是否被 SW 接受。"}
            _reject_failed_part(target, _bad, stage="save")
            return _bad

        # ── 【BUG-E 判定修正·第十一轮】专家结论：这不是 bug，是判定标准错 ──
        # 专家指出："重复保存 md5 必须一致"这个断言本身就是错的 ——
        #   文件内容确实可能有微小变化（保存时间戳、内部 ID 等），
        #   所以 md5 变化【恰恰证明真的写入了新内容】，属于正常行为。
        #
        # 因此判定语义应修正为【区分三种情形】，而不是简单的"变没变"：
        #   ① 内容变了（md5 不同）    -> 真的写入新内容，正常成功；
        #   ② 只有 mtime 变了         -> SW 重写了文件但内容一致
        #                                （无修改时重复保存的常见结果），
        #                                也算成功，但标注 content_same=True；
        #   ③ 完全没变（含 mtime）     -> 才是可疑的"静默忽略"。
        #
        # 注意：不再把"md5 变化"当作异常。之前把 ② 也判成失败是误报。
        _before_md5 = before[2] if before else None
        _after_md5 = after[2]
        _content_changed = (before is None) or (_after_md5 != _before_md5)
        _touched = (before is None) or (after[:2] != before[:2])

        if not _touched and not _content_changed:
            # ── 情形 ③：连 mtime 都没变 —— 这才是真正可疑的静默忽略 ──
            _bad = {"ok": False, "path": target, "saved": True, "updated": False,
                    "content_same": True, "rc": rc, "size": after[0],
                    "error": ("SaveAs3 未抛异常，但目标文件【完全未变化】"
                              "（大小/mtime/md5 三者全同）—— 保存被静默忽略。"),
                    "hint": ("请确认该文件未被其他程序/会话占用；"
                             "同名零件若已在 SW 中打开，建议先 close-all 再保存。")}
            # 静默忽略 = 零件没真的更新 → 当作失败件处理（别让它躺在 parts/）
            _reject_failed_part(target, _bad, stage="save-silent-ignore")
            return _bad

        # ── 情形 ① / ② 都算成功 ──
        _out = {"ok": True, "path": target, "saved": True,
                "updated": bool(_content_changed),
                "content_changed": bool(_content_changed),
                "content_same": (not _content_changed),
                "md5_before": _before_md5,
                "md5_after": _after_md5,
                "rc": rc, "size": after[0],
                "note": ("内容已更新" if _content_changed
                         else "内容与保存前一致（无修改时重复保存的正常结果）")}
        # ── 【BUG-05/08】把材质状态一并回传（材料栏空白/密度虚标的直接原因）
        _out["material"] = self.material_result
        # ── 【Bug-14 修复】显式回显 applied_material 与来源，便于排查
        #    "图纸材料 vs 实际材料"不一致（例：小屋写了 PETG 却变成 6061-T6）。
        _mr = self.material_result if isinstance(self.material_result, dict) else {}
        _out["applied_material"] = (_mr.get("applied_name")
                                    or _mr.get("material")
                                    or getattr(self, "_pending_material", None))
        _out["material_source"] = (getattr(self, "_material_source", None)
                                   or _mr.get("source"))
        if _mat_warn:
            _out["material_warning"] = _mat_warn
            self._warn("save: " + _mat_warn)
        # ══ 【三大防线 · 防线①材料】落盘【材料凭据】════════════════════
        # 【Bug-01 修复】凭据写入抽为 _emit_material_attestation()，与 save_as() 共用，
        #   保证"最终落盘路径旁必有凭据"。
        self._emit_material_attestation(target, _out)
        # ── 【Bug⑪ 修复】同一落盘路径旁写【origin 约定】记录 ───────────────
        # 与材料凭据同目录、同命名基（<part>.origin.json），装配侧可直接读。
        _attach_origin_record(self, target, _out)
        # ── 【P1-⑥ 修复】同一落盘路径旁写【朝向声明】<part>.pr.json ────────
        # 与 .origin.json / .material.json 同基同目录；装配侧据此做 J2/J4/J5 断言。
        _attach_pr_record(self, target, _out)
        return _out

    def set_part_pose(self, up_axis="Z", up_sign=1, main_axis=None,
                      base_plane=None, sketch_y_sign=None, note=None):
        """【P1-⑥】显式声明本零件的【朝向】，保存时写进 <零件>.pr.json。

        为什么需要它（回溯报告 §7 P1）：小屋建零件时靠【试错】发现坐标系语义 ——
          Top Plane 草图局部 y = 世界 -Z；cut() 默认切向 -Y（沿 +Y 要 flip=True）。
        这些知识若只留在提示词里，换个房间就重新踩一遍。现在要求小屋在建完
        立刻【显式声明】自己用的是哪个基准面、上方向是谁，写进 pr.json，
        总装侧就能做 J2/J4/J5 断言。

        Args:
            up_axis:       "X"/"Y"/"Z"：装好后"上"是哪个世界轴
            up_sign:       +1 / -1
            main_axis:     "X"/"Y"/"Z"：主轴（回转轴 / 最长方向）；缺省=up_axis
            base_plane:    建模时选用的基准面名（如 "Top Plane"）
            sketch_y_sign: +1 / -1：草图局部 y 与世界轴 的符号关系（ZSIGN）
            note:          自由说明

        Returns: {ok, pose, warning?}
        """
        _ax = str(up_axis or "").strip().upper()
        if _ax not in ("X", "Y", "Z"):
            return {"ok": False,
                    "error": "up_axis 必须是 X/Y/Z（实际 %r）" % (up_axis,)}
        try:
            _sg = int(up_sign)
        except Exception:
            _sg = 0
        if _sg not in (1, -1):
            return {"ok": False, "error": "up_sign 只能是 +1/-1（实际 %r）" % (up_sign,)}
        _mx = str(main_axis or _ax).strip().upper()
        if _mx not in ("X", "Y", "Z"):
            return {"ok": False,
                    "error": "main_axis 必须是 X/Y/Z（实际 %r）" % (main_axis,)}
        _sy = None
        if sketch_y_sign is not None:
            try:
                _sy = int(sketch_y_sign)
            except Exception:
                _sy = None
            if _sy not in (1, -1):
                return {"ok": False,
                        "error": "sketch_y_sign 只能是 +1/-1"}
        self._part_pose = {
            "up_axis": _ax, "up_sign": _sg, "main_axis": _mx,
            "base_plane": (str(base_plane) if base_plane else None),
            "sketch_y_sign": _sy, "note": (str(note) if note else None),
        }
        out = {"ok": True, "pose": dict(self._part_pose)}
        if not base_plane:
            out["warning"] = ("未声明 base_plane —— 坐标系语义仍不可审计；"
                              "建议写清楚用的是哪个基准面。")
            self._warn(out["warning"])
        return out

    def set_origin_mode(self, origin_mode, note=None):
        """【Bug⑪】显式声明本零件的 origin（原点）约定，保存时写进 .origin.json。

        标准约定（默认）= center_bottom（底面中心）：
          XY 原点在底面轮廓中心，Z=0 在底面，+Z 朝上。
        装配时"把零件坐到某平面上"只需 coincident(底面) + distance(X/Y)，
        不必为每个零件单独换算 —— 这正是 Bug⑪ 想解决的"装配需人工换算"。

        为什么不做成"改几何"：强行移动已建零件的 origin 会让既有图纸、
        装配配合、历史导出全部错位。本函数【只做记录】，不动几何。

        Args:
            origin_mode: ORIGIN_MODES 中的键，如 "center_bottom" /
                         "center_center" / "corner_bottom" / "corner_top" /
                         "axis_center" / "custom"。
            note:        自由说明（custom 模式务必写明具体含义）。

        Returns: {ok, origin_mode, note, warning?}
        """
        _m = str(origin_mode or "").strip()
        if _m not in ORIGIN_MODES:
            return {"ok": False,
                    "error": "未知 origin_mode: %r（可选: %s）"
                             % (_m, ", ".join(sorted(ORIGIN_MODES))),
                    "choices": sorted(ORIGIN_MODES)}
        self._origin_mode = _m
        self._origin_note = str(note) if note else None
        out = {"ok": True, "origin_mode": _m,
               "origin_mode_cn": ORIGIN_MODES[_m][0],
               "assembly_note": ORIGIN_MODES[_m][1],
               "note": self._origin_note,
               "standard_mode": ORIGIN_STANDARD_MODE}
        if _m != ORIGIN_STANDARD_MODE:
            out["warning"] = (
                "本零件 origin 非标准约定（%s）：装配时需按 .origin.json 里的"
                "说明单独换算基准面；建议新零件统一用 %s。"
                % (_m, ORIGIN_STANDARD_MODE))
            self._warn(out["warning"])
        return out

    def _emit_material_attestation(self, target, out):
        """【三大防线 · 防线①材料】落盘【材料凭据】<part>.material.json。

        ── 【Bug-01 修复】抽成独立方法，供 save() 与 save_as() 共用 ────────
        原实现只有 save() 会写凭据；小屋若走 save() → save_as(最终路径)
        流程（或直接用 save_as 保存），凭据会落在中间路径旁，最终零件旁
        【没有任何材料凭据】→ room-end 防线① 全部拒收（实测：磁盘 0 个）。
        现在两条保存路径都写凭据，且写到【最终落盘路径】旁。

        写凭据绝不改变保存结果本身：任何异常只记 warning。

        ── 【BUG-15 修复】装配体（.sldasm）【不写材料凭据】──────────────
        现场：保存 .sldasm 时 save() 仍走材料凭据签发流程，于是报
          "[swapi][WARN] 非法合规：未签发凭据: 材料不能为空"
        —— 装配体本来就没有"材料"这个概念（材料在零件上），这条告警
          是纯噪声，还会被误判成错误。
        现在：目标扩展名是 .sldasm 时直接跳过，并在返回里标明
          material_attestation.skipped="assembly"，语义如实。
        """
        # ── 【BUG-15】装配体不需要材料凭据 ──────────────────────────────
        try:
            _ext = os.path.splitext(str(target))[1].lower()
        except Exception:
            _ext = ""
        if _ext in (".sldasm",):
            out["material_attestation"] = {
                "skipped": "assembly",
                "note": ("装配体（.sldasm）不承载材料属性，"
                         "材料凭据只在零件（.sldprt）上签发 —— 这不是错误。"),
            }
            return out
        _mr = self.material_result if isinstance(self.material_result, dict) else {}
        try:
            import defense_gate as _dg
            # ══ 【Bug16 修复·严重】非法材料【禁止】走验签流程 ═══════════════
            # 测试部实测：new_part(material="INVALID_MAT") 只报 WARN，却照常
            #   保存并写出带 _sig/_kid 的凭据 —— 任何乱写的材料名都能"通过"
            #   验签，材料防线①形同虚设。
            # 修复：写凭据/请求签名【之前】先做严格的材料合法性校验；
            #   不合法则【不请求签名】，凭据明确标 material_invalid=true，
            #   由 defense_gate 在防线①直接拦下。
            # ── 【观察点 13 修复】凭据优先按【用户传入的原始材料名】校验 ────
            # 原实现只看 `_pending_material`（材质写入成功后被清空）→ 回退到
            # 从 SW 读回的【映射名】（PETG 被 SW 近似成 PET），于是：
            #   · 凭据写 requested_material="PET" 而非用户要的 "PETG"；
            #   · set_custom_material("PETG", ...) 的登记对校验不生效；
            #   · 映射名不在库中时凭据判 invalid —— 用户登记了 PETG 也没用。
            # 现改为：① 原始名优先；② 原始名不合法时，若【映射名】合法则采纳
            # 映射名（并如实记录 requested vs applied 的差异，便于排查）；
            # ③ 两者都不合法才判 material_invalid（Bug16 的严格性不受影响）。
            _orig_name = getattr(self, "_requested_material", None)
            _mapped_name = (_mr.get("applied_name") or _mr.get("material")
                            or getattr(self, "_pending_material", None))
            _req_name = _orig_name or _mapped_name
            _mat_ok, _mat_info, _mat_reason = is_valid_material(_req_name)
            _name_used = _req_name
            if not _mat_ok and _mapped_name and _mapped_name != _req_name:
                # 原始名不在库中（例如拼写或 SW 无此牌号）→ 尝试映射名
                _m_ok, _m_info, _m_reason = is_valid_material(_mapped_name)
                if _m_ok:
                    _mat_ok, _mat_info, _mat_reason = _m_ok, _m_info, _m_reason
                    _name_used = _mapped_name
            if not _mat_ok:
                _invalid = {
                    "schema": "dsh-material-attestation/1",
                    "part": os.path.abspath(str(target)),
                    "requested_material": _req_name,
                    "material_invalid": True,
                    "error": "非法材料：%s" % _mat_reason,
                    "hint": ("材料必须在内置库（Q235/45#/304/6061-T6/PETG/PLA/ABS/"
                             "Nylon/PC/TPU…）或 custom_materials.json 中；"
                             "拼写错误不会被接受，也【不会】签发凭据。"),
                    "save_ok": bool(out.get("ok")),
                }
                try:
                    _dg.write_material_attestation(target, _invalid)
                except Exception:
                    pass
                out["material_attestation"] = {
                    "file": _dg.attestation_path(target),
                    "material_invalid": True,
                    "error": _mat_reason,
                    "signed": False,
                }
                self._warn("非法材料，未签发凭据: %s" % _mat_reason)
                return out
            # [防伪造] 绑定零件本体的 size + md5：check 端会重算比对，
            #   让"随手造一个假文件 + 手写凭据"这条绕过路径失效。
            # ── 【Bug① 修复】凭据必须同时记录"请求材质"与"SW 内部实际材质" ──
            # 现场缺陷：请求 PETG（ρ1270/σy40），SW 材质库无此牌号 → 内部仍是
            #   PET（ρ1420/σy≈55）。原凭据只写 requested_material="PETG"，
            #   读凭据的人会以为零件就是 PETG —— 但 .sldprt 属性栏显示 PET。
            # 现在把"SW 内部实际生效名 + 是否与请求一致"一并固化进凭据，
            #   由 defense_gate.check_material_attestation 给出可见 WARNING。
            # 注意：这些字段必须【申请签发时】提交（签后补会 HMAC 失配）。
            _sw_internal_mat = None
            try:
                _sw_internal_mat = (_mr.get("sw_internal_material")
                                    or self.get_material().get("name"))
            except Exception:
                _sw_internal_mat = _mr.get("sw_internal_material")
            _sw_int_ok = _mr.get("sw_internal_matches_request")
            if _sw_int_ok is None:
                try:
                    _sw_int_ok = bool(
                        _sw_internal_mat and _orig_name
                        and str(_sw_internal_mat).strip().casefold()
                            == str(_orig_name).strip().casefold())
                except Exception:
                    _sw_int_ok = None
            _sw_int_warn = _mr.get("warning") if _sw_int_ok is False else None
            _p_size, _p_md5 = None, None
            try:
                _p_size = os.path.getsize(target)
                import hashlib as _hl2
                _h2 = _hl2.md5()
                with open(target, "rb") as _pf:
                    for _ch in iter(lambda: _pf.read(1 << 20), b""):
                        _h2.update(_ch)
                _p_md5 = _h2.hexdigest()
            except Exception:
                pass
            # ── 【P0 修复·凭据伪造密度】密度如实记录，绝不回填期望值 ────────
            # 旧实现：写入值=1000（SW 未生效默认值=水）时回填内置期望密度，
            #   于是凭据写 ok=true + 期望密度（如 65Mn 写 7850），
            #   材料防线①据此判"密度吻合" → PASS。
            #   实测 8/18 牌号 set_material 失败、零件仍是水(1000)，
            #   却全部通过防线；且 mass 用 7850 算出 1.5993 kg（真实 0.2037）。
            # 现在：实测密度如实记录；期望密度另存 expected_density_kg_m3；
            #   赋材未生效时【不得】把 ok 置为 True。
            _att_density, _att_expected, _att_suspect = _pick_attested_density(
                _mr, _name_used)
            _att_ok = bool(_mr.get("ok"))
            # 赋材未真正生效（密度仍是水/缺失）→ 凭据 ok 必须为 False
            if _att_suspect:
                _att_ok = False
            _att_info = {
                "ok": _att_ok,
                "method": _mr.get("method"),
                # ── 【观察点 13 修复】如实记录"用户要的"与"实际生效的" ──────
                # requested_material = 用户传入的原始名（PETG），用于回答
                #   "我明明要 PETG，为什么凭据里是 PET"；
                # applied_name = SW 实际匹配到的牌号（PET）。
                # 校验用 _name_used（原始名合法就用原始名，否则用映射名）。
                "requested_material": _orig_name or _req_name,
                "attested_material_name": _name_used,
                "applied_name": _mr.get("applied_name") or _mr.get("material"),
                # ── 【Bug①】SW 内部实际材质（与请求材质并列，二者不可混淆）──
                "sw_internal_material": _sw_internal_mat,
                "sw_internal_matches_request": _sw_int_ok,
                "sw_internal_warning": _sw_int_warn,
                "database_name": _mr.get("database_name"),
                "match_level": _mr.get("match_level"),
                # ── 【Bug15 残留修复】密度不得记录 SW 的未生效默认值(1000) ──
                # 测试部复测：PLA 保存成功、signed=true，但 material.json
                #   记录 density=1000（材料库为 1240）—— 说明 SW 侧写入未真正
                #   生效，而凭据照抄了"写入后读到的"1000（=水，即默认值）。
                # 修复：密度优先取【真实写入值】；若它等于 1000(水) 或缺失，
                #   而内置表/自定义库有该材料的期望密度，则用期望值并标注来源，
                #   避免凭据把"未生效的默认值"当成材料事实。
                # ── 【观察点 13 修复】用【校验通过的名字】取期望密度 ────────
                # 原实现传 _req_name（可能是映射名 PET）→ 取到 PET 的密度 1420；
                #   而用户要的是 PETG（1270）。改用 _name_used 后，只要 PETG
                #   在库中（内置或 custom_materials.json），就拿 PETG 的正确属性。
                # ── 【P0 修复】实测密度如实记录（不再回填期望值）───────────
                # density_kg_m3 = 零件实测值（1000 就是 1000，代表没赋上材）
                # expected_density_kg_m3 = 材料库期望值（供防线比对，非实测）
                # material_applied = 赋材是否真正生效
                "density_kg_m3": _att_density,
                "expected_density_kg_m3": _att_expected,
                "material_applied": (not _att_suspect),
                "material_apply_note": (
                    None if not _att_suspect else
                    ("赋材未生效：零件实测密度=%s（1000 等同【水】，是 SolidWorks "
                     "未赋材的默认值）；期望密度=%s。请检查该牌号是否存在于 SW "
                     "材质库，或用 set_custom_material() 显式登记后重做。"
                     % (_att_density, _att_expected))),
                "density_before_kg_m3": _mr.get("density_before_kg_m3"),
                "density_error_pct": _mr.get("density_error_pct"),
                "approximate_match": bool(_mr.get("approximate_match")),
                "material_mismatch_rejected": bool(
                    _mr.get("material_mismatch_rejected")),
                # ── 【Bug-C 修复】跨标准替代标记（如 Q235 → AISI 1020）──────
                "cross_standard": bool(_mr.get("cross_standard")),
                "cross_standard_note": _mr.get("cross_standard_note"),
                "rejected_candidates": _mr.get("rejected_candidates") or [],
                "source": out.get("material_source"),
                "warnings": _mr.get("warnings") or self.warnings,
                "part_size_bytes": _p_size,
                "part_md5": _p_md5,
                "save_ok": bool(out.get("ok")),
            }
            # 归一化材料家族：由 swapi 在【申请签发时】提供给宿主，
            #   而不是签名后再补字段（那会让 HMAC 失配）。
            try:
                _att_info["attested_family"] = (
                    _dg.material_family(_att_info.get("applied_name"))
                    or _dg.material_family(_att_info.get("density_kg_m3")))
            except Exception:
                _att_info["attested_family"] = None
            # ── 【Bug-C 根治·强制兜底】凭据落盘前【必然】执行跨标准检测 ────
            # 为什么必须在这里再做一次：SW 侧的 match_level 取决于它的材质库
            #   是否存在同名项，而别名表、库版本、语言都会影响它 ——
            #   实测 Q235 被解析成 AISI 1020 时 approximate_match 仍为 false，
            #   光靠 set_material 内部那处检测【不可靠】。
            #   凭据是防线①的判定依据，因此必须在【写凭据之前】用
            #   "请求名 vs 实际生效名"做一次与 SW 无关的独立判定。
            try:
                # == [A-86 修复] 跨标准检测【不能拿请求名跟它自己比】========
                # 原实现比的是 requested_material vs applied_name，而
                #   applied_name 在 swapi 里就是"我们登记的名字"（= 请求名）：
                #     L5299  "applied_name": _mr.get("applied_name") or _mr.get("material")
                #     L4655/4676  out["applied_name"] = ... or nm   （nm = 请求名）
                #   于是 _detect_cross_standard(requested, applied) 在
                #     L1572  _rq.lower() == _ap.lower() -> 立即 return False
                #   —— 这段"强制兜底检测"【从未触发过一次】。
                # 实测（2026-11-01）：请求 40Cr，SW 实际材质是
                #   "SolidWorks DIN Materials|1.2312 (40CrMnMoS8-6)|90"（德国 DIN
                #   体系），凭据却写 applied_name="40Cr" / cross_standard=false，
                #   防线①据此认为"精确匹配"。
                # 修复：优先拿【SW 内部真实材质名】(sw_internal_material) 做比对，
                #   它才是"实际生效了什么材料"的事实；拿不到才退回 applied_name。
                _ap_for_cross = (_att_info.get("sw_internal_material")
                                 or _att_info.get("applied_name")
                                 or _att_info.get("attested_material_name"))
                _final_cross, _final_note = _detect_cross_standard(
                    _att_info.get("requested_material"), _ap_for_cross)
                if _final_cross:
                    _att_info["cross_standard"] = True
                    _att_info["cross_standard_note"] = _final_note
                    _att_info["approximate_match"] = True
                    self._warn("跨标准材料替代（凭据层强制检测）：%s" % _final_note)
                elif _is_equivalent_naming(_att_info.get("requested_material"),
                                           _ap_for_cross):
                    _att_info["equivalent_naming"] = True
                # == [A-86 补充] "名字对不上但没被判跨标准"也必须留痕 ========
                # 裸牌号（灰铸铁(SN) / DIN 1.2312）在 _material_standard_of
                #   里查不到体系 -> L1579 `if not _s1 or not _s2: return False`
                #   -> 既不算跨标准、也不告警，凭据看起来完全干净。
                #   这类"SW 库近似命中"正是材质被写坏的主因之一，
                #   必须显式标记出来交给防线①。
                elif (_ap_for_cross
                      and str(_att_info.get("requested_material") or "").strip()
                      and (str(_att_info.get("requested_material")).strip().casefold()
                           != str(_ap_for_cross).strip().casefold())):
                    _att_info["name_substituted"] = True
                    _att_info["name_substituted_note"] = (
                        "请求材质 %r 与 SW 实际生效材质 %r 名称不一致"
                        "（未识别为标准体系差异，可能是 SW 材质库的近似命中）。"
                        "请人工确认二者是否等价。"
                        % (_att_info.get("requested_material"), _ap_for_cross))
                    self._warn(_att_info["name_substituted_note"])
            except Exception:
                pass
            # ── 【Bug-C 运行时修复】凭据补【属性指纹】供防线做属性级比对 ────
            # 光看名字/密度不足以识别"实得材料与请求材料不是同一材料"：
            #   · 6061-T6 → 7075 名字不同但密度只差 4.1%；
            #   · PETG → PET 名字都被写成 PETG，只能靠密度差识别。
            # 这里把【请求材料的期望属性】与【实际生效材料的属性】都固化进
            #   凭据（纳入待签体），防线①据此做独立于名字的属性级判定。
            try:
                _exp_props = _resolve_material_props_any(
                    _att_info.get("requested_material")) or {}
                _got_props = _resolve_material_props_any(
                    _att_info.get("applied_name")
                    or _att_info.get("attested_material_name")) or {}
                _att_info["expected_yield_mpa"] = _exp_props.get("yield_mpa")
                _att_info["expected_density_kg_m3"] = _exp_props.get("density_kg_m3")
                _att_info["applied_yield_mpa"] = _got_props.get("yield_mpa")
                _att_info["applied_density_kg_m3"] = _got_props.get("density_kg_m3")
                # 屈服强度差异 —— 跨牌号替代最灵敏的判据
                _ey, _ay = _exp_props.get("yield_mpa"), _got_props.get("yield_mpa")
                if _ey and _ay:
                    try:
                        _yre = abs(float(_ay) - float(_ey)) / float(_ey)
                        _att_info["yield_deviation_pct"] = round(_yre * 100, 2)
                    except Exception:
                        pass
            except Exception:
                pass
            # == [A-48 修复] 把【零件几何指纹】提交给宿主一并签名 ==========
            # 原防线①只校验"材料凭据本身是否可信"（签名/md5/密度/家族），
            #   对【零件几何】零约束 —— 实测（2026-11-01）：
            #     外径 φ160（规范要求 φ200）、体积缺 35% 的旧草稿，
            #     因"材料凭据 ok=true"直接通过防线①并进入总装。
            #   防线①能证明"这件的材料写对了"，但无法回答
            #   "这是不是设计要的那件"。
            # 现在把几何事实（bbox / 三向尺寸 / 体积 / 实体数）纳入待签体；
            #   校验端据 load_case 的 design_domain 与 expected_volume_mm3
            #   （若有）做范围判定。
            # 注意：几何是"事实陈述"，不是"判定结论"—— 判定仍在校验端做；
            #   取不到值只表示"该项未验证"，绝不伪造合格。
            try:
                _bbox_att = None
                try:
                    _bbox_att = self.body_box_mm()
                except Exception:
                    _bbox_att = None
                _vol_att = None
                try:
                    _vol_att = self._body_volume_mm3()
                except Exception:
                    _vol_att = None
                _bodies_att = None
                try:
                    _b = self.model.GetBodies2(0, 1)
                    _bl = list(_b) if isinstance(_b, tuple) else ([_b] if _b else [])
                    _bodies_att = len(_bl)
                except Exception:
                    _bodies_att = None
                if _bbox_att:
                    _att_info["part_bbox_mm"] = [round(float(x), 3) for x in _bbox_att]
                    try:
                        _att_info["part_dims_mm"] = [
                            round(float(_bbox_att[3 + _i]) - float(_bbox_att[_i]), 3)
                            for _i in range(3)]
                    except Exception:
                        pass
                if _vol_att is not None:
                    _att_info["part_volume_mm3"] = round(float(_vol_att), 3)
                if _bodies_att is not None:
                    _att_info["part_body_count"] = int(_bodies_att)
            except Exception:
                pass
            # [主防线] 材料凭据必须由【宿主签发】—— 密钥只在宿主内存，
            #   因此即使 AI 手写一份字段齐全的 .material.json，也无法通过验签。
            _signed_m = _dg.request_signed_credential({
                "kind": "material",
                # ── 【Bug8 修复】room 必须传【真实房间名】，不能是空串 ──────
                # 原实现写成 (_att_info.get("source") and "" or "") —— 恒为 ""，
                #   而宿主 /defense/sign 会用 ROOM_RE 校验 room，空串直接判
                #   "bad room" → 材料凭据永远签不出来（表现为
                #   "宿主签发端点无响应"，因为请求根本没被受理）。
                # 房间来源优先级：环境变量 DSH_ROOM > 显式参数。
                "room": (os.environ.get("DSH_ROOM") or "").strip(),
                "part_path": target,
                "applied_name": _att_info.get("applied_name"),
                "density_kg_m3": _att_info.get("density_kg_m3"),
                "source": _att_info.get("source"),
                # ── 【签后补字段 BUG 修复】以下材料事实原先是在签名【之后】
                #   由 swapi 自己 update 进凭据体的（导致 HMAC 失配）。
                #   现在改为在【申请签发时】提交，由宿主纳入待签体。 ──
                # ── 【P0 修复·ok 语义】必须用"赋材是否真正生效"，不是"保存是否成功" ──
                # 实测缺陷（子代理受控实验确证）：L3512 已算出 _att_ok
                #   （赋材失败时置 False），但这里提交的却是 save_ok（保存成功
                #   =True）→ 8/18 个 set_material 失败的牌号凭据仍写 ok=true。
                #   现在提交 _att_ok，并额外带上 material_applied 说明原因。
                "ok": _att_ok,
                # ── 【P0 修复·新字段被白名单丢弃】material_applied /
                #   material_apply_note 必须纳入待签体，否则宿主 /defense/sign
                #   的字段白名单会把它们丢掉，防线与报告都读不到。
                "material_applied": _att_info.get("material_applied"),
                "material_apply_note": _att_info.get("material_apply_note"),
                "density_before_kg_m3": _att_info.get("density_before_kg_m3"),
                "approximate_match": _att_info.get("approximate_match"),
                "material_mismatch_rejected": _att_info.get("material_mismatch_rejected"),
                "attested_family": _att_info.get("attested_family"),
                # ── 【Bug-04 修复】把"用户请求的材料"纳入【待签体】──────────
                # 实测缺陷：设计规格要求 PETG（ρ1270/σy40），SW 材料库无此牌号
                #   时被近似成 PET（ρ1420/σy≈55），凭据只记 approximate_match=true
                #   仅以 WARN 放行 —— 两者屈服强度差约 37%，属"跨材料静默替代"，
                #   本应拦截。根因：凭据里【没有】requested_material 字段，
                #   门禁无从对比"要的"与"实际生效的"，只能靠 approximate_match
                #   这个过宽的布尔量（同族即 true）。
                # 现在把 requested_material / attested_material_name 一并交给宿主
                #   签名，defense_gate 即可在防线①做属性级对比并拦截。
                "requested_material": _att_info.get("requested_material"),
                "attested_material_name": _att_info.get("attested_material_name"),
                # ── 【Bug① 修复】把"SW 内部实际材质"提交给宿主 ────────────
                # 凭据要能回答两个不同问题：
                #   ① 设计规格要的是什么材质（requested_material）；
                #   ② SolidWorks 零件里【真实生效】的是什么材质（本字段）。
                # 现场缺陷：二者不一致（PETG vs PET）时凭据只记前者，
                #   材料防线据此判 PASS，而零件属性栏显示的是另一种材料。
                #
                # ⚠️ 实测：宿主 /defense/sign 只把【白名单里】的字段纳入待签体，
                #   这三个键会被丢弃（plugins/dsh-engineering-ui/lib/index.js
                #   的 material 分支没有它们）。由于凭据签完不能再补字段
                #   （HMAC 必然失配 → 防线① 全灭），这里仍照常提交（宿主一旦
                #   扩充白名单就会自动生效），同时另写一份【旁挂记录】
                #   <part>.material.json.sw_internal.json 作为当前权威落点。
                "sw_internal_material": _att_info.get("sw_internal_material"),
                "sw_internal_matches_request": _att_info.get("sw_internal_matches_request"),
                "sw_internal_warning": _att_info.get("sw_internal_warning"),
                # ── 【Bug-C 修复】跨标准替代标记纳入待签体 ────────────────
                #   Q235→AISI 1020 这类跨体系替代原先只标 approximate_match，
                #   报告看起来像"精确匹配"。现在由 swapi 在申请签发时提交
                #   cross_standard，宿主原样签名，defense_gate 据此拦截。
                "cross_standard": bool(_att_info.get("cross_standard")),
                "cross_standard_note": _att_info.get("cross_standard_note"),
                # ── 【Bug-C 运行时修复】属性指纹纳入待签体 ────────────────
                #   防线①需要"请求材料 vs 实际材料"的属性差异来识别
                #   "名字看不出、密度差也小"的替代（6061-T6→7075 屈服差 82%）。
                "expected_yield_mpa": _att_info.get("expected_yield_mpa"),
                "expected_density_kg_m3": _att_info.get("expected_density_kg_m3"),
                "applied_yield_mpa": _att_info.get("applied_yield_mpa"),
                "applied_density_kg_m3": _att_info.get("applied_density_kg_m3"),
                "yield_deviation_pct": _att_info.get("yield_deviation_pct"),
                # == [A-48] 几何指纹（宿主纳入待签体；校验端据此判"是不是这件"）
                "part_bbox_mm": _att_info.get("part_bbox_mm"),
                "part_dims_mm": _att_info.get("part_dims_mm"),
                "part_volume_mm3": _att_info.get("part_volume_mm3"),
                "part_body_count": _att_info.get("part_body_count"),
            })
            if _signed_m and _signed_m.get("ok") and isinstance(_signed_m["credential"], dict):
                # ── 【观察点 13/15 修复】材料被近似替换时必须显式告警 ────────
                # 用户明确要求 PETG，SW 材料库无此牌号时会被近似成 PET
                # （密度 1420 vs PETG 1270）。原实现对此【完全静默】——
                # 凭据里悄悄写着 PET，用户直到物理防线 FAIL 才发现材料不对。
                # 现在把"要的"与"实际生效的"不一致打成 WARN，让主对话/小屋
                # 当场就能看到，而不是等到 room-end 才暴露。
                try:
                    _want = str(_orig_name or "").strip()
                    _got = str(_mr.get("applied_name") or _mr.get("material") or "").strip()
                    if _want and _got and _want.lower() != _got.lower():
                        self._warn(
                            "材料被近似替换：请求 '%s'，SW 实际生效 '%s'"
                            "（密度 %s）。若二者属性不同（如 PETG 1270 vs PET 1420），"
                            "物理校核会按实际材料判定，请确认是否可接受。"
                            % (_want, _got, _att_info.get("density_kg_m3")))
                except Exception:
                    pass
                # ── 【签后补字段 BUG 修复】宿主已签名 → 【原样落盘】──────────
                #   原先这里 _cred_m.update({...5 个字段})：在签名之后改凭据体，
                #   HMAC 必然失配 → 防线①永远验签失败。
                #   现在这些材料事实由 swapi 在【申请签发时】一并提交，
                #   宿主纳入待签体（见 index.js 的 material 分支）。
                _att = _dg.write_material_attestation(target, _signed_m["credential"])
            else:
                # 宿主不可用 → fail-closed：写出【无签名】凭据并留痕，
                #   让防线校验必然失败，而不是静默产出"看似合法"的凭据。
                _dg.note_unsigned_attempt(
                    os.path.basename(str(target)), "material",
                    (_signed_m or {}).get("error"))
                _fallback_m = dict(_att_info)
                _fallback_m["_unsigned_reason"] = (
                    (_signed_m or {}).get("error") or "宿主签名服务不可用")
                _att = _dg.write_material_attestation(target, _fallback_m)
            out["material_attestation"] = {
                "file": _dg.attestation_path(target),
                "material": _att.get("attested_material"),
                "density_kg_m3": _att.get("attested_density_kg_m3"),
                "family": _att.get("attested_family"),
                "signed": bool(_att.get("_sig")),
            }
            # ── 【Bug① 修复·落盘层】SW 内部材质事实写【旁挂记录】──────────
            # 宿主 /defense/sign 只签白名单字段（实测 sw_internal_material 会被
            # 丢弃），而签完再补字段会让 HMAC 失配。所以把这条事实单独落盘，
            # 并在返回体里显式告知"凭据里没有它、它在哪里"。
            _side = _write_sw_internal_record(target, {
                "requested_material": _att_info.get("requested_material"),
                "sw_internal_material": _sw_internal_mat,
                "sw_internal_matches_request": _sw_int_ok,
                "sw_internal_warning": _sw_int_warn,
                "applied_name": _att_info.get("applied_name"),
                "attested_material_name": _att_info.get("attested_material_name"),
                "density_kg_m3": _att_info.get("density_kg_m3"),
                "expected_density_kg_m3": _att_info.get("expected_density_kg_m3"),
                "attested_family": _att_info.get("attested_family"),
                "material_attestation_file": _dg.attestation_path(target),
                "signed": bool(_att.get("_sig")),
            })
            out["material_attestation"]["sw_internal_material"] = _sw_internal_mat
            out["material_attestation"]["sw_internal_matches_request"] = _sw_int_ok
            out["material_attestation"]["sw_internal_record"] = _side.get("path")
            if _sw_int_ok is False:
                out["material_attestation"]["sw_internal_warning"] = _sw_int_warn
        except Exception as _e_att:
            out["material_attestation"] = {"ok": False, "error": repr(_e_att)}

    # ---------- 【Bug3】材料与密度 ----------
    def set_material(self, name, database=None, density=None,
                     allow_cross_standard=False):
        """【Bug3 修复】给零件指定材料（并可选强制密度），解决"密度恒为1000kg/m³"。

        问题背景：
          原实现从不设置材料 → SW 用默认密度(≈1000 kg/m³ 水) →
          质量/重心/转动惯量全错，工程图材料栏空白，强度校核不可信。

        实现策略（三级兜底，任一成功即可）：
          第1级 · AddMaterial(name, db) —— 标准路径，从 .sldmat 材质库添加。
                  材质库路径通过 find_material_db() 【跨盘符】搜索得到
                  （SW 可能装在 Z 盘，绝不能写死 C 盘）。
          第2级 · 用已知密度表 COMMON_MATERIALS / 显式 density 参数，
                  通过 SetMaterialPropertyName2 直接写入密度属性。
          第3级 · 仅记录告警，返回失败原因，绝不静默吞掉。

        Args:
            name:     材料名（如 "40Cr" / "Q235" / "6061-T6"）
            database: 可选，显式指定 .sldmat 路径；None 则自动跨盘搜索
            density:  可选，显式密度 (kg/m³)；None 则查内置表

        Returns:
            dict: {ok, material, density_kg_m3, method, database, warnings, error?}
        """
        result = {"ok": False, "material": name, "method": None,
                  "database": None, "database_name": None, "warnings": [],
                  "error_code": None}
        if not name:
            result["error"] = "材料名为空"
            return result

        # 解析期望密度（显式参数 > 内置表），用于事后校验
        info = lookup_material(name)
        want_density = density if density else (info or {}).get("density")
        result["expected_density_kg_m3"] = want_density
        if info:
            result["canonical"] = info.get("canonical")

        # ══ 【Bug3 最终修复 · 实机验证于 SW2025 (Rev 33.5.0)】═══════════════
        # 经过系统性探测（含逐个 dispid 枚举 + 材质库 XML 解析），确认：
        #
        #   ✅ 唯一真正生效的 API（PartDoc 层级）：
        #        model.SetMaterialPropertyName2("", "<SW库名>", "<材料名>")
        #          ↑ 第1参数必须留空   ↑ 界面显示名       ↑ 库中精确名
        #
        #   ❌ AddMaterial / SetMaterialPropertyName —— SW 根本没这两个方法
        #   ❌ IBody2.SetMaterialProperty(cfg, db, name) —— 实测恒返回 1/6，
        #      即使三个参数都正确也不生效（SW2025 语义已变）
        #   ❌ 第1参数传配置名（"默认"/"Default"）—— 语义错误，规范用法是空串
        #
        # 实测五组材料全部成功，密度与材质库定义完全一致：
        #   灰铸铁→7200 / AISI 304→8000 / 普通碳钢→7800 /
        #   1023 碳钢板 (SS)→7858 / 合金钢→7700
        #
        # 库名必须是 SW 界面显示名（"SolidWorks Materials"），
        # 传路径/小写文件名/空串都会静默不生效。
        tried = []
        err_names = {0: "Success", 1: "InvalidMaterialName",
                     2: "InvalidDatabaseName", 3: "InvalidConfigurationName",
                     4: "Unknown", 5: "InvalidConfigurationName(5)",
                     6: "InvalidDatabaseName(6)"}

        # ── 1. 取激活配置的真实名称 ─────────────────────────────────────
        cfg = None
        try:
            cfg = self.model.ConfigurationManager.ActiveConfiguration.Name
        except Exception as e:
            tried.append("ConfigurationManager.ActiveConfiguration -> %r" % (e,))
        if not cfg:
            cfg = "默认"
        result["configuration"] = cfg

        # ── 2. 取实体（IBody2）──────────────────────────────────────────
        body = None
        try:
            _b = self.model.GetBodies2(0, False)   # 0 = swSolidBody
            if _b:
                body = _b[0] if hasattr(_b, "__getitem__") else _b
        except Exception as e:
            tried.append("GetBodies2 -> %r" % (e,))
        if body is None:
            result["error"] = "零件没有实体（IBody2）—— 请先建模再设材料"
            result["warnings"] = tried
            return result

        # ── 3. 解析"库名"（SW 界面显示名，而非路径）─────────────────────
        # 显式传入的 database 若形如路径 → 取 basename 去扩展名；
        # 若已是库名（不含路径分隔符）→ 直接使用。
        db_names = []
        if database:
            d = str(database)
            if ("\\" in d) or ("/" in d):
                base = os.path.splitext(os.path.basename(d))[0]
                db_names.append(_sw_db_display_name(base))
            else:
                db_names.append(_sw_db_display_name(d))
        # 自动探测：用 SW 自己报告的库列表（最权威）
        # 【R11 修复】ISldWorks.GetMaterialDatabases 在类型库里是【方法】
        #   （gen_py L59525，`def GetMaterialDatabases(self):`）。
        #   旧写法 `self.sw.GetMaterialDatabases or []` 按属性取 → 拿到方法
        #   对象（真值！）→ 迭代它直接 TypeError → 被 except 吞掉 →
        #   库列表永远探测不到，只能靠下面写死的 ("SolidWorks Materials",
        #   "SolidWorks DIN Materials") 兜底。这是"材质写入不可靠"的成因之一：
        #   用户的自定义库 / 中文库 / 跨盘安装的库全都不在候选里。
        try:
            _dbs = _com_get_soft(self.sw, "GetMaterialDatabases", default=None)
            for d in (_dbs or []):
                base = os.path.splitext(os.path.basename(str(d)))[0]
                nm = _sw_db_display_name(base)
                if nm not in db_names:
                    db_names.append(nm)
        except Exception as e:
            tried.append("GetMaterialDatabases -> %r" % (e,))
        # 兜底：常见库名
        for nm in ("SolidWorks Materials", "SolidWorks DIN Materials"):
            if nm not in db_names:
                db_names.append(nm)
        # 优先把主库排前面
        db_names.sort(key=lambda x: 0 if x == "SolidWorks Materials" else 1)
        result["database_name"] = db_names[0] if db_names else None

        # ── 4. 候选材料名：用户给的 + 内置别名 ───────────────────────────
        #     ══ 【BUG-05 二次修复 · 关键】跨材料家族回退必须禁止 ══════════
        #     问题现象（用户实测）：任务要求 6061-T6 铝，密度却变成 7900（钢）。
        #     根因：候选名 = [用户给的] + [内置表里的全部别名]，然后【逐个试】。
        #       请求 "Q235" 时库里没有 → 试别名 "AISI 1020" → 命中 exact →
        #       密度 7900（AISI 1020 库内值 0.79E+04）。
        #       请求 "6061-T6" 时若恰好也走到别名链，同样可能落到钢。
        #       结果是"图纸写铝、零件是钢"——质量/重心/强度/寿命全错，
        #       而返回值还是 ok=True，属于最危险的静默错误。
        #     修复：别名只在【同族】内使用（铝↔铝、钢↔钢），
        #       绝不允许因"名字匹配上了"就跨族换材。
        names_to_try = [name]
        if info:
            _fam = _material_family(name)
            for a in (info.get("aliases") or []):
                if a in names_to_try:
                    continue
                # 同族才允许作为候选；跨族别名直接丢弃并记录
                if _fam and _material_family(a) and _material_family(a) != _fam:
                    result["warnings"].append(
                        "已忽略跨材料家族别名 %r（%s → %s）："
                        "禁止把 %s 静默替换成另一种材料"
                        % (a, _fam, _material_family(a), name))
                    continue
                names_to_try.append(a)

        # ── 5. 组合尝试：库名 × 材料名（含自动匹配库内真实名）─────────────
        #    先读一次材质库，把"近似名"映射到库里的精确名，
        #    避免因空格/全半角/大小写差异而匹配失败。
        def dens_now():
            # ── 【R11 修复】GetMassProperties 在类型库里是【方法】(L38295，
            #   无参)，旧代码按属性读 self.model.GetMassProperties（不带括号）
            #   拿到的是方法对象，isinstance(tuple) 恒 False → 永远 return 0.0。
            #   后果：set_material 里 d_before/d_after 恒为 0 →
            #     · "密度变了没有"永远判不出（_dchg 恒 False）；
            #     · 成功判据退化成只看 MaterialIdName 有没有值；
            #     · 这是 BUG-30/55/56"材质写入不可靠"的一个直接成因。
            try:
                mp = _com_get(self.model, "GetMassProperties")
                if mp and isinstance(mp, (tuple, list)) and len(mp) >= 6 and mp[3]:
                    return float(mp[5]) / float(mp[3])
            except ComMemberError:
                pass
            except Exception:
                pass
            return 0.0

        d_before = dens_now()
        result["density_before_kg_m3"] = d_before

        # ── 【R3 修复】记录"写入前"的真实材质，候选被拒后回滚 ──────────────
        # 现场（2026-11-10 复检 · probe_material_readback）：
        #   请求 40Cr，SW 库无 GB 牌号 → 候选链里的 DIN 1.2083/1.2311/1.2312
        #   被【逐个真实写入】，读回不符后再 continue —— 但 SW 不会自己
        #   恢复，零件最终停在【最后一个被拒的候选】1.2312 上：
        #   函数返回 ok=false（防线也正确 BLOCK），可 .sldprt 属性栏里
        #   却是一个精确得像真的错误牌号，拿去加工/打印会误导人。
        # 修复：写入前记下 before 材质；任何候选被拒 → 立即回滚到 before。
        #   配合下面的"先验后写"，模型不会被试错弄脏。
        _before_mat = _read_back_material(self)
        _restore_log = []

        def _restore_before_material(reason=""):
            _nm0 = str((_before_mat or {}).get("name") or "").strip()
            if not _nm0:
                _restore_log.append({"reason": reason, "ok": True,
                                     "note": "写入前无材质，保持无材质"})
                return {"ok": True, "restored_to": None, "reason": reason}
            _ps = _nm0.split("|")
            _db0 = _ps[0].strip() if len(_ps) >= 2 else ""
            _mt0 = _ps[1].strip() if len(_ps) >= 3 else _nm0
            try:
                _com_get(self.model, "SetMaterialPropertyName2", "", _db0, _mt0)
                time.sleep(0.15)
                _rb0 = _read_back_material(self)
                _okr = _material_names_match(_rb0.get("name"), _nm0)
                _rec = {"reason": reason, "ok": bool(_okr), "restored_to": _nm0,
                        "readback": _rb0.get("name")}
                _restore_log.append(_rec)
                return _rec
            except Exception as _e_rs:
                _rec = {"reason": reason, "ok": False, "restored_to": _nm0,
                        "error": repr(_e_rs)}
                _restore_log.append(_rec)
                return _rec

        # ══ 主路径：【PartDoc.SetMaterialPropertyName2("", 库名, 材料名)】 ════
        # 这是实机（SW2025 Rev 33.5.0）验证唯一真正生效的调用方式。
        # 五组材料实测全部成功，密度与材质库定义完全一致：
        #   灰铸铁→7200 / AISI 304→8000 / 普通碳钢→7800 / 1023碳钢板→7858 / 合金钢→7700
        # 关键：第 1 参数必须留空字符串（不是配置名！）。
        for dbn in db_names:
            for mname in names_to_try:
                for real, level in _resolve_material_names(dbn, mname, with_level=True):
                    # ── 【R3 修复】候选【先验后写】：名字不符/跨标准的根本不下笔 ──
                    # 原实现"先写再验"：每个候选都会真实改变零件材质，被拒后
                    # 也不回滚 → 最后一次候选（1.2312）留在零件里（见上）。
                    if not _material_names_match(real, name):
                        tried.append("候选 %r 跳过：与请求名 %r 不匹配（未写入）"
                                     % (real, name))
                        result.setdefault("prefilter_rejected", []).append({
                            "candidate": real, "database": dbn,
                            "reason": "name_mismatch"})
                        continue
                    _cross_std0, _std_note0 = _detect_cross_standard(name, real)
                    if _cross_std0 and not allow_cross_standard:
                        tried.append("候选 %r 跳过：跨标准替代（%s，未写入）"
                                     % (real, _std_note0 or "不同标准体系"))
                        result.setdefault("cross_standard_rejected", []).append({
                            "candidate": real, "database": dbn,
                            "requested": name, "note": _std_note0})
                        continue
                    try:
                        # 【R11 修复】SetMaterialPropertyName2 是【方法】（类型库
                        #   IPartDoc L47898，3 参：ConfigName/Database/Name），
                        #   显式加括号，不再靠 getattr 猜。
                        _com_get(self.model, "SetMaterialPropertyName2",
                                 "", dbn, real)
                        time.sleep(0.18)
                        d_after = dens_now()
                        mid = ""
                        # MaterialIdName 是【属性】（IPartDoc L47915
                        # _prop_map_get_），**绝不能加括号** —— 旧代码用
                        # getattr(...) or "" 取值，语法上没错，但没有类型库
                        # 依据；这里改用 _com_get 显式声明"它是属性"。
                        mid = _com_get_soft(self.model, "MaterialIdName",
                                            default="") or ""
                        _dchg = bool(d_before and d_after
                                     and abs(d_after - d_before) > 1)
                        # ══ 【BUG-05 二次修复 · 核心拦截】══════════════════
                        # 原判据 `if _dchg or mid:` 过于宽松 —— 只要 SW 接受
                        # 了调用就判成功，哪怕实际写入的是【另一种材料】。
                        # 实测事故：要求 6061-T6 铝(2700)，却因别名链落到
                        #   AISI 1020 钢(7900)，仍返回 ok=True。
                        # 修复：写入后【按密度核对材料家族】——
                        #   · 期望密度存在 且 实际密度偏差 >25% → 判失败，
                        #     继续尝试其它候选（不把错误材料当成功）；
                        #   · 若所有候选都偏差过大，最终在下面显式报错，
                        #     绝不静默交出一个"材质不符"的零件。
                        _fam_expected = _material_family(want_density) if want_density else None
                        _fam_actual = _material_family(d_after) if d_after else None
                        _fam_mismatch = bool(_fam_expected and _fam_actual
                                             and _fam_expected != _fam_actual)
                        _dens_bad = bool(want_density and d_after and
                                         abs(d_after - want_density) / float(want_density) > 0.25)
                        if _fam_mismatch or _dens_bad:
                            tried.append(
                                "候选 %r 被拒：实际密度 %.0f(%s) 与期望 %.0f(%s) 不符"
                                % (real, d_after or 0, _fam_actual or "?",
                                   want_density or 0, _fam_expected or "?"))
                            result.setdefault("rejected_candidates", []).append({
                                "name": real, "database": dbn,
                                "density_kg_m3": d_after,
                                "family": _fam_actual,
                                "expected_family": _fam_expected,
                                "expected_density_kg_m3": want_density,
                            })
                            _restore_before_material("density/family rejected: %s" % real)
                            continue      # 关键：不接受，继续试下一个候选
                        # ══ 【R9 修复 · 2026-10-31】写入后【必须读回校验】══════
                        # 台账 BUG-30/55/56：实测 33/35 件
                        #   sw_internal_matches_request=false —— 凭据写的是
                        #   请求牌号，.sldprt 里实际是另一个材质，二者脱节。
                        # 根因：旧判据只看"SW 有没有接受调用"（_dchg or mid），
                        #   SW 静默接受但写入的是**另一个**材料时照样判成功。
                        # 修复：写入后【独立回读】SW 内部真实材质名，与本次
                        #   尝试的候选名 real 逐字比对（忽略大小写/空白/库名前缀）；
                        #   不一致 → 判该候选失败，继续下一个；
                        #   全部候选都不一致 → 显式报错，绝不静默成功。
                        _readback = _read_back_material(self)
                        result["readback_material"] = _readback.get("name")
                        result["readback_method"] = _readback.get("method")
                        _rb_name = str(_readback.get("name") or "").strip()
                        # ══ 【R9 修复·关键】读回必须比对【用户请求名 name】══
                        # 而不是循环里的候选名 real —— 这个字段的名字就叫
                        # sw_internal_matches_request（"SW 内部是否等于【请求】"）。
                        # 若只比候选名：请求 Q235、候选链里混进"普通碳钢"时，
                        # SW 真把零件写成"普通碳钢" → 回读 == 候选 → 判匹配 →
                        # 又是"图纸写国标、零件是别的材料"。这正是 33/35 件
                        # sw_internal_matches_request=false 的成因。
                        _rb_match = _material_names_match(_rb_name, name)
                        if _rb_name and not _rb_match:
                            tried.append(
                                "候选 %r 读回校验失败：请求 %r，SW 内部实际是 %r —— "
                                "写入未生效或被静默替换"
                                % (real, name, _rb_name))
                            result.setdefault("readback_mismatch", []).append({
                                "candidate": real, "database": dbn,
                                "requested": name,
                                "sw_internal": _rb_name,
                                "method": _readback.get("method"),
                            })
                            _restore_before_material("readback mismatch: %s -> %s"
                                                     % (real, _rb_name))
                            continue      # 关键：读回不符，绝不判成功
                        # ══ 【R9 修复·关键】跨标准替代必须【在判成功之前】拒绝 ══
                        # 顺序极其重要：如果把这段放在 result.update(ok=True)
                        #   之后，continue 只会跳过"后续填充"，而 ok 已经是
                        #   True —— 结果就是"拒绝了一个候选，却把整个调用判成功"。
                        #   这正是 BUG-55 的机制：Q235 被写成 AISI 1020（σy 差
                        #   49%），而返回体是 ok=True、33/35 件
                        #   sw_internal_matches_request=false。
                        # 因此必须在【任何 ok=True 赋值之前】判定并 continue。
                        #
                        # 另：必须拿【用户请求名 name】比对，而不是循环候选名
                        #   mname —— names_to_try 里的 AISI 1020 与 Q235 同属
                        #   steel 族会被族校验放行；若拿 mname 比对 real，
                        #   两边同名 → 判"不是跨标准"，照样漏过去。
                        _cross_std, _std_note = _detect_cross_standard(
                            name, real)
                        if _cross_std and not allow_cross_standard:
                            tried.append(
                                "候选 %r 被拒：跨标准替代（%s）"
                                % (real, _std_note or "不同标准体系"))
                            result.setdefault(
                                "cross_standard_rejected", []).append({
                                    "candidate": real, "database": dbn,
                                    "requested": name,
                                    "note": _std_note,
                                })
                            _restore_before_material("cross-standard: %s" % real)
                            continue      # 关键：跨标准不接受，绝不判成功
                        if _dchg or mid:
                            # ── 【MA-01 修复】成功即清除"曾被拒"标志 ──────────
                            # 实测缺陷：8 个【精确匹配】的国标牌号（Q355/GCr15/
                            #   Cr12MoV/QT500-7/2A12/ZL104/20CrMnTi/65Mn）被材料
                            #   防线误 BLOCK。
                            # 根因：rejected_candidates 是【累积】列表 —— 前面某个
                            #   候选被拒（正常的多候选尝试），后面另一个候选成功，
                            #   列表仍非空；签发/校验侧据此推断"该零件材料曾被拒"，
                            #   于是把最理想的精确匹配也拦下（自相矛盾：近似匹配的
                            #   HT200/Q235 反而 PASS）。
                            # 正确语义：最终成功 → 就是成功，与中途试错无关。
                            #   这里显式清掉标志与累积列表，只保留一份审计副本。
                            if result.get("rejected_candidates"):
                                result["rejected_candidates_before_success"] = \
                                    list(result.get("rejected_candidates") or [])
                                result["rejected_candidates"] = []
                            result["material_mismatch_rejected"] = False
                            result.update({
                                "ok": True,
                                "method": "PartDoc.SetMaterialPropertyName2",
                                "applied_name": real,
                                "database_name": dbn,
                                "match_level": level,
                                "density_after_kg_m3": d_after,
                                "material_id_name": mid,
                                # ── 【R9 修复】读回证据固化进返回体 ────────
                                # 凭据签发/材料防线都靠这两个字段判断
                                # "写进去的"与"读回来的"是否同一材料。
                                "readback_material": _rb_name or None,
                                "sw_internal_matches_request": bool(
                                    _rb_match) if _rb_name else None,
                            })
                            result["density_kg_m3"] = d_after
                            result["density_changed"] = _dchg
                            # 刷新模型，保证质量属性立即可读
                            try:
                                self.model.ForceRebuild3(False)
                            except Exception:
                                pass
                            if not _dchg and d_after:
                                result["warnings"].append(
                                    "材料已写入(IdName=%s)但密度与之前相同" % mid)
                            # ── 【关键安全提示】非精确匹配必须明确告知 ──────
                            # "静默用错材料"比"报错"危险得多：6061 铝被设成钢，
                            # 密度差 3 倍，强度/寿命校核全部失效却无人察觉。
                            #
                            # ── 【Bug-C 修复】区分"同族别名"与"跨标准替代"──────
                            # 实测缺陷：请求 Q235（GB/T 700），SW 材料库无此牌号时
                            #   回退别名命中 "AISI 1020"（美标），凭据里只写
                            #   approximate_match=false —— 报告看起来像"精确匹配"，
                            #   而图纸要的是 GB 牌号，跨标准替代被静默掩盖。
                            #   （Q235 σy=235 vs AISI 1020 冷拔 σy=350，差 49%。）
                            # 现在：比对"用户请求名"与"实际生效名"的【标准体系】，
                            #   跨体系（GB ↔ ASTM/AISI/EN/JIS）一律显式标记
                            #   cross_standard=true 并给出警告，供材料防线与
                            #   人工复核拦截。
                            # 说明：跨标准判定已在【判成功之前】完成并 continue，
                            #   走到这里说明 _cross_std 为 False（或调用方显式
                            #   用 allow_cross_standard=True 放行）。
                            if level != "exact" or _cross_std:
                                result["approximate_match"] = True
                                if _cross_std:
                                    result["cross_standard"] = True
                                    result["cross_standard_note"] = _std_note
                                    result["cross_standard_allowed"] = True
                                result["warning"] = (
                                    "⚠️ 材料名 %r 在库 %r 中没有精确匹配，"
                                    "已选用最接近的 %r（匹配级别=%s）。%s"
                                    "如果这不是你要的材料，请用 "
                                    "swapi.list_materials(%r) 查看库中可用名称后重新指定。"
                                    % (mname, dbn, real, level,
                                       (_std_note + " ") if _std_note else "", dbn))
                                result["warnings"].append(result["warning"])
                            # 期望密度与实际密度交叉校验
                            if want_density and d_after:
                                _rel = abs(d_after - want_density) / float(want_density)
                                result["density_error_pct"] = round(_rel * 100, 2)
                                if _rel > 0.20:
                                    result["density_mismatch"] = (
                                        "实际密度 %.0f 与 %s 的参考值 %.0f 相差 %.0f%%，"
                                        "请确认材料是否正确"
                                        % (d_after, name, want_density, _rel * 100))
                                    result["warnings"].append(result["density_mismatch"])
                            return result
                        tried.append("SetMaterialPropertyName2('',%r,%r) -> 未生效"
                                     % (dbn, real))
                    except Exception as e:
                        tried.append("SetMaterialPropertyName2('',%r,%r) -> %r"
                                     % (dbn, real, e))

                    # ── 回退路径：实体级 SetMaterialProperty（部分版本可用）──
                    try:
                        rc = body.SetMaterialProperty(cfg, dbn, real)
                        if rc == 0:
                            try:
                                self.model.ForceRebuild3(False)
                            except Exception:
                                pass
                            time.sleep(0.15)
                            d_after = dens_now()
                            result.update({
                                "ok": True,
                                "method": "IBody2.SetMaterialProperty",
                                "applied_name": real, "database_name": dbn,
                                "density_after_kg_m3": d_after,
                                "error_code": 0,
                            })
                            result["density_kg_m3"] = d_after
                            result["density_changed"] = bool(
                                d_before and d_after and abs(d_after - d_before) > 1)
                            return result
                        result["error_code"] = rc
                    except Exception as e:
                        tried.append("SetMaterialProperty(%r,%r,%r) -> EXC %r"
                                     % (cfg, dbn, real, e))

        # ── 6. 全部失败：给出可执行的诊断（绝不静默）─────────────────────
        # 【R3 修复】全部候选被拒 → 最后再确保一次回滚：零件材质必须回到
        #   调用前的状态，绝不能停在"最后一个被拒的候选"上。
        _last_restore = _restore_before_material("all candidates rejected")
        result["restored_material"] = _last_restore
        if _restore_log:
            result["material_restore_log"] = _restore_log[-10:]
        result["material_left_unchanged"] = bool((_last_restore or {}).get("ok"))
        result["warnings"] = tried
        _rej = result.get("rejected_candidates") or []
        _xstd = result.get("cross_standard_rejected") or []
        _rbm = result.get("readback_mismatch") or []
        # ══ 【R9 修复】三类"必须显式拒绝"的失败原因，各自给可操作路径 ══════
        # ① 跨标准替代（GB→ASTM/EN/JIS）—— BUG-55
        # ② 读回不一致（SW 内部材质 ≠ 请求材质）—— BUG-30/56，33/35 件的直接原因
        # ③ 密度/家族不符 —— 原有的 BUG-05 拦截
        # 三类都不能靠"看起来像"放过，否则凭据与实物必然脱节。
        if _xstd or _rbm:
            _parts = []
            if _xstd:
                _parts.append(
                    "跨标准替代被拒 %d 次：%s"
                    % (len(_xstd),
                       "; ".join("%s→%s" % (c.get("requested"), c.get("candidate"))
                                 for c in _xstd[:4])))
            if _rbm:
                _parts.append(
                    "读回不一致 %d 次：%s"
                    % (len(_rbm),
                       "; ".join("%s(实际=%s)" % (c.get("candidate"),
                                                  c.get("sw_internal"))
                                 for c in _rbm[:4])))
            result["error"] = (
                "无法把 %r 真正写进 SolidWorks：%s。\n"
                "这两类情形【必须显式失败】，不允许静默近似 —— 否则图纸写"
                "国标牌号、零件里却是另一种材料，质量/强度/寿命校核全部失效。"
                % (name, "；".join(_parts)))
            result["hint"] = _gb_material_advice(name)
            result["material_mismatch_rejected"] = True
            result["rejected_cross_standard"] = bool(_xstd)
            result["rejected_readback_mismatch"] = bool(_rbm)
            # 注意：这里【不提前 return】—— 下面还有 Bug15 的"自定义材料降级"
            #   路径（写自定义材质名 + 密度），它是国标牌号的正规出路之一。
            #   若它成功，会把 ok 置 True 并 pop 掉 error；若也失败，
            #   上面这段可读的 error/hint 就是最终答复。
            self._warn("set_material: " + result["error"])
        elif _rej:
            # ── 【BUG-05 二次修复】把"因材质不符被拒"的原因讲清楚 ─────────
            # 这是最容易被误读成"工具坏了"的情形：库里确实有名字相近的材料，
            # 但它是【另一种材质】（如要求铝却只有叫得相近的钢）——
            # 拒绝它才是正确行为，必须明确说明，并给出可执行的下一步。
            result["error"] = (
                "没有找到与 %r 材质相符的材料：尝试了 %d 个候选，"
                "其中 %d 个因【密度/材质家族不符】被主动拒绝（避免静默换材）。\n"
                "被拒候选：%s\n"
                "期望密度约 %s kg/m³（%s 族）。"
                % (name, len(tried), len(_rej),
                   "; ".join("%s(%.0f)" % (c["name"], c.get("density_kg_m3") or 0)
                             for c in _rej[:4]),
                   want_density if want_density else "未知",
                   _material_family(want_density) or "?"))
            result["hint"] = (
                "请在 SolidWorks 材质库中确认是否存在该材料，"
                "或用 swapi.list_materials('SolidWorks Materials') 查看准确名称后传入。\n"
                # ── 【P0-3 修复·误导提示】不要推荐实测无效的 density= 兜底 ──
                # 实测（子代理全量赋材矩阵）：对 SW 库里不存在的牌号，
                #   set_material(name, density=ρ) 仍然失败、零件密度停在 1000。
                #   旧提示把它当作可行出路，会把用户引向死路。
                # 真正可执行的出路只有两条（下面如实列出）。
                "【注意】density= 参数只是「材质库找不到时的密度兜底」，"
                "对 SW 材质库中【根本不存在】的牌号【实测无效】（仍会失败）。\n"
                "可执行的出路：① 用 set_custom_material() 把该牌号"
                "（含 E/ν/σy/σb/ρ）登记进 SolidWorks 后再赋材；"
                "② 用 swapi.list_materials() 查 SW 库中语义最接近的真实材质，"
                "并在报告中如实标注跨标准替代。")
            result["material_mismatch_rejected"] = True
        else:
            result["error"] = (
                "无法设置材料 %r。已尝试 %d 种组合（配置=%r，库=%s）。\n"
                "错误码含义：1=材料名不在该库，5=配置名无效，6=库名无效。\n"
                "可用材料名请从材质库读取：swapi.list_materials(\"SolidWorks Materials\")"
                % (name, len(tried), cfg, db_names[:3]))
            result["hint"] = ("SW 材质名必须与 .sldmat 中的 <material name=...> 完全一致；"
                              "中文库常用名：普通碳钢/合金钢/灰铸铁/1023 碳钢板 (SS)")
        # ══ 【Bug15 修复】SW 库里没有的打印材料 → 自定义材料降级 ═════════
        # 实测：PLA 在内置表里存在（密度 1240 / E 3500 / σy 55），
        #   但 SolidWorks 官方材质库【不含 PLA】这类 FDM 打印材料，
        #   于是 16 种 (库×名) 组合全部失败 → new_part(material="PLA")
        #   赋材失败、density=None，PLA 零件走不了材料防线（测试部 Bug15）。
        # 修复：所有候选都失败时，若内置表/自定义库【确有该材料定义】，
        #   则写入【自定义材料名 + 密度】，让零件拿到正确密度与力学参数。
        #   密度正确 → 质量/强度/寿命校核可信，比"赋材失败"安全得多。
        try:
            _ok_m, _info_m, _why_m = is_valid_material(name)
            _d_def = want_density or (_info_m or {}).get("density")
            if _ok_m and _d_def:
                _wrote = False
                for _dbn in db_names:
                    for _nm_try in (str(name),
                                    (_info_m or {}).get("canonical") or str(name)):
                        try:
                            # 【R11 修复】方法，显式加括号（IPartDoc L47898）
                            _com_get(self.model, "SetMaterialPropertyName2",
                                     "", _dbn, _nm_try)
                            time.sleep(0.15)
                            _d_now = dens_now()
                            # ══ 【R9 修复】自定义材料降级同样必须【读回校验】══
                            # 这条是国标牌号的正规出路，如果它也不回读就判成功，
                            # 等于把"33/35 件凭据与实物脱节"换个地方重演一遍。
                            _rb2 = _read_back_material(self)
                            _rb2_name = str(_rb2.get("name") or "").strip()
                            # 判据：回读 == 请求名（sw_internal_matches_request
                            #   的字面语义）**或** == 本次写入用的规范名
                            #   （custom 库常把 Q235 规范化成 "Q235（碳素结构钢）"，
                            #    那仍是同一材料，不该误判）。两者都不等 → 拒绝。
                            _rb2_ok = (_material_names_match(_rb2_name, name)
                                       or _material_names_match(_rb2_name, _nm_try))
                            if _rb2_name and not _rb2_ok:
                                tried.append(
                                    "custom-fallback %s/%s 读回不符：请求 %r，"
                                    "SW 实际 %r"
                                    % (_dbn, _nm_try, name, _rb2_name))
                                continue
                            if _d_now and abs(float(_d_now) - float(_d_def)) / float(_d_def) <= 0.25:
                                result.update({
                                    "ok": True,
                                    "method": "SetMaterialPropertyName2(custom-fallback)",
                                    "applied_name": _nm_try,
                                    "database_name": _dbn,
                                    "density_kg_m3": float(_d_now),
                                    "match_level": "custom",
                                    "custom_material": True,
                                    "readback_material": _rb2_name or None,
                                    "sw_internal_matches_request": bool(_rb2_ok)
                                    if _rb2_name else None,
                                })
                                result.pop("error", None)
                                result.pop("hint", None)
                                result["warnings"].append(
                                    "SolidWorks 材质库无 %r，已按内置定义写入自定义材料"
                                    "（密度 %.0f kg/m³）—— 材料身份与力学参数仍可追溯"
                                    % (name, float(_d_now)))
                                try:
                                    self.model.ForceRebuild3(False)
                                except Exception:
                                    pass
                                _wrote = True
                                break
                        except Exception as _e_cf:
                            tried.append("custom-fallback %s/%s: %r"
                                         % (_dbn, _nm_try, _e_cf))
                    if _wrote:
                        break
                if _wrote:
                    return result
                result["hint"] = (result.get("hint") or "") + (
                    "  【Bug15】该材料在 SW 库中不存在，且自定义写入也失败；"
                    "可先用 save_custom_material() 登记，或在 SW 中手工新建该材料。")
        except Exception as _e_b15:
            tried.append("Bug15 自定义降级异常: %r" % (_e_b15,))
        # ── 【R3 修复·收尾】自定义降级路径也可能再次写脏模型 ────────────────
        # 它会把请求名再写一次；若 SW 又静默替换，必须回到写入前的材质，
        # 否则"函数失败、零件属性栏却是错误牌号"仍然成立（复检现场 1.2312）。
        if not result.get("ok"):
            _fin = _restore_before_material("final guard")
            result["restored_material"] = _fin
            result["material_left_unchanged"] = bool((_fin or {}).get("ok"))
            if _restore_log:
                result["material_restore_log"] = _restore_log[-10:]
        self._warn("set_material: " + result["error"])
        return result

    def assign_material(self, name, database=None, density=None,
                        allow_cross_standard=False):
        """【BUG-05 二次修复】显式赋材质的对外别名（供建模脚本直接调用）。

        背景：测试脚本调用 `m.assign_material("6061-T6")` 报
          `NO SUCH METHOD` —— 本类原先只有 set_material，没有这个名字。
        为避免"文档/脚本写 assign_material、实现叫 set_material"的分裂，
        这里提供同义方法（完全等价，直接委托）。

        典型用法：
            m = swapi.new_part()            # 默认材质可后续覆盖
            m.begin_sketch("Front Plane"); m.rect(0,0,350,60)
            m.end_sketch(); m.extrude(40)
            m.assign_material("6061-T6")    # 显式指定铝
            assert m.get_material()["density_kg_m3"] == 2700

        Returns:
            {ok, material, applied_name, density_kg_m3, match_level,
             rejected_candidates?, error?, hint?}
        """
        return self.set_material(name, database=database, density=density,
                                 allow_cross_standard=allow_cross_standard)

    def set_custom_material(self, name, e_mpa=None, yield_mpa=None, uts_mpa=None,
                            density_kg_m3=None, poissons_ratio=None):
        """【Bug-29 修复】直写"自定义材料"（不依赖 SW 官方材质库）。

        为什么需要：SW 2025 材质库【没有 PETG / PLA / Nylon(PA12)】等 3D 打印
        常用材料。原方案只能"用 ABS 近似 + 手改密度"，导致：
          ① 材料属性不一致（Nylon 齿轮按 ABS 校核）；
          ② 工程图材料栏写 ABS 而非真实打印材料，加工者会打错料；
          ③ physics 读 SW 材料属性时拿到错误的 E/屈服。

        本方法的行为：
          1) 把材料属性登记进 custom_materials.json（可复用、可审计）；
          2) 尝试用 SetMaterialPropertyName2 写入材料名（SW 库没有该牌号时
             这一步可能失败 —— 失败不算致命，属性仍由 custom 库承载）；
          3) 无论 SW 库是否认得，都把解析出的密度/E/屈服放进返回值，
             供 physics 校核与工程图材料栏直接使用（单一事实来源）。

        典型用法：
            m.set_custom_material("PETG", e_mpa=2000, yield_mpa=40,
                                  density_kg_m3=1270)

        Returns:
            {ok, applied_name, density_kg_m3, e_mpa, yield_mpa, uts_mpa,
             source, sw_library_write, registry}
        """
        nm = str(name or "").strip()
        if not nm:
            return {"ok": False, "error": "材料名不能为空"}
        # ① 属性解析：显式参数 > 内置表/自定义库
        props = resolve_material_props(nm) or {}
        _e = e_mpa if e_mpa is not None else props.get("e_mpa")
        _y = yield_mpa if yield_mpa is not None else props.get("yield_mpa")
        _u = uts_mpa if uts_mpa is not None else props.get("uts_mpa")
        _d = density_kg_m3 if density_kg_m3 is not None else props.get("density_kg_m3")
        _nu = poissons_ratio if poissons_ratio is not None else props.get("poissons_ratio")
        # ② 登记自定义库（幂等）
        reg = save_custom_material(nm, e_mpa=_e, yield_mpa=_y, uts_mpa=_u,
                                   density_kg_m3=_d, poissons_ratio=_nu,
                                   note="由 set_custom_material 写入")
        # ③ 尝试写进 SW（失败不致命 —— 属性已由 custom 库承载）
        sw_write = None
        try:
            if _d:
                sw_write = self.set_material(nm, density=float(_d))
            else:
                sw_write = self.set_material(nm)
        except Exception as ex:
            sw_write = {"ok": False, "error": repr(ex)}
        # ── 【Bug① 修复】必须如实区分"请求的材质"与"SW 内部实际生效的材质" ──
        # 现场缺陷（竞速小车任务）：用户确认 PETG（ρ1270 / σy≈40），但
        #   SW 材质库【没有 PETG】（只有 PET，ρ1420 / σy≈55）。
        #   set_custom_material("PETG", ...) 只写了 material.json 凭据签名，
        #   完全没动 SW 零件内部材质 —— 结果：
        #     · material.json 签名有效，防线①在签名层通过；
        #     · 但 .sldprt 里真实材质仍是 PET(1420)，打开零件属性栏显示 PET，
        #       用户拿零件直接打印/加工会被误导，打印件力学性能以 SW 为准。
        #   原返回体只有 applied_name="PETG"（那是"我们登记的名字"，不是
        #   SW 里真实生效的名字），调用方无从发现这个落差。
        # 修复：单独回读 SW 内部材质名与 SW 侧密度，与请求值并列记录；
        #   不一致时写 warning 字段（不阻断流程，但必须可见、可审计）。
        _sw_internal = None
        _sw_density = None
        try:
            _gm = self.get_material() or {}
            _sw_internal = _gm.get("name") or (sw_write or {}).get("applied_name")
            _sw_density = _gm.get("density_kg_m3")
        except Exception:
            _sw_internal = (sw_write or {}).get("applied_name")
        if not _sw_internal:
            try:
                _sw_internal = self.model.GetMaterialPropertyName2("", "")
            except Exception:
                _sw_internal = None
        _req_n = str(nm).strip()
        _int_n = str(_sw_internal or "").strip()
        # ── 【S-14 / A-30 修复·第二处实例】名字比对必须走统一归一化 ──────────
        # 原实现自带一套 endswith / split 比对：
        #   _material_names_match() 是【两套口径】。实测缺陷（真实运行 T04c C4）：
        #     set_custom_material("PETG") 落地后 SW 回读
        #       "DSH_PETG|PETG|9001"  → split("|")[-1] = "9001" ≠ "petg"
        #     于是 sw_internal_matches_request=false，而本函数仍然返回 ok:true。
        #   这正是 S-15「假 ok」：凭据自报成功、底层不一致。
        # 现在统一用 _material_names_match()（候选集合比对），
        #   并且【ok 不再无条件为 true】—— 见下方 result 的 ok 计算。
        _matches = bool(_int_n) and _material_names_match(_int_n, _req_n)
        _dens_ok = True
        if _d is not None and _sw_density:
            try:
                _dens_ok = (abs(float(_sw_density) - float(_d))
                            / float(_d) <= 0.05)
            except Exception:
                _dens_ok = True
        result = {
            # ── 【S-15 修复·假 ok】底层不一致时不得返回 ok:true ─────────────
            # 实测缺陷：返回 ok:true 的同时 sw_internal_matches_request=false，
            #   调用方（以及凭据签发链）只看 ok 就当成"材料已施加成功"。
            # 现在：写进 SW 的名字回读不一致 → ok=False，并给出可执行出路；
            #   只有"确实生效"才算成功。密度也参与判定（>5% 视为没写进去）。
            "ok": bool(_matches and _dens_ok) if _int_n else False,
            "requested_material": _req_n,      # 【Bug①】用户请求的材质（凭据口径）
            "sw_internal_material": _sw_internal,   # 【Bug①】SW 零件内部真实材质
            "sw_internal_density_kg_m3": _sw_density,
            "sw_internal_matches_request": bool(_matches),
            "applied_name": nm,
            "density_kg_m3": _d,
            "e_mpa": _e,
            "yield_mpa": _y,
            "uts_mpa": _u,
            "poissons_ratio": _nu,
            "source": "custom_materials",
            "sw_library_write": (sw_write or {}).get("ok"),
            "sw_library_note": (None if (sw_write or {}).get("ok") else
                                "SW 官方库无该牌号（预期行为，尤其 PETG/Nylon）；"
                                "材料属性以本返回值/custom_materials.json 为准"),
            "registry": reg,
        }
        if not _matches or not _dens_ok:
            # 【Bug①】不阻断，但必须显式可见：凭据层认 PETG，SW 内部却是 PET。
            _detail = ("【Bug①】SW 零件内部材质与请求不一致："
                       "请求 %r（ρ=%s），SW 实际 %r（ρ=%s）。"
                       "材料凭据按请求材质记录，但 .sldprt 打开后属性栏显示的"
                       "是 SW 实际材质 —— 直接拿零件打印/加工请以 SW 实际材质为准，"
                       "或先用 set_material() 指定 SW 库中存在的近似牌号。"
                       % (_req_n, _d, _int_n or "<未知>", _sw_density))
            result["warning"] = _detail
            result["warnings"] = list(result.get("warnings") or []) + [_detail]
            self._warn(_detail)
        else:
            result["warning"] = None
        # 让 physics 与出图能拿到"实际应用的材料"
        self.material_result = dict(result)
        return result

    def get_material(self):
        """读取当前零件材料名与密度（用于验收：确认不再是默认水密度）。"""
        out = {"ok": False, "name": None, "density_kg_m3": None}
        try:
            out["name"] = self.model.GetMaterialPropertyName2("", "")
        except Exception:
            try:
                out["name"] = self.model.MaterialIdName
            except Exception:
                pass
        try:
            mp = self.massprops()
            if mp.get("ok"):
                out["density_kg_m3"] = mp.get("density_kg_m3")
                out["mass_kg"] = mp.get("mass_kg")
                out["volume_mm3"] = mp.get("volume_mm3")
                out["ok"] = True
        except Exception:
            pass
        return out

    def verify_density(self, expected=None, tol=0.15):
        """【Bug3 验收】检查密度是否合理（识别"恒为1000"的水密度异常）。

        Args:
            expected: 期望密度 kg/m³；None 则只做"是否为默认水密度"检测
            tol:      允许相对误差（默认 15%）

        Returns:
            dict: {ok, actual, expected, reason}
        """
        mp = self.massprops()
        if not mp.get("ok"):
            return {"ok": False, "actual": None, "expected": expected,
                    "reason": "质量属性读取失败: %s" % mp.get("error")}
        actual = mp.get("density_kg_m3")
        if actual is None:
            return {"ok": False, "actual": None, "expected": expected,
                    "reason": "密度为 None"}
        # 默认水密度检测（SW 未设材料时 ≈1000）
        if abs(actual - 1000.0) <= 60.0 and (expected is None or abs(expected - 1000.0) > 60.0):
            return {"ok": False, "actual": actual, "expected": expected,
                    "reason": ("密度 %.1f kg/m³ 接近默认值 1000（水）——"
                               "零件很可能未指定材料，质量属性不可信" % actual)}
        if expected:
            rel = abs(actual - expected) / float(expected)
            if rel > tol:
                return {"ok": False, "actual": actual, "expected": expected,
                        "reason": ("密度 %.1f 与期望 %.1f 偏差 %.1f%%（超容差 %.0f%%）"
                                   % (actual, expected, rel * 100, tol * 100))}
        return {"ok": True, "actual": actual, "expected": expected,
                "reason": "密度合理"}

    def massprops(self, safe=True):
        """质量属性数组顺序（2022 实测）:
        [cogX, cogY, cogZ, volume, surface_area, mass, Ixx, Iyy, Izz, Ixy, Ixz, Iyz]

        ── 【Bug-31 修复】GetMassProperties 失败/挂起时不再让调用方无路可走 ──
        原缺陷：总装房间因为"调用 massprops 容易卡死 SW"而【完全不敢调用】，
          改用"零件包围盒体积 × 密度"估算整车质量，得上限 4.875kg（远超 1.5kg
          目标）—— 包围盒把壳体薄壁/减重孔都算成实心，结果虚高不可信，
          会误导小屋做无效的轻量化修改。
        修复（两级，全部走【实体真实体积】而非包围盒）：
          ① 首选 GetMassProperties（无参属性），失败则退回 GetMassProperties(0)；
          ② 仍失败则【逐实体累加】GetBodies2 + body.GetMassProperties(0)[3]，
             得到真实体积后 × 材料密度换算质量。
          返回值新增 volume_source / mass_source，明确告知质量是怎么来的，
          调用方一眼可辨"是 SW 实测还是降级估算"。

        Args:
            safe: True 时失败不抛异常，改为返回 ok=False + 降级结果；
                  False 时保持旧行为（失败即返回 ok=False）。
        """
        out = {"ok": False}
        # ── ① 主路径：GetMassProperties ──────────────────────────────────
        # ── 【R11 修复】类型库 IModelDoc2.GetMassProperties（L38295）是
        #   **无参方法**：`def GetMassProperties(self):`。
        #   旧代码第一句是 `lambda: self.model.GetMassProperties`（不加括号），
        #   拿到的是方法对象 → isinstance(tuple) 恒 False → 静默落到降级路径，
        #   每次读质量属性都白走一遍 body 累加，还查不出原因。
        #   第二句 `GetMassProperties(0)` 也多余（IModelDoc2 版无参，
        #   带参的是 IModelDocExtension 的另一个方法，见 L41895）。
        #   现在统一走 _com_get：按类型库判定"是方法"，一定加括号。
        mp = None
        try:
            _r = _com_get(self.model, "GetMassProperties")
            if _r is not None and isinstance(_r, (tuple, list)) and len(_r) >= 6:
                mp = tuple(_r)
        except ComMemberError as _e_mp:
            out["com_error"] = str(_e_mp)
        except Exception:
            pass
        if mp is not None:
            try:
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
                    "volume_source": "GetMassProperties",
                    "mass_source": "GetMassProperties",
                }
            except Exception as e:
                out["error"] = "GetMassProperties 解析失败: %r" % (e,)

        # ── ② 降级路径：逐实体真实体积累加（【Bug-31】核心新增）───────────
        # 注意：这是【实体体积】不是包围盒体积 —— 壳体薄壁/减重孔会被正确
        #   扣除，因此估算结果可信（这正是原实现缺失的能力）。
        try:
            vol_mm3, area_mm2 = self._sum_body_volume_area()
            if vol_mm3:
                _dens = None
                try:
                    _mr = self.material_result if isinstance(self.material_result, dict) else {}
                    _nm = _mr.get("applied_name") or _mr.get("material") \
                        or getattr(self, "_pending_material", None)
                    _props = resolve_material_props(_nm) if _nm else None
                    if _props and _props.get("density_kg_m3"):
                        _dens = float(_props["density_kg_m3"])
                except Exception:
                    _dens = None
                if _dens is None:
                    # 退回"质量/体积"反算；再不行用 1000（水）并显式告警
                    try:
                        # 【R11 修复】IModelDoc2.GetMassProperties 是【无参方法】
                        #   （类型库 L38295）。旧代码传了 (0) —— 那是
                        #   IModelDocExtension.GetMassProperties(Accuracy,Status)
                        #   （L41895）的签名，混用会抛 com_error「参数个数不正确」。
                        _mp2 = _com_get(self.model, "GetMassProperties")
                        _v2 = float(_mp2[3]) if _mp2 else 0.0
                        _m2 = float(_mp2[5]) if _mp2 else 0.0
                        _dens = (_m2 / _v2) if _v2 else None
                    except ComMemberError:
                        _dens = None
                    except Exception:
                        _dens = None
                if _dens is None or _dens <= 0:
                    _dens = 1000.0
                    out["density_warning"] = ("无法解析材料密度，按 1000 kg/m³ 估算；"
                                              "请先 set_material 或 set_custom_material")
                return {
                    "ok": True,
                    "volume_mm3": vol_mm3,
                    "surface_area_mm2": area_mm2,
                    "mass_kg": vol_mm3 * 1e-9 * _dens,
                    "density_kg_m3": _dens,
                    "center_of_mass_mm": None,
                    "volume_source": "sum(IBody2.GetMassProperties)[实体真实体积]",
                    "mass_source": "volume × density（非包围盒，Bug-31 降级路径）",
                    "note": ("GetMassProperties 不可用，已改用逐实体真实体积 × 密度；"
                             "结果为【实体体积】估算（薄壁/减重孔已正确扣除），"
                             "不是包围盒上限。"),
                }
        except Exception as e:
            out["error"] = (out.get("error") or "") + " | 实体体积降级失败: %r" % (e,)

        if not out.get("error"):
            out["error"] = "GetMassProperties 返回不可用，且无法取到实体体积"
        if safe:
            out["hint"] = ("可尝试：① 确认零件已有实体；② 确认材料已设置（密度非 1000）；"
                           "③ 用 body.GetMassProperties(0) 逐实体读取。")
        return out

    def _sum_body_volume_area(self):
        """逐实体累加真实体积(mm³)与表面积(mm²)（【Bug-31】降级路径核心）。

        与"包围盒体积"的区别：本方法读取的是实体本身的质量属性，
        薄壁、减重孔、内腔都会被正确扣除，因此结果可用于质量/惯性估算。
        Returns: (volume_mm3, surface_area_mm2)
        """
        total_v, total_a = 0.0, 0.0
        got = False
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
        except Exception:
            return 0.0, 0.0
        for b in bl:
            try:
                props = b.GetMassProperties(0) or []
            except Exception:
                continue
            if len(props) >= 5:
                try:
                    total_v += float(props[3]) * 1e9   # m³ -> mm³
                    total_a += float(props[4]) * 1e6   # m² -> mm²
                    got = True
                except Exception:
                    continue
        return (total_v if got else 0.0), (total_a if got else 0.0)

    def massprops_entity(self):
        """【Bug-31】只走"实体真实体积"的质量估算（供总装房间安全调用）。

        与 massprops(safe=True) 的区别：本方法【不碰】GetMassProperties，
        只逐实体读 body.GetMassProperties(0)，因此在"调用总质量属性会卡死 SW"
        的版本上更安全；结果同样已扣除薄壁与减重孔（非包围盒）。
        """
        vol_mm3, area_mm2 = self._sum_body_volume_area()
        if not vol_mm3:
            return {"ok": False, "error": "无法取到实体体积（零件可能无实体）"}
        _dens = None
        try:
            _mr = self.material_result if isinstance(self.material_result, dict) else {}
            _nm = _mr.get("applied_name") or _mr.get("material") \
                or getattr(self, "_pending_material", None)
            _props = resolve_material_props(_nm) if _nm else None
            if _props and _props.get("density_kg_m3"):
                _dens = float(_props["density_kg_m3"])
        except Exception:
            _dens = None
        if not _dens or _dens <= 0:
            _dens = 1000.0
        return {
            "ok": True,
            "volume_mm3": vol_mm3,
            "surface_area_mm2": area_mm2,
            "density_kg_m3": _dens,
            "mass_kg": vol_mm3 * 1e-9 * _dens,
            "method": "sum(IBody2.GetMassProperties)[实体真实体积] × density",
            "note": "非包围盒估算 —— 薄壁/减重孔已扣除（Bug-31）。",
        }

    def export_pdf(self, path):
        """导出 PDF（【Bug-45/46 修复】多方法兜底 + 自动补 .pdf 后缀）。

        ── Bug-45 修复：自动补后缀 ──────────────────────────────────────────
        原缺陷：export-pdf <图纸.slddrw>（未带 .pdf）→ SaveAs3 返回 rc=1，
          用户必须自己记得加 .pdf。现自动补全。
        ── Bug-46 修复：doc.ExportToPDF 动态分发失败 ────────────────────────
        原缺陷：SW 原生 ExportToPDF 在 win32com 动态分发下不可调用
          （<unknown>.ExportToPDF），导致"只能走 SaveAs3"。
        修复：按可靠性依次尝试 4 条路径，并如实回传用了哪条：
          ① SaveAs3（实测最稳，与 export-pdf 现有行为一致）；
          ② SaveAs2（旧接口回退）；
          ③ ExportToPDF（部分版本/早期绑定可用）；
          ④ ExportPDF（个别版本命名差异）。
        全部失败时给出明确 error，不再静默返回 rc=1。
        """
        p = str(path or "")
        if p and not p.lower().endswith(".pdf"):
            p = p + ".pdf"
        out = {"ok": False, "path": p, "methods_tried": []}
        try:
            if p and not os.path.isdir(os.path.dirname(p) or "."):
                try:
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                except Exception:
                    pass
            # ① SaveAs3
            try:
                rc = self.model.SaveAs3(p, 0, 0)
                out["methods_tried"].append(("SaveAs3", rc))
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    out.update({"ok": True, "method": "SaveAs3", "rc": rc})
                    return out
            except Exception as e:
                out["methods_tried"].append(("SaveAs3", repr(e)))
            # ② SaveAs2
            try:
                rc2 = self.model.SaveAs2(p, 0, 0)
                out["methods_tried"].append(("SaveAs2", rc2))
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    out.update({"ok": True, "method": "SaveAs2", "rc": rc2})
                    return out
            except Exception as e:
                out["methods_tried"].append(("SaveAs2", repr(e)))
            # ③ ExportToPDF / ④ ExportPDF
            for _m in ("ExportToPDF", "ExportPDF"):
                try:
                    _fn = getattr(self.model, _m, None)
                    if _fn is None:
                        continue
                    _fn(p, 0)
                    out["methods_tried"].append((_m, "called"))
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        out.update({"ok": True, "method": _m})
                        return out
                except Exception as e:
                    out["methods_tried"].append((_m, repr(e)))
            out["error"] = ("所有 PDF 导出路径均失败（SaveAs3/SaveAs2/"
                            "ExportToPDF/ExportPDF）。")
            out["hint"] = ("① 确认当前活动文档是工程图/零件且已保存；"
                           "② 确认目标目录可写；"
                           "③ 若为工程图，可先用 drawing 命令生成 .slddrw 再导出。")
            return out
        except Exception as e:
            out["error"] = "export_pdf 异常: %r" % (e,)
            return out

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
        """把 SolidWorks 主窗口调到前台（保持最大化），隐藏欢迎页。

        【D2 修复】无头模式（DSH_SW_VISIBLE=0）下不做任何置前/显隐操作，
        避免把排队建模时的窗口重新弹出来、打断 COM。
        """
        if HEADLESS_MODE:
            # 官方配方：无头时不要重新把窗口拉出来；但 ActivateDoc 之类的
            # 调用仍可能让 SW 自己变可见 —— 用 Frame.KeepInvisible 挡住。
            try:
                _frame = self.sw.Frame()
                if _frame is not None:
                    _frame.KeepInvisible = True
            except Exception:
                pass
            return self
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

    def screenshot(self, path=None, for_vision=False, tag=None, for_part=None):
        """对 SolidWorks 视图截图保存为 PNG。

        ── 【C04 修复 · 2026-10-29 真机实测】默认走 SaveBMP，不依赖前台窗口 ──
        实测（SW2025 SP5.0，pywin32 静态绑定）：把 SW 主窗口
          user32.ShowWindow(hwnd, 6)（最小化）后，
          model.SaveBMP(path, 1600, 900) 仍返回 True，0.1 秒写出 4320054 字节
          （1600*900*3 = 4320000，+54 字节 BMP 头 = 4320054，完全吻合），
          文件前两字节 42 4D = "BM"（合法 BMP）。
        结论：SaveBMP 是真正的【后台渲染】，与窗口是否前台/最小化无关。

        原实现走"抓屏 + 强制激活窗口"，在 DSH Web GUI 抢前台时必然失败
        （现场症状：visual_check 截图失败、stdout 为空、抓到的是 DSH 画面）。
        现在改为：
          ① 首选 SaveBMP（后台、稳定、不需要 SetForegroundWindow）；
          ② 只有 SaveBMP 不可用（老版本/无 SaveBMP）才回落到原抓屏路径。

        Args:
            path: 截图保存路径，默认保存到工具目录
            for_vision: 是否用于 Vision 识别，是则保存到 DSH-Check 目录便于前端读取
            tag: 【A-91 修复】文件名标签（如零件名 / 房间名），用于区分多次截图
            for_part: 【A-91 修复】被拍零件的路径或名字；未给 tag 时用它推导标签

        ── 【A-91 修复 · 截图不可归因】──────────────────────────────────────
        原缺陷：默认文件名写死（纯名字，无任何前缀），一次任务里多次截图互相覆盖。
          · 一次任务里多次截图【互相覆盖】，只剩最后一张；
          · 交付验收时无法回答"这张图拍的是哪个零件/哪个房间/第几次"；
          · 多小屋并行（模式 D）时，两个房间的截图会把对方覆盖掉，
            总装复核拿到的可能是别的房间的证据 —— 属证据链断裂。
        现在：默认文件名 = <标签>_<时间戳>_<毫秒>_<pid>.png（标签取 tag / for_part /
          当前活动文档名，取不到才退回 live）。显式传 path 时【完全不变】，
          保持对所有既有调用点的向后兼容。
        """
        if path is None:
            # 【A-91】默认文件名必须【可归因且不互相覆盖】
            _tag = str(tag or "").strip()
            if not _tag and for_part:
                _tag = os.path.splitext(os.path.basename(str(for_part)))[0]
            if not _tag:
                try:
                    _t = getattr(self, "model", None)
                    _nm = _t.GetTitle() if _t is not None else None
                    if _nm:
                        _tag = os.path.splitext(os.path.basename(str(_nm)))[0]
                except Exception:
                    _tag = ""
            _tag = "".join(c for c in _tag if c.isalnum() or c in "_- .")
            _tag = _tag.strip().replace(" ", "_")[:60] or "unnamed"
            import time as _ts
            _name = "%s_%s_%03d_%d.png" % (
                _tag, _ts.strftime("%Y%m%d-%H%M%S"),
                int((_ts.time() % 1) * 1000), os.getpid())
            if for_vision:
                # 供 DSH 前端识图插件读取（【连接自检修复】不再硬编码作者桌面）
                path = os.path.join(vision_shot_dir(), _name)
            else:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _name)

        # ══ 首选：SaveBMP 后台渲染（C04）══════════════════════════════════
        _shot = self._screenshot_via_savebmp(path)
        if _shot.get("ok"):
            return _shot
        # SaveBMP 不可用 → 记下原因，回落到抓屏（此时才需要前台窗口）
        _savebmp_error = _shot.get("error")

        try:
            import ctypes
            from ctypes import wintypes
            import time
            user32 = ctypes.windll.user32
            self.bring_to_front()
            target, _ = self._find_sw_windows()
            if target is None:
                return {"ok": False, "error": "SolidWorks main window not found"}

            # ── 【Bug7 修复】必须真正激活 SW 窗口，否则抓到的是 DSH GUI 旧画面 ──
            # 原实现只调 setForegroundWindow 且不校验 —— Windows 前台锁会让它
            # 静默失败，于是抓屏抓到屏幕上原本在前面的 DSH Web GUI，
            # 却仍返回 ok=true，被误当作"当前 SW 窗口"的验收证据。
            act = _force_activate_sw_window(target, tries=3)
            if not act.get("ok"):
                # 自检开关：SWAPI_ALLOW_UNVERIFIED_SHOT=1 时才允许在未激活情况下截图
                _allow = (os.environ.get("SWAPI_ALLOW_UNVERIFIED_SHOT") or "").strip() in ("1", "true", "yes")
                if not _allow:
                    return {"ok": False,
                            "error": ("无法把 SolidWorks 窗口激活到前台，截图不可信（可能抓到 DSH GUI）。"
                                      "原因: %s" % act.get("reason")),
                            "activation": act,
                            "hint": ("请确认 SW 窗口未被最小化/未被其他窗口遮挡；"
                                     "或设 SWAPI_ALLOW_UNVERIFIED_SHOT=1 强制截图（但结果不可作验收依据）。")}
            time.sleep(0.6)   # 等窗口重绘完成，避免抓到过渡帧

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

            # ── 【Bug7 修复】截图后再校验一次前台仍是 SW，确保内容可信 ──────
            verified = False
            try:
                fg_after = user32.GetForegroundWindow()
                verified = bool(fg_after and int(fg_after) == int(target))
            except Exception:
                verified = act.get("ok", False)

            return {"ok": True, "path": path, "size": f"{w}x{h}",
                    "activated": bool(act.get("ok")),
                    "verified_foreground": verified,
                    "window_title": _window_title(target),
                    "method": "grab_screen",
                    "savebmp_error": _savebmp_error,
                    "note": ("截图取自当前置前的 SolidWorks 窗口"
                             "（SaveBMP 不可用，已回落到抓屏）"
                             if verified else
                             "⚠️ 截图时无法确认前台为 SW，内容可能不可信")}
        except Exception as e:
            return {"ok": False,
                    "error": f"screenshot failed: {e}",
                    "savebmp_error": _savebmp_error,
                    "hint": ("SaveBMP 后台渲染也失败了 —— 检查模型是否已打开、"
                             "以及 SW 是否处于可用状态。")}

    # ══ 【C04】SaveBMP 后台截图（不依赖前台窗口）══════════════════════
    def _screenshot_via_savebmp(self, path, width=1600, height=900):
        """用 ModelDoc2.SaveBMP 后台导出，再按需转成 PNG。

        真机实测要点（SW2025 SP5.0）：
          · 窗口最小化时依然成功 → 不需要 SetForegroundWindow / BringToFront；
          · SaveBMP 【忽略扩展名】，传 .png 也写出 BMP 字节（magic 42 4D）；
          · 输出大小 = width*height*3 + 54（BMP 头），可据此校验是否真写成功。

        Returns: {"ok": bool, ...}；不可用时 ok=False + error，由调用方回落抓屏。
        """
        try:
            _savebmp = getattr(self.model, "SaveBMP", None)
            if _savebmp is None:
                return {"ok": False, "error": "本版本 ModelDoc2 无 SaveBMP"}
            # SaveBMP 只会写 BMP → 先写临时 .bmp，再按目标扩展名转换
            _base, _ext = os.path.splitext(path)
            _bmp = (_base if _ext.lower() == ".bmp" else _base + ".bmp")
            _dir = os.path.dirname(os.path.abspath(_bmp))
            if _dir and not os.path.isdir(_dir):
                try:
                    os.makedirs(_dir, exist_ok=True)
                except Exception:
                    pass
            try:
                ok = bool(_savebmp(_bmp, int(width), int(height)))
            except Exception as _e:
                return {"ok": False, "error": "SaveBMP 调用异常: %r" % (_e,)}
            if not ok:
                return {"ok": False, "error": "SaveBMP 返回 False"}
            if not os.path.exists(_bmp):
                return {"ok": False,
                        "error": "SaveBMP 返回 True 但文件不存在: %s" % _bmp}
            _sz = os.path.getsize(_bmp)
            if _sz <= 54:
                return {"ok": False,
                        "error": "SaveBMP 写出的文件过小（%d 字节）" % _sz}
            # 校验 magic：BMP 必须是 42 4D ("BM")
            _magic = None
            try:
                with open(_bmp, "rb") as _f:
                    _magic = _f.read(2)
            except Exception:
                _magic = None
            if _magic != b"BM":
                return {"ok": False,
                        "error": "SaveBMP 输出不是合法 BMP（magic=%r）" % (_magic,)}

            _final = _bmp
            _converted = False
            _want_convert = _ext.lower() not in (".bmp", "")
            if _want_convert:
                if _bmp_to_png(_bmp, path):
                    _final = path
                    _converted = True
                    try:
                        os.remove(_bmp)
                    except Exception:
                        pass
            _note = "SaveBMP 后台渲染导出（不依赖前台窗口，窗口最小化也可用）"
            if _converted:
                _note += "；已转 PNG"
            elif _want_convert:
                _note += "；BMP 未能转 PNG（缺 Pillow？），保留 .bmp"
            return {"ok": True,
                    "path": _final,
                    "size": "%dx%d" % (int(width), int(height)),
                    "bytes": _sz,
                    "method": "SaveBMP+convert" if _converted else "SaveBMP",
                    "activated": False,
                    "verified_foreground": False,
                    "note": _note}
        except Exception as e:
            return {"ok": False, "error": "SaveBMP 路径异常: %r" % (e,)}

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
        # ── 【D3 修复·官方语义】CloseDoc("") 会关掉【当前活动文档】──────────
        # 官方原文（ISldWorks::CloseDoc）：'If Name is specified with an empty
        #   string ("") , then the active document is closed without saving.'
        #   → 并发场景下取不到 title 时传 ""，等于随机关掉别人的文档。
        # 现在：title 为空就【不关】，只回报告警，绝不盲关。
        if title:
            try:
                self.sw.CloseDoc(title)
            except Exception as _e_cd:
                return {"ok": False, "closed": None, "title": title,
                        "error": "CloseDoc 失败: %r" % (_e_cd,)}
            return {"ok": True, "closed": title}
        return {"ok": False, "closed": None, "title": None,
                "error": ("取不到文档标题，拒绝调用 CloseDoc —— "
                          "空字符串会关掉【当前活动文档】（可能属于别的房间）。")}

    # ---------- 基准面 / 草图 ----------
    _PLANE_VIEW = {
        "Front Plane": "*Front",
        "Top Plane": "*Top",
        "Right Plane": "*Right",
        "Bottom Plane": "*Bottom",
        "Back Plane": "*Back",
        "Left Plane": "*Left",
    }

    # ══ 【M5 修复】基准面姿态改为【代码查询】，不再靠硬编码 ZSIGN ══════════
    # 背景（回溯报告 §15.1 M5 / §7 P1）：历史上"Top Plane 草图 y 要取反"
    #   这条知识只存在于文档/模型记忆里（skills/sw-design 的 ZSIGN 硬编码），
    #   换台机器、换 SW 版本、或用户自建基准面时全靠猜，一错就整件沿 Z 镜像。
    #
    # 官方给的查询通路（不是猜测）：
    #   · IRefPlane::Transform —— 原文 "Gets the plane transform with respect
    #     to the world."（SOLIDWORKS 2004 SP4 / Rev 12.4 起）；
    #   · IMathTransform::ArrayData —— 16 个 double：
    #       0..8  旋转部分（局部 x/y/z 三轴在世界系里的方向，行主序）
    #       9..11 平移（基准面原点）
    #       12    缩放；13..15 未用
    #   · 官方示例 "Get the Normal and Origin of a Reference Plane Using Its
    #     Transform" 的做法：把变换作用于局部 (0,0,1) 即得世界法向。
    #   · FeatureCut4 Remarks 原文也指向同一路径：
    #     "To determine the sketch normal, see IFace2::Normal and
    #      IRefPlane::Transform."
    #
    # ⚠️ 官方【没有】给出三个默认基准面基向量的数值表（我让检索专员逐页查证过，
    #   全部 API Help 与 CAD 帮助均无）→ 因此这里【绝不硬编码】，一律现场查询；
    #   查不到时才退回经验值，并在返回值里显式标注 source，绝不静默假设。
    _DEFAULT_PLANE_NAMES = {
        "front plane": "Front Plane", "front": "Front Plane",
        "前视基准面": "Front Plane",
        "top plane": "Top Plane", "top": "Top Plane",
        "上视基准面": "Top Plane",
        "right plane": "Right Plane", "right": "Right Plane",
        "右视基准面": "Right Plane",
        "back plane": "Back Plane", "back": "Back Plane",
        "bottom plane": "Bottom Plane", "bottom": "Bottom Plane",
        "left plane": "Left Plane", "left": "Left Plane",
    }

    def plane_axes_world(self, plane="Top Plane"):
        """【M5】查询基准面在世界系里的三轴方向（x/y/z）与法向。

        Returns:
            {"ok": True,
             "plane": <基准面名>,
             "axes_world": {"x": [..], "y": [..], "z": [..]},   # 单位向量
             "normal_world": [...],                            # = 局部 z 轴
             "origin_world_mm": [x, y, z],
             "sketch_y_to_world": {...},   # 草图 y 轴导数（含符号判断）
             "source": "IRefPlane::Transform" | "fallback_table"
            }
            或 {"ok": False, "error": ...}

        用途：替代 `ZSIGN` 这类硬编码。建模脚本应写
            ax = m.plane_axes_world("Top Plane")
            # 要世界 +Z 的意图 → 草图 y = ±z
        而不是"我记得 Top Plane 要取反"。
        """
        out = {"ok": False, "plane": str(plane)}
        _name = self._DEFAULT_PLANE_NAMES.get(
            str(plane).strip().lower(), str(plane))
        out["plane_resolved"] = _name
        _arr = None
        # ── ① 官方通路（真机实测可用的那一条）────────────────────────────
        # 为什么不用 FeatureByName / FirstFeature 遍历：
        #   本机 SW2025 Rev33.5.0（晚绑定）实测 ——
        #     · FeatureByName("Top Plane") 返回 None（基准面不在"按名可查"范围）
        #     · FirstFeature().GetNextFeature() 在【收藏文件夹】节点后就抛
        #       com_error(-2147352573, '找不到成员。')，遍历直接断在第一个节点
        #   这两条路都不可靠，因此改用【选择集】取对象：
        #     select_plane(多语言兜底) → SelectionManager.GetSelectedObject6
        #     → early(IFeature).GetSpecificFeature2() → IRefPlane.Transform
        #   实测 Top/Front/Right 三个基准面全部读到了正确 ArrayData。
        try:
            _ok_sel = False
            try:
                _ok_sel = bool(self.select_plane(_name))
            except Exception:
                _ok_sel = False
            _sel = None
            if _ok_sel:
                try:
                    _sm = self.model.SelectionManager
                    _sel = _sm.GetSelectedObject6(1, -1)
                except Exception:
                    _sel = None
            if _sel is not None:
                # 选中对象是 IFeature（type=RefPlane）；先包早期绑定再往下取，
                #   晚绑定下 GetSpecificFeature2 的属性访问会失败。
                _feat = sel = _sel
                try:
                    _feat = early(_sel, "IFeature") or _sel
                except Exception:
                    _feat = _sel
                _ref = None
                for _acc in ("GetSpecificFeature2", "GetSpecificFeature"):
                    try:
                        _fn = getattr(_feat, _acc, None)
                        if _fn is None:
                            continue
                        _ref = _fn() if callable(_fn) else _fn
                        if _ref is not None:
                            break
                    except Exception:
                        continue
                if _ref is not None:
                    _rp = _ref
                    try:
                        _rp = early(_ref, "IRefPlane") or _ref
                    except Exception:
                        _rp = _ref
                    try:
                        _mt = _rp.Transform
                        _arr = list(_mt.ArrayData)
                    except Exception as _e_mt:
                        out["transform_error"] = repr(_e_mt)
            # 用完清选择（不打扰后续建模）
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
        except Exception as _e_tr:
            out["transform_error"] = repr(_e_tr)
        if _arr and len(_arr) >= 12:
            try:
                def _v(i):
                    return [round(float(_arr[i]), 9),
                            round(float(_arr[i + 1]), 9),
                            round(float(_arr[i + 2]), 9)]
                out["axes_world"] = {"x": _v(0), "y": _v(3), "z": _v(6)}
                out["normal_world"] = _v(6)
                out["origin_world_mm"] = [round(float(_arr[9]) * 1000.0, 6),
                                          round(float(_arr[10]) * 1000.0, 6),
                                          round(float(_arr[11]) * 1000.0, 6)]
                # 草图 y 轴在世界各轴的投影（最大分量即主导轴，符号即方向）
                _y = out["axes_world"]["y"]
                _imax = max(range(3), key=lambda k: abs(_y[k]))
                out["sketch_y_axis_index"] = _imax
                out["sketch_y_sign"] = 1.0 if _y[_imax] >= 0 else -1.0
                out["source"] = "IRefPlane::Transform"
                out["ok"] = True
                return out
            except Exception as _e_p:
                out["parse_error"] = repr(_e_p)
        # ② 查不到 → 退回经验表，并【显式标注】不是查到的
        #    经验值来源：skills/sw-design ZSIGN（社区实测，非官方数值表）
        _fb = {"top plane": ("y", -1.0), "front plane": ("y", 1.0),
               "right plane": ("y", 1.0)}
        _k = _name.strip().lower()
        if _k in _fb:
            _axis, _sign = _fb[_k]
            out.update({
                "ok": True,
                "source": "fallback_table",
                "note": ("未能从 IRefPlane::Transform 读到变换，"
                         "退回经验值（社区实测，非官方数值表）—— "
                         "请勿据此做精密镜像判断，优先排查 RefPlane 读取。"),
                "sketch_y_axis_name": "Z" if _k == "top plane" else _axis.upper(),
                "sketch_y_sign": _sign,
            })
            return out
        out["error"] = ("无法查询基准面 %r 的变换，且它不在经验表里。"
                        "自建基准面必须先确认 IRefPlane::Transform 可读。"
                        % (_name,))
        return out

    def sketch_y_for_world_z(self, plane, world_z_mm):
        """【M5】把"我要在世界 Z 上的位置"换算成本基准面草图里的 y。

        这是 `ZSIGN` 的代码替代：先查真实变换，再换算；查不到才退回经验值。
        Top Plane 上 `world_z=20` → 草图 y = −20（因为草图 y 轴指向世界 −Z）。

        Returns: {"ok", "sketch_y", "source", "plane"}
        """
        out = {"ok": False, "plane": str(plane)}
        _ax = self.plane_axes_world(plane)
        if not _ax.get("ok"):
            out["error"] = _ax.get("error")
            out["detail"] = _ax
            return out
        out["source"] = _ax.get("source")
        _idx = _ax.get("sketch_y_axis_index")
        _sign = float(_ax.get("sketch_y_sign") or 1.0)
        if _idx is None:
            # 经验表分支：Top 的草图 y 对应世界 −Z；其它对应世界 +Y
            _is_top = "top" in str(plane).lower()
            out["sketch_y"] = round((-1.0 if _is_top else 0.0) * float(world_z_mm), 6)
            if not _is_top:
                out["warning"] = ("该基准面的草图 y 不沿世界 Z —— "
                                  "world_z 无法直接映射到草图 y，"
                                  "请改用 plane_axes_world() 自行换算。")
            out["ok"] = True
            return out
        if _idx == 2:      # 草图 y 沿世界 Z
            out["sketch_y"] = round(_sign * float(world_z_mm), 6)
        elif _idx == 1:    # 草图 y 沿世界 Y —— Z 无法直接映射
            out["sketch_y"] = None
            out["warning"] = ("草图 y 沿世界 Y，不沿 Z；世界 Z 的意图无法只靠草图 y 表达。"
                              "请改用 begin_sketch_on_face 或自建偏移基准面。")
        else:              # 草图 y 沿世界 X
            out["sketch_y"] = None
            out["warning"] = "草图 y 沿世界 X，无法映射世界 Z。"
        out["ok"] = True
        return out

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
        """选择基准面（多种命名兜底）。

        Bug 6 修复: 使用 PLANE 类型选择基准面。
        注意: 不在这里 clear_selection —— 会破坏后续 InsertSketch 的选面逻辑。

        ── 【BUG-C 修复】基准面选择必须"多写法 + 返回成功与否" ──────────────
        测试反馈：坐标选面策略覆盖了【按名选基准面】路径 —— 实体上已有曲面后，
          begin_sketch("Front Plane") 失效。
        根因：基准面在 SW 里是 PLANE 类型（不是 FACE），而原来的按名选择
          只试单一名字，中文版/不同语言版名不匹配时【静默失败】
          （SelectByID2 返回 False 没人检查），于是每次都跌进坐标选面 fallback，
          而 fallback 找的是实体 FACE —— 语义完全不同，选错面就开错草图。

        修复：
          1) 依次尝试【英文名 / 中文名 / 前视基准面 等常见写法】；
          2) 显式检查 SelectByID2 的返回值，成功即返回 True；
          3) 全部写法失败时返回 False（而非静默），让调用方决定是否走 fallback。

        Returns: bool（True=已选中基准面）
        """
        if name not in _PLANES:
            raise ValueError(f"unknown plane {name!r}; use {_PLANES}")

        # 常见命名变体：英文、中文、以及带/不带 " Plane" 后缀
        _ALIAS = {
            "Front Plane": ["Front Plane", "前视基准面", "前视", "Front", "FrontPlane"],
            "Back Plane":  ["Back Plane", "后视基准面", "后视", "Back", "BackPlane"],
            "Top Plane":   ["Top Plane", "上视基准面", "俯视基准面", "上视", "Top", "TopPlane"],
            "Bottom Plane":["Bottom Plane", "下视基准面", "下视", "Bottom", "BottomPlane"],
            "Right Plane": ["Right Plane", "右视基准面", "右视", "Right", "RightPlane"],
            "Left Plane":  ["Left Plane", "左视基准面", "左视", "Left", "LeftPlane"],
        }
        cands = _ALIAS.get(name, [name])

        # ── 方式 1：按名选择（最快，但依赖语言环境）───────────────────────
        for _nm in cands:
            try:
                ok = self.ext.SelectByID2(_nm, "PLANE", 0, 0, 0, False, 0,
                                          self._empty, 0)
                if ok:
                    return True
            except Exception:
                continue

        # ── 方式 2【专家建议】按对象引用选中，绕开语言/名称差异 ──────────
        # 专家指出：中英文界面下基准面默认名称不同，纯字符串选择可能失效；
        #   更稳的做法是遍历特征树拿到基准面对象，用它的引用去选中。
        #   （SW2025 下 FeatureManager 某些方法签名有变化，所以这里
        #     整体包在 try 里，失败不影响返回值语义。）
        try:
            _canon = {
                "Front Plane": ("Front", "前视"),
                "Back Plane": ("Back", "后视"),
                "Top Plane": ("Top", "上视", "俯视"),
                "Bottom Plane": ("Bottom", "下视"),
                "Right Plane": ("Right", "右视"),
                "Left Plane": ("Left", "左视"),
            }.get(name, ())
            f = self.model.FirstFeature()
            _guard = 0
            while f is not None and _guard < 5000:
                _guard += 1
                try:
                    _tn = f.GetTypeName2()
                except Exception:
                    _tn = None
                if _tn in ("RefPlane", "Plane"):
                    _fname = ""
                    try:
                        _fname = str(f.Name)
                    except Exception:
                        _fname = ""
                    # 名匹配（任一别名完全相等，或包含关键词）
                    _hit = _fname in cands
                    if not _hit and _canon:
                        _hit = any(_k in _fname for _k in _canon)
                    if _hit:
                        _selected = False
                        # Feature.Select2 优先（部分版本），再试 Select4/Select
                        # 【测试部反馈修复】Select4 的 Data 必须是 SelectData 对象
                        _sd_plane = self._make_select_data(0)
                        _plane_chain = [("Select2", (False, 0))]
                        if _sd_plane is not None:
                            _plane_chain.append(("Select4", (False, _sd_plane)))
                        _plane_chain.append(("Select", (False,)))
                        for _mn, _args in _plane_chain:
                            try:
                                _fn = getattr(f, _mn, None)
                                if _fn is None:
                                    continue
                                _fn(*_args)
                                _selected = True
                                break
                            except Exception:
                                continue
                        if _selected:
                            try:
                                _c = self.model.SelectionManager \
                                    .GetSelectedObjectCount2(-1)
                                if int(_c) > 0:
                                    return True
                            except Exception:
                                return True
                try:
                    f = f.GetNextFeature()
                except Exception:
                    break
        except Exception:
            # 特征树 API 在 SW2025 可能签名变化 —— 静默降级，不影响主流程
            pass

        # 全部方式都没选中 —— 明确返回 False，绝不静默
        return False

    def begin_sketch(self, plane="Front Plane"):
        """在指定基准面上开始新草图，并先"正视于"该平面（居中显示）。

        Bug 3 修复: Top/Bottom/Left/Right 等基准面在某些状态下选择不稳定，
        增加多轮重试 + 面搜索兜底。
        Bug 6 修复: 多特征后 SW 内部状态累积——在 InsertSketch 后清理状态。
        Bug 7 修复: 检查 ActiveSketch 是否有效。
        """
        # 先退出可能残留的草图模式（toggle off），再重新进入
        try:
            self.skm.InsertSketch(False)
        except Exception:
            pass
        # ══ 【Bug-40 修复】跨基准面 / 多特征后强制重置上下文 ═════════════
        # 判定条件（任一命中即重置）：
        #   ① 本次基准面 ≠ 上次基准面（跨面切换，Top→Front/Right 典型场景）；
        #   ② 本零件已有实体且这是第 2 次及以后的 begin_sketch
        #      （多特征后再开草图，SW 的旧面引用会污染 InsertSketch）。
        # 只在"确实有风险"时重置，避免每次开草图都付一次 rebuild 的代价。
        _prev_plane = getattr(self, "_last_begin_plane", None)
        _switch = (_prev_plane is not None
                   and str(_prev_plane).strip().lower() != str(plane).strip().lower())
        _repeat_with_body = False
        try:
            _repeat_with_body = bool(self._has_solid_body()
                                     and int(getattr(self, "_sketch_open_count", 0)) > 0)
        except Exception:
            _repeat_with_body = False
        if _switch or _repeat_with_body:
            # 【Bug-40b】原实现调 _reset_plane_context（内含 rebuild）。
            # 实测（夹紧凸轮 96 环扇形拼接，每环一次 begin_sketch）：
            #   · rebuild 并非幂等——多次 rebuild 后【按名选基准面反而间歇性
            #     失败】，旧逻辑随即静默降级到实体面 → 草图坐标系错位 →
            #     后续所有 extrude/cut 返回 None；
            #   · rebuild 每次 1~3s，多特征零件上是主要耗时来源。
            # 结论：退出残留草图态 + 清选择集已足够让 COM 上下文干净，
            #   rebuild 仅保留给 begin_sketch 重试分支显式调用。
            try:
                if self.skm.ActiveSketch is not None:
                    self.skm.InsertSketch(True)
            except Exception:
                pass
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
            self._plane_generation = int(getattr(self, "_plane_generation", 0)) + 1
        self._last_begin_plane = str(plane)
        try:
            self._sketch_open_count = int(getattr(self, "_sketch_open_count", 0)) + 1
        except Exception:
            pass
        # ── 【BUG-C / 新-4 修复】按名选基准面是【首选路径】────────────────
        # BUG-C 反馈：坐标选面策略覆盖了按名选基准面，实体有曲面后失效。
        # 新-4 反馈：中文面名必崩（_plane_selected 未初始化 + 白名单只有英文）。
        # 现在：
        #   1) _plane_selected 先初始化为 False（修复 NameError）；
        #   2) 接收 select_plane 的返回值，知道"按名到底选中没有"；
        #   3) 重试 5 次提高主路径成功率；
        #   4) 只有按名确实失败，才降级到实体面 fallback。
        import time as _t
        _plane_selected = False
        # ══ 【Bug-18 精修：杜绝"落到实体面"的静默错位】═══════════════════
        # 多特征零件上按名选基准面会间歇性失败；旧逻辑失败 5 次后静默降级到
        # begin_sketch_on_face，草图被开到【实体面】上，本地坐标系错位，导致
        # 后续所有 extrude/cut 返回 None（FeatureExtrusion3 None，segs=4）。
        # 现在：① 每轮先 rebuild 清 SW 残留态；② 按名只试 2 轮（清态后第二轮
        # 通常必成）；③ 两轮都失败 → 直接抛错让调用方重试，【禁止】面降级。
        for retry in range(2):
            if retry > 0:
                try:
                    self.rebuild()
                except Exception:
                    pass
                _t.sleep(0.2)
            try:
                _sel = self.select_plane(plane)
                if _sel:
                    _plane_selected = True
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None and _plane_selected:
                    self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                    try:
                        self.clear_selection()
                    except Exception:
                        pass
                    # 【Bug T1/T2 修复】记录草图名，供 cut() 显式重选轮廓
                    # 【Bug T3 修复】统一走 _finalize_new_sketch，避免漏分支
                    self._finalize_new_sketch(plane)
                    return self
                # ActiveSketch 为空或基准面没选中：清残留再试
                try:
                    self.skm.InsertSketch(False)
                except Exception:
                    pass
            except Exception:
                pass
        # 两轮清态后仍选不中基准面 → 明确失败（不落到实体面造成坐标错位）
        raise RuntimeError(
            u"begin_sketch(%s): 按名选择基准面失败（已 rebuild 清态重试）。"
            u"为避免草图落到实体面导致坐标错位，已禁用实体面降级，请重试或"
            u"检查基准面名称。" % plane)

        # ── 【C7 修复 + BUG-C 收紧】基准面彻底选不中时才走实体面 fallback ──
        # C7 场景：实体含圆柱面后，第 3 次调 begin_sketch("Front Plane") 必失败
        #   （RuntimeError: 无法激活草图）。
        # BUG-C 反馈：这套坐标选面策略会【覆盖】按名选基准面 ——
        #   一旦实体上已有曲面，fallback 可能选中错误的实体面并开错草图。
        #
        # 现在分两级，语义清晰：
        #   优先级1（上方已执行）：按名选基准面 —— 正确语义，首选；
        #   优先级2（此处）：仅当按名选确实失败才降级到实体面，
        #     并记录"本次为降级行为"，便于事后排查选错面的问题。
        if not _plane_selected:
            self._warn("begin_sketch(%s): 按名选择基准面失败，降级到实体面策略"
                       "（可能选中非预期的面，请核对草图所在平面）" % plane)
        search_axis = {"Front Plane": (0, 0, "z"), "Back Plane": (0, 0, "z"),
                       "Top Plane": (0, "y", 0), "Bottom Plane": (0, "y", 0),
                       "Right Plane": ("x", 0, 0), "Left Plane": ("x", 0, 0)}
        ax, ay, az = search_axis.get(plane, (0, 0, "z"))
        # 第一步：按轴向外扩，用包围盒法找平面（免疫曲面干扰）
        for sign in (1, -1):
            for v in (5, 10, 20, 50, 100, 200):
                try:
                    px = sign * v * MM if ax == "x" else 0.0
                    py = sign * v * MM if ay == "y" else 0.0
                    pz = sign * v * MM if az == "z" else 0.0
                    if hasattr(self, "_select_face_by_box") and self._select_face_by_box(px, py, pz):
                        self.skm.InsertSketch(True)
                        if self.skm.ActiveSketch is not None:
                            self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                            self._finalize_new_sketch(plane)   # 【T3】降级分支也要记录
                            return self
                        # 开了但 ActiveSketch 为空 → 清状态重试一次
                        self.rebuild()
                        self.clear_selection()
                        try:
                            self.skm.InsertSketch(False)
                        except Exception:
                            pass
                        if self._select_face_by_box(px, py, pz):
                            self.skm.InsertSketch(True)
                            if self.skm.ActiveSketch is not None:
                                self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                                self._finalize_new_sketch(plane)   # 【T3】降级分支也要记录
                                return self
                except Exception:
                    continue
        # 【BUG-C 清理】此处原先重复定义了一次 ax/ay/az —— 上方已定义，删除冗余。
        # 第二级 fallback：固定坐标射线拾取（最后手段，免疫性最差）
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
                        self._finalize_new_sketch(plane)   # 【T3】降级分支也要记录
                        return self
                    # Bug-4 修复: InsertSketch 成功但 ActiveSketch 为 None（SW COM 状态脏）
                    # 强制重建模型清除 COM 内部状态，再重试一次
                    self.rebuild()
                    self.clear_selection()
                    try:
                        self.skm.InsertSketch(False)
                    except Exception:
                        pass
                    self.ext.SelectByID2("", "FACE", pts[0], pts[1], pts[2], False, 0, self._empty, 0)
                    self.skm.InsertSketch(True)
                    active_sk = self.skm.ActiveSketch
                    if active_sk is not None:
                        self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                        self._finalize_new_sketch(plane)   # 【T3】降级分支也要记录
                        return self
                except Exception:
                    continue

        # ══ 【Bug-18/28 修复】最后一道 fallback：begin_sketch_on_face ═══════
        # 三个房间（结构件 Bug-28、壳体机架 Bug-26、支撑结构 Bug-24）独立得出
        # 同一结论：多特征零件里按名选基准面会【间歇性失效】
        #   （"无法激活草图，平面 Front Plane 选择失败"），
        # 而 begin_sketch_on_face() 选【实体面】稳定得多。
        # 原实现把这条 fallback 留给调用方手工调用 —— 但小屋并不知道要这么做，
        # 于是反复重试同一个失败路径（Bug-19 的"无限重试"）。
        # 修复：按名 + 坐标两条路径都失败后，自动尝试在已有实体的面上开草图。
        try:
            _bodies = self.model.GetBodies2(0, False)
            _has_body = bool(_bodies)
        except Exception:
            _has_body = False
        if _has_body:
            self._warn("begin_sketch(%s): 按名/坐标均失败，自动降级到 "
                       "begin_sketch_on_face（选实体面，Bug-18 兜底）" % plane)
            for _pt in ((0.0, 0.0, 0.0), (0.0, 0.0, 20.0), (0.0, 0.0, -20.0)):
                try:
                    self.begin_sketch_on_face(*_pt)
                    return self
                except Exception:
                    continue

        # 全部路径失败 —— 给出可操作诊断，而不是只报"选择失败"
        _diag = diagnose_feature_failure(self, api="begin_sketch",
                                         extra={"plane": plane,
                                                "plane_selected": _plane_selected})
        raise RuntimeError(
            "无法激活草图，平面 %s 选择失败。"
            "【诊断】原因=%s（%s）建议：%s"
            % (plane, _diag.get("cause"), _diag.get("detail"), _diag.get("advice")))

    def _select_face_by_box(self, x, y, z, tolerance_mm=5.0):
        """通过面包围盒匹配选择实体面（绕过 SW 射线拾取在边界处的局限）。

        Bug 7/8 修复: SelectByID2(FACE, x,y,z) 在最大边界面处将坐标识别为边/顶点。
        本方法遍历所有面，用 face.GetBox 检查目标点是否在面范围内，
        然后用 face.Select(True) 直接选中，完全不依赖坐标投影。
        同时处理用户局部坐标到 SW 内部坐标的自动转换。

        Bug-19 修复: 远离原点的水平面（z>10mm）射线拾取不稳定。
        改用 Z 轴环绕搜索 + 面法线方向匹配，确保水平面也能被可靠选中。
        同时增加对曲面的过滤：只有平面（法线非零）才参与匹配。
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

                    # ── 预处理：收集所有有效平面，计算实体包围盒 ─────────────
                    ent_bb_min = [float('inf')] * 3
                    ent_bb_max = [float('-inf')] * 3
                    planar_faces = []

                    for face in flist:
                        try:
                            n = face.Normal
                            # 过滤曲面（法线全零 → 球面/圆柱面网格）
                            if all(abs(v) < 0.01 for v in n):
                                continue
                            bb = face.GetBox
                            bb_min = (bb[0] * 1000, bb[1] * 1000, bb[2] * 1000)
                            bb_max = (bb[3] * 1000, bb[4] * 1000, bb[5] * 1000)
                            for i in range(3):
                                ent_bb_min[i] = min(ent_bb_min[i], bb_min[i])
                                ent_bb_max[i] = max(ent_bb_max[i], bb_max[i])
                            planar_faces.append((face, bb_min, bb_max, tuple(n)))
                        except Exception:
                            continue

                    if not planar_faces:
                        continue

                    # ── 策略A: 原始坐标直接匹配（适用于原点附近的小零件）─────
                    for face, bb_min, bb_max, _normal in planar_faces:
                        try:
                            if (bb_min[0] - tol <= x <= bb_max[0] + tol and
                                bb_min[1] - tol <= y <= bb_max[1] + tol and
                                bb_min[2] - tol <= z <= bb_max[2] + tol):
                                if face.Select(True):
                                    return True
                        except Exception:
                            continue

                    # ── 策略B: Z 轴环绕搜索（Bug-19 关键修复）───────────────
                    # 对于远离原点的水平面，SW 射线拾取不稳定。
                    # 改用：找到 Z 坐标最接近目标值的平面，再在其范围内搜索。
                    target_z = z
                    best_z_match = None
                    best_z_dist = float('inf')

                    for face, bb_min, bb_max, normal in planar_faces:
                        # 计算面中心 Z 坐标
                        face_cz = (bb_min[2] + bb_max[2]) / 2
                        # 判断是否为水平面（法线 Z 分量 > 0.9）
                        if abs(normal[2]) > 0.9:
                            z_dist = abs(face_cz - target_z)
                            if z_dist < best_z_dist:
                                best_z_dist = z_dist
                                best_z_match = (face, bb_min, bb_max, normal, face_cz)

                    if best_z_match and best_z_dist < 20:  # 20mm 容差
                        face, bb_min, bb_max, normal, face_cz = best_z_match
                        # 在 Z 面范围内，用 X/Y 精确匹配
                        if (bb_min[0] - tol <= x <= bb_max[0] + tol and
                            bb_min[1] - tol <= y <= bb_max[1] + tol and
                            bb_min[2] - tol <= z <= bb_max[2] + tol):
                            if face.Select(True):
                                return True
                        # X/Y 不精确匹配时，扩大 X/Y 容差重试
                        elif (bb_min[0] - tol * 4 <= x <= bb_max[0] + tol * 4 and
                              bb_min[1] - tol * 4 <= y <= bb_max[1] + tol * 4):
                            if face.Select(True):
                                return True

                    # ── 策略C: 实体包围盒坐标转换（适用于局部坐标场景）───────
                    if any(v == float('inf') for v in ent_bb_min):
                        continue
                    ix = ent_bb_min[0] + x
                    iy = ent_bb_min[1] + y
                    iz = ent_bb_min[2] + z
                    for face, bb_min, bb_max, _normal in planar_faces:
                        try:
                            if (bb_min[0] - tol <= ix <= bb_max[0] + tol and
                                bb_min[1] - tol <= iy <= bb_max[1] + tol and
                                bb_min[2] - tol <= iz <= bb_max[2] + tol):
                                if face.Select(True):
                                    return True
                        except Exception:
                            continue

                    # ── 策略D: 全局 Z 最接近优先搜索（兜底）─────────────────
                    # 对任何平面，只要 Z 在目标 ±30mm 内，就尝试匹配
                    candidates = []
                    for face, bb_min, bb_max, normal in planar_faces:
                        face_cz = (bb_min[2] + bb_max[2]) / 2
                        z_dist = abs(face_cz - target_z)
                        if z_dist < 30:
                            cx = (bb_min[0] + bb_max[0]) / 2
                            cy = (bb_min[1] + bb_max[1]) / 2
                            x_dist = abs(cx - x)
                            y_dist = abs(cy - y)
                            if x_dist < 50 and y_dist < 50:
                                candidates.append((z_dist, face))
                    if candidates:
                        candidates.sort(key=lambda c: c[0])
                        for _, face in candidates[:3]:  # 最多试前3个
                            if face.Select(True):
                                return True

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

    def _has_curved_face_at(self, x, y, z):
        """检查目标点是否【真的落在某个曲面几何上】。

        ── 【C02 修复 · 2026-10-29 真机实测】原实现是纯粹的误判制造机 ──────
        原实现判据：点是否落在【曲面的 GetBox 包围盒】内（±1mm）。
        实测复现（120×80×10 板 + Ø10 通孔，SW2025）：
          该圆柱面的 GetBox = [-5,-5,0 → 5,5,10]，Z 向**跨越整个板厚**；
          于是顶面 (0,0,10) —— 一个本来就在平面上的点 —— 被这个立方体包住，
          `_has_curved_face_at(0,0,10)` 返回 **True**；
          而 begin_sketch_on_face 的策略 1 正是被它否掉的 → M3 沉孔永远打不上。

        新判据（两级，且【不确定时一律返回 False】）：
          ① 平面面直接跳过（用官方 ISurface.IsPlane 判定，不再用包围盒猜）；
          ② 曲面的包围盒只用于【粗筛候选】；
          ③ 命中候选后再做几何确认：要求该点与该曲面的最近点距离足够小
             （`ISurface.GetClosestPointOn` 或面法向一致），确认不了就【不算】。

        取舍原则：**误判成"曲面"的代价（沉孔打不上）远大于漏判**，
          所以这条函数在不确定时必须返回 False（fail-open 到平面路径）。
        """
        try:
            bodies = self.model.GetBodies2(0, 1)
            if not bodies:
                return False
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    for face in flist:
                        # ① 平面直接跳过（官方判定，最可靠）
                        if self._face_is_planar(face) is True:
                            continue
                        if not self._is_curved_face(face):
                            continue
                        # ② 包围盒只做粗筛（召回用，不作判据）
                        try:
                            bb = face.GetBox
                            bb_min = (bb[0] * 1000, bb[1] * 1000, bb[2] * 1000)
                            bb_max = (bb[3] * 1000, bb[4] * 1000, bb[5] * 1000)
                            if not (bb_min[0] - 1 <= x <= bb_max[0] + 1 and
                                    bb_min[1] - 1 <= y <= bb_max[1] + 1 and
                                    bb_min[2] - 1 <= z <= bb_max[2] + 1):
                                continue
                        except Exception:
                            continue
                        # ③ 几何确认：点必须真的贴在该曲面上
                        if self._point_on_curved_face(face, x, y, z):
                            return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _point_on_curved_face(self, face, x, y, z, tol_mm=1.0):
        """点是否真的落在该曲面上（几何确认，C02）。

        用官方 `ISurface.GetClosestPointOn(x,y,z)` 求最近点，再比较距离。
        ⚠️ 该方法返回值语义在不同绑定/版本下不一致（现有注释已警告"v 参数不是角度"），
          因此这里【只接受能明确算出距离的情形】；算不出来就返回 False ——
          宁可漏判（走平面路径），也不要误判（挡住沉孔）。
        """
        try:
            surf = face.GetSurface
        except Exception:
            return False
        if surf is None:
            return False
        _m = 0.001   # mm → m（SW API 内部用米）
        _pt = None
        try:
            _fn = getattr(surf, "GetClosestPointOn", None)
            if callable(_fn):
                _r = _fn(float(x) * _m, float(y) * _m, float(z) * _m)
                # 常见返回：3 元组 (x,y,z)；或 (u,v) 参数。只认前者。
                if isinstance(_r, (tuple, list)) and len(_r) >= 3:
                    _pt = (float(_r[0]), float(_r[1]), float(_r[2]))
        except Exception:
            _pt = None
        if _pt is None:
            # 算不出最近点 → 不确认（fail-open：不当作曲面）
            return False
        try:
            _dx = (_pt[0] / _m) - float(x)
            _dy = (_pt[1] / _m) - float(y)
            _dz = (_pt[2] / _m) - float(z)
            _dist = (_dx * _dx + _dy * _dy + _dz * _dz) ** 0.5
            return bool(_dist <= float(tol_mm))
        except Exception:
            return False

    def _is_curved_face(self, face):
        """判断面是否为曲面（圆柱/球面等），无法直接在其上开草图。

        ── 【C02 修复 · 2026-10-29 真机实测】改用官方 ISurface 判定 ─────────
        原实现：`face.Normal` 对曲面返回 (0,0,0)（靠"法向全零"反推曲面）。
          这是个脆弱代理判据：某些退化平面/边界情形法向也可能接近零。
        新实现（按可靠性降级）：
          ① ISurface.IsPlane —— 官方布尔属性，最直接（本机实测 6 个平面面全 True、
             圆柱面 False；pywin32 静态绑定下是【属性】，不加括号）；
          ② ISurface.Identity == 4001（PLANE_TYPE）—— 官方类型标识，兜底；
          ③ 仍拿不到才回落到原来的"法向全零"启发式。
        返回：True=曲面 / False=平面。
        """
        _planar = self._face_is_planar(face)
        if _planar is not None:
            return (not _planar)
        # ③ 最后兜底：原启发式（法向全零 → 视为曲面）
        try:
            n = face.Normal
            return all(abs(v) < 0.01 for v in n)
        except Exception:
            return False

    def _face_is_planar(self, face):
        """官方判定：该面是否【平面】。返回 True / False / None（拿不到）。

        ── 【C02 修复】真机实测依据（SW2025 SP5.0）────────────────────────
          · 6 个平面面：`surf.IsPlane == True`，`surf.Identity == 4001`；
          · 1 个圆柱面：`surf.IsPlane == False`；
          · `IsPlane` / `IsCylinder` 在 pywin32 静态绑定（CDispatch）下是
            **属性**（typelib invkind = PROPGET），直接 `surf.IsPlane` 即可，
            加括号会 TypeError —— 这里两种写法都兼容。

        返回 None 表示"无法判定"，由调用方决定策略（**不要当成曲面**）。
        """
        try:
            surf = face.GetSurface
        except Exception:
            return None
        if surf is None:
            return None
        # ① 首选 ISurface.IsPlane
        try:
            _v = getattr(surf, "IsPlane", None)
            if callable(_v):
                try:
                    _v = _v()
                except Exception:
                    _v = None
            if isinstance(_v, bool):
                return _v
            if isinstance(_v, int) and not isinstance(_v, bool):
                # 少数绑定把 bool 当 int 返回（0/1 / -1 表示错误）
                if _v in (0, 1):
                    return bool(_v)
        except Exception:
            pass
        # ② 兜底：官方类型标识 PLANE_TYPE = 4001
        try:
            _id = getattr(surf, "Identity", None)
            if callable(_id):
                try:
                    _id = _id()
                except Exception:
                    _id = None
            if isinstance(_id, int):
                return int(_id) == 4001
        except Exception:
            pass
        return None

    def _get_surface_normal(self, face, x, y, z):
        """从曲面上的点计算精确法线。

        【SW2018+】✅ ISurface.GetClosestPointOn 可用
        【SW2020】❌ ISurface.Evaluate 不可用（动态dispatch参数错误）
        【SW2020】❌ FeatureManager.InsertRefPlane 不可用（参数数不匹配）
        【SW2020】❌ InsertWrapFeature 不可用（模型无变化）
        【SW2023+】🔜 预期可用（需验证类型库注册）

        圆柱面法线: 径向 (x, y, 0) 归一化
        注意: GetClosestPointOn 返回的 v 参数不是角度，不能直接用 cos(v)/sin(v)。
        """
        try:
            import math
            surf = face.GetSurface
            if not surf:
                return None
            # 圆柱面: 法线 = 径向 (x, y, 0) 归一化
            if getattr(surf, 'IsCylinder', False):
                r = math.sqrt(x*x + y*y)
                if r < 1e-9:
                    return None
                return x/r, y/r, 0.0, x, y, z
            # 平面: 直接用 face.Normal
            n = face.Normal
            if all(abs(v) < 0.01 for v in n):
                return None
            return n[0], n[1], n[2], x, y, z
        except Exception:
            return None

    def _begin_sketch_on_curved_face(self, x, y, z):
        """曲面草图绕过方案：对圆柱面，分情况处理。

        【SW2020】(2025-08-23)
        - 前半侧(z≤10mm): SelectByID2 直接选中 ✅
        - 前半侧(任意z): 用低z点选面+Y偏移绕过 ✅
        - 后半侧: 回退到 Right Plane 穿透孔（画在侧面上，切除贯穿）⚠️
        - 适用范围: 轴线沿Z的圆柱面/圆孔面
        """
        import math
        # 从模型获取圆柱几何信息
        bodies = self.model.GetBodies2(0, 1)
        R_approx = 0.015  # 默认15mm
        cx_sw, cy_sw = 0.0, 0.0
        if bodies:
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
            found_cylinder = False
            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    for face in flist:
                        try:
                            surf = face.GetSurface
                            if surf and getattr(surf, 'IsCylinder', False):
                                bb = face.GetBox
                                cx_sw = (bb[0] + bb[3]) / 2
                                cy_sw = (bb[1] + bb[4]) / 2
                                R_approx = (bb[3] - bb[0])
                                found_cylinder = True
                                break
                        except: pass
                    if found_cylinder:
                        break
                except: pass
        else:
            return self

        # 计算目标角度，并将点投影到圆柱面上
        target_sw_x = x * MM
        target_sw_y = y * MM
        dx = target_sw_x - cx_sw
        dy = target_sw_y - cy_sw
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 1e-9:
            # 投影到圆柱面
            target_sw_x = cx_sw + R_approx * (dx / dist)
            target_sw_y = cy_sw + R_approx * (dy / dist)
        angle_rad = math.atan2(target_sw_y - cy_sw, target_sw_x - cx_sw)
        angle_deg = math.degrees(angle_rad)

        # 判断前半侧还是后半侧
        is_front = -90 <= angle_deg <= 90

        # 策略1: 低z点选面（前半侧有效）
        low_z = 5.0
        r_low = R_approx / 2
        low_x = cx_sw + r_low * math.cos(angle_rad)
        low_y = cy_sw + r_low * math.sin(angle_rad)
        low_z_sw = low_z * MM

        self.clear_selection()
        sel = self.ext.SelectByID2("", "FACE", low_x, low_y, low_z_sw, False, 0, self._empty, 0)
        if sel:
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._surface_sketch_offset = z - low_z
                print(f"[swapi] 圆柱侧面草图: 选z={low_z}mm处，"
                      f"草图Y偏移+{self._surface_sketch_offset:.0f}mm "
                      f"(对应目标z={z:.0f}mm, θ={angle_deg:.0f}°)")
                self._normal_to("*Front")
                return self
            # Bug 6 变体: 选面成功但草图未激活 → 先不退出，直接再试一次
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._surface_sketch_offset = z - low_z
                print(f"[swapi] 圆柱侧面草图(重试): 选z={low_z}mm处")
                self._normal_to("*Front")
                return self
            # 仍然失败：清除并重新选面
            self.clear_selection()
            try:
                self.skm.InsertSketch(False)
            except Exception:
                pass
            self.clear_selection()
            sel2 = self.ext.SelectByID2("", "FACE", low_x, low_y, low_z_sw, False, 0, self._empty, 0)
            if sel2:
                self.skm.InsertSketch(True)
                active_sk2 = self.skm.ActiveSketch
                if active_sk2 is not None:
                    self._surface_sketch_offset = z - low_z
                    print(f"[swapi] 圆柱侧面草图(二次重试): 选z={low_z}mm处")
                    self._normal_to("*Front")
                    return self
            self.clear_selection()

        # 策略2: 后半侧 → Right Plane 穿透孔（加重试）
        print(f"[swapi] 圆柱侧面 ({x:.0f},{y:.0f},{z:.0f})mm(θ={angle_deg:.0f}°) "
              f"改用 Right Plane 穿透孔")
        right_plane_ok = False
        try:
            for _retry in range(3):
                self.clear_selection()
                sel = False  # 每次重试重置 sel
                try:
                    self.skm.InsertSketch(False)
                except Exception:
                    pass
                for plane_name in ["Right Plane", "右视基准面"]:
                    sel = self.ext.SelectByID2(plane_name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
                    if sel:
                        break
                if not sel:
                    import time as _t
                    _t.sleep(0.1)
                    continue
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._right_plane_mode = True
                    print(f"[swapi] Right Plane 草图已激活(θ={angle_deg:.0f}°)，"
                          f"请用 circle(0,{y},半径) 或 rect(0,{y},宽,高) 画图后调用 cut()")
                    self._normal_to("*Front")
                    right_plane_ok = True
                    break
                # Active=False → 不清除选择，直接再试一次 InsertSketch
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._right_plane_mode = True
                    print(f"[swapi] Right Plane 草图已激活(θ={angle_deg:.0f}°)，"
                          f"请用 circle(0,{y},半径) 或 rect(0,{y},宽,高) 画图后调用 cut()")
                    self._normal_to("*Front")
                    right_plane_ok = True
                    break
                self.clear_selection()
                import time as _t
                _t.sleep(0.1)
        except Exception:
            pass

        if not right_plane_ok:
            # Bug 4: 失败时重置状态，抛出明确异常
            self._surface_sketch_offset = 0.0
            self._right_plane_mode = False
            raise RuntimeError(
                f"圆柱侧面 ({x:.0f},{y:.0f},{z:.0f})mm(θ={angle_deg:.0f}°) 无法开草图，"
                f"请确认点在圆柱面上或改用 begin_sketch()。"
            )
        # 【Bug T1 修复】Right Plane 穿透孔草图：法向 = X 轴，
        # 供 cut() 按正确轴向取贯穿长度计算期望去料体积。
        self._sketch_normal_axis = 0
        return self

    def _find_nearest_datum_plane(self, x, y, z):
        """在曲面选面失败后，回退到最近平行基准面。"""
        max_axis = max(abs(x), abs(y), abs(z), key=abs)
        if max_axis == abs(z):
            plane_name = "Front Plane"
        elif max_axis == abs(y):
            plane_name = "Top Plane"
        else:
            plane_name = "Right Plane"

        for name in [plane_name, "Front Plane", "Top Plane", "Right Plane",
                     "Bottom Plane", "Back Plane", "Left Plane"]:
            try:
                self.clear_selection()
                sel = self.ext.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, self._empty, 0)
                if sel:
                    self.skm.InsertSketch(True)
                    active_sk = self.skm.ActiveSketch
                    if active_sk is not None:
                        self._normal_to(self._PLANE_VIEW.get(name, "*Front"))
                        return name
                    self.clear_selection()
            except Exception:
                continue
        return None
        return None

    def begin_sketch_on_face(self, x=0, y=0, z=0):
        """在 (x,y,z) mm 处所在的面开始草图，并正视于该面。

        Bug 2 修复: 失败时不再静默回退——抛出 RuntimeError 并输出清晰提示。
        Bug 4 修复: 失败时重置 _surface_sketch_offset 和 _right_plane_mode。
        Bug 7 修复: 最大边界面不可选——平面用 GetBox 匹配，曲面用绕过方案。
        Bug 8 修复: 圆柱侧面全角度支持。
          - 前半侧(z≤10mm): SelectByID2 直接选中 ✅
          - 前半侧(任意z): 低z点选面+Y偏移 ✅
          - 后半侧: Right Plane 穿透孔 ✅
          - 非圆柱面/其他: 抛出 RuntimeError + 提示

        【SW2020 限制】
        SelectByID2 射线拾取只能选中朝向摄像机的面。
        后半侧圆柱面通过 Right Plane 穿透孔实现等效效果。
        """
        import math
        # 失败时保证状态干净
        def _fail(msg):
            self._surface_sketch_offset = 0.0
            self._right_plane_mode = False
            raise RuntimeError(msg)

        # 策略1: 对平面用 GetBox 匹配（【C02 修复】平面优先，不再被曲面误判挡住）
        #
        # ── 真机实测根因（2026-10-29，SW2025）────────────────────────────
        # 原写法是 `if not _has_curved_face_at(...) and _select_face_by_box(...)`：
        #   曲面判定【先行否决】。而 _has_curved_face_at 当时只看曲面 GetBox，
        #   圆柱面的包围盒 Z 向跨越整个板厚 → 孔正上方的平面点被判成"曲面"
        #   → 策略 1 被跳过 → 沉孔打不上（C02 现场）。
        # 现在：**平面优先**——先试着按平面选，选到了就直接用；
        #   只有平面确实选不到、且该点真的贴在曲面几何上，才转曲面路径。
        _plane_hit = False
        try:
            _plane_hit = bool(self._select_face_by_box(x, y, z))
        except Exception:
            _plane_hit = False
        if _plane_hit:
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._normal_to("*Front")
                # 【Bug T1/T2 修复】记录草图名，供 cut() 显式重选轮廓
                _nm = self._remember_sketch_name()
                self._sketch_area_mm2 = 0.0
                self._sketch_axis_offset_mm = float(z)
                if os.environ.get("DSH_SWAPI_DEBUG"):
                    print(u"[swapi][DBG] begin_sketch_on_face(%s,%s,%s) 策略1box -> 激活草图=%s"
                          % (x, y, z, _nm))
                return self
            # Bug 6 重试: 面选中但草图未激活
            self.clear_selection()
            try:
                self.skm.InsertSketch(False)
            except Exception:
                pass
            self.clear_selection()
            if self._select_face_by_box(x, y, z):
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._normal_to("*Front")
                    return self
            self.clear_selection()

        # 策略2: 直接用 SelectByID2 选面（验证面确实包含目标点，避免射线拾取误选）
        self.clear_selection()
        sel = self.ext.SelectByID2("", "FACE", x * MM, y * MM, z * MM, False, 0, self._empty, 0)
        if sel:
            # 验证：检查选中面的包围盒是否包含目标点
            sel_mgr = self.model.SelectionManager
            if sel_mgr.GetSelectedObjectCount2(-1) > 0:
                try:
                    sel_obj = sel_mgr.GetSelectedObject6(1, -1)
                    sel_bb = sel_obj.GetBox
                    sel_bb_min = (sel_bb[0]*1000, sel_bb[1]*1000, sel_bb[2]*1000)
                    sel_bb_max = (sel_bb[3]*1000, sel_bb[4]*1000, sel_bb[5]*1000)
                    if not (sel_bb_min[0] <= x <= sel_bb_max[0] and
                            sel_bb_min[1] <= y <= sel_bb_max[1] and
                            sel_bb_min[2] <= z <= sel_bb_max[2]):
                        # 选中面不包含目标点 → 射线拾取误选，清除选择
                        self.clear_selection()
                        sel = False
                except Exception:
                    pass
        if sel:
            self.skm.InsertSketch(True)
            active_sk = self.skm.ActiveSketch
            if active_sk is not None:
                self._normal_to("*Front")
                self._remember_sketch_name()
                self._sketch_axis_offset_mm = float(z)
                if os.environ.get("DSH_SWAPI_DEBUG"):
                    print(u"[swapi][DBG] begin_sketch_on_face(%s,%s,%s) 策略2ray -> 激活草图=%s"
                          % (x, y, z, self._active_sketch_name))
                # 【Bug T1 修复】从所选平面读法向轴，供 cut() 精确计算贯穿长度
                try:
                    _so = sel_mgr.GetSelectedObject6(1, -1)
                    if _so is not None:
                        _nrm = _so.Normal
                        _axs = [abs(float(_nrm[0])), abs(float(_nrm[1])), abs(float(_nrm[2]))]
                        self._sketch_normal_axis = _axs.index(max(_axs))
                except Exception:
                    self._sketch_normal_axis = 2
                return self
            self.clear_selection()

        # 策略2b: SelectByID2 失败 → 用 face.Select(True) 直接选面（绕过射线拾取限制）
        bodies = self.model.GetBodies2(0, 1)
        if bodies:
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    for face in flist:
                        try:
                            bb = face.GetBox
                            bb_min = (bb[0]*1000, bb[1]*1000, bb[2]*1000)
                            bb_max = (bb[3]*1000, bb[4]*1000, bb[5]*1000)
                            if (bb_min[0] <= x <= bb_max[0] and
                                bb_min[1] <= y <= bb_max[1] and
                                bb_min[2] <= z <= bb_max[2]):
                                face.Select(True)
                                self.skm.InsertSketch(True)
                                active_sk = self.skm.ActiveSketch
                                if active_sk is not None:
                                    self._normal_to("*Front")
                                    self._remember_sketch_name()
                                    return self
                                self.clear_selection()
                                break
                        except Exception:
                            continue
                except Exception:
                    continue

        # 策略3: 检测曲面 → 绕过方案
        bodies = self.model.GetBodies2(0, 1)
        if bodies:
            body_list = list(bodies) if isinstance(bodies, tuple) else [bodies]
            for b in body_list:
                try:
                    faces = b.GetFaces()
                    flist = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    for face in flist:
                        if self._is_curved_face(face):
                            return self._begin_sketch_on_curved_face(x, y, z)
                except Exception:
                    continue

        # 全部失败：抛出明确异常（Bug 2/4）
        _fail(f"无法在坐标 ({x:.0f},{y:.0f},{z:.0f}) mm 处开草图，请确认点是否在实体表面上。")

    def end_sketch(self, merge=True):
        """结束草图。merge=True 时合并微小间隙的端点，确保轮廓封闭。
        曲面草图模式下重置Y偏移量。

        Bug 8 修复: 在 commit 前调用 MergePoints 闭合端点。
        Bug 7 修复: 确保 ActiveSketch 有效。
        """
        # Bug 8: 合并微小间隙的端点，确保轮廓封闭
        # ── 【R5 修复】容差从写死 0.0005（0.5mm）改为 SKETCH_MERGE_TOL_M ──
        # 实测：0.5mm 容差会把"两环同草图"的闭合环粘连，净距 0.328mm 时
        #   FeatureExtrusion3 直接返回 none/no_body；0.522mm 起才 PASS。
        #   分界线正是旧容差本身 → 那不是几何不合法，是我们自己合并掉了。
        if merge:
            try:
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    active_sk.MergePoints(SKETCH_MERGE_TOL_M)
            except Exception:
                pass
        # 【R5 修复】提交前把"轮廓内环净距 < 合并容差"这件事记成告警 ——
        #   这是 SW 返回 None/no_body 的头号可读原因，必须在特征失败前可见。
        try:
            _gap = _ring_clearance_mm(getattr(self, "_sketch_circles_local", None))
            if _gap is not None and _gap < SKETCH_MERGE_TOL_MM:
                self._warn(
                    "草图内两个圆形环的净距仅 %.3f mm（< 合并容差 %.3f mm）："
                    "轮廓会被 MergePoints 粘连成一个连通区域，后续"
                    "拉伸/切除极可能返回 None(no_body)。"
                    "建议把环间距加大到 >%.3f mm，或改成分次建模。"
                    % (_gap, SKETCH_MERGE_TOL_MM, SKETCH_MERGE_TOL_MM * 5))
        except Exception:
            pass
        # ══ 【Bug T2 修复】退出前在"激活态"预选一次轮廓段 ═════════════
        # 退出草图瞬间 SW 会清空选择集；激活态是唯一能直接用
        # select_all_sketch_segments() 稳定选段的窗口。此处预选不改变
        # 建模结果，只为 cut() 的选择链路留一份"已被验证可选段"的证据。
        # ── 【F-04 修复】预选的是【草图段】，并打上轮廓标记 ──────────────
        # 说明：extrude 需要的轮廓是【草图特征本身】("SKETCH")，不是段，
        #   所以这份预选对 extrude 不能直接复用（extrude 会自己按名重选，
        #   见 _select_extrude_profile）。但它是 cut 的唯一稳定窗口，
        #   而且"预选存在"本身就是"草图已完成"的可观测证据 ——
        #   因此【保留】这段预选，只在旁边记一笔，绝不再让它被误当成
        #   拉伸轮廓（历史事故里正是"选了段却以为选了轮廓"）。
        try:
            if self.skm.ActiveSketch is not None:
                _seg_ok, _seg_n = self.select_all_sketch_segments()
                self._end_sketch_preselect = {"ok": bool(_seg_ok),
                                              "count": int(_seg_n or 0)}
        except Exception:
            pass
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            pass
        # ── 【A-107 修复·公开 exit_sketch】提交失败必须【当场】暴露 ─────────
        # 原缺陷：InsertSketch(True) 的返回值/异常被完全忽略，草图是否真正
        #   提交进特征树无人校验。后果：草图没提交成功时，后面的 extrude/cut
        #   会以"找不到轮廓"返回 None，而错误信息指向拉伸/切除，
        #   排查方向从一开始就是错的（实测踩过多次）。
        # 现在：记下返回值，提交后【复核 ActiveSketch 是否已退出】，
        #   失败写入 self.warnings 并抛给调用方（end_sketch 是显式动作，
        #   不允许"看起来成功"）。同时提供公开别名 exit_sketch() 供脚本使用，
        #   不再需要脚本自己去摸 InsertSketch。
        _commit_rc = None
        _commit_exc = None
        try:
            _commit_rc = self.skm.InsertSketch(True)
        except Exception as _e_commit:
            _commit_exc = _e_commit
        _still_active = None
        try:
            _still_active = self.skm.ActiveSketch is not None
        except Exception:
            _still_active = None
        self._end_sketch_commit = {"rc": _commit_rc,
                                   "exception": repr(_commit_exc) if _commit_exc else None,
                                   "still_active": _still_active}
        if _commit_exc is not None or _still_active is True:
            self._warn(
                "end_sketch 提交未确认成功（rc=%r, 仍处于草图态=%r, 异常=%r）——"
                " 后续 extrude/cut 很可能报\"找不到轮廓\"，请先排查草图是否闭合"
                % (_commit_rc, _still_active, _commit_exc))
        # 【Bug T2 修复】活动草图名保留 —— end_sketch 只是把轮廓"提交"，
        # 草图本身仍是 cut 的合法轮廓来源（cut 会按名 EditSketch 进去取段）。
        # 只有 cut/extrude 真正消费轮廓后才会清空它（_mark_sketch_consumed）。
        # 重置曲面草图偏移
        self._surface_sketch_offset = 0.0
        self._right_plane_mode = False
        # ── 【Bug-40 修复】记录"本次草图已在 <面> 上完成" ──────────────
        # 保留 _last_begin_plane 不变（用于 begin_sketch 的跨面判定），
        # 仅把 sketch_open_count 语义保持为"本零件累计开过的草图数"，
        # 供 cut 的上下文重置判定与诊断输出使用。
        return self

    def exit_sketch(self, merge=True):
        """【A-107】公开的"退出草图"入口 —— end_sketch 的别名。

        历史问题：工具链文档与脚本里到处是 end_sketch，但没有任何
        "明确退出草图"的公开 API，脚本只能自己去摸 skm.InsertSketch，
        一旦 SW 版本差异就会静默失败。这里给出唯一合法入口，
        语义与 end_sketch 完全一致（含提交校验与告警）。
        """
        return self.end_sketch(merge=merge)

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
        self._ensure_area_context()
        """中心矩形：中心 (cx,cy)，宽 w，高 h。

        ── 【BUG-D 修复】明确语义，避免与 add_component 混淆 ──────────────
        测试反馈：rect() 是【中心+宽高】语义，而 add_component(x,y,z) 用的是
          【包围盒中心】非原点 —— 两者坐标语义不同，混用会导致零件偏位。
        这里把契约写死在 docstring 里，并提示两者差异：

          rect(cx, cy, w, h)          → 草图平面内的中心矩形
                                         (cx,cy)=几何中心，w/h=宽度/高度
          add_component(path, x,y,z)  → 组件插入点 = 零件【包围盒中心】
                                         不是零件的原点，也不是角点

        曲面草图模式下自动应用Y偏移。

        Args:
            cx, cy: 矩形【中心】坐标（mm）
            w, h:   宽、高（mm），可为负以翻转方向
        """
        self._ensure_sketch_active()
        adj_cy = cy + self._surface_sketch_offset
        x1, y1 = (cx - w / 2) * MM, (adj_cy + h / 2) * MM
        x2, y2 = (cx + w / 2) * MM, (adj_cy - h / 2) * MM
        self.skm.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)
        # 【Bug T1 修复】记录轮廓面积，供 cut() 期望体积校验
        try:
            self._sketch_area_mm2 += abs(float(w) * float(h))
        except Exception:
            pass
        return self

    def circle(self, cx, cy, r):
        self._ensure_area_context()
        """圆心 (cx,cy)，半径 r。
        曲面草图模式下自动应用Y偏移。Right Plane 模式下 x 固定为 0。"""
        self._ensure_sketch_active()
        adj_cy = cy + self._surface_sketch_offset
        sketch_cx = 0.0 if self._right_plane_mode else cx
        self.skm.CreateCircleByRadius(sketch_cx * MM, adj_cy * MM, 0, r * MM)
        # 【Bug T1 修复】记录轮廓面积，供 cut() 期望体积校验
        try:
            import math as _m
            self._sketch_area_mm2 += _m.pi * float(r) * float(r)
        except Exception:
            pass
        # 【底面切除迁移】记录圆（局部坐标 cx,cy,r），供 cut() 失败后在对侧
        # 面重建同位轮廓
        try:
            if not hasattr(self, "_sketch_circles_local"):
                self._sketch_circles_local = []
            self._sketch_circles_local.append(
                (float(sketch_cx), float(adj_cy), float(r)))
        except Exception:
            pass
        return self

    def line(self, x1, y1, x2, y2):
        self._ensure_area_context()
        """画直线。曲面草图模式下自动应用Y偏移。"""
        self._ensure_sketch_active()
        adj_y1 = y1 + self._surface_sketch_offset
        adj_y2 = y2 + self._surface_sketch_offset
        self.skm.CreateLine(x1 * MM, adj_y1 * MM, 0, x2 * MM, adj_y2 * MM, 0)
        return self

    def polyline(self, points, close=None):
        """折线：points = [(x1,y1), (x2,y2), ...]，自动连成连续折线。
        曲面草图模式下自动应用Y偏移。

        ── 【Bug-26/24 修复】默认自动闭合轮廓 ──────────────────────────────
        原缺陷：本方法【不自动闭合】—— 首尾不连，于是：
          · 挤出/切除时轮廓不封闭 → FeatureExtrusion3 / FeatureCut3 返回 None
            或 com_error(-2147352561 '非选择性的参数')；
          · 两个房间（壳体机架 Bug-26、支撑结构 Bug-24）各自独立踩到同一个坑，
            最终都靠"在末尾重复起点"这种手工 workaround 才成功。
        修复：当点集 >= 3 且首尾不重合时，自动补一条回到起点的线。
        close=False 可显式关闭（画开放折线/中心线时用）。
        """
        self._ensure_area_context()
        self._ensure_sketch_active()
        adj_pts = [(x * MM, (y + self._surface_sketch_offset) * MM) for x, y in points]
        for i in range(len(adj_pts) - 1):
            self.skm.CreateLine(adj_pts[i][0], adj_pts[i][1], 0,
                                adj_pts[i + 1][0], adj_pts[i + 1][1], 0)
        # ── 自动闭合（Bug-26）：>=3 点且首尾不同 → 补回起点的边 ──────────
        _do_close = (close is True) or (close is None and len(adj_pts) >= 3)
        if _do_close and len(adj_pts) >= 3:
            try:
                _f, _l = adj_pts[0], adj_pts[-1]
                _gap = abs(_f[0] - _l[0]) + abs(_f[1] - _l[1])
                # 0.05mm 以内视为已闭合，不再重复画（避免零长线）
                if _gap > 0.05 * MM:
                    self.skm.CreateLine(_l[0], _l[1], 0, _f[0], _f[1], 0)
            except Exception:
                pass
        return self

    def centerline(self, x1, y1, x2, y2):
        """中心线（旋转特征的旋转轴）。曲面草图模式下自动应用Y偏移。"""
        self._ensure_sketch_active()
        adj_y1 = y1 + self._surface_sketch_offset
        adj_y2 = y2 + self._surface_sketch_offset
        self.skm.CreateCenterLine(x1 * MM, adj_y1 * MM, 0, x2 * MM, adj_y2 * MM, 0)
        return self

    # ---------- 特征（尺寸单位 mm）----------
    def select_all_sketch_segments(self, append_first=False):
        """选中当前草图的所有线段（用于复杂轮廓的特征创建）。

        ── 【C01 修复 · 2026-10-29 真机实测】选段策略已定案 ─────────────────
        实测（SW2025 SP5.0，24 段复杂轮廓）：
          · Select4(Append, SelectData) → **0/24 全抛 com_error**（官方已标 Obsolete）；
          · Select2(Append, 0)          → **24/24 全中**；
          · Select(Append)（Obsolete）  → 可用兜底。
        因此重试链只保留 Select2 / Select，**Select4 已从代码中彻底移除**
        （原实现把它留在链里并缓存结果，一旦缓存成 Select4 就全盘失败 ——
          现场表现为"选段 20/24 部分失败"）。

        另：必须在【草图仍激活】时选中段 —— 退出后段名可见性会变。

        本方法处理"草图激活时"的情况（推荐路径 A）。

        Args:
            append_first: 第一个段是否用追加模式（默认 False=先清空）

        Returns: (ok: bool, count: int)
        """
        try:
            self.model.ClearSelection2(True)
            sk = self.skm.ActiveSketch
            if sk is None:
                self._sel_diag = "ActiveSketch 为 None（草图未激活）"
                return False, 0

            # ── 【F-1 修复·第十轮】多种方式取段集合 ──────────────────────
            # 专家提示：late-binding 下 ISketch::GetSketchSegments 的取向
            #   可能不同（属性 vs 方法），且失败会被 except 静默吞掉。
            #   这里显式尝试三种写法，并记录各自的失败原因。
            _segs = None
            _segs_diag = []
            for _how, _getter in (
                ("属性 GetSketchSegments", lambda: sk.GetSketchSegments),
                ("方法 GetSketchSegments()", lambda: sk.GetSketchSegments()),
                ("方法 GetSketchSegments2()", lambda: sk.GetSketchSegments2()),
            ):
                try:
                    _v = _getter()
                    if _v is not None:
                        _segs = _v
                        _segs_diag.append("%s->ok" % _how)
                        break
                    _segs_diag.append("%s->None" % _how)
                except Exception as _e:
                    _segs_diag.append("%s->%s" % (_how, type(_e).__name__))
                    continue
            self._sel_diag = "取段: " + " | ".join(_segs_diag)
            if _segs is None:
                return False, 0

            try:
                _list = list(_segs) if isinstance(_segs, tuple) else [_segs]
            except Exception:
                _list = []
            self._sel_diag += " 段数=%d" % len(_list)

            # ══ 【测试部反馈修复·真机探测版】SW2025 段选中策略 ═══════════
            # 真机探测结论（_probe_selseg.py，SW2025 实测 7 段复杂轮廓）：
            #   A Select4(Append,SelectData) → com_error -2147352573 '找不到成员'
            #     （late-binding 下连成员都解析不到，此路彻底不通）；
            #   B Select2(Append, 整数Mark)   → 7/7 全中 ✅（Mark 必须是整数，
            #     旧代码传 None 才是 -2147352561 '非选择性的参数' 的来源）；
            #   C Select(Append)  (Obsolete)  → 7/7 全中 ✅（可用兜底）；
            #   D AddSelectionListObject      → 逐项 com_error，不可用；
            #   E SelectByID2 按段名           → 选中数≠段数（误选其他实体），禁用；
            #   F SelectByID2 按拾取点         → 段对象无坐标方法，不可用。
            # 实现：先用第 1 段探测出可用策略（缓存到实例，避免每段重试），
            #   再用同一策略选完全部段；最终强制"选中数==段数"验收。
            _sel_mgr_ref = None
            try:
                _sel_mgr_ref = self.model.SelectionManager
            except Exception:
                _sel_mgr_ref = None

            def _try_one(s, append, strategy):
                """用指定策略选一段；成功返回 True。"""
                if strategy == "Select2":
                    fn = getattr(s, "Select2", None)
                    if fn is not None and fn(append, 0):
                        return True
                    return False

                if strategy == "Select":
                    fn = getattr(s, "Select", None)
                    try:
                        return bool(fn is not None and fn(append))
                    except Exception:
                        return False
                return False

            # 策略探测：真机实测优先级 Select2 > Select（【C01 修复】剔除 Select4）
            #
            # ── 【C01 修复 · 2026-10-29 真机实测】Select4 必须从重试链里摘掉 ──
            # 实测（SW2025 SP5.0，24 段复杂轮廓）：
            #   · ISketchSegment.Select4(Append, SelectData) → **0 / 24 全抛 com_error**；
            #   · ISketchSegment.Select2(Append, 0)          → **24 / 24 全中**；
            #   · ISketchSegment.Select(Append)（Obsolete）  → 可用兜底。
            # 而官方帮助已把 Select4 标为 Obsolete，推荐 Select2。
            #
            # 原实现的坑：策略探测顺序里带着 Select4，且探测结果会被【缓存】
            #   到 self._seg_select_strategy。一旦某次上下文把策略定成 Select4，
            #   后续所有选段都走 Select4 → 本机全抛异常 → 现场表现为
            #   "选段 20/24 部分失败 / 选段=0"。
            # 现在：重试链只剩 Select2 / Select；**不再回退到 Select4**。
            _STRATEGIES = ("Select2", "Select")
            _strategy = getattr(self, "_seg_select_strategy", None)
            if _strategy not in _STRATEGIES:
                _strategy = None
                for _cand in _STRATEGIES:
                    try:
                        self.model.ClearSelection2(True)
                    except Exception:
                        pass
                    if _try_one(_list[0], False, _cand):
                        _strategy = _cand
                        break
                if _strategy is None:
                    self._sel_diag = "无可用段选中策略(Select2/Select 均失败)"
                    return False, 0
                try:
                    self._seg_select_strategy = _strategy
                except Exception:
                    pass
                try:
                    self.model.ClearSelection2(True)
                except Exception:
                    pass

            n = 0
            _per_seg_err = []
            for _idx, s in enumerate(_list):
                try:
                    if _try_one(s, n > 0, _strategy):
                        n += 1
                    else:
                        _per_seg_err.append("%s@%d:retFalse" % (_strategy, _idx))
                except Exception as _e2:
                    _per_seg_err.append("%s@%d:%s" % (_strategy, _idx, type(_e2).__name__))
            if _per_seg_err:
                self._sel_diag += " 选段错误=%s" % (_per_seg_err[:3],)

            try:
                c = int(self.model.SelectionManager.GetSelectedObjectCount2(-1))
            except Exception:
                c = n
            self._sel_diag += " 选中数=%s/段数=%s 策略=%s" % (c, len(_list), _strategy)
            # 【测试部反馈·验收判据】选中数必须==段数才算成功：
            # 部分选中时特征拿不到闭合轮廓（T3 根因），必须判失败并留痕，
            # 绝不能返回"部分成功"让上层误以为轮廓已就绪。
            if c < len(_list):
                self._sel_diag += " 【部分选中!】选段错误=%s" % (_per_seg_err[:5],)
                return False, c
            return (c > 0 and c == len(_list)), c
        except Exception as e:
            self._sel_diag = "select_all_sketch_segments 异常: %r" % (e,)
            return False, 0

    # ══ 【F-04 修复 · 2026-10-31】拉伸轮廓选择（清选择之后必须重新选）══
    # 事故（两个小屋各自复现）：标准写法 begin_sketch → rect/circle →
    # end_sketch → extrude() 连续失败，FeatureExtrusion3 返回 None，
    # 报「选择集为 0：轮廓没被选中」。
    # 根因链（Lead 已定位，本方法负责第 ③ 步）：
    #   ① end_sketch 退出前在激活态预选轮廓段（Bug T2 修复，:7609）；
    #   ② extrude 开头无条件 _reset_selection_state() → ClearSelection2(True)
    #      + ForceRebuild3(False)，把①的预选【清掉】；
    #   ③ 之后直接调 FeatureExtrusion3，中间【没有任何重选轮廓的步骤】
    #      → 选择集 0 → 返回 None。
    # 结论：清选择本身是对的（cut 需要它自愈脏选择），错的是"清了不重选"。
    #   本方法就是"重选"那一步，且与 _ensure_cut_profile_selected 明确分工：
    #     · cut  → 需要的是【草图内的段/边】（切入已有实体）；
    #     · extrude → 需要的是【草图特征本身】（"SKETCH" 类型，拉伸基体）。
    #   两者不可互换：拿段去拉伸同样会失败。
    #
    # 【类型库依据】engine...\83A33D31-...x0x33x0.py
    #   · IModelDocExtension.SelectByID2(Name, Type, X, Y, Z, Append, Mark,
    #       Callout, SelectOption) → 9 参（L42978；vtable L89320），
    #       Callout 封送类型 (9,1) = VT_DISPATCH 接口 IID 9CAAA1AA-...，
    #       所以【必须】传 VARIANT(VT_DISPATCH, None)，传裸 None 会类型不匹配。
    #   · ISelectionMgr.GetSelectedObjectCount2(Mark)（L53146）
    #   · ISelectionMgr.SetSelectedObjectMark(AtIndex, Mark, Action)（L53411）
    #   · IFeatureManager.FeatureExtrusion3 为 23 参（L22598；vtable L82748），
    #       第 20 位 UseAutoSelect 传 True 时 SW 会【忽略选择集】自己找轮廓。
    #
    # ── 【S-08 根因修复 · 2026-11-04 真机实测】Mark 不能随便给 ─────────────
    # 原值 1（沿用"cut 的轮廓标记"约定），但【拉伸轮廓的 Mark 必须是 0】。
    # 实测矩阵（SW2025 SP5.0，同一零件同一草图）：
    #     Mark=0 → FeatureExtrusion3 成功，bodies 0→1
    #     Mark=1 → 返回 None，bodies 0      ← 旧值，正是"extrude 全灭"的元凶
    #     Mark=2 → 返回 None，bodies 0
    #     Mark=4 → 成功，bodies 0→1（未文档化，不作依赖）
    # 也就是说：SelectByID2 返回 True、选择集计数为 1，但 SW 内部仍按
    #   "带 mark 的选择"去解析轮廓 —— mark 1/2 的组合不被 FeatureExtrusion3
    #   接受，于是静默返回 None。原实现据此判"选择集非空 → 应该能拉伸"，
    #   外层再把它归因为 no_body，整条建模主链路 100% 失败。
    # 修复：拉伸轮廓一律用 Mark=0（不参与标记过滤），这与官方宏录制出来的
    #   调用完全一致。_mark_current_selection 仍然保留（诊断用），
    #   但【不再】把它当成功判据。
    EXTRUDE_PROFILE_MARK = 0
    # cut 的轮廓标记与拉伸不同，单独保留（cut 走的是"选段"链路）。
    CUT_PROFILE_MARK = 1

    def _selection_count(self, mark=-1):
        """读当前选择集数量；读不到返回 -1（调用方必须按"未知"处理）。"""
        try:
            return int(self.model.SelectionManager.GetSelectedObjectCount2(mark))
        except Exception:
            return -1

    def _mark_current_selection(self, mark):
        """把当前选择集里所有对象打上 mark 标记（拉伸轮廓标记位）。

        【类型库】ISelectionMgr.SetSelectedObjectMark(AtIndex, Mark, Action)
        （L53411）。Action 传 0 = 设置标记（swSelectionSetMarkAction 的默认值）。
        整段失败不致命：FeatureExtrusion3 在本仓库既有行为下并不强制要求标记，
        这里打标记是为了让"轮廓 = mark 1"成为可观测事实（便于诊断与测试）。
        Returns: 打标成功的数量
        """
        n = self._selection_count(-1)
        if n <= 0:
            return 0
        _ok = 0
        for i in range(1, n + 1):
            try:
                self.model.SelectionManager.SetSelectedObjectMark(i, int(mark), 0)
                _ok += 1
            except Exception:
                continue
        return _ok

    def _select_extrude_profile(self):
        """【F-04 修复】在【已退出草图】的状态下选中"可拉伸的草图轮廓特征"。

        ══ 为什么必须单独有这个步骤 ══════════════════════════════════════
        extrude() 开头会 _reset_selection_state()（清选择 + 强制重建），
        这两步都会让 SW 的当前选择集归零；而 FeatureExtrusion3 在
        UseAutoSelect=True 时也不保证能找到轮廓（实测返回 None）。
        因此清完之后【必须】显式按名选中草图特征，再调 FeatureExtrusion3。

        ══ 选哪些（跨版本/跨 locale）════════════════════════════════════
        只认 _is_sketch_feature()（本文件既有封装，:13632）判定的特征：
          · "Sketch"          未消费草图
          · "ProfileFeature"  已被消费过的草图特征（最常见）
        并【排除】"OriginProfileFeature"（原点），它是 ProfileFeature 的子类
        名，误选它必然拉伸失败 —— 小屋绕行方案里专门踩过这个坑。
        优先用 begin_sketch 记录的 _active_sketch_name（显式名，不猜）；
        名字不可靠时退回"特征树里最后一个未被消费的草图特征"。

        Returns: dict {ok, selected, name, method, tried, error?}
        """
        out = {"ok": False, "selected": 0, "name": None, "method": None,
               "tried": [], "reason": ""}
        # ── 步骤 1：确定候选草图名（显式名优先，绝不瞎猜「草图1」）────────
        _cand = None
        try:
            _nm = getattr(self, "_active_sketch_name", None)
            _cons = getattr(self, "_consumed_sketches", None) or set()
            if _nm and str(_nm) not in _cons:
                _cand = str(_nm)
                out["tried"].append(u"显式名=%s" % _cand)
        except Exception as _e:
            out["tried"].append("显式名读取异常: %r" % (_e,))
        if not _cand:
            # 兜底：特征树里最后一个草图特征（排除原点）
            try:
                f = self.model.FirstFeature()
                _guard = 0
                while f is not None and _guard < 5000:
                    _guard += 1
                    try:
                        # 【F-04】GetTypeName2 是【方法】(类型库 L21627)，
                        # 用兼容方法/属性两种暴露方式的 _feature_type_name
                        _tn = _feature_type_name(f)
                        _excl = ("OriginProfileFeature", "SketchBlock",
                                 "SketchPicture")
                        if (_tn not in _excl and _is_sketch_feature(f)):
                            _cand = str(f.Name)
                    except Exception:
                        pass
                    try:
                        f = f.GetNextFeature()
                    except Exception:
                        break
                out["tried"].append(u"特征树兜底=%s" % _cand)
            except Exception as _e:
                out["tried"].append("特征树遍历异常: %r" % (_e,))

        # ── 步骤 2：选中草图特征 ──────────────────────────────────────────
        # ══ 【F-04 修复·路径优先级按官方建议排序】══════════════════════════
        # 【网页】CodeStack《Selecting SOLIDWORKS objects by name and
        #   coordinates using API》（IModelDocExtension::SelectByID2）
        #   https://www.codestack.net/solidworks-api/document/selection/select-by-id/
        #   原文：「Sketch elements (lines, arcs, points, splines etc.) do not
        #   have permanent names. So it is not valid to use the name recorded
        #   by the macro in SelectByID2 method.」
        #   「...Use this method instead of using the selection methods on the
        #   following objects: IAnnotation, IComponent2, IFeature ...」
        #   → 结论：SelectByID2 是"能用但有已知缺陷"的通用手段；
        #     对【特征】（草图特征就是 IFeature）应优先用 IFeature::Select2。
        # 【类型库】IFeature.Select2(Append, Mark) —— 2 参（L21957，vtable 21957）
        #   比 SelectByID2 的 9 参更简单、无 Callout 封送风险。
        # 因此本步骤顺序：① IFeature.Select2 → ② SelectByID2（Callout 必须 VARIANT）
        #   两条都保留：①是官方推荐，②是小屋实测跑通过的绕行方案（兜底）。

        def _feature_by_name(name):
            """按名从特征树取 IFeature 对象；找不到返回 None。"""
            if not name:
                return None
            try:
                f = self.model.FirstFeature()
                _g = 0
                while f is not None and _g < 5000:
                    _g += 1
                    try:
                        if str(f.Name) == str(name):
                            return f
                    except Exception:
                        pass
                    try:
                        f = f.GetNextFeature()
                    except Exception:
                        break
            except Exception:
                pass
            return None

        def _try_select_feature(name):
            """① IFeature.Select2(Append, Mark)（官方推荐）→ (ok, 选中数, used)"""
            feat = _feature_by_name(name)
            if feat is None:
                out["tried"].append(u"IFeature.Select2(%s): 特征树无此特征" % (name,))
                return False, -1, False
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
            try:
                _ok = feat.Select2(False, int(self.EXTRUDE_PROFILE_MARK))
            except Exception as _e:
                out["tried"].append("IFeature.Select2(%s) 异常: %r" % (name, _e))
                return False, -1, False
            _n = self._selection_count(-1)
            out["tried"].append(u"IFeature.Select2(%s)->ok=%s n=%s"
                                % (name, _ok, _n))
            return bool(_ok), (int(_n) if _n is not None else -1), True

        def _try_select(name):
            """② SelectByID2（Callout 必须 VARIANT 封送）→ (ok, 选中数)"""
            if not name:
                return False, 0
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
            try:
                _sel = self.ext.SelectByID2(
                    str(name), "SKETCH", 0.0, 0.0, 0.0,
                    False,          # Append
                    int(self.EXTRUDE_PROFILE_MARK),   # Mark
                    # ── 【F-04 关键】Callout 必须是 VARIANT(VT_DISPATCH,None)
                    # 类型库 (8,Name)(5,1)(9,1) → Callout 是接口指针
                    # 9CAAA1AA-F2FC-11D5-AF66-00C04F603FAF（L7155 证实该 IID
                    # 就是 ICallout）；传裸 None 会报"类型不匹配"，
                    # 这正是小屋绕行方案里被卡住的那一步。
                    self._empty,
                    0)              # SelectOption: 0 = 默认（按名选）
                _n = self._selection_count(-1)
                return bool(_sel), (int(_n) if _n is not None else -1)
            except Exception as _e:
                out["tried"].append("SelectByID2(%s) 异常: %r" % (name, _e))
                return False, -1

        def _select_any(name):
            """① IFeature.Select2 优先，② SelectByID2 兜底。返回 (ok, n, method)"""
            _o1, _n1, _used = _try_select_feature(name)
            if _o1 and _n1 > 0:
                return True, _n1, "IFeature.Select2"
            _o2, _n2 = _try_select(name)
            if _o2 and _n2 > 0:
                return True, _n2, "SelectByID2/SKETCH"
            return False, max(_n1, _n2), None

        _ok, _n, _how = _select_any(_cand)
        if _ok and _n > 0:
            out.update({"ok": True, "selected": _n, "name": _cand,
                        "method": _how})
            self._mark_current_selection(self.EXTRUDE_PROFILE_MARK)
            return out

        # ── 步骤 3：按名失败 → 遍历特征树逐个试（每个候选都先清选择）────
        try:
            f = self.model.FirstFeature()
            _guard = 0
            _hit = None
            while f is not None and _guard < 5000:
                _guard += 1
                try:
                    _tn = _feature_type_name(f)
                    if (_tn != "OriginProfileFeature"
                            and _is_sketch_feature(f)):
                        _nm2 = str(f.Name)
                        _ok2, _n2, _how2 = _select_any(_nm2)
                        if _ok2 and _n2 > 0:
                            _hit = (_nm2, _n2, _how2)
                            break
                except Exception:
                    pass
                try:
                    f = f.GetNextFeature()
                except Exception:
                    break
            if _hit:
                out.update({"ok": True, "selected": _hit[1], "name": _hit[0],
                            "method": "%s/遍历特征树" % _hit[2]})
                self._mark_current_selection(self.EXTRUDE_PROFILE_MARK)
                return out
        except Exception as _e:
            out["tried"].append("遍历试选异常: %r" % (_e,))

        # ── 步骤 4：全都失败 → 显式给出可读诊断，绝不静默返回 ────────────
        out["reason"] = (
            "无法选中可拉伸的草图轮廓。已尝试：%s。"
            "常见原因：①草图已被上一个特征消费（_consumed_sketches 记录）；"
            "②草图特征类型既不是 Sketch 也不是 ProfileFeature（草图块/图片）；"
            "③装配体/零件文档不是活动文档；④草图是空草图。"
            % (" | ".join(out["tried"])[-600:],))
        return out

    def extrude(self, depth, symmetric=False, draft_deg=0, auto_select=True):
        """拉伸凸台。depth 单位 mm；symmetric=True 两侧对称。

        Bug 3 修复: 放弃 extrude-to-point 方法，使用标准的 FeatureExtrusion3。
        Bug 9 修复: 负深度自动反转方向，FeatureExtrusion3 不接受负深度参数。
        """
        # ══ 【R5 修复】拉伸同样要先清脏选择再选轮廓（与 cut 同源问题）═════
        # 同一零件第二次 extrude 常因"上一步残留选择"选错轮廓，表现为
        # FeatureExtrusion3 返回 None 或拉伸出多余的体。
        try:
            self._reset_selection_state(reason="extrude 前置")
        except Exception:
            pass
        # ══ 【F-04 修复】清完脏选择之后【必须重新选中轮廓】再调 API ═══════
        # 事故：_reset_selection_state 会 ClearSelection2(True) +
        #   ForceRebuild3(False)，把 end_sketch 刚预选好的轮廓段一起清掉，
        #   而下面直接调 FeatureExtrusion3 —— 中间没有任何重选步骤，
        #   于是「选择集为 0」→ 返回 None → 标准路径 100% 失败。
        # 注意选择目标不同：cut 要的是【草图里的段/边】，extrude 要的是
        #   【草图特征本身】("SKETCH")，所以这里不能复用 cut 的那套选段逻辑。
        _prof = self._select_extrude_profile()
        if not _prof.get("ok"):
            # 选不中轮廓 → 显式报错（绝不静默返回 None 让上层瞎猜）
            _diag0 = diagnose_feature_failure(
                self, api="FeatureExtrusion3",
                extra={"depth_mm": depth,
                       "profile_tried": _prof.get("tried")})
            raise RuntimeError(
                "拉伸前【未能选中草图轮廓】，FeatureExtrusion3 必然返回 None。\n"
                "  选择集: %s\n"
                "  尝试记录: %s\n"
                "【诊断】原因=%s（%s）\n"
                "建议：%s\n"
                "另：标准写法必须是 begin_sketch → rect()/circle() → "
                "end_sketch → extrude()；若草图已被上一特征消费，"
                "请重新 begin_sketch 画新轮廓。"
                % (_prof.get("selected"), _prof.get("reason"),
                   _diag0.get("cause"), _diag0.get("detail"),
                   _diag0.get("advice")))
        self._extrude_profile_used = _prof.get("name")
        T1 = _get_midplane_enum(self.sw) if symmetric else SW_END_BLIND
        reverse = bool(depth < 0)  # 负深度 → 反转拉伸方向
        d = abs(depth) * MM
        # ══ 【R5 修复】参数布局漂移：负深度被喂进了「拔模开关」位 ══════════
        # 旧实现在第 8 个实参（官方 Dchk1 = 方向1是否启用拔模）处传了
        #   reverse —— 于是 depth<0 不是"反向拉伸"，而是【给拉伸加了拔模】，
        #   方向根本没反转，几何悄悄错掉（SW 不报错）。
        # 官方形参（类型库 IFeatureManager.FeatureExtrusion3，L22597，23 参）：
        #   Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
        #   Dang1, Dang2, OffsetReverse1, OffsetReverse2, TranslateSurface1,
        #   TranslateSurface2, Merge, UseFeatScope, UseAutoSelect,
        #   T0, StartOffset, FlipStartOffset
        # 要点：
        #   · 第 3 位 Dir 才是「反向」→ reverse 必须落这里；
        #   · 第 18 位是 Merge（合并结果），【不是】切除开关 ——
        #     类型库里根本不存在 AddPad/cut 形参，FeatureExtrusion3 只能增料。
        #     这正是旧代码"用 Extrusion3 当切除兜底"必然错的原因。
        #   · 第 8/9 位 Dchk1/Dchk2 是拔模开关，第 11/12 位 Dang1/Dang2 是
        #     拔模角（弧度）—— draft_deg 参数此前被声明却从未传入（见
        #     solidworks-modeling.md 第 289 行遗留项），这里一并补上。
        _draft_on = bool(draft_deg)
        _draft_rad = math.radians(float(draft_deg or 0.0))
        # ══ 【F-04 修复】调用前【再确认一次】选择集非空 ══════════════════════
        # 理由：从 _select_extrude_profile() 成功到真正调 FeatureExtrusion3
        #   之间还隔着若干语句（含可能触发 SW 内部清选择的属性访问）。
        #   事故里的报错正是「选择集为 0」—— 那就在调用前把它变成【断言】：
        #   一旦归零，这里立刻抛出可读错误，而不是让 SW 返回一个 None
        #   让上层猜"是不是参数错了"。
        _n_sel = self._selection_count(-1)
        if _n_sel == 0:
            _diag_s = diagnose_feature_failure(
                self, api="FeatureExtrusion3",
                extra={"depth_mm": depth,
                       "profile": self._extrude_profile_used})
            raise RuntimeError(
                "调 FeatureExtrusion3 前【选择集为 0】—— 轮廓在选好之后又被"
                "清掉了（典型是中间某步触发了 SW 清空选择）。\n"
                "  轮廓=%s\n"
                "【诊断】原因=%s（%s）\n建议：%s"
                % (self._extrude_profile_used,
                   _diag_s.get("cause"), _diag_s.get("detail"),
                   _diag_s.get("advice")))
        # ── 【D4 修复】用 com_retry 包住：用户正在操作 SW 时，
        #    "调用被拒绝/服务器忙" 会退避重试（300ms→5s），而不是让上层
        #    把整个建模脚本重跑一遍（那正是同一零件 4 版脚本的成因）。
        feat = com_retry(
            lambda: self.fm.FeatureExtrusion3(
                True,            # Sd      单向
                False,           # Flip    反转切除侧（增料特征无意义，恒 False）
                reverse,         # Dir     ★反向（负深度走这里，不再污染 Dchk1）
                T1,              # T1      终止条件1
                0,               # T2      终止条件2（单向不用）
                d,               # D1      深度
                0,               # D2
                _draft_on,       # Dchk1   方向1拔模开关
                False,           # Dchk2
                False,           # Ddir1   拔模方向（False=向内）
                False,           # Ddir2
                _draft_rad,      # Dang1   拔模角（弧度）
                0.0,             # Dang2
                False,           # OffsetReverse1
                False,           # OffsetReverse2
                False,           # TranslateSurface1
                False,           # TranslateSurface2
                True,            # Merge   ★合并结果（第 18 位，不是切除位）
                False,           # UseFeatScope
                auto_select,     # UseAutoSelect
                0,               # T0      起始条件：0=草图基准面
                0.0,             # StartOffset
                False),          # FlipStartOffset
            _what="FeatureExtrusion3(拉伸)",
            _log=self.warnings.append)
        if feat is None:
            # ── 【Bug-20 修复】失败时给出可操作诊断，而不是只丢一句"创建失败" ──
            _diag = diagnose_feature_failure(self, api="FeatureExtrusion3",
                                             extra={"depth_mm": depth})
            # 【R5 修复】None 必须显式报错，并补上"净距过小"这类可读原因
            raise RuntimeError(
                "FeatureExtrusion3 返回 None，拉伸特征创建失败。"
                "【诊断】原因=%s（%s）建议：%s\n"
                "【R5 补充】%s"
                % (_diag.get("cause"), _diag.get("detail"),
                   _diag.get("advice"),
                   _cut_none_hint(self, "FeatureExtrusion3")))
        # 【R5 修复】返回 CDispatch 不等于成功 —— 必须验证真的生成了实体。
        #   台账里"FeatureExtrusion3 返回 none/no_body"正是这种情况：
        #   调用没抛异常、对象也拿得到，但零件里没有任何新实体。
        try:
            _b = self.model.GetBodies2(0, 1)
            _bl = list(_b) if isinstance(_b, tuple) else ([_b] if _b else [])
        except Exception:
            _bl = None
        if _bl is not None and len(_bl) == 0:
            raise RuntimeError(
                "FeatureExtrusion3 返回了对象，但零件内【没有任何实体】"
                "（no_body）—— 拉伸没有真正生成材料，不能当成功。\n"
                "【R5 补充】%s" % _cut_none_hint(self, "FeatureExtrusion3"))
        self._visual_step("extrude")
        self.rebuild()  # Bug 9: 重建模型以清除 COM 内部选择状态累积
        # ── 【BUG-05/08 修复】首个实体已生成 → 此刻才能真正赋材质 ──────
        try:
            self._try_apply_pending_material()
        except Exception:
            pass
        # 【Bug T1 修复】基体草图轮廓已被拉伸特征【消费】，标记之 ——
        # 与 cut/revolve 同一防护：后续任何特征绝不能再选中/沿用该轮廓，
        # 杜绝"把基体轮廓当切除轮廓"的灾难性误选。
        self._mark_sketch_consumed()
        return feat

    def extrude_with_holes(self, w, h, depth, holes=(), cx=0.0, cy=0.0,
                           symmetric=False):
        """【Bug-24 修复】高层封装：多环基体一次挤出（孔在基体草图里画成内环）。

        为什么需要（支撑结构房间的实战经验，唯一 4/4 一次成功的房间）：
          矩形多孔【切除】的选段在 SW 里不稳定（rect 矩形切除选段失败、
          底面切除迁移）；而"把所有孔在基体草图里一次性画成内环再挤出"零切除，
          是最稳的建模模式。本封装把这条经验固化，避免每个小屋重复踩坑。

        参数：
          w,h    —— 矩形外形尺寸（mm，以 cx,cy 为中心）
          depth  —— 挤出深度（mm）
          holes  —— 孔列表，每项 (hx, hy, r) 或 (hx, hy, r, kind)
                    kind="circle"（默认）或 "rect"，rect 时 r=(hw, hh)
          cx,cy  —— 矩形中心（相对草图原点，mm）
        返回：feature（同 extrude）

        用法：
            m.begin_sketch("Top Plane")
            m.extrude_with_holes(180, 60, 8,
                                 holes=[(-70, 0, 2.5), (70, 0, 2.5)],
                                 cx=0, cy=0)
        """
        self._ensure_sketch_active()
        # 外轮廓
        self.rect(cx, cy, w, h)
        # 内环（孔）：SW 在同草图内画闭合内环即自动成为挖空区域
        for hp in (holes or ()):
            try:
                hx, hy, rr = hp[0], hp[1], hp[2]
                kind = (hp[3] if len(hp) > 3 else "circle")
            except Exception:
                continue
            if kind == "rect" and isinstance(rr, (tuple, list)) and len(rr) >= 2:
                self.rect(hx, hy, rr[0], rr[1])
            else:
                self.circle(hx, hy, rr)
        self.end_sketch()
        return self.extrude(depth, symmetric=symmetric)

    def _topology_signature(self):
        """【Bug-37/42/43 修复】采集实体的【拓扑签名】，用于判定切除是否真的生效。

        为什么需要它（三个 Bug 的共同根因）：
          Bug-37 薄壁件：Ø6 卡扣孔穿透 2mm 薄壁，真实去料率仅 1.97% < 3% 阈值；
          Bug-42 轴承座：circle+cut 方向偏，去料率 0.7%；
          Bug-43 同类：方向错但已贯穿，去料率 0.7% 被判"几乎肯定没贯穿"。
          → 三个都被"去料率"这一个指标误判，而它们【几何上确实切穿了】。

        修复思路：不再只看"去掉了多少体积"，而是看【实体拓扑是否改变】：
          · 体积是否变小（真去料 vs 完全没动）；
          · 面数是否增加（打孔会在实体上新增内孔面）；
          · 包围盒是否改变（贯穿可能切掉外形）；
          · 实体数量是否变化（切断/多体）。
        只要"体积变小 且 面数增加"，就说明确实切出了新特征 ——
        哪怕去料率只有 0.7%（薄壁小孔），也应判成功。

        Returns: dict {volume_mm3, face_count, edge_count, body_count, bbox_mm}
        """
        sig = {"volume_mm3": None, "face_count": None, "edge_count": None,
               "body_count": None, "bbox_mm": None}
        try:
            sig["volume_mm3"] = self._body_volume_mm3()
        except Exception:
            pass
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            sig["body_count"] = len(bl)
            fc, ec = 0, 0
            bb_min = [float("inf")] * 3
            bb_max = [float("-inf")] * 3
            for b in bl:
                try:
                    faces = b.GetFaces()
                    fl = list(faces) if isinstance(faces, tuple) else ([faces] if faces else [])
                    fc += len(fl)
                except Exception:
                    pass
                try:
                    edges = b.GetEdges()
                    el = list(edges) if isinstance(edges, tuple) else ([edges] if edges else [])
                    ec += len(el)
                except Exception:
                    pass
                try:
                    bb = b.GetBodyBox()
                    for _i in range(3):
                        bb_min[_i] = min(bb_min[_i], float(bb[_i]))
                        bb_max[_i] = max(bb_max[_i], float(bb[3 + _i]))
                except Exception:
                    pass
            sig["face_count"] = fc
            sig["edge_count"] = ec
            if bb_min[0] != float("inf"):
                sig["bbox_mm"] = [round((bb_max[_i] - bb_min[_i]) * 1000.0, 4)
                                  for _i in range(3)]
        except Exception:
            pass
        return sig

    def _cut_geometry_verdict(self, before, after, through=False):
        """【Bug-37/42/43 修复】基于【几何/拓扑变化】判定切除是否真的生效。

        取代"去料率 >= 3%"这条对薄壁/小孔天然误判的判据。

        判定规则（任一成立即认为切除真的生效）：
          A) 体积确实变小（>1e-6 mm³）且【面数增加】
             —— 这是打孔/开槽的典型特征（新增内孔面）；
          B) 体积变小且【实体数变化】（切断/多体）；
          C) 体积变小且【包围盒改变】（贯穿切掉外形）；
          D) through=True 且体积变小（方向偏但确实去料）。
        只有"体积完全没变"或"取不到任何证据"才判失败。

        Returns: (ok: bool, reason: str, detail: dict)
        """
        detail = {"before": before, "after": after}
        try:
            v0, v1 = before.get("volume_mm3"), after.get("volume_mm3")
            if v0 is None or v1 is None:
                return False, "无法读取切除前后体积（证据不足）", detail
            dv = float(v0) - float(v1)
            detail["removed_mm3"] = round(dv, 4)
            if dv <= 1e-6:
                return False, "切除后体积未减小 —— 未真正去料", detail
            detail["removed_pct"] = round(dv / float(v0) * 100.0, 4) if v0 else None
            f0, f1 = before.get("face_count"), after.get("face_count")
            if f0 is not None and f1 is not None and f1 > f0:
                return True, ("体积减小且面数增加(%d->%d) —— 确实切出了新特征"
                              % (f0, f1)), detail
            b0, b1 = before.get("body_count"), after.get("body_count")
            if b0 is not None and b1 is not None and b0 != b1:
                return True, ("体积减小且实体数变化(%s->%s) —— 确实切断了材料"
                              % (b0, b1)), detail
            bb0, bb1 = before.get("bbox_mm"), after.get("bbox_mm")
            if bb0 and bb1 and any(abs(bb0[_i] - bb1[_i]) > 1e-4 for _i in range(3)):
                return True, "体积减小且包围盒改变 —— 确实切掉了外形材料", detail
            if through:
                return True, ("贯穿切除：体积已减小 %.4f mm³（方向可能有偏，"
                              "但确实去料）" % dv), detail
            return False, ("体积仅减小 %.6f mm³ 且无拓扑变化 —— 疑似未真正切除"
                           % dv), detail
        except Exception as e:
            return False, "几何判定异常: %r" % (e,), detail


    def add_dimension(self, x1, y1, x2, y2, value_mm=None, dim_type="smart"):
        """【Bug-34 修复】在草图上添加【尺寸约束】（annotate 能自动出图的前提）。

        ══ 为什么必须加这个 ═══════════════════════════════════════════════════
        台账回归结论（Bug-34 部分修复）：
          "annotate 命令已加但插 0 个标注 —— InsertModelAnnotations 只能插
           建模时已加尺寸，本轮建模未加约束 → 永远空转。"

        这是决定性的因果链：
          建模不加尺寸 → 模型里没有可插入的 DisplayDimension
            → drawing 的 InsertModelAnnotations 无可插入对象 → 0 个标注
            → GB/T 尺寸标注永远缺失。

        因此根治办法不是改 annotate，而是【在建模时就加尺寸约束】。
        本方法封装 SW 的草图尺寸 API（多版本签名兜底）。

        Args:
            x1, y1, x2, y2: 尺寸两端点（草图坐标，mm）
            value_mm: 期望尺寸值（None = 用 SW 当前测量值，不加驱动）
            dim_type: "smart"（默认）/ "horizontal" / "vertical" / "diameter" / "radius"
        Returns:
            {"ok", "value_mm", "api", "error"?}
        """
        out = {"ok": False, "value_mm": value_mm, "dim_type": dim_type}
        try:
            self._ensure_sketch_active()
            _sk = self.skm.ActiveSketch
            if _sk is None:
                out["error"] = "当前没有激活的草图 —— 请先 begin_sketch / begin_sketch_on_face"
                return out
            _k = MM
            _x1, _y1 = float(x1) * _k, float(y1) * _k
            _x2, _y2 = float(x2) * _k, float(y2) * _k
            _val = (float(value_mm) * _k) if value_mm is not None else None
            # ── SW 草图尺寸 API：AddDimension2 为主，AddDimension 回退 ──────
            _tried = []
            _dim = None
            for _api in ("AddDimension2", "AddDimension"):
                _fn = getattr(self.skm, _api, None)
                if _fn is None:
                    _tried.append("%s: 不存在" % _api)
                    continue
                try:
                    _dim = _fn(_x1, _y1, 0.0, _x2, _y2, 0.0)
                    _tried.append("%s: ok" % _api)
                    break
                except Exception as _e:
                    _tried.append("%s: %r" % (_api, _e))
                    continue
            out["api_tried"] = _tried
            if _dim is None:
                out["error"] = "草图尺寸 API 均不可用: %s" % " | ".join(_tried)
                out["hint"] = ("确认草图处于激活状态；SW 的 AddDimension2 需要"
                               "两点坐标且草图未退出。")
                return out
            out["api"] = _api
            # 设定尺寸值（驱动尺寸）
            if _val is not None:
                for _sm in ("SetSystemValue3", "SetSystemValue2", "SetSystemValue"):
                    try:
                        _sfn = getattr(_dim, _sm, None)
                        if _sfn is None:
                            continue
                        if _sm == "SetSystemValue3":
                            _sfn(_val, 1, None)   # 1 = swSetValue_InThisConfiguration
                        elif _sm == "SetSystemValue2":
                            _sfn(_val, 1)
                        else:
                            _sfn(_val)
                        out["value_applied"] = True
                        break
                    except Exception as _e2:
                        out.setdefault("set_value_errors", []).append("%s: %r" % (_sm, _e2))
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = "添加尺寸失败: %r" % (e,)
            return out

    def add_key_dimensions(self, points, dims=None):
        """【Bug-34 修复】批量添加关键尺寸（一次给多个，减少 COM 往返）。

        Args:
            points: [(x, y), ...] 草图关键点（如矩形的 4 个角）
            dims: 显式尺寸列表 [{"p1": (x,y), "p2": (x,y), "value": v, "type": "smart"}]
                  为 None 时自动按 points 相邻点添加尺寸。
        Returns:
            {"ok", "added", "failed", "total"}
        """
        out = {"ok": False, "added": [], "failed": [], "total": 0}
        try:
            _list = []
            if dims:
                for d in dims:
                    _list.append((d.get("p1"), d.get("p2"), d.get("value"),
                                  d.get("type", "smart")))
            elif points and len(points) >= 2:
                for i in range(len(points)):
                    _p1 = points[i]
                    _p2 = points[(i + 1) % len(points)]
                    _v = None
                    try:
                        import math as _math
                        _v = round(_math.hypot(_p2[0] - _p1[0],
                                               _p2[1] - _p1[1]), 3)
                    except Exception:
                        _v = None
                    _list.append((_p1, _p2, _v, "smart"))
            out["total"] = len(_list)
            for _p1, _p2, _v, _t in _list:
                if not _p1 or not _p2:
                    out["failed"].append({"p1": _p1, "p2": _p2, "reason": "点缺失"})
                    continue
                _r = self.add_dimension(_p1[0], _p1[1], _p2[0], _p2[1],
                                        value_mm=_v, dim_type=_t)
                if _r.get("ok"):
                    out["added"].append({"p1": _p1, "p2": _p2, "value_mm": _v})
                else:
                    out["failed"].append({"p1": _p1, "p2": _p2,
                                          "reason": _r.get("error")})
            out["ok"] = bool(out["added"])
            return out
        except Exception as e:
            out["error"] = "批量加尺寸失败: %r" % (e,)
            return out

    def annotate_geometry_fallback(self, part_kind="auto"):
        """【Bug-34 降级方案】当模型里确实没有尺寸约束时，按【几何包围盒】
        生成一份"建议标注清单"，供出图小屋手工补标或写入图纸说明。

        台账建议③："提供 GB/T 检查报告，逐项列出通过/缺失，而非让小屋人工判断。"
        本方法把"该标哪些尺寸"用几何算出来，至少给出完整清单。

        Returns: {"ok", "suggestions": [...], "note"}
        """
        out = {"ok": False, "suggestions": []}
        try:
            _dims = [self._bbox_extent_mm(0), self._bbox_extent_mm(1),
                     self._bbox_extent_mm(2)]
            _dims = [round(d, 2) for d in _dims if d]
            if not _dims:
                out["error"] = "无法读取实体包围盒"
                return out
            out["bbox_mm"] = _dims
            _labels = ["总长", "总宽", "总高"]
            for i, d in enumerate(_dims):
                out["suggestions"].append({
                    "item": _labels[i] if i < len(_labels) else "尺寸%d" % (i + 1),
                    "value_mm": d, "standard": "GB/T 4458.4",
                    "note": "建议在工程图中标注该总体尺寸"})
            # 圆孔建议（从草图记录里取）
            try:
                _radii = self._sketch_circle_radii_mm()
                for r in (_radii or []):
                    out["suggestions"].append({
                        "item": "孔径", "value_mm": round(r * 2, 2),
                        "standard": "GB/T 4458.4",
                        "note": "建议标注 Ø%.1f 及孔位尺寸" % (r * 2)})
            except Exception:
                pass
            out["ok"] = True
            out["note"] = ("这是【几何推算】的建议标注清单（非模型真实尺寸）。"
                           "根治办法：建模时用 add_dimension() 加尺寸约束，"
                           "这样 annotate 才能自动插入（Bug-34）。")
            return out
        except Exception as e:
            out["error"] = "几何标注建议失败: %r" % (e,)
            return out

    def involute_gear_profile(self, module_mm, teeth, pressure_angle_deg=20.0,
                              steps_per_flank=6):
        """【Bug-41 修复】生成【渐开线齿轮齿廓】的点集（供 polyline 一次成型）。

        为什么需要它：
          Bug-41 实测——用 polyline 逐点画 z=30 齿轮的齿形（300+ 点）时，
          SW 无法识别为闭合轮廓 → FeatureExtrusion3 返回 None，
          小屋只能退化成"圆齿槽近似"，齿轮精度下降。
          根因有二：
            ① 点数过多（每齿 10+ 点 × 30 齿 = 300+），SW 轮廓识别吃力；
            ② 齿廓不是真正的渐开线，曲率突变处易被判"轮廓无效"。
          修复：用【解析式渐开线】生成齿廓，并按齿数自适应控制总点数
          （每齿 4~8 点），既保证几何精度又让 SW 能稳定识别。

        Args:
            module_mm: 模数 m（mm）
            teeth: 齿数 z
            pressure_angle_deg: 压力角（GB/T 默认 20°）
            steps_per_flank: 每侧齿廓采样段数（越大越精确、点越多）
        Returns:
            {"ok": True, "points": [(x,y),...], "params": {...}} 或 {"ok": False, "error": ...}
            点集为【单齿】的完整外轮廓（齿根→齿顶→齿根），按齿数旋转复制即可。
        """
        try:
            import math as _math
            m = float(module_mm)
            z = int(teeth)
            if m <= 0 or z < 3:
                return {"ok": False, "error": "模数必须>0且齿数>=3"}
            alpha = _math.radians(float(pressure_angle_deg))
            # ── 基本几何（GB/T 1357 标准直齿圆柱齿轮）──────────────────────
            d = m * z                    # 分度圆直径
            r = d / 2.0                  # 分度圆半径
            r_a = r + m                  # 齿顶圆半径（ha*=1）
            r_f = r - 1.25 * m           # 齿根圆半径（hf*=1.25）
            r_b = r * _math.cos(alpha)   # 基圆半径
            # 渐开线参数：任意半径 rx 处的展开角
            def _inv(a):
                return _math.tan(a) - a

            def _polar(rx):
                """半径 rx 处的渐开线极角（相对齿廓对称中心）。"""
                if rx <= r_b:
                    return 0.0
                ax = _math.acos(min(1.0, r_b / rx))
                return _math.tan(ax) - ax

            # 分度圆处的半齿角
            half_tooth = _math.pi / (2.0 * z)
            inv_alpha = _inv(alpha)
            # ── 【Bug-41 修复 · 齿廓角向符号】────────────────────────────
            # 原实现把渐开线展开角【累加】到分度圆半齿角上：
            #       ang = base_ang + _polar(rx)
            # 这是错的。渐开线由【基圆】向【齿顶】展开时，其极角相对
            # 齿廓对称中心线是【递减】的（分度圆处为 inv(alpha)，齿顶更小），
            # 故正确写法是【减去】展开角：
            #       ang = base_ang - _polar(rx)
            # 实算（z=30, m=2）：错误写法齿顶半齿角 0.1115 rad → 齿顶厚
            #   2×0.1115×32 = 7.13mm，而周节仅 2πr/z = 6.28mm
            #   → 相邻齿廓【交叉自交】，SW 判轮廓无效 →
            #     FeatureExtrusion3 返回 None（正是 Bug-41 的报错）。
            #   正确写法齿顶半齿角 0.0230 rad → 齿顶厚 1.47mm < 周节 6.28mm，
            #   齿形合法，SW 可稳定识别闭合轮廓。
            # ── 【Bug-41 修复 · 齿根低于基圆】──────────────────────────
            # 当 r_f < r_b（小齿数常见，如 z=30,m=1 的 13.75 < 14.095）时，
            #   渐开线只存在于 r_b 以外；原实现对 r_f..r_b 一律 clamp 到 r_b，
            #   于是产出【多个完全重合的点】（退化零长边），同样被 SW 拒绝。
            # 现在：齿根到基圆之间用【径向直线段】过渡（真实齿轮此处为齿根
            #   过渡曲线，直线近似工程上足够），点集不再重合。
            base_ang = half_tooth + inv_alpha
            n = max(2, int(steps_per_flank))
            r_lo = max(r_f, r_b)          # 渐开线起算半径
            pts = []
            # ══ 【退化零长边修复 · 两种情形都要处理】══════════════════════════
            # 实测（m=1）：z=30 时 r_f(13.75) < r_b(14.095)，下标 1≡2、15≡16 重合；
            #              z=50 时 r_f(23.75) > r_b(23.49)，下标 0≡1 重合。
            # 两处根因相同：**相邻两点算出来是同一个坐标** → 零长边。
            #   零长边会让 SolidWorks 的轮廓识别不稳定（与 Bug6 同源），必须消除。
            #
            #   · r_f < r_b：齿根(①) → 基圆径向段(②) → 渐开线(③)。
            #       ③ 的 i=0 点 == ② 的基圆点（同为 r_b、同角度）→ 跳过 ③ 的 i=0。
            #   · r_f ≥ r_b：齿根点(①) 与 ③ 的 i=0 点（r_lo == r_f、同角度）重合
            #       → **不再单独 append ①**，直接由 ③ 的 i=0 充当齿根点。
            _root_ang = base_ang - (_polar(r_lo) if r_lo > r_b else 0.0)
            if r_f < r_b:
                # 情形 A：先给齿根点，再由径向段补基圆点
                pts.append((r_f * _math.cos(-_root_ang),
                            r_f * _math.sin(-_root_ang)))
                pts.append((r_b * _math.cos(-_root_ang),
                            r_b * _math.sin(-_root_ang)))
                _skip_first_involute = True
            else:
                # 情形 B：齿根就在基圆之外，③ 的 i=0 即齿根点，无需另加
                _skip_first_involute = False
            # ③ 左齿廓：基圆 → 齿顶（渐开线，极角递减）
            for i in range(n + 1):
                if _skip_first_involute and i == 0:
                    continue          # 该点已由径向段终点给出，跳过以免零长边
                rx = r_lo + (r_a - r_lo) * (i / float(n))
                ang = base_ang - _polar(rx)
                pts.append((rx * _math.cos(-ang), rx * _math.sin(-ang)))
            # ④ 右齿廓：齿顶 → 基圆（渐开线镜像）
            #   同理：末尾 i==0 的点与 ⑤ 的基圆点重合，需跳过。
            for i in range(n, -1, -1):
                if _skip_first_involute and i == 0:
                    continue
                rx = r_lo + (r_a - r_lo) * (i / float(n))
                ang = base_ang - _polar(rx)
                pts.append((rx * _math.cos(ang), rx * _math.sin(ang)))
            # ⑤ 右齿廓：基圆 → 齿根（径向段镜像）
            if r_f < r_b:
                pts.append((r_b * _math.cos(_root_ang),
                            r_b * _math.sin(_root_ang)))
            # ── 【Bug6 修复·关键】单齿轮廓【不闭合】────────────────────
            # 原实现这里 pts.append(pts[0]) 把【单齿】自我闭合，而 gear()
            #   又按齿数把单齿旋转复制拼接 —— 于是整条折线变成
            #   "一串各自闭合的小环"（实测 z=20 时有 60 个重复点）。
            #   SolidWorks 无法把这种折线识别为一个有效外轮廓，
            #   FeatureExtrusion3 直接返回 None（= no_body，Bug6 现象）。
            # 正确做法：单齿轮廓保持【开放】（根→顶→根），
            #   由 gear() 把所有齿首尾相接成一条闭合外轮廓。
            # 说明：本方法返回的仍是"单齿完整外轮廓"，只是不再自闭合。
            return {"ok": True, "points": pts,
                    "closed": False,
                    "params": {"module_mm": m, "teeth": z,
                               "pressure_angle_deg": float(pressure_angle_deg),
                               "pitch_dia_mm": round(d, 4),
                               "tip_dia_mm": round(r_a * 2, 4),
                               "root_dia_mm": round(r_f * 2, 4),
                               "base_dia_mm": round(r_b * 2, 4),
                               "point_count": len(pts),
                               "points_per_tooth": len(pts)}}
        except Exception as e:
            return {"ok": False, "error": "齿廓生成失败: %r" % (e,)}

    def gear(self, module_mm, teeth, thickness_mm, bore_dia_mm=0.0,
             pressure_angle_deg=20.0, plane="Top Plane", steps_per_flank=4,
             cx=0.0, cy=0.0, bore_via_revolve=False):
        """【Bug-41 修复】参数化直齿圆柱齿轮（模数/齿数/厚度 → 一次成型）。

        台账建议："齿轮齿形应支持参数化绘制（模数/齿数/压力角 → 直接生成齿廓
        曲线），而非让小屋逐点 polyline；或提供 gear(m, z, b, profile='involute')"。

        本方法按 GB/T 1357 标准直齿圆柱齿轮几何生成【完整齿圈轮廓】
        （所有齿一次 polyline 画成闭合轮廓 → 一次 extrude），
        并在需要时用 revolve 加工中心孔（避免 Bug-42 的 cut 方向问题）。

        Args:
            module_mm: 模数 m
            teeth: 齿数 z
            thickness_mm: 齿宽 b（mm）
            bore_dia_mm: 中心孔直径（0 = 不开孔）
            pressure_angle_deg: 压力角（默认 20°）
            plane: 草图基准面（默认 Top Plane；齿轮轴线沿该面法向）
            steps_per_flank: 每侧齿廓采样段数（默认 4，兼顾精度与点数）
            bore_via_revolve: True 时中心孔用 revolve 切除（更稳，避开 cut 方向）
        Returns:
            {"ok", "feature", "params", "profile_points", "error"?}
        """
        out = {"ok": False, "params": None, "profile_points": 0}
        try:
            import math as _math
            prof = self.involute_gear_profile(module_mm, teeth,
                                              pressure_angle_deg, steps_per_flank)
            if not prof.get("ok"):
                out["error"] = prof.get("error")
                return out
            single = prof["points"]
            m = float(module_mm); z = int(teeth)
            r_a = m * z / 2.0 + m
            # ── 把【单齿】按齿数旋转复制成完整齿圈（一次闭合 polyline）────
            # 每个齿的角间距 = 2π/z；单齿点集为【开放】的"左根→顶→右根"。
            #
            # ── 【Bug6 修复】拼接时必须去掉相邻齿的重复连接点 ─────────────
            # 上一齿的末点（右齿根）与下一齿的首点（左齿根）在几何上
            #   往往几乎重合（尤其 r_f < r_b 时两点都落在齿根圆上）；
            #   保留两个相距极近的点会产生"零长边"，SW 判轮廓无效。
            # 这里按最小间距过滤，保证折线是干净的单条闭合外轮廓。
            _MIN_SEG = max(1e-4, float(m) * 0.01)   # 最小相邻点间距（mm）
            full = []
            for k in range(z):
                rot = 2.0 * _math.pi * k / z
                ca, sa = _math.cos(rot), _math.sin(rot)
                for (px, py) in single:
                    q = (px * ca - py * sa + cx, px * sa + py * ca + cy)
                    if full:
                        _dx = q[0] - full[-1][0]
                        _dy = q[1] - full[-1][1]
                        if (_dx * _dx + _dy * _dy) ** 0.5 < _MIN_SEG:
                            continue   # 与上一点几乎重合 → 丢弃（防零长边）
                    full.append(q)
            # 末点与首点过近时也丢弃，避免闭合处出现零长边
            if len(full) > 1:
                _dx = full[0][0] - full[-1][0]
                _dy = full[0][1] - full[-1][1]
                if (_dx * _dx + _dy * _dy) ** 0.5 < _MIN_SEG:
                    full.pop()
            if len(full) < 3:
                out["error"] = ("齿轮轮廓点数不足（%d）—— 检查模数/齿数/压力角"
                                % len(full))
                return out
            # ── 【M1/P5 修复】齿廓解析面积：作为"齿形是否真的生成"的判定基准 ──
            # 没有这条断言时，把齿圈画成 φda 圆柱也能 PASS（P5 事故：无齿"齿轮"
            #   被宣布建模成功）。鞋带公式算闭合多边形面积，乘齿宽 = 解析体积。
            _s2 = 0.0
            _n2 = len(full)
            for _i2 in range(_n2):
                _x1, _y1 = full[_i2]
                _x2, _y2 = full[(_i2 + 1) % _n2]
                _s2 += _x1 * _y2 - _x2 * _y1
            _profile_area_mm2 = abs(_s2) / 2.0
            # 闭合（polyline(close=True) 会补最后一段，这里显式补首点更稳）
            full.append(full[0])
            # ── 一次画出完整齿圈并挤出 ─────────────────────────────────
            self.begin_sketch(plane)
            self.polyline(full, close=True)
            self.end_sketch()
            feat = self.extrude(thickness_mm)
            # ── 【Bug6 修复】必须检查 extrude 结果，不能无条件报 ok=True ────
            # 实机反馈：齿廓 polyline 因自相交/未闭合/点序错乱导致 SW 拒绝
            #   拉伸，extrude 返回 no_body（无实体）。而原实现直接
            #   out.update({"ok": True, ...}) 把失败盖住了 —— 齿轮看起来
            #   "生成成功"，实际没有任何实体，后续开孔/装配全线失败。
            _feat_ok = True
            if isinstance(feat, dict):
                _feat_ok = bool(feat.get("ok", True)) and not feat.get("no_body")
            if not _feat_ok:
                out["ok"] = False
                out["stage"] = "extrude"
                out["extrude_result"] = feat
                out["error"] = (
                    "齿轮齿圈拉伸失败（extrude 返回无实体）—— 齿廓很可能自相交"
                    "或点序错乱。params: m=%s z=%s b=%s, 轮廓点数=%d"
                    % (module_mm, teeth, thickness_mm, len(full)))
                out["hint"] = (
                    "依次尝试：1) 减小 steps_per_flank（如 3）以简化齿廓；"
                    "2) 检查齿根/齿顶半径是否自交（z 过小时齿廓会重叠）；"
                    "3) 用 involute_gear_profile() 单独取出点集核对点序。"
                    "常见根因：齿数过少（z<8）时渐开线齿廓在根部相交。")
                return out
            out.update({"ok": True, "feature": feat,
                        "params": dict(prof["params"],
                                       thickness_mm=float(thickness_mm),
                                       bore_dia_mm=float(bore_dia_mm)),
                        "profile_points": len(full)})
            # ── 中心孔：优先 revolve（避开 cut 方向问题，Bug-42）──────────
            if bore_dia_mm and float(bore_dia_mm) > 0:
                try:
                    if bore_via_revolve:
                        self._bore_by_revolve(bore_dia_mm, thickness_mm, plane=plane)
                        out["bore_method"] = "revolve"
                    else:
                        self.begin_sketch(plane)
                        self.circle(cx, cy, float(bore_dia_mm) / 2.0)
                        self.end_sketch()
                        self.cut(through=True)
                        out["bore_method"] = "cut"
                except Exception as _e_bore:
                    out["bore_warning"] = ("中心孔加工失败(%r)；齿轮本体已生成，"
                                           "可单独用 bore() 补做" % (_e_bore,))
            # ── 【M1/P5 修复】回读断言：拒绝"无齿齿轮" ─────────────────────
            # 回溯报告 §7 P5：小齿轮轴/大齿轮被宣布"建模完成、三道防线 PASS"，
            #   但几何里根本没有齿（按齿顶圆圆柱简化）。根因是完成判据只看
            #   "文件存在 + 凭据 + 看图 PASS"，没有零件定义断言。
            # 现在：齿顶圆 + 体积双重回读，任一不符即 ok=False。
            out["verify"] = self._verify_gear_geometry(
                module_mm=module_mm, teeth=teeth,
                thickness_mm=thickness_mm, bore_dia_mm=bore_dia_mm,
                profile_area_mm2=_profile_area_mm2)
            if not out["verify"].get("ok", True):
                out["ok"] = False
                out["stage"] = "verify"
                out["error"] = out["verify"].get("error") or "齿轮几何回读校验失败"
            return out
        except Exception as e:
            out["error"] = "齿轮生成失败: %r" % (e,)
            return out

    def helix_gear(self, module_mm, teeth, thickness_mm, helix_angle_deg,
                   pressure_angle_deg=20.0, bore_dia_mm=0.0,
                   plane="Top Plane", steps_per_flank=4,
                   tooth_extrude_mm=None):
        """【斜齿】参数化斜齿圆柱齿轮（模数/齿数/齿宽/螺旋角 → 一次成型）。

        为什么必须单独实现（回溯报告 §7 P5 / §16.3）：
          报告指出"小齿轮轴/大齿轮无齿形"，并要求斜齿必须真正成形。
        官方查证结论（检索专员逐页核对 help.solidworks.com 2026）：
          · **FeatureExtrusion3 / FeatureCut3 / FeatureCut4 都没有 twist 参数**
            （23 / 26 / 27 参全部逐位核对过），
            `Dchk1/Dchk2` 是【拔模开关】("allows a draft angle")，
            `Dang1/Dang2` 是【拔模角】—— 与绕轴扭转无关，**不能拿来当螺旋角**；
          · 官方**没有** gear/helix-for-gear 专用接口（IFeatureManager 成员表
            grep "gear" 零命中）；
          · 唯一正规的"沿路径扭转"途径是 **Sweep + ISweepFeatureData**：
              TwistControlType = swTwistControlConstantTwistAlongPath(8)
              SetTwistAngle(角度)
            经 IFeatureManager::CreateDefinition(swFmSweep) + CreateFeature 创建。
          · 螺旋线用 IModelDoc2::InsertHelix（官方："Creates a constant-pitch
            helix or spiral."，Height 单位【米】）。

        实现策略（分两条路，优先级从稳到险）：
          A【sweep 扭转】剖面 = 单齿齿廓 → 沿轴向直线 path 扫掠 + twist 角；
            需要 CreateDefinition/CreateFeature，晚绑定下易失败 → 作为首选尝试。
          B【分段放样近似】把齿宽等分成 N 段，每段端面按螺旋角旋转一个相位，
            逐段 loft/扫掠近似 —— 几何上等价于"扭转拉伸"的离散化。
          两条都失败则【明确返回 ok=False】，绝不退回"圆柱当齿轮"（P5 事故根因）。

        Args:
            module_mm: 模数 m
            teeth: 齿数 z
            thickness_mm: 齿宽 b
            helix_angle_deg: 分度圆螺旋角 β（斜齿轮核心参数）
            pressure_angle_deg: 压力角（20°）
            bore_dia_mm: 中心孔直径（0=不开）
            plane: 草图基准面（齿轮轴线沿该面法向）
            steps_per_flank: 齿廓采样段数
            tooth_extrude_mm: 单齿初始拉伸高度（默认 = 齿宽，仅路径 A 用）
        Returns:
            {"ok", "method", "params", "verify", "error"?}
        """
        import math as _mth
        out = {"ok": False, "params": None, "method": None}
        try:
            m_ = float(module_mm)
            z_ = int(teeth)
            b_ = float(thickness_mm)
            beta = float(helix_angle_deg)
            if m_ <= 0 or z_ < 3:
                out["error"] = "模数必须>0且齿数>=3"
                return out
            if abs(beta) < 1e-9:
                out["error"] = ("helix_angle_deg=0 —— 直齿请直接用 gear()，"
                                "不要用 helix_gear()（避免误以为做出了斜齿）")
                return out
            if abs(beta) >= 45:
                out["error"] = ("螺旋角 %.2f° 超常见范围（人字/双斜齿请分两段建模）；"
                                "单斜齿通常 8°~25°" % beta)
                return out
            d_ = m_ * z_                 # 分度圆直径
            da_ = d_ + 2.0 * m_          # 齿顶圆
            # 导程 L 与螺旋角的关系：tan(β) = π·d / L  →  L = π·d / tan(β)
            _tan = _mth.tan(_mth.radians(beta))
            lead = _mth.pi * d_ / _tan if abs(_tan) > 1e-12 else float("inf")
            # ── 齿宽范围内的【总扭转角】（必须按"轴向推进/导程"算，别算成齿距角）
            # 正确关系：沿轴向走 b，绕轴转过的角度 = 360° × b / L
            #   其中 L = π·d / tan(β)（导程，与 z 无关）。
            # ⚠️ 曾误写成"每齿轴向节距 lead/z"，于是把【一个齿距的相位】当成
            #   整圈扭转，z=24 的小齿轮算出 1666°（4.6 圈）—— 完全不是斜齿。
            # 校验（回溯报告的真实参数 m=2,z=24,b=47,β=14.5337°）：
            #   d=48, L=π·48/tan(14.5337°)=581.7, 扭转=360×47/581.7=29.09°
            #   → 一个齿距角 = 360/24 = 15°，扭转约 1.94 个齿距，物理合理。
            twist_total_deg = (360.0 * b_ / lead) if (lead and lead != float("inf")) else 0.0
            out["params"] = {
                "module_mm": m_, "teeth": z_, "thickness_mm": b_,
                "helix_angle_deg": beta, "pressure_angle_deg": float(pressure_angle_deg),
                "pitch_dia_mm": round(d_, 4), "tip_dia_mm": round(da_, 4),
                "lead_mm": (None if lead == float("inf") else round(lead, 4)),
                "twist_total_deg": round(twist_total_deg, 4),
                "axial_pitch_mm": round(lead / z_ if lead else 0.0, 4),
            }

            # ── 路径 A：单齿剖面 + 轴向扫掠 + twist ──────────────────────
            _errA = None
            try:
                _r = self._helix_gear_via_sweep(
                    module_mm=m_, teeth=z_, thickness_mm=b_,
                    helix_angle_deg=beta, pressure_angle_deg=pressure_angle_deg,
                    plane=plane, steps_per_flank=steps_per_flank,
                    twist_total_deg=twist_total_deg)
                if _r.get("ok"):
                    out.update(_r)
                    out["method"] = "sweep_twist"
                else:
                    _errA = _r.get("error") or "sweep 路径失败"
            except Exception as _eA:
                _errA = repr(_eA)

            # ── 路径 B：分段放样近似（A 失败时的退路）────────────────────
            if not out.get("ok"):
                try:
                    _r2 = self._helix_gear_via_segments(
                        module_mm=m_, teeth=z_, thickness_mm=b_,
                        helix_angle_deg=beta,
                        pressure_angle_deg=pressure_angle_deg,
                        plane=plane, steps_per_flank=steps_per_flank,
                        twist_total_deg=twist_total_deg)
                    if _r2.get("ok"):
                        out.update(_r2)
                        out["method"] = "segmented_loft"
                    else:
                        out["error"] = (
                            "斜齿两条路径都失败。\n"
                            "  A(sweep+twist): %s\n  B(分段放样): %s\n"
                            "【重要】绝不退回圆柱当齿轮 —— 请改用直齿 gear() "
                            "并如实告知用户【斜齿未成形】。"
                            % (_errA, _r2.get("error")))
                except Exception as _eB:
                    out["error"] = ("斜齿两条路径都失败。\n  A: %s\n  B: %r"
                                    % (_errA, _eB))
            # 中心孔
            if out.get("ok") and bore_dia_mm and float(bore_dia_mm) > 0:
                try:
                    self._bore_by_revolve(bore_dia_mm, b_, plane=plane)
                    out["bore_method"] = "revolve"
                except Exception as _eb:
                    out.setdefault("warnings", []).append(
                        "中心孔失败（斜齿本体已成型）: %r" % (_eb,))
            return out
        except Exception as e:
            out["error"] = "斜齿生成失败: %r" % (e,)
            return out

    @staticmethod
    def _sweep_definition_type(fm):
        """探测本机 CreateDefinition 里哪个 type 值是 ISweepFeatureData。

        ── 为什么必须探测，不能写死枚举 ────────────────────────────────
        官方枚举名是 swFeatureNameID_e.swFmSweep，但本机（SW2025 Rev33.5.0）
        的 gen_py 里【没有 swconst 常量模块】（实测 gen_py 只有 2 个文件，
        且不含任何 swFm* 常量），所以无法按名字取值。
        实测穷举 0..39 的结果：type=17 / 18 返回的对象带
          TwistControlType / SetTwistAngle / Path / Profile / SweepType
        —— 即 ISweepFeatureData（17 与 18 在本机等价，取 17）。
        该探测结果会缓存在 swapi 模块级，避免每次建模都穷举 40 次 COM 调用。
        """
        _cached = globals().get("_SWEEP_DEF_TYPE")
        if _cached:
            return _cached
        for _v in (17, 18, 16, 19, 20):
            try:
                _fd = fm.CreateDefinition(_v)
                if _fd is None:
                    continue
                try:
                    _ = _fd.TwistControlType
                    _ = _fd.SetTwistAngle
                    globals()["_SWEEP_DEF_TYPE"] = _v
                    return _v
                except Exception:
                    continue
            except Exception:
                continue
        return None

    def _helix_gear_via_sweep(self, module_mm, teeth, thickness_mm,
                              helix_angle_deg, pressure_angle_deg,
                              plane, steps_per_flank, twist_total_deg):
        """路径 A：官方 sweep + twist（ISweepFeatureData.TwistControlType=8）。

        官方依据（检索专员核对 help.solidworks.com 2026）：
          · 拉伸/切除【没有】twist 参数（FeatureExtrusion3 23 参、FeatureCut4 27 参
            逐一核对过），Dchk/Dang 只是拔模；
          · 唯一"沿路径扭转"途径 = ISweepFeatureData：
              TwistControlType = swTwistControlConstantTwistAlongPath(8)
              原文："Defines the twist of the profile along the sweep path;
                     corresponds to Profile Twist > Specify Twist Value"
              SetTwistAngle(角度)
            经 IFeatureManager::CreateDefinition(swFmSweep) + CreateFeature 创建。

        实现步骤：
          ① 画【单齿齿廓】作扫描剖面（草图 1）
          ② 沿齿宽方向画一条【直线路径】（草图 2，垂直于剖面）
          ③ CreateDefinition(探测到的 sweep 类型) → 设 Path/Profile
             → TwistControlType=8 → SetTwistAngle(twist_total_deg)
          ④ CreateFeature 生成单齿（已扭转）
          ⑤ 圆周阵列 z 份
        """
        out = {"ok": False}
        try:
            import math as _mth
            # ① 单齿剖面
            prof = self.involute_gear_profile(
                module_mm, teeth, pressure_angle_deg,
                steps_per_flank=steps_per_flank)
            if not prof.get("ok"):
                out["error"] = "单齿齿廓生成失败: %s" % prof.get("error")
                return out
            pts = prof.get("points") or []
            if len(pts) < 3:
                out["error"] = "单齿齿廓点数不足"
                return out
            # 剖面草图（在 plane 上）
            self.begin_sketch(plane)
            self.polyline(pts, close=True)
            self.end_sketch()
            _prof_sketch = getattr(self, "_active_sketch_name", None)
            if not _prof_sketch:
                out["error"] = "剖面草图名未记录（begin_sketch 异常）"
                return out
            # ② 路径草图：沿法向的一条直线（长度 = 齿宽）
            _axis = int(getattr(self, "_sketch_normal_axis", 2))
            self.begin_sketch(plane)
            # 在草图平面内无法直接画"法向"直线 → 用 3D 草图概念不可行；
            # 官方做法：路径用【垂直剖面的直线】，在 plane 上画一条垂直于
            #   草图平面的线需要一个 3D sketch。本实现改用"垂直于该平面的
            #   另一张草图的边"太绕 —— 因此这里换更稳的做法：
            #   直接在 plane 的草图里画一条【沿局部 y 的直线】作为路径，
            #   剖面草图放在【垂直于它的另一张基准面】上。
            out["error"] = ("sweep 路径需要 3D 路径草图（垂直于剖面），"
                            "当前封装未实现 3D sketch 支持 —— 走分段路径")
            try:
                self.exit_sketch(merge=False)
            except Exception:
                pass
            return out
        except Exception as e:
            out["error"] = "sweep 路径异常: %r" % (e,)
            try:
                self.exit_sketch(merge=False)
            except Exception:
                pass
            return out

    def _helix_gear_via_segments(self, module_mm, teeth, thickness_mm,
                                 helix_angle_deg, pressure_angle_deg,
                                 plane, steps_per_flank, twist_total_deg):
        """路径 B：分段近似斜齿（每段一张偏移基准面 + 相位旋转 + 独立拉伸）。

        ── 为什么必须用【偏移基准面】────────────────────────────────────────
        首版实现直接对每段调 `begin_sketch(plane)` —— 那是【同一张基面】，
        于是所有段都从 z=0 起拉、互相重叠。实测包围盒 z 只有 0~2mm（齿宽 6mm），
        却返回 ok=True —— 典型的「假成功」，比直接报错更危险。
        现在：第 k 段在【离面 k·dz】的偏移基准面上开草图，段段相接。

        几何：把连续扭转离散成 n_seg 个端面，每段相位差 = 总扭转 / n_seg。
          段数按"每段不超过 3°"选取（上限 24 段）。
        """
        out = {"ok": False}
        try:
            import math as _mth
            n_seg = max(2, int(_mth.ceil(abs(twist_total_deg) / 3.0)))
            n_seg = min(n_seg, 24)
            out["segments"] = n_seg
            prof = self.involute_gear_profile(
                module_mm, teeth, pressure_angle_deg,
                steps_per_flank=steps_per_flank)
            if not prof.get("ok"):
                out["error"] = "单齿齿廓生成失败: %s" % prof.get("error")
                return out
            base_pts = prof.get("points") or []
            if len(base_pts) < 3:
                out["error"] = "单齿齿廓点数不足"
                return out
            dz = thickness_mm / float(n_seg)
            d_phase = twist_total_deg / float(n_seg)
            made = 0
            # 预生成每段的整圈轮廓（避免重复算）
            for i_seg in range(n_seg):
                _phase = _mth.radians(d_phase * i_seg)
                full = []
                for k in range(teeth):
                    _base = 2.0 * _mth.pi * k / teeth + _phase
                    for (x, y) in base_pts:
                        _r = _mth.hypot(x, y)
                        _a = _mth.atan2(y, x) + _base
                        full.append((_r * _mth.cos(_a), _r * _mth.sin(_a)))
                # 第 0 段用原始基准面；其余用偏移基准面（z = i_seg*dz）
                if i_seg == 0:
                    self.begin_sketch(plane)
                else:
                    _op = self._make_offset_plane(plane, i_seg * dz)
                    if not _op.get("ok"):
                        out["error"] = ("第 %d 段偏移基准面创建失败: %s"
                                        % (i_seg + 1, _op.get("error")))
                        return out
                    self.begin_sketch_on_feature(
                        _op.get("feature"), label="斜齿段%d" % (i_seg + 1))
                self.polyline(full, close=True)
                self.end_sketch()
                f = self.extrude(dz)
                if f is None:
                    out["error"] = "第 %d 段拉伸失败（共 %d 段）" % (i_seg + 1, n_seg)
                    return out
                made += 1
            out.update({
                "ok": made == n_seg,
                "segments_made": made,
                "note": ("分段近似：%d 段 × 每段相位 %.3f°，总扭转 %.3f°；"
                         "段数越多越接近真螺旋面，但特征数也越多。"
                         % (n_seg, d_phase, twist_total_deg)),
            })
            # ── 断言：成型后【轴向】尺寸必须 ≈ 齿宽（防"各段重叠"的假成功）──
            # ⚠️ 轴向【不能写死成 Z】：实测在 Top Plane 上建齿轮时，
            #   Top Plane 法向是世界 +Y，所以齿轮轴线落在 Y 轴，
            #   包围盒呈现为 [x:±da/2, y:0~b, z:±da/2]。
            #   首版断言按 z 判 → 把正确结果误判成"重叠"（29.999 vs 6）。
            # 现在：轴向 = 草图法向轴（_sketch_normal_axis，由 begin_sketch
            #   按真实基准面设置），并从包围盒的【该轴】取长度。
            try:
                _box = self.body_box_mm()
                if _box:
                    # 轴向判定：齿宽 b 必然远小于齿顶圆直径 da = m*z+2m，
                    #   所以"三条边长里最接近 b 的那一轴"就是轴向。
                    # 不能依赖 _sketch_normal_axis —— 走 begin_sketch_on_feature
                    #   （偏移基准面）时它不会被更新，会一直停在默认 2，实测误判。
                    _exts = [abs(float(_box[i * 2 + 1]) - float(_box[i * 2]))
                             for i in range(3)]
                    _b = float(thickness_mm)
                    _ax = min(range(3), key=lambda _i: abs(_exts[_i] - _b))
                    _alen = _exts[_ax]
                    out["axial_axis"] = "XYZ"[_ax]
                    out["thickness_actual_mm"] = round(_alen, 3)
                    # 直径方向用于二次校验（应为齿顶圆）
                    _diams = [abs(float(_box[i * 2 + 1]) - float(_box[i * 2]))
                              for i in range(3) if i != _ax]
                    out["diameter_actual_mm"] = round(max(_diams), 3) if _diams else None
                    if abs(_alen - float(thickness_mm)) > max(0.5, 0.1 * thickness_mm):
                        out["ok"] = False
                        out["error"] = (
                            "斜齿成型后【轴向(%s)】尺寸 %.3f mm ≠ 齿宽 %.3f mm —— "
                            "各段没有沿轴向错开（重叠了），不是真斜齿。"
                            % ("XYZ"[_ax], _alen, thickness_mm))
            except Exception as _e_box:
                out["bbox_warning"] = "无法回读包围盒: %r" % (_e_box,)
            return out
        except Exception as e:
            out["error"] = "分段近似异常: %r" % (e,)
            return out

    def _verify_gear_geometry(self, module_mm, teeth, thickness_mm,
                              bore_dia_mm=0.0, profile_area_mm2=None,
                              tol_dia_mm=0.05, tol_vol_pct=2.0):
        """【M1/P5 修复】齿轮零件定义断言：回读体积 + 齿顶圆，拒绝"无齿齿轮"。

        判据（对齐回溯报告 §15.1 M1 / §7 P5）：
          · 齿顶圆实测（包围盒 X/Y 较大边）≈ da = m·z + 2m，偏差 ≤ tol_dia_mm；
          · 实体体积 ≈ 齿廓解析面积 × 齿宽 − 中心孔体积，偏差 ≤ tol_vol_pct%；
        任一不满足 → ok=False。这样"把齿圈画成 φda 圆柱"的假件会被当场拒绝，
        而不是靠人去看图才发现。

        说明：body_box_mm() 返回 [xmin,xmax,ymin,ymax,zmin,zmax]（mm），
          齿轮轴线沿 Z 时，齿顶圆 = max(xmax-xmin, ymax-ymin)。
        """
        out = {"ok": True, "teeth": int(teeth)}
        try:
            import math as _math
            m = float(module_mm)
            z = int(teeth)
            b = float(thickness_mm)
            da = m * z + 2.0 * m
            out["tip_dia_design_mm"] = round(da, 4)
            # ── 齿顶圆回读 ────────────────────────────────────────────────
            try:
                box = self.body_box_mm()
                if not box:
                    out["ok"] = False
                    out["error"] = ("齿轮回读失败：body_box_mm 返回空 —— "
                                    "零件没有实体，齿圈没有真正生成。")
                else:
                    dx = abs(float(box[1]) - float(box[0]))
                    dy = abs(float(box[3]) - float(box[2]))
                    da_actual = max(dx, dy)
                    out["tip_dia_actual_mm"] = round(da_actual, 4)
                    out["tip_dia_dev_mm"] = round(abs(da_actual - da), 4)
                    if abs(da_actual - da) > tol_dia_mm:
                        out["ok"] = False
                        out["error"] = (
                            "齿轮齿顶圆实测 %.3f mm ≠ 设计 %.3f mm"
                            "（偏差 %.3f > %.3f）—— 齿圈外径不对，"
                            "很可能没有按齿廓建模。"
                            % (da_actual, da, abs(da_actual - da), tol_dia_mm))
            except Exception as _e_box:
                out["bbox_error"] = repr(_e_box)
            # ── 体积回读：解析齿廓面积 × 齿宽（减中心孔）vs 实体 ──────────
            if profile_area_mm2 is not None and float(profile_area_mm2) > 0:
                exp_vol = float(profile_area_mm2) * b
                if bore_dia_mm and float(bore_dia_mm) > 0:
                    exp_vol -= _math.pi * (float(bore_dia_mm) / 2.0) ** 2 * b
                out["profile_area_mm2"] = round(float(profile_area_mm2), 3)
                out["expected_volume_mm3"] = round(exp_vol, 2)
                try:
                    _av = self._body_volume_mm3()
                    act_vol = float(_av) if _av is not None else None
                    if act_vol is None:
                        out["volume_note"] = "实体体积回读为空，未做体积断言"
                    else:
                        out["actual_volume_mm3"] = round(act_vol, 2)
                        if exp_vol > 1e-9:
                            dev = abs(act_vol - exp_vol) / exp_vol * 100.0
                            out["volume_dev_pct"] = round(dev, 3)
                            if dev > tol_vol_pct:
                                out["ok"] = False
                                out["error"] = (
                                    "齿轮体积实测 %.1f mm³ ≠ 齿廓解析值 %.1f mm³"
                                    "（偏差 %.2f%% > %.2f%%）—— 齿形没有真正生成，"
                                    "或齿数/模数与设计不符。"
                                    % (act_vol, exp_vol, dev, tol_vol_pct))
                except Exception as _e_vol:
                    out["volume_error"] = repr(_e_vol)
            out["helix_angle_deg"] = 0.0
            out["note"] = ("本断言证明'齿圈几何与解析齿廓一致'；"
                           "斜齿螺旋角需用 sweep/loft 另外实现，当前 gear() 为直齿。")
        except Exception as e:
            out["ok"] = False
            out["error"] = "齿轮几何回读校验异常: %r" % (e,)
        return out

    def _bore_by_revolve(self, dia_mm, depth_mm, plane="Top Plane"):
        """【Bug-42 修复】用 revolve 切中心孔（避开 cut 方向不可控的问题）。"""
        import math as _math
        r = float(dia_mm) / 2.0
        h = float(depth_mm)
        self.begin_sketch("Front Plane")
        # 轮廓：矩形（从轴线到半径 r，高度 h），绕轴线旋转 360° 切除
        self.line(0.0, 0.0, r, 0.0)
        self.line(r, 0.0, r, h)
        self.line(r, h, 0.0, h)
        self.line(0.0, h, 0.0, 0.0)
        # 中心线（旋转轴）
        self.centerline(0.0, 0.0, 0.0, h)
        self.end_sketch()
        return self.revolve(360, cut=True)

    def bore(self, dia_mm, depth_mm=None, axis="Z", through=True, cx=0.0, cy=0.0,
             plane=None):
        """【Bug-42 修复】专用孔加工命令（显式指定轴线方向，避免 cut 方向不可控）。

        台账建议："cut(through=True) 应允许指定切除方向（如 direction=(1,0,0)）；
        或提供 bore(face, d, depth, axis) 专用孔命令。"

        实现：按 axis 选择基准面（基准面法向 = 孔轴线方向），在面上画圆后切除。
          axis="Z" → Top/Bottom Plane（孔沿 Z）
          axis="Y" → Front/Back Plane（孔沿 Y）
          axis="X" → Right/Left Plane（孔沿 X）
        这样孔的轴向是【显式可控】的，不再依赖 SW 的默认贯穿方向。

        Args:
            dia_mm: 孔径
            depth_mm: 盲孔深度（through=True 时忽略）
            axis: 孔轴线方向 "X"/"Y"/"Z"
            through: True=完全贯穿
            cx, cy: 圆心（草图平面内坐标，默认原点）
            plane: 显式指定基准面（覆盖 axis 推导）
        Returns:
            {"ok", "feature", "axis", "plane", "dia_mm", "through", "error"?}
        """
        out = {"ok": False, "axis": str(axis).upper(), "through": bool(through),
               "dia_mm": float(dia_mm)}
        try:
            _axis = str(axis).upper()
            _plane = plane or {"Z": "Top Plane", "Y": "Front Plane",
                               "X": "Right Plane"}.get(_axis)
            if not _plane:
                out["error"] = "axis 必须是 X/Y/Z（或显式给 plane）"
                return out
            out["plane"] = _plane
            self.begin_sketch(_plane)
            self.circle(float(cx), float(cy), float(dia_mm) / 2.0)
            self.end_sketch()
            if through:
                feat = self.cut(through=True)
            else:
                if not depth_mm:
                    out["error"] = "盲孔必须给 depth_mm"
                    return out
                feat = self.cut(depth=float(depth_mm), through=False)
            out.update({"ok": True, "feature": feat})
            return out
        except Exception as e:
            out["error"] = "孔加工失败: %r" % (e,)
            return out

    def revolve_on_face(self, face_point, axis_point, axis_dir, angle_deg=360.0,
                        cut=False):
        """【Bug-40 修复】在【实体面】上做旋转特征（显式给轴，不依赖按名选面）。

        台账建议："revolve 应显式传'旋转轴 + 基准面'，不能依赖按名选基准面；
        或提供 revolve_on_face(face, axis_entity) 专用 API。"

        背景（Bug-40）：多特征零件上 begin_sketch("Front Plane") 会降级到
        begin_sketch_on_face，导致旋转特征的轴/轮廓错位 → FeatureRevolution
        返回 None（且不报错，很隐蔽）。

        本方法：
          ① 用 begin_sketch_on_face(face_point) 在【指定实体面】上开草图；
          ② 用中心线显式定义旋转轴（axis_point + axis_dir），不依赖基准面；
          ③ 调用 revolve(angle, cut=cut)。

        Args:
            face_point: 实体面上的一点 (x,y,z) mm
            axis_point: 旋转轴上一点 (x,y,z) mm（草图平面内坐标由 SW 投影）
            axis_dir: 轴向 (dx,dy,dz)
            angle_deg: 旋转角度
            cut: True=旋转切除
        Returns:
            {"ok", "feature", "axis_dir", "angle_deg", "error"?}
        """
        out = {"ok": False, "axis_dir": list(axis_dir),
               "angle_deg": float(angle_deg)}
        try:
            self.begin_sketch_on_face(*face_point)
            # 显式中心线（旋转轴）：在草图平面内画一条足够长的中心线
            import math as _math
            ax, ay, az = [float(v) for v in axis_dir]
            px, py, pz = [float(v) for v in axis_point]
            _L = 1000.0   # 足够长，保证覆盖轮廓
            self.centerline(px, py, px + ax * _L, py + ay * _L)
            self.end_sketch()
            out["ok"] = True
            out["note"] = ("已在该实体面上开草图并画好中心线；"
                           "请继续在该草图内绘制旋转轮廓，然后调用 revolve()。"
                           "（本方法只负责'面 + 轴'，轮廓由调用方决定）")
            return out
        except Exception as e:
            out["error"] = "revolve_on_face 失败: %r" % (e,)
            return out


    def _check_through_hole(self, axis="Z", samples=9):
        """【F 修复·第三轮】判断切除是否真的打掉了实质材料。

        背景：原判据用 1% 去料率，太松 —— 实测"去料 54% 但孔未贯穿"
          都能通过。半成品之所以危险，是因为它【返回成功】。

        本方法用【去料率下限 + 实体数】组合做判据：
          · 贯穿打孔应移除 >= 3% 的材料（低于此几乎肯定没打通）；
          · 同时记录实体数量，供调用方判断是否意外切断成多体。

        Returns: {ok, removed_pct?, bodies?, reason?}
        """
        out = {"ok": False}
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            out["bodies"] = len(bl)
        except Exception:
            out["bodies"] = None
        _pct = getattr(self, "_cut_removed_pct", None)
        if _pct is not None:
            out["removed_pct"] = round(_pct, 2)
        if _pct is not None and _pct >= 3.0:
            out["ok"] = True
            return out
        out["reason"] = ("去料率 %.2f%% 低于 3%% 的贯穿底线，"
                         "极可能没有打通" % (_pct if _pct is not None else -1))
        return out

    def _ensure_cut_profile_selected(self):
        """【F-1 修复·第十轮】cut 前确保【草图边/闭合轮廓】被选中。

        ══ 专家第二轮意见（决定性）══════════════════════════════════════════
        1) cut() 必须先 EditSketch() 进入草图编辑状态，再用 SketchManager
           层级接口选中草图内的边线/闭合轮廓，完成后再 ExitSketch()。
           直接对"草图特征"操作（type=9）拿不到轮廓。
        2) "遍历特征树异常：找不到成员" 说明 SW2025 下 FeatureManager 的
           某些方法签名变了 —— 应改走 SketchManager 层级，更可靠。
        3) 因此把"遍历特征树"从主路径【降级为最后兜底】。

        新的尝试顺序：
          路径 0（首选）：若草图已激活 -> 直接选段；
          路径 1（核心）：若草图已退出 -> 通过最近草图 EditSketch 进入后再选段；
          路径 2（兜底）：SelectByID2("段名@草图名","EXTSKETCHSEGMENT")；
          路径 3（最后）：遍历特征树（可能抛"找不到成员"，仅作垫底）。

        Returns: (ok: bool, info: str)
        """
        _diag = []

        def _count():
            try:
                return int(self.model.SelectionManager.GetSelectedObjectCount2(-1))
            except Exception:
                return -1

        def _seg_count():
            """只统计"段"类对象（10=SKETCHSEGMENT / 24=EXTSKETCHSEGMENT）"""
            try:
                sm = self.model.SelectionManager
                n = int(sm.GetSelectedObjectCount2(-1))
                seg = 0
                for i in range(1, n + 1):
                    try:
                        if int(sm.GetSelectedObjectType3(i, -1)) in (10, 24):
                            seg += 1
                    except Exception:
                        continue
                return seg
            except Exception:
                return -1

        def _ok_now(tag):
            c = _count()
            s = _seg_count()
            _diag.append("%s: count=%s seg=%s" % (tag, c, s))
            return (c > 0 and s != 0), ("%s(段=%s)" % (tag, s if s > 0 else c))

        # ── 路径 0（永远最先）：草图已激活 → 直接选段 ────────────────────
        # 【Bug T2 实测修正】草图处于激活态时绝不能 EditSketch（会把草图
        # 切换/退出），必须直接选段 —— 这是实测唯一稳定的主路径。
        try:
            if self.skm.ActiveSketch is not None:
                ok0, _c0 = self.select_all_sketch_segments()
                _diag.append(u"路径0(已激活): ok=%s %s"
                             % (ok0, getattr(self, "_sel_diag", "")))
                good, msg = _ok_now(u"路径0")
                if good:
                    return True, msg
        except Exception as e:
            _diag.append(u"路径0 异常: %r" % (e,))

        # ── 路径 -1：按【显式记录的草图名】EditSketch 后选段 ═══════════
        # 【Bug T1/T2/T3 修复】草图已退出（T2）或激活态选段失败时：
        #   · T1：同零件第 2 次 cut 时，"特征树最后一个 ProfileFeature"
        #     是上一次 cut 的草图 —— 启发式必然拿错，显式名不会；
        #   · T2：end_sketch 后 ActiveSketch=None，只有 EditSketch 能回去；
        #   · T3：EditSketch 进草图后取的是真实轮廓段（type 10/24），
        #     不是"草图特征"（type 9），复杂轮廓也能整段选中。
        # 只用显式记录的名字，绝不做"草图1/Sketch1"式猜测 —— 猜错会把
        # 基体轮廓选中去切（比失败更危险）。
        try:
            _cons = getattr(self, "_consumed_sketches", None) or set()
            _cand = self._active_sketch_name
            if _cand and str(_cand) not in _cons:
                _okE, _nE = self._try_edit_sketch_by_name(_cand)
                _diag.append(u"路径-1(EditSketch@%s): ok=%s n=%s"
                             % (_cand, _okE, _nE))
                if _okE:
                    good, msg = _ok_now(u"路径-1")
                    if good:
                        return True, msg
                try:
                    self.model.ClearSelection2(True)
                except Exception:
                    pass
        except Exception as e:
            _diag.append(u"路径-1 异常: %r" % (e,))

        # ── 路径 1（核心）：EditSketch 进入草图后再选段 ──────────────────
        # 专家说这条路最可靠：走 SketchManager 层级，不碰特征树 API。
        _sk_name_for_b = None
        try:
            # 用 SelectByID2 按名选草图（名不确定时用 FirstFeature 轻量探测，
            #   失败也不影响后续 —— 只用于拿到草图名）
            _probe = self._find_last_sketch_name()
            if _probe:
                _sk_name_for_b = _probe
                _diag.append("探测到草图名=%s" % _probe)
        except Exception as e:
            _diag.append("探测草图名异常: %r" % (e,))

        if _sk_name_for_b:
            # 【Bug T1 修复】"最后一个草图"若已被上一次 cut 消费，此路必拿错
            # 轮廓 —— 直接禁用该启发式，不再盲选。
            _cons = getattr(self, "_consumed_sketches", None) or set()
            if str(_sk_name_for_b) in _cons or \
                    (self._last_cut_feature_name and
                     str(_sk_name_for_b) == str(self._last_cut_feature_name)):
                _diag.append(u"路径1 跳过: 最后草图=%s 已被特征消费(T1防护)"
                             % _sk_name_for_b)
            else:
                try:
                    self.model.ClearSelection2(True)
                    if self.ext.SelectByID2(_sk_name_for_b, "SKETCH", 0, 0, 0,
                                            False, 0, self._empty, 0):
                        # 进入草图编辑态
                        try:
                            self.model.EditSketch()
                        except Exception as _e_edit:
                            _diag.append("EditSketch 异常: %r" % (_e_edit,))
                        ok1, _c1 = self.select_all_sketch_segments()
                        _diag.append("路径1(EditSketch+选段): ok=%s %s"
                                     % (ok1, getattr(self, "_sel_diag", "")))
                        good, msg = _ok_now("路径1")
                        if good:
                            return True, msg
                except Exception as e:
                    _diag.append("路径1 异常: %r" % (e,))

        # ── 路径 2：SelectByID2 用 EXTSKETCHSEGMENT + "段名@草图名" ───────
        if _sk_name_for_b:
            try:
                self.model.ClearSelection2(True)
                _names = self._sketch_segment_names(_sk_name_for_b)
                _diag.append("段名=%s" % (_names[:6],))
                _hit = 0
                for _i, _nm in enumerate(_names):
                    for _full in ("%s@%s" % (_nm, _sk_name_for_b), _nm):
                        for _ty in ("EXTSKETCHSEGMENT", "SKETCHSEGMENT"):
                            try:
                                if self.ext.SelectByID2(_full, _ty, 0, 0, 0,
                                                        _hit > 0, 0, self._empty, 0):
                                    _hit += 1
                                    break
                            except Exception:
                                continue
                        if _hit and _i == 0:
                            break
                _diag.append("路径2(EXTSKETCHSEGMENT): hit=%d" % _hit)
                good, msg = _ok_now("路径2")
                if good:
                    return True, msg
            except Exception as e:
                _diag.append("路径2 异常: %r" % (e,))

        # ── 路径 3（最后兜底）：遍历特征树（可能抛"找不到成员"）─────────
        # 【Bug T1 修复】此路选中的是"草图特征"(type 9) 而非轮廓段，
        # 正是历史事故的源头 —— 已被特征消费的草图一律跳过。
        try:
            if _sk_name_for_b:
                _cons = getattr(self, "_consumed_sketches", None) or set()
                if str(_sk_name_for_b) in _cons or \
                        (self._last_cut_feature_name and
                         str(_sk_name_for_b) == str(self._last_cut_feature_name)):
                    _diag.append(u"路径3 跳过: 草图已消费(T1防护)")
                else:
                    self.model.ClearSelection2(True)
                    if self.ext.SelectByID2(_sk_name_for_b, "SKETCH", 0, 0, 0,
                                            False, 0, self._empty, 0):
                        good, msg = _ok_now(u"路径3(选草图特征)")
                        if good:
                            return True, msg
        except Exception as e:
            _diag.append(u"路径3 异常（已知 SW2025 可能找不到成员）: %r" % (e,))

        _diag.append("最终 count=%s seg=%s" % (_count(), _seg_count()))
        return False, " | ".join(_diag)

    def _find_last_sketch_name(self):
        """【F-1 修复】轻量探测最后一个草图名（失败返回 None，不影响主流程）。

        与旧实现不同：这里把"遍历特征树"单独隔离成一个可选步骤，
          即使 SW2025 抛"找不到成员"，也只是拿不到名字，
          不会让整个轮廓选中流程崩掉。
        """
        try:
            f = self.model.FirstFeature()
            last = None
            _guard = 0
            while f is not None and _guard < 5000:
                _guard += 1
                try:
                    if f.GetTypeName2() == "ProfileFeature":
                        last = f.Name
                except Exception:
                    pass
                try:
                    f = f.GetNextFeature()
                except Exception:
                    break
            return last
        except Exception:
            return None

    def _sketch_segment_names(self, sketch_name):
        """【F-1 修复】取指定草图内各段的名字列表（失败返回 []）。"""
        out = []
        try:
            f = self.model.FirstFeature()
            _guard = 0
            while f is not None and _guard < 5000:
                _guard += 1
                try:
                    if (f.GetTypeName2() == "ProfileFeature"
                            and str(f.Name) == str(sketch_name)):
                        sk_obj = None
                        for _acc in ("GetSpecificFeature2", "GetSpecificFeature"):
                            try:
                                sk_obj = getattr(f, _acc)()
                                if sk_obj is not None:
                                    break
                            except Exception:
                                continue
                        if sk_obj is not None:
                            for s in (sk_obj.GetSketchSegments or []):
                                try:
                                    _nm = s.GetName
                                    if _nm:
                                        out.append(str(_nm))
                                except Exception:
                                    continue
                        break
                except Exception:
                    pass
                try:
                    f = f.GetNextFeature()
                except Exception:
                    break
        except Exception:
            pass
        return out

    def _sketch_circle_radii_mm(self):
        """【Bug T1 修复·验收判据】读取轮廓草图中的圆半径列表（mm）。

        用途：估算切除的期望体积 E = π·Σr²·H（H=贯穿长度或盲孔深度），
        替代"去料率≥3%"这种对小孔天然误判的启发式阈值。
        实测依据：Φ10 小孔贯穿 Φ60x40 圆柱，真实去料 2.90% ——
        被 3% 阈值误拒；用几何期望判据则正确接受（96% 命中）。
        """
        sk = None
        try:
            sk = self.skm.ActiveSketch
        except Exception:
            sk = None
        if sk is None:
            # 草图已退出：按显式记录名从特征树直接取草图对象（无需 EditSketch）
            try:
                nm = self._active_sketch_name
                if nm:
                    f = self.model.FirstFeature()
                    _guard = 0
                    while f is not None and _guard < 5000:
                        _guard += 1
                        try:
                            if (f.GetTypeName2() == "ProfileFeature"
                                    and str(f.Name) == str(nm)):
                                sk = f.GetSpecificFeature2()
                                break
                        except Exception:
                            pass
                        try:
                            f = f.GetNextFeature()
                        except Exception:
                            break
            except Exception:
                sk = None
        if sk is None:
            return []
        radii = []
        try:
            segs = sk.GetSketchSegments
            if segs is not None:
                lst = list(segs) if isinstance(segs, (list, tuple)) else [segs]
                for s in lst:
                    try:
                        if int(s.GetType()) == 0:  # swSketch_CIRCLE
                            radii.append(float(s.GetRadius()) * 1000.0)
                    except Exception:
                        continue
        except Exception:
            pass
        return radii

    def _bbox_extent_mm(self, axis=2):
        """实体包围盒在指定轴方向(0=X,1=Y,2=Z)的长度（mm）；取不到返回 None。"""
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            best = 0.0
            for b in bl:
                try:
                    bb = b.GetBodyBox()
                    ext = abs(bb[3 + int(axis)] - bb[int(axis)]) * 1000.0
                    best = max(best, ext)
                except Exception:
                    continue
            return best if best > 0 else None
        except Exception:
            return None

    # ══ 【Bug17/18 回归修复·新增】真实实体包围盒的 6 元组 ════════════════
    # 背景（测试部 v13 反馈）：
    #   «_extract_real_geometry 的变量遮蔽虽改了，但 bbox x/y 仍被 design_domain
    #     锁定，FEA 仍用 design_domain» —— 因为他们拿不到真实的 6 元组，
    #   只能拿 design_domain 的 x/y 去补，于是"读出的 bbox"其实就是设计域尺寸，
    #   导致 SF 对几何完全不敏感（10 种零件全 = 72.81）。
    #
    # 真实能力：SolidWorks 的 `IBody2.GetBodyBox()` 返回
    #   [xmin, ymin, zmin, xmax, ymax, zmax]，单位【米】。
    #   本文件已有 _bbox_extent_mm / _bbox_max_dim_mm 在用同一个 API，
    #   但只返回"边长"，没有返回 min/max 六元组 —— 这正是缺口。
    #
    # 本方法把它补上：返回 [xmin,xmax, ymin,ymax, zmin,zmax]（mm），
    #   与 geometry_gate 期望的顺序一致（注意是 min,max 交替，非 min…max…）。
    def body_box_mm(self):
        """当前零件的真实包围盒（mm），返回 [xmin,xmax,ymin,ymax,zmin,zmax]。

        多实体时取所有实体包围盒的【并集】（最外层包络），这是"零件占用空间"
        的正确语义。取不到返回 None —— 调用方【不得】用设计域代替。
        """
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            mn = [float("inf")] * 3
            mx = [float("-inf")] * 3
            got = False
            for b in bl:
                try:
                    bb = b.GetBodyBox()
                    if bb is None or len(bb) < 6:
                        continue
                    for i in range(3):
                        v0 = float(bb[i]) * 1000.0      # m -> mm
                        v1 = float(bb[3 + i]) * 1000.0
                        if v0 > v1:
                            v0, v1 = v1, v0
                        if v0 < mn[i]:
                            mn[i] = v0
                        if v1 > mx[i]:
                            mx[i] = v1
                    got = True
                except Exception:
                    continue
            if not got:
                return None
            out = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
            # 退化保护：任一边长为 0 视为取不到（例如只有曲面/无实体）
            if (mx[0] - mn[0]) <= 0 and (mx[1] - mn[1]) <= 0 and (mx[2] - mn[2]) <= 0:
                return None
            return [round(v, 4) for v in out]
        except Exception:
            return None

    def _bbox_max_dim_mm(self):
        """实体包围盒最大边长（mm）；取不到返回 None。"""
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            best = 0.0
            for b in bl:
                try:
                    bb = b.GetBodyBox()
                    dims = [abs(bb[3] - bb[0]), abs(bb[4] - bb[1]), abs(bb[5] - bb[2])]
                    best = max(best, max(dims) * 1000.0)
                except Exception:
                    continue
            return best if best > 0 else None
        except Exception:
            return None

    def _body_volume_mm3(self):
        """返回当前实体的体积（mm³）；取不到返回 None。

        【F 修复】用于切除结果校验：切除后体积【必须变小】，
          否则说明"切除"实际没去料（静默失败）。
        """
        try:
            # 【R11 修复】IModelDoc2.GetMassProperties 是【无参方法】（L38295）。
            #   旧代码传 (0) 是 IModelDocExtension 版签名（L41895），
            #   晚绑定下抛 com_error「参数个数不正确」→ 被 except 吞掉 →
            #   每次体积校验都静默落到 body 累加退路，切除判据变慢且失真。
            mp = _com_get(self.model, "GetMassProperties") or []
            # 质量属性数组索引 3 为体积（m³），见项目既有约定
            if len(mp) >= 4:
                return float(mp[3]) * 1e9   # m³ -> mm³
        except ComMemberError:
            pass
        except Exception:
            pass
        # 退路：逐实体累加
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            total = 0.0
            got = False
            for b in bl:
                try:
                    props = b.GetMassProperties(0) or []
                    if len(props) >= 4:
                        total += float(props[3])
                        got = True
                except Exception:
                    continue
            if got:
                return total * 1e9
        except Exception:
            pass
        return None

    def cut(self, depth=10, through=False, flip=False, auto_select=True,
            through_both=True, via=None, flip_side_to_cut=False):
        """切除。through=True 完全贯穿；否则切除 depth mm。

        ── 【R5 修复 · 2026-10-31】flip 语义明确化（此前会切补集）────────
        flip 的语义是【反转切除方向】—— 落到 FeatureCut 的第 3 位 Dir。
        新增 flip_side_to_cut 才是 FeatureCut 第 2 位 Flip 的官方语义
        「flip side to cut = 切掉轮廓【外侧】的材料」，默认 False。

        为什么必须分开（实测证据）：旧实现把 flip=True 直接接到第 2 位
        Flip 上，而自动反向重试也接在那里 → 两者都会切到【补集】，
        实测一次切掉 -30520.6 mm³，且因为体积确实大幅减小，
        体积校验还判「成功」—— 最危险的静默毁件。现在：
          · flip=True             → Dir=True，方向反转（正确语义）
          · flip_side_to_cut=True → Flip=True，切轮廓外侧（显式、少用）
          · 自动反向重试          → 只动 Dir，绝不碰 Flip
        并且 flip_side_to_cut=True 时结果还要额外过一道【补集校验】：
        去料率 > 60% 直接判失败（正常挖孔不会切掉大半个零件）。
        

        ══ 【Bug T1/T2 修复】cut 的轮廓选择已代码级加固 ═════════════════
        · T1（同一零件第 2 次及以后 cut 必失败）：根因是"特征树最后一个
          草图"启发式在多次 cut 后拿到的是【已被消费】的旧草图，选中段
          count=0。现改为：begin_sketch 记录草图名 → cut 按名 EditSketch
          选真实轮廓段 → 成功后把该草图标记为"已消费"，绝不再用。
        · T2（end_sketch 后 cut 失败）：退出草图后 SW 清空选择，
          现在按记录名 EditSketch 回到草图取轮廓段，end_sketch 之后
          cut 与 end_sketch 之前 cut 行为一致（都可用）。
        · 同一零件可安全调用任意多次 cut。

        【R5 更正】旧注释称「改用 FeatureExtrusion3 的切除模式」—— 该说法错误：
        类型库 IFeatureManager.FeatureExtrusion3（L22597，23 参）里没有任何
        「切除/AddPad」形参（第 18 位是 Merge），它只能增料。故该兜底已删除。

        ── 【F 修复·第二轮】贯穿不生效（实测去料 54%、孔未贯穿）══════════
        第一轮把 FeatureCut3 提为主路径后，切除确实生效了（去料 54%），
        但 through=True 的完全贯穿没被应用 —— 孔没打通。

        根因：FeatureCut3 在各 SolidWorks 版本间【参数布局不一致】：
          · 双方向布局 (Sd, Flip, Dir, T1, T2, D1, D2, ...)
          · 单方向布局 (Sd, Flip, Dir, T1, D1, D2, Dchk1, ...)
        参数错位时 T1(贯穿) 会落到 D1(盲孔深度) 的位置 → 变成盲孔 →
        不贯穿；而返回值仍非 None，所以【不报错】。

        修复：多签名依次尝试 + 每次尝试后做【体积校验】：
          · 切除后体积必须变小，否则判该签名失败；
          · through=True 时去料量过小也判失败（可能没贯穿）；
          · 全部签名不达标则明确报错，绝不留半成品当成功。
        修复：显式区分两种语义，由 through_both 控制，默认 True 保持
          既有的双向行为，但【两个方向都显式赋值】，消除隐式默认。
          · through_both=True  (默认) → T1=T2=完全贯穿（真双向穿透）
          · through_both=False        → 仅正向贯穿，反向为盲孔 0
          · through=False             → 双向盲孔，深度 depth

        Args:
            via: 可选，直接传入一个已打开的草图/面名称做切除参考
        Returns: feat 或 None；失败时通过 _warn 留痕，不静默吞掉
        """
        d = depth * MM
        # ══ 【R5 修复】净距预检：把"注定失败"的情形在调 API 前拦下来 ══════
        # 实测（本机 SW2025 SP5.0，同一草图两环）：
        #   净距 0.328 mm → FeatureExtrusion3 返回 none/no_body，FeatureCut 亦失败
        #   净距 0.522 / 0.716 / 0.911 mm → 全部 PASS
        # 分界线正是 end_sketch() 的 MergePoints 旧容差（0.5mm）。也就是说
        # 那不是"几何不合法"，而是【我们自己的合并容差把两个环粘掉了】。
        # 这里显式报错并给出可操作的数值建议，而不是让 SW 返回 None 让上层瞎猜。
        try:
            _gap_mm = _ring_clearance_mm(
                getattr(self, "_sketch_circles_local", None))
        except Exception:
            _gap_mm = None
        _gap_note = (
            "  【实测依据】旧合并容差 0.5mm 时：净距 0.328mm → FeatureExtrusion3 "
            "返回 none/no_body（必失败）；净距 0.522/0.716/0.911mm → 全 PASS。"
            "分界线正是 0.5mm 这个容差本身，说明那不是几何不合法，而是"
            "MergePoints 把两个环粘连掉了。\n"
            "  处置：①把两环间距加大到 > %.3f mm（推荐 >= %.3f mm）；"
            "或②分两次建模（先切一个环，再切另一个）；"
            "或③改用 extrude_with_holes() 把孔画成基体内环一次挤出。"
            % (SKETCH_MERGE_TOL_MM, SKETCH_MERGE_TOL_MM * 5))
        if _gap_mm is not None and _gap_mm < SKETCH_MERGE_TOL_MM:
            raise RuntimeError(
                "切除前【轮廓自检失败】：同一草图内两个圆形环的净距仅 "
                "%.3f mm，小于草图合并容差 %.3f mm —— SolidWorks 会把它们"
                "粘连成一个连通区域，特征必然返回 None(no_body)。\n%s"
                % (_gap_mm, SKETCH_MERGE_TOL_MM, _gap_note))
        if (_gap_mm is not None
                and _gap_mm < SKETCH_MERGE_TOL_MM * 5):
            # 落在历史失败区间（0.1~0.5mm）：容差已下调，理论上能过，
            #   但必须留痕 —— 一旦真失败，这条告警就是直接线索。
            self._warn(
                "切除前【净距告警】：同一草图内两个圆形环净距仅 %.3f mm，"
                "低于历史失败分界线 %.3f mm。当前合并容差已下调到 %.3f mm，"
                "理论上可正常切除；若本次切除返回 None/no_body，"
                "请优先加大环间距或分两次建模。\n%s"
                % (_gap_mm, SKETCH_MERGE_TOL_MM * 5, SKETCH_MERGE_TOL_MM,
                   _gap_note))
        # ── 【F 修复·第四轮】贯穿不生效 -> 大深度兜底 ──────────────────
        # 测试实测：T1=SW_END_THROUGH(1) 只去料 1.96%，被当成极小盲孔。
        #   说明"贯穿枚举"在本版 FeatureCut3 的该参数位上不被识别。
        #
        # 修复策略（测试部建议）：
        #   · 贯穿优先用【双向贯穿枚举 SW_END_THROUGH_BOTH=9】（2026-11-11 订正：
        #     旧注释写的 2 是 swEndCondThroughNext，属误值）；
        #   · 同时把深度设为【极大值】(9999mm)，即使枚举被忽略，
        #     大深度盲孔也能实际切穿整个零件 —— 这是最稳的兜底；
        #   · 两者叠加，无论 SW 认哪个，结果都是贯穿。
        # ── 【F 修复·第五轮】贯穿改用"大深度盲孔"策略 ────────────────────
        # 第四轮实测仍只去料 1.96%，说明：即使把 T1 设为贯穿枚举(2)、
        #   把 d1 设为 9999mm，FeatureCut3 在该参数位上依然把它们当成了
        #   "极小盲孔深度" —— 即【参数整体错位】，我们传的值落到了错误位置。
        #
        # 结论：不能再依赖"贯穿枚举 + 深度"的组合去猜参数位。
        #
        # 新策略（最稳妥，绕开所有贯穿语义）：
        #   through=True 时，直接用【SW_END_BLIND + 9999mm 大深度】。
        #   即"用一个远超零件尺寸的盲孔，把零件彻底切穿"。
        #   这与"贯穿"效果等价，但不依赖任何贯穿枚举 —— 跨版本一致。
        #   工程上这也是常见做法（建模时用极大深度代替"完全贯穿"）。
        # ══ 【F 修复·第七轮】专家意见：FeatureCut3 已 Obsolete ══════════════
        # 专家结论（SW2025）：
        #   · FeatureCut3 已被 SolidWorks 标记为 Obsolete，推荐 FeatureCut4；
        #   · 参数布局核对无误（Sd,Flip,Dir,T1,T2,D1,D2 位置正确）；
        #   · 贯穿失败说明 D1 未被解释为深度，或终止条件被错位解析。
        #   · 建议：优先 FeatureCut4；贯穿用 swEndCondThroughAll(1) 而非 Blind。
        #
        # 本轮调整：
        #   1) FeatureCut4 提为【首选】；
        #   2) 贯穿优先用【ThroughAll 枚举】，让 SW 自己处理贯穿逻辑；
        #   3) 同时保留大深度作为平行尝试（枚举不生效时兜底）。
        _big = THROUGH_FALLBACK_DEPTH_MM * MM   # 9999mm -> m
        if through:
            T1 = SW_END_THROUGH            # 1 = swEndCondThroughAll
            T2 = SW_END_THROUGH_BOTH if through_both else 0
            d1 = 0.0                       # ThroughAll 时深度忽略
            d2 = 0.0
        else:
            T1 = SW_END_BLIND
            T2 = SW_END_BLIND
            d1 = d
            d2 = d

        last_err = None
        # ══ 【F 修复·第二轮】贯穿不生效 —— 多签名 + 体积校验 ══════════════
        # 第一轮实测：FeatureCut3 提为主路径后，去料了 54% 但【孔不贯穿】。
        #   说明切除本身生效，但"完全贯穿(Through All)"没被正确应用。
        #
        # 根因：FeatureCut3 在各版本间参数布局不一致 —— 有的是
        #   (Sd, Flip, Dir, T1, T2, D1, D2, ...) 双方向，
        #   有的是 (Sd, Flip, Dir, T1, D1, D2, Dchk1, ...) 单方向。
        #   参数错位时 T1 落到 D1 的位置 → 被当成"盲孔深度"→ 不贯穿，
        #   而返回值仍非 None，所以【不报错】。
        #
        # 修复：
        #   1) through=True 时【优先】尝试把 T1/T2 放在最可能的贯穿位置；
        #   2) 每个签名尝试后都用【体积校验】确认"真的切穿了"；
        #   3) 全部不达标则明确报错，绝不留一个"半成品切除"当成功。

        # ══ 【F-1 修复·第八轮】cut 前必须【重新选中草图轮廓】══════════════
        # 测试部证据链（决定性）：
        #   · before_count=1 但 type=9（草图实体本身，不是轮廓边/面）
        #   · cut 调用瞬间 GetSelectedObjectCount2(-1) == 0
        #   → 封装拿【空选择】去调 FeatureCut，SW 只能退化处理
        #     （表现为 removed_pct 恒 1.96%）。
        #
        # 根因：草图退出(end_sketch)后 SW 会自动清空选择，而原封装
        #   从未在 cut 前重新选中轮廓 —— 属于"选中丢失"，不是参数问题。
        #
        # 修复：
        #   1) cut 前重新选中草图中的轮廓实体（边/面），而不是草图特征本身；
        #   2) 加【选择前置断言】：选中数必须 > 0，否则立即报明确错误，
        #      不再让 SW 返回模糊的"半成品成功"。
        # ══ 【R5 修复】do_cut 状态依赖型选择错乱 —— 先清选择 + 强制重建 ══
        # 同一 do_cut 在不同选择状态下结果不同（该选的没选中 / 选错面），
        # 根因是上一步特征成功后 SW 仍残留脏选择。这里无条件自愈一次：
        #   ① ClearSelection2(True) 清空；② ForceRebuild3(False) 刷新缓存。
        # 顺序很重要：【先清选择，再选轮廓】—— 反了就等于白清。
        try:
            self._reset_selection_state(reason="cut 前置")
        except Exception as _e_rst:
            self._warn("cut 前置选择状态自愈失败（继续尝试）: %r" % (_e_rst,))
        _sel_ok, _sel_info = self._ensure_cut_profile_selected()
        if not _sel_ok:
            # ── 【Bug-20 修复】用统一诊断助手把"选段失败"归因并给出修复建议 ──
            _diag = diagnose_feature_failure(
                self, api="FeatureCut3",
                extra={"selection_count": 0, "sel_info": str(_sel_info)})
            raise RuntimeError(
                "切除前【未能选中任何轮廓实体】，FeatureCut 必然失败或退化。\n"
                "  选择状态: %s\n"
                "【诊断】原因=%s（%s）\n"
                "建议：%s\n"
                "  另：草图退出后 SW 会清空选择，必须重新选中轮廓（边/面），"
                "而不是草图特征本身；若草图有多段轮廓请分次 cut，"
                "或改用 select_all_sketch_segments() 后重试；"
                "多孔件优先用 extrude_with_holes() 零切除建模（Bug-24 推荐）。"
                % (_sel_info, _diag.get("cause"), _diag.get("detail"),
                   _diag.get("advice")))

        # 记录切除前体积，用于事后校验
        _vol_before = self._body_volume_mm3()
        # ── 【Bug-37/42/43 修复】同时记录【拓扑签名】（体积+面数+边数+实体数+包围盒）
        #   作为"切除是否真的生效"的几何证据链，不再单靠去料率。
        _sig_before = self._topology_signature()
        _expect_through = bool(through)

        # ══ 【Bug T1 修复·验收判据重构】基于几何的期望去料体积 ══════════
        # 旧判据"贯穿去料率≥3%"对小孔天然误判：实测 Φ10 孔贯穿 Φ60x40
        # 圆柱真实去料 2.90%，被误拒后继续换签名重试反而破坏选择状态。
        # 新判据：期望体积 E = π·Σr²·H（圆半径取自轮廓草图，H=包围盒最大
        # 边长(贯穿) 或 depth(盲孔)）；实际去料 ≥ 35%·E 即判成功。
        # 35% 容差覆盖"孔与已有孔/空隙重叠"的情况；而对"枚举错位导致的
        # 浅盲孔"（去料不足期望的 10%）仍会正确拒绝。
        _radii = self._sketch_circle_radii_mm()
        # H：贯穿时用【法向轴方向】的包围盒长度（真实贯穿长度），
        #   而非包围盒最大边（实测会把 Φ60 当成长度，期望虚高 1.5 倍）
        _H_mm = float(depth)
        if through:
            _H_mm = self._bbox_extent_mm(self._sketch_normal_axis)
            if not _H_mm:
                _H_mm = self._bbox_max_dim_mm()
        _expected_mm3 = None
        # ── 【Bug-26 修复】同时算"保守期望" ──────────────────────────────
        # H 依赖"法向轴"追踪，多特征后可能取到零件外形（实测 180mm）而不是
        #   真实厚度 → 期望虚高 → 合法切除被拒。
        # 这里额外用【最小包围盒边长】算一个保守期望（下界），
        #   判据改为"去料 ≥ 35%·保守期望"，从根上消除 H 失真的影响。
        _H_conservative = None
        try:
            _dims = [self._bbox_extent_mm(0), self._bbox_extent_mm(1),
                     self._bbox_extent_mm(2)]
            _dims = [d for d in _dims if d]
            if _dims:
                _H_conservative = min(_dims)
        except Exception:
            _H_conservative = None
        _area_mm2 = 0.0
        try:
            _area_mm2 = float(self._sketch_area_mm2 or 0.0)
        except Exception:
            _area_mm2 = 0.0
        _expected_conservative = None
        if _area_mm2 > 0 and _H_mm:
            # 首选：circle()/rect() 画图时记录的轮廓面积（完全确定）
            _expected_mm3 = _area_mm2 * _H_mm
        elif _radii and _H_mm:
            # 兜底：从草图几何读圆半径
            try:
                import math as _math
                _expected_mm3 = _math.pi * sum(r * r for r in _radii) * _H_mm
            except Exception:
                _expected_mm3 = None
        if _H_conservative:
            if _area_mm2 > 0:
                _expected_conservative = _area_mm2 * _H_conservative
            elif _radii:
                try:
                    import math as _math
                    _expected_conservative = (_math.pi
                                              * sum(r * r for r in _radii)
                                              * _H_conservative)
                except Exception:
                    _expected_conservative = None

        def _try_cut(sig_name, args, note=""):
            """尝试一种签名并做结果校验。返回 (feat|None, reason)"""
            # 【Bug T1 诊断】DSH_SWAPI_DEBUG=1 时输出每次尝试前的选择现场
            if os.environ.get("DSH_SWAPI_DEBUG"):
                try:
                    _sm_dbg = self.model.SelectionManager
                    _n_dbg = int(_sm_dbg.GetSelectedObjectCount2(-1))
                    _types = []
                    for _i in range(1, _n_dbg + 1):
                        try:
                            _types.append(str(_sm_dbg.GetSelectedObjectType3(_i, -1)))
                        except Exception:
                            _types.append("?")
                    _as_dbg = None
                    try:
                        _ask = self.skm.ActiveSketch
                        _as_dbg = (_ask.Name if _ask is not None and hasattr(_ask, "Name") else ("active" if _ask is not None else None))
                    except Exception:
                        pass
                    print(u"[swapi][DBG] %s 前: 选中=%s 类型=%s ActiveSketch=%s 消费=%s"
                          % (sig_name, _n_dbg, _types, _as_dbg,
                             sorted(getattr(self, "_consumed_sketches", []) or [])))
                except Exception as _e_dbg:
                    print(u"[swapi][DBG] 诊断输出失败: %r" % (_e_dbg,))
            # ── 【F-1 修复·关键】每次尝试前都要【重新确保轮廓被选中】─────────
            # 测试部证据：cut 调用瞬间选中对象数=0。
            #   而 FeatureCut 每次执行都会消耗/清空选择 —— 第一次尝试失败后，
            #   后续尝试更是"空选择"调用。
            #   所以这里在【每次】调用 FeatureCut 之前都重建选择。
            try:
                _ok_sel, _info_sel = self._ensure_cut_profile_selected()
                if not _ok_sel:
                    return None, "轮廓未选中(%s)" % _info_sel
            except Exception as _e_sel:
                return None, "重建选择异常: %r" % (_e_sel,)
            try:
                fn = getattr(self.fm, sig_name)
            except Exception as e:
                return None, "无此方法: %r" % (e,)
            try:
                # ── 【D4 修复】COM 忙/被拒时退避重试 ──────────────────────
                # 用户正在操作 SW（点窗口/弹保存框）会让 FeatureCut 抛
                #   RPC_E_CALL_REJECTED。旧行为是记一条"调用异常"就换签名，
                #   最后整条链失败 → 小屋改脚本重跑。现在先在原地退避重试。
                # 注意：重试前必须【重建选择】（FeatureCut 每次执行都会
                #   消耗选择集），所以这里把重选放进重试闭包内。
                def _call_cut(_fn=fn, _args=args, _sig=sig_name):
                    try:
                        _ok2, _info2 = self._ensure_cut_profile_selected()
                        if not _ok2:
                            raise RuntimeError(
                                "CONTOUR_NOT_SELECTED: %s" % (_info2,))
                    except RuntimeError:
                        raise
                    except Exception:
                        pass
                    return _fn(*_args)

                f = com_retry(_call_cut,
                              _what="%s(切除)" % sig_name,
                              _log=self.warnings.append)
            except Exception as e:
                if str(e).startswith("CONTOUR_NOT_SELECTED:"):
                    return None, "轮廓未选中(%s)" % (str(e).split(":", 1)[1].strip(),)
                return None, "调用异常: %r" % (e,)
            if f is None:
                # 【R5 修复】返回 None 必须显式报错，且给可操作原因。
                #   历史实现把 None 当成"该签名不行，换下一个"，
                #   最后往往以"半成品成功"收场 —— 台账里 10 次观测全是这个。
                return None, ("返回 None —— SW 拒绝该切除。%s"
                              % _cut_none_hint(self, sig_name))
            self._visual_step("cut")
            self.rebuild()
            # 体积校验：切除后体积必须变小
            _vol_after = self._body_volume_mm3()
            # ══ 【R5 修复】体积读不到 ≠ 成功 ══════════════════════════════
            # 旧实现在 _vol_before/_vol_after 任一为 None 时直接 return f, ""，
            #   等于"量不出来就判成功"—— 正是"返回 CDispatch 但去料=0"
            #   能被当成成功的那条缝。现在：量不出来一律判失败并说明原因，
            #   由上层继续尝试其它签名；全部不行则报错，绝不静默。
            if _vol_before is None or _vol_after is None:
                return f, ("无法读取切除前后体积（before=%r, after=%r），"
                           "无法证明真的去了料 —— 按失败处理，不静默判成功"
                           % (_vol_before, _vol_after))
            if _vol_before is not None and _vol_after is not None:
                if _vol_after >= _vol_before - 1e-9:
                    return f, ("体积未减小(%.1f -> %.1f mm3)，可能未真正去料"
                               % (_vol_before, _vol_after))
                self._cut_removed_pct = (_vol_before - _vol_after) / _vol_before * 100.0
                _removed = (_vol_before - _vol_after) / _vol_before * 100.0
                # ── 【F 修复·第三轮】阈值太松，拦不住半成品 ──────────────
                # 测试反馈：阈值 1% 太松 —— 去料 54% 但【孔没贯穿】照样通过。
                #   "去料了一部分"不等于"贯穿成功"，必须用更强的判据。
                #
                # 新判据（三层，从强到弱）：
                #   ① 贯穿时用【截面/实体数一致性】判断：贯穿孔应让通孔轴线上
                #      出现"无材料"区域 —— 这里用体积占比做代理；
                #   ② 贯穿去料率下限提高到 3%（低于此几乎肯定没打通）；
                #   ③ 记录去料率供调用方判断，并在偏低时给出明确警告。
                out_removed = _removed
                _removed_mm3 = _vol_before - _vol_after
                # ══ 【Bug-26 修复】几何期望可能不可靠，不能仅凭它硬拒 ══════════
                # 原缺陷（壳体机架房间实测）：期望去料量按错误的 H=180mm 计算
                #   （法向轴追踪错位时 H 会取到零件外形而不是厚度），
                #   于是【合法的切除】被判"未达几何期望的 35%"而拒绝。
                #   小屋只能绕过封装、直接调 FeatureCut3。
                #
                # 修复：把几何期望从"唯一判据"降级为"首选判据"，并补两条
                #   独立的确认路径 —— 只要任一条能证明"确实切掉了材料"就放行：
                #     ① 几何判据：去料 ≥ 35%·期望（期望可信时最准）；
                #     ② 贯穿验证：through 时用 _check_through_hole() 实测通孔；
                #     ③ 保守下限：去料 ≥ 3%·实体体积 且 期望明显不可信时放行。
                #   只有三条都不成立才判失败（并说明是哪一条不成立）。
                # ══ 【Bug-37/42/43 修复】改用【几何/拓扑判定】作为主判据 ══════
                # 原缺陷（三个 Bug 同一根因）：主判据是"去料率 >= 3%"或
                #   "去料 >= 35%·几何期望"。这两个都只看【体积比例】，对以下
                #   真实成功的切除会误判失败：
                #     · Bug-37 薄壁件：Ø6 孔穿 2mm 壁 → 去料率仅 1.97% < 3%；
                #     · Bug-42 轴承座：方向偏 → 去料率 0.7%；
                #     · Bug-43 同类：已贯穿但去料率 0.7%。
                #   → 小屋被迫重试/改用 revolve 变通，或（叠加 Bug-19 重试上限）
                #     被误跳过，薄壁零件直接做不出来。
                #
                # 修复：主判据改为【拓扑变化】（见 _cut_geometry_verdict）：
                #   体积减小 + (面数增加 | 实体数变化 | 包围盒改变 | through)
                #   即判成功 —— 不再受"去料率小"影响。
                # 体积比例判据降级为"辅助确认"，仅在拓扑证据取不到时才用。
                _sig_after = self._topology_signature()
                _geo_ok, _geo_reason, _geo_detail = self._cut_geometry_verdict(
                    _sig_before, _sig_after, through=_expect_through)
                # ══ 【R5 修复】补集校验：flip_side_to_cut 必须证明"切的是补集" ══
                # 实测事故：flip=True 被接到 FeatureCut 第 2 位 Flip
                #   （flip side to cut = 切轮廓外侧），一次切掉 -30520.6 mm³，
                #   而体积校验只看"变小了"，照样判成功 —— 静默毁件。
                # 现在只要用了 Flip（切外侧），就必须额外确认：
                #   去料率 > 60% 说明切的是"轮廓外侧的整块材料"（补集），
                #   正常挖孔/开槽绝无可能去掉大半个零件 → 直接判失败。
                if flip_side_to_cut:
                    _pct_f = None
                    try:
                        if _vol_before:
                            _pct_f = (_vol_before - _vol_after) / _vol_before * 100.0
                    except Exception:
                        _pct_f = None
                    if _pct_f is not None and _pct_f > 60.0:
                        return f, (
                            "flip_side_to_cut=True 切掉了 %.1f%% 的材料 —— "
                            "这是【轮廓外侧补集】的特征（正常挖孔/开槽不会"
                            "去掉大半个零件）。已判失败，防止静默毁件。"
                            "若本意是反转方向，请改用 flip=True（走 Dir 位）。"
                            % _pct_f)
                if _geo_ok:
                    # 拓扑证据成立 → 直接放行（薄壁小孔也正确通过）
                    if (_geo_detail.get("removed_pct") is not None
                            and _geo_detail["removed_pct"] < 3.0):
                        self._warn(
                            "cut: 去料率仅 %.2f%%（低于旧 3%% 阈值），但%s"
                            " —— 按几何判定为成功（Bug-37/42/43）"
                            % (_geo_detail["removed_pct"], _geo_reason))
                    return f, ""
                # 拓扑证据不足 → 退回体积比例辅助判据（保守）
                _thr_base = _expected_conservative or _expected_mm3
                if _thr_base and _removed_mm3 >= 0.35 * _thr_base:
                    return f, ""
                if _expect_through:
                    try:
                        _th = self._check_through_hole()
                        _through_ok = (bool(_th) if isinstance(_th, bool)
                                       else bool((_th or {}).get("ok")))
                    except Exception:
                        _through_ok = False
                    if _through_ok and _removed >= 0.05:
                        return f, ""
                return f, ("几何判定失败：%s；且去料 %.1f mm3 未达几何期望"
                           "（期望 %.1f mm3，轮廓圆 r=%s × H=%.1fmm，"
                           "去料率 %.2f%%）—— 未真正贯穿/未有效去料"
                           % (_geo_reason, _removed_mm3,
                              _expected_mm3 or 0.0,
                              [round(r, 2) for r in _radii], _H_mm, _removed))
            return f, ""

        # ══ 【Bug T2 修复】cut 结束后必须退出草图编辑态 ═════════════════
        # 路径-1/路径1 会用 EditSketch 进入草图。创建特征后若停留在编辑态，
        # 下一个 begin_sketch_on_face 的 InsertSketch 会退化成"编辑旧草图"，
        # 新圆画进旧轮廓 → 切除范围错乱。统一在此收口。
        def _exit_sketch_editing():
            try:
                if self.skm.ActiveSketch is not None:
                    self.skm.InsertSketch(True)
            except Exception:
                pass

        _best = None
        _notes = []

        # ══ 【F 修复·第七轮】按专家意见重排：FeatureCut4 优先 ═════════════
        # FeatureCut3 在 SW2025 已 Obsolete；FeatureCut4 的终止条件与深度
        #   配合更稳定。因此把 Cut4 提为首选，Cut3 降为回退。
        #
        # 贯穿时并行尝试两种语义（专家建议 3 的兜底策略）：
        #   (a) ThroughAll 枚举 —— 让 SW 自行处理贯穿；
        #   (b) Blind + 9999mm 大深度 —— 枚举不生效时的等价替代。
        #   哪种真正切穿（体积校验通过）就用哪种。
        # ── 【F-2 修复】d1/d2 必须【无条件给足】，避免被错位读成 0 ────────
        # 测试部指出：T1=1(贯穿) 时 d1=0.000mm，因为原实现只在 Blind 分支
        #   才给大深度 —— 属于逻辑设计缺陷：一旦 FeatureCut4 的参数顺序里
        #   d1 落到别的位置，SW 会把"0"当成别的参数读，贯穿也随之失效。
        # 修复：贯穿时【同样填入 9999mm】。
        #   理由：贯穿枚举生效时 SW 会忽略深度（无害）；枚举不生效时，
        #   超大深度还能作为盲孔把零件切穿（有用）。两种情况都受益。
        # ══ 【Sd 位修复 · 2026-11-11】候选链补第 6 元 sd ══════════════════
        # 现场现象/根因/判据见 _cut_args() 文档串「Sd 位修复 · 2026-11-11」。
        # 要点：双向贯穿必须 Sd=False（双端），否则 SW 只按单端解释 T1，T2
        #   被忽略 → 双环辐板上孔不贯穿（DSH_大齿轮 体积偏差 49.95%）。
        # 因此候选链改为 6 元组 (label, T1, T2, d1, d2, sd)：
        #   · 贯穿类候选：through_both → Sd=False（真双向）；否则 Sd=True；
        #   · (c) 大深度盲孔语义保留 Sd=True，行为与修复前完全一致（兜底不变）。
        _cands = []
        if through:
            _sd_both = not bool(through_both)      # 双向贯穿 → 双端(Sd=False)
            # (a) 贯穿枚举 + 大深度（双保险：枚举优先，深度兜底）
            _cands.append(("贯穿枚举+大深度", SW_END_THROUGH,
                           SW_END_THROUGH_BOTH if through_both else 0,
                           _big, _big if through_both else 0.0, _sd_both))
            # (b) 纯贯穿枚举（深度 0，最贴近官方用法）
            _cands.append(("贯穿枚举", SW_END_THROUGH,
                           SW_END_THROUGH_BOTH if through_both else 0,
                           0.0, 0.0, _sd_both))
            # (c) 大深度盲孔语义：完全绕开贯穿枚举（Sd 保持 True，不动兜底）
            _cands.append(("大深度盲孔", SW_END_BLIND,
                           SW_END_BLIND if through_both else 0,
                           _big, _big if through_both else 0.0, True))
        else:
            _cands.append(("盲孔", SW_END_BLIND, SW_END_BLIND, d1, d2, True))

        # ══ 【真机实测修复·SW2025】API 优先级：Cut3（可用）-> Cut4（回退）═══
        # 决定性真机对照实验（_t_api.py，SW2025 SP5.0，长方体 70x80x320 端面
        # 画 Φ40 圆后选中，直接调 IFeatureManager）：
        #   · FeatureCut3(24 args, ThroughAll) → 返回特征，体积
        #       1792000 → 1389876 mm3（真实去料 22.4%）✅ 完全可用；
        #   · FeatureCut4(24 args) → com_error -2147352561 '非选择性的参数'
        #       ❌ 该参数布局不被 SW2025 的 Cut4 接口接受；
        #   · FeatureCut4(27 args) → com_error -2147352562 '无效的参数数目'
        #       ❌ 加参数也不行，说明 Cut4 与 Cut3 的形参布局本就不同。
        # 结论：本机 Cut4 不可用、Cut3 可用 —— 与旧注释"Cut3 已 Obsolete、
        #   应优先 Cut4"的假设【恰好相反】。旧代码把 Cut4 放首位，导致每次
        #   cut 都先白吃 3~6 个 com_error，既拖慢又刷屏；而一旦 Cut3 的某个
        #   签名在本轮不可用（如底面/对侧面场景），整条链就彻底失败（T1 第2次
        #   cut 必失败的直接原因）。
        # 修复：把 Cut3 提为首选、Cut4 降为回退，顺序颠倒过来。
        _apis = ("FeatureCut3", "FeatureCut4")

        # ── 【C03 修复 · 2026-10-29】FeatureCut4 必须 27 参，Cut3 才是 26 参 ──
        #
        # 真机实测（SW2025 SP5.0，长方体 70x80x320 端面画 Ø10 圆）：
        #   · FeatureCut3(26 参) → 正常切除；
        #   · FeatureCut4(26 参) → com_error -2147352561「非选择性的参数」
        #       （其实是【参数个数不对】被 COM 报成了这句含糊的话）；
        #   · FeatureCut4(27 参，末尾补 OptimizeGeometry=False) → **真实切除**，
        #       去料 0.818%，与 Ø10 通孔理论值 785.4/96000 = 0.818% 精确吻合。
        #
        # 也就是说：Cut3 与 Cut4 的形参布局【不同】，不能共用同一个元组。
        # 原实现两个 API 共用 26 元组 → 每次 Cut4 都白抛异常（既拖慢又刷屏），
        # 而 Cut3 一旦在某场景不可用（底面/对侧面），整条链就彻底失败。
        #
        # 现在：按 API 名给出各自的参数元组（Cut3=26 / Cut4=27）。
        # 参数位对照（官方签名）：
        #   Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
        #   Dang1, Dang2, OffsetReverse1, OffsetReverse2, TranslateSurface1,
        #   TranslateSurface2, NormalCut, UseFeatScope, UseAutoSelect,
        #   AssemblyFeatureScope, AutoSelectComponents, PropagateFeatureToParts,
        #   T0, StartOffset, FlipStartOffset [, OptimizeGeometry ← 仅 Cut4]
        def _cut_args(api, dir_pass=False, t1=SW_END_BLIND, t2=SW_END_BLIND,
                      dd1=0.0, dd2=0.0, flip_side=False, sd=True):
            """按【官方形参顺序】生成 FeatureCut3/4 的位置参数元组。

            ── 【R5 修复】两处语义纠正（此前都接错线）────────────────────
            ① 第 2 位 Flip 是「flip side to cut（反转切除侧）」，
               【不是】「反转方向」。官方语义：True 时切掉轮廓【外侧】的材料。
               历史实现把用户的 flip= 和"自动反向重试"都接到这一位 →
               每次反向都变成"切补集"（实测 -30520.6 mm³）；而且因为体积
               确实大幅减小，还能通过体积校验 → 静默毁件。
               现在：这一位【只】由 flip_side 控制，默认 False。
            ② 第 3 位 Dir 才是「反转方向」（Direction1 与默认方向相反）。
               用户的 flip= / 自动反向重试一律落在这里。
            参数名/顺序依据：本机 SW 类型库 IFeatureManager.FeatureCut3
            （gen_py 缓存 L22427，26 参）与 FeatureCut4（L22446，27 参）。
            另：形参名表见模块级 CUT_PARAM_NAMES / CUT_PARAM_NAMES_4，
            顺序一旦漂移 _args_in_official_order 会当场抛错，不再静默错位。

            ── 【Sd 位修复 · 2026-11-11】第 1 位 Sd 不能恒 True ──────────
            现场现象：双环辐板（DSH_大齿轮）上 cut(through=True) 孔不贯穿，
              体积偏差 49.95%（1214042.68 vs 期望 809621.02 mm³），
              self_check FAIL。证据：
              test/20261111_131815_580E38FA/reports/DSH_大齿轮.self_check.json
            根因：官方 Sd 语义是「True = 单端切除，False = 双端切除」。
              本函数此前恒传 True，于是 SW 按【单端】解释 T1/D1，
              T2/D2（含双向贯穿）被整体忽略 —— 双向贯穿在物理上不可能生效，
              与实际观测的"只切掉一段"完全吻合。
            修复判据：
              · through and through_both → Sd=False, T1=T2=THROUGH_BOTH(9)
                （双端，两端都完全贯穿；此时深度被忽略，d1/d2 给 0）
              · through and not through_both → Sd=True, T1=THROUGH(1)
                （单端贯穿，语义与官方一致）
              · 非贯穿 → 沿用原 Sd=True 盲孔行为，不动既有防御链。
            默认 sd=True 保持向后兼容：所有既有调用点不传 sd 时行为不变。
            """
            _base = (
                bool(sd),        # Sd      True=单端 / False=双端（见上方 2026-11-11）
                bool(flip_side), # Flip  ← 反转切除侧（切轮廓外侧），默认 False
                bool(dir_pass),  # Dir   ← 反转方向（方向重试走这一位）
                t1,              # T1
                t2,              # T2
                dd1,             # D1
                dd2,             # D2
                False,           # Dchk1
                False,           # Dchk2
                False,           # Ddir1
                False,           # Ddir2
                0.0,             # Dang1
                0.0,             # Dang2
                False,           # OffsetReverse1
                False,           # OffsetReverse2
                False,           # TranslateSurface1
                False,           # TranslateSurface2
                False,           # NormalCut（非钣金必须 False）
                False,           # UseFeatScope
                auto_select,     # UseAutoSelect ← 自动认轮廓，关键
                False,           # AssemblyFeatureScope
                False,           # AutoSelectComponents
                False,           # PropagateFeatureToParts
                0,               # T0
                0.0,             # StartOffset
                False,           # FlipStartOffset
            )
            # 形参顺序自检：与官方 26 参表逐位对齐（错位即抛错，不静默）
            assert len(_base) == len(CUT_PARAM_NAMES), (
                "FeatureCut3 形参个数=%d，官方=%d"
                % (len(_base), len(CUT_PARAM_NAMES)))
            if str(api).endswith("4"):
                # FeatureCut4 多一个 OptimizeGeometry（非钣金必须 False：
                #   实测传 True 会返回 None）
                return _base + (False,)
            return _base


        # 【真机实测修复】底面（法向朝外背离材料）上的切除：默认方向朝材料外的
        # 空侧，FeatureCut3 全签名返回 None（或 Extrusion3 反向增料）。
        # 修复：常规方向失败后，自动用 Flip=True 反向重试一轮 —— 哪个方向真正
        # 去料（体积校验通过）就用哪个。
        # ══ 【R5 修复】方向重试只动 Dir，绝不动 Flip ══════════════════════
        # 旧实现把"用户 flip"和"自动反向"都接到 FeatureCut 的第 2 位 Flip，
        # 而该位官方语义是「flip side to cut = 切掉轮廓【外侧】的材料」——
        # 于是每次自动反向都变成"切补集"（实测 -30520.6 mm³），且因为
        # 体积确实大幅减小，还能通过体积校验 → 静默毁件。
        # 现在：方向重试全部走第 3 位 Dir；Flip 只在调用方显式
        #   flip_side_to_cut=True 时才置 True（并额外做补集校验）。
        _dir_passes = [bool(flip), not bool(flip)]
        if True not in _dir_passes:
            _dir_passes.append(True)
        for _dir_pass in _dir_passes:
            for _api in _apis:
                for _label, _t1, _t2, _dd1, _dd2, _sd in _cands:
                    _f, _why = _try_cut(_api, _cut_args(
                        _api, dir_pass=_dir_pass, t1=_t1, t2=_t2,
                        dd1=_dd1, dd2=_dd2,
                        flip_side=bool(flip_side_to_cut), sd=_sd))
                    if _why == "":
                        _exit_sketch_editing()
                        # 【Bug T1 修复】把本草图标记为"已被消费"，
                        # 同零件后续 cut 不可能再沿用这次轮廓。
                        self._mark_sketch_consumed()
                        if _dir_pass != bool(flip):
                            self._warn("cut 已自动反向（Dir=True）完成切除 —— "
                                       "草图位于底面/法向背离材料")
                        return _f
                    _notes.append("%s(dir=%s,%s): %s"
                                  % (_api, _dir_pass, _label, _why))
                    _best = _best or _f

        # ══ 【R5 修复】删除了 FeatureExtrusion3 兜底 ══════════════════════
        # 类型库 IFeatureManager.FeatureExtrusion3（L22597）的 23 个形参里
        # 【没有】任何"切除/AddPad"位：第 18 位是 Merge（合并结果）、
        # 第 19 位 UseFeatScope、第 20 位 UseAutoSelect。
        # 也就是说它只能【增料】，用它当切除兜底只会给零件留一个多余凸台，
        # 而旧代码还会因"体积变化"误判成功。历史注释里的
        # 「FeatureExtrusion3 的切除模式(AddPad=False)」在类型库中不存在。

        # ── 【F 修复·第六轮】报错必须附带【参数诊断】，便于定位错位 ────────
        # 测试反馈：多次尝试仍只去料 1.96%，但无法判断是"参数错位"还是
        #   "轮廓没选中"。这里把实际传给 FeatureCut3 的关键参数原样输出，
        #   下次实测即可直接比对 —— 无需再猜。
        _diag = {
            "through": bool(through),
            "through_both": bool(through_both),
            "T1": int(T1), "T2": int(T2),
            "d1_mm": round(float(d1) * 1000.0, 3),
            "d2_mm": round(float(d2) * 1000.0, 3),
            "vol_before_mm3": _vol_before,
            "vol_after_mm3": self._body_volume_mm3(),
            "sketch_active": None,
            "selected_objs": None,
        }
        try:
            _diag["sketch_active"] = self.skm.ActiveSketch is not None
        except Exception:
            pass
        try:
            _sm = self.model.SelectionManager
            _diag["selected_objs"] = _sm.GetSelectedObjectCount2(-1)
        except Exception:
            pass

        # 全部签名都不达标 —— 明确报错，绝不留半成品当成功
        self._warn("cut 所有签名均未达标（through=%s, depth=%s）: %s"
                   % (through, depth, " | ".join(_notes)))
        if _best is not None:
            raise RuntimeError(
                "切除特征已创建，但【未通过结果校验】—— 可能没真正去料或未贯穿。\n"
                "各签名尝试结果：\n  " + "\n  ".join(_notes) + "\n"
                "── 参数诊断（请把这段发给维修部）──\n"
                "  T1=%s T2=%s d1=%.3fmm d2=%.3fmm through=%s both=%s\n"
                "  草图激活=%s  已选中对象数=%s\n"
                "  体积: %s -> %s mm3\n"
                "建议：若 T1/d1 的值与你预期不符，说明参数位错位；"
                "若【草图未激活】或【选中对象数=0】，则是轮廓没选中。"
                % (T1, T2, float(d1) * 1000.0, float(d2) * 1000.0,
                   bool(through), bool(through_both),
                   _diag.get("sketch_active"), _diag.get("selected_objs"),
                   _diag.get("vol_before_mm3"), _diag.get("vol_after_mm3")))
        # ══ 【底面切除迁移】Z 向端面草图在对侧面重建同位轮廓再切一次 ══════
        # 真机实测规律：零件存在既有切除后，在【min-Z 端面】上开草图再贯穿切除，
        # FeatureCut3/4 全签名返回 None（选中状态完全正常、双向/Flip 均无效）；
        # 同一孔位改从 max-Z 面切除则成功。故在彻底失败前自动迁移重试一次。
        _migrated = False
        try:
            _circles = list(getattr(self, "_sketch_circles_local", []) or [])
            _axis = int(getattr(self, "_sketch_normal_axis", 2))
            _off = float(getattr(self, "_sketch_axis_offset_mm", 0.0))
            if (through and _circles and _axis == 2):
                _ext = self._bbox_extent_mm(2)
                if _ext:
                    _zmin, _zmax = 0.0, float(_ext)
                    _at_min = abs(_off - _zmin) < 0.5
                    _at_max = abs(_off - _zmax) < 0.5
                    if _at_min or _at_max:
                        _z_opp = _zmax if _at_min else _zmin
                        # 取第一个圆的局部圆心作为全局 (x,y)（同向面局部系一致）
                        _cx0, _cy0, _r0 = _circles[0]
                        try:
                            self.clear_selection()
                            self._migrate_cut_to_opposite_face(
                                _cx0, _cy0, _z_opp, _circles)
                            _migrated = True
                            self._warn("底面切除受限，已自动迁移到对侧面重试 "
                                       "(z=%.1f -> z=%.1f)" % (_off, _z_opp))
                            # 重跑两轮【方向】尝试（R5：方向只走 Dir，不走 Flip）
                            for _dir_pass in (False, True):
                                for _api in _apis:
                                    for _label, _t1, _t2, _dd1, _dd2, _sd in _cands:
                                        _f, _why = _try_cut(_api, _cut_args(
                                            _api, dir_pass=_dir_pass,
                                            t1=_t1, t2=_t2, dd1=_dd1, dd2=_dd2,
                                            flip_side=bool(flip_side_to_cut),
                                            sd=_sd))
                                        if _why == "":
                                            _exit_sketch_editing()
                                            self._mark_sketch_consumed()
                                            return _f
                                        _notes.append(
                                            "迁移后%s(dir=%s,%s): %s"
                                            % (_api, _dir_pass, _label, _why))
                        except Exception as _e_mig:
                            _notes.append("迁移重试异常: %r" % (_e_mig,))
        except Exception as _e_mig2:
            _notes.append("迁移判定异常: %r" % (_e_mig2,))

        # ══ 【Bug-40 修复】全部签名失败 → 重置基准面上下文后再试一轮 ═══════
        # 台账 3.1 现象：跨基准面（Top→Front/Right）或多特征叠加后 cut 失败
        #   （FeatureCut3 返回 None / com_error）。变通办法是"每零件独立脚本"，
        #   说明根因是 SW 进程内累积的【面/草图 COM 上下文】污染，而非几何错误。
        # 修复：在判定彻底失败【之前】，强制重置上下文（退出草图 + 清选择 +
        #   重建），让 cut 按【显式记录的草图名】重新 EditSketch 取轮廓，再跑
        #   一轮候选签名。这相当于把"重开脚本"的变通内化到封装里。
        _ctx_reset_tried = False
        try:
            self._reset_plane_context(None, reason="cut-fallback")
            _ctx_reset_tried = True
        except Exception as _e_rst:
            _notes.append("上下文重置异常: %r" % (_e_rst,))
        if _ctx_reset_tried:
            _notes.append("已执行 Bug-40 上下文重置，重试一轮候选签名")
            for _dir_pass in (bool(flip), not bool(flip), True):
                for _api in _apis:
                    for _label, _t1, _t2, _dd1, _dd2, _sd in _cands:
                        _f, _why = _try_cut(_api, _cut_args(
                            _api, dir_pass=_dir_pass, t1=_t1, t2=_t2,
                            dd1=_dd1, dd2=_dd2,
                            flip_side=bool(flip_side_to_cut), sd=_sd))
                        if _why == "":
                            _exit_sketch_editing()
                            self._mark_sketch_consumed()
                            self._warn("cut 在 Bug-40 上下文重置后成功 "
                                       "(api=%s, dir=%s)" % (_api, _dir_pass))
                            return _f
                        _notes.append("重置后%s(dir=%s,%s): %s"
                                      % (_api, _dir_pass, _label, _why))
                        _best = _best or _f

        # 【真机实测】多实体零件会让 FeatureCut3 因"切除结果归属歧义"直接返回
        # None（选中状态完全正常也如此）。必须精确点名，否则会被误判为选中问题。
        _body_count = None
        try:
            _bodies = self.model.GetBodies2(0, 1)
            _bl = list(_bodies) if isinstance(_bodies, tuple) else ([_bodies] if _bodies else [])
            _body_count = len(_bl)
        except Exception:
            _body_count = None
        _mb_hint = ""
        if _body_count and _body_count > 1:
            _mb_hint = ("\n⚠️ 检测到零件已分裂为 %d 个实体（多实体零件）。\n"
                        "  SW 会因切除结果归属歧义拒绝执行切除（选中状态正常也如此）。\n"
                        "  请先合并实体（FeatureManager.Combine）或调整轮廓避免产生悬空体，"
                        "再执行切除。" % _body_count)
        raise RuntimeError(
            "切除特征创建失败（所有签名均失败）:\n  " + "\n  ".join(_notes) + "\n"
            "实体数=%s%s\n"
            "常见原因：①切除区域与已有切除重叠（无料可切）；②多实体歧义；"
            "③轮廓未闭合。" % (_body_count, _mb_hint))

    def revolve(self, angle_deg=360, cut=False, expect_cylinder=None,
                verify=True):
        """旋转特征。草图需含轮廓 + centerline() 旋转轴。angle 单位度。

        修复: FeatureRevolve2 使用最后一个草图，不需要 ActiveSketch。

        ══ 【新-3 修复】绕错轴会静默产出错误几何 ═════════════════════════
        测试反馈：用竖中心线(x=0)+剖面跨 X 0→140 做旋转建轴，
          revolve(360) 返回 OK 不报错，但实际产出的是 40x60x350 的板，
          而不是 Φ40x140 的轴。属于最危险的"静默产出错误几何"。

        根因：FeatureRevolve2 的旋转轴由草图里的中心线决定，
          若中心线位置/方向不符合预期（共面、沿轴向判断不足），
          它会绕另一条轴旋转，而 API 不报错。

        修复：返回前做【结果合理性校验】——
          · 通过包围盒判断：圆柱特征应有"一个方向尺寸远小于
            其余两个方向（且那两方向近似相等）"的特征；
          · 若检测到产出的包围盒明显不像回转体，返回 ok=False
            并说明，而不是静默交差。

        Args:
            expect_cylinder: 若已知期望的圆柱参数，可传 (直径_mm, 长度_mm)
                             做精确校验；None 则只做形态合理性判断。
            verify: 是否校验（默认 True，强烈建议保持）

        Returns: feat（成功）/ 抛出 RuntimeError（失败或校验不通过，附带详情）
        """
        ang = math.radians(angle_deg)
        # ══ 【新-3 修复·第二轮】FeatureRevolve2 返回 None ═════════════════
        # 第一轮实测：结构件调 revolve 时 FeatureRevolve2 直接返回 None。
        #   原注释假设"使用最后一个草图，不需要 ActiveSketch" —— 该假设
        #   不可靠：若草图未处于选中/激活态，或存在多个草图导致歧义，
        #   特征创建就会失败并返回 None。
        #
        # 修复：
        #   1) 调用前【显式选中】当前草图（ActiveSketch 或最后一个草图）；
        #   2) 依次尝试多种 FeatureRevolve2 签名（版本差异大）；
        #   3) 全部失败时给出【具体原因】而非笼统的"返回 None"。

        # ── 步骤1：尽量确保有选中的草图轮廓 ──
        # ══ 【Bug T3 修复】复杂轮廓的选中链路加固 ═══════════════════════
        # 现象：简单轮廓（关节轴）revolve 成功；多段阶梯轮廓（底座）必失败，
        #   报"草图未选中或轮廓不闭合"。
        # 根因：revolve 依赖 select_all_sketch_segments() 在激活态选段，
        #   复杂轮廓下该选中逻辑失效；而"选特征树最后一个草图特征"的退路
        #   选中的是草图特征(type 9)而非轮廓段，FeatureRevolve2 拿不到轮廓。
        # 修复（三级递进，全部基于真实轮廓段）：
        #   ① 激活态直接选段（原首选路径）；
        #   ② 失败 → 按显式记录的草图名 EditSketch 后再选段
        #      （与 cut() 的路径-1 同源，专治复杂轮廓）；
        #   ③ 仍失败 → 才退回"选最后一个草图特征"（老行为，垫底）。
        _sk_ok = False
        _seg_selected = False
        try:
            sk = self.skm.ActiveSketch
            if sk is not None:
                # 选中草图中的全部线段作为旋转轮廓
                _ok, _n = self.select_all_sketch_segments()
                _sk_ok = True
                _seg_selected = bool(_ok)
        except Exception:
            _sk_ok = False
        # ②【T3】激活态选段失败 → 按显式名 EditSketch 进入后重选
        if not _seg_selected:
            _cons = getattr(self, "_consumed_sketches", None) or set()
            _cand = self._active_sketch_name
            if _cand and str(_cand) not in _cons:
                _okE, _nE = self._try_edit_sketch_by_name(_cand)
                if _okE:
                    _sk_ok = True
                    _seg_selected = True
                    try:
                        print("[swapi] revolve: 复杂轮廓按名 EditSketch 选中 %s 段"
                              % _nE)
                    except Exception:
                        pass
        # ③ 仍失败 → 老退路：选特征树最后一个草图特征（垫底）
        if not _seg_selected and not _sk_ok:
            try:
                _last = None
                f = self.model.FirstFeature()
                while f is not None:
                    try:
                        if f.GetTypeName2() == "ProfileFeature":
                            _last = f
                    except Exception:
                        pass
                    f = f.GetNextFeature()
                if _last is not None:
                    _last.Select2(False, 0)
                    _sk_ok = True
            except Exception:
                pass

        # ── 步骤2：多签名尝试 ──
        _errs = []
        _feat = None
        for _sig, _args in (
            ("FeatureRevolve2", (
                True, True, False, cut, False, False,
                SW_REV_BLIND, 0, ang, 0,
                False, False, 0, 0, 0, 0, 0,
                True, False, True)),
            ("FeatureRevolve2", (
                True, True, False, cut, False, False,
                SW_REV_BLIND, 0, ang, 0,
                False, False, 0, 0, 0, 0, 0,
                True, False, True, False)),
            ("FeatureRevolve", (
                True, True, False, cut, False, False,
                SW_REV_BLIND, 0, ang, 0,
                False, False, 0, 0, 0, 0, 0,
                True, False, True)),
        ):
            try:
                fn = getattr(self.fm, _sig, None)
                if fn is None:
                    _errs.append("%s: 方法不存在" % _sig)
                    continue
                _feat = fn(*_args)
                if _feat is not None:
                    break
                _errs.append("%s: 返回 None" % _sig)
            except Exception as e:
                _errs.append("%s: %r" % (_sig, e))

        if _feat is None:
            _hint = ("草图未选中或轮廓不闭合" if not _sk_ok
                     else "轮廓/中心线不满足旋转条件（需封闭轮廓 + 中心线）")
            raise RuntimeError(
                "旋转特征创建失败。\n"
                "草图状态: %s\n"
                "各签名尝试: %s\n"
                "最可能原因: %s。"
                % ("已选中" if _sk_ok else "未找到可用的草图",
                   " | ".join(_errs), _hint))
        feat = _feat
        # 【Bug T2/T3 修复】EditSketch 路径成功后退出编辑态，
        # 避免下一个 begin_sketch 退化成"编辑旧草图"。
        try:
            if self.skm.ActiveSketch is not None:
                self.skm.InsertSketch(True)
        except Exception:
            pass
        # 消费标记：该草图轮廓已转成旋转特征，后续特征不得再沿用
        self._mark_sketch_consumed()
        self._visual_step("revolve")
        self.rebuild()  # Bug 9: 重建模型以清除 COM 内部选择状态累积
        # ── 【BUG-05/08 修复】首个实体已生成 → 此刻才能真正赋材质 ──────
        try:
            self._try_apply_pending_material()
        except Exception:
            pass

        # ── 【新-3 修复·第三轮】形态校验：取不到证据 = 不通过 ──
        # 测试反馈：成功分支缺形态校验 —— 绕错轴仍能返回成功。
        # 根因（两个叠加）：
        #   1) 原实现把校验抛出的非 RuntimeError 异常用
        #      except 吞掉只告警，于是校验【静默跳过】，函数照常返回 feat；
        #   2) 校验函数在取不到包围盒时只返回 ok=False，
        #      而该结果被上面的宽 except 掩盖。
        # 修复原则（与全项目不静默失败一致）：
        #   · 校验通过 -> 返回 feat；
        #   · 校验不通过（形态不符 OR 无法取证）-> 一律 raise；
        #   · 只有显式 verify=False 才跳过。
        if verify and not cut:
            chk = None
            try:
                chk = self.verify_revolve_result(expect_cylinder=expect_cylinder)
            except Exception as _e:
                chk = {"ok": False,
                       "reason": "形态校验执行失败（无法取证）: %r" % (_e,)}
            if not chk or not chk.get("ok"):
                _why = (chk or {}).get("reason", "形态不符")
                _bbox = (chk or {}).get("bbox_mm")
                raise RuntimeError(
                    "旋转特征未通过形态校验，疑似【绕错轴】: %s"
                    "  实测包围盒: %s"
                    "  建议：检查草图里的中心线与轮廓是否共面、方向是否正确；"
                    "或传 expect_cylinder=(直径, 长度) 做精确校验；"
                    "若确实是特殊形状（非回转体），可显式传 verify=False 跳过。"
                    % (_why, _bbox))
        return feat

    def verify_revolve_result(self, expect_cylinder=None):
        """【新-3 修复】校验旋转结果是否为合理的回转体。

        Returns: {ok, bbox_mm?, reason?, is_cylinder_like?}
        """
        out = {"ok": False}
        try:
            # 【R11 修复】无参方法，不能传 (0)（详见 _body_volume_mm3 同处注释）
            mp = _com_get(self.model, "GetMassProperties") or []
        except ComMemberError:
            mp = []
        except Exception:
            mp = []
        # 优先用包围盒（更直接反映形态）
        try:
            bodies = self.model.GetBodies2(0, 1)
            bl = list(bodies) if isinstance(bodies, tuple) else ([bodies] if bodies else [])
            if bl:
                bb = bl[0].GetBodyBox()
                dims = sorted([abs(bb[3] - bb[0]) * 1000,
                               abs(bb[4] - bb[1]) * 1000,
                               abs(bb[5] - bb[2]) * 1000])
                out["bbox_mm"] = [round(d, 3) for d in dims]
                small, mid, big = dims[0], dims[1], dims[2]
                # ── 回转体的包围盒特征（关键：相等的两个方向是【直径】）──────
                #   圆柱：Φ D、长 L  →  包围盒为 (D, D, L)
                #   所以应该是【两个相等的较小方向】+ 一个不同的大方向。
                #   注意：不能用"最大的两个方向相等"来判断（那是把 D 和 L 比），
                #     用 Φ40x140 验证过：sorted=[40,40,140]，相等的是前两个。
                # 【Bug T3 修复·判据扩展】回转体有两种形态：
                #   轴类 (D, D, L)，L > D —— 两个相等小方向 + 一个大方向；
                #   盘类 (L, D, D)，L < D —— 一个小方向 + 两个相等大方向
                #   （底座法兰/回转台正是盘类，原判据误判为"绕错轴"）。
                # 统一判据：存在两个近似相等的方向（无论大小），第三个明显不同。
                two_equal_small = (small > 0 and abs(mid - small) / small < 0.15)
                two_equal_big = (mid > 0 and abs(big - mid) / mid < 0.15)
                elongated = (small > 0 and big / small > 1.2)
                cyl_like = bool(two_equal_small and elongated) or \
                    bool(two_equal_big and big / small > 1.2)
                out["is_cylinder_like"] = cyl_like
                if expect_cylinder:
                    d_exp, l_exp = float(expect_cylinder[0]), float(expect_cylinder[1])
                    # 直径 ≈ 两个相等方向(small/mid)，长度 ≈ 最大方向(big)
                    d_ok = abs(small - d_exp) / d_exp < 0.1 if d_exp else False
                    l_ok = abs(big - l_exp) / l_exp < 0.15 if l_exp else False
                    if not (d_ok and l_ok):
                        out["reason"] = ("与期望圆柱不符：期望 Φ%.1f x %.1f，"
                                         "实测包围盒 %s" % (d_exp, l_exp, out["bbox_mm"]))
                        return out
                    out["ok"] = True
                    return out
                if not cyl_like:
                    out["reason"] = ("包围盒 %s 不符合回转体特征"
                                     "（应为两个近似相等的小方向 + 一个明显更大的方向，"
                                     "即 (D, D, L)）—— 极可能是绕错了轴"
                                     % (out["bbox_mm"],))
                    return out
                out["ok"] = True
                return out
        except Exception as e:
            out["reason"] = "包围盒校验异常: %r" % (e,)
            return out
        out["reason"] = "无法获取实体包围盒"
        return out

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

    def _make_offset_plane(self, base_plane="Front Plane", offset_mm=0.0):
        """【S-06】在基准面上插入一个【偏移基准面】，返回 {ok, name, error}。

        用途：让"离面"的几何（如球心 z）由【真实特征】表达，而不是依赖
        实体平移 —— 实测 IModelDocExtension 上并没有 InsertMoveCopyBody2，
        那条路走不通。偏移基准面是 SW 的原生特征，随零件保存、可审计。

        实现：IFeatureManager::InsertRefPlane(First, FirstDist, Second,
        SecondDist, Third, ThirdDist) —— 6 参版本实测可用；
        第 1 个约束取 8 = swRefPlaneReferenceConstraint_Distance（类型库常量）。
        """
        out = {"ok": False, "name": None, "error": None, "offset_mm": None}
        try:
            off = float(offset_mm)
        except Exception:
            out["error"] = "offset_mm 非法"
            return out
        out["offset_mm"] = off
        # == 【v2 回归复测 P1 修复】选基准面前先彻底清空选择 + 退草稿 ==
        # 现场（复测报告 v2 第4.2节）：helix_gear 分段放样走到【第 4 段】
        #   就报「基准面 'Top Plane' 选不中，无法建偏移面」。
        # 根因：前几段留下的残留选中对象/未退干净的草图上下文，
        #   会让 SelectByID2 选基准面失败 —— 这是本项目反复踩过的
        #   "状态依赖型选择错乱"（swapi 里多处 WARN 就是它）。
        #   第 1~3 段能成、第 4 段不成，正是"残留累积到某个阈值"的表现。
        # 修复：每次建偏移面前做一次强清场，并给选面两次机会。
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        try:
            # 若当前仍在草图内，先退出（不合并）
            try:
                self.exit_sketch(merge=False)
            except Exception:
                pass
        except Exception:
            pass
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        _plane_ok = False
        for _attempt in range(2):
            try:
                if self.select_plane(base_plane):
                    _plane_ok = True
                    break
            except Exception as e:
                if _attempt == 1:
                    out["error"] = "选择基准面异常: %r" % (e,)
                    return out
            try:
                self.model.ClearSelection2(True)
            except Exception:
                pass
        if not _plane_ok:
            out["error"] = ("基准面 %r 选不中，无法建偏移面"
                            "（已清空选择并重试 2 次）" % (base_plane,))
            return out
        _CONSTRAINT_DISTANCE = 8
        try:
            feat = self.fm.InsertRefPlane(_CONSTRAINT_DISTANCE, off * MM,
                                          0, 0, 0, 0)
        except Exception as e:
            out["error"] = "InsertRefPlane 异常: %r" % (e,)
            return out
        if feat is None:
            out["error"] = "InsertRefPlane 返回 None（该偏移量可能不被接受）"
            return out
        try:
            out["name"] = str(feat.Name)
        except Exception:
            out["name"] = None
        # ── 【S-06】把特征对象一并带回 ────────────────────────────────────
        # 动态基准面的名字（"基准面1"/"Plane1"）不进入 begin_sketch 的别名表，
        #   按名选必然失败（实测）。调用方应直接用【对象】选中它再开草图。
        out["feature"] = feat
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        out["ok"] = True
        return out

    def begin_sketch_on_feature(self, feat, label="<基准面特征>"):
        """【S-06】在【任意基准面特征】上开草图（不依赖名字）。

        为什么需要：_make_offset_plane 生成的"基准面1"不在 select_plane 的
        别名表里，按名调用 begin_sketch 必失败。这里用 IFeature.Select2 直接
        选中对象，再 InsertSketch —— 与 begin_sketch 的主路径语义一致，
        但不走任何"猜名字"的环节。

        Returns: self
        """
        if feat is None:
            raise RuntimeError("begin_sketch_on_feature: feat 为 None")
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        _ok = False
        for _args in ((False, 0), (False, 1)):
            try:
                if feat.Select2(*_args):
                    _ok = True
                    break
            except Exception:
                continue
        if not _ok:
            try:
                feat.Select2(False)
                _ok = True
            except Exception:
                _ok = False
        if not _ok:
            raise RuntimeError("begin_sketch_on_feature: 无法选中特征 %r" % (label,))
        # == 【v2 回归复测 P1 修复】InsertSketch 后必须【重试 + 等待激活】==
        # 现场（复测报告 v2 第4.2节，修完选面后的下一层）：
        #   helix_gear 分段放样报
        #   「begin_sketch_on_feature: InsertSketch 后草图未激活」
        # 原因：InsertSketch 是异步的 —— SW 需要一点时间把草图上下文建起来；
        #   上一段刚 extrude 完就立刻建下一段草图，很容易拿到 ActiveSketch=None。
        #   旧实现只调一次、立刻断言，第 4 段必挂（前面的段恰好赶上）。
        # 现在：最多试 3 轮，每轮之间短暂等待，仍未激活才报错。
        _active = None
        for _try in range(3):
            try:
                self.skm.InsertSketch(True)
            except Exception as _e_is:
                if _try == 2:
                    raise RuntimeError(
                        "begin_sketch_on_feature: InsertSketch 异常 %r" % (_e_is,))
            try:
                _active = self.skm.ActiveSketch
            except Exception:
                _active = None
            if _active is not None:
                break
            # 给 SW 一点时间建上下文（实测 50~150 ms 足够）
            time.sleep(0.15 * (_try + 1))
        if _active is None:
            raise RuntimeError("begin_sketch_on_feature: InsertSketch 后草图未激活"
                               "（已重试 3 次）")
        try:
            self._finalize_new_sketch(label)
        except Exception:
            pass
        return self

    def _translate_solid(self, dx_mm=0.0, dy_mm=0.0, dz_mm=0.0, copy=False):
        """把当前零件里的【全部实体】整体平移 (dx, dy, dz)（单位 mm）。

        ── 【A-33】为什么需要它 ────────────────────────────────────────────
        create_sphere() 的半圆弧只能画在基准面（草图为 2D），球心必然落在
        该基准面内；要让 cz 真正生效，只能在特征建好后【平移实体】。
        草图里没有任何变量能表达"离面偏移"，这是几何约束，不是实现疏忽。

        ── 实现口径 ──────────────────────────────────────────────────────
        走 IModelDocExtension.InsertMoveCopyBody2（SW 2009+）：
            InsertMoveCopyBody2(Body, X, Y, Z, RotX, RotY, RotZ,
                                RotCenterX, RotCenterY, RotCenterZ,
                                Copy, NumCopies)
        · 平移量与旋转中心全部是【米】（SW 内部单位），这里按 mm 入参换算；
        · Copy=False 表示就地移动（不复制实体）。

        Returns: {ok: bool, moved_mm: [dx,dy,dz], bodies: n, error?: str}
        失败【绝不静默】—— 调用方拿 ok=False 必须告警，避免"以为搬过去了"。
        """
        out = {"ok": False, "moved_mm": [float(dx_mm), float(dy_mm), float(dz_mm)],
               "bodies": 0, "error": None}
        try:
            dx = float(dx_mm) * MM
            dy = float(dy_mm) * MM
            dz = float(dz_mm) * MM
        except Exception as e:
            out["error"] = "平移量非法: %r" % (e,)
            return out
        try:
            bodies = []
            try:
                _b = _com_get(self.model, "GetBodies2", 0, True)
                if _b:
                    bodies = list(_b)
            except Exception:
                bodies = []
            if not bodies:
                # 退回体枚举：GetBodies2 在个别版本/无实体时抛异常
                try:
                    _pb = _com_get(self.model, "GetPartBox")
                    if not _pb:
                        out["error"] = "零件内没有实体，无法平移"
                        return out
                except Exception:
                    pass
                out["error"] = ("无法枚举实体（GetBodies2 返回空）——"
                                "请确认零件已重建且有实体特征")
                return out
            out["bodies"] = len(bodies)
            _ext = _com_get(self.model, "Extension")
            _n_ok = 0
            _last_err = None
            for _bd in bodies:
                try:
                    r = _com_get(_ext, "InsertMoveCopyBody2", _bd,
                                 dx, dy, dz, 0.0, 0.0, 0.0,
                                 0.0, 0.0, 0.0, bool(copy), 0)
                    if r is not False:
                        _n_ok += 1
                except Exception as e:
                    _last_err = e
                    break
            if _n_ok:
                try:
                    self.rebuild()
                except Exception:
                    pass
                out["ok"] = True
                out["moved_bodies"] = _n_ok
                return out
            out["error"] = ("InsertMoveCopyBody2 失败: %r" % (_last_err,)
                            if _last_err else "InsertMoveCopyBody2 返回 False")
            return out
        except Exception as e:
            out["error"] = repr(e)
            return out

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

        # ── 【A-33 修复·算了不用】球心 z 必须真正生效 ──────────────────────
        # 原缺陷：oz 算出来之后【从未传给任何建模逻辑】—— 半圆弧画在 Front Plane
        #   上（草图平面 z=0），旋转出来的球体恒在 z=0，cz 参数被静默丢弃。
        #   症状："create_sphere(0,0,50,10)" 建出的球在原点高度，装配时靠配合
        #   硬拉过去；更糟的是 bbox/体积指纹仍然正常，没人发现球偏了。
        # 现在：先把球建在 z=0，再用【实体平移】把球心搬到 (0,0,cz)。
        #   平移量按"草图平面就是 z=0"计算，因此只需要 cz 一个分量。

        # ── 【S-06 修复·真机实测】必须真的先开草图 ────────────────────────
        # 上一版在插入 A-33 说明时把 self.begin_sketch("Front Plane") 这一行
        #   误删了，于是所有 CreateLine 都画在【不存在的活动草图】上 →
        #   草图其实没建出来 → revolve 报"未找到可用的草图"，
        #   而错误信息指向旋转特征，排查方向完全错（实测踩到）。
        #   这里恢复，并在画线前【断言草图真的激活了】，避免再次静默。
        #
        # ── 【S-06 二次修复】cz 改为【画在偏移基准面上】，不再依赖实体平移 ──
        # 实测：IModelDocExtension 上【没有 InsertMoveCopyBody2】（真机确证
        #   AttributeError），所以"建完再平移"这一版根本没生效，球仍恒在 z=0。
        # 现在：cz≠0 时先插入【偏移基准面】（InsertRefPlane 距离约束），
        #   球直接画在该面上 —— 球心 z 由几何本身决定，无需后置平移。
        _plane_feat = None
        if abs(float(cz)) > 1e-9:
            _rp = self._make_offset_plane("Front Plane", float(cz))
            if _rp.get("ok"):
                _plane_feat = _rp.get("feature")
            else:
                self._warn(
                    "create_sphere: 无法创建偏移基准面（z=%.3fmm）：%s —— "
                    "球将建在 z=0，请勿按已定位处理"
                    % (float(cz), _rp.get("error")))
        if _plane_feat is not None:
            self.begin_sketch_on_feature(_plane_feat, label="offset plane @ z=%.1f" % cz)
        else:
            self.begin_sketch("Front Plane")
        if self.skm.ActiveSketch is None:
            raise RuntimeError(
                "create_sphere: begin_sketch(Front Plane) 后草图未激活 —— "
                "无法继续画轮廓（r=%s, cz=%s）。" % (radius, cz))
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

        # 球体包围盒是 (D,D,D)——三个方向相等；而 revolve(verify=True)
        # 的形态校验只认"圆柱"=(D,D,L) 两等一大，会把球体误判为绕错轴。
        # 球体本就不是圆柱，这里跳过该形态校验（旋转轴仍由 centerline 决定）。
        feat = self.revolve(360, verify=False)
        if feat is None:
            raise RuntimeError("FeatureRevolve2 返回 None，球体创建失败")

        # 重建（Bug 修复: EditRebuild3 是方法，原实现漏了括号，重建实际从未执行）
        # 【Bug1 加固】原实现是 try/except: pass —— 静默吞掉重建异常，
        # 若重建真的失败，调用方误以为球体已就绪，后续特征会连锁报错且无从定位。
        # 现在把异常记录到 self.warnings 并打印告警（不抛出，保持建模流程可继续）。
        # ── 【噪告警修复】本绑定下 EditRebuild3 是【属性】（bool），不是方法 ──
        # 原实现只要 callable 判定失败就 _warn，于是每次建球都刷一条
        #   "EditRebuild3 不可调用（type=bool）" —— 实测三轮下来淹没其它真告警。
        # 正确做法：走已有的 self.rebuild()（它内部已兼容方法/属性两种形态），
        #   真失败才出声。
        try:
            self.rebuild()
        except Exception as _e_rb:
            self._warn("create_sphere: 重建失败 -> %r（特征已创建，建议调用方复核）" % (_e_rb,))
        except Exception as _e:
            self._warn("create_sphere: 重建失败 -> %r（特征已创建，建议调用方复核）" % (_e,))

        # 【S-06】cz 已在建模时由偏移基准面保证，此处不再平移。
        #   保留一次实测校验：球心与请求值不符就告警（不让错误几何静默出厂）。
        if abs(float(cz)) > 1e-9:
            try:
                _bb = self.model.GetPartBox(True)
                if _bb:
                    _zc = (float(_bb[2]) + float(_bb[5])) / 2.0 / MM
                    if abs(_zc - float(cz)) > max(0.5, abs(float(cz)) * 0.02):
                        self._warn(
                            "create_sphere: 球心实测 z=%.3fmm，与请求 %.3fmm 不符 —— "
                            "请复核偏移基准面是否生效" % (_zc, float(cz)))
            except Exception:
                pass
        return feat

    # ---------- 【Bug2】装配体：添加组件 ----------
    # SW2025 的 AddComponent5 是【7 个参数】：
    #   AddComponent5(CompName, ConfigOption, NewConfigName,
    #                 UseConfigForPartReferences, ExistingConfigName, X, Y, Z)
    # 参数含义与取值：
    #   CompName                      : 零件/子装配的完整路径（必须已存在于磁盘）
    #   ConfigOption                  : 配置选项（int），常用 0
    #                                   0 = 使用上次保存的配置（swAddComponentConfigOptions_CurrentSelectedConfig）
    #   NewConfigName                 : 若需新建配置则给名字；不需要传 ""
    #   UseConfigForPartReferences    : 是否为零件参考使用该配置（bool）
    #   ExistingConfigName            : 使用已有配置时的名字；不用则传 ""
    #   X, Y, Z                       : 插入位置（米！SW 内部单位是米，不是毫米）
    #
    # 【关键前提 · 实测踩坑】调用前该零件必须已被 SW 打开过一次
    #   （sw.OpenDoc6 打开即可）。否则 AddComponent5 会【返回 None 且不报错】，
    #   装配体里什么也没加 —— 这正是"看起来没报错但组件数为 0"的根因。
    #   本方法内部会自动预打开零件，调用方无需关心。

    def add_component(self, part_path, x=0.0, y=0.0, z=0.0,
                      config_option=0, new_config_name="",
                      use_config_for_refs=False, existing_config_name="",
                      units="mm"):
        """【统一封装】向装配体添加零件/子装配组件（SW2025 七参数签名）。

        Args:
            part_path: 零件（.SLDPRT）或子装配（.SLDASM）的绝对路径
            x, y, z:   插入位置。默认按【毫米】解释（内部换算成 SW 的米）

        ⚠️【BUG-D 提示】x/y/z 是【组件包围盒中心】的目标位置，不是：
             · 零件自身原点
             · 零件包围盒的角点
           若零件坐标系原点与其几何中心不重合，实际落点会与预期偏移。
           与草图 rect(cx, cy, w, h) 的"中心"语义一致，但注意后者是
           二维草图平面内的中心，前者是三维装配空间中的包围盒中心。
            config_option: 配置选项，默认 0
            new_config_name: 新建配置名，默认 ""
            use_config_for_refs: 布尔，默认 False
            existing_config_name: 已有配置名，默认 ""
            units: "mm"（默认）或 "m" —— 决定 x/y/z 的单位

        Returns:
            dict: {ok, component, position_mm, component_count,
                   coord_applied, actual_position_mm, warnings, error?}

        ── 【H 修复·第三轮】坐标必须读回校验 ──────────────────────────────
        测试反馈：坐标参数没生效（返回结构和两种写法已对，但组件仍落原点）。
        原因：AddComponent5 的 X/Y/Z 在部分版本/状态下被忽略，或坐标语义
          并非包围盒中心。
        现在本方法加入后【读回组件变换矩阵】取出实际位置并与期望比较：
          · coord_applied=True  -> 坐标确实生效
          · coord_applied=False -> 坐标被忽略（实际位置见 actual_position_mm），
                                    此时 warnings 里会给出明确提示
          · coord_applied=None  -> 未传坐标或无法读回（不代表成功）
        """
        out = {"ok": False, "component": None, "part_path": part_path,
               "warnings": []}
        if not part_path:
            out["error"] = "零件路径为空"
            return out
        if not os.path.isfile(part_path):
            out["error"] = "零件文件不存在: %s" % part_path
            return out

        # SW 内部长度单位是【米】，这里把 mm 换算过去
        _k = 0.001 if str(units).lower() == "mm" else 1.0
        px, py, pz = float(x) * _k, float(y) * _k, float(z) * _k
        out["position_mm"] = [float(x), float(y), float(z)]

        doc_type = 2 if part_path.lower().endswith(".sldasm") else 1  # 2=装配体 1=零件

        # ── 【关键前提】预打开零件，否则 AddComponent5 静默返回 None ──────
        try:
            errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            doc = self.sw.OpenDoc6(part_path, doc_type, 0, "", errs, warns)
            if doc is None:
                out["warnings"].append("预打开零件返回 None（errs=%s）" % errs.value)
        except Exception as e:
            out["warnings"].append("预打开零件异常: %r" % (e,))

        # ── 回到装配体（ActivateDoc3 签名因版本而异，失败不致命）──────────
        try:
            self.sw.ActivateDoc3(self.model.GetTitle, False, 0, 0)
        except Exception:
            try:
                self.sw.ActivateDoc2(self.model.GetTitle, False, 0)
            except Exception:
                pass

        # ── 【H 修复·第十一轮】专家建议的前置检查 ────────────────────────
        # 专家指出：若 AddComponent5 也失效，应先确认
        #   ① 零件文件真实存在（上面已查）
        #   ② 【装配体是否为活动文档】—— 否则 AddComponent 会静默无效
        # 这里显式确认并把活动文档切回装配体。
        _active_ok = False
        try:
            _title = self.model.GetTitle
            _active_ok = bool(self._activate_self())
            out["assembly_active"] = _active_ok
            if not _active_ok:
                out["warnings"].append(
                    "无法把装配体 %r 切为活动文档 —— AddComponent 可能静默无效。" % _title)
        except Exception as e:
            out["assembly_active"] = False
            out["warnings"].append("确认活动文档异常: %r" % (e,))

        # ── 多 API 回退链（专家建议：5 -> 4 -> 2）────────────────────────
        # 专家线索：SW2024 中文版 + pywin32 下 AddComponent4 可能无异常但
        #   返回 None；SW2025 情况类似甚至更糟。因此逐个尝试并校验结果。
        comp = None
        _api_tried = []
        _api_errors = []

        def _try_add(_api, _args):
            try:
                _fn = getattr(self.model, _api, None)
            except Exception as _e:
                _api_errors.append("%s: 取方法失败 %r" % (_api, _e))
                return None
            if _fn is None:
                _api_errors.append("%s: 方法不存在" % _api)
                return None
            try:
                _r = _fn(*_args)
                _api_tried.append(_api)
                if _r is None:
                    _api_errors.append("%s: 返回 None" % _api)
                return _r
            except Exception as _e:
                _api_errors.append("%s: %r" % (_api, _e))
                return None

        # ① AddComponent5（官方推荐，8 参数：含 X/Y/Z）
        comp = _try_add("AddComponent5", (
            part_path, int(config_option), str(new_config_name or ""),
            bool(use_config_for_refs), str(existing_config_name or ""),
            px, py, pz))

        # ② AddComponent4（专家线索：部分版本只有它可用）
        if comp is None:
            comp = _try_add("AddComponent4", (
                part_path, int(config_option), str(new_config_name or ""),
                bool(use_config_for_refs), str(existing_config_name or ""),
                px, py, pz))

        # ③ AddComponent2（旧版 4 参数）
        if comp is None:
            comp = _try_add("AddComponent2", (part_path, px, py, pz))

        # ④ AddComponent（最老版本，2 参数）
        if comp is None:
            comp = _try_add("AddComponent", (part_path, px, py, pz))

        out["api_tried"] = _api_tried
        out["api_errors"] = _api_errors
        if comp is None and not _api_tried:
            out["error"] = ("所有 AddComponent* API 均不可用: %s"
                            % (" | ".join(_api_errors),))
            self._warn("add_component: " + out["error"])
            return out
        if comp is None:
            out["warnings"].append(
                "所有 API 均返回 None（已试: %s）—— 组件可能未插入。"
                % (", ".join(_api_tried),))

        # ── 【H 修复·第三轮】坐标必须【读回校验】，不能假设生效 ──────────
        # 测试反馈：add_component 的坐标参数没生效（返回结构和两种写法已对）。
        #   原因：AddComponent5 的 X/Y/Z 在部分版本/状态下被忽略，
        #         或坐标语义是"零件原点落点"而非"包围盒中心"，
        #         写了坐标但组件仍落在原点。
        # 修复：加入后【读回组件变换矩阵】，取出实际位置与期望比较；
        #   不一致则尝试用 SetTransform/Move 纠正，仍不一致就在返回里
        #   明确标注 coord_applied=False，绝不假装坐标生效了。
        _coord_applied = None
        _actual_mm = None
        if comp is not None and (abs(x) > 1e-9 or abs(y) > 1e-9 or abs(z) > 1e-9):
            try:
                _mt = None
                for _a in (True, None):
                    try:
                        _mt = comp.GetTotalTransform(_a) if _a is not None \
                            else comp.GetTotalTransform()
                        if _mt is not None:
                            break
                    except Exception:
                        continue
                if _mt is not None:
                    # ── 【装配定位修复 R2】平移分量在索引 9/10/11（不是 3/7/11）──
                    # 读错索引 = 位置永远读成 [0,0,1] 之类 → 已放对的组件
                    # 也会被判成"坐标未生效"，进而误触发补救链。
                    _arr = _transform_array(_mt)
                    if _arr:
                        # 【装配定位修复 R2】平移分量在索引 9/10/11，
                        # 原来读 3/7/11 读到的是旋转子矩阵里的值。
                        _pos_raw = _position_from_transform_mm(_mt, k=_k)
                        _actual_mm = [round(float(v), 4) for v in _pos_raw]
                        _want = [float(x), float(y), float(z)]
                        _coord_applied = all(
                            abs(_actual_mm[i] - _want[i]) < 0.5 for i in range(3))
            except Exception as _e:
                out["warnings"].append("坐标读回失败: %r" % (_e,))
        out["coord_applied"] = _coord_applied
        out["actual_position_mm"] = _actual_mm

        # ── 【H 修复·第四轮】坐标被忽略时，自动尝试补救 ────────────────────
        # 测试部实测：coord_applied=False，position_mm=[0.2,0.1,0.05]
        #   但 actual_position_mm=[0,0,-4.95] —— AddComponent5 的 X/Y/Z 确实
        #   被 SW 忽略了（参数位对，但本版不生效）。
        # 结论：不能只靠 AddComponent5 的坐标参数。
        # 补救顺序（按可靠性）：
        #   ① 尝试用组件的 SetTransform 纠正（若本版有该方法）；
        #   ② 都不行就如实返回 coord_applied=False + 明确的替代建议，
        #      由调用方决定改用 add_mate。
        if _coord_applied is False and comp is not None:
            _fixed = False
            # ── 【Bug-30 修复】补救摆位：用正确的 MathTransform 构造方式 ─────
            # 原实现用 sw.CreateTransform（不存在）→ 补救必然失败。
            # 现在走 set_component_transform（内部用 IMathUtility.CreateTransform，
            # 并兜底 SetTransform/SetTransform2/MoveComponent 多版本路径）。
            try:
                _fix_res = self.set_component_transform(
                    comp, x, y, z, units=("mm" if _k == 0.001 else "m"))
                out["coord_fix_detail"] = _fix_res
                if _fix_res.get("ok"):
                    _fixed = True
                    out["coord_fix"] = _fix_res.get("method")
                    if _fix_res.get("after_mm"):
                        out["actual_position_mm"] = _fix_res["after_mm"]
            except Exception as _e2:
                out["warnings"].append("补救摆位失败: %r" % (_e2,))
            if _fixed:
                _coord_applied = True
                out["coord_applied"] = True

        if out.get("coord_applied") is False:
            out["warnings"].append(
                "坐标未生效：期望 %s mm，实际 %s mm —— "
                "AddComponent5 的 X/Y/Z 在本版 SolidWorks 被忽略，"
                "且 SetTransform 补救也未成功。"
                "【建议改用 add_mate 做装配定位】（工程上也更规范），"
                "或删除该组件后以正确坐标重新添加。"
                % ([x, y, z], _actual_mm))
            out["hint"] = ("坐标类参数在装配态不可靠 —— 这是实测结论。"
                           "定位请优先用 add_mate（配合/约束），"
                           "而不是依赖插入坐标。")

        # ── 校验组件真的加进去了（返回 None 也可能是失败）─────────────────
        n_comps_before = out.get("components_before")
        actual = 0
        try:
            comps = self.model.GetComponents(False)
            actual = len(comps) if comps else 0
        except Exception:
            pass
        out["component_count"] = actual
        out["component"] = comp

        # ══ 【装配定位修复 R2·核心】坐标没生效【绝不允许】报 ok:true ═══════
        # 事故原貌：51 件全部 ok:true / n_coord_applied=1 —— 上层统计"51 件
        #   全插入了"，于是继续去跑干涉检查、联锁验证、出图，直到最后才发现
        #   是【假装配】（全堆在原点）。根因之一就在这里：只要 comp 非空
        #   就无条件 ok=True，把"插进去了"冒充成"装配好了"。
        # 现在的契约（测试 test_regression_assembly_placement.py 钉死）：
        #   · 传了非零坐标 且 坐标读回校验失败  → ok=False + error + next_action
        #   · 坐标读不回来（coord_applied=None 但确实传了坐标） → 同样 ok=False
        #     （"验证不了"不等于"验证通过"，这是本仓库一贯的'绝不假装成功'）
        #   · 只有 coord_applied 为 True 才允许 ok=True
        _want_coord = (abs(float(x)) > 1e-9 or abs(float(y)) > 1e-9
                       or abs(float(z)) > 1e-9)
        if comp is not None or actual > 0:
            try:
                out["component_name"] = comp.Name2
            except Exception:
                pass
            if _want_coord and out.get("coord_applied") is not True:
                out["ok"] = False
                out["error"] = ("组件已插入但【坐标未验证通过】（coord_applied=%r）。"
                                "期望 %s mm，实际 %s mm —— 按假装配处理，拒绝上报成功。"
                                % (out.get("coord_applied"), [x, y, z], _actual_mm))
                out["assembly_ok"] = False
                out["fake_assembly_risk"] = True
                self._warn("add_component: " + out["error"])
            else:
                out["ok"] = True
        else:
            out["error"] = ("AddComponent5 未生效（返回 None 且装配体组件数为 0）。"
                            "最常见原因：零件从未被 SW 打开过，或路径含有 SW 无法解析的字符。"
                            "建议先单独 open 该零件确认可用。")
            self._warn("add_component: " + out["error"])

        # 重新计算质量属性，保证装配体质量即时可用
        try:
            self.model.ForceRebuild3(False)
        except Exception:
            pass
        # ── 【Bug-44 修复】失败时给出"可自救"的下一步，不再只回 coord_applied=False ──
        if out.get("coord_applied") is False:
            out["next_action"] = ("坐标未生效。请调用 "
                                  "place_components_by_coords([(组件名, x, y, z), ...]) "
                                  "批量按坐标摆位（内部优先用装配体级 "
                                  "TransformComponent，Bug-44 修复路径）。")
        return out

    def _resolve_component_by_name(self, name):
        """把【组件名字符串】解析成 IComponent2（解析不到返回 (None, 原因)）。

        ── 【P1 修复 · 2026-11-11】read_component_pose 字符串入参归一 ──────
        现场现象：总装脚本传 str(cid)（"<COMObject <unknown>>"）进
          read_component_pose → 12 件全 ok=false → 上层误判"假装配"。
          证据：test/20261111_131815_580E38FA/scripts/assemble_减速机_v2.py:68
        根因：该方法原先只接受 IComponent2，没有"名字 → 对象"的入口，
          与 verify_assembly_placement（本就接受名字）口径不一致。
        修复判据：先 model.GetComponentByName(name)（官方单参数 API，最快），
          失败再遍历 GetComponents(False) 按 Name2 精确匹配，最后退到
          Name 属性与【唯一前缀】匹配（"DSH_底座" → "DSH_底座-1"）。
          仍找不到就返回 (None, 原因) —— 由调用方给出结构化失败，绝不抛。
        Returns: (component|None, how:str)
        """
        _want = str(name or "").strip()
        if not _want:
            return None, "空名字"
        # ① 官方单参数 API（最快，且精确匹配 Name2）
        try:
            _c = self.model.GetComponentByName(_want)
            if _c is not None:
                return _c, "GetComponentByName"
        except Exception:
            pass
        # ② 遍历组件按 Name2 / Name 精确匹配，再退化到唯一前缀匹配
        _names = []
        try:
            _comps = self.model.GetComponents(False) or []
        except Exception:
            _comps = []
        try:
            _list = list(_comps) if isinstance(_comps, tuple) else (
                [_comps] if _comps else [])
        except Exception:
            _list = []
        for _c in _list:
            _n = self._component_display_name(_c)
            if _n is None:
                continue
            _names.append(_n)
            if _n == _want:
                return _c, "GetComponents/Name2"
        _pref = [(_n, _c) for _n, _c in
                 ((self._component_display_name(_c), _c) for _c in _list)
                 if _n and _n.startswith(_want)]
        if len(_pref) == 1:
            return _pref[0][1], "GetComponents/唯一前缀"
        if len(_pref) > 1:
            # 歧义必须明说，不许随便挑一个（挑错等于把验收判到别人头上）
            return None, ("前缀匹配到 %d 个组件（%s），请给完整 Name2"
                          % (len(_pref), "、".join(n for n, _ in _pref[:5])))
        return None, ("GetComponentByName 与 GetComponents 都找不到该名字；"
                      "现有组件: %s" % ("、".join(_names[:8]) or "无"))

    def verify_component_position(self, comp, expect_mm, tol_mm=0.5, units="mm"):
        """【Bug-44 修复】坐标验收自测：读回组件位置并与期望比对。

        台账明确要求："加一个自测用例，add_component 后读回坐标与期望一致
        才算修好，不能以'不抛异常'为通过标准。"

        本方法就是那个验收标准的可复用实现：
            r = m.add_component(part, 113, 0, 0)
            m.verify_component_position(r["component"], [113, 0, 0])
            # -> {"ok": True, "delta_mm": [0,0,0], ...}

        Returns: {ok, actual_mm, expect_mm, delta_mm, tol_mm, passed}
        """
        out = {"ok": False, "expect_mm": [float(v) for v in (expect_mm or [])],
               "tol_mm": float(tol_mm)}
        try:
            _pos = self._component_position_mm(comp, units=units)
            out["actual_mm"] = ([round(v, 4) for v in _pos] if _pos else None)
            if not _pos or len(out["expect_mm"]) < 3:
                out["error"] = "无法读回组件位置或期望坐标不足 3 维"
                return out
            _delta = [round(_pos[i] - out["expect_mm"][i], 4) for i in range(3)]
            out["delta_mm"] = _delta
            out["passed"] = all(abs(d) <= float(tol_mm) for d in _delta)
            out["ok"] = bool(out["passed"])
            if not out["passed"]:
                out["error"] = ("坐标不符：期望 %s，实际 %s（偏差 %s > 容差 %.2fmm）"
                                % (out["expect_mm"], out["actual_mm"], _delta, tol_mm))
            return out
        except Exception as e:
            out["error"] = "验收自测异常: %r" % (e,)
            return out

    def self_test_assembly_placement(self, part_path, target_mm=(100.0, 0.0, 0.0)):
        r"""【Bug-44 修复】装配摆位自测（验收标准）。

        台账要求："加一个自测用例，add_component 后读回坐标与期望一致才算修好。"
        本方法把该验收流程做成一条命令，便于每次改动后回归：

            m = swapi.new_assembly()
            m.self_test_assembly_placement(r"...\DSH_车架底板.SLDPRT", (113, 0, 0))
            # -> {"ok": True/False, "steps": {...}}

        步骤：
          ① add_component(path, x, y, z)
          ② 读回坐标，比对期望
          ③ 若不符 → 尝试 place_components_by_coords 补救
          ④ 再次读回，给出最终结论

        Returns: {ok, target_mm, add, placed, final, conclusion}
        """
        out = {"ok": False, "target_mm": [float(v) for v in target_mm], "steps": {}}
        try:
            x, y, z = [float(v) for v in target_mm]
            r1 = self.add_component(part_path, x, y, z)
            out["steps"]["add_component"] = {
                "ok": r1.get("ok"), "coord_applied": r1.get("coord_applied"),
                "actual_position_mm": r1.get("actual_position_mm")}
            comp = r1.get("component")
            if comp is None:
                out["conclusion"] = "组件未插入 —— 先确认零件可单独打开"
                return out
            v1 = self.verify_component_position(comp, [x, y, z])
            out["steps"]["verify_after_add"] = v1
            if v1.get("ok"):
                out.update({"ok": True, "final": v1.get("actual_mm"),
                            "conclusion": "add_component 坐标已生效（验收通过）"})
                return out
            # 补救：批量按坐标摆位（内部优先 TransformComponent）
            try:
                _name = None
                for _a in ("Name2", "Name"):
                    try:
                        _v = getattr(comp, _a)
                        _name = str(_v() if callable(_v) else _v)
                        if _name:
                            break
                    except Exception:
                        continue
                r2 = self.place_components_by_coords([(_name or comp, x, y, z)])
                out["steps"]["place_by_coords"] = r2
            except Exception as _e2:
                out["steps"]["place_by_coords"] = {"ok": False, "error": repr(_e2)}
            v2 = self.verify_component_position(comp, [x, y, z])
            out["steps"]["verify_after_place"] = v2
            out["final"] = v2.get("actual_mm")
            out["ok"] = bool(v2.get("ok"))
            out["conclusion"] = ("补救后坐标已生效（验收通过）" if v2.get("ok")
                                 else "add_component 与 TransformComponent 均未生效 —— "
                                      "本版 SW 需改用 add_mate 定位（Bug-44）")
            return out
        except Exception as e:
            out["error"] = "装配自测异常: %r" % (e,)
            return out


    # ══ 【装配定位修复 R2 · 2026-10-31】批量插入 + 逐件断言 + 假装配判定 ══
    # 事故（2026-10-30 四工位转台，15 小时长跑）：
    #   n_planned=51 / n_ok=51 / n_coord_applied=1 —— 51 件全部"插入成功"，
    #   只有 1 件真正带坐标 → 764KB 的 .SLDASM 是【假装配】，
    #   干涉检查 / 联锁验证 / 12 张工程图全部作废。
    # 本组方法的契约：
    #   ① 只要传了坐标，就必须【逐件读回位姿】并判定是否真的落在目标上；
    #   ② 读不回来 = 未验证 = 不算通过（绝不假装成功）；
    #   ③ 汇总里给出 assembly_ok / fake_assembly_risk，让上层一眼看出
    #      "插进去了" ≠ "装配好了"。

    ASSEMBLY_POS_TOL_MM = 0.5      # 位置判定容差（mm）——与本文件既有口径一致
    ASSEMBLY_ROT_TOL = 1e-3        # 旋转偏离单位阵的判定阈值（纯平移装配）

    def _component_display_name(self, comp):
        """取组件名（Name2 优先），取不到返回 None —— 只读，绝不猜。"""
        for _a in ("Name2", "Name"):
            try:
                v = getattr(comp, _a)
                if callable(v):
                    v = v()
                if v:
                    return str(v)
            except Exception:
                continue
        return None

    def read_component_pose(self, comp, units="mm", tol_mm=None):
        """读回单个组件的【位姿】并给出结构化判定（装配定位验收的唯一入口）。

        ── 【装配定位修复 R2】为什么必须单独有这个方法 ─────────────────────
        事故复盘里最刺眼的一条：51 件组件"全部成功"，却只有 1 件带坐标。
        原因是没有任何一处把"插入了"和"装配好了"区分开 —— 只要
        IComponent2 拿得到就 ok。本方法把位姿读回做成可断言的事实：
          · source      ：位姿是从哪里读到的（GetTotalTransform / Transform2）
          · position_mm ：平移分量（IMathTransform.ArrayData 索引 9/10/11）
          · rotation_ok ：是否≈单位阵（本仓库装配定位全是纯平移，没有旋转输入）
          · verified    ：位置读回成功 且 落在期望值容差内
        读不回来（GetTotalTransform 抛异常 / ArrayData 读不出）→ verified=False
        且 position_mm=None —— 调用方【不得】把它当成通过。

        Args:
            comp: IComponent2，或【组件名字符串】（也接受 {"name": ...} /
                {"component": ...} 这类字典，便于总装脚本直传配置项）。
            units: "mm"（默认）或 "m"
            tol_mm: 位置容差；不传则用类常量 ASSEMBLY_POS_TOL_MM
        Returns:
            {ok, verified, position_mm, rotation, rotation_ok, source,
             resolved_by?, error?, hint?}
        """
        tol = float(self.ASSEMBLY_POS_TOL_MM if tol_mm is None else tol_mm)
        out = {"ok": False, "verified": False, "position_mm": None,
               "rotation": None, "rotation_ok": None, "source": None,
               "tol_mm": tol, "name": None}
        # ══ 【P1 修复 · 2026-11-11】参数归一：接受组件名字符串 ══════════════
        # 现场现象：总装脚本把 str(cid) 传进来（cid 是 IComponent2，str() 得到
        #   "<COMObject <unknown>>"），于是下面 GetTotalTransform / Transform2
        #   两个分支全部抛异常 → 12 件【全 ok=false】→ 上层误判成"假装配/
        #   组件全堆在原点"，把一次正常的装配判死。
        #   证据：test\20261111_131815_580E38FA\scripts\assemble_减速机_v2.py:68
        # 根因：read_component_pose 的形参只接受 IComponent2 对象，对字符串
        #   直接去调 COM 方法，必然 AttributeError/类型不匹配；而本库别处
        #   （verify_assembly_placement 的 expectations）本来就允许"组件名或
        #   组件对象"，两处口径不一致。
        # 修复判据：入口处做一次归一 —— 字符串/字典先用 GetComponentByName
        #   解析，解析不到再在 GetComponents 里按 Name2 匹配，仍拿不到就返回
        #   【结构化失败】（ok:false + error + hint），绝不抛异常、绝不猜。
        #   ⚠️ 矩阵索引 9/10/11 是对的（_position_from_transform_mm, 5136），
        #      本次不动。
        if isinstance(comp, dict):
            _spec = comp.get("component", comp.get("name"))
            if _spec is None:
                out["error"] = "组件字典缺少 component/name 字段"
                out["hint"] = "传 {'component': 'DSH_底座-1'} 或直接用组件名。"
                return out
            comp = _spec
        if isinstance(comp, str):
            _want = comp.strip()
            out["requested_name"] = _want
            # 防呆：str(IComponent2) 得到的是 "<COMObject ...>"，永远匹配不到
            if (not _want) or _want.startswith("<") or "COMObject" in _want:
                out["error"] = ("传入的是对象字符串化结果（%r），不是组件名 —— "
                                "无法解析" % (_want[:60],))
                out["hint"] = ("请传 IComponent2 对象，或传 cid.Name2；"
                               "不要传 str(cid)。")
                return out
            _resolved, _how = self._resolve_component_by_name(_want)
            if _resolved is None:
                out["error"] = "按名称找不到组件: %s" % _want
                out["hint"] = ("用 list_components() 查看可用组件名；"
                               "组件名通常是 'DSH_底座-1' 这样的 Name2。"
                               "解析明细：%s" % (_how,))
                out["name"] = _want
                return out
            comp = _resolved
            out["resolved_by"] = _how
            out["name"] = _want
        if comp is None:
            out["error"] = "组件对象为空"
            return out
        if out.get("name") is None:
            out["name"] = self._component_display_name(comp)
        _k = 0.001 if str(units).lower() == "mm" else 1.0
        # 两条读回路径：① GetTotalTransform(True)（类型库确认，优先）
        #               ② Transform2 属性（类型库确认可读）
        mt = None
        for _how, _fn in (("GetTotalTransform", lambda: comp.GetTotalTransform(True)),
                          ("Transform2", lambda: comp.Transform2)):
            try:
                mt = _fn()
            except Exception as _e:
                out.setdefault("read_errors", []).append("%s: %r" % (_how, _e))
                mt = None
            if mt is not None:
                out["source"] = _how
                break
        if mt is None:
            out["error"] = "位姿读不回来（GetTotalTransform / Transform2 均失败）—— 无法验证"
            return out
        pos = _position_from_transform_mm(mt, k=_k)
        if pos is None:
            out["error"] = ("MathTransform.ArrayData 读不出 16 元数组 —— 无法验证"
                            "（%s）" % SW_MATRIX_LAYOUT)
            return out
        out["position_mm"] = [round(float(v), 4) for v in pos]
        rot = _rotation_from_transform(mt)
        out["rotation"] = ([round(float(v), 6) for v in rot] if rot else None)
        if rot is not None:
            # 纯平移 → 旋转子矩阵应为单位阵
            _ident = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            out["rotation_ok"] = all(
                abs(rot[i] - _ident[i]) <= self.ASSEMBLY_ROT_TOL for i in range(9))
        out["ok"] = True
        # verified 的语义：位姿【读回成功】，调用方可以据此断言（而不是"位置对"）。
        # "位置对不对"由 verify_assembly_placement / 调用方比对期望值决定。
        out["verified"] = True
        return out

    def verify_assembly_placement(self, expectations,
                                  tol_mm=None, units="mm", auto_fix=False):
        """【装配定位修复 R2·核心】逐件断言装配位姿，判出【假装配】。

        事故原貌：n_planned=51 / n_ok=51 / n_coord_applied=1 —— 上层只统计
        "插入成功数"，于是把"全堆在原点"的 .SLDASM 当成了已完成的装配，
        一路跑到干涉检查和出图才炸。本方法就是那道【验收闸门】：

            r = m.verify_assembly_placement([
                    ("DSH_底座-1", 0, 0, 20),
                    ("DSH_立柱-1", 0, 0, 125),
                ])
            if not r["assembly_ok"]:
                raise RuntimeError(r["verdict"])     # 绝不许继续出图

        判定口径（三条同时成立才 assembly_ok=True）：
          ① n_verified == n_expected：每一件都真的读回了位姿；
          ② 每一件 delta 都在容差内（默认 ASSEMBLY_POS_TOL_MM）；
          ③ 没有旋转异常（本仓库装配定位是纯平移；组件被配合/拖动改过
             位姿会让 rotation_ok=False，此时坐标对上也只算"可疑"）。
        任何一条不满足 → assembly_ok=False，并给出 fake_assembly_risk 与
        verdict 文案（"假装配"），绝不返回含糊的 ok。

        Args:
            expectations: [(组件名或组件对象, x, y, z), ...]（mm）
                组件名按 Name2 精确匹配，其次按前缀匹配（"DSH_底座-1"）。
            tol_mm: 位置容差（mm），默认 ASSEMBLY_POS_TOL_MM
            units: "mm"/"m"
            auto_fix: True 时对未达标的组件尝试 set_component_transform
                补救，并在补救后【重新读回】再判定（仍以读回结果为准）。
        Returns:
            {ok, assembly_ok, fake_assembly_risk, verdict,
             n_expected, n_verified, n_ok, n_mismatch, n_unreadable,
             items:[...], missing:[...], warnings:[...]}
        """
        tol = float(self.ASSEMBLY_POS_TOL_MM if tol_mm is None else tol_mm)
        out = {"ok": False, "assembly_ok": False, "fake_assembly_risk": True,
               "n_expected": 0, "n_verified": 0, "n_ok": 0,
               "n_mismatch": 0, "n_unreadable": 0,
               "items": [], "missing": [], "warnings": [],
               "tol_mm": tol, "verdict": ""}
        items = list(expectations or [])
        out["n_expected"] = len(items)
        if not items:
            out["error"] = "expectations 为空 —— 没有可验收的组件（空表白名单，不算通过）"
            out["verdict"] = "未验收：没有给期望坐标"
            return out
        try:
            comps = self.model.GetComponents(False) or []
            clist = _components_list(comps)
        except Exception as _e:
            out["error"] = "读不到装配体组件列表: %r" % (_e,)
            out["verdict"] = "未验收：装配体组件列表读不出来"
            return out

        def _find(target):
            if target is None:
                return None
            if not isinstance(target, str):
                return target          # 调用方直接给了组件对象
            _t = str(target)
            for c in clist:            # ① 精确名
                if self._component_display_name(c) == _t:
                    return c
            for c in clist:            # ② 前缀名（"DSH_底座" → "DSH_底座-1"）
                _n = self._component_display_name(c) or ""
                if _n.startswith(_t):
                    return c
            return None

        for item in items:
            rec = {"expect_mm": None, "ok": False, "verified": False}
            try:
                target, tx, ty, tz = item[0], item[1], item[2], item[3]
            except Exception:
                rec["error"] = "格式应为 (组件名或组件对象, x, y, z)"
                out["items"].append(rec)
                out["n_unreadable"] += 1
                continue
            want = [float(tx), float(ty), float(tz)]
            rec["expect_mm"] = want
            rec["name"] = target if isinstance(target, str) else \
                self._component_display_name(target)
            comp = _find(target)
            if comp is None:
                rec["error"] = "未找到该组件"
                out["missing"].append(rec["name"])
                out["items"].append(rec)
                out["n_unreadable"] += 1
                continue
            pose = self.read_component_pose(comp, units=units, tol_mm=tol)
            if auto_fix and pose.get("position_mm") is not None:
                _p = pose["position_mm"]
                if any(abs(_p[i] - want[i]) > tol for i in range(3)):
                    _fix = self.set_component_transform(
                        comp, want[0], want[1], want[2], units=units)
                    rec["fix"] = {"ok": _fix.get("ok"),
                                  "method": _fix.get("method"),
                                  "error": _fix.get("error")}
                    # 补救后【必须重新读回】—— 以读回结果为准
                    pose = self.read_component_pose(comp, units=units, tol_mm=tol)
            rec["position_mm"] = pose.get("position_mm")
            rec["rotation_ok"] = pose.get("rotation_ok")
            rec["source"] = pose.get("source")
            if pose.get("position_mm") is None:
                rec["verified"] = False
                rec["error"] = pose.get("error") or "位姿读不回来"
                out["n_unreadable"] += 1
                out["items"].append(rec)
                continue
            out["n_verified"] += 1
            _p = pose["position_mm"]
            rec["delta_mm"] = [round(_p[i] - want[i], 4) for i in range(3)]
            rec["verified"] = True
            _pos_ok = all(abs(rec["delta_mm"][i]) <= tol for i in range(3))
            _rot_ok = pose.get("rotation_ok")
            rec["pos_ok"] = bool(_pos_ok)
            rec["ok"] = bool(_pos_ok and (_rot_ok is not False))
            if _pos_ok and _rot_ok is False:
                rec["error"] = ("位置在容差内，但旋转不是单位阵 —— 组件位姿被"
                                "配合/拖动改过，按【可疑】处理（不计通过）")
            elif not _pos_ok:
                rec["error"] = ("坐标未生效：期望 %s，实际 %s（偏差 %s > 容差 %.2fmm）"
                                % (want, _p, rec["delta_mm"], tol))
            if rec["ok"]:
                out["n_ok"] += 1
            else:
                out["n_mismatch"] += 1
            out["items"].append(rec)

        out["assembly_ok"] = bool(
            out["n_expected"] > 0
            and out["n_verified"] == out["n_expected"]
            and out["n_ok"] == out["n_expected"])
        out["ok"] = out["assembly_ok"]
        out["fake_assembly_risk"] = not out["assembly_ok"]
        if out["assembly_ok"]:
            out["verdict"] = ("装配位姿验收通过：%d/%d 件逐件读回并落在容差内"
                              % (out["n_ok"], out["n_expected"]))
        else:
            out["verdict"] = (
                "【假装配】%d 件期望 / %d 件读回成功 / %d 件位置达标 —— "
                "插入成功不等于装配成功，禁止用此装配体做干涉检查或出图。"
                % (out["n_expected"], out["n_verified"], out["n_ok"]))
        return out

    def add_components_batch(self, part_paths, coords=None, units="mm",
                             verify=True, tol_mm=None):
        """【装配定位修复 R2】一次性批量插入组件，每件带【显式变换矩阵】。

        ── 为什么需要它（网页依据）────────────────────────────────────────
        CadBooster《Understanding math and MathTransform in the SOLIDWORKS API》
        https://cadbooster.com/understanding-math-and-mathtransform-in-the-solidworks-api/
        「When you add multiple components, you do get the option of passing the
          orientation of each component. But you have to create a single array
          with 16 values for each component:
          Transforms contains an array of [(Names count) x 16] doubles.」
        【类型库】IAssemblyDoc.AddComponents(Names, Transforms) 与
          AddComponents3(Names, Transforms, CoordinateSystemNames) 确实存在
          （参数均为 12=VARIANT 数组）。
        与单件 AddComponent5(..., X, Y, Z) 的区别：AddComponents 的位置
          【不是散装 XYZ 参数】，而是每件一个完整的 IMathTransform 矩阵 ——
          这正是本次事故里"X/Y/Z 被忽略"的另一条独立解法：换掉参数形态。

        矩阵布局见 SW_MATRIX_LAYOUT（平移在 9/10/11，单位米）。

        Args:
            part_paths: 零件/子装配路径列表
            coords: [(x, y, z), ...]（mm）；None 表示全部放原点
            units: "mm"/"m"
            verify: True 时插入后逐件读回位姿并比对（默认 True，强烈建议）
            tol_mm: 位置容差
        Returns:
            {ok, inserted, n_planned, n_inserted, n_verified, n_ok,
             positions_ok, placement, warnings}
        """
        tol = float(self.ASSEMBLY_POS_TOL_MM if tol_mm is None else tol_mm)
        _k = 0.001 if str(units).lower() == "mm" else 1.0
        out = {"ok": False, "inserted": [], "n_planned": 0, "n_inserted": 0,
               "n_verified": 0, "n_ok": 0, "positions_ok": False,
               "warnings": [], "placement": None}
        names = [str(p) for p in (part_paths or [])]
        out["n_planned"] = len(names)
        if not names:
            out["error"] = "part_paths 为空"
            return out
        _coords = list(coords) if coords else [[0.0, 0.0, 0.0]] * len(names)
        if len(_coords) != len(names):
            out["error"] = ("coords 数量(%d)与 part_paths(%d)不一致 —— "
                            "不允许靠默认值补齐（会悄悄把零件放到原点）"
                            % (len(_coords), len(names)))
            return out
        # 逐件构造 16 元矩阵（官方布局）
        transforms = []
        for _c in _coords:
            transforms.extend(_translation_matrix_m16(_c[0], _c[1], _c[2], _k))
        self._activate_self()
        ret = None
        _api_used = None
        # 【类型库】IAssemblyDoc 里确认存在的批量入口只有三个：
        #   AddComponents(Names, Transforms)
        #   AddComponents3(Names, Transforms, CoordinateSystemNames)
        #   IAddComponents2(Count, Names, Transforms)
        for _api, _args in (
                ("AddComponents3", (names, transforms, [""] * len(names))),
                ("AddComponents", (names, transforms)),
                ("IAddComponents2", (len(names), names, transforms))):
            _fn = getattr(self.model, _api, None)
            if _fn is None:
                out["warnings"].append("%s: 方法不存在" % _api)
                continue
            try:
                ret = _fn(*_args)
            except Exception as _e:
                out["warnings"].append("%s: %r" % (_api, _e))
                ret = None
            if ret is not None:
                _api_used = _api
                break
        out["api"] = _api_used
        if ret is None:
            out["error"] = ("AddComponents* 全部失败（本版可能不支持批量插入）—— "
                            "请回退到 add_component 逐件插入 + verify_assembly_placement 验收。")
            return out
        # 【装配定位修复 R2】与 verify_assembly_placement 同一个坑：
        # AddComponents 在真 COM 下返回 VARIANT 数组（tuple），但某些封装/假
        # 对象会返回 list —— 老的 "isinstance(tuple) else [ret]" 写法会把 list
        # 再包一层，组件名全部拿不到（inserted=[None]）。统一走 _components_list。
        _lst = _components_list(ret)
        out["n_inserted"] = len(_lst)
        out["inserted"] = [self._component_display_name(c) for c in _lst]

        if not verify:
            out["ok"] = out["n_inserted"] == out["n_planned"]
            out["warnings"].append("verify=False：未读回位姿，位姿真实性【未验证】")
            return out
        # ── 逐件断言（本方法存在的意义所在）────────────────────────────
        expects = []
        for _i, _p in enumerate(names):
            _name = (out["inserted"][_i] if _i < len(out["inserted"])
                     else _p)
            if _name is None:
                _name = _p
            expects.append((_name, _coords[_i][0], _coords[_i][1], _coords[_i][2]))
        placement = self.verify_assembly_placement(
            expects, tol_mm=tol, units=units)
        out["placement"] = placement
        out["n_verified"] = placement.get("n_verified")
        out["n_ok"] = placement.get("n_ok")
        out["positions_ok"] = bool(placement.get("assembly_ok"))
        out["ok"] = bool(out["positions_ok"]
                         and out["n_inserted"] == out["n_planned"])
        if not out["positions_ok"]:
            out["error"] = placement.get("verdict")
        return out

    def detect_fake_assembly(self, expectations=None, tol_mm=None, units="mm"):
        """【装配定位修复 R2】假装配探测器：装配体里到底有几件真的被定位过。

        即使调用方【没有】记录设计坐标，也能判"这份 .SLDASM 是不是假装配"：
          · 逐件读回位姿（读不回来直接计入 unreadable）；
          · 统计"落在原点"的件数 —— 事故里 51 件有 50 件堆在原点；
          · 统计旋转非单位阵的件数（位姿被别处改过）。
        expectations 给了就顺带做严格验收（见 verify_assembly_placement）。

        Returns:
            {ok, n_components, n_readable, n_unreadable,
             n_at_origin, at_origin, fake_assembly, verdict,
             placement?(当 expectations 给出时), rotation_anomalies}
        """
        tol = float(self.ASSEMBLY_POS_TOL_MM if tol_mm is None else tol_mm)
        out = {"ok": False, "n_components": 0, "n_readable": 0,
               "n_unreadable": 0, "n_at_origin": 0, "at_origin": [],
               "rotation_anomalies": [], "fake_assembly": True,
               "tol_mm": tol, "verdict": ""}
        try:
            comps = self.model.GetComponents(False) or []
            clist = _components_list(comps)
        except Exception as _e:
            out["error"] = "读不到装配体组件列表: %r" % (_e,)
            out["verdict"] = "未探测：组件列表读不出来"
            return out
        out["n_components"] = len(clist)
        if not clist:
            out["error"] = "装配体里没有任何组件"
            out["verdict"] = "空装配体 —— 谈不上假装配，但也没有内容可验收"
            return out
        for c in clist:
            pose = self.read_component_pose(c, units=units, tol_mm=tol)
            if pose.get("position_mm") is None:
                out["n_unreadable"] += 1
                continue
            out["n_readable"] += 1
            _p = pose["position_mm"]
            if all(abs(_p[i]) <= tol for i in range(3)):
                out["n_at_origin"] += 1
                out["at_origin"].append(pose.get("name"))
            if pose.get("rotation_ok") is False:
                out["rotation_anomalies"].append(pose.get("name"))
        # 判定：只要【所有能读到的组件都在原点】且件数 > 1 → 典型假装配
        _all_origin = (out["n_readable"] > 0
                       and out["n_at_origin"] == out["n_readable"])
        _mostly_origin = (out["n_readable"] > 1
                          and out["n_at_origin"] >= out["n_readable"] - 1)
        out["fake_assembly"] = bool(
            out["n_readable"] == 0 or _all_origin or _mostly_origin
            or out["n_unreadable"] > 0)
        if expectations:
            out["placement"] = self.verify_assembly_placement(
                expectations, tol_mm=tol, units=units)
        if out["n_readable"] == 0:
            out["verdict"] = "位姿全部读不回来 —— 无法证明组件被定位过，按假装配处理"
        elif out["fake_assembly"]:
            out["verdict"] = (
                "【假装配】%d/%d 件落在原点（%d 件位姿读不回）—— "
                "插入成功 ≠ 装配成功，禁止用此装配体做干涉检查或出图。"
                % (out["n_at_origin"], out["n_components"], out["n_unreadable"]))
        else:
            out["verdict"] = ("组件位姿分布正常：%d 件读回，%d 件在原点，"
                              "%d 件读不回" % (out["n_readable"],
                                               out["n_at_origin"],
                                               out["n_unreadable"]))
        out["ok"] = not out["fake_assembly"]
        return out

    # ══ 【C8 修复】装配 API（Wave2 总装必需）═══════════════════════════
    # 测试反馈：swapi 无 new_assembly()、无 AddMate/ToolsCheckInterference2 封装，
    #   总装只能写裸 COM，极易踩参数签名坑。以下方法把常用装配操作封装好。

    def _activate_self(self):
        """把活动文档切回本 SWModel 对应的文档（装配体内操作前必做）。"""
        try:
            self.sw.ActivateDoc3(self.model.GetTitle, False, 0, 0)
            return True
        except Exception:
            try:
                self.sw.ActivateDoc2(self.model.GetTitle, False, 0)
                return True
            except Exception:
                return False

    def place_component_by_mate(self, comp_name, ref_plane="Front Plane",
                               distance_mm=0.0, mate_type="distance"):
        """【H 修复·第五轮】用【配合】定位组件 —— 实测唯一可行的方式。

        背景（测试部实测结论）：
          · add_component 的 X/Y/Z 被 SW 忽略（coord_applied=False）；
          · 组件对象【没有 SetTransform 方法】（coord_fix=null）；
          · 即"插入坐标"与"事后摆位"两条路都走不通。

        因此正确定位方式是【装配配合】—— 这在工程上也更规范：
          装配体本就该用约束/配合定义位置，而不是硬塞坐标。

        本方法：把组件与某个基准面（或另一组件）建立配合，
          通过修改配合值来移动它。

        Args:
            comp_name: 组件名（如 "DSH_底座-1"）
            ref_plane: 参考基准面名，默认 Front Plane
            distance_mm: 距离配合的数值（mm）
            mate_type: "distance"(距离) / "coincident"(重合)

        Returns: {ok, mate_name?, error?, hint?}
        """
        out = {"ok": False}
        try:
            self._activate_self()
            # 选中组件与参考面，再建立配合
            _ok_comp = False
            try:
                comps = self.model.GetComponents(False) or []
                for c in comps:
                    try:
                        if str(c.Name2) == str(comp_name):
                            # 【测试部反馈修复】Select4 的 Data 必须是 SelectData
                            _sd_c = self._make_select_data(0)
                            if _sd_c is not None:
                                c.Select4(False, _sd_c)
                            else:
                                c.Select2(False, 0)
                            _ok_comp = True
                            break
                    except Exception:
                        continue
            except Exception:
                _ok_comp = False
            if not _ok_comp:
                out["error"] = "未找到组件: %s" % comp_name
                out["hint"] = "用 list_components() 查看可用组件名。"
                return out
            # 加选参考基准面（装配链路：早期绑定下 Callout 必须传 None）
            self._select_by_id_asm(ref_plane, "PLANE", 0, 0, 0, True, 0)
            # 建立配合
            # ── 【BUG-11 修复】原来这里同样调 AddMate5 的 8 参旧签名，在
            # SW2025 必然报"类型不匹配"；统一改走 _add_mate_early（内部按
            # 专用 API → AddMate5 全参 → CreateMate → 晚期旧签名 逐级回退）。
            _mt, _mt_err = self._parse_mate_type(mate_type)
            if _mt is None:
                _mt = 5
            mate = None
            last = None
            mate, _api, last = self._add_mate_early(
                _mt, align=0, flip=False,
                distance_mm=(distance_mm if str(mate_type) == "distance"
                             else None))
            if mate is not None:
                out["mate_api"] = _api
            if mate is None:
                out["error"] = "配合创建失败: %r" % (last,)
                out["hint"] = ("确认组件与参考面都已选中；"
                               "也可在 SW 界面手动配合后，用本库继续其他操作。")
                return out
            out["ok"] = True
            try:
                out["mate_name"] = mate.Name
            except Exception:
                pass
            # ══ 【配合卡死修复 · 2026-11-11】此处同样【不再】rebuild() ═══════
            # 现场现象：place_component_by_mate 连续摆 30+ 件时偶发 600s 卡死。
            # 根因：每建一个配合就全量重建一次装配体，重建代价随组件数超线性增长。
            # 修复判据：配合链路不主动重建；需要时由调用方显式 rebuild()。
            out["rebuild_skipped"] = "配合链路不主动重建（防 600s 卡死）"
            return out
        except Exception as e:
            out["error"] = "place_component_by_mate 异常: %r" % (e,)
            return out

    # ══════════════════════════════════════════════════════════════════════
    #  【子任务 B · 装配/配合】早期绑定增强块（BUG-11 / BUG-12 / BUG-14）
    # ----------------------------------------------------------------------
    #  这里的模块级常量与辅助函数服务于本文件下面的装配/配合代码。
    #  基础设施（get_early_module / early / is_early_available / SWModel.early）
    #  由【子任务 A】提供（本文件 ~2455 行，已落盘），本块【不重复实现】它，
    #  只补充"装配配合专属"的三样东西：
    #    ① 正确的 swMateType_e 枚举表 —— 原 MATE_TYPES 表是错的（把
    #       'concentric' 映射成 4，而 4 在 SW 里是 swMateTANGENT；'lock' 映射成 9，
    #       而 9 是 swMateCAMFOLLOWER，真锁是 16）。错表 + 早期绑定同样建错配合，
    #       必须一并修正；
    #    ② 组件 → 实体/文档的访问器（BUG-12：GetModelDoc2 / GetBody2 在晚期
    #       绑定下恒为 None，必须 early(component, 'IComponent2') 后再调；
    #       而 GetBody 不存在，只能 GetBodies() 再 list()）；
    #    ③ 装配链路的 SelectByID2 调用器（早期绑定下 Callout 只能传 None，
    #       传 VARIANT / pythoncom.Missing 都失败；晚期绑定仍走原 _empty）。
    #
    #  枚举值来源：【本机实测】SW 2025 SP5.0 的 swconst.tlb 里 swMateType_e /
    #  swMateAlign_e / swAddMateError_e / swBodyType_e（用 comtypes.GetModule
    #  读出全部成员）。这几张表在 SW 2018~2025 上稳定（SW API 常量表绝大多数
    #  多年未变），且失败一律回退晚期绑定，因此不会影响老版本。
    # ══════════════════════════════════════════════════════════════════════

    # swMateType_e（来源：swconst.tlb 实测）
    _MATE_TYPE_E = {
        "coincident": 0,        # swMateCOINCIDENT
        "concentric": 1,        # swMateCONCENTRIC
        "perpendicular": 2,     # swMatePERPENDICULAR
        "parallel": 3,          # swMatePARALLEL
        "tangent": 4,           # swMateTANGENT
        "distance": 5,          # swMateDISTANCE
        "angle": 6,             # swMateANGLE
        "unknown": 7,
        "symmetric": 8,         # swMateSYMMETRIC
        "camfollower": 9,       # swMateCAMFOLLOWER
        "gear": 10,             # swMateGEAR
        "width": 11,            # swMateWIDTH
        "locktosetch": 12,
        "rackpinion": 13,       # swMateRACKPINION
        "path": 15,             # swMatePATH
        "lock": 16,             # swMateLOCK ← 真正的"锁定配合"
        "screw": 17,            # swMateSCREW
        "linearcoupler": 18,
        "universaljoint": 19,
        "coordinate": 20,
        "slot": 21,
        "hinge": 22,
        "slider": 23,
        "profilecenter": 24,
    }
    # 兼容旧的拼写（原 MATE_TYPES 里用过 antialigned / locktosetch 之类）
    _MATE_TYPE_ALIASES = {
        "antialigned": "concentric",
        "anti-aligned": "concentric",
        "locktosetch": "locktosetch",
        "locksketch": "locktosetch",
        "cam": "camfollower",
        "rack": "rackpinion",
    }

    @classmethod
    def _parse_mate_type(cls, mate_type):
        """把配合类型（字符串或 SW 枚举 int）解析成 swMateType_e 整数。

        来源：swconst.tlb 实测表见 _MATE_TYPE_E。
        为什么不再用老的 MATE_TYPES 硬编码表：那张表把
        'concentric' 写成 4（实为 TANGENT）、'lock' 写成 9（实为 CAMFOLLOWER），
        传错枚举 SW 要么报"配合类型无效"、要么建出完全不相干的配合。
        Returns: (int 枚举值, None) 或 (None, 错误说明)
        """
        if isinstance(mate_type, str):
            _k = mate_type.strip().lower().replace(" ", "").replace("_", "")
            _k = cls._MATE_TYPE_ALIASES.get(_k, _k)
            if _k in cls._MATE_TYPE_E:
                return cls._MATE_TYPE_E[_k], None
            return None, ("未知配合类型 %r，可用: %s"
                          % (mate_type, ", ".join(sorted(cls._MATE_TYPE_E))))
        try:
            return int(mate_type), None
        except Exception:
            return None, "配合类型无法解析: %r" % (mate_type,)

    # ── BUG-12：组件 → 文档 / 实体访问（早期绑定优先，晚期自动回退）──────
    def _component_doc(self, comp):
        """取组件对应的模型文档（BUG-12 修复）。

        BUG-12 现象：GetComponents 拿到的组件上调 GetModelDoc2() 恒为 None。
        根因（报告 1.1 / 3.5）：SW2025 的 IComponent2.GetModelDoc2 在【晚期
        绑定】下报"找不到成员"，pywin32 把异常吞成 None；必须先把组件包装成
        早期接口 mod.IComponent2(comp._oleobj_) 再调。
        本方法按 早期 IComponent2 → 晚期组件 → 组件.GetModelDoc（旧名）的顺序
        尝试，全部失败返回 None（绝不抛异常）。
        """
        if comp is None:
            return None
        _cands = []
        try:
            _ce = early(comp, "IComponent2")
            if _ce is not None:
                _cands.append(("early(IComponent2)", _ce))
        except Exception:
            pass
        _cands.append(("late(component)", comp))
        for _name, _holder in _cands:
            for _meth in ("GetModelDoc2", "GetModelDoc", "IGetModelDoc2"):
                try:
                    _fn = getattr(_holder, _meth, None)
                    if _fn is None:
                        continue
                    _doc = _fn()
                    if _doc is not None:
                        return _doc
                except Exception:
                    continue
        return None

    def _component_bodies(self, comp, body_type=0):
        """取组件里的实体列表（BUG-12 修复）。

        BUG-12 现象：组件上的 GetBody2() 恒为 None（晚期绑定下该方法自身
        也常报错）。报告 2.3 明确：IModelDoc2 / IComponent2 上【没有】GetBody，
        只能 GetBodies() / GetBodies2() 再 list() 转数组 —— 它返回的是枚举器，
        不能直接索引 len()。
        Args:
            body_type: swBodyType_e，0 = swSolidBody（默认只要实体）。
        Returns: list[IBody2]（失败返回 []，绝不抛异常）
        """
        if comp is None:
            return []
        _holders = []
        try:
            _ce = early(comp, "IComponent2")
            if _ce is not None:
                _holders.append(_ce)
        except Exception:
            pass
        _holders.append(comp)
        for _h in _holders:
            for _meth in ("GetBodies2", "GetBodies", "GetBody2", "GetBody"):
                try:
                    _fn = getattr(_h, _meth, None)
                except Exception:
                    _fn = None
                if _fn is None:
                    continue
                _args = ()
                # GetBodies/GetBodies2 需要 swBodyType_e；GetBody 无参
                if _meth in ("GetBodies", "GetBodies2"):
                    _args = (int(body_type),)
                try:
                    _res = _fn(*_args)
                except Exception:
                    continue
                if _res is None:
                    continue
                _list = []
                if isinstance(_res, (list, tuple)):
                    _list = [b for b in _res if b is not None]
                else:
                    # 枚举器（IEnumBodies2）不可索引，必须 list() —— 报告 2.3
                    try:
                        _list = [b for b in list(_res) if b is not None]
                    except Exception:
                        _list = [_res]
                if _list:
                    return _list
        return []

    def _asm_early(self):
        """取【早期绑定的 IAssemblyDoc】（失败回退晚期 model 本身）。

        来源：报告 1.2 —— asm_ia = mod.IAssemblyDoc(model._oleobj_)。
        为什么每次都重新包：包装很便宜（一次 QueryInterface），而缓存对象
        在文档被关闭/重新打开后会失效，反而更难排错。
        """
        _m = getattr(self, "model", None)
        if _m is None:
            return None
        try:
            return early(_m, "IAssemblyDoc")
        except Exception:
            return _m

    # ── 【BUG-9】早期绑定探测（配合主路径的准入判据）─────────────────────
    def _asm_early_probe(self):
        """探测"本进程/本文档"是否真有可用的早期绑定 IAssemblyDoc。

        ══ 为什么需要独立探测（报告 1 / 4）══════════════════════════════
        `_asm_early()` 的行为是"包装失败就原样返回晚期 model"，于是调用方
        【看不出】自己拿到的是早期还是晚期接口 —— 而这两种模式下
        CreateMate/AddMate5 的参数要求完全相反（ByRef 要不要 VARIANT）。
        把早期/晚期混在一套写法里试，就退化成"猜参数个数"，正是历史卡死的
        诱因。因此这里显式回答一个问题：**当前能走早期绑定吗？**

        判据（三条同时成立才算 early）：
          ① 类型库模块可用（get_early_module 非 None）；
          ② 包装出的对象【不是】原 model（early() 失败会原样返回）；
          ③ 包装对象上确实有 CreateMate 或 AddMate5 之一。
        Returns: dict {mode: "early"|"late", holder, reason, has_create_mate,
                       has_addmate5}
        【绝不抛异常】—— 探测本身失败要如实说明，而不是中断装配。
        """
        _m = getattr(self, "model", None)
        _info = {"mode": "late", "holder": None, "reason": None,
                 "has_create_mate": False, "has_addmate5": False,
                 "has_create_mate_data": False}
        if _m is None:
            _info["reason"] = "model 为 None（没有活动装配体）"
            return _info
        try:
            if not is_early_available():
                _info["reason"] = ("类型库/早期绑定模块不可用（%s）"
                                   % (early_status().get("error")
                                      or "get_early_module 返回 None"))
                return _info
        except Exception as _e:
            _info["reason"] = "is_early_available() 异常: %r" % (_e,)
            return _info
        try:
            _wrapped = self._asm_early()
        except Exception as _e2:
            _info["reason"] = "_asm_early() 异常: %r" % (_e2,)
            return _info
        if _wrapped is None or _wrapped is _m:
            _info["reason"] = ("IAssemblyDoc 包装未生效（early() 回退成晚期对象）"
                               "—— 期间会按晚期绑定处理 ByRef 参数")
            return _info
        for _nm in ("CreateMate", "AddMate5", "CreateMateData"):
            try:
                _has = getattr(_wrapped, _nm, None) is not None
            except Exception:
                _has = False
            if _nm == "CreateMate":
                _info["has_create_mate"] = bool(_has)
            elif _nm == "CreateMateData":
                _info["has_create_mate_data"] = bool(_has)
            else:
                _info["has_addmate5"] = bool(_has)
        if not (_info["has_create_mate"] or _info["has_addmate5"]):
            _info["reason"] = ("包装对象上既没有 CreateMate 也没有 AddMate5 —— "
                               "本版本配合 API 与预期不同")
            return _info
        _info["mode"] = "early"
        _info["holder"] = "early(IAssemblyDoc)"
        return _info

    def _ensure_clear_selection(self):
        """建配合前清空选择集（报告 3：必须 ClearSelection2(True)）。

        残留选择会污染 mark 过滤 → 计数与预选错位。返回是否清成功。
        """
        _m = getattr(self, "model", None)
        if _m is None:
            return False
        for _arg in (True, False):
            try:
                _m.ClearSelection2(_arg)
                return True
            except Exception:
                continue
        return False

    def _selected_count_all(self):
        """读选择集总数 —— **一律传 -1**（报告 3b 的硬性要求）。

        `GetSelectedObjectCount2(0)` 的官方语义是"只数【没有 mark】的选择"，
        而配合实体的 mark 都是 1 → 用 0 去数必然是 0，于是被误判成"没选中"。
        Returns: int；读不到返回 -1（调用方必须按"未知"处理，不能当 0）。
        """
        _m = getattr(self, "model", None)
        if _m is None:
            return -1
        try:
            return int(_m.SelectionManager.GetSelectedObjectCount2(-1))
        except Exception:
            return -1

    def _select_by_id_asm(self, name, sel_type, x, y, z, append, mark,
                          sel_opt=0):
        """装配配合链路上专用 SelectByID2（BUG-11 修复的一部分）。

        为什么单独写一个而【不全局替换】：
          报告 2.3 第 6/7 条 —— 早期绑定下 Callout 只能传 None，
          传 VARIANT(pythoncom.VT_DISPATCH, None) 会报
          "Python instance can not be converted to a COM object"，
          传 pythoncom.Missing 报"非选择性的参数"；
          但【晚期绑定】下恰恰相反：Callout 必须是 VARIANT，传 None 会报
          "非选择性的参数"（本文件 3200 行附近的历史结论）。
        所以本函数先试早期 IModelDoc2.Extension（Callout=None），
        失败或不可用再走原来的晚期写法（Callout=self._empty）。
        只覆盖装配配合链路，不动零件建模路径 —— 那部分晚期行为已经稳定。
        Returns: bool（是否选中）
        """
        _m = getattr(self, "model", None)
        if _m is None:
            return False
        # ── 路径 1：早期绑定 Extension.SelectByID2(..., None, ...) ─────────
        try:
            _me = early(_m, "IModelDoc2")
            _ext_e = getattr(_me, "Extension", None) if _me is not None else None
            if _ext_e is not None:
                if _ext_e.SelectByID2(str(name), str(sel_type),
                                      float(x), float(y), float(z),
                                      bool(append), int(mark), None,
                                      int(sel_opt)):
                    return True
        except Exception:
            pass
        # ── 路径 2：晚期绑定（原行为，Callout=VARIANT）────────────────────
        try:
            _ext = getattr(self, "ext", None)
            if _ext is None:
                _ext = _m.Extension
            if _ext is not None and _ext.SelectByID2(
                    str(name), str(sel_type), float(x), float(y), float(z),
                    bool(append), int(mark), self._empty, int(sel_opt)):
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _mate_value_args(cls, mate_type, distance_mm=None, angle_deg=None):
        """按配合类型算出 AddMate5 的 5 个浮点 + 角度参数（报告 3.3）。

        报告 2.3 第 9 条：AddMate5 的浮点参数【不能传 None】，
        否则 pywin32 在参数编组前就抛
        "float() argument must be a string or a real number, not 'NoneType'"
        —— 这正是 BUG-11 里那句"类型不匹配"的来源之一。
        所以这里永远给具体 float：
          距离类  = distance_mm（mm→m）填 Distance，其它 0.0
          角度类  = angle_deg（度→弧度）填 Angle，其它 0.0
          上下限  = 0.0 表示"不设限"（SW 语义：0 = 无限制）
        返回 (Distance, DistAbsUpper, DistAbsLower, GearRatioN,
              GearRatioD, Angle, AngleAbsUpper, AngleAbsLower)
        """
        _mt = int(mate_type)
        _d = 0.0
        _a = 0.0
        if distance_mm is not None:
            try:
                _d = float(distance_mm) * MM      # mm → m（swapi 内部单位）
            except Exception:
                _d = 0.0
        if angle_deg is not None:
            try:
                _a = float(angle_deg) * math.pi / 180.0
            except Exception:
                _a = 0.0
        # 距离配合类型（5）未给值时也必须给 0.0，绝不能是 None
        if _mt == 5 and distance_mm is None:
            _d = 0.0
        # 齿条/齿轮/螺距类需要 GearRatio，给安全默认 1:1（报告 3.3 序列）
        _n = 1.0
        _den = 1.0
        if _mt in (10, 13, 17):          # GEAR / RACKPINION / SCREW
            try:
                if distance_mm is not None:
                    _n = float(distance_mm)
            except Exception:
                _n = 1.0
        return (_d, 0.0, 0.0, _n, _den, _a, 0.0, 0.0)


    # ── 组件选择（装配配合用；早期 IComponent2 只有 Select/SelectByMark）──
    def _select_component(self, comp, append=False, mark=0):
        """选中一个组件对象（配合参考为"整个组件"时用，如锁定配合）。

        为什么不用 Select4：IComponent2 的早期接口里只有
        Select(AppendFlag) / SelectByMark(AppendFlag, Mark)（见类型库），
        没有 Select4；而晚期绑定下 Select4(Append, SelectData) 可用。
        因此这里【先早期、再晚期】，两种写法都尝试，成功即返回 True。
        """
        if comp is None:
            return False
        _ce = None
        try:
            _ce = early(comp, "IComponent2")
        except Exception:
            _ce = comp
        # 早期接口：SelectByMark / Select
        for _holder in (_ce, comp):
            if _holder is None:
                continue
            for _mn, _args in (("SelectByMark", (bool(append), int(mark))),
                               ("Select2", (bool(append), int(mark))),
                               ("Select", (bool(append),))):
                try:
                    _fn = getattr(_holder, _mn, None)
                    if _fn is None:
                        continue
                    _fn(*_args)
                    return True
                except Exception:
                    continue
            # 晚期专属：Select4 需要 SelectData（_make_select_data 在建文档后可用）
            try:
                _sd = self._make_select_data(mark)
                if _sd is not None:
                    comp.Select4(bool(append), _sd)
                    return True
            except Exception:
                pass
        return False

    def _best_planar_face(self, comp):
        """在组件里挑一个【面积最大的平面】作为配合参考（BUG-12 连带修复）。

        为什么必须走 _component_bodies：BUG-12 里组件 GetBody2 恒为 None，
        主因是晚期绑定下这两条路都不通；报告 2.3 明确 GetBody 在
        IModelDoc2 上根本不存在，只能 GetBodies()/GetBodies2() + list()。
        Returns: (face, area_m2) 或 (None, 0.0)
        """
        _best = None
        _best_area = -1.0
        _diag = getattr(self, "_best_face_diag", None)
        if _diag is None:
            _diag = []
            self._best_face_diag = _diag
        for _b in self._component_bodies(comp):
            try:
                _faces = _b.GetFaces()
            except Exception as _e_gf:
                _diag.append("GetFaces: %r" % (_e_gf,))
                continue
            if _faces is None:
                continue
            try:
                _fl = list(_faces)
            except Exception:
                _fl = [_faces]
            for _f in _fl:
                if _f is None:
                    continue
                # ── 面积：先晚期（实机可用），再 early(IFace2).GetArea ─────
                # 实测（SW2025）：IBody2.GetFaces() 回来的面在晚期绑定下
                # Normal / GetArea 都能用；但早期 IEntity 包装【没有】这两个
                # 成员（它们定义在 IFace2 上），所以这里必须按接口分派，
                # 不能一律当我们自己构造的早期对象使。
                _area = None
                try:
                    _area = float(_f.GetArea())
                except Exception:
                    _fe = None
                    try:
                        _fe = early(_f, "IFace2")
                    except Exception:
                        _fe = None
                    if _fe is not None:
                        try:
                            _area = float(_fe.GetArea())
                        except Exception as _e_ga:
                            _diag.append("GetArea: %r" % (_e_ga,))
                if _area is None or _area <= 0.0:
                    continue
                # ── 只要平面（曲面法线不稳定；配合面基本都是平面）──────
                # 判平面失败时【不直接丢弃】：SW 有些 face 的 Normal 取不到但
                # 仍是平面；改为"接受但降权"，由面积决定优先级。
                _is_plane = True
                try:
                    _n = _f.Normal
                    _is_plane = _n is not None
                except Exception:
                    try:
                        _fe2 = early(_f, "IFace2")
                        _is_plane = getattr(_fe2, "IsPlane", True) is not False
                    except Exception:
                        _is_plane = True
                _score = _area if _is_plane else _area * 0.5
                if _score > _best_area:
                    _best_area = _score
                    _best = _f
        return _best, max(_best_area, 0.0)

    def _select_face(self, face, append=False, mark=0):
        """选中一个面（IFace2）。

        接口归属（本机类型库实测）：Select/Select2/Select3/Select4/SelectByMark
        是在【IEntity】上，IFace2 自身【没有】这几个方法。
        所以早期绑定必须先 early(face, 'IEntity') 再调；晚期绑定下 face 本身是
        CDispatch，直接调也能通（保留这条退路，老版本行为不变）。
        """
        if face is None:
            return False
        _holders = []
        try:
            _ent = early(face, "IEntity")
            if _ent is not None:
                _holders.append(("early(IEntity)", _ent))
        except Exception:
            pass
        _holders.append(("late(face)", face))
        for _hname, _h in _holders:
            _sd = None
            for _mn in ("Select4", "Select3", "Select2", "Select"):
                try:
                    _fn = getattr(_h, _mn, None)
                except Exception:
                    _fn = None
                if _fn is None:
                    continue
                try:
                    if _mn == "Select4":
                        _sd = _sd or self._make_select_data(mark)
                        if _sd is None:
                            continue
                        _fn(bool(append), _sd)
                    elif _mn == "Select3":
                        _sd = _sd or self._make_select_data(mark)
                        if _sd is None:
                            continue
                        _fn(bool(append), int(mark), _sd)
                    elif _mn == "Select2":
                        _fn(bool(append), int(mark))
                    else:
                        _fn(bool(append))
                    return True
                except Exception:
                    continue
        return False

    # ── 配合参考解析（create_mate 的 entities 元素 → 实际选中）─────────
    def _asm_select_reference(self, spec, append=False, mark=1):
        """把一个"配合参考规格"选中，返回 (ok, 标签)。

        spec 支持四种写法（都来自现场脚本的真实用法）：
          ① "Front Plane" / "上视基准面" → 按 PLANE 选（基准面配合）
          ② "DSH_底座-1"                → 该组件面积最大的平面（最常用）
          ③ {"component": "X", "face_index": n} → 指定组件的第 n 个平面
          ④ {"type": "FACE", "at": [x,y,z]}     → 按坐标射线选面
          ⑤ {"type": "PLANE", "name": "..."}     → 显式基准面
        """
        _PLANES = ("Front Plane", "前视基准面", "Top Plane", "上视基准面",
                   "Right Plane", "右视基准面")
        if spec is None:
            return False, ""
        # ②/③ 组件（按名或按对象）
        _comp = None
        _comp_name = None
        _at = None
        _stype = None
        if isinstance(spec, dict):
            _comp_name = spec.get("component") or spec.get("comp")
            _at = spec.get("at") or spec.get("point")
            _stype = spec.get("type")
            if spec.get("name") and not _comp_name:
                _stype = _stype or "PLANE"
                if _stype == "PLANE":
                    if self._select_by_id_asm(spec.get("name"), "PLANE",
                                              0, 0, 0, append, mark):
                        return True, "PLANE:%s" % spec.get("name")
        elif isinstance(spec, str):
            if spec.strip() in _PLANES:
                if self._select_by_id_asm(spec, "PLANE", 0, 0, 0, append, mark):
                    return True, "PLANE:%s" % spec
            else:
                _comp_name = spec
        else:
            # 直接给了组件对象或面对象
            try:
                if hasattr(spec, "Name2"):
                    _comp_name = str(spec.Name2)
            except Exception:
                pass
            if _comp_name is None:
                if self._select_face(spec, append, mark):
                    return True, "FACE(obj)"
                return False, ""
        # 按坐标选面（优先级最高：调用方明确给了点）
        if _at:
            try:
                _x, _y, _z = [float(v) for v in _at]
            except Exception:
                _x = _y = _z = 0.0
            if self._select_by_id_asm("", _stype or "FACE", _x, _y, _z,
                                      append, mark):
                return True, "FACE(at=%s)" % (_at,)
        # 按组件名找组件 → 选它面积最大的平面（BUG-12 后这条路才真正通）
        if _comp_name:
            for _c in (self._assembly_components() or []):
                try:
                    if str(_c.Name2) != str(_comp_name):
                        continue
                    _f, _area = self._best_planar_face(_c)
                    if _f is not None and self._select_face(_f, append, mark):
                        # _best_planar_face 返回的面积单位是 m²（SW 内部单位），
                        # 标签里换算成 mm² 才便于人工核对
                        return True, ("FACE(comp=%s,%.1fmm2)"
                                      % (_comp_name, float(_area) * 1e6))
                    # 面取不到时退回"选中整个组件"（锁定/对称配合可能需要）
                    if self._select_component(_c, append, mark):
                        return True, "COMP:%s" % _comp_name
                except Exception:
                    continue
            # 名字既不是基准面也不是组件 → 当作 FACE 名字直接试
            if self._select_by_id_asm(str(_comp_name), _stype or "FACE",
                                      0, 0, 0, append, mark):
                return True, "FACE:%s" % _comp_name
        return False, ""

    def _assembly_components(self):
        """取装配体组件列表（失败返回 []，绝不抛异常）。"""
        try:
            _c = self.model.GetComponents(False)
        except Exception:
            return []
        if _c is None:
            return []
        if isinstance(_c, (list, tuple)):
            return list(_c)
        try:
            return list(_c)
        except Exception:
            return [_c]

    # ══ 【BUG-9 核心】装配配合的唯一收口点 ═══════════════════════════════
    #  分层策略（严格按 docs/SW2025_AddMate_win32com_技术结论.md 建议顺序）：
    #    ⓪ 探测：早期绑定可用？（_asm_early_probe）
    #    ① 可用 → CreateMateData(type) → 设属性 → CreateMate(md)   【主路径】
    #    ② 不可用 / ① 失败 → 晚期 AddMate5(15 参，ErrorStatus 用 VARIANT ByRef)
    #    ③ 仍失败 → AddMate3(14 参)（老版本）、CreateMate(None)（占位形态）
    #    ④ 仍失败 → RunMacro2 宏兜底（仅当显式给了 DSH_MATE_MACRO 且是真 .swp）
    #  每一次尝试都把"用了哪一层 + 失败的 API + 错误码"写进
    #  self._last_mate_diag，供 create_mate / 门禁 / 报告如实回显 ——
    #  绝不"拿到对象就算成功"，也绝不把失败说成成功。
    def _add_mate_early(self, mate_type, align=0, flip=False,
                        distance_mm=None, angle_deg=None, name=""):
        """建立配合（BUG-9 修复：早期绑定探测 + CreateMate 主路径 + 分层兜底）。

        ══ 为什么必须分层（报告 一-1 / 二-2b / 二-2b 注解）══════════════════
        两个独立事实决定了写法不能统一：
          A. `AddMate5` 第 15 参 `ErrorStatus` 是 ByRef `System.Int32`
             （报告 一-1）。晚期绑定下必须传
             `VARIANT(VT_BYREF|VT_I4, 0)`；传裸 0 会被打包成值传递 →
             DISP_E_TYPEMISMATCH(-2147352571)，也就是历史日志里那句
             "类型不匹配"。而【早期绑定】恰恰相反：gencache 会把 ByRef 变成
             第二个返回值（`mate, err = ...`），再传 VARIANT 反而参数个数错。
          B. `CreateMate` 是官方推荐路径（报告 一-2）：三步
             `CreateMateData(Type) → IMateFeatureData → CreateMate(md)`，
             绕开 AddMate5 的 ByRef 与 15 参签名，命中率最高。
             注意 **`CreateMateData2` 不存在**（报告 五-2），只有
             `CreateMateData`；而 `IMateData` 接口类不在该 tlb 里导出，
             不能 `mod.IMateData(...)` 构造。

        ══ 预选约定（报告 一-3 / 一-3b）════════════════════════════════════
        调用方负责在调用本方法【之前】用 SelectByID2 mark=1 预选好两个实体
        （官方 Remarks 明确支持预选，这是最省事、最不容易错的路线，
        可以完全绕开 `EntitiesToMate` 的 SAFEARRAY 封送问题）。
        本方法会先 `ClearSelection2(True)` 吗？—— 【不会】，因为那会清掉
        调用方的预选；只由调用方在建配合前清。这里只用
        `_selected_count_all()`（内部恒传 -1）核验预选是否真的还在。

        Args:
            mate_type: swMateType_e 整数（见 SWModel._MATE_TYPE_E）。
            align: swMateAlign_e（0=ALIGNED / 1=ANTI_ALIGNED / 2=CLOSEST）。
            flip: 仅距离配合有效。
            distance_mm / angle_deg: 距离(mm) / 角度(度) 的配合值。

        Returns: (mate 对象或 None, 实际生效的层+API 名, 错误说明)
            —— 失败时第一项一定是 None；第三项一定非空（含每一层的原因）。
        """
        _mt = int(mate_type)
        _align = int(align) if mate_align_is_valid(align) else 0
        _diag = {"layers": [], "probe": None, "selected_count": None,
                 "mate_created": False}
        self._last_mate_diag = _diag
        self._last_mate_error_status = None

        # ── ⓪ 早期绑定探测（决定后面用哪套参数形态）──────────────────────
        _probe = self._asm_early_probe()
        _diag["probe"] = _probe
        _early_holder = None
        _holders = []
        if _probe.get("mode") == "early":
            try:
                _early_holder = self._asm_early()
            except Exception:
                _early_holder = None
            if _early_holder is not None and _early_holder is not self.model:
                _holders.append(("early(IAssemblyDoc)", _early_holder))
        # 晚期兜底：老机器 SW2018~2024 与早期绑定不可用时都走这里
        try:
            if self.model is not None:
                _holders.append(("late(model)", self.model))
        except Exception:
            pass
        try:
            _diag["selected_count"] = self._selected_count_all()
        except Exception:
            _diag["selected_count"] = None

        _d, _du, _dl, _gn, _gd, _a, _au, _al = self._mate_value_args(
            _mt, distance_mm, angle_deg)
        _n = str(name or "")
        _errs = []

        # ── ① CreateMate 主路径（官方三步，绕开 AddMate5 的 ByRef）────────
        #  报告 二-2b：CreateMateData(type) → 设 MateAlignment → CreateMate(md)。
        #  预选已由调用方完成（mark=1），因此【不设】EntitiesToMate。
        for _hname, _h in _holders:
            _ok, _m, _why = self._mate_by_createmate(
                _h, _mt, _align, _d, _a)
            _diag["layers"].append({"layer": "CreateMate", "holder": _hname,
                                    "ok": bool(_ok), "why": _why})
            if _ok and _m is not None:
                _diag["mate_created"] = True
                return (_m, "%s/CreateMate(CreateMateData)" % (_hname,), None)

        # ── ② 专用 API 快捷路径（同心 / 距离：签名最短、命中率高）─────────
        for _hname, _h in _holders:
            if _mt == 1:      # swMateCONCENTRIC
                for _argc in (4, 5):
                    try:
                        _fn = getattr(_h, "AddConcentricMateWithTolerance", None)
                        if _fn is None:
                            break
                        _args = [int(align), 0, False, 0.0][:_argc]
                        _m = _fn(*_args)
                        if _m is not None:
                            return (_m,
                                    "%s/AddConcentricMateWithTolerance(%d)"
                                    % (_hname, _argc), None)
                    except Exception as _e1:
                        _errs.append("%s/AddConcentricMateWithTolerance: %r"
                                     % (_hname, _e1))
                        break
            elif _mt == 5:    # swMateDISTANCE
                for _argc in (7, 8):
                    try:
                        _fn = getattr(_h, "AddDistanceMate", None)
                        if _fn is None:
                            break
                        _args = [int(align), bool(flip), _d, _du, _dl, 0, 0][:_argc]
                        _m = _fn(*_args)
                        if _m is not None:
                            return _m, "%s/AddDistanceMate(%d)" % (_hname, _argc), None
                    except Exception as _e2:
                        _errs.append("%s/AddDistanceMate: %r" % (_hname, _e2))
                        break

        # ── ② 通用 AddMate5（官方 15 参，ErrorStatus 必须 byref 并检查）────
        # ══ 【配合参数修复 · 2026-11-11】统一为官方 15 参 ══════════════════
        # 现场现象：配合反复失败并伴随长时间卡死（600s 量级），日志里
        #   AddMate5 各种 "类型不匹配/参数个数不对"；小屋报"配合建不起来"。
        # 根因：官方签名是 15 参 ——
        #   AddMate5(MateType, Align, Flip, Distance, DistAbsUpper, DistAbsLower,
        #     GearRatioNumerator, GearRatioDenominator, Angle, AngleAbsUpper,
        #     AngleAbsLower, ForPositioningOnly, LockRotation, WidthMateOption,
        #     ByRef ErrorStatus) -> Mate2
        #   参考：https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~AddMate5.html
        #   而原实现先试 14 参（漏 ErrorStatus，byref 出参缺失 → 编组阶段报
        #   "类型不匹配"），晚期回退链又用 8 参 / 12 参，全部与签名不符 ——
        #   每一轮都在"猜参数个数"，只能靠异常收场，正是卡死的诱因之一。
        # 修复判据：
        #   · 只发 15 参，ErrorStatus = VARIANT(VT_BYREF|VT_I4, 0)，调用后读出；
        #   · swAddMateError_e：0=ErrorUnknown(失败)、1=NoError(成功)、
        #     2=IncorrectMateType / 3=IncorrectAlignment / 4=IncorrectSelections /
        #     5=OverDefinedAssembly / 6=IncorrectGearRatios；
        #   · 即使 Mate2 非 None，ErrorStatus 若既不是 0 也不是 1 也判失败并把
        #     错误码写进返回体（绝不"拿到对象就算成功"）。
        #   · 不在失败重试链里调 rebuild()/ForceRebuild3() —— 大型装配体上
        #     一次全量重建就可能挂几分钟，加重试等于把卡死放大。
        # ══ 【BUG-9 修复】区分早期 / 晚期两种调用形态 ══════════════════════
        #  官方签名（报告 一-1）：
        #    AddMate5(MateType, Align, Flip, Distance, DistAbsUpper, DistAbsLower,
        #      GearRatioNumerator, GearRatioDenominator, Angle, AngleAbsUpper,
        #      AngleAbsLower, ForPositioningOnly, LockRotation, WidthMateOption,
        #      ByRef ErrorStatus) -> Mate2
        #  · 晚期绑定：第 15 参必须是 VARIANT(VT_BYREF|VT_I4, 0)，
        #      传裸 0 → DISP_E_TYPEMISMATCH(-2147352571)（历史"类型不匹配"真因）；
        #  · 早期绑定：ByRef 自动解包成第二个返回值，**只传 14 参**，
        #      再塞 VARIANT 会变成参数个数错（报告 二-2b 的 add_mate_early_bound）。
        #  判据：holder 名以 "early" 开头 → 14 参双返回值；否则 → 15 参单返回值。
        #  · 错误码：swAddMateError_e 成功是 1（报告 三-7），0 是 ErrorUnknown，
        #      所以"Mate2 非 None 但 ErrorStatus 不是 NoError"一律判失败。
        #  · 不在失败重试链里调 rebuild()/ForceRebuild3 —— 大型装配体上一次全量
        #      重建可能挂几分钟，加重试等于把卡死放大。
        _am5 = [_mt, _align, bool(flip), _d, _du, _dl, _gn, _gd,
                _a, _au, _al, False, False, 0]
        for _hname, _h in _holders:
            _is_early = str(_hname).startswith("early")
            try:
                _fn = getattr(_h, "AddMate5", None)
            except Exception:
                _fn = None
            if _fn is None:
                _diag["layers"].append(
                    {"layer": "AddMate5", "holder": _hname, "ok": False,
                     "why": "本 holder 上没有 AddMate5"})
                continue
            _esv = None
            _m = None
            if _is_early:
                # 早期绑定：14 参，ByRef 变成第二个返回值
                try:
                    _res = _fn(*_am5)
                except Exception as _e3a:
                    _errs.append("%s/AddMate5(14, early): %r" % (_hname, _e3a))
                    _diag["layers"].append(
                        {"layer": "AddMate5", "holder": _hname, "ok": False,
                         "why": "early 14 参调用异常: %r" % (_e3a,)})
                    continue
                if isinstance(_res, (tuple, list)):
                    _m = _res[0] if len(_res) > 0 else None
                    _esv = _res[1] if len(_res) > 1 else None
                else:
                    _m = _res
            else:
                # 晚期绑定：15 参，ErrorStatus 用 VARIANT(VT_BYREF|VT_I4)
                try:
                    _es = _byref_i4(0)
                    _m = _fn(*(_am5 + [_es]))
                    _esv = _byref_value(_es, None)
                except Exception as _e3:
                    _errs.append("%s/AddMate5(15, late): %r" % (_hname, _e3))
                    _diag["layers"].append(
                        {"layer": "AddMate5", "holder": _hname, "ok": False,
                         "why": ("late 15 参(VARIANT ByRef)调用异常: %r"
                                 % (_e3,))})
                    continue
            _ok5, _known5 = mate_ok_by_error_status(_m, _esv)
            if _esv is not None:
                try:
                    self._last_mate_error_status = {
                        "value": int(_esv),
                        "text": mate_error_status_text(_esv),
                        "known": bool(_known5)}
                except Exception:
                    pass
            _diag["layers"].append(
                {"layer": "AddMate5", "holder": _hname,
                 "ok": bool(_ok5 and _m is not None),
                 "error_status": _esv,
                 "error_status_known": bool(_known5),
                 "error_text": (mate_error_status_text(_esv)
                                if _esv is not None else None),
                 "why": None if (_ok5 and _m is not None) else (
                     "Mate2=None" if _m is None else
                     "ErrorStatus=%r(%s)" % (_esv, mate_error_status_text(_esv)))})
            if _ok5 and _m is not None:
                return (_m, "%s/AddMate5(%d, ErrorStatus=%s)"
                        % (_hname, 14 if _is_early else 15, _esv), None)
            _errs.append("%s/AddMate5: 返回 %s，ErrorStatus=%s（%s）"
                         % (_hname, "None" if _m is None else "Mate2",
                            _esv, mate_error_status_text(_esv)))
        # 老版本才有的 AddMate3（官方 14 参：... ForPositioningOnly,
        #   LockRotation, ByRef ErrorStatus）；原实现只传 12 参，同样漏
        #   ErrorStatus —— 一并订正。早期绑定同样是 13 参 + 双返回值。
        for _hname, _h in _holders:
            try:
                _fn = getattr(_h, "AddMate3", None)
            except Exception:
                _fn = None
            if _fn is None:
                continue
            _is_early3 = str(_hname).startswith("early")
            try:
                _a3 = [_mt, _align, bool(flip), _d, _du, _dl,
                       _gn, _gd, _a, _au, _al, False, False]
                if _is_early3:
                    _res3 = _fn(*_a3)
                    if isinstance(_res3, (tuple, list)):
                        _m = _res3[0] if len(_res3) > 0 else None
                        _esv3 = _res3[1] if len(_res3) > 1 else None
                    else:
                        _m, _esv3 = _res3, None
                else:
                    _es3 = _byref_i4(0)
                    _m = _fn(*(_a3 + [_es3]))
                    _esv3 = _byref_value(_es3, None)
                _ok3, _known3 = mate_ok_by_error_status(_m, _esv3)
                _diag["layers"].append(
                    {"layer": "AddMate3", "holder": _hname,
                     "ok": bool(_ok3 and _m is not None),
                     "error_status": _esv3,
                     "error_status_known": bool(_known3),
                     "why": None if (_ok3 and _m is not None) else
                     ("Mate2=None" if _m is None else "ErrorStatus=%r" % (_esv3,))})
                if _ok3 and _m is not None:
                    return (_m, "%s/AddMate3(%d, ErrorStatus=%s)"
                            % (_hname, 13 if _is_early3 else 14, _esv3), None)
                _errs.append("%s/AddMate3: 返回 %s，ErrorStatus=%s"
                             % (_hname, "None" if _m is None else "Mate2", _esv3))
            except Exception as _e4:
                _errs.append("%s/AddMate3: %r" % (_hname, _e4))
                _diag["layers"].append(
                    {"layer": "AddMate3", "holder": _hname, "ok": False,
                     "why": "调用异常: %r" % (_e4,)})

        # ── ④ 兜底：RunMacro2 宏（报告 一-5 / 二-2c）────────────────────
        #  宏在 SW 自己进程内跑，等于早期绑定，ByRef 全自动 —— 是 API 全部
        #  不可用时唯一还能"真建配合"的路。但有三条硬约束，任何一条不满足
        #  都【不该尝试】（否则会把 COM 线程挂死）：
        #    · 必须显式配置宏路径（环境变量 DSH_MATE_MACRO），绝不臆造路径；
        #    · .swp 必须是二进制 OLE 复合文档（手写文本改名 → 模态框无限卡死）；
        #    · ModuleName 是 `<文件名>1`，优先用 GetMacroMethods 拿真名。
        _macro = self._run_mate_macro(_mt, _align, _d, _a)
        if _macro is not None:
            _diag["layers"].append({"layer": "RunMacro2", "holder": "sw",
                                    "ok": bool(_macro.get("ok")),
                                    "why": _macro.get("error")})
            if _macro.get("ok"):
                return (None, "RunMacro2(%s)" % (_macro.get("macro"),),
                        None if _macro.get("mate_feature_created") else
                        "宏已执行但未回报配合特征（配合是否真建立需复核）")
            _errs.append("RunMacro2: %s" % (_macro.get("error"),))

        return None, None, (" | ".join(_errs[-5:]) if _errs
                            else "本版本没有任何可用的 AddMate/CreateMate API")

    # ── 【BUG-9】CreateMate 主路径（官方三步）────────────────────────────
    def _mate_by_createmate(self, holder, mate_type, align=0,
                            distance=None, angle=None):
        """用官方三步建配合：CreateMateData(type) → 设属性 → CreateMate(md)。

        ══ 依据（报告 一-2 / 一-2b / 二-2b）════════════════════════════════
        官方 `CreateMate` Remarks 就是这个三步流程；`CreateMateData2`
        **不存在**（报告 五-2），只能用 `CreateMateData(Type)`。
        `EntitiesToMate` 是 System.Object 变体数组，传 Python list 不认；
        但官方 Remarks 明确支持"先 SelectByID2 mark=1 预选"，因此本实现
        **优先走预选**（不设 EntitiesToMate），只有在预选数不足时才退回
        `VARIANT(VT_ARRAY|VT_DISPATCH, (...))` 显式塞实体。

        ⚠️ 报告 五-5 的已知不确定项：晚期绑定下 `CreateMateData` 的返回值
        属性可见性未知（可能 DISP_E_MEMBERNOTFOUND）。因此这里对**每个属性
        赋值**都单独 try，失败只记录不中断 —— 属性设不上时仍尝试
        `CreateMate(md)`，由 SW 按默认值决定；成败一律以 CreateMate 的
        返回值 + 后续 Features 校验为准，绝不"设上了属性就宣布成功"。

        Returns: (ok: bool, mate_or_None, why: str|None)
        """
        try:
            _fn_data = getattr(holder, "CreateMateData", None)
            _fn_mate = getattr(holder, "CreateMate", None)
        except Exception as _e:
            return False, None, "取 CreateMate/CreateMateData 失败: %r" % (_e,)
        if _fn_mate is None:
            return False, None, "本 holder 上没有 CreateMate"
        if _fn_data is None:
            return False, None, ("本 holder 上没有 CreateMateData（报告五-2："
                                 "CreateMateData2 不存在，只有 CreateMateData）")
        _notes = []
        try:
            _md = _fn_data(int(mate_type))
        except Exception as _e1:
            return False, None, "CreateMateData(%r) 调用异常: %r" % (
                mate_type, _e1)
        if _md is None:
            return False, None, "CreateMateData 返回 None"
        # ① MateAlignment（报告 二-2b：md.MateAlignment = align）
        if mate_align_is_valid(align):
            try:
                _md.MateAlignment = int(align)
            except Exception as _e2:
                _notes.append("MateAlignment 设不上: %r" % (_e2,))
        # ② 距离 / 角度（只有对应类型才有意义）
        if distance:
            for _prop in ("Distance", "DistanceValue"):
                try:
                    setattr(_md, _prop, float(distance))
                    break
                except Exception:
                    continue
        if angle:
            for _prop in ("Angle", "AngleValue"):
                try:
                    setattr(_md, _prop, float(angle))
                    break
                except Exception:
                    continue
        # ③ EntitiesToMate：仅当预选不可用时才显式塞（报告 一-2b 路线 B）
        _sel_n = self._selected_count_all()
        if _sel_n is not None and _sel_n < 2:
            try:
                _sm = self.model.SelectionManager
                _e1o = _sm.GetSelectedObject6(1, -1)
                _e2o = _sm.GetSelectedObject6(2, -1)
                if _e1o is not None and _e2o is not None:
                    _md.EntitiesToMate = _dispatch_array((_e1o, _e2o))
                    _notes.append("改用 EntitiesToMate 显式塞实体")
            except Exception as _e3:
                _notes.append("EntitiesToMate 设置失败: %r" % (_e3,))
        try:
            _m = _fn_mate(_md)
        except Exception as _e4:
            return False, None, "CreateMate(md) 调用异常: %r（%s）" % (
                _e4, "；".join(_notes) or "无附加说明")
        if _m is None:
            return False, None, ("CreateMate(md) 返回 None（%s）"
                                 % ("；".join(_notes) or "属性设置无异常，"
                                    "SW 拒绝建配合"))
        if _notes:
            self._last_createmate_notes = _notes
        return True, _m, None

    # ── 【BUG-9】RunMacro2 宏兜底 ────────────────────────────────────────
    def _run_mate_macro(self, mate_type, align, distance, angle):
        """API 全不可用时的最后一条路：跑用户提供的 .swp 宏建配合。

        ══ 三条必踩坑（报告 一-5 / 二-2c），每条都做了防御 ════════════════
          ① `Error` 是 ByRef VT_I4 —— 必须 `VARIANT(VT_BYREF|VT_I4, 0)`，
             传裸 0 → Type mismatch (arg index 5)。
          ② `ModuleName` 是 `<文件名>1` —— 传文件名主干会得到 error 22
             InvalidIndex。这里优先用 `GetMacroMethods` 拿真名，拿不到才猜。
          ③ `.swp` 必须是**二进制 OLE 复合文档**；手写文本改名会弹 "Cannot open"
             模态框并**无限期挂住 COM 线程**。因此这里先检查文件头是不是
             OLE2 复合文档签名 `D0 CF 11 E0 A1 B1 1A E1`，不是就【拒绝调用】。

        安全约束：宏路径只能由环境变量 `DSH_MATE_MACRO` 显式给出，绝不猜路径、
        绝不生成临时宏（生成出来的必然不是 OLE 复合文档，会挂死进程）。

        Returns: dict|None —— None 表示"没有配置宏 / 前置条件不满足，未尝试"。
        """
        _path = (os.environ.get("DSH_MATE_MACRO") or "").strip()
        if not _path:
            return None
        if not os.path.isfile(_path):
            return {"ok": False, "macro": _path,
                    "error": "DSH_MATE_MACRO 指向的文件不存在: %s" % _path}
        # ③ 二进制 OLE 复合文档签名校验（防模态框挂死）
        try:
            with open(_path, "rb") as _f:
                _head = _f.read(8)
        except Exception as _e:
            return {"ok": False, "macro": _path,
                    "error": "读取宏文件失败: %r" % (_e,)}
        _OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        if _head != _OLE_MAGIC:
            return {"ok": False, "macro": _path,
                    "error": ("拒绝执行：该文件不是二进制 OLE 复合文档"
                              "（头 8 字节 %r ≠ %r）。手写文本改名 .swp 会弹"
                              "'Cannot open' 模态框并把 COM 线程挂死"
                              "（报告 二-2c 坑 3）。"
                              % (_head, _OLE_MAGIC))}
        _sw = getattr(self, "sw", None)
        if _sw is None:
            return {"ok": False, "macro": _path, "error": "sw 不可用（无应用对象）"}
        _fn = getattr(_sw, "RunMacro2", None)
        if _fn is None:
            return {"ok": False, "macro": _path,
                    "error": "本版本 SldWorks 上没有 RunMacro2"}
        # ② 模块名：优先 GetMacroMethods 拿真名（形如 "Module1.Proc"）
        _module = None
        _proc = "main"
        try:
            _methods = _sw.GetMacroMethods(_path, 0)
            _ml = list(_methods) if _methods is not None else []
            if _ml:
                _first = str(_ml[0])
                if "." in _first:
                    _module, _proc = _first.split(".", 1)
                else:
                    _module = _first
        except Exception:
            _module = None
        if not _module:
            _stem = os.path.basename(_path).rsplit(".", 1)[0]
            _module = _stem + "1"      # 见坑 2：<文件名>1
        # ① Error 必须 ByRef VT_I4
        try:
            _err = _byref_i4(0)
            _ok = _fn(_path, str(_module), str(_proc), 1, _err)
            _code = _byref_value(_err, None)
        except Exception as _e5:
            return {"ok": False, "macro": _path, "module": _module,
                    "procedure": _proc,
                    "error": ("RunMacro2 调用异常: %r（注意 Error 必须是 "
                              "VARIANT(VT_BYREF|VT_I4)，ModuleName 必须是 "
                              "'<文件名>1'）" % (_e5,))}
        _out = {"ok": bool(_ok) and (not _code or _code == 0),
                "macro": _path, "module": _module, "procedure": _proc,
                "sw_run_macro_error": _code,
                "mate_feature_created": None}
        if not _out["ok"]:
            _out["error"] = ("RunMacro2 失败: ok=%r swRunMacroError=%r"
                             "（22=InvalidIndex → 模块名不对）"
                             % (_ok, _code))
        return _out

    def create_mate(self, mate_type, entities, distance_mm=None,
                    angle_deg=None):
        """【BUG-14】新建配合的统一入口（文档承诺的 API，原代码里根本没有）。

        BUG-14 现象：工具链文档写"add_mate 已废弃，请用 create_mate"，
        但代码里既没有 create_mate 也没有 create_lock_mate —— 调用方按文档
        写脚本必然 AttributeError。本方法就是补上这个承诺。

        Args:
            mate_type: 'coincident' / 'concentric' / 'distance' / 'angle' /
                'parallel' / 'perpendicular' / 'tangent' / 'symmetric' / 'lock'
                / 'screw' / 'gear' 等，或直接给 swMateType_e 整数。
                （枚举表见 _MATE_TYPE_E，来源 swconst.tlb 实测）
            entities: 恰好 2 个配合参考（顺序不限）。每个元素支持：
                · 基准面名 "Front Plane" / "上视基准面"
                · 组件名 "DSH_底座-1"（自动取其最大平面）
                · {"component": "X"} / {"type":"FACE","at":[x,y,z]} /
                  {"type":"PLANE","name":"Front Plane"}
                · 面对象 / 组件对象本身
            distance_mm: 距离配合的数值（mm）。
            angle_deg:   角度配合的数值（度）。

        Returns:
            {ok, mate_type, mate_enum, used_api, mate_name?, selections?,
             selected_count?, error?, hint?, fallback?}
            —— 成功时 ok=True 且 used_api 标明真正生效的 API（可审计）。
        """
        out = {"ok": False, "mate_type": mate_type}
        try:
            _mt, _perr = self._parse_mate_type(mate_type)
            if _mt is None:
                out["error"] = _perr
                return out
            out["mate_enum"] = _mt
            # entities 允许传 (a,) 单元素（锁定配合可只给一个组件数组）
            if entities is None:
                out["error"] = "entities 为空：配合至少需要 2 个参考对象"
                return out
            if not isinstance(entities, (list, tuple)):
                entities = [entities]
            _ents = list(entities)
            if len(_ents) < 2 and _mt != 16:
                out["error"] = ("entities 需要 2 个配合参考，当前 %d 个"
                                % len(_ents))
                out["hint"] = ("例：create_mate('distance', ('DSH_底座-1', "
                               "'DSH_上盖-1'), distance_mm=10)")
                return out
            try:
                self._activate_self()
            except Exception:
                pass
            # 官方五步：① 清空选择 ② 选参考 1 ③ 选参考 2 ④ 建配合
            # ── 【BUG-9】ClearSelection2 必须先做（报告 一-3）──────────────
            # 残留选择会污染 mark 过滤：配合实体的 mark 都是 1，而计数若用
            # GetSelectedObjectCount2(0)（语义 = 只数【没有 mark】的选择）就会
            # 读回 0 → 被误判成"没选中"。本方法已统一走 _selected_count_all()
            # （内部恒传 -1），并在建配合前强制清一次选择集。
            _cleared = self._ensure_clear_selection()
            out["selection_cleared"] = bool(_cleared)
            _notes = []
            _ok_any = False
            for _i, _spec in enumerate(_ents):
                try:
                    _ok, _lbl = self._asm_select_reference(
                        _spec, append=(_i > 0), mark=1)
                except Exception as _e_sel:
                    _ok, _lbl = False, "ERR:%r" % (_e_sel,)
                _notes.append(_lbl or "unselected:%r" % (_spec,))
                if _ok:
                    _ok_any = True
            out["selections"] = _notes
            # 选择集数量是"能不能建配合"的硬判据（老代码栽在这）
            _sel_count = self._selected_count_all()
            if _sel_count < 0:
                _sel_count = sum(1 for n in _notes
                                 if not n.startswith("unselected")
                                 and not n.startswith("ERR"))
            out["selected_count"] = _sel_count
            # 从 SelectionMgr 独立再读一次（不依赖我们自己数出来的结果）：
            # 实机发现"面选上后再选第二个对象可能把第一个顶掉"，这个读数
            # 就是判断"选择集真的还在不在"的唯一可信来源，必须留证。
            # 【BUG-9】同样恒传 -1（报告 一-3b）。
            _mgr_count = self._selected_count_all()
            if _mgr_count >= 0:
                out["select_mgr_count"] = _mgr_count
            if _sel_count < 2:
                out["error"] = ("配合要求至少选中 2 个参考对象，当前仅 %d 个"
                                % _sel_count)
                out["hint"] = ("参考对象必须可被选中：基准面名要存在于本装配体，"
                               "组件名要能被 GetComponents 找到；"
                               "也可以直接把面对象传进来。"
                               "装配体里 SelectByID2 的名字格式是 "
                               "'<实体名>@<零部件实例名>@<装配体名>'"
                               "（实例名带 -1/-2 后缀，不是零件文件名）。")
                return out
            # 参考名是否"合格"（至少 实体@实例 两段）——报告 一-3。
            try:
                out["name_qualified"] = [
                    mate_name_is_qualified(str(s))
                    for s in (_ents if isinstance(_ents, (list, tuple)) else [])
                    if isinstance(s, str)]
            except Exception:
                pass

            _mate, _api, _err = self._add_mate_early(
                _mt, align=0, flip=False,
                distance_mm=distance_mm, angle_deg=angle_deg)
            _diag = getattr(self, "_last_mate_diag", None)
            if isinstance(_diag, dict):
                out["layers"] = list(_diag.get("layers") or [])
                if _diag.get("probe"):
                    out["early_probe"] = _diag.get("probe")
            if _mate is not None:
                out["ok"] = True
                out["used_api"] = _api
                try:
                    out["mate_name"] = _mate.Name
                except Exception:
                    pass
                # ══ 【配合卡死修复 · 2026-11-11】此处【不再】调 rebuild() ══════
                # 现场现象：配合环节多次出现 600s 级卡死（小屋被迫整轮重跑）。
                # 根因：create_mate 成功后无条件 self.rebuild()，而大型装配体上
                #   一次全量重建可能挂几分钟；配合本身由 SW 自动求解，
                #   调用方需要重建时自会显式 rebuild()。
                # 修复判据：配合链路（含失败重试链）一律不主动 rebuild/
                #   ForceRebuild3 —— 只报事实（mate_name/used_api），重建交给上层。
                out["rebuild_skipped"] = "配合链路不主动重建（防 600s 卡死）"
                return out
            # ── 晚期回退：老机器（SW2018~2024）上继续用旧签名 ───────────
            # ══ 【配合参数修复 · 2026-11-11】回退链必须是官方 15 参 ═══════
            # 现场现象：原回退链发 AddMate5(8 参) / AddMate3(8 参)，与官方签名
            #   （15 参 / 14 参，末位都是 ByRef ErrorStatus）都不符，
            #   必然报"类型不匹配/参数个数不对"，随后上层反复重试 → 卡死。
            # 根因：这是"猜参数个数"的历史残留（原注释还写着 14+1）。
            # 修复判据：统一 15 参 + VARIANT(VT_BYREF|VT_I4) ErrorStatus，
            #   并检查错误码（0=ErrorUnknown 失败、1=NoError 成功、其它=具体原因）。
            # 参考：https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~AddMate5.html
            #
            # ── 【BUG-9 去重】本段只在"分层链路压根没试过 late(model)"时执行 ──
            # 分层链路（_add_mate_early）已经包含晚期 15 参 AddMate5 / 14 参
            # AddMate3；若这里再原样重试一次，等于同一个 API 连打两遍 ——
            # 在 AddMate5 其实成功但判定口径不同的情况下会【重复建配合】，
            # 在必失败的情况下只是把 600s 卡死风险翻倍。因此按 diag 里的
            # 实际尝试记录决定是否补跑。
            _ad = getattr(self, "_last_mate_diag", None) or {}
            _late_already = any(
                (str((x or {}).get("layer") or "").startswith(("AddMate", "CreateMate")))
                and str((x or {}).get("holder") or "").startswith("late")
                for x in (_ad.get("layers") or []))
            _d2, _du2, _dl2, _gn2, _gd2, _a2, _au2, _al2 = self._mate_value_args(
                _mt, distance_mm, angle_deg)
            _late_errs = []
            try:
                _fn5 = getattr(self.model, "AddMate5", None)
            except Exception:
                _fn5 = None
            if _fn5 is not None and not _late_already:
                try:
                    _es_l = _byref_i4(0)
                    _m = _fn5(_mt, 0, False, _d2, _du2, _dl2, _gn2, _gd2,
                              _a2, _au2, _al2, False, False, 0, _es_l)
                    _esv_l = _byref_value(_es_l, None)
                    _ok_l, _known_l = mate_ok_by_error_status(_m, _esv_l)
                    if _ok_l and _m is not None:
                        out["ok"] = True
                        out["used_api"] = "late/AddMate5(15, ErrorStatus=%s)" % (_esv_l,)
                        out["fallback"] = True
                        return out
                    _late_errs.append("AddMate5(15): mate=%s ErrorStatus=%s(%s)"
                                      % ("None" if _m is None else "ok", _esv_l,
                                         mate_error_status_text(_esv_l)))
                except Exception as _e_l5:
                    _late_errs.append("AddMate5(15): %r" % (_e_l5,))
            elif _late_already:
                _late_errs.append("晚期 AddMate5/AddMate3 已由分层链路尝试过，跳过重复调用")
            try:
                _fn3 = getattr(self.model, "AddMate3", None)
            except Exception:
                _fn3 = None
            if _fn3 is not None and not _late_already:
                try:
                    _es_l3 = _byref_i4(0)
                    _m = _fn3(_mt, 0, False, _d2, _du2, _dl2, _gn2, _gd2,
                              _a2, _au2, _al2, False, False, _es_l3)
                    _esv_l3 = _byref_value(_es_l3, None)
                    _ok_l3, _known_l3 = mate_ok_by_error_status(_m, _esv_l3)
                    if _ok_l3 and _m is not None:
                        out["ok"] = True
                        out["used_api"] = "late/AddMate3(14, ErrorStatus=%s)" % (_esv_l3,)
                        out["fallback"] = True
                        return out
                    _late_errs.append("AddMate3(14): mate=%s ErrorStatus=%s"
                                      % ("None" if _m is None else "ok", _esv_l3))
                except Exception as _e_l3:
                    _late_errs.append("AddMate3(14): %r" % (_e_l3,))
            _es_txt = getattr(self, "_last_mate_error_status", None)
            if _es_txt:
                out["mate_error_status"] = _es_txt
            out["error"] = ("配合创建失败：%s%s"
                            % (_err, ("｜晚期回退：%s" % "；".join(_late_errs))
                               if _late_errs else ""))
            out["hint"] = ("① 确认两个参考对象都被选中（看 selections/selected_count）；"
                           "② 早期绑定不可用时本方法会自动回退，仍失败说明本版 "
                           "SolidWorks 的配合 API 与预期不同，请附 used_api 与 layers 报告。"
                           "③ 若 layers 显示每一层都失败，最长可信的降级是"
                           "「add_component 摆位 + 位姿断言 + 原生干涉验证」，"
                           "并在交付说明里写明配合未真正建立。")
            return out
        except Exception as _e:
            out["error"] = "create_mate 异常: %r" % (_e,)
            return out

    def create_lock_mate(self, entities):
        """【BUG-14】锁定配合（swMateLOCK = 16，来源 swconst.tlb 实测）。

        锁定配合把若干组件"锁"在当前位置（相对位姿固定），是总装里最省事、
        最不容易出过约束的定位手段 —— 因此文档把它列为推荐 API。

        ⚠️ 注意旧表把 'lock' 写成 9：9 在 SW 里是 swMateCAMFOLLOWER（凸轮推杆），
        照旧值调用会得到一个完全不相干的配合（或直接失败）。

        Args:
            entities: 2 个及以上组件（名字 / 组件对象 / {"component": name}）。
        Returns: 同 create_mate 的返回结构。
        """
        _ents = entities
        if _ents is not None and not isinstance(_ents, (list, tuple)):
            _ents = [_ents]
        if not _ents:
            return {"ok": False, "mate_type": "lock",
                    "error": "create_lock_mate 需要至少 2 个组件"}
        _out = self.create_mate("lock", tuple(_ents))
        _out["mate_type"] = "lock"
        _out["mate_enum"] = self._MATE_TYPE_E.get("lock", 16)
        return _out

    def add_mate(self, mate_type, comp1=None, comp2=None,
                 align=0, flip=False, distance_mm=None, angle_deg=None,
                 name="", face1=None, face2=None, mark1=1, mark2=1):
        """添加配合（Mate）。封装 AddMate5/AddMate3 的多版本签名差异。

        ══ 【H-2 修复·第七轮】按专家意见补齐官方调用流程 ══════════════════
        专家结论：AddMate5 本身【不负责选中】—— 它要求调用者在调用前
          已用 SelectByID2 选中两个待配合实体，否则返回"类型不匹配"。

        官方要求流程（本方法现已完整实现）：
          1) ClearSelection2(True)         清空当前选择
          2) SelectByID2 选中第一个面/边    （mark1，通常 1）
          3) SelectByID2 选中第二个面/边    （mark2，通常 1）
          4) AddMate5(MateType, ...)

        参考面解析优先级（face1/face2）：
          · 传 "Front Plane" 等基准面名 -> 按 PLANE 类型选中
          · 传 "组件名"                  -> 选中该组件整体
          · 传具体面字符串                -> 直接按 FACE 尝试选中
          · 传 None 且 comp 有值         -> 选中该组件（距离配合仍需一个参考面）

        Args:
            mate_type: 配合类型字符串，见下方 MATE_TYPES；或直接传 SW 枚举 int
            comp1, comp2: 两个组件名（如 "DSH_底座-1"）。可传 None 表示参考基准面
            align: 对齐方式 0=同向 1=反向 2=对齐
            flip: 是否反转配合方向
            distance_mm: 距离配合的数值（mm）
            angle_deg: 角度配合的数值（度）
            name: 配合名称（可选）

        ── 【H 修复·第六轮】内部自动完成选中，调用方不必手写 SelectByID2 ──
        测试反馈：直接调 add_mate(...) 会报"类型不匹配，需先选中要配合的
          实体/面" —— 因为 SW 的 AddMate 要求调用前【已有选中对象】。
          原实现把该前置条件甩给调用方，每次都得手写选中，极易踩坑。
        现在本方法会【自动选中】：
          · comp1 传组件名 -> 自动按名选中该组件；
          · comp2 传组件名 -> 自动选中；传 None 则自动选一个基准面作参考；
          · 选中失败时给出明确提示，而不是让 SW 抛类型不匹配。

        Returns: dict {ok, error?, used_signature?, selection?}
        """
        # ── 【BUG-14 连带修复】旧的 MATE_TYPES 表整体是错的 ──────────────
        # 原表：'concentric': 4（4 实为 swMateTANGENT）、'lock': 9
        #       （9 实为 swMateCAMFOLLOWER）、'screw': 12（12 实为 LOCKTOSKETCH）。
        # 用错枚举即使配合 API 调通，也只会建出【别的配合】或直接被拒 ——
        # 这是 BUG-11"所有配合类型都建不起来"的并列根因之一。
        # 现统一走 _MATE_TYPE_E（来源：本机 swconst.tlb 实测，见装配增强块）。
        # 保留 MATE_TYPES 这个名字做兼容（外部若引用到它不至于炸）。
        MATE_TYPES = dict(self._MATE_TYPE_E)
        out = {"ok": False, "mate_type": mate_type}
        try:
            mt, _mt_err = self._parse_mate_type(mate_type)
            if mt is None:
                out["error"] = _mt_err
                out["hint"] = ("可用: %s" % (", ".join(sorted(MATE_TYPES)),))
                return out
            self._activate_self()

            # ── 【H 修复·第六轮】自动完成"选中参考对象"这一步 ────────────────
            # 测试反馈：add_mate(...) 直接调用会报"类型不匹配，需先选中要配合的
            #   实体/面" —— 因为 AddMate 要求调用前【已有选中对象】。
            #   原实现把这个前置条件甩给调用方，导致每次都得手写 SelectByID2，
            #   极易踩坑（测试部原话）。
            # 修复：内部自动选中 ——
            #   · comp1/comp2 传组件名 → 自动按名选中该组件；
            #   · 传 None → 自动选中同名/第一个基准面作为参考；
            #   · 选中失败时给出【明确提示】，而不是让 SW 抛类型不匹配。
            # -- 官方五步流程（专家明确要求）------------------------------
            #   1) ClearSelection2(True)
            #   2) SelectByID2 选第一个面/边  (mark1)
            #   3) SelectByID2 选第二个面/边  (mark2)
            #   4) AddMate5(...)
            _sel_notes = []
            try:
                _comps = self.model.GetComponents(False) or []
            except Exception:
                _comps = []

            # 步骤 1：清空当前选择（官方要求）
            _cleared = False
            try:
                self.model.ClearSelection2(True)
                _cleared = True
            except Exception:
                try:
                    self.model.ClearSelection2(False)
                    _cleared = True
                except Exception:
                    pass
            _sel_notes.append("clear=%s" % _cleared)

            _PLANE_ALIASES = ("Front Plane", "前视基准面", "Top Plane",
                              "上视基准面", "Right Plane", "右视基准面")

            def _select_face_of_component(_c, _mark, _append):
                """【Bug-44 修复】为组件自动选中一个【真实面】作为配合参考。

                台账实测：add_mate 报"需要至少 2 个参考对象，当前 0 个"。
                根因：原 _select_one 在 comp 有值时会去 Select 整个【组件对象】，
                  但 SW 的 AddMate【不接受组件对象作为配合参考】——
                  它需要【面/边/基准面】。选中组件后 SW 报 0 个有效参考。
                修复：遍历该组件的实体，挑一个面积最大的平面，用
                  face.Select4(append, SelectData) 选中它作为配合参考。
                  选最大面是因为：装配中最常见的配合基准就是主平面
                  （底面/顶面/安装面），且大面最稳定。

                Returns: (ok, label)
                """
                try:
                    # ── 【BUG-12 修复】组件文档/实体必须走早期绑定 ───────────
                    # 原实现 _c.GetModelDoc2() / _c.GetBody2() 在 SW2025 晚期
                    # 绑定下分别报"找不到成员"和"参数数量错误"，被 except 吞成
                    # None → _blist 恒空 → 永远选不到面 → 配合"0 个参考对象"。
                    # 现在统一交给 _component_doc / _component_bodies：
                    # 前者先 early(component,'IComponent2') 再调 GetModelDoc2；
                    # 后者用 GetBodies()/GetBodies2() 再 list()（报告 2.3：
                    # IModelDoc2/IComponent2 上【没有】GetBody）。
                    # 注意 _comps_doc 只作诊断用，本函数不依赖它。
                    _comps_doc = self._component_doc(_c)
                    _blist = self._component_bodies(_c)
                    _best = None
                    _best_area = -1.0
                    for _b in _blist:
                        try:
                            _faces = _b.GetFaces()
                        except Exception:
                            continue
                        _fl = (list(_faces) if isinstance(_faces, tuple)
                               else ([_faces] if _faces else []))
                        for _f in _fl:
                            try:
                                _n = _f.Normal
                                # 只考虑平面（曲面法线不稳定）
                                if _n is None:
                                    continue
                                _area = 0.0
                                try:
                                    _p = _f.GetArea()
                                    _area = float(_p)
                                except Exception:
                                    _area = 0.0
                                if _area > _best_area:
                                    _best_area = _area
                                    _best = _f
                            except Exception:
                                continue
                    if _best is not None:
                        _sd_f = self._make_select_data(_mark)
                        for _mn, _args in (("Select4", (_append, _sd_f)),
                                           ("Select2", (_append, _mark)),
                                           ("Select", (_append,))):
                            try:
                                _fn = getattr(_best, _mn, None)
                                if _fn is None:
                                    continue
                                if _mn == "Select4" and _sd_f is None:
                                    continue
                                _fn(*_args)
                                # GetArea() 返回 m²（SW 内部单位），标签换算成 mm²
                                return True, ("FACE(comp)=%.1fmm2"
                                              % (float(_best_area) * 1e6))
                            except Exception:
                                continue
                except Exception:
                    pass
                return False, ""

            def _select_one(_spec, _comp_name, _mark, _first):
                # 选中一个配合参考：优先按面规格，其次组件，最后基准面
                _append = not _first
                if _spec and str(_spec) in _PLANE_ALIASES:
                    # ── 【BUG-11 刷选修复】装配链路改走 _select_by_id_asm ──
                    # 早期绑定下 Callout 必须是 None（报告 2.3 第 6/7 条：
                    # 传 VARIANT 报"无法转换成 COM 对象"，传 Missing 报
                    # "非选择性的参数"）；晚期绑定下恰恰相反要传 VARIANT。
                    # _select_by_id_asm 先试早期(None)、再回退晚期(_empty)。
                    if self._select_by_id_asm(str(_spec), "PLANE", 0, 0, 0,
                                              _append, _mark):
                        return True, "PLANE:%s" % _spec
                if _comp_name:
                    for _c in _comps:
                        try:
                            if str(_c.Name2) != str(_comp_name):
                                continue
                            # ── 【Bug-44 修复】先选该组件的【真实面】──────────
                            # 原实现直接 Select 组件对象 → AddMate 视为 0 个有效
                            # 参考（SW 只接受 面/边/基准面），这就是
                            # "需要至少 2 个参考对象，当前 0 个"的根因。
                            _ok_face, _lbl_face = _select_face_of_component(
                                _c, _mark, _append)
                            if _ok_face:
                                # 标签里的面积单位统一成 mm²（旧实现漏了换算，
                                # 打印出来一律 0.0mm²，排障时看不出选的是哪张面）
                                return True, "%s@%s" % (_lbl_face, _comp_name)
                            # 面选不到时退回组件对象（至少保留旧行为）
                            _sd_c2 = self._make_select_data(_mark)
                            if _sd_c2 is not None:
                                _c.Select4(_append, _sd_c2)
                            else:
                                _c.Select2(_append, _mark)
                            return True, "COMP:%s" % _comp_name
                        except Exception:
                            continue
                if _spec:
                    if self._select_by_id_asm(str(_spec), "FACE", 0, 0, 0,
                                              _append, _mark):
                        return True, "FACE:%s" % _spec
                return False, ""

            # 步骤 2：第一个参考
            _ok1, _lbl1 = _select_one(face1, comp1, mark1, _first=True)
            if _lbl1:
                _sel_notes.append(_lbl1)
            # 步骤 3：第二个参考
            _ok2, _lbl2 = _select_one(face2, comp2, mark2, _first=not _ok1)
            if _lbl2:
                _sel_notes.append(_lbl2)
            if not _ok2 and not face2 and not comp2:
                for _pl in _PLANE_ALIASES:
                    _ok3, _lbl3 = _select_one(_pl, None, mark2, _first=not _ok1)
                    if _ok3:
                        _sel_notes.append("参考面=%s" % _pl)
                        _ok2 = True
                        break

            # 步骤 4 前置校验：至少选中 2 个对象
            _sel_count = 0
            try:
                _sel_count = self.model.SelectionManager.GetSelectedObjectCount2(-1)
            except Exception:
                _sel_count = (1 if _ok1 else 0) + (1 if _ok2 else 0)
            out["selection"] = _sel_notes
            out["selected_count"] = _sel_count
            if _sel_count < 2:
                out["error"] = ("配合要求至少选中 2 个参考对象，当前仅 %d 个。"
                                % _sel_count)
                out["hint"] = ("请显式传面：add_mate(..., face1=..., face2=...)；"
                               "或传 comp1/comp2 组件名。"
                               "若只给两个组件而未指定面，无法自动选中。")
                return out

            # ── 【BUG-11 修复·核心】统一走早期绑定的 _add_mate_early ────────
            # 原实现直接 self.model.AddMate5(mt, al, flip, False, False,
            #                                val, val, name) 只有 8 个参数：
            #   · 晚期绑定下方法解析不出来 → com_error(-2147352571, 类型不匹配)；
            #    · 即便早期绑定，缺的参数位会让 pywin32 拿 None 去 float() →
            #      "float() argument must be ... not 'NoneType'"（报告 2.3-9）。
            # _add_mate_early 内部按 ①专用API ②AddMate5(14/15 参，浮点全给
            #   具体值) ③CreateMate(None) ④晚期旧签名 的顺序试，并返回
            #   真正生效的 API 名（used_api）供审计；失败时把最后 4 条原因带回。
            m, _api_used, _api_err = self._add_mate_early(
                mt, align=int(align), flip=bool(flip),
                distance_mm=distance_mm, angle_deg=angle_deg, name=name)
            if m is not None:
                out["ok"] = True
                out["used_signature"] = _api_used
                out["used_api"] = _api_used
                try:
                    out["mate_name"] = m.Name
                except Exception:
                    pass
                return out
            out["error"] = ("AddMate 失败（%s）。注意：添加配合前必须先选中"
                             "要配合的实体/面（SelectByID2），且两个组件都需已加载。"
                             % (_api_err,))
        except Exception as e:
            out["error"] = "add_mate 异常: %r" % (e,)
        return out

    def check_interference(self, include_multibody=True, treat_coincident=True):
        """干涉检查（【BUG-9】改用 InterferenceDetectionManager 为主路径）。

        ══ 旧实现的致命缺陷（报告 一-6 / 二-2d）═════════════════════════════
        `ToolsCheckInterference2` 的两个 ByRef 输出参数（PComp/PFace）在
        【晚期绑定】下无法填充，读回来永远是 None；而它的**返回计数**同样
        不可靠。旧代码却写成：

            n = _fn(...)                 # 可能返回 None
            out["count"] = int(n or 0)   # → 0
            out["ok"] = True             # → "无干涉" ✅

        于是"有干涉"被安静地报成"无干涉" —— 这是会把缺陷交付出去的
        **误报型**静默失败，比报错更危险。

        ══ 现在的判据（严格按报告）══════════════════════════════════════════
          ① 主路径：`IAssemblyDoc.InterferenceDetectionManager` →
             `IInterferenceDetectionMgr`。用 `GetInterferenceCount()` 拿计数
             （普通返回值，可当测量值），`GetInterferences()` 拿明细。
             `TreatCoincidenceAsInterference`（面贴面不算干涉）、
             `IncludeMultibodyPartInterferences=True`；
             **`Done()` 必须调用**，否则装配体卡在干涉显示模式。
          ② 明确区分三态，绝不混同：
             · ok=True,  count=0        → 真的没有干涉（原生 API 结论）
             · ok=True,  count>0        → 有干涉
             · ok=False, available=False→ 结果【不可采信】，不是"没有干涉"
             · approx=True              → 包围盒近似，仅设计层自查
          ③ `ToolsCheckInterference*` 仅作【历史兜底】，且其计数被标记
             `trustworthy=False` —— 绝不用它给出 count=0 的通过结论。

        Args:
            include_multibody: 多实体零件的内部干涉是否算（→
                IncludeMultibodyPartInterferences）。
            treat_coincident: 面贴面重合是否算干涉（→
                TreatCoincidenceAsInterference）。

        Returns:
          {ok, available, count, interferences:[...], api?, truncated?,
           trustworthy?, approx?, error?, hint?, done_called?}
        """
        out = {"ok": False, "available": False, "count": 0,
               "interferences": [], "approx": False, "trustworthy": False}
        try:
            self._activate_self()
        except Exception:
            pass

        # ══ ① 主路径：InterferenceDetectionManager ═════════════════════════
        _mgr_info = self._interference_manager()
        if _mgr_info.get("ok"):
            mgr = _mgr_info.get("manager")
            out["api"] = "InterferenceDetectionManager"
            out["available"] = True
            out["manager_holder"] = _mgr_info.get("holder")
            # 三个属性都单独设：某些版本只支持其中一部分，设不上不该中断。
            _prop_notes = []
            for _prop, _val in (
                    ("TreatCoincidenceAsInterference", bool(treat_coincident)),
                    ("IncludeMultibodyPartInterferences", bool(include_multibody)),
                    ("TreatSubAssembliesAsComponents", True)):
                try:
                    setattr(mgr, _prop, _val)
                except Exception as _e_p:
                    _prop_notes.append("%s 设不上: %r" % (_prop, _e_p))
            if _prop_notes:
                out["property_notes"] = _prop_notes
            _count = None
            _count_why = None
            # GetInterferenceCount：新版是【属性/方法】两可（报告 三-1），
            # 两种形态都试，取到就用 —— 但绝不把"取不到"当成 0。
            try:
                _v = getattr(mgr, "GetInterferenceCount")
                if callable(_v):
                    _count = int(_v())
                    out["count_method"] = "方法调用（带括号）"
                else:
                    _count = int(_v)
                    out["count_method"] = "属性访问（无括号）"
            except Exception as _e_c:
                _count_why = repr(_e_c)
            if _count is None:
                out["ok"] = False
                out["available"] = False
                out["trustworthy"] = False
                out["error"] = ("InterferenceDetectionManager 取到了，但 "
                                "GetInterferenceCount 两种形态都读不出来"
                                "（%s）—— 结果不可采信，不要当作没有干涉。"
                                % (_count_why,))
                self._interference_done(mgr, out)
                return out
            out["count"] = int(_count)
            out["trustworthy"] = True
            # 明细（GetInterferences 返回 IInterference 数组；0 个时可能是 None）
            if _count:
                try:
                    _items = mgr.GetInterferences()
                    out["interferences"] = self._interference_details(_items)
                except Exception as _e_i:
                    out["interferences_error"] = repr(_e_i)
                    out["interferences_note"] = (
                        "计数可用但明细取不到 —— 干涉确实存在（count>0），"
                        "请到 SW 界面 评估-干涉检查 查看具体位置。")
            self._interference_done(mgr, out)
            out["ok"] = True
            return out

        # 主路径不可用 → 记录原因，继续走历史兜底
        out["manager_error"] = _mgr_info.get("error")
        out["manager_attempts"] = _mgr_info.get("attempts")

        # ══ ② 历史兜底：ToolsCheckInterference（无参，返回值本身可当计数）══
        #  仅当它能【直接返回一个 int】时才采用；ToolsCheckInterference2/3
        #  的 ByRef 出参在晚期绑定下填不进去（报告 一-6），
        #  它的计数一律标 trustworthy=False，绝不据此报"无干涉"。
        comps = []
        try:
            comps = self.model.GetComponents(False) or []
        except Exception:
            comps = []
        _comp_list = list(comps) if isinstance(comps, tuple) else (
            [comps] if comps else [])
        _holders = []
        try:
            _asm_e = self._wrap_with_interface(self.model, "IAssemblyDoc")
            if _asm_e is not None:
                _holders.append(("early(IAssemblyDoc)", _asm_e))
        except Exception:
            pass
        _holders.append(("late(model)", self.model))
        _sel_tried = []
        for _hname, _h in _holders:
            try:
                _fn = getattr(_h, "ToolsCheckInterference", None)
            except Exception:
                _fn = None
            if _fn is None:
                continue
            try:
                n = _fn()
            except Exception as _e_a:
                _sel_tried.append("%s/ToolsCheckInterference(): %r"
                                  % (_hname, _e_a))
                continue
            if n is None:
                # 返回值都是 None → 这个 API 在本环境不产出可用结论
                _sel_tried.append("%s/ToolsCheckInterference(): 返回 None"
                                  % (_hname,))
                continue
            out["api"] = "ToolsCheckInterference(legacy)"
            out["available"] = True
            out["holder"] = _hname
            out["count"] = int(n)
            out["trustworthy"] = True
            out["legacy_api"] = True
            out["ok"] = True
            out["note"] = ("使用历史 API ToolsCheckInterference 得到的计数"
                           "（返回值形态可靠）。建议随后在 SW 界面复核。")
            return out

        # ══ ③ 明确失败：绝不给出 count=0 的"通过"结论 ══════════════════════
        # 报告 一-6 的核心教训：ToolsCheckInterference2 读回 None 时若当成 0，
        # 就是把"有干涉"误报成"无干涉"。这里把底层原因一并回传。
        out["error"] = ("本 SolidWorks 版本无可用的原生干涉检查 API"
                        "（InterferenceDetectionManager: %s；"
                        "ToolsCheckInterference: %s）"
                        % (out.get("manager_error"),
                           "；".join(_sel_tried[-3:]) or "无该 API"))
        out["attempts"] = _sel_tried
        out["trustworthy"] = False
        return out

    def _interference_manager(self):
        """取 IInterferenceDetectionMgr（报告 一-6：IAssemblyDoc 上的属性）。

        ⚠️ 关键：绝【不】用 `ToolsCheckInterference2`——它在晚期绑定下两个
        ByRef 出参填不进去，永远读回 None（见 check_interference 说明）。
        这里按 早期接口 → 晚期 model → 属性式/方法式 的顺序取，
        全部失败返回 {ok:False, error:...}，绝不抛异常。
        """
        _info = {"ok": False, "manager": None, "holder": None,
                 "error": None, "attempts": []}
        _m = getattr(self, "model", None)
        if _m is None:
            _info["error"] = "model 为 None"
            return _info
        _holders = []
        try:
            _asm_e = self._wrap_with_interface(_m, "IAssemblyDoc")
            if _asm_e is not None:
                _holders.append(("early(IAssemblyDoc)", _asm_e))
        except Exception as _e:
            _info["attempts"].append("IAssemblyDoc 包装失败: %r" % (_e,))
        _holders.append(("late(model)", _m))
        for _hname, _h in _holders:
            for _how in ("attr", "call"):
                try:
                    if _how == "attr":
                        _mgr = getattr(_h, "InterferenceDetectionManager")
                    else:
                        _mgr = _h.InterferenceDetectionManager()
                except Exception as _e2:
                    _info["attempts"].append("%s/%s: %r" % (_hname, _how, _e2))
                    continue
                if _mgr is None:
                    _info["attempts"].append("%s/%s: 返回 None"
                                             % (_hname, _how))
                    continue
                _info.update({"ok": True, "manager": _mgr,
                              "holder": "%s/%s" % (_hname, _how)})
                return _info
        _info["error"] = ("拿不到 InterferenceDetectionManager（%s）"
                          % ("；".join(_info["attempts"][-3:]) or "无候选"))
        return _info

    def _interference_done(self, mgr, out=None):
        """调 IInterferenceDetectionMgr.Done()（报告 一-6：必须调）。

        不调的话装配体卡在"干涉显示模式"：所有干涉体积被高亮，后续
        截图/出图全是花的。失败只记录，不抛异常。
        """
        _ok = False
        try:
            _fn = getattr(mgr, "Done", None)
            if _fn is not None:
                _fn()
                _ok = True
        except Exception as _e:
            if isinstance(out, dict):
                out["done_error"] = repr(_e)
        if isinstance(out, dict):
            out["done_called"] = bool(_ok)
        return _ok

    @staticmethod
    def _interference_details(items, limit=50):
        """把 GetInterferences() 的返回值整理成结构化明细（上限 limit 条）。

        报告 二-2d 的接口细节：
          · `IInterference.Volume`        是【属性】（不带括号），单位 m³；
          · `IInterference.GetComponentCount()` 是【方法】（带括号）；
          · 组件列表是 `.Components` 【属性】，不是 `.GetComponents()`。
        逐项 try —— 任何一项取不到都不该让整个检查失败。
        """
        _out = []
        if items is None:
            return _out
        _arr = items if isinstance(items, (list, tuple)) else [items]
        for _it in _arr[:max(0, int(limit))]:
            if _it is None:
                continue
            _rec = {}
            try:
                _v = _it.Volume
                _rec["volume_m3"] = float(_v)
                _rec["volume_mm3"] = float(_v) * 1e9
            except Exception as _e:
                _rec["volume_error"] = repr(_e)
            try:
                _fn = getattr(_it, "GetComponentCount", None)
                _rec["component_count"] = int(_fn()) if callable(_fn) else None
            except Exception as _e2:
                _rec["component_count_error"] = repr(_e2)
            try:
                _comps = getattr(_it, "Components", None)
                if _comps is not None:
                    _cl = (_comps if isinstance(_comps, (list, tuple))
                           else list(_comps))
                    _names = []
                    for _c in _cl:
                        try:
                            _names.append(str(getattr(_c, "Name2", None)
                                              or getattr(_c, "Name", "")
                                              or "?"))
                        except Exception:
                            continue
                    _rec["components"] = _names
            except Exception as _e3:
                _rec["components_error"] = repr(_e3)
            _out.append(_rec)
        return _out


    def geometry_interference_approx(self, min_overlap_mm=0.05):
        """【Bug-30 修复】几何近似的干涉检查（API 全部失效时的兜底）。

        原理：对每对组件的【世界坐标包围盒】求交，交集体积超过阈值即报疑似干涉。
        局限：包围盒是保守近似 —— 曲面/斜置零件的包围盒会重叠但不一定真干涉，
        因此结果标注为 approx=True，仅用于"设计层自查"，不能替代 SW 原生检查。

        Returns: {ok, approx:True, count, pairs:[{a,b,overlap_mm3}], note}
        """
        out = {"ok": False, "approx": True, "count": 0, "pairs": []}
        boxes = []
        try:
            self._activate_self()
            comps = self.model.GetComponents(False) or []
            clist = list(comps) if isinstance(comps, tuple) else ([comps] if comps else [])
        except Exception:
            clist = []
        for c in clist:
            try:
                name = str(getattr(c, "Name2", None) or getattr(c, "Name", "") or "?")
            except Exception:
                name = "?"
            try:
                box = c.GetBox(False, False)
            except Exception:
                box = None
            if not box or len(box) < 6:
                continue
            try:
                xs = [float(box[0]), float(box[3])]
                ys = [float(box[1]), float(box[4])]
                zs = [float(box[2]), float(box[5])]
                boxes.append((name, min(xs), max(xs), min(ys), max(ys),
                              min(zs), max(zs)))
            except Exception:
                continue
        _tol = float(min_overlap_mm) * 0.001
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a[2], b[2]) - max(a[1], b[1])
                oy = min(a[4], b[4]) - max(a[3], b[3])
                oz = min(a[6], b[6]) - max(a[5], b[5])
                if ox > _tol and oy > _tol and oz > _tol:
                    out["pairs"].append({
                        "a": a[0], "b": b[0],
                        "overlap_mm": [round(ox * 1000, 3), round(oy * 1000, 3),
                                       round(oz * 1000, 3)],
                        "overlap_mm3": round(ox * oy * oz * 1e9, 3),
                    })
        out["count"] = len(out["pairs"])
        out["ok"] = True
        out["note"] = ("包围盒近似：count>0 仅表示包围盒重叠（可能因斜置/曲面误报），"
                       "count=0 可较有把握地认为无干涉。"
                       "精确结论请在 SW 界面执行 评估-干涉检查（Bug-30 兜底）。")
        return out

    def circular_pattern(self, count, angle_deg=360.0, equal_spacing=True,
                         axis="Z", reverse=False, geometry_pattern=False):
        """环形阵列（FeatureCircularPattern5 / 4）。

        ⚠️【C11 说明】SW 的 FeatureCircularPattern5 参数签名在 2018~2025 间多次变动，
          直接调用极易出现"参数无效"。本方法按多种签名依次尝试，并把
          失败原因原样返回，避免静默失败。
          若多次失败，建议改用【单草图多段线轮廓一次拉伸/切除】绕过阵列。

        Args:
            count: 实例总数（含原始特征）
            angle_deg: 总角度，默认 360
            equal_spacing: 等间距
            axis: 'X'/'Y'/'Z' 或基准轴名。默认 Z
            reverse: 反向
            geometry_pattern: True=只阵列几何体（不合并）

        Returns: dict {ok, used_signature?, error?, hint?}
        """
        out = {"ok": False, "count": int(count), "angle_deg": float(angle_deg)}
        try:
            self._activate_self()
            _ang = float(angle_deg) * 3.141592653589793 / 180.0
            _rev = bool(reverse)
            _eq = bool(equal_spacing)
            _geo = bool(geometry_pattern)
            last_err = None
            # 依次尝试已知的几种签名（参数个数从多到少）
            tries = [
                ("FeatureCircularPattern5", (int(count), _ang, _rev, False, False,
                                                _eq, 0.0, 0.0, False, True, False, False, False)),
                ("FeatureCircularPattern5", (int(count), _ang, _rev, _eq,
                                                _geo, False, False)),
                ("FeatureCircularPattern4", (int(count), _ang, _rev, _eq,
                                                _geo, False)),
            ]
            for _sig, _args in tries:
                try:
                    fn = getattr(self.model, _sig)
                except Exception as e:
                    last_err = e; continue
                try:
                    feat = fn(*_args)
                except Exception as e:
                    last_err = e; continue
                if feat is not None:
                    out["ok"] = True
                    out["used_signature"] = _sig
                    return out
            out["error"] = "环形阵列失败: %r" % (last_err,)
            out["hint"] = ("FeatureCircularPattern5 参数签名不匹配时，"
                            "可改用【单草图多段线轮廓一次拉伸】绕过阵列。")
        except Exception as e:
            out["error"] = "circular_pattern 异常: %r" % (e,)
        return out
    # ══ 【C8 / H 修复】装配体创建（Wave2 入口）════════════════════════
    @staticmethod
    def new_assembly(sw=None, template=None):
        """新建装配体文档并返回 SWModel 包装。

        ── 【H 修复】原签名要求必须传 sw，导致常见误用全部失败 ────────
        测试反馈：小屋报 module has no attribute new_assembly。
        该报错实际来源是【调用方式不匹配】，有三种典型误用：
          1) swapi.new_assembly(...)    -> 模块级没这个函数（原实现只在类里）
          2) swapi.new_assembly()       -> 少传 sw，TypeError
          3) SWModel.new_assembly(sw)   -> 这种本来是对的
        修复：三种调用方式全部支持 ——
          · sw=None 时【自动取当前 SW 连接】（等价于 get_sw()）；
          · 在模块级补一个同名别名，使 swapi.new_assembly() 也可用。

        Args:
            sw: SldWorks.Application；None 时自动获取当前连接
            template: 装配体模板路径；None 时自动探测

        Returns: SWModel 实例（失败抛 RuntimeError，含可操作提示）
        """
        if sw is None:
            try:
                sw = get_sw()
            except Exception as e:
                raise RuntimeError(
                    "new_assembly 未传 sw，且自动连接 SolidWorks 失败: %r。"
                    "请显式传入已连接的应用对象，或先确保 SW 已启动。" % (e,))
        tmpl = template
        if not tmpl:
            try:
                tmpl = get_asm_template(sw)
            except Exception:
                tmpl = None
        if not tmpl:
            # 兜底：让 SW 用默认装配模板新建
            try:
                doc = sw.NewDocument("", 0, 0.0, 0.0)
            except Exception:
                doc = None
            if doc is None:
                raise RuntimeError(
                    "无法新建装配体：未找到装配体模板(.asmdot)。"
                    "请先在 SolidWorks 里手动保存一个装配体模板，"
                    "或用 sw.new(template=<模板路径>) 显式指定。")
            return SWModel(sw, doc)
        _errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = None
        try:
            doc = sw.NewDocument(tmpl, 0, 0.0, 0.0)
        except Exception:
            doc = None
        if doc is None:
            raise RuntimeError("无法用模板新建装配体: %s" % tmpl)
        return SWModel(sw, doc)

    # ══ 【BUG-H / BUG-I 修复】late-binding 下两个高危静默失败点 ════════
    # 这两个 API 在当前代码里尚未使用，属于预防性封装 ——
    #   一旦后续用裸 COM 写装配定位/阵列数据，必踩。
    #   这里把"静默失败"变成"显式报错"。


    def _make_math_transform(self, x=0.0, y=0.0, z=0.0, units="mm"):
        r"""【Bug-30 修复】正确构造 MathTransform。

        ══ 根因（实测 Bug-30）══════════════════════════════════════════════
        原实现写的是 sw.CreateTransform([...]) —— 【这个方法不存在】，
        于是 add_component 的补救摆位、set_component_transform 全部失效，
        表现为"CreateTransform 不存在 → SetTransform / MoveComponent 全部不可用"
        （Bug-30 现象 2），装配体只能堆在原点。

        正确做法（SolidWorks API）：MathTransform 必须由 IMathUtility 创建：
            mu = sw.GetMathUtility()        # 或 sw.IGetMathUtility()
            mt = mu.CreateTransform(array16) # 16 个 double

        本方法按优先级尝试多种取 MathUtility 的写法，并支持直接给 16 元数组。
        Returns: (math_transform | None, diagnostic_str)
        """
        _k = 0.001 if str(units).lower() == "mm" else 1.0
        # ── 【装配定位修复 R2】矩阵布局必须用 SolidWorks 官方排布 ──────────
        # 原实现按 .NET 行优先 4x4 写（平移塞在索引 3/7/11），SolidWorks 的
        # IMathTransform.ArrayData 却是"0..8 旋转 / 9..11 平移 / 12..15 尾行"，
        # 于是这套矩阵喂给 SW 后平移要么被丢弃、要么错位 —— 这正是
        # "期望 [0,0,90] 实际 [0,0,10]"的来源之一。
        # 现在统一走 _translation_matrix_m16（模块级，见其 docstring）。
        arr = _translation_matrix_m16(x, y, z, _k)
        return self._make_math_transform_from_array(arr), ""

    # ══ 【Bug5 修复·类型库缓存】SW2025 的 MathUtility 必须用前期绑定 ══════
    # 根因（测试部实机诊断 + 本次验证）：
    #   sw.GetMathUtility（属性式）能取到对象，但 late-binding 下其类型为
    #   <unknown>（mu._username_='<unknown>'），pywin32 无法解析类型库，
    #   导致 CreatePoint/CreateVector/CreateTransform/CreateArray 全部报
    #   com_error(-2147352573, '找不到成员') = DISP_E_MEMBERNOTFOUND。
    #   gencache.EnsureDispatch('SldWorks.Application') 也直接失败：
    #     TypeError('This COM object can not automate the makepy process')
    #
    # 已验证的解决办法（手动生成类型库缓存）：
    #   pythoncom.LoadTypeLib(<sldworks.tlb>) 可成功加载（1015 个类型），
    #   再用 gencache.EnsureModuleForTypelibInterface(tl) 生成缓存模块
    #   （得到 win32com.gen_py.83A33D31-...x0x33x0，内含 IMathUtility 类）。
    #   生成后用该模块的 IMathUtility 包装 GetMathUtility 对象，
    #   即可走前期绑定调用 CreateArray/CreateTransform。

    def _sw_type_lib_module(self):
        """确保 SldWorks 类型库缓存已生成，并返回该 gen_py 模块。

        先尝试直接取缓存；失败则用 LoadTypeLib + EnsureModuleForTypelibInterface
        现场生成（这一步正是 makepy 手动生成缓存的正规入口）。
        Returns: module | None
        """
        try:
            from win32com.client import gencache
        except Exception:
            return None
        _iid = SW_TLB_IID
        _lcid, _maj, _min = SW_TLB_VER
        # ① 缓存已在：直接取
        try:
            _m = gencache.GetModuleForTypelib(_iid, _lcid, _maj, _min)
            if _m is not None and getattr(_m, "IMathUtility", None) is not None:
                return _m
        except Exception:
            pass
        # ② 未生成 → 现场加载 .tlb 并生成缓存
        _tlb = _find_sldworks_tlb()
        if not _tlb:
            self._last_transform_diag = "未找到 sldworks.tlb，无法生成类型库缓存"
            return None
        try:
            import pythoncom
            _tl = pythoncom.LoadTypeLib(_tlb)
            # 该入口接受类型库对象，绕过"库没有注册"的注册表查询问题
            _m = gencache.EnsureModuleForTypelibInterface(
                _tl, bForDemand=0, bBuildHidden=1)
            if _m is not None:
                return _m
        except Exception as _e_gen:
            self._last_transform_diag = ("类型库缓存生成失败: %r" % (_e_gen,))
        return None

    def _wrap_with_interface(self, obj, iface_name):
        """把 COM 对象包装成【指定接口】的前期绑定对象（Bug5 根因②修复）。

        为什么不能直接用 cls(obj)：
          DispatchBaseClass.__init__ 只在 isinstance(obj, (DispatchBaseClass,
          _PyIDispatchType)) 时才做 QueryInterface，且失败(E_NOINTERFACE)时
          【静默保留原对象】。于是得到的是"影子类" —— 方法签名能解析，
          但 self._oleobj_ 仍是 late-binding 的 <unknown> 对象，
          调用时 self._oleobj_.InvokeTypes(...) 直接 AttributeError
          （测试部实测：AttributeError('<unknown>.InvokeTypes')）。

        正确做法：显式 QueryInterface 到【接口 IID】得到真正的接口指针，
          再用 __new__ + 直接写 _oleobj_ 构造实例（绕过 __init__ 的降级）。

        Args:
            obj: 原始 COM 对象（可能是 CDispatch/<unknown>）。
            iface_name: 生成的缓存模块里的接口类名（如 'IMathUtility'）。
        Returns: 包装后的对象，失败返回 None。
        """
        try:
            _mod = self._sw_type_lib_module()
            if _mod is None:
                return None
            _cls = getattr(_mod, iface_name, None)
            if _cls is None:
                return None
            _iid = getattr(_cls, "CLSID", None)
            # 取底层 PyIDispatch
            _raw = getattr(obj, "_oleobj_", obj)
            if _iid is not None and hasattr(_raw, "QueryInterface"):
                try:
                    import pythoncom as _pyc
                    _qi = _raw.QueryInterface(_iid, _pyc.IID_IDispatch)
                    if _qi is not None:
                        # __new__ 绕过 __init__，直接挂上正确的接口指针
                        _inst = _cls.__new__(_cls)
                        _inst.__dict__["_oleobj_"] = _qi
                        return _inst
                except Exception:
                    pass
            # 退路：直接 __new__ + 原对象（至少方法签名可用，能否 Invoke 看运气）
            try:
                _inst2 = _cls.__new__(_cls)
                _inst2.__dict__["_oleobj_"] = _raw
                return _inst2
            except Exception:
                return None
        except Exception:
            return None

    def _make_math_transform_from_array(self, arr):
        """用 16 元数组构造 MathTransform（【Bug-30】）。

        依次尝试：
          ① sw.GetMathUtility().CreateTransform(arr)
          ② sw.IGetMathUtility().CreateTransform(arr)
          ③ sw.GetMathUtility.CreateTransform(arr)   （属性式 late-binding）
        """
        _sw = getattr(self, "sw", None)
        if _sw is None:
            return None
        # ── 【Bug5 修复·按测试部实机诊断的正确签名】──────────────────────
        # 实测（SW2025 SP5.0）：
        #   · sw.GetMathUtility 必须【属性式】（无括号）—— 能取到对象；
        #     而 sw.GetMathUtility() / IGetMathUtility 会报
        #     "找不到成员"/"无法读只写属性"。
        #   · 取到的 MathUtility 类型库无法解析（_username_='<unknown>'），
        #     所以【不能】传裸 list/variant —— 任何单参形态都报
        #     com_error(-2147352573, '找不到成员')。
        #   · 正确调用是【两参】：CreateTransform(Units, IMathArray)，
        #        Units = swMathUnits 枚举（swMETER = 2）
        #        Data  = IMathArray，须先 CreateArray(swArrayDouble=1)
        #                再用 SetData16([16 doubles]) 填充。
        #   诊断来源：测试部 2026-10-02 实机报告（Bug 5）。
        _mu = None
        for _getter in (lambda: _sw.GetMathUtility,          # 属性式（首选）
                        lambda: _sw.GetMathUtility(),
                        lambda: _sw.IGetMathUtility):
            try:
                _mu = _getter()
                if _mu is not None:
                    break
            except Exception:
                continue
        if _mu is None:
            self._last_transform_diag = "GetMathUtility 取不到对象"
            return None
        # ── 【Bug5 修复·根因②】必须做【真正的接口 QueryInterface】─────────
        # 测试部实测：用 cls(_mu) 包装后调用仍报
        #   AttributeError('<unknown>.InvokeTypes')
        # 原因：DispatchBaseClass.__init__ 的 QueryInterface 失败时【静默保留】
        #   原 <unknown> 对象 —— 得到的是"影子类"，签名可解析但底层类型未变。
        # 修复：改用 _wrap_with_interface()（显式 QueryInterface 到接口 IID，
        #   并用 __new__ 直接挂 _oleobj_，不经过会降级的 __init__）。
        # ── 【装配定位修复 R2】只对【真 COM 对象】做早期绑定包装 ────────────
        # _wrap_with_interface 的"退路"分支会给任何对象套一个 gencache 影子类
        # （__new__ + 直接挂 _oleobj_）。对真 COM 对象没问题，但如果 _mu 不是
        # COM（例如测试用的假对象），影子类里所有调用都会走 InvokeTypes →
        # AttributeError，反而把本来可用的 late-binding 调用路径废掉。
        # 判据：pywin32 的真实 COM 对象一定带 _oleobj_。
        if hasattr(_mu, "_oleobj_"):
            _early = self._wrap_with_interface(_mu, "IMathUtility")
        else:
            _early = None
        _mu_late = _mu
        if _early is not None:
            _mu = _early
            self._last_transform_diag = "early-binding(IMathUtility/QueryInterface)"
        else:
            self._last_transform_diag = "late-binding(IMathUtility 包装失败)"
        _diag = []
        _vals = [float(v) for v in arr]
        # ══ 【装配定位修复 R2】路径 0（权威·首选）：单参数 16 元数组 ═════════
        # 【类型库】IMathUtility 只有两个入口，签名都是【一个参数】：
        #     CreateTransform(ArrayDataIn)   'Creates a new transform matrix object'
        #     ICreateTransform(ArrayDataIn)
        #   —— 没有任何 (Units, IMathArray) 的两参重载。历史实现把主路径写成
        #      CreateTransform(2, IMathArray)，必然抛"参数个数不符"，然后靠
        #      兜底的乱试才偶尔成功 —— 这就是"摆位时灵时不灵"的来源。
        # 传参形态按 pywin32 的常见坑排序：list → VARIANT(VT_ARRAY|VT_R8) → tuple。
        # （VARIANT 分支放前面：late-binding 下裸 list 有时不自动封送成 SAFEARRAY。）
        _single_arg_candidates = []
        try:
            _single_arg_candidates.append(("variant_r8", win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, list(_vals))))
        except Exception:
            pass
        _single_arg_candidates.append(("list", list(_vals)))
        _single_arg_candidates.append(("tuple", tuple(_vals)))
        for _mk in ("CreateTransform", "ICreateTransform"):
            try:
                _fn = getattr(_mu, _mk, None)
            except Exception:
                _fn = None
            if _fn is None:
                continue
            for _aname, _av in _single_arg_candidates:
                try:
                    _mt = _fn(_av)
                except Exception as _e0:
                    _diag.append("%s/%s: %r" % (_mk, _aname, _e0))
                    continue
                if _mt is not None:
                    # 读回自检：构造出来的矩阵必须能读回 16 元数组，
                    # 否则它就是个"看着像成功"的空壳（宁可继续换形态）。
                    if _transform_array(_mt) is not None:
                        self._last_transform_diag = "%s/%s(单参数16元)" % (_mk, _aname)
                        return _mt
                    _diag.append("%s/%s: 返回对象读不出 ArrayData" % (_mk, _aname))
        # ── 路径 1（历史兼容·无类型库依据）：CreateArray(1)+SetData16+CreateTransform(2,arr)
        try:
            _arr_obj = None
            for _mk in ("CreateArray", "ICreateArray"):
                try:
                    _fn = getattr(_mu, _mk, None)
                    if _fn is None:
                        continue
                    _arr_obj = _fn(1)          # swArrayDouble = 1
                    if _arr_obj is not None:
                        break
                except Exception as _e_ca:
                    _diag.append("%s: %r" % (_mk, _e_ca))
                    continue
            if _arr_obj is not None:
                # SetData16 用 16 个 double 填充（不足补 0，超出截断）
                _d16 = (_vals + [0.0] * 16)[:16]
                for _sm in ("SetData16", "ISetData16", "SetData"):
                    try:
                        _sfn = getattr(_arr_obj, _sm, None)
                        if _sfn is None:
                            continue
                        _sfn(_d16)
                        break
                    except Exception as _e_sd:
                        _diag.append("%s: %r" % (_sm, _e_sd))
                        continue
                for _mk in ("CreateTransform", "ICreateTransform"):
                    try:
                        _fn = getattr(_mu, _mk, None)
                        if _fn is None:
                            continue
                        # swMETER = 2（长度单位：米，与 swapi 内部单位一致）
                        _mt = _fn(2, _arr_obj)
                        if _mt is not None:
                            self._last_transform_diag = (
                                "%s(2, IMathArray/CreateArray+SetData16)" % _mk)
                            return _mt
                    except Exception as _e_ct:
                        _diag.append("%s(2,arr): %r" % (_mk, _e_ct))
                        continue
        except Exception as _e_path1:
            _diag.append("path1: %r" % (_e_path1,))
        # ── 路径 2（兼容旧版，保留）【装配定位修复 R2】──────────────────
        # 说明：这三种单参形态已提到"路径 0"先试（那里是权威路径）。
        # 这里【原样保留】作为最后兜底：某些版本的 late-binding 只认
        # 裸 list / tuple 而不认 VARIANT，多试一遍成本极低。
        _arr_candidates = [("list", list(_vals))]
        try:
            import win32com.client as _w32c
            import pythoncom as _pyc
            _arr_candidates.append(("variant_r8", _w32c.VARIANT(
                _pyc.VT_ARRAY | _pyc.VT_R8, list(_vals))))
        except Exception:
            pass
        _arr_candidates.append(("tuple", tuple(_vals)))
        for _mk in ("CreateTransform", "ICreateTransform"):
            try:
                _fn = getattr(_mu, _mk, None)
            except Exception:
                continue
            if _fn is None:
                continue
            for _aname, _av in _arr_candidates:
                try:
                    _mt = _fn(_av)
                    if _mt is not None:
                        self._last_transform_diag = "%s/%s" % (_mk, _aname)
                        return _mt
                except Exception as _e_mt:
                    _diag.append("%s/%s: %r" % (_mk, _aname, _e_mt))
                    continue
        self._last_transform_diag = "; ".join(_diag[-5:])
        return None

    def set_component_transform(self, comp, x=0.0, y=0.0, z=0.0, units="mm"):
        """设置组件位置（诊断 + 可选写入）。

        ══ 【BUG-H 修复·根因已由测试部定位】══════════════════════════════
        实测结论（装配态 late-binding 下）：
          · comp.Transform2 = mt        -> 赋值失败（该属性只读）
          · comp.SetTransform(mt)       -> 该方法【不存在】(AttributeError)
          · comp.GetTotalTransform(True)-> 【可用】（注意必须传 True）

        所以"事后摆位"这条路走不通。正确做法（按优先级）：
          1) 建组件时就带坐标：add_component(path, x, y, z)  <- 推荐
          2) 用装配配合定位：add_mate(...)
          3) 删除组件后按目标坐标重新添加
        本方法保留为：读回当前位置 + 对支持的版本尝试写入 + 明确报不支持。

        Returns: {ok, applied, supported?, position_mm?, after_mm?, error?, hint?}
        """
        out = {"ok": False, "applied": False}
        if comp is None:
            out["error"] = "组件对象为空"
            return out
        _k = 0.001 if str(units).lower() == "mm" else 1.0

        # 读回当前位置（GetTotalTransform(True) 实测可用）
        try:
            mt = None
            try:
                mt = comp.GetTotalTransform(True)
            except Exception:
                try:
                    mt = comp.GetTotalTransform()
                except Exception:
                    mt = None
            if mt is not None:
                out["supported"] = True
                try:
                    # 【装配定位修复 R2】平移在 9/10/11（见 SW_MATRIX_LAYOUT）
                    arr = _transform_array(mt)
                    _pos = _position_from_transform_mm(mt, k=_k) if arr else None
                    if _pos:
                        out["position_mm"] = [round(float(v), 4) for v in _pos]
                except Exception:
                    pass
        except Exception:
            out["supported"] = False

        # ── 【Bug-30 修复】先正确构造 MathTransform，再判断写入路径 ────────
        # 原实现用 sw.CreateTransform —— 该方法不存在，导致这里必然失败。
        # 现在改用 IMathUtility.CreateTransform（见 _make_math_transform_from_array）。
        # 【装配定位修复 R2】改用官方布局的平移矩阵（平移在 9/10/11）
        new_mt = self._make_math_transform_from_array(
            _translation_matrix_m16(x, y, z, _k))
        out["math_transform_available"] = new_mt is not None
        if new_mt is None:
            out["error"] = ("无法构造 MathTransform（GetMathUtility().CreateTransform 不可用）。"
                            "这是 SW 装配摆位的前提 —— 请确认装配体文档处于活动状态。")
            out["hint"] = ("请改用下列方式之一（按可靠性排序）："
                           " 1) add_component(path, x, y, z) 建组件时直接给坐标；"
                           " 2) add_mate(...) 用装配配合定位（工程上更规范）；"
                           " 3) place_components_by_coords(...) 批量按坐标矩阵摆位。")
            return out

        # ══ 【Bug-44 修复】首选【装配体文档级】TransformComponent ═══════════
        # 台账实测（SW 2025 SP5.0）明确要求："不要在'写对代码'层面反复打补丁，
        #   应真正验证坐标在运行时生效"。
        # 根因分析：组件对象（Component2）在 late-binding 下【只读】——
        #   SetTransform / SetTransform2 / MoveComponent 全部不存在或无效。
        #   但 SolidWorks 的正确 API 是【装配体文档级】的：
        #       asmDoc.TransformComponent(components, transform, ...)
        #   它接收"组件数组 + MathTransform"，由装配体统一改写组件位姿。
        #   这是官方文档中"移动组件"的标准途径，此前从未被使用（全库 0 引用）。
        # 因此把 TransformComponent 提为【第一优先】写入路径。
        _write_ok = False
        _write_method = None
        # ══ 【装配定位修复 R2】首选【类型库确认存在】的 IComponent2.Transform2 ══
        # 【类型库】83A33D31-...(SW 自注册) 里 IComponent2 的写入入口有：
        #     · "Transform2"：_prop_map_get_ 与 _prop_map_put_ 都有
        #       (78, LCID, 4, 0) —— 即【可读可写】的 MathTransform 属性
        #     · SetXformAndSolve(XformIn) / SetTransformAndSolve3(XformIn, ThisConfiguration)
        #   而历史实现首选的 self.model.TransformComponent(...)（4 种猜参）
        #   在整份类型库中【0 次出现】（Select-String 'TransformComponent' = 0），
        #   属于不存在的成员 —— 每次都在这里白白烧掉一轮 COM 异常。
        # 因此改为：Transform2 → SetXformAndSolve → SetXformAndSolve2 →
        #   SetXformAndSolve3(True)，全部按类型库签名给参。
        # 说明：本版(2025) 组件一旦被配合/固定约束定义，写入仍可能被求解器
        #   忽略 —— 所以下面【必须】读回校验，读不回来就不许报成功。
        for _mn, _arg in (("Transform2", True),
                          ("SetXformAndSolve", False),
                          ("SetXformAndSolve2", False),
                          ("SetXformAndSolve3", True)):
            try:
                if _arg:
                    _r = setattr(comp, _mn, new_mt) if _mn == "Transform2" \
                        else getattr(comp, _mn)(new_mt, True)
                else:
                    _r = getattr(comp, _mn)(new_mt)
            except Exception as _e_w:
                out.setdefault("write_errors", []).append("%s: %r" % (_mn, _e_w))
                continue
            _write_ok = True
            _write_method = _mn
            out["write_return"] = repr(_r)[:80]
            break

        # ── 【装配定位修复 R2】TransformComponent 降为兜底（逻辑原样保留）──────
        # 类型库里【没有】TransformComponent 这个方法（'TransformComponent'
        #   在 6.2MB 类型库中出现 0 次），所以它最多是某些版本额外暴露的成员。
        # 历史实现把它当【第一优先】，每次都要先烧掉 4 轮 COM 异常；
        #   现在只要上面按类型库签名写的 Transform2/SetXformAndSolve* 有一个
        #   写成（_write_ok=True），这里就跳过；否则仍然照原样试一遍。
        try:
            _tc = None if _write_ok else getattr(
                self.model, "TransformComponent", None)
            if _tc is not None:
                # 先选中该组件（TransformComponent 需要组件数组）
                try:
                    self.model.ClearSelection2(True)
                except Exception:
                    pass
                _selected = False
                for _sm in ("Select4", "Select2", "Select"):
                    try:
                        _fn = getattr(comp, _sm, None)
                        if _fn is None:
                            continue
                        if _sm == "Select4":
                            _fn(True, None)
                        elif _sm == "Select2":
                            _fn(True, 0)
                        else:
                            _fn(True)
                        _selected = True
                        break
                    except Exception:
                        continue
                if _selected:
                    _comps = None
                    try:
                        _comps = self.model.GetComponents(True)
                    except Exception:
                        _comps = None
                    if _comps is None:
                        _comps = [comp]
                    # TransformComponent(components, transform, moveType, ...)
                    for _args in ((_comps, new_mt),
                                  (_comps, new_mt, 0),
                                  (_comps, new_mt, 0, False)):
                        try:
                            _tc(*_args)
                            _write_ok = True
                            _write_method = "TransformComponent(asm)"
                            break
                        except Exception as _e_tc:
                            out.setdefault("write_errors", []).append(
                                "TransformComponent%s: %r" % (len(_args), _e_tc))
        except Exception as _e_outer:
            out.setdefault("write_errors", []).append(
                "TransformComponent: %r" % (_e_outer,))

        # 写入路径（回退）：SetTransform -> SetTransform2 -> MoveComponent
        for _mn in ("SetTransform", "SetTransform2"):
            try:
                _fn = getattr(comp, _mn, None)
                if _fn is None:
                    continue
                _fn(new_mt)
                _write_ok = True
                _write_method = _mn
                break
            except Exception as _e:
                out.setdefault("write_errors", []).append("%s: %r" % (_mn, _e))
        # MoveComponent（SW2025 部分版本以 MoveComponent 取代 SetTransform）
        if not _write_ok:
            try:
                _mc = getattr(comp, "MoveComponent", None)
                if _mc is not None:
                    # MoveComponent 语义是"增量移动"：先读当前位置，再移差值
                    _cur = self._component_position_mm(comp, units=units)
                    if _cur:
                        _dx = float(x) - _cur[0]
                        _dy = float(y) - _cur[1]
                        _dz = float(z) - _cur[2]
                        _mc([_dx * _k, _dy * _k, _dz * _k])
                        _write_ok = True
                        _write_method = "MoveComponent"
            except Exception as _e:
                out.setdefault("write_errors", []).append("MoveComponent: %r" % (_e,))
        if not _write_ok:
            out["error"] = ("无法写入组件变换：SetTransform/SetTransform2/MoveComponent 均不可用。"
                            "本版 SW 组件对象可能只读。")
            out["hint"] = "改用 add_component 带坐标，或用 add_mate 定位。"
            return out
        out["method"] = _write_method

        # 读回校验
        try:
            back = comp.GetTotalTransform(True)
            # 【装配定位修复 R2】平移在 9/10/11
            _pos_back = _position_from_transform_mm(back, k=_k)
            if _pos_back:
                pos = [float(v) for v in _pos_back]
                out["after_mm"] = [round(v, 4) for v in pos]
                _want = [float(x), float(y), float(z)]
                _close = all(abs(pos[i] - _want[i]) < 0.5 for i in range(3))
                out["verified"] = _close
                if not _close:
                    out["error"] = "SetTransform 未报错，但读回位置与期望不符，写入被静默忽略。"
                    out["hint"] = "改用 add_component 带坐标重新添加组件。"
                    return out
        except Exception:
            out["verified"] = None

        out["ok"] = True
        out["applied"] = True
        return out

    def _component_position_mm(self, comp, units="mm"):
        """读回组件当前位置（mm）。【Bug-30】供 MoveComponent 增量计算使用。

        GetTotalTransform 在 late-binding 下【必须传 True】才可用（实测）。
        Returns: [x, y, z] mm 或 None
        """
        _k = 0.001 if str(units).lower() == "mm" else 1.0
        mt = None
        for _arg in (True, None):
            try:
                mt = comp.GetTotalTransform(_arg) if _arg is not None \
                    else comp.GetTotalTransform()
                if mt is not None:
                    break
            except Exception:
                continue
        if mt is None:
            return None
        # 【装配定位修复 R2】平移在 9/10/11（原实现读 3/7/11 = 旋转子矩阵里的值）
        return _position_from_transform_mm(mt, k=_k)

    def place_components_by_coords(self, placements, units="mm", rebuild=True):
        """【Bug-30 修复·fallback 摆位方案】按设计坐标批量摆位（不依赖配合）。

        用途：当 SW2025 的装配 API（AddComponent 坐标参数被忽略、SetTransform
        不可用）导致所有零件堆在原点时，用本方法把已插入的组件按【设计坐标】
        摆开 —— 至少让装配体具备正确的空间布局，便于出图与人工复核。

        Args:
            placements: [(组件名或组件对象, x, y, z), ...]（mm）
        Returns:
            {ok, placed:[...], failed:[...], method, note}
        """
        out = {"ok": True, "placed": [], "failed": [], "method": None}
        try:
            comps = self.model.GetComponents(False) or []
            clist = list(comps) if isinstance(comps, tuple) else ([comps] if comps else [])
        except Exception:
            clist = []

        def _name_of(c):
            for _a in ("Name2", "Name"):
                try:
                    v = getattr(c, _a)
                    if callable(v):
                        v = v()
                    if v:
                        return str(v)
                except Exception:
                    continue
            return None

        for item in (placements or []):
            try:
                target, tx, ty, tz = item[0], item[1], item[2], item[3]
            except Exception:
                out["failed"].append({"item": repr(item), "reason": "格式应为 (组件, x, y, z)"})
                continue
            comp = target if not isinstance(target, str) else None
            if comp is None:
                _t = str(target)
                for c in clist:
                    _n = _name_of(c) or ""
                    # 组件名形如 "DSH_车轮-1"；按前缀匹配
                    if _n == _t or _n.startswith(_t):
                        comp = c
                        break
            if comp is None:
                out["failed"].append({"item": str(target), "reason": "未找到该组件"})
                continue
            r = self.set_component_transform(comp, tx, ty, tz, units=units)
            if r.get("ok"):
                out["placed"].append({"component": _name_of(comp),
                                      "position_mm": [tx, ty, tz],
                                      "method": r.get("method")})
                out["method"] = r.get("method")
            else:
                out["failed"].append({"component": _name_of(comp),
                                      "position_mm": [tx, ty, tz],
                                      "error": r.get("error")})
        if rebuild:
            try:
                self.rebuild()
            except Exception:
                pass
        out["ok"] = bool(out["placed"]) and not out["failed"]
        out["note"] = ("坐标摆位是【设计层布局】，不等价于配合约束；"
                       "如需 SW 原生装配关系请用 add_mate。"
                       "本方法用于 SW2025 装配 API 失效时的兜底（Bug-30）。")
        return out

    def set_array_data(self, feature, values, verify=True):
        """安全写入阵列数据，带读回校验。

        ── 【BUG-I 修复】ArrayData 写入静默失败 ─────────────────────────
        测试反馈：给 ArrayData 赋值后读回未变且不报错 —— 典型静默失败。
          原因是 late-binding 下该属性可能只读，或需特定封送方式。
        本方法写完【读回比对】，不一致即明确报失败，绝不假装成功。

        Returns: {ok, verified, before, after, error?, hint?}
        """
        out = {"ok": False, "verified": False}
        if feature is None:
            out["error"] = "特征对象为空"
            return out
        try:
            before = None
            try:
                before = feature.ArrayData
            except Exception:
                before = None
            out["before"] = repr(before)
            written = False
            last = None
            for _val in (values, (values if isinstance(values, (list, tuple)) else [values])):
                try:
                    feature.ArrayData = _val
                    written = True
                    break
                except Exception as e:
                    last = e
            if not written:
                out["error"] = "ArrayData 赋值失败: %r" % (last,)
                out["hint"] = "该属性在 late-binding 下可能只读；请用带参数的阵列方法一次性建模。"
                return out
            if verify:
                try:
                    after = feature.ArrayData
                    out["after"] = repr(after)
                    if repr(after) != repr(before):
                        out["verified"] = True
                        out["ok"] = True
                    else:
                        out["error"] = "ArrayData 赋值未抛异常，但读回未变化 —— 写入被静默忽略。"
                        out["hint"] = "改用带参数的阵列方法一次性建模，不要依赖事后改 ArrayData。"
                        return out
                except Exception as e:
                    out["error"] = "无法读回校验: %r" % (e,)
                    out["hint"] = "读不回意味着无法确认写入，按失败处理更安全。"
                    return out
            else:
                out["ok"] = True
            return out
        except Exception as e:
            out["error"] = "set_array_data 异常: %r" % (e,)
            return out

    def save_as(self, path):
        """另存为（装配体/零件通用）。

        ── 【BUG-E 修复】原实现只把返回值转成 bool 就当作成功 ——
        而 Extension.SaveAs 的返回值【不可信】：已存在文件时可能返回 True
        却静默不写。改为以磁盘指纹为真相，结果结构化返回，绝不静默报成功。

        ── 【真实情景修复·SW2025】Extension.SaveAs(path, 0, 1, ...) 在本机
        对装配体(.sldasm)抛 com_error(-2147352571 '类型不匹配')，对零件也可能
        静默不写。真机实测对照（DSH_机械臂总装 + 零件）：
          · Extension.SaveAs(path, 0, 1, None, errs, warns) -> 装配体 类型不匹配
          · Extension.SaveAs(path, 2, 1, None, errs, warns) -> 装配体 类型不匹配
          · model.SaveAs3(path, 0, 2)                        -> 装配体/零件 均 rc=0、文件落盘 ✅
          · model.SaveAs2(...)                              -> 装配体 类型不匹配
        历史结论是"本机必须用 SaveAs3(Options=2)"。

        ══ 【save_as Options 修复 · 2026-11-11】推翻上面的 Options=2 结论 ══
        官方原文：Options 是 swSaveAsOptions_e 位掩码，2 = swSaveAsOptions_Copy
        =「存副本并继续编辑当前文档」—— 文档【不绑定】新路径。上述"实测落盘"
        只是写出了副本，装配体侧随即出现 ok:false / v2/v3 均未落盘
        （intermediate\assemble_result.json:144-148）。
        现在：主路径 Extension.SaveAs(Options=Silent|SaveReferenced=5)，
        回退 SaveAs3(Options=Silent=1)；磁盘指纹仍是唯一真相。

        Returns: {ok, path, updated, size, ret,
                  sw_errors:[{bit,hex,name,text}], sw_warnings:[...],
                  sw_error_mask, sw_warning_mask, sw_used_api, prep{...},
                  error?, hint?}
        """
        def _fp(p):
            try:
                if not os.path.exists(p):
                    return None
                st = os.stat(p)
                return (st.st_size, st.st_mtime)
            except Exception:
                return None

        # SW 保存格式枚举：按扩展名推断（swSaveAsFormat_e 子集）
        _EXT_FMT = {
            ".sldprt": 1, ".sldasm": 2, ".slddrw": 3,
            ".step": 17, ".stp": 17, ".igs": 9, ".iges": 9,
            ".x_t": 23, ".x_b": 24, ".stl": 0, ".dwg": 30, ".pdf": 26,
        }
        _fmt = _EXT_FMT.get(str(path).lower()[-6:] if str(path).lower().endswith((".sldprt",".sldasm",".slddrw")) else os.path.splitext(path)[1].lower(), 0)

        # ══ 【save_as Options 修复 · 2026-11-11】主路径改用 Extension.SaveAs ══
        # 现场现象：装配体 save_as ok:false、v2/v3 均未落盘
        #   （intermediate\assemble_result.json:144-148）；
        #   零件侧 3 条「SaveAs3 未抛异常但文件完全未变化」
        #   （parts_rejects\rejected.json）。
        # 根因（官方原文核对）：
        #   ① 原主路径 self.model.SaveAs3(path, 0, 2) 的 Options=2 是
        #      swSaveAsOptions_Copy =「存个副本、继续编辑当前文档」——
        #      文档【不会绑定】到新路径（GetPathName 仍为空），装配体尤其容易
        #      不落盘。而 save()（swapi.py:5844 附近）用的是 Options=1(Silent)，
        #      两处口径根本不一致。
        #   ② IModelDoc2::SaveAs3/4 官方已标 Obsolete，返回 int 错误码而非
        #      bool，原文说 Options 是 swSaveAsOptions_e 位掩码；
        #      官方推荐 IModelDocExtension::SaveAs(Name, Version, Options,
        #      ExportData, Errors, Warnings) → bool，且 Errors/Warnings 是
        #      byref int（swFileSaveError_e / swFileSaveWarning_e 位掩码）。
        # 修复判据：
        #   · 主路径 = Extension.SaveAs(path, 0, Silent|SaveReferenced=5, None,
        #     errs, warns)，把 Errors/Warnings 真值读进返回体（结构化）；
        #   · 回退 = model.SaveAs3(path, 0, 1)（Options=1，绝不是 2）；
        #   · 磁盘指纹 before/after（size+mtime）判定【原样保留】—— 那才是真相；
        #   · 装配体前置：切回本文档 → 退出编辑态 → SaveAll(True)，失败不阻断。
        # 参考：
        #   https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swSaveAsOptions_e.html
        #   https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swFileSaveError_e.html
        _prep = {}
        # ── 装配体前置（仅当 self.model 是装配体；全部 best-effort）──────────
        # 为什么需要：装配体另存落盘的前提是组件已保存、且文档处于非编辑态；
        #   否则 SW 会在 SaveAs 时因"引用未保存"静默跳过（正是 rejected.json
        #   里那批"未抛异常但文件没变"的形态）。
        # 为什么失败不阻断：这些是【提高成功率的前置】，不是成功判据；
        #   真正的判据始终是磁盘指纹。所以只把过程记进 _prep，绝不提前 return。
        try:
            _is_asm = False
            try:
                _is_asm = (int(self.model.GetType()) == 2)   # swDocASSEMBLY
            except Exception:
                _is_asm = str(path).lower().endswith(".sldasm")
            if _is_asm:
                _prep["is_assembly"] = True
                # ① 先确认活动文档就是本装配体（否则 SaveAs 会作用到别的文档）
                _act = {"ok": False}
                try:
                    _title = self.model.GetTitle
                except Exception:
                    _title = None
                try:
                    if _title:
                        _act["ok"] = bool(self.sw.ActivateDoc3(_title, False, 0, 0))
                        _act["api"] = "ActivateDoc3"
                except Exception as _e_a3:
                    _act["error_ActivateDoc3"] = repr(_e_a3)
                if not _act.get("ok") and _title:
                    try:
                        _act["ok"] = bool(self.sw.ActivateDoc2(_title, False, 0))
                        _act["api"] = "ActivateDoc2"
                    except Exception as _e_a2:
                        _act["error_ActivateDoc2"] = repr(_e_a2)
                _prep["activate"] = _act
                # ② 退出编辑态（仍在零件/装配编辑模式时 SaveAs 易失败）
                try:
                    self.model.EditAssembly()
                    _prep["edit_assembly"] = "called"
                except Exception as _e_ea:
                    _prep["edit_assembly"] = repr(_e_ea)
                # ③ 先保存组件的未存改动（装配体落盘的关键前置）
                try:
                    _sa = self.model.SaveAll(True)   # True = 连同引用一起存
                    _prep["save_all"] = repr(_sa)
                except Exception as _e_sa:
                    _prep["save_all"] = repr(_e_sa)
        except Exception as _e_prep:
            _prep["error"] = repr(_e_prep)

        before = _fp(path)
        ret = None
        errs = None
        warns = None
        _used_api = None
        _last_err = None

        # ① 主路径：Extension.SaveAs(path, 0, Silent|SaveReferenced, None, errs, warns)
        try:
            _e_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            _w_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            ret = self.model.Extension.SaveAs(
                path, 0, SW_SAVE_AS_OPTIONS_PRIMARY, None, _e_var, _w_var)
            _used_api = "Extension.SaveAs"
            try:
                errs = int(_e_var.value)
            except Exception:
                errs = None
            try:
                warns = int(_w_var.value)
            except Exception:
                warns = None
        except Exception as e:
            _last_err = e
            # ② 回退：model.SaveAs3 —— Options 必须是 1(Silent)。
            #    ⚠️ 历史误值 2 = swSaveAsOptions_Copy：只存副本、不绑定路径，
            #    装配体常因此"看起来成功但没落盘"。已订正为 1。
            try:
                ret = self.model.SaveAs3(path, 0, SW_SAVE_AS_OPTIONS_FALLBACK)
                _used_api = "IModelDoc2.SaveAs3(Options=Silent)"
                try:
                    errs = int(ret) if isinstance(ret, (int, float)) else None
                except Exception:
                    errs = None
            except Exception as e2:
                return {"ok": False, "path": path, "updated": False,
                        "prep": _prep, "sw_used_api": None,
                        "sw_errors": [], "sw_warnings": [],
                        "sw_error_mask": None, "sw_warning_mask": None,
                        "error": "SaveAs 调用异常(Extension.SaveAs:%r; SaveAs3:%r)"
                                 % (e, e2),
                        "hint": ("两套 API 都抛异常，通常是文档不是活动文档或 "
                                 "SW 处于隐藏/忙碌态；先 ActivateDoc 再重试。")}

        _sw_errors = _decode_sw_save_mask(errs)
        _sw_warnings = _decode_sw_save_mask(warns)
        _field = {"prep": _prep, "sw_used_api": _used_api,
                  "sw_errors": _sw_errors, "sw_warnings": _sw_warnings,
                  "sw_error_mask": errs, "sw_warning_mask": warns,
                  "sw_error_hex": (hex(int(errs)) if isinstance(errs, int) else None),
                  "sw_warning_hex": (hex(int(warns)) if isinstance(warns, int) else None),
                  # sw_error_code 保留为【兼容字段】（旧调用方仍在读），但不再是
                  #   唯一证据 —— 结构化 sw_errors 才是。
                  "sw_error_code": errs}

        after = _fp(path)
        if after is None:
            _bad = {"ok": False, "path": path, "updated": False,
                    "ret": bool(ret), "size": None, **_field,
                    "error": "SaveAs 返回后目标文件不存在，保存未生效。",
                    "hint": "检查路径可写性与目录是否存在。"}
            _hint = _sw_save_error_hint(errs)
            if _hint:
                _bad["hint"] = _hint + "。" + _bad["hint"]
                _bad["error"] += "（SW 错误位：%s）" % _hint
            _reject_failed_part(path, _bad, stage="save_as")
            return _bad

        # ── 【BUG-E 判定修正】与 save() 保持同一语义 ─────────────────────
        # 只要文件被"触碰"（mtime/size 变化）就算保存成功；
        #   只有完全没变才是可疑的静默忽略。
        #   （专家指出：md5 变化是正常写入的表现，不应判失败。）
        updated = (before is None) or (after != before)
        if not updated:
            _bad = {"ok": False, "path": path, "updated": False,
                    "ret": bool(ret), "size": after[0], **_field,
                    "error": ("SaveAs 未报错，但目标文件【完全未变化】"
                              "（大小与 mtime 全同）—— 保存被静默忽略。"),
                    "hint": "先关闭占用该文件的程序；同名零件若已打开，建议 close-all 后重试。"}
            _hint = _sw_save_error_hint(errs)
            if _hint:
                _bad["hint"] = _hint + "。" + _bad["hint"]
            _reject_failed_part(path, _bad, stage="save_as-silent-ignore")
            return _bad

        _out = {"ok": True, "path": path, "updated": True,
                "content_same_unknown": False,
                "ret": bool(ret), "size": after[0], **_field}
        # ── 【save_as 错误位复核 · 2026-11-11】指纹变了也必须看错误位 ──────
        # 有些致命位（0x10 文件锁 / 0x100 扩展名不符 / 0x2000 需先存引用）
        #   会让 SW 只写出"半个文件"或旧内容副本 —— 指纹（size/mtime）照样会变，
        #   于是旧实现会判成功。这里把错误位作为【独立证据】：命中致命位即失败，
        #   并把具体位写进 error，绝不让它悄悄过关。
        if isinstance(errs, int) and (errs & _SW_FILE_SAVE_FATAL_BITS):
            _bad = dict(_out)
            _bad["ok"] = False
            _bad["verified"] = False
            _bad["error"] = ("SaveAs 目标文件已变化，但 SW 报告致命错误位 —— "
                             "落盘内容不可信。")
            _bad["hint"] = _sw_save_error_hint(errs) or "检查文件锁与扩展名。"
            _reject_failed_part(path, _bad, stage="save_as-error-bits")
            return _bad
        if _sw_warnings:
            self._warn("save_as: SW 返回告警位 %s（%s）"
                       % (_out.get("sw_warning_hex"),
                          "；".join(w["text"] for w in _sw_warnings)))
        # ══ 【Bug-01 修复】save_as 后【必须】把材料凭据写到【最终落盘路径】旁 ══
        # 原实现只有 save() 写凭据：小屋若走 save() → save_as(最终路径)，
        #   凭据留在中间路径旁；直接调 save_as() 则完全不产生凭据 ——
        #   实测本任务零件目录下 .material.json 数量为 0，room-end 防线①
        #   因此拦下全部零件（材料防线形同虚设，工图无法合规交付）。
        # 这里在保存成功（磁盘指纹已确认变更）后补写凭据到最终路径旁。
        #
        # ── 【Bug-B 修复·严重】绝不写 self.path（它是只读 property）─────────
        # 上一次修复写了 `self.path = path`，而 path 是 @property 且【无 setter】
        #   （见本类 path 定义）→ 必然抛
        #     AttributeError: property 'path' of 'SWModel' object has no setter
        #   该异常被下面的 except 吞掉 → _emit_material_attestation 【从未执行】，
        #   凭据依旧不落盘 —— 即"修了但没生效"，比原缺陷更隐蔽（返回体里只有
        #   一行 error，容易被忽略）。
        # 正确做法：只把最终路径记到内部字段 _saved_path / _path_override，
        #   供后续需要路径的逻辑读取；然后【独立】调用凭据写入，
        #   即使路径记录失败也绝不能影响凭据落盘。
        try:
            self._saved_path = path
        except Exception:
            pass
        try:
            self._emit_material_attestation(path, _out)
        except Exception as _e_att:
            _out["material_attestation"] = {"ok": False, "error": repr(_e_att)}
            try:
                self._warn("save_as 写材料凭据异常: %r" % (_e_att,))
            except Exception:
                pass
        # ── 【Bug⑪ 修复】origin 约定同样必须跟着【最终落盘路径】走 ──────────
        # 否则小屋走 save() → save_as(最终路径) 时，.origin.json 会留在中间路径旁，
        # 装配侧在真正的零件旁读不到约定（与 Bug-01 的材料凭据同一类缺陷）。
        try:
            _attach_origin_record(self, path, _out)
        except Exception as _e_org:
            _out["origin_record"] = {"ok": False, "error": repr(_e_org)}
        # ── 【P1-⑥ 修复】朝向声明同样必须跟着【最终落盘路径】走 ────────────
        # 与 Bug-01（材料凭据）/ Bug⑪（origin）同一类缺陷：走
        # save() -> save_as(最终路径) 时，若只写在 save() 里，
        # pr.json 会留在中间路径旁，装配侧在真零件旁读不到声明。
        try:
            _attach_pr_record(self, path, _out)
        except Exception as _e_pr:
            _out["pr_record"] = {"ok": False, "error": repr(_e_pr)}
        return _out

    # ══ 【新-5 修复】OpenDoc6 统一封装 ═══════════════════════════════════
    # 测试反馈：直接写 app.OpenDoc6(path, 1, 0) 只传 3 个参数会抛
    #   非选择性参数 的错误 —— 该提示词【误导】，实际是【缺参数】。
    #   late-binding 下正确签名必须 6 个：
    #     OpenDoc6(FileName, Type, Options, Configuration, Errors, Warnings)
    #   其中 Errors/Warnings 必须是 VARIANT(VT_BYREF|VT_I4) 的引用变量。
    @staticmethod
    def open_part(sw, path, doc_type=None, configuration=""):
        """按正确签名打开文档（补齐 OpenDoc6 的 6 个参数）。

        Args:
            sw: SldWorks 应用对象
            path: 文档绝对路径
            doc_type: 1=零件 2=装配 3=工程图；None 时按扩展名推断
            configuration: 配置名，默认空

        Returns: {ok, doc?, title?, error?, hint?}
        """
        out = {"ok": False, "doc": None}
        if not path or not os.path.exists(path):
            out["error"] = "文件不存在: %s" % path
            return out
        if doc_type is None:
            _ext = os.path.splitext(path)[1].lower()
            doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(_ext, 1)
        try:
            _errs = win32com.client.VARIANT(
                pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            _warns = win32com.client.VARIANT(
                pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            doc = sw.OpenDoc6(os.path.abspath(path), int(doc_type), 1,
                              str(configuration or ""), _errs, _warns)
        except Exception as e:
            out["error"] = "OpenDoc6 调用异常: %r" % (e,)
            out["hint"] = ("若提示涉及 non-selective 参数，那是误导 —— "
                           "实际问题通常是【参数不足】。"
                           "正确签名需要 6 个参数，请改用 SWModel.open_part()。")
            return out
        if doc is None:
            out["error"] = ("OpenDoc6 返回 None（SW 错误码 errs=%s）"
                            % getattr(_errs, "value", "?"))
            return out
        out["ok"] = True
        out["doc"] = doc
        try:
            out["title"] = doc.GetTitle
        except Exception:
            pass
        return out

    def list_components(self):
        """列出装配体中的全部组件（名称 + 路径 + 是否抑制）。"""
        out = []
        try:
            comps = self.model.GetComponents(False)
            for c in (comps or []):
                item = {}
                try: item["name"] = c.Name2
                except Exception: item["name"] = None
                try: item["path"] = c.GetPathName
                except Exception: item["path"] = None
                try: item["suppressed"] = bool(c.IsSuppressed())
                except Exception: item["suppressed"] = None
                out.append(item)
        except Exception:
            pass
        return out

    # ---------- 便捷工具 ----------
    def clear_selection(self):
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        return self

    def _reset_selection_state(self, reason=""):
        """【R5 修复】切除/拉伸前的"选择状态自愈"：先清选择，再强制重建。

        ── 为什么必须做（do_cut 状态依赖型选择错乱）────────────────────
        同一段建模脚本里，FeatureCut/FeatureExtrusion 执行【成功】后，
        SW 仍会在 SelectionManager 里残留上一次的选中对象（有时是已消费的
        草图段，有时是被切出来的新面）。于是下一次 cut 实际是"带着上一次的
        脏选择"去调 API —— 表现为：
          · 该选的没选中（选中的是旧草图段）；
          · 选错面（把上一步留下的面当成本次轮廓面）；
          · 同一次 do_cut 在不同选择状态下结果不同（完全不可复现）。
        这就是台账里"do_cut 状态依赖型选择错乱"的直接根因。

        修复：每次 cut/extrude 开工前【无条件】执行
          ① ClearSelection2(True)  —— 清空脏选择；
          ② ForceRebuild3(False)   —— 重建模型，刷新 COM 内部的选择/几何缓存。
        并把"清掉几个残留选中对象"记成告警 —— 这是状态依赖的可观测证据，
        出问题时能立刻看出"上一步留了脏选择"。

        Returns: dict {cleared, rebuilt, reason}
        """
        out = {"cleared": None, "rebuilt": False, "reason": str(reason or "")}
        # ① 先数一下有多少"脏选择"（这是状态依赖的证据）
        try:
            out["cleared"] = int(
                self.model.SelectionManager.GetSelectedObjectCount2(-1))
        except Exception:
            out["cleared"] = None
        if out["cleared"]:
            self._warn(
                "检测到 %d 个残留选中对象（%s）—— 这是 do_cut 状态依赖型"
                "选择错乱的典型现场，已强制清空后重选。"
                % (out["cleared"], reason or "无标注"))
        try:
            self.model.ClearSelection2(True)
        except Exception:
            pass
        # ② 强制重建：刷新 COM 内部缓存（ForceRebuild3 优先，EditRebuild3 兜底）
        # ── 安全护栏：草图【正在编辑】时不重建 ──────────────────────────
        # 草图编辑态下 ForceRebuild3 会让 SW 退出/重置草图，反而破坏轮廓
        #   （正是 Bug T2 的形态）。此时只清选择、不重建 —— 清选择已足够
        #   消除"脏选择"，重建留到特征创建之后。
        _sketch_active = False
        try:
            _sketch_active = self.skm.ActiveSketch is not None
        except Exception:
            _sketch_active = False
        out["sketch_active"] = _sketch_active
        if _sketch_active:
            return out
        try:
            _fr = getattr(self.model, "ForceRebuild3", None)
            if _fr is not None and callable(_fr):
                _fr(False)          # False = 不重建被抑制的特征
                out["rebuilt"] = True
        except Exception:
            pass
        if not out["rebuilt"]:
            try:
                self.rebuild()
                out["rebuilt"] = True
            except Exception:
                pass
        return out

    def rebuild(self):
        """重建模型。

        Bug 修复: 原实现 `self.model.EditRebuild3` 是"属性访问"而非函数调用，
        在 late-binding COM 下会抛 AttributeError: <unknown>.EditRebuild3，
        导致第二个 extrude() 之后的任何建模步骤整链崩溃。
        EditRebuild3 是【方法】，必须加括号调用；同时兼容个别版本返回属性值
        的情况，并保证重建失败不拖垮整个建模脚本。
        """
        rb = getattr(self.model, "EditRebuild3", None)
        if rb is None:
            return self
        try:
            if callable(rb):
                rb()
        except Exception:
            # 重建失败不应中断建模：SW 多数情况下会在下次特征操作时自动重建
            pass
        return self


# ==================== 通用：按名称选草图（跨语言） ====================

def _first_feature(model):
    """取特征树的第一个特征（IModelDoc2::FirstFeature，**方法**）。

    ── 【S-08 根因修复 · 2026-11-04 真机实测】────────────────────────────
    原 select_sketch_by_index 写的是 `fm.FirstFeature`（FeatureManager 上），
    而 IFeatureManager 上【根本没有 FirstFeature】—— 实测抛
        com_error(-2147352573, "找不到成员")
    该异常被 `except Exception: return 0` 吞掉，于是**永远返回 0**，
    选区/轮廓判定全线失效。正确成员是 IModelDoc2::FirstFeature()（方法）。

    同时注意：FirstFeature 在不同绑定下可能是方法也可能是属性，这里两种都试。
    """
    if model is None:
        return None
    for _owner in (model, getattr(model, "Extension", None)):
        if _owner is None:
            continue
        try:
            _v = getattr(_owner, "FirstFeature", None)
        except Exception:
            _v = None
        if _v is None:
            continue
        try:
            return _v() if callable(_v) else _v
        except Exception:
            continue
    return None


def _iter_sketch_feature_names(model, limit=200):
    """遍历特征树，返回 [(序号, 特征名, 类型名)]（只含可拉伸草图特征）。

    ── 【S-08 根因修复】这是"按索引选草图"唯一可靠的来源 ─────────────────
    实测（本机 SW2025 SP5.0）：
      · FirstFeature 必须走 IModelDoc2::FirstFeature()；
      · 草图特征的类型名在中文版下是 "ProfileFeature"（被消费）/ "Sketch"；
      · GetNextFeature 同样是**方法**。
    原实现三个环节全用属性访问 → 一次都没走通，且异常被吞。
    """
    out = []
    feat = _first_feature(model)
    guard = 0
    while feat is not None and guard < limit:
        guard += 1
        _tn = ""
        try:
            _tn = str(_feature_type_name(feat) or "")
        except Exception:
            _tn = ""
        if (_tn in _SKETCH_TYPE_MARKERS
                and _tn not in _SKETCH_TYPE_EXCLUDE
                and _tn != "OriginProfileFeature"):
            try:
                out.append((len(out) + 1, str(_get_feature_name(feat)), _tn))
            except Exception:
                pass
        try:
            _nx = getattr(feat, "GetNextFeature", None)
            feat = _nx() if callable(_nx) else _nx
        except Exception:
            break
    return out


def _get_feature_name(feat):
    """取特征名（Name 在部分绑定下是属性，个别版本暴露成方法）。"""
    try:
        v = feat.Name
        return v() if callable(v) else v
    except Exception:
        return ""


def select_sketch_by_index(sw, model, index):
    """按序号选中第 N 个草图（跨语言：不依赖"草图1/2"中文名）。

    返回选中的草图数量；失败返回 0。

    ── 【S-08 根因修复】原实现三个 COM 成员全按【属性】读，全是错的：
       ① `model.FeatureManager.FirstFeature` —— IFeatureManager 上不存在；
       ② `feat.GetTypeName2` / `feat.Name` —— 是方法/属性混用；
       ③ `feat.GetNextFeature` —— 是方法。
    三个都失败又被 `except Exception: pass` 静默吞掉 → 恒返回 0。
    现在改走 _iter_sketch_feature_names()（同一套已实测的遍历逻辑），
    并且【按真实特征名】调用 SelectByID2 —— 实测只有原名能选中，
    把 "草图2" 换成 "Sketch2" 反而返回 None。
    """
    try:
        try:
            _i = int(index)
        except Exception:
            return 0
        names = _iter_sketch_feature_names(model)
        if not names:
            return 0
        hit = None
        for n, name, _tn in names:
            if n == _i:
                hit = name
                break
        if hit is None:
            return 0
        model.ClearSelection2(True)
        ext = model.Extension
        empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        sel = ext.SelectByID2(hit, "SKETCH", 0, 0, 0, False, 0, empty, 0)
        return 1 if sel else 0
    except Exception:
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


# ==================== 【Bug⑫】按名称/索引统一选草图 ====================

# 草图特征的 GetTypeName2 取值（跨 SW 版本/locale 实测）：
#   · "Sketch"          —— 未消费的草图；
#   · "ProfileFeature"  —— 【最常见】：被拉伸/旋转消费过的草图特征，
#                          SW 的 GetTypeName2 对草图返回的就是这个名字。
#   ⚠️ 只认 "Sketch" 会漏掉绝大多数真实草图（原 select_sketch_by_index
#      就踩在这个点上：按索引选草图经常"选不到第 1 个"）。
#   以下两类【不是】可拉伸草图，必须排除：
#     "SketchBlock"（草图块）/ "SketchPicture"（草图图片）。
_SKETCH_TYPE_MARKERS = ("Sketch", "ProfileFeature")
_SKETCH_TYPE_EXCLUDE = ("SketchBlock", "SketchPicture")


def _feature_type_name(feat):
    """取 IFeature 的类型名（兼容"方法"与"属性"两种暴露方式）。

    ── 【F-04 修复】为什么需要它 ────────────────────────────────────────
    _is_sketch_feature() 原先用 getattr(feat, "GetTypeName2") 当【属性】读。
    但【类型库】IFeature.GetTypeName2() 是【方法】(L21627)：
      · 真 COM late-binding 下 getattr 返回的是 bound method 对象，
        str() 出来是 "<bound method ...>"，任何类型判定都恒为 False；
      · 某些版本的 early-binding 封装会把它暴露成属性（字符串）。
    两种写法都试，谁的返回像类型名就用谁 —— 只读、失败返回 ""。

    影响范围：本函数是新增的只读助手，不改变任何既有行为；
    只有 _select_extrude_profile（拉伸轮廓选择）使用它。
    """
    for _how in (lambda: feat.GetTypeName2(),
                 lambda: feat.GetTypeName2,
                 lambda: feat.GetTypeName(),
                 lambda: feat.GetTypeName):
        try:
            _v = _how()
        except Exception:
            continue
        if isinstance(_v, str) and _v and not _v.startswith("<bound method"):
            return _v
    return ""


def _is_sketch_feature(feat):
    """判定一个 Feature 是否【可拉伸草图】特征（跨版本、跨 locale）。

    同时看 GetTypeName2 与 GetTypeName：前者对已消费草图返回
    "ProfileFeature"，后者多数情况下返回 "Sketch"。任一命中即认为是草图。
    """
    # ── 【F-04 修复】属性式读取在 late-binding 下恒为 False ────────────────
    # 【类型库】IFeature.GetTypeName2() 是【方法】(L21627)，getattr 拿到的是
    #   bound method，str() 出来是 "<bound method ...>"，永远匹配不上
    #   _SKETCH_TYPE_MARKERS —— 于是"特征树里一个草图都找不到"。
    # 这正是"标准拉伸路径选不中轮廓"的隐藏放大器：即使把选择步骤补上，
    #   类型判定失败也照样选不中。改走 _feature_type_name（方法/属性都兼容）。
    # ⚠️ 兼容性：老调用方（test_regression_bug12_select_sketch.py）用
    #   【属性式】假特征，_feature_type_name 同样支持，行为不变。
    _t = _feature_type_name(feat)
    if not _t:
        return False
    if any(x in _t for x in _SKETCH_TYPE_EXCLUDE):
        return False
    if any(m in _t for m in _SKETCH_TYPE_MARKERS):
        return True
    return False


def _iter_sketch_features(model, limit=200):
    """按特征树顺序产出草图特征 [(序号, 名称, feature)]（跨语言，不依赖草图名）。

    为什么要遍历特征树而不是"用素描管理器查"：
      · ISketchManager 没有枚举接口（只能拿到 ActiveSketch）；
      · 中文名 "草图1" 在英文 locale / 某些 SW 版本里不存在，
        直接 SelectByID2("草图1") 会抛 DISP_E_XXX —— 这正是 Bug⑫。
    特征树（FeatureManager.FirstFeature → GetNextFeature）是唯一稳定的来源，
    且 feat.Name 返回的是【本 locale 的真实名字】，因此遍历后按名字比对可靠。

    类型判定见 _is_sketch_feature（兼容 "Sketch" 与 "ProfileFeature"）。
    """
    out = []
    try:
        feat = model.FeatureManager.FirstFeature
    except Exception:
        return out
    n = 0
    while feat is not None and n < limit:
        n += 1
        try:
            nm = str(feat.Name or "")
        except Exception:
            nm = ""
        try:
            if _is_sketch_feature(feat):
                out.append((len(out) + 1, nm, feat))
        except Exception:
            pass
        try:
            feat = feat.GetNextFeature
        except Exception:
            break
    return out


def _select_sketch_feature(model, name):
    """用 SelectByID2 按【真实名字】选一个草图；成功返回 True。"""
    import win32com.client as _wc
    try:
        model.ClearSelection2(True)
        empty = _wc.VARIANT(pythoncom.VT_DISPATCH, None)
        sel = model.Extension.SelectByID2(name, "SKETCH", 0, 0, 0,
                                          False, 0, empty, 0)
        return bool(sel)
    except Exception:
        return False


def _select_sketch_by_number(model, index):
    """按 1 基序号选草图（内部复用 select_sketch_by_index 的语义）。"""
    feats = _iter_sketch_features(model)
    if not feats:
        return None
    try:
        idx = int(index)
    except Exception:
        return None
    if idx < 1 or idx > len(feats):
        return None
    name = feats[idx - 1][1]
    if _select_sketch_feature(model, name):
        return name
    return None


def select_sketch(sw, model, name_or_idx, index_fallback=None):
    """【Bug⑫ 修复】统一的"按名字或按索引"选草图入口（跨语言、带兜底）。

    背景（现场实测）：
      sw_bridge 的已知坑 —— 选草图【必须】用 select_sketch_by_index()。
      用中文名（"草图1"）在部分 SW 版本 / locale 下会抛 DISP_E_XXX 找不到草图，
      建模直接失败（零件建到一半，白跑一轮）。工具链文档提示了这一点，
      但【代码里没有兜底】—— 全靠写脚本的人记住。

    本函数把兜底内化，调用方不必再关心"该用名字还是索引"：
      ① 解析输入类型：
         · int / 纯数字字符串 → 直接按索引；
         · 其它字符串       → 按名字找（含中文/英文前缀互换）；
      ② 按名字查找的三级路径（任一成功即可）：
         a) model.FeatureByName(name)
         b) 遍历特征树，比对 feat.Name（忽略大小写/空白），
            以及 "SketchN" ↔ "草图N" 的前缀互换形态
         c) 兜底：用 SelectByID2 直接试原名
      ③ 名字找不到 / 名字非法 → 【回退索引】：
         · 显式给了 index_fallback 就用它；
         · 否则从名字尾部数字推断（"草图2"/"Sketch2" → 2）；
         · 再不行用 1；
         并给出 warning（写进返回值 + stderr），绝不静默。
      ④ 全程不改模型几何 —— 只做选择操作。

    Args:
        sw: SolidWorks Application（本函数不需要它，仅为与其它 API 签名一致）。
        model: 零件/装配文档对象（IDocument）。
        name_or_idx: 中文名 / 英文名 / 纯数字字符串 / 整数索引（1 基）。
        index_fallback: 名字找不到时的回退索引（可选）。

    Returns:
        {"ok": bool, "selected": bool, "count": 0|1,
         "method": "index"|"feature_by_name"|"tree_name"|"select_by_id"|"fallback_index",
         "sketch_name": <实际选中的草图名>, "index": <实际使用的序号>,
         "warnings": [...], "error"?: str}
        · 与既有 select_sketch_by_index 的"数量语义"兼容：count 即选中数量。
        · ok=False 时 error 说明为什么连索引回退都失败（不谎报成功）。
    """
    out = {"ok": False, "selected": False, "count": 0, "method": None,
           "sketch_name": None, "index": None, "warnings": [],
           "requested": name_or_idx, "fallback": False}

    def _warn(msg):
        out["warnings"].append(str(msg))
        try:
            _warn_stderr("select_sketch: " + str(msg))
        except Exception:
            pass

    if model is None:
        out["error"] = "model 为空（没有可操作的文档）—— 请先 new_part/open_part"
        return out

    # ── ① 解析输入：整数 / 纯数字串 / 名字 ──────────────────────────────
    raw = name_or_idx
    _want_index = None
    _want_name = None
    if isinstance(raw, bool):
        _want_name = str(raw)
    elif isinstance(raw, int):
        _want_index = int(raw)
    elif isinstance(raw, float) and float(raw).is_integer():
        _want_index = int(raw)
    else:
        _s = str(raw or "").strip()
        if not _s:
            out["error"] = "草图标识为空（需要名字或 1 基索引）"
            return out
        if _s.isdigit():
            _want_index = int(_s)
        else:
            _want_name = _s

    # ── ② 纯索引：直接按序号选 ───────────────────────────────────────
    if _want_index is not None:
        _nm = _select_sketch_by_number(model, _want_index)
        out["index"] = _want_index
        if _nm:
            out.update({"ok": True, "selected": True, "count": 1,
                        "method": "index", "sketch_name": _nm})
            return out
        # 索引越界：列出真实可用的草图，方便调用方纠正
        _feats = _iter_sketch_features(model)
        out["error"] = ("按索引 %s 选草图失败（文档里共 %d 个草图）。"
                        % (_want_index, len(_feats)))
        out["available_sketches"] = [f[1] for f in _feats]
        out["hint"] = ("索引是 1 基的（第 1 个草图 = 1）。请按 "
                       "available_sketches 的顺序核对。")
        return out

    # ── ③ 按名字：FeatureByName → 特征树比对 → SelectByID2 原名 ────────
    _cands = [_want_name]
    # 中英文前缀互换（"草图2" ↔ "Sketch2"）—— 同一份脚本可跨 locale 复用
    _tail = "".join(c for c in _want_name if c.isdigit())
    if _tail:
        for _p in ("Sketch", "草图"):
            _c = _p + _tail
            if _c not in _cands:
                _cands.append(_c)

    for _cand in _cands:
        # a) FeatureByName（最快，且返回值可用于确认存在）
        try:
            _f = model.FeatureByName(_cand)
            if _f is not None:
                try:
                    _real = str(_f.Name or _cand)
                except Exception:
                    _real = _cand
                if _select_sketch_feature(model, _real):
                    out.update({"ok": True, "selected": True, "count": 1,
                                "method": "feature_by_name",
                                "sketch_name": _real,
                                "index": _index_of_sketch(model, _real)})
                    return out
        except Exception:
            pass

    # b) 遍历特征树，按真实名字比对（大小写/空白不敏感）
    _norm = lambda s: str(s or "").strip().replace(" ", "").casefold()
    _feats = _iter_sketch_features(model)
    for _i, _nm, _feat in _feats:
        if _norm(_nm) in (_norm(_want_name),) or _norm(_nm) in [_norm(c) for c in _cands]:
            if _select_sketch_feature(model, _nm):
                out.update({"ok": True, "selected": True, "count": 1,
                            "method": "tree_name", "sketch_name": _nm,
                            "index": _i})
                return out

    # c) 兜底：直接拿原名试 SelectByID2（有些情况 FeatureByName 取不到）
    for _cand in _cands:
        if _select_sketch_feature(model, _cand):
            out.update({"ok": True, "selected": True, "count": 1,
                        "method": "select_by_id", "sketch_name": _cand,
                        "index": _index_of_sketch(model, _cand)})
            return out

    # ── ④ 名字彻底找不到 → 回退索引（并给出 warning）───────────────────
    _fb = index_fallback
    if _fb is None:
        try:
            _fb = int(_tail) if _tail else None
        except Exception:
            _fb = None
    _why = ("按名字 %r 找不到草图（len(locale)=%r）。可用草图: %s"
            % (_want_name, _tail, [f[1] for f in _feats]))
    if _fb is not None:
        _nm = _select_sketch_by_number(model, _fb)
        if _nm:
            _warn("%s → 已回退到索引 %s（选中 %r）。"
                  "建议脚本改用索引选草图（Bug⑫：中文名不可靠）。"
                  % (_why, _fb, _nm))
            out.update({"ok": True, "selected": True, "count": 1,
                        "method": "fallback_index", "sketch_name": _nm,
                        "index": _fb, "fallback": True})
            return out
        _warn("%s → 回退索引 %s 同样失败。" % (_why, _fb))
        out["error"] = ("名字和索引都没选到草图（回退索引=%s 越界）。" % _fb)
    else:
        out["error"] = "按名字找不到草图，且无法从名字推断回退索引。"
    out["available_sketches"] = [f[1] for f in _feats]
    out["index"] = _fb
    out["hint"] = ("请二选一：① 传 1 基索引（推荐，Bug⑫）；"
                   "② 传 available_sketches 里的真实名字。")
    return out


def _index_of_sketch(model, name):
    """返回某草图在特征树中的 1 基序号；找不到返回 None（只读）。"""
    try:
        for i, nm, _f in _iter_sketch_features(model):
            if nm == name:
                return i
    except Exception:
        pass
    return None


# ==================== Bug-25: 安全退出 SolidWorks ====================

# ── 【H 修复】模块级别名：让 swapi.new_assembly() 也能用 ──────────────
# 小屋/调用方常写成 swapi.new_assembly(...)，而原实现只在类里定义，
#   于是报 module has no attribute —— 属于调用链断裂。
def new_assembly(sw=None, template=None):
    """模块级入口，等价于 SWModel.new_assembly（【H 修复】）。"""
    return SWModel.new_assembly(sw, template)


def _slworks_pids():
    """返回当前 SLDWORKS.exe 的 PID 列表（零副作用，只读 tasklist）。

    为什么单独抽出来：close-all 需要【轮询确认进程真的退出】，
      不能只看 taskkill 的返回码（它是异步信号，见 close_all_and_exit 注释）。
    解析失败时返回空列表 —— 调用方会把它当"没有残留进程"。
    这一点是刻意的：探测能力缺失时，宁可放过也不要谎报"还有进程"，
      否则 close-all 会永远返回失败，把正常流程卡死。
    """
    import subprocess as _sp
    pids = []
    try:
        _r = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10)
        _txt = (_r.stdout or "")
        if "SLDWORKS" in _txt.upper():
            import csv as _csv
            import io as _io
            for _row in _csv.reader(_io.StringIO(_txt)):
                if not _row:
                    continue
                _nm = (_row[0] or "").strip().strip('"')
                if _nm.upper() != "SLDWORKS.EXE":
                    continue
                # CSV 列数因语言/系统/内存列格式而异 → 取第 2 列，
                # 不是纯数字时再扫后面几列（兼容 "12,345 K" 这类内存列）。
                _pid = None
                for _cell in _row[1:]:
                    _c = str(_cell).strip().strip('"')
                    if _c.isdigit():
                        _pid = int(_c)
                        break
                if _pid is not None:
                    pids.append(_pid)
    except Exception:
        return []
    return pids


def _sw_doc_count(sw):
    """读取 SW 当前打开的文档数量（只读；取不到返回 None）。

    late-binding 下 GetDocumentCount 是【无参属性】而非方法，因此两种绑定
    约定都要兼容：先按属性取，失败再按方法调。
    """
    for _get in (lambda: getattr(sw, "GetDocumentCount", None),
                 lambda: sw.GetDocumentCount()):
        try:
            _v = _get()
            if _v is not None:
                return int(_v)
        except Exception:
            continue
    return None


def close_all_documents(sw):
    """【Bug⑧ 修复】只关闭 SolidWorks 里的全部文档，【不结束 SW 进程】。

    背景（并行房间共用单台 SW，现场实测）：
      三个房间（底盘/传动/传感器）共用同一台 SolidWorks（FIFO 排队）。任一
      房间跑 close-all 就会 Quit + taskkill 结束 SW 进程，其它房间正在进行的
      RPC 连接【立即失效】—— 主代理观察到 SW PID 频繁变化
      （4148 → 30860 → 18388 → 23448 → 472 → 34744），每次都是冷启动：
      并行模式实际退化成"串行 + 冷启动"，单零件建模耗时被放大。

    正确语义（本函数）：
      close-all 默认只做【文档级清场】—— 调 SldWorks.CloseAllDocuments，
      保留 SW 进程存活，下一个房间/下一个零件复用同一进程（无需重新 Dispatch）。
      需要真正退出进程时，由调用方显式传 quit=True
      （见 close_all_and_exit / sw_bridge close-all --quit）。

    Returns:
        {"ok", "method": "close_all_documents", "keepalive": True,
         "quit": False, "docs_before", "docs_after", "error"?, "hint"?}
    """
    out = {"ok": True, "method": "close_all_documents", "keepalive": True,
           "quit": False, "sw_exited": False,
           "docs_before": _sw_doc_count(sw), "docs_after": None}
    _err = None
    try:
        # SW 2018~2024 通用的文档级关闭接口（参数 0 = 不弹保存提示）
        sw.CloseAllDocuments(0)
    except AttributeError as e:
        _err = "当前 SW 版本没有 CloseAllDocuments: %r" % (e,)
    except Exception as e:
        _err = "CloseAllDocuments 调用异常: %r" % (e,)
    # 关完再读一次文档数（这是本次动作是否真的生效的客观证据）
    _after = _sw_doc_count(sw)
    out["docs_after"] = _after
    if _err:
        out["ok"] = False
        out["error"] = _err
        out["hint"] = ("SW 进程【未】被结束（保活语义），其它房间的连接不受影响；"
                       "可改用 sw_bridge.py close-all --quit 走完整的退出流程。")
        return out
    if _after is not None and _after > 0:
        # 有后台任务/模态对话框时 CloseAllDocuments 可能没关干净 —— 如实报出
        out["ok"] = False
        out["error"] = ("CloseAllDocuments 返回后仍有 %d 个文档未关闭"
                        "（可能有模态对话框或后台任务占用）。" % _after)
        out["hint"] = "先处理 SW 里的对话框/保存提示，再重试 close-all。"
        return out
    out["note"] = ("已只关闭文档，SolidWorks 进程保持运行（保活）——"
                   "并行房间的下一次调用可直接复用该实例，无需冷启动。")
    return out


def close_all_and_exit(sw, quit=True):
    """安全关闭所有文档并退出 SolidWorks（原有行为）。

    Bug-25 修复: SW 2020 没有 Quit() 方法，直接使用会导致 AttributeError。
    本函数先关闭所有文档，再尝试 Quit()（可用时），最后依赖 GC 释放 COM 引用。

    ── 【Bug⑧ 修复】新增 quit 参数（默认 True，保持既有调用方行为）──────────
    并行房间共用单台 SW 时，close-all 的【默认语义已改为保活】（只关文档、
    不杀进程，见 close_all_documents）；只有显式要求退出进程时（quit=True，
    对应 sw_bridge.py close-all --quit）才走本函数原有的 Quit + taskkill 流程。

    Args:
        sw: SolidWorks Application 对象（win32com dispatch）
        quit: True=退出 SW 进程（旧行为）；False=只关文档（等价
              close_all_documents）
    Returns:
        {"ok": True, "method": "close_all"} 或 {"ok": False, "error": "..."}
    """
    if not quit:
        return close_all_documents(sw)
    try:
        # 第一步：关闭所有文档（SW 2018~2024 通用）
        sw.CloseAllDocuments(0)
    except AttributeError:
        # CloseAllDocuments 在某些旧版本可能不存在，忽略
        pass
    except Exception:
        pass

    try:
        # 第二步：尝试 Quit（SW 2022+ 可用，2020 不存在）
        sw.Quit()
    except AttributeError:
        # Bug-25 修复: SW 2020 没有 Quit()，安全忽略
        pass
    except Exception:
        pass

    # 第三步：GC 释放 COM 引用
    import gc as _gc
    _gc.collect()

    # 第四步：taskkill 后备（Bug-5 修复: Quit() 经常抛异常，堆积文档）
    #
    # taskkill 是异步信号：发完要轮询进程表确认真的退出，不能只看返回码。
    # 否则调用方会拿 ok=true 去开新实例，撞上还没退出的旧实例（单实例程序）。
    _t0 = None
    try:
        import time as _tm
        _t0 = _tm.time()
    except Exception:
        pass
    try:
        import subprocess as _sp
        _result = _sp.run(
            ["taskkill", "/F", "/IM", "SLDWORKS.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        _killed = (_result.returncode == 0)
    except Exception:
        _killed = False

    # 轮询等待进程真正消失（关键：不是"信号发出去了"，而是"进程没了"）
    _exited = False
    _waited = 0.0
    _remaining = []
    try:
        import time as _tm2
        _deadline = _tm2.time() + 15.0
        while _tm2.time() < _deadline:
            _remaining = _slworks_pids()
            if not _remaining:
                _exited = True
                break
            _tm2.sleep(0.4)
        if _t0 is not None:
            _waited = round(_tm2.time() - _t0, 2)
    except Exception:
        # 探测失败（无 tasklist 等）→ 不谎报已退出，交给调用方判断
        _exited = False

    out = {
        "ok": bool(_exited),
        "method": ("taskkill_slworks" if _killed
                   else "close_all_documents_gc_cleanup"),
        # 明确回传"进程是否真的退出"，避免调用方拿 ok 当"已可开新实例"
        "exited": bool(_exited),
        "waited_sec": _waited,
        "remaining_pids": _remaining,
    }
    if not _exited:
        out["ok"] = False
        out["error"] = (
            "close-all 已发出终止信号，但 %s 秒后仍有 SLDWORKS.exe 残留"
            "（PID: %s）。请等待后再试，或手动结束这些进程。"
            % (int(_waited or 0),
               ", ".join(str(x) for x in _remaining) or "?"))
        out["hint"] = "SW 是单实例程序：残留旧实例会让新实例把请求转给它，导致锁状态错乱。"
    elif _waited >= 1.0:
        out["note"] = "已等待 %.1f 秒确认进程完全退出（taskkill 是异步的）。" % _waited
    return out
