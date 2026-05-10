CREATE TABLE IF NOT EXISTS "agent_work_requests" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"company_id" uuid NOT NULL,
	"agent_id" uuid NOT NULL,
	"manager_agent_id" uuid,
	"last_issue_id" uuid,
	"idle_since_iso" timestamp with time zone,
	"status" text DEFAULT 'pending' NOT NULL,
	"manager_woke_at" timestamp with time zone,
	"manager_response_latency_ms" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_work_requests_company_id_companies_id_fk') THEN
		ALTER TABLE "agent_work_requests" ADD CONSTRAINT "agent_work_requests_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_work_requests_agent_id_agents_id_fk') THEN
		ALTER TABLE "agent_work_requests" ADD CONSTRAINT "agent_work_requests_agent_id_agents_id_fk" FOREIGN KEY ("agent_id") REFERENCES "public"."agents"("id") ON DELETE cascade ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_work_requests_manager_agent_id_agents_id_fk') THEN
		ALTER TABLE "agent_work_requests" ADD CONSTRAINT "agent_work_requests_manager_agent_id_agents_id_fk" FOREIGN KEY ("manager_agent_id") REFERENCES "public"."agents"("id") ON DELETE set null ON UPDATE no action;
	END IF;
END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "agent_work_requests_company_agent_status_idx" ON "agent_work_requests" ("company_id","agent_id","status");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "agent_work_requests_agent_created_idx" ON "agent_work_requests" ("agent_id","created_at");
