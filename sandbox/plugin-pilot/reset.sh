#!/usr/bin/env bash
# Reset the plugin-pilot sandbox.
#
# Removes workspace/ (the isolated install target) so the next smoke run
# starts with a clean slate.  Audit logs in audit/ are preserved by default.
#
# Usage:
#   bash reset.sh              # clear workspace only
#   bash reset.sh --clear-audit  # clear workspace + audit logs
#
# Production safety: only sandbox/plugin-pilot/workspace/ is removed.
# This script never touches ~/.claude/ or any Paperclip platform directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$SCRIPT_DIR/workspace"
AUDIT_DIR="$SCRIPT_DIR/audit"
PROD_CLAUDE="$HOME/.claude"
CLEAR_AUDIT=0

for arg in "$@"; do
    [ "$arg" = "--clear-audit" ] && CLEAR_AUDIT=1
done

echo "=== plugin-pilot reset ==="

# Remove workspace.
if [ -d "$WORKSPACE_DIR" ]; then
    echo "Removing: $WORKSPACE_DIR"
    rm -rf "$WORKSPACE_DIR"
    echo "Workspace cleared."
else
    echo "Workspace not found -- already clean."
fi

# Optionally clear audit logs.
if [ "$CLEAR_AUDIT" -eq 1 ]; then
    if [ -d "$AUDIT_DIR" ]; then
        echo "Removing: $AUDIT_DIR"
        rm -rf "$AUDIT_DIR"
        echo "Audit logs cleared."
    fi
fi

# Production safety check: warn if ~/.claude/plugins/ has files newer than
# the workspace (which was just removed). Use the script itself as the
# comparison anchor since the workspace is gone.
if [ -d "$PROD_CLAUDE/plugins" ]; then
    recent=$(find "$PROD_CLAUDE/plugins" -newer "$SCRIPT_DIR/reset.sh" -name "*.json" 2>/dev/null | head -5)
    if [ -n "$recent" ]; then
        echo "WARNING: production ~/.claude/plugins/ has files newer than this script:"
        echo "$recent"
        echo "Investigate whether sandbox HOME isolation held."
    else
        echo "Production ~/.claude/plugins/ unmodified -- sandbox boundary confirmed."
    fi
fi

echo "Reset complete. Run 'python3 plugin_sandbox.py code-simplifier' for a fresh smoke run."
