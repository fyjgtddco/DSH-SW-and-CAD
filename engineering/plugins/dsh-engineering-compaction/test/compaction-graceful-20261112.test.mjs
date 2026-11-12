/**
 * compaction-graceful-20261112.test.mjs — 【压缩不许报错】降级链的回归
 *
 * ── 现场 ────────────────────────────────────────────────────────────────
 * 用户点压缩后 GUI 弹：
 *   "Compaction could not produce a useful summary.
 *    The attempt is recorded in the session log."
 * 那句是宿主 dsh-compaction-basic:527 的
 *   ManualCompactionError("summary", "manual compaction could not produce
 *   a smaller summary")，由本插件 summarize() 抛出的【任何】异常包装而成。
 *
 * 结论：只要本插件的附加逻辑（L0 注入 / 回填）挂了，整次压缩就失败 ——
 *   用户失去的不只是"数值保证"，而是**普通摘要也用不上**，上下文继续涨。
 *
 * 本测试钉死降级链：
 *   ① L0 路径失败 → 退回基础摘要（压缩仍成功）
 *   ② 回填后反而更长 → 退回"仅模型摘要"（压缩仍成功）
 *   ③ 两级都失败 / 模型摘要也不短 → 才允许抛错
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';

import { restoreFacts } from '../lib/facts.js';

const LIB = path.join(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
  '..', 'lib');
const SRC = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8');
const SUM = fs.readFileSync(path.join(LIB, 'summarize.js'), 'utf8');

test('① 源码契约：summarizeWithLlm 调用必须被 try/catch 包住并有降级路径', () => {
  const i = SRC.indexOf('const result = await summarizeWithLlm(');
  // 允许 `result = await`（已去掉 const 的写法）
  const j = SRC.indexOf('await summarizeWithLlm(');
  const at = j >= 0 ? j : i;
  assert.ok(at > 0, '必须还在调 summarizeWithLlm');
  const before = SRC.slice(Math.max(0, at - 1500), at);
  assert.match(before, /try\s*\{/, '调用前必须有 try（否则一次失败就整次压缩报错）');
  const after = SRC.slice(at, at + 2500);
  assert.match(after, /catch\s*\(/, '必须有 catch 降级');
  assert.match(after, /buildBasicInstruction|basicInstruction/,
    '降级路径要改用基础指令（最短、最不易失败的提示词）');
});

test('①b 降级要走基础指令，且基础指令比完整指令短得多', () => {
  const m = SRC.match(/function buildBasicInstruction\(\)\s*\{[\s\S]*?\n\}/);
  assert.ok(m, '必须定义 buildBasicInstruction');
  const body = m[0];
  // 基础指令不该要求"逐行抄写每一个编号"（那正是被截断/空流的原因）
  assert.ok(!/EVERY F-number/i.test(body),
    '基础指令不得再要求逐条抄写（会撑爆输出上限）');
  assert.match(body, /VERBATIM|verbatim/, '但仍要求路径/数字原样保留');
});

test('② 源码契约：回填后不缩短时必须【退一档】而不是直接 throw', () => {
  assert.match(SRC, /ENG_COMPACTION_NOT_SMALLER/, '保留该错误码用于真正压不动的场景');
  // 退一档的判据：先把"仅模型摘要"和原文比，能更短就退
  assert.match(SRC, /const modelChars = modelText\.length/,
    '必须计算仅模型摘要的长度');
  assert.match(SRC, /modelChars > 0 && modelChars < inputChars/,
    '仅模型摘要更短时必须退一档，而不是整次压缩失败');
  assert.match(SRC, /仅模型摘要|restoreNote/, '退一档要有可识别的标记');
  // 退一档后不能在后面又被回填文本覆盖
  assert.match(SRC, /if \(!restoreNote\) summaryBlocks/, 
    '退一档后不得再被回填文本覆盖');
});

test('②b 降级链留痕：账本要有 degraded_reason（可审计）', () => {
  assert.match(SRC, /degraded_reason/, 'L0 降级要写进 compaction 账本');
  assert.match(SRC, /L3-degraded/, '降级记录的 layer 要可区分');
});

test('③ 双路径都失败才抛，且错误里带上两条路径的原因', () => {
  assert.match(SRC, /ENG_COMPACTION_BOTH_PATHS_FAILED/, '要有专门错误码');
  const i = SRC.indexOf('ENG_COMPACTION_BOTH_PATHS_FAILED');
  const around = SRC.slice(Math.max(0, i - 900), i + 200);
  assert.match(around, /L0 path error/, '错误信息必须带上第一级失败原因');
});

test('④ chunkChars 必须在 preset 里显式配置（否则 64000 默认值贴网关上限）', () => {
  const yml = path.join(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..', '..', 'dsh-engineering-ui', 'preset-engineering.patch.yml');
  const txt = fs.readFileSync(yml, 'utf8');
  const start = txt.indexOf('- id: compaction-engineering');
  const tail = txt.slice(start);
  const end = tail.indexOf('- id: command-compact');
  const block = (end > 0 ? tail.slice(0, end) : tail)
    .split(/\r?\n/).filter((l) => !/^\s*#/.test(l)).join('\n');
  const cc = block.match(/chunkChars:\s*(\d+)/);
  assert.ok(cc, '必须显式配 chunkChars');
  assert.ok(Number(cc[1]) <= 40000,
    'chunkChars 必须留余量（当前路由 maxTokens=128000，输入+输出不能贴边），实际 ' + cc[1]);
  const mt = block.match(/maxTokens:\s*(\d+)/);
  assert.ok(mt, '必须显式配 maxTokens');
  assert.ok(Number(mt[1]) <= 20000,
    'maxTokens 要与 chunkChars 一起留余量，实际 ' + mt[1]);
  // 输入 + 输出必须明显小于 128000
  const sum = Number(cc[1]) + Number(mt[1]);
  assert.ok(sum < 128000 * 0.75,
    'chunkChars + maxTokens 必须显著低于路由上限 128000，实际 ' + sum);
});

test('⑤ 阈值必须是用户指定的 200K（1M 窗口 × 0.2）', () => {
  const yml = path.join(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..', '..', 'dsh-engineering-ui', 'preset-engineering.patch.yml');
  const txt = fs.readFileSync(yml, 'utf8');
  const start = txt.indexOf('- id: compaction-engineering');
  const tail = txt.slice(start);
  const end = tail.indexOf('- id: command-compact');
  const block = (end > 0 ? tail.slice(0, end) : tail)
    .split(/\r?\n/).filter((l) => !/^\s*#/.test(l)).join('\n');
  const tr = block.match(/thresholdRatio:\s*([\d.]+)/);
  assert.ok(tr, '必须配 thresholdRatio');
  assert.equal(Number(tr[1]), 0.2, '用户要求 200K 阈值 → 1M × 0.2');
  assert.equal(Math.floor(1000000 * Number(tr[1])), 200000);
});

test('⑥ summarize.js 的 max-tokens finish 必须 fail-closed（不能被当成成功）', () => {
  assert.match(SUM, /max-tokens/, '必须处理 max-tokens 终态');
  const i = SUM.indexOf("case 'max-tokens'");
  assert.ok(i > 0);
  const around = SUM.slice(i, i + 300);
  assert.match(around, /truncated|MAX_TOKENS/, '截断必须变成错误，而不是静默成功');
});
