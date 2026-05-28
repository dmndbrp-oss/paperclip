# Plugin-Pilot Sandbox

**SAG-2404** — MVP sandbox for `anthropics/claude-plugins-official` installs.

Shared by [SAG-2401](/SAG/issues/SAG-2401) (registry connectivity + knowledge-work pilot)
and [SAG-2402](/SAG/issues/SAG-2402) (R3 attestation pilot).

## Sandbox layout

```
sandbox/plugin-pilot/
├── plugin_sandbox.py   # main installer + audit pipeline
├── denylist_check.py   # SAG-683 §1 + §1.5 denylist pre-check
├── reset.sh            # reset workspace between runs
├── tests/
│   └── test_sandbox.py # unit tests
├── workspace/          # ISOLATED install target (≠ production ~/.claude/)
│   └── .claude/
│       └── plugins/    # plugins land here, never in the real catalog
└── audit/              # 5-signal audit JSON files written here
    └── <ISO>-<slug>.json
```

The `workspace/` directory is **separate from** `~/.claude/` and from the
production skill catalog.  Nothing in this sandbox modifies the user's active
Claude Code configuration.

## Prerequisites

- Python 3.10+
- `anthropics/claude-plugins-official` marketplace already synced under
  `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/`
- Tool denylist at
  `companies/$PAPERCLIP_COMPANY_ID/config/tool-denylist.md`

## Activation — install a plugin into the sandbox

```bash
cd sandbox/plugin-pilot

# Install the smoke-test plugin (code-simplifier, the smallest available):
python3 plugin_sandbox.py code-simplifier

# See full audit JSON on stdout as well:
python3 plugin_sandbox.py code-simplifier --json

# Override paths if needed:
python3 plugin_sandbox.py code-simplifier \
  --marketplace ~/.claude/plugins/marketplaces/claude-plugins-official/plugins \
  --workspace   ./workspace \
  --audit-dir   ./audit \
  --denylist    /path/to/config/tool-denylist.md
```

Exit codes:
- `0` — install succeeded, audit written, post-install check passed
- `1` — install succeeded but post-install check failed (inspect audit JSON)
- `2` — denylist HIT; install was BLOCKED (inspect stderr)

## Reset procedure

Run between sandbox trials to start with a clean workspace.
Audit logs are preserved by default so both runs remain readable.

```bash
# Reset workspace, keep audit logs (recommended between sequential runs):
bash reset.sh

# Reset workspace AND clear audit logs:
bash reset.sh --clear-audit
```

Production safety: `reset.sh` removes only `sandbox/plugin-pilot/workspace/`.
It never touches `~/.claude/`, any production catalog, or any Paperclip
platform directory.

## Running both smoke runs (acceptance criterion)

```bash
cd sandbox/plugin-pilot

# Run 1
python3 plugin_sandbox.py code-simplifier --json

# Reset (preserving run 1 audit log)
bash reset.sh

# Run 2
python3 plugin_sandbox.py code-simplifier --json

# Verify two audit files exist, both with denylist_precheck.result == "clean"
ls audit/
```

## Denylist pre-check

The pre-check parses `config/tool-denylist.md` and matches the plugin's:
- Manifest `author` name + email
- Derived repo identifier (`anthropics/<plugin-name>`)
- Any declared `dependencies`

For `anthropics/claude-plugins-official` plugins, the expected result is
`"clean"` (Anthropic is a §1 Tier-1 trusted maintainer per SAG-683 §1).

A HIT prints to stderr and exits with code 2; the audit file is **not** written.

## Audit log format

Each install writes `audit/<ISO-timestamp>-<slug>.json` with five signals:

| Signal | Field | Description |
|---|---|---|
| 1 | `manifest` | Raw plugin.json contents from the registry |
| 2 | `files_written` | `[{path, sha256, size_bytes}]` — filesystem diff |
| 3 | `network_egress` | `[{host, bytes_out, bytes_in}]` — new TCP connections during install |
| 4 | `denylist_precheck` | `{result, hits, framework_ref, checked_identifiers}` |
| 5 | `post_install_check` | `{ok, notes}` — presence check of installed files |

## Trust framework

Governed by SAG-683 §1 + §1.5 (`AGENT_OPERATIONS_GUIDE.md`).
Denylist source: `companies/1dc911ed-ff05-4072-b2ae-a3e3177e3873/config/tool-denylist.md`.
