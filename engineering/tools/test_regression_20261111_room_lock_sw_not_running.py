#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：「SW 没在跑」不得被当成「锁空闲」（2026-11-11 并发事故）。

── 事故原貌 ─────────────────────────────────────────────────────────────
房间级独占（hold_mode=room）配置下，仍然出现真实的跨房间并发：
  · 15:20:39 壳体开窗、15:20:51 支承密封也开窗，重叠 112 秒；
  · room-board 自报 hold_mode=room、build_overlap_count=2。
根因：判"能不能取锁"时用了 (not running) —— SW 进程没在跑就被当成"锁空闲"。
于是 A 刚取到锁、SW 还没冷启动，B 也能拿到，两个房间各自驱动同一个 SW。

── 修复后的形态 ─────────────────────────────────────────────────────────
**"SW 在不在跑"与"谁有权用 SW"彻底解耦**：
  · 授权只由外部监控器的票据状态机决定（按 epoch/task/room/session 绑定）；
  · sw_bridge 的许可入口不再看一眼 tasklist 就放行；
  · 进程探活只用于报告与清场（sw-status 的 sw_running / close-all）。

本文件断言这四条：
  ① 源码里不再存在"因为 SW 没在跑就放行"的判据；
  ② 没有监控器时，许可入口【拒绝】而不是"反正 SW 没跑就给你用"；
  ③ 许可入口三态（permit / busy / denied）齐备，busy 时给 job id；
  ④ room-board 的 build_overlap_count 指标仍在（可回归）。

用法：python test_regression_20261111_room_lock_sw_not_running.py
"""
import inspect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

import mode_gate as mg
import sw_bridge as sb

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  [PASS] " + name)
    else:
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))
        FAIL.append(name)


def sec(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main():
    mg_src = inspect.getsource(mg)
    sb_src = inspect.getsource(sb)
    gate = inspect.getsource(sb._gate_sw_lock)

    sec("A. ★ 核心：「SW 没在跑」不再等于「锁空闲」")
    # 旧判据的三种写法在授权路径里都必须查无此人。
    for bad in ("not running)", "(not running)"):
        check("授权路径不含判据 %r" % bad,
              bad not in gate and bad not in mg_src,
              [l for l in (gate + mg_src).split("\n") if bad in l][:2])
    check("许可入口不看 _sw_running（进程事实不参与授权）",
          "_sw_running" not in gate, gate[:300])
    check("许可入口不写 mode_state.sw_lock.owner（不再有本地 owner）",
          '["owner"]' not in gate)

    sec("B. 没有监控器时必须拒绝，不许因为 SW 没跑就放行")
    old = os.environ.get("DSH_STATE_DIR")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="swmon_nr_")
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        import sw_monitor_client as mc
        check("隔离起点：未接入", mc.enabled() is False)
        got = sb._gate_sw_lock("房间甲", command="run")
        check("★ 未接入时 denied=True", got.get("denied") is True, got)
        check("★ 拒绝码是 MONITOR_NOT_CONFIGURED",
              got.get("code") == "MONITOR_NOT_CONFIGURED", got)
        # hint 可能来自两处：配置缺失报错本身的 hint（sw_monitor_client 的
        #   require_ready），或外层包装的 hint。两处都必须给出可执行命令。
        _hint = str(got.get("hint") or "") + str(got.get("error") or "")
        check("★ 拒绝时带可执行 hint",
              "sw-monitor attach" in _hint, got)
        check("拒绝时【不】返回 permit", got.get("permit") is not True, got)
    finally:
        if old is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    sec("C. 许可入口三态齐备（permit / busy / denied）")
    for k in ('"permit"', '"busy"', '"denied"'):
        check("入口返回结构含 %s" % k, k in gate, gate[:400])
    check("busy 分支带 job_id", '"job_id"' in gate)
    check("★ busy 分支不含 sleep（等待交给后台 job）",
          not re.search(r"time\.sleep|__import__\(.time.\).sleep", gate), gate[:400])

    sec("D. 串行性指标仍在（build_overlap_count 可回归）")
    board_src = inspect.getsource(mg.cmd_room_board) + \
        inspect.getsource(mg._cmd_room_board_once)
    check("room-board 暴露 build_overlap_count",
          "build_overlap_count" in board_src)

    sec("E. sw_status 仍能回答「SW 在不在跑」（能力没丢，只是不再参与授权）")
    st = mg.cmd_sw_status()
    check("sw-status ok=true", st.get("ok") is True, st)
    check("带 sw_running", "sw_running" in st, st)
    check("带 monitor 状态", isinstance(st.get("monitor"), dict), st)

    print("\n" + "=" * 70)
    if FAIL:
        print("FAILED %d:" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
