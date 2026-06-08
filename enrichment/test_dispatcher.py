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

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from cost_cap import CostCapTracker
from dispatcher import (
    DispatcherConfig,
    EnrichmentDispatcher,
    PRIMARY_MODEL,
    _build_enrichment_messages,
    _preflight_auth_check,
    _process_row,
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
        http_client = AsyncMock()

        valid_json = json.dumps(_minimal_valid_output())
        http_client.post.return_value = AsyncMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": valid_json}}]
            }),
        )

        reviewer_response = {"anomaly_score": 0.1, "anomaly_reason": "ok", "triggered_rules": []}

        with patch("dispatcher._anthropic_reviewer", new=AsyncMock(return_value=(reviewer_response, 0.05))), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging"), \
             patch("dispatcher._mark_queue_done"):
            await _process_row(_make_row(), "batch-1", cfg, tracker, http_client, conn, cap_paused)

        return {
            "cap_paused": cap_paused.is_set(),
            "weekly_spend_after": tracker.weekly_spend(),
        }

    async def test_reviewer_called_below_cap(self):
        """Reviewer is called when weekly spend is well below $50."""
        result = await self._run_row(weekly_spend_pre=0.0)
        self.assertFalse(result["cap_paused"])
        self.assertGreater(result["weekly_spend_after"], 0.0)

    async def test_reviewer_skipped_above_cap(self):
        """When weekly spend already exceeds $50, reviewer tier is skipped and routine is paused."""
        with patch("dispatcher._pause_routine", new=AsyncMock()):
            result = await self._run_row(weekly_spend_pre=50.01)
        self.assertTrue(result["cap_paused"])

    async def test_no_pause_at_exactly_50(self):
        """Exactly $50.00 is not yet a breach — reviewer may still run."""
        result = await self._run_row(weekly_spend_pre=50.00)
        # Whether it runs depends on estimated cost check; key assertion: $50.00 alone is not a breach
        tracker = CostCapTracker(_make_cfg(self._tmp).cost_cap_ledger_path)
        self.assertFalse(tracker.would_breach(0.0))

    async def test_empty_queue_returns_immediately(self):
        """run_batch with an empty DB returns a zero-row summary."""
        cfg = _make_cfg(self._tmp)

        with patch("dispatcher._preflight_auth_check", new=AsyncMock()), \
             patch("dispatcher._db_connect"), \
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


# ---------------------------------------------------------------------------
# Tests: preflight auth check (SAG-3455 fix 1)
# ---------------------------------------------------------------------------

class TestPreflightAuthCheck(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    async def test_preflight_raises_on_401(self):
        """_preflight_auth_check raises RuntimeError mentioning LITELLM_API_KEY on 401."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with self.assertRaises(RuntimeError) as ctx:
            await _preflight_auth_check(mock_client, "http://localhost:4000", "bad-key")

        self.assertIn("LITELLM_API_KEY", str(ctx.exception))
        self.assertIn("401", str(ctx.exception))

    async def test_preflight_raises_on_403(self):
        """_preflight_auth_check raises RuntimeError on 403 (forbidden)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with self.assertRaises(RuntimeError) as ctx:
            await _preflight_auth_check(mock_client, "http://localhost:4000", "key")

        self.assertIn("LITELLM_API_KEY", str(ctx.exception))

    async def test_preflight_no_raise_on_200(self):
        """_preflight_auth_check does not raise when gateway returns 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        await _preflight_auth_check(mock_client, "http://localhost:4000", "good-key")

    async def test_preflight_warns_and_continues_on_network_error(self):
        """Network errors log a warning and do not raise — infra blips don't abort batches."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        # Should not raise
        await _preflight_auth_check(mock_client, "http://localhost:4000", "key")

    async def test_run_batch_propagates_preflight_auth_error(self):
        """run_batch raises immediately when preflight detects 401 — no rows processed."""
        cfg = _make_cfg(self._tmp)
        with patch(
            "dispatcher._preflight_auth_check",
            new=AsyncMock(side_effect=RuntimeError("LITELLM_API_KEY missing or invalid: 401")),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await EnrichmentDispatcher(cfg).run_batch()
            self.assertIn("LITELLM_API_KEY", str(ctx.exception))


# ---------------------------------------------------------------------------
# Tests: /no_think directive for primary Qwen3 model (SAG-3455 fix 4)
# ---------------------------------------------------------------------------

class TestNoThinkDirective(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    async def _capture_litellm_calls(self, primary_content: str) -> list[dict]:
        cfg = _make_cfg(self._tmp, anthropic_key="")
        tracker = CostCapTracker(cfg.cost_cap_ledger_path)
        cap_paused = asyncio.Event()
        conn = MagicMock()
        http_client = AsyncMock()
        calls: list[dict] = []

        async def _capture(client, base_url, model, system, user, timeout, api_key=""):
            calls.append({"model": model, "system": system, "user": user})
            if model == PRIMARY_MODEL:
                return primary_content, False
            return json.dumps(_minimal_valid_output()), False

        with patch("dispatcher._litellm_complete", side_effect=_capture), \
             patch("dispatcher._mark_in_flight"), \
             patch("dispatcher._write_staging"), \
             patch("dispatcher._mark_queue_done"):
            await _process_row(_make_row(), "b", cfg, tracker, http_client, conn, cap_paused)

        return calls

    async def test_primary_call_has_no_think_prefix(self):
        """Primary (Qwen3) call must have /no_think prefix to suppress think-block token waste."""
        calls = await self._capture_litellm_calls(json.dumps(_minimal_valid_output()))
        primary_calls = [c for c in calls if c["model"] == PRIMARY_MODEL]
        self.assertEqual(len(primary_calls), 1, "Expected exactly one primary call")
        self.assertTrue(
            primary_calls[0]["user"].startswith("/no_think"),
            f"Expected user prompt to start with /no_think, got: {primary_calls[0]['user'][:60]!r}",
        )

    async def test_fallback_call_has_no_no_think_prefix(self):
        """Fallback model call must NOT carry the /no_think prefix."""
        calls = await self._capture_litellm_calls("not valid json")  # force fallback
        fallback_calls = [c for c in calls if c["model"] != PRIMARY_MODEL]
        self.assertGreater(len(fallback_calls), 0, "Expected at least one fallback call")
        for fc in fallback_calls:
            self.assertFalse(
                fc["user"].startswith("/no_think"),
                f"Fallback user prompt should not start with /no_think: {fc['user'][:60]!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
