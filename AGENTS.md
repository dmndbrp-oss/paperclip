# Agent Coding Guidelines

## Git hygiene (mandatory)

**Never use `git add -A` or `git add .` when committing to `main` or any shared branch.**

Stage only the files that are in scope for the current issue. Broad-glob staging sweeps in working-directory noise (*.db, log files, run-metrics JSONL, eval results, pilot artifacts) and produces omnibus commits that require expensive remediation.

Pattern: `git add <explicit-file-or-dir>` per commit.

See [SAG-5142](/SAG/issues/SAG-5142) for the incident that produced this rule (fe3e259 swept 165 non-canary files onto main).

### Files that are never committed

The following types are in `.gitignore` and must never be staged:

- `*.db` / `enrichment/*.db` — live SQLite databases
- `/SAG-*-status.txt`, `/*-bg-state.json`, `/payload.json` — ephemeral heartbeat state
- `infra/runtime-eval/results/` — generated nightly eval results
- `infra/bench/**/*.log` — benchmark logs
- `pilot-artifacts/phase-a/` — historical pilot run artifacts
- `failover-monitor/tick*-findings.md`, `failover-monitor/heartbeat-ledger.jsonl` — Monitor routine outputs
