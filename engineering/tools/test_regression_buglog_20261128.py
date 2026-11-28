#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BUG记录.md（2026-11-28 机械臂测试）修复回归。

对应 12 条里【已改代码】的部分。锁的是判据，不是某种写法。
用法： python engineering\tools\test_regression_buglog_20261128.py
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
FLOW = os.path.normpath(os.path.join(HERE, '..', 'plugins',
                                     'dsh-engineering-seven-stage', 'lib', 'flow.js'))
GATE = os.path.normpath(os.path.join(HERE, 'workflow_gate.py'))

PASS, FAIL = [], []


def ok(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  [PASS] ' if cond else '  [FAIL] ') + name + ('' if cond else '  → ' + detail))


def main():
    flow = io.open(FLOW, encoding='utf-8').read()
    gate = io.open(GATE, encoding='utf-8').read()

    print('=' * 70)
    print('BUG-1 · init 的 next_command 不能指向"第 0 题"')
    print('=' * 70)
    ok('next_command 不再说"第 0 题"', '第0题回答原文' not in gate)
    ok('next_command 指向模式确认两问', '两项固定问题' in gate)
    # 历史注释里保留"原本写的是第 0 题"是有价值的，只查可执行代码
    code = '\n'.join(ln for ln in gate.splitlines() if not ln.lstrip().startswith('#'))
    ok('可执行代码里无"先问第 0 题"', '请先把第 0 题' not in code)

    print('=' * 70)
    print('BUG-2 · flow_order 不能再提题数区间')
    print('=' * 70)
    ok('flow_order 说明不设配额', '七阶段不设题数配额' in gate)
    ok('不再出现"强 10~50"可执行文案',
       '题数区间：强 10~50、弱 5~10' not in code)

    print('=' * 70)
    print('BUG-4 · planner 在角色阶段默认 yield')
    print('=' * 70)
    ok('角色阶段给出 yield 指引', "case 'knowledge':" in flow and "action:'yield'" in flow)
    ok('明说"不需要你唤醒"', '不需要你唤醒' in flow)
    ok('指向 handoff 判据而非 current.*', 'status.handoff.nudgeable' in flow)

    print('=' * 70)
    print('BUG-6 · resolved 提示必须看 blockedBy 是否为空')
    print('=' * 70)
    ok('resolved 提示按 blockedBy 分支', 'blockedBy 当前是【空】的' in flow)
    ok('空时明说不要传 resolved', '不要传 resolved' in flow)

    print('=' * 70)
    print('BUG-8 / 12 · status 自带新鲜度')
    print('=' * 70)
    ok('status 有 observedAt', 'observedAt: Date.now()' in flow)

    print('=' * 70)
    print('BUG-9 · 来源 ID 免于裁剪')
    print('=' * 70)
    ok('ready 里带 sourceIds', 'sourceIds: allHits.map' in flow)
    ok('明说这份清单不会被裁剪', '不会被裁剪' in flow)

    print('=' * 70)
    print('BUG-10 · 无需检索的豁免要认得"人话"')
    print('=' * 70)
    ok('豁免正则覆盖 无需新检索', '[^。；;\\n]{0,8}检索' in flow)
    ok('豁免仍要求 blocking gap', 'NO_SEARCH_DECLARED_NEEDS_BLOCKING_GAP' in flow)

    print('=' * 70)
    print('BUG-11 · query 长度守住宿主上限')
    print('=' * 70)
    ok('定义宿主上限常量', 'HOST_QUERY_MAX' in flow)
    ok('超长时拒绝并要求拆分', 'SEARCH_QUERY_TOO_LONG' in flow)
    ok('支持 queryParts', 'queryParts' in flow)
    ok('queryParts 上限 4 条', 'HOST_QUERY_BATCH' in flow)
    ok('help 里写明 500 字符', '最多 500 字符' in flow)

    print('=' * 70)
    print('反向：不许把这些判据改回去')
    print('=' * 70)
    ok('SEARCH_REQUIRED 兜底仍在', 'SEARCH_REQUIRED' in flow)
    ok('RESOLVED_DEPENDENCY_NOT_FOUND 仍在', 'RESOLVED_DEPENDENCY_NOT_FOUND' in flow)
    ok('HIT_LIMIT 仍在', 'HIT_LIMIT' in flow)

    print('=' * 70)
    print('PASS=%d FAIL=%d' % (len(PASS), len(FAIL)))
    print('=' * 70)
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
