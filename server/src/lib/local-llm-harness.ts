import Ajv, { type ValidateFunction } from "ajv";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const AjvCtor = (Ajv as any).default ?? Ajv;

// ---------------------------------------------------------------------------
// local_continuous adapterConfig — canonical TypeScript type (SAG-1172 §6).
// This is the single source of truth for field semantics; the runtime
// registry's getConfigSchema() is derived from these definitions.
//
// Required fields for local_continuous adapterConfig:
// ---------------------------------------------------------------------------
export interface LocalContinuousAdapterConfig {
  /** Ollama model tag, e.g. "qwen2.5-coder:32b" */
  modelTag: string;
  /** Estimated VRAM/RAM required to run the model, in GB */
  memoryEstimateGB: number;
  /** ID of the Local Adapter Daemon host to run on */
  ladHostId: string;
  /** How long to wait before asking manager for work when idle (ms). Default 300000. */
  idleAskBossMs?: number;
  /** Paperclip API poll interval (ms). Default 1000. */
  pollIntervalMs?: number;
  /** Consecutive errors before daemon pauses the agent. Default 3. */
  consecutiveFailureThreshold?: number;
  /** Whether the daemon is pinned to this host. Default false. */
  pinned?: boolean;
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Opaque JSON Schema object. */
export type HarnessSchema = Record<string, unknown>;

export type HarnessCallResult<T = unknown> =
  | { ok: true; value: T; attempts: 1 | 2 }
  | { ok: false; error: string; attempts: 2; escalationEvent: HarnessEscalationEvent };

/** Emitted when both the initial attempt and one retry both fail validation. */
export interface HarnessEscalationEvent {
  agentId: string;
  jobClass: string;
  model: string;
  attempts: 2;
  escalationReason: string;
  timestamp: string;
}

/** Persisted for every harness call regardless of outcome. */
export interface HarnessCallLog {
  agent_id: string;
  job_class: string;
  model: string;
  latency_ms: number;
  attempts: 1 | 2;
  success: boolean;
  escalation_reason: string | null;
}

export interface HarnessContext {
  agentId: string;
  jobClass: string;
  model: string;
  onEscalate?: (event: HarnessEscalationEvent) => void | Promise<void>;
  onLog?: (entry: HarnessCallLog) => void | Promise<void>;
}

// ---------------------------------------------------------------------------
// JSON-leak detection (SAG-1172 §5 — covers SAG-722 / SAG-749 residual class)
// ---------------------------------------------------------------------------

const TOOL_CALL_JSON_RE = /\{\s*"(?:name|type)"\s*:\s*"[^"]+"/;
const FUNCTION_CALLS_XML_RE = /<function_calls>[\s\S]*?<\/function_calls>/;
const ANALYSIS_XML_RE = /<(?:analysis|thinking|antml:thinking)>[\s\S]*?<\/(?:analysis|thinking|antml:thinking)>/;

function detectJsonLeak(rawOutput: string): string | null {
  if (TOOL_CALL_JSON_RE.test(rawOutput)) {
    return "Output contains leaked tool-call JSON object";
  }
  if (FUNCTION_CALLS_XML_RE.test(rawOutput)) {
    return "Output contains leaked <function_calls> XML fragment";
  }
  if (ANALYSIS_XML_RE.test(rawOutput)) {
    return "Output contains leaked internal reasoning XML block";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Schema validation
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ajv = new AjvCtor({ allErrors: true, strict: false }) as unknown as { compile: (schema: unknown) => ValidateFunction };

function buildValidator(schema: HarnessSchema): ValidateFunction {
  return ajv.compile(schema);
}

function validateOutput<T>(
  rawOutput: string,
  validate: ValidateFunction,
): { value: T } | { error: string } {
  const leakError = detectJsonLeak(rawOutput);
  if (leakError) {
    return { error: leakError };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawOutput.trim());
  } catch {
    return { error: `Output is not valid JSON: ${rawOutput.slice(0, 200)}` };
  }

  const valid = validate(parsed);
  if (!valid) {
    const messages = (validate.errors ?? [])
      .map((e) => `${e.instancePath || "/"} ${e.message ?? "unknown error"}`)
      .join("; ");
    return { error: `Schema validation failed: ${messages}` };
  }

  return { value: parsed as T };
}

// ---------------------------------------------------------------------------
// Core harness call (SAG-1172 §1–§4)
// ---------------------------------------------------------------------------

/**
 * Wraps a single tool-shaped local-LLM call with schema validation, one retry,
 * and structured escalation on double failure.
 *
 * - On first validation failure the error message is appended to the original
 *   prompt and the call is retried once.
 * - If the retry also fails, an escalation event is emitted and the result
 *   carries `ok: false`.  The orchestrator must mark the in-flight job blocked.
 *
 * For opencode_local callers: pass the JSON-formatted final summary as `prompt`
 * and supply a noop callFn that returns a pre-captured string; the harness's
 * JSON-leak check then surfaces leaked content as a validation error.
 *
 * For local_continuous daemon callers: pass the structured call payload and a
 * callFn that dispatches to the Ollama endpoint.
 */
export async function harnessCall<T = unknown>(
  schema: HarnessSchema,
  prompt: string,
  callFn: (prompt: string) => Promise<string>,
  ctx: HarnessContext,
): Promise<HarnessCallResult<T>> {
  const validate = buildValidator(schema);
  const startMs = Date.now();
  let attempts = 0 as 1 | 2;

  const attempt = async (p: string): Promise<{ value: T } | { error: string }> => {
    const raw = await callFn(p);
    return validateOutput<T>(raw, validate);
  };

  const firstResult = await attempt(prompt);
  attempts = 1;

  if ("value" in firstResult) {
    const latency_ms = Date.now() - startMs;
    await ctx.onLog?.({ agent_id: ctx.agentId, job_class: ctx.jobClass, model: ctx.model, latency_ms, attempts, success: true, escalation_reason: null });
    return { ok: true, value: firstResult.value, attempts };
  }

  // Retry: append validation error to original prompt.
  const retryPrompt = `${prompt}\n\nValidation error from previous attempt: ${firstResult.error}\nPlease correct the output and respond with valid JSON matching the required schema.`;
  const retryResult = await attempt(retryPrompt);
  attempts = 2;
  const latency_ms = Date.now() - startMs;

  if ("value" in retryResult) {
    await ctx.onLog?.({ agent_id: ctx.agentId, job_class: ctx.jobClass, model: ctx.model, latency_ms, attempts, success: true, escalation_reason: null });
    return { ok: true, value: retryResult.value, attempts };
  }

  const escalationReason = retryResult.error;
  const escalationEvent: HarnessEscalationEvent = {
    agentId: ctx.agentId,
    jobClass: ctx.jobClass,
    model: ctx.model,
    attempts: 2,
    escalationReason,
    timestamp: new Date().toISOString(),
  };

  await ctx.onLog?.({ agent_id: ctx.agentId, job_class: ctx.jobClass, model: ctx.model, latency_ms, attempts, success: false, escalation_reason: escalationReason });
  await ctx.onEscalate?.(escalationEvent);

  return { ok: false, error: escalationReason, attempts, escalationEvent };
}

/**
 * Validates raw LLM output text against the JSON-leak patterns without full
 * schema validation.  Used by opencode_local and local_continuous adapter
 * layers to surface leaked tool-call content as a clean validation error
 * rather than silently ignoring it or letting it pollute issue comments.
 */
export function detectLeakedToolCallContent(rawOutput: string): string | null {
  return detectJsonLeak(rawOutput);
}
