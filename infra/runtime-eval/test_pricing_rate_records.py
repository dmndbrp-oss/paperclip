from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pricing_rate_records import (
    RateRecordImportError,
    canonical_decimal,
    canonical_record_key,
    canonical_rate_payload,
    content_hash_for_record,
    import_jsonl,
    import_rate_records,
    parse_imported_at,
)


UTC = timezone.utc
FIRST_IMPORT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
SECOND_IMPORT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _row(**overrides):
    row = {
        "record_key": "Countertops:FG3:Central-TX",
        "product_estimate_group": "Countertops",
        "fee_bucket": "FG3",
        "territory": "Central-TX",
        "fee_per_sqft": "12.50",
        "cost_basis_per_sqft": "8.10",
        "install_adder_per_sqft": "0",
        "effective_at": "2026-08-01T00:00:00Z",
        "source": "pricing-feed",
        "last_verified_at": "2026-08-02T00:00:00Z",
        "notes": "approved",
    }
    row.update(overrides)
    return row


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((sql, params))
        if self.connection.fail_on_execute or self.connection.fail_after_execute == len(self.connection.executed):
            raise RuntimeError("database write failed")
        if "FOR UPDATE" in sql:
            self._result = self.connection.active.get(params["record_key"])
        elif "pricing_rate_records" in sql and sql.lstrip().startswith("INSERT"):
            self.connection.active[params["record_key"]] = {
                key: params[key]
                for key in (
                    "content_hash",
                    "rate_card_version",
                    "imported_at",
                    "fee_per_sqft",
                    "cost_basis_per_sqft",
                    "install_adder_per_sqft",
                    "source",
                    "notes",
                )
            }
        elif "pricing_rate_record_imports" in sql:
            key = (params["record_key"], params["imported_at"])
            if key in self.connection.history:
                raise AssertionError("duplicate import history row")
            self.connection.history[key] = params.copy()

    def fetchone(self):
        return self._result


class FakeConnection:
    def __init__(self):
        self.active = {}
        self.history = {}
        self.executed = []
        self.fail_on_execute = False
        self.fail_after_execute = None
        self.commits = 0
        self.rollbacks = 0
        self._snapshot = None

    def __enter__(self):
        self._snapshot = (self.active.copy(), self.history.copy())
        return self

    def __exit__(self, exc_type, *_args):
        if exc_type:
            self.active, self.history = self._snapshot
            self.rollbacks += 1
        else:
            self.commits += 1
        return False

    def cursor(self):
        return FakeCursor(self)


class TestCanonicalForm:
    def test_record_key_is_colon_only_and_rejects_invalid_dimensions(self):
        assert canonical_record_key("Countertops", "FG3", "Central-TX") == "Countertops:FG3:Central-TX"
        for dimensions in [("", "FG3", "TX"), ("Counter:tops", "FG3", "TX"), ("Countertops", "FG3", " ")]:
            with pytest.raises(RateRecordImportError):
                canonical_record_key(*dimensions)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("12.500", "12.5"),
            (Decimal("0.0100"), "0.01"),
            (Decimal("1E+3"), "1000"),
            (Decimal("-0.000"), "0"),
        ],
    )
    def test_decimal_is_finite_and_non_exponent(self, value, expected):
        assert canonical_decimal(value) == expected

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "not-a-number"])
    def test_decimal_rejects_non_finite_or_invalid_values(self, value):
        with pytest.raises(RateRecordImportError):
            canonical_decimal(value)

    def test_hash_uses_fixed_compact_payload_order_and_excludes_metadata(self):
        first = _row(source="first", notes="one", effective_at="2026-01-01T00:00:00Z")
        second = _row(source="second", notes="two", effective_at="2026-02-01T00:00:00Z")

        assert canonical_rate_payload(first) == (
            '{"record_key":"Countertops:FG3:Central-TX","fee_per_sqft":"12.5",'
            '"cost_basis_per_sqft":"8.1","install_adder_per_sqft":"0"}'
        )
        assert content_hash_for_record(first) == content_hash_for_record(second)
        assert content_hash_for_record(first) != content_hash_for_record(_row(fee_per_sqft="12.51"))


class TestValidationAndTransaction:
    @pytest.mark.parametrize(
        "bad_row",
        [
            _row(record_key="wrong:key"),
            _row(product_estimate_group="Counter:tops"),
            _row(fee_per_sqft="NaN"),
            _row(effective_at="not-a-time"),
            {key: value for key, value in _row().items() if key != "territory"},
        ],
    )
    def test_invalid_rows_do_not_open_a_transaction(self, bad_row):
        conn = FakeConnection()

        with pytest.raises(RateRecordImportError):
            import_rate_records(conn, [bad_row], imported_at=FIRST_IMPORT)

        assert conn.commits == conn.rollbacks == 0
        assert conn.executed == []

    def test_duplicate_keys_are_rejected_before_any_transaction(self):
        conn = FakeConnection()

        with pytest.raises(RateRecordImportError, match="duplicate"):
            import_rate_records(conn, [_row(), _row(notes="metadata differs")], imported_at=FIRST_IMPORT)

        assert conn.commits == conn.rollbacks == 0

    def test_database_failure_rolls_back_the_entire_batch(self):
        conn = FakeConnection()
        # The first row is already written when the second row lookup fails.
        conn.fail_after_execute = 4

        with pytest.raises(RuntimeError, match="database write failed"):
            import_rate_records(conn, [_row(), _row(record_key="Cabinets:FG3:TX", product_estimate_group="Cabinets", territory="TX")], imported_at=FIRST_IMPORT)

        assert conn.rollbacks == 1
        assert conn.active == conn.history == {}


class TestVersionSemantics:
    def test_new_record_starts_at_version_one_and_creates_history(self):
        conn = FakeConnection()

        assert import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT) == {"inserted": 1, "unchanged": 0, "updated": 0}
        active = conn.active["Countertops:FG3:Central-TX"]
        assert active["rate_card_version"] == 1
        assert len(conn.history) == 1

    def test_metadata_only_change_is_unchanged_but_records_new_observation(self):
        conn = FakeConnection()
        import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT)

        result = import_rate_records(conn, [_row(source="re-verified", notes="metadata changed")], imported_at=SECOND_IMPORT)

        active = conn.active["Countertops:FG3:Central-TX"]
        assert result == {"inserted": 0, "unchanged": 1, "updated": 0}
        assert active["rate_card_version"] == 1
        assert active["source"] == "re-verified"
        assert len(conn.history) == 2

    def test_rate_change_increments_version_exactly_once(self):
        conn = FakeConnection()
        import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT)

        result = import_rate_records(conn, [_row(fee_per_sqft="13.00")], imported_at=SECOND_IMPORT)

        assert result == {"inserted": 0, "unchanged": 0, "updated": 1}
        assert conn.active["Countertops:FG3:Central-TX"]["rate_card_version"] == 2
        assert conn.history[("Countertops:FG3:Central-TX", SECOND_IMPORT)]["rate_card_version"] == 2

    def test_out_of_order_observation_is_rejected_without_transaction(self):
        conn = FakeConnection()
        import_rate_records(conn, [_row()], imported_at=SECOND_IMPORT)

        with pytest.raises(RateRecordImportError, match="out-of-order"):
            import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT)

        assert conn.commits == 1
        assert len(conn.history) == 1
        assert sum("INSERT INTO enrichment_staging.pricing_rate_records" in sql for sql, _ in conn.executed) == 1
        assert sum("INSERT INTO enrichment_staging.pricing_rate_record_imports" in sql for sql, _ in conn.executed) == 1

    def test_same_timestamp_same_payload_is_idempotent(self):
        conn = FakeConnection()
        import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT)

        assert import_rate_records(conn, [_row(notes="ignored repeat metadata")], imported_at=FIRST_IMPORT) == {
            "inserted": 0,
            "unchanged": 1,
            "updated": 0,
        }
        assert conn.commits == 2  # replay still locks/reads the active row atomically
        assert len(conn.history) == 1
        assert sum("INSERT INTO enrichment_staging.pricing_rate_records" in sql for sql, _ in conn.executed) == 1
        assert sum("INSERT INTO enrichment_staging.pricing_rate_record_imports" in sql for sql, _ in conn.executed) == 1

    def test_same_timestamp_different_payload_is_rejected(self):
        conn = FakeConnection()
        import_rate_records(conn, [_row()], imported_at=FIRST_IMPORT)

        with pytest.raises(RateRecordImportError, match="same timestamp"):
            import_rate_records(conn, [_row(fee_per_sqft="99")], imported_at=FIRST_IMPORT)


class TestJsonlCliBoundary:
    def test_explicit_utc_timestamp_and_jsonl_import(self, tmp_path):
        path = Path(tmp_path) / "rates.jsonl"
        path.write_text('{"record_key":"Countertops:FG3:Central-TX","product_estimate_group":"Countertops","fee_bucket":"FG3","territory":"Central-TX","fee_per_sqft":12.5,"cost_basis_per_sqft":8.1,"install_adder_per_sqft":0}\n')
        conn = FakeConnection()

        assert parse_imported_at("2026-08-05T12:00:00Z") == FIRST_IMPORT
        assert import_jsonl(conn, path, imported_at=FIRST_IMPORT) == {"inserted": 1, "unchanged": 0, "updated": 0}
        with pytest.raises(RateRecordImportError):
            parse_imported_at("2026-08-05T12:00:00")
