"""Alert-sink contract for the pricing staleness-detection runner (SAG-6344).

The `enrichment_staging.pricing_staleness_alerts` append-only table (SAG-6327
Phase 1) landed in migration `003_pricing_staleness_alerts_up.sql` (commit
b35be578). Its columns (`sku`, `bucket_code`, `schedule_id`,
`affected_record_count`, `measured_vs_baseline`, plus CHECK-constrained
`signal_type`/`severity` enums) were fixed by the epic's acceptance criteria
before the Phase-0 feed/runner code existed, so the runner's own
`StalenessAlert` shape (`record_key`, free-form `severity`/`signal_type`
strings, a `details` dict) does not match the table 1:1. `alert_to_row()`
below is the single reconciliation point between the two: it splits
`record_key` into `sku`/`bucket_code`, maps runner severity/signal_type
strings onto the table's CHECK-constrained values, and pulls the
signal-specific numeric fields (`pct_delta`, `count`, `rate_card_version`)
out of `details` into their matching columns. `warm_up` is intentionally not
persisted as a column -- it is a pure function of `detected_at` vs the known
warm-up window (see `is_warm_up()` in pricing_staleness_runner.py), so
readers derive it instead of trusting a stored flag.

Per the epic's established loud-fail pattern (see pricing_feeds.py Phase 0),
`NotImplementedAlertSink` raises rather than silently dropping alerts on the
floor -- used only if a caller constructs the runner without a DSN and
without opting into the in-memory fakes.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import psycopg2

# Runner-side severity strings -> DB CHECK-constrained values (migration 003).
_SEVERITY_TO_DB = {
    "info": "info",
    "warn": "warning",
    "warning": "warning",
    "critical": "critical",
}

# Runner-side signal_type strings -> DB CHECK-constrained values (migration 003).
_SIGNAL_TYPE_TO_DB = {
    "anomaly": "anomaly",
    "version_hash_drift": "version_hash_drift",
    "sla_breach": "manual_change_sla_breach",
    "bulk_escalation": "bulk_escalator",
}


class AlertSinkUnavailableError(RuntimeError):
    """Raised when the real alert sink is queried before its physical table exists.

    Callers must handle this by escalating, not by treating it as "alert written."
    """


@dataclass(frozen=True)
class StalenessAlert:
    """One detection event, in the detection runner's own working shape.

    `record_key` identifies the affected rate record or negotiated-rate change
    (e.g. "product_estimate_group|bucket_code|territory" for `RateRecord`-derived
    signals, or an opaque feed-supplied key for change-feed-derived signals).
    `details` carries signal-specific evidence (pct_delta, versions, due/committed
    timestamps, etc.); `alert_to_row()` extracts the subset that has a home in the
    `pricing_staleness_alerts` table columns.
    """

    signal_type: str
    severity: str
    record_key: str
    detected_at: datetime
    warm_up: bool
    details: dict[str, Any]
    id: Optional[str] = None


def _split_record_key(record_key: str) -> tuple[str, str]:
    """Split a "|"-joined record_key into (sku, bucket_code).

    Falls back to using the whole key for both columns when the key isn't in
    the pipe-delimited form (e.g. opaque change-feed keys, or the "MULTIPLE"
    sentinel used by the bulk-escalation meta-signal) -- `bucket_code` is
    NOT NULL in the table, so there is always a value to write.
    """
    parts = record_key.split("|")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return record_key, record_key


def alert_to_row(alert: StalenessAlert) -> dict[str, Any]:
    """Map a `StalenessAlert` onto the exact column set of
    `enrichment_staging.pricing_staleness_alerts` (migration 003)."""
    try:
        db_signal_type = _SIGNAL_TYPE_TO_DB[alert.signal_type]
    except KeyError:
        raise ValueError(f"Unknown signal_type for DB mapping: {alert.signal_type!r}") from None
    try:
        db_severity = _SEVERITY_TO_DB[alert.severity]
    except KeyError:
        raise ValueError(f"Unknown severity for DB mapping: {alert.severity!r}") from None

    sku, bucket_code = _split_record_key(alert.record_key)

    return {
        "detected_at": alert.detected_at,
        "signal_type": db_signal_type,
        "severity": db_severity,
        "sku": sku,
        "bucket_code": bucket_code,
        "schedule_id": alert.details.get("rate_card_version"),
        "affected_record_count": alert.details.get("count", 1),
        "measured_vs_baseline": alert.details.get("pct_delta"),
        "auto_issue_id": None,
    }


class AlertSink(ABC):
    @abstractmethod
    def write_alert(self, alert: StalenessAlert) -> None:
        """Persist one alert. Must raise rather than silently drop on failure."""


class NotImplementedAlertSink(AlertSink):
    def write_alert(self, alert: StalenessAlert) -> None:
        raise AlertSinkUnavailableError(
            "No pricing-staleness DB DSN configured (PRICING_STALENESS_DB_DSN unset) "
            "and --use-fakes not passed; refusing to silently drop this alert."
        )


@dataclass
class InMemoryAlertSink(AlertSink):
    """In-memory sink for detection-runner development/tests (SAG-6344)."""

    alerts: list[StalenessAlert] = field(default_factory=list)

    def write_alert(self, alert: StalenessAlert) -> None:
        if alert.id is None:
            alert = StalenessAlert(
                signal_type=alert.signal_type,
                severity=alert.severity,
                record_key=alert.record_key,
                detected_at=alert.detected_at,
                warm_up=alert.warm_up,
                details=alert.details,
                id=str(uuid.uuid4()),
            )
        self.alerts.append(alert)


class PostgresAlertSink(AlertSink):
    """Writes alerts into `enrichment_staging.pricing_staleness_alerts` (migration 003).

    Expects a DSN authenticating as the `pricing_staleness_writer` role
    (SELECT+INSERT only; append-only, no UPDATE/DELETE granted -- see
    migrations/README.md). One connection is opened at construction and
    reused for the life of the sink (one nightly run = one connection).
    """

    def __init__(self, dsn: str):
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True

    def write_alert(self, alert: StalenessAlert) -> None:
        row = alert_to_row(alert)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO enrichment_staging.pricing_staleness_alerts
                    (detected_at, signal_type, severity, sku, bucket_code,
                     schedule_id, affected_record_count, measured_vs_baseline, auto_issue_id)
                VALUES
                    (%(detected_at)s, %(signal_type)s, %(severity)s, %(sku)s, %(bucket_code)s,
                     %(schedule_id)s, %(affected_record_count)s, %(measured_vs_baseline)s, %(auto_issue_id)s)
                """,
                row,
            )

    def close(self) -> None:
        self._conn.close()
