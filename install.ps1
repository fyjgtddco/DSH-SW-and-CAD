# =============================================================================
#  install.ps1 - one-click installer for DSH Engineering Mode
# =============================================================================
#  WHAT THIS DOES
#    1. Preflight: locate DSH_HOME (default ~/.dsh) and require profiles to
#       exist (start DSH once first if they do not).
#    2. Register the dsh-engineering-ui plugin in EVERY profile
#       (package.json: dependencies + dsh.profile.bundles). Without this the
#       engineering UI never loads -- this was a real gap: the 'web' profile
#       did not declare the plugin while install targeted only 'web'.
#    3. Sync engineering files to their install locations:
#         engineering\tools\   -> <DSH>\.agent-presets\engineering\tools
#         engineering\skills\  -> <DSH>\skills
#         engineering\plugins\ -> <DSH>\profiles\<profile>\node_modules\...
#         root scripts / yml   -> <DSH>\.agent-presets\engineering\
#    4. Verify and remind you to restart DSH.
#
#  USAGE
#    powershell -ExecutionPolicy Bypass -File install.ps1
#    powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
#    powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop
#
#  SAFETY
#    * Nothing is ever bulk-deleted: overwritten files are backed up as
#      <file>.bak-sync-<stamp>.
#    * Runtime state (current room / material / workflow) is never copied.
#    * Idempotent - safe to run repeatedly.
#
#  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a BOM-less
#        .ps1 as ANSI, so non-ASCII literals here would corrupt parsing.
# =============================================================================
param(
    [switch]$DryRun,
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Rule($t) {
    Say ""
    Say "================================================================" Cyan
    if ($t) { Say ("  " + $t) Cyan }
    Say "================================================================" Cyan
}

$DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }

Rule "DSH Engineering Mode - one-click install"
Say ("  source   : " + $Root) Gray
Say ("  DSH_HOME : " + $DshHome) Gray
if ($DryRun) { Say "  *** DRY RUN - nothing will be written ***" Yellow }

# ---- preflight --------------------------------------------------------------
$ProfilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path $DshHome)) {
    Say ""
    Say ("[X] DSH_HOME not found: " + $DshHome) Red
    Say "    Start DSH once so it creates its home directory, then re-run." Yellow
    exit 1
}
if (-not (Test-Path $ProfilesDir)) {
    Say ""
    Say ("[X] profiles directory not found: " + $ProfilesDir) Red
    Say "    Start DSH once so it creates at least one profile, then re-run." Yellow
    exit 1
}

$allProfiles = @()
Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue |
    ForEach-Object { $allProfiles += $_.Name }
if ($Profile.Count -gt 0) {
    $targets = @()
    foreach ($p in $Profile) {
        if ($allProfiles -contains $p) { $targets += $p }
        else { Say ("  [WARN] profile not found, skipped: " + $p) Yellow }
    }
} else {
    $targets = $allProfiles
}
if ($targets.Count -eq 0) {
    Say ""
    Say "[X] no target profile found." Red
    exit 1
}
Say ("  profiles : " + ($targets -join ', ')) Gray

$UiSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-ui'
if (-not (Test-Path $UiSrc)) {
    Say ""
    Say ("[X] plugin source missing: " + $UiSrc) Red
    exit 1
}
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# ---- 1. register plugin in every profile ------------------------------------
Rule "1/2  register plugin in profiles"
$regCount = 0
foreach ($prof in $targets) {
    $pd  = Join-Path $ProfilesDir $prof
    $pkg = Join-Path $pd 'package.json'
    if (-not (Test-Path $pkg)) {
        Say ("  [skip] " + $prof + " : no package.json") DarkGray
        continue
    }
    $obj = ([System.IO.File]::ReadAllText($pkg)) | ConvertFrom-Json

    $deps = [ordered]@{}
    foreach ($p in $obj.dependencies.PSObject.Properties) { $deps[$p.Name] = $p.Value }
    $needDep = -not $deps.Contains('dsh-engineering-ui')

    $bundles = @()
    if ($obj.dsh -and $obj.dsh.profile -and $obj.dsh.profile.bundles) {
        foreach ($b in $obj.dsh.profile.bundles) { $bundles += $b }
    }
    $needBundle = ($bundles -notcontains 'dsh-engineering-ui')

    if (-not $needDep -and -not $needBundle) {
        Say ("  [same] " + $prof + " : already registered") DarkGray
        continue
    }
    if ($DryRun) {
        Say ("    [dry] " + $prof + " : would register (dep=" + $needDep +
             " bundle=" + $needBundle + ")") DarkGray
        $regCount++
        continue
    }

    if ($needDep) {
        $deps['dsh-engineering-ui'] = 'file:./node_modules/dsh-engineering-ui'
    }
    if ($needBundle) {
        # Insert right before the web-app bundle so the UI shell the plugin
        # extends is loaded first; append when the anchor is absent.
        $idx = [array]::IndexOf($bundles, '@deepseek-ai/dsh-web-app')
        if ($idx -lt 0) {
            $bundles += 'dsh-engineering-ui'
        } else {
            $before = @($bundles[0..$idx])
            $after  = @()
            if ($idx + 1 -lt $bundles.Count) {
                $after = @($bundles[($idx + 1)..($bundles.Count - 1)])
            }
            $bundles = $before + @('dsh-engineering-ui') + $after
        }
    }

    $out = [ordered]@{}
    $out['name']    = $obj.name
    $out['private'] = $true
    if ($obj.pnpm) { $out['pnpm'] = $obj.pnpm }
    $out['dependencies'] = $deps
    $out['dsh'] = [ordered]@{ profile = [ordered]@{ bundles = $bundles } }

    Copy-Item $pkg ($pkg + '.bak-sync-' + $Stamp) -Force
    [System.IO.File]::WriteAllText($pkg,
        ($out | ConvertTo-Json -Depth 12),
        (New-Object System.Text.UTF8Encoding($false)))
    Say ("  [OK] " + $prof + " : registered") Green
    $regCount++
}

# ---- 2. sync code (delegates to the maintained sync script) -----------------
Rule "2/2  sync engineering code"
$Sync = Join-Path $Root 'sync-to-dsh.ps1'
if (-not (Test-Path $Sync)) {
    Say ("  [X] sync-to-dsh.ps1 not found next to install.ps1: " + $Sync) Red
    exit 1
}
$syncArgs = @()
if ($DryRun) { $syncArgs += '-DryRun' }
if ($Profile.Count -gt 0) { $syncArgs += @('-Profile') + $Profile }
& powershell -NoProfile -ExecutionPolicy Bypass -File $Sync @syncArgs

# ---- 3. verify --------------------------------------------------------------
Rule "verify"
$PresetDir = Join-Path $DshHome '.agent-presets\engineering'
$bad = 0
function Check($name, $path) {
    if (Test-Path $path) { Say ("  [OK]    " + $name) Green }
    else { Say ("  [MISS]  " + $name + "  -> " + $path) Red; $script:bad++ }
}
Check "agent.cordis.yml"  (Join-Path $PresetDir 'agent.cordis.yml')
Check "preset.yml"        (Join-Path $PresetDir 'preset.yml')
Check "install-plugin.ps1" (Join-Path $PresetDir 'install-plugin.ps1')
Check "tools/swapi.py"    (Join-Path $PresetDir 'tools\swapi.py')
Check "tools/sw_bridge.py" (Join-Path $PresetDir 'tools\sw_bridge.py')
Check "tools/physics/material_db.py" (Join-Path $PresetDir 'tools\physics\material_db.py')
Check "tools/physics/gb_materials.json" (Join-Path $PresetDir 'tools\physics\gb_materials.json')
Check "skills"            (Join-Path $DshHome 'skills')
foreach ($prof in $targets) {
    Check ("plugin@" + $prof) (Join-Path $ProfilesDir ($prof + '\node_modules\dsh-engineering-ui\lib\index.js'))
}

# ---- done -------------------------------------------------------------------
Rule "done"
Say ("  registered profiles : " + $regCount) Gray
Say ("  verification errors : " + $bad) Gray
Say ""
Say "  NEXT STEPS" Yellow
Say "    1. FULLY QUIT and reopen DSH." Gray
Say "       The plugin host half and the .defense trust root are process-level," Gray
Say "       so a page refresh alone is not enough." Gray
Say "    2. Settings -> Agent Preset -> pick 'Engineering Mode'." Gray
Say "    3. On first run .agent-presets\engineering\tools\.defense\runtime.json" Gray
Say "       is generated and the defense endpoints become available." Gray
Say ""
Say "  Desktop and Web profiles are both supported." Gray
Say "================================================================" Cyan
if ($bad -gt 0) { exit 1 }
