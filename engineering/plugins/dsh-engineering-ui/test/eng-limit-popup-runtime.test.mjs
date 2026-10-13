/**
 * 【第5项·真执行】工程模式"设置提醒弹窗"的运行时契约测试
 * ============================================================================
 * 为什么不能只做源码正则断言：
 *   同目录既有测试都是"读 client.js 文本 + 正则匹配"。那种做法对本功能
 *   **完全无效** —— 曾经出现过这样的真实缺陷：
 *     · `installEngLimitPopup()` 里写了 `ctx.get('commandUi')`，但该函数
 *       定义在 `apply(ctx)` 之外、作用域里根本没有 `ctx` → 抛 ReferenceError
 *       → 被自带的 try/catch 吞掉 → `commandUi.register` **一次都没被调用**。
 *       于是弹窗永远不出现，而所有正则断言（`available: function` 存在、
 *       `kind: 'popupSelect'` 存在……）**全部通过**。
 *     · `tryAutoOpenEngLimitPopup()` 被定义并导出，却**没有任何调用点** →
 *       "点工程模式自动弹出"这条核心需求 0% 实现，正则同样看不出来。
 *   因此本测试**用 vm 真加载 client.js 并真的执行**，断言"行为"而非"文本"。
 *
 * 运行：node --test test/eng-limit-popup-runtime.test.mjs
 * （可用环境变量 DSH_ENG_CLIENT 指向别的 client.js 副本，便于回归对照。）
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLIENT = process.env.DSH_ENG_CLIENT || join(__dirname, '..', 'lib', 'client.js');
const SRC = readFileSync(CLIENT, 'utf8');

/** 最小 React shim：只求值出 exports，不做真渲染。 */
const REACT = {
  createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }),
  Fragment: 'Fragment',
  useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
  useRef: (v) => ({ current: v }),
  useEffect: () => {},
  useMemo: (f) => f(),
};

const LIMIT_KEY = 'dsh-engineering-ui/limit-popup/v1';
const ENG_SESSION = {
  sessionId: 's-eng',
  projections: { faceOf: () => ({ getSnapshot: () => 'engineering' }) },
};
const STD_SESSION = {
  sessionId: 's-std',
  projections: { faceOf: () => ({ getSnapshot: () => 'standard' }) },
};

/**
 * 真加载 client.js 并跑一次 apply()，返回可观测的桩。
 *
 * @param opts.listSnapshot 会话列表快照（默认：一个 mainView 保留的工程会话）
 * @param opts.withCommandUi 是否提供 commandUi 服务
 * @param opts.withScope      sessions.scope 是否返回有效 scope（false → 走降级）
 */
function boot(opts = {}) {
  const store = new Map();
  const registered = [];   // commandUi.register 收到的 contribution
  const opened = [];       // popupFor(...).open(...) 的实参
  const listSubs = [];     // sessions.list 的订阅者
  const infoLogs = [];

  const commandUi = {
    register(c) { registered.push(c); return () => {}; },
    dismiss() {},
    popupFor() {
      if (opts.withScope === false) throw new Error('command.popupFor requires a retained Session scope');
      return { open(...args) { opened.push(args); } };
    },
  };
  const sessions = {
    list: {
      getSnapshot: () => opts.listSnapshot || {
        byId: { 's-eng': { retainedBy: { mainView: 1 }, projectionValues: { agentPreset: 'engineering' } } },
      },
      subscribe(fn) { listSubs.push(fn); return () => {}; },
    },
    scope: () => (opts.withScope === false ? undefined : { sessionId: 's-eng' }),
  };

  const ctx = {
    get: (n) => (n === 'commandUi' ? (opts.withCommandUi === false ? undefined : commandUi)
      : n === 'sessions' ? sessions : undefined),
    effect: () => () => {},
    inject() {},
    slots: { inject() { return () => {}; }, register() { return () => {}; } },
    sessions,
  };

  let client;
  const fakeDoc = (() => {
    // 这个假 document 必须【真的】把 appendChild 的结果登记进 getElementById，
    // 否则"降级卡片到底有没有被插进 body"就无法观测 —— 而那正是本测试
    // 要锁的行为（降级路径不能让用户什么都看不到）。
    const nodes = new Map();          // id → element
    const mk = () => ({
      id: '', style: { cssText: '' }, textContent: '', onclick: null,
      childNodes: [],
      attrs: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      appendChild(child) {
        this.childNodes.push(child);
        if (child) { child.__parent = this; if (child.id) nodes.set(child.id, child); }
        return child;
      },
      remove() {
        Object.keys(nodes).forEach((k) => { if (nodes.get(k) === this) nodes.delete(k); });
        const parent = this.__parent;
        if (parent && parent.childNodes) {
          const i = parent.childNodes.indexOf(this);
          if (i >= 0) parent.childNodes.splice(i, 1);
        }
      },
    });
    const body = mk();
    body.classList = { add() {}, remove() {}, contains: () => false };
    body.appendChild = function (child) {
      body.childNodes.push(child);
      if (child) { child.__parent = body; if (child.id) nodes.set(child.id, child); }
      return child;
    };
    return {
      body,
      getElementById: (id) => nodes.get(id) || null,
      createElement: () => mk(),
      head: { appendChild() {} },
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {}, removeEventListener() {},
    };
  })();
  ctx.__doc = fakeDoc;   // 供本测试观测"降级卡片是否真的插进了 body"
  vm.runInNewContext(SRC, {
    window: { __ModuleLoader__: { load(def) { client = def.factory(() => REACT); } } },
    document: fakeDoc,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    console: {
      log() {}, warn() {}, error() {},
      info(...a) { infoLogs.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout, setInterval, clearInterval,
  });

  let applyError = null;
  try { client.apply(ctx); } catch (e) { applyError = e; }
  // 降级卡片是否真的出现在 body 里（可观测）
  const fallbackShown = !!(ctx.__doc && ctx.__doc.body.childNodes || []).some(
    (n) => n && n.id === 'eng-limits-mask');
  return { client, store, registered, opened, listSubs, infoLogs, applyError, fallbackShown };
}

// ══════════════════════════════════════════════════════════════════════════
// 1. 真执行：apply 不抛，且 contribution **真的**被注册
// ══════════════════════════════════════════════════════════════════════════
test('真执行：apply() 不抛异常', () => {
  const h = boot();
  assert.equal(h.applyError, null, 'apply 不得抛：' + (h.applyError && h.applyError.message));
});

test('真执行：commandUi.register 必须真的被调用一次（不是只写在源码里）', () => {
  const h = boot();
  assert.equal(h.registered.length, 1,
    '必须恰好注册 1 个 contribution；实际 ' + h.registered.length +
    '（若为 0：说明服务读取抛错被吞掉了，弹窗永远不会出现）');
});

test('真执行：contribution 结构契约（name / available / ui 三必填）', () => {
  const c = boot().registered[0];
  assert.ok(c, '必须先注册成功');
  assert.equal(typeof c.name, 'string');
  assert.ok(c.name.length > 0, 'name 不能为空');
  assert.equal(typeof c.available, 'function', 'available 必须存在（官方无条件调用）');
  assert.ok(c.ui && typeof c.ui === 'object', 'ui 必须存在');
});

test('真执行：available 是同步函数（不得是 async，官方每次候选求值都调用）', () => {
  const c = boot().registered[0];
  assert.equal(typeof c.available, 'function');
  assert.notEqual(c.available.constructor.name, 'AsyncFunction',
    'available 必须是同步函数（返回 Promise 会被当成永远为真）');
  // 真实调用一次，返回值必须是布尔、且不是 thenable
  const r = c.available(ENG_SESSION);
  assert.equal(typeof r, 'boolean', 'available 必须返回严格布尔，实际 ' + typeof r);
});

test('真执行：命令名不得与官方宿主命令冲突', () => {
  const c = boot().registered[0];
  for (const reserved of ['model', 'permission', 'file', 'compact', 'export', 'goal', 'plan']) {
    assert.notEqual(c.name, reserved, `name 与官方命令 /${reserved} 重名，register 会抛错`);
  }
});

// ══════════════════════════════════════════════════════════════════════════
// 2. 触发变量：available 的真假语义
// ══════════════════════════════════════════════════════════════════════════
test('available：工程模式 → 真；非工程模式 → 假', () => {
  const c = boot().registered[0];
  assert.equal(c.available(ENG_SESSION), true, '工程模式会话必须可用（弹窗才会出现）');
  assert.equal(c.available(STD_SESSION), false, '非工程模式必须不可用（弹窗不出现）');
});

test('available：工程模式下【即使还没有任何子代理】也必须为真', () => {
  // 这条锁住一个真实缺陷：_isEngineeringContext() 的置位点在 SubagentConsole 内，
  // 而那里有一道 `if (entries.length === 0) return null` 的闸门 ——
  // 即"只有已经存在子代理时它才为真"。而"刚点进工程模式、还没建子代理"
  // 恰恰是最该提醒的时刻。故判据必须以【会话的 agentPreset 投影】为准。
  const h = boot({
    listSnapshot: { byId: { 's-eng': { retainedBy: { mainView: 1 } } } }, // 无子代理目录
  });
  const c = h.registered[0];
  assert.equal(c.available(ENG_SESSION), true,
    '刚进入工程模式（尚无子代理）时 available 必须为真，否则用户在最该看到提醒的时刻看不到');
});

test('available：不可抛（抛了会打断整个命令候选求值）', () => {
  const c = boot().registered[0];
  const hostile = {
    sessionId: 'x',
    get projections() { throw new Error('投影服务炸了'); },
  };
  assert.doesNotThrow(() => c.available(hostile), 'available 遇到异常输入必须内部消化');
  assert.equal(typeof c.available(hostile), 'boolean', '异常时也要返回布尔');
  assert.equal(c.available(null), false, '传 null 也必须安全返回假');
  assert.equal(c.available(undefined), false, '传 undefined 也必须安全返回假');
});

test('available：选"不再提醒"后必须永久返回假', async () => {
  const h = boot();
  const c = h.registered[0];
  assert.equal(c.available(ENG_SESSION), true, '前置条件：一开始可用');
  // 走官方语义：用户在二级确认框点"我确认" → onSelect 才被调用
  await c.ui.onSelect({ id: 'never' }, ENG_SESSION);
  assert.equal(c.available(ENG_SESSION), false, '注销后必须不再可用');
  assert.ok(h.store.get(LIMIT_KEY), '必须写入独立的 localStorage 键');
  const saved = JSON.parse(h.store.get(LIMIT_KEY));
  assert.equal(saved.never, true, '必须写入 never:true');
});

test('永久标记必须用【独立】键，不得并进设置项 settings/v1', async () => {
  const h = boot();
  await h.registered[0].ui.onSelect({ id: 'never' }, ENG_SESSION);
  assert.ok(h.store.has(LIMIT_KEY), '应写入独立键 ' + LIMIT_KEY);
  assert.ok(!h.store.has('dsh-engineering-ui/settings/v1'),
    '不得写进设置页的键（用户明确要求"不能将这个弹窗绑定在设置中"）');
});

test('选"稍后提醒"不得写永久标记（下次进工程模式仍要弹）', async () => {
  const h = boot();
  const c = h.registered[0];
  await c.ui.onSelect({ id: 'later' }, ENG_SESSION);
  assert.equal(h.store.has(LIMIT_KEY), false,
    '"稍后提醒"绝不能写永久标记，否则"稍后"就变成了"不再"');
  assert.equal(c.available(ENG_SESSION), true, '"稍后提醒"后仍应可用');
});

// ══════════════════════════════════════════════════════════════════════════
// 3. popupSelect spec 契约
// ══════════════════════════════════════════════════════════════════════════
test('ui.kind === popupSelect，且 options / onSelect 都是函数', () => {
  const ui = boot().registered[0].ui;
  assert.equal(ui.kind, 'popupSelect');
  assert.equal(typeof ui.options, 'function', 'options 必填');
  assert.equal(typeof ui.onSelect, 'function', 'onSelect 必填');
});

test('options 返回的行：id / label 必填，且恰好是 later 与 never', async () => {
  const ui = boot().registered[0].ui;
  const opts = await ui.options(ENG_SESSION);
  assert.ok(Array.isArray(opts), 'options 必须解析为数组');
  // ⚠️ 必须把跨 realm 的数组/对象"本地化"再比较：
  //   opts 来自 vm.runInNewContext 里的另一个 realm，它的 Array 原型与本文件
  //   的不是同一个 → assert.deepStrictEqual 会因原型不同而误判失败
  //   （expected/actual 打印出来一模一样却报错）。故用 join 成字符串比较。
  assert.equal(opts.map((o) => o.id).join(','), 'later,never',
    'popupSelect 只能单选列表（NOTES §6），"两个按键"= 恰好两行 later / never');
  for (const o of opts) {
    assert.equal(typeof o.id, 'string', 'id 必填（React key）');
    assert.ok(o.id.length > 0, 'id 不能为空');
    assert.equal(typeof o.label, 'string', 'label 必填（渲染 + 匹配）');
    assert.ok(o.label.length > 0, 'label 不能为空');
  }
  // 标签必须是用户指定的那两个按键文案
  const byId = Object.fromEntries(opts.map((o) => [o.id, o]));
  assert.equal(byId.later.label, '稍后提醒');
  assert.equal(byId.never.label, '不再提醒');
});

test('never 行带 confirmation，且含 title / cancelLabel / confirmLabel', async () => {
  const ui = boot().registered[0].ui;
  const never = (await ui.options(ENG_SESSION)).find((o) => o.id === 'never');
  assert.ok(never.confirmation, 'never 行必须带 confirmation（官方唯一的嵌套确认框）');
  for (const k of ['title', 'cancelLabel', 'confirmLabel']) {
    assert.equal(typeof never.confirmation[k], 'string',
      `confirmation 必须含 ${k}（官方 RiskConfirmation 会读）`);
  }
  // beyond：官方实际用到五个字段
  for (const k of ['description', 'acknowledgeLabel']) {
    assert.ok(never.confirmation[k], `confirmation 建议含 ${k}`);
  }
  assert.equal(never.confirmation.cancelLabel, '好的，我回去修改',
    'cancelLabel = "好的，我回去修改"（官方语义：取消 → 回到仍打开的上一层选择器）');
  assert.equal(never.confirmation.confirmLabel, '我确认', 'confirmLabel = "我确认"');
  assert.ok(never.confirmation.title.includes('请一定要修改'),
    '二级弹窗标题必须是用户指定的那句话');
  // 官方 risk gate 强制勾选框：confirmLabel 在勾选前是禁用的（asar 452918）
  assert.ok(never.confirmation.acknowledgeLabel,
    'acknowledgeLabel 必须给，否则勾选框无文案、用户无法解锁"我确认"');
});

test('later 行【不得】带 confirmation（它应当直接关闭）', async () => {
  const ui = boot().registered[0].ui;
  const later = (await ui.options(ENG_SESSION)).find((o) => o.id === 'later');
  assert.equal(later.confirmation, undefined,
    'later 必须一步到位，挂 confirmation 会让"稍后提醒"多出一层不必要的确认');
});

test('弹窗正文必须真的渲染出来（含 999 与两项设置名）', async () => {
  const ui = boot().registered[0].ui;
  const opts = await ui.options(ENG_SESSION);
  // popupSelect 没有自由正文区（NOTES §10-5），detail 是官方外壳里唯一能承载
  // 整段文字的渲染位（asar 402924，常驻渲染、不依赖高亮）。因此正文必须落在 detail。
  const all = opts.map((o) => String(o.detail || '')).join('\n');
  assert.ok(all.includes('999'), '用户指定的正文（含 999）必须出现在弹窗里，实际 detail=' + JSON.stringify(opts.map((o) => o.detail)));
  assert.ok(all.includes('最大递进深度'), '正文须提到「最大递进深度」');
  assert.ok(all.includes('子智能体并行上限'), '正文须提到「子智能体并行上限」');
  assert.ok(all.includes('预设模式将无法正常生效'), '正文须含用户指定的警示语');
});

test('onSelect 对未知 id 必须安全（不抛、不误写标记）', async () => {
  const h = boot();
  const c = h.registered[0];
  await assert.doesNotReject(() => c.ui.onSelect({ id: '__nope__' }, ENG_SESSION));
  await assert.doesNotReject(() => c.ui.onSelect(null, ENG_SESSION));
  assert.equal(h.store.has(LIMIT_KEY), false, '未知 id 不得写永久标记');
});

// ── 自动弹出的 spec 与 contribution 是【两份】onSelect，必须行为一致 ──────
// 反证测试（mutation testing）暴露过这个盲区：只断言 contribution 的 onSelect，
// 会把"自动弹出那份 onSelect 写错"完全放过。两处都是真实代码路径，都要锁。
test('自动弹出的 spec：onSelect 对 never / later 的行为必须与 contribution 一致', async () => {
  const h = boot();
  const autoSpec = h.opened[0] && h.opened[0][1];
  assert.ok(autoSpec && typeof autoSpec.onSelect === 'function',
    '自动打开时传入的 spec 必须有 onSelect');
  // later → 不写标记
  await autoSpec.onSelect({ id: 'later' }, ENG_SESSION);
  assert.equal(h.store.has(LIMIT_KEY), false,
    '自动弹出这条路径上，"稍后提醒"也不得写永久标记');
  // never → 写标记
  await autoSpec.onSelect({ id: 'never' }, ENG_SESSION);
  assert.equal(h.store.has(LIMIT_KEY), true,
    '自动弹出这条路径上，"不再提醒"必须写永久标记（否则注销不生效）');
  assert.equal(JSON.parse(h.store.get(LIMIT_KEY)).never, true);
});

test('自动弹出的 spec：options 内容必须与 contribution 一致（同源，不重复维护）', async () => {
  const h = boot();
  const autoSpec = h.opened[0] && h.opened[0][1];
  const autoOpts = await autoSpec.options(ENG_SESSION);
  const regOpts = await h.registered[0].ui.options(ENG_SESSION);
  assert.equal(autoOpts.map((o) => o.id).join(','), 'later,never', '自动弹出也该是两行');
  assert.equal(autoOpts.map((o) => o.id).join(','), regOpts.map((o) => o.id).join(','),
    '两条路径的选项必须一致');
  const autoNever = autoOpts.find((o) => o.id === 'never');
  assert.ok(autoNever.confirmation, '自动弹出路径的 never 行也必须带 confirmation');
  assert.equal(autoNever.confirmation.confirmLabel, '我确认');
  assert.equal(autoNever.confirmation.cancelLabel, '好的，我回去修改');
});

// ══════════════════════════════════════════════════════════════════════════
// 4. 自动弹出：必须有【真实调用点】并真的触发 popupFor().open()
// ══════════════════════════════════════════════════════════════════════════
test('自动弹出：进入工程模式时必须真的调用 popupFor().open()', () => {
  const h = boot();
  assert.equal(h.opened.length, 1,
    'apply() 之后必须已经自动打开过一次弹窗；实际 ' + h.opened.length +
    '（若为 0：说明自动弹出函数只是被定义/导出，从未被调用 —— 核心需求未实现）');
  const [cmd, spec, context, segment] = h.opened[0];
  assert.equal(cmd, h.registered[0].name, 'open 的命令名必须与注册名一致');
  assert.equal(spec.kind, 'popupSelect', '自动打开也必须用 popupSelect spec');
  assert.equal(typeof spec.options, 'function');
  assert.equal(typeof spec.onSelect, 'function');
  assert.equal(segment && segment.via, 'enter', 'segment 必须带 via（内部通道契约）');
  assert.equal(segment && segment.token, '/' + cmd, 'token 必须是 /命令名');
});

test('自动弹出：必须接在会话列表变更上（否则切会话不会触发）', () => {
  const h = boot();
  assert.ok(h.listSubs.length >= 1,
    '必须订阅 sessions.list，用户切到/新建工程模式会话时才能再次驱动');
});

test('自动弹出：非工程模式会话不得自动弹出', () => {
  const h = boot({
    listSnapshot: { byId: { 's-std': { retainedBy: { mainView: 1 }, projectionValues: { agentPreset: 'standard' } } } },
  });
  assert.equal(h.opened.length, 0, '标准模式绝不能弹（用户要求只在工程模式强制提醒）');
});

test('自动弹出：防轰炸 —— 每个会话最多自动弹一次', () => {
  const h = boot();
  const before = h.opened.length;
  assert.ok(before >= 1, '前置条件：已弹过一次');
  // 模拟会话列表反复变更（available 每次候选求值都会被调用，不能每次都弹）
  for (let i = 0; i < 8; i++) h.listSubs.forEach((fn) => fn());
  assert.equal(h.opened.length, before,
    '重复触发会话列表变更不得重复弹窗（否则用户关掉就被立刻重新弹出）');
});

test('自动弹出：已选"不再提醒"后不得再自动弹', async () => {
  const h = boot();
  await h.registered[0].ui.onSelect({ id: 'never' }, ENG_SESSION);
  const after = h.opened.length;
  for (let i = 0; i < 5; i++) h.listSubs.forEach((fn) => fn());
  assert.equal(h.opened.length, after, '永久注销后不得再自动弹');
});

test('自动弹出：官方通道不可用时必须优雅降级（不抛到插件外）', () => {
  // withScope:false → popupFor 抛 "requires a retained Session scope"
  const h = boot({ withScope: false });
  assert.equal(h.applyError, null, '自动弹出失败绝不能抛到 apply 之外');
  assert.equal(h.opened.length, 0, '官方通道确实没走通');
  // 降级：必须留下用户可见/可操作的东西（自绘卡片 或 至少一条可操作提示）
  const usedFallback = h.infoLogs.some((s) => s.includes('eng-subagent-limits'));
  assert.ok(usedFallback || h.fallbackShown,
    '降级路径必须给用户可操作的出路（自绘卡片或提示输入 /命令）');
});

test('缺少 commandUi 服务的 profile：插件必须静默降级，不抛错', () => {
  const h = boot({ withCommandUi: false });
  assert.equal(h.applyError, null, '没有 commandUi 服务时 apply 不得抛');
  assert.equal(h.registered.length, 0, '服务不在时无从注册，应当是 0 而不是崩');
});

// ══════════════════════════════════════════════════════════════════════════
// 5. 生命周期与 inject
// ══════════════════════════════════════════════════════════════════════════
test('inject 必须声明 commandUi（否则无该服务的 profile 会抛错而非不激活）', () => {
  const m = SRC.match(/exports\.inject\s*=\s*\[([^\]]*)\]/);
  assert.ok(m, '应能找到 exports.inject');
  assert.ok(m[1].includes("'commandUi'"), 'inject 必须含 commandUi，当前为: ' + m[1].trim());
});

test('不得 import @deepseek-ai/dsh-client-ui-primitives（授权规则禁止）', () => {
  // 只在【代码】里禁止，注释里提到包名是允许的（NOTES 本身就要求注释说明原因）。
  const code = SRC.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!/require\(\s*['"]@deepseek-ai\/dsh-client-ui-primitives/.test(code),
    'NOTES §10 / 授权规则 line 292609：禁止 require 该包；自绘 UI 必须自带标记与 CSS');
  assert.ok(!/from\s+['"]@deepseek-ai\/dsh-client-ui-primitives/.test(code),
    '不得 import 该包（只允许在注释里说明为什么不能用）');
});
