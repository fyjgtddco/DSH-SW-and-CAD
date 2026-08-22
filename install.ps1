<#
.SYNOPSIS
    Install Engineering Mode for DeepSeek Harness + DSH_SW SolidWorks Bridge
.DESCRIPTION
    Installs:
    1. The Engineering Mode Agent Preset
    2. The DSH_SW SolidWorks bridge scripts
    3. All skill files
#>

$ErrorActionPreference = "Stop"

# Configuration
$PRESET_ID = "engineering"
$DSH_HOME = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE ".dsh" }
$PRESET_DIR = Join-Path $DSH_HOME ".agent-presets" $PRESET_ID
$SKILLS_DIR = Join-Path $DSH_HOME "skills"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$TOOLS_DIR = Join-Path $PRESET_DIR "tools"

# Colors
$GREEN = [console]::ForegroundColor = "Green"
$CYAN = [console]::ForegroundColor = "Cyan"
$YELLOW = [console]::ForegroundColor = "Yellow"
$RED = [console]::ForegroundColor = "Red"
$WHITE = [console]::ForegroundColor = "White"
$RESET = [console]::ForegroundColor = "White"

function Write-Title {
    param([string]$Text)
    Write-Host "`n--- $Text ---" -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Text, [string]$Status = "INFO")
    switch ($Status) {
        "OK"    { Write-Host "[$Status] $Text" -ForegroundColor Green }
        "SKIP"  { Write-Host "[$Status] $Text" -ForegroundColor Yellow }
        "ERROR" { Write-Host "[$Status] $Text" -ForegroundColor Red }
        default { Write-Host "[$Status] $Text" -ForegroundColor White }
    }
}

# Main
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DSH Engineering Mode Installer" -ForegroundColor Cyan
Write-Host "  + DSH_SW SolidWorks Bridge" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Check DSH
Write-Title "Step 1: Checking DSH Installation"
if (-not (Test-Path $DSH_HOME)) {
    Write-Step "DSH home not found: $DSH_HOME" "ERROR"
    Write-Host "Please install DeepSeek Harness first." -ForegroundColor Red
    exit 1
}
Write-Step "DSH home: $DSH_HOME" "OK"

# Step 2: Find DSH_SW source
Write-Title "Step 2: Finding DSH_SW Source"
$dsh_sw_source = $null
$possible_paths = @(
    "$SCRIPT_DIR\..\DSH_SW-main",
    "$SCRIPT_DIR\..\DSH_SW",
    "$env:USERPROFILE\Desktop\DSH_SW-main",
    "$env:USERPROFILE\Desktop\DSH_SW"
)
foreach ($p in $possible_paths) {
    if (Test-Path $p) {
        $dsh_sw_source = $p
        break
    }
}
if (-not $dsh_sw_source) {
    Write-Step "DSH_SW source not found. Please place it next to this script or on Desktop." "WARN"
} else {
    Write-Step "Found DSH_SW at: $dsh_sw_source" "OK"
}

# Step 3: Install Agent Preset
Write-Title "Step 3: Installing Agent Preset"
$sourcePreset = Join-Path $SCRIPT_DIR "engineering"
if (-not (Test-Path $sourcePreset)) {
    Write-Step "Preset source not found: $sourcePreset" "ERROR"
    exit 1
}

if (Test-Path $PRESET_DIR) {
    Write-Step "Removing existing preset..." "SKIP"
    Remove-Item -Recurse -Force $PRESET_DIR
}
New-Item -ItemType Directory -Path $PRESET_DIR -Force | Out-Null
Copy-Item -Recurse -Path "$sourcePreset\*" -Destination $PRESET_DIR
Write-Step "Preset installed to $PRESET_DIR" "OK"

# Step 4: Copy DSH_SW tools
Write-Title "Step 4: Installing DSH_SW Tools"
if ($dsh_sw_source) {
    # Create tools directory
    New-Item -ItemType Directory -Path $TOOLS_DIR -Force | Out-Null
    
    # Copy main files
    $files_to_copy = @("sw_bridge.py", "swapi.py", "solidworks-modeling.md")
    foreach ($f in $files_to_copy) {
        $src = Join-Path $dsh_sw_source $f
        $dst = Join-Path $TOOLS_DIR $f
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-Step "Copied $f" "OK"
        } else {
            Write-Step "$f not found in source" "WARN"
        }
    }
    
    # Copy examples
    $examples_src = Join-Path $dsh_sw_source "examples"
    $examples_dst = Join-Path $TOOLS_DIR "examples"
    if (Test-Path $examples_src) {
        Copy-Item -Recurse $examples_src $examples_dst -Force
        Write-Step "Copied examples/" "OK"
    }
} else {
    Write-Step "Skipping DSH_SW tools (source not found)" "SKIP"
}

# Step 5: Install Skills
Write-Title "Step 5: Installing Skills"
$sourceSkills = Join-Path $sourcePreset "skills"
if (Test-Path $sourceSkills) {
    New-Item -ItemType Directory -Path $SKILLS_DIR -Force | Out-Null
    Get-ChildItem -Path $sourceSkills -Directory | ForEach-Object {
        $skillName = $_.Name
        $targetSkillDir = Join-Path $SKILLS_DIR $skillName
        if (Test-Path $targetSkillDir) {
            Write-Step "Skill '$skillName' already exists - skipping" "SKIP"
        } else {
            Copy-Item -Recurse $_.FullName $targetSkillDir
            Write-Step "Skill '$skillName' installed" "OK"
        }
    }
}

# Step 6: Verify
Write-Title "Step 6: Verification"
$allOk = $true
$checks = @(
    @{ Name = "agent.cordis.yml"; Path = "$PRESET_DIR\agent.cordis.yml" },
    @{ Name = "preset.yml"; Path = "$PRESET_DIR\preset.yml" },
    @{ Name = "skills/cad-workflow"; Path = "$PRESET_DIR\skills\cad-workflow" },
    @{ Name = "skills/sw-design"; Path = "$PRESET_DIR\skills\sw-design" },
    @{ Name = "skills/solidworks-bridge"; Path = "$PRESET_DIR\skills\solidworks-bridge" }
)

foreach ($check in $checks) {
    if (Test-Path $check.Path) {
        Write- "  [OK] $($check.Name)" "OK"
    } else {
        Write- "  [ERROR] $($check.Name) MISSING" "ERROR"
        $allOk = $false
    }
}

# Check DSH_SW files
if ($dsh_sw_source) {
    if (Test-Path "$TOOLS_DIR\sw_bridge.py" -and Test-Path "$TOOLS_DIR\swapi.py") {
        Write- "  [OK] tools/sw_bridge.py + swapi.py" "OK"
    } else {
        Write- "  [ERROR] DSH_SW tools missing" "ERROR"
        $allOk = $false
    }
}

# Step 7: Python Dependencies
Write-Title "Step 7: Python Dependencies Check"
$python_ok = $true
try {
    $py_ver = & python --version 2>&1
    Write- "Python: $py_ver" "OK"
} catch {
    Write- "Python not found - please install Python 3.8+" "ERROR"
    $python_ok = $false
}

if ($python_ok) {
    $deps = @("win32com", "mss", "PIL")
    $missing = @()
    foreach ($dep in $deps) {
        $test_result = python -c "import $dep" 2>&1
        if ($LASTEXITCODE -ne 0) {
            $missing += $dep
        }
    }
    if ($missing.Count -eq 0) {
        Write- "All Python deps installed" "OK"
    } else {
        Write- "Missing: $($missing -join ', ') - run: pip install pywin32 mss Pillow" "WARN"
    }
}

# Step 6: Install QQ Notification Plugin
Write-Title "Step 6: Installing QQ Notification Plugin"
$pluginSrc = Join-Path $SCRIPT_DIR "engineering" "plugins" "dsh-qq-notification"
if (Test-Path $pluginSrc) {
    # Find the web profile node_modules directory
    $webProfile = Join-Path $DSH_HOME "profiles" "web" "node_modules"
    if (-not (Test-Path $webProfile)) {
        $webProfile = Join-Path $DSH_HOME "profiles" "default" "node_modules"
    }
    if (Test-Path $webProfile) {
        $pluginDst = Join-Path $webProfile "dsh-qq-notification"
        New-Item -ItemType Directory -Force -Path $pluginDst | Out-Null
        Copy-Item -Path "$pluginSrc\*" -Destination $pluginDst -Recurse -Force
        Write-Step "QQ notification plugin installed to node_modules" "OK"
    } else {
        Write-Step "Web profile node_modules not found, plugin source available at: $pluginSrc" "SKIP"
    }
} else {
    Write-Step "QQ notification plugin source not found at: $pluginSrc" "SKIP"
}

# Final
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan

if ($allOk) {
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Restart DeepSeek Harness (refresh the Web UI)" -ForegroundColor White
    Write-Host "  2. Start a new session and select 'Engineering Mode'" -ForegroundColor White
    Write-Host "  3. Try: '画一个长11mm的正方形' - AI will drive SolidWorks!" -ForegroundColor White
    Write-Host ""
    Write-Host "Preset location: $PRESET_DIR" -ForegroundColor Gray
    if ($dsh_sw_source) {
        Write-Host "DSH_SW tools: $TOOLS_DIR" -ForegroundColor Gray
    }
} else {
    Write-Host "Installation completed with warnings." -ForegroundColor Yellow
}