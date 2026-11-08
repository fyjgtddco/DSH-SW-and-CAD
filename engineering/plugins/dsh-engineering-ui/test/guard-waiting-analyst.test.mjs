/**
 * guard-waiting-analyst.test.mjs — 【BUG-A1 / A11】守卫行为的端到端回归
 * ==========================================================================
 * 与 turn-guard.test.mjs 的分工：
 *   那个测【判定函数】对不对；这个测【守卫真的按判定结果行动了没有】。
 *   两者必须都有 —— 判定对了但没接线，等于没修（这正是 A1/A11 的历史状态）。
 *
 * 跑的是 lib/index.js 里真正的 apply()，只把 6 个外部依赖替换成内联 shim，
 * 与 compatibility.test.mjs / repeat-output.test.mjs 同一套做法。
 *
 * A1 的三个必须成立的结论：
 *   ① 只写 [WAITING]、拿不出客观证据 → 必须【照旧 steer】（不能当后门）；
 *   ② [WAITING] + 客观证据（未答提问卡）→ 必须【放行】（不 steer）；
 *   ③ 没有 [WAITING] 也没有代码级终态 → 照旧 steer（守卫没被削弱）。
 * A11：
 *   ④ 命中 analyst.session_id → steer 的文案里必须出现"总计子代理"；
 *   ⑤ 不命中 → 仍发建模小屋文案（回退，不发错）。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', 'lib');

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;
const SHIM_REGISTRY = `
export function resolveSessionPreset({ header, events }) {
  const evs = Array.isArray(events) ? events : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
      return String(ev.data.agentPreset);
    }
  }
  return (header && header.agentPreset) ?? null;
}
export const agentPresetProjectionDefinition = {
  init: (header) => (header && header.agentPreset) ?? null,
  apply: (state, event) => {
    if (event && event.type === 'agent-preset/selected' && event.data && event.data.agentPreset) {
      return String(event.data.agentPreset);
    }
    return state;
  },
};
`;

const dataUrl = (s) => 'data:text/javascript;base64,' + Buffer.from(s).toString('base64');
const LLM_URL = dataUrl(SHIM_LLM);
const REG_URL = dataUrl(SHIM_REGISTRY);

/**
 * 把 lib/ 下的相对 import 递归内联成 data: URL —— 新增模块无需再改本文件。
 * （compatibility.test.mjs 已经有 inlineLocalLibs，这里保留同名实现以便独立运行）
 */
function inlineLocalLibs(src, libDir) {
  let out = src;
  const names = new Set();
  const re = /from '\.\/([A-Za-z0-9_.-]+\.js)'/g;
  let m;
  while ((m = re.exec(src)) !== null) names.add(m[1]);
  for (const name of names) {
    let body;
    try { body = readFileSync(path.join(libDir, name), 'utf8'); } catch (e) { continue; }
    const inner = inlineLocalLibs(body, libDir);
    out = out.split("from './" + name + "'").join("from '" + dataUrl(inner) + "'");
  }
  return out;
}

let source = readFileSync(path.join(LIB, 'index.js'), 'utf8');
source = source
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + LLM_URL + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + REG_URL + "'");
const rewritten = inlineLocalLibs(source, LIB);
assert.ok(rewritten.includes('turn-guard') === false || true, 'placeholder');
const { apply } = await import(dataUrl(rewritten));

function harness() {
  const hooks = new Map();
  const routes = new Map();
  const root = { id: 'root' };
  const ctx = {
    tools: { guard() { return () => {}; } },
    on(name, fn) {
      const list = hooks.get(name) || [];
      list.push(fn);
      hooks.set(name, list);
      return () => {
        const cur = hooks.get(name) || [];
        const i = cur.indexOf(fn);
        if (i >= 0) cur.splice(i, 1);
        if (!cur.length) hooks.delete(name);
      };
    },
    webServer: { register(r) { routes.set(r.path, r.handler); return () => routes.delete(r.path); } },
    sessionPersistence: {
      async open() { throw new Error('no log'); },
      async stat() { return null; },
    },
    sessions: { list() { return []; } },
    agents: { get() { return undefined; }, roots() { return [root]; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
    get(name) { return name === 'jobs' ? undefined : undefined; },
  };
  const dispose = apply(ctx);
  hooks.all = (name) => hooks.get(name) || [];
  /** 真实运行时会依次调用同名钩子【全部】处理函数 */
  const fire = (name, payload) => {
    for (const fn of (hooks.get(name) || [])) fn(payload);
  };
  return { ctx, routes, hooks, fire, dispose };
}

/** 造一个工程模式 agent；lastText 会作为最后一条 assistant/message 进事件。 */
function agent(id, lastText, extraEvents) {
  const events = [
    { type: 'agent-preset/selected', data: { agentPreset: 'engineering' } },
  ];
  if (extraEvents) events.push(...extraEvents);
  if (lastText) {
    events.push({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: lastText }] } } });
  }
  return {
    id,
    delegationDepth: 1,                       // 子代理（走子代理分支）
    parentSession: 'root',
    session: {
      id,
      header: { agentPreset: 'engineering', parentSession: 'root', delegationDepth: 1 },
      snapshotEvents() { return events; },
    },
    steers: [],
    steer(msg) { this.steers.push(msg); },
  };
}

function withHome(prefix, fn) {
  const oldHome = process.env.DSH_HOME;
  const oldState = process.env.DSH_STATE_DIR;
  const temp = mkdtempSync(path.join(tmpdir(), prefix));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  process.env.DSH_HOME = temp;
  delete process.env.DSH_STATE_DIR;              // 让 findToolsDir 认这个临时目录
  const h = harness();
  try {
    return fn(h, tools, temp);
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    if (oldState === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = oldState;
    rmSync(temp, { recursive: true, force: true });
  }
}

const steerText = (ag) => (ag.steers[0] && ag.steers[0].content[0] && ag.steers[0].content[0].text) || '';

// ══════════════════════════════════════════════════════════════════════════
//  A1 · [WAITING] 既不能当后门，也不能继续被堵死
// ══════════════════════════════════════════════════════════════════════════

test('A1 ① 只写 [WAITING] 但拿不出客观证据 → 必须照旧 steer（不是后门）', () => {
  withHome('gw-backdoor-', (h, tools) => {
    // 门禁：没有 finished、没有房间（= 没在等任何东西）
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    const ag = agent('sub-1', '[WAITING]');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1,
      '[WAITING] 没有任何客观证据时必须打回 —— 否则它就成了逃避推进的后门');
  });
});

test('A1 ② [WAITING] + 客观证据（未答提问卡）→ 必须放行', () => {
  withHome('gw-pass-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    const ag = agent('sub-2', '[WAITING]');
    // 造一张真实的未答提问卡：走 /ask-child 登记
    const asked = h.routes.get('/dsh-engineering-ui/ask-child');
    assert.ok(asked, '/ask-child 必须存在（本用例用它制造"人没答"的客观事实）');
    // 直接驱动钩子前先登记 pending，再用同一 childSessionId 触发
    return (async () => {
      await callRoute(h, '/ask-child', {
        childSessionId: 'sub-2',
        questions: [{ id: 'q1', question: '继续吗？', options: [{ label: '是' }] }],
      });
      h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
      assert.equal(ag.steers.length, 0,
        '有未答提问卡 → 模型确实只能等 → [WAITING] 必须放行（A1 的正向用例）');
    })();
  });
});

test('A1 ③ 既没有 [WAITING] 也没有代码级终态 → 照旧 steer（守卫没被削弱）', () => {
  withHome('gw-normal-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    const ag = agent('sub-3', '我做完了，全部交付。');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1, '散文式完成声明不得放行（PhaseA 的严格性必须保住）');
  });
});

test('A1 ④ [WAITING] 嵌在句子里不算数（不是独占一行）', () => {
  withHome('gw-inline-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    const ag = agent('sub-4', '我在等房间，先写个 [WAITING] 试试');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1, '嵌在句中的 [WAITING] 无效');
  });
});

// ══════════════════════════════════════════════════════════════════════════
//  A11 · 守卫按角色选文案
// ══════════════════════════════════════════════════════════════════════════

test('A11 ① 命中 analyst.session_id → 文案是"总计子代理"，不再催 sw_bridge', () => {
  withHome('gw-analyst-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    writeFileSync(path.join(tools, 'mode2_plan.json'),
      JSON.stringify({ analyst: { session_id: 'sub-analyst' } }));
    const ag = agent('sub-analyst', '我这边阶段都推进完了。');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1, '仍未到终态 → 该 steer');
    const t = steerText(ag);
    assert.match(t, /总计子代理/, '必须自报"总计子代理"身份（A11 现场就是被当成建模小屋）');
    assert.match(t, /mode2_flow/, '必须指向它真正该用的阶段命令');
    assert.match(t, /不要.*sw_bridge|sw_bridge.*不要/, '必须明说不要调 sw_bridge');
  });
});

test('A11 ② 不命中 analyst → 仍发建模小屋文案（保守回退，不发错）', () => {
  withHome('gw-builder-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    writeFileSync(path.join(tools, 'mode2_plan.json'),
      JSON.stringify({ analyst: { session_id: 'some-other-session' } }));
    const ag = agent('sub-builder', '零件建完了。');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1);
    const t = steerText(ag);
    assert.match(t, /建模\/专项子代理/, '建模小屋必须仍收到建模文案');
    assert.equal(/总计子代理/.test(t), false, '不得把建模小屋误判成 analyst');
  });
});

test('A11 ③ 拿不到 mode2_plan → 回退建模小屋文案（不猜身份）', () => {
  withHome('gw-noplan-', (h, tools) => {
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
    writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: {} }));
    // 故意不写 mode2_plan.json
    const ag = agent('sub-x', '做完了。');
    h.fire('agent/turn-stopping', { agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1);
    assert.match(steerText(ag), /建模\/专项子代理/, '读不到身份必须保守回退');
  });
});

/** 极简 HTTP 路由调用（只用于 /ask-child 这种同步返回 JSON 的端点）。 */
function callRoute(h, routePath, payload) {
  return new Promise((resolve) => {
    const handler = h.routes.get(routePath);
    if (!handler) return resolve(null);
    const chunks = [JSON.stringify(payload)];
    const req = {
      url: routePath,
      method: 'POST',
      on(ev, fn) { if (ev === 'data') chunks.forEach((c) => fn(Buffer.from(c))); if (ev === 'end') fn(); },
      destroy() {},
    };
    const res = {
      statusCode: 200,
      setHeader() {},
      end(text) { try { resolve(JSON.parse(text)); } catch (e) { resolve(null); } },
    };
    handler(req, res);
  });
}
