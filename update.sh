#!/bin/bash
# =============================================================================
# DomeBreaker - Command-Line Auto-Updater (Linux)
# =============================================================================

set -e

echo "================================================================="
echo "   [+] DomeBreaker - Command-Line Auto-Updater (Linux)"
echo "================================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 &> /dev/null; then
    python3 "${SCRIPT_DIR}/scripts/update.py"
    exit 0
fi

if command -v hython &> /dev/null; then
    hython "${SCRIPT_DIR}/scripts/update.py"
    exit 0
fi

for hdir in /opt/hfs*/bin/hython /opt/sidefx/houdini*/bin/hython; do
    if [ -x "$hdir" ]; then
        "$hdir" "${SCRIPT_DIR}/scripts/update.py"
        exit 0
    fi
done

if command -v git &> /dev/null && [ -d "${SCRIPT_DIR}/.git" ]; then
    echo "[INFO] Running git pull directly..."
    cd "${SCRIPT_DIR}"
    git pull origin main
    exit 0
fi

echo "[ERROR] Python or Git not found to perform update."
exit 1