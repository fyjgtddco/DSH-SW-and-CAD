/**
 * 压缩历史「子代理分组」回归（2026-11-28）
 *
 * 现场：子代理页把十几个不同子代理的记录混在一张表里，label 全是 hash 前缀
 *   （31da68c4 / f4fdb8a7…），用户原话「这里也要分子代理展示，不然太乱了」。
 *
 * 本测试真实渲染 EngCompactPage，检查：
 *   · 多子代理时出现分组头（.eng-cp-group），每组带名字/次数/省下字符；
 *   · 单子代理时不分组（不制造多余的一层壳）；
 *   · 分组可折叠；
 *   · 路由到具体子代理（selectedId）时回归扁平视图。
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = process.argv[2] || path.join(here, '..');

const stateSlots = [];
let cursor = 0;
const resetHooks = () => { cursor = 0; stateSlots.length = 0; };
const react = {
  createElement(type, props, ...children) {
    return { type, props: props || {}, children: children.flat().filter(c => c != null && c !== false) };
  },
  Fragment: 'Fragment',
  useState(init) {
    const i = cursor++;
    if (stateSlots.length <= i) stateSlots[i] = typeof init === 'function' ? init() : init;
    return [stateSlots[i], (v) => { stateSlots[i] = typeof v === 'function' ? v(stateSlots[i]) : v; }];
  },
  useRef(v) { const i = cursor++; if (stateSlots.length <= i) stateSlots[i] = { current: v }; return stateSlots[i]; },
  useEffect() { cursor++; },
  useMemo(f) { return f(); },
  useCallback(f) { return f; },
};

let client;
vm.runInNewContext(readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8'), {
  window: { __ModuleLoader__: { load(def) { client = def.factory(() => react); } } },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
});

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

function walk(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach(n => walk(n, out)); return out; }
  out.push(node);
  (node.children || []).forEach(c => walk(c, out));
  return out;
}
const hasClass = (nodes, cls) => nodes.some(n =>
  String((n.props && n.props.className) || '').split(/\s+/).includes(cls));
const textOf = (node) => walk(node).map(n =>
  typeof n.children?.[0] === 'string' ? n.children[0] : '').join(' ');

const ROOT = 'session-root-aaaa';
const mk = (target, label, at) => ({
  schema: 2, target, root: ROOT, label, at,
  l0_numbers: 0, l0_materials: 0, l0_restored: 0,
  input_chars: 40000, output_chars: 5000, saved_chars: 35000,
  summary_excerpt: '摘要', engine: 'dsh-engineering-memory-guarded', mode: 'real',
});

/** 把数据塞进组件的 useState 初值槽，然后渲染一次。 */
/**
 * 渲染一次。
 *
 * ⚠️ 不能按下标塞 state slot：组件开头先调 useEngSettings()，它自己会占用槽位，
 *   于是 dataSt 的真实下标并不固定。这里改成【先跑一遍渲染】，再按"哪个槽是
 *   含 entries 的对象"来定位 dataSt —— 与内部实现顺序解耦。
 * ⚠️ exists 必须为 true：sessionView 且 exists===undefined 时会提前 return
 *   「正在读取压缩历史…」，分组根本走不到（第一次跑全红就是这个原因）。
 */
function render(entries, opts) {
  resetHooks();
  const props = {
    rootSessionId: ROOT,
    forceTab: opts && opts.forceTab,
    selectedId: opts && opts.selectedId,
    sessionView: true,
  };
  const data = { ok: true, entries, engine: { mode: 'real' },
    threshold: { value: 200000 }, exists: true, err: null };
  // 第一遍：让组件自己建立槽位
  client.EngCompactPage(props);
  // 定位 dataSt 槽（初值是 { ok, entries:[], counts:[], err }）
  let slot = -1;
  for (let i = 0; i < stateSlots.length; i++) {
    const v = stateSlots[i];
    if (v && typeof v === 'object' && Array.isArray(v.entries) && 'ok' in v) { slot = i; break; }
  }
  if (slot >= 0) stateSlots[slot] = data;
  if (opts && opts.tab) {
    for (let i = 0; i < stateSlots.length; i++) {
      if (stateSlots[i] === 'agent' || stateSlots[i] === 'main') { stateSlots[i] = opts.tab; break; }
    }
  }
  // 第二遍：用真实数据渲染
  cursor = 0;
  return client.EngCompactPage(props);
}

console.log('\n=== 1. 多个子代理 → 必须分组 ===');
{
  const entries = [
    mk(ROOT, '主对话', 1000),
    mk('child-A', 'child-A', 2000),
    mk('child-A', 'child-A', 2100),
    mk('child-B', 'child-B', 3000),
  ];
  const tree = client.EngCompactPage
    ? render(entries)
    : null;
  const nodes = walk(tree);
  ck('出现了分组头 .eng-cp-group', hasClass(nodes, 'eng-cp-group'));
  const heads = nodes.filter(n => String((n.props && n.props.className) || '') === 'eng-cp-group');
  ck('分组数 = 2（child-A / child-B）', heads.length === 2, '实际 ' + heads.length);
  const txt = JSON.stringify(tree);
  ck('组头含子代理名 child-A', txt.includes('child-A'));
  ck('组头含子代理名 child-B', txt.includes('child-B'));
  ck('组头显示次数（2 次）', txt.includes('2 次'));
  // 主对话（target === root）必须被排除在子代理页之外。
  //   判据：所有分组头的 target 都不是 ROOT。原来那条断言把"节点里出现过
  //   '主对话' 字样"和"存在分组"混在一起判，逻辑绕且会误报，改成直接查分组本身。
  const groupIds = heads.map(n => {
    // 组头 key 形如 'g:<target>'，用它反查归属最可靠
    return String((n.props && n.props.key) || '');
  });
  ck('★ 主对话不占分组', !groupIds.some(k => k.includes(ROOT)),
     groupIds.join(','));
  ck('分组恰好是 child-A / child-B',
     groupIds.includes('g:child-A') && groupIds.includes('g:child-B'));
}

console.log('\n=== 2. 只有一个子代理 → 不分组（不制造多余层级） ===');
{
  const entries = [mk(ROOT, '主对话', 1000), mk('child-A', 'child-A', 2000)];
  const nodes = walk(render(entries));
  ck('只有 1 个 → 无分组头', !hasClass(nodes, 'eng-cp-group'));
}

console.log('\n=== 3. 选中具体子代理 → 扁平视图（不再分组） ===');
{
  const entries = [
    mk(ROOT, '主对话', 1000),
    mk('child-A', 'child-A', 2000),
    mk('child-B', 'child-B', 3000),
  ];
  const nodes = walk(render(entries, { selectedId: 'child-A' }));
  ck('selectedId 时无分组头', !hasClass(nodes, 'eng-cp-group'));
}

console.log('\n=== 4. 源码契约 ===');
const src = readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8');
ck('有 openGroup 状态（可折叠）', src.includes('openGroupSt'));
ck('分组按 target 聚合', src.includes('byTarget[gk]'));
ck('组按最近时间倒序', src.includes('return b.lastAt - a.lastAt;'));
ck('组内行不重复显示名字', src.includes('hideName ? null :'));

console.log('\n' + '='.repeat(50));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
