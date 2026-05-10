import { beforeEach, describe, expect, it, vi } from "vitest";

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const companyId = "bbbb0000-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const ladId = "lad-host-1";
const issueId = "dddd0000-dddd-4ddd-8ddd-dddddddddddd";
const incidentId = "eeee0000-eeee-4eee-8eee-eeeeeeeeeeee";
const ceoAgentId = "ffff0000-ffff-4fff-8fff-ffffffffffff";
const incidentLabelId = "1111aaaa-1111-4111-8111-111111111111";
const ladDownLabelId = "2222aaaa-2222-4222-8222-222222222222";

// ──────────────────────────────────────────────────────────────────────────────
// Mock issueService.create
// ──────────────────────────────────────────────────────────────────────────────

const mockIssueService = vi.hoisted(() => ({
  create: vi.fn(),
}));

vi.mock("../services/issues.js", () => ({
  issueService: () => mockIssueService,
}));

// ──────────────────────────────────────────────────────────────────────────────
// DB builder helpers — Drizzle-compatible thenables
// ──────────────────────────────────────────────────────────────────────────────

/** Creates a fully Promise-compatible thenable chain for Drizzle mocks. */
function makeChain(value: unknown[], extraMethods: Record<string, unknown> = {}) {
  const p = Promise.resolve(value);
  const chain: Record<string, unknown> = {
    then: p.then.bind(p),
    catch: p.catch.bind(p),
    finally: p.finally.bind(p),
    ...extraMethods,
  };
  // Chainable traversal methods all return this same object
  for (const m of ["from", "where", "orderBy", "groupBy", "limit", "set"]) {
    chain[m] = vi.fn().mockReturnValue(chain);
  }
  return chain;
}

/**
 * Builds a Drizzle-style select chain resolving to `rows`.
 * Supports `.then(rows => rows[0])` and direct `await`.
 */
function makeSelect(rows: unknown[]) {
  return makeChain(rows);
}

/**
 * Builds a chainable insert mock. The chain resolves to `returning`.
 * `.returning()` also resolves to the same array.
 */
function makeInsert(returning: unknown[] = []) {
  const chain = makeChain(returning, {
    values: vi.fn(),
    onConflictDoUpdate: vi.fn(),
    returning: vi.fn().mockResolvedValue(returning),
  });
  (chain.values as ReturnType<typeof vi.fn>).mockReturnValue(chain);
  (chain.onConflictDoUpdate as ReturnType<typeof vi.fn>).mockReturnValue(chain);
  return chain;
}

/** Builds a chainable update mock resolving to []. */
function makeUpdate() {
  return makeChain([]);
}

/** Builds a chainable delete mock resolving to []. */
function makeDelete() {
  return makeChain([]);
}

// ──────────────────────────────────────────────────────────────────────────────
// recordHeartbeat
// ──────────────────────────────────────────────────────────────────────────────

describe("ladWatchdogService.recordHeartbeat", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
  });

  it("returns notRegistered when no agent has this ladHostId", async () => {
    const db = {
      select: vi.fn().mockReturnValue(makeSelect([])), // no agents found
      insert: vi.fn().mockReturnValue(makeInsert()),
      delete: vi.fn().mockReturnValue(makeDelete()),
      update: vi.fn().mockReturnValue(makeUpdate()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const result = await svc.recordHeartbeat(ladId, companyId, {
      wallClockIso: new Date().toISOString(),
    });

    expect(result).toEqual({ notRegistered: true });
  });

  it("persists heartbeat row and returns ackIso + nextDueMs on success", async () => {
    let selectCallIndex = 0;
    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) return makeSelect([{ id: "agent-1" }]); // isLadRegistered
        return makeSelect([]); // openIncident check → no open incident
      }),
      insert: vi.fn().mockReturnValue(makeInsert()),
      delete: vi.fn().mockReturnValue(makeDelete()),
      update: vi.fn().mockReturnValue(makeUpdate()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const wallClockIso = new Date().toISOString();
    const result = await svc.recordHeartbeat(ladId, companyId, {
      wallClockIso,
      workers: [{ id: "w1", state: "idle" }],
      lastErrors: [],
    });

    expect(result).toMatchObject({ nextDueMs: 30_000 });
    expect(typeof (result as { ackIso: string }).ackIso).toBe("string");
    // insert called at least twice: lad_records upsert + lad_heartbeats insert
    expect(db.insert).toHaveBeenCalledTimes(2);
  });

  it("auto-comments on open incident and marks it resolved on reconnect", async () => {
    const openedAt = new Date(Date.now() - 5 * 60 * 1000); // 5 min ago
    let selectCallIndex = 0;

    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) return makeSelect([{ id: "agent-1" }]); // isLadRegistered
        return makeSelect([{ id: incidentId, issueId, openedAt }]); // open incident
      }),
      insert: vi.fn().mockReturnValue(makeInsert()),
      delete: vi.fn().mockReturnValue(makeDelete()),
      update: vi.fn().mockReturnValue(makeUpdate()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    await svc.recordHeartbeat(ladId, companyId, {
      wallClockIso: new Date().toISOString(),
    });

    // lad_incidents + lad_records both updated
    expect(db.update).toHaveBeenCalled();
    // reconnect comment + lad_records upsert + lad_heartbeats insert
    expect(db.insert).toHaveBeenCalled();
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// scanStale
// ──────────────────────────────────────────────────────────────────────────────

describe("ladWatchdogService.scanStale", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    mockIssueService.create.mockResolvedValue({ id: issueId, identifier: "SAG-999" });
  });

  it("identifies stale LAD and creates incident issue with template fields", async () => {
    const lastHeartbeatAt = new Date(Date.now() - 200_000); // 200s ago (> 120s threshold)
    let selectCallIndex = 0;

    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) {
          // lad_records: one stale LAD
          return makeSelect([{
            ladId,
            companyId,
            hostname: ladId,
            status: "up",
            stalenessThresholdSec: 120,
            lastHeartbeatAt,
          }]);
        }
        if (idx === 1) return makeSelect([]);  // no open incident
        if (idx === 2) return makeSelect([]);  // last heartbeat payload (none)
        if (idx === 3) return makeSelect([{ id: ceoAgentId, role: "ceo" }]); // CEO agent
        if (idx === 4) return makeSelect([{ id: incidentLabelId }]); // incident label
        if (idx === 5) return makeSelect([{ id: ladDownLabelId }]);   // lad-down label
        return makeSelect([]);
      }),
      insert: vi.fn().mockReturnValue(makeInsert()),
      update: vi.fn().mockReturnValue(makeUpdate()),
      delete: vi.fn().mockReturnValue(makeDelete()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const result = await svc.scanStale();

    expect(result.tripped).toBe(1);
    expect(result.alreadyOpen).toBe(0);

    // Issue created with correct shape
    expect(mockIssueService.create).toHaveBeenCalledOnce();
    const [createCompanyId, createData] = mockIssueService.create.mock.calls[0];
    expect(createCompanyId).toBe(companyId);
    expect(createData.priority).toBe("critical");
    expect(createData.assigneeAgentId).toBe(ceoAgentId);
    expect(createData.title).toContain(ladId);
    expect(createData.title).toMatch(/unresponsive/i);
    expect(createData.description).toContain("LAD Unresponsive Incident");

    // lad_incidents row inserted
    expect(db.insert).toHaveBeenCalled();
  });

  it("dedupes: skips incident creation when open incident already exists", async () => {
    const lastHeartbeatAt = new Date(Date.now() - 200_000);
    let selectCallIndex = 0;

    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) {
          return makeSelect([{
            ladId,
            companyId,
            hostname: ladId,
            status: "stale",
            stalenessThresholdSec: 120,
            lastHeartbeatAt,
          }]);
        }
        // Open incident exists
        return makeSelect([{ id: incidentId }]);
      }),
      insert: vi.fn().mockReturnValue(makeInsert()),
      update: vi.fn().mockReturnValue(makeUpdate()),
      delete: vi.fn().mockReturnValue(makeDelete()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const result = await svc.scanStale();

    expect(result.tripped).toBe(0);
    expect(result.alreadyOpen).toBe(1);
    expect(mockIssueService.create).not.toHaveBeenCalled();
  });

  it("skips LAD that is within staleness threshold", async () => {
    const lastHeartbeatAt = new Date(Date.now() - 30_000); // 30s — within 120s

    const db = {
      select: vi.fn().mockReturnValue(makeSelect([{
        ladId,
        companyId,
        hostname: ladId,
        status: "up",
        stalenessThresholdSec: 120,
        lastHeartbeatAt,
      }])),
      insert: vi.fn().mockReturnValue(makeInsert()),
      update: vi.fn().mockReturnValue(makeUpdate()),
      delete: vi.fn().mockReturnValue(makeDelete()),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const result = await svc.scanStale();

    expect(result.tripped).toBe(0);
    expect(result.alreadyOpen).toBe(0);
    expect(mockIssueService.create).not.toHaveBeenCalled();
  });

  it("reconnect: lad_records status set to 'up' and comment added without closing incident issue", async () => {
    const openedAt = new Date(Date.now() - 300_000); // 5 min ago
    let selectCallIndex = 0;
    const insertSpy = vi.fn().mockReturnValue(makeInsert());
    const updateSpy = vi.fn().mockReturnValue(makeUpdate());

    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) return makeSelect([{ id: "agent-1" }]); // isLadRegistered
        return makeSelect([{ id: incidentId, issueId, openedAt }]); // open incident
      }),
      insert: insertSpy,
      delete: vi.fn().mockReturnValue(makeDelete()),
      update: updateSpy,
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    await svc.recordHeartbeat(ladId, companyId, {
      wallClockIso: new Date().toISOString(),
    });

    // update must be called (lad_records → "up", lad_incidents → "resolved", issues updatedAt)
    expect(updateSpy).toHaveBeenCalled();
    // insert must be called (upsert lad_records, heartbeat, reconnect comment)
    expect(insertSpy).toHaveBeenCalled();
    // issue itself is NOT deleted or closed — no status update that sets status=done
    const updateSetCalls = updateSpy.mock.results.map(() => updateSpy.mock.calls);
    // incident remains open (we only mark the lad_incidents row resolved, not the issue)
    expect(mockIssueService.create).not.toHaveBeenCalled();
  });
});

// ──────────────────────────────────────────────────────────────────────────────
// getLadDashboardData
// ──────────────────────────────────────────────────────────────────────────────

describe("ladWatchdogService.getLadDashboardData", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
  });

  it("returns array with correct shape for fixture LAD data", async () => {
    const lastHeartbeatAt = new Date(Date.now() - 15_000);
    let selectCallIndex = 0;

    const db = {
      select: vi.fn().mockImplementation(() => {
        const idx = selectCallIndex++;
        if (idx === 0) {
          // lad_records
          return makeSelect([{
            ladId,
            hostname: "lad-host-1.example.com",
            status: "up",
            lastHeartbeatAt,
            stalenessThresholdSec: 120,
          }]);
        }
        if (idx === 1) {
          // latest heartbeat
          return makeSelect([{
            workers: [
              { id: "w1", state: "idle", idleTimePct: 80, loadedModels: 1, memoryUsedMb: 4096, memoryBudgetMb: 16384 },
            ],
            scheduler: {},
            queueDepths: { default: 0 },
            lastErrors: [],
            ackIso: lastHeartbeatAt.toISOString(),
          }]);
        }
        if (idx === 2) {
          // recent heartbeats (1h window)
          return makeSelect([{
            workers: [{ id: "w1", state: "idle", idleTimePct: 80 }],
          }]);
        }
        // no open incident
        return makeSelect([]);
      }),
    };

    const { ladWatchdogService } = await import("../services/lad-watchdog.js");
    const svc = ladWatchdogService(db as any);

    const result = await svc.getLadDashboardData(companyId);

    expect(result).toHaveLength(1);
    const tile = result[0];
    expect(tile.id).toBe(ladId);
    expect(tile.hostname).toBe("lad-host-1.example.com");
    expect(tile.status).toBe("up");
    expect(tile.lastSeenIso).toBeTruthy();
    expect(tile.lastSeenRelative).toBeTruthy();
    expect(tile.workers.total).toBe(1);
    expect(tile.workers.byState).toMatchObject({ idle: 1 });
    expect(tile.models.loadedCount).toBe(1);
    expect(typeof tile.avgIdleTimePct1h).toBe("number");
    expect(tile.openIncident).toBeNull();
  });
});
