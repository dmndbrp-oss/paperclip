import { pgTable, uuid, text, timestamp, index } from "drizzle-orm/pg-core";
import { companies } from "./companies.js";
import { agents } from "./agents.js";

export const agentWorkRequests = pgTable(
  "agent_work_requests",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id),
    agentId: uuid("agent_id").notNull().references(() => agents.id),
    managerAgentId: uuid("manager_agent_id").references(() => agents.id),
    lastIssueId: uuid("last_issue_id"),
    idleSinceIso: timestamp("idle_since_iso", { withTimezone: true }),
    status: text("status").notNull().default("pending"),
    managerWokeAt: timestamp("manager_woke_at", { withTimezone: true }),
    managerResponseLatencyMs: text("manager_response_latency_ms"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    companyAgentStatusIdx: index("agent_work_requests_company_agent_status_idx").on(
      table.companyId,
      table.agentId,
      table.status,
    ),
    agentCreatedIdx: index("agent_work_requests_agent_created_idx").on(table.agentId, table.createdAt),
  }),
);
