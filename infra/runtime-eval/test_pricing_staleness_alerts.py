from datetime import datetime
from unittest.mock import MagicMock

import pytest

import pricing_staleness_alerts
from pricing_staleness_alerts import (
    AlertSinkUnavailableError,
    InMemoryAlertSink,
    NotImplementedAlertSink,
    PostgresAlertSink,
    StalenessAlert,
    alert_to_row,
)

NOW = datetime(2026, 7, 7, 12, 0, 0)


def _alert(**overrides) -> StalenessAlert:
    defaults = dict(
        signal_type="anomaly",
        severity="warn",
        record_key="FG3|FQ3-A|TX",
        detected_at=NOW,
        warm_up=True,
        details={"pct_delta": 0.06},
    )
    defaults.update(overrides)
    return StalenessAlert(**defaults)


class TestNotImplementedAlertSink:
    def test_write_alert_raises_sink_unavailable(self):
        with pytest.raises(AlertSinkUnavailableError, match="PRICING_STALENESS_DB_DSN"):
            NotImplementedAlertSink().write_alert(_alert())


class TestInMemoryAlertSink:
    def test_write_alert_appends_and_assigns_id(self):
        sink = InMemoryAlertSink()

        sink.write_alert(_alert())
        sink.write_alert(_alert(signal_type="sla_breach"))

        assert len(sink.alerts) == 2
        assert sink.alerts[0].signal_type == "anomaly"
        assert sink.alerts[1].signal_type == "sla_breach"
        assert sink.alerts[0].id is not None
        assert sink.alerts[0].id != sink.alerts[1].id


# ---------------------------------------------------------------------------
# alert_to_row: reconciliation between the runner's StalenessAlert shape and
# the migrated `pricing_staleness_alerts` table's exact column/CHECK values.
# ---------------------------------------------------------------------------


class TestAlertToRow:
    def test_anomaly_maps_severity_warn_to_warning_and_splits_record_key(self):
        alert = _alert(
            signal_type="anomaly",
            severity="warn",
            record_key="FG3|FQ3-A|TX",
            details={"pct_delta": 0.06, "field": "fee_per_sqft"},
        )

        row = alert_to_row(alert)

        assert row["signal_type"] == "anomaly"
        assert row["severity"] == "warning"
        assert row["sku"] == "FG3"
        assert row["bucket_code"] == "FQ3-A"
        assert row["measured_vs_baseline"] == 0.06
        assert row["affected_record_count"] == 1
        assert row["schedule_id"] is None
        assert row["auto_issue_id"] is None

    def test_version_hash_drift_maps_rate_card_version_to_schedule_id(self):
        alert = _alert(
            signal_type="version_hash_drift",
            severity="critical",
            record_key="FG3|FQ3-A|TX",
            details={"rate_card_version": "v3"},
        )

        row = alert_to_row(alert)

        assert row["signal_type"] == "version_hash_drift"
        assert row["severity"] == "critical"
        assert row["schedule_id"] == "v3"
        assert row["measured_vs_baseline"] is None

    def test_sla_breach_maps_to_manual_change_sla_breach_and_falls_back_on_opaque_key(self):
        alert = _alert(
            signal_type="sla_breach",
            severity="warn",
            record_key="FG3-FQ3-A-TX",
            details={},
        )

        row = alert_to_row(alert)

        assert row["signal_type"] == "manual_change_sla_breach"
        assert row["sku"] == "FG3-FQ3-A-TX"
        assert row["bucket_code"] == "FG3-FQ3-A-TX"

    def test_bulk_escalation_maps_to_bulk_escalator_and_uses_details_count(self):
        alert = _alert(
            signal_type="bulk_escalation",
            severity="critical",
            record_key="MULTIPLE",
            details={"count": 5},
        )

        row = alert_to_row(alert)

        assert row["signal_type"] == "bulk_escalator"
        assert row["affected_record_count"] == 5
        assert row["sku"] == "MULTIPLE"
        assert row["bucket_code"] == "MULTIPLE"

    def test_unknown_signal_type_raises(self):
        alert = _alert(signal_type="nonsense")

        with pytest.raises(ValueError, match="nonsense"):
            alert_to_row(alert)

    def test_unknown_severity_raises(self):
        alert = _alert(severity="nonsense")

        with pytest.raises(ValueError, match="nonsense"):
            alert_to_row(alert)


# ---------------------------------------------------------------------------
# PostgresAlertSink: verified against a mocked psycopg2 connection (no live
# DB in this environment -- see migrations/README.md "Which Postgres
# instance: TBD").
# ---------------------------------------------------------------------------


class TestPostgresAlertSink:
    def test_write_alert_executes_insert_with_mapped_row(self, monkeypatch):
        fake_cursor = MagicMock()
        fake_cursor.__enter__.return_value = fake_cursor
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        monkeypatch.setattr(pricing_staleness_alerts.psycopg2, "connect", lambda dsn: fake_conn)

        sink = PostgresAlertSink(dsn="postgresql://pricing_staleness_writer@localhost/db")
        sink.write_alert(_alert(signal_type="anomaly", severity="warn", details={"pct_delta": 0.06}))

        assert fake_conn.autocommit is True
        assert fake_cursor.execute.called
        sql, params = fake_cursor.execute.call_args.args
        assert "enrichment_staging.pricing_staleness_alerts" in sql
        assert params["signal_type"] == "anomaly"
        assert params["severity"] == "warning"

    def test_close_closes_connection(self, monkeypatch):
        fake_conn = MagicMock()
        monkeypatch.setattr(pricing_staleness_alerts.psycopg2, "connect", lambda dsn: fake_conn)

        sink = PostgresAlertSink(dsn="postgresql://pricing_staleness_writer@localhost/db")
        sink.close()

        fake_conn.close.assert_called_once()
