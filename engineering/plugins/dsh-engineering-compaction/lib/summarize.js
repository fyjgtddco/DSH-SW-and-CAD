/**
 * summarize.js — 可注入指令的摘要调用（官方 summarizeWithLlm 的等价实现）
 * ==========================================================================
 * 为什么不直接用父类的 summarize()：
 *   @deepseek-ai/dsh-compaction-basic 把 summarizeWithLlm 写在模块内部且【没有
 *   导出】，压缩指令 COMPACTION_INSTRUCTION 也是模块私有常量。要换成 L0–L3
 *   口径，只能重写这一层。
 *
 * 但重写必须严格复刻它的三个关键行为，否则会破坏缓存或压缩事务：
 *   ① 把【会话自己的 system 提示 + 工具声明 + 被压缩区的历史消息】按序回放，
 *      压缩指令作为【最后一条 user 消息】追加 —— 这样本次调用是上一次请求的
 *      真前缀，provider 的 KV cache 能命中。
 *   ② purpose 设为 "compaction"。
 *   ③ 只回传纯文本 blocks；带图片直接判错（父类不允许图片输出）。
 *
 * 与官方实现的唯一差异：指令文本由调用方传入（可注入 L0–L3 规则）。
 *
 * ⚠️ 流式装配【直接复用】官方导出的 BlockAssembler / LlmError，
 *    不自己解析 chunk 协议 —— 否则宿主换版本时这里会静默错位。
 */

/**
 * 装配器与错误类的来源。
 *
 * ── 为什么要动态取 ────────────────────────────────────────────────────────
 * 插件由宿主 loader 加载时，'@deepseek-ai/dsh-llm' 能正常解析（loader 会把
 * 宿主 app.asar 的 node_modules 纳入解析范围）。但同一个文件被【直接 node 跑】
 * （例如本地单测、排障脚本）时，profile 的 node_modules 里并没有 @deepseek-ai，
 * 静态 import 会在模块解析阶段就把整个插件打死。
 * 因此改为动态导入 + 本地等价实现兜底：宿主里用官方实现，脱离宿主也能跑。
 */
let LlmErrorImpl = class LlmErrorFallback extends Error {
  constructor(message, code, options) {
    super(message, options);
    this.name = 'LlmError';
    this.code = code;
  }
};
let BlockAssemblerImpl = null;

try {
  const llm = await import('@deepseek-ai/dsh-llm');
  if (llm && typeof llm.LlmError === 'function') LlmErrorImpl = llm.LlmError;
  if (llm && typeof llm.BlockAssembler === 'function') BlockAssemblerImpl = llm.BlockAssembler;
} catch (e) {
  // 脱离宿主（单测 / 排障）：用下面的本地装配器，行为等价
}

/**
 * 官方 BlockAssembler 的等价实现（仅在拿不到官方实现时使用）。
 * 只处理压缩摘要真正会出现的分片：text-delta / block-end / usage / finish。
 */
class LocalBlockAssembler {
  constructor() {
    this.partials = new Map();
    this.order = [];
    this._usage = undefined;
    this._finish = undefined;
  }
  push(chunk) {
    switch (chunk && chunk.type) {
      case 'block-start':
        if (!this.partials.has(chunk.index)) {
          this.order.push(chunk.index);
          this.partials.set(chunk.index, { blockType: chunk.blockType, text: '' });
        }
        return;
      case 'text-delta':
      case 'reasoning-delta': {
        const p = this._ensure(chunk.index, chunk.type === 'text-delta' ? 'text' : 'reasoning');
        if (p.block) return;
        p.text += chunk.text;
        return;
      }
      case 'block-end': {
        const p = this._ensure(chunk.index, chunk.block && chunk.block.type);
        p.block = chunk.block;
        return;
      }
      case 'usage':
        this._usage = chunk.usage;
        return;
      case 'finish':
        this._finish = chunk.reason || chunk.finish;
        return;
      default:
        return;
    }
  }
  _ensure(index, blockType) {
    let p = this.partials.get(index);
    if (!p) {
      p = { blockType, text: '' };
      this.partials.set(index, p);
      this.order.push(index);
    }
    return p;
  }
  blocks() {
    return this.order.map((index) => {
      const p = this.partials.get(index);
      if (p.block) return p.block;
      if (p.blockType === 'text' || p.blockType === 'reasoning') return { type: p.blockType, text: p.text };
      return { type: p.blockType || 'text', text: p.text || '' };
    });
  }
  get usage() { return this._usage; }
  get finish() { return this._finish || { kind: 'stop' }; }
}

const Assembler = BlockAssemblerImpl || LocalBlockAssembler;
const LlmError = LlmErrorImpl;

/** 把 finish 终态映射成 fail-closed 的错误（与官方 finishError 一致）。 */
function finishError(finish) {
  switch (finish && finish.kind) {
    case 'error':
    case 'aborted':
      return new LlmError(
        (finish.failure && finish.failure.message) || 'summarization failed',
        finish.failure && finish.failure.code,
        finish.failure,
      );
    case 'max-tokens': {
      const e = new Error('summarization truncated at the token cap (incomplete checkpoint)');
      e.code = 'MAX_TOKENS';
      return e;
    }
    default:
      return undefined;
  }
}

/** 取 blocks 并剔除图片（图片必须显式失败，不能悄悄丢）。 */
function summaryText(blocks) {
  if (blocks.some((b) => b && b.type === 'image')) {
    throw new LlmError('compaction summary cannot contain image output', 'UNSUPPORTED_CONTENT');
  }
  return blocks.filter((b) => b && b.type === 'text');
}

/**
 * 调模型写摘要。
 *
 * @param {object} ctx        cordis 上下文（需 ctx.llm）
 * @param {object} config     已解析的压缩配置
 * @param {object} input      父类给的 { messages, tools? }
 * @param {object} agent      agent（提供路由模型与会话 id）
 * @param {AbortSignal} signal
 * @param {string} instruction 压缩指令（最后一条 user 消息）
 * @returns {Promise<{summary:Array, rawOutput:Array, llmStreamCall:true, provider:string, model:string, maxTokens:any, usage:any}>}
 */
export async function summarizeWithLlm(ctx, config, input, agent, signal, instruction) {
  // ── 选路由模型：显式配置 > 最近一次已记录请求 > AgentOptions ─────────
  let latest;
  try { latest = agent && agent.session ? agent.session.requestHeader()?.config : undefined; } catch (e) { latest = undefined; }

  const configured = config && config.summarizationProvider
    ? { provider: config.summarizationProvider, model: config.summarizationModel }
    : undefined;
  const agentTarget = (agent && agent.options && agent.options.provider && agent.options.model)
    ? { provider: agent.options.provider, model: agent.options.model }
    : undefined;
  const target = configured || latest || agentTarget;
  if (!target) {
    throw new Error('no provider/model available for summarization: set both summarization fields, route one request, or set both AgentOptions fields');
  }

  const baseMessages = (input && Array.isArray(input.messages)) ? input.messages : [];
  const tools = (input && input.tools !== undefined) ? input.tools : undefined;

  // ── 【CMP-OVERSIZE 修复】单请求 token 上限保护 ───────────────────────
  // 现场（2026-11-10 会话 57278639）：14 次压缩全部失败：
  //   "buddy: 内容过长，请精简或新建任务 prompt is too long:
  //    100001 tokens > 100000 maximum"
  // 根因：摘要请求把【整段历史（≈100K tokens）】一次性发给模型，超过网关
  //   100K 的 prompt 上限 —— 压缩永远跑不完，会话一路涨到"内容过长"。
  // 修法：按字符预算分段（保守 80K chars ≈ 最坏 1 char/token 也留余量），
  //   每段单独摘要；段摘要再合并成最终 checkpoint。若某次请求仍被判超长，
  //   自动二分重试（递归），绝不把"压缩失败"当默认结局。
  let budget = (config && Number.isFinite(config.chunkChars) && config.chunkChars > 0)
    ? Number(config.chunkChars)
    : (Number(process.env.DSH_ENG_COMPACTION_CHUNK_CHARS) || DEFAULT_CHUNK_CHARS);
  budget = Math.max(8000, Math.floor(budget));

  const total = totalChars(baseMessages);
  if (total <= budget) {
    // 单段：与旧行为完全一致（保住 provider 的 KV 前缀缓存）
    const r = await callOnce(ctx, config, target, baseMessages, tools, agent, signal, instruction);
    const rawOutput = r.blocks;
    const summary = summaryText(rawOutput);
    if (!summary.some((b) => String(b.text || '').trim().length > 0)) {
      throw new Error('summarization produced no text summary content');
    }
    return {
      summary,
      rawOutput,
      llmStreamCall: true,
      provider: target.provider,
      model: target.model,
      ...(config && config.maxTokens !== undefined ? { maxTokens: config.maxTokens } : {}),
      ...(r.usage !== undefined ? { usage: r.usage } : {}),
      chunked: false,
      chunk_count: 1,
      input_chars: total,
    };
  }

  // ── 分段摘要 ────────────────────────────────────────────────────────
  const chunks = splitMessages(baseMessages, budget);
  const segTexts = [];
  let usage;
  for (let i = 0; i < chunks.length; i += 1) {
    const chunkInstruction = instruction + '\n\n' + [
      '## SEGMENT MODE',
      'The conversation above is ONLY segment ' + (i + 1) + ' of ' + chunks.length + ' of a longer history.',
      'Summarize THIS segment only; never invent content for other segments.',
      'Keep every number, tolerance, material grade, path, file name and parameter verbatim.',
      'Output terse Chinese bullets covering: 已完成 / 工程决策 / 失败条件 / 文件路径. Drop reasoning and chatter.',
    ].join('\n');
    const r = await summarizeChunk(ctx, config, target, chunks[i], agent, signal, chunkInstruction, budget, 0);
    usage = r.usage !== undefined ? r.usage : usage;
    if (r.text) segTexts.push('--- 历史分段 ' + (i + 1) + '/' + chunks.length + ' ---\n' + r.text);
  }

  // ── 合并：把分段摘要 + 完整 L0–L3 指令再压一次 ──────────────────────
  const mergedText = segTexts.join('\n\n');
  const final = await mergeSummaries(ctx, config, target, mergedText, agent, signal, instruction, budget, 0);
  usage = final.usage !== undefined ? final.usage : usage;
  const summary = summaryText(final.blocks);
  if (!summary.some((b) => String(b.text || '').trim().length > 0)) {
    throw new Error('summarization produced no text summary content');
  }
  return {
    summary,
    rawOutput: final.blocks,
    llmStreamCall: true,
    provider: target.provider,
    model: target.model,
    ...(config && config.maxTokens !== undefined ? { maxTokens: config.maxTokens } : {}),
    ...(usage !== undefined ? { usage } : {}),
    chunked: true,
    chunk_count: chunks.length,
    input_chars: total,
  };
}

/** 单次可安全容纳的消息字符数（可用 config.chunkChars / 环境变量覆盖）。 */
const DEFAULT_CHUNK_CHARS = 64000;

function isPromptTooLong(err) {
  const msg = String((err && err.message) || err || '');
  return /prompt is too long|too many tokens|context length|maximum context|tokens?\s*>\s*\d+/i.test(msg)
    || (err && (err.code === 'CONTEXT_TOO_LONG' || err.code === 'PROMPT_TOO_LONG'));
}

function messageChars(m) {
  try {
    if (!m || typeof m !== 'object') return 0;
    const c = m.content;
    if (typeof c === 'string') return c.length;
    if (Array.isArray(c)) {
      let n = 0;
      for (const b of c) {
        if (!b || typeof b !== 'object') continue;
        if (typeof b.text === 'string') n += b.text.length;
        else if (b.type === 'tool-call') n += 64 + String(b.name || '').length;
        else if (b.type === 'tool-result') n += 64 + String(b.content !== undefined ? b.content : b.output).length;
        else n += 64;
      }
      return n + String(m.role || '').length + 8;
    }
  } catch (e) {}
  return 0;
}

function totalChars(messages) {
  let n = 0;
  for (const m of (messages || [])) n += messageChars(m);
  return n;
}

/** 把一条超大消息拆成多条纯文本消息（按行边界切，尽量保留结构）。 */
function splitOversized(m, limit) {
  let text = '';
  try {
    const c = m && m.content;
    if (typeof c === 'string') text = c;
    else if (Array.isArray(c)) {
      const parts = [];
      for (const b of c) {
        if (!b || typeof b !== 'object') continue;
        if (typeof b.text === 'string') parts.push(b.text);
        else if (b.type === 'tool-call') parts.push('[tool-call ' + String(b.name || '') + '] ' + safeJson(b.arguments));
        else if (b.type === 'tool-result') parts.push('[tool-result] ' + safeJson(b.content !== undefined ? b.content : b.output));
      }
      text = parts.join('\n');
    }
  } catch (e) { text = ''; }
  if (text.length <= limit) return [m];
  const out = [];
  const role = String((m && m.role) || 'user');
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + limit);
    if (end < text.length) {
      const nl = text.lastIndexOf('\n', end);
      if (nl > start + Math.floor(limit * 0.5)) end = nl + 1;
    }
    out.push({ role, content: [{ type: 'text', text: text.slice(start, end) }] });
    start = end;
  }
  return out.length ? out : [m];
}

/** 按字符预算切分消息列表（保持顺序；超长单条先拆）。 */
function splitMessages(messages, budget) {
  const limit = Math.max(2000, Math.floor(budget));
  const flat = [];
  for (const m of (messages || [])) {
    for (const p of splitOversized(m, limit)) flat.push(p);
  }
  const chunks = [];
  let cur = [];
  let curChars = 0;
  for (const m of flat) {
    const c = messageChars(m);
    if (cur.length && curChars + c > limit) {
      chunks.push(cur);
      cur = [];
      curChars = 0;
    }
    cur.push(m);
    curChars += c;
  }
  if (cur.length) chunks.push(cur);
  return chunks.length ? chunks : [[]];
}

/** 单次调用（消息 + 指令作为最后一条 user）。返回原始 blocks/usage。 */
async function callOnce(ctx, config, target, messages, tools, agent, signal, instruction) {
  const assembler = new Assembler();
  const all = [
    ...(messages || []),
    { role: 'user', content: [{ type: 'text', text: String(instruction || '') }] },
  ];
  const options = {
    provider: target.provider,
    model: target.model,
    messages: all,
    ...(tools !== undefined ? { tools: [...tools] } : {}),
    ...(config && config.maxTokens !== undefined ? { maxTokens: config.maxTokens } : {}),
    sessionId: agent && agent.session ? agent.session.id : undefined,
    purpose: 'compaction',
    ...(signal !== undefined ? { signal } : {}),
  };
  try {
    if (agent && agent.session && typeof agent.session.toolHistory === 'function') {
      options.toolHistory = agent.session.toolHistory();
    }
  } catch (e) {}
  for await (const chunk of ctx.llm.stream(options)) assembler.push(chunk);
  const error = finishError(assembler.finish);
  if (error !== undefined) throw error;
  return { blocks: assembler.blocks(), usage: assembler.usage, finish: assembler.finish };
}

/** 摘要一段消息；遇到"prompt too long"自动二分重试。 */
async function summarizeChunk(ctx, config, target, messages, agent, signal, instruction, budget, depth) {
  try {
    const r = await callOnce(ctx, config, target, messages, undefined, agent, signal, instruction);
    const blocks = summaryText(r.blocks);
    return { text: blocks.map((b) => String(b.text || '')).join('\n').trim(), usage: r.usage };
  } catch (err) {
    if (!isPromptTooLong(err) || depth >= 3) throw err;
    const half = Math.max(2000, Math.floor(budget / 2));
    const parts = splitMessages(messages, half);
    if (parts.length <= 1) throw err;
    const outs = [];
    for (let k = 0; k < parts.length; k += 1) {
      const sub = await summarizeChunk(ctx, config, target, parts[k], agent, signal,
        instruction + '\n\n（本段为长历史的第 ' + (k + 1) + '/' + parts.length + ' 个子段）', half, depth + 1);
      if (sub.text) outs.push(sub.text);
    }
    return { text: outs.join('\n'), usage: undefined };
  }
}

/** 合并分段摘要；合并请求本身超限时继续分段（递归深度受限）。 */
async function mergeSummaries(ctx, config, target, mergedText, agent, signal, instruction, budget, depth) {
  const msgs = [{ role: 'user', content: [{ type: 'text', text: String(mergedText || '') }] }];
  if (totalChars(msgs) + String(instruction || '').length + 200 <= budget || depth >= 2) {
    return callOnce(ctx, config, target, msgs, undefined, agent, signal, instruction);
  }
  const parts = splitMessages(msgs, Math.max(2000, Math.floor(budget / 2)));
  const outs = [];
  for (let i = 0; i < parts.length; i += 1) {
    const r = await callOnce(ctx, config, target, parts[i], undefined, agent, signal,
      instruction + '\n\n（合并分段：第 ' + (i + 1) + '/' + parts.length + ' 段，只归纳本段，保留全部数字与路径）');
    outs.push(summaryText(r.blocks).map((b) => String(b.text || '')).join('\n'));
  }
  const merged2 = [{ role: 'user', content: [{ type: 'text', text: outs.join('\n\n') }] }];
  if (depth + 1 >= 2 || totalChars(merged2) + String(instruction || '').length + 200 <= budget) {
    return callOnce(ctx, config, target, merged2, undefined, agent, signal, instruction);
  }
  return mergeSummaries(ctx, config, target, outs.join('\n\n'), agent, signal, instruction, budget, depth + 1);
}
