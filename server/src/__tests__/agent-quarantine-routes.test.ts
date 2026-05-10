import express from "express";
import request from "supertest";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const agentId = "aaaa0000-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const managerId = "bbbb0000-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const companyId = "cccc0000-cccc-4ccc-8ccc-cccccccccccc";
const ladId = "lad-host-1";

const activeAgent = {
  id: agentId,
  companyId,
  name: "Test Coder",
  role: "worker",
  title: null,
  status: "idle",
  adapterType: "local_continuous",
  adapterConfig: { ladHostId: ladId, modelTag: "qwen2.5-coder:7b" },
  runtimeConfig: {},
  reportsTo: managerId,
};

const quarantinedAgent = { ...activeAgent, status: "quarantined", pauseReason: "test quarantine" };

const managerAgent = {
  id: managerId,
  companyId,
  name: "Manager",
  role: "director",
  title: null,
  status: "idle",
};

// ──────────────────────────────────────────────────────────────────────────────
// Mocks
// ──────────────────────────────────────────────────────────────────────────────

const mockAgentService = vi.hoisted(() => ({
  getById: vi.fn(),
  getChainOfCommand: vi.fn(),
  quarantine: vi.fn(),
  unquarantine: vi.fn(),
}));

const mockIssueService = vi.hoisted(() => ({
  create: vi.fn(),
}));

const mockHeartbeatService = vi.hoisted(() => ({
  cancelActiveForAgent: vi.fn(),
  wakeup: vi.fn(),
}));

const mockLogActivity = vi.hoisted(() => vi.fn());

const mockDb = vi.hoisted(() => ({}));

function registerMocks() {
  vi.doMock("../services/index.js", () => ({
    agentService: () => mockAgentService,
    issueService: () => mockIssueService,
    heartbeatService: () => mockHeartbeatService,
    logActivity: mockLogActivity,
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
  vi.doMock("../services/activity-log.js", () => ({
    logActivity: mockLogActivity,
  }));
}

async function createWorkClaimApp(
  actor: Record<string, unknown> = { type: "board", source: "api_key", isInstanceAdmin: true, userId: "user-1" },
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
  app.use("/api", agentWorkClaimRoutes(mockDb as any));
  app.use(errorHandler);
  return app;
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /api/local-agent-daemons/:ladId/agent-events
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/local-agent-daemons/:ladId/agent-events", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
    mockLogActivity.mockResolvedValue(undefined);
  });

  it("200: records agent_unquarantined event and returns ok", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "agent_unquarantined", agentId });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.event).toBe("agent_unquarantined");
    expect(res.body.agentId).toBe(agentId);

    // Verify activity was logged.
    expect(mockLogActivity).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        action: "agent.worker_restarted_after_unquarantine",
        entityId: agentId,
      }),
    );
  });

  it("200: accepts unknown events without error (forward-compat)", async () => {
    mockAgentService.getById.mockResolvedValue(activeAgent);

    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "some_future_event", agentId });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(mockLogActivity).not.toHaveBeenCalled();
  });

  it("403: rejects agent JWT callers", async () => {
    const app = await createWorkClaimApp({
      type: "agent",
      agentId,
      companyId,
      source: "agent_jwt",
    });
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "agent_unquarantined", agentId });

    expect(res.status).toBe(403);
  });

  it("403: rejects when ladHostId does not match agent's ladHostId", async () => {
    mockAgentService.getById.mockResolvedValue({
      ...activeAgent,
      adapterConfig: { ladHostId: "other-lad", modelTag: "qwen2.5-coder:7b" },
    });

    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "agent_unquarantined", agentId });

    expect(res.status).toBe(403);
  });

  it("400: rejects missing event field", async () => {
    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ agentId });

    expect(res.status).toBe(400);
  });

  it("400: rejects missing agentId field", async () => {
    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "agent_unquarantined" });

    expect(res.status).toBe(400);
  });

  it("404: rejects unknown agentId", async () => {
    mockAgentService.getById.mockResolvedValue(null);

    const app = await createWorkClaimApp();
    const res = await request(app)
      .post(`/api/local-agent-daemons/${ladId}/agent-events`)
      .send({ event: "agent_unquarantined", agentId: "nonexistent" });

    expect(res.status).toBe(404);
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// work-claim: quarantined agent guard
// ──────────────────────────────────────────────────────────────────────────────

describe("POST /api/agents/:agentId/work-claim — quarantine guard", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    registerMocks();
    process.env.PAPERCLIP_AGENT_JWT_SECRET = "test-jwt-secret";
  });

  it("423: returns locked when agent is quarantined", async () => {
    mockAgentService.getById.mockResolvedValue(quarantinedAgent);

    const app = await createWorkClaimApp({
      type: "agent",
      agentId,
      companyId,
      source: "agent_jwt",
    });
    const res = await request(app).post(`/api/agents/${agentId}/work-claim`);

    expect(res.status).toBe(423);
    expect(res.body.agentStatus).toBe("quarantined");
  });
});
