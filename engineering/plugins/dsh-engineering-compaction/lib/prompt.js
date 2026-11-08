/**
 * prompt.js — 分层压缩指令（L0–L3）与失败/成功方案的压缩模板
 * ==========================================================================
 * 用户给的四条规则，逐条落在这里（这是唯一改动提示词的地方，便于审阅）：
 *
 *   1. 分四层压缩
 *      L0 机械事实层：尺寸/材料/配合/公差/约束/机械关系 —— 无损编码，直接复制，
 *                     不能压缩。→ 由 facts.js 在模型之外单独保管，并强制回填。
 *      L1 工程状态层：已完成零件、装配状态、特征树、当前进度 —— 只结构化更新。
 *      L2 工程决策层：为什么选这个方案、为什么改某个结构 —— 可提炼，但不得丢失
 *                     有效决策；错误只简单提一下。且要求「无论压缩多少次，
 *                     大部分 L2 都要在」→ 用 L2 摘要累积 + 合并实现。
 *      L3 执行过程层：思考过程、重复操作、无效尝试、冗余交流 —— 除用户关键要求
 *                     外全删。
 *
 *   2. 错误方案压缩：AI 尝试了什么 → 为什么失败 → 失败条件是什么 → 下一步不能
 *      再做什么。且「禁止重复」要针对【失败条件】而不是方案名。
 *
 *   3. 成功方案压缩：具体操作顺序 → 哪些参数不能改 → 哪些步骤可复用 → 哪些步骤
 *      依赖之前的特征 → 验证成功时的模型版本。字少但不丢关键内容。
 *
 *   4. L0–L2 第一次压缩就要尽量短，说到重点；但「写了就不能改，只能加，不能减，
 *      不能删」。→ 由 history.js 的单调累积 + 本文件的「保留旧内容」指令共同保证。
 */

/** 分层说明（供 UI 与日志复用）。 */
export const LAYERS = {
  L0: { id: 'L0', name: '机械事实层', keep: 'lossless', note: '尺寸/材料/配合/公差/约束/机械关系' },
  L1: { id: 'L1', name: '工程状态层', keep: 'structured', note: '已完成零件/装配状态/特征树/进度' },
  L2: { id: 'L2', name: '工程决策层', keep: 'condense', note: '方案取舍理由/结构修改原因' },
  L3: { id: 'L3', name: '执行过程层', keep: 'drop', note: '思考过程/重复操作/无效尝试/冗余交流' },
};

/**
 * 构建压缩指令。作为【最后一条 user 消息】追加在回放对话之后
 * （与 compaction-basic 的做法一致：保住 provider 的 KV 前缀缓存）。
 *
 * @param {object} opts
 *   factsBlock  由 facts.renderFactsBlock() 生成的 L0 只读区块
 *   prevL2      上一次的 L2 工程决策摘要（要求模型在此基础上【只增不减】）
 *   prevL1      上一次的 L1 工程状态（要求结构化更新）
 *   hasCheckpoint 原文里是否已存在压缩检查点
 */
export function buildCompactionInstruction(opts) {
  const o = opts || {};
  const factsBlock = String(o.factsBlock || '');
  const prevL2 = String(o.prevL2 || '').trim();
  const prevL1 = String(o.prevL1 || '').trim();

  const parts = [];
  parts.push('You are now the compaction engine for a MECHANICAL ENGINEERING agent (CAD / SolidWorks).');
  parts.push('Condense the conversation ABOVE into a layered checkpoint so another model can resume the design work with zero loss of engineering data.');
  parts.push('');
  parts.push('## ABSOLUTE RULES (violating any of them fails the compaction)');
  parts.push('1. NEVER change, round, convert, merge or drop any number. Every numeric value, tolerance, material grade and dimension is immutable.');
  parts.push('2. Every L0 item listed in the FACTS block below MUST appear verbatim in your output, including its F-number.');
  parts.push('3. Output ONLY the checkpoint text. Do not call tools. Do not mention this summarization request.');
  parts.push('');

  if (factsBlock) {
    parts.push('## L0 — MACHINE FACTS (copied from the transcript; reproduce verbatim)');
    parts.push(factsBlock);
    parts.push('');
  }

  parts.push('## Required output structure (keep every section, in order; write "(none)" when empty)');
  parts.push('');
  parts.push('### L0 机械事实');
  parts.push('- Copy EVERY F-number and M-number above, verbatim, one per line. No edits, no re-ordering of digits, no unit changes.');
  parts.push('- Also copy any mechanical relationship stated in the transcript (配合/约束/传动比/装配基准), verbatim.');
  parts.push('');
  parts.push('### L1 工程状态');
  parts.push('- 已完成零件 / 正在建 / 未开始，各自的状态与文件路径');
  parts.push('- 装配状态、特征树关键节点、当前进度');
  parts.push('- Use structured bullets: object → state → evidence (file/report path).');
  if (prevL1) {
    parts.push('- PREVIOUS L1 (update it structurally; do not drop a line unless the transcript proves it is obsolete):');
    parts.push(prevL1);
  }
  parts.push('');
  parts.push('### L2 工程决策（成功后压缩）');
  parts.push('- For each ACCEPTED decision, in this order:');
  parts.push('  1) 具体操作顺序（步骤序列）');
  parts.push('  2) 哪些参数不能修改');
  parts.push('  3) 哪些步骤可以复用');
  parts.push('  4) 哪些步骤依赖之前的特征');
  parts.push('  5) 验证成功时的模型版本 / 文件 / 报告');
  parts.push('- Keep it terse, but never drop a parameter or a dependency.');
  if (prevL2) {
    parts.push('- PREVIOUS L2 (this is APPEND-ONLY: keep every line still valid, add new ones; do NOT shorten, do NOT delete):');
    parts.push(prevL2);
  }
  parts.push('');
  parts.push('### L2 失败记录（错误方案压缩）');
  parts.push('- For each FAILED attempt use exactly this shape:');
  parts.push('  - 尝试: <what was tried>');
  parts.push('    失败条件: <the concrete, checkable condition that made it fail>');
  parts.push('    不可再做: <what must NOT be repeated, expressed as the failing CONDITION, not the plan name>');
  parts.push('- Deduplicate by FAILING CONDITION. Two attempts with the same failing condition collapse into one entry; two attempts with different conditions must stay separate even if they share a plan name.');
  parts.push('- Keep failure records short: one line for the attempt, one for the condition, one for what not to do.');
  parts.push('');
  // ── 【B6 修复】进行中的工作状态：不可压缩，必须原样带走 ──────────────
  //   现场：`sel_snap: []——又是 dict 没解包！step2h 直接把 face dict 喂给了
  //   _select_face（step2f 的解包补丁没带过来）` —— 同一段逻辑重写 8 版。
  //   摘要模型会把"我刚改好哪一行"当过程噪音删掉，可它恰恰是下一版能复用的关键。
  if (o.openWorkBlock && String(o.openWorkBlock).trim()
      && String(o.openWorkBlock).trim() !== '(none)') {
    parts.push('### L1.5 进行中的工作状态（★不可压缩）');
    parts.push('- Reproduce this block VERBATIM. It is NOT process noise.');
    parts.push('- 特别是「已修好的补丁点」：下一版必须直接沿用，**禁止重写**。');
    parts.push('');
    parts.push(String(o.openWorkBlock).trim());
    parts.push('');
  }

  parts.push('### L3 执行过程');
  parts.push('- (none) — drop all reasoning, repeated operations, dead ends and chatter.');
  parts.push('- EXCEPTION: any explicit user requirement, correction or preference must be quoted verbatim into "### 用户关键要求".');
  parts.push('');
  parts.push('### 用户关键要求');
  parts.push('- Quote the user\'s own wording where it constrains the design (dimensions, materials, style, deadlines).');
  parts.push('');
  parts.push('### 下一步');
  parts.push('- The single next action aligned with the most recent request, or "(none)".');
  parts.push('');
  parts.push('### 禁止写进摘要的内容（违反即失败）');
  parts.push('- 任何"如何绕过检测/提醒"的技巧，例如 "vary the command each round"、'
    + '"change the arguments to avoid the repeat reminder"、"换个写法骗过去"。'
    + '这些是【失败做法】，属于 L3，必须整条丢弃，绝不允许写进 L2 经验。');
  parts.push('- 若摘要里出现这类措辞，等于教下一个模型继续犯规。');
  parts.push('');
  parts.push('Rules recap:');
  parts.push('- Write terse Chinese engineering bullets; keep identifiers, file paths, commands and error strings exact.');
  parts.push('- Numbers are sacred. If you are unsure about a number, copy it exactly as written.');
  if (o.hasCheckpoint) {
    parts.push('- The transcript already contains a prior checkpoint. Merge into ONE consolidated checkpoint: keep still-true facts, add newer ones, and NEVER delete an L0 fact or an L2 decision.');
  }
  return parts.join('\n');
}

/** 压缩检查点的前缀（替换 user 消息的开头，和 compaction-basic 保持一致语义）。 */
export const CHECKPOINT_PREAMBLE = 'This is an automatically generated engineering checkpoint condensing an earlier span of the conversation. Treat the captured context as established background and build on it without restating it. L0 machine facts and L2 decisions in it are authoritative and must not be changed. Continue the task directly from the messages that follow.';

export const SUMMARY_OPEN_TAG = '<engineering-checkpoint>';
export const SUMMARY_CLOSE_TAG = '</engineering-checkpoint>';
