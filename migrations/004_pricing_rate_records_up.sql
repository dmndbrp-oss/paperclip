-- Migration 004 (UP): Pricing rate records, import history, and permissions
-- SAG-6343 Task 1 | Parent: SAG-6327
--
-- Stores Pricing's canonical rate-record grain in enrichment_staging. The
-- active table holds the current value; the import table is an append-only
-- observation history for freshness and ordered staleness queries.

BEGIN;

-- ─── Active Pricing rate records ────────────────────────────────────────────

CREATE TABLE enrichment_staging.pricing_rate_records (
    record_key                TEXT        NOT NULL,
    product_estimate_group    TEXT        NOT NULL,
    fee_bucket                TEXT        NOT NULL,
    territory                 TEXT        NOT NULL,
    fee_per_sqft              NUMERIC     NOT NULL,
    cost_basis_per_sqft       NUMERIC     NOT NULL,
    install_adder_per_sqft    NUMERIC     NOT NULL,
    rate_card_version         INTEGER     NOT NULL CHECK (rate_card_version > 0),
    content_hash              TEXT        NOT NULL,
    imported_at               TIMESTAMPTZ NOT NULL,
    effective_at              TIMESTAMPTZ,
    source                    TEXT,
    last_verified_at          TIMESTAMPTZ,
    notes                     TEXT,

    CONSTRAINT pricing_rate_records_pkey PRIMARY KEY (record_key),
    CONSTRAINT pricing_rate_records_dimensions_key
        UNIQUE (product_estimate_group, fee_bucket, territory),
    CONSTRAINT pricing_rate_records_record_key_check
        CHECK (record_key = product_estimate_group || ':' || fee_bucket || ':' || territory)
);

-- ─── Append-only import observations ────────────────────────────────────────

CREATE TABLE enrichment_staging.pricing_rate_record_imports (
    id                        UUID        NOT NULL DEFAULT gen_random_uuid(),
    record_key                TEXT        NOT NULL,
    product_estimate_group    TEXT        NOT NULL,
    fee_bucket                TEXT        NOT NULL,
    territory                 TEXT        NOT NULL,
    fee_per_sqft              NUMERIC     NOT NULL,
    cost_basis_per_sqft       NUMERIC     NOT NULL,
    install_adder_per_sqft    NUMERIC     NOT NULL,
    rate_card_version         INTEGER     NOT NULL CHECK (rate_card_version > 0),
    content_hash              TEXT        NOT NULL,
    imported_at               TIMESTAMPTZ NOT NULL,

    CONSTRAINT pricing_rate_record_imports_pkey PRIMARY KEY (id),
    CONSTRAINT pricing_rate_record_imports_record_key_imported_at_key
        UNIQUE (record_key, imported_at),
    CONSTRAINT pricing_rate_record_imports_record_key_check
        CHECK (record_key = product_estimate_group || ':' || fee_bucket || ':' || territory)
);

-- The UNIQUE (record_key, imported_at) constraint creates the ordered history
-- index; this separate index serves freshness scans across all pricing grains.
CREATE INDEX pricing_rate_record_imports_imported_at_idx
    ON enrichment_staging.pricing_rate_record_imports (imported_at);

-- ─── Roles ──────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pricing_rate_importer') THEN
        CREATE ROLE pricing_rate_importer NOLOGIN;
    END IF;
END
$$;

-- ─── Permissions: enrichment_staging schema ─────────────────────────────────

GRANT USAGE ON SCHEMA enrichment_staging
    TO pricing_rate_importer, pricing_staleness_reader;

-- Do not inherit privileges through PUBLIC or a pre-existing direct grant.
REVOKE ALL ON TABLE enrichment_staging.pricing_rate_records,
                    enrichment_staging.pricing_rate_record_imports
    FROM PUBLIC, pricing_rate_importer, pricing_staleness_reader;

-- The importer can atomically upsert active records and append observations.
-- DELETE is deliberately not granted on either table.
GRANT SELECT, INSERT, UPDATE
    ON TABLE enrichment_staging.pricing_rate_records
    TO pricing_rate_importer;
GRANT SELECT, INSERT
    ON TABLE enrichment_staging.pricing_rate_record_imports
    TO pricing_rate_importer;

-- Staleness detection consumes current and historical rate records only.
GRANT SELECT
    ON TABLE enrichment_staging.pricing_rate_records,
             enrichment_staging.pricing_rate_record_imports
    TO pricing_staleness_reader;

-- ─── Negative isolation: no public-schema writes ────────────────────────────

REVOKE CREATE ON SCHEMA public
    FROM pricing_rate_importer, pricing_staleness_reader;

REVOKE INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    FROM pricing_rate_importer, pricing_staleness_reader;

COMMIT;
