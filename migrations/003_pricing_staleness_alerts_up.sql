-- Migration 003 (UP): pricing_staleness_alerts table, roles, permissions
-- SAG-6327 Phase 1 | Parent: SAG-6302
--
-- Append-only detection-alert log for the pricing staleness runner. Lives in
-- the existing 'enrichment_staging' schema, fully isolated from the
-- production catalog (public schema). No FK references to production or to
-- any pricing feed table (sku/bucket_code/schedule_id are plain text
-- identifiers). Role permissions enforce INSERT/SELECT-only (no UPDATE /
-- DELETE granted to any role) -- append-only, matching the
-- enrichment_promotion_log precedent from migration 001.

BEGIN;

-- ─── pricing_staleness_alerts ──────────────────────────────────────────────
-- One row per detection event, written by the nightly detection runner
-- (SAG-6327 Phase 3+4). signal_type enumerates the four detection signals
-- from the SAG-6302 plan; severity is a coarse triage tier for the nightly
-- digest. auto_issue_id records the Paperclip issue identifier if the
-- detection escalated to one (nullable -- not every alert escalates).

CREATE TABLE enrichment_staging.pricing_staleness_alerts (
    id                     UUID        NOT NULL DEFAULT gen_random_uuid(),
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_type            TEXT        NOT NULL
        CHECK (signal_type IN (
            'anomaly',
            'version_hash_drift',
            'manual_change_sla_breach',
            'bulk_escalator'
        )),
    severity               TEXT        NOT NULL
        CHECK (severity IN ('info', 'warning', 'critical')),
    sku                    TEXT        NOT NULL,
    bucket_code            TEXT        NOT NULL,
    schedule_id            TEXT,
    affected_record_count  INTEGER     NOT NULL CHECK (affected_record_count > 0),
    measured_vs_baseline   NUMERIC(8, 4),
    auto_issue_id          TEXT,

    CONSTRAINT pricing_staleness_alerts_pkey PRIMARY KEY (id)
);

CREATE INDEX pricing_staleness_alerts_detected_at_idx
    ON enrichment_staging.pricing_staleness_alerts (detected_at);

-- Serves the Phase 5 freeze-arming check: ">=1 clean baseline median per
-- (SKU, bucket)" is a query against this table keyed on (sku, bucket_code).
CREATE INDEX pricing_staleness_alerts_sku_bucket_idx
    ON enrichment_staging.pricing_staleness_alerts (sku, bucket_code);

-- ─── Roles ───────────────────────────────────────────────────────────────────
-- Create roles only if they don't already exist (idempotent via DO block).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pricing_staleness_writer') THEN
        CREATE ROLE pricing_staleness_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pricing_staleness_reader') THEN
        CREATE ROLE pricing_staleness_reader NOLOGIN;
    END IF;
END
$$;

-- ─── Permissions: enrichment_staging schema ──────────────────────────────────

GRANT USAGE ON SCHEMA enrichment_staging
    TO pricing_staleness_writer, pricing_staleness_reader;

-- pricing_staleness_writer: the nightly detection runner. INSERT + SELECT
--   only (append-only; no UPDATE/DELETE granted to any role).
GRANT SELECT, INSERT
    ON TABLE enrichment_staging.pricing_staleness_alerts
    TO pricing_staleness_writer;

-- pricing_staleness_reader: digest/QA/freeze-arming consumers. SELECT-only.
GRANT SELECT
    ON TABLE enrichment_staging.pricing_staleness_alerts
    TO pricing_staleness_reader;

-- ─── Negative isolation: no write access to public (production) schema ────────
-- Belt-and-suspenders: revoke CREATE on public so these roles cannot create
-- objects there, and they carry no DML grants on public tables.
REVOKE CREATE ON SCHEMA public FROM pricing_staleness_writer;
REVOKE CREATE ON SCHEMA public FROM pricing_staleness_reader;

REVOKE INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    FROM pricing_staleness_writer;
REVOKE INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    FROM pricing_staleness_reader;

COMMIT;
