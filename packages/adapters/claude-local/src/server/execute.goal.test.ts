import { mkdir, mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Module mocks ──────────────────────────────────────────────────────────────

const {
  runAdapterExecutionTargetProcess,
  ensureCommandResolvable,
  resolveCommandForLogs,
} = vi.hoisted(() => ({
  runAdapterExecutionTargetProcess: vi.fn(async () => ({
    exitCode: 0,
    signal: null,
    timedOut: false,
    stdout: [
      JSON.stringify({ type: "system", subtype: "init", session_id: "goal-session-1", model: "claude-haiku" }),
      JSON.stringify({ type: "result", session_id: "goal-session-1", result: "done", usage: { input_tokens: 5, cache_read_input_tokens: 0, output_tokens: 5 } }),
    ].join("\n"),
    stderr: "",
    pid: 99,
    startedAt: new Date().toISOString(),
  })),
  ensureCommandResolvable: vi.fn(async () => undefined),
  resolveCommandForLogs: vi.fn(async () => "claude"),
}));

vi.mock("@paperclipai/adapter-utils/execution-target", async () => {
  const actual = await vi.importActual<typeof import("@paperclipai/adapter-utils/execution-target")>(
    "@paperclipai/adapter-utils/execution-target",
  );
  return { ...actual, runAdapterExecutionTargetProcess };
});

vi.mock("@paperclipai/adapter-utils/server-utils", async () => {
  const actual = await vi.importActual<typeof import("@paperclipai/adapter-utils/server-utils")>(
    "@paperclipai/adapter-utils/server-utils",
  );
  return { ...actual, ensureCommandResolvable, resolveCommandForLogs };
});

import { execute } from "./execute.js";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeEvaluatorResponse(met: boolean, reason: string, inputTokens = 40, outputTokens = 20): Response {
  return {
    ok: true,
    json: async () => ({
      content: [{ type: "text", text: JSON.stringify({ met, reason }) }],
      usage: { input_tokens: inputTokens, output_tokens: outputTokens },
    }),
  } as Response;
}

function makePaperclipApiResponse(): Response {
  return { ok: true, json: async () => ({}) } as Response;
}

const BASE_AGENT = {
  id: "agent-goal-1",
  companyId: "company-goal-1",
  name: "Coder Goal",
  adapterType: "claude_local",
  adapterConfig: {},
} as const;

const BASE_RUNTIME = {
  sessionId: null,
  sessionParams: null,
  sessionDisplayId: null,
  taskKey: null,
} as const;

const GOAL_CONFIG = {
  command: "claude",
  goalCondition: "The file /tmp/goal-smoke-test.txt exists and contains the word DONE",
  goalMaxTurns: 3,
  goalBudgetTokensEvaluator: 5000,
  goalEvaluatorModel: "claude-haiku-4-5-20251001",
} as const;

const GOAL_CONTEXT = {
  taskId: "issue-goal-smoke-1",
  paperclipWorkspace: {
    cwd: "", // set per-test
    source: "project_primary",
  },
} as const;

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("execute goal-mode", () => {
  const cleanupDirs: string[] = [];
  let fetchMock: ReturnType<typeof vi.fn>;
  let cwd: string;

  beforeEach(async () => {
    cwd = await mkdtemp(path.join(os.tmpdir(), "paperclip-goal-test-"));
    cleanupDirs.push(cwd);
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    delete process.env.PAPERCLIP_AGENT_PAUSED;
    process.env.ANTHROPIC_API_KEY = "test-anthropic-key";
    process.env.PAPERCLIP_API_URL = "http://127.0.0.1:19100";
  });

  afterEach(async () => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.PAPERCLIP_AGENT_PAUSED;
    while (cleanupDirs.length > 0) {
      const dir = cleanupDirs.pop();
      if (!dir) continue;
      await rm(dir, { recursive: true, force: true }).catch(() => undefined);
    }
  });

  it("isGoalMode=false — goal code never runs, standard path executes", async () => {
    // No goalCondition → isGoalMode=false
    fetchMock.mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-no-goal",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: { command: "claude" },
      context: { taskId: "issue-1", paperclipWorkspace: { cwd, source: "project_primary" } },
      onLog: async () => {},
    });

    // fetch should NOT have been called for an evaluator (no ANTHROPIC_API call)
    const evaluatorCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("anthropic.com"),
    );
    expect(evaluatorCalls).toHaveLength(0);
    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(1);
    expect(result.exitCode).toBe(0);
  });

  it("goal met on turn 1 — closing comment posted + PATCH done", async () => {
    fetchMock
      .mockResolvedValueOnce(makeEvaluatorResponse(true, "File exists with DONE", 40, 20))
      .mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-goal-met-t1",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: GOAL_CONFIG,
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-1",
      onLog: async () => {},
    });

    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(1);

    // Evaluator call: one fetch to anthropic
    const evaluatorCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("anthropic.com"),
    );
    expect(evaluatorCalls).toHaveLength(1);

    // Paperclip API calls: one comment POST, one PATCH
    const paperclipCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("127.0.0.1:19100"),
    );
    expect(paperclipCalls).toHaveLength(2);
    // first is the closing comment
    const commentCall = paperclipCalls.find(([url, opts]) => (url as string).includes("/comments") && (opts as RequestInit).method === "POST");
    expect(commentCall).toBeDefined();
    const commentBody = JSON.parse((commentCall![1] as RequestInit).body as string);
    expect(commentBody.body).toContain("Goal-Mode Result: MET");
    expect(commentBody.body).toContain("File exists with DONE");
    expect(commentBody.body).toContain("**Turns taken:** 1/3");
    // second is the PATCH
    const patchCall = paperclipCalls.find(([url, opts]) => (opts as RequestInit).method === "PATCH");
    expect(patchCall).toBeDefined();
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({ status: "done" });

    expect(result.exitCode).toBe(0);
    expect(result.errorMessage).toBeNull();
    expect((result.resultJson as Record<string, unknown>)?.goalMet).toBe(true);
    expect((result.resultJson as Record<string, unknown>)?.turns).toBe(1);
  });

  it("goal met on turn 2 — first eval false, second true", async () => {
    fetchMock
      .mockResolvedValueOnce(makeEvaluatorResponse(false, "Not done yet", 30, 15))
      .mockResolvedValueOnce(makeEvaluatorResponse(true, "Done now", 40, 20))
      .mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-goal-met-t2",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: GOAL_CONFIG,
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-2",
      onLog: async () => {},
    });

    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(2);

    const evaluatorCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("anthropic.com"),
    );
    expect(evaluatorCalls).toHaveLength(2);

    const patchCalls = fetchMock.mock.calls.filter(
      ([url, opts]) => typeof url === "string" && (url as string).includes("127.0.0.1:19100") && (opts as RequestInit).method === "PATCH",
    );
    expect(patchCalls).toHaveLength(1);
    expect(JSON.parse((patchCalls[0][1] as RequestInit).body as string)).toEqual({ status: "done" });

    expect((result.resultJson as Record<string, unknown>)?.goalMet).toBe(true);
    expect((result.resultJson as Record<string, unknown>)?.turns).toBe(2);
    // Evaluator tokens: (30+15) + (40+20) = 105
    expect((result.resultJson as Record<string, unknown>)?.evaluatorTokensUsed).toBe(105);
  });

  it("maxTurns=2 reached without met=true — blocked comment + PATCH blocked", async () => {
    fetchMock
      .mockResolvedValueOnce(makeEvaluatorResponse(false, "Still not done", 30, 10))
      .mockResolvedValueOnce(makeEvaluatorResponse(false, "Still no", 30, 10))
      .mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-goal-blocked",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: { ...GOAL_CONFIG, goalMaxTurns: 2 },
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-3",
      onLog: async () => {},
    });

    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(2);

    const paperclipCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("127.0.0.1:19100"),
    );
    // comment + PATCH
    expect(paperclipCalls).toHaveLength(2);
    const blockedCommentCall = paperclipCalls.find(([url, opts]) => (url as string).includes("/comments") && (opts as RequestInit).method === "POST");
    expect(blockedCommentCall).toBeDefined();
    const blockedBody = JSON.parse((blockedCommentCall![1] as RequestInit).body as string);
    expect(blockedBody.body).toContain("BLOCKED — maxTurns Reached");
    expect(blockedBody.body).toContain("Still no");

    const patchCall = paperclipCalls.find(([url, opts]) => (opts as RequestInit).method === "PATCH");
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({ status: "blocked" });

    expect(result.errorCode).toBe("goal_max_turns_exceeded");
    expect((result.resultJson as Record<string, unknown>)?.goalMet).toBe(false);
  });

  it("PAPERCLIP_AGENT_PAUSED=true before first turn — pause comment posted, loop exits", async () => {
    process.env.PAPERCLIP_AGENT_PAUSED = "true";
    fetchMock.mockResolvedValue(makePaperclipApiResponse());

    await execute({
      runId: "run-goal-paused",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: GOAL_CONFIG,
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-4",
      onLog: async () => {},
    });

    // No subprocess turns
    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(0);

    const pauseCommentCalls = fetchMock.mock.calls.filter(
      ([url, opts]) => typeof url === "string" && (url as string).includes("/comments") && (opts as RequestInit).method === "POST",
    );
    expect(pauseCommentCalls).toHaveLength(1);
    const pauseBody = JSON.parse((pauseCommentCalls[0][1] as RequestInit).body as string);
    expect(pauseBody.body).toContain("Goal-Mode: Paused");

    // No PATCH (not blocked because paused, not done)
    const patchCalls = fetchMock.mock.calls.filter(
      ([url, opts]) => typeof url === "string" && (url as string).includes("127.0.0.1:19100") && (opts as RequestInit).method === "PATCH",
    );
    expect(patchCalls).toHaveLength(0);
  });

  it("ANTHROPIC_API_KEY missing — falls back to standard mode, no crash", async () => {
    delete process.env.ANTHROPIC_API_KEY;
    fetchMock.mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-goal-no-key",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: GOAL_CONFIG,
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-5",
      onLog: async () => {},
    });

    // Falls back to standard mode: subprocess runs once
    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(1);
    // No evaluator calls
    const evaluatorCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && (url as string).includes("anthropic.com"),
    );
    expect(evaluatorCalls).toHaveLength(0);
    expect(result.exitCode).toBe(0);
  });

  it("evaluator parse error — met=false, loop continues safely until maxTurns", async () => {
    // Evaluator returns invalid JSON both turns
    const badResponse = {
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: "THIS IS NOT JSON" }],
        usage: { input_tokens: 10, output_tokens: 5 },
      }),
    } as Response;
    fetchMock
      .mockResolvedValueOnce(badResponse)
      .mockResolvedValueOnce(badResponse)
      .mockResolvedValue(makePaperclipApiResponse());

    const result = await execute({
      runId: "run-goal-parse-err",
      agent: BASE_AGENT,
      runtime: BASE_RUNTIME,
      config: { ...GOAL_CONFIG, goalMaxTurns: 2 },
      context: { ...GOAL_CONTEXT, paperclipWorkspace: { ...GOAL_CONTEXT.paperclipWorkspace, cwd } },
      authToken: "auth-token-6",
      onLog: async () => {},
    });

    // Both turns ran
    expect(runAdapterExecutionTargetProcess).toHaveBeenCalledTimes(2);
    // Result is blocked (maxTurns exhausted after parse errors)
    expect(result.errorCode).toBe("goal_max_turns_exceeded");
    expect((result.resultJson as Record<string, unknown>)?.goalMet).toBe(false);
  });
});
