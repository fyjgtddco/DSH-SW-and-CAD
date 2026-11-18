import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openFlow, steps } from '../lib/flow.js';
import {install} from '../lib/index.js';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

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
  const knowledge = () => {
    const job = call(agents.knowledge,'status').job;
    assert.throws(() => deliver('knowledge',{content:'没有联网'}), /SEARCH_REQUIRED/);
    run.recordSearch(agents.knowledge, taskId, job.id, 'fixture', [{title:'fixture',url:'https://example.com/a',snippet:'mock only'}]);
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
  // 合并进仓库后，读闸脚本落在 engineering/tools/seven_stage_gate.py。
  // 用 DSH_SEVEN_STAGE_GATE_DIR 可覆盖（旧分离交付目录仍可跑）。
  const adapterDir = process.env.DSH_SEVEN_STAGE_GATE_DIR
    || fileURLToPath(new URL('../../../tools/',import.meta.url));
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
  const hostActors=new Map(), hooks=new Map(), started=[], steers=[];
  let registered, toolGuard;
  const hostAgent=(id,parent=null)=>({id,session:{id,header:{parentSession:parent},
    deriveMessages:()=>[{id:'user-1',role:'user',source:{kind:'user'},
      content:[{type:'text',text:'真人原始任务'}]}]},
    followup(msg){this.lastMessage=msg;},steer(message){steers.push({id,message});}});
  const hr=hostAgent('dsh-root'), hp=hostAgent('dsh-planner','dsh-root');
  for(const h of [hr,hp]) hostActors.set(h.id,h);
  install({tools:{register(t){registered=t;},guard(fn){toolGuard=fn;}},
    agents:{get:id=>hostActors.get(id)},
    subagents:{getProvider:()=>({}),listDescendants:async()=>[],prompt:async()=>({}),async startActivation(input){
      started.push(input);
      hostActors.set(input.childId,hostAgent(input.childId,input.parent.id));
      return {childId:input.childId,result:Promise.resolve({})};
    }},
    on:(event,fn)=>hooks.set(event,fn)
  },{stateDir:join(dir,'host-state'),workflowFile},x=>x);
  const signal=new AbortController().signal;
  const invoke=async (agent,action,args={})=>JSON.parse((await registered.execute({
    taskId:'host-job',action,dataJson:JSON.stringify(args)}, {agent,signal})).text);
  await invoke(hr,'attach',{planner:hp.id,messageIds:['user-1']});
  assert.match(toolGuard({agent:hp,name:'shell'}),/SEVEN_STAGE_TOOL_GATE/);
  assert.equal((await hooks.get('tools/pre-execute')({agent:hp,name:'subagent_fork'},()=>({kind:'allow'}))).kind,'deny');
  assert.equal(toolGuard({agent:hostAgent('unrelated'),name:'shell'}),undefined);
  await invoke(hp,'launch');
  assert.equal(started.length,3);
  // Invitations and child IDs come from the simulated native activation, not model arguments.
  assert.equal(new Set(started.map(x=>x.childId)).size,3);
  for(const record of started) {
    const invitation=JSON.parse(record.prompt[0].text.match(/dataJson=(\{[^}]+\})/)[1]);
    await invoke(hostActors.get(record.childId),'bind',invitation);
  }
  await invoke(hp,'frame',frame);
  const hw=hostActors.get(started[0].childId);
  const job=(await invoke(hw,'status')).job;
  const oldFetch=globalThis.fetch;
  const oldKey=process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  delete process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  try {
    await assert.rejects(invoke(hw,'search',{jobId:job.id,query:'plain fixture'}),/SEARCH_KEY_MISSING/);
    process.env.DSH_SEVEN_STAGE_BRAVE_KEY='fixture-only';
    globalThis.fetch=async (url,options)=>{
      assert.equal(new URL(url).host,'api.search.brave.com');
      assert.equal(new URL(url).searchParams.get('q'),'plain fixture');
      assert.equal(options.headers['X-Subscription-Token'],'fixture-only');
      return new Response(JSON.stringify({web:{results:[{url:'https://example.com/fixture',
        title:'Fixture',description:'placeholder source for controlled replay'}]}}));
    };
    const rec=await invoke(hw,'search',{jobId:job.id,query:'plain fixture'});
    assert.equal(rec.search.hits.length,1);
    const math=await invoke(hw,'calculate',{jobId:job.id,purpose:'数字接口',units:'N',ops:[
      {op:'mul',a:2,b:9.8},{op:'div',a:'$1',b:2}]});
    assert.deepEqual(math.calculation.values,[19.6,9.8]);
    hooks.get('agent/turn-stopping')({agent:hw,signal});
    assert.equal(steers.length,1);
    await assert.rejects(invoke(hw,'yield',{reason:'掩盖当前待办'}),/PENDING_JOB/);
  } finally {
    globalThis.fetch=oldFetch;
    if(oldKey===undefined) delete process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
    else process.env.DSH_SEVEN_STAGE_BRAVE_KEY=oldKey;
    hooks.get('dispose')();
  }
  console.log('OK: 模拟 DSH 宿主启动三会话、两处工具守卫、联网错误和搜索接口回放、数值计算、结束钩子、Python 交接凭证');
  console.log('OK: 真实身份模拟、独立预审、在线凭据门、两轮上限、事实换版、63 步完整链、结束理由、重读');
} finally {
  rmSync(dir, {recursive:true, force:true});
}
