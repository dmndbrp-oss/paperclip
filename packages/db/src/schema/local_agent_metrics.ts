import {
  pgTable,
  text,
  real,
  timestamp,
  index,
  uuid,
} from "drizzle-orm/pg-core";
import { companies } from "./companies.js";

// Per-agent timeseries rows. One row per (agentId, ladId, tsStart, metricKey).
// Pruned at 30 days by the telemetry TTL job.
export const localAgentMetrics = pgTable(
  "local_agent_metrics",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    ladId: text("lad_id").notNull(),
    agentId: text("agent_id").notNull(),
    tsStart: timestamp("ts_start", { withTimezone: true }).notNull(),
    tsEnd: timestamp("ts_end", { withTimezone: true }).notNull(),
    metricKey: text("metric_key").notNull(),
    metricValue: real("metric_value").notNull(),
  },
  (table) => ({
    agentTsIdx: index("local_agent_metrics_agent_ts_idx").on(table.companyId, table.agentId, table.tsStart),
    ladTsIdx: index("local_agent_metrics_lad_ts_idx").on(table.companyId, table.ladId, table.tsStart),
    companyIdx: index("local_agent_metrics_company_idx").on(table.companyId),
  }),
);

// Scheduler-level timeseries rows (no agentId). One row per (ladId, tsStart, metricKey).
export const ladSchedulerMetrics = pgTable(
  "lad_scheduler_metrics",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    ladId: text("lad_id").notNull(),
    tsStart: timestamp("ts_start", { withTimezone: true }).notNull(),
    tsEnd: timestamp("ts_end", { withTimezone: true }).notNull(),
    metricKey: text("metric_key").notNull(),
    metricValue: real("metric_value").notNull(),
  },
  (table) => ({
    ladTsIdx: index("lad_scheduler_metrics_lad_ts_idx").on(table.companyId, table.ladId, table.tsStart),
    companyIdx: index("lad_scheduler_metrics_company_idx").on(table.companyId),
  }),
);
