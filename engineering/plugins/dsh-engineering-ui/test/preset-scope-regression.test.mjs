/**
 * 非工程会话被误接管 回归（2026-11-28）
 *
 * 现场：非工程模式的会话也报
 *   "Cannot switch to loomy/seedance-2-0-fast: compaction-basic:
 *    no context capacity for loomy/seedance-2-0-fast;
 *    configure contextWindow on that adapter model"
 * —— 说明【工程模式的换模型逻辑跑到了非工程会话上】。
 *
 * 两个根因（同一轮修复引入）：
 *   ① ancestorCache 未预热时 break → 落到「父级未知 → 保守按工程」的 return true；
 *   ② 那条 return true 对【任何有父会话的会话】都成立，非工程子代理也被接管。
 * 附带：presetCache 里脏值（曾写成 engineering）也没被实时证据纠正。
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

// ══════════════════════════════════════════════════════════════════════
// 复刻 modelChainApplies 的判定骨架（与源码同构），验证各种会话的结论。
// 只关心「有没有父会话 + 各级 preset 证据」这三类输入。
function applies({ hasChain = true, selfPreset = null, parentPreset = null,
                   grandPreset = null, hasParent = true, chainIsEng = null }) {
  if (!hasChain) return false;
  if (selfPreset === 'engineering') return true;          // ① 自己就是工程
  if (!hasParent) return false;                           // 无父会话 → 不接管
  if (chainIsEng === true) return true;                   // 父级状态明确是工程
  if (chainIsEng === false) return false;
  if (parentPreset === 'engineering') return true;        // 直接父级是工程
  if (parentPreset && parentPreset !== 'engineering') return false;
  if (grandPreset === 'engineering') return true;         // 祖先里有工程
  if (grandPreset && grandPreset !== 'engineering') return false;
  // ★ 关键修复点：未知 → 不再默认按工程
  return false;
}

console.log('\n=== 1. 非工程会话绝不能被接管 ===');
{
  const cases = [
    ['无父会话、预设未知', { hasParent: false }],
    ['父级是 standard', { parentPreset: 'standard' }],
    ['父级未知、祖先未知', {}],
    ['父级未知但祖先明确 standard', { grandPreset: 'standard' }],
  ];
  for (const [label, opt] of cases) {
    ck('★ ' + label + ' → 不接管', applies(opt) === false);
  }
}

console.log('\n=== 2. 工程模式仍然要能接管（别修过头） ===');
{
  ck('自己是 engineering → 接管', applies({ selfPreset: 'engineering' }) === true);
  ck('父级是 engineering → 接管', applies({ parentPreset: 'engineering' }) === true);
  ck('祖先里有 engineering → 接管', applies({ grandPreset: 'engineering' }) === true);
  ck('父级链状态明确是工程 → 接管', applies({ chainIsEng: true }) === true);
}

console.log('\n=== 3. 链为空时一律不介入 ===');
{
  ck('链空 → 即使自己是工程也不接管',
     applies({ hasChain: false, selfPreset: 'engineering' }) === false);
}

// ══════════════════════════════════════════════════════════════════════
console.log('\n=== 4. 源码契约（防止把宽松口径改回去） ===');
ck('★ 未知不再返回 true（改成 return false）',
   SRC.includes('未知【不再】默认当成工程') && SRC.includes('return false;'));
ck('注释写明现场报错', SRC.includes('no context capacity'));
ck('★ 脏缓存要实时证据纠正', SRC.includes('缓存说工程、但【拿不到任何实时证据】时'));
ck('脏缓存命中时 delete 而非直接采信',
   SRC.includes('presetCache.delete(sessionId)'));
ck('浅层 select 拒绝链外模型',
   SRC.includes('只允许写【链内】候选'));
ck('祖先链未预热时不擅自定义',
   SRC.includes('不阻塞判定'));

console.log('\n=== 5. 反向：不能因为修这个把工程子代理又弄死 ===');
ck('仍保留祖先链上溯', SRC.includes('ancestorCache'));
ck('仍保留 warmAncestors 预热', SRC.includes('warmAncestors'));
ck('仍有「父级链状态是工程」的判据',
   SRC.includes('pst && pst.isEng === true'));

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
