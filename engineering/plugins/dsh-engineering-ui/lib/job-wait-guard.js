/**
 * job-wait-guard.js — 【卡死修复 · 宿主层】job_output 长等待的静默阻塞与整轮中断
 * ==========================================================================
 * 事故现场（会话 session-6019cd4b-1ade-4e1f-8850-93f5aec97960，2026-10-31）
 *   · 22:45:32  父会话调用 job_output { job_id, timeout_ms: 270000, wait: true }
 *   · 22:45:56  总计子代理调用 job_output { job_id, timeout_ms: 240000, wait: true }
 *   · 22:46:58  两者【同一毫秒】失败：
 *                 Error: cannot get required service "jobs" in inactive context
 *                 turn/end { kind: "interrupted" }   ← 整轮结束
 *   · 用户只能强退再发「继续，刚刚卡死了，我只能强制退出了」
 *   · 22:49:03 / 22:49:34 重试（120000 / 330000）→ 22:50:42 再次一模一样地失败
 *
 * 根因（两层，缺一不可）
 *   ①【静默长阻塞】job_output 的 wait 一次性阻塞几分钟，期间 GUI 没有任何反馈；
 *      用户看到的就是"DSH 死机"，于是强制退出。这是用户可感知伤害的直接来源。
 *   ②【异常冒到 turn 级】等待期间宿主若停机 / 该服务 fiber 失活，cordis 在解析
 *      ctx.jobs 时抛 "cannot get required service ... in inactive context"；
 *      该异常穿过工具边界冒到 agent loop，最终被记成 turn/end{interrupted}，
 *      整轮对话直接作废。
 *
 * 本模块修的就是这两点，且【不依赖预设人设写没写对】：
 *   A. 把 job_output 的单次等待切成若干【短窗口】（默认 45s）。窗口到点后照常
 *      返回 "仍在运行" 给模型 —— 模型拿到进度、GUI 保持响应、用户随时能打断。
 *   B. 任何异常（含上面的 inactive context）都转成**普通工具错误**（isError=true，
 *      带可操作指引），绝不让框架异常终结整轮。
 *
 * 机制（官方契约，非 hack）
 *   @deepseek-ai/dsh-tool-call-timeout-policy 使用的就是同一层：
 *     ctx.on('tools/execute', async (exec, next) => ...)
 *   这里只补"等待窗口 + 异常降级"，不改变 job_output 的任何正常语义。
 *
 * 关闭开关（写错配置也绝不会误关；非法值一律回落默认）
 *   DSH_ENG_JOB_WAIT_GUARD=0         → 整个守卫关闭
 *   DSH_ENG_JOB_WAIT_MAX_MS=<ms>     → 单次等待上限（默认 45000，最小 5000）
 *
 * ⚠️ 设计约束（每条都对应一次实测踩坑，勿随意简化）
 *   1. 只处理 name === 'job_output' 且 wait:true 且请求超时超过窗口的调用，
 *      其余工具一律原样透传。
 *   2. 窗口未到点时必须原样返回 next() 的真实结果，不改变任何正常语义。
 *   3. 窗口到点返回的是【正常成功结果】，不是 throw —— 绝不能用另一种方式
 *      重新把对话打断。
 *   4. 纯 JS 实现、零外部依赖：宿主层缺包会整块不加载，那样修复就白做了。
 *   5. 守卫自身任何异常都要回落 next()，守卫坏了不能拖垮工具。
 */

/** 单次等待窗口上限（毫秒）。45s 覆盖一次 SW 建模步骤，又远低于"用户以为死机"的阈值。 */
const DEFAULT_MAX_WAIT_MS = 45000;
/** 下限：低于 5s 的等待会退化成忙轮询。 */
const MIN_MAX_WAIT_MS = 5000;

/** 守卫是否启用（默认启用；显式 0/false/off/no 才关闭）。 */
function guardEnabled() {
  try {
    const raw = process.env.DSH_ENG_JOB_WAIT_GUARD;
    if (raw === undefined || raw === null || String(raw).trim() === '') return true;
    const v = String(raw).trim().toLowerCase();
    return !(v === '0' || v === 'false' || v === 'off' || v === 'no');
  } catch (e) {
    return true;
  }
}

/** 等待上限：环境变量可覆盖，非法值一律回落默认。 */
function resolveMaxWaitMs() {
  try {
    const raw = process.env.DSH_ENG_JOB_WAIT_MAX_MS;
    if (raw !== undefined && raw !== null && String(raw).trim() !== '') {
      const v = Number(String(raw).trim());
      if (Number.isFinite(v) && v >= MIN_MAX_WAIT_MS) return Math.floor(v);
    }
  } catch (e) {}
  return DEFAULT_MAX_WAIT_MS;
}

/**
 * 是否为"宿主上下文 / 服务已失活"这一类框架层拒绝。
 * 命中后必须降级为可读工具错误，绝不能再让它冒到 turn 级。
 */
function isContextLostError(error) {
  try {
    const msg = String((error && error.message) || error || '');
    return /inactive context/i.test(msg)
        || /cannot get required service/i.test(msg);
  } catch (e) {
    return false;
  }
}

/** 上下文失活时给模型的、可操作的错误结果。 */
function contextLostResult(jobId) {
  const message = 'job_output 等待失败：后台任务所属的宿主上下文已失效（DSH 可能被重启，或该服务已卸载）。'
    + '这不代表任务本身失败。请改用磁盘 / 文件产物判断进度（例如 mode2_plan.json、room-board、reports/），'
    + '或先用 job_list 确认该任务是否还在注册表中，再决定是否重新发起。';
  return {
    content: [{ type: 'text', text: 'Error: ' + message + (jobId ? '（job_id=' + jobId + '）' : '') }],
    isError: true,
    error: {
      message: 'JOB_CONTEXT_LOST: ' + message,
      info: { name: 'JobContextLostError', code: 'JOB_CONTEXT_LOST', jobId },
    },
  };
}

/** 等待窗口到点时给模型的"仍在运行"回执（正常成功返回，不是错误）。 */
function waitWindowResult(jobId, waitedMs, sawStatus) {
  const status = sawStatus ? '（当前状态：' + sawStatus + '）' : '';
  const text = '[等待窗口已到 ' + Math.round(waitedMs / 1000) + 's] 后台任务 ' + jobId
    + ' 仍在运行' + status + '。\n'
    + '这是【正常的中途返回】，不是失败：任务继续在后台跑。\n'
    + '要继续等待请再调一次 job_output（wait:true）；先做别的事就稍后再读；'
    + '真的不需要了就 job_kill。建议同时用磁盘产物判断进度，别把整轮对话挂在等待上。';
  return {
    content: [{ type: 'text', text }],
    isError: false,
    meta: { jobWaitWindow: waitedMs },
  };
}

/** 从一次 job_output 的结果里抽状态行，用于"仍在运行"回执。 */
function statusOf(result) {
  try {
    const blocks = (result && result.content) || [];
    for (const b of blocks) {
      if (!b || b.type !== 'text' || typeof b.text !== 'string') continue;
      const m = b.text.match(/\[status:\s*([^\]]+)\]/);
      if (m) return m[1].trim();
    }
    const job = result && result.value && result.value.job;
    if (job && job.status) return String(job.status);
  } catch (e) {}
  return '';
}

/** 该次调用是否属于本守卫要处理的长等待。 */
function inspectsLongWait(exec, maxWaitMs) {
  try {
    if (!exec || exec.name !== 'job_output') return null;
    const args = exec.arguments || {};
    if (args.wait !== true) return null;
    const requested = (typeof args.timeout_ms === 'number' && Number.isFinite(args.timeout_ms))
      ? args.timeout_ms
      : 30000;                                  // 与 dsh-tool-jobs 的 waitTimeoutMs 默认一致
    if (requested <= maxWaitMs) return null;     // 本来就短，不必介入
    return { jobId: String(args.job_id || ''), requested, effective: maxWaitMs };
  } catch (e) {
    return null;
  }
}

/**
 * 装配守卫。返回 disposer。
 * @param ctx 宿主插件上下文（需可监听 'tools/execute'）
 * @returns {() => void}
 */
export function installJobWaitGuard(ctx) {
  if (!guardEnabled() || !ctx || typeof ctx.on !== 'function') {
    return () => {};
  }
  const maxWaitMs = resolveMaxWaitMs();

  const listener = async (exec, next) => {
    let plan = null;
    try {
      plan = inspectsLongWait(exec, maxWaitMs);
    } catch (e) {
      plan = null;
    }

    // ── 非长等待：原样透传；仅当是 job_output 且命中上下文失活时降级 ──
    if (!plan) {
      try {
        return await next();
      } catch (error) {
        if (exec && exec.name === 'job_output' && isContextLostError(error)) {
          return contextLostResult(exec.arguments && exec.arguments.job_id);
        }
        throw error;
      }
    }

    // ── 长等待：用【窗口竞速】实现，绝不改写 exec.arguments ─────────────
    //   ⚠️ 为什么不能改写参数（实测契约，别想当然）：
    //     dsh-tools 在 createExecution 里就做了
    //         arguments: deepFreeze(snapshotJsonValue(exec.arguments))
    //     所以 tools/execute 包装层拿到的 exec.arguments 是【深冻结】的，
    //     任何赋值都 throws（严格模式）。早先"把 timeout_ms 改小"的写法在这里
    //     会被静默跳过 —— 看起来装上了守卫，实际一次都没生效。
    //   因此这里改为：保留原始参数不动，自己给 next() 设一个截止时间；
    //     到点后该工具调用立即返回（后台 job 继续跑，语义与 job_output 一致）。
    const startedAt = Date.now();
    let timer = null;
    let finished = false;
    const windowExpired = new Promise((resolve) => {
      timer = setTimeout(() => {
        if (finished) return;
        resolve('window');
      }, maxWaitMs);
      // 让定时器不阻塞进程退出（宿主关闭时立刻放行，不留悬挂 handle）
      try { if (timer && typeof timer.unref === 'function') timer.unref(); } catch (e) {}
    });

    try {
      const raced = await Promise.race([
        Promise.resolve(next()).then((result) => ({ kind: 'done', result })),
        windowExpired.then(() => ({ kind: 'window' })),
      ]);
      finished = true;
      if (timer) { try { clearTimeout(timer); } catch (e) {} }
      if (raced.kind === 'window') {
        // 窗口到点：返回正常回执，任务仍在后台运行（不是失败、不打断对话）
        return waitWindowResult(plan.jobId, Date.now() - startedAt, '');
      }
      const result = raced.result;
      const st = statusOf(result);
      // 任务已结束（completed / killed / failed）→ 真实结果原样返回
      if (st && !/running|stopping/i.test(st)) return result;
      return waitWindowResult(plan.jobId, Date.now() - startedAt, st);
    } catch (error) {
      finished = true;
      if (timer) { try { clearTimeout(timer); } catch (e) {} }
      if (isContextLostError(error)) return contextLostResult(plan.jobId);
      throw error;
    }
  };

  const off = ctx.on('tools/execute', listener);
  return () => {
    try { if (typeof off === 'function') off(); } catch (e) {}
  };
}

export const JOB_WAIT_GUARD_DEFAULTS = Object.freeze({
  enabled: true,
  maxWaitMs: DEFAULT_MAX_WAIT_MS,
  minWaitMs: MIN_MAX_WAIT_MS,
});
