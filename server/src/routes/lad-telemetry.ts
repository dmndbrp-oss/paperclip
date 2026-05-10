/**
 * @fileoverview LAD telemetry ingest and query endpoints.
 *
 * POST /api/local-adapter-daemons/:ladId/telemetry
 *   Service-principal (board API key) auth. Accepts a telemetry batch,
 *   writes rows to local_agent_metrics and lad_scheduler_metrics in one
 *   transaction. Returns 202 Accepted.
 *
 * GET /api/local-agent-daemons/:ladId/agent-metrics
 *   Board or agent JWT auth. Returns metric series for ?agentId= over
 *   a rolling 1h window (configurable via ?since= ISO param).
 */

import { Router } from "express";
import { and, eq, sql } from "drizzle-orm";
import type { Db } from "@paperclipai/db";
import { agents } from "@paperclipai/db";
import { ladTelemetryService, type TelemetryIngestBody } from "../services/lad-telemetry.js";

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

export function ladTelemetryRoutes(db: Db) {
  const router = Router();
  const svc = ladTelemetryService(db);

  // POST /api/local-adapter-daemons/:ladId/telemetry
  router.post("/local-adapter-daemons/:ladId/telemetry", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (req.actor.type !== "board") {
      res.status(403).json({ error: "Board API key required" });
      return;
    }

    const isInstanceAdmin =
      req.actor.source === "local_implicit" || req.actor.isInstanceAdmin === true;
    const companyId = await resolveCompanyForLad(
      db,
      ladId,
      req.actor.companyIds,
      isInstanceAdmin,
    );
    if (!companyId) {
      res.status(410).json({ error: "Gone: ladId not registered" });
      return;
    }

    const body = req.body as Partial<TelemetryIngestBody>;
    if (
      !body.window ||
      typeof body.window.startIso !== "string" ||
      typeof body.window.endIso !== "string"
    ) {
      res.status(400).json({ error: "window.startIso and window.endIso are required" });
      return;
    }
    if (!Array.isArray(body.perAgent)) {
      res.status(400).json({ error: "perAgent must be an array" });
      return;
    }
    if (!body.scheduler || typeof body.scheduler !== "object") {
      res.status(400).json({ error: "scheduler object is required" });
      return;
    }

    const ingestBody: TelemetryIngestBody = {
      window: { startIso: body.window.startIso, endIso: body.window.endIso },
      perAgent: body.perAgent,
      scheduler: body.scheduler,
    };

    await svc.ingest(ladId, companyId, ingestBody);
    res.status(202).json({ accepted: true });
  });

  // GET /api/local-adapter-daemons/:ladId/agent-metrics?agentId=...&since=...
  router.get("/local-adapter-daemons/:ladId/agent-metrics", async (req, res) => {
    const { ladId } = req.params as { ladId: string };

    if (req.actor.type === "none") {
      res.status(401).json({ error: "Authentication required" });
      return;
    }

    const isInstanceAdmin =
      req.actor.type === "board" &&
      (req.actor.source === "local_implicit" || req.actor.isInstanceAdmin === true);
    const actorCompanyIds = req.actor.type === "board" ? req.actor.companyIds : undefined;
    const companyId = await resolveCompanyForLad(db, ladId, actorCompanyIds, isInstanceAdmin);
    if (!companyId) {
      res.status(410).json({ error: "Gone: ladId not registered" });
      return;
    }

    const { agentId, since } = req.query as { agentId?: string; since?: string };
    if (!agentId) {
      res.status(400).json({ error: "agentId query param required" });
      return;
    }

    const sinceDate = since
      ? new Date(since)
      : new Date(Date.now() - 60 * 60 * 1000);

    const series = await svc.getAgentSeries(companyId, agentId, sinceDate);
    res.json({ ladId, agentId, series });
  });

  return router;
}
