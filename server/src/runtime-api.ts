import os from "node:os";

function normalizeHost(value: string | null | undefined): string {
  return (value ?? "").trim();
}

function isLoopbackHost(host: string): boolean {
  const normalized = normalizeHost(host).toLowerCase();
  return normalized === "127.0.0.1" || normalized === "localhost" || normalized === "::1";
}

function isWildcardHost(host: string): boolean {
  const normalized = normalizeHost(host).toLowerCase();
  return normalized === "0.0.0.0" || normalized === "::";
}

function isLinkLocalHost(host: string): boolean {
  const normalized = normalizeHost(host).toLowerCase();
  if (normalized.startsWith("169.254.")) return true;
  // IPv6 link-local block is fe80::/10 (fe80:: through febf::)
  if (/^fe[89ab][0-9a-f]:/.test(normalized)) return true;
  return false;
}

function formatOrigin(protocol: string, host: string, port: number): string {
  const normalizedHost = host.includes(":") && !host.startsWith("[") && !host.endsWith("]")
    ? `[${host}]`
    : host;
  return `${protocol}//${normalizedHost}:${port}`;
}

function pushCandidate(
  candidates: string[],
  seen: Set<string>,
  rawUrl: string | null | undefined,
): void {
  const trimmed = rawUrl?.trim();
  if (!trimmed) return;
  try {
    const normalized = new URL(trimmed).origin;
    if (seen.has(normalized)) return;
    seen.add(normalized);
    candidates.push(normalized);
  } catch {
    // Ignore malformed candidates.
  }
}

export function choosePrimaryRuntimeApiUrl(input: {
  authPublicBaseUrl?: string | null;
  allowedHostnames: string[];
  bindHost: string;
  port: number;
}): string {
  const explicitPublicBaseUrl = input.authPublicBaseUrl?.trim();
  if (explicitPublicBaseUrl) {
    try {
      return new URL(explicitPublicBaseUrl).origin;
    } catch {
      // Fall through to derived candidates if config parsing drifted.
    }
  }

  const bindHost = normalizeHost(input.bindHost);
  // When the server is bound to loopback, allowedHostnames are unreachable —
  // the server has no socket on those interfaces. Return the loopback URL so
  // co-located agents don't get an unreachable host injected.
  if (bindHost && isLoopbackHost(bindHost)) {
    return formatOrigin("http:", bindHost, input.port);
  }

  const allowedHostname = input.allowedHostnames
    .map((value) => value.trim())
    .find(Boolean);
  if (allowedHostname) {
    return formatOrigin("http:", allowedHostname, input.port);
  }

  if (bindHost && !isWildcardHost(bindHost)) {
    return formatOrigin("http:", bindHost, input.port);
  }

  return formatOrigin("http:", "localhost", input.port);
}

export function collectReachableInterfaceHosts(input: {
  networkInterfacesMap?: NodeJS.Dict<os.NetworkInterfaceInfo[]>;
} = {}): string[] {
  const interfaces = input.networkInterfacesMap ?? os.networkInterfaces();
  const rankedHosts: Array<{ host: string; rank: number; index: number }> = [];
  const seen = new Set<string>();
  let index = 0;

  for (const entries of Object.values(interfaces)) {
    for (const entry of entries ?? []) {
      if (entry.internal) continue;
      const host = normalizeHost(entry.address);
      if (!host || isLoopbackHost(host) || isWildcardHost(host) || isLinkLocalHost(host)) continue;
      if (seen.has(host)) continue;
      seen.add(host);
      rankedHosts.push({
        host,
        rank: entry.family === "IPv4" ? 0 : 1,
        index: index++,
      });
    }
  }

  return rankedHosts
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map((entry) => entry.host);
}

/**
 * Part B: Detects when the environment-configured PAPERCLIP_API_URL resolves to
 * a non-loopback host while the server is bound to loopback or wildcard. In that
 * case co-located agents would receive an unreachable URL. Returns a warning
 * message string when the mismatch is detected, or null when everything is consistent.
 *
 * Call this once at server startup after runtimeApiUrl is computed and log the
 * result. See also: choosePrimaryRuntimeApiUrl (Part A fix) and SAG-810.
 */
export function detectRuntimeApiUrlMismatch(input: {
  configuredApiUrl: string | null | undefined;
  runtimeApiUrl: string;
  bindHost: string;
}): string | null {
  const configuredUrl = input.configuredApiUrl?.trim();
  if (!configuredUrl) return null;
  const bindHost = normalizeHost(input.bindHost);
  // Only warn when the server is bound to loopback — on wildcard (0.0.0.0/::)
  // all interfaces are bound so external hostnames are genuinely reachable.
  if (!isLoopbackHost(bindHost)) return null;
  let configuredHostname: string;
  try {
    configuredHostname = new URL(configuredUrl).hostname;
  } catch {
    return null;
  }
  if (isLoopbackHost(configuredHostname)) return null;
  return (
    `PAPERCLIP_API_URL (${configuredUrl}) points to a non-loopback host but ` +
    `the server bindHost is "${bindHost || "unset"}". Co-located agents will be ` +
    `injected with a loopback URL (${input.runtimeApiUrl}) instead. ` +
    `Set authPublicBaseUrl in config if off-box callers need the external URL.`
  );
}

export function buildRuntimeApiCandidateUrls(input: {
  preferredApiUrl?: string | null;
  authPublicBaseUrl?: string | null;
  allowedHostnames: string[];
  bindHost: string;
  port: number;
  networkInterfacesMap?: NodeJS.Dict<os.NetworkInterfaceInfo[]>;
}): string[] {
  const candidates: string[] = [];
  const seen = new Set<string>();
  const explicitPublicBaseUrl = input.authPublicBaseUrl?.trim() ?? "";
  const explicitOrigin = (() => {
    if (!explicitPublicBaseUrl) return null;
    try {
      return new URL(explicitPublicBaseUrl).origin;
    } catch {
      return null;
    }
  })();
  const protocol = explicitOrigin ? new URL(explicitOrigin).protocol : "http:";

  pushCandidate(candidates, seen, input.preferredApiUrl);
  pushCandidate(candidates, seen, explicitOrigin);

  const bindHost = normalizeHost(input.bindHost);
  const bindIsLoopback = bindHost ? isLoopbackHost(bindHost) : false;

  // When the server is bound to loopback, push the loopback candidate first so
  // co-located callers get a reachable URL. Wildcard binds serve all interfaces
  // so allowedHostnames come first (existing behaviour preserved).
  if (bindIsLoopback) {
    pushCandidate(candidates, seen, formatOrigin(protocol, bindHost, input.port));
  }

  for (const rawHost of input.allowedHostnames) {
    const host = normalizeHost(rawHost);
    if (!host) continue;
    // Skip allowedHostnames that are not bound — on a loopback bind they are
    // unreachable, so only include them if they appear in actual interfaces.
    if (bindIsLoopback) {
      const reachable = collectReachableInterfaceHosts({ networkInterfacesMap: input.networkInterfacesMap });
      if (!reachable.includes(host)) continue;
    }
    pushCandidate(candidates, seen, formatOrigin(protocol, host, input.port));
  }

  if (!bindIsLoopback && bindHost && !isWildcardHost(bindHost)) {
    pushCandidate(candidates, seen, formatOrigin(protocol, bindHost, input.port));
  }

  if (explicitOrigin) {
    const hostname = new URL(explicitOrigin).hostname;
    if (isLoopbackHost(hostname)) {
      pushCandidate(candidates, seen, formatOrigin(protocol, "host.docker.internal", input.port));
    }
  }

  for (const host of collectReachableInterfaceHosts({ networkInterfacesMap: input.networkInterfacesMap })) {
    pushCandidate(candidates, seen, formatOrigin(protocol, host, input.port));
  }

  if (candidates.length === 0) {
    pushCandidate(
      candidates,
      seen,
      choosePrimaryRuntimeApiUrl({
        authPublicBaseUrl: input.authPublicBaseUrl,
        allowedHostnames: input.allowedHostnames,
        bindHost: input.bindHost,
        port: input.port,
      }),
    );
  }

  return candidates;
}
