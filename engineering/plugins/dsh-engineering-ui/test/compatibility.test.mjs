import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { Readable } from 'node:stream';
import vm from 'node:vm';

const profile = process.env.DSH_TEST_PROFILE;
assert.ok(profile, 'Set DSH_TEST_PROFILE to the installed web profile');
const require = createRequire(path.join(profile, 'package.json'));
const source = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
// Resolve the source under test against the actual installed DSH modules.
const resolved = source.replace(/from '(@deepseek-ai\/[^']+)'/g,
  (_, name) => `from '${pathToFileURL(require.resolve(name)).href}'`);
const { apply } = await import('data:text/javascript;base64,' + Buffer.from(resolved).toString('base64'));
const { Session } = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-session')).href);

function harness(inspections = {}) {
  const routes = new Map(), hooks = new Map(), asks = [];
  const root = { id: 'root' };
  const ctx = {
    on(name, fn) { hooks.set(name, fn); return () => hooks.delete(name); },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path); } },
    sessionPersistence: { async inspect(id) { return inspections[id]; } },
    sessions: {},
    agents: { get(id) { return id === root.id ? root : undefined; }, roots() { return [root]; } },
    subagents: { async listDescendants() { return [{ id: 'child', kind: 'child' }]; } },
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

test('all nine HTTP routes and the engineering guard remain registered', () => {
  const h = harness();
  try {
    // 7 个原始路由 + 【Bug-32】verify-subagent + 【Bug-21】notify-room = 9
    assert.equal(h.routes.size, 9);
    for (const p of ['/dsh-engineering-ui/agents', '/dsh-engineering-ui/log',
                     '/dsh-engineering-ui/ask', '/dsh-engineering-ui/ask-child',
                     '/dsh-engineering-ui/pending-child', '/dsh-engineering-ui/result-child',
                     '/dsh-engineering-ui/answer', '/dsh-engineering-ui/verify-subagent',
                     '/dsh-engineering-ui/notify-room']) {
      assert.ok(h.routes.has(p), 'missing route ' + p);
    }
    assert.equal(h.hooks.has('agent/turn-stopping'), true);
  } finally { h.dispose(); }
  assert.equal(h.routes.size, 0);
  assert.equal(h.hooks.size, 0);
});

test('preset switches and guard use real DSH Session snapshots', () => {
  const oldHome = process.env.DSH_HOME;
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-engineering-test-'));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
  writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: { part: { active: true } } }));
  process.env.DSH_HOME = temp;
  const h = harness();
  try {
    const session = Session.create('guard-test');
    assert.equal(typeof session.snapshotEvents, 'function');
    assert.equal(session.events, undefined);
    const steered = [];
    const agent = { id: session.id, session, steer(message) { steered.push(message); } };
    const stop = () => h.hooks.get('agent/turn-stopping')({ agent, signal: new AbortController().signal });
    session.append('agent-preset/selected', { agentPreset: 'engineering' });
    stop();
    assert.equal(steered.length, 1, 'unfinished engineering turn must continue');
    assert.equal(steered[0].source.plugin, 'dsh-engineering-ui');
    session.append('agent-preset/selected', { agentPreset: 'standard' });
    stop();
    assert.equal(steered.length, 1, 'standard mode must be released immediately');
    session.append('agent-preset/selected', { agentPreset: 'engineering' });
    stop();
    assert.equal(steered.length, 2, 'switching back must restore the guard');
    h.hooks.get('agent/turn-stopping')({ agent, signal: AbortSignal.abort() });
    assert.equal(steered.length, 2);
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'finished' }));
    stop();
    assert.equal(steered.length, 2, 'completed workflow must be released');
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    rmSync(temp, { recursive: true });
  }
});

test('completion markers and user stop messages still release the guard', () => {
  const oldHome = process.env.DSH_HOME;
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-engineering-finish-'));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
  writeFileSync(path.join(tools, 'mode_state.json'), JSON.stringify({ rooms: { part: { active: true } } }));
  process.env.DSH_HOME = temp;
  const h = harness();
  try {
    for (const event of [
      { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '[TASK_DONE]' }] } } },
      { type: 'user/message', data: { content: [{ type: 'text', text: 'stop' }] } },
    ]) {
      const steered = [];
      const agent = { id: 'finish', session: {
        header: { agentPreset: 'engineering' },
        snapshotEvents() { return [event]; },
      }, steer(message) { steered.push(message); } };
      h.hooks.get('agent/turn-stopping')({ agent });
      assert.equal(steered.length, 0);
    }
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    rmSync(temp, { recursive: true });
  }
});

test('descendants and log pagination exclude inherited and dropped events', async () => {
  const h = harness({ child: {
    meta: { parentSession: 'root', createdAt: 10 }, inheritedEventCount: 2,
    events: [
      { seq: 0, type: 'assistant/message', data: {} },
      { seq: 1, type: 'tool/result', data: {} },
      { seq: 2, type: 'assistant/chunk', data: {} },
      { seq: 3, type: 'tool/call', data: { name: 'cad' } },
      { seq: 4, type: 'tool/result', data: { message: { content: [] } } },
    ],
  } });
  try {
    assert.equal((await h.request('/agents?rootSessionId=root')).agents[0].id, 'child');
    const log = await h.request('/log?sessionId=child');
    assert.deepEqual(log.events.map(e => e.seq), [3, 4]);
    assert.equal(log.latestSeq, 4);
    assert.deepEqual((await h.request('/log?sessionId=child&since=3')).events.map(e => e.seq), [4]);
    assert.equal((await h.request('/log?sessionId=unknown')).status, 404);
    assert.equal((await h.request('/log?sessionId=../invalid')).status, 400);
  } finally { h.dispose(); }
});

const questions = [{ id: 'q1', question: 'Confirm part 10?', options: [{ label: 'Continue' }, { label: 'Revise' }] }];
test('child questions route to the parent, deduplicate, and return native answers', async () => {
  const h = harness({ child: { meta: { parentSession: 'root' }, events: [] } });
  try {
    const payload = { childSessionId: 'child', questions };
    const first = await h.request('/ask-child', payload);
    assert.equal(first.ok, true);
    assert.equal(first.rootSessionId, 'root');
    assert.equal(h.asks[0].request.agent, h.root);
    const second = await h.request('/ask-child', payload);
    assert.equal(second.pendingId, first.pendingId);
    assert.equal(h.asks.length, 1);
    assert.equal((await h.request('/pending-child?childSessionId=child')).pending.length, 1);
    const answers = [{ id: 'q1', selected: ['Continue'], custom: 'Use steel' }];
    h.asks[0].resolve({ answers });
    await new Promise(setImmediate);
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers, answers);
    assert.equal((await h.request('/pending-child?rootSessionId=root')).pending.length, 0);
  } finally { h.dispose(); }
});

test('panel answers and root question registration keep their existing protocol', async () => {
  const h = harness();
  try {
    const first = await h.request('/ask-child', { childSessionId: 'child', rootSessionId: 'root', questions });
    const result = await h.request('/answer', { pendingId: first.pendingId, selected: [{ qid: 'q1', label: 'Revise' }], custom: '20 mm' });
    assert.equal(result.bound, 'child-tool-result');
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers,
      [{ id: 'q1', selected: ['Revise'], custom: '20 mm' }]);
    assert.equal((await h.request('/answer', { pendingId: first.pendingId })).ok, false);
    const rootAsk = await h.request('/ask', { sessionId: 'root', questions });
    assert.equal(rootAsk.ok, true);
    h.asks[1].resolve({ answers: [{ id: 'q1', selected: ['Continue'] }] });
    await new Promise(setImmediate);
    assert.equal((await h.request('/ask', { sessionId: 'missing', questions })).ok, false);
  } finally { h.dispose(); }
});

test('client keeps panels, warning banner, question cards and event renderers', () => {
  const react = require('react');
  let client;
  vm.runInNewContext(readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load(definition) { client = definition.factory(name => {
      assert.equal(name, 'react'); return react;
    }); } } }, console,
  });
  for (const key of ['apply', 'SubagentConsole', 'EngineeringBanner', 'AskCard', 'formatMessage', 'renderEvent', 'parseQuestions']) {
    assert.equal(typeof client[key], 'function', key);
  }
  assert.equal(client.SubagentDock, client.SubagentConsole);
  const { renderToStaticMarkup } = require('react-dom/server');
  const state = { current: 'root', byId: { root: { agentPreset: 'engineering' } },
    subagentsByParent: { root: { entries: [{ id: 'child', kind: 'child', label: 'CAD part', activity: 'running' }] } } };
  const renderPanel = () => renderToStaticMarkup(react.createElement(client.SubagentConsole, {
    useSessions: selector => selector(state),
  }));
  assert.match(renderPanel(), /eng-fab/);
  assert.match(renderPanel(), /CAD part/);
  state.byId.root.agentPreset = 'standard';
  assert.equal(renderPanel(), '');
  const banner = renderToStaticMarkup(react.createElement(client.EngineeringBanner, {
    sessionId: 'root', useSessions: selector => selector(state),
    useProjection: () => ({ currentValue: 'sw-single-line' }),
  }));
  assert.match(banner, /role="alert"/);
  const card = renderToStaticMarkup(react.createElement(client.AskCard, { questions, asChild: true, childSessionId: 'child' }));
  assert.match(card, /Continue/);
  assert.match(card, /Revise/);
  for (const [kind, type] of [['thinking', 'think'], ['message', 'text'], ['tool-call', 'tool'], ['tool-result', 'result'], ['error', 'error']]) {
    assert.equal(client.formatMessage({ kind, text: 'CAD result', tool: 'sw_bridge' }).t, type);
  }
  assert.equal(client.formatMessage({ kind: 'tool-call', tool: 'ask_user_question', args: JSON.stringify({ questions }) }).t, 'ask');
});
