// 【问题1/4 根因回归】body class 残留验证
//
// 用户现象：
//   · 关掉「使用工程模式自带的子代理显示页」后，右侧仍留一条空白蓝带；
//   · 且【左侧项目栏的边界也被改动】；
//   · 结论："设置没有用"。
//
// 根因（本文件要锁住的）：
//   组件的 useEffect 用 [active, paneOpen, embedded] 作依赖，
//   而 active 定义在 preset/setting 门禁【之前】。于是关掉设置后：
//     · 组件 return null（不再渲染内容），
//     · 但依赖未变化 → effect【不重跑、不清理】→
//       body 上残留 dsh-eng-dock / eng-pane-open →
//       CSS 继续强改 grid-template-columns（含第一列比例）→ 蓝带 + 左栏错位。
//   修复：把"是否允许占列"(engAllowed) 纳入依赖，保证设置/预设一变就清理。
//
// 本测试用【带依赖比较与 cleanup 语义】的 React shim，真实复现该链路：
//   渲染(设置开) → 类被加上 → 触发设置变更 → 重渲染 → 类必须被摘掉。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = process.argv[2] || path.join(here, '..');
const src = readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8');

const DOCK = 'dsh-eng-dock';
const OPEN = 'eng-pane-open';
const ENGMODE = 'dsh-eng-mode';
const SETTINGS_KEY = 'dsh-engineering-ui/settings/v1';

let pass = 0, fail = 0;
const check = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

// ── 假 document / window / localStorage ────────────────────────────────
function makeEnv() {
  const classes = new Set();
  const doc = {
    body: {
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
      },
    },
    documentElement: {
      style: { setProperty() {}, removeProperty() {} },
      classList: { add() {}, remove() {}, contains: () => false },
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    head: { appendChild() {} },
    createElement: () => ({ style: {}, className: '', id: '', textContent: '', remove() {}, appendChild() {} }),
    addEventListener() {}, removeEventListener() {},
  };
  const store = new Map();
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  const winHandlers = {};
  const win = {
    innerWidth: 1400,
    addEventListener: (t, f) => { (winHandlers[t] = winHandlers[t] || []).push(f); },
    removeEventListener() {},
    localStorage,
  };
  return { classes, doc, win, localStorage, winHandlers, store };
}

// ── 带依赖比较 + cleanup 的 React shim ─────────────────────────────────
function makeReact() {
  const state = [];      // 跨渲染持久
  const effects = [];    // 跨渲染持久
  let cursor = 0;

  const reset = () => { cursor = 0; };

  const React = {
    createElement: (type, props, ...children) => ({
      type, props: props || {}, children: children.flat().filter((c) => c !== null && c !== undefined && c !== false),
    }),
    Fragment: 'Fragment',
    useState(init) {
      const i = cursor++;
      if (!(i in state)) state[i] = typeof init === 'function' ? init() : init;
      return [state[i], (v) => { state[i] = typeof v === 'function' ? v(state[i]) : v; }];
    },
    useRef(v) {
      const i = cursor++;
      if (!(i in state)) state[i] = { current: v };
      return state[i];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      const rec = effects[i];
      const changed = !rec || !deps || !rec.deps
        || deps.length !== rec.deps.length
        || deps.some((d, k) => !Object.is(d, rec.deps[k]));
      if (changed) {
        if (rec && rec.cleanup) { try { rec.cleanup(); } catch (e) {} }
        const cleanup = fn();
        effects[i] = { deps: deps || null, cleanup: typeof cleanup === 'function' ? cleanup : null };
      }
    },
    useMemo: (f) => f(),
  };
  return { React, reset };
}

// ── 用假环境加载模块 ───────────────────────────────────────────────────
const env = makeEnv();
const { React, reset } = makeReact();
let client;
// window 必须【自带】__ModuleLoader__，否则模块体的
// `window.__ModuleLoader__.load({...})` 会因 undefined 而抛错。
env.win.__ModuleLoader__ = {
  load(def) { client = def.factory(() => React); },
};
const fn = new Function(
  'window', 'document', 'localStorage', 'console',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
  'getComputedStyle', 'MutationObserver', 'navigator',
  src,
);
fn(
  env.win, env.doc, env.localStorage, console,
  setTimeout, clearTimeout, () => 0, () => {},
  () => ({ getPropertyValue: () => '' }), undefined, { userAgent: 'test' },
);

// 当前会话（engineering）+ 一个子代理 → 面板本应占列
const state = {
  ids: ['root'],
  byId: {
    root: { projectionValues: { agentPreset: 'engineering' }, retainedBy: { mainView: 1 } },
    c1: { running: true },
  },
  projectionsBySession: {
    root: { values: { subagentCatalog: [{ id: 'c1', mode: 'continuable', label: '结构件小屋' }] } },
  },
};
const render = () => { reset(); return client.SubagentConsole({ useSessions: (sel) => sel(state) }); };

console.log('\n=== 步骤1：设置=开（默认）→ 面板应占列 ===');
env.localStorage.setItem(SETTINGS_KEY, JSON.stringify({ useOwnSubagentPane: true }));
render();
check('★ 已挂 dsh-eng-dock（进入三栏模式）', env.classes.has(DOCK), [...env.classes].join(','));
check('★ 已挂 eng-pane-open（我方占列）', env.classes.has(OPEN));

console.log('\n=== 步骤2：用户关闭设置 → 必须摘掉所有 body 类 ===');
// 模拟用户在设置页关闭开关：写 localStorage 并触发 storage 事件
// （这正是 apply() 里监听的跨窗口/同页同步通道）
env.localStorage.setItem(SETTINGS_KEY, JSON.stringify({ useOwnSubagentPane: false }));
(env.winHandlers['storage'] || []).forEach((h) => h({ key: SETTINGS_KEY }));
render();
check('★ dsh-eng-dock 已被摘掉（不再强改三列）', !env.classes.has(DOCK), [...env.classes].join(','));
check('★ eng-pane-open 已被摘掉（不再撑开第三列）', !env.classes.has(OPEN));
check('★ dsh-eng-mode 也一并摘掉（主对话样式一并交回官方）',
  !env.classes.has(ENGMODE), [...env.classes].join(','));
check('★ 这正是"设置没有用/蓝带残留"的根治点',
  !env.classes.has(DOCK) && !env.classes.has(OPEN));

console.log('\n=== 步骤3：重新开启 → 应恢复占列 ===');
env.localStorage.setItem(SETTINGS_KEY, JSON.stringify({ useOwnSubagentPane: true }));
(env.winHandlers['storage'] || []).forEach((h) => h({ key: SETTINGS_KEY }));
render();
check('★ 重新挂上 dsh-eng-dock', env.classes.has(DOCK), [...env.classes].join(','));
check('★ 重新挂上 eng-pane-open', env.classes.has(OPEN));

console.log('\n=== 步骤4：源码级保障（防止后人把依赖改回去）===');
check('★ effect 依赖含 engAllowed（设置/预设一变即清理）',
  /\[active,\s*paneOpen,\s*embedded,\s*engAllowed\]/.test(src));
check('★ engAllowed 同时含 preset 与设置开关',
  /engAllowed\s*=\s*\(preset === 'engineering'\)\s*&&\s*!!engSettings\.useOwnSubagentPane/.test(src));
check('★ 清理函数无条件摘掉两个类',
  /return function \(\) \{[\s\S]{0,220}classList\.remove\(DOCK_CLASS\)[\s\S]{0,160}classList\.remove\("eng-pane-open"\)/.test(src));

console.log(`\n===== 结果：${pass} 通过 / ${fail} 失败 =====`);
process.exit(fail === 0 ? 0 : 1);
