import { describe, expect, it, vi, afterEach } from "vitest";
import { validateLiveRunFlip } from "../lib/live-run-flip-validator.js";

const BASE_INPUT = {
  apiUrl: "http://127.0.0.1:9999",
  apiKey: "test-key",
  agentId: "agent-local-1",
  companyId: "company-1",
  pollIntervalMs: 1,
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("validateLiveRunFlip", () => {
  it("returns ok:true when test issue reaches done within timeout", async () => {
    let pollCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string, opts: RequestInit) => {
        const method = opts.method ?? "GET";
        if (method === "POST") {
          return new Response(JSON.stringify({ id: "issue-test-1", status: "todo" }), { status: 201 });
        }
        pollCount += 1;
        const status = pollCount >= 2 ? "done" : "in_progress";
        return new Response(JSON.stringify({ id: "issue-test-1", status }), { status: 200 });
      }),
    );

    const result = await validateLiveRunFlip({ ...BASE_INPUT, timeoutMs: 5_000 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.issueId).toBe("issue-test-1");
      expect(result.durationMs).toBeGreaterThanOrEqual(0);
    }
  });

  it("returns ok:false when create fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "forbidden" }), { status: 403 })),
    );

    const result = await validateLiveRunFlip({ ...BASE_INPUT, timeoutMs: 1_000 });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toContain("Failed to create test issue");
      expect(result.issueId).toBeNull();
    }
  });

  it("returns ok:false when issue reaches blocked status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (_url: string, opts: RequestInit) => {
        const method = opts.method ?? "GET";
        if (method === "POST") {
          return new Response(JSON.stringify({ id: "issue-test-2", status: "todo" }), { status: 201 });
        }
        return new Response(JSON.stringify({ id: "issue-test-2", status: "blocked" }), { status: 200 });
      }),
    );

    const result = await validateLiveRunFlip({ ...BASE_INPUT, timeoutMs: 5_000 });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toContain("blocked");
    }
  });

  it("returns ok:false when timeout fires before done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (_url: string, opts: RequestInit) => {
        const method = opts.method ?? "GET";
        if (method === "POST") {
          return new Response(JSON.stringify({ id: "issue-test-3", status: "todo" }), { status: 201 });
        }
        return new Response(JSON.stringify({ id: "issue-test-3", status: "in_progress" }), { status: 200 });
      }),
    );

    // Tiny timeout — will expire before in_progress→done.
    const result = await validateLiveRunFlip({ ...BASE_INPUT, timeoutMs: 5, pollIntervalMs: 50 });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toContain("did not reach 'done'");
    }
  });
});
