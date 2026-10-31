/**
 * 【R15 回归】fork 子代理额度用完必须能自动换模型（用户实测反馈）
 *
 * 用户原话：「子代理的模型额度用完的时候，好像是不会自动换，你看看可不可以」
 *
 * 根因：modelChainApplies() 对子代理的判定依赖 parentChainStateOf(agent).isEng，
 *   而 isEng 只在父级【真正发过一次 agent/request】之后才有值。
 *   9 个建模小屋是同批 fork 出来的，此刻父级可能还没发请求（或压缩/重启后
 *   状态被清）→ 判定为"非工程"→ QUOTA 错误直接透传 → 子代理当场死亡，
 *   而主对话却能正常换模型。
 *
 * 本测试真实加载插件本体，锁定：
 *   A. 父级尚未发过请求时，子代理遇 QUOTA → 必须换模型
 *   B. 父级已跑过时，子代理遇 QUOTA → 换模型，且沿用父级钉住的候选
 *   C. 非工程父会话的子代理 → 不接管（作用域零外溢）
 *   D. 无父会话的普通会话 → 不接管
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const HERE = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname)
  .replace(/^\/([A-Za-z]:)/, '$1'));

function resolveLib() {
  const cands = [path.join(HERE, '..', 'plugins', 'dsh-engineering-ui', 'lib')];
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) for (const p of ['desktop', 'web']) {
    cands.push(path.join(home, '.dsh', 'profiles', p, 'node_modules', 'dsh-engineering-ui', 'lib'));
  }
  for (const c of cands) if (existsSync(path.join(c, 'index.js'))) return c;
  return cands[0];
}
const LIB = process.env.MC_LIB || resolveLib();
if (!existsSync(path.join(LIB, 'index.js'))) {
  console.log('[SKIP] 未找到插件源码：' + LIB);
  process.exit(0);
}

const source = readFileSync(path.join(LIB, 'index.js'), 'utf8');
const signSource = readFileSync(path.join(LIB, 'defense-sign.js'), 'utf8');
const SHIM_LLM = 'export function createUserMessage(i){return {role:"user",content:i.content,source:i.source};}';
const SHIM_REG = 'export function resolveSessionPreset({header,events}){const e=Array.isArray(events)?events:[];for(let i=e.length-1;i>=0;i--){const ev=e[i];if(ev&&ev.type==="agent-preset/selected"&&ev.data&&ev.data.agentPreset)return String(ev.data.agentPreset);}return (header&&header.agentPreset)??null;}export const agentPresetProjectionDefinition={init:h=>(h&&h.agentPreset)??null,apply:(s,e)=>s};';
const url = (s) => 'data:text/javascript;base64,' + Buffer.from(s).toString('base64');
const rewritten = source
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + url(SHIM_LLM) + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + url(SHIM_REG) + "'")
  .replace("from './defense-sign.js'", "from '" + url(signSource) + "'");

async function boot(models) {
  const home = mkdtempSync(path.join(tmpdir(), 'chainr15_'));
  const tools = path.join(home, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(tools, 'model_chain.json'),
    JSON.stringify({ enabled: true, models }), 'utf8');
  const oldHome = process.env.DSH_HOME;
  process.env.DSH_HOME = home;

  const hooks = new Map();
  const ctx = {
    on(n, fn) { const l = hooks.get(n) || []; l.push(fn); hooks.set(n, l); return () => {}; },
    webServer: { register() { return () => {}; } },
    sessionPersistence: { async open() { throw new Error('no log'); }, async stat() { return null; } },
    sessions: { list() { return []; } },
    agents: { get() { return undefined; }, roots() { return []; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
  };
  const { apply } = await import(url(rewritten));
  apply(ctx);

  const fire = async (name, payload, nextImpl) => {
    let out;
    const next = (v) => { out = (v === undefined ? payload : v); return out; };
    for (const fn of (hooks.get(name) || [])) {
      const r = await fn(payload, next);
      if (r !== undefined) out = r;
    }
    return out;
  };
  const cleanup = () => {
    if (oldHome === undefined) delete process.env.DSH_HOME;
    else process.env.DSH_HOME = oldHome;
    rmSync(home, { recursive: true, force: true });
  };
  return { fire, cleanup };
}

const MODELS = [
  { provider: 'p1', model: 'm1' },
  { provider: 'p2', model: 'm2' },
  { provider: 'p3', model: 'm3' },
];

/** 子代理：header 无 agentPreset、自身 events 无 agent-preset/selected */
function forkedChild(parentId, id = 'C-1') {
  return {
    id,
    parentSession: parentId,
    session: {
      id,
      header: { parentSession: parentId, delegationDepth: 1 },
      snapshotEvents() { return []; },
    },
  };
}

function engRoot(id = 'P-ROOT') {
  return {
    id,
    session: {
      id,
      header: { agentPreset: 'engineering' },
      snapshotEvents() { return [{ type: 'agent-preset/selected', data: { agentPreset: 'engineering' } }]; },
    },
  };
}

function nonEngRoot(id = 'P-OTHER') {
  return {
    id,
    session: {
      id,
      header: { agentPreset: 'default' },
      snapshotEvents() { return [{ type: 'agent-preset/selected', data: { agentPreset: 'default' } }]; },
    },
  };
}

test('A. ★ 父级尚未发过请求时，子代理遇额度耗尽必须换模型', async () => {
  const { fire, cleanup } = await boot(MODELS);
  try {
    // 注意：完全没有 fire('agent/request')，父级状态不存在 —— 这就是实测现场
    const child = forkedChild('P-ROOT');
    const r = await fire('agent/request-error', {
      agent: child, turn: 1,
      provider: 'p1', failure: { code: 'QUOTA_EXCEEDED', model: 'm1', message: 'quota' },
    });
    assert.equal(r && r.kind, 'retry', '★ 必须返回 retry 触发接替（旧实现透传 → 子代理死亡）');
    const next = await fire('agent/request', { agent: child, turn: 1 },
      () => ({ provider: 'p1', model: 'm1' }));
    assert.equal(next.provider, 'p2', '应切到链上第二个候选的 provider');
    assert.equal(next.model, 'm2', '应切到链上第二个候选的 model');
  } finally { cleanup(); }
});

test('B. 父级已跑过时，子代理同样换模型', async () => {
  const { fire, cleanup } = await boot(MODELS);
  try {
    const parent = engRoot();
    await fire('agent/request', { agent: parent, turn: 1 }, () => ({ provider: 'p1', model: 'm1' }));
    const child = forkedChild('P-ROOT', 'C-2');
    const r = await fire('agent/request-error', {
      agent: child, turn: 1,
      provider: 'p1', failure: { code: 'QUOTA_EXCEEDED', model: 'm1' },
    });
    assert.equal(r && r.kind, 'retry', '子代理应触发接替');
  } finally { cleanup(); }
});

test('C. 非工程父会话的子代理 → 不接管（作用域零外溢）', async () => {
  const { fire, cleanup } = await boot(MODELS);
  try {
    const parent = nonEngRoot();
    await fire('agent/request', { agent: parent, turn: 1 }, () => ({ provider: 'p1', model: 'm1' }));
    const child = forkedChild('P-OTHER', 'C-3');
    const r = await fire('agent/request-error', {
      agent: child, turn: 1,
      provider: 'p1', failure: { code: 'QUOTA_EXCEEDED', model: 'm1' },
    });
    assert.notEqual(r && r.kind, 'retry', '非工程会话的子代理不得被接管');
  } finally { cleanup(); }
});

test('D. 无父会话的普通会话 → 不接管', async () => {
  const { fire, cleanup } = await boot(MODELS);
  try {
    const solo = {
      id: 'SOLO',
      session: { id: 'SOLO', header: { agentPreset: 'default' }, snapshotEvents() { return []; } },
    };
    const r = await fire('agent/request-error', {
      agent: solo, turn: 1,
      provider: 'p1', failure: { code: 'QUOTA_EXCEEDED', model: 'm1' },
    });
    assert.notEqual(r && r.kind, 'retry', '普通会话不得被接管');
  } finally { cleanup(); }
});

test('E. 链已全部试过 → 不空转（返回透传）', async () => {
  // 真实场景是【各模型依次失败】：p1 挂 → 切 p2；p2 也挂 → 切 p3；
  //   p3 再挂 → 链已耗尽 → 必须交还宿主，绝不无限重试。
  const { fire, cleanup } = await boot(MODELS);
  try {
    const child = forkedChild('P-ROOT', 'C-5');
    const failures = [
      { provider: 'p1', model: 'm1' },
      { provider: 'p2', model: 'm2' },
      { provider: 'p3', model: 'm3' },
    ];
    let last = null;
    let switched = 0;
    for (const f of failures) {
      last = await fire('agent/request-error', {
        agent: child, turn: 1,
        provider: f.provider, failure: { code: 'QUOTA_EXCEEDED', model: f.model },
      });
      if (last && last.kind === 'retry') switched += 1;
    }
    assert.equal(switched, 2, '三个候选依次失败时，前两次应各切换一次（p1→p2→p3）');
    assert.notEqual(last && last.kind, 'retry',
      '候选全部耗尽后必须交还宿主，不得无限重试');
  } finally { cleanup(); }
});
