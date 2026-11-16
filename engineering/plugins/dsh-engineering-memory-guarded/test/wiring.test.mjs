/**
 * wiring.test.mjs — 「工程模式专用 + 历史可看」的静态一致性回归
 *
 * 把用户这次的三条要求变成可重复执行的断言：
 *   ① 只有工程模式能替换压缩后端（非工程模式不受影响）；
 *   ② 两个子代理 / 主对话的压缩历史都要能看到（图一 / 图二黄圈）；
 *   ③ 最新在上，旧记录不消失（只被刷下去）。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// HERE = <repo>/engineering/plugins/dsh-engineering-memory-guarded/test
// 插件根是 HERE/..，产物集根是 <repo>/engineering，仓库根是 <repo>。
const PLUGIN_ROOT = path.resolve(HERE, '..');
const ENGINEERING = path.resolve(PLUGIN_ROOT, '..', '..');
const REPO = path.resolve(ENGINEERING, '..');
const UI_LIB = path.join(ENGINEERING, 'plugins', 'dsh-engineering-ui', 'lib');
const PRESET = path.join(ENGINEERING, 'plugins', 'dsh-engineering-ui', 'preset-engineering.patch.yml');
const ENGINE = fs.readFileSync(path.join(HERE, '..', 'lib', 'guarded.js'), 'utf8');
const LEDGER = fs.readFileSync(path.join(HERE, '..', 'lib', 'ledger.js'), 'utf8');
const SERVER = fs.readFileSync(path.join(UI_LIB, 'index.js'), 'utf8');
const CLIENT = fs.readFileSync(path.join(UI_LIB, 'client.js'), 'utf8');
const YML = fs.readFileSync(PRESET, 'utf8');

test('① preset 的 compaction 子项引用本插件（只在工程模式生效）', () => {
  assert.ok(YML.includes("name: 'dsh-engineering-memory-guarded'"),
    'preset 的 compaction 组必须引用本插件');
  assert.ok(!YML.includes("name: '@deepseek-ai/dsh-compaction-basic'"),
    '同一 compaction 组不能同时挂两个压缩服务（重复注册会被 loader 拒绝）');
  // 上一个后端不能再被引用：一个上下文只能有一个 ctx.compaction
  assert.ok(!YML.includes("name: 'dsh-engineering-compaction'"),
    '旧后端不能再被 preset 引用，否则两套后端争同一个 ctx.compaction');
});

test('① 本包绝不进宿主层（不进 profile bundles，cordis.patch 空插入）', () => {
  const patch = fs.readFileSync(path.join(HERE, '..', 'cordis.patch.yml'), 'utf8');
  const effective = patch.split('\n').filter((l) => !/^\s*#/.test(l)).join('\n').trim();
  assert.equal(effective, '- insert: []',
    '宿主层插入必须为空，否则会与 preset 实例重复注册');
});

test('① 审核插件在 isolate 里（否则 commit 读不到自己的 journal）', () => {
  // isolate 在 compaction group 的头部，位置在 `- id: compaction` 之后、
  // 第一个子项之前。用这段区间判断，比"离 compaction-engineering 多少字符"
  // 稳（后者一改注释长度就会误报 —— 实测踩过）。
  const groupAt = YML.indexOf('- id: compaction\n');
  assert.ok(groupAt > 0, '找不到 compaction group');
  const firstChild = YML.indexOf('- id: compaction-engineering', groupAt);
  assert.ok(firstChild > groupAt, '找不到 compaction-engineering');
  const seg = YML.slice(groupAt, firstChild);
  assert.ok(seg.includes('engineeringCompactionAudit: true'),
    'isolate 必须声明 engineeringCompactionAudit');
  assert.ok(seg.includes('compaction: true'), 'compaction 仍要 isolate');
});

test('② 引擎把提交写进账本，且带 root/label（UI 才能按房间分链）', () => {
  assert.ok(ENGINE.includes('appendLedgerEntry'), '提交成功后必须落账本');
  assert.ok(ENGINE.includes('root: id.root'), '账本要带 root（区分主对话 / 子代理）');
  assert.ok(ENGINE.includes('label: id.label'), '账本要带 label（房间名）');
  // 记账失败不能影响压缩提交
  const i = ENGINE.indexOf('#recordLedger(entry, pin) {');
  const seg = ENGINE.slice(i, i + 300);
  assert.ok(seg.includes('try'), '记账必须 fail-open');
});

test('② UI 同时读两代账本（换后端后旧历史不消失）', () => {
  assert.ok(SERVER.includes("compaction_history_v2.jsonl"), '宿主必须读新账本');
  assert.ok(SERVER.includes("compaction_history.jsonl"), '宿主必须仍读旧账本');
  const i = SERVER.indexOf('function readCompactionHistory');
  const seg = SERVER.slice(i, i + 3000);
  assert.ok(seg.includes('COMPACT_HISTORY_V2_FILE'), '应引用 v2 常量');
  assert.ok(seg.includes('COMPACT_HISTORY_FILE'), '应引用 v1 常量');
});

test('③ 最新在上：宿主侧统一倒序，前端不再自己 reverse', () => {
  const i = SERVER.indexOf('function readCompactionHistory');
  const seg = SERVER.slice(i, i + 3500);
  assert.ok(/\.sort\(\(a, b\) => \(Number\(b\.r\.at\)/.test(seg),
    '宿主必须按 at 倒序（最新在前）');
  assert.ok(!/rows = shown\.slice\(-200\)\.reverse\(\)/.test(CLIENT),
    '前端不应再 reverse（排序口径只留宿主一处）');
  assert.ok(CLIENT.includes('var ordered = shown.slice(0, 200)'), '前端应直接用已排好的顺序');
});

test('③ 旧记录不删：账本只提供 append + read', () => {
  assert.ok(LEDGER.includes('appendLedgerEntry'), '必须有追加');
  assert.ok(LEDGER.includes('readLedger'), '必须有读取');
  assert.ok(!/export function (deleteLedger|removeLedger|updateLedger)/.test(LEDGER),
    '账本模块不得提供删/改函数（用户要求：写了就不能删、不能改）');
  // 引擎侧只调用 append，绝不自己重写整份文件
  assert.ok(!ENGINE.includes('writeFileSync'), '引擎不得整份重写账本');
});

test('③ 空态文案区分"新后端还没压过"与"引擎没加载"', () => {
  assert.ok(CLIENT.includes('engines'), '前端要带 engines 标记');
  assert.ok(CLIENT.includes('dsh-engineering-memory-guarded'),
    '空态文案要指向当前后端，不能还写旧包名');
});

test('尺寸工具只在工程模式注册（工具名固定）', () => {
  assert.ok(ENGINE.includes("const TOOL = 'engineering_memory_request'"));
  assert.ok(ENGINE.includes('ctx.tools.register'), '必须注册只读记忆工具');
  assert.ok(ENGINE.includes('engineering:memory-v2'), '必须注入 systemPrompt 段');
});
