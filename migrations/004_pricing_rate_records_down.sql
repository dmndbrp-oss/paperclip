-- Migration 004 (DOWN): remove Pricing rate records and import history
-- SAG-6343 Task 1 | Parent: SAG-6327
--
-- DESTRUCTIVE: drops all active Pricing rate records and their append-only
-- import history. Do not run against a database whose history must be kept.
-- Does not alter the shared pricing_staleness_reader role or its pre-existing
-- enrichment_staging schema grant from migration 003.

BEGIN;

-- Drop observations first. Deliberately omit CASCADE: a dependency added by a
-- later migration must make this rollback fail instead of removing objects
-- outside Task 1.
DROP TABLE IF EXISTS enrichment_staging.pricing_rate_record_imports;
DROP TABLE IF EXISTS enrichment_staging.pricing_rate_records;

DO $$
BEGIN
    -- Only remove a role this migration created. An identically named role may
    -- pre-date this migration and be used outside this Task-1 boundary.
    IF EXISTS (
        SELECT 1
        FROM pg_roles AS role
        JOIN pg_shdescription AS description ON description.objoid = role.oid
        WHERE role.rolname = 'pricing_rate_importer'
          AND description.description =
              'Created by SAG-6343 migration 004; safe for migration 004 down to remove.'
    ) THEN
        BEGIN
            -- This is the USAGE grant added by the matching up migration. It
            -- must be removed before PostgreSQL can drop the marked role.
            REVOKE USAGE ON SCHEMA enrichment_staging FROM pricing_rate_importer;
            DROP ROLE pricing_rate_importer;
        EXCEPTION
            WHEN dependent_objects_still_exist THEN
                RAISE NOTICE 'Role pricing_rate_importer has dependents outside this migration; skipping drop.';
        END;
    ELSE
        RAISE NOTICE 'Role pricing_rate_importer was not created by migration 004; preserving it.';
    END IF;
END
$$;

COMMIT;
