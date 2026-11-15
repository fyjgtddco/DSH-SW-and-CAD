import { createHash, randomUUID } from 'node:crypto';
import { mkdir, appendFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';

const run = promisify(execFile);
const stable = (value, seen = new Set()) => {
  if (!value || typeof value !== 'object') return typeof value === 'bigint' ? String(value) : value;
  if (seen.has(value)) return '[cycle]';
  seen.add(value);
  try {
    if (Array.isArray(value)) return value.map(v => stable(v, seen));
    return Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k], seen)]));
  } finally { seen.delete(value); }
};
const hash = value => createHash('sha256').update(JSON.stringify(stable(value)) ?? 'null').digest('hex');
let pending = Promise.resolve();

export function appendTrace(stateDir, task, event) {
  if (!/^[a-zA-Z0-9_-]{1,180}$/.test(task || '')) return Promise.reject(new Error('TRACE_TASK_INVALID'));
  const dir = path.join(stateDir, 'trace');
  const next = pending.catch(() => {}).then(async () => {
    await mkdir(dir, { recursive: true });
    await appendFile(path.join(dir, task + '.jsonl'),
      JSON.stringify({ schema: 1, at: Date.now(), ...event, task }) + '\n', 'utf8');
  });
  pending = next;
  return next;
}

export function installMemoryTrace(ctx, identity, onError = console.warn) {
  return ctx.on('tools/execute', async (exec, next) => {
    let record;
    try {
      const { stateDir, task, session } = identity(exec) || {};
      if (stateDir && task && session) {
        const base = { id: randomUUID(), session, tool: String(exec.name),
          args_sha256: hash(exec.arguments) };
        await appendTrace(stateDir, task, { ...base, event: 'tool.start' });
        record = (out, status) => appendTrace(stateDir, task, {
          ...base, event: 'tool.result', status, result_sha256: hash(out),
        });
      }
    } catch (err) { onError('ClawVM trace: ' + err.message); }
    try {
      const result = await next();
      if (record) try { await record(result, result?.isError ? 'error' : 'returned'); }
      catch (err) { onError('ClawVM trace: ' + err.message); }
      return result;
    } catch (err) {
      if (record) try { await record({ name: err?.name, code: err?.code }, 'threw'); }
      catch (logError) { onError('ClawVM trace: ' + logError.message); }
      throw err;
    }
  });
}

export async function memoryCommand({ python, tools, stateDir }, operation, args = []) {
  const { stdout } = await run(python, ['-B', path.join(tools, 'memory_gate.py'), operation,
    '--state-dir', stateDir, ...args], {
    cwd: tools, timeout: 15000, maxBuffer: 1024 * 1024, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  });
  const result = JSON.parse(stdout);
  if (!result.ok) throw new Error(result.error || 'PAGE_CHECK_FAILED');
  return result;
}

export function memoryPageRoute(guardRoute, send, config) {
  return guardRoute({ kind: 'exact', path: '/dsh-engineering-ui/page-table',
    handler: async (req, res) => {
      if (req.method !== 'GET') return send(res, 405, { ok: false, error: 'METHOD_NOT_ALLOWED' });
      try { return send(res, 200, await memoryCommand(config(), 'page-status')); }
      catch (err) { return send(res, 503, { ok: false, error: 'PAGE_CHECK_FAILED' }); }
    } });
}
