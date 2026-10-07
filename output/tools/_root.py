# -*- coding: utf-8 -*-
r"""_root.py — 工程模式根目录自动探测（Bug-01 修复）

═══════════════════════════════════════════════════════════════════════════
【Bug-01 根因】文档写 `python "<工程模式根目录>\tools\workflow_gate.py"`，
但"工程模式根目录"在文档里从未被定义，实测用户按字面理解会指向
`C:\Users\<user>\.dsh\skills\mode-selection\`（那里只有 SKILL.md），
于是第 1 步就 `[Errno 2] No such file or directory`。

【本模块的解法】提供**唯一权威**的根目录解析函数，所有脚本/文档都引用它：
  解析优先级（从高到低）：
    ① 环境变量 `DSH_ENGINEERING_ROOT`（用户/编排显式指定，最高优先）
    ② 从调用方脚本自身位置向上回溯，找到同时含
       `tools/mode_gate.py` 与 `tools/workflow_gate.py` 的目录
    ③ 已知的候选路径（工程仓库根、`.dsh/engineering` 等）
    ④ 当前工作目录向上回溯

  另外提供 `find_gate()`：在根目录下定位任意门禁脚本（含 `tools/` 与
  `skills/*/tools/` 两级），解决"脚本被挪到别处"的场景。

【用法】
    from _root import engineering_root, gate_path
    root = engineering_root()                  # -> ...\engineering
    wg   = gate_path("workflow_gate.py")       # -> ...\engineering\tools\workflow_gate.py

命令行自检（Bug-01 建议的 find_gate.py 等价物）：
    python _root.py            # 打印解析结果与全部候选
    python _root.py --json     # 机器可读
"""
from __future__ import annotations

import json
import os
import sys

# 根目录必须同时包含这些"指纹"文件，才算真的工程模式根目录
_ROOT_FINGERPRINTS = (
    os.path.join("tools", "mode_gate.py"),
    os.path.join("tools", "workflow_gate.py"),
)
# 门禁脚本可能出现的相对位置（相对工程根目录）
_GATE_SUBDIRS = (
    "tools",
    os.path.join("skills", "mode-selection", "tools"),
    os.path.join("skills", "assembly-orchestration", "tools"),
)

ENV_VAR = "DSH_ENGINEERING_ROOT"

# 门禁脚本清单（供 find_gate 校验）
GATE_SCRIPTS = (
    "mode_gate.py",
    "workflow_gate.py",
    "sw_bridge.py",
    "swapi.py",
    "ask_user.py",
    "physics_bridge.py",
    "ac_validate.py",
    "ac_bridge.py",
)


def _is_engineering_root(path: str) -> bool:
    """目录是否为工程模式根目录（含全部指纹文件）。"""
    if not path:
        return False
    try:
        if not os.path.isdir(path):
            return False
        return all(os.path.isfile(os.path.join(path, fp)) for fp in _ROOT_FINGERPRINTS)
    except Exception:
        return False


def _walk_up(start: str):
    """从 start 向上逐级产出目录（含 start 本身）。"""
    try:
        cur = os.path.abspath(start)
    except Exception:
        return
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        yield cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


def _candidate_roots():
    """产出全部候选根目录（按优先级）。"""
    out = []

    # ① 环境变量
    try:
        env = (os.environ.get(ENV_VAR) or "").strip().strip('"').strip("'")
        if env:
            out.append(env)
            # 环境变量可能指向仓库根（含 engineering/ 子目录）
            out.append(os.path.join(env, "engineering"))
    except Exception:
        pass

    # ② 调用方脚本自身位置向上回溯
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        for d in _walk_up(here):
            out.append(d)
    except Exception:
        pass

    # ③ 当前工作目录向上回溯
    try:
        for d in _walk_up(os.getcwd()):
            out.append(d)
    except Exception:
        pass

    # ④ 常见约定位置
    try:
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        if home:
            out.append(os.path.join(home, ".dsh", "engineering"))
            out.append(os.path.join(home, ".dsh", "skills", "mode-selection"))
            out.append(os.path.join(home, ".dsh"))
        out.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    except Exception:
        pass

    # 去重保序
    seen = set()
    uniq = []
    for p in out:
        if not p:
            continue
        try:
            key = os.path.normcase(os.path.abspath(p))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def engineering_root(required: bool = False) -> str | None:
    """解析工程模式根目录。

    Args:
        required: 为 True 时解析失败直接抛 RuntimeError（附带全部候选），
                  便于脚本尽早失败并给出可操作的错误信息。
    Returns:
        根目录绝对路径；解析失败返回 None（required=False 时）。
    """
    cands = _candidate_roots()
    for c in cands:
        if _is_engineering_root(c):
            try:
                return os.path.abspath(c)
            except Exception:
                return c

    # 退化路径：只要能找到 mode_gate.py / workflow_gate.py 所在的 tools/ 目录，
    # 就把它当作"工具目录"，其父目录即根目录。这覆盖"指纹文件不全但脚本都在"
    # 的场景（例如用户只拷了 tools/ 目录）。
    for c in cands:
        try:
            if os.path.isfile(os.path.join(c, "mode_gate.py")) and \
               os.path.isfile(os.path.join(c, "workflow_gate.py")):
                return os.path.abspath(os.path.dirname(c))
        except Exception:
            continue

    if required:
        raise RuntimeError(
            "无法定位工程模式根目录。请设置环境变量 %s 指向包含 tools/ 的目录。\n"
            "已尝试的候选路径:\n  - %s" % (ENV_VAR, "\n  - ".join(cands))
        )
    return None


def tools_dir(required: bool = False) -> str | None:
    """工程模式的 tools/ 目录（门禁脚本所在处）。"""
    root = engineering_root(required=False)
    if root:
        cand = os.path.join(root, "tools")
        if os.path.isdir(cand):
            return cand
    # 退化：本文件所在目录通常就是 tools/
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "mode_gate.py")):
        return here
    if required:
        raise RuntimeError("无法定位工程模式 tools/ 目录，请设置 %s" % ENV_VAR)
    return None


def gate_path(script: str, required: bool = False) -> str | None:
    """定位某个门禁脚本的绝对路径。

    搜索顺序：环境变量根 → 工程根 tools/ → 工程根 skills/*/tools/ →
    本文件所在目录 → 工作目录向上。
    """
    name = os.path.basename(str(script or "").strip())
    if not name:
        return None

    cands = []
    root = engineering_root(required=False)
    if root:
        for sub in _GATE_SUBDIRS:
            cands.append(os.path.join(root, sub, name))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, name))
    for d in _walk_up(os.getcwd()):
        cands.append(os.path.join(d, name))
        cands.append(os.path.join(d, "tools", name))

    for c in cands:
        try:
            if os.path.isfile(c):
                return os.path.abspath(c)
        except Exception:
            continue
    if required:
        raise RuntimeError("找不到门禁脚本 %s（工程模式根目录=%s）" % (name, root))
    return None


def root_report() -> dict:
    """自检报告：解析结果 + 全部候选 + 各候选是否命中。"""
    root = engineering_root(required=False)
    tools = tools_dir(required=False)
    cands = []
    for c in _candidate_roots():
        cands.append({"path": c, "is_root": _is_engineering_root(c)})
    gates = {}
    for s in GATE_SCRIPTS:
        gates[s] = gate_path(s)
    return {
        "ok": bool(root),
        "env_var": ENV_VAR,
        "env_value": os.environ.get(ENV_VAR),
        "engineering_root": root,
        "tools_dir": tools,
        "gate_scripts": gates,
        "missing_gate_scripts": [k for k, v in gates.items() if not v],
        "candidates": cands,
        "hint": (None if root else
                 "未找到工程模式根目录。请设置 %s 指向含 tools/ 的目录，例如：\n"
                 "  setx %s \"<仓库>\\engineering\"  （或在当前会话 set %s=...）"
                 % (ENV_VAR, ENV_VAR, ENV_VAR)),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    rep = root_report()
    if "--json" in argv:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("工程模式根目录 : %s" % (rep["engineering_root"] or "（未找到）"))
        print("tools/ 目录    : %s" % (rep["tools_dir"] or "（未找到）"))
        print("环境变量       : %s=%s" % (rep["env_var"], rep["env_value"] or "（未设置）"))
        print("门禁脚本:")
        for k, v in rep["gate_scripts"].items():
            print("  %-20s %s" % (k, v or "（缺失）"))
        if rep["hint"]:
            print("")
            print(rep["hint"])
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
