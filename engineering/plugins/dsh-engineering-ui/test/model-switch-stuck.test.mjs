/**
 * 换模型卡死救急回归（2026-11-28）
 *
 * 现场：报过 "Cannot switch to trae/mimo-v2.6-flash: compaction still above
 *   threshold (50578 >= 40000)" 之后，子代理及其二级子代理【固定停在原模型】，
 *   哪怕把 trae 从模型链里删掉、重启 DSH 也不换。
 *
 * 三个根因（都会导致"永远换不动"）：
 *   ① pendingKey 只在内存里、且按 agent.id 索引 —— 子代理 dispose 重建后丢失；
 *   ② 候选从链里删掉后，want===undefined，旧代码既不重放也不清理 → 死锁；
 *   ③ providerQuarantine / tried / failCounts 跨轮保留，配置变了也不失效。
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

console.log('\n=== ① pendingKey 必须跨会话重建保留 ===');
ck('有落盘文件名常量', SRC.includes("PENDING_FILE = 'model_chain_pending.json'"));
ck('有写入函数 rememberPending', SRC.includes('function rememberPending'));
ck('有读取函数 recallPending', SRC.includes('function recallPending'));
ck('★ 按 sessionId 而非 agent.id 索引',
   SRC.includes('const sid = chainSessionId(agent)') &&
   SRC.includes('agent?.session?.id || agent?.sessionId || agent?.id') &&
   SRC.includes('rememberPending(sid, { key, fromKey: current'));
ck('★ pre-step 回读磁盘兜底', SRC.includes('const fromDisk = recallPending(sid)'));
ck('生效后清盘', SRC.includes('rememberPending(sid, null)'));

console.log('\n=== ② 候选被删后必须主动脱离（本次卡死的直接原因） ===');
ck('★ 有"候选已从链里移除"分支',
   SRC.includes('原候选已从模型链移除'));
ck('清掉失效的 pending', SRC.includes('if (!want || modelKey(want.provider, want.model) === currentKey)') &&
   SRC.includes('rememberPending(sid, null);'));
ck('★ 按当前链重新挑候选', SRC.includes('改按当前链重新挑'));
ck('无候选时交还宿主（不空转）', SRC.includes('没有可用候选，交还宿主'));

console.log('\n=== ③ 配置变更要让历史判定失效 ===');
ck('有配置指纹', SRC.includes('chainFingerprint'));
ck('指纹函数', SRC.includes('function fingerprintOf'));
ck('★ 变更时清隔离表', SRC.includes('providerQuarantine.clear()'));
ck('★ 变更时清已试集合', SRC.includes('st.tried = new Set();'));
ck('变更时清失败计数', SRC.includes('st.failCounts = new Map();'));
ck('读配置时自动比对', SRC.includes('syncChainFingerprint(cfg)'));

console.log('\n=== 反向：不能把链外模型写进去（原有保护保留） ===');
ck('select 仍拒绝链外模型', SRC.includes('只允许写【链内】候选'));

console.log('\n=== 复刻判定：删掉 trae 后应能换到别的 ===');
function pickAfterRemoval(chain, pendingKey) {
  // 复刻分支：want 找不到 → 按当前链重新挑第一个
  const want = chain.find(e => (e.provider + '\u0000' + e.model) === pendingKey);
  if (want) return want;
  return chain[0] || null;
}
{
  const chain = [{ provider: 'workbuddy', model: 'hy4flash-free' },
                 { provider: 'buddy', model: 'hy4flash-free' }];
  const dead = 'trae' + '\u0000' + 'mimo-v2.6-flash';
  const got = pickAfterRemoval(chain, dead);
  ck('★ 原候选 trae 已删除 → 改选 workbuddy',
     got && got.provider === 'workbuddy', JSON.stringify(got));
  ck('★ 不会返回已被删除的 trae', !got || got.provider !== 'trae');
}

console.log('\n' + '='.repeat(52));
console.log('PASS=' + pass + ' FAIL=' + fail);
process.exit(fail ? 1 : 0);
