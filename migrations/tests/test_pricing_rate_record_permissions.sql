-- Permission test for Migration 004 (SAG-6343 Task 1)
--
-- Run as a superuser after migrations 001, 003, and 004. This script creates
-- a disposable public-schema probe solely so it can prove both roles cannot
-- write public tables. Exit 0 plus no "UNEXPECTED" output is a pass.
--
-- Usage:
--   psql -U postgres -d <isolated_db> -f migrations/tests/test_pricing_rate_record_permissions.sql

\set ON_ERROR_STOP off

CREATE TABLE IF NOT EXISTS public.pricing_rate_record_permission_probe (
    id INTEGER PRIMARY KEY
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
            12.50, 8.10, 0, 1, repeat('a', 64), '2026-01-01T00:00:00Z'
        ) ON CONFLICT (record_key) DO UPDATE
            SET imported_at = EXCLUDED.imported_at;
        RAISE NOTICE 'PASS: pricing_rate_importer active-record INSERT/UPDATE succeeded.';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer active-record INSERT/UPDATE denied: %', SQLERRM;
    END;

    BEGIN
        INSERT INTO enrichment_staging.pricing_rate_record_imports (
            record_key, product_estimate_group, fee_bucket, territory,
            fee_per_sqft, cost_basis_per_sqft, install_adder_per_sqft,
            rate_card_version, content_hash, imported_at
        ) VALUES (
            'test-group:test-bucket:test-territory', 'test-group', 'test-bucket', 'test-territory',
            12.50, 8.10, 0, 1, repeat('a', 64), '2026-01-01T00:00:00Z'
        ) ON CONFLICT (record_key, imported_at) DO NOTHING;
        RAISE NOTICE 'PASS: pricing_rate_importer import-history INSERT succeeded.';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'UNEXPECTED: pricing_rate_importer import-history INSERT denied: %', SQLERRM;
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_records WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer DELETE on pricing_rate_records correctly denied.';
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_record_imports WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer DELETE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer DELETE on pricing_rate_record_imports correctly denied.';
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_record_imports
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer UPDATE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer UPDATE on pricing_rate_record_imports correctly denied.';
    END;

    BEGIN
        INSERT INTO public.pricing_rate_record_permission_probe VALUES (1);
        RAISE NOTICE 'UNEXPECTED: pricing_rate_importer INSERT in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_rate_importer INSERT in public correctly denied.';
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
            RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader SELECT denied: %', SQLERRM;
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
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_records
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader UPDATE on pricing_rate_records correctly denied.';
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_records WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_records SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader DELETE on pricing_rate_records correctly denied.';
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
    END;

    BEGIN
        UPDATE enrichment_staging.pricing_rate_record_imports
            SET imported_at = imported_at
            WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader UPDATE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader UPDATE on pricing_rate_record_imports correctly denied.';
    END;

    BEGIN
        DELETE FROM enrichment_staging.pricing_rate_record_imports WHERE false;
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader DELETE on pricing_rate_record_imports SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader DELETE on pricing_rate_record_imports correctly denied.';
    END;

    BEGIN
        INSERT INTO public.pricing_rate_record_permission_probe VALUES (2);
        RAISE NOTICE 'UNEXPECTED: pricing_staleness_reader INSERT in public SUCCEEDED.';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE 'PASS: pricing_staleness_reader INSERT in public correctly denied.';
    END;
END
$$;

RESET ROLE;

DROP TABLE public.pricing_rate_record_permission_probe;

-- Scan output for "UNEXPECTED". Zero lines means every expected permission
-- boundary held.
