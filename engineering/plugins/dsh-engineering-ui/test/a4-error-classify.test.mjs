#!/usr/bin/env node
/**
 * 【A4 回归测试】限流被判死：错误码归一 + 退避读 retry-after。
 *
 * 对应缺陷（01-缺陷清单与日志定位.md · A4，P0 · ◆）：
 *
 *   现场（2026-11-07 会话 · 总计子代理 #1 fc370db5）：
 *     :164  …[RATE_LIMIT…],500,10000,0.1],retry:1,maxRetries:5,delayMs:491.59
 *           ← 重试预算 maxRetries:5，实际只用掉 **1 次**
 *     :166  同一原因改报 QUOTA_EXCEEDED（当时不在白名单）→ 判死
 *     :167  已切到 trae/mimo-v2.6-flash；:169 仍 turn/end error
 *     :211  给用户的通知里没有错误码
 *
 * 判据：
 *   ① classifyModelError 把 429 / RATE_LIMIT / QUOTA_EXCEEDED / 402 / 401 / 413
 *      以及中英文限流措辞统一归类，且 switchNow=true；
 *   ② 不认识的普通错误不得被误判成"该换模型"（switchNow=false）；
 *   ③ backoffMsOf 优先用 retry-after，其次指数退避，且上限 30s；
 *   ④ index.js 的接替路径必须真的调用这两个函数（不是只定义不接线）。
 *
 * 用法：node --test test/a4-error-classify.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
// 纯函数模块：不依赖 cordis 宿主，可直接单测（lib/index.js 是 host 半，import 即失败）
import { classifyModelError, backoffMsOf } from '../lib/model-errors.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'index.js'), 'utf8');

test('① 限流/额度类错误必须统一归类并立刻换模型', () => {
  const cases = [
    ['RATE_LIMIT', ''],
    ['429', ''],
    ['QUOTA_EXCEEDED', ''],
    ['402', ''],
    ['AUTH', ''],
  ];
  for (const pair of cases) {
    const r = classifyModelError(pair[0], pair[1]);
    assert.equal(r.switchNow, true, pair[0] + ' 必须触发换模型');
    assert.notEqual(r.class, 'other', pair[0] + ' 必须被归到具体类别');
  }
});

test('①-b 只有消息、没有码也要能判出限流/额度', () => {
  assert.equal(classifyModelError('', 'rate limit exceeded, retry later').switchNow, true);
  assert.equal(classifyModelError('', 'Too Many Requests').switchNow, true);
  assert.equal(classifyModelError('', '当前账号请求过于频繁').switchNow, true);
  assert.equal(classifyModelError('', '额度已用尽').switchNow, true);
  assert.equal(classifyModelError('', 'Insufficient quota').switchNow, true);
});

test('② 普通错误不得被误判成"该换模型"', () => {
  const r = classifyModelError('SOME_RANDOM_ERROR', 'tool crashed');
  assert.equal(r.switchNow, false, '普通错误交回宿主重试，不换模型');
  assert.equal(classifyModelError('', '').switchNow, false);
});

test('③ 退避优先读 retry-after，且不超过 30s', () => {
  const withHeader = backoffMsOf({ headers: { 'retry-after': '12' } }, 1);
  assert.ok(withHeader >= 12000 && withHeader <= 12001,
    'retry-after:12 秒必须被采用，实际 ' + withHeader);

  const withMs = backoffMsOf({ retryAfterMs: 3500 }, 1);
  assert.ok(withMs >= 3500 && withMs <= 3501, 'retryAfterMs 必须被采用');

  const huge = backoffMsOf({ retryAfterMs: 999999 }, 1);
  assert.ok(huge <= 30000, '上限必须是 30s，实际 ' + huge);

  const exp1 = backoffMsOf({}, 1);
  const exp3 = backoffMsOf({}, 3);
  assert.ok(exp1 >= 1000, '第 1 次退避至少 1s，实际 ' + exp1);
  assert.ok(exp3 > exp1, '退避必须随次数增长（指数），实际 ' + exp1 + '->' + exp3);
});

test('④ 接替路径必须真的调用这两个函数（不是只定义）', () => {
  assert.ok(SRC.includes('classifyModelError(code, (failure && failure.message)'),
    '错误码归一必须接进 agent/request-error');
  assert.ok(SRC.includes('backoffMsOf(failure,'),
    '退避必须接进接替路径');
  assert.ok(!/const delay = 800;/.test(SRC),
    '写死的 800ms 必须被移除（A4 现场是 491.59ms 立刻重撞）');
});

test('⑤ 日志必须能看出归类结果（现场 :211 无错误码）', () => {
  assert.ok(SRC.includes('_cls.class'),
    '日志/通知必须带分类结果');
});
