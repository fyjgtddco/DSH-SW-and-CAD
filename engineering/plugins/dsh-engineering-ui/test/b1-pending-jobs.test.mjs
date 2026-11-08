#!/usr/bin/env node
/**
 * 【B1 回归测试】后台 job 未 settle 时，子代理不得被当成"已完成"。
 *
 * 对应缺陷（01-缺陷清单与日志定位.md · B1，P0 · ★P2-②）：
 *
 *   现场（2026-11-07 会话 · 行走传动间 b55576ac）：
 *     :670  模型自述 "Log is 0 bytes (buffered). Build still ru…"
 *     :672  **turn/end {completed}**      ← 先报了"正常收工"
 *     :673  才是该 job 的 **job/settled**  ← 后台任务这时才结束
 *     :1133 父代理据此当完成
 *     :1389 总理 21 分钟后才发现 "run6.log 为 0 字节…未提交任何 prime-report"
 *
 * 判据（源码级护栏 + 行为级断言）：
 *   ① index.js 的 agent/turn-stopping 监听器里必须有 pendingJobsOf 探测；
 *   ② 探测到未完成 job 时必须写 pending_jobs / turn_completed_but_busy；
 *   ③ jobs 服务不可用时必须显式标注（pending_jobs_unavailable），不假装"没有 job"；
 *   ④ 绝不能因为"有未完成 job"就把 status 改成 completed —— 必须仍是 running。
 *
 * 用法：node --test test/b1-pending-jobs.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const IDX = path.resolve(HERE, '..', 'lib', 'index.js');
const SRC = fs.readFileSync(IDX, 'utf8');

test('① turn-stopping 监听器里有 pendingJobsOf 探测', () => {
  assert.ok(SRC.includes('function pendingJobsOf('), '必须定义 pendingJobsOf');
  assert.ok(SRC.includes('pendingJobsOf(agent.id)'),
    'turn-stopping 里必须真的调用它');
});

test('② 未完成 job 必须写入 pending_jobs 与 turn_completed_but_busy', () => {
  assert.ok(SRC.includes('rec.pending_jobs = pj.pending'),
    '必须把未完成 job 明细写进 platform 记录');
  assert.ok(SRC.includes('rec.turn_completed_but_busy'),
    '必须给出"回合结束但仍在忙"的显式标记');
  assert.ok(SRC.includes('rec.pending_job_count'), '必须给出数量');
});

test('③ jobs 服务不可用必须显式标注，不假装"没有 job"', () => {
  assert.ok(SRC.includes('pending_jobs_unavailable'),
    'jobs 不可用时要写明，不能静默当成"无未完成 job"');
  assert.ok(SRC.includes("available: false"), '探测结果要带 available 标志');
});

test('④ 有未完成 job 时状态仍必须是 running（不得伪造 completed）', () => {
  // 取 turn-stopping 监听到下一个监听器之间的那段源码
  // 注意：主 turn-stopping 监听器前面紧贴着 【B1】pendingJobsOf 的注释块，
  //   所以从 pendingJobsOf 定义处开始取，才能覆盖到真正的监听器体。
  const start = SRC.indexOf('function pendingJobsOf(');
  assert.ok(start > 0, '必须存在该监听器');
  const seg = SRC.slice(start, start + 4000);
  assert.ok(seg.includes("status: 'running'"),
    '回合结束 ≠ 死亡：必须保持 running');
  assert.ok(!/status:\s*'completed'/.test(seg),
    '不得在 turn-stopping 里把状态写成 completed');
});

test('⑤ 必须留下可读告警（不是只写文件）', () => {
  assert.ok(SRC.includes('回合结束但仍有'),
    '必须打日志告诉人"它还没真收工"');
  assert.ok(SRC.includes('（B1）'), '日志里标明缺陷编号，便于回溯');
});
