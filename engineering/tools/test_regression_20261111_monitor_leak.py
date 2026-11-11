# -*- coding: utf-8 -*-
"""test_regression_20261111_monitor_leak.py — 【P2 残留进程】回归

═══════════════════════════════════════════════════════════════════════════
现场（验收复跑 20261111_131815_580E38FA，磁盘 + 进程实锤）
═══════════════════════════════════════════════════════════════════════════
任务 18:07 全部房间 done、SW 已关闭，但 `_monitor_bg_impl` 仍有 4 个进程
（PID 28804 / 14092 / 31040 / 15796），且跨两个任务累积。init 与 room-end
均未终止它们。

根因（三条叠加）
  ① cmd_sw_monitor_stop() 旧实现只 os.remove(PID 文件)，【从不杀进程】；
     而 _sw_monitor_loop 是 while True —— PID 文件一删就再也没人找得到它。
  ② cmd_sw_monitor_bg() 的幂等判活用 os.kill(old_pid, 0)，而本文件
     _pid_alive() 的注释早已写明该调用在本机是【假阴性】→ 每次 select
     都新起一个（workflow_gate.py:4525 在 parallel 模式 Popen 一次）。
  ③ 没有清理挂点：room-end / close-all / rooms-reset 都不理它。

本测试钉住三件事：
  A. stop 必须真的结束进程（不是只删文件）
  B. bg 必须幂等（第二次调用不得再起新进程）
  C. 任务级收尾（_maybe_advance_workflow_finished → finished）要带
     monitor_cleanup 字段，证明清理挂点存在

运行：python test_regression_20261111_monitor_leak.py
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mode_gate as MG

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [PASS] %s" % name)
    else:
        FAIL.append((name, detail))
        print("  [FAIL] %s %s" % (name, detail))


def _monitor_procs():
    """当前所有 _monitor_bg_impl 进程的 PID 列表（不含自己）。

    ⚠️ 【2026-11-11 实测】本机 Win11 【没有 wmic】（已弃用未预装），
    第一版用 wmic 枚举 → 恒返回 [] → 测试误判"进程没起来"。
    改用 mode_gate 自己的枚举器（PowerShell Get-CimInstance），
    保证"测什么"和"被测代码看什么"是同一份事实。
    """
    pids = []
    try:
        for pid, cmd in MG._list_python_procs_with_cmdline():
            if pid > 0 and pid != os.getpid() and "_monitor_bg_impl" in (cmd or ""):
                pids.append(pid)
    except Exception:
        pass
    return pids


def _wait_procs(n, timeout=12.0):
    """等到恰有 n 个 monitor 进程可见，返回最后一次枚举结果。

    ⚠️ 【2026-11-11 实测】不能只 sleep 固定 1.5s 就断言：
    子进程 Popen 出来到被 WMI 快照到有延迟（实测偶发 2~3s，机器忙时更久），
    固定 sleep 会出现"进程其实起来了，测试却说没起来"的假失败
    （单跑通过、串行跑挂 2 个，就是这么来的）。这里改成轮询等待。
    """
    deadline = time.time() + timeout
    pids = []
    while time.time() < deadline:
        pids = _monitor_procs()
        if len(pids) == n:
            return pids
        time.sleep(0.3)
    return pids


def _cleanup_all():
    """测试前后都清干净，避免污染下一次运行。

    ⚠️ 【2026-11-11 实测】只调一次 _stop_sw_monitor 不够：
    它在"PID 文件已被删"的分支里走的是孤儿清理，而孤儿清理要先枚举
    进程（PowerShell Get-CimInstance，RTT 约 0.5~1s）。若进程刚 Popen
    出来、命令行还没被 WMI 快照到，这一轮就会漏杀 → 测试结束后留下
    一个孤儿 monitor（实测踩到：跑完全量后残留 PID 18648/20892）。
    所以这里做【两轮清理 + 显式扫杀】，务必让测试自身零残留。
    """
    for _round in range(2):
        try:
            MG._stop_sw_monitor(quiet=True)
        except Exception:
            pass
        for pid in _monitor_procs():
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            except Exception:
                pass
        time.sleep(0.3)
    # 最后兜一层：PID 文件也删掉（它可能指向已死进程，留着会误导下一次 bg）
    try:
        if os.path.exists(MG._SW_MONITOR_PID_FILE):
            os.remove(MG._SW_MONITOR_PID_FILE)
    except Exception:
        pass


print("=" * 70)
print("0. 前置清理（保证从一个干净状态开始）")
print("=" * 70)
_cleanup_all()
check("清理后无残留 monitor 进程", _monitor_procs() == [],
      "残留=%r" % (_monitor_procs(),))

print("=" * 70)
print("A. ★ stop 必须【真的结束进程】，不是只删 PID 文件")
print("=" * 70)
r = MG.cmd_sw_monitor_bg()
check("sw-monitor-bg 启动成功", r.get("ok") is True, repr(r))
started_pid = r.get("started_pid")
check("返回 started_pid", bool(started_pid), repr(r))
alive_pids = _wait_procs(1)
check("进程确实起来了", len(alive_pids) == 1, "实际=%r" % (alive_pids,))

r2 = MG.cmd_sw_monitor_stop()
check("stop 报 ok=True", r2.get("ok") is True, repr(r2))
check("★ stop 报 stopped=True（不是只删文件）", r2.get("stopped") is True, repr(r2))
time.sleep(1.0)
left = _monitor_procs()
check("★★ stop 之后【没有】残留进程（旧实现会残留）", left == [],
      "残留=%r（这正是 P2 现场：4 个）" % (left,))
check("stop 之后 PID 文件已清掉",
      not os.path.exists(MG._SW_MONITOR_PID_FILE))

print("=" * 70)
print("B. ★ bg 必须幂等（旧实现用 os.kill 假阴性 → 每次都新起）")
print("=" * 70)
_cleanup_all()
r1 = MG.cmd_sw_monitor_bg()
pid1 = r1.get("started_pid")
_wait_procs(1)
r2 = MG.cmd_sw_monitor_bg()
check("第一次启动成功", r1.get("ok") is True, repr(r1))
check("★ 第二次返回 already_running=True", r2.get("already_running") is True,
      repr(r2))
check("第二次返回的 pid 与第一次相同", r2.get("pid") == pid1,
      "pid1=%r pid2=%r" % (pid1, r2.get("pid")))
now_pids = _wait_procs(1)
check("★★ 两次 bg 之后仍只有 1 个进程（旧实现会是 2 个）",
      len(now_pids) == 1, "实际=%r" % (now_pids,))
_cleanup_all()

print("=" * 70)
print("C. 清理挂点存在：任务级收尾 / rooms-reset / close-all --quit")
print("=" * 70)
import inspect

src_finish = inspect.getsource(MG._maybe_advance_workflow_finished)
check("★ 任务收尾(finished)带 monitor_cleanup",
      "monitor_cleanup" in src_finish and "_stop_sw_monitor" in src_finish)

src_reset = inspect.getsource(MG.cmd_rooms_reset)
check("rooms-reset 带 monitor_cleanup",
      "monitor_cleanup" in src_reset and "_stop_sw_monitor" in src_reset)

src_close = inspect.getsource(MG.cmd_close_all)
check("close-all --quit 带 monitor_cleanup",
      "monitor_cleanup" in src_close and "_stop_sw_monitor" in src_close)

print("=" * 70)
print("D. 孤儿清理兜底（PID 文件丢失但进程还在）")
print("=" * 70)
_cleanup_all()
r = MG.cmd_sw_monitor_bg()
check("起了一个监控", len(_wait_procs(1)) == 1, repr(_monitor_procs()))
# 模拟旧实现的坑：文件被删掉，进程还在
try:
    os.remove(MG._SW_MONITOR_PID_FILE)
except Exception:
    pass
check("PID 文件已被人为删除（模拟旧 stop 的坑）",
      not os.path.exists(MG._SW_MONITOR_PID_FILE))
r3 = MG.cmd_sw_monitor_stop()
time.sleep(1.0)
check("★ PID 文件丢失时也能清理孤儿进程",
      _monitor_procs() == [],
      "残留=%r" % (_monitor_procs(),))
check("孤儿清理在返回体里留痕",
      ("stopped" in r3) or ("note" in r3), repr(r3))
_cleanup_all()

print("=" * 70)
print("汇总：PASS=%d  FAIL=%d" % (len(PASS), len(FAIL)))
print("=" * 70)
if FAIL:
    for n, d in FAIL:
        print("  FAILED: %s %s" % (n, d))
    sys.exit(1)
