/**
 * integration.test.mjs — 引擎装配验证（两层）
 *
 * 第 1 层（永远跑）：模块必须能被导入，且默认导出是可实例化的类。
 *   这是「不拖垮 preset」的底线 —— 压缩插件加载失败会让整个 engineering
 *   预设挂掉，而 UI 面板只在 engineering 下渲染，等于界面一起消失。
 *
 * 第 2 层（有宿主依赖才跑）：必须继承真实的 BasicCompactionEngine、
 *   覆盖 summarize()、沿用父类配置校验。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-compact-int-'));
process.env.DSH_STATE_DIR = TMP;

/** 加载本插件；失败时记录原因而不是静默。 */
let Engine = null;
let importError = null;
try {
  const mod = await import('../lib/index.js');
  Engine = mod.default;
} catch (e) {
  importError = String((e && e.message) || e);
}

/** 真实宿主依赖是否可用。 */
let Basic = null;
try {
  const m = await import('@deepseek-ai/dsh-compaction-basic');
  Basic = m.default;
} catch (e) { Basic = null; }

const HAS_HOST = Basic !== null;

test('模块可导入，默认导出是可实例化的类', () => {
  assert.equal(importError, null, '导入失败：' + importError);
  assert.equal(typeof Engine, 'function', '默认导出必须是类/构造函数');
});

test('默认导出能被实例化（不依赖宿主）', () => {
  assert.equal(typeof Engine, 'function');
  let inst;
  assert.doesNotThrow(() => { inst = new Engine(); }, '无宿主依赖时也必须可实例化');
  assert.ok(inst, '实例应存在');
});

test('有宿主依赖时：继承真实父类且覆盖 summarize()', { skip: !HAS_HOST ? '无宿主依赖（在 DSH 内运行才会启用）' : false }, () => {
  assert.ok(Engine.prototype instanceof Basic, '必须继承 BasicCompactionEngine');
  assert.notEqual(Engine.prototype.summarize, Basic.prototype.summarize, 'summarize() 必须被覆盖');
  const body = String(Engine.prototype.summarize);
  assert.ok(/restoreFacts|restored/.test(body), '应含 L0 回填');
  assert.ok(/appendCompaction/.test(body), '应含账本记账');
  assert.ok(/ENG_COMPACTION_NOT_SMALLER|not smaller/.test(body), '应含不缩短保护');
});

test('有宿主依赖时：合法配置可构造（与 preset 三项一致）', { skip: !HAS_HOST ? '无宿主依赖' : false }, () => {
  // 构造需要 cordis 服务上下文；这里只验证 Config schema 能接受 preset 的取值
  const schema = Basic.Config;
  assert.ok(schema, '父类应暴露 Config');
  assert.doesNotThrow(() => schema({ thresholdRatio: 0.5, headroomTokens: 48000, retainRatio: 0.12 }),
    'preset 里的三项配置必须被接受');
});

test('有宿主依赖时：非法配置必须被拒', { skip: !HAS_HOST ? '无宿主依赖' : false }, () => {
  const schema = Basic.Config;
  assert.throws(() => schema({ thresholdRatio: 99 }), /thresholdRatio|max|min/i);
});
