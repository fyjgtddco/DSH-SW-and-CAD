const fs = require('fs');
const path = require('path');
process.chdir('C:/Users/j1877/.dsh/profiles/web');
const yaml = require('js-yaml');
const cpi = require('@deepseek-ai/cordis-plugin-include');
const { discoverPresets } = require('@deepseek-ai/dsh-agent-presets/lib/discovery.js');
const hp = require('@deepseek-ai/dsh-home-paths');

const presetPath = 'C:/Users/j1877/.dsh/.agent-presets/engineering/agent.cordis.yml';
const content = fs.readFileSync(presetPath, 'utf8');

try {
  const rows = yaml.load(content, { schema: cpi.entryListSchema });
  console.log('OK: parsed', Array.isArray(rows) ? rows.length + ' entries' : 'not array');
} catch(e) {
  console.error('PARSE FAILED:', e.message.split('\n')[0]);
}

(async () => {
  const roots = [{ path: hp.dshHomePath('.agent-presets'), trust: 'user' }];
  const presets = await discoverPresets(roots);
  console.log('Discovered:', presets.length);
  for (const p of presets) {
    console.log(' ', p.id, '| broken:', !!p.broken, '| name:', p.name);
  }
})().catch(e => console.error('Discovery ERR:', e.message));
