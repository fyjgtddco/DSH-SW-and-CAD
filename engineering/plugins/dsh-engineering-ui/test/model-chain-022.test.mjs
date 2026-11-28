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
/**
 * 【通用兜底】lib/ 下新增模块时，显式替换链会漏一行 → data: URL 无法解析
 *   相对路径 → 整组用例 ERR_UNSUPPORTED_RESOLVE_REQUEST。已连续踩到两次
 *   （model-errors.js / turn-guard.js）。这里统一内联，新增文件不必再改测试。
 */
function inlineLocalLibs(src, libDir) {
  let out = src;
  const names = new Set();
  // 【子目录 import】原来只认 './x.js'，遇到 './run-log/y.mjs' 整份漏掉 →
  //   data: URL 无法解析相对路径 → 测试文件 ERR_UNSUPPORTED_RESOLVE_REQUEST。
  const re = /from '\.\/((?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:js|mjs))'/g;
  let m;
  while ((m = re.exec(src)) !== null) names.add(m[1]);
  for (const name of names) {
    let body;
    let own;
    try { own = join(libDir, name); body = readFileSync(own, 'utf8'); } catch (e) { continue; }
    // 递归基准 = 该模块自己的目录（子目录 import 才解析得对）
    const inner = inlineLocalLibs(body, dirname(own));
    out = out.split("from './" + name + "'").join("from '" + url(inner) + "'");
  }
  return out;
}

const rewritten = inlineLocalLibs(readFileSync(join(__dirname, '..', 'lib', 'index.js'), 'utf8'), join(__dirname, '..', 'lib'))
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + url(SHIM_LLM) + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + url(SHIM_REGISTRY) + "'")
  .replace("from './defense-sign.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'defense-sign.js'), 'utf8')) + "'")
  .replace("from './job-wait-guard.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'job-wait-guard.js'), 'utf8')) + "'")
  // 【Webots 接入】webots.js 也是相对 import；data: URL 解析不了相对路径。
  .replace("from './webots.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'webots.js'), 'utf8')) + "'")
  .replace("from './mem-guard.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'mem-guard.js'), 'utf8')) + "'")
  // 【A4】错误码归一模块同样是相对 import，一并内联
  .replace("from './model-errors.js'", "from '" + url(readFileSync(join(__dirname, '..', 'lib', 'model-errors.js'), 'utf8')) + "'");

const { apply } = await import(url(rewritten));

function harness() {
  const hooks = new Map();
  const ctx = {
    // 【Webots 接入】index.js 已声明 inject 含 'tools' 并调用 ctx.tools.guard。
    tools: { guard() { return () => {}; } },
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

test('接管目标按会话恢复，手动改用第三个模型时取消恢复', async () => {
  const { all, dispose } = harness();
  const selected = [];
  const agent = fakeAgent('a-old', true, selected);
  agent.session.id = 'session-stable';
  const fail = { agent, provider: 'p1', turn: 1, failure: { code: 'RATE_LIMIT', model: 'm1' } };
  await all('agent/request-error')[0](fail, nextFn);
  await all('agent/disposed')[0]({ agent });
  dispose();

  const restarted = harness();
  const agent2 = fakeAgent('a-rebuilt', true, selected);
  agent2.session.id = agent.session.id;
  const pre = restarted.all('agent/pre-step')[0];
  await pre({ agent: agent2, modelSelection: { provider: 'p1', model: 'm1' } }, nextFn);
  assert.deepEqual(selected.map(route => route.model), ['m2', 'm2']);
  await pre({ agent: agent2, modelSelection: { provider: 'manual', model: 'chosen' } }, nextFn);
  assert.deepEqual(selected.map(route => route.model), ['m2', 'm2']);
  await pre({ agent: agent2, modelSelection: { provider: 'p1', model: 'm1' } }, nextFn);
  assert.equal(selected.length, 2, '已取消的接管不应再次恢复');
  restarted.dispose();
});

test('同一子代理会话反复重建时保留续跑次数', async () => {
  const { all, dispose } = harness();
  const resumed = [];
  const pre = all('agent/pre-step')[0];
  const onError = all('agent/request-error')[0];
  const onDisposed = all('agent/disposed')[0];
  for (let turn = 1; turn <= 3; turn++) {
    const agent = fakeAgent('recreated-' + turn, true, [], resumed);
    agent.session.id = 'durable-resume-budget';
    const modelSelection = { provider: 'p' + turn, model: 'm' + turn };
    agent.model.current = () => modelSelection;
    await pre({ agent, turn, modelSelection }, nextFn);
    await onError({ agent, turn, provider: modelSelection.provider,
      failure: { code: 'RATE_LIMIT', model: modelSelection.model } }, nextFn);
    await onDisposed({ agent });
    await new Promise(resolve => setTimeout(resolve, 35));
  }
  assert.equal(resumed.length, 2);
  dispose();
});

test('关闭接管时保留候选配置，压缩超限时能跳过窗口偏小的同家模型', async () => {
  const file = join(stateDir, 'model_chain.json');
  const original = readFileSync(file, 'utf8');
  const selected = [];
  const agent = fakeAgent('a-window', true, selected);
  const request = { agent, provider: 'p1', turn: 1, failure: {
    model: 'm1', message: 'compaction still above threshold (50,578 >= 40000)',
  } };
  const config = { autoResume: false, models: [
    { provider: 'p1', model: 'small', contextWindow: 40000 },
    { provider: 'p1', model: 'large', contextWindow: 200000 },
  ] };
  try {
    writeFileSync(file, JSON.stringify({ ...config, enabled: false }));
    const off = harness();
    await off.all('agent/request-error')[0](request, nextFn);
    assert.deepEqual(selected, []);
    off.dispose();

    writeFileSync(file, JSON.stringify({ ...config, enabled: true }));
    const on = harness();
    await on.all('agent/request-error')[0](request, nextFn);
    assert.deepEqual(selected, [{ provider: 'p1', model: 'large' }]);
    on.dispose();
  } finally {
    writeFileSync(file, original);
  }
});
