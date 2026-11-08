#!/usr/bin/env node
/**
 * 【B2 回归测试】LLM 流挂死必须有首包超时，不能靠看门狗杀活着的房间。
 *
 * 对应缺陷（01-缺陷清单与日志定位.md · B2，P0 · ◆）：
 *
 *   现场（2026-11-07 会话 · 总装 #2 0b161dc4）：
 *     :1550  09:51:30  step/start step:175
 *     :1551  10:21:22  assistant/attempt {step:175, stream: []}
 *     → **1791.6 秒，零 chunk、零 retry**
 *     :4079  "30 minutes since last disk write…" → :4080 interrupt_agent
 *
 *   重试策略里明明有 TIMEOUT（:4039），但"连接已建、首包不来"走不到它。
 *
 * 判据：
 *   ① profiles/cordis.patch.yml 的 provider 配置必须有明确的流超时；
 *   ② 超时值必须是有限正数且 <= 120000（否则等于没超时）；
 *   ③ 必须有首包超时（firstByteTimeoutMs）或等效的单值空闲超时；
 *   ④ 首包超时之后要有重试预算（maxRetries>=1），否则只是把挂死换成判死。
 *
 * 用法：node --test test/b2-stream-timeout.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROFILE = path.resolve(HERE, '..', '..', '..', '..', 'profiles', 'cordis.patch.yml');

function readProfile() {
  return fs.readFileSync(PROFILE, 'utf8');
}

/** 极简 YAML 取值：只需要从 llm-pi-ai 段里抠出 deepseek 的几个数字。 */
function pickNum(src, key) {
  const m = src.match(new RegExp('^\\s*' + key + '\\s*:\\s*(\\d+)\\s*$', 'm'));
  return m ? Number(m[1]) : null;
}

test('① profile 里的 provider 配置存在', () => {
  const src = readProfile();
  assert.ok(src.includes('llm-pi-ai'), '必须声明 llm-pi-ai provider 配置');
  assert.ok(src.includes('providers:'), '必须有 providers 段');
});

test('② 流空闲超时是有限正数且不超过 120 秒', () => {
  const src = readProfile();
  const idle = pickNum(src, 'streamIdleTimeoutMs');
  assert.ok(idle !== null, '必须显式声明 streamIdleTimeoutMs（不能靠宿主默认）');
  assert.ok(idle > 0, 'streamIdleTimeoutMs 必须为正数');
  assert.ok(idle <= 120000,
    'streamIdleTimeoutMs 超过 120s 等于没有首包超时（B2 是 1791.6s 挂死）');
});

test('③ 有首包超时（firstByteTimeoutMs 或等效空闲超时）', () => {
  const src = readProfile();
  const first = pickNum(src, 'firstByteTimeoutMs');
  const idle = pickNum(src, 'streamIdleTimeoutMs');
  assert.ok(first !== null || idle !== null,
    '必须能约束"连接已建、首包不来"这种情形');
  if (first !== null) {
    assert.ok(first > 0 && first <= 120000, '首包超时必须在 (0, 120s]');
  }
});

test('④ 首包超时后要有重试预算（否则只是把挂死换成判死）', () => {
  const src = readProfile();
  const retries = pickNum(src, 'maxRetries');
  assert.ok(retries !== null && retries >= 1,
    '必须给出 maxRetries>=1，让超时能回到重试链而不是直接判死');
});

test('⑤ 注释里保留了缺陷现场（防止后来人删掉阈值）', () => {
  const src = readProfile();
  assert.ok(src.includes('B2'), '阈值旁必须标注缺陷编号，便于回溯');
  assert.ok(src.includes('首包'), '必须说明"首包"语义，否则会被误当成慢响应调大');
});
