/**
 * Tests for the local_continuous adapter type (SAG-802):
 * - Heartbeat scheduler suppression
 * - Company feature flag enforcement on create / update
 * - PATCH /api/agents/:id/adapter round-trip migration
 */

import { randomUUID } from "node:crypto";
import express from "express";
import request from "supertest";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Route-test mocks (used by the create / update / adapter-patch tests)
// ---------------------------------------------------------------------------

const mockAgentService = vi.hoisted(() => ({
  create: vi.fn(),
  getById: vi.fn(),
  update: vi.fn(),
  updatePermissions: vi.fn(),
  resolveByReference: vi.fn(),
}));

const mockAccessService = vi.hoisted(() => ({
  canUser: vi.fn(),
  hasPermission: vi.fn(),
  ensureMembership: vi.fn(),
  setPrincipalPermission: vi.fn(),
}));

const mockCompanySkillService = vi.hoisted(() => ({
  listRuntimeSkillEntries: vi.fn(),
  resolveRequestedSkillKeys: vi.fn(),
}));

const mockSecretService = vi.hoisted(() => ({
  normalizeAdapterConfigForPersistence: vi.fn(async (_companyId: string, config: Record<string, unknown>) => config),
  resolveAdapterConfigForRuntime: vi.fn(async (_companyId: string, config: Record<string, unknown>) => ({ config })),
}));

const mockAgentInstructionsService = vi.hoisted(() => ({
  materializeManagedBundle: vi.fn(),
  getBundle: vi.fn(),
  readFile: vi.fn(),
  updateBundle: vi.fn(),
  writeFile: vi.fn(),
  deleteFile: vi.fn(),
  exportFiles: vi.fn(),
  ensureManagedBundle: vi.fn(),
}));

const mockBudgetService = vi.hoisted(() => ({
  upsertPolicy: vi.fn(),
}));

const mockHeartbeatService = vi.hoisted(() => ({
  cancelActiveForAgent: vi.fn(),
}));

const mockIssueApprovalService = vi.hoisted(() => ({
  linkManyForApproval: vi.fn(),
}));

const mockApprovalService = vi.hoisted(() => ({
  create: vi.fn(),
  getById: vi.fn(),
}));

const mockInstanceSettingsService = vi.hoisted(() => ({
  getGeneral: vi.fn(async () => ({ censorUsernameInLogs: false })),
}));

const mockLogActivity = vi.hoisted(() => vi.fn());

vi.mock("../services/index.js", () => ({
  agentService: () => mockAgentService,
  agentInstructionsService: () => mockAgentInstructionsService,
  accessService: () => mockAccessService,
  approvalService: () => mockApprovalService,
  companySkillService: () => mockCompanySkillService,
  budgetService: () => mockBudgetService,
  heartbeatService: () => mockHeartbeatService,
  issueApprovalService: () => mockIssueApprovalService,
  issueService: () => ({}),
  logActivity: mockLogActivity,
  secretService: () => mockSecretService,
  syncInstructionsBundleConfigFromFilePath: vi.fn((_agent: unknown, config: unknown) => config),
  workspaceOperationService: () => ({}),
}));

vi.mock("../services/instance-settings.js", () => ({
  instanceSettingsService: () => mockInstanceSettingsService,
}));

function registerModuleMocks() {
  vi.doMock("../services/index.js", () => ({
    agentService: () => mockAgentService,
    agentInstructionsService: () => mockAgentInstructionsService,
    accessService: () => mockAccessService,
    approvalService: () => mockApprovalService,
    companySkillService: () => mockCompanySkillService,
    budgetService: () => mockBudgetService,
    heartbeatService: () => mockHeartbeatService,
    issueApprovalService: () => mockIssueApprovalService,
    issueService: () => ({}),
    logActivity: mockLogActivity,
    secretService: () => mockSecretService,
    syncInstructionsBundleConfigFromFilePath: vi.fn((_agent: unknown, config: unknown) => config),
    workspaceOperationService: () => ({}),
  }));
  vi.doMock("../services/instance-settings.js", () => ({
    instanceSettingsService: () => mockInstanceSettingsService,
  }));
}

// ---------------------------------------------------------------------------
// App factory — controls localContinuousAdapterEnabled per test
// ---------------------------------------------------------------------------

const COMPANY_ID = "11111111-1111-4111-8111-111111111111";

async function createApp(opts: {
  localContinuousAdapterEnabled?: boolean;
} = {}) {
  const { localContinuousAdapterEnabled = false } = opts;
  const [{ agentRoutes }, { errorHandler }] = await Promise.all([
    vi.importActual<typeof import("../routes/agents.js")>("../routes/agents.js"),
    vi.importActual<typeof import("../middleware/index.js")>("../middleware/index.js"),
  ]);
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    (req as any).actor = {
      type: "board",
      userId: "local-board",
      companyIds: [COMPANY_ID],
      source: "local_implicit",
      isInstanceAdmin: false,
    };
    next();
  });
  const fakeTx = {
    delete: vi.fn(() => ({ where: vi.fn(() => Promise.resolve()) })),
    insert: vi.fn(() => ({ values: vi.fn(() => Promise.resolve()) })),
  };
  const db = {
    select: vi.fn(() => ({
      from: vi.fn(() => ({
        where: vi.fn(async () => [
          {
            id: COMPANY_ID,
            requireBoardApprovalForNewAgents: false,
            localContinuousAdapterEnabled,
          },
        ]),
      })),
    })),
    transaction: vi.fn(async (fn: (tx: typeof fakeTx) => Promise<unknown>) => fn(fakeTx)),
  };
  app.use("/api", agentRoutes(db as any));
  app.use(errorHandler);
  return app;
}

async function httpRequest(
  app: express.Express,
  buildRequest: (baseUrl: string) => request.Test,
) {
  const { createServer } = await vi.importActual<typeof import("node:http")>("node:http");
  const server = createServer(app);
  try {
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected HTTP server to listen on a TCP port");
    }
    return await buildRequest(`http://127.0.0.1:${address.port}`);
  } finally {
    if (server.listening) {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Feature flag enforcement tests (route-level, mock DB)
// ---------------------------------------------------------------------------

describe("local_continuous adapter: feature flag enforcement", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.doUnmock("../routes/agents.js");
    vi.doUnmock("../routes/authz.js");
    vi.doUnmock("../middleware/index.js");
    registerModuleMocks();
    vi.clearAllMocks();
    mockCompanySkillService.listRuntimeSkillEntries.mockResolvedValue([]);
    mockCompanySkillService.resolveRequestedSkillKeys.mockResolvedValue([]);
    mockAccessService.canUser.mockResolvedValue(true);
    mockAccessService.hasPermission.mockResolvedValue(true);
    mockAccessService.ensureMembership.mockResolvedValue(undefined);
    mockAccessService.setPrincipalPermission.mockResolvedValue(undefined);
    mockLogActivity.mockResolvedValue(undefined);
  });

  it("returns 422 when creating a local_continuous agent with feature flag disabled", async () => {
    const app = await createApp({ localContinuousAdapterEnabled: false });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .post(`/api/companies/${COMPANY_ID}/agents`)
        .send({
          name: "Continuous Agent",
          adapterType: "local_continuous",
          adapterConfig: {
            modelTag: "qwen2.5-coder:32b",
            memoryEstimateGB: 20,
            ladHostId: "host-1",
          },
        }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    expect(String(res.body.error ?? res.body.message ?? "")).toMatch(/localContinuousAdapter/);
  });

  it("allows creating a local_continuous agent when feature flag is enabled", async () => {
    const createdAgent = {
      id: "22222222-2222-4222-8222-222222222222",
      companyId: COMPANY_ID,
      name: "Continuous Agent",
      adapterType: "local_continuous",
      adapterConfig: { modelTag: "qwen2.5-coder:32b", memoryEstimateGB: 20, ladHostId: "host-1" },
      runtimeConfig: {},
      permissions: {},
      status: "idle",
      role: "engineer",
    };
    mockAgentService.create.mockResolvedValue(createdAgent);
    mockAgentService.getById.mockResolvedValue(null);
    mockAgentInstructionsService.materializeManagedBundle.mockResolvedValue(createdAgent);
    mockAgentInstructionsService.ensureManagedBundle.mockResolvedValue(createdAgent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .post(`/api/companies/${COMPANY_ID}/agents`)
        .send({
          name: "Continuous Agent",
          adapterType: "local_continuous",
          adapterConfig: {
            modelTag: "qwen2.5-coder:32b",
            memoryEstimateGB: 20,
            ladHostId: "host-1",
          },
        }),
    );

    expect([200, 201], `Got ${res.status}: ${JSON.stringify(res.body)}`).toContain(res.status);
  });
});

// ---------------------------------------------------------------------------
// SAG-1172 §7: PATCH /api/agents/:id — incomplete adapterConfig gate
// ---------------------------------------------------------------------------

describe("local_continuous adapter: PATCH /api/agents/:id rejects incomplete adapterConfig", () => {
  const AGENT_ID = "44444444-4444-4444-8444-444444444444";

  beforeEach(() => {
    vi.resetModules();
    vi.doUnmock("../routes/agents.js");
    vi.doUnmock("../routes/authz.js");
    vi.doUnmock("../middleware/index.js");
    registerModuleMocks();
    vi.clearAllMocks();
    mockAccessService.canUser.mockResolvedValue(true);
    mockAccessService.hasPermission.mockResolvedValue(true);
    mockLogActivity.mockResolvedValue(undefined);
  });

  function makeAgent(adapterType: string, adapterConfig: Record<string, unknown> = {}) {
    return {
      id: AGENT_ID,
      companyId: COMPANY_ID,
      name: "Test Agent",
      adapterType,
      adapterConfig,
      runtimeConfig: {},
      permissions: {},
      status: "idle",
      role: "engineer",
    };
  }

  it("returns 422 with field names when all three required fields are missing", async () => {
    const agent = makeAgent("local_continuous");
    mockAgentService.getById.mockResolvedValue(agent);
    mockAgentService.update.mockResolvedValue(agent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}`)
        .send({
          adapterConfig: {}, // all three fields missing
        }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    const errorMsg = String(res.body.error ?? res.body.message ?? "");
    expect(errorMsg).toMatch(/modelTag/);
    expect(errorMsg).toMatch(/memoryEstimateGB/);
    expect(errorMsg).toMatch(/ladHostId/);
  });

  it("returns 422 naming only the missing field when two are present", async () => {
    const agent = makeAgent("local_continuous");
    mockAgentService.getById.mockResolvedValue(agent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}`)
        .send({
          adapterConfig: {
            modelTag: "llama3.1:8b",
            memoryEstimateGB: 8,
            // ladHostId missing
          },
        }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    const errorMsg = String(res.body.error ?? res.body.message ?? "");
    expect(errorMsg).toMatch(/ladHostId/);
    expect(errorMsg).not.toMatch(/modelTag/);
    expect(errorMsg).not.toMatch(/memoryEstimateGB/);
  });

  it("returns 200 when all three required fields are present", async () => {
    const completeConfig = {
      modelTag: "llama3.1:8b",
      memoryEstimateGB: 8,
      ladHostId: "lad-host-001",
    };
    const agent = makeAgent("local_continuous", completeConfig);
    mockAgentService.getById.mockResolvedValue(agent);
    mockAgentService.update.mockResolvedValue(agent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}`)
        .send({ adapterConfig: completeConfig }),
    );

    expect([200, 201], `Got ${res.status}: ${JSON.stringify(res.body)}`).toContain(res.status);
  });

  it("does NOT apply the gate for opencode_local (no false-positives)", async () => {
    const agent = makeAgent("opencode_local", {});
    mockAgentService.getById.mockResolvedValue(agent);
    mockAgentService.update.mockResolvedValue({ ...agent, name: "Updated" });

    const app = await createApp();
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}`)
        .send({ name: "Updated" }),
    );

    // Should NOT 422 on non-local_continuous adapters
    expect(res.status, JSON.stringify(res.body)).not.toBe(422);
  });
});

// ---------------------------------------------------------------------------
// SAG-1172 §7: POST /api/companies/:id/agents — incomplete adapterConfig gate on create
// ---------------------------------------------------------------------------

describe("local_continuous adapter: POST /api/companies/:id/agents rejects incomplete adapterConfig", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.doUnmock("../routes/agents.js");
    vi.doUnmock("../routes/authz.js");
    vi.doUnmock("../middleware/index.js");
    registerModuleMocks();
    vi.clearAllMocks();
    mockCompanySkillService.listRuntimeSkillEntries.mockResolvedValue([]);
    mockCompanySkillService.resolveRequestedSkillKeys.mockResolvedValue([]);
    mockAccessService.canUser.mockResolvedValue(true);
    mockAccessService.hasPermission.mockResolvedValue(true);
    mockAccessService.ensureMembership.mockResolvedValue(undefined);
    mockAccessService.setPrincipalPermission.mockResolvedValue(undefined);
    mockLogActivity.mockResolvedValue(undefined);
  });

  it("returns 422 with field names when adapterConfig is missing required fields on create", async () => {
    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .post(`/api/companies/${COMPANY_ID}/agents`)
        .send({
          name: "Partial Agent",
          adapterType: "local_continuous",
          adapterConfig: {
            modelTag: "llama3.1:8b",
            // memoryEstimateGB and ladHostId missing
          },
        }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    const errorMsg = String(res.body.error ?? res.body.message ?? "");
    expect(errorMsg).toMatch(/memoryEstimateGB/);
    expect(errorMsg).toMatch(/ladHostId/);
    expect(errorMsg).not.toMatch(/modelTag/);
  });
});

// ---------------------------------------------------------------------------
// PATCH /api/agents/:id/adapter — round-trip migration
// ---------------------------------------------------------------------------

describe("local_continuous adapter: PATCH /api/agents/:id/adapter", () => {
  // Use real UUIDs so router.param("id",...) resolveByReference is bypassed
  const AGENT_ID = "33333333-3333-4333-8333-333333333333";

  beforeEach(() => {
    vi.resetModules();
    vi.doUnmock("../routes/agents.js");
    vi.doUnmock("../routes/authz.js");
    vi.doUnmock("../middleware/index.js");
    registerModuleMocks();
    vi.clearAllMocks();
    mockAccessService.canUser.mockResolvedValue(true);
    mockAccessService.hasPermission.mockResolvedValue(true);
    mockLogActivity.mockResolvedValue(undefined);
  });

  function makeAgent(adapterType: string) {
    return {
      id: AGENT_ID,
      companyId: COMPANY_ID,
      name: "Test Agent",
      adapterType,
      adapterConfig: {},
      runtimeConfig: {},
      permissions: {},
      status: "idle",
      role: "engineer",
    };
  }

  it("migrates opencode_local -> local_continuous when flag is enabled", async () => {
    const originalAgent = makeAgent("opencode_local");
    const updatedAgent = { ...originalAgent, adapterType: "local_continuous", adapterConfig: {} };
    mockAgentService.getById.mockResolvedValue(originalAgent);
    mockAgentService.update.mockResolvedValue(updatedAgent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}/adapter`)
        .send({ adapterType: "local_continuous" }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(200);
    expect(res.body.adapterType).toBe("local_continuous");
    expect(mockAgentService.update).toHaveBeenCalledWith(AGENT_ID, {
      adapterType: "local_continuous",
      adapterConfig: {},
    });
  });

  it("migrates local_continuous -> opencode_local (flag not required)", async () => {
    const originalAgent = makeAgent("local_continuous");
    const updatedAgent = { ...originalAgent, adapterType: "opencode_local", adapterConfig: {} };
    mockAgentService.getById.mockResolvedValue(originalAgent);
    mockAgentService.update.mockResolvedValue(updatedAgent);

    // Feature flag disabled — migrating back to opencode_local must not require the flag
    const app = await createApp({ localContinuousAdapterEnabled: false });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}/adapter`)
        .send({ adapterType: "opencode_local" }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(200);
    expect(res.body.adapterType).toBe("opencode_local");
  });

  it("rejects disallowed transitions (claude_local -> local_continuous)", async () => {
    const originalAgent = makeAgent("claude_local");
    mockAgentService.getById.mockResolvedValue(originalAgent);

    const app = await createApp({ localContinuousAdapterEnabled: true });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}/adapter`)
        .send({ adapterType: "local_continuous" }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    expect(String(res.body.error ?? res.body.message ?? "")).toMatch(/not allowed/);
  });

  it("rejects opencode_local -> local_continuous when flag is disabled", async () => {
    const originalAgent = makeAgent("opencode_local");
    mockAgentService.getById.mockResolvedValue(originalAgent);

    const app = await createApp({ localContinuousAdapterEnabled: false });
    const res = await httpRequest(app, (baseUrl) =>
      request(baseUrl)
        .patch(`/api/agents/${AGENT_ID}/adapter`)
        .send({ adapterType: "local_continuous" }),
    );

    expect(res.status, JSON.stringify(res.body)).toBe(422);
    expect(String(res.body.error ?? res.body.message ?? "")).toMatch(/localContinuousAdapter/);
  });
});

// ---------------------------------------------------------------------------
// Heartbeat scheduler suppression (embedded Postgres)
// ---------------------------------------------------------------------------

import { eq, sql } from "drizzle-orm";
import {
  agents,
  agentRuntimeState,
  companies,
  createDb,
  activityLog,
  agentWakeupRequests,
  heartbeatRuns,
  heartbeatRunEvents,
} from "@paperclipai/db";
import {
  getEmbeddedPostgresTestSupport,
  startEmbeddedPostgresTestDatabase,
} from "./helpers/embedded-postgres.js";
import { heartbeatService } from "../services/heartbeat.ts";

const embeddedPostgresSupport = await getEmbeddedPostgresTestSupport();
const describeEmbeddedPostgres = embeddedPostgresSupport.supported ? describe : describe.skip;

if (!embeddedPostgresSupport.supported) {
  console.warn(
    `Skipping embedded Postgres local_continuous heartbeat suppression tests: ${embeddedPostgresSupport.reason ?? "unsupported"}`,
  );
}

describeEmbeddedPostgres("local_continuous adapter: heartbeat suppression", () => {
  let db!: ReturnType<typeof createDb>;
  let tempDb: Awaited<ReturnType<typeof startEmbeddedPostgresTestDatabase>> | null = null;

  beforeAll(async () => {
    tempDb = await startEmbeddedPostgresTestDatabase("paperclip-lc-heartbeat-");
    db = createDb(tempDb.connectionString);
  }, 20_000);

  async function waitForHeartbeatIdle(timeoutMs = 5_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const active = await db
        .select({ count: sql`count(*)` })
        .from(heartbeatRuns)
        .where(sql`${heartbeatRuns.status} in ('queued', 'running', 'scheduled_retry')`);
      const count = Number((active[0] as any)?.count ?? 0);
      if (count === 0) return;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error("Timed out waiting for heartbeat runs to settle");
  }

  afterEach(async () => {
    // Wait for any background heartbeat execution to finish before deleting
    await waitForHeartbeatIdle().catch(() => {/* proceed with cleanup anyway */});
    // Delete in FK-safe order matching the issue-monitor-scheduler test pattern
    await db.delete(heartbeatRunEvents);
    await db.delete(activityLog);
    await db.delete(heartbeatRuns);
    await db.delete(agentWakeupRequests);
    await db.delete(agentRuntimeState);
    await db.delete(agents);
    await db.delete(companies);
  });

  afterAll(async () => {
    await tempDb?.cleanup();
  });

  async function seedAgent(adapterType: string) {
    const companyId = randomUUID();
    const agentId = randomUUID();
    const issuePrefix = `T${companyId.replace(/-/g, "").slice(0, 6).toUpperCase()}`;

    await db.insert(companies).values({
      id: companyId,
      name: "Test Co",
      issuePrefix,
      requireBoardApprovalForNewAgents: false,
    });
    await db.insert(agents).values({
      id: agentId,
      companyId,
      name: "Test Agent",
      role: "engineer",
      status: "active",
      adapterType,
      adapterConfig: {},
      runtimeConfig: {
        heartbeat: { enabled: true, intervalSec: 1 },
      },
      permissions: {},
      lastHeartbeatAt: new Date(Date.now() - 10_000), // well overdue
    });
    return { companyId, agentId };
  }

  it("does NOT enqueue a timer wake for a local_continuous agent", async () => {
    await seedAgent("local_continuous");
    const heartbeat = heartbeatService(db);

    const result = await heartbeat.tickTimers(new Date());

    expect(result.enqueued).toBe(0);
    const requests = await db.select().from(agentWakeupRequests);
    expect(requests).toHaveLength(0);
  });

  it("DOES enqueue a timer wake for an opencode_local agent (control)", async () => {
    const { agentId } = await seedAgent("opencode_local");
    const heartbeat = heartbeatService(db);

    const result = await heartbeat.tickTimers(new Date());

    expect(result.enqueued).toBeGreaterThanOrEqual(1);
    const requests = await db
      .select()
      .from(agentWakeupRequests)
      .then((rows) => rows.filter((r) => r.agentId === agentId));
    expect(requests.length).toBeGreaterThanOrEqual(1);
  });

  it("skip_push_wake: enqueueWakeup returns null and records a skipped request for local_continuous", async () => {
    const { agentId } = await seedAgent("local_continuous");
    const heartbeat = heartbeatService(db);

    const result = await heartbeat.wakeup(agentId, { source: "on_demand" });

    expect(result).toBeNull();
    const requests = await db
      .select()
      .from(agentWakeupRequests)
      .where(eq(agentWakeupRequests.agentId, agentId));
    expect(requests).toHaveLength(1);
    expect(requests[0]!.status).toBe("skipped");
    expect(requests[0]!.reason).toBe("local_continuous.skip_push_wake");
  });
});
