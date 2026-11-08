# -*- coding: utf-8 -*-
r"""task_workspace.py — 【任务目录分层 + 收尾清理】单一实现

══ 用户需求（原文，2026-11-07）═══════════════════════════════════════════
「如图一所示，一堆奇奇怪怪的文件夹，包含一些奇奇怪怪的 py 文档、json 文档。
  假如你是机械专业的老哥，你根本就找不到你需要的文件在哪里。我的建议是：
  1. 写进代码里面，让主对话最后收尾的时候，直接删去那些奇奇怪怪的文件和
     失败的建模文档；
  2. 建模过程中这样子搞，两个文件夹：第一个放那种类似于 py 文档、json 文档
     等等和建模没有关系的（这些最后主对话可以删去）；第二个是放零件，然后
     假设建模失败的文件，直接不用保存即可。」

══ 目录契约（本模块是唯一权威）═════════════════════════════════════════
  <work_dir>/
    parts/        ★ 零件（.sldprt / .sldasm）—— 交付物，永不自动删除
    drawings/     ★ 工程图（.slddrw/.dwg/.dxf/.pdf）—— 交付物
    reports/       防线凭据 / 房间报告（交付时的证据，保留）
    parts_rejects/  ✗ 建模失败/废弃的零件（收尾时整个删掉）
    scripts/       建模脚本（.py 等，可归档或删除）
    intermediate/  ✗ 中间产物（json/txt/log/pyc/临时文件…，收尾删掉）

  ⇒ 机械工程师打开任务目录，【只看得到】parts/ drawings/ reports/ 三个文件夹。

══ 两条硬规则 ══════════════════════════════════════════════════════════
  ① 零件只有在"确实建出来了"时才进 parts/；失败的直接进 parts_rejects/。
     （sw_bridge 保存前后的校验见 reject_part() 的调用点）
  ② 收尾只清 intermediate/ 与 parts_rejects/，绝不碰 parts/drawings/reports。
     清之前写一份 cleanup_manifest.json 到 reports/，做到"删了什么有据可查"。
"""

from __future__ import annotations

import json
import os
import shutil
import time

#: 交付物目录（收尾永不清）
KEEP_DIRS = ("parts", "drawings", "reports")
#: 中间产物目录（收尾可清）
SCRATCH_DIRS = ("intermediate", "parts_rejects")
#: 建模脚本目录（默认归档而不是删，避免误删可复现的建模过程）
SCRIPT_DIR = "scripts"
#: 归档脚本的目录（收尾时把 scripts/ 搬到这里）
SCRIPT_ARCHIVE = os.path.join("reports", "scripts_archive")

#: 这些扩展名属于"可交付的建模产物"，落在 parts/ 下就绝不删
PART_EXTS = (".sldprt", ".sldasm", ".step", ".stp", ".iges", ".igs", ".x_t", ".par")
DRAWING_EXTS = (".slddrw", ".dwg", ".dxf", ".pdf")

#: 绝不能搬/删的运行时状态文件（门禁与计划就靠它们活着）。
#   ⚠️ 实测教训：mode2_plan.json 就在任务目录根上，tidy 若把它搬进
#      intermediate/，cleanup 一删 —— 十阶段台账直接消失，门禁全崩。
KEEP_FILES = frozenset((
    "mode2_plan.json", "workflow_state.json", "mode_state.json",
    "TASK_INFO.json", "TASK_FINISHED.json", "analyst_heartbeat.json",
    "part_ownership.json", "platform_agents.json", "artifacts_registry.json",
    "load_case.json", "gate_load_case.json",
))

#: 中间产物的典型扩展名（会被搬进 intermediate/）
SCRATCH_EXTS = (
    ".json", ".txt", ".log", ".pyc", ".pyo", ".tmp", ".bak", ".old",
    ".md", ".csv", ".ini", ".cfg", ".yml", ".yaml", ".xml", ".sqlite3",
    ".pid", ".lock", ".py", ".ps1", ".bat", ".sh", ".lsp",
)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_layout(work_dir):
    """建立标准目录骨架（幂等）。返回 {ok, dirs}。"""
    out = {"ok": False, "dirs": {}}
    try:
        wd = os.path.abspath(str(work_dir))
        os.makedirs(wd, exist_ok=True)
        for d in KEEP_DIRS + SCRATCH_DIRS + (SCRIPT_DIR,):
            p = os.path.join(wd, d)
            os.makedirs(p, exist_ok=True)
            out["dirs"][d] = p
        out["ok"] = True
        out["work_dir"] = wd
        return out
    except Exception as e:
        out["error"] = "建立任务目录骨架失败: %r" % (e,)
        return out


def dirs_of(work_dir):
    """返回标准目录路径表（不存在就返回预期路径，不创建）。"""
    wd = os.path.abspath(str(work_dir or ""))
    out = {"work_dir": wd}
    out["parts"] = os.path.join(wd, "parts")
    out["drawings"] = os.path.join(wd, "drawings")
    out["reports"] = os.path.join(wd, "reports")
    out["intermediate"] = os.path.join(wd, "intermediate")
    out["parts_rejects"] = os.path.join(wd, "parts_rejects")
    out["scripts"] = os.path.join(wd, SCRIPT_DIR)
    out["scripts_archive"] = os.path.join(wd, SCRIPT_ARCHIVE)
    return out


def classify_name(name):
    """按文件名判断它该归到哪一类：part / drawing / keep / scratch。"""
    low = str(name or "").lower()
    # ① 运行时状态 / 计划文件 —— 一眼都不能动（见 KEEP_FILES 的注释）
    if low in {k.lower() for k in KEEP_FILES}:
        return "keep"
    if low.startswith("artifacts_registry."):
        return "keep"
    # ② 十阶段产物（artifact_*.md 之类）是分析证据，且被 mode2_plan 引用
    if low.startswith("artifact_") or low.startswith("stage"):
        return "keep"
    if low.endswith(PART_EXTS):
        return "part"
    if low.endswith(DRAWING_EXTS):
        return "drawing"
    # 凭据与报告必须留在原处：.material.json / *.report.json / *.physics.json …
    if low.endswith(".material.json") or low.endswith(".report.json"):
        return "keep"
    if low.endswith(".physics.json") or low.endswith(".domain.json"):
        return "keep"
    if low.startswith("task_finished") or low == "task_info.json":
        return "keep"
    if low.endswith(SCRATCH_EXTS):
        return "scratch"
    return "scratch"


def plan_tidy(work_dir):
    """扫描任务目录根，给出"该搬到哪/该删"的清单（只读，不动磁盘）。

    返回 {ok, work_dir, moves:[{src,dst,kind}], stray_parts:[...], unknown:[...]}
    """
    wd = os.path.abspath(str(work_dir))
    out = {"ok": False, "work_dir": wd, "moves": [], "kept": [], "unknown": []}
    if not os.path.isdir(wd):
        out["error"] = "任务目录不存在: %s" % wd
        return out
    d = dirs_of(wd)
    try:
        for name in sorted(os.listdir(wd)):
            src = os.path.join(wd, name)
            if os.path.isdir(src):
                # 标准目录自身跳过；未知文件夹整体当中间产物（收尾可清）
                if name in KEEP_DIRS or name in SCRATCH_DIRS or name == SCRIPT_DIR:
                    continue
                out["moves"].append({"src": src, "dst": os.path.join(d["intermediate"], name),
                                     "kind": "scratch_dir"})
                continue
            kind = classify_name(name)
            if kind == "keep":
                out["kept"].append(src)
            elif kind == "part":
                out["moves"].append({"src": src, "dst": os.path.join(d["parts"], name),
                                     "kind": "part"})
            elif kind == "drawing":
                out["moves"].append({"src": src, "dst": os.path.join(d["drawings"], name),
                                     "kind": "drawing"})
            else:
                out["moves"].append({"src": src, "dst": os.path.join(d["intermediate"], name),
                                     "kind": "scratch"})
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = "扫描任务目录失败: %r" % (e,)
        return out


def tidy(work_dir, dry_run=False):
    """把任务根目录下的散落文件搬进标准目录（幂等、可重入）。

    ⚠️ 刻意【不】调用 ensure_layout：那会顺手创建 parts_rejects/ 等空目录，
       而 cleanup() 刚删完它们又会被这里创建回来（实测踩过）。
       搬运时 shutil.move 会按需 makedirs，不需要预先铺架子。
    """
    plan = plan_tidy(work_dir)
    if not plan.get("ok"):
        return plan
    moved, failed = [], []
    if not dry_run:
        try:
            for _k in KEEP_DIRS:
                os.makedirs(os.path.join(os.path.abspath(str(work_dir)), _k), exist_ok=True)
        except Exception:
            pass
        for mv in plan["moves"]:
            try:
                os.makedirs(os.path.dirname(mv["dst"]), exist_ok=True)
                if os.path.exists(mv["dst"]):
                    base, ext = os.path.splitext(mv["dst"])
                    mv["dst"] = base + ".dup%d%s" % (int(time.time()), ext)
                shutil.move(mv["src"], mv["dst"])
                moved.append(mv)
            except Exception as e:
                failed.append({"src": mv.get("src"), "error": repr(e)})
    return {"ok": True, "work_dir": plan["work_dir"], "dry_run": bool(dry_run),
            "planned": plan["moves"], "moved": moved, "failed": failed,
            "kept": plan["kept"],
            "note": ("零件→parts/ 工程图→drawings/ 其余→intermediate/；"
                     "凭据与报告留在原处。")}


def reject_part(work_dir, path, reason=""):
    """把一个【建模失败/废弃】的零件搬进 parts_rejects/（收尾会被删掉）。

    用户要求：「假设建模失败的文件，直接不用保存即可」。
    —— 不直接 unlink，是因为失败件本身是排障证据；放进 rejects/ 让
      收尾统一清理，同时不会污染 parts/（那是交付目录）。
    """
    out = {"ok": False, "from": path}
    try:
        d = dirs_of(work_dir)
        src = os.path.abspath(str(path))
        if not os.path.exists(src):
            out["ok"] = True
            out["note"] = "文件本就不存在，无需处理"
            return out
        os.makedirs(d["parts_rejects"], exist_ok=True)
        dst = os.path.join(d["parts_rejects"], os.path.basename(src))
        if os.path.exists(dst):
            base, ext = os.path.splitext(dst)
            dst = base + ".%d%s" % (int(time.time()), ext)
        shutil.move(src, dst)
        # 失败件旁的 .material.json 凭据也一并移走，避免残留在 parts/
        # 命名沿用 "<零件名>.meta.json" —— rejects/ 只是暂存区，收尾会整目录删掉。
        for side in (src + ".material.json", src + ".report.json"):
            if os.path.exists(side):
                try:
                    shutil.move(side, dst + ".meta.json")
                except Exception:
                    pass
        try:
            rec = {"at": _now(), "from": src, "to": dst, "reason": str(reason or "")}
            _rp = os.path.join(d["parts_rejects"], "rejected.json")
            lst = []
            if os.path.exists(_rp):
                try:
                    with open(_rp, "r", encoding="utf-8") as f:
                        lst = json.load(f) or []
                except Exception:
                    lst = []
            if not isinstance(lst, list):
                lst = []
            lst.append(rec)
            with open(_rp, "w", encoding="utf-8") as f:
                json.dump(lst[-200:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        out.update({"ok": True, "to": dst, "reason": str(reason or "")})
        return out
    except Exception as e:
        out["error"] = "移入 parts_rejects 失败: %r" % (e,)
        return out


def _rm(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False


def cleanup(work_dir, keep_scripts=True, dry_run=False, apply=True):
    """收尾清理：删 intermediate/ 与 parts_rejects/，脚本归档，写清单。

    Args:
        keep_scripts: True = 把 scripts/ 搬到 reports/scripts_archive/；
                      False = 直接删掉（用户明确不要时的选项）。
        dry_run: 只报告不执行。
        apply: False 时等同于 dry_run（保留参数语义，便于门禁回传"计划"）。

    返回 {ok, removed:[...], archived:[...], manifest, saved_bytes}
    """
    out = {"ok": False, "work_dir": os.path.abspath(str(work_dir or "")),
           "removed": [], "archived": [], "skipped": []}
    wd = out["work_dir"]
    if not os.path.isdir(wd):
        out["error"] = "任务目录不存在: %s" % wd
        return out
    d = dirs_of(wd)
    really = (not dry_run) and apply
    saved = 0

    def _size(p):
        try:
            if os.path.isfile(p):
                return os.path.getsize(p)
            t = 0
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    try:
                        t += os.path.getsize(os.path.join(root, fn))
                    except Exception:
                        pass
            return t
        except Exception:
            return 0

    # ① 中间产物目录
    for name in SCRATCH_DIRS:
        p = os.path.join(wd, name)
        if not os.path.exists(p):
            continue
        sz = _size(p)
        if really:
            if _rm(p):
                out["removed"].append({"path": p, "kind": name, "bytes": sz})
                saved += sz
            else:
                out["skipped"].append({"path": p, "reason": "删除失败"})
        else:
            out["removed"].append({"path": p, "kind": name, "bytes": sz, "dry_run": True})

    # ② 脚本目录：归档或删除
    sp = os.path.join(wd, SCRIPT_DIR)
    if os.path.isdir(sp):
        sz = _size(sp)
        if not keep_scripts:
            if really:
                if _rm(sp):
                    out["removed"].append({"path": sp, "kind": "scripts", "bytes": sz})
                    saved += sz
            else:
                out["removed"].append({"path": sp, "kind": "scripts", "bytes": sz, "dry_run": True})
        else:
            dst = os.path.join(wd, SCRIPT_ARCHIVE)
            if really:
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.exists(dst):
                        dst = dst + "_" + time.strftime("%Y%m%d_%H%M%S")
                    shutil.move(sp, dst)
                    out["archived"].append({"from": sp, "to": dst, "bytes": sz})
                except Exception as e:
                    out["skipped"].append({"path": sp, "reason": repr(e)})
            else:
                out["archived"].append({"from": sp, "to": dst, "bytes": sz, "dry_run": True})

    # ③ 根目录残留（二次保险：收尾前再 tidy 一次，避免又有新散落文件）
    if really:
        try:
            tidy(wd)
        except Exception:
            pass
        # tidy 之后 intermediate/ 可能刚被创建，再清一次
        ip = os.path.join(wd, "intermediate")
        if os.path.exists(ip):
            sz = _size(ip)
            if _rm(ip):
                out["removed"].append({"path": ip, "kind": "intermediate", "bytes": sz})
                saved += sz

    out["saved_bytes"] = saved
    out["saved_mb"] = round(saved / 1048576.0, 2)
    out["ok"] = True

    # ④ 清理清单落在 reports/（交付物之一，永不删）
    manifest_path = os.path.join(d["reports"], "cleanup_manifest.json")
    manifest = {
        "at": _now(), "work_dir": wd, "dry_run": (not really),
        "removed": out["removed"], "archived": out["archived"],
        "skipped": out["skipped"], "saved_bytes": saved,
        "kept_dirs": list(KEEP_DIRS),
        "note": ("只清 intermediate/ 与 parts_rejects/；parts/ drawings/ reports/ 原样保留。"
                 "脚本默认归档到 reports/scripts_archive/。"),
    }
    if really:
        try:
            os.makedirs(d["reports"], exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            out["manifest"] = manifest_path
        except Exception as e:
            out["manifest_error"] = repr(e)
    else:
        out["manifest"] = None
    out["preview"] = manifest
    return out


def survey(work_dir):
    """给看板/UI 用的目录概览：每个标准目录有几个文件、多大。"""
    wd = os.path.abspath(str(work_dir or ""))
    d = dirs_of(wd)
    out = {"ok": True, "work_dir": wd, "dirs": {}, "cleanable_mb": 0.0}
    for name in KEEP_DIRS + SCRATCH_DIRS + (SCRIPT_DIR,):
        p = d.get(name) or os.path.join(wd, name)
        n, sz = 0, 0
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                for fn in files:
                    n += 1
                    try:
                        sz += os.path.getsize(os.path.join(root, fn))
                    except Exception:
                        pass
        out["dirs"][name] = {"path": p, "exists": os.path.isdir(p),
                             "files": n, "mb": round(sz / 1048576.0, 2)}
        if name in SCRATCH_DIRS:
            out["cleanable_mb"] = round(out["cleanable_mb"] + sz / 1048576.0, 2)
    stray = []
    if os.path.isdir(wd):
        try:
            for name in sorted(os.listdir(wd)):
                p = os.path.join(wd, name)
                if os.path.isdir(p):
                    if name not in KEEP_DIRS and name not in SCRATCH_DIRS and name != SCRIPT_DIR:
                        stray.append(name)
                elif classify_name(name) != "keep":
                    stray.append(name)
        except Exception:
            pass
    out["stray"] = stray[:200]
    out["stray_count"] = len(stray)
    return out
