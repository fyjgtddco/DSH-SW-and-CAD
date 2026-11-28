/**
 * dsh-engineering-memory-guarded — 【受约束的工程记忆压缩器】
 * ==========================================================================
 * 只在 engineering 预设下加载（见 preset-engineering.patch.yml 的 compaction 组），
 * 用来【替换】@deepseek-ai/dsh-compaction-basic。所以它是「工程模式专用」的：
 * 其它预设读的是它们自己的 compaction-basic，两边互不串台。
 *
 * ── 它和旧压缩器的差别（不是升级，是换设计）─────────────────────────────
 *   旧 dsh-engineering-compaction：调 LLM 写分层摘要（L0 数值抽取 + 强制回填）。
 *   本插件：**不调 LLM 写摘要**，改用确定性快照 ——
 *     · 活动约束（用户明确给出的尺寸）结构保真；
 *     · 派生值必须声明 parents，上游一改就级联撤下；
 *     · 摘要内容是插件算出来的，模型改不了；
 *     · 提交要过独立审核（重新读回已落盘会话，核对真实替换事件）。
 *   代价与旧插件互补：旧插件保「长段推理」，本插件保「尺寸真相 + 可恢复 + 可验哈希」。
 *   本插件不做旧插件的 L0 数值回填 —— 两类记录写两份独立账本，UI 侧合并展示，
 *   所以工程模式的压缩面板不会因为换后端而变空白（见 lib/ledger.js）。
 *
 * ── 加载失败时的行为（关键）─────────────────────────────────────────────
 *   preset 里任何一行加载失败，整个 engineering 预设都会挂掉，用户看到的是
 *   「工程模式从列表里消失」。旧压缩器已经踩过这个坑并做了动态导入 + 本地兜底。
 *   这里沿用同一策略：父类拿不到就退化成空壳（不压缩，但会话照常跑），
 *   绝不因为"压缩功能坏了"把整个工程模式拖垮。
 */

import { randomUUID } from 'node:crypto';
import { dirname, isAbsolute } from 'node:path';
import { applyChange, candidatePages, directChanges, exportGraph, isResumeOriginal, messageText, needsDimensionIntent, openJournal, readableSummary } from './guarded-memory.js';
import { apply as installAudit } from './guarded-audit.js';
import { appendLedgerEntry } from './ledger.js';

const TOOL = 'engineering_memory_request';

/**
 * 父类解析：先试真实 @deepseek-ai/dsh-compaction-basic，拿不到就用本地空壳。
 *
 * 为什么不用静态 import：profile 的 node_modules 里通常没有 @deepseek-ai/*
 *   （pnpm autoInstallPeers=false），裸静态 import 一旦解析失败就是【模块加载期】
 *   抛错 —— 那会直接把整个 preset 挂掉，连兜底的机会都没有。
 */
let BasicCompactionEngine;
let balancedAfter;
let ENGINE_MODE = 'real';
try {
  const m = await import('@deepseek-ai/dsh-compaction-basic');
  BasicCompactionEngine = m.default;
  if (typeof BasicCompactionEngine !== 'function') throw new Error('default export is not a class');
} catch (e) {
  ENGINE_MODE = 'fallback';
  try {
    const cordis = await import('@deepseek-ai/cordis');
    const Service = cordis.Service || (cordis.default && cordis.default.Service);
    if (typeof Service === 'function') {
      BasicCompactionEngine = class FallbackCompaction extends Service {
        constructor(ctx) { super(ctx, 'compaction'); this.config = {}; }
        async compactIfNeeded() { return null; }
        async compactRegion() { throw new Error('engineering memory compaction unavailable: @deepseek-ai/dsh-compaction-basic not resolvable'); }
        async compactNow() { throw new Error('engineering memory compaction unavailable: @deepseek-ai/dsh-compaction-basic not resolvable'); }
        async summarize() { throw new Error('engineering memory compaction unavailable: @deepseek-ai/dsh-compaction-basic not resolvable'); }
      };
    }
  } catch (e2) { /* 连 cordis 都没有 → 保留 undefined，下面兜底 */ }
  if (typeof BasicCompactionEngine !== 'function') {
    BasicCompactionEngine = class NoopCompaction {
      constructor() { this.config = {}; }
      async compactIfNeeded() { return null; }
      async compactRegion() { throw new Error('compaction unavailable'); }
      async compactNow() { throw new Error('compaction unavailable'); }
      async summarize() { throw new Error('compaction unavailable'); }
    };
  }
}
if (ENGINE_MODE === 'real') {
  try { ({ toolPairingBalancedAfter: balancedAfter } = await import('@deepseek-ai/dsh-compaction')); }
  catch { /* The regular host range still works without the optional wider selection. */ }
}

export const engineMode = () => ENGINE_MODE;

const toolParameters = {
  type: 'object', additionalProperties: false, required: ['action'],
  properties: {
    action: { type: 'string', enum: ['read', 'correct', 'derive', 'invalidate'] },
    id: { type: 'string' }, value: { type: 'string' },
    parents: { type: 'array', items: { type: 'string' } },
  },
};

function selectedSpan(agent) {
  const { session } = agent;
  const seqs = session.surface.nodes;
  const first = session.eventAt(seqs[0])?.type === 'system/message' ? seqs[1] : seqs[0];
  return first !== undefined ? { start: first, end: seqs.at(-1) } : null;
}

function activePins(state) {
  const live = new Map();
  for (const pin of state.pins) {
    for (const seq of pin.shadowed) live.delete(seq);
    live.set(pin.seq, pin);
  }
  return [...live.values()];
}

function recentPages(messages, previous, revised, redacted = new Set()) {
  const pages = new Map(previous.filter(page => ['user', 'user-evidence', 'answer', 'workflow'].includes(page.kind))
    .map(page => [page.id, page]));
  const calls = new Map();
  for (const message of messages) {
    if (message.role === 'assistant') {
      for (const block of message.content) {
        if (block.type === 'tool-call') calls.set(block.id, block.name);
      }
    }
    if (message.role === 'tool' && !message.isError && message.source?.kind === 'tool') {
      const tool = calls.get(message.toolCallId ?? message.source.callId);
      if (tool === 'ask_user_question' || tool === 'engineering_seven_stage') {
        let result;
        try { result = JSON.parse(messageText(message)); }
        catch { continue; }
        if (tool === 'ask_user_question' && Array.isArray(result?.answers)) {
          for (const answer of result.answers) {
            if (typeof answer?.id !== 'string') continue;
            const selected = Array.isArray(answer.selected) ? answer.selected : [];
            const text = [...selected, answer.custom].filter(value => typeof value === 'string' && value.trim()).join('\n');
            if (!text) continue;
            const id = message.id + '/' + answer.id;
            if (!pages.has(id)) pages.set(id, { kind: 'answer', id, text });
          }
        }
        if (tool === 'engineering_seven_stage' && result?.ok !== false &&
            /^[\w.-]{1,80}$/.test(result?.taskId ?? '') && /^[a-z_]{1,40}$/.test(result?.phase ?? '')) {
          const step = result.current?.step;
          pages.set('workflow', { kind: 'workflow', id: 'workflow', task: result.taskId,
            step: typeof step === 'string' && /^\d+(\.\d+)*$/.test(step) ? step : '', phase: result.phase });
        }
      }
    }
    if (message.source?.kind !== 'user' || message.role !== 'user') continue;
    const text = messageText(message);
    if (text && !pages.has(message.id)) pages.set(message.id, { kind: 'user', id: message.id, text });
  }
  return [...pages.values()].map(page => redacted.has(page.id) || (revised && /\p{N}/u.test(page.text ?? ''))
    ? { kind: 'user-evidence', id: page.id } : page);
}

/**
 * 沿 agent.parent 往上找根会话，并取到子代理的显示名（房间名）。
 *
 * 为什么需要 root / label：UI 要把压缩历史按「主对话 / 各个子代理」分开显示
 *   （用户截图里的两条黄圈）。账本里没有这两个字段的话，前端就只能拿到一串
 *   裸 sessionId，分不清哪条属于哪个房间。
 */
// ---- 父会话反查器 --------------------------------------------------------
// identityOf 必须能问到「某个 sessionId 的父会话是谁」，才能一路爬到最顶层根。
// 但它是纯函数、拿不到 ctx；所以在 apply() 里把反查能力注进来。
// 返回 '' 表示没有父会话（= 它就是根）。
let parentResolver = null;
export function setParentResolver(fn) {
  parentResolver = typeof fn === 'function' ? fn : null;
}

function identityOf(agent) {
  const out = { target: '', root: '', label: '', preset: '' };
  try {
    const session = agent && agent.session;
    out.target = String((session && session.id) || (agent && agent.id) || '');
    try { out.preset = String((session && session.header && session.header.agentPreset) || ''); } catch (e) {}
  } catch (e) {}
  // ══ 【BUG 修复 2026-11-28】root 必须沿 header.parentSession 上溯 ══════════
  // 现场：压缩历史面板的「子代理」页永远空白，账本里 1006 条记录【全部】
  //   target === root，前端按 `target !== root` 分链时分不出任何一条。
  //
  // 根因：这里原来靠 agent.parent / agent.parentAgent —— 那是【对象引用】，
  //   而 DSH 0.2.x 的 agent 上并没有这个字段（谱系是写在会话 header 里的
  //   字符串 parentSession）。于是循环第一次就拿不到 parent，
  //   root 直接等于 target，所有记录都被判成「主对话」。
  //
  // 正确取法与 dsh-engineering-ui/lib/index.js 的 parentSessionId() 一致：
  //   header.parentSession → agent.parentSession / parentId 兜底。
  try {
    // ---- 取【直接父会话 id】 ----
    const p = agent || {};
    let parentId = '';
    try {
      parentId = String((p.session && p.session.header && p.session.header.parentSession) || '');
    } catch (e) {}
    if (!parentId) parentId = String(p.parentSession || p.parentId || '');
    if (!parentId) {
      const par = p.parent || p.parentAgent || null;
      parentId = par ? String((par.session && par.session.id) || par.id || '') : '';
    }
    out.parent = parentId;
    // ---- 再一路往上找到【最顶层根会话】 ----
    // 【BUG 修复 2026-11-28 · 第二轮】上一版把 root 直接当成直接父会话就停了，
    //   结果二级及以下的孙代挂到它自己的父会话下，和一级子代理的 root 不同，
    //   面板按 root 聚合时它们整组丢失（用户反馈：分子代理能显示，二级以下不显示）。
    //
    // 正确口径：root = 谱系最顶端的主对话。这样一条链上所有层级的记录，
    //   root 字段都相同，面板一次 root 过滤就能全部取到；
    //   「属于哪个子代理」由 target 自己回答，不靠 root 表达。
    // 上溯方式：优先用 ctx 反查（能拿到父会话的 header，最可靠）；
    //   拿不到 ctx 时退回沿对象引用爬。两路都最多 32 层，防环。
    let rootId = parentId;
    if (parentId) {
      // ① 首选：注入的反查器（见 apply 里的 setParentResolver）
      const look = parentResolver;
      if (typeof look === 'function') {
        let cur = parentId; const seen = new Set([cur]);
        for (let i = 0; i < 32; i++) {
          let up = '';
          try { up = String(look(cur) || ''); } catch (e) { up = ''; }
          // ══ 【2026-11-28】查不到 ≠ 没有父会话 ══════════════════════════
          // 现场：二级及以下的记录 root 停在【直接父级】，于是同一条链上的
          //   记录 root 各不相同（31da68c4 / f4fdb8a7 / session-4466…），
          //   面板按 root 聚合时它们散成好几组，二级子代理根本看不见。
          // 根因：反查表（parentMap）靠异步预热填充，落账那一刻常常还没轮到
          //   这个会话；旧实现把"查不到"当成"没有父会话"直接 break，
          //   于是 root 永远停在第一层父级。
          // 口径：查不到时不再静默截断 —— 用特殊标记触发下面的一次同步兜底
          //   （从 agent 的对象引用往上爬），爬不到才真的停。
          if (up === '__UNKNOWN__') {
            // 同步兜底：沿对象引用上溯（能爬多高爬多高）
            let o = p.parent || p.parentAgent || null;
            let g2 = 0;
            while (o && g2 < 32) {
              g2 += 1;
              const pid2 = String((o.session && o.session.id) || o.id || '');
              if (!pid2) break;
              rootId = pid2;
              o = o.parent || o.parentAgent || null;
            }
            break;
          }
          if (!up) break;                    // 明确的根（没有父会话）
          if (seen.has(up)) break;           // 防环
          seen.add(up); rootId = up; cur = up;
        }
      } else {
        // ② 兜底：沿对象引用往上爬（旧版结构 / 单测环境）
        let cur = p.parent || p.parentAgent || null;
        let guard = 0;
        while (cur && guard < 32) {
          guard += 1;
          const pid = String((cur.session && cur.session.id) || cur.id || '');
          if (!pid) break;
          rootId = pid;
          cur = cur.parent || cur.parentAgent || null;
        }
      }
    }
    // 没有父会话时它自己就是根（主对话），target 与 root 相等是【正确】的。
    out.root = rootId || out.target;
  } catch (e) { out.root = out.target; }
  try {
    const label = agent && (agent.label || agent.title || (agent.session && agent.session.title));
    out.label = String(label || '');
  } catch (e) {}
  if (!out.label) out.label = out.target.slice(0, 8);
  return out;
}

export class GuardedCompaction extends BasicCompactionEngine {
  #sessions = new Map();
  #running = new Set();

  constructor(ctx, config) {
    super(ctx, config);
    // Cordis 通过代理暴露服务：私有方法里的 this 必须绑在自有实例上，
    // 否则父类内部回调（compactRegion 等）拿到的是代理，私有字段全部读不到。
    this.compactRegion = this.compactRegion.bind(this);
    this.compactNow = this.compactNow.bind(this);
    this.summarize = this.summarize.bind(this);

    ctx.on('agent/pre-step', async ({ agent, messages, signal }, next) => {
      const entry = this.#entry(agent);
      const decision = await next();
      if (decision.kind !== 'enter') return decision;
      signal.throwIfAborted();
      for (const message of decision.messages ?? messages) {
        if (message.source?.kind !== 'user') continue;
        const text = messageText(message);
        const changes = directChanges(text);
        if (changes.length) entry.corrections.set(message.id, changes.map(change => ({
          ...change, evidence: message.id, evidenceText: text,
        })));
        else if (needsDimensionIntent(text)) {
          const state = entry.journal.state();
          // 「!继续原尺寸」= 用户收回变更，解除暂停；其它"我要换尺寸"= 先追问 ID/值/单位。
          entry.journal.save({ ...state, needsDimensions: !isResumeOriginal(text) });
        }
      }
      await this.#prepare(entry, signal);
      return decision;
    });

    ctx.on('agent/request', async ({ agent, signal }, next) => {
      const entry = this.#entry(agent);
      await this.#prepare(entry, signal);
      const result = await next();
      this.#check(entry, entry.agent.session.deriveMessages());
      return result;
    });

    ctx.on('llm/stream', (options, next) => {
      const entry = this.#sessions.get(options.sessionId);
      if (!entry) return next();
      this.#check(entry, options.messages);
      const engine = this;
      return (async function* guardedStream() {
        options.signal?.throwIfAborted();
        // 第一次校验放在生成器外，确保"发给模型前"就拦下；这里再校验一次是为了
        // 记录真实发出的请求消息所用检查点（reads.jsonl 的口径）。
        const pins = engine.#check(entry, options.messages);
        entry.journal.recordRead(pins);
        yield* next();
      })();
    });

    if (ctx.tools && typeof ctx.tools.guard === 'function') {
      ctx.tools.guard(exec => {
        const entry = exec.agent && this.#sessions.get(exec.agent.session.id);
        if (!entry) return undefined;
        const state = entry.journal.state();
        if (this.#running.has(entry.agent.session.id) || state.pending) {
          return 'MEMORY_COMPACTION_PENDING: 工程记忆还在压缩或审核，稍后恢复此会话。';
        }
        if (exec.name === TOOL) return undefined;
        if (state.needsDimensions) return 'MEMORY_DIMENSIONS_REQUIRED: 先向用户确认尺寸 ID、数值和单位。';
        const { session } = entry.agent;
        const seq = session.surface.nodes.findLast(seq => session.eventAt(seq)?.type === 'assistant/message');
        const last = seq === undefined ? undefined : session.eventAt(seq).data.message;
        if (last?.content.some(block => {
          if (block.type !== 'tool-call' || block.name !== TOOL) return false;
          try { return JSON.parse(block.arguments).action !== 'read'; }
          catch { return true; }
        })) return 'MEMORY_CHANGE_IN_BATCH: 尺寸修改请求须先单独提交和审核。';
        return undefined;
      });
    }

    ctx.on('agent/disposed', ({ agent }) => {
      if (this.#sessions.get(agent.session.id)?.agent === agent) this.#sessions.delete(agent.session.id);
    });

    if (ctx.tools && typeof ctx.tools.register === 'function') {
      ctx.tools.register({
        name: TOOL,
        description: '只读查看工程尺寸关系图，或向插件提交更正请求。correct 仅接受用户本轮明确指定的尺寸和值。derive 登记有依赖的暂定计算值，invalidate 撤下算错的派生值及全部下游。完成更正前暂停使用旧尺寸。',
        parameters: toolParameters,
        output: {
          schema: { type: 'object', additionalProperties: false, required: ['text'], properties: { text: { type: 'string' } } },
          render: (_args, result) => [{ type: 'text', text: result.text }],
        },
        execute: async (args, exec) => {
          exec.signal.throwIfAborted();
          if (!exec.agent) throw new Error('MEMORY_AGENT_REQUIRED');
          return this.#request(this.#entry(exec.agent), args);
        },
      });
    }
  }

  #entry(agent) {
    const { session } = agent;
    const existing = this.#sessions.get(session.id);
    if (existing?.agent === agent) return existing;
    const path = this.ctx.sessionPersistence.locate(session.header)?.path;
    if (!path || !isAbsolute(path)) throw new Error('MEMORY_SESSION_STORAGE_UNSUPPORTED');
    const entry = { agent, journal: openJournal(dirname(path), session.id), corrections: new Map() };
    this.#sessions.set(session.id, entry);
    return entry;
  }

  get #audit() {
    const audit = this.ctx.get('engineeringCompactionAudit');
    if (!audit) throw new Error('MEMORY_AUDITOR_UNAVAILABLE');
    return audit;
  }

  #request(entry, { action, id, value, parents }) {
    const state = entry.journal.state();
    if (action === 'read') return { text: JSON.stringify(exportGraph(state.graph)) };
    if (state.pending || this.#running.has(entry.agent.session.id)) throw new Error('MEMORY_COMPACTION_PENDING');
    if (state.needsDimensions && action !== 'correct') throw new Error('MEMORY_USER_EVIDENCE_REQUIRED');
    let change = { kind: action, id, value, parents };
    let messageId;
    if (action === 'correct') {
      const messages = entry.agent.session.deriveMessages();
      const user = messages.findLast(message => message.role === 'user' && message.source?.kind === 'user');
      const matched = directChanges(messageText(user)).find(item => item.id === id && item.value === value);
      if (!matched || state.consumed.includes(user.id)) {
        throw new Error('MEMORY_USER_EVIDENCE_REQUIRED: 请用户明确输入 !尺寸 <ID> = <值及单位>。');
      }
      change = { ...matched, evidence: user.id, evidenceText: messageText(user) };
      messageId = user.id;
    }
    this.#begin(entry, [change], messageId ? [messageId] : []);
    return { text: '请求通过规则检查，下一步先压缩并审核，审核通过才更新活动记忆。' };
  }

  #begin(entry, changes = [], consumed = [], range = null) {
    const { journal } = entry;
    const state = journal.state();
    if (state.pending) return state.pending;
    const pending = { id: randomUUID(), changes, consumed, graph: changes.reduce(applyChange, state.graph), range };
    journal.save({ ...state, pending });
    return pending;
  }

  #check(entry, messages) {
    const state = entry.journal.state();
    if (state.pending || this.#running.has(entry.agent.session.id)) throw new Error('MEMORY_COMPACTION_PENDING');
    try {
      const pins = activePins(state);
      for (const pin of pins) entry.journal.load(pin.candidate);
      this.#audit.read(pins, messages);
      return pins;
    } catch (error) {
      this.#begin(entry);
      throw error;
    }
  }

  async #finish(entry, signal) {
    const { journal, agent } = entry;
    const state = journal.state();
    if (!state.pending?.candidate) return false;
    const pin = await this.#audit.commit(agent.session, journal, state.pending, signal);
    signal?.throwIfAborted();
    // 审核的持久化读与 agent 的私有 pin 都成功后才释放；任何一步失败都保持 pending，
    // 下一次 #prepare 会重做，绝不在中间态放行模型请求。
    journal.save({ ...state, graph: state.pending.graph, pending: null,
      needsDimensions: state.needsDimensions && !state.pending.changes.some(change => change.kind === 'user'),
      consumed: [...new Set([...state.consumed, ...state.pending.consumed])],
      pins: [...state.pins, pin],
    });
    this.#recordLedger(entry, pin);
    return true;
  }

  /**
   * 落账本 —— UI 的压缩历史数据源（用户图一 / 图二的两条黄圈）。
   *
   * fail-open：记账失败绝不影响压缩提交本身。账本里的 label / root 让前端能把
   *   记录分到「主对话」或「某个子代理」名下；summary_excerpt 供点开看详情。
   */
  #recordLedger(entry, pin) {
    try {
      const id = identityOf(entry.agent);
      const excerpt = this.#excerptOf(entry, pin);
      appendLedgerEntry({
        at: Number(pin.at) || Date.now(),
        target: id.target,
        root: id.root,
        label: id.label,
        preset: id.preset,
        engine: 'dsh-engineering-memory-guarded',
        mode: ENGINE_MODE,
        action: 'compaction',
        attempt: String(pin.attempt || ''),
        message: String(pin.message || ''),
        constraints: Number(pin.constraints) || 0,
        input_chars: Number(pin.inputChars) || 0,
        output_chars: Number(pin.outputChars) || 0,
        saved_chars: Math.max(0, (Number(pin.inputChars) || 0) - (Number(pin.outputChars) || 0)),
        l0_numbers: Number(pin.constraints) || 0,
        l0_materials: 0,
        l0_restored: 0,
        hash: String(pin.hash || ''),
        summary_excerpt: excerpt,
      });
    } catch (e) { /* 记账失败不能影响压缩 */ }
  }

  /**
   * 从候选快照里提取一段可读摘要（给面板点开看）。
   *
   * 候选快照的正文是 MARKER 行 + JSON（见 guarded-memory.snapshot）。这里把它
   * 还原成「活动约束 / 派生值 / 保留的原文页」三段可读文本 —— 面板点开就能看到
   * 这次压缩到底保住了什么，而不是只有一个数字。
   */
  #excerptOf(entry, pin) {
    try {
      if (!pin?.candidate) return '';
      const candidate = entry.journal.load(pin.candidate);
      if (candidate.format === 'readable') return candidate.body.slice(0, 1600);
      const { body } = candidate;
      const json = body.slice(body.indexOf('\n') + 1);
      const parsed = JSON.parse(json);
      const lines = [];
      const constraints = Object.entries(parsed.activeConstraints || {});
      const derived = Object.entries(parsed.derived || {});
      if (constraints.length) {
        lines.push('活动约束 ' + constraints.length + ' 条：');
        for (const [id, node] of constraints) lines.push('  ' + id + ' = ' + node.value + '（用户给定）');
      }
      if (derived.length) {
        lines.push('派生值 ' + derived.length + ' 条：');
        for (const [id, node] of derived) {
          lines.push('  ' + id + ' = ' + node.value + (node.parents?.length ? '  ← ' + node.parents.join(', ') : ''));
        }
      }
      const pages = Array.isArray(parsed.pages) ? parsed.pages : [];
      const kept = pages.filter(page => page.kind === 'user' || page.kind === 'user-evidence');
      const activity = pages.find(page => page.kind === 'activity');
      const refs = pages.find(page => page.kind === 'tool-evidence');
      lines.push('保留用户原文 ' + kept.length + ' 页' + (refs && refs.messageIds ? '，工具证据 ' + refs.messageIds.length + ' 条' : ''));
      for (const page of kept) {
        const text = String(page.text || '').replace(/\s+/g, ' ').slice(0, 120);
        if (text) lines.push('  · ' + text);
      }
      if (activity?.excerpt) lines.push('最近进展摘录：' + String(activity.excerpt).replace(/\s+/g, ' ').slice(0, 200));
      return lines.join('\n').slice(0, 1600);
    } catch (e) { return ''; }
  }

  async #prepare(entry, signal) {
    const { journal, agent, corrections } = entry;
    if (this.#running.has(agent.session.id)) throw new Error('MEMORY_COMPACTION_PENDING');
    let state = journal.state();
    if (state.pending?.candidate && state.pending.hostId) {
      const closed = agent.session.snapshotEvents().some(event => event.type === 'compaction/end' &&
        event.data.compactionId === state.pending.hostId && !('error' in event.data));
      if (closed) {
        try { await this.#finish(entry, signal); }
        catch (error) {
          signal.throwIfAborted();
          this.ctx.logger.warn('工程记忆提交复核未通过，将重新压缩：' + error.message);
        }
        state = journal.state();
      }
    }
    if (!state.pending) {
      const consumed = [...corrections.keys()].filter(id => !state.consumed.includes(id));
      const changes = consumed.flatMap(id => corrections.get(id));
      if (changes.length) this.#begin(entry, changes, consumed);
      else {
        try { this.#check(entry, agent.session.deriveMessages()); return; }
        catch (error) {
          if (!journal.state().pending) throw error;
        }
      }
    }
    state = journal.state();
    const range = state.pending.changes.length || state.pending.failure
      ? selectedSpan(agent) : state.pending.range ?? selectedSpan(agent);
    if (!range && state.pending.changes.length && state.pins.length === 0) {
      const pending = state.pending;
      const hash = journal.backup({ kind: 'bootstrap', attempt: pending.id, graph: pending.graph });
      this.#audit.bootstrap(journal, pending, hash);
      journal.save({ ...state, graph: pending.graph, pending: null, needsDimensions: false,
        consumed: [...state.consumed, ...pending.consumed] });
    } else {
      if (!range) throw new Error('MEMORY_RECOVERY_NO_SAFE_RANGE');
      const nodes = agent.session.surface.nodes;
      const target = nodes.includes(range.start) && nodes.includes(range.end) ? range : selectedSpan(agent);
      if (!target) throw new Error('MEMORY_RECOVERY_NO_SAFE_RANGE');
      await this.#compactSelected(target.start, target.end, agent, signal);
    }
    for (const id of journal.state().consumed) corrections.delete(id);
  }

  async #run(agent, signal, operation) {
    const entry = this.#entry(agent);
    const key = agent.session.id;
    const wasPending = Boolean(entry.journal.state().pending);
    if (this.#running.has(key)) throw new Error('MEMORY_COMPACTION_PENDING');
    this.#running.add(key);
    try {
      signal?.throwIfAborted();
      const result = await operation(entry);
      if (result !== null && !await this.#finish(entry, signal)) throw new Error('MEMORY_AUDIT_NOT_PREPARED');
      return result;
    } catch (error) {
      const state = entry.journal.state();
      const end = agent.session.snapshotEvents().findLast(event => event.type === 'compaction/end' &&
        event.data.compactionId === state.pending?.hostId);
      if (!wasPending && !signal?.aborted && state.pending && end &&
          /^(summary is not smaller than the shadowed content \(|MEMORY_MINIMUM_OVER_BUDGET$)/.test(end.data.error ?? '')) {
        // No replacement was made. Normal budget rejection must not force recovery compaction.
        entry.journal.save({ ...state, pending: null });
        return null;
      }
      if (state.pending) entry.journal.save({ ...state, pending: { ...state.pending,
        failure: signal?.aborted ? 'MEMORY_INTERRUPTED' : String(error.cause?.message ?? error.message).slice(0, 300),
      } });
      throw error;
    } finally { this.#running.delete(key); }
  }

  async compactRegion(start, end, agent, signal) {
    const entry = this.#entry(agent);
    if (entry.journal.state().pending) return null;
    const session = agent.session;
    const nodes = this.ctx.tokenMeter.measure(session).nodes;
    const first = nodes.findIndex(node => node.seq === start);
    const last = nodes.findIndex(node => node.seq === end);
    if (first < 0 || last < first) return null;
    const minTokens = 512;
    const selected = nodes.slice(first, last + 1);
    const selectedSize = selected.reduce((sum, node) => sum + node.tokens, 0);
    if (selectedSize < minTokens) {
      if (selected.every(node => session.eventAt(node.seq)?.data.source?.kind === 'compact-checkpoint')) return null;
      if (!balancedAfter) return null;
      // Let a token-poor header-only prefix join older history while leaving a recent tail.
      const total = nodes.slice(first).reduce((sum, node) => sum + node.tokens, 0);
      let tail = 0;
      let cutoff = nodes.length - 1;
      while (cutoff > last && tail < Math.max(128, total * 0.16)) tail += nodes[cutoff--].tokens;
      while (cutoff > last && !balancedAfter(session, nodes[cutoff].seq)) cutoff--;
      if (cutoff <= last || nodes.slice(first, cutoff + 1).reduce((sum, node) => sum + node.tokens, 0) < minTokens) return null;
      end = nodes[cutoff].seq;
    }
    return this.#compactSelected(start, end, agent, signal);
  }

  async #compactSelected(start, end, agent, signal) {
    return this.#run(agent, signal, entry => {
      this.#begin(entry, [], [], { start, end });
      return super.compactRegion(start, end, agent, signal);
    });
  }

  async compactNow(agent, signal, sourceCommandId) {
    return this.#run(agent, signal, () => super.compactNow(agent, signal, sourceCommandId));
  }

  async summarize(input, agent, signal) {
    const entry = this.#entry(agent);
    const { journal } = entry;
    const { session } = agent;
    const pending = this.#begin(entry);
    const state = journal.state();
    const hostId = session.snapshotEvents().findLast(event => event.type === 'compaction/start')?.data.compactionId;
    if (!hostId) throw new Error('MEMORY_HOST_TRANSACTION_MISSING');
    const source = journal.backup({ kind: 'input', attempt: pending.id, hostId, messages: input.messages });
    journal.save({ ...state, pending: { ...pending, hostId, source, candidate: null } });
    // 让出一次事件循环，暂停/停止能在这里生效，避免"点了停还在写候选"。
    await new Promise(resolve => setImmediate(resolve));
    signal?.throwIfAborted();
    let previous = [];
    const currentPins = activePins(state);
    let backfillLegacy = false;
    for (const pin of currentPins) {
      try {
        const candidate = journal.load(pin.candidate);
        previous.push(...candidatePages(candidate));
        backfillLegacy ||= candidate.format !== 'readable';
      } catch {
        // 只从备份的原始输入里恢复用户要求，绝不从损坏的摘要里恢复。
        try { previous.push(...recentPages(journal.load(pin.source).messages, [], false)); }
        catch { throw new Error('MEMORY_RECOVERY_BACKUP_INVALID'); }
      }
    }
    if (backfillLegacy) {
      // Legacy checkpoints omitted question answers. Recover them once from audited input backups.
      let recovered = [];
      for (const pin of state.pins) {
        recovered = recentPages(journal.load(pin.source).messages, recovered, false);
      }
      if (state.pins.some(pin => pin.redacted?.length)) {
        recovered = recovered.filter(page => page.kind !== 'answer' || !/\p{N}/u.test(page.text));
      }
      previous = [...recovered, ...previous];
    }
    if (pending.source && pending.hostId !== hostId) {
      try { previous.push(...recentPages(journal.load(pending.source).messages, [], false)); }
      catch { /* 当前宿主区间与既有已核实检查点仍是恢复来源 */ }
    }
    const redacted = new Set(state.pins.flatMap(pin => pin.redacted ?? []));
    const pages = recentPages(input.messages, previous, pending.changes.length > 0, redacted);
    const textFor = items => readableSummary(pending.graph, items);
    const meter = this.ctx.tokenMeter;
    const price = text => meter.estimateMessage({ role: 'user', content: [{ type: 'text', text }] });
    const inputCost = input.messages.filter(message => message.role !== 'system')
      .reduce((sum, message) => sum + meter.estimateMessage(message), 0);
    const limit = Math.min(12000, Math.max(0, inputCost - 75));
    let body = textFor(pages);
    for (let index = pages.length - 1; price(body) > limit && index >= 0; index--) {
      if (pages[index].kind !== 'workflow') continue;
      pages.splice(index, 1);
      body = textFor(pages);
    }
    if (price(body) > limit) throw new Error('MEMORY_MINIMUM_OVER_BUDGET');
    const candidate = journal.backup({ kind: 'candidate', format: 'readable', body, pages, graph: pending.graph }, {
      label: identityOf(agent).label, root: identityOf(agent).root,
    });
    journal.save({ ...state, pending: { ...pending, hostId, source, candidate } });
    signal?.throwIfAborted();
    const { provider, model } = session.requestHeader()?.config ?? {};
    if (!provider || !model) throw new Error('MEMORY_ROUTE_MISSING');
    return { provider, model, summary: [{ type: 'text', text: body }] };
  }
}

export const inject = ['sessionPersistence', 'sessions', 'llm', 'tokenMeter', 'tools', 'systemPrompt'];

export async function apply(ctx, config) {
  // -- 【2026-11-28】父会话谱系：异步预热 + 落账时同步查表 --
  // 约束：账本落盘是【同步】路径（压缩提交那一刻），不能 await。
  //   而 sessionPersistence.stat() 是异步的。
  // 方案：启动时异步把「每个 sessionId 的父会话」扫进 map，落账时纯查表。
  //   表是【缓存】不是真相 —— 查不到就返回空，identityOf 会退回直接父级，
  //   绝不会因为表没建好而写错数据（只会少折叠一两层，面板仍能显示）。
  const parentMap = new Map();   // sessionId -> parentSessionId(''=根)
  try {
    setParentResolver(function (sessionId) {
      const id = String(sessionId || '');
      if (!id) return '';
      // ⚠️ 三态，不能把"还没查到"和"没有父会话"混为一谈：
      //   命中且有父 → 返回父 id；命中且无父 → ''（真根）；
      //   未命中 → '__UNKNOWN__'（让调用方走同步兜底，而不是静默当成根）。
      if (!parentMap.has(id)) {
        // 顺手触发一次补建，下次就准了
        if (!ancestorPending.has(id)) {
          ancestorPending.add(id);
          try {
            const sp = ctx.get ? ctx.get('sessionPersistence') : null;
            if (sp && typeof sp.stat === 'function') {
              Promise.resolve(sp.stat(id)).then((snap) => {
                const h = (snap && snap.header) || {};
                parentMap.set(id, String(h.parentSession || ''));
              }).catch(() => { parentMap.set(id, ''); })
                .finally(() => ancestorPending.delete(id));
            }
          } catch (e) { ancestorPending.delete(id); }
        }
        return '__UNKNOWN__';
      }
      return parentMap.get(id);
    });
  } catch (e) {}
  /** 异步补建谱系表（懒执行、限流、失败静默）。 */
  let lineageTimer = null;
  function scheduleLineageRefresh() {
    if (lineageTimer) return;
    lineageTimer = setTimeout(async function () {
      lineageTimer = null;
      try {
        const sp = ctx.get ? ctx.get('sessionPersistence') : null;
        if (!sp || typeof sp.list !== 'function' || typeof sp.stat !== 'function') return;
        const ids = await sp.list();
        if (!Array.isArray(ids)) return;
        // 只补表里还没有的，避免每次全量重扫
        for (const raw of ids.slice(0, 4000)) {
          const id = String(raw && (raw.id || raw.sessionId || raw) || '');
          if (!id || parentMap.has(id)) continue;
          try {
            const snap = await sp.stat(id);
            const h = (snap && snap.header) || {};
            parentMap.set(id, String(h.parentSession || ''));
          } catch (e) { parentMap.set(id, ''); }
        }
      } catch (e) {
        try { ctx.logger.warn('工程记忆：谱系表补建失败（压缩历史可能只折叠到直接父级）'); } catch (_e) {}
      }
    }, 800);
    try { if (typeof lineageTimer.unref === 'function') lineageTimer.unref(); } catch (e) {}
  }
  scheduleLineageRefresh();
  await ctx.plugin(installAudit);
  await ctx.inject(['engineeringCompactionAudit'], async scope => {
    scope.systemPrompt.section({
      name: 'engineering:memory-v2',
      order: 700,
      text: () => '工程记忆只读。用 engineering_memory_request 查看当前尺寸及依赖，或单独发起更正。听到"我要换尺寸"但缺 ID、值或单位时，先追问。优先用稳定尺寸 ID，例如 shaft.diameter。用户可输入 !尺寸 shaft.diameter = 24 mm。派生值须登记依赖且重新验算，等待更新通过审核再建模。',
    });
    await scope.plugin(GuardedCompaction, config);
  });
}
