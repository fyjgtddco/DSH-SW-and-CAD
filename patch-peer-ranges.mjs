/**
 * patch-peer-ranges.mjs — widen a DSH plugin's declared DSH peer ranges so it
 * passes DSH 0.2.0's bundle version gate.
 *
 * WHY THIS IS THE WHOLE FIX
 *   app-boot refuses (skips) a bundle whose peerDependencies name
 *   `@deepseek-ai/dsh` or `@deepseek-ai/dsh-*` with a range the running
 *   runtime does not satisfy. That check reads the DECLARATION, not the code.
 *   When the plugin's code references only packages the runtime still ships,
 *   a too-narrow declaration is the only real incompatibility — widening it is
 *   a one-line, behaviour-preserving change.
 *
 * WHAT IT DOES
 *   For every `@deepseek-ai/dsh*` key in peerDependencies whose current range
 *   does NOT satisfy <runtime>, append ` || ^<runtime>` — keeping the original
 *   branches intact so the plugin still loads on the older runtime it declared.
 *   Nothing else in the file is touched: the edit is a literal string
 *   replacement of that one JSON value.
 *
 * USAGE
 *   node patch-peer-ranges.mjs <path/to/package.json> <runtimeVersion> [--check]
 *     --check  report only, write nothing
 *
 * NOTE  `^0.2.0` does NOT match `0.2.0-rc.2` (a prerelease sorts below its
 *       release), so the appended range must name the prerelease itself.
 *       Verified with the semver build DSH ships.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const SEMVER_CANDIDATES = [
  'Z:\\DSH\\resources\\runtime\\pnpm\\dist\\node_modules\\semver',
  'C:\\Program Files\\nodejs\\node_modules\\npm\\node_modules\\semver',
];
const req = createRequire(import.meta.url);
let semver = null;
let semverFrom = '';
for (const c of SEMVER_CANDIDATES) {
  try { semver = req(c); semverFrom = c; break; } catch { /* next */ }
}
if (!semver) {
  try { semver = req('semver'); semverFrom = 'semver'; } catch { /* fall through */ }
}

const [file, runtime, ...flags] = process.argv.slice(2);
const checkOnly = flags.includes('--check');

if (!file || !runtime) {
  console.error('usage: node patch-peer-ranges.mjs <package.json> <runtimeVersion> [--check]');
  process.exit(2);
}
if (!semver) {
  console.error('semver not found; looked at:\n  ' + SEMVER_CANDIDATES.join('\n  '));
  process.exit(3);
}

const original = fs.readFileSync(file, 'utf8');
const pkg = JSON.parse(original.replace(/^\uFEFF/, ''));
const peers = pkg.peerDependencies || {};
const DSH_KEY = k => k === '@deepseek-ai/dsh' || k.startsWith('@deepseek-ai/dsh-');

let text = original;
const changes = [];
for (const key of Object.keys(peers)) {
  if (!DSH_KEY(key)) continue;
  const range = peers[key];
  let ok = true;
  try { ok = semver.satisfies(runtime, range, { includePrerelease: true }); } catch { ok = false; }
  if (ok) {
    console.log('  keep    ' + key + ': "' + range + '"   (already satisfies ' + runtime + ')');
    continue;
  }
  const widened = range.trim() + ' || ^' + runtime;
  // literal, JSON-quoted replacement of that exact value only
  const needle = JSON.stringify(key) + ':' + JSON.stringify(range);
  const needleSpaced = JSON.stringify(key) + ': ' + JSON.stringify(range);
  let next;
  if (text.includes(needleSpaced)) next = text.replace(needleSpaced, JSON.stringify(key) + ': "' + widened + '"');
  else if (text.includes(needle)) next = text.replace(needle, JSON.stringify(key) + ':' + JSON.stringify(widened));
  else {
    console.log('  SKIP    ' + key + ': could not locate the literal value; edit by hand');
    continue;
  }
  text = next;
  changes.push({ key, from: range, to: widened });
  console.log('  widened ' + key + ':\n            - ' + range + '\n            + ' + widened);
}

if (!changes.length) {
  console.log('  nothing to do (already compatible, or no DSH peers)');
  process.exit(0);
}
if (checkOnly) {
  console.log('  --check: no write');
  process.exit(0);
}
fs.writeFileSync(file, text, 'utf8');   // UTF-8, no BOM
console.log('  wrote ' + file + '   (' + changes.length + ' range(s) widened; semver from ' + semverFrom + ')');
