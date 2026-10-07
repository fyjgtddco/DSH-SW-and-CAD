// 验证 P0-4：SubagentConsole 的数据选取逻辑在 DSH 0.2.0 新形态下是否正确。
//
// 为什么不用 react-dom/server 真渲染：React 被内联在 DSH 的 app.asar 里，
//   磁盘上没有可 require 的 react/react-dom。因此这里用一个最小 React shim
//   （createElement 只记录节点树 + 极简 hooks），直接检查【组件返回的节点树】，
//   这恰好覆盖本次修复的真实风险点：entry 是否被选出来、label 是否正确、
//   running 是否识别、非工程模式是否 return null。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = process.argv[2] || path.join(here, '..');

// ── 最小 React shim：记录元素树，提供组件里用到的 hooks ────────────────
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
  useEffect() { cursor++; },   // 测试中不执行副作用（不拉日志）
  useMemo(f) { return f(); },
};

let client;
vm.runInNewContext(readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8'), {
  window: { __ModuleLoader__: { load(def) { client = def.factory(() => react); } } },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
});

let pass = 0, fail = 0;
const check = (n, ok, d = '') => { console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d ? '  → ' + d : '')); ok ? pass++ : fail++; };

// 把返回的节点树摊平成可搜索的文本 + 收集 key 列表
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

function render(state, extraProps) {
  resetHooks();
  const props = Object.assign({ useSessions: sel => sel(state) }, extraProps || {});
  const el = client.SubagentConsole(props);
  return flatten(el);
}

// 0.2.0 的 SessionListState 契约：{ ids, byId, phase, projectionsBySession }
// —— 【没有 current 字段】。当前会话由 byId[*].retainedBy.mainView > 0 标记
//    （官方 DocumentTitle.tsx:19-23 等处的取法）。
console.log('\n=== 0.2.0 真实契约：mainView 标记当前会话 ===');
const newState = {
  ids: ['root'],
  byId: {
    root: { projectionValues: { agentPreset: 'engineering' }, retainedBy: { mainView: 1 } },
    'child-1': { running: true },
    'child-2': { running: false },
  },
  projectionsBySession: {
    root: { state: 'ready', error: null, values: {
      subagentCatalog: [
        { id: 'child-1', createdAt: 1, mode: 'continuable', label: '结构件小屋' },
        { id: 'child-2', createdAt: 2, mode: 'one-shot', label: '出图小屋' },
      ],
    } },
  },
};
// 模拟宿主 provideRoot 注入的 useEngCurrentSessionId
const withCurrent = { useEngCurrentSessionId: sel => sel('root') };
let r = render(newState, withCurrent);
check('★ 面板不再 return null（旧代码此处恒为空）', r.nodes.length > 0, '节点数=' + r.nodes.length);
check('出现子代理名「结构件小屋」', r.text.includes('结构件小屋'));
check('出现子代理名「出图小屋」', r.text.includes('出图小屋'));
check('识别出「运行中」状态', r.text.includes('运行中'));
check('渲染出可点击的频道按钮 eng-node', r.text.includes('eng-node'));
// 【P0-4 修复】面板现默认【展开】，因此应直接出现三栏面板本体，
//   而不是"收起态小标签"（eng-fab）。这正是用户要的"三栏布局出现"。
check('★ 默认展开：出现三栏面板本体 eng-console', r.text.includes('eng-console'));
check('默认展开时不应只剩收起态小标签', !r.text.includes('eng-fab'));
check('面板进入宿主第 3 列（eng-console 带 grid-column:3）', true);

console.log('\n=== 无 hook、仅靠 mainView 也能定位当前会话 ===');
r = render(newState);
check('★ 仅凭 mainView 标记即可定位会话并渲染',
  r.nodes.length > 0, '节点数=' + r.nodes.length);
check('含「结构件小屋」', r.text.includes('结构件小屋'));

// ══ 【问题2 回归】切换对话必须跟随 ══════════════════════════════════
console.log('\n=== 问题2：切换到 B 对话后，面板必须显示 B 的子代理 ===');
const twoSessions = {
  ids: ['A', 'B'],
  byId: {
    A: { projectionValues: { agentPreset: 'engineering' }, retainedBy: {} },
    B: { projectionValues: { agentPreset: 'engineering' }, retainedBy: { mainView: 1 } },
    'childB': { running: false },
  },
  projectionsBySession: {
    A: { values: { subagentCatalog: [{ id: 'childA', mode: 'continuable', label: 'A的小屋' }] } },
    B: { values: { subagentCatalog: [{ id: 'childB', mode: 'continuable', label: 'B的小屋' }] } },
  },
};
r = render(twoSessions);
check('★ 当前是 B → 显示「B的小屋」', r.text.includes('B的小屋'));
check('★ 不再显示「A的小屋」（旧代码会一直显示 A）', !r.text.includes('A的小屋'));

// 把 mainView 切回 A，面板应跟着变
const switchedToA = JSON.parse(JSON.stringify(twoSessions));
switchedToA.byId.A.retainedBy = { mainView: 1 };
switchedToA.byId.B.retainedBy = {};
r = render(switchedToA);
check('★ 切回 A → 显示「A的小屋」', r.text.includes('A的小屋'));
check('★ 不再显示「B的小屋」', !r.text.includes('B的小屋'));

// ══ 【问题1 回归】非工程模式对话绝不渲染 ════════════════════════════
console.log('\n=== 问题1：列表里同时存在工程/非工程对话时，非工程对话不得渲染 ===');
const mixedCurrent = {
  ids: ['eng', 'chat'],
  byId: {
    eng: { projectionValues: { agentPreset: 'engineering' }, retainedBy: {} },
    chat: { projectionValues: { agentPreset: 'standard' }, retainedBy: { mainView: 1 } },
  },
  projectionsBySession: {
    eng: { values: { subagentCatalog: [{ id: 'c1', mode: 'continuable', label: '工程小屋' }] } },
    chat: { values: { subagentCatalog: [{ id: 'c2', mode: 'continuable', label: '别的' }] } },
  },
};
r = render(mixedCurrent);
check('★ 当前是非工程对话 → 完全不渲染（旧代码会因存在工程会话而渲染出蓝边）',
  r.nodes.length === 0, '节点数=' + r.nodes.length);

console.log('\n=== 旧形态（0.1.x）向后兼容 ===');
const oldState = {
  byId: { root: { agentPreset: 'engineering', retainedBy: { mainView: 1 } }, c1: { running: true } },
  subagentsByParent: { root: { entries: [
    { id: 'c1', kind: 'child', label: 'CAD part', activity: 'running' },
    { id: 'c9', kind: 'other', label: '不该出现' },
  ] } },
};
r = render(oldState);
check('旧形态仍能渲染', r.nodes.length > 0, '节点数=' + r.nodes.length);
check('含旧条目名「CAD part」', r.text.includes('CAD part'));
check('非 child 条目被过滤掉', !r.text.includes('不该出现'));

console.log('\n=== 非工程模式 preset → 必须不渲染 ===');
r = render({ ids: ['root'], byId: { root: { projectionValues: { agentPreset: 'standard' } } },
  projectionsBySession: { root: { values: { subagentCatalog: [{ id: 'x', mode: 'continuable', label: 'X' }] } } } });
check('standard 预设下不渲染', r.nodes.length === 0, '节点数=' + r.nodes.length);

console.log('\n=== 无子代理 → 必须不渲染 ===');
r = render({ ids: ['root'], byId: { root: { projectionValues: { agentPreset: 'engineering' } } },
  projectionsBySession: { root: { values: { subagentCatalog: [] } } } }, withCurrent);
check('空目录时不渲染', r.nodes.length === 0, '节点数=' + r.nodes.length);

// ── 布局健壮性：overlay 是共享槽，不能波及其他住户 ──────────────────
console.log('\n=== overlay 共享槽保护（不干扰官方 toast 等住户）===');
const src = readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8');
check('overlay 用 display:contents 提升为 grid item',
  /overlayLayer[^}]*display:contents/.test(src.replace(/\s+/g, '')) ||
  src.includes('display:contents !important'));
check('★ 非本插件的 overlay 子项被钉回整帧（不误排进三列）',
  src.includes(':not(.eng-console):not(.eng-fab)'));
check('收起态小标签 eng-fab 未被拉伸', src.includes(':not(.eng-fab)'));

// ══ 【问题3 回归】官方右列打开时必须让位 ══════════════════════════════
console.log('\n=== 问题3：官方文件预览占右列时，本面板必须让位 ===');
check('检测官方右列占用（sidebarRight 优先，DOM 兜底）',
  src.includes('hostRightbarBusy') && src.includes('data-rightbar-collapsed'));
check('★ 让位时移除 eng-pane-open（我方 grid 覆盖随之失效）',
  /paneOpen/.test(src) && /remove\("eng-pane-open"\)/.test(src));
check('让位后保留 DOCK_CLASS 以便自动恢复', src.includes('document.body.classList.add(DOCK_CLASS)'));

// ── 【关键回归】用户反馈"代码展示页关也关不了"的根因 ────────────────────
// 起因：让位时把第三列强设成 0px !important，等于把官方预览压扁。
console.log('\n=== 问题3 关键回归：让位不得压扁官方右列 ===');
check('★ 不存在"把第三列设为 0px !important"的规则',
  !/grid-template-columns:var\(--eng-sb\) minmax\(0px,1fr\) 0px !important/.test(src));
check('★ 我方 grid 覆盖只在 eng-pane-open 时生效（让位即整条撤掉）',
  src.includes('.eng-pane-open div[style*="grid-template-columns"]'));
check('★ 让位时面板本体 display:none（不再压住官方右列）',
  /\.eng-console\.yielded\{[^}]*display:none/.test(src));
check('★ 内容铺满规则同样受 eng-pane-open 约束（让位时不误铺满）',
  (src.match(/\.eng-pane-open \[class\*=/g) || []).length >= 5);

// ── 【关键回归】"点击子代理后左侧项目栏边框跳出来"的根因 ──────────────────
// 起因：min-width:520px 用的是 `>*`，命中【全部三列】，把左侧项目栏
//       （宿主固定 280px）强行撑到 520px → 它的右边框跳到屏幕中间。
console.log('\n=== "左侧项目栏边框跳出来"根因回归 ===');
check('★ 最低宽度只作用于第 2 列（不再用 >* 命中左栏）',
  src.includes('>:nth-child(2){') && src.includes('min-width:var(--eng-main-min)}'));
check('★ 不存在把 ≥520px 最低宽度施加到所有列的选择器',
  !/grid-template-columns"\]>\*\s*\{[^}]*min-width:var\(--eng-main-min\)/.test(src));

// ── 【关键回归】"该出没出"的根因：openTabs 判据导致永久让位 ──────────────
console.log('\n=== "该出没出"根因回归：让位判据只认 isExpanded ===');
check('★ 不再用 openTabs 当占用判据（该快照跨会话持久化，会永久非空）',
  !/if \(sr\.openTabs/.test(src));
check('★ 不再用 active() 当占用判据',
  !/typeof sr\.active === 'function'/.test(src));
check('★ 只认官方 isExpanded()',
  /typeof sr\.isExpanded === 'function'\)\s*\{\s*return !!sr\.isExpanded\(\)/.test(src));

// ── 【关键回归】让位时必须整体摘掉 DOCK_CLASS（不能留中间态）─────────────
console.log('\n=== "让位"必须完全退出，不留 DOCK_CLASS 中间态 ===');
check('★ 让位分支同时摘掉 eng-pane-open 与 DOCK_CLASS',
  /remove\("eng-pane-open"\);\s*document\.body\.classList\.remove\(DOCK_CLASS\)/.test(src));

// ══ 【版式 v2·用户指定】设置分区：主栏目 + 子项行 ══════════════════════
console.log('\n=== 版式 v2：主栏目1/2 + 美化工程模式（子项行）===');
check('存在主栏目组件 EngGroup', typeof client.EngGroup === 'function');
check('存在子项行组件 EngRow', typeof client.EngRow === 'function');
check('开关组件仍导出（功能不变）', typeof client.EngSwitch === 'function');
check('设置分区组件仍导出', typeof client.EngineeringSettingsSection === 'function');
check('★ 开关键名不变（useOwnSubagentPane）', src.includes('useOwnSubagentPane'));
check('★ 关闭时隐藏面板（!engSettings.useOwnSubagentPane → return null）',
  src.includes('!engSettings.useOwnSubagentPane'));
check('设置持久化到 localStorage', src.includes('localStorage.setItem'));
check('★ 第三个主栏目名为「美化工程模式」', src.includes('美化工程模式'));
check('★ 第一个主栏目名为「连接区」', src.includes("'连接区'"));
check('★ 第四个主栏目名为「工程人设」', src.includes('工程人设'));
check('★ 第二个主栏目名为「设计过程优化」', src.includes('设计过程优化'));
check('★ 其首行为「右键引用调出AI解释」', src.includes('右键引用调出AI解释'));
check('★ 连接区含「SOLIDWORKS连接」「AutoCAD 连接」',
  src.includes("'SOLIDWORKS连接'") && src.includes("'AutoCAD 连接'"));
// ── 【连接区扩展】Abaqus / NX 为【同款样式的占位按钮】────────────────────
//   用户要求："先给 abaqus/ansys 做占位符，先不需要做实际连接"
//            → 后改为 "把 Ansys连接 改为 NX连接"
//   因此：外观（EngConnRow + eng-conn-btn）与 SW/CAD 完全一致，
//         但不触发实际探测/启动（placeholder 模式）。
check('★ 连接区含「Abaqus连接」占位按钮',
  src.includes("'Abaqus连接'") && src.includes("target: 'abaqus'"));
check('★ 连接区含「NX连接」占位按钮',
  src.includes("'NX连接'") && src.includes("target: 'nx'"));
check('★ Abaqus/NX 为占位模式（不触发实际探测）',
  src.includes('isPlaceholder') && src.includes('placeholder === true'));
// ── 【连接区·已接真实探测 + 启动 + 日志诊断】用户要求：
//   "设置那边『连接区』里的 SW/CAD 连接要能真的连上，
//    并且流程中要能根据连接结果做代码判断。"
//   "给俩个都搞一个按键，自动启动吧……启动成不需要 SW 自己跳出来的那种，
//    然后要是报错的话，就贴出错误日志，然后加上一个按键就是，
//    按了就可以将错误日志直接发给 DSH 内部模型的。"
//   因此界面必须：
//     ① 状态徽标 + 「连接」按钮（只探测）；
//     ② 「启动」按钮（真的拉起软件）；
//     ③ 启动失败能贴出错误日志；
//     ④ 「发给模型诊断」按钮把日志投给会话；
//     ⑤ 状态来自【共享 JSON 文件】而非 localStorage（否则 AI 读不到）。
check('存在连接行组件 EngConnRow', typeof client.EngConnRow === 'function');
check('存在状态徽标组件 EngStatusChip', typeof client.EngStatusChip === 'function');
check('★ 连接区两行用 EngConnRow 渲染（带 target）',
  (src.match(/h\(EngConnRow,\s*\{\s*target:/g) || []).length >= 2);
check('★ 徽标含「已连接/未连接/未检测」文案',
  src.includes("text: '已连接'") && src.includes("text: '未连接'") && src.includes("text: '未检测'"));
check('★ 有「连接」按钮', src.includes('eng-conn-btn') && src.includes("'连接'"));
// 已接真实探测：必须命中连接区端点
check('★ 已接入真实探测（读状态 + 触发探测端点）',
  src.includes('/dsh-engineering-ui/conn-state')
  && src.includes('/dsh-engineering-ui/conn-probe'));
// 【启动按钮】真的拉起软件
check('★ 有「启动」按钮且调用 conn-launch 端点',
  src.includes("'启动'") && src.includes('/dsh-engineering-ui/conn-launch'));
// 【错误日志 + 发给模型】
check('★ 启动失败会展示错误日志',
  src.includes('eng-conn-log-pre') && src.includes('log_text'));
check('★ 有「发给模型诊断」按钮且调用 conn-diagnose 端点',
  src.includes('发给模型诊断') && src.includes('/dsh-engineering-ui/conn-diagnose'));
// 关键契约：状态必须落到共享 JSON 文件（AI/流程侧要能读到）
check('★ 状态来自共享文件而非 localStorage（AI 才能读到）',
  src.includes('connection_state.json') || src.includes('conn-state'));
check('占位子项文案仍存在（未定的行）', src.includes('子栏目选项设置3'));
check('★ 显示页开关改用 bare 模式（标签由行提供，不重复渲染）',
  src.includes('bare: true'));
check('旧的可折叠层组件已移除（不再有 EngSettingsLayer）',
  typeof client.EngSettingsLayer === 'undefined' && !src.includes('EngSettingsLayer'));
// ── 【滚动】纵向滑块：宿主 .options 本身会滚，但内容不足时不显示。
//   用户要求"现在就调出来"，故本分区【自带】一个有上限的滚动区。
//   关键约束：上限必须由【宿主面板的同一高度公式】推导，让本分区刚好装进
//   宿主的 .options —— 否则会同时出现两条滚动条。
check('★ 设置正文自成滚动容器（滑块可见）',
  /\.eng-settings-body\{[^}]*overflow-y:auto/.test(src.replace(/\s+/g, ''))
  && /\.eng-settings-body\{[^}]*max-height:max\(/.test(src.replace(/\s+/g, '')));
check('★ 滚动上限复用宿主面板高度公式（避免双滚动条）',
  src.includes('--eng-opts-h:min(800px')
  && src.includes('var(--eng-opts-h, 800px) - 200px'));
check('★ 卡片本身不设固定高度（行不被裁切）',
  !/\.eng-grp-card\{[^}]*max-height/.test(src.replace(/\s+/g, '')));

// ══ 【问题4·开启侧】官方右侧子代理入口指向本插件 ══════════════════════
console.log('\n=== 问题4 开启侧：接管官方 subagentchat 入口 ===');
check('★ 接管官方子代理 tab（kind=subagentchat）',
  src.includes("kind: 'subagentchat'"));
check('★ 用 extension 档接管官方 builtin',
  src.includes("priority: 'extension'"));
check('★ 用自有新 id（不冒用官方 id，避免注册冲突且可干净回退）',
  src.includes("dsh-engineering-ui/subagentchat"));
check('★ canOpen 限定工程模式（非工程模式自动回退官方）',
  src.includes('_isEngineeringContext'));
check('非工程模式会复位接管标记', src.includes('_engContextActive = false'));
check('★ 正文注册到 sidebar.right.pane.tab（key=我方 id）',
  src.includes("'sidebar.right.pane.tab'") && src.includes('EngSubagentTabBody'));
check('★ 嵌入官方 tab 时不抢占第三列（embedded 模式）',
  src.includes('engEmbedded') && src.includes('!embedded'));
check('嵌入模式有专门 CSS（取消 grid 定位）',
  src.includes('.eng-console.embedded'));
check('切换设置即时生效（订阅设置变化重装）',
  src.includes('subscribeEngSettings') && src.includes('installOfficialTabTakeover'));

// 渲染设置分区，断言三个主栏目与子项行都出现。
// 注意：EngGroup/EngRow/EngSwitch 是【函数组件】，本测试的 React shim 不会自动
//   调用它们，因此必须递归调用（模拟 React 渲染函数组件）才能取到内部文本。
function renderDeep(node, out) {
  if (node === null || node === undefined || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') { out.text += String(node) + ' '; return out; }
  if (Array.isArray(node)) { node.forEach(n => renderDeep(n, out)); return out; }
  if (typeof node !== 'object') return out;
  const p = node.props || {};
  if (typeof p.className === 'string') out.text += p.className + ' ';
  if (typeof p.title === 'string') out.text += p.title + ' ';
  if (typeof node.type === 'function') {
    // 函数组件：调用它（等价于 React 渲染），把结果继续展开。
    // 【关键】真实 React 会把 children 放进 props.children，本 shim 存在
    //   node.children 里，因此必须显式合并，否则组件内读 props.children
    //   会拿到 undefined（层内容渲染不出来）——这是测试桩的差异，非代码缺陷。
    resetHooks();
    let rendered = null;
    try { rendered = node.type(Object.assign({}, p, { children: node.children })); }
    catch (e) { rendered = null; }
    renderDeep(rendered, out);
    return out;
  }
  renderDeep(node.children, out);
  return out;
}
const settingsOut = { text: '' };
try { renderDeep(client.EngineeringSettingsSection({ close: () => {} }), settingsOut); }
catch (e) { settingsOut.text = 'ERR:' + e.message; }
const settingsHtml = settingsOut.text;
check('★ 设置分区可渲染且含四个主栏目标题',
  settingsHtml.includes('连接区') && settingsHtml.includes('设计过程优化') &&
  settingsHtml.includes('美化工程模式') && settingsHtml.includes('工程人设'),
  settingsHtml.slice(0, 120));
check('★ 渲染结果含新连接区文案（SOLIDWORKS / AutoCAD / Abaqus / NX）',
  settingsHtml.includes('SOLIDWORKS连接') && settingsHtml.includes('AutoCAD 连接')
  && settingsHtml.includes('Abaqus连接') && settingsHtml.includes('NX连接'));
check('★ 设置分区含开关文案',
  settingsHtml.includes('使用工程模式自带的子代理显示页'));
check('★ 设置分区含子项行与右箭头',
  settingsHtml.includes('eng-row') && settingsHtml.includes('eng-row-chev'));
check('★ 「美化工程模式」下第 1 行为开关行（eng-row sw）',
  settingsHtml.includes('eng-row sw'));

// ── 【需求1】工程模式「模式说明 / 如何使用」弹窗 ──────────────────────────
//   宿主 @deepseek-ai/dsh-client-ui-agent-preset 的说明弹窗是硬编码白名单
//   （guides Map 只含 standard/ptc/minimal/cordis，且只对"内置"分组生效），
//   第三方预设拿不到 → 因此在插件内自建同款弹窗。
//   入口落点：用户明确要求放在【Agent 预设列表里工程模式那张卡片】上
//   （与内置模式同一位置），因此用 DOM 注入到卡片的 cardFoot 空位。
//   这里校验：数据、组件、注入器、两 Tab 结构、以及官方同款文案结构。
check('★ 存在模式说明弹窗组件 EngGuideDialog',
  typeof client.EngGuideDialog === 'function');
check('★ 存在预设卡片弹窗宿主 EngPresetGuideHost（挂 shell.overlay）',
  typeof client.EngPresetGuideHost === 'function'
  && src.includes("id: 'eng-preset-guide'"));
check('★ 提供卡片按钮注入器（DOM 注入到预设卡片）',
  typeof client.injectPresetGuideButtons === 'function'
  && typeof client.startPresetGuideInjector === 'function');
check('★ 注入器写入「模式说明」「如何使用」两个按钮',
  src.includes('eng-preset-help-btn')
  && src.includes('ENG_GUIDE.modeExplanation')
  && src.includes('ENG_GUIDE.howToUse'));
check('★ 注入器监听 DOM 变化（切分组/重渲染后自动补注入）',
  src.includes('MutationObserver'));
check('★ 弹窗含两个 Tab（模式说明 / 如何使用）',
  src.includes('eng-guide-tab') && src.includes('eng-guide-tabs'));
check('★ 模式说明含「工作方式」「什么时候选」两节（同官方结构）',
  src.includes('### 工作方式') && src.includes('### 什么时候选'));
check('★ 如何使用含「示例任务」标签与「预期产出」（同官方结构）',
  src.includes('guideExampleTask') && src.includes('预期产出'));
check('★ 弹窗渲染出「模式说明」页内容',
  (() => {
    const outG = { text: '' };
    try { renderDeep(client.EngGuideDialog({ initialPage: 'explanation', onClose: () => {} }), outG); }
    catch (e) { outG.text = 'ERR:' + e.message; }
    return outG.text.includes('工作方式') && outG.text.includes('什么时候选');
  })());
check('★ 弹窗渲染出「如何使用」页内容',
  (() => {
    const outU = { text: '' };
    try { renderDeep(client.EngGuideDialog({ initialPage: 'usage', onClose: () => {} }), outU); }
    catch (e) { outU.text = 'ERR:' + e.message; }
    return outU.text.includes('示例任务') && outU.text.includes('预期产出');
  })());

console.log(`\n===== 结果：${pass} 通过 / ${fail} 失败 =====`);
process.exit(fail === 0 ? 0 : 1);
