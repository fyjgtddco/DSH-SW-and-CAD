/**
 * compaction-selfdefeat-20261112.test.mjs — 【压缩"压不动"】三个自毁漏洞的回归
 *
 * ── 现场（379 个会话全扫，46 个有压缩事件，277 次 compaction/end）────────
 * 用户体感「压缩插件一直没生效」，实际是两层叠加：
 *   (A) 阈值够不着：换了 1M 窗口模型后阈值 = 1000000×0.8 = 800K，
 *       实测峰值 479K/511K，永远不触发（这是正确的，不是 bug）；
 *   (B) 一旦够着就自毁：真正触发过的会话里，压缩失败率极高 ——
 *         · 26 次 `ENG_COMPACTION_NOT_SMALLER`（回填后比原文长，最坏 2.79 倍）
 *         · 54 次 `trae: llm response empty stream (code=3003)`（摘要被截断）
 *
 * 本文件钉死三个根因的修复，任何一条回退都会立刻红。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { restoreFacts, mergeFacts, factsStats } from '../lib/facts.js';
import { readFacts, writeFacts, factsPath } from '../lib/history.js';

// ══════════════════════════════════════════════════════════════════════════
// 根因 1：restoreFacts 回填把摘要撑爆（26 次 NOT_SMALLER 的直接来源）
// ══════════════════════════════════════════════════════════════════════════
function makeFacts(n, weight) {
  return {
    numbers: Array.from({ length: n }, (_, i) => ({
      key: 'F' + i, raw: 'F' + i + '=12.5', value: 12.5,
      weight: weight || (i % 2 === 0 ? 'high' : 'low'),
      line: '第 ' + i + ' 行上下文：切向力 Ft=2T/d1 计算过程……',
    })),
    materials: [],
  };
}

test('① 回填必须带硬上限，不能把摘要撑到比原文还长', () => {
  const facts = makeFacts(5000);              // 实测现场是 5000~7000 条
  const summary = '压缩后的摘要正文。';        // 很短，所有事实都"缺失"
  const r = restoreFacts(summary, facts, {});
  assert.ok(r.text.length > summary.length, '确实回填了一些');
  // 200 条上限 × 每条 ~10 字符 ≈ 数千字符，绝不能到几万
  assert.ok(r.text.length < 20000,
    '回填后长度必须受控，实际 ' + r.text.length + ' 字符');
  assert.equal(r.capped, true, '超限时必须标 capped');
  assert.ok(r.skipped > 0, '要如实报出还有多少条没列');
  assert.match(r.text, /另有 \d+ 条/, '必须标注未列出的条数');
});

test('①b 回填不再带「← 上下文」尾巴（那是长度翻倍的直接原因）', () => {
  const facts = makeFacts(3);
  const r = restoreFacts('摘要', facts, {});
  assert.equal(r.text.includes('←'), false,
    '不得再输出 ← 上下文尾巴：' + r.text);
});

test('①c 默认只回填 high 权重（低权重交给摘要正文）', () => {
  const facts = {
    numbers: [
      { key: 'F1', raw: 'F1=100', weight: 'high' },
      { key: 'F2', raw: 'F2=200', weight: 'low' },
    ],
    materials: [],
  };
  const r = restoreFacts('摘要', facts, {});
  assert.ok(r.text.includes('F1=100'), 'high 必须回填');
  // low 仍可能被列入（有配额），但 high 必须排在前面
  const iHigh = r.text.indexOf('F1=100');
  const iLow = r.text.indexOf('F2=200');
  if (iLow >= 0) assert.ok(iHigh < iLow, 'high 必须排在 low 之前');
});

test('①d 二次校验仍失败时必须留告警（而不是静默或整块附原文）', () => {
  // 构造"回填也补不完"的场景：500 条超长数值，maxChars 上限会截掉绝大部分
  const facts = {
    numbers: Array.from({ length: 500 }, (_, i) => ({
      key: 'K' + i,
      raw: 'K' + i + '=' + '9'.repeat(200),
      weight: 'high',
    })),
    materials: [],
  };
  const r = restoreFacts('摘要', facts, {});
  assert.equal(r.capped, true, '必须标 capped');
  assert.match(r.text, /⚠️/, '补不完时必须留下告警标记：' + r.text.slice(-300));
  assert.match(r.text, /仍有 \d+ 条数值未能逐字回填/, '要说明还剩多少条');
  // 关键：绝不能把未回填的原文整块附上
  assert.ok(r.text.length < 30000, '长度必须受控，实际 ' + r.text.length);
});

test('①e 已经完备的摘要不会被改动', () => {
  const facts = { numbers: [{ key: 'F1', raw: 'F1=100', weight: 'high' }], materials: [] };
  const good = '摘要里已经有 F1=100 了';
  const r = restoreFacts(good, facts, {});
  assert.equal(r.restored, 0);
  assert.equal(r.text, good);
});

// ══════════════════════════════════════════════════════════════════════════
// 根因 2：L0 事实集跨会话全局累积（l0_numbers 3064→7007 的成因）
// ══════════════════════════════════════════════════════════════════════════
test('② facts 按 target 分文件，不同会话互不污染', () => {
  const p1 = factsPath('session-aaaa');
  const p2 = factsPath('session-bbbb');
  assert.notEqual(p1, p2, '不同会话必须落到不同文件');
  assert.ok(p1.includes('compaction_facts'), '要落到 compaction_facts 子目录');
});

test('②b 空 target 仍回落全局文件（向后兼容旧调用方/测试）', () => {
  const p = factsPath('');
  assert.ok(p.endsWith('compaction_facts.json'), '空 target 回到旧全局文件: ' + p);
  assert.ok(!p.includes(path.sep + 'compaction_facts' + path.sep), '不是子目录');
});

test('②c 写入一个会话的事实，另一个会话读不到', () => {
  // 用真实状态目录（resolveStateDir 会按 DSH_STATE_DIR 走）
  const prev = process.env.DSH_STATE_DIR;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'facts-'));
  try {
    process.env.DSH_STATE_DIR = tmp;
    // 模块内 resolveStateDir 每次调用都重新解析（无缓存），所以环境变量生效
    const A = { numbers: [{ key: 'F1', raw: 'F1=1', weight: 'high' }], materials: [] };
    const w = writeFacts(A, 'session-aaaa');
    assert.equal(w.ok, true, JSON.stringify(w));

    const gotA = readFacts('session-aaaa');
    assert.equal(gotA.numbers.length, 1, '自己的事实读得到');

    const gotB = readFacts('session-bbbb');
    assert.equal(gotB.numbers.length, 0,
      '别的会话不得看到 A 的事实（旧实现会串味）');
  } finally {
    if (prev === undefined) delete process.env.DSH_STATE_DIR;
    else process.env.DSH_STATE_DIR = prev;
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
  }
});

// ══════════════════════════════════════════════════════════════════════════
// 根因 3：headroomTokens 同时是摘要器 maxTokens（这个耦合由 preset 修）
// ══════════════════════════════════════════════════════════════════════════
test('③ preset 必须显式声明 maxTokens（否则被 headroom 压到 8000 → 必截断）', () => {
  const yml = path.join(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..', '..', 'dsh-engineering-ui', 'preset-engineering.patch.yml');
  const txt = fs.readFileSync(yml, 'utf8');
  // 直接在整个文件的"末段配置区"找：compaction-engineering 之后、
  // tool-result-pruner 之前的 yaml 配置行（注释里出现过的值不算）
  const start = txt.indexOf('- id: compaction-engineering');
  assert.ok(start > 0, 'preset 里必须有 compaction-engineering 这一行');
  const tail = txt.slice(start);
  const end = tail.indexOf('- id: command-compact');
  const block = end > 0 ? tail.slice(0, end) : tail;
  // 去掉注释行，只看真正的 yaml 配置
  const cfg = block.split(/\r?\n/)
    .filter((l) => !/^\s*#/.test(l))
    .join('\n');
  const m = cfg.match(/maxTokens:\s*(\d+)/);
  assert.ok(m, 'compaction-engineering 必须显式配 maxTokens（实际配置区：\n' + cfg + '\n）');
  // 【2026-11-12 调整】判据不再是"越大越好"：
  //   48000 在 11-01/11-07（trae/200K 窗口）时代能抄完编号，
  //   但当前路由 buddy 的 maxTokens=128000，chunkChars(36000字符)+maxTokens(16000)
  //   ≈ 52K 才留足余量；照搬 48000 会贴边 128K → 截断 → 报错。
  //   所以这里只要求"明显大于曾经致命的 8000"，具体值交给 ④ 那条做和校验。
  assert.ok(Number(m[1]) >= 12000,
    'maxTokens 必须明显大于 8000（曾因 8000 导致 54 次 empty-stream），实际 ' + m[1]);
  // 同时确认 headroom 仍是 8000（阈值口径不变）
  assert.match(cfg, /headroomTokens:\s*8000/, 'headroomTokens 应保持 8000');
});

test('③b mergeFacts 仍是只增不减（这是用户的硬要求，不能被改掉）', () => {
  const a = { numbers: [{ key: 'F1', raw: 'F1=1', weight: 'high' }], materials: [] };
  const b = { numbers: [{ key: 'F2', raw: 'F2=2', weight: 'high' }], materials: [] };
  const m = mergeFacts(a, b);
  assert.equal(m.numbers.length, 2, '必须并集（只增不减）');
  // 同一个 key 不重复
  const m2 = mergeFacts(m, b);
  assert.equal(m2.numbers.length, 2, '重复 key 不得累加');
});

test('③c factsStats 仍能给出 high 计数（面板依赖它）', () => {
  const s = factsStats(makeFacts(10));
  assert.equal(s.numbers, 10);
  assert.equal(s.high, 5);
});
