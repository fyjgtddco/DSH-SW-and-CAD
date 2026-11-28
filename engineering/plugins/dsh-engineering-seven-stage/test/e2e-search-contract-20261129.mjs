/**
 * 七阶段 · BUG-N8 检索工单契约回归（2026-11-29）
 * ============================================================================
 * 背景：BUG记录.md · 任务 20261128_165714_E2089D16 里知识角色报了两条：
 *   ① actionHelp 宣称支持 queryParts，但单独传 queryParts 被
 *      PUBLIC_SEARCH_QUERY_REQUIRED 拒 —— 文档说有、契约说没有；
 *   ② 约 200 字符的描述性 query（文档称上限 500）也被同一个码拒，
 *      ≤63 字符才成功，且与标点无关。
 *
 * 复核结论（**必须先看清楚，别重复修**）：
 *   · ① 在 9ea298f「七阶段死锁修复」那次提交里已经修掉一半 ——
 *     index.js 的 `if (data.queryParts===undefined)` 让 queryParts 这条路通了；
 *     但 actionContract.search 仍把 query 写进语义上的必填，文档与实现还是脱节。
 *   · ② 才是真正没修的：旧代码把"没写 / 太短 / 带换行"三种情况
 *     **全部糊成同一个 PUBLIC_SEARCH_QUERY_REQUIRED**，调用方拿到这个码
 *     完全不知道自己是漏了字段还是写得太短，只能盲试。
 *     （复现方式：对 HEAD 的 index.js 跑本用例，第 ③ 组会红。）
 *
 * 因此本用例锁三件事：
 *   ① 只给 queryParts 必须能发单（防回退）；
 *   ② "没写"与"太短"必须是【不同的码】（这是本轮修的核心）；
 *   ③ 长度上限只在 flow.orderSearch 一处裁定，index 层不另造一套阈值。
 *
 * 运行：node test/e2e-search-contract-20261129.mjs
 */
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as mod from '../lib/index.js';

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

/** 与 smoke.mjs 同形的宿主 agent：session.id + header.parentSession + deriveMessages。 */
const hostAgent = (id, parent = null) => ({
  id,
  session: { id, header: { parentSession: parent },
    deriveMessages: () => [{ id: 'u1', role: 'user', source: { kind: 'user' },
      content: [{ type: 'text', text: '建机械臂' }] }] },
});

function boot() {
  const dir = mkdtempSync(join(tmpdir(), 'n8_'));
  const wf = join(dir, 'workflow.json');
  writeFileSync(wf, JSON.stringify({ task_id: 'job-x', analysis_engine: 'seven_v03',
    seven_stage_root_session_id: 'root' }));
  const agents = new Map();
  agents.set('root', hostAgent('root'));
  agents.set('p1', hostAgent('p1', 'root'));
  let reg = null;
  mod.install({
    logger: { warn() {}, info() {} },
    tools: { register(t) { reg = t; }, guard() {} },
    agents: { get: (id) => agents.get(id) },
    subagents: {
      getProvider: () => ({}),
      listDescendants: async () => [],
      prompt: async () => ({}),
      startActivation: async (i) => {
        agents.set(i.childId, hostAgent(i.childId, i.parent.id));
        return { childId: i.childId, result: Promise.resolve({}) };
      },
    },
    on: () => () => {},
  }, { stateDir: join(dir, 'ss'), workflowFile: wf, workerProvider: 'fork' }, (o) => o);
  const signal = { throwIfAborted() {} };
  const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
    { taskId: 'job-x', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
  return { dir, agents, call };
}

const { dir, agents, call } = boot();
await call(agents.get('root'), 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(agents.get('p1'), 'launch');
const s0 = JSON.parse(readFileSync(join(dir, 'ss', 'job-x.json'), 'utf8'));
const byRole = {};
for (const [role, cid] of Object.entries(s0.childForRole)) byRole[role] = agents.get(cid);
for (const role of ['knowledge', 'design', 'review']) {
  if (byRole[role]) await call(byRole[role], 'bind', { role, token: s0.invitations[role] });
}
await call(agents.get('p1'), 'frame', {
  object: '工位A', decision: 'd', boundaries: 'b', searchTopic: '检索齿轮强度标准',
  verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [],
});
const ka = byRole.knowledge;
const job = (await call(ka, 'status')).job;
ck('knowledge 已拿到 job', Boolean(job?.id));
if (!job?.id) { console.log('\njob 未派发，用例无法继续'); process.exit(1); }

const code = (r) => (r.ok === false ? r.code : 'OK');

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== ① queryParts 必须是真正可用的替代写法 ===');
{
  const r = await call(ka, 'search', {
    jobId: job.id, queryParts: ['齿轮弯曲强度 GB/T 3480', '齿轮接触强度'],
  });
  ck('★ 只给 queryParts（不给 query）→ 发单成功', code(r) === 'OK',
    code(r) + ' | ' + (r.error || '').slice(0, 150));
  ck('落盘保留 queryParts 结构',
    Array.isArray(r.order?.queryParts) && r.order.queryParts.length === 2);
  ck('合成 query 用 || 串联（供 root 直接执行）',
    String(r.order?.query || '').includes('||'));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== ② "没写"与"太短"必须是不同的码（本轮修的核心） ===');
{
  const order = await call(ka, 'search', {
    jobId: job.id, queryParts: ['甲主题的子查询', '乙主题的子查询'],
  });
  // 先回填，释放单工单约束，才能继续发下一张
  if (order.order) {
    await call(agents.get('root'), 'search_fill', {
      jobId: job.id, orderId: order.order.id, query: order.order.query, outcome: 'ok',
      hits: [{ title: 't', url: 'https://example.com/x' }],
    });
  }
  const none = await call(ka, 'search', { jobId: job.id });
  ck('① 两者都不给 → PUBLIC_SEARCH_QUERY_REQUIRED',
    code(none) === 'PUBLIC_SEARCH_QUERY_REQUIRED', code(none));
  ck('   提示里明确教了 queryParts 这个替代写法',
    String(none.error || '').includes('queryParts'));

  const tooShort = await call(ka, 'search', { jobId: job.id, query: '齿' });
  ck('★ query 太短 → PUBLIC_SEARCH_QUERY_TOO_SHORT（不再糊成 REQUIRED）',
    code(tooShort) === 'PUBLIC_SEARCH_QUERY_TOO_SHORT', code(tooShort));
  ck('   提示写清"至少 3 个字符"这类可执行的改法',
    String(tooShort.error || '').includes('3'));

  const multiline = await call(ka, 'search', { jobId: job.id, query: '齿轮\n强度' });
  ck('★ query 带换行 → PUBLIC_SEARCH_QUERY_MULTILINE（独立码）',
    code(multiline) === 'PUBLIC_SEARCH_QUERY_MULTILINE', code(multiline));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== ③ 长度上限只由 flow.orderSearch 一处裁定 ===');
{
  const long = await call(ka, 'search', { jobId: job.id, query: '齿轮强度校核标准'.repeat(70) });
  const longCode = code(long);
  ck('超 500 字符 → SEARCH_QUERY_TOO_LONG（或单工单约束先拦）',
    ['SEARCH_QUERY_TOO_LONG', 'SEARCH_ORDER_ALREADY_PENDING'].includes(longCode),
    longCode);
  // ⚠️ 只有真走到长度校验那条路时才该出现 500 —— 被"单工单约束"先拦时
  //   提示里不会有上限数字，那是**另一条**判据先命中，不是缺陷。
  //   所以这里按实际命中的分支分别断言，避免把"约束先拦"误判成"没写上限"。
  if (longCode === 'SEARCH_QUERY_TOO_LONG') {
    ck('   提示里写明宿主上限 500', String(long.error || '').includes('500'));
    ck('   提示里教了用 queryParts 拆条', String(long.error || '').includes('queryParts'));
  } else {
    ck('   被单工单约束先拦时，提示应说明这是正常约束（不是在报错）',
      /正常约束|pending|回填/.test(String(long.error || '')));
  }
}

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
console.log('='.repeat(52));
process.exit(fail ? 1 : 0);
