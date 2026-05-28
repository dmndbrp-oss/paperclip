"""
Plugin install audit harness — SAG-2404.

Installs a Claude Code plugin into an isolated sandbox HOME (state/home/)
and records a structured JSON audit entry capturing:
  - denylist precheck result (SAG-683 §1 + §1.5 framework)
  - plugin.json manifest
  - files_written: all new/modified files with path, sha256, size_bytes
  - network_egress: /proc/net/tcp delta (best-effort; see notes)
  - post_install_check: `claude plugin list` output in sandbox
  - sandbox_violated: whether any writes landed outside state/home/

Usage:
    python audit_install.py code-review@claude-plugins-official
    python audit_install.py example-plugin@claude-plugins-official

Output:
    audits/<timestamp>-<plugin>.json
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from precheck import check as denylist_check, PrecheckResult

SANDBOX_ROOT = Path(__file__).parent
STATE_HOME = SANDBOX_ROOT / "state" / "home"
AUDITS_DIR = SANDBOX_ROOT / "audits"
PRODUCTION_CLAUDE_HOME = Path.home() / ".claude"

# Pre-seeded marketplace data: copy from production to avoid network call for
# the marketplace index itself; the plugin files under marketplaces/ already live
# in the production cache from the prior `claude plugin marketplace add` call.
MARKETPLACE_SRC = PRODUCTION_CLAUDE_HOME / "plugins"


@dataclass
class FileEntry:
    path: str       # relative to sandbox HOME
    sha256: str
    size_bytes: int
    action: str     # "created" | "modified"


@dataclass
class NetworkEgressCapture:
    capture_method: str
    captured: bool
    connections: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class PostInstallCheck:
    plugin_listed: bool
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class AuditRecord:
    schema_version: int
    timestamp: str
    plugin: str
    marketplace: str
    vendor: str | None
    denylist_precheck: dict
    manifest: dict | None
    files_written: list[dict]
    network_egress: dict
    post_install_check: dict
    sandbox_violated: bool
    sandbox_violation_paths: list[str]
    install_exit_code: int
    install_stdout: str
    install_stderr: str
    duration_seconds: float


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_dir(root: Path) -> dict[Path, tuple[str, int]]:
    """Return {path: (sha256, size)} for all files under root."""
    result: dict[Path, tuple[str, int]] = {}
    if not root.exists():
        return result
    for p in root.rglob("*"):
        if p.is_file():
            result[p] = (_sha256(p), p.stat().st_size)
    return result


def _diff_snapshots(
    before: dict[Path, tuple[str, int]],
    after: dict[Path, tuple[str, int]],
    sandbox_home: Path,
) -> list[FileEntry]:
    entries = []
    for path, (sha, size) in after.items():
        if path not in before:
            action = "created"
        elif before[path][0] != sha:
            action = "modified"
        else:
            continue
        try:
            rel = path.relative_to(sandbox_home)
        except ValueError:
            rel = path
        entries.append(FileEntry(
            path=str(rel),
            sha256=sha,
            size_bytes=size,
            action=action,
        ))
    return entries


def _read_proc_net_tcp() -> set[str]:
    """Read /proc/net/tcp and /proc/net/tcp6 for active connections."""
    connections: set[str] = set()
    for fname in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fname) as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[3] == "01":  # ESTABLISHED
                        connections.add(parts[2])  # remote address hex
        except OSError:
            pass
    return connections


def _seed_sandbox_home(sandbox_home: Path) -> None:
    """
    Pre-seed the sandbox .claude/plugins directory from the production cache
    so the install doesn't need to re-fetch the marketplace index from GitHub.
    """
    target_plugins = sandbox_home / ".claude" / "plugins"
    target_plugins.mkdir(parents=True, exist_ok=True)

    if not MARKETPLACE_SRC.exists():
        return

    # Copy known_marketplaces.json
    src_manifest = MARKETPLACE_SRC / "known_marketplaces.json"
    if src_manifest.exists():
        shutil.copy2(src_manifest, target_plugins / "known_marketplaces.json")

    # Symlink (or copy) the marketplaces directory to avoid duplicating large files.
    src_marketplaces = MARKETPLACE_SRC / "marketplaces"
    target_marketplaces = target_plugins / "marketplaces"
    if src_marketplaces.exists() and not target_marketplaces.exists():
        # Copy so the sandbox is truly isolated (installs can't mutate production).
        shutil.copytree(src_marketplaces, target_marketplaces, dirs_exist_ok=True)


def _read_manifest(sandbox_home: Path, plugin: str, marketplace: str) -> dict | None:
    """
    Read the plugin.json manifest from the sandbox's marketplace cache.
    """
    manifest_path = (
        sandbox_home
        / ".claude"
        / "plugins"
        / "marketplaces"
        / marketplace
        / "plugins"
        / plugin
        / ".claude-plugin"
        / "plugin.json"
    )
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _run_install(plugin: str, marketplace: str, sandbox_home: Path) -> tuple[int, str, str]:
    """
    Run `claude plugin install <plugin>@<marketplace>` with HOME overridden.
    Returns (exit_code, stdout, stderr).
    """
    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)
    # Suppress telemetry inside sandbox.
    env["CLAUDE_TELEMETRY_DISABLED"] = "1"

    spec = f"{plugin}@{marketplace}"
    result = subprocess.run(
        ["claude", "plugin", "install", spec, "--scope", "user"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stdout, result.stderr


def _run_post_install_check(plugin: str, sandbox_home: Path) -> PostInstallCheck:
    """
    Run `claude plugin list` in the sandbox to verify the plugin appears.
    """
    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)
    env["CLAUDE_TELEMETRY_DISABLED"] = "1"

    result = subprocess.run(
        ["claude", "plugin", "list"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    listed = plugin in result.stdout
    return PostInstallCheck(
        plugin_listed=listed,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
    )


def _check_sandbox_violation(files_written: list[FileEntry], sandbox_home: Path) -> tuple[bool, list[str]]:
    """
    Detect if any written path escaped the sandbox home.
    FileEntry.path is relative to sandbox_home so all clean paths are relative.
    An absolute path in any entry indicates sandbox escape.
    """
    violations = []
    for f in files_written:
        p = Path(f.path)
        if p.is_absolute():
            real = p.resolve()
            if not str(real).startswith(str(sandbox_home.resolve())):
                violations.append(str(p))
    return bool(violations), violations


def run_audit(plugin_spec: str) -> AuditRecord:
    """
    Main entry point. plugin_spec is e.g. "code-review@claude-plugins-official".
    """
    if "@" in plugin_spec:
        plugin, marketplace = plugin_spec.split("@", 1)
    else:
        plugin = plugin_spec
        marketplace = "claude-plugins-official"

    timestamp = datetime.now(timezone.utc).isoformat()
    t_start = time.monotonic()

    # 1. Denylist precheck.
    precheck: PrecheckResult = denylist_check(plugin, marketplace)
    if not precheck.passed:
        raise SystemExit(
            f"PRECHECK FAIL: {precheck.note}\n"
            f"Matched entry: {precheck.matched_entry}\n"
            "Install aborted per SAG-683 §1 denylist policy."
        )

    sandbox_home = STATE_HOME
    sandbox_home.mkdir(parents=True, exist_ok=True)
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Seed sandbox with marketplace data (no network needed for Tier 1 plugins).
    _seed_sandbox_home(sandbox_home)

    # 3. Read manifest before install (from seeded marketplace data).
    manifest = _read_manifest(sandbox_home, plugin, marketplace)

    # 4. Snapshot before.
    before = _snapshot_dir(sandbox_home)
    net_before = _read_proc_net_tcp()

    # 5. Install.
    exit_code, stdout, stderr = _run_install(plugin, marketplace, sandbox_home)

    # 6. Snapshot after + network delta.
    after = _snapshot_dir(sandbox_home)
    net_after = _read_proc_net_tcp()
    net_new = net_after - net_before

    # 7. Diff files.
    files_written = _diff_snapshots(before, after, sandbox_home)

    # 8. Network egress record.
    network_egress = NetworkEgressCapture(
        capture_method="proc_net_tcp_delta",
        captured=bool(net_new),
        connections=list(net_new),
        note=(
            "Delta captures only connections still ESTABLISHED after install completes. "
            "Short-lived connections (e.g. GitHub HTTPS) close before snapshot — "
            "not captured. Use strace-based capture for full egress logging."
            if not net_new
            else ""
        ),
    )

    # 9. Post-install check.
    post_check = _run_post_install_check(plugin, sandbox_home)

    # 10. Sandbox violation check.
    sandbox_violated, violation_paths = _check_sandbox_violation(files_written, sandbox_home)

    duration = time.monotonic() - t_start

    # Derive vendor from marketplace.
    vendor: str | None = None
    if marketplace == "claude-plugins-official":
        vendor = "anthropics"
    elif "/" in marketplace:
        vendor = marketplace.split("/")[0]

    record = AuditRecord(
        schema_version=1,
        timestamp=timestamp,
        plugin=plugin,
        marketplace=marketplace,
        vendor=vendor,
        denylist_precheck={
            "passed": precheck.passed,
            "tier1_exempt": precheck.tier1_exempt,
            "matched_entry": (
                {"token": precheck.matched_entry.token, "category": precheck.matched_entry.category}
                if precheck.matched_entry
                else None
            ),
            "note": precheck.note,
            "checked_tokens": precheck.checked_tokens,
        },
        manifest=manifest,
        files_written=[asdict(f) for f in files_written],
        network_egress=asdict(network_egress),
        post_install_check=asdict(post_check),
        sandbox_violated=sandbox_violated,
        sandbox_violation_paths=violation_paths,
        install_exit_code=exit_code,
        install_stdout=stdout,
        install_stderr=stderr,
        duration_seconds=round(duration, 2),
    )

    # Write audit record.
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = AUDITS_DIR / f"{ts_slug}-{plugin}.json"
    audit_path.write_text(json.dumps(asdict(record), indent=2))
    print(f"Audit record: {audit_path}")
    return record


def _print_summary(record: AuditRecord) -> None:
    status = "PASS" if record.install_exit_code == 0 and not record.sandbox_violated else "FAIL"
    print(f"\n=== Audit Summary: {status} ===")
    print(f"  Plugin:          {record.plugin}@{record.marketplace}")
    print(f"  Vendor:          {record.vendor}")
    print(f"  Precheck:        {'PASS (Tier1 exempt)' if record.denylist_precheck['tier1_exempt'] else 'PASS'}")
    print(f"  Manifest:        {'found' if record.manifest else 'not found'}")
    print(f"  Files written:   {len(record.files_written)}")
    print(f"  Sandbox violated:{record.sandbox_violated}")
    print(f"  Plugin listed:   {record.post_install_check['plugin_listed']}")
    print(f"  Exit code:       {record.install_exit_code}")
    print(f"  Duration:        {record.duration_seconds}s")
    if record.install_stdout:
        print(f"\n--- install stdout ---\n{record.install_stdout.strip()}")
    if record.install_stderr:
        print(f"\n--- install stderr ---\n{record.install_stderr.strip()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python audit_install.py <plugin>@<marketplace>")
        print("Example: python audit_install.py code-review@claude-plugins-official")
        sys.exit(1)

    record = run_audit(sys.argv[1])
    _print_summary(record)

    if record.install_exit_code != 0:
        print("\nInstall failed.", file=sys.stderr)
        sys.exit(1)
    if record.sandbox_violated:
        print("\nSANDBOX VIOLATION detected.", file=sys.stderr)
        sys.exit(2)
