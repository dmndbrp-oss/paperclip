"""
run_eval.py — SAG-4191

Nightly local-AI regression scoring runner.

Drives each gold-set class through the on-box Ollama model (qwen3.6:latest)
SERIALLY (one row at a time, one model, respects OLLAMA_MAX_LOADED_MODELS=1).
Emits per-agent JSON results with three scores per class:
  (a) task_correct    — schema-valid / task-correct rate
  (b) tool_call_correct — tool-call correctness rate (null when N/A)
  (c) clean           — contamination-clean rate (via clean_parser)

Results written to: infra/runtime-eval/results/<UTC-stamp>.json

Usage:
  cd <workspace_root>/infra/runtime-eval
  python3 run_eval.py [--classes enrichment_sku,code_review] [--model NAME] [--dry-run]

Environment variables:
  OLLAMA_URL          default http://localhost:11434
  EVAL_MODEL          override model (default qwen3.6:latest)
  EVAL_TIMEOUT_S      per-row timeout seconds (default 300)
  NO_TEMP_OVERRIDE    if set, omit temperature from per-request options so the
                      Modelfile's baked temperature is used (production path)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
GOLD_DIR = str(HERE / "gold")
RESULTS_DIR = HERE / "results"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("EVAL_MODEL", "qwen3.6:latest")
EVAL_TIMEOUT = float(os.environ.get("EVAL_TIMEOUT_S", "300"))

INVALID_ERROR_THRESHOLD = 0.50   # classes with ≥ 50% errors are marked invalid/skipped
HIGH_TIMEOUT_RATE = 0.30         # timeout rate ≥ 30% triggers run-health alert
RETRY_COUNT = 2                  # per-call retries on transient URLError
RETRY_DELAY_S = 10               # seconds between retries

ALL_CLASSES = [
    "enrichment_sku",
    "doc_extraction",
    "pricing",
    "code_review",
    "qa_unit_tests",
    "paralegal",
]

# Gold classes whose scorer expects JSON output from the model.
# tool_call and any future free-text check types are NOT in this set.
JSON_CHECK_TYPES = {"json_values", "json_values_normalized", "json_list_min"}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _is_timeout_exception(exc: Exception) -> bool:
    """Return True if *exc* looks like a server-side timeout (HTTP 504, etc.)."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 504:
        return True
    return False


# ---------------------------------------------------------------------------
# Endpoint health check
# ---------------------------------------------------------------------------

def check_endpoint_health(url: str = OLLAMA_URL, timeout: float = 10.0) -> bool:
    """Return True if the Ollama /api/tags endpoint responds with HTTP 200."""
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Import clean_parser (sibling module)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(HERE))
from clean_parser import score as _clean_score  # noqa: E402


# ---------------------------------------------------------------------------
# Gold-set loader
# ---------------------------------------------------------------------------

def load_gold_set(class_name: str) -> list[dict]:
    path = Path(GOLD_DIR) / f"{class_name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Gold set not found: {path}")
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Scoring functions (pure — no I/O, tested in test_run_eval.py)
# ---------------------------------------------------------------------------

def _parse_json_dict(output_text: str) -> dict | None:
    """Parse output_text as JSON and return it if it is a dict, else None."""
    try:
        parsed = json.loads(output_text.strip())
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def score_json_values(output_text: str, expected: dict) -> float:
    """Parse output as JSON; return 1.0 if all expected key=value pairs match."""
    parsed = _parse_json_dict(output_text)
    if parsed is None:
        return 0.0
    for key, val in expected.items():
        if parsed.get(key) != val:
            return 0.0
    return 1.0


_DATE_FORMATS = ('%B %d, %Y', '%B %d %Y', '%Y-%m-%d', '%m/%d/%Y')
_CURRENCY_STRIP = re.compile(r'[$€£¥,\s]')


def _normalize_value(val):
    """Canonical form for normalized comparison: bool→bool, None→None,
    list→comma-joined string, numeric strings→Decimal (strips currency/commas),
    date strings→date, all other strings→case-folded."""
    if isinstance(val, bool):
        return val
    if val is None:
        return val
    # Lists: join to comma-separated string so model-as-list matches gold-as-string
    if isinstance(val, list):
        val = ', '.join(str(item) for item in val)
    s = str(val).strip()
    if not s:
        return s
    # Numeric: strip currency symbols and thousand-separators, compare as Decimal
    num_str = _CURRENCY_STRIP.sub('', s)
    if num_str:
        try:
            return Decimal(num_str)
        except InvalidOperation:
            pass
    # Date: common textual and ISO formats
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return s.casefold()


def score_json_values_normalized(output_text: str, expected: dict) -> float:
    """Like score_json_values but applies case/numeric/date normalization.

    Accepts: case variants, int/float vs numeric-string, currency-prefixed
    numbers, comma thousand-separators, and common date format differences.
    The strict score_json_values variant remains for classes that need exact
    matching."""
    parsed = _parse_json_dict(output_text)
    if parsed is None:
        return 0.0
    for key, val in expected.items():
        if _normalize_value(parsed.get(key)) != _normalize_value(val):
            return 0.0
    return 1.0


def score_json_list_min(output_text: str, expected: dict) -> float:
    """Parse output as JSON; return 1.0 if output[key] is a list with >= min items."""
    parsed = _parse_json_dict(output_text)
    if parsed is None:
        return 0.0
    key = expected.get("key", "")
    min_count = expected.get("min", 1)
    value = parsed.get(key)
    if not isinstance(value, list):
        return 0.0
    return 1.0 if len(value) >= min_count else 0.0


def score_tool_call(tool_calls: list | None, expected: dict) -> float:
    """Return 1.0 if first tool_call matches expected function name + required args."""
    if not tool_calls:
        return 0.0
    tc = tool_calls[0]
    fn = tc.get("function", {})
    if fn.get("name") != expected.get("function"):
        return 0.0
    args = fn.get("arguments", {}) or {}
    for arg in expected.get("required_args", []):
        if arg not in args:
            return 0.0
    return 1.0


def score_row(row: dict, output_text: str, tool_calls: list | None) -> dict:
    """Score a single row against its gold expected, returning a scores dict."""
    check_type = row.get("check_type", "json_values")
    expected = row.get("expected", {})

    # (a) task_correct
    if check_type == "json_values":
        task_correct = score_json_values(output_text, expected)
    elif check_type == "json_values_normalized":
        task_correct = score_json_values_normalized(output_text, expected)
    elif check_type == "json_list_min":
        task_correct = score_json_list_min(output_text, expected)
    elif check_type == "tool_call":
        task_correct = score_tool_call(tool_calls, expected)
    else:
        task_correct = 0.0

    # (b) tool_call_correct — only for explicit tool_call tasks or rows with a tool definition
    tool_call_correct: float | None = None
    if check_type == "tool_call":
        tool_call_correct = task_correct  # already computed above — no need to call again
    elif row.get("tool_def"):
        tool_call_correct = score_tool_call(tool_calls, expected)

    # (c) clean — contamination check on the raw content
    clean = _clean_score(output_text)

    return {
        "id": row.get("id"),
        "task_correct": task_correct,
        "tool_call_correct": tool_call_correct,
        "clean": clean,
    }


# ---------------------------------------------------------------------------
# Ollama call (serialized — one request at a time)
# ---------------------------------------------------------------------------

def _ollama_call(
    model: str,
    system: str,
    user: str,
    tools: list | None = None,
    timeout: float = EVAL_TIMEOUT,
    want_json: bool = False,
) -> dict:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})

    body: dict = {
        "model": model,
        "messages": msgs,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": ({} if os.environ.get("NO_TEMP_OVERRIDE") else {"temperature": 0}) | {"num_predict": 2048},
    }
    if want_json:
        body["format"] = "json"

    if tools:
        body["tools"] = tools

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    data["_wall_s"] = round(time.time() - t0, 2)
    return data


# ---------------------------------------------------------------------------
# Wilson CI helper
# ---------------------------------------------------------------------------

def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def eval_class(class_name: str, model: str = DEFAULT_MODEL, dry_run: bool = False) -> dict:
    rows = load_gold_set(class_name)
    log.info("[%s] %d rows, model=%s", class_name, len(rows), model)

    per_row: list[dict] = []
    for i, row in enumerate(rows):
        log.info("[%s] row %d/%d id=%s", class_name, i + 1, len(rows), row["id"])
        if dry_run:
            result = {
                "id": row["id"],
                "task_correct": 1.0,
                "tool_call_correct": None,
                "clean": 1.0,
                "wall_s": 0.0,
                "error": None,
            }
            per_row.append(result)
            continue

        t0 = time.monotonic()
        try:
            check_type = row.get("check_type", "json_values")
            last_exc: Exception | None = None
            resp = None
            for attempt in range(RETRY_COUNT + 1):
                try:
                    resp = _ollama_call(
                        model=model,
                        system=row.get("system", ""),
                        user=row.get("input", ""),
                        tools=row.get("tool_def"),
                        want_json=check_type in JSON_CHECK_TYPES,
                    )
                    break
                except urllib.error.URLError as exc:
                    last_exc = exc
                    if attempt < RETRY_COUNT:
                        log.warning(
                            "[%s] row %s attempt %d/%d URLError (%s) — retrying in %ds",
                            class_name, row["id"], attempt + 1, RETRY_COUNT, exc, RETRY_DELAY_S,
                        )
                        time.sleep(RETRY_DELAY_S)
                    else:
                        raise
            assert resp is not None
            msg = resp.get("message", {})
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
            scores = score_row(row, content, tool_calls)
            scores["wall_s"] = resp.get("_wall_s", 0.0)
            scores["error"] = None
        except Exception as exc:
            log.warning("[%s] row %s error: %s", class_name, row["id"], exc)
            scores = {
                "id": row["id"],
                "task_correct": 0.0,
                "tool_call_correct": None,
                "clean": 0.0,
                "wall_s": round(time.monotonic() - t0, 2),
                "error": str(exc),
            }
        per_row.append(scores)

    n = len(per_row)
    n_correct = sum(1 for r in per_row if r["task_correct"] == 1.0)
    n_clean = sum(1 for r in per_row if r["clean"] == 1.0)
    tc_rows = [r for r in per_row if r.get("tool_call_correct") is not None]

    ci_lo, ci_hi = _wilson_ci(n_correct, n)
    clean_lo, clean_hi = _wilson_ci(n_clean, n)

    summary = {
        "class": class_name,
        "model": model,
        "n": n,
        "task_correct_rate": round(n_correct / n, 4) if n else 0.0,
        "task_correct_ci_95": [ci_lo, ci_hi],
        "tool_call_correct_rate": (
            round(sum(r["tool_call_correct"] for r in tc_rows) / len(tc_rows), 4)
            if tc_rows else None
        ),
        "clean_rate": round(n_clean / n, 4) if n else 0.0,
        "clean_ci_95": [clean_lo, clean_hi],
        "errors": sum(1 for r in per_row if r.get("error")),
        "per_row": per_row,
    }
    n_errors = summary["errors"]
    error_rate = n_errors / n if n else 1.0
    summary["invalid"] = error_rate >= INVALID_ERROR_THRESHOLD
    summary["invalid_reason"] = (
        f"error rate {n_errors}/{n} ({error_rate:.0%}) ≥ threshold {INVALID_ERROR_THRESHOLD:.0%}"
        if summary["invalid"] else None
    )
    log.info(
        "[%s] done: task_correct=%.1f%% clean=%.1f%% errors=%d",
        class_name,
        summary["task_correct_rate"] * 100,
        summary["clean_rate"] * 100,
        summary["errors"],
    )
    return summary


def run_eval(
    classes: list[str] | None = None,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict:
    if classes is None:
        classes = ALL_CLASSES

    if not dry_run and not check_endpoint_health():
        msg = (
            f"Pre-flight health check failed: Ollama endpoint {OLLAMA_URL} is unreachable. "
            "Aborting eval — no results written."
        )
        log.error(msg)
        raise RuntimeError(msg)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{ts}.json"

    results: dict = {
        "run_ts": ts,
        "model": model,
        "dry_run": dry_run,
        "classes": {},
    }

    for cls in classes:
        summary = eval_class(cls, model=model, dry_run=dry_run)
        results["classes"][cls] = summary
        # Incremental write: persists after each class so a mid-sweep crash loses no data
        out_path.write_text(json.dumps(results, indent=2))

    # Final write (idempotent — ensures file is up-to-date even if loop exits cleanly)
    log.info("Results written to %s", out_path)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> dict:
    args: dict = {"classes": None, "model": DEFAULT_MODEL, "dry_run": False}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--classes" and i + 1 < len(argv):
            args["classes"] = [c.strip() for c in argv[i + 1].split(",")]
            i += 2
        elif arg == "--model" and i + 1 < len(argv):
            args["model"] = argv[i + 1]
            i += 2
        elif arg == "--dry-run":
            args["dry_run"] = True
            i += 1
        else:
            i += 1
    return args


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    args = _parse_args(sys.argv[1:])
    try:
        results = run_eval(**args)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    print("\n=== EVAL SUMMARY ===")
    for cls, summary in results["classes"].items():
        tc = summary["task_correct_rate"]
        cl = summary["clean_rate"]
        ci = summary["task_correct_ci_95"]
        tool = summary.get("tool_call_correct_rate")
        tool_str = f"  tool_call={tool:.1%}" if tool is not None else ""
        invalid_note = " [INVALID]" if summary.get("invalid") else ""
        print(
            f"  {cls}: task_correct={tc:.1%} CI95[{ci[0]:.2f},{ci[1]:.2f}]"
            f"  clean={cl:.1%}{tool_str}  errors={summary['errors']}/{summary['n']}{invalid_note}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
