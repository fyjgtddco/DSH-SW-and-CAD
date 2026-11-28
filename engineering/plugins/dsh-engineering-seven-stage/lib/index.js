import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import { openFlow, roles, specHashDrift } from './flow.js';

export const name = 'dsh-engineering-seven-stage';
export const inject = ['tools','agents','subagents'];
const TOOL = 'engineering_seven_stage';
const instances = new Set();
const actorOf = agent => {
  const id = agent?.session?.id;
  if (!id) return null;
  const h = agent.session.header ?? {};
  const parent = h.parentSession ?? agent.parentSession ?? agent.parentId ?? null;
  if (!parent && Number(agent.delegationDepth ?? h.delegationDepth ?? 0) > 0) return null;
  return { id:String(id), parent:parent ? String(parent) : null };
};

const check = (yes, code) => { if(!yes) throw Error(code); };
const compact = x => String(x ?? '').trim();
// ══ 【BUG-02 修复】capture 必须能读【ask_user_question 的答案】═══════════════
// 现场（第 3 轮实测，阻断级）：主对话用 ask_user_question 拿到用户对 Q1~Q4 的
//   四项明确答复，planner 侧 newFacts 里却只有"用户最后随口说的一句话"。
//   于是七阶段的 question → capture → interpret 链路在【最常用的提问方式】下是断的。
//
// 根因：ask_user_question 的答复是以 **tool result** 回到会话的——
//   role:'tool'、source:{kind:'tool',callId}、content 里是一段 JSON
//   {answers:[{id,selected[],custom?}]}。
//   旧过滤器只认 role==='user' && source.kind==='user'，把它整体丢弃。
//
// 修法：增加第二条采集通道，但**严格限定为 ask_user_question 的成功输出**。
//   为什么不放宽到"任意 tool result"：
//     那样模型自己写的工具输出也会被当成"真人原话"，而 capture 的全部意义
//     就是"只认宿主给的事实"。放宽 = 把伪造真人答复的口子重新打开。
//   所以这里的判据是【输出形状】而不是【工具名】：只有精确匹配
//   {answers:[{id:string, selected:string[], custom?:string}]} 的才收。
const ASK_MAX_ITEMS = 64;
/**
 * 解析 ask_user_question 的答案。
 *
 * 【2026-11-19】返回值从"一段文本"扩成 {text, ids}：
 *   ids 是每道题的题目 id（如 rated_duty）。根会话手里通常只有题目 id，
 *   而 capture 内部用的是消息 id —— 两者不同，现场就是拿题目 id 去 capture/interpret
 *   连撞两次错（ROOT_USER_MESSAGE_NOT_AVAILABLE / HOST_CAPTURED_MESSAGE_REQUIRED）。
 *   现在 capture 允许按题目 id 反查，错误提示里也把两种 id 都列出来。
 */
const askAnswer = m => {
  if (m?.role !== 'tool') return null;
  if (m?.source?.kind !== 'tool') return null;
  const raw = (m.content ?? []).filter(x => x && x.type === 'text')
    .map(x => x.text ?? '').join('').trim();
  if (!raw || raw.length > 60_000) return null;
  let json;
  try { json = JSON.parse(raw); } catch { return null; }
  const answers = json?.answers;
  if (!Array.isArray(answers) || !answers.length || answers.length > ASK_MAX_ITEMS) return null;
  // 严格形状校验：任何一个 answer 形状不对就整条不收（宁可漏，不可误收）
  if (!answers.every(a => a && typeof a === 'object'
      && typeof a.id === 'string' && a.id.trim()
      && Array.isArray(a.selected) && a.selected.every(s => typeof s === 'string')
      && (a.custom === undefined || typeof a.custom === 'string'))) return null;
  const text = answers.map(a => {
    const pick = (a.selected ?? []).map(s => String(s).trim()).filter(Boolean).join('、');
    const custom = String(a.custom ?? '').trim();
    const body = pick && custom ? pick + ' / ' + custom : (pick || custom);
    return '[' + a.id.trim() + '] ' + (body || '（用户跳过该题）');
  }).join('\n');
  // ids：题目 id 列表。capture 用它反查"用户答的是哪一题"。
  return {text, ids: answers.map(a => a.id.trim())};
};
/** 兼容旧调用：只要文本。 */
const askAnswerText = m => askAnswer(m)?.text ?? null;
const directUser = agent => {
  const out = [];
  for (const m of (agent.session.deriveMessages?.() ?? [])) {
    // ① 真人直接输入的纯文本（原有路径，保持不变）
    if (m.role === 'user' && m.source?.kind === 'user' && m.id) {
      const text = (m.content ?? []).filter(x => x && x.type === 'text')
        .map(x => x.text ?? '').join('\n');
      if (text.trim()) out.push({id: String(m.id), text, source: 'user'});
      continue;
    }
    // ② ask_user_question 的结构化答案（新增路径）
    const asked = askAnswer(m);
    if (asked) {
      out.push({id: String(m.id ?? m.toolCallId ?? ('ask-' + out.length)),
        text: asked.text, source: 'user-question', questionIds: asked.ids,
        toolCallId: m.toolCallId ? String(m.toolCallId) : undefined});
    }
  }
  return out;
};

export function activeSevenStage(agent) {
  const a = actorOf(agent);
  return a ? [...instances].map(f=>f.trackedActor(a)).find(Boolean) ?? null : null;
}

// ══ 【BUG-1 修复】插件【不再自己联网】═══════════════════════════════════════
// 用户口径（2026-11-18）：
//   「bug1是改成由那个外部插件去发布指令让去调用DSH的插件去，将联网搜索外包出去，
//     然后去给DSH联网搜索了之后，再返回会出插件」
//   「不能打破插件现有的大脑和传话员角色的绝对地位」
//
// 旧实现的两个死因（现场：SEARCH_KEY_MISSING + web_search 被门禁拦，双重断路）：
//   ① braveSearch() 要求部署方自备 DSH_SEVEN_STAGE_BRAVE_KEY —— 宿主没配就整条断；
//   ② hostSearch() 要求注入 DSH_SEVEN_STAGE_SEARCH_URL —— 宿主同样没配；
//   ③ 与此同时 knowledge 的 web_search 被 SEVEN_STAGE_TOOL_GATE 关死，
//      于是 knowledge【没有任何联网途径】，step 1.1 直接 failed。
//
// 新架构（外包给传话员，大脑地位不变）：
//   知识 ──action=search──► 插件把【检索工单】落盘（不联网、不读任何 key）
//   真实主对话（传话员）──status 读到工单──► 调宿主内置 web_search 实际检索
//   真实主对话 ──action=search_fill──► 插件收下来源，记进 job.searches
//   知识 ──submit──► 按来源写依据（依据仍由知识判断）
//
// 三条红线（改之前先读）：
//   1. 插件进程内【绝不】发起网络请求 —— 所以在本文件里不要再出现 fetch/API key；
//   2. 只有 root（真实主对话）能 search_fill，knowledge 只能发布工单；
//   3. root 只负责"搬运回填"，不能替知识下结论，也不能改查询词。
// 这样既不依赖宿主配置，也不会把检索权交给被审对象。


export async function apply(ctx, config = {}) {
  const {createUserMessage}=await import('@deepseek-ai/dsh-llm');
  return install(ctx, config, createUserMessage);
}

/**
 * 解析 workflowFile —— **零配置也要能挂载成功**。
 *
 * 背景（2026-11-18 事故）：原来这里只认显式配置，没配就抛
 * SET_ABSOLUTE_SEVEN_STAGE_PATHS，把整个 engineering preset 拖垮，
 * 表现为 webserver: duplicate exact route "/dsh-engineering-ui/agents"。
 *
 * 现在的候选顺序（第一个【真实存在的绝对路径】胜出）：
 *   ① config.workflowFile / DSH_SEVEN_STAGE_WORKFLOW_FILE（部署方显式指定）
 *   ② DSH_STATE_DIR/workflow_state.json（宿主注入的权威状态目录）
 *   ③ <DSH_HOME>/.agent-presets/engineering/tools/workflow_state.json
 *      —— 与 Python 侧 _store.state_dir() 的默认落点完全一致
 *   ④ <USERPROFILE>/.dsh/... 同上（DSH_HOME 没注入时的兜底）
 *   ⑤ 都没有 → 返回 ③ 的路径（不存在的路径也接受）
 *
 * ⑤ 为什么接受一个"暂时不存在"的路径：
 *   首次运行时 workflow_state.json 还没被 workflow_gate init 写出来，
 *   但这时插件必须已经挂上（否则 init 之后还得重启 DSH 才能分析）。
 *   openFlow() 只要求目录存在，会自己 mkdir；状态文件由宿主在首次
 *   attach 时写入。所以"路径确定、文件稍后出现"是正常的启动顺序。
 */
function resolveWorkflowFile(config, nonEmpty) {
  const cands = [];
  cands.push(nonEmpty(config.workflowFile));
  cands.push(nonEmpty(process.env.DSH_SEVEN_STAGE_WORKFLOW_FILE));
  // 家目录三级兜底：DSH_HOME → USERPROFILE/.dsh → os.homedir()/.dsh。
  // 最后一级是必需的：宿主有时在清理过的环境里启动（USERPROFILE 可能为空），
  // 而 homedir() 直接问操作系统，Windows 上恒可用。
  const home = nonEmpty(process.env.DSH_HOME)
    || (nonEmpty(process.env.USERPROFILE) ? join(process.env.USERPROFILE.trim(), '.dsh') : undefined)
    || (() => { try { return join(homedir(), '.dsh'); } catch { return undefined; } })();
  const stateRoot = nonEmpty(process.env.DSH_STATE_DIR);
  if (stateRoot) cands.push(join(stateRoot, 'workflow_state.json'));
  if (home) cands.push(join(home, '.agent-presets', 'engineering', 'tools', 'workflow_state.json'));
  const abs = cands.filter(v => typeof v === 'string' && isAbsolute(v));
  // ⚠️ 【不要】按"文件是否存在"重排候选。
  //   踩过的坑：显式注入的 DSH_STATE_DIR 指向一个还没 init 过的目录（首次运行），
  //   而 ~/.dsh/... 下恰好有上一次任务留下的 workflow_state.json ——
  //   一旦按存在性挑，就会挑到【别人那次任务】的状态文件，
  //   于是 Node 写 A 目录、Python 读 B 目录，凭证永远对不上。
  //   显式注入永远是第一优先，存在的优先级只在同一层内部才有意义
  //   （目前不需要）。这里只保留一个"该路径是否已存在"的诊断标记。
  return abs.length ? abs[0] : undefined;
}

/**
 * 降级挂载：配置不全时【不抛异常、不注册任何钩子】，
 * 只登记一个"永远返回可操作错误"的工具。
 *
 * 为什么必须这么做：install() 抛异常会让整个 engineering preset 组合挂载失败，
 * 宿主重建组合时会把已经 apply 过的其它插件（dsh-engineering-ui）再 apply 一次，
 * 于是 "/dsh-engineering-ui/agents" 这类精确路由重复注册 → 工程模式直接打不开。
 * 一个可选的分析能力不该有这个连坐面。
 */
function installDisabled(ctx, msg, missing, stateDir, workflowFile) {
  const why = [];
  if (missing.includes('stateDir'))
    why.push('stateDir 未配置：请设置绝对路径 DSH_SEVEN_STAGE_STATE_DIR'
      + '（或 DSH_SEVEN_STAGE_WORKFLOW_FILE，会推出同级 seven_stage/）');
  if (missing.includes('workflowFile'))
    why.push('workflowFile 未配置：请设置绝对路径 DSH_SEVEN_STAGE_WORKFLOW_FILE'
      + '（指向当前 profile 的 workflow_state.json）');
  if (missing.includes('hostContract'))
    why.push('宿主契约不完整：本插件需要 tools / agents / subagents 注入'
      + '（tools.register、tools.guard、subagents.startActivation、'
      + 'subagents.prompt、subagents.listDescendants、agents.get）');
  const text = '七阶段分析插件【未启用】。' + why.join('；')
    + '。其余工程模式功能不受影响。';
  try { ctx.logger?.warn?.('[seven-stage] ' + text); } catch {}

  if (typeof ctx.tools?.register !== 'function') return;
  ctx.tools.register({
    name: TOOL,
    description: '七阶段分析调度器（当前未启用：' + (missing.join(', ') || '未配置') + '）。',
    parameters: { type: 'object', additionalProperties: false, required: ['taskId', 'action'],
      properties: {
        taskId: { type: 'string', description: '根任务的 workflow_state.task_id' },
        action: { type: 'string', description: '（当前未启用）' },
        dataJson: { type: 'string', description: '（当前未启用）' },
      } },
    output: { schema: { type: 'object', required: ['text'], additionalProperties: false,
      properties: { text: { type: 'string' } } },
      render: (_args, r) => [{ type: 'text', text: r.text }] },
    isConcurrencySafe: () => false,
    async execute() {
      return { text: JSON.stringify({
        ok: false, code: 'SEVEN_STAGE_NOT_CONFIGURED', error: text,
        missing, stateDir: stateDir ?? null, workflowFile: workflowFile ?? null,
        how: [
          '在启动 DSH 前设置（同一个进程、同一个 profile）：',
          '  $env:DSH_SEVEN_STAGE_WORKFLOW_FILE = "<绝对路径>\\workflow_state.json"',
          '  # 可选：$env:DSH_SEVEN_STAGE_STATE_DIR = "<绝对路径>"（不设则用同级 seven_stage/）',
          '  # 联网：无需配置。插件发布检索工单，由真实主对话用宿主内置 web_search 执行后回填。',
          '然后【完全退出并重开 DSH】。',
        ],
      }) };
    },
  });
}

/** 供回归测试断言 Node 与 Python 解析出的路径逐字相等（不要在生产逻辑里依赖它）。 */
export function resolvePaths(config = {}) {
  const nonEmpty = v => (typeof v === 'string' && v.trim() ? v.trim() : undefined);
  const workflowFile = resolveWorkflowFile(config, nonEmpty);
  const stateDir = nonEmpty(config.stateDir)
    ?? nonEmpty(process.env.DSH_SEVEN_STAGE_STATE_DIR)
    ?? (typeof workflowFile === 'string' && isAbsolute(workflowFile)
        ? join(dirname(workflowFile), 'seven_stage') : undefined);
  return { workflowFile, stateDir,
    workflowFileExists: !!(workflowFile && existsSync(workflowFile)) };
}

export function install(ctx, config, createUserMessage) {
  const msg = text => createUserMessage({
    content:[{type:'text',text}], source:{kind:'plugin:dsh-engineering-seven-stage'}
  });
  // ⚠️ 这里必须用 || 而不是 ??：preset 里空值会以【空串】传进来，
  //   ?? 只挡 null/undefined，空串会穿过去变成"看起来配了、其实没配"。
  const nonEmpty = v => (typeof v === 'string' && v.trim() ? v.trim() : undefined);
  const workflowFile = resolveWorkflowFile(config, nonEmpty);
  // 状态目录的来源顺序必须与 Python 侧 seven_stage_gate.state_dir_for() 逐字一致，
  // 否则会出现"插件写了、门禁读不到"的错配（本仓库反复踩过的坑）：
  //   ① 显式配置（config.stateDir 或 DSH_SEVEN_STAGE_STATE_DIR）
  //   ② dirname(workflowFile)/seven_stage
  // 任务隔离靠文件名 <task_id>.json（task_id 全局唯一）。
  const stateDir = nonEmpty(config.stateDir)
    ?? nonEmpty(process.env.DSH_SEVEN_STAGE_STATE_DIR)
    ?? (typeof workflowFile==='string' && isAbsolute(workflowFile)
        ? join(dirname(workflowFile), 'seven_stage') : undefined);

  // ══ 【2026-11-18 事故修复】装不上时【绝不能抛异常】══════════════════════
  // 现场：DSH 报
  //     webserver: duplicate exact route "/dsh-engineering-ui/agents"
  // 根因不是工程 UI 插件本身，而是【本行】：
  //   环境变量没配 → install() 抛 SET_ABSOLUTE_SEVEN_STAGE_PATHS →
  //     整个 preset 组合挂载失败 → 宿主重建组合 →
  //     已经 apply 过的 dsh-engineering-ui 被【第二次】apply →
  //     ctx.webServer.register(PREFIX + '/agents') 撞上精确路由重名。
  // 也就是说：一个"可选的分析插件"没配置，把整个工程预设连坐了。
  //
  // 正确行为（与本仓库既有红线一致 —— 「failOnStartupError: false」那一类）：
  //   · 配置缺失 → 插件【降级挂载】：不注册任何守卫/钩子/路由，只在被调用时
  //     返回一条可操作的错误。它不再影响 preset 里其它任何一行。
  //   · 宿主契约缺失（tools/subagents/agents 注入不全）→ 同样降级，不抛。
  // 这样"没配置"的代价仅仅是用不了七阶段分析，而不是工程模式打不开。
  const missing = [];
  if (typeof stateDir !== 'string' || !isAbsolute(stateDir)) missing.push('stateDir');
  if (typeof workflowFile !== 'string' || !isAbsolute(workflowFile)) missing.push('workflowFile');
  if (typeof ctx.tools?.register !== 'function' || typeof ctx.tools?.guard !== 'function'
      || typeof ctx.subagents?.startActivation !== 'function'
      || typeof ctx.subagents?.prompt !== 'function'
      || typeof ctx.subagents?.listDescendants !== 'function'
      || typeof ctx.agents?.get !== 'function') missing.push('hostContract');

  if (missing.length) return installDisabled(ctx, msg, missing, stateDir, workflowFile);

  const flow = openFlow(stateDir);
  instances.add(flow);
  const stopTries = new Map();
  const expectedDisposal = new WeakSet();
  const launches = new Set();
  const wakeups = new Map();
  /**
   * ── 【2026-11-19 · 排队消息】同一节点"未消费就不再重复投递" ───────────────
   *
   * 现场（截图）：主对话/子会话侧堆了 **4 条**「七阶段插件任务…有新任务包」
   *   「…检索工单已回填…」，全部卡在队列里逐个 turn 跑。
   *
   * 两个原因叠加：
   *   ① 宿主的 delivery:'queue' → agent.followup()，语义是
   *      "Queue an ordinary follow-up turn... The item becomes the sole ordinary
   *       message of its own turn."（runtime-types.ts:253）
   *      —— 每一条通知都**独占一个完整 turn**，4 条就是 4 轮往返。
   *   ② route() 在每次工具调用后都会跑，靶心 key 里含 phase/jobs/facts 等，
   *      状态一动 key 就变，于是同质内容被反复投递。
   *
   * 但所有通知的实质都是同一件事：**"去调 status 读最新状态"**。
   *   status 读的是磁盘上的当下值，所以**只要一条就够了**，早发的必然过时。
   *
   * 修法（两道）：
   *   ① delivery 'queue' → 'steer'：
   *      "Submit steering for the nearest step. An idle driver starts a turn;
   *       a running driver consumes it at its next step boundary."（同上文件）
   *      —— 插队进当前步，空闲时立刻开 turn，不再排成一串。
   *   ② 每个 (task, sid) 维护一个"未消费"标记：期间新的通知只更新
   *      `pendingText`，等这一轮真正被消费（该角色下次调用工具）才允许再发。
   *      这样同一波状态变化无论触发多少次 route()，对方最多收到一条。
   */
  const inFlight = new Map();     // n -> {text, key}
  const consumedAt = new Map();   // n -> 上次被消费的 key
  const targetPhase = s => [s.revision,s.cursor,s.phase,s.question?.id,
    ...Object.values(s.jobs).map(j=>j.id),Object.keys(s.ended).join(','),
    Object.keys(s.failures).join(','),s.facts.length,Object.keys(s.members).length].join('|');

  /**
   * @param {boolean} [force] 跳过"在途合并"强制投递 —— 只给 deliver（人工显式重发）用。
   *   写成显式形参而不是读 arguments：后者在参数增删时静默失效，是最难查的那种 bug。
   */
  function notify(task, sid, text, key, force = false) {
    if(!sid || !text) return;
    const n = task + ':' + sid;
    if(wakeups.get(n)===key) return;
    // ══ 【2026-11-19 · 会话 c9c2fd56 现场】角色不该"睡死"在一个回合里 ═══════
    // 现场：knowledge/design/review 各调了一次 wait_agent(timeout_ms=600000)。
    //   11:44:58 任务包已经到了它们的 inbox，但 wait_agent 的语义是
    //   "等【下一个 step】的输入"——它只把消息放进 next-turn 队列，
    //   不打断正在进行的这一步。于是三个角色各空等 41 分钟，
    //   直到用户在 12:26 手动掐断。
    //   问题的本质：**插件用"投递消息"驱动角色，而角色用"阻塞等待"驱动自己**，
    //   两套模型对不上。能改的是插件这一侧：明确告诉角色"别等，直接 status 轮询"。
    const message='七阶段插件任务 ' + task + '。' + text
      + ' 请先调用 ' + TOOL + ' action=status 读取当前状态及版本。'
      // 这段每句话都有明确目的：不用 wait_agent、按 next 走、没事就 yield 退出回合。
      + '\n【推进方式】status.next 已经写清这一步该做什么，照做即可；'
      + '有任务包就处理，没有任务包就 action=yield 结束本回合（宿主会在需要时再叫你）。'
      + '⚠️ 不要用 wait_agent 等待任务包 —— 它等的是"下一步的输入"，你的任务包进了队列也不会唤醒它，'
      + '实测会空等到超时。等待的正确姿势是：yield 结束回合，靠插件投递唤醒。';
    // ② 已有一条【已投递但还没被消费】的通知 —— 不再另发。
    //    同一批 route() 里可能对同一个 sid 触发多条（例如 root 同时收到
    //    "工单待回填" 与 "进入补证等待"）：只保留**最后一条**。
    //    为什么保留最后一条而不是拼接：每条通知都以"请先调 status 读当前状态"收尾，
    //    status 永远给最新值，所以旧的那条必然过时 —— 拼进去只会让 prompt 更长。
    const inflight = force ? undefined : inFlight.get(n);
    if (inflight) {
      inflight.text = message;
      inflight.key = key ?? inflight.key;
      wakeups.set(n, key);
      return;
    }
    const s=flow.load(task);
    wakeups.set(n,key);
    const failed=()=>{
      // 投递失败必须清掉 inFlight，否则这个 (task,sid) 会被永久静音。
      inFlight.delete(n);
      if (wakeups.get(n)===key) wakeups.delete(n);
      ctx.logger?.warn?.('[seven-stage] 通知未确认，任务已存。可用 deliver 重发 ' + sid);
    };
    inFlight.set(n, {text:message, key, at:Date.now()});
    try {
      if (sid===s.root) {
        const live=ctx.agents.get(sid);
        if (!live) return failed();
        // root 是真实主对话：steer 会在它下一个 step 边界插入，空闲时直接开 turn。
        // 旧实现用 followup()，每条通知独占一个 turn。
        try { live.steer(msg(message)); }
        catch { live.followup(msg(message)); }
      } else {
        // 使用宿主原生的可续跑投递。delivery:'steer' —— 插队到最近一步，
        // 而不是 'queue' 那样"每条通知排成独立 turn"。
        Promise.resolve(ctx.subagents.prompt({
          requestId:randomUUID(),childSessionId:sid,delivery:'steer',
          content:[{type:'text',text:message}]
        },AbortSignal.timeout(15_000))).catch(failed);
      }
    } catch { failed(); }
  }
  /**
   * 某角色刚刚真的调用了一次工具 → 说明它已经消费掉此前的通知，
   * 解除该 (task,sid) 的在途标记，允许下一波状态变化再投递一条。
   */
  function markNotifiedConsumed(task, sid) {
    if (!sid) return;
    const n = task + ':' + sid;
    if (inFlight.has(n)) { consumedAt.set(n, inFlight.get(n).key); inFlight.delete(n); }
  }
  /**
   * 【BUG-1】检索工单必须真的送到传话员手里。
   *
   * 旧实现里 knowledge 自己联网，所以"发工单"这一步根本不存在。
   * 现在工单由知识发布、由真实主对话执行，如果这里不发通知，
   * 传话员永远不知道有人等它去 web_search —— 那就是新的静默死锁。
   *
   * targetPhase() 只覆盖 jobs / phase / ended / failures / facts 等字段，
   * 不包含 searchOrder，所以工单状态变化要单独算一个 key。
   */
  const searchKey = s => s.jobs.knowledge?.searchOrder
    ? 'order|' + s.jobs.knowledge.searchOrder.id + '|' + s.jobs.knowledge.searchOrder.status
    : '';
  // ══ 【BUG-4 修复】终态通知只能发一次 ═══════════════════════════════════════
  // 现场：任务停在 phase=partial 后，**连续 7 次**收到同一句话
  //   「收到需要真人参与或核对的分析消息。请先调用 status…」，
  //   每次 status 返回逐字节相同，模型被反复唤醒、零可推进。
  //
  // 根因（已用模拟宿主复现，不是猜的）：
  //   targetPhase() 的 key 里含 Object.keys(s.ended).join(',') 与
  //   Object.keys(s.failures).join(',')。终态下每有一个角色补登记 end，
  //   这个 key 就变一次 → 去重失效 → 同一条通知重发一次。
  //   现场四个角色 + root 陆续 end，正好是 6~7 次。
  //   也就是说：不是"无限循环"，是"每次状态推进都重发一遍同样的文案"。
  //
  // 修法（两条一起才够）：
  //   ① 终态单独处理：每个任务只发一条【说清还剩谁、该做什么】的收尾通知，
  //      发过就记在 terminalNotified 里，永不重发；
  //   ② 终态**不再走**下面那串逐条 notify —— 那串是给"分析进行中"设计的，
  //      终态下它只会重复同一句无信息量的话。
  //   非终态的通知逻辑完全不动（那部分是有用的状态推进信号）。
  const terminalNotified = new Set();
  function route(prior, s, sender) {
    if (!s) return;
    // ── 终态：只发一次收尾通知，然后退出 ────────────────────────────────
    if (TERMINAL_PHASES.has(s.phase)) {
      const open = ['knowledge','design','review','planner','root']
        .filter(r => (r==='root'||r==='planner'||s.members[r]) && !s.ended[r] && !s.failures[r]);
      const noticeKey = s.taskId + ':' + s.phase + ':' + open.join(',');
      if (terminalNotified.has(s.taskId)) return;      // 已经说过，绝不重发
      terminalNotified.add(s.taskId);
      // 只有真的需要 root 做事（还有角色没 end / 还在等它取凭证）才发。
      if (s.root !== sender) {
        const tail = open.length
          ? '还差 ' + open.join('、') + ' 登记 end（kind=' + s.phase + '）。'
          : '五个角色都已登记结束，可以用 action=export 取凭证、再 register-design。'
          ;
        notify(s.taskId, s.root,
          '分析已进入终态（phase=' + s.phase + '）。' + tail
          + '这是本任务最后一条自动通知，之后不会重复；需要时你自己调 status 即可。',
          noticeKey);
      }
      // 终态下也给没 end 的角色发一次（同样靠 wakeups 去重，不再逐次重发）
      for (const role of roles) {
        if (open.includes(role) && s.members[role] !== sender) {
          notify(s.taskId, s.members[role],
            '任务已进入终态（phase=' + s.phase + '）。请用 action=end 登记你的结束原因（kind=' + s.phase + '）。',
            noticeKey);
        }
      }
      return;
    }
    // ── 非终态：以下逻辑与修复前完全一致，只是把 ready/partial 从各判断里摘掉
    //    （它们已经在上面 return 了，留着只会误导下一个改代码的人）。
    if (prior && targetPhase(prior)===targetPhase(s)
        && searchKey(prior)===searchKey(s)) return;
    const key = targetPhase(s) + '|' + searchKey(s);
    // ① 有【待回填】的检索工单 → 通知真实主对话（传话员）去用内置 web_search 执行。
    //    【BUG-12】必须先读【磁盘实时状态】，不能用传进来的 s（那是动作开始前的快照）：
    //      现场出现过"已经 filled 的工单被重新推送"，主对话照做一次 web_search +
    //      一次失败的 search_fill，还会误判成自己漏填了。
    const liveNow = flow.load(s.taskId);
    const order = liveNow?.jobs?.knowledge?.searchOrder;
    if (order && order.status === 'pending' && prior?.jobs?.knowledge?.searchOrder?.status !== 'pending'
        && s.root !== sender) {
      notify(s.taskId, s.root,
        '知识发布了一条检索工单：query=' + JSON.stringify(order.query) + '，orderId=' + order.id
        + '，jobId=' + order.jobId + '。请用你的内置 web_search 执行这条查询，'
        + '再把 sources 用 engineering_seven_stage action=search_fill 回填'
        + '（dataJson={jobId,orderId,query,hits:[{title,url,snippet}]}）。'
        + '你只负责搬运结果，不要改查询词、不要替知识下结论。',
        key);
    }
    // ② 工单被回填 → 通知知识回来 submit。
    if (order && order.status === 'filled' && prior?.jobs?.knowledge?.searchOrder?.status === 'pending'
        && s.members.knowledge !== sender) {
      notify(s.taskId, s.members.knowledge,
        '检索工单已回填：' + order.hitCount + ' 条来源（orderId=' + order.id + '）。'
        + '请按来源 ID 提交出处位置、适用边界与缺口。',
        key);
    }
    for (const [role,job] of Object.entries(s.jobs)) {
      if (prior?.jobs[role]?.id===job.id && !(prior?.failures[role] && !s.failures[role])) continue;
      if (!s.failures[role]) notify(s.taskId,s.members[role],'有新任务包。',key);
    }
    // ══ 【2026-11-19 · 报告 BUG-21 / §7.2】planner 的"状态更新"通知必须退避 ══
    // 现场（139 条台账 log、123.7 分钟）：yield 占 **68 次 / 49%**，
    //   其中大量是连续 yield（09:34–09:40 六连、10:00–10:03 五连）。
    //   根因：planner 每 yield 一次，targetPhase 的 key 就变一次（turns 里写了新时间），
    //   于是 route() 认为"状态更新了"→ 通知 planner → 它醒来 → status → 没活 → 再 yield
    //   → key 又变 → 再通知 …… **自我唤醒循环**，每轮 30–60 秒，纯空转。
    //
    // 只在总计有可执行动作或需要介入时唤醒。执行岗位之间直接交接，
    // phase / job 变动本身不需要总计重新读状态、催办、再 yield。
    const phaseChanged = prior?.phase !== s.phase;
    const factsGrew = (s.facts?.length ?? 0) > (prior?.facts?.length ?? 0);
    const beforeFailures = prior?.failures ?? {};
    const failuresAppeared = Object.keys(s.failures ?? {}).some(r => !Object.hasOwn(beforeFailures,r));
    const failuresCleared = Object.keys(beforeFailures).some(r => !Object.hasOwn(s.failures,r));
    const plannerWorkReady = !s.question && ['frame','commit','resolve','evidence_wait'].includes(s.phase)
      && (phaseChanged || prior?.revision !== s.revision || prior?.cursor !== s.cursor
        || prior?.question || failuresCleared);
    if (s.planner !== sender && !s.ended.planner
        && (plannerWorkReady || factsGrew || failuresAppeared)) {
      notify(s.taskId, s.planner,
        '收到岗位回执或流程状态更新（' + [
          phaseChanged ? 'phase ' + prior?.phase + '→' + s.phase : '',
          factsGrew ? '新增真人答复 ' + (s.facts.length - (prior?.facts.length ?? 0)) + ' 条' : '',
          failuresAppeared ? '新增故障登记' : '',
          plannerWorkReady && !phaseChanged ? '总计待办已更新' : '',
        ].filter(Boolean).join('；') + '）。', key);
    }
    if (s.root!==sender && (s.question || s.phase==='evidence_wait' || Object.keys(s.failures).length)) {
      // 文案必须带信息量 + 带【故障原因分类】：
      //   · host-disposed 型（宿主回收会话）插件已自动重派，root 不用管；
      //   · role-blocked 型（角色自己干不下去）需要 planner/root 判断后 retry。
      //   混在一起说"有角色登记了故障"会让人白忙一轮。
      const causes = Object.entries(s.failures).map(([r, f]) => {
        const kind = f?.cause === 'host-disposed' ? '宿主回收会话（已自动重派）'
          : f?.cause === 'run-failed' ? '子代理运行失败'
          : '角色自报阻塞（需人工判断）';
        return r + '：' + kind;
      });
      const why = s.question ? 'planner 提了一个需要真人回答的问题'
        : s.phase === 'evidence_wait' ? '分析进入补证等待（evidence_wait）：' + (s.waitReason ?? '')
        : '故障登记 —— ' + causes.join('；');
      // 【BUG-12】通知前再核一次真实状态：不能拿 prior 快照说事。
      //   现场有"已 filled 的旧工单被重新推给主对话"，主对话白跑一次检索。
      const live = flow.load(s.taskId);
      const liveOrder = live?.jobs?.knowledge?.searchOrder;
      if (liveOrder && liveOrder.status !== 'pending' && /检索工单/.test(why)) return;
      notify(s.taskId,s.root,why + '。处理完请按 status 的 next 继续。',key);
    }
  }
  function currentWorkflow(task) {
    const wf = JSON.parse(readFileSync(workflowFile,'utf8').replace(/^\uFEFF/,''));
    check(wf.task_id===task,'WRONG_DSH_WORKFLOW_TASK');
    check(wf.analysis_engine==='seven_v03' && wf.seven_stage_root_session_id,
      'WORKFLOW_SEVEN_STAGE_NOT_CONFIGURED');
    return wf;
  }
  // ══ 【BUG-seven-stage-tool-gate · P0】═════════════════════════════════════
  // 现场：attach 一成功，真实主对话自己的 pwsh / read / write / grep /
  //   list_agents / todo_write / ask_user_question 全被同一句
  //   SEVEN_STAGE_TOOL_GATE 拒掉 —— 门禁跑不了，"把 bug 写进文件"和
  //   "请人介入"也做不到，只能干等 63 个小步跑完才解锁。
  //
  // 根因：attach 把【真实根会话】登记成 role='root' 成员，
  //   于是 trackedActor(rootActor) 为真，主对话被当成"分析期间会话"一起限制。
  //   而 mode-selection 技能明确承诺「普通根会话与建模小屋不受影响」。
  //
  // 口径（不要改回去）：
  //   · root      —— 真实主对话，**永不限制**。它必须能跑门禁、读写文件、
  //                  向用户提问、读写 todo；它只用插件的 attach / capture / status / end。
  //   · planner / knowledge / design / review —— 分析角色，只能用插件工具。
  //   · unbound   —— 已被 planner 创建但还没 bind 的二级会话，必须先 bind。
  //   · 其它会话（建模小屋、普通根）—— trackedActor 为 null，天然不受影响。
  // ══ 【BUG-3 修复】终态必须解锁分析角色的工具 ═══════════════════════════════
  // 现场：任务进入 phase=partial（已终结、不可能再推进）后，knowledge / design /
  //   review 仍被 SEVEN_STAGE_TOOL_GATE 锁死 —— 它们连 glob / read 都调不了，
  //   磁盘上已有产物也读不回来，"保留部分结果、补证责任与下一步"这条产品承诺落空。
  //
  // 根因：enforce() 只看角色、**不看 phase**。terminal 集合在 flow.js 里早就定义好了
  //   （const terminal = new Set(['ready','partial'])），guard 却没用它。
  //
  // 口径（不要改回去）：
  //   · 非终态（setup / frame / knowledge / preflight / design / review / commit /
  //     resolve / evidence_wait）—— 仍然只许用插件工具。这是"分析期间的隔离"，不能松。
  //   · 终态（ready / partial）—— 解锁。理由：流水线已经停了，此时再锁着只会让人
  //     连"把自己已经做出来的东西落盘"都做不到；解锁后也【不可能】推进任何一步，
  //     因为 flow.call() 那边所有推进动作都要求非终态（ALREADY_TERMINAL / WRONG_PHASE）。
  //     → 解锁的是"读写自己产物的能力"，不是"改变分析结论的能力"。
  //   · 撤销线：插件状态一旦 reopen 回非终态（见 flow.js 的 reopen），这里自动重新上锁。
  const TERMINAL_PHASES = new Set(['ready', 'partial']);
  const isTerminal = st => !!st && TERMINAL_PHASES.has(st.phase);
  // ══ 【BUG-05 修复】只读白名单：这几个工具【不改任何流程状态】，应当放行 ══════
  // 现场：分析角色连 wait_agent / send_message 都被 SEVEN_STAGE_TOOL_GATE 拒，
  //   于是子代理无法主动催办上游，只能被动 yield 等唤醒 —— 整个推进完全依赖
  //   父代理反复轮询，既慢又容易把"没反应"误判成"死了"。
  //
  // 口径：白名单只放【观察 + 通讯】能力，绝不放任何能改状态的工具。
  //   · wait_agent      —— 只是阻塞等待，不写任何东西
  //   · send_message    —— 给已存在的会话发消息，不改变七阶段的 state
  //   · list_agents     —— 只读，用来确认上游是否还活着
  // 明确【不放】的：write / edit / pwsh / run_code / todo_write / 任何门禁命令 ——
  //   能写出去的工具一律不放，否则"分析角色只能读、结论归插件"这条隔离就破了。
  //   todo_write 看着无害，但它会把计划写进会话可见面，容易被当成"已登记的结论"，
  //   与插件里的 records 混淆，所以宁可不放。
  // ══ 【2026-11-19 · 现场 20261119_173044】wait_agent 必须从白名单里摘掉 ══════
  // 现场实测：knowledge 角色调了 10 次 wait_agent(600000)，合计睡眠 70.9 分钟，
  //   其中 **52.1 分钟纯属白等**：
  //     · 17:48:57 / 18:12:12 / 18:23:03 三次，整段时间**没有任何输入**，睡满 600s；
  //     · 17:59:05 / 18:33:26 / 18:43:56 三次，插件的"有新任务包"通知**已经在队列里**
  //       （inbox 记录可见），却照样睡满 600s 才醒。
  //   而前 4 次之所以能醒，全靠 planner 手动 send_message 去戳它。
  //
  // 根因（宿主两个投递语义不是同一个事件）：
  //   · send_message → target='next-step' → 会唤醒 wait_agent；
  //   · 插件 notify（subagents.prompt）→ target='next-turn' → **不会**唤醒 wait_agent。
  //   也就是说：**插件用消息驱动角色，而 wait_agent 等的不是这个消息通道**，
  //   两套模型天生对不上。改投递方式治不了根 —— 它取决于宿主把 prompt 落到哪个队列。
  //
  // 口径（**插件单方面决定，模型无权绕过**）：
  //   分析角色唯一的等待姿势是 **yield 结束回合**：回合结束后插件再投递就会开新回合，
  //   这是插件与角色之间唯一的合法同步点，也是"插件是大脑"这条设计的物理基础。
  //   wait_agent 恰好相反 —— 它把模型锁在回合内，拒绝把控制权交回插件。
  //   所以在分析期间它**不是只读工具，而是对抗调度的工具**，必须禁掉。
  //
  // 为什么不是"写文档让模型别用"：上一轮已经在通知正文里写了"不要用 wait_agent"，
  //   现场照样用了 10 次（工具列在那里，模型就会试）。**能力上不给，比嘴上说更可靠。**
  // ══ 【2026-11-19 · 报告 BUG-2】每个动作的 dataJson 契约（拒绝时随错误一起回）══
  // 为什么单独放一份而不是复用 status 的 actionHelp：
  //   ① actionHelp 只在 status 里出现，而"被拒"的现场往往正是没读 status 的时候；
  //   ② 这份要能按【当前要做的那个动作】精确给出，而不是糊一份大全；
  //   ③ 两处若漂移，以本份为准（它离报错最近，模型一定会看到）。
  // 字段名逐字对应 flow.js 里的 required 列表 —— 改一处必须改两处。
  const ACTION_CONTRACT = {
    frame:{required:['object','decision','boundaries','searchTopic','verification','assumptions','prior'],
      optional:{blockedBy:'[]（每项是依赖名称字符串）'},
      example:{object:'…',decision:'…',boundaries:'…',searchTopic:'…',verification:'…',
        assumptions:'…',prior:'…',blockedBy:[]}},
    submit:{required:['jobId','content'],
      optional:{
        'sources[]（仅 knowledge，数组）':'{id,location,applicability}，id 取自 status.job.searches[].hits[].id',
        'gaps[]（数组）':'{object,impact,obtain,owner,allowed,blocking:true|false}；blocking 必须是布尔值',
        'evidence（仅 design）':'字符串，写计算记录/依据来源',
        'verdict（仅 review）':'pass|conditional|blocked',
        'issues[]（仅 review，数组）':'{key,object,requirement,mechanism,evidence,impact,needed,owner,close,kind}'
          + ' —— 10 个字段【全部必填且都是非空字符串】；kind ∈ hard|unknown|condition|preference|unsupported；'
          + 'owner ∈ knowledge|design|planner|human',
        'closed[]（仅 review，数组）':'{key,evidence} —— 关闭上一轮提过、本轮不再提的异议'},
      example:{jobId:'<status.job.id>',content:'…'}},
    calculate:{required:['jobId','purpose','units','ops'],
      optional:{},
      example:{jobId:'<status.job.id>',purpose:'…',units:'N·m',
        ops:[{op:'mul',a:9550,b:3},{op:'div',a:'$1',b:1450}]}},
    rework:{required:['issueKey','reason'],example:{issueKey:'<issues[].key>',reason:'…'}},
    question:{required:['decision','question','why','unknownPath'],
      example:{decision:'…',question:'…',why:'…',unknownPath:'…'}},
    interpret:{required:['messageId','classification','conflict','changed','impact','reason'],
      optional:{parameters:'任意 JSON（对象/数组/字符串都行）',fromStep:'要回退到的小步 id'},
      example:{messageId:'<capture 回执里的 messageId>',classification:'fact|preference|authorization|estimate|unknown|no_change',
        parameters:'…',conflict:'…',changed:'…',impact:'…',reason:'…',fromStep:'1.1'}},
    search:{required:['jobId','query'],example:{jobId:'<status.job.id>',query:'…'}},
    search_fill:{required:['jobId','orderId','query','hits'],
      optional:{outcome:'ok|unavailable|error|blocked',error:'宿主原始原因'},
      example:{jobId:'<order.jobId>',orderId:'<order.id>',query:'<order.query 原文>',
        hits:[{title:'…',url:'https://…',snippet:'…'}]}},
    commit:{required:['jobId','scope','basis','conditions','outputs'],
      example:{jobId:'<status.current.review.id>',scope:'…',basis:'…',conditions:'…',
        outputs:{'<step.outputs 的每个键>':'…'}}},
    retry:{required:['role','reason'],example:{role:'knowledge|design|review',reason:'…'}},
    blocked:{required:['reason'],example:{reason:'…'}},
    // 【F 节】stop 与 blocked 的差别必须写进契约，否则模型还会拿 blocked 当停止用。
    clearFailed:{required:['reason'],
      optional:{roles:'数组，要清的角色名（knowledge|design|review|planner|root）；不传则清全部',
        role:'单个角色名的简写' },
      note:'只清【已经不适用】的故障登记（旧版本遗留 / 回退过 / 角色已换人），**不派新活**。'
        + '若故障仍然成立、要让它继续干活，请用 retry（retry 才会重派 job）。',
      example:{roles:['planner'], reason:'那条是修复前遗留的 role-blocked，当前已不适用'}},
    stop:{required:['reason'],
      note:'表示【用户/上游叫停】，不是故障、也不是等待：不写 failures，已提交产物全部保留。'
        + '与 blocked（我卡了，要人 retry）、yield（我在等外部回执）是三种不同的事。',
      example:{reason:'用户要求停止本轮分析，先总结已有结果'}},
    // 【P0-1】clean：清掉残留的七阶段状态（僵尸 planner 绑死新任务时用）。
    clean:{optional:{taskId:'要清理的 taskId；不传则只看不删（dry-run）',
      force:'布尔；必须显式 true 才会真的删除状态文件'}},
    yield:{required:['reason'],example:{reason:'…'}},
    partial:{required:['reason'],example:{reason:'…'}},
    end:{required:['kind','reason'],example:{kind:'complete|partial',reason:'…'}},
  };
  /** 取某动作的契约；没有登记的动作返回 undefined（不硬造）。 */
  const actionContract = name => ACTION_CONTRACT[name];

  const READONLY_TOOLS = new Set(['send_message', 'list_agents']);
  // ══ 【2026-11-19 · 提速】把工具【从声明里摘掉】，而不只是拦住执行 ══════════
  //
  // 现场测量（planner 会话，91 个 LLM 步）：
  //   · 每次请求的 tools schema = **29440 字符**（43 个工具）；
  //   · 单步输入 token p50 = **40077**，p90 = 55616，总量 4.04M / 75 次调用；
  //   · 其中 52% 的步是纯协调步（status 22 次 / yield 14 次 / send_message 11 次）。
  // 而分析角色真正能用到的工具只有 4 个（engineering_seven_stage + 三个只读通讯）。
  //   ⚠️ 光靠 guard() 是**不省 token** 的：guard 只在调用时拒绝，
  //      43 个工具的 schema 依然逐字进入每一次请求的上下文。
  // 现在用宿主原生的 tools.restrict()（见 @deepseek-ai/dsh-tools 的 restrict 实现：
  //   它作用在【声明层】，被 restrict 掉的工具不会出现在 view()/schema 里），
  // 把分析角色的可见工具收窄到最小集，schema 从 29.4KB 降到 ~1KB。
  //
  // 为什么用 allow 而不是 deny：deny 要逐条列名，宿主新增工具就会漏；
  //   allow 是白名单，新工具默认【不可见】—— 与"分析角色只能读、结论归插件"的口径一致。
  // 见上面 READONLY_TOOLS 的说明：wait_agent 在分析期间等于"拒绝把控制权交回插件"，
  //   实测吃掉 52 分钟，故从可见工具里一并摘掉（不只是 guard 拦）。
  const ANALYSIS_TOOL_ALLOW = ['engineering_seven_stage', 'send_message', 'list_agents'];
  function enforce(exec) {
    const a = actorOf(exec.agent);
    if (!a) return;
    const bound = flow.trackedActor(a);
    if (!bound || exec.name===TOOL) return;
    if (bound.role === 'root') return;      // ← 【P0 修复】真实主对话放行
    if (isTerminal(bound.s)) return;        // ← 【BUG-3 修复】终态解锁分析角色
    if (READONLY_TOOLS.has(exec.name)) return;  // ← 【BUG-05 修复】只读/通讯放行
    // ══ 【2026-11-19 · 报告 BUG-6】拒绝理由必须给出【转 lead】的处置路径 ══════
    // 现场：owner=planner 的 rv11-loadcase-unreadable 要求修复 gate_load_case.json，
    //   但 planner 会话 glob/read/pwsh/write 全被拒，无法自诊自修，
    //   只能 send_message 转主对话 —— 而错误提示只说"不在本阶段许可内"，
    //   没说"这件事该谁做、怎么交出去"，于是 planner 反复试同类工具。
    //
    // **为什么不给 planner 开只读诊断白名单**：
    //   一旦开了 glob/read，模型就会开始"自己找文件、自己判断内容"，
    //   而它的结论必须来自插件台账、由 review 核销 —— 这条隔离是七阶段成立的前提。
    //   正确的处置路径一直是：分析角色只负责【登记缺口】，修文件是主对话的活。
    return 'SEVEN_STAGE_TOOL_GATE: 分析期间由 engineering_seven_stage 分派任务、搜索、转发和结束。'
      + '当前工具（' + exec.name + '）不在本阶段许可内。\n'
      + '★ 如果你是分析角色（planner / knowledge / design / review）：'
      + '不要自己找文件或改文件 —— 需要外部事实（载荷文件、标准原文、现场数据）时，'
      + '用 action=question 登记给真人，或用 gaps 写一条缺口（owner=human|planner）说明谁补、怎么补；'
      + '插件会把责任落到具体角色。\n'
      + '★ 修文件、跑门禁命令、写盘落档，是【真实主对话（传话员）】的职责：'
      + '用 send_message 发给 lead 说明"要哪个文件、改成什么、为什么"，由它执行。';
  }
  ctx.tools.guard(enforce);
  ctx.on('tools/pre-execute', (exec, next) => {
    const reason = enforce(exec);
    return reason ? {kind:'deny', reason, info:{code:'SEVEN_STAGE_TOOL_GATE'}} : next();
  });

  /**
   * 【提速】给一个分析角色会话【收窄可见工具】。
   *
   * 为什么要单独一步而不是在 enforce() 里顺手做：
   *   enforce() 是"调用时拒绝"，发生在 schema 已经发给模型之后 ——
   *   省不了任何 token。真正的省法是在【声明层】摘掉，即宿主原生的
   *   `agent.ctx.tools.restrict({allow:[...]})`：
   *     实现见 @deepseek-ai/dsh-tools/src/index.ts:restrict()
   *       "Restrict global tools for the calling agent scope... Restrictions
   *        intersect; scoped registrations remain visible."
   *   它按 scope 生效，返回 disposer，并且**只认 agent.ctx**（全局作用域会抛错）。
   *
   * 失败必须静默：restrict 是性能优化，不是正确性依赖。
   *   任何一步拿不到 ctx / 未知工具名，都只是"没省到 token"，
   *   绝不能让七阶段分析因此启动失败 —— 所以整段包在 try/catch 里。
   */
  const restrictedActors = new WeakSet();
  function narrowTools(agentLike) {
    if (!agentLike || restrictedActors.has(agentLike)) return;
    const scoped = agentLike.ctx;
    if (!scoped || typeof scoped.tools?.restrict !== 'function') return;
    try {
      scoped.tools.restrict({ allow: ANALYSIS_TOOL_ALLOW });
      restrictedActors.add(agentLike);
      ctx.logger?.info?.('[seven-stage] 已收窄分析角色工具集到 ' + ANALYSIS_TOOL_ALLOW.length + ' 个');
    } catch (err) {
      // 常见原因：该 scope 下没有这些全局工具名（不同 profile 注册不同）。
      // 记一条 info 即可 —— 行为退回"全部可见 + guard 拦"，仍然正确。
      ctx.logger?.info?.('[seven-stage] 收窄工具集跳过：' + String(err && err.message || err).slice(0, 160));
    }
  }
  /** 按 sessionId 收窄：bind 是角色"正式成为分析角色"的那一刻，收窄也从这里开始。 */
  function narrowBySessionId(sid) {
    if (!sid) return;
    try { narrowTools(ctx.agents.get(sid)); } catch {}
  }

  ctx.tools.register({
    name:TOOL,
    description:'DSH 七阶段分析调度器。真实主对话先创建总计子代理再 attach；总计子代理 launch 启动并绑定知识、设计、反驳。'
      + 'status 只返回你能看到的下一步（含 waitingOn / dispatch / search.order）。'
      + '联网搜索由插件发布检索工单（search），真实主对话用宿主内置 web_search 执行后 search_fill 回填 —— '
      + '插件自身不联网、不需要任何搜索 key。所有提交与暂停、结束均走此工具。',
    parameters:{type:'object',additionalProperties:false,required:['taskId','action'],properties:{
      taskId:{type:'string',description:'根任务的 workflow_state.task_id'},
      // wait_user: 仅真实主对话可用 —— 表示"这一回合是在等用户回答，正常停止"。
      //   它不是结束（结束仍要 end + read_design 复核）。
      action:{type:'string',enum:['attach','launch','bind','status','frame','search','search_fill','calculate','explore','submit','commit','rework',
        'question','capture','interpret','rewind','propose_rooms','partial','yield','wait_user','blocked','stop','retry','reopen','end','read','export','deliver','clean','clearFailed']},
      dataJson:{type:'string',description:'动作参数的 JSON 对象。status 返回所需字段，可按错误修正。'}
    }},
    output:{schema:{type:'object',required:['text'],additionalProperties:false,properties:{text:{type:'string'}}},
      render:(_args,r)=>[{type:'text',text:r.text}]},
    isConcurrencySafe:()=>false,
    // ══ 【BUG-seven-stage-tool-gate · 附带】════════════════════════════════
    // 工具内部每一条 check() 都应当变成【模型能读懂的 JSON 结果】，而不是把
    // 异常冒给宿主 —— 宿主捕获后通常只回一句 "tool failed"，模型看不到
    // REAL_DIRECT_CHILD_REQUIRED 里那段"fork 与 attach 要连着做"的指引，等于白写。
    // 做法：execute 只做异常→{text} 的转换，真实逻辑在 opAction 里。
    async execute(args, exec) {
      try {
        return await opAction(args, exec);
      } catch (err) {
        if (exec && exec.signal && exec.signal.aborted) throw err;
        const raw = String((err && err.message) || err);
        const code = String((err && err.code) || raw.split(':')[0] || 'SEVEN_STAGE_ERROR').trim();
        // ══ 【BUG-2 修复】拒绝时把「你现在能做什么」一并给出 ══════════════════
        // 现场：同一状态下 search / submit / yield / blocked 回了四个互斥错误码
        //   （CURRENT_KNOWLEDGE_JOB_REQUIRED / SEARCH_REQUIRED /
        //     PENDING_JOB_USE_SUBMIT_OR_REPORT_BLOCKER / ACTIVE_JOB_REQUIRED），
        //   调用方无从判断到底哪个状态是真的 —— 因为错误里只有一句裸码。
        // 现在：附上 phase / role / 当前 job / 允许的动作清单，让模型能自救。
        let context = {};
        try {
          const a2 = actorOf(exec && exec.agent);
          const st = a2 ? flow.load(String((args && args.taskId) ?? '')) : null;
          if (st && a2) {
            const bound = flow.trackedActor(a2);
            const role = bound ? bound.role : null;
            context = {
              phase: st.phase, role,
              currentJob: st.jobs?.[role]?.id ?? null,
              searchOrder: st.jobs?.knowledge?.searchOrder ?? null,
              waitingOn: flow.waitingOn(st),
              allowed: role ? flow.allowedActions(st, role) : undefined,
            };
          }
        } catch {}
        // ══ 【2026-11-19 · 报告 BUG-2】被拒时必须附【本动作的 dataJson 契约】════
        // 现场：submit 的完整 schema
        //   {jobId,content,verdict,issues:[{key,object,requirement,mechanism,
        //     evidence,impact,needed,owner,close,kind}],closed:[{key,evidence}]}
        // 只在 actionHelp（status 里）和【首次成功】的响应里出现。
        // 一旦被拒，错误响应只有 allowed —— 于是"错一次就要 back 翻 status 再猜一次"，
        // 实测同一个 submit 被拒 10+ 轮才成功。
        // 现在：把该动作的契约直接挂在错误体上，改错不需要再查任何地方。
        const contract = actionContract(String(args?.action ?? ''));
        // ══ 【2026-11-20 · Bug3】"参数被拒"必须给出【可操作字段】 ══════════════
        // 现场：note 写"按 error 修正后重试"，而 error 体里一个可操作字段都没有
        //   —— 既没有 allowedParts，也没有 expectedParams，调用方只能盲试
        //   15+ 个参数变体，每次都拿不到比上一次更多的信息。
        // 口径：只要异常本身带了结构化线索（allowedParts / forbiddenParts /
        //   expectedParams / pendingRoles / endedRoles / failedRoles），
        //   就原样透出到错误体 —— 这些线索由 flow 层产生（插件裁定），
        //   这里只做搬运，不新增任何判定。
        const HINTS = ['allowedParts','forbiddenParts','expectedParams',
          'pendingRoles','endedRoles','failedRoles',
          // 【2026-11-20】残留/竞争/关闭类错误的结构化线索：
          'blockers','boundPlanner','boundRoot','plannerAlive','howToFix',
          'closableKeys','reopenedKeys',
          // 【2026-11-25】evidence_wait 相关：依赖核销与分类建议
          'blockedBy','suggestedClassifications','factQuestionId','stillBlocked'];
        const hints = {};
        for (const k of HINTS) {
          if (err && err[k] !== undefined) hints[k] = err[k];
        }
        return { text: JSON.stringify({
          ok: false, code,
          error: raw,
          ...context,
          ...hints,
          ...(contract ? { actionContract: contract } : {}),
          note: '按上面的 error 修正后重试；一次拒绝不会改变任何已落盘状态。'
            + (context.allowed ? ' 当前允许的动作见 allowed。' : '')
            + (hints.allowedParts ? ' 本次调用【参数】允许的取值见 allowedParts。' : '')
            + (hints.expectedParams ? ' 期望的参数名见 expectedParams。' : '')
            + (hints.pendingRoles ? ' 还差这些角色没登记结束见 pendingRoles。' : '')
            + (contract ? ' 本动作的 dataJson 契约见 actionContract。' : ''),
        }) };
      }
    }
  });

  // 真实动作体。必须是【外层闭包函数】，不能写成 register 对象的方法：
  //   对象字面量里的 opAction 无法被 execute 按名字引用（ReferenceError，实测踩到）。
  async function opAction({taskId,action,dataJson},exec) {
      const a = actorOf(exec.agent);
      if (exec.agent) expectedDisposal.delete(exec.agent);
      check(a,'HOST_IDENTITY_REQUIRED');
      check(typeof taskId==='string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(taskId),'INVALID_TASK_ID');
      // ══ 【BUG-2 修复】dataJson 的类型错误必须说成"类型错"，不能报"太大" ══════
      // 现场：把 dataJson 当 JSON 对象传（而不是字符串）时报 INPUT_TOO_LARGE。
      // 根因：check(!dataJson || dataJson.length<350_000) —— 对象没有 .length，
      //   undefined < 350000 为 false，于是 !false 成立 → 抛"太大"。
      //   真因（类型错）被完全掩盖，模型按"太大"去删内容，永远修不好。
      // 同一处的 JSON.parse 也会把数组/坏字符串的原始异常直接冒给宿主。
      // 现在：三种情况分别给【准确的码 + 可照着改的示例】。
      if (dataJson !== undefined && dataJson !== null && typeof dataJson !== 'string') {
        const e = new Error('DATAJSON_MUST_BE_STRING: 收到的是 '
          + (Array.isArray(dataJson) ? 'array' : typeof dataJson)
          + '，但 dataJson 必须是【JSON 字符串】。'
          + '正确写法：dataJson=\'{"jobId":"<jobId>","query":"<查询词>"}\''
          + '（整段是一个字符串；别直接传对象，宿主不会替你序列化）。');
        e.code = 'DATAJSON_MUST_BE_STRING';
        throw e;
      }
      if (typeof dataJson === 'string' && dataJson.length>=350_000) throw Error('INPUT_TOO_LARGE');
      let data = {};
      if (dataJson) {
        try { data = JSON.parse(dataJson); }
        catch (err) {
          const e = new Error('DATAJSON_INVALID_JSON: dataJson 不是合法 JSON（'
            + String(err && err.message || err) + '）。'
            + '检查引号/逗号；示例：\'{"jobId":"abc","query":"GB/T 1096"}\'。');
          e.code = 'DATAJSON_INVALID_JSON';
          throw e;
        }
      }
      check(data && typeof data==='object' && !Array.isArray(data),
        'INVALID_PAYLOAD: dataJson 解析出来必须是对象（{}），不能是数组或标量。');
      exec.signal.throwIfAborted();
      // 【2026-11-19 · 排队消息】任何角色只要真的动了一次工具，就说明它已经
      //   消化掉前一条"去 status"的通知。在这里解除在途标记，允许下一波再投一条。
      //   位置必须在最前面：即使这次调用会因为别的 check 被拒，也证明它醒了。
      markNotifiedConsumed(taskId, a.id);
      // ══ 【2026-11-20 · P0】clean 必须抢在所有"绑定校验"之前 ═══════════════
      // 理由：它要处理的正是"绑定已经烂掉"的现场 —— 僵尸 planner 让
      //   trackedActor 指到一个与 taskId 不符的任务上，WRONG_BOUND_TASK 会先把
      //   清理入口挡死，于是又回到"只能手工删文件"。
      // 权限不在这里判：flow.clean 内部要求调用者是该任务的 root 或 planner。
      if (action==='clean') return {text:JSON.stringify(flow.clean(a, taskId, data))};
      const prior = flow.load(taskId);
      const bound = flow.trackedActor(a);
      check(!bound || bound.s.taskId===taskId,'WRONG_BOUND_TASK');
      if (!['status','read','export','clean'].includes(action)) {
        // ══ 【2026-11-19 · 会话 c9c2fd56 现场】错误码必须能自解释 ═══════════════
        // 现场：planner 在 attach 落地前就 launch → 拿到裸 WRONG_WORKFLOW_ROOT。
        //   这个码的字面意思是"工作流根会话不对"，而真实原因是【任务还没 attach】。
        //   结果 planner 把它理解成"身份不匹配/不可恢复"，等了 4 分钟才向主对话报告。
        // 现在分两种：
        //   · 任务状态文件还不存在 → TASK_NOT_ATTACHED（并说明"先让主对话 attach"）；
        //   · 存在但根会话对不上 → WRONG_WORKFLOW_ROOT（并给出两边的值）。
        let wf = null;
        try { wf = currentWorkflow(taskId); }
        catch (err) {
          const e = new Error('WORKFLOW_STATE_UNREADABLE: 读不到门禁的 workflow_state。'
            + 'taskId=' + JSON.stringify(taskId) + '；原因：' + String(err && err.message || err).slice(0,200)
            + '。先确认 workflow_gate.py init 已经跑过、且 taskId 与它一致。');
          e.code = 'WORKFLOW_STATE_UNREADABLE';
          throw e;
        }
        // attach 本身就是在【创建】状态文件，所以它必须豁免这个判据。
        if (!prior && action !== 'attach') {
          const e = new Error('TASK_NOT_ATTACHED: 任务 ' + JSON.stringify(taskId)
            + ' 还没有被 attach —— 七阶段状态文件不存在。'
            + '\n谁来做：只有【真实主对话】能 attach。'
            + '\n顺序：主对话 subagent_fork 建总计子代理 → 主对话 engineering_seven_stage '
            + 'action=attach {planner:<子代理 sessionId>, messageIds?} → 总计子代理再 action=launch。'
            + '\n当前调用的是 action=' + JSON.stringify(action)
            + '，说明 attach 还没落地（或落到了别的 taskId）。等主对话 attach 完成后再来。');
          e.code = 'TASK_NOT_ATTACHED';
          throw e;
        }
        if (prior && wf.seven_stage_root_session_id !== prior.root) {
          const e = new Error('WRONG_WORKFLOW_ROOT: workflow_state.seven_stage_root_session_id='
            + JSON.stringify(wf.seven_stage_root_session_id) + '，而本任务状态的 root='
            + JSON.stringify(prior.root) + '，两者必须一致。'
            + '\n通常是「同一台机器上跑了两个任务」：workflow_state 已被新任务覆盖，'
            + '而旧任务的状态文件还在。处理：确认 taskId 是否取错了；'
            + '确实要换任务就让主对话重新 workflow_gate.py init 并重新 attach。');
          e.code = 'WRONG_WORKFLOW_ROOT';
          throw e;
        }
      }
      let result;
      if (action==='attach') {
        check(a.parent===null, 'ROOT_REQUIRED');
        // ══ 【BUG-7-B】模板指纹漂移要在【开工前】就报，不能等跑一半 ══════════
        // steps.json 的 sha256 字段与现算值不一致，说明有人改了模板却没重算指纹，
        // 或者对应关系被手工改坏。此时开工的后果是：63 步跑完，read_design 却因为
        // specHash 对不上而拒掉全部记录 —— 白干一场。所以在这里 fail-fast。
        check(!specHashDrift,
          'SPEC_HASH_DRIFT: steps.json 的 sha256 字段与现算指纹不一致。'
          + '说明模板被改过但没重算指纹（或两边算法不同源）。'
          + '修法：让 flow.js 的 computeSpecHash() 重算并把结果写回 steps.json 的 sha256，'
          + '同时同步 seven_stage_gate._SPEC_HASH。'
          + '不修就开工，63 步跑完也会因为 specHash 不匹配而无法登记交付。');
        // ══ 【BUG-seven-stage-tool-gate · P1】═════════════════════════════════
        // 现场：subagent_fork 建的总计子代理在 attach 落地【之前】就自己结束了。
        //   此时 ctx.agents.get(planner) 已查不到它 → 原来这一行必然抛
        //   REAL_DIRECT_CHILD_REQUIRED，而且【不可恢复】：只能重新 fork。
        //   实测就是换了个 planner2 才成功。
        //
        // 两道修复：
        //   ① 只要宿主还能证明"这是我真实创建过的直系子会话"，就允许 attach。
        //      证据按可信度排序：
        //        · planner 此刻仍活着且 parent === root（原路径，保留）；
        //        · ctx.subagents.listDescendants(root) 里仍有这条 kind=child 记录
        //          —— 子代理结束回合后 agents 会消失，但谱系仍在。
        //      两者都拿不到证据 → 照旧拒绝（不采信模型自报）。
        //   ② 报错改成【可操作的】，直接说明"fork 与 attach 必须连着做"。
        // ══ 【BUG-6 修复】planner 必须是 sessionId，不是 subagent_fork 的显示名 ═══
        // 现场：传 dataJson='{"planner":"agent-1"}'（subagent_fork 返回的显示名）
        //   → REAL_DIRECT_CHILD_REQUIRED，报错里【一个字都没提】要填 sessionId。
        //   实测是靠翻 ~/.dsh/sessions/ 的 mtime 才猜出正确 UUID，卡了很多轮。
        //
        // 两道修：
        //   ① 报错文本明确写"要 sessionId（UUID）"，并给出获取方法；
        //   ② 传进来不是 UUID 时，先尝试在直系子会话里【按显示名唯一匹配】自动解析。
        //      只有"恰好匹配到一个"才采用 —— 多个同名就报错让人选，绝不瞎猜。
        const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        let plannerId = typeof data.planner === 'string' ? data.planner.trim() : '';
        let plannerNote = '';
        let kids = null;
        if (typeof ctx.subagents?.listDescendants === 'function') {
          try { kids = await ctx.subagents.listDescendants(a.id, exec.signal); } catch { kids = null; }
        }
        if (plannerId && !uuidRe.test(plannerId) && Array.isArray(kids)) {
          const byLabel = kids.filter(x => x && x.kind === 'child'
            && (String(x.label ?? '') === plannerId || String(x.name ?? '') === plannerId));
          if (byLabel.length === 1) {
            plannerNote = '（已按显示名 "' + plannerId + '" 自动解析为 sessionId '
              + byLabel[0].id + '）';
            plannerId = String(byLabel[0].id);
          } else if (byLabel.length > 1) {
            const e = new Error('PLANNER_NAME_AMBIGUOUS: 显示名 "' + plannerId.trim()
              + '" 对应了 ' + byLabel.length + ' 个直系子会话（'
              + byLabel.map(x => x.id).join('、') + '）。'
              + '请改用 sessionId 精确指定。');
            e.code = 'PLANNER_NAME_AMBIGUOUS';
            throw e;
          }
        }
        let realChild = false;
        if (plannerId) {
          const live = actorOf(ctx.agents.get(plannerId));
          if (live && live.parent === a.id) realChild = true;
          if (!realChild && Array.isArray(kids)) {
            realChild = kids.some(x => x && x.kind === 'child' && String(x.id) === plannerId);
          }
        }
        if (!realChild) {
          const e = new Error('REAL_DIRECT_CHILD_REQUIRED: planner 必须是你刚刚用 '
            + 'subagent_fork 创建、且【仍在等 attach】的直系子会话。'
            + '\n\n'
            + '★ 传什么：planner 字段要填【子代理的 sessionId】，'
            + '形如 16713df1-09d5-4754-b153-ed86d97dfb4c（36 位 UUID）。'
            + '【不要】填 subagent_fork 返回的显示名（如 agent-1）—— '
            + '除非它恰好能唯一匹配到一个子会话（插件会尝试自动解析，失败就报这个错）。'
            + '\n取 sessionId 的方法：① 调 list_agents 看该子代理的 id；'
            + '② 或看 ~/.dsh/sessions/ 下最新目录名。'
            + '\n\n'
            + '最常见的另一个失败：子代理在 attach 落地之前就自己结束了回合。'
            + '正确做法 —— 把 subagent_fork 与 engineering_seven_stage(action=attach) '
            + '写在【同一条回复】里连续执行，并在它的初始 prompt 里明写'
            + '「收到 attach 通知前不得结束回合」。'
            + '如果它已经结束，请重新 fork 一个新的子代理再 attach。'
            + (plannerId ? '\n\n本次收到的 planner = "' + plannerId + '"'
              + (uuidRe.test(plannerId) ? '（形状是 UUID，但宿主查不到这个会话）'
                                        : '（不是 UUID，且没能在直系子会话里唯一匹配到）')
              : '\n\n本次没有收到 planner 字段。'));
          e.code = 'REAL_DIRECT_CHILD_REQUIRED';
          throw e;
        }
        const original = directUser(exec.agent);
        // ══ 【2026-11-19 · 会话 c9c2fd56 现场】messageIds:[] 把 attach 逼成猜谜 ══════
        // 现场：主对话按 preset/SKILL 的模板原样传 dataJson={"planner":"...","messageIds":[]}，
        //   wanted=[] → requirements=[] → check 失败 → ROOT_USER_MESSAGE_NOT_AVAILABLE。
        //   而这个错误【一个字都没说要传什么 id】，主对话只能去 grep 源码、翻了 15 个回合。
        // 现在：
        //   · messageIds 为空数组（或只含无效项）→ 退回"最近一条真人原文"，不再直接死；
        //   · 仍然找不到 → 报错里列出【可选的 messageId 清单】（id + 60 字预览），
        //     并明确写清"不传 messageIds 时取最近一条"。
        //   注意：只有"有没有指定"影响选哪条，不改变"必须是真人来源"这个准入条件。
        const wanted = (Array.isArray(data.messageIds) ? data.messageIds : [])
          .map(x => compact(x)).filter(Boolean);
        const pickIds = wanted.length ? wanted : original.slice(-1).map(m=>m.id);
        const requirements = pickIds.map(id=>original.find(m=>m.id===id)).filter(Boolean);
        if (!requirements.length) {
          const seen = original.slice(-8).map(m => ({id:m.id, source:m.source,
            ...(m.questionIds ? {questionIds:m.questionIds} : {}),
            preview:String(m.text).slice(0,60)}));
          const e = new Error('ROOT_USER_MESSAGE_NOT_AVAILABLE: attach 需要一条【真人来源】'
            + '的原始需求消息，但没找到符合条件的。'
            + (wanted.length ? '\n你指定的 messageIds=' + JSON.stringify(wanted) + ' 都不在可采集记录里。' : '')
            + '\n最近 8 条可采集记录（id 就是 messageIds 要填的值）：'
            + JSON.stringify(seen)
            + '\n不传 messageIds（或传空数组）时，插件会自动取【最近一条】真人原文。'
            + '\n（若列表为空：用户消息尚未到达本会话，或本回合还没有真人输入。）');
          e.code = 'ROOT_USER_MESSAGE_NOT_AVAILABLE';
          throw e;
        }
        // ⚠️ 必须用【解析后】的 plannerId（BUG-6：允许按显示名唯一解析），
        //   否则状态文件里会记下 "agent-1" 这种名字，之后 actorRole() 永远匹配不上。
        // 【附带修复】把门禁落盘的载荷工况路径一起交给状态机（workPacket 会读它，
        // 让分析角色不必开 shell 也能拿到额定载荷/材料/安全系数）。
        // 只读取、不修改：数值权威仍是 workflow_gate 写的那个文件。
        let loadCasePath = null;
        try {
          const wf = currentWorkflow(taskId);
          loadCasePath = typeof wf.load_case_file === 'string' ? wf.load_case_file : null;
        } catch {}
        // ══ 【2026-11-20 · P0】把"哪些会话还活着"作为插件判定的输入 ══════════
        // attach 需要回答"旧 planner 是不是已经不在了"。这个判断**不能**由模型自报，
        // 只能由宿主证明。做法：把工作流根会话下所有仍能被 ctx.agents 查到的
        // 子会话 id 收集起来，交给 flow 去比对旧 planner。
        // 注意：这只是"输入证据"，判定仍在 flow 里（插件是大脑这条口径不变）。
        // ⚠️ 只有"确实查得到宿主会话表"时才给出证据 —— 查不到就传 undefined，
        //   让 flow 走"无法确认 ⇒ 保守不接管"那条分支。
        //   区分"查过了、它不在"（[]，可以接管）与"没法查"（undefined，不许接管），
        //   这两个含义相反，绝不能混成一个空数组。
        let livePlanners;
        try {
          const oldPl = prior ? prior.planner : null;
          if (oldPl && ctx.agents && typeof ctx.agents.get === 'function') {
            const live = actorOf(ctx.agents.get(oldPl));
            livePlanners = (live && live.parent === a.id) ? [String(oldPl)] : [];
          }
        } catch { livePlanners = undefined; }
        result = flow.call(a,taskId,'attach',
          {planner:plannerId, requirements, loadCasePath, livePlanners});
        if (plannerNote) result.plannerNote = plannerNote;
        if (!prior) notify(taskId,plannerId,'已绑定为需求与规划。调用 launch 创建三个工作角色。','attach');
      } else if (action==='launch') {
        // ══ 【2026-11-20 · P0】launch 必须认人：只有【当前绑定的那个 planner】能 launch ══
        // 现场（BUG测试结果_20261120 BUG-3）：连开 3 个总计子代理，只有最后一个
        //   attach 成功，前几个仍挂在平台 —— 它们照样能发动作指令。
        // 旧判据只查 bound.role==='planner'，而 trackedActor 是按"谁的 parent 是 planner"
        //   找角色，于是**任何一个** planner 的子会话都可能被认成 analyst，
        //   多个大脑同时推进同一份状态。
        // 口径：launch / frame / commit 等"大脑动作"必须核对【就是状态里记的那一个】。
        //   actorRole 已经按 s.planner === a.id 判定，这里再显式兜一层，
        //   并把"你不是被绑定的那个 planner"说清楚，避免模型以为是阶段问题。
        if (bound?.role === 'planner' && prior && String(a.id) !== String(prior.planner)) {
          const e = new Error('PLANNER_SETUP_REQUIRED: 你（session ' + JSON.stringify(a.id)
            + '）不是本任务绑定的那个总计子代理（绑定的是 '
            + JSON.stringify(prior.planner) + '）。'
            + '同一个 taskId 只允许一个大脑。'
            + '\n如果你是新 fork 的替代 planner：先让主对话 action=attach {planner:<你的 sessionId>} '
            + '（旧 planner 已失效时插件会自动接管），再 launch。'
            + '\n如果你是误开的第二个：直接结束本会话，不要对同一 taskId 发动作。');
          e.code = 'PLANNER_SETUP_REQUIRED';
          e.boundPlanner = prior.planner;
          e.yourSessionId = String(a.id);
          e.howToFix = ['主对话 action=attach {planner:<正确 sessionId>}',
            '结束这个多余的子代理'];
          throw e;
        }
        check(bound?.role==='planner' && prior?.phase==='setup','PLANNER_SETUP_REQUIRED');
        check(!launches.has(taskId),'LAUNCH_IN_PROGRESS');
        const provider = config.workerProvider ?? 'fork';
        check(ctx.subagents.getProvider(provider),'WORKER_PROVIDER_UNAVAILABLE');
        launches.add(taskId);
        try {
          const {invitations} = flow.call(a,taskId,'invite');
          for (const {role,prompt} of invitations) {
            if (flow.load(taskId).childForRole[role]) continue;
            exec.signal.throwIfAborted();
            const descendants=await ctx.subagents.listDescendants(prior.root,exec.signal);
            const known=new Set([a.id,...Object.values(flow.load(taskId).childForRole)]);
            // ══ 【2026-11-20 · P0】OTHER_ROOMS_ACTIVE 必须点名"谁在挡" ══════════
            // 现场（BUG测试结果_20261120 BUG-1）：launch 报"其他房间未排空"，
            //   而 room-board --json 显示 rooms 为空 —— 因为这里挡的是**活跃会话**，
            //   不是建模房间。两套词汇混用，排查方向被带偏；错误里又没有任何
            //   "哪个 session / 怎么处置"的信息，只能人工翻 list_agents。
            //
            // 口径：把挡路的会话逐个列出来（id + label + activity），并给两条出路：
            //   · 让它们结束（分析角色应等 launch；若是陈旧会话，用 clean 清残留）；
            //   · 若确认它们与本任务无关且已 inactive，本判据本来就会放行。
            const blockers = descendants
              .filter(x=>x.kind==='child' && x.activity!=='inactive')
              .filter(x=>!known.has(String(x.id)));
            if (blockers.length) {
              const e = new Error('OTHER_ROOMS_ACTIVE_DRAIN_BEFORE_ANALYSIS: 分析开始前还有 '
                + blockers.length + ' 个活跃子会话不是本任务的成员，先把它们排空。'
                + '\n⚠️ 注意：这条判据看的是【活跃会话】，不是建模房间 —— '
                + 'room-board 显示 rooms 为空并不代表这里会放行。'
                + '\n挡路的会话（sessionId / label / activity）：'
                + JSON.stringify(blockers.map(x=>({
                    sessionId:String(x.id), label:x.label ?? null,
                    activity:x.activity ?? null})))
                + '\n处置（按顺序试）：'
                + '\n  ① 如果它们是本任务上一轮遗留的总计子代理：确认已结束后重新 launch；'
                + '\n  ② 如果它们是别的任务的会话：等它们结束，或先在那边收尾；'
                + '\n  ③ 残留状态清了还挡：action=clean {dryRun 看目录} → '
                + 'action=clean {taskId:"<本任务>", force:true}，再重新 attach + launch。');
              e.code = 'OTHER_ROOMS_ACTIVE_DRAIN_BEFORE_ANALYSIS';
              e.blockers = blockers.map(x=>({sessionId:String(x.id),
                label:x.label ?? null, activity:x.activity ?? null}));
              e.howToFix = ['等这些会话结束', 'action=clean {taskId, force:true} 后重新 attach',
                '或在对应任务里收尾'];
              throw e;
            }
            const childId = randomUUID();
            flow.reserveWorker(a,taskId,role,childId);
            try {
              const activation=await ctx.subagents.startActivation({
                provider,label:'七阶段分析 '+role,childId,parent:exec.agent,maxDepth:2,
                prompt:[{type:'text',text:prompt}],signal:exec.signal,delivery:'parent'
              });
              check(activation.childId===childId,'HOST_CHILD_ID_MISMATCH');
              Promise.resolve(activation.result).catch(()=>{
                const child=ctx.agents.get(childId);
                if (child && flow.load(taskId)?.members[role]===childId) {
                  flow.interrupt(actorOf(child),taskId,'宿主子代理运行失败');
                }
              });
            } catch (err) {
              flow.interrupt(a,taskId,'二级代理 '+role+' 启动未确认，保留分配 ID，防止重复创建。');
              throw err;
            }
          }
          result = flow.call(a,taskId,'status');
        } finally { launches.delete(taskId); }
      } else if (action==='deliver') {
        check(['root','planner'].includes(bound?.role),'RELAY_ACCESS_DENIED');
        const role=data.role;
        check(role==='planner' || roles.includes(role),'RELAY_ROLE_INVALID');
        const recipient=role==='planner' ? prior.planner : prior.members[role];
        check(recipient && !prior.ended[role], 'RELAY_ROLE_UNAVAILABLE');
        check(role==='planner' || prior.jobs[role] || ['ready','partial'].includes(prior.phase),
          'NO_PENDING_WORK_TO_RELAY');
        // deliver 是**人工显式重发**（有人主动想再催一次），必须绕过"在途合并"，
        // 否则用户点了一次没反应，会以为 deliver 坏了。
        notify(taskId,recipient,'重读待办和已落盘的消息。',targetPhase(prior), true);
        result={ok:true,note:'已安排通知，是否接收以会话读取到任务并提交为准。'};
      } else if (action==='search') {
        // ① 知识：只发布检索工单，自己**不联网**。
        const query = compact(data.query);
        check(query.length>=3 && query.length<=240 && !/[\r\n]/.test(query),'PUBLIC_SEARCH_QUERY_REQUIRED');
        const job = flow.searchJob(a,taskId,data.jobId).jobs.knowledge;
        check(!job.searches.some(x=>x.query===query),'SEARCH_LIMIT_OR_DUPLICATE');
        const order = flow.orderSearch(a,taskId,data.jobId,query);
        result = {order, status:'pending',
          next:'检索工单已发布。真实主对话（传话员）会用宿主内置 web_search 执行本条查询，'
            + '然后 search_fill 回填来源；你先做本步其它准备，等 status.search.order.status 变成 filled 再 submit。'
            + '不要自己构造来源。'};
      } else if (action==='search_fill') {
        // ③ 传话员（真实主对话）：把宿主内置 web_search 的结果搬回插件。
        //   权限校验在 flow.fillSearch 里（非 root 一律 SEARCH_FILL_RELAY_ONLY），
        //   这样"谁出的证据"在数据层就被锁死，不靠调用方自觉。
        const record = flow.fillSearch(a,taskId,{
          jobId:data.jobId, orderId:data.orderId, query:data.query,
          hits:data.hits, provider:data.provider ?? '宿主内置 web_search',
          // 【BUG-1 补强】三态区分：ok / unavailable / error / blocked。
          // 不传视为 ok；搜不了就明确说 unavailable 并把宿主原因写进 error。
          outcome:data.outcome, error:data.error});
        result = {search:record,
          next: record.outcome === 'ok'
            ? '来源已回填（' + record.hits.length + ' 条）。知识会按来源 ID 提交 submit。'
            : '已记录一次【未成功】的检索（outcome=' + record.outcome + '，'
              + 'error=' + JSON.stringify(record.error ?? '') + '）。'
              + '知识若在本步拿不到来源，可在 gaps 里登记 blocking 缺口后提交；'
              + '但这类步骤无法通过最终交付门（read_design 要求每步都有真实来源）。'};
      } else if (action==='capture') {
        check(bound?.role==='root','ROOT_REQUIRED');
        const all = directUser(exec.agent);
        // 【2026-11-19】三种 id 都认：消息 id / ask_user_question 的题目 id / toolCallId。
        //   现场主对话手里只有题目 id（"rated_duty"），旧实现只比消息 id → 永远找不到，
        //   而错误提示里给出的 id 是另一种形态，模型只会再猜一次。
        const wanted = compact(data.messageId ?? data.recordId ?? data.questionId);
        const record = all.findLast(m=>!wanted || m.id===wanted
          || m.toolCallId===wanted || (m.questionIds ?? []).includes(wanted));
        if (!record) {
          // 【BUG-02】采集失败时必须说清"我到底看到了什么"，
          // 而不是一句 ROOT_USER_MESSAGE_NOT_AVAILABLE 让人干猜。
          const seen = all.slice(-5).map(m => ({id:m.id, source:m.source,
            ...(m.questionIds ? {questionIds:m.questionIds} : {}),
            preview:String(m.text).slice(0,60)}));
          const e = new Error('ROOT_USER_MESSAGE_NOT_AVAILABLE: 在会话里找不到符合条件的真人来源消息。'
            + (wanted ? '指定的 messageId=' + JSON.stringify(wanted)
              + ' 既不是消息 id，也不是 ask_user_question 的题目 id。' : '')
            + '\n能作为真人来源的只有两类：'
            + '① role=user 且 source.kind=user 的纯文本；'
            + '② ask_user_question 返回的 {answers:[{id,selected,custom}]} 结构（形如 tool result）。'
            + '\n最近 5 条可采集记录（id=消息 id，questionIds=题目 id，两种都能直接传给 capture）：'
            + JSON.stringify(seen)
            + '\n（若为空，说明用户答复尚未到达本会话，或答复用了别的提问方式。）');
          e.code = 'ROOT_USER_MESSAGE_NOT_AVAILABLE';
          throw e;
        }
        result = flow.call(a,taskId,'capture',{record});
        // ══ 【2026-11-19 · 现场 20261119_173044】capture 之后必须【主动叫醒】planner ══
        // 现场：root capture 成功、回执里也写了 messageId，但 planner 不知道；
        //   root 只好自己再 send_message 转一遍（17:45、18:03、18:37 各一次），
        //   而这条消息到达 planner 后又要走一轮完整的 LLM 往返。
        //   evidence_wait 期间每多绕一圈就是好几分钟 —— 因为 planner 此刻
        //   很可能正在 yield 后的空闲态，没有人叫它就没人推进。
        //
        // 为什么不是"让 root 再调一次 interpret"：interpret 是**判断**
        //   （给原话定性、决定回退到哪一小步），归总计子代理，不能下放给传话员。
        //   传话员的职责到 capture 为止 —— 所以正确的做法是 capture 一落库，
        //   由插件自己去叫大脑，而不是让传话员兼职跑腿。
        //
        // 只对**新捕获**发通知（幂等返回不重复叫）：重复 capture 同一条消息，
        //   说明 planner 早就在处理了，再叫只会制造无谓的往返。
        try {
          if (!result.idempotent) {
            const sAfter = flow.load(taskId);
            if (sAfter?.planner) {
              notify(taskId, sAfter.planner,
                '真人答复已捕获（messageId=' + (result.messageId ?? '<见 capture 回执>')
                + '）。请 action=interpret 给它定性，并按 classification 决定是否回退受影响小步。',
                'capture:' + (result.messageId ?? '') + '|' + (sAfter.revision ?? ''));
            }
          }
        } catch (err) {
          // 通知失败不能把 capture 的成功结果吞掉 —— 事实已经落库了。
          ctx.logger?.warn?.('[seven-stage] capture 后通知 planner 失败：'
            + String(err && err.message || err).slice(0, 160));
        }
      } else if (action==='partial') {
        // 真人的原话来自会话。模型填写的布尔值不能赋予结束授权。
        const last = directUser(exec.agent).at(-1);
        const explicitStop = bound?.role==='root' && last && /^\s*(停止分析|终止分析|结束分析|stop analysis)[。.!！\s]*$/i.test(last.text);
        result = flow.call(a,taskId,'partial',{reason:data.reason,hostStop:explicitStop===true});
      } else {
        result = flow.call(a,taskId,action,data);
      }
      // ══ 【2026-11-19 · 提速】bind 成功的下一刻就收窄该角色的可见工具 ══════
      //   为什么放在这里而不是 enforce() 里：restrict 作用在【声明层】，
      //   必须在角色开始真正干活之前生效，才能让后续每一次请求都少带 ~28KB schema。
      //   bind 是角色"正式成立"的唯一时刻，也是最早的、能安全拿到 agent.ctx 的时间点。
      if (action==='bind') narrowTools(exec.agent);
      if (action==='launch') {
        // launch 会一次性建出三个角色会话；这里顺手收窄，避免第一个任务包
        // 在 bind 通知到达之前又带着全量 schema 跑一轮。
        const live = flow.load(taskId);
        if (live) { for (const sid of Object.values(live.members ?? {})) narrowBySessionId(sid); }
      }
      const s = flow.load(taskId);
      if (action==='export' && result.result) {
        const {records, history, log, ...manifest} = result.result;
        result = {manifest, recordIndex:records.map(r=>({step:r.step,scope:r.scope,revision:r.revision})),
          final:records.at(-1)?.step==='7.12' ? records.at(-1).outputs : null,
          note:'完整记录由交接接口导出，各小步可用 read 查询。'};
      }
      route(prior,s,a.id);
      if (action==='yield') {
        // 详细任务包由下一次真正唤醒后的 status 提供。等待回执保留状态和审计定位字段。
        const {taskId,role,phase,revision,waitingOn,waitReason,progress} = result;
        const step = result.current?.step;
        return {text:JSON.stringify({ok:true,taskId,role,phase,revision,waitingOn,waitReason,progress,
          current:{step:typeof step==='string' ? step : step?.id ?? null},
          next:'本回合等待已登记，结束本回合，等插件派发新任务。'})};
      }
      return {text:JSON.stringify(result)};
  }

  ctx.on('agent/turn-stopping', ({agent,signal}) => {
    const a = actorOf(agent);
    const bound = a && flow.trackedActor(a);
    if (!bound) return;
    const {s,role} = bound;
    // 【P0 附带】root 是用户的真实主对话：结束一个回合是【正常交互】。
    //   原实现会 steer 它"结束请求未登记"，两次之后直接 flow.interrupt(root)
    //   把 root 记进 failures —— 用户只是正常回一句话，却把任务标记成根会话故障。
    //   root 该不该 end 由 read_design / select 的 fail-closed 报错来提醒。
    if (role==='root') return;
    if (role==='unbound') {
      if (!roles.some(r=>s.childForRole[r]===a.id) || signal?.aborted) return;
      const key = a.id+':bind';
      if ((stopTries.get(key) ?? 0)<2) {
        stopTries.set(key,(stopTries.get(key) ?? 0)+1);
        agent.steer(msg('注册尚未完成。使用启动任务提供的角色和一次性邀请码调用 engineering_seven_stage bind。'));
      }
      return;
    }
    if (signal?.aborted) {
      flow.interrupt(a,s.taskId,'宿主或用户取消回合');
      return;
    }
    if (flow.consumeTurn(a,s.taskId)) { expectedDisposal.add(agent); stopTries.delete(a.id); return; }
    const count = stopTries.get(a.id) ?? 0;
    if (count>=2) {
      flow.interrupt(a,s.taskId,'两次提醒后未登记暂停或结束原因');
      const next = flow.load(s.taskId);
      route(s,next,a.id);
      return;
    }
    stopTries.set(a.id,count+1);
    const mode = ['ready','partial'].includes(s.phase) ? 'end（结束）' : 'yield（等待）或 blocked（任务受阻）';
    agent.steer(msg('结束请求未登记。任务 '+s.taskId+'。先用 '+TOOL+' status，完成当前职责后说明原因并提交 '+mode+'。'));
  });
  ctx.on('agent/disposed', ({agent}) => {
    const a = actorOf(agent);
    const bound = a && flow.trackedActor(a);
    if (!bound || bound.s.ended[bound.role] || expectedDisposal.has(agent)) return;
    const {s,role} = bound;
    // 【P0 附带】root 会话被宿主释放（换窗口 / 重开）不该把任务判成故障：
    //   状态文件还在，重新进入同一会话即可继续。
    if (role==='root') return;
    if (role==='unbound') {
      const planner = actorOf(ctx.agents.get(s.planner));
      if (planner) flow.interrupt(planner,s.taskId,'已分配的二级子代理在绑定前退出');
    } else {
      // 【BUG-11】明确标 cause='host-disposed'：这类失败【不是角色自己干不下去】，
      //   而是宿主把会话回收了。两者处置方式相反，必须在数据层就区分开。
      flow.interrupt(a,s.taskId,'宿主释放会话，未办理结束',{cause:'host-disposed'});
      // 自动重派一次（每角色一生一次，且绝不自动重派 role-blocked 的）
      const issued = flow.autoReissue(a, s.taskId, role);
      if (issued) {
        ctx.logger?.info?.('[seven-stage] 宿主释放 ' + role + ' 的会话，已自动重派 job ' + issued);
        const next = flow.load(s.taskId);
        if (next && next.members[role]) {
          notify(s.taskId, next.members[role],
            '宿主回收了你的会话，插件已经自动重新派发任务包（job=' + issued + '）。'
            + '请先 action=status 读取，然后继续推进本步。', 'auto-reissue:' + issued);
        }
      }
      route(s,flow.load(s.taskId),a.id);
    }
    stopTries.delete(a.id);
    stopTries.delete(a.id+':bind');
  });
  ctx.on('dispose', () => { instances.delete(flow); stopTries.clear(); wakeups.clear(); });
}
