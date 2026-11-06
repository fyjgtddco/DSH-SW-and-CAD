/**
 * mem-guard.js — 【内存守卫】子代理并发限流 + 只回收工作集（不杀进程、不打断流程）
 * ==========================================================================
 * 为什么要有这个模块（两件事，一个来源）
 *   ① 多屋并行时主对话会一口气 subagent_fork 出 8~12 个小屋：每个小屋是
 *      一个独立 Agent + 独立上下文档，同时跑 SolidWorks 时把测试机内存打爆；
 *   ② 用户要求「最多同时 3 个子代理」，且「清理系统运行内存但不要影响流程」。
 *
 * 结论（也是本模块的设计红线）
 *   · 唯一安全的「清理内存」手段是 EmptyWorkingSet：它把进程工作集里的干净页
 *     换出到 standby / pagefile，进程继续存活，下次访问时由缺页中断换回来 ——
 *     **不杀进程、不断会话、不丢上下文**。
 *   · 绝不使用杀 SLDWORKS / 杀 node / 清 standby list 这类会破坏流程的手段。
 *     杀 SW 已由 sw_guard 负责（只在 CPU/内存 > 85% 的极端情况触发），
 *     本模块只做低风险回收。
 *   · 任何一步失败都静默降级（返回 ok:false + 原因），绝不抛、绝不影响主流程。
 *
 * 两条能力
 *   A. 并发闸门：evaluateConcurrency() 是纯函数，输入 subagents.listDescendants
 *      的结果，输出是否允许再 fork。真正的拦截点在 index.js 的 tools/pre-execute。
 *   B. 工作集回收：reclaimWorkingSets() 只对本进程 + 会话相关进程调用
 *      EmptyWorkingSet；memorySnapshot() 用 kernel32.GlobalMemoryStatusEx 读真实
 *      可用内存，供阈值判定与报告。
 *
 * 环境开关（写错一律回落默认，绝不因配置把功能关死）
 *   DSH_ENG_MEM_GUARD=0             → 关闭整个内存守卫（并发闸门 + 回收都停）
 *   DSH_ENG_MAX_SUBAGENTS=3         → 并发子代理上限（默认 3，范围 1..12）
 *   DSH_ENG_MEM_RECLAIM_PCT=85      → 内存占用超过该百分比才做工作集回收
 *   DSH_ENG_MEM_RECLAIM_MS=90000    → 两次回收的最小间隔
 *
 * 依赖：只用 Node 内置模块（child_process）。EmptyWorkingSet 通过 PowerShell
 *   一行 Add-Type 调 psapi.dll —— 不引入任何第三方包，宿主缺包也不会整块挂掉。
 */

import { spawnSync } from 'node:child_process';

/** 默认并发上限（用户明确要求：最多同时开 3 个子代理）。 */
export const DEFAULT_MAX_SUBAGENTS = 3;
/** 上限的硬边界：再大就等于没有闸门。 */
const MAX_SUBAGENTS_CAP = 12;
/** 默认内存占用阈值（百分比）。 */
const DEFAULT_RECLAIM_PCT = 85;
/** 两次回收的最小间隔。 */
const DEFAULT_RECLAIM_MS = 90000;

/** 守卫是否启用（默认启用；显式 0/false/off/no 才关闭）。 */
export function memGuardEnabled() {
  try {
    const raw = process.env.DSH_ENG_MEM_GUARD;
    if (raw === undefined || raw === null || String(raw).trim() === '') return true;
    const v = String(raw).trim().toLowerCase();
    return !(v === '0' || v === 'false' || v === 'off' || v === 'no');
  } catch (e) {
    return true;
  }
}

/**
 * 运行期覆盖（由宿主从 tools/mem_guard.json 读出来后注入）。
 * 优先级：运行期覆盖 > 环境变量 > 内置默认。
 * 之所以留这一层：设置页改完要【立刻生效】，而环境变量在宿主进程里改不动。
 */
let RUNTIME_OVERRIDES = {};

/** 注入运行期覆盖（宿主在装配时调用；非法值一律忽略）。 */
export function setMemGuardOverrides(obj) {
  RUNTIME_OVERRIDES = (obj && typeof obj === 'object') ? { ...obj } : {};
}

/** 读当前运行期覆盖（只读副本，供状态路由回显）。 */
export function memGuardOverrides() {
  return { ...RUNTIME_OVERRIDES };
}

/** 取一个数：运行期覆盖 → 环境变量 → 默认。 */
function pickInt(key, envName, fallback, min, max) {
  const over = RUNTIME_OVERRIDES ? RUNTIME_OVERRIDES[key] : undefined;
  if (over !== undefined && over !== null && String(over).trim() !== '') {
    const n = Number(String(over).trim());
    if (Number.isFinite(n)) {
      const v = Math.floor(n);
      if (v >= min) return Math.min(max, v);
    }
  }
  return positiveInt(process.env[envName], fallback, min, max);
}

function positiveInt(raw, fallback, min, max) {
  try {
    if (raw === undefined || raw === null || String(raw).trim() === '') return fallback;
    const v = Number(String(raw).trim());
    if (!Number.isFinite(v)) return fallback;
    const n = Math.floor(v);
    if (n < min) return fallback;
    return Math.min(max, n);
  } catch (e) {
    return fallback;
  }
}

/** 并发子代理上限（默认 3）。 */
export function maxActiveSubagents() {
  return pickInt('max_subagents', 'DSH_ENG_MAX_SUBAGENTS', DEFAULT_MAX_SUBAGENTS, 1, MAX_SUBAGENTS_CAP);
}

/** 触发工作集回收的内存占用百分比。 */
export function reclaimThresholdPct() {
  return pickInt('reclaim_threshold_pct', 'DSH_ENG_MEM_RECLAIM_PCT', DEFAULT_RECLAIM_PCT, 50, 99);
}

/** 两次回收的最小间隔（毫秒）。 */
export function reclaimIntervalMs() {
  return pickInt('reclaim_interval_ms', 'DSH_ENG_MEM_RECLAIM_MS', DEFAULT_RECLAIM_MS, 5000, 3600000);
}

/**
 * 该 root 当前「占着并发名额」的子代理 id 列表。
 *
 * ── 为什么统计孙代（实测坑）────────────────────────────────────────
 * DSH 的子代理名额由每个 root 的 ActivationPool 统一计数，且只有子代理真正
 * 结束（自然收敛或被 dispose）才释放。主对话 fork 出来、已经跑完但仍驻留的
 * 小屋照样占名额 —— 只数「自己的直接子代理」会低估，于是闸门看起来放行了，
 * 实际 fork 仍会被宿主拒绝（ACTIVATION_LIMIT_REACHED）。
 *
 * @param rows subagents.listDescendants(rootId) 的返回值
 * @param rootId root 会话 id
 */
export function countOccupiedChildren(rows, rootId) {
  const out = [];
  const list = Array.isArray(rows) ? rows : [];
  for (const row of list) {
    if (!row || row.kind !== 'child' || !row.id) continue;
    if (String(row.parentId || '') === String(rootId || '')) { out.push(String(row.id)); continue; }
    if (row.hasChildren === true) out.push(String(row.id));
  }
  return { ids: out, hasChildren: list.some((r) => r && r.kind === 'child') };
}

/**
 * 并发闸门判定（纯函数，便于单测）。
 *
 * @param rows listDescendants 结果
 * @param rootId 当前 root 会话 id
 * @param _agentId 想 fork 的那个 agent id（保留参数，便于日志）
 * @param max 上限
 */
export function evaluateConcurrency(rows, rootId, _agentId, max) {
  const limit = Number.isFinite(max) && max > 0 ? max : DEFAULT_MAX_SUBAGENTS;
  const active = countOccupiedChildren(rows, rootId).ids.length;
  if (active < limit) return { allowed: true, active, max: limit };
  const message = '子代理并发已达上限（当前 ' + active + ' / 上限 ' + limit + '），本次 subagent_fork 已被拒绝。'
    + '\n【为什么】每个子代理都常驻占用内存与一个 DSH 子代理名额；同批开太多会把内存打爆，整个工程模式一起卡死。'
    + '上限可用 DSH_ENG_MAX_SUBAGENTS 调整（默认 3）。'
    + '\n【现在怎么做】先等一个已在跑的小屋结束（或对它 room-fail / interrupt_agent 收尾），'
    + '再用 mode_gate.py room-board 确认名额释放，然后重新 fork。'
    + '\n【不要】把多的零件塞给同一个房间硬编 —— 那会让该房间的报告与门禁口径对不上。';
  return { allowed: false, active, max: limit, reason: 'SUBAGENT_LIMIT_REACHED', message };
}

/** 会话相关进程：自己 + DSH 宿主 + SolidWorks。 */
export function targetProcessNames() {
  return ['node.exe', 'SLDWORKS.exe', 'DeepSeek Harness.exe'];
}

/**
 * 生成读内存 + 回收工作集的 PowerShell 脚本。
 *
 * 只用 psapi.EmptyWorkingSet：把工作集里的干净页移出（standby），进程继续运行；
 * 不做 SetProcessWorkingSetSize(-1,-1) 的硬修剪（x64 上参数会被截断，且会强制
 * 把整个工作集刷到 pagefile，反而拖慢下一次访问）。
 *
 * @param pids 目标进程 id 列表（空数组 = 只读内存，不回收）
 */
export function buildWorkingSetScript(pids) {
  const list = Array.isArray(pids)
    ? pids.map((n) => Number(n)).filter((n) => Number.isFinite(n) && n > 0)
    : [];
  const arr = list.length ? list.join(',') : '';
  return [
    '$ErrorActionPreference = "SilentlyContinue"',
    'Add-Type -Namespace DshMem -Name Native -MemberDefinition @"',
    '[DllImport("kernel32.dll", SetLastError=true)] public static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX lpBuffer);',
    '[DllImport("psapi.dll", SetLastError=true)] public static extern bool EmptyWorkingSet(IntPtr hProcess);',
    '[DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);',
    '[DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr h);',
    '[StructLayout(LayoutKind.Sequential)] public struct MEMORYSTATUSEX {',
    '  public uint dwLength; public uint dwMemoryLoad;',
    '  public ulong ullTotalPhys; public ulong ullAvailPhys;',
    '  public ulong ullTotalPageFile; public ulong ullAvailPageFile;',
    '  public ulong ullTotalVirtual; public ulong ullAvailVirtual;',
    '  public ulong ullAvailExtendedVirtual;',
    '}',
    '"@',
    '$m = New-Object DshMem.Native+MEMORYSTATUSEX',
    '$m.dwLength = [System.Runtime.InteropServices.Marshal]::SizeOf($m)',
    '[void][DshMem.Native]::GlobalMemoryStatusEx([ref]$m)',
    '$before = [pscustomobject]@{',
    '  load = [int]$m.dwMemoryLoad',
    '  total_mb = [math]::Round($m.ullTotalPhys / 1MB)',
    '  free_mb = [math]::Round($m.ullAvailPhys / 1MB)',
    '}',
    '$ok = 0',
    '$failed = 0',
    arr ? ('$targets = @(' + arr + ')') : '$targets = @()',
    'foreach ($p in $targets) {',
    '  $h = [DshMem.Native]::OpenProcess(0x1500, $false, [uint32]$p)',
    '  if ($h -eq [IntPtr]::Zero) { $failed++; continue }',
    '  try { if ([DshMem.Native]::EmptyWorkingSet($h)) { $ok++ } else { $failed++ } }',
    '  finally { [void][DshMem.Native]::CloseHandle($h) }',
    '}',
    '[void][DshMem.Native]::GlobalMemoryStatusEx([ref]$m)',
    '$after = [pscustomobject]@{',
    '  load = [int]$m.dwMemoryLoad',
    '  total_mb = [math]::Round($m.ullTotalPhys / 1MB)',
    '  free_mb = [math]::Round($m.ullAvailPhys / 1MB)',
    '}',
    '[pscustomobject]@{ before = $before; after = $after; reclaimed = $ok; failed = $failed } | ConvertTo-Json -Compress -Depth 4',
  ].join('\n');
}

/** PowerShell 可能带警告行，取最后一段 JSON。 */
export function parseLastJson(text) {
  const s = String(text || '').trim();
  if (!s) return null;
  try { return JSON.parse(s); } catch (e) { /* 继续 */ }
  const i = s.indexOf('{');
  const j = s.lastIndexOf('}');
  if (i >= 0 && j > i) {
    try { return JSON.parse(s.slice(i, j + 1)); } catch (e) { return null; }
  }
  return null;
}

/**
 * 只读内存快照（不回收）。失败时返回 ok:false，绝不抛。
 */
export function memorySnapshot() {
  try {
    const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', buildWorkingSetScript([])], {
      encoding: 'utf8', windowsHide: true, timeout: 15000,
    });
    if (!r || typeof r.stdout !== 'string' || !r.stdout.trim()) {
      return { ok: false, error: String((r && r.stderr) || 'powershell 无输出').slice(0, 400) };
    }
    const j = parseLastJson(r.stdout);
    if (!j || !j.before) return { ok: false, error: '内存快照解析失败' };
    return { ok: true, load: j.before.load, total_mb: j.before.total_mb, free_mb: j.before.free_mb };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/** 按进程名取 pid（tasklist /FO CSV 解析，失败返回空数组）。 */
export function listPidsByName(names) {
  const wanted = new Set((Array.isArray(names) ? names : []).map((n) => String(n).toLowerCase()));
  if (!wanted.size) return [];
  try {
    const r = spawnSync('tasklist', ['/FO', 'CSV', '/NH'], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
    const out = String((r && r.stdout) || '');
    const pids = [];
    for (const line of out.split(/\r?\n/)) {
      if (!line.trim()) continue;
      const cols = line.split('","').map((c) => c.replace(/^"|"$/g, ''));
      if (cols.length < 2) continue;
      if (!wanted.has(String(cols[0] || '').toLowerCase())) continue;
      const pid = Number(cols[1]);
      if (Number.isFinite(pid) && pid > 0) pids.push(pid);
    }
    return pids;
  } catch (e) {
    return [];
  }
}

/**
 * 回收工作集并返回前后对比。
 *
 * @param options.pids 显式指定的进程 id（不传则按进程名匹配）
 * @param options.force 忽略阈值与间隔（仍受 memGuardEnabled 控制）
 */
export function reclaimWorkingSets(options) {
  const opts = options || {};
  if (!memGuardEnabled()) return { ok: false, skipped: 'disabled' };
  let pids = Array.isArray(opts.pids) ? opts.pids : null;
  if (!pids || !pids.length) {
    if (!opts.force) {
      const snap = memorySnapshot();
      if (!snap.ok) return { ok: false, error: snap.error };
      if (typeof snap.load === 'number' && snap.load < reclaimThresholdPct()) {
        return { ok: true, skipped: 'below-threshold', before: snap };
      }
    }
    pids = listPidsByName(targetProcessNames());
  }
  if (!pids.length) return { ok: true, skipped: 'no-targets' };
  try {
    const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', buildWorkingSetScript(pids)], {
      encoding: 'utf8', windowsHide: true, timeout: 30000,
    });
    const j = parseLastJson(r && r.stdout);
    if (!j || !j.before) {
      return { ok: false, error: String((r && r.stderr) || 'powershell 无输出').slice(0, 400) };
    }
    return {
      ok: true, before: j.before, after: j.after,
      reclaimed: Number(j.reclaimed) || 0, failed: Number(j.failed) || 0, pids,
    };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/**
 * 节流回收器：同一进程内两次回收至少间隔 reclaimIntervalMs()。
 * 供「每轮结束 / 每次 fork 之后」的轻量调用，避免频繁刷工作集。
 */
export function createReclaimer() {
  let lastAt = 0;
  let lastResult = null;
  return function maybeReclaim(options) {
    const opts = options || {};
    const now = Date.now();
    if (!opts.force && lastAt && (now - lastAt) < reclaimIntervalMs()) {
      return { ok: true, skipped: 'throttled', last: lastResult };
    }
    const r = reclaimWorkingSets(opts);
    lastAt = now;
    lastResult = r;
    return r;
  };
}
