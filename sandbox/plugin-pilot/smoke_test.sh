#!/usr/bin/env bash
# Two-run smoke test for the plugin-pilot sandbox + audit pipeline.
#
# Run 1: Install code-review@claude-plugins-official, validate audit record.
# Reset:  Wipe sandbox state.
# Run 2:  Re-install, confirm fresh audit record created, pipeline is idempotent.
#
# Exits 0 on full pass, non-zero on any failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PLUGIN="code-review@claude-plugins-official"
PASS=0
FAIL=0

log() { echo "[smoke] $*"; }
ok()  { echo "[PASS] $*"; PASS=$((PASS+1)); }
fail(){ echo "[FAIL] $*"; FAIL=$((FAIL+1)); }

# ------------------------------------------------------------------ Run 1
log "=== RUN 1: install $PLUGIN ==="
python3 audit_install.py "$PLUGIN"
INSTALL_EXIT=$?

if [ $INSTALL_EXIT -eq 0 ]; then
    ok "Install exited 0"
else
    fail "Install exited $INSTALL_EXIT"
fi

# Verify audit file was created.
latest=$(ls -t audits/*.json 2>/dev/null | head -1)
if [ -n "$latest" ]; then
    ok "Audit file created: $latest"
else
    fail "No audit file found in audits/"
fi

# Validate key fields in audit JSON.
if [ -n "$latest" ]; then
    passed=$(python3 -c "import json,sys; d=json.load(open('$latest')); print(d['denylist_precheck']['passed'])")
    listed=$(python3 -c "import json,sys; d=json.load(open('$latest')); print(d['post_install_check']['plugin_listed'])")
    violated=$(python3 -c "import json,sys; d=json.load(open('$latest')); print(d['sandbox_violated'])")

    [ "$passed" = "True" ]   && ok "denylist_precheck.passed=True" || fail "denylist_precheck.passed=$passed"
    [ "$listed" = "True" ]   && ok "post_install_check.plugin_listed=True" || fail "post_install_check.plugin_listed=$listed"
    [ "$violated" = "False" ] && ok "sandbox_violated=False" || fail "sandbox_violated=$violated"

    manifest=$(python3 -c "import json,sys; d=json.load(open('$latest')); print(d['manifest'] is not None)")
    [ "$manifest" = "True" ] && ok "manifest present" || fail "manifest missing"

    file_count=$(python3 -c "import json,sys; d=json.load(open('$latest')); print(len(d['files_written']))")
    [ "$file_count" -gt 0 ] && ok "files_written count=$file_count" || fail "files_written is empty"
fi

# ------------------------------------------------------------------ Reset
log ""
log "=== RESET ==="
bash reset.sh
if [ ! -d "state" ]; then
    ok "state/ directory removed"
else
    fail "state/ directory still present after reset"
fi

# ------------------------------------------------------------------ Run 2
log ""
log "=== RUN 2: re-install $PLUGIN (idempotency check) ==="
python3 audit_install.py "$PLUGIN"
INSTALL2_EXIT=$?

if [ $INSTALL2_EXIT -eq 0 ]; then
    ok "Run 2 install exited 0"
else
    fail "Run 2 install exited $INSTALL2_EXIT"
fi

count=$(ls audits/*.json 2>/dev/null | wc -l)
if [ "$count" -ge 2 ]; then
    ok "Two distinct audit records in audits/"
else
    fail "Expected >=2 audit records, found $count"
fi

# ------------------------------------------------------------------ Summary
log ""
log "=== Smoke test complete: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ]
