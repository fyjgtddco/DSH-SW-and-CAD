#!/usr/bin/env node
/**
 * 【A11 回归测试】守卫必须按角色发文案：不得把"总计子代理"当建模小屋。
 *
 * 对应缺陷（01-缺陷清单与日志定位.md · A11，P1 · ◆）：
 *
 *   现场（2026-11-07 会话 · 总计子代理 d921be74）：
 *     :497 / :517 守卫原文：
 *       "你是【建模/专项子代理】……直接调用工具继续做
 *        （sw_bridge.py run / visual_check.py gate / room-report）"
 *     而它当时已完成 **10/10 阶段**，任务书明写"禁调 sw_bridge"。
 *     结果空转 5 步、53 秒才收工。
 *
 * 判据：
 *   ① 存在 analyst 专用文案 GUARD_TEXT_ANALYST，且明确"不要调 sw_bridge"；
 *   ② 存在 isAnalystAgent()，从 mode2_plan.json 的 analyst.session_id 判定；
 *   ③ steer 时按角色二选一，不再无条件发建模小屋文案；
 *   ④ 取不到身份时回退原文案（不能因为读不到状态就不拦）。
 *
 * 用法：node --test test/a11-analyst-guard.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'index.js'), 'utf8');

test('① 存在 analyst 专用文案，且明确禁止调 sw_bridge', () => {
  assert.ok(SRC.includes('const GUARD_TEXT_ANALYST'), '必须有 analyst 专用文案');
  const start = SRC.indexOf('const GUARD_TEXT_ANALYST');
  const seg = SRC.slice(start, start + 1600);
  assert.ok(seg.includes('总计子代理'), '文案要自报身份');
  assert.ok(seg.includes('不要') && seg.includes('sw_bridge'),
    '必须明确"不要调 sw_bridge"（现场就是被催着调它）');
  assert.ok(seg.includes('mode2_flow'), '必须指向它真正该用的工具');
});

test('② isAnalystAgent 从 mode2_plan.json 判定', () => {
  assert.ok(SRC.includes('function isAnalystAgent('), '必须有角色判定函数');
  const start = SRC.indexOf('function isAnalystAgent(');
  const seg = SRC.slice(start, start + 1200);
  assert.ok(seg.includes('mode2_plan.json'), '必须读模式二编制文件');
  assert.ok(seg.includes('session_id'), '必须比对 analyst-bind 写入的 session_id');
  assert.ok(seg.includes('return false'), '取不到身份要回退');
});

test('③ steer 时按角色二选一', () => {
  assert.ok(SRC.includes('isAnalystAgent(agent)'), 'steer 路径必须调用角色判定');
  assert.ok(SRC.includes('_isAnalyst ? GUARD_TEXT_ANALYST : GUARD_TEXT_SUBAGENT'),
    '必须按角色二选一，不能无条件发建模小屋文案');
});

test('④ 不得出现"无条件发建模小屋文案"的写法', () => {
  // steer 里若直接写死 GUARD_TEXT_SUBAGENT 而没走三元，就是退回了旧行为
  const idx = SRC.indexOf('_isAnalyst ? GUARD_TEXT_ANALYST');
  assert.ok(idx > 0, '三元选择必须存在');
  const around = SRC.slice(Math.max(0, idx - 600), idx + 200);
  assert.ok(around.includes('createUserMessage'), '选择结果要用于构造 steer 消息');
});
