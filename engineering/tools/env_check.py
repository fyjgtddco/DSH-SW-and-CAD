# -*- coding: utf-8 -*-
r"""env_check.py —— 工程模式【本机环境自检】（设置页「工程环境检查」的后端）

═══════════════════════════════════════════════════════════════════════════
【为什么需要它】
用户在学校微机房实测到的现象，本质是同一件事：
  工程模式默认"这台电脑什么都有"，一旦缺 Python 3.6+、缺 pywin32、
  缺 numpy，或者 SW/CAD 装在了别的盘 —— 表现就变成"某些设置静默失效"，
  而界面不会告诉用户少了什么。

用户的要求（原话）：
  「在设置 → 工程模式中，连接区上面再加一个『检查电脑是否有对应的环境』，
   然后写一段提示词自己发给新的对话，新的 Agent 对话就自己去查看看少啥，
   自己安装。」

【本模块提供什么】
  1) probe()          —— 一次性把依赖查清楚，返回结构化结论
                         （ok / warn / missing，每一项都带"怎么修"）
  2) install_prompt() —— 生成一段**可直接发给新对话**的提示词：
                         把缺失项、期望版本、可用命令、验收标准全部写进去，
                         新 Agent 照着做就行，不需要用户自己翻译报错。
  3) 交付目录 / 软件路径等"本机设置"的读写入口（转发到 settings.py）。

【为什么不用 import 探测就下结论】
  微机房最容易骗人的一件事：import win32com 在宿主进程里可能成功
  （宿主用的是随包 Python），但真正跑 sw_bridge.py 的那个解释器根本没有。
  所以这里对 Python 的检查一律【用目标解释器起子进程跑一段探针】，
  而不是在当前进程里 try/import。
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 依赖清单（单一事实源：UI 文案、提示词、自检都读它）────────────────────
DEPS = [
    {"id": "python", "name": "Python 解释器（≥ 3.6）",
     "why": "sw_bridge.py / swapi.py 使用 f-string 等 3.6+ 语法；"
            "旧版 Python 会直接 SyntaxError，界面表现为「插件解析失败 / 没有输出」。"},
    {"id": "pywin32", "name": "pywin32（SolidWorks / AutoCAD 的 COM 自动化）",
     "why": "没有它就完全无法驱动 SolidWorks 与 AutoCAD。",
     "kind": "module", "module": "win32com.client", "pip": "pywin32"},
    {"id": "numpy", "name": "numpy（有限元求解 Level 1）",
     "why": "物理在环的纯 Python FEA（CST/CTE 单元）依赖它；"
            "缺了只能退回 Level 0 解析解，薄壁件判定会变粗。",
     "kind": "module", "module": "numpy", "pip": "numpy"},
    {"id": "ezdxf", "name": "ezdxf（DXF 解析 / CADX 几何验证）",
     "why": "cad-validate 的 7 项检查需要读 DXF；缺了 CADX 验证直接不可用。",
     "kind": "module", "module": "ezdxf", "pip": "ezdxf"},
    {"id": "shapely", "name": "shapely（几何布尔运算）",
     "why": "DXF 重叠/相交检查依赖它；缺了该项检查会被跳过。",
     "kind": "module", "module": "shapely", "pip": "shapely"},
    {"id": "PIL", "name": "Pillow + mss（截图 / 看图矫正）",
     "why": "「建模后必须看图」这条门禁靠截图证据；缺了截图链路会降级。",
     "kind": "module", "module": "PIL", "pip": "Pillow mss"},
]

PROBE_CODE = r'''
import json, sys
out = {"ok": True, "version": "%d.%d.%d" % sys.version_info[:3],
       "executable": sys.executable, "prefix": sys.prefix,
       "bits": 64 if sys.maxsize > 2**32 else 32,
       "modules": {}, "error": None}
for mod, shown in (("win32com.client", "pywin32"), ("numpy", "numpy"),
                   ("ezdxf", "ezdxf"), ("shapely", "shapely"),
                   ("PIL", "PIL"), ("mss", "mss")):
    try:
        __import__(mod)
        out["modules"][shown] = {"ok": True, "version": None}
    except Exception as e:
        out["modules"][shown] = {"ok": False, "error": repr(e)[:200]}
if sys.version_info < (3, 6):
    out["ok"] = False
    out["error"] = "Python 版本过低（需要 3.6+，当前 %d.%d）" % sys.version_info[:2]
print(json.dumps(out))
'''


# ══════════════════════════════════════════════════════════════════════════
# 解释器发现
# ══════════════════════════════════════════════════════════════════════════

def _candidates():
    """候选解释器（去重保序）。

    顺序刻意把 py -3 放在前面：Windows 上 py.exe 是官方启动器，
    它会挑最新注册的 3.x，而不是 PATH 里那个 3.5.x（微机房踩的正是这个坑）。
    """
    out = []
    try:
        import settings as _settings
        _p = _settings.python_exe()
        if _p:
            out.append(_p)
    except Exception:
        pass
    v = (os.environ.get("DSH_PYTHON") or "").strip().strip('"')
    if v:
        out.append(v)
    out.extend([sys.executable, "py", "python", "python3"])
    seen, uniq = set(), []
    for c in out:
        if not c:
            continue
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def _run_probe(exe):
    """用某个解释器跑探针脚本，返回 (ok, data, raw_error)。"""
    args = [exe]
    if exe == "py":
        args.append("-3")
    try:
        r = subprocess.run(args + ["-c", PROBE_CODE],
                           capture_output=True, text=True, timeout=25)
    except Exception as e:
        return False, None, "无法执行 %r: %r" % (exe, e)
    stdout = (r.stdout or "").strip()
    stderr = (r.stderr or "").strip()
    if not stdout:
        # 典型旧版 Python：语法错误直接死在 stderr，stdout 空 ——
        # 这正是微机房"插件解析失败"的现场。
        return False, None, (stderr or
                             "无输出（可能是 Python 版本过低，语法错误只打到 stderr）")[-600:]
    try:
        data = json.loads(stdout.splitlines()[-1])
    except Exception:
        return False, None, "探针输出不是 JSON: %s" % stdout[-300:]
    return bool(data.get("ok")), data, (stderr[-600:] or None)


def probe_python():
    """挑一个能满足最低要求的解释器，并返回它的模块状况。"""
    tried = []
    for exe in _candidates():
        ok, data, err = _run_probe(exe)
        tried.append({"exe": exe, "version": (data or {}).get("version"),
                      "ok": bool(ok), "error": err})
        if ok and data:
            return {"ok": True, "chosen": exe,
                    "version": data.get("version"),
                    "bits": data.get("bits"),
                    "executable": data.get("executable"),
                    "modules": data.get("modules") or {},
                    "tried": tried, "note": None}
    return {"ok": False, "chosen": None, "version": None, "modules": {},
            "tried": tried,
            "error": "没有找到可用的 Python 3.6+ 解释器",
            "note": ("候选全部失败。最常见原因：PATH 里排在最前的是 Python 3.5 一类的旧版本"
                     "（3.5 不支持 f-string），而 sw_bridge.py 需要 3.6+。"
                     "解决：在设置页「工程环境检查」里指定 python.exe，"
                     "或安装 Python 3.11/3.12 并勾选 Add to PATH。")}


# ══════════════════════════════════════════════════════════════════════════
# 软件路径 / 设置
# ══════════════════════════════════════════════════════════════════════════

def probe_sw_path():
    mine = None
    try:
        import settings as _settings
        mine = _settings.sw_exe()
    except Exception:
        pass
    try:
        import swapi
        found = swapi.find_sldworks_exe()
    except Exception as e:
        return {"ok": bool(mine), "path": mine,
                "error": "探测 swapi 失败: %r" % (e,)}
    return {"ok": bool(found), "path": found or mine,
            "from_settings": bool(mine),
            "error": None if found else
                     "未找到 SLDWORKS.exe（注册表 / 常见安装目录 / 环境变量都没命中）"}


def probe_cad_path():
    try:
        import conn_state
        p = conn_state._find_acad_exe()
    except Exception as e:
        return {"ok": False, "path": None, "error": "探测失败: %r" % (e,)}
    return {"ok": bool(p), "path": p,
            "error": None if p else "未找到 acad.exe（可选组件：缺了仍可出 SLDDRW）"}


def probe_settings():
    try:
        import settings as _settings
        root, src = _settings.delivery_root()
        rep = _settings.report()
        writable = False
        try:
            os.makedirs(root, exist_ok=True)
            probe_file = os.path.join(root, ".dsh_write_probe")
            with open(probe_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe_file)
            writable = True
        except Exception:
            writable = False
        return {"ok": bool(writable), "delivery_root": root,
                "delivery_root_source": src, "writable": writable,
                "file": rep.get("file"),
                "ignored": [r for r in rep.get("paths", [])
                            if r.get("usable") is False],
                "error": None if writable else ("交付目录不可写：%s" % root)}
    except Exception as e:
        return {"ok": False, "error": "读取设置失败: %r" % (e,)}


def probe_engine():
    """工程模式自身文件是否完整（换机 / 半拷贝时很容易缺）。"""
    need = ["sw_bridge.py", "swapi.py", "mode_gate.py", "workflow_gate.py",
            "defense_gate.py", "physics_bridge.py", "conn_state.py", "settings.py"]
    missing = [n for n in need if not os.path.isfile(os.path.join(HERE, n))]
    return {"ok": not missing, "tools_dir": HERE, "missing": missing,
            "error": ("缺少核心脚本：%s" % ", ".join(missing)) if missing else None}


# ══════════════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════════════

def probe(full=True):
    """完整自检。返回 {ok, status, items[], summary, counts}。

    status: 'ok'（全绿）/ 'warn'（只缺可选项）/ 'bad'（缺必需项）
    """
    items = []

    def add(item_id, name, state, detail, fix=None, optional=False):
        items.append({"id": item_id, "name": name, "state": state,
                      "optional": bool(optional),
                      "detail": detail or "", "fix": fix or ""})

    eng = probe_engine()
    add("engine", "工程模式核心脚本", "ok" if eng["ok"] else "missing",
        eng["tools_dir"] if eng["ok"] else eng.get("error"),
        "重新运行 install-0.2.0.ps1 或 sync-to-dsh.ps1，把 tools/ 同步到安装副本")

    py = probe_python()
    if py["ok"]:
        add("python", "Python 解释器", "ok",
            "%s（%s，%s 位）" % (py["version"], py["chosen"], py["bits"]))
    else:
        add("python", "Python 解释器", "missing", py.get("error") or "未找到",
            py.get("note") or "安装 Python 3.11/3.12 并加入 PATH，"
                              "或在设置里指定 python.exe")

    mods = py.get("modules") or {}
    for dep in DEPS:
        if dep.get("kind") != "module":
            continue
        m = mods.get(dep["id"])
        if m is None:
            add(dep["id"], dep["name"], "unknown",
                "无法确认（Python 解释器不可用）", "先解决 Python 解释器问题")
        elif m.get("ok"):
            add(dep["id"], dep["name"], "ok", "已安装")
        else:
            add(dep["id"], dep["name"], "missing", (m.get("error") or "导入失败"),
                '-m pip install %s' % (dep.get("pip") or dep["id"]))

    sw = probe_sw_path()
    add("solidworks", "SolidWorks 安装路径",
        "ok" if sw["ok"] else "missing",
        sw.get("path") or sw.get("error"),
        "在连接区点「SOLIDWORKS连接」重新探测；"
        "或在「工程环境检查」里手动指定 SLDWORKS.exe")
    cad = probe_cad_path()
    add("autocad", "AutoCAD 安装路径",
        "ok" if cad["ok"] else "warn",
        cad.get("path") or cad.get("error"),
        "可选：在连接区点「AutoCAD 连接」探测；缺少时仍可输出 SLDDRW / PDF",
        optional=True)

    st = probe_settings()
    add("delivery_root", "交付目录",
        "ok" if st.get("ok") else "missing",
        "%s（来源：%s）" % (st.get("delivery_root"),
                           st.get("delivery_root_source")),
        "在「工程环境检查」里把交付目录改到一个可写的位置（例如 D:/dsh-out）")
    ignored = st.get("ignored") or []
    if ignored:
        add("stale_paths", "跨机器残留设置", "warn",
            "；".join("%s=%s（%s）" % (r["key"], r["value"], r.get("note") or "")
                     for r in ignored),
            "这些设置来自另一台电脑，已自动忽略；可点「清空本机路径设置」重来")

    state = "ok"
    if any(i["state"] == "missing" and not i["optional"] for i in items):
        state = "bad"
    elif any(i["state"] in ("missing", "warn", "unknown") for i in items):
        state = "warn"

    return {
        "ok": state != "bad",
        "status": state,
        "items": items,
        "missing": [i for i in items
                    if i["state"] in ("missing", "unknown") and not i["optional"]],
        "summary": ("环境完整，可以直接建模" if state == "ok" else
                    ("缺少可选项，部分功能会降级" if state == "warn" else
                     "缺少必需项，工程模式无法正常建模")),
        "counts": {"ok": len([i for i in items if i["state"] == "ok"]),
                   "warn": len([i for i in items
                                if i["state"] in ("warn", "unknown")]),
                   "missing": len([i for i in items if i["state"] == "missing"])},
        "python": {"chosen": py.get("chosen"), "version": py.get("version"),
                   "tried": py.get("tried")},
        "full": bool(full),
    }


# ══════════════════════════════════════════════════════════════════════════
# 提示词生成（用户要的「写一段提示词自己发给新的对话」）
# ══════════════════════════════════════════════════════════════════════════

_PROMPT_HEAD = """【工程模式 · 本机环境修复任务】

你正在一台可能缺少依赖的电脑上，为 DSH「工程模式」预设做环境修复。
请按下面清单逐项处理，不要跳过、不要假设"应该已经装了"。

## 一、当前自检结论（由 tools/env_check.py 实测生成）
状态：{status_cn}
{summary}

## 二、必须处理的问题

"""

_PROMPT_TAIL = """
## 三、执行规则（请严格遵守）

1. **先确认解释器，再装包。** 本机 PATH 里的 python 可能是 Python 3.5 一类旧版本
   （3.5 不支持 f-string，而 sw_bridge.py 需要 3.6+）。装包前先跑：
```powershell
   python -c "import sys;print(sys.version)"
   py -3   -c "import sys;print(sys.version)"
```
   若默认 python 版本过低，**所有 pip 安装都必须显式用高版本解释器**：
```powershell
   py -3 -m pip install --upgrade pip
   py -3 -m pip install {pip_pkgs}
```
2. **装完必须回填。** 若 py -3 可用而 python 不可用，请把这件事告诉我，
   我会把它固定成本机设置（settings.py set python_exe <路径>），
   这样插件和门禁脚本用的是同一个解释器，不会再出现"装了但没生效"。
3. **每装完一项就复验**，不要全装完再一次性验证：
```powershell
   "{python}" "{tools}\\env_check.py" probe
```
4. **SolidWorks / AutoCAD 路径**：若自检说找不到，先点设置页连接区的「连接」；
   仍不行就在「工程环境检查」里手动指定可执行文件。**不要全盘扫描 C: 盘根目录**。
5. **交付目录**：默认不再落在桌面（以前会凭空建一个 test 文件夹）。
   请在设置页「工程环境检查」里把交付目录设到一个明确、可写的位置。
6. 全部处理完后，最后再跑一次完整自检，并把结论贴给我：
```powershell
   "{python}" "{tools}\\env_check.py" probe
```

## 四、验收标准（做不到就是没修好）

- env_check.py probe 的 status 必须是 ok（或只剩 AutoCAD 这类可选项）；
- sw_bridge.py doctor 不再报 Python 语法错误 / 缺 pywin32；
- 设置页「工程环境检查」里所有必需项都是绿色。
"""


def _guess_python():
    try:
        py = probe_python()
        return py.get("chosen") or py.get("executable")
    except Exception:
        return None


def install_prompt(result=None, tools_dir=None):
    """生成可直接粘贴给新对话的修复提示词。"""
    r = result or probe()
    items = r.get("items") or []
    tools = tools_dir or HERE
    lines, pip_pkgs = [], []
    idx = 0
    for it in items:
        if it["state"] not in ("missing", "warn", "unknown"):
            continue
        idx += 1
        tag = "可选项" if it.get("optional") else "必需"
        lines.append("### %d. %s（%s）" % (idx, it["name"], tag))
        if it.get("detail"):
            lines.append("- 现状：%s" % it["detail"])
        if it.get("fix"):
            lines.append("- 处理：%s" % it["fix"])
        lines.append("")
        m = re.search(r"-m pip install\s+(.+)$", it.get("fix") or "")
        if m:
            pip_pkgs.append(m.group(1).strip())
    if not lines:
        lines = ["（无 —— 当前环境已完整，无需处理任何项。）", ""]
    status_cn = {"ok": "完整", "warn": "有可选项缺失",
                 "bad": "缺少必需项"}.get(r.get("status"), str(r.get("status")))
    head = _PROMPT_HEAD.format(status_cn=status_cn,
                               summary=r.get("summary") or "")
    tail = _PROMPT_TAIL.format(
        pip_pkgs=" ".join(dict.fromkeys(pip_pkgs)) if pip_pkgs else "<包名>",
        python=(_guess_python() or "python"),
        tools=tools)
    return head + "\n".join(lines) + tail


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _main(argv):
    cmd = (argv[0] if argv else "probe").lower()
    if cmd in ("probe", "check", "status"):
        res = probe(full=True)
        _print(res)
        return 0 if res.get("ok") else 1
    if cmd in ("prompt", "install-prompt"):
        res = probe(full=True)
        text = install_prompt(res)   # 代码围栏已在模块常量里就位，无需二次替换
        if "--json" in argv:
            _print({"ok": True, "prompt": text, "status": res.get("status")})
        else:
            sys.stdout.write(text + "\n")
        return 0
    if cmd in ("set-delivery-root", "set_delivery_root") and len(argv) >= 2:
        import settings as _settings
        _print(_settings.set_path("delivery_root", argv[1]))
        return 0
    if cmd == "set-path" and len(argv) >= 3:
        import settings as _settings
        _print(_settings.set_path(argv[1], argv[2]))
        return 0
    if cmd == "settings":
        import settings as _settings
        _print(_settings.report())
        return 0
    _print({"ok": False,
            "error": "用法: env_check.py probe | prompt [--json] | settings | "
                     "set-path <key> <path> | set-delivery-root <dir>"})
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
