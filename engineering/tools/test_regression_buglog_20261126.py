#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BUG记录.md（2026-11-26 机械臂任务）修复回归。

对应 19 条缺陷里【已改代码】的 8 条。其余 11 条属于环境/宿主/机制设计，
本测试只锁"改过的判据不会被后人删回去"，不假装它们也修了。

用法： python engineering\tools\test_regression_buglog_20261126.py
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
FLOW = os.path.join(HERE, '..', 'plugins', 'dsh-engineering-seven-stage', 'lib', 'flow.js')
FLOW = os.path.normpath(FLOW)

PASS, FAIL = [], []


def ok(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  [PASS] ' if cond else '  [FAIL] ') + name + ('' if cond else '  → ' + detail))


def main():
    with open(FLOW, encoding='utf-8') as f:
        src = f.read()

    print('=' * 70)
    print('BUG-013 · hits 计数不能是"检索工单数"')
    print('=' * 70)
    ok('hits 用真实命中总数', 'reduce((n, x) => n + ((x.hits ?? []).length), 0)' in src)
    ok('另给 searchRuns 区分次数', 'searchRuns:runs' in src)
    ok('不再直接 hits:filled', 'hits:filled' not in src)

    print('=' * 70)
    print('BUG-007 · reopenedKeys 语义拆分')
    print('=' * 70)
    ok('真·重开只在曾关闭过里面取', 'const trulyReopened = restated.filter(k => everClosed.has(k));' in src)
    ok('reopenedKeys 用 trulyReopened', 'reopenedKeys: trulyReopened,' in src)
    ok('新增 restatedKeys', 'restatedKeys: restated,' in src)
    ok('新增 stillOpenKeys', 'stillOpenKeys:' in src)
    ok('note 里解释三个字段', 'reopenedKeys=曾被关闭过' in src)

    print('=' * 70)
    print('BUG-009 · commit 的 conditions 必须标字符串')
    print('=' * 70)
    ok('actionHelp 明写"不是数组"', 'conditions:<字符串，不是数组' in src)

    print('=' * 70)
    print('BUG-011 · 无需检索的显式豁免')
    print('=' * 70)
    ok('识别无需检索标记', 'noSearchDeclared' in src)
    ok('豁免仍要求 blocking gap',
       'NO_SEARCH_DECLARED_NEEDS_BLOCKING_GAP' in src)
    ok('豁免写审计留痕', "search_exempt_declared" in src)
    ok('SEARCH_REQUIRED 指路到豁免', '引擎会据此豁免本步的检索证据要求' in src)

    print('=' * 70)
    print('BUG-002 · setup 的 next 必须指向 launch')
    print('=' * 70)
    ok("setup next 写 launch", "launch → 一次性创建并邀请三个角色子会话" in src)
    # 只检查【可执行代码】里的 next 文案：历史注释里保留"现场原本写的是 invite"
    # 是有价值的（说明为什么改），不能因为它存在就判失败。
    code_lines = [ln for ln in src.splitlines()
                  if not ln.lstrip().startswith('//')]
    code = '\n'.join(code_lines)
    ok("可执行代码里不再把 invite 当 next",
       "invite → 为每个角色创建独立二级子代理" not in code)

    print('=' * 70)
    print('BUG-004 / 010 / 014 · 竞态与约束的文案必须可自纠')
    print('=' * 70)
    ok('单工单约束说"不是故障"', '这是【正常的单工单约束，不是故障】' in src)
    ok('PLANNER_ACTION_PENDING 先讲竞态', '先排除【竞态】' in src)
    ok('PENDING_JOB 先讲竞态', '常见于【竞态】' in src)

    print('=' * 70)
    print('BUG-012 / 019 · status 必须给出权威催办判据')
    print('=' * 70)
    ok('存在 handoff 投影', 'handoff:(() => {' in src)
    ok('给出 state delivered/working/idle',
       "'delivered'" in src and "'working'" in src and "'idle'" in src)
    ok('给出 shouldNudge', 'shouldNudge: !done && !!j' in src)
    ok('给出 nudgeable', 'nudgeable,' in src)
    ok('给出 verdict 一句话结论', 'verdict: nudgeable.length' in src)
    ok('明说 current.* 不能用来判断交没交',
       'current.knowledge / current.design / current.review 为 null' in src)
    ok('send_message help 指向 handoff', 'status.handoff.verdict' in src)

    print('=' * 70)
    print('反向：不许把已修的地方改回去')
    print('=' * 70)
    ok('BLIND_READ_PARTS 仍在（BUG-001 未改，属提示类）',
       "BLIND_READ_PARTS = ['requirements'" in src)
    ok('SEARCH_BUDGET_REACHED 保留', 'SEARCH_BUDGET_REACHED' in src)
    ok('VERDICT_ISSUE_MISMATCH 保留', 'VERDICT_ISSUE_MISMATCH' in src)

    print('=' * 70)
    print('PASS=%d FAIL=%d' % (len(PASS), len(FAIL)))
    print('=' * 70)
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
