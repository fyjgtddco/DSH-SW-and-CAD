#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：2026-11-10 复检报告（_repro_20261110）四项新问题修复验收

对应《回归复检报告_20261110.md》：
  N1  save 失败改道把【只读好件】移进 parts_rejects → 只挪本次新建的失败产物
  N2  run 产物核对路径解析错误（变量路径猜成 scripts\\）→ 静态+磁盘校正
  N3  并发台账把"持锁+排队"算 overlap → 只统计实际 SW 命令执行区间
  N4  self-check 门禁读不到 workflow_state.work_dir → 与门禁读根同源
  （P5 收口另见 test_regression_room_gates_p5.py F10~F14）

本测试只验判定逻辑与接线，不拉 SW：
  A. N4：mode_state 无 work_dir 时，门禁也能在 workflow_state.work_dir\\reports
     找到 self_check.json（放行/拦截两向）
  B. N3：room-start 不再开构建窗口；窗口只在实际命令执行开合（源码级 +
     功能级：两段不相交的窗口不产生 overlap）
  C. N2：_extract_output_paths 解析内联 os.path.join(变量,...)；
     _verify_run_artifacts 用磁盘新增件校正猜错的目录（present/missing 两向）
  D. N1：reject_part 不再挪走已 PASS/已冻结/带凭据的好件；坏件照常进 rejects

运行: python test_regression_20261110_repro_fixes.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        FAILS.append("%s  %s" % (name, detail))
        print("  [FAIL] %s  %s" % (name, detail))


def section(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


def _rejects_have_skip(p):
    try:
        if not os.path.exists(p):
            return False
        with open(p, "r", encoding="utf-8") as f:
            lst = json.load(f) or []
        return any(x.get("skipped_good") for x in lst)
    except Exception:
        return False


def main():
    tmp = tempfile.mkdtemp(prefix="repro20261110_")
    old_state = os.environ.get("DSH_STATE_DIR")
    old_cwd = os.getcwd()
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        os.chdir(tmp)
        import mode_gate as mg
        import sw_bridge as sb
        import task_workspace as tw
        import part_registry as pr

        # ---------------- A. N4：work_dir 读 workflow_state ----------------
        section("A. N4 self-check 门禁的 work_dir 与门禁读根同源")
        work = os.path.join(tmp, "任务")
        os.makedirs(os.path.join(work, "reports"), exist_ok=True)
        os.makedirs(os.path.join(work, "parts"), exist_ok=True)
        part = os.path.join(work, "parts", "DSH_方块.SLDPRT")
        with open(part, "wb") as f:
            f.write(b"x")
        # mode_state 无 work_dir（复检现场的真实形态）
        with open(os.path.join(tmp, "mode_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"rooms": {"结构件": {"active": True,
                                          "type": "structural"}}},
                      f, ensure_ascii=False)
        # work_dir 真正落在 workflow_state.json
        # 刻意不写 task_id：_registry_file() 在无 task_id 时读
        #   artifacts_registry.json（本测试写的就是这个文件）
        with open(os.path.join(tmp, "workflow_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"work_dir": work}, f, ensure_ascii=False)
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"结构件": [{"path": part}]}, f, ensure_ascii=False)

        # 没有自检记录 -> 拦
        r = mg._selfcheck_gate_for_room("结构件")
        check("A1 缺自检记录 -> 拦", isinstance(r, dict), r)

        # 自检落【任务目录】reports/ -> 门禁应能找到并放行
        sc = os.path.join(work, "reports", "DSH_方块.self_check.json")
        with open(sc, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "verdict": "PASS", "failed": [],
                       "skipped": []}, f, ensure_ascii=False)
        r = mg._selfcheck_gate_for_room("结构件")
        check("A2 work_dir(reports) 里的 PASS 记录被找到 -> 放行",
              r is None, r)

        # pose 门同样从 workflow_state 找到 up_contract.json（N4 同口径）
        os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
        with open(os.path.join(work, "scripts", "up_contract.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"world_up": "Z", "world_up_sign": 1}, f,
                      ensure_ascii=False)
        with open(os.path.join(tmp, "mode_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"rooms": {"总装": {"active": True,
                                          "type": "assembly"}}},
                      f, ensure_ascii=False)
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"总装": [{"path": part}]}, f, ensure_ascii=False)
        import part_pose as pp
        pp.write_pr_record(part, up_axis="Z", up_sign=1, main_axis="Z",
                           base_plane="Top Plane")
        check("A3 pose 门经 workflow_state 找到契约且一致 -> 放行",
              mg._pose_gate_for_room("总装") is None,
              mg._pose_gate_for_room("总装"))

        # ---------------- B. N3：build 窗口语义 ----------------
        section("B. N3 build_overlap_count 只算实际 SW 命令执行区间")
        # B1 源码级：room-start 不再开窗；sw_bridge main 取锁后开、finally 关
        _msrc = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        _m = re.search(r"def cmd_room_start\(.*?\n(?=def )", _msrc, re.S)
        _body = _m.group(0) if _m else ""
        check("B1 room-start 不再调用 _record_build_start",
              "_record_build_start(" not in _body,
              "仍在 room-start 开窗 = 排队等待也会算 overlap")
        _bsrc = open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
        check("B2 sw_bridge 取锁后调用 _record_build_start",
              "_mg_bw._record_build_start(" in _bsrc)
        check("B3 sw_bridge finally 调用 _record_build_end",
              "_mg_bw._record_build_end(" in _bsrc)
        check("B4 窗口只在非 _NO_SW_CMDS 命令开（只读命令不开窗）",
              "cmd not in _NO_SW_CMDS" in _bsrc)

        # B5 功能级：两段不相交的窗口 -> 无 overlap
        mg._save_build_windows([])
        mg._record_build_start("甲")
        time.sleep(0.05)
        mg._record_build_end("甲")
        mg._record_build_start("乙")
        time.sleep(0.05)
        mg._record_build_end("乙")
        check("B5 串行两段窗口 -> overlap=0",
              mg._build_overlaps() == [], mg._build_overlaps())

        # B6 功能级：同一时刻两段窗口 -> 恰好 1 对重叠
        mg._save_build_windows([])
        mg._record_build_start("甲")
        mg._record_build_start("乙")
        mg._record_build_end("甲")
        mg._record_build_end("乙")
        _ov = mg._build_overlaps()
        check("B6 重叠两段窗口 -> 1 对", len(_ov) == 1, _ov)
        check("B6b 重叠对带 overlap_sec", _ov and "overlap_sec" in _ov[0], _ov)

        # B7 只有 room-start / 没有实际命令 -> 窗口为空
        mg._save_build_windows([])
        mg._record_build_end("无人房间")   # 无开窗时闭合是 no-op，不凭空造窗
        check("B7 未开窗时 _record_build_end 不造窗口",
              mg._load_build_windows() == [], mg._load_build_windows())

        # ---------------- C. N2：路径解析 ----------------
        section("C. N2 变量路径 / 磁盘校正")
        _scripts = os.path.join(tmp, "scripts")
        os.makedirs(_scripts, exist_ok=True)
        os.makedirs(os.path.join(tmp, "parts"), exist_ok=True)

        # C1 内联 os.path.join(E, "parts", "x.SLDPRT")，E 是变量
        sp1 = os.path.join(_scripts, "probe_inline.py")
        _E = tmp.replace("\\", "/")
        with open(sp1, "w", encoding="utf-8") as f:
            f.write("E = r\"%s\"\n" % _E)
            f.write("m.save(os.path.join(E, \"parts\", \"DSH_回归A方块.SLDPRT\"))\n")
        _p1 = sb._extract_output_paths(sp1)
        _want1 = os.path.join(tmp, "parts", "DSH_回归A方块.SLDPRT")
        check("C1 内联变量路径解析到 parts",
              any(_norm(x) == _norm(_want1) for x in _p1), _p1)

        # C2 旧反例仍绿：save(t)，t = os.path.join(od, "x.SLDPRT")
        sp2 = os.path.join(_scripts, "probe_var.py")
        with open(sp2, "w", encoding="utf-8") as f:
            f.write("od = r\"C:/out\"\n")
            f.write("t = os.path.join(od, \"DSH_b.SLDPRT\")\n")
            f.write("m.save(t)\n")
        _p2 = sb._extract_output_paths(sp2)
        check("C2 二级变量回溯仍可用",
              any("DSH_b" in x for x in _p2), _p2)

        # C3 磁盘校正：静态提取猜错目录（纯函数拼路径），磁盘新增件校正
        sp3 = os.path.join(_scripts, "probe_unresolvable.py")
        with open(sp3, "w", encoding="utf-8") as f:
            f.write("m.save(os.path.join(os.path.dirname("
                    "os.path.abspath(__file__)), \"DSH_磁盘件.SLDPRT\"))\n")
        _p3 = sb._extract_output_paths(sp3)
        check("C3a 静态提取对纯函数路径只能给相对拼接（先猜错）",
              any("DSH_磁盘件" in x for x in _p3), _p3)
        # 造一个运行窗口内新增的磁盘件（脚本目录上级的 parts\\）
        _real3 = os.path.join(tmp, "parts", "DSH_磁盘件.SLDPRT")
        with open(_real3, "wb") as f:
            f.write(b"part-content")
        _res = {"ok": True, "_started_at": time.time() - 5}
        _art = sb._verify_run_artifacts(sp3, _res)
        check("C3b 磁盘新增件校正后 present 命中真实路径",
              any(_norm(x) == _norm(_real3) for x in _art.get("present", [])),
              _art)
        check("C3c 校正后 missing 为空（不再假 MISSING_ARTIFACT）",
              _art.get("missing") == [], _art.get("missing"))
        check("C3d path_corrected 留痕",
              any("DSH_磁盘件" in str(c.get("expected")) or
                  "DSH_磁盘件" in str(c.get("actual"))
                  for c in _art.get("path_corrected", [])),
              _art.get("path_corrected"))

        # C4 真缺件仍被拦（校正不掩盖真实失败）
        sp4 = os.path.join(_scripts, "probe_missing.py")
        with open(sp4, "w", encoding="utf-8") as f:
            f.write("m.save(r\"%s/parts/DSH_不存在件.SLDPRT\")\n"
                    % _E)
        _res4 = {"ok": True, "_started_at": time.time() - 5}
        _art4 = sb._verify_run_artifacts(sp4, _res4)
        check("C4 真缺件仍 missing（不因校正放水）",
              any("DSH_不存在件" in str(x) for x in _art4.get("missing", [])),
              _art4)

        # ---------------- D. N1：reject_part 保护好件 ----------------
        section("D. N1 reject_part 只挪失败产物，不动好件")
        wd = os.path.join(tmp, "任务2")
        os.makedirs(os.path.join(wd, "parts"), exist_ok=True)
        os.makedirs(os.path.join(wd, "reports"), exist_ok=True)

        # D1 已 PASS 自检的好件 -> 拒绝挪走
        good = os.path.join(wd, "parts", "DSH_好件.SLDPRT")
        with open(good, "wb") as f:
            f.write(b"good")
        with open(os.path.join(wd, "reports", "DSH_好件.self_check.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"ok": True, "verdict": "PASS", "failed": [],
                       "skipped": []}, f, ensure_ascii=False)
        r = tw.reject_part(wd, good, reason="save 失败(冻结只读)")
        check("D1 好件(PASS) -> skipped_good 且文件仍在",
              r.get("skipped_good") is True and os.path.exists(good), r)
        check("D1b rejected.json 留痕 skipped_good",
              _rejects_have_skip(os.path.join(wd, "parts_rejects",
                                              "rejected.json")))

        # D2 已冻结的好件 -> 拒绝挪走
        frozen = os.path.join(wd, "parts", "DSH_冻结件.SLDPRT")
        with open(frozen, "wb") as f:
            f.write(b"frozen")
        with open(os.path.join(tmp, "mode_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"rooms": {"结构件": {
                "active": False,
                "frozen": {"files": [{"path": frozen,
                                      "sha256": "x"}]}}}},
                f, ensure_ascii=False)
        r = tw.reject_part(wd, frozen, reason="save 失败")
        check("D2 冻结件 -> skipped_good 且文件仍在",
              r.get("skipped_good") is True and os.path.exists(frozen), r)

        # D3 带材料凭据的好件 -> 拒绝挪走
        cred = os.path.join(wd, "parts", "DSH_凭据件.SLDPRT")
        with open(cred, "wb") as f:
            f.write(b"cred")
        with open(cred + ".material.json", "w", encoding="utf-8") as f:
            json.dump({"material": "Q235"}, f, ensure_ascii=False)
        r = tw.reject_part(wd, cred, reason="save 失败")
        check("D3 凭据件 -> skipped_good 且文件仍在",
              r.get("skipped_good") is True and os.path.exists(cred), r)

        # D4 真正的失败件（无任何凭据）-> 照常进 rejects
        bad = os.path.join(wd, "parts", "DSH_坏件.SLDPRT")
        with open(bad, "wb") as f:
            f.write(b"bad")
        r = tw.reject_part(wd, bad, reason="建模失败")
        check("D4 失败件照常移入 rejects",
              r.get("ok") is True and bool(r.get("to")) and
              not os.path.exists(bad), r)

        # ---------------- E. R1/R2：run 自动登记字段 + 任务级台账 ----------------
        section("E. R1/R2 自动登记：name 字段 + 台账落到本任务")
        _taskwd = os.path.join(tmp, "任务R")
        os.makedirs(os.path.join(_taskwd, "parts"), exist_ok=True)
        with open(os.path.join(tmp, "workflow_state.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"task_id": "T-R12", "work_dir": _taskwd}, f,
                      ensure_ascii=False)
        _partE = os.path.join(_taskwd, "parts", "DSH_甲零件.SLDPRT")
        with open(_partE, "wb") as f:
            f.write(b"p")
        # R2：无参数 registry_path 必须指到本任务（而不是 tools/load_cases）
        _rp = pr.registry_path()
        check("E1 registry_path() 指向本任务目录",
              _norm(_rp) == _norm(os.path.join(_taskwd, "load_cases",
                                               pr.REGISTRY_FILENAME)), _rp)
        # R1：自动登记必须成功（name 字段不再缺失）
        _reg = sb._auto_register_parts([_partE], room="结构件",
                                        script_path="probe.py")
        check("E2 自动登记没有 failed（name 字段已对齐）",
              not (_reg.get("failed") or []), _reg)
        check("E3 自动登记 registered 非空", bool(_reg.get("registered")), _reg)
        _led = json.load(open(os.path.join(_taskwd, "load_cases",
                                           pr.REGISTRY_FILENAME),
                              encoding="utf-8"))
        check("E4 台账写入本任务文件且带 task_id",
              _led.get("task_id") == "T-R12" and bool(_led.get("parts")), _led.get("task_id"))
        check("E5 台账里有甲零件", any("甲零件" in k for k in (_led.get("parts") or {})),
              list((_led.get("parts") or {}).keys()))

        # ---------------- F. R7：HERE 变量路径 → 磁盘校正 ----------------
        section("F. R7 静态猜错目录后按磁盘实际落盘校正")
        _t7 = os.path.join(tmp, "任务G")
        os.makedirs(os.path.join(_t7, "intermediate"), exist_ok=True)
        os.makedirs(os.path.join(_t7, "parts"), exist_ok=True)
        _sp7 = os.path.join(_t7, "intermediate", "probe_here.py")
        with open(_sp7, "w", encoding="utf-8") as f:
            f.write("HERE = os.path.dirname(os.path.abspath(__file__))" + chr(10))
            f.write('m.save(os.path.join(os.path.dirname(HERE), "parts", "DSH_大齿轮.SLDPRT"))' + chr(10))
        _real7 = os.path.join(_t7, "parts", "DSH_大齿轮.SLDPRT")
        with open(_real7, "wb") as f:
            f.write(b"gear")
        _art7 = sb._verify_run_artifacts(_sp7, {"ok": True, "_started_at": time.time() - 5})
        check("F1 校正后 present 命中 <任务>\\parts\\ 真实路径",
              any(_norm(x) == _norm(_real7) for x in _art7.get("present", [])), _art7)
        check("F2 校正后不再假报 missing", _art7.get("missing") == [],
              _art7.get("missing"))

        # ---------------- G. R6：build_overlap 按 task_id 过滤 ----------------
        section("G. R6 并发台账只统计本任务")
        mg._save_build_windows([
            {"room": "回归室A", "start_ts": time.time() - 100,
             "end_ts": time.time() - 90, "task_id": "T-OLD"},
            {"room": "回归室B", "start_ts": time.time() - 95,
             "end_ts": time.time() - 85, "task_id": "T-OLD"},
        ])
        check("G1 上一任务的 1 对重叠不计入本任务", mg._build_overlaps() == [],
              mg._build_overlaps())
        mg._save_build_windows([])
        mg._record_build_start("甲")
        time.sleep(0.05)
        mg._record_build_end("甲")
        check("G2 本任务窗口已带 task_id",
              all(w.get("task_id") == "T-R12" for w in mg._load_build_windows()),
              mg._load_build_windows())

        # ---------------- H. R5：上报终态即释放 SW 占位 ----------------
        section("H. R5 完工上报释放 SW 队列占位")
        _st5 = {"mode": "2", "rooms": {"甲": {"active": True}},
                "sw_lock": {"owner": "甲", "queue": ["乙"], "acquired_at": time.time()}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(_st5, f, ensure_ascii=False)
        _rel5 = mg._release_sw_after_report("甲", "done")
        check("H1 持锁房间 done → released", (_rel5 or {}).get("released") is True, _rel5)
        _after5 = json.load(open(os.path.join(tmp, "mode_state.json"), encoding="utf-8"))
        check("H2 释放后 owner 不再是甲",
              (_after5.get("sw_lock") or {}).get("owner") != "甲", _after5.get("sw_lock"))
        _st5b = {"mode": "2", "rooms": {"甲": {"active": True}, "乙": {"active": True}},
                 "sw_lock": {"owner": "乙", "queue": ["甲"], "acquired_at": time.time()}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(_st5b, f, ensure_ascii=False)
        _rel5b = mg._release_sw_after_report("甲", "done")
        check("H3 排队房间 done → 从队列移除",
              (_rel5b or {}).get("queue_removed") is True, _rel5b)
        check("H4 非终态上报不释放",
              mg._release_sw_after_report("甲", "modeling") is None)

        # ---------------- I. R8：ended_at 粘滞显式标注 ----------------
        section("I. R8 终态后又上报 → ended_at_stale")
        _now8 = time.time()
        _st8 = {"rooms": {}, "latest_report": {"stage": "validating", "ts": _now8}}
        _info8 = {"active": True, "ended_at": _now8 - 60, "type": "structural"}
        _row8 = mg._board_entry("总理房", _info8, {}, st=_st8)
        check("I1 state 仍为 done（终态不篡改）", _row8.get("state") == "done", _row8.get("state"))
        check("I2 ended_at_stale 被标记", _row8.get("ended_at_stale") is True, _row8)
        check("I3 detail 里写明重点", "收尾" in str(_row8.get("detail") or ""), _row8.get("detail"))

        section("汇总")
        print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
        if FAILS:
            print("\n失败明细：")
            for x in FAILS:
                print("  - " + x)
        return 0 if FAIL == 0 else 1
    finally:
        os.chdir(old_cwd)
        if old_state is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old_state
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
