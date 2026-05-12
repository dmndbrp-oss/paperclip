import { afterEach, describe, expect, it } from "vitest";
import { buildPaperclipEnv } from "../adapters/utils.js";
import { sanitizeInheritedPaperclipEnv } from "@paperclipai/adapter-utils/server-utils";

const ORIGINAL_PAPERCLIP_RUNTIME_API_URL = process.env.PAPERCLIP_RUNTIME_API_URL;
const ORIGINAL_PAPERCLIP_API_URL = process.env.PAPERCLIP_API_URL;
const ORIGINAL_PAPERCLIP_LISTEN_HOST = process.env.PAPERCLIP_LISTEN_HOST;
const ORIGINAL_PAPERCLIP_LISTEN_PORT = process.env.PAPERCLIP_LISTEN_PORT;
const ORIGINAL_HOST = process.env.HOST;
const ORIGINAL_PORT = process.env.PORT;
const ORIGINAL_PAPERCLIP_API_TOKEN = process.env.PAPERCLIP_API_TOKEN;

afterEach(() => {
  if (ORIGINAL_PAPERCLIP_RUNTIME_API_URL === undefined) delete process.env.PAPERCLIP_RUNTIME_API_URL;
  else process.env.PAPERCLIP_RUNTIME_API_URL = ORIGINAL_PAPERCLIP_RUNTIME_API_URL;

  if (ORIGINAL_PAPERCLIP_API_URL === undefined) delete process.env.PAPERCLIP_API_URL;
  else process.env.PAPERCLIP_API_URL = ORIGINAL_PAPERCLIP_API_URL;

  if (ORIGINAL_PAPERCLIP_LISTEN_HOST === undefined) delete process.env.PAPERCLIP_LISTEN_HOST;
  else process.env.PAPERCLIP_LISTEN_HOST = ORIGINAL_PAPERCLIP_LISTEN_HOST;

  if (ORIGINAL_PAPERCLIP_LISTEN_PORT === undefined) delete process.env.PAPERCLIP_LISTEN_PORT;
  else process.env.PAPERCLIP_LISTEN_PORT = ORIGINAL_PAPERCLIP_LISTEN_PORT;

  if (ORIGINAL_HOST === undefined) delete process.env.HOST;
  else process.env.HOST = ORIGINAL_HOST;

  if (ORIGINAL_PORT === undefined) delete process.env.PORT;
  else process.env.PORT = ORIGINAL_PORT;

  if (ORIGINAL_PAPERCLIP_API_TOKEN === undefined) delete process.env.PAPERCLIP_API_TOKEN;
  else process.env.PAPERCLIP_API_TOKEN = ORIGINAL_PAPERCLIP_API_TOKEN;
});

describe("buildPaperclipEnv", () => {
  it("prefers an explicit PAPERCLIP_RUNTIME_API_URL", () => {
    process.env.PAPERCLIP_RUNTIME_API_URL = "http://203.0.113.42:3102";
    process.env.PAPERCLIP_API_URL = "http://localhost:4100";
    process.env.PAPERCLIP_LISTEN_HOST = "127.0.0.1";
    process.env.PAPERCLIP_LISTEN_PORT = "3101";

    const env = buildPaperclipEnv({ id: "agent-1", companyId: "company-1" });

    expect(env.PAPERCLIP_API_URL).toBe("http://203.0.113.42:3102");
  });

  it("falls back to PAPERCLIP_API_URL when no runtime URL is configured", () => {
    delete process.env.PAPERCLIP_RUNTIME_API_URL;
    process.env.PAPERCLIP_API_URL = "http://localhost:4100";
    process.env.PAPERCLIP_LISTEN_HOST = "127.0.0.1";
    process.env.PAPERCLIP_LISTEN_PORT = "3101";

    const env = buildPaperclipEnv({ id: "agent-1", companyId: "company-1" });

    expect(env.PAPERCLIP_API_URL).toBe("http://localhost:4100");
  });

  it("uses runtime listen host/port when explicit URL is not set", () => {
    delete process.env.PAPERCLIP_RUNTIME_API_URL;
    delete process.env.PAPERCLIP_API_URL;
    process.env.PAPERCLIP_LISTEN_HOST = "0.0.0.0";
    process.env.PAPERCLIP_LISTEN_PORT = "3101";
    process.env.PORT = "3100";

    const env = buildPaperclipEnv({ id: "agent-1", companyId: "company-1" });

    expect(env.PAPERCLIP_API_URL).toBe("http://localhost:3101");
  });

  it("formats IPv6 hosts safely in fallback URL generation", () => {
    delete process.env.PAPERCLIP_RUNTIME_API_URL;
    delete process.env.PAPERCLIP_API_URL;
    process.env.PAPERCLIP_LISTEN_HOST = "::1";
    process.env.PAPERCLIP_LISTEN_PORT = "3101";

    const env = buildPaperclipEnv({ id: "agent-1", companyId: "company-1" });

    expect(env.PAPERCLIP_API_URL).toBe("http://[::1]:3101");
  });

  it("never includes PAPERCLIP_API_TOKEN even when set in process.env", () => {
    process.env.PAPERCLIP_API_TOKEN = "board-master-secret";

    const env = buildPaperclipEnv({ id: "agent-1", companyId: "company-1" });

    expect("PAPERCLIP_API_TOKEN" in env).toBe(false);
  });
});

describe("sanitizeInheritedPaperclipEnv", () => {
  it("strips PAPERCLIP_API_TOKEN from the inherited env", () => {
    const result = sanitizeInheritedPaperclipEnv({
      PAPERCLIP_API_TOKEN: "board-master-secret",
      PAPERCLIP_AGENT_ID: "agent-1",
      HOME: "/home/user",
    });

    expect("PAPERCLIP_API_TOKEN" in result).toBe(false);
  });

  it("preserves PAPERCLIP_RUNTIME_API_URL, PAPERCLIP_LISTEN_HOST, and PAPERCLIP_LISTEN_PORT", () => {
    const result = sanitizeInheritedPaperclipEnv({
      PAPERCLIP_API_TOKEN: "board-master-secret",
      PAPERCLIP_RUNTIME_API_URL: "http://localhost:3102",
      PAPERCLIP_LISTEN_HOST: "127.0.0.1",
      PAPERCLIP_LISTEN_PORT: "3101",
      PAPERCLIP_AGENT_ID: "agent-1",
    });

    expect("PAPERCLIP_API_TOKEN" in result).toBe(false);
    expect("PAPERCLIP_AGENT_ID" in result).toBe(false);
    expect(result.PAPERCLIP_RUNTIME_API_URL).toBe("http://localhost:3102");
    expect(result.PAPERCLIP_LISTEN_HOST).toBe("127.0.0.1");
    expect(result.PAPERCLIP_LISTEN_PORT).toBe("3101");
  });
});
