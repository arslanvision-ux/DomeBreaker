#!/bin/bash
# =============================================================================
# DomeBreaker - Automated Linux Installer
# =============================================================================

set -e

echo "================================================================="
echo "   ⚡ DomeBreaker - Solaris USD Suite Installer (Linux)"
echo "================================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[INFO] Installation Directory: ${SCRIPT_DIR}"
echo ""

# 1. Try python3 in PATH
if command -v python3 &> /dev/null; then
    echo "[INFO] Found python3 in PATH. Running installer..."
    python3 "${SCRIPT_DIR}/scripts/install.py" "$@"
    exit 0
fi

# 2. Try hython in PATH or standard SideFX installation directories
if command -v hython &> /dev/null; then
    echo "[INFO] Found hython in PATH. Running installer..."
    hython "${SCRIPT_DIR}/scripts/install.py" "$@"
    exit 0
fi

for hdir in /opt/hfs*/bin/hython /opt/sidefx/houdini*/bin/hython; do
    if [ -x "$hdir" ]; then
        echo "[INFO] Found Houdini python at: ${hdir}"
        "$hdir" "${SCRIPT_DIR}/scripts/install.py" "$@"
        exit 0
    fi
done

# 3. Fallback: Pure Bash package installation
echo "[INFO] Python not found. Running pure bash installation..."

# Check if already installed
FOUND_EXISTING=0
for hpref in "$HOME"/houdini* "$HOME"/.houdini*; do
    if [ -f "${hpref}/packages/domebreaker.json" ]; then
        FOUND_EXISTING=1
        break
    fi
done

if [ "$FOUND_EXISTING" -eq 1 ]; then
    echo "[INFO] Existing DomeBreaker installation detected."
    echo "[1] Reinstall / Update (default)"
    echo "[2] Uninstall DomeBreaker"
    echo "[3] Cancel"
    read -r -p "Enter choice [1/2/3] (default: 1): " USER_CHOICE
    if [ "$USER_CHOICE" = "2" ]; then
        UNINSTALLED=0
        for hpref in "$HOME"/houdini* "$HOME"/.houdini*; do
            if [ -f "${hpref}/packages/domebreaker.json" ]; then
                rm -f "${hpref}/packages/domebreaker.json"
                echo "[SUCCESS] Removed: ${hpref}/packages/domebreaker.json"
                UNINSTALLED=$((UNINSTALLED + 1))
            fi
        done
        echo "================================================================="
        echo "🎉 DomeBreaker uninstalled from ${UNINSTALLED} Houdini version(s)."
        echo "================================================================="
        exit 0
    elif [ "$USER_CHOICE" = "3" ]; then
        echo "Operation cancelled."
        exit 0
    fi
fi

COUNT=0
for hpref in "$HOME"/houdini* "$HOME"/.houdini*; do
    if [ -d "$hpref" ]; then
        PKG_DIR="${hpref}/packages"
        mkdir -p "${PKG_DIR}"
        
        cat <<EOF > "${PKG_DIR}/domebreaker.json"
{
    "env": [
        {
            "DOMEBREAKER_ROOT": "${SCRIPT_DIR}"
        },
        {
            "HDRI_MATCH_SOLARIS_ROOT": "\$DOMEBREAKER_ROOT"
        },
        {
            "PYTHONPATH": {
                "value": [
                    "\$DOMEBREAKER_ROOT/python"
                ],
                "method": "append"
            }
        }
    ],
    "path": [
        "\$DOMEBREAKER_ROOT/houdini"
    ],
    "houdini_version": ">= 19.5",
    "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
}
EOF
        echo "[SUCCESS] Registered: ${PKG_DIR}/domebreaker.json"
        COUNT=$((COUNT + 1))
    fi
done

echo ""
echo "================================================================="
if [ $COUNT -gt 0 ]; then
    echo "🎉 DomeBreaker successfully installed into ${COUNT} Houdini version(s)!"
    echo "================================================================="
    echo "Next Steps:"
    echo "1. Launch Houdini."
    echo "2. Open the 'DomeBreaker' shelf or Windows -> Python Panel -> DomeBreaker."
else
    echo "[WARNING] No Houdini preference directories found in ${HOME}."
    echo "Please ensure Houdini has been run at least once."
fi
