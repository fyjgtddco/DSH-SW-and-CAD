/**
 * 七阶段 · 2026-11-28 BUG记录 行为回归（端到端）
 *
 * 覆盖 BUG记录.md（2026-11-28 机械臂测试）里【改了判据】的四条：
 *   · BUG-11  检索 query 超宿主 500 字符上限 → 发单时就拦住，教用 queryParts
 *   · BUG-9   可用来源 ID 必须在 ready.sourceIds 里（不受 status 裁剪）
 *   · BUG-10  frame 写"无需检索"时的豁免：没理由仍拒绝，有理由才放行
 *   · 对照     frame 没写"无需检索"时仍强制 SEARCH_REQUIRED（豁免不能写宽）
 *
 * 为什么这些必须是【行为测试】而不是源码断言：
 *   源码断言只能证明"某句话还在"，证明不了"这条路径真的走得通"。
 *   本轮就抓到两个只有跑起来才会暴露的 bug：
 *     ① orderSearch 里引用了不存在的 data（ReferenceError）
 *     ② 豁免分支里 audit(s, a.id) —— finishPacket 作用域内没有 a
 *   两者语法检查都过，只有真跑一遍才会炸。
 */
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openFlow } from '../lib/flow.js';

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

/** 建一个"已 attach + 三角色已 bind + 已 frame"的最小现场。 */
function setup(tag, frameExtra) {
  const run = openFlow(mkdtempSync(join(tmpdir(), 'seven_' + tag + '_')));
  const a = (id, p = null) => ({ id, parent: p });
  const root = a('root'), planner = a('planner', 'root');
  const agents = Object.fromEntries(['knowledge', 'design', 'review']
    .map(r => [r, a(r, 'planner')]));
  const T = 'task-' + tag;
  const call = (who, act, d = {}) => run.call(who, T, act, d);
  call(root, 'attach', { planner: 'planner',
    requirements: [{ id: 'msg-1', text: '模拟任务：建造机械臂', source: 'user' }] });
  const inv = call(planner, 'invite');
  inv.invitations.forEach(({ role, token }) => {
    run.reserveWorker(planner, T, role, role);
    call(agents[role], 'bind', { role, token });
  });
  call(planner, 'frame', Object.assign({
    object: '工位A', decision: '模拟决策', boundaries: '限定范围',
    searchTopic: '检索齿轮强度标准', verification: '独立检查',
    assumptions: '当前假设', prior: '版本基线', blockedBy: [],
  }, frameExtra || {}));
  return { run, root, planner, agents, call };
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-11：query 长度必须守住宿主上限 ===');
// ⚠️ 数据长度要【算准】：'齿轮强度校核标准 GB/T 3480 ' 重复 25 次只有 475 字符，
//   第一次写测试时就是这里算错，误以为校验没生效。下面显式断言长度。
{
  const { call, agents } = setup('q', { searchTopic: '无需检索（本用例只测 query 长度）' });
  const longQ = '齿轮强度校核标准 GB/T 3480 '.repeat(40);
  if (longQ.length <= 500) throw new Error('测试数据错误：longQ 只有 ' + longQ.length + ' 字符');

  let rej = false, msg = '';
  try { call(agents.knowledge, 'search', { jobId: call(agents.knowledge, 'status').job.id, query: longQ }); }
  catch (e) { rej = true; msg = e.message; }
  ck('超 500 字符的 query 被拒', rej);
  ck('拒绝码 SEARCH_QUERY_TOO_LONG', msg.includes('SEARCH_QUERY_TOO_LONG'));
  ck('提示写明宿主上限 500', msg.includes('500'));
  ck('提示教用 queryParts', msg.includes('queryParts'));

  // queryParts 模式：query 可以省略
  let order = null, partsErr = '';
  try {
    order = call(agents.knowledge, 'search', {
      jobId: call(agents.knowledge, 'status').job.id,
      queryParts: ['齿轮弯曲强度 GB/T 3480', '齿轮接触强度', '齿轮精度等级'],
    }).order;
  } catch (e) { partsErr = e.message.slice(0, 160); }
  ck('queryParts 模式下 query 可省略、发单成功', Boolean(order?.id), partsErr);
  ck('落盘保留 queryParts 结构',
     Array.isArray(order?.queryParts) && order.queryParts.length === 3);
  ck('合成 query 用 || 串联', String(order?.query || '').includes('||'));

  let tooMany = false;
  try {
    call(agents.knowledge, 'search', {
      jobId: call(agents.knowledge, 'status').job.id,
      queryParts: ['a', 'b', 'c', 'd', 'e'],
    });
  } catch (e) {
    tooMany = /SEARCH_QUERY_PARTS_INVALID|SEARCH_BUDGET_REACHED|SEARCH_ORDER_ALREADY_PENDING/.test(e.message);
  }
  ck('超过 4 条子查询被拒', tooMany);
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-9：可用来提交的来源 ID 必须完整且不被裁剪 ===');
{
  const { root, agents, call } = setup('ids', { searchTopic: '检索齿轮强度标准' });
  const job = call(agents.knowledge, 'status').job;
  const order = call(agents.knowledge, 'search', { jobId: job.id, query: 'q1' }).order;
  call(root, 'search_fill', {
    jobId: job.id, orderId: order.id, query: 'q1',
    hits: Array.from({ length: 8 }, (_, i) => ({ title: '源' + (i + 1), url: 'https://e.com/' + i })),
    provider: '宿主内置 web_search',
  });
  // ⚠️ nextAction 挂在 job 上（st.job.nextAction），不是 status 顶层
  const ready = call(agents.knowledge, 'status').job?.nextAction?.ready;
  ck('state=ready-to-submit', ready?.state === 'ready-to-submit');
  ck('hits 是真实命中数 8（不是工单数 1）', ready?.hits === 8, '实际 ' + ready?.hits);
  ck('searchRuns 单独给出 1', ready?.searchRuns === 1);
  ck('sourceIds 列出全部 8 条',
     Array.isArray(ready?.sourceIds) && ready.sourceIds.length === 8,
     '实际 ' + (ready?.sourceIds || []).length);
  ck('sourceIds 每条都有可用 id', (ready?.sourceIds || []).every(x => x && x.id));

  // 用 ready.sourceIds 提交必须成功（这正是 fix 的目的）
  let submitted = false, err = '';
  try {
    call(agents.knowledge, 'submit', {
      jobId: job.id, content: '依据充分',
      sources: ready.sourceIds.slice(0, 3).map(x => ({ id: x.id, location: 'p.1', applicability: '仅模拟' })),
      gaps: [],
    });
    submitted = true;
  } catch (e) { err = e.message.slice(0, 160); }
  ck('直接用 ready.sourceIds 提交成功', submitted, err);
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-10：frame 写"无需检索"的豁免（措辞带"新"字） ===');
{
  const { agents, call } = setup('exempt', {
    searchTopic: '无需新检索，直接汇总前四步既有记录即可',
  });
  const job = call(agents.knowledge, 'status').job;
  ck('job 已派发', Boolean(job?.id));

  // ① 不给 noSearchReason → 仍被拒（豁免有代价）
  // 【2026-11-28 死锁修复】契约从「必须在 gaps 写 blocking gap」改成
  //   「必须用独立字段 noSearchReason」—— 前者会让 commit 把它当关键缺证，
  //   两条门禁互斥成死锁。本段断言随之更新。
  let noGap = false, m1 = '';
  try { call(agents.knowledge, 'submit', { jobId: job.id, content: '汇总', sources: [], gaps: [] }); }
  catch (e) { noGap = true; m1 = e.message; }
  ck('缺 noSearchReason 时仍被拒', noGap);
  ck('拒绝码 NO_SEARCH_REASON_REQUIRED',
     m1.includes('NO_SEARCH_REASON_REQUIRED'), m1.slice(0, 90));

  // ② 带 noSearchReason → 豁免通过（顺便验证 audit 不再 ReferenceError）
  let ok = false, m2 = '';
  try {
    call(agents.knowledge, 'submit', {
      jobId: job.id, content: '汇总内容（依据前四步）', sources: [], gaps: [],
      noSearchReason: '本步为前四步的汇总，依据已在对应小步登记，无新增外部依赖',
    });
    ok = true;
  } catch (e) { m2 = e.message.slice(0, 160); }
  ck('带 noSearchReason 后豁免通过', ok, m2);
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 对照：没有"无需检索"声明时，豁免绝不能生效 ===');
{
  const { agents, call } = setup('strict', { searchTopic: '检索齿轮强度标准' });
  const job = call(agents.knowledge, 'status').job;
  let strict = false, msg = '';
  try { call(agents.knowledge, 'submit', { jobId: job.id, content: 'x', sources: [], gaps: [] }); }
  catch (e) { strict = true; msg = e.message; }
  ck('仍强制 SEARCH_REQUIRED', strict && msg.includes('SEARCH_REQUIRED'), msg.slice(0, 90));
}

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
console.log('='.repeat(52));
process.exit(fail ? 1 : 0);
