/**
 * summarize.test.mjs — 摘要线协议与回填路径（脱离宿主的兜底实现）
 *
 * 宿主里用官方 BlockAssembler；这里专门验证「拿不到官方实现时」本地装配器
 * 是否等价，避免本地能跑、线上却错位。
 */

import test from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../lib/summarize.js');

/** 造一个假的 ctx.llm，按给定顺序吐 chunk。 */
function fakeCtx(chunks) {
  return {
    llm: {
      async *stream() {
        for (const c of chunks) yield c;
      },
    },
  };
}

function fakeAgent() {
  return {
    options: { provider: 'test-provider', model: 'test-model' },
    session: {
      id: 'sess-1',
      requestHeader: () => ({ config: { provider: 'test-provider', model: 'test-model' } }),
      toolHistory: () => ({}),
    },
  };
}

test('summarizeWithLlm: 拼接文本分片并返回元信息', async () => {
  const ctx = fakeCtx([
    { type: 'block-start', index: 0, blockType: 'text' },
    { type: 'text-delta', index: 0, text: '### L0 机械事实\n' },
    { type: 'text-delta', index: 0, text: '- 板厚 12mm' },
    { type: 'block-end', index: 0, block: { type: 'text', text: '### L0 机械事实\n- 板厚 12mm' } },
    { type: 'usage', usage: { inputTokens: 100, outputTokens: 20 } },
    { type: 'finish', reason: { kind: 'stop' } },
  ]);
  const r = await mod.summarizeWithLlm(ctx, { maxTokens: 4096 }, { messages: [] }, fakeAgent(), undefined, 'INSTR');
  assert.equal(r.provider, 'test-provider');
  assert.equal(r.model, 'test-model');
  assert.equal(r.llmStreamCall, true);
  assert.equal(r.summary.length, 1);
  assert.ok(r.summary[0].text.includes('12mm'));
  assert.deepEqual(r.usage, { inputTokens: 100, outputTokens: 20 });
});

test('summarizeWithLlm: 纯 delta 协议（无 block-end）也要能拼', async () => {
  const ctx = fakeCtx([
    { type: 'text-delta', index: 0, text: 'A' },
    { type: 'text-delta', index: 0, text: 'B' },
    { type: 'finish', reason: { kind: 'stop' } },
  ]);
  const r = await mod.summarizeWithLlm(ctx, {}, { messages: [] }, fakeAgent(), undefined, 'I');
  assert.equal(r.summary[0].text, 'AB');
});

test('summarizeWithLlm: 空输出必须报错', async () => {
  const ctx = fakeCtx([{ type: 'finish', reason: { kind: 'stop' } }]);
  await assert.rejects(
    () => mod.summarizeWithLlm(ctx, {}, { messages: [] }, fakeAgent(), undefined, 'I'),
    /no text summary content/,
  );
});

test('summarizeWithLlm: max-tokens 必须 fail-closed', async () => {
  const ctx = fakeCtx([
    { type: 'text-delta', index: 0, text: 'half' },
    { type: 'finish', reason: { kind: 'max-tokens' } },
  ]);
  await assert.rejects(
    () => mod.summarizeWithLlm(ctx, {}, { messages: [] }, fakeAgent(), undefined, 'I'),
    /truncated at the token cap/,
  );
});

test('summarizeWithLlm: 没有可用模型时报清晰错误', async () => {
  const ctx = fakeCtx([]);
  await assert.rejects(
    () => mod.summarizeWithLlm(ctx, {}, { messages: [] }, { options: {}, session: { id: 'x', requestHeader: () => undefined } }, undefined, 'I'),
    /no provider\/model available/,
  );
});

test('summarizeWithLlm: 指令作为最后一条 user 消息追加', async () => {
  let captured = null;
  const ctx = {
    llm: {
      async *stream(opts) {
        captured = opts;
        yield { type: 'text-delta', index: 0, text: 'ok' };
        yield { type: 'finish', reason: { kind: 'stop' } };
      },
    },
  };
  await mod.summarizeWithLlm(ctx, {}, { messages: [{ role: 'system', content: [{ type: 'text', text: 'S' }] }] }, fakeAgent(), undefined, 'MY-INSTRUCTION');
  assert.equal(captured.purpose, 'compaction', 'purpose 必须是 compaction');
  const last = captured.messages[captured.messages.length - 1];
  assert.equal(last.role, 'user');
  assert.equal(last.content[0].text, 'MY-INSTRUCTION');
  assert.equal(captured.messages.length, 2, '回放消息 + 指令');
});
