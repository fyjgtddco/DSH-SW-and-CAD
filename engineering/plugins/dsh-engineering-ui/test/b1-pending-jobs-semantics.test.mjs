#!/usr/bin/env node
/**
 * 【B1 修复·取数口径回归】pendingJobsOf 必须只认【归属本会话】的未 settle job。
 * ==========================================================================
 * 为什么单独一个文件（与 b1-pending-jobs.test.mjs 并存）：
 *   那份测的是"接线有没有接上"（源码级 grep），这份测的是"取数对不对"——
 *   两者都可能单独出错，而且取数错了会让接线【看起来正常但结论全反】。
 *
 * 现场（2026-11-07 会话 · 行走传动间 b55576ac）：
 *   :670 "Log is 0 bytes (buffered). Build still ru…"
 *   :672 turn/end {completed}      ← 先报了正常收工
 *   :673 才是该 job 的 job/settled ← 后台任务这时才结束
 *   :1133 父代理据此当完成；:1389 总理 21 分钟后才发现 run6.log 为 0 字节
 *
 * 初版实现按"瞎猜字段"取数，两个错误都实测复刻过：
 *   ① 没把 caller 传给 jobs.list()。真实签名 list(caller) 的过滤是
 *      `owner === undefined || owner.id === caller`（dsh-jobs-local:503-505），
 *      不传 caller 就【只拿回无主任务】→ 该 agent 自己的建模 job 全看不到
 *      → B1 明明"接了线"却永远报 0 个未完成（等于没修）。
 *   ② 把【无主】任务算进每个 agent → 所有子代理一律 pending_jobs，
 *      父代理看到"全部未完成"，又一堵永不放行的墙。
 *
 * 本文件用【复刻的 jobs 服务】直接跑 index.js 里真正的 pendingJobsOf，
 * 而不是 grep 源码 —— 只有这样才能证明"取数口径"是对的。
 *
 * 用法：node --test test/b1-pending-jobs-semantics.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'index.js'), 'utf8');

/** 把 pendingJobsOf 的定义原样抠出来（不重写、不简写，跑的就是真代码）。 */
function extractPendingJobsOf() {
  const start = SRC.indexOf('function pendingJobsOf(');
  assert.ok(start > 0, 'index.js 里必须存在 pendingJobsOf（B1 的探测入口）');
  // 从函数头开始做花括号配平，取完整函数体
  const braceStart = SRC.indexOf('{', start);
  let depth = 0;
  let end = -1;
  for (let i = braceStart; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) { end = i + 1; break; }
    }
  }
  assert.ok(end > braceStart, 'pendingJobsOf 花括号必须配平');
  return SRC.slice(start, end);
}

/**
 * 复刻 dsh-jobs-local 的 jobs 服务：
 *   list(caller) → owner === undefined || owner.id === caller
 *   view()       → { id, kind, label, owner?, status, ... }
 * ⚠️ 必须与宿主源码一致，否则测的是"我编的契约"而不是宿主的契约。
 */
function fakeJobs(records) {
  return {
    calls: [],
    list(caller) {
      this.calls.push(caller);
      return records
        .filter((r) => r.owner === undefined || r.owner === caller)
        .map((r) => Object.assign({}, r));
    },
  };
}

function build(records, agentId) {
  const fnSrc = extractPendingJobsOf();
  const ctx = { get: (name) => (name === 'jobs' ? fakeJobs(records) : undefined) };
  // eslint-disable-next-line no-new-func
  const fn = new Function('ctx', fnSrc + '\nreturn pendingJobsOf;')(ctx);
  return fn(agentId);
}

test('★ 只算【归属本会话】的未 settle job（不能漏，也不能把无主的算进来）', () => {
  const r = build([
    { id: 'j-own', owner: 'agent-A', status: 'running', label: 'run6.log 建模' },
    { id: 'j-other', owner: 'agent-B', status: 'running', label: '别人的' },
    { id: 'j-none', owner: undefined, status: 'running', label: '无主看护任务' },
  ], 'agent-A');
  assert.equal(r.available, true);
  assert.deepEqual(r.pending.map((j) => j.id), ['j-own'],
    '只认 owner===agent-A；无主任务不属于任何会话，不得让它判忙');
});

test('★ 必须把 caller 传给 jobs.list()（不传就只拿回无主任务 → B1 等于没修）', () => {
  const records = [
    { id: 'j-own', owner: 'agent-A', status: 'running' },
    { id: 'j-none', owner: undefined, status: 'running' },
  ];
  const fnSrc = extractPendingJobsOf();
  const jobs = fakeJobs(records);
  const ctx = { get: (n) => (n === 'jobs' ? jobs : undefined) };
  // eslint-disable-next-line no-new-func
  const fn = new Function('ctx', fnSrc + '\nreturn pendingJobsOf;')(ctx);
  fn('agent-A');
  assert.deepEqual(jobs.calls, ['agent-A'],
    'jobs.list 的第一个参数必须是 caller（宿主按它做归属过滤）');
});

test('★ 无主任务不得让每个 agent 都"永久未完成"', () => {
  const records = [{ id: 'j-none', owner: undefined, status: 'running' }];
  for (const id of ['agent-A', 'agent-B', 'agent-C']) {
    const r = build(records, id);
    assert.deepEqual(r.pending, [],
      '无主任务不属于 ' + id + '，不得判它忙（否则父代理看到的是"全部未完成"）');
  }
});

test('终态 job 一律不算未 settle（与宿主 isTerminal 同口径）', () => {
  for (const st of ['completed', 'killed', 'failed', 'done', 'settled', 'cancelled']) {
    const r = build([{ id: 'j1', owner: 'agent-A', status: st }], 'agent-A');
    assert.deepEqual(r.pending, [], '终态 ' + st + ' 不得算未完成');
  }
});

test('running / stopping 才算未 settle', () => {
  const r = build([
    { id: 'j1', owner: 'agent-A', status: 'running' },
    { id: 'j2', owner: 'agent-A', status: 'stopping' },
  ], 'agent-A');
  assert.deepEqual(r.pending.map((j) => j.id), ['j1', 'j2']);
});

test('明细必须带 id/label/status（父代理要能读懂"是谁没干完"）', () => {
  const r = build([{ id: 'j1', owner: 'agent-A', status: 'running',
                     label: 'run6.log 建模', kind: 'command' }], 'agent-A');
  assert.equal(r.pending[0].id, 'j1');
  assert.equal(r.pending[0].label, 'run6.log 建模');
  assert.equal(r.pending[0].status, 'running');
  assert.equal(r.pending[0].kind, 'command');
});

test('jobs 服务不可用 → available=false（绝不假装"没有未完成 job"）', () => {
  const fnSrc = extractPendingJobsOf();
  const ctx = { get: () => undefined };
  // eslint-disable-next-line no-new-func
  const fn = new Function('ctx', fnSrc + '\nreturn pendingJobsOf;')(ctx);
  const r = fn('agent-A');
  assert.equal(r.available, false, '缺服务必须显式标注，不能静默当成"无未完成"');
  assert.deepEqual(r.pending, []);
  assert.match(String(r.reason), /jobs/);
});

test('jobs.list 抛异常 → 降级为 available=false，绝不冒泡', () => {
  const fnSrc = extractPendingJobsOf();
  const ctx = { get: () => ({ list() { throw new Error('boom'); } }) };
  // eslint-disable-next-line no-new-func
  const fn = new Function('ctx', fnSrc + '\nreturn pendingJobsOf;')(ctx);
  const r = fn('agent-A');
  assert.equal(r.available, false, '宿主探测异常不得把守卫带崩');
  assert.match(String(r.reason), /boom/);
});

test('缺 agentId → available=false（不猜归属）', () => {
  const r = build([{ id: 'j1', owner: 'agent-A', status: 'running' }], '');
  assert.equal(r.available, false);
});
