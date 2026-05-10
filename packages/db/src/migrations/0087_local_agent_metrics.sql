CREATE TABLE IF NOT EXISTS "local_agent_metrics" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"company_id" uuid NOT NULL,
	"lad_id" text NOT NULL,
	"agent_id" text NOT NULL,
	"ts_start" timestamp with time zone NOT NULL,
	"ts_end" timestamp with time zone NOT NULL,
	"metric_key" text NOT NULL,
	"metric_value" real NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "lad_scheduler_metrics" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"company_id" uuid NOT NULL,
	"lad_id" text NOT NULL,
	"ts_start" timestamp with time zone NOT NULL,
	"ts_end" timestamp with time zone NOT NULL,
	"metric_key" text NOT NULL,
	"metric_value" real NOT NULL
);
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'local_agent_metrics_company_id_companies_id_fk') THEN
		ALTER TABLE "local_agent_metrics" ADD CONSTRAINT "local_agent_metrics_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lad_scheduler_metrics_company_id_companies_id_fk') THEN
		ALTER TABLE "lad_scheduler_metrics" ADD CONSTRAINT "lad_scheduler_metrics_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "local_agent_metrics_agent_ts_idx" ON "local_agent_metrics" ("company_id","agent_id","ts_start");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "local_agent_metrics_lad_ts_idx" ON "local_agent_metrics" ("company_id","lad_id","ts_start");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "local_agent_metrics_company_idx" ON "local_agent_metrics" ("company_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_scheduler_metrics_lad_ts_idx" ON "lad_scheduler_metrics" ("company_id","lad_id","ts_start");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_scheduler_metrics_company_idx" ON "lad_scheduler_metrics" ("company_id");
