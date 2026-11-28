import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRunLogHandler } from '../lib/run-log/run-log-route.mjs';

const specPath = fileURLToPath(new URL('../../dsh-engineering-seven-stage/lib/steps.json', import.meta.url));
const spec = JSON.parse(readFileSync(specPath, 'utf8'));

test('工程流程首页读取七阶段规格与当前会话的已落盘进度', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-workflow-view-'));
  const stateFile = join(dir, 'task.json');
  const oldSpecFile = join(dir, 'older.json');
  writeFileSync(oldSpecFile, JSON.stringify({ ...spec, sha256: 'older-spec' }));
  const state = {
    schema: 'dsh-engineering/seven-stage-state@1', root: 'session-active', taskId: 'task',
    specHash: spec.sha256, phase: 'design', cursor: 0, records: [], history: [],
    requirements: { text: '私有草稿' },
  };
  writeFileSync(stateFile, JSON.stringify(state));
  const route = createRunLogHandler({
    readSessionLog: () => assert.fail('首页不需要读取会话日志'),
    listDescendants: () => assert.fail('首页不需要遍历子代理'),
    stateDirCandidates: () => [dir],
    stepsJsonCandidates: () => [oldSpecFile, specPath],
    queryOf: req => req.query,
    send: (_, status, body) => ({ status, body }),
  });
  const getWorkflow = async rootSessionId => {
    const res = await route({ method: 'GET', query: new URLSearchParams({ rootSessionId, view: 'workflow' }) }, {});
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    return res.body;
  };

  const first = await getWorkflow('session-active');
  assert.equal(first.stages.length, 7);
  assert.equal(first.total, spec.steps.length);
  assert.equal(first.current.id, spec.steps[0].id);
  assert.equal(first.stages[0].steps[0].status, 'active');
  assert.equal(JSON.stringify(first).includes('私有草稿'), false);

  const next = spec.steps.findIndex(step => step.stage === 2);
  state.cursor = next;
  state.records = spec.steps.slice(0, next).map(step => ({ step: step.id }));
  writeFileSync(stateFile, JSON.stringify(state));
  const advanced = await getWorkflow('session-active');
  assert.ok(advanced.stages[0].steps.every(step => step.status === 'complete'));
  assert.equal(advanced.stages[1].steps[0].status, 'active');
  assert.equal(advanced.completed, next);

  state.phase = 'evidence_wait';
  state.cursor = 0;
  state.history = [{ invalidated: state.records }];
  state.records = [];
  writeFileSync(stateFile, JSON.stringify(state));
  const reopened = await getWorkflow('session-active');
  assert.equal(reopened.current.waiting, true);
  assert.equal(reopened.stages[0].steps[1].status, 'revisit');
  assert.equal(reopened.completed, 0);

  const separate = await getWorkflow('session-other');
  assert.equal(separate.taskId, null);
  assert.equal(separate.current, null);
  assert.ok(separate.stages.every(stage => stage.steps.every(step => step.status === 'pending')));
});
