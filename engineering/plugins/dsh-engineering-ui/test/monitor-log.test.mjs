import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildMonitorLog,
  buildStatus,
  parseMonitorLogQuery,
  normalizeEvent,
  normalizeEvents,
  severityOf,
  machineCode,
  cleanText,
  countSeverity,
  exportFilename,
  DEFAULT_LIMIT,
  MAX_LIMIT,
} from '../lib/run-log/monitor-model.mjs';
import { createMonitorLogHandler } from '../lib/run-log/monitor-log-route.mjs';

const START = { seq: 1, op: 'monitor-start', code: 'MONITOR_STARTED_COM', task: null, ts: 1795670000.25 };
const REJECT = {
  seq: 2, op: 'com-build', code: 'STALE_GENERATION_OR_PART', task: 'A',
  room: '建模', part: '支座', ts: 1795670012.31,
};
const FAIL = {
  seq: 3, op: 'com-complete', code: 'COM_CALL_FAILED', task: 'A', room: '建模', part: '支座',
  ts: 1795670020.5,
  details: { command: 'c-1', stage: 'extrude', step: 4, hresult: '0x80004005' },
};

function response() {
  const headers = {};
  return {
    statusCode: 0, body: null, headers,
    setHeader(k, v) { headers[k.toLowerCase()] = v; },
    end(text) { this.body = JSON.parse(text); },
  };
}

const send = (res, status, body) => {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(body));
};

function query(qs = '') {
  return new URLSearchParams(qs);
}

/** 假的 MonitorBridge：只实现 currentTask / readDiagnostics。 */
function fakeBridge({ task = 'A', pages = [], fail = null } = {}) {
  const calls = [];
  return {
    calls,
    currentTask() { return task; },
    async readDiagnostics({ since_seq = 0, limit = DEFAULT_LIMIT, task: bound = null }) {
      calls.push({ since_seq, limit, task: bound });
      if (fail) return fail;
      const page = pages.shift() || { ok: true, events: [], latest_seq: 0, next_since_seq: since_seq,
        has_more: false, incomplete: false };
      return Object.assign({ ok: true, since_seq, limit, task: bound,
        scope: bound ? 'task' : 'global' }, page);
    },
  };
}

// ── 归一化 ───────────────────────────────────────────────────────────────
test('normalizeEvent keeps only allowlisted details', () => {
  const row = normalizeEvent({ ...FAIL, details: { ...FAIL.details, path: 'C:/raw/x.SLDPRT',
    checks: { 登记: 'BLOCK', 材料: 'PASS' } } });
  assert.deepEqual(row.details, undefined);
  assert.equal(row.stage, 'extrude');
  assert.equal(row.step, 4);
  assert.equal(row.hresult, '0x80004005');
  assert.equal(row.command, 'c-1');
  assert.deepEqual(row.checks, { '登记': 'BLOCK' });
  assert.equal(row.at, new Date(1795670020500).toISOString());
  assert.equal(row.severity, 'error');
});

test('normalizeEvent rejects non-events and invalid codes', () => {
  assert.equal(normalizeEvent(null), undefined ?? null);
  assert.equal(normalizeEvent({ seq: 0, code: 'A_B' }), null);
  assert.equal(normalizeEvent({ seq: 4, code: 'not a code' }), null);
  assert.equal(normalizeEvent({ seq: 4, code: 'lowercase' }), null);
});

test('normalizeEvents sorts, de-duplicates and never throws on junk', () => {
  const rows = normalizeEvents([REJECT, { junk: true }, START, REJECT, FAIL]);
  assert.deepEqual(rows.map((r) => r.seq), [1, 2, 3]);
  assert.deepEqual(normalizeEvents(undefined), []);
  assert.deepEqual(normalizeEvents('nope'), []);
});

test('severity buckets: info / warn / error', () => {
  assert.equal(severityOf('MONITOR_STARTED_COM'), 'info');
  assert.equal(severityOf('HOST_ATTACHED'), 'info');
  assert.equal(severityOf('HOST_HEARTBEAT_EXPIRED'), 'warn');
  assert.equal(severityOf('COM_CALL_FAILED'), 'error');
  assert.equal(severityOf('SAVE_NOT_CONFIRMED'), 'error');
  assert.equal(severityOf('REVIEW_BLOCKED'), 'error');
  // 未来新增、本模型还不认识的机器码：标 warn（要人看一眼），不擅自标红
  assert.equal(severityOf('SOMETHING_NEW'), 'warn');
  assert.equal(severityOf('not-a-code'), 'info');
});

test('cleanText drops paths and credential-looking strings', () => {
  assert.equal(cleanText('建模'), '建模');
  assert.equal(cleanText('C:/raw/x.SLDPRT'), '');
  assert.equal(cleanText('Bearer abcdef'), '');
  assert.equal(cleanText('token: xyz'), '');
  assert.equal(cleanText('x'.repeat(500)), '');
  assert.equal(machineCode(' COM_TIMEOUT '), 'COM_TIMEOUT');
});

// ── 状态 ────────────────────────────────────────────────────────────────
test('buildStatus levels', () => {
  assert.equal(buildStatus({ ok: false, code: 'MONITOR_UNAVAILABLE' }).level, 'unavailable');
  assert.equal(buildStatus({ ok: true, events: [] }).level, 'empty');
  assert.equal(buildStatus({ ok: true, events: normalizeEvents([START]) }).level, 'ok');
  assert.equal(buildStatus({ ok: true, events: normalizeEvents([START, { ...REJECT, code: 'HOST_HEARTBEAT_EXPIRED' }]) }).level, 'warn');
  assert.equal(buildStatus({ ok: true, events: normalizeEvents([START, FAIL]) }).level, 'error');
  const status = buildStatus({ ok: true,
    events: normalizeEvents([START, FAIL, { ...FAIL, seq: 4, code: 'SAVE_FAILED' }]),
    incomplete: true });
  assert.equal(status.detail.includes('失败 2'), true, status.detail);
  assert.equal(status.detail.includes('可能不全'), true, status.detail);
  // STALE_GENERATION_OR_PART 不在"失败族"后缀里 → 归 warn（要人看一眼，不擅自标红）
  assert.deepEqual(countSeverity(normalizeEvents([START, REJECT, FAIL])), { total: 3, error: 1, warn: 1, info: 1 });
});

// ── 查询串 ──────────────────────────────────────────────────────────────
test('parseMonitorLogQuery defaults and validation', () => {
  assert.deepEqual(parseMonitorLogQuery(query('')), { ok: true, since_seq: 0, limit: DEFAULT_LIMIT, download: false, task: null });
  assert.deepEqual(parseMonitorLogQuery(query('since_seq=12&limit=5')),
    { ok: true, since_seq: 12, limit: 5, download: false, task: null });
  assert.equal(parseMonitorLogQuery(query('download=1')).download, true);
  assert.equal(parseMonitorLogQuery(query('limit=' + MAX_LIMIT)).ok, true);
  assert.equal(parseMonitorLogQuery(query('limit=' + (MAX_LIMIT + 1))).code, 'DIAGNOSTICS_LIMIT_INVALID');
  assert.equal(parseMonitorLogQuery(query('limit=0')).code, 'DIAGNOSTICS_LIMIT_INVALID');
  assert.equal(parseMonitorLogQuery(query('limit=abc')).code, 'DIAGNOSTICS_LIMIT_INVALID');
  assert.equal(parseMonitorLogQuery(query('since_seq=-1')).code, 'DIAGNOSTICS_CURSOR_INVALID');
  assert.equal(parseMonitorLogQuery(query('since_seq=1.5')).code, 'DIAGNOSTICS_CURSOR_INVALID');
  assert.equal(parseMonitorLogQuery(query('task=..%2Fetc')).code, 'TASK_SCOPE_INVALID');
  assert.equal(parseMonitorLogQuery(query('task=T-1')).task, 'T-1');
});

// ── 模型 ────────────────────────────────────────────────────────────────
test('buildMonitorLog success carries events, cursor and no warnings', () => {
  const model = buildMonitorLog({
    ok: true, task: 'A', scope: 'task', attached: true,
    events: [START, REJECT, FAIL], since_seq: 0, limit: 100,
    next_since_seq: 3, latest_seq: 3, has_more: false, incomplete: false,
    observed_at: '2026-11-26T00:00:00.000Z',
  });
  assert.equal(model.ok, true);
  assert.equal(model.status.level, 'error');
  assert.equal(model.events.length, 3);
  assert.equal(model.cursor.next_since_seq, 3);
  assert.equal(model.cursor.latest_seq, 3);
  assert.deepEqual(model.warnings, []);
  assert.equal(model.status.task, 'A');
});

test('degradation is explicit, never an empty "no logs" result', () => {
  const model = buildMonitorLog({
    ok: false, code: 'MONITOR_NOT_ATTACHED', error: '监控器尚未 attach(drained)',
    hint: '先完成接管', scope: 'unavailable', events: [],
  });
  assert.equal(model.ok, false);
  assert.equal(model.degraded, true);
  assert.equal(model.code, 'MONITOR_NOT_ATTACHED');
  assert.equal(model.status.level, 'unavailable');
  assert.equal(model.status.label, '诊断日志不可用');
  assert.equal(model.events.length, 0);
  assert.ok(model.warnings.some((w) => w.includes('MONITOR_NOT_ATTACHED')));
  assert.ok(model.warnings.includes('先完成接管'));
});

test('global scope and pagination warnings', () => {
  const model = buildMonitorLog({
    ok: true, task: 'A', scope: 'task', attached: true, has_more: true,
    events: [START], since_seq: 0, limit: 1, next_since_seq: 1, latest_seq: 9,
  });
  assert.ok(model.warnings.some((w) => w.includes('next_since_seq=1')));
  const globalModel = buildMonitorLog({ ok: true, task: null, scope: 'global', events: [START] });
  assert.equal(globalModel.status.scope, 'global');
  assert.equal(globalModel.status.level, 'ok');
});

test('export filename is ascii-safe', () => {
  assert.match(exportFilename('A'), /^monitor-log-A-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.json$/);
  assert.match(exportFilename(null), /^monitor-log-global-/);
  assert.match(exportFilename('..\/x'), /^monitor-log-global-/);
});

// ── bridge 降级分类（不可达 ≠ 未接管）────────────────────────────────────
test('bridge: monitor down reports MONITOR_UNAVAILABLE, not NOT_ATTACHED', async () => {
  const { MonitorBridge } = await import('../lib/sw-monitor.js');
  const { mkdtemp, writeFile, rm } = await import('node:fs/promises');
  const os = await import('node:os');
  const nodePath = (await import('node:path')).default;
  const dir = await mkdtemp(nodePath.join(os.tmpdir(), 'mon-diag-'));
  try {
    await writeFile(nodePath.join(dir, 'sw_monitor.json'), JSON.stringify({
      enabled: true, url: 'http://127.0.0.1:1', tokenFile: nodePath.join(dir, 'host.token'),
    }));
    await writeFile(nodePath.join(dir, 'host.token'), 't'.repeat(48) + '\n');
    const bridge = new MonitorBridge({ stateDir: () => dir });
    const r = await bridge.readDiagnostics({ since_seq: 0, limit: 10, task: 'A' });
    assert.equal(r.ok, false);
    assert.equal(r.code, 'MONITOR_UNAVAILABLE');
    assert.equal(r.events.length, 0);
    bridge.stopHeartbeat();
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('bridge: missing config reports MONITOR_NOT_ENABLED', async () => {
  const { MonitorBridge } = await import('../lib/sw-monitor.js');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const os = await import('node:os');
  const nodePath = (await import('node:path')).default;
  const dir = await mkdtemp(nodePath.join(os.tmpdir(), 'mon-diag-'));
  try {
    const bridge = new MonitorBridge({ stateDir: () => dir });
    const r = await bridge.readDiagnostics({ since_seq: 0, limit: 10 });
    assert.equal(r.ok, false);
    assert.equal(r.code, 'MONITOR_NOT_ENABLED');
    assert.equal(r.scope, 'global');
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

// ── 路由 ────────────────────────────────────────────────────────────────
test('route: bridge down degrades with a code', async () => {
  const handler = createMonitorLogHandler({ bridge: null, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: '' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, false);
  assert.equal(res.body.code, 'MONITOR_BRIDGE_DOWN');
  assert.equal(res.body.status.level, 'unavailable');
});

test('route: binds task from host and forwards paging', async () => {
  const bridge = fakeBridge({ task: 'A', pages: [{
    ok: true, events: [START, REJECT], next_since_seq: 2, latest_seq: 2, has_more: false,
  }] });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: 'since_seq=0&limit=20' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.events.length, 2);
  assert.equal(res.body.cursor.limit, 20);
  assert.deepEqual(bridge.calls, [{ since_seq: 0, limit: 20, task: 'A' }]);
});

test('route: task mismatch is rejected with 409 instead of silently rereading', async () => {
  const bridge = fakeBridge({ task: 'B' });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: 'task=A' }, res);
  assert.equal(res.statusCode, 409);
  assert.equal(res.body.code, 'TASK_CHANGED');
  assert.equal(bridge.calls.length, 0);
});

test('route: no host task → global scope read, still ok', async () => {
  const bridge = fakeBridge({ task: null, pages: [{ ok: true, events: [START], next_since_seq: 1, latest_seq: 1 }] });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: '' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.status.scope, 'global');
  assert.equal(bridge.calls[0].task, null);
});

test('route: invalid cursor is 400 with code', async () => {
  const bridge = fakeBridge({ task: 'A' });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: 'since_seq=oops' }, res);
  assert.equal(res.statusCode, 400);
  assert.equal(res.body.code, 'DIAGNOSTICS_CURSOR_INVALID');
});

test('route: accepts a bridge getter (host keeps it late-bound)', async () => {
  const bridge = fakeBridge({ task: 'A', pages: [{ ok: true, events: [START], next_since_seq: 1, latest_seq: 1 }] });
  const handler = createMonitorLogHandler({ bridge: () => bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: '' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.events.length, 1);
});

test('route: a getter returning null degrades instead of throwing', async () => {
  const handler = createMonitorLogHandler({ bridge: () => null, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: '' }, res);
  assert.equal(res.body.code, 'MONITOR_BRIDGE_DOWN');
});

test('route: POST is 405', async () => {
  const handler = createMonitorLogHandler({ bridge: fakeBridge() , send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'POST', qs: '' }, res);
  assert.equal(res.statusCode, 405);
  assert.equal(res.headers.allow, 'GET');
});

test('route: download=1 pages to the end and sets attachment', async () => {
  const bridge = fakeBridge({ task: 'A', pages: [
    { ok: true, events: [START, REJECT], next_since_seq: 2, latest_seq: 3, has_more: true },
    { ok: true, events: [FAIL], next_since_seq: 3, latest_seq: 3, has_more: false },
  ] });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: 'download=1' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.events.length, 3);
  assert.equal(res.body.cursor.next_since_seq, 3);
  assert.match(res.headers['content-disposition'], /^attachment; filename="monitor-log-A-.*\.json"$/);
  assert.deepEqual(bridge.calls.map((c) => c.since_seq), [0, 2]);
});

test('route: download fails fast on a degraded page (no fake partial success)', async () => {
  const bridge = fakeBridge({ task: 'A', pages: [
    { ok: true, events: [START], next_since_seq: 1, has_more: true },
    { ok: false, code: 'MONITOR_UNAVAILABLE', error: '端口不通', events: [] },
  ] });
  const handler = createMonitorLogHandler({ bridge, send, queryOf: (req) => query(req.qs) });
  const res = response();
  await handler({ method: 'GET', qs: 'download=1' }, res);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, false);
  assert.equal(res.body.code, 'MONITOR_UNAVAILABLE');
});
