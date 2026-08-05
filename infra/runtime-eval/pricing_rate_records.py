"""Deterministic, transactional importer for finalized Pricing rate records.

Only the canonical rate-bearing payload contributes to a record's content hash.
The active row is updated atomically with an append-only import observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from pricing_feeds import RateRecord, RateRecordFeed


LOGGER = logging.getLogger(__name__)
RATE_FIELDS = ("fee_per_sqft", "cost_basis_per_sqft", "install_adder_per_sqft")
DIMENSION_FIELDS = ("product_estimate_group", "fee_bucket", "territory")
METADATA_TIMESTAMP_FIELDS = ("effective_at", "last_verified_at")


class RateRecordImportError(ValueError):
    """Raised for input that cannot safely become a Pricing rate observation."""


@dataclass(frozen=True)
class ValidatedRateRecord:
    record_key: str
    product_estimate_group: str
    fee_bucket: str
    territory: str
    fee_per_sqft: Decimal
    cost_basis_per_sqft: Decimal
    install_adder_per_sqft: Decimal
    effective_at: datetime | None
    source: str | None
    last_verified_at: datetime | None
    notes: str | None
    content_hash: str

    def db_params(self, *, imported_at: datetime, rate_card_version: int) -> dict[str, Any]:
        return {
            "record_key": self.record_key,
            "product_estimate_group": self.product_estimate_group,
            "fee_bucket": self.fee_bucket,
            "territory": self.territory,
            "fee_per_sqft": self.fee_per_sqft,
            "cost_basis_per_sqft": self.cost_basis_per_sqft,
            "install_adder_per_sqft": self.install_adder_per_sqft,
            "rate_card_version": rate_card_version,
            "content_hash": self.content_hash,
            "imported_at": imported_at,
            "effective_at": self.effective_at,
            "source": self.source,
            "last_verified_at": self.last_verified_at,
            "notes": self.notes,
        }


def canonical_record_key(product_estimate_group: object, fee_bucket: object, territory: object) -> str:
    """Return the sole supported colon-form key for the finalized record grain."""
    dimensions = (product_estimate_group, fee_bucket, territory)
    if any(not isinstance(value, str) or not value.strip() or ":" in value for value in dimensions):
        raise RateRecordImportError("record dimensions must be non-empty strings without colons")
    return ":".join(dimensions)


def canonical_decimal(value: object) -> str:
    """Return a finite plain-decimal representation with insignificant zeros removed."""
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise RateRecordImportError("rate value must be a valid decimal") from error
    if not decimal_value.is_finite():
        raise RateRecordImportError("rate value must be finite")

    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _decimal(value: object) -> Decimal:
    return Decimal(canonical_decimal(value))


def canonical_rate_payload(record: Mapping[str, object] | ValidatedRateRecord) -> str:
    """Build the compact, ordered JSON bytestring used for content hashing."""
    if isinstance(record, ValidatedRateRecord):
        record_key = record.record_key
        rates = (record.fee_per_sqft, record.cost_basis_per_sqft, record.install_adder_per_sqft)
    else:
        record_key = canonical_record_key(
            record.get("product_estimate_group"), record.get("fee_bucket"), record.get("territory")
        )
        supplied_key = record.get("record_key")
        if supplied_key != record_key:
            raise RateRecordImportError("record_key must match the canonical dimensions")
        try:
            rates = tuple(record[field] for field in RATE_FIELDS)
        except KeyError as error:
            raise RateRecordImportError(f"missing required rate field: {error.args[0]}") from error

    payload = {
        "record_key": record_key,
        "fee_per_sqft": canonical_decimal(rates[0]),
        "cost_basis_per_sqft": canonical_decimal(rates[1]),
        "install_adder_per_sqft": canonical_decimal(rates[2]),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def content_hash_for_record(record: Mapping[str, object] | ValidatedRateRecord) -> str:
    return hashlib.sha256(canonical_rate_payload(record).encode("utf-8")).hexdigest()


def parse_imported_at(value: str) -> datetime:
    """Parse an explicit UTC CLI timestamp, retaining a timezone-aware value."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RateRecordImportError("--imported-at must be an explicit UTC timestamp ending in Z")
    return _parse_utc_timestamp(value, field_name="--imported-at")


def _parse_utc_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RateRecordImportError(f"{field_name} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RateRecordImportError(f"{field_name} must be an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RateRecordImportError(f"{field_name} must be UTC")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(row: Mapping[str, object], field_name: str) -> datetime | None:
    value = row.get(field_name)
    return None if value is None else _parse_utc_timestamp(value, field_name=field_name)


def _optional_text(row: Mapping[str, object], field_name: str) -> str | None:
    value = row.get(field_name)
    if value is not None and not isinstance(value, str):
        raise RateRecordImportError(f"{field_name} must be a string or null")
    return value


def validate_rate_records(rows: Iterable[Mapping[str, object]]) -> list[ValidatedRateRecord]:
    """Validate every row before the caller starts a database transaction."""
    validated: list[ValidatedRateRecord] = []
    seen_keys: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise RateRecordImportError(f"row {index} must be a JSON object")
        try:
            dimensions = tuple(row[field] for field in DIMENSION_FIELDS)
        except KeyError as error:
            raise RateRecordImportError(f"row {index} missing required field: {error.args[0]}") from error
        record_key = canonical_record_key(*dimensions)
        if row.get("record_key") != record_key:
            raise RateRecordImportError(f"row {index} record_key is inconsistent with its dimensions")
        if record_key in seen_keys:
            raise RateRecordImportError(f"row {index} duplicates record_key in this batch")
        seen_keys.add(record_key)
        try:
            rates = tuple(_decimal(row[field]) for field in RATE_FIELDS)
        except KeyError as error:
            raise RateRecordImportError(f"row {index} missing required field: {error.args[0]}") from error

        partial = {
            "record_key": record_key,
            "product_estimate_group": dimensions[0],
            "fee_bucket": dimensions[1],
            "territory": dimensions[2],
            "fee_per_sqft": rates[0],
            "cost_basis_per_sqft": rates[1],
            "install_adder_per_sqft": rates[2],
        }
        validated.append(
            ValidatedRateRecord(
                **partial,
                effective_at=_optional_timestamp(row, "effective_at"),
                source=_optional_text(row, "source"),
                last_verified_at=_optional_timestamp(row, "last_verified_at"),
                notes=_optional_text(row, "notes"),
                content_hash=content_hash_for_record(partial),
            )
        )
    return validated


_ACTIVE_SELECT = """
SELECT content_hash, rate_card_version, imported_at
FROM enrichment_staging.pricing_rate_records
WHERE record_key = %(record_key)s
FOR UPDATE
"""

_ACTIVE_UPSERT = """
INSERT INTO enrichment_staging.pricing_rate_records (
    record_key, product_estimate_group, fee_bucket, territory,
    fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
    rate_card_version, content_hash, imported_at,
    effective_at, source, last_verified_at, notes
) VALUES (
    %(record_key)s, %(product_estimate_group)s, %(fee_bucket)s, %(territory)s,
    %(fee_per_sqft)s, %(cost_basis_per_sqft)s, %(install_adder_per_sqft)s,
    %(rate_card_version)s, %(content_hash)s, %(imported_at)s,
    %(effective_at)s, %(source)s, %(last_verified_at)s, %(notes)s
) ON CONFLICT (record_key) DO UPDATE SET
    fee_per_sqft = EXCLUDED.fee_per_sqft,
    cost_basis_per_sqft = EXCLUDED.cost_basis_per_sqft,
    install_adder_per_sqft = EXCLUDED.install_adder_per_sqft,
    rate_card_version = EXCLUDED.rate_card_version,
    content_hash = EXCLUDED.content_hash,
    imported_at = EXCLUDED.imported_at,
    effective_at = EXCLUDED.effective_at,
    source = EXCLUDED.source,
    last_verified_at = EXCLUDED.last_verified_at,
    notes = EXCLUDED.notes
"""

_HISTORY_INSERT = """
INSERT INTO enrichment_staging.pricing_rate_record_imports (
    record_key, product_estimate_group, fee_bucket, territory,
    fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
    rate_card_version, content_hash, imported_at
) VALUES (
    %(record_key)s, %(product_estimate_group)s, %(fee_bucket)s, %(territory)s,
    %(fee_per_sqft)s, %(cost_basis_per_sqft)s, %(install_adder_per_sqft)s,
    %(rate_card_version)s, %(content_hash)s, %(imported_at)s
)
"""

_HISTORY_AS_OF_SELECT = """
SELECT
    record_key, product_estimate_group, fee_bucket, territory,
    fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
    rate_card_version, imported_at, content_hash
FROM enrichment_staging.pricing_rate_record_imports
WHERE imported_at <= %(as_of)s
ORDER BY record_key ASC, imported_at ASC
"""


class PostgresRateRecordFeed(RateRecordFeed):
    """Read finalized Pricing rate-import observations from PostgreSQL.

    One connection is opened for this feed's lifetime so each detection run
    observes a single, ordered history source. Call ``close`` when the runner
    is finished with the feed.
    """

    def __init__(self, dsn: str):
        import psycopg2

        self._connection = psycopg2.connect(dsn)

    def get_active_rate_records(self, as_of: datetime) -> list[RateRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(_HISTORY_AS_OF_SELECT, {"as_of": as_of})
            rows = cursor.fetchall()
        return [self._to_rate_record(row) for row in rows]

    @staticmethod
    def _to_rate_record(row: Mapping[str, Any] | tuple[Any, ...]) -> RateRecord:
        if isinstance(row, Mapping):
            values = row
        else:
            (
                record_key,
                product_estimate_group,
                fee_bucket,
                territory,
                fee_per_sqft,
                cost_basis_per_sqft,
                install_adder_per_sqft,
                rate_card_version,
                imported_at,
                content_hash,
            ) = row
            values = {
                "record_key": record_key,
                "product_estimate_group": product_estimate_group,
                "fee_bucket": fee_bucket,
                "territory": territory,
                "fee_per_sqft": fee_per_sqft,
                "cost_basis_per_sqft": cost_basis_per_sqft,
                "install_adder_per_sqft": install_adder_per_sqft,
                "rate_card_version": rate_card_version,
                "imported_at": imported_at,
                "content_hash": content_hash,
            }
        return RateRecord(**values)

    def close(self) -> None:
        self._connection.close()


def _active_values(active: Any) -> tuple[str, int, datetime]:
    if isinstance(active, Mapping):
        return active["content_hash"], active["rate_card_version"], active["imported_at"]
    return active[0], active[1], active[2]


def import_rate_records(connection: Any, rows: Iterable[Mapping[str, object]], *, imported_at: datetime) -> dict[str, int]:
    """Atomically import a fully validated batch into active and history tables."""
    if not isinstance(imported_at, datetime) or imported_at.tzinfo is None or imported_at.utcoffset() != timezone.utc.utcoffset(imported_at):
        raise RateRecordImportError("imported_at must be timezone-aware UTC")
    imported_at = imported_at.astimezone(timezone.utc)
    records = validate_rate_records(rows)
    result = {"inserted": 0, "unchanged": 0, "updated": 0}

    with connection:
        with connection.cursor() as cursor:
            for record in records:
                cursor.execute(_ACTIVE_SELECT, {"record_key": record.record_key})
                active = cursor.fetchone()
                if active is None:
                    version = 1
                    result["inserted"] += 1
                else:
                    active_hash, active_version, active_imported_at = _active_values(active)
                    if imported_at < active_imported_at:
                        raise RateRecordImportError("out-of-order observation is older than the active record")
                    if imported_at == active_imported_at:
                        if active_hash != record.content_hash:
                            raise RateRecordImportError("same timestamp has a different rate payload")
                        result["unchanged"] += 1
                        continue
                    if active_hash == record.content_hash:
                        version = active_version
                        result["unchanged"] += 1
                    else:
                        version = active_version + 1
                        result["updated"] += 1

                params = record.db_params(imported_at=imported_at, rate_card_version=version)
                cursor.execute(_ACTIVE_UPSERT, params)
                cursor.execute(_HISTORY_INSERT, params)
    return result


def import_jsonl(connection: Any, input_path: Path | str, *, imported_at: datetime) -> dict[str, int]:
    """Read JSONL without float coercion, then delegate to the transactional importer."""
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(Path(input_path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line, parse_float=Decimal, parse_int=Decimal)
        except json.JSONDecodeError as error:
            raise RateRecordImportError(f"invalid JSON on line {line_number}") from error
        if not isinstance(parsed, Mapping):
            raise RateRecordImportError(f"JSONL line {line_number} must be an object")
        rows.append(parsed)
    return import_rate_records(connection, rows, imported_at=imported_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import finalized Pricing rate records from JSONL.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--imported-at", required=True)
    args = parser.parse_args(argv)
    imported_at = parse_imported_at(args.imported_at)

    import psycopg2

    connection = psycopg2.connect(args.dsn)
    try:
        result = import_jsonl(connection, args.input, imported_at=imported_at)
    finally:
        connection.close()
    LOGGER.info(
        "Pricing rate import complete: inserted=%d unchanged=%d updated=%d",
        result["inserted"], result["unchanged"], result["updated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
