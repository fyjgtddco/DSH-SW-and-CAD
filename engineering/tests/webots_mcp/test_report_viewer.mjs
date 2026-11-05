// Node's runner exercises the shipped client components without launching DSH.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

let client;
let slots = [], cursor = 0;
let effectsEnabled = false, pendingEffects = [];
const React = {
  createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
  useState: value => { const at = cursor++; if (!(at in slots)) slots[at] = value;
    return [slots[at], next => { slots[at] = typeof next === 'function' ? next(slots[at]) : next; }]; },
  useEffect: (run, deps) => {
    if (!effectsEnabled) return;
    const at = cursor++, previous = slots[at];
    if (!previous || deps.some((value, i) => value !== previous.deps[i])) pendingEffects.push(() => {
      previous?.cleanup?.(); slots[at] = { deps, cleanup: run() };
    });
  }, useRef: value => { const at = cursor++; return slots[at] ||= { current: value }; },
};
const context = vm.createContext({ console, localStorage: { getItem: () => null },
  window: { __ModuleLoader__: { load: spec => { client = spec.factory(name => {
    if (name === 'react') return React;
    throw new Error(`Unexpected module ${name}`);
  }); } } },
});

test('unauthorized context clears report access instead of inventing a room', async () => {
  assert.equal(typeof client.EngWebotsPanel, 'function', 'report reader is missing');
  slots = []; const calls = [];
  context.fetch = async url => { calls.push(url); return { ok: false, json: async () => ({ ok: false,
    error_code: 'CONTEXT_UNAVAILABLE', error: 'No live room binding' }) }; };
  function render() { cursor = 0; return client.EngWebotsPanel({ sessionId: 'agent-1' }); }
  let tree = render();
  await nodes(tree, node => node.type === 'button' && text(node) === '读取仿真上下文')[0].props.onClick();
  tree = render();
  assert.ok(text(tree).includes('CONTEXT_UNAVAILABLE'));
  assert.ok(nodes(tree, node => node.type === 'button' && text(node) === '读取报告')[0].props.disabled);
  assert.equal(calls.length, 1);
  assert.equal(calls[0], '/dsh-engineering-ui/webots-context?sessionId=agent-1');
});

test('log pagination forwards byte cursor/hash and discards a stale snapshot', async () => {
  assert.equal(typeof client.EngWebotsPanel, 'function', 'log reader is missing');
  slots = []; const calls = [];
  context.fetch = async url => { calls.push(url); const query = new URL(url, 'http://localhost');
    if (query.pathname.endsWith('webots-context')) return { ok: true, json: async () => ({ ok: true,
      task_id: 'task-1', room_id: 'room-1', core_ready: false, execution_state: 'not_started', stale: true }) };
    if (query.searchParams.get('offset') === '7') return { ok: false, json: async () => ({ ok: false,
      error_code: 'EVIDENCE_INVALID', error: 'Log snapshot changed' }) };
    return { ok: true, json: async () => ({ ok: true, data: { path: 'logs/worker.log', text: '中文\n',
      offset: 0, next_offset: 7, size_bytes: 20, sha256: 'b'.repeat(64), eof: false } }) };
  };
  function render() { cursor = 0; return client.EngWebotsPanel({ sessionId: 'agent-1' }); }
  let tree = render();
  await nodes(tree, node => node.type === 'button' && text(node) === '读取仿真上下文')[0].props.onClick();
  tree = render();
  nodes(tree, node => node.type === 'input' && node.props['aria-label'] === '运行ID')[0].props.onChange({ target: { value: 'run-1' } });
  tree = render();
  await nodes(tree, node => node.type === 'button' && text(node) === '从头读取日志')[0].props.onClick();
  tree = render();
  assert.ok(text(tree).includes('中文'));
  await nodes(tree, node => node.type === 'button' && text(node) === '下一页日志')[0].props.onClick();
  const last = new URL(calls.at(-1), 'http://localhost');
  assert.equal(last.searchParams.get('offset'), '7');
  assert.equal(last.searchParams.get('expected_sha256'), 'b'.repeat(64));
  assert.equal(last.searchParams.get('limit'), '16384');
  tree = render();
  assert.ok(text(tree).includes('EVIDENCE_INVALID'));
  assert.ok(!text(tree).includes('中文'));
  assert.ok(nodes(tree, node => node.type === 'button' && text(node) === '下一页日志')[0].props.disabled);
});
vm.runInContext(readFileSync(new URL('../../plugins/dsh-engineering-ui/lib/client.js', import.meta.url), 'utf8'), context);
function text(node) {
  if (node === null || node === undefined || node === false) return '';
  if (typeof node !== 'object') return String(node);
  if (typeof node.type === 'function') return text(node.type(node.props));
  return node.children.map(text).join(' ');
}
function nodes(node, predicate) {
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...node.children.flatMap(child => nodes(child, predicate))];
}
const report = {
  schema_version: 1, report_type: 'experiment_report', run_id: 'run-1', task_id: 'task-1', room_id: 'room-1',
  provenance: 'fake', status: 'unsupported', source_revision: null, created_at: '2026-10-30T00:00:00Z',
  missing_information: ['physical_validation'],
  evidence: Array.from({ length: 23 }, (_, i) => ({ path: `evidence/item-${i}`, sha256: 'a'.repeat(64),
    size_bytes: i, provenance: 'synthetic', kind: 'sample', producer: null })),
  data: { verdict: { result: 'unknown' }, issues: [{ evidence_level: 'suspected',
    candidate_parts: [{ instance_path: 'arm/bolt-1' }, { instance_path: 'arm/bolt-2' }],
    missing_information: ['unique_mapping'], message: '<script>not HTML</script>' }] },
};

test('unknown/fake/stale snapshots cannot render readiness or physical success', () => {
  assert.equal(typeof client.EngWebotsSnapshot, 'function', 'simulation snapshot view is missing');
  const rendered = text(client.EngWebotsSnapshot({ snapshot: { provenance: 'fake', core_ready: false,
    execution_state: 'unknown', job_state: 'READY', stale: true, snapshot_at: null, unsupported: ['real-worker'] } }));
  for (const marker of ['fake', 'unknown', 'stale', 'unsupported', '未实现', '禁止盲重发']) assert.ok(rendered.includes(marker), marker);
  assert.ok(!rendered.includes('仿真已就绪'));
  assert.ok(text(client.EngWebotsSnapshot({ snapshot: null })).includes('未执行'));
});

test('report retains multiple candidate identities, evidence grade and unknown physical verdict', () => {
  assert.equal(typeof client.EngWebotsReport, 'function', 'owned report view is missing');
  const tree = client.EngWebotsReport({ report, page: 0, onPage: () => {} });
  const rendered = text(tree);
  for (const marker of ['fake', 'unsupported', 'suspected', 'arm/bolt-1', 'arm/bolt-2', '候选', 'unknown', 'physical_validation']) assert.ok(rendered.includes(marker), marker);
  assert.equal(nodes(tree, node => node.type === 'script').length, 0);
  assert.ok(rendered.includes('<script>not HTML</script>'));
});

test('evidence controls expose bounded non-overlapping pages including final item', () => {
  assert.equal(typeof client.EngWebotsReport, 'function', 'evidence pagination is missing');
  let next = null;
  const first = client.EngWebotsReport({ report, page: 0, onPage: value => { next = value; } });
  const firstRows = nodes(first, node => node.props['data-webots-evidence']);
  assert.equal(firstRows.length, 20);
  assert.ok(text(firstRows[0]).includes('item-0'));
  assert.ok(text(firstRows[19]).includes('item-19'));
  nodes(first, node => node.type === 'button' && text(node) === '下一页证据')[0].props.onClick();
  assert.equal(next, 1);
  const last = client.EngWebotsReport({ report, page: 1, onPage: () => {} });
  assert.equal(nodes(last, node => node.props['data-webots-evidence']).length, 3);
  assert.ok(text(last).includes('item-22'));
  assert.equal(nodes(last, node => node.type === 'button' && text(node) === '下一页证据')[0].props.disabled, true);
});

test('existing Webots connection row labels process availability without claiming a simulation connection', async () => {
  slots = [];
  context.fetch = async () => ({ json: async () => ({ ok: true,
    state: { webots: { connected: true, running: true, checked_at: '2026-10-30' } } }) });
  function render() { cursor = 0; return client.EngConnRow({ target: 'webots' }); }
  let tree = render();
  nodes(tree, node => node.type === 'button' && text(node) === '连接')[0].props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  tree = render();
  assert.ok(text(tree).includes('安装/进程可用'));
  assert.ok(text(tree).includes('仿真未确认'));
  assert.ok(!text(tree).includes('已连接'));
});

test('revoked live binding removes previously authorized context and disables reading', async () => {
  slots = [];
  context.fetch = async url => url.includes('webots-context') ? { ok: true, json: async () => ({ ok: true,
    task_id: 'task-1', room_id: 'room-1', core_ready: false, execution_state: 'not_started', stale: true }) }
    : { ok: false, status: 403, json: async () => ({ ok: false, error_code: 'CONTEXT_UNAVAILABLE' }) };
  function render() { cursor = 0; return client.EngWebotsPanel({ sessionId: 'agent-1' }); }
  await nodes(render(), node => node.type === 'button' && text(node) === '读取仿真上下文')[0].props.onClick();
  nodes(render(), node => node.type === 'input' && node.props['aria-label'] === '运行ID')[0].props.onChange({ target: { value: 'run-1' } });
  await nodes(render(), node => node.type === 'button' && text(node) === '读取报告')[0].props.onClick();
  const tree = render();
  assert.ok(nodes(tree, node => node.type === 'button' && text(node) === '读取报告')[0].props.disabled);
  assert.ok(!text(tree).includes('任务： task-1'));
});

test('changing run while a read is pending cannot render the old run response', async () => {
  slots = []; let finish;
  context.fetch = async url => url.includes('webots-context') ? { ok: true, json: async () => ({ ok: true,
    task_id: 'task-1', room_id: 'room-1', core_ready: false, execution_state: 'not_started', stale: true }) }
    : await new Promise(resolve => { finish = () => resolve({ ok: true, json: async () => ({ ok: true, data: report }) }); });
  function render() { cursor = 0; return client.EngWebotsPanel({ sessionId: 'agent-1' }); }
  const button = (tree, label) => nodes(tree, node => node.type === 'button' && text(node) === label)[0];
  const input = tree => nodes(tree, node => node.type === 'input' && node.props['aria-label'] === '运行ID')[0];
  await button(render(), '读取仿真上下文').props.onClick();
  input(render()).props.onChange({ target: { value: 'run-1' } });
  const pending = button(render(), '读取报告').props.onClick();
  input(render()).props.onChange({ target: { value: 'run-2' } });
  finish(); await pending;
  assert.ok(!text(render()).includes('physical_validation'));
});

test('session effect invalidates a pending old-session response and cleanup invalidates unmounted reads', async () => {
  slots = []; effectsEnabled = true; pendingEffects = []; let finish; let active = 'agent-1';
  context.fetch = async url => url.includes('webots-context') ? { ok: true, json: async () => ({ ok: true,
    task_id: 'task-1', room_id: 'room-1', core_ready: false, execution_state: 'not_started', stale: true }) }
    : await new Promise(resolve => { finish = () => resolve({ ok: true, json: async () => ({ ok: true, data: report }) }); });
  function render() { cursor = 0; const tree = client.EngWebotsPanel({ sessionId: active });
    const effects = pendingEffects; pendingEffects = []; effects.forEach(run => run()); return tree; }
  const button = (tree, label) => nodes(tree, node => node.type === 'button' && text(node) === label)[0];
  const input = tree => nodes(tree, node => node.type === 'input' && node.props['aria-label'] === '运行ID')[0];
  try {
    render(); await button(render(), '读取仿真上下文').props.onClick();
    input(render()).props.onChange({ target: { value: 'run-1' } });
    const first = button(render(), '读取报告').props.onClick();
    active = 'agent-2'; render(); finish(); await first;
    assert.ok(!text(render()).includes('physical_validation'));
    assert.ok(button(render(), '读取报告').props.disabled);
    await button(render(), '读取仿真上下文').props.onClick();
    const second = button(render(), '读取报告').props.onClick();
    slots.forEach(value => value?.cleanup?.());
    const before = JSON.stringify(slots);
    finish(); await second;
    assert.equal(JSON.stringify(slots), before, 'unmounted request updated component state');
  } finally { effectsEnabled = false; pendingEffects = []; }
});
