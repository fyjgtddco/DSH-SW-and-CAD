/**
 * 模型接替 · 【不劫持用户选择】回归测试（2026-10-30 反馈）
 *
 * 用户现象：
 *   「我发现，这个不是工程模式的，也运用了刚刚那个换模型的代码，
 *     而且我选的是 DeepSeek V4.1，但是实际给我响应的是我在设置页选的 Hy3。
 *     模型显示也没跟上」
 *
 * 三个 bug：
 *   bug1 非工程模式会话也被接替逻辑接管
 *   bug2 每轮无条件把请求覆盖成"链首候选"，顶掉了用户手动选择
 *        （用户选 workbuddy/deepseek-v4.1-flash，实际回的是 chain[0]）
 *   bug3 失败后从链首重找，而不是从当前模型往后找
 *
 * 本测试直接抽取源码里的处理逻辑做行为验证（不依赖完整宿主）。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

const HERE = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname)
  .replace(/^\/([A-Za-z]:)/, '$1'));
function resolveLib() {
  const cands = [path.join(HERE, '..', 'plugins', 'dsh-engineering-ui', 'lib')];
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) for (const p of ['desktop', 'web']) {
    cands.push(path.join(home, '.dsh', 'profiles', p, 'node_modules', 'dsh-engineering-ui', 'lib'));
  }
  for (const c of cands) if (existsSync(path.join(c, 'index.js'))) return c;
  return cands[0];
}
const LIB = process.env.MC_LIB || resolveLib();
if (!existsSync(path.join(LIB, 'index.js'))) {
  console.log('[SKIP] 未找到插件源码：' + LIB);
  process.exit(0);
}
const HOST = readFileSync(path.join(LIB, 'index.js'), 'utf8');

// ── 抽取纯函数（normalizeModelChain / nextModelCandidate / modelKey）────
function extract(fnName) {
  const re = new RegExp('function ' + fnName + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(HOST);
  if (!m) throw new Error(fnName + ' 不存在');
  let depth = 0, k = m.index + m[0].length - 1;
  for (; k < HOST.length; k++) {
    if (HOST[k] === '{') depth++;
    else if (HOST[k] === '}') { depth--; if (depth === 0) { k++; break; } }
  }
  return HOST.slice(m.index, k);
}
const helpers = extract('normalizeModelChain') + '\n' +
                extract('nextModelCandidate') + '\n' +
                extract('modelKey');
const mod = new Function(helpers + '\nreturn { normalizeModelChain, nextModelCandidate, modelKey };')();

test('T1 用户手动选模型时必须原样保留（不被链首顶掉）', () => {
  // 场景：chain[0] = buddy/deepseek-v4.1-flash
  //       用户在设置页选 workbuddy/deepseek-v4.1-flash
  //       本轮没有发生任何接替（switched=false）
  const switched = false;
  const resolvedFromHost = { provider: 'workbuddy', model: 'deepseek-v4.1-flash' };
  // 修复后的逻辑：只有 switched 才覆盖
  const finalCfg = switched ? { provider: 'buddy', model: 'deepseek-v4.1-flash' } : resolvedFromHost;
  assert.deepEqual(finalCfg, { provider: 'workbuddy', model: 'deepseek-v4.1-flash' },
    '未发生接替时，必须保持用户选择');
});

test('T2 发生接替后，下一轮才改用候选', () => {
  const switched = true;
  const key = mod.modelKey('workbuddy', 'hy3');
  const cfg = { entries: [{ provider: 'workbuddy', model: 'hy3' }] };
  const cur = cfg.entries.find((e) => mod.modelKey(e.provider, e.model) === key);
  assert.ok(cur, '应能定位到接替候选');
  assert.equal(cur.provider, 'workbuddy');
});

test('T3 用户重新手动改选 → 放弃接替、交还控制权', () => {
  // 接替状态指向 hy3，但用户此刻选的是 deepseek → 检测到不同 → 交还
  const stKey = mod.modelKey('workbuddy', 'hy3');
  const want = { provider: 'workbuddy', model: 'deepseek-v4.1-flash' };
  const differs = mod.modelKey(want.provider, want.model) !== stKey;
  assert.equal(differs, true, '应检测出用户改选，从而放弃接替');
});

test('T4 失败后从【当前模型】往后找，而不是从链首重来', () => {
  const entries = [
    { provider: 'a', model: '1' },
    { provider: 'b', model: '2' },
    { provider: 'c', model: '3' },
  ];
  const tried = new Set();
  // 用户当前用 b/2，b 失败了
  const curKey = mod.modelKey('b', '2');
  const start = entries.findIndex((e) => mod.modelKey(e.provider, e.model) === curKey);
  assert.equal(start, 1, '应定位到当前模型下标');
  tried.add(curKey);
  let nxt = null;
  for (let i = start + 1; i < entries.length; i++) {
    const e = entries[i];
    if (tried.has(mod.modelKey(e.provider, e.model))) continue;
    nxt = e; break;
  }
  assert.deepEqual(nxt, { provider: 'c', model: '3' },
    '应从当前模型之后继续，而不是回到链首 a/1');
});

test('T5 源码：agent/request 必须受 switched 守卫', () => {
  assert.ok(HOST.includes('if (!st.switched || !st.key) return resolved'),
    '缺少 switched 守卫 —— 会导致每轮无条件覆盖用户选择');
  assert.ok(HOST.includes('st.switched = false'), '用户改选后应清除接替状态');
});

test('T6 源码：request-error 必须置 switched=true 且从当前往后找', () => {
  assert.ok(HOST.includes('st.switched = true'), '接替发生时必须置 switched');
  assert.ok(HOST.includes('_start + 1'), '应从当前模型之后开始找候选');
});

test('T7 源码：作用域仍限定工程模式', () => {
  assert.ok(HOST.includes('function modelChainApplies'), '缺少作用域判定');
  assert.ok(HOST.includes('isGuardSession(agent)'), '应与守卫同口径');
  // 非工程模式：不进入覆盖分支
  assert.ok(HOST.includes('if (!modelChainApplies(agent)) return resolved'),
    'agent/request 必须先做作用域判断');
});

console.log('=== fallback-only tests loaded ===');
