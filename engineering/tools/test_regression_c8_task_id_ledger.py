# -*- coding: utf-8 -*-
"""【C8 回归测试】part_registry 默认指向旧任务台账 → 一致性假阳性

现场（缺陷清单 C8 / P2）：
    同一台机器换任务后，part_registry 默认路径仍然是
        <tools>/load_cases/part_registry.json
    那是【上一轮任务】留下的台账。于是：
      · check_adjacency_consistency / list / get 读到旧零件、旧邻接
        → 报出"当前任务相邻零件尺寸不一致"（假阳性）；
      · add_part 把新任务的零件写进旧台账，而台账 task_id 一栏还留着旧任务号
        （_set_task_id 见到"已有戳"就直接 return）→ 两块任务的数据混在一起，
        之后任何按 task_id 的比对都永远对不上。

修复：读/写台账时校验台账 task_id 与当前 workflow_state.json 的 task_id：
      · 不一致 → 写路径【明确拒绝】（TASK_ID_MISMATCH，列出两边 task_id 与
        两个文件路径）；只读路径不拦截，但结果里带 task_id_mismatch 警告；
      · _set_task_id 不再"有戳就不管"，旧任务的戳不允许被新任务静默继承。

运行：python test_regression_c8_task_id_ledger.py
退出码 0 = 通过
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 隔离：状态目录 + 台账路径都指向临时目录，绝不碰生产台账 ─────────────
_ISO = tempfile.mkdtemp(prefix="c8_state_")
os.environ["DSH_STATE_DIR"] = _ISO
os.environ.pop("DSH_PART_REGISTRY", None)

import part_registry as pr  # noqa: E402

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


R_ADD = "首次登记车架底板，承载整车与电池，材料 45#钢"


def _mk_task(task_id, extra_parts=None):
    """造一个独立"任务目录"：workflow_state.json + 台账。

    目录结构与真实运行一致：<work_dir>/workflow_state.json +
    <work_dir>/load_cases/part_registry.json（registry_path 的口径）。
    """
    d = tempfile.mkdtemp(prefix="c8task_")
    with io.open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
        json.dump({"task_id": task_id, "step": "analysis"}, f, ensure_ascii=False)
    os.makedirs(os.path.join(d, "load_cases"), exist_ok=True)
    return d


def _ledger(d):
    return os.path.join(d, "load_cases", pr.REGISTRY_FILENAME)


def _seed(d, task_id, parts):
    """直接铺一份"旧任务台账"（模拟上轮任务遗留物）。"""
    reg = pr._fresh_registry()
    reg["task_id"] = task_id
    reg["task_id_source"] = os.path.join(d, "workflow_state.json")
    for name, rec in (parts or {}).items():
        reg["parts"][name] = rec
    with io.open(_ledger(d), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    return reg


# ════════════════════════════════════════════════════════════════════
sec("A. task_id 一致性的判据（_task_id_check / _current_task_id）")
# ════════════════════════════════════════════════════════════════════
d_same = _mk_task("T-2026-A")
try:
    ck("_current_task_id 从 workflow_state.json 取到当前任务号",
       pr._current_task_id(d_same)[0] == "T-2026-A", pr._current_task_id(d_same))
    ck("台账无 task_id（新台账）→ 不拦截", pr._task_id_check({"task_id": None}, d_same) is None)
    ck("台账 task_id 与当前一致 → 不拦截",
       pr._task_id_check({"task_id": "T-2026-A"}, d_same) is None)
    _g = pr._task_id_check({"task_id": "T-OLD-999"}, d_same)
    ck("★ 台账 task_id 与当前不一致 → 返回 TASK_ID_MISMATCH",
       isinstance(_g, dict) and _g.get("gate") == "TASK_ID_MISMATCH", _g)
    ck("★ 判据里列出【两边】task_id",
       _g.get("ledger_task_id") == "T-OLD-999" and
       _g.get("current_task_id") == "T-2026-A", _g)
    ck("★ 判据里列出【台账文件】路径",
       str(_g.get("registry_file")).endswith(pr.REGISTRY_FILENAME),
       _g.get("registry_file"))
    ck("★ 判据里列出【workflow_state】路径",
       str(_g.get("workflow_state_file")).endswith("workflow_state.json"),
       _g.get("workflow_state_file"))
    ck("判据里给出可执行的修法（不是让人直接改 task_id 蒙混）",
       "DSH_PART_REGISTRY" in str(_g.get("hint") or ""), _g.get("hint"))
finally:
    shutil.rmtree(d_same, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("B. 读路径：_load_registry 默认 fail-closed（strict_task_id=True）")
# ════════════════════════════════════════════════════════════════════
d_old = _mk_task("T-2026-NEW")
try:
    _seed(d_old, "T-2026-OLD", {"DSH_旧车架": {
        "name": "DSH_旧车架", "room": "结构件", "rev": 1,
        "dims": {"length_mm": 100.0}, "adjacent": []}})
    _data, _path, _err = pr._load_registry(d_old)
    ck("★ 旧任务台账默认读取 → 返回明确错误（不再静默通过）", bool(_err), _err)
    ck("★ 错误文案里写明 TASK_ID_MISMATCH 语义（台账≠当前）",
       "T-2026-OLD" in str(_err) and "T-2026-NEW" in str(_err), _err)
    _data2, _path2, _err2 = pr._load_registry(d_old, strict_task_id=False)
    ck("只读模式（strict_task_id=False）→ 不拦错误", not _err2, _err2)
    _warn = (_data2 or {}).get("task_id_mismatch")
    ck("★ 只读模式必须回显 task_id_mismatch 警告（不许假装是当前数据）",
       isinstance(_warn, dict) and _warn.get("gate") == "TASK_ID_MISMATCH", _warn)
    ck("  警告里带两边 task_id",
       _warn.get("ledger_task_id") == "T-2026-OLD" and
       _warn.get("current_task_id") == "T-2026-NEW", _warn)
finally:
    shutil.rmtree(d_old, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("C. 写路径：新任务的数据绝不能被写进旧任务台账")
# ════════════════════════════════════════════════════════════════════
d_w = _mk_task("T-2026-NEW")
try:
    before = _seed(d_w, "T-2026-OLD", {"DSH_旧车架": {
        "name": "DSH_旧车架", "room": "结构件", "rev": 1,
        "dims": {"length_mm": 100.0}, "adjacent": []}})
    r = pr.add_part({"name": "DSH_新立柱", "room": "结构件", "file": "C:/x/new.SLDPRT",
                     "dims": {"length_mm": 200.0}},
                    work_dir=d_w, reason=R_ADD)
    ck("★ add_part 被拒绝（不是静默继承旧任务号）", not r.get("ok"), r)
    ck("★ 返回 gate=TASK_ID_MISMATCH", r.get("gate") == "TASK_ID_MISMATCH", r)
    ck("  返回里列出两边 task_id",
       r.get("ledger_task_id") == "T-2026-OLD" and
       r.get("current_task_id") == "T-2026-NEW", r)
    ck("  返回里列出两个文件路径",
       bool(r.get("registry_file")) and bool(r.get("workflow_state_file")), r)
    with io.open(_ledger(d_w), encoding="utf-8") as f:
        after = json.load(f)
    ck("★★ 磁盘上的旧台账【一个字节都没被改】（拒绝落盘）",
       after.get("parts") == before.get("parts"), after.get("parts"))
    ck("★ 旧台账的 task_id 没有被改写",
       after.get("task_id") == "T-2026-OLD", after.get("task_id"))

    # update / link / remove 同属写路径，必须一律拒绝
    for label, fn in (
        ("update_part", lambda: pr.update_part("DSH_旧车架", {"material": "45#钢"},
                                               work_dir=d_w, reason=R_ADD)),
        ("link_adjacent", lambda: pr.link_adjacent("DSH_旧车架", "DSH_新立柱",
                                                   work_dir=d_w, reason=R_ADD)),
        ("remove_part", lambda: pr.remove_part("DSH_旧车架", work_dir=d_w, reason=R_ADD)),
    ):
        _r = fn()
        ck("★ %s 同样被 TASK_ID_MISMATCH 拦下" % label,
           (not _r.get("ok")) and _r.get("gate") == "TASK_ID_MISMATCH", _r)
finally:
    shutil.rmtree(d_w, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("D. 一致性检查不再报假阳性（旧台账 → 明说'这不是当前任务'）")
# ════════════════════════════════════════════════════════════════════
d_c = _mk_task("T-2026-NEW")
try:
    _seed(d_c, "T-2026-OLD", {
        "A": {"name": "A", "room": "r", "rev": 1, "adjacent": ["B"],
              "interfaces": [{"id": "J1", "type": "shaft", "nominal_mm": 25.0}]},
        # 故意造一对尺寸对不上的旧零件（这正是"假阳性"的来源）
        "B": {"name": "B", "room": "r", "rev": 1, "adjacent": ["A"],
              "interfaces": [{"id": "J1", "type": "hole", "nominal_mm": 30.0}]},
    })
    chk = pr.check_adjacency_consistency(work_dir=d_c)
    ck("★ 只读检查不因为任务号不一致而直接崩",
       isinstance(chk, dict) and chk.get("evaluated") is not None, chk.get("error"))
    ck("★★ 结论里必须带 task_id_mismatch（否则会被当成当前任务的结论）",
       isinstance(chk.get("task_id_mismatch"), dict) and
       chk["task_id_mismatch"].get("gate") == "TASK_ID_MISMATCH",
       chk.get("task_id_mismatch"))
    ck("  结论里同时带出台账 task_id（能追到是哪一轮的数据）",
       chk.get("task_id") == "T-2026-OLD", chk.get("task_id"))
    ck("  警告文案明确提示'可能来自上一轮任务'",
       "上一轮" in str((chk.get("task_id_mismatch") or {}).get("message") or ""),
       chk.get("task_id_mismatch"))

    lst = pr.list_parts(work_dir=d_c)
    ck("★ list_parts 同样回显 task_id_mismatch",
       isinstance(lst.get("task_id_mismatch"), dict), lst.get("task_id_mismatch"))
    one = pr.get_part("A", work_dir=d_c)
    ck("★ get_part 同样回显 task_id_mismatch",
       isinstance(one.get("task_id_mismatch"), dict), one.get("task_id_mismatch"))
finally:
    shutil.rmtree(d_c, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("E. 正常路径不被误伤：同一任务续写照常通过")
# ════════════════════════════════════════════════════════════════════
d_ok = _mk_task("T-2026-SAME")
try:
    r1 = pr.add_part({"name": "DSH_车架底板", "room": "结构件", "file": "C:/x/a.SLDPRT",
                      "dims": {"length_mm": 120.0}}, work_dir=d_ok, reason=R_ADD)
    ck("同一任务首次登记 → 成功", r1.get("ok") is True, r1)
    with io.open(_ledger(d_ok), encoding="utf-8") as f:
        _d = json.load(f)
    ck("★ 新台账被自动打上当前任务的 task_id",
       _d.get("task_id") == "T-2026-SAME", _d.get("task_id"))
    r2 = pr.add_part({"name": "DSH_立柱", "room": "结构件", "file": "C:/x/b.SLDPRT",
                      "dims": {"length_mm": 200.0}}, work_dir=d_ok, reason=R_ADD)
    ck("同一任务继续登记 → 仍然成功（任务号一致不拦截）", r2.get("ok") is True, r2)
    r3 = pr.update_part("DSH_立柱", {"material": "45#钢"}, work_dir=d_ok, reason=R_ADD)
    ck("同一任务 update → 成功", r3.get("ok") is True, r3)
    ck("正常台账不产生 task_id_mismatch 噪音",
       not (pr.list_parts(work_dir=d_ok).get("task_id_mismatch")))
    ck("★ _set_task_id 对一致台账返回 None（不是错误）",
       pr._set_task_id({"task_id": "T-2026-SAME"}, d_ok) is None)
    _mm = pr._set_task_id({"task_id": "T-2026-OTHER"}, d_ok)
    ck("★ _set_task_id 对【旧任务的戳】返回错误（不再静默继承）",
       isinstance(_mm, dict) and _mm.get("gate") == "TASK_ID_MISMATCH", _mm)
finally:
    shutil.rmtree(d_ok, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F. 没有权威 workflow_state 时不臆造结论（手工跑不误伤）")
# ════════════════════════════════════════════════════════════════════
d_nostate = tempfile.mkdtemp(prefix="c8nostate_")
os.makedirs(os.path.join(d_nostate, "load_cases"), exist_ok=True)
try:
    with io.open(_ledger(d_nostate), "w", encoding="utf-8") as f:
        json.dump({"schema": pr.SCHEMA, "task_id": "T-ANY-1",
                   "parts": {}, "revisions": [], "unresolved": []},
                  f, ensure_ascii=False)
    # 注：_state_candidates 还有 <tools>/workflow_state.json 这条兜底（与
    #   _read_task_id 同口径），因此这里显式把它置空，模拟"确实拿不到权威状态"。
    _real_cands = pr._state_candidates
    try:
        pr._state_candidates = lambda work_dir=None: []
        ck("取不到当前任务号 → _current_task_id 返回 (None, None)",
           pr._current_task_id(d_nostate) == (None, None), pr._current_task_id(d_nostate))
        ck("★ 取不到当前任务号 → _task_id_check 放行（不瞎判）",
           pr._task_id_check({"task_id": "T-ANY-1"}, d_nostate) is None)
        ck("  此时读台账也不报错（手工在 shell 里跑不被误伤）",
           pr._load_registry(d_nostate)[2] is None, pr._load_registry(d_nostate)[2])
    finally:
        pr._state_candidates = _real_cands
finally:
    shutil.rmtree(d_nostate, ignore_errors=True)

shutil.rmtree(_ISO, ignore_errors=True)

print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
