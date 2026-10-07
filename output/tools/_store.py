# -*- coding: utf-8 -*-
"""_store.py — 工程模式【状态目录单一事实源】（P0-3 写侧修复）

═══════════════════════════════════════════════════════════════════════════
【要解决的问题】状态双副本分裂
───────────────────────────────────────────────────────────────────────────
工程模式在磁盘上存在【两份副本】：
    · 源码/工作区副本：<repo>\\engineering\\tools\\
    · 安装副本：      %DSH_HOME%\\.agent-presets\\engineering\\tools\\
两者都含完整脚本。而原实现里每个模块都用
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
把状态写到【自己所在的那份】——
    · 从安装副本跑 mode_gate  → 写安装副本的 mode_state.json
    · 从工作区跑 workflow_gate → 写工作区的 workflow_state.json
于是同一次任务的状态被劈成两半：step、房间表、reports/ 凭据、防线 runtime
全部各写各的。实测表现：
    · workflow_state.json：工作区 step=user_selected vs 安装 step=depth_asked
    · mode_state.json    ：工作区 1 个房间 vs 安装 0 个房间
    · reports/           ：工作区有 physics/domain 凭据，安装副本一个都没有
    · 宿主守卫读安装副本 → 把"已完成"判成"未完成"，反复 steer 拦住对话结束

【为什么不能只改读取侧】
只在读取侧做"取 mtime 最新"只是治标：写入仍在分叉，两个文件会持续互相
追赶，谁最后被写谁"赢"，判定随调用顺序漂移。必须在【写入侧】收敛到唯一
目录，读取侧才有意义。

【本模块的解法】所有状态/凭据/报告的落点统一由本模块决定：
    ① 环境变量 DSH_STATE_DIR（最高优先，便于测试与多实例隔离）
    ② 环境变量 DSH_ENGINEERING_ROOT 推导（+ /tools）
    ③ _root.py 探测到的工程模式根（+ /tools）
    ④ 本文件所在目录（最终兜底，永不返回 None）

【重要兼容】状态目录与【脚本所在目录】解耦后，脚本仍可能从任一副本被调用；
因此本模块只负责"状态放哪"，不改变脚本自身的可执行性。
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys

ENV_STATE_DIR = "DSH_STATE_DIR"
ENV_ROOT = "DSH_ENGINEERING_ROOT"

_cached = None


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def _from_env_state_dir():
    v = (os.environ.get(ENV_STATE_DIR) or "").strip()
    if v:
        return os.path.abspath(v)
    return None


def _from_env_root():
    v = (os.environ.get(ENV_ROOT) or "").strip()
    if not v:
        return None
    try:
        # 允许 DSH_ENGINEERING_ROOT 指到 engineering 或 engineering\tools
        cand = os.path.abspath(v)
        if os.path.basename(cand).lower() == "tools":
            return cand
        return os.path.join(cand, "tools")
    except Exception:
        return None


def _from_root_module():
    """用 _root.py 的权威探测结果（不引入循环依赖：_root 不 import 本模块）。"""
    try:
        import _root
        td = _root.tools_dir(required=False)
        if td and os.path.isdir(td):
            return os.path.abspath(td)
    except Exception:
        pass
    return None


def _installed_tools_dir():
    """宿主所在的那份 tools 目录（安装副本）。

    ── 为什么它优先级很高 ────────────────────────────────────────────────
    宿主（DSH 进程）是防线的【信任根】与守卫所在处：
      · 它按【自己加载插件的那份 tools 目录】读 reports/*.physics.json 等凭据；
      · turn-stopping 守卫也按那份读 workflow_state.json / mode_state.json。
    因此【安装副本】才是状态与凭据的天然权威落点；工作区副本按 cmd_doctor
    的既定说明"仅作分发，不应承载运行时状态"。
    若 Python 侧把凭据写到工作区，宿主永远读不到 → 判定"缺少凭据"（P0-3）。
    """
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    dsh_home = (os.environ.get("DSH_HOME") or "").strip()
    cands = []
    if dsh_home:
        cands.append(os.path.join(dsh_home, ".agent-presets", "engineering", "tools"))
        cands.append(os.path.join(dsh_home, "engineering", "tools"))
    if home:
        cands.append(os.path.join(home, ".dsh", ".agent-presets", "engineering", "tools"))
        cands.append(os.path.join(home, ".dsh", "engineering", "tools"))
    for c in cands:
        try:
            if os.path.isdir(c):
                return os.path.abspath(c)
        except Exception:
            continue
    return None


def _score(path):
    """候选目录的"可信度"打分：状态文件越新越多，越可能是当前真相。"""
    if not path or not os.path.isdir(path):
        return (-1, 0.0)
    n = 0
    newest = 0.0
    for fn in ("workflow_state.json", "mode_state.json",
               "TASK_FINISHED.json", "artifacts_registry.json",
               "platform_status.json", "sw_state.json"):
        try:
            st = os.stat(os.path.join(path, fn))
            n += 1
            if st.st_mtime > newest:
                newest = st.st_mtime
        except Exception:
            continue
    return (n, newest)


def state_dir(required: bool = False) -> str:
    """返回【唯一】的状态目录（缓存）。

    优先级（高 → 低，严格按序取第一个存在的）：
      ① DSH_STATE_DIR（显式指定，测试/多实例隔离用）
      ② 安装副本 tools（宿主/守卫/信任根所在，天然权威）
      ③ DSH_ENGINEERING_ROOT
      ④ _root.py 的工程根探测
      ⑤ 本文件所在目录（兜底，必然存在，故永不返回 None）

    ── 【P0-3 修复·禁止跨目录回退】──────────────────────────────────────
    这里【严格按优先级取第一个存在的目录】，不做"按 mtime 挑最新"、
    也不因某目录暂无状态文件就跳去下一个。原因与 Node 侧同：
    跨目录回退等于让状态在部署之间互相污染 —— 宿主守卫会因此读到
    另一个部署的"任务已完成"，该拦不拦（实测已复现）。
    契约：认哪个目录就只认那个目录。
    """
    global _cached
    if _cached and os.path.isdir(_cached):
        return _cached

    for fn in (_from_env_state_dir, _installed_tools_dir,
               _from_env_root, _from_root_module, _here):
        try:
            c = fn()
        except Exception:
            c = None
        if c and os.path.isdir(c):
            _cached = os.path.abspath(c)
            return _cached
    _cached = _here()
    return _cached


def state_path(name: str) -> str:
    """状态目录下的某个文件绝对路径。"""
    return os.path.join(state_dir(), name)


def subdir(name: str) -> str:
    """状态目录下的子目录路径（不自动创建）。"""
    return os.path.join(state_dir(), name)


def reset_cache():
    """清除缓存（测试用；环境变量变化后调用）。"""
    global _cached
    _cached = None


def _shadow_dirs():
    """除权威目录外的其他 tools 副本（可能承载着"还没搬过来"的新状态）。"""
    out = []
    try:
        import _root
        for c in (_root.tools_dir(required=False), _here()):
            if not c:
                continue
            ap = os.path.abspath(c)
            if os.path.isdir(ap) and os.path.normcase(ap) != os.path.normcase(os.path.abspath(state_dir())):
                if ap not in out:
                    out.append(ap)
    except Exception:
        pass
    # 环境变量声明的工作区
    try:
        v = (os.environ.get(ENV_ROOT) or "").strip()
        if v:
            ap = os.path.abspath(v if os.path.basename(v).lower() == "tools"
                                 else os.path.join(v, "tools"))
            if os.path.isdir(ap) and ap not in out and \
               os.path.normcase(ap) != os.path.normcase(os.path.abspath(state_dir())):
                out.append(ap)
    except Exception:
        pass
    return out


# 需要跟随状态一起搬迁的运行时文件（凭据/报告/台账）
MIGRATE_FILES = (
    "workflow_state.json", "mode_state.json", "TASK_FINISHED.json",
    "artifacts_registry.json", "platform_status.json", "sw_state.json",
    "design_params.json", "defense_bypass.json",
)
MIGRATE_SUBDIRS = ("reports", "heartbeats", "load_cases")


def migrate_state(dry_run=False):
    """把"影子副本"里【更新】的状态搬到权威目录（P0-3 数据保全）。

    ── 为什么需要 ────────────────────────────────────────────────────────
    收敛写入点之后，此前写在另一份副本里的状态不会自动跟过来；若直接忽略，
    用户会看到"任务进度凭空回退"（实测：工作区 step=user_selected，
    安装副本 step=depth_asked —— 回退了整整两个阶段）。
    本函数在【权威目录那份更旧】时，把影子副本的文件复制过来。

    安全约束：
      · 只在新 > 旧 时覆盖（按 mtime 比较），绝不回退数据；
      · 覆盖前先把权威目录的旧文件备份为 <name>.bak-migrate-<时间戳>；
      · 子目录逐文件比较，同样只搬更新的；
      · dry_run=True 只报告不落盘。
    """
    import shutil
    import time as _t

    dst_dir = state_dir()
    stamp = _t.strftime("%Y%m%d-%H%M%S")
    moved, skipped, backed_up = [], [], []

    def _newer(src, dst):
        try:
            if not os.path.exists(dst):
                return True
            return os.path.getmtime(src) > os.path.getmtime(dst) + 1.0
        except Exception:
            return False

    def _do_file(src, dst):
        if not _newer(src, dst):
            skipped.append(os.path.basename(dst))
            return
        if dry_run:
            moved.append(os.path.basename(dst))
            return
        try:
            if os.path.exists(dst):
                bak = dst + ".bak-migrate-" + stamp
                shutil.copy2(dst, bak)
                backed_up.append(os.path.basename(bak))
            os.makedirs(os.path.dirname(dst) or dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
            moved.append(os.path.basename(dst))
        except Exception as e:
            skipped.append("%s(失败:%r)" % (os.path.basename(dst), e))

    for sh in _shadow_dirs():
        for fn in MIGRATE_FILES:
            sp = os.path.join(sh, fn)
            if os.path.isfile(sp):
                _do_file(sp, os.path.join(dst_dir, fn))
        for sub in MIGRATE_SUBDIRS:
            sd = os.path.join(sh, sub)
            if not os.path.isdir(sd):
                continue
            for root, _dirs, files in os.walk(sd):
                for fn in files:
                    sp = os.path.join(root, fn)
                    rel = os.path.relpath(sp, sh)
                    _do_file(sp, os.path.join(dst_dir, rel))

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "state_dir": dst_dir,
        "shadow_dirs": _shadow_dirs(),
        "migrated": sorted(set(moved)),
        "skipped_newer_or_equal": sorted(set(skipped)),
        "backups": backed_up,
        "note": ("只搬【更新】的文件；权威目录的旧版本已就地备份为 "
                 "*.bak-migrate-<时间戳>（可用 --dry-run 先预览）"),
    }


def report() -> dict:
    """诊断用：当前选择与全部候选的评分。"""
    explicit = _from_env_state_dir()
    cands = []
    for fn, label in ((_from_env_state_dir, "env:DSH_STATE_DIR"),
                      (_installed_tools_dir, "installed(~/.dsh/.agent-presets)"),
                      (_from_env_root, "env:DSH_ENGINEERING_ROOT"),
                      (_from_root_module, "_root.tools_dir()"),
                      (_here, "module dir")):
        try:
            c = fn()
        except Exception:
            c = None
        cands.append({"from": label, "path": c, "score": _score(c)})
    return {
        "state_dir": state_dir(),
        "explicit": explicit,
        "exists": os.path.isdir(state_dir()),
        "candidates": cands,
        "note": ("所有状态/凭据/报告统一写入 state_dir，消除双副本分裂（P0-3）"),
    }


def _ensure_importable():
    """把状态目录加入 sys.path，供 import mode_gate / defense_gate 等使用。"""
    d = state_dir()
    if d and d not in sys.path:
        sys.path.insert(0, d)
    return d


if __name__ == "__main__":
    import json
    import sys as _sys

    args = [a for a in _sys.argv[1:]]
    if "--migrate" in args or "--dry-run" in args:
        _out = migrate_state(dry_run=("--dry-run" in args))
    else:
        _out = report()
    # Windows 控制台默认 GBK，中文会乱码 —— 强制 UTF-8 输出
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(_out, ensure_ascii=False, indent=2))
