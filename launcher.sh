#!/usr/bin/env bash
# =============================================================================
# Mistral Intelligence Monitor  (c) 2026 BalTac  |  v3.0.0  |  MIT License
# =============================================================================
# launcher.sh -- bash / WSL / Linux / macOS wrapper
#
# Usage:
#   ./launcher.sh                        # interactive menu
#   ./launcher.sh --stats --window 7d    # one-liner passthrough
#   ./launcher.sh --models               # model catalog
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_PY="${SCRIPT_DIR}/mistral_monitor/monitor.py"

# Prefer project .venv, then uv-tool graphify, then system python
PYTHON=""
for candidate in \
    "${SCRIPT_DIR}/.venv/Scripts/python.exe" \
    "${SCRIPT_DIR}/.venv/bin/python" \
    "${APPDATA}/uv/tools/graphifyy/Scripts/python.exe" \
    "${USERPROFILE:-$HOME}/AppData/Roaming/uv/tools/graphifyy/Scripts/python.exe" \
    "$HOME/.local/share/uv/tools/graphifyy/venv/bin/python" \
    python3 \
    python; do
    if [ -x "$candidate" ] 2>/dev/null || command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found." >&2
    exit 1
fi

# Ensure rich is available (for interactive menu)
"$PYTHON" -c "import rich" 2>/dev/null || {
    echo "Installing rich (needed for interactive menu)..."
    "$PYTHON" -m pip install rich -q 2>/dev/null || true
}

# If args given → passthrough to monitor.py
if [ $# -gt 0 ]; then
    exec "$PYTHON" "$MONITOR_PY" "$@"
fi

# No args → interactive launcher
exec "$PYTHON" "$SCRIPT_DIR/launcher.py"
