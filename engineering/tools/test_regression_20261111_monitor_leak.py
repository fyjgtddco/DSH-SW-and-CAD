#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【回归】旧的"进程内 SW 监控"必须彻底消失。

背景（test_regression_20261111_monitor_leak.py 原文）：
  任务 18:07 全部房间 done、SW 已关闭，但 `_monitor_bg_impl` 仍有 4 个进程
  （PID 28804/14092/31040/15796，跨两个任务）；cmd_sw_monitor_stop() 只删
  PID 文件不杀进程；cmd_sw_monitor_bg() 的幂等判活用 os.kill(pid,0) 判活。

那套东西的职责只有一件：把 SLDWORKS.EXE 在不在写进 sw_state.json。
它**不参与任何授权判断**，却要维护一个后台进程 + PID 文件 + 泄漏清理逻辑。
监控器接管后它整个消失了：
  · "谁在用 SW" → 监控器票据状态机（持久化，不靠进程探活）；
  · "SW 在不在跑" → sw-status 现查 tasklist（一次调用，不常驻）。

本文件断言：
  ① 那些函数/常量/PID 文件已删除；
  ② mode_gate 里不再有 spawn 常驻监控进程的代码；
  ③ sw-status 仍然能回答"SW 在不在跑"（能力没丢，只是不再靠常驻进程）。

用法：python test_regression_20261111_monitor_leak.py
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

import mode_gate as mg

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  [PASS] " + name)
    else:
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))
        FAIL.append(name)


def main():
    src = inspect.getsource(mg)
    print("\n=== ① 常驻监控进程的实现已删除 ===")
    for gone in ("cmd_sw_monitor_bg", "_monitor_bg_impl", "cmd_sw_monitor_start",
                 "cmd_sw_monitor_stop", "_purge_orphan_monitors",
                 "_sw_monitor_loop", "_monitor_alive", "_sync_sw_state_file",
                 "_write_sw_state_now", "_SW_MONITOR_PID_FILE"):
        check("mg 不再有 %s" % gone, not hasattr(mg, gone))

    print("\n=== ② 不再 spawn 常驻监控 ===")
    check("源码里没有 _monitor_bg_impl 的分发分支",
          '"_monitor_bg_impl"' not in src)
    check("源码里没有 sw-monitor-bg 命令",
          '"sw-monitor-bg"' not in src)
    check("workflow_gate 不再启动后台监控进程",
          "sw-monitor-bg" not in open(os.path.join(HERE, "workflow_gate.py"),
                                       encoding="utf-8-sig").read())

    print("\n=== ③ 能力没丢：sw-status 仍回答 SW 在不在跑 ===")
    try:
        st = mg.cmd_sw_status()
        check("sw-status ok=true", st.get("ok") is True, st)
        check("sw-status 带 sw_running 字段", "sw_running" in st, st)
        check("sw-status 带 sw_pids 字段", "sw_pids" in st, st)
        check("sw-status 明确监控器接入状态",
              isinstance(st.get("monitor"), dict), st)
    except Exception as e:
        check("sw-status 可调用", False, repr(e))

    print("\n" + "=" * 60)
    if FAIL:
        print("FAILED %d:" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
