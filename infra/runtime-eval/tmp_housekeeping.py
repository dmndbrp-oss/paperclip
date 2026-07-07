#!/usr/bin/env python3
"""SAG-6346: durable /tmp housekeeping — prune stale scratch dirs safely.

Follow-up to SAG-6340 (one-time /tmp inode-exhaustion cleanup). Runs nightly
(invoked from nightly_eval.sh) and removes only dirs that pass explicit
safety gates:

1. `/tmp/pcvt-<PID>-<seq>-<rand>` conversion scratch — removed only if the
   owning PID is dead AND the dir is older than 12h.
2. `/tmp/{paperclip-*,sag*}` worktree/review/iterate/clean checkouts —
   removed only if git-clean (or not a git dir), no open file handles, and
   older than 24h. Losing the checkout loses no durable work: branch/commits
   live in the shared object store, so `git worktree prune` is run afterward
   on the owning repo to clear the stale registration.

`/tmp/claude-*` (live-session bookkeeping) is never scanned, matching the
ticket's explicit "never touch" requirement.

Defaults to dry-run (log candidates only); set TMP_HOUSEKEEPING_APPLY=1 to
actually delete.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

PCVT_RE = re.compile(r"^pcvt-(\d+)-\d+-[0-9a-zA-Z]+$")
WORKTREE_NAME_RE = re.compile(r"(worktree|review|iterate|clean)", re.IGNORECASE)
WORKTREE_PREFIX_RE = re.compile(r"^(paperclip-|sag)", re.IGNORECASE)

DEFAULT_PCVT_MIN_AGE_HOURS = 12
DEFAULT_WORKTREE_MIN_AGE_HOURS = 24


@dataclass
class Candidate:
    path: Path
    kind: str  # "pcvt" or "worktree"
    reason: str
    repo_hint: Optional[Path] = None  # main repo to `worktree prune` afterward


@dataclass
class Summary:
    candidates: List[Candidate] = field(default_factory=list)
    removed: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def parse_pcvt_pid(name: str) -> Optional[int]:
    m = PCVT_RE.match(name)
    if not m:
        return None
    return int(m.group(1))


def dir_age_hours(path: Path, now: Optional[float] = None) -> float:
    now = now if now is not None else time.time()
    return (now - path.stat().st_mtime) / 3600.0


def _default_is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive (safe default).
        return True


def _default_has_open_handles(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "+D", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # lsof unavailable or hung — fail closed (assume open handle, skip removal).
        return True
    return bool(result.stdout.strip())


def _default_git_status_clean(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == b""


def _default_resolve_main_repo(entry: Path) -> Optional[Path]:
    """Resolve a worktree's owning main-repo path via --git-common-dir.

    Must be called while `entry` still exists (i.e. at discovery time,
    before any delete) — `git -C <entry>` needs the directory to be present
    to resolve anything.
    """
    try:
        common_dir = subprocess.run(
            ["git", "-C", str(entry), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if common_dir.returncode != 0:
        return None
    git_dir = Path(common_dir.stdout.decode().strip())
    if not git_dir.is_absolute():
        git_dir = (entry / git_dir).resolve()
    return git_dir.parent if git_dir.name == ".git" else git_dir


def _default_prune_worktree_repo(repo_path: Path) -> None:
    """Run `git worktree prune` on an already-resolved main-repo path.

    `repo_path` must be the main repo (resolved via `_default_resolve_main_repo`
    at discovery time), not the worktree checkout — the checkout may already be
    deleted by the time this runs.
    """
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _is_claude_dir(name: str) -> bool:
    return name.startswith("claude-")


def find_pcvt_candidates(
    tmp_root: Path,
    min_age_hours: float = DEFAULT_PCVT_MIN_AGE_HOURS,
    is_pid_alive: Callable[[int], bool] = _default_is_pid_alive,
    now: Optional[float] = None,
) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not tmp_root.is_dir():
        return candidates
    for entry in sorted(tmp_root.iterdir()):
        if not entry.is_dir() or _is_claude_dir(entry.name):
            continue
        pid = parse_pcvt_pid(entry.name)
        if pid is None:
            continue
        if is_pid_alive(pid):
            continue
        if dir_age_hours(entry, now=now) <= min_age_hours:
            continue
        candidates.append(
            Candidate(path=entry, kind="pcvt", reason=f"dead pid {pid}, age>{min_age_hours}h")
        )
    return candidates


def find_worktree_candidates(
    tmp_root: Path,
    min_age_hours: float = DEFAULT_WORKTREE_MIN_AGE_HOURS,
    has_open_handles: Callable[[Path], bool] = _default_has_open_handles,
    git_status_clean: Callable[[Path], bool] = _default_git_status_clean,
    resolve_main_repo: Callable[[Path], Optional[Path]] = _default_resolve_main_repo,
    now: Optional[float] = None,
) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not tmp_root.is_dir():
        return candidates
    for entry in sorted(tmp_root.iterdir()):
        if not entry.is_dir() or _is_claude_dir(entry.name):
            continue
        if not WORKTREE_PREFIX_RE.match(entry.name):
            continue
        if not WORKTREE_NAME_RE.search(entry.name):
            continue
        if dir_age_hours(entry, now=now) <= min_age_hours:
            continue
        is_git_dir = (entry / ".git").exists()
        if is_git_dir and not git_status_clean(entry):
            continue
        if has_open_handles(entry):
            continue
        candidates.append(
            Candidate(
                path=entry,
                kind="worktree",
                reason=f"clean={is_git_dir}, no open handles, age>{min_age_hours}h",
                repo_hint=resolve_main_repo(entry) if is_git_dir else None,
            )
        )
    return candidates


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_housekeeping(
    tmp_root: Path,
    log_path: Path,
    apply: bool = False,
    pcvt_min_age_hours: float = DEFAULT_PCVT_MIN_AGE_HOURS,
    worktree_min_age_hours: float = DEFAULT_WORKTREE_MIN_AGE_HOURS,
    is_pid_alive: Callable[[int], bool] = _default_is_pid_alive,
    has_open_handles: Callable[[Path], bool] = _default_has_open_handles,
    git_status_clean: Callable[[Path], bool] = _default_git_status_clean,
    prune_worktree_repo: Callable[[Path], None] = _default_prune_worktree_repo,
) -> Summary:
    summary = Summary()
    summary.candidates.extend(
        find_pcvt_candidates(tmp_root, min_age_hours=pcvt_min_age_hours, is_pid_alive=is_pid_alive)
    )
    summary.candidates.extend(
        find_worktree_candidates(
            tmp_root,
            min_age_hours=worktree_min_age_hours,
            has_open_handles=has_open_handles,
            git_status_clean=git_status_clean,
        )
    )

    lines = [f"{_ts()} [tmp_housekeeping] mode={'APPLY' if apply else 'DRY-RUN'} root={tmp_root}"]
    pruned_repos = set()

    for cand in summary.candidates:
        if apply:
            try:
                shutil.rmtree(cand.path)
                summary.removed.append(cand.path)
                lines.append(f"{_ts()} [tmp_housekeeping] REMOVED {cand.path} ({cand.kind}: {cand.reason})")
                if cand.repo_hint is not None and cand.repo_hint not in pruned_repos:
                    prune_worktree_repo(cand.repo_hint)
                    pruned_repos.add(cand.repo_hint)
            except OSError as exc:
                summary.errors.append(f"{cand.path}: {exc}")
                lines.append(f"{_ts()} [tmp_housekeeping] ERROR removing {cand.path}: {exc}")
        else:
            lines.append(
                f"{_ts()} [tmp_housekeeping] DRY-RUN would remove {cand.path} ({cand.kind}: {cand.reason})"
            )

    lines.append(
        f"{_ts()} [tmp_housekeeping] summary: candidates={len(summary.candidates)} "
        f"removed={len(summary.removed)} errors={len(summary.errors)}"
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write("\n".join(lines) + "\n")

    return summary


def main() -> int:
    tmp_root = Path(os.environ.get("TMP_HOUSEKEEPING_ROOT", "/tmp"))
    script_dir = Path(__file__).parent
    log_path = Path(
        os.environ.get(
            "TMP_HOUSEKEEPING_LOG",
            str(script_dir / "results" / "tmp_housekeeping.log"),
        )
    )
    apply = os.environ.get("TMP_HOUSEKEEPING_APPLY", "0") == "1"
    pcvt_min_age_hours = float(os.environ.get("TMP_HOUSEKEEPING_PCVT_AGE_HOURS", DEFAULT_PCVT_MIN_AGE_HOURS))
    worktree_min_age_hours = float(
        os.environ.get("TMP_HOUSEKEEPING_WORKTREE_AGE_HOURS", DEFAULT_WORKTREE_MIN_AGE_HOURS)
    )

    summary = run_housekeeping(
        tmp_root=tmp_root,
        log_path=log_path,
        apply=apply,
        pcvt_min_age_hours=pcvt_min_age_hours,
        worktree_min_age_hours=worktree_min_age_hours,
    )

    print(
        f"tmp_housekeeping: mode={'APPLY' if apply else 'DRY-RUN'} "
        f"candidates={len(summary.candidates)} removed={len(summary.removed)} "
        f"errors={len(summary.errors)}"
    )
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
