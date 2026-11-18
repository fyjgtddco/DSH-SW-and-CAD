import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import { openFlow, roles } from './flow.js';

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
const directUser = agent => (agent.session.deriveMessages?.() ?? [])
  .filter(m=>m.role==='user' && m.source?.kind==='user' && m.id)
  .map(m=>({id:String(m.id), text:(m.content ?? []).filter(x=>x.type==='text').map(x=>x.text).join('\n'),source:'user'}))
  .filter(m=>m.text.trim());

export function activeSevenStage(agent) {
  const a = actorOf(agent);
  return a ? [...instances].map(f=>f.trackedActor(a)).find(Boolean) ?? null : null;
}

const pendingLookups = new Set();
const normaliseHits = (raw, pick) => raw.filter(x=>{
  try { return ['https:','http:'].includes(new URL(pick(x).url).protocol); } catch { return false; }
}).slice(0,6).map(x=>({title:compact(pick(x).title).slice(0,500),
  url:compact(pick(x).url).slice(0,1800), snippet:compact(pick(x).snippet).slice(0,1400)}));

/** Brave Search（HTTPS 直连）。只有在配置了 DSH_SEVEN_STAGE_BRAVE_KEY 时才用。 */
async function braveSearch(query, signal) {
  const token = process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  check(token,'SEARCH_KEY_MISSING: 需要为 DSH 宿主配置 DSH_SEVEN_STAGE_BRAVE_KEY');
  const url = new URL('https://api.search.brave.com/res/v1/web/search');
  url.search = new URLSearchParams({q:query,count:'6',safesearch:'strict'});
  const response = await fetch(url, {redirect:'error',
    signal:AbortSignal.any([signal,AbortSignal.timeout(15_000)]),
    headers:{Accept:'application/json','X-Subscription-Token':token}});
  check(response.ok,'SEARCH_HTTP_'+response.status);
  const json = await response.json();
  return normaliseHits(json.web?.results ?? [], x=>x);
}

/** DSH 内部联网搜索桥。
 *
 *  用户口径（2026-11-18）：「到时候直接调用 DSH 内部的联网搜索，是可以搜索的」。
 *  因此不强制自备 Brave 密钥 —— 只要宿主注入了内部检索端点就走它：
 *      DSH_SEVEN_STAGE_SEARCH_URL   例：http://127.0.0.1:<port>/<route>
 *      DSH_SEVEN_STAGE_SEARCH_TOKEN 可选 Bearer
 *
 *  返回体接受两种常见形状：
 *      {results:[{title,url,snippet?}]}   或   {web:{results:[...]}}
 *  形状不认识就显式失败（OUTCOME_UNKNOWN 不猜），绝不当成"搜到 0 条"。
 */
async function hostSearch(query, signal) {
  const url = process.env.DSH_SEVEN_STAGE_SEARCH_URL;
  check(url,'SEARCH_ENDPOINT_MISSING: 未配置 DSH 联网搜索（DSH_SEVEN_STAGE_SEARCH_URL 或 DSH_SEVEN_STAGE_BRAVE_KEY）');
  const token = process.env.DSH_SEVEN_STAGE_SEARCH_TOKEN;
  const response = await fetch(url, {
    method:'POST', redirect:'error',
    signal:AbortSignal.any([signal,AbortSignal.timeout(20_000)]),
    headers:{'Content-Type':'application/json',Accept:'application/json',
      ...(token ? {Authorization:'Bearer '+token} : {})},
    body:JSON.stringify({query,count:6})
  });
  check(response.ok,'SEARCH_HTTP_'+response.status);
  const json = await response.json();
  const flat = Array.isArray(json?.results) ? json.results
    : Array.isArray(json?.web?.results) ? json.web.results : null;
  check(flat,'SEARCH_SHAPE_UNKNOWN: 宿主检索返回体里没有 results / web.results');
  return normaliseHits(flat, x=>({title:x.title, url:x.url, snippet:x.snippet ?? x.description}));
}

/** 选后端：显式端点优先，其次 Brave 密钥。两者都没有 → fail-closed。 */
function webSearch(query, signal) {
  if (process.env.DSH_SEVEN_STAGE_SEARCH_URL) return hostSearch(query, signal);
  return braveSearch(query, signal);
}

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
          '  # 联网：$env:DSH_SEVEN_STAGE_SEARCH_URL，或 $env:DSH_SEVEN_STAGE_BRAVE_KEY',
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
  const targetPhase = s => [s.revision,s.cursor,s.phase,s.question?.id,
    ...Object.values(s.jobs).map(j=>j.id),Object.keys(s.ended).join(','),
    Object.keys(s.failures).join(','),s.facts.length,Object.keys(s.members).length].join('|');

  function notify(task, sid, text, key) {
    if(!sid || !text) return;
    const n = task + ':' + sid;
    if(wakeups.get(n)===key) return;
    const message='七阶段插件任务 ' + task + '。' + text
      + ' 请先调用 ' + TOOL + ' action=status 读取当前状态及版本。';
    const s=flow.load(task);
    wakeups.set(n,key);
    const failed=()=>{
      if (wakeups.get(n)===key) wakeups.delete(n);
      ctx.logger?.warn?.('[seven-stage] 通知未确认，任务已存。可用 deliver 重发 ' + sid);
    };
    try {
      if (sid===s.root) {
        const live=ctx.agents.get(sid);
        if (!live) return failed();
        live.followup(msg(message));
      } else {
        // 使用宿主原生的可续跑投递，正常 yield 后会话可能已不在 agents.get。
        Promise.resolve(ctx.subagents.prompt({
          requestId:randomUUID(),childSessionId:sid,delivery:'queue',
          content:[{type:'text',text:message}]
        },AbortSignal.timeout(15_000))).catch(failed);
      }
    } catch { failed(); }
  }
  function route(prior, s, sender) {
    if (!s || (prior && targetPhase(prior)===targetPhase(s))) return;
    const key = targetPhase(s);
    for (const [role,job] of Object.entries(s.jobs)) {
      if (prior?.jobs[role]?.id===job.id && !(prior?.failures[role] && !s.failures[role])) continue;
      if (!s.failures[role]) notify(s.taskId,s.members[role],'有新任务包。',key);
    }
    if (s.planner!==sender && (['frame','commit','resolve','evidence_wait','ready','partial'].includes(s.phase)
      || s.facts.length!==prior?.facts.length || Object.keys(s.failures).length)) {
      notify(s.taskId,s.planner,'收到岗位回执或流程状态更新。',key);
    }
    if (s.root!==sender && (s.question || ['ready','partial','evidence_wait'].includes(s.phase) || Object.keys(s.failures).length)) {
      notify(s.taskId,s.root,'收到需要真人参与或核对的分析消息。',key);
    }
    if (['ready','partial'].includes(s.phase) && prior?.phase!==s.phase) {
      for (const role of roles) if (s.members[role]!==sender && !s.ended[role] && !s.failures[role]) {
        notify(s.taskId,s.members[role],'请通过插件登记你的结束原因。',key);
      }
    }
  }
  function currentWorkflow(task) {
    const wf = JSON.parse(readFileSync(workflowFile,'utf8').replace(/^\uFEFF/,''));
    check(wf.task_id===task,'WRONG_DSH_WORKFLOW_TASK');
    check(wf.analysis_engine==='seven_v03' && wf.seven_stage_root_session_id,
      'WORKFLOW_SEVEN_STAGE_NOT_CONFIGURED');
    return wf;
  }
  function enforce(exec) {
    const a = actorOf(exec.agent);
    if (!a) return;
    const bound = flow.trackedActor(a);
    if (!bound || exec.name===TOOL) return;
    return 'SEVEN_STAGE_TOOL_GATE: 分析期间由 engineering_seven_stage 分派任务、搜索、转发和结束。当前工具不在本阶段许可内。';
  }
  ctx.tools.guard(enforce);
  ctx.on('tools/pre-execute', (exec, next) => {
    const reason = enforce(exec);
    return reason ? {kind:'deny', reason, info:{code:'SEVEN_STAGE_TOOL_GATE'}} : next();
  });

  ctx.tools.register({
    name:TOOL,
    description:'DSH 七阶段分析调度器。真实主对话先创建总计子代理再 attach；总计子代理 launch 启动并绑定知识、设计、反驳。status 只返回你能看到的下一步。所有提交与暂停、结束均走此工具。',
    parameters:{type:'object',additionalProperties:false,required:['taskId','action'],properties:{
      taskId:{type:'string',description:'根任务的 workflow_state.task_id'},
      action:{type:'string',enum:['attach','launch','bind','status','frame','search','calculate','explore','submit','commit','rework',
        'question','capture','interpret','rewind','propose_rooms','partial','yield','blocked','retry','end','read','export','deliver']},
      dataJson:{type:'string',description:'动作参数的 JSON 对象。status 返回所需字段，可按错误修正。'}
    }},
    output:{schema:{type:'object',required:['text'],additionalProperties:false,properties:{text:{type:'string'}}},
      render:(_args,r)=>[{type:'text',text:r.text}]},
    isConcurrencySafe:()=>false,
    async execute({taskId,action,dataJson},exec) {
      const a = actorOf(exec.agent);
      if (exec.agent) expectedDisposal.delete(exec.agent);
      check(a,'HOST_IDENTITY_REQUIRED');
      check(typeof taskId==='string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(taskId),'INVALID_TASK_ID');
      check(!dataJson || dataJson.length<350_000,'INPUT_TOO_LARGE');
      const data = dataJson ? JSON.parse(dataJson) : {};
      check(data && typeof data==='object' && !Array.isArray(data),'INVALID_PAYLOAD');
      exec.signal.throwIfAborted();
      const prior = flow.load(taskId);
      const bound = flow.trackedActor(a);
      check(!bound || bound.s.taskId===taskId,'WRONG_BOUND_TASK');
      if (!['status','read','export'].includes(action)) {
        const wf=currentWorkflow(taskId);
        check(wf.seven_stage_root_session_id===(prior?.root ?? a.id), 'WRONG_WORKFLOW_ROOT');
      }
      let result;
      if (action==='attach') {
        check(a.parent===null, 'ROOT_REQUIRED');
        check(typeof data.planner==='string' && actorOf(ctx.agents.get(data.planner))?.parent===a.id,'REAL_DIRECT_CHILD_REQUIRED');
        const original = directUser(exec.agent);
        const wanted = Array.isArray(data.messageIds) ? data.messageIds : original.slice(-1).map(m=>m.id);
        const requirements = wanted.map(id=>original.find(m=>m.id===id));
        check(requirements.length && requirements.every(Boolean),'ROOT_USER_MESSAGE_NOT_AVAILABLE');
        result = flow.call(a,taskId,'attach',{planner:data.planner,requirements});
        if (!prior) notify(taskId,data.planner,'已绑定为需求与规划。调用 launch 创建三个工作角色。','attach');
      } else if (action==='launch') {
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
            check(descendants.filter(x=>x.kind==='child' && x.activity!=='inactive')
              .every(x=>known.has(String(x.id))), 'OTHER_ROOMS_ACTIVE_DRAIN_BEFORE_ANALYSIS');
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
        notify(taskId,recipient,'重读待办和已落盘的消息。',targetPhase(prior));
        result={ok:true,note:'已安排通知，是否接收以会话读取到任务并提交为准。'};
      } else if (action==='search') {
        const query = compact(data.query);
        check(query.length>=3 && query.length<=240 && !/[\r\n]/.test(query),'PUBLIC_SEARCH_QUERY_REQUIRED');
        const job = flow.searchJob(a,taskId,data.jobId).jobs.knowledge;
        check(job.searches.length<5 && !job.searches.some(x=>x.query===query),'SEARCH_LIMIT_OR_DUPLICATE');
        check(!pendingLookups.has(data.jobId),'SEARCH_ALREADY_RUNNING');
        pendingLookups.add(data.jobId);
        try {
          const hits = await webSearch(query,exec.signal);
          exec.signal.throwIfAborted();
          result = {search:flow.recordSearch(a,taskId,data.jobId,query,hits),
            next:'已联网。用返回的来源 ID 提交出处位置、适用边界与缺口。网页摘要只能作资料。'};
        } finally { pendingLookups.delete(data.jobId); }
      } else if (action==='capture') {
        check(bound?.role==='root','ROOT_REQUIRED');
        const record = directUser(exec.agent).findLast(m=>!data.messageId || m.id===data.messageId);
        check(record,'ROOT_USER_MESSAGE_NOT_AVAILABLE');
        result = flow.call(a,taskId,'capture',{record});
      } else if (action==='partial') {
        // 真人的原话来自会话。模型填写的布尔值不能赋予结束授权。
        const last = directUser(exec.agent).at(-1);
        const explicitStop = bound?.role==='root' && last && /^\s*(停止分析|终止分析|结束分析|stop analysis)[。.!！\s]*$/i.test(last.text);
        result = flow.call(a,taskId,'partial',{reason:data.reason,hostStop:explicitStop===true});
      } else {
        result = flow.call(a,taskId,action,data);
      }
      const s = flow.load(taskId);
      if (action==='export' && result.result) {
        const {records, history, log, ...manifest} = result.result;
        result = {manifest, recordIndex:records.map(r=>({step:r.step,scope:r.scope,revision:r.revision})),
          final:records.at(-1)?.step==='7.12' ? records.at(-1).outputs : null,
          note:'完整记录由交接接口导出，各小步可用 read 查询。'};
      }
      route(prior,s,a.id);
      return {text:JSON.stringify(result)};
    }
  });

  ctx.on('agent/turn-stopping', ({agent,signal}) => {
    const a = actorOf(agent);
    const bound = a && flow.trackedActor(a);
    if (!bound) return;
    const {s,role} = bound;
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
    if (role==='unbound') {
      const planner = actorOf(ctx.agents.get(s.planner));
      if (planner) flow.interrupt(planner,s.taskId,'已分配的二级子代理在绑定前退出');
    } else {
      flow.interrupt(a,s.taskId,'宿主释放会话，未办理结束');
      route(s,flow.load(s.taskId),a.id);
    }
    stopTries.delete(a.id);
    stopTries.delete(a.id+':bind');
  });
  ctx.on('dispose', () => { instances.delete(flow); stopTries.clear(); wakeups.clear(); });
}
