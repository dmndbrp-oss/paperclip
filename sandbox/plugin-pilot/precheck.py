"""
Denylist precheck for plugin install sandbox.

Parses config/tool-denylist.md and checks whether a plugin vendor/marketplace
would match any HARD-BLOCK or REJECT entry before an install is attempted.

SAG-683 §1 trust-tier gate: Tier 1 (anthropics/*) passes by default.
All other vendors checked against the denylist before any install proceeds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


DENYLIST_PATH = Path(__file__).parents[2] / "companies" / "1dc911ed-ff05-4072-b2ae-a3e3177e3873" / "config" / "tool-denylist.md"

# Anthropic-first-party sources are Tier 1 — exempt from denylist check.
TIER1_SOURCES = frozenset({"anthropics", "anthropic"})

# Regex to pull the Repo/Vendor cell from a markdown table row.
# Matches: | `repo/vendor` | ... | or | repo/vendor | ... |
_TABLE_ROW_RE = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|", re.MULTILINE)

# Section header markers so we can label which list a match came from.
_HARD_BLOCK_HEADER = re.compile(r"^##\s+HARD-BLOCK", re.MULTILINE | re.IGNORECASE)
_REJECT_HEADER = re.compile(r"^##\s+REJECT", re.MULTILINE | re.IGNORECASE)
_OVERRIDES_HEADER = re.compile(r"^##\s+Overrides", re.MULTILINE | re.IGNORECASE)


@dataclass
class DenylistEntry:
    token: str
    category: str  # "HARD-BLOCK" | "REJECT"


@dataclass
class PrecheckResult:
    passed: bool
    tier1_exempt: bool = False
    matched_entry: DenylistEntry | None = None
    note: str = ""
    checked_tokens: list[str] = field(default_factory=list)


def _load_denylist(path: Path = DENYLIST_PATH) -> list[DenylistEntry]:
    text = path.read_text()

    hb_m = _HARD_BLOCK_HEADER.search(text)
    rej_m = _REJECT_HEADER.search(text)
    ov_m = _OVERRIDES_HEADER.search(text)

    entries: list[DenylistEntry] = []

    def _extract(section_text: str, category: str) -> None:
        for m in _TABLE_ROW_RE.finditer(section_text):
            token = m.group(1).strip().lower()
            if token and not token.startswith("repo") and not token.startswith("---"):
                entries.append(DenylistEntry(token=token, category=category))

    if hb_m and rej_m:
        entries += []  # populated below
        hb_text = text[hb_m.end(): rej_m.start()]
        _extract(hb_text, "HARD-BLOCK")

    if rej_m:
        ov_end = ov_m.start() if ov_m else len(text)
        rej_text = text[rej_m.end(): ov_end]
        _extract(rej_text, "REJECT")

    return entries


def _tokens_from_plugin_spec(plugin: str, marketplace: str | None) -> list[str]:
    """
    Build a set of tokens to check against denylist entries.

    Examples:
      plugin="code-review", marketplace="claude-plugins-official"
        → ["code-review", "claude-plugins-official", "anthropics/claude-plugins-official"]
      plugin="some-tool", marketplace=None
        → ["some-tool"]
    """
    tokens = [plugin.lower()]
    if marketplace:
        tokens.append(marketplace.lower())
        # marketplace names often map to <owner>/<repo> combos
        if marketplace == "claude-plugins-official":
            tokens.append("anthropics/claude-plugins-official")
        elif "/" in marketplace:
            tokens.append(marketplace.lower())
    return tokens


def _vendor_from_marketplace(marketplace: str | None) -> str | None:
    if not marketplace:
        return None
    if marketplace.startswith("claude-plugins-official"):
        return "anthropics"
    if "/" in marketplace:
        return marketplace.split("/")[0].lower()
    return None


def check(plugin: str, marketplace: str | None = None, denylist_path: Path = DENYLIST_PATH) -> PrecheckResult:
    """
    Run the denylist precheck.

    Args:
        plugin: plugin slug, e.g. "code-review"
        marketplace: marketplace name or owner/repo, e.g. "claude-plugins-official"
        denylist_path: override for testing

    Returns:
        PrecheckResult with passed=True if safe to install.
    """
    vendor = _vendor_from_marketplace(marketplace)

    # Tier 1 fast-path: anthropics/* is exempt from denylist check (§1 trust framework).
    if vendor in TIER1_SOURCES:
        return PrecheckResult(
            passed=True,
            tier1_exempt=True,
            note=f"Tier 1 exempt: vendor '{vendor}' is Anthropic first-party (§1 trust framework).",
            checked_tokens=[],
        )

    tokens = _tokens_from_plugin_spec(plugin, marketplace)
    entries = _load_denylist(denylist_path)

    for token in tokens:
        for entry in entries:
            # Substring match: denylist entry "ruvnet/*" catches "ruvnet/ruflo"
            entry_token = entry.token
            if "*" in entry_token:
                namespace = entry_token.rstrip("/*").rstrip("/")
                if token.startswith(namespace):
                    return PrecheckResult(
                        passed=False,
                        matched_entry=entry,
                        note=f"Token '{token}' matches {entry.category} namespace '{entry_token}'.",
                        checked_tokens=tokens,
                    )
            elif entry_token in token or token in entry_token:
                return PrecheckResult(
                    passed=False,
                    matched_entry=entry,
                    note=f"Token '{token}' matches {entry.category} entry '{entry_token}'.",
                    checked_tokens=tokens,
                )

    return PrecheckResult(
        passed=True,
        note="No denylist match found.",
        checked_tokens=tokens,
    )


if __name__ == "__main__":
    import json
    import sys

    plugin = sys.argv[1] if len(sys.argv) > 1 else "code-review"
    marketplace = sys.argv[2] if len(sys.argv) > 2 else "claude-plugins-official"

    result = check(plugin, marketplace)
    print(json.dumps({
        "passed": result.passed,
        "tier1_exempt": result.tier1_exempt,
        "matched_entry": {
            "token": result.matched_entry.token,
            "category": result.matched_entry.category,
        } if result.matched_entry else None,
        "note": result.note,
        "checked_tokens": result.checked_tokens,
    }, indent=2))
