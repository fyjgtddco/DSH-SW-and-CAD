#Requires -Version 5.1
<#
    remove-extra-plugins.ps1

    Remove every third-party plugin that restore-old-plugins.ps1 put into the
    DSH desktop profile, EXCEPT the two engineering-mode plugins, which are kept:

        KEEP : dsh-engineering-ui
        KEEP : dsH-engineering-sw-single-line

    Everything removed is MOVED to a quarantine folder first (same volume =
    instant rename), so the removal is reversible. Pass -Purge to delete the
    quarantine afterwards as well.

    NOTE: keep this file pure ASCII. PowerShell 5.1 parses .ps1 as ANSI, and a
    stray non-ASCII byte makes the whole script fail to parse. All Chinese text
    lives in data files, never in this script.
#>
param(
    [string]$DshHome = '',
    [switch]$Purge
)

$ErrorActionPreference = 'Continue'
$Root = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
$LogPath = Join-Path $Root 'remove-extra-plugins.log'

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$t) { [void]$script:Log.AppendLine($t) }
function WriteNoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
function Flush() { try { WriteNoBom $LogPath $script:Log.ToString() } catch {} }
function Step([string]$t) { Say ''; Say ('== ' + $t); Flush }

# ---------------------------------------------------------------- target paths
if (-not $DshHome) { $DshHome = Join-Path $env:USERPROFILE '.dsh' }
$ProfileDir = Join-Path $DshHome 'profiles\desktop'
$Nm         = Join-Path $ProfileDir 'node_modules'
$PkgPath    = Join-Path $ProfileDir 'package.json'
$Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$Quar       = Join-Path $env:USERPROFILE ('.workbuddy\dsh_backup\plugins-removed-' + $Stamp)

# ------------------------------------------------------------- what goes away
# Directories under node_modules (relative path, '/' separates scopes).
$DROP_DIRS = @(
    'dsh-skin-market',
    'dsh-dafeiyu',
    'dshmarket',
    'dsh-codearts-auth',
    '@dsh-external'
)

# Keys of package.json -> dependencies that go away.
$DROP_DEPS = @(
    'dsh-skin-market',
    'dsh-dafeiyu',
    'dshmarket',
    'dsh-codearts-auth',
    '@dsh-external/dsh-client-ui-skin-maid-atelier',
    '@dsh-external/dsh-client-ui-skin-deep-whale-manager'
)

# Entries of dsh.profile.bundles that go away.
$DROP_BUNDLES = @(
    'dsh-skin-market',
    'dsh-dafeiyu',
    'dshmarket',
    'dsh-codearts-auth',
    '@dsh-external/dsh-client-ui-skin-maid-atelier',
    '@dsh-external/dsh-client-ui-skin-deep-whale-manager'
)

# Data directories created by those plugins.
$DROP_PROFILE_DATA = @('.dsh-market', '.dsh-skin-market', '.desktop-marketplace-default-v1')
$DROP_LOCAL_DATA   = @('dsh-dafeiyu')

# ------------------------------------------------------------------- pre-check
Step ('0. pre-check   (' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ')')
Say ('DshHome      : ' + $DshHome)
Say ('Profile      : ' + $ProfileDir)
Say ('Quarantine   : ' + $Quar)
if (-not (Test-Path -LiteralPath $ProfileDir)) { Say '[FATAL] profile not found'; Flush; exit 2 }
if (-not (Test-Path -LiteralPath $PkgPath))    { Say '[FATAL] package.json not found'; Flush; exit 2 }
if (-not (Test-Path -LiteralPath $Quar)) {
    try { New-Item -ItemType Directory -Path $Quar -Force -ErrorAction Stop | Out-Null; Say '[OK] quarantine created' }
    catch { Say ('[FATAL] cannot create quarantine: ' + $_.Exception.Message); Flush; exit 2 }
}

# ------------------------------------------------------------------ 1. plugins
Step '1. remove plugin directories (moved to quarantine)'
foreach ($rel in $DROP_DIRS) {
    $src = Join-Path $Nm ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $src)) { Say ('  [absent] ' + $rel); continue }
    $files = Get-ChildItem -Recurse -Force -File -LiteralPath $src -ErrorAction SilentlyContinue
    $mb = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1MB), 1)
    $dst = Join-Path $Quar ($rel -replace '/', '\')
    try {
        $parent = Split-Path $dst -Parent
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null }
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force -ErrorAction SilentlyContinue }
        Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
        Say ('  [OK] ' + $rel + '  (' + $files.Count + ' files, ' + $mb + ' MB)')
    } catch {
        Say ('  [FAIL] ' + $rel + ' :: ' + $_.Exception.Message)
        $script:Bad++
    }
    Flush
}

# ------------------------------------------------------------------ 2. manifest
Step '2. rewrite package.json'
try {
    $raw = [System.IO.File]::ReadAllText($PkgPath)
    $json = $raw.TrimStart([char]0xFEFF) | ConvertFrom-Json

    # dependencies
    $keptDeps = [ordered]@{}
    $json.dependencies.PSObject.Properties | ForEach-Object {
        if ($DROP_DEPS -contains $_.Name) { Say ('  [dep removed] ' + $_.Name) }
        else { $keptDeps[$_.Name] = $_.Value }
    }

    # bundles
    $keptBundles = @()
    foreach ($b in $json.dsh.profile.bundles) {
        if ($DROP_BUNDLES -contains $b) { Say ('  [bundle removed] ' + $b) }
        else { $keptBundles += $b }
    }

    $newObj = [ordered]@{
        name         = $json.name
        private      = $json.private
        dependencies = $keptDeps
        dsh          = [ordered]@{ profile = [ordered]@{ bundles = $keptBundles } }
    }
    $text = ($newObj | ConvertTo-Json -Depth 12) -replace '\\u0026', '&'
    WriteNoBom $PkgPath ($text + [Environment]::NewLine)
    Say '[OK] package.json rewritten'
} catch {
    Say ('[FAIL] package.json :: ' + $_.Exception.Message)
    $script:Bad++
}
Flush

# ------------------------------------------------------------------ 3. data
Step '3. remove plugin data directories'
foreach ($n in $DROP_PROFILE_DATA) {
    $src = Join-Path $ProfileDir $n
    if (-not (Test-Path -LiteralPath $src)) { Say ('  [absent] ' + $n); continue }
    $dst = Join-Path $Quar ('data\' + $n)
    try {
        $parent = Split-Path $dst -Parent
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null }
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force -ErrorAction SilentlyContinue }
        Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
        Say ('  [OK] ' + $n)
    } catch { Say ('  [FAIL] ' + $n + ' :: ' + $_.Exception.Message); $script:Bad++ }
}
foreach ($n in $DROP_LOCAL_DATA) {
    $src = Join-Path $env:LOCALAPPDATA $n
    if (-not (Test-Path -LiteralPath $src)) { Say ('  [absent] LOCALAPPDATA\' + $n); continue }
    $dst = Join-Path $Quar ('data\' + $n)
    try {
        if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Path $dst -Force -ErrorAction Stop | Out-Null }
        Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
        Say ('  [OK] LOCALAPPDATA\' + $n)
    } catch { Say ('  [FAIL] LOCALAPPDATA\' + $n + ' :: ' + $_.Exception.Message); $script:Bad++ }
}
Flush

# ------------------------------------------------------------------ 4. verify
Step '4. verify'
Say '-- node_modules now --'
Get-ChildItem -Force $Nm -ErrorAction SilentlyContinue | ForEach-Object { Say ('  ' + $_.Name) }
Say '-- kept plugins must exist --'
foreach ($k in @('dsh-engineering-ui', 'dsH-engineering-sw-single-line')) {
    $pj = Join-Path $Nm (($k -replace '/', '\') + '\package.json')
    if (Test-Path -LiteralPath $pj) {
        $v = '?'
        try { $v = ((([System.IO.File]::ReadAllText($pj)).TrimStart([char]0xFEFF)) | ConvertFrom-Json).version } catch {}
        Say ('  [OK] ' + $k + ' @' + $v)
    } else { Say ('  [MISSING] ' + $k); $script:Bad++ }
}
Say '-- plugin package.json must be gone --'
foreach ($d in $DROP_DIRS) {
    $p = Join-Path $Nm (($d -replace '/', '\') + '\package.json')
    if (Test-Path -LiteralPath $p) { Say ('  [STILL THERE] ' + $d); $script:Bad++ } else { Say ('  [OK] gone: ' + $d) }
}
Say '-- package.json --'
Say ([System.IO.File]::ReadAllText($PkgPath))
$bytes = [System.IO.File]::ReadAllBytes($PkgPath)
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
Say ('  BOM present: ' + $bom)
try { $null = ([System.IO.File]::ReadAllText($PkgPath) | ConvertFrom-Json); Say '  JSON parses: OK' }
catch { Say ('  JSON parses: FAIL :: ' + $_.Exception.Message); $script:Bad++ }
Flush

# ------------------------------------------------------------------ 5. purge
Step '5. quarantine'
if ($Purge) {
    try { Remove-Item -LiteralPath $Quar -Recurse -Force -ErrorAction Stop; Say '[PURGED] quarantine deleted' }
    catch { Say ('[FAIL] purge: ' + $_.Exception.Message); $script:Bad++ }
} else {
    $f = Get-ChildItem -Recurse -Force -File -LiteralPath $Quar -ErrorAction SilentlyContinue
    Say ('[KEPT] ' + $Quar + '   (' + $f.Count + ' files, ' + [math]::Round((($f | Measure-Object Length -Sum).Sum / 1MB), 1) + ' MB)')
    Say '       rerun with -Purge to delete it for good'
}

Step 'DONE'
Say ('problems: ' + $script:Bad)
Say ('log: ' + $LogPath)
Flush
exit $(if ($script:Bad -gt 0) { 1 } else { 0 })
