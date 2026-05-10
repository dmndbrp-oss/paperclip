import { and, eq, lt, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import { localAgentMetrics, ladSchedulerMetrics } from "@paperclipai/db";

const TELEMETRY_TTL_DAYS = 30;

export const AGENT_METRIC_KEYS = [
  "idle_time_pct",
  "model_swap_count",
  "queue_empty_events",
  "manager_response_latency_ms",
  "model_load_pending_ms",
  "run_duration_ms",
  "consecutive_failures",
] as const;

export const SCHEDULER_METRIC_KEYS = [
  "loaded_models_count",
  "memory_used_gb",
  "budget_utilization_pct",
  "ollama_healthcheck_ok",
] as const;

type AgentMetricKey = (typeof AGENT_METRIC_KEYS)[number];
type SchedulerMetricKey = (typeof SCHEDULER_METRIC_KEYS)[number];

export type TelemetryAgentEntry = {
  agentId: string;
  metrics: Partial<Record<AgentMetricKey, number>>;
};

export type TelemetrySchedulerEntry = Partial<Record<SchedulerMetricKey, number | boolean>>;

export type TelemetryIngestBody = {
  window: { startIso: string; endIso: string };
  perAgent: TelemetryAgentEntry[];
  scheduler: TelemetrySchedulerEntry;
};

export type TelemetryMetricRow = {
  agentId: string;
  tsStart: Date;
  tsEnd: Date;
  metricKey: string;
  metricValue: number;
};

export type SchedulerMetricRow = {
  tsStart: Date;
  tsEnd: Date;
  metricKey: string;
  metricValue: number;
};

export type AgentMetricSeries = {
  key: string;
  rows: Array<{ ts: string; value: number }>;
};

function schedulerValueToNumber(val: number | boolean): number {
  return typeof val === "boolean" ? (val ? 1 : 0) : val;
}

export function ladTelemetryService(db: Db) {
  return {
    async ingest(
      ladId: string,
      companyId: string,
      body: TelemetryIngestBody,
    ): Promise<void> {
      const tsStart = new Date(body.window.startIso);
      const tsEnd = new Date(body.window.endIso);

      const agentRows: (typeof localAgentMetrics.$inferInsert)[] = [];
      for (const entry of body.perAgent) {
        for (const key of AGENT_METRIC_KEYS) {
          const raw = entry.metrics[key];
          if (raw === undefined || raw === null) continue;
          agentRows.push({
            companyId,
            ladId,
            agentId: entry.agentId,
            tsStart,
            tsEnd,
            metricKey: key,
            metricValue: raw,
          });
        }
      }

      const schedulerRows: (typeof ladSchedulerMetrics.$inferInsert)[] = [];
      for (const key of SCHEDULER_METRIC_KEYS) {
        const raw = body.scheduler[key];
        if (raw === undefined || raw === null) continue;
        schedulerRows.push({
          companyId,
          ladId,
          tsStart,
          tsEnd,
          metricKey: key,
          metricValue: schedulerValueToNumber(raw),
        });
      }

      await db.transaction(async (tx) => {
        if (agentRows.length > 0) {
          await tx.insert(localAgentMetrics).values(agentRows);
        }
        if (schedulerRows.length > 0) {
          await tx.insert(ladSchedulerMetrics).values(schedulerRows);
        }
      });
    },

    async getAgentSeries(
      companyId: string,
      agentId: string,
      since: Date,
    ): Promise<AgentMetricSeries[]> {
      const rows = await db
        .select({
          metricKey: localAgentMetrics.metricKey,
          tsStart: localAgentMetrics.tsStart,
          metricValue: localAgentMetrics.metricValue,
        })
        .from(localAgentMetrics)
        .where(
          and(
            eq(localAgentMetrics.companyId, companyId),
            eq(localAgentMetrics.agentId, agentId),
            sql`${localAgentMetrics.tsStart} >= ${since}`,
          ),
        )
        .orderBy(localAgentMetrics.tsStart);

      const byKey = new Map<string, Array<{ ts: string; value: number }>>();
      for (const row of rows) {
        let arr = byKey.get(row.metricKey);
        if (!arr) {
          arr = [];
          byKey.set(row.metricKey, arr);
        }
        arr.push({ ts: row.tsStart.toISOString(), value: row.metricValue });
      }
      return Array.from(byKey.entries()).map(([key, series]) => ({ key, rows: series }));
    },

    async pruneOldMetrics(): Promise<{ agentRows: number; schedulerRows: number }> {
      const cutoff = new Date(Date.now() - TELEMETRY_TTL_DAYS * 24 * 60 * 60 * 1000);

      const [agentResult, schedulerResult] = await Promise.all([
        db
          .delete(localAgentMetrics)
          .where(lt(localAgentMetrics.tsStart, cutoff)),
        db
          .delete(ladSchedulerMetrics)
          .where(lt(ladSchedulerMetrics.tsStart, cutoff)),
      ]);

      return {
        agentRows: (agentResult as unknown as { rowCount?: number }).rowCount ?? 0,
        schedulerRows: (schedulerResult as unknown as { rowCount?: number }).rowCount ?? 0,
      };
    },
  };
}
