import {
  pgTable,
  text,
  integer,
  timestamp,
  index,
  primaryKey,
  uuid,
} from "drizzle-orm/pg-core";
import { companies } from "./companies.js";

export const ladRecords = pgTable(
  "lad_records",
  {
    ladId: text("lad_id").notNull(),
    companyId: uuid("company_id").notNull().references(() => companies.id, { onDelete: "cascade" }),
    hostname: text("hostname").notNull(),
    status: text("status").notNull().default("down"),
    stalenessThresholdSec: integer("staleness_threshold_sec").notNull().default(120),
    lastHeartbeatAt: timestamp("last_heartbeat_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.ladId, table.companyId], name: "lad_records_pk" }),
    companyIdx: index("lad_records_company_idx").on(table.companyId),
  }),
);
