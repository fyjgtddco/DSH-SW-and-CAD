/**
 * facts.test.mjs — 数值保险箱的回归测试
 *
 * 守住用户的硬约束：「压缩 N 次数值一个都不能动」。
 * 这些用例全部是纯函数级，不依赖宿主、不碰网络，可直接 node --test 跑。
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractFacts, extractMaterials, renderFactsBlock,
  verifySummary, restoreFacts, mergeFacts, factPresent, factsStats,
} from '../lib/facts.js';

const SAMPLE = [
  '## 底座法兰',
  '材料选用 Q235，板厚 12mm，外径 φ120，内孔 φ24H7。',
  '螺栓孔 M8×1.25，共 6 个，均布于 φ100 圆上。',
  '圆角 R3，表面粗糙度 Ra1.6。',
  '额定载荷 45N，安全系数取 2.5。',
  '弹性模量 206000 MPa，泊松比 0.3。',
].join('\n');

test('extractFacts 能抓到尺寸/公差/材料参数', () => {
  const f = extractFacts(SAMPLE, { room: '壳体机架', part: 'DSH_底座法兰' });
  const raws = f.numbers.map((x) => x.raw);
  assert.ok(raws.includes('12mm'), '应抓到 12mm，实际: ' + raws.join(','));
  assert.ok(raws.includes('φ120'), '应抓到 φ120，实际: ' + raws.join(','));
  assert.ok(raws.includes('φ24H7'), '应抓到公差 φ24H7，实际: ' + raws.join(','));
  assert.ok(raws.includes('M8×1.25'), '应抓到螺纹 M8×1.25');
  assert.ok(raws.includes('Ra1.6'), '应抓到 Ra1.6');
  assert.ok(raws.includes('45N'), '应抓到 45N');
  assert.ok(raws.includes('206000 MPa'), '应抓到 206000 MPa，实际: ' + raws.join(','));
  assert.ok(raws.includes('0.3'), '应抓到泊松比 0.3');
});

test('extractMaterials 抓到国标牌号', () => {
  const m = extractMaterials(SAMPLE);
  assert.ok(m.some((x) => x.value === 'Q235'), '应识别 Q235');
});

test('isNoise: 行号/seq/长串不入账', () => {
  const f = extractFacts('line 1234 seq=99 id=abcdef1234567 正常尺寸 8mm', {});
  const raws = f.numbers.map((x) => x.raw);
  assert.ok(!raws.some((r) => r === '1234'), '行号不该入账');
  assert.ok(!raws.some((r) => r === '99'), 'seq 不该入账');
  assert.ok(raws.includes('8mm'), '真实尺寸应入账');
});

test('verifySummary: 数值齐全时通过', () => {
  const f = extractFacts(SAMPLE, {});
  const summary = '### L0\n- 板厚 12mm\n- 外径 φ120\n- 内孔 φ24H7\n- M8×1.25\n- 6 个孔均布于 φ100\n- Ra1.6\n- 45N\n- 安全系数 2.5\n- 206000 MPa\n- 0.3\n- Q235';
  const v = verifySummary(summary, f);
  assert.equal(v.ok, true, '缺失: ' + v.missing.map((x) => x.raw).join(','));
  assert.equal(v.ratio, 1);
});

test('verifySummary: 漏掉数值必须报缺', () => {
  const f = extractFacts(SAMPLE, {});
  const v = verifySummary('只有 12mm 和 φ120', f);
  assert.equal(v.ok, false);
  assert.ok(v.missing.length > 0);
});

test('verifySummary: 数值被改写（12mm→12 mm 以外，12→13）必须报缺', () => {
  const f = extractFacts('板厚 12mm', {});
  const v = verifySummary('板厚 13mm', f);
  assert.equal(v.ok, false, '13mm 不应被认为保住了 12mm');
});

test('verifySummary: 数字与单位间被插入空格仍算保住', () => {
  const f = extractFacts('板厚 12mm', {});
  const v = verifySummary('板厚 12 mm', f);
  assert.equal(v.ok, true);
});

test('restoreFacts: 模型漏数时必须原样补回', () => {
  const f = extractFacts(SAMPLE, {});
  const bad = '### L0\n- 板厚 12mm';
  const r = restoreFacts(bad, f, { rawSpan: SAMPLE });
  assert.ok(r.restored > 0, '应回填若干条');
  const again = verifySummary(r.text, f);
  assert.equal(again.ok, true, '回填后必须 100% 命中，仍缺: ' + again.missing.map((x) => x.raw).join(','));
  // 回填的是压缩前的原始字面量
  assert.ok(r.text.includes('φ24H7'));
  assert.ok(r.text.includes('206000 MPa'));
});

test('restoreFacts: 已齐全时不该改动文本', () => {
  const f = extractFacts('板厚 12mm', {});
  const good = '板厚 12mm';
  const r = restoreFacts(good, f, {});
  assert.equal(r.restored, 0);
  assert.equal(r.text, good);
});

test('mergeFacts: 只能加不能减（跨多次压缩单调累积）', () => {
  const a = extractFacts('板厚 12mm，外径 φ120', {});
  const b = extractFacts('内孔 φ24H7，载荷 45N', {});   // 完全没有 12mm / φ120
  const m = mergeFacts(a, b);
  const raws = m.numbers.map((x) => x.raw);
  assert.ok(raws.includes('12mm'), '旧事实不能丢');
  assert.ok(raws.includes('φ120'), '旧事实不能丢');
  assert.ok(raws.includes('φ24H7'), '新事实要并进来');
  assert.ok(raws.includes('45N'), '新事实要并进来');
});

test('mergeFacts: 重复合并不会膨胀（幂等）', () => {
  const a = extractFacts(SAMPLE, {});
  const once = mergeFacts(a, a);
  const twice = mergeFacts(once, a);
  assert.equal(once.numbers.length, twice.numbers.length);
  assert.equal(twice.numbers.length, a.numbers.length);
});

test('mergeFacts: 材料也是只增不减', () => {
  const a = extractFacts('材料 Q235', {});
  const b = extractFacts('材料 6061-T6', {});
  const m = mergeFacts(a, b);
  const mats = m.materials.map((x) => x.value);
  assert.ok(mats.includes('Q235'));
  assert.ok(mats.includes('6061-T6'));
});

test('renderFactsBlock: 带禁令与编号', () => {
  const f = extractFacts(SAMPLE, {});
  const s = renderFactsBlock(f);
  assert.ok(s.includes('L0-MACHINE-FACTS'));
  assert.ok(s.includes('禁止改写'));
  assert.ok(/F\d+\./.test(s), '应有 F 编号');
  assert.ok(/M\d+\./.test(s), '应有 M 编号');
});

test('factPresent: 空串不误判', () => {
  assert.equal(factPresent('abc', { raw: '' }), false);
  assert.equal(factPresent('', { raw: '12mm' }), false);
});

test('factsStats: 统计口径', () => {
  const f = extractFacts(SAMPLE, {});
  const s = factsStats(f);
  assert.ok(s.numbers > 0);
  assert.ok(s.materials >= 1);
  assert.ok(s.high > 0, '工程线索行应被标 high');
});

test('极端：同一数值出现在两个零件上算两条事实', () => {
  const t = '零件A 板厚 10mm\n零件B 板厚 10mm';
  const f = extractFacts(t, {});
  const tens = f.numbers.filter((x) => x.raw === '10mm');
  assert.equal(tens.length, 2, '不同上下文里的同值应分别记账');
});
