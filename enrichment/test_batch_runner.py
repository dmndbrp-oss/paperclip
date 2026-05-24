"""
Unit tests for batch_runner.py — SAG-2184.

Verifies: summary comment format, Paperclip API calls, exit codes.
No DB, LiteLLM, or Anthropic calls.

Run: python3 -m pytest enrichment/test_batch_runner.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from batch_runner import _build_comment, run


# ---------------------------------------------------------------------------
# _build_comment
# ---------------------------------------------------------------------------

class TestBuildComment(unittest.TestCase):
    _started = datetime(2026, 5, 24, 2, 0, 0, tzinfo=timezone.utc)
    _finished = datetime(2026, 5, 24, 2, 0, 30, tzinfo=timezone.utc)

    def _comment(self, **summary_overrides):
        base = {"total": 10, "done": 10, "failed": 0, "cap_paused": False}
        base.update(summary_overrides)
        return _build_comment(base, self._started, self._finished)

    def test_success_message_present(self):
        c = self._comment()
        self.assertIn("10/10 rows enriched successfully", c)

    def test_empty_queue_message(self):
        c = self._comment(total=0, done=0, failed=0)
        self.assertIn("queue empty", c)

    def test_partial_failure_message(self):
        c = self._comment(total=10, done=8, failed=2)
        self.assertIn("8/10 enriched", c)
        self.assertIn("2 failed", c)

    def test_total_failure_message(self):
        c = self._comment(total=5, done=0, failed=5)
        self.assertIn("FAILED", c)

    def test_cap_paused_warning_present(self):
        c = self._comment(cap_paused=True)
        self.assertIn("cost cap hit", c.lower())
        self.assertIn("SAG-2160", c)

    def test_cap_not_paused_no_warning(self):
        c = self._comment(cap_paused=False)
        self.assertNotIn("auto-paused", c)

    def test_duration_in_comment(self):
        c = self._comment()
        self.assertIn("30.0s", c)


# ---------------------------------------------------------------------------
# run() — happy path and error path
# ---------------------------------------------------------------------------

class TestRunFunction(unittest.IsolatedAsyncioTestCase):
    def _env(self, **overrides) -> dict:
        base = {
            "DATABASE_URL": "postgresql://test/test",
            "PAPERCLIP_API_URL": "http://localhost:3100",
            "PAPERCLIP_API_KEY": "fake-key",
            "PAPERCLIP_RUN_ID": "run-xyz",
            "PAPERCLIP_TASK_ID": "issue-abc",
        }
        base.update(overrides)
        return base

    async def _run_with_summary(self, summary: dict) -> int:
        with patch.dict(os.environ, self._env(), clear=False), \
             patch("batch_runner.EnrichmentDispatcher") as MockDisp, \
             patch("batch_runner._mark_issue_done", new=AsyncMock()) as mock_mark:
            instance = MockDisp.return_value
            instance.run_batch = AsyncMock(return_value=summary)
            code = await run()
        return code

    async def test_success_returns_zero(self):
        code = await self._run_with_summary(
            {"total": 5, "done": 5, "failed": 0, "cap_paused": False}
        )
        self.assertEqual(code, 0)

    async def test_empty_queue_returns_zero(self):
        code = await self._run_with_summary(
            {"total": 0, "done": 0, "failed": 0, "cap_paused": False}
        )
        self.assertEqual(code, 0)

    async def test_dispatcher_exception_returns_one(self):
        with patch.dict(os.environ, self._env(), clear=False), \
             patch("batch_runner.EnrichmentDispatcher") as MockDisp, \
             patch("batch_runner._mark_issue_done", new=AsyncMock()):
            instance = MockDisp.return_value
            instance.run_batch = AsyncMock(side_effect=RuntimeError("db gone"))
            code = await run()
        self.assertEqual(code, 1)

    async def test_mark_issue_done_called_on_success(self):
        with patch.dict(os.environ, self._env(), clear=False), \
             patch("batch_runner.EnrichmentDispatcher") as MockDisp, \
             patch("batch_runner._mark_issue_done", new=AsyncMock()) as mock_mark:
            instance = MockDisp.return_value
            instance.run_batch = AsyncMock(
                return_value={"total": 3, "done": 3, "failed": 0, "cap_paused": False}
            )
            await run()

        mock_mark.assert_awaited_once()
        _, _, _, issue_id, comment = mock_mark.call_args.args
        self.assertEqual(issue_id, "issue-abc")
        self.assertIn("3/3 rows enriched", comment)

    async def test_skips_api_when_task_id_missing(self):
        env = self._env()
        env.pop("PAPERCLIP_TASK_ID", None)
        with patch.dict(os.environ, env, clear=False), \
             patch("batch_runner.EnrichmentDispatcher") as MockDisp, \
             patch("batch_runner._mark_issue_done", new=AsyncMock()) as mock_mark:
            os.environ.pop("PAPERCLIP_TASK_ID", None)
            instance = MockDisp.return_value
            instance.run_batch = AsyncMock(
                return_value={"total": 0, "done": 0, "failed": 0, "cap_paused": False}
            )
            await run()
        mock_mark.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
