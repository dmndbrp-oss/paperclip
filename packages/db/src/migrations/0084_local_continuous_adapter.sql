ALTER TABLE "companies" ADD COLUMN IF NOT EXISTS "local_continuous_adapter_enabled" boolean DEFAULT false NOT NULL;
