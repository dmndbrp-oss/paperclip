"""
Enrichment batch dispatcher — SAG-2160 sub-deliverable 2b.

Pull pending rows from enrichment_staging.enrichment_queue, run:
  primary (Qwen 2.5 14B via LiteLLM) → fallback (Llama 3.3 70B via LiteLLM)
  → reviewer (Opus via Anthropic, cost-cap gated)
Write results to enrichment_staging.enrichment_staging.

Entry point:
  python -m enrichment.dispatcher [--batch-size N]
  or imported: asyncio.run(EnrichmentDispatcher(cfg).run_batch())

Environment variables:
  DATABASE_URL          postgres://user:pass@host/db  (required)
  LITELLM_BASE_URL      default http://localhost:4000
  ANTHROPIC_API_KEY     required for reviewer tier
  PAPERCLIP_API_URL     for routine pause notification
  PAPERCLIP_API_KEY     for routine pause notification
  PAPERCLIP_ROUTINE_ID  routine to pause when cap hit
  ENRICHMENT_ISSUE_ID   issue to post cost-cap comment on
  COST_CAP_LEDGER_PATH  default ./enrichment_cost_ledger.json
  ENRICHMENT_BATCH_SIZE default 10
  ENRICHMENT_DISPATCHER_CONCURRENCY  default 1, max 4
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pilot-artifacts"))
from validator import validate  # type: ignore[import]

from cost_cap import CostCapTracker

logger = logging.getLogger(__name__)

# Opus pricing (per 1K tokens) — matches dispatcher/src/router/costCap.ts
OPUS_INPUT_PER_1K = 0.015
OPUS_OUTPUT_PER_1K = 0.075

PRIMARY_MODEL = "ollama/qwen2.5:14b-instruct-q4_K_M"
FALLBACK_MODEL = "ollama/llama3.3:70b-instruct-q4_K_M"
REVIEWER_MODEL = "claude-opus-4-7"

PRIMARY_TIMEOUT = 60.0
FALLBACK_TIMEOUT = 180.0
REVIEWER_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class DispatcherConfig:
    database_url: str
    litellm_base_url: str = "http://localhost:4000"
    anthropic_api_key: str = ""
    paperclip_api_url: str = ""
    paperclip_api_key: str = ""
    paperclip_routine_id: str = ""
    enrichment_issue_id: str = ""
    cost_cap_ledger_path: str = "./enrichment_cost_ledger.json"
    batch_size: int = 10
    concurrency: int = 1

    @classmethod
    def from_env(cls) -> "DispatcherConfig":
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        raw_concurrency = int(os.environ.get("ENRICHMENT_DISPATCHER_CONCURRENCY", "1"))
        concurrency = min(max(raw_concurrency, 1), 4)  # clamp [1, 4]
        return cls(
            database_url=database_url,
            litellm_base_url=os.environ.get("LITELLM_BASE_URL", "http://localhost:4000"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            paperclip_api_url=os.environ.get("PAPERCLIP_API_URL", ""),
            paperclip_api_key=os.environ.get("PAPERCLIP_API_KEY", ""),
            paperclip_routine_id=os.environ.get("PAPERCLIP_ROUTINE_ID", ""),
            enrichment_issue_id=os.environ.get("ENRICHMENT_ISSUE_ID", ""),
            cost_cap_ledger_path=os.environ.get("COST_CAP_LEDGER_PATH", "./enrichment_cost_ledger.json"),
            batch_size=int(os.environ.get("ENRICHMENT_BATCH_SIZE", "10")),
            concurrency=concurrency,
        )


# ---------------------------------------------------------------------------
# DB helpers (synchronous psycopg2, called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _db_connect(database_url: str):
    return psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)


def _fetch_pending_rows(conn, limit: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_row_id, payload_json
            FROM enrichment_staging.enrichment_queue
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def _mark_in_flight(conn, row_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE enrichment_staging.enrichment_queue SET status='in_flight', started_at=NOW() WHERE id=%s",
            (row_id,),
        )
    conn.commit()


def _mark_queue_done(conn, row_id: str, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE enrichment_staging.enrichment_queue SET status=%s, finished_at=NOW() WHERE id=%s",
            (status, row_id),
        )
    conn.commit()


def _write_staging(conn, batch_id: str, source_row_id: str, result: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO enrichment_staging.enrichment_staging
              (batch_id, source_row_id, primary_output_json, fallback_output_json,
               validator_result, anomaly_score, reviewer_verdict)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                batch_id,
                source_row_id,
                json.dumps(result.get("primary_output")) if result.get("primary_output") else None,
                json.dumps(result.get("fallback_output")) if result.get("fallback_output") else None,
                json.dumps(result.get("validator_result")) if result.get("validator_result") else None,
                result.get("anomaly_score"),
                result.get("reviewer_verdict"),
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# LiteLLM client (OpenAI-compatible)
# ---------------------------------------------------------------------------

def _build_enrichment_messages(payload: dict) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the enrichment task."""
    system = (
        "You are a product data enrichment specialist for Sage Surfaces. "
        "Analyze the product information and produce a structured JSON object describing "
        "the surface material's attributes. Output ONLY valid JSON. No prose. No markdown fences."
    )
    user = (
        "Enrich the following surface product. Return only the JSON object.\n\n"
        f"Product input:\n{json.dumps(payload, indent=2)}"
    )
    return system, user


def _build_reviewer_messages(payload: dict, enriched: dict) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the Opus anomaly reviewer."""
    system = (
        "You are an anomaly reviewer for Sage Surfaces' automated catalog enrichment pipeline. "
        "Your job is narrow: read a product's original description alongside the AI-generated "
        "enrichment, and decide if the enrichment looks plausible. "
        "Return a JSON object with exactly three fields: "
        '"anomaly_score" (float 0.0–1.0), '
        '"anomaly_reason" (one or two sentences, max 300 chars), '
        '"triggered_rules" (list of rule IDs or empty list). '
        "Output ONLY valid JSON."
    )
    user = (
        f"Original product data:\n{json.dumps(payload, indent=2)}\n\n"
        f"AI enrichment:\n{json.dumps(enriched, indent=2)}"
    )
    return system, user


async def _litellm_complete(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
) -> tuple[str | None, bool]:
    """
    Call LiteLLM gateway. Returns (content, timed_out).
    Returns (None, False) on non-timeout errors.
    """
    try:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 2048,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content, False
    except httpx.TimeoutException:
        return None, True
    except Exception as exc:
        logger.warning("LiteLLM call failed for model=%s: %s", model, exc)
        return None, False


async def _anthropic_reviewer(
    api_key: str,
    payload: dict,
    enriched: dict,
) -> tuple[dict | None, float]:
    """
    Call Opus for anomaly review. Returns (verdict_dict, cost_usd).
    On error returns (None, 0.0).
    """
    try:
        import anthropic as _ant

        system, user = _build_reviewer_messages(payload, enriched)
        aclient = _ant.AsyncAnthropic(api_key=api_key)
        resp = await aclient.messages.create(
            model=REVIEWER_MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        content = resp.content[0].text if resp.content else ""
        cost = (
            (resp.usage.input_tokens / 1000) * OPUS_INPUT_PER_1K
            + (resp.usage.output_tokens / 1000) * OPUS_OUTPUT_PER_1K
        )
        try:
            verdict = json.loads(content)
        except json.JSONDecodeError:
            verdict = {"anomaly_score": None, "anomaly_reason": content[:300], "triggered_rules": []}
        return verdict, cost
    except Exception as exc:
        logger.warning("Anthropic reviewer failed: %s", exc)
        return None, 0.0


# ---------------------------------------------------------------------------
# Paperclip notifications
# ---------------------------------------------------------------------------

async def _pause_routine(cfg: DispatcherConfig, weekly_spend: float) -> None:
    """Pause the Paperclip routine via API and post a comment."""
    if not (cfg.paperclip_api_url and cfg.paperclip_api_key and cfg.paperclip_routine_id):
        logger.warning("Routine pause skipped: PAPERCLIP_ROUTINE_ID / credentials not set")
        return

    headers = {
        "Authorization": f"Bearer {cfg.paperclip_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        # Pause the routine
        try:
            r = await client.patch(
                f"{cfg.paperclip_api_url}/api/routines/{cfg.paperclip_routine_id}",
                headers=headers,
                json={"status": "paused"},
            )
            r.raise_for_status()
            logger.info("Routine %s paused due to cost cap", cfg.paperclip_routine_id)
        except Exception as exc:
            logger.error("Failed to pause routine: %s", exc)

        # Post comment to the enrichment issue
        if not cfg.enrichment_issue_id:
            return
        comment = (
            "**COST CAP HIT — enrichment routine auto-paused.**\n\n"
            f"Rolling 7-day Opus reviewer spend: **${weekly_spend:.2f}** (cap: $50.00).\n\n"
            "Reviewer tier suspended for remaining rows in this batch. "
            "Primary and fallback tiers continue.\n\n"
            "**Manual unpause required.** "
            "Please post an explicit comment on this issue or on "
            "[SAG-2146](/SAG/issues/SAG-2146) to re-enable.\n\n"
            "cc [@CEO](agent://b0f67cc2-259e-477b-ac89-d0ff4e7c8e89) "
            "[@CTO](agent://f3c48afc-c339-4e43-b47b-a42a0891229d)"
        )
        try:
            r = await client.post(
                f"{cfg.paperclip_api_url}/api/issues/{cfg.enrichment_issue_id}/comments",
                headers=headers,
                json={"body": comment},
            )
            r.raise_for_status()
            logger.info("Cost-cap comment posted to issue %s", cfg.enrichment_issue_id)
        except Exception as exc:
            logger.error("Failed to post cost-cap comment: %s", exc)


# ---------------------------------------------------------------------------
# Row processor
# ---------------------------------------------------------------------------

async def _process_row(
    row: dict,
    batch_id: str,
    cfg: DispatcherConfig,
    cost_tracker: CostCapTracker,
    http_client: httpx.AsyncClient,
    conn,
    cap_paused: asyncio.Event,
) -> str:
    """
    Process one enrichment queue row. Returns final tier: 'primary', 'fallback', 'failed'.
    Updates queue and staging tables in-place.
    """
    row_id = str(row["id"])
    source_row_id = row["source_row_id"]
    payload = row["payload_json"] if isinstance(row["payload_json"], dict) else json.loads(row["payload_json"])

    await asyncio.to_thread(_mark_in_flight, conn, row_id)

    result: dict[str, Any] = {
        "primary_output": None,
        "fallback_output": None,
        "validator_result": None,
        "anomaly_score": None,
        "reviewer_verdict": None,
    }

    system, user = _build_enrichment_messages(payload)
    tier_used = "failed"

    # --- Primary tier ---
    content, timed_out = await _litellm_complete(
        http_client, cfg.litellm_base_url, PRIMARY_MODEL, system, user, PRIMARY_TIMEOUT
    )
    if content:
        try:
            parsed = json.loads(content)
            validation = validate(parsed)
            result["primary_output"] = parsed
            result["validator_result"] = validation
            if validation.get("valid"):
                tier_used = "primary"
        except (json.JSONDecodeError, Exception) as exc:
            logger.debug("Primary output parse/validate error for %s: %s", source_row_id, exc)

    # --- Fallback tier (if primary failed schema validation) ---
    if tier_used == "failed":
        content, timed_out = await _litellm_complete(
            http_client, cfg.litellm_base_url, FALLBACK_MODEL, system, user, FALLBACK_TIMEOUT
        )
        if content:
            try:
                parsed = json.loads(content)
                validation = validate(parsed)
                result["fallback_output"] = parsed
                result["validator_result"] = validation
                if validation.get("valid"):
                    tier_used = "fallback"
            except (json.JSONDecodeError, Exception) as exc:
                logger.debug("Fallback output parse/validate error for %s: %s", source_row_id, exc)

    # --- Reviewer tier (Opus, cost-cap gated) ---
    if tier_used in ("primary", "fallback") and cfg.anthropic_api_key:
        enriched = result.get("primary_output") or result.get("fallback_output")
        if cap_paused.is_set():
            result["reviewer_verdict"] = "cap_paused"
            logger.debug("Reviewer skipped for %s (cap already paused)", source_row_id)
        else:
            # Estimate cost conservatively (512 tokens in + 512 out)
            estimated_cost = (512 / 1000) * OPUS_INPUT_PER_1K + (512 / 1000) * OPUS_OUTPUT_PER_1K
            if cost_tracker.would_breach(estimated_cost):
                weekly = cost_tracker.weekly_spend()
                logger.warning("Cost cap hit at $%.2f — pausing routine", weekly)
                cap_paused.set()
                result["reviewer_verdict"] = "cap_paused"
                # Fire-and-forget pause notification
                asyncio.create_task(_pause_routine(cfg, weekly))
            else:
                verdict, actual_cost = await _anthropic_reviewer(
                    cfg.anthropic_api_key, payload, enriched or {}
                )
                if verdict is not None:
                    cost_tracker.record(actual_cost)
                    result["anomaly_score"] = verdict.get("anomaly_score")
                    result["reviewer_verdict"] = json.dumps(verdict)
                else:
                    result["reviewer_verdict"] = "reviewer_error"

    # --- Write staging + update queue ---
    queue_status = "done" if tier_used != "failed" else "failed"
    await asyncio.to_thread(_write_staging, conn, batch_id, source_row_id, result)
    await asyncio.to_thread(_mark_queue_done, conn, row_id, queue_status)

    logger.info("Row %s: tier=%s queue_status=%s", source_row_id, tier_used, queue_status)
    return tier_used


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class EnrichmentDispatcher:
    def __init__(self, cfg: DispatcherConfig) -> None:
        self._cfg = cfg
        self._cost_tracker = CostCapTracker(cfg.cost_cap_ledger_path)

    async def run_batch(self) -> dict:
        """
        Pull up to batch_size pending rows and process them.
        Returns summary: {total, done, failed, cap_paused}.
        """
        cfg = self._cfg
        cap_paused = asyncio.Event()

        conn = await asyncio.to_thread(_db_connect, cfg.database_url)
        try:
            rows = await asyncio.to_thread(_fetch_pending_rows, conn, cfg.batch_size)
        except Exception as exc:
            conn.close()
            logger.error("Failed to fetch pending rows: %s", exc)
            raise

        if not rows:
            logger.info("No pending rows — batch complete (empty queue)")
            conn.close()
            return {"total": 0, "done": 0, "failed": 0, "cap_paused": False}

        batch_id = str(uuid.uuid4())
        logger.info("Batch %s: %d rows, concurrency=%d", batch_id, len(rows), cfg.concurrency)

        semaphore = asyncio.Semaphore(cfg.concurrency)
        totals = {"done": 0, "failed": 0}

        async def _bounded(row: dict) -> None:
            async with semaphore:
                tier = await _process_row(
                    row, batch_id, cfg, self._cost_tracker,
                    http_client, conn, cap_paused,
                )
                if tier != "failed":
                    totals["done"] += 1
                else:
                    totals["failed"] += 1

        async with httpx.AsyncClient(timeout=None) as http_client:
            await asyncio.gather(*[_bounded(r) for r in rows])

        conn.close()

        summary = {
            "total": len(rows),
            "done": totals["done"],
            "failed": totals["failed"],
            "cap_paused": cap_paused.is_set(),
        }
        logger.info("Batch %s complete: %s", batch_id, summary)
        return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Sage Surfaces enrichment batch dispatcher")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    cfg = DispatcherConfig.from_env()
    if args.batch_size is not None:
        cfg.batch_size = args.batch_size
    if args.concurrency is not None:
        cfg.concurrency = min(max(args.concurrency, 1), 4)

    summary = asyncio.run(EnrichmentDispatcher(cfg).run_batch())
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
