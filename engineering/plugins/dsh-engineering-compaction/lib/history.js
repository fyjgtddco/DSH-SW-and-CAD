/**
 * history.js — 【压缩历史】append-only 账本（红框面板的数据源）
 * ==========================================================================
 * 用户要求：
 *   「红框里面放上各个子代理的压缩历史，并且每次压缩的时候，红框地方的压缩
 *    历史也要跟上」「黑框加上主对话的历史压缩记录」
 *
 * 因此每发生一次压缩，就往账本追加一条不可变记录：
 *   { seq, at, target, layer, shadowed, summary, l0, ... }
 *
 * 落盘位置与 Python 侧 _store.state_dir() 对齐，写到：
 *   <state_dir>/compaction_history.jsonl      ← 账本（一行一条，append-only）
 *   <state_dir>/compaction_facts.json         ← L0 事实单调累积集
 *
 * 为什么用 JSONL 而不是 JSON 数组：
 *   · 追加是 O(1)，不需要读全量再重写（长任务里压缩几十次也不会变慢）；
 *   · 进程被杀时最多丢最后一行，不会把整个账本写成半个 JSON。
 *
 * 为什么不能删/改行：
 *   用户明确「写了就不能改，你只能加，不能减少，不能删」。本模块【只提供】
 *   append 与 read，刻意不提供 update / remove。
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const HISTORY_FILE = 'compaction_history.jsonl';
const FACTS_FILE = 'compaction_facts.json';

/**
 * 解析状态目录。优先级与 Python tools/_store.py:state_dir() 完全一致：
 *   ① DSH_STATE_DIR
 *   ② 安装副本（~/.dsh/.agent-presets/engineering/tools）
 *   ③ DSH_ENGINEERING_ROOT
 *   ④ cwd 下的 engineering/tools
 *   ⑤ 本插件目录（兜底）
 * 与工程模式其余部分认同一个目录，红框才能和门禁/零件表读到同一批状态。
 */
/**
 * 【B6 修复】open_work_state —— 压缩时必须原样带走的"正在做的事"。
 *
 * ── 现场（2026-11-07 会话 · 总装 #2 `0b161dc4`）──────────────────────
 *   `:1536` `sel_snap: []——又是 dict 没解包！step2h 直接把 face dict 喂给了
 *   _select_face（step2f 的解包补丁没带过来）`
 *   产物：`asm_step2a…2h` **8 版脚本、41 次 write/edit**。
 *   也就是说：上一版已经修好的补丁，被压缩摘要"丢了"，下一版又从零写起。
 *
 * ── 为什么普通摘要会丢 ──────────────────────────────────────────────────
 *   L2 记录的是"方案与理由"，但"我刚改好哪一行、补丁长什么样"属于【进行中的
 *   局部实现细节】。摘要模型天然会把它当"过程噪音"（L3）删掉 —— 而它恰恰是
 *   下一版能不能直接复用的关键。
 *
 * ── 本函数 ──────────────────────────────────────────────────────────────
 *   把"当前正在做的事"结构化固化下来，作为【不可压缩区块】注入摘要：
 *     · doing        正在做的具体动作（一句话，可执行）
 *     · files        正在改的文件与其角色
 *     · patched      已经踩过并修好的坑（补丁点）——★最容易被丢的就是这个
 *     · pitfalls     已知陷阱（踩过但还没修 / 改了会复发）
 *     · next_step    下一步
 *   提示词里会强制要求"一字不改地复制"，并在压缩后核对是否还在。
 *
 * @param {object} raw 形如 {doing, files[], patched[], pitfalls[], next_step}
 * @returns {{ok:boolean, state:object, block:string}}
 */
export function buildOpenWorkState(raw) {
  const s = raw && typeof raw === 'object' ? raw : {};
  const arr = (v) => (Array.isArray(v) ? v.filter((x) => x !== null && x !== undefined && String(x).trim() !== '') : (v ? [v] : []));
  const state = {
    doing: String(s.doing || '').trim(),
    files: arr(s.files).map((x) => (typeof x === 'object' ? x : { path: String(x) })),
    patched: arr(s.patched).map((x) => (typeof x === 'object' ? x : { what: String(x) })),
    pitfalls: arr(s.pitfalls).map((x) => (typeof x === 'object' ? x : { what: String(x) })),
    next_step: String(s.next_step || '').trim(),
  };
  const lines = [];
  const has = state.doing || state.files.length || state.patched.length
    || state.pitfalls.length || state.next_step;
  if (!has) return { ok: true, state, block: '(none)' };
  if (state.doing) lines.push('- 正在做: ' + state.doing);
  if (state.files.length) {
    lines.push('- 正在改的文件:');
    for (const f of state.files) {
      lines.push('  - ' + (f.path || '?') + (f.role ? '（' + f.role + '）' : ''));
    }
  }
  if (state.patched.length) {
    lines.push('- ★已修好的补丁点（下一版必须原样沿用，禁止重写）:');
    for (const p of state.patched) {
      lines.push('  - ' + (p.what || '?')
        + (p.where ? ' @ ' + p.where : '')
        + (p.how ? ' → ' + p.how : ''));
    }
  }
  if (state.pitfalls.length) {
    lines.push('- 已知陷阱（改了会复发）:');
    for (const p of state.pitfalls) {
      lines.push('  - ' + (p.what || '?') + (p.why ? '：' + p.why : ''));
    }
  }
  if (state.next_step) lines.push('- 下一步: ' + state.next_step);
  return { ok: true, state, block: lines.join('\n') };
}

/**
 * 【B6】核对摘要里有没有把 open_work_state 原样带过来。
 * 缺哪条就返回哪条（供 restoreFacts 同款"强制回填"使用）。
 */
export function missingOpenWork(summaryText, state) {
  const s = String(summaryText || '');
  const miss = [];
  const probe = (label, val) => {
    const v = String(val || '').trim();
    if (!v) return;
    // 用前 24 个字符做指纹，避免摘要里加了标点就判丢
    const key = v.slice(0, Math.min(24, v.length));
    if (s.indexOf(key) < 0) miss.push({ label, value: v });
  };
  if (state) {
    probe('doing', state.doing);
    probe('next_step', state.next_step);
    for (const p of (state.patched || [])) probe('patched', p && p.what);
    for (const p of (state.pitfalls || [])) probe('pitfalls', p && p.what);
  }
  return miss;
}

/**
 * 【B6】进行中工作状态的读写（跨压缩继承）。
 *
 * 落点：<state_dir>/open_work_state.json（与 facts/history 同目录，口径一致）。
 * 语义与 L0 事实一致：**只增不减**。写入时把新的补丁点并入旧的，
 * 不会因为"这次没提"就把上次修好的补丁删掉 —— 那正是 B6 丢补丁的机制。
 */
const OPEN_WORK_FILE = 'open_work_state.json';

function _openWorkPath() {
  return path.join(resolveStateDir(), OPEN_WORK_FILE);
}

export function readOpenWorkState(target) {
  try {
    const p = _openWorkPath();
    if (!fs.existsSync(p)) return null;
    const d = JSON.parse(fs.readFileSync(p, 'utf8')) || {};
    // 支持按 target（会话/子代理）分槽；没有槽就读全局那一份
    if (target && d.by_target && d.by_target[String(target)]) {
      return d.by_target[String(target)];
    }
    return d.global || null;
  } catch (e) {
    return null;
  }
}

export function writeOpenWorkState(state, target) {
  try {
    const p = _openWorkPath();
    let d = {};
    try {
      if (fs.existsSync(p)) d = JSON.parse(fs.readFileSync(p, 'utf8')) || {};
    } catch (e) { d = {}; }
    if (!d.by_target) d.by_target = {};
    const key = target ? String(target) : '';
    const prev = key ? (d.by_target[key] || null) : (d.global || null);
    const merged = _mergeOpenWork(prev, state);
    if (key) d.by_target[key] = merged; else d.global = merged;
    d.updated_at = new Date().toISOString();
    fs.writeFileSync(p, JSON.stringify(d, null, 2), 'utf8');
    return { ok: true, file: p };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/** 合并：补丁点/陷阱按 what 去重后【并集】，doing/next_step 用最新的。 */
function _mergeOpenWork(prev, next) {
  const p = prev || {};
  const n = next || {};
  const unionBy = (a, b, key) => {
    const out = [];
    const seen = new Set();
    for (const it of [].concat(a || [], b || [])) {
      if (!it) continue;
      const k = typeof it === 'string' ? it : String(it[key] || it.what || it.path || '');
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(it);
    }
    return out;
  };
  return {
    doing: String(n.doing || p.doing || '').trim(),
    files: unionBy(p.files, n.files, 'path'),
    // ★补丁点只增不减：这是 B6 的核心
    patched: unionBy(p.patched, n.patched, 'what'),
    pitfalls: unionBy(p.pitfalls, n.pitfalls, 'what'),
    next_step: String(n.next_step || p.next_step || '').trim(),
    updated_at: new Date().toISOString(),
  };
}

export function resolveStateDir() {
  const home = process.env.USERPROFILE || process.env.HOME || os.homedir || '';
  const dshHome = process.env.DSH_HOME || (home ? path.join(home, '.dsh') : '');
  const cands = [];
  const push = (p) => { try { if (p && fs.existsSync(p) && cands.indexOf(p) < 0) cands.push(p); } catch (e) {} };

  const explicit = String(process.env.DSH_STATE_DIR || '').trim();
  if (explicit) push(explicit);
  push(dshHome && path.join(dshHome, '.agent-presets', 'engineering', 'tools'));
  push(dshHome && path.join(dshHome, 'engineering', 'tools'));
  push(home && path.join(home, '.dsh', '.agent-presets', 'engineering', 'tools'));
  push(home && path.join(home, '.dsh', 'engineering', 'tools'));
  const eng = String(process.env.DSH_ENGINEERING_ROOT || '').trim();
  if (eng) push(path.join(eng, 'tools'));
  try { push(path.join(process.cwd(), 'engineering', 'tools')); push(path.join(process.cwd(), 'tools')); } catch (e) {}
  if (cands.length) return cands[0];

  // 兜底：插件自己的 data 目录（保证永远有地方写）
  const fallback = path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..', 'data');
  try { fs.mkdirSync(fallback, { recursive: true }); } catch (e) {}
  return fallback;
}

function ensureDir(dir) {
  try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
  return dir;
}

/** 追加一条压缩记录。失败静默（绝不因记账把压缩流程搞挂）。 */
export function appendCompaction(record) {
  try {
    const dir = ensureDir(resolveStateDir());
    const line = JSON.stringify(record) + '\n';
    fs.appendFileSync(path.join(dir, HISTORY_FILE), line, 'utf8');
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/**
 * 读账本。
 * @param {object} opts { target, root, layer, limit }
 *   target: 只取某个会话（子代理 id 或 root 会话 id）
 *   root:   取某棵子树下的全部记录
 *   limit:  最多返回最近 N 条（默认 500）
 */
export function readHistory(opts) {
  const o = opts || {};
  try {
    const dir = resolveStateDir();
    const p = path.join(dir, HISTORY_FILE);
    if (!fs.existsSync(p)) return { ok: true, entries: [], stateDir: dir };
    const raw = fs.readFileSync(p, 'utf8');
    const lines = raw.split(/\r?\n/);
    let entries = [];
    for (const line of lines) {
      const s = line.trim();
      if (!s) continue;
      try {
        const rec = JSON.parse(s);
        if (rec && typeof rec === 'object') entries.push(rec);
      } catch (e) { /* 坏行跳过，不影响其余记录 */ }
    }
    if (o.target) entries = entries.filter((r) => String(r.target || '') === String(o.target));
    if (o.root) entries = entries.filter((r) => String(r.root || '') === String(o.root));
    if (o.layer) entries = entries.filter((r) => String(r.layer || '') === String(o.layer));
    const limit = Number.isFinite(o.limit) && o.limit > 0 ? Math.floor(o.limit) : 500;
    if (entries.length > limit) entries = entries.slice(entries.length - limit);
    return { ok: true, entries, stateDir: dir };
  } catch (e) {
    return { ok: false, entries: [], error: String((e && e.message) || e) };
  }
}

/** 每个会话各压缩了几次（红框上显示计数用）。 */
export function historyStats(opts) {
  const r = readHistory(Object.assign({ limit: 100000 }, opts || {}));
  const byTarget = new Map();
  for (const e of r.entries) {
    const k = String(e.target || '');
    const cur = byTarget.get(k) || { target: k, label: e.label || '', count: 0, lastAt: 0, numbersKept: 0 };
    cur.count += 1;
    if (e.at > cur.lastAt) { cur.lastAt = e.at; cur.label = e.label || cur.label; }
    cur.numbersKept += Number(e.l0_numbers || 0);
    byTarget.set(k, cur);
  }
  return { ok: r.ok, stateDir: r.stateDir, total: r.entries.length, byTarget: Array.from(byTarget.values()) };
}

/**
 * 读/写 L0 事实累积集（跨压缩单调增长）。
 * 单文件 JSON（不是 JSONL）：它是一份「当前权威快照」，需要整体替换。
 * 但替换规则是【只增不减】，见 facts.mergeFacts()。
 */
export function readFacts() {
  try {
    const p = path.join(resolveStateDir(), FACTS_FILE);
    if (!fs.existsSync(p)) return { numbers: [], materials: [] };
    const j = JSON.parse(fs.readFileSync(p, 'utf8'));
    return {
      numbers: Array.isArray(j && j.numbers) ? j.numbers : [],
      materials: Array.isArray(j && j.materials) ? j.materials : [],
    };
  } catch (e) {
    return { numbers: [], materials: [] };
  }
}

export function writeFacts(facts) {
  try {
    const dir = ensureDir(resolveStateDir());
    const p = path.join(dir, FACTS_FILE);
    fs.writeFileSync(p, JSON.stringify(facts || { numbers: [], materials: [] }, null, 2), 'utf8');
    return { ok: true, path: p };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

export const FILES = { HISTORY_FILE, FACTS_FILE };
