import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const agentId = "aaaa0000-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const companyId = "bbbb0000-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const managerId = "cccc0000-cccc-4ccc-8ccc-cccccccccccc";
const issueId = "dddd0000-dddd-4ddd-8ddd-dddddddddddd";
const runId = "eeee0000-eeee-4eee-8eee-eeeeeeeeeeee";

const activeAgent = {
  id: agentId,
  companyId,
  name: "Test Agent",
  role: "worker",
  title: null,
  status: "idle",
  adapterType: "local_continuous",
  adapterConfig: { ladHostId: "lad-host-1", modelTag: "qwen2.5-coder:7b" },
  runtimeConfig: {},
  reportsTo: managerId,
};

const managerAgent = {
  id: managerId,
  companyId,
  name: "Manager",
  role: "director",
  title: null,
};

const sampleIssue = {
  id: issueId,
  identifier: "TEST-1",
  title: "Fix the widget",
  status: "todo",
  priority: "high",
  checkoutRunId: null,
};

// ──────────────────────────────────────────────────────────────────────────────
// Mocks (hoisted so vi.mock calls are available before imports)
// ──────────────────────────────────────────────────────────────────────────────

const mockAgentService = vi.hoisted(() => ({
  getById: vi.fn(),
  getChainOfCommand: vi.fn(),
}));

const mockIssueService = vi.hoisted(() => ({
  checkout: vi.fn(),
}));

const mockHeartbeatService = vi.hoisted(() => ({
  wakeup: vi.fn(),
}));

const mockDb = vi.hoisted(() => ({
  select: vi.fn(),
  insert: vi.fn(),
}));

// ──────────────────────────────────────────────────────────────────────────────
// Module registration helpers
// ──────────────────────────────────────────────────────────────────────────────

function registerMocks() {
  vi.doMock("../services/index.js", () => ({
    agentService: () => mockAgentService,
    issueService: () => mockIssueService,
    heartbeatService: () => mockHeartbeatService,
  }));

  vi.doMock("../services/agents.js", () => ({
    agentService: () => mockAgentService,
  }));

  vi.doMock("../services/issues.js", () => ({
    issueService: () => mockIssueService,
  }));

  vi.doMock("../services/heartbeat.js", () => ({
    heartbeatService: () => mockHeartbeatService,
  }));
}

async function createApp(
  actor: Record<string, unknown> = {
    type: "agent",
    agentId,
    companyId,
    source: "agent_jwt",
    runId: undefined,
  },
  dbOverride?: Record<string, unknown>,
) {
  const [{ agentWorkClaimRoutes }, { errorHandler }] = await Promise.all([
    vi.importActual<typeof import("../routes/agent-work-claim.js")>("../routes/agent-work-claim.js"),
    vi.importActual<typeof import("../middleware/index.js")>("../middleware/index.js"),
  ]);

  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    (req as any).actor = actor;
    next();
  });
  app.use("/api", agentWorkClaimRoutes((dbOverride ?? mockDb) as any));
  app.use(errorHandler);
  return app;
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /api/agents/:agentId/work-claim
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/agents/:agentId/work-claim", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
    process.env.PAPERCLIP_AGENT_JWT_SECRET = "test-jwt-secret";
    process.env.PAPERCLIP_AGENT_JWT_TTL_SECONDS = "3600";
  });

  it("200: returns issue + runId + runToken on happy path", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    // DB stub: findNextIssue returns a candidate
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockReturnThis(),
      orderBy: vi.fn().mockReturnThis(),
      limit: vi.fn().mockResolvedValue([sampleIssue]),
    };
    mockDb.select.mockReturnValue(selectChain);

    // DB stub: createClaimRun inserts a heartbeat run
    const insertChain = {
      values: vi.fn().mockReturnThis(),
      returning: vi.fn().mockResolvedValue([{ id: runId }]),
    };
    mockDb.insert.mockReturnValue(insertChain);

    // checkout resolves with the issue
    mockIssueService.checkout.mockResolvedValue({ ...sampleIssue, status: "in_progress", checkoutRunId: runId });

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    expect(res.status).toBe(200);
    expect(res.body.issue.id).toBe(issueId);
    expect(typeof res.body.runId).toBe("string");
    expect(typeof res.body.runToken).toBe("string");
    expect(typeof res.body.runTokenTtlSec).toBe("number");
    expect(res.body.modelHint).toBe("qwen2.5-coder:7b");
  });

  it("204: returns empty when no issues are available within poll window", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    // DB always returns empty — simulates no available work.
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockReturnThis(),
      orderBy: vi.fn().mockReturnThis(),
      limit: vi.fn().mockResolvedValue([]),
    };
    mockDb.select.mockReturnValue(selectChain);

    // Override timeout to 0ms so we don't wait 60s in CI.
    process.env.PAPERCLIP_WORK_CLAIM_TIMEOUT_MS = "0";

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    delete process.env.PAPERCLIP_WORK_CLAIM_TIMEOUT_MS;
    expect(res.status).toBe(204);
  });

  it("409: retries and eventually returns 204 when checkout always conflicts", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockReturnThis(),
      orderBy: vi.fn().mockReturnThis(),
      limit: vi.fn().mockResolvedValue([sampleIssue]),
    };
    mockDb.select.mockReturnValue(selectChain);

    // Run insert succeeds
    const insertChain = {
      values: vi.fn().mockReturnThis(),
      returning: vi.fn().mockResolvedValue([{ id: runId }]),
    };
    mockDb.insert.mockReturnValue(insertChain);

    // Checkout always throws a conflict (409)
    const conflictErr = Object.assign(new Error("conflict"), { status: 409 });
    mockIssueService.checkout.mockRejectedValue(conflictErr);

    process.env.PAPERCLIP_WORK_CLAIM_TIMEOUT_MS = "0";

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    delete process.env.PAPERCLIP_WORK_CLAIM_TIMEOUT_MS;
    // After exhausting the poll window, returns 204 idle.
    expect(res.status).toBe(204);
  });

  it("423: returns locked when agent is paused", async () => {
    mockAgentService.getById.mockResolvedValue({ ...activeAgent, status: "paused" });

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    expect(res.status).toBe(423);
    expect(res.body.agentStatus).toBe("paused");
  });

  it("403: rejects when a different agent tries to claim work", async () => {
    const otherAgentActor = { type: "agent", agentId: "other-agent", companyId, source: "agent_jwt" };
    const app = await createApp(otherAgentActor);
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    expect(res.status).toBe(403);
  });

  it("404: returns not found for unknown agentId", async () => {
    mockAgentService.getById.mockResolvedValue(null);

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    expect(res.status).toBe(404);
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// POST /api/agents/:agentId/request-work  (agent_needs_work integration test)
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/agents/:agentId/request-work", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
    process.env.PAPERCLIP_AGENT_JWT_SECRET = "test-jwt-secret";
  });

  it("202: emits agent_needs_work wake to manager and returns requestId", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);
    mockAgentService.getChainOfCommand.mockResolvedValue([managerAgent]);
    mockHeartbeatService.wakeup.mockResolvedValue({ id: "run-xyz" });

    // DB insert stub for agentWorkRequests
    const insertChain = {
      values: vi.fn().mockResolvedValue([]),
    };
    mockDb.insert.mockReturnValue(insertChain);

    const app = await createApp();
    const res = await request(app)
      .post(`/api/agents/${agentId}/request-work`)
      .send({ idleSinceIso: "2026-05-10T00:00:00.000Z", lastIssueId: issueId });

    expect(res.status).toBe(202);
    expect(typeof res.body.requestId).toBe("string");
    expect(res.body.managerAgentId).toBe(managerId);
    expect(typeof res.body.pollAfterMs).toBe("number");

    // Verify the wake was emitted with agent_needs_work reason and correct payload
    expect(mockHeartbeatService.wakeup).toHaveBeenCalledWith(
      managerId,
      expect.objectContaining({
        reason: "agent_needs_work",
        payload: expect.objectContaining({
          idleAgent: agentId,
          idleSinceIso: "2026-05-10T00:00:00.000Z",
          lastIssueId: issueId,
        }),
        contextSnapshot: expect.objectContaining({
          wakeReason: "agent_needs_work",
          idleAgent: agentId,
        }),
      }),
    );
  });

  it("422: returns unprocessable when agent has no manager", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);
    mockAgentService.getChainOfCommand.mockResolvedValue([]);

    const app = await createApp();
    const res = await request(app).post(`/api/agents/${agentId}/request-work`);

    expect(res.status).toBe(422);
  });

  it("403: rejects cross-agent requests", async () => {
    const otherActor = { type: "agent", agentId: "other-agent", companyId, source: "agent_jwt" };
    const app = await createApp(otherActor);
    const res = await request(app).post(`/api/agents/${agentId}/request-work`);

    expect(res.status).toBe(403);
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// POST /api/local-agent-daemons/:ladId/agent-tokens
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/local-agent-daemons/:ladId/agent-tokens", () => {
  const boardActor = {
    type: "board",
    userId: "local-board",
    companyIds: [companyId],
    source: "board_key",
    isInstanceAdmin: false,
  };

  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
    process.env.PAPERCLIP_AGENT_JWT_SECRET = "test-jwt-secret";
    process.env.PAPERCLIP_AGENT_JWT_TTL_SECONDS = "3600";
  });

  it("200: mints a run token for a valid agent+lad combination", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    const app = await createApp(boardActor);
    const res = await request(app)
      .post("/api/local-agent-daemons/lad-host-1/agent-tokens")
      .send({ agentId });

    expect(res.status).toBe(200);
    expect(typeof res.body.runToken).toBe("string");
    expect(typeof res.body.ttlSec).toBe("number");
  });

  it("403: rejects when ladHostId does not match", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    const app = await createApp(boardActor);
    const res = await request(app)
      .post("/api/local-agent-daemons/wrong-lad-id/agent-tokens")
      .send({ agentId });

    expect(res.status).toBe(403);
  });

  it("403: rejects agent-key callers (not board)", async () => {
    const agentActor = { type: "agent", agentId, companyId, source: "agent_jwt" };
    const app = await createApp(agentActor);
    const res = await request(app)
      .post("/api/local-agent-daemons/lad-host-1/agent-tokens")
      .send({ agentId });

    expect(res.status).toBe(403);
  });

  it("400: rejects missing agentId body field", async () => {
    const app = await createApp(boardActor);
    const res = await request(app)
      .post("/api/local-agent-daemons/lad-host-1/agent-tokens")
      .send({});

    expect(res.status).toBe(400);
  });

  it("404: returns not found for unknown agentId", async () => {
    mockAgentService.getById.mockResolvedValue(null);

    const app = await createApp(boardActor);
    const res = await request(app)
      .post("/api/local-agent-daemons/lad-host-1/agent-tokens")
      .send({ agentId });

    expect(res.status).toBe(404);
  });
});
