import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";

const companyId = "cccc0000-cccc-4ccc-8ccc-cccccccccccc";
const ladId = "lad-host-test";
const agentId = "dddd0000-dddd-4ddd-8ddd-dddddddddddd";

const mockTelemetryService = vi.hoisted(() => ({
  ingest: vi.fn().mockResolvedValue(undefined),
  getAgentSeries: vi.fn().mockResolvedValue([
    { key: "idle_time_pct", rows: [{ ts: new Date().toISOString(), value: 42 }] },
  ]),
  pruneOldMetrics: vi.fn().mockResolvedValue({ agentRows: 0, schedulerRows: 0 }),
}));

const mockDb = vi.hoisted(() => ({
  select: vi.fn(),
}));

function registerMocks() {
  vi.doMock("../services/lad-telemetry.js", () => ({
    ladTelemetryService: () => mockTelemetryService,
  }));
}

async function createApp(
  actor: Record<string, unknown> = {
    type: "board",
    source: "board_key",
    companyIds: [companyId],
    isInstanceAdmin: false,
  },
) {
  const [{ ladTelemetryRoutes }, { errorHandler }] = await Promise.all([
    vi.importActual<typeof import("../routes/lad-telemetry.js")>("../routes/lad-telemetry.js"),
    vi.importActual<typeof import("../middleware/index.js")>("../middleware/index.js"),
  ]);

  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    (req as any).actor = actor;
    next();
  });

  const selectChain = {
    from: vi.fn().mockReturnThis(),
    where: vi.fn().mockReturnThis(),
    limit: vi.fn().mockResolvedValue([{ companyId }]),
  };
  mockDb.select.mockReturnValue(selectChain);

  app.use("/api", ladTelemetryRoutes(mockDb as any));
  app.use(errorHandler);
  return app;
}

describe("POST /api/local-adapter-daemons/:ladId/telemetry", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    registerMocks();
  });

  const validBody = {
    window: {
      startIso: new Date(Date.now() - 30_000).toISOString(),
      endIso: new Date().toISOString(),
    },
    perAgent: [
      {
        agentId,
        metrics: {
          idle_time_pct: 25,
          model_swap_count: 1,
          queue_empty_events: 3,
          manager_response_latency_ms: 140,
        },
      },
    ],
    scheduler: {
      loaded_models_count: 2,
      memory_used_gb: 8.5,
      budget_utilization_pct: 60,
      ollama_healthcheck_ok: true,
    },
  };

  it("returns 202 with valid body and board auth", async () => {
    const app = await createApp();
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(validBody);
    expect(res.status).toBe(202);
    expect(res.body).toEqual({ accepted: true });
    expect(mockTelemetryService.ingest).toHaveBeenCalledWith(ladId, companyId, validBody);
  });

  it("returns 401 when unauthenticated", async () => {
    const app = await createApp({ type: "none" });
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(validBody);
    expect(res.status).toBe(401);
  });

  it("returns 403 when actor is agent JWT not board key", async () => {
    const app = await createApp({ type: "agent", agentId });
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(validBody);
    expect(res.status).toBe(403);
  });

  it("returns 400 when window is missing", async () => {
    const app = await createApp();
    const { window: _w, ...noWindow } = validBody;
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(noWindow);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/window/i);
  });

  it("returns 400 when perAgent is missing", async () => {
    const app = await createApp();
    const { perAgent: _pa, ...noPerAgent } = validBody;
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(noPerAgent);
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/perAgent/i);
  });

  it("returns 410 when ladId not registered", async () => {
    const app = await createApp();
    mockDb.select.mockReturnValue({
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockReturnThis(),
      limit: vi.fn().mockResolvedValue([]),
    });
    const res = await request(app)
      .post(`/api/local-adapter-daemons/unknown-lad/telemetry`)
      .send(validBody);
    expect(res.status).toBe(410);
  });
});

describe("GET /api/local-adapter-daemons/:ladId/agent-metrics", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    registerMocks();
  });

  it("returns metric series for a registered LAD and agent", async () => {
    const app = await createApp();
    const res = await request(app)
      .get(`/api/local-adapter-daemons/${ladId}/agent-metrics`)
      .query({ agentId });
    expect(res.status).toBe(200);
    expect(res.body.agentId).toBe(agentId);
    expect(Array.isArray(res.body.series)).toBe(true);
    expect(res.body.series[0].key).toBe("idle_time_pct");
  });

  it("returns 400 when agentId missing", async () => {
    const app = await createApp();
    const res = await request(app).get(
      `/api/local-adapter-daemons/${ladId}/agent-metrics`,
    );
    expect(res.status).toBe(400);
  });

  it("ingest writes both agent and scheduler rows in one transaction", async () => {
    mockTelemetryService.ingest.mockResolvedValue(undefined);
    const app = await createApp();
    const body = {
      window: {
        startIso: new Date(Date.now() - 30_000).toISOString(),
        endIso: new Date().toISOString(),
      },
      perAgent: [
        { agentId, metrics: { idle_time_pct: 10, model_swap_count: 2 } },
      ],
      scheduler: { loaded_models_count: 1, ollama_healthcheck_ok: true },
    };
    await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/telemetry`)
      .send(body);
    expect(mockTelemetryService.ingest).toHaveBeenCalledOnce();
    const call = mockTelemetryService.ingest.mock.calls[0];
    expect(call[0]).toBe(ladId);
    expect(call[1]).toBe(companyId);
    expect(call[2].perAgent[0].agentId).toBe(agentId);
    expect(call[2].scheduler.loaded_models_count).toBe(1);
  });
});
