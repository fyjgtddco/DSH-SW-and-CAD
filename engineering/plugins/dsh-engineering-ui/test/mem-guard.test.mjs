/**
 * mem-guard.test.mjs — 【内存守卫】纯函数与真实回收的回归
 *
 * 覆盖三块（都对应一次真实需求/实测）：
 *   A. 并发闸门 evaluateConcurrency：≤3 放行、第 4 个拒绝、含孙代计数
 *   B. 配置优先级：运行期覆盖 > 环境变量 > 默认值；非法值一律回落默认
 *   C. 工作集回收：EmptyWorkingSet 真的返回成功（只回收、不杀进程），
 *      且脚本里的 OpenProcess 掩码含 PROCESS_QUERY_LIMITED_INFORMATION(0x1000)
 *      —— 只用 0x0300 会对自身进程 Access denied（实测踩过）。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEFAULT_MAX_SUBAGENTS,
  memGuardEnabled,
  maxActiveSubagents,
  reclaimThresholdPct,
  reclaimIntervalMs,
  setMemGuardOverrides,
  memGuardOverrides,
  countOccupiedChildren,
  evaluateConcurrency,
  buildWorkingSetScript,
  memorySnapshot,
  reclaimWorkingSets,
  listPidsByName,
  createReclaimer,
  parseLastJson,
} from '../lib/mem-guard.js';

test('A. 并发闸门：≤3 放行，第 4 个拒绝并给出可读指引', () => {
  const rows = [
    { kind: 'child', id: 'a', parentId: 'R' },
    { kind: 'child', id: 'b', parentId: 'R' },
  ];
  let g = evaluateConcurrency(rows, 'R', 'R', 3);
  assert.equal(g.allowed, true);
  assert.equal(g.active, 2);

  rows.push({ kind: 'child', id: 'c', parentId: 'R' });
  g = evaluateConcurrency(rows, 'R', 'R', 3);
  assert.equal(g.allowed, false);
  assert.equal(g.reason, 'SUBAGENT_LIMIT_REACHED');
  assert.match(g.message, /上限 3/);
  assert.match(g.message, /DSH_ENG_MAX_SUBAGENTS/);
});

test('A2. 孙代也占名额（只有子代理结束才释放）', () => {
  const rows = [
    { kind: 'child', id: 'a', parentId: 'R' },
    { kind: 'child', id: 'a1', parentId: 'a', hasChildren: true },
    { kind: 'child', id: 'a2', parentId: 'a' },
    { kind: 'diagnostic', id: 'x', parentId: 'R' },
  ];
  const { ids } = countOccupiedChildren(rows, 'R');
  // R 的直接子代理 a + 中间层 a1（hasChildren）→ 2；a2 与 diagnostic 不额外计
  assert.equal(ids.length, 2);
  assert.deepEqual(ids.sort(), ['a', 'a1']);
});

test('B. 配置优先级：运行期覆盖 > 环境变量 > 默认', () => {
  const oldEnv = { ...process.env };
  try {
    delete process.env.DSH_ENG_MAX_SUBAGENTS;
    setMemGuardOverrides({});
    assert.equal(maxActiveSubagents(), DEFAULT_MAX_SUBAGENTS);

    process.env.DSH_ENG_MAX_SUBAGENTS = '5';
    assert.equal(maxActiveSubagents(), 5);

    setMemGuardOverrides({ max_subagents: 2 });
    assert.equal(maxActiveSubagents(), 2, '运行期覆盖优先于环境变量');

    // 超上界 → 夹到硬上界 12（不是回落到环境变量：用户显式写了值，
    // 夹紧比"悄悄换成另一个数"更可预期）
    setMemGuardOverrides({ max_subagents: 999 });
    assert.equal(maxActiveSubagents(), 12);

    // 非数字 → 视为没写，回落到环境变量
    setMemGuardOverrides({ max_subagents: 'abc' });
    assert.equal(maxActiveSubagents(), 5);

    process.env.DSH_ENG_MEM_RECLAIM_PCT = '70';
    process.env.DSH_ENG_MEM_RECLAIM_MS = '30000';
    setMemGuardOverrides({});
    assert.equal(reclaimThresholdPct(), 70);
    assert.equal(reclaimIntervalMs(), 30000);
  } finally {
    for (const k of ['DSH_ENG_MAX_SUBAGENTS', 'DSH_ENG_MEM_RECLAIM_PCT', 'DSH_ENG_MEM_RECLAIM_MS']) {
      if (oldEnv[k] === undefined) delete process.env[k];
      else process.env[k] = oldEnv[k];
    }
    setMemGuardOverrides({});
  }
});

test('B2. DSH_ENG_MEM_GUARD=0 关闭整个守卫', () => {
  const old = process.env.DSH_ENG_MEM_GUARD;
  try {
    process.env.DSH_ENG_MEM_GUARD = '0';
    assert.equal(memGuardEnabled(), false);
    process.env.DSH_ENG_MEM_GUARD = 'yes';
    assert.equal(memGuardEnabled(), true);
  } finally {
    if (old === undefined) delete process.env.DSH_ENG_MEM_GUARD;
    else process.env.DSH_ENG_MEM_GUARD = old;
  }
});

test('B3. 节流回收器：间隔内不再真的回收', () => {
  const r = createReclaimer();
  const first = r({ pids: [] });
  assert.ok(first, '第一次必须给出结果对象');
  const second = r({ pids: [] });
  assert.equal(second.skipped, 'throttled');
});

test('C. 工作集回收脚本：OpenProcess 掩码必须含 0x1000（否则自身进程被拒）', () => {
  const script = buildWorkingSetScript([1, 2]);
  assert.match(script, /OpenProcess\(0x1500/);
  assert.match(script, /EmptyWorkingSet/);
  assert.ok(!/SetProcessWorkingSetSize/.test(script),
    '不得用硬修剪（会把整个工作集刷到 pagefile）');
  assert.ok(!/taskkill/i.test(script), '绝不能杀进程');
});

test('C2. 真实回收：返回 ok 且 reclaimed≥1（进程仍然活着）', () => {
  const r = reclaimWorkingSets({ pids: [process.pid], force: true });
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.ok((r.reclaimed || 0) >= 1, '本进程必须被成功回收一次');
  assert.equal(r.failed, 0);
  assert.equal(typeof r.before.load, 'number');
  assert.equal(typeof r.after.load, 'number');
});

test('C3. 内存快照与进程枚举可用（不抛、不启动任何进程）', () => {
  const snap = memorySnapshot();
  assert.equal(snap.ok, true, JSON.stringify(snap));
  assert.ok(snap.total_mb > 0);
  const pids = listPidsByName(['node.exe']);
  assert.ok(Array.isArray(pids));
});

test('C4. parseLastJson 容忍 PowerShell 的警告行', () => {
  const text = 'WARNING: something\r\n{"before":{"load":1},"after":{"load":1}}\r\n';
  const j = parseLastJson(text);
  assert.equal(j.before.load, 1);
  assert.equal(parseLastJson(''), null);
  assert.equal(parseLastJson('not json'), null);
});
