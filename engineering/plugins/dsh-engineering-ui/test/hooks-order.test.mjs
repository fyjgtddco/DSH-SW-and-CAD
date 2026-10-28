/**
 * 【React hooks 顺序】子代理面板不得在提前 return 之后声明 hook
 * ============================================================================
 * 现场事故（2026-10-28，用户原话「我的子标签页跑哪里去了？你给我干消失了」）：
 *   给子代理面板加「逐分支折叠」时，把 var collapsedSt = useState({}) 写在了
 *   渲染阶段 —— 而那三处 return null（非工程模式 / 关掉自带显示页 /
 *   还没有子代理）都在它前面。
 *
 *   后果（React 硬规则：hook 数量必须逐次渲染保持一致）：
 *     · 刚进工程模式、还没建子代理 → 走 entries.length===0 的 return null
 *       → 本次只跑了 34 个 hook；
 *     · 建成第一个子代理 → 这次会跑到第 35 个 hook
 *       → Rendered more hooks than during the previous render → 组件抛错；
 *     · 组件崩了 ⇒ 整个右侧面板不渲染，连带 _engContextActive 也不再置位
 *       ⇒ 官方右侧的子代理 tab 接管一起失效 —— 表现就是子标签页消失。
 *
 * 本测试用【真的 hook 计数】把这件事锁死。
 *
 * 运行：node --test test/hooks-order.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'lib', 'client.js'), 'utf8');

function boot() {
  let mod = null;
  let hooks = 0;
  const store = new Map();
  const mkEl = () => ({
    style: {}, setAttribute() {}, appendChild() {}, remove() {},
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    childNodes: [], dataset: {},
    querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 44, height: 92 }),
  });
  const fakeDoc = {
    body: Object.assign(mkEl(), { classList: { add() {}, remove() {}, contains: () => false, toggle() {} } }),
    getElementById: () => null, createElement: mkEl, head: { appendChild() {} },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  };
  const REACT = {
    createElement: (t, p, ...c) => ({ type: t, props: p || {}, children: c.flat() }),
    Fragment: 'Fragment',
    useState: (v) => { hooks++; return [typeof v === 'function' ? v() : v, () => {}]; },
    useRef: (v) => { hooks++; return { current: v }; },
    useEffect: () => { hooks++; },
    useMemo: (f) => f(),
    memo: (x) => x,
  };
  vm.runInNewContext(SRC, {
    window: {
      __ModuleLoader__: { load(d) { mod = d.factory(() => REACT); } },
      addEventListener() {}, removeEventListener() {},
      confirm: () => true, location: { href: '' },
      innerWidth: 1600, innerHeight: 900,
    },
    document: fakeDoc,
    localStorage: {
      _d: store,
      getItem(k) { return this._d.get(k) === undefined ? null : this._d.get(k); },
      setItem(k, v) { this._d.set(k, String(v)); },
      removeItem(k) { this._d.delete(k); },
      key: () => null, get length() { return 0; },
    },
    console: { log() {}, warn() {}, error() {}, info() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, agents: [] }) }),
    MutationObserver: function () { return { observe() {}, disconnect() {} }; },
  });
  return {
    mod,
    render(preset, catalog) {
      hooks = 0;
      const useSessions = (sel) => {
        try {
          return sel({
            byId: { s1: { sessionId: 's1', retainedBy: { mainView: 1 },
                          projectionValues: { agentPreset: preset } } },
            projectionsBySession: { s1: { values: { subagentCatalog: catalog } } },
            subagentsByParent: {},
          });
        } catch (e) { return undefined; }
      };
      let node = null, err = null;
      try { node = mod.SubagentConsole({ useSessions }); } catch (e) { err = e; }
      return { node, hooks, err };
    },
  };
}

test('★ 没有子代理 vs 有子代理：hook 次数必须完全相同', () => {
  const h = boot();
  assert.ok(h.mod && typeof h.mod.SubagentConsole === 'function', '应加载出 SubagentConsole');
  const empty = h.render('engineering', []);
  const full = h.render('engineering', [
    { id: 'c1', label: '结构件', mode: 'continuable' },
    { id: 'c2', label: '传动机构', mode: 'continuable' },
  ]);
  assert.equal(empty.err, null, '空列表渲染不得抛错：' + (empty.err && empty.err.message));
  assert.equal(full.err, null,
    '★ 有子代理时渲染不得抛错（抛错 = 整个面板与右侧 tab 一起消失）：' +
    (full.err && full.err.message));
  assert.equal(empty.hooks, full.hooks,
    '★ hook 数量必须一致（' + empty.hooks + ' vs ' + full.hooks + '）');
});

test('★ 非工程模式 vs 工程模式：hook 次数也必须相同', () => {
  const h = boot();
  const std = h.render('standard', []);
  const eng = h.render('engineering', [{ id: 'c1', label: 'A', mode: 'continuable' }]);
  assert.equal(std.node, null, '非工程模式必须返回 null');
  assert.equal(std.hooks, eng.hooks,
    '非工程模式也要走完同样的 hook 数量（早退必须发生在所有 hook 之后）');
});

test('★ 源码层：SubagentConsole 内任何 hook 都不得出现在提前 return 之后', () => {
  const lines = SRC.split(/\r?\n/);
  const start = lines.findIndex((l) => l.includes('function SubagentConsole(props)'));
  assert.ok(start > 0, '找不到 SubagentConsole');
  let end = start;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].includes('主对话内的交互提问节点')) { end = i; break; }
  }
  assert.ok(end > start, '找不到 SubagentConsole 的结束位置');
  // ── 如何可靠地找到「组件级提前 return」而不误判回调里的 return ──────
  //   不靠缩进（回调里也是 8 空格），改用【唯一语义标记】：
  //   三道门禁的提前返回前都必然先写 `_engContextActive = false`，
  //   而 useSessions 选择器回调里的 return 不会有这一行。
  const boundary = lines.findIndex((l, i) =>
    i >= start && i < end && l.includes('_engContextActive = false'));
  assert.ok(boundary > start,
    '找不到组件级提前返回的分界（应有 _engContextActive = false 的三道门禁）');
  // 该标记之后必须紧跟 return null（确认我们找对了位置）
  const guarded = lines.slice(boundary, boundary + 4).join('');
  assert.ok(guarded.includes('return null;'),
    '分界之后应紧跟 return null，实际：' + guarded.replace(/\s+/g, ' ').slice(0, 120));
  // ── 核心断言：分界之后不得再有任何 hook ────────────────────────────
  const bad = [];
  for (let i = boundary; i < end; i++) {
    if (/\buse(State|Ref|Effect|Memo|Callback)\(/.test(lines[i])) {
      bad.push((i + 1) + ': ' + lines[i].trim());
    }
  }
  assert.equal(bad.length, 0,
    '★ 这些 hook 写在了提前 return 之后，会让 hook 数量跳变：' + bad.join(' | '));

});
