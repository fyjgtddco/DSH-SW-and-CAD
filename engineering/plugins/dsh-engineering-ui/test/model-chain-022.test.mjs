/**
 * 【DSH 0.2.2 模型接替契约】agent/request 不得再改路由，换模型只能走 agent.model.select()
 * ============================================================================================
 * 背景（2026-11-05，0.2.2-rc.1 实测）：
 *   dsh-agent-loop 在提示词组装前就 model.current() 捕获本步路由，prepareRequest 里强校验：
 *     throw new Error('agent "x": agent/request proposed "p/m" after step preparation
 *                      fixed "p/m"; use agent.model.select() before step preparation')
 *   旧实现（在 agent/request 里返回 {provider, model} 换模型）因此在 0.2.2 上
 *   会把"能接替"变成"更早地 turn/end error"，比不接替更糟。
 *
 * 本测试锁死三件事：
 *   ① 宿主有 agent.model.select 时：agent/request 原样放行（一个字都不改）；
 *   ② 同上：request-error 走 select(下一个候选)，且【不返回 {kind:'retry'}】
 *      （retry 复用本步已捕获的旧路由，只会再撞一次同样的错）；
 *   ③ 老宿主（无 select）：保留原来的覆盖 + retry 行为，不能把 0.2.1 弄坏。
 *
 * 运行：node --test test/model-chain-022.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// model_chain.json 从 DSH_STATE_DIR 读（findToolsDir 的第一优先级）
const stateDir = mkdtempSync(join(tmpdir(), 'dsh-eng-mc-'));
process.env.DSH_STATE_DIR = stateDir;
writeFileSync(join(stateDir, 'model_chain.json'), JSON.stringify({
  models: [
    { provider: 'p1', model: 'm1' },
    { provider: 'p2', model: 'm2', reasoningEffort: 'high' },
    { provider: 'p3', model: 'm3' },
    { provider: 'p4', model: 'm4' },
  ],
  autoResumeDelayMs: 10,
  autoResumeMax: 2,
}));

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;
const SHIM_REGISTRY = `
export function resolveSessionPreset({ header }) { return (header && header.agentPreset) ?? null; }
export const agentPresetProjectionDefinition = {
  init: (header) => (header && header.agentPreset) ?? null,
  apply: (state) => state,
};
`;
const url = (s) => 'data:text/javascript;base64,' + Buffer.from(s).toString('base64');
const rewritten = readFileSync(join(__dirname, '..', 'lib', 'index.js'), 'utf8')
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + url(SHIM_LLM) + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + url(SHIM_REGISTRY) + "'")
  .replace("from './defense-sign.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'defense-sign.js'), 'utf8')) + "'")
  .replace("from './job-wait-guard.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'job-wait-guard.js'), 'utf8')) + "'");

const { apply } = await import(url(rewritten));

function harness() {
  const hooks = new Map();
  const ctx = {
    on(name, fn) {
      const list = hooks.get(name) || [];
      list.push(fn);
      hooks.set(name, list);
      return () => {};
    },
    webServer: { register() { return () => {}; } },
    sessionPersistence: { async open() { throw new Error('no session'); }, async stat() { return null; } },
    sessions: {},
    agents: { get() { return undefined; }, roots() { return []; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
  };
  const dispose = apply(ctx);
  const all = (name) => hooks.get(name) || [];
  return { dispose, all };
}

/** 造一个工程模式会话的假 agent；withSelect=false 模拟 0.2.1 宿主。 */
function fakeAgent(id, withSelect, selected, resumed) {
  const agent = {
    id,
    agentPreset: 'engineering',
    session: { header: { agentPreset: 'engineering' } },
    followup(msg) { if (resumed) resumed.push(msg); },
  };
  if (withSelect) {
    agent.model = {
      current: () => ({ provider: 'p1', model: 'm1' }),
      select(sel) { selected.push(sel); },
    };
  }
  return agent;
}

const nextFn = () => Promise.resolve(undefined);

test('0.2.2：agent/request 不再覆盖路由（原样放行）', async () => {
  const { all, dispose } = harness();
  const selected = [];
  const agent = fakeAgent('a-022-req', true, selected);
  const reqHook = all('agent/request')[0];
  const errHook = all('agent/request-error')[0];
  assert.ok(reqHook && errHook, '两个钩子都必须注册');

  // 先制造一次额度失败（内部会把 switched 置位、并 select 下一个候选）
  await errHook({ agent, provider: 'p1', turn: 1, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } }, nextFn);

  // 再模拟下一轮请求：resolved 仍是失败那个模型
  const resolved = { provider: 'p1', model: 'm1' };
  const out = await reqHook({ agent, turn: 1 }, async () => resolved);
  assert.deepEqual(out, resolved, '0.2.2 下 agent/request 绝不能改 provider/model');
  dispose();
});

test('0.2.2：request-error 走 select，且不返回 retry', async () => {
  const { all, dispose } = harness();
  const selected = [];
  const agent = fakeAgent('a-022-err', true, selected);
  const errHook = all('agent/request-error')[0];

  const out = await errHook(
    { agent, provider: 'p1', turn: 1, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } },
    nextFn,
  );
  assert.equal(out, undefined, '必须交还下游，不能返回 {kind:"retry"}（重试会复用旧路由）');
  assert.deepEqual(selected, [{ provider: 'p2', model: 'm2', reasoningEffort: 'high' }],
    '必须 select 链里下一个候选，并带上候选自己的 reasoningEffort');
  dispose();
});

test('0.2.1 兼容：没有 agent.model.select 时保留覆盖 + retry', async () => {
  const { all, dispose } = harness();
  const agent = fakeAgent('a-021', false, []);
  const reqHook = all('agent/request')[0];
  const errHook = all('agent/request-error')[0];

  const out = await errHook(
    { agent, provider: 'p1', turn: 1, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } },
    nextFn,
  );
  assert.deepEqual(out, { kind: 'retry' }, '老宿主仍应返回 retry');

  const out2 = await reqHook({ agent, turn: 1 }, async () => ({ provider: 'p1', model: 'm1' }));
  assert.equal(out2.provider, 'p2', '老宿主仍应把请求改到候选');
  assert.equal(out2.model, 'm2');
  dispose();
});

test('0.2.2：失败后自动续跑（不需要用户手动重发）', async () => {
  const { all, dispose } = harness();
  const selected = [], resumed = [];
  const agent = fakeAgent('a-022-auto', true, selected, resumed);
  const errHook = all('agent/request-error')[0];

  await errHook(
    { agent, provider: 'p1', turn: 1, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } },
    nextFn,
  );
  assert.equal(resumed.length, 0, '同步阶段不能立刻投递（驱动器还在 running，followup 不会唤醒）');
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(resumed.length, 1, '延迟后必须自动续一轮');
  assert.equal(resumed[0].source.kind, 'plugin:dsh-engineering-ui', '续跑消息必须标明插件来源');
  assert.match(resumed[0].content[0].text, /自动接替/);
  assert.match(resumed[0].content[0].text, /p2\/m2/);
  dispose();
});

test('0.2.2：自动续跑有上限，不会无限重试', async () => {
  const { all, dispose } = harness();
  const selected = [], resumed = [];
  const agent = fakeAgent('a-022-cap', true, selected, resumed);
  const errHook = all('agent/request-error')[0];

  for (let turn = 1; turn <= 4; turn += 1) {
    await errHook(
      { agent, provider: 'p' + turn, turn, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } },
      nextFn,
    );
    await new Promise((r) => setTimeout(r, 30));
  }
  assert.equal(resumed.length, 2, 'autoResumeMax=2 时必须停在第 2 次');
  dispose();
});

test('0.2.2：用户手动发新消息 → 续跑计数清零', async () => {
  const { all, dispose } = harness();
  const selected = [], resumed = [];
  const agent = fakeAgent('a-022-reset', true, selected, resumed);
  const errHook = all('agent/request-error')[0];
  const preHook = all('agent/pre-step')[0];

  for (let turn = 1; turn <= 3; turn += 1) {
    await errHook({ agent, provider: 'p' + turn, turn, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } }, nextFn);
    await new Promise((r) => setTimeout(r, 30));
  }
  assert.equal(resumed.length, 2);

  // 用户真的开口了 → 计数清零
  await preHook({
    agent, turn: 4, step: 1,
    messages: [{ role: 'user', content: [{ type: 'text', text: '继续' }], source: { kind: 'user' } }],
    modelSelection: { provider: 'p4', model: 'm4' },
  }, async () => ({ kind: 'enter', messages: [] }));

  await errHook({ agent, provider: 'p4', turn: 5, failure: { code: 'QUOTA_EXCEEDED', model: 'm1' } }, nextFn);
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(resumed.length, 3, '清零后应该又能续一次');
  dispose();
});
