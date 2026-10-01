// dsh-engineering-ui — Host 半 v5
// 数据接口：子代理树 / 原始事件流 / 阻塞式代问 / turn-stopping 守卫。
//
// 设计要点：
//  1. 事件以原始形态下发（seq/time/type/data），字段映射由 client 半负责，
//     这样字段名修正只需刷新页面（HMR），无需重启 DSH。
//  2. 【Bug1 根因】子代理无权向用户提问。DSH 的 userQuestions.ask() 会校验
//     `agents.roots().includes(agent)`，子代理不在 roots 中 → 必抛
//     DELEGATED_CALLER。因此子代理的 ask_user_question 永远失败。
//     旧版用 subagents.followup 把答案当"下一轮消息"发回去 —— 那是新指令，
//     不是工具返回值，子代理会把确认当成新任务，造成"自问自答/消息错乱"。
//  3. 【Bug1 修复】改为主代理代问：提问路由到主代理（root），由主代理调
//     ctx.userQuestions.ask({ agent: 主代理 })，该方法【真正阻塞挂起】，
//     用户在前端作答后 resolve → 答案以 tool_result 形式绑定到那次工具调用。
//  4. 【问题3】用 agent/turn-stopping 钩子 + agent.steer() 阻止对话提前结束：
//     仅对 engineering 预设生效（见 GUARD_PRESET）。
import { createUserMessage } from '@deepseek-ai/dsh-llm';
// ── DSH 0.2.0 兼容改造（兼容性清单 第 2 项）────────────────────────────────
// 旧包名 @deepseek-ai/dsh-agent-presets 在 0.2.0 安装体中已不存在：
//   · 0.2.0 提供的是 @deepseek-ai/dsh-agent-preset-registry（+ dsh-agent-preset）。
//   · 该包导出的 agentPresetProjectionDefinition 与旧包同名同形
//     （init(header) / apply(state, event)，见 registry/src/session.ts），
//     所以这里只换模块说明符，投影语义完全不变（最小改动）。
// 若静态 import 求值失败，整个 Host 插件会挂掉；因此 resolveSessionPreset
// 额外保留一条纯本地兜底路径（不依赖任何外部包），保证最坏情况下仍能判预设。
import * as agentPresets from '@deepseek-ai/dsh-agent-preset-registry';
// 【Bug3 修复】守卫需要读门禁状态文件来判断"任务是否已完成"。
// 用静态 import 最可靠（apply 是同步函数，不能用 await import）。
import fs from 'node:fs';
import path from 'node:path';

// 纯本地兜底：最后一次 agent-preset/selected，否则 header.agentPreset。
function resolvePresetFallback(header, events) {
  const evs = Array.isArray(events) ? events : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
      return String(ev.data.agentPreset);
    }
  }
  return (header && header.agentPreset) ?? null;
}

// Current session preset: the registry projection over immutable event snapshots.
function resolveSessionPreset({ header, events }) {
  if (typeof agentPresets?.resolveSessionPreset === 'function') {
    return agentPresets.resolveSessionPreset({ header, events });
  }
  const projection = agentPresets?.agentPresetProjectionDefinition;
  const evs = Array.isArray(events) ? events : [];
  if (!projection || typeof projection.apply !== 'function') {
    return resolvePresetFallback(header, evs);
  }
  try {
    return evs.reduce((state, event) => projection.apply(state, event), projection.init(header));
  } catch (e) {
    return resolvePresetFallback(header, evs);
  }
}

function sessionEvents(session) {
  return typeof session?.snapshotEvents === 'function'
    ? session.snapshotEvents() : session?.events;
}

export const name = 'dsh-engineering-ui';
// 【必须声明 userQuestions】阻塞式代问依赖 ctx.userQuestions.ask()；
// cordis 的依赖注入要求服务名必须列在 inject 里，否则 ctx.userQuestions 为
// undefined，/ask 会直接失败（"userQuestions 服务不可用"）。
export const inject = ['webServer', 'subagents', 'sessionPersistence', 'sessions', 'agents', 'userQuestions'];
const PREFIX = '/dsh-engineering-ui';
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
// 【Bug2】守卫只对这一个预设的会话生效。原先无差别 steer 所有对话 → 到处都不给结束。
const GUARD_PRESET = 'engineering';
// 高频低值事件：不入前端流（流式 chunk 每token一条，会挤满窗口）
const RAW_DROP = new Set(['assistant/chunk', 'step/start', 'step/end', 'request/header']);

// 【问题3】阻止对话提前结束
// 【Bug3 修复】原文案过于刚性：只声明"禁止结束"，却没说清"什么情况算完成"。
// 导致 AI 明明已经交付工件（图纸已导出、验证已通过），仍被反复拉回来，
// 用户看到的是"活干完了却还在自说自话继续干"。
// 新文案：明确列出"可以结束"的正向判据，并给出收尾动作，
// 让守卫从"阻止结束"变成"推动收尾"。
// 【Bug4 修复】提示词必须与守卫的检测逻辑一致。
// 原提示词承诺"命中 A/B/C 任一条即可结束"，但守卫当时只认文件，
// 模型按 A 条口头声明完成时检测不到 → 被反复拉回来。
// 现在两侧对齐：提示词明确告知"系统会扫描你的输出文本"，
// 模型只要把完成状态讲清楚（含交付物与路径）就会被放行。
// ── 【PhaseA 修复】守卫提示文案与新判定逻辑严格对齐 ────────────────────
// 旧文案承诺"写清完成状态就会放行"，但判定已改为【只认代码级终态】，
// 文案若不同步，模型会反复尝试"用文字声明完成"却始终被拉回来 —— 死循环。
// 新文案明确告知：能否结束取决于【门禁状态文件】，不取决于你的措辞。
const GUARD_TEXT = [
  '【系统提示 · 工程模式守卫】',
  '你正准备结束本轮，但系统尚未检测到【代码级终止态】，因此本轮不予结束。',
  '',
  '⚠️ 注意：本守卫【不看你说了什么】——"已完成/已生成/已导出"这类措辞一律无效，',
  '   必须让门禁状态文件真正进入终态，系统才会放行。',
  '',
  '放行的唯一途径（任一即可）：',
  '  1) 走完 workflow_gate 收尾流程，使 workflow_state.json 的 step 变为 finished；',
  '  2) 让所有房间都 room-end / room-fail，使 mode_state.json 中无 active 房间；',
  '  3) 若任务确实全部结束且无需再操作，在正文【单独一行】写出 [TASK_DONE]。',
  '',
  '若任务尚未完成，请继续调用工具推进下一步（room-start / sw_bridge 建模 /',
  '  room-report 上报等），而不是仅用文字描述进展。',
  '',
  '若用户已明确要求停止（"停 / 够了 / 不用了 / 就这样"），直接结束即可。'
].join('\n');

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(text);
}

function queryOf(req) {
  try {
    return new URL(req.url, 'http://localhost').searchParams;
  } catch {
    return new URLSearchParams();
  }
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 2000000) req.destroy();
    });
    req.on('end', () => resolve(data));
    req.on('error', () => resolve(''));
  });
}

// ── DSH 0.2.0 兼容改造（兼容性清单 第 4 项）────────────────────────────────
// 0.2.0 的 SessionPersistence 合同是 create/open/flush/stat/list，
// 【已移除 inspect()】。冷读取必须走：
//     const h = await ctx.sessionPersistence.open(id, 'read')
//     const { eventState, events } = await h.read()
//     await h.close()
// handle 自带 header 与 inheritedEventCount，取代旧的 meta / inheritedEventCount。
/**
 * 冷读一个会话的持久化日志。
 * @returns {{header:object, events:Array, inheritedEventCount:number}|null}
 */
async function readSessionLog(ctx, sessionId) {
  let handle = null;
  try {
    handle = await ctx.sessionPersistence.open(sessionId, 'read');
    const header = handle.header || {};
    const inheritedEventCount = Number(handle.inheritedEventCount ?? 0) || 0;
    const res = await handle.read();
    const events = (res && res.events) || [];
    return { header, events, inheritedEventCount };
  } catch (e) {
    // 回退：stat() 只读元数据，不读日志（也用于日志本身损坏/未物化的场景）
    try {
      const snap = await ctx.sessionPersistence.stat(sessionId);
      if (snap && snap.header) {
        return { header: snap.header, events: [], inheritedEventCount: 0 };
      }
    } catch (e2) {}
    return null;
  } finally {
    // close() 幂等；读 handle 关闭只释放本地资源
    try { if (handle) await handle.close(); } catch (e) {}
  }
}

/** 从持久化事件里找出某会话的父会话 ID。 */
async function parentOf(ctx, childId) {
  // 首选：0.2.0 的 header.parentSession —— 就是 fork 谱系（子代理的父会话）
  try {
    const snap = await ctx.sessionPersistence.stat(childId);
    const h = snap && snap.header;
    if (h && h.parentSession) return String(h.parentSession);
  } catch (e) {}
  // 次选：冷读日志，找 request/header 里的 header.parentSession（兼容旧日志）
  try {
    const log = await readSessionLog(ctx, childId);
    if (log) {
      for (const ev of log.events) {
        if (ev.type === 'request/header' && ev.data && ev.data.header) {
          const p = ev.data.header.parentSession;
          if (p) return String(p);
        }
      }
      if (log.header && log.header.parentSession) return String(log.header.parentSession);
    }
  } catch (e) {}
  return null;
}

export function apply(ctx) {
  const disposers = [];

  // ── 【Bug2】会话预设解析（同步缓存，供 turn-stopping 钩子零延迟查询）──────
  // 钩子必须同步返回，不能 await，所以这里预先把 sessionId → preset 缓存起来；
  // 缓存未命中时给一次异步回填，本次按"非工程模式"放行（宁可漏守一次，
  // 也绝不能像旧版那样把非工程会话也拦住）。
  const presetCache = new Map(); // sessionId -> preset id | null

  async function readPreset(sessionId) {
    if (!sessionId) return null;
    if (presetCache.has(sessionId)) return presetCache.get(sessionId);
    try {
      // 【0.2.0 兼容】inspect() 已移除 → open/read/close（见 readSessionLog）。
      const log = await readSessionLog(ctx, sessionId);
      if (!log) return null;
      const header = log.header || {};
      const events = log.events || [];
      // resolveSessionPreset 需要 { header, events }：取最新的 agent-preset/selected，
      // 没有该事件时才回落到头部创建时的值。
      const resolved = resolveSessionPreset({ header, events });
      // 【问题5 修复】同样只接受白名单内的预设 ID，避免把脏值写进缓存
      const raw = resolved === undefined || resolved === null ? null : String(resolved);
      const value = isKnownPreset(raw) ? raw : (raw === null ? null : raw);
      presetCache.set(sessionId, value);
      return value;
    } catch (e) {
      return null;
    }
  }

  // ── 【PhaseA 修复】冷缓存不再无条件放行 ──────────────────────────────
  // 原实现：缓存未命中时直接 return false（放行），只异步回填。
  //   后果：新建会话/刚切换预设时，前若干次 turn-stopping 全部放行，
  //   守卫在最需要它的开局阶段完全缺席 —— 用户反馈"又失灵了"。
  // 修复：改用【同步可用的事实】兜底判定：
  //   1) 缓存命中 → 用缓存值（最准）
  //   2) 缓存未命中 → 看 agent 自身的 preset 字段（宿主通常已挂在 agent 上）
  //   3) 再看会话事件里是否有 agent-preset/selected = engineering
  //   4) 最后才回退"不作为"（仅当三条都取不到证据时，避免误拦非工程会话）
  // 同时异步回填缓存，让后续判定走最快路径。
  // ── 【问题5 修复】已知预设白名单 ──────────────────────────────────────
  // 只有落在白名单里的值才被当作"预设 ID"。原实现会把 agent 上任意
  // 非空的 preset 类字段直接当成预设 ID —— 一旦该字段承载的是别的东西
  // （如 "default"、权限名、undefined 转换来的字符串），就会误判成
  // 非 engineering 而放行，或反过来。白名单从根上消除这类误判。
  const KNOWN_PRESETS = new Set([
    'engineering', 'sw-single-line', 'default', 'default-preset',
  ]);
  function isKnownPreset(v) {
    return typeof v === 'string' && KNOWN_PRESETS.has(v);
  }

  function syncPresetOf(agent) {
    const session = agent && agent.session;
    if (session && typeof session.snapshotEvents === 'function') {
      return resolveSessionPreset({ header: session.header || {}, events: sessionEvents(session) });
    }
    // (2) agent 自身的预设字段（不同 DSH 版本字段名略有差异）
    try {
      const cands = [agent && agent.agentPreset, agent && agent.preset,
                     agent && agent.session && agent.session.agentPreset,
                     agent && agent.session && agent.session.header &&
                       agent.session.header.agentPreset];
      for (const c of cands) {
        if (c === undefined || c === null) continue;
        const s = String(c);
        // 只认白名单里的值；未知值视为"证据不足"，继续往后找
        if (isKnownPreset(s)) return s;
      }
    } catch (e) {}
    // (3) 会话事件里的 agent-preset/selected（取最后一条）
    try {
      const evs = sessionEvents(agent && agent.session) || [];
      for (let i = evs.length - 1; i >= 0; i--) {
        const ev = evs[i];
        if (ev && ev.type === 'agent-preset/selected') {
          const v = ev.data && ev.data.agentPreset;
          if (v) return String(v);
        }
      }
    } catch (e) {}
    return null;
  }

  // ── 【问题5 修复】守卫作用域二次确认 ──────────────────────────────────
  // 用户在非工程模式看到守卫弹出（越线）。根因有二：
  //   ① 缓存键用 agent.sessionId || agent.id，不同会话可能拿到同一个 id，
  //      导致工程会话的 'engineering' 被错误地复用给非工程会话；
  //   ② syncPresetOf 扫 agent.session.events 时，可能读到该对象上
  //      残留的【其他会话】的 agent-preset/selected 事件。
  // 修复策略（从严）：只有当【所有可用证据都一致指向 engineering】时才拦截；
  //   只要出现任何一条证据明确指向"非工程"，立刻判为非工程并放行。
  function isGuardSession(agent) {
    const sessionId = (agent && (agent.sessionId || agent.id)) || null;
    if (!sessionId) return false;

    // 证据收集（同步可用的事实）
    const sync = syncPresetOf(agent);

    if (sync !== null && sync !== undefined) {
      presetCache.set(sessionId, sync);
      return sync === GUARD_PRESET;
    }

    if (presetCache.has(sessionId)) {
      const cached = presetCache.get(sessionId);
      // 缓存与实时证据矛盾 → 以实时证据为准并纠正缓存（防脏缓存长期生效）
      if (cached === GUARD_PRESET && sync && sync !== GUARD_PRESET) {
        presetCache.set(sessionId, sync);
        return false;                       // 明确非工程 → 放行
      }
      return cached === GUARD_PRESET;
    }

    if (sync) {
      presetCache.set(sessionId, sync);
      return sync === GUARD_PRESET;
    }
    // 证据不足 → 异步回填，本次放行（宁漏勿误拦非工程会话）
    readPreset(sessionId).catch(() => {});
    return false;
  }

  // ── 【问题3】turn-stopping 守卫：阻止对话提前结束 ────────────────────────
  // 【Bug3 修复】原实现只判断"是不是 engineering 会话"，完全不看任务是否已完成。
  // 后果：AI 已经交付工件、走完收尾流程，守卫仍反复 steer 把它拉回来继续干，
  // 用户看到"活儿干完了却还在自说自话"。
  //
  // 现新增【完成信号检测】：命中任一信号即放行（不再 steer）。
  // 检测源（按可靠性排序）：
  //   ① workflow_state.json 的 step == "finished"（代码级终态，最权威）
  //   ② mode_state.json 里所有房间 ended_at 均有值（全房间收尾完毕）
  //   ③ 最近若干条助手消息里命中收尾关键词（兜底，如"已交付/已完成/文件路径"）
  //   ④ 用户消息里出现明确的停止指令
  // 【Bug3 修复】fs/path 已在文件顶部静态导入（见 import 段）。
  // 若打包环境导致导入失败，下面所有读取都会走 catch 分支，
  // 自动降级为"仅关键词兜底"，不影响主流程。

  const STOP_WORDS = ['停止', '停下', '不用了', '别做了', '够了', '结束吧', '到此为止',
                      '不用继续', '可以了', '就这样', 'stop', 'enough', 'cancel'];
  // ── 【PhaseA 修复·守卫失灵根因】关键词放行表已整体废弃 ─────────────────
  // 原实现用一张"交付性关键词"表判定任务完成（'已生成'/'已导出'/'DWG'/'PDF'
  //   /'已保存' …），命中即放行。这在工程对话里等于【永远放行】：
  //   建模过程中每一轮都会出现"零件已生成""导出 DWG"这类词，
  //   守卫因此形同虚设 —— 这正是用户反馈"不能自行结束的守卫又失灵了"的真凶。
  //
  // 用户明确决策：守卫改为【严格模式 · 只认代码级终态】。
  //   · 允许结束的唯一条件：门禁状态文件里的机器可判定终态
  //       (1) TASK_FINISHED.json 标记
  //       (2) workflow_state.json 的 step == 'finished'
  //       (3) mode_state.json 全部房间 ended/failed 且无 active
  //   · 模型嘴上说"已完成"【不再】作为放行依据（避免自说自话骗过守卫）
  //   · 仅保留一个零歧义的显式开关 [TASK_DONE] / [任务完成]，
  //     它必须由模型在正文单独写出，且仍受"工程模式"作用域约束
  //   · 用户明确的停止指令仍然放行（人的意志高于一切）
  //
  // 说明：DONE_WORDS / WRAPUP_WORDS 保留为空数组仅为兼容旧引用点，
  //   实际判定逻辑见 detectTaskFinished()，不再遍历它们。
  const DONE_WORDS = [];
  const WRAPUP_WORDS = [];

  /** 读取门禁状态文件（失败返回 null）。 */
  function readJsonSafe(p) {
    try {
      if (!fs || !path || !p) return null;
      if (!fs.existsSync(p)) return null;
      return JSON.parse(fs.readFileSync(p, 'utf8'));
    } catch (e) {
      return null;
    }
  }

  /** 定位工程模式的 tools 目录（含 workflow_state.json / mode_state.json）。
   *
   * 【PhaseA 修复】新增 DSH_HOME 支持与候选目录扩充，并缓存结果。
   *  原实现只认 USERPROFILE/.dsh/...，当用户自定义了 DSH_HOME 时定位失败，
   *  detectTaskFinished 会误以为"没有门禁终态"从而一直拦住任务（或反之）。
   */
  let toolsDirCache = null;
  function findToolsDir() {
    if (toolsDirCache) {
      try { if (fs && fs.existsSync(toolsDirCache)) return toolsDirCache; } catch (e) {}
      toolsDirCache = null;
    }
    const home = process.env.USERPROFILE || process.env.HOME || '';
    const dshHome = process.env.DSH_HOME || (home ? path.join(home, '.dsh') : '');
    const cands = [
      dshHome && path.join(dshHome, '.agent-presets', 'engineering', 'tools'),
      dshHome && path.join(dshHome, 'engineering', 'tools'),
      home && path.join(home, '.dsh', '.agent-presets', 'engineering', 'tools'),
      home && path.join(home, '.dsh', 'engineering', 'tools'),
    ].filter(Boolean);
    for (const c of cands) {
      try {
        if (fs && fs.existsSync(c)) { toolsDirCache = c; return c; }
      } catch (e) {}
    }
    return null;
  }

  /**
   * 判断任务是否已真正完成（用于放行）。
   * 返回 {done: bool, reason: string}
   */
  function detectTaskFinished(agent) {
    try {
      // ── 【BUG-B 修复】[TASK_DONE] 必须是【零条件】的放行开关 ────────────
      // 测试反馈：守卫提示写明三条放行途径"任一即可"，但原实现把
      //   "有 active 房间 → 拦截" 的早返回放在 [TASK_DONE] 判断【之前】，
      //   于是三者变成了 AND 关系 —— 只要还有活动房间，[TASK_DONE] 永远无效。
      // 更糟的是：清空房间需要 room-end，而 room-end 是【编排命令】，
      //   子代理被明令禁止调用 → 子代理陷入彻底死锁（既不能用标记放行、
      //   又不能自己清房间）。
      //
      // 修复：把 [TASK_DONE] 提到所有房间/门禁检查【之前】，作为最高优先级
      //   的显式开关。它本就是模型主动声明的"我做完了"，必须无条件生效。
      const _textEarly = collectRecentAssistantText(agent, 60);
      if (_textEarly && _textEarly.indexOf('[TASK_DONE]') >= 0) {
        return { done: true, reason: '检测到显式完成标记 [TASK_DONE]（最高优先级，无条件放行）' };
      }

      const toolsDir = findToolsDir();
      if (toolsDir) {
        // ① 显式完成标记（最权威、最直接）
        const mk = readJsonSafe(path.join(toolsDir, 'TASK_FINISHED.json'));
        if (mk && mk.finished) {
          return { done: true, reason: 'TASK_FINISHED 标记：' + (mk.finished_at || '') };
        }
        // ② 门禁状态机：finished 是代码级终态
        const wf = readJsonSafe(path.join(toolsDir, 'workflow_state.json'));
        // ── 【守卫修复·误拦】补全所有【非进行中】的门禁状态 ──────────────
        // 原实现只认 step === 'finished'。但 reset 后 step 为 'idle'
        //   （以及初始化前的 ''/undefined），这些同样是【没有进行中的任务】，
        //   守卫却把它们当成"任务未完成"→ 反复拦截，形成死锁：
        //   用户已经收工，但守卫要求必须有 finished，而 reset 又不会给 finished。
        // 修复：把 idle / 空 / 未开始 一并视为可放行（任务确实没在进行）。
        const _step = wf && wf.step !== undefined && wf.step !== null ? String(wf.step) : '';
        if (wf && (wf.step === 'finished' || wf.finished_at)) {
          return { done: true, reason: 'workflow_gate 已收尾（step=finished）' };
        }
        // ── 【守卫修复】先做"是否有活动房间"的硬检查 ──────────────────────
        // 顺序很重要：房间活跃性是最强信号，必须【先于】idle 放行判断。
        //   否则出现边界漏洞：step=idle 但房间仍在跑（状态被误置/竞态）时，
        //   守卫会漏放正在进行的任务。
        const _msEarly = readJsonSafe(path.join(toolsDir, 'mode_state.json'));
        const _roomsEarly = (_msEarly && _msEarly.rooms) || {};
        const _namesEarly = Object.keys(_roomsEarly);
        const _anyActiveEarly = _namesEarly.some((n) => (_roomsEarly[n] || {}).active);
        if (_anyActiveEarly) {
          return { done: false, reason: '仍有 active 房间，任务在进行中' };
        }
        if (wf && (_step === 'idle' || _step === '')) {
          return { done: true, reason: '门禁处于未开始/已重置状态（step=' + _step + '），且无活动房间' };
        }
        // ② 全部房间均已结束
        const ms = readJsonSafe(path.join(toolsDir, 'mode_state.json'));
        if (ms) {
          const rooms = ms.rooms || {};
          const names = Object.keys(rooms);
          // ── 【守卫修复·误拦】空房间表 = 没有进行中的房间 ────────────────
          // 原条件要求 names.length > 0 才判定，导致 rooms-reset 清空后
          //   （rooms = {}）直接跳过，判定为"未完成"→ 永久拦截。
          //   而"没有任何房间"恰恰是最明确的"没有活儿在进行"。
          //   注意：仅在门禁不在 known 进行中状态时才放行，避免误放。
          const _wfBusy = (_step && _step !== 'idle' && _step !== 'finished');
          if (names.length === 0) {
            if (!_wfBusy) {
              return { done: true, reason: '无任何房间记录且门禁非进行中（已收工）' };
            }
          } else {
            const allEnded = names.every((n) => {
              const r = rooms[n] || {};
              return r.ended_at || r.failed_at;
            });
            const anyActive = names.some((n) => (rooms[n] || {}).active);
            if (allEnded && !anyActive) {
              return { done: true, reason: '全部房间已收尾（ended/failed）' };
            }
          }
        }
      }
      // ── 【PhaseA 修复 / BUG-B 修复】关键词放行已删除 ───────────────────
      // 只保留【零歧义的显式开关】[TASK_DONE]，且它已在函数开头判定完毕
      // （见上方 BUG-B 注释：必须无条件生效，不能被房间状态挡住）。
      // 其余"已生成/已导出/完成"等自然语言一律【不再】放行 —— 它们在
      // 工程对话里每轮都出现，正是守卫失灵的根因。
      // 注：[任务完成] 这个中文标记过于口语化，极易被普通叙述命中
      //     （比如"任务完成度 60%"），已从放行条件中移除，只保留英文标记。
    } catch (e) {}
    return { done: false, reason: '' };
  }

  /**
   * 【Bug4 修复】稳健地抽取最近若干条助手文本。
   *
   * 真实事件里正文可能出现在多个位置（text / content / message.content[]），
   * 原实现只认 ev.data.text，导致大量消息读不到 → 检测永远失败，
   * 这正是"AI 说了完成、守卫却检测不到"的直接原因。
   *
   * 同时兼容三种取事件的方式：
   *   agent.session.events（官方 Session getter，最可靠）
   *   agent.events / agent.session.messages（兼容旧形态）
   */
  function collectRecentAssistantText(agent, limit) {
    let events = null;
    try {
      if (agent && agent.session && Array.isArray(sessionEvents(agent.session))) {
        events = sessionEvents(agent.session);
      } else if (agent && Array.isArray(agent.events)) {
        events = agent.events;
      } else if (agent && agent.session && Array.isArray(agent.session.messages)) {
        events = agent.session.messages;
      }
    } catch (e) {
      events = null;
    }
    if (!events || !events.length) return '';

    const pick = (node) => {
      if (node === null || node === undefined) return '';
      if (typeof node === 'string') return node;
      if (Array.isArray(node)) return node.map(pick).filter(Boolean).join('\n');
      if (typeof node !== 'object') return '';
      // 内容块数组：[{type:'text', text:'...'}]
      if (Array.isArray(node.parts)) {
        const inner = pick(node.parts);
        if (inner) return inner;
      }
      const direct = node.text !== undefined ? node.text
                   : (node.content !== undefined ? node.content : node.message);
      if (typeof direct === 'string') return direct;
      if (direct && direct !== node) {
        const inner = pick(direct);
        if (inner) return inner;
      }
      return '';
    };

    const parts = [];
    const tail = events.slice(-limit);
    for (let i = tail.length - 1; i >= 0 && parts.length < 12; i--) {
      const ev = tail[i];
      if (!ev) continue;
      // 只取助手产出（避免把用户的话误判成交付声明）
      const t = String(ev.type || '');
      if (t && !(t.indexOf('assistant') >= 0 || t.indexOf('agent') >= 0 ||
                 t === 'message' || t === 'text')) {
        continue;
      }
      const txt = pick(ev.data !== undefined ? ev.data : ev);
      if (txt && txt.trim()) parts.push(txt);
    }
    return parts.join('\n');
  }

  /** 用户是否明确要求停止。 */
  function userRequestedStop(agent) {
    try {
      const events = sessionEvents(agent.session) || [];
      const tail = events.slice(-10);
      for (let i = tail.length - 1; i >= 0; i--) {
        const ev = tail[i];
        if (!ev || ev.type !== 'user/message') continue;
        const content = ev.data && (ev.data.text || ev.data.content || ev.data.message?.content);
        const txt = Array.isArray(content)
          ? content.filter((part) => part.type === 'text').map((part) => part.text).join('\n')
          : String(content || '');
        for (const w of STOP_WORDS) {
          if (txt.indexOf(w) >= 0) return true;
        }
      }
    } catch (e) {}
    return false;
  }

  const guardState = new Map(); // agentId -> { count, firstAt }
  const MAX_GUARD = 12;         // 【Bug3】从 40 降到 12：过高的上限本身就会造成"死缠烂打"
  const GUARD_WINDOW = 30 * 60 * 1000; // 30 分钟内计数，超过则重置

  disposers.push(ctx.on('agent/turn-stopping', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      // 用户主动取消 → 放行
      const signal = payload.signal;
      if (signal && signal.aborted) return;
      // 【Bug2 修复】只有 engineering 预设的会话才拦截结束。
      if (!isGuardSession(agent)) return;

      // ── 【Bug3 修复】完成信号检测：任务真做完了就放行，不再拉回来 ──────
      const fin = detectTaskFinished(agent);
      if (fin.done) {
        guardState.delete(agent.id);   // 清计数，下一轮重新开始
        return;                        // 放行，允许结束
      }
      // 用户明确要求停止 → 放行
      if (userRequestedStop(agent)) {
        guardState.delete(agent.id);
        return;
      }

      const st = guardState.get(agent.id) || { count: 0, firstAt: Date.now() };
      if (Date.now() - st.firstAt > GUARD_WINDOW) { st.count = 0; st.firstAt = Date.now(); }
      if (st.count >= MAX_GUARD) return;
      st.count += 1;
      guardState.set(agent.id, st);
      // 必须带 source：DSH 宿主多处按 message.source.kind 判别消息来源
      // （dsh-agent-loop/isOwned、dsh-goal-round-driver/restoreOtherClaimed 等），
      // 缺 source 的 user 消息会让宿主抛 "Cannot read properties of undefined (reading 'kind')"，
      // 该错误经 promptError 冒到输入框下方的 Toast。见 2026-09-12 排查记录。
      const msg = createUserMessage({
        content: [{ type: 'text', text: GUARD_TEXT }],
        source: { kind: 'plugin', plugin: 'dsh-engineering-ui' }
      });
      agent.steer(msg);
    } catch (e) {
      // 守卫失败不能影响主流程
    }
  }));

  // ── 子代理树 ──────────────────────────────────────────────────────────────
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/agents',
    handler: async (req, res) => {
      const q = queryOf(req);
      const root = (q.get('rootSessionId') || '').trim();
      if (!ID_RE.test(root)) return send(res, 400, { ok: false, error: 'bad rootSessionId' });
      try {
        const list = await ctx.subagents.listDescendants(root);
        return send(res, 200, { ok: true, rootSessionId: root, agents: list });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ══ 【Bug-32 修复】子代理启动校验（subagent_fork 后确认它真的起来了）══════
  // 原缺陷（实测 22:36）：主对话 subagent_fork(description="工程图输出房间")
  //   返回 "started subagent 506651c9..."，但 list_agents 只显示 5 个 inactive
  //   小屋，【完全没有】"工程图输出房间"对应的子代理。
  //   主对话无法通过返回的 ID 找到它，也无法 send_message 唤醒；
  //   mode_gate 只能看到 no_heartbeat + 无产物，无法判定小屋死活。
  // 修复：提供 /verify-subagent 只读端点，fork 后立即核对：
  //   · expected：主对话期望出现的房间名/标签
  //   · 在子代理树里查找匹配项，返回 found / id / status / activity
  //   · 未找到时给出"重试 subagent_fork"的明确指引
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/verify-subagent',
    handler: async (req, res) => {
      const q = queryOf(req);
      const root = (q.get('rootSessionId') || '').trim();
      const expect = (q.get('expect') || '').trim();
      const expectId = (q.get('expectId') || '').trim();
      if (!ID_RE.test(root)) return send(res, 400, { ok: false, error: 'bad rootSessionId' });
      try {
        const list = await ctx.subagents.listDescendants(root);
        const agents = Array.isArray(list) ? list : (list && list.agents) || [];
        const norm = (v) => String(v == null ? '' : v).trim();
        let hit = null;
        if (expectId) {
          hit = agents.find((a) => a && (norm(a.id) === expectId
            || norm(a.sessionId) === expectId)) || null;
        }
        if (!hit && expect) {
          const e = expect.toLowerCase();
          hit = agents.find((a) => {
            if (!a) return false;
            const cands = [a.id, a.sessionId, a.label, a.displayTitle, a.title, a.name];
            return cands.some((c) => norm(c).toLowerCase().indexOf(e) >= 0);
          }) || null;
        }
        const running = agents.filter((a) => a && (a.activity === 'running'
          || a.running === true)).length;
        return send(res, 200, {
          ok: true,
          rootSessionId: root,
          expected: expect || expectId || null,
          found: !!hit,
          agent: hit,
          agent_count: agents.length,
          running_count: running,
          // 【Bug-32】未找到时必须给出可操作指引，而不是让主对话干等
          hint: hit ? null : ('未在子代理树中找到匹配 "' + (expect || expectId)
            + '" 的子代理。这是 Bug-32 现象：subagent_fork 可能只返回了注册凭证但'
            + '子代理未真正启动。请重新调用 subagent_fork 创建该小屋，'
            + '并再次用本端点校验；若连续两次失败，改为串行创建（避免并发冲突）。')
        });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ══ 【Bug-21 修复】房间通知通道（只记录，不打断正在建模的小屋）══════════
  // 原缺陷：主对话用 send_message 给 4 个正在 SW 建模的小屋发通知
  //   （"旧产物已删除，继续设计"）。消息虽然 delivered，但小屋要等当前回合
  //   结束才收到；长建模脚本期间收不到；收到后还会【切换上下文】处理通知，
  //   有打断建模流程的风险。
  // 修复：提供 /notify-room 只读端点，把通知写入房间的 reports/ 目录下的
  //   notify 文件（与 mode_gate 的 reports 同构），小屋在【自愿的检查点】
  //   读取即可，不强制打断。纯通知类消息不再走对话通道。
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/notify-room',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const room = String((payload && payload.room) || '').trim();
      const message = String((payload && payload.message) || '').slice(0, 8000);
      if (!room) return send(res, 400, { ok: false, error: 'bad room' });
      if (!message) return send(res, 400, { ok: false, error: 'empty message' });
      try {
        // ══ 【Bug-21 修复·路径错误】必须复用 findToolsDir() ═══════════════
        // 原实现自己拼路径，且兜底用了 process.cwd()/engineering/tools ——
        //   实测（本机复现）它拼出了
        //     ~/.dsh/profiles/desktop/engineering/tools/reports   ← 错误位置
        //   而 mode_gate 实际读的是
        //     ~/.dsh/.agent-presets/engineering/tools/reports     ← 正确位置
        //   结果：通知写进去了，但小屋永远读不到（静默失效）。
        // 修复：直接复用插件里已正确实现的 findToolsDir()（它会优先命中
        //   .agent-presets/engineering/tools），并只在它失败时才做兜底。
        const fsMod = await import('node:fs');
        const pathMod = await import('node:path');
        const dirs = [];
        const _td = findToolsDir();
        if (_td) dirs.push(pathMod.join(_td, 'reports'));
        try {
          const eng = process.env.DSH_ENGINEERING_ROOT;
          if (eng) dirs.push(pathMod.join(eng, 'tools', 'reports'));
        } catch (e) {}
        // 兜底：DSH_HOME 下的标准落点（与 install-0.2.0.ps1 的安装位置一致）
        try {
          const _home = process.env.USERPROFILE || process.env.HOME || '';
          const _dshHome = process.env.DSH_HOME || (_home ? pathMod.join(_home, '.dsh') : '');
          if (_dshHome) {
            dirs.push(pathMod.join(_dshHome, '.agent-presets', 'engineering', 'tools', 'reports'));
            dirs.push(pathMod.join(_dshHome, 'engineering', 'tools', 'reports'));
          }
        } catch (e) {}
        // ── 只接受【已存在】的 tools 目录，绝不凭空 mkdir 造一个新目录 ──
        //   （原实现无条件 mkdirSync 第一个候选 → 错误路径也被"造出来"，
        //     于是错误位置看起来"可用"，掩盖了问题）
        let target = null;
        for (const d of dirs) {
          try {
            const parent = pathMod.dirname(d);
            if (fsMod.existsSync(parent)) { target = d; break; }
          } catch (e) { continue; }
        }
        if (!target) {
          return send(res, 200, {
            ok: false,
            error: '无法定位工程模式 reports 目录（tools 目录不存在）',
            tried: dirs,
            hint: '请确认工程模式已安装：~/.dsh/.agent-presets/engineering/tools 应存在'
          });
        }
        const safe = room.replace(/[^A-Za-z0-9_一-龥-]/g, '_');
        const file = pathMod.join(target, safe + '.notify.json');
        let arr = [];
        try {
          if (fsMod.existsSync(file)) {
            const raw = fsMod.readFileSync(file, 'utf8');
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) arr = parsed;
            else if (parsed && Array.isArray(parsed.items)) arr = parsed.items;
          }
        } catch (e) { arr = []; }
        arr.push({ at: new Date().toISOString(), message, source: 'main-conversation' });
        fsMod.writeFileSync(file, JSON.stringify(arr.slice(-100), null, 2), 'utf8');
        return send(res, 200, {
          ok: true, room, file, count: arr.length,
          note: ('通知已写入房间文件（不打断建模）。小屋在下一个自愿检查点读取 '
                 + 'room-report-read 或直接读该文件即可（Bug-21）。')
        });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── 原始事件流（不做字段映射，client 半负责解析）──────────────────────────
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/log',
    handler: async (req, res) => {
      const q = queryOf(req);
      const sessionId = (q.get('sessionId') || '').trim();
      const since = Number(q.get('since') || 0);
      const limit = Math.min(Number(q.get('limit') || 300), 800);
      if (!ID_RE.test(sessionId)) return send(res, 400, { ok: false, error: 'bad sessionId' });
      try {
        // 【0.2.0 兼容】inspect() 已移除 → open(id,'read') / read() / close()。
        // handle.header / handle.inheritedEventCount 取代旧的 meta / inheritedEventCount。
        const log = await readSessionLog(ctx, sessionId);
        if (!log) return send(res, 404, { ok: false, error: 'no session log' });
        const header = log.header || {};
        const seedLength = log.inheritedEventCount || 0;
        const all = (log.events || []).filter((e) => e.seq >= seedLength && !RAW_DROP.has(e.type));
        const out = [];
        for (const event of all) {
          if (since && event.seq <= since) continue;
          out.push({ seq: event.seq, time: event.time, type: event.type, data: event.data });
        }
        const tail = out.slice(-limit);
        // 0.2.0 的 SessionHeader 不含 title（标题是会话投影，不是存储元数据）。
        // 这里从日志里做一次尽力而为的标题提取，取不到就让前端回落到 sessionId。
        let title = header.title;
        if (!title) {
          for (let i = all.length - 1; i >= 0; i--) {
            const ev = all[i];
            const d = ev && ev.data;
            if (d && typeof d.title === 'string' && d.title) { title = d.title; break; }
          }
        }
        return send(res, 200, {
          ok: true,
          sessionId,
          title: title || null,
          createdAt: header.createdAt,
          latestSeq: tail.length ? tail[tail.length - 1].seq : since,
          events: tail
        });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── 【Bug1 核心修复】阻塞式代问（Blocking Wait）────────────────────────────
  // 问题本质：子代理调 ask_user_question 必然抛 DELEGATED_CALLER ——
  //   dsh-user-questions 的 ask() 会执行 `agents.roots().includes(agent)` 校验，
  //   只有运行时根代理（主对话）能通过；子代理被判定为 owned child，
  //   平台直接注释说明"an owned child has no human answerer and would block forever"。
  //
  // 错误做法（旧版）：用 subagents.followup 把答案当"下一轮消息"推给子代理。
  //   followup 的语义是"作为子代理的下一次 FIFO 回合"——它是一条新指令，
  //   不是那次工具调用的返回值。子代理收到后会当成新任务/自问自答，消息错乱。
  //
  // 正确做法（本实现）：
  //   提问必须由【主代理】发起。ctx.userQuestions.ask({ agent: 主代理 }) 返回
  //   一个【真正挂起的 Promise】，直到用户在前端作答才 resolve；resolve 的值
  //   直接就是那次工具调用的 tool_result。子代理侧拿到的是"工具返回了答案"，
  //   然后从原来中断的地方继续，绝不会当成新 Prompt。
  //
  // 本端点职责：
  //   - /ask     主代理发起阻塞式提问（真正挂起，直到用户作答）
  //   - /answer  前端提交选择 → resolve 上面对应 pendingId 的挂起 Promise
  //
  // 挂起登记表：pendingId -> { questions, resolve, reject, settled }
  // 这是"阻塞等待"的物理载体。只要它还在表里，调用方的 Promise 就没结束，
  // 该轮次就不会关闭、不会开启新一轮 —— 这正是需求 1「强制阻塞等待」。
  const pendingAsks = new Map();

  // ── 【Bug1】阻塞式提问桥接（供子代理侧上报使用）────────────────────────
  //
  // 【分工说明 · 重要】
  //   · 主对话的提问：完全由 DSH 原生承担，本插件不介入。
  //     原生链路：模型调 ask_user_question 工具
  //       → ctx.userQuestions.ask({agent: 主代理})  ← 真正挂起
  //       → question/requested 帧 → @deepseek-ai/dsh-client-ui-user-questions
  //         接管 conversation.composer 渲染选项卡
  //       → 用户点选项 + "下一题/提交" → pending.answer() → wait.respond()
  //       → 标准 RPC 回填 → **成为那次工具调用的 tool_result**
  //     该链路天然满足：阻塞等待、绑定工具返回值、不自动提交、无模型参与。
  //
  //   · 子代理的提问：平台硬性禁止（DELEGATED_CALLER），只能在子代理面板里
  //     用本端点做桥接 —— 由本插件代为主代理发起 ask()，把 pendingId 交给
  //     前端选项卡，用户提交后 resolve 成 tool_result。
  //
  // DSH 的 userQuestions.ask 会校验 agent 是否为运行时根代理：
  //   子代理 → DELEGATED_CALLER（平台设计如此，无法绕过）
  //   主代理 → 通过，且 ask() 返回的 Promise 直到用户作答才 resolve
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/ask',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const sessionId = String((payload && payload.sessionId) || '').trim();
      const questions = Array.isArray(payload && payload.questions) ? payload.questions : [];
      if (!ID_RE.test(sessionId)) return send(res, 400, { ok: false, error: 'bad sessionId' });
      if (!questions.length) return send(res, 400, { ok: false, error: 'empty questions' });

      const agents = ctx.agents;
      const uq = ctx.userQuestions;
      if (!agents || typeof agents.get !== 'function') {
        return send(res, 200, { ok: false, error: 'agents 服务不可用' });
      }
      if (!uq || typeof uq.ask !== 'function') {
        return send(res, 200, { ok: false, error: 'userQuestions 服务不可用（无法发起原生提问）' });
      }
      // 找到既有的 live agent（必须是运行时根代理，否则 ask() 会拒绝）
      const agent = agents.get(sessionId);
      if (agent === undefined) {
        return send(res, 200, { ok: false, error: '会话不在运行中（请保持主对话开启）' });
      }
      const roots = typeof agents.roots === 'function' ? agents.roots() : [];
      if (!roots.includes(agent)) {
        return send(res, 200, {
          ok: false,
          error: 'DELEGATED_CALLER：该会话是被拥有的子代理，平台禁止其向用户提问。'
               + '提问必须由主对话发起（子代理应把问题上报主对话）。'
        });
      }

      const pendingId = 'q_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);

      // 【bug6 修复】entry 必须自己持有 resolve —— 旧版只把 ask() 返回的 promise
      // 存成 entry.promise，却从没往 entry 上挂 resolve/reject，于是 /answer 里
      // 执行 entry.resolve({answers}) 直接抛 "entry.resolve is not a function"。
      // 正确做法：自建 Promise 拿到它的 resolve/reject 存进 entry，
      // 同时把原生 ask() 的完成桥接到同一个 entry，保证只 settle 一次。
      let settleResolve, settleReject;
      const settledPromise = new Promise((res2, rej2) => { settleResolve = res2; settleReject = rej2; });

      const entry = {
        questions: questions.map((q) => ({
          id: String(q.id || 'q1'),
          question: String(q.question || ''),
          options: Array.isArray(q.options) ? q.options : []
        })),
        settled: false,
        resolve: settleResolve,
        reject: settleReject,
        promise: settledPromise
      };
      pendingAsks.set(pendingId, entry);

      // 原生 ask()：真正挂起，直到用户作答（或取消）。
      // 它 resolve 的答案走标准 RPC 回填成 tool_result —— 这是主路径。
      try {
        uq.ask({
          questions: questions.map((q) => ({
            id: String(q.id || 'q1'),
            question: String(q.question || ''),
            ...(q.header !== undefined ? { header: String(q.header) } : {}),
            ...(Array.isArray(q.options) ? { options: q.options } : {}),
            ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
          })),
          agent
        }).then((answer) => {
          if (!entry.settled) {
            entry.settled = true;
            pendingAsks.delete(pendingId);
            settleResolve(answer);
          }
        }).catch((err) => {
          if (!entry.settled) {
            entry.settled = true;
            pendingAsks.delete(pendingId);
            settleReject(err);
          }
        });
      } catch (err) {
        pendingAsks.delete(pendingId);
        return send(res, 200, { ok: false, error: String((err && err.message) || err) });
      }
      return send(res, 200, {
        ok: true, pendingId, sessionId,
        note: '已发起原生阻塞式提问；答案将以 tool_result 形式回到提问方。'
      });
    }
  }));

  // ══ 【问题4 修复】子代理提问必须落到【真人所在的 root 会话】 ═════════════
  //
  // ── 原缺陷（C+D 模式死锁的真实原因）─────────────────────────────────────
  // 旧实现虽然用 root agent 承载 ask()（为了绕过 DELEGATED_CALLER 校验），
  // 但调用时传的是 roots.find(...) 找到的第一个 root —— 而且更致命的是：
  // 前端 AskCard 只在【右侧子代理面板】里渲染，真人始终只盯着【主对话框】。
  // 于是：
  //   · 提问帧挂到了某个 root 会话，但 UI 没有把卡片摆到真人眼前；
  //   · 子代理面板里没有可交互的人 → /pending-child 永远为空；
  //   · 子代理（ask_user.py 原地轮询）要么死等到超时，要么违规自跳过。
  //
  // ── 修复思路（用户给定的方向 A）────────────────────────────────────────
  // 把提问【真正挂到 root 会话的 userQuestions 上】：
  //   1) 路由目标 sessionId 用 **root**（不是 child）—— 帧就发到主对话框，
  //      由 DSH 原生 ui-user-questions 渲染选项卡，真人一定能看见。
  //   2) 答案经 root 的 wait.respond() 回到宿主 → 本插件把它存进
  //      childResults(pendingId)，供 /result-child 取回。
  //   3) ask_user.py 的轮询协议【完全不变】：它照样轮 /pending-child 与
  //      /result-child，拿到的就是真人在主对话框点的结果。
  //      → 子代理那段代码一行不用改，C+D 原封不动。
  //
  // ── 如何确定"真人所在的 root" ─────────────────────────────────────────
  // 优先用请求里显式给的 rootSessionId / parentSessionId（由 ask_user.py 或
  // 宿主注入）；否则用 childSessionId 反查其父会话（parentOf）—— 子代理的父
  // 就是发起它的主对话；最后才退化为 roots() 里的第一个。
  const childAsks = new Map(); // pendingId -> { childSessionId, rootSessionId, questions, resolve, reject, settled }
  // 【问题4 修复】答案留存表（声明提前，与 childAsks 同处）
  const childResults = new Map(); // pendingId -> { answers, at }

  // 【问题4 修复】把原生 ask() 的返回形状归一成 ask_user.py 期望的
  // { answers: [{ id, selected: [...], custom? }] }。
  // 原生可能返回：
  //   · { answers: [...] }（标准）
  //   · [{...}]（数组）
  //   · 单题时返回裸值/字符串
  // 这里统一成标准形状，保证子代理侧解析稳定。
  function normalizeAnswers(questions, raw) {
    const qs = Array.isArray(questions) ? questions : [];
    const pickFrom = (item) => {
      if (!item || typeof item !== 'object') {
        return { selected: item === undefined || item === null ? [] : [String(item)] };
      }
      let sel = item.selected;
      if (sel === undefined && item.answer !== undefined) sel = item.answer;
      if (sel === undefined && item.value !== undefined) sel = item.value;
      if (sel === undefined) sel = [];
      if (!Array.isArray(sel)) sel = [sel];
      const out = { selected: sel.map((s) => String(s)) };
      if (item.custom !== undefined && item.custom !== null && String(item.custom) !== '') {
        out.custom = String(item.custom);
      }
      return out;
    };
    // 情况 A：{ answers: [...] }
    if (raw && typeof raw === 'object' && Array.isArray(raw.answers)) {
      const arr = raw.answers;
      return qs.map((q, i) => {
        const hit = arr.find((a) => a && String(a.id) === String(q.id));
        const one = pickFrom(hit || arr[i] || {});
        return { id: q.id, selected: one.selected, ...(one.custom ? { custom: one.custom } : {}) };
      });
    }
    // 情况 B：数组
    if (Array.isArray(raw)) {
      return qs.map((q, i) => {
        const hit = raw.find((a) => a && typeof a === 'object' && String(a.id) === String(q.id));
        const one = pickFrom(hit || raw[i] || {});
        return { id: q.id, selected: one.selected, ...(one.custom ? { custom: one.custom } : {}) };
      });
    }
    // 情况 C：单题裸值
    if (qs.length === 1) {
      const one = pickFrom(raw);
      return [{ id: qs[0].id, selected: one.selected, ...(one.custom ? { custom: one.custom } : {}) }];
    }
    // 情况 D：无法识别 → 空答案（不抛错，交由调用方判断）
    return qs.map((q) => ({ id: q.id, selected: [] }));
  }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/ask-child',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const childSessionId = String((payload && payload.childSessionId) || '').trim();
      // 【问题4 修复】可选：显式指定"真人所在的 root 会话"
      const explicitRoot = String(
        (payload && (payload.rootSessionId || payload.parentSessionId)) || ''
      ).trim();
      const questions = Array.isArray(payload && payload.questions) ? payload.questions : [];
      if (!ID_RE.test(childSessionId)) return send(res, 400, { ok: false, error: 'bad childSessionId' });
      if (explicitRoot && !ID_RE.test(explicitRoot)) {
        return send(res, 400, { ok: false, error: 'bad rootSessionId' });
      }
      if (!questions.length) return send(res, 400, { ok: false, error: 'empty questions' });

      const agents = ctx.agents;
      const uq = ctx.userQuestions;
      if (!agents || !uq || typeof uq.ask !== 'function') {
        return send(res, 200, { ok: false, error: '服务不可用（agents/userQuestions）' });
      }
      const roots = typeof agents.roots === 'function' ? agents.roots() : [];
      if (!roots.length) {
        return send(res, 200, { ok: false, error: '没有可用的根代理承载提问（请确认主对话在运行）' });
      }

      // ── 【问题4 修复】定位"真人所在的 root 会话" ──────────────────────
      // 顺序：显式参数 > 由 child 反查父会话 > roots()[0]（兜底）。
      // 找到的 root 必须是 live agent，且能通过 roots().includes() 校验 ——
      // 否则 ask() 会抛 DELEGATED_CALLER。
      let holder = null;
      let holderId = null;
      const pickRoot = (id) => {
        if (!id) return null;
        const cand = typeof agents.get === 'function' ? agents.get(id) : undefined;
        if (cand && roots.includes(cand)) return cand;
        return null;
      };
      if (explicitRoot) {
        holder = pickRoot(explicitRoot);
        if (holder) holderId = explicitRoot;
      }
      if (!holder && childSessionId) {
        // 子代理的父会话 = 发起它的主对话（真人所在处）
        try {
          const pid = await parentOf(ctx, childSessionId);
          if (pid) {
            holder = pickRoot(pid);
            if (holder) holderId = pid;
          }
        } catch (e) {
          // 反查失败：不致命，走兜底
        }
      }
      if (!holder) {
        holder = roots.find((a) => a && a.id) || null;
        holderId = holder ? holder.id : null;
      }
      if (!holder || !holderId) {
        return send(res, 200, { ok: false, error: '没有可用的根代理承载提问（请确认主对话在运行）' });
      }

      // ══ 【Bug#3 修复·服务端去重】同 child + 同一张卡 → 复用已有 pendingId ══
      //
      // ── 为什么要在服务端也做一遍 ─────────────────────────────────────
      //   原先去重只存在于【客户端 ask_user.py】（它先查 /pending-child 再决定
      //   是否登记）。但只要能直接打到 /ask-child 的调用方（复现脚本、
      //   其它工具、以及任何重试路径）就会绕过它 —— 服务端每次都无条件
      //   生成新 pendingId，于是同一零件堆出多张卡。
      //   测试部复现脚本正是直接 POST /ask-child，因此必然"复现成功"。
      //
      // ── 判定规则（与 ask_user.py 的 _same_question 对齐，但更保守）────
      //   · 同一 childSessionId
      //   · 题目数量一致
      //   · 选项集合完全一致（用户可选项是契约，绝不放宽）
      //   · 问题文本"归一化后"相同，或互为包含，或关键数字一致
      //   · 修改类问句（改为/修改/重做…）绝不合并 —— 那是新的一轮确认
      //
      //   命中 → 直接返回已有 pendingId（复用同一张卡），不重复登记。
      //   可用 force:true 显式绕开（确实想让用户再确认一次时）。
      // ── 归一化：先【提取标识】，再剥离括号，最后去标点/套话 ──────────
      //   Bug#3 实测用例正是"加括号补充规格"：
      //     A: 以下参数将用于建模【测试零件X】，确认要用吗？
      //     B: 以下参数将用于建模【测试零件X】（6061-T6），要用吗？
      //   两者是同一张卡，但括号里的 6061-T6 会让纯文本比对失败。
      //
      //   ⚠️ 顺序很关键：【】里的内容往往就是【零件名本身】，
      //     若先剥括号会把零件名一起删掉 → 剩下的文本变成空串
      //     → 任何两个零件都会"相等"（过度合并，把 A 的确认套到 D 上）。
      //     因此必须【先取标识】，标识单独参与比对。
      const _idsQ = (s) => {
        const out = [];
        const m = String(s || '').match(/[【\[（(][^】\]）)]+[】\]）)]/g) || [];
        for (const one of m) {
          // 去掉包裹括号后归一化，作为"零件标识"
          const inner = one.replace(/^[【\[（(]/, '').replace(/[】\]）)]$/, '');
          const t = String(inner).replace(/[\s，。、；：！？,.;:!?~～…\-—_]/g, '');
          if (t) out.push(t);
        }
        return out.sort();
      };
      // 归一化：剥离【所有】括号内容后，再去标点与套话。
      //   （零件名已由 _idsQ 单独提取，这里剥离是为了让"括号规格差异"不影响比对）
      const _normQ = (s) => {
        let t = String(s || '');
        t = t.replace(/（[^）]*）/g, '').replace(/\([^)]*\)/g, '');
        t = t.replace(/【[^】]*】/g, '').replace(/\[[^\]]*\]/g, '');
        t = t.replace(/[\s，。、；：！？,.;:!?~～…\-—_（）()【】\[\]"'“”‘’]/g, '');
        return t.replace(/以下参数将用于建模|以下参数用于|参数如下|请确认|要用吗|可以用吗|是否确认|确认开始建模|开始建模|参数确认|请选择|请核对|核对后|将用于|确认/g, '');
      };
      const _digitsQ = (s) => (String(s || '').match(/\d+(?:\.\d+)?/g) || []).sort();
      const _optsKey = (qs) => JSON.stringify((qs || []).map((q) =>
        ((q && q.options) || []).map((o) => String((o && o.label !== undefined) ? o.label : o)).sort()));
      const _isModify = (s) => /改为|改成|修改|调整|变更|重做|重新|换成/.test(String(s || ''));

      /** 判断服务端已登记的 pending 是否与本请求"语义相同"。 */
      const _sameServerQuestion = (a, b) => {
        try {
          if (!a || !b || a.length !== b.length) return false;
          // 硬约束：选项集合必须一致
          if (_optsKey(a) !== _optsKey(b)) return false;
          for (let i = 0; i < a.length; i++) {
            const qa = a[i] || {}, qb = b[i] || {};
            const sa = String(qa.question || ''), sb = String(qb.question || '');
            // 硬约束：修改类 vs 非修改类 不可合并
            if (_isModify(sa) !== _isModify(sb)) return false;
            // ── 硬约束：双方都带【零件标识】时，必须有共同标识 ──────────
            //   【】里的名字是零件身份。B 比 A 多一个规格括号（6061-T6）
            //   属于同一零件的补充说明 → 只要有**交集**即判同题。
            //   但若两边标识完全不相交（零件X vs 零件Y）→ 绝不相同，
            //   无论后面文本多像都不能合并（否则会把 A 的确认套到 D 上）。
            const ia = _idsQ(sa), ib = _idsQ(sb);
            if (ia.length && ib.length) {
              const _shared = ia.some((x) => ib.indexOf(x) >= 0);
              if (!_shared) return false;
              continue;   // 有共同标识 → 同一零件，忽略规格/措辞差异
            }
            // ── 单边带标识：降级用"标识 vs 对侧归一化文本"比对 ─────────
            //   例：A="…建模【测试零件X】，确认要用吗？"
            //       G="确认开始建模测试零件X？"（没有括号，零件名裸写在句中）
            //   A 剥掉【】后剩空串，无法与 G 比对；此时用 A 的标识
            //   去 G 的归一化文本里找。找到 = 同一零件。
            if (ia.length || ib.length) {
              const _ids = ia.length ? ia : ib;
              const _otherRaw = ia.length ? sb : sa;
              const _other = _normQ(_otherRaw) + String(_otherRaw)
                .replace(/[\s，。、；：！？,.;:!?~～…\-—_（）()【】\[\]"'“”‘’]/g, '');
              if (_ids.some((x) => x && _other.indexOf(x) >= 0)) continue;
              return false;   // 有标识但对面完全找不到 → 不同零件
            }
            // ── 两边都没有【】标识：退回文本归一化 + 数字比对 ───────────
            const na = _normQ(sa), nb = _normQ(sb);
            if (na === nb) continue;
            if (na && nb && (na.indexOf(nb) >= 0 || nb.indexOf(na) >= 0)) continue;
            const da = _digitsQ(sa), db = _digitsQ(sb);
            if (da.length && da.join() === db.join()) continue;
            return false;
          }
          return true;
        } catch (e) {
          return false;   // 判定异常时按"不同题"处理（宁可多一张，不可错合并）
        }
      };

      // force=true 时跳过去重（调用方明确要求重新提问）
      const _forceNew = !!(payload && (payload.force === true || payload.forceNew === true));
      if (!_forceNew) {
        for (const [pid, e] of childAsks.entries()) {
          if (e.settled) continue;
          if (e.childSessionId !== childSessionId) continue;
          if (_sameServerQuestion(e.questions, questions)) {
            return send(res, 200, {
              ok: true,
              pendingId: pid,
              childSessionId,
              rootSessionId: e.rootSessionId,
              reused: true,
              note: '【Bug#3】检测到同 child 已存在语义相同的待答卡片，已复用该卡片'
                  + '（不再重复登记，避免面板堆卡）。'
            });
          }
        }
      }

      const pendingId = 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
      let settleResolve, settleReject;
      const settledPromise = new Promise((r2, j2) => { settleResolve = r2; settleReject = j2; });

      const entry = {
        childSessionId,
        rootSessionId: holderId,
        // ── 【Bug#3 修复·真正的根因】必须保留完整 questions（含 options）──
        //   原实现只存 {id, question}，把 options 丢掉了 → 服务端去重比对时
        //   _optsKey(旧卡)  = [[]]              （没有 options）
        //   _optsKey(新请求)= [["修改","确认，继续"]]
        //   两者恒不相等 → 去重分支【永远不会命中】，同题照样堆卡。
        //   这就是"代码写了、实测仍出 3 张卡"的真正原因（不是没重启，是逻辑被
        //   这行 map 截断）。options 是判定"是否同一张卡"的必要信息，不能省。
        questions: questions.map((q) => ({
          id: String(q.id || 'q1'),
          question: String(q.question || ''),
          ...(q.header !== undefined ? { header: String(q.header) } : {}),
          ...(Array.isArray(q.options) ? { options: q.options } : {}),
          ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
        })),
        settled: false,
        resolve: settleResolve,
        reject: settleReject,
        promise: settledPromise
      };
      childAsks.set(pendingId, entry);

      // 【问题4 核心】用 root 承载 ask() —— 帧路由到主对话框，
      // 由 DSH 原生 ui-user-questions 渲染选项卡，真人在主对话里作答；
      // 答案经 root 的 wait.respond() 回到宿主，本插件再留存给 ask_user.py。
      try {
        uq.ask({
          questions: questions.map((q) => ({
            id: String(q.id || 'q1'),
            question: String(q.question || ''),
            ...(q.header !== undefined ? { header: String(q.header) } : {}),
            ...(Array.isArray(q.options) ? { options: q.options } : {}),
            ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
          })),
          agent: holder
        }).then((answer) => {
          if (!entry.settled) {
            entry.settled = true;
            // 【问题4】答案先留存，再删除 pending —— 保证 ask_user.py 轮询时
            // "pending 消失"与"result 可取"始终成对出现，不会读到空结果。
            try {
              const answers = normalizeAnswers(entry.questions, answer);
              childResults.set(pendingId, { answers, at: Date.now() });
            } catch (e) {
              // 留存失败不阻断 resolve
            }
            childAsks.delete(pendingId);
            settleResolve(answer);
          }
        }).catch((err) => {
          if (!entry.settled) {
            entry.settled = true;
            childAsks.delete(pendingId);
            settleReject(err);
          }
        });
      } catch (err) {
        childAsks.delete(pendingId);
        return send(res, 200, { ok: false, error: String((err && err.message) || err) });
      }

      return send(res, 200, {
        ok: true, pendingId, childSessionId,
        rootSessionId: holderId,
        routedTo: holderId === childSessionId ? 'child' : 'root',
        note: '子代理提问已登记并路由到主对话（root）——请在【主对话】的选项卡里作答，'
            + '答案将经 /result-child 返回给提问的子代理。'
      });
    }
  }));

  // 子代理面板查询：当前有哪些属于该子代理的待答问题
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/pending-child',
    handler: async (req, res) => {
      const q = queryOf(req);
      const childSessionId = (q.get('childSessionId') || '').trim();
      // 【问题4 修复】也允许按 root 会话查询（真人在主对话作答时，前端可用它反查）
      const rootSessionId = (q.get('rootSessionId') || '').trim();
      if (childSessionId && !ID_RE.test(childSessionId)) {
        return send(res, 400, { ok: false, error: 'bad childSessionId' });
      }
      if (rootSessionId && !ID_RE.test(rootSessionId)) {
        return send(res, 400, { ok: false, error: 'bad rootSessionId' });
      }
      if (!childSessionId && !rootSessionId) {
        return send(res, 400, { ok: false, error: 'need childSessionId or rootSessionId' });
      }
      const out = [];
      for (const [pid, e] of childAsks.entries()) {
        if (e.settled) continue;
        const byChild = childSessionId && e.childSessionId === childSessionId;
        const byRoot = rootSessionId && e.rootSessionId === rootSessionId;
        if (byChild || byRoot) {
          out.push({
            pendingId: pid,
            questions: e.questions,
            childSessionId: e.childSessionId,
            rootSessionId: e.rootSessionId
          });
        }
      }
      return send(res, 200, {
        ok: true, childSessionId: childSessionId || undefined,
        rootSessionId: rootSessionId || undefined, pending: out
      });
    }
  }));

  // 【问题4 修复】已作答题目的结果留存表 —— 声明提前到 childAsks 旁，
  // 避免"使用在前、声明在后"的跨作用域引用（虽然闭包延迟执行不会报错，
  // 但提前声明让阅读顺序与执行顺序一致）。
  // 子代理 CLI（ask_user.py）靠它把答案读回来当作"工具返回值"。
  // 保留 10 分钟，够一次阻塞等待读取；过期自动清理。

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/result-child',
    handler: async (req, res) => {
      const q = queryOf(req);
      const pendingId = (q.get('pendingId') || '').trim();
      if (!pendingId) return send(res, 400, { ok: false, error: 'bad pendingId' });
      const hit = childResults.get(pendingId);
      if (!hit) {
        return send(res, 200, { ok: false, error: '结果不存在或已过期' });
      }
      return send(res, 200, { ok: true, answers: hit.answers });
    }
  }));

  // 定期清理过期结果（10 分钟）
  const _resultSweeper = setInterval(() => {
    const now = Date.now();
    for (const [pid, v] of childResults.entries()) {
      if (now - v.at > 10 * 60 * 1000) childResults.delete(pid);
    }
  }, 60 * 1000);
  if (_resultSweeper && typeof _resultSweeper.unref === 'function') _resultSweeper.unref();
  disposers.push(() => clearInterval(_resultSweeper));

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/answer',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try {
        payload = JSON.parse(await readBody(req));
      } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      // 【Bug1】提交的是"对某个已发布问题的回答"，而不是一段自由文本。
      // pendingId 指向 ask-block 阶段登记的那次提问；我们只负责 resolve 它，
      // 答案会沿着 ask() 的返回路径变成那次工具调用的 tool_result。
      const pendingId = String((payload && payload.pendingId) || '').trim();
      // 【问题4 修复 · 关键 bug】原来是 payload.selected.map(String) ——
      // 这会把前端提交的 { qid, label } 对象【转成字符串 "[object Object]"】，
      // 导致后面 selected.filter(s => s.qid === q.id) 永远匹配不到（s.qid === undefined），
      // 最终 answers[].selected 恒为空数组 —— 用户明明点了选项，子代理却收到空答案。
      // 现在保留原始对象结构，并兼容历史字符串写法。
      const _rawSel = Array.isArray(payload && payload.selected) ? payload.selected : [];
      const selected = _rawSel.map((s) => {
        if (s && typeof s === 'object') {
          return {
            qid: String(s.qid !== undefined ? s.qid : (s.id !== undefined ? s.id : '')),
            label: String(s.label !== undefined ? s.label : (s.value !== undefined ? s.value : '')),
            ...(typeof s.custom === 'string' ? { custom: s.custom } : {})
          };
        }
        // 兼容：纯字符串（无 qid）→ 记为空 qid，由下方兜底逻辑挂到当前/第一题
        return { qid: '', label: String(s) };
      });
      const custom = String((payload && payload.custom) || '').slice(0, 4000);

      // ── 1) pendingId 路径：resolve 挂起的 ask()（正解）──────────────────
      // 两个登记表：pendingAsks（主对话发起）/ childAsks（子代理提问）。
      //
      // 【问题4 修复 · 两条作答路径都要能取回答案】
      //   路径甲（本端点）：前端选项卡提交 pendingId → 这里 resolve + 留存。
      //   路径乙（原生）：ask() 挂在 root 上时，真人也可直接在 DSH 原生
      //     ui-user-questions 选项卡作答 → wait.respond() 回填 →
      //     ask-child 的 .then 回调里完成留存（见该端点）。
      //   两条路径互斥且都写 childResults，因此 ask_user.py 无论走哪条
      //   都能通过 /result-child 取到真人在主对话点的结果。
      if (pendingId) {
        const isChild = childAsks.has(pendingId);
        const entry = isChild ? childAsks.get(pendingId) : pendingAsks.get(pendingId);
        if (!entry) {
          return send(res, 200, {
            ok: false,
            error: '该问题已结束或不存在（可能已被回答/取消）。请等待新的提问。'
          });
        }
        if (entry.settled) {
          return send(res, 200, { ok: false, error: '该问题已作答，请勿重复提交。' });
        }
        entry.settled = true;
        if (isChild) childAsks.delete(pendingId); else pendingAsks.delete(pendingId);
        try {
          // 回答形状：{ answers: [{ id, selected, custom? }] }
          // 【问题4 修复】无 qid 的裸选项（兼容旧前端/脚本）统一挂到唯一一题；
          // 单题场景下这是最合理的解释，多题场景下则忽略（要求前端必须带 qid）。
          const orphanLabels = selected.filter((s) => !s.qid).map((s) => s.label);
          const answers = entry.questions.map((q, qi) => {
            const picked = selected
              .filter((s) => s.qid === q.id)
              .map((s) => s.label);
            // 单题（或第一题且存在裸选项）时接纳 orphan
            if (qi === 0 && entry.questions.length === 1) {
              for (const lb of orphanLabels) {
                if (lb && picked.indexOf(lb) < 0) picked.push(lb);
              }
            }
            const one = selected.find((s) => s.qid === q.id && typeof s.custom === 'string');
            const item = { id: q.id, selected: picked };
            if (one && one.custom) item.custom = one.custom;
            return item;
          });
          // 若前端只提交了单一 custom（自由文本），挂到第一题
          if (custom && answers.length && !answers[0].custom) answers[0].custom = custom;
          // 【问题4】先留存再 resolve —— 保证子代理轮询时"pending 消失"与
          // "result 可取"成对出现，绝不出现中间的读取空窗。
          if (isChild) childResults.set(pendingId, { answers, at: Date.now() });
          entry.resolve({ answers });
          return send(res, 200, {
            ok: true, pendingId,
            bound: isChild ? 'child-tool-result' : 'tool_result',
            childSessionId: isChild ? entry.childSessionId : undefined,
            rootSessionId: isChild ? entry.rootSessionId : undefined
          });
        } catch (error) {
          return send(res, 200, { ok: false, error: String((error && error.message) || error) });
        }
      }

      // ── 2) 兼容旧前端：只有文本，没有 pendingId ───────────────────────
      // 明确拒绝而不是静默 followup —— 旧路径正是"消息错乱"的元凶。
      return send(res, 200, {
        ok: false,
        error: '缺少 pendingId：答案必须绑定到一次具体提问。'
             + '（旧版 followup 投递会导致子代理把确认当新任务，已废弃）'
      });
    }
  }));

  return () => {
    for (const dispose of disposers.reverse()) {
      try { dispose(); } catch {}
    }
  };
}
