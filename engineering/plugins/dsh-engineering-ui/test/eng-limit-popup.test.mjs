/**
 * 【第5项】工程模式"设置提醒弹窗"的契约测试
 * ============================================================================
 * 用户需求：进入工程模式时强制弹出提醒（把插件的「最大递进深度」与
 * 「子智能体并行上限」都设为 999），两个按键「稍后提醒」/「不再提醒」，
 * 「不再提醒」再弹一个二级小弹窗（"我回去修改" 回到上一层 / "我确认" 永久注销）。
 *
 * ══ 本测试的第二版：从"正则断言"升级为"真执行" ═══════════════════════════
 * 【为什么必须升级】第一版全是源码静态断言（assert.match(src, /.../)），
 *   只证明"字符存在"，**证明不了代码真的被执行**。它全绿却掩盖了一个致命缺陷：
 *   安装函数定义在 apply(ctx) 之外，却写了 `ctx.get('commandUi')` ——
 *   那里没有 ctx 在作用域，抛 ReferenceError 被内部 catch 吞掉 →
 *   commandUi.register **从未被调用** → 弹窗永远不出现。
 *   静态断言看不出这种问题，因此这里用 vm 真加载模块 + 真调 apply()，
 *   断言 register 真的被调用、contribution 结构真的可用。
 *
 * 运行：node --test test/eng-limit-popup.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'lib', 'client.js'), 'utf8');

/** 取某函数体（从 `function NAME(` 到下一个顶层函数/exports 为止）。 */
function fnBody(name) {
  const i = SRC.indexOf('function ' + name);
  if (i < 0) return '';
  const rest = SRC.slice(i);
  const j = rest.indexOf('\n    function ', 10);
  const k = rest.indexOf('\n    exports.', 10);
  const ends = [j, k].filter((x) => x > 0);
  return ends.length ? rest.slice(0, Math.min(...ends)) : rest.slice(0, 4000);
}

/**
 * 用 vm 真加载 client.js，返回 { mod, registered, calls, openCalls, sandbox }。
 * 只提供最小可用的砂箱；定时器给"只记录不执行"的空实现，
 * 避免 apply 里的后台循环把测试进程挂住。
 */
function loadModule() {
  const registered = [];
  const calls = [];
  const openCalls = [];
  const logs = [];
  let listSubscriber = null;

  const fakeCommandUi = {
    register(c) { registered.push(c); calls.push('register:' + (c && c.name)); return () => {}; },
    decorate() { return () => {}; },
    dismiss() {},
    popupFor() {
      calls.push('popupFor');
      return { open(cmd, spec, ctx, seg) { openCalls.push({ cmd, spec, ctx, seg }); } };
    },
  };
  // 会话列表：默认把 s1 当作【工程模式】会话（agentPreset=engineering）
  const fakeList = {
    _subs: [],
    getSnapshot() {
      return {
        activeId: 's1',
        byId: {
          s1: { sessionId: 's1', agentPreset: 'engineering' },
          s2: { sessionId: 's2', agentPreset: 'default' },
        },
      };
    },
    subscribe(fn) { this._subs.push(fn); listSubscriber = fn; return () => {}; },
  };
  const fakeCtx = {
    get(name) {
      calls.push('ctx.get:' + name);
      if (name === 'commandUi') return fakeCommandUi;
      if (name === 'sessions') return { scope: () => ({ id: 'scope-s1' }), list: fakeList };
      if (name === 'slots') return { inject: () => (() => {}), register: () => (() => {}) };
      return undefined;
    },
    on() { return () => {}; },
    effect(fn) { try { fn(); } catch (e) { /* ignore */ } return () => {}; },
    slots: { inject: () => (() => {}), register: () => (() => {}) },
    inject() { return (() => {}); },
  };

  function makeEl() {
    const el = {
      style: {}, dataset: {},
      classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
      children: [],
      setAttribute() {}, getAttribute: () => null, removeAttribute() {},
      appendChild(c) { el.children.push(c); return c; },
      removeChild() {}, insertBefore(c) { return c; }, remove() {},
      addEventListener() {}, removeEventListener() {},
      querySelector: () => null, querySelectorAll: () => [],
      getBoundingClientRect: () => ({ width: 100, height: 100, top: 0, left: 0 }),
      hasAttribute: () => false, focus() {}, blur() {}, click() {},
      innerHTML: '', textContent: '', value: '',
    };
    return el;
  }

  const sandbox = {
    console: { log() {}, info(...a) { logs.push(a.join(' ')); }, warn() {}, error() {} },
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    localStorage: {
      _d: {},
      getItem(k) { return this._d[k] ?? null; },
      setItem(k, v) { this._d[k] = String(v); },
      removeItem(k) { delete this._d[k]; },
      key: () => null,
      get length() { return Object.keys(this._d).length; },
    },
    document: {
      querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
      addEventListener() {}, removeEventListener() {},
      createElement: makeEl, createTextNode: makeEl,
      head: makeEl(), body: makeEl(), documentElement: makeEl(), readyState: 'complete',
    },
    window: { addEventListener() {}, removeEventListener() {}, location: { href: '' }, confirm: () => true },
    React: null,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    MutationObserver: function () { return { observe() {}, disconnect() {} }; },
  };
  sandbox.globalThis = sandbox;
  sandbox.__require = () => ({});
  sandbox.window.__ModuleLoader__ = {
    load(spec) { sandbox.__loaded = spec.factory(sandbox.__require); },
  };

  const ctxObj = vm.createContext(sandbox);
  vm.runInContext(SRC, ctxObj, { filename: 'client.js' });

  let applyErr = null;
  const mod = sandbox.__loaded;
  try { mod.apply(fakeCtx); } catch (e) { applyErr = e.message; }

  return { mod, registered, calls, openCalls, logs, sandbox,
           getListSubscriber: () => listSubscriber, applyErr };
}

// ══════════════════════════════════════════════════════════════════════════
// 一、★ 真执行断言：这些是"代码真的跑起来"才成立的
// ══════════════════════════════════════════════════════════════════════════

test('★真执行：模块能被 vm 加载（无语法/顶层错误）', () => {
  const { mod } = loadModule();
  assert.ok(mod, '模块应被 __ModuleLoader__ 加载出 exports');
  assert.equal(typeof mod.apply, 'function', '必须导出 apply');
});

test('★★真执行：apply() 之后 commandUi.register 必须【真的被调用】', () => {
  // 这条是本测试的核心 —— 第一版静态断言全绿却漏掉了"register 从未被调用"。
  const { registered, calls, applyErr } = loadModule();
  assert.equal(applyErr, null, 'apply 不应抛错：' + applyErr);
  assert.ok(registered.length > 0,
    '★ commandUi.register 必须被调用（修复前为 0 次）。calls=' + JSON.stringify(calls));
  assert.ok(registered.some((c) => c && c.name === 'eng-subagent-limits'),
    '必须注册名为 eng-subagent-limits 的 contribution，实际=' +
    JSON.stringify(registered.map((c) => c && c.name)));
});

test('★真执行：不得在 apply 之外用 ctx（会抛 ReferenceError 被吞掉）', () => {
  // 根因断言：apply() 之外的函数体里出现 `ctx.get` ⇒ 服务读不到。
  // ⚠️ 必须先剥掉注释：修复说明里会【提到】ctx.get（"原先写 ctx.get ..."），
  //    不剥注释会把解释文字当成违规代码，产生假失败（本测试第一版就踩了）。
  const stripComments = (s) => s
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  for (const fn of ['installEngLimitPopup', 'tryAutoOpenEngLimitPopup',
                    'showEngLimitFallbackCard']) {
    const body = stripComments(fnBody(fn));
    assert.ok(!/\bctx\.get\b/.test(body),
      `${fn} 定义在 apply 之外，不得直接用 ctx.get（应用 engService()）`);
    if (/\bengService\(/.test(body) === false && /\bctx\b/.test(body)) {
      assert.fail(`${fn} 若需要服务，必须走 engService()`);
    }
  }
});

test('★真执行：contribution 的结构在运行时可被消费', () => {
  const { registered } = loadModule();
  const c = registered.find((x) => x && x.name === 'eng-subagent-limits');
  assert.ok(c, '应注册 eng-subagent-limits');
  assert.equal(typeof c.available, 'function', 'available 必须是函数');
  assert.notEqual(c.available.constructor.name, 'AsyncFunction',
    'available 必须是同步的（官方在候选求值时无条件调用）');
  assert.equal(c.ui && c.ui.kind, 'popupSelect', "ui.kind 必须是 popupSelect");
  assert.equal(typeof c.ui.options, 'function', 'options 必须是函数');
  assert.equal(typeof c.ui.onSelect, 'function', 'onSelect 必须是函数');
});

test('★真执行：available 在非工程模式返回假、且不可抛', () => {
  const { registered } = loadModule();
  const c = registered.find((x) => x && x.name === 'eng-subagent-limits');
  // 明确给一个【非工程模式】会话（必须带 agentPreset，否则会走兜底判据）
  let v = null;
  assert.doesNotThrow(() => {
    v = c.available({ sessionId: 's2', projectionValues: { agentPreset: 'default' } });
  }, 'available 不得抛错（抛了会打断整个候选求值）');
  assert.equal(v, false, '非工程模式时 available 应为 false');
  // 畸形入参也不得抛
  assert.doesNotThrow(() => c.available(null), 'available(null) 不得抛错');
  assert.doesNotThrow(() => c.available({}), 'available({}) 不得抛错');
});

test('★真执行：options() 返回恰好两行 later / never，二级弹窗五字段齐全', async () => {
  const { registered } = loadModule();
  const c = registered.find((x) => x && x.name === 'eng-subagent-limits');
  const opts = await c.ui.options({ sessionId: 's1' });
  assert.equal(opts.length, 2, '应恰好两行（popupSelect 无多按钮）');
  // ⚠️ 不能用 deepEqual 比数组：vm 里造出来的数组属【另一个 realm】，
  //    其原型与宿主 Array.prototype 不同，deepStrictEqual 会因此失败。
  //    转成字符串再比，语义相同且跨 realm 稳定。
  assert.equal(opts.map((o) => o.id).join(','), 'later,never');
  assert.equal(opts[0].label, '稍后提醒');
  assert.equal(opts[1].label, '不再提醒');
  const cf = opts[1].confirmation;
  assert.ok(cf, 'never 行必须带 confirmation（官方唯一的二级弹窗）');
  for (const k of ['title', 'description', 'acknowledgeLabel', 'cancelLabel', 'confirmLabel']) {
    assert.ok(cf[k], `confirmation 缺字段 ${k}`);
  }
  assert.equal(cf.cancelLabel, '好的，我回去修改');
  assert.equal(cf.confirmLabel, '我确认');
  assert.ok(cf.title.includes('请一定要修改'), '二级标题应为用户指定文案');
});

test('★真执行：onSelect(never) 写入永久标记；onSelect(later) 不写', async () => {
  // ── later：不得写永久标记（否则"稍后提醒"就变成"不再提醒"了）──
  const A = loadModule();
  const ca = A.registered.find((x) => x && x.name === 'eng-subagent-limits');
  await ca.ui.onSelect({ id: 'later' }, { sessionId: 's1' });
  const rawA = A.sandbox.localStorage.getItem('dsh-engineering-ui/limit-popup/v1');
  assert.ok(!rawA || JSON.parse(rawA).never !== true,
    'later 不得写入 never 标记，实际=' + rawA);
  assert.equal(ca.available({ sessionId: 's1', projectionValues: { agentPreset: 'engineering' } }),
    true, 'later 之后在工程模式下仍应为可用（下次还会提醒）');

  // ── never：必须写入永久标记 ──
  const B = loadModule();
  const cb = B.registered.find((x) => x && x.name === 'eng-subagent-limits');
  await cb.ui.onSelect({ id: 'never' }, { sessionId: 's1' });
  const rawB = B.sandbox.localStorage.getItem('dsh-engineering-ui/limit-popup/v1');
  assert.ok(rawB, 'never 必须写入 localStorage');
  assert.equal(JSON.parse(rawB).never, true, 'never 标记应为 true，实际=' + rawB);
  assert.equal(cb.available({ sessionId: 's1', projectionValues: { agentPreset: 'engineering' } }),
    false, '★ never 之后即使处于工程模式也必须为 false（永久注销）');
});

test('★真执行：降级卡片函数存在且不抛（自动弹出失败时用户仍看得见）', () => {
  const { mod } = loadModule();
  assert.equal(typeof mod.showEngLimitFallbackCard, 'function',
    '必须提供自绘降级卡片（子代理指出：否则自动弹出失败时用户什么都看不到）');
  assert.doesNotThrow(() => { mod.showEngLimitFallbackCard(); },
    '降级卡片不得抛错');
  assert.ok(SRC.includes('eng-limits-mask'), '降级卡片应有 eng-limits-mask 容器');
  // 降级 UI 必须带自己的类名前缀；不得【真的 require/import】官方 primitives
  //   （授权规则禁止）。注意只查真实引用，不查注释里的提及 ——
  //   上一版直接搜字符串，把我自己写的解释性注释也算成了违规。
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/require\(\s*['"]@deepseek-ai\/dsh-client-ui-primitives['"]\s*\)/.test(code),
    '不得 require 官方 ui-primitives（授权规则禁止）');
  assert.ok(!/from\s+['"]@deepseek-ai\/dsh-client-ui-primitives['"]/.test(code),
    '不得 import 官方 ui-primitives（授权规则禁止）');
});

test('★真执行：自动弹出失败时【必须走降级卡片】而不是静默 return false', () => {
  const body = fnBody('tryAutoOpenEngLimitPopup');
  // 每一个 return false 的分支都应改为调降级卡片
  const returns = body.match(/return false;/g) || [];
  // ⚠️ 正则必须容忍实参：showEngLimitFallbackCard 现已带 (session, preChecked)，
  //   原先写死 `showEngLimitFallbackCard\(\)` 匹配不到任何一处 → 恒报"实际=0"的假失败。
  const fallbacks = body.match(/showEngLimitFallbackCard\s*\(/g) || [];
  assert.ok(fallbacks.length >= 3,
    '至少 3 处失败分支应降级到卡片（无 popupFor / 无 scope / 异常），实际=' +
    fallbacks.length + '（return false 有 ' + returns.length + ' 处）');
});

// ══════════════════════════════════════════════════════════════════════════
// 一之补、★ 三个"静态断言看不出来"的真实缺陷（子代理 vm 实测发现）
// ══════════════════════════════════════════════════════════════════════════

test('★★缺陷1修复：自动弹出必须【真的被触发】（原先零调用点）', () => {
  // 根因：tryAutoOpenEngLimitPopup 全文只有"定义"和"导出"两处，无调用点。
  //   → 即便 register 成功，用户不手动输入 /命令 就永远看不到弹窗。
  const { openCalls, getListSubscriber, logs } = loadModule();
  assert.equal(typeof getListSubscriber(), 'function',
    '必须订阅会话列表作为触发源（否则自动弹出永不发生）');
  assert.ok(openCalls.length >= 1,
    '★ apply 之后应已自动 open（修复前为 0 次）。日志=' +
    JSON.stringify(logs).slice(0, 200));
  assert.equal(openCalls[0].cmd, 'eng-subagent-limits',
    'open 的命令名必须是 eng-subagent-limits');
});

test('★★缺陷2修复：还没建子代理时也必须判为工程模式', () => {
  // 根因：_engContextActive 的置位点在 SubagentConsole 内，
  //   前面有 `if (entries.length === 0) { _engContextActive = false; return null; }`
  //   → 刚进工程模式、还没建任何子代理时恒为 false，
  //     而那正是本提醒最该出现的时刻。
  const { mod } = loadModule();
  assert.equal(typeof mod._sessionIsEngineering, 'function',
    '应提供会话身份判据 _sessionIsEngineering');
  assert.equal(
    mod._sessionIsEngineering({ sessionId: 's1', projectionValues: { agentPreset: 'engineering' } }),
    true, '★ 工程模式会话（无子代理）应判为 true');
  assert.equal(
    mod._sessionIsEngineering({ sessionId: 's2', projectionValues: { agentPreset: 'default' } }),
    false, '普通会话应判为 false');
  assert.equal(mod._sessionIsEngineering(null), false, 'null 应安全返回 false');
  assert.doesNotThrow(() => mod._sessionIsEngineering({}, undefined),
    '畸形入参不得抛错');
});

test('★★缺陷2修复：available 收 session 并按会话身份判定', () => {
  const { registered } = loadModule();
  const c = registered.find((x) => x && x.name === 'eng-subagent-limits');
  assert.equal(c.available.length, 1,
    'available 必须接收 session 参数（官方签名 available(session)）');
  assert.equal(c.available({ sessionId: 's1', projectionValues: { agentPreset: 'engineering' } }),
    true, '★ 工程模式（无子代理）时 available 必须为 true');
  assert.equal(c.available({ sessionId: 's2', projectionValues: { agentPreset: 'default' } }),
    false, '非工程模式应为 false');
});

test('★★缺陷3修复：整段警告正文必须出现在 detail 里（唯一可承载它的渲染位）', async () => {
  // 根因：popupSelect 没有自由正文区，渲染词汇表只有 label/badge/detail/active，
  //   detail 是唯一能承载整段文字的位置。原先 detail 是短句，
  //   用户看不到那段完整警告 → 等于提醒没生效。
  const { registered } = loadModule();
  const c = registered.find((x) => x && x.name === 'eng-subagent-limits');
  const opts = await c.ui.options({ sessionId: 's1' });
  const d0 = String(opts[0].detail || '');
  assert.ok(d0.includes('最大递进深度'), 'later.detail 必须含「最大递进深度」');
  assert.ok(d0.includes('子智能体并行上限'), 'later.detail 必须含「子智能体并行上限」');
  assert.ok(d0.includes('999'), 'later.detail 必须含 999');
  assert.ok(d0.includes('预设模式将无法正常生效'), 'later.detail 必须含警示语');
  assert.ok(String(opts[1].detail || '').includes('999'), 'never.detail 也必须含 999');
});

test('★★缺陷修复：_engLimitTried 记账点必须在"确实即将 open"之后', () => {
  // 根因：原先在函数开头就置位，首次因 scope 未保留而失败时
  //   一次性机会被白白烧掉，此后再不重试。
  const body = fnBody('tryAutoOpenEngLimitPopup');
  const iTried = body.indexOf('_engLimitTried[sessionId] = true');
  const iOpen = body.indexOf('ctl.open(');
  assert.ok(iTried > 0 && iOpen > 0, '应能找到记账点与 open 调用');
  assert.ok(iTried < iOpen, '记账应在 open 之前（两者相邻）');
  // 首个失败分支（无 popupFor）之前不应有置位
  const iGuard = body.indexOf('typeof cmdUi.popupFor');
  const firstSet = body.indexOf('_engLimitTried[sessionId] = true');
  assert.ok(iGuard < firstSet,
    '不应在能力检查之前就置位（否则机会被浪费）');
});

// ══════════════════════════════════════════════════════════════════════════
// 二、源码层契约（保留第一版有价值的静态断言）
// ══════════════════════════════════════════════════════════════════════════

test('第5项：必须通过 ctx.commandUi.register 注册（走官方弹窗外壳）', () => {
  assert.ok(SRC.includes('cmdUi.register('), '必须调用 commandUi.register()');
  assert.ok(SRC.includes('engService('),
    '在 apply 之外读服务必须用既有助手 engService()');
});

test('第5项：触发变量必须复用工程模式判据（与聊天/工作切换器同一个）', () => {
  assert.match(fnBody('installEngLimitPopup'), /_isEngineeringContext\(\)/,
    'available 必须复用 _isEngineeringContext()');
});

test('第5项：不得把这个弹窗绑定到设置页', () => {
  const body = fnBody('installEngLimitPopup');
  assert.ok(!/_engSettings\[/.test(body),
    'installEngLimitPopup 不得读写设置项（不得绑设置页）');
  assert.ok(SRC.includes('dsh-engineering-ui/limit-popup/v1'),
    '应有独立 localStorage 键存放"不再提醒"标记');
});

test('第5项：弹窗正文必须与用户给的原话一致（含 999 与两项设置名）', () => {
  const i = SRC.indexOf('var ENG_LIMIT_TITLE');
  const seg = SRC.slice(i, i + 600);
  assert.ok(seg.includes('最大递进深度'), '正文须提到「最大递进深度」');
  assert.ok(seg.includes('子智能体并行上限'), '正文须提到「子智能体并行上限」');
  assert.ok(seg.includes('999'), '正文须提到 999');
  assert.ok(seg.includes('预设模式将无法正常生效'), '正文须含警示语');
});

test('第5项：自动弹出必须有防轰炸闸门且 try/catch', () => {
  const body = fnBody('tryAutoOpenEngLimitPopup');
  assert.match(body, /_engLimitTried\[sessionId\]/,
    '必须按 sessionId 防重复自动弹出');
  assert.match(body, /try\s*\{/, '必须有 try');
  assert.match(body, /catch\s*\(/, '必须有 catch');
});

test('第5项：inject 必须声明 commandUi（否则无该服务的 profile 会抛错）', () => {
  const m = SRC.match(/exports\.inject\s*=\s*\[([^\]]*)\]/);
  assert.ok(m, '应能找到 exports.inject');
  assert.ok(m[1].includes("'commandUi'"),
    'inject 必须含 commandUi，当前为: ' + m[1].trim());
});

test('第5项：不得与官方已占用的命令名冲突（会抛 duplicate/collision）', () => {
  const reserved = ['model', 'permission', 'file', 'compact', 'export', 'goal', 'plan'];
  const m = SRC.match(/var ENG_LIMIT_CMD\s*=\s*'([^']+)'/);
  assert.ok(m, '应能找到 ENG_LIMIT_CMD 定义');
  assert.ok(!reserved.includes(m[1]),
    `ENG_LIMIT_CMD='${m[1]}' 与官方命令重名，注册会抛错`);
  assert.ok(m[1].startsWith('eng-'), '自建命令应带 eng- 前缀，当前: ' + m[1]);
});
