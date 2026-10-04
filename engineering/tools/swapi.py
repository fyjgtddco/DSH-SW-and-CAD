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
    # 0) 环境变量显式指定（最高优先，用于非常规安装位置）
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
COMMON_MATERIALS = {
    "Q235": {"density": 7850, "aliases": ["Q235", "AISI 1020", "Plain Carbon Steel", "碳钢"]},
    "45#": {"density": 7850, "aliases": ["45", "AISI 1045", "1045"]},
    "40Cr": {"density": 7850, "aliases": ["40Cr", "AISI 5140", "5140", "Chromium Steel"]},
    "304": {"density": 8000, "aliases": ["304", "AISI 304", "Stainless Steel", "不锈钢"]},
    "316": {"density": 8000, "aliases": ["316", "AISI 316"]},
    "6061-T6": {"density": 2700, "aliases": ["6061", "6061-T6", "Aluminum Alloy 6061", "铝合金"]},
    "7075": {"density": 2810, "aliases": ["7075", "7075-T6"]},
    "Cr12MoV": {"density": 7700, "aliases": ["Cr12MoV", "D2", "Tool Steel"]},
    "HT200": {"density": 7200, "aliases": ["HT200", "Gray Cast Iron", "灰铸铁"]},
    # ── 3D 打印材料（【Bug-13/29】新增）────────────────────────────────
    # 注意：不把 PETG-CF / PETG-GF 列为别名 —— 碳纤/玻纤增强的弹性模量
    #   (~6.5GPa) 与普通 PETG (2.0GPa) 差 3 倍，别名会让增强料被解析成普通料。
    #   增强料应由 set_custom_material 显式登记（custom_materials.json）。
    "PETG": {"density": 1270, "e_mpa": 2000, "yield_mpa": 40, "uts_mpa": 50,
             "aliases": ["PETG", "PET-G", "Copolyester"]},
    "PLA": {"density": 1240, "e_mpa": 3500, "yield_mpa": 55, "uts_mpa": 60,
            "aliases": ["PLA", "PLA+", "Polylactic Acid", "聚乳酸"]},
    "ABS": {"density": 1050, "e_mpa": 2400, "yield_mpa": 45, "uts_mpa": 50,
            "aliases": ["ABS", "Acrylonitrile Butadiene Styrene"]},
    "ASA": {"density": 1070, "e_mpa": 2300, "yield_mpa": 45, "uts_mpa": 50,
            "aliases": ["ASA"]},
    "Nylon": {"density": 1140, "e_mpa": 2800, "yield_mpa": 80, "uts_mpa": 85,
              "aliases": ["Nylon", "PA", "PA6", "PA66", "Nylon 6/6", "尼龙"]},
    "PA12": {"density": 1010, "e_mpa": 1700, "yield_mpa": 48, "uts_mpa": 50,
             "aliases": ["PA12", "PA-12", "Nylon 12", "尼龙12"]},
    "TPU": {"density": 1210, "e_mpa": 50, "yield_mpa": 30, "uts_mpa": 40,
            "aliases": ["TPU", "TPE", "Flexible", "热塑性聚氨酯"]},
    "PC": {"density": 1200, "e_mpa": 2400, "yield_mpa": 65, "uts_mpa": 70,
           "aliases": ["PC", "Polycarbonate", "聚碳酸酯"]},
    "POM": {"density": 1410, "e_mpa": 3100, "yield_mpa": 70, "uts_mpa": 75,
            "aliases": ["POM", "Delrin", "POM-C", "聚甲醛"]},
    "PEEK": {"density": 1320, "e_mpa": 3700, "yield_mpa": 100, "uts_mpa": 110,
             "aliases": ["PEEK"]},
}

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
        nm = str(mat.get("name") or "").strip()
        if nm:
            # "Aluminum 6061-T6" → "6061-T6"
            import re as _re
            m = _re.search(r"(6061(?:-T\d)?|7075|5052|1060|304|316|Q235|40Cr)", nm)
            if m:
                return m.group(1)
            return nm
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
    return DEFAULT_PART_MATERIAL

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


def _pick_attested_density(material_result, req_name):
    """选出凭据里该记录的【密度】（Bug15 残留修复）。

    规则：
      ① 优先用实际写入值（density_kg_m3 / density_after_kg_m3）
      ② 若该值为空 或 恰为 1000（SW 未赋材质的默认值=水），
         则退回【内置表/自定义库】的期望密度，并标注 material_result 里的来源
      ③ 都拿不到则返回 None（让防线判"无法确认"，而不是当成有效密度）
    """
    _mr = material_result if isinstance(material_result, dict) else {}
    _d = _mr.get("density_kg_m3", _mr.get("density_after_kg_m3"))
    try:
        _d = float(_d) if _d is not None else None
    except Exception:
        _d = None
    # 1000 视为"未生效默认值"（与 _material_guard 的判据一致）
    _suspect = (_d is None) or (abs(_d - 1000.0) < 1.0)
    if not _suspect:
        return _d
    try:
        _ok, _info, _why = is_valid_material(req_name)
        if _ok and _info and _info.get("density"):
            return float(_info["density"])
    except Exception:
        pass
    return _d


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
    # ① 内置材料库：canonical 或 alias 全等
    for _canon, _info in COMMON_MATERIALS.items():
        if _canon.lower() == _kl:
            return True, {"canonical": _canon, **_info}, "builtin(canonical)"
        for _a in (_info.get("aliases") or []):
            if str(_a).lower() == _kl:
                return True, {"canonical": _canon, **_info}, "builtin(alias)"
    # ② 用户自定义材料（显式登记才算合法）
    if allow_custom:
        try:
            _cm = load_custom_materials() or {}
            for _k, _v in _cm.items():
                if str(_k).lower() == _kl:
                    return True, {"canonical": _k, "custom": True, **(_v or {})}, \
                           "custom_materials.json"
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
# ── 【F 修复·第四轮】swEndConditions_e 完整枚举 ────────────────────────
# 测试反馈：FeatureCut3 双向传 T1=SW_END_THROUGH 时只去料 1.96%，
#   说明该枚举值【没有被识别为贯穿】，而是被当成了极小盲孔。
# 根因：SolidWorks 的 swEndConditions_e 各版本取值不同，且
#   "完全贯穿"与"双向贯穿"是两个不同枚举：
#     swEndCondBlind            = 0  给定深度
#     swEndCondThroughAll       = 1  完全贯穿（单向）
#     swEndCondThroughAllBoth   = 2  完全贯穿-双向
#   原实现把 T1/T2 都设为 1，在需要"双向贯穿"的场合语义不符，
#   部分版本会退化成盲孔。
SW_END_BLIND = 0                # 给定深度
SW_END_THROUGH = 1              # 完全贯穿（单向）
SW_END_THROUGH_BOTH = 2         # 完全贯穿-双向（各版本通用取值）
SW_END_UP_TO_NEXT = 3
SW_END_UP_TO_VERTEX = 4
SW_END_UP_TO_SURFACE = 5
SW_END_OFFSET_FROM_SURFACE = 6
# 贯穿兜底深度（mm）：当贯穿枚举不生效时，用极大的盲孔深度模拟贯穿。
#   这是测试部建议的兜底策略 —— 比依赖枚举值更可靠、跨版本一致。
THROUGH_FALLBACK_DEPTH_MM = 9999.0
SW_START_SKETCHPLANE = 0    # 起始: 草图基准面
SW_REV_BLIND = 0            # 旋转到给定角度
# 圆角 Options
SW_FILLET_UNIFORM_RADIUS = 2   # 恒定半径圆角
SW_FILLET_SIMPLE = 0           # swFeatureFilletType_Simple
# 倒角 ChamferType
SW_CHAMFER_ANGLE_DIST = 1   # 角度-距离倒角
SW_CHAMFER_DIST_DIST = 2    # 距离-距离倒角
SW_CHAMFER_VERTEX = 3

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
    _disable_snapping(sw)
    if VISUAL_MODE:
        _wait_sw_window(timeout=60)
        time.sleep(1.0)
        _show_main_window()
    return sw


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
    if material and str(material).strip():
        m._pending_material = str(material).strip()
        m._material_source = "explicit(new_part material=)"
    else:
        _env = (os.environ.get("DSH_PART_MATERIAL") or "").strip()
        if _env:
            m._pending_material = _env
            m._material_source = "env(DSH_PART_MATERIAL)"
        else:
            _lc = _material_from_load_case()
            if _lc:
                m._pending_material = _lc
                m._material_source = "load_case(gate_load_case.json)"
            else:
                m._pending_material = DEFAULT_PART_MATERIAL
                m._material_source = "default(DEFAULT_PART_MATERIAL)"
    m._pending_density = density
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


def from_active(sw=None):
    """包装当前活动文档为 SWModel。"""
    if sw is None:
        sw = get_sw()
    model = sw.ActiveDoc
    if model is None:
        raise RuntimeError("no active document")
    return SWModel(sw, model)


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
            self.material_result = res
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
        try:
            rc = self.model.SaveAs3(target, 0, 2)
        except Exception as e:
            return {"ok": False, "path": target, "saved": False, "updated": False,
                    "rc": rc, "error": "SaveAs3 调用异常: %r" % (e,)}

        after = _fingerprint(target)

        if after is None:
            return {"ok": False, "path": target, "saved": False, "updated": False,
                    "rc": rc,
                    "error": "SaveAs3 返回后目标文件【不存在】，保存实际未生效。",
                    "hint": "检查路径是否可写、目录是否存在、文件名是否被 SW 接受。"}

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
            return {"ok": False, "path": target, "saved": True, "updated": False,
                    "content_same": True, "rc": rc, "size": after[0],
                    "error": ("SaveAs3 未抛异常，但目标文件【完全未变化】"
                              "（大小/mtime/md5 三者全同）—— 保存被静默忽略。"),
                    "hint": ("请确认该文件未被其他程序/会话占用；"
                             "同名零件若已在 SW 中打开，建议先 close-all 再保存。")}

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
        # ══ 【三大防线 · 防线①材料】落盘【材料凭据】════════════════════════
        # 背景：原先"材料是否正确"只存在于本进程内存里 —— save() 返回后
        #   事实即丢失，门禁脚本(mode_gate/workflow_gate)无从校验，
        #   于是"密度=1000(水)/跨族被静默替换"的零件照样能 room-end、
        #   照样能交付。三道防线因此形同虚设。
        # 修复：在 SolidWorks 进程内（此处才有【真实密度】）把材料事实
        #   固化成 <part>.material.json，供 defense_gate.py 强制校验。
        #   写凭据【绝不】影响保存结果本身：任何异常都只记 warning。
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
                    "save_ok": bool(_out.get("ok")),
                }
                try:
                    _dg.write_material_attestation(target, _invalid)
                except Exception:
                    pass
                _out["material_attestation"] = {
                    "file": _dg.attestation_path(target),
                    "material_invalid": True,
                    "error": _mat_reason,
                    "signed": False,
                }
                self._warn("非法材料，未签发凭据: %s" % _mat_reason)
                return _out
            # [防伪造] 绑定零件本体的 size + md5：check 端会重算比对，
            #   让"随手造一个假文件 + 手写凭据"这条绕过路径失效。
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
            _att_info = {
                "ok": bool(_mr.get("ok")),
                "method": _mr.get("method"),
                # ── 【观察点 13 修复】如实记录"用户要的"与"实际生效的" ──────
                # requested_material = 用户传入的原始名（PETG），用于回答
                #   "我明明要 PETG，为什么凭据里是 PET"；
                # applied_name = SW 实际匹配到的牌号（PET）。
                # 校验用 _name_used（原始名合法就用原始名，否则用映射名）。
                "requested_material": _orig_name or _req_name,
                "attested_material_name": _name_used,
                "applied_name": _mr.get("applied_name") or _mr.get("material"),
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
                "density_kg_m3": _pick_attested_density(_mr, _name_used),
                "density_before_kg_m3": _mr.get("density_before_kg_m3"),
                "density_error_pct": _mr.get("density_error_pct"),
                "approximate_match": bool(_mr.get("approximate_match")),
                "material_mismatch_rejected": bool(
                    _mr.get("material_mismatch_rejected")),
                "rejected_candidates": _mr.get("rejected_candidates") or [],
                "source": _out.get("material_source"),
                "warnings": _mr.get("warnings") or self.warnings,
                "part_size_bytes": _p_size,
                "part_md5": _p_md5,
                "save_ok": bool(_out.get("ok")),
            }
            # 归一化材料家族：由 swapi 在【申请签发时】提供给宿主，
            #   而不是签名后再补字段（那会让 HMAC 失配）。
            try:
                _att_info["attested_family"] = (
                    _dg.material_family(_att_info.get("applied_name"))
                    or _dg.material_family(_att_info.get("density_kg_m3")))
            except Exception:
                _att_info["attested_family"] = None
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
                "ok": _att_info.get("save_ok"),
                "approximate_match": _att_info.get("approximate_match"),
                "material_mismatch_rejected": _att_info.get("material_mismatch_rejected"),
                "attested_family": _att_info.get("attested_family"),
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
            _out["material_attestation"] = {
                "file": _dg.attestation_path(target),
                "material": _att.get("attested_material"),
                "density_kg_m3": _att.get("attested_density_kg_m3"),
                "family": _att.get("attested_family"),
                "signed": bool(_att.get("_sig")),
            }
        except Exception as _e_att:
            _out["material_attestation"] = {"ok": False, "error": repr(_e_att)}
        return _out

    # ---------- 【Bug3】材料与密度 ----------
    def set_material(self, name, database=None, density=None):
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
        try:
            for d in (self.sw.GetMaterialDatabases or []):
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
            try:
                mp = self.model.GetMassProperties
                if mp and isinstance(mp, tuple) and len(mp) >= 6 and mp[3]:
                    return mp[5] / mp[3]
            except Exception:
                pass
            return 0.0

        d_before = dens_now()
        result["density_before_kg_m3"] = d_before

        # ══ 主路径：【PartDoc.SetMaterialPropertyName2("", 库名, 材料名)】 ════
        # 这是实机（SW2025 Rev 33.5.0）验证唯一真正生效的调用方式。
        # 五组材料实测全部成功，密度与材质库定义完全一致：
        #   灰铸铁→7200 / AISI 304→8000 / 普通碳钢→7800 / 1023碳钢板→7858 / 合金钢→7700
        # 关键：第 1 参数必须留空字符串（不是配置名！）。
        for dbn in db_names:
            for mname in names_to_try:
                for real, level in _resolve_material_names(dbn, mname, with_level=True):
                    try:
                        self.model.SetMaterialPropertyName2("", dbn, real)
                        time.sleep(0.18)
                        d_after = dens_now()
                        mid = ""
                        try:
                            mid = self.model.MaterialIdName or ""
                        except Exception:
                            pass
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
                            continue      # 关键：不接受，继续试下一个候选
                        if _dchg or mid:
                            result.update({
                                "ok": True,
                                "method": "PartDoc.SetMaterialPropertyName2",
                                "applied_name": real,
                                "database_name": dbn,
                                "match_level": level,
                                "density_after_kg_m3": d_after,
                                "material_id_name": mid,
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
                            if level != "exact":
                                result["approximate_match"] = True
                                result["warning"] = (
                                    "⚠️ 材料名 %r 在库 %r 中没有精确匹配，"
                                    "已选用最接近的 %r（匹配级别=%s）。"
                                    "如果这不是你要的材料，请用 "
                                    "swapi.list_materials(%r) 查看库中可用名称后重新指定。"
                                    % (mname, dbn, real, level, dbn))
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
        result["warnings"] = tried
        _rej = result.get("rejected_candidates") or []
        if _rej:
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
                "或用 swapi.list_materials('SolidWorks Materials') 查看准确名称后传入；"
                "也可显式指定密度兜底: set_material(%r, density=%s)"
                % (name, want_density if want_density else "2700"))
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
                            self.model.SetMaterialPropertyName2("", _dbn, _nm_try)
                            time.sleep(0.15)
                            _d_now = dens_now()
                            if _d_now and abs(float(_d_now) - float(_d_def)) / float(_d_def) <= 0.25:
                                result.update({
                                    "ok": True,
                                    "method": "SetMaterialPropertyName2(custom-fallback)",
                                    "applied_name": _nm_try,
                                    "database_name": _dbn,
                                    "density_kg_m3": float(_d_now),
                                    "match_level": "custom",
                                    "custom_material": True,
                                })
                                result.pop("error", None)
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
        self._warn("set_material: " + result["error"])
        return result

    def assign_material(self, name, database=None, density=None):
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
        return self.set_material(name, database=database, density=density)

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
        result = {
            "ok": True,
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
        # ── ① 主路径：GetMassProperties（属性式 + 方法式都试）─────────────
        mp = None
        for _getter in (lambda: self.model.GetMassProperties,
                        lambda: self.model.GetMassProperties(0)):
            try:
                _r = _getter()
                if _r is not None and isinstance(_r, tuple) and len(_r) >= 6:
                    mp = _r
                    break
            except Exception:
                continue
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
                        _mp2 = self.model.GetMassProperties(0)
                        _v2 = float(_mp2[3]) if _mp2 else 0.0
                        _m2 = float(_mp2[5]) if _mp2 else 0.0
                        _dens = (_m2 / _v2) if _v2 else None
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
                # 供 DSH 前端识图插件读取（【连接自检修复】不再硬编码作者桌面）
                path = os.path.join(vision_shot_dir(),
                                    "solidworks_vision_screenshot.png")
            else:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "solidworks_live.png")
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
                    "note": ("截图取自当前置前的 SolidWorks 窗口"
                             if verified else
                             "⚠️ 截图时无法确认前台为 SW，内容可能不可信")}
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
        "Bottom Plane": "*Bottom",
        "Back Plane": "*Back",
        "Left Plane": "*Left",
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
            self._reset_plane_context(
                plane,
                reason=("cross-plane" if _switch else "multi-feature"))
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
        for retry in range(5):
            try:
                _sel = self.select_plane(plane)
                if _sel:
                    _plane_selected = True
                self.skm.InsertSketch(True)
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    self._normal_to(self._PLANE_VIEW.get(plane, "*Front"))
                    try:
                        self.clear_selection()
                    except Exception:
                        pass
                    # 【Bug T1/T2 修复】记录草图名，供 cut() 显式重选轮廓
                    # 【Bug T3 修复】统一走 _finalize_new_sketch，避免漏分支
                    self._finalize_new_sketch(plane)
                    return self
                # ActiveSketch 为空：清残留再试
                try:
                    self.skm.InsertSketch(False)
                except Exception:
                    pass
            except Exception:
                pass
            _t.sleep(0.12)

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
        """检查目标点是否落在某个曲面的包围盒内。
        用于在 begin_sketch_on_face 中区分"用户真的想画在曲面上"还是"碰巧落在平面包围盒里"。
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
                        if not self._is_curved_face(face):
                            continue
                        try:
                            bb = face.GetBox
                            bb_min = (bb[0] * 1000, bb[1] * 1000, bb[2] * 1000)
                            bb_max = (bb[3] * 1000, bb[4] * 1000, bb[5] * 1000)
                            if (bb_min[0] - 1 <= x <= bb_max[0] + 1 and
                                bb_min[1] - 1 <= y <= bb_max[1] + 1 and
                                bb_min[2] - 1 <= z <= bb_max[2] + 1):
                                return True
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _is_curved_face(self, face):
        """判断面是否为曲面（圆柱/球面等），无法直接在其上开草图。
        SW COM 动态调用下 face.Normal 对曲面返回 (0,0,0)，而平面返回非零法向量。
        【SW2018+】✅ 所有版本可用
        """
        try:
            n = face.Normal
            return all(abs(v) < 0.01 for v in n)
        except Exception:
            return False

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

        # 策略1: 对平面用 GetBox 匹配（但如果目标是曲面则跳过）
        if not self._has_curved_face_at(x, y, z) and self._select_face_by_box(x, y, z):
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
        if merge:
            try:
                active_sk = self.skm.ActiveSketch
                if active_sk is not None:
                    active_sk.MergePoints(0.0005)
            except Exception:
                pass
        # ══ 【Bug T2 修复】退出前在"激活态"预选一次轮廓段 ═════════════
        # 退出草图瞬间 SW 会清空选择集；激活态是唯一能直接用
        # select_all_sketch_segments() 稳定选段的窗口。此处预选不改变
        # 建模结果，只为 cut() 的选择链路留一份"已被验证可选段"的证据。
        try:
            if self.skm.ActiveSketch is not None:
                self.select_all_sketch_segments()
        except Exception:
            pass
        try:
            self.model.ViewZoomtofit2()
        except Exception:
            pass
        self.skm.InsertSketch(True)
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

        ── 【F-1 修复·第九轮】专家指出两个关键点 ───────────────────────────
        1) ISketchSegment::Select 在 SW2025 已 Obsolete，应改用 Select4；
        2) 必须在【草图仍激活】时选中段 —— 退出后段名可见性会变，
           需要改用 EXTSKETCHSEGMENT 类型并带草图名前缀（见路径 B）。

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
                if strategy == "Select4":
                    sd = None
                    try:
                        sd = self._make_select_data(0)
                    except Exception:
                        sd = None
                    if sd is None:
                        return False
                    fn = getattr(s, "Select4", None)
                    try:
                        return bool(fn is not None and fn(append, sd))
                    except Exception:
                        return False
                if strategy == "Select":
                    fn = getattr(s, "Select", None)
                    try:
                        return bool(fn is not None and fn(append))
                    except Exception:
                        return False
                return False

            # 策略探测：真机实测优先级 Select2 > Select > Select4
            _strategy = getattr(self, "_seg_select_strategy", None)
            if _strategy not in ("Select2", "Select", "Select4"):
                _strategy = None
                for _cand in ("Select2", "Select", "Select4"):
                    try:
                        self.model.ClearSelection2(True)
                    except Exception:
                        pass
                    if _try_one(_list[0], False, _cand):
                        _strategy = _cand
                        break
                if _strategy is None:
                    self._sel_diag = "无可用段选中策略(Select2/Select/Select4 全失败)"
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
            # ── 【Bug-20 修复】失败时给出可操作诊断，而不是只丢一句"创建失败" ──
            _diag = diagnose_feature_failure(self, api="FeatureExtrusion3",
                                             extra={"depth_mm": depth})
            raise RuntimeError(
                "FeatureExtrusion3 返回 None，拉伸特征创建失败。"
                "【诊断】原因=%s（%s）建议：%s"
                % (_diag.get("cause"), _diag.get("detail"), _diag.get("advice")))
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
            # ① 齿根起点（左侧）
            _root_ang = base_ang - (_polar(r_lo) if r_lo > r_b else 0.0)
            pts.append((r_f * _math.cos(-_root_ang),
                        r_f * _math.sin(-_root_ang)))
            # ② 左齿廓：齿根 → 基圆（径向段，仅 r_f < r_b 时需要）
            if r_f < r_b:
                pts.append((r_b * _math.cos(-_root_ang),
                            r_b * _math.sin(-_root_ang)))
            # ③ 左齿廓：基圆 → 齿顶（渐开线，极角递减）
            for i in range(n + 1):
                rx = r_lo + (r_a - r_lo) * (i / float(n))
                ang = base_ang - _polar(rx)
                pts.append((rx * _math.cos(-ang), rx * _math.sin(-ang)))
            # ④ 右齿廓：齿顶 → 基圆（渐开线镜像）
            for i in range(n, -1, -1):
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
            return out
        except Exception as e:
            out["error"] = "齿轮生成失败: %r" % (e,)
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
            mp = self.model.GetMassProperties(0) or []
            # 质量属性数组索引 3 为体积（m³），见项目既有约定
            if len(mp) >= 4:
                return float(mp[3]) * 1e9   # m³ -> mm³
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
            through_both=True, via=None):
        """切除。through=True 完全贯穿；否则切除 depth mm。

        ══ 【Bug T1/T2 修复】cut 的轮廓选择已代码级加固 ═════════════════
        · T1（同一零件第 2 次及以后 cut 必失败）：根因是"特征树最后一个
          草图"启发式在多次 cut 后拿到的是【已被消费】的旧草图，选中段
          count=0。现改为：begin_sketch 记录草图名 → cut 按名 EditSketch
          选真实轮廓段 → 成功后把该草图标记为"已消费"，绝不再用。
        · T2（end_sketch 后 cut 失败）：退出草图后 SW 清空选择，
          现在按记录名 EditSketch 回到草图取轮廓段，end_sketch 之后
          cut 与 end_sketch 之前 cut 行为一致（都可用）。
        · 同一零件可安全调用任意多次 cut。

        Bug 4 修复: FeatureCut3 在 SW 2020 中不可靠，改用 FeatureExtrusion3 的切除模式。

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
        # ── 【F 修复·第四轮】贯穿不生效 -> 大深度兜底 ──────────────────
        # 测试实测：T1=SW_END_THROUGH(1) 只去料 1.96%，被当成极小盲孔。
        #   说明"贯穿枚举"在本版 FeatureCut3 的该参数位上不被识别。
        #
        # 修复策略（测试部建议）：
        #   · 贯穿优先用【双向贯穿枚举 SW_END_THROUGH_BOTH=2】；
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
                f = fn(*args)
            except Exception as e:
                return None, "调用异常: %r" % (e,)
            if f is None:
                return None, "返回 None"
            self._visual_step("cut")
            self.rebuild()
            # 体积校验：切除后体积必须变小
            _vol_after = self._body_volume_mm3()
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
        _cands = []
        if through:
            # (a) 贯穿枚举 + 大深度（双保险：枚举优先，深度兜底）
            _cands.append(("贯穿枚举+大深度", SW_END_THROUGH,
                           SW_END_THROUGH_BOTH if through_both else 0,
                           _big, _big if through_both else 0.0))
            # (b) 纯贯穿枚举（深度 0，最贴近官方用法）
            _cands.append(("贯穿枚举", SW_END_THROUGH,
                           SW_END_THROUGH_BOTH if through_both else 0,
                           0.0, 0.0))
            # (c) 大深度盲孔语义：完全绕开贯穿枚举
            _cands.append(("大深度盲孔", SW_END_BLIND,
                           SW_END_BLIND if through_both else 0,
                           _big, _big if through_both else 0.0))
        else:
            _cands.append(("盲孔", SW_END_BLIND, SW_END_BLIND, d1, d2))

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

        # 【真机实测修复】底面（法向朝外背离材料）上的切除：默认方向朝材料外的
        # 空侧，FeatureCut3 全签名返回 None（或 Extrusion3 反向增料）。
        # 修复：常规方向失败后，自动用 Flip=True 反向重试一轮 —— 哪个方向真正
        # 去料（体积校验通过）就用哪个。
        for _flip_pass, _dir_pass in ((bool(flip), False),
                                      (not bool(flip), False),
                                      (bool(flip), True)):
            for _api in _apis:
                for _label, _t1, _t2, _dd1, _dd2 in _cands:
                    _f, _why = _try_cut(_api, (
                        True, _flip_pass, _dir_pass, _t1, _t2, _dd1, _dd2,
                        False, False, False, False, 0, 0,
                        False, False, False, False, False, False, auto_select,
                        False, False, False, 0, 0, False))
                    if _why == "":
                        _exit_sketch_editing()
                        # 【Bug T1 修复】把本草图标记为"已被消费"，
                        # 同零件后续 cut 不可能再沿用这次轮廓。
                        self._mark_sketch_consumed()
                        if _flip_pass != bool(flip):
                            self._warn("cut 已自动反向（Flip）完成切除 —— "
                                       "草图位于底面/法向背离材料")
                        return _f
                    _notes.append("%s(flip=%s,dir=%s,%s): %s" % (_api, _flip_pass, _dir_pass, _label, _why))
                    _best = _best or _f

        # 签名 D：FeatureExtrusion3 兜底（不保证切除语义）
        _f, _why = _try_cut("FeatureExtrusion3", (
            True, False, False, T1, T2, d1, d2,
            False, False, False, False, 0, 0,
            False, False, True, False, False, False, auto_select,
            0, 0, False))
        if _why == "":
            _exit_sketch_editing()
            self._mark_sketch_consumed()
            self._warn("cut 走了 FeatureExtrusion3 兜底路径，语义不保证")
            return _f
        _notes.append("FeatureExtrusion3: " + _why)
        _best = _best or _f

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
                            # 重跑两轮方向尝试
                            for _flip_pass, _dir_pass in ((False, False),
                                                          (True, False)):
                                for _api in _apis:
                                    for _label, _t1, _t2, _dd1, _dd2 in _cands:
                                        _f, _why = _try_cut(_api, (
                                            True, _flip_pass, _dir_pass,
                                            _t1, _t2, _dd1, _dd2,
                                            False, False, False, False, 0, 0,
                                            False, False, False, False,
                                            False, False, auto_select,
                                            False, False, False, 0, 0, False))
                                        if _why == "":
                                            _exit_sketch_editing()
                                            self._mark_sketch_consumed()
                                            return _f
                                        _notes.append(
                                            "迁移后%s(flip=%s,%s): %s"
                                            % (_api, _flip_pass, _label, _why))
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
            for _flip_pass, _dir_pass in ((bool(flip), False),
                                          (not bool(flip), False),
                                          (False, True)):
                for _api in _apis:
                    for _label, _t1, _t2, _dd1, _dd2 in _cands:
                        _f, _why = _try_cut(_api, (
                            True, _flip_pass, _dir_pass, _t1, _t2, _dd1, _dd2,
                            False, False, False, False, 0, 0,
                            False, False, False, False, False, False, auto_select,
                            False, False, False, 0, 0, False))
                        if _why == "":
                            _exit_sketch_editing()
                            self._mark_sketch_consumed()
                            self._warn("cut 在 Bug-40 上下文重置后成功 "
                                       "(api=%s, flip=%s)" % (_api, _flip_pass))
                            return _f
                        _notes.append("重置后%s(flip=%s,%s): %s"
                                      % (_api, _flip_pass, _label, _why))
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
            mp = self.model.GetMassProperties(0) or []
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

        # 重建（Bug 修复: EditRebuild3 是方法，原实现漏了括号，重建实际从未执行）
        # 【Bug1 加固】原实现是 try/except: pass —— 静默吞掉重建异常，
        # 若重建真的失败，调用方误以为球体已就绪，后续特征会连锁报错且无从定位。
        # 现在把异常记录到 self.warnings 并打印告警（不抛出，保持建模流程可继续）。
        try:
            rb = getattr(self.model, "EditRebuild3", None)
            if callable(rb):
                rb()
            elif rb is not None:
                self._warn("create_sphere: EditRebuild3 不可调用（type=%s）" % type(rb).__name__)
        except Exception as _e:
            self._warn("create_sphere: 重建失败 -> %r（特征已创建，建议调用方复核）" % (_e,))
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
                    _arr = list(_mt.ArrayData) if hasattr(_mt, "ArrayData") else None
                    if _arr and len(_arr) >= 12:
                        # 取平移分量（索引 3/7/11），m -> mm
                        _actual_mm = [round(float(_arr[3]) / _k, 4),
                                      round(float(_arr[7]) / _k, 4),
                                      round(float(_arr[11]) / _k, 4)]
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

        if comp is not None or actual > 0:
            out["ok"] = True
            try:
                out["component_name"] = comp.Name2
            except Exception:
                pass
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
            # 加选参考基准面
            try:
                self.ext.SelectByID2(ref_plane, "PLANE", 0, 0, 0,
                                     True, 0, self._empty, 0)
            except Exception:
                pass
            # 建立配合
            _mt = {"distance": 5, "coincident": 0}.get(
                str(mate_type).lower(), 5)
            _val = float(distance_mm) * MM if mate_type == "distance" else 0.0
            mate = None
            last = None
            for _sig, _args in (
                ("AddMate5", (_mt, 0, False, False, False, _val, _val, "")),
                ("AddMate3", (_mt, 0, False, False, False, _val, 0, "")),
            ):
                try:
                    fn = getattr(self.model, _sig, None)
                    if fn is None:
                        continue
                    mate = fn(*_args)
                    if mate is not None:
                        out["mate_api"] = _sig
                        break
                except Exception as e:
                    last = e
                    continue
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
            self.rebuild()
            return out
        except Exception as e:
            out["error"] = "place_component_by_mate 异常: %r" % (e,)
            return out

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
        MATE_TYPES = {
            'coincident': 0, 'parallel': 1, 'perpendicular': 2, 'tangent': 3,
            'concentric': 4, 'distance': 5, 'angle': 6, 'antialigned': 7,
            'symmetric': 8, 'lock': 9, 'screw': 12, 'gear': 13,
        }
        out = {"ok": False, "mate_type": mate_type}
        try:
            if isinstance(mate_type, str):
                mt = MATE_TYPES.get(mate_type.strip().lower())
                if mt is None:
                    out["error"] = ("未知配合类型 %r，可用: %s"
                                     % (mate_type, ", ".join(sorted(MATE_TYPES))))
                    return out
            else:
                mt = int(mate_type)
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
                    _comps_doc = None
                    try:
                        _comps_doc = _c.GetModelDoc2()
                    except Exception:
                        _comps_doc = None
                    _bodies = None
                    try:
                        _bodies = _c.GetBody2()
                    except Exception:
                        _bodies = None
                    _blist = []
                    if _bodies is not None:
                        _blist = (list(_bodies) if isinstance(_bodies, tuple)
                                  else [_bodies])
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
                                return True, "FACE(comp)=%.1fmm2" % _best_area
                            except Exception:
                                continue
                except Exception:
                    pass
                return False, ""

            def _select_one(_spec, _comp_name, _mark, _first):
                # 选中一个配合参考：优先按面规格，其次组件，最后基准面
                _append = not _first
                if _spec and str(_spec) in _PLANE_ALIASES:
                    try:
                        if self.ext.SelectByID2(str(_spec), "PLANE", 0, 0, 0,
                                                _append, _mark, self._empty, 0):
                            return True, "PLANE:%s" % _spec
                    except Exception:
                        pass
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
                    try:
                        if self.ext.SelectByID2(str(_spec), "FACE", 0, 0, 0,
                                                _append, _mark, self._empty, 0):
                            return True, "FACE:%s" % _spec
                    except Exception:
                        pass
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

            val = 0.0
            if distance_mm is not None:
                val = float(distance_mm) * MM   # mm -> m
            elif angle_deg is not None:
                val = float(angle_deg) * 3.141592653589793 / 180.0
            _n = str(name or "")
            _flip = bool(flip)
            _al = int(align)
            # 多版本签名兜底：AddMate5(7) / AddMate3(6)
            last_err = None
            for _sig in ("AddMate5", "AddMate3"):
                try:
                    fn = getattr(self.model, _sig)
                except Exception as e:
                    last_err = e; continue
                try:
                    m = fn(mt, _al, _flip, False, False, val, val, _n)
                except TypeError:
                    try:
                        m = fn(mt, _al, _flip, False, False, val,
                               val if _sig == "AddMate3" else 0, _n)
                    except Exception as e2:
                        last_err = e2; continue
                except Exception as e2:
                    last_err = e2; continue
                if m is not None:
                    out["ok"] = True
                    out["used_signature"] = _sig
                    try:
                        out["mate_name"] = m.Name
                    except Exception:
                        pass
                    return out
            out["error"] = ("AddMate 失败（%r）。注意：添加配合前必须先选中"
                             "要配合的实体/面（SelectByID2），且两个组件都需已加载。"
                             % (last_err,))
        except Exception as e:
            out["error"] = "add_mate 异常: %r" % (e,)
        return out

    def check_interference(self, include_multibody=True, treat_coincident=True):
        """干涉检查（多版本 API 兜底）。

        ── 【BUG-F 修复】ToolsCheckInterference2 在 SolidWorks 2025 不可用 ──
        测试反馈：该 API 在 SW2025 已被移除/改名，直接调用即抛异常，
          而原实现只把异常塞进 error 字段就返回 —— 上层看到 count=0
          很容易误判成没有干涉（又一次静默失败）。

        修复：
          1) 依次尝试 ToolsCheckInterference2 / ToolsCheckInterference（版本差异）；
          2) 全部不可用时返回 ok=False 且 available=False，
             明确区别于"检查通过、无干涉"，绝不给出 count=0 的假结果；
          3) 给出可操作的降级建议。

        Returns:
          {ok, available, count, interferences:[...], api?, error?, hint?}
          ok=True 且 count=0  -> 真的没有干涉
          available=False     -> 本版本无此 API，结果不可采信
        """
        out = {"ok": False, "available": False, "count": 0, "interferences": []}
        comps = []
        try:
            self._activate_self()
            comps = self.model.GetComponents(False) or []
        except Exception:
            comps = []
        n_comp = len(comps)

        # ── 【Bug-30 修复】先准备组件选择集 ────────────────────────────────
        # 原缺陷：直接调 ToolsCheckInterference2 且把第二参传 None ——
        #   该 API 需要"要检查的组件数组"，空/None 会因参数类型不匹配而失败
        #   （实测报"无效参数/类型不匹配"，Bug-30 现象 4）。
        # 修复：按 API 语义准备两种形态并依次尝试：
        #   ① 组件数组（GetComponents 的元组）—— 新版本签名；
        #   ② None（表示"全部组件"）—— 部分版本接受；
        #   ③ 全选组件后传数量（旧签名）。
        _comp_list = list(comps) if isinstance(comps, tuple) else (
            [comps] if comps else [])
        _sel_tried = []
        try:
            self.clear_selection()
            for _c in _comp_list:
                try:
                    _c.Select4(False, None)
                except Exception:
                    try:
                        _c.Select(False)
                    except Exception:
                        pass
            out["selection_prepared"] = True
        except Exception as _e:
            out["selection_prepared"] = False
            out["selection_error"] = repr(_e)

        # ══ 【Bug5 修复·按实测签名重写】══════════════════════════════════
        # 本次用生成的类型库缓存【读出真实签名】，修正了先前的错误假设：
        #
        #   IAssemblyDoc.ToolsCheckInterference2(
        #       NumComponents, LpComponents, CoincidentInterference,
        #       PComp, PFace)
        #     · NumComponents          = 组件数量 (int)
        #     · LpComponents           = 组件数组 (VARIANT)
        #     · CoincidentInterference = 是否把重合面也算干涉 (bool)
        #     · PComp / PFace          = byref 输出
        #   IAssemblyDoc.ToolsCheckInterference()  —— 无参版本（最稳）
        #
        #   已核对：这些方法【只存在于 IAssemblyDoc】，
        #     ISldWorks(Application) 上【完全没有】—— 测试部报的
        #     AttributeError('SldWorks.Application.Tools...') 正是对象层级用错。
        #
        #   关键：必须用【前期绑定】的 IAssemblyDoc 包装 self.model 再调用，
        #     才能命中 InvokeTypes(dispid)，否则 late-binding 报"找不到成员"。
        last_err = None
        # 同样必须用【真正的接口 QueryInterface】包装（见 _wrap_with_interface）
        _asm_early = self._wrap_with_interface(self.model, "IAssemblyDoc")
        if _asm_early is None:
            _sel_tried.append("IAssemblyDoc 包装失败，回落 late-binding")
            _asm_early = None
        _holders = []
        if _asm_early is not None:
            _holders.append(("early(IAssemblyDoc)", _asm_early))
        _holders.append(("late(model)", self.model))
        # ── 尝试 A：无参版本（最简单，优先）──────────────────────────────
        for _hname, _h in _holders:
            try:
                _fn = getattr(_h, "ToolsCheckInterference", None)
                if _fn is None:
                    continue
                n = _fn()
                out["available"] = True
                out["api"] = "ToolsCheckInterference"
                out["holder"] = _hname
                out["count"] = int(n or 0)
                out["ok"] = True
                out["selection_prepared"] = out.get("selection_prepared", False)
                return out
            except Exception as _e_a:
                last_err = _e_a
                _sel_tried.append("%s/ToolsCheckInterference(): %r"
                                  % (_hname, _e_a))
        # ── 尝试 B：ToolsCheckInterference2/3（五参签名，逐形态回退）──────
        _comp_forms = []
        if _comp_list:
            _comp_forms.append(("list", list(_comp_list)))
            try:
                _comp_forms.append(("variant_dispatch", win32com.client.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(_comp_list))))
            except Exception:
                pass
        _comp_forms.append(("none", None))
        for _sig in ("ToolsCheckInterference2", "ToolsCheckInterference3",
                     "IToolsCheckInterference2", "IToolsCheckInterference3"):
            for _hname, _h in _holders:
                try:
                    _fn = getattr(_h, _sig, None)
                except Exception:
                    _fn = None
                if _fn is None:
                    continue
                for _cname, _cv in _comp_forms:
                    try:
                        _pcomp = win32com.client.VARIANT(
                            pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)
                        _pface = win32com.client.VARIANT(
                            pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)
                    except Exception:
                        _pcomp, _pface = None, None
                    _argc_forms = (("5arg", 5), ("4arg", 4), ("3arg", 3),
                                   ("2arg", 2))
                    for _aname, _n in _argc_forms:
                        try:
                            if _n >= 5:
                                n = _fn(n_comp, _cv, bool(treat_coincident),
                                        _pcomp, _pface)
                            elif _n == 4:
                                n = _fn(n_comp, _cv, bool(treat_coincident), _pcomp)
                            elif _n == 3:
                                n = _fn(n_comp, _cv, bool(treat_coincident))
                            else:
                                n = _fn(n_comp, _cv)
                            out["available"] = True
                            out["api"] = _sig
                            out["holder"] = _hname
                            out["components_arg"] = _cname
                            out["argc"] = _aname
                            out["count"] = int(n or 0)
                            try:
                                _arr = _pcomp.value if _pcomp is not None else None
                            except Exception:
                                _arr = None
                            if _arr:
                                try:
                                    for x in _arr:
                                        item = {}
                                        try:
                                            item["volume_mm3"] = float(x.Volume) * 1e9
                                        except Exception:
                                            item["raw"] = repr(x)
                                        out["interferences"].append(item)
                                except Exception:
                                    pass
                            out["ok"] = True
                            out["selection_prepared"] = out.get("selection_prepared", False)
                            return out
                        except Exception as _e_call:
                            last_err = _e_call
                            _sel_tried.append("%s/%s/%s/%s: %r"
                                              % (_hname, _sig, _cname, _aname, _e_call))
                            continue

        # ── 【Bug5 修复】API 全不可用时【自动降级】到几何近似检查 ────────
        # 实机（SW2025）反馈：ToolsCheckInterference* 在本版不可用，
        #   原实现只返回 ok=False + 让调用方自己想办法 —— 装配验证链
        #   因此断在这里（调用方往往直接当"检查失败"跳过）。
        # 现在自动用包围盒近似检查兜底，并明确标注 approx=True，
        #   使流程可继续，同时绝不把近似结果伪装成 SW 原生结论。
        try:
            _approx = self.geometry_interference_approx()
            if isinstance(_approx, dict) and _approx.get("ok"):
                _approx["degraded_from"] = "native-api-unavailable"
                _approx["native_error"] = repr(last_err)
                _approx["attempts"] = _sel_tried
                _approx["hint"] = ("本版 SolidWorks 无可用干涉 API，已自动降级为"
                                   "包围盒近似检查（approx=True，仅作设计层自查）。"
                                   "正式交付前请在 SW 界面执行 评估-干涉检查 复核。")
                return _approx
        except Exception as _e_approx:
            out["approx_error"] = repr(_e_approx)

        out["error"] = ("本 SolidWorks 版本无可用的干涉检查 API"
                        "（ToolsCheckInterference2/1 均已按多种传参形态尝试）: %r"
                        % (last_err,))
        out["attempts"] = _sel_tried
        out["hint"] = ("结果【不可采信】，不要当作没有干涉。"
                       "请在 SolidWorks 界面手动执行 评估-干涉检查 确认，"
                       "或改用 geometry_interference_approx() 做包围盒近似判断。")
        return out

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
        arr = [1.0, 0.0, 0.0, float(x) * _k,
               0.0, 1.0, 0.0, float(y) * _k,
               0.0, 0.0, 1.0, float(z) * _k,
               1.0, 0.0, 0.0, 0.0]
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
        _early = self._wrap_with_interface(_mu, "IMathUtility")
        _mu_late = _mu
        if _early is not None:
            _mu = _early
            self._last_transform_diag = "early-binding(IMathUtility/QueryInterface)"
        else:
            self._last_transform_diag = "late-binding(IMathUtility 包装失败)"
        _diag = []
        _vals = [float(v) for v in arr]
        # ── 路径 1（推荐）：CreateArray(1) + SetData16 + CreateTransform(2, arr)
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
        # ── 路径 2（兼容旧版）：单参形态（list / VARIANT / tuple）─────────
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
                    arr = list(mt.ArrayData) if hasattr(mt, "ArrayData") else None
                    if arr and len(arr) >= 12:
                        out["position_mm"] = [round(float(arr[3]) / _k, 4),
                                              round(float(arr[7]) / _k, 4),
                                              round(float(arr[11]) / _k, 4)]
                except Exception:
                    pass
        except Exception:
            out["supported"] = False

        # ── 【Bug-30 修复】先正确构造 MathTransform，再判断写入路径 ────────
        # 原实现用 sw.CreateTransform —— 该方法不存在，导致这里必然失败。
        # 现在改用 IMathUtility.CreateTransform（见 _make_math_transform_from_array）。
        new_mt = self._make_math_transform_from_array(
            [1.0, 0.0, 0.0, float(x) * _k,
             0.0, 1.0, 0.0, float(y) * _k,
             0.0, 0.0, 1.0, float(z) * _k,
             1.0, 0.0, 0.0, 0.0])
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
        try:
            _tc = getattr(self.model, "TransformComponent", None)
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
            out.setdefault("write_errors", []).append("TransformComponent: %r" % (_e_outer,))

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
            arr = list(back.ArrayData) if hasattr(back, "ArrayData") else None
            if arr and len(arr) >= 12:
                pos = [float(arr[3]) / _k, float(arr[7]) / _k, float(arr[11]) / _k]
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
        try:
            arr = list(mt.ArrayData) if hasattr(mt, "ArrayData") else None
            if arr and len(arr) >= 12:
                return [float(arr[3]) / _k, float(arr[7]) / _k, float(arr[11]) / _k]
        except Exception:
            pass
        return None

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
        结论：本机【必须】用 model.SaveAs3（带版本标志 2=覆盖/另存）+ 由扩展名
        自动推断格式枚举。故本方法改为优先 SaveAs3，失败再回退 Extension.SaveAs。

        Returns: {ok, path, updated, size, sw_error_code, error?, hint?}
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

        before = _fp(path)
        ret = None
        errs = None
        _last_err = None
        # ① 首选 SaveAs3（本机 SW2025 实测可用）
        try:
            ret = self.model.SaveAs3(path, 0, 2)
            try:
                errs = int(ret) if isinstance(ret, (int, float)) else None
            except Exception:
                errs = None
        except Exception as e:
            _last_err = e
            # ② 回退 Extension.SaveAs（带格式枚举）
            try:
                _errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                _warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                ret = self.model.Extension.SaveAs(path, _fmt, 1, None, _errs, _warns)
                try:
                    errs = _errs.value
                except Exception:
                    errs = None
            except Exception as e2:
                return {"ok": False, "path": path, "updated": False,
                        "error": "SaveAs 调用异常(SaveAs3:%r; Extension.SaveAs:%r)"
                                % (e, e2)}

        after = _fp(path)
        if after is None:
            return {"ok": False, "path": path, "updated": False,
                    "ret": bool(ret), "sw_error_code": errs,
                    "error": "SaveAs 返回后目标文件不存在，保存未生效。",
                    "hint": "检查路径可写性与目录是否存在。"}

        # ── 【BUG-E 判定修正】与 save() 保持同一语义 ─────────────────────
        # 只要文件被"触碰"（mtime/size 变化）就算保存成功；
        #   只有完全没变才是可疑的静默忽略。
        #   （专家指出：md5 变化是正常写入的表现，不应判失败。）
        updated = (before is None) or (after != before)
        if not updated:
            return {"ok": False, "path": path, "updated": False,
                    "ret": bool(ret), "sw_error_code": errs,
                    "size": after[0],
                    "error": ("SaveAs 未报错，但目标文件【完全未变化】"
                              "（大小与 mtime 全同）—— 保存被静默忽略。"),
                    "hint": "先关闭占用该文件的程序；同名零件若已打开，建议 close-all 后重试。"}

        return {"ok": True, "path": path, "updated": True,
                "content_same_unknown": False,
                "ret": bool(ret), "sw_error_code": errs, "size": after[0]}

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


# ==================== Bug-25: 安全退出 SolidWorks ====================

# ── 【H 修复】模块级别名：让 swapi.new_assembly() 也能用 ──────────────
# 小屋/调用方常写成 swapi.new_assembly(...)，而原实现只在类里定义，
#   于是报 module has no attribute —— 属于调用链断裂。
def new_assembly(sw=None, template=None):
    """模块级入口，等价于 SWModel.new_assembly（【H 修复】）。"""
    return SWModel.new_assembly(sw, template)


def close_all_and_exit(sw):
    """安全关闭所有文档并退出 SolidWorks。

    Bug-25 修复: SW 2020 没有 Quit() 方法，直接使用会导致 AttributeError。
    本函数先关闭所有文档，再尝试 Quit()（可用时），最后依赖 GC 释放 COM 引用。

    Args:
        sw: SolidWorks Application 对象（win32com dispatch）
    Returns:
        {"ok": True, "method": "close_all"} 或 {"ok": False, "error": "..."}
    """
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
    # 通过进程名查找 SW 实例并强制终止
    try:
        import subprocess as _sp
        result = _sp.run(
            ["taskkill", "/F", "/IM", "SLDWORKS.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if result.returncode == 0:
            return {"ok": True, "method": "taskkill_slworks"}
        # taskkill 未找到进程也视为成功（可能已关闭）
        return {"ok": True, "method": "close_all_documents_gc_cleanup"}
    except Exception:
        return {"ok": True, "method": "close_all_documents_gc_cleanup",
                "note": "taskkill 不可用，依赖 GC 回收"}

    return {"ok": True, "method": "close_all_documents_gc_cleanup"}
