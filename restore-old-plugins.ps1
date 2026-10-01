# =============================================================================
# Restore the OLD third-party plugins into the DSH 0.2.0 desktop profile
# =============================================================================
# Only plugins that PASSED the 0.2.0 suitability check are installed, see
# suitability_report.md. The gate is checked by app-boot: a bundle whose
# peerDependencies name @deepseek-ai/dsh or @deepseek-ai/dsh-* must satisfy the
# runtime version, otherwise the whole bundle is SKIPPED.
#
#   INSTALLED (no DSH peers -> no constraint):
#     dsh-skin-market                                     0.1.54
#     dsh-dafeiyu                                         0.1.14  (+ %LOCALAPPDATA%\dsh-dafeiyu)
#     @dsh-external/dsh-client-ui-skin-maid-atelier       0.0.1
#     @dsh-external/dsh-client-ui-skin-deep-whale-manager 0.1.0
#
#   HELD BACK (0.2.0's version gate would skip them anyway):
#     dshmarket          peers @deepseek-ai/dsh-settings ^0.1.x        -> FAIL
#     dsh-codearts-auth  peers @deepseek-ai/dsh-{credentials,commands,llm} ^0.1.6-alpha.2 -> FAIL
#     Pass -IncludeHeldBack together with a compatibility.json exemption if you
#     ever want them; a 0.1.x plugin calling 0.2.0 APIs can crash or lose data.
#
#   NEVER COPIED:
#     dsh-engineering-ui / dsH-engineering-sw-single-line
#       -> already installed, and the live copies are the MIGRATED 0.2.0
#          versions; the old ones would silently undo the migration.
#     dshmarket.broken-junction-20260925  -> broken leftover junction, not a package
#     @deepseek-ai/dsh-workflow-worker-thread -> the 0.1.x workflow provider that
#          used to break preset mounting; 0.2.0 presets use dsh-workflow-ptc.
#
# Robustness: every file is copied individually with its own try/catch, so one
# locked or unreadable file can no longer abort the whole run.
#
# Keep this file ASCII-only (PowerShell 5.1 reads .ps1 as ANSI without a BOM).
# =============================================================================

param(
    [string]$BackupRoot = "$env:USERPROFILE\.workbuddy\dsh_backup\20260929-214541-full-removal",
    [string]$DshHome    = '',
    # patched copies live here (see patch-incompatible.ps1); when a plugin exists
    # there it is installed instead of the untouched quarantine copy
    [string]$PatchedRoot = '',
    [switch]$SkipData,
    [switch]$IncludeHeldBack
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($DshHome)) {
    $DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
}
$OldProfile = Join-Path $BackupRoot 'dot-dsh\profiles\desktop'
$OldNm      = Join-Path $OldProfile 'node_modules'
$NewProfile = Join-Path $DshHome 'profiles\desktop'
$NewNm      = Join-Path $NewProfile 'node_modules'
$LogPath    = Join-Path $Root 'restore-old-plugins.log'
if ([string]::IsNullOrWhiteSpace($PatchedRoot)) { $PatchedRoot = Join-Path $Root 'third-party-patched' }

# name -> original dependency spec from the OLD desktop manifest
$SUITABLE = [ordered]@{
    'dsh-skin-market'                                     = 'file:./node_modules/dsh-skin-market'
    'dsh-dafeiyu'                                         = 'file:./node_modules/dsh-dafeiyu'
    '@dsh-external/dsh-client-ui-skin-maid-atelier'       = 'github:Small-tailqwq/dsh-deep-whale#b693d2c224a0a6fb5b621e7cc1284f20d9849c1c&path:/maid-atelier'
    '@dsh-external/dsh-client-ui-skin-deep-whale-manager' = 'github:Small-tailqwq/dsh-deep-whale#b693d2c224a0a6fb5b621e7cc1284f20d9849c1c&path:/skin-manager'
}
$HELD_BACK = [ordered]@{
    'dsh-codearts-auth' = 'file:./node_modules/dsh-codearts-auth'
    'dshmarket'         = '1.58.0'
}

# Bundle order taken verbatim from the OLD desktop manifest (order = patch
# precedence), minus anything not installed.
$BUNDLE_ORDER = @(
    '@deepseek-ai/dsh-base',
    'dsh-skin-market',
    'dsh-dafeiyu',
    'dsh-engineering-ui',
    'dsH-engineering-sw-single-line',
    '@deepseek-ai/dsh-web-app',
    'dshmarket',
    'dsh-codearts-auth',
    '@dsh-external/dsh-client-ui-skin-maid-atelier',
    '@dsh-external/dsh-client-ui-skin-deep-whale-manager'
)

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$Text) { [void]$script:Log.AppendLine($Text); Write-Host $Text }
function Step([string]$Text) { Say ("`n---- " + $Text + " ----") }
function WriteNoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
# Persist the log after every step so a later crash never hides progress.
function Flush() { try { WriteNoBom $LogPath $script:Log.ToString() } catch {} }

# Any terminating error: report it, persist what happened, and stop cleanly.
trap {
    Say ("[FATAL] " + $_.Exception.Message)
    try { Say ("  at: " + $_.InvocationInfo.PositionMessage) } catch {}
    $script:Bad++
    Flush
    exit 1
}

# Copy a tree file by file; collect failures instead of aborting.
# Everything that can throw is inside the try, so one bad path cannot kill the run.
function CopyTree([string]$Src, [string]$Dst) {
    $okCount = 0; $failed = New-Object System.Collections.ArrayList
    $files = @()
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
            $okCount++
        } catch {
            if ($failed.Count -lt 20) { [void]$failed.Add($rel + "  ::  " + $_.Exception.Message) }
        }
    }
    return @{ ok = $okCount; failed = $failed; total = $files.Count }
}

Say "Restore OLD third-party plugins into the DSH 0.2.0 desktop profile"
Say ("backup root = " + $BackupRoot)
Say ("DSH_HOME    = " + $DshHome)
Say ("mode        = " + ($(if ($IncludeHeldBack) { 'include held-back' } else { 'suitable only' })))

Step "0. preflight"
foreach ($p in @($OldProfile, $OldNm, $NewProfile)) {
    if (-not (Test-Path $p)) { Say ("[X] missing: " + $p); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
}
Say "[OK] source and target present"
if (-not (Test-Path $NewNm)) { New-Item -ItemType Directory -Path $NewNm -Force | Out-Null }
Flush

$installed = [ordered]@{}
foreach ($k in $SUITABLE.Keys) { $installed[$k] = $SUITABLE[$k] }
if ($IncludeHeldBack) { foreach ($k in $HELD_BACK.Keys) { $installed[$k] = $HELD_BACK[$k] } }

Step "1. copy plugin packages (file by file)"
foreach ($name in $installed.Keys) {
    $rel     = $name -replace '/', '\'
    $cand    = Join-Path $PatchedRoot $rel
    $fromPatched = Test-Path -LiteralPath (Join-Path $cand 'package.json')
    $src     = if ($fromPatched) { $cand } else { Join-Path $OldNm $rel }
    $dst     = Join-Path $NewNm $rel
    if (-not (Test-Path -LiteralPath $src)) { Say ("  [MISSING SRC] " + $name); $script:Bad++; continue }
    $r = CopyTree -Src $src -Dst $dst
    $ver = '?'
    $pj = Join-Path $dst 'package.json'
    if (Test-Path $pj) { try { $ver = (([System.IO.File]::ReadAllText($pj).TrimStart([char]0xFEFF)) | ConvertFrom-Json).version } catch {} }
    $origin = if ($fromPatched) { 'PATCHED' } else { 'original' }
    $mark = ''
    if ($HELD_BACK.Contains($name) -and -not $fromPatched) {
        $mark = '   <== 0.2.0 will SKIP this bundle (peer range too narrow; run patch-incompatible.ps1)'
    } elseif ($HELD_BACK.Contains($name) -and $fromPatched) {
        $mark = '   <== peer range widened for 0.2.0'
    }
    Say ("  [OK] " + $name + " @" + $ver + "  " + $r.ok + "/" + $r.total + " files   [" + $origin + "]" + $mark)
    if ($r.failed.Count -gt 0) {
        $script:Bad++
        Say ("       " + $r.failed.Count + " file(s) FAILED:")
        foreach ($m in $r.failed) { Say ("         - " + $m) }
    }
}
Say ("  (not installed: " + ($(if ($IncludeHeldBack) { '-' } else { ($HELD_BACK.Keys -join ', ') })) + ")")
Flush

Step "2. plugin data"
if ($SkipData) {
    Say "  [SKIP] -SkipData"
} else {
    # .dsh-skin-market belongs to dsh-skin-market; .dsh-market + the marker file
    # belong to dshmarket (restored only when that bundle is being installed).
    $dataDirs = @('.dsh-skin-market')
    if ($installed.Contains('dshmarket')) { $dataDirs += '.dsh-market' }
    foreach ($d in $dataDirs) {
        $s = Join-Path $OldProfile $d
        $t = Join-Path $NewProfile $d
        if (Test-Path $s) {
            $r = CopyTree -Src $s -Dst $t
            Say ("  [OK] " + $d + "  " + $r.ok + "/" + $r.total + " files")
            if ($r.failed.Count -gt 0) { $script:Bad++; foreach ($m in $r.failed) { Say ("         - " + $m) } }
        } else { Say ("  [absent] " + $d) }
    }
    if ($installed.Contains('dshmarket')) {
        $mf = '.desktop-marketplace-default-v1'
        $s = Join-Path $OldProfile $mf
        if (Test-Path $s) { Copy-Item $s (Join-Path $NewProfile $mf) -Force; Say ("  [OK] " + $mf) }
        else { Say ("  [absent] " + $mf) }
    }

    $petSrc = Join-Path $BackupRoot 'LocalAppData-dsh-dafeiyu'
    $petDst = Join-Path $env:LOCALAPPDATA 'dsh-dafeiyu'
    if (Test-Path $petSrc) {
        $r = CopyTree -Src $petSrc -Dst $petDst
        Say ("  [OK] pet data -> " + $petDst + "  " + $r.ok + "/" + $r.total + " files")
    } else { Say ("  [absent] " + $petSrc) }
}
Flush

Step "3. register in the desktop profile manifest"
$ProfilePkg = Join-Path $NewProfile 'package.json'
$pkg = ([System.IO.File]::ReadAllText($ProfilePkg).TrimStart([char]0xFEFF)) | ConvertFrom-Json
$deps = [ordered]@{}
foreach ($p in $pkg.dependencies.PSObject.Properties) {
    if (-not $deps.Contains($p.Name)) { $deps[$p.Name] = $p.Value }
}
foreach ($name in $installed.Keys) { $deps[$name] = $installed[$name] }
foreach ($k in @('dsh-engineering-ui', 'dsH-engineering-sw-single-line')) {
    if (-not $deps.Contains($k)) { $deps[$k] = ('file:./node_modules/' + $k) }
}

$bundles = @()
foreach ($b in $BUNDLE_ORDER) {
    # a bundle only appears if its package was actually installed
    if ($b -eq 'dsh-codearts-auth' -and -not $IncludeHeldBack) { continue }
    if ($b -eq 'dshmarket' -and -not $deps.Contains('dshmarket')) { continue }
    if ($b -eq 'dsh-skin-market' -and -not $deps.Contains('dsh-skin-market')) { continue }
    if ($b -eq 'dsh-dafeiyu' -and -not $deps.Contains('dsh-dafeiyu')) { continue }
    $bundles += $b
}
foreach ($b in $pkg.dsh.profile.bundles) { if ($bundles -notcontains $b) { $bundles += $b } }

$out = [ordered]@{}
$out['name'] = $pkg.name
$out['private'] = $true
if ($pkg.pnpm) { $out['pnpm'] = $pkg.pnpm }
$out['dependencies'] = $deps
$out['dsh'] = [ordered]@{ profile = [ordered]@{ bundles = $bundles } }
WriteNoBom $ProfilePkg (($out | ConvertTo-Json -Depth 12) + "`n")
Say "  [OK] dependencies:"
foreach ($k in $deps.Keys) { Say ("        " + $k) }
Say "  [OK] bundles (old order preserved):"
foreach ($b in $bundles) { Say ("        " + $b) }
Flush

Step "4. verify"
foreach ($name in $installed.Keys) {
    # note: build the path as (scope -> scope dir) + file name; replacing '/' with
    # '\package.json' would produce a nonsense path for scoped names.
    $pj = Join-Path $NewNm (($name -replace '/', '\') + '\package.json')
    if (Test-Path -LiteralPath $pj) { Say ("  [OK] " + $name) } else { Say ("  [MISSING] " + $name); $script:Bad++ }
}
foreach ($k in @('dsh-engineering-ui', 'dsH-engineering-sw-single-line')) {
    $pj = Join-Path $NewNm ($k -replace '/', '\package.json')
    if (Test-Path $pj) { Say ("  [OK] " + $k + " (migrated, untouched)") } else { Say ("  [MISSING] " + $k); $script:Bad++ }
}
$bytes = [System.IO.File]::ReadAllBytes($ProfilePkg)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    Say "  [BOM!] profile package.json has a BOM"; $script:Bad++
} else { Say "  [OK] profile package.json is BOM-less" }

Step "done"
if ($script:Bad -eq 0) { Say "ALL GOOD." } else { Say ("FINISHED WITH " + $script:Bad + " PROBLEM(S) - see above.") }
Say "RESTART the DSH desktop app (fully quit, then reopen)."
Say ("log: " + $LogPath)
WriteNoBom $LogPath $script:Log.ToString()
