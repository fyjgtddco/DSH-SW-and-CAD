/**
 * 子代理模型接替 + 压缩历史分链 回归（2026-11-28）
 *
 * 两个 bug：
 *   ① 压缩历史「子代理」页空白 —— identityOf 用 agent.parent 对象引用找根，
 *      而 0.2.x 把谱系写在 session.header.parentSession（字符串）里。
 *   ② 子代理被限流时不换模型 —— select() 只对下一步生效，子代理失败即 dispose，
 *      新路由没机会被读到；且所有放行分支都是静默的，无从诊断。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ck = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d && !ok ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

const here = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const guarded = readFileSync(here + '../lib/guarded.js', 'utf8');
const uiIndex = readFileSync(here + '../../dsh-engineering-ui/lib/index.js', 'utf8');

// ══════════════════════════════════════════════════════════════════
console.log('\n=== bug① 压缩历史分链 ===');
ck('identityOf 读 header.parentSession',
   guarded.includes('session.header.parentSession'));
// root 已改成「最顶层根会话」，见 ledger-chain.test.mjs 的说明
ck('root 上溯到最顶层', guarded.includes('let rootId = parentId'));
ck('无父会话时才等于 target', guarded.includes('out.root = rootId || out.target'));
ck('不再以 agent.parent 对象引用为首选',
   !guarded.includes('let cur = agent; let guard = 0; let root = out.target;'));

// ══════════════════════════════════════════════════════════════════
console.log('\n=== bug② 子代理模型接替 ===');
ck('request-error 有诊断开关',
   uiIndex.includes('DSH_ENG_MODEL_DIAG'));
ck('NOT_APPLIES 分支会说明原因',
   uiIndex.includes("_diag('NOT_APPLIES'"));
ck('NOT_SWITCH_WORTHY 分支会说明原因',
   uiIndex.includes("_diag('NOT_SWITCH_WORTHY'"));
ck('NO_CANDIDATE_LEFT 分支会说明原因',
   uiIndex.includes("_diag('NO_CANDIDATE_LEFT'"));
ck('SELECT_WRITTEN 会报告写入结果',
   uiIndex.includes("_diag('SELECT_WRITTEN'"));
ck('★ 无法续跑时明确告警（不静默放弃）',
   uiIndex.includes('模型接替已写入但无法自动续跑'));
ck('★ 区分子代理 / 主对话',
   uiIndex.includes('const isChild = !!parentSessionId(agent)'));

// ══════════════════════════════════════════════════════════════════
console.log('\n=== 反向：不能把既有保护改掉 ===');
ck('链为空仍完全不介入',
   uiIndex.includes('if (!cfg.entries.length) return false;'));
ck('子代理父级未知仍保守按工程处理',
   uiIndex.includes('父级预设未知'));
ck('provider 隔离仍然存在',
   uiIndex.includes('function quarantineProvider'));
ck('绝不返回 retry（避免白烧请求）',
   uiIndex.includes("绝不返回 {kind:'retry'}"));

console.log('\n' + '='.repeat(50));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
