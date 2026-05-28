#!/usr/bin/env bash
# Reset the plugin-pilot sandbox state.
# Wipes state/home/ (the isolated install target) so the next smoke run starts clean.
# Does NOT touch audits/ — audit records are preserved.
# Does NOT touch ~/.claude/ (production) — verified below.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$SCRIPT_DIR/state"
PROD_CLAUDE="$HOME/.claude"

echo "=== plugin-pilot reset ==="

# Wipe sandbox state.
if [ -d "$STATE_DIR" ]; then
    echo "Removing: $STATE_DIR"
    rm -rf "$STATE_DIR"
    echo "State cleared."
else
    echo "State directory not found -- already clean."
fi

# Verify production HOME was not touched: check if any files in
# ~/.claude/plugins/ are newer than this script file.
if [ -d "$PROD_CLAUDE/plugins" ]; then
    recent=$(find "$PROD_CLAUDE/plugins" -newer "$SCRIPT_DIR/reset.sh" -name "*.json" 2>/dev/null | head -5)
    if [ -n "$recent" ]; then
        echo "WARNING: production ~/.claude/plugins/ has files newer than this script:"
        echo "$recent"
        echo "Investigate whether the sandbox HOME isolation held."
    else
        echo "Production ~/.claude/plugins/ unmodified -- sandbox boundary confirmed."
    fi
fi

echo "Reset complete. Run 'python audit_install.py code-review@claude-plugins-official' for a fresh smoke run."
