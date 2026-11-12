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
//
// ══════════════════════════════════════════════════════════════════════════
//  【2026-11-07 · v5 收尾守卫修正】A1 / A11 / A14 / B1
// ══════════════════════════════════════════════════════════════════════════
// 现场（会话 session-dcb91b26，竞速小车，6 h 06 min / 656 次工具调用）暴露的
// 四件事，全部落在本文件的收尾守卫与状态暴露上：
//
//   A1（P0）守卫没有"等待"这一终态 → 模型只能 sleep 轮询。
//     3 次想结束回合被打回（:98/:179/:508）后 5 h 40 m 不再尝试；同一回合
//     用 sleep(30) 轮询 477 次 = 4 h 02 m（占墙钟 66%），几何产出 0 次。
//     → 新增第 4 种【代码可判】终态 [WAITING]：正文独占一行 + 客观证据
//       （房间 active 且近 5 分钟有心跳/报告变化 / 未答提问卡 /
//        workflow_state 挂 pending / 本会话有未 settle 的 job）才放行。
//       只写标记拿不出证据 → 照旧 steer，绝不削弱守卫。
//   A11（P1）守卫把"总计子代理"当建模小屋。
//     总计子代理 d921be74 已完成 10/10 阶段、任务书写明禁调 sw_bridge，却被
//     催着调 sw_bridge → 空转 5 步 53 秒。→ 先按 mode2_plan.json 的
//     analyst.session_id 判角色，命中发阶段命令文案；取不到身份保守回退。
//   A14（P2）压缩阈值固定 ~100K（窗口 200K）。
//     6 次触发点 99,838–100,025，而会话记着 contextWindow:200000。
//     → 阈值 = contextWindow × 0.7（下限 60K，取不到回退 100K 并打日志）。
//       ⚠️ 宿主真实阈值是 min(窗口×ratio, 窗口−reserved−headroom)，
//       只调 ratio 可能反而更低 —— 故同时暴露 limiter 指出被哪一项压住。
//   B1（P0）后台 job 未 settle 就 turn/end(completed)。
//     行走传动间 b55576ac：:672 报 completed、:673 才 job/settled，父代理据此
//     当完成，21 分钟后才发现 run6.log 是 0 字节。
//     → turn 结束时同口径探测未 settle job，写 pending_jobs /
//       turn_completed_but_busy，并在 /parts-board 暴露 pending_jobs_host。
//
// 纯判定逻辑放在 lib/turn-guard.js（无副作用、可单测）；本文件只保留接线。
// 这样拆分是被动的防御：本文件曾被并行改动整份 write 覆盖过两次，
// 判定逻辑放外面才保得住。
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
// 【第4项 · SW 健康看护】CPU/内存采样需要 node:os。
// ⚠️ 这个 import 必须保留：sampleSystem() 用了 os.totalmem()/os.freemem()/os.cpus()，
//    缺了它 → 运行时 ReferenceError → 看护端点装载失败 →
//    /dsh-engineering-ui/sw-guard-job 返回 404（实测症状）。
//    ⚠️ 曾发生过该修复被回退（文件被其它进程覆盖），故这里保留显式注释。
import os from 'node:os';
// 【连接区】需要同步调用 Python 探测器（sw_bridge.py conn-probe）——
//   用 spawnSync 而非 spawn：探测最长 120s，同步等待语义更简单，
//   且设置页是低频人工操作，不占用事件循环热点。
import { spawnSync } from 'node:child_process';
// 【三大防线 · 签名式信任根】仅工程模式装配；密钥只存宿主内存。
import { DefenseSigner } from './defense-sign.js';
// 【卡死修复 · 宿主层】job_output 长等待守卫。纯本地实现、零外部依赖：
//   宿主层少了任何一个包都会让整块 import 求值失败 → 插件不装配 → 修复白做，
//   所以这里只引本插件自己的文件。
import { installJobWaitGuard } from './job-wait-guard.js';
// 【外部监控器接入】SW 使用权/排队/等待/逐零件看板的唯一权威在外部服务。
// 本模块只做宿主桥（token+epoch 留在宿主内存，Python 侧不可见）。
import { MonitorBridge, CONFIG_NAME as SW_MONITOR_CONFIG_NAME } from './sw-monitor.js';
// 【A4】错误码归一 + 退避（纯函数模块，可脱离宿主单测）
import { classifyModelError, backoffMsOf } from './model-errors.js';
// 【BUG-A1/A11/A14/B1 修复】收尾守卫的"可判定事实"层（纯函数、无副作用）。
//   单独成文件是为了把与并行改动的冲突面从"几百行"压到"一条 import"：
//   lib/index.js 曾被另一路 agent 整份 write 覆盖过两次，判定逻辑放外面才保得住。
//   ⚠️ 必须写成【单行 import】：多处测试用 /^import .*$/gm 剥 import 头。
import { hasStandaloneMarker, collectWaitingEvidence, isAnalystAgent as turnGuardIsAnalystAgent, readAnalystSessionId, latestContextWindow, resolveCompactionThreshold, explainCompactionLimit, describePendingJobs, COMPACTION_THRESHOLD_RATIO, ENG_COMPACTION_CONFIG } from './turn-guard.js';
// 【Webots 仿真 · 只读宿主门】配置阶段接入；MCP 行默认禁用，见 preset-engineering.patch.yml。
//   只做「上下文归属校验 + 报告/证据只读路由」，不启动 Webots、不碰 SolidWorks。
import { installWebots } from './webots.js';
// 【内存守卫】子代理并发闸门 + 工作集回收（详见 lib/mem-guard.js 顶部说明）。
//   ⚠️ 这些符号必须显式 import：插件是 ESM，没有 require，缺一个就是
//   "createReclaimer is not defined" —— 而且是在 apply() 跑到一半才炸，
//   表现为整个工程模式装配失败。
//   ⚠️ 必须写成【单行 import】：多处测试用 /^import .*$/gm 剥掉 import 头，
//      多行写法会留下残缺的收尾行，导致 vm 里 SyntaxError。
import { memGuardEnabled, maxActiveSubagents, reclaimThresholdPct, reclaimIntervalMs, evaluateConcurrency, countOccupiedChildren, memorySnapshot, createReclaimer, setMemGuardOverrides, memGuardOverrides } from './mem-guard.js';

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
// 【刻意不 inject llm】只有 /model-catalog（设置页模型目录）会用到它，
// 而 inject 是【硬依赖】：服务缺失会让本插件整体 PENDING、永不 apply，
// 连带收尾守卫与三大防线一起失效 —— 代价远大于收益。
// 因此 llm 走 ctx.get('llm') 惰性读取（与 client 半读 sidebarRight 同一做法），
// 缺失时 /model-catalog 返回 errors[] 并如实标注，主体功能不受影响。
// webots.js 用 ctx.tools.guard 守卫 mcp__webots__* 的执行归属 → 必须声明 tools。
//   注：整块装配已对 ctx.tools 缺失/无 guard 做优雅降级（见 installWebots 返回的守卫），
//   因此旧宿主/测试桩没有 tools 也不会让插件装配失败。
export const inject = ['webServer', 'subagents', 'sessionPersistence', 'sessions', 'agents', 'userQuestions', 'tools'];
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
// ══════════════════════════════════════════════════════════════════════
//  【模型自动接替】额度/限流耗尽时自动切换下一个候选模型
// ══════════════════════════════════════════════════════════════════════
// 用户需求（原文）：「在工程模式的时候，可以指定模型进行选择，类似于第一个
//   模型额度不够嘎了，第二个就立马接上，不至于出现模型会直接死掉的情况，
//   可以直接指定10个模型」。
//
// ── 为什么由本插件做，而不是等宿主 ──────────────────────────────────
// 实测日志（2026-10-30 四工位转台任务）：商汤 sn-sensenova-6-8-flash 账号
//   全部限流时，宿主直接 turn/end error 终止本轮：
//     {"code":"QUOTA_EXCEEDED","message":"...可用账号均在限流中，
//       最近一个将于 ... 解禁"}
//   六个建模子代理 + 主对话全部因此停摆，一个零件都没产出。
//   DSH 自带的 dsh-llm-retry 只做「同 provider 重试」，不换模型。
//
// ── 【2026-11-05 · DSH 0.2.2 适配】换模型改走 agent.model.select() ─────
// 0.2.2 把「同一步内换模型重试」这条路封死了：dsh-agent-loop 在提示词组装
//   前就 this.model.current() 捕获本步路由，并在 prepareRequest 里强校验
//     agent/request proposed "p/m" after step preparation fixed "p/m";
//     use agent.model.select() before step preparation
//   → 旧实现（在 agent/request 里返回 {provider, model}）现在会【直接抛错】，
//     把"能接替"变成"更早地 turn/end error"。
// 现在两条通道并存，按宿主能力自动选：
//   · 0.2.2+（agent.model.select 存在）：
//       agent/request-error → 隔离该 provider + select(下一个候选)
//       → 本步仍以该错误结束，【下一次尝试】直接落到新路由（新的一步 / 下一轮 /
//         新建的子代理都会用新路由）。select 语义就是这样：只对"下一个还没
//         开始准备的步骤"生效，本步连重试都改不动。
//   · 0.2.1 及以前：保留原来的 agent/request 覆盖 + {kind:'retry'} 老路径。
//
// ── 关键约束（踩过的坑，别改回去）────────────────────────────────────
// 1. 【最重要】绝不无条件覆盖模型选择 —— v2 曾把请求每轮改成"链首候选"，
//    结果用户选 DeepSeek 却回 Hy3（插件劫持了选型权）。详见下方
//    agent/request 处的长注释。现在的语义是：默认不介入，只在"本轮已接替"
//    时才覆盖，且一旦发现手动改选立即交还控制权。
// 2. 状态必须【按 agent】保存：不同会话/子代理各有自己的接替进度。
// 3. 只对 engineering 预设生效（复用 isGuardSession 的口径）。
// 4. 任何异常都必须静默降级（next() 原样返回），绝不能因为接替逻辑
//    让本该成功的请求失败。
// 5. 失败后从【当前实际在用的模型】往后找候选，而不是从链首重来；
//    tried 集合按 turn 重置。
const MODEL_CHAIN_FILE = 'model_chain.json';
/**
 * 被认为"该换模型"的错误码（宿主 dsh-llm 的 provider-neutral 码）。
 *
 * ── 【2026-10-30 补充】加上 EMPTY_RESPONSE ────────────────────────────
 * 用户原话：「只要模型在 DSH 中重试超过 5 次那么，反正就是报错了，
 *            模型输不出内容了，那就换下去，尽量保证不要断掉」。
 * EMPTY_RESPONSE 正是"模型正常返回但没有任何内容"（dsh-llm 的规范码）：
 * 适配器把它当失败而不是空消息，因为空消息会让回合静默结束、用户干瞪眼。
 * 这种情况换模型往往立刻就好，所以必须纳入自动接替。
 */
const MODEL_CHAIN_SWITCH_CODES = new Set([
  'QUOTA', 'ACCOUNT_QUOTA', 'QUOTA_EXCEEDED', 'RATE_LIMIT',
  'CONTEXT_WINDOW_EXCEEDED',
  'EMPTY_RESPONSE',            // 模型输不出内容（空响应）
]);

/**
 * 单轮内同一模型连续失败多少次就强制换下一个。
 *
 * 用户要求：「重试超过 5 次就换下去，尽量保证不要断掉」。
 * 为什么不能只靠错误码：有些失败是【反复同一个错】但不属于上面那些码
 * （比如某家 provider 的网关间歇性 5xx、适配器抛了别的 code）。
 * 那些情况下宿主会一直重试同一个模型，用户体验就是"卡住不动"。
 * 所以这里再加一道【计数兜底】：同一模型在同一轮里失败到第 N 次，
 * 不管什么错误码，都直接换下一个候选。
 */
const MODEL_CHAIN_MAX_SAME_FAILS = 5;

/**
 * 规整一条链配置。纯函数，供 host 与测试共用。
 * @param raw 任意输入
 * @returns {{ok:boolean, enabled:boolean, entries:Array<{provider:string,model:string,reasoningEffort?:string}>, error?:string}}
 */
function normalizeModelChain(raw) {
  const out = { ok: true, enabled: false, entries: [] };
  try {
    if (!raw || typeof raw !== 'object') return out;
    out.enabled = raw.enabled === true;
    // ── 自动续跑（0.2.2 起）───────────────────────────────────────────────
    // select() 只对"下一个尚未开始准备的步骤"生效，本轮必定以该错误结束；
    // autoResume（默认 true）打开时，插件在失败后自己补一条「继续」把这一轮
    // 接上，用户不需要手动重发。autoResumeMax 是同一轮用户输入内的续跑上限。
    out.autoResume = raw.autoResume !== false;
    const _max = Number(raw.autoResumeMax);
    out.autoResumeMax = Number.isFinite(_max) && _max > 0 ? Math.min(10, Math.floor(_max)) : 3;
    const _delay = Number(raw.autoResumeDelayMs);
    out.autoResumeDelayMs = Number.isFinite(_delay) && _delay >= 0
      ? Math.min(60000, Math.floor(_delay))
      : 1200;
    const list = Array.isArray(raw.models) ? raw.models : [];
    const seen = new Set();
    for (const m of list) {
      if (!m || typeof m !== 'object') continue;
      const provider = String(m.provider || '').trim();
      const model = String(m.model || '').trim();
      if (!provider || !model) continue;              // 空槽位直接跳过
      const key = provider + '\u0000' + model;
      if (seen.has(key)) continue;                    // 去重
      seen.add(key);
      const entry = { provider, model };
      const eff = String(m.reasoningEffort || '').trim();
      if (eff) entry.reasoningEffort = eff;
      out.entries.push(entry);
      // ── 【2026-10-30 改版】不再限制 10 个 ──────────────────────────────
      // 用户要求：「不限制他们应该加多少个」。
      // 原上限 10 已移除；只保留一个防御性极端上限，避免异常输入撑爆内存。
      if (out.entries.length >= 100) break;
    }
    return out;
  } catch (e) {
    out.ok = false;
    out.error = String((e && e.message) || e);
    return out;
  }
}

/**
 * 从链里挑出下一个候选（排除本次 turn 已尝试过的）。
 * @param entries 规整后的链
 * @param tried   本 turn 已尝试的 key 集合（provider\0model）
 * @returns 候选或 null
 */
function nextModelCandidate(entries, tried) {
  for (const e of (entries || [])) {
    const key = e.provider + '\u0000' + e.model;
    if (tried && tried.has(key)) continue;
    return e;
  }
  return null;
}

function modelKey(provider, model) {
  return String(provider) + '\u0000' + String(model);
}

// 高频低值事件：不入前端流（流式 chunk 每token一条，会挤满窗口）
const RAW_DROP = new Set(['assistant/chunk', 'step/start', 'step/end', 'request/header']);

// ══════════════════════════════════════════════════════════════════════
//  【P0 修复·路由无鉴权】请求来源与来源站校验（单点包装，覆盖全部路由）
// ══════════════════════════════════════════════════════════════════════
// 缺陷：原先 [23 条 HTTP 路由] 全部无鉴权，且与 Harness Web 同源同端口。
//   任何以同用户身份运行的本地进程都能直接调用，后果最重的三条：
//     · POST /persona      —— 写入内容会进入【每个】工程模式会话的 system prompt
//                             （持久化提示词注入）；
//     · POST /conn-launch  —— 让宿主 spawn 本地可执行文件。
//   /defense/verify-gate 不消费 nonce 可作免费验签预言机。
//
// 能做与不能做（如实说明，不假装做到了密码学隔离）：
//   · 同用户下的本地进程【无法】用密码学手段彻底隔离 —— 它们同样能读到
//     进程文件与端口。真正的隔离边界是操作系统账户。
//   · 因此这里采取【纵深防御】，逐层抬高成本：
//       ① 仅接受 loopback 来源（远程连接直接 403）；
//       ② 校验 Origin / Referer：浏览器的跨站请求（CSRF）一律 403；
//       ③ 对本机非浏览器调用（Python 助手）保持放行 —— 否则
//          defense_gate.py / ask_user.py 会全部失效；
//       ④ 对【高副作用】端点额外要求本地令牌（见 env 与 runtime 文件），
//          未配置令牌时退化为"仅 loopback + Origin 校验"并如实记日志；
//       ⑤ 敏感端点限流（原有 rateLimited 复用）。
//   这些措施挡得住"被诱导的浏览器页面"与"远程误连"，
//   挡不住"同用户下的恶意本地程序"——这一点必须明确，不能给使用者错觉。

/** 请求是否来自 loopback（无 socket 信息时按【测试/同进程调用】放行）。 */
function isLoopbackRequest(req) {
  try {
    const sock = req && (req.socket || req.connection);
    const ra = sock && (sock.remoteAddress || (sock.address && sock.address()));
    // 测试 harness 用 Readable 伪造 req，没有 socket → 视为受信任的同进程调用
    if (!ra) return true;
    return ra === '127.0.0.1' || ra === '::1' || ra === '::ffff:127.0.0.1';
  } catch (e) {
    return true;
  }
}

/** 取请求头（大小写不敏感；无 headers 的伪造请求返回空）。 */
function headerOf(req, name) {
  try {
    const h = req && req.headers;
    if (!h) return '';
    const v = h[name] !== undefined ? h[name] : h[name.toLowerCase()];
    if (Array.isArray(v)) return String(v[0] || '');
    return v === undefined || v === null ? '' : String(v);
  } catch (e) {
    return '';
  }
}

/** 浏览器跨站（CSRF）判定：带 Origin/Referer 且不是本机时拒绝。 */
function isCrossSiteRequest(req) {
  const origin = headerOf(req, 'origin');
  const referer = headerOf(req, 'referer');
  const check = (u) => {
    if (!u) return false;
    try {
      const h = new URL(u).hostname;
      return !(h === '127.0.0.1' || h === 'localhost' || h === '::1' ||
               h === '[::1]');
    } catch (e) {
      return true;      // 解析不了的来源一律视为跨站
    }
  };
  if (origin && check(origin)) return true;
  if (referer && check(referer)) return true;
  return false;
}

/**
 * 高副作用端点：需要额外的本地令牌（若已配置）。
 * 令牌来源（按序）：env DSH_ENGINEERING_TOKEN → .defense/local.token。
 * 未配置时【不阻断】（否则会打断现网 Python 助手），但在响应里带
 * degraded_auth=true 并在日志中记录，便于使用者察觉"加固未生效"。
 */
const SENSITIVE_ROUTES = new Set([
  PREFIX + '/persona',
  // 【本机设置写入口】POST /settings 能改"交付目录 / 各软件路径"，
  //   属高副作用（可让宿主后续按任意路径启动进程）→ 与 /persona 同级加固。
  //   只读的 GET（自检、读设置）不在此列，避免设置页一打开就 403。
  PREFIX + '/settings',
  PREFIX + '/conn-launch',
  PREFIX + '/conn-diagnose',
  PREFIX + '/defense/sign',
]);

function localTokenOf() {
  try {
    if (process.env.DSH_ENGINEERING_TOKEN) {
      return String(process.env.DSH_ENGINEERING_TOKEN);
    }
    const dir = findToolsDir();
    if (!dir) return '';
    const p = path.join(dir, '.defense', 'local.token');
    if (!fs.existsSync(p)) return '';
    return String(fs.readFileSync(p, 'utf8')).trim();
  } catch (e) {
    return '';
  }
}

/**
 * 统一请求守卫。返回 {ok:true, degraded?} 或 {ok:false, status, error}。
 * @param {string} routePath - 已注册的路由路径（用于敏感端点判定）
 */
function guardRequest(req, routePath) {
  // ① 仅 loopback
  if (!isLoopbackRequest(req)) {
    return { ok: false, status: 403,
             error: '仅接受本机（loopback）请求：远程访问被拒绝' };
  }
  // ② 浏览器跨站（CSRF）
  if (isCrossSiteRequest(req)) {
    return { ok: false, status: 403,
             error: '拒绝跨站来源请求（Origin/Referer 非本机）' };
  }
  // ③ 高副作用端点：若配置了本地令牌则强制携带
  if (routePath && SENSITIVE_ROUTES.has(routePath)) {
    const want = localTokenOf();
    if (want) {
      const got = headerOf(req, 'x-dsh-engineering-token');
      if (got !== want) {
        return { ok: false, status: 403,
                 error: '该端点需要本机令牌（x-dsh-engineering-token）' };
      }
      return { ok: true };
    }
    // 未配置令牌：放行但如实标注降级
    return { ok: true, degraded: true };
  }
  return { ok: true };
}

/** 包装一个 route 定义，注入统一守卫。 */
function guardRoute(route) {
  const orig = route && route.handler;
  if (typeof orig !== 'function') return route;
  return Object.assign({}, route, {
    handler: async (req, res) => {
      const g = guardRequest(req, route.path);
      if (!g.ok) {
        try { console.warn('[dsh-engineering-ui] 拒绝请求 ' + route.path + '：' + g.error); } catch (e) {}
        return send(res, g.status, { ok: false, error: g.error, path: route.path });
      }
      // ── 【P0 修复】全路由限流 ────────────────────────────────────────
      //   会触发真实 LLM 调用、/conn-launch 会 spawn 本地进程，却完全无限流
      //   → 任何本地进程可反复触发（资源滥用 / 可用性 DoS）。
      //   这里在守卫层统一加桶，按路径分桶；rateLimited 定义在文件后段，
      //   故用 typeof 惰性判定，避免依赖声明顺序。
      if (typeof rateLimited === 'function' &&
          rateLimited('route:' + (route.path || 'unknown'))) {
        return send(res, 429, { ok: false, error: '请求过于频繁（限流）', path: route.path });
      }
      return orig(req, res);
    },
  });
}

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
  '⚠️ 注意：散文式的完成声明【一律无效】——"已完成/已生成/已导出"这类措辞不算数，',
  '   必须让门禁状态文件真正进入终态，或命中下面第 4 条的【客观等待】。',
  '',
  '放行的途径（任一即可）：',
  '  1) 走完 workflow_gate 收尾流程，使 workflow_state.json 的 step 变为 finished；',
  '  2) 让所有房间都 room-end / room-fail，使 mode_state.json 中无 active 房间；',
  '  3) 若任务确实全部结束且无需再操作，在正文【独占一行】写出 [TASK_DONE]',
  '     （该标记必须单独成行且无其它文字；写作"[TASK_DONE]"嵌在句子里无效）。',
  // ── 【BUG-A1 修复】第 4 种合法终态 [WAITING] ──────────────────────────
  // 现场：模型 3 次想结束回合被打回（:98 / :179 / :508），此后 5 h 40 m 不再尝试；
  //   同一回合用 sleep(30) 轮询 477 次 = 4 h 02 m（占墙钟 66%），几何产出 0 次。
  // 根因：守卫只认三种代码级终态。"我在等"在语义上完全合法，但守卫里没有这一档
  //   → 模型想等就只能"占着回合"sleep 轮询，因为一结束回合就被打回。
  // 判据（必须代码可判）：正文单独一行 [WAITING] **且** turn-guard.js 的
  //   collectWaitingEvidence() 拿到客观证据（见该函数注释）。证据不足仍然打回。
  '  4) 若你【确实只能等】（等房间 / 等 SW 锁 / 等后台 job / 等用户作答），',
  '     在正文【独占一行】写出 [WAITING]。系统会先核实客观证据：房间仍在世且',
  '     近 5 分钟内心跳或报告有变化 / 有未答提问卡 / workflow_state 挂 pending /',
  '     本会话仍有未 settle 的后台 job；一条都对不上就打回。',
  '     [WAITING] 只表示"我在等"，不等于任务完成，也不写任何房间状态。',
  '',
  '若任务尚未完成，请继续调用工具推进下一步（room-start / sw_bridge 建模 /',
  '  room-report 上报等），而不是仅用文字描述进展。',
  '',
  '等房间/等锁请优先用阻塞原语（mode_gate.py wait-rooms / sw-wait），一次调用拿到',
  '  结论；用 sleep 循环轮询是本守卫要消除的行为，不是允许的姿势。',
  '',
  '若用户已明确要求停止（"停 / 够了 / 不用了 / 就这样"），直接结束即可。'
].join('\n');

// ── 【2026-10-29 修复】子代理专用守卫文案 ────────────────────────────────
// 主对话那份文案说的是"用 workflow_gate / room-end 让状态进入终态"，
// 但这些都是【编排命令】，子代理本来就被禁止调用（反夺舍规则）。
// 拿主对话的文案去 steer 子代理，等于让它去做一件它无权做的事 ——
// 实测它只能在"I'm still working"和空转之间反复，最后房间判 dead。
// 子代理唯一该做的是：继续推进【本房间的建模/验证】，然后如实停下等父代理收尾。
// ══ 【A11】守卫必须按【角色】发文案，不能把"总计子代理"当建模小屋 ═══
// 现场（d921be74:497/517）：它已完成 10/10 阶段、任务书写明"禁调 sw_bridge"，
//   却被催"直接调 sw_bridge.py run / visual_check.py gate / room-report"，空转 5 步 53 秒。
// 修复：analyst 的 sessionId 由 mode2_flow.py analyst-bind 写进 mode2_plan.json，
//   命中就发专用文案；取不到身份时保守回退原文案。
const GUARD_TEXT_ANALYST = [
  '【系统提示 · 工程模式守卫 · 总计子代理】',
  '你正准备结束本轮。你是【总计子代理（analyst）】，不是建模小屋 ——',
  '你的职责是推进十阶段 / 按需提问 / 决定子代理编制，**不要**去调 sw_bridge，',
  '也不要碰任何建模/装配命令（任务书里通常明写"禁调"）。',
  '',
  '请检查你自己这一轮：',
  '  · 十阶段还没走完 → 用 mode2_flow.py begin-stage / mark-stage-done 推进；',
  '  · 缺信息 → 用 mode2_flow.py ask --stage <阶段> --reason "为什么缺"；',
  '  · 编制未登记 → 用 mode2_flow.py set-subagent-plan（房间模板用 rooms-template）；',
  '  · 都做完了 → 直接结束即可，无需任何标记；父代理会看到你的阶段状态。',
  '',
  '⚠️ 只输出文字而不调用工具，等于没有推进（但也不要用 sw_bridge 冒充进展）。',
].join('\n');

const GUARD_TEXT_SUBAGENT = [
  '【系统提示 · 工程模式守卫 · 子代理】',
  '你正准备结束本轮。你是【建模/专项子代理】，不需要让门禁进入终态 ——',
  '收尾（room-end）由父代理负责，你没有权限、也不应该调用编排命令。',
  '',
  '请检查你自己这一轮：',
  '  · 如果本房间的零件/验证还没做完 → 直接调用工具继续做',
  '    （sw_bridge.py run / visual_check.py gate / room-report / room-artifact），',
  '    不要只写"我接下来要做X"却没有工具调用。',
  '  · 如果你确实已经做完本轮该做的 → 直接结束即可，无需任何标记，',
  '    父代理会通过 room-board / room-report-read 看到你的进度。',
  '',
  '⚠️ 只输出文字而不调用工具，等于没有推进。',
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

  // ── cwd prompt 变量 · 已彻底移除 ──────────────────────────────────────
  // 历史：0.2.1 移除了 dsh-agent-loop 对 cwd 变量的注册，而工程模式人设首行
  //   写了「工作目录 {{cwd}}(已废弃)」，于是每次装配都抛：
  //     unknown prompt variable "{{cwd}}(已废弃)" in section "deployment:persona-prefix"
  //   导致整轮请求失败、工程模式完全不可用。当时的修法是由本插件补注册。
  //
  // 为什么现在改为【人设里不写、插件不注册】：
  //   · 补注册依赖 systemPrompt.variable() 的调用时机 —— 全局注册与 agent
  //     作用域装配之间存在竞态，实测仍会概率性复发（用户两次反馈）；
  //   · 工作目录本就由 DSH 的 working-directory 运行时上下文自动注入
  //     （"Current working directory: ..."），人设里再写一遍是冗余的；
  //   · 少一个自定义变量 = 少一个能让整轮请求失败的故障点。
  //
  // 现状：preset-engineering.patch.yml 与 agent.cordis.yml 均已删除 {{cwd}}(已废弃)，
  //   本插件也不再注册该变量。工作目录信息始终由运行时上下文提供。

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

  // ── 【P0 修复·STOP_WORDS 子串误匹配】─────────────────────────────────
  // 原缺陷：对用户消息做【任意位置子串】匹配，且表里含 '可以了' / '就这样' /
  //   'stop' / 'enough' / 'cancel'。实测后果：用户在讨论里说
  //   "这样可以了吗？"、代码/参数名里出现 "cancel"、英文句子含 "enough"，
  //   都会被判成"用户要求停止"而放行守卫 —— 人的意志被误读。
  // 修复：① 去掉过泛的短词（stop/enough/cancel/可以了/就这样 改由
  //         下方"整句匹配"承接）；② 匹配前先归一化（去空白与标点），
  //          且要求【整条消息就是停止指令】（长度受限），不做句中子串。
  const STOP_WORDS = ['停', '停止', '停下', '停下来', '不用了', '别做了',
                      '够了', '结束吧', '到此为止', '不用继续', '不用再',
                      '收工', '就这样', '可以了'];
  const STOP_WORDS_EN = ['stop', 'enough', 'cancel', 'quit', 'abort'];
  // 否定前缀：出现这些词说明用户在【拒绝】停止，不能判为停止指令
  const STOP_NEGATIONS = ['不要', '不用', '别', '先不', '不能', '没有', 'don\'t', 'dont', 'not', 'no '];

  /** 判断一条用户消息是否【本身就是】停止指令（而非恰好含某个词）。 */
  function isStopInstruction(raw) {
    const txt = String(raw || '').trim();
    if (!txt) return false;
    const lower = txt.toLowerCase();
    // 中文优先：去标点后"整句就是停止词"即放行（"不用了"本身是停止指令，
    //   不能因为含"不用"就被否定词规则误杀，故中文判定放在否定检查之前）
    const zh = txt.replace(/[\s，。！？、,.!?；;：:"'""''～~]/g, '');
    if (zh.length <= 6) {
      for (const w of STOP_WORDS) {
        if (zh === w || zh.endsWith(w)) return true;
      }
    }
    // ── 英文路径：仅当整条消息【不含中文】时才启用 ─────────────────────
    //   否则中文句子里夹带英文参数名（"参数里的 cancel 字段"、
    //   "这个 cancel 参数怎么用"）会被误判成用户要求停止。
    if (/[\u4e00-\u9fff]/.test(txt)) return false;
    for (const n of STOP_NEGATIONS) {
      if (lower.indexOf(n) >= 0) return false;
    }
    const words = lower.replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!words) return false;
    // 整句恰好是停止词（"stop" / "please stop"）
    if (words.length <= 24 && STOP_WORDS_EN.indexOf(words) >= 0) return true;
    // 或：短句（≤2 词）且含停止词（"stop please"）
    const toks = words.split(' ');
    if (toks.length <= 2 && toks.some((t) => STOP_WORDS_EN.indexOf(t) >= 0)) return true;
    return false;
  }
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
  // ── 【P0 修复·[TASK_DONE] 匹配过宽 + 文案自相矛盾】────────────────────
  // 原缺陷①：用 `_textEarly.indexOf('[TASK_DONE]') >= 0` 做【任意位置】子串匹配。
  //   文档（GUARD_TEXT 与注释 374-375）承诺的是"在正文【单独一行】写出"，
  //   实现却把任何提及都当成完成声明 —— 例如守卫自己的提示文本
  //   （GUARD_TEXT 第 3 条就含 '[TASK_DONE]' 字样）被回灌进上下文后即可触发放行。
  // 原缺陷②：GUARD_TEXT 写"本守卫【不看你说了什么】"，而本分支恰恰只扫文本，
  //   自相矛盾，使用者无法判断到底以什么为准。
  // 修复：① 只认【独占一行】的标记（前后仅允许空白），与文档契约一致；
  //       ② 文案改为如实说明：散文式声明无效，唯一的文本级开关是
  //          独占一行的 [TASK_DONE]（其余一律以状态文件为准）。
  // 注意：不移除该开关 —— 注释 523-532 说明子代理被禁止调用 room-end，
  //   若没有这个显式开关，子代理会陷入"既不能标完成、又不能自己清房间"的死锁。
  function hasStandaloneTaskDone(text) {
    // 【BUG-A1 修复】实现搬到 lib/turn-guard.js（hasStandaloneMarker），
    //   语义不变：只认【独占一行】的标记，散文里提一句无效。
    return hasStandaloneMarker(text, '[TASK_DONE]');
  }

  // ── 【BUG-A1 修复】把"客观等待证据"接到宿主侧可得的事实上 ─────────────
  // lib/turn-guard.js 里的 collectWaitingEvidence 只做纯判定；这里负责把它需要
  // 的四个事实【从宿主现有设施】取出来，不另造一套：
  //   · mode_state.json / workflow_state.json  → 房间活跃性 + pending 标记
  //     （与 detectTaskFinished 读的是同一份文件、同一个 toolsDir）
  //   · 未答提问卡                              → childAsks（/ask-child 的台账）
  //   · 未 settle 后台 job                      → pendingJobsOf（B1 已建好的那个）
  // 任何一项取不到都只是"少一条证据"，绝不影响其它证据，也绝不抛。
  function collectGuardWaitingEvidence(agent) {
    try {
      const toolsDir = findToolsDir();
      const modeState = toolsDir ? readJsonSafe(path.join(toolsDir, 'mode_state.json')) : null;
      const workflowState = toolsDir ? readJsonSafe(path.join(toolsDir, 'workflow_state.json')) : null;
      let unsettledJobs = 0;
      let jobsUnavailable = '';
      try {
        const pj = pendingJobsOf(agent && agent.id);
        if (pj && pj.available) unsettledJobs = pj.pending.length;
        else jobsUnavailable = (pj && pj.reason) || 'jobs 服务不可用';
      } catch (e) { jobsUnavailable = String((e && e.message) || e); }

      // ── 【2026-11-12 修复·BUG-2】补两条以前完全没接的宿主侧证据 ──────────
      // 现场（减速器任务 20261111_224045_56A62E32）：
      //   主对话 10+ 次写 [WAITING] 全被打回，即使当时明明在等 ——
      //     · 后台 job（wait-rooms）正在跑；
      //     · 宿主侧有活跃提问卡（/pending-child 查得到 pendingId）；
      //   打回的直接原因不是判据错，而是【取证拿不到】：
      //     a) unsettledJobs 依赖 `jobs.list()` 的 `owner` 字段，而 ownership
      //        在宿主里并非总是填的 → pendingJobsOf 返回 available:false →
      //        计数恒 0，"有 job 在跑"这条证据永远拿不到；
      //     b) 提问卡只在【总计子代理】阶段才有，ask_user.py 走的是
      //        childAsks 台账 + 宿主 /pending-child 两条路；countOpenQuestions
      //        只扫 childAsks，宿主侧登记的卡片根本看不见。
      //   于是模型唯一剩下的证据只有"房间有心跳"—— 而分析阶段压根没有
      //   active 建模房间（报告 BUG-2 原文正是这句）→ 四条证据实际只剩一条
      //   窄通道，形同"半实现"。
      // 修复：把上面两类事实各自补一条独立取证路径（任一命中即放行）。
      let hostJobs = 0;
      try {
        const sum = pendingJobsSummary();
        // pendingJobsSummary 口径：全局 running/stopping，不看 owner
        hostJobs = Number(sum && sum.total) || 0;
      } catch (e) {}
      let hostAsks = 0;
      try {
        hostAsks = Number(pendingChildAskCount()) || 0;
      } catch (e) {}

      const res = collectWaitingEvidence({
        toolsDir,
        modeState,
        workflowState,
        openQuestions: Math.max(countOpenQuestions(agent), hostAsks),
        unsettledJobs: Math.max(unsettledJobs, hostJobs),
      });
      if (jobsUnavailable && hostJobs === 0) {
        // 取证通道不可用 ≠ 没有在等：把原因留在日志里，便于事后判断打回是否合理
        try {
          console.warn('[dsh-engineering-ui] [WAITING] 后台 job 取证不可用：' + jobsUnavailable);
        } catch (e) {}
      }
      return res;
    } catch (e) {
      return { waiting: false, evidence: [], reason: String((e && e.message) || e) };
    }
  }

  /**
   * 【BUG-B1 修复】宿主侧"未 settle 后台 job"的只读汇总（给面板用）。
   *
   * 为什么要它：B1 的现场是"回合已结束、job 还在跑"，而面板只看 parts-board
   *   （Python 侧）时是看不见这件事的 —— 那时报告还没写、日志还是 0 字节。
   *   这里把宿主内存里【此刻还在 running/stopping】的 job 汇总成一张小表，
   *   让父代理/人去面板上一眼看到"哪个会话还没真收工"。
   *
   * 口径与 pendingJobsOf 完全一致（同一个 services、同一套归属判定），
   *   不另造一套 —— 两套口径必然打架，这正是这次要消灭的东西。
   * 任何异常都退化成 { available:false }，绝不让面板因此 500。
   */
  function pendingJobsSummary() {
    try {
      const jobs = (ctx && typeof ctx.get === 'function') ? ctx.get('jobs') : undefined;
      if (!jobs || typeof jobs.list !== 'function') {
        return { available: false, reason: 'jobs 服务不可用', sessions: [], total: 0 };
      }
      let rows = [];
      try { rows = jobs.list() || []; } catch (e) {
        return { available: false, reason: 'jobs.list 失败：' + String((e && e.message) || e),
                 sessions: [], total: 0 };
      }
      if (!Array.isArray(rows)) rows = [];
      // ⚠️ 这里【故意】不过滤 owner：面板要的是全局视图（谁在忙）。
      //   与 pendingJobsOf（按会话判定）不同，但数据源同一份，不会矛盾：
      //   pendingJobsOf 只是在这里再叠一层 owner===id。
      const byOwner = new Map();
      for (const j of rows) {
        if (!j) continue;
        const st = String(j.status || j.state || '').toLowerCase();
        if (st !== 'running' && st !== 'stopping') continue;
        const owner = String(j.owner || '');
        if (!owner) continue;            // 无主任务不属于任何会话，不进面板
        const arr = byOwner.get(owner) || [];
        arr.push({ id: j.id || j.jobId || null,
                   label: j.label || j.name || null,
                   kind: j.kind || null,
                   status: st });
        byOwner.set(owner, arr);
      }
      const sessions = Array.from(byOwner.entries()).map(([id, list]) => ({
        sessionId: id,
        count: list.length,
        jobs: list,
      }));
      return {
        available: true,
        total: sessions.reduce((n, s) => n + s.count, 0),
        sessions,
        note: '这些会话的后台 job 还没 settle —— 不要据此判它们"已完成"（B1）',
      };
    } catch (e) {
      return { available: false, reason: String((e && e.message) || e), sessions: [], total: 0 };
    }
  }

  /**
   * 该会话（或其父会话）名下仍在 pending 的提问卡数量。
   * 人没作答 → 模型除了等没有别的选择，这是最干净的一种"客观等待证据"。
   * 直接读 childAsks（/ask-child 的待答台账，声明在本文件下方，运行时已就绪）。
   */
  function countOpenQuestions(agent) {
    try {
      const id = String((agent && (agent.id || agent.sessionId)) || '');
      if (!id) return 0;
      const pid = parentSessionId(agent);
      const keys = [id];
      if (pid) keys.push(String(pid));
      let n = 0;
      for (const e of childAsks.values()) {
        if (!e || e.settled) continue;
        const owner = String(e.childSessionId || '');
        if (keys.indexOf(owner) >= 0) n += 1;
      }
      return n;
    } catch (e) {
      return 0;
    }
  }

  /**
   * 【2026-11-12 修复·BUG-2】宿主侧所有未结提问卡的总数（含 pendingAsks）。
   *
   * 与 countOpenQuestions 的区别（这正是 BUG-2 打回 10+ 次的直接原因）：
   *   · countOpenQuestions 按 childSessionId / parentSessionId 过滤，
   *     只有"这张卡属于当前 agent 这一支"才算证据；
   *   · 而减速器任务里提问的是【总计子代理】，主对话只是它的父级，且
   *     ask_user.py 的台账 + 宿主 pendingAsks 两条路都可能登记卡片。
   *     一旦 owner 字段没落上（或子代理用的是自己的 sessionId），
   *     父级就数不到 → "有未答提问卡"这条证据对它永远是 0。
   *
   * 判据放宽到"本进程里还有没结的卡"：守卫要回答的问题是
   * 「这个会话此刻是不是在等人」，而不是「这张卡是不是它的直属子代理的」。
   * 只要整条链路里有一张卡没结，模型除了等确实没有别的选择。
   */
  function pendingChildAskCount() {
    try {
      let n = 0;
      for (const e of childAsks.values()) if (e && !e.settled) n += 1;
      // 主对话自己的提问卡（pendingAsks）同样属于"在等人"
      try {
        for (const e of pendingAsks.values()) if (e && !e.settled) n += 1;
      } catch (e) {}
      return n;
    } catch (e) {
      return 0;
    }
  }

  function detectTaskFinished(agent) {
    try {
      const _textEarly = collectRecentAssistantText(agent, 60);
      if (hasStandaloneTaskDone(_textEarly)) {
        return { done: true, reason: '检测到显式完成标记 [TASK_DONE]（独占一行，最高优先级）' };
      }
      // ── 【BUG-A1 修复】第 4 种合法终态：[WAITING]（等）─────────────────
      // 现场：模型 3 次想结束回合被守卫打回（:98/:179/:508），此后 5 h 40 m 不再
      //   尝试结束；同一回合用 sleep(30) 轮询 477 次 = 4 h 02 m（占墙钟 66%），
      //   几何产出 0 次。根因：守卫只认三种代码级终态，"我在等"没有合法出口。
      // 判据（代码可判，不信模型口头声明）：
      //   ① 正文【独占一行】写出 [WAITING]（必要条件，散文里提一句无效）；
      //   ② turn-guard.collectWaitingEvidence() 拿到至少一条客观证据：
      //      房间 active 且近 5 分钟有心跳/报告变化 / 有未答提问卡 /
      //      workflow_state 挂 pending / 本会话仍有未 settle 的后台 job。
      //   只满足 ① 不满足 ② → 视为"用 [WAITING] 逃避推进"，照旧 steer。
      //   ⚠️ 这条分支【绝不】返回 done 之外的宽放行：证据缺失时原样落到下面的
      //      代码级终态判定，守卫的严格性一点没减。
      if (hasStandaloneMarker(_textEarly, '[WAITING]')) {
        const _wait = collectGuardWaitingEvidence(agent);
        if (_wait.waiting) {
          const _why = _wait.evidence.join('；');
          try {
            console.warn('[dsh-engineering-ui] [WAITING] 放行：' + _why);
          } catch (e) {}
          return { done: true, reason: '[WAITING] 放行：' + _why };
        }
        try {
          console.warn('[dsh-engineering-ui] [WAITING] 打回：正文有标记但拿不出客观等待证据（'
            + (_wait.reason || '证据为空') + '）');
        } catch (e) {}
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
        // ── 【未记录 BUG 修复·跨任务残留房间卡死守卫】────────────────────
        // 实测（2026-10-29 盘查真实安装副本）：
        //   mode_state.json 里躺着 3 个 active 房间（底盘与车架总成 / 驱动轮组 /
        //   总理子代理），task_epoch 还是 10 月中旬的上一轮任务，而且
        //   mode_state 里【没有 task_id 字段】，无法判断它们属不属于本轮。
        //   后果：detectTaskFinished() 永远走"仍有 active 房间 → done=false"
        //   分支，守卫对【所有新会话】每轮 steer、永不放行 —— 用户看到的就是
        //   "模型一直在重复同一句话、停不下来"。
        // 修复：判断 active 时只看【属于本轮 workflow task_id】的房间；
        //   · 房间记录没有 task_id（升级前的老数据）→ 按【属本轮】处理
        //     （宁可不放行，也不能把正在跑的房间当残留误放）；
        //   · 只有明确记着别的 task_id 才排除，并在日志里留痕。
        const _wfTidEarly = (wf && wf.task_id) ? String(wf.task_id) : '';
        const _namesAllEarly = Object.keys(_roomsEarly);
        let _staleRoomCount = 0;
        const _namesEarly = _namesAllEarly.filter((n) => {
          const _rec = _roomsEarly[n] || {};
          const _rt = _rec.task_id;
          if (!_rt || !_wfTidEarly) return true;      // 无从判断 → 保守按本轮
          if (String(_rt) === _wfTidEarly) return true;
          _staleRoomCount += 1;
          return false;
        });
        if (_staleRoomCount > 0) {
          try {
            console.warn('[dsh-engineering-ui] 守卫忽略上一轮任务的 '
                         + _staleRoomCount + ' 个残留房间（task 不匹配）');
          } catch (e) {}
        }
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
          // 【未记录 BUG 修复】这里同样只认【本轮任务】的房间（理由见上）。
          const names = Object.keys(rooms).filter((n) => {
            const _rt = (rooms[n] || {}).task_id;
            if (!_rt || !_wfTidEarly) return true;
            return String(_rt) === _wfTidEarly;
          });
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
        // 【P0 修复】只认"整条消息就是停止指令"，不再做句中子串匹配
        if (isStopInstruction(txt)) return true;
      }
    } catch (e) {}
    return false;
  }

  // ── 【模型自动接替】运行时状态（每 agent 一份）────────────────────────
  // 结构：agentId -> { key: 当前钉住的候选 key, tried: Set(本 turn 已试),
  //                    turn: 该 tried 属于哪个 turn }
  // 放在 apply() 作用域内，与 guardState 同级：随插件卸载自然释放。
  const modelChainState = new Map();
  /** 配置缓存（读盘有开销，按 mtime 失效）。 */
  let modelChainCache = null;
  let modelChainCacheAt = 0;

  /** 读模型链接配置（找不到/读坏都返回"未启用"）。 */
  function readModelChainConfig() {
    const now = Date.now();
    if (modelChainCache && (now - modelChainCacheAt) < 5000) return modelChainCache;
    let cfg = { ok: true, enabled: false, entries: [] };
    try {
      const dir = findToolsDir();
      if (dir) {
        const p = path.join(dir, MODEL_CHAIN_FILE);
        const j = readJsonSafe(p);
        if (j) cfg = normalizeModelChain(j);
      }
    } catch (e) {
      cfg = { ok: false, enabled: false, entries: [], error: String((e && e.message) || e) };
    }
    modelChainCache = cfg;
    modelChainCacheAt = now;
    return cfg;
  }

  /**
   * 该 agent 是否适用模型接替（工程模式 + 配置启用 + 有候选）。
   *
   * ── 作用域（用户要求：最好只运用在工程模式）──────────────────────────
   * 三条证据任一成立即认为是工程模式会话：
   *   ① isGuardSession(agent)        —— 与收尾守卫完全同一口径（最权威）
   *   ② 会话 header 的 agentPreset === 'engineering'
   *   ③ 子代理：父会话是工程模式（fork 子代理继承父级 preset，但它自己的
   *      header 未必带 agentPreset，所以要顺着 parentSession 查一次）
   * 非工程模式会话【完全不介入】—— 不换模型、不改请求配置。
   */
  function modelChainApplies(agent) {
    try {
      const cfg = readModelChainConfig();
      // ── 【2026-10-30 改版】空链 = 不兜底 ────────────────────────────────
      // 用户要求：「没填模型就是使用默认的，用户自己选择的模型即可」。
      // 因此不再依赖 enabled 开关（该开关已从 UI 移除）：
      //   · 链里【没有】模型  → 完全不管，用用户自己选的默认模型；
      //   · 链里【有】模型    → 按顺序兜底。
      if (!cfg.entries.length) return false;
      // ① 与守卫同口径
      if (isGuardSession(agent)) return true;
      // ② header 直接标明
      try {
        const h = agent && agent.session && agent.session.header;
        if (h && String(h.agentPreset || '') === GUARD_PRESET) return true;
        if (agent && String(agent.agentPreset || '') === GUARD_PRESET) return true;
      } catch (e) {}
      // ③ 子代理继承父级 preset 判断
      //
      // ── 【R15 / 子代理不换模型修复】不能只看父级"已经跑过一轮"的缓存 ──
      // 原实现的链路是：parentChainStateOf(agent).isEng —— 而 isEng 只在
      //   modelChainStateOf() 里赋值，也就是【父级必须先发过一次 agent/request】。
      // 实测缺陷（用户反馈"子代理额度用完不换模型"）：
      //   9 个建模小屋是【同批 fork】出来的，父对话这一刻可能还没发过请求
      //   （或父级状态在压缩/重启后被清掉）→ parentChainStateOf 返回 null
      //   → modelChainApplies=false → 子代理的 QUOTA 错误走 next() 直接透传
      //   → 该子代理当场死掉，而主对话却正常换模型。
      // 正确语义：**有父会话 = 属于父会话的作用域**。
      //   父级已知 → 用父级的 isEng；父级未知 → 先查预设缓存，
      //   再不行就【保守按工程模式处理】（子代理本来就只可能由工程模式 fork 出来，
      //   且链路副作用仅是"换一个模型"，远小于"子代理直接死掉"的代价）。
      try {
        const pid = parentSessionId(agent);
        if (pid) {
          const pst = parentChainStateOf(agent);
          if (pst && pst.isEng === true) return true;
          if (pst && pst.isEng === false) return false;   // 父级明确不是工程 → 不管
          const cached = presetCache.get(String(pid));
          if (cached === GUARD_PRESET) return true;
          if (cached && cached !== GUARD_PRESET && isKnownPreset(cached)) {
            return false;                                  // 父级明确是别的预设
          }
          if (cached === undefined) readPreset(String(pid)).catch(() => {});
          // 父级预设未知（还没跑过请求 / 刚重启 / 读不到日志）——
          //   子代理场景下按工程模式处理，宁可多兜一次也不让小屋里断。
          return true;
        }
      } catch (e) {}
      return false;
    } catch (e) {
      return false;
    }
  }

  /** 取/建某 agent 的接替状态，并按 turn 重置已试集合。 */
  function modelChainStateOf(agent, turn) {
    let st = modelChainState.get(agent.id);
    if (!st) {
      st = {
        key: null, tried: new Set(), turn: null, isEng: false,
        switched: false, prevKey: null,
        /** 本轮内每个模型失败了几次（key=provider\0model）→ 用于"反复失败就换" */
        failCounts: new Map(),
        /** 自动续跑：本轮用户输入已续了几次 / 待触发的定时器（跨 turn 保留，见 maybeAutoResume） */
        resumeCount: 0, resumeTimer: null,
        /**
         * 待生效的候选 key（provider\0model）。
         *
         * ── 为什么需要它（子代理"死了就是死了"的第二个根因）──────────────
         * agent/model/selection 事件是【本步 preStep 之后】才追加的；
         * 而子代理的 failureAt 会在 agent/error 时置位，让激活在
         * "inbox 为空 + 没有 owned children" 时直接判定 ready →
         * 会话被 dispose，select() 写进去的 pending 选择【再也没机会被读到】。
         * 主对话因为下一轮一定还会跑，所以它看起来正常 —— 这正是
         * "主对话能换、子代理不换"的差异来源。
         * 现在失败时把目标记在这里，并在下一次 pre-step 之前【再写一次】，
         * 保证只要该 agent 还有任何一步要跑，就一定落到新路由上。
         */
        pendingKey: null,
      };
      modelChainState.set(agent.id, st);
    }
    if (st.turn !== turn) {
      st.turn = turn;
      st.tried = new Set();
      // 新的一轮：接替状态复位（否则上一轮切换会一直粘住，
      // 表现为"用户重新选了模型却还被按在旧候选上"）
      st.switched = false;
      st.prevKey = null;
      st.failCounts = new Map();   // 新一轮：失败计数清零，重新给每个模型机会
    }
    // 记录"这个 agent 是否工程模式"，供子代理顺着父级判断作用域。
    // ⚠️ 必须是【非粘性】判定：每轮重算、允许从 true 变回 false。
    //    旧实现只在为 true 时赋值，一旦某轮误判成工程模式就永久生效，
    //    导致普通会话也被接替逻辑接管（用户反馈"不是工程模式也被接管"）。
    try {
      let _eng = false;
      if (isGuardSession(agent)) _eng = true;
      else {
        const h = agent && agent.session && agent.session.header;
        if (h && String(h.agentPreset || '') === GUARD_PRESET) _eng = true;
      }
      st.isEng = _eng;
    } catch (e) { st.isEng = false; }
    return st;
  }

  /**
   * 找出该 agent 的【父会话】的接替状态。
   *
   * ── 为什么子代理必须跟随父级（用户要求：子代理继承模型）─────────────
   * DSH 的 fork 子代理本来就继承父级模型：
   *   ForkInProcessProvider 的 agentOptions 是 merged over the parent route，
   *   且工具描述明确写 "Omit provider, model ... to inherit compatible
   *   missing values from the parent Agent"。
   * 所以如果子代理【各自独立】从链首重新试，会出现：
   *   主对话已经切到候选 2，子代理却又从候选 1（已耗尽的）开始撞墙，
   *   白白失败一轮才切走 —— 既浪费额度，也违背"继承"语义。
   * 正确做法：子代理直接沿用父级当前钉住的候选；只有当父级也没定
   * （比如父级不是工程模式会话）时才自己从头挑。
   */
  function parentChainStateOf(agent) {
    try {
      const pid = parentSessionId(agent);
      if (!pid) return null;
      const st = modelChainState.get(String(pid));
      return st || null;
    } catch (e) {
      return null;
    }
  }

  /** 取该 agent 的父会话 id（fork 谱系的唯一可靠来源）。 */
  function parentSessionId(agent) {
    try {
      return agent && (agent.parentSession || agent.parentId
        || (agent.session && agent.session.header && agent.session.header.parentSession))
        || null;
    } catch (e) {
      return null;
    }
  }

  // ── ⓪ 0.2.2 通道：agent.model.select() + provider 级隔离 ──────────────
  // 见文件顶部【2026-11-05 · DSH 0.2.2 适配】。下面这组函数只在宿主提供
  // agent.model.select 时被用到；老宿主（0.2.1 及以前）走下面的 agent/request。
  function supportsModelSelect(agent) {
    try {
      const m = agent && agent.model;
      return !!m && typeof m.select === 'function' && typeof m.current === 'function';
    } catch (e) {
      return false;
    }
  }

  /**
   * 把该 agent 的路由改成链里的某个候选。
   * ⚠️ select() 语义：完整赋值（省略 reasoningEffort = 用适配器默认值，
   *    不继承旧强度）；对"下一个尚未开始准备的步骤"生效，本步改不动。
   * @returns 是否成功写入（失败一律静默，绝不影响主流程）
   */
  function selectModelRoute(agent, cand) {
    try {
      if (!agent || !cand) return false;
      const m = agent.model;
      if (!m || typeof m.select !== 'function') return false;
      const sel = { provider: cand.provider, model: cand.model };
      if (cand.reasoningEffort !== undefined) sel.reasoningEffort = cand.reasoningEffort;
      m.select(sel);
      return true;
    } catch (e) {
      return false;
    }
  }

  /**
   * provider 级隔离（额度/限流是按账号算的，同一家的候选大概率一起挂）。
   *
   * ── 和 st.tried 的区别 ───────────────────────────────────────────────
   *   st.tried  按【本轮】记，turn 变了就清空（旧行为保持不变）；
   *   隔离表按【provider】记，跨轮生效，TTL 过期自动失效。
   * 为什么需要跨轮：0.2.2 里 select() 只对下一个步骤生效，本轮必然以失败
   * 结束；没有跨轮记忆的话，用户重发一句又会先撞同一个限流 provider。
   */
  const PROVIDER_QUARANTINE_MS = 10 * 60 * 1000;
  const providerQuarantine = new Map();     // provider -> 到期时间戳

  function quarantineProvider(provider) {
    try {
      const p = String(provider || '');
      if (p) providerQuarantine.set(p, Date.now() + PROVIDER_QUARANTINE_MS);
    } catch (e) {}
  }

  function isProviderQuarantined(provider) {
    try {
      const p = String(provider || '');
      if (!p) return false;
      const until = providerQuarantine.get(p);
      if (until === undefined) return false;
      if (Date.now() >= until) { providerQuarantine.delete(p); return false; }
      return true;
    } catch (e) {
      return false;
    }
  }

  /** 用户手动改选 / 成功换到新家后，解除该 provider 的隔离。 */
  function releaseQuarantine(provider) {
    try {
      const p = String(provider || '');
      if (p) providerQuarantine.delete(p);
    } catch (e) {}
  }

  /** 在链里挑第一个"本轮没试过、且 provider 没被隔离"的候选。 */
  function firstHealthyCandidate(entries, tried, excludeKey) {
    for (const e of (entries || [])) {
      const key = modelKey(e.provider, e.model);
      if (excludeKey && key === excludeKey) continue;
      if (tried && tried.has(key)) continue;
      if (isProviderQuarantined(e.provider)) continue;
      return e;
    }
    return null;
  }

  /**
   * 【0.2.2 自动续跑】换模型之后，自动把这一轮接上，不需要用户手动重发。
   *
   * ── 为什么非要多发一条消息 ─────────────────────────────────────────────
   * 0.2.2 的 turn 一旦因模型错误结束，就只能靠 agent.followup() 唤醒驱动器
   * 才会再开一轮；宿主没有"静默续跑"的接口（inject() 不唤醒）。
   * 这条消息的 source 是 plugin:dsh-engineering-ui —— 和收尾守卫那几条同源，
   * 不是用户发的，前端会当成插件消息。
   *
   * ── 三重保险（别删）────────────────────────────────────────────────────
   *   · autoResume=false 可整体关闭（model_chain.json）；
   *   · 同一轮用户输入最多续 autoResumeMax 次（默认 3），防止整条链都挂时无限重试；
   *   · 用户真的手动发新消息时立刻清零（见 agent/pre-step 监听器）。
   *
   * ── 为什么必须延迟 ─────────────────────────────────────────────────────
   * 调用的这一刻驱动器还在 running（本 step 还没收尾），此时 followup 只会把
   * 消息塞进 inbox 而不唤醒（wakeDriver 在 running 且未 abort 时会丢弃唤醒）。
   * 等 turn/end 落盘后再发，才是"空闲时投递 → 立刻开新轮"。
   */
  function maybeAutoResume(agent, st, payload, cand) {
    try {
      const cfg = readModelChainConfig();
      if (cfg.autoResume === false) return false;
      if (!agent || typeof agent.followup !== 'function') return false;
      const max = Number.isFinite(cfg.autoResumeMax) ? cfg.autoResumeMax : 3;
      if ((st.resumeCount || 0) >= max) {
        try {
          console.warn('[dsh-engineering-ui] 自动续跑已达上限（' + max + ' 次），交还用户');
        } catch (e) {}
        return false;
      }
      if (st.resumeTimer) {
        try { clearTimeout(st.resumeTimer); } catch (e) {}
        st.resumeTimer = null;
      }
      const delay = Number.isFinite(cfg.autoResumeDelayMs) ? cfg.autoResumeDelayMs : 1200;
      const code = String((payload && payload.failure && payload.failure.code) || '错误');
      const text = '（模型自动接替：上一个模型返回 ' + code
        + '，已切到 ' + cand.provider + '/' + cand.model
        + '。请从上次中断处继续；已经完成的步骤、已经成功的工具调用不要重做。）';
      st.resumeCount = (st.resumeCount || 0) + 1;
      st.resumeTimer = setTimeout(() => {
        st.resumeTimer = null;
        try {
          agent.followup(createUserMessage({
            content: [{ type: 'text', text }],
            source: { kind: 'plugin:dsh-engineering-ui' },
          }));
        } catch (e) {
          try {
            console.warn('[dsh-engineering-ui] 自动续跑失败：'
              + String((e && e.message) || e));
          } catch (_e) {}
        }
      }, delay);
      // 不让这个定时器把进程吊住（宿主退出时不必等它）
      try { if (typeof st.resumeTimer.unref === 'function') st.resumeTimer.unref(); } catch (e) {}
      try {
        console.warn('[dsh-engineering-ui] 自动续跑已排程（' + delay + 'ms，第 '
          + st.resumeCount + '/' + max + ' 次）');
      } catch (e) {}
      return true;
    } catch (e) {
      return false;
    }
  }

  // ── ⓪' 用户手动改选 → 交还控制权（0.2.2 下 request 监听器不再跑，挪到这里）──
  // 热路径注意：先看内存态，没接替过的 agent 直接放行（不读盘、不算预设）。
  disposers.push(ctx.on('agent/pre-step', async (payload, next) => {
    try {
      const agent = payload && payload.agent;
      const sel = payload && payload.modelSelection;
      const st = agent && agent.id ? modelChainState.get(agent.id) : null;
      if (st) {
        // 用户真的发了一条新消息（非插件来源）→ 自动续跑计数清零，
        // 下一次失败又能续满 autoResumeMax 次。
        try {
          for (const m of (Array.isArray(payload.messages) ? payload.messages : [])) {
            const kind = m && m.source && m.source.kind;
            if (typeof kind === 'string' && kind.indexOf('plugin:dsh-engineering-ui') !== 0) {
              st.resumeCount = 0;
              break;
            }
          }
        } catch (e) {}
        if (st.resumeTimer) {
          // 用户主动发消息了 → 撤销还没触发的自动续跑，别插在人家的话后面
          try { clearTimeout(st.resumeTimer); } catch (e) {}
          st.resumeTimer = null;
        }
      }
      if (st && st.key && sel && sel.provider) {
        const key = modelKey(sel.provider, sel.model);
        // 既不是我们钉住的候选、也不是切换前用户在用的那个 → 用户真的改了选
        if (key !== st.key && key !== st.prevKey) {
          st.switched = false;
          st.key = null;
          st.prevKey = null;
          st.tried = new Set();
          releaseQuarantine(sel.provider);
          try {
            console.warn('[dsh-engineering-ui] 检测到手动选型变更（'
              + sel.provider + '/' + sel.model + '），已放弃接替、交还用户选择');
          } catch (e) {}
        }
      }
      // ── 【子代理不换模型 · 兜底重放】─────────────────────────────────
      // 见 modelChainState.pendingKey 的注释：select() 写入的 model/selection
      // 是本步 preStep 之后才落盘的，下一次 preStep 读到的 selection 仍可能是
      // 旧的。这里在【进入装配前】再补一次 select —— 它只追加一条事件，
      // 幂等（同一条目不会重复追加），但能保证下一步一定用新路由。
      try {
        if (st && st.pendingKey && agent && agent.model && typeof agent.model.select === 'function') {
          const cfg = readModelChainConfig();
          const want = cfg.entries.find((e) => modelKey(e.provider, e.model) === st.pendingKey);
          const curKey = sel && sel.provider ? modelKey(sel.provider, sel.model) : '';
          if (want && curKey !== st.pendingKey) {
            selectModelRoute(agent, want);
            try {
              console.warn('[dsh-engineering-ui] 子代理接替兜底：pre-step 重新写回 '
                + want.provider + '/' + want.model);
            } catch (e) {}
          } else if (want) {
            st.pendingKey = null;      // 已经生效 → 清掉待办
          }
        }
      } catch (e) {}
    } catch (e) {}
    return next();
  }));

  // ── ① 老宿主专用（0.2.1 及以前）：本轮已接替过才覆盖请求配置 ──────────
  //
  // ⚠️ 0.2.2 起本监听器【直接放行】：那时改 provider/model 会抛
  //    "agent/request proposed ... ; use agent.model.select() before step preparation"。
  //    判断方式就是 supportsModelSelect(agent)，见文件顶部适配说明。
  //
  // ══ 【2026-10-30 重大修正】绝不能再无条件覆盖用户的选择 ══════════════
  // 原实现（v2）每轮都把请求强制改成"链首候选"，导致两个严重问题：
  //   ① 用户在设置页选了 workbuddy/deepseek-v4.1-flash，
  //      实际请求却被改成 chain[0] = buddy/... —— 用户看到的是
  //      "我选 DeepSeek，回的却是 Hy3"。这等于插件劫持了模型选择权。
  //   ② 本插件监听器挂在【root 上下文】，而官方的 installModelSelection
  //      挂在【agent 作用域】。cordis 的 waterfall 是 outermost-first，
  //      所以我们的覆盖永远赢过官方的选择 —— 手动选择彻底失效。
  //
  // 正确语义（把"接替"还原成"兜底"）：
  //   · 默认【完全不介入】—— 用户/宿主选什么就用什么；
  //   · 只有当 agent/request-error 判定当前模型失效、并把 st.key 指向
  //     链里【下一个候选】之后，本监听器才在下一轮把请求改成那个候选；
  //   · 用户一旦重新手动选模型（请求头里出现与 st.key 不同的选择），
  //     立即清除接替状态，把控制权交还给用户。
  disposers.push(ctx.on('agent/request', async (payload, next) => {
    let resolved;
    try { resolved = await next(); } catch (e) { throw e; }
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return resolved;
      // 【0.2.2】换模型只能走 agent.model.select()；这里一个字都不能改。
      if (supportsModelSelect(agent)) return resolved;
      if (!modelChainApplies(agent)) return resolved;

      const st = modelChainStateOf(agent, payload.turn);
      // 本轮没有发生接替 → 什么都不做（这是最常见路径）
      if (!st.switched || !st.key) return resolved;

      // ── 【最终保险】链里没有的模型 → 一律不碰 ──────────────────────────
      // 用户手动选的模型常常不在"自动接替链"里（比如临时换了个别的家）。
      // 那种情况下接替毫无意义，直接放行，绝不改用户的选型。
      const cfg = readModelChainConfig();
      const cur = cfg.entries.find((e) => modelKey(e.provider, e.model) === st.key) || null;
      if (!cur) return resolved;

      // ── 【2026-10-30 修复·误判用户改选】────────────────────────────────
      // 场景（用户实际问到的）：设置页选 A，链里第一个填的是 B。
      //   A 失败 → 我们切到 B → 下一轮 agent/request 的 resolved 仍来自
      //   【上一个成功请求头】，也就是还是 A（A 那次失败了，头没更新）。
      //   旧代码看到 "resolved=A ≠ st.key=B" 就判定"用户改选了"，
      //   于是放弃接替、退回 A → A 又失败 → 死循环，接替形同虚设。
      //
      // 修复：记住【切换前用户实际在用的模型】(st.prevKey)。
      //   只有 resolved 既不是当前候选、也【不是】切换前那个模型时，
      //   才真的是用户又改了选，此时才交还控制权。
      const wantP = String((resolved && resolved.provider) || '');
      const wantM = String((resolved && resolved.model) || '');
      const wantKey = (wantP && wantM) ? modelKey(wantP, wantM) : '';
      if (wantKey && wantKey !== st.key) {
        // resolved 是"上一个成功请求头"的模型：
        //   · 等于 st.prevKey（切换前的模型）→ 正常，这是我们自己切走的，
        //     继续用候选，不算用户改选；
        //   · 其它值 → 用户确实又手动换了，交还控制权。
        if (st.prevKey && wantKey === st.prevKey) {
          // 正常接替路径，继续往下走
        } else {
          st.switched = false;
          st.key = null;
          st.prevKey = null;
          try {
            console.warn('[dsh-engineering-ui] 检测到手动选型变更（'
              + wantP + '/' + wantM + '），已放弃接替、交还用户选择');
          } catch (e) {}
          return resolved;
        }
      }

      try {
        console.warn('[dsh-engineering-ui] 接替生效：本轮改用 '
          + cur.provider + '/' + cur.model);
      } catch (e) {}
      // 只改 provider/model，并清掉继承来的 reasoningEffort
      const { reasoningEffort: _inherited, ...rest } = resolved || {};
      return {
        ...rest,
        provider: cur.provider,
        model: cur.model,
        ...cur.reasoningEffort === undefined ? {} : { reasoningEffort: cur.reasoningEffort },
      };
    } catch (e) {
      return resolved;      // 接替逻辑任何异常都不得影响主流程
    }
  }));

  // ── ② 请求失败后：额度/限流类错误 → 换下一个候选并让本轮重试 ────────
  disposers.push(ctx.on('agent/request-error', async (payload, next) => {
    try {
      const agent = payload && payload.agent;
      const failure = payload && payload.failure;
      const code = String((failure && failure.code) || '');
      if (!agent || !agent.id) return next();
      if (!modelChainApplies(agent)) return next();
      const cfg = readModelChainConfig();
      const st = modelChainStateOf(agent, payload.turn);

      // ── 【2026-10-30 强化】判定"这个模型是否还能正常工作" ────────────────
      // 用户要求（原话）：
      //   「不只是失败 5 次就换，就模型输不出文字就换，
      //     模型无法正常工作就换的那种」
      // 所以判断标准不是"错误码在不在白名单里"，而是：
      //   ① 明确的致命类型（额度耗尽/限流/上下文爆掉/空响应/凭据失效）
      //       → 立刻换，不用等次数；
      //   ② 其它任何错误 → 同一模型在同一轮内累计失败到 N 次 → 换；
      //   ③ 不属于错误、但模型确实没产出（空文本）→ 也算不正常。
      // 这样"卡在那儿一直重试同一个模型"就不可能发生了。
      const _failModelKey = modelKey(payload.provider, (failure && failure.model) || '');
      st.failCounts = st.failCounts || new Map();
      const _cnt = (st.failCounts.get(_failModelKey) || 0) + 1;
      st.failCounts.set(_failModelKey, _cnt);

      // ① 致命类型：立刻换
      // ── 【A4】不再只认字面量白名单：先做错误码归一 ─────────────────────
      //   :164 报 RATE_LIMIT、:166 同因改报 QUOTA_EXCEEDED（当时不在白名单）
      //   → 5 次重试预算只用 1 次就被判死。
      const _cls = classifyModelError(code, (failure && failure.message) || '');
      const _fatal = MODEL_CHAIN_SWITCH_CODES.has(code) || _cls.switchNow;
      // ② 反复失败：达到阈值也换（覆盖各种没被列入白名单的错）
      const _tooMany = _cnt >= MODEL_CHAIN_MAX_SAME_FAILS;
      // ③ 模型明显不正常：空响应 / 编码类错误（输不出内容）
      const _broken = /^(EMPTY_RESPONSE|INVALID_RESPONSE|DECODE|MALFORMED|UNSUPPORTED)/i.test(code)
        || /empty response|no content|invalid response|decode/i.test(String((failure && failure.message) || ''));

      // 达到任一条件才换；否则交回默认重试链路（让宿主自己重试本模型）
      if (!_fatal && !_tooMany && !_broken) return next();
      // 把"刚失败的模型"记为已试，避免又切回它
      st.tried.add(modelKey(payload.provider, (failure && failure.model) || ''));
      st.tried.add(modelKey(payload.provider, ''));
      // 同 provider 的候选全部标掉 —— 额度是按账号算的，同家大概率一起挂
      for (const e of cfg.entries) {
        if (e.provider === payload.provider) st.tried.add(modelKey(e.provider, e.model));
      }
      // ── 【2026-10-30 修正】从【当前实际在用的模型】往后找候选 ──────────
      // 原实现用 nextModelCandidate(entries, tried) 从头扫，于是：
      //   · 用户手动选了链里第 5 个，失败后又回到链首第 1 个；
      //   · 甚至把用户自己选的模型也跳过了。
      // 现在：先定位当前模型在链中的下标，之后只往后找（找不到再回到链首
      //       找没过期的，保持"兜底"语义但不打乱用户偏好）。
      const _curKey = modelKey(payload.provider, (failure && failure.model) || '');
      // ── 【2026-10-30 最终定案】严格【从上到下】按列表顺序替换 ─────────────
      // 用户原话：「不应该是按照从上到下开始替换吗？」
      //
      // 这是对的，也是列表语义（"按顺序优先"）本来的意思。此前实现是
      // "从当前模型的下标往后找"，导致：
      //   · 用户用 A，列表 [B,C,D]，A 失败 → 从链首 B 开始（碰巧对）；
      //   · 但若当前是 C（已在链中），C 失败 → 只往 D 找，跳过了 B；
      //   · 若当前是列表最后一个，还会回绕到链首，顺序完全乱掉。
      // 现在统一为：**永远从第 1 行开始，取第一个本轮还没试过的**。
      //   · 与"当前用的是哪个模型"完全无关；
      //   · tried 集合保证同一轮不重复试；
      //   · 已试过的（含刚失败的）自动跳过 → 不会死循环；
      //   · 全部试完 → 明确返回"耗尽"，交还宿主。
      // ── 【2026-11-05 · 0.2.2】优先挑"没被隔离的家" ─────────────────────
      // 同批 fork 出来的子代理会读到同一份隔离表：一个撞了额度，后面的
      // 直接跳过这个 provider，不必每个都先撞一堵墙再切。
      let nxt = firstHealthyCandidate(cfg.entries, st.tried, null);
      if (!nxt) {
        // 整条链都被隔离 / 都试过了 → 退一步只按"本轮试没试过"挑，
        // 宁可去试一个被隔离的家，也不空转。
        nxt = nextModelCandidate(cfg.entries, st.tried);
      }
      if (!nxt) {
        // 链已试完 → 交回默认（明确失败，绝不空转）
        try {
          console.warn('[dsh-engineering-ui] 链内候选已全部失败（'
            + cfg.entries.length + ' 个），交还宿主处理');
        } catch (e) {}
        return next();
      }
      // 记下【切换前用户实际在用的模型】——老宿主靠它区分
      // "这是我们自己切走的" 还是 "用户又手动改了选型"；0.2.2 下由
      // agent/pre-step 监听器做同样的事。
      st.prevKey = _curKey;
      st.key = modelKey(nxt.provider, nxt.model);
      st.switched = true;             // ← 老宿主：只有这个标记置位，下一轮才会覆盖请求
      // 额度/限流是按账号算的：把这个 provider 整体隔离一会儿
      quarantineProvider(payload.provider);
      // 记录"待生效"的候选：即使本步之后会话被判定 ready 并 dispose，
      // 只要它还有任何一次 pre-step，就会在装配前被重新写回（见 pendingKey 注释）。
      st.pendingKey = modelKey(nxt.provider, nxt.model);
      if (supportsModelSelect(agent)) {
        // ── 0.2.2 通道 ────────────────────────────────────────────────
        const ok = selectModelRoute(agent, nxt);
        try {
          console.warn('[dsh-engineering-ui] 模型接替（select）：' + payload.provider
            + ' 错误 ' + code + ' → ' + nxt.provider + '/' + nxt.model
            + (ok ? '，下一次尝试生效' : '（select 写入失败，交还宿主）'));
        } catch (e) {}
        // 失败后自己续一轮，不用用户手动重发（可用 autoResume:false 关掉）
        maybeAutoResume(agent, st, payload, nxt);
        // ⚠️ 绝不返回 {kind:'retry'}：重试会复用本步已捕获的旧路由，
        //    只会再撞一次同样的错、白烧一次请求。交回下游收尾。
        return next();
      }
      try {
        console.warn('[dsh-engineering-ui] 模型接替：' + payload.provider
          + ' 错误 ' + code + ' → 切到 ' + nxt.provider + '/' + nxt.model);
      } catch (e) {}
      // 冷却一小会儿再重试，避免立刻撞同一个限流窗口
      // ── 【A4】退避必须读 retry-after，不能写死 800ms（现场 delayMs:491.59）
      st._retryAttempt = (st._retryAttempt || 0) + 1;
      const delay = backoffMsOf(failure, st._retryAttempt);
      try {
        console.warn('[dsh-engineering-ui] 限流/额度退避 ' + delay
          + 'ms（归类 ' + _cls.class + '，第 ' + st._retryAttempt + ' 次）');
      } catch (e) {}
      await new Promise((r) => setTimeout(r, delay));
      return { kind: 'retry' };
    } catch (e) {
      return next();
    }
  }));

  // ── ③ 会话结束/agent 销毁时清理状态，避免 Map 无界增长 ──────────────
  disposers.push(ctx.on('agent/disposed', (payload) => {
    try {
      const id = payload && payload.agent && payload.agent.id;
      if (id) modelChainState.delete(id);
    } catch (e) {}
  }));

  const guardState = new Map(); // agentId -> { count, firstAt, probe }
  const MAX_GUARD = 12;         // 【Bug3】从 40 降到 12：过高的上限本身就会造成"死缠烂打"
  const GUARD_WINDOW = 30 * 60 * 1000; // 30 分钟内计数，超过则重置

  // ══════════════════════════════════════════════════════════════════════
  //  【2026-10-29 修复 · 子代理死循环】把"无进展"变成可判定的结构信号
  // ══════════════════════════════════════════════════════════════════════
  // 实测（竞速小车任务，日志 _logs_666）：
  //   建模小屋 66c7982f 连续 5 步【只输出文本、不调用任何工具】，
  //   每次都写"I'm still working. Let me run the sensor bracket script:"，
  //   最后到 65 步也没有真正发起调用，房间被判 dead，两件零件没建。
  //   与此同时 turn-stopping 守卫每一轮都把它拉回来 —— 共 steer 12 次，
  //   正好撞上 MAX_GUARD 上限才停。也就是说：
  //     · 模型侧确实有"光说不做"的退化（用户判断正确）；
  //     · 但守卫【无条件】把每一次"想停"都当成"你想偷懒"打回去，
  //       连【子代理】也一视同仁，于是把退化放大成了死循环。
  //
  // 本修复不基于"文本重复度"（Bug-47 已证明会误伤），而是看结构事实：
  //   ① 子代理【不设】收尾守卫 —— 它的收尾由父代理的 room-end 决定，
  //      它自己本来就没有"结束任务"的权限，反复 steer 只会让它空转；
  //   ② 主对话连续 N 次"想结束且没产生任何工具调用"→ 判定无进展，
  //      停止 steer 并放行（把控制权交回用户，而不是无限拉回）。
  // 这两个信号都是"回合数/是否存在工具调用"，不依赖措辞。
  const MAX_NO_PROGRESS = 4;    // 连续这么多轮"无工具调用却想结束"就放手
  const GUARD_MAX_STEER_SUBAGENT = 2; // 子代理最多 steer 2 次（原本 12）

  /** 该 agent 是否是被 fork 出来的子代理（delegationDepth > 0）。 */
  function isDelegatedAgent(agent) {
    try {
      const d = agent && (agent.delegationDepth ?? agent.session?.delegationDepth
                          ?? agent.session?.header?.delegationDepth);
      if (typeof d === 'number') return d > 0;
      if (d !== undefined && d !== null) return Number(d) > 0;
      // 兜底：会话 header 带 parentSession 也说明是子代理
      const parent = agent && (agent.parentSession || agent.session?.header?.parentSession);
      return !!parent;
    } catch (e) {
      return false;
    }
  }

  // ══ 【A11】判断"我"是不是总计子代理（analyst）════════════════
  // analyst 的 sessionId 由 mode2_flow.py analyst-bind 写进 mode2_plan.json
  // 的 plan.analyst.session_id。命中就说明该发"总计子代理"文案。
  //
  // 【A11 修复】原实现有三个坑，全部落到"判不出角色 → 发错文案"：
  //   ① 只读 toolsDirCandidates()[0] 下的 mode2_plan.json —— 而 mode2_flow
  //      实际把它写在【任务目录】（workflow_state.work_dir）或 load_cases/ 下，
  //      于是真实任务里这个函数几乎恒 false；
  //   ② 用 sid.indexOf(bind) >= 0 这种【互相包含】判定 —— 只要一个短 id
  //      是另一个的子串就判成同一人，属于"宁可误判成分析师"的错误方向：
  //      误判会把建模小屋的守卫文案换成"你是总计子代理，别调 sw_bridge"，
  //      正好把它推进空转（比不判角色更坏）。
  //   ③ JSON.parse 直接抛到 catch，等于"读坏了就当不是"。
  // 现在：判定搬到 lib/turn-guard.js（纯函数、按 mode2_plan 的权威路径顺序读、
  //   只做【严格相等】），这里只保留一层包装 + header 的显式角色旁证。
  // 取不到身份时返回 false —— 保守回退建模小屋文案（宁可发错，也不因读不到就不拦）。
  function isAnalystAgent(agent) {
    try {
      const h = (agent && agent.session && agent.session.header) || {};
      if (String(h.engineeringRole || '') === 'analyst') return true;
      // 权威判据：mode2_plan.json 里 analyst.session_id（analyst-bind 写入），
      //   由 turn-guard.js 按"任务目录 > toolsDir > load_cases"的顺序读，
      //   只做严格相等比对；读不到 → 返回 false（回退建模小屋文案）。
      const toolsDir = (typeof findToolsDir === 'function') ? findToolsDir() : null;
      const wf = toolsDir ? readJsonSafe(path.join(toolsDir, 'workflow_state.json')) : null;
      const r = turnGuardIsAnalystAgent(agent, toolsDir, wf);
      return !!(r && r.isAnalyst);
    } catch (e) {
      return false;
    }
  }

  /**
   * 最近若干步里，模型是否【连续只说话、没调用任何工具】。
   * 这是"卡住"的结构证据：正常的推进一定会伴随工具调用或状态文件变化。
   */
  function countConsecutiveNoToolSteps(agent, limit = MAX_NO_PROGRESS + 2) {
    try {
      const evs = sessionEvents(agent && agent.session) || [];
      if (!evs.length) return 0;
      let n = 0;
      for (let i = evs.length - 1; i >= 0 && n < limit; i--) {
        const ev = evs[i];
        if (!ev) continue;
        // 只看"一轮模型产出"这个粒度：assistant/message
        const t = String(ev.type || '');
        if (t !== 'assistant/message' && t !== 'assistant') continue;
        const content = ev.data?.message?.content
          || (Array.isArray(ev.data?.content) ? ev.data.content : []);
        let hasTool = false;
        if (Array.isArray(content)) {
          for (const b of content) {
            if (!b || typeof b !== 'object') continue;
            if (b.type === 'tool-call' || b.type === 'tool_use') { hasTool = true; break; }
          }
        }
        if (hasTool) break;        // 一遇到有工具调用的步就停止计数
        n += 1;
      }
      return n;
    } catch (e) {
      return 0;
    }
  }

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

      // ── 【2026-10-29 修复】子代理不设收尾守卫 ──────────────────────────
      // 理由见 MAX_NO_PROGRESS 上方长注释：子代理没有"结束任务"的权限
      //   （收尾由父代理 room-end 决定），把它拉回来只会让它空转。
      //   实测 66c7982f 被 steer 12 次后仍然什么都没多做，最后房间判 dead。
      if (isDelegatedAgent(agent)) {
        const sd = guardState.get(agent.id) || { count: 0, firstAt: Date.now(), probe: 0 };
        if (Date.now() - sd.firstAt > GUARD_WINDOW) {
          sd.count = 0; sd.firstAt = Date.now();
        }
        // 子代理只在【最长 GUARD_MAX_STEER_SUBAGENT 次】内被提醒，
        // 且仅当它其实还在干活（有工具调用）时 —— 否则直接放手。
        const _noToolSub = countConsecutiveNoToolSteps(agent);
        if (sd.count >= GUARD_MAX_STEER_SUBAGENT || _noToolSub >= MAX_NO_PROGRESS) {
          return;                    // 放手：交回父代理/用户处理，不再拉回
        }
        sd.count += 1;
        guardState.set(agent.id, sd);
        // ── 【A11 修复】先判角色，再选文案 ──────────────────────────────
        // 现场（总计子代理 d921be74:497/517）：守卫对它说"你是【建模/专项子代理】
        //   …请调 sw_bridge.py run / visual_check.py gate / room-report"，
        //   而它已完成 10/10 阶段、任务书明写"禁调 sw_bridge"→ 空转 5 步 53 秒。
        // 根因：守卫只有"主对话/子代理"两套文案，analyst 被算进了建模小屋。
        // 判据：mode2_plan.json 的 analyst.session_id（analyst-bind 写入）与本
        //   agent 的 sessionId 严格相等 → 它是总计子代理，发阶段命令文案。
        // 取不到身份 → false，保守回退建模小屋文案（宁可发错，也不因读不到就不拦）。
        let _isAnalyst = false;
        try { _isAnalyst = isAnalystAgent(agent); } catch (e) { _isAnalyst = false; }
        if (_isAnalyst) {
          try {
            console.warn('[dsh-engineering-ui] guard 角色判定：session=' + agent.id
              + ' 是【总计子代理 analyst】→ 发阶段命令文案（不催 sw_bridge）（A11）');
          } catch (e) {}
        }
        const subMsg = createUserMessage({
          content: [{ type: 'text', text: _isAnalyst ? GUARD_TEXT_ANALYST : GUARD_TEXT_SUBAGENT }],
          source: { kind: 'plugin:dsh-engineering-ui' }
        });
        agent.steer(subMsg);
        return;
      }

      const st = guardState.get(agent.id) || { count: 0, firstAt: Date.now(), probe: 0 };
      if (Date.now() - st.firstAt > GUARD_WINDOW) { st.count = 0; st.firstAt = Date.now(); }
      // ── 【2026-10-29 修复】无进展就放手，别把"光说不做"放大成死循环 ──
      // 主对话连续 MAX_NO_PROGRESS 轮"想结束且没有工具调用"→ 判无进展。
      // 此时再 steer 也不会带来新动作（实测 4 轮全部只输出一句话），
      // 继续拉回只会烧额度、并让用户看到"模型一直在重复同一句话"。
      const _noTool = countConsecutiveNoToolSteps(agent);
      if (_noTool >= MAX_NO_PROGRESS) {
        try {
          console.warn('[dsh-engineering-ui] guard 放手：连续 ' + _noTool
                       + ' 轮无工具调用，判定无进展（session=' + agent.id + '）');
        } catch (e) {}
        guardState.delete(agent.id);
        return;
      }
      if (st.count >= MAX_GUARD) {
        // 撞上限也放手（原有行为），但留一条日志便于事后定位
        try {
          console.warn('[dsh-engineering-ui] guard 撞上限放手：已 steer '
                       + st.count + ' 次（session=' + agent.id + '）');
        } catch (e) {}
        return;
      }
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
  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

  // ══ 【压缩历史】GET /dsh-engineering-ui/compaction-history ═══════════════
  // 用户要求（2026-11-07，图三）：
  //   「红框里面放上各个子代理的压缩历史……黑框加上主对话的历史压缩记录」
  //   「显示的使用设置里面的『使用工程模式自带的子代理显示页』判断是否开启」
  //
  // 数据源：dsh-engineering-compaction 写下的 append-only 账本
  //   <state_dir>/compaction_history.jsonl（一行一条，只增不改）
  //
  // 为什么这里【自己读文件】而不 import 那个包：
  //   · 压缩包是 preset 子项，UI 插件是宿主层插件，两者生命周期不同；
  //   · 一个只读 JSONL 解析不值得引入跨包耦合与版本约束。
  //   契约只有两个文件名，见 dsh-engineering-compaction/lib/history.js。
  const COMPACT_HISTORY_FILE = 'compaction_history.jsonl';

  /** 解析压缩账本所在目录（与 Python _store.state_dir() 同优先级）。 */
  function compactionStateDir() {
    try { return findToolsDir(); } catch (e) { return null; }
  }

  // ══ 【BUG-A14 修复】压缩阈值必须跟着【会话自己的 contextWindow】走 ═══════
  // 现场（2026-11-07 会话）：6 次压缩触发点全在 99,838–100,025 token 之间，
  //   而 `:224` 明确记着 `{"type":"request/context","contextWindow":200000}`
  //   —— 窗口只用到 50% 就开始压缩。代价是每次压缩都要重写摘要、丢细节，
  //   直接引出"压缩后重复读同一文件"和"同一段逻辑重写 4 版"（B6）。
  //
  // 判据：阈值 = contextWindow × 0.7，且不低于 60K（小窗口按比例算会过频压缩）。
  //   取不到 contextWindow → 回退 100000（历史行为）并【打日志】，绝不崩。
  //
  // 为什么这里只"算 + 记 + 暴露"而不直接改 compaction-basic 的配置：
  //   · 真正的触发点在 @deepseek-ai/dsh-compaction-basic 的 agent/pre-step，
  //     它的 config 在构造时就被 deepFreeze 了（见其 resolveConfig 的返回值），
  //     运行期改不动，硬改只会静默失败；
  //   · 所以阈值本身由 preset 的 thresholdRatio 下发（见 preset-engineering.patch.yml），
  //     宿主这一侧负责【把 effective 阈值如实算出来并留痕】，让"阈值配错了"
  //     这件事第一次变得可观测 —— 本轮缺陷的根源恰恰是没人知道它一直是 100K。
  const compactThresholdCache = new Map();   // sessionId -> { value, source, contextWindow, at }
  let compactThresholdWarned = new Set();

  /**
   * 【BUG-A14 修复·补】读 preset 里 compaction-basic 的阈值参数（只读，带缓存）。
   *
   * 为什么不直接读宿主的运行时 config：@deepseek-ai/dsh-compaction-basic 在
   *   resolveConfig() 里就 deepFreeze 了（源码:75-86），运行期拿不到也改不动。
   *   而 preset yml 是【唯一真源】：宿主启动时正是照它装配的。
   * 找不到 preset 时回落到 host 侧常量（0.7 / 48000），并标 source='default'，
   *   让排查时能区分"按配置算的"与"按默认算的"。
   */
  let compactPresetCache = null;
  let compactPresetCacheAt = 0;
  function compactionPresetConfig() {
    const now = Date.now();
    if (compactPresetCache && (now - compactPresetCacheAt) < 30000) return compactPresetCache;
    const out = { thresholdRatio: COMPACTION_THRESHOLD_RATIO,
                  headroomTokens: ENG_COMPACTION_CONFIG.headroomTokens,
                  maxTokens: ENG_COMPACTION_CONFIG.maxTokens,
                  chunkChars: ENG_COMPACTION_CONFIG.chunkChars,
                  retainRatio: ENG_COMPACTION_CONFIG.retainRatio,
                  source: 'default', file: null };
    try {
      // ══ 【2026-11-11 修复·P1 preset 探测路径错】══════════════════════════
      // 现场（验收复跑 20261111_131815_580E38FA）：面板显示的 limiter /
      //   limitBudgetTokens 恒按 headroomTokens=48000 算，而真实 preset 写的是
      //   8000（preset-engineering.patch.yml:578）→ 预算项被低估 40K，
      //   "被哪一项压住"的结论一半是错的。
      //
      // 根因：旧实现只探测一处 `<state_dir>/../preset-engineering.patch.yml`。
      //   实测该路径【不存在】—— 安装副本的 .agent-presets/engineering 下只有
      //   preset.yml（元数据），真正的 preset-engineering.patch.yml 是作为
      //   插件包的一部分装进 profile 的 node_modules 的：
      //     <profile>/node_modules/dsh-engineering-ui/preset-engineering.patch.yml
      //   探测失败 → source 恒 'default' → headroom 恒 48000。
      //
      // 修复：按「离本插件越近越权威」的顺序多路探测，全部命中不了才回落默认。
      //   ① 本插件包根（lib/..）—— 与运行时真正加载的那份同一出处，最权威；
      //   ② state_dir 的上一级（历史布局，保留兼容）；
      //   ③ <state_dir>/.. 之上一级（某些安装把 engineering 当独立目录）。
      const dir = findToolsDir();
      const cands = [];
      // ① 插件包根：本文件是 <pkg>/lib/index.js → 包根 = lib 的上一级。
      //
      // ⚠️ 【2026-11-11 踩坑】这里【不能写 import.meta.url】：
      //    本插件有多处测试（test/webots-host.test.mjs、compiler-wiring 等）
      //    把 lib/index.js 丢进 node:vm 的 CommonJS 上下文里求值，
      //    `import.meta` 在那种上下文是【语法错误】，会让整块求值直接抛
      //    "Cannot use 'import.meta' outside a module" —— 插件测试全红。
      //    所以只用"运行时存在的全局量"兜：__dirname（vm/CJS）→ process.cwd()。
      //    cwd 命不中时会落到下面的 findToolsDir 分支，功能不因此丢失。
      try {
        const _here = (typeof __dirname === 'string' && __dirname)
          ? __dirname
          : null;
        if (_here) {
          cands.push(path.join(_here, '..', 'preset-engineering.patch.yml'));
        }
      } catch (e) {}
      // ①b 从 cwd 起多探两级（进程一般在 DSH 安装目录或 profile 目录启动）
      try {
        const _cwd = process.cwd();
        cands.push(path.join(_cwd, 'preset-engineering.patch.yml'));
        cands.push(path.join(_cwd, 'node_modules', 'dsh-engineering-ui',
                             'preset-engineering.patch.yml'));
        cands.push(path.join(_cwd, '..', 'node_modules', 'dsh-engineering-ui',
                             'preset-engineering.patch.yml'));
      } catch (e) {}
      if (dir) {
        // ② 历史布局：<state_dir>/../preset-engineering.patch.yml
        cands.push(path.join(dir, '..', 'preset-engineering.patch.yml'));
        // ③ 再上一级（engineering/ 独立目录布局）
        cands.push(path.join(dir, '..', '..', 'preset-engineering.patch.yml'));
        // ④ 安装副本里 preset 随插件包进 node_modules —— 与 ① 同义，
        //    但 findToolsDir 到不了插件目录时（自定义 DSH_STATE_DIR）靠这条兜底。
        cands.push(path.join(dir, '..', 'node_modules',
                             'dsh-engineering-ui', 'preset-engineering.patch.yml'));
      }
      for (const p of cands) {
        let raw;
        try {
          if (!fs.existsSync(p)) continue;
          raw = fs.readFileSync(p, 'utf8');
        } catch (e) { continue; }
        // 极简 YAML 取值：只认这几个键（不引 yaml 库 —— 宿主层少一个依赖少一个故障点）
        const grab = (key) => {
          const m = new RegExp('^\\s*' + key + '\\s*:\\s*([0-9.]+)\\s*$', 'm').exec(raw);
          return m ? Number(m[1]) : null;
        };
        const tr = grab('thresholdRatio');
        const hd = grab('headroomTokens');
        const mt = grab('maxTokens');
        const cc = grab('chunkChars');
        const rr = grab('retainRatio');
        if (tr !== null) out.thresholdRatio = tr;
        if (hd !== null) out.headroomTokens = hd;
        // ── 【2026-11-12】maxTokens 必须一起读：它决定摘要器输出上限。
        // 不配时宿主会让它 = headroomTokens（见 dsh-compaction-basic:63-64），
        // 而 headroom 只有 8000 → 摘要被截断 → empty-stream。
        if (mt !== null) out.maxTokens = mt;
        if (cc !== null) out.chunkChars = cc;
        if (rr !== null) out.retainRatio = rr;
        out.source = 'preset';
        out.file = p;
        break;
      }
    } catch (e) {}
    compactPresetCache = out;
    compactPresetCacheAt = now;
    return out;
  }

  /**
   * 取该会话的压缩阈值（contextWindow × 0.7，下限 60K，取不到回退 100K）。
   * @returns {{value:number, source:string, contextWindow:number|null, ratio:number, at:number}}
   */
  function compactionThresholdOf(agent) {
    try {
      const sid = String((agent && (agent.id || agent.sessionId)) || '');
      const cached = compactThresholdCache.get(sid);
      if (cached) return cached;
      const events = sessionEvents(agent && agent.session) || [];
      const header = (agent && agent.session && agent.session.header) || {};
      const cw = latestContextWindow(events, header);
      const r = resolveCompactionThreshold(cw);
      const rec = {
        value: r.threshold,
        source: r.source,
        contextWindow: r.contextWindow,
        ratio: r.ratio,
        // 下限高于窗口本身 → 这个阈值永远达不到（退化情形，必须看得见）
        exceedsWindow: !!r.exceedsWindow,
        at: Date.now(),
      };
      // ── 【BUG-A14 修复·补】指出"被哪一项压住"，供设置页/排查定位 ────────
      // 见 turn-guard.explainCompactionLimit 的注释：宿主实际阈值是
      //   min(contextWindow×ratio, contextWindow−reserved−headroom)，
      // 只调 ratio 可能反而让阈值更低（200K/48000 headroom 时压到 ~86K）。
      // headroomTokens / thresholdRatio 取自 preset 的 compaction-basic 配置
      //   （宿主那份 config 构造后 deepFreeze，运行期不可写，故这里只做核算）。
      try {
        const pc = compactionPresetConfig();
        // ── 【2026-11-12 修复·reserved 写死 0 让 limiter 永远算错】────────────
        // 旧调用传的是 `null` → explainCompactionLimit 内部把 reserved 当 0 →
        // 预算项恒等于"窗口 − headroom"，limiter 永远算出 ratio。
        // 真实口径（宿主 dsh-compaction-basic:757-758）：
        //   reservedCompletionTokens = header.config.maxTokens ?? defaultMaxTokens ?? 0
        // 所以这里把【本会话真实的 reserved】传进去：
        //   ① 优先 header.config.maxTokens（路由真正声明了多少补全预算）；
        //   ② 没有就用 preset 里的 maxTokens（工程模式已显式配置）；
        //   ③ 都没有才是 0。
        let _reserved = 0;
        try {
          const _mt = header && header.config && header.config.maxTokens;
          if (Number.isFinite(Number(_mt)) && Number(_mt) > 0) _reserved = Number(_mt);
          else if (Number.isFinite(Number(pc.maxTokens)) && Number(pc.maxTokens) > 0) {
            // 注意：preset 的 maxTokens 是【摘要器输出上限】，不是路由的补全预算。
            // 它不该直接当 reserved —— 否则 1M 窗口会算成 1000000-48000-8000。
            // 这里只为让面板能显示"这个数存在"，reserved 仍以路由声明为准。
            _reserved = 0;
          }
        } catch (e) { _reserved = 0; }
        const ex = explainCompactionLimit(cw, pc.thresholdRatio, pc.headroomTokens,
                                          _reserved);
        rec.presetThresholdRatio = pc.thresholdRatio;
        rec.presetHeadroomTokens = pc.headroomTokens;
        rec.presetMaxTokens = pc.maxTokens;
        rec.presetRetainRatio = pc.retainRatio;
        rec.presetSource = pc.source;
        rec.reservedCompletionTokens = _reserved;
        rec.limitRatioTokens = ex.limitRatioTokens;
        rec.limitBudgetTokens = ex.limitBudgetTokens;
        rec.limiter = ex.limiter;
        if (ex.error) {
          // 预算项为负 → 宿主的压缩彻底不工作，必须显式暴露而不是当成"阈值偏低"
          rec.limitError = ex.error;
          rec.limitErrorMessage = ex.message;
        }
        // ── 摘要器输出上限：这是"压不动"的关键解释项 ──────────────────────
        // 没显式配 maxTokens 时宿主让它 = headroomTokens（8000）→ 抄不完编号 →
        // 截断 → empty-stream。面板必须能把这件事说清楚。
        rec.summaryMaxTokens = Number.isFinite(Number(pc.maxTokens)) && Number(pc.maxTokens) > 0
          ? Number(pc.maxTokens)
          : Number(pc.headroomTokens);
        rec.summaryMaxTokensExplicit = Number.isFinite(Number(pc.maxTokens)) && Number(pc.maxTokens) > 0;
      } catch (e) {}
      if (sid) compactThresholdCache.set(sid, rec);
      // 每个会话只打一次，避免每步刷屏（守卫/hook 都在热路径上）
      if (sid && !compactThresholdWarned.has(sid)) {
        compactThresholdWarned.add(sid);
        try {
          if (r.source === 'fallback') {
            console.warn('[dsh-engineering-ui] 压缩阈值：会话 ' + sid
              + ' 取不到 contextWindow → 回退 ' + r.threshold
              + '（历史行为；若窗口更大就会过早压缩）（A14）');
          } else if (r.source === 'floor') {
            console.warn('[dsh-engineering-ui] 压缩阈值：会话 ' + sid
              + ' 窗口 ' + r.contextWindow + ' × ' + r.ratio + ' 低于下限 → 采用 '
              + r.threshold + '（防止小窗口过频压缩）'
              + (r.exceedsWindow ? '；⚠️ 但该阈值已高于窗口本身，实际永不触发，'
                 + 'upper bound 会被 CONTEXT_WINDOW_EXCEEDED 兜住' : '')
              + '（A14）');
          } else {
            console.warn('[dsh-engineering-ui] 压缩阈值：会话 ' + sid
              + ' 窗口 ' + r.contextWindow + ' × ' + r.ratio + ' = ' + r.threshold + '（A14）');
          }
        } catch (e) {}
      }
      return rec;
    } catch (e) {
      // 任何异常都不能影响会话：按"取不到 contextWindow"处理
      return { value: COMPACTION_THRESHOLD_FALLBACK, source: 'fallback',
               contextWindow: null, ratio: COMPACTION_THRESHOLD_RATIO, at: Date.now() };
    }
  }

  // 会话释放时清缓存（否则 Map 与 warned 集合会无界增长）
  disposers.push(ctx.on('agent/disposed', (payload) => {
    try {
      const id = payload && payload.agent && payload.agent.id;
      if (!id) return;
      compactThresholdCache.delete(String(id));
      compactThresholdWarned.delete(String(id));
    } catch (e) {}
  }));

  /**
   * 读"压缩引擎已加载"标记（dsh-engineering-compaction 在构造时写的）。
   *
   * 为什么面板需要它：账本为空时，用户看不出是
   *   ① 引擎没加载（preset 那行没解析到 / 包没装好）→ 永远不会有记录；
   *   ② 引擎加载了、只是这条会话还没到阈值。
   * 这两种在界面上完全一样，以前只能翻日志。
   */
  const COMPACT_ENGINE_FILE = 'compaction_engine.json';
  function readCompactionEngine() {
    const dir = compactionStateDir();
    if (!dir) return null;
    try {
      const p = path.join(dir, COMPACT_ENGINE_FILE);
      if (!fs.existsSync(p)) return null;
      const j = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (!j || typeof j !== 'object') return null;
      return {
        at: Number(j.at) || 0,
        mode: String(j.mode || ''),
        pid: Number(j.pid) || 0,
      };
    } catch (e) { return null; }
  }

  /** 读账本，按 root/target 过滤。坏行跳过，绝不让 UI 因一行损坏而空白。 */
  function readCompactionHistory(opts) {
    const o = opts || {};
    const dir = compactionStateDir();
    if (!dir) return { ok: false, entries: [], error: '未找到工程模式状态目录' };
    const p = path.join(dir, COMPACT_HISTORY_FILE);
    let raw;
    try {
      if (!fs.existsSync(p)) {
        return { ok: true, entries: [], stateDir: dir, exists: false, engine: readCompactionEngine() };
      }
      raw = fs.readFileSync(p, 'utf8');
    } catch (e) {
      return { ok: false, entries: [], stateDir: dir, error: String((e && e.message) || e) };
    }
    let entries = [];
    for (const line of raw.split(/\\r?\\n/)) {
      const s = line.trim();
      if (!s) continue;
      try {
        const rec = JSON.parse(s);
        if (rec && typeof rec === 'object') entries.push(rec);
      } catch (e) { /* 坏行跳过 */ }
    }
    if (o.root) entries = entries.filter((r) => String(r.root || '') === String(o.root));
    if (o.target) entries = entries.filter((r) => String(r.target || '') === String(o.target));
    const limit = Number.isFinite(o.limit) && o.limit > 0 ? Math.floor(o.limit) : 400;
    const total = entries.length;
    if (entries.length > limit) entries = entries.slice(entries.length - limit);
    // 每个会话的累计计数（红框里「压缩 3 次」这种徽标要用）
    const counts = {};
    for (const e of entries) {
      const k = String(e.target || '');
      const c = counts[k] || { target: k, label: e.label || '', count: 0, lastAt: 0, numbers: 0 };
      c.count += 1;
      if (Number(e.at) > c.lastAt) { c.lastAt = Number(e.at) || 0; c.label = e.label || c.label; }
      c.numbers += Number(e.l0_numbers || 0) + Number(e.l0_materials || 0);
      counts[k] = c;
    }
    return {
      ok: true,
      stateDir: dir,
      exists: true,
      total,
      entries,
      counts: Object.keys(counts).map((k) => counts[k]),
      // 引擎是否被加载过（面板用它区分"引擎没起来"和"还没到阈值"）
      engine: readCompactionEngine(),
    };
  }

  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/compaction-history',
    handler: async (req, res) => {
      const q = queryOf(req);
      const rootId = (q.get('rootSessionId') || '').trim();
      const target = (q.get('target') || '').trim();
      const limit = Number(q.get('limit') || 400);
      try {
        const r = readCompactionHistory({
          root: rootId || undefined,
          target: target || undefined,
          limit,
        });
        // ── 【BUG-A14 修复】把 effective 压缩阈值一并回给前端 ──────────────
        // 目标会话：显式 target 优先，否则 rootId（主对话自己的阈值）。
        // 取不到会话对象就退回"100K + fallback"，并如实标注来源 ——
        //   宁可让面板显示"未知"，也不能让面板显示一个编出来的阈值。
        let threshold = null;
        try {
          const probeId = target || rootId;
          let ag = null;
          if (probeId) {
            ag = (ctx.agents && typeof ctx.agents.get === 'function')
              ? ctx.agents.get(String(probeId)) : null;
          }
          if (ag) {
            threshold = compactionThresholdOf(ag);
          } else {
            const fb = resolveCompactionThreshold(null);
            threshold = { value: fb.threshold, source: 'fallback',
                          contextWindow: null, ratio: fb.ratio, at: Date.now() };
          }
        } catch (e) {}
        return send(res, 200, Object.assign({}, r, { threshold }));
      } catch (error) {
        return send(res, 200, { ok: false, entries: [], error: String((error && error.message) || error) });
      }
    }
  })));

  // ══════════════════════════════════════════════════════════════════════════
  // 【外部监控器 · 命令许可】POST /dsh-engineering-ui/sw-monitor-permit
  // ══════════════════════════════════════════════════════════════════════════
  // 这是"每条 SW 命令都必须核验并记账"（教程 3.3）的代码落点。
  //
  // 为什么取锁必须放在【宿主】而不是 Python 里做：
  //   教程 3.1 要求"许可证信息由宿主与真实会话、实际工具调用和 worker 绑定"，
  //   "禁止使用代理可自行填写的 room、lease 字符串来模拟该身份"。
  //   如果 sw_bridge 自己拿 room 字符串去排队、自己 claim，那么任何进程
  //   （包括模型随手写的脚本）都能伪造一个 room 把票领走 —— 队列形同虚设。
  //   放在这里以后：
  //     · 排队票由宿主用【真实 agent 会话 id + epoch】创建，Python 传不了；
  //     · 队首预留时宿主挂一个 wait-lock 后台 job，图一的"阻塞等待"变成 job id；
  //     · claim 也只发生在宿主，Python 拿到的是一次性 command id + 结果。
  //   Python 侧（sw_bridge._monitor_acquire / _monitor_end）只消费结论。
  //
  // 请求：{room, command, part?, wait_s?, session?}
  // 响应：{ok, command, state:'running'} 或 {ok:false, code, error, position?, owner?}
  const monitorPermits = new Map();   // command id -> {room, session, startedAt}
  const monitorWaits = new Map();     // room -> jobId（同一房间只挂一个等待 job）

  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/sw-monitor-permit',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      if (!swMonitorBridge) {
        return send(res, 200, { ok: false, code: 'MONITOR_BRIDGE_DOWN', error: '宿主桥未装配' });
      }
      const _raw = await readBody(req);
      let body = null;
      try { body = JSON.parse(_raw); } catch (e) { body = null; }
      if (!body || typeof body !== 'object') {
        return send(res, 400, { ok: false, code: 'BAD_REQUEST', error: 'bad json' });
      }
      const room = String(body.room || '').trim();
      const command = String(body.command || '').trim();
      const part = body.part ? String(body.part) : undefined;
      const waitS = Number(body.wait_s || 0);
      if (!room) return send(res, 200, { ok: false, code: 'ROOM_REQUIRED', error: '缺少 room' });
      if (!command) return send(res, 200, { ok: false, code: 'COMMAND_REQUIRED', error: '缺少 command' });
      if (!swMonitorBridge.enabled()) {
        return send(res, 200, {
          ok: false, code: 'MONITOR_NOT_CONFIGURED',
          error: '未接入外部监控器：SW 使用权没有权威，命令不得执行。',
          hint: ('启动 DSH-SW-Monitor 后执行 python mode_gate.py sw-monitor attach --drained；'
            + '旧进程内锁已删除，没有回退路径。'),
        });
      }
      try {
        // ① 申请使用权（request → claim 由宿主桥封装成 acquire）。
        //    注意：监控器把"建票"与"领租约"拆成两步，且必须带 task ——
        //    这些细节都在 lib/sw-monitor.js 里处理，本路由只关心三态结论。
        const scope = { room, session: String(body.session || '') };
        const got = await swMonitorBridge.acquire({
          room, session: scope.session, wait_s: Number.isFinite(waitS) && waitS > 0 ? waitS : 0,
        });
        const state = String((got && got.state) || '');
        // ② 排队中：挂后台 job 等队首预留（这就是图一的替换点）。
        //    注意：这里绝不用 sleep 轮询 —— 等待完全发生在 DSH job 里，
        //    模型拿到的是 job id，回合随时可以结束。
        if (state === 'queued') {
          // 等待 job 必须用【同一张票】等，否则每次重试都会新开一张票插队。
          if (got && got.ticket) scope.ticket = got.ticket;
          const jobs = (() => { try { return ctx.get ? ctx.get('jobs') : undefined; } catch (e) { return undefined; } })();
          if (!jobs || typeof jobs.start !== 'function') {
            return send(res, 200, {
              ok: false, code: 'JOBS_UNAVAILABLE', state: 'queued',
              position: got && got.position, owner: got && got.owner,
              error: 'SW 使用权在排队，但宿主没有 jobs 服务，无法挂后台等待任务。',
            });
          }
          const job = swMonitorBridge.startWaitLockJob(ctx, jobs, scope, room);
          monitorWaits.set(room, job);
          return send(res, 200, {
            ok: true, state: 'queued', job_id: job,
            position: got && got.position, owner: got && got.owner,
            hint: ('SW 使用权在队列第 ' + (got && got.position) + ' 位，已挂后台任务 job=' + job
              + '。用 job_output 等它自然结束（不要 sleep 轮询），'
              + '结束后重试同一条命令即可。'),
          });
        }
        // ③ 记账：begin 必须在启动 CAD worker 之前完成。
        //    lease 已经在 acquire() 里领到，且只留在宿主内存里 ——
        //    教程 3.1 要求许可与真实会话/工具调用绑定，不能交给调用方保管。
        await swMonitorBridge.call('begin', { room, command, part });
        monitorPermits.set(command, { room, session: scope.session, startedAt: Date.now() });
        try { monitorWaits.delete(room); } catch (e) {}
        return send(res, 200, {
          ok: true, state: 'running', command,
          position: got && got.position,
        });
      } catch (error) {
        const code = (error && error.code) || 'MONITOR_ERROR';
        return send(res, 200, { ok: false, code, error: String((error && error.message) || error) });
      }
    }
  })));

  // ══════════════════════════════════════════════════════════════════════════
  // 【外部监控器 · 命令收尾】POST /dsh-engineering-ui/sw-monitor-done
  // ══════════════════════════════════════════════════════════════════════════
  // 教程 3.3 的两条硬要求在这里落地：
  //   · end 必须在"确认 worker / 子进程已退出、该次命令无在途 COM 操作"之后；
  //   · 命令返回但后台 job 尚未 settled、spawn 失败后子进程存活情况不明、
  //     超时、服务掉线时，留在隔离/在途状态，禁止用 finally 无条件 end/release。
  // 因此本端点【要求调用方显式声明 drained】，并拒绝未知 command。
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/sw-monitor-done',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      if (!swMonitorBridge) {
        return send(res, 200, { ok: false, code: 'MONITOR_BRIDGE_DOWN', error: '宿主桥未装配' });
      }
      const _raw = await readBody(req);
      let body = null;
      try { body = JSON.parse(_raw); } catch (e) { body = null; }
      if (!body || typeof body !== 'object') {
        return send(res, 400, { ok: false, code: 'BAD_REQUEST', error: 'bad json' });
      }
      const room = String(body.room || '').trim();
      const command = String(body.command || '').trim();
      const release = body.release === true;
      if (!room || !command) {
        return send(res, 200, { ok: false, code: 'BAD_REQUEST', error: '需要 room 与 command' });
      }
      const known = monitorPermits.get(command);
      if (!known) {
        // 未知 command 不能"帮忙 end 一下"：那等于给没取过许可的命令背书。
        return send(res, 200, {
          ok: false, code: 'UNKNOWN_COMMAND',
          error: '该 command 不是本宿主发放的许可，拒绝收尾。',
        });
      }
      try {
        await swMonitorBridge.call('end', { room, command });
        monitorPermits.delete(command);
        let rel = null;
        if (release) {
          rel = await swMonitorBridge.call('release', { room, drained: body.drained === true });
        }
        return send(res, 200, { ok: true, state: 'ended', released: !!rel });
      } catch (error) {
        const code = (error && error.code) || 'MONITOR_ERROR';
        return send(res, 200, { ok: false, code, error: String((error && error.message) || error) });
      }
    }
  })));

  // ══════════════════════════════════════════════════════════════════════════
  // 【外部监控器 · 宿主桥】POST /dsh-engineering-ui/sw-monitor-op
  // ══════════════════════════════════════════════════════════════════════════
  // 为什么需要这一层：host.token 与 epoch 只能待在宿主进程内存里。
  //   · Python 门禁（sw_bridge / mode_gate）要排队和记账，但它不该看见 token；
  //   · 模型要"等锁"，但模型自报的 room/lease 字符串不可信；
  //   · 监控器的票按 epoch+task+room+session 绑定，epoch 换了旧票作废。
  // 于是：Python 只调本机这条路由 → 宿主补 epoch/token 转发 → 结果回传。
  // 白名单在 lib/sw-monitor.js 的 OPS 里，路由本身不做任何业务判定。
  //
  // ⚠️ 端口：Python 侧【不要】写死，也不该依赖 DSH_WEB_PORT ——
  //   实测真机会话里 DSH_WEB_PORT 根本没被注入，而 GUI 端口每次启动都变
  //   （2026-11-12 会话 630e2e4d：宿主在 57496，Python 却连 19387，
  //    结果 sw-monitor attach 必然 MONITOR_BRIDGE_UNREACHABLE）。
  //   正确做法是读宿主注入的 DSH_WEB_URL（ask_user.py 也是这么修的）。
  //   本插件只负责"在这台宿主的这个端口上提供这条路由"，端口由宿主决定。
  let swMonitorBridge = null;
  try {
    swMonitorBridge = new MonitorBridge({
      stateDir: () => findToolsDir(),
      warn: (m) => { try { console.warn('[dsh-engineering-ui] ' + m); } catch (e) {} },
    });
  } catch (e) {
    swMonitorBridge = null;
  }

  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/sw-monitor-op',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      if (!swMonitorBridge) {
        return send(res, 200, { ok: false, code: 'MONITOR_BRIDGE_DOWN', error: '宿主桥未装配' });
      }
      const _raw = await readBody(req);
      let body = null;
      try { body = JSON.parse(_raw); } catch (e) { body = null; }
      if (!body || typeof body !== 'object') {
        return send(res, 400, { ok: false, code: 'BAD_REQUEST', error: 'bad json' });
      }
      const op = String(body.op || '').trim();
      const payload = (body.payload && typeof body.payload === 'object') ? body.payload : {};
      // 记账类操作（begin/end/part-*）一次 SW 命令就来一轮，正常门禁会打到上千次，
      //   所以给它们单独一个更宽的桶；排队类操作本来就少，用默认桶即可。
      const bucket = 'sw-monitor:' + (/^(begin|end|part-)/.test(op) ? 'ledger' : 'ops');
      if (rateLimited(bucket)) {
        return send(res, 429, { ok: false, code: 'RATE_LIMITED', error: '请求过于频繁（监控器桥限流）' });
      }
      try {
        const out = await swMonitorBridge.call(op, payload);
        return send(res, 200, Object.assign({ ok: true }, out));
      } catch (error) {
        const code = (error && error.code) || 'MONITOR_ERROR';
        const message = String((error && error.message) || error);
        // 可达性/未接管属于【可预期的降级】，用 200 返回让 Python 侧拿到结构化 code；
        //   真正 5xx 只留给宿主内部异常（那样才是 bug）。
        return send(res, 200, { ok: false, code, error: message });
      }
    }
  })));

  // ══ 【零件进度表】GET /dsh-engineering-ui/parts-board ═══════════════════
  // 图二：红线上面是子代理，下面是本轮要造的零件表（正在建/未开始/完成…）。
  //
  // 数据源换成了【外部监控器】（教程 4.3）：mode2_flow.py parts-board 现在
  //   直接 rpc('board')，状态来自监控器的逐件事件（part-start / part-saved /
  //   part-failed）与验收结论，不再读 prime_reports（该数据源随总理子代理
  //   一起删除了）。
  //   JS 侧仍然【不重新判定状态】，只做 HTTP 转发 —— 面板与门禁必须看到同一份
  //   结论（历史教训：UI 自己猜状态，和门禁打架）。
  //   监控器不可达时 Python 返回 degraded=true，前端据此显示「不可用」，
  //   绝不回退成「零件列表是空的」（那会让人以为任务还没开始）。
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/parts-board',
    handler: async (req, res) => {
      try {
        const dir = findToolsDir();
        if (!dir) return send(res, 200, { ok: false, error: '未定位到 tools 目录' });
        const script = path.join(dir, 'mode2_flow.py');
        if (!fs.existsSync(script)) {
          return send(res, 200, { ok: false, error: '未找到 mode2_flow.py: ' + script });
        }
        const py = resolvePython();
        if (!py) return send(res, 200, { ok: false, error: '未找到可用的 Python 解释器' });
        const r = spawnSync(py, [script, 'parts-board'], {
          encoding: 'utf8', timeout: 90000, cwd: dir, windowsHide: true, env: pyEnv(),
        });
        const out = String((r && r.stdout) || '');
        const start = out.indexOf('{');
        let parsed = null;
        if (start >= 0) { try { parsed = JSON.parse(out.slice(start)); } catch (e) { parsed = null; } }
        if (!parsed) {
          return send(res, 200, {
            ok: false, error: 'parts-board 没有返回可解析的 JSON',
            stdout_tail: out.slice(-800),
            stderr_tail: String((r && r.stderr) || '').slice(-800),
            // 【BUG-B1 修复】解析失败也带 pending_jobs —— 面板至少要能看出
            //   "有子代理收工了但后台还在跑"，而不是因为 Python 那一步挂了
            //   就整块空白（B1 的现场正是"报告没写、job 还在跑"）。
            pending_jobs: pendingJobsSummary(),
          });
        }
        // ── 【BUG-B1 修复】把"回合已结束但后台 job 未 settle"的会话一并暴露 ──
        // 现场（b55576ac:670-673）：turn/end{completed} 先到、job/settled 后到，
        //   父代理据此当完成，21 分钟后才发现 run6.log 是 0 字节。
        // 这里把宿主已知的未完成 job 作为【只读附加字段】挂上去：
        //   · 不改 Python 的判定结论（面板的权威仍是 parts-board 本身）；
        //   · 名字带 _host 后缀，明确它来自宿主侧而不是门禁侧，避免两套结论打架；
        //   · 读不到就留空数组 + unavailable 标记，绝不让面板因此报错。
        try {
          parsed.pending_jobs_host = pendingJobsSummary();
        } catch (e) {}
        return send(res, 200, parsed);
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  // ══════════════════════════════════════════════════════════════════════════
  // 【第4项 · ctx.jobs 版】SW 健康看护 —— 走 DSH 官方后台任务运行时
  // ══════════════════════════════════════════════════════════════════════════
  // 用户原话：
  //   「在子代理的SW锁解锁之后，利用SW建模之前，利用 DSH 的后台任务运行时
  //     （ctx.jobs），让父代理启动一个耗时命令，专门做轻量级的定期检查。
  //     比如每 10 秒统计一次系统关键指标，一旦内存、CPU超85％阈值就输出一行
  //     警报。这个 job 会立即返回一个 job id，在后台持续跑着。一旦，相关指数
  //     超标立马关闭SW」
  //
  // ── 与已实现的 mode_gate.ensure_sw_guard 的关系（两条路都用，互补）────────
  //   · mode_gate.ensure_sw_guard()：由 sw_bridge 在**取锁那一刻**自动拉起
  //     一个 OS 子进程。优点是"任何取锁路径必然触发"，不依赖 DSH 运行时；
  //     缺点是不产生 DSH job id（用户明确要求"立即返回一个 job id"）。
  //   · 本端点：用 ctx.jobs.start() 把同一件事注册为 **DSH 后台任务**，
  //     因而有 job id、能被 job_list / job_output / job_kill 看到与管理。
  //
  //   ctx.jobs 契约（asar 1036381-1036400 实测）：
  //     const jobs = ctx.get('jobs');
  //     jobs.start({ kind, label, owner, run: () => ({ cancel, done }) })
  //   注意 run 必须**立即**返回 {cancel, done}（done 是 Promise），
  //   不能在 run 里 await 长任务 —— 否则 start 不会立即返回 job id。
  //
  //   缺少 @deepseek-ai/dsh-jobs / dsh-tool-jobs 时 ctx.get('jobs') 为 undefined，
  //   官方原话（asar 1036382）会抛 "background jobs unavailable"。
  //   本端点对此**显式降级**：返回 job_id=null + 说明，并指出 OS 子进程那条路
  //   仍在工作（不假装成功，也不阻断建模）。
  const SW_GUARD_INTERVAL_MS = 10000;   // 用户要求：每 10 秒采一次
  const SW_GUARD_CPU_LIMIT = 85;
  const SW_GUARD_MEM_LIMIT = 85;
  //: 同一进程内只保留一个看护 job（避免每次取锁都堆一个）
  let swGuardJobId = null;
  let swGuardState = { ticks: 0, alerts: [], killed: false, startedAt: null, last: null };

  /** 读一次系统 CPU/内存。 */
  // ⚠️ index.js 是 ESM（顶部用 import），**没有 require**。
  //    原先这里写 require('node:os') 会在 ESM 下抛 ReferenceError。
  //    因此改用文件已 import 的 os / spawn（见顶部 import）。
  function sampleSystem() {
    try {
      const total = os.totalmem();
      const free = os.freemem();
      const memPct = total > 0 ? ((total - free) / total) * 100 : null;
      let cpuPct = null;
      try {
        const cpus = os.cpus() || [];
        let idle = 0, totalT = 0;
        for (const c of cpus) {
          const t = c.times || {};
          idle += (t.idle || 0);
          totalT += (t.user || 0) + (t.nice || 0) + (t.sys || 0) + (t.irq || 0) + (t.idle || 0);
        }
        if (globalThis.__engCpuPrev) {
          const dIdle = idle - globalThis.__engCpuPrev.idle;
          const dTotal = totalT - globalThis.__engCpuPrev.total;
          if (dTotal > 0) cpuPct = Math.max(0, Math.min(100, (1 - dIdle / dTotal) * 100));
        }
        globalThis.__engCpuPrev = { idle, total: totalT };
      } catch (e) { cpuPct = null; }
      return { ok: true, cpu_pct: cpuPct, mem_pct: memPct,
               total_mb: total / 1048576, free_mb: free / 1048576 };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  /** 超阈值判定（阈值来自代码常量，不来自调用方）。 */
  function judgeSystem(s) {
    const reasons = [];
    if (typeof s.cpu_pct === 'number' && s.cpu_pct > SW_GUARD_CPU_LIMIT) {
      reasons.push('CPU 占用 ' + s.cpu_pct.toFixed(1) + '% 超过阈值 ' + SW_GUARD_CPU_LIMIT + '%');
    }
    if (typeof s.mem_pct === 'number' && s.mem_pct > SW_GUARD_MEM_LIMIT) {
      reasons.push('内存占用 ' + s.mem_pct.toFixed(1) + '% 超过阈值 ' + SW_GUARD_MEM_LIMIT + '%');
    }
    return { breach: reasons.length > 0, reasons };
  }

  /** 立即关闭 SolidWorks（taskkill）。进程本来不在 → 视为成功（非错误）。 */
  async function killSolidWorks() {
    try {
      return await new Promise((resolve) => {
        let out = '';
        // 用顶部已 import 的 spawnSync（ESM 无 require）
        try {
          const r = spawnSync('taskkill', ['/F', '/IM', 'SLDWORKS.exe'],
                              { encoding: 'utf8', windowsHide: true, timeout: 15000 });
          out = String((r && r.stdout) || '') + String((r && r.stderr) || '');
          const code = r && typeof r.status === 'number' ? r.status : -1;
          const notFound = /not found|没有找到|No tasks|INFO:/i.test(out);
          if (code === 0) return resolve({ ok: true, method: 'taskkill', code });
          if (notFound || code === 128) {
            return resolve({ ok: true, method: 'not_running', code,
                             note: 'SolidWorks 未在运行' });
          }
          return resolve({ ok: false, method: 'taskkill_failed', code,
                           output: out.slice(0, 400) });
        } catch (e) {
          return resolve({ ok: false, method: 'exception',
                           error: String((e && e.message) || e) });
        }
      });
    } catch (e) {
      return { ok: false, method: 'exception', error: String((e && e.message) || e) };
    }
  }

  /** 启动（或复用）看护 job。返回 job id 与当前状态。 */
  function ensureSwGuardJob() {
    const jobs = (() => { try { return ctx.get ? ctx.get('jobs') : undefined; } catch (e) { return undefined; } })();
    if (!jobs || typeof jobs.start !== 'function') {
      // ★ 官方契约：缺 jobs 服务时无法启动后台任务（asar 1036382 同款判定）。
      //   这里**显式降级**，不假装成功；OS 子进程那条路（mode_gate）仍在工作。
      return {
        ok: false, job_id: null, degraded: true,
        error: 'background jobs unavailable: 需要加载 @deepseek-ai/dsh-jobs 与 '
             + '@deepseek-ai/dsh-tool-jobs（ctx.get("jobs") 为空）。',
        fallback: ('已自动降级：sw_bridge 取锁时拉起的 OS 子进程看护 '
                   + '（mode_gate.py sw-guard-status）仍在工作，'
                   + 'CPU/内存超 85% 同样会告警并关闭 SolidWorks。'),
        interval_ms: SW_GUARD_INTERVAL_MS,
        cpu_limit: SW_GUARD_CPU_LIMIT, mem_limit: SW_GUARD_MEM_LIMIT,
      };
    }
    if (swGuardJobId) {
      return { ok: true, job_id: swGuardJobId, already_running: true,
               interval_ms: SW_GUARD_INTERVAL_MS,
               cpu_limit: SW_GUARD_CPU_LIMIT, mem_limit: SW_GUARD_MEM_LIMIT,
               state: swGuardState };
    }
    swGuardState = { ticks: 0, alerts: [], killed: false,
                     startedAt: new Date().toISOString(), last: null };
    let timer = null;
    let stopped = false;
    const jobId = jobs.start({
      kind: 'command',
      label: 'SW 健康看护（每 10 秒采 CPU/内存，超 85% 关闭 SolidWorks）',
      // owner 用本插件名：它是"拥有这个后台任务"的实体标识
      owner: 'dsh-engineering-ui',
      run: () => {
        // ★ 契约要求：run 必须【立即】返回 {cancel, done}，
        //   长任务放进 done 的 Promise 里，这样 jobs.start 才能马上给出 job id。
        const done = (async () => {
          const tick = async () => {
            if (stopped) return;
            const s = sampleSystem();
            const v = judgeSystem(s);
            swGuardState.ticks++;
            swGuardState.last = { at: new Date().toISOString(), ...s,
                                  breach: v.breach, reasons: v.reasons };
            if (v.breach) {
              const line = '[sw-guard][ALERT] ' + new Date().toISOString() + ' '
                + v.reasons.join('；') + ' → 立即关闭 SolidWorks';
              // 用户要求："一旦内存、CPU超85％阈值就输出一行警报"
              try { console.warn(line); } catch (e) {}
              let kill = { ok: true, method: 'skipped' };
              kill = await killSolidWorks();
              if (kill.ok && kill.method === 'taskkill') swGuardState.killed = true;
              const rec = { at: new Date().toISOString(), message: line, reasons: v.reasons, kill };
              swGuardState.alerts.push(rec);
              if (swGuardState.alerts.length > 50) {
                swGuardState.alerts = swGuardState.alerts.slice(-50);
              }
              // SW 真的被杀掉 → 看护目的已达成，结束任务
              if (swGuardState.killed) { stopped = true; return 'killed'; }
            }
          };
          await tick();
          if (stopped) return 'killed';
          return await new Promise((resolve) => {
            timer = setInterval(async () => {
              try {
                const r = await tick();
                if (r === 'killed' || stopped) {
                  clearInterval(timer); timer = null;
                  resolve('killed');
                }
              } catch (e) { /* 单次失败不终止看护 */ }
            }, SW_GUARD_INTERVAL_MS);
            // 定时器不应阻止进程退出
            if (timer && typeof timer.unref === 'function') timer.unref();
          });
        })();
        return {
          cancel: (reason) => {
            stopped = true;
            if (timer) { try { clearInterval(timer); } catch (e) {} timer = null; }
            swGuardState.stoppedReason = String(reason || 'cancelled');
          },
          done,
        };
      },
    });
    swGuardJobId = jobId;
    return { ok: true, job_id: jobId, started: true,
             interval_ms: SW_GUARD_INTERVAL_MS,
             cpu_limit: SW_GUARD_CPU_LIMIT, mem_limit: SW_GUARD_MEM_LIMIT,
             note: ('SW 健康看护已作为 DSH 后台任务启动（job id=' + jobId + '）：'
                    + '每 ' + (SW_GUARD_INTERVAL_MS / 1000) + ' 秒采样，'
                    + 'CPU/内存超 ' + SW_GUARD_CPU_LIMIT + '% 即告警并立即关闭 SolidWorks。') };
  }

  // ══ 【第4项】SW 健康看护（ctx.jobs 版）路由 ═══════════════════════════════
  // POST /dsh-engineering-ui/sw-guard-job  启动（幂等）
  // GET  /dsh-engineering-ui/sw-guard-job  查状态（不含 job id 时只读状态）
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/sw-guard-job',
    handler: async (req, res) => {
      try {
        const method = String((req && req.method) || 'GET').toUpperCase();
        if (method === 'GET') {
          return send(res, 200, { ok: true, job_id: swGuardJobId,
                                  interval_ms: SW_GUARD_INTERVAL_MS,
                                  cpu_limit: SW_GUARD_CPU_LIMIT,
                                  mem_limit: SW_GUARD_MEM_LIMIT,
                                  state: swGuardState });
        }
        const r = ensureSwGuardJob();
        return send(res, 200, r);
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

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
  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

  // ══ 【Bug-21 修复】房间通知通道（只记录，不打断正在建模的小屋）══════════
  // 原缺陷：主对话用 send_message 给 4 个正在 SW 建模的小屋发通知
  //   （"旧产物已删除，继续设计"）。消息虽然 delivered，但小屋要等当前回合
  //   结束才收到；长建模脚本期间收不到；收到后还会【切换上下文】处理通知，
  //   有打断建模流程的风险。
  // 修复：提供 /notify-room 只读端点，把通知写入房间的 reports/ 目录下的
  //   notify 文件（与 mode_gate 的 reports 同构），小屋在【自愿的检查点】
  //   读取即可，不强制打断。纯通知类消息不再走对话通道。
  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

  // ── 原始事件流（不做字段映射，client 半负责解析）──────────────────────────
  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

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
  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

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

  disposers.push(ctx.webServer.register(guardRoute({
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
      // ══ 【卡死修复 A · 2026-11-08】用户点了提交，卡片却永远不消失 ═══════════
      // 现场（session-55a2009b）：用户选完点提交后，界面停在选项页动不了，
      //   子代理 ask_user.py 一直轮询"用户尚未作答"，最后用户只能强关 DSH。
      //
      // 根因：本端点有两套"等待"，但只有一套被接住。
      //   ① settleResolve/settledPromise —— 插件自建的 Promise，
      //      **没有任何人 await 它**；
      //   ② uq.ask(...) —— 真正挂起、真正驱动原生选项卡的那个 Promise。
      //   /answer 端点执行的是 entry.resolve() → 只 resolve ①，
      //   而 ② 的原生挂起无人理会 → 选项卡永远等不到 settle → 界面卡死。
      //
      // 修法：把②的 resolve 通道也挂到 entry 上（entry.nativeSettle），
      //   /answer 与原生作答两条路径都走"只 settle 一次"的闸门。

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
      //
      // ══ 【卡死修复 A】原生 ask() 必须可被 /answer 提前 settle ═══════════════
      //   原生 ask() 返回的是 ctx.waterfall(...) 的 Promise，它由**前端选项卡**
      //   决定何时 settle。但本插件自己的卡片（子代理面板里的 AskCard）走的是
      //   /answer → entry.resolve()，那条路径**碰不到**原生 Promise，
      //   于是"用户已经点了提交，选项卡却还挂着" —— 这正是本次卡死的直接现象。
      //   修法：把原生的 settle 也接进来（entry.nativeSettle / nativeReject），
      //   让 /answer 在 resolve 自建 Promise 的同时，也把原生那次挂起结掉。
      let nativeSettle = null;
      let nativeReject = null;
      entry.nativeSettle = (ans) => {
        try { if (typeof nativeSettle === 'function') nativeSettle(ans); } catch (e) {}
      };
      entry.nativeReject = (err) => {
        try { if (typeof nativeReject === 'function') nativeReject(err); } catch (e) {}
      };
      try {
        const _nativePromise = uq.ask({
          questions: questions.map((q) => ({
            id: String(q.id || 'q1'),
            question: String(q.question || ''),
            ...(q.header !== undefined ? { header: String(q.header) } : {}),
            ...(Array.isArray(q.options) ? { options: q.options } : {}),
            ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
          })),
          agent: holder
        });
        // 【卡死修复 A】拿到原生 Promise 的 resolve/reject —— 但不能 await，
        //   否则本端点会一直挂着（ask 本身就是阻塞式的）。
        if (_nativePromise && typeof _nativePromise.then === 'function') {
          Promise.resolve(_nativePromise).then((answer) => {
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
        }
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
  })));

  // 子代理面板查询：当前有哪些属于该子代理的待答问题
  disposers.push(ctx.webServer.register(guardRoute({
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
      // 允许 "*" = 全局查询（跨子代理会话去重用，见下方 byAll）
      if (rootSessionId && rootSessionId !== "*" && !ID_RE.test(rootSessionId)) {
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
        // ── 【2026-10-29 修复】rootSessionId=* 时返回【全部】未结提问 ──────
        // 现场事故（会话 ceac3fff）：三个总计子代理同时存活，各自用不同的
        //   childSessionId 提问同一道题；而旧去重只按 childSessionId 匹配，
        //   于是同一题被判成"三张新卡"，用户看到已答过的选项反复弹出。
        // 现在允许按 root 全局查，让 ask_user.py 能做【跨会话】去重。
        const byAll = rootSessionId === "*";
        if (byChild || byRoot || byAll) {
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
  })));

  // 【问题4 修复】已作答题目的结果留存表 —— 声明提前到 childAsks 旁，
  // 避免"使用在前、声明在后"的跨作用域引用（虽然闭包延迟执行不会报错，
  // 但提前声明让阅读顺序与执行顺序一致）。
  // 子代理 CLI（ask_user.py）靠它把答案读回来当作"工具返回值"。
  // 保留 10 分钟，够一次阻塞等待读取；过期自动清理。

  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

  // 定期清理过期结果（10 分钟）
  const _resultSweeper = setInterval(() => {
    const now = Date.now();
    for (const [pid, v] of childResults.entries()) {
      if (now - v.at > 10 * 60 * 1000) childResults.delete(pid);
    }
  }, 60 * 1000);
  if (_resultSweeper && typeof _resultSweeper.unref === 'function') _resultSweeper.unref();
  disposers.push(() => clearInterval(_resultSweeper));

  disposers.push(ctx.webServer.register(guardRoute({
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
          // ══ 【卡死修复 A】必须同时结掉【原生】那次挂起 ══════════════════════
          //   否则：插件自建 Promise 结束了，但原生选项卡（uq.ask）还挂着 →
          //   界面停在选项页、用户点提交没反应、只能强关 DSH（本次现场）。
          //   原生 settle 的期望形状是 { answers: [{id, selected}] }，
          //   与 entry.questions 的 id 口径一致，直接复用即可。
          if (isChild && typeof entry.nativeSettle === 'function') {
            try { entry.nativeSettle({ answers }); } catch (e) {}
          }
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
  })));

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

  // ══ 【FL-04 修复】请求体【长度 + 嵌套深度】上限 ══════════════════════════
  // 实测：5000 字符字符串、50 层深嵌套 JSON 都能 200 且签名成功 ——
  //   虽未触发异常，但缺少输入边界意味着超长/超深 payload 理论上可撑内存，
  //   而这几个端点的合法输入结构是【已知且很浅】的，没有理由接受任意形状。
  // 这里给出保守上限（远超正常用量，不会误伤真实调用）。
  const MAX_BODY_BYTES = 256 * 1024;      // 256 KB
  const MAX_DEPTH = 12;                   // 凭据体嵌套层级上限

  function jsonDepth(v, d = 0) {
    if (d > MAX_DEPTH) return d;
    if (v === null || typeof v !== 'object') return d;
    let m = d;
    if (Array.isArray(v)) {
      for (const it of v) { const x = jsonDepth(it, d + 1); if (x > m) m = x; }
    } else {
      for (const k of Object.keys(v)) {
        const x = jsonDepth(v[k], d + 1); if (x > m) m = x;
      }
    }
    return m;
  }

  /** 校验请求体规模；返回 null 表示通过，否则返回错误文案。 */
  function bodyLimitError(rawText, body) {
    try {
      if (typeof rawText === 'string' && rawText.length > MAX_BODY_BYTES) {
        return '请求体过大（' + rawText.length + ' > ' + MAX_BODY_BYTES + ' 字节）';
      }
      if (body && jsonDepth(body) > MAX_DEPTH) {
        return '请求体嵌套过深（> ' + MAX_DEPTH + ' 层）';
      }
    } catch (e) { /* 校验本身异常时不阻断（保守放行由签名兜底）*/ }
    return null;
  }

  // ══ 【FL-08 修复】防线端点【速率限制】════════════════════════════════════
  // 实测：100 次 verify 请求 0.81s 全部正常处理 —— 无任何速率约束。
  // 虽非漏洞（伪造凭据本就会被 HMAC 挡下），但无限速率意味着可以低成本
  //   反复触发 PowerShell 端口检查 / 文件读，白白消耗宿主资源。
  // 这里用【滑动窗口】限流：单端点每 10 秒最多 N 次，超出返回 429。
  //   阈值刻意放宽（正常门禁一次任务只调几十次），不会误伤真实流程。
  const RATE_WINDOW_MS = 10000;
  const RATE_MAX = 120;
  const _rateBuckets = new Map();

  function rateLimited(bucketKey) {
    try {
      const now = Date.now();
      let arr = _rateBuckets.get(bucketKey);
      if (!Array.isArray(arr)) { arr = []; _rateBuckets.set(bucketKey, arr); }
      // 丢弃窗口外的记录
      while (arr.length && (now - arr[0]) > RATE_WINDOW_MS) arr.shift();
      if (arr.length >= RATE_MAX) return true;
      arr.push(now);
      // 防无界增长：桶数过多时清理空桶
      if (_rateBuckets.size > 64) {
        for (const [k, v] of _rateBuckets) {
          if (!v || !v.length || (now - v[v.length - 1]) > RATE_WINDOW_MS) {
            _rateBuckets.delete(k);
          }
        }
      }
      return false;
    } catch (e) { return false; }
  }

  disposers.push(ctx.webServer.register(guardRoute({
    kind: "exact",
    path: PREFIX + "/defense/sign",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      // ── 【FL-08 修复】速率限制（滑动窗口，防无成本反复触发）────────────
      if (rateLimited('defense/sign')) {
        return send(res, 429, { ok: false, error: '请求过于频繁（防线签发端点已限流，请稍后重试）' });
      }
      const _raw = await readBody(req);
      // ── 【FL-04 修复】请求体长度上限 ─────────────────────────────────
      let body = null;
      try { body = JSON.parse(_raw); } catch (e) { body = null; }
      if (!body || typeof body !== "object") return send(res, 400, { ok: false, error: "bad json" });
      const _lim = bodyLimitError(_raw, body);
      if (_lim) return send(res, 413, { ok: false, error: _lim });
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
          // ── 【新 Bug 修复】纳入【可追溯标识】────────────────────────────
          // 校验端要求 run_id 或 checked_at 至少一个存在，否则判
          //   "缺少可追溯标识（疑似伪造）"→ CRITICAL。原先宿主不签这两个
          //   字段，而 Python 侧又【不能】在签名后补（会让 HMAC 失配），
          //   结果每份领域凭据都自带一条防伪造违规。
          // 现在：由签发调用方提供（Python 已在 payload 里带上），
          //   宿主原样纳入待签体；缺失时用宿主自己的签发时间兜底，
          //   保证凭据一定可追溯。
          run_id: (body.run_id === undefined || body.run_id === null)
            ? "" : String(body.run_id),
          checked_at: (body.checked_at === undefined || body.checked_at === null
                       || String(body.checked_at).trim() === "")
            ? new Date().toISOString() : String(body.checked_at),
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
          // ── 【Bug-04 修复】把"用户请求的材料"纳入待签体 ────────────────
          // 校验端需要在防线①对比 requested_material 与 applied/attested，
          //   发现跨材质静默替代（PETG→PET，σy 差约 37%）时拦截。
          //   这两个字段由 swapi 在申请签发时提交、宿主原样签名，
          //   之后任何一方都无法静默篡改（否则验签失配）。
          requested_material: body.requested_material === undefined
            ? null : String(body.requested_material),
          attested_material_name: body.attested_material_name === undefined
            ? null : String(body.attested_material_name),
          // ── 【Bug-C 修复】跨标准体系替代标记也纳入待签体 ──────────────
          // Q235(GB) → AISI 1020(ASTM) 这类替代原先只看 approximate_match，
          //   报告显得像"精确匹配"。签名后不可篡改，防线据此拦截。
          cross_standard: !!body.cross_standard,
          cross_standard_note: body.cross_standard_note === undefined
            ? null : String(body.cross_standard_note),
          // ── 【Bug-C 运行时修复】属性指纹纳入待签体 ────────────────────
          // 防线①靠"请求材料 vs 实际材料"的属性差异识别名字看不出、密度差
          //   也小的替代（6061-T6→7075 屈服差 82%，密度仅差 4.1%）。
          //   签名后不可篡改，判据才可信。
          expected_yield_mpa: typeof body.expected_yield_mpa === "number"
            ? body.expected_yield_mpa : null,
          expected_density_kg_m3: typeof body.expected_density_kg_m3 === "number"
            ? body.expected_density_kg_m3 : null,
          applied_yield_mpa: typeof body.applied_yield_mpa === "number"
            ? body.applied_yield_mpa : null,
          applied_density_kg_m3: typeof body.applied_density_kg_m3 === "number"
            ? body.applied_density_kg_m3 : null,
          yield_deviation_pct: typeof body.yield_deviation_pct === "number"
            ? body.yield_deviation_pct : null,
          // == [A-48 修复] 几何指纹纳入待签体 ========================
          // 原待签体只含材料事实，防线①因此对【零件几何】零约束：
          //   实测（2026-11-01）外径 φ160（规范 φ200）、体积缺 35% 的
          //   旧草稿凭"材料凭据 ok"直通总装。
          // 现在把几何事实一并签名。注意：这些是【事实陈述】而非判定结论，
          //   真实值仍由宿主的 md5 绑定保护（part_md5 覆盖整个文件内容，
          //   改几何必然改变 md5 → 签名与文件绑定仍然成立）。
          // 校验端（defense_gate.check_material_attestation）据此与
          //   load_case 的 design_domain / expected_volume_mm3 比对。
          part_bbox_mm: Array.isArray(body.part_bbox_mm)
            ? body.part_bbox_mm.map((x) => (typeof x === "number" ? x : null))
            : null,
          part_dims_mm: Array.isArray(body.part_dims_mm)
            ? body.part_dims_mm.map((x) => (typeof x === "number" ? x : null))
            : null,
          part_volume_mm3: typeof body.part_volume_mm3 === "number"
            ? body.part_volume_mm3 : null,
          part_body_count: typeof body.part_body_count === "number"
            ? body.part_body_count : null,
        };
      } else {
        return send(res, 400, { ok: false, error: "unknown kind: " + kind });
      }
      const signed = defenseSigner.sign(payload);
      if (!signed) return send(res, 200, { ok: false, error: "签名失败（密钥未就绪）" });
      return send(res, 200, { ok: true, credential: { ...payload, ...signed } });
    }
  })));

  disposers.push(ctx.webServer.register(guardRoute({
    kind: "exact",
    path: PREFIX + "/defense/verify",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配" });
      // 【FL-08】限流：防止以极低成本反复触发验签/文件读
      if (rateLimited('defense/verify')) {
        return send(res, 429, { ok: false, error: '请求过于频繁（验签端点已限流）' });
      }
      const _raw = await readBody(req);
      let cred = null;
      try { cred = JSON.parse(_raw); } catch (e) { cred = null; }
      if (!cred || typeof cred !== "object") return send(res, 400, { ok: false, error: "bad json" });
      // 【FL-04】请求体规模上限
      const _lim = bodyLimitError(_raw, cred);
      if (_lim) return send(res, 413, { ok: false, error: _lim });
      // ── 【P2 修复】公开验签端点【单次消费 nonce】，防重放 ──────────────
      // 实测（子代理3）：同一份凭据原样重放 3 次，全部 verified=true。
      //   根因是 verify() 只核对"nonce 由本宿主签发"，从不记录"已被消费"。
      // 现在：本端点 consume=true —— 同一 nonce 第二次出现即判重放并拒绝。
      // 注意：门禁自身需要对同一凭据做多阶段校验（room-end →
      //   confirm-assembly → 收尾），那是受信任的内部重复调用，因此走
      //   /defense/verify-gate（不消费）；公开端点与内部端点分离，
      //   既堵住外部重放，又不破坏门禁流程。
      const v = defenseSigner.verify(cred, true);
      return send(res, 200, { ok: !!v.ok, verified: !!v.ok, reason: v.reason || null,
                              kid: defenseSigner.kid, replay_protected: true });
    }
  })));

  // ── 门禁内部验签端点（不消费 nonce，允许同一凭据多阶段重复校验）────────
  disposers.push(ctx.webServer.register(guardRoute({
    kind: "exact",
    path: PREFIX + "/defense/verify-gate",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配" });
      // 【FL-08】限流（门禁一轮只调几十次，120/10s 不会误伤）
      if (rateLimited('defense/verify-gate')) {
        return send(res, 429, { ok: false, error: '请求过于频繁（门禁验签端点已限流）' });
      }
      const _raw = await readBody(req);
      let cred = null;
      try { cred = JSON.parse(_raw); } catch (e) { cred = null; }
      if (!cred || typeof cred !== "object") return send(res, 400, { ok: false, error: "bad json" });
      const _lim = bodyLimitError(_raw, cred);
      if (_lim) return send(res, 413, { ok: false, error: _lim });
      const v = defenseSigner.verify(cred, false);
      return send(res, 200, { ok: !!v.ok, verified: !!v.ok, reason: v.reason || null,
                              kid: defenseSigner.kid, replay_protected: false,
                              scope: "gate-internal（供门禁多阶段校验，不消费 nonce）" });
    }
  })));

  disposers.push(ctx.webServer.register(guardRoute({
    kind: "exact",
    path: PREFIX + "/defense/info",
    handler: async (req, res) => {
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      return send(res, 200, defenseSigner.status());
    }
  })));

  // ── /defense/judge：宿主侧执行完整防线判定（C 方案核心） ────────────
  // 判定权收回宿主：Python 侧不再自行组合"验签 + 规则"，而是把房间上报，
  //   由宿主读凭据、验签、判定并返回结论。这样 AI 在 Python 侧没有可冒充
  //   的判定点（它若伪造判定结果，等于篡改 defense_gate 本身，属另一类攻击，
  //   且守约会记录凭据异常）。
  disposers.push(ctx.webServer.register(guardRoute({
    kind: "exact",
    path: PREFIX + "/defense/judge",
    handler: async (req, res) => {
      if (req.method !== "POST") return send(res, 405, { ok: false, error: "POST only" });
      if (!defenseSigner) return send(res, 200, { ok: false, error: "防线签名服务未装配（仅工程模式）" });
      // 【FL-08】限流
      if (rateLimited('defense/judge')) {
        return send(res, 429, { ok: false, error: '请求过于频繁（防线判定端点已限流）' });
      }
      const _raw = await readBody(req);
      let body = null;
      try { body = JSON.parse(_raw); } catch (e) { body = null; }
      if (!body || typeof body !== "object") return send(res, 400, { ok: false, error: "bad json" });
      // 【FL-04】请求体规模上限
      const _lim = bodyLimitError(_raw, body);
      if (_lim) return send(res, 413, { ok: false, error: _lim });
      const room = String(body.room || "").trim();
      if (!ROOM_RE.test(room)) return send(res, 400, { ok: false, error: "bad room" });
      // 【本次修复·Bug⑤】把 task_id 透传给判定器 —— 凭据实际在
      //   reports/<task_id>/ 子目录里，不带这个参数宿主只能读 flat 根，
      //   于是"凭据明明存在"却被判 missing（room-end 永久被拒）。
      const result = defenseSigner.judgeDefense(room, String(body.kind || "room"), {
        requirePhysics: body.require_physics !== false,
        taskId: String(body.task_id || body.taskId || "").trim(),
        rooms: Array.isArray(body.rooms) ? body.rooms : undefined,
      });
      return send(res, 200, result);
    }
  })));

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
        abaqus: { connected: false, running: null, checked_at: null },
        webots: { connected: false, running: null, checked_at: null },
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
      // ── 【连接区扩展】把 Abaqus / Webots 也透给前端 ───────────────────
      //   EngConnRow 按 state[target] 读状态；缺了这两项，新增的两行按钮
      //   会永远停在"尚未检测过"。
      //   【NX → Webots 改造】原 nx 字段已被 webots 取代；为兼容【旧状态文件】
      //   （里面可能还写着 nx），这里做一次只读回退映射：读不到 webots
      //   就试着读 nx，让升级瞬间不至于显示成"从未检测"。
      abaqus: j.abaqus || { connected: false, running: null, checked_at: null },
      webots: j.webots || j.nx || { connected: false, running: null, checked_at: null },
      paths: j.paths || {},
    };
  }

  // ══════════════════════════════════════════════════════════════════════
  //  【换机事故修复】Python 解释器解析：必须校验【版本 >= 3.6】
  // ══════════════════════════════════════════════════════════════════════
  // 微机房实测（用户提供的现场结论）：
  //   · 默认 python 指向 Python 3.5.3 (32-bit)；
  //   · sw_bridge.py 用了 f-string（3.6+），直接 SyntaxError；
  //   · stderr 有错误信息、stdout 没有任何 JSON →
  //     插件解析失败 → 界面表现成「某些设置静默失效」。
  // 旧实现只跑 python --version 看 exitCode==0 就认它 —— 3.5 也会返回 0，
  //   于是永远挑中那个跑不起来的解释器。
  //
  // 新实现（三重）：
  //   ① 顺序改为 py 在前：Windows 的 py.exe 是官方启动器，会挑最新 3.x；
  //   ② 逐候选跑【真探针】（打印版本 + 能否 import pywin32），
  //      >=3.6 才采纳 —— 只认「真的能跑」，不认「存在」；
  //   ③ 结果缓存 5 分钟；详情模式（settings 页环境检查）不吃缓存，
  //      并把被拒的候选一并返回，直接告诉用户「为什么没用那个 python」。
  const PYTHON_MIN_MAJOR = 3;
  const PYTHON_MIN_MINOR = 6;
  let pyCache = null, pyCacheAt = 0;
  const PY_CACHE_MS = 5 * 60 * 1000;

  /** 把 '3.13.15' 解析成 [3,13]；解析不了返回 null。 */
  function parsePyVersion(v) {
    const parts = String(v || '').trim().split('.');
    if (parts.length < 2) return null;
    const a = Number(parts[0]), b = Number(parts[1]);
    if (!isFinite(a) || !isFinite(b)) return null;
    return [a, b];
  }

  /** py.exe 需要显式 -3 才是 Python 3。 */
  function pyArgsFor(c) {
    return c === 'py' ? ['-3'] : [];
  }

  /** 用某解释器跑一次探针，返回 {exe, version, ok, pywin32, error}。 */
  function probePythonExe(c) {
    const pre = [c].concat(pyArgsFor(c));
    let version = null, error = null, pywin32 = null;
    try {
      const r = spawnSync(pre[0], pre.slice(1).concat(['-c',
        'import sys;print("%d.%d.%d" % sys.version_info[:3])']),
        { encoding: 'utf8', timeout: 15000, windowsHide: true, env: pyEnv() });
      if (r && r.status === 0) {
        version = String((r.stdout || '')).trim() || null;
      } else {
        error = 'exit=' + (r && r.status) + ' ' +
          String(((r && r.stderr) || '') + ((r && r.stdout) || '')).trim().slice(0, 200);
      }
    } catch (e) {
      error = String((e && e.message) || e);
    }
    const ver = parsePyVersion(version);
    const okVer = !!ver && (ver[0] > PYTHON_MIN_MAJOR ||
      (ver[0] === PYTHON_MIN_MAJOR && ver[1] >= PYTHON_MIN_MINOR));
    if (okVer) {
      try {
        const r2 = spawnSync(pre[0], pre.slice(1).concat(['-c',
          'import win32com.client;print("ok")']),
          { encoding: 'utf8', timeout: 15000, windowsHide: true, env: pyEnv() });
        pywin32 = !!(r2 && r2.status === 0);
      } catch (e) { pywin32 = false; }
    }
    return { exe: c, version, ok: okVer, pywin32, error };
  }

  // ══════════════════════════════════════════════════════════════════════
  //  【中文乱码修复】所有 Python 子进程必须强制 UTF-8 输出
  // ══════════════════════════════════════════════════════════════════════
  // 实测（本机 Windows + 中文系统）：env_check.py / settings.py 用
  //   ensure_ascii=False 打印中文，Python 默认按【系统 ANSI 代码页(GBK)】
  //   写 stdout，而 Node 按 UTF-8 解码 → 设置页上整段中文变成乱码。
  // 修法：给子进程注入 PYTHONIOENCODING=utf-8 / PYTHONUTF8=1，
  //   让 Python 自己就用 UTF-8 输出（与 Python 侧 sys.stdout.reconfigure
  //   双保险；后者在旧版本 Python 上可能不可用）。
  function pyEnv() {
    const env = Object.assign({}, process.env);
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONUTF8 = '1';
    return env;
  }

  /** 候选解释器顺序：本机设置 > DSH_PYTHON > py > python > python3。 */
  function pythonCandidates() {
    const out = [];
    try {
      const dir0 = findToolsDir();
      if (dir0) {
        const j = readJsonSafe(path.join(dir0, 'settings.json'));
        const e = j && j.paths && j.paths.python_exe;
        if (e && typeof e.value === 'string' && e.value) out.push(e.value);
      }
    } catch (e) { /* 读不到就当没设置 */ }
    if (process.env.DSH_PYTHON) out.push(String(process.env.DSH_PYTHON));
    out.push('py', 'python', 'python3');
    const seen = new Set();
    return out.filter((c) => {
      if (!c || typeof c !== 'string') return false;
      const k = c.toLowerCase();
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  /**
   * 找到【真的能跑】的 Python 解释器（>= 3.6）。
   * @param {boolean} detail true = 详情模式（每次真探测、返回全部候选，供环境检查页）
   * @returns {string|null|object} 详情模式返回 {chosen, tried}
   */
  function resolvePython(detail) {
    const cands = pythonCandidates();
    if (detail) {
      const tried = cands.map(probePythonExe);
      const hit = tried.filter((t) => t.ok)[0] || null;
      return { chosen: hit ? hit.exe : null, tried };
    }
    if (pyCache !== null && (Date.now() - pyCacheAt) < PY_CACHE_MS) return pyCache;
    pyCache = null;
    for (const c of cands) {
      if (probePythonExe(c).ok) { pyCache = c; break; }
    }
    pyCacheAt = Date.now();
    return pyCache;
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
    // ── 【连接区扩展】任何具体目标都要走【单目标探测】───────────────
    //   原实现只对 sw/cad 传 --target，abaqus/webots 会退化成“探测全部”，
    //   于是点『Webots连接』会把 SolidWorks 的 COM Dispatch 也带着跑一遍
    //   （慢、且可能把 SW 窗口拉到前台）。
    if (target) args.push('--target', target);
    try {
      const r = spawnSync(py, args, {
        encoding: 'utf8', timeout: 120000, cwd: dir,
        windowsHide: true, env: pyEnv(),
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

  // ══ 【工程环境检查】设置页「连接区」上方的新栏目（用户需求 3）═══════════
  // 用户原话：
  //   「在设置 → 工程模式中，连接区上面再加一个『检查电脑是否有对应的环境』，
  //    然后写一段提示词自己发给新的对话，新的 Agent 对话就自己去查看看少啥，
  //    自己安装。」
  //
  // 落点设计（为什么不放在 Python 侧只放 CLI）：
  //   设置页是浏览器，读不到 Python 的 stdout；必须由宿主把它转成 HTTP。
  //   但**判定逻辑一行都不在 JS 里** —— 全部委托给 tools/env_check.py，
  //   保证「设置页看到的」和「Agent 跑出来的」是同一套结论（同一个事实源）。
  function runEnvCheck(args, timeoutMs) {
    const dir = findToolsDir();
    if (!dir) return { ok: false, error: '未定位到 tools 目录' };
    const script = path.join(dir, 'env_check.py');
    if (!fs.existsSync(script)) {
      return { ok: false, error: '未找到 env_check.py: ' + script,
               hint: '请重新安装/同步工程模式（install-0.2.0.ps1 或 sync-to-dsh.ps1）' };
    }
    const py = resolvePython();
    if (!py) {
      // 这里必须给出【可操作】的结论：没有能跑的 Python，本身就是最重要的自检结果。
      const detail = resolvePython(true);
      return {
        ok: false,
        status: 'bad',
        error: '没有找到 Python 3.6+ 解释器',
        hint: 'PATH 里排在最前的 python 可能是 3.5 一类旧版本（不支持 f-string）。' +
              '请在设置页指定 python.exe，或安装 Python 3.11/3.12 并加入 PATH。',
        python: detail,
      };
    }
    try {
      const r = spawnSync(py, [script].concat(args || []), {
        encoding: 'utf8', timeout: timeoutMs || 120000, cwd: dir, windowsHide: true,
        env: pyEnv(),
      });
      const out = String((r && r.stdout) || '');
      const errOut = String((r && r.stderr) || '');
      const start = out.indexOf('{');
      let parsed = null;
      if (start >= 0) { try { parsed = JSON.parse(out.slice(start)); } catch (e) { parsed = null; } }
      if (!parsed) {
        return {
          ok: false, status: 'bad',
          error: '环境自检没有返回可解析的 JSON',
          stdout_tail: out.slice(-1200),
          stderr_tail: errOut.slice(-1200),
          hint: '典型原因：Python 版本过低导致 env_check.py 一起 SyntaxError。' +
                '请先确认 python -c "import sys;print(sys.version)" 能正常输出。',
        };
      }
      parsed.python_used = py;
      return parsed;
    } catch (e) {
      return { ok: false, status: 'bad',
               error: '环境自检执行异常: ' + String((e && e.message) || e) };
    }
  }

  // GET：跑一次完整自检（只读，不装任何东西）
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/env-check',
    handler: async (req, res) => {
      try {
        return send(res, 200, runEnvCheck(['probe'], 180000));
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  // GET：生成【可发给新对话】的修复提示词（--json 便于前端原样展示）
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/env-prompt',
    handler: async (req, res) => {
      try {
        const r = runEnvCheck(['prompt', '--json'], 180000);
        if (!r || r.ok === false || !r.prompt) {
          // 连 env_check 都跑不了时，也要给用户一段【能自救】的说明，不能空手
          const detail = resolvePython(true);
          return send(res, 200, {
            ok: false, error: (r && r.error) || '无法生成提示词',
            python: detail,
            hint: '请在设置页指定一个 Python 3.6+ 的 python.exe 后重试。',
          });
        }
        r.python_used = resolvePython();
        return send(res, 200, r);
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  /** 读/写本机设置（交付目录、各软件路径）—— 转发 tools/settings.py。 */
  function runSettings(args) {
    const dir = findToolsDir();
    if (!dir) return { ok: false, error: '未定位到 tools 目录' };
    const script = path.join(dir, 'settings.py');
    if (!fs.existsSync(script)) {
      return { ok: false, error: '未找到 settings.py: ' + script };
    }
    const py = resolvePython();
    if (!py) return { ok: false, error: '没有找到 Python 3.6+ 解释器' };
    try {
      const r = spawnSync(py, [script].concat(args), {
        encoding: 'utf8', timeout: 60000, cwd: dir, windowsHide: true,
        env: pyEnv(),
      });
      const out = String((r && r.stdout) || '');
      const start = out.indexOf('{');
      if (start >= 0) {
        try { return JSON.parse(out.slice(start)); } catch (e) { /* 落到下面 */ }
      }
      return { ok: false, error: '设置命令没有返回 JSON',
               stdout_tail: out.slice(-600),
               stderr_tail: String((r && r.stderr) || '').slice(-600) };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  // GET/POST：本机设置（交付目录、SW/CAD/python 路径）
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/settings',
    handler: async (req, res) => {
      if (req.method === 'GET') {
        try { return send(res, 200, runSettings(['report'])); }
        catch (error) { return send(res, 200, { ok: false, error: String((error && error.message) || error) }); }
      }
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'GET or POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const key = String((payload && payload.key) || '').trim();
      const value = String((payload && payload.value) || '').trim();
      // 白名单：只允许机器级路径设置，防止这条路由被用来改别的状态文件
      const ALLOWED = ['delivery_root', 'sw_exe', 'cad_exe', 'python_exe',
                       'abaqus_exe', 'webots_exe'];
      if (ALLOWED.indexOf(key) < 0) {
        return send(res, 400, { ok: false, error: '不允许的设置项: ' + key,
                                allowed: ALLOWED });
      }
      return send(res, 200, runSettings(['set', key, value]));
    }
  })));

  // ── 【Webots 仿真 · 只读接入】连接区「Webots连接」旁边那块报告/证据面板 ──────
  // 与 conn-state / conn-probe 同一套 guardRoute（loopback + Origin/Referer + 限流），
  //   不新造鉴权；四条 GET 路由：webots-context / -report / -evidence / -attribute。
  // ⚠️ 这里【只增加只读入口】，不启动 Webots、不申请 SW FIFO 锁、不改门禁状态。
  //   归属（task_id / room_id）逐请求由 workflow_state.json + mode_state.json 推导，
  //   绝不采信模型或前端的自报值；拿不到唯一活跃房间绑定就 403 CONTEXT_UNAVAILABLE。
  disposers.push(installWebots(ctx, {
    env: process.env,
    findToolsDir,
    presetOf: syncPresetOf,
    guardRoute,
    send,
    queryOf: (req) => {
      const u = new URL(String((req && req.url) || '/'), 'http://127.0.0.1');
      return u.searchParams;
    },
  }));

  // GET：只读状态（零副作用）—— 设置页打开时轮询用
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/conn-state',
    handler: async (req, res) => {
      try {
        return send(res, 200, readConnState());
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  // POST：触发探测（点「连接」按钮时才走这里）
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/conn-probe',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const body = await readJsonBody(req);
      const target = String((body && body.target) || '').trim().toLowerCase();
      // ── 【连接区扩展】白名单加入 abaqus / webots ───────────────────────
      // 连接区有「Abaqus连接」「Webots连接」两个同款按钮，它们的
      //   target 必须被放行，否则点「连接」会拿到 HTTP 400。
      //   （Python 侧 probe_target 亦已支持这两个目标。）
      // 【NX → Webots 改造】旧名 'nx' 也放行（Python 侧映射到 webots）。
      if (target !== 'sw' && target !== 'cad' && target !== 'all'
          && target !== 'abaqus' && target !== 'webots' && target !== 'nx') {
        return send(res, 400, {
          ok: false,
          error: "target 必须是 'sw' | 'cad' | 'abaqus' | 'webots' | 'all'" });
      }
      try {
        const r = runConnProbe(target === 'all' ? null : target);
        return send(res, 200, r);
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  // ── POST /conn-probe-process-only：【10 分钟自动刷新】纯进程层检测 ─────────
  // 用户诉求原文："10 分钟自动探测就是看是不是在运行的，没有的话那个 UI
  //   就熄灭，让用户到时候自己选择去设置连接啥的，就是只做检测就行了，
  //   关了 UI 就更新就行了，其他不用搞。"
  //
  // 与 /conn-probe 的【关键区别】：这条【绝不发 COM】。
  //   · conn-probe 会 Dispatch：SW 没开时可能把它【拉起来】，还可能把
  //     窗口置前/最大化，10 分钟一次会周期性打断用户；
  //   · 本路由只跑 Python 的 probe_process_only（纯 tasklist），
  //     进程没了 → connected=False（灯熄灭），进程换实例（PID 变化）→
  //     旧会话已死也置 False。用户想重连时自己点「连接」。
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/conn-probe-process-only',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const dir = findToolsDir();
      if (!dir) return send(res, 200, { ok: false, error: '未定位到 tools 目录' });
      const bridge = path.join(dir, 'sw_bridge.py');
      if (!fs.existsSync(bridge)) {
        return send(res, 200, { ok: false, error: '未找到 sw_bridge.py: ' + bridge });
      }
      const py = resolvePython();
      if (!py) return send(res, 200, { ok: false, error: '未找到可用的 Python 解释器' });
      try {
        // 超时给得比 conn-probe 短得多：纯 tasklist 检测是秒级的，
        //   卡住就说明环境有问题，不该拖住设置页。
        const r = spawnSync(py, [bridge, 'conn-probe-process-only'], {
          encoding: 'utf8', timeout: 30000, cwd: dir, windowsHide: true, env: pyEnv(),
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
            error: '自动检测未返回可解析 JSON',
            stdout_tail: out.slice(-800),
            stderr_tail: errOut.slice(-800),
          });
        }
        // 检测完成后回读文件，保证 UI 拿到的是落盘后的真相
        return send(res, 200, { ok: true, probe: parsed, state: readConnState() });
      } catch (error) {
        return send(res, 200, { ok: false, error: '自动检测异常: ' + String((error && error.message) || error) });
      }
    }
  })));

  // ── POST /conn-launch：显式启动 SW / CAD（「启动」按钮）──────────────────
  // 用户诉求："给俩个都搞一个按键，自动启动吧，不然一直点连接启动不了的，
  //   就是启动成不需要 SW 自己跳出来的那种。"
  // 与 /conn-probe 的区别：这个真的会拉起进程。因此【只由用户点击触发】，
  //   不做任何自动调用；且耗时较长（SW 冷启动可达数十秒），
  //   故给足超时（默认 120s + 余量）。
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/conn-launch',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      const body = await readJsonBody(req);
      const target = String((body && body.target) || '').trim().toLowerCase();
      // ── 【连接区扩展】Abaqus / Webots 也允许「启动」───────────────────
      // 两者无 COM 接口，Python 侧 conn-launch 走"定位 exe → 拉起进程"，
      //   未安装时如实报错（不伪装成功）。
      // 【NX → Webots 改造】旧名 'nx' 一并放行（Python 侧映射到 webots）。
      if (target !== 'sw' && target !== 'cad'
          && target !== 'abaqus' && target !== 'webots' && target !== 'nx') {
        return send(res, 400, {
          ok: false,
          error: "target 必须是 'sw' | 'cad' | 'abaqus' | 'webots'" });
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
          encoding: 'utf8', timeout: 240000, cwd: dir, windowsHide: true, env: pyEnv(),
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
  })));

  // ── POST /conn-diagnose：把启动日志【当作消息发给当前会话的模型】────────
  // 用户诉求："加上一个按键，按了就可以将错误日志直接发给 DSH 内部模型的，
  //   然后 DSH 就可以根据错误日志去找到底是啥情况。"
  // 实现：包装成一条 user 消息并用 agent.steer() 投递（与收尾守卫同一机制），
  //   模型收到后会带着日志内容与工具能力去排查根因。
  disposers.push(ctx.webServer.register(guardRoute({
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
                                 { encoding: 'utf8', timeout: 60000, cwd: dir0, windowsHide: true, env: pyEnv() });
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

      const name = (target === 'cad') ? 'AutoCAD'
                 : (target === 'abaqus') ? 'Abaqus'
                 : (target === 'nx') ? 'NX' : 'SolidWorks';
      // ── 【缺陷修复】文案必须按【日志实际结果】生成，不能一律写"启动失败"──
      // 实测缺陷：日志里明明写着「结果: 成功」且四步全 [OK]（resolve_path /
      //   check_running / show_window / connect），但发给模型的消息标题仍是
      //   "SolidWorks 启动失败" —— 模型被错误前提带偏，去排查并不存在的故障。
      // 现在从日志文本里解析结果：
      //   · 成功 → 说明"启动成功"，若要诊断则围绕"真正想解决的问题"提问；
      //   · 失败 → 保留原排查指引（这才是该模式的本意）。
      const _okLine = /结果\s*[:：]\s*成功/.test(logText);
      const _failLine = /结果\s*[:：]\s*失败/.test(logText);
      const _isOk = _okLine && !_failLine;
      const text = _isOk
        ? [
            `【连接区诊断请求】${name} 启动【成功】，下面是完整日志与机器环境事实。`,
            '',
            '日志显示各步骤均已通过。如果你本来是想排查问题，请补充你实际遇到的现象；',
            '否则无需修复动作，可直接开始建模。',
            '',
            '可选的后续核查：运行 `sw_bridge.py doctor` / `sw-proc` 复核环境；',
            '若建模阶段仍报错，再带上具体报错继续诊断。',
            '',
            '```',
            logText,
            '```',
          ].join('\n')
        : [
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
  })));






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

  // ══ 【模型自动接替】列出宿主当前可用的模型（GET /model-catalog）══════
  // 为什么需要它（用户反馈 2026-10-30）：
  //   "这我也看不到是啥模型啊，而且你起码给我一个可以有模型表的模型让我选择吧"
  //   —— 原版只能手打 provider / model 字符串：用户既不知道有哪些可用，
  //      也看不清自己填了什么（还撞上深色主题下输入框黑底黑字的问题）。
  // 数据来源（全部取自宿主内存里的真实注册信息，不猜）：
  //   ① ctx.llm.listProviders()                  → 已注册的 provider 路由
  //   ② 每个 provider 的 adapter.listModels(pr)  → 该路由能广告的模型
  //   ③ ctx.llm.listConfigurableProviders()      → 配置目录（含未激活）
  //   ④ 并入【当前链里已保存的模型】—— 即便 catalog 没广告，也不能让
  //      用户已保存的选择从列表里消失（否则一保存就被覆盖成空）。
  // 任一来源失败都不抛错：宁可列表少几项，也要保证设置页能打开。
  /**
   * 列出宿主当前真实可路由的模型目录 —— 与官方 GUI 用【完全同一套 API】。
   *
   * ── 为什么第一版只列出 2 家、模型不全（用户反馈 2026-10-30）───────────
   *  v1 走的是 `llm.adapters.get(pid).adapter.listModels(provider)`：
   *    · 那个 adapters Map 只含"当前进程注册过"的少数路由；
   *    · adapter.listModels 的入参是"provider 路由对象"而非 id，签名对不上；
   *    · 更要命的是它【不走 resolveModelInfo】，拿不到官方展示用的 name。
   *  结果：provider 只有 buddy / raccoon 两家，模型也缺。
   *
   * ── 现在对齐官方（dsh-api-session-controller: buildModelCatalog）────
   *  · ctx.llm.listProviders()            → 全部 provider（含 {id, name}）
   *  · ctx.llm.listModels(provider.id)    → 该 provider 的模型（{id, name, ...}）
   *  · ctx.llm.resolveModelInfo(p, id)    → 模型元信息（含 reasoning 档位）
   *  这样设置页列出来的东西与聊天框底部那个模型选择器【完全一致】。
   *
   * ── 额外保留的兜底 ──────────────────────────────────────────────────
   *  · 某个 provider 抛错 → 只记 failure，其余照常（与官方一致）；
   *  · 当前 chain 里已保存但目录里没有的 → 补进来并标 fromChain，
   *    避免用户一保存就被"清空"；
   *  · llm 服务整体不可用 → 返回空目录 + errors，绝不抛错。
   *
   * @returns {Promise<{ok:boolean, providers:Array, groups:Array,
   *                    models:Array, failures:Array, sources:Array, errors:Array}>}
   */
  async function collectModelCatalog() {
    const out = {
      ok: true, providers: [], groups: [], models: [],
      failures: [], sources: [], errors: [],
    };
    const seen = new Set();
    const pushModel = (provider, model, extra) => {
      const p = String(provider || '').trim();
      const m = String(model || '').trim();
      if (!p || !m) return;
      const key = p + '\u0000' + m;
      if (seen.has(key)) return;
      seen.add(key);
      out.models.push(Object.assign({ provider: p, model: m }, extra || {}));
    };

    let llm = null;
    try {
      llm = (typeof ctx.get === 'function' ? ctx.get('llm') : null) || ctx.llm || null;
    } catch (e) { llm = null; }

    if (!llm || typeof llm.listProviders !== 'function' || typeof llm.listModels !== 'function') {
      out.ok = true;   // 目录失败不影响设置页打开
      out.errors.push('llm 服务不可用（listProviders / listModels 缺失）');
    } else {
      let providers = [];
      try {
        providers = llm.listProviders() || [];
      } catch (e) {
        out.errors.push('listProviders: ' + String((e && e.message) || e));
      }
      out.sources.push('listProviders=' + providers.length);

      // 与官方 buildModelCatalog 同构：每个 provider 独立 try/catch
      for (const pr of providers) {
        const pid = String((pr && (pr.id || pr.provider)) || '').trim();
        if (!pid) continue;
        const pname = String((pr && (pr.name || pr.label)) || pid);
        let models = [];
        try {
          models = await Promise.resolve(llm.listModels(pid) || []);
        } catch (e) {
          out.failures.push({ id: pid, name: pname,
            message: String((e && e.message) || e) });
          continue;
        }
        const entries = [];
        for (const m of models) {
          const mid = String((m && (m.id || m.model)) || '').trim();
          if (!mid) continue;
          let label = String((m && (m.name || m.label)) || mid);
          let desc = (m && m.description) || undefined;
          let reasoning = m && m.reasoning;
          // 与官方一致：再问一次元信息（拿 name / reasoning 档位）
          try {
            if (typeof llm.resolveModelInfo === 'function') {
              const info = await Promise.resolve(llm.resolveModelInfo(pid, mid));
              if (info) {
                if (info.name) label = String(info.name);
                if (info.description) desc = info.description;
                if (info.reasoning) reasoning = info.reasoning;
              }
            }
          } catch (e) { /* 元信息拿不到就用基础信息，不影响可选 */ }
          const entry = { id: mid, name: label };
          if (desc) entry.description = desc;
          if (reasoning) {
            entry.reasoning = {
              efforts: Array.isArray(reasoning.efforts) ? reasoning.efforts.map((x) => ({
                id: x.id, name: x.name || x.id,
                ...x.description === undefined ? {} : { description: x.description },
              })) : [],
              ...reasoning.defaultEffort === undefined ? {} : { defaultEffort: reasoning.defaultEffort },
            };
          }
          entries.push(entry);
          pushModel(pid, mid, { label, ...desc === undefined ? {} : { description: desc } });
        }
        if (entries.length) {
          out.groups.push({ id: pid, label: pname, models: entries });
        }
      }
    }

    // 兜底：当前链里已保存的模型一定列出（否则一保存就从 UI 消失）
    try {
      const cur = readModelChainConfig();
      for (const e of (cur.entries || [])) {
        const pid = String(e.provider || '').trim();
        const mid = String(e.model || '').trim();
        if (!pid || !mid) continue;
        pushModel(pid, mid, { fromChain: true });
        if (!out.groups.some((g) => g.id === pid)) {
          out.groups.push({ id: pid, label: pid + '（链外）',
            models: [{ id: mid, name: mid }] });
        }
      }
    } catch (e) {}

    out.groups.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    out.providers = out.groups.map((g) => ({ id: g.id, label: g.label }));
    if (!out.groups.length && !out.failures.length) {
      out.errors.push('宿主没有返回任何可用 provider（模型目录为空）');
    }
    return out;
  }


  // ══ 【模型自动接替】配置读写（GET/POST /model-chain）════════════════
  // 存 tools/model_chain.json：{ enabled: bool, models: [{provider,model,reasoningEffort?}] }
  //   · 与 persona.json 同一处（findToolsDir 解析的 tools 目录），
  //     保证 UI 与宿主读的是同一份真相；
  //   · 不参与签名/防线，属于用户配置，不是凭据。
  function modelChainPath() {
    const dir = findToolsDir();
    if (!dir) return null;
    return path.join(dir, MODEL_CHAIN_FILE);
  }
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/model-catalog',
    handler: async (req, res) => {
      if (req.method !== 'GET') return send(res, 405, { ok: false, error: 'GET only' });
      try {
        const cat = await collectModelCatalog();
        // collectModelCatalog 已按官方 buildModelCatalog 的结构给出 groups
        // （每个 model 带 name / description / reasoning），这里直接透传，
        // 只补一个给 UI 用的 total。
        return send(res, 200, {
          ok: true,
          total: cat.models.length,
          provider_count: cat.groups.length,
          groups: cat.groups,
          providers: cat.providers,
          models: cat.models,
          failures: cat.failures,
          sources: cat.sources,
          errors: cat.errors,
        });
      } catch (e) {
        return send(res, 200, {
          ok: false,
          error: String((e && e.message) || e),
          groups: [], providers: [],
        });
      }
    }
  })));

  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/model-chain',
    handler: async (req, res) => {
      if (req.method === 'GET') {
        const p = modelChainPath();
        const raw = p ? readJsonSafe(p) : null;
        const cfg = normalizeModelChain(raw);
        // ── 【2026-10-30】同时回传宿主当前默认模型 ────────────────────────
        // 用户反馈"模型显示没跟上"：链里第一条是 buddy/...，但设置页
        // 默认选的是 workbuddy/...，UI 摘要只显示链、不显示"实际在用哪个"，
        // 于是看起来像是"我选的没用上"。
        // 这里把宿主真实默认选择一并给出，UI 就能并排显示并提示不一致。
        let hostDefault = null;
        try {
          const adm = (typeof ctx.get === 'function' ? ctx.get('agentDefaultModel') : null)
            || ctx.agentDefaultModel;
          if (adm && typeof adm.currentSelection === 'function') {
            hostDefault = adm.currentSelection();
          }
        } catch (e) { hostDefault = null; }
        // 链首是否与宿主默认一致（不一致时前端给出提示）
        const head = cfg.entries[0] || null;
        const headMatchesDefault = !!(head && hostDefault
          && String(head.provider) === String(hostDefault.provider)
          && String(head.model) === String(hostDefault.model));
        return send(res, 200, {
          ok: true,
          enabled: cfg.enabled,
          models: cfg.entries,
          file: p,
          // 【2026-10-30 改版】不再限制数量（用户要求「不限制加多少个」）。
          // 这里仅给出建议下限，供 UI 决定初始显示几行。
          min_slots: 3,
          host_default: hostDefault,
          head_matches_default: headMatchesDefault,
          switch_codes: [...MODEL_CHAIN_SWITCH_CODES],
          note: ('接替只在前一个模型真的报额度/限流错误后才生效，'
                 + '正常情况下【不会】改掉你在设置页选的模型；'
                 + '本轮内不重复试同一个模型。'),
        });
      }
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'GET or POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const cfg = normalizeModelChain(payload);
      const p = modelChainPath();
      if (!p) {
        return send(res, 200, { ok: false, error: '无法定位工程模式 tools 目录，模型链未保存' });
      }
      try {
        // ── 不要整份覆盖 ──────────────────────────────────────────────────
        // 设置页只认识 enabled/models；同一个文件里还可能有用户手写的
        // autoResume / autoResumeMax / autoResumeDelayMs（0.2.2 自动续跑）。
        // 整份重写会把这些键吃掉 → 先读旧值再合并。
        const prevCfg = readJsonSafe(p) || {};
        const nextCfg = {
          ...prevCfg,
          enabled: cfg.enabled,
          models: cfg.entries,
          updated_at: new Date().toISOString(),
        };
        if (payload && payload.autoResume !== undefined) nextCfg.autoResume = cfg.autoResume;
        if (payload && payload.autoResumeMax !== undefined) nextCfg.autoResumeMax = cfg.autoResumeMax;
        if (payload && payload.autoResumeDelayMs !== undefined) {
          nextCfg.autoResumeDelayMs = cfg.autoResumeDelayMs;
        }
        fs.writeFileSync(p, JSON.stringify(nextCfg, null, 2), 'utf8');
        modelChainCache = null;          // 立刻失效，下一轮请求就用新配置
        modelChainState.clear();         // 链变了，旧进度作废
        return send(res, 200, { ok: true, enabled: cfg.enabled, models: cfg.entries, file: p });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));

  disposers.push(ctx.webServer.register(guardRoute({
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
  })));

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

  // ══ 【阶段 C · 状态可信化】平台子代理注册表（platform_agents.json）═══════
  // 背景（用户会话实测）：主对话分不清子代理死活 —— 它手动跑 while(true)
  //   platform-sync running 造假心跳，subagent-assign 从没调用，于是 recover 把
  //   还在跑的房间当"僵尸残留"，紧接着 reset 把三个正在建模的子代理全杀了。
  // 根因：存活状态靠主对话手写，而不是平台事实。
  //
  // 本登记器把【平台事实】自动落盘，供 mode_gate._platform_liveness() 读取：
  //   agent/created       → 记一条 {id,label,parentId,createdAt,status:'running'}
  //   agent/turn-stopping → 刷新 lastEventAt，status 仍为 'running'（回合结束≠死亡）
  //   agent/disposed      → status='disposed'（平台确认释放，mode_gate 才允许判死）
  //
  // 硬约束（任何一条破了都可能误杀正在建模的小屋）：
  //   · 只登记【子代理】（delegationDepth>0 或 有 parentId），绝不写主会话；
  //   · 拿不到字段就跳过（不猜、不报错）；
  //   · 原子写（tmp + rename）、读-改-写、节流 ≥300ms；
  //   · 任何异常一律吞掉 —— 登记器坏了也绝不能影响会话。
  const PLATFORM_AGENTS_NAME = 'platform_agents.json';
  // 本次 DSH 进程运行标识（崩溃/重启后必然变化 → 可据此判"整批失联"）
  const PLATFORM_RUN_ID = (() => {
    try {
      return 'run-' + process.pid + '-' + Date.now().toString(36);
    } catch (e) {
      return 'run-unknown';
    }
  })();
  const PLATFORM_RUN_STARTED_AT = new Date().toISOString();
  const PLATFORM_AGENTS_THROTTLE_MS = 300;
  const PLATFORM_AGENTS_KEEP = 200;
  let _paPending = new Map();     // id -> 待落盘记录
  let _paTimer = null;
  let _paLastWriteAt = 0;

  /** 注册表路径（找不到 tools 目录返回 null → 静默跳过）。 */
  function platformAgentsPath() {
    try {
      const dir = findToolsDir();
      if (!dir) return null;
      return path.join(dir, PLATFORM_AGENTS_NAME);
    } catch (e) {
      return null;
    }
  }

  /** 只认字符串/数字标签；函数、对象一律不猜（返回 null）。 */
  function paPickStr(v) {
    try {
      if (v === undefined || v === null) return null;
      if (typeof v === 'string') return v.trim() || null;
      if (typeof v === 'number') return String(v);
      return null;
    } catch (e) {
      return null;
    }
  }

  /** 读现有注册表（读不到返回 null，由调用方决定跳过还是新建）。 */
  function readPlatformAgentsFile() {
    try {
      const p = platformAgentsPath();
      if (!p) return null;
      const j = readJsonSafe(p);
      if (!j || typeof j !== 'object') {
        return { updatedAt: null, source: 'dsh-engineering-ui', agents: [] };
      }
      const agents = Array.isArray(j.agents)
        ? j.agents.filter((a) => a && a.id) : [];
      return {
        updatedAt: j.updatedAt || null,
        source: j.source || 'dsh-engineering-ui',
        // runId / runStartedAt 供 mode_gate 判"整批失联"（G04）
        runId: j.runId || null,
        runStartedAt: j.runStartedAt || null,
        agents,
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * 从 agent 对象抽取登记所需字段。
   * 只返回【子代理】的元信息；主会话或拿不到字段 → null（跳过）。
   * label 取值顺序：label / displayTitle / title / session.header.title / 兜底 id。
   * parentId 取值顺序：parentId / parent.id / header.parentId / header.parentSessionId。
   */
  function platformAgentMeta(agent) {
    try {
      if (!agent || !agent.id) return null;
      const header = (agent.session && agent.session.header) || {};
      const depthRaw = (typeof agent.delegationDepth === 'number')
        ? agent.delegationDepth
        : ((typeof agent.depth === 'number') ? agent.depth
          : ((typeof header.delegationDepth === 'number')
            ? header.delegationDepth : null));
      const parentId = agent.parentId
        || (agent.parent && agent.parent.id)
        || header.parentId
        || header.parentSessionId
        || agent.parentSessionId
        || null;
      const isSub = (depthRaw !== null && depthRaw > 0) || !!parentId;
      if (!isSub) return null;   // 主会话不登记
      const label = paPickStr(agent.label)
        || paPickStr(agent.displayTitle)
        || paPickStr(agent.title)
        || paPickStr(header.title)
        || paPickStr(agent.name)
        || String(agent.id);
      return {
        id: String(agent.id),
        label,
        parentId: parentId ? String(parentId) : null,
        createdAt: (agent.createdAt !== undefined && agent.createdAt !== null)
          ? agent.createdAt : new Date().toISOString(),
      };
    } catch (e) {
      return null;
    }
  }

  /** 该 id 是否已在注册表（文件或待写队列）里 —— 用于 disposed 时认领已知子代理。 */
  function platformAgentKnown(id) {
    try {
      const key = String(id);
      if (_paPending.has(key)) return true;
      const cur = readPlatformAgentsFile();
      if (!cur) return false;
      return (cur.agents || []).some((a) => String(a && a.id) === key);
    } catch (e) {
      return false;
    }
  }

  /** 读-改-写落盘（原子：先写 .tmp 再 rename；rename 失败退回直接写）。 */
  function flushPlatformAgents() {
    _paTimer = null;
    try {
      const p = platformAgentsPath();
      if (!p) {
        // 没有 tools 目录 → 静默跳过（丢弃待写队列，避免内存里无限堆积）
        _paPending = new Map();
        return;
      }
      const cur = readPlatformAgentsFile() || { agents: [] };
      const map = new Map();
      (cur.agents || []).forEach((a) => {
        if (a && a.id) map.set(String(a.id), a);
      });
      const pending = _paPending;
      _paPending = new Map();
      pending.forEach((rec, id) => {
        const old = map.get(String(id)) || {};
        const merged = Object.assign({}, old, rec);
        if (!merged.createdAt) merged.createdAt = new Date().toISOString();
        if (!merged.label) merged.label = String(id);
        if (!merged.status) merged.status = 'running';
        map.set(String(id), merged);
      });
      const agents = Array.from(map.values()).slice(-PLATFORM_AGENTS_KEEP);
      const payload = {
        updatedAt: new Date().toISOString(),
        source: 'dsh-engineering-ui',
        // ── 【2026-10-29 G04】本进程（= 本次 DSH 运行）的标识 ────────────
        // 用途：DSH 崩溃重启后，mode_gate 看到 registry.runId 与上次记录的不同，
        //   就能判定"上一轮 fork 的子代理全部失联"，而不是逐房间靠 id 去猜。
        runId: PLATFORM_RUN_ID,
        runStartedAt: PLATFORM_RUN_STARTED_AT,
        agents,
      };
      const text = JSON.stringify(payload, null, 2);
      const tmp = p + '.' + process.pid + '.tmp';
      let wrote = false;
      try {
        fs.writeFileSync(tmp, text, 'utf8');
        fs.renameSync(tmp, p);
        wrote = true;
      } catch (e) {
        // Windows：目标被轮询/杀软占用时 rename 可能失败 → 退回直接写
        try {
          fs.writeFileSync(p, text, 'utf8');
          wrote = true;
        } catch (e2) {}
        try { fs.unlinkSync(tmp); } catch (e3) {}
      }
      if (wrote) {
        _paLastWriteAt = Date.now();
      } else {
        // 写失败 → 把这批记录放回队列，等下一次事件重试（不丢状态）
        pending.forEach((rec, id) => {
          if (!_paPending.has(id)) _paPending.set(id, rec);
        });
      }
    } catch (e) {
      // 登记器异常绝不能影响会话
    }
  }

  /** 节流调度：≥300ms 内最多落盘一次，多次事件合并成一次写。 */
  function schedulePlatformAgentsFlush() {
    try {
      if (_paTimer) return;
      const elapsed = Date.now() - _paLastWriteAt;
      const wait = elapsed >= PLATFORM_AGENTS_THROTTLE_MS
        ? 0 : (PLATFORM_AGENTS_THROTTLE_MS - elapsed);
      _paTimer = setTimeout(() => {
        try { flushPlatformAgents(); } catch (e) {}
      }, wait);
    } catch (e) {}
  }

  /** 合并进待写队列并安排落盘（同 id 后续字段覆盖先前字段）。 */
  function stagePlatformAgent(rec) {
    try {
      if (!rec || !rec.id) return;
      const key = String(rec.id);
      const prev = _paPending.get(key) || {};
      _paPending.set(key, Object.assign({}, prev, rec));
      schedulePlatformAgentsFlush();
    } catch (e) {}
  }


  // ══════════════════════════════════════════════════════════════════════
  // 【2026-10-29 · 99% 可靠的存活判据】主进程级定时脉冲
  // ══════════════════════════════════════════════════════════════════════
  // 为什么之前的判活全都不可靠：
  //   · list_agents / wait_agent → fork 出来的子代理【不是 Team member】，
  //     永远看不到（会话 ceac3fff 就是因此误判死亡，连开 3 个）；
  //   · 子代理自己写心跳     → 它一忙（跑 SW、等锁、长命令）就停写；
  //   · 只靠 agent/created|disposed 事件 → 事件不来时无法区分"没事件"与"已死"。
  //
  // 本方案（核心思路）：心跳由【DSH 主进程的 setInterval】写，
  //   与子代理在干什么【完全无关】：
  //     - 子代理跑 SolidWorks、卡死、等锁、发呆 → 定时器照跳；
  //     - 只有 agent 被 disposed（真结束）→ 从表里移除；
  //     - 只有 DSH 整个进程没了 → 定时器停 → pulse 停止前进。
  //   而"DSH 进程没了"时【所有】子代理也都没了，此时判死是【正确】的，
  //   不会造成"误杀活子代理"。
  //
  // 两个信号缺一不可，组合起来才 99%：
  //   ① pulse.seq  单调递增 —— 证明"写它的那个进程还活着且在跳"；
  //   ② agents[] 里仍有该 id 且 status!=disposed —— 证明"该 agent 对象还在"。
  //   ① 且 ② → 子代理活着。任一不成立 → 才允许判死。
  const PULSE_INTERVAL_MS = 10000;      // 每 10 秒一跳
  const PULSE_STALE_MS = 35000;         // 超过 3.5 跳没更新 → 视为进程已停
  let _pulseSeq = 0;
  let _pulseTimer = null;
  /** 主进程内存里的 agent 台账：id -> 记录。只在 disposed 时移除。 */
  const _liveAgents = new Map();

  /** 把一个 agent 记进内存台账（created 时调用）。 */
  function pulseTrack(agent, meta) {
    try {
      if (!agent || !agent.id) return;
      const key = String(agent.id);
      const prev = _liveAgents.get(key) || {};
      _liveAgents.set(key, Object.assign({}, prev, meta || {}, {
        id: key,
        status: "running",
        trackedAt: prev.trackedAt || new Date().toISOString(),
      }));
    } catch (e) {}
  }

  /** 从内存台账移除（disposed 时调用）。 */
  function pulseUntrack(agentId) {
    try {
      if (agentId) _liveAgents.delete(String(agentId));
    } catch (e) {}
  }

  /** 打一拍：把 pulse + 全部存活 agent 落盘（原子写）。 */
  function pulseTick() {
    try {
      const p = platformAgentsPath();
      if (!p) return;
      _pulseSeq += 1;
      const nowIso = new Date().toISOString();
      const nowMs = Date.now();
      // 先读现有文件，保留历史里"已 disposed"的记录（审计用）
      const cur = readPlatformAgentsFile() || { agents: [] };
      const map = new Map();
      (cur.agents || []).forEach((a) => {
        if (a && a.id) map.set(String(a.id), a);
      });
      // 合并内存台账（这是权威：只有 disposed 才会被移除）
      _liveAgents.forEach((rec, id) => {
        const old = map.get(id) || {};
        map.set(id, Object.assign({}, old, rec, {
          status: "running",
          lastPulseAt: nowIso,
          lastPulseAtMs: nowMs,
        }));
      });
      const agents = Array.from(map.values()).slice(-PLATFORM_AGENTS_KEEP);
      const payload = {
        updatedAt: nowIso,
        source: "dsh-engineering-ui",
        runId: PLATFORM_RUN_ID,
        runStartedAt: PLATFORM_RUN_STARTED_AT,
        // ── 心跳脉冲：证明"写这份文件的进程还活着且在跳" ──
        pulse: {
          seq: _pulseSeq,
          at: nowIso,
          atMs: nowMs,
          intervalMs: PULSE_INTERVAL_MS,
          staleMs: PULSE_STALE_MS,
        },
        agents,
      };
      const text = JSON.stringify(payload, null, 2);
      const tmp = p + "." + process.pid + ".tmp";
      try {
        fs.writeFileSync(tmp, text, "utf8");
        fs.renameSync(tmp, p);
      } catch (e) {
        try { fs.writeFileSync(p, text, "utf8"); } catch (e2) {}
        try { fs.unlinkSync(tmp); } catch (e3) {}
      }
    } catch (e) {
      // 脉冲失败绝不能影响会话
    }
  }

  /** 启动脉冲定时器（幂等）。 */
  function pulseStart() {
    try {
      if (_pulseTimer) return;
      pulseTick();                       // 立刻打第一拍
      _pulseTimer = setInterval(pulseTick, PULSE_INTERVAL_MS);
      // Node 里定时器会阻止进程退出；unref 让它不拖住退出
      try { if (_pulseTimer.unref) _pulseTimer.unref(); } catch (e) {}
    } catch (e) {}
  }

  pulseStart();
  disposers.push(() => {
    try { if (_pulseTimer) clearInterval(_pulseTimer); } catch (e) {}
    _pulseTimer = null;
  });

  // ── 事件接线：本插件【只新增登记器】，不改任何既有路由/守卫行为 ──────────
  disposers.push(ctx.on('agent/created', (payload) => {
    try {
      const agent = payload && payload.agent;
      const meta = platformAgentMeta(agent);
      if (!meta) return;                       // 主会话 / 字段缺失 → 跳过
      // 记进主进程内存台账 → 之后由脉冲定时器持续续期（与子代理忙不忙无关）
      pulseTrack(agent, meta);
      stagePlatformAgent(Object.assign({}, meta, {
        status: 'running',
        lastEventAt: new Date().toISOString(),
        lastEventAtMs: Date.now(),
      }));
    } catch (e) {}
  }));

  // ══ 【B1】后台 job 未 settle 就报 completed → 父代理误判完成 ═══
  // 现场：:670 "Log is 0 bytes (buffered). Build still ru…" → :672 turn/end{completed}
  //   → :673 才是 job/settled；:1133 父代理据此当完成。
  // 修复：有未 settle 的 job 就不能当 completed —— 仍记 running，
  //   并写 pending_jobs，让父代理/看板看得见"它还没真收工"。
  // ── 【BUG-B1 修复·取数口径】────────────────────────────────────────────
  // 初版有两个会把结论带偏的取数错误（均已按宿主源码核对，见 dsh-jobs-local）：
  //
  //  ① **没有把 caller 传给 jobs.list()**。
  //     真实签名是 `list(caller)`，过滤条件是
  //         `job.owner === undefined || job.owner.id === caller`
  //     （dsh-jobs-local/lib/index.js:503-505）。
  //     不传 caller → 只拿回【无主】任务，该 agent 自己的 job 全都看不到。
  //     实测复刻：agent 自己有 j-own、别人有 j-other、另有一个无主 j-none 时，
  //       list()        → 只剩 j-none      （漏报自己的 → B1 完全失效）
  //       list('A')     → j-own + j-none   （对，但还得过滤掉无主的）
  //     这正是 B1 要修的那个现场（b55576ac 的建模 job）—— 漏报就等于没修。
  //
  //  ② **把无主任务算进每个 agent**。
  //     `owner === undefined` 的任务（DSH 自己的看护任务等）不属于任何会话，
  //     若把它算成"该 agent 还有活没干"，【所有】子代理都会被判 pending_jobs，
  //     父代理看到的是一律"未完成" —— 又一个"永远不放行"，比漏报更糟。
  //
  // 现在的口径（严格）：只认 `owner === agentId` 的 job；无主任务一律不算。
  //   owner 缺失时也不猜（拿不到归属就不拿它当证据）。
  // 状态判定同样对齐宿主：只有 running / stopping 算"未 settle"
  //   （宿主 isTerminal 判的是 completed / killed / failed；这里把 done/settled/
  //    cancelled 也覆盖上，兼容其它实现的叫法）。
  function pendingJobsOf(agentId) {
    try {
      const jobs = (ctx && typeof ctx.get === 'function') ? ctx.get('jobs') : undefined;
      if (!jobs || typeof jobs.list !== 'function') {
        return { available: false, pending: [], reason: 'jobs 服务不可用（ctx.get("jobs") 为空）' };
      }
      const id = String(agentId || '');
      if (!id) return { available: false, pending: [], reason: '缺少 agentId' };
      // ⚠️ list() 抛异常必须报 available=false，不能吞成 rows=[]。
      //   "读不到"和"确实没有"是两件事：吞成空数组 = 告诉父代理"它没在忙"，
      //   而那正是 B1 要防的误判方向。与 jobs 服务缺失同一处理。
      let rows;
      try {
        rows = jobs.list(id) || [];
      } catch (e) {
        return { available: false, pending: [],
                 reason: 'jobs.list 探测失败：' + String((e && e.message) || e) };
      }
      if (!Array.isArray(rows)) {
        return { available: false, pending: [], reason: 'jobs.list 返回非数组' };
      }
      const pending = [];
      for (const j of rows) {
        if (!j) continue;
        // 只认【归属本会话】的 job；无主（owner 缺失）不算 —— 理由见上
        if (String(j.owner || '') !== id) continue;
        const st = String(j.status || j.state || '').toLowerCase();
        // 只有"还在跑"才算未 settle；终态一律不算（与宿主 isTerminal 同口径）
        if (st !== 'running' && st !== 'stopping') continue;
        pending.push({ id: j.id || j.jobId || null,
                       label: j.label || j.name || null, status: st || null,
                       kind: j.kind || null });
      }
      return { available: true, pending, reason: null };
    } catch (e) {
      return { available: false, pending: [], reason: String((e && e.message) || e) };
    }
  }

  disposers.push(ctx.on('agent/turn-stopping', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      const meta = platformAgentMeta(agent);
      // 只有"已登记过的子代理"或"能证明是子代理"的才更新 —— 主会话不写
      if (!meta && !platformAgentKnown(agent.id)) return;
      // ── 【B1】先看有没有未 settle 的后台 job ────────────────────────────
      const pj = pendingJobsOf(agent.id);
      const rec = {
        id: String(agent.id),
        // 【关键】回合结束 ≠ 死亡：子代理仍然活着，禁止让 mode_gate 判死
        status: 'running',
        lastEventAt: new Date().toISOString(),
        lastEventAtMs: Date.now(),
      };
      if (pj.available) {
        rec.pending_jobs = pj.pending;
        rec.pending_job_count = pj.pending.length;
        rec.turn_completed_but_busy = pj.pending.length > 0;
      } else {
        rec.pending_jobs_unavailable = pj.reason || true;
      }
      if (meta) { rec.label = meta.label; rec.parentId = meta.parentId; }
      stagePlatformAgent(rec);
      if (pj.available && pj.pending.length) {
        try {
          console.warn('[dsh-engineering-ui] agent ' + rec.id + ' 回合结束但仍有 '
            + pj.pending.length + ' 个后台 job 未完成：'
            + pj.pending.map((j) => j.label || j.id || '?').join('、')
            + ' —— 不要据此判它"已完成"（B1）');
        } catch (e2) {}
      }
    } catch (e) {}
  }));

  disposers.push(ctx.on('agent/disposed', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      const meta = platformAgentMeta(agent);
      if (!meta && !platformAgentKnown(agent.id)) return;
      // 【关键】从内存台账移除 —— 脉冲不再为它续期，这是【唯一】判死来源
      pulseUntrack(agent.id);
      const rec = {
        id: String(agent.id),
        // 平台已确认释放 → mode_gate 才允许把它判成 dead（唯一判死来源）
        status: 'disposed',
        lastEventAt: new Date().toISOString(),
        lastEventAtMs: Date.now(),
      };
      if (meta) { rec.label = meta.label; rec.parentId = meta.parentId; }
      stagePlatformAgent(rec);
    } catch (e) {}
  }));

  // ══ 【卡死修复 · 宿主层】job_output 长等待守卫 ═══════════════════════════
  // 事故：job_output { wait:true, timeout_ms:270000/240000 } 静默阻塞数分钟，
  //   用户以为死机而强退；宿主停机时 ctx.jobs 的 fiber 失活 → cordis 抛
  //   cannot get required service "jobs" in inactive context → 整轮 turn/end{interrupted}。
  // 修法：把单次等待切成短窗口（默认 45s，到点照常返回"仍在运行"），
  //   并把上下文失活异常降级成普通工具错误。详见 lib/job-wait-guard.js。
  // 注意：ctx.on('tools/execute') 是官方包装点（dsh-tool-call-timeout-policy 同款）。
  try {
    disposers.push(installJobWaitGuard(ctx));
  } catch (e) {
    // 守卫装配失败绝不能影响插件本身（它是兜底，不是主功能）
    try { console.warn('[dsh-engineering-ui] job-wait-guard install failed', e); } catch (e2) {}
  }

  // ══ 【内存守卫】子代理并发闸门 + 工作集回收 ═══════════════════════════════
  // 用户需求（原文）：
  //   「在多子代理并行中，会出现一堆子代理都在猛猛开的情况，导致出现"给测试者
  //     的电脑内存给干爆了"，这样子改，只能最多三个子代理开启，然后要避免内存
  //     干爆的情况，你看看可不可以搞一下啥类似的只清理系统运行内存但是不影响
  //     流程的正常使用的」。
  //
  // ── 闸门放在哪、为什么是这里 ────────────────────────────────────────────
  // 官方 subagents 服务有进程级名额（activation pool，默认 8），超限时抛
  //   ACTIVATION_LIMIT_REACHED。问题是：
  //   ① 默认 8 对 SolidWorks 场景太大，八个屋同时建模必然把内存打爆；
  //   ② 那个错误发生在 fork【之后】，主对话已经消耗了一轮，还不好读。
  // 本插件在 tools/pre-execute（官方包装点，dsh-tools pipeline.js:387）先拦一刀：
  //   · 只拦 subagent / subagent_fork 两个工具；
  //   · 只对【工程模式】作用域生效（其他预设零影响）；
  //   · 名额按 root 会话统计（含孙代，见 mem-guard.countOccupiedChildren 的注释）；
  //   · 超限直接返回可读的工具错误，模型拿到的是"先等一个屋收尾"的明确指引，
  //     而不是宿主那句冷冰冰的 ACTIVATION_LIMIT_REACHED。
  // 拦截是【软闸门】：它不改宿主配置，关掉 DSH_ENG_MEM_GUARD=0 就恢复原样。

  /** 工程模式作用域判定（比 modelChainApplies 更宽：不要求链里有模型）。 */
  function isEngineeringScope(agent) {
    try {
      if (!agent) return false;
      if (isGuardSession(agent)) return true;
      const h = agent && agent.session && agent.session.header;
      if (h && String(h.agentPreset || '') === GUARD_PRESET) return true;
      if (agent && String(agent.agentPreset || '') === GUARD_PRESET) return true;
      // 子代理：有父会话即视为继承父作用域（父级是不是工程模式由 fork 决定）
      const pid = parentSessionId(agent);
      if (pid) {
        const cached = presetCache.get(String(pid));
        if (cached === GUARD_PRESET) return true;
        if (cached && isKnownPreset(cached) && cached !== GUARD_PRESET) return false;
        return true;   // 父级未知 → 保守按工程模式处理（同 modelChainApplies 的口径）
      }
      return false;
    } catch (e) {
      return false;
    }
  }

  /** 找到 root 会话 id（并发名额挂在 root 的 pool 上）。 */
  function rootSessionIdOf(agent) {
    try {
      let cur = agent;
      let guard = 0;
      while (cur && guard < 32) {
        guard += 1;
        const pid = parentSessionId(cur);
        if (!pid) return String((cur && (cur.sessionId || cur.id)) || '');
        let parent = null;
        try { parent = ctx.agents && typeof ctx.agents.get === 'function' ? ctx.agents.get(String(pid)) : null; } catch (e) { parent = null; }
        if (!parent) return String(pid);
        cur = parent;
      }
      return String((cur && (cur.sessionId || cur.id)) || '');
    } catch (e) {
      return String((agent && (agent.sessionId || agent.id)) || '');
    }
  }

  /**
   * 构造"并发超限"的 pre-execute 决策。
   *
   * ══ 【2026-11-11 修复·P0 闸门静默失效】════════════════════════════════
   * 现场（验收复跑 20261111_131815_580E38FA，日志实锤）：
   *   峰值并发 4 个子代理，出现 3 个越限窗口（13:30:21–14:27:12、
   *   15:01:29–15:05:03、15:21:23–15:34:26）；9 个日志文件里
   *   `SUBAGENT_LIMIT_REACHED` 一次都没有真正拦下过 fork —— 主会话
   *   :345/:353 甚至自己记下"第 4 个 fork 竟然成功"。用户要求的上限 3
   *   等于完全不存在。
   *
   * 根因（契约不匹配，不是逻辑错）：
   *   `tools/pre-execute` 是 **waterfall**，宿主签名见
   *   dsh-tools/lib/types/pipeline.js（api-catalog 逐字记录）：
   *     'tools/pre-execute'(exec, next: () => Promise<PreToolDecision>)
   *   `PreToolDecision` 只认 `{ kind: 'allow' | 'deny' | 'cancel' | 'ask' }`：
   *     · pipeline-types.js:143  `waterfall(..., () => ({ kind: 'allow' }))`
   *     · :154  `decision.kind === 'allow' ? checkGuards(exec) : decision.reason`
   *     · :155  `decision.kind === 'deny' ? decision.info : undefined`
   *   旧实现返回的是**工具结果形状**（`{content, isError, error}`）——
   *   那是 execute 之后才能用的东西。`decision.kind` 因此是 undefined：
   *   不等于 'allow' 也不等于 'deny' → `denialReason = decision.reason`
   *   = undefined → 宿主的 `if (denialReason !== undefined)` 不成立 →
   *   **既不拦也不报错，直接放行**。这就是"闸门看起来装了、实际没拦"。
   *
   * 修复：返回宿主真正认的 `{kind:'deny', reason, info}`。
   *   `reason` 会被拼进 `Error: ${reason}` 交给模型（pipeline-types.js:161），
   *   `info` 在 :155 被取出作为结构化错误信息。两处都用上，模型才能
   *   拿到"当前 4 / 上限 3"并知道下一步该干什么。
   *
   * ⚠️ 不要再改回 `{content, isError}` 形状 —— 那样会静默回到"不拦"。
   */
  function subagentLimitResult(gate) {
    return {
      kind: 'deny',
      reason: gate.message,
      info: {
        name: 'SubagentLimitError',
        code: 'SUBAGENT_LIMIT_REACHED',
        active: gate.active,
        max: gate.max,
      },
    };
  }

  /**
   * 装配并发闸门。返回 disposer。
   * @param hostCtx 插件上下文（需可监听 'tools/pre-execute'）
   */
  function installSubagentConcurrencyGate(hostCtx) {
    if (!memGuardEnabled() || !hostCtx || typeof hostCtx.on !== 'function') return () => {};
    // ── 为什么【不只拦 subagent_fork】────────────────────────────────────
    // 用户报告「子代理死了不换模型」时我们发现：spawn 出来的子代理没有
    // fork 那一批的历史，出问题往往就是靠 subagent(spawn) 重开。
    // 两种委派都要过闸门，否则把 fork 拦住、走 spawn 绕过去，等于没拦。
    const listener = async (exec, next) => {
      // ── 只处理两个委派工具，其余原样放行 ────────────────────────────────
      let name = '';
      try { name = String((exec && exec.name) || ''); } catch (e) { name = ''; }
      if (name !== 'subagent' && name !== 'subagent_fork') return next();
      try {
        const agent = exec && exec.agent;
        if (!agent || !isEngineeringScope(agent)) return next();
        const rootId = rootSessionIdOf(agent);
        if (!rootId) return next();
        let rows = [];
        try {
          rows = await hostCtx.subagents.listDescendants(String(rootId));
        } catch (e) {
          // 读不到名册（旧宿主 / 服务缺失）→ 宁可放行，也不能因为闸门把流程锁死
          // 【2026-11-11 补】但【仍要登记批次】：否则这条路径下 fork 出去的子代理
          //   永远不会被 pollBatches 观察到 → 整批收工后的内存回收永不触发，
          //   正是用户问的"主对话有没有释放运行内存"。放行 ≠ 不记账。
          trackBatch(rootId);
          return next();
        }
        const gate = evaluateConcurrency(rows, rootId, agent.id, maxActiveSubagents());
        if (gate.allowed) {
          // 记下"这一批还在跑" —— 回收不在 fork 时做，而是等【整批收工】后做，
          // 见 pollBatches()。用户要求原文：
          //   「这个清理内存是每三个子代理都干完了，然后主对话自己清理的」。
          trackBatch(rootId);
          return next();
        }
        try {
          console.warn('[dsh-engineering-ui] ' + gate.message.split('\n')[0]);
        } catch (e) {}
        return subagentLimitResult(gate);
      } catch (e) {
        return next();
      }
    };
    return hostCtx.on('tools/pre-execute', listener);
  }

  /**
   * 节流回收器（阈值 + 间隔都在 mem-guard.js 里，写错配置不会把功能关死）。
   * ⚠️ 必须【防御性构造】：有些部署/测试会用正则剥掉 import 头再求值，
   *    此时 createReclaimer 未定义 —— 直接调用会在 apply() 里抛 ReferenceError，
   *    让整个工程模式装配失败。这里退化成"永不回收"的空实现。
   */
  let maybeReclaim = function () { return { ok: false, skipped: 'unavailable' }; };
  try { maybeReclaim = createReclaimer(); } catch (e) { /* 保持空实现 */ }

  // ══ 【2026-11-11 修复·回收不可观测】════════════════════════════════════
  // 现场问题（用户提问「主对话在子代理完成任务后释放内存，有没有生效？」）：
  //   旧实现 scheduleReclaim() 里 `try { maybeReclaim(...) } catch (e) {}` ——
  //   【结果直接丢掉】，只在 console.warn 打一句"已排程一次内存回收"。
  //   于是"排程了"和"真回收了"在外部看起来一模一样：
  //     · 真回收成功 → 内存下降（但没人记账，事后无从证明它跑过）；
  //     · 被 85% 阈值挡掉（skipped）→ 跟成功长得一样；
  //     · createReclaimer 构造失败（skipped:'unavailable'）→ 也一样。
  //   用户这次问"有没有生效"，我只能靠翻进程和手工跑一次来回答 ——
  //   这本身就是缺陷：一个声称"会清理内存"的功能，必须能自证跑过。
  //
  // 修复：把最近 N 次回收的【真实结果】留在内存里（不写盘，避免污染运行时
  //   状态目录），并在 /dsh-engineering-ui/mem-guard 的 GET 里返回，
  //   设置页与排查脚本都能直接看到"哪一次、什么时候、回收了几个进程、
  //   省了多少 MB / 为什么跳过"。
  const RECLAIM_LOG_MAX = 20;
  const reclaimLog = [];
  let reclaimCount = 0;
  let lastReclaimResult = null;

  /** 记一条回收结果（含失败/跳过），供状态接口回显。 */
  function noteReclaim(trigger, result) {
    try {
      const rec = {
        at: new Date().toISOString(),
        trigger: String(trigger || 'unknown'),   // batch-complete / manual / config
        result: (result && typeof result === 'object') ? result : { ok: false, error: String(result) },
      };
      reclaimCount += 1;
      lastReclaimResult = rec;
      reclaimLog.push(rec);
      if (reclaimLog.length > RECLAIM_LOG_MAX) reclaimLog.shift();
      // 真正回收成功才值得打一行日志；跳过/失败降为 debug 级别噪音
      try {
        const r = rec.result;
        if (r && r.ok && !r.skipped) {
          console.warn('[dsh-engineering-ui] 内存回收：' + (rec.trigger)
            + '，已回收 ' + String(r.reclaimed || 0) + ' 个进程'
            + (r.before && r.after
              ? ('，可用内存 ' + r.before.free_mb + 'MB → ' + r.after.free_mb + 'MB')
              : ''));
        }
      } catch (e) {}
    } catch (e) {}
  }

  /** 非阻塞地排一次回收（绝不让 PowerShell 卡住 agent loop）。 */
  function scheduleReclaim(force, trigger) {
    try {
      const t = setTimeout(() => {
        let r = { ok: false, error: 'not-run' };
        try { r = maybeReclaim({ force: !!force }); } catch (e) {
          r = { ok: false, error: String((e && e.message) || e) };
        }
        // 【关键】结果不再丢弃 —— 必须记账，否则无法回答"到底生效没有"。
        noteReclaim(trigger || (force ? 'batch-complete' : 'throttled'), r);
      }, 1200);
      if (t && typeof t.unref === 'function') t.unref();
    } catch (e) {
      noteReclaim(trigger || 'schedule-error', { ok: false, error: String((e && e.message) || e) });
    }
  }

  // ══ 【回收时机】只在"这一批子代理全部收工"后回收一次 ═══════════════════
  // 用户要求（原文）：「这个清理内存是每三个子代理都干完了，然后主对话自己清理的」。
  // 旧实现是在【每次 fork 放行】和【每轮 turn-stopping】各回收一次 —— 那是
  //   "开着的时候天天刷工作集"，既没必要（刚分配的内存马上又要用），
  //   也会把主对话自己的热页刷掉，反而拖慢。
  // 现在改成：fork 时只登记 root，之后由 pollBatches() 轮询名册；
  //   当该 root 名下【没有活跃子代理】时，才做一次回收，并注销登记。
  // 轮询很轻（只是读投影），间隔 15s，且 unref 不吊住进程。
  const BATCH_POLL_MS = 15000;
  const BATCH_MAX_WAIT_MS = 4 * 60 * 60 * 1000;   // 4h 兜底（与 SW_MAX_HOLD 同量级）
  /** rootId -> { since: number }；表示"这个 root 有一批子代理在跑，收工后要回收"。 */
  const batchRoots = new Map();
  let batchTimer = null;

  /** 登记一个批次（幂等）。 */
  function trackBatch(rootId) {
    try {
      const id = String(rootId || '');
      if (!id) return;
      if (!batchRoots.has(id)) batchRoots.set(id, { since: Date.now() });
      startBatchWatcher();
    } catch (e) {}
  }

  /** 启动批次轮询（只启一次）。 */
  function startBatchWatcher() {
    try {
      if (batchTimer) return;
      batchTimer = setInterval(() => { pollBatches(); }, BATCH_POLL_MS);
      if (batchTimer && typeof batchTimer.unref === 'function') batchTimer.unref();
    } catch (e) {}
  }

  /** 停掉轮询（没有待观察批次时）。 */
  function stopBatchWatcher() {
    try {
      if (!batchTimer) return;
      clearInterval(batchTimer);
      batchTimer = null;
    } catch (e) { batchTimer = null; }
  }

  /**
   * 轮询所有待观察批次：名下活跃子代理归零 → 回收一次内存并注销。
   * 任何异常都只影响本次轮询，绝不冒泡。
   *
   * ══ 【2026-11-11 修复·P0 回收时机被提前】══════════════════════════════
   * 设计语义（用户原话）：「每三个子代理都干完了，然后主对话自己清理的」。
   * 所以唯一合法的回收时机 = 本 root 名下【活跃子代理归零】。
   *
   * 旧实现有两个叠加缺陷，导致回收被提前到 fork 后 ~15 秒：
   *   ① `countOccupiedChildren` 在文件头 import 里【漏了】（mem-guard.js
   *      确实导出了它，但 index.js:96 的白名单没带上）→ 每次调用必抛
   *      ReferenceError → 被 catch 吞掉 → `known=false`；
   *   ② `if (active === 0 || !known || timedOut)` —— `!known` 被当成
   *      "这批没了，直接收尾"，于是【名册读失败 = 立刻回收】。
   *      这与"整批收工"恰好相反：本该最不该回收的时刻（读不到状态）
   *      反而立刻回收，而且刚 fork 完的子代理内存正要用，被刷掉最亏。
   *
   * 修复：
   *   ① 补上 import（见文件头）；
   *   ② 把"读不到名册"从【收尾】改成【本次跳过、下轮再试】——
   *      只有 `active === 0`（真归零）或 4h 超时兜底才回收；
   *   ③ 回收改成 `force: true`：走到这里已经满足"整批收工"，若再让
   *      85% 阈值把回收挡掉，就等于"时机对了但不干活"（实测多数空跑）。
   *      阈值仍由 reclaimWorkingSets 的 force 分支负责：force 只跳过
   *      阈值/节流判断，不做任何破坏性操作（EmptyWorkingSet 本身安全）。
   */
  async function pollBatches() {
    if (!batchRoots.size) { stopBatchWatcher(); return; }
    const now = Date.now();
    for (const [rootId, info] of Array.from(batchRoots.entries())) {
      try {
        let active = 0;
        let known = false;
        try {
          const rows = await ctx.subagents.listDescendants(String(rootId));
          known = true;
          active = countOccupiedChildren(rows, String(rootId)).ids.length;
        } catch (e) {
          // 【2026-11-11】名册读不到 ≠ 收工。本轮跳过，下轮再试；
          //   只有 4h 超时兜底才允许在 known=false 时收尾（见 timedOut）。
          known = false;
        }
        const timedOut = (now - (info.since || now)) > BATCH_MAX_WAIT_MS;
        // 真归零才算收工；读不到名册时只有超时兜底才收（不再"读不到就收"）。
        const finished = (known && active === 0) || timedOut;
        if (finished) {
          batchRoots.delete(rootId);
          // 整批（≤3 个）收工 → 主对话这一侧回收一次（force：时机已对，别再被阈值挡掉）
          scheduleReclaim(true, 'batch-complete');
          try {
            console.warn('[dsh-engineering-ui] 子代理批次收工（root=' + rootId
              + (timedOut ? '，超时兜底' : ('，活跃归零 active=' + active))
              + '），已排程一次内存回收');
          } catch (e) {}
        }
      } catch (e) { /* 单批失败不影响其他批 */ }
    }
    if (!batchRoots.size) stopBatchWatcher();
  }

  /** 该 root 名下当前活跃子代理数（失败返回 -1，供状态接口展示）。 */
  async function activeChildCount(rootId) {
    try {
      const rows = await ctx.subagents.listDescendants(String(rootId));
      return countOccupiedChildren(rows, String(rootId)).ids.length;
    } catch (e) {
      return -1;
    }
  }

  try {
    disposers.push(installSubagentConcurrencyGate(ctx));
  } catch (e) {
    try { console.warn('[dsh-engineering-ui] subagent concurrency gate install failed', e); } catch (e2) {}
  }

  // ⚠️ 不再挂 turn-stopping 回收：那是"每轮都刷"，与用户要求的
  //   "整批子代理干完之后清理"相反。回收时机统一由 pollBatches() 管；
  //   另外保留设置页的「立刻回收内存」按钮作手动兜底。
  // 插件卸载时停掉轮询。
  disposers.push(function () { stopBatchWatcher(); batchRoots.clear(); });

  // ══ 【内存守卫】状态 / 手动回收接口 ═══════════════════════════════════
  // GET  /dsh-engineering-ui/mem-guard        → 只读快照（含上限与阈值）
  // POST /dsh-engineering-ui/mem-guard        → 立刻回收一次（可带 force:true）
  // POST 带 max_subagents 等字段               → 写 tools/mem_guard.json 并立刻生效

  /** mem-guard 配置文件路径（与 model_chain.json 同一个 tools 目录）。 */
  function memGuardConfigPath() {
    try {
      const dir = findToolsDir();
      if (!dir) return null;
      return path.join(dir, 'mem_guard.json');
    } catch (e) {
      return null;
    }
  }

  /** 读 mem_guard.json（找不到/读坏 → 空对象，即全用默认值）。 */
  function readMemGuardSettings() {
    try {
      const p = memGuardConfigPath();
      if (!p) return {};
      const j = readJsonSafe(p);
      if (!j || typeof j !== 'object') return {};
      const out = {};
      const num = (v, min, max) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return undefined;
        const x = Math.floor(n);
        return (x >= min && x <= max) ? x : undefined;
      };
      const a = num(j.max_subagents, 1, 12); if (a !== undefined) out.max_subagents = a;
      const b = num(j.reclaim_threshold_pct, 50, 99); if (b !== undefined) out.reclaim_threshold_pct = b;
      const c = num(j.reclaim_interval_ms, 5000, 3600000); if (c !== undefined) out.reclaim_interval_ms = c;
      return out;
    } catch (e) {
      return {};
    }
  }

  /** 写 mem_guard.json（原子写失败也不抛，返回 null 表示没存成）。 */
  function writeMemGuardSettings(next) {
    try {
      const p = memGuardConfigPath();
      if (!p) return null;
      const clean = {};
      const num = (v, min, max) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return undefined;
        const x = Math.floor(n);
        return (x >= min && x <= max) ? x : undefined;
      };
      const a = num(next && next.max_subagents, 1, 12); if (a !== undefined) clean.max_subagents = a;
      const b = num(next && next.reclaim_threshold_pct, 50, 99); if (b !== undefined) clean.reclaim_threshold_pct = b;
      const c = num(next && next.reclaim_interval_ms, 5000, 3600000); if (c !== undefined) clean.reclaim_interval_ms = c;
      clean.updated_at = new Date().toISOString();
      fs.writeFileSync(p, JSON.stringify(clean, null, 2), 'utf8');
      return clean;
    } catch (e) {
      return null;
    }
  }

  // 装配时把磁盘配置注入运行期覆盖（文件不存在 = 全用默认值，不写盘）。
  try { setMemGuardOverrides(readMemGuardSettings()); } catch (e) {}
  disposers.push(ctx.webServer.register(guardRoute({
    kind: 'exact',
    path: PREFIX + '/mem-guard',
    handler: async (req, res) => {
      try {
        const method = String((req && req.method) || 'GET').toUpperCase();
        if (method === 'GET') {
          const snap = memorySnapshot();
          // 待观察批次（rootId + 起始时间）—— 面板据此显示"这批还没收工"。
          const pending = Array.from(batchRoots.entries()).map(([id, info]) => ({
            root_session_id: id,
            since: new Date((info && info.since) || Date.now()).toISOString(),
          }));
          return send(res, 200, {
            ok: true,
            enabled: memGuardEnabled(),
            max_subagents: maxActiveSubagents(),
            reclaim_threshold_pct: reclaimThresholdPct(),
            reclaim_interval_ms: reclaimIntervalMs(),
            // 【回收时机】只在"这一批子代理全部收工"后回收一次（用户要求原文：
            //   「每三个子代理都干完了，然后主对话自己清理的」）。
            reclaim_on: 'batch-complete',
            batch_poll_interval_ms: BATCH_POLL_MS,
            pending_batches: pending,
            // 【2026-11-11】回收记账：证明"到底跑过没有、跑了几次、省了多少"
            reclaim_count: reclaimCount,
            last_reclaim: lastReclaimResult,
            recent_reclaims: reclaimLog.slice().reverse(),
            memory: snap,
          });
        }
        if (method !== 'POST') return send(res, 405, { ok: false, error: 'GET or POST only' });
        let payload = {};
        try { payload = JSON.parse(await readBody(req)) || {}; } catch (e) { payload = {}; }
        // ── 设置写入：允许把 max_subagents / threshold / interval 落到
        //    tools/mem_guard.json，并【当场】更新运行期覆盖（设置页改完立刻生效）。
        if (payload && (payload.max_subagents !== undefined
            || payload.reclaim_threshold_pct !== undefined
            || payload.reclaim_interval_ms !== undefined)) {
          const next = { ...memGuardOverrides() };
          if (payload.max_subagents !== undefined) next.max_subagents = payload.max_subagents;
          if (payload.reclaim_threshold_pct !== undefined) next.reclaim_threshold_pct = payload.reclaim_threshold_pct;
          if (payload.reclaim_interval_ms !== undefined) next.reclaim_interval_ms = payload.reclaim_interval_ms;
          const saved = writeMemGuardSettings(next);
          setMemGuardOverrides(saved || next);
          return send(res, 200, {
            ok: true,
            max_subagents: maxActiveSubagents(),
            reclaim_threshold_pct: reclaimThresholdPct(),
            reclaim_interval_ms: reclaimIntervalMs(),
            config: saved || next,
          });
        }
        const r = maybeReclaim({ force: payload && payload.force === true });
        // 【2026-11-11】手动回收同样记账，面板/排查脚本能看到这一笔。
        noteReclaim('manual', r);
        return send(res, 200, {
          ok: !!(r && r.ok),
          max_subagents: maxActiveSubagents(),
          result: r,
          reclaim_count: reclaimCount,
        });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  })));


  return () => {
    // 卸载前把待写记录落盘（尽力而为；失败也绝不能抛）
    try { if (_paPending && _paPending.size) flushPlatformAgents(); } catch (e) {}
    for (const dispose of disposers.reverse()) {
      try { dispose(); } catch {}
    }
  };
}
