# -*- coding: utf-8 -*-
r"""回归测试：F-01 dry-run 假执行 + F-02 _root.py 编码 + F-03 select 无 --brief

★ 锁定的真实事故：
  F-01（最优先）：python mode_gate.py room-end X --force --dry-run 中
        --dry-run 被 CLI【静默丢弃】→ 命令真实执行了下线、写入
        reports/defense_bypass.json（exit=0, ok=true）。
        这是"预演变成真操作"，是最危险的一类静默语义篡改。
  F-02：python _root.py 中文全乱码（其它脚本都有 reconfigure，唯独它没有）。
  F-03：workflow_gate select 回包全量内联十几 KB，无 --brief 通道。

★ 本测试钉死的契约：
  A. 未知 --flag 必须显式拒绝，并【列出合法参数】（不静默丢弃）；
  B. room-end --dry-run 零副作用：不写 defense_bypass.json、不改 mode_state、
     不释放 SW 锁；且返回体说明"本会做什么"；
  C. 不带 --dry-run 时行为不变（真执行、真留痕）—— 防止"修成永远不执行"；
  D. _root.py 输出可被 UTF-8 解码且不含替换符（子进程实测，--json 同）；
  E. select --brief：返回体远小于全量，且关键结论字段齐全、大对象有文件路径；
     默认（不加 --brief）仍全量 —— 不破坏既有调用方。

全部用【临时 DSH_STATE_DIR 沙箱】+ 子进程真跑 CLI，不碰仓库真实状态。

运行：python test_regression_dryrun_and_encoding.py
退出码：0 = 全部通过；1 = 有失败
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label + ("  <" + str(detail)[:400] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


# ══════════════════════════════════════════════════════════════════════
# 沙箱：每次跑 CLI 都用独立的 DSH_STATE_DIR，绝不碰仓库真实状态
# ══════════════════════════════════════════════════════════════════════
_SANDBOXES = []


def _sandbox():
    d = tempfile.mkdtemp(prefix="dsh_f01_")
    _SANDBOXES.append(d)
    return d


def _cleanup():
    for d in _SANDBOXES:
        shutil.rmtree(d, ignore_errors=True)


def _env(state_dir, work_dir=None):
    e = dict(os.environ)
    e["DSH_STATE_DIR"] = state_dir
    e["DSH_ENGINEERING_ROOT"] = os.path.dirname(HERE)
    e.pop("DSH_DEFENSE_BYPASS", None)
    if work_dir:
        e["DSH_WORK_DIR"] = work_dir
    return e


def _run(script, args, state_dir, work_dir=None):
    """跑 CLI，返回 (returncode, stdout_text, parsed_json_or_None)。"""
    p = subprocess.run([sys.executable, script] + list(args), cwd=HERE,
                       capture_output=True, env=_env(state_dir, work_dir))
    raw = p.stdout
    text = raw.decode("utf-8", "replace")
    try:
        return p.returncode, text, json.loads(text)
    except Exception:
        return p.returncode, text, None


def _snapshot(root):
    """目录快照：相对路径 -> (size, mtime)。用于断言"零副作用"。"""
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                out[os.path.relpath(p, root)] = (os.path.getsize(p),
                                                 os.path.getmtime(p))
            except Exception:
                continue
    return out


# ══════════════════════════════════════════════════════════════════════
sec("F-01 A. 未知 --flag 必须显式拒绝并列出合法参数")
# ══════════════════════════════════════════════════════════════════════
_sb = _sandbox()
_run("mode_gate.py", ["declare", "2"], _sb)
_run("mode_gate.py", ["room-start", "X"], _sb)

_rc, _txt, _j = _run("mode_gate.py", ["room-end", "X", "--forse"], _sb)
check("★ 未知 flag --forse → ok=false", _j is not None and _j.get("ok") is False,
      _txt[:300])
check("★ gate == UNKNOWN_FLAG", (_j or {}).get("gate") == "UNKNOWN_FLAG",
      (_j or {}).get("gate"))
check("★ unknown_flags 里列出 --forse",
      "--forse" in ((_j or {}).get("unknown_flags") or []),
      (_j or {}).get("unknown_flags"))
check("★ allowed_flags 列出合法参数（含 --force / --dry-run）",
      set(["--force", "--dry-run"]).issubset(
          set((_j or {}).get("allowed_flags") or [])),
      (_j or {}).get("allowed_flags"))
check("★ 退出码非 0（自动化可识别为调用错误）", _rc != 0, _rc)
check("★ 错误信息说明『绝不静默丢弃未知参数』",
      "绝不静默丢弃" in str((_j or {}).get("error")),
      str((_j or {}).get("error"))[:300])

_rc2, _txt2, _j2 = _run("mode_gate.py",
                        ["room-end", "X", "--force", "--dry-run"], _sb)
check("★ room-end X --force --dry-run → dry_run=true（--dry-run 被识别）",
      (_j2 or {}).get("dry_run") is True,
      ((_j2 or {}).get("gate"), (_j2 or {}).get("dry_run")))
check("★ 该组合不是 UNKNOWN_FLAG（两个参数都合法）",
      (_j2 or {}).get("gate") != "UNKNOWN_FLAG", (_j2 or {}).get("gate"))

_rc3, _txt3, _j3 = _run("mode_gate.py", ["room-end", "X", "--no-such-thing"], _sb)
check("★ 臆造 flag → 拒绝", (_j3 or {}).get("ok") is False, _txt3[:200])

# ══════════════════════════════════════════════════════════════════════
sec("F-01 B. room-end --dry-run 零副作用")
# ══════════════════════════════════════════════════════════════════════
_sb = _sandbox()
_run("mode_gate.py", ["declare", "2"], _sb)
_run("mode_gate.py", ["room-start", "Y"], _sb)

_st_path = os.path.join(_sb, "mode_state.json")
_before_state = json.load(open(_st_path, encoding="utf-8"))
_before_room = dict((_before_state.get("rooms") or {}).get("Y") or {})
_before_snap = _snapshot(_sb)

_rc, _txt, _j = _run("mode_gate.py",
                     ["room-end", "Y", "--force", "--dry-run"], _sb)
check("dry-run 退出码 0", _rc == 0, _rc)
check("★ dry-run 返回 ok=true（预演成功）", (_j or {}).get("ok") is True,
      _txt[:300])
check("★ 返回体标明 dry_run=true", (_j or {}).get("dry_run") is True,
      (_j or {}).get("dry_run"))
check("★ gate == DRY_RUN", (_j or {}).get("gate") == "DRY_RUN",
      (_j or {}).get("gate"))
check("★ 返回 would_do（说明本会执行哪些动作）",
      bool((_j or {}).get("would_do")),
      (_j or {}).get("would_do"))
check("★ would_do 里明确提到会写 defense_bypass.json（--force 时）",
      any("defense_bypass" in str(x) for x in ((_j or {}).get("would_do") or [])),
      (_j or {}).get("would_do"))
check("★ 返回体声明 side_effects 为空",
      (_j or {}).get("side_effects") == [], (_j or {}).get("side_effects"))

_after_snap = _snapshot(_sb)
check("★ dry-run 后目录快照完全不变（零副作用）",
      _after_snap == _before_snap,
      ("new=%s changed=%s"
       % (sorted(set(_after_snap) - set(_before_snap)),
          sorted(k for k in _after_snap
                 if k in _before_snap and _after_snap[k] != _before_snap[k]))))
check("★ 没有写出 defense_bypass.json",
      not os.path.exists(os.path.join(_sb, "defense_bypass.json")))

_after_state = json.load(open(_st_path, encoding="utf-8"))
_after_room = dict((_after_state.get("rooms") or {}).get("Y") or {})
check("★ mode_state 里房间 Y 仍是 active（没被下线）",
      _after_room.get("active") is True, _after_room)
check("★ mode_state 里房间 Y 没有写 ended_at",
      _after_room.get("ended_at") == _before_room.get("ended_at"),
      (_after_room.get("ended_at"), _before_room.get("ended_at")))
check("★ SW 锁没有被改动",
      (_after_state.get("sw_lock") or {}) == (_before_state.get("sw_lock") or {}),
      (_after_state.get("sw_lock"), _before_state.get("sw_lock")))

# ══════════════════════════════════════════════════════════════════════
sec("F-01 C. 不带 --dry-run 时行为不变（真执行、真留痕）")
# ══════════════════════════════════════════════════════════════════════
_sb = _sandbox()
_run("mode_gate.py", ["declare", "2"], _sb)
_run("mode_gate.py", ["room-start", "Z"], _sb)
_before2 = _snapshot(_sb)
_rc, _txt, _j = _run("mode_gate.py", ["room-end", "Z", "--force"], _sb)
check("真执行退出码 0", _rc == 0, _rc)
check("★ 真执行 ok=true 且【不是】dry_run",
      (_j or {}).get("ok") is True and not (_j or {}).get("dry_run"),
      ((_j or {}).get("ok"), (_j or {}).get("dry_run")))
_after2 = _snapshot(_sb)
check("★ 真执行确实改了状态（证明修复没把功能改坏）",
      _after2 != _before2,
      ("new=%s" % (sorted(set(_after2) - set(_before2)),)))
_st2 = json.load(open(os.path.join(_sb, "mode_state.json"), encoding="utf-8"))
check("★ 真执行后房间 Z 已下线（active=False）",
      ((_st2.get("rooms") or {}).get("Z") or {}).get("active") is False,
      (_st2.get("rooms") or {}).get("Z"))

# ══════════════════════════════════════════════════════════════════════
sec("F-02 _root.py UTF-8 输出（子进程实测）")
# ══════════════════════════════════════════════════════════════════════
for _args, _label in (([" _root.py".strip()], "默认"),
                      (["_root.py", "--json"], "--json")):
    p = subprocess.run([sys.executable] + _args, cwd=HERE, capture_output=True,
                       env=_env(_sandbox()))
    raw = p.stdout
    _ok_utf8 = True
    _text = ""
    try:
        _text = raw.decode("utf-8")
    except UnicodeDecodeError as _e:
        _ok_utf8 = False
        _text = repr(_e)
    check("★ _root.py %s：stdout 可被 UTF-8 解码" % _label, _ok_utf8,
          _text[:200])
    check("★ _root.py %s：不含替换符 U+FFFD（没乱码）" % _label,
          "\ufffd" not in _text, _text[:300])
    check("_root.py %s：退出码 0" % _label, p.returncode == 0, p.returncode)

p = subprocess.run([sys.executable, "_root.py"], cwd=HERE, capture_output=True,
                   env=_env(_sandbox()))
_t = p.stdout.decode("utf-8", "replace")
check("★ 默认输出含可读中文『工程模式根目录』", "工程模式根目录" in _t, _t[:300])
check("★ 默认输出含可读中文『门禁脚本』", "门禁脚本" in _t, _t[:400])
check("★ 默认输出不再出现乱码字节序列（如 '宸ョ▼'）",
      "宸ョ▼" not in _t and "鍏ラ棬" not in _t, _t[:300])

p = subprocess.run([sys.executable, "_root.py", "--json"], cwd=HERE,
                   capture_output=True, env=_env(_sandbox()))
try:
    _rj = json.loads(p.stdout.decode("utf-8"))
    _json_ok = isinstance(_rj, dict) and "engineering_root" in _rj
except Exception as _e:
    _json_ok = False
    _rj = repr(_e)
check("★ _root.py --json 输出仍是合法 JSON", _json_ok, str(_rj)[:200])

_src = open(os.path.join(HERE, "_root.py"), encoding="utf-8").read()
check("★ _root.py 里有 sys.stdout.reconfigure(encoding=...)",
      "sys.stdout.reconfigure" in _src and "utf-8" in _src)
check("★ _root.py 里有 sys.stderr.reconfigure(encoding=...)",
      "sys.stderr.reconfigure" in _src)
check("★ reconfigure 被 try/except 包裹（兼容无该属性的环境）",
      "try:" in _src and "except Exception:" in _src)

# ══════════════════════════════════════════════════════════════════════
sec("F-03 select --brief：大配置改文件引用，默认全量不变")
# ══════════════════════════════════════════════════════════════════════
_sb = _sandbox()
os.environ["DSH_STATE_DIR"] = _sb
os.environ["DSH_ENGINEERING_ROOT"] = os.path.dirname(HERE)
import workflow_gate as wg  # noqa: E402

check("★ 新增 _select_brief 辅助函数",
      callable(getattr(wg, "_select_brief", None)))
check("★ cmd_select 支持 brief 参数",
      "brief" in (wg.cmd_select.__code__.co_varnames
                  if hasattr(wg.cmd_select, "__code__") else ()))

_rooms = [["结构件", "structural"], ["传动机构", "transmission"],
          ["壳体机架", "housing"]]
_full = {
    "ok": True, "step": "user_selected", "choice": "C", "choice_label": "C",
    "parallel_mode": "E", "parallel_label": "E", "subagent_strategy": "wave",
    "subagent_config": {"rooms": _rooms, "blob": "x" * 4000},
    "subagent_plan": {"rooms": _rooms, "blob": "w" * 6000},
    "part_ownership": {"rooms": {r[0]: {"keywords": ["a"] * 50} for r in _rooms},
                       "blob": "z" * 5000},
    # 原键名 prime_report_contract 已随总理子代理删除，换成 monitor_contract
    "monitor_contract": {"parts": "p" * 4000, "review": "r" * 3000},
    "assembly_confirmed": False,
    "pending_rooms": _rooms, "wave_rooms": _rooms,
    "all_pending_rooms": [{"name": r[0], "type": r[1]} for r in _rooms],
    "all_pending_count": 3, "pending_rooms_note": "note",
    "completed_count": 0, "total_rooms": 3, "wave": 1, "wave_name": "W1",
    "sync": {"done_rooms": [], "all_rooms": ["结构件"], "in_sync": True},
    "message": "M", "conflict_warning": None,
    "part_ownership_file": "/tmp/x.json",
    "subagent_plan_required": False,
    "ready_to_build": True,
}


def _size(o):
    return len(json.dumps(o, ensure_ascii=False).encode("utf-8"))


_brief = wg._select_brief(_full)
_full_sz, _brief_sz = _size(_full), _size(_brief)
check("★ brief 返回体显著小于全量（< 40%）",
      _brief_sz < _full_sz * 0.40,
      "full=%d brief=%d (%.1f%%)" % (_full_sz, _brief_sz,
                                     100.0 * _brief_sz / _full_sz))
check("★ brief 标记为 brief=true", _brief.get("brief") is True)

for _k in ("ok", "step", "choice", "parallel_mode", "pending_rooms",
           "wave_rooms", "all_pending_rooms", "all_pending_count",
           "completed_count", "total_rooms", "wave", "wave_name",
           "sync", "message", "ready_to_build"):
    check("★ brief 保留关键字段 %r" % _k, _k in _brief, sorted(_brief)[:20])

_ext = _brief.get("brief_externalized") or {}
for _k in ("subagent_config", "subagent_plan", "part_ownership",
           "monitor_contract"):
    check("★ brief 把 %r 外置成文件" % _k, _k in _ext, sorted(_ext))
    _p = _brief.get(_k + "_file")
    check("★ brief 给出 %r 的文件路径且文件存在" % _k,
          bool(_p) and os.path.exists(_p), _p)
    if _p and os.path.exists(_p):
        try:
            _back = json.load(open(_p, encoding="utf-8"))
            _same = (_back == _full[_k])
        except Exception:
            _same = False
        check("★ 外置文件 %r 内容与全量完全一致（没丢信息）" % _k, _same, _p)
        check("★ brief 里 %r 不再内联（体积已移出）" % _k, _k not in _brief)

check("★ brief 给出 brief_note 说明怎么读",
      bool(_brief.get("brief_note")), _brief.get("brief_note"))

_src = open(os.path.join(HERE, "workflow_gate.py"), encoding="utf-8").read()
check("★ 源码里 brief 只在显式开关时生效（默认全量）",
      "return _select_brief(_sel_out) if brief else _sel_out" in _src,
      [ln for ln in _src.split(chr(10)) if "_select_brief(_sel_out)" in ln][:2])
check("★ CLI 识别 --brief 开关（不会被 FL-06 当未知参数拒绝）",
      '"--brief"' in _src and '"-b"' in _src)
check("★ --brief 在 _parse_choice_args 里被提前拦下（不进 unknown）",
      'if a in ("--brief", "-b"):' in _src)

# ══════════════════════════════════════════════════════════════════════
print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for x in FAILURES:
        print("  - " + x)
print("")
print("真机验证（需真实 SolidWorks 会话，人工执行）：")
print("  · room-end X --force --dry-run 后 reports/defense_bypass.json 必须【不存在】")
print("  · 去掉 --dry-run 再跑一次，房间才真正下线")
print("  · select C,E --brief 的返回体应是原全量的 <20%")
_cleanup()
sys.exit(0 if FAIL == 0 else 1)
