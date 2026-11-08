/**
 * turn-guard.js — 【BUG-A1 / A11 / A14 / B1 修复】收尾守卫的"可判定事实"层
 * ==========================================================================
 * 为什么单独一个文件：`lib/index.js` 曾被并行改动【整份覆盖】过两次
 *   （2026-11-07 现场：另一路 agent 用整份 write 重排代码块，本文件的改动连同
 *   语法一起被抹掉）。把"可判定的纯逻辑"放进来，只让 index.js 保留【一条 import
 *   + 几处一行调用】，冲突面从"几百行"降到"一行"，即便被覆盖也能在 30 秒内补回。
 *
 * 本模块只做判定，不做副作用（不 steer、不写盘、不起进程）：
 *   · A1  [WAITING] 终态的客观证据（不是"模型说在等"就信）
 *   · A11 总计子代理（analyst）身份判定，用于选对守卫文案
 *   · A14 由会话 contextWindow 推出的压缩阈值（×0.7，带下限）
 *   · B1  未 settle 后台 job 的提示文案
 *
 * 任何读取失败一律返回"没有证据"/null，让调用方走【保守】分支：
 *   守卫宁可多拦一次，也不能被一句 [WAITING] 骗过去。
 */

import fs from 'node:fs';
import path from 'node:path';

/** 「在等」的证据窗口：房间近 5 分钟内必须有过心跳/报告变化才算还没死。 */
export const WAIT_EVIDENCE_WINDOW_MS = 5 * 60 * 1000;

/** A14：阈值 = contextWindow × 该比例。 */
export const COMPACTION_THRESHOLD_RATIO = 0.7;
/** A14：下限。小窗口（32K/64K）若按比例算会过频压缩，压到 60K 以下没有意义。 */
export const COMPACTION_THRESHOLD_FLOOR = 60000;
/** A14：取不到 contextWindow 时沿用原值（历史上就是 ~100K），并打日志。 */
export const COMPACTION_THRESHOLD_FALLBACK = 100000;

/** 只认【独占一行】的标记（前后仅允许空白），与文档契约一致。 */
export function hasStandaloneMarker(text, marker) {
  if (!text || !marker) return false;
  for (const line of String(text).split(/\r?\n/)) {
    if (line.trim() === marker) return true;
  }
  return false;
}

/** 读 JSON（失败/null 一律返回 null，绝不抛）。 */
export function readJsonSafe(p) {
  try {
    if (!p || !fs.existsSync(p)) return null;
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    return null;
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  A14 · 压缩阈值来自【会话自己的 contextWindow】，不是写死 100K
// ══════════════════════════════════════════════════════════════════════════
// 现场（2026-11-07 会话）：6 次压缩触发点 99,838–100,025 token，
//   而 `:224` 明确记着 `{"type":"request/context", "contextWindow":200000}`。
//   也就是说窗口只用了 50% 就开始压缩 —— 每次压缩都要重写整段摘要、
//   丢掉细节，直接导致"压缩后重复读同一文件""补丁丢失重写第 4 版"。
// 修法：阈值 = contextWindow × 0.7；低于 60K 时抬到 60K（防小窗口过频压缩）。
// 取不到 contextWindow → 回退 100000（历史行为），并把 source 标成 fallback，
//   调用方负责打日志；绝不因为"读不到"就崩或改变其它行为。

/**
 * 从会话事件里取最近一次 request/context 的 contextWindow。
 * 真实日志形状（test/log/session.v4.jsonl:14 / :224）：
 *   {"type":"request/context","seq":222,"data":{"provider":"trae","model":"…","contextWindow":200000}}
 * 另外兼容 header.contextWindow / data.context_window 等写法。
 */
export function latestContextWindow(events, header) {
  try {
    const evs = Array.isArray(events) ? events : [];
    for (let i = evs.length - 1; i >= 0; i--) {
      const ev = evs[i];
      if (!ev || ev.type !== 'request/context') continue;
      const d = ev.data || {};
      const cw = d.contextWindow ?? d.context_window ?? (d.context && d.context.contextWindow);
      const n = Number(cw);
      if (Number.isFinite(n) && n > 0) return Math.floor(n);
    }
    const hc = header && (header.contextWindow ?? header.context_window);
    const hn = Number(hc);
    if (Number.isFinite(hn) && hn > 0) return Math.floor(hn);
  } catch (e) {}
  return null;
}

/**
 * 【BUG-A14 修复·补】复刻 compaction-basic 的真实阈值公式，并指出【是哪一项在压】。
 *
 * 宿主源码（@deepseek-ai/dsh-compaction-basic/lib/index.js:124-147 resolveCompactSpec）：
 *     const messageBudgetTokens  = contextWindow - reservedCompletionTokens;
 *     const pressureBudgetTokens = messageBudgetTokens - policy.headroomTokens;
 *     const thresholdTokens = Math.floor(Math.min(
 *         contextWindow * policy.thresholdRatio,   // ← 比例项
 *         pressureBudgetTokens));                  // ← 预算项
 * 其中 reservedCompletionTokens(header, defaultMaxTokens)
 *     = header.config.maxTokens ?? defaultMaxTokens ?? 0        （同文件:757-759）
 *
 * ⚠️ 这就是本节最要紧的发现：**只调 thresholdRatio 是不够的**。
 *   200K 窗口、headroomTokens=48000、reserved 记为 65536 时：
 *     比例项 = 140000，预算项 = 200000-65536-48000 = 86464 → 实际阈值 86464，
 *     被【预算项】压住，比原来的 ~100K 还低 —— 越改越糟。
 *   而 reserved 实际取的是适配器给的 maxTokens（deepseek 适配器默认 256K，
 *     会直接导致 messageBudget <= 0 抛 TargetPressureConfigError），
 *     所以这一项【必须按真实取值算，不能凭猜】。
 *
 * 本函数只做"算 + 说明"，不改宿主配置（宿主的 config 在构造时 deepFreeze，
 *   运行期改不动）。它的价值是：让"实际生效阈值是多少、被谁压住"第一次可观测。
 *
 * @returns {{limitRatioTokens:number, limitBudgetTokens:number,
 *            limiter:'ratio'|'budget'|'window', threshold:number}}
 */
export function explainCompactionLimit(contextWindow, ratio, headroomTokens, reservedTokens) {
  const cw = Number(contextWindow);
  const r = Number.isFinite(Number(ratio)) && Number(ratio) > 0
    ? Number(ratio) : COMPACTION_THRESHOLD_RATIO;
  const head = Number.isFinite(Number(headroomTokens)) ? Math.max(0, Number(headroomTokens)) : 0;
  const res = Number.isFinite(Number(reservedTokens)) ? Math.max(0, Number(reservedTokens)) : 0;
  if (!Number.isFinite(cw) || cw <= 0) {
    return { limitRatioTokens: null, limitBudgetTokens: null, limiter: 'window', threshold: null };
  }
  const limitRatioTokens = Math.floor(cw * r);
  const limitBudgetTokens = Math.floor(cw - res - head);
  const limiter = limitBudgetTokens < limitRatioTokens ? 'budget' : 'ratio';
  return {
    limitRatioTokens,
    limitBudgetTokens,
    limiter,
    threshold: Math.floor(Math.min(limitRatioTokens, limitBudgetTokens)),
  };
}

/**
 * 解析压缩触发阈值。
 * @returns {{threshold:number, contextWindow:number|null, source:'contextWindow'|'floor'|'fallback', ratio:number}}
 */
export function resolveCompactionThreshold(contextWindow) {
  const cw = Number(contextWindow);
  if (!Number.isFinite(cw) || cw <= 0) {
    return {
      threshold: COMPACTION_THRESHOLD_FALLBACK,
      contextWindow: null,
      source: 'fallback',
      ratio: COMPACTION_THRESHOLD_RATIO,
      exceedsWindow: false,
    };
  }
  const cwInt = Math.floor(cw);
  const scaled = Math.floor(cw * COMPACTION_THRESHOLD_RATIO);
  if (scaled < COMPACTION_THRESHOLD_FLOOR) {
    // ⚠️ 下限是"防止小窗口过频压缩"，但窗口本身比下限还小时，
    //   这个阈值就【永远达不到】—— 压缩变成死配置，上下文会一路涨到
    //   CONTEXT_WINDOW_EXCEEDED。所以必须把这种退化情形如实标出来
    //   （exceedsWindow），让面板/日志能一眼看见，而不是静默地"配了个不生效的值"。
    return {
      threshold: COMPACTION_THRESHOLD_FLOOR,
      contextWindow: cwInt,
      source: 'floor',
      ratio: COMPACTION_THRESHOLD_RATIO,
      exceedsWindow: COMPACTION_THRESHOLD_FLOOR >= cwInt,
    };
  }
  return {
    threshold: scaled,
    contextWindow: cwInt,
    source: 'contextWindow',
    ratio: COMPACTION_THRESHOLD_RATIO,
    exceedsWindow: false,
  };
}

// ══════════════════════════════════════════════════════════════════════════
//  A11 · 总计子代理（analyst）身份 —— 守卫必须先判角色再选文案
// ══════════════════════════════════════════════════════════════════════════
// 现场（2026-11-07 会话 · 总计子代理 `d921be74`）：
//   `:497`/`:517` 守卫对它说"你是【建模/专项子代理】…请调 sw_bridge/room-report"，
//   而它已经完成 10/10 阶段、任务书明写"禁调 sw_bridge" → 空转 5 步、53 秒后才收工。
// 根因：守卫只有"主对话/子代理"两套文案，analyst 被算进了建模小屋。
// 身份来源（唯一权威）：mode2_flow.py analyst-bind 写进 mode2_plan.json 的
//   `plan.analyst.session_id`（见 mode2_flow.py:1263-1334）。
// 诊断位置：mode2_plan.json 与 mode_state 同目录层级 —— 优先 workflow_state.work_dir，
//   其次 toolsDir 本身、toolsDir/load_cases（mode2_flow._plan_dir 的默认落点）。
// 取不到 → 返回 {sessionId:null}，调用方【保守回退】建模小屋文案（宁可发错，
//   也不能因为读不到状态就不再拦）。

/** mode2_plan.json 的候选路径（按权威性排序，去重）。 */
export function planPathCandidates(toolsDir, workflowState) {
  const out = [];
  const push = (p) => { try { if (p && out.indexOf(p) < 0) out.push(p); } catch (e) {} };
  try {
    const wd = workflowState && workflowState.work_dir;
    if (wd) push(path.join(String(wd), 'mode2_plan.json'));
    if (toolsDir) {
      push(path.join(String(toolsDir), 'mode2_plan.json'));
      push(path.join(String(toolsDir), 'load_cases', 'mode2_plan.json'));
    }
  } catch (e) {}
  return out;
}

/**
 * 读 analyst 的 sessionId。
 * @returns {{sessionId:string|null, source:string, planPath:string|null, taskId:string|null}}
 */
export function readAnalystSessionId(toolsDir, workflowState) {
  for (const p of planPathCandidates(toolsDir, workflowState)) {
    const plan = readJsonSafe(p);
    if (!plan) continue;
    const a = plan.analyst;
    if (!a || typeof a !== 'object') continue;
    const sid = String(a.session_id || a.sessionId || '').trim();
    if (!sid) continue;
    return {
      sessionId: sid,
      source: 'mode2_plan.analyst.session_id',
      planPath: p,
      taskId: plan.task_id ? String(plan.task_id) : null,
    };
  }
  return { sessionId: null, source: 'unavailable', planPath: null, taskId: null };
}

/**
 * 该 agent 是不是总计子代理。
 * 判据：它的 sessionId / 父会话 id 与 mode2_plan.analyst.session_id 相等。
 * 只做【正向】判定：拿不到身份一律 false（回退建模小屋文案）。
 */
export function isAnalystAgent(agent, toolsDir, workflowState) {
  try {
    const info = readAnalystSessionId(toolsDir, workflowState);
    if (!info.sessionId) return { isAnalyst: false, info };
    const ids = [];
    if (agent && agent.id) ids.push(String(agent.id));
    if (agent && agent.sessionId) ids.push(String(agent.sessionId));
    if (agent && agent.session && agent.session.id) ids.push(String(agent.session.id));
    return { isAnalyst: ids.indexOf(info.sessionId) >= 0, info };
  } catch (e) {
    return { isAnalyst: false, info: { sessionId: null, source: 'error' } };
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  A1 · 「在等」必须是代码可判的事实，不是模型说了算
// ══════════════════════════════════════════════════════════════════════════
// 用户要求原文：「判据必须是代码可判的，不是模型说什么就信什么」。
// 因此正文单独一行 [WAITING] 只是【必要条件】，还须命中至少一条客观证据：
//
//   ① 仍有 active 房间，且【近 5 分钟内心跳/报告有变化】
//      —— 为什么要求"有变化"：active 但半小时没动静的房间不是"在等"，是"卡住了"，
//         那种情况守卫必须继续催处置，绝不能被 [WAITING] 放过去。
//   ② 有未答的提问卡（人没答 → 模型除了等没有别的选择）
//   ③ workflow_state 挂了 pending / awaiting 标记（门禁侧的显式等待）
//   ④ 该会话仍有未 settle 的后台 job（b55576ac 那次"日志 0 字节但还在建"）
//
// 四条都拿不到 → waiting:false，守卫照旧 steer。

/** 只认【本轮任务】的房间（与 index.js detectTaskFinished 同一口径）。 */
export function activeRoomNamesOf(modeState, workflowState) {
  try {
    const rooms = (modeState && modeState.rooms) || {};
    const wfTid = (workflowState && workflowState.task_id) ? String(workflowState.task_id) : '';
    return Object.keys(rooms).filter((n) => {
      const rec = rooms[n] || {};
      if (!rec.active) return false;
      const rt = rec.task_id;
      if (!rt || !wfTid) return true;         // 无从判断 → 保守按本轮
      return String(rt) === wfTid;
    });
  } catch (e) {
    return [];
  }
}

/**
 * 房间"还在动"的证据：心跳文件或报告文件的 mtime 落在窗口内。
 * 文件名约定来自 mode_gate.py：heartbeats/<房间>.heartbeat、reports/<房间>.report.json
 * @returns {string[]} 形如 ["结构件:车架结构间.heartbeat", ...]
 */
export function roomActivityEvidence(toolsDir, roomNames, nowMs, windowMs) {
  const now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
  const win = Number.isFinite(Number(windowMs)) ? Number(windowMs) : WAIT_EVIDENCE_WINDOW_MS;
  const seen = [];
  if (!toolsDir) return seen;
  for (const room of (Array.isArray(roomNames) ? roomNames : [])) {
    const name = String(room || '').trim();
    if (!name) continue;
    // 与 mode_gate._safe_room_name 对齐：保留中英文数字与 空格_-.
    const safe = name.replace(/[^\w\u4e00-\u9fa5 .:\-]/g, '').trim();
    if (!safe) continue;
    const cands = [
      path.join(String(toolsDir), 'heartbeats', safe + '.heartbeat'),
      path.join(String(toolsDir), 'reports', safe + '.report.json'),
    ];
    for (const p of cands) {
      try {
        if (!fs.existsSync(p)) continue;
        const st = fs.statSync(p);
        if ((now - st.mtimeMs) <= win) {
          seen.push(name + ':' + path.basename(p));
          break;
        }
      } catch (e) {}
    }
  }
  return seen;
}

/**
 * 汇总"在等"的客观证据。
 *
 * @param {object} o
 * @param {string} [o.toolsDir]        工程模式状态目录
 * @param {object} [o.modeState]       mode_state.json 内容
 * @param {object} [o.workflowState]   workflow_state.json 内容
 * @param {number} [o.openQuestions]   未答提问卡数量
 * @param {number} [o.unsettledJobs]   未 settle 后台 job 数量
 * @param {number} [o.nowMs]           注入时钟（测试用）
 * @param {number} [o.windowMs]        证据窗口（测试用）
 * @returns {{waiting:boolean, evidence:string[]}}
 */
export function collectWaitingEvidence(o) {
  const opts = o || {};
  const evidence = [];
  try {
    const active = activeRoomNamesOf(opts.modeState, opts.workflowState);
    if (active.length) {
      const live = roomActivityEvidence(opts.toolsDir, active, opts.nowMs, opts.windowMs);
      if (live.length) {
        const mins = Math.round((Number.isFinite(Number(opts.windowMs))
          ? Number(opts.windowMs) : WAIT_EVIDENCE_WINDOW_MS) / 60000);
        evidence.push('active 房间 ' + active.length + ' 个，近 ' + mins
          + ' 分钟内有心跳/报告变化：' + live.slice(0, 4).join(','));
      }
    }
  } catch (e) {}

  try {
    const wf = opts.workflowState;
    if (wf) {
      const pend = wf.pending || wf.pending_actions || wf.pending_stage;
      if (pend && (!Array.isArray(pend) || pend.length)) {
        evidence.push('workflow_state 有 pending 标记：'
          + (Array.isArray(pend) ? pend.length + ' 项' : String(pend).slice(0, 60)));
      }
      const awaiting = wf.awaiting || wf.waiting_for;
      if (awaiting) evidence.push('workflow_state awaiting=' + String(awaiting).slice(0, 60));
    }
  } catch (e) {}

  try {
    const n = Number(opts.unsettledJobs);
    if (Number.isFinite(n) && n > 0) evidence.push('该会话仍有 ' + n + ' 个后台 job 未 settle');
  } catch (e) {}

  try {
    const q = Number(opts.openQuestions);
    if (Number.isFinite(q) && q > 0) evidence.push('有 ' + q + ' 张未答的提问卡（等用户作答）');
  } catch (e) {}

  return { waiting: evidence.length > 0, evidence };
}

// ══════════════════════════════════════════════════════════════════════════
//  B1 · 未 settle 后台 job 的可读提示
// ══════════════════════════════════════════════════════════════════════════
/**
 * 把 pending 列表拍成一行人话：`有 2 个后台 job 未完成：run6.log 建模、pwsh-…`。
 * 名字缺失时退到 job id；id 也缺失时退到序号（绝不输出 undefined）。
 */
export function describePendingJobs(pending) {
  const list = Array.isArray(pending) ? pending.filter(Boolean) : [];
  if (!list.length) return '';
  const names = list.map((j, i) => {
    const label = j && (j.label || j.name);
    if (label) return String(label);
    const id = j && (j.id || j.jobId);
    if (id) return String(id);
    return '#' + (i + 1);
  });
  return '有 ' + list.length + ' 个后台 job 未完成：' + names.slice(0, 6).join('、')
    + (names.length > 6 ? ' 等' : '');
}
