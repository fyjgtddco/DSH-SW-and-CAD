import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const yaml=require('../../../../third-party-patched/dshmarket/node_modules/js-yaml');
const expression=new yaml.Type('tag:yaml.org,2002:js',{kind:'scalar',construct:value=>({__jsExpr:value})});
const source=fs.readFileSync(new URL('../preset-engineering.patch.yml',import.meta.url),'utf8');
const parsed=yaml.load(source,{schema:yaml.DEFAULT_SCHEMA.extend([expression])});
function entry(env) {
 const preset=parsed[0].insert.find(x=>x.id==='preset-engineering');
 const raw=preset.config.plugins.find(x=>x.name==='@deepseek-ai/dsh-mcp-client');
 assert.ok(raw,'engineering preset must provide optional MCP entry');
 function resolve(value){ if(value && typeof value==='object'){if('__jsExpr' in value)return JSON.parse(JSON.stringify(vm.runInNewContext(value.__jsExpr,{process:{env}}))); if(Array.isArray(value))return value.map(resolve);return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,resolve(v)]));}return value; }
 return resolve(raw);
}
test('optional preset resolves explicit isolated transport and never carries shared task/room identity',()=>{
 const env={DSH_WEBOTS_ENABLE:'1',DSH_WEBOTS_PYTHON:'C:/isolated/python.exe',DSH_ENGINEERING_ROOT:'C:/isolated/engineering',DSH_HOME:'C:/isolated/dsh',DSH_STATE_DIR:'C:/isolated/state',DSH_WEBOTS_INPUT_ROOT:'C:/isolated/models',WEBOTS_HOME:'D:/Webots'};
 const row=entry(env);assert.equal(row.disabled,false); const c=row.config;
 assert.equal(c.transport,'stdio');assert.equal(c.serverName,'webots');assert.equal(c.command,env.DSH_WEBOTS_PYTHON);assert.equal(c.cwd,'C:/isolated/engineering/tools');
 assert.deepEqual(c.args,['-B','-m','webots_mcp.server','--input-root','C:/isolated/models']);assert.equal(c.failOnStartupError,false);
 assert.equal(c.env.DSH_STATE_DIR,env.DSH_STATE_DIR);assert.equal(c.env.DSH_HOME,env.DSH_HOME);assert.equal(c.env.DSH_ENGINEERING_ROOT,env.DSH_ENGINEERING_ROOT);assert.equal(c.env.WEBOTS_HOME,'D:/Webots');assert.equal(c.env.PYTHONIOENCODING,'utf-8');
 assert.equal('task_id' in c.env,false);assert.equal('room_id' in c.env,false);assert.equal('DSH_SW_LOCK_OFF' in c.env,false);
 assert.equal(entry({}).disabled,true);
});

test('operator runtime config is passed only as a separate explicit argument',()=>{
 const env={DSH_WEBOTS_ENABLE:'1',DSH_WEBOTS_RUNTIME_CONFIG:'C:/isolated/runtime.json'};
 const row=entry(env); assert.deepEqual(row.config.args.slice(-2),['--runtime-config',env.DSH_WEBOTS_RUNTIME_CONFIG]);
 assert.equal(entry({DSH_WEBOTS_ENABLE:'1'}).config.args.includes('--runtime-config'),false);
});

