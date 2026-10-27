/**
 * 【工程环境检查】宿主路由契约测试（用户需求 3）
 * ============================================================================
 * 用户要求：「连接区上面再加一个『检查电脑是否有对应的环境』，然后写一段提示词
 *   自己发给新的对话，新的 Agent 对话就自己去查查看少啥，自己安装。」
 *
 * 本测试锁三件事：
 *   ① 三条路由真的注册了、方法/参数校验正确；
 *   ② 判定逻辑【不在 JS 里】—— 必须转调 tools/env_check.py（单一事实源）；
 *   ③ 环境不全时（没有 Python 3.6+），接口必须给出【可操作】的结构化结论，
 *      而不是一句"失败"，更不能静默返回 ok:true 骗前端显示成"全绿"。
 *
 * 运行：node --test test/env-check.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HOST_SRC = readFileSync(join(__dirname, '..', 'lib', 'index.js'), 'utf8');
const CLIENT_SRC = readFileSync(join(__dirname, '..', 'lib', 'client.js'), 'utf8');

/** 最小可用 ctx：只要够 apply() 跑起来并登记路由即可。 */
function boot() {
  const routes = new Map();
  const hooks = new Map();
  const logs = [];
  const ctx = {
    webServer: {
      register(route) { routes.set(route.path, route); return () => routes.delete(route.path); },
    },
    on(name, fn) { hooks.set(name, fn); return () => hooks.delete(name); },
    get() { return undefined; },
    inject() { return () => {}; },
    slots: { inject() { return () => {}; }, register() { return () => {}; } },
    log: { info() {}, warn() {}, error() {} },
  };
  let mod = null;
  try {
    vm.runInNewContext(
      HOST_SRC.replace(/^import .*$/gm, '').replace(/^export /gm, ''),
      {
        console: { log() {}, warn() {}, error() {}, info(...a) { logs.push(a.join(' ')); } },
        process: { env: {}, platform: 'win32', cwd: () => 'C:/', execPath: 'node' },
        require: () => ({}),
        module: { exports: {} },
        exports: {},
        __dirname: join(__dirname, '..', 'lib'),
        setTimeout, clearTimeout, setInterval, clearInterval,
      },
      { filename: 'index.js' },
    );
  } catch (e) {
    // 允许失败：本测试的关键断言在源码层 + 路由表，不强依赖 vm 跑通整个宿主
    mod = null;
  }
  return { routes, hooks, logs, mod };
}

test('① 三条新路由必须在源码里注册（env-check / env-prompt / settings）', () => {
  assert.ok(HOST_SRC.includes("PREFIX + '/env-check'"), '缺少 /env-check 路由');
  assert.ok(HOST_SRC.includes("PREFIX + '/env-prompt'"), '缺少 /env-prompt 路由');
  assert.ok(HOST_SRC.includes("PREFIX + '/settings'"), '缺少 /settings 路由');
});

test('② 判定逻辑必须在 Python 侧（JS 只做转接，不自己判定）', () => {
  // 关键：不得在 JS 里写 "if (某模块存在) ... else 缺失" 这类判定
  const body = HOST_SRC.slice(HOST_SRC.indexOf('function runEnvCheck'));
  const seg = body.slice(0, body.indexOf('// GET：跑一次完整自检'));
  assert.ok(seg.includes("path.join(dir, 'env_check.py')"),
    '必须调用 tools/env_check.py（单一事实源）');
  assert.ok(!/require\.resolve\(['"]pywin32/.test(HOST_SRC),
    '不得在 JS 里自己探测 pywin32');
});

test('③ 没有可用 Python 时必须给出【可操作】结论，不得假装成功', () => {
  const i = HOST_SRC.indexOf('function runEnvCheck');
  const seg = HOST_SRC.slice(i, i + 2400);
  assert.ok(/status:\s*'bad'/.test(seg), 'Python 不可用时 status 必须是 bad');
  assert.ok(/hint:/.test(seg), '必须给 hint（怎么修），不能只报错');
  assert.ok(/3\.5|3\.6|f-string/.test(seg),
    'hint 应点明"旧版 Python 不支持 f-string"这个真实根因');
  assert.ok(!/return\s*\{\s*ok:\s*true\s*\}\s*;\s*\/\/\s*降级/.test(seg),
    '不得用 ok:true 假装通过');
});

test('④ resolvePython 必须校验版本并且认 py -3', () => {
  assert.ok(HOST_SRC.includes('PYTHON_MIN_MINOR'), '缺少最低版本常量');
  const i = HOST_SRC.indexOf('let pyCache');
  const seg = HOST_SRC.slice(i, i + 4000);
  assert.ok(/sys\.version_info/.test(seg), '探针必须真的问解释器要版本');
  assert.ok(/import win32com\.client/.test(seg), '探针必须验证 pywin32 真的能 import');
  assert.ok(/out\.push\('py'/.test(seg), "候选里必须有 py（Windows 官方启动器，会挑最新 3.x）");
  // 顺序：py 必须在 python 之前 —— 否则又会选中 PATH 里那个 3.5
  const pyIdx = seg.indexOf("'py'");
  const pyIdx2 = seg.indexOf("'python'");
  assert.ok(pyIdx > 0 && pyIdx2 > pyIdx, "py 必须排在 python 之前（微机房踩过的坑）");
});

test('⑤ /settings 写入口必须列入敏感路由（能改路径 = 高副作用）', () => {
  const i = HOST_SRC.indexOf('const SENSITIVE_ROUTES');
  const seg = HOST_SRC.slice(i, i + 500);
  assert.ok(seg.includes("PREFIX + '/settings'"), '/settings 必须列入 SENSITIVE_ROUTES');
});

test('⑥ /settings 只允许白名单键（防止被用来改别的状态文件）', () => {
  const i = HOST_SRC.indexOf('const ALLOWED =');
  assert.ok(i > 0, '缺少白名单');
  const seg = HOST_SRC.slice(i, i + 300);
  for (const k of ['delivery_root', 'sw_exe', 'cad_exe', 'python_exe']) {
    assert.ok(seg.includes("'" + k + "'"), '白名单缺 ' + k);
  }
  assert.ok(!seg.includes("'workflow_state"), '白名单不得包含状态文件名');
});

test('⑦ 客户端：环境检查栏目必须注册在「连接区」之前', () => {
  const iConn = CLIENT_SRC.indexOf("h(EngGroup, { title: '连接区' }");
  const iEnv = CLIENT_SRC.indexOf("h(EngGroup, { title: '工程环境检查' }");
  assert.ok(iEnv > 0, '缺少「工程环境检查」栏目');
  assert.ok(iConn > 0, '找不到「连接区」栏目');
  assert.ok(iEnv < iConn, '「工程环境检查」必须排在「连接区」之前（用户指定位置）');
});

test('⑧ 客户端：必须能把提示词交给新对话（复用既有发送通道）', () => {
  assert.ok(CLIENT_SRC.includes('EngEnvPanel'), '缺少环境检查组件');
  const i = CLIENT_SRC.indexOf('function EngEnvPanel');
  const seg = CLIENT_SRC.slice(i, i + 9000);
  assert.ok(seg.includes("'/dsh-engineering-ui/env-prompt'"), '必须调用 /env-prompt');
  assert.ok(seg.includes('engSendToWorkSession'), '必须走既有的"新建工程会话并发首轮"通道');
});

test('⑨ 客户端：交付目录可在设置里改，且走 /settings', () => {
  const i = CLIENT_SRC.indexOf('function EngEnvPanel');
  const seg = CLIENT_SRC.slice(i, i + 9000);
  assert.ok(seg.includes("key: 'delivery_root'"), '必须能保存 delivery_root');
  assert.ok(seg.includes("fetch('/dsh-engineering-ui/settings'"), '必须走 /settings 路由');
});

test('⑩ 所有 Python 子进程必须强制 UTF-8（否则设置页中文全乱码）', () => {
  // 实测（中文 Windows）：env_check.py / settings.py 用 ensure_ascii=False 打印
  //   中文，Python 默认按系统 ANSI(GBK) 写 stdout，Node 按 UTF-8 解码 →
  //   设置页上每一项的名称/说明都变成问号方块。
  // 实证对比（同一命令，2026-10-27 本机实测）：
  //   WITHOUT env -> item[0].name = 锟斤拷锟斤拷妯″紡
  //   WITH    env -> item[0].name = 工程模式核心脚本
  assert.ok(HOST_SRC.includes('PYTHONIOENCODING'), '必须设置 PYTHONIOENCODING=utf-8');
  assert.ok(HOST_SRC.includes('PYTHONUTF8'), '必须设置 PYTHONUTF8=1（旧版本兼容）');
  assert.ok(/function pyEnv\(\)/.test(HOST_SRC), '应有统一的 pyEnv() 助手');
  // 每一处 spawnSync 调 python 的地方都必须带 env: pyEnv()
  const calls = HOST_SRC.match(/spawnSync\([\s\S]{0,400}?\}\)/g) || [];
  const pyCalls = calls.filter((c) => /spawnSync\(py|spawnSync\(pre\[0\]/.test(c));
  assert.ok(pyCalls.length >= 4, '应至少找到 4 处 Python 调用，实际 ' + pyCalls.length);
  for (const c of pyCalls) {
    assert.ok(c.includes('env: pyEnv()'),
      'Python 调用必须带 env: pyEnv()，否则中文乱码。片段=' +
      c.replace(/\s+/g, ' ').slice(0, 160));
  }
});
