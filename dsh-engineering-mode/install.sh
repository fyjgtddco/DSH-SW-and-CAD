#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Engineering Mode + DSH_SW Installer for DeepSeek Harness
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Configuration
PRESET_ID="engineering"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PRESET_DIR="$DSH_HOME/.agent-presets/$PRESET_ID"
SKILLS_DIR="$DSH_HOME/skills"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_DIR="$PRESET_DIR/tools"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $1"; }
skip()  { echo -e "${YELLOW}[SKIP]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  DSH Engineering Mode Installer${NC}"
echo -e "${CYAN}  + DSH_SW SolidWorks Bridge${NC}"
echo -e "${CYAN}========================================${NC}"

# Step 1: Check DSH
echo -e "\n${CYAN}--- Step 1: Checking DSH ---${NC}"
if [ ! -d "$DSH_HOME" ]; then
    err "DSH home not found at $DSH_HOME"
    exit 1
fi
ok "DSH home: $DSH_HOME"

# Step 2: Find DSH_SW
echo -e "\n${CYAN}--- Step 2: Finding DSH_SW Source ---${NC}"
dsh_sw_source=""
for p in "$SCRIPT_DIR/../DSH_SW-main" "$SCRIPT_DIR/../DSH_SW" "$HOME/Desktop/DSH_SW-main" "$HOME/Desktop/DSH_SW"; do
    if [ -d "$p" ]; then
        dsh_sw_source="$p"
        break
    fi
done
if [ -z "$dsh_sw_source" ]; then
    skip "DSH_SW source not found. Place it next to this script or on Desktop."
else
    ok "Found DSH_SW at: $dsh_sw_source"
fi

# Step 3: Install Preset
echo -e "\n${CYAN}--- Step 3: Installing Agent Preset ---${NC}"
source_preset="$SCRIPT_DIR/engineering"
if [ ! -d "$source_preset" ]; then
    err "Preset source not found: $source_preset"
    exit 1
fi
[ -d "$PRESET_DIR" ] && { skip "Removing existing preset..."; rm -rf "$PRESET_DIR"; }
mkdir -p "$PRESET_DIR"
cp -r "$source_preset"/* "$PRESET_DIR/"
ok "Preset installed to $PRESET_DIR"

# Step 4: Copy DSH_SW tools
echo -e "\n${CYAN}--- Step 4: Installing DSH_SW Tools ---${NC}"
if [ -n "$dsh_sw_source" ]; then
    mkdir -p "$TOOLS_DIR"
    for f in sw_bridge.py swapi.py solidworks-modeling.md; do
        if [ -f "$dsh_sw_source/$f" ]; then
            cp "$dsh_sw_source/$f" "$TOOLS_DIR/"
            ok "Copied $f"
        fi
    done
    if [ -d "$dsh_sw_source/examples" ]; then
        cp -r "$dsh_sw_source/examples" "$TOOLS_DIR/"
        ok "Copied examples/"
    fi
else
    skip "Skipping DSH_SW tools (source not found)"
fi

# Step 5: Install Skills
echo -e "\n${CYAN}--- Step 5: Installing Skills ---${NC}"
source_skills="$source_preset/skills"
if [ -d "$source_skills" ]; then
    mkdir -p "$SKILLS_DIR"
    for skill_dir in "$source_skills"/*/; do
        name="$(basename "$skill_dir")"
        target="$SKILLS_DIR/$name"
        if [ -d "$target" ]; then
            skip "Skill '$name' already exists - skipping"
        else
            cp -r "$skill_dir" "$target"
            ok "Skill '$name' installed"
        fi
    done
fi

# Step 6: Verify
echo -e "\n${CYAN}--- Step 6: Verification ---${NC}"
all_ok=true
for check in "agent.cordis.yml" "preset.yml"; do
    if [ -f "$PRESET_DIR/$check" ]; then
        ok "  $check"
    else
        err "  $check MISSING"
        all_ok=false
    fi
done

if [ -n "$dsh_sw_source" ] && [ -f "$TOOLS_DIR/sw_bridge.py" ] && [ -f "$TOOLS_DIR/swapi.py" ]; then
    ok "  tools/sw_bridge.py + swapi.py"
else
    err "  DSH_SW tools missing"
    all_ok=false
fi

# Step 7: Python deps
echo -e "\n${CYAN}--- Step 7: Python Dependencies ---${NC}"
if command -v python &>/dev/null; then
    py_ver=$(python --version 2>&1)
    ok "Python: $py_ver"
    
    missing=""
    for dep in win32com mss PIL; do
        python -c "import $dep" 2>/dev/null || missing="$missing $dep"
    done
    if [ -z "$missing" ]; then
        ok "All Python deps installed"
    else
        skip "Missing:$missing - run: pip install pywin32 mss Pillow"
    fi
else
    err "Python not found - please install Python 3.8+"
fi

# Final
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  INSTALLATION COMPLETE${NC}"
echo -e "${GREEN}========================================${NC}"

if [ "$all_ok" = true ]; then
    echo -e "\n${YELLOW}Next steps:${NC}"
    echo "  1. Restart DeepSeek Harness (refresh the Web UI)"
    echo "  2. Start a new session and select 'Engineering Mode'"
    echo "  3. Try: '画一个长11mm的正方形' - AI will drive SolidWorks!"
    echo ""
    echo -e "${GRAY}Preset: $PRESET_DIR${NC}"
    [ -n "$dsh_sw_source" ] && echo -e "${GRAY}DSH_SW tools: $TOOLS_DIR${NC}"
else
    echo -e "${YELLOW}Installation completed with warnings.${NC}"
fi