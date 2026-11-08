/**
 * task-workspace.test.mjs —— 【目录分层 + 收尾清理 + 零件进度表】回归
 *
 * 对应用户需求（2026-11-07）：
 *   ① 「一堆奇奇怪怪的文件夹，包含一些奇奇怪怪的 py 文档、json 文档……
 *      让主对话最后收尾的时候，直接删去那些奇奇怪怪的文件和失败的建模文档」
 *   ② 「两个文件夹：一个放 py/json 这类和建模没关系的，一个放零件；
 *      建模失败的文件直接不用保存」

 * 这里只校验【能纯静态/纯 Python 验证】的部分：
 *   · Python 模块 task_workspace 的分层与清理语义（真跑，用临时目录）
 *   · 插件侧接线：init 建骨架、finish 清理、失败件进 parts_rejects
 * 状态的"活体"渲染由 subagent-panel.test.mjs / hooks-order.test.mjs 覆盖。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * 定位 tools/ 目录。
 *
 * ⚠️ 不能写死 path.join(HERE,'..','..','..')：
 *   仓库布局是 <repo>/engineering/plugins/dsh-engineering-ui/test
 *     → 上三级正好是 <repo>/engineering，下有 tools/ ✅
 *   但【安装副本】是 ~/.dsh/profiles/<p>/node_modules/dsh-engineering-ui/test
 *     → 上三级是 node_modules，下面没有 tools ✗
 * 所以依次尝试：环境变量 → 各级祖先里的 tools/ → 安装副本的 .agent-presets。
 */
function findTools() {
  const cands = [];
  if (process.env.DSH_ENGINEERING_ROOT) {
    cands.push(path.join(process.env.DSH_ENGINEERING_ROOT, 'tools'));
    cands.push(process.env.DSH_ENGINEERING_ROOT);
  }
  let cur = HERE;
  for (let i = 0; i < 6; i++) {
    cands.push(path.join(cur, 'tools'));
    cands.push(path.join(cur, 'engineering', 'tools'));
    const up = path.dirname(cur);
    if (up === cur) break;
    cur = up;
  }
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) cands.push(path.join(home, '.dsh', '.agent-presets', 'engineering', 'tools'));
  for (const c of cands) {
    try {
      if (existsSync(path.join(c, 'task_workspace.py'))) return c;
    } catch (e) { /* 继续 */ }
  }
  return null;
}

const TOOLS = findTools();
const REPO = TOOLS ? path.dirname(TOOLS) : path.join(HERE, '..', '..', '..');
const TW = TOOLS ? path.join(TOOLS, 'task_workspace.py') : '';

function python() {
  for (const c of ['py', 'python', 'python3']) {
    const r = spawnSync(c, ['-c', 'import sys;print(sys.version_info[0])'], { encoding: 'utf8' });
    if (r.status === 0 && String(r.stdout).trim() === '3') return c;
  }
  return null;
}
const PY = python();

const SKIP = TOOLS ? false : true;
if (SKIP) console.log('[SKIP] 找不到 tools/task_workspace.py（用 DSH_ENGINEERING_ROOT 指定工程模式根目录）');

test('① task_workspace 模块存在且能被 Python 语法解析', () => {
  if (SKIP) { console.log('[SKIP] 无 tools 目录'); return; }
  assert.ok(existsSync(TW), '缺少 tools/task_workspace.py');
  if (!PY) { console.log('[SKIP] 本机没有可用的 python3'); return; }
  const r = spawnSync(PY, ['-c',
    'import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding="utf-8").read());print("OK")', TW],
    { encoding: 'utf8' });
  assert.equal(String(r.stdout).trim(), 'OK', String(r.stderr).slice(0, 400));
});

test('② 分层：零件→parts/ 工程图→drawings/ 脚本与 json→intermediate/，状态文件不动', () => {
  if (!PY) { console.log('[SKIP] 无 python'); return; }
  const wd = mkdtempSync(path.join(tmpdir(), 'tw-'));
  try {
    const script = [
      'import io, json, os, sys',
      'sys.path.insert(0, sys.argv[1])',
      'import task_workspace as tw',
      'wd = sys.argv[2]',
      'for n, c in [("build_arm.py","x"),("mode2_plan.json","{}"),("DSH_大臂.SLDPRT","p"),',
      '             ("DSH_大臂.SLDDRW","d"),("validation_calc.json","{}")]:',
      '    io.open(os.path.join(wd, n), "w", encoding="utf-8").write(c)',
      'r = tw.tidy(wd)',
      'print(json.dumps({"ok": r["ok"], "root": sorted(os.listdir(wd)),',
      '                  "parts": sorted(os.listdir(os.path.join(wd, "parts"))),',
      '                  "drawings": sorted(os.listdir(os.path.join(wd, "drawings"))),',
      '                  "inter": sorted(os.listdir(os.path.join(wd, "intermediate")))},',
      '                 ensure_ascii=False))',
    ].join('\n');
    // ⚠️ 文件名含中文：必须强制 UTF-8 输出 + 用 JSON 解析，
    //   否则 Windows 控制台代码页会把 '大臂' 变成 ???（测试假失败）。
    const r = spawnSync(PY, ['-c', script, TOOLS, wd], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    assert.equal(r.status, 0, String(r.stderr).slice(0, 600));
    const j = JSON.parse(String(r.stdout).trim().split('\n').pop());
    assert.equal(j.ok, true);
    assert.deepEqual(j.parts, ['DSH_大臂.SLDPRT'], '零件必须进 parts/');
    assert.deepEqual(j.drawings, ['DSH_大臂.SLDDRW'], '工程图必须进 drawings/');
    assert.ok(j.inter.includes('build_arm.py'), '脚本必须进 intermediate/');
    assert.ok(j.inter.includes('validation_calc.json'), '无关 json 必须进 intermediate/');
    assert.ok(j.root.includes('mode2_plan.json'),
      '★ mode2_plan.json 是运行时台账，绝不能被搬走（搬走=门禁全崩）');
  } finally {
    rmSync(wd, { recursive: true, force: true });
  }
});

test('③ 清理：只删 intermediate/ 与 parts_rejects/，交付物原样保留 + 写清单', () => {
  if (!PY) { console.log('[SKIP] 无 python'); return; }
  const wd = mkdtempSync(path.join(tmpdir(), 'twc-'));
  try {
    const script = [
      'import io, json, os, sys',
      'sys.path.insert(0, sys.argv[1])',
      'import task_workspace as tw',
      'wd = sys.argv[2]',
      'tw.ensure_layout(wd)',
      'io.open(os.path.join(wd, "parts", "A.SLDPRT"), "w").write("p")',
      'io.open(os.path.join(wd, "drawings", "A.SLDDRW"), "w").write("d")',
      'io.open(os.path.join(wd, "intermediate", "x.json"), "w").write("{}")',
      'io.open(os.path.join(wd, "parts_rejects", "BAD.SLDPRT"), "w").write("b")',
      'r = tw.cleanup(wd)',
      'print(json.dumps({"ok": r["ok"], "root": sorted(os.listdir(wd)),',
      '                  "parts": sorted(os.listdir(os.path.join(wd, "parts"))),',
      '                  "manifest": os.path.exists(os.path.join(wd, "reports", "cleanup_manifest.json"))},',
      '                 ensure_ascii=False))',
    ].join('\n');
    const r = spawnSync(PY, ['-c', script, TOOLS, wd], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    assert.equal(r.status, 0, String(r.stderr).slice(0, 600));
    const j = JSON.parse(String(r.stdout).trim().split('\n').pop());
    assert.equal(j.ok, true);
    assert.deepEqual(j.parts, ['A.SLDPRT'], '交付零件必须保留');
    assert.deepEqual(j.root.sort(), ['drawings', 'parts', 'reports'],
      '清理后任务目录只剩 parts/ drawings/ reports/');
    assert.equal(j.manifest, true, '清理必须写 reports/cleanup_manifest.json 留痕');
  } finally {
    rmSync(wd, { recursive: true, force: true });
  }
});

test('④ 失败件：reject_part 把半成品挪进 parts_rejects/（不直接删）', () => {
  if (!PY) { console.log('[SKIP] 无 python'); return; }
  const wd = mkdtempSync(path.join(tmpdir(), 'twr-'));
  try {
    const script = [
      'import io, json, os, sys',
      'sys.path.insert(0, sys.argv[1])',
      'import task_workspace as tw',
      'wd = sys.argv[2]',
      'tw.ensure_layout(wd)',
      'p = os.path.join(wd, "parts", "BAD.SLDPRT")',
      'io.open(p, "w").write("half")',
      'r = tw.reject_part(wd, p, reason="save failed")',
      'print(json.dumps({"ok": r["ok"], "moved": os.path.exists(r.get("to") or ""),',
      '                  "still_in_parts": os.path.exists(p)}, ensure_ascii=False))',
    ].join('\n');
    const r = spawnSync(PY, ['-c', script, TOOLS, wd], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    assert.equal(r.status, 0, String(r.stderr).slice(0, 600));
    const j = JSON.parse(String(r.stdout).trim().split('\n').pop());
    assert.equal(j.ok, true);
    assert.equal(j.moved, true, '失败件必须被移入 parts_rejects/');
    assert.equal(j.still_in_parts, false, 'parts/ 里不得残留失败件');
  } finally {
    rmSync(wd, { recursive: true, force: true });
  }
});

test('⑤ 接线：init 建骨架 / finish 清理 / 保存失败进 rejects / 零件表有路由', () => {
  if (SKIP) { console.log('[SKIP] 无 tools 目录'); return; }
  const wg = readFileSync(path.join(TOOLS, 'workflow_gate.py'), 'utf8');
  assert.ok(wg.includes('task_workspace as _tw') && wg.includes('_tw.ensure_layout(wd)'),
    'workflow_gate.init 必须用 task_workspace 建目录骨架');
  assert.ok(wg.includes('_tw.cleanup(_wd)'), 'workflow_gate 收尾必须调 cleanup');
  assert.ok(wg.includes('"cleanup": _cleanup'), '收尾返回里必须带上清理结果');

  const sw = readFileSync(path.join(TOOLS, 'sw_bridge.py'), 'utf8');
  assert.ok(sw.includes('_reject_failed_save'), 'sw_bridge 保存失败必须移入 parts_rejects');

  const swapi = readFileSync(path.join(TOOLS, 'swapi.py'), 'utf8');
  assert.ok(swapi.includes('_reject_failed_part'),
    'swapi.save/save_as 失败必须走同一个 reject 逻辑');

  const m2 = readFileSync(path.join(TOOLS, 'mode2_flow.py'), 'utf8');
  assert.ok(m2.includes('def parts_board('), 'mode2_flow 必须有 parts_board');
  // 状态常量 → 状态码的映射必须齐全（用户要求的五种）
  for (const st of ['done', 'building', 'rebuilding', 'failed', 'not_started']) {
    assert.ok(m2.includes('"' + st + '"'),
      '零件状态表必须包含状态码 ' + st);
  }
  for (const constName of ['PART_STATE_DONE', 'PART_STATE_BUILDING', 'PART_STATE_REBUILDING',
                           'PART_STATE_FAILED', 'PART_STATE_TODO']) {
    assert.ok(m2.includes(constName + ' ='), '缺少状态常量 ' + constName);
  }
  assert.ok(m2.includes('PART_STATE_LABEL'), '必须有状态中文标签表');

  const host = readFileSync(path.join(HERE, '..', 'lib', 'index.js'), 'utf8');
  assert.ok(host.includes("PREFIX + '/parts-board'"), '宿主必须有 /parts-board 路由');

  const client = readFileSync(path.join(HERE, '..', 'lib', 'client.js'), 'utf8');
  assert.ok(client.includes('function EngPartsBoard'), '客户端必须有零件进度表组件');
  assert.ok(client.includes('eng-parts-board'), '零件表必须有独立样式类');
  // 【2026-11-07 变更】用户要求把零件表「整体平移到黑框（左列下半区）」，
  //   因此挂载点从右侧详情区改为 .eng-lower，且带 compact（窄列渲染）。
  assert.ok(client.includes('h(EngPartsBoard, { compact: true })'),
    '零件表必须真的挂到左列下半区（compact 模式）');
  assert.ok(client.includes("className: 'eng-lower'"),
    '零件表必须位于左列下半区容器内');
  // 状态→徽标配色映射必须与 Python 的五个状态一一对应
  for (const k of ['done', 'building', 'rebuilding', 'failed', 'not_started']) {
    assert.ok(client.includes(k + ":"), '客户端状态映射缺少 ' + k);
  }
  // 删失败件前要不要问用户（A=不问，B/C=问）
  assert.ok(m2.includes('can_delete_without_asking'), 'mode2_flow 必须有删件策略判定');
  assert.ok(m2.includes('delete-policy'), '必须有 delete-policy 命令');
  assert.ok(m2.includes('may_delete_silently'), '返回值必须显式给出能否不问就删');
  // 骨架里必须建 intermediate / parts_rejects
  const tw = readFileSync(path.join(TOOLS, 'task_workspace.py'), 'utf8');
  assert.ok(tw.includes('"intermediate"') && tw.includes('"parts_rejects"'),
    '目录骨架必须含 intermediate/ 与 parts_rejects/');
  assert.ok(tw.includes('KEEP_FILES'), '必须保护 mode2_plan 等运行时状态文件');
});
