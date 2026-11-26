/**
 * monitor-log-route.mjs — GET /dsh-engineering-ui/monitor-log 的实现
 * ==========================================================================
 * 为什么单独成文件而不是塞进 lib/index.js：
 *   ① index.js 已经 300KB+，路由再长一段就彻底没法单测；
 *   ② 这条路由的"降级语义"是本次接入最容易出错的地方（把"不可用"显示成
 *      "没有日志"），必须能脱离宿主单独跑用例；
 *   ③ 宿主只负责"注册 + 传依赖"，不做任何业务判定，符合本仓库一直以来的
 *      分层（Python/监控器是权威，宿主只转发）。
 *
 * 数据来源与权限：
 *   · 任务作用域由宿主从 workflow_state.json 绑定（bridge.currentTask()），
 *     **不**信任查询串里的 task；查询串里的 task 只用于"任务是否已切换"的
 *     连续性核对，不一致直接 409 TASK_CHANGED。
 *   · 监控器地址与 token 全在宿主进程内存里，浏览器只拿到归一化后的结果。
 *
 * 降级（永不假装成功）：
 *   任何失败都返回 HTTP 200 + { ok:false, code, error, hint, status:{level:'unavailable'} }，
 *   code 明确区分：MONITOR_BRIDGE_DOWN / MONITOR_NOT_ENABLED / MONITOR_UNAVAILABLE /
 *   MONITOR_NOT_ATTACHED / DIAGNOSTICS_UNSUPPORTED / DIAGNOSTICS_CURSOR_INVALID /
 *   TASK_UNKNOWN / TASK_CHANGED …
 */

import {
  buildMonitorLog,
  parseMonitorLogQuery,
  exportFilename,
  DOWNLOAD_PAGE,
  MAX_DOWNLOAD_EVENTS,
} from './monitor-model.mjs';

/**
 * @param {object} deps
 * @param {object|null|(()=>object|null)} deps.bridge MonitorBridge 实例，
 *   或返回该实例的取值函数（宿主传取值函数：桥可能在 apply 期间被重建；
 *   返回 null 表示宿主桥未装配 → 显式降级 MONITOR_BRIDGE_DOWN）
 * @param {(res:any, status:number, body:any)=>void} deps.send
 * @param {(req:any)=>URLSearchParams} deps.queryOf
 * @returns {(req:any, res:any)=>Promise<void>}
 */
export function createMonitorLogHandler({ bridge, send, queryOf }) {
  // 允许传实例或取值函数：传实例时行为不变（既有单测就是这么用的）。
  const bridgeOf = typeof bridge === 'function' ? bridge : () => bridge;
  const safeBridge = () => {
    try {
      return bridgeOf() || null;
    } catch (e) {
      return null;
    }
  };
  return async function monitorLogHandler(req, res) {
    if (req.method !== 'GET') {
      try { res.setHeader('allow', 'GET'); } catch (e) {}
      return send(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED', error: 'GET only' });
    }
    try {
      res.setHeader('cross-origin-resource-policy', 'same-origin');
      res.setHeader('x-content-type-options', 'nosniff');
      res.setHeader('referrer-policy', 'no-referrer');
    } catch (e) {}

    const bridge = safeBridge();
    if (!bridge) {
      const payload = {
        ok: false,
        code: 'MONITOR_BRIDGE_DOWN',
        error: '宿主桥未装配（MonitorBridge 初始化失败）',
        hint: '检查插件日志里的 dsh-engineering-ui 初始化错误',
        scope: 'unavailable',
        observed_at: new Date().toISOString(),
      };
      return send(res, 200, buildMonitorLog(payload));
    }

    const parsed = parseMonitorLogQuery(queryOf(req));
    if (!parsed.ok) {
      return send(res, 400, buildMonitorLog({
        ok: false,
        code: parsed.code,
        error: parsed.error,
        scope: 'unavailable',
        observed_at: new Date().toISOString(),
      }));
    }

    // 任务绑定只有一个权威来源：宿主读 workflow_state.json。
    let task = null;
    try {
      task = bridge.currentTask() || null;
    } catch (e) {
      task = null;
    }
    if (parsed.task && parsed.task !== task) {
      return send(res, 409, buildMonitorLog({
        ok: false,
        code: 'TASK_CHANGED',
        error: '当前工程任务已切换（查询串 task=' + parsed.task
          + '，宿主绑定=' + (task || '无') + '）',
        hint: '刷新面板重读',
        scope: 'unavailable',
        task,
        observed_at: new Date().toISOString(),
      }));
    }

    if (!parsed.download) {
      const raw = await bridge.readDiagnostics({
        since_seq: parsed.since_seq,
        limit: parsed.limit,
        task,
      });
      return send(res, 200, buildMonitorLog(Object.assign({}, raw, {
        since_seq: parsed.since_seq,
        limit: parsed.limit,
      })));
    }

    // ── download=1：分页拉全量（有上限，绝不无限循环）──────────────────
    const events = [];
    let cursor = parsed.since_seq;
    let latest = null;
    let incomplete = false;
    let hasMore = true;
    let failure = null;
    let pages = 0;
    let truncated = false;
    while (hasMore && !failure) {
      pages += 1;
      if (pages > Math.ceil(MAX_DOWNLOAD_EVENTS / DOWNLOAD_PAGE) + 2) {
        truncated = true;
        break;
      }
      const page = await bridge.readDiagnostics({
        since_seq: cursor,
        limit: DOWNLOAD_PAGE,
        task,
      });
      if (page.ok !== true) {
        failure = page;
        break;
      }
      for (const event of page.events || []) events.push(event);
      latest = page.latest_seq;
      incomplete = incomplete || page.incomplete === true;
      hasMore = page.has_more === true;
      const next = Number.isSafeInteger(page.next_since_seq) ? page.next_since_seq : cursor;
      if (next === cursor) break;      // 防御：游标不前进就停，绝不空转
      cursor = next;
      if (events.length >= MAX_DOWNLOAD_EVENTS) {
        truncated = hasMore || events.length > MAX_DOWNLOAD_EVENTS;
        events.length = MAX_DOWNLOAD_EVENTS;
        break;
      }
    }
    if (failure) {
      return send(res, 200, buildMonitorLog(Object.assign({}, failure, {
        since_seq: parsed.since_seq,
        limit: parsed.limit,
        observed_at: new Date().toISOString(),
      })));
    }
    const payload = {
      ok: true,
      task,
      scope: task ? 'task' : 'global',
      attached: true,
      events,
      since_seq: parsed.since_seq,
      limit: DOWNLOAD_PAGE,
      latest_seq: latest,
      next_since_seq: cursor,
      has_more: false,
      incomplete,
      truncated,
      download: true,
      observed_at: new Date().toISOString(),
    };
    try {
      res.setHeader('content-disposition',
        'attachment; filename="' + exportFilename(task) + '"');
    } catch (e) {}
    return send(res, 200, buildMonitorLog(payload));
  };
}
