import { mkdir, mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { runChildProcess, ensureCommandResolvable, resolveCommandForLogs } = vi.hoisted(() => ({
  runChildProcess: vi.fn(),
  ensureCommandResolvable: vi.fn(async () => undefined),
  resolveCommandForLogs: vi.fn(async () => "claude"),
}));

vi.mock("@paperclipai/adapter-utils/server-utils", async () => {
  const actual = await vi.importActual<typeof import("@paperclipai/adapter-utils/server-utils")>(
    "@paperclipai/adapter-utils/server-utils",
  );
  return { ...actual, runChildProcess, ensureCommandResolvable, resolveCommandForLogs };
});

import { execute } from "./execute.js";

const workspaceDirs: string[] = [];
const apiKey = "paperclip-test-token";

function workerOutput(turn: number) {
  return [
    JSON.stringify({ type: "system", subtype: "init", session_id: `session-${turn}`, model: "claude-sonnet" }),
    JSON.stringify({ type: "assistant", session_id: `session-${turn}`, message: { content: [{ type: "text", text: `turn ${turn}` }] } }),
    JSON.stringify({ type: "result", session_id: `session-${turn}`, subtype: "success", result: `turn ${turn}`, usage: { input_tokens: 2, output_tokens: 3 } }),
  ].join("\n");
}

function evaluatorResponse(met: boolean, reason: string, input = 10, output = 5) {
  return new Response(JSON.stringify({
    content: [{ type: "text", text: JSON.stringify({ met, reason }) }],
    usage: { input_tokens: input, output_tokens: output },
  }), { status: 200 });
}

function makeContext(cwd: string) {
  return {
    issueId: "issue-1",
    paperclipWorkspace: { cwd },
  };
}

async function runGoal(config: Record<string, unknown>, fetchMock: typeof fetch) {
  const cwd = await mkdtemp(path.join(os.tmpdir(), "paperclip-claude-goal-"));
  workspaceDirs.push(cwd);
  vi.stubGlobal("fetch", fetchMock);
  process.env.ANTHROPIC_API_KEY = "anthropic-test-key";
  return execute({
    runId: "run-goal-1",
    agent: { id: "agent-1", companyId: "company-1", name: "Coder", adapterType: "claude_local", adapterConfig: config },
    runtime: { sessionId: null, sessionParams: null, sessionDisplayId: null, taskKey: null },
    config: { command: "claude", env: { PAPERCLIP_API_KEY: apiKey }, ...config },
    context: makeContext(cwd),
    authToken: apiKey,
    onLog: async () => {},
  });
}

describe("claude goal mode", () => {
  beforeEach(() => {
    runChildProcess.mockReset();
    runChildProcess.mockImplementation(async (_runId: string, _command: string, _args: string[], options: { env: Record<string, string> }) => ({
      exitCode: 0,
      signal: null,
      timedOut: false,
      stdout: workerOutput(runChildProcess.mock.calls.length),
      stderr: "",
      pid: 123,
      startedAt: new Date().toISOString(),
      goalEnv: options.env,
    }));
    vi.stubEnv("PAPERCLIP_AGENT_PAUSED", "false");
    vi.stubEnv("PAPERCLIP_API_URL", "http://paperclip.test");
  });

  afterEach(async () => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    while (workspaceDirs.length) await rm(workspaceDirs.pop()!, { recursive: true, force: true });
  });

  const baseConfig = { goalCondition: "file exists", goalMaxTurns: 2, goalBudgetTokensEvaluator: 500 };

  it("meets the goal on turn 1 and closes the issue", async () => {
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      requests.push(request);
      if (request.url.includes("anthropic.com")) return evaluatorResponse(true, "file exists");
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;

    const result = await runGoal(baseConfig, fetchMock);
    expect(runChildProcess).toHaveBeenCalledTimes(1);
    expect((runChildProcess.mock.calls[0][3] as { env: Record<string, string> }).env.PAPERCLIP_API_KEY).toBeUndefined();
    expect(result.resultJson).toMatchObject({ goalMode: true, goalMet: true, goalTurns: 1, evaluatorTokensUsed: 15 });
    expect(requests.filter((request) => request.url.startsWith("http://paperclip.test")).map((request) => `${request.method} ${request.url}`)).toEqual([
      "POST http://paperclip.test/api/issues/issue-1/comments",
      "PATCH http://paperclip.test/api/issues/issue-1",
    ]);
  });

  it("continues to turn 2 before closing", async () => {
    let evaluation = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      if (request.url.includes("anthropic.com")) {
        evaluation += 1;
        return evaluatorResponse(evaluation === 2, evaluation === 2 ? "done" : "not yet");
      }
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const result = await runGoal(baseConfig, fetchMock);
    expect(runChildProcess).toHaveBeenCalledTimes(2);
    expect(result.resultJson).toMatchObject({ goalMet: true, goalTurns: 2, evaluatorTokensUsed: 30 });
  });

  it("blocks after maxTurns without a met evaluation", async () => {
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      requests.push(request);
      if (request.url.includes("anthropic.com")) return evaluatorResponse(false, "not done");
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const result = await runGoal(baseConfig, fetchMock);
    expect(result.resultJson).toMatchObject({ goalMet: false, goalTurns: 2, evaluatorTokensUsed: 30 });
    const blockedComment = requests.find((request) => request.url.endsWith("/comments"));
    expect(blockedComment).toBeDefined();
    expect(await blockedComment!.clone().text()).toContain("BLOCKED");
    expect(requests.at(-1)?.method).toBe("PATCH");
    expect(await requests.at(-1)!.clone().text()).toContain('"blocked"');
  });

  it("posts a pause comment and does not start a turn when paused", async () => {
    vi.stubEnv("PAPERCLIP_AGENT_PAUSED", "true");
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      requests.push(request);
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const result = await runGoal(baseConfig, fetchMock);
    expect(runChildProcess).not.toHaveBeenCalled();
    expect(result.resultJson).toMatchObject({ goalMode: true, goalMet: false, goalTurns: 0 });
    expect(await requests[0].clone().text()).toContain("Paused");
  });

  it("treats evaluator parse errors as unmet and continues", async () => {
    let evaluation = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("anthropic.com")) {
        evaluation += 1;
        if (evaluation === 1) return new Response(JSON.stringify({ content: [{ type: "text", text: "not json" }], usage: { input_tokens: 1, output_tokens: 2 } }), { status: 200 });
        return evaluatorResponse(true, "recovered");
      }
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const result = await runGoal(baseConfig, fetchMock);
    expect(runChildProcess).toHaveBeenCalledTimes(2);
    expect(result.resultJson).toMatchObject({ goalMet: true, goalTurns: 2, evaluatorTokensUsed: 18 });
  });
});
