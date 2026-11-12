/**
 * turn-guard.test.mjs — 【BUG-A1 / A11 / A14 / B1 修复】的回归测试
 * ==========================================================================
 * 被测对象是 lib/turn-guard.js（守卫的可判定事实层，纯函数、无副作用）。
 *
 * 为什么单独测这个模块而不是整份 index.js：
 *   · index.js 需要 shim 掉 6 个 @deepseek-ai 包才能跑（见 compatibility.test.mjs），
 *     一处 shim 漏了就是"加载失败"而不是断言失败，很难定位；
 *   · 本模块是这次四条修复里【唯一】需要"判据正确性"的部分 ——
 *     守卫文案、日志、接线都属于胶水，真正的对错在这几个判定函数里。
 *
 * 每个用例都对应日志里的一条实测现场（注释里给了出处），
 * 且【不允许放宽断言】——尤其"证据不足必须返回 false"那条，
 * 它正是"别让 [WAITING] 变成逃避推进的后门"的守门人。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  WAIT_EVIDENCE_WINDOW_MS,
  COMPACTION_THRESHOLD_RATIO,
  COMPACTION_THRESHOLD_FLOOR,
  COMPACTION_THRESHOLD_FALLBACK,
  hasStandaloneMarker,
  readJsonSafe,
  latestContextWindow,
  resolveCompactionThreshold,
  explainCompactionLimit,
  planPathCandidates,
  readAnalystSessionId,
  isAnalystAgent,
  activeRoomNamesOf,
  roomActivityEvidence,
  collectWaitingEvidence,
  describePendingJobs,
} from '../lib/turn-guard.js';

function tempDir(prefix) {
  const d = mkdtempSync(path.join(tmpdir(), prefix));
  return d;
}

// ══════════════════════════════════════════════════════════════════════════
//  A1 · [WAITING] 的文本判据
// ══════════════════════════════════════════════════════════════════════════

test('A1 · 标记必须【独占一行】才算数（散文里提一句无效）', () => {
  // 出处：守卫自己的 GUARD_TEXT 里就含 "[WAITING]" 字样，若做任意位置子串匹配，
  //   守卫提示被回灌进上下文后即可自我放行 —— 与 [TASK_DONE] 当年同一个坑。
  assert.equal(hasStandaloneMarker('我正在等，[WAITING]', '[WAITING]'), false,
    '嵌在句子里不得算数');
  assert.equal(hasStandaloneMarker('前面有字\n[WAITING] 后面也有字', '[WAITING]'), false,
    '同一行还有别的字不得算数');
  assert.equal(hasStandaloneMarker('[WAITING] 后面有字', '[WAITING]'), false,
    '标记后紧跟文字不得算数（契约是独占一行）');
  assert.equal(hasStandaloneMarker('[WAITING]', '[WAITING]'), true, '独占一行必须算数');
  assert.equal(hasStandaloneMarker('  [WAITING]\t ', '[WAITING]'), true,
    '前后仅空白（缩进/尾随空格）必须算数');
  assert.equal(hasStandaloneMarker('第一行\n[WAITING]\n第三行', '[WAITING]'), true);
  assert.equal(hasStandaloneMarker('', '[WAITING]'), false);
  assert.equal(hasStandaloneMarker(null, '[WAITING]'), false);
  // [TASK_DONE] 走同一个实现，语义不得漂移
  assert.equal(hasStandaloneMarker('[TASK_DONE]', '[TASK_DONE]'), true);
  assert.equal(hasStandaloneMarker('任务 [TASK_DONE] 完成', '[TASK_DONE]'), false);
});

// ══════════════════════════════════════════════════════════════════════════
//  A1 · 客观等待证据（"不信模型说什么"的守门人）
// ══════════════════════════════════════════════════════════════════════════

test('A1 · 没有任何证据时必须 waiting=false（[WAITING] 不能当后门）', () => {
  // 这是本组里【最重要】的一条：把 [WAITING] 写成"永远放行"就等于毁掉守卫。
  const dir = tempDir('tg-none-');
  try {
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: {} },
      workflowState: { step: 'design' },
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, false, '零证据必须不放行');
    assert.deepEqual(r.evidence, [], '证据列表必须为空');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · 只有 active 房间但心跳/报告都不新鲜 → 仍不放行（那叫卡住，不叫在等）', () => {
  const dir = tempDir('tg-stale-');
  try {
    const hb = path.join(dir, 'heartbeats');
    mkdirSync(hb, { recursive: true });
    const f = path.join(hb, '结构件.heartbeat');
    writeFileSync(f, '{}');
    // 30 分钟前的心跳：房间 active 但早就没动静了
    const old = (Date.now() - 30 * 60 * 1000) / 1000;
    utimesSync(f, old, old);
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: { '结构件': { active: true } } },
      workflowState: { step: 'design' },
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, false,
      'active 但 30 分钟没心跳 → 是卡住不是等待，必须继续催处置');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · active 房间 + 近 5 分钟心跳 → 放行，且证据里点出房间与文件', () => {
  const dir = tempDir('tg-live-');
  try {
    const hb = path.join(dir, 'heartbeats');
    mkdirSync(hb, { recursive: true });
    writeFileSync(path.join(hb, '结构件.heartbeat'), '{}');
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: { '结构件': { active: true } } },
      workflowState: { step: 'design' },
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, true, '新鲜心跳必须构成等待证据');
    assert.equal(r.evidence.length, 1);
    assert.match(r.evidence[0], /结构件/, '证据必须点出房间名');
    assert.match(r.evidence[0], /heartbeat/, '证据必须点出证据文件');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · 报告文件的新鲜度同样是证据（心跳缺失时不误杀）', () => {
  const dir = tempDir('tg-report-');
  try {
    const rp = path.join(dir, 'reports');
    mkdirSync(rp, { recursive: true });
    writeFileSync(path.join(rp, '行走传动间.report.json'), '{}');
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: { '行走传动间': { active: true } } },
      workflowState: {},
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, true, '报告新鲜也算"还在动"');
    assert.match(r.evidence[0], /report\.json/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · 上一轮任务的残留 active 房间不得冒充"在等"（与 detectTaskFinished 同口径）', () => {
  // 出处：mode_state.json 里躺着 3 个 10 月份任务的 active 房间，task_epoch 早已过期，
  //   守卫却据此永不放行 —— 这次修复的第一条纪律就是"只认本轮任务"。
  const dir = tempDir('tg-stale-task-');
  try {
    const hb = path.join(dir, 'heartbeats');
    mkdirSync(hb, { recursive: true });
    writeFileSync(path.join(hb, '旧房间.heartbeat'), '{}');   // 新鲜但属于别的任务
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: { '旧房间': { active: true, task_id: 'OLD_TASK' } } },
      workflowState: { step: 'design', task_id: 'NEW_TASK' },
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, false, '别的任务的房间不算本轮在等');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · 房间没有 task_id（升级前老数据）→ 保守按本轮处理', () => {
  const dir = tempDir('tg-legacy-');
  try {
    const hb = path.join(dir, 'heartbeats');
    mkdirSync(hb, { recursive: true });
    writeFileSync(path.join(hb, '结构件.heartbeat'), '{}');
    const r = collectWaitingEvidence({
      toolsDir: dir,
      modeState: { rooms: { '结构件': { active: true } } },   // 没有 task_id
      workflowState: { task_id: 'NEW_TASK' },
      openQuestions: 0,
      unsettledJobs: 0,
    });
    assert.equal(r.waiting, true, '无从判断归属时必须保守算本轮（宁拦不放）');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A1 · 未答提问卡是独立证据（人没答 → 模型只能等）', () => {
  const r = collectWaitingEvidence({ openQuestions: 2 });
  assert.equal(r.waiting, true);
  assert.match(r.evidence.join('；'), /2 张未答的提问卡/);
});

test('A1 · 未 settle 的后台 job 是独立证据（B1 同一份事实）', () => {
  const r = collectWaitingEvidence({ unsettledJobs: 1 });
  assert.equal(r.waiting, true);
  assert.match(r.evidence.join('；'), /1 个后台 job 未 settle/);
});

test('A1 · workflow_state 的 pending 标记是独立证据', () => {
  const a = collectWaitingEvidence({ workflowState: { pending: ['confirm-assembly'] } });
  assert.equal(a.waiting, true);
  assert.match(a.evidence.join('；'), /pending 标记/);
  const b = collectWaitingEvidence({ workflowState: { awaiting: 'user_answer' } });
  assert.equal(b.waiting, true);
  assert.match(b.evidence.join('；'), /awaiting=user_answer/);
  // 空数组不算"挂着 pending"
  const c = collectWaitingEvidence({ workflowState: { pending: [] } });
  assert.equal(c.waiting, false);
});

test('A1 · 证据窗口常量 = 5 分钟（与文案一致）', () => {
  assert.equal(WAIT_EVIDENCE_WINDOW_MS, 5 * 60 * 1000,
    '文案写的"近 5 分钟"必须与常量一致，否则守卫说的话和做的事不一样');
});

// ══════════════════════════════════════════════════════════════════════════
//  A11 · 总计子代理（analyst）身份
// ══════════════════════════════════════════════════════════════════════════

test('A11 · 命中 mode2_plan.analyst.session_id → 是 analyst', () => {
  const dir = tempDir('tg-analyst-');
  try {
    writeFileSync(path.join(dir, 'mode2_plan.json'), JSON.stringify({
      analyst: { type: 'analyst', session_id: 'sess-analyst-1' },
    }));
    const r = isAnalystAgent({ id: 'sess-analyst-1' }, dir, null);
    assert.equal(r.isAnalyst, true);
    assert.equal(r.info.source, 'mode2_plan.analyst.session_id');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A11 · 建模小屋（sessionId 不匹配）→ 不是 analyst，回退原文案', () => {
  const dir = tempDir('tg-analyst-no-');
  try {
    writeFileSync(path.join(dir, 'mode2_plan.json'), JSON.stringify({
      analyst: { session_id: 'sess-analyst-1' },
    }));
    const r = isAnalystAgent({ id: 'sess-builder-9' }, dir, null);
    assert.equal(r.isAnalyst, false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A11 · 只做严格相等，不得用"子串包含"判身份（误判方向最危险）', () => {
  // 原实现用 sid.indexOf(bind) >= 0：短 id 是长 id 的子串就判成同一人。
  // 误判成 analyst 会把建模小屋的文案换成"你是总计子代理，别调 sw_bridge"，
  // 正好把它推进空转 —— 比不判角色更坏。
  const dir = tempDir('tg-analyst-sub-');
  try {
    writeFileSync(path.join(dir, 'mode2_plan.json'), JSON.stringify({
      analyst: { session_id: 'abc' },
    }));
    assert.equal(isAnalystAgent({ id: 'abcdef' }, dir, null).isAnalyst, false,
      'abcdef 不得因为包含 abc 就被判成同一人');
    assert.equal(isAnalystAgent({ id: 'xabcx' }, dir, null).isAnalyst, false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A11 · 取不到身份 → false（保守回退，不猜）', () => {
  const dir = tempDir('tg-analyst-none-');
  try {
    // 没有任何 mode2_plan.json
    const r = isAnalystAgent({ id: 'sess-1' }, dir, null);
    assert.equal(r.isAnalyst, false);
    assert.equal(r.info.sessionId, null);
    // 空 plan
    writeFileSync(path.join(dir, 'mode2_plan.json'), '{}');
    assert.equal(isAnalystAgent({ id: 'sess-1' }, dir, null).isAnalyst, false);
    // plan 里 analyst 为 null（_fresh_plan 的默认值）
    writeFileSync(path.join(dir, 'mode2_plan.json'), JSON.stringify({ analyst: null }));
    assert.equal(isAnalystAgent({ id: 'sess-1' }, dir, null).isAnalyst, false);
    // 坏 JSON 不得抛
    writeFileSync(path.join(dir, 'mode2_plan.json'), '{ not json');
    assert.equal(isAnalystAgent({ id: 'sess-1' }, dir, null).isAnalyst, false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('A11 · 任务目录（workflow_state.work_dir）优先于 toolsDir', () => {
  // 出处：mode2_flow._plan_dir() 默认把 mode2_plan.json 写进【当前任务目录】，
  //   而不是 tools 目录 —— 原实现只找 toolsDir，所以真实任务里几乎恒 false。
  const tools = tempDir('tg-plan-tools-');
  const wd = tempDir('tg-plan-wd-');
  try {
    writeFileSync(path.join(tools, 'mode2_plan.json'), JSON.stringify({
      analyst: { session_id: 'stale-analyst' },
    }));
    writeFileSync(path.join(wd, 'mode2_plan.json'), JSON.stringify({
      analyst: { session_id: 'live-analyst' },
    }));
    const r = readAnalystSessionId(tools, { work_dir: wd });
    assert.equal(r.sessionId, 'live-analyst', '任务目录里的 plan 才是当前真相');
    const cands = planPathCandidates(tools, { work_dir: wd });
    assert.equal(cands[0], path.join(wd, 'mode2_plan.json'));
  } finally {
    rmSync(tools, { recursive: true, force: true });
    rmSync(wd, { recursive: true, force: true });
  }
});

test('A11 · 任务目录缺失时回落到 toolsDir / load_cases（mode2_flow 的默认落点）', () => {
  const tools = tempDir('tg-plan-fallback-');
  try {
    const lc = path.join(tools, 'load_cases');
    mkdirSync(lc, { recursive: true });
    writeFileSync(path.join(lc, 'mode2_plan.json'), JSON.stringify({
      analyst: { session_id: 'sess-lc' },
    }));
    const r = readAnalystSessionId(tools, { work_dir: path.join(tools, 'no-such-dir') });
    assert.equal(r.sessionId, 'sess-lc');
  } finally { rmSync(tools, { recursive: true, force: true }); }
});

// ══════════════════════════════════════════════════════════════════════════
//  A14 · 压缩阈值 = contextWindow × ratio（ratio 默认 0.8，与官方/preset 平齐）
// ══════════════════════════════════════════════════════════════════════════
// 【2026-11-12】ratio 从 0.7 改成 0.8 后，下面这些断言不能再写死 140000/700000。
// 改成【按 COMPACTION_THRESHOLD_RATIO 现算】，这样以后调 ratio 不用再逐个改
// 测试数字（同时也避免"测试跟着实现改、改到失去意义"）。
const RATIO = COMPACTION_THRESHOLD_RATIO;

test('A14 · 200K 窗口 → 受 60K 下限保护（ratio 0.2 时 200K×0.2=40K < 60K）', () => {
  // 【2026-11-12】ratio 改成 0.2 后，200K 窗口算出 40000，低于 60000 下限，
  //   所以抬到下限并标 source=floor。这是【设计的保护】而不是回归：
  //   小窗口按 0.2 压得太频繁（每 40K 就压一次），下限把它挡住。
  //   用户要的"200K 阈值"针对的是 1M 窗口的主路由，见下一条测试。
  const r = resolveCompactionThreshold(200000);
  assert.equal(r.threshold, COMPACTION_THRESHOLD_FLOOR);
  assert.equal(r.contextWindow, 200000);
  assert.equal(r.source, 'floor');
  assert.equal(r.exceedsWindow, false, '60K < 200K，阈值仍可达到');
});

test('A14 · 1M 窗口 → 200K 阈值（用户指定，不要 800K）', () => {
  assert.equal(resolveCompactionThreshold(1000000).threshold, 200000,
    '1M 窗口下阈值必须正好是 200K');
  assert.equal(RATIO, 0.2);
});

test('A14 · 小窗口有下限：64K → 抬到 60K，且标记 source=floor', () => {
  const r = resolveCompactionThreshold(64000);
  assert.equal(r.threshold, COMPACTION_THRESHOLD_FLOOR,
    '64K*' + RATIO + ' 太频，必须抬到下限');
  assert.equal(r.source, 'floor');
  assert.equal(COMPACTION_THRESHOLD_FLOOR, 60000);
  assert.equal(r.exceedsWindow, false, '60K < 64K，阈值仍可达到');
  // 32K 窗口同理
  assert.equal(resolveCompactionThreshold(32000).threshold, 60000);
});

test('A14 · 窗口小于下限时必须标 exceedsWindow（否则阈值永远达不到、压缩成死配置）', () => {
  // 32K*0.7 = 22.4K，抬到 60K 后【高于窗口本身】→ 压缩永不触发，
  // 上下文会一路撞 CONTEXT_WINDOW_EXCEEDED。这个退化必须被如实标出来，
  // 而不是"配了个不生效的值"却没人知道。
  const r = resolveCompactionThreshold(32000);
  assert.equal(r.exceedsWindow, true);
  const ok = resolveCompactionThreshold(64000);
  assert.equal(ok.exceedsWindow, false);
});

test('A14 · 刚好在边界上：窗口×ratio 高于下限 → 用比例值', () => {
  // ratio 0.2 时需要一个足够大的窗口才会走比例项：500000×0.2=100000 > 60000
  const r = resolveCompactionThreshold(500000);
  assert.equal(r.threshold, Math.floor(500000 * RATIO));
  assert.equal(r.source, 'contextWindow');
});

test('A14 · 拿不到 contextWindow → 回退原值并标 fallback（不崩）', () => {
  for (const v of [null, undefined, 0, -1, NaN, '', 'abc']) {
    const r = resolveCompactionThreshold(v);
    assert.equal(r.threshold, COMPACTION_THRESHOLD_FALLBACK,
      '非法输入必须回退，不得崩：' + String(v));
    assert.equal(r.source, 'fallback');
    assert.equal(r.contextWindow, null);
  }
  assert.equal(COMPACTION_THRESHOLD_FALLBACK, 100000, '回退值就是历史行为 ~100K');
});

test('A14 · 从 request/context 事件里取 contextWindow（真实日志形状）', () => {
  // 逐字取自 test/log/session.v4.jsonl:224
  const events = [
    { type: 'request/context', seq: 12, data: { provider: 'raccoon', model: 'sn-x', contextWindow: 256000 } },
    { type: 'assistant/message', seq: 20, data: {} },
    { type: 'request/context', seq: 222, data: { provider: 'trae', model: 'mimo', contextWindow: 200000 } },
  ];
  assert.equal(latestContextWindow(events), 200000, '必须取【最后一条】，别用会话开头的旧值');
  assert.equal(latestContextWindow([], { contextWindow: 128000 }), 128000, '事件里没有就读 header');
  assert.equal(latestContextWindow([], {}), null);
  assert.equal(latestContextWindow(null, null), null);
  // 兼容 snake_case
  assert.equal(latestContextWindow([{ type: 'request/context', data: { context_window: 64000 } }]), 64000);
});

test('A14 · 复刻宿主公式：只调 ratio 可能被【预算项】压住（200K + 48K headroom → 86464）', () => {
  // 宿主源码 @deepseek-ai/dsh-compaction-basic/lib/index.js:124-147：
  //   messageBudget  = contextWindow - reservedCompletionTokens
  //   pressureBudget = messageBudget  - policy.headroomTokens
  //   thresholdTokens = min(contextWindow*thresholdRatio, pressureBudget)
  // reservedCompletionTokens = header.config.maxTokens ?? defaultMaxTokens ?? 0
  const e = explainCompactionLimit(200000, 0.7, 48000, 65536);
  assert.equal(e.limitRatioTokens, 140000, '比例项 = 200000 × 0.7');
  assert.equal(e.limitBudgetTokens, 86464, '预算项 = 200000 − 65536 − 48000');
  assert.equal(e.limiter, 'budget', '被预算项压住 —— 只改 ratio 反而更早压缩');
  assert.equal(e.threshold, 86464);
});

test('A14 · reserved=0 时比例项生效，阈值就是 140000', () => {
  const e = explainCompactionLimit(200000, 0.7, 48000, 0);
  assert.equal(e.limitBudgetTokens, 152000);
  assert.equal(e.limiter, 'ratio');
  assert.equal(e.threshold, 140000);
});

test('A14 · explainCompactionLimit 对非法输入不崩', () => {
  const e = explainCompactionLimit(null, 0.7, 48000, 0);
  assert.equal(e.threshold, null);
  assert.equal(e.limiter, 'window');
  // 【2026-11-12】非法 ratio 现在回落到 COMPACTION_THRESHOLD_RATIO（0.8），
  // 不是硬编码 0.7；headroom 缺省回落到 preset 的 headroomTokens（不再按 0）。
  const f = explainCompactionLimit(200000, NaN, NaN, NaN);
  assert.equal(f.threshold, Math.floor(200000 * RATIO),
    '非法 ratio 应回落当前生效的 ratio（与 preset 一致）');
});

test('A14 · 端到端：1M 窗口（当前主路由）→ 200K 阈值', () => {
  const events = [{ type: 'request/context', data: { contextWindow: 1000000 } }];
  const r = resolveCompactionThreshold(latestContextWindow(events));
  assert.equal(r.threshold, 200000, '用户指定：1M 窗口压到 200K 就该触发');
});

// ══════════════════════════════════════════════════════════════════════════
//  B1 · 未 settle 后台 job 的可读描述
// ══════════════════════════════════════════════════════════════════════════

test('B1 · 描述必须给出"有 N 个后台 job 未完成：<名字>"', () => {
  // 出处：b55576ac:670 "Log is 0 bytes (buffered). Build still ru…" → :672
  //   turn/end{completed} → :673 才 job/settled；父代理据此当完成。
  const s = describePendingJobs([
    { id: 'j1', label: 'run6.log 建模' },
    { id: 'j2', label: 'pwsh-abc123' },
  ]);
  assert.match(s, /^有 2 个后台 job 未完成：/);
  assert.match(s, /run6\.log 建模/);
  assert.match(s, /pwsh-abc123/);
});

test('B1 · 名字缺失时退回 id，绝不能输出 undefined', () => {
  const s = describePendingJobs([{ id: 'job-x' }, { label: '有名字的' }]);
  assert.equal(s.indexOf('undefined'), -1, '不得出现 undefined');
  assert.equal(s.indexOf('null'), -1, '不得出现 null');
  assert.match(s, /job-x/);
});

test('B1 · 空列表返回空串（调用方据此判断"没有未完成"）', () => {
  assert.equal(describePendingJobs([]), '');
  assert.equal(describePendingJobs(null), '');
  assert.equal(describePendingJobs(undefined), '');
});

test('B1 · 超过 6 个只列前 6 个并加"等"，避免刷屏', () => {
  const many = Array.from({ length: 9 }, (_, i) => ({ id: 'j' + i }));
  const s = describePendingJobs(many);
  assert.match(s, /^有 9 个后台 job 未完成：/);
  assert.match(s, /等$/);
});

// ══════════════════════════════════════════════════════════════════════════
//  公共小工具
// ══════════════════════════════════════════════════════════════════════════

test('readJsonSafe 对缺失/坏文件返回 null，绝不抛', () => {
  const dir = tempDir('tg-json-');
  try {
    assert.equal(readJsonSafe(path.join(dir, 'nope.json')), null);
    assert.equal(readJsonSafe(null), null);
    assert.equal(readJsonSafe(''), null);
    const bad = path.join(dir, 'bad.json');
    writeFileSync(bad, '{oops');
    assert.equal(readJsonSafe(bad), null);
    const ok = path.join(dir, 'ok.json');
    writeFileSync(ok, JSON.stringify({ a: 1 }));
    assert.deepEqual(readJsonSafe(ok), { a: 1 });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('activeRoomNamesOf 只返回 active 且属本轮的房间', () => {
  const ms = {
    rooms: {
      A: { active: true, task_id: 'T1' },
      B: { active: false, task_id: 'T1' },
      C: { active: true, task_id: 'T0' },
      D: { active: true },
    },
  };
  assert.deepEqual(activeRoomNamesOf(ms, { task_id: 'T1' }).sort(), ['A', 'D']);
  assert.deepEqual(activeRoomNamesOf(null, null), []);
});

test('roomActivityEvidence 对中文房间名与特殊字符都能定位文件', () => {
  const dir = tempDir('tg-roomname-');
  try {
    const hb = path.join(dir, 'heartbeats');
    mkdirSync(hb, { recursive: true });
    writeFileSync(path.join(hb, '总装与验证间.heartbeat'), '{}');
    const out = roomActivityEvidence(dir, ['总装与验证间'], Date.now(), WAIT_EVIDENCE_WINDOW_MS);
    assert.equal(out.length, 1, '中文房间名必须能对上心跳文件（mode_gate 的命名约定）');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
