"""
SAG-2404: Unit tests for the plugin install sandbox.

Tests cover:
  - denylist_check.precheck() — clean + hit paths
  - plugin_sandbox.run_install() — golden path via a temp marketplace fixture
  - denylist pre-check integration (hit aborts install)
  - reset idempotency (workspace can be deleted + recreated)
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add sandbox root to path so imports resolve without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from denylist_check import precheck as denylist_precheck  # noqa: E402
from plugin_sandbox import run_install  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_denylist(tmp_path: Path) -> Path:
    """A minimal denylist with one HARD-BLOCK entry."""
    content = """\
# Test denylist
## HARD-BLOCK
| Repo / Vendor | License | Reason |
|---|---|---|
| `evil-org/bad-plugin` | MIT | Test block |
| `ruvnet/*` | MIT | Supply chain |
## REJECT
(none)
"""
    f = tmp_path / "tool-denylist.md"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.fixture()
def anthropics_denylist(tmp_path: Path) -> Path:
    """A denylist with the production block list but no anthropics entries."""
    real_path = Path(
        "/home/gus-pinsoneault/.paperclip/instances/default/companies/"
        "1dc911ed-ff05-4072-b2ae-a3e3177e3873/config/tool-denylist.md"
    )
    if real_path.exists():
        return real_path
    # Fallback: empty denylist
    f = tmp_path / "tool-denylist.md"
    f.write_text("# denylist\n## HARD-BLOCK\n(none)\n", encoding="utf-8")
    return f


@pytest.fixture()
def tmp_marketplace(tmp_path: Path) -> Path:
    """A minimal local marketplace with two plugins: clean-plugin and blocked-plugin."""
    mkt = tmp_path / "plugins"

    # clean-plugin — Anthropic-authored
    clean = mkt / "clean-plugin"
    (clean / ".claude-plugin").mkdir(parents=True)
    (clean / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({
            "name": "clean-plugin",
            "version": "1.0.0",
            "description": "Test plugin",
            "author": {"name": "Anthropic", "email": "support@anthropic.com"},
        }),
        encoding="utf-8",
    )
    (clean / "agents").mkdir()
    (clean / "agents" / "clean-plugin.md").write_text(
        "# Clean Plugin Agent\nDoes nothing.\n", encoding="utf-8"
    )
    (clean / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")

    # blocked-plugin — evil-org authored (matches the tmp_denylist fixture)
    evil = mkt / "evil-plugin"
    (evil / ".claude-plugin").mkdir(parents=True)
    (evil / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({
            "name": "bad-plugin",
            "version": "0.1.0",
            "description": "Malicious plugin",
            "author": {"name": "evil-org", "email": "evil@evil-org.example"},
        }),
        encoding="utf-8",
    )
    (evil / "LICENSE").write_text("MIT\n", encoding="utf-8")

    return mkt


# ---------------------------------------------------------------------------
# denylist_check tests
# ---------------------------------------------------------------------------

class TestDenylistPrecheck:
    def test_clean_on_anthropic_manifest(self, anthropics_denylist: Path) -> None:
        manifest = {
            "name": "code-simplifier",
            "version": "1.0.0",
            "author": {"name": "Anthropic", "email": "support@anthropic.com"},
        }
        result = denylist_precheck(manifest, anthropics_denylist, "anthropics")
        assert result["result"] == "clean"
        assert result["hits"] == []
        assert result["framework_ref"] == "SAG-683 §1 + §1.5"
        assert "anthropics/code-simplifier" in result["checked_identifiers"]

    def test_hit_on_blocked_repo(self, tmp_denylist: Path) -> None:
        manifest = {
            "name": "bad-plugin",
            "version": "0.1.0",
            "author": {"name": "evil-org", "email": "evil@evil-org.example"},
        }
        result = denylist_precheck(manifest, tmp_denylist, "evil-org")
        assert result["result"] == "hit"
        assert len(result["hits"]) > 0

    def test_wildcard_block(self, tmp_denylist: Path) -> None:
        manifest = {
            "name": "some-plugin",
            "version": "1.0.0",
            "author": {"name": "ruvnet", "email": "ruvnet@example.com"},
        }
        result = denylist_precheck(manifest, tmp_denylist, "ruvnet")
        assert result["result"] == "hit"

    def test_clean_when_denylist_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.md"
        manifest = {"name": "whatever", "author": {"name": "X"}}
        result = denylist_precheck(manifest, missing, "x")
        assert result["result"] == "clean"

    def test_framework_ref_always_present(self, tmp_denylist: Path) -> None:
        manifest = {"name": "x", "author": {"name": "Y"}}
        result = denylist_precheck(manifest, tmp_denylist, "y")
        assert result["framework_ref"] == "SAG-683 §1 + §1.5"


# ---------------------------------------------------------------------------
# plugin_sandbox.run_install tests
# ---------------------------------------------------------------------------

class TestRunInstall:
    def test_clean_install_succeeds(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "ws"
        audit_dir = tmp_path / "audit"

        result = run_install(
            plugin_slug="clean-plugin",
            marketplace_root=tmp_marketplace,
            workspace=workspace,
            audit_dir=audit_dir,
            denylist_path=tmp_denylist,
        )

        assert result["post_install_check"]["ok"] is True
        assert result["denylist_precheck"]["result"] == "clean"
        assert result["manifest"]["name"] == "clean-plugin"
        assert len(result["files_written"]) > 0
        # Audit file written
        audit_files = list(audit_dir.glob("*-clean-plugin.json"))
        assert len(audit_files) == 1

    def test_all_five_signals_present(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        result = run_install(
            plugin_slug="clean-plugin",
            marketplace_root=tmp_marketplace,
            workspace=tmp_path / "ws",
            audit_dir=tmp_path / "audit",
            denylist_path=tmp_denylist,
        )
        for signal in ("manifest", "files_written", "network_egress",
                       "denylist_precheck", "post_install_check"):
            assert signal in result, f"Signal '{signal}' missing from audit"

    def test_files_written_have_required_fields(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        result = run_install(
            plugin_slug="clean-plugin",
            marketplace_root=tmp_marketplace,
            workspace=tmp_path / "ws",
            audit_dir=tmp_path / "audit",
            denylist_path=tmp_denylist,
        )
        for entry in result["files_written"]:
            assert "path" in entry
            assert "sha256" in entry
            assert "size_bytes" in entry

    def test_denylist_hit_blocks_install(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(RuntimeError, match="DENYLIST HIT"):
            run_install(
                plugin_slug="evil-plugin",
                marketplace_root=tmp_marketplace,
                workspace=tmp_path / "ws",
                audit_dir=tmp_path / "audit",
                denylist_path=tmp_denylist,
            )

    def test_blocked_install_writes_no_audit_file(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        audit_dir = tmp_path / "audit"
        try:
            run_install(
                plugin_slug="evil-plugin",
                marketplace_root=tmp_marketplace,
                workspace=tmp_path / "ws",
                audit_dir=audit_dir,
                denylist_path=tmp_denylist,
            )
        except RuntimeError:
            pass
        # No audit file should exist for the blocked plugin
        assert not list(audit_dir.glob("*.json"))

    def test_idempotent_reinstall(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        """Two installs of the same plugin produce two separate audit files."""
        workspace = tmp_path / "ws"
        audit_dir = tmp_path / "audit"
        run_install("clean-plugin", tmp_marketplace, workspace, audit_dir, tmp_denylist)
        # Simulate reset: delete workspace
        import shutil
        shutil.rmtree(workspace)
        run_install("clean-plugin", tmp_marketplace, workspace, audit_dir, tmp_denylist)
        audit_files = list(audit_dir.glob("*-clean-plugin.json"))
        assert len(audit_files) == 2, "Expected two distinct audit files after two runs"

    def test_unknown_plugin_raises(
        self,
        tmp_marketplace: Path,
        tmp_denylist: Path,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            run_install(
                plugin_slug="nonexistent-plugin",
                marketplace_root=tmp_marketplace,
                workspace=tmp_path / "ws",
                audit_dir=tmp_path / "audit",
                denylist_path=tmp_denylist,
            )
