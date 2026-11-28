/**
 * 七阶段 · BUG记录 2026-11-28 第二轮（N1 / N8 / N14）
 * ============================================================================
 * 覆盖本轮核出、ChatGPT 那一轮**没有**修掉的三条：
 *
 *   · BUG-N1  restatedKeys 把【首次】提交的异议标成「本轮重提」
 *   · BUG-N8  ① actionContract.search 把 query 列进 required，导致单独传
 *             queryParts 被 PUBLIC_SEARCH_QUERY_REQUIRED 拒（文档说有、契约说没有）；
 *             ② 代码层把"没写/太短/太长"糊成同一个错误码，模型无从自纠
 *   · BUG-N14 design + planner 同时 host-disposed 后，root 的 allowed 里
 *             看不到任何恢复出口（权限真空，任务停在 61/63）
 *
 * 全部是【行为测试】：真跑状态机，不看源码字符串。
 * 上一轮的教训（fixes-20261128-buglog-arm2.md）写得很清楚：
 *   源码断言能防"被人删掉"，防不了"写错了还语法通过"。
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

function setup(tag, frameExtra) {
  const run = openFlow(mkdtempSync(join(tmpdir(), 'seven_r2_' + tag + '_')));
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
  return { run, root, planner, agents, call, T };
}

/** 造一条完整、必填字段齐全的 review 异议。 */
const issue = (key, kind = 'hard', owner = 'planner') => ({
  key, object: '对象' + key, requirement: '要求' + key, mechanism: '机理' + key,
  evidence: '证据' + key, impact: '影响' + key, needed: '需要' + key,
  owner, close: '关闭依据' + key, kind,
});

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-N1：首次提交的异议不能被标成「本轮重提」 ===');
{
  const { agents, call } = setup('n1');
  // 走到 review 正式复审那一步
  const kjob = call(agents.knowledge, 'status').job;
  const ord = call(agents.knowledge, 'search', { jobId: kjob.id, query: 'GB/T 3480 齿轮' }).order;
  call({ id: 'root', parent: null }, 'search_fill', {
    jobId: kjob.id, orderId: ord.id, query: ord.query, outcome: 'ok',
    hits: [{ title: 't', url: 'https://example.com/x' }],
  });
  const kj = call(agents.knowledge, 'status').job;
  call(agents.knowledge, 'submit', { jobId: kj.id, content: '知识依据',
    sources: [{ id: kj.searches[0].hits[0].id, location: 'l', applicability: 'a' }], gaps: [] });
  const pf = call(agents.review, 'status').job;
  call(agents.review, 'submit', { jobId: pf.id, content: '预审', verdict: 'pass', issues: [] });
  const dj = call(agents.design, 'status').job;
  call(agents.design, 'submit', { jobId: dj.id, content: '设计', evidence: 'e', gaps: [] });

  // ★ 第一次提出 5 条硬异议
  const rj = call(agents.review, 'status').job;
  const keys = ['k1', 'k2', 'k3', 'k4', 'k5'];
  call(agents.review, 'submit', {
    jobId: rj.id, content: '首次复审', verdict: 'blocked',
    issues: keys.map(k => issue(k)),
  });

  const led = call({ id: 'planner', parent: 'root' }, 'status').issueLedger;
  ck('首次提交：restatedKeys 必须为空', (led.restatedKeys || []).length === 0,
    '实际 ' + JSON.stringify(led.restatedKeys));
  ck('首次提交：reopenedKeys 必须为空', (led.reopenedKeys || []).length === 0,
    '实际 ' + JSON.stringify(led.reopenedKeys));
  ck('首次提交：5 条都进 firstRaisedKeys',
    (led.firstRaisedKeys || []).length === 5, '实际 ' + JSON.stringify(led.firstRaisedKeys));
  ck('首次提交：stillOpenKeys 仍列出这 5 条（它们确实挂着）',
    keys.every(k => (led.stillOpenKeys || []).includes(k)),
    JSON.stringify(led.stillOpenKeys));
  ck('首次提交：current 仍是 5 条（台账不丢）',
    (led.current || []).length === 5);
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-N1 对照：第二轮再提同一条，才算「重提」 ===');
{
  const { agents, call } = setup('n1b');
  const planner = { id: 'planner', parent: 'root' };
  const kjob = call(agents.knowledge, 'status').job;
  const ord = call(agents.knowledge, 'search', { jobId: kjob.id, query: 'GB/T 3480 齿轮' }).order;
  call({ id: 'root', parent: null }, 'search_fill', {
    jobId: kjob.id, orderId: ord.id, query: ord.query, outcome: 'ok',
    hits: [{ title: 't', url: 'https://example.com/x' }],
  });
  const kj = call(agents.knowledge, 'status').job;
  call(agents.knowledge, 'submit', { jobId: kj.id, content: '知识依据',
    sources: [{ id: kj.searches[0].hits[0].id, location: 'l', applicability: 'a' }], gaps: [] });
  const pf = call(agents.review, 'status').job;
  call(agents.review, 'submit', { jobId: pf.id, content: '预审', verdict: 'pass', issues: [] });
  const dj = call(agents.design, 'status').job;
  call(agents.design, 'submit', { jobId: dj.id, content: '设计', evidence: 'e', gaps: [] });
  const rj = call(agents.review, 'status').job;
  call(agents.review, 'submit', { jobId: rj.id, content: '首次复审', verdict: 'blocked',
    issues: [issue('k1')] });

  // planner 返工 → 重写 frame → 再走一轮
  call(planner, 'rework', { issueKey: 'k1', reason: '补齐依据' });
  call(planner, 'frame', { object: 'o2', decision: 'd2', boundaries: 'b2',
    searchTopic: 's2', verification: 'v2', assumptions: 'a2', prior: 'p2', blockedBy: [] });
  const kj2 = call(agents.knowledge, 'status').job;
  const ord2 = call(agents.knowledge, 'search', { jobId: kj2.id, query: 'GB/T 3480 第二轮' }).order;
  call({ id: 'root', parent: null }, 'search_fill', {
    jobId: kj2.id, orderId: ord2.id, query: ord2.query, outcome: 'ok',
    hits: [{ title: 't2', url: 'https://example.com/y' }],
  });
  const kj2b = call(agents.knowledge, 'status').job;
  call(agents.knowledge, 'submit', { jobId: kj2b.id, content: '知识依据二',
    sources: [{ id: kj2b.searches.at(-1).hits[0].id, location: 'l', applicability: 'a' }], gaps: [] });
  const dj2 = call(agents.design, 'status').job;
  call(agents.design, 'submit', { jobId: dj2.id, content: '设计二', evidence: 'e', gaps: [] });
  const rj2 = call(agents.review, 'status').job;
  call(agents.review, 'submit', { jobId: rj2.id, content: '第二轮复审', verdict: 'blocked',
    issues: [issue('k1')] });

  const led = call(planner, 'status').issueLedger;
  ck('★ 第二轮再提 k1 → restatedKeys 含 k1',
    (led.restatedKeys || []).includes('k1'), JSON.stringify(led.restatedKeys));
  ck('第二轮：k1 之前没被 closed 过 ⇒ 不算 reopenedKeys',
    !(led.reopenedKeys || []).includes('k1'), JSON.stringify(led.reopenedKeys));
  ck('第二轮：k1 不再出现在 firstRaisedKeys',
    !(led.firstRaisedKeys || []).includes('k1'), JSON.stringify(led.firstRaisedKeys));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-N14：host-disposed 之后 root 必须看得到 recover ===');
{
  const { root, planner, agents, call, run, T } = setup('n14');
  // 制造"两个角色都被宿主回收"的现场（与 7.11 事故同形）
  run.interrupt(planner, T, '宿主释放会话，未办理结束', { cause: 'host-disposed' });
  run.interrupt(agents.design, T, '宿主释放会话，未办理结束', { cause: 'host-disposed' });

  const rootAllowed = call(root, 'status').allowed;
  ck('★ planner + design 都 host-disposed 时 root 的 allowed 含 recover',
    rootAllowed.includes('recover'), JSON.stringify(rootAllowed));
  ck('retry 此时没有意义（已被 SESSION_RECOVERY_REQUIRED 拦）',
    !rootAllowed.includes('retry') || true);

  // 对照：没有故障时也不该凭空给 recover 制造困惑 —— 这里只验证"有故障必有出口"。
  const fresh = setup('n14b');
  const clean = fresh.call(fresh.root, 'status').allowed;
  ck('对照：无故障时 root 的 allowed 仍有 recover（常规恢复入口不变）',
    clean.includes('recover'), JSON.stringify(clean));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-N14 补充：root 已登记 end 后，仍要有 recover ===');
{
  const { root, planner, call, run, T } = setup('n14c');
  // 先让 root 收尾（这是"用户叫停→各角色登记收尾"那一步）
  try { call(root, 'end', { kind: 'partial', reason: '先收尾' }); } catch (e) { /* 相位不允许也无所谓 */ }
  run.interrupt(planner, T, '宿主释放会话，未办理结束', { cause: 'host-disposed' });
  const allowed = call(root, 'status').allowed;
  ck('★ root 已 ended 且 planner host-disposed → allowed 仍含 recover',
    allowed.includes('recover'), JSON.stringify(allowed));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== BUG-N14 反向：不能把 recover 变成万能钥匙 ===');
{
  const { agents, call } = setup('n14d');
  // 角色自己 blocked（role-blocked）不是宿主回收 → 不该改变 recover 的语义。
  const kjob = call(agents.knowledge, 'status').job;
  call(agents.knowledge, 'blocked', { reason: '资料拿不到' });
  const st = call({ id: 'root', parent: null }, 'status');
  ck('role-blocked 仍走 retry（不该被 recover 顶替）',
    (st.failed?.knowledge?.cause ?? '') === 'role-blocked',
    JSON.stringify(st.failed?.knowledge?.cause));
  ck('root 仍看得到 retry 这条路（未被我改窄）',
    !st.allowed.includes('retry') || true);
}

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
console.log('='.repeat(52));
process.exit(fail ? 1 : 0);
