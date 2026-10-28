/**
 * 【小标签拖动】垂直方向必须真的能动，且吸附只改 X 不改 Y
 * ============================================================================
 * 现场（2026-10-28，用户截图 + 原话）：
 *   「这个上下拖不了，我要可以在右边上下拖到的，然后在非右边的时候是需要
 *     放开就吸附到右边的，对于高度不是要固定长度的」
 *
 * 根因（React 闭包旧快照）：
 *   onMouseDown 里注册的 onMove/onUp 闭包捕获的是【本次渲染的】fabDragSt，
 *   而 setState 不会改写闭包里的那个旧元组 —— onUp 里读 `fabDragSt[0].live`
 *   永远是 undefined，于是回退到起点 originY → 松手弹回原位，
 *   垂直位移 100% 丢失（水平看起来正常，因为吸附把 x 又贴回边上）。
 *
 * 修法：实时坐标写 ref（跨渲染稳定），onUp 从 ref 读。
 *
 * 运行：node --test test/fab-drag.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'lib', 'client.js'), 'utf8');

test('★ 拖动实时坐标必须走 ref，不得从闭包里的 state 读', () => {
  assert.ok(SRC.includes('fabLiveRef'), '必须有 fabLiveRef（跨渲染稳定的实时坐标）');
  const iMove = SRC.indexOf('var next = clampFab(originX + dx');
  assert.ok(iMove > 0, '找不到 onMove 里的坐标计算');
  const moveSeg = SRC.slice(iMove, iMove + 320);
  assert.ok(moveSeg.includes('fabLiveRef.current = next'),
    'onMove 必须把实时坐标写进 ref，否则 onUp 读不到');
  const iUp = SRC.indexOf('var live = fabLiveRef.current');
  assert.ok(iUp > 0,
    '★ onUp 必须从 ref 读实时坐标（原来读 fabDragSt[0] 是旧快照 → 垂直拖不动）');
  assert.ok(!/var live\s*=\s*\(?fabDragSt\[0\]/.test(SRC),
    '★ 不得再从 fabDragSt[0] 读 live —— 那是渲染时的旧快照');
});

test('★ 吸附只改 X，保持 Y（高度不固定）', () => {
  const i = SRC.indexOf('var snapFab = function');
  assert.ok(i > 0, '找不到 snapFab');
  const seg = SRC.slice(i, i + 700);
  // 返回体里 y 必须来自传入坐标（c.y），不能被写死/居中
  assert.ok(/return\s*\{\s*x:\s*nx,\s*y:\s*c\.y/.test(seg),
    '★ snapFab 必须返回 y: c.y（垂直位置由用户拖到哪算哪），' +
    '不能固定高度或强制居中');
  assert.ok(!/y:\s*(vh|height|innerHeight)\s*\/\s*2/.test(seg),
    '★ 吸附不得把 Y 强制设成视口一半（那就成固定高度了）');
});

test('★ 小标签自身高度必须由内容撑开，不得写死 height', () => {
  const i = SRC.indexOf("'.eng-fab{'");
  assert.ok(i > 0, '找不到 .eng-fab 规则');
  const seg = SRC.slice(i, i + 700);
  assert.ok(!/\bheight\s*:\s*\d+px/.test(seg),
    '★ .eng-fab 不得写死 height（用户明确要求高度不固定）');
  assert.ok(!/\bmin-height\s*:/.test(seg),
    '.eng-fab 不宜设 min-height，内容多少就是多高');
});

test('★ 每次按下都要重置实时坐标（避免上一次的残留）', () => {
  const iDown = SRC.indexOf('var fabOnMouseDown = function');
  assert.ok(iDown > 0, '找不到 fabOnMouseDown');
  const seg = SRC.slice(iDown, iDown + 1200);
  assert.ok(seg.includes('fabLiveRef.current = null'),
    '按下时必须清掉上一次的实时坐标');
});
