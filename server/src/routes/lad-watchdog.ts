/**
 * @fileoverview LAD watchdog endpoints.
 *
 * POST /api/local-adapter-daemons/register
 *   Service-principal (board API key) auth. Registers a new LAD, returns ladId.
 * DELETE /api/local-adapter-daemons/:ladId
 *   Service-principal (board API key) auth. Deregisters a LAD (marks as down).
 * POST /api/local-adapter-daemons/:ladId/heartbeat
 *   Service-principal (board API key) auth. Accepts heartbeat body, persists
 *   to lad_heartbeats rolling table, returns ackIso + nextDueMs.
 *   Returns 410 Gone if ladId is not registered (no agent with that ladHostId).
 */

import { randomUUID } from "node:crypto";
import { Router } from "express";
import { and, eq, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import { agents, ladRecords } from "@paperclipai/db";
import { ladWatchdogService, type LadHeartbeatBody } from "../services/lad-watchdog.js";

const DEFAULT_REFRESH_INTERVAL_SEC = 30;

/**
 * Resolve which company this LAD belongs to, constrained by what the
 * board actor is allowed to access. Returns null if none found.
 * Checks lad_records first (explicit registration), then falls back to agent config.
 */
async function resolveCompanyForLad(
  db: Db,
  ladId: string,
  actorCompanyIds: string[] | undefined,
  isInstanceAdmin: boolean,
): Promise<string | null> {
  // Check lad_records first (populated by POST /register)
  const ladRows = await db
    .select({ companyId: ladRecords.companyId })
    .from(ladRecords)
    .where(eq(ladRecords.ladId, ladId))
    .limit(1);

  if (ladRows.length > 0) {
    const companyId = ladRows[0].companyId;
    if (isInstanceAdmin) return companyId;
    const allowed = new Set(actorCompanyIds ?? []);
    return allowed.has(companyId) ? companyId : null;
  }

  // Fall back: look for agents configured with this ladHostId
  const rows = await db
    .select({ companyId: agents.companyId })
    .from(agents)
    .where(sql`${agents.adapterConfig}->>'ladHostId' = ${ladId}`)
    .limit(50);

  if (rows.length === 0) return null;

  if (isInstanceAdmin) return rows[0].companyId;

  const allowed = new Set(actorCompanyIds ?? []);
  const match = rows.find((r) => allowed.has(r.companyId));
  return match?.companyId ?? null;
}

export function ladWatchdogRoutes(db: Db) {
  const router = Router();
  const watchdog = ladWatchdogService(db);

  // POST /api/local-adapter-daemons/register
  // Registers a LAD and returns a stable ladId. The LAD should use the returned
  // ladId as its ladHostId, and configure agents with adapterConfig.ladHostId = ladId.
  router.post("/local-adapter-daemons/register", async (req, res) => {
    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required for LAD registration" });
      return;
    }

    const isInstanceAdmin = req.actor.source === "local_implicit" || req.actor.isInstanceAdmin === true;
    const body = req.body as {
      agentAllowlist?: unknown;
      modelBudgetGB?: unknown;
      ollamaUrl?: unknown;
      hostname?: unknown;
    };

    // Determine company
    let companyId: string | null = null;
    if (!isInstanceAdmin && Array.isArray(req.actor.companyIds) && req.actor.companyIds.length > 0) {
      companyId = req.actor.companyIds[0];
    } else if (isInstanceAdmin) {
      // Prefer explicit companyIds; fall back to agentAllowlist lookup
      if (Array.isArray(req.actor.companyIds) && req.actor.companyIds.length > 0) {
        companyId = req.actor.companyIds[0];
      } else if (Array.isArray(body.agentAllowlist) && body.agentAllowlist.length > 0) {
        const rows = await db
          .select({ companyId: agents.companyId })
          .from(agents)
          .where(eq(agents.id, body.agentAllowlist[0] as string))
          .limit(1);
        companyId = rows[0]?.companyId ?? null;
      }
    }

    if (!companyId) {
      res.status(422).json({
        error: "Cannot determine company: use a company-scoped API key or include agentAllowlist",
      });
      return;
    }

    // Use provided hostname as ladId for stability across restarts; fall back to UUID
    const hostname = typeof body.hostname === "string" && body.hostname.trim().length > 0
      ? body.hostname.trim()
      : null;
    const ladId = hostname ?? randomUUID();

    await db
      .insert(ladRecords)
      .values({
        ladId,
        companyId,
        hostname: hostname ?? ladId,
        status: "up",
        lastHeartbeatAt: new Date(),
      })
      .onConflictDoUpdate({
        target: [ladRecords.ladId, ladRecords.companyId],
        set: { status: "up", lastHeartbeatAt: new Date(), updatedAt: new Date() },
      });

    res.json({ ladId, refreshIntervalSec: DEFAULT_REFRESH_INTERVAL_SEC });
  });

  // DELETE /api/local-adapter-daemons/:ladId
  // Marks the LAD as deregistered (status = "down").
  router.delete("/local-adapter-daemons/:ladId", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required" });
      return;
    }

    await db
      .update(ladRecords)
      .set({ status: "down", updatedAt: new Date() })
      .where(eq(ladRecords.ladId, ladId));

    res.json({ ok: true, ladId });
  });

  // POST /api/local-adapter-daemons/:ladId/heartbeat
  router.post("/local-adapter-daemons/:ladId/heartbeat", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required for LAD heartbeat" });
      return;
    }

    const isInstanceAdmin = req.actor.source === "local_implicit" || req.actor.isInstanceAdmin === true;
    const companyId = await resolveCompanyForLad(
      db,
      ladId,
      req.actor.companyIds,
      isInstanceAdmin,
    );

    if (!companyId) {
      res.status(410).json({ error: "Gone: ladId is not registered to an accessible company" });
      return;
    }

    const body = req.body as Partial<LadHeartbeatBody>;

    if (!body.wallClockIso || typeof body.wallClockIso !== "string") {
      res.status(400).json({ error: "wallClockIso is required" });
      return;
    }

    const heartbeatBody: LadHeartbeatBody = {
      wallClockIso: body.wallClockIso,
      workers: Array.isArray(body.workers) ? body.workers : undefined,
      scheduler: body.scheduler && typeof body.scheduler === "object"
        ? (body.scheduler as Record<string, unknown>)
        : undefined,
      queueDepths: body.queueDepths && typeof body.queueDepths === "object"
        ? (body.queueDepths as Record<string, number>)
        : undefined,
      lastErrors: Array.isArray(body.lastErrors) ? body.lastErrors : undefined,
    };

    const result = await watchdog.recordHeartbeat(ladId, companyId, heartbeatBody);

    if ("notRegistered" in result) {
      res.status(410).json({ error: "Gone: ladId is not registered" });
      return;
    }

    res.json(result);
  });

  return router;
}
