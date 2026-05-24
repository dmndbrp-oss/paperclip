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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from dispatcher import DispatcherConfig, EnrichmentDispatcher  # type: ignore[import]

logger = logging.getLogger(__name__)


def _build_comment(summary: dict, started_at: datetime, finished_at: datetime) -> str:
    duration_s = (finished_at - started_at).total_seconds()
    total = summary["total"]
    done = summary["done"]
    failed = summary["failed"]
    cap_paused = summary["cap_paused"]

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
    cfg = DispatcherConfig.from_env()

    api_url = os.environ.get("PAPERCLIP_API_URL", "")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    run_id = os.environ.get("PAPERCLIP_RUN_ID", "")
    task_id = os.environ.get("PAPERCLIP_TASK_ID", "")

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

    finished_at = datetime.now(timezone.utc)

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
