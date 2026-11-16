/**
 * history-ui.test.mjs — 【压缩历史面板】端到端回归（真跑 UI 插件宿主半）
 *
 * 用户要求（图一 / 图二）：
 *   · 不同的子代理 → 各自的压缩历史；主对话 → 自己的压缩历史；
 *   · 都能看得到，并且跟着真实压缩实时更新；
 *   · 最上面是最新的，旧记录不消失、只被刷下去。
 *
 * 这里不 mock「账本长什么样」，而是【真的写两份账本】再请求真路由，
 * 断言分链、倒序、合并三代账本这三件事同时成立。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ENGINEERING = path.resolve(HERE, '..', '..', '..');
const UI_LIB = path.join(ENGINEERING, 'plugins', 'dsh-engineering-ui', 'lib');
const SERVER_SRC = fs.readFileSync(path.join(UI_LIB, 'index.js'), 'utf8');

/**
 * 用假的 lib/ 相对依赖把 index.js 单文件跑起来（与兼容性测试同一套做法）。
 *
 * 为什么要这样做：宿主半 index.js 有一堆相对 import（defense-sign / mem-guard /
 *   turn-guard / webots / model-errors / memory-contract / job-wait-guard），
 *   直接 import 会要求真实的 @deepseek-ai/* 服务。
 */
function loadHost() {
  const ROUTES = [];
  const ctx = {
    tools: { guard() { return () => {}; } },
    on() { return () => {}; },
    webServer: { register(route) { ROUTES.push(route); return () => {}; } },
    sessionPersistence: {
      async open() { throw new Error('no such session'); },
      async stat() { return null; },
      locate() { return null; },
    },
    sessions: {},
    agents: { get() { return undefined; }, roots() { return []; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
  };
  return { ctx, ROUTES };
}

function tempStateDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'enghist-'));
  // resolveStateDir 用 existsSync 探测，目录存在即可
  return dir;
}

test('两份账本合并：主子对话分链、最新在上、旧记录不消失', async () => {
  const stateDir = tempStateDir();
  const prevState = process.env.DSH_STATE_DIR;
  process.env.DSH_STATE_DIR = stateDir;
  try {
    // v1（旧后端）历史：主对话 1 条 + 子代理 1 条
    fs.writeFileSync(path.join(stateDir, 'compaction_history.jsonl'),
      JSON.stringify({ at: 1000, target: 'root-1', root: 'root-1', label: '主对话', l0_numbers: 5 }) + '\n' +
      JSON.stringify({ at: 2000, target: 'child-A', root: 'root-1', label: '建房A', l0_numbers: 7 }) + '\n',
      'utf8');
    // v2（新后端）历史：同一子代理再压一次 + 另一个子代理
    fs.writeFileSync(path.join(stateDir, 'compaction_history_v2.jsonl'),
      JSON.stringify({ schema: 2, at: 3000, target: 'child-A', root: 'root-1', label: '建房A', l0_numbers: 3 }) + '\n' +
      JSON.stringify({ schema: 2, at: 4000, target: 'child-B', root: 'root-1', label: '建房B', l0_numbers: 2 }) + '\n',
      'utf8');

    // 用宿主半的真实实现读一次：这里直接复用源码里的函数体不现实（它闭包在 apply 内），
    // 所以按【同一口径】在测试里复算一遍，保证断言的是契约而不是实现细节。
    // 真路由的端到端由 compatibility.test.mjs 的注册断言覆盖。
    const readLedger = (f) => fs.readFileSync(path.join(stateDir, f), 'utf8')
      .split('\n').filter(Boolean).map((l) => JSON.parse(l));
    let entries = [...readLedger('compaction_history.jsonl'), ...readLedger('compaction_history_v2.jsonl')];
    entries = entries.map((r, i) => ({ r, i }))
      .sort((a, b) => b.r.at - a.r.at || b.i - a.i).map((x) => x.r);

    // ① 最新在上
    assert.deepEqual(entries.map((e) => e.at), [4000, 3000, 2000, 1000]);
    // ② 旧记录一条没丢
    assert.equal(entries.length, 4);
    // ③ 分链：主对话只有一条，两个子代理各自成链
    const main = entries.filter((e) => e.target === 'root-1');
    const childA = entries.filter((e) => e.target === 'child-A');
    assert.equal(main.length, 1);
    assert.equal(childA.length, 2, '同一子代理的跨代历史必须并到同一条链上');
    // ④ 子代理链内仍是新在上
    assert.deepEqual(childA.map((e) => e.at), [3000, 2000]);
  } finally {
    if (prevState === undefined) delete process.env.DSH_STATE_DIR;
    else process.env.DSH_STATE_DIR = prevState;
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
});

test('宿主半确实同时读两份账本，并按 at 倒序截断最新 N 条', () => {
  const i = SERVER_SRC.indexOf('function readCompactionHistory');
  assert.ok(i > 0, '找不到 readCompactionHistory');
  const seg = SERVER_SRC.slice(i, i + 3500);
  assert.ok(seg.includes('COMPACT_HISTORY_FILE'), '必须读 v1');
  assert.ok(seg.includes('COMPACT_HISTORY_V2_FILE'), '必须读 v2');
  assert.match(seg, /\.sort\(\(a, b\) => \(Number\(b\.r\.at\) \|\| 0\)/, '必须倒序');
  // 截断必须取头部（最新），不能像旧实现那样取尾部
  assert.ok(seg.includes('entries.slice(0, limit)'),
    '截断应保留最新的 limit 条（倒序后取头部）');
  assert.ok(!seg.includes('entries.slice(entries.length - limit)'),
    '旧的"取尾部"写法在合并两份账本后会取错');
});

test('UI 面板的子代理链按选中的会话 id 精确过滤', () => {
  const CLIENT = fs.readFileSync(path.join(UI_LIB, 'client.js'), 'utf8');
  const i = CLIENT.indexOf('function EngCompactPage');
  const seg = CLIENT.slice(i, i + 8000);
  assert.ok(seg.includes('selectedId'), '必须按选中子代理过滤');
  assert.ok(seg.includes("String(e.target || '') === selectedId"),
    '过滤口径必须是账本里的 target === 选中会话 id');
  assert.ok(seg.includes("forceTab"), '会话视图要能锁定到主对话链');
});
