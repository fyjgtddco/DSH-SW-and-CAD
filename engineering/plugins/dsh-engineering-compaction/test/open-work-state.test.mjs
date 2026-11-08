#!/usr/bin/env node
/**
 * 【B6 回归测试】压缩不许丢补丁：open_work_state 必须结构化且只增不减。
 *
 * 对应缺陷（01-缺陷清单与日志定位.md · B6，P1 · ★P3）：
 *
 *   现场（2026-11-07 会话 · 总装 #2 0b161dc4:1536）：
 *     "sel_snap: []——又是 dict 没解包！step2h 直接把 face dict 喂给了
 *      _select_face（step2f 的解包补丁没带过来）"
 *   产物：asm_step2a…2h **8 版脚本、41 次 write/edit** ——
 *   上一版已修好的补丁被压缩摘要在"过程噪音"里删掉，下一版又从零写起。
 *
 * 判据：
 *   ① buildOpenWorkState 能把 doing/files/patched/pitfalls/next_step 结构化成区块；
 *   ② 提示词必须把该区块标成"不可压缩 / 原样复制 / 补丁点禁止重写"；
 *   ③ writeOpenWorkState 合并时【补丁点只增不减】（本次没提也不许删）；
 *   ④ missingOpenWork 能检出摘要里丢掉的补丁点；
 *   ⑤ 摘要提示词必须禁止"改参数规避检测"这类措辞（A3 同源）。
 *
 * 用法：node --test test/open-work-state.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', 'lib');

// history.js 依赖 state dir；用临时目录隔离，绝不碰生产状态
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-b6-'));
process.env.DSH_STATE_DIR = TMP;

// 注意：Windows 上 path.join 得到 "C:\..."，直接 import 会报
// ERR_UNSUPPORTED_ESM_URL_SCHEME（会被当成自定义 scheme）—— 必须转成 file:// URL。
const { pathToFileURL } = await import('node:url');
const hist = await import(pathToFileURL(path.join(LIB, 'history.js')).href);
const promptMod = await import(pathToFileURL(path.join(LIB, 'prompt.js')).href);

test('① buildOpenWorkState 结构化输出', () => {
  const r = hist.buildOpenWorkState({
    doing: '修 asm_step2 的面选择解包',
    files: [{ path: 'asm_step2.py', role: '总装脚本' }],
    patched: [{ what: 'dict 解包补丁', where: '_select_face', how: "f['faces'] 先解包" }],
    pitfalls: [{ what: '直接喂 face dict', why: 'sel_snap 会空' }],
    next_step: '重跑 step2h 验证',
  });
  assert.equal(r.ok, true);
  assert.ok(r.block.includes('dict 解包补丁'), '补丁点必须在区块里');
  assert.ok(r.block.includes('禁止重写'), '必须写明补丁点禁止重写');
  assert.ok(r.block.includes('asm_step2.py'), '文件必须在区块里');
  assert.ok(r.block.includes('重跑 step2h'), '下一步必须在区块里');
});

test('①-b 空输入输出 (none)，不伪造内容', () => {
  const r = hist.buildOpenWorkState({});
  assert.equal(r.block, '(none)');
});

test('② 提示词把该区块标成不可压缩', () => {
  const text = promptMod.buildCompactionInstruction({
    factsBlock: '',
    prevL1: '', prevL2: '',
    openWorkBlock: '- 正在做: X\n- ★已修好的补丁点: Y',
    hasCheckpoint: false,
  });
  assert.ok(text.includes('不可压缩'), '必须标注不可压缩');
  assert.ok(text.includes('VERBATIM'), '必须要求原样复制');
  assert.ok(text.includes('禁止重写'), '必须写明补丁点禁止重写');
});

test('③ writeOpenWorkState 合并时补丁点只增不减', () => {
  const t = 'session-a';
  hist.writeOpenWorkState({
    doing: '第一轮',
    patched: [{ what: '补丁A' }],
  }, t);
  // 第二轮没提补丁A
  hist.writeOpenWorkState({
    doing: '第二轮',
    patched: [{ what: '补丁B' }],
  }, t);
  const got = hist.readOpenWorkState(t);
  const whats = (got.patched || []).map((p) => p.what);
  assert.ok(whats.includes('补丁A'), '上一次的补丁点不许被删（B6 核心），实际 ' + JSON.stringify(whats));
  assert.ok(whats.includes('补丁B'), '新补丁点要并进来');
  assert.equal(got.doing, '第二轮', 'doing 用最新的');
});

test('④ missingOpenWork 能检出摘要里丢掉的补丁点', () => {
  const state = { doing: '正在修解包', patched: [{ what: 'dict 解包补丁' }], next_step: '重跑' };
  const miss = hist.missingOpenWork('摘要里只写了正在修解包', state);
  const labels = miss.map((m) => m.label);
  assert.ok(labels.includes('patched'), '补丁点丢了必须被检出，实际 ' + JSON.stringify(labels));
  assert.ok(labels.includes('next_step'), '下一步丢了必须被检出');
  assert.ok(!labels.includes('doing'), '写进去的不应误报');
});

test('⑤ 摘要提示词禁止"改参数规避检测"措辞（A3 同源）', () => {
  const text = promptMod.buildCompactionInstruction({ factsBlock: '', prevL1: '', prevL2: '', hasCheckpoint: false });
  assert.ok(text.includes('禁止写进摘要的内容'), '必须有禁用段落');
  assert.ok(text.includes('vary the command each round'),
    '必须点名这条被踩过的错误经验');
  assert.ok(text.includes('绝不允许写进 L2'), '必须明确不许进 L2 经验');
});
