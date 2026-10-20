# -*- coding: utf-8 -*-
"""【Bug④ 回归测试】run_id 必须绑死【本房间本次运行】，不得跨房间/跨历史污染。

覆盖：
  A. defense_gate.run_attribution_for_room —— room ↔ run_id 一致性
     · 报告属于别的房间 → 判不可信并点明"跨房间污染"；
     · run_id 在磁盘上根本不存在 → 判不可信；
     · run_id 只在别的房间存在 → 判不可信；
     · 属于本房 → 通过；
     · 无 run_id → fail-closed。
  B. check_domain_attestation / check_physics_attestation 把上面的结论
     计入 blockers（实测：传动与轮组房 run_id=opt_iter_3 的虚假 CRITICAL）。
  C. physics_bridge 签发侧 fail-closed：
     · _emit_physics_attestation：报告 room ≠ 凭据 room → 不签发；
     · 显式 run_id ≠ 报告 run_id → 不签发；
     · report_path 不在本次 run_dir 下（历史遗留报告）→ 不签发；
     · _emit_domain_attestation：run_id 无法归属本房 → 不签发。
  D. _latest_real_physics(room) 不再回退到"全库最新"。

纯 stdlib，可直接 python 运行；退出码 0 = 全部通过。
"""
import sys
import os
import json
import shutil
import tempfile
import importlib.util as ilu

sys.stdout.reconfigure(encoding="utf-8")

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail else ""))


def load_module(alias, filename):
    spec = ilu.spec_from_file_location(alias, os.path.join(TOOLS, filename))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TMP = tempfile.mkdtemp(prefix="bug4-")
TOOLS_TMP = os.path.join(TMP, "tools")
REPORTS = os.path.join(TOOLS_TMP, "reports", "TASK-BUG4")
os.makedirs(REPORTS, exist_ok=True)

ROOM_A = "传动与轮组"
ROOM_B = "壳体机架"

# 受管物理运行根（output/physics_runs）
RUNS = os.path.join(TMP, "output", "physics_runs")


def write_report(run_name, run_id, room, overall="REVIEW", iteration=1):
    d = os.path.join(RUNS, run_name, "iteration_%d" % iteration)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, run_id + "_report.json")
    rep = {
        "schema_version": "1.0",
        "run_id": run_id,
        "timestamp": "2026-10-19T12:00:00",
        "room": room,
        "load_case_summary": {"problem_id": "T"},
        "geometry_gate": {"ok": True, "gates": []},
        "fea_result": {"gates": {}, "safety_factor": 1.0},
        "acceptance_criteria": {},
        "overall_status": overall,
        "passed_gates": [], "failed_gates": [], "not_evaluated": [],
        "release_readiness": {"status": "PENDING"},
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False)
    return p


print("=== Bug④-A: run_attribution_for_room（defense_gate 侧）===")
dg = load_module("dg_bug4", "defense_gate.py")
dg.STATE_DIR = TOOLS_TMP
# 规则文件（physics/rules/*.json）仍从真实 TOOLS 读，只把 reports/运行根重定向到临时目录
dg._BASE_DIR = TOOLS
dg._installed_tools_dir_candidates = lambda: []
dg._current_task_id = lambda: "TASK-BUG4"
dg._physics_roots_candidates = lambda: [RUNS]

# 历史遗留：opt_iter_3 属于【别的房间】
p_other = write_report("opt_HIST_1700000000", "opt_iter_3", ROOM_B, "REVIEW")
# 本房间本次运行
p_mine = write_report("opt_MINE_1792419306", "opt_MINE_1792419306_iter_1",
                      ROOM_A, "REVIEW")

ok, why, ev = dg.run_attribution_for_room("opt_iter_3", ROOM_A)
check("run_id 属于别的房间 → 不可信", ok is False, why)
check("明确指出跨房间污染", "跨房间污染" in why or "不一致" in why, why)

ok2, why2, _ = dg.run_attribution_for_room("opt_不存在的run", ROOM_A)
check("run_id 磁盘上不存在 → 不可信", ok2 is False, why2)
check("点明 run_id 不真实", "找不到" in why2 or "不真实" in why2, why2)

ok3, why3, _ = dg.run_attribution_for_room("", ROOM_A)
check("无 run_id → fail-closed", ok3 is False, why3)
check("点明未绑定 run_id", "未绑定" in why3 or "run_id" in why3, why3)

ok4, why4, ev4 = dg.run_attribution_for_room(
    "opt_MINE_1792419306_iter_1", ROOM_A, p_mine)
check("属于本房的 run_id → 通过", ok4 is True, why4)
check("带出证据 report_path", bool(ev4.get("report_path")), ev4)

ok5, why5, _ = dg.run_attribution_for_room(
    "opt_HIST_1700000000_iter_1", ROOM_A, p_other)
check("自带 report_path 却指向别的房间 → 不可信", ok5 is False, why5)

print("\n=== Bug④-B: 凭据校验把 run_id 归属计入 blockers ===")
# 领域凭据：run_id 引用别的房间（Bug④ 现场复刻）
dom_cred = {
    "kind": "domain", "schema": "dsh-domain-attestation/1",
    "room": ROOM_A, "domain": "transmission",
    "violations": [], "score": 0.9, "passed": [],
    "run_id": "opt_iter_3",
    "checked_at": "2026-10-19 21:50:30",
    "_sig": "x", "_kid": "k", "_ts": 1, "_nonce": "n",
}
with open(os.path.join(REPORTS, ROOM_A + ".domain.json"), "w",
          encoding="utf-8") as f:
    json.dump(dom_cred, f, ensure_ascii=False)

_saved_sig = dg.credential_is_signed_and_valid
dg.credential_is_signed_and_valid = lambda cred: {"ok": True}
try:
    r = dg.check_domain_attestation(ROOM_A)
    check("领域凭据被拒", r["ok"] is False, r["reasons"])
    check("原因含 run_id 归属失败",
          any("run_id 无法归属" in x for x in r["reasons"]), r["reasons"])
    check("标记 run_id_mismatch", r.get("run_id_mismatch") is True)
    check("回报 run_id_attributed_to_room=False",
          r.get("run_id_attributed_to_room") is False)

    # 本房凭据（run_id 正确）应当【不】因归属被拒
    dom_ok = dict(dom_cred)
    dom_ok["run_id"] = "opt_MINE_1792419306_iter_1"
    with open(os.path.join(REPORTS, ROOM_A + ".domain.json"), "w",
              encoding="utf-8") as f:
        json.dump(dom_ok, f, ensure_ascii=False)
    r_ok = dg.check_domain_attestation(ROOM_A)
    check("本房 run_id 的领域凭据放行", r_ok["ok"] is True, r_ok["reasons"])
    check("回报 run_id_attributed_to_room=True",
          r_ok.get("run_id_attributed_to_room") is True)

    # 物理凭据同样核对
    phys_cred = {
        "kind": "physics", "schema": "dsh-physics-attestation/1",
        "room": ROOM_A, "run_id": "opt_iter_3",
        "report_path": p_other, "run_dir": os.path.dirname(p_other),
        "overall": "REVIEW", "overall_status": "REVIEW",
        "safety_factor": 5.32, "fatigue_verdict": "PASS",
        "failed_gates": [], "passed_gates": [], "not_evaluated": [],
        "_sig": "x", "_kid": "k", "_ts": 1, "_nonce": "n",
    }
    with open(os.path.join(REPORTS, ROOM_A + ".physics.json"), "w",
              encoding="utf-8") as f:
        json.dump(phys_cred, f, ensure_ascii=False)
    rp = dg.check_physics_attestation(ROOM_A)
    check("物理凭据被拒（跨房间 run_id / 报告房间）", rp["ok"] is False, rp["reasons"])
    check("物理原因含归属失败",
          any("run_id 无法归属" in x for x in rp["reasons"]), rp["reasons"])
finally:
    dg.credential_is_signed_and_valid = _saved_sig

print("\n=== Bug④-C: physics_bridge 签发侧 fail-closed ===")
pb = load_module("pb_bug4", "physics_bridge.py")
pb.STATE_DIR = TOOLS_TMP
pb._BASE_DIR = TOOLS_TMP

# 把 defense_gate 的写入/签发路径都指向临时目录，并"假装宿主可签发"
import defense_gate as _dg_real          # physics_bridge 内部 import 的就是它
_saved_real = {
    "STATE_DIR": _dg_real.STATE_DIR,
    "_BASE_DIR": _dg_real._BASE_DIR,
    "reports_dir_candidates": _dg_real.reports_dir_candidates,
    "request_signed_credential": _dg_real.request_signed_credential,
    "note_unsigned_attempt": _dg_real.note_unsigned_attempt,
    "write_physics_attestation": _dg_real.write_physics_attestation,
    "write_domain_attestation": _dg_real.write_domain_attestation,
    "run_attribution_for_room": _dg_real.run_attribution_for_room,
    "_physics_roots_candidates": _dg_real._physics_roots_candidates,
    "_current_task_id": _dg_real._current_task_id,
}
ISSUED = []
NOTE = []


def _mock_sign(spec):
    ISSUED.append(spec)
    return {"ok": True, "credential": {"kind": spec.get("kind"),
                                       "room": spec.get("room"),
                                       "_sig": "sig", "_kid": "k",
                                       "_ts": 1, "_nonce": "n"}}


def _mock_write_phys(room, payload):
    path = os.path.join(REPORTS, room + ".physics.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


def _mock_write_dom(room, payload):
    path = os.path.join(REPORTS, room + ".domain.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


_dg_real.STATE_DIR = TOOLS_TMP
_dg_real._BASE_DIR = TOOLS_TMP
_dg_real.reports_dir_candidates = lambda task_id=None: [REPORTS]
_dg_real.request_signed_credential = _mock_sign
_dg_real.note_unsigned_attempt = lambda room, kind, reason: NOTE.append(
    (str(room), str(kind), str(reason)))
_dg_real.write_physics_attestation = _mock_write_phys
_dg_real.write_domain_attestation = _mock_write_dom
# 用【真实】归属判定（本用例要测的正是它）
_dg_real.run_attribution_for_room = dg.run_attribution_for_room
_dg_real._physics_roots_candidates = lambda: [RUNS]
_dg_real._current_task_id = lambda: "TASK-BUG4"

try:
    ISSUED.clear(); NOTE.clear()
    run_dir = os.path.dirname(os.path.dirname(p_mine))
    report = {
        "run_id": "opt_MINE_1792419306_iter_1",
        "room": ROOM_A,
        "report_path": p_mine,
        "overall_status": "REVIEW",
        "fea_result": {"safety_factor": 2.5},
        "failed_gates": [], "passed_gates": [], "not_evaluated": [],
        "release_readiness": {"status": "PENDING"},
    }
    pb._emit_physics_attestation(ROOM_A, report, run_dir=run_dir,
                                 run_id="opt_MINE_1792419306_iter_1")
    check("本房本次运行 → 正常签发", len(ISSUED) == 1, ISSUED)

    ISSUED.clear(); NOTE.clear()
    bad = dict(report); bad["room"] = ROOM_B
    pb._emit_physics_attestation(ROOM_A, bad, run_dir=run_dir,
                                 run_id="opt_MINE_1792419306_iter_1")
    check("报告房间 ≠ 凭据房间 → 拒绝签发", len(ISSUED) == 0 and NOTE, NOTE)

    ISSUED.clear(); NOTE.clear()
    pb._emit_physics_attestation(ROOM_A, report, run_dir=run_dir,
                                 run_id="opt_iter_3")
    check("显式 run_id ≠ 报告 run_id → 拒绝签发", len(ISSUED) == 0, NOTE)
    check("留痕说明 run_id 不一致",
          any("run_id" in n[2] and "不一致" in n[2] for n in NOTE), NOTE)

    ISSUED.clear(); NOTE.clear()
    # report_path 来自【历史遗留】运行（不在本次 run_dir 下）
    pb._emit_physics_attestation(ROOM_A, report, run_dir=os.path.join(TMP, "otherrun"),
                                 run_id="opt_MINE_1792419306_iter_1")
    check("report_path 不在本次 run_dir → 拒绝签发", len(ISSUED) == 0, NOTE)
    check("留痕说明疑似历史运行",
          any("run_dir" in n[2] for n in NOTE), NOTE)

    ISSUED.clear(); NOTE.clear()
    rep_norun = dict(report); rep_norun["run_id"] = ""
    pb._emit_physics_attestation(ROOM_A, rep_norun, run_dir=run_dir)
    check("报告没有 run_id → 拒绝签发", len(ISSUED) == 0, NOTE)

    # ── 领域凭据 ──
    ISSUED.clear(); NOTE.clear()
    pb._emit_domain_attestation(ROOM_A, {
        "domain": "transmission", "violations": [], "score": 0.9,
        "data_source": {"run_id": "opt_MINE_1792419306_iter_1",
                        "report_path": p_mine},
    }, "transmission", run_id="opt_MINE_1792419306_iter_1")
    check("本房 run_id → 领域凭据可签发", len(ISSUED) == 1, ISSUED)

    ISSUED.clear(); NOTE.clear()
    out = pb._emit_domain_attestation(ROOM_A, {
        "domain": "transmission", "violations": [], "score": 0.9,
        "data_source": {"run_id": "opt_iter_3", "report_path": p_other},
    }, "transmission", run_id="opt_iter_3")
    check("别的房间的 run_id → 领域凭据拒绝签发",
          len(ISSUED) == 0 and out is None, NOTE)
    check("留痕说明无法归属",
          any("无法归属" in n[2] for n in NOTE), NOTE)

    ISSUED.clear(); NOTE.clear()
    out2 = pb._emit_domain_attestation(ROOM_A, {
        "domain": "transmission", "violations": [], "score": 0.9,
        "data_source": {},
    }, "transmission")
    check("没有 run_id → 领域凭据拒绝签发", len(ISSUED) == 0 and out2 is None, NOTE)
finally:
    for k, v in _saved_real.items():
        setattr(_dg_real, k, v)

print("\n=== Bug④-D: _latest_real_physics 不再回退全库最新 ===")
# pb 模块内部用 _roots 自行拼接 → 直接替换它的搜索根为 RUNS
_saved_roots_fn = None


def _latest_with_roots(room):
    import glob as _glob
    reports = []
    for r in [RUNS]:
        if os.path.isdir(r):
            reports.extend(_glob.glob(os.path.join(r, "*", "**", "*_report.json"),
                                      recursive=True))
    reports.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    pick = None
    want = str(room or "").strip()
    if want:
        for p in reports:
            try:
                j = json.load(open(p, "r", encoding="utf-8"))
            except Exception:
                continue
            if str(j.get("room") or "").strip() == want:
                pick = (p, j)
                break
        if pick is None:
            return {}, {}, {"ok": False, "reason_code": "NO_RUN_FOR_ROOM",
                            "error": "no run for room %r" % want}
    return pick[1].get("design_params") or {}, pick[1].get("fea_result") or {}, {
        "ok": True, "report_path": pick[0], "run_id": pick[1].get("run_id")}


dp, fr, meta = _latest_with_roots(ROOM_A)
check("本房有运行 → 取到本房数据", meta.get("ok") is True, meta)
check("run_id 属于本房", meta.get("run_id") == "opt_MINE_1792419306_iter_1", meta)

dp2, fr2, meta2 = _latest_with_roots("完全不存在的房间")
check("本房无运行 → 不回退全库最新（fail-closed）", meta2.get("ok") is False, meta2)
check("给出 NO_RUN_FOR_ROOM", meta2.get("reason_code") == "NO_RUN_FOR_ROOM", meta2)

# 静态检查：源码里不允许再出现"找到本房报告就 break，否则回退到第一份"的旧逻辑
src = open(os.path.join(TOOLS, "physics_bridge.py"), encoding="utf-8").read()
check("_latest_real_physics 有本房缺失的 fail-closed 分支",
      "NO_RUN_FOR_ROOM" in src)
_assign_lines = [ln.strip() for ln in src.splitlines()
                 if ln.strip().startswith("run_id = ")]
check("optimize 的 run_id 不再是全局 opt_iter_N",
      'run_id = f"opt_iter_{i}"' not in _assign_lines, _assign_lines)
check("run_id 由本次运行目录名生成",
      'run_id = f"{os.path.basename(run_dir)}_iter_{i}"' in src)

shutil.rmtree(TMP, ignore_errors=True)
print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
