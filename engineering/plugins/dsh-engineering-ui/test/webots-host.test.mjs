import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
const python=spawnSync('python',['-c','import sys;print(sys.executable)'],{encoding:'utf8'}).stdout.trim();
import { fileURLToPath } from 'node:url';
const mod = await import('../lib/webots.js').catch(() => ({}));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 's07-host-'));
const env = { DSH_STATE_DIR: temp, DSH_HOME: temp, DSH_ENGINEERING_ROOT: temp };
const agent = { id: 'child-1', preset: 'engineering' };
const ctx = { agents: { get: id => id === agent.id ? agent : undefined } };
const options = { env, findToolsDir: () => temp, presetOf: a => a.preset };
function state(task = 'task-1', room = '结构件', owner = 'child-1') {
 fs.writeFileSync(path.join(temp, 'workflow_state.json'), JSON.stringify({ task_id: task }));
 fs.writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify({ rooms: { [room]: { active: true, task_id: task } }, subagents: { [room]: { id: owner } } }));
}
function resolve() { assert.equal(typeof mod.resolveWebotsContext, 'function', 'host adapter is missing'); return mod.resolveWebotsContext(ctx, agent, options); }
test.after(() => fs.rmSync(temp, { recursive: true, force: true }));
test('real live room binding yields deterministic context without touching SW', () => {
 state(); const c = resolve();
 assert.equal(c.task_id, 'task-1'); assert.equal(c.room_label, '结构件');
 assert.equal(c.room_id, 'room-' + crypto.createHash('sha256').update('结构件').digest('hex'));
 assert.equal(c.state_dir, fs.realpathSync(temp));
});
test('context reads live task and rejects stale, duplicate, missing and forged identities', () => {
 state('task-2'); assert.equal(resolve().task_id, 'task-2');
 state('task-1', 'room-1', 'other'); assert.throws(resolve);
 state(); const ms = JSON.parse(fs.readFileSync(path.join(temp,'mode_state.json'))); ms.rooms['room-2']={active:true,task_id:'task-1'}; ms.subagents['room-2']={id:agent.id}; fs.writeFileSync(path.join(temp,'mode_state.json'),JSON.stringify(ms)); assert.throws(resolve);
 state(); const wf=path.join(temp,'workflow_state.json'); fs.writeFileSync(wf,JSON.stringify({task_id:'changed'})); assert.throws(resolve);
 state(); assert.throws(() => mod.resolveWebotsContext(ctx, {...agent}, options));
 agent.preset='default'; assert.throws(resolve); agent.preset='engineering';
});
test('explicit missing or mismatched state refuses installation fallback', () => {
 state(); resolve();
 assert.throws(() => mod.resolveWebotsContext(ctx,agent,{...options,env:{...env,DSH_STATE_DIR:path.join(temp,'missing')}}));
 assert.throws(() => mod.resolveWebotsContext(ctx,agent,{...options,findToolsDir:()=>path.dirname(temp)}));
});
test('guard rejects other contexts on all namespaced tools and leaves SW tools alone', () => {
 state('task-1','room-1'); resolve(); assert.equal(typeof mod.webotsGuard,'function');
 const guard=mod.webotsGuard(ctx,options);
 assert.equal(guard({name:'sw-tool',arguments:{},agent}),undefined);
 assert.equal(guard({name:'mcp__webots__sim_status',arguments:{task_id:'task-1',room_id:'room-'+crypto.createHash('sha256').update('room-1').digest('hex')},agent}),undefined);
 assert.match(guard({name:'mcp__webots__sim_step',arguments:{task_id:'task-2',room_id:'room-'+crypto.createHash('sha256').update('room-1').digest('hex')},agent}),/CONTEXT/);
 assert.match(guard({name:'mcp__webots__sim_status',arguments:{task_id:'task-1',room_id:'room-'+crypto.createHash('sha256').update('room-1').digest('hex')}}),/CONTEXT/);
});
test('read-only routes derive ownership, apply wrapper and preserve unavailable status', async () => {
 state('task-1','room-1'); resolve(); assert.equal(typeof mod.installWebots,'function');
 const routes=new Map(); let guard; let wrapped=0;
 const c={...ctx,tools:{guard(fn){guard=fn;return ()=>{};}},webServer:{register(r){routes.set(r.path,r);return ()=>{};}}};
 const cleanup=mod.installWebots(c,{...options,guardRoute:r=>{wrapped++;return r;},send:(res,status,body)=>Object.assign(res,{status,body}),queryOf:req=>new URL(req.url,'http://localhost').searchParams});
 assert.equal(wrapped,4); assert.equal(typeof guard,'function');
 const call=async (route,url,method='GET')=>{const res={};await routes.get('/dsh-engineering-ui/'+route).handler({url,method},res);return res;};
 let r=await call('webots-context','/?sessionId=child-1'); assert.equal(r.status,200);assert.equal(r.body.core_ready,false);assert.equal(r.body.error_code,'RUNTIME_NOT_READY'); assert.equal(r.body.job_state,'NEW');
 r=await call('webots-context','/?sessionId=unknown');assert.equal(r.status,403);
 r=await call('webots-context','/?sessionId=child-1','POST');assert.equal(r.status,405);
 r=await call('webots-report','/?sessionId=child-1&task_id=forged&run_id=run-1&report_type=experiment_report');assert.equal(r.status,400);
 r=await call('webots-report','/?sessionId=child-1&run_id=../escape&report_type=experiment_report');assert.equal(r.status,400);
 r=await call('webots-evidence','/?sessionId=child-1&run_id=run-1&limit=99999999');assert.equal(r.status,400);
 cleanup();
});

test('ASCII names cannot impersonate a non-ASCII room identity',()=>{
 state('task-1','结构件');const first=resolve().room_id;
 state('task-1',first);const second=resolve().room_id;assert.notEqual(second,first);
});
test('a slow read subprocess does not block host context queries',async()=>{
 state('task-1','room-1'); const tools=path.join(temp,'engineering','tools');const pkg=path.join(tools,'webots_mcp');fs.mkdirSync(pkg,{recursive:true});
 fs.writeFileSync(path.join(pkg,'__init__.py'),'');fs.writeFileSync(path.join(pkg,'host.py'),'import time,json\ntime.sleep(0.3)\nprint(json.dumps({"ok":True,"data":{"fixture":"slow-reader"}}))');
 const routes=new Map();const c={...ctx,tools:{guard:()=>()=>{}},webServer:{register:r=>{routes.set(r.path,r);return ()=>{};}}};
 const dispose=mod.installWebots(c,{...options,env:{...env,DSH_WEBOTS_PYTHON:python,DSH_ENGINEERING_ROOT:path.dirname(tools)},guardRoute:r=>r,send:(res,status,body)=>Object.assign(res,{status,body}),queryOf:req=>new URL(req.url,'http://localhost').searchParams});
 const response={};let finished=false;
 const reading=routes.get('/dsh-engineering-ui/webots-report').handler({method:'GET',url:'/?sessionId=child-1&run_id=run-1&report_type=experiment_report'},response).then(()=>{finished=true;});
 await new Promise(resolve=>setTimeout(resolve,30));
 const status={};await routes.get('/dsh-engineering-ui/webots-context').handler({method:'GET',url:'/?sessionId=child-1'},status);
 assert.equal(finished,false,'slow reader blocked the host');assert.equal(status.status,200);await reading;assert.equal(response.status,200);assert.equal(response.body.data.fixture,'slow-reader');dispose();
});

test('actual host apply keeps connection and read-only routes available without MCP',async()=>{
 state('task-1','room-1');const routes=new Map();const guards=[];const hooks=new Map();
 const ctx2={...ctx,webServer:{register:r=>{routes.set(r.path,r);return ()=>{};}},tools:{guard:fn=>{guards.push(fn);return ()=>{};}},on:(name,fn)=>{hooks.set(name,fn);return ()=>{};},get:()=>undefined,inject:()=>()=>{}};
 const source=fs.readFileSync(process.env.DSH_S07_HOST_SOURCE || new URL('../lib/index.js',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace(/^export /gm,'');
 const sandbox={fs,path,os,crypto,spawnSync,installWebots:mod.installWebots,agentPresets:{},DefenseSigner:class{writeRuntime(){}startGuard(){}stopGuard(){}},process:{env:{...env},platform:process.platform,cwd:()=>temp},console:{log(){},warn(){},error(){}},setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},URL,URLSearchParams};
 vm.runInNewContext(source+'\nthis.boot=apply;',sandbox);const dispose=sandbox.boot(ctx2);
 assert.ok(routes.has('/dsh-engineering-ui/conn-state'));assert.ok(routes.has('/dsh-engineering-ui/webots-context'),'host did not install Webots routes');assert.equal(guards.length,1);
 let body;const res={setHeader(){},end:text=>{body=JSON.parse(text);}};
 await routes.get('/dsh-engineering-ui/webots-context').handler({method:'GET',url:'/?sessionId=child-1'},res);
 assert.equal(body.ok,true);assert.equal(body.core_ready,false);assert.equal(body.error_code,'RUNTIME_NOT_READY');
 const forbidden={setHeader(){},end:text=>{body=JSON.parse(text);}};
 await routes.get('/dsh-engineering-ui/webots-context').handler({method:'GET',url:'/?sessionId=child-1',headers:{origin:'https://evil.example'}},forbidden);assert.equal(forbidden.statusCode,403);
 dispose();
});

test('a binding revoked while reading cannot return the old owner data',async()=>{
 state('task-1','room-1');const tools=path.join(temp,'revoked','tools');const pkg=path.join(tools,'webots_mcp');fs.mkdirSync(pkg,{recursive:true});
 fs.writeFileSync(path.join(pkg,'__init__.py'),'');fs.writeFileSync(path.join(pkg,'host.py'),'import time,json\ntime.sleep(0.3)\nprint(json.dumps({"ok":True,"data":{"private":"old task"}}))');
 const routes=new Map();const c={...ctx,tools:{guard:()=>()=>{}},webServer:{register:r=>{routes.set(r.path,r);return ()=>{};}}};
 const dispose=mod.installWebots(c,{...options,env:{...env,DSH_WEBOTS_PYTHON:python,DSH_ENGINEERING_ROOT:path.dirname(tools)},guardRoute:r=>r,send:(res,status,body)=>Object.assign(res,{status,body}),queryOf:req=>new URL(req.url,'http://localhost').searchParams});
 const response={};const pending=routes.get('/dsh-engineering-ui/webots-report').handler({method:'GET',url:'/?sessionId=child-1&run_id=run-1&report_type=experiment_report'},response);
 state('task-2','room-1');await pending;assert.equal(response.status,403);assert.equal(response.body.error_code,'CONTEXT_UNAVAILABLE');assert.equal(response.body.data,undefined);dispose();
});
test('host route reads real owned S04 reports and UTF8 pages without upgrading fixture evidence',async()=>{
 state('task-real','room-real');const tools=fileURLToPath(new URL('../../../tools/',import.meta.url));
 const room='room-'+crypto.createHash('sha256').update('room-real').digest('hex');
 const readerEnv={...process.env,...env,DSH_WEBOTS_PYTHON:python,DSH_ENGINEERING_ROOT:path.dirname(tools.replace(/[\\/]$/,'')),PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1'};
 const script='import os;from pathlib import Path;from webots_mcp.evidence import EvidenceStore;'+
  's=EvidenceStore(Path(os.environ["DSH_STATE_DIR"]),task_id="task-real",room_id="'+room+'");'+
  's.write_report(s.new_report("experiment_report","run-real",provenance="synthetic",data={"issues":[{"evidence_level":"suspected","candidate_parts":["bolt1","bolt2"]}],"verdict":{"status":"unknown"}}));s.append_log("run-real","中文日志");'+
  'f=EvidenceStore(Path(os.environ["DSH_STATE_DIR"]),task_id="foreign",room_id="foreign");f.write_report(f.new_report("experiment_report","run-foreign",provenance="synthetic"))';
 const created=spawnSync(python,['-B','-c',script],{cwd:tools,env:readerEnv,encoding:'utf8'});assert.equal(created.status,0,created.stderr);
 const routes=new Map();const c={...ctx,tools:{guard:()=>()=>{}},webServer:{register:r=>{routes.set(r.path,r);return ()=>{};}}};
 const dispose=mod.installWebots(c,{...options,env:readerEnv,guardRoute:r=>r,send:(res,status,body)=>Object.assign(res,{status,body}),queryOf:req=>new URL(req.url,'http://localhost').searchParams});
 const call=async(kind,query)=>{const response={};await routes.get('/dsh-engineering-ui/webots-'+kind).handler({method:'GET',url:'/?sessionId=child-1&'+query},response);return response;};
 let response=await call('report','run_id=run-real&report_type=experiment_report');assert.equal(response.status,200);assert.equal(response.body.core_ready,false);assert.equal(response.body.data.provenance.kind,'synthetic');assert.equal(response.body.data.data.verdict.status,'unknown');assert.deepEqual(response.body.data.data.issues[0].candidate_parts,['bolt1','bolt2']);
 response=await call('evidence','run_id=run-real&limit=6');assert.equal(response.status,200);assert.equal(response.body.data.text,'中文');assert.equal(response.body.data.next_offset,6);
 response=await call('report','run_id=run-foreign&report_type=experiment_report');assert.equal(response.status,403);assert.equal(response.body.error_code,'OWNERSHIP_MISMATCH');assert.equal(response.body.data,undefined);dispose();
});

test('context projection is scoped, expires and never revives unconfigured core',async()=>{
 state('task-1','room-1');
 const routes=new Map(); const c={...ctx,tools:{guard(){return ()=>{};}},webServer:{register(r){routes.set(r.path,r);return ()=>{};}}};
 const cleanup=mod.installWebots(c,{...options,guardRoute:r=>r,send:(res,status,body)=>Object.assign(res,{status,body}),queryOf:req=>new URL(req.url,'http://localhost').searchParams});
 const call=async()=>{const res={};await routes.get('/dsh-engineering-ui/webots-context').handler({method:'GET',url:'/?sessionId=child-1'},res);return res.body;};
 const file=path.join(temp,'webots_state.json');
 const projection={schema_version:1,...resolve(),run_id:'run-1',session_generation:1,provenance:'real-webots',core_ready:true,stale:false,job_state:'READY',snapshot_at:new Date().toISOString(),valid_until:new Date(Date.now()+1500).toISOString(),sim_time:.032,completed_steps:1};
 fs.writeFileSync(file,JSON.stringify(projection)); assert.equal((await call()).core_ready,true);
 fs.writeFileSync(file,JSON.stringify({...projection,valid_until:new Date(0).toISOString()}));const stale=await call();assert.equal(stale.core_ready,false);assert.equal(stale.stale,true);
 fs.writeFileSync(file,JSON.stringify({...projection,room_id:'foreign'}));assert.equal((await call()).core_ready,false);
 fs.unlinkSync(file);cleanup();
});


