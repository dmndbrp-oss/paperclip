import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { asBoolean } from "@paperclipai/adapter-utils/server-utils";

type PreparedOpenCodeRuntimeConfig = {
  env: Record<string, string>;
  notes: string[];
  cleanup: () => Promise<void>;
};

type McpStdioEntry = {
  command: string;
  args?: string[];
  env?: Record<string, string>;
};

type McpHttpEntry = {
  url: string;
  headers?: Record<string, string>;
};

type McpEntry = McpStdioEntry | McpHttpEntry;

function resolveXdgConfigHome(env: Record<string, string>): string {
  return (
    (typeof env.XDG_CONFIG_HOME === "string" && env.XDG_CONFIG_HOME.trim()) ||
    (typeof process.env.XDG_CONFIG_HOME === "string" && process.env.XDG_CONFIG_HOME.trim()) ||
    path.join(os.homedir(), ".config")
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readJsonObject(filepath: string): Promise<Record<string, unknown>> {
  try {
    const raw = await fs.readFile(filepath, "utf8");
    const parsed = JSON.parse(raw);
    return isPlainObject(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function isHttpEntry(entry: McpEntry): entry is McpHttpEntry {
  return "url" in entry && typeof (entry as McpHttpEntry).url === "string";
}

function parseMcpServers(value: unknown): Record<string, McpEntry> | null {
  if (!isPlainObject(value)) return null;
  const result: Record<string, McpEntry> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (!isPlainObject(entry)) continue;
    if (typeof (entry as { url?: unknown }).url === "string") {
      const httpEntry: McpHttpEntry = { url: (entry as { url: string }).url };
      if (isPlainObject((entry as { headers?: unknown }).headers)) {
        httpEntry.headers = (entry as { headers: Record<string, string> }).headers;
      }
      result[key] = httpEntry;
    } else if (typeof (entry as { command?: unknown }).command === "string") {
      const stdioEntry: McpStdioEntry = { command: (entry as { command: string }).command };
      const rawArgs = (entry as { args?: unknown }).args;
      if (Array.isArray(rawArgs) && rawArgs.every((a) => typeof a === "string")) {
        stdioEntry.args = rawArgs as string[];
      }
      if (isPlainObject((entry as { env?: unknown }).env)) {
        stdioEntry.env = (entry as { env: Record<string, string> }).env;
      }
      result[key] = stdioEntry;
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}

function toOpenCodeMcpEntry(entry: McpEntry): Record<string, unknown> {
  if (isHttpEntry(entry)) {
    return {
      type: "remote",
      url: entry.url,
      ...(entry.headers ? { headers: entry.headers } : {}),
    };
  }
  return {
    type: "local",
    command: [entry.command, ...(entry.args ?? [])],
    ...(entry.env ? { environment: entry.env } : {}),
  };
}

export async function prepareOpenCodeRuntimeConfig(input: {
  env: Record<string, string>;
  config: Record<string, unknown>;
  targetIsRemote?: boolean;
}): Promise<PreparedOpenCodeRuntimeConfig> {
  const skipPermissions = asBoolean(input.config.dangerouslySkipPermissions, true);
  const mcpServers = parseMcpServers(input.config.mcpServers);
  const needsRuntimeConfig = skipPermissions || mcpServers !== null;

  if (!needsRuntimeConfig) {
    return {
      env: input.env,
      notes: [],
      cleanup: async () => {},
    };
  }

  // For remote execution targets the host XDG_CONFIG_HOME path is meaningless
  // (and actively harmful — it leaks a macOS-only path into the remote Linux
  // env). Callers that need to ship a runtime opencode config to the remote
  // box do that via prepareAdapterExecutionTargetRuntime in execute.ts; this
  // host-fs helper is local-only.
  if (input.targetIsRemote) {
    return {
      env: input.env,
      notes: [],
      cleanup: async () => {},
    };
  }

  if (!skipPermissions && mcpServers !== null) {
    console.warn(
      "[opencode-local] mcpServers is set but dangerouslySkipPermissions is false — injecting MCP config without permission override",
    );
  }

  const sourceConfigDir = path.join(resolveXdgConfigHome(input.env), "opencode");
  const runtimeConfigHome = await fs.mkdtemp(path.join(os.tmpdir(), "paperclip-opencode-config-"));
  const runtimeConfigDir = path.join(runtimeConfigHome, "opencode");
  const runtimeConfigPath = path.join(runtimeConfigDir, "opencode.json");

  await fs.mkdir(runtimeConfigDir, { recursive: true });
  try {
    await fs.cp(sourceConfigDir, runtimeConfigDir, {
      recursive: true,
      force: true,
      errorOnExist: false,
      dereference: false,
    });
  } catch (err) {
    if ((err as NodeJS.ErrnoException | null)?.code !== "ENOENT") {
      throw err;
    }
  }

  const existingConfig = await readJsonObject(runtimeConfigPath);
  const nextConfig: Record<string, unknown> = { ...existingConfig };

  if (skipPermissions) {
    const existingPermission = isPlainObject(existingConfig.permission) ? existingConfig.permission : {};
    nextConfig.permission = {
      ...existingPermission,
      external_directory: "allow",
    };
  }

  if (mcpServers !== null) {
    const existingMcp = isPlainObject(existingConfig.mcp) ? existingConfig.mcp : {};
    const newMcpEntries = Object.fromEntries(
      Object.entries(mcpServers).map(([k, v]) => [k, toOpenCodeMcpEntry(v)]),
    );
    nextConfig.mcp = { ...existingMcp, ...newMcpEntries };
  }

  await fs.writeFile(runtimeConfigPath, `${JSON.stringify(nextConfig, null, 2)}\n`, "utf8");

  const notes: string[] = [];
  if (skipPermissions) {
    notes.push(
      "Injected runtime OpenCode config with permission.external_directory=allow to avoid headless approval prompts.",
    );
  }
  if (mcpServers !== null) {
    notes.push(
      `Injected ${Object.keys(mcpServers).length} MCP server(s) into runtime OpenCode config.`,
    );
  }

  return {
    env: {
      ...input.env,
      XDG_CONFIG_HOME: runtimeConfigHome,
    },
    notes,
    cleanup: async () => {
      await fs.rm(runtimeConfigHome, { recursive: true, force: true });
    },
  };
}
