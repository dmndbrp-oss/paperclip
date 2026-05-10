/**
 * @fileoverview LAD watchdog endpoints.
 *
 * POST /api/local-adapter-daemons/:ladId/heartbeat
 *   Service-principal (board API key) auth. Accepts heartbeat body, persists
 *   to lad_heartbeats rolling table, returns ackIso + nextDueMs.
 *   Returns 410 Gone if ladId is not registered (no agent with that ladHostId).
 */

import { Router } from "express";
import { and, eq, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import { agents } from "@paperclipai/db";
import { ladWatchdogService, type LadHeartbeatBody } from "../services/lad-watchdog.js";

/**
 * Resolve which company this LAD belongs to, constrained by what the
 * board actor is allowed to access. Returns null if none found.
 */
async function resolveCompanyForLad(
  db: Db,
  ladId: string,
  actorCompanyIds: string[] | undefined,
  isInstanceAdmin: boolean,
): Promise<string | null> {
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
