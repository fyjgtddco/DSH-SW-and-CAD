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

  const assembler = new Assembler();
  const baseMessages = (input && Array.isArray(input.messages)) ? input.messages : [];
  const messages = [
    ...baseMessages,
    { role: 'user', content: [{ type: 'text', text: String(instruction || '') }] },
  ];

  const options = {
    provider: target.provider,
    model: target.model,
    messages,
    ...(input && input.tools !== undefined ? { tools: [...input.tools] } : {}),
    ...(config && config.maxTokens !== undefined ? { maxTokens: config.maxTokens } : {}),
    sessionId: agent && agent.session ? agent.session.id : undefined,
    purpose: 'compaction',
    ...(signal !== undefined ? { signal } : {}),
  };
  // toolHistory 让投影能推迟/保留工具声明（与官方一致）
  try {
    if (agent && agent.session && typeof agent.session.toolHistory === 'function') {
      options.toolHistory = agent.session.toolHistory();
    }
  } catch (e) {}

  for await (const chunk of ctx.llm.stream(options)) assembler.push(chunk);

  const error = finishError(assembler.finish);
  if (error !== undefined) throw error;

  const rawOutput = assembler.blocks();
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
    ...(assembler.usage !== undefined ? { usage: assembler.usage } : {}),
  };
}
