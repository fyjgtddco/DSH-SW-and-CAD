import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';

class TransportError extends Error {}

export async function createMonitorHost({ url = 'http://127.0.0.1:18779', tokenFile, epoch = randomUUID() }) {
  const endpoint = new URL(url);
  if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1' ||
      endpoint.username || endpoint.password || endpoint.pathname !== '/' ||
      endpoint.search || endpoint.hash) throw new Error('监控器必须是本机 HTTP 根地址');
  const token = (await readFile(tokenFile, 'ascii')).trim();
  if (token.length < 40) throw new Error('host.token 无效');
  let heartbeat, pending = false;

  async function rpc(op, payload = {}, signal) {
    let response, result;
    try {
      const deadline = AbortSignal.timeout(op.startsWith('review-') || op === 'verify-task' ? 58_000 : 30_000);
      response = await fetch(new URL('/v1/' + op, endpoint), {
        method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
        redirect: 'error', body: JSON.stringify({ ...payload, epoch }),
        signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
      });
      result = await response.json();
    } catch (error) {
      throw new TransportError('MONITOR_UNREACHABLE', { cause: error });
    }
    if (result.error === 'WAIT_CAPACITY') throw new TransportError('WAIT_CAPACITY');
    if (!response.ok || result.ok !== true) throw new Error(result.error || 'MONITOR_REJECTED');
    return result;
  }

  function startHeartbeat(onProblem = console.warn) {
    if (heartbeat) return;
    heartbeat = setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await rpc('heartbeat');
        if (result.state === 'quarantined') onProblem('监控器处于隔离状态，等待恢复确认');
      } catch (error) {
        onProblem(error.message);
      } finally {
        pending = false;
      }
    }, 3000);
    heartbeat.unref?.();
  }

  function startWaitJob(jobs, { owner, kind, payload }) {
    if (!jobs || typeof jobs.start !== 'function') throw new Error('DSH_JOBS_UNAVAILABLE');
    if (kind !== 'wait-lock' && kind !== 'wait-rooms') throw new Error('INVALID_WAIT_JOB');
    if (!owner) throw new Error('DSH_JOB_OWNER_REQUIRED');
    return jobs.start({
      kind: 'command', owner,
      label: kind === 'wait-lock' ? '等待外部监控器分配 SW 锁' : '等待外部监控器确认房间收尾',
      run: () => {
        const abort = new AbortController();
        let settled = false;
        const done = (async () => {
          try {
            while (!abort.signal.aborted) {
              try {
                const result = await rpc(kind, { ...payload, timeout: 20 }, abort.signal);
                if (abort.signal.aborted) break;
                if (!['queued', 'waiting', 'paused', 'offline', 'quarantined'].includes(result.state)) {
                  return JSON.stringify(result);
                }
                if (['paused', 'offline', 'quarantined'].includes(result.state)) {
                  await sleep(1000, undefined, { signal: abort.signal });
                }
              } catch (error) {
                if (abort.signal.aborted) break;
                if (!(error instanceof TransportError)) {
                  return JSON.stringify({ ok: false, state: 'attention', error: error.message });
                }
                await sleep(1000, undefined, { signal: abort.signal });
              }
            }
          } catch (error) {
            if (!abort.signal.aborted) throw error;
          } finally {
            settled = true;
          }
          return JSON.stringify({ ok: true, state: 'cancelled' });
        })();
        return {
          done,
          cancel: () => {
            if (settled || abort.signal.aborted) return;
            abort.abort();
            if (kind === 'wait-lock') {
              // 仅取消对应排队票。领取后监控器会拒绝，实际 CAD 命令不受影响。
              void rpc('cancel', payload).catch((error) => console.warn('撤销等待票待核对:', error.message));
            }
          },
        };
      },
    });
  }

  return {
    epoch, rpc, startHeartbeat, startWaitJob,
    stopHeartbeat() { clearInterval(heartbeat); heartbeat = undefined; },
  };
}
