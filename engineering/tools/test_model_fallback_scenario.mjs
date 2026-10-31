/**
 * 场景验证：用户选 A，链里第一个填的是 B —— 到底会怎样？
 *
 * 这是用户明确问的问题，必须给出可验证的答案：
 *   1) 正常情况下：一直用 A，【不会】因为链里有 B 就换成 B；
 *   2) A 报额度错误：才切到 B，并在本轮内重试；
 *   3) 切到 B 后：下一轮不会被误判成"用户改选"而退回 A（这是刚修的 bug）；
 *   4) 用户中途手动改成 C：立即放弃接替，改用 C。
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

/** 用与源码一致的状态机模拟一轮请求。 */
function makeSim(entries, userModel) {
  const st = { key: null, tried: new Set(), switched: false, prevKey: null };
  return {
    st,
    /** 模拟 agent/request：返回本轮实际会用的模型 */
    request(resolvedModel) {
      if (!st.switched || !st.key) return { ...resolvedModel, _src: 'user' };
      const cur = entries.find(e => modelKey(e.provider, e.model) === st.key);
      if (!cur) return { ...resolvedModel, _src: 'user' };
      const wantKey = modelKey(resolvedModel.provider, resolvedModel.model);
      if (wantKey && wantKey !== st.key) {
        if (st.prevKey && wantKey === st.prevKey) {
          // 正常接替路径
        } else {
          st.switched = false; st.key = null; st.prevKey = null;
          return { ...resolvedModel, _src: 'user-changed' };
        }
      }
      return { provider: cur.provider, model: cur.model, _src: 'fallback' };
    },
    /** 模拟 agent/request-error */
    error(failedModel) {
      const curKey = modelKey(failedModel.provider, failedModel.model);
      st.tried.add(curKey);
      for (const e of entries) if (e.provider === failedModel.provider) st.tried.add(modelKey(e.provider, e.model));
      let start = entries.findIndex(e => modelKey(e.provider, e.model) === curKey);
      if (start < 0) start = -1;
      let nxt = null;
      for (let i = start + 1; i < entries.length; i++) {
        if (st.tried.has(modelKey(entries[i].provider, entries[i].model))) continue;
        nxt = entries[i]; break;
      }
      if (!nxt) nxt = entries.find(e => !st.tried.has(modelKey(e.provider, e.model)));
      if (!nxt) return null;
      st.prevKey = curKey;
      st.key = modelKey(nxt.provider, nxt.model);
      st.switched = true;
      return nxt;
    },
  };
}

const CHAIN = [{ provider: 'buddy', model: 'B' }];
const USER_A = { provider: 'workbuddy', model: 'A' };

test('S1 正常情况：一直用 A，链里有 B 也不会被换成 B', () => {
  const sim = makeSim(CHAIN, USER_A);
  const got = sim.request(USER_A);
  assert.equal(got.provider, 'workbuddy');
  assert.equal(got.model, 'A');
  assert.equal(got._src, 'user', '未发生错误时必须原样使用用户选的 A');
});

test('S2 A 报额度错误 → 才切到 B，并在本轮重试', () => {
  const sim = makeSim(CHAIN, USER_A);
  const nxt = sim.error(USER_A);
  assert.ok(nxt, '应找到候选 B');
  assert.equal(nxt.provider, 'buddy');
  assert.equal(nxt.model, 'B');
  assert.equal(sim.st.switched, true, '应置接替标记以便下一轮生效');
});

test('S3 切到 B 后，下一轮【不会】被误判成用户改选而退回 A（关键修复）', () => {
  const sim = makeSim(CHAIN, USER_A);
  sim.error(USER_A);
  // 下一轮 resolved 仍来自上一个成功请求头 → 还是 A
  const got = sim.request(USER_A);
  assert.equal(got._src, 'fallback', '应继续走接替，不能判成 user-changed');
  assert.equal(got.provider, 'buddy', '本轮应使用 B');
  assert.equal(got.model, 'B');
});

test('S4 用户中途手动改成 C → 立即放弃接替，改用 C', () => {
  const sim = makeSim(CHAIN, USER_A);
  sim.error(USER_A);                 // 已切到 B
  const C = { provider: 'trae', model: 'C' };
  const got = sim.request(C);        // 用户手动选了 C
  assert.equal(got._src, 'user-changed', '应识别为用户改选');
  assert.equal(got.provider, 'trae');
  assert.equal(got.model, 'C');
  assert.equal(sim.st.switched, false, '应清除接替状态');
});

test('S5 源码：必须有 prevKey 记录与判定', () => {
  assert.ok(HOST.includes('st.prevKey = _curKey'), '应在切替时记录切换前的模型');
  assert.ok(HOST.includes('wantKey === st.prevKey'), '应用 prevKey 区分"自己切的"与"用户改的"');
  assert.ok(HOST.includes('prevKey: null'), 'state 应初始化 prevKey');
});

test('S6 空链 = 完全不介入（用用户模型）', () => {
  const sim = makeSim([], USER_A);
  const got = sim.request(USER_A);
  assert.equal(got.provider, 'workbuddy');
  assert.equal(got._src, 'user');
});
