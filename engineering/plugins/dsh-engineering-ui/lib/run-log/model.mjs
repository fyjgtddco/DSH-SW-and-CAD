const ROLES = ['root', 'planner', 'knowledge', 'design', 'review'];
const LABELS = {root:'主对话', planner:'总计子代理', knowledge:'知识', design:'设计', review:'反驳', unknown:'未绑定角色'};
const PHASE_OWNER = {frame:'planner', knowledge:'knowledge', design:'design', preflight:'review',
  review:'review', resolve:'planner', commit:'planner'};
const TOOL_LABELS = {web_search:'联网检索', wait_agent:'等待子代理', ask_user_question:'等待真人答复',
  subagent_fork:'启动子代理', send_message:'发送消息', list_agents:'查询子代理'};
const HOST_SOURCE = 'DSH 会话';
const AUDIT_SOURCE = '七阶段台账';
const clean = (v, max = 220) => typeof v === 'string'
  ? v.replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, ' ').trim().slice(0, max) || null : null;
export const validId = v => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(v);
const id = v => validId(v) ? v : null;
const integer = v => Number.isSafeInteger(v) && v >= 0 ? v : null;
const timestamp = v => {
  const n = typeof v === 'string' ? Date.parse(v) : v;
  return Number.isFinite(n) && n > 0 && n <= 8.64e15 ? n : null;
};
const list = v => Array.isArray(v) ? v : [];
const obj = v => v && typeof v === 'object' && !Array.isArray(v) ? v : {};
const stepId = v => typeof v === 'string' && /^\d{1,2}\.\d{1,2}$/.test(v) ? v : null;
const errorId = v => typeof v === 'string' && /^[A-Za-z][A-Za-z0-9_.-]{1,119}$/.test(v) ? v : null;
const parse = v => {
  if (typeof v !== 'string') return obj(v);
  try { return obj(JSON.parse(v)); } catch { return {}; }
};
const elapsed = (start, end) => start != null && end >= start ? end - start : null;
const keyForStep = d => Number.isInteger(d.turn) && Number.isInteger(d.step) ? `${d.turn}/${d.step}` : null;

function visibleOutput(message) {
  const text = list(message?.content).filter(block => block?.type === 'text' && typeof block.text === 'string')
    .map(block => block.text).join('\n')
    .replace(/-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)/g, '[已隐藏密钥]')
    .replace(/\bBearer[ \t]+[^\s"',;]+/gi, 'Bearer [已隐藏]')
    .replace(/\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,})\b/g, '[已隐藏密钥]')
    .replace(/(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|authorization|password|secret)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)/gi, '$1[已隐藏]')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, '').trim();
  if (!text) return {};
  return { output: text.slice(0, 4000), ...(text.length > 4000 ? { outputTruncated: true } : {}) };
}

function sessionRows(session) {
  const ownStart = timestamp(session.header.createdAt);
  const inherited = integer(session.inheritedEventCount) ?? 0;
  return list(session.events).filter(e => e && timestamp(e.time) &&
    (!inherited || integer(e.seq) >= inherited) && (!ownStart || timestamp(e.time) >= ownStart));
}

function pluginResult(message) {
  for (const block of list(message?.content)) {
    if (block?.type !== 'text') continue;
    const parsed = parse(block.text);
    if (Object.keys(parsed).length) return parsed;
  }
  return {};
}

function callContext(args) {
  const payload = parse(args.dataJson);
  return {taskId:id(args.taskId), step:stepId(payload.step), jobId:id(payload.jobId),
    orderId:id(payload.orderId), messageId:id(payload.messageId),
    role:ROLES.includes(payload.role) ? payload.role : null, revision:integer(payload.revision)};
}

function failureContext(output, hostError, rejected) {
  if (!rejected) return {};
  const pluginCode = errorId(output.code) ?? errorId(output.error);
  const hostCode = errorId(hostError?.code);
  const pluginDescription = output.ok === false
    ? [output.error, output.note].map(x => clean(x,450)).filter((x,i,a) => x && x !== pluginCode && a.indexOf(x) === i)
    : [];
  return {failed:true, errorCode:pluginCode ?? hostCode, pluginCode, hostCode,
    errorSource:output.ok === false ? '七阶段工具回执' : 'DSH 工具结果',
    errorName:errorId(hostError?.name), errorStatus:integer(hostError?.status),
    detail:pluginDescription.join(' · ') || '日志未附可展示的错误说明',
    currentJobId:id(output.currentJob), currentOrderId:id(output.searchOrder?.id),
    blockedOn:clean(output.waitingOn), allowed:list(output.allowed).filter(validId).slice(0,30)};
}

function projection(view, at) {
  const p = obj(view.progress), current = obj(view.current), order = obj(view.search?.order);
  return {taskId:id(view.taskId), phase:clean(view.phase, 48),
    step:stepId(current.step?.id ?? current.step ?? view.activeStep),
    done:integer(p.done), total:integer(p.total), revision:integer(view.revision),
    waitingOn:clean(view.waitingOn), waitReason:clean(view.waitReason), changedAt:at,
    source:'七阶段工具回执', jobs:list(view.dispatch).filter(j => j && id(j.jobId)).map(j => ({
      role:ROLES.includes(j.role) ? j.role : 'unknown', id:id(j.jobId), step:stepId(j.step), kind:null
    })),
    search:id(order.id) ? {id:id(order.id), status:clean(order.status, 32), hitCount:integer(order.hitCount)} : null,
    question:id(view.question?.id) ? {id:id(view.question.id)} : null};
}

function projectState(s, spec, receipt) {
  const steps = Array.isArray(spec) ? spec : list(spec?.steps);
  const specMatches = steps.length > 0 && (Array.isArray(spec) || s.specHash === spec.sha256);
  const sameTask = receipt?.taskId === s.taskId;
  const compatible = sameTask && receipt.revision === s.revision && receipt.phase === s.phase;
  const jobs = ROLES.filter(role => id(s.jobs?.[role]?.id)).map(role => {
    const j = s.jobs[role];
    return {role, id:id(j.id), step:stepId(j.step), kind:clean(j.kind, 40)};
  });
  const k = s.jobs?.knowledge, order = k?.searchOrder;
  const step = (specMatches ? stepId(steps[s.cursor]?.id) : null) ??
    jobs.find(job => job.step)?.step ?? (compatible ? receipt.step : null);
  const currentReceipt = compatible && receipt.step === step;
  const pending = order?.status === 'pending';
  const failed = ROLES.filter(role => s.failures?.[role] && s.failures[role].revision === s.revision);
  let waitingOn = null;
  if (s.question) waitingOn = '等待真人答复';
  else if (s.phase === 'evidence_wait') waitingOn = '等待关键证据';
  else if (pending) waitingOn = '主对话执行联网检索并回填';
  else if (jobs.length) waitingOn = jobs.map(j => LABELS[j.role]).join('、');
  else if (PHASE_OWNER[s.phase]) waitingOn = LABELS[PHASE_OWNER[s.phase]];
  return {taskId:s.taskId, phase:clean(s.phase, 48), step, done:Array.isArray(s.records) ? s.records.length : null,
    total:specMatches ? steps.length : sameTask ? receipt.total : null, revision:integer(s.revision),
    waitingOn:currentReceipt && receipt.waitingOn ? receipt.waitingOn : waitingOn,
    waitReason:clean(s.waitReason), changedAt:currentReceipt ? receipt.changedAt : null,
    source:AUDIT_SOURCE, jobs, failures:failed,
    search:id(order?.id) ? {id:id(order.id), status:clean(order.status, 32),
      hitCount:integer(order.hitCount) ?? (Array.isArray(order.hits) ? order.hits.length : null)} : null,
    question:id(s.question?.id) ? {id:id(s.question.id)} : null};
}

function readActivities(session, role, mode, add, onStatus) {
  const {header} = session, sessionId = header.id, rows = sessionRows(session);
  const turns = new Map(), calls = new Map(), steps = new Map(), pendingJobs = new Map();
  const translations = new Map(), compactions = new Map();
  let lastAt = timestamp(header.createdAt), last = null, tools = 0, errors = 0;
  let latestTurn = null, latestStep = null;
  const emit = (e, fields) => {
    const event = {id:`${sessionId}:${e.seq ?? e.time}:${fields.kind}:${e.type}`, at:timestamp(e.time),
      role, sessionId, source:HOST_SOURCE, seq:integer(e.seq), ...fields};
    add(event);
    last = event;
    if (event.failed) errors += 1;
  };
  for (const e of rows) {
    const at = timestamp(e.time), d = obj(e.data), stepKey = keyForStep(d);
    lastAt = Math.max(lastAt ?? 0, at);
    switch (e.type) {
      case 'turn/start':
        latestTurn = {id:d.turn, at, open:true};
        turns.set(d.turn, at);
        latestStep = null;
        emit(e,{kind:'turn',title:'回合开始',detail:Number.isInteger(d.turn) ? `第 ${d.turn} 回合` : null});
        break;
      case 'turn/end': {
        const reason = clean(d.reason?.kind ?? d.reason, 40);
        if (latestTurn?.id === d.turn) latestTurn = {id:d.turn, at, open:false, reason};
        emit(e,{kind:'turn',title:'回合结束',detail:reason, durationMs:elapsed(turns.get(d.turn),at)});
        turns.delete(d.turn);
        break;
      }
      case 'step/start':
        if (stepKey) {
          steps.set(stepKey, at);
          latestStep = {key:stepKey, at, replied:false};
        }
        break;
      case 'step/end':
        if (latestStep?.key === stepKey) latestStep = null;
        steps.delete(stepKey);
        break;
      case 'assistant/message': {
        const source = d.message?.source;
        if (latestStep?.key === stepKey) latestStep.replied = true;
        if (source?.kind !== 'model') break;
        const summary = [clean(source.provider, 55),clean(source.model, 90)].filter(Boolean).join(' / ');
        const tokenIn = integer(d.usage?.inputTokens), tokenOut = integer(d.usage?.outputTokens);
        const tokens = tokenIn != null || tokenOut != null ? `输入 ${tokenIn ?? '—'} / 输出 ${tokenOut ?? '—'} tokens` : '';
        emit(e,{kind:'model',title:'模型返回消息',detail:[summary,tokens].filter(Boolean).join(' · '),
          ...(header.parentSession ? visibleOutput(d.message) : {}),
          durationMs:elapsed(steps.get(stepKey),at)});
        break;
      }
      case 'user/message': {
        const source = d.message?.source?.kind ?? d.source?.kind;
        if (source === 'plugin:dsh-engineering-seven-stage') {
          emit(e,{kind:'message',title:'收到七阶段插件消息'});
        } else if (source === 'user' || source === 'direct') {
          emit(e,{kind:'message',title:'收到用户消息'});
        }
        break;
      }
      case 'agent/inbox/spliced':
        emit(e,{kind:'message',title:'宿主消息进入会话'});
        break;
      case 'subagent/catalog':
        emit(e,{kind:'agent',title:'登记子代理',detail:id(d.childId)});
        break;
      case 'tool/call': {
        const name = clean(d.name, 80) ?? '工具';
        const args = name === 'engineering_seven_stage' ? parse(d.arguments ?? d.args) : null;
        const action = id(args?.action);
        const context = args ? callContext(args) : undefined;
        const title = action ? `${name} / ${action}` : Object.hasOwn(TOOL_LABELS,name) ? TOOL_LABELS[name] : name;
        if (clean(d.callId, 128)) calls.set(d.callId, {at, turn:d.turn, action, title, name, context});
        tools += 1;
        emit(e,{kind:'tool',title:`调用 · ${title}`,tool:name,action,callId:clean(d.callId,128),context});
        break;
      }
      case 'tool/result': {
        const msg = obj(d.message), callId = msg.toolCallId ?? d.callId ?? msg.source?.callId;
        const call = calls.get(callId);
        calls.delete(callId);
        const output = call?.name === 'engineering_seven_stage' ? pluginResult(msg) : {};
        const hostError = d.error ?? msg.error;
        const rejected = output.ok === false || msg.isError === true || Boolean(hostError);
        emit(e,{kind:'tool',title:`${rejected ? '失败' : '返回'} · ${call?.title ?? '工具'}`,
          detail:call ? null : '当前日志中缺少对应调用', tool:call?.name, action:call?.action,
          callId:clean(callId,128), context:call?.context, durationMs:elapsed(call?.at,at),
          step:stepId(output.current?.step?.id ?? output.current?.step ?? output.searchOrder?.step) ?? call?.context?.step,
          phase:clean(output.phase,48), ...failureContext(output,hostError,rejected),
          ...(header.parentSession && call && !['ask_user_question', 'request_user_input', 'send_message'].includes(call.name)
            ? visibleOutput(msg) : {})});
        if (!rejected && id(output.taskId) && typeof output.phase === 'string' &&
            Number.isSafeInteger(output.progress?.done) && Number.isSafeInteger(output.progress?.total)) {
          onStatus(projection(output, at));
        }
        break;
      }
      case 'job/registered':
        if (id(d.jobId)) pendingJobs.set(d.jobId,{at, kind:clean(d.kind,50)});
        emit(e,{kind:'agent',title:'宿主登记后台任务',detail:clean(d.kind,50),jobId:id(d.jobId)});
        break;
      case 'job/settled': {
        const job = pendingJobs.get(d.jobId);
        pendingJobs.delete(d.jobId);
        emit(e,{kind:'agent',title:'宿主后台任务结算',detail:clean(d.kind,50),jobId:id(d.jobId),
          durationMs:elapsed(job?.at ?? timestamp(d.startedAt),at)});
        break;
      }
      case 'plugin:translator/request':
      case 'translator/request':
        translations.set(e.seq, at);
        emit(e,{kind:'tool',title:'请求翻译'});
        break;
      case 'plugin:translator/result':
      case 'translator/result':
        emit(e,{kind:'tool',title:'翻译返回',durationMs:elapsed(translations.get(d.requestSeq),at)});
        translations.delete(d.requestSeq);
        break;
      case 'compaction/start':
        compactions.set(d.turn ?? 'current',at);
        emit(e,{kind:'compaction',title:'上下文压缩开始'});
        break;
      case 'compaction/summary':
        emit(e,{kind:'compaction',title:'上下文压缩摘要已记录',
          durationMs:elapsed(compactions.get(d.turn ?? 'current'),at)});
        compactions.delete(d.turn ?? 'current');
        break;
    }
  }
  const open = latestTurn?.open === true;
  const pendingCall = open ? [...calls.values()].filter(c => c.turn === latestTurn.id).at(-1) : null;
  let state = 'unknown', activity = last?.title ?? null, since = last?.at ?? lastAt;
  if (latestTurn && !open) {
    state = latestTurn.reason === 'aborted' ? 'aborted' :
      /^(error|failed)$/.test(latestTurn.reason ?? '') ? 'failed' : 'idle';
    since = latestTurn.at;
  } else if (pendingCall) {
    state = ['wait_agent','ask_user_question'].includes(pendingCall.name) ? 'waiting' : 'tool';
    activity = pendingCall.title;
    since = pendingCall.at;
  } else if (open && latestStep && !latestStep.replied) {
    state = 'model';
    activity = '步骤已开始，尚未记录模型返回';
    since = latestStep.at;
  } else if (open) {
    state = 'running';
    activity = last?.title ?? '回合已开始，尚未记录回合结束';
    since = latestTurn.at;
  }
  const label = clean(session.label ?? rows.find(e => e.type === 'subagent/descriptor')?.data?.label, 100);
  return {id:sessionId, parentId:id(header.parentSession), role, label:label ?? LABELS[role],
    state, activity, since, lastAt, tools, errors, pendingJobs:pendingJobs.size,
    ...(mode === 'history' && open ? {unclosed:true} : {})};
}

export function projectChildRun(log, label) {
  const events = [], receipts = [];
  const agent = readActivities({ ...log, label }, 'unknown', 'history',
    event => events.push(event), receipt => receipts.push(receipt));
  return { agent, events, receipts };
}

export function buildSnapshot({rootSessionId, sessions, state, steps, mode = 'history', limit = 300,
  observedAt = Date.now(), warnings = [], includeAll = false, monitor = null}) {
  if (!validId(rootSessionId)) throw new Error('根会话 ID 无效');
  const notices = list(warnings).map(w => clean(w, 300)).filter(Boolean);
  const source = new Map(list(sessions).filter(s => id(s?.header?.id)).map(s => [s.header.id,s]));
  if (!source.has(rootSessionId) || id(source.get(rootSessionId).header.parentSession)) {
    throw new Error('未取得匹配的根会话日志');
  }
  const belongs = sid => {
    const seen = new Set();
    while (sid && !seen.has(sid)) {
      if (sid === rootSessionId) return true;
      seen.add(sid);
      sid = id(source.get(sid)?.header?.parentSession);
    }
    return false;
  };
  const authoritative = state?.schema === 'dsh-engineering/seven-stage-state@1' &&
    state.root === rootSessionId && id(state.taskId) ? state : null;
  if (state && !authoritative) notices.push('状态文件与此根会话不匹配，已忽略。');
  const participants = [...source.values()].filter(s => belongs(s.header.id));
  const roleOf = new Map([[rootSessionId,'root']]);
  if (authoritative) {
    if (id(state.planner)) roleOf.set(state.planner,'planner');
    for (const role of ROLES.slice(2)) if (id(state.members?.[role])) roleOf.set(state.members[role],role);
  }
  for (const session of participants) {
    if (roleOf.has(session.header.id)) continue;
    const descriptor = sessionRows(session).find(e => e.type === 'subagent/descriptor');
    const label = descriptor?.data?.label ?? session.projection?.agent?.label ?? session.label;
    const role = typeof label === 'string' ? /^七阶段分析 (knowledge|design|review)$/.exec(label)?.[1] : null;
    if (role && source.get(session.header.parentSession)?.header.parentSession === rootSessionId) {
      roleOf.set(session.header.id,role);
    } else if (label === '总计子代理（七阶段分析）' && session.header.parentSession === rootSessionId) {
      roleOf.set(session.header.id,'planner');
    }
  }
  const events = [], receipts = [];
  const agents = participants.map(s => {
    const role = roleOf.get(s.header.id) ?? 'unknown';
    let agent;
    if (s.projection) {
      for (const event of s.projection.events) events.push({ ...event, role });
      for (const receipt of s.projection.receipts) receipts.push(receipt);
      agent = { ...s.projection.agent, role, archivedAt: s.archivedAt, archivePending: s.pending };
    } else {
      agent = readActivities(s, role, mode, event => events.push(event), receipt => receipts.push(receipt));
    }
    const collecting = monitor?.collecting?.find(row => row.id === agent.id);
    return { ...agent, ...(monitor?.active?.includes(agent.id) ? { activation: 'active' }
      : collecting ? { activation: 'collecting', collectionCode: collecting.code, collectionDetail: collecting.detail }
      : s.projection ? { activation: s.pending ? 'archive_pending' : 'archived' } : {}) };
  });
  const roleOrder = [...ROLES,'unknown'];
  agents.sort((a,b) => roleOrder.indexOf(a.role) - roleOrder.indexOf(b.role));
  for (const [sid,role] of roleOf) {
    if (!participants.some(s => s.header.id === sid)) notices.push(`${LABELS[role]}会话日志未取得。`);
  }
  if (authoritative) {
    for (const [i,row] of list(state.log).entries()) {
      const at = timestamp(row?.at), action = id(row?.action);
      if (!at || !action) continue;
      const sessionId = id(row.actor);
      events.push({id:`audit:${state.taskId}:${i}`, at, sessionId, role:roleOf.get(sessionId) ?? 'unknown',
        source:AUDIT_SOURCE, kind:'workflow', title:`台账 · ${action}`, step:stepId(row.step),
        detail:integer(row.revision) != null ? `修订 ${row.revision}` : null, seq:i});
    }
  }
  const sorted = receipts.sort((a,b) => b.changedAt - a.changedAt);
  const recent = authoritative ? sorted.find(r => r.taskId === state.taskId) : sorted[0];
  if (recent) {
    const changed = sorted.find(r => r.taskId === recent.taskId &&
      (r.phase !== recent.phase || r.step !== recent.step || r.revision !== recent.revision));
    // Only the receipt time is observable. A transition may have occurred before it.
    recent.changedAt = (changed && sorted.filter(r => r.taskId === recent.taskId &&
      r.changedAt > changed.changedAt).at(-1)?.changedAt) ?? null;
  }
  const workflow = authoritative ? projectState(state,steps,recent) : recent ?? null;
  if (!authoritative) notices.push(workflow ? '阶段取自最后一条完整工具回执。' : '暂无可核对的七阶段进度。');
  if (mode === 'live') notices.push('运行内容来自 DSH 会话事件，已提交的模型回复和工具结果会出现在时间线。');
  events.sort((a,b) => b.at-a.at || (b.seq ?? 0)-(a.seq ?? 0) || a.id.localeCompare(b.id));
  const slowest = events.filter(e => ['tool','model'].includes(e.kind) && e.durationMs > 0)
    .sort((a,b) => b.durationMs-a.durationMs).slice(0,6);
  limit = includeAll ? events.length : Math.min(1000, Math.max(1,integer(limit) ?? 300));
  return {schema:'dsh-run-log@1', rootSessionId, mode:mode === 'live' ? 'live' : 'history', observedAt,
    lastEventAt:Math.max(0,events[0]?.at ?? 0,...agents.map(a => a.lastAt ?? 0)) || null,
    workflow, agents, events:events.slice(0,limit), slowest, warnings:[...new Set(notices)],
    totalEvents:events.length, limit, fullExport:includeAll, monitor};
}
