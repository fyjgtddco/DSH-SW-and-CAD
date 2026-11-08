/**
 * facts.js — 【数值保险箱】机械事实层（L0）的提取、核对与回填
 * ==========================================================================
 * 用户红线（原话）：
 *   「将数据（模型分析出来的数据、人为手动设计的）等等关于机械的相关数值，
 *    不可压缩，数值一个都不能动进行压缩，哪怕我压缩 999999999+ 次，
 *    数值都不能动。」
 *
 * 因此本模块的唯一职责：让摘要模型【碰不到】这些数值。
 *
 * 做法（三道闸，缺一不可）：
 *   ① 压缩前 extractFacts()：把被压缩区里的每一个数值连同它所在的原文片段、
 *      上一级标题、所属零件/房间，全部抽成结构化记录（L0 层，永不交给模型改写）。
 *   ② 摘要时把这份记录【原样注入】提示词，并明确「原样复制，禁止改写/换算/
 *      四舍五入/合并/删除」。
 *   ③ 压缩后 verifySummary()：把模型产出的摘要重新扫一遍，与 L0 逐条比对。
 *      缺失、被改写、被合并 → 判定 FAIL，走 restoreFacts() 把数值【强行补回】。
 *
 * 关键设计：失败时不是「放弃压缩」，而是「把数值贴回去」。这样即使模型 100%
 * 不听话，最终落盘的数值也一定与压缩前一致 —— 这是「压缩 N 次都不动」的
 * 结构性保证，而不是靠模型自觉。
 *
 * 纯函数、零依赖（只用 node 内置），便于单测。
 */

/** 数值：整数 / 小数 / 分数 / 带正负 / 带单位前缀。刻意【不】匹配纯年份以外的孤立数字以外的东西。 */
const NUM_RE = /[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?/g;

/** 数值后面紧跟的单位（含中文单位与公差符号）。用于把「5mm」整体当作事实。
 *  ⚠️ 必须【按长度降序】排列：正则交替取首个命中，若 MPa 排在 M 之后会被截成 M，
 *     于是「206000 MPa」被记成「206000 M」，回填时核不上（实测踩过）。 */
const UNIT_LIST = ['N·m', 'kN·m', 'mm', 'cm', 'km', 'μm', 'um', 'nm', 'inch', 'in', 'kg', 'mg',
  'MPa', 'GPa', 'kPa', 'Pa', 'kN', 'Nm', 'Hz', 'kHz', 'rpm', 'r/min', 'm/s', 'rad',
  'HRC', 'HB', 'HV', 'ms', 'min', 'mL', '℃',
  'g', 't', 'N', 'm', 's', 'V', 'A', 'W', 'h', 'L', '°', '%'];
const UNIT_RE = new RegExp('^\\s*(' + UNIT_LIST.join('|') + ')');

/** 公差串，如 φ24H7、Ø10±0.02、M8×1.25、Ra1.6。整体视为一个不可分事实。 */
const TOL_PATTERNS = [
  /[φØΦ]\s?\d+(?:\.\d+)?\s?(?:[A-Z]{1,2}\d{1,2})?/g,
  /M\s?\d+(?:\.\d+)?\s?[×x]\s?\d+(?:\.\d+)?/g,
  /Ra\s?\d+(?:\.\d+)?/g,
  /\d+(?:\.\d+)?\s?±\s?\d+(?:\.\d+)?/g,
  /[+-]\d+(?:\.\d+)?\s?\/\s?[+-]\d+(?:\.\d+)?/g,
];

/** 明确是「工程数值」的线索词：出现这些词的行，其中的数字权重更高。 */
const DIM_HINT = /(尺寸|直径|半径|长度|宽度|高度|厚度|壁厚|孔径|螺距|齿数|模数|压力角|跨距|轴距|行程|角度|倾角|配合|公差|间隙|过盈|圆角|倒角|材料|牌号|密度|弹性模量|泊松比|屈服|抗拉|硬度|载荷|额定|扭矩|转速|功率|安全系数|寿命|循环|应力|应变|位移|变形|质量|重量|体积|面积|mm|MPa|N·m|Nmm|±|H7|H8|g6|k6|js6|GB\/T|Q235|Q355|45钢|40Cr|20CrMnTi|65Mn|GCr15|6061|7075|2A12|HT200|QT500|ZL104|ZCuSn|H62)/;

/** 纯噪声数字：行号、seq、时间戳、ID 里的数字、token 数、百分比进度等。 */
function isNoiseNumber(raw, lineText, indexInLine) {
  const s = String(raw || '');
  if (!s) return true;
  // 1) 很长的一串（>=7 位）基本是 id / 时间戳 / sha
  const digitsOnly = s.replace(/[^0-9]/g, '');
  if (digitsOnly.length >= 7) return true;
  // 2) 行内定位：紧跟在 # / seq / id / line / 第 之后
  const before = lineText.slice(Math.max(0, indexInLine - 12), indexInLine);
  if (/(#|seq\s*[:=]?|id\s*[:=]?|line\s*[:=]?|第|L)\s*$/i.test(before)) return true;
  // 3) 时间/秒数（如 12:34、2026-11-07）
  if (/^\d{1,2}$/.test(s) && /[:\/]\s*$/.test(before)) return true;
  return false;
}

/**
 * 从一段文本里抽出所有事实（数值 + 上下文）。
 * @param {string} text 原文
 * @param {object} ctx  归属信息 { room, part, source }
 * @returns {Array<{key:string, raw:string, value:string, unit:string, context:string, line:string, ...}>}
 */
export function extractFacts(text, ctx) {
  const out = [];
  const meta = ctx && typeof ctx === 'object' ? ctx : {};
  const seen = new Set();
  const lines = String(text == null ? '' : text).split(/\r?\n/);
  let lastHeading = '';

  lines.forEach((line, li) => {
    const t = String(line || '');
    const h = t.match(/^\s*#{1,6}\s+(.+)$/);
    if (h) { lastHeading = h[1].trim(); return; }
    if (!t.trim()) return;

    // 先抓「成组公差/螺纹/粗糙度」——它们整体不可分
    const covered = [];
    for (const re of TOL_PATTERNS) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(t)) !== null) {
        covered.push([m.index, m.index + m[0].length]);
        pushFact(m[0].trim(), m[0].trim(), '', m.index);
      }
    }

    NUM_RE.lastIndex = 0;
    let m;
    while ((m = NUM_RE.exec(t)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      // 已被公差模式覆盖 → 跳过，避免同一事实记两遍
      if (covered.some(([a, b]) => start >= a && start < b)) continue;
      if (isNoiseNumber(m[0], t, start)) continue;
      const rest = t.slice(end);
      const u = rest.match(UNIT_RE);
      const unit = u ? u[1] : '';
      const rawVal = m[0];
      const full = rawVal + (unit ? (u[0].match(/^\s+/) ? ' ' : '') + unit : '');
      pushFact(full, rawVal, unit, start);
    }

    function pushFact(full, value, unit, col) {
      const context = t.trim().slice(0, 240);
      // key 用「数值 + 单位 + 该行上下文指纹」——同一数值出现在两处不同上下文里算两条事实，
      // 这样「同一个 10mm 在两个零件上」不会被合并成一条。
      const key = [full, lastHeading, context].join('\u0001');
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        key,
        raw: full,
        value,
        unit,
        heading: lastHeading,
        line: context,
        col,
        lineNo: li + 1,
        room: meta.room || '',
        part: meta.part || '',
        source: meta.source || '',
        // 权重：行内出现工程线索词 → high；否则 normal。HIGH 的事实永远不可删。
        weight: DIM_HINT.test(t) ? 'high' : 'normal',
      });
    }
  });

  // 材料牌号单独抽（它们是「事实」但不是数字，同样不可改写）
  const mats = extractMaterials(text);
  return { numbers: out, materials: mats };
}

/** 常见国标材料牌号（与 persona 里声明的清单保持一致）。 */
const MATERIAL_RE = /\b(Q235[A-Z]?|Q355[A-Z]?|Q345|45钢|45#|40Cr|20CrMnTi|65Mn|GCr15|Cr12MoV|HT200|HT250|QT500-7|QT450-10|06Cr19Ni10|022Cr17Ni12Mo2|304|316L?|6061-T6|7075-T6|2A12|ZL104|ZCuSn10P1|H62)\b/g;

export function extractMaterials(text) {
  const out = [];
  const seen = new Set();
  MATERIAL_RE.lastIndex = 0;
  let m;
  const s = String(text == null ? '' : text);
  while ((m = MATERIAL_RE.exec(s)) !== null) {
    const v = m[1];
    if (seen.has(v)) continue;
    seen.add(v);
    const from = Math.max(0, m.index - 60);
    out.push({ key: 'material:' + v, value: v, line: s.slice(from, m.index + 60).replace(/\s+/g, ' ').trim() });
  }
  return out;
}

/**
 * 把 L0 事实渲染成给模型的【只读区块】。
 * 刻意用代码块包起来，并在首尾加显式禁令。
 */
export function renderFactsBlock(facts) {
  const nums = (facts && facts.numbers) || [];
  const mats = (facts && facts.materials) || [];
  if (!nums.length && !mats.length) return '';
  const lines = [];
  lines.push('<<<L0-MACHINE-FACTS: 以下为机械事实层，压缩时必须【逐字原样复制】到摘要中>>>');
  lines.push('规则：禁止改写、禁止换算、禁止四舍五入、禁止单位转换、禁止合并同类项、禁止删除任何一条。');
  lines.push('每条前面的编号必须一并保留，编号缺失即视为篡改。');
  lines.push('');
  lines.push('### 材料牌号（不可改写）');
  if (mats.length) mats.forEach((x, i) => lines.push('M' + (i + 1) + '. ' + x.value + '   ← ' + x.line));
  else lines.push('(none)');
  lines.push('');
  lines.push('### 尺寸/公差/材料参数（不可改写）');
  if (nums.length) {
    nums.forEach((x, i) => {
      const tag = x.weight === 'high' ? '[HIGH]' : '';
      const where = [x.room ? '房间=' + x.room : '', x.part ? '零件=' + x.part : '', x.heading ? '节=' + x.heading : ''].filter(Boolean).join(' ');
      lines.push('F' + (i + 1) + '. ' + tag + ' ' + x.raw + (where ? '   (' + where + ')' : '') + '   ← ' + x.line);
    });
  } else lines.push('(none)');
  lines.push('');
  lines.push('<<<END-L0-MACHINE-FACTS>>>');
  return lines.join('\n');
}

/** 摘要里是否原样含有该事实（允许数字与单位之间的空白差异，不允许数字/单位变化）。 */
export function factPresent(summary, fact) {
  const s = String(summary == null ? '' : summary);
  if (!fact) return false;
  const raw = String(fact.raw || '').trim();
  if (!raw) return false;
  if (s.indexOf(raw) >= 0) return true;

  // 允许模型在【数字与单位之间】插入/删除空格（12mm → 12 mm）。
  // 数字本身与单位本身一个字符都不许变。
  const escapeRe = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, (c) => "\\" + c);
  try {
    const m = raw.match(/^(.*?[\d.])[\s]*([^\d\s].*)$/);
    if (m) {
      const re = new RegExp(escapeRe(m[1]) + "\\s*" + escapeRe(m[2]));
      if (re.test(s)) return true;
    }
    return new RegExp(escapeRe(raw).replace(/\s+/g, "\\s*")).test(s);
  } catch (e) {
    return false;
  }
}

/**
 * 核对摘要是否保住了全部 L0 事实。
 * @returns {{ok:boolean, missing:Array, total:number, ratio:number}}
 */
export function verifySummary(summary, facts) {
  const nums = (facts && facts.numbers) || [];
  const mats = (facts && facts.materials) || [];
  const all = nums.concat(mats.map((m) => ({ key: m.key, raw: m.value, value: m.value, weight: 'high' })));
  const missing = all.filter((f) => !factPresent(summary, f));
  const total = all.length;
  return {
    ok: missing.length === 0,
    missing,
    total,
    ratio: total === 0 ? 1 : (total - missing.length) / total,
  };
}

/**
 * 【兜底回填】把缺失的事实强行补到摘要末尾。
 *
 * 这是「压缩 N 次数值都不动」的最终保证：即使模型漏了/改了，这里也把它们补回去。
 * 补的是【压缩前的原始字面量】，因此不可能与压缩前不一致。
 *
 * 注意：补回后摘要可能变长，极端情况下甚至不比原文短。此时由调用方决定
 * 是否放弃本次压缩（见 index.js 的「回填后仍不缩短 → 跳过压缩」分支）。
 */
export function restoreFacts(summary, facts, options) {
  const opts = options || {};
  const verdict = verifySummary(summary, facts);
  if (verdict.ok) return { text: String(summary == null ? '' : summary), restored: 0, verdict };
  const missing = verdict.missing;
  const lines = [];
  lines.push('');
  lines.push('### L0 事实补录（压缩器自动补齐，禁止删除）');
  missing.forEach((f) => {
    const line = String(f.line || f.raw || '').trim();
    lines.push('- ' + String(f.raw) + (line ? '   ← ' + line : ''));
  });
  let text = String(summary == null ? '' : summary) + '\n' + lines.join('\n');
  // 二次核对：补完必须 100% 命中，否则说明 raw 本身与原文不符（不该发生）
  const again = verifySummary(text, facts);
  if (!again.ok && opts.strict !== false) {
    // 仍然缺 → 把原文片段整块附上，绝不丢
    text += '\n\n### L0 原文片段（兜底，逐字保留）\n' + String(opts.rawSpan || '');
  }
  return { text, restored: missing.length, verdict: verdict };
}

/**
 * 【单调合并】把新一轮事实并入历史事实集：只增不减。
 *
 * 用户要求「写了就不能改，只能加，不能减少，不能删」。因此跨多次压缩时，
 * L0 是累积集合：early 的事实即使本轮压缩窗口里已经不出现，也必须继续保留。
 */
export function mergeFacts(prev, next) {
  const p = prev && typeof prev === 'object' ? prev : {};
  const n = next && typeof next === 'object' ? next : {};
  const pn = Array.isArray(p.numbers) ? p.numbers : [];
  const nn = Array.isArray(n.numbers) ? n.numbers : [];
  const pm = Array.isArray(p.materials) ? p.materials : [];
  const nm = Array.isArray(n.materials) ? n.materials : [];

  const byKey = new Map();
  for (const x of pn) if (x && x.key) byKey.set(x.key, x);
  for (const x of nn) {
    if (!x || !x.key) continue;
    if (!byKey.has(x.key)) byKey.set(x.key, x);
  }
  const matKey = new Set();
  const mats = [];
  for (const x of pm.concat(nm)) {
    const k = x && (x.key || x.value);
    if (!k || matKey.has(k)) continue;
    matKey.add(k);
    mats.push(x);
  }
  return { numbers: Array.from(byKey.values()), materials: mats };
}

/** 统计：给 UI 显示「本次保住 N 个数值」。 */
export function factsStats(facts) {
  const nums = (facts && facts.numbers) || [];
  const mats = (facts && facts.materials) || [];
  return {
    numbers: nums.length,
    materials: mats.length,
    high: nums.filter((x) => x.weight === 'high').length,
  };
}
