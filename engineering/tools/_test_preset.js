const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const cpi = require('./node_modules/@deepseek-ai/cordis-plugin-include/lib/index.js');

const presetPath = 'C:/Users/j1877/.dsh/.agent-presets/engineering/agent.cordis.yml';
const content = fs.readFileSync(presetPath, 'utf8');

// Test 1: parse with entryListSchema (what DSH actually uses)
try {
  const rows = yaml.load(content, { schema: cpi.entryListSchema });
  console.log('OK with entryListSchema:', Array.isArray(rows) ? rows.length + ' entries' : 'not array');
} catch(e) {
  console.error('PARSE FAILED with entryListSchema:', e.message.split('\n')[0]);
  const lines = e.message.split('\n');
  for (const l of lines.slice(0, 5)) console.log('  ', l);
}

// Test 2: check what !!js tags exist
const jsTagMatches = content.match(/!!js\s+[^\n]*/g);
console.log('!!js tags found:', jsTagMatches ? jsTagMatches.length : 0);
if (jsTagMatches) {
  for (const t of jsTagMatches.slice(0, 5)) {
    console.log('  ', t.trim().substring(0, 80));
  }
}

// Test 3: discover presets
const { discoverPresets } = require('./node_modules/@deepseek-ai/dsh-agent-presets/lib/discovery.js');
const hp = require('./node_modules/@deepseek-ai/dsh-home-paths/lib/index.js');
(async () => {
  const roots = [{ path: hp.dshHomePath('.agent-presets'), trust: 'user' }];
  const presets = await discoverPresets(roots);
  console.log('\nDiscovered presets:', presets.length);
  for (const p of presets) {
    console.log('  ', p.id, '| broken:', !!p.broken, '| name:', p.name, '| trust:', p.trust);
    if (p.broken) console.log('    reason:', p.broken);
  }
})().catch(e => console.error('Discovery error:', e.message));
