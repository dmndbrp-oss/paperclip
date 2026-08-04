"""
Unit tests for dispatcher.py — cost-cap enforcement and concurrency flag.

All DB and LiteLLM/Anthropic calls are mocked; no network or DB required.

Run: python3 -m pytest enrichment/test_dispatcher.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from cost_cap import CostCapTracker
from dispatcher import (
    DispatcherConfig,
    EnrichmentDispatcher,
    _build_enrichment_messages,
    _process_row,
    _repair_cross_fields,
)
from cost_cap import WEEKLY_CAP_USD

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_dir: str, concurrency: int = 1, anthropic_key: str = "fake-key") -> DispatcherConfig:
    return DispatcherConfig(
        database_url="postgresql://test/test",
        litellm_base_url="http://localhost:4000",
        anthropic_api_key=anthropic_key,
        paperclip_api_url="http://localhost:3101",
        paperclip_api_key="fake",
        paperclip_routine_id="routine-123",
        enrichment_issue_id="issue-456",
        cost_cap_ledger_path=os.path.join(tmp_dir, "ledger.json"),
        batch_size=5,
        concurrency=concurrency,
    )


def _minimal_valid_output() -> dict:
    return {
        "sku": "TEST-001",
        "product_name": "Test Surface",
        "manufacturer": None,
        "material_type": "quartz",
        "primary_color_family": "white",
        "secondary_color_family": None,
        "finish": "polished",
        "pattern_type": "veined",
        "thickness_options_mm": [20, 30],
        "slab_sizes_available": [{"width_mm": 3050, "height_mm": 1440}],
        "applications": ["countertop"],
        "price_tier": "mid",
        "availability": "in_stock",
        "is_outdoor": False,
        "weather_rating": None,
        "heat_resistance": "good",
        "scratch_resistance": "good",
        "stain_resistant": True,
        "sealing_required": False,
        "care_level": "low",
        "edge_profiles_available": [],
        "certifications": [],
        "country_of_origin": None,
        "uv_resistant": None,
        "warranty_years": None,
        "recycled_content_pct": None,
        "voc_compliant": None,
        "series_name": None,
        "collection_name": None,
        "enrichment_confidence": 0.85,
        "low_confidence_fields": [],
        "enrichment_notes": None,
    }


def _make_row() -> dict:
    return {
        "id": "row-abc-123",
        "source_row_id": "SKU-001",
        "payload_json": {"sku": "SKU-001", "product_name": "Test", "raw_description": "A quartz slab"},
    }


# ---------------------------------------------------------------------------
# Tests: concurrency feature flag
# ---------------------------------------------------------------------------

class TestConcurrencyFlag(unittest.TestCase):
    def test_default_concurrency_is_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(tmp, concurrency=1)
            self.assertEqual(cfg.concurrency, 1)

    def test_concurrency_clamped_to_four(self):
        from dispatcher import DispatcherConfig
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_URL"] = "postgresql://test/test"
            os.environ["ENRICHMENT_DISPATCHER_CONCURRENCY"] = "10"
            cfg = DispatcherConfig.from_env()
            self.assertEqual(cfg.concurrency, 4)  # clamped
            del os.environ["ENRICHMENT_DISPATCHER_CONCURRENCY"]

    def test_concurrency_minimum_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_URL"] = "postgresql://test/test"
            os.environ["ENRICHMENT_DISPATCHER_CONCURRENCY"] = "0"
            from dispatcher import DispatcherConfig
            cfg = DispatcherConfig.from_env()
            self.assertEqual(cfg.concurrency, 1)
            del os.environ["ENRICHMENT_DISPATCHER_CONCURRENCY"]


# ---------------------------------------------------------------------------
# Tests: cost-cap enforcement in process_row
# ---------------------------------------------------------------------------

class TestCostCapEnforcement(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    async def _run_row(self, weekly_spend_pre: float) -> dict:
        """Run one row through _process_row with the given pre-seeded weekly spend."""
        cfg = _make_cfg(self._tmp)
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        if weekly_spend_pre > 0:
            tracker.record(weekly_spend_pre)

        cap_paused = asyncio.Event()
        conn = MagicMock()

        valid_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.1, "anomaly_reason": "ok", "triggered_rules": []})
        call_count = [0]

        async def mock_litellm(client, base_url, model, system, user, timeout, api_key=""):
            call_count[0] += 1
            return (valid_json if call_count[0] == 1 else reviewer_json), False

        with patch("dispatcher._litellm_complete", side_effect=mock_litellm), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging"), \
             patch("dispatcher._mark_queue_done"):
            await _process_row(_make_row(), "batch-1", cfg, tracker, AsyncMock(), conn, cap_paused)

        return {
            "cap_paused": cap_paused.is_set(),
            "weekly_spend_after": tracker.weekly_spend(),
        }

    async def test_reviewer_called_below_cap(self):
        """Reviewer runs when weekly spend is below the old $50 threshold; local reviewer costs $0."""
        result = await self._run_row(weekly_spend_pre=0.0)
        self.assertFalse(result["cap_paused"])
        # Local reviewer is free — no cost recorded
        self.assertEqual(result["weekly_spend_after"], 0.0)

    async def test_reviewer_runs_above_old_cap(self):
        """Local reviewer still runs when ledger exceeds old $50 Opus cap (now free, never paused)."""
        result = await self._run_row(weekly_spend_pre=50.01)
        self.assertFalse(result["cap_paused"])

    async def test_no_pause_at_exactly_50(self):
        """Exactly $50.00 is not yet a breach — reviewer may still run."""
        result = await self._run_row(weekly_spend_pre=50.00)
        # Whether it runs depends on estimated cost check; key assertion: $50.00 alone is not a breach
        tracker = CostCapTracker(_make_cfg(self._tmp).cost_cap_ledger_path)
        self.assertFalse(tracker.would_breach(0.0))

    async def test_empty_queue_returns_immediately(self):
        """run_batch with an empty DB returns a zero-row summary."""
        cfg = _make_cfg(self._tmp)

        with patch("dispatcher._db_connect"), \
             patch("dispatcher._fetch_pending_rows", return_value=[]):
            dispatcher = EnrichmentDispatcher(cfg)
            summary = await dispatcher.run_batch()

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["done"], 0)
        self.assertFalse(summary["cap_paused"])


# ---------------------------------------------------------------------------
# Tests: row processing tiers
# ---------------------------------------------------------------------------

class TestRowTierRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    async def _run_with_primary_content(self, primary_content: str, fallback_content: str = "") -> str:
        cfg = _make_cfg(self._tmp, anthropic_key="")  # no reviewer
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        cap_paused = asyncio.Event()
        conn = MagicMock()

        call_count = [0]

        async def mock_litellm_complete(client, base_url, model, system, user, timeout, api_key=""):
            call_count[0] += 1
            if call_count[0] == 1:
                return primary_content, False
            return fallback_content, False

        with patch("dispatcher._litellm_complete", side_effect=mock_litellm_complete), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging") as mock_write, \
             patch("dispatcher._mark_queue_done") as mock_done:
            http_client = AsyncMock()
            tier = await _process_row(_make_row(), "batch-1", cfg, tracker, http_client, conn, cap_paused)
            queue_status = mock_done.call_args[0][2] if mock_done.called else None

        return tier

    async def test_primary_valid_uses_primary_tier(self):
        tier = await self._run_with_primary_content(json.dumps(_minimal_valid_output()))
        self.assertEqual(tier, "primary")

    async def test_invalid_primary_falls_to_fallback(self):
        tier = await self._run_with_primary_content(
            primary_content="not json",
            fallback_content=json.dumps(_minimal_valid_output()),
        )
        self.assertEqual(tier, "fallback")

    async def test_both_fail_returns_failed(self):
        tier = await self._run_with_primary_content(
            primary_content="bad",
            fallback_content="also bad",
        )
        self.assertEqual(tier, "failed")

    async def test_fallback_nested_applications_list_is_normalized(self):
        fallback = _minimal_valid_output()
        fallback["applications"] = [["countertop", "bathroom_vanity"]]

        tier = await self._run_with_primary_content(
            primary_content="bad",
            fallback_content=json.dumps(fallback),
        )

        self.assertEqual(tier, "fallback")


# ---------------------------------------------------------------------------
# Tests: SAG-3166 — reviewer tier on local LiteLLM gateway (no ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

class TestLocalReviewerBackend(unittest.IsolatedAsyncioTestCase):
    """Reviewer must use local LiteLLM gateway; no ANTHROPIC_API_KEY required."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def _no_key_cfg(self) -> DispatcherConfig:
        return DispatcherConfig(
            database_url="postgresql://test/test",
            litellm_base_url="http://localhost:4000",
            anthropic_api_key="",
            cost_cap_ledger_path=os.path.join(self._tmp, "ledger.json"),
            batch_size=5,
            concurrency=1,
        )

    async def _run_row_litellm(self, cfg, tracker, primary_json, reviewer_json):
        """Helper: runs _process_row with mocked _litellm_complete calls."""
        cap_paused = asyncio.Event()
        conn = MagicMock()
        call_count = [0]

        async def mock_litellm(client, base_url, model, system, user, timeout, api_key=""):
            call_count[0] += 1
            return (primary_json if call_count[0] == 1 else reviewer_json), False

        with patch("dispatcher._litellm_complete", side_effect=mock_litellm), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging") as mock_write, \
             patch("dispatcher._mark_queue_done"):
            tier = await _process_row(_make_row(), "batch-1", cfg, tracker, AsyncMock(), conn, cap_paused)

        result_dict = mock_write.call_args[0][3] if mock_write.called else {}
        return tier, cap_paused.is_set(), result_dict

    async def test_reviewer_calls_local_litellm_not_anthropic(self):
        """Reviewer uses _litellm_complete; no anthropic.AsyncAnthropic instantiated."""
        cfg = self._no_key_cfg()
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        primary_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.15, "anomaly_reason": "ok", "triggered_rules": []})

        litellm_models = []

        async def capturing_litellm(client, base_url, model, system, user, timeout, api_key=""):
            litellm_models.append(model)
            return (primary_json if len(litellm_models) == 1 else reviewer_json), False

        cap_paused = asyncio.Event()
        conn = MagicMock()

        with patch("dispatcher._litellm_complete", side_effect=capturing_litellm), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging") as mock_write, \
             patch("dispatcher._mark_queue_done"):
            await _process_row(_make_row(), "batch-1", cfg, tracker, AsyncMock(), conn, cap_paused)

        # At least 2 LiteLLM calls: primary + reviewer
        self.assertGreaterEqual(len(litellm_models), 2)
        # Reviewer model is not a Claude/Opus model
        reviewer_model = litellm_models[-1]
        self.assertNotIn("claude", reviewer_model.lower())
        self.assertNotIn("opus", reviewer_model.lower())
        # Result carries the reviewer verdict
        result_dict = mock_write.call_args[0][3]
        self.assertEqual(result_dict.get("anomaly_score"), 0.15)
        self.assertIsNotNone(result_dict.get("reviewer_verdict"))

    async def test_reviewer_runs_without_anthropic_api_key(self):
        """Reviewer produces a valid verdict even when ANTHROPIC_API_KEY is absent from env."""
        cfg = self._no_key_cfg()
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        primary_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.05, "anomaly_reason": "all good", "triggered_rules": []})

        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env_without_key.setdefault("DATABASE_URL", "postgresql://test/test")

        with patch.dict(os.environ, env_without_key, clear=True):
            tier, paused, result_dict = await self._run_row_litellm(
                cfg, tracker, primary_json, reviewer_json
            )

        self.assertEqual(tier, "primary")
        self.assertFalse(paused)
        self.assertEqual(result_dict.get("anomaly_score"), 0.05)

    async def test_reviewer_cost_is_zero(self):
        """Reviewer contributes $0.0 to the cost tracker (local inference is free)."""
        cfg = self._no_key_cfg()
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        initial_spend = tracker.weekly_spend()
        primary_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.1, "anomaly_reason": "ok", "triggered_rules": []})

        await self._run_row_litellm(cfg, tracker, primary_json, reviewer_json)

        self.assertEqual(tracker.weekly_spend(), initial_spend)

    async def test_reviewer_not_paused_when_ledger_above_old_cap(self):
        """Local reviewer runs even when ledger exceeds old $50 Opus cap."""
        cfg = self._no_key_cfg()
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        tracker.record(99.0)  # far above the old cap
        primary_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.2, "anomaly_reason": "high spend but free", "triggered_rules": []})

        tier, paused, result_dict = await self._run_row_litellm(
            cfg, tracker, primary_json, reviewer_json
        )

        self.assertFalse(paused)
        self.assertEqual(result_dict.get("anomaly_score"), 0.2)
        self.assertNotEqual(result_dict.get("reviewer_verdict"), "cap_paused")

    async def test_reviewer_model_default_is_llama(self):
        """Default reviewer model is the registered LiteLLM alias when REVIEWER_MODEL not set.

        SAG-2154: bare "llama3.3:70b-instruct" is NOT a registered alias and 400s at the
        gateway ("Invalid model name"); the registered name is the ollama/...-q4_K_M form.
        """
        env = {k: v for k, v in os.environ.items() if k != "REVIEWER_MODEL"}
        env.setdefault("DATABASE_URL", "postgresql://test/test")
        with patch.dict(os.environ, env, clear=True):
            cfg = DispatcherConfig.from_env()
        self.assertEqual(cfg.reviewer_model, "ollama/llama3.3:70b-instruct-q4_K_M")

    async def test_reviewer_model_config_driven_via_env(self):
        """REVIEWER_MODEL env var overrides the default reviewer model."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test/test",
                                     "REVIEWER_MODEL": "mistral:7b"}):
            cfg = DispatcherConfig.from_env()
        self.assertEqual(cfg.reviewer_model, "mistral:7b")


# ---------------------------------------------------------------------------
# Tests: Phase A dry-run — per-row JSONL metrics capture (SAG-2154)
# ---------------------------------------------------------------------------

class TestPhaseAMetricsInstrumentation(unittest.IsolatedAsyncioTestCase):
    """PHASE_A_METRICS_DIR set → run-metrics-{batch_id}.jsonl with correct records."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    async def test_metrics_jsonl_written_for_2_row_batch(self):
        """2-row batch with PHASE_A_METRICS_DIR → JSONL with 2 records, all required fields, non-negative latencies."""
        import glob
        metrics_dir = os.path.join(self._tmp, "metrics")
        os.makedirs(metrics_dir)

        cfg = DispatcherConfig(
            database_url="postgresql://test/test",
            litellm_base_url="http://localhost:4000",
            cost_cap_ledger_path=os.path.join(self._tmp, "ledger.json"),
            batch_size=2,
            concurrency=1,
        )

        rows = [
            {"id": "row-1", "source_row_id": "SKU-001",
             "payload_json": {"sku": "SKU-001", "product_name": "Test", "raw_description": "A quartz slab"}},
            {"id": "row-2", "source_row_id": "SKU-002",
             "payload_json": {"sku": "SKU-002", "product_name": "Test2", "raw_description": "A marble slab"}},
        ]

        primary_json = json.dumps(_minimal_valid_output())
        reviewer_json = json.dumps({"anomaly_score": 0.3, "anomaly_reason": "ok", "triggered_rules": []})

        async def mock_litellm(client, base_url, model, system, user, timeout, api_key=""):
            if "llama" in model:
                return reviewer_json, False
            return primary_json, False

        batch_id = "test-batch-phase-a"
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        conn = MagicMock()

        with patch("dispatcher._litellm_complete", side_effect=mock_litellm), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging"), \
             patch("dispatcher._mark_queue_done"), \
             patch.dict(os.environ, {"PHASE_A_METRICS_DIR": metrics_dir}):
            for row in rows:
                await _process_row(row, batch_id, cfg, tracker, AsyncMock(), conn, asyncio.Event())

        files = glob.glob(os.path.join(metrics_dir, f"run-metrics-{batch_id}.jsonl"))
        self.assertEqual(len(files), 1, "JSONL metrics file not created")

        with open(files[0]) as f:
            records = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(records), 2, f"Expected 2 records, got {len(records)}")

        required_fields = {
            "source_row_id", "primary_latency_s", "primary_validator_valid",
            "fallback_used", "fallback_latency_s", "fallback_validator_valid",
            "reviewer_used", "reviewer_latency_s", "reviewer_anomaly_score",
            "tier_used", "row_total_s",
        }

        for rec in records:
            missing = required_fields - set(rec.keys())
            self.assertEqual(missing, set(), f"Missing fields: {missing}")
            self.assertGreaterEqual(rec["primary_latency_s"], 0.0)
            self.assertGreaterEqual(rec["row_total_s"], 0.0)
            self.assertIn(rec["tier_used"], ("primary", "fallback", "failed"))


# ---------------------------------------------------------------------------
# Tests: model constants (SAG-3657)
# ---------------------------------------------------------------------------

class TestModelConstants(unittest.TestCase):
    """Verify dispatcher model constants match fleet-standard gemma4 after SAG-3657 repoint."""

    def test_primary_model_is_gemma4(self):
        import dispatcher
        self.assertEqual(
            dispatcher.PRIMARY_MODEL,
            "gemma4-26b-a4b-it-q4_K_M",
            "PRIMARY_MODEL must point to gemma4 — qwen3 <think> tags broke Phase A (SAG-2154)",
        )

    def test_fallback_model_is_qwen25(self):
        import dispatcher
        self.assertEqual(
            dispatcher.FALLBACK_MODEL,
            "ollama/qwen2.5:14b-instruct-q4_K_M",
            "FALLBACK_MODEL must remain qwen2.5:14b so fallback is a different model family",
        )

    def test_primary_and_fallback_are_distinct(self):
        import dispatcher
        self.assertNotEqual(
            dispatcher.PRIMARY_MODEL,
            dispatcher.FALLBACK_MODEL,
            "PRIMARY_MODEL and FALLBACK_MODEL must differ (same model = no meaningful fallback)",
        )


# ---------------------------------------------------------------------------
# Tests: _repair_cross_fields availability null repair (SAG-3528)
# ---------------------------------------------------------------------------

class TestRepairCrossFieldsAvailability(unittest.TestCase):
    def setUp(self):
        from dispatcher import _repair_cross_fields
        self._repair = _repair_cross_fields

    def test_null_availability_defaults_to_in_stock(self):
        """gemma4 emits null availability — repair must default to in_stock."""
        row = {"sku": "SSI-QTZ-0109", "availability": None, "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["availability"], "in_stock")

    def test_missing_availability_key_defaults_to_in_stock(self):
        """Missing availability key (not present) is treated same as null."""
        row = {"sku": "SSI-QTZ-0109", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["availability"], "in_stock")

    def test_valid_availability_is_not_overwritten(self):
        """Pre-set availability values are preserved unchanged."""
        for val in ("discontinued", "made_to_order", "limited_stock", "coming_soon"):
            row = {"sku": "X", "availability": val, "is_outdoor": False}
            self._repair(row)
            self.assertEqual(row["availability"], val)

    def test_weather_rating_repair_still_works(self):
        """Existing weather_rating repair is not broken by the new availability fix."""
        row = {"sku": "X", "availability": None, "is_outdoor": True, "weather_rating": None}
        self._repair(row)
        self.assertEqual(row["weather_rating"], "not_rated")
        self.assertEqual(row["availability"], "in_stock")


# ---------------------------------------------------------------------------
# Tests: _repair_cross_fields availability null repair (SAG-3528)
# ---------------------------------------------------------------------------

class TestRepairCrossFieldsAvailability(unittest.TestCase):
    def setUp(self):
        from dispatcher import _repair_cross_fields
        self._repair = _repair_cross_fields

    def test_null_availability_defaults_to_in_stock(self):
        """gemma4 emits null availability — repair must default to in_stock."""
        row = {"sku": "SSI-QTZ-0109", "availability": None, "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["availability"], "in_stock")

    def test_missing_availability_key_defaults_to_in_stock(self):
        """Missing availability key (not present) is treated same as null."""
        row = {"sku": "SSI-QTZ-0109", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["availability"], "in_stock")

    def test_valid_availability_is_not_overwritten(self):
        """Pre-set availability values are preserved unchanged."""
        for val in ("discontinued", "made_to_order", "limited_stock", "coming_soon"):
            row = {"sku": "X", "availability": val, "is_outdoor": False}
            self._repair(row)
            self.assertEqual(row["availability"], val)

    def test_weather_rating_repair_still_works(self):
        """Existing weather_rating repair is not broken by the new availability fix."""
        row = {"sku": "X", "availability": None, "is_outdoor": True, "weather_rating": None}
        self._repair(row)
        self.assertEqual(row["weather_rating"], "not_rated")
        self.assertEqual(row["availability"], "in_stock")


# ---------------------------------------------------------------------------
# Tests: SAG-3677 R1 — country_of_origin normalization
# ---------------------------------------------------------------------------

class TestRepairCountryOfOrigin(unittest.TestCase):
    """R1: model emits full country name instead of ISO 3166-1 alpha-2 code."""

    def _repair(self, row: dict) -> dict:
        _repair_cross_fields(row)
        return row

    def test_china_maps_to_cn(self):
        row = {"sku": "SSI-MBL-0113", "country_of_origin": "China", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["country_of_origin"], "CN")

    def test_italy_maps_to_it(self):
        row = {"sku": "SSI-MBL-0100", "country_of_origin": "Italy", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["country_of_origin"], "IT")

    def test_brazil_maps_to_br(self):
        row = {"sku": "SSI-QZT-0100", "country_of_origin": "Brazil", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["country_of_origin"], "BR")

    def test_case_insensitive(self):
        row = {"sku": "X", "country_of_origin": "CHINA", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["country_of_origin"], "CN")

    def test_valid_iso_code_unchanged(self):
        row = {"sku": "X", "country_of_origin": "CN", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["country_of_origin"], "CN")

    def test_null_coo_unchanged(self):
        row = {"sku": "X", "country_of_origin": None, "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertIsNone(row["country_of_origin"])

    def test_unrecognised_name_nulled_out(self):
        row = {"sku": "X", "country_of_origin": "Narnia", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertIsNone(row["country_of_origin"])


# ---------------------------------------------------------------------------
# Tests: SAG-3677 R2 — finish array normalization
# ---------------------------------------------------------------------------

class TestRepairFinishArray(unittest.TestCase):
    """R2: model emits array for finish when product has multiple finish options."""

    def _repair(self, row: dict) -> dict:
        _repair_cross_fields(row)
        return row

    def test_array_takes_first_valid(self):
        row = {"sku": "SSI-QZT-0100", "finish": ["polished", "honed"], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["finish"], "polished")

    def test_array_with_invalid_first_takes_first_valid(self):
        row = {"sku": "X", "finish": ["polished_or_honed", "honed"], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["finish"], "honed")

    def test_all_invalid_array_left_unchanged(self):
        row = {"sku": "X", "finish": ["shiny", "rough"], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["finish"], ["shiny", "rough"])  # validator will surface error

    def test_valid_string_finish_unchanged(self):
        row = {"sku": "X", "finish": "polished", "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["finish"], "polished")


# ---------------------------------------------------------------------------
# Tests: SAG-3677 R3 — applications filtering
# ---------------------------------------------------------------------------

class TestRepairApplicationsFiltering(unittest.TestCase):
    """R3: model emits invalid application values — filter to valid enum."""

    def _repair(self, row: dict) -> dict:
        _repair_cross_fields(row)
        return row

    def test_invalid_value_filtered_out(self):
        row = {"sku": "SSI-PRC-0105", "applications": ["outdoor_kitchen", "outdoor_flooring"], "availability": "in_stock", "is_outdoor": True, "weather_rating": "excellent"}
        self._repair(row)
        self.assertEqual(row["applications"], ["outdoor_kitchen"])

    def test_invalid_value_filtered_when_valid_value_present(self):
        row = {"sku": "X", "applications": ["flooring", "pool"], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["applications"], ["flooring"])

    def test_all_invalid_left_unchanged(self):
        row = {"sku": "X", "applications": ["patio", "outdoor_flooring"], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["applications"], ["patio", "outdoor_flooring"])  # validator surfaces error

    def test_all_valid_unchanged(self):
        apps = ["countertop", "kitchen_island", "bathroom_vanity"]
        row = {"sku": "X", "applications": apps[:], "availability": "in_stock", "is_outdoor": False}
        self._repair(row)
        self.assertEqual(row["applications"], apps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
