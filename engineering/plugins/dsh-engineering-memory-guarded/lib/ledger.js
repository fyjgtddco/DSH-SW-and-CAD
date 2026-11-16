/**
 * ledger.js — 【压缩历史账本】UI 的数据源（append-only，只增不改）
 * ==========================================================================
 * 用户要求（图一 / 图二）：
 *   「要让历史记录对应上图一的黄圈（不同的子代理对话对应不同的压缩历史）
 *     和图二的主对话黄圈。要可以看得到，并且对于真实的压缩历史的版本和
 *     进行更新」「最上面的是最先更新的历史，然后就是后面的历史不消失但是
 *     被刷下去那种」
 *
 * 也就是说：
 *   · 每次压缩成功 = 一条记录，写入即可见；
 *   · 子代理与主对话各有一条链，靠 target 区分；
 *   · 列表按【最新在上】排；旧记录不删，只被"刷下去"。
 *
 * 为什么单独一个文件而不是复用 dsh-engineering-compaction 的
 * compaction_history.jsonl：
 *   两者语义不同 —— 旧账本记的是「L0 数值被保住几条」，新引擎记的是
 *   「尺寸依赖图这次提交了什么」。混写会让两边的字段互相污染，
 *   回退时也无法分辨。用独立文件名 + 独立版本号，UI 侧再合并展示。
 *
 * 落点：与旧账本同一个 <state_dir>，这样 UI 一次探测就能读到两份。
 *
 * 铁律：
 *   · 追加是 O(1)，进程被杀最多丢最后一行，不会写坏整个文件；
 *   · 本模块【只提供】append 与 read，刻意不提供 update / remove
 *     （用户明确：写了就不能删、不能改）。
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export const LEDGER_FILE = 'compaction_history_v2.jsonl';
export const LEDGER_SCHEMA = 2;

/**
 * 解析状态目录。优先级与 dsh-engineering-compaction/lib/history.js 的
 * resolveStateDir() 完全一致 —— 两份账本必须落在同一个目录，
 * UI 才能用一次探测同时读到（见 lib/index.js 的 compactionStateDir）。
 */
export function resolveStateDir() {
  const home = process.env.USERPROFILE || process.env.HOME || os.homedir() || '';
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
  return '';
}

function ensureDir(dir) {
  try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
  return dir;
}

/**
 * 追加一条压缩记录。失败静默 —— 记账绝不能把压缩流程搞挂。
 *
 * record 必填：at / target / root / label
 * 建议：inputChars / outputChars / summary_excerpt / engine / mode
 */
export function appendLedgerEntry(record) {
  try {
    const dir = resolveStateDir();
    if (!dir) return { ok: false, error: 'NO_STATE_DIR' };
    ensureDir(dir);
    const row = { schema: LEDGER_SCHEMA, engine: 'dsh-engineering-memory-guarded', ...record };
    if (!Number.isFinite(Number(row.at))) row.at = Date.now();
    fs.appendFileSync(path.join(dir, LEDGER_FILE), JSON.stringify(row) + '\n', 'utf8');
    return { ok: true, file: path.join(dir, LEDGER_FILE) };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/**
 * 读账本，**最新在前**。
 *
 * 为什么在这里就排好序而不是交给前端：
 *   用户要求「最上面的是最先更新的历史」—— 排序口径属于数据契约的一部分，
 *   放在前端会让每个渲染入口各排一次，容易有一处漏掉（历史上压缩面板就
 *   出过"空列表但引擎正常"的误报，因为前端把字段丢了）。
 *
 * @param {object} opts { root, target, limit }
 */
export function readLedger(opts) {
  const o = opts || {};
  const dir = resolveStateDir();
  if (!dir) return { ok: true, entries: [], stateDir: '', exists: false };
  const p = path.join(dir, LEDGER_FILE);
  if (!fs.existsSync(p)) return { ok: true, entries: [], stateDir: dir, exists: false };
  let raw;
  try { raw = fs.readFileSync(p, 'utf8'); }
  catch (e) { return { ok: false, entries: [], stateDir: dir, error: String((e && e.message) || e) }; }

  let entries = [];
  for (const line of raw.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    try {
      const rec = JSON.parse(s);
      if (rec && typeof rec === 'object') entries.push(rec);
    } catch (e) { /* 坏行跳过，绝不让一行损坏使整块面板空白 */ }
  }
  if (o.root) entries = entries.filter((r) => String(r.root || '') === String(o.root));
  if (o.target) entries = entries.filter((r) => String(r.target || '') === String(o.target));

  // 先按时间稳定排序（newest first），再取前 limit 条。
  // 同一毫秒内用文件内序号兜底，保证顺序稳定、不会凭运气抖动。
  entries = entries.map((r, i) => ({ r, i }))
    .sort((a, b) => (Number(b.r.at) || 0) - (Number(a.r.at) || 0) || b.i - a.i)
    .map((x) => x.r);
  const limit = Number.isFinite(o.limit) && o.limit > 0 ? Math.floor(o.limit) : 400;
  const total = entries.length;
  if (entries.length > limit) entries = entries.slice(0, limit);
  return { ok: true, entries, stateDir: dir, exists: true, total };
}

/** 每个会话的累计统计（面板徽标用）。 */
export function ledgerCounts(entries) {
  const map = new Map();
  for (const e of entries || []) {
    const k = String(e.target || '');
    const c = map.get(k) || { target: k, label: '', count: 0, lastAt: 0 };
    c.count += 1;
    const at = Number(e.at) || 0;
    if (at > c.lastAt) { c.lastAt = at; c.label = String(e.label || c.label || ''); }
    map.set(k, c);
  }
  return [...map.values()];
}
