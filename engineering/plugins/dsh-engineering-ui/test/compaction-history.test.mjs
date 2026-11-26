/**
 * compaction-history.test.mjs — 【压缩历史路由】端到端（真跑宿主半，不 mock 数据）
 *
 * 用户要求（图一 / 图二）：
 *   · 不同子代理 → 各自的压缩历史；主对话 → 自己的压缩历史；
 *   · 都要能看得到，并跟着真实压缩更新；
 *   · 最上面是最新的，后面的历史不消失、只被刷下去。
 *
 * 这个文件的价值在于：它真的把 lib/index.js 装配起来、真的往磁盘写两份账本、
 * 真的调用 /compaction-history 拿 JSON。换后端最容易出的问题是
 * "面板看起来清空了" —— 那正是这里要钉死的行为。
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;
const SHIM_REGISTRY = `
export function resolveSessionPreset() { return null; }
export const agentPresetProjectionDefinition = { init: () => null, apply: (s) => s };
`;
const url = (s) => 'data:text/javascript;base64,' + Buffer.from(s).toString('base64');

const LIB = new URL('../lib/', import.meta.url);
function inline(src, base = LIB) {
  let out = src;
  // 【子目录 import】原来只认 './x.js'，遇到 './run-log/y.mjs' 会整份漏掉 →
  //   data: URL 无法解析相对路径 → 整个测试文件 ERR_UNSUPPORTED_RESOLVE_REQUEST。
  //   递归基准必须是【该模块自己的目录】，否则 lib/run-log/x.mjs 里的 './y.mjs'
  //   会被当成 lib/y.mjs 而漏内联。
  const re = /from '\.\/((?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:js|mjs))'/g;
  const names = new Set();
  let m;
  while ((m = re.exec(src)) !== null) names.add(m[1]);
  for (const name of names) {
    let body, own;
    try { own = new URL(name, base); body = readFileSync(own, 'utf8'); }
    catch { continue; }
    out = out.split("from './" + name + "'")
      .join("from '" + url(inline(body, new URL('.', own))) + "'");
  }
  return out;
}

const source = inline(readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8'))
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + url(SHIM_LLM) + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + url(SHIM_REGISTRY) + "'");

let apply;
async function loadHost() {
  if (apply) return apply;
  ({ apply } = await import('data:text/javascript;base64,'
    + Buffer.from(source).toString('base64')));
  return apply;
}

/** 极简宿主上下文：只提供路由注册所必需的服务。 */
function makeHost() {
  const routes = new Map();
  const ctx = {
    tools: { guard() { return () => {}; } },
    on() { return () => {}; },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => {}; } },
    sessionPersistence: {
      async open() { throw new Error('no session'); },
      async stat() { return null; },
      locate() { return null; },
    },
    sessions: {},
    agents: { get() { return undefined; }, roots() { return []; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
  };
  return { ctx, routes };
}

async function call(routes, routePath, query = '') {
  const req = Readable.from([]);
  req.url = routePath + query;
  req.method = 'GET';
  let body;
  const res = { statusCode: 200, setHeader() {}, end(text) { body = JSON.parse(text); } };
  await routes.get(routePath)(req, res);
  return body;
}

test('两份账本合并：主子对话分链 / 最新在上 / 旧记录不消失', async () => {
  const boot = await loadHost();
  const temp = mkdtempSync(path.join(tmpdir(), 'enghist2-'));
  const oldHome = process.env.DSH_HOME;
  const oldState = process.env.DSH_STATE_DIR;
  try {
    // 状态目录 = <DSH_HOME>/.agent-presets/engineering/tools（findToolsDir 的探测路径）
    const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
    mkdirSync(tools, { recursive: true });
    // 让宿主真的认得这个目录：写一个工作流状态文件（findToolsDir 判定依据）
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ task_id: 'task-1' }));
    process.env.DSH_HOME = temp;
    process.env.DSH_STATE_DIR = tools;

    // ── 旧后端（v1）留下的历史：主对话 1 条 + 子代理 A 1 条
    writeFileSync(path.join(tools, 'compaction_history.jsonl'),
      JSON.stringify({ at: 1000, target: 'root-1', root: 'root-1', label: '主对话', l0_numbers: 5, summary_excerpt: 'v1 主对话' }) + '\n' +
      JSON.stringify({ at: 2000, target: 'child-A', root: 'root-1', label: '建模小屋-输入轴', l0_numbers: 7, summary_excerpt: 'v1 A' }) + '\n',
      'utf8');
    // ── 新后端（v2）的历史：同一子代理 A 又压一次 + 子代理 B
    writeFileSync(path.join(tools, 'compaction_history_v2.jsonl'),
      JSON.stringify({ schema: 2, at: 3000, target: 'child-A', root: 'root-1', label: '建模小屋-输入轴', l0_numbers: 3, summary_excerpt: 'v2 A' }) + '\n' +
      JSON.stringify({ schema: 2, at: 4000, target: 'child-B', root: 'root-1', label: '建模小屋-中间轴', l0_numbers: 2, summary_excerpt: 'v2 B' }) + '\n',
      'utf8');
    // ── 引擎标记（旧插件写的），验证新面板仍能正确解释
    writeFileSync(path.join(tools, 'compaction_engine.json'), JSON.stringify({ at: 1, mode: 'real', pid: 1 }));

    const { ctx, routes } = makeHost();
    boot(ctx);
    const P = '/dsh-engineering-ui/compaction-history';
    assert.ok(routes.has(P), '路由必须注册');

    const all = await call(routes, P, '?rootSessionId=root-1&limit=400');
    assert.equal(all.ok, true);
    assert.equal(all.entries.length, 4, '两代账本一条都不能少（用户要求旧历史不消失）');

    // ① 最新在上
    assert.deepEqual(all.entries.map((e) => e.at), [4000, 3000, 2000, 1000]);

    // ② 主子对话分链：主对话只拿到自己那条
    const main = await call(routes, P, '?rootSessionId=root-1&target=root-1');
    assert.equal(main.entries.length, 1);
    assert.equal(main.entries[0].target, 'root-1');

    // ③ 同一子代理的跨代历史并成一条链，且链内仍是新在上
    const a = await call(routes, P, '?target=child-A');
    assert.deepEqual(a.entries.map((e) => e.at), [3000, 2000]);
    assert.equal(a.entries[0].summary_excerpt, 'v2 A');

    // ④ 另一个子代理独立成链
    const b = await call(routes, P, '?target=child-B');
    assert.equal(b.entries.length, 1);
    assert.equal(b.entries[0].label, '建模小屋-中间轴');

    // ⑤ counts 徽标按会话聚合
    const cA = all.counts.find((c) => c.target === 'child-A');
    assert.equal(cA.count, 2);

    // ⑥ 两代账本的存在标记要透出来（面板据此解释"新后端还没压过"）
    assert.deepEqual(all.engines, { v1: true, v2: true });

    // ⑦ limit 截断保留最新
    const lim = await call(routes, P, '?limit=2');
    assert.deepEqual(lim.entries.map((e) => e.at), [4000, 3000]);

    // ⑧ 坏行不拖垮整块面板
    writeFileSync(path.join(tools, 'compaction_history_v2.jsonl'),
      '{broken json\n' + JSON.stringify({ schema: 2, at: 5000, target: 'child-B', root: 'root-1', label: 'X' }) + '\n', 'utf8');
    const afterBad = await call(routes, P, '');
    assert.equal(afterBad.ok, true);
    assert.ok(afterBad.entries.some((e) => e.at === 5000), '坏行之后的好行仍要读出来');
  } finally {
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    if (oldState === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = oldState;
    rmSync(temp, { recursive: true, force: true });
  }
});
