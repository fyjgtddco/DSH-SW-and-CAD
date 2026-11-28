/**
 * 压缩历史·二级及以下必须生效（2026-11-28）
 *
 * 现场：二级子代理的压缩历史「不出现在子代理压缩历史里面」。
 * 根因：账本 root 是【落账那一刻】算的，而它依赖异步预热的反查表 ——
 *   时序上不可靠，常停在直接父级。于是同一条链上记录的 root 各不相同：
 *     31da68c4 的 root = session-4466
 *     f4fdb8a7 的 root = 31da68c4     ← 指向中间层
 *   按 root 聚合时整组散架。
 *
 * 修法（读取端保证）：用宿主谱系树判定归属，任何深度只要在树里就归本 root。
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

/** 复刻读取端的归属判定（与源码同构）。 */
function belongs(entry, wantRoot, lineageSet) {
  const t = String(entry.target || '');
  const rRoot = String(entry.root || '');
  if (rRoot === wantRoot) return true;
  if (!rRoot && t === wantRoot) return true;
  if (lineageSet && lineageSet.has(t)) return true;
  return false;
}

const ROOT = 'session-4466';
// 真实形态：三级链 ROOT → L1(31da) → L2(f4fd) → L3(941d)
const lineage = new Set([ROOT, '31da', 'f4fd', '941d', 'd522']);

console.log('\n=== 1. 任意深度的记录都归到同一 root ===');
{
  const entries = [
    { target: ROOT,  root: ROOT,   at: 1 },   // 主对话
    { target: '31da', root: ROOT,  at: 2 },   // 一级
    { target: 'f4fd', root: '31da', at: 3 },  // 二级（root 指向中间层！）
    { target: '941d', root: 'f4fd', at: 4 },  // 三级（root 指向中间层！）
    { target: 'd522', root: '31da', at: 5 },  // 另一个二级
  ];
  const got = entries.filter(e => belongs(e, ROOT, lineage));
  ck('★ 三级全部被收录', got.length === 5, '实际 ' + got.length);
  ck('二级 f4fd 在里面', got.some(e => e.target === 'f4fd'));
  ck('★ 三级 941d 在里面', got.some(e => e.target === '941d'));
}

console.log('\n=== 2. 别的任务不会被误收（关键：不能混入外人） ===');
{
  const outsider = { target: 'other-task-child', root: 'other-root', at: 9 };
  ck('★ 不在谱系里的记录被排除',
     belongs(outsider, ROOT, lineage) === false);
}

console.log('\n=== 3. 谱系拿不到时退回原判据（不崩、不空） ===');
{
  const e1 = { target: '31da', root: ROOT };
  ck('无谱系 + root 命中 → 保留', belongs(e1, ROOT, null) === true);
  const e2 = { target: 'f4fd', root: '31da' };
  ck('无谱系 + root 不命中 → 排除（旧行为，退化但不崩）',
     belongs(e2, ROOT, null) === false);
}

console.log('\n=== 4. 旧记录没有 root 字段 ===');
{
  const old = { target: ROOT, at: 1 };
  ck('无 root 且 target===root → 保留', belongs(old, ROOT, null) === true);
}

console.log('\n=== 5. 源码契约 ===');
ck('存在 lineageOf', SRC.includes('async function lineageOf'));
ck('★ 用谱系集合判定归属', SRC.includes('_line.set.has(t)'));
ck('注释写明二级丢失现场', SRC.includes('二级及以下的子代理压缩历史'));
ck('名字映射复用同一谱系表', SRC.includes('const nameOf = _ln.names'));
ck('读取函数已改 async', SRC.includes('async function readCompactionHistory'));
ck('调用处已 await', SRC.includes('await readCompactionHistory({'));

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
