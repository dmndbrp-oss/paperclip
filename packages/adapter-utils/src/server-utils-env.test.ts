import { describe, expect, it } from "vitest";
import { sanitizeInheritedPaperclipEnv, scrubInheritedHostEnv } from "./server-utils.js";

describe("sanitizeInheritedPaperclipEnv", () => {
  it("drops the host-only Paperclip CLI command pointer", () => {
    expect(sanitizeInheritedPaperclipEnv({
      PAPERCLIPAI_CMD: "node /missing/paperclipai/dist/index.js",
      PAPERCLIP_RUNTIME_API_URL: "http://127.0.0.1:3100",
      PATH: "/usr/bin",
    })).toEqual({
      PAPERCLIP_RUNTIME_API_URL: "http://127.0.0.1:3100",
      PATH: "/usr/bin",
    });
  });
});

describe("scrubInheritedHostEnv", () => {
  it("removes inherited host secrets and reserved runtime state", () => {
    expect(scrubInheritedHostEnv({
      PATH: "/usr/bin",
      PAPERCLIP_RUNTIME_API_URL: "http://127.0.0.1:3100",
      PAPERCLIP_LISTEN_HOST: "127.0.0.1",
      PAPERCLIP_LISTEN_PORT: "3100",
      PAPERCLIP_SECRETS_MASTER_KEY_FILE: "host-only",
      PAPERCLIP_AGENT_JWT_SECRET: "host-only",
      BETTER_AUTH_SECRET: "host-only",
      VAPID_PRIVATE_KEY: "host-only",
      AZURE_GRAPH_CLIENT_SECRET: "host-only",
      MS365_MCP_CLIENT_SECRET: "host-only",
      PAPERCLIPAI_CMD: "host-only",
      PAPERCLIP_TASK_ID: "host-only",
    })).toEqual({
      PATH: "/usr/bin",
      PAPERCLIP_RUNTIME_API_URL: "http://127.0.0.1:3100",
      PAPERCLIP_LISTEN_HOST: "127.0.0.1",
      PAPERCLIP_LISTEN_PORT: "3100",
    });
  });
});
