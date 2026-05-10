import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const companyId = "bbbb0000-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const ladId = "lad-host-1";
const agentId = "aaaa0000-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

// ──────────────────────────────────────────────────────────────────────────────
// Mocks (hoisted)
// ──────────────────────────────────────────────────────────────────────────────

const mockWatchdogService = vi.hoisted(() => ({
  recordHeartbeat: vi.fn(),
  scanStale: vi.fn(),
  getLadDashboardData: vi.fn(),
}));

const mockDb = vi.hoisted(() => ({
  select: vi.fn(),
}));

function registerMocks() {
  vi.doMock("../services/lad-watchdog.js", () => ({
    ladWatchdogService: () => mockWatchdogService,
  }));
}

async function createApp(actor: Record<string, unknown> = {
  type: "board",
  source: "board_key",
  companyIds: [companyId],
  isInstanceAdmin: false,
}) {
  const [{ ladWatchdogRoutes }, { errorHandler }] = await Promise.all([
    vi.importActual<typeof import("../routes/lad-watchdog.js")>("../routes/lad-watchdog.js"),
    vi.importActual<typeof import("../middleware/index.js")>("../middleware/index.js"),
  ]);

  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    (req as any).actor = actor;
    next();
  });

  // Stub db.select for resolveCompanyForLad: returns one agent row for ladId
  const selectChain = {
    from: vi.fn().mockReturnThis(),
    where: vi.fn().mockReturnThis(),
    limit: vi.fn().mockResolvedValue([{ companyId }]),
  };
  mockDb.select.mockReturnValue(selectChain);

  app.use("/api", ladWatchdogRoutes(mockDb as any));
  app.use(errorHandler);
  return app;
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /api/local-adapter-daemons/:ladId/heartbeat
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/local-adapter-daemons/:ladId/heartbeat", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
  });

  it("200: persists heartbeat and returns ackIso + nextDueMs", async () => {
    const ackIso = new Date().toISOString();
    mockWatchdogService.recordHeartbeat.mockResolvedValue({ ackIso, nextDueMs: 30_000 });

    const app = await createApp();
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/heartbeat`)
      .send({
        wallClockIso: ackIso,
        workers: [{ id: "w1", state: "idle" }],
        scheduler: { queue: "default" },
        queueDepths: { default: 0 },
        lastErrors: [],
      });

    expect(res.status).toBe(200);
    expect(res.body.ackIso).toBe(ackIso);
    expect(res.body.nextDueMs).toBe(30_000);
    expect(mockWatchdogService.recordHeartbeat).toHaveBeenCalledWith(
      ladId,
      companyId,
      expect.objectContaining({ wallClockIso: ackIso }),
    );
  });

  it("410: returns Gone when ladId is not registered", async () => {
    mockWatchdogService.recordHeartbeat.mockResolvedValue({ notRegistered: true });

    const app = await createApp();
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/heartbeat`)
      .send({ wallClockIso: new Date().toISOString() });

    expect(res.status).toBe(410);
    expect(res.body.error).toMatch(/not registered/i);
  });

  it("410: returns Gone when no agent in company has this ladHostId", async () => {
    // Simulate db.select returning no rows (no agent registered with this ladId)
    mockWatchdogService.recordHeartbeat.mockResolvedValue({ notRegistered: true });

    const { ladWatchdogRoutes } = await vi.importActual<typeof import("../routes/lad-watchdog.js")>("../routes/lad-watchdog.js");
    const { errorHandler } = await vi.importActual<typeof import("../middleware/index.js")>("../middleware/index.js");

    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as any).actor = {
        type: "board",
        source: "board_key",
        companyIds: [companyId],
        isInstanceAdmin: false,
      };
      next();
    });

    // Empty select result → resolveCompanyForLad returns null
    const emptySelectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockReturnThis(),
      limit: vi.fn().mockResolvedValue([]),
    };
    const emptyDb = { select: vi.fn().mockReturnValue(emptySelectChain) };

    app.use("/api", ladWatchdogRoutes(emptyDb as any));
    app.use(errorHandler);

    const res = await request(app)
      .post(`/api/local-adapter-daemons/unknown-lad/heartbeat`)
      .send({ wallClockIso: new Date().toISOString() });

    expect(res.status).toBe(410);
  });

  it("401: returns Unauthorized when unauthenticated", async () => {
    const app = await createApp({ type: "none" });
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/heartbeat`)
      .send({ wallClockIso: new Date().toISOString() });

    expect(res.status).toBe(401);
  });

  it("403: returns Forbidden when agent key used instead of board key", async () => {
    const app = await createApp({
      type: "agent",
      agentId,
      companyId,
      source: "agent_jwt",
    });
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/heartbeat`)
      .send({ wallClockIso: new Date().toISOString() });

    expect(res.status).toBe(403);
  });

  it("400: returns Bad Request when wallClockIso is missing", async () => {
    const app = await createApp();
    const res = await request(app)
      .post(`/api/local-adapter-daemons/${ladId}/heartbeat`)
      .send({});

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/wallClockIso/i);
  });
});
