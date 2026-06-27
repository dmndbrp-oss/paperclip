#!/usr/bin/env bash
# Phase A corrective run script — SAG-3657
# Runs dispatcher from enrichment/ directory with proper env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

export PHASE_A_METRICS_DIR="$SCRIPT_DIR"
export ENRICHMENT_DISPATCHER_CONCURRENCY=1

# Load enrichment env (set -a exports all vars)
set -a
source "$PROJECT_DIR/enrichment/.env"
set +a

# Override DATABASE_URL with known-good local URL
export DATABASE_URL="postgresql://paperclip:paperclip@localhost:54329/enrichment_db"
export LITELLM_BASE_URL="http://localhost:4000"

LOG="$SCRIPT_DIR/run-corrective.log"
echo "Phase A corrective run start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
echo "PRIMARY_MODEL=gemma4-26b-a4b-it-q4_K_M BATCH=100 CONCURRENCY=1" | tee -a "$LOG"

cd "$PROJECT_DIR/enrichment"
python3 dispatcher.py --batch-size 100 2>&1 | tee -a "$LOG"
EXIT_CODE=$?

echo "Phase A corrective run end: $(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$EXIT_CODE" | tee -a "$LOG"
