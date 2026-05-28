"""Tests for sandbox/plugin-pilot/precheck.py."""
import sys
from pathlib import Path
import pytest

# Add parent to path so we can import precheck directly.
sys.path.insert(0, str(Path(__file__).parents[1]))
from precheck import check, DenylistEntry


class TestTier1Exempt:
    def test_anthropics_plugins_official_passes(self):
        r = check("code-review", "claude-plugins-official")
        assert r.passed
        assert r.tier1_exempt
        assert "Tier 1" in r.note

    def test_anthropics_knowledge_work_passes(self):
        r = check("finance", "anthropics/knowledge-work-plugins")
        assert r.passed
        assert r.tier1_exempt

    def test_anthropic_vendor_spelling(self):
        r = check("some-plugin", "anthropics/some-repo")
        assert r.passed
        assert r.tier1_exempt


class TestDenylistBlocks:
    def test_ruvnet_ruflo_blocked(self, denylist_path):
        r = check("ruflo", "ruvnet/ruflo", denylist_path=denylist_path)
        assert not r.passed
        assert r.matched_entry is not None
        assert r.matched_entry.category == "HARD-BLOCK"

    def test_ruvnet_namespace_blocked(self, denylist_path):
        # The ruvnet/* namespace entry should catch any ruvnet repo.
        r = check("some-tool", "ruvnet/any-repo", denylist_path=denylist_path)
        assert not r.passed

    def test_cloakbrowser_blocked(self, denylist_path):
        r = check("cloakhq/cloakbrowser", None, denylist_path=denylist_path)
        assert not r.passed
        assert r.matched_entry.category == "HARD-BLOCK"

    def test_rejected_license_blocked(self, denylist_path):
        r = check("cmux", "manaflow-ai/cmux", denylist_path=denylist_path)
        assert not r.passed
        assert r.matched_entry.category == "REJECT"


class TestCleanPasses:
    def test_unknown_community_tool_passes_no_match(self, denylist_path):
        # A hypothetical tool with no denylist match should pass.
        r = check("my-tool", "somerepo/my-tool", denylist_path=denylist_path)
        assert r.passed
        assert not r.tier1_exempt
        assert "No denylist match" in r.note

    def test_checked_tokens_populated(self, denylist_path):
        r = check("my-tool", "somerepo/my-tool", denylist_path=denylist_path)
        assert "my-tool" in r.checked_tokens


@pytest.fixture
def denylist_path():
    """Use the real production denylist for integration-level checks."""
    p = (
        Path(__file__).parents[3]
        / "companies"
        / "1dc911ed-ff05-4072-b2ae-a3e3177e3873"
        / "config"
        / "tool-denylist.md"
    )
    if not p.exists():
        pytest.skip(f"Denylist not found at {p}")
    return p
