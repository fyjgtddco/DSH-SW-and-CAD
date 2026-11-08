# -*- coding: utf-8 -*-
"""【A9 回归测试】压缩摘要里的状态行必须由脚本读盘生成（不是模型写的）

现场（缺陷清单 A9 / P1）：
    压缩摘要把"状态"交给模型叙述 → 摘要里出现【错误事实】：
      "已生成 xxx.SLDPRT"（文件根本不存在）、"刚刚更新"（mtime 已是两小时前）。
    压缩后下一轮把这些错误事实当权威前提继续干活。

根因：状态行是"让模型写"的。只要模型写，就会有记错的那一天。

修复（本仓库边界内可行的部分）：workflow_gate 新增
    build_state_line()   —— 用 os.stat（存在性 + mtime + age）和状态文件枚举
                            生成一行 [STATE] ...，供摘要【原样引用】；
    state-line 子命令    —— 只读、随时可跑，输出同一行；
    gate-summary         —— 结果里也带 state_line。

本测试锁定：
  A. 状态行只由磁盘事实构成（不存在的文件必须报"不存在"，不编造）；
  B. 时间全部是 mtime + 秒数年龄（可复算），不是"刚刚/N 分钟前"这类措辞；
  C. 房间计数取自 mode_state 的枚举值；
  D. CLI 子命令可用，且两次调用之间只有 age 在变（事实稳定）；
  E. gate-summary 里带同一行状态行。

运行：python test_regression_a9_state_line.py
退出码 0 = 通过
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

# ── 隔离：状态目录指到临时目录（_store.state_dir 会缓存，需先建好目录）──
_ISO = tempfile.mkdtemp(prefix="a9_state_")
os.environ["DSH_STATE_DIR"] = _ISO
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _store  # noqa: E402
_store.reset_cache()
import workflow_gate as wg  # noqa: E402

P = F = 0
FAILS = []


def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] " + label)
    else:
        F += 1
        FAILS.append(label)
        print("  [FAIL] " + label + ("  " + str(extra)[:400] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


def wipe_state():
    """清掉临时状态目录里的状态文件（每个用例从干净盘开始）。"""
    for fn in ("workflow_state.json", "mode_state.json"):
        p = os.path.join(_ISO, fn)
        if os.path.exists(p):
            os.remove(p)


def write_state(task_id="T-A9-1", step="user_selected", work_dir=None):
    payload = {"task_id": task_id, "step": step}
    if work_dir:
        payload["work_dir"] = work_dir
    with io.open(os.path.join(_ISO, "workflow_state.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


def write_mode_state(rooms):
    with io.open(os.path.join(_ISO, "mode_state.json"), "w", encoding="utf-8") as f:
        json.dump({"rooms": rooms}, f, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
sec("A. 状态行只由磁盘事实构成：文件不存在就写'不存在'（不编造）")
# ════════════════════════════════════════════════════════════════════
wipe_state()
_tmp_task = tempfile.mkdtemp(prefix="a9task_")
try:
    write_state(work_dir=_tmp_task)
    write_mode_state({})
    sl = wg.build_state_line()
    _txt = sl.get("text") or ""
    ck("build_state_line 返回 text + facts", bool(_txt) and bool(sl.get("facts")), sl.keys())
    ck("★ 状态行以 [STATE] 开头（便于摘要在正文里原样引用）",
       _txt.startswith("[STATE]"), _txt)
    ck("★ 不存在的 parts/ drawings 目录 → 明确写'不存在'",
       "parts=不存在" in _txt and "drawings=不存在" in _txt, _txt)
    ck("workflow_state.json 存在 → 有 mtime 与 age（不是'刚刚'）",
       "workflow_state=" in _txt and "age=" in _txt, _txt)
    ck("★ 步进/任务号来自状态文件（不是模型叙述）",
       "step=user_selected" in _txt and "task_id=T-A9-1" in _txt, _txt)
    ck("★ 状态行里【没有】'刚刚/刚才/刚刚更新'这类无法复算的措辞",
       not re.search(r"刚刚|刚才|马上|刚刚更新", _txt), _txt)
    ck("★ 状态行里的时间都是绝对时间戳（YYYY-MM-DD HH:MM:SS）",
       bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", _txt)), _txt)
    ck("facts.files 里每个文件都带 exists 字段",
       all("exists" in x for x in (sl["facts"].get("files") or [])), sl["facts"].get("files"))

    # ── 真的建一个 parts/ 目录：状态行必须跟着变（证明是读盘，不是模板）──
    os.makedirs(os.path.join(_tmp_task, "parts"), exist_ok=True)
    time.sleep(0.05)
    sl2 = wg.build_state_line()
    ck("★ 建出 parts/ 后，状态行里 parts 变为存在（读盘生效）",
       "parts=不存在" not in (sl2.get("text") or ""), sl2.get("text"))
finally:
    shutil.rmtree(_tmp_task, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("B. age 是可复算的（os.stat 的 mtime 差值），不是模型估的")
# ════════════════════════════════════════════════════════════════════
wipe_state()
write_state()
write_mode_state({})
_p = os.path.join(_ISO, "workflow_state.json")
sl = wg.build_state_line()
_wf = next((x for x in (sl["facts"]["files"] or [])
            if x.get("label") == "workflow_state.json"), None)
ck("facts 里能找到 workflow_state.json", bool(_wf), sl["facts"]["files"])
ck("★ mtime 与磁盘 os.path.getmtime 完全一致（可复算）",
   _wf and abs(float(_wf["mtime"]) - os.path.getmtime(_p)) < 1e-6,
   (_wf or {}).get("mtime"))
ck("★ age_sec = now - mtime（误差 < 2s）",
   _wf and abs((time.time() - float(_wf["mtime"])) - float(_wf["age_sec"])) < 2.0,
   (_wf or {}).get("age_sec"))
ck("状态行里的 age 与 facts 一致（同一个来源）",
   ("age=%ss" % _wf["age_sec"]) in (sl.get("text") or ""), sl.get("text"))


# ════════════════════════════════════════════════════════════════════
sec("C. 房间计数取自 mode_state 的枚举值（active/ended/failed）")
# ════════════════════════════════════════════════════════════════════
wipe_state()
write_state()
write_mode_state({
    "结构件": {"active": True},
    "传动件": {"ended_at": 1},
    "壳体": {"failed_at": 1},
})
sl = wg.build_state_line()
_txt = sl.get("text") or ""
ck("★ active=1 / ended=1 / failed=1（与 mode_state 一致）",
   "active=1 ended=1 failed=1" in _txt, _txt)
ck("facts.rooms 里列出具体房间名（可追溯，不是只有计数）",
   sl["facts"]["rooms"].get("active") == ["结构件"] and
   sl["facts"]["rooms"].get("failed") == ["壳体"], sl["facts"]["rooms"])

wipe_state()
write_state()
write_mode_state({})
ck("mode_state 为空/不存在 → 计数全 0（不崩、不猜）",
   "active=0 ended=0 failed=0" in (wg.build_state_line().get("text") or ""))


# ════════════════════════════════════════════════════════════════════
sec("D. CLI：state-line 子命令可跑，且两次调用只有 age 在变")
# ════════════════════════════════════════════════════════════════════
wipe_state()
write_state()

def _run_cli(argv):
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["workflow_gate.py"] + argv
    try:
        with contextlib.redirect_stdout(buf):
            wg._main_body()
    finally:
        sys.argv = old
    return json.loads(buf.getvalue() or "{}")

try:
    out = _run_cli(["state-line"])
except SystemExit as e:
    out = {"__exit__": e.code}
ck("★ CLI state-line 输出合法 JSON 且 ok=true",
   isinstance(out, dict) and out.get("ok") is True, out)
ck("CLI state-line 同样带 text/facts",
   bool(out.get("text")) and bool(out.get("facts")), list(out.keys()))
_out1 = out

time.sleep(0.2)
_out2 = _run_cli(["state-line"])
_t1 = re.sub(r"age=[\d.]+s", "age=Xs", _out1.get("text") or "")
_t2 = re.sub(r"age=[\d.]+s", "age=Xs", _out2.get("text") or "")
ck("★ 两次调用除 age 外完全一致（事实稳定，不是随机/模型生成）",
   _t1 == _t2, (_t1, _t2))

# help 里必须能查到这条命令（否则模型不知道要引用它）
_buf = io.StringIO()
_old = sys.argv
sys.argv = ["workflow_gate.py", "--help"]
try:
    with contextlib.redirect_stdout(_buf):
        wg._main_body()
except SystemExit:
    pass
finally:
    sys.argv = _old
_help = json.loads(_buf.getvalue() or "{}")
ck("★ CLI 帮助里列出 state-line（可发现性）",
   "state-line" in (_help.get("commands") or {}), list((_help.get("commands") or {}).keys()))


# ════════════════════════════════════════════════════════════════════
sec("E. gate-summary 结果里带同一行状态行（摘要可直接引用）")
# ════════════════════════════════════════════════════════════════════
wipe_state()
write_state()
write_mode_state({})
_gs = wg.cmd_gate_summary()
ck("gate-summary 仍返回 ok", _gs.get("ok") is True, _gs.get("error"))
_sl_gs = _gs.get("state_line") or {}
ck("★ gate-summary 内嵌 state_line.text", bool(_sl_gs.get("text")), _gs.keys())
ck("★ 内嵌状态行与 build_state_line 同源（step/room 计数一致）",
   ("step=user_selected" in (_sl_gs.get("text") or "")) and
   ("active=0" in (_sl_gs.get("text") or "")), _sl_gs.get("text"))
ck("state_line 里带使用规则（摘要必须原样引用）",
   "原样引用" in str(_sl_gs.get("rule") or ""), _sl_gs.get("rule"))

shutil.rmtree(_ISO, ignore_errors=True)

print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
