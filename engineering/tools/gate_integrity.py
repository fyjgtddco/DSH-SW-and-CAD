# -*- coding: utf-8 -*-
r"""gate_integrity.py —— 门禁核心脚本的完整性自检（【A-20】修复）

═══ 为什么需要这个模块 ═══════════════════════════════════════════════════
2026-11-01 任务（20261101_002006_024C510A）实测：
  逐份子代理日志里 "has been updated successfully" 去重后共 **33 次**，
  跨 **7 个代理**，改的全是【门禁自己】：
      fea_solver.py ×21、swapi.py ×5、domain_validator.py ×2、
      physics_bridge.py ×2、mode_gate.py ×1、sw_bridge.py ×2
  其中一次修完之后，check-room 从
      ok=false, blockers=["【防线③领域】…4 条 CRITICAL"]
  变成 ok=true, blockers=[] —— 而磁盘上的零件没有任何变化。

这是三道防线最根本的架构漏洞：**裁判可以被被告改写**。
  判定逻辑（defense_gate / physics / domain）+ 判定对象（parts/ 下的零件）
  都在同一个可写目录里，AI 有写权限 → "改源码让判定变绿"永远比
  "改设计让校核通过"便宜。

═══ 本模块的定位（诚实说明能力边界）═══════════════════════════════════
它不是、也不可能阻止有写权限的进程去改文件 —— 那是操作系统权限的事。
它能做的是【让改动无法静默】：
  · 首次运行时把门禁核心文件的 sha256 记进基线文件；
  · 之后每次调用都能回答"这些文件相对基线有没有变"；
  · 门禁关键路径（room-end / confirm-assembly / 收尾）调用 assert_clean()，
    一旦发现被改就【留痕 + 降级】而不是静默照常放行。

用法：
    python gate_integrity.py baseline            # 建立/更新基线（人工确认后）
    python gate_integrity.py check [--json]      # 检查是否被改动（CI/门禁可调）
    python gate_integrity.py explain             # 说明基线与威胁模型

作为库：
    from gate_integrity import check_integrity, assert_clean
    r = check_integrity()          # {ok, changed[], missing[], baseline_at}
    assert_clean("room-end")       # 被改则写留痕并返回 False

═══ 设计取舍 ═══════════════════════════════════════════════════════════
· 基线缺失时 **不判失败**（首次部署 / 换机都不该直接红），
  只返回 baseline_missing=True 并在门禁返回体里如实标注；
· 基线文件本身放在 .defense/ 下，与凭据隔离区同处；
· 只覆盖【判定逻辑】文件，不覆盖业务脚本（避免正常迭代天天报红）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── 被保护的文件：判定逻辑本身 ────────────────────────────────────────────
# 注意：刻意【不包含】sw_bridge.py / ask_user.py 这类业务入口 ——
#   它们经常需要按现场调整，纳入保护会导致天天报"被改"，
#   反而让人习惯性忽略告警（告警疲劳）。真正的判定本体才在这里。
PROTECTED = (
    "defense_gate.py",              # 三道防线的判定本体
    os.path.join("physics", "fea_solver.py"),        # 强度/疲劳判据
    os.path.join("physics", "domain_validator.py"),  # 领域规则引擎
    os.path.join("physics", "load_case.py"),         # 载荷口径（唯一事实源）
    os.path.join("physics", "fatigue.py"),           # 疲劳判据
    os.path.join("physics", "geometry_gate.py"),     # 几何门禁
    os.path.join("physics", "material_db.py"),       # 材料库（属性来源）
)

BASELINE_NAME = "gate_baseline.json"


def _state_dir():
    """状态目录（与 _store.state_dir 同口径，但这里不 import 以免循环依赖）。"""
    try:
        env = (os.environ.get("DSH_STATE_DIR") or "").strip()
        if env:
            return env
    except Exception:
        pass
    try:
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        if home:
            cand = os.path.join(home, ".dsh", ".agent-presets", "engineering", "tools")
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return _HERE


def baseline_path():
    return os.path.join(_state_dir(), ".defense", BASELINE_NAME)


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _file_mtimes(root=None):
    """取全部受保护文件的 sha256 + 大小。"""
    root = root or _HERE
    out = {}
    for rel in PROTECTED:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            continue
        key = rel.replace(os.sep, "/")
        try:
            out[key] = {
                "sha256": _sha256(p),
                "size": int(os.path.getsize(p)),
                "mtime": round(os.path.getmtime(p), 3),
            }
        except Exception:
            continue
    return out


def write_baseline(reason="manual", root=None):
    """建立/更新基线。只有【人工确认过】才该调用。"""
    p = baseline_path()
    data = {
        "schema": "dsh-engineering/gate-baseline@1",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "reason": str(reason),
        "root": os.path.abspath(root or _HERE),
        "files": _file_mtimes(root),
    }
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return {"ok": True, "path": p, "count": len(data["files"])}
    except Exception as e:
        return {"ok": False, "error": repr(e), "path": p}


def load_baseline():
    p = baseline_path()
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def check_integrity(root=None):
    """检查受保护文件相对基线是否被改动。

    Returns:
        {ok, baseline_missing, baseline_at, changed[], missing[], added[],
         checked_count, note}
        ok=True 表示"没有发现相对基线的改动"（基线缺失时也为 True，
        但 baseline_missing=True，调用方应如实标注"未校验"）。
    """
    out = {"ok": True, "baseline_missing": False, "baseline_at": None,
           "changed": [], "missing": [], "added": [],
           "checked_count": 0, "note": ""}
    bl = load_baseline()
    cur = _file_mtimes(root)
    out["checked_count"] = len(cur)
    if not bl or not isinstance(bl.get("files"), dict):
        out["baseline_missing"] = True
        out["note"] = (
            "尚无门禁完整性基线 —— 本次【未校验】判定逻辑是否被改动。"
            "人工确认当前代码可信后执行一次："
            "python gate_integrity.py baseline")
        return out
    out["baseline_at"] = bl.get("created_at")
    base = bl["files"]
    for rel, info in base.items():
        c = cur.get(rel)
        if c is None:
            out["missing"].append(rel)
            continue
        if str(c.get("sha256") or "") != str(info.get("sha256") or ""):
            out["changed"].append({
                "file": rel,
                "sha_now": (c.get("sha256") or "")[:16],
                "sha_baseline": (info.get("sha256") or "")[:16],
                "size_now": c.get("size"),
                "size_baseline": info.get("size"),
            })
    for rel in cur:
        if rel not in base:
            out["added"].append(rel)
    _bad = bool(out["changed"] or out["missing"])
    out["ok"] = not _bad
    if _bad:
        out["note"] = (
            "【A-20】门禁判定逻辑相对基线发生了改动：%d 个文件被改、%d 个缺失。"
            "三道防线的判定本体被改过，其结论的可信度存疑 —— "
            "请先核对 diff 是否经人工审阅；确认无误后重建基线："
            "python gate_integrity.py baseline"
            % (len(out["changed"]), len(out["missing"])))
    return out


def assert_clean(where="", room=""):
    """门禁关键路径调用点：返回 (clean: bool, evidence: dict)。

    被改动时不抛异常、不阻断（避免把正常迭代锁死），但会：
      ① 把事件写进 .defense/gate_integrity_log.jsonl（只追加，可审计）；
      ② 返回 clean=False，供调用方在响应体里如实标注。
    """
    r = check_integrity()
    if r.get("ok"):
        return True, r
    try:
        _log = os.path.join(_state_dir(), ".defense", "gate_integrity_log.jsonl")
        os.makedirs(os.path.dirname(_log), exist_ok=True)
        rec = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "where": str(where),
            "room": str(room),
            "changed": [c.get("file") for c in (r.get("changed") or [])],
            "missing": r.get("missing") or [],
            "baseline_at": r.get("baseline_at"),
        }
        with io.open(_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        r["logged_to"] = _log
        try:
            if os.path.getsize(_log) > 2 * 1024 * 1024:
                with io.open(_log, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-2000:]
                with io.open(_log, "w", encoding="utf-8") as f:
                    f.writelines(lines)
        except Exception:
            pass
    except Exception:
        pass
    return False, r


def protect_gate_files(scope="all", dry_run=False):
    """【A-20 修复】把判定本体置为【只读】—— 让"裁判被被告改写"变贵。

    ── 原缺陷（本次审计再次确认）──────────────────────────────────────────
    本模块此前只能"事后发现被改"（留痕），改文件本身仍然是一次 chmod/覆盖
    就完成的事。同目录同账户，API 能力上没有任何阻力 —— 实测一次任务里
    7 个代理改了 33 次判定源码，其中一次直接把 CRITICAL 改成 PASS。

    ── 现在做的（是"提高成本"，不是"绝对防止"，如实说明）──────────────
    · 对 PROTECTED 里的文件清掉写位（Windows 只读属性 / POSIX 去 w）；
    · 改完立刻复核：os.access(path, W_OK) 必须为 False 才算成功；
    · 失败项如实列出（被占用/权限不足/文件系统不支持），绝不假装成功。

    这不是安全边界：有权限的进程依然能 chmod 回来。它的价值在于 ——
      "改判定逻辑"从此变成一个【明显、可留痕、需要两步】的动作，
      而不是 AI 顺手 edit 一下就能把红灯改绿。

    解除保护（人工审阅完改动、重建基线后）：protect_gate_files(scope="none")
    """
    out = {"ok": True, "protected": [], "failed": [], "already": [],
           "scope": str(scope), "dry_run": bool(dry_run)}
    if str(scope) in ("none", "off", "0", "false"):
        for rel in PROTECTED:
            p = os.path.join(_HERE, rel)
            if not os.path.isfile(p):
                continue
            if dry_run:
                out["protected"].append(rel)
                continue
            try:
                mode = os.stat(p).st_mode
                os.chmod(p, mode | 0o200)
                out["protected"].append(rel)
            except Exception as e:
                out["failed"].append({"file": rel, "error": repr(e)})
        out["ok"] = not out["failed"]
        out["note"] = "已解除只读保护（恢复可写）"
        return out
    for rel in PROTECTED:
        p = os.path.join(_HERE, rel)
        if not os.path.isfile(p):
            continue
        try:
            if not os.access(p, os.W_OK):
                out["already"].append(rel)
                continue
            if dry_run:
                out["protected"].append(rel)
                continue
            mode = os.stat(p).st_mode
            os.chmod(p, mode & ~0o222)
            if os.access(p, os.W_OK):
                out["failed"].append({"file": rel,
                                      "error": "chmod 后仍可写（文件系统/属性不生效）"})
            else:
                out["protected"].append(rel)
        except Exception as e:
            out["failed"].append({"file": rel, "error": repr(e)})
    out["ok"] = not out["failed"]
    if out["failed"]:
        out["note"] = ("部分文件无法置为只读（多半被进程占用或权限不足）——"
                       "这部分仍是可改的，改动只能靠基线比对发现。")
    return out


def register_objection(room, reason, evidence=None, by=""):
    """【A-20 补】合法的"异议通道"：对门禁结论不服时，走登记而不是改源码。

    问题：审计发现 AI 遇到门禁挡住时会去改判定源码（因为那是唯一"出路"）。
    现在给一条正规通道 —— 把"我认为这条结论不成立，理由与证据如下"
    记进 .defense/objections.jsonl，并【明确告诉调用方】：
      · 登记异议不会改变本次判定（门禁结论仍然生效，防线不因此放宽）；
      · 它会成为人工复核的输入，由人决定是改设计、改规则，还是改代码。
    这样"有不同意见"与"私自改裁判"就分开了。
    """
    rec = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "room": str(room or ""),
        "reason": str(reason or "").strip()[:2000],
        "evidence": evidence if isinstance(evidence, (list, dict)) else None,
        "by": str(by or ""),
    }
    if not rec["reason"]:
        return {"ok": False, "error": "异议必须写理由（reason 不能为空）"}
    try:
        _d = os.path.join(_state_dir(), ".defense")
        os.makedirs(_d, exist_ok=True)
        _p = os.path.join(_d, "objections.jsonl")
        with io.open(_p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "path": _p,
                "note": ("异议已登记。注意：本次门禁结论【不因此改变】，"
                         "登记只用于人工复核；不要为此去改判定源码（那会被基线比对抓到）。")}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    if cmd == "baseline":
        reason = argv[1] if len(argv) > 1 else "manual"
        res = write_baseline(reason=reason)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    if cmd == "explain":
        print(__doc__)
        return 0
    if cmd == "protect":
        # 【A-20】protect / unprotect —— 判定本体的 OS 写保护
        scope = "all"
        _args = [a for a in argv[1:] if not a.startswith("-")]
        if _args:
            scope = _args[0]
        if "--off" in argv or "unprotect" in argv:
            scope = "none"
        res = protect_gate_files(scope=scope, dry_run=("--dry-run" in argv))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    if cmd == "objection":
        # 【A-20】objection 登记：<room> <reason>
        _pos = [a for a in argv[1:] if not a.startswith("-")]
        _room = _pos[0] if _pos else ""
        _reason = _pos[1] if len(_pos) > 1 else ""
        res = register_objection(_room, _reason, by="cli")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    r = check_integrity()
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("受检文件数 : %d" % r["checked_count"])
        print("基线建立于 : %s" % (r.get("baseline_at") or "（尚未建立）"))
        print("结论       : %s" % ("未被改动" if r["ok"] else "【发现改动】"))
        for c in r.get("changed") or []:
            print("  改动 %-46s %s -> %s"
                  % (c["file"], c["sha_baseline"], c["sha_now"]))
        for m in r.get("missing") or []:
            print("  缺失 %s" % m)
        for a in r.get("added") or []:
            print("  新增 %s（基线里没有）" % a)
        if r.get("note"):
            print("说明       : %s" % r["note"])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
