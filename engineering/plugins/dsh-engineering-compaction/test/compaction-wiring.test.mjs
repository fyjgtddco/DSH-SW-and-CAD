/**
 * compaction-wiring.test.mjs — 压缩历史「真的接上了」的回归
 *
 * 2026-11-08 用户要求：「压缩历史去对接 dsh-engineering-compaction，
 *   记得是在工程模式里面触发的」。
 *
 * 三件事一起钉死，缺一个面板就永远是空的：
 *   ① preset 的 compaction 子项必须引用本插件（不是 compaction-basic）；
 *   ② 引擎必须落"已加载"标记，UI 才能区分"引擎没起来"和"还没到阈值"；
 *   ③ 宿主 /compaction-history 必须把该标记透出来，且账本目录与引擎一致。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..', '..', '..');
const ENGINE_SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'index.js'), 'utf8');
const HISTORY_SRC = fs.readFileSync(path.resolve(HERE, '..', 'lib', 'history.js'), 'utf8');
const PRESET = fs.readFileSync(path.resolve(REPO, 'engineering', 'plugins', 'dsh-engineering-ui', 'preset-engineering.patch.yml'), 'utf8');
const HOST = fs.readFileSync(path.resolve(REPO, 'engineering', 'plugins', 'dsh-engineering-ui', 'lib', 'index.js'), 'utf8');
const CLIENT = fs.readFileSync(path.resolve(REPO, 'engineering', 'plugins', 'dsh-engineering-ui', 'lib', 'client.js'), 'utf8');

test('① preset 的 compaction 子项引用本插件', () => {
  assert.ok(PRESET.includes("name: 'dsh-engineering-compaction'"));
  assert.ok(!PRESET.includes("name: '@deepseek-ai/dsh-compaction-basic'"),
    '同一 compaction 组不能挂两个压缩服务');
});

test('② 引擎构造时落"已加载"标记，且失败不影响构造', () => {
  assert.ok(ENGINE_SRC.includes('function markEngineLoaded('), '必须有标记函数');
  assert.ok(ENGINE_SRC.includes('markEngineLoaded(PARSENT_MODE)'), '构造时必须调用');
  const i = ENGINE_SRC.indexOf('markEngineLoaded(PARSENT_MODE)');
  const around = ENGINE_SRC.slice(Math.max(0, i - 400), i + 120);
  assert.ok(around.includes('try'), '标记写入必须 try/catch（诊断信息不能拖垮预设）');
});

test('② 标记文件与账本落在同一个状态目录', () => {
  assert.ok(ENGINE_SRC.includes('compaction_engine.json'), '标记文件名固定');
  assert.ok(ENGINE_SRC.includes('resolveStateDir()'), '必须用 history.js 的同一解析');
  assert.ok(HISTORY_SRC.includes("process.env.DSH_STATE_DIR"), '状态目录优先级与 Python _store 一致');
});

test('③ 宿主把 engine 标记透给面板', () => {
  assert.ok(HOST.includes('function readCompactionEngine()'), '宿主必须读标记');
  assert.ok(HOST.includes('engine: readCompactionEngine()'), '返回体必须带 engine 字段');
  // 账本不存在时也要带（那正是最需要诊断的时刻）
  assert.ok(/exists: false[^}]*engine: readCompactionEngine\(\)/.test(HOST),
    '账本为空时同样要返回 engine，否则无法区分两种空态');
});

test('③ 面板空态必须区分三种原因', () => {
  assert.ok(CLIENT.includes('压缩引擎未加载'), '缺引擎的文案');
  assert.ok(CLIENT.includes('降级模式'), '降级的文案');
  // 【2026-11-08 文案修正】正常空态改成更具体的说法（并带出当前阈值），
  //   原写法「还没有发生压缩：引擎已就绪」会让人误以为"没启动"。
  assert.ok(CLIENT.includes('引擎已就绪') && CLIENT.includes('还没有压缩过'),
    '正常空态的文案');
  // 并且必须把阈值带出来，用户才能判断"是没到阈值"还是"真坏了"
  assert.ok(CLIENT.includes('到阈值才会自动压缩'),
    '正常空态要说明阈值机制');
});

test('④ 前端必须把 engine/threshold 带进 D（否则永远误报未加载）', () => {
  // 现场 bug：dataSt 只回填 entries/counts，engine 被丢弃 →
  //   面板空态永远走"未加载"分支，与磁盘上的 mode:"real" 矛盾。
  assert.ok(CLIENT.includes('engine: j.engine || null'),
    '必须回填 engine');
  assert.ok(CLIENT.includes('threshold: j.threshold || null'),
    '必须回填 threshold');
});
