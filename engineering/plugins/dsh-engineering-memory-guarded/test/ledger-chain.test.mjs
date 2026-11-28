/**
 * 压缩历史分链回归（2026-11-28）
 *
 * 现场：面板「子代理」页永远空白。账本 1006 条记录【全部】target === root，
 *   前端按 target !== root 分链时分不出任何一条子代理记录。
 *
 * 根因：identityOf 用 agent.parent 对象引用找根，而 0.2.x 把谱系写在
 *   session.header.parentSession（字符串）里 —— 循环第一次就 break，
 *   root 直接等于 target。
 */
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

const dir = mkdtempSync(join(tmpdir(), 'ledger_'));
process.env.DSH_STATE_DIR = dir;

const { appendLedgerEntry, readLedger } = await import('../lib/ledger.js');

// ══════════════════════════════════════════════════════════════════
// 直接用 identityOf 的等价输入验证语义（identityOf 未导出，走账本端到端）
console.log('\n=== 账本按 target 分链 ===');

const ROOT = 'session-root-aaaa';
const CHILD1 = 'session-child-1';
const CHILD2 = 'session-child-2';

// 主对话：target=root, root=root（无父会话时这是正确的）
appendLedgerEntry({ at: 1, target: ROOT, root: ROOT, label: '主对话' });
// 子代理：target=child, root=父会话
appendLedgerEntry({ at: 2, target: CHILD1, root: ROOT, label: '建模小屋A' });
appendLedgerEntry({ at: 3, target: CHILD2, root: ROOT, label: '建模小屋B' });

const all = readLedger({ root: ROOT, limit: 100 });
ck('三张记录都归在同一个 root 下', all.entries.length === 3, String(all.entries.length));

const main = all.entries.filter(e => String(e.target) === ROOT);
const agents = all.entries.filter(e => String(e.target) !== ROOT);
ck('主对话链 1 条', main.length === 1, String(main.length));
ck('子代理链 2 条', agents.length === 2, String(agents.length));
ck('★ 子代理链非空（这正是面板空白的直接原因）', agents.length > 0);
ck('子代理记录带得出 label', agents.every(e => e.label && e.label !== ROOT.slice(0, 8)));

const byTarget = readLedger({ root: ROOT, target: CHILD1, limit: 100 });
ck('可按单个子代理过滤', byTarget.entries.length === 1 &&
   String(byTarget.entries[0].target) === CHILD1);

// ══════════════════════════════════════════════════════════════════
console.log('\n=== identityOf 源码契约（不能退回 agent.parent 对象引用） ===');
const src = readFileSync(new URL('../lib/guarded.js', import.meta.url), 'utf8');
// ⚠️ 这里必须是 includes，不是 in —— in 是对象键查找，拿字符串会 TypeError（写错过一次）
ck('读 header.parentSession', src.includes('session.header.parentSession'));
// 【2026-11-28 第二轮】root 语义从「直接父会话」改成「谱系最顶层根会话」：
//   上一版只取直接父级，导致二级及以下的孙代挂到自己的父会话下，
//   面板按 root 聚合时整组丢失（用户反馈：分子代理能显示、二级以下不显示）。
ck('root 上溯到最顶层（let rootId = parentId）',
   src.includes('let rootId = parentId'));
ck('没有父会话时才等于 target', src.includes('out.root = rootId || out.target'));
ck('记录直接父级（供 UI 显示层级）', src.includes('out.parent = parentId'));
ck('有反查器时逐层上溯', src.includes('parentResolver'));
ck('不再依赖 agent.parent 对象引用作为首选',
   !src.includes('let cur = agent; let guard = 0; let root = out.target;'));

console.log('\n' + '='.repeat(50));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
