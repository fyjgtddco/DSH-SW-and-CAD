# =============================================================================
# sync-to-dsh.ps1 - sync Engineering-Mode CODE changes into the DSH install
# =============================================================================
# WHY: Engineering Mode lives in two places on disk:
#   A) source workspace : <repo>\engineering\            (where edits happen)
#   B) installed copy   : ~/.dsh/.agent-presets/engineering/  and
#                         ~/.dsh/profiles/*/node_modules/dsh-engineering-ui
# DSH actually loads (B). Editing only (A) leaves the host running old code.
# Measured consequence: a stale defense-sign.js means the /defense/sign endpoint
# and the cross-copy workspace reading are both unavailable, so no defense
# credential can be signed (the "fixed but not in effect" Bug-A / Bug-02).
#
# RULES:
#   * Sync CODE / RULES / SKILLS / PROMPTS only. Never overwrite runtime state.
#   * Back up every file that is about to be overwritten (.bak-sync-<stamp>).
#   * Idempotent: safe to run repeatedly.
#
# USAGE: powershell -ExecutionPolicy Bypass -File sync-to-dsh.ps1
#        powershell -ExecutionPolicy Bypass -File sync-to-dsh.ps1 -DryRun
#        powershell -ExecutionPolicy Bypass -File sync-to-dsh.ps1 -Register
#          -Register also declares the plugin in every profile's package.json
#          (dependencies + dsh.profile.bundles). Needed when handing this
#          preset to someone else: a profile that does not declare the plugin
#          will not load the engineering UI at all.
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as ANSI unless a
# UTF-8 BOM is present, so non-ASCII literals here would corrupt parsing.
# =============================================================================
param(
    [switch]$DryRun,
    [switch]$Register,
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$Stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'

# Runtime state: never copy these from the source workspace (session live data).
$RuntimeNames = @(
    'mode_state.json', 'workflow_state.json', 'sw_state.json',
    'platform_status.json', 'artifacts_registry.json', 'TASK_FINISHED.json',
    'connection_state.json', 'design_params.json', 'defense_bypass.json',
    'violations.json', 'guard.json', 'runtime.json'
)
$RuntimeDirs = @('reports', 'heartbeats', 'load_cases', 'physics_runs',
                 'mode_logs', '.defense', '__pycache__', 'output')

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }

$script:Stats = @{ copied = 0; skipped = 0; backed = 0; bytes = 0 }

function Should-Skip([string]$Name) {
    if ($RuntimeNames -contains $Name) { return $true }
    if ($Name -like '*.bak*') { return $true }
    if ($Name -like '*.pyc') { return $true }
    if ($Name -like '~$*') { return $true }
    # Deliverable/artefact binaries are NOT code: skip them so the install copy
    # stays lean and syncs fast. They are reproduced by modelling, not shipped.
    $lower = $Name.ToLower()
    foreach ($ext in @('.sldprt', '.sldasm', '.slddrw', '.dwg', '.dxf', '.step',
                       '.stp', '.igs', '.stl', '.pdf', '.png', '.jpg', '.jpeg',
                       '.zip', '.7z')) {
        if ($lower.EndsWith($ext)) { return $true }
    }
    return $false
}

function Copy-Tree([string]$Src, [string]$Dst, [string]$Label) {
    if (-not (Test-Path $Src)) { Say ("  [WARN] source missing: " + $Src) 'Yellow'; return }
    if (-not (Test-Path $Dst)) { New-Item -ItemType Directory -Path $Dst -Force | Out-Null }
    $files = Get-ChildItem -Path $Src -Recurse -File -Force -ErrorAction SilentlyContinue
    $n = 0
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($Src.Length).TrimStart('\')
        $parts = $rel -split '\\'
        $inRuntime = $false
        foreach ($p in $parts) { if ($RuntimeDirs -contains $p) { $inRuntime = $true } }
        if ($inRuntime) { $script:Stats.skipped++; continue }
        if (Should-Skip $f.Name) { $script:Stats.skipped++; continue }
        $target = Join-Path $Dst $rel
        $tdir = Split-Path $target -Parent
        if (-not (Test-Path $tdir)) { New-Item -ItemType Directory -Path $tdir -Force | Out-Null }
        if (Test-Path $target) {
            $same = $false
            try {
                if ((Get-FileHash $f.FullName).Hash -eq (Get-FileHash $target).Hash) { $same = $true }
            } catch { }
            if ($same) { $script:Stats.skipped++; continue }
            if (-not $DryRun) {
                Copy-Item $target ($target + '.bak-sync-' + $Stamp) -Force
                $script:Stats.backed++
            }
        }
        if ($DryRun) {
            Say ("    [dry] " + $rel) 'DarkGray'
        } else {
            Copy-Item $f.FullName $target -Force
        }
        $script:Stats.copied++
        $script:Stats.bytes += $f.Length
        $n++
    }
    Say ("  [OK] " + $Label + "  (" + $n + " file(s) synced -> " + $Dst + ")") 'Green'
}

Say "================================================" Cyan
Say "  Engineering Mode : code sync -> DSH install" Cyan
if ($DryRun) { Say "  *** DRY RUN : showing what would be synced ***" Yellow }
Say "================================================" Cyan
Say ("source workspace : " + $Root)
Say ("DSH_HOME         : " + $DshHome)
Say ""

# ---- 1. tools/ -> <DSH_HOME>\.agent-presets\engineering\tools --------------
Say "---- 1. tools/ (gates / physics / SW bridge) ----" Yellow
$ToolsSrc = Join-Path $Root 'engineering\tools'
$DataDir  = Join-Path $DshHome '.agent-presets\engineering'
$ToolsDst = Join-Path $DataDir 'tools'
Copy-Tree $ToolsSrc $ToolsDst 'tools'

# ---- 2. skills/ -> <DSH_HOME>\skills --------------------------------------
Say ""
Say "---- 2. skills/ (7 skills) ----" Yellow
$SkillsSrc = Join-Path $Root 'engineering\skills'
$SkillsDst = Join-Path $DshHome 'skills'
Copy-Tree $SkillsSrc $SkillsDst 'skills'

# ---- 3. plugin -> each profile's node_modules -----------------------------
Say ""
Say "---- 3. dsh-engineering-ui -> profiles\*\node_modules ----" Yellow
$UiSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-ui'
$ProfilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path $UiSrc)) {
    Say ("  [WARN] plugin source missing: " + $UiSrc) 'Yellow'
} elseif (-not (Test-Path $ProfilesDir)) {
    Say ("  [WARN] profiles dir missing: " + $ProfilesDir) 'Yellow'
} else {
    $targets = @()
    if ($Profile.Count -gt 0) {
        foreach ($p in $Profile) { if (Test-Path (Join-Path $ProfilesDir $p)) { $targets += $p } }
    } else {
        Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue |
            ForEach-Object { $targets += $_.Name }
    }
    foreach ($prof in $targets) {
        $nm = Join-Path (Join-Path $ProfilesDir $prof) 'node_modules'
        $dst = Join-Path $nm 'dsh-engineering-ui'
        $pkg = Join-Path (Join-Path $ProfilesDir $prof) 'package.json'
        $declared = $false
        if (Test-Path $pkg) {
            try {
                $txt = [System.IO.File]::ReadAllText($pkg)
                if ($txt -match 'dsh-engineering-ui') { $declared = $true }
            } catch { $declared = $false }
        }
        # ---- auto-register (needed when handing the preset to someone else) ----
        # A profile that does not declare the plugin will NOT load it, so the
        # engineering UI would be missing even though files were copied.
        # This was a real delivery gap: the 'web' profile did not declare it,
        # while install-plugin.ps1 targeted only 'web'.
        # -Register makes sync install + declare the plugin in every profile.
        if (-not $declared -and $Register) {
            $script:Stats.copied++
            Say ("  [register] profile '" + $prof + "' (adding dependency + bundle)") 'Green'
            if (-not $DryRun) {
                try {
                    Copy-Item $pkg ($pkg + '.bak-sync-' + $Stamp) -Force
                    $script:Stats.backed++
                    $raw = [System.IO.File]::ReadAllText($pkg)
                    $obj = $raw | ConvertFrom-Json
                    $deps = [ordered]@{}
                    foreach ($p in $obj.dependencies.PSObject.Properties) { $deps[$p.Name] = $p.Value }
                    if (-not $deps.Contains('dsh-engineering-ui')) {
                        $deps['dsh-engineering-ui'] = 'file:./node_modules/dsh-engineering-ui'
                    }
                    $bundles = @()
                    if ($obj.dsh -and $obj.dsh.profile -and $obj.dsh.profile.bundles) {
                        foreach ($b in $obj.dsh.profile.bundles) { $bundles += $b }
                    }
                    if ($bundles -notcontains 'dsh-engineering-ui') {
                        $idx = [array]::IndexOf($bundles, '@deepseek-ai/dsh-web-app')
                        if ($idx -lt 0) { $bundles += 'dsh-engineering-ui' }
                        else {
                            $before = @($bundles[0..$idx])
                            $after = @()
                            if ($idx + 1 -lt $bundles.Count) {
                                $after = @($bundles[($idx + 1)..($bundles.Count - 1)])
                            }
                            $bundles = $before + @('dsh-engineering-ui') + $after
                        }
                    }
                    $out = [ordered]@{}
                    $out['name'] = $obj.name
                    $out['private'] = $true
                    if ($obj.pnpm) { $out['pnpm'] = $obj.pnpm }
                    $out['dependencies'] = $deps
                    $out['dsh'] = [ordered]@{ profile = [ordered]@{ bundles = $bundles } }
                    [System.IO.File]::WriteAllText($pkg,
                        ($out | ConvertTo-Json -Depth 12),
                        (New-Object System.Text.UTF8Encoding($false)))
                    $declared = $true
                } catch {
                    Say ("  [WARN] register failed for '" + $prof + "': " + $_.Exception.Message) 'Yellow'
                }
            }
        }
        if (-not $declared -and -not (Test-Path $dst)) {
            Say ("  [skip] profile '" + $prof + "' does not declare the plugin (use -Register)") 'DarkGray'
            continue
        }
        Say ("  profile: " + $prof) 'Gray'
        Copy-Tree $UiSrc $dst ('plugin@' + $prof)
    }
}

# ---- 4. preset-layer files at the engineering root -------------------------
Say ""
Say "---- 4. preset layer (agent.cordis.yml / preset.yml / root scripts) ----" Yellow
# Besides the two preset YAMLs, the engineering root also holds code/doc files
# (e.g. install-plugin.ps1) that are part of the preset and must ship too.
# The earlier implementation missed install-plugin.ps1 -- the workspace had it
# but the install copy did not.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a BOM-less
#       .ps1 as ANSI, so non-ASCII comments get mangled and can break parsing
#       of the statements that follow (this actually happened: RootFiles
#       silently became empty).
$RootFiles = @('agent.cordis.yml', 'preset.yml', 'install-plugin.ps1', 'README.md')
foreach ($f in $RootFiles) {
    $s = Join-Path $Root ('engineering\' + $f)
    if (-not (Test-Path $s)) { continue }
    if (Should-Skip $f) { $script:Stats.skipped++; continue }
    $d = Join-Path $DataDir $f
    $same = $false
    if (Test-Path $d) {
        try { if ((Get-FileHash $s).Hash -eq (Get-FileHash $d).Hash) { $same = $true } } catch { }
    }
    if ($same) { $script:Stats.skipped++; Say ("  [same] " + $f) 'DarkGray' }
    elseif ($DryRun) { Say ("    [dry] " + $f) 'DarkGray' }
    else {
        if (Test-Path $d) {
            Copy-Item $d ($d + '.bak-sync-' + $Stamp) -Force
            $script:Stats.backed++
        }
        Copy-Item $s $d -Force
        $script:Stats.copied++
        Say ("  [OK] " + $f) 'Green'
    }
}

Say ""
Say "================================================" Cyan
Say ("  done   copied=" + $script:Stats.copied +
     "  skipped=" + $script:Stats.skipped +
     "  backed_up=" + $script:Stats.backed +
     "  (" + [math]::Round($script:Stats.bytes / 1KB, 1) + " KB)") Cyan
Say "================================================" Cyan
if (-not $DryRun -and $script:Stats.copied -gt 0) {
    Say ""
    Say "!! The plugin host half and the .defense trust root are PROCESS-LEVEL." Red
    Say "!! FULLY QUIT and reopen the DSH desktop app to load the new code." Red
    Say "!! Afterwards .agent-presets\engineering\tools\.defense\runtime.json" Red
    Say "!! is generated and /defense/sign becomes available, so the three" Red
    Say "!! defense lines can actually sign credentials." Red
}
