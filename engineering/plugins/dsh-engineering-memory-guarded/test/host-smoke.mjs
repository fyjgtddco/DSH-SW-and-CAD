import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { tmpdir } from 'node:os';
import { setImmediate as tick } from 'node:timers/promises';
import { Context } from '@deepseek-ai/cordis';
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session';
import SessionProjection from '@deepseek-ai/dsh-session-projection';
import * as Persistence from '@deepseek-ai/dsh-session-persistence-jsonl';
import AgentRegistry from '@deepseek-ai/dsh-agent';
import AgentLoop from '@deepseek-ai/dsh-agent-loop';
import SystemPrompt from '@deepseek-ai/dsh-system-prompt';
import Tools from '@deepseek-ai/dsh-tools';
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native';
import Llm, { LlmAdapter, createUserMessage } from '@deepseek-ai/dsh-llm';
import TokenMeter from '@deepseek-ai/dsh-token-meter';
import * as Compactor from '../lib/guarded.js';
import { openJournal, SUMMARY_TITLE } from '../lib/guarded-memory.js';

const root = await mkdtemp(join(tmpdir(), 'dsh-memory-native-'));
process.env.DSH_STATE_DIR = join(root, 'ledger');
let ctx;
const received = [];
class FakeModel extends LlmAdapter {
  async resolveModel(provider, model) {
    return { provider, id: model, name: model, context: { contextWindow: 8192 } };
  }
  async *stream(options) {
    received.push(options);
    const output = '测试研制结果。'.repeat(650);
    yield { type: 'block-start', index: 0, blockType: 'text' };
    yield { type: 'block-end', index: 0, block: { type: 'text', text: output } };
    yield { type: 'finish', reason: { kind: 'stop' } };
  }
}

async function mount(auto = false) {
  const ctx = new Context();
  await ctx.plugin(Llm);
  await ctx.plugin(SessionStore);
  await ctx.plugin(SessionProjection);
  await ctx.plugin(SystemPrompt, {});
  await ctx.plugin(Tools);
  await ctx.plugin(NativeExecutor);
  await ctx.plugin(AgentRegistry);
  await ctx.plugin(Persistence, { root, compression: 'zstd' });
  await ctx.plugin(AgentLoop, { agents: [] });
  await ctx.plugin(TokenMeter);
  ctx.llm.registerAdapter(['mock'], new FakeModel());
  await ctx.plugin(Compactor, { auto, thresholdRatio: 0.2, headroomTokens: 0, maxTokens: 512, retainTokens: 130 });
  assert.ok(ctx.compaction, 'new compaction service mounted');
  return ctx;
}
const route = { provider: 'mock', model: 'model' };
async function followup(agent, text) {
    const idle = new Promise(resolve => {
      const stop = ctx.on('agent/status', ({ status, agent: observed }) => {
        if (observed === agent && status === 'idle') { stop(); resolve(); }
      });
    });
    agent.followup(createUserMessage({ source: { kind: 'user' }, content: [{ type: 'text', text }] }));
    await Promise.race([idle, new Promise((_, reject) => setTimeout(() => reject(new Error('idle timeout')), 5000).unref())]);
}

try {
  ctx = await mount();
  const id = SessionId('guarded-smoke');
  const agent = await ctx.agentLoop.create(id, route);
  await followup(agent, '!尺寸 shaft.diameter = 20 mm');
  assert.equal(received.length, 1);
  const journal = openJournal(dirname(ctx.sessionPersistence.locate(agent.session.header).path), agent.session.id);
  assert.equal(journal.state().graph['shaft.diameter']?.value, '20 mm');
  await followup(agent, '继续');
  const result = await ctx.compaction.compactNow(agent, new AbortController().signal);
  assert.ok(result);
  assert.equal(journal.state().pins.length, 1);
  await followup(agent, '请把 shaft.diameter 改为 24 mm。');
  assert.equal(received.length, 3);
  assert.equal(journal.state().graph['shaft.diameter'].value, '24 mm');
  assert.ok(received.at(-1).messages.some(m => m.content.some(b => b.text?.startsWith(SUMMARY_TITLE) && b.text.includes('24 mm'))));
  assert.ok(!JSON.stringify(received.at(-1).messages).includes('20 mm'));
  const before = received.length;
  const pin = journal.state().pins.at(-1);
  await writeFile(journal.backupPath(pin.candidate), '{}');
  assert.throws(() => ctx.llm.stream({
    provider: 'mock', model: 'model', sessionId: agent.session.id, messages: agent.session.deriveMessages(),
  }), /HASH/);
  assert.ok(journal.state().pending);
  assert.equal(received.length, before);
  const other = await ctx.agentLoop.create(SessionId('guarded-other'), route);
  await followup(other, '另一个会话应该继续执行');
  assert.equal(received.at(-1).sessionId, other.session.id);
  await ctx.sessions.flush(agent.session);
  await ctx.fiber.dispose();
  ctx = await mount();
  const { agent: resumed } = await ctx.agentLoop.resume(ctx, { resumeSessionId: id, agentOptions: route });
  assert.ok(journal.state().pending, 'restart must retain pending recovery');
  await followup(resumed, '压缩后继续');
  assert.equal(received.at(-1).sessionId, id);
  assert.equal(journal.state().pending, null);
  assert.equal(journal.state().pins.length, 3);
  assert.ok(!JSON.stringify(received.at(-1).messages).includes('20 mm'), 'recovery must not reintroduce the superseded value');
  const readAudit = await readFile(join(dirname(journal.backupPath(pin.candidate)), 'reads.jsonl'), 'utf8');
  assert.ok(readAudit.includes(journal.state().pins.at(-1).hash));
  await followup(resumed, '为暂停测试再留一轮可压缩历史。');
  const control = new AbortController();
  const manual = ctx.compaction.compactNow(resumed, control.signal);
  const rejected = assert.rejects(manual, /smoke pause/);
  for (let i = 0; !journal.state().pending?.source && i < 50; i++) await tick();
  assert.ok(journal.state().pending?.source, 'backup exists before pause');
  control.abort(new Error('smoke pause'));
  await rejected;
  assert.equal(journal.state().pending?.failure, 'MEMORY_INTERRUPTED');
  await followup(resumed, '从暂停继续');
  assert.equal(journal.state().pending, null);
  await ctx.fiber.dispose();
  ctx = await mount(true);
  const automatic = await ctx.agentLoop.create(SessionId('guarded-auto'), route);
  for (let index = 1; index <= 4; index++) await followup(automatic, '继续运行自动压缩验证。');
  const autoJournal = openJournal(dirname(ctx.sessionPersistence.locate(automatic.session.header).path), automatic.session.id);
  assert.ok(autoJournal.state().pins.length > 0, 'automatic pressure compaction must reach verified commit');
  assert.equal(autoJournal.state().pending, null);
  const active = autoJournal.state().pins.at(-1);
  const count = automatic.session.snapshotEvents().filter(event => event.type === 'compaction/start').length;
  assert.equal(await ctx.compaction.compactRegion(active.seq, active.seq, automatic, new AbortController().signal), null);
  assert.equal(automatic.session.snapshotEvents().filter(event => event.type === 'compaction/start').length, count);
  assert.equal(autoJournal.state().pending, null, 'a checkpoint-only retry must not lock the conversation');
  console.log('DSH rc.3 with Zstd: manual and automatic commit, revision, hash guard, independent session, restart, cancellation and recovery passed');
} finally {
  await ctx?.fiber.dispose();
  if (/^dsh-memory-native-[^\\/]+$/u.test(relative(tmpdir(), root))) {
    await rm(root, { recursive: true, force: true });
  }
}
