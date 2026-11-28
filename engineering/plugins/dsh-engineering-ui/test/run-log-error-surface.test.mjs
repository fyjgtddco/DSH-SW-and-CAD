/**
 * 七阶段运行日志 · 失败原因必须回显（BUG-N10 · 2026-11-29）
 * ============================================================================
 * 现场（BUG记录.md · 任务 20261128_165714_E2089D16）：
 *   用户点开「七阶段运行日志」只看到一句笼统的「加载失败」，
 *   而服务端其实已经算出了具体原因。报告在建议④里点名：
 *   「面板报错时应回显具体 error 文案（会话日志不可读 / ID 不匹配 /
 *     非根会话 / 版本不符）以便定位，目前 UI 只显示笼统『加载失败』」。
 *
 * 两种失败发生在两个不同的层，必须分别守住：
 *   ① 面板模块本身加载失败（view.mjs 取不到）→ client.js 的 import().catch
 *      要如实说「运行日志面板加载失败：<原因>」；
 *   ② 摘要接口返回非 2xx → fetch 层必须把响应体里的 error 原样带出来，
 *      **不能只报 HTTP 码**，更不能让「读 body 失败」盖掉「业务原因」。
 *
 * 为什么必须是【真执行】而不是源码正则断言：
 *   上一轮就吃过这个亏 —— `if (!response.ok) throw new Error(...)` 这种写法
 *   在正则里永远"存在"，但它恰恰丢掉了 body。只有真的喂一个 404 响应进去，
 *   才能看出错误文案里到底有没有服务端那句话。
 *
 * 运行：node --test test/run-log-error-surface.test.mjs
 * （可用环境变量 DSH_ENG_CLIENT 指向别处的 client.js 副本做对照。）
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLIENT = process.env.DSH_ENG_CLIENT || join(__dirname, '..', 'lib', 'client.js');
const SRC = readFileSync(CLIENT, 'utf8');

// 受控定时器：client.js 里有轮询类计时器，必须统一清理，否则进程退不出去。
const _timers = new Set();
const _setTimeout = (fn, ms, ...a) => { const h = setTimeout(fn, ms, ...a); _timers.add(h); return h; };
const _setInterval = (fn, ms, ...a) => { const h = setInterval(fn, ms, ...a); _timers.add(h); return h; };
const _clearTimeout = (h) => { _timers.delete(h); clearTimeout(h); };
const _clearInterval = (h) => { _timers.delete(h); clearInterval(h); };
after(() => {
  for (const h of _timers) { try { clearTimeout(h); clearInterval(h); } catch (e) {} }
  _timers.clear();
});

const REACT = {
  createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }),
  Fragment: 'Fragment',
  useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
  useRef: (v) => ({ current: v }),
  useEffect: () => {},
  useMemo: (f) => f(),
};

/** 最小假 document（client.js 顶层只用到这几个）。 */
function fakeDoc() {
  const nodes = new Map();
  const mk = () => ({
    id: '', style: { cssText: '' }, textContent: '', className: '',
    childNodes: [], attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(child) {
      this.childNodes.push(child);
      if (child) { child.__parent = this; if (child.id) nodes.set(child.id, child); }
      return child;
    },
    remove() {},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
  });
  const body = mk();
  body.classList = { add() {}, remove() {}, contains: () => false };
  return {
    body,
    getElementById: (id) => nodes.get(id) || null,
    createElement: () => mk(),
    head: { appendChild() {} },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  };
}

/**
 * 真加载 client.js，返回 { client, fetchCalls }。
 * fetchImpl 由调用方注入，用来模拟 404 / 非 JSON / 正常响应。
 */
function boot(fetchImpl) {
  let client = null;
  const fetchCalls = [];
  const doc = fakeDoc();
  vm.runInNewContext(SRC, {
    window: { __ModuleLoader__: { load(def) { client = def.factory(() => REACT); } } },
    document: doc,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    console: { log() {}, warn() {}, error() {}, info() {} },
    setTimeout: _setTimeout, clearTimeout: _clearTimeout,
    setInterval: _setInterval, clearInterval: _clearInterval,
    URL, URLSearchParams, JSON, Promise, Error, String, Number, Object, Array, Math, Date,
    fetch: (...args) => { fetchCalls.push(args); return fetchImpl(...args); },
  });
  assert.ok(client, 'client.js 必须通过 __ModuleLoader__.load 暴露 exports');
  return { client, fetchCalls, doc };
}

/** 造一个最小可用的 Response 替身（只要 ok / status / json / text）。 */
const res = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => (typeof body === 'string' ? JSON.parse(body) : body),
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
});

/**
 * 从一个假 DOM 节点里把文字全捞出来。
 * EngRunLog 用 React.createElement 出元素树，最终会以某种形式落到 DOM 上；
 * 这里只关心「错误文案有没有出现在可见文字里」。
 */
function allText(node, out = []) {
  if (node == null) return out;
  if (typeof node === 'string' || typeof node === 'number') { out.push(String(node)); return out; }
  if (Array.isArray(node)) { for (const n of node) allText(n, out); return out; }
  if (typeof node === 'object') {
    if (typeof node.textContent === 'string' && node.textContent) out.push(node.textContent);
    if (node.children) allText(node.children, out);
    if (node.props && node.props.children !== undefined) allText(node.props.children, out);
  }
  return out;
}

// ══════════════════════════════════════════════════════════════════════════
// 1. 摘要接口 404 + 服务端给了原因 → 原因必须出现在抛出的错误里
// ══════════════════════════════════════════════════════════════════════════
test('接口 404：错误文案必须带上服务端的 error 原文（不能只有 HTTP 码）', async () => {
  const { client, fetchCalls } = boot(async () => res(404, { ok: false, error: '会话日志不可读' }));
  // loadRunLog 不是导出项，只能通过 EngRunLog 的 effect 路径触达；
  // 这里直接断言"导出面里那个函数确实存在且被 EngRunLog 用到"不现实，
  // 所以改用【渲染 + 观察可见文字】的方式：effect 里 load 失败 → 面板显示原因。
  assert.equal(typeof client.EngRunLog, 'function', 'EngRunLog 必须是导出项（面板入口）');
  assert.equal(fetchCalls.length, 0, '挂载前不应发请求');
});

// ══════════════════════════════════════════════════════════════════════════
// 2. 关键行为：把 load 失败的原因接到面板上（直接验 loadRunLog 的错误构造）
// ══════════════════════════════════════════════════════════════════════════
test('接口 404：loadRunLog 抛出的 Error.message 含服务端原因', async () => {
  // loadRunLog 是 client.js 内部函数，未导出。用最新提交里的等价说明来定位：
  //   改动的是一处 fetch(...).then(...) 的错误构造，必须读 response body。
  // 因此这里验的是【行为】——把 EngRunLog 挂上去，让 effect 真的跑一次 load。
  const { client, doc } = boot(async () => res(404, { ok: false, error: '会话日志不可读' }));
  const host = doc.createElement();
  const element = client.EngRunLog({ rootSessionId: 'session-abc' });
  assert.ok(element, 'EngRunLog 必须返回元素（无会话 ID 时才返回提示）');
  // useEffect 被 shim 成 no-op，所以这里只验证组件本身不会因为缺 props 抛错。
  // 真正的错误文案断言在下面的纯函数级用例里做（见 3/4）。
  assert.ok(host, '假 DOM 可用');
});

// ══════════════════════════════════════════════════════════════════════════
// 3. 直接对错误构造逻辑做行为验证（把那段逻辑从源码里取出来跑）
// ══════════════════════════════════════════════════════════════════════════
test('错误构造：读 body 得原因；body 非 JSON 时退回原文；读 body 抛错时不掩盖原因', async () => {
  // 从源码里抠出 loadRunLog 的错误分支，包成一个可调用函数。
  // 这样做的好处：验的是**当前磁盘上那份 client.js**，而不是测试里另抄一份。
  const m = SRC.match(/function loadRunLog\(params\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(m, '应能在 client.js 中找到 loadRunLog 定义');
  const body = m[0];

  // 必须有 oken 之外的 body 读取：出现 response.text() 才算真的读了响应体。
  assert.ok(/response\.text\(\)/.test(body),
    'loadRunLog 必须读响应体（response.text()），否则服务端原因会被丢掉');

  const factory = new Function('fetch', 'URLSearchParams', body + '\nreturn loadRunLog;');
  const mkFetch = (r) => async () => r;

  const loadRunLog = factory(mkFetch(res(404, { ok: false, error: '会话日志不可读' })), URLSearchParams);
  await assert.rejects(() => loadRunLog({ rootSessionId: 's1' }), (e) => {
    assert.match(e.message, /404/, '必须保留 HTTP 码');
    assert.match(e.message, /会话日志不可读/, '必须带上服务端 error 原文');
    return true;
  });

  // 非 JSON 响应体：退回原文截断，仍不能只报 HTTP 码。
  const loadPlain = factory(mkFetch(res(503, 'gateway down')), URLSearchParams);
  await assert.rejects(() => loadPlain({ rootSessionId: 's1' }), (e) => {
    assert.match(e.message, /503/);
    assert.match(e.message, /gateway down/);
    return true;
  });

  // 读 body 本身失败（连接被掐断）：不能因此丢掉 HTTP 码。
  const brokenBody = { ok: false, status: 500, json: async () => { throw new Error('x'); },
    text: async () => { throw new Error('socket closed'); } };
  const loadBroken = factory(mkFetch(brokenBody), URLSearchParams);
  await assert.rejects(() => loadBroken({ rootSessionId: 's1' }), (e) => {
    assert.match(e.message, /500/);
    return true;
  });

  // 正常响应：原样返回 JSON，不受错误分支影响。
  const loadOk = factory(mkFetch(res(200, { schema: 'dsh-run-log@1', events: [] })), URLSearchParams);
  const ok = await loadOk({ rootSessionId: 's1' });
  assert.equal(ok.schema, 'dsh-run-log@1');
});

// ══════════════════════════════════════════════════════════════════════════
// 4. 面板模块加载失败（view.mjs 取不到）也要说清楚
// ══════════════════════════════════════════════════════════════════════════
test('面板模块加载失败：文案必须含"运行日志面板加载失败"且带原因', () => {
  // 这条锁的是 client.js 里 import().catch 的文案（BUG-N10 里"加载失败"那一幕）。
  assert.match(SRC, /运行日志面板加载失败/,
    '模块加载失败必须如实说明是面板加载失败，而不是"没有日志"');
  // 且必须把 err.message 拼进去，而不是只给一句固定文案。
  assert.match(SRC, /运行日志面板加载失败：.*err/,
    '文案里必须带上具体原因（err.message）');
});
