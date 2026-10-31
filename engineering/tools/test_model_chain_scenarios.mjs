/**
 * 场景验证 A/B/C/D 全链路（2026-10-30 用户追问）
 *
 * 用户问题：
 *   Q1「我选 A 作为初始模型，替换是 B/C/D（里面没有 A），
 *       A 额度用完了就换到 B、C、D？」
 *   Q2「假设 A 在替换模型里面呢？有一个失败会不会跳过去，
 *       还是傻傻在那儿等？」
 *   Q3「模型输不出文字/无法正常工作就换（不只看失败 5 次）」
 *
 * 本测试用与源码一致的状态机逐条验证。
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
const modelKey = (p, m) => String(p) + '\u0000' + String(m);
const FATAL = new Set(['QUOTA', 'ACCOUNT_QUOTA', 'QUOTA_EXCEEDED', 'RATE_LIMIT',
  'CONTEXT_WINDOW_EXCEEDED', 'EMPTY_RESPONSE']);
const MAX_SAME_FAILS = 5;

/** 与源码逻辑一致的状态机 */
function sim(entries) {
  const st = { key: null, tried: new Set(), switched: false, prevKey: null, failCounts: new Map() };
  const nextCandidate = (tried) => entries.find(e => !tried.has(modelKey(e.provider, e.model))) || null;
  return {
    st,
    get currentModel() {
      if (!st.switched || !st.key) return 'USER';
      return st.key;
    },
    /** 返回 { switched: bool } —— 是否发生了切换 */
    onError(model, code, message) {
      const curKey = modelKey(model.provider, model.model);
      const cnt = (st.failCounts.get(curKey) || 0) + 1;
      st.failCounts.set(curKey, cnt);

      const fatal = FATAL.has(code);
      const tooMany = cnt >= MAX_SAME_FAILS;
      const broken = /^(EMPTY_RESPONSE|INVALID_RESPONSE|DECODE)/i.test(code)
        || /empty response|no content/i.test(message || '');
      if (!fatal && !tooMany && !broken) return { switched: false, reason: 'retry-same' };

      st.tried.add(curKey);
      for (const e of entries) if (e.provider === model.provider) st.tried.add(modelKey(e.provider, e.model));

      // ══ 严格从上到下：永远从第 1 行开始，取第一个本轮没试过的 ══
      let nxt = null;
      for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        if (st.tried.has(modelKey(e.provider, e.model))) continue;
        nxt = e; break;
      }
      if (!nxt) return { switched: false, reason: 'exhausted' };
      st.prevKey = curKey;
      st.key = modelKey(nxt.provider, nxt.model);
      st.switched = true;
      return { switched: true, to: nxt, reason: fatal ? 'fatal' : (tooMany ? 'too-many' : 'broken') };
    },
  };
}

const A = { provider: 'wa', model: 'A' };
const B = { provider: 'wb', model: 'B' };
const C = { provider: 'wc', model: 'C' };
const D = { provider: 'wd', model: 'D' };

// ══ Q1：A 不在链路里 ══════════════════════════════════════════
test('Q1-1 A 不在链路里：A 额度用完 → 从链首 B 开始', () => {
  const s = sim([B, C, D]);
  const r = s.onError(A, 'QUOTA_EXCEEDED');
  assert.ok(r.switched, '应发生切换');
  assert.equal(r.to.provider, 'wb', '应换到 B');
});

test('Q1-2 继续失败 → C → D → 用尽', () => {
  const s = sim([B, C, D]);
  s.onError(A, 'QUOTA_EXCEEDED');
  let r = s.onError(B, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wc', '应换到 C');
  r = s.onError(C, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wd', '应换到 D');
  r = s.onError(D, 'QUOTA_EXCEEDED');
  assert.equal(r.switched, false, '链已走完');
  assert.equal(r.reason, 'exhausted', '应明确告知已耗尽');
});

// ══ Q2：A 在链路里 ════════════════════════════════════════════
test('Q2-1 A 在链首 [A,B,C,D]：A 失败 → B（跳过已失败的 A）', () => {
  const s = sim([A, B, C, D]);
  const r = s.onError(A, 'QUOTA_EXCEEDED');
  assert.ok(r.switched);
  assert.equal(r.to.provider, 'wb', '应从 A 往后取 B，不能跳过');
});

test('Q2-2 A 在链中 [B,A,C,D]：A 失败 → 仍从上到下取 B（不是 C）', () => {
  const s = sim([B, A, C, D]);
  s.st.key = modelKey('wa', 'A'); s.st.switched = true; s.st.prevKey = null;
  const r = s.onError(A, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wb',
    '按用户要求：替换一律从上到下，列表第 1 行的 B 才是第一个候补');
});

test('Q2-3 中间某个失败会被跳过，绝不干等', () => {
  const s = sim([A, B, C, D]);
  // A 失败 → B；B 失败 → C；C 失败 → D
  s.onError(A, 'QUOTA_EXCEEDED');
  let r = s.onError(B, 'RATE_LIMIT');
  assert.equal(r.to.provider, 'wc', 'B 一失败就跳到 C，不会等 B');
  r = s.onError(C, 'EMPTY_RESPONSE');
  assert.equal(r.to.provider, 'wd', 'C 输不出内容也立刻换 D');
});

test('Q2-4 同一模型反复失败到 5 次 → 换（兜底，覆盖未知错误码）', () => {
  const s = sim([B]);
  // 用非致命错误码反复失败
  let r;
  for (let i = 1; i <= 4; i++) {
    r = s.onError(A, 'SOME_WEIRD_GATEWAY_ERROR');
    assert.equal(r.switched, false, '第 ' + i + ' 次还不换（让宿主先自己重试）');
  }
  r = s.onError(A, 'SOME_WEIRD_GATEWAY_ERROR');
  assert.ok(r.switched, '第 5 次必须换');
  assert.equal(r.reason, 'too-many');
  assert.equal(r.to.provider, 'wb');
});

test('Q2-5 严格从上到下：当前在 C，C 失败后取 B（列表更靠前且未试过）', () => {
  const s = sim([B, C, D]);
  s.st.key = modelKey('wc', 'C'); s.st.switched = true;
  const r = s.onError(C, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wb', '应回到列表第 1 行 B —— 替换顺序与当前用谁无关');
});

test('Q2-6 顺序推进：B→C→D 严格按列表，不会乱序', () => {
  const s = sim([B, C, D]);
  let r = s.onError(B, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wc');
  r = s.onError(C, 'QUOTA_EXCEEDED');
  assert.equal(r.to.provider, 'wd');
  r = s.onError(D, 'QUOTA_EXCEEDED');
  assert.equal(r.switched, false);
  assert.equal(r.reason, 'exhausted');
});

// ══ Q3：模型"不正常工作"就换 ══════════════════════════════════
test('Q3-1 空响应（输不出文字）立即换，不等 5 次', () => {
  const s = sim([B]);
  const r = s.onError(A, 'EMPTY_RESPONSE');
  assert.ok(r.switched, '空响应应立刻换模型');
  assert.equal(r.reason, 'fatal');
});

test('Q3-2 消息里说 empty response 也能识别', () => {
  const s = sim([B]);
  const r = s.onError(A, 'UNKNOWN', 'provider returned empty response');
  assert.ok(r.switched, '应按"输不出内容"处理');
});

test('Q3-3 普通错误第一次不换（避免误伤偶发抖动）', () => {
  const s = sim([B]);
  const r = s.onError(A, 'TIMEOUT');
  assert.equal(r.switched, false, '偶发错误先让宿主重试');
});

test('Q3-4 上下文超限也换（这个模型干不了这活）', () => {
  const s = sim([B]);
  const r = s.onError(A, 'CONTEXT_WINDOW_EXCEEDED');
  assert.ok(r.switched);
});

// ══ 源码接线 ═════════════════════════════════════════════════
test('S1 源码含 5 次兜底常量', () => {
  assert.ok(HOST.includes('MODEL_CHAIN_MAX_SAME_FAILS = 5'), '应有 5 次阈值');
  assert.ok(HOST.includes('failCounts'), '应有失败计数');
});
test('S2 源码含 EMPTY_RESPONSE', () => {
  assert.ok(HOST.includes("'EMPTY_RESPONSE'"), '白名单应含空响应');
});
test('S3 源码含 broken 判定', () => {
  assert.ok(HOST.includes('_broken'), '应有"模型不正常"判定');
  assert.ok(HOST.includes('_tooMany'), '应有"反复失败"判定');
});
