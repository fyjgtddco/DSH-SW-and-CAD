/**
 * prompt-variables.test.mjs — 守住工程模式人设的 prompt 变量契约
 *
 * 真实故障（2026-10-17 实测）：
 *   切到工程模式后一发送消息就报
 *     unknown prompt variable "{{cwd}}" in section "deployment:persona-prefix";
 *     registered variables: provider, model
 *   → 整轮请求失败，工程模式完全不可用。
 *
 * 根因：
 *   0.2.0 的 dsh-agent-loop 注册过 provider / model / cwd 三个 prompt 变量；
 *   0.2.1-rc.2 只保留 provider 与 model
 *   （dsh-agent-loop/lib/index.js:1591-1592），cwd 被移除。
 *   而 dsh-system-prompt 的 interpolate() 对未注册变量【直接抛错】
 *   （dsh-system-prompt/lib/index.js:172-175），不是降级为空串。
 *
 * 维修：人设仍保留 {{cwd}}（工作目录是工程模式的必要信息），
 *   由本插件在全局 ctx 注册 cwd 变量把 0.2.0 的行为补回来。
 *
 * 本测试守四件事：
 *   ① 插件必须真的注册 cwd 变量；
 *   ② 取值必须走 workingDirectory.get 并回落 session.header.cwd；
 *   ③ 取不到时必须返回空串（undefined 会让整轮请求抛 has no value）；
 *   ④ 人设文本引用的每个变量都必须能被解析（provider/model 官方提供，cwd 本插件提供）。
 *
 * 运行：node --test test/prompt-variables.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HOST_SRC = readFileSync(join(__dirname, '..', 'lib', 'index.js'), 'utf8');

/** 官方（dsh-agent-loop）在 0.2.1-rc.2 注册的变量。 */
const OFFICIAL_VARIABLES = new Set(['provider', 'model']);
/** 本插件补注册的变量（0.2.0 曾有、0.2.1 移除）。 */
const PLUGIN_VARIABLES = new Set(['cwd']);

/** 定位 preset-engineering.patch.yml，兼容仓库与安装副本两种布局。 */
function findPreset() {
  const candidates = [
    resolve(__dirname, '..', 'preset-engineering.patch.yml'),
    process.env.DSH_HOME
      ? resolve(process.env.DSH_HOME, '.agent-presets', 'engineering', 'plugins', 'dsh-engineering-ui', 'preset-engineering.patch.yml')
      : null,
  ].filter(Boolean);
  for (const c of candidates) if (existsSync(c)) return c;
  return null;
}

/** 抽出 {{name}} 引用。 */
function promptRefs(text) {
  return [...new Set([...text.matchAll(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g)].map((m) => m[1]))];
}

/** 取出 cwd 变量注册那段源码（从 variable('cwd' 起 1600 字符）。 */
function cwdRegistrationSource() {
  const start = HOST_SRC.indexOf("variable('cwd'");
  assert.ok(start > 0, 'lib/index.js 里找不到 systemPrompt.variable(\'cwd\', ...) 注册');
  return HOST_SRC.slice(start, start + 1600);
}

test('① 插件必须为 cwd 注册 prompt 变量（否则人设里的 {{cwd}} 会让整轮请求失败）', () => {
  assert.match(HOST_SRC, /systemPrompt\.variable\(\s*['"]cwd['"]/,
    '缺少 cwd 变量注册');
});

test('② 注册必须挂在 ctx.inject([\'systemPrompt\'], ...) 上（可选服务不硬依赖）', () => {
  const idx = HOST_SRC.indexOf("variable('cwd'");
  assert.ok(idx > 0, '找不到 cwd 注册点');
  // 注册点本身在回调里，真正调用 ctx.inject 的语句在它之后若干行内。
  const after = HOST_SRC.slice(idx, idx + 2000);
  const m = /ctx\.inject\(\s*\[([^\]]*)\]\s*,/.exec(after);
  assert.ok(m !== null, 'cwd 注册之后应出现 ctx.inject([...], callback)');
  assert.match(m[1], /['"]systemPrompt['"]/,
    'ctx.inject 的服务列表应包含 systemPrompt，实际为 [' + m[1].trim() + ']');
  // 而且必须做能力判断，缺失该服务时不得在 apply 阶段抛错
  assert.match(after, /typeof\s+ctx\.inject\s*===\s*['"]function['"]/,
    '调用前应判断 ctx.inject 是否存在');
});

test('③ 取值必须优先 workingDirectory.get（同步、无 I/O）', () => {
  const seg = cwdRegistrationSource();
  assert.match(seg, /workingDirectory/, '应读取 ctx.workingDirectory');
  assert.match(seg, /\.get\(/, '应调用 workingDirectory.get(...)');
});

test('④ 必须有 session.header.cwd 兜底', () => {
  assert.match(cwdRegistrationSource(), /header/, '应回落 session.header.cwd');
});

test('⑤ 取不到目录时必须返回空字符串，绝不能返回 undefined', () => {
  // 官方语义：value === undefined → 抛 'has no value for this assembly'
  // （dsh-system-prompt/lib/index.js:176-177）；空串是合法值。
  const seg = cwdRegistrationSource();
  assert.match(seg, /return\s+''/, "兜底分支必须 return ''");
  assert.ok(!/return\s+undefined/.test(seg),
    'cwd provider 不得 return undefined（会让整轮请求抛 has no value）');
});

test('⑥ provider 内任何异常都只降级为空串，不得抛出', () => {
  const seg = cwdRegistrationSource();
  const catches = (seg.match(/catch/g) || []).length;
  assert.ok(catches >= 2, 'provider 内应有至少两层 catch（服务读取 + 整体）');
});

test('⑦ 人设文本引用的每个变量都必须有人提供（官方 或 本插件）', (t) => {
  const presetPath = findPreset();
  if (presetPath === null) { t.skip('未找到 preset-engineering.patch.yml'); return; }
  const refs = promptRefs(readFileSync(presetPath, 'utf8'));
  const unresolved = refs.filter((r) => !OFFICIAL_VARIABLES.has(r) && !PLUGIN_VARIABLES.has(r));
  assert.deepEqual(unresolved, [],
    '人设引用了没人注册的变量 ' + JSON.stringify(unresolved) +
    '；官方提供 ' + JSON.stringify([...OFFICIAL_VARIABLES]) +
    '，本插件提供 ' + JSON.stringify([...PLUGIN_VARIABLES]));
});

test('⑧ 人设必须保留 {{cwd}}（工作目录是工程模式的必要信息，不得为规避报错而删除）', (t) => {
  const presetPath = findPreset();
  if (presetPath === null) { t.skip('未找到 preset-engineering.patch.yml'); return; }
  const refs = promptRefs(readFileSync(presetPath, 'utf8'));
  assert.ok(refs.includes('cwd'), '人设应显式告知工作目录（{{cwd}}）');
  assert.ok(refs.includes('model'), '人设应保留 {{model}}');
});

test('⑨ agent.cordis.yml（历史对照）与 preset 的变量集合必须一致', (t) => {
  const presetPath = findPreset();
  if (presetPath === null) { t.skip('未找到 preset'); return; }
  const cordis = resolve(__dirname, '..', '..', '..', 'agent.cordis.yml');
  if (!existsSync(cordis)) { t.skip('agent.cordis.yml 不在安装副本里，跳过'); return; }
  const a = promptRefs(readFileSync(presetPath, 'utf8')).sort();
  const b = promptRefs(readFileSync(cordis, 'utf8')).sort();
  assert.deepEqual(a, b, '两份文件的变量集合应一致，避免后人照抄到废弃变量');
});
