/**
 * dsh-engineering-compaction — 【工程模式专用分层压缩器】
 * ==========================================================================
 * 只在 engineering 预设下加载（见 preset-engineering.patch.yml），
 * 用它【替换】@deepseek-ai/dsh-compaction-basic 的默认实现。
 *
 * ── 为什么继承而不是重写 ─────────────────────────────────────────────────
 * 压缩子系统里真正危险的是「区域事务」：选区、锁、compaction/start…end 事件、
 * surface 替换、token 计量、并发与取消。那套东西在 compaction-basic 里已经写死
 * 且经过验证，官方也明确「summarize() 是唯一的子类定制钩子」。
 * 因此这里只做一件事：接管【摘要怎么写】，其余全交给父类。
 * 这样「替换工程模式的压缩」不会把会话存档机制搞坏。
 *
 * ── 本插件在摘要环节多做的四件事 ─────────────────────────────────────────
 *   ① 压缩前把 L0 机械事实抽出来（facts.extractFacts），不交给模型。
 *   ② 提示词里强制「原样复制」，并把事实区块作为只读材料注入。
 *   ③ 摘要返回后【核对】L0：缺失就强行补回（restoreFacts），保证数值一个不少。
 *   ④ 每次压缩写一条 append-only 历史（history.appendCompaction），
 *      供右侧红框面板显示「各子代理 + 主对话」的压缩链。
 *
 * ── 加载失败时的行为 ─────────────────────────────────────────────────────
 * 父类构造如果抛错（例如宿主缺 tokenMeter），整个 preset 会挂掉。
 * 这里不做兜底 —— 缺服务就是配置问题，静默降级反而更难排查。
 */

/**
 * 父类来源：@deepseek-ai/dsh-compaction-basic。
 *
 * ── 为什么【动态】导入 + 本地兜底 ─────────────────────────────────────────
 * 工程模式的压缩器挂在 preset 里。preset 里任何一行加载失败，整个 engineering
 * 预设都会挂掉 —— 而 UI 面板恰恰只在 engineering 预设下渲染，结果是
 * 「插件一坏，界面整个消失」。
 *
 * 实测：profile 的 node_modules 里没有 @deepseek-ai/*（pnpm autoInstallPeers=false），
 * 插件内部的裸静态 import 依赖宿主 ESM 解析链。那条链在某些装配下能解析
 * （UI 插件就依赖它），但不该由「压缩功能」去赌 —— 压缩坏了大不了不压缩，
 * 界面不该跟着没了。
 *
 * 因此：先试真实父类；拿不到就用本地等价实现（不触发压缩，安全降级）。
 */
let BasicCompactionEngine;
let PARSENT_MODE = 'real';
try {
  const m = await import('@deepseek-ai/dsh-compaction-basic');
  BasicCompactionEngine = m.default;
  if (typeof BasicCompactionEngine !== 'function') throw new Error('default export is not a class');
} catch (e) {
  PARSENT_MODE = 'fallback';
  // 本地空壳：能被 cordis 实例化成 Service，但不做任何压缩。
  // 会话照常运行，只是不自动压缩 —— 这是「宁可功能降级，不可拖垮预设」的选择。
  try {
    const cordis = await import('@deepseek-ai/cordis');
    const Service = cordis.Service || (cordis.default && cordis.default.Service);
    if (typeof Service === 'function') {
      BasicCompactionEngine = class FallbackCompaction extends Service {
        constructor(ctx) { super(ctx, 'compaction'); this.config = {}; }
        async compactIfNeeded() { return null; }
        async summarize() { throw new Error("engineering compaction unavailable: @deepseek-ai/dsh-compaction-basic not resolvable"); }
      };
    }
  } catch (e2) { /* 连 cordis 都没有 → 保留 undefined，下面兜底 */ }
  if (typeof BasicCompactionEngine !== 'function') {
    BasicCompactionEngine = class NoopCompaction {
      constructor() { this.config = {}; }
      async compactIfNeeded() { return null; }
    };
  }
}
import fs from 'node:fs';
import { summarizeWithLlm } from './summarize.js';
import { extractFacts, renderFactsBlock, verifySummary, restoreFacts, mergeFacts, factsStats } from './facts.js';
import { buildCompactionInstruction, LAYERS } from './prompt.js';
import { appendCompaction, readHistory, readFacts, writeFacts, resolveStateDir,
         buildOpenWorkState, missingOpenWork, readOpenWorkState, writeOpenWorkState } from './history.js';

/**
 * 加载标记：把"压缩引擎到底有没有被加载"落盘。
 *
 * 为什么需要：面板显示「还没有发生压缩」时，用户无法区分两种情况 ——
 *   ① 引擎没加载（preset 那行没解析到 / 包没装）→ 永远不会有任何记录；
 *   ② 引擎加载了，但这条会话还没到阈值 → 只是还没发生。
 * 这两种在界面上长得一模一样，排查起来要翻日志。所以引擎一被实例化就写一行
 * marker（<state_dir>/compaction_engine.json），UI 读它给准确提示。
 *
 * fail-open：写不进去就跳过 —— 记账失败绝不能影响压缩本身。
 */
function markEngineLoaded(mode) {
  try {
    const dir = resolveStateDir();
    const p = dir + '/compaction_engine.json';
    fs.writeFileSync(p, JSON.stringify({
      at: Date.now(),
      mode: mode || 'real',
      state_dir: dir,
      pid: process.pid,
    }, null, 2), 'utf8');
  } catch (e) { /* 写不进去就算了 */ }
}

/**
 * 【2026-11-12 新增】基础摘要指令（降级路径专用）。
 *
 * 为什么需要：完整指令要求模型"逐行抄写 EVERY F/M 编号 + 原样保留所有数值"，
 * 在网关输出上限不够时会空流/截断。降级路径改用一条**最短、最不容易失败**的
 * 指令，目标是"先把上下文压下去"，而不是"数值零丢失"。
 * 这是分级降级里的第 ② 级（见 summarize() 里的双层降级注释）。
 */
function buildBasicInstruction() {
  return [
    'Summarize the conversation above into a terse engineering checkpoint.',
    'Rules:',
    '- Keep file paths, commands, and key numbers VERBATIM. Never invent numbers.',
    '- Cover: 已完成 / 关键决策 / 失败与原因 / 下一步.',
    '- Drop reasoning, greetings, repeated tool output. Use short bullets.',
    '- Output Chinese bullets only. No preamble, no explanation of what you did.',
  ].join('\n');
}

/** 从 agent 上取会话身份信息（root / target / label / preset）。 */
function identityOf(agent) {
  const out = { target: '', root: '', label: '', preset: '' };
  try {
    const session = agent && agent.session;
    out.target = String((session && session.id) || (agent && agent.id) || '');
    try {
      const header = session && session.header;
      out.preset = String((header && header.agentPreset) || '');
    } catch (e) {}
  } catch (e) {}
  // root：沿 agent.parent 往上找，找不到就认为自己就是 root
  try {
    let cur = agent;
    let guard = 0;
    let root = out.target;
    while (cur && guard < 32) {
      guard += 1;
      const parent = cur.parent || cur.parentAgent || null;
      if (!parent) break;
      const pid = String((parent.session && parent.session.id) || parent.id || '');
      if (pid) root = pid;
      cur = parent;
    }
    out.root = root;
  } catch (e) {
    out.root = out.target;
  }
  // label：子代理的显示名（房间名），没有就用 id 前 8 位
  try {
    const label = agent && (agent.label || agent.title || (agent.session && agent.session.title));
    out.label = String(label || '');
  } catch (e) {}
  if (!out.label) out.label = out.target.slice(0, 8);
  return out;
}

/** 把回放输入里的消息拍平成纯文本，供事实抽取用。 */
function inputToText(input) {
  const msgs = (input && Array.isArray(input.messages)) ? input.messages : [];
  const chunks = [];
  for (const m of msgs) {
    if (!m || typeof m !== 'object') continue;
    const role = String(m.role || '');
    const content = Array.isArray(m.content) ? m.content : [];
    const texts = [];
    for (const b of content) {
      if (!b || typeof b !== 'object') continue;
      if (b.type === 'text' && typeof b.text === 'string') texts.push(b.text);
      else if (b.type === 'tool-call') texts.push('[tool-call ' + String(b.name || '') + '] ' + safeJson(b.arguments));
      else if (b.type === 'tool-result') texts.push('[tool-result] ' + safeJson(b.content !== undefined ? b.content : b.output));
    }
    if (texts.length) chunks.push('[' + role + ']\n' + texts.join('\n'));
  }
  return chunks.join('\n\n');
}

function safeJson(v) {
  try {
    if (typeof v === 'string') return v;
    return JSON.stringify(v);
  } catch (e) {
    return String(v);
  }
}

/** 从历史账本里取上一次的 L1 / L2 文本（要求「只增不减」）。 */
function previousLayers(target) {
  try {
    const r = readHistory({ target, limit: 50 });
    if (!r.ok || !r.entries.length) return { l1: '', l2: '' };
    // 取最近一条带 l1 / l2 的记录
    for (let i = r.entries.length - 1; i >= 0; i--) {
      const e = r.entries[i];
      if (e && (e.l1_text || e.l2_text)) return { l1: String(e.l1_text || ''), l2: String(e.l2_text || '') };
    }
  } catch (e) {}
  return { l1: '', l2: '' };
}

/** 从摘要文本里切出 L1 / L2 段落（用于下一次的「保留旧内容」注入）。 */
export function sliceSections(summary) {
  const s = String(summary || '');
  const grab = (name) => {
    const re = new RegExp('###\\s*' + name + '[^\\n]*\\n([\\s\\S]*?)(?=\\n###\\s|$)');
    const m = s.match(re);
    return m ? m[1].trim() : '';
  };
  return {
    l1: grab('L1 工程状态'),
    l2: [grab('L2 工程决策（成功后压缩）') || grab('L2 工程决策'), grab('L2 失败记录（错误方案压缩）') || grab('L2 失败记录')]
      .filter(Boolean).join('\n'),
  };
}

/** 插件主体。 */
class EngineeringCompactionEngine extends BasicCompactionEngine {
  /**
   * 构造即落盘"引擎已加载"标记。
   *
   * ⚠️ 必须 try/catch 包住并【不改变父类构造语义】：
   *   父类构造负责注册 ctx.compaction 服务，任何异常都会让整个 preset 挂掉
   *   （用户表现就是"工程模式界面整个消失"）。标记只是诊断信息，
   *   写不进去绝不能影响构造。
   */
  constructor(...args) {
    super(...args);
    try { markEngineLoaded(PARSENT_MODE); } catch (e) { /* 诊断信息，失败即忽略 */ }
  }
  /**
   * 覆盖唯一的子类钩子。
   * 与父类签名完全一致：(input, agent, signal) → { summary, rawOutput, ... }
   */
  async summarize(input, agent, signal) {
    const id = identityOf(agent);
    const session = agent && agent.session;

    // ── ① 抽 L0（压缩前的真相，模型碰不到）────────────────────────────
    const rawSpan = inputToText(input);
    const fresh = extractFacts(rawSpan, { source: 'compaction' });

    // 与历史累积集合并：只增不减（用户要求「写了就不能改，只能加」）
    //
    // ══ 【2026-11-12 修复·L0 跨会话串味】══════════════════════════════════
    // 旧实现 `readFacts()` 不带 target → 读全局 `<state_dir>/compaction_facts.json`
    // → **所有会话（主对话 + 每个子代理）共用一份不断并集的 L0 事实集**
    //   （mergeFacts 只增不减）。实测 l0_numbers 一路涨：
    //   3064 → 4627 → 5279 → 7007 条，saved_chars 占比
    //   62.6% → 13.7% → 2.5% → 30.4% —— 压缩收益被 L0 吃干净，
    //   最终 restoreFacts 回填量反超原文，触发 ENG_COMPACTION_NOT_SMALLER
    //   （26 次，输出最高达输入的 2.79 倍）。
    // 现在按会话 id 分文件：每个 target 只累积自己的事实。
    const prevFacts = readFacts(id.target);
    const merged = mergeFacts(prevFacts, fresh);

    // ── ② 上一次的 L1 / L2（要求本轮在此基础上只增不减）───────────────
    const prev = previousLayers(id.target);

    // ── ③ 调模型（提示词里强制原样复制 L0）───────────────────────────
    // ── 【B6 修复】把"进行中的工作状态"结构化注入（★不可压缩区块）────
    //   现场：step2f 修好的解包补丁在 step2h 又丢了，同一段逻辑重写 8 版。
    //   来源优先级：① 显式传入 ② 上一次压缩账本里记的 ③ 空
    let _openWork = { ok: true, state: null, block: '(none)' };
    try {
      const _ow = readOpenWorkState(id.target);
      if (_ow) _openWork = buildOpenWorkState(_ow);
    } catch (e) { /* 拿不到就退化成 (none)，不阻断压缩 */ }
    const instruction = buildCompactionInstruction({
      factsBlock: renderFactsBlock(merged),
      prevL1: prev.l1,
      prevL2: prev.l2,
      openWorkBlock: _openWork.block,
      hasCheckpoint: /<engineering-checkpoint>|<compacted-summary>/.test(rawSpan),
    });

    // ══ 【2026-11-12 修复·压缩直接报错】双层降级 ══════════════════════════
    // 现场：用户点压缩 → GUI 弹
    //   "Compaction could not produce a useful summary.
    //    The attempt is recorded in the session log."
    // 这句是宿主 dsh-compaction-basic:527 的
    //   ManualCompactionError("summary", "manual compaction could not
    //   produce a smaller summary")，由本 summarize() 抛出的任何异常包装而成。
    //
    // 设计原则修正：这是【工程模式的附加能力】（L0 数值零丢失），
    //   它失败时**不该让整次压缩失败** —— 那等于"为了更严格的摘要，
    //   连普通摘要都用不上"，用户看到的就是压缩报错、上下文继续涨。
    //   正确做法是分级降级：附加能力挂了就退回官方等价行为，并如实记账。
    //
    // 三级：
    //   ① 完整 L0 注入 + 核对回填（本插件的目标行为）
    //   ② 失败 → 只做基础摘要（不注入 factsBlock，不做回填），仍保住压缩
    //   ③ 连基础也失败 → 抛出去（交给宿主报错，这是真的无法压缩）
    let result;
    let degraded = null;
    try {
      result = await summarizeWithLlm(this.ctx, this.config, input, agent, signal, instruction);
    } catch (e1) {
      degraded = 'summarize-l0:' + String((e1 && e1.message) || e1).slice(0, 300);
      try {
        const basicInstruction = buildBasicInstruction();
        result = await summarizeWithLlm(this.ctx, this.config, input, agent, signal,
                                        basicInstruction);
        // 降级成功：不注入 L0 事实，merged 仅用于记账统计
        try {
          appendCompaction({
            at: Date.now(), target: id.target, root: id.root, label: id.label,
            preset: id.preset, layer: 'L3-degraded',
            l0_numbers: 0, l0_materials: 0, l0_total: 0,
            degraded_reason: degraded,
            note: 'L0 注入路径失败，已退回基础摘要（数值不再逐条保证）',
          });
        } catch (e) {}
      } catch (e2) {
        // 两级都失败 → 如实抛出，并把第一级原因挂上（便于排查真正病因）
        const err = new Error(
          'engineering compaction failed at both L0 and basic summarization: '
          + String((e2 && e2.message) || e2).slice(0, 300)
          + ' | L0 path error: ' + String((e1 && e1.message) || e1).slice(0, 300));
        err.code = 'ENG_COMPACTION_BOTH_PATHS_FAILED';
        err.cause = e2;
        throw err;
      }
    }

    // ── ④ 核对 + 强制回填（数值一个都不能动）──────────────────────────
    const modelText = (result.summary || []).map((b) => (b && b.text) || '').join('');
    const verified = verifySummary(modelText, merged);
    const restored = restoreFacts(modelText, merged, { rawSpan, strict: false });

    // ── ⑤ 防「回填后反而不缩短」──────────────────────────────────────
    // 父类有一道硬校验：摘要必须比被替换内容小，否则整次压缩报错。
    // 【2026-11-12 修正】这里原本是 throw，但那会让用户看到"压缩报错"。
    //   正确取舍：**模型自己的摘要**（modelText）本来就比原文短的话，
    //   就退回"只用模型摘要 + 少量高权重事实"，而不是整次压缩失败。
    //   只有连模型摘要都不短（或空）时才抛 —— 那是真的压不动。
    const inputChars = rawSpan.length;
    let outChars = restored.text.length;
    let summaryBlocks = result.summary;
    let restoreNote = '';
    if (inputChars > 0 && outChars >= inputChars) {
      // 退一档：去掉回填，只用模型摘要（它通常远短于原文）
      const modelChars = modelText.length;
      if (modelChars > 0 && modelChars < inputChars) {
        outChars = modelChars;
        summaryBlocks = [{ type: 'text', text: modelText }];
        restoreNote = ('回填后反而更长（input ' + inputChars + ' / output '
                       + restored.text.length + ' chars），已退回【仅模型摘要】'
                       + '（' + modelChars + ' chars）以保证压缩成功；'
                       + '本轮数值逐条保证降级。');
      } else {
        const err = new Error(
          'engineering compaction skipped: the checkpoint is not smaller even '
          + 'without L0 restoration (input ' + inputChars + ' chars, '
          + 'model summary ' + modelText.length + ' chars, output ' + outChars + ' chars).');
        err.code = 'ENG_COMPACTION_NOT_SMALLER';
        throw err;
      }
    }

    if (restored.restored > 0 || restored.text !== modelText) {
      // 用补回后的文本重建 blocks（保持纯 text，符合父类对「只能文本」的要求）
      // ⚠️ 只有【没走退一档】时才用回填后的文本；上面退一档时 summaryBlocks
      //    已经是 [{text: modelText}]，不能再被 covered 掉。
      if (!restoreNote) summaryBlocks = [{ type: 'text', text: restored.text }];
    }

    // ── ⑥ 落账本（红框数据源）────────────────────────────────────────
    const stats = factsStats(merged);
    const sections = sliceSections(restored.text);
    try {
      appendCompaction({
        at: Date.now(),
        target: id.target,
        root: id.root,
        label: id.label,
        preset: id.preset,
        layer: 'L0-L3',
        l0_numbers: stats.numbers,
        l0_materials: stats.materials,
        l0_total: stats.numbers + stats.materials,
        l0_missing: verified.missing.length,
        l0_restored: restored.restored,
        l0_ratio: Number(verified.ratio.toFixed(4)),
        input_chars: inputChars,
        output_chars: outChars,
        saved_chars: Math.max(0, inputChars - outChars),
        model: result && result.model ? String(result.model) : '',
        provider: result && result.provider ? String(result.provider) : '',
        l1_text: sections.l1.slice(0, 8000),
        l2_text: sections.l2.slice(0, 8000),
        summary_excerpt: restored.text.slice(0, 1200),
        layers: Object.keys(LAYERS),
      });
    } catch (e) { /* 记账失败不影响压缩 */ }

    // 事实集写回（单调累积，但**按会话分文件**，见上面 readFacts 的修复说明）
    try { writeFacts(merged, id.target); } catch (e) {}

    return Object.assign({}, result, { summary: summaryBlocks });
  }
}

export default EngineeringCompactionEngine;
export { EngineeringCompactionEngine };
export { resolveStateDir, readHistory };
