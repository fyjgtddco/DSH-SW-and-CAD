/**
 * run-log-route.mjs — GET /dsh-engineering-ui/run-log 与两块同源静态资源
 * ==========================================================================
 * 为什么单独成文件而不是塞进 lib/index.js：
 *   ① index.js 已经 350KB+，再往里加一段"会话谱系 + 状态文件定位 + 版本核对"
 *      就彻底没法单测；
 *   ② 本模块只依赖注入进来的四个宿主原语（readSessionLog / listDescendants /
 *      stateDirCandidates / stepsJsonCandidates），可以脱离宿主直接跑用例；
 *   ③ 宿主只负责"注册 + 传依赖"，业务判定全部在这里，与本仓库既有的
 *      lib/run-log/monitor-log-route.mjs 同一分层。
 *
 * 只读，永不写会话、不调工具、不注入提示词。数据来源就两处：
 *   · 宿主持久化会话日志（readSessionLog：open(id,'read') → read() → close()）；
 *   · 七阶段插件写的任务状态文件 <stateDir>/<taskId>.json + 同版 steps.json。
 *
 * 安全边界（与接入手册第 2 节一致）：
 *   · rootSessionId 只允许宿主 ID 口径（调用方传入 ID_RE），不做模糊匹配；
 *   · 子会话必须用 header.parentSession 沿父链回到该根，回不去的一律丢弃并提示；
 *   · 状态文件必须 schema 一致且 state.root === rootSessionId，否则忽略；
 *   · 下发摘要及子代理可见输出。模型推理、原始消息和工具调用参数不直接导出。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildSnapshot } from './model.mjs';

// 本文件所在目录（只有宿主侧会用到，浏览器那半走独立模块）。
// 用 try 包住：万一某处把本文件当 CJS 文本求值，import.meta 会抛，
// 那时退回空串 —— 静态资源路由降级成 404，而摘要路由完全不受影响。
let HERE = '';
try {
  HERE = path.dirname(fileURLToPath(import.meta.url));
} catch (e) {
  HERE = '';
}

const STATE_SCHEMA = 'dsh-engineering/seven-stage-state@1';
// 一次最多读几个子会话：与接入手册 MAX_SESSIONS=24 相比刻意收紧，
// 因为本路由会被面板轮询，5 份日志（含 2MB 级的子会话）已经够重。
const MAX_CHILD_SESSIONS = 12;
const MAX_LIMIT = 1000;
const DEFAULT_LIMIT = 300;
const STEPS_CACHE_MS = 30000;

/**
 * 在途请求去重表：key → Promise<snapshot>。
 *
 * 为什么需要：面板每 2.5s 轮询一次，而拼一份摘要要冷读根会话 + 最多 12 个子会话
 * （实测 5 个会话约 400ms）。同一根会话被两个标签页/两个面板同时打开时，
 * 不去重就会把同一份重活叠着干。这里只合并【同时在飞】的相同请求，
 * 不缓存结果 —— 下一次轮询仍然读到最新落盘事件，不会看到过期数据。
 */
const inFlight = new Map();

const ASSET_TYPES = {
  'view.mjs': 'text/javascript; charset=utf-8',
  'view.css': 'text/css; charset=utf-8',
};

/** 读同目录下的静态资源（浏览器半的 view.mjs / view.css）。 */
export function readRunLogAsset(name) {
  if (!HERE || !Object.hasOwn(ASSET_TYPES, name)) return null;
  try {
    return fs.readFileSync(path.join(HERE, name), 'utf8');
  } catch (e) {
    return null;
  }
}

/** 同源静态资源路由（与摘要路由同一套 guardRoute 访问范围，由宿主包裹）。 */
export function createRunLogAssetHandler(name) {
  const type = ASSET_TYPES[name];
  if (!type) throw new Error('run-log: unknown asset ' + name);
  return async function runLogAssetHandler(req, res) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      try { res.setHeader('allow', 'GET, HEAD'); } catch (e) {}
      res.statusCode = 405;
      res.setHeader('content-type', 'text/plain; charset=utf-8');
      res.end('GET only');
      return;
    }
    const text = readRunLogAsset(name);
    res.statusCode = text == null ? 404 : 200;
    res.setHeader('content-type', text == null ? 'text/plain; charset=utf-8' : type);
    // 面板是 dev 期热改的，不做长缓存；nost敏感性 = 资源本身是插件源码。
    res.setHeader('cache-control', 'no-store');
    res.setHeader('x-content-type-options', 'nosniff');
    res.setHeader('cross-origin-resource-policy', 'same-origin');
    res.end(req.method === 'HEAD' ? undefined : (text == null ? 'not found' : text));
  };
}

const safeJson = (file) => {
  try {
    const raw = fs.readFileSync(file, 'utf8');
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (e) {
    return null;
  }
};

const mtimeOf = (file) => {
  try { return fs.statSync(file).mtimeMs; } catch (e) { return 0; }
};

/** steps.json 缓存：路径 → {at, spec}。 */
const stepsCache = new Map();

function loadSteps(candidates, specHash) {
  const now = Date.now();
  let fallback = null;
  for (const file of candidates) {
    if (typeof file !== 'string' || !path.isAbsolute(file)) continue;
    let entry = stepsCache.get(file);
    if (!entry || (now - entry.at) > STEPS_CACHE_MS) {
      const spec = safeJson(file);
      entry = { at: now, spec };
      stepsCache.set(file, entry);
    }
    if (!Array.isArray(entry.spec?.steps) || !entry.spec.steps.length) continue;
    if (!specHash || entry.spec.sha256 === specHash) return entry.spec;
    fallback ??= entry.spec;
  }
  return fallback;
}

// 阶段简称对应《设计依据 V0.3》的七个流程标题。步骤编号、标题和顺序取自插件规格。
const STAGE_NAMES = ['现场与边界', '工件与工艺', '需求与验收', '功能与路线',
  '骨架与接口', '零件与连接', '整机集成'];

export function projectEngineeringWorkflow(spec, state) {
  const steps = Array.isArray(spec?.steps) ? spec.steps : [];
  if (!steps.length) return { ok: false, error: '未找到工程流程规格' };
  if (state?.specHash && state.specHash !== spec.sha256) {
    return { ok: false, error: '当前任务的流程版本与已安装规格不一致' };
  }
  const records = Array.isArray(state?.records) ? state.records : [];
  const history = Array.isArray(state?.history) ? state.history : [];
  const complete = new Set(records.map(record => record?.step));
  const invalidated = new Set(history.flatMap(row => Array.isArray(row?.invalidated)
    ? row.invalidated.map(record => record?.step) : []));
  const current = state && !['setup', 'ready', 'partial'].includes(state.phase)
    ? steps[state.cursor] ?? null : null;
  const waiting = Boolean(state?.question || state?.waitReason || state?.phase === 'evidence_wait');
  const stages = new Map();
  for (const step of steps) {
    const n = step.stage;
    if (!stages.has(n)) stages.set(n, { id: n, title: STAGE_NAMES[n - 1] ?? `阶段 ${n}`, steps: [] });
    const status = step.id === current?.id ? (waiting ? 'waiting' : 'active')
      : complete.has(step.id) ? 'complete' : invalidated.has(step.id) ? 'revisit' : 'pending';
    stages.get(n).steps.push({ id: step.id, title: step.title, status });
  }
  return {
    ok: true, taskId: state?.taskId ?? null, phase: state?.phase ?? null,
    version: spec.version, current: current ? { id: current.id, title: current.title, waiting } : null,
    completed: steps.filter(step => complete.has(step.id)).length, total: steps.length,
    stages: [...stages.values()],
  };
}

/**
 * 按"离当前部署越近越权威"的顺序找该根会话的七阶段状态文件。
 * @returns {{state:object|null, file:string|null, dir:string|null, stale:string[]}}
 */
function findState(rootSessionId, dirs) {
  const stale = [];
  let best = null;
  for (const dir of Array.isArray(dirs) ? dirs : []) {
    if (typeof dir !== 'string' || !path.isAbsolute(dir)) continue;
    let names = [];
    try { names = fs.readdirSync(dir); } catch (e) { continue; }
    for (const name of names) {
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}\.json$/.test(name)) continue;
      const file = path.join(dir, name);
      const state = safeJson(file);
      if (!state || state.schema !== STATE_SCHEMA) continue;
      if (String(state.root || '') !== rootSessionId) {
        if (state.taskId && !stale.includes(String(state.taskId))) stale.push(String(state.taskId));
        continue;
      }
      const at = mtimeOf(file);
      if (!best || at > best.at) best = { state, file, dir, at };
    }
    // 命中即停：优先级高的目录里已经有本根会话的状态，就不再去低优先级目录里找
    // （与 findToolsDir 的"认哪个目录就只认那个目录"口径一致，避免跨部署串数据）。
    if (best) break;
  }
  return { state: best ? best.state : null, file: best ? best.file : null,
    dir: best ? best.dir : null, mtime: best ? best.at : 0, stale };
}

/**
 * @param {object} deps
 * @param {(id:string)=>Promise<{header:object,events:Array,inheritedEventCount?:number}|null>} deps.readSessionLog
 * @param {(rootId:string)=>Promise<Array<object>>} deps.listDescendants
 * @param {()=>string[]} deps.stateDirCandidates 七阶段状态目录候选（绝对路径，按优先级）
 * @param {()=>string[]} deps.stepsJsonCandidates steps.json 候选（绝对路径）
 * @param {(id:string)=>boolean} [deps.isLiveSession] 该会话此刻是否还在宿主里跑
 * @param {(req:any)=>URLSearchParams} deps.queryOf
 * @param {(res:any,status:number,body:any)=>void} deps.send
 * @param {(msg:string)=>void} [deps.warn]
 * @param {RegExp} [deps.idRe] 宿主会话 ID 口径（默认与 index.js 的 ID_RE 同源）
 */
export function createRunLogHandler(deps) {
  const {
    readSessionLog, listDescendants, stateDirCandidates, stepsJsonCandidates,
    isLiveSession, childMonitor, queryOf, send, warn, idRe,
  } = deps || {};
  const ID = idRe instanceof RegExp ? idRe : /^[A-Za-z0-9_.:-]{1,128}$/;
  const note = typeof warn === 'function' ? warn : () => {};

  if (typeof readSessionLog !== 'function' || typeof listDescendants !== 'function' ||
      typeof send !== 'function' || typeof queryOf !== 'function') {
    throw new TypeError('run-log: readSessionLog / listDescendants / queryOf / send 均为必需');
  }
  const stateDirs = typeof stateDirCandidates === 'function' ? stateDirCandidates : () => [];
  const stepsCands = typeof stepsJsonCandidates === 'function' ? stepsJsonCandidates : () => [];

  return async function runLogHandler(req, res) {
    if (req.method !== 'GET') {
      try { res.setHeader('allow', 'GET'); } catch (e) {}
      return send(res, 405, { ok: false, error: 'GET only' });
    }
    const q = queryOf(req);
    const rootSessionId = String((q && q.get ? q.get('rootSessionId') : '') || '').trim();
    if (!ID.test(rootSessionId)) {
      return send(res, 400, { ok: false, error: 'bad rootSessionId' });
    }
    if (q.get('view') === 'workflow') {
      const { state } = findState(rootSessionId, stateDirs());
      const spec = loadSteps(stepsCands(), state?.specHash);
      try { res.setHeader('cache-control', 'no-store'); } catch (e) {}
      return send(res, 200, projectEngineeringWorkflow(spec, state));
    }
    const download = String((q.get && q.get('download')) || '') === '1';
    const rawLimit = Number((q.get && q.get('limit')) || DEFAULT_LIMIT);
    const limit = Number.isFinite(rawLimit)
      ? Math.min(MAX_LIMIT, Math.max(1, Math.trunc(rawLimit)))
      : DEFAULT_LIMIT;

    // 轮询请求按「根会话 + limit」合并：面板每 2.5s 一次，而拼一份摘要要冷读
    //   根会话 + 最多 12 个子会话（实测 5 个会话约 400ms）。两个标签页同时开
    //   同一个任务时不去重就会把同一份重活叠着干。
    //   ⚠️ 只合并【同时在飞】的请求，不缓存结果 —— 下一次轮询仍然读到最新落盘
    //      事件；串行的两次请求各读一次，绝不会看到过期快照。
    //   download=1 不参与合并：那是一次性的大活，也不该被 300 条的轮询"搭便车"。
    const pendingKey = rootSessionId + '|' + limit;
    let snapshot;
    try {
      if (!download) {
        const shared = inFlight.get(pendingKey);
        if (shared) {
          snapshot = await shared;
        } else {
          const task = compose();
          inFlight.set(pendingKey, task);
          try {
            snapshot = await task;
          } finally {
            inFlight.delete(pendingKey);
          }
        }
      } else {
        snapshot = await compose();
      }
    } catch (error) {
      const status = (error && error.httpStatus) || 503;
      // 响应体只给固定提示，具体原因进宿主诊断日志（可能含路径与会话 ID）。
      note('run-log 组装失败：' + String((error && error.message) || error));
      return send(res, status, (error && error.httpBody)
        || { ok: false, error: '运行日志暂时不可读' });
    }
    try {
      res.setHeader('cache-control', 'no-store');
      res.setHeader('x-content-type-options', 'nosniff');
      res.setHeader('cross-origin-resource-policy', 'same-origin');
    } catch (e) {}
    return send(res, 200, snapshot);

    /** 取数 + 组装。可判定的失败抛带 httpStatus 的错误，由上面的 catch 统一回。 */
    async function compose() {
      const warnings = [];
      const fail = (httpStatus, httpBody) => {
        const err = new Error(httpBody.error);
        err.httpStatus = httpStatus;
        err.httpBody = httpBody;
        return err;
      };
      let root = null;
      try {
        root = await readSessionLog(rootSessionId);
      } catch (e) {
        root = null;
      }
      if (!root || !root.header || !root.header.id) {
        throw fail(404, { ok: false, error: '会话日志不可读' });
      }
      if (String(root.header.id) !== rootSessionId) {
        throw fail(404, { ok: false, error: '会话日志与会话 ID 不匹配' });
      }
      // 根会话才是谱系的起点：拿子会话当根会把"别人那段"当成本任务（串数据）。
      if (root.header.parentSession) {
        throw fail(400, { ok: false, error: '该会话不是根会话' });
      }
      const rootEvents = Array.isArray(root.events) ? root.events : [];
      if (!rootEvents.length) {
        warnings.push('根会话只取得元数据，日志正文暂不可读。');
      }
      const sessions = [{
        header: root.header,
        events: rootEvents,
        inheritedEventCount: Number(root.inheritedEventCount) || 0,
      }];

      // ── 子会话：先按宿主名册取候选，再用 header.parentSession 沿父链回根 ──
      const monitor = childMonitor?.status(rootSessionId) ?? null;
      const tracked = new Set([...(monitor?.active ?? []), ...(monitor?.collecting ?? []).map(row => row.id)]);
      let rows = [];
      try {
        rows = await listDescendants(rootSessionId);
      } catch (e) {
        warnings.push('子代理名册暂不可读。');
      }
      try { rows = [...rows, ...(await childMonitor?.archives(rootSessionId) ?? [])]; }
      catch (error) { warnings.push('CHILD_RUN_ARCHIVE_LIST_FAILED · ' + (error.code || error.name)); }
      rows = [...[...tracked].map(id => ({ id, kind: 'child' })), ...rows];
      const childIds = [];
      for (const row of Array.isArray(rows) ? rows : []) {
        const cid = String((row && row.id) || '');
        if (!cid || cid === rootSessionId || childIds.includes(cid)) continue;
        if (row && row.kind && row.kind !== 'child') continue;
        if (!ID.test(cid)) continue;
        if (!download && !childMonitor && childIds.length >= MAX_CHILD_SESSIONS) {
          warnings.push('子会话数量超过 ' + MAX_CHILD_SESSIONS + ' 个，时间线可能不完整。');
          break;
        }
        childIds.push(cid);
      }
      for (const cid of childIds) {
        let archive = null;
        try { archive = await childMonitor?.readArchive(rootSessionId, cid); }
        catch (error) { warnings.push('CHILD_RUN_ARCHIVE_READ_FAILED · ' + cid + ' · ' + (error.code || error.name)); }
        if (archive && !archive.pending && !tracked.has(cid)) {
          sessions.push(archive);
          continue;
        }
        let log = null;
        try { log = await readSessionLog(cid); } catch (e) { log = null; }
        if (archive && (!log?.complete || !log.events.length)) {
          sessions.push(archive);
          continue;
        }
        if (!log || !log.header || String(log.header.id || '') !== cid) {
          warnings.push('子会话日志暂未取得：' + cid);
          continue;
        }
        sessions.push({
          header: log.header,
          events: Array.isArray(log.events) ? log.events : [],
          inheritedEventCount: Number(log.inheritedEventCount) || 0,
        });
      }
      const parentOf = new Map(sessions.map((s) => [String(s.header.id),
        s.header.parentSession ? String(s.header.parentSession) : null]));
      const onChain = (sid) => {
        const seen = new Set([sid]);
        let cur = parentOf.get(sid) ?? null;
        while (cur) {
          if (cur === rootSessionId) return true;
          if (seen.has(cur)) return false;
          seen.add(cur);
          cur = parentOf.get(cur) ?? null;
        }
        return false;
      };
      const kept = [];
      for (const s of sessions) {
        const sid = String(s.header.id);
        if (sid === rootSessionId || onChain(sid)) kept.push(s);
        else warnings.push('已忽略不属于该根会话的子会话日志：' + sid);
      }

      // ── 七阶段状态：schema + 根会话归属都要对得上 ─────────────────────
      let found = { state: null, file: null, dir: null, mtime: 0, stale: [] };
      try {
        found = findState(rootSessionId, stateDirs());
      } catch (e) {
        found = { state: null, file: null, dir: null, mtime: 0, stale: [] };
      }
      if (!found.state) {
        warnings.push(found.stale.length
          ? '状态目录里的任务不属于当前根会话（' + found.stale.slice(0, 3).join('、') + '），已忽略。'
          : '未找到该根会话的七阶段状态文件，阶段信息改取工具回执。');
      }

      // ── 同版 steps.json：版本对不上就只补步号，总步数按回执显示 ─────────
      let steps = null;
      if (found.state) {
        steps = loadSteps(stepsCands(), found.state.specHash);
        if (steps) {
          if (found.state.specHash && steps.sha256 &&
              String(found.state.specHash) !== String(steps.sha256)) {
            warnings.push('步骤规格与任务版本不一致，总步数改按工具回执显示。');
          }
        } else {
          warnings.push('步骤规格暂时不可读，总步数改按工具回执显示。');
        }
      }

      let mode = 'history';
      try {
        if (childMonitor ? tracked.size > 0
          : typeof isLiveSession === 'function' && isLiveSession(rootSessionId)) mode = 'live';
      } catch (e) {
        mode = 'history';
      }

      return buildSnapshot({
        rootSessionId,
        sessions: kept,
        state: found.state,
        steps,
        mode,
        warnings,
        observedAt: Date.now(),
        limit,
        includeAll: download,
        monitor,
      });
    }
  };
}
