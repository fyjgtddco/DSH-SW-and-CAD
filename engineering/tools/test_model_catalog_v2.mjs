/**
 * 模型目录收集 + UI 显示 契约测试（对齐官方 buildModelCatalog）
 *
 * 背景（用户反馈 2026-10-30）：
 *   「模型供应商都没给完，就俩家的，要给完，然后模型也没给完」
 *   → v1 用 adapter.listModels(provider路由对象) 拿不全；
 *     现在改用官方同一套：ctx.llm.listProviders / listModels(id) / resolveModelInfo。
 *
 *   「模型显示也要看同步，如图二」→ 下拉显示 model.name 而非 id。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

const HERE = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname)
  .replace(/^\/([A-Za-z]:)/, '$1'));
function resolveLib() {
  const cands = [path.join(HERE, '..', 'plugins', 'dsh-engineering-ui', 'lib')];
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) for (const p of ['desktop', 'web']) {
    cands.push(path.join(home, '.dsh', 'profiles', p, 'node_modules', 'dsh-engineering-ui', 'lib'));
  }
  for (const c of cands) if (existsSync(path.join(c, 'index.js'))) return c;
  return cands[0];
}
const LIB = process.env.MC_LIB || resolveLib();
if (!existsSync(path.join(LIB, 'index.js'))) {
  console.log('[SKIP] 未找到插件源码：' + LIB);
  process.exit(0);
}
const HOST = readFileSync(path.join(LIB, 'index.js'), 'utf8');
const CLIENT = readFileSync(path.join(LIB, 'client.js'), 'utf8');

function extractCatalogFn(src) {
  const i = src.indexOf('async function collectModelCatalog');
  if (i < 0) throw new Error('collectModelCatalog 不存在');
  let depth = 0, k = src.indexOf('{', i);
  for (; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}') { depth--; if (depth === 0) { k++; break; } }
  }
  return src.slice(i, k);
}
function makeFactory(chain) {
  const fnSrc = extractCatalogFn(HOST);
  const stub = 'function readModelChainConfig(){ return ' + JSON.stringify(chain) + '; }';
  return new Function('ctx', stub + '\n' + fnSrc + '\nreturn collectModelCatalog;');
}

test('A1 走官方 API：listProviders + listModels(id) + resolveModelInfo', async () => {
  const factory = makeFactory({ enabled: true, entries: [] });
  const calls = [];
  const ctx = {
    get: (n) => n === 'llm' ? {
      listProviders: () => [
        { id: 'codebuddy', name: 'CodeBuddy (腾讯)' },
        { id: 'raccoon', name: 'Raccoon (商汤)' },
        { id: 'buddy', name: 'Buddy' },
      ],
      listModels: async (pid) => {
        calls.push(pid);
        return pid === 'codebuddy'
          ? [{ id: 'cb-1', name: 'CodeBuddy-Pro' }, { id: 'cb-2', name: 'CodeBuddy-Lite' }]
          : pid === 'raccoon'
            ? [{ id: 'sn-a', name: 'SenseNova-Flash' }]
            : [{ id: 'ds-1', name: 'DeepSeek-V4.1-Flash' }];
      },
      resolveModelInfo: async (pid, mid) => ({
        provider: pid, id: mid,
        name: pid === 'buddy' ? 'DeepSeek-V4.1-Flash' : mid,
        reasoning: mid === 'ds-1'
          ? { efforts: [{ id: 'low', name: 'Lo' }, { id: 'high', name: 'Hi' }], defaultEffort: 'high' }
          : undefined,
      }),
    } : null,
  };
  const cat = await factory(ctx)();
  assert.equal(cat.ok, true);
  assert.deepEqual(calls.sort(), ['buddy', 'codebuddy', 'raccoon'], '必须按 id 调 listModels（不是适配器对象）');
  assert.equal(cat.groups.length, 3, '三家 provider 都要在');
  const total = cat.groups.reduce((s, g) => s + g.models.length, 0);
  assert.equal(total, 4, '模型总数应为 4');
  // provider 名字要带官方 label
  const cb = cat.groups.find((g) => g.id === 'codebuddy');
  assert.equal(cb.label, 'CodeBuddy (腾讯)');
  // 模型要带 name（这就是"模型显示同步"的关键）
  const buddy = cat.groups.find((g) => g.id === 'buddy');
  assert.equal(buddy.models[0].name, 'DeepSeek-V4.1-Flash', '必须用官方 name 而非 id');
  // reasoning 档位要带出来
  assert.ok(buddy.models[0].reasoning, 'reasoning 元信息应保留');
  assert.equal(buddy.models[0].reasoning.efforts.length, 2);
});

test('A2 单个 provider 抛错：只记 failure，其余照常', async () => {
  const factory = makeFactory({ enabled: true, entries: [] });
  const ctx = {
    get: () => ({
      listProviders: () => [{ id: 'bad', name: 'B' }, { id: 'good', name: 'G' }],
      listModels: async (pid) => { if (pid === 'bad') throw new Error('boom'); return [{ id: 'g1', name: 'G1' }]; },
      resolveModelInfo: async (p, m) => ({ provider: p, id: m, name: m }),
    }),
  };
  const cat = await factory(ctx)();
  assert.equal(cat.groups.length, 1);
  assert.equal(cat.groups[0].id, 'good');
  assert.equal(cat.failures.length, 1);
  assert.equal(cat.failures[0].id, 'bad');
});

test('A3 链里已保存但目录没有的模型，仍要出现（不能被清空）', async () => {
  const factory = makeFactory({ enabled: true, entries: [{ provider: 'ghost', model: 'g-1' }] });
  const ctx = {
    get: () => ({
      listProviders: () => [],
      listModels: async () => [],
      resolveModelInfo: async (p, m) => ({ provider: p, id: m, name: m }),
    }),
  };
  const cat = await factory(ctx)();
  assert.ok(cat.groups.some((g) => g.id === 'ghost'), '链外模型要保留');
  assert.ok(cat.models.some((m) => m.provider === 'ghost' && m.model === 'g-1'));
});

test('A4 llm 不可用：不抛错，返回空目录 + errors', async () => {
  const factory = makeFactory({ enabled: false, entries: [] });
  const cat = await factory({ get: () => null, llm: null })();
  assert.equal(cat.ok, true);
  assert.equal(cat.groups.length, 0);
  assert.ok(cat.errors.length > 0);
});

test('B1 UI 用模型 name 显示（不是 id）', () => {
  assert.ok(CLIENT.includes('mm.name || mm.label || mm.id'), '下拉应用 name');
  assert.ok(CLIENT.includes('function displayName'), '应有 displayName 辅助');
  assert.ok(CLIENT.includes('function effortName'), '应有 effortName 辅助');
});

test('B2 UI 有思考档位列', () => {
  assert.ok(CLIENT.includes('eng-mc-effort'), '应有档位列样式/表头');
  assert.ok(CLIENT.includes('reasoningEffort'), '应能保存 reasoningEffort');
});

test('B3 provider 下拉显示 label + 模型数', () => {
  assert.ok(CLIENT.includes("(g.label || g.id) + (cnt"), 'provider 选项应带计数');
});

test('C1 作用域：只对工程模式生效', () => {
  assert.ok(HOST.includes('function modelChainApplies'), '缺少作用域判定');
  assert.ok(HOST.includes('GUARD_PRESET'), '应复用 engineering 预设常量');
  assert.ok(HOST.includes('isGuardSession'), '应与守卫同口径');
});

test('C2 子代理继承父级模型', () => {
  assert.ok(HOST.includes('function parentChainStateOf'), '缺少父级状态查询');
  // 【2026-10-30 修正】原断言要求"优先沿用父级候选"，但那段逻辑正是
  // 导致"插件劫持用户选型"的根源（无条件把请求改成父级/链首候选），
  // 已在 fallback-only 改造中移除。现在子代理的继承方式改为：
  //   · 作用域上顺着 parentSession 判断是否工程模式（isEng === true）；
  //   · 模型路由本身由宿主 agentOptions 继承父级，插件不再插手。
  assert.ok(HOST.includes('pst.isEng === true'),
    '子代理应顺着父级判断工程模式作用域（isEng 必须是非粘性严格判断）');
  assert.ok(HOST.includes('if (!st.switched || !st.key) return resolved;'),
    '接替必须受 switched 守卫（否则会无条件覆盖用户选型）');
});

console.log('=== catalog v2 tests loaded ===');
