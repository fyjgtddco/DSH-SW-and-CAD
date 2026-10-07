# -*- coding: utf-8 -*-
"""conn_state.py — 连接区：SW/CAD 连接的「探测 / 落盘 / 供 AI 判断」单一事实来源

═══ 这个模块解决什么问题 ═══════════════════════════════════════════════════

用户需求（原文）：
  "在设置那边有一个『连接区』，里面有『SW 连接』和『CAD 连接』。
   我想在流程中搞一个代码判断：如果设置那边 SW 连接成功了，
   那么 AI 就直接启动 SW 开始建模；如果没有连接，
   那么就按照之前的代码启动 SW 和 CAD。"

还有一个关键诉求：
  "AI 是如何在 Agent 上找 SW 和 CAD 的？如果是直接找其主要的文件地方的话，
   就感觉完全有点多此一举，就可以改为 AI 找对应的地址，
   让 AI 不用再找放在其他盘的时候。"

═══ 设计要点（三条） ═══════════════════════════════════════════════════════

【1】状态落成【JSON 文件】，不放 localStorage。
    为什么：设置页的 UI 状态若只存浏览器 localStorage，**AI/流程侧读不到**，
      "流程中做代码判断"根本无从实现。UI、AI、Python 三方必须读同一个文件。
    位置：`_store.state_dir()/connection_state.json`
      —— 与 mode_state.json / workflow_state.json 同一目录，
         自动跟随 DSH_STATE_DIR / 安装副本优先级，不另造一套约定。

【2】把【探测到的路径】一并落盘并交给 AI。
    为什么：`swapi.find_sldworks_exe()` 在注册表查不到时会【全盘符扫描】
      （C/D/E/F/Z…，本机 SW 装在 Z 盘）。这个扫描每次建模前都跑一遍
      纯属重复劳动，而且慢。既然连接区已经探测过了，就把结果缓存下来，
      AI 直接读 `paths.sldworks_exe` / `paths.acad_exe` 用，
      不必再自己去猜"装在哪块盘"。

【3】判断是【代码级】的，不依赖模型自觉。
    `is_connected(target)` 返回机器可判定的 True/False；
    `gate(target)` 给出可直接执行的分支结论（skip_startup / next）。
    流程里读这个结论决定"跳过启动排障"还是"走原有启动流程"。

═══ 与既有代码的关系（重要 · 不重复造轮子） ═══════════════════════════════

· SW 安装路径探测：复用 `swapi.find_sldworks_exe()`（已有注册表+全盘符逻辑）
· SW 进程探测   ：复用 `sw_bridge._sw_processes()`（tasklist + PowerShell 回退）
· CAD 进程探测  ：复用 `ac_bridge._acad_process_running()`
· CAD 连接      ：复用 `ac_bridge.get_ac()`（已内建"未运行则不激活 COM"安全前置）
· 状态目录      ：复用 `_store.state_dir()`（与门禁状态同目录）

本模块【只负责】探测编排 + 落盘 + 判断，不重复实现底层探测。
"""

import os
import sys
import json
import time

# 状态文件名（与 mode_state.json / workflow_state.json 并列）
STATE_NAME = "connection_state.json"

# 连接的"新鲜度"上限（秒）。超过则视为过期，需要重新探测。
#   理由：SW/CAD 进程可能被用户手动关掉；陈旧状态会让流程误判"已连接"
#   从而跳过必要的启动步骤，最后在建模时才失败（更难排查）。
STALE_AFTER_SEC = 900  # 15 分钟


# ══════════════════════════════════════════════════════════════════════════
# 状态文件读写
# ══════════════════════════════════════════════════════════════════════════

def state_path():
    """连接状态文件的绝对路径（与门禁状态同目录）。"""
    try:
        import _store
        return _store.state_path(STATE_NAME)
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            STATE_NAME)


def _empty_state():
    return {
        "schema": "dsh-connection-state/1",
        "updated_at": None,
        "updated_at_str": None,
        "sw": {"connected": False, "running": None, "pid": None,
               "processes": [], "checked_at": None, "error": None},
        "cad": {"connected": False, "running": None, "pid": None,
                "processes": [], "checked_at": None, "error": None},
        # 【连接区扩展】Abaqus（无 COM 接口，探测进程 + 安装路径）
        "abaqus": {"connected": False, "running": None, "pid": None,
                   "processes": [], "checked_at": None, "error": None,
                   "interface": "process-only（Abaqus 无 COM 自动化接口）"},
        # 【诉求2】探测到的安装路径 —— 交给 AI 直接复用，避免重复全盘扫描
        "paths": {
            "sldworks_exe": None,
            "acad_exe": None,
            "abaqus_exe": None,
            "scanned_drives": [],
            "resolved_at": None,
        },
        # 【自动启动】最近一次「启动」按钮的执行日志（供 UI 展示 + 一键发给 DSH 诊断）
        "launch": {
            "sw": None,
            "cad": None,
            "abaqus": None,
        },
    }


def load():
    """读取连接状态（不存在/损坏时返回空状态，绝不抛异常）。"""
    p = state_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                # 补齐缺字段（兼容将来新增键）
                base = _empty_state()
                for k, v in base.items():
                    d.setdefault(k, v)
                if not isinstance(d.get("paths"), dict):
                    d["paths"] = base["paths"]
                return d
    except Exception:
        pass
    return _empty_state()


def save(state):
    """原子写入连接状态（先写临时文件再替换，避免读到半截 JSON）。"""
    p = state_path()
    d = os.path.dirname(p)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    state["updated_at"] = time.time()
    state["updated_at_str"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return p
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


def age_sec(state=None):
    """状态距今多少秒；无时间戳返回 None。"""
    st = state or load()
    t = st.get("updated_at")
    if not t:
        return None
    try:
        return max(0.0, time.time() - float(t))
    except Exception:
        return None


def is_stale(state=None):
    """状态是否已过期（超过 STALE_AFTER_SEC 或无时间戳）。"""
    a = age_sec(state)
    return (a is None) or (a > STALE_AFTER_SEC)


# ══════════════════════════════════════════════════════════════════════════
# 路径发现（诉求2：让 AI 不用自己找盘符）
# ══════════════════════════════════════════════════════════════════════════

def _find_acad_exe():
    """探测 AutoCAD 可执行文件路径。

    搜索顺序（最可靠 → 兜底）：
      ① 环境变量 DSH_ACAD_EXE（用户显式指定，最高优先）
      ② 注册表【递归】搜索 AcadLocation（跨任意安装盘/任意目录）
      ③ 运行中进程的可执行路径
      ④ 常见安装目录 × 全部盘符
      ⑤ 兜底：从注册表拿到的目录直接拼 acad.exe

    ── 【实机事故修复 · 2026-10-04】三个叠加缺陷 ────────────────────────
    实测现象：AutoCAD 2020 明明装着（`Z:\\7-Zip\\AutoCAD 2020\\acad.exe`
      真实存在、5.8MB），连接区却报
        "未找到 acad.exe —— 已扫描系统上全部盘符的常见安装目录"，
      即"装没装都找不到"，把用户引向"是不是没装"的错误方向。

    缺陷1（注册表只查一层）：
      原实现枚举 `HKLM\\SOFTWARE\\Autodesk\\AutoCAD` 下的【直接子键】
        （R23.1）然后在该层读 AcadLocation —— 但真实的 AcadLocation
        在【第三层】：`...\\AutoCAD\\R23.1\\ACAD-3001:804\\AcadLocation`。
      实测该层只有 `(default)`，读任何值名都 FileNotFoundError。
      ⇒ 修法：改为【递归】遍历（深度受限），任何层级出现 GeoLocation
        类值都能命中。

    缺陷2（只扫 Program Files）：
      常见目录只列了 `Program Files\\Autodesk` 等，而本机 AutoCAD 装在
      `Z:\\7-Zip\\AutoCAD 2020` —— 完全不在 Program Files 下。
      ⇒ 修法：注册表递归命中优先；全盘扫描时把目录名放宽
        （含 "AutoCAD"/"Autodesk" 关键字的一级目录）。

    缺陷3（承诺了却没实现的环境变量）：
      原错误文案写着"可设置环境变量 DSH_SW_EXE / DSH_ACAD_EXE 显式指定
      路径"，但全代码库【没有任何地方读这两个变量】——
      用户照着提示设了也没用。⇒ 修法：本函数与
      `_find_sldworks_exe_env()` 真正读取它们。
    """
    # ── ① 环境变量：用户显式指定（最高优先，用于非常规安装）──────────
    _env = (os.environ.get("DSH_ACAD_EXE") or "").strip().strip('"')
    if _env:
        for _c in (_env,
                   os.path.join(_env, "acad.exe"),
                   os.path.join(_env, "acadlt.exe")):
            try:
                if os.path.isfile(_c):
                    return os.path.abspath(_c)
            except Exception:
                pass

    cands = []      # 注册表收集到的"目录"候选（最后拼 acad.exe 用）
    found = None

    # ── ② 注册表【递归】搜索（跨任意盘/任意目录，最可靠）───────────────
    if sys.platform == "win32":
        try:
            import winreg
            _VALUE_NAMES = ("AcadLocation", "Location", "InstallDir",
                            "InstallLocation", "ProductPath")
            _MAX_DEPTH = 4

            def _walk(hive, path, depth, out_vals):
                """递归遍历注册表键，收集含安装路径性质的值。"""
                if depth > _MAX_DEPTH:
                    return
                try:
                    k = winreg.OpenKey(hive, path)
                except Exception:
                    return
                try:
                    # 先读本层的候选值
                    for vn in _VALUE_NAMES:
                        try:
                            val, _t = winreg.QueryValueEx(k, vn)
                            if isinstance(val, str) and val.strip():
                                out_vals.append(val.strip())
                        except Exception:
                            pass
                    # 再递归子键
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                            i += 1
                        except OSError:
                            break
                        _walk(hive, path + "\\" + sub, depth + 1, out_vals)
                finally:
                    try:
                        winreg.CloseKey(k)
                    except Exception:
                        pass

            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for base in (r"SOFTWARE\Autodesk\AutoCAD",
                             r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD"):
                    vals = []
                    _walk(hive, base, 0, vals)
                    for v in vals:
                        # 注册表值可能直接是可执行文件，也可能是目录
                        low = v.lower()
                        if low.endswith(".exe"):
                            try:
                                if os.path.isfile(v):
                                    found = os.path.abspath(v)
                                    break
                            except Exception:
                                pass
                        if v not in cands:
                            cands.append(v)
                    if found:
                        break
                if found:
                    break
        except Exception:
            pass

    # 先从注册表目录候选里拼 acad.exe（本机就是这条命中）
    if not found:
        for c in cands:
            try:
                for name in ("acad.exe", "Acad.exe", "acadlt.exe", "AcadLT.exe"):
                    p = os.path.join(c, name)
                    if os.path.isfile(p):
                        found = os.path.abspath(p)
                        break
                if found:
                    break
            except Exception:
                continue

    # ── ③ 运行中进程反推（最准：它正在跑就说明这个 exe 能用）──────────
    if not found:
        try:
            import subprocess
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-Process acad,acadlt -ErrorAction SilentlyContinue | "
                 "Select-Object -First 1).Path"],
                capture_output=True, text=True, timeout=20)
            _p = (out.stdout or "").strip()
            if _p and _p.lower().endswith(".exe") and os.path.isfile(_p):
                found = os.path.abspath(_p)
        except Exception:
            pass

    # ── ④ 常见安装目录 × 全部盘符（兜底）────────────────────────────
    if not found:
        try:
            import swapi
            drives = swapi._all_drive_letters()
        except Exception:
            drives = ["C:"]
        _exe_names = ("acad.exe", "acadlt.exe")
        # 放宽：不只 Program Files，也扫盘符根下含 AutoCAD 关键字的一级目录
        for d in drives:
            bases = [
                os.path.join(d + "\\", "Program Files", "Autodesk"),
                os.path.join(d + "\\", "Program Files (x86)", "Autodesk"),
                os.path.join(d + "\\", "Autodesk"),
            ]
            # 盘符根下任意一级目录，名字含 autocad/autodesk 的也纳入
            try:
                for _e in os.listdir(d + "\\"):
                    if "autocad" in _e.lower() or "autodesk" in _e.lower():
                        bases.append(os.path.join(d + "\\", _e))
            except Exception:
                pass
            for base in bases:
                if not os.path.isdir(base):
                    continue
                try:
                    for root, _dirs, files in os.walk(base):
                        for fn in files:
                            if fn.lower() in _exe_names:
                                found = os.path.abspath(os.path.join(root, fn))
                                break
                        if found:
                            break
                except Exception:
                    continue
                if found:
                    break
            if found:
                break

    # ── ⑤ 【连接区加固】有界深度遍历（兜住多层嵌套目录）────────────────
    #   实测缺口：glob/os.walk 白名单只能覆盖 1~2 层，而本机 AutoCAD 装在
    #     `Z:\7-Zip\AutoCAD 2020\acad.exe` —— "盘符根 → 7-Zip → 软件目录"
    #     三层嵌套。复用 swapi.walk_drive_for_exe()（与 SW 侧同一实现）。
    if not found:
        try:
            import swapi
            for d in swapi._all_drive_letters():
                _hit = swapi.walk_drive_for_exe(
                    d, ("acad.exe", "acadlt.exe"),
                    keyword="autocad", max_depth=3)
                if not _hit:
                    # 有的装法目录名是 Autodesk\AutoCAD 20xx，关键字再放宽一档
                    _hit = swapi.walk_drive_for_exe(
                        d, ("acad.exe", "acadlt.exe"),
                        keyword="autodesk", max_depth=3)
                if _hit:
                    found = _hit
                    break
        except Exception:
            pass
    return found


def discover_paths(force=False):
    """探测并返回 SW / CAD / Abaqus / NX 安装路径。

    【诉求2】把结果落盘，AI 直接读，不必重复"猜装在哪块盘"。

    缓存策略：已解析过且未 force 时，直接复用文件里的结果 ——
      除非该路径已不存在（例如软件被卸载/换盘），那时才重新扫描。
    """
    st = load()
    paths = st.get("paths") or {}
    sw_exe = paths.get("sldworks_exe")
    acad_exe = paths.get("acad_exe")
    abq_exe = paths.get("abaqus_exe")
    ans_exe = paths.get("nx_exe")
    cached_ok = (not force) and (sw_exe or acad_exe or abq_exe or ans_exe)
    if cached_ok:
        # 缓存有效性：至少有一个已解析路径仍真实存在
        if any(p and os.path.exists(p) for p in (sw_exe, acad_exe, abq_exe, ans_exe)):
            return paths
    # 重新探测
    _sw = None
    try:
        import swapi
        _sw = swapi.find_sldworks_exe()
    except Exception:
        pass
    _ac = _find_acad_exe()
    # ── 【连接区扩展】CAE 安装路径（Abaqus / NX）──────────────────────
    #   与 SW/CAD 同一套"跨盘符 × 常见安装目录"思路；找不到就是 None，
    #   不影响其它目标。
    _cae_exe = {}
    for _t, _cfg in _CAE_TARGETS.items():
        try:
            _cae_exe[_t] = _find_cae_exe(_cfg)
        except Exception:
            _cae_exe[_t] = None
    try:
        import swapi
        _drives = swapi._all_drive_letters()
    except Exception:
        _drives = []
    paths = {
        "sldworks_exe": _sw,
        "acad_exe": _ac,
        "abaqus_exe": _cae_exe.get("abaqus"),
        "nx_exe": _cae_exe.get("nx"),
        "scanned_drives": _drives,
        "resolved_at": time.time(),
        "resolved_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    st["paths"] = paths
    save(st)
    return paths


# ══════════════════════════════════════════════════════════════════════════
# 探测（进程层 + COM 层）
# ══════════════════════════════════════════════════════════════════════════

def _sw_process_list():
    """枚举 SLDWORKS.EXE 进程（tasklist，零副作用）。

    ⚠️ 【为什么不 import sw_bridge】────────────────────────────────────────
    本模块若 `import sw_bridge` 会形成【循环导入】：
      sw_bridge → conn_state（cmd_conn_probe 里 import）→ sw_bridge
    更危险的是：sw_bridge 作为 `__main__` 运行时，Python 会把它
      【再导入一次】成一个独立的模块对象，而该对象可能解析到
      另一份副本（安装副本）→ 拿到的是**旧版本**，属性缺失。
      实测现象：`AttributeError: module 'sw_bridge' has no attribute
      '_sw_processes'`（明明源码里有）。
    因此：进程探测在本模块【自带实现】，只依赖标准库。
      COM 连接才去 import swapi / ac_bridge（它们不反向依赖本模块）。
    """
    import subprocess
    procs = []
    err = None
    try:
        o = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.EXE",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        txt = (o.stdout or "").strip()
        if "SLDWORKS.EXE" in txt.upper():
            import csv
            import io
            for row in csv.reader(io.StringIO(txt)):
                if not row:
                    continue
                nm = (row[0] or "").strip().strip('"')
                if nm.upper() != "SLDWORKS.EXE":
                    continue
                try:
                    pid = int(str(row[1]).strip().strip('"'))
                except (ValueError, IndexError):
                    pid = None
                procs.append({
                    "pid": pid, "name": nm,
                    "mem": (row[4].strip().strip('"')
                            if len(row) > 4 else None),
                })
        return procs, None
    except Exception as e:
        err = "tasklist 失败: %r" % (e,)
    # PowerShell 回退
    try:
        import subprocess
        o = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process sldworks -ErrorAction SilentlyContinue | "
             "Select-Object Id,ProcessName | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20)
        txt = (o.stdout or "").strip()
        if txt:
            data = json.loads(txt)
            if isinstance(data, dict):
                data = [data]
            for d in (data or []):
                procs.append({"pid": d.get("Id"),
                              "name": d.get("ProcessName"), "mem": None})
        return procs, None
    except Exception as e2:
        err = "%s; powershell 回退也失败: %r" % (err or "", e2)
    return [], err


def probe_sw(with_com=True):
    """探测 SolidWorks：进程层 + （可选）COM 连接层。

    返回 dict：{running, pid, processes, connected, error}

    ⚠️ 【重要】with_com=False 时【只做进程探测】，一个 COM 调用都不发 ——
       避免"只想看看开没开"却把 SolidWorks 拉起来。
    """
    out = {"running": None, "pid": None, "processes": [],
           "connected": False, "error": None}
    # ① 进程层（零副作用，自带实现，不 import sw_bridge —— 见函数注释）
    procs, err = _sw_process_list()
    out["processes"] = procs or []
    out["running"] = bool(procs)
    if procs:
        out["pid"] = procs[0].get("pid")
    if err:
        out["error"] = err
        out["running"] = None
    # ② COM 层：只有"已在运行"时才去连（未运行绝不 Dispatch，避免拉起 SW）
    if with_com and out.get("running"):
        try:
            import swapi
            sw = swapi.get_sw()
            if sw is not None:
                _ = sw.RevisionNumber       # 真实读取以确认连接
                out["connected"] = True
                out["revision"] = str(sw.RevisionNumber)
                out["binding"] = getattr(sw, "_dsh_binding", None)
                try:
                    out["pid"] = out["pid"] or sw.GetProcessID
                except Exception:
                    pass
        except Exception as e:
            out["connected"] = False
            out["error"] = "COM 连接失败: %r" % (e,)
    elif not out.get("running"):
        out["connected"] = False
    return out


def probe_cad(with_com=True):
    """探测 AutoCAD：进程层 + （可选）COM 连接层。

    ⚠️ 复用 ac_bridge 的【安全前置检查】：AutoCAD 未运行时
       `Dispatch('AutoCAD.Application')` 会触发 COM 激活去【启动】CAD，
       既慢又可能在安装/许可异常时把 CAD 弄崩（已实测事故）。
       因此未运行就只报"未运行"，绝不尝试连接。
    """
    out = {"running": None, "pid": None, "processes": [],
           "connected": False, "error": None}
    # ① 进程层（零副作用）
    try:
        import ac_bridge
        # ⚠️ 用 getattr 兜底：若 ac_bridge 被解析到【旧副本】（不含本函数），
        #    不能让整个探测崩掉 —— 回退到自带的 tasklist 探测。
        _fn = getattr(ac_bridge, "_acad_process_running", None)
        if callable(_fn):
            running = _fn()
        else:
            running = bool(_acad_process_list())
        out["running"] = bool(running)
        if running:
            out["processes"] = _acad_process_list()
            if out["processes"]:
                out["pid"] = out["processes"][0].get("pid")
    except Exception as e:
        # ac_bridge 不可用 → 直接用自带进程探测（不阻断）
        try:
            lst = _acad_process_list()
            out["running"] = bool(lst)
            out["processes"] = lst
            if lst:
                out["pid"] = lst[0].get("pid")
        except Exception:
            out["error"] = "进程探测失败: %r" % (e,)
    # ② COM 层：仅当已在运行
    if with_com and out.get("running"):
        ac = None
        try:
            import ac_bridge
            ac = ac_bridge.get_ac()
            if ac:
                st = ac.status()
                out["connected"] = bool(st.get("connected"))
                out["drawing"] = st.get("drawing")
        except Exception as e:
            out["connected"] = False
            out["error"] = "COM 连接失败: %r" % (e,)
        finally:
            try:
                if ac is not None:
                    ac.close()
            except Exception:
                pass
    return out


def _acad_process_list():
    """枚举 acad.exe 进程（tasklist）。"""
    import subprocess
    procs = []
    try:
        o = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        txt = (o.stdout or "").strip()
        if "acad.exe" in txt.lower():
            import csv
            import io
            for row in csv.reader(io.StringIO(txt)):
                if not row:
                    continue
                nm = (row[0] or "").strip().strip('"')
                if nm.lower() != "acad.exe":
                    continue
                try:
                    pid = int(str(row[1]).strip().strip('"'))
                except (ValueError, IndexError):
                    pid = None
                procs.append({"pid": pid, "name": nm})
    except Exception:
        pass
    return procs


def probe_all(with_com=True, resolve_paths=True):
    """完整探测 SW + CAD + Abaqus + NX（+ 路径），并落盘。返回最新状态 dict。"""
    st = load()
    now = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    sw = probe_sw(with_com=with_com)
    sw["checked_at"] = now_str
    st["sw"] = sw
    cad = probe_cad(with_com=with_com)
    cad["checked_at"] = now_str
    st["cad"] = cad
    for _t in ("abaqus", "nx"):
        _cae = probe_cae(_t)
        _cae["checked_at"] = now_str
        st[_t] = _cae
    if resolve_paths:
        st["paths"] = discover_paths()
    save(st)
    return st


def _abaqus_process_list():
    """列出 Abaqus 相关进程（零副作用，只读 tasklist）。

    Abaqus 的进程名因版本/组件而异，常见有：
      · ABQcaeK.exe / abaqus.exe / ABQcaeG.exe（CAE 图形界面）
      · standard.exe / explicit.exe（求解器）
      · ABQLauncher.exe / abqlauncher.exe（启动器）
    """
    return _tasklist_match(("ABQCAEK", "ABQCAEG", "ABAQUS", "ABQLAUNCHER",
                            "STANDARD", "EXPLICIT", "ABQSMA"))


def _nx_process_list():
    """列出 Siemens NX 相关进程（零副作用，只读 tasklist）。

    常见进程名：
      · ugraf.exe —— NX 的图形主进程（最典型）
      · nx.exe / ug_ii.exe —— 启动器/交互
      · NXNASTRAN.exe —— NX Nastran 求解器
      · ugslmd.exe —— NX 许可证服务（License Daemon）
    """
    return _tasklist_match(("UGRAF", "NXNASTRAN", "UGSLMD",
                            "UG_II", "NX.EXE"))


def _tasklist_match(keywords):
    """通用 tasklist 关键词匹配（返回 [{name, pid}]）。

    抽出来给 Abaqus / NX 共用 —— 两者的探测方式完全相同（都是"按进程名
    关键词匹配"），只有关键词不同，没必要写两份重复代码。
    """
    found = []
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20,
            creationflags=(0x08000000 if sys.platform == "win32" else 0))
        for line in (out.stdout or "").splitlines():
            _l = line.strip()
            if not _l.startswith('"'):
                continue
            try:
                _parts = [x.strip('"') for x in _l.split('","')]
                _nm = _parts[0]
                _pid = int(_parts[1])
            except Exception:
                continue
            _up = _nm.upper()
            if any(_k in _up for _k in keywords):
                found.append({"name": _nm, "pid": _pid})
    except Exception:
        pass
    return found


# ── 通用 CAE 目标描述表（Abaqus / NX）────────────────────────────────
# 两者的能力边界相同：都【没有 COM 自动化接口】，集成方式是命令行/脚本调用
#   （abaqus python / run_journal）。因此共用一套探测逻辑，只在
#   "安装目录关键词 / 可执行文件名 / 显示名 / 集成方式" 上区分。
_CAE_TARGETS = {
    "abaqus": {
        "display": "Abaqus",
        "install_dirs": ("SIMULIA", "Abaqus", "Dassault Systemes",
                         "Program Files\\SIMULIA",
                         "Program Files\\Dassault Systemes",
                         "Program Files (x86)\\SIMULIA"),
        "exe_names": ("abaqus.bat", "abq*.exe", "ABQcaeK.exe", "ABQcaeG.exe",
                      "abaqus.exe", "abqlauncher.exe"),
        "process_fn": _abaqus_process_list,
        "integration": ("`abaqus python <脚本>` 或 `abaqus cae noGUI=<脚本>`"),
        "version_re": r"(20\d{2}|6\.\d{1,2})",
    },
    "nx": {
        "display": "NX",
        # Siemens NX 的安装根常见为 <盘>:\Program Files\Siemens\NX<版本>
        "install_dirs": ("Siemens", "Siemens NX", "NX",
                         "Program Files\\Siemens",
                         "Program Files\\Siemens NX",
                         "Program Files (x86)\\Siemens"),
        "exe_names": ("ugraf.exe", "nx.exe", "ug_ii.exe", "run_journal.exe",
                      "NXNASTRAN.exe"),
        "process_fn": _nx_process_list,
        "integration": ("`run_journal <脚本.py>`（NX Open 批处理）"
                        " 或 NX Nastran 求解器命令行"),
        "version_re": r"(NX\s*\d+(?:\.\d+)?|1[89]\.\d|2[0-9]\.\d|20\d{2})",
    },
}


def probe_cae(target):
    """探测 CAE 软件（Abaqus / NX）：进程层 + 安装路径。

    ── 设计取舍：不做 COM 连接 ────────────────────────────────────────────
    Abaqus 与 NX 都**没有可用的 COM 自动化接口**（它们的脚本接口是各自的
      Python/APDL，需在其进程内或命令行下运行）。因此这里【只做进程与安装
      探测】，不尝试任何"建立会话"的动作：
        · 进程在跑 → running=True；
        · 能找到安装路径 → 记录 exe（供后续命令行调用）；
        · connected 语义 = "检测到可用安装或进程"，
          而非"已建立 COM 会话"，避免给出误导性的"已连接"。
    这与 SW/CAD 的按钮外观完全一致，但结论口径如实反映能力边界
      （返回体里带 interface 字段说明）。
    """
    _cfg = _CAE_TARGETS.get(str(target or "").lower())
    _disp = (_cfg or {}).get("display", str(target))
    out = {"running": None, "pid": None, "processes": [],
           "connected": False, "error": None,
           "interface": "process-only（%s 无 COM 自动化接口）" % _disp,
           "integration": (_cfg or {}).get("integration")}
    if not _cfg:
        out["error"] = "未知 CAE 目标: %r" % (target,)
        return out
    # ① 进程层（零副作用）
    try:
        lst = (_cfg.get("process_fn") or (lambda: []))()
        out["running"] = bool(lst)
        out["processes"] = lst
        if lst:
            out["pid"] = lst[0].get("pid")
    except Exception as e:
        out["error"] = "进程探测失败: %r" % (e,)
    # ② 安装路径（跨盘符；有安装即视为"可用"，命令行可调用）
    try:
        exe = _find_cae_exe(_cfg)
        if exe:
            out["exe"] = exe
            out["revision"] = _cae_version_from_path(exe, _cfg.get("version_re"))
            out["connected"] = True
            out["note"] = ("检测到 %s 安装：%s。%s 无 COM 接口，集成方式为 %s。"
                           % (_disp, exe, _disp, _cfg.get("integration") or "命令行调用"))
        elif out.get("running"):
            out["connected"] = True
            out["note"] = "检测到 %s 进程在运行（未定位到安装路径）" % _disp
        else:
            out["note"] = "未检测到 %s 进程或安装" % _disp
    except Exception as e:
        out["error"] = (out.get("error") or "") + " 路径探测失败: %r" % (e,)
    return out


def _find_cae_exe(cfg):
    """跨盘符查找 CAE 可执行文件（按 cfg 的 install_dirs × exe_names）。

    只做有限深度扫描（在已知安装目录下 glob），避免全盘遍历拖慢探测。
    """
    import glob as _glob
    pats = []
    try:
        roots = []
        # 复用 SW 那套"枚举真实盘符"的思路（A~Z 中存在的盘）
        try:
            import swapi as _sw
            _dr = getattr(_sw, "_drive_roots", None)
            if callable(_dr):
                roots = list(_dr())
        except Exception:
            roots = []
        if not roots:
            import string
            roots = ["%s:\\" % c for c in string.ascii_uppercase
                     if os.path.isdir("%s:\\" % c)]
        for _d in roots:
            for _sub in (cfg.get("install_dirs") or ()):
                pats.append(os.path.join(_d, _sub))
    except Exception:
        return None
    for _base in pats:
        if not os.path.isdir(_base):
            continue
        for _nm in (cfg.get("exe_names") or ()):
            try:
                for _hit in _glob.glob(os.path.join(_base, "**", _nm),
                                       recursive=True)[:1]:
                    if os.path.isfile(_hit):
                        return _hit
            except Exception:
                continue
    return None


def _cae_version_from_path(p, ver_re=None):
    """从路径里解析版本号（如 ...\\SIMULIA\\Abaqus\\2023\\... → Abaqus 2023）。"""
    import re as _re
    try:
        m = _re.search(ver_re or r"(20\d{2})", str(p or ""))
        return m.group(1) if m else None
    except Exception:
        return None


def probe_abaqus(with_com=True):
    """探测 Abaqus（保留旧签名，内部转调通用 probe_cae）。"""
    return probe_cae("abaqus")


def probe_nx(with_com=True):
    """探测 NX（内部转调通用 probe_cae）。"""
    return probe_cae("nx")


def probe_target(target, with_com=True):
    """只探测单个目标（'sw' | 'cad' | 'abaqus' | 'nx' | 占位项），其余字段保持原值，落盘。

    【连接区扩展】新增 'abaqus' / 'nx' —— 两者都无 COM 接口，
      只做进程/安装探测（见 probe_cae 的设计说明）。
    占位项（'placeholder*'）不探测、不报错：设置页里它们是未启用的
      预留位，探测一个不存在的目标只会制造噪音；这里明确返回
      "占位项，未接入探测"的结论，保证 UI 行为可预期。
    """
    st = load()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    t = str(target or "").lower()
    if t == "sw":
        sw = probe_sw(with_com=with_com)
        sw["checked_at"] = now_str
        st["sw"] = sw
    elif t == "cad":
        cad = probe_cad(with_com=with_com)
        cad["checked_at"] = now_str
        st["cad"] = cad
    elif t in _CAE_TARGETS:
        # Abaqus / NX 共用通用 CAE 探测（都无 COM，走进程+安装探测）
        _cae = probe_cae(t)
        _cae["checked_at"] = now_str
        st[t] = _cae
    elif t.startswith("placeholder"):
        # 占位项：如实记录"未接入"，绝不伪装成已连接/已检测
        st[t] = {
            "running": None, "connected": False, "checked_at": now_str,
            "placeholder": True,
            "note": "该栏目为预留占位项，尚未接入探测逻辑",
        }
    else:
        raise ValueError(
            "target 必须是 'sw' / 'cad' / 'abaqus' / 'placeholder*'，收到: %r"
            % (target,))
    # 路径一并刷新（若尚未解析过）
    if not (st.get("paths") or {}).get("sldworks_exe"):
        st["paths"] = discover_paths()
    save(st)
    return st


# ══════════════════════════════════════════════════════════════════════════
# 判断（给流程用的代码级结论）
# ══════════════════════════════════════════════════════════════════════════

# 全部合法的连接目标（新增目标只需在这里登记）
KNOWN_TARGETS = ("sw", "cad", "abaqus", "nx")


def _norm_target(target):
    """归一化 target 名，非法时返回 None。

    ── 【连接区扩展修复】消除"静默当 cad"的隐患 ──────────────────────────
    原实现是 `"sw" if target == "sw" else "cad"` —— 任何非 sw 的输入
      （包括拼错、新增的 abaqus/nx）都会被【静默当成 cad】，
      于是查的是 CAD 的连接状态，却回报给调用方，属"安静地算错"。
    现在只认 KNOWN_TARGETS，未知一律返回 None，由调用方明确报错。
    """
    t = str(target or "").strip().lower()
    if t in KNOWN_TARGETS:
        return t
    if t.startswith("placeholder"):
        return t          # 占位项允许通过（不参与连接判定）
    return None


def is_connected(target, allow_stale=False):
    """该目标是否【已连接】（机器可判定）。

    allow_stale=False（默认）时，过期状态一律视为"未连接"——
      宁可多启动一次，也不要因陈旧状态跳过启动、最后在建模时莫名其妙失败。

    ⚠️ 未知 target 一律返回 False（而非"当成 cad"）：宁可不判定，
      也不能把别的软件的状态当成它的。
    """
    st = load()
    if is_stale(st) and not allow_stale:
        return False
    key = _norm_target(target)
    if not key:
        return False
    return bool((st.get(key) or {}).get("connected"))


def gate(target="sw"):
    """【流程判断入口】返回可直接照着执行的分支结论。

    这是用户要的"在流程中搞一个代码判断"的落点：
      · skip_startup=True  → 连接区已连上，AI 直接进建模，跳过启动/排障
      · skip_startup=False → 按【原有流程】启动 SW（和 CAD），一步不动

    返回 dict（全部字段都是给 AI/脚本直接读的）：
      target, connected, skip_startup, next, paths, state_age_sec, stale, hint
    """
    st = load()
    t = _norm_target(target)
    if not t:
        # 未知 target：明确报错，绝不静默按 cad 处理
        return {
            "ok": False,
            "target": str(target),
            "error": "未知的连接目标 %r；合法值：%s"
                     % (target, " / ".join(KNOWN_TARGETS)),
            "connected": False,
            "skip_startup": False,
            "next": "请用合法的 --target 重新调用（sw / cad / abaqus / nx）。",
            "hint": "连接区新增了 Abaqus / NX 目标；拼写错误会被显式拒绝，不再退化成 CAD。",
        }
    stale = is_stale(st)
    connected = is_connected(t)
    paths = st.get("paths") or {}
    entry = st.get(t) or {}

    # 各目标的"下一步"文案（新增目标在此登记，避免落到默认分支说错话）
    _NEXT_OK = {
        "sw": "直接开始建模（跳过启动与排障）。",
        "cad": "直接使用现有 AutoCAD（跳过启动与排障）。",
        "abaqus": ("检测到 Abaqus 可用 —— 可直接用 `abaqus python <脚本>` "
                   "或 `abaqus cae noGUI=<脚本>` 提交分析。"),
        "nx": ("检测到 NX 可用 —— 可直接用 `run_journal <脚本.py>`"
               "（NX Open 批处理）提交作业。"),
    }
    _NEXT_NO = {
        "sw": ("按原有流程启动 SolidWorks（含《常见SW启动失败问题》7 步排查），"
               "启动后再建模。"),
        "cad": "按原有流程启动 AutoCAD。",
        "abaqus": ("未检测到 Abaqus —— 请在【设置 → 连接区】点『Abaqus连接』"
                   "探测安装路径，或先安装/启动 Abaqus。"),
        "nx": ("未检测到 NX —— 请在【设置 → 连接区】点『NX连接』"
                  "探测安装路径，或先安装/启动 NX。"),
    }
    if connected:
        nxt = _NEXT_OK.get(t, "目标已连接，可直接使用。")
        skip = True
    else:
        nxt = _NEXT_NO.get(t, "目标未连接，请先启动或安装。")
        skip = False

    hint = None
    if not connected and stale and (entry.get("checked_at")):
        hint = ("连接状态已过期（%.0f 秒前），判定为未连接 —— "
                "可先运行 conn-probe 重新探测。" % (age_sec(st) or 0))
    elif not entry.get("checked_at"):
        hint = ("从未探测过连接状态。请先在【设置 → 连接区】点『连接』，"
                "或运行 python sw_bridge.py conn-probe --target %s。" % t)

    out = {
        "ok": True,
        "target": t,
        "connected": connected,
        "skip_startup": skip,
        "next": nxt,
        "stale": stale,
        "state_age_sec": age_sec(st),
        "checked_at": entry.get("checked_at"),
        "paths": paths,
        "hint": hint,
    }
    # 把"该用哪个 exe"直接给出来 —— 免得 AI 再去全盘找（诉求2）
    _EXE_KEY = {"sw": "sldworks_exe", "cad": "acad_exe",
                "abaqus": "abaqus_exe", "nx": "nx_exe"}
    out["exe"] = paths.get(_EXE_KEY.get(t, "")) or entry.get("exe")
    if not out["exe"]:
        out["exe_hint"] = ("尚未解析到安装路径，可运行 "
                           "python sw_bridge.py conn-probe --resolve-paths")
    return out


def summary():
    """人读友好的连接区状态摘要（供 UI/CLI 展示）。"""
    st = load()
    a = age_sec(st)
    return {
        "ok": True,
        "state_file": state_path(),
        "updated_at": st.get("updated_at_str"),
        "age_sec": a,
        "stale": is_stale(st),
        "sw": st.get("sw"),
        "cad": st.get("cad"),
        "paths": st.get("paths"),
        "launch": st.get("launch"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 自动启动（「启动」按钮）
# ══════════════════════════════════════════════════════════════════════════
# 用户诉求原文：
#   "要不给俩个都搞一个按键，自动启动吧，不然一直点连接启动不了的，
#    就是启动成不需要 SW 自己跳出来的那种，然后要是报错的话，就贴出错误日志，
#    然后加上一个按键就是，按了就可以将错误日志直接发给 DSH 内部模型的，
#    然后 DSH 就可以根据错误日志去找到底是啥情况。"
#
# 设计要点：
#   ① 「连接」= 只探测（安全，默认）；「启动」= 显式启动进程（用户主动点击）。
#      两者分开，避免"只想看看"却把几十秒的 CAD/SW 拉起来。
#   ② 「不需要 SW 自己跳出来」= 启动后调用 _show_main_window()，
#      它会 SW_RESTORE + 最大化 主窗口，并【隐藏欢迎页】。
#   ③ 全流程留日志：每一步（找路径 / 拉起进程 / 等窗口 / 连 COM）都记进
#      state["launch"][target]，包含 stdout/stderr/returncode/耗时。
#      失败时 UI 能直接贴出日志，并能一键发给 DSH 模型诊断。

def _log_launch(target, ok, steps, error=None):
    """把一次启动尝试的日志写入状态文件（覆盖上一次）。"""
    st = load()
    st.setdefault("launch", {})
    st["launch"][target] = {
        "ok": bool(ok),
        "at": time.time(),
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error": error,
        # steps 是"步骤名 → {ok, detail, ...}"的有序列表，便于 UI 逐条展示
        "steps": steps,
    }
    save(st)
    return st["launch"][target]


def _step_result(name, ok, **kw):
    """构造一条启动步骤记录。"""
    d = {"step": name, "ok": bool(ok)}
    d.update(kw)
    return d


def _drive_hint():
    """返回"本次扫描了哪些盘符"的可读串（供错误信息展示）。"""
    try:
        import swapi
        _d = swapi._drive_roots()
        return ", ".join(x.rstrip("\\") for x in _d) if _d else "C:"
    except Exception:
        return "C:"


def launch(target, exe=None, wait_window=120, connect=True):
    """启动 SW / CAD 并（可选）连接，全程记录日志。

    Args:
        target: 'sw' | 'cad'
        exe:    可执行文件路径（None 则自动解析）
        wait_window: 等待主窗口/进程就绪的最长秒数
        connect: 启动后是否再走一次探测+连接

    Returns: {"ok":..., "target":..., "steps":[...], "error":..., "state":...}
    """
    import subprocess
    t = "sw" if str(target).lower() == "sw" else "cad"
    steps = []

    # ── 步骤 1：解析可执行文件路径（全盘符，任意盘都能找到）──────────────
    if not exe:
        try:
            paths = discover_paths()
            exe = (paths or {}).get(
                "sldworks_exe" if t == "sw" else "acad_exe")
        except Exception as e:
            exe = None
            steps.append(_step_result("resolve_path", False, error=repr(e)))
        else:
            steps.append(_step_result(
                "resolve_path", bool(exe),
                exe=exe,
                detail=("已定位可执行文件" if exe else
                        "未找到安装路径（已扫描全部盘符）")))
    else:
        steps.append(_step_result("resolve_path", os.path.exists(exe), exe=exe))

    if not exe or not os.path.exists(exe):
        _exe_name = "SLDWORKS.exe" if t == "sw" else "acad.exe"
        _env_name = "DSH_SW_EXE" if t == "sw" else "DSH_ACAD_EXE"
        _hint_reg = ("· 若软件确实已安装，多为此类情况：注册表里没有完整安装记录，"
                     "或装在【非常规目录】（例如 Z:\\7-Zip\\AutoCAD 2020）。"
                     if t == "cad" else
                     "· 若软件确实已安装，多为注册表缺少安装记录，"
                     "或装在非常规目录。")
        err = ("未找到 %s。\n"
               "已尝试：环境变量 %s → 注册表递归搜索 → 运行中进程反推 → "
               "全部盘符(%s)常见目录扫描。\n"
               "可能原因：① 软件未安装；② 安装在非常规目录；"
               "③ 安装目录无读取权限；④ 注册表无安装记录。\n"
               "%s\n"
               "【最可靠的办法】显式指定路径（支持指向 exe 本身或所在目录）：\n"
               "   PowerShell:  $env:%s = 'D:\\你的安装目录\\acad.exe'\n"
               "   或在系统环境变量里新建 %s。"
               % (_exe_name, _env_name, _drive_hint(), _hint_reg, _env_name,
                  _env_name))
        steps.append(_step_result("resolve_path", False, error=err))
        _log_launch(t, False, steps, error=err)
        return {"ok": False, "target": t, "steps": steps, "error": err}

    # ── 步骤 2：是否已在运行（已在运行就不重复启动）─────────────────────
    already = False
    try:
        if t == "sw":
            already = bool(_sw_process_list()[0])
        else:
            already = bool(_acad_process_list())
    except Exception:
        already = False
    steps.append(_step_result("check_running", True, already_running=already,
                              detail=("已在运行，跳过启动" if already
                                      else "未运行，需要启动")))

    proc_info = None
    if not already:
        # ── 步骤 3：拉起进程（分离启动，不阻塞本进程）──────────────────
        try:
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP：
            #   让 SW/CAD 独立于本 Python 进程存活，Python 退出不带走它。
            _flags = 0
            if sys.platform == "win32":
                _flags = (0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
            p = subprocess.Popen(
                [exe], cwd=os.path.dirname(exe),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_flags, close_fds=True)
            proc_info = {"pid": p.pid}
            steps.append(_step_result("spawn", True, pid=p.pid,
                                      detail="已拉起进程 PID %s" % p.pid))
        except Exception as e:
            err = "拉起进程失败: %r" % (e,)
            steps.append(_step_result("spawn", False, error=err))
            _log_launch(t, False, steps, error=err)
            return {"ok": False, "target": t, "steps": steps, "error": err}

        # ── 步骤 4：等待就绪 ─────────────────────────────────────────
        #   SW：等主窗口出现（可能几十秒，首次启动更久）
        #   CAD：等进程出现即可（CAD 的 COM 激活本身会等）
        deadline = time.time() + max(10, int(wait_window))
        appeared = False
        _last = None
        while time.time() < deadline:
            try:
                if t == "sw":
                    lst, _e = _sw_process_list()
                    appeared = bool(lst)
                    _last = lst
                else:
                    _last = _acad_process_list()
                    appeared = bool(_last)
            except Exception:
                appeared = False
            if appeared:
                break
            time.sleep(2.0)
        steps.append(_step_result(
            "wait_ready", appeared,
            waited_sec=round(max(0.0, wait_window - (deadline - time.time())), 1),
            processes=_last or [],
            detail=("进程已就绪" if appeared
                    else "等待超时：进程未出现")))
        if not appeared:
            err = ("进程已拉起但 %d 秒内未出现在进程列表中。\n"
                   "常见原因：① 许可证缺失/失效（sw_d.lic）；"
                   "② 缺少 netapi32.dll；③ 被杀软/沙箱拦截；"
                   "④ 首次启动极慢（可再等一会儿后点『连接』重试）。"
                   % wait_window)
            _log_launch(t, False, steps, error=err)
            return {"ok": False, "target": t, "steps": steps, "error": err}

        # 给 GUI 一点初始化时间，避免立刻 COM 连接被拒
        time.sleep(3.0)
    else:
        proc_info = {"already_running": True}

    # ── 步骤 5：让它"体面地出现"（隐藏欢迎页 / 最大化主窗口）─────────────
    #   SW 的欢迎页是独立小窗，会挡住视线；用户明确要"不需要 SW 自己跳出来"。
    if t == "sw":
        try:
            import swapi
            swapi._wait_sw_window(timeout=60)
            swapi._show_main_window(maximize=True)
            steps.append(_step_result("show_window", True,
                                      detail="主窗口已置前并最大化，欢迎页已隐藏"))
        except Exception as e:
            # 窗口处理失败不算致命：SW 可能仍在初始化
            steps.append(_step_result("show_window", False, error=repr(e),
                                      detail="窗口处理失败（不影响连接尝试）"))

    # ── 步骤 6：连接确认（读属性以验证 COM 真的可用）────────────────────
    connected = False
    if connect:
        try:
            if t == "sw":
                res = probe_sw(with_com=True)
            else:
                res = probe_cad(with_com=True)
            connected = bool(res.get("connected"))
            steps.append(_step_result(
                "connect", connected,
                detail=("COM 连接成功" if connected else "COM 连接失败"),
                **{k: v for k, v in res.items()
                   if k in ("error", "revision", "binding", "pid", "drawing")}))
            if not connected and res.get("error"):
                _log_launch(t, False, steps, error=str(res.get("error")))
                return {"ok": False, "target": t, "steps": steps,
                        "error": str(res.get("error")), "state": summary()}
        except Exception as e:
            steps.append(_step_result("connect", False, error=repr(e)))
            _log_launch(t, False, steps, error=repr(e))
            return {"ok": False, "target": t, "steps": steps,
                    "error": repr(e), "state": summary()}

    # 启动成功后再探一次，刷新状态文件（进程层信息也一并更新）
    try:
        probe_target(t, with_com=connect)
    except Exception:
        pass

    _log_launch(t, True, steps, error=None)
    return {"ok": True, "target": t, "steps": steps, "connected": connected,
            "error": None, "state": summary()}


def launch_log(target):
    """读取某目标最近一次启动日志（供 UI 展示 / 发给模型诊断）。"""
    st = load()
    t = "sw" if str(target).lower() == "sw" else "cad"
    lg = (st.get("launch") or {}).get(t)
    if not lg:
        return {"ok": False, "target": t, "error": "尚无启动日志"}
    return {"ok": True, "target": t, "log": lg}


def format_launch_log(target, for_model=True):
    """把启动日志渲染成【可读文本】，供 UI 展示或直接发给 DSH 模型诊断。

    for_model=True 时输出面向模型的诊断材料：包含步骤、错误、环境事实，
    让模型能据此判断"到底是许可证问题还是路径问题还是沙箱问题"。
    """
    st = load()
    t = "sw" if str(target).lower() == "sw" else "cad"
    lg = (st.get("launch") or {}).get(t)
    name = "SolidWorks" if t == "sw" else "AutoCAD"
    lines = []
    if not lg:
        return "[%s] 尚无启动日志 —— 请先在设置页点『启动』。" % name
    lines.append("=== %s 启动日志 ===" % name)
    lines.append("时间    : %s" % lg.get("at_str"))
    lines.append("结果    : %s" % ("成功" if lg.get("ok") else "失败"))
    if lg.get("error"):
        lines.append("错误    : %s" % lg.get("error"))
    lines.append("")
    lines.append("--- 步骤明细 ---")
    for s in (lg.get("steps") or []):
        mark = "OK " if s.get("ok") else "FAIL"
        line = "[%s] %s" % (mark, s.get("step"))
        for k in ("detail", "error", "exe", "pid", "waited_sec",
                  "already_running", "revision", "binding"):
            if s.get(k) not in (None, "", []):
                line += "\n        %s: %s" % (k, s.get(k))
        lines.append(line)
    if for_model:
        lines.append("")
        lines.append("--- 环境事实（供诊断）---")
        try:
            import swapi
            lines.append("盘符扫描      : %s" % (swapi._drive_roots(),))
            lines.append("SW 可执行     : %s" % swapi.find_sldworks_exe())
        except Exception as e:
            lines.append("SW 探测异常   : %r" % (e,))
        _p = st.get("paths") or {}
        lines.append("CAD 可执行    : %s" % _p.get("acad_exe"))
        try:
            import platform
            lines.append("Python        : %s (%s)" % (
                platform.python_version(), platform.platform()))
        except Exception:
            pass
        try:
            import win32com
            lines.append("pywin32       : %s" % win32com.__file__)
        except Exception as e:
            lines.append("pywin32       : 缺失 %r" % (e,))
        _swm = st.get("sw") or {}
        _cadm = st.get("cad") or {}
        lines.append("SW 进程/连接  : running=%s connected=%s pid=%s" % (
            _swm.get("running"), _swm.get("connected"), _swm.get("pid")))
        lines.append("CAD 进程/连接 : running=%s connected=%s pid=%s" % (
            _cadm.get("running"), _cadm.get("connected"), _cadm.get("pid")))
        lines.append("")
        lines.append("请据此判断失败根因，并给出下一步修复动作。")
    return "\n".join(lines)
