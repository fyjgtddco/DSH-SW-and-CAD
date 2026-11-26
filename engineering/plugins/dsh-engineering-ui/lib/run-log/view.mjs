// ══════════════════════════════════════════════════════════════════════════
// view.mjs — 「七阶段运行日志」面板的浏览器半（由宿主同源发布，动态 import）
// ══════════════════════════════════════════════════════════════════════════
// 与独立预览版的唯一差异（对齐 DSH 工程模式页面）：
//   ① 下载文件名追加本地时间戳，同一任务多次导出不会互相覆盖；
//   ② 其余逻辑与 model.mjs 的摘要口径逐字一致，不做第二套判定。
// 本文件只读：不写会话、不调工具、不发网络请求（请求由宿主传入的 load 发出）。
const roles = {
  root: '主对话', planner: '总计子代理', knowledge: '知识', design: '设计',
  review: '反驳 / 复审', unknown: '身份未确认',
};
const kinds = {
  workflow: '流程', tool: '工具', model: '模型', turn: '回合',
  agent: 'Agent', message: '消息', compaction: '上下文压缩',
};
const phases = {
  setup: '绑定角色', frame: '确定步骤范围', knowledge: '搜集知识', preflight: '预审',
  design: '设计', review: '复审', resolve: '处理异议', commit: '登记完成',
  evidence_wait: '等待补证', ready: '分析完成', partial: '部分交付',
};
const states = {
  model: '未见模型返回', tool: '未见工具返回', running: '回合未记录结束',
  waiting: '等待调用返回', idle: '最近回合已结束', aborted: '最近回合已中止',
  failed: '最近回合失败', unknown: '状态未确认',
};
const sources = { session: '会话日志', state: '插件状态', audit: '插件审计' };
const time = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
const timestamp = value => Number.isFinite(value) && Math.abs(value) < 8.64e15 ? time.format(value) : '—';
const display = value => value === '' || value == null ? '—' : String(value);
const roleName = value => roles[value] ?? roles.unknown;
const kindName = value => kinds[value] ?? '事件';
const sourceName = value => sources[value] ?? display(value);
const phaseName = value => phases[value] ?? display(value);
const hasError = e => e.failed === true || Boolean(e.errorCode);

function duration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} 秒`;
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

function element(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  el.append(...children.filter(child => child != null));
  return el;
}

function chip(text, tone = 'normal') {
  return element('span', { class: 'rl-chip', 'data-tone': tone }, text);
}

function field(label, value) {
  return element('div', { class: 'rl-field' },
    element('dt', {}, label),
    element('dd', {}, display(value)));
}

function renderWorkflow(workflow, observedAt, history) {
  const wrapper = element('section', { class: 'rl-workflow', 'aria-label': '流程状态' });
  if (!workflow) {
    wrapper.append(element('div', { class: 'rl-card' }, element('h2', {}, '流程状态'),
      element('p', { class: 'rl-muted' }, '当前会话未读到可确认的七阶段状态。可继续查看 Agent 和会话日志。')));
    return wrapper;
  }
  const w = workflow;
  const progressKnown = Number.isInteger(w.done) && Number.isInteger(w.total) &&
    w.total > 0 && w.done >= 0 && w.done <= w.total;
  const progress = element('article', { class: 'rl-card' },
    element('h2', {}, '子步骤进度'),
    element('div', { class: 'rl-big' }, progressKnown ? `${w.done} / ${w.total}` : '待确认'));
  if (progressKnown) {
    progress.append(element('progress', { value: w.done, max: w.total, 'aria-label': '已登记完成的子步骤数' }));
  }
  progress.append(element('dl', { class: 'rl-fields' },
    field('观察到的步骤', w.step), field('修订', w.revision)));

  const failedRoles = Array.isArray(w.failures) ? w.failures.map(roleName) : [];
  const waitTone = w.phase === 'evidence_wait' || w.question || failedRoles.length ? 'warn' : 'normal';
  const phase = element('article', { class: 'rl-card' },
    element('h2', {}, '流程阶段'),
    chip(phaseName(w.phase), waitTone),
    element('dl', { class: 'rl-fields' },
      field('正在等', w.waitingOn),
      field('原因', w.waitReason),
      failedRoles.length ? field('流程故障角色', failedRoles.join('、')) : null,
      field('状态来源', sourceName(w.source))));
  if (Number.isFinite(w.changedAt)) {
    phase.append(element('p', { class: 'rl-muted' },
      `阶段记录于 ${timestamp(w.changedAt)}`,
      !history && Number.isFinite(observedAt) && observedAt >= w.changedAt
        ? ` · 截至采集已过 ${duration(observedAt - w.changedAt)}` : ''));
  }

  const pending = element('article', { class: 'rl-card' }, element('h2', {}, '任务与外部依赖'));
  const jobs = Array.isArray(w.jobs) ? w.jobs : [];
  const list = element('dl', { class: 'rl-fields' },
    field('工作单', jobs.length
      ? jobs.map(j => [roleName(j.role), j.step, phaseName(j.kind), j.id].filter(Boolean).join(' · ')).join('\n')
      : '未观察到'),
    field('检索工单', w.search
      ? [w.search.status === 'pending' ? '等搜索回填'
        : w.search.status === 'filled' ? '已回填' : w.search.status,
      Number.isInteger(w.search.hitCount) ? `${w.search.hitCount} 条来源` : null, w.search.id]
        .filter(Boolean).join(' · ')
      : '未观察到'),
    field('等真人答复', w.question ? (w.question.id ?? '已观察到提问') : '未观察到'),
    field('任务编号', w.taskId));
  pending.append(list);
  wrapper.append(progress, phase, pending);
  return wrapper;
}

function renderAgents(agents, mode, observedAt) {
  const roster = element('section', { 'aria-label': 'Agent 状态' },
    element('div', { class: 'rl-section-head' },
      element('h2', {}, 'Agent 状态'),
      element('span', { class: 'rl-muted' }, mode === 'history' ? '截至归档末尾' : '截至最近一次采集')));
  if (!agents.length) {
    roster.append(element('p', { class: 'rl-empty' }, '暂未读取到会话记录。'));
    return roster;
  }
  const group = element('div', { class: 'rl-agents' });
  for (const a of agents) {
    const tone = a.state === 'failed' || a.state === 'aborted' ? 'error'
      : a.state === 'waiting' ? 'warn'
      : a.state === 'model' || a.state === 'tool' || a.state === 'running' ? 'active' : 'normal';
    const start = Number.isFinite(a.since) ? timestamp(a.since) : null;
    const open = ['model', 'tool', 'running', 'waiting'].includes(a.state);
    const age = open && mode === 'live' && start && Number.isFinite(observedAt)
      ? duration(observedAt - a.since) : null;
    const card = element('article', { class: 'rl-card rl-agent' },
      element('h3', {}, roleName(a.role)),
      chip((mode === 'history' && open
        ? '末次记录 · ' : '') + (states[a.state] ?? states.unknown), tone),
      element('p', { class: 'rl-activity' }, a.activity || '未观察到最近活动'),
      element('dl', { class: 'rl-fields' },
        start ? field('状态起点', start) : null,
        age ? field('距状态起点', age) : null,
        field('末次事件', timestamp(a.lastAt)),
        field('工具 / 异常', `${display(a.tools)} / ${display(a.errors)}`),
        Number.isInteger(a.pendingJobs) && a.pendingJobs > 0 ? field('未结算任务', a.pendingJobs) : null,
        field('会话', a.id)));
    if (a.label && a.label !== roles[a.role]) card.append(element('p', { class: 'rl-muted' }, String(a.label)));
    group.append(card);
  }
  roster.append(group);
  return roster;
}

const detailText = value => typeof value === 'string' ? value
  : value && typeof value === 'object' ? JSON.stringify(value, null, 2) : '';

function renderEvent(e, expanded) {
  const meta = [sourceName(e.source), e.step ? `步骤 ${e.step}` : null,
    e.phase ? phaseName(e.phase) : null].filter(Boolean).join(' · ');
  const header = element('span', { class: 'rl-event-copy' },
    element('strong', {}, display(e.title)),
    element('small', {}, meta));
  const detail = [
    detailText(e.detail),
    e.errorSource && `错误来源 ${e.errorSource}`,
    e.hostCode && e.pluginCode && `宿主错误码 ${e.hostCode}`,
    e.errorName && `错误类型 ${e.errorName}`,
    e.blockedOn && `等待对象 ${e.blockedOn}`,
    e.context?.jobId && `请求工作单 ${e.context.jobId}`,
    e.currentJobId && `当前工作单 ${e.currentJobId}`,
    e.context?.orderId && `请求检索工单 ${e.context.orderId}`,
    e.currentOrderId && `当前检索工单 ${e.currentOrderId}`,
    e.sessionId && `会话 ${e.sessionId}`,
    e.callId && `调用 ${e.callId}`,
    e.jobId && `后台任务 ${e.jobId}`,
    Number.isInteger(e.seq) && `事件序号 ${e.seq}`,
  ].filter(Boolean).join('\n');
  const body = detail
    ? element('details', { class: 'rl-details', 'data-event-id': display(e.id) },
      element('summary', {}, header),
      element('div', { class: 'rl-detail' }, detail))
    : header;
  if (detail && expanded.has(display(e.id))) body.open = true;
  return element('li', { class: 'rl-event', 'data-error': hasError(e) },
    element('time', { class: 'rl-event-time' }, timestamp(e.at)),
    element('div', { class: 'rl-event-type' }, element('strong', {}, roleName(e.role)),
      element('small', {}, kindName(e.kind))),
    body,
    element('div', { class: 'rl-event-result' },
      hasError(e) ? chip(e.errorCode ?? '源日志无错误码', 'error') : null,
      Number.isFinite(e.durationMs) ? element('small', {}, duration(e.durationMs)) : null));
}

function renderSlowest(events) {
  const wrap = element('section', { 'aria-label': '耗时最长的操作' },
    element('div', { class: 'rl-section-head' },
      element('h2', {}, '耗时最长的操作'),
      element('span', { class: 'rl-muted' }, '按全部已读事件排序，仅统计起止齐全的模型和工具操作')));
  if (!Array.isArray(events) || !events.length) {
    wrap.append(element('p', { class: 'rl-empty' }, '未观察到可配对的开始和返回事件。'));
    return wrap;
  }
  wrap.append(element('ol', { class: 'rl-events' }, ...events.map(e =>
    element('li', { class: 'rl-event', 'data-error': hasError(e) },
      element('time', { class: 'rl-event-time' }, timestamp(e.at)),
      element('div', { class: 'rl-event-type' }, element('strong', {}, roleName(e.role)),
        element('small', {}, kindName(e.kind))),
      element('div', { class: 'rl-event-copy' }, element('strong', {}, display(e.title)),
        element('small', {}, sourceName(e.source))),
      element('strong', { class: 'rl-elapsed' }, duration(e.durationMs))))));
  return wrap;
}

export function mountLogPanel(container, { load, intervalMs = 2500 } = {}) {
  if (!container || typeof load !== 'function') throw new TypeError('需要容器和 load 函数');
  const limit = 300;
  let snapshot = null;
  let disposed = false;
  let polling = false;
  let timer;
  let request;
  let downloadRequest;
  // 两个看门狗计时器（轮询 15s / 下载 30s）。单独留引用是为了卸载时能立刻清掉：
  //   如果 load 永不 settle（模块被换掉、宿主没响应），finally 永远不会跑，
  //   只看 finally 清理会让一个 15s 计时器活到自然到期。
  let pollWatchdog;
  let downloadWatchdog;
  let lastEventKeys;
  const status = element('p', { class: 'rl-status', role: 'status', 'aria-live': 'polite' }, '正在读取会话日志…');
  const updated = element('p', { class: 'rl-muted' });
  const refresh = element('button', { class: 'rl-button', type: 'button' }, '立即刷新');
  const download = element('button', { class: 'rl-button', type: 'button' }, '下载日志 JSON');
  const downloadStatus = element('p', { class: 'rl-muted', role: 'status' });
  download.disabled = true;
  const notice = element('div', { class: 'rl-notices' });
  const workflow = element('div');
  const roster = element('div');
  const slowest = element('div');
  const count = element('p', { class: 'rl-muted' });
  const events = element('ul', { class: 'rl-events', 'aria-label': '时间线事件' });
  const empty = element('p', { class: 'rl-empty' }, '正在读取事件…');

  function select(label, options) {
    const el = element('select', { 'aria-label': label },
      element('option', { value: '' }, label),
      ...Object.entries(options).map(([value, name]) => element('option', { value }, name)));
    el.addEventListener('change', renderEvents);
    return el;
  }
  const roleFilter = select('全部角色', roles);
  const kindFilter = select('全部事件', kinds);
  const errorsOnly = element('input', { type: 'checkbox' });
  const keyword = element('input', { type: 'search', placeholder: '找事件或错误码', 'aria-label': '搜索事件或错误码' });
  errorsOnly.addEventListener('change', renderEvents);
  keyword.addEventListener('input', renderEvents);

  const root = element('div', { class: 'dsh-runlog' },
    element('header', { class: 'rl-header' },
      element('div', {}, element('h1', {}, '七阶段运行日志'), status, updated),
      element('div', { class: 'rl-actions' }, refresh, download, downloadStatus)),
    notice, workflow, roster, slowest,
    element('section', { 'aria-label': '事件时间线' },
      element('div', { class: 'rl-section-head' }, element('h2', {}, '事件时间线'), count),
      element('div', { class: 'rl-filters' }, roleFilter, kindFilter, keyword,
        element('label', {}, errorsOnly, '仅异常')),
      empty, events));
  container.append(root);

  function renderEvents() {
    if (!snapshot) return;
    const query = keyword.value.trim().toLowerCase();
    const shown = snapshot.events.filter(e =>
      (!roleFilter.value || e.role === roleFilter.value) &&
      (!kindFilter.value || e.kind === kindFilter.value) &&
      (!errorsOnly.checked || hasError(e)) &&
      (!query || [e.title, e.errorCode, e.step, e.phase, detailText(e.detail)]
        .some(value => value != null && String(value).toLowerCase().includes(query))));
    count.textContent = `显示 ${shown.length} 条 · 已载入最近 ${snapshot.events.length} / ${snapshot.totalEvents} 条`;
    empty.hidden = shown.length > 0;
    empty.textContent = snapshot.events.length ? '没有符合筛选条件的事件。' : '暂未观察到时间线事件。';
    const keys = JSON.stringify(shown.map(e => [e.id, e.title, e.durationMs, e.errorCode, e.phase, e.step]));
    if (keys === lastEventKeys) return;
    lastEventKeys = keys;
    const expanded = new Set([...events.querySelectorAll('details[open]')].map(el => el.dataset.eventId));
    events.replaceChildren(...shown.map(e => renderEvent(e, expanded)));
  }

  function render() {
    const history = snapshot.mode === 'history';
    status.dataset.tone = 'normal';
    status.textContent = history ? '归档回放' : '正在读取实时记录';
    updated.textContent = `最近采集 ${timestamp(snapshot.observedAt)} · 最后事件 ${timestamp(snapshot.lastEventAt)} · 北京时间`;
    notice.replaceChildren(...(snapshot.warnings ?? []).map(message => element('p', {}, String(message))));
    workflow.replaceChildren(renderWorkflow(snapshot.workflow, snapshot.observedAt, history));
    roster.replaceChildren(renderAgents(snapshot.agents, snapshot.mode, snapshot.observedAt));
    slowest.replaceChildren(renderSlowest(snapshot.slowest));
    renderEvents();
    download.disabled = Boolean(downloadRequest);
  }

  async function exportLog() {
    if (!snapshot || downloadRequest || disposed) return;
    const controller = new AbortController();
    downloadRequest = controller;
    download.disabled = true;
    downloadStatus.textContent = '正在准备 JSON…';
    const timeout = setTimeout(() => controller.abort(), 30000);
    downloadWatchdog = timeout;
    try {
      const data = await load({signal:controller.signal, download:true});
      if (disposed) return;
      if (data?.schema !== 'dsh-run-log@1' || data.rootSessionId !== snapshot.rootSessionId ||
          data.fullExport !== true || !Array.isArray(data.events) || data.events.length !== data.totalEvents) {
        throw new Error('接口未返回所选会话的完整日志');
      }
      const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
      const fileId = String(data.workflow?.taskId || data.rootSessionId).replace(/[^A-Za-z0-9_-]/g,'_');
      // 时间戳用【导出那一刻】的本地时间（与面板显示的北京时间同一口径），
      // 目的是同一任务多次导出不互相覆盖；不参与任何判定。
      const at = new Date();
      const pad = value => String(value).padStart(2,'0');
      const stamp = `${at.getFullYear()}${pad(at.getMonth()+1)}${pad(at.getDate())}-`
        + `${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`;
      const link = element('a',{href:url,download:`DSH-运行日志-${fileId}-${stamp}.json`});
      root.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url),1000);
      downloadStatus.textContent = `已发起下载 · ${data.events.length} 条事件`;
    } catch (err) {
      if (!disposed) downloadStatus.textContent = controller.signal.aborted ? '下载请求超时' :
        `下载失败 · ${err?.message || '请求失败'}`;
    } finally {
      clearTimeout(timeout);
      downloadWatchdog = null;
      downloadRequest = null;
      if (!disposed) download.disabled = false;
    }
  }

  async function poll(force = false) {
    if (disposed || polling) return;
    clearTimeout(timer);
    if (!force && document.hidden) return;
    polling = true;
    refresh.disabled = true;
    request = new AbortController();
    // Fetch owns no session mutations. End a stalled request so polling can recover.
    const timeout = setTimeout(() => request.abort(), 15000);
    pollWatchdog = timeout;
    try {
      const data = await load({ signal: request.signal, limit });
      if (disposed) return;
      if (data?.schema !== 'dsh-run-log@1' || !Array.isArray(data.events) || !Array.isArray(data.agents)) {
        throw new Error('日志接口格式不匹配');
      }
      snapshot = data;
      render();
    } catch (err) {
      if (disposed) return;
      status.dataset.tone = 'error';
      const reason = request.signal.aborted ? '请求超时' : (err?.message || '请求失败');
      status.textContent = snapshot ? `读取失败，保留上次快照 · ${reason}` : `暂时无法读取日志 · ${reason}`;
      if (!snapshot) empty.textContent = '数据尚未载入。';
    } finally {
      clearTimeout(timeout);
      pollWatchdog = null;
      polling = false;
      if (!disposed) {
        refresh.disabled = false;
        // An archive is immutable for this view and can be refreshed by hand.
        if (snapshot?.mode !== 'history' || status.dataset.tone === 'error') {
          timer = setTimeout(() => poll(), intervalMs);
        }
      }
    }
  }
  function onVisibility() {
    if (!document.hidden && snapshot?.mode !== 'history') void poll();
  }
  refresh.addEventListener('click', () => poll(true));
  download.addEventListener('click', exportLog);
  document.addEventListener('visibilitychange', onVisibility);
  void poll(true);
  return () => {
    disposed = true;
    clearTimeout(timer);
    // 看门狗一并清掉：卸载后不该再有任何计时器在跑（哪怕 load 卡住不 settle）。
    clearTimeout(pollWatchdog);
    clearTimeout(downloadWatchdog);
    pollWatchdog = null;
    downloadWatchdog = null;
    request?.abort();
    downloadRequest?.abort();
    document.removeEventListener('visibilitychange', onVisibility);
    root.remove();
  };
}
