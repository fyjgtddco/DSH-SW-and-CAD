#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【A15 回归测试】跨房间并发建模：SW 使用权必须由【监控器】串行化。

── 这个文件为什么被整份重写 ──────────────────────────────────────────────
它原先断言的是"房间级独占锁"：room-start 取锁、room-end 释放、
hold_mode="room"、甲持锁时乙 sw-request 必须 acquired=False。

那套实现已经被【删除】了 —— 不是因为不好，而是因为它有个结构性问题：
它把"谁在用 SW"记在 mode_state.sw_lock 里，靠心跳/产物 mtime 猜持有者死活，
于是"SW 进程没在跑"会被当成"锁空闲"（2026-11-11 实测 8 对构建重叠，最长 216 s）。

现在的判据变成：
  ① 房间**不再长期占锁**：使用权按【命令】发放（begin/end），
     房间在纯文件阶段（写脚本/读报告/验签）本来就不该占着 SW；
  ② 串行化由监控器的 FIFO 票据保证，不由本地 owner 字段保证；
  ③ 本文件能验证的是【契约】而不是监控器内部 —— 监控器自身的队列语义
     由 tools/sw_monitor/selfcheck.py 覆盖（5/5）。

因此这里断言的是：
  A. 旧房间级锁的实现实体（hold 常量 / cmd_sw_request / _reclaim_lock）已消失；
  B. room-start **不再**取 SW 使用权（它只登记房间）；
  C. room-end 也不会去"释放锁"，只在确实还占着时兜底 release；
  D. build_overlap_count 仍然可观测（串行性的可回归指标没有丢）。

用法：python test_regression_a15_room_lock.py
退出码 0 = 全部通过。
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

    print("\n=== A. 房间级锁的实现实体已删除 ===")
    for gone in ("SW_ROOM_HOLD", "SW_ROOM_HOLD_MAX", "_room_hold_enabled",
                 "_room_hold_limit", "cmd_sw_request", "_reclaim_lock",
                 "_lock_owner_alive", "_purge_orphan_lock"):
        check("mg 不再有 %s" % gone, not hasattr(mg, gone))
    check("源码里不再有 hold_mode=room 的分支",
          '"hold_mode": "room"' not in src)

    print("\n=== B. room-start 不再取 SW 使用权 ===")
    # room-start 里不得再出现"取锁"调用；它只登记房间。
    rs = inspect.getsource(mg.cmd_room_start)
    # 注意：注释里提到旧函数名是允许的（那是防止回归的说明），
    #   要禁的是【调用】。
    check("cmd_room_start 不调用取锁函数",
          "cmd_sw_request(" not in rs, [l for l in rs.split("\n") if "cmd_sw_request(" in l])
    check("cmd_room_start 不写 sw_lock.owner",
          '["owner"] =' not in rs and "_sw_lock(" not in rs)
    check("cmd_room_start 明确说明不再预取锁",
          "不再取锁" in rs or "不取锁" in rs, rs[:200])

    print("\n=== C. room-end 只在确实占用时兜底释放 ===")
    re_src = inspect.getsource(mg.cmd_room_end)
    check("cmd_room_end 仍会兜底 release", "cmd_sw_release" in re_src)
    check("cmd_room_end 的 release 有前置判断（不是无条件调用）",
          "owner" in re_src or "未占用" in re_src)

    print("\n=== D. 串行性的可回归指标仍在 ===")
    check("build_overlap 统计仍存在", hasattr(mg, "_build_overlap_report")
          or "build_overlap_count" in src)
    # 它由 _cmd_room_board_once 组装（cmd_room_board 只是 wait 包装层）
    _board_src = inspect.getsource(mg.cmd_room_board) + \
        inspect.getsource(mg._cmd_room_board_once)
    check("room-board 仍暴露 build_overlap_count",
          "build_overlap_count" in _board_src)

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
