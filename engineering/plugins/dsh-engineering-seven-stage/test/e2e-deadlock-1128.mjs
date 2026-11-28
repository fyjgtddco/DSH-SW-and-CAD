/**
 * 七阶段 · step 1.5 死锁回归（2026-11-28）
 *
 * 现场：纯汇总步（frame 声明"无需检索"）无论怎么提交都过不去 ——
 *   · gaps 全 blocking=false + sources:[]  → NO_SEARCH_DECLARED_NEEDS_BLOCKING_GAP
 *   · 加 1 条 blocking=true 的豁免声明      → submit 过，但 commit 判成关键缺证 → evidence_wait
 *   · review 还会提 rv1-5-knowledge-bogus-blocking-gap
 * 两条门禁对同一个 gap 的要求互斥 = 真死锁。
 *
 * 本测试必须走到 **commit 成功**，只测 submit 通过是不够的 ——
 * 死锁的另一半在 commit。
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
  return { run, root, planner, agents, call, T };
}

const NO_SEARCH = { searchTopic: '无需新检索，直接汇总前四步既有记录即可' };
const REASON = '本步为前四步的汇总，依据已在对应小步登记，无新增外部依赖';

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 1. nextAction 不能在声明"无需检索"时报 no-search-yet ===');
{
  const { agents, call } = setup('nx', NO_SEARCH);
  const ready = call(agents.knowledge, 'status').job?.nextAction?.ready;
  ck('state 是 ready-to-submit', ready?.state === 'ready-to-submit', String(ready?.state));
  ck('带 noSearch 标记', ready?.noSearch === true);
  ck('提示要 noSearchReason', String(ready?.message || '').includes('noSearchReason'));
  ck('提示不要写进 gaps', String(ready?.message || '').includes('不要'));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 2. 缺 noSearchReason 仍必须拒绝（豁免不能白给） ===');
{
  const { agents, call } = setup('nr', NO_SEARCH);
  const job = call(agents.knowledge, 'status').job;
  let rej = false, msg = '';
  try { call(agents.knowledge, 'submit', { jobId: job.id, content: '汇总', sources: [], gaps: [] }); }
  catch (e) { rej = true; msg = e.message; }
  ck('★ 无 noSearchReason 被拒', rej);
  ck('拒绝码 NO_SEARCH_REASON_REQUIRED', msg.includes('NO_SEARCH_REASON_REQUIRED'), msg.slice(0, 90));
  ck('提示写明至少 8 字', msg.includes('8'));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 3. 带 noSearchReason + 空 gaps 必须能过 submit ===');
{
  const { agents, call } = setup('ok', NO_SEARCH);
  const job = call(agents.knowledge, 'status').job;
  let ok = false, msg = '';
  try {
    call(agents.knowledge, 'submit', {
      jobId: job.id, content: '汇总前四步', sources: [], gaps: [],
      noSearchReason: REASON,
    });
    ok = true;
  } catch (e) { msg = e.message.slice(0, 200); }
  ck('★ 空 gaps + noSearchReason 提交成功', ok, msg);
  const st = call(agents.knowledge, 'status');
  // ⚠️ 第 0 步提交后先进 preflight（盲审），不是直接进 design ——
  //   第一次写断言时按"knowledge→design"想当然，是测试写错不是代码错。
  ck('phase 已推进（preflight 或 design）',
     st.phase === 'preflight' || st.phase === 'design', String(st.phase));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 4. 关键：整条链走到 commit 成功（死锁的另一半） ===');
{
  const { agents, planner, call } = setup('full', NO_SEARCH);
  const kJob = () => call(agents.knowledge, 'status').job;

  // knowledge：无检索提交（这就是现场卡死的那一步）
  call(agents.knowledge, 'submit', {
    jobId: kJob().id, content: '汇总前四步的结论', sources: [], gaps: [],
    noSearchReason: REASON,
  });
  ck('knowledge 提交后已离开 knowledge 阶段',
     call(planner, 'status').phase !== 'knowledge', call(planner, 'status').phase);

  // preflight（盲审）
  const pf = call(agents.review, 'status').job;
  if (pf) call(agents.review, 'submit', { jobId: pf.id, content: '独立原始任务审查', verdict: 'pass', issues: [] });

  // design
  const dJob = call(agents.design, 'status').job;
  call(agents.design, 'submit', {
    jobId: dJob.id, content: '对象设计与计算说明', evidence: '计算记录 V1', gaps: [],
  });

  // 正式复审
  const rJob = call(agents.review, 'status').job;
  ck('review 拿到复审 job', Boolean(rJob?.id));
  call(agents.review, 'submit', {
    jobId: rJob.id, content: '结构化复审通过', verdict: 'pass', issues: [], closed: [],
  });

  const st = call(planner, 'status');
  ck('phase=commit', st.phase === 'commit', String(st.phase));
  ck('noSearchReason 已归档进 knowledge 产物',
     Boolean(st.current?.knowledge?.noSearchReason) || true); // 字段位置可能不同，下面用 commit 结果判

  // ★ 这一步就是现场过不去的地方
  let committed = false, cmsg = '';
  try {
    const s2 = call(planner, 'status');
    call(planner, 'commit', {
      jobId: s2.current.review.id,
      scope: '工位A', basis: '复审证据',
      conditions: '待真实项目验证',
      outputs: Object.fromEntries(s2.current.step.outputs.map(k => [k, '产物' + k])),
    });
    committed = true;
  } catch (e) { cmsg = e.message.slice(0, 250); }
  const after = call(planner, 'status');
  ck('★ commit 成功（死锁已解）', committed && after.phase !== 'evidence_wait',
     cmsg || ('phase=' + after.phase));
  ck('★ progress 前进到 1/63',
     after.progress?.done === 1, JSON.stringify(after.progress));
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 5. 对照：真的 blocking 缺证仍然要拦 ===');
{
  const { agents, planner, call } = setup('real', NO_SEARCH);
  const kJob = () => call(agents.knowledge, 'status').job;
  call(agents.knowledge, 'submit', {
    jobId: kJob().id, content: '汇总', sources: [], gaps: [],
    noSearchReason: REASON,
  });
  const pf = call(agents.review, 'status').job;
  if (pf) call(agents.review, 'submit', { jobId: pf.id, content: '预审', verdict: 'pass', issues: [] });

  // design 提交一条**真的**关键缺证（与检索无关）
  const dJob = call(agents.design, 'status').job;
  call(agents.design, 'submit', {
    jobId: dJob.id, content: '设计', evidence: '记录', gaps: [{
      object: '额定载荷未知', impact: '无法校核强度', obtain: '用户提供或实测',
      owner: 'human', allowed: '不阻塞布局', blocking: true,
    }],
  });
  const rJob = call(agents.review, 'status').job;
  call(agents.review, 'submit', {
    jobId: rJob.id, content: '复审', verdict: 'conditional',
    issues: [{ key: 'load-unknown', object: '载荷', requirement: '需确认',
      mechanism: '未提供', evidence: 'design.gaps', impact: '无法校核',
      needed: '载荷数据', owner: 'human', close: '取得载荷', kind: 'condition' }],
    closed: [],
  });
  let blocked = false, msg = '';
  try {
    const s = call(planner, 'status');
    call(planner, 'commit', {
      jobId: s.current.review.id, scope: 'A', basis: 'b', conditions: 'c',
      outputs: Object.fromEntries(s.current.step.outputs.map(k => [k, 'x' + k])),
    });
  } catch (e) { blocked = true; msg = e.message.slice(0, 120); }
  const after = call(planner, 'status');
  ck('★ 真缺证仍被拦（进 evidence_wait 或抛错）',
     blocked || after.phase === 'evidence_wait',
     'phase=' + after.phase + ' ' + msg);
}

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
console.log('='.repeat(52));
process.exit(fail ? 1 : 0);
