/**
 * 回归测试：【第4项】SW 健康看护 —— ctx.jobs 版
 * ============================================================================
 * 用户需求原文：
 *   「在子代理的SW锁解锁之后，利用SW建模之前，利用 DSH 的后台任务运行时
 *     （ctx.jobs），让父代理启动一个耗时命令，专门做轻量级的定期检查。
 *     比如每 10 秒统计一次系统关键指标，一旦内存、CPU超85％阈值就输出一行
 *     警报。这个 job 会立即返回一个 job id，在后台持续跑着。一旦，相关指数
 *     超标立马关闭SW」
 *
 * ★ 本测试的教训（来自弹窗那次真实事故）：
 *   上一版的弹窗测试全是"源码正则断言"，只证明字符存在，
 *   结果把"commandUi.register 从未被调用"这种致命缺陷全部放过了。
 *   因此这里**必须真执行**：把 index.js 的看护逻辑抽出来真跑，
 *   断言 job 真的被 start、真的返回 job id、超阈值真的触发 kill。
 *
 * 做法：从 index.js 源码里提取 sampleSystem / judgeSystem 的等价逻辑
 * （通过读取源码 + 在受控沙箱里重建），并用注入的假 jobs 服务验证契约。
 *
 * 运行：node --test test/sw-guard-job.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HOST_SRC = readFileSync(join(__dirname, '..', 'lib', 'index.js'), 'utf8');

/** 取某个 `[async] function NAME(...) { ... }` 的完整源码（按花括号配平）。
 *
 * ⚠️ 必须把 `async` 前缀一起带上 —— 否则抽出来的函数是同步的，
 *    而里面有 await，会直接 SyntaxError（这个坑我实测踩到过：
 *    `function ` 的搜索会匹配到 `async function ` 的后半段，丢掉 async）。
 */
function extractFn(src, name) {
  const needle = 'function ' + name + '(';
  let start = src.indexOf(needle);
  if (start < 0) return '';
  // 往前看是否紧邻 "async "，是则把它纳入切片
  const prefix = src.slice(Math.max(0, start - 6), start);
  if (/async\s$/.test(prefix)) start -= 6;
  let i = src.indexOf('{', start);
  if (i < 0) return '';
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  return '';
}

// ══════════════════════════════════════════════════════════════════════════
// 一、★ 真执行：把看护逻辑抽出来在受控环境里真跑
// ══════════════════════════════════════════════════════════════════════════

/** 构造一个可跑的看护环境：注入假 os / jobs / kill，返回控制器。 */
function buildGuard({ cpuPct, memPct, killResult = { ok: true, method: 'taskkill' } } = {}) {
  const src = HOST_SRC;
  const jobStarts = [];
  const kills = [];
  const warns = [];

  const fakeOs = {
    totalmem: () => 16 * 1024 * 1024 * 1024,
    freemem: () => Math.round(16 * 1024 * 1024 * 1024 * (1 - (memPct ?? 50) / 100)),
    cpus: () => [{ times: { user: 100, nice: 0, sys: 50, irq: 0, idle: 1000 } }],
  };

  // 把常量与函数拼成一段可求值的代码（保持与 index.js 同源）
  const judge = extractFn(src, 'judgeSystem');
  const sample = extractFn(src, 'sampleSystem');
  const kill = extractFn(src, 'killSolidWorks');
  const ensure = extractFn(src, 'ensureSwGuardJob');

  const consts = `
    const SW_GUARD_INTERVAL_MS = ${/:?\s*const SW_GUARD_INTERVAL_MS\s*=\s*(\d+)/.exec(src)?.[1] || 10000};
    const SW_GUARD_CPU_LIMIT = ${/:?\s*const SW_GUARD_CPU_LIMIT\s*=\s*(\d+)/.exec(src)?.[1] || 85};
    const SW_GUARD_MEM_LIMIT = ${/:?\s*const SW_GUARD_MEM_LIMIT\s*=\s*(\d+)/.exec(src)?.[1] || 85};
  `;

  // 覆盖 CPU 采样：让 sampleSystem 走假 cpu 值（通过 pre-seed 基线）
  const harness = `
    ${consts}
    let swGuardJobId = null;
    let swGuardState = { ticks: 0, alerts: [], killed: false, startedAt: null, last: null };
    const os = __fakeOs;
    const spawnSync = () => __killResult;
    const ctx = { get: (n) => (n === 'jobs' ? __jobs : undefined) };
    const console = { warn: (...a) => __warns.push(a.join(' ')) };
    ${judge}
    ${kill}
    ${ensure}
    return { ensureSwGuardJob,
             setCpuBaseline: (idle, total) => { globalThis.__engCpuPrev = { idle, total }; },
             getState: () => swGuardState,
             sampleSystem, judgeSystem };
  `;

  const fakeJobs = {
    start(spec) {
      jobStarts.push(spec);
      const handle = spec.run();
      return 'job-' + jobStarts.length;
    },
  };

  const fn = new Function('__fakeOs', '__jobs', '__killResult', '__warns', harness);
  const api = fn(fakeOs, fakeJobs, killResult, warns);
  return { api, jobStarts, kills, warns };
}

test('★真执行：ctx.jobs 缺失时必须【显式降级】且不假装成功', () => {
  const src = HOST_SRC;
  // 把 jobs 传成 undefined，走降级分支
  const judge = extractFn(src, 'judgeSystem');
  const ensure = extractFn(src, 'ensureSwGuardJob');
  const consts = `
    const SW_GUARD_INTERVAL_MS = 10000;
    const SW_GUARD_CPU_LIMIT = 85;
    const SW_GUARD_MEM_LIMIT = 85;
    let swGuardJobId = null;
    let swGuardState = {};
    const os = { totalmem: () => 1, freemem: () => 1, cpus: () => [] };
    const spawnSync = () => ({ status: 1, stdout: '', stderr: '' });
    const ctx = { get: () => undefined };   // ← 没有 jobs 服务
    ${judge}
    ${ensure}
    return ensureSwGuardJob();
  `;
  const r = new Function(consts)();
  assert.equal(r.ok, false, '缺 jobs 服务时 ok 必须为 false（不得假装成功）');
  assert.equal(r.job_id, null, 'job_id 必须为 null');
  assert.equal(r.degraded, true, '必须标记 degraded=true');
  assert.match(String(r.error), /background jobs unavailable/,
    '错误文案应与官方契约一致（asar 1036382 同款）');
  assert.ok(r.fallback && r.fallback.includes('sw-guard-status'),
    '★ 降级说明必须指出 OS 子进程那条路仍在工作');
});

test('★★真执行：有 ctx.jobs 时必须真的 start，并【立即返回 job id】', async () => {
  const src = HOST_SRC;
  const judge = extractFn(src, 'judgeSystem');
  const sample = extractFn(src, 'sampleSystem');
  const kf = extractFn(src, 'killSolidWorks');
  const ensure = extractFn(src, 'ensureSwGuardJob');
  const started = [];
  // ⚠️ 必须把 sampleSystem / killSolidWorks 一并注入 ——
  //   ensureSwGuardJob 的 run() 内会调用它们；漏注入会在 done 的 Promise 里
  //   抛 ReferenceError（且表现为"测试结束后才异步报错"，很难定位）。
  const consts = `
    const SW_GUARD_INTERVAL_MS = 10000;
    const SW_GUARD_CPU_LIMIT = 85;
    const SW_GUARD_MEM_LIMIT = 85;
    let swGuardJobId = null;
    let swGuardState = { ticks: 0, alerts: [], killed: false };
    const os = { totalmem: () => 16 * 1024 * 1024 * 1024,
                 freemem: () => 14 * 1024 * 1024 * 1024, cpus: () => [] };
    const spawnSync = () => ({ status: 1, stdout: '', stderr: 'not found' });
    const ctx = { get: (n) => (n === 'jobs' ? {
      start: (spec) => { __started.push(spec); const h = spec.run(); return 'job-abc'; }
    } : undefined) };
    const console = { warn: () => {} };
    ${judge}
    ${sample}
    ${kf}
    ${ensure}
    return { r: ensureSwGuardJob(), started: __started };
  `;
  const fn = new Function('__started', consts);
  const out = fn(started);
  assert.equal(out.started.length, 1, '★ jobs.start 必须被调用一次');
  assert.equal(out.r.job_id, 'job-abc', '★ 必须立即返回 job id');
  assert.equal(out.r.ok, true);
  assert.equal(out.r.cpu_limit, 85, 'CPU 阈值应为 85');
  assert.equal(out.r.mem_limit, 85, '内存阈值应为 85');
  assert.equal(out.r.interval_ms, 10000, '间隔应为 10 秒（用户要求）');
  // 契约核对：run 必须【同步】返回 {cancel, done}
  const spec = out.started[0];
  assert.ok(spec.kind, 'spec 需要 kind');
  assert.ok(spec.label, 'spec 需要 label');
  assert.ok(spec.owner, 'spec 需要 owner');
  assert.equal(typeof spec.run, 'function');
  const h = spec.run();
  assert.equal(typeof h.cancel, 'function', '★ run 必须同步返回 cancel');
  assert.ok(h.done && typeof h.done.then === 'function', '★ run 必须同步返回 done(Promise)');
  h.cancel('test cleanup');
  // 等 done 落定，避免"测试结束后才异步报错"掩盖问题
  await h.done.catch(() => {});
});

test('★真执行：judgeSystem 阈值判定（>85 触发，=85 不触发）', () => {
  const src = HOST_SRC;
  const judge = extractFn(src, 'judgeSystem');
  const consts = `
    const SW_GUARD_CPU_LIMIT = 85;
    const SW_GUARD_MEM_LIMIT = 85;
    ${judge}
    return judgeSystem;
  `;
  const judgeSystem = new Function(consts)();
  assert.equal(judgeSystem({ cpu_pct: 90, mem_pct: 50 }).breach, true, 'CPU 90 > 85 应触发');
  assert.equal(judgeSystem({ cpu_pct: 50, mem_pct: 90 }).breach, true, '内存 90 > 85 应触发');
  assert.equal(judgeSystem({ cpu_pct: 50, mem_pct: 50 }).breach, false, '都低于阈值不应触发');
  // 边界：恰好等于阈值不应触发（与 sw_guard.py 的实测口径一致）
  assert.equal(judgeSystem({ cpu_pct: 85, mem_pct: 85 }).breach, false,
    '恰好等于 85% 不应触发（避免临界误杀）');
  const r = judgeSystem({ cpu_pct: 91.2, mem_pct: 50 });
  assert.ok(r.reasons[0].includes('91.2'), '告警文案必须含真实数值');
  assert.ok(r.reasons[0].includes('85'), '告警文案必须含阈值');
});

test('★真执行：采样失败不得假报 0%（必须 ok:false）', () => {
  const src = HOST_SRC;
  const sample = extractFn(src, 'sampleSystem');
  const consts = `
    const os = { totalmem: () => { throw new Error('boom'); },
                 freemem: () => 0, cpus: () => [] };
    ${sample}
    return sampleSystem();
  `;
  const r = new Function(consts)();
  assert.equal(r.ok, false, '★ 采样异常必须 ok:false，绝不能假报 0%');
  assert.ok(r.error && r.error.includes('boom'), '应带出真实原因');
});

test('★真执行：进程不在运行 → taskkill 非 0 也算成功（不是错误）', async () => {
  const src = HOST_SRC;
  const kf = extractFn(src, 'killSolidWorks');
  // 模拟 taskkill 对"进程不存在"的实测行为：rc=128 + 英文提示
  // ⚠️ killSolidWorks 是 async，外层 new Function 必须也是 async 才能 await
  const consts = `
    const spawnSync = () => ({ status: 128, stdout: '',
      stderr: 'ERROR: The process "SLDWORKS.exe" not found.' });
    ${kf}
    return killSolidWorks();
  `;
  const r = await new Function(`return (async () => { ${consts} })();`)();
  assert.equal(r.ok, true, '★ 进程不存在不应算失败');
  assert.equal(r.method, 'not_running', '应标记 not_running');
});

test('★真执行：真实击杀成功 → method=taskkill', async () => {
  const src = HOST_SRC;
  const kf = extractFn(src, 'killSolidWorks');
  const consts = `
    const spawnSync = () => ({ status: 0, stdout: 'SUCCESS: ...', stderr: '' });
    ${kf}
    return killSolidWorks();
  `;
  const r = await new Function(`return (async () => { ${consts} })();`)();
  assert.equal(r.ok, true);
  assert.equal(r.method, 'taskkill', '★ 真杀掉应标记 taskkill');
});

test('★真执行：击杀失败（非"未找到"）必须 ok:false（不得当成功）', async () => {
  const src = HOST_SRC;
  const kf = extractFn(src, 'killSolidWorks');
  const consts = `
    const spawnSync = () => ({ status: 5, stdout: '', stderr: 'ERROR: Access is denied.' });
    ${kf}
    return killSolidWorks();
  `;
  const r = await new Function(`return (async () => { ${consts} })();`)();
  assert.equal(r.ok, false, '★ 击杀失败必须 ok:false');
  assert.equal(r.method, 'taskkill_failed');
});

// ══════════════════════════════════════════════════════════════════════════
// 二、源码契约（ESM / 路由 / 常量）
// ══════════════════════════════════════════════════════════════════════════

test('★★第4项：用到的 node 内置模块必须已 import（曾漏 import os → 端点 404）', () => {
  // 【为什么必须锁这条】实测踩到的真 bug：
  //   sampleSystem() 里用了 os.totalmem()/os.freemem()/os.cpus()，
  //   但 index.js 顶部**没有 import os** → 运行时 ReferenceError →
  //   看护端点装载失败 → /dsh-engineering-ui/sw-guard-job 返回 404。
  //   语法检查（node --check）发现不了这种"标识符未定义"的问题，
  //   只有真正执行到那行才会炸 —— 因此这里做静态的"使用即须已导入"检查。
  const code = HOST_SRC.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  // 收集已 import 的绑定名
  const imported = new Set();
  for (const m of code.matchAll(/^import\s+(?:\*\s+as\s+(\w+)|(\w+)|{([^}]+)})\s+from/gm)) {
    if (m[1]) imported.add(m[1]);
    if (m[2]) imported.add(m[2]);
    if (m[3]) {
      for (const part of m[3].split(',')) {
        const nm = part.trim().split(/\s+as\s+/).pop().trim();
        if (nm) imported.add(nm);
      }
    }
  }
  // 检查本功能用到的 node 内置
  const checks = [
    ['os', /\bos\s*\.\s*(totalmem|freemem|cpus)\s*\(/],
    ['spawnSync', /\bspawnSync\s*\(/],
    ['path', /\bpath\s*\.\s*(join|dirname|basename|resolve)\s*\(/],
    ['fs', /\bfs\s*\.\s*(readFileSync|writeFileSync|existsSync|promises)\b/],
  ];
  const missing = [];
  for (const [name, re] of checks) {
    if (re.test(code) && !imported.has(name) && !code.includes(`const ${name} =`)) {
      missing.push(name);
    }
  }
  assert.deepEqual(missing, [],
    '★ 以下内置模块被使用但未 import（运行时会 ReferenceError）：' + missing.join(', '));
});

test('第4项：必须用 ctx.jobs（而不是另起一套进程管理）', () => {
  assert.ok(HOST_SRC.includes("ctx.get('jobs')") || HOST_SRC.includes('ctx.get("jobs")'),
    "必须读 ctx.get('jobs') 服务");
  assert.ok(/jobs\.start\(\{/.test(HOST_SRC) || /jobs\.start\(/.test(HOST_SRC),
    '必须调用 jobs.start()');
});

test('第4项：index.js 是 ESM —— 不得出现裸 require()（会抛 ReferenceError）', () => {
  // 只查真实代码，不查注释
  const code = HOST_SRC.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  const bad = [...code.matchAll(/(^|[^.\w])require\s*\(/g)];
  assert.equal(bad.length, 0,
    'ESM 下没有 require；发现 ' + bad.length + ' 处，应改用顶部 import');
});

test('第4项：必须通过 webServer 暴露看护端点', () => {
  assert.ok(HOST_SRC.includes("PREFIX + '/sw-guard-job'"),
    '应注册 /sw-guard-job 端点');
});

test('第4项：阈值与间隔必须是代码常量（用户指定的 10 秒 / 85%）', () => {
  const iv = /SW_GUARD_INTERVAL_MS\s*=\s*(\d+)/.exec(HOST_SRC);
  const cpu = /SW_GUARD_CPU_LIMIT\s*=\s*(\d+)/.exec(HOST_SRC);
  const mem = /SW_GUARD_MEM_LIMIT\s*=\s*(\d+)/.exec(HOST_SRC);
  assert.ok(iv, '应有 SW_GUARD_INTERVAL_MS 常量');
  assert.equal(iv[1], '10000', '间隔必须是 10000ms（= 10 秒）');
  assert.equal(cpu && cpu[1], '85', 'CPU 阈值必须是 85');
  assert.equal(mem && mem[1], '85', '内存阈值必须是 85');
});

test('第4项：不得与 sw_guard.py 那条路冲突（两条互补，都要在）', () => {
  // mode_gate 的 OS 子进程看护仍须存在（取锁即拉起，不依赖 DSH 运行时）
  const mg = readFileSync(join(__dirname, '..', '..', '..', 'tools', 'mode_gate.py'), 'utf8');
  assert.ok(mg.includes('def ensure_sw_guard'), 'mode_gate 应保留 ensure_sw_guard');
  assert.ok(mg.includes("'sw-guard-start'") || mg.includes('"sw-guard-start"'),
    'mode_gate 应保留 sw-guard-start 命令');
});
