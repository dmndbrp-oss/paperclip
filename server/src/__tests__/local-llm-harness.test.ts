import { describe, expect, it, vi } from "vitest";
import {
  harnessCall,
  detectLeakedToolCallContent,
  type HarnessEscalationEvent,
  type HarnessCallLog,
} from "../lib/local-llm-harness.js";

const SIMPLE_SCHEMA = {
  type: "object",
  properties: {
    status: { type: "string" },
  },
  required: ["status"],
};

// ---------------------------------------------------------------------------
// AC: schema-pass
// ---------------------------------------------------------------------------
describe("harnessCall — schema-pass", () => {
  it("returns ok:true on first attempt when output matches schema", async () => {
    const logs: HarnessCallLog[] = [];
    const callFn = vi.fn().mockResolvedValue(JSON.stringify({ status: "ok" }));

    const result = await harnessCall(
      SIMPLE_SCHEMA,
      "Return JSON with a status field.",
      callFn,
      {
        agentId: "agent-1",
        jobClass: "echo",
        model: "llama3.1:8b",
        onLog: (e) => { logs.push(e); },
      },
    );

    expect(result).toEqual({ ok: true, value: { status: "ok" }, attempts: 1 });
    expect(callFn).toHaveBeenCalledOnce();
    expect(logs).toHaveLength(1);
    expect(logs[0].success).toBe(true);
    expect(logs[0].attempts).toBe(1);
    expect(logs[0].escalation_reason).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC: schema-fail-then-retry-pass
// ---------------------------------------------------------------------------
describe("harnessCall — schema-fail-then-retry-pass", () => {
  it("retries once when first attempt fails and returns ok:true on retry", async () => {
    const logs: HarnessCallLog[] = [];
    const callFn = vi
      .fn()
      .mockResolvedValueOnce(JSON.stringify({ wrong_field: 42 }))
      .mockResolvedValueOnce(JSON.stringify({ status: "fixed" }));

    const result = await harnessCall(
      SIMPLE_SCHEMA,
      "Return JSON with a status field.",
      callFn,
      {
        agentId: "agent-1",
        jobClass: "structured-task",
        model: "qwen2.5-coder:32b",
        onLog: (e) => { logs.push(e); },
      },
    );

    expect(result).toEqual({ ok: true, value: { status: "fixed" }, attempts: 2 });
    expect(callFn).toHaveBeenCalledTimes(2);

    // Retry prompt must embed the validation error from attempt 1.
    const retryPrompt = callFn.mock.calls[1][0] as string;
    expect(retryPrompt).toContain("Validation error from previous attempt");
    expect(retryPrompt).toContain("Schema validation failed");

    expect(logs).toHaveLength(1);
    expect(logs[0].success).toBe(true);
    expect(logs[0].attempts).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// AC: schema-fail-twice-then-escalate
// ---------------------------------------------------------------------------
describe("harnessCall — schema-fail-twice-then-escalate", () => {
  it("emits escalation event and returns ok:false when both attempts fail", async () => {
    const escalations: HarnessEscalationEvent[] = [];
    const logs: HarnessCallLog[] = [];
    const callFn = vi
      .fn()
      .mockResolvedValueOnce("not json at all")
      .mockResolvedValueOnce(JSON.stringify({ still_wrong: true }));

    const result = await harnessCall(
      SIMPLE_SCHEMA,
      "Return JSON.",
      callFn,
      {
        agentId: "agent-2",
        jobClass: "analysis",
        model: "gemma4:31b",
        onEscalate: (e) => { escalations.push(e); },
        onLog: (e) => { logs.push(e); },
      },
    );

    expect(result.ok).toBe(false);
    expect(result.attempts).toBe(2);

    // Escalation event must have the required fields (SAG-1172 §3).
    expect(escalations).toHaveLength(1);
    const evt = escalations[0];
    expect(evt.agentId).toBe("agent-2");
    expect(evt.jobClass).toBe("analysis");
    expect(evt.model).toBe("gemma4:31b");
    expect(evt.attempts).toBe(2);
    expect(typeof evt.escalationReason).toBe("string");
    expect(evt.escalationReason.length).toBeGreaterThan(0);
    expect(typeof evt.timestamp).toBe("string");

    // Per-call log: failure path.
    expect(logs).toHaveLength(1);
    expect(logs[0].success).toBe(false);
    expect(logs[0].attempts).toBe(2);
    expect(typeof logs[0].escalation_reason).toBe("string");

    // Maximum one retry — callFn called exactly twice.
    expect(callFn).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// AC: JSON-leak detection (SAG-1172 §5 / SAG-722 repro)
// ---------------------------------------------------------------------------
describe("harnessCall — JSON-leak detection", () => {
  it("surfaces leaked tool-call JSON object as a validation error, not silent output", async () => {
    const escalations: HarnessEscalationEvent[] = [];
    // Leaked content: a tool-call JSON object mid-text — SAG-722 repro pattern.
    const leaked = `Here is my analysis.\n{"type":"function","name":"post_comment","parameters":{"body":"done"}}`;
    const callFn = vi.fn().mockResolvedValue(leaked);

    const result = await harnessCall(
      SIMPLE_SCHEMA,
      "Return JSON.",
      callFn,
      {
        agentId: "agent-3",
        jobClass: "review",
        model: "opencode_local",
        onEscalate: (e) => { escalations.push(e); },
      },
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("leaked tool-call JSON");
    }
  });

  it("surfaces <function_calls> XML fragment as a validation error", async () => {
    const leaked = `Some text <function_calls><invoke name="bash"><cmd>ls</cmd></invoke></function_calls>`;
    const callFn = vi.fn().mockResolvedValue(leaked);

    const result = await harnessCall(
      SIMPLE_SCHEMA,
      "Return JSON.",
      callFn,
      { agentId: "a", jobClass: "b", model: "c" },
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("leaked <function_calls>");
    }
  });
});

// ---------------------------------------------------------------------------
// detectLeakedToolCallContent — standalone utility
// ---------------------------------------------------------------------------
describe("detectLeakedToolCallContent", () => {
  it("returns null for clean natural-language output", () => {
    expect(detectLeakedToolCallContent("Moved issue to in_progress.")).toBeNull();
  });

  it("detects inline tool-call JSON object", () => {
    const raw = `Proceeding.\n{"name":"bash_tool","input":{"cmd":"ls"}}`;
    expect(detectLeakedToolCallContent(raw)).not.toBeNull();
  });

  it("detects <function_calls> XML", () => {
    expect(
      detectLeakedToolCallContent(`<function_calls><invoke name="read"/></function_calls>`),
    ).not.toBeNull();
  });

  it("detects <analysis> reasoning block", () => {
    expect(
      detectLeakedToolCallContent(`<analysis>internal reasoning here</analysis>`),
    ).not.toBeNull();
  });
});
