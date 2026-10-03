# =============================================================================
# DSH 0.2.0 - Engineering Mode + DSH_SW installer  (bundle path)
# =============================================================================
# Why a separate script from install.ps1 (the 0.1.x one):
#   0.1.x discovered the engineering preset by scanning
#   ~/.dsh/.agent-presets/engineering/agent.cordis.yml.
#   DSH 0.2.0 NO LONGER reads that directory. Presets are now ordinary
#   `@deepseek-ai/dsh-agent-preset` rows declared by a bundle patch, installed
#   into the profile and selected through package.json's dsh.profile.bundles.
#
# WHICH PROFILE: the Electron desktop shell boots the RESERVED profile named
#   `desktop` (~/.dsh/profiles/desktop), NOT `web`. `dsh web` boots `web`.
#   So a plugin installed only into `web` is invisible in the desktop app.
#   This script therefore installs into EVERY profile under ~/.dsh/profiles by
#   default. Pass -Profile to target specific ones.
#
# What still works unchanged (so this stays a small delta):
#   * tools/  -> ~/.dsh/.agent-presets/engineering/tools   (read by
#     dsh-engineering-ui's findToolsDir() for the workflow/mode gate state)
#   * skills/ -> ~/.dsh/skills                              (0.2.0 skill-filesystem
#     discovers user skills at <DSH_HOME>/skills, same as before)
#   Those two are shared by every profile and are installed once.
#
# SAFETY: the profile's cordis.patch.yml is NEVER overwritten.
#   [problem-2 fix] The custom `sw-single-line` permission preset and its
#   plugin (dsH-engineering-sw-single-line) were removed. This installer now
#   only CLEANS any residual `sw-single-line` block from an existing profile
#   and leaves the standard three permission tiers intact.
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as ANSI unless a
# UTF-8 BOM is present; Chinese literals here would be corrupted. All Chinese
# text lives in data files (profiles/web-0.2.0/*.yml, the plugin sources).
# =============================================================================

param(
    # One or more profile names. Empty = every directory under <DSH_HOME>/profiles.
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'

$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$DshHome     = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$ProfilesDir = Join-Path $DshHome 'profiles'
$DataDir     = Join-Path $DshHome '.agent-presets\engineering'
$UserSkills  = Join-Path $DshHome 'skills'
$PatchSrc    = Join-Path $Root 'profiles\web-0.2.0\cordis.patch.yml'
$LogPath     = Join-Path $Root 'install-0.2.0.log'

$UI_NAME   = 'dsh-engineering-ui'
$UI_SRC    = Join-Path $Root "engineering\plugins\$UI_NAME"

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$Text) {
    [void]$script:Log.AppendLine($Text)
    Write-Host $Text
}
function Step([string]$Text) { Say ("`n---- " + $Text + " ----") }
function WriteNoBom([string]$Path, [string]$Text) {
    # package.json / yml must be BOM-less: a BOM breaks JSON.parse at boot.
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

Say "DSH 0.2.0 Engineering Mode installer"
Say ("root      = " + $Root)
Say ("DSH_HOME  = " + $DshHome)

# ------------------------------------------------------------ 0. resolve profiles
Step "0. resolve target profiles"
if (-not (Test-Path $ProfilesDir)) {
    Say ("[X] no profiles dir: " + $ProfilesDir)
    Say "    Boot DSH once so the profile is created, then rerun."
    WriteNoBom $LogPath $script:Log.ToString(); exit 1
}
$targets = @()
if ($Profile.Count -gt 0) {
    foreach ($p in $Profile) {
        $d = Join-Path $ProfilesDir $p
        if (Test-Path $d) { $targets += $p } else { Say ("[WARN] profile '" + $p + "' not found, skipped") }
    }
} else {
    Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue |
        ForEach-Object { $targets += $_.Name }
}
if ($targets.Count -eq 0) { Say "[X] nothing to install into"; WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
Say ("targets   = " + ($targets -join ', '))

if (-not (Test-Path $UI_SRC))    { Say ("[X] missing plugin source " + $UI_SRC); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
# [problem-2 fix] the sw-single-line plugin + its preset file were removed.
if (-not (Test-Path $UI_SRC))    { Say ("[X] missing plugin source " + $UI_SRC); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
if (-not (Test-Path $PatchSrc))  { Say ("[X] missing " + $PatchSrc); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
Say "[OK] preflight"

# ===================== 1. shared data: tools + docs + skills (once) ==========
Step "1. install tools / docs / skills (shared, paths unchanged from 0.1.x)"
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
$ToolsSrc = Join-Path $Root 'engineering\tools'
if (Test-Path $ToolsSrc) {
    $ToolsDst = Join-Path $DataDir 'tools'
    if (-not (Test-Path $ToolsDst)) { New-Item -ItemType Directory -Path $ToolsDst -Force | Out-Null }
    Copy-Item -Path (Join-Path $ToolsSrc '*') -Destination $ToolsDst -Recurse -Force
    Say ("[OK] tools  ->  " + $ToolsDst)
} else { Say "[WARN] engineering\tools not found" }

# the SW troubleshooting note has a Chinese filename; glob it instead of naming it
Get-ChildItem -File (Join-Path $Root 'engineering') -Filter '*.md' -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName -Destination $DataDir -Force; Say ("[OK] doc  ->  " + $_.Name) }

$SkillsSrc = Join-Path $Root 'engineering\skills'
if (Test-Path $SkillsSrc) {
    if (-not (Test-Path $UserSkills)) { New-Item -ItemType Directory -Path $UserSkills -Force | Out-Null }
    Copy-Item -Path (Join-Path $SkillsSrc '*') -Destination $UserSkills -Recurse -Force
    Say ("[OK] skills ->  " + $UserSkills)
} else { Say "[WARN] engineering\skills not found" }


# ============================= per-profile install ===========================
foreach ($prof in $targets) {
    $Web         = Join-Path $ProfilesDir $prof
    $NodeModules = Join-Path $Web 'node_modules'
    $ProfilePkg  = Join-Path $Web 'package.json'
    $ProfilePatch= Join-Path $Web 'cordis.patch.yml'

    Step ("profile: " + $prof + "   (" + $Web + ")")
    if (-not (Test-Path $ProfilePkg)) { Say ("[X] missing " + $ProfilePkg + " - skipped"); $script:Bad++; continue }
    if (-not (Test-Path $NodeModules)) { New-Item -ItemType Directory -Path $NodeModules -Force | Out-Null }

    # --- copy plugin packages (copy-overwrite only; never delete) ---
    # [problem-2 fix] only the UI plugin is installed now
    foreach ($pair in @(@{ n = $UI_NAME; s = $UI_SRC })) {
        $dst = Join-Path $NodeModules $pair.n
        if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
        Copy-Item -Path (Join-Path $pair.s '*') -Destination $dst -Recurse -Force
        $n = (Get-ChildItem -Recurse -File $dst -ErrorAction SilentlyContinue).Count
        Say ("  [OK] " + $pair.n + "  (" + $n + " files)")
    }

    # --- host-layer permission presets: merge in place, never overwrite ---
    $bak = $ProfilePatch + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    if (Test-Path $ProfilePatch) { Copy-Item $ProfilePatch $bak -Force; Say ("  [bak] " + (Split-Path $bak -Leaf)) }
    # [problem-2 fix] do NOT insert a custom preset; strip any residual block instead.
    if (Test-Path $ProfilePatch) {
        $ptxt = [System.IO.File]::ReadAllText($ProfilePatch).TrimStart([char]0xFEFF)
        if ($ptxt -match 'sw-single-line') {
            $nlch = if ($ptxt -match "`r`n") { "`r`n" } else { "`n" }
            $clean = [regex]::Replace($ptxt, '(?m)^[ 	]*sw-single-line:[ 	]*?
(?:[ 	]{6,}.*?
)*', '')
            if ($clean -ne $ptxt) {
                WriteNoBom $ProfilePatch $clean
                Say "  [OK] removed residual sw-single-line preset (standard three tiers kept)"
            } else { Say "  [WARN] sw-single-line found but not auto-removable; edit manually" }
        } else { Say "  [OK] no custom permission preset (standard three tiers only)" }
    }

    # --- register bundles in the profile manifest ---
    $pkg = ([System.IO.File]::ReadAllText($ProfilePkg).TrimStart([char]0xFEFF)) | ConvertFrom-Json
    $deps = [ordered]@{}
    foreach ($p in $pkg.dependencies.PSObject.Properties) { $deps[$p.Name] = $p.Value }
    $deps[$UI_NAME]   = ('file:./node_modules/' + $UI_NAME)

    $bundles = @()
    foreach ($b in $pkg.dsh.profile.bundles) { $bundles += $b }
    $ordered = @($UI_NAME)   # [problem-2 fix] only the UI plugin bundle
    foreach ($name in $ordered) {
        if ($bundles -notcontains $name) {
            $idx = [array]::IndexOf($bundles, '@deepseek-ai/dsh-web-app')
            if ($idx -lt 0) { $bundles += $name }
            else {
                $before = @($bundles[0..$idx])
                $after  = if ($idx + 1 -lt $bundles.Count) { @($bundles[($idx + 1)..($bundles.Count - 1)]) } else { @() }
                $bundles = $before + @($name) + $after
            }
        }
    }
    $out = [ordered]@{}
    $out['name']    = $pkg.name
    $out['private'] = $true
    if ($pkg.pnpm) { $out['pnpm'] = $pkg.pnpm }
    $out['dependencies'] = $deps
    $out['dsh'] = [ordered]@{ profile = [ordered]@{ bundles = $bundles } }
    WriteNoBom $ProfilePkg (($out | ConvertTo-Json -Depth 12) + "`n")
    Say "  [OK] bundles now:"
    foreach ($b in $bundles) { Say ("         " + $b) }

    # --- per-profile verify ---
    $checks = @(
        @{ n = 'package.json'; p = $ProfilePkg },
        @{ n = 'cordis.patch.yml'; p = $ProfilePatch },
        @{ n = ('node_modules\' + $UI_NAME + '\package.json'); p = (Join-Path $NodeModules ($UI_NAME + '\package.json')) },
        @{ n = ('node_modules\' + $UI_NAME + '\preset-engineering.patch.yml'); p = (Join-Path $NodeModules ($UI_NAME + '\preset-engineering.patch.yml')) }
    )
    foreach ($c in $checks) {
        if (Test-Path $c.p) { Say ("  [OK] " + $c.n) } else { Say ("  [MISSING] " + $c.n + " -> " + $c.p); $script:Bad++ }
    }
    $bytes = [System.IO.File]::ReadAllBytes($ProfilePkg)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Say ("  [BOM!] " + $ProfilePkg + " has a UTF-8 BOM - boot will fail"); $script:Bad++
    } else { Say "  [OK] package.json is BOM-less" }
    $pt = [System.IO.File]::ReadAllText($ProfilePatch)
    if ($pt -match 'sw-single-line') { Say "  [WARN] cordis.patch.yml still contains sw-single-line"; $script:Bad++ } else { Say "  [OK] no custom permission preset remains" }
    if ($pt -match 'defaultPreset')  { Say "  [OK] existing defaultPreset preserved" }
}

Step "done"
if ($script:Bad -eq 0) {
    Say "ALL GOOD."
    Say "RESTART the DSH desktop app (fully quit, then reopen) so the new bundles load."
    Say "Then: new session -> preset list should show the engineering preset."
} else {
    Say ("FINISHED WITH " + $script:Bad + " PROBLEM(S) - see [MISSING]/[BOM!]/[WARN] above.")
}
Say ("log: " + $LogPath)
WriteNoBom $LogPath $script:Log.ToString()
