/**
 * 模型自动接替 · 目录收集与 UI 下拉 契约测试
 *
 * 背景（用户反馈 2026-10-30）：
 *   「这我也看不到是啥模型啊，而且你起码给我一个可以有模型表的模型让我选择吧」
 *   —— 原版只能手打 provider/model，《设置页》里还出现黑底黑字看不见内容。
 *
 * 【2026-10-30 更新】v1 的两个用例（A1/A4）原本 mock 的是
 *   llm.adapters.get(pid).adapter.listModels()；后来发现那样拿不全模型
 *   （用户反馈"供应商就给俩家"），实现已改为官方同款 API：
 *   llm.listProviders / listModels(id) / resolveModelInfo。
 *   对应新断言见 test_model_catalog_v2.mjs（A1~A4）。本文件只保留
 *   仍然有效的 UI / 兜底断言。
 *
 * 本次修复：
 *   1) Host 新增 GET /model-catalog，用宿主真实注册信息拼出模型目录；
 *   2) Client 把两个自由输入框改成【下拉选择】，并显式写死颜色。
 *
 * 本测试验证：
 *   A. 目录收集函数在 mock ctx.llm 下的行为（去重、分组、兜底链内模型、容错）
 *   B. 源码接线（路由存在 / UI 用 select 而非 input / 颜色不再 inherit）
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

// ── 用与其它回归测试相同的定位口径（工作区 / 安装副本都能跑）────────────
// 工作区：<repo>/engineering/plugins/dsh-engineering-ui/lib
// 安装副本：插件在 profiles/<p>/node_modules 下 → 由 MC_LIB 或探测提供
import { existsSync } from 'node:fs';
const HERE = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname)
  .replace(/^\/([A-Za-z]:)/, '$1'));

function resolveLib() {
  if (process.env.MC_LIB) return process.env.MC_LIB;
  const cands = [
    path.join(HERE, '..', 'plugins', 'dsh-engineering-ui', 'lib'),
  ];
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) {
    for (const p of ['desktop', 'web', 'bili-duty']) {
      cands.push(path.join(home, '.dsh', 'profiles', p, 'node_modules', 'dsh-engineering-ui', 'lib'));
    }
  }
  for (const c of cands) { try { if (existsSync(path.join(c, 'index.js'))) return c; } catch (e) {} }
  return cands[0];
}
const LIB = resolveLib();
const HOST = readFileSync(path.join(LIB, 'index.js'), 'utf8');
const CLIENT = readFileSync(path.join(LIB, 'client.js'), 'utf8');

// ── 抽取 Host 里的 collectModelCatalog（含它的依赖 pushModel 等）──────────
function extractCatalogFn(src) {
  const start = src.indexOf('/**');
  const i = src.indexOf('async function collectModelCatalog');
  if (i < 0) throw new Error('collectModelCatalog 不存在');
  // 从函数头开始，按大括号配平截取
  let depth = 0, j = src.indexOf('{', i), k = j;
  for (; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}') { depth--; if (depth === 0) { k++; break; } }
  }
  return src.slice(i, k);
}

test('A2 collectModelCatalog：catalog 无广告时仍保留链里已保存的模型', async () => {
  const fnSrc = extractCatalogFn(HOST);
  const stub = 'function readModelChainConfig(){ return { enabled:true, entries:[{provider:"saved",model:"only-one"}] }; }';
  const factory = new Function('ctx', stub + '\n' + fnSrc + '\nreturn collectModelCatalog;');
  const ctx = { llm: { listProviders: () => [], adapters: new Map(), listConfigurableProviders: () => [] } };
  const cat = await factory(ctx)();
  assert.equal(cat.ok, true);
  const hit = cat.models.find((m) => m.provider === 'saved' && m.model === 'only-one');
  assert.ok(hit, '已保存的模型必须保留在列表里，否则一保存就被清空');
  assert.equal(hit.fromChain, true);
});

test('A3 collectModelCatalog：llm 完全不可用也不抛错', async () => {
  const fnSrc = extractCatalogFn(HOST);
  const stub = 'function readModelChainConfig(){ return { enabled:false, entries:[] }; }';
  const factory = new Function('ctx', stub + '\n' + fnSrc + '\nreturn collectModelCatalog;');
  const cat = await factory({})();
  assert.equal(cat.ok, true, '必须返回 ok:true（宁可列表空，也不能让设置页崩）');
  assert.ok(Array.isArray(cat.errors) && cat.errors.length > 0, '应记录 llm 不可用');
});

test('B1 源码：有 /model-catalog 路由且按 provider 分组返回', () => {
  assert.ok(HOST.includes("PREFIX + '/model-catalog'"), '缺少 /model-catalog 路由');
  assert.ok(HOST.includes('groups.sort'), '应按 provider 分组并排序');
});

test('B2 源码：设置页用 select 下拉而非纯 input', () => {
  assert.ok(CLIENT.includes("className: 'eng-mc-sel'"), '缺少下拉类名');
  assert.ok(CLIENT.includes("'/dsh-engineering-ui/model-catalog'"), 'UI 未请求模型目录');
  assert.ok(CLIENT.includes('modelsOf('), '应能按 provider 过滤模型');
});

test('B3 源码：输入框颜色必须显式写死（黑底黑字回归）', () => {
  const m = CLIENT.match(/\.eng-mc-in\{([^}]*)\}/);
  assert.ok(m, '找不到 .eng-mc-in 样式');
  assert.ok(!/color:inherit/.test(m[1]),
    '.eng-mc-in 不得用 color:inherit —— 实测会变成黑底黑字看不见');
  assert.ok(/color:#1f2328/i.test(m[1]), '应显式指定深色文字');
  assert.ok(/background:#fff/i.test(m[1]), '应显式指定白色背景');
});

test('B4 源码：保留"手填"兜底（目录不全时仍能配置）', () => {
  assert.ok(CLIENT.includes('toggleManual'), '缺少手填切换');
  assert.ok(CLIENT.includes('eng-mc-manual'), '缺少手填按钮样式');
});

console.log('=== model catalog tests loaded ===');
