"""
Tests for audit_install.py.

The install tests are integration-level (they call audit_install.run_audit)
but run against the real sandbox with HOME isolation.  Fixtures clean up state/.
"""
import json
import shutil
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import audit_install as ai


SANDBOX_ROOT = Path(__file__).parents[1]
STATE_HOME = SANDBOX_ROOT / "state" / "home"
AUDITS_DIR = SANDBOX_ROOT / "audits"


@pytest.fixture(autouse=True)
def clean_sandbox():
    """Wipe sandbox state before and after each test."""
    if STATE_HOME.exists():
        shutil.rmtree(STATE_HOME)
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if STATE_HOME.exists():
        shutil.rmtree(STATE_HOME)


class TestSandboxSeeding:
    def test_seed_creates_known_marketplaces(self):
        STATE_HOME.mkdir(parents=True, exist_ok=True)
        ai._seed_sandbox_home(STATE_HOME)
        manifest = STATE_HOME / ".claude" / "plugins" / "known_marketplaces.json"
        assert manifest.exists(), "known_marketplaces.json not seeded"

    def test_seed_creates_marketplace_dir(self):
        STATE_HOME.mkdir(parents=True, exist_ok=True)
        ai._seed_sandbox_home(STATE_HOME)
        marketplace_dir = STATE_HOME / ".claude" / "plugins" / "marketplaces"
        assert marketplace_dir.exists(), "marketplaces/ directory not seeded"


class TestSnapshotDiff:
    def test_empty_before_returns_all_created(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        before: dict = {}
        after = ai._snapshot_dir(tmp_path)
        diffs = ai._diff_snapshots(before, after, tmp_path)
        assert len(diffs) == 1
        assert diffs[0].action == "created"
        assert diffs[0].path == "a.txt"

    def test_unchanged_file_not_reported(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        snapshot = ai._snapshot_dir(tmp_path)
        diffs = ai._diff_snapshots(snapshot, snapshot, tmp_path)
        assert diffs == []

    def test_modified_file_detected(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("v1")
        before = ai._snapshot_dir(tmp_path)
        f.write_text("v2")
        after = ai._snapshot_dir(tmp_path)
        diffs = ai._diff_snapshots(before, after, tmp_path)
        assert len(diffs) == 1
        assert diffs[0].action == "modified"

    def test_sha256_matches_file_content(self, tmp_path):
        import hashlib
        content = b"test content"
        f = tmp_path / "b.bin"
        f.write_bytes(content)
        before: dict = {}
        after = ai._snapshot_dir(tmp_path)
        diffs = ai._diff_snapshots(before, after, tmp_path)
        expected = hashlib.sha256(content).hexdigest()
        assert diffs[0].sha256 == expected


class TestSandboxViolation:
    def test_relative_path_no_violation(self, tmp_path):
        from audit_install import FileEntry
        entries = [FileEntry(path=".claude/plugins/foo.json", sha256="abc", size_bytes=10, action="created")]
        violated, paths = ai._check_sandbox_violation(entries, tmp_path)
        assert not violated
        assert paths == []

    def test_absolute_path_outside_sandbox_is_violation(self, tmp_path):
        from audit_install import FileEntry
        entries = [FileEntry(path="/etc/passwd", sha256="abc", size_bytes=10, action="created")]
        violated, paths = ai._check_sandbox_violation(entries, tmp_path)
        assert violated
        assert "/etc/passwd" in paths


class TestAuditRecordSchema:
    """Validate the JSON schema of a live audit record (integration)."""

    @pytest.mark.integration
    def test_audit_record_schema(self):
        record = ai.run_audit("code-review@claude-plugins-official")
        assert record.schema_version == 1
        assert record.plugin == "code-review"
        assert record.marketplace == "claude-plugins-official"
        assert record.vendor == "anthropics"
        assert record.denylist_precheck["passed"] is True
        assert record.denylist_precheck["tier1_exempt"] is True
        assert isinstance(record.files_written, list)
        assert record.manifest is not None
        assert record.manifest.get("name") == "code-review"
        # Sandbox should not be violated.
        assert record.sandbox_violated is False
        # Plugin should appear in list.
        assert record.post_install_check["plugin_listed"] is True
        # Audit file should be written.
        audits = list(AUDITS_DIR.glob("*.json"))
        assert len(audits) >= 1, "No audit file created"

    @pytest.mark.integration
    def test_audit_json_parseable(self):
        ai.run_audit("code-review@claude-plugins-official")
        latest = sorted(AUDITS_DIR.glob("*.json"))[-1]
        data = json.loads(latest.read_text())
        assert data["schema_version"] == 1
        assert "files_written" in data
        assert "network_egress" in data
        assert "post_install_check" in data

    @pytest.mark.integration
    def test_precheck_blocks_denylisted_plugin(self):
        with pytest.raises(SystemExit) as exc:
            ai.run_audit("cloakhq/cloakbrowser@some-marketplace")
        assert exc.value.code != 0
