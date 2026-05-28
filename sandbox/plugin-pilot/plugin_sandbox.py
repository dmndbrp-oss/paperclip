#!/usr/bin/env python3
"""
SAG-2404 MVP plugin-install sandbox + audit logger.

Usage:
    python3 plugin_sandbox.py <plugin-slug> [options]

Example:
    python3 plugin_sandbox.py code-simplifier

The script:
  1. Loads the plugin manifest from the local marketplace cache.
  2. Runs a denylist pre-check (SAG-683 §1 + §1.5).  Fails-fast on HIT.
  3. Snapshots the workspace filesystem (before).
  4. Notes current TCP connections (before).
  5. Installs the plugin into sandbox/plugin-pilot/workspace/.claude/plugins/.
  6. Notes current TCP connections (after).
  7. Snapshots the workspace filesystem (after).
  8. Runs a post-install smoke check.
  9. Writes a 5-signal audit JSON to sandbox/plugin-pilot/audit/<ts>-<slug>.json.

Nothing in this script touches the production ~/.claude/ catalog.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from denylist_check import precheck as denylist_precheck

# ---------------------------------------------------------------------------
# Default paths (all overridable via CLI flags or env vars)
# ---------------------------------------------------------------------------

_COMPANY_ID = os.environ.get("PAPERCLIP_COMPANY_ID", "1dc911ed-ff05-4072-b2ae-a3e3177e3873")

DENYLIST_PATH = Path(
    os.environ.get(
        "DENYLIST_PATH",
        f"/home/gus-pinsoneault/.paperclip/instances/default/companies/"
        f"{_COMPANY_ID}/config/tool-denylist.md",
    )
)

MARKETPLACE_ROOT = Path(
    os.environ.get(
        "MARKETPLACE_ROOT",
        str(Path.home() / ".claude/plugins/marketplaces/claude-plugins-official/plugins"),
    )
)

SANDBOX_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = SANDBOX_ROOT / "workspace"
AUDIT_DIR = SANDBOX_ROOT / "audit"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_dir(base: Path) -> dict[str, tuple[str, int]]:
    """Return {relative_path: (sha256, size_bytes)} for all files under base."""
    result: dict[str, tuple[str, int]] = {}
    if not base.exists():
        return result
    for p in base.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(base))
            result[rel] = (_sha256_file(p), p.stat().st_size)
    return result


def _diff_snapshots(
    before: dict[str, tuple[str, int]],
    after: dict[str, tuple[str, int]],
    base: Path,
) -> list[dict]:
    """Return signal records for files added or changed between snapshots."""
    written: list[dict] = []
    for rel, (sha, size) in after.items():
        if rel not in before or before[rel][0] != sha:
            written.append({
                "path": str(base / rel),
                "sha256": sha,
                "size_bytes": size,
            })
    return written


# ---------------------------------------------------------------------------
# Network egress helpers (process-level, /proc/net/tcp)
# ---------------------------------------------------------------------------

def _read_tcp_connections() -> set[tuple[str, int]]:
    """Return set of (ip, port) for currently ESTABLISHED outbound TCP connections."""
    conns: set[tuple[str, int]] = set()
    try:
        data = Path("/proc/net/tcp").read_text(encoding="ascii", errors="replace")
        for line in data.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            state = parts[3]
            if state != "01":  # 01 = ESTABLISHED
                continue
            remote_hex = parts[2]
            ip_hex, port_hex = remote_hex.split(":")
            # /proc/net/tcp stores addresses in little-endian hex
            ip_bytes = bytes.fromhex(ip_hex)
            ip = ".".join(str(b) for b in reversed(ip_bytes))
            port = int(port_hex, 16)
            conns.add((ip, port))
    except Exception:
        pass
    return conns


def _connections_to_egress(
    before: set[tuple[str, int]],
    after: set[tuple[str, int]],
) -> list[dict]:
    """Convert new TCP connections to audit egress records."""
    result: list[dict] = []
    for ip, port in (after - before):
        try:
            host = socket.gethostbyaddr(ip)[0]
        except Exception:
            host = ip
        result.append({
            "host": f"{host}:{port}",
            "bytes_out": None,  # process-level capture; byte counts not available
            "bytes_in": None,
        })
    return result


# ---------------------------------------------------------------------------
# Plugin install
# ---------------------------------------------------------------------------

def _load_manifest(plugin_dir: Path) -> dict:
    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No .claude-plugin/plugin.json in {plugin_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _install_plugin(plugin_dir: Path, workspace: Path) -> None:
    """Copy plugin source tree into workspace/.claude/plugins/<slug>/."""
    dest = workspace / ".claude" / "plugins" / plugin_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(plugin_dir, dest)


def _post_install_check(plugin_slug: str, workspace: Path) -> dict:
    """Verify the plugin files landed correctly in the workspace."""
    dest = workspace / ".claude" / "plugins" / plugin_slug
    files_present = list(dest.rglob("*")) if dest.exists() else []
    file_count = sum(1 for f in files_present if f.is_file())
    ok = dest.exists() and file_count > 0
    notes = (
        f"Found {file_count} file(s) under {dest}" if ok
        else f"No files found at expected path {dest}"
    )
    return {"ok": ok, "notes": notes}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_install(
    plugin_slug: str,
    marketplace_root: Path,
    workspace: Path,
    audit_dir: Path,
    denylist_path: Path,
) -> dict:
    """
    Execute the full sandbox install pipeline and return the audit record.

    Raises RuntimeError if the denylist pre-check fails.
    """
    plugin_dir = marketplace_root / plugin_slug
    if not plugin_dir.exists():
        raise ValueError(f"Plugin '{plugin_slug}' not found in {marketplace_root}")

    # 1. Load manifest
    manifest = _load_manifest(plugin_dir)

    # 2. Denylist pre-check — fail-fast on any hit
    denylist_result = denylist_precheck(
        manifest=manifest,
        denylist_path=denylist_path,
        marketplace_owner="anthropics",
    )
    if denylist_result["result"] == "hit":
        raise RuntimeError(
            f"DENYLIST HIT — install aborted for '{plugin_slug}': "
            f"{denylist_result['hits']}"
        )

    # 3. Filesystem snapshot (before)
    snap_before = _snapshot_dir(workspace)

    # 4. TCP snapshot (before)
    tcp_before = _read_tcp_connections()

    # 5. Install plugin
    workspace.mkdir(parents=True, exist_ok=True)
    _install_plugin(plugin_dir, workspace)

    # 6. TCP snapshot (after)
    tcp_after = _read_tcp_connections()

    # 7. Filesystem snapshot (after)
    snap_after = _snapshot_dir(workspace)

    # 8. Diff
    files_written = _diff_snapshots(snap_before, snap_after, workspace)

    # 9. Network egress
    network_egress = _connections_to_egress(tcp_before, tcp_after)

    # 10. Post-install check
    post_check = _post_install_check(plugin_slug, workspace)

    # 11. Build 5-signal audit record
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S") + f"-{now.microsecond // 1000:03d}Z"
    audit: dict = {
        "schema_version": "1.0",
        "timestamp": now.isoformat(),
        "plugin_slug": plugin_slug,
        "marketplace": "anthropics/claude-plugins-official",
        "manifest": manifest,
        "files_written": files_written,
        "network_egress": network_egress,
        "denylist_precheck": denylist_result,
        "post_install_check": post_check,
    }

    # 12. Write audit file
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / f"{ts}-{plugin_slug}.json"
    audit_file.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[sandbox] Audit log written: {audit_file}", file=sys.stderr)

    return audit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAG-2404 plugin install sandbox — installs a plugin from the "
                    "claude-plugins-official marketplace into an isolated workspace "
                    "and captures a 5-signal audit log.",
    )
    parser.add_argument("plugin_slug", help="Plugin slug (e.g. code-simplifier)")
    parser.add_argument(
        "--marketplace",
        default=str(MARKETPLACE_ROOT),
        help="Path to marketplace plugins root",
    )
    parser.add_argument(
        "--workspace",
        default=str(WORKSPACE_DIR),
        help="Isolated workspace dir (must NOT overlap production ~/.claude/)",
    )
    parser.add_argument(
        "--audit-dir",
        default=str(AUDIT_DIR),
        help="Directory for audit JSON output",
    )
    parser.add_argument(
        "--denylist",
        default=str(DENYLIST_PATH),
        help="Path to tool-denylist.md",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full audit JSON to stdout",
    )
    args = parser.parse_args()

    try:
        result = run_install(
            plugin_slug=args.plugin_slug,
            marketplace_root=Path(args.marketplace),
            workspace=Path(args.workspace),
            audit_dir=Path(args.audit_dir),
            denylist_path=Path(args.denylist),
        )
    except RuntimeError as exc:
        print(f"[sandbox] BLOCKED: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    sys.exit(0 if result["post_install_check"]["ok"] else 1)


if __name__ == "__main__":
    main()
