/**
 * model-errors.js — 【A4】错误码归一与退避（纯函数，无宿主依赖）
 * ==========================================================================
 * 为什么单独成文件：这两个函数必须能被 `node --test` 直接单测，
 * 而 lib/index.js 是 host 半（依赖 cordis 上下文、会动态 import 宿主包）。
 * 把它们放进独立模块，既能被 index.js 引用，也能脱离宿主验证口径。
 */

// ══ 【A4 修复】错误码归一：429 / HTTP 状态码 / 各家自定义码 → 统一分类 ═══════
// 现场（2026-11-07 会话 · 总计子代理 #1 `fc370db5`）：
//   `:164` `…[RATE_LIMIT…],500,10000,0.1],retry:1,maxRetries:5,delayMs:491.59`
//          ← 重试预算 maxRetries:5，但实际只用掉 **1 次**就放弃了
//   `:166` 同一原因改报 `QUOTA_EXCEEDED`（当时不在白名单）→ 被判死
//   `:167` 已切到 trae/mimo-v2.6-flash；`:169` 仍然 `turn/end error`
//   `:211` 给用户的通知里**没有错误码**，事后无从判断
//
// 根因两条：
//   ① 同一个"限流"在不同层有不同码（RATE_LIMIT / QUOTA / 429 / "请求过于频繁"），
//      白名单只认字面量 → 换个说法就漏判 → 5 次预算只用 1 次就判死；
//   ② 退避时间写死 491ms，完全没读 provider 返回的 retry-after，
//      立刻撞回同一个限流窗口。
const FATAL_CLASS = {
  RATE_LIMIT: 'rate_limit',
  QUOTA: 'quota',
  CONTEXT: 'context',
  AUTH: 'auth',
  EMPTY: 'empty',
  OTHER: 'other',
};

/**
 * 【A4】把五花八门的错误码/消息归一成一类。纯函数，供 host 与测试共用。
 *
 * 判据顺序（先精确码，再语义）：
 *   ① 直接命中已知码集合；
 *   ② 数字状态码：429→rate_limit，401/403→auth，402→quota，413/400+context→context；
 *   ③ 文本语义：rate limit / 过于频繁 / too many requests / quota / 额度 / 余额…
 *
 * @param {string} code    错误码（可能为空）
 * @param {string} message 错误消息（可能为空）
 * @returns {{class:string, switchNow:boolean, code:string}}
 */
export function classifyModelError(code, message) {
  const c = String(code || '').trim();
  const up = c.toUpperCase();
  const msg = String(message || '');
  const low = msg.toLowerCase();
  const hit = (k) => up === k || up.indexOf(k) >= 0;

  // ① 精确/包含已知码
  if (hit('RATE_LIMIT') || hit('RATE LIMITED') || up === '429') {
    return { class: FATAL_CLASS.RATE_LIMIT, switchNow: true, code: c || 'RATE_LIMIT' };
  }
  if (hit('QUOTA') || up === '402') {
    return { class: FATAL_CLASS.QUOTA, switchNow: true, code: c || 'QUOTA' };
  }
  if (hit('CONTEXT_WINDOW') || hit('CONTEXT_LENGTH') || up === '413') {
    return { class: FATAL_CLASS.CONTEXT, switchNow: true, code: c || 'CONTEXT_WINDOW_EXCEEDED' };
  }
  if (hit('EMPTY_RESPONSE')) {
    return { class: FATAL_CLASS.EMPTY, switchNow: true, code: c || 'EMPTY_RESPONSE' };
  }
  if (up === '401' || up === '403' || hit('UNAUTHORIZED') || hit('INVALID_API_KEY')
      || hit('AUTH')) {
    return { class: FATAL_CLASS.AUTH, switchNow: true, code: c || 'AUTH' };
  }
  // ② 数字状态码兜底（有些适配器只回裸状态码）
  if (/^4\d\d$/.test(c)) {
    return { class: FATAL_CLASS.OTHER, switchNow: true, code: c };
  }
  // ③ 文本语义（没有码、或码不认识，但消息说得很清楚）
  const sayRate = (low.indexOf('rate limit') >= 0 || low.indexOf('too many requests') >= 0
    || msg.indexOf('过于频繁') >= 0 || msg.indexOf('请求频率') >= 0
    || msg.indexOf('限流') >= 0);
  const sayQuota = (low.indexOf('quota') >= 0 || low.indexOf('insufficient') >= 0
    || msg.indexOf('额度') >= 0 || msg.indexOf('余额') >= 0
    || msg.indexOf('欠费') >= 0);
  if (sayRate) return { class: FATAL_CLASS.RATE_LIMIT, switchNow: true, code: c || 'RATE_LIMIT' };
  if (sayQuota) return { class: FATAL_CLASS.QUOTA, switchNow: true, code: c || 'QUOTA' };
  return { class: FATAL_CLASS.OTHER, switchNow: false, code: c };
}

/**
 * 【A4】从错误里取退避时长（毫秒）。优先读 provider 的 retry-after。
 *
 * 现场：`delayMs:491.59` 是写死的，撞限流后 0.5 秒就重试，等于再撞一次。
 * 现在：retry-after（秒或毫秒）> retryAfterMs > delayMs > 指数退避，上限 30s。
 *
 * @param {object} failure  payload.failure
 * @param {number} attempt  第几次重试（从 1 开始）
 */
export function backoffMsOf(failure, attempt) {
  const f = failure || {};
  const MAX = 30000;
  const clamp = (n) => Math.max(0, Math.min(MAX, Math.round(n)));
  // ① retry-after 头（秒），有些适配器塞在 headers / info 里
  const candidates = [];
  const pushIf = (v, unit) => {
    if (v === undefined || v === null) return;
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) candidates.push(unit === 'ms' ? n : n * 1000);
  };
  try {
    const h = (f.headers) || (f.info && f.info.headers) || (f.response && f.response.headers) || {};
    const get = (k) => (typeof h.get === 'function' ? h.get(k) : h[k]);
    pushIf(get('retry-after'), 's');
    pushIf(get('x-ratelimit-reset'), 's');
  } catch (e) {}
  pushIf(f.retryAfterMs, 'ms');
  pushIf(f.retry_after, 's');
  if (Array.isArray(f.delays) && f.delays.length) pushIf(f.delays[0], 'ms');
  pushIf(f.delayMs, 'ms');
  if (candidates.length) {
    // 有多来源取最大（宁可多等一点，也别立刻再撞）
    return clamp(Math.max.apply(null, candidates));
  }
  // ② 指数退避（1s, 2s, 4s …），带抖动
  const n = Math.max(1, Number(attempt) || 1);
  const base = Math.min(MAX, 1000 * Math.pow(2, n - 1));
  return clamp(base + Math.floor(Math.random() * 250));
}

export { FATAL_CLASS };
