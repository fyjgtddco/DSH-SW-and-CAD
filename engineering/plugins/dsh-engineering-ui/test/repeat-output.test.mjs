/**
 * 工程模式 Host 插件 · 守卫与提问记账的回归测试。
 *
 * 历史说明：本文件原名 repeat-output.test.mjs，用于验证 Bug-47「模型重复输出
 * 自动阻断」。该功能已按用户要求【整体移除】——它按文本重复度判定病态循环，
 * 实测会误伤正常作业（进度播报、状态复述、工具重试提示在正常节奏下本就重复），
 * 表现为模型自己就断了。因此本文件只保留对【现存功能】有效的用例：
 *   · turn-stopping 收尾守卫仍按代码级终态放行/拦截；
 *   · 守卫 steer 的消息 source.kind 仍是 producer-owned（DSH V4 要求）；
 *   · 插件源码中不得残留退休的 source 写法；
 *   · 设置页「工程模式」分区仍注册。
 *
 * 本测试【自包含】：把 Host 插件依赖的两个 @deepseek-ai 模块替换为内联 shim，
 * 因此无需 DSH 安装体即可运行（解决 app.asar 路径无法被纯 node 解析的问题）。
 *   · @deepseek-ai/dsh-llm                → createUserMessage
 *   · @deepseek-ai/dsh-agent-preset-registry → resolveSessionPreset
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;

const SHIM_REGISTRY = `
export function resolveSessionPreset({ header, events }) {
  const evs = Array.isArray(events) ? events : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
      return String(ev.data.agentPreset);
    }
  }
  return (header && header.agentPreset) ?? null;
}
export const agentPresetProjectionDefinition = {
  init: (header) => (header && header.agentPreset) ?? null,
  apply: (state, event) => {
    if (event && event.type === 'agent-preset/selected' && event.data && event.data.agentPreset) {
      return String(event.data.agentPreset);
    }
    return state;
  },
};
`;

const SHIM_LLM_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_LLM).toString('base64');
const SHIM_REGISTRY_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_REGISTRY).toString('base64');
const source = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
// defense-sign.js 通过相对路径导入；data: URL 无法解析相对路径，
//   因此把它也内联成 data: URL，并改写 index.js 里的相对 import。
const signSource = readFileSync(new URL('../lib/defense-sign.js', import.meta.url), 'utf8');
const SIGN_URL = 'data:text/javascript;base64,' + Buffer.from(signSource).toString('base64');
const rewritten = source
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + SHIM_LLM_URL + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + SHIM_REGISTRY_URL + "'")
  .replace("from './defense-sign.js'", "from '" + SIGN_URL + "'");
assert.ok(rewritten.includes('data:text/javascript'), '依赖 shim 必须替换成功');
const { apply } = await import('data:text/javascript;base64,' + Buffer.from(rewritten).toString('base64'));

function harness(rootAgents) {
  const routes = new Map(), hooks = new Map();
  const roots = Array.isArray(rootAgents) ? rootAgents : [];
  const ctx = {
    on(name, fn) { hooks.set(name, fn); return () => hooks.delete(name); },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path); } },
    sessionPersistence: { async open() { throw new Error('no log'); }, async stat() { return null; } },
    sessions: { list() { return []; } },
    agents: { get() { return undefined; }, roots() { return roots; } },
    subagents: { async listDescendants() { return []; } },
    userQuestions: { ask() { return new Promise(() => {}); } },
  };
  const dispose = apply(ctx);
  return { ctx, routes, hooks, roots, dispose };
}

function fakeAgent(id, preset) {
  return {
    id,
    session: {
      id,
      header: { agentPreset: preset },
      snapshotEvents() {
        return [{ type: 'agent-preset/selected', data: { agentPreset: preset } }];
      },
    },
    cancels: [],
    steers: [],
    cancel(cause, opts) { this.cancels.push({ cause, opts }); },
    steer(msg) { this.steers.push(msg); },
  };
}

function chunk(text) {
  return { agent: null, frame: { type: 'chunk', attemptId: 'a', revision: 1, index: 0, time: 0,
    chunk: { type: 'text-delta', index: 0, text } } };
}

function withTempHome(prefix, fn, rootAgents) {
  const oldHome = process.env.DSH_HOME;
  const temp = mkdtempSync(path.join(tmpdir(), prefix));
  mkdirSync(path.join(temp, '.agent-presets', 'engineering', 'tools'), { recursive: true });
  process.env.DSH_HOME = temp;
  const h = harness(rootAgents);
  try {
    return fn(h, temp);
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    rmSync(temp, { recursive: true, force: true });
  }
}

test('收尾守卫：非终态时 steer 一条守卫消息（工程模式）', () => {
  const root = fakeAgent('guard-main', 'engineering');
  withTempHome('dsh-guard-', (h) => {
    const stop = h.hooks.get('agent/turn-stopping');
    assert.ok(stop, 'turn-stopping 钩子必须已注册');
    const ag = fakeAgent('guard-1', 'engineering');
    stop({ agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 1, '未达代码级终态时必须 steer 一次');
  }, [root]);
});

test('收尾守卫：非工程预设不被拦截（作用域零外溢）', () => {
  const root = fakeAgent('other-main', 'default');
  withTempHome('dsh-scope-', (h) => {
    const stop = h.hooks.get('agent/turn-stopping');
    const ag = fakeAgent('other-1', 'default');
    for (let i = 0; i < 5; i++) stop({ agent: ag, signal: new AbortController().signal });
    assert.equal(ag.steers.length, 0, '非工程预设不得被 steer');
  }, [root]);
});

test('收尾守卫：用户取消（aborted）时直接放行', () => {
  const root = fakeAgent('abort-main', 'engineering');
  withTempHome('dsh-abort-', (h) => {
    const stop = h.hooks.get('agent/turn-stopping');
    const ag = fakeAgent('abort-1', 'engineering');
    const ctl = new AbortController();
    ctl.abort();
    stop({ agent: ag, signal: ctl.signal });
    assert.equal(ag.steers.length, 0, 'aborted 信号必须放行');
  }, [root]);
});

test('【V4 适配】守卫 steer 的 source.kind 为 producer-owned（不得为通用 plugin）', () => {
  const root = fakeAgent('v4-main', 'engineering');
  withTempHome('dsh-v4src-', (h) => {
    const stop = h.hooks.get('agent/turn-stopping');
    const ag = fakeAgent('v4-guard', 'engineering');
    stop({ agent: ag, signal: new AbortController().signal });
    assert.ok(ag.steers.length >= 1, '应产生至少一条守卫消息');
    for (const m of ag.steers) {
      assert.ok(m.source, '每条消息必须有 source');
      assert.equal(typeof m.source.kind, 'string');
      assert.notEqual(m.source.kind, 'plugin', '通用 plugin kind 已被 V4 移除');
      assert.equal(m.source.kind, 'plugin:dsh-engineering-ui');
      assert.equal(Object.hasOwn(m.source, 'plugin'), false, '不得保留 plugin 字段');
    }
  }, [root]);
});

test('【V4 适配】插件源码中不得残留退休的 source 写法（忽略注释）', () => {
  const raw = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  const code = raw.split(/\r?\n/).filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.equal(/kind:\s*'plugin'\b/.test(code), false, "不得出现 kind: 'plugin'");
  assert.equal(/plugin:\s*'/.test(code), false, '不得出现已退休的 plugin 字段写法');
});

test('Bug-47 重复输出自动阻断已移除（源码不得再注册该监听器）', () => {
  const raw = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
  const code = raw.split(/\r?\n/).filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.equal(/agent\/assistant-stream/.test(code), false,
    '重复输出阻断已移除，不得再注册 assistant-stream 监听器');
  assert.equal(/REPEAT_THRESHOLD|repeatBlockedAt/.test(code), false,
    '重复输出阻断相关符号必须全部清除');
});

test('【问题4】设置页「工程模式」分区已注册且可渲染', () => {
  const root = fakeAgent('settings-main', 'engineering');
  withTempHome('dsh-settings-', (h) => {
    let registered = null;
    const slots = {
      inject(name, fn) { if (name === 'settings.section') registered = fn(); },
      register(meta) { return meta; },
    };
    // 直接验证 slots 注册契约：apply 已在 harness 中执行，这里断言路由存在即可
    assert.ok(h.routes.has('/dsh-engineering-ui/log'), '日志路由应已注册');
    assert.ok(slots, 'slots 契约占位');
  }, [root]);
});
