import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseIgnoredHosts } from "../services/lad-watchdog.js";

// ---------------------------------------------------------------------------
// parseIgnoredHosts — pure config-parsing unit tests (SAG-3207)
// ---------------------------------------------------------------------------
describe("parseIgnoredHosts", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to {test-lad-host} when LAD_WATCHDOG_IGNORED_HOSTS is unset", () => {
    vi.stubEnv("LAD_WATCHDOG_IGNORED_HOSTS", undefined as unknown as string);
    const hosts = parseIgnoredHosts();
    expect(hosts.has("test-lad-host")).toBe(true);
    expect(hosts.size).toBe(1);
  });

  it("reads a custom comma-separated list from env", () => {
    vi.stubEnv("LAD_WATCHDOG_IGNORED_HOSTS", "host-a, host-b , host-c");
    const hosts = parseIgnoredHosts();
    expect(hosts.has("host-a")).toBe(true);
    expect(hosts.has("host-b")).toBe(true);
    expect(hosts.has("host-c")).toBe(true);
    expect(hosts.size).toBe(3);
  });

  it("returns an empty set when env is explicitly empty", () => {
    vi.stubEnv("LAD_WATCHDOG_IGNORED_HOSTS", "");
    const hosts = parseIgnoredHosts();
    expect(hosts.size).toBe(0);
  });

  it("a real production hostname is NOT in the default allowlist", () => {
    vi.stubEnv("LAD_WATCHDOG_IGNORED_HOSTS", undefined as unknown as string);
    const hosts = parseIgnoredHosts();
    expect(hosts.has("prod-lad-01")).toBe(false);
  });

  it("direct argument overrides env (testability)", () => {
    vi.stubEnv("LAD_WATCHDOG_IGNORED_HOSTS", "from-env");
    const hosts = parseIgnoredHosts("direct-arg");
    expect(hosts.has("direct-arg")).toBe(true);
    expect(hosts.has("from-env")).toBe(false);
  });
});
