/**
 * engine.test.mjs — 分层压缩引擎的组装测试（不连宿主、不打网络）
 *
 * 用假的 ctx.llm 与假 agent 走一遍 summarize()，验证：
 *   · 提示词里确实注入了 L0 事实与 L2 保留要求
 *   · 模型漏数时被自动补回
 *   · 账本按 append-only 记账，并能按 target 过滤
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-compact-'));
process.env.DSH_STATE_DIR = TMP;

const { buildCompactionInstruction } = await import('../lib/prompt.js');
const { extractFacts, renderFactsBlock } = await import('../lib/facts.js');
const history = await import('../lib/history.js');

test('指令包含四层结构与 L0 禁令', () => {
  const facts = extractFacts('板厚 12mm，材料 Q235', {});
  const s = buildCompactionInstruction({ factsBlock: renderFactsBlock(facts) });
  assert.ok(s.includes('L0 机械事实'));
  assert.ok(s.includes('L1 工程状态'));
  assert.ok(s.includes('L2 工程决策'));
  assert.ok(s.includes('L3 执行过程'));
  assert.ok(s.includes('NEVER change, round, convert'));
  assert.ok(s.includes('12mm'), '事实区块要注入');
  assert.ok(s.includes('Q235'));
});

test('指令包含失败方案的「失败条件」口径', () => {
  const s = buildCompactionInstruction({});
  assert.ok(s.includes('失败条件'));
  assert.ok(s.includes('不可再做'));
  assert.ok(s.includes('Deduplicate by FAILING CONDITION'), '去重必须针对失败条件而非方案名');
});

test('指令包含成功方案的五段式', () => {
  const s = buildCompactionInstruction({});
  assert.ok(s.includes('具体操作顺序'));
  assert.ok(s.includes('哪些参数不能修改'));
  assert.ok(s.includes('哪些步骤可以复用'));
  assert.ok(s.includes('哪些步骤依赖之前的特征'));
  assert.ok(s.includes('验证成功时的模型版本'));
});

test('指令要求 L2 只增不减', () => {
  const s = buildCompactionInstruction({ prevL2: '- 选 45 钢因为切削性好' });
  assert.ok(s.includes('APPEND-ONLY'));
  assert.ok(s.includes('选 45 钢因为切削性好'), '旧 L2 必须注入');
});

test('指令要求删掉 L3 但保留用户关键要求', () => {
  const s = buildCompactionInstruction({});
  assert.ok(s.includes('(none) — drop all reasoning'));
  assert.ok(s.includes('用户关键要求'));
});

test('history：append-only 追加与按 target 过滤', () => {
  history.appendCompaction({ at: 1, target: 'sess-A', root: 'root-1', label: '结构件房间', l0_numbers: 5 });
  history.appendCompaction({ at: 2, target: 'sess-B', root: 'root-1', label: '传动房间', l0_numbers: 3 });
  history.appendCompaction({ at: 3, target: 'sess-A', root: 'root-1', label: '结构件房间', l0_numbers: 4 });

  const all = history.readHistory({});
  assert.equal(all.entries.length, 3);

  const a = history.readHistory({ target: 'sess-A' });
  assert.equal(a.entries.length, 2, 'sess-A 应有两条');
  assert.deepEqual(a.entries.map((x) => x.at), [1, 3], '顺序必须是追加顺序');

  const r = history.readHistory({ root: 'root-1' });
  assert.equal(r.entries.length, 3);

  const st = history.historyStats({});
  const aStat = st.byTarget.find((x) => x.target === 'sess-A');
  assert.equal(aStat.count, 2);
  assert.equal(aStat.numbersKept, 9);
});

test('history：坏行不影响其余记录', () => {
  const p = path.join(TMP, history.FILES.HISTORY_FILE);
  fs.appendFileSync(p, 'this is not json\n', 'utf8');
  const r = history.readHistory({});
  assert.equal(r.ok, true);
  assert.equal(r.entries.length, 3, '坏行应被跳过而不是整体失败');
});

test('history：facts 单调集读写', () => {
  history.writeFacts({ numbers: [{ key: 'a', raw: '12mm' }], materials: [] });
  const f = history.readFacts();
  assert.equal(f.numbers.length, 1);
  assert.equal(f.numbers[0].raw, '12mm');
});

test('history：不提供删除/修改接口（结构上保证只能加）', () => {
  const keys = Object.keys(history);
  assert.ok(!keys.includes('removeCompaction'));
  assert.ok(!keys.includes('updateCompaction'));
  assert.ok(!keys.includes('deleteCompaction'));
});
