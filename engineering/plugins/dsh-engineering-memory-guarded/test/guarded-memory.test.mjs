import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { applyChange, directChanges, openJournal, snapshot, digest } from '../lib/guarded-memory.js';
import { auditCommit, auditRead } from '../lib/guarded-audit.js';

test('an authoritative revision removes all dependent dimensions and keeps independent values', () => {
  const [change] = directChanges('请把 shaft.diameter 改为 24 mm。');
  assert.deepEqual(directChanges('!尺寸 shaft.diameter = 24 mm'), [change]);
  const graph = {
    'shaft.diameter': { value: '20 mm', parents: [], source: 'user' },
    radius: { value: '10 mm', parents: ['shaft.diameter'], source: 'derived' },
    flange: { value: '40 mm', parents: ['radius'], source: 'derived' },
    height: { value: '8 mm', parents: [], source: 'user' },
  };
  const next = applyChange(graph, change);
  assert.deepEqual(Object.keys(next).sort(), ['height', 'shaft.diameter']);
  assert.equal(next['shaft.diameter'].value, '24 mm');
  assert.throws(() => applyChange(graph, { kind: 'invalidate', id: 'height' }), /USER_CONSTRAINT/);
  assert.throws(() => applyChange(graph, { kind: 'derive', id: 'radius', value: '1 mm', parents: ['flange'] }), /DEPENDENCY/);
});

test('restart keeps the guard closed and the audit distinguishes a prepared candidate from a durable replacement', () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-guarded-'));
  try {
    const j = openJournal(dir, 'A');
    const pending = { id: 'attempt', graph: {}, source: j.backup({ messages: ['旧上下文'] }) };
    j.save({ ...j.state(), pending });
    assert.equal(openJournal(dir, 'A').state().pending.id, 'attempt');
    assert.equal(openJournal(dir, 'B').state().pending, null);
    const body = snapshot(pending.id, {}, []);
    const cp = { id: 'message', source: { kind: 'compact-checkpoint', compactionId: 'host' }, content: [
      { type: 'text', text: 'frame' }, { type: 'text', text: body }, { type: 'text', text: 'end' },
    ] };
    const events = [
      { seq: 1, type: 'compaction/start', data: { compactionId: 'host' } },
      { seq: 2, type: 'compaction/summary', data: { compactionId: 'host', summary: [cp.content[1]] } },
      { seq: 3, type: 'user/message', data: cp, surfaceOp: { op: 'replace' } },
      { seq: 4, type: 'compaction/end', data: { compactionId: 'host' } },
    ];
    const prepared = { ...pending, hostId: 'host', candidate: j.backup({ body, graph: {} }) };
    assert.throws(() => auditCommit(j, prepared, events.slice(0, 3), [3]), /COMMIT/);
    const pin = auditCommit(j, prepared, events, [3]);
    assert.equal(pin.hash, digest(cp.content));
    assert.doesNotThrow(() => auditRead(pin, [cp]));
    assert.throws(() => auditRead(pin, [{ ...cp, content: cp.content.slice(1) }]), /HASH/);
    writeFileSync(j.backupPath(prepared.candidate), '{}');
    assert.throws(() => auditCommit(j, prepared, events, [3]), /HASH/);
  } finally {
    if (/^dsh-guarded-[^\\/]+$/u.test(relative(tmpdir(), dir))) rmSync(dir, { recursive: true, force: true });
  }
});

test('a corrupt agent record restores a verified prior revision with the gate still closed', () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-guarded-'));
  try {
    const journal = openJournal(dir, 'A');
    const hash = journal.backup({ kind: 'test' });
    journal.save(journal.state());
    journal.save({ ...journal.state(), needsDimensions: true });
    writeFileSync(join(dirname(journal.backupPath(hash)), 'agent.json'), Buffer.from([0xff]));
    assert.equal(journal.state().pending.failure, 'MEMORY_AGENT_RECORD_RESTORED');
    assert.doesNotThrow(() => journal.load(hash));
  } finally {
    if (/^dsh-guarded-[^\\/]+$/u.test(relative(tmpdir(), dir))) rmSync(dir, { recursive: true, force: true });
  }
});
