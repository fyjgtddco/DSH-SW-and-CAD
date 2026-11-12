/**
 * bug2-waiting-evidence-20261112.test.mjs — 【BUG-2 回归】[WAITING] 的四条证据必须都拿得到
 *
 * ── 现场（减速器任务 20261111_224045_56A62E32）──────────────────────────
 * 主对话 10+ 次写 `[WAITING]` 结束回合，每次都被守卫打回，即使当时真的在等：
 *   · 后台 job（wait-rooms）正在跑；
 *   · 宿主侧存在活跃提问卡（/pending-child 查得到 pendingId）；
 *   · workflow_state.json step=context_asked（挂 pending）。
 * 报告把根因写成"守卫的 [WAITING] 证据检测为半实现，只认房间心跳"。
 *
 * 实测根因更具体 —— 证据【采集端】拿不到，不是判定端写错：
 *   ① workflow_state 里 **没有** pending / awaiting 这些键（workflow_gate
 *      从来不写它们），判定端读 `wf.pending || wf.awaiting` 永远为空；
 *      真正的等待语义在 `wf.step === 'depth_asked' | 'context_asked'` 里。
 *   ② 未 settle job 依赖 jobs.list() 的 owner 字段，取不到就恒 0。
 *   ③ 提问卡只扫 childAsks 且按 owner 过滤，总计子代理提的卡父级数不到。
 *
 * 本测试钉死这四条通道，任何一条被改回"永远拿不到"都会立刻红。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { collectWaitingEvidence, activeRoomNamesOf } from '../lib/turn-guard.js';

// ══════════════════════════════════════════════════════════════════════════
// ① workflow_state 的等待态：step 就是门禁的等待语义
// ══════════════════════════════════════════════════════════════════════════
test('BUG-2 ①：step=context_asked 必须算"在等用户"（workflow 从不写 pending 键）', () => {
  const wf = { step: 'context_asked', task_id: 't1' };
  // 反证：这两个键在真实落盘文件里根本不存在
  assert.equal(wf.pending, undefined);
  assert.equal(wf.awaiting, undefined);

  const r = collectWaitingEvidence({ workflowState: wf });
  assert.equal(r.waiting, true, 'step=context_asked 必须放行');
  assert.ok(r.evidence.some((x) => x.includes('context_asked')),
    '证据里要点名 step，便于事后核对：' + JSON.stringify(r.evidence));
});

test('BUG-2 ①b：step=depth_asked 同样算在等', () => {
  const r = collectWaitingEvidence({ workflowState: { step: 'depth_asked' } });
  assert.equal(r.waiting, true);
  assert.ok(r.evidence.some((x) => x.includes('depth_asked')));
});

test('BUG-2 ①c：干活阶段的 step 不算在等（不能拿它逃避推进）', () => {
  for (const step of ['user_selected', 'design', 'modeling', 'finished', 'idle', '']) {
    const r = collectWaitingEvidence({ workflowState: { step } });
    const hit = r.evidence.some((x) => x.includes('门禁在等用户作答'));
    assert.equal(hit, false, `step=${step} 不该被当成"在等用户"`);
  }
});

test('BUG-2 ①d：旧写法（pending / awaiting）仍然兼容', () => {
  const a = collectWaitingEvidence({ workflowState: { pending: ['x'] } });
  assert.equal(a.waiting, true);
  const b = collectWaitingEvidence({ workflowState: { awaiting: 'user_answer' } });
  assert.equal(b.waiting, true);
});

// ══════════════════════════════════════════════════════════════════════════
// ② 后台 job：计数 > 0 就是证据
// ══════════════════════════════════════════════════════════════════════════
test('BUG-2 ②：有未 settle 后台 job → 算在等', () => {
  const r = collectWaitingEvidence({ unsettledJobs: 2 });
  assert.equal(r.waiting, true);
  assert.ok(r.evidence.some((x) => x.includes('后台 job') && x.includes('2')));
});

test('BUG-2 ②b：job 数为 0 / 非法值 → 不产生证据', () => {
  for (const v of [0, undefined, null, NaN, -1, 'abc']) {
    const r = collectWaitingEvidence({ unsettledJobs: v });
    assert.equal(r.evidence.some((x) => x.includes('后台 job')), false, 'v=' + String(v));
  }
});

// ══════════════════════════════════════════════════════════════════════════
// ③ 提问卡：有未答卡就是最干净的等待证据
// ══════════════════════════════════════════════════════════════════════════
test('BUG-2 ③：有未答提问卡 → 算在等', () => {
  const r = collectWaitingEvidence({ openQuestions: 1 });
  assert.equal(r.waiting, true);
  assert.ok(r.evidence.some((x) => x.includes('提问卡')));
});

// ══════════════════════════════════════════════════════════════════════════
// ④ 四条全无 → 必须 waiting:false（守卫不能被 [WAITING] 骗过去）
// ══════════════════════════════════════════════════════════════════════════
test('BUG-2 ④：四条证据一条都没有 → waiting=false（不削弱守卫）', () => {
  const r = collectWaitingEvidence({
    toolsDir: null,
    modeState: null,
    workflowState: { step: 'user_selected' },
    openQuestions: 0,
    unsettledJobs: 0,
  });
  assert.equal(r.waiting, false);
  assert.deepEqual(r.evidence, []);
});

test('BUG-2 ④b：workflowState 为 null 时不崩', () => {
  const r = collectWaitingEvidence({});
  assert.equal(r.waiting, false);
});

// ══════════════════════════════════════════════════════════════════════════
// 与"房间仍在世"的口径互不影响
// ══════════════════════════════════════════════════════════════════════════
test('BUG-2 ⑤：房间证据与本轮 task 对齐（跨任务残留不算证据）', () => {
  const modeState = {
    rooms: {
      本轮的屋: { active: true, task_id: 't1' },
      上轮的屋: { active: true, task_id: 't-old' },
    },
  };
  const names = activeRoomNamesOf(modeState, { task_id: 't1' });
  assert.deepEqual(names, ['本轮的屋']);
});
