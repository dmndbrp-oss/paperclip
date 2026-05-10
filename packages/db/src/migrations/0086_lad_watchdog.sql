CREATE TABLE IF NOT EXISTS "lad_records" (
	"lad_id" text NOT NULL,
	"company_id" uuid NOT NULL,
	"hostname" text NOT NULL,
	"status" text DEFAULT 'down' NOT NULL,
	"staleness_threshold_sec" integer DEFAULT 120 NOT NULL,
	"last_heartbeat_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "lad_records_pk" PRIMARY KEY("lad_id","company_id")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "lad_heartbeats" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"lad_id" text NOT NULL,
	"company_id" uuid NOT NULL,
	"wall_clock_iso" text NOT NULL,
	"workers" jsonb,
	"scheduler" jsonb,
	"queue_depths" jsonb,
	"last_errors" jsonb,
	"ack_iso" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "lad_incidents" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"lad_id" text NOT NULL,
	"company_id" uuid NOT NULL,
	"issue_id" uuid,
	"status" text DEFAULT 'open' NOT NULL,
	"opened_at" timestamp with time zone DEFAULT now() NOT NULL,
	"resolved_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lad_records_company_id_companies_id_fk') THEN
		ALTER TABLE "lad_records" ADD CONSTRAINT "lad_records_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lad_heartbeats_company_id_companies_id_fk') THEN
		ALTER TABLE "lad_heartbeats" ADD CONSTRAINT "lad_heartbeats_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lad_incidents_company_id_companies_id_fk') THEN
		ALTER TABLE "lad_incidents" ADD CONSTRAINT "lad_incidents_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lad_incidents_issue_id_issues_id_fk') THEN
		ALTER TABLE "lad_incidents" ADD CONSTRAINT "lad_incidents_issue_id_issues_id_fk" FOREIGN KEY ("issue_id") REFERENCES "public"."issues"("id") ON DELETE set null ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_records_company_idx" ON "lad_records" ("company_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_heartbeats_lad_created_idx" ON "lad_heartbeats" ("lad_id","company_id","created_at");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_heartbeats_company_idx" ON "lad_heartbeats" ("company_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_incidents_company_lad_idx" ON "lad_incidents" ("company_id","lad_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "lad_incidents_open_idx" ON "lad_incidents" ("company_id","lad_id","status");
