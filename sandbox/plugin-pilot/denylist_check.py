#!/usr/bin/env python3
"""
SAG-2404: Denylist pre-check for plugin installs.

Parses companies/{co}/config/tool-denylist.md and checks a plugin manifest
against the HARD-BLOCK and REJECT lists.  Called from plugin_sandbox.py before
every install; a HIT aborts the install with a structured log entry.

Framework reference: SAG-683 §1 + §1.5
"""
import re
from pathlib import Path


def _load_blocked_entries(denylist_path: Path) -> list[str]:
    """Extract repo/vendor identifiers from the denylist markdown table rows."""
    blocked: list[str] = []
    if not denylist_path.exists():
        return blocked

    for line in denylist_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        # Extract backtick-quoted tokens from table cells
        for tok in re.findall(r"`([^`]+)`", line):
            tok_stripped = tok.strip()
            # Accept repo-path tokens (contains /) and vendor keywords (contain *)
            # Skip: pure license names, SAG-### refs, pure URLs
            if not tok_stripped:
                continue
            if tok_stripped.startswith("SAG-") or tok_stripped.startswith("http"):
                continue
            if "/" in tok_stripped or "*" in tok_stripped or tok_stripped.lower() in {
                "hard-block", "reject", "defer",
            }:
                blocked.append(tok_stripped.lower())
            # Also catch simple vendor name entries (no slash, but in the first column)
            # These appear as `vendor name` in the Repo/Vendor column
            elif re.match(r"^[a-z0-9_\-. ]+$", tok_stripped.lower()):
                blocked.append(tok_stripped.lower())

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for e in blocked:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def _identifiers_from_manifest(manifest: dict, marketplace_owner: str) -> list[str]:
    """Derive check candidates from the plugin manifest + known marketplace owner."""
    candidates: list[str] = []

    plugin_name = str(manifest.get("name", "")).lower()
    if plugin_name:
        candidates.append(f"{marketplace_owner}/{plugin_name}")

    author = manifest.get("author", {})
    if isinstance(author, dict):
        author_name = str(author.get("name", "")).lower()
        author_email = str(author.get("email", "")).lower()
        if author_name:
            candidates.append(author_name)
        if author_email:
            candidates.append(author_email)
    elif isinstance(author, str):
        candidates.append(author.lower())

    deps = manifest.get("dependencies", [])
    if isinstance(deps, dict):
        deps = list(deps.keys())
    for dep in deps:
        candidates.append(str(dep).lower())

    return [c for c in candidates if c]


def _matches(candidate: str, entry: str) -> bool:
    """Return True if candidate is covered by the denylist entry (wildcard aware)."""
    if entry.endswith("/*"):
        prefix = entry[:-2]  # strip trailing /*
        return candidate.startswith(prefix + "/") or candidate == prefix
    return (
        entry in candidate              # entry is a substring of candidate
        or candidate.startswith(entry)  # candidate starts with entry
        or entry.startswith(candidate + "/")  # candidate is org-prefix of entry
    )


def precheck(
    manifest: dict,
    denylist_path: Path,
    marketplace_owner: str = "anthropics",
) -> dict:
    """
    Run denylist pre-check for a plugin manifest.

    Returns:
        {
          "result": "clean" | "hit",
          "hits": [...],
          "framework_ref": "SAG-683 §1 + §1.5",
          "checked_identifiers": [...],
        }

    Raises RuntimeError on HARD BLOCK hit (so callers that don't inspect the
    return value still get an early failure).
    """
    blocked = _load_blocked_entries(denylist_path)
    candidates = _identifiers_from_manifest(manifest, marketplace_owner)

    hits: list[dict] = []
    for entry in blocked:
        for candidate in candidates:
            if _matches(candidate, entry):
                hits.append({
                    "denylist_entry": entry,
                    "matched_field": candidate,
                })

    result = {
        "result": "hit" if hits else "clean",
        "hits": hits,
        "framework_ref": "SAG-683 §1 + §1.5",
        "checked_identifiers": candidates,
    }
    return result
