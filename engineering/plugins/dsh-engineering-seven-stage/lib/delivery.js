import { randomUUID } from 'node:crypto';
import { linkSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const deliveryRoles = ['root', 'planner', 'knowledge', 'design', 'review'];
export const COALESCE_MS = 60;
export const RECOVERY_MS = 120_000;

export function deliveryTopics(s, role, step) {
  if (!s || s.ended[role]) return [];
  const make = (key,text) => ({key,text});
  if (s.phase==='ready' || s.phase==='partial') {
    return [make('close:'+s.phase, role==='planner' && s.phase==='ready'
      ? '分析已登记完成。处理房间草案和你的收尾登记。'
      : '流程已进入 '+s.phase+'。按角色登记结束原因或核对交接凭证。')];
  }
  const order = s.jobs.knowledge?.searchOrder;
  const items = [];
  if (role==='root') {
    if (order?.status==='pending') items.push(make('order:'+order.id,
      '联网检索工单 '+order.id+'，jobId='+order.jobId+'，查询='+JSON.stringify(order.query)
      +(order.queryParts?.length
        ? '。子查询='+JSON.stringify(order.queryParts)+'，用内置 web_search 的 queries 参数集中执行。'
        : '。用内置 web_search 执行。')
      +'合并来源后只调用一次 search_fill，query 保留工单原文。'));
    if (s.question) items.push(make('question:'+s.question.id,
      '真人问题 '+s.question.id+'：'+s.question.question+'。说明依据：'+s.question.why+'。答复到达后调用 capture。'));
  } else if (role==='planner') {
    if (s.phase==='setup' && !Object.keys(s.invitations ?? {}).length && !s.failures.planner)
      items.push(make('launch','绑定已落盘。调用 launch 启动岗位。'));
    for (const fact of s.facts.filter(f=>!f.interpretation)) items.push(make('fact:'+fact.id,
      '真人答复已落盘，使用 interpret 处理 messageId='+fact.id+'。'));
    if (!s.question && ['frame','commit','resolve','evidence_wait'].includes(s.phase)) {
      const action = {frame:'frame',commit:'commit',resolve:'核查异议并 rework 或 question',
        evidence_wait:'处理补证，可 question、rework 或提交局部规格'}[s.phase];
      items.push(make(['planner',s.phase,s.revision,s.slot?.review?.id ?? '',s.jobs.planner?.id ?? ''].join(':'),
        '总计待办 '+action+(s.phase==='commit' && s.slot?.review?.id ? '，复审记录 jobId='+s.slot.review.id : '')+'。'));
    }
  } else {
    const job = s.jobs[role];
    if (job && !s.failures[role] && !(role==='knowledge' && order?.status==='pending')) {
      const filled = role==='knowledge' && order?.status==='filled';
      const action = role==='knowledge'
        ? (filled || job.searches?.length ? '核对来源 ID 后 submit'
          : '按 status.job.nextAction 核对检索或豁免要求。需要检索时，把主题合成一张 search 工单')
        : (job.kind==='preflight' ? '按盲审可读范围核查原始需求后 submit' : '读取本步材料后 submit');
      items.push(make('job:'+job.id+(filled ? ':filled:'+order.id : ''),
        '工作单 jobId='+job.id+'，目的='+job.kind+'。请'+action+'。'
        +(step?.outputs?.length ? '本步产出：'+step.outputs.join('、')+'。' : '')));
    }
  }
  if (role==='root' || role==='planner') {
    for (const [owner,fault] of Object.entries(s.failures ?? {})) {
      items.push(make('failure:'+owner+':'+(fault.at ?? fault.job ?? fault.reason),
        owner+' 已登记故障（'+(fault.cause ?? '原因待查')+'）。核对 failed 后决定补证、重派或暂停。'));
    }
  }
  return items;
}

export function fourthReason(s, last) {
  if (last?.outcome==='failed') return 'HOST_DELIVERY_FAILED';
  if (s.slot?.rework>=2) return 'SECOND_REVIEW_REWORK';
  if (s.phase==='evidence_wait' && (s.question || s.facts.some(f=>!f.interpretation)))
    return 'CRITICAL_HUMAN_EVIDENCE';
  if (Object.values(s.failures ?? {}).some(f=>f.step===last?.step && f.revision===s.revision))
    return 'ACTIVE_ROLE_FAILURE';
  return null;
}

export function openDeliveryBook(stateDir) {
  // A decided slot counts as an attempt, including a failed or unconfirmed host send.
  // An exclusive hard link reserves the next slot even if two host processes race.
  const file = (task, step, role, n) => join(stateDir,'.delivery',task,`${step}-${role}-${n}.json`);
  function read(task, step, role) {
    const rows = [];
    for (let n=1;n<=4;n++) {
      try { rows.push(JSON.parse(readFileSync(file(task,step,role,n),'utf8'))); }
      catch (err) { if (err.code==='ENOENT') break; throw err; }
    }
    return rows;
  }
  function reserve(task, step, role, select) {
    for (;;) {
      const rows = read(task,step,role);
      const selected = select(rows);
      if (!selected.keys) return selected;
      const n = rows.length + 1;
      if (n > (selected.exception ? 4 : 3)) return {code:'DELIVERY_BUDGET_EXHAUSTED'};
      const path = file(task,step,role,n);
      mkdirSync(join(stateDir,'.delivery',task),{recursive:true,mode:0o700});
      const tmp = path + '.' + randomUUID() + '.tmp';
      const row = {n, step, role, at:Date.now(), ...selected, outcome:'reserved'};
      try {
        writeFileSync(tmp,JSON.stringify(row),{flag:'wx',mode:0o600});
        try { linkSync(tmp,path); }
        catch (err) { if (err.code==='EEXIST') continue; throw err; }
        return {row};
      } finally {
        try { unlinkSync(tmp); } catch (err) { if (err.code!=='ENOENT') throw err; }
      }
    }
  }
  function settle(task, row, outcome) {
    const path = file(task,row.step,row.role,row.n);
    const tmp = path + '.' + randomUUID() + '.tmp';
    try {
      writeFileSync(tmp,JSON.stringify({...row,outcome,settledAt:Date.now()}),{flag:'wx',mode:0o600});
      renameSync(tmp,path);
    } finally {
      try { unlinkSync(tmp); } catch (err) { if (err.code!=='ENOENT') throw err; }
    }
  }
  return {read,reserve,settle};
}
