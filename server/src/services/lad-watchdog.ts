import { randomUUID } from "node:crypto";
import { and, desc, eq, lt, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import {
  agents,
  issues,
  issueComments,
  ladHeartbeats,
  ladIncidents,
  ladRecords,
  labels,
  issueLabels,
} from "@paperclipai/db";
import { logger } from "../middleware/logger.js";
import { issueService } from "./issues.js";

const HEARTBEAT_RETENTION_MS = 24 * 60 * 60 * 1000;
const DEFAULT_STALENESS_THRESHOLD_SEC = 120;
const RECONNECT_COMMENT_AUTHOR_TYPE = "system" as const;

export type LadHeartbeatBody = {
  wallClockIso: string;
  workers?: unknown[];
  scheduler?: Record<string, unknown>;
  queueDepths?: Record<string, number>;
  lastErrors?: unknown[];
};

export type LadRegistrationStatus =
  | { registered: true; companyId: string }
  | { registered: false };

/** Returns true if at least one agent in the company has ladHostId === ladId. */
async function isLadRegistered(db: Db, ladId: string, companyId: string): Promise<boolean> {
  const rows = await db
    .select({ id: agents.id })
    .from(agents)
    .where(
      and(
        eq(agents.companyId, companyId),
        sql`${agents.adapterConfig}->>'ladHostId' = ${ladId}`,
      ),
    )
    .limit(1);
  return rows.length > 0;
}

/** Returns the top-level agent (CEO) for a company — the one with reportsTo = null. */
async function findCeoAgent(db: Db, companyId: string): Promise<string | null> {
  const rows = await db
    .select({ id: agents.id, role: agents.role })
    .from(agents)
    .where(
      and(
        eq(agents.companyId, companyId),
        sql`${agents.reportsTo} IS NULL`,
      ),
    )
    .limit(5);
  if (rows.length === 0) return null;
  const ceo = rows.find((r) => r.role === "ceo" || r.role === "administrator") ?? rows[0];
  return ceo.id;
}

/** Find or create a label by name for the company. */
async function findOrCreateLabel(
  db: Db,
  companyId: string,
  name: string,
  color: string,
): Promise<string> {
  const existing = await db
    .select({ id: labels.id })
    .from(labels)
    .where(and(eq(labels.companyId, companyId), eq(labels.name, name)))
    .then((rows) => rows[0] ?? null);
  if (existing) return existing.id;

  const [created] = await db
    .insert(labels)
    .values({ companyId, name, color })
    .returning({ id: labels.id });
  return created.id;
}

function relativeTimeLabel(ms: number): string {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  return `${Math.floor(min / 60)}h ${min % 60}m ago`;
}

function buildIncidentIssueBody(params: {
  ladId: string;
  hostname: string;
  lastHeartbeatAt: Date | null;
  lastKnownWorkers: unknown;
  lastKnownErrors: unknown;
}): string {
  const relative = params.lastHeartbeatAt
    ? relativeTimeLabel(Date.now() - params.lastHeartbeatAt.getTime())
    : "never";

  const workersSection =
    params.lastKnownWorkers
      ? `\`\`\`json\n${JSON.stringify(params.lastKnownWorkers, null, 2)}\n\`\`\``
      : "_No worker data available._";

  const errorsSection =
    params.lastKnownErrors
      ? `\`\`\`json\n${JSON.stringify(params.lastKnownErrors, null, 2)}\n\`\`\``
      : "_No recent errors recorded._";

  return `## LAD Unresponsive Incident

The Local Adapter Daemon (LAD) on **${params.hostname}** has stopped sending heartbeats.

| Field | Value |
|---|---|
| LAD ID | \`${params.ladId}\` |
| Hostname | \`${params.hostname}\` |
| Last heartbeat | ${relative} |

### Last-known worker state
${workersSection}

### Recent errors
${errorsSection}

## Runbook

1. SSH into \`${params.hostname}\` and check the LAD process: \`ps aux | grep opencode_local\`
2. Review LAD logs for the root cause.
3. Restart the LAD if it has crashed: \`systemctl restart opencode-local\` (or equivalent).
4. Confirm reconnect — the incident issue will receive an auto-comment when heartbeats resume.
5. Close this issue once you have confirmed stable operation.

> This incident was auto-created by the Paperclip LAD watchdog. Do NOT auto-close — CEO decides when to close.
`;
}

export function ladWatchdogService(db: Db) {
  const issueSvc = issueService(db);

  return {
    /**
     * Records a LAD heartbeat. Returns the ackIso and nextDueMs.
     * Throws if the LAD is not registered (caller should return 410).
     */
    recordHeartbeat: async (
      ladId: string,
      companyId: string,
      body: LadHeartbeatBody,
    ): Promise<{ ackIso: string; nextDueMs: number } | { notRegistered: true }> => {
      const registered = await isLadRegistered(db, ladId, companyId);
      if (!registered) return { notRegistered: true };

      const now = new Date();
      const ackIso = now.toISOString();

      // Upsert lad_records to track status + last seen
      await db
        .insert(ladRecords)
        .values({
          ladId,
          companyId,
          hostname: ladId,
          status: "up",
          lastHeartbeatAt: now,
        })
        .onConflictDoUpdate({
          target: [ladRecords.ladId, ladRecords.companyId],
          set: {
            status: "up",
            lastHeartbeatAt: now,
            updatedAt: now,
          },
        });

      // Persist heartbeat row
      await db.insert(ladHeartbeats).values({
        ladId,
        companyId,
        wallClockIso: body.wallClockIso,
        workers: body.workers ?? null,
        scheduler: body.scheduler ?? null,
        queueDepths: body.queueDepths ?? null,
        lastErrors: body.lastErrors ?? null,
        ackIso,
      });

      // Prune heartbeats older than 24h for this LAD
      const cutoff = new Date(now.getTime() - HEARTBEAT_RETENTION_MS);
      await db
        .delete(ladHeartbeats)
        .where(
          and(
            eq(ladHeartbeats.ladId, ladId),
            eq(ladHeartbeats.companyId, companyId),
            lt(ladHeartbeats.createdAt, cutoff),
          ),
        );

      // If there's an open incident, add reconnect comment and update status
      const openIncident = await db
        .select({ id: ladIncidents.id, issueId: ladIncidents.issueId, openedAt: ladIncidents.openedAt })
        .from(ladIncidents)
        .where(
          and(
            eq(ladIncidents.ladId, ladId),
            eq(ladIncidents.companyId, companyId),
            eq(ladIncidents.status, "open"),
          ),
        )
        .then((rows) => rows[0] ?? null);

      if (openIncident) {
        const staleDurationMs = now.getTime() - openIncident.openedAt.getTime();
        const staleDurationSec = Math.round(staleDurationMs / 1000);

        // Mark incident resolved (but don't close the issue — CEO decides)
        await db
          .update(ladIncidents)
          .set({ status: "resolved", resolvedAt: now, updatedAt: now })
          .where(eq(ladIncidents.id, openIncident.id));

        // Auto-comment on the incident issue if it still exists
        if (openIncident.issueId) {
          await db
            .insert(issueComments)
            .values({
              companyId,
              issueId: openIncident.issueId,
              authorType: RECONNECT_COMMENT_AUTHOR_TYPE,
              body: `LAD reconnected at ${ackIso}. Stale duration was ${staleDurationSec}s (${staleDurationMs}ms).`,
            })
            .catch((err) => {
              logger.warn({ err, issueId: openIncident.issueId }, "lad-watchdog: failed to add reconnect comment");
            });

          // Update issue updatedAt
          await db
            .update(issues)
            .set({ updatedAt: now })
            .where(eq(issues.id, openIncident.issueId))
            .catch(() => {/* non-fatal */});
        }
      }

      return { ackIso, nextDueMs: 30_000 };
    },

    /**
     * Scans all registered LADs for staleness (1-min cron cadence).
     * Creates incident issues for newly stale LADs; dedupes via lad_incidents.
     */
    scanStale: async (): Promise<{ tripped: number; alreadyOpen: number }> => {
      const now = new Date();

      // Load all lad_records that have sent at least one heartbeat
      const allLads = await db
        .select({
          ladId: ladRecords.ladId,
          companyId: ladRecords.companyId,
          hostname: ladRecords.hostname,
          status: ladRecords.status,
          stalenessThresholdSec: ladRecords.stalenessThresholdSec,
          lastHeartbeatAt: ladRecords.lastHeartbeatAt,
        })
        .from(ladRecords)
        .where(sql`${ladRecords.lastHeartbeatAt} IS NOT NULL`);

      let tripped = 0;
      let alreadyOpen = 0;

      for (const lad of allLads) {
        if (!lad.lastHeartbeatAt) continue;

        const thresholdMs = lad.stalenessThresholdSec * 1000;
        const msSinceHeartbeat = now.getTime() - lad.lastHeartbeatAt.getTime();
        if (msSinceHeartbeat <= thresholdMs) continue;

        // LAD is stale
        // Dedupe: check for existing open incident
        const existingIncident = await db
          .select({ id: ladIncidents.id })
          .from(ladIncidents)
          .where(
            and(
              eq(ladIncidents.ladId, lad.ladId),
              eq(ladIncidents.companyId, lad.companyId),
              eq(ladIncidents.status, "open"),
            ),
          )
          .then((rows) => rows[0] ?? null);

        if (existingIncident) {
          alreadyOpen++;
          continue;
        }

        // Mark stale in lad_records
        await db
          .update(ladRecords)
          .set({ status: "stale", updatedAt: now })
          .where(
            and(
              eq(ladRecords.ladId, lad.ladId),
              eq(ladRecords.companyId, lad.companyId),
            ),
          );

        // Fetch last heartbeat payload for context
        const lastHb = await db
          .select({ workers: ladHeartbeats.workers, lastErrors: ladHeartbeats.lastErrors })
          .from(ladHeartbeats)
          .where(
            and(
              eq(ladHeartbeats.ladId, lad.ladId),
              eq(ladHeartbeats.companyId, lad.companyId),
            ),
          )
          .orderBy(desc(ladHeartbeats.createdAt))
          .limit(1)
          .then((rows) => rows[0] ?? null);

        // Find CEO agent to assign the issue to
        const ceoAgentId = await findCeoAgent(db, lad.companyId);

        // Find or create incident/lad-down labels
        const [incidentLabelId, ladDownLabelId] = await Promise.all([
          findOrCreateLabel(db, lad.companyId, "incident", "#e11d48"),
          findOrCreateLabel(db, lad.companyId, "lad-down", "#7c3aed"),
        ]);

        const relativeLabel = relativeTimeLabel(msSinceHeartbeat);
        const issueTitle = `LAD on ${lad.hostname} unresponsive (last heartbeat ${relativeLabel})`;
        const issueBody = buildIncidentIssueBody({
          ladId: lad.ladId,
          hostname: lad.hostname,
          lastHeartbeatAt: lad.lastHeartbeatAt,
          lastKnownWorkers: lastHb?.workers ?? null,
          lastKnownErrors: lastHb?.lastErrors ?? null,
        });

        let createdIssueId: string | null = null;
        try {
          const created = await issueSvc.create(lad.companyId, {
            title: issueTitle,
            description: issueBody,
            status: "todo",
            priority: "critical",
            ...(ceoAgentId ? { assigneeAgentId: ceoAgentId } : {}),
            labelIds: [incidentLabelId, ladDownLabelId],
          });
          createdIssueId = created.id;
        } catch (err) {
          logger.error({ err, ladId: lad.ladId, companyId: lad.companyId }, "lad-watchdog: failed to create incident issue");
        }

        // Create incident record for dedupe
        await db.insert(ladIncidents).values({
          ladId: lad.ladId,
          companyId: lad.companyId,
          issueId: createdIssueId,
          status: "open",
          openedAt: now,
        });

        logger.warn(
          { ladId: lad.ladId, companyId: lad.companyId, msSinceHeartbeat, issueId: createdIssueId },
          "lad-watchdog: LAD stale — incident created",
        );
        tripped++;
      }

      return { tripped, alreadyOpen };
    },

    /**
     * Returns dashboard tile data for all LADs in a company.
     * Per plan §11.4: id+hostname, status badge, last-seen, worker breakdown,
     * model info, avg idle_time_pct (last 1h), model-load events (last 1h),
     * latest open incident link.
     */
    getLadDashboardData: async (companyId: string) => {
      const now = new Date();
      const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);

      const lads = await db
        .select({
          ladId: ladRecords.ladId,
          hostname: ladRecords.hostname,
          status: ladRecords.status,
          lastHeartbeatAt: ladRecords.lastHeartbeatAt,
          stalenessThresholdSec: ladRecords.stalenessThresholdSec,
        })
        .from(ladRecords)
        .where(eq(ladRecords.companyId, companyId));

      const results = await Promise.all(
        lads.map(async (lad) => {
          // Latest heartbeat for current worker/model state
          const latestHb = await db
            .select({
              workers: ladHeartbeats.workers,
              scheduler: ladHeartbeats.scheduler,
              queueDepths: ladHeartbeats.queueDepths,
              lastErrors: ladHeartbeats.lastErrors,
              ackIso: ladHeartbeats.ackIso,
            })
            .from(ladHeartbeats)
            .where(
              and(
                eq(ladHeartbeats.ladId, lad.ladId),
                eq(ladHeartbeats.companyId, companyId),
              ),
            )
            .orderBy(desc(ladHeartbeats.createdAt))
            .limit(1)
            .then((rows) => rows[0] ?? null);

          // Aggregate idle_time_pct + model-load events from last-1h heartbeats
          const recentHbs = await db
            .select({
              workers: ladHeartbeats.workers,
            })
            .from(ladHeartbeats)
            .where(
              and(
                eq(ladHeartbeats.ladId, lad.ladId),
                eq(ladHeartbeats.companyId, companyId),
                sql`${ladHeartbeats.createdAt} >= ${oneHourAgo}`,
              ),
            );

          let totalIdlePct = 0;
          let modelLoadEvents = 0;
          for (const hb of recentHbs) {
            const workers = Array.isArray(hb.workers) ? hb.workers as Record<string, unknown>[] : [];
            for (const w of workers) {
              if (typeof w.idleTimePct === "number") totalIdlePct += w.idleTimePct;
              if (w.state === "loading") modelLoadEvents++;
            }
          }
          const avgIdleTimePct = recentHbs.length > 0 ? totalIdlePct / recentHbs.length : null;

          // Open incident
          const openIncident = await db
            .select({ id: ladIncidents.id, issueId: ladIncidents.issueId, openedAt: ladIncidents.openedAt })
            .from(ladIncidents)
            .where(
              and(
                eq(ladIncidents.ladId, lad.ladId),
                eq(ladIncidents.companyId, companyId),
                eq(ladIncidents.status, "open"),
              ),
            )
            .then((rows) => rows[0] ?? null);

          // Worker breakdown from latest heartbeat
          const workers = Array.isArray(latestHb?.workers) ? latestHb.workers as Record<string, unknown>[] : [];
          const workerStateCounts: Record<string, number> = {};
          let loadedModels = 0;
          let memoryUsedMb = 0;
          let memoryBudgetMb = 0;
          for (const w of workers) {
            const state = typeof w.state === "string" ? w.state : "unknown";
            workerStateCounts[state] = (workerStateCounts[state] ?? 0) + 1;
            if (typeof w.loadedModels === "number") loadedModels += w.loadedModels;
            if (typeof w.memoryUsedMb === "number") memoryUsedMb += w.memoryUsedMb;
            if (typeof w.memoryBudgetMb === "number") memoryBudgetMb += w.memoryBudgetMb;
          }

          const lastSeenIso = lad.lastHeartbeatAt?.toISOString() ?? null;
          const lastSeenRelative = lad.lastHeartbeatAt
            ? relativeTimeLabel(now.getTime() - lad.lastHeartbeatAt.getTime())
            : null;

          return {
            id: lad.ladId,
            hostname: lad.hostname,
            status: lad.status as "up" | "stale" | "down",
            lastSeenIso,
            lastSeenRelative,
            stalenessThresholdSec: lad.stalenessThresholdSec,
            workers: {
              total: workers.length,
              byState: workerStateCounts,
            },
            models: {
              loadedCount: loadedModels,
              memoryUsedMb,
              memoryBudgetMb,
            },
            avgIdleTimePct1h: avgIdleTimePct,
            modelLoadEvents1h: modelLoadEvents,
            openIncident: openIncident
              ? { incidentId: openIncident.id, issueId: openIncident.issueId, openedAt: openIncident.openedAt.toISOString() }
              : null,
          };
        }),
      );

      return results;
    },
  };
}
