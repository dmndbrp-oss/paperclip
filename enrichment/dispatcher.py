"""
Enrichment batch dispatcher — SAG-2160 sub-deliverable 2b.

Pull pending rows from enrichment_staging.enrichment_queue, run:
  primary (Qwen 2.5 14B via LiteLLM) → fallback (Llama 3.3 70B via LiteLLM)
  → reviewer (local model via LiteLLM gateway)
Write results to enrichment_staging.enrichment_staging.

Entry point:
  python -m enrichment.dispatcher [--batch-size N]
  or imported: asyncio.run(EnrichmentDispatcher(cfg).run_batch())

Environment variables:
  DATABASE_URL          postgres://user:pass@host/db  (required)
  LITELLM_BASE_URL      default http://localhost:4000
  LITELLM_API_KEY       bearer token for LiteLLM gateway (required when master_key set)
  REVIEWER_MODEL        reviewer model alias in LiteLLM (default ollama/llama3.3:70b-instruct-q4_K_M)
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
import re
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
import headroom_compress  # SAG-3060: token-compression plugin scaffold

logger = logging.getLogger(__name__)

# Opus pricing (per 1K tokens) — matches dispatcher/src/router/costCap.ts
OPUS_INPUT_PER_1K = 0.015
OPUS_OUTPUT_PER_1K = 0.075

# SAG-3657: repoint primary to fleet-standard gemma4 (SAG-3380); qwen3 <think> tags
# broke json.loads at 1% schema_valid_rate in Phase A dry-run (SAG-2154 NO-GO).
# gemma4 is warm, resident, and LiteLLM gateway confirmed resolving (SAG-3656).
PRIMARY_MODEL = "gemma4-26b-a4b-it-q4_K_M"            # LiteLLM alias → ollama/gemma4:26b-a4b-it-q4_K_M
FALLBACK_MODEL = "ollama/qwen2.5:14b-instruct-q4_K_M"  # different model family keeps fallback meaningful
REVIEWER_MODEL = os.environ.get("REVIEWER_MODEL", "ollama/llama3.3:70b-instruct-q4_K_M")  # SAG-3166/SAG-2154: registered LiteLLM alias (bare "llama3.3:70b-instruct" 400s — not registered)

# Timeouts must absorb APU queue-wait behind in-flight Paperclip agent inferences
# (can be 60-120s on a busy APU) plus model-load + inference time.
PRIMARY_TIMEOUT = 600.0   # 10 min — gemma4 always warm; absorbs queue-wait
FALLBACK_TIMEOUT = 300.0  # 5 min — qwen2.5:14b; budget includes GPU model-swap overhead
REVIEWER_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class DispatcherConfig:
    database_url: str
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    anthropic_api_key: str = ""  # kept for backward compat; no longer used by reviewer tier
    reviewer_model: str = "ollama/llama3.3:70b-instruct-q4_K_M"  # SAG-3166/SAG-2154: registered LiteLLM alias
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
            litellm_api_key=os.environ.get("LITELLM_API_KEY", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            reviewer_model=os.environ.get("REVIEWER_MODEL", "ollama/llama3.3:70b-instruct-q4_K_M"),
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
        "You are a product data enrichment specialist for Sage Surfaces, a countertop and "
        "surface materials distributor. Your job is to analyze product information and produce "
        "a structured JSON object describing a surface material's attributes.\n\n"
        "Rules:\n"
        "1. Output ONLY a valid JSON object. No explanation, no markdown, no prose.\n"
        "2. Every required field must be present. Optional fields may be null if genuinely unknown.\n"
        "3. Use only the exact enum values listed in the schema. Do not invent new values.\n"
        "4. If is_outdoor is true, weather_rating must NOT be null.\n"
        "5. Set enrichment_confidence to a float 0.0-1.0 reflecting your overall certainty.\n"
        "6. List any fields you are uncertain about in low_confidence_fields.\n"
        "7. Keep enrichment_notes under 200 characters if used.\n\n"
        "Schema reference (required fields: sku, product_name, material_type, primary_color_family, "
        "finish, applications, price_tier, availability, is_outdoor, enrichment_confidence):\n"
        "- material_type: quartz | granite | marble | quartzite | porcelain | sintered_stone | "
        "laminate | solid_surface | recycled_glass | terrazzo | soapstone | slate | travertine | "
        "limestone | onyx | other\n"
        "- primary_color_family: white | off_white | gray | black | beige | cream | brown | taupe | "
        "blue | green | red | pink | gold | multicolor\n"
        "- finish: polished | honed | matte | leathered | brushed | sandblasted | flamed | "
        "bush_hammered | satin\n"
        "- pattern_type: solid | veined | flecked | marbled | speckled | linear | geometric | "
        "organic | null\n"
        "- applications (array): countertop | kitchen_island | bathroom_vanity | flooring | "
        "wall_cladding | shower_surround | backsplash | fireplace_surround | outdoor_kitchen | "
        "table_top | commercial\n"
        "- weather_rating: excellent | good | fair | not_rated | null\n"
        "- heat_resistance / scratch_resistance: excellent | good | moderate | low | null\n"
        "- care_level: low | moderate | high | null\n"
        "- price_tier: budget | mid | premium | luxury\n"
        "- availability: in_stock | made_to_order | limited_stock | discontinued | coming_soon\n"
        "- edge_profiles_available: eased | beveled | bullnose | ogee | waterfall | mitered | "
        "dupont | chiseled\n"
        "- certifications: NSF_51 | GREENGUARD_Gold | LEED_eligible | ISO_14001 | "
        "recycled_content_certified\n"
        "- country_of_origin: ISO 3166-1 alpha-2 code (e.g. US, IT, IN, BR) or null"
    )
    # Build a readable product summary from whatever fields are in the payload
    lines = ["Enrich the following surface product. Return only the JSON object.", "", "Product input:"]
    for key, value in payload.items():
        if value is not None:
            lines.append(f"  {key}: {value}")
    user = "\n".join(lines) + "\n\nOutput the enriched JSON now:"
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
    api_key: str = "",
) -> tuple[str | None, bool]:
    """
    Call LiteLLM gateway. Returns (content, timed_out).
    Returns (None, False) on non-timeout errors.
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
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
        # Strip qwen3 thinking tags — qwen3:30b-a3b emits <think>…</think> before JSON
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip markdown JSON fences if present
        content = re.sub(r"^```(?:json)?\s*", "", content).rstrip("`").strip()
        return content, False
    except httpx.TimeoutException:
        return None, True
    except Exception as exc:
        logger.warning("LiteLLM call failed for model=%s: %s", model, exc)
        return None, False


async def _local_reviewer(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str,
    payload: dict,
    enriched: dict,
) -> tuple[dict | None, float]:
    """
    Call local LiteLLM gateway for anomaly review. Returns (verdict_dict, 0.0).
    Cost is always 0.0 — local inference is free. On error returns (None, 0.0).
    """
    system, user = _build_reviewer_messages(payload, enriched)
    content, timed_out = await _litellm_complete(
        client, base_url, model, system, user, REVIEWER_TIMEOUT, api_key=api_key
    )
    if content is None:
        logger.warning(
            "Local reviewer %s (model=%s)",
            "timed out" if timed_out else "returned no content",
            model,
        )
        return None, 0.0
    try:
        verdict = json.loads(content)
    except json.JSONDecodeError:
        verdict = {"anomaly_score": None, "anomaly_reason": content[:300], "triggered_rules": []}
    return verdict, 0.0


# Thin alias kept so any external import of the old name continues to resolve (SAG-3166)
_anthropic_reviewer = _local_reviewer


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
# Cross-field repair
# ---------------------------------------------------------------------------

# SAG-3677: country_of_origin — map common full country names to ISO 3166-1 alpha-2 codes.
# Model occasionally emits the full name (e.g. "China") instead of the required 2-letter code.
_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "china": "CN",
    "italy": "IT",
    "brazil": "BR",
    "india": "IN",
    "spain": "ES",
    "turkey": "TR",
    "portugal": "PT",
    "greece": "GR",
    "france": "FR",
    "united states": "US",
    "usa": "US",
    "mexico": "MX",
    "canada": "CA",
    "germany": "DE",
    "norway": "NO",
    "sweden": "SE",
    "australia": "AU",
    "iran": "IR",
    "taiwan": "TW",
}


def _repair_cross_fields(parsed: dict) -> None:
    """Enforce schema cross-field invariants that models frequently miss.
    Mutates parsed in-place; logs any repairs made.
    SAG-3677: extended with constraint-class repairs (R1-R3).
    """
    # SAG-3663: model responses can contain nested list-valued fields. Flatten
    # them before validation so downstream set-based checks do not raise a
    # TypeError and incorrectly discard an otherwise recoverable row.
    for field_name in (
        "applications",
        "edge_profiles_available",
        "certifications",
        "thickness_options_mm",
    ):
        value = parsed.get(field_name)
        if isinstance(value, list) and any(isinstance(item, list) for item in value):
            flattened = []
            for item in value:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            parsed[field_name] = flattened
            logger.info(
                "Cross-field repair: flattened nested %s list sku=%s",
                field_name,
                parsed.get("sku"),
            )

    # Rule: is_outdoor=true requires weather_rating != null
    if parsed.get("is_outdoor") and not parsed.get("weather_rating"):
        parsed["weather_rating"] = "not_rated"
        logger.info("Cross-field repair: set weather_rating=not_rated for is_outdoor=true sku=%s", parsed.get("sku"))

    # SAG-3528: gemma4 emits null for availability when payload has no explicit stock info.
    # availability is a required non-null field. Default to in_stock — catalog items are
    # presumed active unless explicitly marked otherwise.
    if parsed.get("availability") is None:
        parsed["availability"] = "in_stock"
        logger.info("Cross-field repair: set availability=in_stock (was null) sku=%s", parsed.get("sku"))

    # SAG-3677 R1: country_of_origin — model sometimes emits full name ("China") instead of ISO
    # alpha-2 ("CN").  Map known names; null-out unrecognised non-conforming strings (the field
    # is optional, so null is a safe fallback that does not fail validation).
    coo = parsed.get("country_of_origin")
    if coo is not None and isinstance(coo, str) and not re.match(r"^[A-Z]{2}$", coo):
        mapped = _COUNTRY_NAME_TO_ISO.get(coo.lower().strip())
        parsed["country_of_origin"] = mapped  # valid ISO code, or None for unknown
        logger.info(
            "Cross-field repair R1: country_of_origin %r → %r sku=%s",
            coo, mapped, parsed.get("sku"),
        )

    # SAG-3677 R2: finish — for "polished or honed" descriptions the model occasionally emits an
    # array (["polished","honed"]).  Take the first valid enum value; leave unchanged if none
    # match so the validator surfaces the real error.
    finish = parsed.get("finish")
    if isinstance(finish, list):
        from validator import FINISHES as _FINISHES  # local import avoids module-level cycle risk
        valid_finishes = [f for f in finish if isinstance(f, str) and f in _FINISHES]
        if valid_finishes:
            parsed["finish"] = valid_finishes[0]
            logger.info(
                "Cross-field repair R2: finish array %r → %r sku=%s",
                finish, parsed["finish"], parsed.get("sku"),
            )

    # SAG-3677 R3: applications — model sometimes includes invalid values (e.g. "pool" instead of
    # "pool_deck", "outdoor_flooring" instead of "flooring").  Filter to valid enum values only;
    # preserve the original list if filtering would leave it empty so the validator reports the
    # real error rather than surfacing an empty-array error.
    apps = parsed.get("applications")
    if isinstance(apps, list):
        from validator import APPLICATIONS as _APPLICATIONS  # local import
        valid_apps = [a for a in apps if isinstance(a, str) and a in _APPLICATIONS]
        if valid_apps and len(valid_apps) < len(apps):
            parsed["applications"] = valid_apps
            logger.info(
                "Cross-field repair R3: applications filtered %r → %r sku=%s",
                apps, valid_apps, parsed.get("sku"),
            )


# ---------------------------------------------------------------------------
# Phase A metrics JSONL writer (SAG-2154)
# ---------------------------------------------------------------------------

def _write_phase_a_metrics(metrics: dict, batch_id: str) -> None:
    """Append one JSONL record to PHASE_A_METRICS_DIR/run-metrics-{batch_id}.jsonl.
    No-op when PHASE_A_METRICS_DIR is unset or the directory is not writable.
    The production cron path is unaffected — this is a pure additive side channel.
    """
    metrics_dir = os.environ.get("PHASE_A_METRICS_DIR", "")
    if not metrics_dir or not os.path.isdir(metrics_dir) or not os.access(metrics_dir, os.W_OK):
        return
    jsonl_path = os.path.join(metrics_dir, f"run-metrics-{batch_id}.jsonl")
    try:
        with open(jsonl_path, "a") as fh:
            fh.write(json.dumps(metrics) + "\n")
    except Exception as exc:
        logger.warning("Phase A metrics write failed for batch %s: %s", batch_id, exc)

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

    row_t0 = time.monotonic()
    metrics: dict[str, Any] = {
        "source_row_id": source_row_id,
        "primary_latency_s": None,
        "primary_validator_valid": None,
        "fallback_used": False,
        "fallback_latency_s": None,
        "fallback_validator_valid": None,
        "reviewer_used": False,
        "reviewer_latency_s": None,
        "reviewer_anomaly_score": None,
        "tier_used": None,
        "row_total_s": None,
    }

    system, user = _build_enrichment_messages(payload)
    tier_used = "failed"

    # --- SAG-3060: compress user prompt before LLM calls ---
    cr = headroom_compress.compress(user)
    if cr.original_len > 0 and cr.compressed_len < cr.original_len:
        logger.info(
            "headroom compress sku=%s orig=%d comp=%d ratio=%.1f%%",
            source_row_id, cr.original_len, cr.compressed_len,
            (1 - cr.compressed_len / cr.original_len) * 100,
        )
        user = cr.compressed

    # --- Primary tier ---
    t_prim = time.monotonic()
    content, timed_out = await _litellm_complete(
        http_client, cfg.litellm_base_url, PRIMARY_MODEL, system, user, PRIMARY_TIMEOUT,
        api_key=cfg.litellm_api_key,
    )
    metrics["primary_latency_s"] = time.monotonic() - t_prim
    if content:
        try:
            parsed = json.loads(content)
            _repair_cross_fields(parsed)
            validation = validate(parsed)
            result["primary_output"] = parsed
            result["validator_result"] = validation
            metrics["primary_validator_valid"] = bool(validation.get("valid"))
            if validation.get("valid"):
                tier_used = "primary"
            else:
                logger.info("Primary schema invalid for %s: %s", source_row_id, validation.get("errors"))
        except json.JSONDecodeError as exc:
            logger.warning("Primary JSON parse error for %s: %s", source_row_id, exc)
        except Exception as exc:
            logger.warning("Primary validate error for %s: %s", source_row_id, exc)
    else:
        logger.warning("Primary %s for %s (model=%s)", "timed out" if timed_out else "returned no content", source_row_id, PRIMARY_MODEL)

    # --- Fallback tier (if primary failed schema validation) ---
    if tier_used == "failed":
        metrics["fallback_used"] = True
        t_fall = time.monotonic()
        content, timed_out = await _litellm_complete(
            http_client, cfg.litellm_base_url, FALLBACK_MODEL, system, user, FALLBACK_TIMEOUT,
            api_key=cfg.litellm_api_key,
        )
        metrics["fallback_latency_s"] = time.monotonic() - t_fall
        if content:
            try:
                parsed = json.loads(content)
                _repair_cross_fields(parsed)
                validation = validate(parsed)
                result["fallback_output"] = parsed
                result["validator_result"] = validation
                metrics["fallback_validator_valid"] = bool(validation.get("valid"))
                if validation.get("valid"):
                    tier_used = "fallback"
                else:
                    logger.warning("Fallback schema invalid for %s: %s", source_row_id, validation.get("errors"))
            except json.JSONDecodeError as exc:
                logger.warning("Fallback JSON parse error for %s: %s", source_row_id, exc)
            except Exception as exc:
                logger.warning("Fallback validate error for %s: %s", source_row_id, exc)
        else:
            logger.warning("Fallback %s for %s (model=%s)", "timed out" if timed_out else "returned no content", source_row_id, FALLBACK_MODEL)

    # --- Reviewer tier (local LiteLLM gateway, always free — SAG-3166) ---
    if tier_used in ("primary", "fallback"):
        enriched = result.get("primary_output") or result.get("fallback_output")
        metrics["reviewer_used"] = True
        t_rev = time.monotonic()
        verdict, _ = await _local_reviewer(
            http_client, cfg.litellm_base_url, cfg.reviewer_model,
            cfg.litellm_api_key, payload, enriched or {}
        )
        metrics["reviewer_latency_s"] = time.monotonic() - t_rev
        if verdict is not None:
            result["anomaly_score"] = verdict.get("anomaly_score")
            metrics["reviewer_anomaly_score"] = verdict.get("anomaly_score")
            result["reviewer_verdict"] = json.dumps(verdict)
        else:
            result["reviewer_verdict"] = "reviewer_error"

    # --- Write staging + update queue ---
    queue_status = "done" if tier_used != "failed" else "failed"
    await asyncio.to_thread(_write_staging, conn, batch_id, source_row_id, result)
    await asyncio.to_thread(_mark_queue_done, conn, row_id, queue_status)

    # --- Phase A metrics JSONL (SAG-2154): write only when PHASE_A_METRICS_DIR is set ---
    metrics["tier_used"] = tier_used
    metrics["row_total_s"] = time.monotonic() - row_t0
    _write_phase_a_metrics(metrics, batch_id)

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

        # SAG-3060: verify headroom-compress health on startup
        health = headroom_compress.check_health()
        if health["headroomAvailable"]:
            logger.info(
                "headroom-compress ready: version=%s telemetry_off=%s library_mode_only=%s",
                health.get("version"),
                health.get("telemetryEnforced"),
                health.get("libraryModeOnly"),
            )
        else:
            logger.warning(
                "headroom-compress unavailable — token compression disabled "
                "(install: pip install 'headroom-ai>=0.23.0' with Python 3.12/3.13)"
            )

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
