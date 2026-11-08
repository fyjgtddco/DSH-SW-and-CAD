#!/usr/bin/env node
/**
 * 【卡死回归】用户点提交后卡片不消失 / 答案丢失（session-55a2009b，2026-11-08）
 *
 * ── 现场（用户原话）──────────────────────────────────────────────────────
 *   "当时是我选择了选择，然后还是停留在选项的那个设置页，就一直卡着动不了了，
 *    然后我就自己关了DSH"
 *
 * ── 三层根因（本文件逐条防守）────────────────────────────────────────────
 *  A. 宿主半：ask-child 建了两个"等待" ——
 *       · settleResolve/settledPromise（插件自建，**没人 await**）
 *       · uq.ask(...)（真正驱动原生选项卡的那个，**没有接 settle 通道**）
 *     /answer 只 resolve 前者 → 原生挂起永不结束 → 界面停在选项页。
 *     判据：entry 必须持有 nativeSettle，/answer 必须调用它。
 *
 *  B. ask_user.py：默认 --seg-max 90 会"每 90 秒退出重来"，
 *     两次调用之间有真实空窗；用户恰在空窗作答 → 读不到 → 判仍在等待。
 *     判据：默认 seg_max 必须为 0（不分段），且读取答案有短重试。
 *
 *  C. 前端：提交成功后没有闭锁，卡片仍可点/仍在倒计时 →
 *     看起来"没反应"；再加一次点击会得到"已作答请勿重复提交"。
 *     判据：必须存在已结案标记，且提交前短路。
 *
 * 用法：node --test test/question-freeze.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', 'lib');
const HOST = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8');
const CLIENT = fs.readFileSync(path.join(LIB, 'client.js'), 'utf8');
const PY = fs.readFileSync(
  path.resolve(HERE, '..', '..', '..', 'tools', 'ask_user.py'), 'utf8');

test('A1 · ask-child 必须把原生 ask 的 settle 接到 entry 上', () => {
  assert.ok(HOST.includes('entry.nativeSettle'),
    '必须给 entry 挂 nativeSettle —— 否则原生选项卡永不关闭（本次卡死直接原因）');
  assert.ok(HOST.includes("let nativeSettle = null"),
    '必须持有原生 Promise 的 resolve');
  assert.ok(HOST.includes('const _nativePromise = uq.ask('),
    '必须接住 uq.ask 返回的 Promise（不能丢弃）');
});

test('A2 · /answer 必须同时结掉原生挂起', () => {
  const i = HOST.indexOf("PREFIX + '/answer'");
  assert.ok(i > 0, '缺少 /answer 端点');
  const seg = HOST.slice(i, i + 6000);
  assert.ok(seg.includes('entry.nativeSettle'),
    '/answer 里必须调用 nativeSettle —— 只 resolve 自建 Promise 等于没结案');
});

test('A3 · 只结案一次（不能双 settle）', () => {
  const i = HOST.indexOf('entry.nativeSettle = ');
  const seg = HOST.slice(i, i + 500);
  assert.ok(seg.includes('try') && seg.includes('catch'),
    '原生 settle 调用必须包 try/catch（双 settle 会让 Promise 抛错）');
});

test('B1 · ask_user.py 默认不再分段（避免答案空窗）', () => {
  assert.ok(/_SEG_MAX = 0/.test(PY),
    '默认 seg_max 必须为 0（不分段）—— 90 秒分段之间的空窗正是丢答案的窗口');
  assert.ok(PY.includes('DSH_ASK_SEG_MAX'),
    '必须保留显式开关供需要的场景使用');
});

test('B2 · 答案读取必须有短重试（应对写读竞态）', () => {
  assert.ok(PY.includes('for _k in range(6)'),
    'pending 消失后必须短重试读 result，不能立刻判过期');
  assert.ok(PY.includes('_confirmed'),
    '重试读到的答案必须被采用');
});

test('C1 · 前端提交成功后必须闭锁，禁止二次提交', () => {
  assert.ok(CLIENT.includes('settledDoneRef'),
    '必须有已结案 ref');
  const i = CLIENT.indexOf("var submit = function (cause)");
  const seg = CLIENT.slice(i, i + 900);
  assert.ok(seg.includes('settledDoneRef.current'),
    'submit 开头必须短路已结案，避免"再点一次报已作答"被当成卡死');
});

test('C2 · 提交成功后要通知父组件清账', () => {
  const i = CLIENT.indexOf("stSt[1]('ok')");
  const seg = CLIENT.slice(i, i + 1400);
  assert.ok(seg.includes('onAnswered'),
    '提交成功必须回调 onAnswered，让待答队列把这张卡清掉');
});
