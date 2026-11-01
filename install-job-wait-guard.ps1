# =============================================================================
#  install-job-wait-guard.ps1 — 把【job_output 长等待守卫】装进当前 DSH
# =============================================================================
#
#  背景（2026-10-31 事故，见 docs/DSH卡死事故分析_job_output阻塞.md）
#    job_output { wait:true, timeout_ms:270000 } 会静默阻塞数分钟：
#      · 期间 GUI 没有任何反馈 → 用户以为 DSH 死机 → 强制退出
#      · 退出瞬间所有插件 fiber 失活 → cordis 抛
#        cannot get required service "jobs" in inactive context
#      · 该异常冒到 agent loop → turn/end{interrupted} → 整轮对话作废
#
#  修复落点分两层（本脚本装【宿主层】，仓库层随 preset 自然生效）：
#    宿主层 = dsh-engineering-ui 插件的 lib/job-wait-guard.js
#             （用官方包装点 ctx.on('tools/execute') 把长等待切成 ≤45s 窗口，
#               并把上下文失活异常降级成普通工具错误）
#    仓库层 = preset 人设/技能/skill 里禁止"Start-Sleep + job_output wait:true"
#
#  为什么必须做成脚本（而不是一次性手改）
#    DSH 每次升级都会用新的 app.asar 覆盖安装目录，装进去的插件也随
#    profile/node_modules 一起被替换/回退 → 修复会静默消失。
#    这里做的是【幂等重装】：升级后再跑一次本脚本即可。
#
#  用法
#    powershell -ExecutionPolicy Bypass -File install-job-wait-guard.ps1
#    powershell -ExecutionPolicy Bypass -File install-job-wait-guard.ps1 -DryRun
#    powershell -ExecutionPolicy Bypass -File install-job-wait-guard.ps1 -Verify
#
#  装完必须【重启 DSH】（插件 host 半是进程级配置，热更新无效）。
#
#  NOTE: keep this file ASCII-only (PowerShell 5.1 reads BOM-less .ps1 as ANSI).
# =============================================================================
param(
    [switch]$DryRun,
    [switch]$Verify,
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'
$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }

# Source files that make up the host-side fix.
$SrcLib   = Join-Path $Root 'engineering\plugins\dsh-engineering-ui\lib'
$Files    = @('index.js', 'job-wait-guard.js')
$GuardSig = 'installJobWaitGuard'

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Rule($t) {
    Say ''
    Say '================================================================' Cyan
    if ($t) { Say ('  ' + $t) Cyan }
    Say '================================================================' Cyan
}

Rule 'install job_output wait-guard -> DSH profiles'
Say ('  source   : ' + $SrcLib)
Say ('  DSH_HOME : ' + $DshHome) Gray
if ($DryRun) { Say '  *** DRY RUN - nothing will be written ***' Yellow }

foreach ($f in $Files) {
    $p = Join-Path $SrcLib $f
    if (-not (Test-Path $p)) { Say ('[X] missing source: ' + $p) Red; exit 1 }
}

# ---- 1. locate install targets ---------------------------------------------
$ProfilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path $ProfilesDir)) { Say ('[X] no profiles dir: ' + $ProfilesDir) Red; exit 1 }

$all = @()
Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue |
    ForEach-Object { $all += $_.Name }
if ($Profile.Count -gt 0) { $targets = @($all | Where-Object { $Profile -contains $_ }) }
else { $targets = $all }
if ($targets.Count -eq 0) { Say '[X] no target profile' Red; exit 1 }
Say ('  profiles : ' + ($targets -join ', ')) Gray

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ok = 0; $miss = 0

foreach ($prof in $targets) {
    $dst = Join-Path $ProfilesDir (Join-Path $prof 'node_modules\dsh-engineering-ui\lib')
    if (-not (Test-Path $dst)) {
        Say ('  [skip] ' + $prof + ' : plugin not installed (not a target)') DarkGray
        continue
    }
    foreach ($f in $Files) {
        $src = Join-Path $SrcLib $f
        $tgt = Join-Path $dst $f
        if ($Verify) { continue }
        if (Test-Path $tgt) {
            $a = (Get-FileHash $src).Hash
            $b = (Get-FileHash $tgt).Hash
            if ($a -eq $b) { Say ('  [same] ' + $prof + '/' + $f) DarkGray; $ok++; continue }
            if (-not $DryRun) { Copy-Item $tgt ($tgt + '.bak-guard-' + $Stamp) -Force }
        }
        if ($DryRun) { Say ('    [dry] ' + $prof + '/' + $f) DarkGray }
        else {
            Copy-Item $src $tgt -Force
            Say ('  [OK]   ' + $prof + '/' + $f) Green
        }
        $ok++
    }
}

# ---- 2. verify --------------------------------------------------------------
Rule 'verify'
foreach ($prof in $targets) {
    $dst = Join-Path $ProfilesDir (Join-Path $prof 'node_modules\dsh-engineering-ui\lib')
    if (-not (Test-Path $dst)) { continue }
    $idx = Join-Path $dst 'index.js'
    $gd  = Join-Path $dst 'job-wait-guard.js'
    $hasGuard = $false; $hasWire = $false
    if (Test-Path $gd)  { $hasGuard = (Select-String -Path $gd -SimpleMatch $GuardSig -Quiet) }
    if (Test-Path $idx) { $hasWire  = (Select-String -Path $idx -SimpleMatch $GuardSig -Quiet) }
    if ($hasGuard -and $hasWire) { Say ('  [PASS] ' + $prof + ' : guard module + wiring present') Green }
    else { Say ('  [FAIL] ' + $prof + ' : guard=' + $hasGuard + ' wired=' + $hasWire) Red; $miss++ }
}

Rule ([string]::Format('done: {0} file(s) handled, {1} problem(s)', $ok, $miss))
Say ''
Say '  NEXT: restart DSH completely (host half is process-level).' Yellow
Say '  Toggle off with env DSH_ENG_JOB_WAIT_GUARD=0 ; tune window with DSH_ENG_JOB_WAIT_MAX_MS.' Gray
Say ''
if ($miss -gt 0 -and -not $Verify) { exit 1 }
