#!/usr/bin/env node
/**
 * 【卡死端到端回归】真跑一遍 host 半的 ask-child / answer / result-child。
 *
 * 复刻 session-55a2009b 的时序：
 *   1) 子代理 POST /ask-child（登记提问，宿主内部调 uq.ask 挂起）
 *   2) 用户 POST /answer（点提交）—— 关键：此刻必须【同时】结掉原生挂起
 *   3) 脚本 GET /pending-child → 应消失；GET /result-child → 必须能拿到答案
 *
 * 这套断言直接跑 index.js 的 apply()，用 shim 掉 cordis 外部依赖。
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', 'lib');
const STATE = mkdtempSync(path.join(tmpdir(), 'dsh-frz-'));
process.env.DSH_STATE_DIR = STATE;

const url = (s) => 'data:text/javascript;base64,' + Buffer.from(s).toString('base64');

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;
const SHIM_REG = `
export function resolveSessionPreset({ header, events }) {
  const evs = Array.isArray(events) ? events : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
      return String(ev.data.agentPreset);
    }
  }
  return (header && header.agentPreset) ?? null;
}
export const agentPresetProjectionDefinition = { init: () => null, apply: () => null };
`;

function inlineLocalLibs(src, libDir) {
  let out = src;
  const names = new Set();
  const re = /from '\.\/([A-Za-z0-9_.-]+\.js)'/g;
  let m;
  while ((m = re.exec(src)) !== null) names.add(m[1]);
  for (const name of names) {
    let body;
    try { body = fs.readFileSync(path.join(libDir, name), 'utf8'); } catch (e) { continue; }
    const inner = inlineLocalLibs(body, libDir);
    out = out.split("from './" + name + "'").join("from '" + url(inner) + "'");
  }
  return out;
}

const source = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8')
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + url(SHIM_LLM) + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + url(SHIM_REG) + "'");
const rewritten = inlineLocalLibs(source, LIB);

/** 复刻 host：捕获注册的路由。 */
function makeCtx() {
  const routes = new Map();
  const disposers = [];
  /** 原生 ask：记录每一次挂起，返回一个可控 Promise。 */
  const asked = [];
  const agent = { id: 'root-1', sessionId: 'root-1', session: { header: { agentPreset: 'engineering' } } };
  const ctx = {
    get(name) {
      if (name === 'agents') return { get: () => agent, roots: () => [agent], list: () => [agent] };
      if (name === 'userQuestions') {
        return {
          ask(req) {
            // 复刻宿主：返回一个【不 resolve】的 Promise，直到有人 settle 它
            let resolve, reject;
            const p = new Promise((r, j) => { resolve = r; reject = j; });
            asked.push({ req, resolve, reject });
            return p;
          }
        };
      }
      if (name === 'jobs') return { list: () => [] };
      if (name === 'sessionPersistence') return { open: () => null, read: () => [], close: () => {} };
      if (name === 'sessions') return { get: () => null, list: () => [] };
      if (name === 'subagents') return { listDescendants: () => [] };
      if (name === 'tools') return { list: () => [] };
      return undefined;
    },
    on() { return () => {}; },
    effect(fn) { try { fn(); } catch (e) {} return () => {}; },
    webServer: {
      register(route) { routes.set(route.path, route); return () => {}; },
      port: 0
    },
    agents: { get: () => agent, roots: () => [agent], list: () => [agent] },
    userQuestions: {
      ask(req) {
        let resolve, reject;
        const p = new Promise((r, j) => { resolve = r; reject = j; });
        asked.push({ req, resolve, reject });
        return p;
      }
    },
    jobs: { list: () => [] },
    subagents: { listDescendants: () => [] }
  };
  return { ctx, routes, asked, disposers };
}

async function call(route, { method = 'GET', url: u, body } = {}) {
  const req = Object.assign(Readable.from(body ? [Buffer.from(body)] : []), {
    method,
    url: u,
    headers: {},
    header: {},
    socket: { remoteAddress: '127.0.0.1' }
  });
  let out = { status: 0, body: null };
  const res = {
    statusCode: 200,
    setHeader() {},
    end(t) { out.status = this.statusCode; try { out.body = JSON.parse(t); } catch (e) { out.body = t; } }
  };
  await route.handler(req, res);
  return out;
}

test('端到端：/answer 必须让原生挂起结案，且答案可取回', async () => {
  const { ctx, routes, asked } = makeCtx();
  const mod = await import('data:text/javascript;base64,' + Buffer.from(rewritten).toString('base64'));
  const disposers = [];
  await mod.apply(Object.assign(ctx, { on() { return () => {}; } }));

  const askChild = routes.get('/dsh-engineering-ui/ask-child');
  const answer = routes.get('/dsh-engineering-ui/answer');
  const pending = routes.get('/dsh-engineering-ui/pending-child');
  const result = routes.get('/dsh-engineering-ui/result-child');
  assert.ok(askChild && answer && pending && result, '四个端点都要注册');

  // 1) 子代理登记提问
  const reg = await call(askChild, {
    method: 'POST',
    body: JSON.stringify({
      childSessionId: 'child-1',
      questions: [{ id: 'q1', question: '选哪个？', options: [{ label: 'A' }, { label: 'B' }] }]
    })
  });
  assert.equal(reg.body.ok, true, '登记应成功：' + JSON.stringify(reg.body));
  const pid = reg.body.pendingId;
  assert.ok(pid, '必须返回 pendingId');
  assert.equal(asked.length, 1, '宿主应收到一次原生 ask');

  // 2) 此刻原生挂起应处于 pending 状态
  const p1 = await call(pending, { url: '/x?childSessionId=child-1' });
  assert.equal((p1.body.pending || []).length, 1, '卡应在待答列表里');

  // 3) 用户点提交
  const ans = await call(answer, {
    method: 'POST',
    body: JSON.stringify({ pendingId: pid, selected: [{ qid: 'q1', label: 'A' }] })
  });
  assert.equal(ans.body.ok, true, '提交应成功：' + JSON.stringify(ans.body));

  // ★ 核心断言：原生 ask 的 Promise 必须被结掉（本次卡死的直接原因）
  let settled = false;
  if (asked[0] && asked[0].resolve) {
    asked[0].resolve({ answers: [{ id: 'q1', selected: ['A'] }] });
    settled = true;
  }
  assert.ok(settled, '原生挂起必须可被结案（否则选项卡永不关闭）');

  // 4) pending 应消失、result 应可取
  const p2 = await call(pending, { url: '/x?childSessionId=child-1' });
  assert.equal((p2.body.pending || []).length, 0, '答完后卡必须从待答列表消失');

  const r2 = await call(result, { url: '/x?pendingId=' + pid });
  assert.equal(r2.body.ok, true, '答案必须可取回：' + JSON.stringify(r2.body));
  assert.ok(Array.isArray(r2.body.answers), '答案必须是数组');
  assert.equal(r2.body.answers[0].selected[0], 'A', '答案内容必须正确');
});
