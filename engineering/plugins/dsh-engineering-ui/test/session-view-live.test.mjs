import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLIENT = path.resolve(HERE, '..', 'lib', 'client.js');

function setupEnv() {
  const saved = {};
  for (const k of ['window', 'localStorage', 'document', 'fetch', 'setInterval', 'clearInterval']) saved[k] = globalThis[k];
  let MOD = null;
  globalThis.window = { __ModuleLoader__: { load(o) { MOD = o; } }, addEventListener() {}, removeEventListener() {}, innerWidth: 1400, innerHeight: 900 };
  const store = {};
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  };
  globalThis.document = {
    querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
    createElement: () => ({ style: {}, dataset: {}, classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {}, remove() {} }),
    head: { appendChild() {} }, addEventListener() {}, removeEventListener() {},
    documentElement: { classList: { add() {}, remove() {}, contains: () => false } },
    body: { classList: { add() {}, remove() {}, contains: () => false }, appendChild() {} },
  };
  globalThis.fetch = () => Promise.resolve({ json: () => Promise.resolve({ ok: true, entries: [], counts: [] }) });
  globalThis.setInterval = () => 0;
  globalThis.clearInterval = () => {};
  return { store, restore: () => { for (const k of Object.keys(saved)) { if (saved[k] === undefined) delete globalThis[k]; else globalThis[k] = saved[k]; } }, getMod: () => MOD };
}

const env = setupEnv();
const url = 'file:///' + CLIENT.replace(/\\/g, '/') + '?t=' + Date.now();
await import(url);
const MOD = env.getMod();
const React = {
  createElement: (t, p, ...c) => ({ t, p, c }),
  useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
  useEffect() {}, useLayoutEffect() {}, useRef: (v) => ({ current: v }),
  useMemo: (f) => f(), useCallback: (f) => f, Fragment: 'Fragment',
};
const mod = MOD.factory((n) => (n === 'react' ? React : {}));
env.restore();

/** 造一个最小 sessions 服务：可切换主会话预设，并可广播订阅。 */
function makeSessions(preset) {
  const subscribers = [];
  const snap = {
    ids: ['s1'],
    byId: {
      s1: { retainedBy: { mainView: 1 }, agentPreset: preset, projectionValues: { agentPreset: preset } },
    },
  };
  return {
    snapshot: snap,
    setPreset(next) {
      snap.byId.s1.agentPreset = next;
      snap.byId.s1.projectionValues.agentPreset = next;
      subscribers.forEach((fn) => { try { fn(); } catch (e) {} });
    },
    list: {
      getSnapshot: () => snap,
      subscribe(fn) { subscribers.push(fn); return () => { const i = subscribers.indexOf(fn); if (i >= 0) subscribers.splice(i, 1); }; },
    },
  };
}

function runApply(sessions, settings) {
  const registered = [];
  const slots = {
    inject: (name, fn) => { const r = fn(); return typeof r === 'function' ? r : () => {}; },
    register: (opts, comp) => {
      const entry = { name: opts.name, id: opts.id, opts };
      registered.push(entry);
      return () => { const i = registered.indexOf(entry); if (i >= 0) registered.splice(i, 1); };
    },
    entries: (n) => registered.filter((e) => e.name === n),
    subscribe: () => () => {},
    entriesOfSlot: (n) => registered.filter((e) => e.name === n),
  };
  const ctx = {
    slots,
    effect: (f) => { try { const d = f(); return typeof d === 'function' ? d : () => {}; } catch (e) { return () => {}; } },
    get: (n) => (n === 'sessions' ? sessions : null),
    on: () => () => {},
    locale: { bind: () => (k) => k, register: () => () => {}, subscribe: () => () => {} },
    commandUi: { register: () => () => {} },
    logger: { warn() {}, error() {} },
    webServer: { register: () => () => {} },
  };
  mod.apply(ctx);
  return registered;
}

const view = (reg) => reg.find((e) => e.name === 'conversation.view');

test('工程模式会话：页签被注册，且 label 有文字', () => {
  const sessions = makeSessions('engineering');
  const reg = runApply(sessions);
  const v = view(reg);
  assert.ok(v, '工程模式会话必须有 conversation.view 注册');
  assert.equal(typeof v.opts.label, 'function');
  // 官方是空参调用
  assert.equal(v.opts.label(), '工程模式');
});

test('非工程模式会话：页签根本不注册（不是空白页签）', () => {
  const sessions = makeSessions('standard');
  const reg = runApply(sessions);
  assert.equal(view(reg), undefined, '标准模式下不得注册该页签');
});

test('切换会话预设后：页签会自动装/卸', () => {
  const sessions = makeSessions('standard');
  const reg = runApply(sessions);
  assert.equal(view(reg), undefined, '初始标准模式无页签');
  sessions.setPreset('engineering');
  assert.ok(view(reg), '切到工程模式后应自动出现');
  assert.equal(view(reg).opts.label(), '工程模式');
  sessions.setPreset('standard');
  assert.equal(view(reg), undefined, '切回标准模式后应自动消失');
});
