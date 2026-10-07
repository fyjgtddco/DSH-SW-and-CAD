const fs = require('fs');
const path = require('path');
const sessionsDir = 'C:/Users/j1877/.dsh/sessions';
const dirs = fs.readdirSync(sessionsDir).sort().reverse().slice(0, 8);
for (const d of dirs) {
  const fp = path.join(sessionsDir, d, 'session.jsonl');
  if (!fs.existsSync(fp)) continue;
  const lines = fs.readFileSync(fp, 'utf8').split('\n').filter(Boolean);
  // Find the last few entries
  for (let i = Math.max(0, lines.length - 3); i < lines.length; i++) {
    try {
      const obj = JSON.parse(lines[i]);
      if (obj.agentPreset || obj.type === 'session:start') {
        console.log(d, '| agentPreset:', obj.agentPreset, '| type:', obj.type);
      }
    } catch(e) {}
  }
}
