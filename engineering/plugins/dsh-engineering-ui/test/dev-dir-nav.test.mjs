// 验证「开发者使用」的两级导航：目录页 → 单个子面板 → 返回目录。
//
// 为什么不用 react-dom/server 真渲染：React 内联在 DSH 的 app.asar 里，
//   磁盘上没有可 require 的 react。因此沿用本目录既有测试的 vm + 最小 React shim
//   方案（见 subagent-panel.test.mjs），直接检查组件返回的节点树 ——
//   这恰好覆盖本次改动的真实风险：目录项是否正确、面板是否互斥、返回目标是否对。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = process.argv[2] || path.join(here, '..');

const stateSlots = [];
let cursor = 0;
function resetHooks() { cursor = 0; stateSlots.length = 0; }
const react = {
  createElement(type, props, ...children) {
    return { type, props: props || {}, children: children.flat().filter(c => c !== null && c !== undefined && c !== false) };
  },
  Fragment: 'Fragment',
  useState(init) {
    const i = cursor++;
    if (stateSlots.length <= i) stateSlots[i] = typeof init === 'function' ? init() : init;
    return [stateSlots[i], (v) => { stateSlots[i] = typeof v === 'function' ? v(stateSlots[i]) : v; }];
  },
  useRef(v) { const i = cursor++; if (stateSlots.length <= i) stateSlots[i] = { current: v }; return stateSlots[i]; },
  useEffect() { cursor++; },   // 不执行副作用：测试里不真的去拉日志
  useMemo(f) { return f(); },
  useCallback(f) { return f; },
};

let client;
vm.runInNewContext(readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8'), {
  window: { __ModuleLoader__: { load(def) { client = def.factory(() => react); } } },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
});

function flatten(node, out = { text: '', nodes: [] }) {
  if (node === null || node === undefined || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') { out.text += String(node) + ' '; return out; }
  if (Array.isArray(node)) { node.forEach(n => flatten(n, out)); return out; }
  if (typeof node !== 'object') return out;
  out.nodes.push(node);
  const p = node.props || {};
  if (typeof p.className === 'string') out.text += p.className + ' ';
  if (typeof p.title === 'string') out.text += p.title + ' ';
  flatten(node.children, out);
  return out;
}

/**
 * 渲染指定页面。
 * EngSessionViewPage 的页面状态存在它自己的 useState 里；这里通过
 * 「先渲染 home，再模拟点击卡片」的方式推进，确保走的是真实导航逻辑。
 */
function renderAt(pages) {
  resetHooks();
  const sid = 'session-test';
  let tree = client.EngSessionViewPage({ sessionId: sid });
  let flat = flatten(tree);
  for (const want of pages) {
    // 在上一棵树里找带 want 文案的按钮，模拟点击
    const btn = flat.nodes.find(n => n.type === 'button'
      && JSON.stringify(n.children || []).includes(want));
    if (!btn) return { flat, failedAt: want };
    stateSlots.length = 0;  // 保留 state 槽但重置 cursor 以重跑 hooks
    const snapshot = stateSlots.slice();
    resetHooks();
    stateSlots.push(...snapshot);
    resetHooks();
    // 先推进到目标页：调用组件的 go() 等价于 setNavigation
    // —— 这里直接改 EngSessionViewPage 内部持有的 navigation 槽。
    //    navigation 是本组件的第一个 useState，槽位 0。
    stateSlots[0] = { sid, page: pageOf(want) };
    cursor = 0;
    tree = client.EngSessionViewPage({ sessionId: sid });
    flat = flatten(tree);
  }
  return { flat };
}

function pageOf(label) {
  if (label === '日常使用') return 'daily';
  if (label === '开发者使用') return 'developer';
  if (label === '七阶段运行日志') return 'dev-seven';
  if (label === '外部监控插件') return 'dev-monitor';
  return label;
}

/** 直接渲染指定已导航页面，返回【原始节点树】（供组件级断言用）。 */
function renderTree(page) {
  resetHooks();
  const sid = 'session-test';
  stateSlots[0] = page === 'home' ? null : { sid, page };
  cursor = 0;
  return client.EngSessionViewPage({ sessionId: sid });
}

/** 直接渲染指定已导航页面（更可控，避免点击模拟的脆弱性）。 */
function renderPage(page) {
  resetHooks();
  const sid = 'session-test';
  stateSlots[0] = page === 'home' ? null : { sid, page };
  cursor = 0;
  const tree = client.EngSessionViewPage({ sessionId: sid });
  return flatten(tree);
}

let pass = 0, fail = 0;
const check = (n, ok, d = '') => { console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d ? '  → ' + d : '')); ok ? pass++ : fail++; };

console.log('\n=== 1. 首页：栏目入口仍是两张卡片 ===');
const home = renderPage('home');
check('首页出现「栏目入口」', home.text.includes('栏目入口'));
check('首页有「日常使用」卡片', home.text.includes('日常使用'));
check('首页有「开发者使用」卡片', home.text.includes('开发者使用'));
check('首页不再直接渲染日志面板', !home.text.includes('外部监控插件 · 诊断日志'));

console.log('\n=== 1b. 首页不再有「画布」占位块（2026-11-28） ===');
// 用户明确要求删除：它没有任何数据源，却吃掉首屏近一半高度，
//   把「工程流程 / 交付资料」挤到折叠线以下。
check('首页没有「画布」标题', !home.text.includes('画布'));
check('首页没有「AI 投屏占符位」', !home.text.includes('AI 投屏占符位'));
check('首页不再渲染 eng-sv-cast 容器',
  !home.nodes.some(n => String((n.props && n.props.className) || '').split(/\s+/).includes('eng-sv-cast')));

console.log('\n=== 2. 开发者使用 = 目录页（不是面板） ===');
const dev = renderPage('developer');
check('标题是「开发者使用」', dev.text.includes('开发者使用'));
check('出现「开发者目录」', dev.text.includes('开发者目录'));
check('目录含「七阶段运行日志」', dev.text.includes('七阶段运行日志'));
check('目录含「外部监控插件」', dev.text.includes('外部监控插件'));
check('目录页有说明「只读」', dev.text.includes('只读'));
check('★ 目录页不渲染任何日志面板', !dev.text.includes('外部监控插件 · 诊断日志'));
check('目录页不渲染七阶段面板', !dev.text.includes('七阶段运行日志\u0000') && !dev.text.includes('子步骤进度'));
check('有返回「栏目首页」按钮', dev.text.includes('← 栏目首页'));
check('有「切换到日常使用」按钮', dev.text.includes('切换到日常使用'));

console.log('\n=== 3. 子面板：只渲染自己那一块 ===');
const seven = renderPage('dev-seven');
check('七阶段面板标题正确', seven.text.includes('七阶段运行日志'));
check('★ 七阶段页【不含】监控器面板', !seven.text.includes('外部监控插件 · 诊断日志'));
check('★ 返回按钮是「← 开发者目录」', seven.text.includes('← 开发者目录'));
check('提供互跳按钮「看外部监控插件」', seven.text.includes('看外部监控插件'));

const mon = renderPage('dev-monitor');
check('监控器面板标题正确', mon.text.includes('外部监控插件'));
check('★ 监控器页【不含】七阶段面板', !mon.text.includes('子步骤进度'));
check('★ 返回按钮是「← 开发者目录」', mon.text.includes('← 开发者目录'));
check('提供互跳按钮「看七阶段日志」', mon.text.includes('看七阶段日志'));

console.log('\n=== 4. 日常使用 = 两级（2026-11-28） ===');
const daily = renderPage('daily');
check('日常使用是目录页', daily.text.includes('日常目录'));
check('目录含「主对话压缩历史」', daily.text.includes('主对话压缩历史'));
check('目录含「子代理压缩历史」', daily.text.includes('子代理压缩历史'));
check('★ 目录页不渲染压缩面板', !collectComponents(renderTree('daily')).includes('EngCompactPage'));
check('返回「栏目首页」', daily.text.includes('← 栏目首页'));
check('切换到开发者使用', daily.text.includes('切换到开发者使用'));

const dmain = renderPage('daily-main');
const dagent = renderPage('daily-agent');
check('主对话页标题正确', dmain.text.includes('主对话压缩历史'));
check('主对话页返回「← 日常目录」', dmain.text.includes('← 日常目录'));
check('子代理页标题正确', dagent.text.includes('子代理压缩历史'));
check('子代理页返回「← 日常目录」', dagent.text.includes('← 日常目录'));
check('主对话页提供互跳', dmain.text.includes('看子代理压缩历史'));
check('子代理页提供互跳', dagent.text.includes('看主对话压缩历史'));
check('★ 主对话页渲染 EngCompactPage', collectComponents(renderTree('daily-main')).includes('EngCompactPage'));
check('★ 子代理页渲染 EngCompactPage', collectComponents(renderTree('daily-agent')).includes('EngCompactPage'));
// forceTab 必须分别锁死两条链 —— 否则两个页面会长得一模一样
const srcDaily = readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8');
check('daily-main 锁 forceTab=main', /forceTab:\s*'main'/.test(srcDaily));
check('daily-agent 锁 forceTab=agent', /forceTab:\s*'agent'/.test(srcDaily));

console.log('\n=== 5. 面板互斥：任何时刻只渲染一块 ===');
// ⚠️ 判据必须用【组件根节点的 className】，不能用文案：
//   · 「外部监控插件」这几个字在目录卡片和互跳按钮里也有 → 文案会假阳性；
//   · 七阶段的「子步骤进度」由 view.mjs 异步挂载，静态树里根本不存在；
//   · 插件里本就有一个 id="dsh-engineering-runlog-style"（样式），
//     用 includes('eng-runlog') 也会命中它 → 必须精确匹配 className 分词。
const hasPanelClass = (t, cls) => t.nodes.some(n =>
  String((n.props && n.props.className) || '').split(/\s+/).includes(cls));
// 面板【组件】本身不会被展开成树（h(EngRunLog, …) 只是把组件引用放进 children），
//   所以"到底渲染的是哪个面板"要靠组件名判定。
const panelComponents = (t) => t.nodes
  .filter(n => typeof n.type === 'function')
  .map(n => n.type.name || '');
// 扁平化时组件节点会被 push 进 nodes —— 但它们没有 className，
//   需要单独从 children 里捞。改用递归收集所有 function 类型节点。
function collectComponents(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach(n => collectComponents(n, out)); return out; }
  if (typeof node.type === 'function') out.push(node.type.name || '');
  (node.children || []).forEach(c => collectComponents(c, out));
  return out;
}
for (const p of ['developer', 'dev-seven', 'dev-monitor']) {
  const t = renderPage(p);
  const hasSeven = hasPanelClass(t, 'eng-runlog');
  const hasMon = hasPanelClass(t, 'eng-mlog');
  check(p + ' 最多只渲染一块面板', !(hasSeven && hasMon),
    'seven=' + hasSeven + ' monitor=' + hasMon);
}
// 注意：renderPage 返回的是【扁平化结果】{text, nodes}，组件断言必须用 renderTree。
const sevenComps = collectComponents(renderTree('dev-seven'));
const monComps = collectComponents(renderTree('dev-monitor'));
const devComps = collectComponents(renderTree('developer'));
check('dev-seven 确实渲染了 EngRunLog', sevenComps.includes('EngRunLog'), sevenComps.join(','));
check('dev-seven 没有 EngMonitorLog', !sevenComps.includes('EngMonitorLog'));
check('dev-monitor 确实渲染了 EngMonitorLog', monComps.includes('EngMonitorLog'), monComps.join(','));
check('dev-monitor 没有 EngRunLog', !monComps.includes('EngRunLog'));
check('developer 目录页两块都不渲染',
  !devComps.includes('EngRunLog') && !devComps.includes('EngMonitorLog'), devComps.join(','));

console.log('\n' + '='.repeat(60));
console.log('PASS=' + pass + ' FAIL=' + fail);
console.log('='.repeat(60));
process.exit(fail ? 1 : 0);
