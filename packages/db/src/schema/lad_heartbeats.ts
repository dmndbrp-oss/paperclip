import {
  pgTable,
  uuid,
  text,
  timestamp,
  jsonb,
  index,
} from "drizzle-orm/pg-core";
import { companies } from "./companies.js";

export const ladHeartbeats = pgTable(
  "lad_heartbeats",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    ladId: text("lad_id").notNull(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    wallClockIso: text("wall_clock_iso").notNull(),
    workers: jsonb("workers"),
    scheduler: jsonb("scheduler"),
    queueDepths: jsonb("queue_depths"),
    lastErrors: jsonb("last_errors"),
    ackIso: text("ack_iso").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    ladCreatedIdx: index("lad_heartbeats_lad_created_idx").on(table.ladId, table.companyId, table.createdAt),
    companyIdx: index("lad_heartbeats_company_idx").on(table.companyId),
  }),
);
