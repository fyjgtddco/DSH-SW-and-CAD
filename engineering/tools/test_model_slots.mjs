/**
 * 模型链 UI 改版 · 契约测试（2026-10-30 第二轮反馈）
 *
 * 用户要求：
 *   1) 默认 3 个槽位，后面给一个 "+" 让他们自己加，【不限制】加多少个
 *   2) "模型自动接替" 与 "设计过程优化" 整块互换位置
 *   3) 去掉 "额度不足自动换模型" 开关
 *   4) 保存/刷新目录 旁边加 "清空"；没填模型 = 用默认（自己选的）模型
 *
 * 重点验证 "+" 是【真生效】的：点击 → 行数+1 → 渲染 → 保存链路完整。
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
const CLIENT = readFileSync(path.join(LIB, 'client.js'), 'utf8');

// ── 1. "+" 真的会加行：用源码里的 addSlot 逻辑做行为验证 ────────────────
function extractFn(name) {
  const re = new RegExp('function ' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(CLIENT);
  if (!m) throw new Error(name + ' 不存在');
  let depth = 0, k = m.index + m[0].length - 1;
  for (; k < CLIENT.length; k++) {
    if (CLIENT[k] === '{') depth++;
    else if (CLIENT[k] === '}') { depth--; if (depth === 0) { k++; break; } }
  }
  return CLIENT.slice(m.index, k);
}

test('P1 "+" 点击后行数 +1（真生效）', () => {
  const addSlotSrc = extractFn('addSlot');
  const S = { models: [{ provider: 'a', model: '1' }, { provider: '', model: '' }, { provider: '', model: '' }] };
  let patched = null;
  const patch = (o) => { patched = o; };
  // 把 addSlot 包成 (S, patch) => 执行，参数名与源码一致
  const run = new Function('S', 'patch', addSlotSrc + '\nreturn addSlot;')(S, patch);
  run();
  assert.ok(patched, 'addSlot 必须调用 patch 触发重渲染');
  assert.equal(patched.models.length, 4, '3 行 → 点一次 + 应变成 4 行');
  assert.deepEqual(patched.models[3], { provider: '', model: '' }, '新增的是空槽');
});

test('P2 连续点 "+" 不设上限（超过 10 个仍可加）', () => {
  const addSlotSrc = extractFn('addSlot');
  const state = { models: [{}, {}, {}] };
  let patched = null;
  const patch = (o) => { patched = o; state.models = o.models; };
  const run = new Function('S', 'patch', addSlotSrc + '\nreturn addSlot;')(state, patch);
  for (let i = 0; i < 12; i++) run();
  assert.ok(patched.models.length > 10, '不应被 10 个上限截断，实际=' + patched.models.length);
});

test('P3 默认 3 个槽位（MIN_SLOTS=3 且 withTen 补齐）', () => {
  assert.ok(CLIENT.includes('var MIN_SLOTS = 3'), '应有 MIN_SLOTS=3');
  const withTen = extractFn('withTen');
  assert.ok(withTen.includes('MIN_SLOTS'), 'withTen 应用 MIN_SLOTS');
  assert.ok(!/slice\(0, 10\)/.test(withTen), '不得再 slice(0,10) 截断');
});

test('P4 保存前裁掉尾部空槽（避免写一堆空行）', () => {
  const trimSrc = extractFn('trimEmpty');
  const fn = new Function(trimSrc + '\nreturn trimEmpty;')();
  const got = fn([{ provider: 'a', model: '1' }, { provider: '', model: '' }, { provider: '', model: '' }]);
  assert.equal(got.length, 1, '尾部空槽应被裁掉');
  const got2 = fn([{ provider: 'a', model: '1' }, { provider: '', model: '' }, { provider: 'b', model: '2' }]);
  assert.equal(got2.length, 3, '中间空槽保留（用户可能稍后填）');
});

test('P5 清空按钮：清空后等于"不兜底"', () => {
  assert.ok(CLIENT.includes('function clearAll'), '应有 clearAll');
  const clr = extractFn('clearAll');
  assert.ok(clr.includes('MIN_SLOTS'), '清空后应回到默认 3 个空槽');
  assert.ok(clr.includes('models: []'), '清空应保存空链 = 不兜底');
});

test('P6 删除按钮与 "+" 配对', () => {
  assert.ok(CLIENT.includes('function removeSlot'), '应有 removeSlot');
  assert.ok(CLIENT.includes("eng-mc-del"), '应有删除按钮样式');
  assert.ok(CLIENT.includes('removeSlot(i)'), '删除按钮应绑定 removeSlot');
  assert.ok(CLIENT.includes('addSlot()'), '添加按钮应绑定 addSlot');
});

test('P7 已去掉"额度不足自动换模型"开关', () => {
  assert.ok(!CLIENT.includes("'额度不足自动换模型'"), '该开关文案应已移除');
});

test('P8 栏目顺序：模型自动接替 在 设计过程优化 之前', () => {
  const iModel = CLIENT.indexOf("title: '模型自动接替'");
  const iDesign = CLIENT.indexOf("title: '设计过程优化'");
  assert.ok(iModel > 0 && iDesign > 0, '两个栏目都应存在');
  assert.ok(iModel < iDesign, '模型自动接替必须排在设计过程优化之前');
});

test('P9 Host：空链 = 不介入（用用户默认模型）', () => {
  assert.ok(HOST.includes('if (!cfg.entries.length) return false;'),
    '空链必须直接返回 false（不接管）');
});

test('P10 Host：不再限制 10 个', () => {
  // 用正则精确匹配 ">= 10"（避免被 ">= 100" 子串误命中）
  assert.ok(!/entries\.length\s*>=\s*10\b(?!0)/.test(HOST),
    'Host 侧不应再有 10 个上限');
  assert.ok(!HOST.includes('limit: 10'), '接口不应再返回 limit:10');
});

console.log('=== add-slot tests loaded ===');
