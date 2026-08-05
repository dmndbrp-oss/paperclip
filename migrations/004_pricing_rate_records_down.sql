-- Migration 004 (DOWN): remove Pricing rate records and import history
-- SAG-6343 Task 1 | Parent: SAG-6327
--
-- DESTRUCTIVE: drops all active Pricing rate records and their append-only
-- import history. Do not run against a database whose history must be kept.
-- Does not alter the shared pricing_staleness_reader role or its pre-existing
-- enrichment_staging schema grant from migration 003.

BEGIN;

REVOKE ALL ON TABLE enrichment_staging.pricing_rate_records,
                    enrichment_staging.pricing_rate_record_imports
    FROM pricing_rate_importer, pricing_staleness_reader;

REVOKE USAGE ON SCHEMA enrichment_staging
    FROM pricing_rate_importer;

-- Drop observations first so this migration owns the removal order even if a
-- future revision adds a relationship from history to the active table.
DROP TABLE IF EXISTS enrichment_staging.pricing_rate_record_imports CASCADE;
DROP TABLE IF EXISTS enrichment_staging.pricing_rate_records CASCADE;

DO $$
BEGIN
    DROP ROLE IF EXISTS pricing_rate_importer;
EXCEPTION
    WHEN dependent_objects_still_exist THEN
        RAISE NOTICE 'Role pricing_rate_importer has dependents outside this migration; skipping drop.';
END
$$;

COMMIT;
