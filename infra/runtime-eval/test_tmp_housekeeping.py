"""Tests for tmp_housekeeping.py — SAG-6346.

Fixture /tmp trees mix dead-PID/alive-PID/dirty/clean/open-handle/young/old
dirs and assert only dirs passing every safe-delete gate are selected.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from tmp_housekeeping import (
    Candidate,
    dir_age_hours,
    find_pcvt_candidates,
    find_worktree_candidates,
    parse_pcvt_pid,
    run_housekeeping,
)


def _touch_dir(path: Path, age_hours: float = 0.0):
    path.mkdir(parents=True, exist_ok=True)
    if age_hours:
        stamp = time.time() - age_hours * 3600
        os.utime(path, (stamp, stamp))
    return path


def _dead_pid() -> int:
    """A PID that is guaranteed not to be a running process."""
    # Spawn and immediately reap a child; its PID is dead the instant it exits.
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


# ---------------------------------------------------------------------------
# parse_pcvt_pid
# ---------------------------------------------------------------------------

def test_parse_pcvt_pid_matches_format():
    assert parse_pcvt_pid("pcvt-12345-7-ab12") == 12345


def test_parse_pcvt_pid_rejects_non_numeric_pid():
    assert parse_pcvt_pid("pcvt-abc-7-ab12") is None


def test_parse_pcvt_pid_rejects_non_pcvt_name():
    assert parse_pcvt_pid("paperclip-worktree-foo") is None


# ---------------------------------------------------------------------------
# dir_age_hours
# ---------------------------------------------------------------------------

def test_dir_age_hours_reports_elapsed_time(tmp_path):
    d = _touch_dir(tmp_path / "sample", age_hours=13)
    age = dir_age_hours(d)
    assert 12.9 <= age <= 13.1


# ---------------------------------------------------------------------------
# find_pcvt_candidates
# ---------------------------------------------------------------------------

def test_pcvt_dead_pid_and_old_is_candidate(tmp_path):
    dead = _dead_pid()
    _touch_dir(tmp_path / f"pcvt-{dead}-1-aaaa", age_hours=13)
    cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert len(cands) == 1
    assert cands[0].kind == "pcvt"


def test_pcvt_dead_pid_but_too_young_is_not_candidate(tmp_path):
    dead = _dead_pid()
    _touch_dir(tmp_path / f"pcvt-{dead}-1-aaaa", age_hours=1)
    cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert cands == []


def test_pcvt_alive_pid_is_never_candidate_even_if_old(tmp_path):
    alive = os.getpid()
    _touch_dir(tmp_path / f"pcvt-{alive}-1-aaaa", age_hours=48)
    cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert cands == []


def test_pcvt_malformed_name_is_skipped(tmp_path):
    _touch_dir(tmp_path / "pcvt-not-a-pid", age_hours=48)
    cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert cands == []


def test_pcvt_ignores_unrelated_dirs(tmp_path):
    _touch_dir(tmp_path / "some-other-dir", age_hours=48)
    cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert cands == []


# ---------------------------------------------------------------------------
# find_worktree_candidates
# ---------------------------------------------------------------------------

def _init_git_dir(path: Path, dirty: bool = False):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    if dirty:
        (path / "README.md").write_text("dirty change\n")
    return path


def test_worktree_clean_no_handles_old_is_candidate(tmp_path):
    d = _init_git_dir(tmp_path / "paperclip-worktree-sag1", dirty=False)
    os.utime(d, (time.time() - 25 * 3600,) * 2)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    assert len(cands) == 1
    assert cands[0].kind == "worktree"


def test_worktree_dirty_is_not_candidate(tmp_path):
    d = _init_git_dir(tmp_path / "sag-review-checkout", dirty=True)
    os.utime(d, (time.time() - 25 * 3600,) * 2)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    assert cands == []


def test_worktree_open_handle_is_not_candidate(tmp_path):
    d = _init_git_dir(tmp_path / "paperclip-iterate-run", dirty=False)
    os.utime(d, (time.time() - 25 * 3600,) * 2)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: True
    )
    assert cands == []


def test_worktree_too_young_is_not_candidate(tmp_path):
    d = _init_git_dir(tmp_path / "paperclip-clean-run", dirty=False)
    # fresh mtime (default from creation), well under 24h
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    assert cands == []


def test_worktree_name_not_matching_pattern_is_ignored(tmp_path):
    # "paperclip-foo" has no worktree|review|iterate|clean token
    d = _init_git_dir(tmp_path / "paperclip-foo", dirty=False)
    os.utime(d, (time.time() - 25 * 3600,) * 2)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    assert cands == []


def test_worktree_non_git_dir_uses_age_and_handle_gates_only(tmp_path):
    d = _touch_dir(tmp_path / "sag-review-notes", age_hours=25)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    assert len(cands) == 1


def test_claude_dirs_are_never_scanned(tmp_path):
    _touch_dir(tmp_path / "claude-1000", age_hours=999)
    _init_git_dir(tmp_path / "claude-worktree-review", dirty=False)
    os.utime(tmp_path / "claude-worktree-review", (time.time() - 999 * 3600,) * 2)
    cands = find_worktree_candidates(
        tmp_path, min_age_hours=24, has_open_handles=lambda p: False
    )
    pcvt_cands = find_pcvt_candidates(tmp_path, min_age_hours=12)
    assert cands == []
    assert pcvt_cands == []


# ---------------------------------------------------------------------------
# run_housekeeping — dry-run vs apply, logging, prune
# ---------------------------------------------------------------------------

def test_dry_run_logs_but_never_deletes(tmp_path):
    dead = _dead_pid()
    pcvt_dir = _touch_dir(tmp_path / f"pcvt-{dead}-1-aaaa", age_hours=13)
    log_path = tmp_path / "results" / "tmp_housekeeping.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    summary = run_housekeeping(
        tmp_root=tmp_path,
        log_path=log_path,
        apply=False,
        has_open_handles=lambda p: False,
        prune_worktree_repo=lambda p: None,
    )

    assert pcvt_dir.exists()
    assert summary.removed == []
    assert len(summary.candidates) == 1
    assert log_path.exists()
    assert "DRY-RUN" in log_path.read_text()


def test_apply_mode_removes_only_gated_candidates(tmp_path):
    dead = _dead_pid()
    alive = os.getpid()
    doomed = _touch_dir(tmp_path / f"pcvt-{dead}-1-aaaa", age_hours=13)
    survivor = _touch_dir(tmp_path / f"pcvt-{alive}-1-bbbb", age_hours=48)
    log_path = tmp_path / "results" / "tmp_housekeeping.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    summary = run_housekeeping(
        tmp_root=tmp_path,
        log_path=log_path,
        apply=True,
        has_open_handles=lambda p: False,
        prune_worktree_repo=lambda p: None,
    )

    assert not doomed.exists()
    assert survivor.exists()
    assert summary.removed == [doomed]


def test_apply_mode_prunes_affected_worktree_repos(tmp_path):
    d = _init_git_dir(tmp_path / "paperclip-worktree-sag1", dirty=False)
    os.utime(d, (time.time() - 25 * 3600,) * 2)
    log_path = tmp_path / "results" / "tmp_housekeeping.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pruned = []

    run_housekeeping(
        tmp_root=tmp_path,
        log_path=log_path,
        apply=True,
        has_open_handles=lambda p: False,
        prune_worktree_repo=lambda p: pruned.append(p),
    )

    assert not d.exists()
    assert pruned == [d]


def test_apply_mode_actually_prunes_real_linked_worktree(tmp_path):
    """SAG-6354: end-to-end against the DEFAULT prune_worktree_repo.

    Regression test for the bug where `repo_hint` was set to the worktree
    dir itself, which is deleted by `shutil.rmtree` before
    `prune_worktree_repo` runs — making `git worktree prune` a silent no-op
    against a path that no longer exists. Uses a real `git worktree add`
    linked worktree (not the mocked `prune_worktree_repo` lambda above) so
    the main repo's stale registration must actually be cleared.
    """
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=main_repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=main_repo, check=True)
    (main_repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=main_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=main_repo, check=True)

    worktree_dir = tmp_path / "paperclip-worktree-sag2"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree_dir), "-b", "sag2-branch"],
        cwd=main_repo,
        check=True,
    )
    os.utime(worktree_dir, (time.time() - 25 * 3600,) * 2)

    def _worktree_list(repo: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            stdout=subprocess.PIPE,
            check=True,
        )
        return result.stdout.decode()

    assert str(worktree_dir) in _worktree_list(main_repo)

    log_path = tmp_path / "results" / "tmp_housekeeping.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    run_housekeeping(
        tmp_root=tmp_path,
        log_path=log_path,
        apply=True,
        has_open_handles=lambda p: False,
        # prune_worktree_repo and resolve_main_repo both use their real
        # (non-mocked) default implementations here.
    )

    assert not worktree_dir.exists()
    assert str(worktree_dir) not in _worktree_list(main_repo)
