import fs from 'node:fs/promises';
import path from 'node:path';
import { createConnection } from 'node:net';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { projectChildRun, validId } from './model.mjs';

// A TCP probe only authorizes a launch attempt. The existing monitor RPC proves readiness.
function portState(port) {
  return new Promise(resolve => {
    const socket = createConnection({ host: '127.0.0.1', port });
    function finish(state) { socket.destroy(); resolve(state); }
    socket.setTimeout(1000, () => finish('timeout'));
    socket.once('connect', () => finish('open'));
    socket.once('error', error => finish(error.code === 'ECONNREFUSED' ? 'closed' : error.code));
  });
}

export function monitorService(bridge, toolsDir) {
  let status = { state: 'idle' };
  let nextAttempt = 0, child = null;
  async function ensure() {
    try {
      const config = bridge?.config();
      if (!config) { status = { state: 'disabled' }; return; }
      const url = new URL(config.url);
      const tokenFile = config.tokenFile;
      if (url.origin !== `http://127.0.0.1:${url.port}` || url.href !== url.origin + '/' ||
          !path.isAbsolute(tokenFile) || path.basename(tokenFile) !== 'host.token') {
        status = { state: 'error', code: 'MONITOR_AUTOSTART_CONFIG_INVALID' };
        return;
      }
      const data = path.dirname(tokenFile);
      const logFile = path.join(data, 'monitor-startup.log');
      const endpoint = await portState(Number(url.port));
      if (endpoint === 'open') {
        const createMonitorHost = await bridge.loadAdapter();
        const host = await createMonitorHost({ url: config.url, tokenFile });
        await host.rpc('diagnostics', { since_seq: 0, limit: 1 }, AbortSignal.timeout(1500));
        status = { state: 'ready' };
        return;
      }
      if (endpoint !== 'closed') {
        status = { state: 'error', code: 'MONITOR_PORT_PROBE_FAILED', detail: endpoint };
        return;
      }
      if (child && status.state === 'starting' && Date.now() >= nextAttempt) {
        status = { state: 'error', code: 'MONITOR_START_NOT_LISTENING', logFile };
      }
      if (child || Date.now() < nextAttempt) return;
      const launch = config.launch;
      if (![launch?.pythonExe, launch?.workspaceRoot, launch?.solidworksExe].every(
        value => typeof value === 'string' && path.isAbsolute(value))) {
        status = { state: 'error', code: 'MONITOR_AUTOSTART_PATHS_MISSING' };
        return;
      }
      const dir = toolsDir();
      const script = path.join(dir, 'sw_monitor', 'monitor.py');
      await Promise.all([fs.access(launch.pythonExe), fs.access(launch.solidworksExe),
        fs.access(launch.workspaceRoot), fs.access(script)]);
      await fs.mkdir(data, { recursive: true });
      const log = await fs.open(logFile, 'a');
      try {
        await log.write(`\n[${new Date().toISOString()}] DSH monitor launch\n`);
        nextAttempt = Date.now() + 30000;
        child = spawn(launch.pythonExe, ['-B', '-u', script, '--data', data, '--workspace-root',
          launch.workspaceRoot, '--port', url.port, '--dsh-tools', dir,
          '--solidworks-exe', launch.solidworksExe], {
          cwd: dir, detached: true, windowsHide: true, stdio: ['ignore', log.fd, log.fd],
        });
        status = { state: 'starting', logFile };
        child.once('error', error => {
          child = null;
          status = { state: 'error', code: 'MONITOR_SPAWN_FAILED', detail: error.code, logFile };
        });
        child.once('exit', (code, signal) => {
          child = null;
          status = { state: 'error', code: 'MONITOR_PROCESS_EXITED',
            detail: signal || String(code), logFile };
        });
        child.unref();
      } finally {
        await log.close();
      }
    } catch (error) {
      status = { state: 'error', code: 'MONITOR_AUTOSTART_FAILED',
        detail: error.code || error.name, logFile: status.logFile };
    }
  }
  return { ensure, status: () => status };
}

export function childRunMonitor({ toolsDir, readLog, listChildren, service, warn }) {
  const roots = new Map();
  let timer, running = false, disposed = false;
  const directory = root => {
    const dir = toolsDir();
    return dir && validId(root) ? path.join(dir, 'run-log-archive', root) : null;
  };
  const fail = (row, code, error) => {
    if (row.code !== code || row.detail !== error?.code) {
      warn(`${code} session=${row.id}${error?.code ? ' ' + error.code : ''}`);
    }
    row.code = code;
    row.detail = error?.code || null;
  };
  function observe(root, id, active, label) {
    if (disposed || !validId(root) || !validId(id) || root === id) return;
    if (!roots.has(root)) roots.set(root, new Map());
    const children = roots.get(root);
    let row = children.get(id);
    if (!row || (active && !row.active)) {
      row = { id, kind: 'child', active, label: label || row?.label,
        openedAt: Date.now(), closedAt: active ? null : Date.now() };
      children.set(id, row);
    } else if (!active && row.active) {
      row.active = false;
      row.closedAt = Date.now();
    }
    if (!timer) {
      timer = setInterval(() => void tick(), 3000);
      timer.unref();
    }
    void tick();
  }
  async function belongs(root, id, log) {
    const seen = new Set([id]);
    let parent = log.header.parentSession;
    while (validId(parent) && !seen.has(parent)) {
      if (parent === root) return true;
      seen.add(parent);
      const next = await readLog(parent) ?? await readArchive(root, parent);
      if (next?.header?.id !== parent) return false;
      parent = next.header.parentSession;
    }
    return false;
  }
  async function capture(root, children, row) {
    if (row.active || children.get(row.id) !== row || Date.now() - row.closedAt < 2500) return;
    try {
      const log = await readLog(row.id);
      if (!log?.complete || log.header?.id !== row.id) {
        fail(row, 'CHILD_RUN_LOG_UNAVAILABLE');
      } else if (!await belongs(root, row.id, log)) {
        fail(row, 'CHILD_RUN_PARENT_UNVERIFIED');
      } else {
        const projection = projectChildRun(log, row.label);
        const pending = projection.agent.pendingJobs > 0;
        const stamp = JSON.stringify([log.events.length, log.events.at(-1)?.seq,
          projection.agent.lastAt, pending]);
        if (row.stamp !== stamp) {
          row.stamp = stamp;
          row.code = pending ? 'CHILD_RUN_JOBS_PENDING' : null;
          return;
        }
        const dir = directory(root);
        if (!dir) throw Object.assign(new Error('archive directory unavailable'), { code: 'NO_TOOLS_DIR' });
        if (row.savedStamp !== stamp) {
          const archive = {
            schema: 'dsh-child-run@1', root, archivedAt: Date.now(), pending,
            header: { id: row.id, parentSession: log.header.parentSession, createdAt: log.header.createdAt },
            projection,
          };
          await fs.mkdir(dir, { recursive: true });
          const tmp = path.join(dir, `.${row.id}.${randomUUID()}.tmp`);
          try {
            await fs.writeFile(tmp, JSON.stringify(archive), { flag: 'wx', mode: 0o600 });
            if (disposed || row.active || children.get(row.id) !== row) return;
            await fs.rename(tmp, path.join(dir, row.id + '.json'));
            row.savedStamp = stamp;
          } finally { await fs.rm(tmp, { force: true }).catch(() => {}); }
        }
        row.code = pending ? 'CHILD_RUN_JOBS_PENDING' : null;
        row.detail = null;
        if (!pending && children.get(row.id) === row) children.delete(row.id);
      }
    } catch (error) { fail(row, 'CHILD_RUN_ARCHIVE_WRITE_FAILED', error); }
  }
  async function tick() {
    if (running || disposed) return;
    running = true;
    try {
      // Start the service before reading any child files. Recheck while any activation lives.
      const ensure = [...roots.values()].some(children => [...children.values()].some(row => row.active))
        ? service.ensure() : null;
      for (const [root, children] of roots) {
        try {
          const roster = await listChildren(root);
          for (const item of roster) {
            const row = children.get(item.id);
            if (!row && item.kind === 'child' && item.activity === 'running') {
              observe(root, item.id, true, item.label);
              continue;
            }
            if (row?.active && Date.now() - row.openedAt >= 3000 &&
                item.kind === 'child' && item.activity === 'inactive') {
              row.active = false;
              row.closedAt = Date.now();
            }
          }
        } catch (error) { /* An unavailable roster does not close live children. */ }
        for (const row of children.values()) await capture(root, children, row);
        if (!children.size) roots.delete(root);
      }
      await ensure;
    } finally {
      running = false;
      if (!roots.size && timer) { clearInterval(timer); timer = null; }
    }
  }
  async function archives(root) {
    const dir = directory(root);
    if (!dir) return [];
    let files;
    try { files = await fs.readdir(dir); }
    catch (error) { if (error.code === 'ENOENT') return []; throw error; }
    return files.filter(file => file.endsWith('.json') && validId(file.slice(0, -5)))
      .map(file => ({ id: file.slice(0, -5), kind: 'child' }));
  }
  async function readArchive(root, id) {
    const dir = directory(root);
    if (!dir || !validId(id)) return null;
    try {
      const log = JSON.parse(await fs.readFile(path.join(dir, id + '.json'), 'utf8'));
      return log.schema === 'dsh-child-run@1' && log.root === root && log.header?.id === id ? log : null;
    } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }
  function status(root) {
    const children = [...(roots.get(root)?.values() || [])];
    return { active: children.filter(row => row.active).map(row => row.id),
      collecting: children.filter(row => !row.active).map(row =>
        ({ id: row.id, code: row.code || null, detail: row.detail || null })),
      service: service.status() };
  }
  function dispose() { disposed = true; clearInterval(timer); roots.clear(); }
  return { observe, archives, readArchive, status, dispose };
}
