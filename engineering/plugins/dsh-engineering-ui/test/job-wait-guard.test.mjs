/**
 * job-wait-guard.test.mjs — 【卡死修复】job_output 长等待守卫的回归测试
 * ==========================================================================
 * 对应事故：docs/DSH卡死事故分析_job_output阻塞.md
 *   job_output { wait:true, timeout_ms:270000 } 静默阻塞 → 用户以为死机强退
 *   → 宿主停机时 cordis 抛 "cannot get required service \"jobs\" in inactive context"
 *   → 整轮 turn/end{interrupted}
 *
 * 本测试覆盖四条契约（每条都对应修复前的一个真实故障）：
 *  T1 长等待被切成窗口：请求 270000 时，实际下发给工具的是窗口值（≤45000）
 *  T2 窗口到点返回【正常成功结果】（isError=false），文案明确"仍在运行"
 *  T3 上下文失活异常 → 降级为可读工具错误（isError=true，JOB_CONTEXT_LOST），绝不 throw
 *  T4 非 job_output / 短等待 / wait:false → 原样透传，守卫零侵入
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  installJobWaitGuard,
  JOB_WAIT_GUARD_DEFAULTS,
} from '../lib/job-wait-guard.js';

/** 造一个最小 ctx：只实现本守卫用到的 on()。 */
function fakeCtx() {
  const handlers = [];
  return {
    handlers,
    on(event, fn) {
      assert.equal(event, 'tools/execute');
      handlers.push(fn);
      return () => {
        const i = handlers.indexOf(fn);
        if (i >= 0) handlers.splice(i, 1);
      };
    },
    /** 用当前注册的监听器跑一次工具执行；返回它给出的结果。 */
    async run(exec, next) {
      const fn = handlers[0];
      assert.ok(fn, 'guard listener must be registered');
      return fn(exec, next);
    },
  };
}

/** 一个"永远在跑"的假 job_output：记录它实际收到的 timeout_ms，然后立即返回 running。 */
function runningJobNext(seen) {
  return async () => ({
    content: [{ type: 'text', text: 'output so far\n[status: running]' }],
    isError: false,
  });
}

test('T1: 长等待被切成窗口 —— 不改写（冻结的）参数，到点即返回', async () => {
  const ctx = fakeCtx();
  const prevMax = process.env.DSH_ENG_JOB_WAIT_MAX_MS;
  process.env.DSH_ENG_JOB_WAIT_MAX_MS = '5000';   // 测试用短窗口，别真等 45s
  let dispose;
  try { dispose = installJobWaitGuard(ctx); } finally {
    if (prevMax === undefined) delete process.env.DSH_ENG_JOB_WAIT_MAX_MS;
    else process.env.DSH_ENG_JOB_WAIT_MAX_MS = prevMax;
  }
  assert.equal(ctx.handlers.length, 1, '应注册一个 tools/execute 监听器');

  // 模拟宿主契约：exec.arguments 是深冻结的（dsh-tools createExecution 行为）
  const args = Object.freeze({ job_id: 'pwsh-abc', wait: true, timeout_ms: 270000 });
  const exec = Object.freeze({ name: 'job_output', arguments: args, signal: undefined });

  // 一个"永远不返回"的工具体，模拟任务长时间运行
  const neverResolves = () => new Promise(() => {});

  const t0 = Date.now();
  const result = await Promise.race([
    ctx.run(exec, neverResolves),
    new Promise((resolve) => setTimeout(() => resolve({ __timeout: true }), 8000)),
  ]);
  const elapsed = Date.now() - t0;

  assert.ok(!(result && result.__timeout), '守卫必须在窗口内自行返回，不能一直挂着');
  assert.ok(elapsed < 7000, '必须在窗口（5s）附近返回，实测 ' + elapsed + 'ms');

  // 关键：窗口到点返回的是【正常成功结果】，绝不能再 throw（否则又是一种打断）
  assert.equal(result.isError, false, '窗口到点必须是正常成功结果，不能是错误');
  assert.match(String(result.content[0].text), /仍在运行/, '文案要说明任务仍在运行');
  assert.match(String(result.content[0].text), /窗口已到/, '文案要说明这是窗口到点');

  // 参数必须没被动过（证明我们不再依赖"改写冻结对象"这条路）
  assert.equal(args.timeout_ms, 270000, '参数不得被改写');

  dispose();
  assert.equal(ctx.handlers.length, 0, 'disposer 必须注销监听器');
});

test('T2: 任务在本窗口内结束 → 原样返回真实结果', async () => {
  const ctx = fakeCtx();
  installJobWaitGuard(ctx);

  const exec = { name: 'job_output', arguments: { job_id: 'pwsh-done', wait: true, timeout_ms: 270000 } };
  const real = {
    content: [{ type: 'text', text: 'done, exit code 0\n[status: completed, exit code: 0]' }],
    isError: false,
    value: { text: 'done', job: { id: 'pwsh-done', status: 'completed' } },
  };

  const result = await ctx.run(exec, async () => real);
  assert.equal(result, real, '任务结束时必须原样返回工具结果，不得包装');
});

test('T3: 上下文失活异常 → 降级为可读工具错误（绝不 throw）', async () => {
  const ctx = fakeCtx();
  installJobWaitGuard(ctx);

  const exec = { name: 'job_output', arguments: { job_id: 'pwsh-x', wait: true, timeout_ms: 270000 } };
  const frameworkError = new Error('cannot get required service "jobs" in inactive context');

  const result = await ctx.run(exec, async () => { throw frameworkError; });

  assert.equal(result.isError, true, '必须降级成工具错误');
  assert.equal(result.error.info.code, 'JOB_CONTEXT_LOST', '要给结构化错误码');
  const text = String(result.content[0].text);
  assert.match(text, /上下文已失效/, '文案要说明宿主上下文失效');
  assert.match(text, /磁盘|产物|job_list|mode2_plan/, '必须给出可操作的替代路径');
  assert.doesNotMatch(text, /inactive context/, '不要把框架内部错误原样丢给模型');
});

test('T3b: 短等待也命中上下文失活 → 同样降级（不止长等待那条路径）', async () => {
  const ctx = fakeCtx();
  installJobWaitGuard(ctx);

  const exec = { name: 'job_output', arguments: { job_id: 'pwsh-y', wait: true, timeout_ms: 20000 } };
  const result = await ctx.run(exec, async () => {
    throw new Error('cannot get required service "jobs" in inactive context');
  });
  assert.equal(result.isError, true);
  assert.equal(result.error.info.code, 'JOB_CONTEXT_LOST');
});

test('T4: 非 job_output / 短等待 / wait:false → 原样透传', async () => {
  const ctx = fakeCtx();
  installJobWaitGuard(ctx);

  const cases = [
    { name: 'pwsh', arguments: { command: 'echo hi', run_in_background: true } },
    { name: 'job_output', arguments: { job_id: 'j1', wait: true, timeout_ms: 30000 } },
    { name: 'job_output', arguments: { job_id: 'j2', wait: false } },
    { name: 'job_list', arguments: {} },
  ];

  for (const exec of cases) {
    const real = { content: [{ type: 'text', text: 'unchanged' }], isError: false };
    const before = JSON.stringify(exec.arguments);
    const result = await ctx.run(exec, async () => real);
    assert.equal(result, real, exec.name + ' 必须原样透传');
    assert.equal(JSON.stringify(exec.arguments), before, exec.name + ' 参数不得被改写');
  }
});

test('T5: 守卫自身异常不能拖垮工具（回落 next）', async () => {
  const ctx = fakeCtx();
  installJobWaitGuard(ctx);
  // arguments 访问器抛错 → 守卫内部应吞掉并原样透传
  const exec = { name: 'job_output', get arguments() { throw new Error('boom'); } };
  const real = { content: [{ type: 'text', text: 'ok' }], isError: false };
  const result = await ctx.run(exec, async () => real);
  assert.equal(result, real);
});
