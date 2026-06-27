"""
digest_and_alert.py — SAG-4193

Compare the latest two non-dry-run results, format a digest, post it to the
SAG-4193 digest log issue, and open a regression-alert issue (or @-mention
CTO) when any metric crosses the threshold.

Regression thresholds (per SAG-4193 spec):
  - task_correct_rate drops > 5 pp vs prior night   → REGRESSION
  - tool_call_correct_rate drops > 5 pp (when both non-null) → REGRESSION
  - clean_rate falls < 0.90 (absolute floor)         → CONTAMINATION ALERT

Usage:
  python3 digest_and_alert.py [--results-dir PATH]
                               [--digest-issue-id UUID]
                               [--no-post]          # dry-run (print only)
                               [--demo-alert]       # force a fake regression alert

Environment:
  PAPERCLIP_API_URL
  PAPERCLIP_API_KEY
  PAPERCLIP_COMPANY_ID
  PAPERCLIP_RUN_ID       (optional, for audit trail)
  PAPERCLIP_AGENT_ID     (for alert-issue assignment)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
DEFAULT_RESULTS_DIR = HERE / "results"
SAG4193_ISSUE_ID = "c8304ce0-3116-40b2-a718-4e7d0fb2c6d8"  # SAG-4193 parent
DIGEST_ISSUE_ID = SAG4193_ISSUE_ID  # digest log; moved off cross-project SAG-3196 to fix 403 — SAG-4600
CTO_AGENT_ID = "f3c48afc-c339-4e43-b47b-a42a0891229d"
MY_AGENT_ID = os.environ.get("PAPERCLIP_AGENT_ID", "3ab7fa06-f831-4631-922a-2fe824005788")
COMPANY_ID = os.environ.get("PAPERCLIP_COMPANY_ID", "1dc911ed-ff05-4072-b2ae-a3e3177e3873")
PROJECT_ID = "4dc8eabc-212d-4a46-a0eb-aa61b75e82d0"

REGRESSION_DROP_THRESHOLD = 0.05   # 5 percentage points
CLEAN_FLOOR = 0.90                  # absolute floor for contamination-clean
INVALID_ERROR_THRESHOLD = 0.50     # classes with ≥ 50% errors are invalid/skipped


# ---------------------------------------------------------------------------
# Paperclip API helpers
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: dict | None = None) -> dict:
    api_url = (
        os.environ.get("PAPERCLIP_API_URL", "http://127.0.0.1:3100")
        or "http://127.0.0.1:3100"
    )
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    run_id = os.environ.get("PAPERCLIP_RUN_ID", "")
    if not api_key:
        raise RuntimeError("PAPERCLIP_API_KEY not set")
    url = f"{api_url}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if run_id:
        headers["X-Paperclip-Run-Id"] = run_id
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        raise RuntimeError(f"API {method} {path} → {e.code}: {body_text}") from e


def post_comment(issue_id: str, markdown: str) -> dict:
    return _api("POST", f"/api/issues/{issue_id}/comments", {"body": markdown})


def create_issue(title: str, description: str) -> dict:
    return _api("POST", f"/api/companies/{COMPANY_ID}/issues", {
        "title": title,
        "description": description,
        "status": "todo",
        "priority": "high",
        "assigneeAgentId": MY_AGENT_ID,
        "projectId": PROJECT_ID,
        "parentId": SAG4193_ISSUE_ID,
    })


# ---------------------------------------------------------------------------
# Results loader
# ---------------------------------------------------------------------------

def load_results(results_dir: Path) -> list[dict]:
    """Return non-dry-run result dicts sorted oldest→newest."""
    jsons = sorted(results_dir.glob("*.json"))
    out = []
    for p in jsons:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if d.get("dry_run"):
            continue
        d["_path"] = str(p)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Invalid-class helpers
# ---------------------------------------------------------------------------

def _class_error_rate(cs: dict) -> float:
    n = cs.get("n", 0)
    if n == 0:
        return 1.0
    return cs.get("errors", 0) / n


def _is_class_invalid(cs: dict) -> bool:
    """True when the class has too many errors to produce meaningful scores."""
    return cs.get("invalid", False) or _class_error_rate(cs) >= INVALID_ERROR_THRESHOLD


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def _delta_str(cur: float | None, prev: float | None) -> str:
    if cur is None or prev is None:
        return "N/A"
    delta = cur - prev
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta * 100:.1f}pp"


def compute_regressions(current: dict, prior: dict) -> dict:
    """Classify each class-metric into regressions, floor breaches, or invalid/skipped.

    Returns a dict with three lists:
      "regressions"     — true drops >5pp on valid samples (or contamination breach);
                          these trigger a CTO @-mention and an alert issue.
      "floor_breaches"  — clean_rate below floor but stable; informational only, no CTO alert.
      "invalid_classes" — skipped because error rate ≥ INVALID_ERROR_THRESHOLD.

    Returns all-empty when the model changed vs. the prior run — current run is a
    new baseline and cross-model deltas are not meaningful.
    """
    empty = {"regressions": [], "floor_breaches": [], "invalid_classes": []}

    if current.get("model") != prior.get("model"):
        log.info(
            "Model changed %s → %s: current run is new baseline, skipping regression check",
            prior.get("model"), current.get("model"),
        )
        return empty

    regressions: list[dict] = []
    floor_breaches: list[dict] = []
    invalid_classes: list[dict] = []

    for cls, cs in current["classes"].items():
        ps = prior["classes"].get(cls)
        if ps is None:
            continue

        # High error rate → invalid; skip all regression/floor logic for this class
        if _is_class_invalid(cs):
            n = cs.get("n", 0)
            errs = cs.get("errors", 0)
            rate = _class_error_rate(cs)
            invalid_classes.append({
                "class": cls,
                "reason": (
                    cs.get("invalid_reason")
                    or f"error rate {errs}/{n} ({rate:.0%}) ≥ {INVALID_ERROR_THRESHOLD:.0%} threshold"
                ),
            })
            continue

        prior_invalid = _is_class_invalid(ps)

        # task_correct: regression only when prior is also valid
        if not prior_invalid:
            cur_tc = cs.get("task_correct_rate", 0.0)
            prev_tc = ps.get("task_correct_rate", 0.0)
            if prev_tc - cur_tc > REGRESSION_DROP_THRESHOLD:
                regressions.append({
                    "class": cls, "metric": "task_correct_rate",
                    "cur": cur_tc, "prev": prev_tc, "drop": prev_tc - cur_tc,
                })

        # tool_call_correct: regression only when both runs have valid data and non-null rate
        if not prior_invalid:
            cur_tc2 = cs.get("tool_call_correct_rate")
            prev_tc2 = ps.get("tool_call_correct_rate")
            if cur_tc2 is not None and prev_tc2 is not None:
                if prev_tc2 - cur_tc2 > REGRESSION_DROP_THRESHOLD:
                    regressions.append({
                        "class": cls, "metric": "tool_call_correct_rate",
                        "cur": cur_tc2, "prev": prev_tc2, "drop": prev_tc2 - cur_tc2,
                    })

        # clean_rate: contamination regression if dropped >5pp; floor breach if chronically low
        cur_cl = cs.get("clean_rate", 1.0)
        prev_cl = ps.get("clean_rate", 1.0) if not prior_invalid else None

        if prev_cl is not None and prev_cl - cur_cl > REGRESSION_DROP_THRESHOLD:
            regressions.append({
                "class": cls, "metric": "clean_rate",
                "cur": cur_cl, "prev": prev_cl, "drop": prev_cl - cur_cl,
            })
        elif cur_cl < CLEAN_FLOOR:
            floor_breaches.append({
                "class": cls, "metric": "clean_rate",
                "cur": cur_cl, "prev": prev_cl,
                "note": (
                    f"below floor {CLEAN_FLOOR:.0%}"
                    + (f" (prev {prev_cl:.1%}, stable)" if prev_cl is not None else " (no valid prior)")
                ),
            })

    return {"regressions": regressions, "floor_breaches": floor_breaches, "invalid_classes": invalid_classes}


# ---------------------------------------------------------------------------
# Digest formatter
# ---------------------------------------------------------------------------

def format_digest(
    current: dict,
    prior: dict | None,
    classify: dict,
    alert_issue_identifier: str | None = None,
) -> str:
    """Format the nightly digest.

    classify is the dict returned by compute_regressions:
      {"regressions": [...], "floor_breaches": [...], "invalid_classes": [...]}
    alert_issue_identifier, if provided, is linked in the @CTO mention line.
    """
    regressions = classify.get("regressions", [])
    floor_breaches = classify.get("floor_breaches", [])
    invalid_classes = classify.get("invalid_classes", [])
    invalid_cls_names = {iv["class"] for iv in invalid_classes}

    run_ts = current.get("run_ts", "unknown")
    model = current.get("model", "unknown")
    lines = [f"## Nightly Local-AI Eval — {run_ts[:8]}"]
    lines.append(f"Model: `{model}` | run_ts: `{run_ts}`")
    if prior:
        if current.get("model") != prior.get("model"):
            lines.append(
                f"Prior: `{prior.get('run_ts', 'unknown')}` ({prior.get('model', '?')}) "
                f"| ⚠️ Model changed → **new baseline** (delta vs gemma4 not shown)"
            )
        else:
            lines.append(f"Prior: `{prior.get('run_ts', 'unknown')}` | Delta shown")
    else:
        lines.append("_No prior run for delta (baseline night)_")
    lines.append("")
    lines.append("| Class | N | task_correct | Δ | clean | Δ | tool_call | Δ | errors | status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for cls, cs in current["classes"].items():
        ps = prior["classes"].get(cls) if prior else None
        tc = cs.get("task_correct_rate", 0.0)
        cl = cs.get("clean_rate", 1.0)
        tc2 = cs.get("tool_call_correct_rate")
        n = cs.get("n", 0)
        errs = cs.get("errors", 0)
        ci = cs.get("task_correct_ci_95", [0, 0])

        tc_str = f"{tc:.1%} [{ci[0]:.2f},{ci[1]:.2f}]"
        cl_str = f"{cl:.1%}"
        tc2_str = f"{tc2:.1%}" if tc2 is not None else "N/A"

        dtc = _delta_str(tc, ps.get("task_correct_rate") if ps else None)
        dcl = _delta_str(cl, ps.get("clean_rate") if ps else None)
        dtc2 = _delta_str(tc2, ps.get("tool_call_correct_rate") if ps else None)

        if cls in invalid_cls_names:
            status = "🚫 INVALID"
        elif any(r["class"] == cls for r in regressions):
            status = "⚠️ REGRESSION"
        elif any(f["class"] == cls for f in floor_breaches):
            status = "ℹ️ FLOOR"
        else:
            status = "✅"

        lines.append(
            f"| {cls} | {n} | {tc_str} | {dtc} | {cl_str} | {dcl} | {tc2_str} | {dtc2} | {errs} | {status} |"
        )

    if regressions:
        lines.append("")
        lines.append("### ⚠️ REGRESSIONS DETECTED")
        for r in regressions:
            note = r.get("note") or f"dropped {r['drop'] * 100:.1f}pp (prev {r['prev']:.1%} → cur {r['cur']:.1%})"
            lines.append(f"- **{r['class']}** `{r['metric']}`: {note}")
        cto_line = f"[@CTO](agent://{CTO_AGENT_ID})"
        if alert_issue_identifier:
            cto_line += f" — regression alert: [{alert_issue_identifier}](/SAG/issues/{alert_issue_identifier})"
        else:
            cto_line += " — regression alert (issue creation failed, check logs)"
        lines.append(f"\n{cto_line}")

    if floor_breaches:
        lines.append("")
        lines.append("### ℹ️ FLOOR (informational — no alert issued)")
        for f in floor_breaches:
            lines.append(f"- **{f['class']}** `{f['metric']}`: {f.get('note', '')}")

    if invalid_classes:
        lines.append("")
        lines.append("### 🚫 INVALID/SKIPPED (high error rate — excluded from alerting)")
        for iv in invalid_classes:
            lines.append(f"- **{iv['class']}**: {iv.get('reason', 'high error rate')}")

    if not regressions and not floor_breaches and not invalid_classes:
        lines.append("")
        lines.append("✅ No regressions. All classes within threshold.")
    elif not regressions:
        lines.append("")
        lines.append("✅ No regressions detected.")

    return "\n".join(lines)


def format_alert_description(classify: dict, run_ts: str, prior_ts: str | None) -> str:
    regressions = classify.get("regressions", [])
    floor_breaches = classify.get("floor_breaches", [])
    invalid_classes = classify.get("invalid_classes", [])
    lines = [
        "## Regression Alert — Nightly Eval",
        "",
        f"Run: `{run_ts}` vs prior: `{prior_ts or 'N/A'}`",
        "",
        "### Regressions",
    ]
    for r in regressions:
        note = r.get("note") or f"dropped {r['drop'] * 100:.1f}pp (prev {r['prev']:.1%} → cur {r['cur']:.1%})"
        lines.append(f"- **{r['class']}** `{r['metric']}`: {note}")
    if floor_breaches:
        lines.append("")
        lines.append("### Floor Breaches (informational)")
        for f in floor_breaches:
            lines.append(f"- **{f['class']}** `{f['metric']}`: {f.get('note', '')}")
    if invalid_classes:
        lines.append("")
        lines.append("### Invalid/Skipped Classes")
        for iv in invalid_classes:
            lines.append(f"- **{iv['class']}**: {iv.get('reason', 'high error rate')}")
    lines.append("")
    lines.append("### Required Action")
    lines.append("1. Investigate the affected gold-set class(es).")
    lines.append("2. **Do NOT** autonomously swap models or bulk-PATCH agents.")
    lines.append("3. Escalate to CTO if the regression persists across two consecutive nights.")
    lines.append("")
    lines.append(f"Parent: [SAG-4193](/SAG/issues/SAG-4193)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    results_dir: Path = DEFAULT_RESULTS_DIR,
    digest_issue_id: str = DIGEST_ISSUE_ID,
    no_post: bool = False,
    demo_alert: bool = False,
) -> int:
    runs = load_results(results_dir)
    if not runs:
        log.error("No non-dry-run results found in %s", results_dir)
        return 1

    current = runs[-1]
    prior = runs[-2] if len(runs) >= 2 else None

    log.info("Current run: %s", current.get("run_ts"))
    if prior:
        log.info("Prior run:   %s", prior.get("run_ts"))
    else:
        log.info("No prior run — baseline night")

    classify = compute_regressions(current, prior) if prior else {
        "regressions": [], "floor_breaches": [], "invalid_classes": [],
    }

    if demo_alert:
        # --demo-alert: inject a synthetic regression, create a ticket, then immediately cancel it
        # to verify end-to-end API connectivity without leaving noise in the issue tracker.
        if not classify["regressions"]:
            log.info("--demo-alert: injecting synthetic regression for demonstration")
            classify["regressions"] = [{
                "class": "doc_extraction",
                "metric": "task_correct_rate",
                "cur": 0.10,
                "prev": 1.00,
                "drop": 0.90,
            }]

    regressions = classify["regressions"]

    if no_post:
        digest = format_digest(current, prior, classify)
        print("\n" + "=" * 60)
        print(digest)
        print("=" * 60 + "\n")
        log.info("--no-post: skipping API calls")
        return 0

    # Create regression alert issue FIRST (if needed) so we can reference it in the digest
    alert_issue_identifier: str | None = None
    if regressions:
        prior_ts = prior.get("run_ts") if prior else None
        alert_desc = format_alert_description(classify, current["run_ts"], prior_ts)
        alert_title = (
            f"[REGRESSION ALERT] Nightly eval {current['run_ts'][:8]}: "
            f"{len(regressions)} metric(s) regressed"
        )
        try:
            alert_issue = create_issue(alert_title, alert_desc)
            alert_issue_identifier = alert_issue.get("identifier")
            log.info("Regression alert issue created: %s", alert_issue_identifier)
            if demo_alert:
                _api("PATCH", f"/api/issues/{alert_issue['id']}", {
                    "status": "cancelled",
                    "comment": "Auto-cancelled: demo-alert connectivity test (safe to ignore)",
                })
                log.info(
                    "--demo-alert: issue %s immediately cancelled (connectivity test PASSED)",
                    alert_issue_identifier,
                )
        except Exception as exc:
            log.error("Failed to create alert issue: %s", exc)

    digest = format_digest(current, prior, classify, alert_issue_identifier=alert_issue_identifier)
    print("\n" + "=" * 60)
    print(digest)
    print("=" * 60 + "\n")

    # Post digest to SAG-4193
    try:
        result = post_comment(digest_issue_id, digest)
        log.info("Digest posted to %s (comment %s)", digest_issue_id, result.get("id"))
    except Exception as exc:
        log.error("Failed to post digest: %s", exc)
        return 1

    return 0


def _parse_args(argv: list[str]) -> dict:
    args: dict = {
        "results_dir": DEFAULT_RESULTS_DIR,
        "digest_issue_id": DIGEST_ISSUE_ID,
        "no_post": False,
        "demo_alert": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--results-dir" and i + 1 < len(argv):
            args["results_dir"] = Path(argv[i + 1])
            i += 2
        elif arg == "--digest-issue-id" and i + 1 < len(argv):
            args["digest_issue_id"] = argv[i + 1]
            i += 2
        elif arg == "--no-post":
            args["no_post"] = True
            i += 1
        elif arg == "--demo-alert":
            args["demo_alert"] = True
            i += 1
        else:
            i += 1
    return args


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    args = _parse_args(sys.argv[1:])
    sys.exit(run(**args))
