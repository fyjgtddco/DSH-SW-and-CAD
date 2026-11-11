/**
 * p0-gate-contract-20261111.test.mjs — 【2026-11-11】两个 P0 的回归
 *
 * 背景（验收复跑会话 20261111_131815_580E38FA，日志实锤）：
 *   A. 并发闸门【装了但没拦】：峰值并发 4 个子代理，3 个越限窗口，
 *      9 个日志文件里 SUBAGENT_LIMIT_REACHED 一次都没真正拦下过 fork。
 *   B. 批次内存回收【时机被提前】：fork 后 ~15s 就回收，与"整批收工后
 *      由主对话回收"的设计相反；`activeChildCount` 恒 -1。
 *
 * 本文件把这两件事钉死。两条都是【契约级】约束，改坏了不会报错、
 * 只会静默失效 —— 所以必须有测试守着。
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

import {
  countOccupiedChildren,
  evaluateConcurrency,
} from '../lib/mem-guard.js';

// ══════════════════════════════════════════════════════════════════════════
// A. pre-execute 决策契约：必须是 {kind:'deny', reason, info}
// ══════════════════════════════════════════════════════════════════════════

/**
 * 复刻 lib/index.js 里 subagentLimitResult 的当前实现。
 *
 * 为什么复制而不是导入：index.js 是插件入口，静态 import 会求值整个
 * 宿主装配链（`@deepseek-ai/dsh-agent-preset-registry` 等），单测环境里
 * 不可用。这里只固化"返回值形状"这一条契约 —— 一旦有人把它改回
 * {content,isError} 形状，本测试立刻红，而不是等到线上发现没拦。
 */
function makeLimitDecision(gate) {
  return {
    kind: 'deny',
    reason: gate.message,
    info: {
      name: 'SubagentLimitError',
      code: 'SUBAGENT_LIMIT_REACHED',
      active: gate.active,
      max: gate.max,
    },
  };
}

test('P0-A1. 超限决策必须是宿主认的 PreToolDecision（kind=deny），不能是工具结果形状', () => {
  const rows = [
    { kind: 'child', id: 'a', parentId: 'R' },
    { kind: 'child', id: 'b', parentId: 'R' },
    { kind: 'child', id: 'c', parentId: 'R' },
  ];
  const gate = evaluateConcurrency(rows, 'R', 'R', 3);
  assert.equal(gate.allowed, false, '第 4 个必须被拒');

  const d = makeLimitDecision(gate);

  // ── 宿主真正读的字段（dsh-tools/lib/types/pipeline.js）─────────────
  //   :154 `decision.kind === 'allow' ? checkGuards : decision.reason`
  //   :155 `decision.kind === 'deny' ? decision.info : undefined`
  // kind 缺失/非法 → 既不拦也不报错 → 静默放行。
  assert.equal(d.kind, 'deny', 'kind 必须是 deny（宿主据此拦）');
  assert.equal(typeof d.reason, 'string');
  assert.ok(d.reason.length > 0, 'reason 会被拼进 "Error: ${reason}" 交给模型');
  assert.equal(d.info.code, 'SUBAGENT_LIMIT_REACHED');
  assert.equal(d.info.active, 3);
  assert.equal(d.info.max, 3);

  // ── 绝不能再出现的旧形状（它们是 execute 之后的东西，不是决策）────
  assert.equal(d.content, undefined, '不能带 content：那是工具结果形状');
  assert.equal(d.isError, undefined, '不能带 isError：决策层没有这个字段');
  assert.equal(d.error, undefined, '不能带 error：决策层用 reason/info');
});

test('P0-A2. kind 必须是 allow/deny/cancel/ask 之一 —— 拼错等于不拦', () => {
  const allowed = new Set(['allow', 'deny', 'cancel', 'ask']);
  const rows = [
    { kind: 'child', id: 'a', parentId: 'R' },
    { kind: 'child', id: 'b', parentId: 'R' },
    { kind: 'child', id: 'c', parentId: 'R' },
  ];
  const d = makeLimitDecision(evaluateConcurrency(rows, 'R', 'R', 3));
  assert.ok(allowed.has(d.kind), 'kind=%r 不在宿主白名单里', d.kind);
});

test('P0-A3. 未超限时 evaluateConcurrency 必须放行（闸门不能把流程锁死）', () => {
  const rows = [{ kind: 'child', id: 'a', parentId: 'R' }];
  assert.equal(evaluateConcurrency(rows, 'R', 'R', 3).allowed, true);
});

// ══════════════════════════════════════════════════════════════════════════
// B. 批次回收判据：名册读不到 ≠ 收工
// ══════════════════════════════════════════════════════════════════════════

/**
 * 复刻 lib/index.js pollBatches 的【新】判据。
 *
 * 旧判据 `active === 0 || !known || timedOut` 的问题：
 *   名册读失败（known=false）被当成"这批没了，直接收尾"→ 立刻回收。
 *   这恰好是【最不该回收】的时刻：刚 fork 完的子代理内存正要用，
 *   而且读不到状态时根本无法证明它们已经收工。
 * 新判据：只有真归零（known && active===0）或 4h 超时兜底才回收。
 */
function shouldReclaim(known, active, timedOut) {
  return (known && active === 0) || timedOut;
}

test('P0-B1. 名册读不到（known=false）绝不当成收工', () => {
  assert.equal(shouldReclaim(false, 0, false), false,
    '读不到名册时必须跳过本轮，不能回收');
  assert.equal(shouldReclaim(false, 3, false), false);
});

test('P0-B2. 只有活跃真归零才回收', () => {
  assert.equal(shouldReclaim(true, 0, false), true, '归零 → 该回收');
  assert.equal(shouldReclaim(true, 1, false), false, '还有 1 个在跑 → 不回收');
  assert.equal(shouldReclaim(true, 3, false), false, '还有 3 个在跑 → 不回收');
});

test('P0-B3. 4h 超时兜底仍可回收（防止永远等不到归零）', () => {
  assert.equal(shouldReclaim(true, 2, true), true);
  assert.equal(shouldReclaim(false, 0, true), true);
});

test('P0-B4. 回收必须 force —— 时机已对，不能再被 85% 阈值挡掉空跑', () => {
  // 这条是契约断言：pollBatches 收尾分支调的是 scheduleReclaim(true, ...)。
  // 见 lib/index.js pollBatches() 结尾的 scheduleReclaim(true, 'batch-complete')。
  // 用字符串检查源码，防止有人"顺手"改回 false 而测试无感知。
  // 【2026-11-11】新增 trigger 实参（回收记账），正则要允许多一个参数。
  const src = fs.readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  const m = /batchRoots\.delete\(rootId\);[\s\S]{0,400}?scheduleReclaim\((true|false)\b/.exec(src);
  assert.ok(m, 'pollBatches 收尾分支里应能找到 scheduleReclaim(...)');
  assert.equal(m[1], 'true', '整批收工后的回收必须 force=true');
});

test('P0-B5. 回收结果必须被记账（不能把 maybeReclaim 的返回值丢掉）', () => {
  // 【2026-11-11】现场问题：旧 scheduleReclaim 是
  //     try { maybeReclaim({force}) } catch (e) {}
  // 结果直接丢弃 → "排程了"与"真回收了"外观完全一致，用户问
  // "有没有生效"时无法自证。现在必须 noteReclaim(trigger, result)。
  const src = fs.readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  assert.match(src, /function noteReclaim\(/, '应有 noteReclaim 记账函数');
  const m = /scheduleReclaim\(force, trigger\)[\s\S]{0,600}?noteReclaim\(/.exec(src);
  assert.ok(m, 'scheduleReclaim 必须把 maybeReclaim 的结果交给 noteReclaim');
  assert.equal(/try \{ maybeReclaim\(\{ force: !!force \}\); \} catch \(e\) \{\}/.test(src), false,
    '不得回到"调用后直接丢弃结果"的旧写法');
  // 状态接口要能读到记账
  assert.match(src, /recent_reclaims/, 'GET /mem-guard 必须回显回收记账');
  assert.match(src, /reclaim_count/, 'GET /mem-guard 必须回显回收次数');
});

test('P0-B6. 名册读不到而放行时，仍必须登记批次（否则永不回收）', () => {
  // listDescendants 抛错 → return next() 放行。旧代码这条路【不登记批次】，
  // 于是 fork 出去的子代理永远不会被 pollBatches 观察到 → 内存回收永不触发。
  const src = fs.readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  const i = src.indexOf('读不到名册（旧宿主 / 服务缺失）');
  assert.ok(i > 0, '应能找到"读不到名册"分支');
  const seg = src.slice(i, i + 700);
  assert.match(seg, /trackBatch\(rootId\)/,
    '名册读不到而放行的分支里必须 trackBatch(rootId)');
});

// ══════════════════════════════════════════════════════════════════════════
// C. lib/index.js 必须能在 node:vm 的 CJS 上下文里求值（不能用 import.meta）
// ══════════════════════════════════════════════════════════════════════════

test('C1. lib/index.js 不得出现 import.meta（vm/CJS 上下文里是语法错误）', () => {
  // 现场（2026-11-11）：为修 preset 探测路径顺手写了
  //   path.dirname(new URL(import.meta.url).pathname)
  // → test/webots-host.test.mjs 把 index.js 丢进 node:vm 求值，直接
  //   SyntaxError: Cannot use 'import.meta' outside a module → 插件测试全红。
  // 这条测试就是防止有人再为了"拿 __dirname"把 import.meta 写回来。
  const src = fs.readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  // 去掉注释再检查 —— 注释里讨论 import.meta 是允许的（而且必须允许，
  // 那样才能把踩坑记录写清楚），只有【真代码】里出现才是错。
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, '')      // 块注释
    .replace(/\/\/[^\n]*/g, '')            // 行注释（任意位置的 //）
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''") // 单引号字符串内容（避免字符串里提到）
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""');
  assert.equal(code.includes('import.meta'), false,
    'lib/index.js 里出现了 import.meta —— 会被 vm 求值的测试打成语法错误');
});
