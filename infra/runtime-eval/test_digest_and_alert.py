"""Tests for digest_and_alert.py — SAG-4193."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from digest_and_alert import (
    compute_regressions,
    format_digest,
    load_results,
    REGRESSION_DROP_THRESHOLD,
    CLEAN_FLOOR,
)


def _make_result(run_ts, dry_run=False, classes=None):
    classes = classes or {}
    return {"run_ts": run_ts, "model": "test-model", "dry_run": dry_run, "classes": classes}


def _cls(task_correct=1.0, clean=1.0, tool_call=None, n=20, errors=0):
    return {
        "task_correct_rate": task_correct,
        "task_correct_ci_95": [0.8, 1.0],
        "tool_call_correct_rate": tool_call,
        "clean_rate": clean,
        "clean_ci_95": [0.8, 1.0],
        "n": n,
        "errors": errors,
        "per_row": [],
    }


# ---------------------------------------------------------------------------
# load_results
# ---------------------------------------------------------------------------

class TestLoadResults:
    def test_filters_dry_runs(self, tmp_path):
        (tmp_path / "A.json").write_text(json.dumps(_make_result("20260601T000000Z", dry_run=True, classes={"x": _cls()})))
        (tmp_path / "B.json").write_text(json.dumps(_make_result("20260602T000000Z", dry_run=False, classes={"x": _cls()})))
        results = load_results(tmp_path)
        assert len(results) == 1
        assert results[0]["run_ts"] == "20260602T000000Z"

    def test_sorted_oldest_first(self, tmp_path):
        (tmp_path / "B.json").write_text(json.dumps(_make_result("20260602T000000Z", classes={"x": _cls()})))
        (tmp_path / "A.json").write_text(json.dumps(_make_result("20260601T000000Z", classes={"x": _cls()})))
        results = load_results(tmp_path)
        assert results[0]["run_ts"] == "20260601T000000Z"
        assert results[1]["run_ts"] == "20260602T000000Z"

    def test_empty_dir(self, tmp_path):
        assert load_results(tmp_path) == []

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("{invalid json")
        (tmp_path / "good.json").write_text(json.dumps(_make_result("20260601T000000Z", classes={"x": _cls()})))
        results = load_results(tmp_path)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# compute_regressions
# ---------------------------------------------------------------------------

class TestComputeRegressions:
    def test_no_regression_when_stable(self):
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.9, clean=1.0)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.9, clean=1.0)})
        assert compute_regressions(cur, pri) == []

    def test_detects_task_correct_drop(self):
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.70)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.90)})
        regressions = compute_regressions(cur, pri)
        assert len(regressions) == 1
        assert regressions[0]["metric"] == "task_correct_rate"
        assert regressions[0]["class"] == "x"
        assert abs(regressions[0]["drop"] - 0.20) < 0.001

    def test_no_regression_below_threshold(self):
        # 4pp drop — below 5pp threshold, should NOT trigger
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.86)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.90)})
        regressions = compute_regressions(cur, pri)
        assert regressions == []

    def test_detects_tool_call_drop(self):
        cur = _make_result("T2", classes={"x": _cls(tool_call=0.60)})
        pri = _make_result("T1", classes={"x": _cls(tool_call=0.90)})
        regressions = compute_regressions(cur, pri)
        assert any(r["metric"] == "tool_call_correct_rate" for r in regressions)

    def test_skips_tool_call_when_null_in_either(self):
        cur = _make_result("T2", classes={"x": _cls(tool_call=None)})
        pri = _make_result("T1", classes={"x": _cls(tool_call=0.90)})
        regressions = compute_regressions(cur, pri)
        assert not any(r["metric"] == "tool_call_correct_rate" for r in regressions)

    def test_detects_clean_below_floor(self):
        cur = _make_result("T2", classes={"x": _cls(clean=0.80)})
        pri = _make_result("T1", classes={"x": _cls(clean=1.0)})
        regressions = compute_regressions(cur, pri)
        assert any(r["metric"] == "clean_rate" for r in regressions)

    def test_no_alert_when_clean_at_floor(self):
        cur = _make_result("T2", classes={"x": _cls(clean=CLEAN_FLOOR)})
        pri = _make_result("T1", classes={"x": _cls(clean=1.0)})
        # Exactly at floor should NOT trigger (< floor, not <=)
        regressions = compute_regressions(cur, pri)
        assert not any(r["metric"] == "clean_rate" for r in regressions)

    def test_skips_class_missing_from_prior(self):
        cur = _make_result("T2", classes={"new_class": _cls(task_correct=0.0)})
        pri = _make_result("T1", classes={})
        assert compute_regressions(cur, pri) == []

    def test_multiple_regressions(self):
        cur = _make_result("T2", classes={
            "a": _cls(task_correct=0.60),
            "b": _cls(clean=0.50),
        })
        pri = _make_result("T1", classes={
            "a": _cls(task_correct=0.90),
            "b": _cls(clean=1.0),
        })
        regressions = compute_regressions(cur, pri)
        assert len(regressions) == 2


# ---------------------------------------------------------------------------
# format_digest
# ---------------------------------------------------------------------------

class TestFormatDigest:
    def test_baseline_shows_no_prior(self):
        cur = _make_result("20260616T132119Z", classes={"enrichment_sku": _cls(task_correct=0.85)})
        out = format_digest(cur, None, [])
        assert "baseline night" in out
        assert "enrichment_sku" in out
        assert "85.0%" in out
        assert "✅ No regressions" in out

    def test_regression_shown_in_output(self):
        regressions = [{"class": "doc_extraction", "metric": "task_correct_rate",
                        "cur": 0.10, "prev": 1.0, "drop": 0.90}]
        cur = _make_result("T2", classes={"doc_extraction": _cls(task_correct=0.10)})
        pri = _make_result("T1", classes={"doc_extraction": _cls(task_correct=1.0)})
        out = format_digest(cur, pri, regressions)
        assert "REGRESSIONS DETECTED" in out
        assert "doc_extraction" in out
        assert "CTO" in out

    def test_delta_shown_when_prior_exists(self):
        cur = _make_result("T2", classes={"pricing": _cls(task_correct=0.90)})
        pri = _make_result("T1", classes={"pricing": _cls(task_correct=1.0)})
        out = format_digest(cur, prior=pri, regressions=[])
        assert "-10.0pp" in out

    def test_positive_delta_shown(self):
        cur = _make_result("T2", classes={"pricing": _cls(task_correct=1.0)})
        pri = _make_result("T1", classes={"pricing": _cls(task_correct=0.80)})
        out = format_digest(cur, prior=pri, regressions=[])
        assert "+20.0pp" in out
