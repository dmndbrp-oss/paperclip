"""Tests for run_eval scoring logic — TDD, written before implementation (SAG-4191).

These tests cover the pure scoring functions only (no Ollama calls).
Integration (actual Ollama sweep) is tested via the manual full-sweep run.
"""
import json
import pathlib
import pytest
from run_eval import (
    score_json_values,
    score_json_list_min,
    score_tool_call,
    score_row,
    load_gold_set,
    GOLD_DIR,
)


# ---------------------------------------------------------------------------
# score_json_values — parses JSON output and checks expected key=value pairs
# ---------------------------------------------------------------------------

class TestScoreJsonValues:
    def test_exact_match_returns_one(self):
        output = '{"material_type": "marble", "color_family": "white"}'
        expected = {"material_type": "marble", "color_family": "white"}
        assert score_json_values(output, expected) == 1.0

    def test_extra_keys_in_output_still_pass(self):
        output = '{"material_type": "marble", "color_family": "white", "extra": "ok"}'
        expected = {"material_type": "marble"}
        assert score_json_values(output, expected) == 1.0

    def test_wrong_value_returns_zero(self):
        output = '{"material_type": "granite"}'
        expected = {"material_type": "marble"}
        assert score_json_values(output, expected) == 0.0

    def test_missing_expected_key_returns_zero(self):
        output = '{"color_family": "white"}'
        expected = {"material_type": "marble"}
        assert score_json_values(output, expected) == 0.0

    def test_invalid_json_returns_zero(self):
        assert score_json_values("not json at all", {"key": "val"}) == 0.0

    def test_empty_expected_passes_valid_json(self):
        assert score_json_values('{"any": "thing"}', {}) == 1.0

    def test_empty_expected_fails_invalid_json(self):
        assert score_json_values("bad", {}) == 0.0

    def test_boolean_value_match(self):
        output = '{"has_bugs": true}'
        assert score_json_values(output, {"has_bugs": True}) == 1.0

    def test_boolean_mismatch_returns_zero(self):
        output = '{"has_bugs": false}'
        assert score_json_values(output, {"has_bugs": True}) == 0.0

    def test_json_embedded_in_prose_returns_zero(self):
        output = 'Here is the result: {"material_type": "marble"} done.'
        expected = {"material_type": "marble"}
        assert score_json_values(output, expected) == 0.0

    def test_case_sensitive_string_match(self):
        output = '{"material_type": "Marble"}'
        expected = {"material_type": "marble"}
        assert score_json_values(output, expected) == 0.0

    def test_multiple_checks_all_must_pass(self):
        output = '{"material_type": "marble", "color_family": "gray"}'
        expected = {"material_type": "marble", "color_family": "white"}
        assert score_json_values(output, expected) == 0.0

    def test_currency_value_match(self):
        output = '{"currency": "USD", "total_low": 1200}'
        assert score_json_values(output, {"currency": "USD"}) == 1.0


# ---------------------------------------------------------------------------
# score_json_list_min — parses JSON, checks that output[key] is a list of >= min len
# ---------------------------------------------------------------------------

class TestScoreJsonListMin:
    def test_sufficient_items_returns_one(self):
        output = '{"test_cases": [1, 2, 3, 4]}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 3}) == 1.0

    def test_exactly_min_returns_one(self):
        output = '{"test_cases": [1, 2, 3]}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 3}) == 1.0

    def test_too_few_items_returns_zero(self):
        output = '{"test_cases": [1, 2]}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 3}) == 0.0

    def test_empty_list_returns_zero(self):
        output = '{"test_cases": []}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 1}) == 0.0

    def test_key_missing_returns_zero(self):
        output = '{"other": [1, 2, 3]}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 1}) == 0.0

    def test_value_not_a_list_returns_zero(self):
        output = '{"test_cases": "three items"}'
        assert score_json_list_min(output, {"key": "test_cases", "min": 1}) == 0.0

    def test_invalid_json_returns_zero(self):
        assert score_json_list_min("bad json", {"key": "test_cases", "min": 1}) == 0.0


# ---------------------------------------------------------------------------
# score_tool_call — checks model's tool_calls for expected function + required args
# ---------------------------------------------------------------------------

class TestScoreToolCall:
    def _make_tc(self, name, args=None):
        return [{"function": {"name": name, "arguments": args or {}}}]

    def test_correct_function_name_returns_one(self):
        tool_calls = self._make_tc("get_price", {"material": "marble", "sq_ft": 45})
        expected = {"function": "get_price", "required_args": ["material", "sq_ft"]}
        assert score_tool_call(tool_calls, expected) == 1.0

    def test_wrong_function_name_returns_zero(self):
        tool_calls = self._make_tc("wrong_fn", {"material": "marble"})
        expected = {"function": "get_price", "required_args": ["material"]}
        assert score_tool_call(tool_calls, expected) == 0.0

    def test_missing_required_arg_returns_zero(self):
        tool_calls = self._make_tc("get_price", {"material": "marble"})
        expected = {"function": "get_price", "required_args": ["material", "sq_ft"]}
        assert score_tool_call(tool_calls, expected) == 0.0

    def test_no_tool_calls_returns_zero(self):
        assert score_tool_call([], {"function": "get_price", "required_args": []}) == 0.0

    def test_null_tool_calls_returns_zero(self):
        assert score_tool_call(None, {"function": "get_price", "required_args": []}) == 0.0

    def test_no_required_args_just_name_match(self):
        tool_calls = self._make_tc("get_price", {})
        expected = {"function": "get_price", "required_args": []}
        assert score_tool_call(tool_calls, expected) == 1.0


# ---------------------------------------------------------------------------
# score_row — dispatches to the right scorer and adds contamination score
# ---------------------------------------------------------------------------

class TestScoreRow:
    def _row(self, check_type, expected, tool_def=None):
        return {
            "id": "test-1",
            "task_type": "test",
            "check_type": check_type,
            "expected": expected,
            "tool_def": tool_def,
        }

    def test_clean_correct_json_scores_all_ones(self):
        row = self._row("json_values", {"material_type": "marble"})
        result = score_row(row, '{"material_type": "marble"}', tool_calls=[])
        assert result["task_correct"] == 1.0
        assert result["clean"] == 1.0
        assert result["tool_call_correct"] is None

    def test_contaminated_output_scores_zero_clean(self):
        row = self._row("json_values", {"k": "v"})
        output = '<think>reasoning</think>{"k": "v"}'
        result = score_row(row, output, tool_calls=[])
        assert result["clean"] == 0.0

    def test_wrong_value_scores_zero_task_correct(self):
        row = self._row("json_values", {"material_type": "marble"})
        result = score_row(row, '{"material_type": "granite"}', tool_calls=[])
        assert result["task_correct"] == 0.0

    def test_json_list_min_check_type(self):
        row = self._row("json_list_min", {"key": "test_cases", "min": 2})
        result = score_row(row, '{"test_cases": [1, 2, 3]}', tool_calls=[])
        assert result["task_correct"] == 1.0

    def test_tool_call_check_type_uses_tool_calls(self):
        row = self._row(
            "tool_call",
            {"function": "get_price", "required_args": ["sq_ft"]},
        )
        tc = [{"function": {"name": "get_price", "arguments": {"sq_ft": 45}}}]
        result = score_row(row, "", tool_calls=tc)
        assert result["tool_call_correct"] == 1.0

    def test_score_row_returns_required_fields(self):
        row = self._row("json_values", {})
        result = score_row(row, "{}", tool_calls=[])
        assert "task_correct" in result
        assert "tool_call_correct" in result
        assert "clean" in result
        assert "id" in result


# ---------------------------------------------------------------------------
# load_gold_set — reads a class JSONL file and validates basic structure
# ---------------------------------------------------------------------------

class TestLoadGoldSet:
    def test_loads_enrichment_sku(self):
        rows = load_gold_set("enrichment_sku")
        assert len(rows) >= 20
        for r in rows:
            assert "id" in r
            assert "input" in r
            assert "system" in r
            assert "expected" in r
            assert "check_type" in r

    def test_loads_code_review(self):
        rows = load_gold_set("code_review")
        assert len(rows) >= 20

    def test_loads_qa_unit_tests(self):
        rows = load_gold_set("qa_unit_tests")
        assert len(rows) >= 20

    def test_loads_paralegal(self):
        rows = load_gold_set("paralegal")
        assert len(rows) >= 20

    def test_loads_pricing(self):
        rows = load_gold_set("pricing")
        assert len(rows) >= 20

    def test_loads_doc_extraction(self):
        rows = load_gold_set("doc_extraction")
        assert len(rows) >= 20

    def test_missing_class_raises(self):
        with pytest.raises(FileNotFoundError):
            load_gold_set("nonexistent_class")

    def test_gold_dir_constant_exists(self):
        assert pathlib.Path(GOLD_DIR).is_dir()


# ---------------------------------------------------------------------------
# TestIncrementalWrite — proves run_eval writes JSON after each class completes,
# not only at the very end (SAG-4217 root-cause fix)
# ---------------------------------------------------------------------------

class TestIncrementalWrite:
    """Incremental-persistence contract: file must contain class N's summary
    even when class N+1 raises before completion."""

    def _fake_summary(self, cls: str) -> dict:
        return {
            "class": cls,
            "model": "test-model",
            "n": 1,
            "task_correct_rate": 0.75,
            "task_correct_ci_95": [0.3, 0.95],
            "tool_call_correct_rate": None,
            "clean_rate": 1.0,
            "clean_ci_95": [0.5, 1.0],
            "errors": 0,
            "per_row": [],
        }

    def test_first_class_persisted_before_second_completes(self, tmp_path, monkeypatch):
        """If class 2 raises, the file must already contain class 1's summary."""
        import run_eval as re_mod

        call_count = {"n": 0}

        def fake_eval_class(cls, model=None, dry_run=False):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return self._fake_summary(cls)
            raise RuntimeError("second class explodes — simulating mid-sweep crash")

        monkeypatch.setattr(re_mod, "RESULTS_DIR", tmp_path)
        monkeypatch.setattr(re_mod, "eval_class", fake_eval_class)

        with pytest.raises(RuntimeError, match="second class explodes"):
            re_mod.run_eval(classes=["enrichment_sku", "doc_extraction"], dry_run=False)

        result_files = list(tmp_path.glob("*.json"))
        assert len(result_files) == 1, "Expected exactly one results file to have been created"

        data = json.loads(result_files[0].read_text())
        assert "enrichment_sku" in data["classes"], "First class must be persisted before crash"
        assert data["classes"]["enrichment_sku"]["task_correct_rate"] == 0.75
        assert "doc_extraction" not in data["classes"], "Crashed class must not appear"
