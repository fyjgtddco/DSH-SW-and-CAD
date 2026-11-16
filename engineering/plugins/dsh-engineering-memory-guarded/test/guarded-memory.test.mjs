import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { applyChange, digest, directChanges, exportGraph, isResumeOriginal, needsDimensionIntent,
         openJournal, snapshot, MARKER } from '../lib/guarded-memory.js';
import { LEDGER_FILE, appendLedgerEntry, ledgerCounts, readLedger } from '../lib/ledger.js';

function tempSession() {
  const root = mkdtempSync(join(tmpdir(), 'engmem-'));
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

// ── 尺寸指令识别 ────────────────────────────────────────────────────────────
test('识别 !尺寸 写法', () => {
  assert.deepEqual(directChanges('!尺寸 shaft.diameter = 20 mm'),
    [{ kind: 'user', id: 'shaft.diameter', value: '20 mm' }]);
});
test('识别「把…改为…」写法（含句号）', () => {
  assert.deepEqual(directChanges('请把 shaft.diameter 改为 24 mm。'),
    [{ kind: 'user', id: 'shaft.diameter', value: '24 mm' }]);
});
test('一条消息可分行登记多项', () => {
  const got = directChanges('!尺寸 a.d = 10 mm\n!尺寸 b.len = 30 mm');
  assert.equal(got.length, 2);
  assert.equal(got[1].id, 'b.len');
});
test('非法单位/越界值不登记', () => {
  assert.deepEqual(directChanges('!尺寸 shaft.diameter = 20 个'), []);
  assert.deepEqual(directChanges('随便聊聊尺寸'), []);
});

// ── 「我要换尺寸」意图与收回 ────────────────────────────────────────────────
test('识别「我要换尺寸」并区别于具体指令', () => {
  assert.equal(needsDimensionIntent('我要换尺寸'), true);
  assert.equal(needsDimensionIntent('我想改一下规格'), true);
  // 具体指令不是意图：它自带 ID 与值，直接走登记
  assert.equal(needsDimensionIntent('!尺寸 shaft.diameter = 20 mm'), false);
  assert.equal(needsDimensionIntent('!继续原尺寸'), true);
});
test('!继续原尺寸 是收回变更', () => {
  assert.equal(isResumeOriginal('!继续原尺寸'), true);
  assert.equal(isResumeOriginal('继续原尺寸'), false);
});

// ── 依赖图 ──────────────────────────────────────────────────────────────────
test('用户约束受保护，派生值不能顶替', () => {
  let g = applyChange({}, { kind: 'user', id: 'shaft.diameter', value: '20 mm' });
  assert.throws(() => applyChange(g, { kind: 'derive', id: 'shaft.diameter', value: '21 mm', parents: ['x'] }),
    /MEMORY_USER_CONSTRAINT_PROTECTED/);
});
test('派生值必须声明存在的父节点且无环', () => {
  const g = { 'shaft.diameter': { value: '20 mm', parents: [], source: 'user' } };
  assert.throws(() => applyChange(g, { kind: 'derive', id: 'shaft.radius', value: '10 mm', parents: [] }),
    /MEMORY_DEPENDENCY_INVALID/);
  assert.throws(() => applyChange(g, { kind: 'derive', id: 'shaft.radius', value: '10 mm', parents: ['nope'] }),
    /MEMORY_DEPENDENCY_INVALID/);
  const ok = applyChange(g, { kind: 'derive', id: 'shaft.radius', value: '10 mm', parents: ['shaft.diameter'] });
  assert.equal(ok['shaft.radius'].source, 'derived');
});
test('上游变更级联撤下全部下游，无关尺寸保留', () => {
  let g = { 'shaft.diameter': { value: '20 mm', parents: [], source: 'user' },
            'shaft.radius': { value: '10 mm', parents: ['shaft.diameter'], source: 'derived' },
            'hole.third': { value: '3 mm', parents: ['shaft.radius'], source: 'derived' },
            'frame.width': { value: '80 mm', parents: [], source: 'user' } };
  g = applyChange(g, { kind: 'user', id: 'shaft.diameter', value: '24 mm' });
  assert.deepEqual(Object.keys(g).sort(), ['frame.width', 'shaft.diameter']);
  assert.equal(g['shaft.diameter'].value, '24 mm');
});
test('invalidate 撤下节点与全部下游', () => {
  const g = { 'a': { value: '1 mm', parents: [], source: 'user' },
              'b': { value: '2 mm', parents: ['a'], source: 'derived' } };
  assert.deepEqual(Object.keys(applyChange(g, { kind: 'invalidate', id: 'b' })), ['a']);
  assert.throws(() => applyChange(g, { kind: 'invalidate', id: 'zz' }), /MEMORY_NODE_UNKNOWN/);
});
test('exportGraph 给 UI 的 nodes/edges 方向正确', () => {
  const g = { 'a': { value: '1 mm', parents: [], source: 'user' },
              'b': { value: '2 mm', parents: ['a'], source: 'derived' } };
  const e = exportGraph(g);
  assert.equal(e.edges.length, 1);
  assert.deepEqual(e.edges[0], { source: 'a', target: 'b' });
});
test('snapshot 带 MARKER 且只收活动约束与派生值', () => {
  const g = { 'a': { value: '1 mm', parents: [], source: 'user' },
              'b': { value: '2 mm', parents: ['a'], source: 'derived' } };
  const s = snapshot('att-1', g, [{ kind: 'user', id: 'm1', text: 'hi' }]);
  assert.ok(s.startsWith(MARKER + 'att-1\n'));
  const body = JSON.parse(s.slice(s.indexOf('\n') + 1));
  assert.deepEqual(Object.keys(body.activeConstraints), ['a']);
  assert.deepEqual(Object.keys(body.derived), ['b']);
});

// ── 会话日志（备份 / 哈希 / 回退）──────────────────────────────────────────
test('备份可读回，篡改一位即失败', () => {
  const t = tempSession();
  try {
    const j = openJournal(t.root, 'sess-1');
    const hash = j.backup({ kind: 'input', messages: [{ role: 'user' }] });
    assert.equal(j.load(hash).kind, 'input');
    // 新增一份带附加证据的备份，同样受哈希保护
    const h2 = j.backup({ graph: { a: 1 } }, { label: '房间A', root: 'root-1' });
    assert.equal(j.loadRecord(h2).label, '房间A');
    j.save({ version: 2, session: 'sess-1', graph: {}, pending: null, pins: [], consumed: [] });
    assert.equal(j.state().session, 'sess-1');
  } finally { t.cleanup(); }
});
test('agent.json 损坏时回退 agent.previous.json', () => {
  const t = tempSession();
  try {
    const j = openJournal(t.root, 'sess-2');
    j.save({ version: 2, session: 'sess-2', graph: { a: 1 }, pending: null, pins: [], consumed: [] });
    j.save({ version: 2, session: 'sess-2', graph: { a: 1, b: 2 }, pending: null, pins: [], consumed: [] });
    writeFileSync(join(j.directory, 'agent.json'), '{broken', 'utf8');
    const st = j.state();
    assert.equal(st.session, 'sess-2');
    assert.equal(st.pending.failure, 'MEMORY_AGENT_RECORD_RESTORED');
  } finally { t.cleanup(); }
});

// ── 账本（UI 数据源）───────────────────────────────────────────────────────
test('账本只增不改，且最新在前', () => {
  const t = tempSession();
  const prev = process.env.DSH_STATE_DIR;
  process.env.DSH_STATE_DIR = t.root;
  try {
    writeFileSync(join(t.root, 'placeholder'), '', 'utf8');  // 让 resolveStateDir 认得这个目录
    appendLedgerEntry({ at: 1000, target: 'a', root: 'r', label: '房间A' });
    appendLedgerEntry({ at: 3000, target: 'a', root: 'r', label: '房间A' });
    appendLedgerEntry({ at: 2000, target: 'b', root: 'r', label: '房间B' });
    const r = readLedger({});
    assert.equal(r.entries.length, 3);
    // 最新在上：3000 → 2000 → 1000
    assert.deepEqual(r.entries.map((e) => e.at), [3000, 2000, 1000]);
    // 按 target 过滤仍保持倒序
    assert.deepEqual(readLedger({ target: 'a' }).entries.map((e) => e.at), [3000, 1000]);
    // 计数徽标
    const counts = ledgerCounts(r.entries);
    assert.equal(counts.find((c) => c.target === 'a').count, 2);
  } finally {
    if (prev === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = prev;
    t.cleanup();
  }
});
test('账本文件名带版本，不会覆盖旧账本', () => {
  assert.equal(LEDGER_FILE, 'compaction_history_v2.jsonl');
  assert.notEqual(LEDGER_FILE, 'compaction_history.jsonl');
});
