// SAG-1172 §8: Live-run flip validation helper.
//
// After flipping an agent to local_continuous, call validateLiveRunFlip() to
// confirm the freshly-flipped agent can execute a trivial task end-to-end.
// A T+0 API state read-back (adapterType echoing correctly) is insufficient —
// the daemon must actually pick up and complete a test issue.

export interface LiveRunFlipInput {
  apiUrl: string;
  apiKey: string;
  agentId: string;
  companyId: string;
  /** Maximum wait time in ms. Must be ≤ 600_000 (10 min). Default 600_000. */
  timeoutMs?: number;
  /** Poll interval in ms. Default 10_000. */
  pollIntervalMs?: number;
}

export type LiveRunFlipResult =
  | { ok: true; issueId: string; durationMs: number }
  | { ok: false; reason: string; issueId: string | null; durationMs: number };

const DEFAULT_TIMEOUT_MS = 600_000;
const MAX_TIMEOUT_MS = 600_000;
const DEFAULT_POLL_INTERVAL_MS = 10_000;

async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(
  url: string,
  options: RequestInit,
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const res = await fetch(url, options);
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { ok: res.ok, status: res.status, body };
}

/**
 * Dispatches a trivial echo test issue to the target agent, polls until it
 * reaches `done` status (or the timeout fires), and returns a structured
 * result.  The test issue is created with a title that makes its purpose
 * unambiguous in the issue list.
 *
 * Callers must mark the in-flight deployment blocked if this returns ok:false.
 */
export async function validateLiveRunFlip(
  input: LiveRunFlipInput,
): Promise<LiveRunFlipResult> {
  const timeoutMs = Math.min(
    input.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
  );
  const pollIntervalMs = input.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${input.apiKey}`,
  };
  const startMs = Date.now();

  // Create the test issue.
  const createRes = await fetchJson(
    `${input.apiUrl}/api/companies/${input.companyId}/issues`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        title: "[SAG-1172 live-run flip test] echo test — respond with OK",
        description:
          "This is an automated live-run flip validation issue created by the local-LLM harness (SAG-1172 §8). Respond with a single comment containing the word OK and mark the issue done.",
        assigneeAgentId: input.agentId,
        status: "todo",
        priority: "low",
      }),
    },
  );

  if (!createRes.ok) {
    return {
      ok: false,
      reason: `Failed to create test issue: HTTP ${createRes.status}`,
      issueId: null,
      durationMs: Date.now() - startMs,
    };
  }

  const issueBody = createRes.body as Record<string, unknown>;
  const issueId = typeof issueBody.id === "string" ? issueBody.id : null;
  if (!issueId) {
    return {
      ok: false,
      reason: "Test issue created but response did not include an id",
      issueId: null,
      durationMs: Date.now() - startMs,
    };
  }

  // Poll until done or timeout.
  while (Date.now() - startMs < timeoutMs) {
    await sleep(pollIntervalMs);

    const pollRes = await fetchJson(`${input.apiUrl}/api/issues/${issueId}`, {
      method: "GET",
      headers,
    });

    if (!pollRes.ok) {
      continue;
    }

    const pollBody = pollRes.body as Record<string, unknown>;
    if (pollBody.status === "done") {
      return {
        ok: true,
        issueId,
        durationMs: Date.now() - startMs,
      };
    }

    if (pollBody.status === "cancelled" || pollBody.status === "blocked") {
      return {
        ok: false,
        reason: `Test issue reached terminal status '${String(pollBody.status)}' instead of 'done'`,
        issueId,
        durationMs: Date.now() - startMs,
      };
    }
  }

  return {
    ok: false,
    reason: `Test issue did not reach 'done' within ${timeoutMs / 1000}s`,
    issueId,
    durationMs: Date.now() - startMs,
  };
}
