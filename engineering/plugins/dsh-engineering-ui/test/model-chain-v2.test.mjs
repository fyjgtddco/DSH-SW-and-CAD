/**
 * 换模型策略 v2 回归（2026-11-28）
 *
 * 用户三条要求：
 *   ③ 切到小窗口模型报 "compaction still above threshold（50578 >= 40000）"
 *   ④ 二级及以下子代理换不回来
 *   ⑤ 第 4 个失败时先回头试第 1~3 个（它们可能已恢复），四个都不行才轮到第 5 个
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

// ── 把 pickCandidateV2 从源码里取出来单独跑（纯函数，无外部依赖）──────────
// 只截「windowOfEntry + pickCandidateV2」这一小段纯函数区：
//   起点是 function windowOfEntry，终点是 pickCandidateV2 的收尾（下一段注释开头）。
// ⚠️ 边界必须精确：切多了会带进 export 语句，new Function 直接语法错（踩过一次）。
const start = SRC.indexOf('function windowOfEntry');
const anchorEnd = SRC.indexOf('// ══ 【P0 修复·路由无鉴权】', start);
const end = anchorEnd > start ? anchorEnd : SRC.indexOf('function modelKey', start);
const block = SRC.slice(start, end > start ? end : start + 8000);
const factory = new Function(block + '\nreturn { pickCandidateV2, windowOfEntry, RETRY_AFTER_MS, cooledDown };');
const { pickCandidateV2, windowOfEntry, RETRY_AFTER_MS } = factory();

const chain = [
  { provider: 'A', model: 'a1' },
  { provider: 'B', model: 'b1' },
  { provider: 'C', model: 'c1' },
  { provider: 'D', model: 'd1' },
  { provider: 'E', model: 'e1' },
];
const k = (e) => e.provider + '\u0000' + e.model;

console.log('\n=== ⑤ 全部试过之后，回头试已冷却的 ===');
{
  // ⚠️ 场景必须让【所有候选都试过】才谈得上"回头"。
  //   第一次写测试时只标了前 4 个试过，第 5 个 E 没试过 →
  //   它当然在 FIRST_TRY 轮就被选中（那是正确行为，不是 bug）。
  const tried = new Set();
  const times = new Map();
  const old = Date.now() - RETRY_AFTER_MS - 1000;
  for (const e of chain) { tried.add(k(e)); times.set(k(e), old); }
  const r = pickCandidateV2(chain, tried, { triedTimes: times });
  ck('★ 全部试过且都已冷却 → 回头选 A', r.entry && r.entry.provider === 'A', r.reason);
  ck('判定为 RETRY_COOLED', r.reason === 'RETRY_COOLED');
}

console.log('\n=== ⑤-变体 4 个试过、第 5 个还没试 → 应先试第 5 个 ===');
{
  const tried = new Set();
  const times = new Map();
  const old = Date.now() - RETRY_AFTER_MS - 1000;
  for (let i = 0; i < 4; i++) { tried.add(k(chain[i])); times.set(k(chain[i]), old); }
  const r = pickCandidateV2(chain, tried, { triedTimes: times });
  ck('★ 优先试未试过的 E（首次尝试优先于回头）',
     r.entry && r.entry.provider === 'E', String(r.entry && r.entry.provider) + ' ' + r.reason);
}

console.log('\n=== ⑤-补 冷却中不回头（避免立刻撞同一个限流窗口） ===');
{
  const tried = new Set();
  const times = new Map();
  const just = Date.now();   // 刚刚才失败
  for (let i = 0; i < 4; i++) { tried.add(k(chain[i])); times.set(k(chain[i]), just); }
  const r = pickCandidateV2(chain, tried, { triedTimes: times });
  ck('★ 未冷却时不回头，选第 5 个 E', r.entry && r.entry.provider === 'E', String(r.entry && r.entry.provider) + ' ' + r.reason);
}

console.log('\n=== ③ 小窗口模型必须跳过 ===');
{
  const chainW = [
    { provider: 'small', model: 's', contextWindow: 40000 },
    { provider: 'big', model: 'b', contextWindow: 200000 },
  ];
  // 当前占用 50578（正是现场那个数）
  const r = pickCandidateV2(chainW, new Set(), { needTokens: 50578 });
  ck('★ 40K 窗口装不下 50578 → 跳过', r.entry && r.entry.provider === 'big',
     String(r.entry && r.entry.provider) + ' ' + r.reason);
}

console.log('\n=== ③-补 全都不够大时要明确报 NO_WINDOW_FITS ===');
{
  const chainW = [
    { provider: 's1', model: 'm', contextWindow: 40000 },
    { provider: 's2', model: 'm', contextWindow: 32000 },
  ];
  const r = pickCandidateV2(chainW, new Set(), { needTokens: 50578 });
  ck('★ 无候选返回 reason=NO_WINDOW_FITS', r.entry === null && r.reason === 'NO_WINDOW_FITS', r.reason);
}

console.log('\n=== ③-补 未声明窗口的候选仍允许试（不能因未知就拒绝） ===');
{
  const chainW = [{ provider: 'unknown', model: 'u' }];
  const r = pickCandidateV2(chainW, new Set(), { needTokens: 50578 });
  ck('未知窗口 → 允许尝试', r.entry && r.entry.provider === 'unknown', r.reason);
}

console.log('\n=== 隔离的 provider 优先跳过 ===');
{
  const r = pickCandidateV2(chain, new Set(), {
    isQuarantined: (p) => p === 'A',
  });
  ck('跳过被隔离的 A', r.entry && r.entry.provider === 'B', String(r.entry && r.entry.provider));
}

console.log('\n=== 源码契约 ===');
ck('③ 从错误文本解析实际占用',
   SRC.includes('estimated\\s*tokens'));
ck('③ 配置支持 contextWindow', SRC.includes('entry.contextWindow = Math.floor(cw)'));
ck('③ 区分 NO_WINDOW_FITS 与 ALL_TRIED',
   SRC.includes('NO_WINDOW_FITS') && SRC.includes('ALL_TRIED'));
ck('④ 祖先链上溯（ancestorCache）', SRC.includes('ancestorCache'));
ck('④ 异步预热谱系（warmAncestors）', SRC.includes('warmAncestors'));
ck('⑤ 记录 tried 时间戳（triedTimes）', SRC.includes('st.triedTimes'));
ck('⑤ 冷却期常量存在', SRC.includes('RETRY_AFTER_MS'));

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
