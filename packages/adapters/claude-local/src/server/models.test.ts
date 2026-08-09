import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  listClaudeModels,
  refreshClaudeModels,
  resetClaudeModelsCacheForTests,
} from "./models.js";

describe("Claude model catalog", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubEnv("ANTHROPIC_API_KEY", "");
    vi.stubEnv("ANTHROPIC_BASE_URL", "");
    vi.stubEnv("CLAUDE_CODE_USE_BEDROCK", "");
    vi.stubEnv("ANTHROPIC_BEDROCK_BASE_URL", "");
    resetClaudeModelsCacheForTests();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    resetClaudeModelsCacheForTests();
  });

  it("keeps the canonical Sonnet 5 ID in fallback list and refresh results", async () => {
    await expect(listClaudeModels()).resolves.toContainEqual({
      id: "claude-sonnet-5",
      label: "Claude Sonnet 5",
    });
    await expect(refreshClaudeModels()).resolves.toContainEqual({
      id: "claude-sonnet-5",
      label: "Claude Sonnet 5",
    });
  });

  it("preserves the canonical Sonnet 5 ID alongside discovered list and refresh results", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", "test-api-key");
    const fetch = vi.fn(async () => new Response(JSON.stringify({
      data: [{ id: "claude-test-model", display_name: "Claude Test Model" }],
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    const expectedModels = expect.arrayContaining([
      { id: "claude-test-model", label: "Claude Test Model" },
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
    ]);
    await expect(listClaudeModels()).resolves.toEqual(expectedModels);
    await expect(refreshClaudeModels()).resolves.toEqual(expectedModels);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
