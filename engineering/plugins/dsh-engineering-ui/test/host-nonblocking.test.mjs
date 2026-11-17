/**
 * host-nonblocking.test.mjs — 【宿主事件循环不得被同步子进程冻住】
 *
 * ── 事故（2026-11-17，用户报障）───────────────────────────────────────────
 *   「DSH 会话要加载历史很久，工程模式的会话一直显示会话历史加载中，
 *     从工程模式会话换到普通会话也要卡 10 秒」
 *
 * 现场实测（真实 DSH，端口 53755）：
 *   GET /parts-board                        → 30,287 ms
 *   GET /compaction-history（本应 268ms）    → 30,297 ms   ← 被上一条堵住
 *   两条【并发】请求同时各 30 秒 → 整个宿主被冻住，不只是某一条慢。
 *
 * 根因链条：
 *   ① pyEnv() 把 DSH_WEB_URL 注入 Python（2026-11-16 的修复，让 Python 能找到宿主端口）
 *   ② mode2_flow.py parts-board 要【回连宿主】的 /sw-monitor-op 去问监控器
 *   ③ 宿主用 spawnSync 起 Python → 事件循环冻结 → 无法 accept 那个回连
 *   ④ Python 侧 CALL_TIMEOUT_S = 30，等满超时报 degraded
 *   ⑤ spawnSync 返回，宿主解冻
 *   实测同一命令：宿主空闲 232ms / 宿主忙 29,349ms —— 差 126 倍。
 *
 * 这条测试把"路由里不得用同步子进程"钉死成静态断言，防止以后再被加回来。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', 'lib');
const HOST_SRC = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8');

/**
 * 取出某个路由 handler 的源码片段。
 *
 * 取法：从 "'<path>'" 出现处【往前】找到它所在的 ctx.webServer.register(...)，
 * 再向后截到下一个 'ctx.webServer.register(' 出现处（即下一个路由开始）。
 * 早先写成"从 path 往后找 register"，方向反了 —— 拿到的是【上一个】路由的尾巴，
 * 于是既漏判又误判（自测时 ①③ 两条就是被这个取法坑掉的）。
 */
function routeHandler(routePath) {
  // ⚠️ 路由的 path 在源码里写成 PREFIX + '/parts-board'，
  //   不是裸字符串 —— 必须按引号内的那一段去找（早先直接用裸路径找，
  //   找不到就退化成 i>0 断言失败，测试自己先挂了）。
  const at = HOST_SRC.indexOf("'" + routePath + "'");
  assert.ok(at > 0, '找不到路由 ' + routePath);
  const start = HOST_SRC.lastIndexOf('ctx.webServer.register', at);
  const from = start >= 0 ? start : at;
  // ⚠️ 不能截到"下一个 register" —— 那会把【下一个路由】一起包进来，
  //   于是本路由的断言会被邻居的代码误伤（实测：parts-board 被 sw-guard-job
  //   里的 taskkill/spawnSync 拖累，报"还在用同步子进程"）。
  //   正确做法：从注册处起，按括号配平找到 register( ... ) 的收尾。
  const open = HOST_SRC.indexOf('(', from);
  let depth = 0;
  let end = HOST_SRC.length;
  for (let i = open; i < HOST_SRC.length; i++) {
    const ch = HOST_SRC[i];
    if (ch === '(') depth++;
    else if (ch === ')') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  const seg = HOST_SRC.slice(from, end);
  // 去掉整行注释：注释里写明"这里原先是 spawnSync，为什么是坑"是【好事】，
  // 不该被判成"还在用同步子进程"。只扫可执行代码。
  return seg.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
}

test('① 路由处理器不得使用同步子进程（会冻住整个宿主）', () => {
  const ROUTES = [
    '/parts-board', '/env-check', '/env-prompt', '/settings',
    '/conn-probe', '/conn-launch', '/compaction-history',
  ];
  for (const route of ROUTES) {
    const seg = routeHandler(route);
    assert.ok(!/spawnSync\s*\(/.test(seg),
      route + ' 路由里出现了 spawnSync —— 同步子进程会阻塞事件循环，'
      + '若被调命令要回连宿主就会自死锁（2026-11-17 事故）。请改用 execFileAsync。');
  }
});

test('② 提供异步执行助手 execFileAsync', () => {
  assert.ok(/function execFileAsync\(/.test(HOST_SRC), '缺少 execFileAsync 助手');
  const i = HOST_SRC.indexOf('function execFileAsync');
  const seg = HOST_SRC.slice(i, i + 900);
  assert.ok(/execFileCb\(/.test(seg), '内部必须真的调用异步 execFile');
  assert.ok(/settled/.test(seg), '必须防重复 resolve');
  assert.ok(/resolve\(/.test(seg), '必须以 Promise 形式返回结果');
});

test('③ parts-board 走异步通道（本次事故的具体落点）', () => {
  const seg = routeHandler('/parts-board');
  assert.ok(/await execFileAsync\(py, \[script, 'parts-board'\]/.test(seg),
    'parts-board 必须 await execFileAsync 调用 mode2_flow.py');
  assert.ok(!/spawnSync/.test(seg), 'parts-board 不得再出现 spawnSync');
});

test('④ 相关函数已声明为 async（否则 await 无效）', () => {
  for (const fn of ['runConnProbe', 'runEnvCheck', 'runSettings', 'probePythonExe']) {
    const re = new RegExp('async function ' + fn + '\\s*\\(');
    assert.ok(re.test(HOST_SRC), fn + ' 必须声明为 async');
  }
});

test('⑤ resolvePython 有预热通道：请求路径不探测 Python', () => {
  assert.ok(/function warmPythonCache\(/.test(HOST_SRC), '缺少预热函数');
  assert.ok(/pyWarm/.test(HOST_SRC), '必须有预热缓存变量');
  const i = HOST_SRC.indexOf('function resolvePython');
  const seg = HOST_SRC.slice(i, i + 1400);
  assert.ok(seg.indexOf('PY_CACHE_MS') < seg.indexOf('probePythonExeSync'),
    '必须先检查缓存/预热，再退回同步探测');
  const applyAt = HOST_SRC.indexOf('export function apply');
  assert.ok(/warmPythonCache\(\)/.test(HOST_SRC.slice(applyAt)),
    'apply() 必须在启动期预热 Python 选择');
});

test('⑥ 同步探针只做浅探测（不 import pywin32）', () => {
  const i = HOST_SRC.indexOf('function probePythonExeSync');
  assert.ok(i > 0, '缺少 probePythonExeSync');
  const seg = HOST_SRC.slice(i, i + 900);
  assert.ok(!/win32com\.client/.test(seg),
    '同步版不得验证 pywin32 —— 那是深探测，必须留在异步版');
});
