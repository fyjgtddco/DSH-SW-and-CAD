# -*- coding: utf-8 -*-
r"""_plugin_paths.py —— 定位 dsh-engineering-ui 插件源码（工作区 / 安装副本都适用）

═══════════════════════════════════════════════════════════════════════════
【为什么需要这个模块】

回归测试里多个文件都硬编码了
    os.path.join(HERE, "..", "plugins", "dsh-engineering-ui", "lib", "xxx.js")

这个路径只在【工作区副本】成立：
    <repo>/engineering/tools/  →  ../plugins/dsh-engineering-ui/lib/*.js   ✅

而在【安装副本】上根本不存在：
    %USERPROFILE%\.dsh\.agent-presetsengineering	ools\
      ../plugins/...                                         ❌ 没有这个目录

因为安装时插件被放到了 profile 的 node_modules：
    %USERPROFILE%\.dsh\profiles\<profile>\node_modules\dsh-engineering-ui\

后果：测试在安装副本上一跑就 FileNotFoundError，
      表现为"回归失败"，实际是测试自己的路径假设错了 ——
      这会掩盖真正的回归问题（谁也不想在 30 个 ❌ 里找那 1 个真的）。
═══════════════════════════════════════════════════════════════════════════
"""
import os


def _candidates():
    """插件 lib 目录的候选位置（按优先级）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    eng_root = os.path.dirname(here)          # .../engineering
    out = [
        # ① 工作区布局：<repo>/engineering/plugins/dsh-engineering-ui/lib
        os.path.join(eng_root, "plugins", "dsh-engineering-ui", "lib"),
        # ② 上层工作区（有些布局把 plugins 放在仓库根）
        os.path.join(os.path.dirname(eng_root), "plugins",
                     "dsh-engineering-ui", "lib"),
    ]
    # ③ 安装副本：<DSH_HOME>/profiles/<profile>/node_modules/dsh-engineering-ui/lib
    dsh_home = ""
    for k in ("DSH_HOME",):
        v = (os.environ.get(k) or "").strip()
        if v:
            dsh_home = v
            break
    if not dsh_home:
        home = (os.environ.get("USERPROFILE") or os.environ.get("HOME") or "").strip()
        if home and not home.startswith("~"):
            dsh_home = os.path.join(home, ".dsh")
    if dsh_home:
        prof_dir = os.path.join(dsh_home, "profiles")
        try:
            for p in sorted(os.listdir(prof_dir)):
                out.append(os.path.join(prof_dir, p, "node_modules",
                                        "dsh-engineering-ui", "lib"))
        except Exception:
            pass
        out.append(os.path.join(dsh_home, "node_modules",
                                "dsh-engineering-ui", "lib"))
    return out


def plugin_lib_dir():
    """返回存在的插件 lib 目录；都找不到返回 None。"""
    for d in _candidates():
        try:
            if os.path.isdir(d):
                return d
        except Exception:
            continue
    return None


def plugin_file(name):
    """返回插件某个文件（如 'client.js' / 'index.js'）的绝对路径；找不到返回 None。"""
    d = plugin_lib_dir()
    if not d:
        return None
    p = os.path.join(d, name)
    return p if os.path.isfile(p) else None


def read_plugin_file(name):
    """读取插件文件内容；找不到返回 None（调用方应据此 skip 而非 fail）。"""
    p = plugin_file(name)
    if not p:
        return None
    try:
        import io
        with io.open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None
