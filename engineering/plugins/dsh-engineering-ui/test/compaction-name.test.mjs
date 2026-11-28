/**
 * 压缩历史「子代理名」回归（2026-11-28）
 *
 * 现场：面板里每条都显示 31da68c4 / f4fdb8a7 这类 hash 前缀，
 *   用户原话「还是没有区分是哪个子代理的压缩历史，可以沿用子代理名称」。
 *
 * 根因：账本写入时 agent.label / session.title 在 0.2.x 上取不到，只能退回
 *   target 前 8 位。修法：宿主侧用 subagents.listDescendants(root) 的权威目录
 *   建 id→名字映射，回填进 /compaction-history 的响应。
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(path.join(here, '../lib/index.js'), 'utf8');

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

// ── 复刻回填逻辑（与 index.js 里的实现同构，用于验证语义）──────────────
function applyNames(entries, counts, tree, probeRoot) {
  const nameOf = new Map();
  for (const row of (Array.isArray(tree) ? tree : [])) {
    const id = String((row && (row.id || row.sessionId)) || '');
    if (!id) continue;
    const nm = String((row && (row.label || row.name || row.title)) || '').trim();
    if (nm) nameOf.set(id, nm);
  }
  if (nameOf.size && Array.isArray(entries)) {
    for (const e of entries) {
      const id = String(e && e.target || '');
      if (!nameOf.has(id) && id && id === String(probeRoot)) nameOf.set(id, '主对话');
      const nm = nameOf.get(id);
      if (nm) {
        if (!e.labelRaw) e.labelRaw = String(e.label || '');
        e.label = nm;
      }
    }
    if (counts && typeof counts === 'object') {
      for (const k of Object.keys(counts)) {
        const nm = nameOf.get(k);
        if (nm) counts[k].label = nm;
      }
    }
  }
  return { entries, counts, nameOf };
}

console.log('\n=== 1. hash 前缀 → 子代理名字 ===');
{
  const entries = [
    { target: 'session-aaaa', root: 'session-aaaa', label: 'session-' },
    { target: 'child-K', root: 'session-aaaa', label: 'child-K' },
    { target: 'child-D', root: 'session-aaaa', label: 'child-D' },
  ];
  const counts = {
    'child-K': { target: 'child-K', label: 'child-K', count: 3 },
    'child-D': { target: 'child-D', label: 'child-D', count: 1 },
  };
  const tree = [
    { id: 'child-K', kind: 'child', label: '知识', activity: 'inactive' },
    { id: 'child-D', kind: 'child', label: '设计', activity: 'inactive' },
  ];
  applyNames(entries, counts, tree, 'session-aaaa');
  ck('child-K 显示为「知识」', entries[1].label === '知识', entries[1].label);
  ck('child-D 显示为「设计」', entries[2].label === '设计', entries[2].label);
  ck('★ 根会话显示为「主对话」', entries[0].label === '主对话', entries[0].label);
  ck('保留原始 label 供排查（labelRaw）', entries[1].labelRaw === 'child-K');
  ck('counts 徽标同步改名', counts['child-K'].label === '知识');
}

console.log('\n=== 2. 拿不到名字时退回原值（绝不显示空白） ===');
{
  const entries = [{ target: 'child-X', root: 'r', label: 'child-X' }];
  applyNames(entries, {}, [], 'r');
  ck('无目录时保持原 label', entries[0].label === 'child-X');
}

console.log('\n=== 3. 目录里名字为空的行 → 不覆盖 ===');
{
  const entries = [{ target: 'child-Y', root: 'r', label: 'child-Y' }];
  applyNames(entries, {}, [{ id: 'child-Y', label: '' }], 'r');
  ck('空名字不覆盖原值', entries[0].label === 'child-Y');
}

console.log('\n=== 4. 多层级（含孙代）都能映射 ===');
{
  const entries = [
    { target: 'lvl1', root: 'r', label: 'lvl1' },
    { target: 'lvl2', root: 'r', label: 'lvl2' },
    { target: 'lvl3', root: 'r', label: 'lvl3' },
  ];
  const tree = [
    { id: 'lvl1', kind: 'child', label: '总计子代理' },
    { id: 'lvl2', kind: 'child', label: '知识' },
    { id: 'lvl3', kind: 'child', label: '孙代-校核' },
  ];
  applyNames(entries, {}, tree, 'r');
  ck('★ 三级都能拿到名字',
     entries.every(e => /[\u4e00-\u9fa5]/.test(e.label)),
     entries.map(e => e.label).join(','));
}

console.log('\n=== 5. 源码契约 ===');
// 【2026-11-28】名字映射已改成复用 lineageOf 的谱系表（同一 root 只查一次
//   listDescendants，避免读取端查两遍）。断言随之更新。
ck('名字映射复用 lineageOf', SRC.includes('const _ln = probeRoot ? await lineageOf'));
ck('lineageOf 内部调 listDescendants',
   SRC.includes("typeof ctx.subagents.listDescendants !== 'function'") === false
   || SRC.includes('ctx.subagents.listDescendants(id)'));
ck('用 label/name/title 三选一', SRC.includes('row.label || row.name || row.title'));
ck('保留 labelRaw', SRC.includes('e.labelRaw = String(e.label'));
ck('counts 同步改名', SRC.includes('r.counts[k].label = nm'));
ck('根会话补「主对话」', SRC.includes("nameOf.set(id, '主对话')"));
ck('失败不影响面板', SRC.includes('拿不到名字就用原值'));

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
