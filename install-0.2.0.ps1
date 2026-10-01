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
# SAFETY: the profile's cordis.patch.yml is NEVER overwritten. An existing
#   `permission` row is edited IN PLACE - only `sw-single-line` is inserted
#   into its `presets:` map, so `defaultPreset` and every other key the user
#   already set survive. With no `permission` row at all, a full 4-preset row
#   is appended.
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
$SwLineSrc   = Join-Path $Root 'profiles\web-0.2.0\sw-single-line.presets.yml'
$LogPath     = Join-Path $Root 'install-0.2.0.log'

$UI_NAME   = 'dsh-engineering-ui'
$SWSL_NAME = 'dsH-engineering-sw-single-line'
$UI_SRC    = Join-Path $Root "engineering\plugins\$UI_NAME"
$SWSL_SRC  = Join-Path $Root "engineering\plugins\$SWSL_NAME"

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
if (-not (Test-Path $SWSL_SRC))  { Say ("[X] missing plugin source " + $SWSL_SRC); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
if (-not (Test-Path $SwLineSrc)) { Say ("[X] missing " + $SwLineSrc); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
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

# ----------------------------------------------------- helper: patch permission
function Merge-PermissionPreset {
    param([string]$PatchPath, [string]$SwLinePath)

    $nl = "`n"
    $raw = ''
    if (Test-Path $PatchPath) { $raw = [System.IO.File]::ReadAllText($PatchPath) }
    $raw = $raw.TrimStart([char]0xFEFF)
    if ($raw -match "`r`n") { $nl = "`r`n" }

    if ($raw -match 'sw-single-line') { return 'ALREADY' }

    $lines = @()
    if ($raw.Trim().Length -gt 0) { $lines = $raw -split "`r?`n" }

    # locate the `- id: permission` entry
    $permIdx = -1
    $permIndent = 0
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^(\s*)-\s*id:\s*permission\s*$') {
            $permIdx = $i
            $permIndent = $Matches[1].Length
            break
        }
    }

    $swLines = ([System.IO.File]::ReadAllText($SwLinePath).TrimStart([char]0xFEFF)) -split "`r?`n"

    if ($permIdx -lt 0) {
        # No permission row anywhere -> append the full canonical block.
        $block = [System.IO.File]::ReadAllText($PatchSrc).TrimStart([char]0xFEFF)
        if ($lines.Count -eq 0) {
            WriteNoBom $PatchPath $block
        } else {
            WriteNoBom $PatchPath (($lines -join $nl) + $nl + $nl + $block)
        }
        return 'APPENDED'
    }

    # bound this entry: next `- id:` at indent <= permIndent
    $endIdx = $lines.Count
    for ($i = $permIdx + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^(\s*)-\s*id:') {
            if ($Matches[1].Length -le $permIndent) { $endIdx = $i; break }
        }
    }

    # find `presets:` inside the entry
    $presetsIdx = -1
    $presetsIndent = 0
    for ($i = $permIdx + 1; $i -lt $endIdx; $i++) {
        if ($lines[$i] -match '^(\s*)presets:\s*$') {
            $presetsIdx = $i
            $presetsIndent = $Matches[1].Length
            break
        }
    }
    if ($presetsIdx -lt 0) { return 'NO_PRESETS_KEY' }

    # Append at the END of the presets map: walk forward while lines are part of it
    # (blank, or indented deeper than `presets:`); stop at a sibling key such as
    # `defaultPreset`, at the entry boundary, or at EOF.
    $insertAt = $presetsIdx + 1
    for ($i = $presetsIdx + 1; $i -lt $endIdx; $i++) {
        if ($lines[$i].Trim().Length -eq 0) { continue }
        if ($lines[$i] -match '^(\s*)\S') {
            if ($Matches[1].Length -gt $presetsIndent) { $insertAt = $i + 1; continue }
        }
        break
    }
    # drop a single trailing blank line at the insertion point so we do not double it up
    if ($insertAt -gt $presetsIdx + 1 -and $lines[$insertAt - 1].Trim().Length -eq 0) { $insertAt = $insertAt - 1 }

    $childIndent = ' ' * ($presetsIndent + 2)
    $insert = @()
    foreach ($l in $swLines) {
        if ($l.Trim().Length -eq 0) { $insert += '' } else { $insert += ($childIndent + $l) }
    }

    $out = @()
    if ($insertAt -gt 0) { $out += $lines[0..($insertAt - 1)] }
    $out += $insert
    if ($insertAt -le $lines.Count - 1) { $out += $lines[$insertAt..($lines.Count - 1)] }
    WriteNoBom $PatchPath (($out -join $nl))
    return 'MERGED'
}

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
    foreach ($pair in @(@{ n = $UI_NAME; s = $UI_SRC }, @{ n = $SWSL_NAME; s = $SWSL_SRC })) {
        $dst = Join-Path $NodeModules $pair.n
        if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
        Copy-Item -Path (Join-Path $pair.s '*') -Destination $dst -Recurse -Force
        $n = (Get-ChildItem -Recurse -File $dst -ErrorAction SilentlyContinue).Count
        Say ("  [OK] " + $pair.n + "  (" + $n + " files)")
    }

    # --- host-layer permission presets: merge in place, never overwrite ---
    $bak = $ProfilePatch + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    if (Test-Path $ProfilePatch) { Copy-Item $ProfilePatch $bak -Force; Say ("  [bak] " + (Split-Path $bak -Leaf)) }
    $r = Merge-PermissionPreset -PatchPath $ProfilePatch -SwLinePath $SwLineSrc
    switch ($r) {
        'ALREADY'        { Say "  [SKIP] sw-single-line already present" }
        'MERGED'         { Say "  [OK] inserted sw-single-line into existing permission row (defaultPreset & other keys preserved)" }
        'APPENDED'       { Say "  [OK] appended full permission row (4 presets incl. sw-single-line)" }
        'NO_PRESETS_KEY' { Say "  [WARN] permission row has no 'presets:' key - left untouched; add sw-single-line by hand"; $script:Bad++ }
    }

    # --- register bundles in the profile manifest ---
    $pkg = ([System.IO.File]::ReadAllText($ProfilePkg).TrimStart([char]0xFEFF)) | ConvertFrom-Json
    $deps = [ordered]@{}
    foreach ($p in $pkg.dependencies.PSObject.Properties) { $deps[$p.Name] = $p.Value }
    $deps[$UI_NAME]   = ('file:./node_modules/' + $UI_NAME)
    $deps[$SWSL_NAME] = ('file:./node_modules/' + $SWSL_NAME)

    $bundles = @()
    foreach ($b in $pkg.dsh.profile.bundles) { $bundles += $b }
    $ordered = @($SWSL_NAME, $UI_NAME)   # sw-single-line host service first, then the UI plugin
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
        @{ n = ('node_modules\' + $UI_NAME + '\preset-engineering.patch.yml'); p = (Join-Path $NodeModules ($UI_NAME + '\preset-engineering.patch.yml')) },
        @{ n = ('node_modules\' + $SWSL_NAME + '\package.json'); p = (Join-Path $NodeModules ($SWSL_NAME + '\package.json')) }
    )
    foreach ($c in $checks) {
        if (Test-Path $c.p) { Say ("  [OK] " + $c.n) } else { Say ("  [MISSING] " + $c.n + " -> " + $c.p); $script:Bad++ }
    }
    $bytes = [System.IO.File]::ReadAllBytes($ProfilePkg)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Say ("  [BOM!] " + $ProfilePkg + " has a UTF-8 BOM - boot will fail"); $script:Bad++
    } else { Say "  [OK] package.json is BOM-less" }
    $pt = [System.IO.File]::ReadAllText($ProfilePatch)
    if ($pt -match 'sw-single-line') { Say "  [OK] cordis.patch.yml contains sw-single-line" } else { Say "  [MISSING] sw-single-line not in cordis.patch.yml"; $script:Bad++ }
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
