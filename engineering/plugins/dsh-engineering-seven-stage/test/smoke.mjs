import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openFlow, steps } from '../lib/flow.js';
import {install} from '../lib/index.js';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {COALESCE_MS, openDeliveryBook} from '../lib/delivery.js';

const dir = mkdtempSync(join(tmpdir(), 'dsh7-check-'));
try {
  assert.equal(steps.length, 63);
  const run = openFlow(dir);
  const actor = (id, parent = null) => ({ id, parent });
  const root = actor('root');
  const planner = actor('planner', 'root');
  const roles = ['knowledge','design','review'];
  const agents = Object.fromEntries(roles.map(role => [role, actor(role, 'planner')]));
  const taskId = 'job-1';
  const call = (who, action, data = {}) => run.call(who, taskId, action, data);
  call(root, 'attach', { planner: 'planner', requirements: [{ id:'msg-1', text:'模拟任务', source:'user' }] });
  const invite = call(planner, 'invite');
  assert.throws(() => call(actor('imposter', 'root'), 'bind', {role:'review', token:invite.invitations[2].token}));
  invite.invitations.forEach(({role, token}) => { run.reserveWorker(planner, taskId, role, role); call(agents[role], 'bind', {role, token}); });
  const frame = {object:'工位A', decision:'模拟决策', boundaries:'限定范围', searchTopic:'evidence',
     verification:'独立检查', assumptions:'当前假设', prior:'版本基线', blockedBy:[]};
  const begin = () => call(planner, 'frame', frame);
  const deliver = (role, more) => {
    const job = call(agents[role], 'status').job;
    return call(agents[role], 'submit', {jobId:job.id, ...more});
  };
  // ══ 【BUG-1】联网搜索外包：知识发工单 → 传话员(root)回填 → 知识 submit ══
  const knowledge = () => {
    const job = call(agents.knowledge,'status').job;
    assert.throws(() => deliver('knowledge',{content:'没有联网'}), /SEARCH_REQUIRED/);
    // ① 知识只能发布工单，不能碰网络
    const {order} = call(agents.knowledge,'search',{jobId:job.id, query:'fixture query'});
    assert.equal(order.status,'pending');
    // ② 知识不能自己回填（否则等于检索者自证）
    assert.throws(() => call(agents.knowledge,'search_fill',{jobId:job.id, orderId:order.id,
      query:'fixture query', hits:[{title:'x',url:'https://example.com/x'}]}), /SEARCH_FILL_RELAY_ONLY/);
    // ③ 传话员（root）用宿主内置 web_search 的结果回填
    const rec = call(root,'search_fill',{jobId:job.id, orderId:order.id, query:'fixture query',
      hits:[{title:'fixture',url:'https://example.com/a',snippet:'mock only'}],
      provider:'宿主内置 web_search'});
    assert.equal(rec.search.hits.length,1);
    assert.equal(rec.search.provider,'宿主内置 web_search');
    // ④ 回填后知识才拿得到来源
    const [ref] = call(agents.knowledge,'status').job.searches[0].hits;
    deliver('knowledge', {content:'依据', sources:[{id:ref.id, location:'p.1', applicability:'仅模拟'}], gaps:[]});
  };
  const design = () => deliver('design',{content:'对象设计、计算和变更说明', evidence:'计算记录 V1', gaps:[]});
  const approve = () => {
    const x = call(planner, 'status');
    call(planner, 'commit', {jobId:x.current.review.id, outputs:Object.fromEntries(
      x.current.step.outputs.map(k=>[k,'模拟产物'+k])), conditions:'待真实项目验证', basis:'复审证据', scope:'工位A'});
  };
  assert.throws(() => call(planner,'end',{kind:'complete',reason:'提前结束'}));
  begin();
  knowledge();
  assert.equal(call(agents.design,'status').job,null);
  deliver('review',{content:'独立原始任务审查', verdict:'pass', issues:[]});
  design();
  const issue = {key:'missing-reaction', object:'工位A', requirement:'原始任务', mechanism:'无反力路径',
    evidence:'同一条反例', impact:'无法推进', needed:'独立测量', owner:'design', close:'支承反力可追溯', kind:'hard'};
  for (let attempt=0;attempt<3;attempt++) {
    deliver('review',{content:'结构化复审', verdict:'blocked', issues:[issue]});
    if(attempt<2) {
      call(planner,'rework',{issueKey:issue.key,reason:'按关闭判据修改'});
      design();
    }
  }
  assert.equal(call(planner,'status').phase, 'evidence_wait');
  assert.throws(() => call(planner,'rework',{issueKey:issue.key,reason:'继续争论'}));
  assert.throws(() => call(root,'end',{kind:'complete',reason:'冒充完成'}));
  call(root,'capture',{record:{id:'msg-2', text:'补充实测来源',source:'user'}});
  call(planner,'interpret',{messageId:'msg-2', fromStep:'1.1', classification:'fact',
    parameters:'工况和测量口径', conflict:'无', changed:'增加独立证据',
    impact:'重做后续', reason:'新事实'});
  for (let i=0;i<steps.length;i++) {
    begin();
    knowledge();
    if(i===0) deliver('review',{content:'独立原始任务审查', verdict:'pass', issues:[]});
    design();
    deliver('review',{content:'逐项审查', verdict:'pass', issues:[]});
    approve();
  }
  assert.equal(call(planner,'status').phase,'ready');
  call(planner,'propose_rooms',{rooms:[{name:'结构房间',type:'structural',parts:['DSH_支架'],why:'结构范围',depends_on:[]}],rationale:'按功能切分',scope:'工位A'});
  for (const role of roles) call(agents[role],'end',{kind:'complete',reason:'交付本角色记录'});
  call(planner,'end',{kind:'complete',reason:'已交规格'});
  call(root,'end',{kind:'complete',reason:'分析交接'});
  const reopened = openFlow(dir);
  const out = reopened.call(root,taskId,'export',{});
  assert.equal(out.result.records.length,63);
  assert.equal(out.result.ready,true);
  assert.equal(out.result.completions.length,5);
  // 读闸脚本 seven_stage_gate.py 的位置随部署方式变化，按候选顺序探测：
  //   ① DSH_SEVEN_STAGE_GATE_DIR（显式覆盖）
  //   ② <包目录>/../../../tools/          —— 仓库源码布局
  //        engineering/plugins/<pkg>/test/  →  engineering/tools/
  //   ③ <DSH_HOME>/.agent-presets/engineering/tools/  —— 安装副本布局
  //        （node_modules/dsh-engineering-seven-stage/test/ 里往上找不到 tools/）
  //   ④ 当前工作目录下的 tools/
  const gateCandidates = [
    process.env.DSH_SEVEN_STAGE_GATE_DIR,
    fileURLToPath(new URL('../../../tools/', import.meta.url)),
    (() => {
      const home = process.env.DSH_HOME
        || (process.env.USERPROFILE ? join(process.env.USERPROFILE, '.dsh') : null);
      return home ? join(home, '.agent-presets', 'engineering', 'tools') : null;
    })(),
    join(process.cwd(), 'tools'),
  ].filter(Boolean);
  const adapterDir = gateCandidates.find(d => existsSync(join(d, 'seven_stage_gate.py')))
    || gateCandidates[1];
  const python = spawnSync(process.env.DSH_SEVEN_STAGE_PYTHON || 'python', ['-B','-c',
    'import sys; sys.path.insert(0,sys.argv[1]); from seven_stage_gate import read_design as read; '
    + 'ok=read("job-1","root",include_records=True); assert ok["ok"],ok; '
    + 'assert read("job-1","root",ok["ticket"])["ok"]; assert not read("job-1","root","old")["ok"]; '
    + 'assert not read("job-1","wrong-root")["ok"]; assert len(ok["design_record"]["records"])==63',
    adapterDir],{env:{...process.env,DSH_SEVEN_STAGE_STATE_DIR:dir},encoding:'utf8'});
  assert.equal(python.status,0,python.error?.message || python.stderr);

  const workflowFile=join(dir,'workflow.json');
  writeFileSync(workflowFile,JSON.stringify({task_id:'host-job',analysis_engine:'seven_v03',
    seven_stage_root_session_id:'dsh-root'}));
  const hostActors=new Map(), hooks=new Map(), started=[], steers=[], prompts=[], extraUser=[];
  let registered, toolGuard;
  const hostAgent=(id,parent=null)=>({id,session:{id,header:{parentSession:parent},
    deriveMessages:()=>[{id:'user-1',role:'user',source:{kind:'user'},
      content:[{type:'text',text:'真人原始任务'}]},...(id==='dsh-root' ? extraUser : [])]},
    followup(msg){this.lastMessage=msg;},steer(message){steers.push({id,message});}});
  const hr=hostAgent('dsh-root'), hp=hostAgent('dsh-planner','dsh-root');
  for(const h of [hr,hp]) hostActors.set(h.id,h);
  install({tools:{register(t){registered=t;},guard(fn){toolGuard=fn;}},
    agents:{get:id=>hostActors.get(id)},
    subagents:{getProvider:()=>({}),listDescendants:async()=>[],prompt:async input=>{ prompts.push(input); return {}; },async startActivation(input){
      started.push(input);
      hostActors.set(input.childId,hostAgent(input.childId,input.parent.id));
      return {childId:input.childId,result:Promise.resolve({})};
    }},
    on:(event,fn)=>hooks.set(event,fn)
  },{stateDir:join(dir,'host-state'),workflowFile},x=>x);
  const signal=new AbortController().signal;
  const waitBatch = () => delay(COALESCE_MS + 20);
  const invoke=async (agent,action,args={})=>{
    const out=JSON.parse((await registered.execute({
      taskId:'host-job',action,dataJson:JSON.stringify(args)}, {agent,signal})).text);
    assert.notEqual(out.ok,false,action+': '+(out.error ?? out.code));
    return out;
  };
  // 【BUG-seven-stage-tool-gate · 附带】execute() 现在把内部异常统一转成
  //   {ok:false, code, error}，不再把异常冒给宿主。凡是断言"必须被拒绝"的地方，
  //   用这个变体：它把 error/ok:false 还原成一次 throw，保持原断言写法可用。
  const invokeErr=async (agent,action,args={})=>{
    const out=JSON.parse((await registered.execute({
      taskId:'host-job',action,dataJson:JSON.stringify(args)}, {agent,signal})).text);
    if (!out.ok) throw Object.assign(new Error(out.error || out.code || 'rejected'), {code:out.code});
    return out;
  };
  await invoke(hr,'attach',{planner:hp.id,messageIds:['user-1']});
  await waitBatch();
  assert.match(toolGuard({agent:hr,name:'send_message'}),/SEVEN_STAGE_TOOL_GATE/);
  assert.match(toolGuard({agent:hp,name:'shell'}),/SEVEN_STAGE_TOOL_GATE/);
  assert.equal((await hooks.get('tools/pre-execute')({agent:hp,name:'subagent_fork'},()=>({kind:'allow'}))).kind,'deny');
  assert.equal(toolGuard({agent:hostAgent('unrelated'),name:'shell'}),undefined);
  // 【P0】真实主对话（root）attach 之后必须仍然能用全部普通工具。
  //   这是 2026-11-18 实机事故：root 被当成"分析期间会话"一起锁死，
  //   门禁跑不了、连"把 bug 写进文件"和"请人介入"都做不到。
  for (const tool of ['shell','read','write','grep','list_agents','todo_write','ask_user_question'])
    assert.equal(toolGuard({agent:hr,name:tool}),undefined, 'root 不该被锁：'+tool);
  assert.equal(toolGuard({agent:hr,name:'engineering_seven_stage'}),undefined);
  await invoke(hp,'launch');
  assert.equal(started.length,3);
  // Invitations and child IDs come from the simulated native activation, not model arguments.
  assert.equal(new Set(started.map(x=>x.childId)).size,3);
  for(const record of started) {
    const invitation=JSON.parse(record.prompt[0].text.match(/dataJson=(\{[^}]+\})/)[1]);
    await invoke(hostActors.get(record.childId),'bind',invitation);
  }
  await waitBatch();
  await invoke(hp,'frame',frame);
  await waitBatch();
  const hw=hostActors.get(started[0].childId);
  const job=(await invoke(hw,'status')).job;
  // ══ 【BUG-1】插件【绝不自己联网】：无 key、无端点也能跑完整链路 ══════════
  // 旧实现在这里断言 SEARCH_KEY_MISSING —— 那正是现场死因。现在验证的是：
  //   插件进程内一个 fetch 都不发，联网完全外包给真实主对话（传话员）。
  const oldFetch=globalThis.fetch;
  const oldKey=process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  const oldUrl=process.env.DSH_SEVEN_STAGE_SEARCH_URL;
  delete process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  delete process.env.DSH_SEVEN_STAGE_SEARCH_URL;
  let fetchCalls=0;
  globalThis.fetch=async (...a)=>{ fetchCalls++; throw new Error('插件不该联网：'+a[0]); };
  try {
    // ① 知识发布检索工单：不联网、不需要任何 key
    const queryParts = ['plain fixture','interface fixture'];
    const ord=await invoke(hw,'search',{jobId:job.id,queryParts});
    assert.equal(ord.order.status,'pending');
    await waitBatch();
    assert.deepEqual(ord.order.queryParts,queryParts);
    assert.ok(steers.at(-1).message.content[0].text.includes('plain fixture'));
    assert.ok(steers.at(-1).message.content[0].text.includes('interface fixture'));
    assert.equal(fetchCalls,0,'发布工单期间不得发起任何网络请求');
    // ② 知识重复发布同一工单 → 拒绝（防止绕开预算）
    await assert.rejects(invokeErr(hw,'search',{jobId:job.id,queryParts}),/SEARCH_ORDER_ALREADY_PENDING/);
    // ③ 知识自己回填 → 拒绝（检索者不能自证）
    await assert.rejects(invokeErr(hw,'search_fill',{jobId:job.id,orderId:ord.order.id,
      query:ord.order.query,hits:[{title:'伪造',url:'https://example.com/fake'}]}),/SEARCH_FILL_RELAY_ONLY/);
    // ④ 传话员（真实主对话）回填宿主内置 web_search 的结果
    const rec=await invoke(hr,'search_fill',{jobId:job.id,orderId:ord.order.id,
      query:ord.order.query,provider:'宿主内置 web_search',
      hits:[{title:'Fixture',url:'https://example.com/fixture',snippet:'controlled replay'}]});
    assert.equal(rec.search.hits.length,1);
    assert.equal(rec.search.provider,'宿主内置 web_search');
    assert.equal(fetchCalls,0,'回填期间插件也不得联网');
    // ⑤ 回填后知识能拿到来源
    const st=await invoke(hw,'status');
    assert.equal(st.job.searches.length,1);
    assert.equal(st.search.order.status,'filled');
    assert.equal(st.job.searches[0].hits.length,1);
    const math=await invoke(hw,'calculate',{jobId:job.id,purpose:'数字接口',units:'N',ops:[
      {op:'mul',a:2,b:9.8},{op:'div',a:'$1',b:2}]});
    assert.deepEqual(math.calculation.values,[19.6,9.8]);
    await waitBatch();
    hooks.get('agent/turn-stopping')({agent:hw,signal});
    // 【2026-11-19】投递语义已从 followup(queue) 改为 steer，并加了"在途合并"：
    //   同一条状态变化不再每条通知都投一次，只保留最后一条未消费的通知。
    //   这里只断言"确实有提醒到达"，不再绑定具体条数 —— 条数是实现细节，
    //   而"不许一条都没有"才是这个用例真正要守的东西。
    assert.ok(steers.length >= 1, '结束请求未登记时必须提醒至少一次');
    assert.ok(String(steers.at(-1).message?.content?.[0]?.text ?? '').length > 0,
      '提醒正文不能为空');
    await assert.rejects(invokeErr(hw,'yield',{reason:'掩盖当前待办'}),/PENDING_JOB/);
    const byRole = role => hostActors.get(started.find(item => item.label === '七阶段分析 '+role).childId);
    const hReview = byRole('review'), hDesign = byRole('design');
    const deliverHost = async (agent,data) => {
      const status = await invoke(agent,'status');
      return invoke(agent,'submit',{jobId:status.job.id,...data});
    };
    const lastPrompt = () => prompts.at(-1)?.childSessionId;
    const plannerPrompts = () => prompts.filter(input => input.childSessionId === hp.id).length;
    await invoke(hp,'status'); // consume the startup notice
    const baseline = plannerPrompts();
    const quiet = await invoke(hp,'yield',{reason:'等待执行岗位完成当前任务'});
    assert.equal(quiet.phase,'knowledge');
    await deliverHost(hw,{content:'检索后的知识依据',sources:[{
      id:st.job.searches[0].hits[0].id, location:'第 1 页', applicability:'受控回放样本'
    }],gaps:[]});
    await waitBatch();
    assert.equal(lastPrompt(),hReview.id,'盲审应收到插件通知');
    assert.equal(plannerPrompts(),baseline,'交班给盲审不应叫醒无待办的总计');
    await invoke(hp,'status');
    await deliverHost(hReview,{content:'审查原始任务边界',verdict:'pass',issues:[],closed:[]});
    await waitBatch();
    assert.equal(lastPrompt(),hDesign.id,'设计应收到插件通知');
    assert.equal(plannerPrompts(),baseline,'交班给设计不应叫醒无待办的总计');
    await invoke(hp,'status');
    await deliverHost(hDesign,{content:'候选设计方案',evidence:'模拟计算记录',gaps:[]});
    await waitBatch();
    assert.equal(lastPrompt(),hReview.id,'复审应收到插件通知');
    assert.equal(plannerPrompts(),baseline,'交班给复审不应叫醒无待办的总计');
    await deliverHost(hReview,{content:'设计方案可提交',verdict:'pass',issues:[],closed:[]});
    await waitBatch();
    assert.equal(lastPrompt(),hp.id,'复审完成应唤醒总计提交');
    assert.equal(plannerPrompts(),baseline+1);
    assert.equal(quiet.actionHelp,undefined,'等待回执不重复全套操作说明');
    assert.equal((await invoke(hp,'status')).phase,'commit','详细状态仍然可读');
    for (let i=0;i<steps.length;i++) {
      if (i>0) {
        await invoke(hp,'frame',frame);
        await waitBatch();
        if (i===1) {
          const ids=['batch-note-1','batch-note-2'];
          for (const id of ids) extraUser.push({id,role:'user',source:{kind:'user'},
            content:[{type:'text',text:'向操作者确认的备注 '+id}]});
          await assert.rejects(invokeErr(hr,'capture',{messageIds:[ids[0],'missing']}),/ROOT_USER_MESSAGE_NOT_AVAILABLE/);
          assert.equal((await invoke(hp,'status')).newFacts.length,0);
          const before = plannerPrompts();
          const captured = await invoke(hr,'capture',{messageIds:ids});
          assert.deepEqual(captured.records.map(x=>x.id),ids);
          await waitBatch();
          assert.equal(plannerPrompts(),before+1,'两条真人答复整批只投一次');
          for (const id of ids) {
            assert.ok(prompts.at(-1).content[0].text.includes(id));
            await invoke(hp,'interpret',{messageId:id,classification:'no_change',parameters:'只记录沟通备注',
              conflict:'未发现与现有需求冲突',changed:'这条备注不修改设计边界',
              impact:'本小步仍按已登记条件继续',reason:'备注仅用于补充交流记录'});
          }
          const duplicate = await invoke(hr,'capture',{messageIds:ids});
          assert.equal(duplicate.idempotent,true);
          await waitBatch();
          assert.equal(plannerPrompts(),before+1,'重采同一批不重新投递');
        }
        const {job:k} = await invoke(hw,'status');
        const {order} = await invoke(hw,'search',{jobId:k.id,query:'fixture '+steps[i].id});
        await waitBatch();
        const {search} = await invoke(hr,'search_fill',{jobId:k.id,orderId:order.id,query:order.query,
          hits:[{title:'受控样本',url:'https://example.com/fixture'}]});
        await waitBatch();
        await deliverHost(hw,{content:'检索证据',sources:[{id:search.hits[0].id,
          location:'第 1 页',applicability:'受控回放样本'}],gaps:[]});
        await waitBatch();
        await deliverHost(hDesign,{content:'候选设计结果',evidence:'模拟计算记录',gaps:[]});
        await waitBatch();
        await deliverHost(hReview,{content:'独立复审结论',verdict:'pass',issues:[],closed:[]});
        await waitBatch();
      }
      const plan = await invoke(hp,'status');
      assert.equal(plan.delivery.scope,steps[i].id);
      assert.equal(plan.delivery.recipients.knowledge.attempted,i===0 ? 3 : 2);
      assert.equal(plan.delivery.recipients.design.attempted,1);
      assert.equal(plan.delivery.recipients.review.attempted,i===0 ? 2 : 1);
      assert.equal(plan.delivery.recipients.root.attempted,1);
      assert.equal(plan.delivery.recipients.planner.attempted,i<=1 ? 2 : 1);
      const committed = await invoke(hp,'commit',{jobId:plan.current.review.id,
        scope:'工程范围',basis:'独立复审证据',conditions:'保留实机验证条件',
        outputs:Object.fromEntries(steps[i].outputs.map(k=>[k,'模拟交付 '+k]))});
      assert.equal(committed.progress.done,i+1);
    }
    await waitBatch();
    assert.equal((await invoke(hp,'status')).phase,'ready');
    assert.match(toolGuard({agent:hr,name:'send_message'} ) ?? '',/^$/); // after final delivery
    const book = openDeliveryBook(join(dir,'host-state'));
    const propose = exception => () => ({sid:'role-fixture',keys:['sample'],exception});
    for (let i=1;i<=3;i++) assert.equal(book.reserve('cap-fixture','1.1','review',propose(null)).row.n,i);
    assert.equal(book.reserve('cap-fixture','1.1','review',propose(null)).code,'DELIVERY_BUDGET_EXHAUSTED');
    const restarted = openDeliveryBook(join(dir,'host-state'));
    assert.equal(restarted.reserve('cap-fixture','1.1','review',propose('SECOND_REVIEW_REWORK')).row.n,4);
    assert.equal(restarted.reserve('cap-fixture','1.1','review',propose('SECOND_REVIEW_REWORK')).code,
      'DELIVERY_BUDGET_EXHAUSTED');
    console.log('OK: 63 小步模拟宿主入口回放，真人答复合批和去重，第四批例外、第五批硬拒、重开预算保持');
  } finally {
    globalThis.fetch=oldFetch;
    const restore=(k,v)=>{ if(v===undefined) delete process.env[k]; else process.env[k]=v; };
    restore('DSH_SEVEN_STAGE_BRAVE_KEY',oldKey);
    restore('DSH_SEVEN_STAGE_SEARCH_URL',oldUrl);
    hooks.get('dispose')();
  }
  console.log('OK: 模拟 DSH 宿主启动三会话、两处工具守卫、检索工单外包（插件零联网）、数值计算、结束钩子、Python 交接凭证');
  console.log('OK: 真实身份模拟、独立预审、在线凭据门、两轮上限、事实换版、63 步完整链、结束理由、重读');
} finally {
  rmSync(dir, {recursive:true, force:true});
}
