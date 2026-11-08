/**
 * session-view.test.mjs — 「工程模式」页签 + 设置开关的回归
 *
 * 2026-11-08 三个现场缺陷，全部在这里钉死：
 *   ① 页签没有文字（空白页签）—— 根因：label 返回空串并不等于"视图不存在"，
 *      官方 viewTabs() 只按 id 过滤（app.asar 里逐字核对），于是留下一个空页签。
 *      正确做法是【真的注销注册】。
 *   ② 设置开关按不动 / 关掉重开又自己变开 —— 根因：useEngSettings 的订阅回调
 *      只回填了旧的两个字段，新字段被丢弃 → 组件读到 undefined
 *      → "!== false" 恒为真 → 永远显示开。
 *   ③ 页签判定拿不到 sessionId —— 官方 resolveSlotLabel 是「函数就空参调用」，
 *      所以判定必须读模块级快照，不能指望参数。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'client.js'), 'utf8');

test('① 页签 label 不再返回空串（那会留下空白页签）', () => {
  assert.ok(!SRC.includes("return _sessionPresetIsEngineering() ? '工程模式' : ''"),
    'label 不得再按条件返回空串');
  assert.ok(SRC.includes("label: function () { return '工程模式'; }"),
    'label 必须是固定文案，出现/消失由注册与否决定');
});

test('① 页签按状态真正装卸（uninstall 而不是空 label）', () => {
  assert.ok(SRC.includes('function sessionViewWanted()'), '必须有唯一判据函数');
  assert.ok(SRC.includes('var uninstallSessionView = function'), '必须有真注销');
  assert.ok(SRC.includes('if (sessionViewWanted()) installSessionView();'),
    'reinstall 必须按判据装卸');
  assert.ok(SRC.includes('else uninstallSessionView();'), '不想要时必须注销');
});

test('② 设置订阅回调不得漏字段（开关按不动的根因）', () => {
  const idx = SRC.indexOf('return subscribeEngSettings(function () {');
  assert.ok(idx > 0, '必须存在设置订阅');
  const block = SRC.slice(idx, idx + 700);
  // 【2026-11-08】enableOptionCountdown 随倒计时功能一起删除，
  //   这里只校验仍然存在的两个开关字段。该断言的本意是"订阅回填不能漏字段"，
  //   不是"必须有这三个字段"——所以删掉不存在的那个即可，不必放宽检查方式。
  for (const key of ['useOwnSubagentPane', 'showEngConversationView']) {
    assert.ok(block.includes(key), '订阅回填漏了字段 ' + key + ' → 该开关会永远显示默认值');
  }
});

test('③ 预设判定读模块级快照，不依赖 label 参数', () => {
  assert.ok(SRC.includes('function refreshEngMainPreset()'), '必须有快照刷新函数');
  assert.ok(SRC.includes('var _engMainPresetId'), '必须有模块级快照变量');
  const idx = SRC.indexOf('function _sessionPresetIsEngineering(');
  assert.ok(idx > 0);
  const sig = SRC.slice(idx, idx + 80);
  assert.ok(!/function _sessionPresetIsEngineering\(\s*sessionId/.test(sig),
    '不得再声明 sessionId 参数（官方不传，会静默失效）');
});

test('③ 无子代理时页签文字仍在（不得依赖 _isEngineeringContext 作为主判据）', () => {
  const idx = SRC.indexOf('function _sessionPresetIsEngineering(');
  const body = SRC.slice(idx, idx + 400);
  assert.ok(body.indexOf('_engMainPresetId') < body.indexOf('_isEngineeringContext'),
    '必须先读快照，_isEngineeringContext 只能作兜底');
});

test('设置页有「是否启用工程模式的会话视图」且写入正确键位', () => {
  assert.ok(SRC.includes('是否启用工程模式的会话视图'));
  assert.ok(SRC.includes("setEngSetting('showEngConversationView', next)"));
  assert.ok(SRC.includes('showEngConversationView: true'), '默认值必须存在（默认开）');
});
