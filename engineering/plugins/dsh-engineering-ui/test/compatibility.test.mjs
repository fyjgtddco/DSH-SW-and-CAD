/**
 * dsh-engineering-ui Host 插件 · 兼容性与契约回归测试。
 *
 * ── 【P1-7 修复】本文件此前完全失效，原因有三 ──────────────────────────
 *  1. 依赖 createRequire 解析 @deepseek-ai/* 到【安装体】。0.2.0 把这些包
 *     打进 app.asar，磁盘上 require.resolve 必然 MODULE_NOT_FOUND ——
 *     测试根本跑不起来（Cannot find module '@deepseek-ai/dsh-llm'）。
 *  2. 断言 h.routes.size === 9，而插件实际注册 13 条路由（多了 4 条
 *     /defense/*）。断言与实际长期不符。
 *  3. 用 sessionPersistence.inspect() —— 该 API 在 0.2.0 已被移除
 *     （改为 open/read/close），且断言 source.plugin 字段，与
 *     repeat-output.test.mjs 的"不得存在 plugin 字段"断言直接矛盾。
 *
 * 现改为【自包含】：把两个外部依赖替换为内联 shim（与
 * repeat-output.test.mjs 同一套做法），因此无需 DSH 安装体即可运行。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';

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

const LLM_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_LLM).toString('base64');
const REG_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_REGISTRY).toString('base64');
const source = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
const signSource = readFileSync(new URL('../lib/defense-sign.js', import.meta.url), 'utf8');
const SIGN_URL = 'data:text/javascript;base64,' + Buffer.from(signSource).toString('base64');

const rewritten = source
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + LLM_URL + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + REG_URL + "'")
  .replace("from './defense-sign.js'", "from '" + SIGN_URL + "'");
assert.ok(rewritten.includes('data:text/javascript'), '依赖 shim 必须替换成功');

const { apply } = await import('data:text/javascript;base64,' + Buffer.from(rewritten).toString('base64'));

/**
 * 构造一个可控的插件宿主上下文。
 *
 * sessionPersistence 用 0.2.0 合同：open(id,'read') → { header, read(), close() }，
 * 并保留 stat() 供 parentOf() 回落使用（inspect() 已移除）。
 */
function harness(handles = {}) {
  const routes = new Map(), hooks = new Map(), asks = [];
  const root = { id: 'root' };
  const ctx = {
    on(name, fn) { hooks.set(name, fn); return () => hooks.delete(name); },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path); } },
    sessionPersistence: {
      async open(id) {
        const h = handles[id];
        if (!h) throw new Error('no such session');
        return {
          header: h.header || {},
          inheritedEventCount: h.inheritedEventCount || 0,
          async read() { return { events: h.events || [] }; },
          async close() {},
        };
      },
      async stat(id) {
        const h = handles[id];
        if (!h) return null;
        return { header: h.header || {} };
      },
    },
    sessions: {},
    agents: { get(id) { return id === root.id ? root : undefined; }, roots() { return [root]; } },
    subagents: { async listDescendants() { return [{ id: 'child', label: 'child' }]; } },
    userQuestions: { ask(request) { return new Promise((resolve, reject) => asks.push({ request, resolve, reject })); } },
  };
  const dispose = apply(ctx);
  async function request(route, payload, method = payload ? 'POST' : 'GET') {
    const req = Readable.from(payload ? [JSON.stringify(payload)] : []);
    req.url = '/dsh-engineering-ui' + route;
    req.method = method;
    let body;
    const res = { statusCode: 200, setHeader() {}, end(text) { body = JSON.parse(text); } };
    await routes.get(req.url.split('?')[0])(req, res);
    return { status: res.statusCode, ...body };
  }
  return { ctx, root, routes, hooks, asks, dispose, request };
}

test('全部 21 条 HTTP 路由与守卫钩子注册，且可完整卸载', () => {
  const h = harness();
  try {
    // 9 条基础/提问/通知路由 + 4 条【三大防线】签名路由
    //   + 4 条【连接区】路由（conn-state / conn-probe
    //     / conn-launch / conn-diagnose）
    //   + 1 条【独立聊天页】推荐追问路由（/suggest）
    //   + 1 条【划词详情】侧边分析路由（/side-chat）
    //   + 1 条【工程人设】读写路由（/persona）
    //   + 1 条【聊天模式】同会话能力切换路由（/chat-mode）= 21
    assert.equal(h.routes.size, 21, '路由数量应为 21（含 /suggest、/side-chat、/persona、/chat-mode）');
    for (const p of [
      '/dsh-engineering-ui/agents', '/dsh-engineering-ui/log',
      '/dsh-engineering-ui/ask', '/dsh-engineering-ui/ask-child',
      '/dsh-engineering-ui/pending-child', '/dsh-engineering-ui/result-child',
      '/dsh-engineering-ui/answer', '/dsh-engineering-ui/verify-subagent',
      '/dsh-engineering-ui/notify-room',
      '/dsh-engineering-ui/defense/sign', '/dsh-engineering-ui/defense/verify',
      '/dsh-engineering-ui/defense/info', '/dsh-engineering-ui/defense/judge',
      '/dsh-engineering-ui/conn-state', '/dsh-engineering-ui/conn-probe',
      '/dsh-engineering-ui/conn-launch', '/dsh-engineering-ui/conn-diagnose',
      '/dsh-engineering-ui/suggest', '/dsh-engineering-ui/side-chat',
      '/dsh-engineering-ui/persona', '/dsh-engineering-ui/chat-mode',
    ]) {
      assert.ok(h.routes.has(p), 'missing route ' + p);
    }
    assert.equal(h.hooks.has('agent/turn-stopping'), true);
  } finally { h.dispose(); }
  assert.equal(h.routes.size, 0, 'dispose 后路由必须清空');
  assert.equal(h.hooks.size, 0, 'dispose 后钩子必须清空');
});

test('收尾守卫：非终态时 steer，达到 finished 后放行，且 source 为 producer-owned', () => {
  const oldHome = process.env.DSH_HOME;
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-eng-compat-'));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
  writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: { part: { active: true } } }));
  process.env.DSH_HOME = temp;
  const h = harness();
  try {
    const steered = [];
    const agent = {
      id: 'guard-1',
      session: {
        header: { agentPreset: 'engineering' },
        snapshotEvents() { return [{ type: 'agent-preset/selected', data: { agentPreset: 'engineering' } }]; },
      },
      steer(message) { steered.push(message); },
    };
    const stop = () => h.hooks.get('agent/turn-stopping')({ agent, signal: new AbortController().signal });
    stop();
    assert.equal(steered.length, 1, '未达代码级终态必须 steer 一次');
    // 【V4】steer 的消息 source 必须是生产者自有 kind，不得是通用 plugin
    assert.equal(steered[0].source.kind, 'plugin:dsh-engineering-ui');
    assert.equal(Object.hasOwn(steered[0].source, 'plugin'), false,
      '不得保留已退休的 plugin 字段（与 repeat-output.test.mjs 一致）');
    // 用户取消 → 放行
    h.hooks.get('agent/turn-stopping')({ agent, signal: AbortSignal.abort() });
    assert.equal(steered.length, 1, 'aborted 信号必须放行');
    // 门禁进入终态 → 放行
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'finished' }));
    stop();
    assert.equal(steered.length, 1, 'workflow step=finished 必须放行');
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    rmSync(temp, { recursive: true, force: true });
  }
});

test('日志端点走 0.2.0 的 open/read/close，过滤继承事件与高频 chunk', async () => {
  const h = harness({ child: {
    header: { parentSession: 'root', createdAt: 10 },
    inheritedEventCount: 2,
    events: [
      { seq: 0, type: 'assistant/message', data: {} },
      { seq: 1, type: 'tool/result', data: {} },
      { seq: 2, type: 'assistant/chunk', data: {} },
      { seq: 3, type: 'tool/call', data: { name: 'cad' } },
      { seq: 4, type: 'tool/result', data: { message: { content: [] } } },
    ],
  } });
  try {
    const log = await h.request('/log?sessionId=child');
    // 继承的 2 条被排除；assistant/chunk 属于 RAW_DROP
    assert.deepEqual(log.events.map(e => e.seq), [3, 4]);
    assert.equal(log.latestSeq, 4);
    assert.deepEqual((await h.request('/log?sessionId=child&since=3')).events.map(e => e.seq), [4]);
    assert.equal((await h.request('/log?sessionId=unknown')).status, 404);
    assert.equal((await h.request('/log?sessionId=../invalid')).status, 400);
  } finally { h.dispose(); }
});

const questions = [{ id: 'q1', question: 'Confirm part 10?', options: [{ label: 'Continue' }, { label: 'Revise' }] }];

test('子代理提问路由到父会话、可去重、答案以原生 tool_result 返回', async () => {
  const h = harness({ child: { header: { parentSession: 'root' }, events: [] } });
  try {
    const payload = { childSessionId: 'child', questions };
    const first = await h.request('/ask-child', payload);
    assert.equal(first.ok, true);
    assert.equal(first.rootSessionId, 'root', '提问必须挂到 root 会话（真人所在处）');
    assert.equal(h.asks[0].request.agent, h.root);
    // 同一题重复登记 → 复用同一张卡
    const second = await h.request('/ask-child', payload);
    assert.equal(second.pendingId, first.pendingId);
    assert.equal(h.asks.length, 1, '同题不得重复登记');
    assert.equal((await h.request('/pending-child?childSessionId=child')).pending.length, 1);
    const answers = [{ id: 'q1', selected: ['Continue'], custom: 'Use steel' }];
    h.asks[0].resolve({ answers });
    await new Promise(setImmediate);
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers, answers);
    assert.equal((await h.request('/pending-child?rootSessionId=root')).pending.length, 0);
  } finally { h.dispose(); }
});

test('面板作答与主对话登记各自沿用既有协议', async () => {
  const h = harness();
  try {
    const first = await h.request('/ask-child', { childSessionId: 'child', rootSessionId: 'root', questions });
    const result = await h.request('/answer', { pendingId: first.pendingId, selected: [{ qid: 'q1', label: 'Revise' }], custom: '20 mm' });
    assert.equal(result.bound, 'child-tool-result');
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers,
      [{ id: 'q1', selected: ['Revise'], custom: '20 mm' }]);
    // 重复提交必须被拒绝
    assert.equal((await h.request('/answer', { pendingId: first.pendingId })).ok, false);
    // 主对话提问
    const rootAsk = await h.request('/ask', { sessionId: 'root', questions });
    assert.equal(rootAsk.ok, true);
    h.asks[1].resolve({ answers: [{ id: 'q1', selected: ['Continue'] }] });
    await new Promise(setImmediate);
    assert.equal((await h.request('/ask', { sessionId: 'missing', questions })).ok, false);
  } finally { h.dispose(); }
});

test('client 半导出既有组件与渲染器（横幅已随问题2 移除）', () => {
  // 用最小 React shim 加载 client.js：只需要能求值出 exports，
  // 不依赖安装体的 react（那个在 app.asar 里，磁盘上取不到）。
  const shim = {
    createElement: () => null, Fragment: 'Fragment',
    useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
    useRef: (v) => ({ current: v }), useEffect: () => {}, useMemo: (f) => f(),
  };
  let client;
  const mod = readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8');
  // client.js 是浏览器侧的 __ModuleLoader__ 包装，用一个假 window 接住它
  const fn = new Function('window', 'console', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', mod);
  fn({ __ModuleLoader__: { load(def) { client = def.factory(() => shim); } } },
     console, setTimeout, clearTimeout, setInterval, clearInterval);
  for (const key of ['apply', 'SubagentConsole', 'EngineeringSettingsSection', 'AskCard', 'formatMessage', 'renderEvent', 'parseQuestions']) {
    assert.equal(typeof client[key], 'function', key);
  }
  assert.equal(client.SubagentDock, client.SubagentConsole);
  // 【问题2 修复】配套横幅已随 SW单行模式 一起删除
  assert.equal('EngineeringBanner' in client, false, 'EngineeringBanner must be removed');
  // 事件分类契约
  for (const [kind, type] of [['thinking', 'think'], ['message', 'text'], ['tool-call', 'tool'], ['tool-result', 'result'], ['error', 'error']]) {
    assert.equal(client.formatMessage({ kind, text: 'CAD result', tool: 'sw_bridge' }).t, type);
  }
  assert.equal(client.formatMessage({ kind: 'tool-call', tool: 'ask_user_question', args: JSON.stringify({ questions }) }).t, 'ask');
});
