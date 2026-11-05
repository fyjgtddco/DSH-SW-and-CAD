// Host context gate; execution remains in the existing MCP server and S04 reader.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
const execFile = promisify(execFileCallback);
const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,95}(?![\s\S])/;
const PREFIX = '/dsh-engineering-ui/';
function objectFile(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1048576) throw Error('Invalid context file');
  const value = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!value || Array.isArray(value) || typeof value !== 'object') throw Error('Invalid context object');
  return value;
}
export function resolveWebotsContext(ctx, agent, options) {
  const env = options.env || process.env;
  const declared = env.DSH_STATE_DIR;
  if (!declared || !path.isAbsolute(declared) || !fs.statSync(declared).isDirectory()) throw Error('Explicit existing state directory required');
  const state = fs.realpathSync(declared);
  if (fs.realpathSync(options.findToolsDir()) !== state) throw Error('State directory mismatch');
  if (!agent?.id || ctx.agents.get(agent.id) !== agent || options.presetOf(agent) !== 'engineering') throw Error('Live engineering agent required');
  const workflow = objectFile(path.join(state, 'workflow_state.json'));
  const mode = objectFile(path.join(state, 'mode_state.json'));
  if (typeof workflow.task_id !== 'string' || !ID.test(workflow.task_id)) throw Error('Current task unavailable');
  const rooms = Object.entries(mode.rooms || {}).filter(([room, record]) =>
    room && record?.active === true && record.task_id === workflow.task_id && mode.subagents?.[room]?.id === agent.id);
  if (rooms.length !== 1) throw Error('Unique active same-task room binding required');
  const room = rooms[0][0];
  return { task_id: workflow.task_id, room_id: 'room-' + crypto.createHash('sha256').update(room, 'utf8').digest('hex'),
    room_label: room, state_dir: state };
}
export function webotsGuard(ctx, options) {
  return exec => {
    if (!exec.name?.startsWith('mcp__webots__')) return;
    try {
      const own = resolveWebotsContext(ctx, exec.agent, options);
      if (exec.arguments?.task_id !== own.task_id || exec.arguments?.room_id !== own.room_id) throw Error('Request ownership mismatch');
    } catch (error) { return 'CONTEXT_UNAVAILABLE: ' + error.message; }
  };
}
// ⚠️ ctx.tools 的优雅降级（本工程既有约束）：
//   · 宿主组合里若没有 @deepseek-ai/dsh-tool-* / tools 服务，ctx.tools 为 undefined；
//   · 插件测试桩、旧宿主也可能没注册 guard。
//   此时【绝不能让整块装配抛异常】—— 那会让 import 求值失败、插件整体不装配，
//   表现为"设置页打不开 / 连接区整块消失"，比缺一个守卫严重得多。
//   降级语义是 fail-closed 的反面吗？不是：没有 tools 服务 = 根本没有 mcp__webots__*
//   工具可执行（MCP 行默认禁用），守卫无事可守；只读路由仍走原有 guardRoute。
export function installWebots(ctx, options) {
  const disposers = [];
  try {
    const guard = ctx && ctx.tools && typeof ctx.tools.guard === 'function'
      ? ctx.tools.guard.bind(ctx.tools)
      : null;
    if (guard) disposers.push(guard(webotsGuard(ctx, options)));
  } catch { /* 守卫不可用：不影响只读路由与插件装配 */ }
  for (const kind of ['context', 'report', 'evidence', 'attribute']) {
    disposers.push(ctx.webServer.register(options.guardRoute({ kind: 'exact', path: PREFIX + 'webots-' + kind,
      handler: async (req, res) => {
        const reply = (status, body) => options.send(res, status, body);
        if (req.method !== 'GET') return reply(405, {ok:false,error_code:'METHOD_NOT_ALLOWED'});
        const query = options.queryOf(req);
        const allowed = ['sessionId', ...(kind === 'context' ? [] : ['run_id']), ...(kind === 'report' ? ['report_type'] : kind === 'evidence' ? ['name','offset','limit','expected_sha256'] : kind === 'attribute' ? ['issue_id'] : [])];
        if ([...query.keys()].some(key => !allowed.includes(key)) || [...query.keys()].some(key => query.getAll(key).length !== 1)) return reply(400,{ok:false,error_code:'INVALID_ARGUMENT'});
        let own;
        try { own = resolveWebotsContext(ctx, ctx.agents.get(query.get('sessionId')), options); }
        catch (error) { return reply(403, {ok:false,error_code:'CONTEXT_UNAVAILABLE',hint:error.message}); }
        const base = { ...own, core_ready:false, execution_state:'not_started', job_state:'NEW', provenance:'unavailable',
          snapshot_at:new Date().toISOString(), stale:true, sim_time:null, completed_steps:null,
          unsupported:['unconfigured_runtime','native_export','native_repair'],
          capabilities:['stored_reports','bounded_evidence','attribution','pinned_runtime','durable_operations'],
          blocked_native:['native_mesh_frame','complete_global_mate_state','native_identity_calibration','converter_physical_precision','calibrated_collision_feedback','authorized_native_repair'] };
        if (kind === 'context') {
          try {
            const s = objectFile(path.join(own.state_dir,'webots_state.json'));
            const stamp = Date.parse(s.snapshot_at), until = Date.parse(s.valid_until), now = Date.now();
            if (s.schema_version !== 1 || s.task_id !== own.task_id || s.room_id !== own.room_id || !ID.test(s.run_id || '')
              || !Number.isSafeInteger(s.session_generation) || s.session_generation < 1) throw Error('Unbound projection');
            const fresh = Number.isFinite(stamp) && Number.isFinite(until) && stamp <= now && until > now && until-stamp <= 3000;
            for (const key of ['run_id','session_generation','job_state','snapshot_at','sim_time','completed_steps','provenance','diagnostic']) if (key in s) base[key]=s[key];
            base.stale = !fresh || s.stale !== false;
            base.core_ready = !base.stale && s.core_ready === true && s.provenance === 'real-webots';
            if (fresh) base.unsupported = ['native_export','native_repair'];
          } catch { /* No verified same-room cached runtime; conservative base remains. */ }
          return reply(200,{...base,ok:true,error_code:base.core_ready ? null : 'RUNTIME_NOT_READY'});
        }
        const number = (key, min, max) => !query.has(key) || (/^[0-9]+(?![\s\S])/.test(query.get(key)) && Number.isSafeInteger(Number(query.get(key))) && Number(query.get(key)) >= min && Number(query.get(key)) <= max);
        if (!ID.test(query.get('run_id') || '') || (kind === 'report' && !['model_manifest','conversion_report','experiment_report'].includes(query.get('report_type')))
          || (kind === 'attribute' && !ID.test(query.get('issue_id') || ''))
          || (query.has('name') && !ID.test(query.get('name'))) || !number('offset',0,Number.MAX_SAFE_INTEGER) || !number('limit',4,16384)
          || (query.has('expected_sha256') && !/^[a-f0-9]{64}(?![\s\S])/.test(query.get('expected_sha256')))) return reply(400,{...base,ok:false,error_code:'INVALID_ARGUMENT'});
        const env = options.env || process.env;
        if (!env.DSH_WEBOTS_PYTHON || !path.isAbsolute(env.DSH_WEBOTS_PYTHON) || !env.DSH_ENGINEERING_ROOT || !path.isAbsolute(env.DSH_ENGINEERING_ROOT)) return reply(503,{...base,ok:false,error_code:'READER_UNAVAILABLE'});
        const args = ['-B','-m','webots_mcp.host',kind,'--task-id',own.task_id,'--room-id',own.room_id];
        for (const key of allowed.filter(key => key !== 'sessionId')) if (query.has(key)) args.push('--' + key.replaceAll('_','-'),query.get(key));
        let result;
        try { result = await execFile(env.DSH_WEBOTS_PYTHON,args,{cwd:path.join(env.DSH_ENGINEERING_ROOT,'tools'),
          env:{...env,DSH_STATE_DIR:own.state_dir,PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1'},encoding:'utf8',windowsHide:true,timeout:15000,maxBuffer:2*1048576}); }
        catch { return reply(503,{...base,ok:false,error_code:'READER_UNAVAILABLE'}); }
        try {
          const current = resolveWebotsContext(ctx, ctx.agents.get(query.get('sessionId')), options);
          if (['task_id','room_id','state_dir'].some(key => current[key] !== own[key])) throw Error('Binding changed during read');
        } catch (error) { return reply(403,{ok:false,error_code:'CONTEXT_UNAVAILABLE',hint:error.message}); }
        try {
          const data = JSON.parse(result.stdout);
          const status = data.ok ? 200 : data.error_code === 'OWNERSHIP_MISMATCH' ? 403 : data.error_code === 'EVIDENCE_NOT_FOUND' ? 404 : 400;
          return reply(status,{...base,...data});
        } catch { return reply(503,{...base,ok:false,error_code:'READER_UNAVAILABLE'}); }
      }
    })));
  }
  return () => { for (const dispose of disposers.reverse()) dispose(); };
}
