"""Tests for digest_and_alert.py — SAG-4193 / SAG-5103."""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from digest_and_alert import (
    CLEAN_FLOOR,
    INVALID_ERROR_THRESHOLD,
    REGRESSION_DROP_THRESHOLD,
    _is_class_invalid,
    compute_regressions,
    format_digest,
    load_results,
)


def _make_result(run_ts, dry_run=False, classes=None, model="test-model"):
    classes = classes or {}
    return {"run_ts": run_ts, "model": model, "dry_run": dry_run, "classes": classes}


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
# _is_class_invalid
# ---------------------------------------------------------------------------

class TestIsClassInvalid:
    def test_valid_class(self):
        assert not _is_class_invalid(_cls(n=20, errors=0))

    def test_just_below_threshold(self):
        # 9/20 = 45% < 50% threshold → valid
        assert not _is_class_invalid(_cls(n=20, errors=9))

    def test_exactly_at_threshold(self):
        # 10/20 = 50% ≥ threshold → invalid
        assert _is_class_invalid(_cls(n=20, errors=10))

    def test_all_errors(self):
        assert _is_class_invalid(_cls(n=20, errors=20))

    def test_explicit_invalid_flag(self):
        cs = _cls(n=20, errors=0)
        cs["invalid"] = True
        assert _is_class_invalid(cs)

    def test_zero_n(self):
        # n=0 → error_rate=1.0 → invalid
        cs = {"n": 0, "errors": 0}
        assert _is_class_invalid(cs)


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
# compute_regressions — return structure
# ---------------------------------------------------------------------------

class TestComputeRegressionsStructure:
    def test_returns_dict_with_three_keys(self):
        cur = _make_result("T2", classes={"x": _cls()})
        pri = _make_result("T1", classes={"x": _cls()})
        result = compute_regressions(cur, pri)
        assert set(result.keys()) == {"regressions", "floor_breaches", "invalid_classes"}

    def test_empty_on_model_change(self):
        cur = _make_result("T2", model="qwen3.6:latest", classes={"x": _cls(task_correct=0.0)})
        pri = _make_result("T1", model="gemma4:26b-a4b-it-q4_K_M", classes={"x": _cls(task_correct=1.0)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["floor_breaches"] == []
        assert result["invalid_classes"] == []


# ---------------------------------------------------------------------------
# compute_regressions — task_correct and tool_call (unchanged semantics)
# ---------------------------------------------------------------------------

class TestComputeRegressionsTaskMetrics:
    def test_no_regression_when_stable(self):
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.9, clean=1.0)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.9, clean=1.0)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["floor_breaches"] == []
        assert result["invalid_classes"] == []

    def test_detects_task_correct_drop(self):
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.70)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.90)})
        result = compute_regressions(cur, pri)
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["metric"] == "task_correct_rate"
        assert result["regressions"][0]["class"] == "x"
        assert abs(result["regressions"][0]["drop"] - 0.20) < 0.001

    def test_no_regression_below_threshold(self):
        # 4pp drop — below 5pp threshold, should NOT trigger
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.86)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.90)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []

    def test_detects_tool_call_drop(self):
        cur = _make_result("T2", classes={"x": _cls(tool_call=0.60)})
        pri = _make_result("T1", classes={"x": _cls(tool_call=0.90)})
        result = compute_regressions(cur, pri)
        assert any(r["metric"] == "tool_call_correct_rate" for r in result["regressions"])

    def test_skips_tool_call_when_null_in_either(self):
        cur = _make_result("T2", classes={"x": _cls(tool_call=None)})
        pri = _make_result("T1", classes={"x": _cls(tool_call=0.90)})
        result = compute_regressions(cur, pri)
        assert not any(r["metric"] == "tool_call_correct_rate" for r in result["regressions"])

    def test_skips_class_missing_from_prior(self):
        cur = _make_result("T2", classes={"new_class": _cls(task_correct=0.0)})
        pri = _make_result("T1", classes={})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["invalid_classes"] == []

    def test_regression_fires_when_model_unchanged(self):
        cur = _make_result("T2", model="qwen3.6:latest", classes={"x": _cls(task_correct=0.70)})
        pri = _make_result("T1", model="qwen3.6:latest", classes={"x": _cls(task_correct=0.90)})
        result = compute_regressions(cur, pri)
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["metric"] == "task_correct_rate"


# ---------------------------------------------------------------------------
# compute_regressions — clean_rate: regression vs floor (new semantics)
# ---------------------------------------------------------------------------

class TestComputeRegressionsCleanRate:
    def test_detects_clean_contamination_regression(self):
        # clean dropped 20pp > 5pp threshold → regression (CTO alert)
        cur = _make_result("T2", classes={"x": _cls(clean=0.80)})
        pri = _make_result("T1", classes={"x": _cls(clean=1.0)})
        result = compute_regressions(cur, pri)
        assert any(r["metric"] == "clean_rate" for r in result["regressions"])
        assert result["floor_breaches"] == []

    def test_clean_drop_above_threshold_is_regression(self):
        # clean dropped 10pp from 1.0 to 0.90 → contamination regression (dropped >5pp)
        cur = _make_result("T2", classes={"x": _cls(clean=CLEAN_FLOOR)})
        pri = _make_result("T1", classes={"x": _cls(clean=1.0)})
        result = compute_regressions(cur, pri)
        assert any(r["metric"] == "clean_rate" for r in result["regressions"])

    def test_floor_breach_not_regression_when_stable(self):
        # clean chronically at 0.80 (below floor) but stable → floor breach, NOT regression
        cur = _make_result("T2", classes={"x": _cls(clean=0.80, task_correct=1.0)})
        pri = _make_result("T1", classes={"x": _cls(clean=0.80, task_correct=1.0)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert len(result["floor_breaches"]) == 1
        assert result["floor_breaches"][0]["class"] == "x"
        assert result["floor_breaches"][0]["metric"] == "clean_rate"

    def test_small_clean_drop_below_floor_is_floor_breach(self):
        # clean dropped 4pp (below threshold) and is below floor → floor breach, not regression
        cur = _make_result("T2", classes={"x": _cls(clean=0.84)})
        pri = _make_result("T1", classes={"x": _cls(clean=0.88)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert any(f["class"] == "x" for f in result["floor_breaches"])

    def test_clean_above_floor_not_flagged(self):
        # clean slightly above floor → no alert at all
        cur = _make_result("T2", classes={"x": _cls(clean=0.92)})
        pri = _make_result("T1", classes={"x": _cls(clean=0.92)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["floor_breaches"] == []

    def test_multiple_regressions(self):
        cur = _make_result("T2", classes={
            "a": _cls(task_correct=0.60),
            "b": _cls(clean=0.50),
        })
        pri = _make_result("T1", classes={
            "a": _cls(task_correct=0.90),
            "b": _cls(clean=1.0),
        })
        result = compute_regressions(cur, pri)
        assert len(result["regressions"]) == 2


# ---------------------------------------------------------------------------
# compute_regressions — INVALID class handling (SAG-5103)
# ---------------------------------------------------------------------------

class TestComputeRegressionsInvalid:
    def test_invalid_class_excluded_from_alerts(self):
        # High error rate (75%) → invalid; excluded from regression/floor checks
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.0, clean=0.0, n=20, errors=15)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=1.0, clean=1.0)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["floor_breaches"] == []
        assert len(result["invalid_classes"]) == 1
        assert result["invalid_classes"][0]["class"] == "x"

    def test_class_below_threshold_not_invalid(self):
        # 9/20 = 45% < 50% threshold → valid, regression still fires
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.55, clean=1.0, n=20, errors=9)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.90, clean=1.0)})
        result = compute_regressions(cur, pri)
        assert result["invalid_classes"] == []
        assert any(r["class"] == "x" for r in result["regressions"])

    def test_prior_invalid_skips_delta_checks(self):
        # Prior run was all-error → cannot compute meaningful delta → no regression
        # clean < floor → floor breach (not regression, since prior was invalid)
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.55, clean=0.80, n=20, errors=0)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=0.0, clean=0.0, n=20, errors=20)})
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert any(f["class"] == "x" for f in result["floor_breaches"])
        assert result["invalid_classes"] == []  # current is valid

    def test_both_invalid_and_valid_classes(self):
        # Mixed: one invalid class, one valid regression
        cur = _make_result("T2", classes={
            "bad": _cls(task_correct=0.0, clean=0.0, n=20, errors=20),
            "ok": _cls(task_correct=0.60, clean=1.0),
        })
        pri = _make_result("T1", classes={
            "bad": _cls(task_correct=0.9, clean=1.0),
            "ok": _cls(task_correct=0.9, clean=1.0),
        })
        result = compute_regressions(cur, pri)
        assert len(result["invalid_classes"]) == 1
        assert result["invalid_classes"][0]["class"] == "bad"
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["class"] == "ok"

    def test_explicit_invalid_flag_respected(self):
        # run_eval marks a class invalid via the "invalid" flag
        cs = _cls(task_correct=0.0, clean=0.0, n=20, errors=5)  # only 25% errors
        cs["invalid"] = True
        cs["invalid_reason"] = "manually flagged"
        cur = _make_result("T2", classes={"x": cs})
        pri = _make_result("T1", classes={"x": _cls(task_correct=1.0)})
        result = compute_regressions(cur, pri)
        assert len(result["invalid_classes"]) == 1
        assert result["regressions"] == []

    def test_all_classes_invalid_emits_only_invalid_list(self):
        # SAG-5103 scenario: endpoint went down mid-run, later classes 100% connection refused
        cur = _make_result("T2", classes={
            "pricing": _cls(n=20, errors=20),
            "code_review": _cls(n=20, errors=20),
            "paralegal": _cls(n=20, errors=20),
        })
        pri = _make_result("T1", classes={
            "pricing": _cls(task_correct=0.5, clean=0.85),
            "code_review": _cls(task_correct=0.6, clean=0.88),
            "paralegal": _cls(task_correct=0.4, clean=0.80),
        })
        result = compute_regressions(cur, pri)
        assert result["regressions"] == []
        assert result["floor_breaches"] == []
        assert len(result["invalid_classes"]) == 3


# ---------------------------------------------------------------------------
# format_digest (updated signature: classify dict)
# ---------------------------------------------------------------------------

def _empty_classify():
    return {"regressions": [], "floor_breaches": [], "invalid_classes": []}


class TestFormatDigest:
    def test_baseline_shows_no_prior(self):
        cur = _make_result("20260616T132119Z", classes={"enrichment_sku": _cls(task_correct=0.85)})
        out = format_digest(cur, None, _empty_classify())
        assert "baseline night" in out
        assert "enrichment_sku" in out
        assert "85.0%" in out
        assert "No regressions" in out

    def test_regression_shown_in_output(self):
        classify = {
            "regressions": [{"class": "doc_extraction", "metric": "task_correct_rate",
                             "cur": 0.10, "prev": 1.0, "drop": 0.90}],
            "floor_breaches": [],
            "invalid_classes": [],
        }
        cur = _make_result("T2", classes={"doc_extraction": _cls(task_correct=0.10)})
        pri = _make_result("T1", classes={"doc_extraction": _cls(task_correct=1.0)})
        out = format_digest(cur, pri, classify)
        assert "REGRESSIONS DETECTED" in out
        assert "doc_extraction" in out
        assert "CTO" in out

    def test_no_cto_mention_for_floor_only(self):
        classify = {
            "regressions": [],
            "floor_breaches": [{"class": "pricing", "metric": "clean_rate",
                                "cur": 0.70, "prev": 0.70, "note": "below floor 90% (stable)"}],
            "invalid_classes": [],
        }
        cur = _make_result("T2", classes={"pricing": _cls(clean=0.70)})
        pri = _make_result("T1", classes={"pricing": _cls(clean=0.70)})
        out = format_digest(cur, pri, classify)
        assert "FLOOR" in out
        assert "pricing" in out
        assert "[@CTO]" not in out  # no @-mention for floor-only
        assert "No regressions detected" in out

    def test_invalid_class_shown_without_cto(self):
        classify = {
            "regressions": [],
            "floor_breaches": [],
            "invalid_classes": [{"class": "pricing", "reason": "error rate 20/20 (100%) ≥ 50% threshold"}],
        }
        cur = _make_result("T2", classes={"pricing": _cls(n=20, errors=20)})
        pri = _make_result("T1", classes={"pricing": _cls()})
        out = format_digest(cur, pri, classify)
        assert "INVALID" in out
        assert "pricing" in out
        assert "CTO" not in out

    def test_all_invalid_run_is_not_reported_as_no_regressions_detected(self):
        classify = {
            "regressions": [],
            "floor_breaches": [],
            "invalid_classes": [
                {"class": "pricing", "reason": "error rate 20/20 (100%) ≥ 50% threshold"},
                {"class": "code_review", "reason": "error rate 20/20 (100%) ≥ 50% threshold"},
            ],
        }
        cur = _make_result("T2", classes={
            "pricing": _cls(n=20, errors=20),
            "code_review": _cls(n=20, errors=20),
        })
        pri = _make_result("T1", classes={
            "pricing": _cls(),
            "code_review": _cls(),
        })
        out = format_digest(cur, pri, classify)
        assert "EVAL OUTAGE" in out
        assert "No regressions detected" not in out
        assert "CTO" not in out

    def test_alert_issue_id_referenced_in_regression(self):
        classify = {
            "regressions": [{"class": "x", "metric": "task_correct_rate",
                             "cur": 0.5, "prev": 1.0, "drop": 0.5}],
            "floor_breaches": [],
            "invalid_classes": [],
        }
        cur = _make_result("T2", classes={"x": _cls(task_correct=0.5)})
        pri = _make_result("T1", classes={"x": _cls(task_correct=1.0)})
        out = format_digest(cur, pri, classify, alert_issue_identifier="SAG-9999")
        assert "SAG-9999" in out
        assert "CTO" in out

    def test_delta_shown_when_prior_exists(self):
        cur = _make_result("T2", classes={"pricing": _cls(task_correct=0.90)})
        pri = _make_result("T1", classes={"pricing": _cls(task_correct=1.0)})
        out = format_digest(cur, prior=pri, classify=_empty_classify())
        assert "-10.0pp" in out

    def test_positive_delta_shown(self):
        cur = _make_result("T2", classes={"pricing": _cls(task_correct=1.0)})
        pri = _make_result("T1", classes={"pricing": _cls(task_correct=0.80)})
        out = format_digest(cur, prior=pri, classify=_empty_classify())
        assert "+20.0pp" in out

    def test_model_change_shows_new_baseline_note(self):
        cur = _make_result("T2", model="qwen3.6:latest", classes={"x": _cls()})
        pri = _make_result("T1", model="gemma4:26b-a4b-it-q4_K_M", classes={"x": _cls()})
        out = format_digest(cur, prior=pri, classify=_empty_classify())
        assert "new baseline" in out
        assert "Delta shown" not in out

    def test_status_column_shows_invalid_label(self):
        classify = {
            "regressions": [],
            "floor_breaches": [],
            "invalid_classes": [{"class": "pricing", "reason": "error rate 20/20"}],
        }
        cur = _make_result("T2", classes={"pricing": _cls(n=20, errors=20)})
        pri = _make_result("T1", classes={"pricing": _cls()})
        out = format_digest(cur, pri, classify)
        assert "INVALID" in out

    def test_status_column_shows_floor_label(self):
        classify = {
            "regressions": [],
            "floor_breaches": [{"class": "pricing", "metric": "clean_rate",
                                "cur": 0.70, "prev": 0.70, "note": "below floor"}],
            "invalid_classes": [],
        }
        cur = _make_result("T2", classes={"pricing": _cls(clean=0.70)})
        pri = _make_result("T1", classes={"pricing": _cls(clean=0.70)})
        out = format_digest(cur, pri, classify)
        assert "FLOOR" in out


# ---------------------------------------------------------------------------
# Integration: compute_regressions → format_digest round-trip
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_sag5103_scenario_no_false_alert(self):
        """Replay of 20260627 evidence: all-error prior + high-error-rate current → no regression."""
        # Prior run: all timeouts (100% error rate per class)
        prior_classes = {cls: _cls(task_correct=0.0, clean=0.0, n=20, errors=20)
                         for cls in ["enrichment_sku", "doc_extraction", "pricing",
                                     "code_review", "qa_unit_tests", "paralegal"]}
        # Current run: enrichment_sku partial success, later classes 100% connection refused
        current_classes = {
            "enrichment_sku": _cls(task_correct=0.55, clean=0.75, n=20, errors=5),
            "doc_extraction": _cls(task_correct=0.05, clean=0.10, n=20, errors=18),
            "pricing": _cls(task_correct=0.0, clean=0.0, n=20, errors=20),
            "code_review": _cls(task_correct=0.0, clean=0.0, n=20, errors=20),
            "qa_unit_tests": _cls(task_correct=0.0, clean=0.0, n=20, errors=20),
            "paralegal": _cls(task_correct=0.0, clean=0.0, n=20, errors=20),
        }
        cur = _make_result("20260627T152400Z", model="qwen3.6:latest", classes=current_classes)
        pri = _make_result("20260623T030047Z", model="qwen3.6:latest", classes=prior_classes)

        result = compute_regressions(cur, pri)

        # No regressions — everything improved or is invalid
        assert result["regressions"] == [], (
            f"Expected no regressions, got: {result['regressions']}"
        )
        # pricing/code_review/qa_unit_tests/paralegal/doc_extraction are INVALID (≥50% errors)
        invalid_names = {iv["class"] for iv in result["invalid_classes"]}
        assert "pricing" in invalid_names
        assert "code_review" in invalid_names
        assert "doc_extraction" in invalid_names
        # enrichment_sku is valid (25% errors) but prior was invalid → no regression; clean<floor → floor breach
        floor_names = {f["class"] for f in result["floor_breaches"]}
        assert "enrichment_sku" in floor_names

        # Verify the digest does NOT say "REGRESSIONS DETECTED" and does NOT @-mention CTO
        digest = format_digest(cur, pri, result)
        assert "REGRESSIONS DETECTED" not in digest
        assert "[@CTO]" not in digest  # no @-mention — infra outage is not a regression
        assert "INVALID" in digest

    def test_synthetic_true_regression_still_alerts(self):
        """A genuine 30pp drop on a valid class must still trigger a regression alert."""
        cur = _make_result("T2", model="qwen3.6:latest", classes={
            "pricing": _cls(task_correct=0.60, clean=1.0, n=20, errors=0),
        })
        pri = _make_result("T1", model="qwen3.6:latest", classes={
            "pricing": _cls(task_correct=0.90, clean=1.0, n=20, errors=0),
        })
        result = compute_regressions(cur, pri)
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["class"] == "pricing"
        digest = format_digest(cur, pri, result)
        assert "REGRESSIONS DETECTED" in digest
        assert "CTO" in digest


# ---------------------------------------------------------------------------
# _api DNS default (SAG-5104 Defect 2)
# ---------------------------------------------------------------------------

class TestApiDnsDefault:
    def test_api_uses_127_0_0_1_when_env_unset(self, monkeypatch):
        """When PAPERCLIP_API_URL is unset, _api must default to 127.0.0.1:3100."""
        import digest_and_alert as da_mod
        import urllib.request

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            raise RuntimeError("stop here")

        monkeypatch.delenv("PAPERCLIP_API_URL", raising=False)
        monkeypatch.setenv("PAPERCLIP_API_KEY", "test-key")
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        try:
            da_mod._api("GET", "/api/test")
        except RuntimeError as exc:
            if "stop here" not in str(exc):
                raise

        assert captured.get("url", "").startswith("http://127.0.0.1:3100"), (
            f"Expected 127.0.0.1:3100 base, got: {captured.get('url')}"
        )

    def test_api_uses_127_0_0_1_when_env_empty_string(self, monkeypatch):
        """When PAPERCLIP_API_URL is set to empty string, _api must fall back to 127.0.0.1:3100."""
        import digest_and_alert as da_mod
        import urllib.request

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            raise RuntimeError("stop here")

        monkeypatch.setenv("PAPERCLIP_API_URL", "")
        monkeypatch.setenv("PAPERCLIP_API_KEY", "test-key")
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        try:
            da_mod._api("GET", "/api/test")
        except RuntimeError as exc:
            if "stop here" not in str(exc):
                raise

        assert captured.get("url", "").startswith("http://127.0.0.1:3100"), (
            f"Expected 127.0.0.1:3100 base, got: {captured.get('url')}"
        )


# ---------------------------------------------------------------------------
# test_all_invalid_does_not_trigger_regression_or_floor
# ---------------------------------------------------------------------------

class TestAllInvalidDoesNotTriggerRegressionOrFloor:
    def test_all_invalid_emits_only_invalid_with_outage_message(self):
        # All classes 100% errors → invalid_classes populated, empty regressions/floor
        classify = {
            "regressions": [],
            "floor_breaches": [],
            "invalid_classes": [
                {"class": "pricing", "reason": "error rate 20/20 (100%) ≥ 50% threshold"},
                {"class": "code_review", "reason": "error rate 18/20 (90%) ≥ 50% threshold"},
                {"class": "qa_unit_tests", "reason": "error rate 20/20 (100%) ≥ 50% threshold"},
            ],
        }
        cur = _make_result("T2", classes={
            "pricing": _cls(n=20, errors=20),
            "code_review": _cls(n=20, errors=18),
            "qa_unit_tests": _cls(n=20, errors=20),
        })
        pri = _make_result("T1", classes={
            "pricing": _cls(),
            "code_review": _cls(),
            "qa_unit_tests": _cls(),
        })
        out = format_digest(cur, pri, classify)
        assert "EVAL OUTAGE" in out
        assert "No regressions detected" not in out
        assert "CTO" not in out

    def test_partial_invalid_still_shows_regressions_when_present(self):
        # Not all classes invalid; regression present → should show regression, not outage
        classify = {
            "regressions": [{"class": "pricing", "metric": "task_correct_rate",
                             "cur": 0.5, "prev": 1.0, "drop": 0.5}],
            "floor_breaches": [],
            "invalid_classes": [
                {"class": "code_review", "reason": "error rate 20/20 (100%) ≥ 50% threshold"},
            ],
        }
        cur = _make_result("T2", classes={
            "pricing": _cls(task_correct=0.5),
            "code_review": _cls(n=20, errors=20),
        })
        pri = _make_result("T1", classes={
            "pricing": _cls(task_correct=1.0),
            "code_review": _cls(),
        })
        out = format_digest(cur, pri, classify)
        assert "REGRESSIONS DETECTED" in out
        assert "EVAL OUTAGE" not in out


# ---------------------------------------------------------------------------
# _is_timeout_exception (run_eval.py)
# ---------------------------------------------------------------------------

class TestIsTimeoutException:
    def test_http_504_is_timeout(self):
        from run_eval import _is_timeout_exception
        exc = urllib.error.HTTPError("http://x", 504, "Gateway Timeout", {}, None)
        assert _is_timeout_exception(exc) is True

    def test_http_500_is_not_timeout(self):
        from run_eval import _is_timeout_exception
        exc = urllib.error.HTTPError("http://x", 500, "Server Error", {}, None)
        assert _is_timeout_exception(exc) is False

    def test_urllib_error_is_not_timeout(self):
        from run_eval import _is_timeout_exception
        exc = urllib.error.URLError("connection refused")
        assert _is_timeout_exception(exc) is False

    def test_regular_exception_is_not_timeout(self):
        from run_eval import _is_timeout_exception
        assert _is_timeout_exception(RuntimeError("boom")) is False

    def test_http_502_is_not_timeout(self):
        from run_eval import _is_timeout_exception
        exc = urllib.error.HTTPError("http://x", 502, "Bad Gateway", {}, None)
        assert _is_timeout_exception(exc) is False

    def test_http_503_is_not_timeout(self):
        from run_eval import _is_timeout_exception
        exc = urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, None)
        assert _is_timeout_exception(exc) is False


# ---------------------------------------------------------------------------
# HIGH_TIMEOUT_RATE constant (SAG-5280)
# ---------------------------------------------------------------------------

class TestHighTimeoutRate:
    def test_constant_exists_and_is_0_30(self):
        from run_eval import HIGH_TIMEOUT_RATE
        assert HIGH_TIMEOUT_RATE == 0.30


# ---------------------------------------------------------------------------
# demo-alert: create+cancel (SAG-5104 Defect 2 DoD)
# ---------------------------------------------------------------------------

class TestDemoAlert:
    def test_demo_alert_does_not_force_no_post(self, tmp_path, monkeypatch):
        """--demo-alert must NOT imply --no-post; it should attempt real API calls."""
        import digest_and_alert as da_mod

        api_calls = []

        def fake_api(method, path, body=None):
            api_calls.append((method, path, body))
            if path.endswith("/comments"):
                return {"id": "comment-001"}
            if "/issues" in path and method == "POST":
                return {"id": "uuid-demo-alert", "identifier": "SAG-TEST-999"}
            if "/issues/" in path and method == "PATCH":
                return {}
            return {}

        monkeypatch.setattr(da_mod, "_api", fake_api)

        # Write a minimal results file
        run_data = {
            "run_ts": "20260627T000000Z",
            "model": "qwen3.6:latest",
            "dry_run": False,
            "classes": {"pricing": {
                "task_correct_rate": 1.0, "task_correct_ci_95": [0.8, 1.0],
                "tool_call_correct_rate": None, "clean_rate": 1.0,
                "clean_ci_95": [0.8, 1.0], "n": 20, "errors": 0, "per_row": [],
            }},
        }
        (tmp_path / "20260627T000000Z.json").write_text(json.dumps(run_data))
        (tmp_path / "20260626T000000Z.json").write_text(json.dumps({
            **run_data, "run_ts": "20260626T000000Z",
        }))

        da_mod.run(results_dir=tmp_path, demo_alert=True, no_post=False)

        # Must have attempted to create an issue (not skipped due to no_post)
        issue_create_calls = [(m, p) for m, p, _ in api_calls if "companies" in p and m == "POST"]
        assert len(issue_create_calls) >= 1, "demo-alert must attempt issue creation"

        # Must have immediately cancelled the demo issue
        cancel_calls = [(m, p, b) for m, p, b in api_calls if "/issues/uuid-demo-alert" in p and m == "PATCH"]
        assert len(cancel_calls) >= 1, "demo-alert must cancel the created issue"
        assert cancel_calls[0][2].get("status") == "cancelled"
