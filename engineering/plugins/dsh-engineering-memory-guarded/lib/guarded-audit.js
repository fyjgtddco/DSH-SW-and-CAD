import { applyChange, digest, directChanges, MARKER, snapshot } from './guarded-memory.js';

function checkedChange(graph, change) {
  if (change.kind === 'user' && (!change.evidence || !directChanges(change.evidenceText)
    .some(item => item.id === change.id && item.value === change.value))) {
    throw new Error('MEMORY_USER_EVIDENCE_REQUIRED');
  }
  return applyChange(graph, change);
}

export function auditCommit(journal, pending, events, surface) {
  const candidate = journal.load(pending.candidate);
  const input = journal.load(pending.source);
  const before = journal.state();
  const expected = (pending.changes ?? []).reduce(checkedChange, before.graph);
  if (digest(expected) !== digest(pending.graph)) throw new Error('MEMORY_PROTECTED_DATA_CHANGED');
  const { pages } = JSON.parse(candidate.body.slice(candidate.body.indexOf('\n') + 1));
  if (candidate.body !== snapshot(pending.id, expected, pages) || digest(candidate.graph) !== digest(expected)) {
    throw new Error('MEMORY_PROTECTED_DATA_CHANGED');
  }
  const matches = event => event.data?.compactionId === pending.hostId;
  const start = events.find(event => event.type === 'compaction/start' && matches(event));
  const summary = events.find(event => event.type === 'compaction/summary' && matches(event));
  const end = events.find(event => event.type === 'compaction/end' && matches(event));
  const replacement = events.find(event => event.seq === summary?.seq + 1);
  const block = { type: 'text', text: candidate.body };
  if (!start || !summary || !end || 'error' in end.data || start.seq >= summary.seq || end.seq <= replacement?.seq ||
      replacement?.type !== 'user/message' || replacement.surfaceOp?.op !== 'replace' ||
      replacement.data.source?.kind !== 'compact-checkpoint' || replacement.data.source.compactionId !== pending.hostId ||
      !surface.includes(replacement.seq) || digest(summary.data.summary) !== digest([block]) ||
      replacement.data.content?.length !== 3 || digest(replacement.data.content[1]) !== digest(block)) {
    throw new Error('MEMORY_COMMIT_NOT_DURABLE');
  }
  return {
    attempt: pending.id, hostId: pending.hostId, message: replacement.data.id, seq: replacement.seq,
    hash: digest(replacement.data.content), source: pending.source, candidate: pending.candidate,
    shadowed: summary.data.shadowedSeqs ?? [], at: end.time ?? Date.now(),
    inputChars: JSON.stringify(input.messages).length, outputChars: candidate.body.length,
    constraints: Object.values(expected).filter(node => node.source === 'user').length,
    redacted: pages.filter(page => page.kind === 'user-evidence').map(page => page.id),
  };
}

export function auditRead(pins, messages) {
  const allowed = new Map((Array.isArray(pins) ? pins : pins ? [pins] : []).map(pin => [pin.message, pin]));
  for (const message of messages) {
    if (message.source?.kind !== 'compact-checkpoint') continue;
    const pin = allowed.get(message.id);
    const ours = message.content?.some(block => block.type === 'text' && block.text.startsWith(MARKER));
    if (!pin && !ours) continue;
    if (!pin || message.source.compactionId !== pin.hostId || digest(message.content) !== pin.hash) {
      throw new Error('MEMORY_READ_HASH_MISMATCH');
    }
    allowed.delete(message.id);
  }
  if (allowed.size) throw new Error('MEMORY_READ_CHECKPOINT_MISSING');
}

// Separate Cordis plugin. Its commit check uses a new read handle after the
// session flush, independently of the writer's in-memory result.
export function apply(ctx) {
  ctx.provide('engineeringCompactionAudit', Object.freeze({
    async commit(session, journal, pending, signal) {
      await ctx.sessions.flush(session);
      signal?.throwIfAborted();
      const handle = await ctx.sessionPersistence.open(session.id, 'read', { signal });
      try {
        const { events } = await handle.read(undefined, undefined, { signal });
        const pin = auditCommit(journal, pending, events, session.surface.nodes);
        auditRead(pin, session.deriveMessages().filter(message => message.id === pin.message));
        return pin;
      } finally { await handle.close(); }
    },
    bootstrap(journal, pending, hash) {
      const state = journal.state();
      const expected = pending.changes.reduce(checkedChange, state.graph);
      const record = journal.load(hash);
      if (state.pins.length || record.attempt !== pending.id || digest(record.graph) !== digest(expected) ||
          digest(pending.graph) !== digest(expected)) throw new Error('MEMORY_PROTECTED_DATA_CHANGED');
    },
    read: auditRead,
  }));
}
