/**
 * ui-wiring.test.mjs — UI 接线的一致性回归（静态扫描，不启宿主）
 *
 * 目的：把「用户这次要的三件事」变成可重复执行的断言，防止以后改 UI 时
 * 悄悄退回去：
 *   ① 零件表从右侧详情区搬到左列下半区；
 *   ② 左列下半区同时有零件表与压缩历史；
 *   ③ 压缩历史受设置页开关控制，且有可滚动容器。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const UI_LIB = path.resolve(HERE, '..', '..', 'dsh-engineering-ui', 'lib');
const CLIENT = path.join(UI_LIB, 'client.js');
const SERVER = path.join(UI_LIB, 'index.js');

const client = fs.readFileSync(CLIENT, 'utf8');
const server = fs.readFileSync(SERVER, 'utf8');

test('左列下半区容器 eng-lower 只挂零件表（压缩历史已搬到右列）', () => {
  // ── 【布局 v12 更新】此断言原写于"左列下半区同时放零件表 + 压缩历史"那版。
  //   现行设计（client.js 注释原文）："压缩历史移到右边那个绿色的框 →
  //   已搬到右列详情区（.eng-dtabs）"，并明写
  //   「这里【只】放零件表；压缩历史在右列，绝不能再挂回来，
  //     否则同一个组件会出现两份（左窄+右宽），用户看到的还是"乱"」。
  //   所以"eng-lower 里必须有 EngCompactPanel"这条断言与现行设计直接矛盾，
  //   保留它只会逼着人把两份面板挂回去。改为断言正确的形态。
  const i = client.indexOf("className: 'eng-lower'");
  assert.ok(i > 0, '缺少 eng-lower 容器');
  const seg = client.slice(i, i + 400);
  assert.ok(seg.includes('EngPartsBoard'), 'eng-lower 里应挂零件表');
  assert.ok(!seg.includes('EngCompactPanel'),
    'eng-lower 里不该再挂压缩历史（v12 已搬到右列，挂回来会出现两份）');
  // 压缩历史确实存在于右列详情区
  assert.ok(client.includes('EngCompactPage'), '右列应有压缩历史整页组件');
});

test('零件表已从右侧详情区移除（不再出现在 eng-detail 内）', () => {
  // 详情区以 eng-detail 开始、以 eng-log 结束
  const a = client.indexOf("h('div', { className: 'eng-detail' }");
  assert.ok(a > 0, '找不到 eng-detail');
  const b = client.indexOf("className: 'eng-log'", a);
  assert.ok(b > a, '找不到 eng-log');
  const detail = client.slice(a, b);
  assert.ok(!detail.includes('EngPartsBoard'), '零件表不该再渲染在详情区');
});

test('压缩历史面板受「使用工程模式自带的子代理显示页」开关控制', () => {
  // 【布局 v12 更新】组件已更名为 EngCompactPage（旧名 EngCompactPanel 保留别名）
  const i = client.indexOf('function EngCompactPage');
  assert.ok(i > 0, '缺少 EngCompactPage');
  const seg = client.slice(i, i + 6000);
  assert.ok(seg.includes('useOwnSubagentPane'), '必须读设置页同一字段');
  // v12 实现把门禁写成 `var enabled = !!(settings && settings.useOwnSubagentPane)`
  // 并在取数 effect 与渲染分支里都用它，不一定长成 `if (!enabled) return null`。
  assert.ok(/enabled/.test(seg), '开关必须有实际使用点');
});

test('压缩历史面板：子代理 / 主对话 两个分区', () => {
  // 【布局 v12 更新】EngCompactPage
  const i = client.indexOf('function EngCompactPage');
  const seg = client.slice(i, i + 6000);
  // v12：tab 用 'agent' / 'main' 表示两个视图，文案在别处渲染
  assert.ok(seg.includes("'agent'") && seg.includes("'main'"),
    '应有子代理 / 主对话 两个视图');
  assert.ok(seg.includes('rootSessionId'), '应能按 root 过滤');
});

test('压缩历史面板：有可滚动容器与自适应字号', () => {
  assert.ok(/\.eng-cp\{[^}]*overflow:hidden/.test(client), '.eng-cp 应锁死 overflow');
  assert.ok(/\.eng-cp-list\{[^}]*overflow-y:auto/.test(client), '.eng-cp-list 应可纵向滚动');
  assert.ok(/\.eng-cp-row\{[^}]*font-size:clamp\(/.test(client), '字号应自适应');
});

test('子代理列表改为可滚动且不挤走下半区', () => {
  // v12：子代理列表与零件表各自 flex:1 1 50%（各占左列一半）
  assert.ok(/\.eng-tree-list\{[^}]*flex:1 1 50%/.test(client), '列表应吃掉应得的高度');
  assert.ok(/\.eng-tree-list\{[^}]*overflow-y:auto/.test(client), '列表应可滚动');
});

test('服务端提供 /compaction-history 只读路由', () => {
  assert.ok(server.includes("PREFIX + '/compaction-history'"), '缺少压缩历史路由');
  assert.ok(server.includes('compaction_history.jsonl'), '应读 append-only 账本');
  assert.ok(server.includes('compactionStateDir'), '应复用状态目录解析');
});

test('服务端路由对坏行容错（不让一行损坏拖垮面板）', () => {
  const i = server.indexOf('function readCompactionHistory');
  assert.ok(i > 0);
  const seg = server.slice(i, i + 2200);
  assert.ok(seg.includes('parse'), '应有 JSON.parse');
  assert.ok(/catch \(e\) \{ \/\* 坏行跳过 \*\/ \}/.test(seg), '坏行应被跳过');
});

test('preset 【已启用】本插件，且只挂在 engineering 的 compaction 组里', () => {
  // ── 现状（2026-11-08 用户要求）────────────────────────────────────────
  // 「压缩历史的插件是在工程模式下面的，只在工程模式里面触发，
  //   不要跑到其他模式里面触发」。
  //   实现方式：只把它写进【本 preset 的 compaction 子项】。
  //   · preset 子项 = 只在 engineering 会话上下文里实例化 → 天然不外溢；
  //   · 标准/极简等预设用的是它们自己的 compaction-basic，互不相干；
  //   · 绝不能挂到宿主层（cordis.patch.yml / profile bundles）—— 一个上下文
  //     只能有一个 ctx.compaction，重复注册 loader 会直接拒绝。
  //
  // ── 2026-11-07 回退的原因与现在的解除条件 ─────────────────────────────
  //   当时失败的真实原因是「包根本没被安装进 profile 的 node_modules」，
  //   而不是继承来的父类解析不到（宿主自己的 @deepseek-ai/* 在 asar 里，
  //   插件内部的裸 import 由宿主解析链负责，UI 插件本身就靠这条链）。
  //   现在 sync-to-dsh.ps1 / install-0.2.0.ps1 都会把这个包一起铺进
  //   profile 的 node_modules，并写进 dependencies（但【不】进 bundles）。
  const p = path.resolve(HERE, '..', '..', 'dsh-engineering-ui', 'preset-engineering.patch.yml');
  const y = fs.readFileSync(p, 'utf8');
  assert.ok(y.includes("name: 'dsh-engineering-compaction'"),
    'preset 的 compaction 组应引用本插件');
  assert.ok(!y.includes("name: '@deepseek-ai/dsh-compaction-basic'"),
    '同一个 compaction 组里不能同时装两个压缩服务（重复注册会被拒）');

  // 只出现在 preset，不得【插入】到宿主层组合里。
  //   注意：本包自己的 cordis.patch.yml 里出现包名是正常的（那是注释在解释
  //   为什么不 insert）；真正要守的是"插入列表必须为空"。
  const host = fs.readFileSync(path.resolve(HERE, '..', 'cordis.patch.yml'), 'utf8');
  const effective = host.split('\n').filter((l) => !/^\s*#/.test(l)).join('\n').trim();
  assert.equal(effective, '- insert: []',
    '本包的 cordis.patch.yml 必须保持空插入，否则会和 preset 实例重复注册 ctx.compaction');
});
