"""
Nightly catalog enrichment batch runner — SAG-2184.

Entry point for the Paperclip routine execution. Calls EnrichmentDispatcher.run_batch(),
posts a summary comment to the execution issue, and exits 0 on success or partial success,
1 on total failure (no rows processed and DB error).

Environment variables (beyond dispatcher's own set):
  PAPERCLIP_TASK_ID   current execution issue UUID (auto-injected by harness)
  PAPERCLIP_RUN_ID    current run ID for X-Paperclip-Run-Id audit header
  PAPERCLIP_API_URL   Paperclip control-plane URL
  PAPERCLIP_API_KEY   scoped JWT for Paperclip API calls

OpenShell sandbox (SAG-2357 production routing flip):
  OPENSH_SANDBOX_ENABLED   1=on, 0=off (default 0; flip to 1 for production — CEO approved 2026-05-26)
  OPENSH_SANDBOX_TAG        openshell image tag (default 0.0.47)
  OPENSH_USE_GRPC           1=gRPC dispatcher, 0=CLI (default 1; gRPC is -24% latency)
  OPENSH_MACOS_FALLBACK     container|none (default container)
  OPENSH_POOL_SIZE          sandbox pool size (default 3; ~42 MiB total at pool=3)
  OPENSH_RAM_ALERT_MIB      alert threshold in MiB (default 200)
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

import httpx

# SAG-3455: load enrichment/.env explicitly so LITELLM_API_KEY is present regardless
# of whether the calling shell sourced the file. os.environ is not overridden for keys
# already set, so injected routine env always wins over .env defaults.
_ENV_FILE = pathlib.Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        pass  # python-dotenv not installed; fall back to shell-sourced env

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher import DispatcherConfig, EnrichmentDispatcher  # type: ignore[import]

try:
    from scripts.opensh_shim.config import ShimConfig  # type: ignore[import]
    from scripts.opensh_shim.pool import SandboxPool  # type: ignore[import]
    from scripts.opensh_shim.monitor import PoolMonitor  # type: ignore[import]
    _OPENSH_AVAILABLE = True
except ImportError:
    _OPENSH_AVAILABLE = False

logger = logging.getLogger(__name__)

# SAG-3529: single-runner guard — only one batch_runner.py may drain at a time.
# On Linux, flock() is process-scoped and auto-released on crash/exit, so no
# stale-lock cleanup is needed.
_LOCK_PATH: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / "enrichment_batch_runner.lock"


def _try_acquire_lock() -> "IO[str] | None":
    """
    Try to acquire an exclusive non-blocking flock on _LOCK_PATH.
    Returns the open file descriptor on success, or None if another runner holds it.
    Caller must release with _release_lock() when done.
    """
    try:
        fd = open(_LOCK_PATH, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except BlockingIOError:
        try:
            fd.close()
        except Exception:
            pass
        return None


def _release_lock(fd: "IO[str]") -> None:
    """Release a lock fd returned by _try_acquire_lock()."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except OSError:
        pass


def _build_comment(summary: dict, started_at: datetime, finished_at: datetime) -> str:
    duration_s = (finished_at - started_at).total_seconds()
    total = summary["total"]
    done = summary["done"]
    failed = summary["failed"]
    cap_paused = summary["cap_paused"]
    pool_ram_mib = summary.get("opensh_pool_ram_mib")
    pool_size = summary.get("opensh_pool_size")

    if total == 0:
        status_line = "Batch complete — queue empty, no rows to process."
    elif failed == total:
        status_line = f"Batch FAILED — {failed}/{total} rows failed enrichment."
    elif failed > 0:
        status_line = f"Batch partial — {done}/{total} enriched, {failed} failed."
    else:
        status_line = f"Batch complete — {done}/{total} rows enriched successfully."

    lines = [
        f"## Nightly enrichment batch",
        f"",
        status_line,
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Rows processed | {total} |",
        f"| Enriched (primary + fallback) | {done} |",
        f"| Failed (both tiers) | {failed} |",
        f"| Reviewer cap paused | {'yes ⚠️' if cap_paused else 'no'} |",
        f"| Duration | {duration_s:.1f}s |",
        f"| Finished at | {finished_at.strftime('%Y-%m-%d %H:%M:%S')} UTC |",
    ]

    if pool_ram_mib is not None:
        lines += [
            f"| OpenShell pool RAM | {pool_ram_mib:.1f} MiB (size={pool_size}) |",
        ]

    if cap_paused:
        lines += [
            f"",
            f"> **Opus reviewer cost cap hit.** The routine has been auto-paused. "
            f"Manual unpause required on [SAG-2160](/SAG/issues/SAG-2160).",
        ]

    return "\n".join(lines)


async def _post_comment(
    api_url: str,
    api_key: str,
    run_id: str,
    issue_id: str,
    body: str,
) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Paperclip-Run-Id": run_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{api_url}/api/issues/{issue_id}/comments",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()
            logger.info("Summary comment posted to issue %s", issue_id)
        except Exception as exc:
            logger.warning("Failed to post summary comment: %s", exc)


async def _mark_issue_done(
    api_url: str,
    api_key: str,
    run_id: str,
    issue_id: str,
    comment: str,
) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Paperclip-Run-Id": run_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.patch(
                f"{api_url}/api/issues/{issue_id}",
                headers=headers,
                json={"status": "done", "comment": comment},
            )
            r.raise_for_status()
            logger.info("Issue %s marked done", issue_id)
        except Exception as exc:
            logger.warning("Failed to mark issue done: %s", exc)


async def run() -> int:
    """
    Execute the nightly batch and post summary to Paperclip.
    Returns exit code: 0 on success or empty queue, 1 on dispatcher error.
    """
    # SAG-3529: enforce single runner — exit clean if another drain is active.
    lock_fd = _try_acquire_lock()
    if lock_fd is None:
        logger.info(
            "enrichment drain already active — another batch_runner.py holds the run lock, exiting clean (SAG-3529)"
        )
        return 0

    try:
        return await _run_batch()
    finally:
        _release_lock(lock_fd)


async def _run_batch() -> int:
    """Inner batch execution, called only when the run lock is held."""
    cfg = DispatcherConfig.from_env()

    api_url = os.environ.get("PAPERCLIP_API_URL", "")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    run_id = os.environ.get("PAPERCLIP_RUN_ID", "")
    task_id = os.environ.get("PAPERCLIP_TASK_ID", "")

    # --- OpenShell sandbox pool initialization (SAG-2357) ---
    sandbox_pool: SandboxPool | None = None
    pool_monitor: PoolMonitor | None = None
    if _OPENSH_AVAILABLE:
        shim_cfg = ShimConfig.from_env()
        if shim_cfg.enabled:
            sandbox_pool = SandboxPool(shim_cfg.sandbox_tag, shim_cfg.pool_size)
            sandbox_pool.prefill()
            pool_monitor = PoolMonitor(
                shim_cfg, sandbox_pool,
                api_url=api_url, api_key=api_key,
                issue_id=task_id, run_id=run_id,
            )
            logger.info(
                "OpenShell sandbox pool active: tag=%s pool_size=%d grpc=%s",
                shim_cfg.sandbox_tag, shim_cfg.pool_size, shim_cfg.use_grpc,
            )

    started_at = datetime.now(timezone.utc)
    exit_code = 0

    try:
        dispatcher = EnrichmentDispatcher(cfg)
        summary = await dispatcher.run_batch()
        logger.info("Batch summary: %s", json.dumps(summary))
    except Exception as exc:
        logger.error("Dispatcher error: %s", exc)
        summary = {"total": 0, "done": 0, "failed": 0, "cap_paused": False, "error": str(exc)}
        exit_code = 1

    # --- Pool RAM check + alert ---
    if pool_monitor:
        metrics = await pool_monitor.check_and_alert()
        summary["opensh_pool_ram_mib"] = metrics["pool_ram_mib"]
        summary["opensh_pool_size"] = metrics["pool_size"]

    finished_at = datetime.now(timezone.utc)

    if sandbox_pool:
        sandbox_pool.teardown()

    if api_url and api_key and task_id:
        comment = _build_comment(summary, started_at, finished_at)
        status_word = "done" if exit_code == 0 else "Dispatcher failed"
        close_comment = f"{status_word}\n\n" + comment if exit_code != 0 else comment
        await _mark_issue_done(api_url, api_key, run_id, task_id, close_comment)
    else:
        logger.warning("PAPERCLIP_API_URL/API_KEY/TASK_ID not set — skipping issue update")

    return exit_code


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
