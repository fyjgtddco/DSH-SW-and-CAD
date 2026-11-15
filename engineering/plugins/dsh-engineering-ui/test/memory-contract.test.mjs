import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { installMemoryTrace, memoryPageRoute } from '../lib/memory-contract.js';

test('paired trace has hashes but no tool payload', async () => {
  const stateDir = await mkdtemp(path.join(os.tmpdir(), 'clawvm-'));
  try {
    let listener;
    const ctx = {on(name, fn) { assert.equal(name, 'tools/execute'); listener = fn; }};
    installMemoryTrace(ctx, () => ({stateDir, task:'T', session:'S'}));
    await listener({name:'read_file', arguments:{value:'SECRET'}}, async () => ({content:'PRIVATE'}));
    const raw = await readFile(path.join(stateDir,'trace','T.jsonl'), 'utf8');
    const rows = raw.trim().split('\n').map(JSON.parse);
    assert.deepEqual(rows.map(r => r.event), ['tool.start', 'tool.result']);
    assert.equal(rows[0].id, rows[1].id);
    assert.match(rows[1].result_sha256, /^[a-f0-9]{64}$/);
    assert.doesNotMatch(raw, /SECRET|PRIVATE/);
  } finally { await rm(stateDir, {recursive:true, force:true}); }
});

test('page route delegates auth to the common guard', async () => {
  let used = false;
  const guard = r => ({...r, handler: async (req, res) =>
    req.allowed ? r.handler(req,res) : { status:403 }});
  const route = memoryPageRoute(guard, (_, status) => ({status}), () => {
    used = true;
    throw new Error('not connected');
  });
  assert.equal((await route.handler({method:'GET'})).status, 403);
  assert.equal(used, false);
  assert.equal((await route.handler({method:'POST',allowed:true})).status, 405);
});
