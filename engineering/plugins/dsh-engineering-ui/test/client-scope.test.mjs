/**
 * client-scope.test.mjs — 客户端组件的【作用域与渲染】回归
 *
 * ══ 为什么有这个文件（2026-11-07 事故）══════════════════════════════════
 * 我改了 client.js：把零件表搬进新的 eng-lower 容器，并保留了一个
 * 以 props.compact 决定样式类的分支。但那次编辑【只改了使用处，
 * 没加变量声明】：
 *
 *     h('div', { className: boardCls }, ...)   // boardCls 从未定义
 *     compact ? null : h('span', ...)          // compact 从未定义
 *
 * 结果：EngPartsBoard 一渲染就抛 ReferenceError → React 把整个
 *   SubagentConsole 子树卸掉 → 用户看到「面板整个消失」。
 *
 * 教训：node --check 只查语法，查不出「用了未定义的变量」。
 *   必须真的把组件跑一遍。这个文件就是干这个的。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLIENT = path.resolve(HERE, '..', 'lib', 'client.js');
const SRC = fs.readFileSync(CLIENT, 'utf8');

/** 在沙箱里加载客户端模块，返回它的 factory 产物（含所有组件）。 */
function loadClient() {
  const saved = {};
  for (const k of ['window', 'localStorage', 'document', 'fetch', 'setInterval', 'clearInterval']) {
    saved[k] = globalThis[k];
  }
  let MOD = null;
  globalThis.window = { __ModuleLoader__: { load(o) { MOD = o; } } };
  const store = {};
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem(k, v) { store[k] = v; },
    removeItem(k) { delete store[k]; },
  };
  globalThis.document = {
    getElementById: () => null,
    createElement: () => ({ style: {}, setAttribute() {}, appendChild() {}, remove() {}, classList: { add() {}, remove() {} } }),
    head: { appendChild() {} },
    addEventListener() {}, removeEventListener() {},
    documentElement: { classList: { add() {}, remove() {}, contains: () => false } },
    body: { classList: { add() {}, remove() {}, contains: () => false }, appendChild() {} },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  globalThis.fetch = () => Promise.resolve({ json: () => Promise.resolve({ ok: true, parts: [], summary: {}, entries: [], counts: [] }) });
  globalThis.setInterval = () => 0;
  globalThis.clearInterval = () => {};

  // 同一进程内多次 import 会走缓存；用 query 参数破缓存
  const url = 'file:///' + CLIENT.replace(/\\/g, '/') + '?t=' + Date.now();
  return { url, restore: () => { for (const k of Object.keys(saved)) { if (saved[k] === undefined) delete globalThis[k]; else globalThis[k] = saved[k]; } }, getMod: () => MOD };
}

/** 最小 React 替身：createElement 记录结构，hooks 返回稳定的假状态。 */
function fakeReact() {
  return {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
    useEffect() {},
    useLayoutEffect() {},
    useRef: (v) => ({ current: v }),
    useMemo: (f) => f(),
    useCallback: (f) => f,
    Fragment: 'Fragment',
  };
}

let mod = null;
{
  const env = loadClient();
  await import(env.url);
  const MOD = env.getMod();
  assert.ok(MOD, 'client.js 应调用 __ModuleLoader__.load()');
  assert.equal(MOD.id, 'dsh-engineering-ui');
  mod = MOD.factory((n) => (n === 'react' ? fakeReact() : {}));
  env.restore();
}

test('client.js 能导出核心组件（模块可执行）', () => {
  for (const name of ['SubagentConsole', 'EngPartsBoard', 'EngCompactPanel', 'apply']) {
    assert.equal(typeof mod[name], 'function', '应导出 ' + name);
  }
});

test('EngPartsBoard 在【正常模式】可渲染（不得抛 ReferenceError）', () => {
  assert.doesNotThrow(() => mod.EngPartsBoard({}), 'EngPartsBoard 渲染抛错 → 整个面板会消失');
});

test('EngPartsBoard 在【紧凑模式】可渲染（左列窄区就是用它）', () => {
  assert.doesNotThrow(() => mod.EngPartsBoard({ compact: true }),
    'compact 模式渲染抛错 → 左下区整个消失');
});

test('EngPartsBoard 紧凑模式：样式类必须带上 compact', () => {
  const el = mod.EngPartsBoard({ compact: true });
  // 空数据时返回的也是 .eng-parts-board 容器
  assert.ok(el && typeof el === 'object', '应返回元素');
  assert.ok(el.p && String(el.p.className || '').includes('eng-parts-board'),
    '容器类名应为 eng-parts-board 系列，实际: ' + JSON.stringify(el.p && el.p.className));
  assert.ok(String(el.p.className).includes('compact'), '紧凑模式应带 compact 类，实际: ' + String(el.p.className));
});

test('EngCompactPanel 可渲染（开关关/开两种）', () => {
  assert.doesNotThrow(() => mod.EngCompactPanel({ rootSessionId: 'x', selectedId: 'y' }),
    'EngCompactPanel 渲染抛错 → 左列下半区消失');
});

test('SubagentConsole 可渲染（preset 不匹配时返回 null 是正常的）', () => {
  assert.doesNotThrow(() => mod.SubagentConsole({ useSessions: () => undefined }));
});

test('源码里不得出现「用了但没声明」的样式变量（本次事故的直接特征）', () => {
  // 只针对 EngPartsBoard 函数体做精确检查，避免全文误报
  const i = SRC.indexOf('function EngPartsBoard');
  assert.ok(i > 0, '应能找到 EngPartsBoard');
  const end = SRC.indexOf('\n    }', i);
  const body = SRC.slice(i, end);
  for (const id of ['boardCls', 'compact']) {
    const used = new RegExp('\\b' + id + '\\b').test(body);
    const declared = new RegExp('var\\s+' + id + '\\b').test(body);
    if (used) assert.ok(declared, 'EngPartsBoard 用了 ' + id + ' 却没声明 → 渲染时 ReferenceError');
  }
});
