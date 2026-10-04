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
import crypto from 'node:crypto';
// 【连接区】需要同步调用 Python 探测器（sw_bridge.py conn-probe）——
//   用 spawnSync 而非 spawn：探测最长 120s，同步等待语义更简单，
//   且设置页是低频人工操作，不占用事件循环热点。
import { spawnSync } from 'node:child_process';
// 【三大防线 · 签名式信任根】仅工程模式装配；密钥只存宿主内存。
import { DefenseSigner } from './defense-sign.js';

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
//
// 【独立聊天页 · 刻意不 inject llm】推荐追问只影响聊天页的"锦上添花"，
// 而 inject 是【硬依赖】：服务缺失会让本插件整体 PENDING、永不 apply，
// 连带收尾守卫与三大防线一起失效 —— 代价远大于收益。
// 因此 llm 走 ctx.get('llm') 惰性读取（与 client 半读 sidebarRight 同一做法），
// 缺失时 /suggest 返回 ok:false，聊天主体功能不受影响。
export const inject = ['webServer', 'subagents', 'sessionPersistence', 'sessions', 'agents', 'userQuestions'];
const PREFIX = '/dsh-engineering-ui';
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
// ── 【Bug3 修复】房间名必须接受【中文】─────────────────────────────────
// 工程模式的房间名天然是中文（结构件 / 传动机构 / 壳体机架 / 总装与验证 …），
//   而 /defense/sign、/defense/judge 之前用只允许 ASCII 的 ID_RE 校验 room，
//   于是中文房间一律被判 "bad room" → 凭据签不出来、宿主判定也不可用，
//   表现为三道防线全部 fail-closed（看起来像"签名服务未装配"）。
// 该正则仅用于房间名，允许中英文字母、数字、下划线与常见连接符。
const ROOM_RE = /^[\u4e00-\u9fa5A-Za-z0-9_.: -]{1,64}$/;
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
  // ── 【问题2 修复】移除 'sw-single-line' ──────────────────────────────
  //   该权限预设及其配套插件/横幅已整体删除，白名单同步收敛，
  //   避免把已不存在的预设当成合法证据。
  const KNOWN_PRESETS = new Set([
    'engineering', 'default', 'default-preset',
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
   *
   * ── 【P0-3 状态双副本分裂修复】────────────────────────────────────────
   * 原实现【只】搜 DSH_HOME 下的安装副本，完全不含工作区。而工程模式的
   *  Python 工具在【工作区】里跑，于是：
   *   · Python 写工作区的 workflow_state.json / mode_state.json / reports/
   *   · 宿主守卫却读安装副本 → step、房间数、凭据全部对不上
   *   · 守卫把"已完成"判成"未完成"，反复 steer 拦住对话结束
   *
   * 【最终裁决】宿主与 Python 两侧必须认【同一个目录】。现约定：
   *   DSH_STATE_DIR（显式）> 安装副本（宿主/守卫/签名信任根所在）
   *   > DSH_ENGINEERING_ROOT > 进程 cwd 下的工作区
   * 与 Python 的 tools/_store.py:state_dir() 保持完全一致的优先级。
   * 安装副本优先是有意的：宿主进程本身就是信任根与守卫所在处，
   *   凭据写在那里才有意义；工作区副本按代码注释"仅作分发"。
   * 同时保留 mtime 兜底：安装副本没有任何状态文件时，才采用工作区。
   */
  let toolsDirCache = null;
  let toolsDirCacheAt = 0;
  let toolsDirCacheKey = '';
  function toolsDirCandidates() {
    const home = process.env.USERPROFILE || process.env.HOME || '';
    const dshHome = process.env.DSH_HOME || (home ? path.join(home, '.dsh') : '');
    const out = [];
    const push = (p) => { try { if (p && fs.existsSync(p) && out.indexOf(p) < 0) out.push(p); } catch (e) {} };
    // ① 安装副本（与 Python _store.py 的第一优先一致）
    push(dshHome && path.join(dshHome, '.agent-presets', 'engineering', 'tools'));
    push(dshHome && path.join(dshHome, 'engineering', 'tools'));
    push(home && path.join(home, '.dsh', '.agent-presets', 'engineering', 'tools'));
    push(home && path.join(home, '.dsh', 'engineering', 'tools'));
    // ② 工作区副本（未安装时的回退）
    try {
      const eng = process.env.DSH_ENGINEERING_ROOT;
      if (eng) push(path.join(eng, 'tools'));
    } catch (e) {}
    try {
      const cwd = process.cwd();
      if (cwd) {
        push(path.join(cwd, 'engineering', 'tools'));
        push(path.join(cwd, 'tools'));
      }
    } catch (e) {}
    return out;
  }

  /** 目录的"新鲜度"= 状态文件里最新的 mtime（越大越可能是当前真相）。 */
  function toolsDirFreshness(dir) {
    let newest = 0;
    for (const f of ['workflow_state.json', 'mode_state.json', 'TASK_FINISHED.json']) {
      try {
        const st = fs.statSync(path.join(dir, f));
        if (st.mtimeMs > newest) newest = st.mtimeMs;
      } catch (e) {}
    }
    return newest;
  }

  /**
   * 缓存键：把这几个环境变量的当前取值拼起来。
   *
   * ── 【P0-3 修复·缓存失效】──────────────────────────────────────────────
   * 原实现只做 10 秒超时，不看环境。后果：进程内若 DSH_HOME 变化
   *   （测试逐个用例切换临时 DSH_HOME；真实场景里也可能被重设为别的部署），
   *   缓存仍指向旧目录，守卫会读【上一个环境】的状态 → 判定完全错乱。
   *   实测：升级后的回归测试第 1 个用例被误判为"已完成"而不 steer。
   * 现在把环境纳入键：键变化即强制重新解析，10 秒超时保留作为兜底。
   */
  function toolsDirEnvKey() {
    try {
      return [
        process.env.DSH_STATE_DIR || '',
        process.env.DSH_HOME || '',
        process.env.DSH_ENGINEERING_ROOT || '',
        process.env.USERPROFILE || process.env.HOME || '',
        process.cwd() || '',
      ].join('\u0000');
    } catch (e) {
      return '';
    }
  }

  function findToolsDir() {
    const _key = toolsDirEnvKey();
    // 缓存有效条件：环境未变 且 未超时 且 目录仍存在
    if (toolsDirCache && _key === toolsDirCacheKey &&
        (Date.now() - toolsDirCacheAt) < 10000) {
      try { if (fs && fs.existsSync(toolsDirCache)) return toolsDirCache; } catch (e) {}
      toolsDirCache = null;
    }
    // 显式指定优先
    try {
      const explicit = (process.env.DSH_STATE_DIR || '').trim();
      if (explicit) {
        try {
          if (fs.existsSync(explicit)) {
            toolsDirCache = explicit; toolsDirCacheAt = Date.now(); toolsDirCacheKey = _key;
            return explicit;
          }
        } catch (e) {}
      }
    } catch (e) {}
    const cands = toolsDirCandidates();
    if (!cands.length) return null;
    // ── 【P0-3 修复·必须严格按优先级，禁止跨目录回退】────────────────────
    // 取【第一个存在的候选】即最高优先级目录（安装副本优先），
    //   与 Python 侧 tools/_store.py:state_dir() 完全一致。
    //
    // 为什么不能"按 mtime 挑最新"或"空目录就跳到下一个"：
    //   那样守卫会因为当前目录【暂时没有状态文件】而去读另一个部署的
    //   状态 —— 正是"状态跨目录污染"本身。实测：临时 DSH_HOME 下没有
    //   状态文件时，守卫回退到真实安装目录，读到"任务已完成"→ 该拦不拦。
    //   契约必须是：认哪个目录就只认那个目录；目录里没有状态文件，
    //   就按"无门禁终态"处理（宁可不放行），而不是去别处找证据。
    toolsDirCache = cands[0];
    toolsDirCacheAt = Date.now();
    toolsDirCacheKey = _key;
    return toolsDirCache;
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
      //
      // ══ 【V4 适配修复】source.kind 必须"生产者自有" ══════════════════════
      // 报错：format v4 message requires a producer-owned source kind
      // 根因：Session V4 已【取消通用 'plugin' kind】。校验见
      //   session-format-v3-to-v4/src/message-sources.ts：
      //     value.kind 为空串或恰为 'plugin' → 直接抛该错。
      //   类型契约见 llm/src/message.ts：MessageSourceMap 只声明各生产者
      //   自己的 kind（user / model / tool / system-prompt / runtime-context …），
      //   "there is no shared catch-all plugin kind"。
      // 正确写法（V3→V4 迁移规则 sources.ts:producerKind 对外部插件的规定）：
      //     { kind: 'plugin:<插件名>' }        ← 必须去掉 plugin 字段
      // 旧的 { kind: 'plugin', plugin: 'X' } 属于【已退休写法】，
      //   既会被 V4 拒绝，也会让该消息无法持久化。
      // 已核对：宿主 isOwned() 只比对 'runtime-context'，改 ours 无冲突。
      const msg = createUserMessage({
        content: [{ type: 'text', text: GUARD_TEXT }],
        source: { kind: 'plugin:dsh-engineering-ui' }
      });
      agent.steer(msg);
    } catch (e) {
      // 守卫失败不能影响主流程
    }
  }));


  // ── 【已移除】Bug-47 重复输出自动阻断 ──────────────────────────────
  // 原实现挂在 agent/assistant-stream 上，统计同一段文字在窗口内的出现次数，
  //   达阈值即 agent.cancel() 打断本轮。实测该判定误伤正常作业：进度播报、
  //   状态复述、工具重试提示都属于正常工作节奏下的重复，却被当作病态循环
  //   直接终止对话，表现为模型自己就断了。用户决定移除。
  // 若将来需要防死循环，应基于【回合数 / 无进展】而非文本重复度。


  // ── 【已移除】系统提问账本（question_log.json）──────────────────────
  // 原实现监听 tools/post-execute，把每次真实 ask_user_question 的题面/选项
  //   写进 question_log.json，供门禁校验"题目是否问全"。
  // 用户反馈该机制实用价值低、且反复造成流程卡顿（记录不全就被判定"没问"），
  //   已连同消费方 choice_contract 的核对逻辑一并移除。
  // 现在两个固定问题（搭建方式 A/B/C、并行策略 D/E）依然是代码固定题面，
  //   但不再依赖任何账本文件 —— 门禁只检查 select 是否给出了合法选择。



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
        // ── 【路由修复 v2】多种来源依次尝试 ────────────────────────────────
        // 教训：v1 只在持久化日志里找 request/header，实测取不到（会话日志
        //   里该事件的形状/位置与本机版本不完全一致）。
        // 现在按【可靠性从高到低】依次尝试三个来源：
        //   ① 正在运行的 agent 的 session.requestHeader()  ← 最权威
        //      （官方 API：dsh-session/src/index.ts:788，返回当前生效的
        //        EpochHeader，其 .config 即 provider/model）
        //   ② 持久化日志里的最后一条 request/header（agent 已释放时唯一来源）
        //   ③ 请求参数显式传入（前端已拿到时直接用作兜底）
        // 任一命中即用，全部落空才报"无法确定路由"。
        const seedLength = log.inheritedEventCount || 0;
        const raw = (log.events || []).filter((e) => e.seq >= seedLength);
        let route = null;

        // ① 运行中的 agent（最可靠）
        try {
          const agentsSvc = ctx.agents;
          if (agentsSvc && typeof agentsSvc.get === 'function') {
            const live = agentsSvc.get(sessionId);
            const hdr = live && live.session && typeof live.session.requestHeader === 'function'
              ? live.session.requestHeader() : null;
            const cfg = hdr && hdr.config;
            if (cfg && cfg.provider && cfg.model) {
              route = { provider: String(cfg.provider), model: String(cfg.model) };
            }
          }
        } catch (e) { /* 继续尝试下一来源 */ }

        // ② 持久化日志（agent 不在时）
        if (!route) {
          for (let i = raw.length - 1; i >= 0; i--) {
            const ev = raw[i];
            if (!ev || ev.type !== 'request/header') continue;
            const cfg = ev.data && ev.data.header && ev.data.header.config;
            if (cfg && cfg.provider && cfg.model) {
              route = { provider: String(cfg.provider), model: String(cfg.model) };
            }
            break;
          }
        }
        // 下发用的事件流：仍按 RAW_DROP 过滤（高频低值事件不入前端）
        const all = raw.filter((e) => !RAW_DROP.has(e.type));
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
          route,
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

  // ══ 【三大防线 · 签名式信任根】══════════════════════════════════════════
  // 定位：签名是主防线，守卫是围绕签名服务的补丁。
  //   · 签名：HMAC + 密钥隔离（只存宿主内存）+ 时间窗 + nonce。
  //     防的是 AI 直接 write 一份格式完美的伪造凭据。
  //   · 守卫：文件监控 + 台账比对 + 无效凭据记异常。
  //     它不产生信任，只保证校验读到的是签名认可的版本；
  //     读不到合法文件时，defense_gate 会因验签失败而阻断（fail-closed）。
  // 范围：仅工程模式 —— tools 目录不存在时不构造、不注册、不写文件。
  let defenseSigner = null;
  try {
    const _toolsDir = findToolsDir();
    if (_toolsDir) {
      defenseSigner = new DefenseSigner(_toolsDir, (m) => {
        try { console.log("[defense] " + m); } catch (e) {}
      });
      const _port = (ctx.webServer && ctx.webServer.port) || null;
      defenseSigner.writeRuntime(_port);
      defenseSigner.startGuard(5000);
      disposers.push(() => { try { defenseSigner.stopGuard(); } catch (e) {} });
    }
  } catch (e) {
    console.error("[dsh-engineering-ui] defense signer init", e);
  }

  const readJsonBody = async (req) => {
    try { return JSON.parse(await readBody(req)); } catch (e) { return null; }
  };

  disposers.push(ctx.webServer.register({
    kind: "exact",
    path: PREFIX + "/defense/sign",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      const body = await readJsonBody(req);
      if (!body || typeof body !== "object") return send(res, 400, { ok: false, error: "bad json" });
      const kind = String(body.kind || "");
      const room = String(body.room || "").trim();
      // ── 【Bug8 修复】room 校验按凭据类型区分 ────────────────────────
      // 物理/领域凭据【必须】绑定房间（房间是判定的作用域）；
      // 而材料凭据是【按零件】的事实（零件可能尚未归属任何房间，
      //   例如单独建模/批量生成），因此允许 room 为空 ——
      //   否则 DSH_ROOM 未设时材料凭据永远签不出来。
      const _needRoom = (kind === "physics" || kind === "domain");
      if (_needRoom && !ROOM_RE.test(room)) {
        return send(res, 400, { ok: false, error: "bad room" });
      }
      if (room && !ROOM_RE.test(room)) {
        return send(res, 400, { ok: false, error: "bad room" });
      }
      let payload = null;
      if (kind === "physics") {
        const built = defenseSigner.buildPhysicsPayload(room, body.report_path);
        if (!built.ok) return send(res, 200, { ok: false, error: "物理凭据拒绝签发: " + built.reason });
        payload = built.payload;
      } else if (kind === "domain") {
        const dom = String(body.domain || "").trim();
        if (!/^[A-Za-z0-9_-]{1,32}$/.test(dom)) return send(res, 400, { ok: false, error: "bad domain" });
        const _td = findToolsDir() || "";
        // ── 【Bug8 修复】规则文件必须能在【多个部署位置】找到 ──────────────
        // 宿主按自己的 toolsDir 拼路径，而工程模式存在工作区/安装目录两份
        //   副本；若宿主那份缺 physics/rules，就会误报
        //   "规则文件无法解析 <domain>"，导致领域凭据永远签不出来。
        const _ruleCands = [];
        try {
          _ruleCands.push(path.join(_td, "physics", "rules", dom + "_rules.json"));
          const _home = process.env.USERPROFILE || process.env.HOME || "";
          const _dshHome = process.env.DSH_HOME
            || (_home ? path.join(_home, ".dsh") : "");
          if (_dshHome) {
            _ruleCands.push(path.join(_dshHome, ".agent-presets", "engineering",
                                      "tools", "physics", "rules", dom + "_rules.json"));
            _ruleCands.push(path.join(_dshHome, "engineering", "tools",
                                      "physics", "rules", dom + "_rules.json"));
          }
        } catch (e) {
          // 环境变量缺失不影响主候选
        }
        let rulesPath = null;
        for (const _rc of _ruleCands) {
          try {
            if (fs.existsSync(_rc)) { rulesPath = _rc; break; }
          } catch (e) {
            continue;
          }
        }
        if (!rulesPath) {
          return send(res, 200, { ok: false,
            error: "领域凭据拒绝签发: 规则文件不存在 " + dom,
            tried: _ruleCands });
        }
        if (!Array.isArray(body.violations)) {
          return send(res, 200, { ok: false, error: "领域凭据拒绝签发: violations 必须是数组" });
        }
        // 【缺口5 修复】不再原样签调用方给的 violations —— 宿主自己加载规则文件，
        //   复核其结构并把"宿主确认过的事实"（规则哈希 + 规则条数 + 违规条目）
        //   纳入待签体。调用方仍可声称 violations=[]，但待签体由宿主构造，
        //   且 rules_sha256 会被校验端比对，使"换一套规则"也会被发现。
        let ruleCount = -1;
        try {
          const rj = JSON.parse(fs.readFileSync(rulesPath, "utf8"));
          ruleCount = Array.isArray(rj.rules) ? rj.rules.length : -1;
        } catch (e) {
          ruleCount = -1;
        }
        if (ruleCount < 0) {
          return send(res, 200, { ok: false, error: "领域凭据拒绝签发: 规则文件无法解析 " + dom });
        }
        payload = {
          kind: "domain",
          // schema / at / ts 由宿主在签名时写定（Python 侧不得再补，否则验签失配）
          schema: "dsh-domain-attestation/1",
          at: new Date().toISOString(),
          ts: Date.now(),
          room, domain: dom,
          rules_sha256: fileSha256(rulesPath),
          rule_count: ruleCount,
          violations: body.violations,
          score: typeof body.score === "number" ? body.score : null,
          checked_items: Array.isArray(body.checked_items) ? body.checked_items : [],
          // 校验端 check_domain_attestation() 会读这两项作为"真的跑过校验"的凭据
          warnings: Array.isArray(body.warnings) ? body.warnings : [],
          passed: Array.isArray(body.passed) ? body.passed : [],
          source: body.source === undefined ? "physics_bridge" : String(body.source),
        };
      } else if (kind === "material") {
        const partPath = String(body.part_path || "").trim();
        if (!partPath || !fs.existsSync(partPath)) {
          return send(res, 200, { ok: false, error: "材料凭据拒绝签发: 零件文件不存在" });
        }
        const st = fs.statSync(partPath);
        if (st.size < 4096) {
          return send(res, 200, { ok: false, error: "材料凭据拒绝签发: 零件文件过小（疑似伪造）" });
        }
        // ── 【签后补字段 BUG 修复】材料凭据同样必须一次签全 ──────────────
        // 校验端 check_material_attestation() 读的是 part / part_name /
        //   part_md5 / schema / attested_* / ok / approximate_match /
        //   material_mismatch_rejected 等字段，而原先宿主只签
        //   part_path / part_sha256 / applied_name / density_kg_m3 / source。
        //   Python 侧只好在签名后补十几个字段 → HMAC 必然失配 → 防线①也失效。
        //   现在把这些字段全部纳入待签体：
        //     · part_sha256 / part_size_bytes / part_md5 由宿主【自己读盘】算出，
        //       调用方无法伪造（这正是信任根的职责）；
        //     · 其余材料事实（材料名/密度/家族/是否近似匹配）由调用方声明，
        //       宿主原样纳入签名 —— 它们随后不可再被静默篡改。
        const _md5 = (() => {
          try {
            return crypto.createHash("md5").update(fs.readFileSync(partPath)).digest("hex");
          } catch (e) { return null; }
        })();
        const _apName = body.applied_name === undefined ? null : String(body.applied_name);
        const _dens = typeof body.density_kg_m3 === "number" ? body.density_kg_m3 : null;
        payload = {
          kind: "material",
          // schema/room/at/ts 一并签名：校验端会读它们，事后不得再补
          schema: "dsh-material-attestation/1",
          room,
          at: new Date().toISOString(),
          ts: Date.now(),
          part: path.resolve(partPath),
          part_name: path.basename(partPath).replace(/\.[^.]+$/, ""),
          part_path: path.resolve(partPath),
          part_sha256: fileSha256(partPath),
          part_size_bytes: st.size,
          part_md5: _md5,
          applied_name: _apName,
          density_kg_m3: _dens,
          source: body.source === undefined ? null : String(body.source),
          // 校验端归一化字段
          attested_material: _apName,
          attested_density_kg_m3: _dens,
          attested_family: body.attested_family === undefined ? null : String(body.attested_family),
          ok: body.ok === undefined ? null : !!body.ok,
          approximate_match: !!body.approximate_match,
          material_mismatch_rejected: !!body.material_mismatch_rejected,
        };
      } else {
        return send(res, 400, { ok: false, error: "unknown kind: " + kind });
      }
      const signed = defenseSigner.sign(payload);
      if (!signed) return send(res, 200, { ok: false, error: "签名失败（密钥未就绪）" });
      return send(res, 200, { ok: true, credential: { ...payload, ...signed } });
    }
  }));

  disposers.push(ctx.webServer.register({
    kind: "exact",
    path: PREFIX + "/defense/verify",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配" });
      const cred = await readJsonBody(req);
      if (!cred || typeof cred !== "object") return send(res, 400, { ok: false, error: "bad json" });
      const v = defenseSigner.verify(cred);
      return send(res, 200, { ok: !!v.ok, verified: !!v.ok, reason: v.reason || null, kid: defenseSigner.kid });
    }
  }));

  disposers.push(ctx.webServer.register({
    kind: "exact",
    path: PREFIX + "/defense/info",
    handler: async (req, res) => {
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      return send(res, 200, defenseSigner.status());
    }
  }));

  // ── /defense/judge：宿主侧执行完整防线判定（C 方案核心） ────────────
  // 判定权收回宿主：Python 侧不再自行组合"验签 + 规则"，而是把房间上报，
  //   由宿主读凭据、验签、判定并返回结论。这样 AI 在 Python 侧没有可冒充
  //   的判定点（它若伪造判定结果，等于篡改 defense_gate 本身，属另一类攻击，
  //   且守约会记录凭据异常）。
  disposers.push(ctx.webServer.register({
    kind: "exact",
    path: PREFIX + "/defense/judge",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      const body = await readJsonBody(req);
      if (!body || typeof body !== "object") return send(res, 400, { ok: false, error: "bad json" });
      const room = String(body.room || "").trim();
      if (!ROOM_RE.test(room)) return send(res, 400, { ok: false, error: "bad room" });
      const result = defenseSigner.judgeDefense(room, String(body.kind || "room"), {
        requirePhysics: body.require_physics !== false,
      });
      return send(res, 200, result);
    }
  }));

  /** 计算文件 sha256（读失败返回 null）。 */
  function fileSha256(p2) {
    try {
      return crypto.createHash("sha256").update(fs.readFileSync(p2)).digest("hex");
    } catch (e) { return null; }
  }

  // ══ 【连接区】SW / CAD 连接状态：读状态 + 触发探测 ═══════════════════════
  // 设计要点（与 conn_state.py 同一份契约）：
  //   · 状态落成【JSON 文件】(connection_state.json)，与 mode_state.json 同目录，
  //     这样【设置页 UI】与【AI 流程】读的是同一份真相 ——
  //     只放 localStorage 的话 AI 侧读不到，"流程中做代码判断"无从实现。
  //   · 探测动作【委托给 Python】(sw_bridge.py conn-probe)，宿主不自己写
  //     COM/进程逻辑 —— 避免 Node 与 Python 两套探测结论互相打架。
  //   · 探测有副作用风险（COM Dispatch 可能把 SW 拉起来），
  //     因此【只在用户点「连接」时】触发；GET 只读文件，绝无副作用。

  /** 解析 connection_state.json 路径（与 Python _store.state_dir() 同优先级）。 */
  function connStatePath() {
    const dir = findToolsDir();
    if (!dir) return null;
    return path.join(dir, 'connection_state.json');
  }

  /** 读取连接状态（纯文件读，零副作用）。 */
  function readConnState() {
    const p2 = connStatePath();
    if (!p2) return { ok: false, error: '未定位到 tools 目录' };
    const j = readJsonSafe(p2);
    if (!j) {
      return {
        ok: true, state_file: p2, updated_at: null, age_sec: null,
        stale: true,
        sw: { connected: false, running: null, checked_at: null },
        cad: { connected: false, running: null, checked_at: null },
        paths: {},
      };
    }
    const age = j.updated_at ? Math.max(0, Date.now() / 1000 - Number(j.updated_at)) : null;
    return {
      ok: true,
      state_file: p2,
      updated_at: j.updated_at_str || null,
      age_sec: age,
      // 与 Python 侧 STALE_AFTER_SEC 保持一致（900s）
      stale: age == null || age > 900,
      sw: j.sw || { connected: false, running: null, checked_at: null },
      cad: j.cad || { connected: false, running: null, checked_at: null },
      paths: j.paths || {},
    };
  }

  /** 找到 Python 解释器（优先 python，回退 py -3 / python3）。 */
  function resolvePython() {
    const cands = [];
    if (process.env.DSH_PYTHON) cands.push(process.env.DSH_PYTHON);
    cands.push('python', 'python3', 'py');
    for (const c of cands) {
      try {
        const r = spawnSync(c, ['--version'], { encoding: 'utf8', timeout: 15000 });
        if (r && r.status === 0) return c;
      } catch (e) { /* 试下一个 */ }
    }
    return null;
  }

  /** 触发一次真实探测（会调 Python，可能连接 SW/CAD）。 */
  function runConnProbe(target) {
    const dir = findToolsDir();
    if (!dir) return { ok: false, error: '未定位到 tools 目录' };
    const bridge = path.join(dir, 'sw_bridge.py');
    if (!fs.existsSync(bridge)) {
      return { ok: false, error: '未找到 sw_bridge.py: ' + bridge };
    }
    const py = resolvePython();
    if (!py) return { ok: false, error: '未找到可用的 Python 解释器' };
    const args = [bridge, 'conn-probe'];
    if (target === 'sw' || target === 'cad') args.push('--target', target);
    try {
      const r = spawnSync(py, args, {
        encoding: 'utf8', timeout: 120000, cwd: dir,
        windowsHide: true,
      });
      const out = String((r && r.stdout) || '');
      const errOut = String((r && r.stderr) || '');
      let parsed = null;
      // sw_bridge 输出 JSON；取最后一段完整 JSON
      const start = out.indexOf('{');
      if (start >= 0) {
        try { parsed = JSON.parse(out.slice(start)); } catch (e) { parsed = null; }
      }
      if (!parsed) {
        return {
          ok: false,
          error: '探测未返回可解析 JSON',
          stdout_tail: out.slice(-800),
          stderr_tail: errOut.slice(-800),
        };
      }
      // 探测完成后回读文件，保证 UI 拿到的是落盘后的真相
      const fresh = readConnState();
      return { ok: true, probe: parsed, state: fresh };
    } catch (e) {
      return { ok: false, error: '探测执行异常: ' + String((e && e.message) || e) };
    }
  }

  // GET：只读状态（零副作用）—— 设置页打开时轮询用
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/conn-state',
    handler: async (req, res) => {
      try {
        return send(res, 200, readConnState());
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // POST：触发探测（点「连接」按钮时才走这里）
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/conn-probe',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const body = await readJsonBody(req);
      const target = String((body && body.target) || '').trim().toLowerCase();
      if (target !== 'sw' && target !== 'cad' && target !== 'all') {
        return send(res, 400, { ok: false, error: "target 必须是 'sw' | 'cad' | 'all'" });
      }
      try {
        const r = runConnProbe(target === 'all' ? null : target);
        return send(res, 200, r);
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── POST /conn-launch：显式启动 SW / CAD（「启动」按钮）──────────────────
  // 用户诉求："给俩个都搞一个按键，自动启动吧，不然一直点连接启动不了的，
  //   就是启动成不需要 SW 自己跳出来的那种。"
  // 与 /conn-probe 的区别：这个真的会拉起进程。因此【只由用户点击触发】，
  //   不做任何自动调用；且耗时较长（SW 冷启动可达数十秒），
  //   故给足超时（默认 120s + 余量）。
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/conn-launch',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const body = await readJsonBody(req);
      const target = String((body && body.target) || '').trim().toLowerCase();
      if (target !== 'sw' && target !== 'cad') {
        return send(res, 400, { ok: false, error: "target 必须是 'sw' | 'cad'" });
      }
      const dir = findToolsDir();
      if (!dir) return send(res, 200, { ok: false, error: '未定位到 tools 目录' });
      const bridge = path.join(dir, 'sw_bridge.py');
      if (!fs.existsSync(bridge)) {
        return send(res, 200, { ok: false, error: '未找到 sw_bridge.py: ' + bridge });
      }
      const py = resolvePython();
      if (!py) return send(res, 200, { ok: false, error: '未找到可用的 Python 解释器' });
      try {
        const r = spawnSync(py, [bridge, 'conn-launch', '--target', target], {
          encoding: 'utf8', timeout: 240000, cwd: dir, windowsHide: true,
        });
        const out = String((r && r.stdout) || '');
        const errOut = String((r && r.stderr) || '');
        let parsed = null;
        const start = out.indexOf('{');
        if (start >= 0) {
          try { parsed = JSON.parse(out.slice(start)); } catch (e) { parsed = null; }
        }
        if (!parsed) {
          return send(res, 200, {
            ok: false,
            error: '启动命令未返回可解析 JSON',
            stdout_tail: out.slice(-1500),
            stderr_tail: errOut.slice(-1500),
          });
        }
        // 启动后回读状态，保证 UI 拿到落盘后的真相
        return send(res, 200, { ok: !!parsed.ok, ...parsed, state: readConnState() });
      } catch (error) {
        return send(res, 200, { ok: false, error: '启动执行异常: ' + String((error && error.message) || error) });
      }
    }
  }));

  // ── POST /conn-diagnose：把启动日志【当作消息发给当前会话的模型】────────
  // 用户诉求："加上一个按键，按了就可以将错误日志直接发给 DSH 内部模型的，
  //   然后 DSH 就可以根据错误日志去找到底是啥情况。"
  // 实现：包装成一条 user 消息并用 agent.steer() 投递（与收尾守卫同一机制），
  //   模型收到后会带着日志内容与工具能力去排查根因。
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/conn-diagnose',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const body = await readJsonBody(req);
      const target = String((body && body.target) || 'sw').trim().toLowerCase();
      const provided = String((body && body.log_text) || '');
      const wantSid = String((body && body.sessionId) || '').trim();

      // 优先用前端传来的日志；没传就现场向后端取一份（含环境事实）
      let logText = provided;
      if (!logText) {
        try {
          const dir0 = findToolsDir();
          const py0 = resolvePython();
          if (dir0 && py0) {
            const rr = spawnSync(py0, [path.join(dir0, 'sw_bridge.py'),
                                       'conn-log', '--target', target],
                                 { encoding: 'utf8', timeout: 60000, cwd: dir0, windowsHide: true });
            const oo = String((rr && rr.stdout) || '');
            const st0 = oo.indexOf('{');
            if (st0 >= 0) {
              try { logText = (JSON.parse(oo.slice(st0)) || {}).log_text || ''; } catch (e) {}
            }
          }
        } catch (e) { /* 取不到就用空，下面会兜底 */ }
      }
      if (!logText) {
        return send(res, 200, { ok: false, error: '没有可发送的启动日志（请先点「启动」）' });
      }

      // 找目标 agent：显式 sessionId > roots()[0]
      const agents = ctx.agents;
      if (!agents || typeof agents.get !== 'function') {
        return send(res, 200, { ok: false, error: 'agents 服务不可用' });
      }
      let agent = wantSid && ID_RE.test(wantSid) ? agents.get(wantSid) : undefined;
      if (agent === undefined) {
        try {
          const roots = typeof agents.roots === 'function' ? agents.roots() : [];
          agent = (roots && roots.length) ? roots[0] : undefined;
        } catch (e) { agent = undefined; }
      }
      if (agent === undefined || agent === null) {
        return send(res, 200, {
          ok: false,
          error: '会话不在运行中（请保持主对话开启后再点「发给模型诊断」）',
        });
      }

      const name = target === 'cad' ? 'AutoCAD' : 'SolidWorks';
      const text = [
        `【连接区诊断请求】${name} 启动失败，下面是完整日志与机器环境事实。`,
        '',
        '请据此判断失败的**根本原因**，并给出**具体可执行的下一步**。',
        '可用手段：阅读 常见SW启动失败问题.md、运行 sw_bridge.py doctor / sw-proc、',
        '检查许可证(sw_d.lic)/netapi32.dll/安装盘符/沙箱限制等。',
        '',
        '```',
        logText,
        '```',
      ].join('\n');

      try {
        const msg = createUserMessage({
          content: [{ type: 'text', text }],
          source: { kind: 'plugin:dsh-engineering-ui' },
        });
        agent.steer(msg);
        return send(res, 200, { ok: true, target, delivered_to: String(agent.sessionId || agent.id || ''), chars: text.length });
      } catch (error) {
        return send(res, 200, { ok: false, error: '投递失败: ' + String((error && error.message) || error) });
      }
    }
  }));

  // ══ 【独立聊天页】推荐追问生成（POST /suggest）═════════════════════════
  // 用户需求（对照 ChatGPT 的"相关追问"）：模型每次生成结果后，给出几条
  //   顺着当前话题往下走的推荐问题。
  //
  // 设计（与官方 session-title-llm 的辅助调用同一套做法，见
  //   packages/session/session-title-llm/src/index.ts）：
  //   · 一次【一次性】辅助请求：system 给死规则，messages 只带最近若干轮文本；
  //   · 用 BlockAssembler 把流收成文本，再解析成一问一行的数组；
  //   · 【不落库】：这是一次纯 UI 建议生成，不写会话事件、不进模型上下文，
  //     因此不会污染工程模式的会话日志与守卫判定。
  //     （官方 session-title-llm 会 append 一条 log-only 请求事件；这里更保守，
  //      连该事件也不写 —— 因为它不是会话语义的一部分。）
  //   · 失败一律返回 ok:false，前端静默忽略 —— 推荐追问是可选增强，
  //     绝不能因为模型/网络问题影响聊天本身。
  //
  // 模型路由：优先用请求里显式给的 provider/model（前端可从会话头部读），
  //   否则回退到 process.env 配置，再否则放弃生成。
  const SUGGEST_SYSTEM = [
    'You generate follow-up question suggestions for a user in a chat with an engineering AI assistant.',
    'Read the recent conversation and propose exactly 3 short follow-up questions THE USER might ask next.',
    'Rules:',
    '- Write in the SAME language as the conversation (Chinese conversation -> Chinese questions).',
    '- Each suggestion must be a question or imperative the user would say, NOT the assistant.',
    '- Keep each under 24 characters (CJK) or 12 words (English). No numbering, no quotes, no trailing punctuation.',
    '- Base them on the actual topic just discussed; make them concrete, never generic like "tell me more".',
    '- Output exactly 3 lines, one suggestion per line, nothing else.',
  ].join('\n');

  /**
   * 把一次 LLM 流收成纯文本。
   *
   * ── 为什么不用官方的 BlockAssembler ──────────────────────────────────────
   * 官方确实提供 BlockAssembler（dsh-llm 的 assembler.ts，且从包根导出），
   *   用它也能work。但本插件刻意【不新增任何 import】：
   *   · 本插件是【独立分发包】，要保证别人 clone 下来即可运行；每多一个
   *     从官方包静态 import 的名字，就多一个"该名字在本机 DSH 构建里
   *     是否存在/同名同形"的假设，一旦 DSH 版本变动就会让整个 Host 插件
   *     求值失败（连带守卫与三大防线一起失效）。
   *   · 只用手写累加，依赖面收敛到【流协议本身】（StreamChunk），这是
   *     最稳定的一层契约：
   *       { type:'text-delta', index, text }
   *       { type:'finish', reason: { kind } }
   *   · 包内测试用 data-URL 替身模拟官方模块，替身只有 createUserMessage；
   *     不引入新导出，测试替身无需改动、回归测试保持绿色。
   *
   * @param llm - ctx.llm 服务。
   * @param options - GenerateOptions。
   * @returns 累计的纯文本（多块按换行拼接）。
   */
  async function collectLlmText(llm, options, timeoutMs) {
    let text = '';
    let finishKind = null;
    let finishMsg = '';
    // ── 超时保护（修「有概率超时/报错」）────────────────────────────────
    // 曾经的问题：流一旦不结束就永远挂着，前端只能一直转圈或被更外层的
    //   超时打断，错误信息也看不出原因。
    // 这里主动给一个上限：到点 abort，让它变成一个【可解释】的错误。
    // 官方适配器契约要求遵守 options.signal（llm/src/index.ts:286-290）。
    const ms = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 90000;
    const ctrl = new AbortController();
    const timer = setTimeout(() => { try { ctrl.abort(); } catch (e) {} }, ms);
    try {
      for await (const chunk of llm.stream(Object.assign({}, options, { signal: ctrl.signal }))) {
        if (!chunk || typeof chunk !== 'object') continue;
        if (chunk.type === 'text-delta' && typeof chunk.text === 'string') {
          text += chunk.text;
        } else if (chunk.type === 'finish') {
          const reason = chunk.reason || {};
          finishKind = reason.kind || null;
          finishMsg = (reason.failure && reason.failure.message) || '';
        }
      }
    } catch (error) {
      // 中断 → 给一个用户看得懂的原因，而不是底层 AbortError
      if (ctrl.signal.aborted) {
        throw new Error('请求超时（超过 ' + Math.round(ms / 1000) + ' 秒未完成）');
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
    // 只有 stop（或无 finish 帧）算正常收尾；其余都当失败。
    // aborted 单独说明：多半就是上面的超时。
    if (finishKind && finishKind !== 'stop') {
      if (finishKind === 'aborted') {
        throw new Error('请求被中断' + (ctrl.signal.aborted ? '（超时）' : '') +
          (finishMsg ? '：' + finishMsg : ''));
      }
      throw new Error('模型返回异常（' + String(finishKind) + '）' + (finishMsg ? '：' + finishMsg : ''));
    }
    return text;
  }

  /** 解析模型输出为最多 3 条建议（容错：去序号/引号/空行）。 */
  function parseSuggestions(text) {
    return String(text || '')
      .split(/\r?\n/)
      .map((s) => s.trim()
        .replace(/^[-*•]\s*/, '')            // 去掉项目符号
        .replace(/^\d+[.)、]\s*/, '')        // 去掉 "1. " / "1、"
        .replace(/^["'“”「『]+|["'“”」』]+$/g, '')  // 去掉包裹引号
        .trim())
      .filter((s) => s && s.length <= 120)
      .slice(0, 3);
  }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/suggest',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const turns = Array.isArray(payload && payload.turns) ? payload.turns : [];
      if (!turns.length) return send(res, 200, { ok: false, error: 'no turns' });

      // llm 走 ctx.get：未装配时静默降级，绝不影响其他功能（见 inject 注释）
      let llm = null;
      try { llm = ctx.get ? ctx.get('llm') : ctx.llm; } catch (e) { llm = null; }
      if (!llm || typeof llm.stream !== 'function') {
        return send(res, 200, { ok: false, error: 'llm 服务不可用（已跳过推荐追问）' });
      }

      const provider = String((payload && payload.provider) || process.env.DSH_SUGGEST_PROVIDER || '').trim();
      const model = String((payload && payload.model) || process.env.DSH_SUGGEST_MODEL || '').trim();
      if (!provider || !model) {
        return send(res, 200, {
          ok: false,
          error: '缺少模型路由（provider/model）：请由前端传入或设置 DSH_SUGGEST_PROVIDER / DSH_SUGGEST_MODEL',
        });
      }

      // 只取最近 6 条，压成纯文本；过长截断，避免把整段代码塞进辅助请求
      const transcript = turns.slice(-6).map((t) => {
        const who = String((t && t.role) || 'user') === 'assistant' ? 'Assistant' : 'User';
        const text = String((t && t.text) || '').replace(/\s+/g, ' ').trim().slice(0, 1200);
        return who + ': ' + text;
      }).filter((s) => s.length > 8).join('\n');
      if (!transcript) return send(res, 200, { ok: false, error: 'no usable transcript' });

      try {
        const text = await collectLlmText(llm, {
          provider,
          model,
          system: SUGGEST_SYSTEM,
          // ── 【必须用 RequestUserInput，不能用 durable Message】────────────
          // 官方契约（dsh-llm/src/types.ts:486-495）：RequestUserInput 显式
          //   禁止 id/source，语义是"仅供本次请求、不落库"。
          // 这正是本场景 —— auto-review 的 classifyRisk 就是这么做的
          //   （packages/experimental/auto-review/src/index.ts:617-638），
          //   注释明写该 prompt 永不进入 Session log。
          // 若改用 createUserMessage(source:{kind:'plugin:...'}) 反而更糟：
          //   durable 消息要求 source.kind 已在 MessageSourceMap 里声明，
          //   本插件没有做 declaration merge，会被判为未知来源。
          messages: [{
            role: 'user',
            content: [{ type: 'text', text: 'Recent conversation:\n' + transcript }],
          }],
          maxTokens: 256,
          // ⚠️ 刻意不传 purpose：它是【闭集】'compaction' | 'session-title'
          //   （types.ts:552），不是可合并扩展的 map。传自定义值非法；
          //   传那两个字面量则语义错误（会改变适配器行为，如关闭思考）。
          //   一次性辅助调用正确做法就是省略它。
        });
        const items = parseSuggestions(text);
        if (!items.length) return send(res, 200, { ok: false, error: 'empty suggestions' });
        return send(res, 200, { ok: true, items, provider, model });
      } catch (error) {
        // 失败静默：推荐追问不是关键路径
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ══ 【划词详情】侧边分析对话（POST /side-chat）══════════════════════════
  // 用户需求（对照三张截图）：
  //   · 在 Agent 回复里【划选一段文字】→ 出现「更多详情」；
  //   · 点它 → 右下角弹出一个小聊天面板，就这段内容与你展开分析；
  //   · 结果可以「添加到对话」回填到主对话输入框。
  //
  // 设计要点：
  //   · 【无状态】：历史由前端持有并每次整包回传。宿主不写任何文件、
  //     不落会话日志 —— 这样它既不污染工程模式的会话与守卫判定，
  //     也让"别人 clone 下来即用"成立（无需任何持久化目录假设）。
  //   · 【一次性辅助调用】：与 /suggest 同一套做法（不落库、不建 Agent）。
  //     用同一个模型路由（前端把当前会话的 provider/model 传进来）。
  //   · 失败一律 ok:false，前端显示错误 —— 这是用户主动发起的操作，
  //     不能像 /suggest 那样静默。
  const SIDE_CHAT_SYSTEM = [
    'You are a focused engineering analysis assistant shown in a side panel.',
    'The user selected a passage from an AI agent conversation and wants deeper analysis of it.',
    '',
    'Rules:',
    '- Answer in the SAME language as the selected passage and the user question.',
    '- Ground every claim in the selected passage; quote it when useful.',
    '- Be concrete and technical. If the passage contains a plan, a result, or code,',
    '  point out assumptions, risks, and what to verify next.',
    '- Keep it tight: a few short paragraphs or a compact list. No filler, no restating the question.',
    '- You have no tools; if something needs execution, say what should be run instead of pretending.',
  ].join('\n');

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/side-chat',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }

      // llm 走 ctx.get：未装配时明确报错（用户主动操作，不静默）
      let llm = null;
      try { llm = ctx.get ? ctx.get('llm') : ctx.llm; } catch (e) { llm = null; }
      if (!llm || typeof llm.stream !== 'function') {
        return send(res, 200, { ok: false, error: 'llm 服务不可用（无法进行详情分析）' });
      }

      const provider = String((payload && payload.provider) || '').trim();
      const model = String((payload && payload.model) || '').trim();
      if (!provider || !model) {
        return send(res, 200, {
          ok: false,
          error: '缺少模型路由（provider/model）：前端应从当前会话读取后传入',
        });
      }

      // 选中文本：这是本次分析的锚点，必须有
      const selection = String((payload && payload.selection) || '').trim().slice(0, 8000);
      if (!selection) return send(res, 200, { ok: false, error: '没有选中内容' });

      // 历史：前端整包回传，宿主只用不管生命周期。限制条数与单条长度防滥用。
      const rawHistory = Array.isArray(payload && payload.messages) ? payload.messages : [];
      const history = rawHistory.slice(-20).map((m) => ({
        role: String((m && m.role) || 'user') === 'assistant' ? 'assistant' : 'user',
        text: String((m && m.text) || '').slice(0, 4000),
      })).filter((m) => m.text.trim());

      // 拼成一次请求：
      //   [选中内容（锚点）] + [历史...] + [本次提问]
      // 选中内容每次都带上，保证多轮里锚点不丢（历史是整包来的，不依赖宿主记忆）。
      const parts = [];
      parts.push('【选中的原文】\n' + selection);
      if (history.length) {
        parts.push('【此前的分析对话】\n' + history.map((m) => (
          (m.role === 'assistant' ? 'Assistant: ' : 'User: ') + m.text
        )).join('\n'));
      }
      // 最后一轮如果是用户提问，它就是本次要回答的问题（历史里已含）；
      //   否则给一个默认指令，保证模型有明确任务。
      const last = history.length ? history[history.length - 1] : null;
      if (!last || last.role !== 'user') {
        parts.push('请就以上选中内容给出深入分析：关键结论、隐含假设、风险点与下一步建议。');
      }

      try {
        const reply = await collectLlmText(llm, {
          provider,
          model,
          system: SIDE_CHAT_SYSTEM,
          // RequestUserInput：一次性、不落库（同 /suggest 的理由）
          messages: [{
            role: 'user',
            content: [{ type: 'text', text: parts.join('\n\n') }],
          }],
          maxTokens: 1200,
        }, 90000);
        const text = String(reply || '').trim();
        if (!text) return send(res, 200, { ok: false, error: '模型未返回内容（可能被安全策略拦截或输出为空）' });
        return send(res, 200, { ok: true, reply: text, provider, model });
      } catch (error) {
        // 把失败原因说清楚，前端直接展示（用户要求"必须有效"，不能只转圈）
        const msg = String((error && error.message) || error);
        return send(res, 200, {
          ok: false,
          error: msg,
          hint: /超时/.test(msg)
            ? '模型响应超时。可稍后重试、减少选中文字量，或换一个更快的模型。'
            : (/路由|模型/.test(msg)
              ? '无法确定模型路由：请先在该会话里正常发送一条消息，让会话记录下所用模型。'
              : null),
        });
      }
    }
  }));

  // ══ 【纯聊天开关】同一个会话内切换「聊天 / 工作」════════════════════════
  // 用户要求：在【工程模式里面直接选择聊天】就能纯聊天，不要另开一个预设。
  //
  // ── 为什么之前失败、现在为什么能成 ────────────────────────────────────
  // 之前只想"换个显示"，模型照样挂满工具 → 照样调 skill。
  // 要让同一个 Agent 真的没有工具，必须同时做两件事（都已核对官方契约）：
  //
  //   ① 摘掉工具：ctx.tools.restrict({ deny: [...] })
  //      （dsh-tools/src/index.ts:1097-1123）
  //      · 必须在【作用域上下文】调用（agent.ctx），全局调用会 throw；
  //      · deny 的每个名字都必须是【已知的全局工具名】，否则 throw
  //        —— 所以这里必须先 tools.schemas(agent) 现场取名字，
  //        绝不硬编码（不同预设挂的工具不同，硬编码必崩）；
  //      · 返回 disposer，注销即恢复，天然支持来回切。
  //
  //   ② 换掉系统提示词：systemPrompt.section({ complete: true })
  //      （dsh-system-prompt/src/index.ts:69-75、629-632）
  //      · complete 的 section 会把整个系统提示词替换成它自己：
  //          sections: completeSection === undefined ? transformed.sections : [completeSection]
  //      · 这样那套 700 行 CAD 人设（"必须先调用 mode-selection skill"）
  //        就不再进入请求 —— 模型不会以为自己该去调工具。
  //      · 两件事同时做才算"真正不是 Agent"。
  //
  // 状态：每个 agent 一份，存在内存（Map）。会话重开回到"工作"模式，
  //   这是刻意的保守默认 —— 绝不静默改变用户原本的 Agent 行为。
  const CHAT_MODE_SECTION = 'dsh-engineering-ui:chat-mode';
  const chatModeAgents = new Map();   // agentId -> { restrict, section }
  const chatModeState = new Map();    // agentId -> boolean（当前是否聊天模式）

  /** 聊天模式的完整系统提示词：一段干净的对话人设，且不声称有工具。 */
  function chatModePrompt() {
    return [
      '你是一个友好的对话助手。',
      '',
      '现在是【纯聊天】模式：你没有任何可调用的工具，只能与用户进行自然语言交流。',
      '',
      '规则：',
      '1. 直接回答用户的问题，用清晰、自然的中文（或用户所用的语言）。',
      '2. 不要声称要执行命令、读写文件或运行程序，也不要输出假装是工具调用或执行结果的文本。',
      '3. 如果用户要求做需要工具才能完成的事（读取文件、执行命令、操作 SolidWorks 等），',
      '   直接说明当前处于纯聊天模式、你无法执行，并建议他切回「工作」模式；',
      '   然后仍然尽力用语言帮他分析或解答。',
      '4. 需要用户做选择时，可以正常提问并给出选项，等用户回答。',
    ].join('\n');
  }

  /** 进入聊天模式：摘掉该 agent 的全部工具 + 替换系统提示词。 */
  function enterChatMode(agent) {
    if (!agent || !agent.ctx) throw new Error('agent 不可用');
    // 幂等：已在聊天模式就不重复挂
    if (chatModeAgents.has(agent.id)) return;
    const handles = {};

    // ① 取当前【实际可见】的工具名，逐个 deny。
    //    ⚠️ 必须现场取：restrict() 对未知名字会 throw（tools/src/index.ts:1114-1118），
    //    而工具集合取决于预设，硬编码名字在别的部署上必崩。
    let names = [];
    try {
      const schemas = (typeof ctx.tools.schemas === 'function')
        ? (ctx.tools.schemas(agent) || []) : [];
      names = schemas.map((s) => s && s.name).filter((n) => typeof n === 'string' && n);
    } catch (e) {
      names = [];
    }

    // 逐个尝试 restrict。官方契约（tools/tests/scoped.spec.ts:184-195）：
    //   · 命名 scope 自己的注册会 throw unknown global tool；
    //   · 空过滤会 throw no-op。
    // 因此这里逐个名字单独 try：能被 restrict 的记账，不能被 restrict 的
    //   跳过（它们由下面的 guard 兜底拦执行）。
    // 之所以不整体传一个大数组：一个"未知"名字会让整批失败（全都不生效）。
    const lifters = [];
    for (const n of names) {
      try {
        lifters.push(agent.ctx.tools.restrict({ deny: [n] }));
      } catch (e) {
        // 该名字不可 restrict（scope 自身注册 / 未继承）→ 交给 guard 兜底
      }
    }
    if (lifters.length) {
      handles.restrict = () => { for (const l of lifters) { try { l(); } catch (e) {} } };
    }

    // ② 【执行兜底】guard 拒绝一切工具调用。
    //    为什么还需要它：restrict 只影响"模型看到的工具列表"，若某个工具名
    //    不可 restrict（见上），模型仍可能看到它并尝试调用。guard 是同步的
    //    最终否决权（tools/src/index.ts:1126-1136），保证聊天模式下
    //    【任何】工具都无法真正执行 —— 这是"真正不是 Agent"的硬保证。
    try {
      handles.guard = agent.ctx.tools.guard((exec) => {
        // 聊天模式下拒绝一切工具执行
        const reason = '当前处于【纯聊天】模式，没有可用的工具。'
          + '请直接用语言回答；如需执行操作，请切回「工作」模式。';
        return reason;
      });
    } catch (e) { /* guard 不可用时不阻断（restrict 已尽力） */ }

    // ③ 用 complete section 独占系统提示词
    handles.section = agent.ctx.systemPrompt.section({
      name: CHAT_MODE_SECTION,
      // order 任意（complete section 最终会独占），给身份说明的位置即可
      order: agent.ctx.systemPrompt.getSectionOrder('DEPLOYMENT_PERSONA_PREFIX'),
      text: chatModePrompt(),
      complete: true,
    });
    chatModeAgents.set(agent.id, handles);
  }

  /** 退出聊天模式：恢复工具与系统提示词。 */
  function exitChatMode(agent) {
    const h = chatModeAgents.get(agent.id);
    if (!h) return;
    chatModeAgents.delete(agent.id);
    try { if (h.guard) h.guard(); } catch (e) {}
    try { if (h.restrict) h.restrict(); } catch (e) {}
    try { if (h.section) h.section(); } catch (e) {}
  }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/chat-mode',
    handler: async (req, res) => {
      const q = queryOf(req);
      // GET：查询某会话当前是否聊天模式
      if (req.method === 'GET') {
        const sid = (q.get('sessionId') || '').trim();
        if (!ID_RE.test(sid)) return send(res, 400, { ok: false, error: 'bad sessionId' });
        return send(res, 200, { ok: true, sessionId: sid, chatMode: chatModeState.get(sid) === true });
      }
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'GET or POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const sid = String((payload && payload.sessionId) || '').trim();
      const on = !!(payload && payload.on);
      if (!ID_RE.test(sid)) return send(res, 400, { ok: false, error: 'bad sessionId' });

      const agents = ctx.agents;
      if (!agents || typeof agents.get !== 'function') {
        return send(res, 200, { ok: false, error: 'agents 服务不可用' });
      }
      const agent = agents.get(sid);
      if (agent === undefined || agent === null) {
        return send(res, 200, {
          ok: false,
          error: '会话不在运行中：请保持该对话为当前对话后再切换（聊天模式是运行期状态）',
        });
      }
      try {
        if (on) enterChatMode(agent); else exitChatMode(agent);
        chatModeState.set(sid, on);
        // 报告切换后【实际可见】的工具数量，便于前端确认真的摘干净了
        let left = -1;
        try {
          const sc = (typeof ctx.tools.schemas === 'function') ? (ctx.tools.schemas(agent) || []) : [];
          left = sc.length;
        } catch (e) { left = -1; }
        return send(res, 200, { ok: true, sessionId: sid, chatMode: on, tools_left: left });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // agent 释放时清掉聊天模式状态，避免 Map 泄漏
  disposers.push(ctx.on('agent/disposed', (payload) => {
    try {
      const a = payload && payload.agent;
      if (!a || !a.id) return;
      chatModeAgents.delete(a.id);
      chatModeState.delete(a.id);
    } catch (e) {}
  }));

  // ══ 【工程人设】设置页可填写的自定义提示词（GET/POST /persona）══════════
  // 用户需求：「当使用者使用工程模式的时候，在一开始会当作提示词发给模型」，
  //   内容由用户在【设置 → 工程模式 → 美化工程模式】下的新主栏目里填写。
  //
  // ── 存哪里 ────────────────────────────────────────────────────────────
  // 与工程模式其它状态同目录（findToolsDir() 解析出的 tools 目录）下的
  //   persona.json：{ text: "..." }。
  //   · 与 mode_state.json / workflow_state.json 同一处 → 用户/AI/设置页
  //     读的是同一份真相，不需要额外的目录约定；
  //   · 纯文本、无签名：它不是防线凭据，只是用户自己的提示词。
  // ── 怎么进提示词 ──────────────────────────────────────────────────────
  // 见下方 /persona 之后的 agent/created 监听：为【工程模式】的 agent 注册
  //   一个 per-agent 的 systemPrompt.section()，text 是【函数】——每次装配
  //   现场读取，因此用户改完设置，【下一轮】请求立即生效，无需重启或新建会话。
  /** persona.json 路径（找不到 tools 目录时返回 null）。 */
  function personaPath() {
    const dir = findToolsDir();
    if (!dir) return null;
    return path.join(dir, 'persona.json');
  }

  /** 读自定义人设文本（读不到返回空串）。 */
  function readPersona() {
    const p = personaPath();
    if (!p) return '';
    const j = readJsonSafe(p);
    return (j && typeof j.text === 'string') ? j.text : '';
  }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/persona',
    handler: async (req, res) => {
      if (req.method === 'GET') {
        return send(res, 200, { ok: true, text: readPersona(), file: personaPath() });
      }
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'GET or POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const text = String((payload && payload.text) || '').slice(0, 20000);
      const p = personaPath();
      if (!p) {
        return send(res, 200, {
          ok: false,
          error: '无法定位工程模式 tools 目录，人设未保存',
        });
      }
      try {
        fs.writeFileSync(p, JSON.stringify({ text, updated_at: new Date().toISOString() }, null, 2), 'utf8');
        return send(res, 200, { ok: true, text, file: p });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── 【工程人设】把自定义文本注入【工程模式会话】的系统提示词 ──────────────
  // 官方契约（dsh-system-prompt/src/index.ts:454-463、:53-76）：
  //   · section() 必须在【作用域上下文】里注册（这里用 agent.ctx），
  //     全局注册会与 registry 自己的 persona 注册撞名而失败；
  //   · text 可以是函数 → 每次装配现场求值（官方先例：
  //     context/file-reference-local/src/index.ts:69-75 就是这么做的）；
  //   · order 用 getSectionOrder('DEPLOYMENT_PERSONA_PREFIX')，
  //     即与官方 persona 同段位置 —— 用户人设紧跟身份说明之后，最自然。
  // 作用域：只有【工程模式】的会话才注入（用户明确要求）。
  //   判据优先用会话头的 agentPreset，缺失时回落扫事件（与 client 半一致）。
  const PERSONA_SECTION = 'dsh-engineering-ui:user-persona';

  /** 该 agent 是否为工程模式会话。 */
  function isEngineeringAgent(agent) {
    try {
      const header = agent && agent.session && agent.session.header;
      const direct = header && header.agentPreset;
      if (typeof direct === 'string') return direct === 'engineering';
      // 回退：扫会话事件里最后一次 agent-preset/selected
      const evs = (agent && agent.session && typeof agent.session.snapshotEvents === 'function')
        ? agent.session.snapshotEvents() : (agent && agent.session && agent.session.events);
      if (Array.isArray(evs)) {
        for (let i = evs.length - 1; i >= 0; i--) {
          const ev = evs[i];
          if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
            return String(ev.data.agentPreset) === 'engineering';
          }
        }
      }
    } catch (e) { /* 判定异常 → 按非工程处理（宁可不注入） */ }
    return false;
  }

  const personaFibers = new Map();   // agentId -> disposer
  disposers.push(ctx.on('agent/created', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.ctx || !agent.id) return;
      if (!isEngineeringAgent(agent)) return;
      if (personaFibers.has(agent.id)) return;
      // agent.ctx.inject 等待 systemPrompt 服务就绪；官方同一写法
      const fiber = agent.ctx.inject(['systemPrompt'], (scope) => {
        scope.systemPrompt.section({
          name: PERSONA_SECTION,
          order: scope.systemPrompt.getSectionOrder('DEPLOYMENT_PERSONA_PREFIX'),
          // 函数形式：每轮装配现场读取，改设置后下一轮即生效
          text: () => {
            const t = readPersona().trim();
            if (!t) return '';                 // 没填 → 不贡献任何文本
            return t;
          },
        });
      });
      personaFibers.set(agent.id, fiber);
    } catch (e) {
      // 人设注入失败不能影响会话创建
      try { console.error('[dsh-engineering-ui] persona section', e); } catch (e2) {}
    }
  }));
  // agent 释放时回收（避免 fiber 泄漏）
  const disposePersonaFiber = (agentId) => {
    const f = personaFibers.get(agentId);
    if (!f) return;
    personaFibers.delete(agentId);
    try { const r = f.dispose && f.dispose(); if (r && r.catch) r.catch(() => {}); } catch (e) {}
  };
  disposers.push(ctx.on('agent/disposed', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (agent && agent.id) disposePersonaFiber(agent.id);
    } catch (e) {}
  }));

  return () => {
    for (const dispose of disposers.reverse()) {
      try { dispose(); } catch {}
    }
  };
}
