# =============================================================================
# Patch the two DSH-0.1.x-era plugins so DSH 0.2.0 accepts them.
# =============================================================================
# Verified first (see plugin_patch_plan.md): neither plugin's CODE references a
# package 0.2.0 no longer ships. They were refused purely because their
# peerDependencies declared 0.1.x-only ranges, and app-boot skips any bundle
# whose declared @deepseek-ai/dsh* range does not satisfy the running runtime.
#
# So the change is ONE thing, on the plugin side:
#   append " || ^0.2.0-rc.2" to each offending peer range.
# No source file, no cordis.patch.yml, no behaviour is touched. The original
# branches are kept, so the plugin still loads on the older runtime too.
#
# Output: <repo>\third-party-patched\<plugin>\  = patched copy, the source of
# truth for these two plugins. Install them with:
#     .\restore-old-plugins.ps1 -IncludeHeldBack
#
# Keep this file ASCII-only (PowerShell 5.1 reads .ps1 as ANSI without a BOM).
# =============================================================================

param(
    [string]$BackupRoot = "$env:USERPROFILE\.workbuddy\dsh_backup\20260929-214541-full-removal",
    [string]$Runtime    = '0.2.0-rc.2'
)

$ErrorActionPreference = 'Stop'

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$OldNm   = Join-Path $BackupRoot 'dot-dsh\profiles\desktop\node_modules'
$Patched = Join-Path $Root 'third-party-patched'
$Patcher = Join-Path $Root 'patch-peer-ranges.mjs'
$LogPath = Join-Path $Root 'patch-incompatible.log'

$PLUGINS = @('dshmarket', 'dsh-codearts-auth')

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$Text) { [void]$script:Log.AppendLine($Text); Write-Host $Text }
function Step([string]$Text) { Say ("`n---- " + $Text + " ----") }
function Flush() { try { [System.IO.File]::WriteAllText($LogPath, $script:Log.ToString(), (New-Object System.Text.UTF8Encoding($false))) } catch {} }
trap {
    Say ("[FATAL] " + $_.Exception.Message)
    try { Say ("  at: " + $_.InvocationInfo.PositionMessage) } catch {}
    $script:Bad++; Flush; exit 1
}

function CopyTree([string]$Src, [string]$Dst) {
    $ok = 0; $failed = New-Object System.Collections.ArrayList; $files = @()
    try {
        if (-not (Test-Path -LiteralPath $Dst)) { New-Item -ItemType Directory -Path $Dst -Force -ErrorAction Stop | Out-Null }
        $files = @(Get-ChildItem -Recurse -Force -File -LiteralPath $Src -ErrorAction Stop)
    } catch {
        [void]$failed.Add("ENUMERATE/CREATE :: " + $_.Exception.Message)
        return @{ ok = 0; failed = $failed; total = 0 }
    }
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($Src.Length).TrimStart('\')
        $target = Join-Path $Dst $rel
        $dir = Split-Path $target -Parent
        try {
            if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            Copy-Item -LiteralPath $f.FullName -Destination $target -Force -ErrorAction Stop
            $ok++
        } catch { if ($failed.Count -lt 20) { [void]$failed.Add($rel + "  ::  " + $_.Exception.Message) } }
    }
    return @{ ok = $ok; failed = $failed; total = $files.Count }
}

# Resolve a REAL node.exe.
#   Do NOT use Z:\DSH\resources\runtime\bin\node.cmd: it expands
#   "%DSH_DESKTOP_NODE_EXECUTABLE% --expose-internals %*", and that variable only
#   exists inside the Electron desktop process -- outside it the shim becomes
#   `"" --expose-internals`, which cmd rejects. The extensionless `node` next to
#   it is a 100-byte shell script, likewise unusable here.
#   Prefer the Node that DSH itself bootstrapped (primary-runtime), then a
#   system install, then whatever is on PATH.
function ResolveNode() {
    $deps = 'Z:\DSH\resources\runtime\primary-runtime\dependencies\node'
    if (Test-Path $deps) {
        $hit = Get-ChildItem -Recurse -File -Filter 'node.exe' -LiteralPath $deps -ErrorAction SilentlyContinue |
               Sort-Object Length -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    foreach ($c in @("$env:ProgramFiles\nodejs\node.exe", "${env:ProgramFiles(x86)}\nodejs\node.exe")) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Say "Patch DSH plugins for runtime $Runtime"
Say ("backup root = " + $BackupRoot)
Say ("output      = " + $Patched)

Step "0. preflight"
if (-not (Test-Path $OldNm)) { Say ("[X] missing " + $OldNm); Flush; exit 1 }
if (-not (Test-Path $Patcher)) { Say ("[X] missing " + $Patcher); Flush; exit 1 }
$node = ResolveNode
if (-not $node) { Say "[X] no node runner found"; Flush; exit 1 }
Say ("[OK] node = " + $node)
Say ("[OK] patcher = " + (Split-Path $Patcher -Leaf))

if (-not (Test-Path $Patched)) { New-Item -ItemType Directory -Path $Patched -Force | Out-Null }

foreach ($name in $PLUGINS) {
    Step ("plugin: " + $name)
    $src = Join-Path $OldNm $name
    $dst = Join-Path $Patched $name
    if (-not (Test-Path -LiteralPath $src)) { Say ("  [X] source missing: " + $src); $script:Bad++; continue }

    $r = CopyTree -Src $src -Dst $dst
    Say ("  [OK] copied to third-party-patched\" + $name + "   " + $r.ok + "/" + $r.total + " files")
    if ($r.failed.Count -gt 0) {
        $script:Bad++
        foreach ($m in $r.failed) { Say ("       - " + $m) }
    }

    $pkg = Join-Path $dst 'package.json'
    Say "  --- patch-peer-ranges.mjs ---"
    $out = & $node $Patcher $pkg $Runtime 2>&1
    foreach ($line in $out) { Say ("  " + $line) }
    if ($LASTEXITCODE -ne 0) { Say ("  [X] patcher exited " + $LASTEXITCODE); $script:Bad++; continue }

    Say "  --- re-check (must now satisfy the runtime) ---"
    $chk = & $node $Patcher $pkg $Runtime --check 2>&1
    foreach ($line in $chk) { Say ("  " + $line) }
    Flush
}

Step "done"
Say ("patched copies: " + $Patched)
Say "next:  .\restore-old-plugins.ps1 -IncludeHeldBack"
if ($script:Bad -eq 0) { Say "ALL GOOD." } else { Say ("FINISHED WITH " + $script:Bad + " PROBLEM(S).") }
Flush
