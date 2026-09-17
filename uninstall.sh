#!/bin/bash
# =============================================================================
# DomeBreaker - Automated Linux Uninstaller
# =============================================================================

set -e

echo "================================================================="
echo "   🗑️  DomeBreaker - Solaris USD Suite Uninstaller (Linux)"
echo "================================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Try python3 in PATH
if command -v python3 &> /dev/null; then
    python3 "${SCRIPT_DIR}/scripts/install.py" --uninstall
    exit 0
fi

# 2. Try hython in PATH or standard SideFX installation directories
if command -v hython &> /dev/null; then
    hython "${SCRIPT_DIR}/scripts/install.py" --uninstall
    exit 0
fi

for hdir in /opt/hfs*/bin/hython /opt/sidefx/houdini*/bin/hython; do
    if [ -x "$hdir" ]; then
        "$hdir" "${SCRIPT_DIR}/scripts/install.py" --uninstall
        exit 0
    fi
done

# 3. Fallback: Pure Bash package uninstaller
echo "[INFO] Running pure bash uninstallation..."
REMOVED=0

for hpref in "$HOME"/houdini* "$HOME"/.houdini*; do
    if [ -f "${hpref}/packages/domebreaker.json" ]; then
        rm -f "${hpref}/packages/domebreaker.json"
        echo "[SUCCESS] Removed: ${hpref}/packages/domebreaker.json"
        REMOVED=$((REMOVED + 1))
    fi
    if [ -f "${hpref}/packages/hdri_match_solaris.json" ]; then
        rm -f "${hpref}/packages/hdri_match_solaris.json"
        echo "[SUCCESS] Removed legacy: ${hpref}/packages/hdri_match_solaris.json"
        REMOVED=$((REMOVED + 1))
    fi
done

echo ""
echo "================================================================="
if [ $REMOVED -gt 0 ]; then
    echo "🎉 DomeBreaker uninstalled from ${REMOVED} location(s)."
else
    echo "[INFO] No DomeBreaker package files found to remove."
fi
echo "================================================================="
