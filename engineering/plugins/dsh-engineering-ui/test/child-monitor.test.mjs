import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { childRunMonitor } from '../lib/run-log/child-monitor.mjs';
import { createRunLogHandler } from '../lib/run-log/run-log-route.mjs';

test('starts with planner, archives settled child output, and keeps tracking another child', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'dsh-child-monitor-'));
  const root = 'root-session', planner = 'planner-session', worker = 'worker-session';
  const at = Date.now();
  const rootLog = { header: { id: root, createdAt: at }, events: [], complete: true };
  const log = { complete: true, inheritedEventCount: 1,
    header: { id: planner, parentSession: root, createdAt: at },
    events: [
      { seq: 0, type: 'assistant/message', time: at, data: { message: {
        source: { kind: 'model' }, content: [{ type: 'text', text: 'inherited content' }],
      } } },
      { seq: 1, type: 'assistant/message', time: at, data: { message: {
        source: { kind: 'model' }, content: [{ type: 'thinking', text: 'internal thought' },
          { type: 'text', text: '分析已完成\napi_key=secret_value' }],
      } } },
      { seq: 2, type: 'job/registered', time: at, data: { jobId: 'job-1' } },
    ] };
  let checks = 0;
  const logs = new Map([[root, rootLog], [planner, log]]);
  const monitor = childRunMonitor({ toolsDir: () => dir, readLog: async id => logs.get(id),
    listChildren: async () => [], warn: () => {},
    service: { ensure: async () => { checks += 1; }, status: () => ({ state: 'ready' }) } });
  try {
    monitor.observe(root, planner, true, '总计子代理（七阶段分析）');
    assert.equal(checks, 1);
    monitor.observe(root, worker, true);
    monitor.observe(root, planner, false);
    await delay(6500);
    const preliminary = await monitor.readArchive(root, planner);
    assert.equal(preliminary?.pending, true);
    assert.deepEqual(monitor.status(root).active, [worker]);
    log.events.push({ seq: 3, type: 'job/settled', time: Date.now(), data: { jobId: 'job-1' } });
    const deadline = Date.now() + 9000;
    let archive;
    do {
      await delay(100);
      archive = await monitor.readArchive(root, planner);
    } while (archive?.pending && Date.now() < deadline);
    assert.equal(archive?.pending, false);
    assert.equal(archive.projection.events[0].output, '分析已完成\napi_key=[已隐藏]');
    assert.doesNotMatch(JSON.stringify(archive), /inherited content|internal thought|secret_value/);
    logs.delete(planner);
    let response;
    const route = createRunLogHandler({ childMonitor: monitor, readSessionLog: async id => logs.get(id),
      listDescendants: async () => [], queryOf: () => new URLSearchParams({ rootSessionId: root, download: '1' }),
      send: (_res, status, body) => { response = { status, body }; } });
    const target = { setHeader() {}, end(body) { response = { status: this.statusCode, body: JSON.parse(body) }; } };
    await route({ method: 'GET' }, target);
    assert.equal(response.status, 200);
    assert.equal(response.body.mode, 'live');
    assert.equal(response.body.agents.find(agent => agent.id === planner)?.activation, 'archived');
  } finally {
    monitor.dispose();
    await rm(dir, { recursive: true, force: true });
  }
});
