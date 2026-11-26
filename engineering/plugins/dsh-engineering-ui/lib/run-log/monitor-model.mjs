/**
 * monitor-model.mjs — 外部监控器诊断日志（/monitor-log）的纯函数模型
 * ==========================================================================
 * 这个模块只做【归一化 + 归类】，不做任何 IO、不持有状态、不 import 任何
 * Node/DOM API：宿主路由（lib/index.js）与浏览器面板（lib/client.js）可以
 * 引用同一份实现，保证"面板看到的"就是"宿主转发下来的"，中间不存在第二
 * 套判定（历史教训：UI 自己猜状态，和门禁打架）。
 *
 * 数据源只有一个：MonitorBridge.readDiagnostics() —— 也就是监控器的
 * POST /v1/diagnostics。诊断序号 seq 独立于 /v1/audit 的 rev，也独立于
 * page-status 的 revision，别混用。
 *
 * 约定（宿主与面板共同的契约）：
 *   buildMonitorLog(payload) -> { ok, code, status, events, warnings, cursor, … }
 *   status.level ∈ 'ok' | 'empty' | 'warn' | 'error' | 'unavailable'
 *   ok 仅表示"这次读取是否按宿主绑定的作用域拿到了数据"；
 *   降级时 ok=false 且 code 明说是哪种降级 —— 绝不返回"空数组 + ok:true"
 *   假装"没有日志"。
 */

/** 宿主路由路径（面板可直接引用，避免两边各写一份字符串）。 */
export const ROUTE = '/dsh-engineering-ui/monitor-log';

/** 单页默认/上限（与监控器 _read_diagnostics 的校验一致：1–500）。 */
export const DEFAULT_LIMIT = 100;
export const MAX_LIMIT = 500;
/** download=1 全量导出时每页条数与总量上限。 */
export const DOWNLOAD_PAGE = 500;
export const MAX_DOWNLOAD_EVENTS = 10000;

/** 监控器允许的 COM 阶段（com_gate.observed_com_call 只产出这些）。 */
export const DIAGNOSTIC_STAGES = new Set([
  'new_document', 'sketch', 'circle', 'rectangle', 'line', 'end_sketch',
  'extrude', 'cut', 'gear', 'helix_gear', 'save', 'close',
  'postcheck', 'worker_timeout', 'cleanup', 'commit',
]);

/** 验收检查项名字（监控器只透传这些键，见 monitor.py _review 的 checks 白名单）。 */
export const REVIEW_CHECKS = new Set([
  '登记', '材料', '物理', '领域', '仿真', '采购清单',
  'DSH 收尾', '任务总核实', '相邻尺寸', '台账一致性',
]);

const TASK_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
const CODE_RE = /^[A-Z][A-Z0-9]{1,23}(?:_[A-Z0-9]{1,24}){0,5}$/;
const HRESULT_RE = /^0x[0-9A-F]{8}$/;
const COMMAND_RE = /^[A-Za-z0-9_-]{1,128}$/;
/** 明显是路径 / 凭证的字符串一律不显示（诊断里绝不该出现原始证据路径）。 */
const UNSAFE_TEXT_RE = /[\\/\u0000-\u001f]|\b(?:bearer\s|(?:api[_-]?key|authorization|token|password)\s*[:=])/iu;

/** 明确属于"信息"的码（启动、接管、恢复 —— 它们不是故障）。 */
const INFO_CODES = new Set([
  'MONITOR_STARTED_COM', 'MONITOR_STARTED_COORDINATOR',
  'HOST_ATTACHED', 'HOST_RECOVERED', 'HOST_HEARTBEAT_RESUMED',
]);
/** 需要人看一眼但不代表失败。 */
const WARN_CODES = new Set([
  'HOST_HEARTBEAT_EXPIRED', 'MONITOR_RESTARTED_WITH_CLAIMED_TICKET',
  'HOST_ATTACH_QUARANTINED',
]);
/** 失败 / 不可用族。 */
const ERROR_RE = /(?:FAILED|TIMEOUT|BLOCKED|UNAVAILABLE|UNREACHABLE|INVALID|QUARANTINED|RESTARTED_DURING|CHANGED|NOT_CONFIRMED)$/;

/** 显示用文本：不可信 / 太长 / 像路径或凭证 → 丢弃。 */
export function cleanText(value, max = 240) {
  if (typeof value !== 'string') return '';
  const text = value.trim();
  if (!text || text.length > max) return '';
  if (UNSAFE_TEXT_RE.test(text)) return '';
  return text;
}

/** 机器码（固定故障码）归一：不是 `A_B_C` 形状的一律丢弃。 */
export function machineCode(value) {
  if (typeof value !== 'string') return '';
  const code = cleanText(value, 64);
  return code && CODE_RE.test(code) ? code : '';
}

/**
 * 故障码 → 严重度。
 *   · 已知的"过程性"码（启动/接管/恢复）→ info；
 *   · 已知的"留意"码（心跳过期、重启时仍有票）→ warn；
 *   · 以 FAILED/TIMEOUT/BLOCKED/… 结尾的固定故障码 → error；
 *   · 其它机器码（监控器以后新增、本模型还不认识）→ warn：
 *     宁可让人看一眼，也不假装它不是故障；但也绝不擅自标红说"失败了"。
 *   · 根本不是机器码 → info（不替监控器编造结论）。
 */
export function severityOf(code) {
  const value = machineCode(code);
  if (!value) return 'info';
  if (INFO_CODES.has(value)) return 'info';
  if (WARN_CODES.has(value)) return 'warn';
  return ERROR_RE.test(value) ? 'error' : 'warn';
}

/** Unix 秒（含小数）→ ISO 字符串；越界/非法返回 ''。 */
export function isoOf(ts) {
  if (!Number.isFinite(ts) || ts <= 0) return '';
  const ms = ts * 1000;
  if (!Number.isFinite(ms) || ms < 0 || ms > 8.64e15) return '';
  try {
    return new Date(ms).toISOString();
  } catch {
    return '';
  }
}

function checksOf(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const out = {};
  for (const [key, status] of Object.entries(value)) {
    if (!REVIEW_CHECKS.has(key)) continue;
    if (status === 'BLOCK' || status === 'UNSUPPORTED') out[key] = status;
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * 一条诊断事件归一化。
 * @returns {{seq:number, op:string, code:string, severity:string, task:string|null,
 *   room:string, part:string, ts:number|null, at:string, stage?:string,
 *   step?:number, hresult?:string, command?:string, checks?:object} | null}
 */
export function normalizeEvent(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const seq = Number.isSafeInteger(raw.seq) && raw.seq > 0 ? raw.seq : null;
  if (seq === null) return null;
  const code = machineCode(raw.code);
  if (!code) return null;
  const ts = Number.isFinite(raw.ts) ? raw.ts : null;
  const event = {
    seq,
    op: cleanText(raw.op, 64) || 'unknown',
    code,
    severity: severityOf(code),
    task: cleanText(raw.task, 128) || null,
    room: cleanText(raw.room, 128),
    part: cleanText(raw.part, 128),
    ts,
    at: isoOf(ts),
  };
  const details = raw.details;
  if (details && typeof details === 'object' && !Array.isArray(details)) {
    if (typeof details.stage === 'string' && DIAGNOSTIC_STAGES.has(details.stage)) {
      event.stage = details.stage;
    }
    if (Number.isSafeInteger(details.step) && details.step > 0 && details.step <= 999999) {
      event.step = details.step;
    }
    if (typeof details.hresult === 'string' && HRESULT_RE.test(details.hresult)) {
      event.hresult = details.hresult;
    }
    if (typeof details.command === 'string' && COMMAND_RE.test(details.command)) {
      event.command = details.command;
    }
    const checks = checksOf(details.checks);
    if (checks) event.checks = checks;
  }
  return event;
}

/** 把监控器返回的原始事件数组归一化，按 seq 升序、去重。 */
export function normalizeEvents(rawEvents) {
  if (!Array.isArray(rawEvents)) return [];
  const seen = new Set();
  const out = [];
  for (const raw of rawEvents) {
    const event = normalizeEvent(raw);
    if (!event || seen.has(event.seq)) continue;
    seen.add(event.seq);
    out.push(event);
  }
  out.sort((a, b) => a.seq - b.seq);
  return out;
}

/** 严重度统计（面板徽标用）。 */
export function countSeverity(events) {
  const counts = { total: 0, error: 0, warn: 0, info: 0 };
  for (const event of events || []) {
    counts.total += 1;
    if (event.severity === 'error') counts.error += 1;
    else if (event.severity === 'warn') counts.warn += 1;
    else counts.info += 1;
  }
  return counts;
}

const LEVEL_LABEL = {
  ok: '诊断日志正常',
  empty: '暂无诊断记录',
  warn: '有需要留意的记录',
  error: '存在失败记录',
  unavailable: '诊断日志不可用',
};

/**
 * 读取结论 → 面板状态。
 * 判定顺序：不可读（ok:false）→ 有 error 事件 → 有 warn 事件 → 有事件 → 空。
 */
export function buildStatus(input = {}) {
  const { ok, code, error, scope, task, incomplete, events = [], truncated } = input;
  const counts = countSeverity(events);
  let level;
  if (ok !== true) level = 'unavailable';
  else if (counts.error > 0) level = 'error';
  else if (counts.warn > 0) level = 'warn';
  else if (counts.total > 0) level = 'ok';
  else level = 'empty';
  const parts = [];
  if (level === 'unavailable') {
    parts.push(machineCode(code) || 'MONITOR_UNAVAILABLE');
    const text = cleanText(String(error ?? ''), 300);
    if (text) parts.push(text);
  } else {
    parts.push(counts.total + ' 条记录');
    if (counts.error) parts.push('失败 ' + counts.error);
    if (counts.warn) parts.push('告警 ' + counts.warn);
    if (incomplete === true) parts.push('监控器本次进程有写入失败，记录可能不全');
    if (truncated === true) parts.push('导出达到上限，已截断');
  }
  return {
    level,
    code: machineCode(code) || '',
    label: LEVEL_LABEL[level],
    detail: parts.join(' · '),
    degraded: ok !== true,
    scope: scope === 'task' ? 'task' : (scope === 'global' ? 'global' : 'unavailable'),
    task: cleanText(task, 128) || null,
    incomplete: incomplete === true,
    truncated: truncated === true,
    counts,
  };
}

/**
 * 宿主路由 / MonitorBridge 的返回 → 面板模型。
 *
 * @param {object} payload MonitorBridge.readDiagnostics() 的返回值
 * @returns {{ok:boolean, code:string, error:string, hint:string, task:string|null,
 *   scope:string, attached:boolean, degraded:boolean, status:object,
 *   events:object[], warnings:string[], counts:object,
 *   cursor:{since_seq:number, next_since_seq:number, latest_seq:number|null,
 *           has_more:boolean, limit:number}, observed_at:string}}
 */
export function buildMonitorLog(payload = {}) {
  const raw = payload && typeof payload === 'object' ? payload : {};
  const ok = raw.ok === true;
  const code = machineCode(raw.code) || '';
  const error = cleanText(String(raw.error ?? ''), 300);
  const hint = cleanText(String(raw.hint ?? ''), 300);
  const events = normalizeEvents(raw.events);
  const task = cleanText(raw.task, 128) || null;
  const scope = raw.scope === 'task' ? 'task' : (raw.scope === 'global' ? 'global' : 'unavailable');
  const attached = raw.attached === true;
  const incomplete = raw.incomplete === true;
  const truncated = raw.truncated === true;
  const limit = Number.isSafeInteger(raw.limit) && raw.limit >= 1 && raw.limit <= MAX_LIMIT
    ? raw.limit : DEFAULT_LIMIT;
  const since = Number.isSafeInteger(raw.since_seq) && raw.since_seq >= 0 ? raw.since_seq : 0;
  const latest = Number.isSafeInteger(raw.latest_seq) && raw.latest_seq >= 0 ? raw.latest_seq : null;
  const next = Number.isSafeInteger(raw.next_since_seq) && raw.next_since_seq >= since
    ? raw.next_since_seq : since;

  const warnings = [];
  if (!ok) {
    warnings.push((code || 'MONITOR_UNAVAILABLE') + (error ? '：' + error : ''));
    if (hint) warnings.push(hint);
  }
  if (scope === 'global' && task) {
    warnings.push('宿主尚未按任务作用域读取（' + (attached ? '当前绑定缺失' : '监控器未 attach') + '），下面只包含全局事件');
  }
  if (incomplete) {
    warnings.push('监控器本次进程诊断写入失败（MONITOR_DIAGNOSTICS_WRITE_FAILED），这段期间的记录无法事后补齐');
  }
  if (raw.has_more === true) {
    warnings.push('还有更多事件：用 next_since_seq=' + next + ' 继续读取');
  }
  if (truncated) {
    warnings.push('全量导出达到上限（' + MAX_DOWNLOAD_EVENTS + ' 条），仅含前 ' + events.length + ' 条');
  }

  return {
    ok,
    code,
    error,
    hint,
    task,
    scope,
    attached,
    degraded: !ok,
    status: buildStatus({ ok, code, error, scope, task, incomplete, events, truncated }),
    events,
    warnings,
    counts: countSeverity(events),
    cursor: {
      since_seq: since,
      next_since_seq: next,
      latest_seq: latest,
      has_more: raw.has_more === true,
      limit,
    },
    observed_at: cleanText(raw.observed_at, 64) || '',
  };
}

/**
 * 解析 /monitor-log 的查询串。
 * 只接受 since_seq / limit / download / task（task 仅用于"任务已切换"的连续性核对，
 * 不是数据源 —— 数据作用域永远由宿主从 workflow_state.json 绑定）。
 *
 * @param {URLSearchParams|{get:(k:string)=>string|null}} sp
 * @returns {{ok:true, since_seq:number, limit:number, download:boolean, task:string|null}
 *          |{ok:false, code:string, error:string}}
 */
export function parseMonitorLogQuery(sp) {
  const get = (key) => {
    try {
      const value = sp && typeof sp.get === 'function' ? sp.get(key) : null;
      return value === null || value === undefined ? null : String(value);
    } catch {
      return null;
    }
  };
  const rawSeq = get('since_seq');
  if (rawSeq !== null && rawSeq !== '' && !/^\d{1,15}$/.test(rawSeq)) {
    return { ok: false, code: 'DIAGNOSTICS_CURSOR_INVALID', error: 'since_seq 必须是非负整数' };
  }
  const since_seq = rawSeq && rawSeq !== '' ? Number(rawSeq) : 0;
  if (!Number.isSafeInteger(since_seq)) {
    return { ok: false, code: 'DIAGNOSTICS_CURSOR_INVALID', error: 'since_seq 越界' };
  }
  const rawLimit = get('limit');
  let limit = DEFAULT_LIMIT;
  if (rawLimit !== null && rawLimit !== '') {
    if (!/^\d{1,4}$/.test(rawLimit)) {
      return { ok: false, code: 'DIAGNOSTICS_LIMIT_INVALID', error: 'limit 必须是 1–' + MAX_LIMIT + ' 的整数' };
    }
    limit = Number(rawLimit);
    if (!(limit >= 1 && limit <= MAX_LIMIT)) {
      return { ok: false, code: 'DIAGNOSTICS_LIMIT_INVALID', error: 'limit 必须是 1–' + MAX_LIMIT + ' 的整数' };
    }
  }
  const downloadRaw = get('download');
  const download = downloadRaw === '1' || downloadRaw === 'true';
  const taskRaw = get('task');
  const task = taskRaw && TASK_RE.test(taskRaw) ? taskRaw : null;
  if (taskRaw && !task) {
    return { ok: false, code: 'TASK_SCOPE_INVALID', error: 'task 参数格式非法' };
  }
  return { ok: true, since_seq, limit, download, task };
}

/** 导出文件名（ASCII 安全，避免 Content-Disposition 里的中文/注入）。 */
export function exportFilename(task, now = new Date()) {
  const stamp = (() => {
    try {
      return now.toISOString().replace(/[:.]/g, '-').slice(0, 19);
    } catch {
      return 'unknown-time';
    }
  })();
  const scope = task && TASK_RE.test(task) ? task : 'global';
  return 'monitor-log-' + scope + '-' + stamp + '.json';
}
