# -*- coding: utf-8 -*-
r"""settings.py —— 工程模式【本机设置】单一事实源（跨机器安全）

═══════════════════════════════════════════════════════════════════════════
【为什么需要这个模块】
用户在学校微机房实测到两个"换台电脑就出问题"的现象：

  ① 交付目录被写死成"桌面\test"
     workflow_gate._default_delivery_root() 与 mode_gate._default_work_dir()
     都无条件回落到 %USERPROFILE%\Desktop\test。于是换一台电脑（甚至只是换
     一个用户名），第一次跑任务就会在那台电脑的桌面上【凭空造出】一个 test
     文件夹 —— 用户原话："在学校的电脑的桌面上，也莫名其妙就出现了一个 test
     文件"。而且用户没有任何途径把它改到别处。

  ② 连接区的 SW/CAD 路径"记住"了作者本机的盘符
     connection_state.json 里存着 Z:\Program Files\SW\SLDWORKS.exe。
     这份状态随仓库/安装副本一起被拷到另一台电脑后，那台电脑上根本没有 Z 盘，
     于是每次"连接"都先撞一次不存在的路径，排查方向被带偏。

【本模块的解法】
把"跟【这台电脑】有关的设置"收敛到一个**只有一份**的文件里：
    <state_dir>/settings.json

  · 交付目录     → paths.delivery_root
  · SW / CAD / Python 可执行文件 → paths.sw_exe / cad_exe / python_exe
  · 全部设置都带【机器指纹】；指纹不匹配 = 这条设置来自别的电脑
    → 自动忽略（并在报告里明说"已忽略，因为来自另一台电脑"），
      绝不让别人的盘符在本机生效。

【优先级（读）】
  delivery_root:  环境变量 DSH_DELIVERY_ROOT > settings.json > 兜底
  sw_exe/cad_exe: 环境变量 DSH_SW_EXE / DSH_ACAD_EXE > settings.json > 自动探测

【为什么不用 localStorage】
  设置页在浏览器里，而真正干活的 Python / 宿主守卫读不到浏览器存储。
  UI、AI、Python 三方必须读同一份文件（与 connection_state.json 同一套理由）。
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time

SCHEMA = "dsh-engineering-settings/1"
FILE_NAME = "settings.json"

# 机器级设置的键（值只在本机有意义）
MACHINE_KEYS = ("delivery_root", "sw_exe", "cad_exe", "python_exe",
                "abaqus_exe", "webots_exe")

ENV_DELIVERY = "DSH_DELIVERY_ROOT"
ENV_SW = "DSH_SW_EXE"
ENV_CAD = "DSH_ACAD_EXE"
ENV_PY = "DSH_PYTHON"


# ══════════════════════════════════════════════════════════════════════════
# 路径 / 机器指纹
# ══════════════════════════════════════════════════════════════════════════

def settings_path():
    """settings.json 的绝对路径（与 mode_state / workflow_state 同目录）。"""
    try:
        import _store
        return _store.state_path(FILE_NAME)
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), FILE_NAME)


def _drives():
    """枚举真实存在的盘符（Windows）—— 用来构成指纹的一部分。"""
    out = []
    try:
        if sys.platform == "win32":
            import string
            for letter in string.ascii_uppercase:
                root = letter + ":\\"
                if os.path.exists(root):
                    out.append(letter)
        else:
            out.append("/")
    except Exception:
        pass
    return sorted(out)


def machine_fingerprint():
    """本机的稳定指纹（主机名 + 用户名 + 盘符集合）。

    ── 为什么用这三项 ────────────────────────────────────────────────────
    要判"这条设置是不是这台电脑的"，判据必须：
      · 换电脑一定变（主机名/用户名）；换盘符也要变（Z 盘没了）；
      · 同机重启、改名日期、升级都不变（不含时间戳、不含进程号）。
    注意：不把 CPU/网卡信息算进去 —— 虚拟机快照被克隆时那些会重复，
      反而判不出"其实是另一台机器"。
    """
    parts = []
    try:
        parts.append(socket.gethostname() or "")
    except Exception:
        parts.append("")
    parts.append(os.environ.get("USERNAME") or os.environ.get("USER") or "")
    parts.append(",".join(_drives()))
    raw = "|".join(parts).lower()
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def machine_info():
    return {
        "fingerprint": machine_fingerprint(),
        "hostname": (socket.gethostname() if True else ""),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "drives": _drives(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
    }


# ══════════════════════════════════════════════════════════════════════════
# 读写
# ══════════════════════════════════════════════════════════════════════════

def _empty():
    return {"schema": SCHEMA, "updated_at": None, "updated_at_str": None,
            "paths": {}}


def load():
    """读取设置（不存在/损坏/跨机器一律安全返回空结构，绝不抛异常）。"""
    p = settings_path()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("paths", {})
                if not isinstance(d["paths"], dict):
                    d["paths"] = {}
                return d
    except Exception:
        pass
    return _empty()


def save(state):
    """原子写入（先写临时文件再替换）。"""
    p = settings_path()
    d = os.path.dirname(p)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    state["updated_at"] = time.time()
    state["updated_at_str"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state.setdefault("schema", SCHEMA)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return p
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════════════════════════
# 单条设置的读 / 写（核心：跨机器自动忽略）
# ══════════════════════════════════════════════════════════════════════════

def _entry(state, key):
    v = (state.get("paths") or {}).get(key)
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        return {"value": v, "machine": None}
    return None


def _usable(value, key):
    """这条值在本机是否可信。

    · exe 类：必须文件真的存在；
    · delivery_root：允许暂时不存在（首次使用会创建），
      但它所在的【盘符】必须存在 —— 否则就是别人电脑上的 D:/E:。
    """
    if not value:
        return False, "值为空"
    if key.endswith("_exe"):
        if os.path.isfile(value):
            return True, ""
        return False, "文件不存在（可能来自另一台电脑或软件已卸载）"
    # 目录类
    drive = os.path.splitdrive(os.path.abspath(value))[0]
    if drive and not os.path.exists(drive + os.sep):
        return False, "盘符 %s 不存在（这条设置来自另一台电脑）" % drive
    return True, ""


def get(key, default=None, validate=True):
    """读一条设置。跨机器 / 已失效 → 返回 default，并在 note 里说明原因。

    返回 (value, note)。note 为空串表示"正常取到"。
    """
    state = load()
    e = _entry(state, key)
    if not e:
        return default, ""
    value = str(e.get("value") or "").strip().strip('"')
    if not value:
        return default, ""
    owner = e.get("machine")
    mine = machine_fingerprint()
    if owner and owner != mine:
        return default, ("该设置记录于另一台电脑（指纹 %s，本机 %s），"
                         "已忽略 —— 请在本机重新指定。" % (owner, mine))
    if validate:
        ok, why = _usable(value, key)
        if not ok:
            return default, "已失效：%s" % why
    return value, ""


def set_path(key, value):
    """写入一条【本机】设置（自动打上本机指纹）。value 为空 = 清除该条。"""
    if key not in MACHINE_KEYS:
        return {"ok": False, "error": "未知设置项 %r（可用：%s）"
                                     % (key, " / ".join(MACHINE_KEYS))}
    state = load()
    paths = state.setdefault("paths", {})
    v = str(value or "").strip().strip('"')
    if not v:
        paths.pop(key, None)
        p = save(state)
        return {"ok": True, "key": key, "value": None, "cleared": True,
                "file": p, "note": "已清除，回落到自动探测/默认值"}
    if key.endswith("_exe"):
        cand = os.path.abspath(v)
        if os.path.isdir(cand):
            # 允许用户给"安装目录"，自动拼可执行文件名
            names = {"sw_exe": ("SLDWORKS.exe", "sldworks.exe"),
                     "cad_exe": ("acad.exe", "acadlt.exe"),
                     "python_exe": ("python.exe",),
                     "abaqus_exe": ("abaqus.exe", "abqlauncher.exe"),
                     "webots_exe": ("webots.exe",)}
            hit = None
            for n in names.get(key, ()):
                c = os.path.join(cand, n)
                if os.path.isfile(c):
                    hit = c
                    break
            if not hit:
                return {"ok": False, "error": "目录里找不到可执行文件 %s"
                                             % (names.get(key) or "")}
            v = hit
        elif not os.path.isfile(cand):
            return {"ok": False, "error": "路径不存在：%s" % cand}
        else:
            v = cand
    else:
        v = os.path.abspath(v)
        drive = os.path.splitdrive(v)[0]
        if drive and not os.path.exists(drive + os.sep):
            return {"ok": False, "error": "盘符 %s 在本机不存在" % drive}
    paths[key] = {"value": v, "machine": machine_fingerprint(),
                  "host": socket.gethostname(), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    p = save(state)
    return {"ok": True, "key": key, "value": v, "file": p,
            "note": "已保存为本机设置（换电脑会自动忽略，不会误用别机的路径）"}


# ══════════════════════════════════════════════════════════════════════════
# 语义化读取（各模块统一调用这些，不要再各写各的默认值）
# ══════════════════════════════════════════════════════════════════════════

def _tools_output_dir():
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "output"))


def delivery_root():
    """交付根目录（所有任务的【上层】）。

    优先级：
      ① 环境变量 DSH_DELIVERY_ROOT（显式指定，最高）
      ② settings.json 的 delivery_root（设置页可改，本机有效）
      ③ 历史兼容：若 <用户桌面>\\test 已经存在（老用户目录里有旧任务），
         继续用它，避免"升级后找不到上次的成果"；
      ④ 兜底：<工程模式根>/output  —— ★ 不再凭空在桌面创建 test ★

    ── 关键行为变更（本次修复）────────────────────────────────────────────
    旧行为：桌面存在就直接返回 桌面\\test 并【在第一次建任务时创建它】。
      于是任何人第一次用工程模式，桌面都会莫名其妙多一个 test 文件夹
      （用户在学校微机房实测到这一点）。
    新行为：只有当 desktop\\test 【本来就存在】时才沿用；否则落到
      工程模式自己的 output 目录。要改就改设置页里的「交付目录」。
    """
    env = (os.environ.get(ENV_DELIVERY) or "").strip().strip('"')
    if env:
        try:
            return os.path.abspath(env), "env:%s" % ENV_DELIVERY
        except Exception:
            pass
    v, _note = get("delivery_root", None, validate=False)
    if v:
        try:
            return os.path.abspath(v), "settings.json"
        except Exception:
            pass
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if home:
        legacy = os.path.join(home, "Desktop", "test")
        try:
            if os.path.isdir(legacy):
                return legacy, "legacy-desktop-test(已存在，沿用)"
        except Exception:
            pass
    return _tools_output_dir(), "default:<root>/output"


def sw_exe():
    """SolidWorks 可执行文件（本机设置 > 环境变量 > None 交给自动探测）。"""
    env = (os.environ.get(ENV_SW) or "").strip().strip('"')
    if env and os.path.isfile(os.path.join(env, "SLDWORKS.exe")
                              if os.path.isdir(env) else env):
        return env
    v, _note = get("sw_exe", None)
    return v


def cad_exe():
    env = (os.environ.get(ENV_CAD) or "").strip().strip('"')
    if env:
        for c in (env, os.path.join(env, "acad.exe")):
            try:
                if os.path.isfile(c):
                    return c
            except Exception:
                pass
    v, _note = get("cad_exe", None)
    return v


def python_exe():
    env = (os.environ.get(ENV_PY) or "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    v, _note = get("python_exe", None)
    return v


def report():
    """诊断用：把每条设置在本机的生效状态列出来（含被忽略的原因）。"""
    state = load()
    mine = machine_fingerprint()
    rows = []
    for key in MACHINE_KEYS:
        e = _entry(state, key)
        if not e:
            rows.append({"key": key, "value": None, "usable": None,
                         "source": "未设置（走自动探测/默认）", "note": ""})
            continue
        value = str(e.get("value") or "")
        owner = e.get("machine")
        if owner and owner != mine:
            rows.append({"key": key, "value": value, "usable": False,
                         "source": "另一台电脑",
                         "note": "指纹不符，已忽略（本机指纹 %s）" % mine})
            continue
        ok, why = _usable(value, key)
        rows.append({"key": key, "value": value, "usable": bool(ok),
                     "source": "本机设置", "note": why})
    root, root_src = delivery_root()
    return {
        "ok": True,
        "file": settings_path(),
        "machine": machine_info(),
        "delivery_root": root,
        "delivery_root_source": root_src,
        "paths": rows,
        "env": {ENV_DELIVERY: os.environ.get(ENV_DELIVERY),
                ENV_SW: os.environ.get(ENV_SW),
                ENV_CAD: os.environ.get(ENV_CAD),
                ENV_PY: os.environ.get(ENV_PY)},
        "note": ("机器级设置（交付目录 / 各软件路径）只在本机生效；"
                 "换电脑会自动忽略并重新探测，不会沿用别人的盘符。"),
    }


def _main(argv):
    cmd = argv[0] if argv else "report"
    if cmd == "report":
        print(json.dumps(report(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "set" and len(argv) >= 3:
        r = set_path(argv[1], argv[2])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if cmd in ("delivery-root", "delivery_root"):
        root, src = delivery_root()
        print(json.dumps({"ok": True, "delivery_root": root, "source": src},
                         ensure_ascii=False))
        return 0
    print(json.dumps({"ok": False, "error": "用法: settings.py report | set <key> <path> | delivery-root"},
                     ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
