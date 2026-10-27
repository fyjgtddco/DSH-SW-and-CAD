/**
 * prompt-variables.test.mjs — 守住工程模式人设的 prompt 变量契约
 *
 * ── 真实故障史（两次复发，2026-10-17 / 2026-10-20）─────────────────────
 *   切到工程模式后一发送消息就报：
 *     unknown prompt variable "{{cwd}}(已废弃)" in section "deployment:persona-prefix";
 *     registered variables: provider, model
 *   → 整轮请求失败，工程模式完全不可用。
 *
 * 根因：
 *   0.2.0 的 dsh-agent-loop 注册过 provider / model / cwd 三个 prompt 变量；
 *   0.2.1-rc.2 只保留 provider 与 model（dsh-agent-loop/lib/index.js:1591-1592），
 *   cwd 被移除；而 dsh-system-prompt 的 interpolate() 对未注册变量【直接抛错】
 *   （dsh-system-prompt/lib/index.js:172-175），不是降级为空串。
 *
 * ── 两次修法的对比（这是本测试的核心教训）──────────────────────────────
 *   第一次：人设保留 {{cwd}}(已废弃)，由本插件补注册 cwd 变量。
 *     → 失败。补注册依赖 systemPrompt.variable() 的调用时机，
 *       全局注册与 agent 作用域装配之间存在竞态，实测仍概率性复发。
 *   第二次（现行）：人设里【删除】{{cwd}}(已废弃)，插件也【不再注册】。
 *     → 工作目录本就由 DSH 的 working-directory 运行时上下文自动注入
 *       （"Current working directory: ..."），人设里再写一遍是冗余的。
 *     → 少一个自定义变量 = 少一个能让整轮请求失败的故障点。
 *
 * 本测试守三件事：
 *   ① 人设文本里【绝不能】再出现 {{cwd}}(已废弃)（否则 0.2.1 下必然抛错）；
 *   ② 插件里也【不应】再注册 cwd 变量（留死代码会误导后人加回 {{cwd}}(已废弃)）；
 *   ③ 人设引用的每个变量都必须由官方提供（provider / model）。
 *
 * 运行：node --test test/prompt-variables.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_DIR = join(__dirname, '..');
const HOST_SRC = readFileSync(join(PLUGIN_DIR, 'lib', 'index.js'), 'utf8');

/** 官方（dsh-agent-loop）在 0.2.1-rc.2 注册的变量。 */
const OFFICIAL_VARIABLES = new Set(['provider', 'model']);

/** 定位 preset-engineering.patch.yml，兼容仓库与安装副本两种布局。 */
function findPreset() {
  const candidates = [
    resolve(__dirname, '..', 'preset-engineering.patch.yml'),
    process.env.DSH_HOME
      ? resolve(process.env.DSH_HOME, '.agent-presets', 'engineering', 'plugins',
                'dsh-engineering-ui', 'preset-engineering.patch.yml')
      : null,
  ].filter(Boolean);
  for (const c of candidates) if (existsSync(c)) return c;
  return null;
}

/** 抽出 {{name}} 引用。 */
function promptRefs(text) {
  return [...new Set([...text.matchAll(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g)].map((m) => m[1]))];
}

/** 只看代码行，剔除注释 —— 注释里提到 {{cwd}}(已废弃) 属正常说明，不该判失败。 */
function codeOnly(src) {
  return src.split(/\r?\n/)
    .filter((ln) => !/^\s*(\/\/|\*|\/\*)/.test(ln))
    .join('\n');
}

test('① 人设里绝不能出现 {{cwd}}(已废弃)（0.2.1 未注册该变量，必然抛错）', () => {
  const presetPath = findPreset();
  assert.ok(presetPath, '找不到 preset-engineering.patch.yml');
  const preset = readFileSync(presetPath, 'utf8');
  const refs = promptRefs(preset);
  assert.ok(!refs.includes('cwd'),
    '人设里仍有 {{cwd}}(已废弃) —— 0.2.1 未注册该变量，会导致 "unknown prompt variable" 整轮失败。' +
    '工作目录由运行时上下文自动注入，不需要写 {{cwd}}(已废弃)。');
});

test('② 插件不应再注册 cwd 变量（留死代码会误导后人加回 {{cwd}}(已废弃)）', () => {
  const code = codeOnly(HOST_SRC);
  assert.ok(!/variable\(\s*['"]cwd['"]/.test(code),
    'lib/index.js 里仍有 systemPrompt.variable(\'cwd\', ...) 注册。' +
    '既然人设已不引用 cwd，这段注册属于死代码，应删除以免后人误以为可以加回 {{cwd}}(已废弃)。');
});

test('③ 人设引用的每个变量都必须由官方提供', () => {
  const presetPath = findPreset();
  assert.ok(presetPath, '找不到 preset-engineering.patch.yml');
  const refs = promptRefs(readFileSync(presetPath, 'utf8'));
  const unknown = refs.filter((r) => !OFFICIAL_VARIABLES.has(r));
  assert.deepEqual(unknown, [],
    '人设引用了官方未注册的变量: ' + unknown.join(', ') +
    '（官方仅注册 provider / model）');
});

test('④ 人设仍应告知模型它在做机械设计（不能把整段人设删空）', () => {
  const presetPath = findPreset();
  const preset = readFileSync(presetPath, 'utf8');
  assert.match(preset, /机械工程设计 Agent/, '人设首行应保留角色声明');
  assert.match(preset, /\{\{\s*model\s*\}\}/, '人设应保留 {{model}}');
});

test('⑤ 插件源码里不得残留 cwd 变量注册的实现细节', () => {
  const code = codeOnly(HOST_SRC);
  for (const bad of ['_cwdProvider', '_registerCwdOn', 'cwd variable setup',
                     '_cwdVariableDisposer']) {
    assert.ok(!code.includes(bad),
      '插件里仍残留 cwd 注册实现: ' + bad + '（应整体删除）');
  }
});
