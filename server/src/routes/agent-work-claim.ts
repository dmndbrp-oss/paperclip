/**
 * @fileoverview Pull-model endpoints for the local-continuous adapter daemon (LAD).
 *
 * Four routes replace the push-based heartbeat for local_continuous agents:
 *   POST /api/agents/:agentId/work-claim                   – long-poll next task, mint run JWT
 *   POST /api/agents/:agentId/request-work                 – ask manager for work (agent_needs_work wake)
 *   POST /api/local-agent-daemons/:ladId/agent-tokens      – service-principal JWT mint
 *   POST /api/local-agent-daemons/:ladId/agent-events      – inbound agent lifecycle events from LAD
 */

import { randomUUID } from "node:crypto";
import { Router } from "express";
import { and, asc, eq, inArray, isNull, or, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import {
  agents,
  agentWorkRequests,
  heartbeatRuns,
  issues,
} from "@paperclipai/db";
import { createLocalAgentJwt } from "../agent-auth-jwt.js";
import { issueService, heartbeatService, agentService, logActivity } from "../services/index.js";
import { logger } from "../middleware/logger.js";

// How long work-claim will long-poll before returning 204 (override via env for tests).
function getWorkClaimTimeoutMs() {
  const raw = Number(process.env.PAPERCLIP_WORK_CLAIM_TIMEOUT_MS);
  return Number.isFinite(raw) && raw >= 0 ? raw : 60_000;
}
// Interval between readiness checks inside the long-poll loop.
const WORK_CLAIM_POLL_INTERVAL_MS = 1_500;

const PRIORITY_ORDER = sql`CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END`;

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Resolve the adapter config's ladHostId from an agent record. */
function readLadHostId(agent: typeof agents.$inferSelect): string | null {
  const cfg = agent.adapterConfig as Record<string, unknown>;
  const raw = cfg?.ladHostId;
  return typeof raw === "string" && raw.trim().length > 0 ? raw.trim() : null;
}

/** Return the model tag from adapter config, used as modelHint. */
function readModelTag(agent: typeof agents.$inferSelect): string | null {
  const cfg = agent.adapterConfig as Record<string, unknown>;
  const raw = cfg?.modelTag;
  return typeof raw === "string" && raw.trim().length > 0 ? raw.trim() : null;
}

/**
 * Find the next actionable issue for agentId: assigned, not checked out by
 * another run, status in_progress or todo, ordered by priority then updatedAt.
 */
async function findNextIssue(
  db: Db,
  companyId: string,
  agentId: string,
  currentRunId: string | null,
) {
  const rows = await db
    .select({
      id: issues.id,
      identifier: issues.identifier,
      title: issues.title,
      status: issues.status,
      priority: issues.priority,
      checkoutRunId: issues.checkoutRunId,
    })
    .from(issues)
    .where(
      and(
        eq(issues.companyId, companyId),
        eq(issues.assigneeAgentId, agentId),
        inArray(issues.status, ["todo", "in_progress"]),
        or(
          isNull(issues.checkoutRunId),
          currentRunId ? eq(issues.checkoutRunId, currentRunId) : isNull(issues.checkoutRunId),
        ),
      ),
    )
    .orderBy(PRIORITY_ORDER, asc(issues.updatedAt))
    .limit(1);
  return rows[0] ?? null;
}

/**
 * Create a heartbeat run record for the claim. We insert in "running" status
 * directly because the LAD executes the agent in its own process — the normal
 * enqueueWakeup flow would try to invoke the (stub) execute() on the adapter.
 */
async function createClaimRun(
  db: Db,
  companyId: string,
  agentId: string,
  issueId: string,
): Promise<{ id: string }> {
  const [run] = await db
    .insert(heartbeatRuns)
    .values({
      companyId,
      agentId,
      invocationSource: "on_demand",
      triggerDetail: "system",
      status: "running",
      startedAt: new Date(),
      contextSnapshot: {
        issueId,
        source: "work_claim",
        wakeReason: "issue_assigned",
      },
    })
    .returning({ id: heartbeatRuns.id });
  if (!run) throw new Error("Failed to insert heartbeat run for work-claim");
  return run;
}

// ──────────────────────────────────────────────────────────────────────────────
// Route factory
// ──────────────────────────────────────────────────────────────────────────────

export function agentWorkClaimRoutes(db: Db) {
  const router = Router();
  const issueSvc = issueService(db);
  const agentSvc = agentService(db);

  // ── POST /api/agents/:agentId/work-claim ──────────────────────────────────
  router.post("/agents/:agentId/work-claim", async (req, res) => {
    const { agentId } = req.params as { agentId: string };

    // Auth: must be the agent itself or a board principal.
    if (req.actor.type === "agent" && req.actor.agentId !== agentId) {
      res.status(403).json({ error: "Agent can only claim work for itself" });
      return;
    }
    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }

    const agent = await agentSvc.getById(agentId);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    // 423 Locked: agent cannot accept work.
    if (
      agent.status === "paused" ||
      agent.status === "terminated" ||
      agent.status === "pending_approval" ||
      agent.status === "quarantined"
    ) {
      res.status(423).json({ error: "Agent is not available for work", agentStatus: agent.status });
      return;
    }

    const companyId = agent.companyId;
    const modelHint = readModelTag(agent);

    // Long-poll loop.
    const deadline = Date.now() + getWorkClaimTimeoutMs();
    while (Date.now() < deadline) {
      const candidate = await findNextIssue(db, companyId, agentId, null);

      if (candidate) {
        // Attempt atomic checkout.
        let checkedOut: Awaited<ReturnType<typeof issueSvc.checkout>> | null = null;
        try {
          // Create run first so we have a runId for the checkout lock.
          const run = await createClaimRun(db, companyId, agentId, candidate.id);

          checkedOut = await issueSvc.checkout(candidate.id, agentId, ["todo", "in_progress"], run.id);

          // Mint JWT.
          const runToken = createLocalAgentJwt(agentId, companyId, agent.adapterType, run.id);
          if (!runToken) {
            // JWT secret not configured — clean up and surface error.
            logger.warn({ agentId, runId: run.id }, "work-claim: JWT secret missing, cannot mint run token");
            res.status(500).json({ error: "Run token could not be minted: JWT secret not configured" });
            return;
          }

          const config = jwtConfig();
          const ttlSec = config?.ttlSeconds ?? 60 * 60 * 48;

          res.json({
            issue: {
              id: checkedOut.id,
              identifier: checkedOut.identifier,
              title: checkedOut.title,
              status: checkedOut.status,
              priority: checkedOut.priority,
            },
            runId: run.id,
            runToken,
            runTokenTtlSec: ttlSec,
            modelHint,
          });
          return;
        } catch (err: unknown) {
          const isConflict =
            err !== null &&
            typeof err === "object" &&
            "status" in err &&
            (err as { status: number }).status === 409;

          if (isConflict) {
            // Race — another claimer won; poll again.
          } else {
            throw err;
          }
        }
      }

      // No work yet — wait before next poll (honour deadline).
      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      await sleep(Math.min(WORK_CLAIM_POLL_INTERVAL_MS, remaining));
    }

    // Timed out with nothing available.
    res.status(204).end();
  });

  // ── POST /api/agents/:agentId/request-work ────────────────────────────────
  router.post("/agents/:agentId/request-work", async (req, res) => {
    const { agentId } = req.params as { agentId: string };

    if (req.actor.type === "agent" && req.actor.agentId !== agentId) {
      res.status(403).json({ error: "Agent can only request work for itself" });
      return;
    }
    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }

    const agent = await agentSvc.getById(agentId);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    const companyId = agent.companyId;

    // Resolve the manager (chainOfCommand[0]).
    const chain = await agentSvc.getChainOfCommand(agentId);
    const manager = chain[0] ?? null;
    if (!manager) {
      res.status(422).json({ error: "Agent has no manager in chainOfCommand; cannot request work" });
      return;
    }

    const lastIssueId = typeof req.body?.lastIssueId === "string" ? req.body.lastIssueId : null;
    const idleSinceIso = typeof req.body?.idleSinceIso === "string" ? req.body.idleSinceIso : null;
    const requestId = randomUUID();

    // Insert the work-request record.
    await db.insert(agentWorkRequests).values({
      id: requestId,
      companyId,
      agentId,
      managerAgentId: manager.id,
      lastIssueId: lastIssueId ?? undefined,
      idleSinceIso: idleSinceIso ? new Date(idleSinceIso) : undefined,
      status: "pending",
    });

    // Emit wake to manager with the new wake reason.
    const heartbeat = heartbeatService(db);
    const wakePayload: Record<string, unknown> = {
      idleAgent: agentId,
      requestId,
    };
    if (idleSinceIso) wakePayload.idleSinceIso = idleSinceIso;
    if (lastIssueId) wakePayload.lastIssueId = lastIssueId;

    await heartbeat
      .wakeup(manager.id, {
        source: "on_demand",
        triggerDetail: "system",
        reason: "agent_needs_work",
        payload: wakePayload,
        requestedByActorType: "agent",
        requestedByActorId: agentId,
        contextSnapshot: {
          wakeReason: "agent_needs_work",
          idleAgent: agentId,
          requestId,
        },
      })
      .catch((err) => {
        logger.warn({ err, agentId, managerId: manager.id }, "request-work: failed to wake manager");
      });

    res.status(202).json({
      requestId,
      managerAgentId: manager.id,
      pollAfterMs: 5_000,
    });
  });

  // ── POST /api/local-agent-daemons/:ladId/agent-tokens ────────────────────
  //
  // Service-principal route: a board API key authenticates the LAD process.
  // The LAD supplies ladId (its ladHostId) and a target agentId; the server
  // validates the agent is configured with that ladHostId before minting.
  router.post("/local-agent-daemons/:ladId/agent-tokens", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    // Board API key (service principal) required — agent JWTs cannot mint here.
    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required for agent-token minting" });
      return;
    }

    const targetAgentId = typeof req.body?.agentId === "string" ? req.body.agentId.trim() : null;
    if (!targetAgentId) {
      res.status(400).json({ error: "agentId is required" });
      return;
    }

    const agent = await agentSvc.getById(targetAgentId);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    // Validate that this LAD is authorised to mint for the given agent.
    const agentLadHostId = readLadHostId(agent);
    if (!agentLadHostId || agentLadHostId !== ladId) {
      res.status(403).json({
        error: "ladHostId mismatch: this daemon is not authorised to mint tokens for the requested agent",
      });
      return;
    }

    if (
      agent.status === "terminated" ||
      agent.status === "pending_approval" ||
      agent.status === "quarantined"
    ) {
      res.status(403).json({ error: "Agent is not active", agentStatus: agent.status });
      return;
    }

    const runId = randomUUID();
    const runToken = createLocalAgentJwt(targetAgentId, agent.companyId, agent.adapterType, runId);
    if (!runToken) {
      res.status(500).json({ error: "JWT secret not configured; cannot mint run token" });
      return;
    }

    const cfg = jwtConfig();
    const ttlSec = cfg?.ttlSeconds ?? 60 * 60 * 48;

    res.json({ runToken, ttlSec });
  });

  // ── POST /api/local-agent-daemons/:ladId/agent-events ────────────────────
  //
  // Inbound lifecycle events from the LAD: the LAD reports agent lifecycle
  // changes (e.g. worker restart after unquarantine) back to the server.
  // Authenticated with a board API key (the LAD's service principal).
  router.post("/local-agent-daemons/:ladId/agent-events", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required for agent-events" });
      return;
    }

    const event = typeof req.body?.event === "string" ? req.body.event : null;
    const targetAgentId = typeof req.body?.agentId === "string" ? req.body.agentId.trim() : null;

    if (!event || !targetAgentId) {
      res.status(400).json({ error: "event and agentId are required" });
      return;
    }

    const agent = await agentSvc.getById(targetAgentId);
    if (!agent) {
      res.status(404).json({ error: "Agent not found" });
      return;
    }

    // Validate that this LAD is associated with the given agent.
    const agentLadHostId = readLadHostId(agent);
    if (!agentLadHostId || agentLadHostId !== ladId) {
      res.status(403).json({
        error: "ladHostId mismatch: this daemon is not authorised to report events for the requested agent",
      });
      return;
    }

    if (event === "agent_unquarantined") {
      await logActivity(db, {
        companyId: agent.companyId,
        actorType: "system",
        actorId: ladId,
        action: "agent.worker_restarted_after_unquarantine",
        entityType: "agent",
        entityId: agent.id,
        details: { ladId, event },
      });

      res.json({ ok: true, event, agentId: targetAgentId });
      return;
    }

    // Unknown events are accepted but not processed, so future LAD versions
    // don't break against older server versions.
    logger.info({ ladId, event, agentId: targetAgentId }, "agent-events: received unknown event, ignoring");
    res.json({ ok: true, event, agentId: targetAgentId });
  });

  return router;
}

// Re-export the config reader so tests can inspect it without re-importing
// agent-auth-jwt (avoids circular dependency in tests).
function jwtConfig() {
  const secret = process.env.PAPERCLIP_AGENT_JWT_SECRET?.trim() || process.env.BETTER_AUTH_SECRET?.trim();
  if (!secret) return null;
  const parsed = Number(process.env.PAPERCLIP_AGENT_JWT_TTL_SECONDS);
  const ttlSeconds =
    Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 60 * 60 * 48;
  return { ttlSeconds };
}
