/**
 * summarize-chunking.test.mjs — 【CMP-OVERSIZE】压缩请求超上限必须自动分段
 *
 * 现场（2026-11-10 会话 57278639）：14 次 compaction 全部失败：
 *   "buddy: 内容过长，请精简或新建任务 prompt is too long:
 *    100001 tokens > 100000 maximum"
 * 根因：摘要请求把整段历史一次性发给模型，超过网关 prompt 上限。
 * 修法：summarizeWithLlm 按字符预算分段、段摘要再合并；仍超限则二分重试。
 *
 * 本测试用假 ctx.llm 模拟"请求超过 limit 就抛 prompt too long"，验证：
 *   ① 超长输入会被拆成多次请求（不会一上来就失败）；
 *   ② 单次请求超限时自动二分重试，最终成功；
 *   ③ 输入不超限时仍然只发一次请求（保持旧行为）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../lib/summarize.js');

function charsOf(messages) {
  let n = 0;
  for (const m of messages) {
    const c = m && m.content;
    if (typeof c === 'string') n += c.length;
    else if (Array.isArray(c)) for (const b of c) n += String((b && b.text) || '').length;
  }
  return n;
}

/** 假 ctx.llm：请求字符数超过 limit 时抛 prompt too long；否则回一段摘要。 */
function fakeCtx(limit, outText) {
  const calls = [];
  return {
    calls,
    llm: {
      async *stream(opts) {
        calls.push(charsOf(opts.messages || []));
        if (charsOf(opts.messages || []) > limit) {
          throw new Error('buddy: 内容过长，请精简或新建任务 prompt is too long: 100001 tokens > 100000 maximum');
        }
        yield { type: 'text-delta', index: 0, text: outText };
        yield { type: 'finish', reason: { kind: 'stop' } };
      },
    },
  };
}

function fakeAgent() {
  return {
    options: { provider: 'p', model: 'm' },
    session: {
      id: 'sess-1',
      requestHeader: () => ({ config: { provider: 'p', model: 'm' } }),
      toolHistory: () => ({}),
    },
  };
}

function bigMessages(totalChars) {
  const msgs = [];
  const chunk = 'x'.repeat(1000);
  let n = 0;
  while (n < totalChars) {
    msgs.push({ role: 'user', content: [{ type: 'text', text: chunk }] });
    n += chunk.length + 8;
  }
  return msgs;
}

test('① 超长输入按预算分段（不再是单发一次就失败）', async () => {
  const ctx = fakeCtx(1e9, '### L0 机械事实\n- 板厚 12mm');
  const r = await mod.summarizeWithLlm(ctx, { chunkChars: 8000 }, { messages: bigMessages(30000) }, fakeAgent(), undefined, 'INSTR');
  assert.equal(r.chunked, true);
  assert.ok(r.chunk_count >= 2, '至少 2 段: ' + r.chunk_count);
  assert.ok(ctx.calls.length >= 2, '至少 2 次请求: ' + ctx.calls.length);
  assert.ok(r.summary[0].text.includes('12mm'));
});

test('② 单次请求超限 → 自动二分重试直到成功', async () => {
  const ctx = fakeCtx(5000, 'OK-SEG');
  const r = await mod.summarizeWithLlm(ctx, { chunkChars: 8000 }, { messages: bigMessages(40000) }, fakeAgent(), undefined, 'INSTR');
  assert.ok(r.summary[0].text.includes('OK-SEG'));
  assert.ok(ctx.calls.length >= 3, '二分+分段后应多次请求: ' + ctx.calls.length);
});

test('③ 未超限时仍然只发一次请求（保持旧行为/KV 前缀缓存）', async () => {
  const ctx = fakeCtx(1e9, 'SMALL-OK');
  const r = await mod.summarizeWithLlm(ctx, {}, { messages: bigMessages(2000) }, fakeAgent(), undefined, 'INSTR');
  assert.equal(r.chunked, false);
  assert.equal(ctx.calls.length, 1, '只应有一次请求');
});
