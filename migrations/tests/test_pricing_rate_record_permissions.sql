-- Permission test for Migration 004 (SAG-6343 Task 1)
--
-- Run as a superuser after migrations 001, 003, and 004. The suite is fully
-- transaction-scoped: it creates a unique public-schema probe, tests both
-- roles, then rolls everything back. Exit 0 plus no "UNEXPECTED" output is a
-- pass.
--
-- Usage:
--   psql -v ON_ERROR_STOP=1 -U postgres -d <isolated_db> \
--     -f migrations/tests/test_pricing_rate_record_permissions.sql

\set ON_ERROR_STOP on

BEGIN;

SELECT format('pricing_rate_record_permission_probe_%s', txid_current()) AS probe_name
\gset

CREATE TABLE public.:"probe_name" (
    id INTEGER PRIMARY KEY
);

SELECT set_config('pricing_rate_record_permissions.probe_name', :'probe_name', TRUE);

-- Seed an active record as the migration operator so the importer must take
-- the ON CONFLICT DO UPDATE branch rather than only exercising INSERT.
INSERT INTO enrichment_staging.pricing_rate_records (
    record_key, product_estimate_group, fee_bucket, territory,
    fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
    rate_card_version, content_hash, imported_at
) VALUES (
    'test-group:test-bucket:test-territory', 'test-group', 'test-bucket', 'test-territory',
    12.50, 8.10, 0, 1, repeat('seed', 16), '2025-12-31T00:00:00Z'
);

-- ── Importer expected rights ────────────────────────────────────────────────

SET ROLE pricing_rate_importer;

DO $$
BEGIN
    BEGIN
        INSERT INTO enrichment_staging.pricing_rate_records (
            record_key, product_estimate_group, fee_bucket, territory,
            fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
            rate_card_version, content_hash, imported_at
        ) VALUES (
            'test-group:test-bucket:test-territory', 'test-group', 'test-bucket', 'test-territory',
            12.50, 8.10, 0, 2, repeat('a', 64), '2026-01-01T00:00:00Z'
        ) ON CONFLICT (record_key) DO UPDATE
            SET rate_card_version = EXCLUDED.rate_card_version,
                content_hash = EXCLUDED.content_hash,
                imported_at = EXCLUDED.imported_at;

        IF EXISTS (
            SELECT 1
            FROM enrichment_staging.pricing_rate_records
            WHERE record_key = 'test-group:test-bucket:test-territory'
              AND rate_card_version = 2
              AND imported_at = '2026-01-01T00:00:00Z'
        ) THEN
            RAISE NOTICE 'PASS: pricing_rate_importer active-record upsert UPDATE succeeded.';
        ELSE
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer active-record upsert did not update the seeded row.';
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer active-record upsert failed: %', SQLERRM;
    END;

    BEGIN
        INSERT INTO enrichment_staging.pricing_rate_record_imports (
            record_key, product_estimate_group, fee_bucket, territory,
            fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
            rate_card_version, content_hash, imported_at
        ) VALUES (
            'test-group:test-bucket:test-territory', 'test-group', 'test-bucket', 'test-territory',
            12.50, 8.10, 0, 2, repeat('a', 64), '2026-01-01T00:00:00Z'
        );
        RAISE NOTICE 'PASS: pricing_rate_importer import-history INSERT succeeded.';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer import-history INSERT failed: %', SQLERRM;
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_records WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer DELETE on pricing_rate_records correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_records failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_record_imports WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer DELETE on pricing_rate_record_imports correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_record_imports failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_record_imports
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer UPDATE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer UPDATE on pricing_rate_record_imports correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer UPDATE on pricing_rate_record_imports failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'CREATE TABLE public.%I (id INTEGER)',
            current_setting('pricing_rate_record_permissions.probe_name') || '_created'
        );
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer CREATE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer CREATE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer CREATE in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'INSERT INTO public.%I VALUES (1)',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer INSERT in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer INSERT in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer INSERT in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'UPDATE public.%I SET id = id WHERE false',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer UPDATE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer UPDATE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer UPDATE in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'DELETE FROM public.%I WHERE false',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer DELETE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE in public failed unexpectedly: %', SQLERRM;
    END;
END
$$;

RESET ROLE;

-- ── Reader expected read-only rights ────────────────────────────────────────

SET ROLE pricing_staleness_reader;

DO $$
BEGIN
    BEGIN
        PERFORM 1 FROM enrichment_staging.pricing_rate_records LIMIT 1;
        PERFORM 1 FROM enrichment_staging.pricing_rate_record_imports LIMIT 1;
        RAISE NOTICE 'PASS: pricing_staleness_reader SELECT on both pricing tables succeeded.';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader SELECT failed: %', SQLERRM;
    END;

    BEGIN
        INSERT INTO enrichment_staging.pricing_rate_records (
            record_key, product_estimate_group, fee_bucket, territory,
            fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
            rate_card_version, content_hash, imported_at
        ) VALUES (
            'reader-test:bucket:territory', 'reader-test', 'bucket', 'territory',
            1, 1, 1, 1, repeat('b', 64), '2026-01-02T00:00:00Z'
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader INSERT on pricing_rate_records correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT on pricing_rate_records failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_records
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader UPDATE on pricing_rate_records correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_records failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_records WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader DELETE on pricing_rate_records correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_records failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        INSERT INTO enrichment_staging.pricing_rate_record_imports (
            record_key, product_estimate_group, fee_bucket, territory,
            fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
            rate_card_version, content_hash, imported_at
        ) VALUES (
            'reader-test:bucket:territory', 'reader-test', 'bucket', 'territory',
            1, 1, 1, 1, repeat('b', 64), '2026-01-02T00:00:00Z'
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader INSERT on pricing_rate_record_imports correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT on pricing_rate_record_imports failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_record_imports
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader UPDATE on pricing_rate_record_imports correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_record_imports failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_record_imports WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader DELETE on pricing_rate_record_imports correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_record_imports failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'CREATE TABLE public.%I (id INTEGER)',
            current_setting('pricing_rate_record_permissions.probe_name') || '_created'
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader CREATE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader CREATE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader CREATE in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'INSERT INTO public.%I VALUES (2)',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader INSERT in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'UPDATE public.%I SET id = id WHERE false',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader UPDATE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE in public failed unexpectedly: %', SQLERRM;
    END;

    BEGIN
        EXECUTE format(
            'DELETE FROM public.%I WHERE false',
            current_setting('pricing_rate_record_permissions.probe_name')
        );
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader DELETE in public correctly denied.';
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE in public failed unexpectedly: %', SQLERRM;
    END;
END
$$;

RESET ROLE;

ROLLBACK;

-- Scan output for "UNEXPECTED". Zero lines means every expected permission
-- boundary held, and the ROLLBACK leaves no public-schema probe or sample rows.
