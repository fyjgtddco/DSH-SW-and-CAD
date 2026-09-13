// dsh-engineering-ui — Host 半 v5
// 数据接口：子代理树 / 原始事件流 / 阻塞式代问 / turn-stopping 守卫。
//
// 设计要点：
//  1. 事件以原始形态下发（seq/time/type/data），字段映射由 client 半负责，
//     这样字段名修正只需刷新页面（HMR），无需重启 DSH。
//  2. 【Bug1 根因】子代理无权向用户提问。DSH 的 userQuestions.ask() 会校验
//     `agents.roots().includes(agent)`，子代理不在 roots 中 → 必抛
//     DELEGATED_CALLER。因此子代理的 ask_user_question 永远失败。
//     旧版用 subagents.followup 把答案当"下一轮消息"发回去 —— 那是新指令，
//     不是工具返回值，子代理会把确认当成新任务，造成"自问自答/消息错乱"。
//  3. 【Bug1 修复】改为主代理代问：提问路由到主代理（root），由主代理调
//     ctx.userQuestions.ask({ agent: 主代理 })，该方法【真正阻塞挂起】，
//     用户在前端作答后 resolve → 答案以 tool_result 形式绑定到那次工具调用。
//  4. 【问题3】用 agent/turn-stopping 钩子 + agent.steer() 阻止对话提前结束：
//     仅对 engineering 预设生效（见 GUARD_PRESET）。
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { resolveSessionPreset } from '@deepseek-ai/dsh-agent-presets';

export const name = 'dsh-engineering-ui';
// 【必须声明 userQuestions】阻塞式代问依赖 ctx.userQuestions.ask()；
// cordis 的依赖注入要求服务名必须列在 inject 里，否则 ctx.userQuestions 为
// undefined，/ask 会直接失败（"userQuestions 服务不可用"）。
export const inject = ['webServer', 'subagents', 'sessionPersistence', 'sessions', 'agents', 'userQuestions'];
const PREFIX = '/dsh-engineering-ui';
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
// 【Bug2】守卫只对这一个预设的会话生效。原先无差别 steer 所有对话 → 到处都不给结束。
const GUARD_PRESET = 'engineering';
// 高频低值事件：不入前端流（流式 chunk 每token一条，会挤满窗口）
const RAW_DROP = new Set(['assistant/chunk', 'step/start', 'step/end', 'request/header']);

// 【问题3】阻止对话提前结束
const GUARD_TEXT = [
  '【系统强制指令 · 不可忽略】',
  '本轮对话禁止结束。你必须在当前任务全部完成（所有零件建模完毕、验证通过、文件已输出）之后才能结束；',
  '或者仅在用户明确要求停止时才可结束。',
  '现在请继续执行下一步实际工作（调用工具推进任务），不要仅用文字回复表示完成。'
].join('\n');

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(text);
}

function queryOf(req) {
  try {
    return new URL(req.url, 'http://localhost').searchParams;
  } catch {
    return new URLSearchParams();
  }
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 2000000) req.destroy();
    });
    req.on('end', () => resolve(data));
    req.on('error', () => resolve(''));
  });
}

/** 从持久化事件里找出某会话的父会话 ID。 */
async function parentOf(ctx, childId) {
  try {
    const insp = await ctx.sessionPersistence.inspect(childId);
    const evs = (insp && insp.events) || [];
    for (const ev of evs) {
      if (ev.type === 'request/header' && ev.data && ev.data.header) {
        const p = ev.data.header.parentSession;
        if (p) return String(p);
      }
    }
    // 兜底：session 元数据
    if (insp && insp.meta && insp.meta.parentSession) return String(insp.meta.parentSession);
  } catch (e) {}
  return null;
}

export function apply(ctx) {
  const disposers = [];

  // ── 【Bug2】会话预设解析（同步缓存，供 turn-stopping 钩子零延迟查询）──────
  // 钩子必须同步返回，不能 await，所以这里预先把 sessionId → preset 缓存起来；
  // 缓存未命中时给一次异步回填，本次按"非工程模式"放行（宁可漏守一次，
  // 也绝不能像旧版那样把非工程会话也拦住）。
  const presetCache = new Map(); // sessionId -> preset id | null

  async function readPreset(sessionId) {
    if (!sessionId) return null;
    if (presetCache.has(sessionId)) return presetCache.get(sessionId);
    try {
      const insp = await ctx.sessionPersistence.inspect(sessionId);
      const header = (insp && insp.meta) || {};
      const events = (insp && insp.events) || [];
      // resolveSessionPreset 需要 { header, events }：取最新的 agent-preset/selected，
      // 没有该事件时才回落到头部创建时的值。
      const resolved = resolveSessionPreset({ header, events });
      const value = resolved === undefined ? null : String(resolved);
      presetCache.set(sessionId, value);
      return value;
    } catch (e) {
      return null;
    }
  }

  function isGuardSession(agent) {
    const sessionId = (agent && (agent.sessionId || agent.id)) || null;
    if (!sessionId) return false;
    if (presetCache.has(sessionId)) return presetCache.get(sessionId) === GUARD_PRESET;
    // 冷缓存：异步回填，本次放行（避免误拦、避免 await 破坏同步钩子）
    readPreset(sessionId).catch(() => {});
    return false;
  }

  // ── 【问题3】turn-stopping 守卫：阻止对话提前结束 ────────────────────────
  // DSH 官方语义：agent/turn-stopping 在 turn 即将关闭时触发，
  // listener 若调用 agent.steer(...) 注入消息，机器会重读 inbox 并再跑一步。
  // 这样"模型想停"就会被强制继续，直到任务真正完成或用户叫停。
  const guardState = new Map(); // agentId -> { count, firstAt }
  const MAX_GUARD = 40;         // 单轮最多阻止 40 次，防止无限循环
  const GUARD_WINDOW = 30 * 60 * 1000; // 30 分钟内计数，超过则重置

  disposers.push(ctx.on('agent/turn-stopping', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      // 用户主动取消 → 放行
      const signal = payload.signal;
      if (signal && signal.aborted) return;
      // 【Bug2 修复】只有 engineering 预设的会话才拦截结束。
      // 旧版此处无任何预设判断，导致所有对话（含普通聊天）都被反复 steer，
      // 用户表现为"到处每个对话每次都不给结束"。
      if (!isGuardSession(agent)) return;
      const st = guardState.get(agent.id) || { count: 0, firstAt: Date.now() };
      if (Date.now() - st.firstAt > GUARD_WINDOW) { st.count = 0; st.firstAt = Date.now(); }
      if (st.count >= MAX_GUARD) return;
      st.count += 1;
      guardState.set(agent.id, st);
      // 必须带 source：DSH 宿主多处按 message.source.kind 判别消息来源
      // （dsh-agent-loop/isOwned、dsh-goal-round-driver/restoreOtherClaimed 等），
      // 缺 source 的 user 消息会让宿主抛 "Cannot read properties of undefined (reading 'kind')"，
      // 该错误经 promptError 冒到输入框下方的 Toast。见 2026-09-12 排查记录。
      const msg = createUserMessage({
        content: [{ type: 'text', text: GUARD_TEXT }],
        source: { kind: 'plugin', plugin: 'dsh-engineering-ui' }
      });
      agent.steer(msg);
    } catch (e) {
      // 守卫失败不能影响主流程
    }
  }));

  // ── 子代理树 ──────────────────────────────────────────────────────────────
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/agents',
    handler: async (req, res) => {
      const q = queryOf(req);
      const root = (q.get('rootSessionId') || '').trim();
      if (!ID_RE.test(root)) return send(res, 400, { ok: false, error: 'bad rootSessionId' });
      try {
        const list = await ctx.subagents.listDescendants(root);
        return send(res, 200, { ok: true, rootSessionId: root, agents: list });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── 原始事件流（不做字段映射，client 半负责解析）──────────────────────────
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/log',
    handler: async (req, res) => {
      const q = queryOf(req);
      const sessionId = (q.get('sessionId') || '').trim();
      const since = Number(q.get('since') || 0);
      const limit = Math.min(Number(q.get('limit') || 300), 800);
      if (!ID_RE.test(sessionId)) return send(res, 400, { ok: false, error: 'bad sessionId' });
      try {
        const inspection = await ctx.sessionPersistence.inspect(sessionId);
        if (!inspection) return send(res, 404, { ok: false, error: 'no session log' });
        const seedLength = (inspection.meta && inspection.meta.seedLength) || 0;
        const all = (inspection.events || []).filter((e) => e.seq >= seedLength && !RAW_DROP.has(e.type));
        const out = [];
        for (const event of all) {
          if (since && event.seq <= since) continue;
          out.push({ seq: event.seq, time: event.time, type: event.type, data: event.data });
        }
        const tail = out.slice(-limit);
        return send(res, 200, {
          ok: true,
          sessionId,
          title: inspection.meta && inspection.meta.title,
          createdAt: inspection.meta && inspection.meta.createdAt,
          latestSeq: tail.length ? tail[tail.length - 1].seq : since,
          events: tail
        });
      } catch (error) {
        return send(res, 200, { ok: false, error: String((error && error.message) || error) });
      }
    }
  }));

  // ── 【Bug1 核心修复】阻塞式代问（Blocking Wait）────────────────────────────
  // 问题本质：子代理调 ask_user_question 必然抛 DELEGATED_CALLER ——
  //   dsh-user-questions 的 ask() 会执行 `agents.roots().includes(agent)` 校验，
  //   只有运行时根代理（主对话）能通过；子代理被判定为 owned child，
  //   平台直接注释说明"an owned child has no human answerer and would block forever"。
  //
  // 错误做法（旧版）：用 subagents.followup 把答案当"下一轮消息"推给子代理。
  //   followup 的语义是"作为子代理的下一次 FIFO 回合"——它是一条新指令，
  //   不是那次工具调用的返回值。子代理收到后会当成新任务/自问自答，消息错乱。
  //
  // 正确做法（本实现）：
  //   提问必须由【主代理】发起。ctx.userQuestions.ask({ agent: 主代理 }) 返回
  //   一个【真正挂起的 Promise】，直到用户在前端作答才 resolve；resolve 的值
  //   直接就是那次工具调用的 tool_result。子代理侧拿到的是"工具返回了答案"，
  //   然后从原来中断的地方继续，绝不会当成新 Prompt。
  //
  // 本端点职责：
  //   - /ask     主代理发起阻塞式提问（真正挂起，直到用户作答）
  //   - /answer  前端提交选择 → resolve 上面对应 pendingId 的挂起 Promise
  //
  // 挂起登记表：pendingId -> { questions, resolve, reject, settled }
  // 这是"阻塞等待"的物理载体。只要它还在表里，调用方的 Promise 就没结束，
  // 该轮次就不会关闭、不会开启新一轮 —— 这正是需求 1「强制阻塞等待」。
  const pendingAsks = new Map();

  // ── 【Bug1】阻塞式提问桥接（供子代理侧上报使用）────────────────────────
  //
  // 【分工说明 · 重要】
  //   · 主对话的提问：完全由 DSH 原生承担，本插件不介入。
  //     原生链路：模型调 ask_user_question 工具
  //       → ctx.userQuestions.ask({agent: 主代理})  ← 真正挂起
  //       → question/requested 帧 → @deepseek-ai/dsh-client-ui-user-questions
  //         接管 conversation.composer 渲染选项卡
  //       → 用户点选项 + "下一题/提交" → pending.answer() → wait.respond()
  //       → 标准 RPC 回填 → **成为那次工具调用的 tool_result**
  //     该链路天然满足：阻塞等待、绑定工具返回值、不自动提交、无模型参与。
  //
  //   · 子代理的提问：平台硬性禁止（DELEGATED_CALLER），只能在子代理面板里
  //     用本端点做桥接 —— 由本插件代为主代理发起 ask()，把 pendingId 交给
  //     前端选项卡，用户提交后 resolve 成 tool_result。
  //
  // DSH 的 userQuestions.ask 会校验 agent 是否为运行时根代理：
  //   子代理 → DELEGATED_CALLER（平台设计如此，无法绕过）
  //   主代理 → 通过，且 ask() 返回的 Promise 直到用户作答才 resolve
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/ask',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const sessionId = String((payload && payload.sessionId) || '').trim();
      const questions = Array.isArray(payload && payload.questions) ? payload.questions : [];
      if (!ID_RE.test(sessionId)) return send(res, 400, { ok: false, error: 'bad sessionId' });
      if (!questions.length) return send(res, 400, { ok: false, error: 'empty questions' });

      const agents = ctx.agents;
      const uq = ctx.userQuestions;
      if (!agents || typeof agents.get !== 'function') {
        return send(res, 200, { ok: false, error: 'agents 服务不可用' });
      }
      if (!uq || typeof uq.ask !== 'function') {
        return send(res, 200, { ok: false, error: 'userQuestions 服务不可用（无法发起原生提问）' });
      }
      // 找到既有的 live agent（必须是运行时根代理，否则 ask() 会拒绝）
      const agent = agents.get(sessionId);
      if (agent === undefined) {
        return send(res, 200, { ok: false, error: '会话不在运行中（请保持主对话开启）' });
      }
      const roots = typeof agents.roots === 'function' ? agents.roots() : [];
      if (!roots.includes(agent)) {
        return send(res, 200, {
          ok: false,
          error: 'DELEGATED_CALLER：该会话是被拥有的子代理，平台禁止其向用户提问。'
               + '提问必须由主对话发起（子代理应把问题上报主对话）。'
        });
      }

      const pendingId = 'q_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);

      // 【bug6 修复】entry 必须自己持有 resolve —— 旧版只把 ask() 返回的 promise
      // 存成 entry.promise，却从没往 entry 上挂 resolve/reject，于是 /answer 里
      // 执行 entry.resolve({answers}) 直接抛 "entry.resolve is not a function"。
      // 正确做法：自建 Promise 拿到它的 resolve/reject 存进 entry，
      // 同时把原生 ask() 的完成桥接到同一个 entry，保证只 settle 一次。
      let settleResolve, settleReject;
      const settledPromise = new Promise((res2, rej2) => { settleResolve = res2; settleReject = rej2; });

      const entry = {
        questions: questions.map((q) => ({
          id: String(q.id || 'q1'),
          question: String(q.question || ''),
          options: Array.isArray(q.options) ? q.options : []
        })),
        settled: false,
        resolve: settleResolve,
        reject: settleReject,
        promise: settledPromise
      };
      pendingAsks.set(pendingId, entry);

      // 原生 ask()：真正挂起，直到用户作答（或取消）。
      // 它 resolve 的答案走标准 RPC 回填成 tool_result —— 这是主路径。
      try {
        uq.ask({
          questions: questions.map((q) => ({
            id: String(q.id || 'q1'),
            question: String(q.question || ''),
            ...(q.header !== undefined ? { header: String(q.header) } : {}),
            ...(Array.isArray(q.options) ? { options: q.options } : {}),
            ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
          })),
          agent
        }).then((answer) => {
          if (!entry.settled) {
            entry.settled = true;
            pendingAsks.delete(pendingId);
            settleResolve(answer);
          }
        }).catch((err) => {
          if (!entry.settled) {
            entry.settled = true;
            pendingAsks.delete(pendingId);
            settleReject(err);
          }
        });
      } catch (err) {
        pendingAsks.delete(pendingId);
        return send(res, 200, { ok: false, error: String((err && err.message) || err) });
      }
      return send(res, 200, {
        ok: true, pendingId, sessionId,
        note: '已发起原生阻塞式提问；答案将以 tool_result 形式回到提问方。'
      });
    }
  }));

  // ══ 【用户诉求】子代理面板直接提问 ═══════════════════════════════════════
  // 目标：子代理提出问题时，选项卡片出现在【右侧子代理面板】，
  //       用户在面板里选完 → 子代理那边即刻生效 —— 不需要结束回合、
  //       也不需要主对话再转发一圈。
  //
  // 技术约束（已核实源码）：
  //   · dsh-user-questions 的 ask() 校验 agents.roots().includes(agent)，
  //     子代理必然 DELEGATED_CALLER —— 无法让子代理自己调用 ask()。
  //   · host-apiproxy 的 provider 用 sessionId = request.agent?.id 作为帧路由，
  //     且应答校验 matchesQuestions() 要求 payload.sessionId === pending.sessionId。
  //   · 结论：原生帧只能挂在"能通过校验的那个 agent（主代理）"名下。
  //
  // 因此实现方式为【代理持有 + 面板呈现】：
  //   1) 本端点代表子代理发起 ask()，agent 传主代理（通过校验，帧能发出去）
  //   2) 但把 pendingId 与本子代理的 sessionId 建立绑定，返回给子代理面板
  //   3) 子代理面板渲染选项卡，用户选择后提交 pendingId → resolve
  //   4) 调用方（发起该提问的子代理）拿到结果当作工具返回值继续 —— 不结束回合
  //
  // 与旧 followup 方案的本质区别：答案通过 ask() 的 resolve 回到【发起的那个
  // 调用栈】，而不是变成一条"下一轮消息"。这正是"选完即生效"的关键。
  const childAsks = new Map(); // pendingId -> { childSessionId, questions, resolve, reject, settled }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/ask-child',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try { payload = JSON.parse(await readBody(req)); } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      const childSessionId = String((payload && payload.childSessionId) || '').trim();
      const questions = Array.isArray(payload && payload.questions) ? payload.questions : [];
      if (!ID_RE.test(childSessionId)) return send(res, 400, { ok: false, error: 'bad childSessionId' });
      if (!questions.length) return send(res, 400, { ok: false, error: 'empty questions' });

      const agents = ctx.agents;
      const uq = ctx.userQuestions;
      if (!agents || !uq || typeof uq.ask !== 'function') {
        return send(res, 200, { ok: false, error: '服务不可用（agents/userQuestions）' });
      }
      // 找一个能通过所有权校验的 root（主代理）来承载这次提问
      const roots = typeof agents.roots === 'function' ? agents.roots() : [];
      const holder = roots.find((a) => a && a.id) || null;
      if (!holder) {
        return send(res, 200, { ok: false, error: '没有可用的根代理承载提问（请确认主对话在运行）' });
      }

      const pendingId = 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
      let settleResolve, settleReject;
      const settledPromise = new Promise((r2, j2) => { settleResolve = r2; settleReject = j2; });

      const entry = {
        childSessionId,
        questions: questions.map((q) => ({ id: String(q.id || 'q1'), question: String(q.question || '') })),
        settled: false,
        resolve: settleResolve,
        reject: settleReject,
        promise: settledPromise
      };
      childAsks.set(pendingId, entry);

      try {
        uq.ask({
          questions: questions.map((q) => ({
            id: String(q.id || 'q1'),
            question: String(q.question || ''),
            ...(q.header !== undefined ? { header: String(q.header) } : {}),
            ...(Array.isArray(q.options) ? { options: q.options } : {}),
            ...(q.multiSelect !== undefined ? { multiSelect: !!q.multiSelect } : {})
          })),
          agent: holder
        }).then((answer) => {
          if (!entry.settled) {
            entry.settled = true;
            childAsks.delete(pendingId);
            settleResolve(answer);
          }
        }).catch((err) => {
          if (!entry.settled) {
            entry.settled = true;
            childAsks.delete(pendingId);
            settleReject(err);
          }
        });
      } catch (err) {
        childAsks.delete(pendingId);
        return send(res, 200, { ok: false, error: String((err && err.message) || err) });
      }

      return send(res, 200, {
        ok: true, pendingId, childSessionId,
        note: '子代理提问已登记；请在右侧子代理面板作答，答案将成为该提问的返回值。'
      });
    }
  }));

  // 子代理面板查询：当前有哪些属于该子代理的待答问题
  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/pending-child',
    handler: async (req, res) => {
      const q = queryOf(req);
      const childSessionId = (q.get('childSessionId') || '').trim();
      if (!ID_RE.test(childSessionId)) return send(res, 400, { ok: false, error: 'bad childSessionId' });
      const out = [];
      for (const [pid, e] of childAsks.entries()) {
        if (e.childSessionId === childSessionId && !e.settled) out.push({ pendingId: pid, questions: e.questions });
      }
      return send(res, 200, { ok: true, childSessionId, pending: out });
    }
  }));

  // 已作答题目的结果留存：/ask-child 的答案在 resolve 后仍可被取回。
  // 子代理 CLI（ask_user.py）靠它把答案读回来当作"工具返回值"。
  // 保留 10 分钟，够一次阻塞等待读取；过期自动清理。
  const childResults = new Map(); // pendingId -> { answers, at }

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/result-child',
    handler: async (req, res) => {
      const q = queryOf(req);
      const pendingId = (q.get('pendingId') || '').trim();
      if (!pendingId) return send(res, 400, { ok: false, error: 'bad pendingId' });
      const hit = childResults.get(pendingId);
      if (!hit) {
        return send(res, 200, { ok: false, error: '结果不存在或已过期' });
      }
      return send(res, 200, { ok: true, answers: hit.answers });
    }
  }));

  // 定期清理过期结果（10 分钟）
  const _resultSweeper = setInterval(() => {
    const now = Date.now();
    for (const [pid, v] of childResults.entries()) {
      if (now - v.at > 10 * 60 * 1000) childResults.delete(pid);
    }
  }, 60 * 1000);
  if (_resultSweeper && typeof _resultSweeper.unref === 'function') _resultSweeper.unref();
  disposers.push(() => clearInterval(_resultSweeper));

  disposers.push(ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/answer',
    handler: async (req, res) => {
      if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
      let payload;
      try {
        payload = JSON.parse(await readBody(req));
      } catch {
        return send(res, 400, { ok: false, error: 'bad json' });
      }
      // 【Bug1】提交的是"对某个已发布问题的回答"，而不是一段自由文本。
      // pendingId 指向 ask-block 阶段登记的那次提问；我们只负责 resolve 它，
      // 答案会沿着 ask() 的返回路径变成那次工具调用的 tool_result。
      const pendingId = String((payload && payload.pendingId) || '').trim();
      const selected = Array.isArray(payload && payload.selected) ? payload.selected.map(String) : [];
      const custom = String((payload && payload.custom) || '').slice(0, 4000);

      // ── 1) pendingId 路径：resolve 挂起的 ask()（正解）──────────────────
      // 两个登记表：pendingAsks（主对话发起）/ childAsks（子代理面板发起）。
      // 子代理提问优先匹配 —— 这正是"在子代理面板选完就生效"的落点。
      if (pendingId) {
        const isChild = childAsks.has(pendingId);
        const entry = isChild ? childAsks.get(pendingId) : pendingAsks.get(pendingId);
        if (!entry) {
          return send(res, 200, {
            ok: false,
            error: '该问题已结束或不存在（可能已被回答/取消）。请等待新的提问。'
          });
        }
        if (entry.settled) {
          return send(res, 200, { ok: false, error: '该问题已作答，请勿重复提交。' });
        }
        entry.settled = true;
        if (isChild) childAsks.delete(pendingId); else pendingAsks.delete(pendingId);
        try {
          // 回答形状：{ answers: [{ id, selected, custom? }] }
          const answers = entry.questions.map((q) => {
            const picked = selected.filter((s) => s.qid === q.id).map((s) => s.label);
            const one = selected.find((s) => s.qid === q.id && typeof s.custom === 'string');
            const item = { id: q.id, selected: picked };
            if (one && one.custom) item.custom = one.custom;
            return item;
          });
          // 若前端只提交了单一 custom（自由文本），挂到第一题
          if (custom && answers.length && !answers[0].custom) answers[0].custom = custom;
          entry.resolve({ answers });
          // 子代理提问：把结果留存，供 ask_user.py 轮询取回（当作工具返回值）
          if (isChild) childResults.set(pendingId, { answers, at: Date.now() });
          return send(res, 200, {
            ok: true, pendingId,
            bound: isChild ? 'child-tool-result' : 'tool_result',
            childSessionId: isChild ? entry.childSessionId : undefined
          });
        } catch (error) {
          return send(res, 200, { ok: false, error: String((error && error.message) || error) });
        }
      }

      // ── 2) 兼容旧前端：只有文本，没有 pendingId ───────────────────────
      // 明确拒绝而不是静默 followup —— 旧路径正是"消息错乱"的元凶。
      return send(res, 200, {
        ok: false,
        error: '缺少 pendingId：答案必须绑定到一次具体提问。'
             + '（旧版 followup 投递会导致子代理把确认当新任务，已废弃）'
      });
    }
  }));

  return () => {
    for (const dispose of disposers.reverse()) {
      try { dispose(); } catch {}
    }
  };
}
