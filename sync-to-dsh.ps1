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
    # [2026-11-18] Copy-only sync cannot express DELETIONS. When the repo drops a
    #   file (e.g. the ten-stage regression tests removed in the seven-stage
    #   switch), the stale copy stays in the install and keeps running against
    #   APIs that no longer exist. -Prune removes install-side files that do not
    #   exist in the repo source tree (runtime state is always protected).
    [switch]$Prune,
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
    'violations.json', 'guard.json', 'runtime.json',
    'model_chain.json', 'model_chain_pending.json', 'persona.json',
    # 【2026-10-28】这三份也是【运行时/本机数据】，不是代码：
    #   platform_agents.json  插件自动写的平台子代理注册表（每次会话都在变）
    #   part_registry.json    零件台账（本任务数据，覆盖 = 丢数据）
    #   custom_materials.json 用户自己登记的材料（覆盖 = 丢用户数据）
    'platform_agents.json', 'part_registry.json', 'custom_materials.json',
    # -- [换机修复] 本机设置绝不跟着同步 ----------------------------------
    # settings.json 记的是【这台电脑】的交付目录 / SW-CAD-Python 路径，
    # 带着机器指纹。同步到别的电脑毫无意义（会被指纹判为外来而忽略），
    # 还可能误导排障，因此按运行时数据处理。
    'settings.json',
    'webots_state.json', 'webots_jobs.sqlite3', 'webots_jobs.sqlite3-wal',
    'webots_jobs.sqlite3-shm', 'webots_instance.lock'
)
$RuntimeDirs = @('reports', 'heartbeats', 'load_cases', 'physics_runs', 'seven_stage',
                 'mode_logs', '.defense', '__pycache__', 'output', 'webots_runs')

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }

$script:Stats = @{ copied = 0; skipped = 0; backed = 0; bytes = 0; pruned = 0 }

function Should-Skip([string]$Name) {
    if ($RuntimeNames -contains $Name) { return $true }
    if ($Name -like '*.bak*') { return $true }
    if ($Name -like '*.pyc') { return $true }
    # PID files are live process state, not code. Copying the source workspace's
    # stale pid over the install copy makes sw_monitor look dead (or point at an
    # unrelated process). Seen twice on 2026-11-05: it overwrote a live 37012
    # with an old 27656, so it is excluded here rather than restored by hand.
    if ($Name -like '*.pid') { return $true }
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
    if ($Prune) { Remove-Orphans $Src $Dst $Label }
}

# Remove files that exist in $Dst but NOT in $Src (i.e. the repo deleted them).
# Guard rails, in this order:
#   * runtime state / .bak / .pid / build artefacts are never touched;
#   * only files inside the runtime dirs list are skipped too;
#   * the file is backed up (.bak-prune-<stamp>) before removal so a wrong prune
#     is one copy away from being undone;
#   * $DryRun only reports.
# Only SOURCE extensions are eligible. Everything else stays, even when the repo
# no longer has it:
#   * .json  is a runtime/config format here (mode2_plan.json, sw_monitor.json,
#     compaction_engine.json, artifacts_registry.*.json, pages_manifest.json ...),
#   * .jsonl/.db/.sqlite3/.token/.log/.txt are live session data,
#   * deliverable binaries are never shipped at all.
# Deleting a stale .json would destroy a running task's state; the reverse
# mistake (leaving a stale .json behind) is harmless because every reader
# validates its schema. So the safe direction is: prune code, never data.
$PruneExts = @('.py', '.js', '.mjs', '.cjs', '.ts', '.md', '.yml', '.yaml',
               '.ps1', '.sh', '.lsp', '.h', '.hpp', '.cpp', '.c')
# Directories that hold live data regardless of file extension.
$PruneProtectedDirs = @('compaction_facts', 'trace', 'sw_monitor', '运行数据',
                        'reports', 'heartbeats', 'physics_runs', 'mode_logs',
                        'seven_stage', '__pycache__', '.git')

function Remove-Orphans([string]$Src, [string]$Dst, [string]$Label) {
    if (-not (Test-Path $Src) -or -not (Test-Path $Dst)) { return }
    $srcSet = @{}
    Get-ChildItem -Path $Src -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $srcSet[$_.FullName.Substring($Src.Length).TrimStart('\')] = $true }
    $removed = 0
    Get-ChildItem -Path $Dst -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($Dst.Length).TrimStart('\')
        if ($srcSet.ContainsKey($rel)) { return }
        # (1) source extensions only
        if ($PruneExts -notcontains $_.Extension.ToLower()) { return }
        # (2) never a runtime / state file, whatever its extension
        if (Should-Skip $_.Name) { return }
        # (3) never inside a data directory
        $parts = $rel -split '\\'
        foreach ($p in $parts) {
            if ($RuntimeDirs -contains $p) { return }
            if ($PruneProtectedDirs -contains $p) { return }
        }
        if ($DryRun) {
            Say ("    [prune-dry] " + $rel) 'DarkGray'
        } else {
            Copy-Item $_.FullName ($_.FullName + '.bak-prune-' + $Stamp) -Force
            Remove-Item $_.FullName -Force
            Say ("    [prune] " + $rel) 'Yellow'
        }
        $script:Stats.pruned++
        $removed++
    }
    if ($removed -gt 0) { Say ("  [OK] " + $Label + "  (" + $removed + " orphan(s) pruned)") 'Yellow' }
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
Say "---- 3. plugins -> profiles\*\node_modules ----" Yellow
$UiSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-ui'
# [2026-11-08] Engineering-mode compaction engine. preset-engineering.patch.yml's
#   compaction child refers to it by package name 'dsh-engineering-compaction';
#   if that package is missing the row cannot resolve and the WHOLE engineering
#   preset fails to load (symptom: the engineering UI silently vanished).
#   It does NOT go into dsh.profile.bundles: a context may hold only one
#   ctx.compaction, so mounting it at the host layer is duplicate registration.
$CompactSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-compaction'
# [2026-11-16] Guarded memory compaction backend. preset-engineering.patch.yml
#   now points the compaction child at this package name instead. Same rules:
#   files only (preset child), never a dsh.profile.bundles entry.
$GuardSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-memory-guarded'
# [2026-11-18] Seven-stage analysis plugin (Mode 2's ONLY analysis engine).
#   preset-engineering.patch.yml declares the row 'engineering-seven-stage' by
#   package name; if the package is not in node_modules the row cannot resolve
#   and the WHOLE engineering preset fails to load (engineering mode vanishes).
#   Files only -- never a dsh.profile.bundles entry (same rule as compaction).
$SevenSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-seven-stage'
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
                    # Compaction engine: dependency only, NEVER a bundle entry.
                    #   The preset resolves it by package name from the profile's
                    #   node_modules; listing it under dsh.profile.bundles would
                    #   mount it at the host layer and duplicate ctx.compaction.
                    if (-not $deps.Contains('dsh-engineering-compaction')) {
                        $deps['dsh-engineering-compaction'] = 'file:./node_modules/dsh-engineering-compaction'
                    }
                    # [2026-11-16] the guarded backend the preset now references.
                    if (-not $deps.Contains('dsh-engineering-memory-guarded')) {
                        $deps['dsh-engineering-memory-guarded'] = 'file:./node_modules/dsh-engineering-memory-guarded'
                    }
                    # [2026-11-18] seven-stage: dependency only (preset child row).
                    if (-not $deps.Contains('dsh-engineering-seven-stage')) {
                        $deps['dsh-engineering-seven-stage'] = 'file:./node_modules/dsh-engineering-seven-stage'
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
        # Compaction engine: files only, never a bundle (see $CompactSrc above).
        #   Its only job: make the preset row resolvable by package name.
        if (Test-Path $CompactSrc) {
            Copy-Tree $CompactSrc (Join-Path $nm 'dsh-engineering-compaction') ('compaction@' + $prof)
        }
        # [2026-11-16] Guarded backend: files only, never a bundle entry.
        if (Test-Path $GuardSrc) {
            Copy-Tree $GuardSrc (Join-Path $nm 'dsh-engineering-memory-guarded') ('guard@' + $prof)
        }
        # [2026-11-18] Seven-stage analysis plugin: files only, never a bundle.
        #   Its lib/steps.json (63 sub-steps) MUST ship with it -- without it
        #   flow.js throws on import and the preset row fails to mount.
        if (Test-Path $SevenSrc) {
            Copy-Tree $SevenSrc (Join-Path $nm 'dsh-engineering-seven-stage') ('seven@' + $prof)
        }

        # ---- dependency declaration backfill (always, not only with -Register) ----
        # 现场（2026-11-16）：文件已经铺进 node_modules，但 profile 的
        #   package.json 里没有 'dsh-engineering-memory-guarded'，于是 preset 那行
        #   按包名解析失败 → 整个 engineering 预设加载失败（表现就是工程模式消失）。
        #   旧实现只在 -Register 分支里补依赖，日常同步不会补 —— 这是一个静默缺口。
        #   现在改成：只要文件铺成功就核对一遍依赖，缺则补（原始文件先备份）。
        $needDeps = @()
        if (Test-Path $pkg) {
            $ptxt = [System.IO.File]::ReadAllText($pkg)
            foreach ($depName in @('dsh-engineering-compaction', 'dsh-engineering-memory-guarded', 'dsh-engineering-seven-stage')) {
                if ($ptxt -notmatch [regex]::Escape('"' + $depName + '"')) { $needDeps += $depName }
            }
        }
        if ($needDeps.Count -gt 0) {
            if ($DryRun) {
                Say ("  [dry] would declare dependency: " + ($needDeps -join ', ')) 'DarkGray'
            } else {
                try {
                    Copy-Item $pkg ($pkg + '.bak-sync-' + $Stamp) -Force
                    $script:Stats.backed++
                    $raw = [System.IO.File]::ReadAllText($pkg)
                    $obj2 = $raw | ConvertFrom-Json
                    $deps2 = [ordered]@{}
                    foreach ($q in $obj2.dependencies.PSObject.Properties) { $deps2[$q.Name] = $q.Value }
                    foreach ($depName in $needDeps) {
                        $deps2[$depName] = 'file:./node_modules/' + $depName
                    }
                    $bundles2 = @()
                    if ($obj2.dsh -and $obj2.dsh.profile -and $obj2.dsh.profile.bundles) {
                        foreach ($b2 in $obj2.dsh.profile.bundles) { $bundles2 += $b2 }
                    }
                    $out2 = [ordered]@{}
                    $out2['name'] = $obj2.name
                    $out2['private'] = $true
                    if ($obj2.pnpm) { $out2['pnpm'] = $obj2.pnpm }
                    $out2['dependencies'] = $deps2
                    $out2['dsh'] = [ordered]@{ profile = [ordered]@{ bundles = $bundles2 } }
                    [System.IO.File]::WriteAllText($pkg,
                        ($out2 | ConvertTo-Json -Depth 12) + "`n",
                        (New-Object System.Text.UTF8Encoding($false)))
                    Say ("  [OK] declared dependency: " + ($needDeps -join ', ')) 'Green'
                } catch {
                    Say ("  [WARN] dependency backfill failed for '" + $prof + "': " + $_.Exception.Message) 'Yellow'
                }
            }
        }
    }
}

# ---- 3.5 docs/ -> <DSH_HOME>\.agent-presets\engineering\docs ------------
# 设计/重构文档也要随代码走，否则安装副本上查不到本次改动的契约。
$DocsSrc = Join-Path $Root 'engineering\docs'
if (Test-Path $DocsSrc) {
    Say ""
    Say "---- 3.5 docs/ (design notes) ----" 'Yellow'
    Copy-Tree $DocsSrc (Join-Path $DataDir 'docs') 'docs'
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
     "  pruned=" + $script:Stats.pruned +
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
