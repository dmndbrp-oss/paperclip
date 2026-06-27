# Inference Hygiene Audit — 21 Local Gemma4 Agents

**Issue:** SAG-4192 (child of SAG-4190)  
**Audited:** 2026-06-16  
**Model scope:** `gemma4:26b-a4b-it-q4_K_M` (shared Modelfile tag)

---

## 1. Inference Path Map

### How a local agent turn reaches Ollama

```
Issue assigned → Paperclip heartbeat triggered
      ↓
opencode_local adapter starts
  - Reads adapterConfig: { model: "ollama/gemma4:26b-a4b-it-q4_K_M", variant: (if set) }
  - Builds OpenCode CLI args: `opencode run --format json --model ollama/gemma4:26b-a4b-it-q4_K_M [--variant <V>]`
  - Injects AGENTS.md as system prompt (instructionsFilePath or default)
      ↓
OpenCode CLI runs
  - Reads ~/.config/opencode/opencode.json for provider config
  - Provider: ollama / npm: @ai-sdk/openai-compatible
  - baseURL: http://localhost:11434/v1
      ↓
OpenCode → Ollama API
  POST http://localhost:11434/v1/chat/completions
  {
    "model": "gemma4:26b-a4b-it-q4_K_M",
    "messages": [
      { "role": "system", "content": "<AGENTS.md content>" },
      { "role": "user", "content": "<task context>" }
    ]
    // temperature, stop, format: NOT set per-request; fall through to Modelfile defaults
  }
      ↓
Ollama Modelfile defaults applied:
  PARAMETER temperature 1      ← NOT 0
  PARAMETER stop <none>        ← NO stop tokens
  TEMPLATE {{ .Prompt }}       ← pass-through; Ollama auto-applies chat template
  PARAMETER num_ctx 65536      ← from SAG-3606 ctx-cutover
      ↓
Response: raw text → OpenCode → Paperclip → agent turn output
```

**Key inference levers and where they live:**

| Lever | Location | Current value |
|-------|----------|---------------|
| Model | adapterConfig.model | `ollama/gemma4:26b-a4b-it-q4_K_M` |
| Context window | Modelfile PARAMETER num_ctx | 65536 (baked, SAG-3606) |
| Temperature | Modelfile PARAMETER temperature | **1** (not 0) |
| Stop tokens | Modelfile PARAMETER stop | **none set** |
| Chat template | Modelfile TEMPLATE | `{{ .Prompt }}` (Ollama applies model-default) |
| Think suppression | AGENTS.md line 1 `/no_think` | per-agent prompt token |
| Narrate suppression | AGENTS.md CRITICAL line | per-agent prompt instruction |
| JSON mode | opencode.json / per-request | **not set** |
| Variant (reasoning) | adapterConfig.variant | not set (no thinking profile) |
| Strip-think post-process | opencode_local adapter | **not present** (only in enrichment batch_runner) |

**Important:** Temperature, stop tokens, json_mode are NOT configurable via Paperclip `adapterConfig` fields — they must be set either in the shared Modelfile or via OpenCode's `opencode.json` per-model options (neither is currently set). Changing the Modelfile affects all 21 agents simultaneously (shared tag `gemma4:26b-a4b-it-q4_K_M`) and is a HARD LIMIT requiring CEO approval per SAG-4192.

**Contrast with enrichment path:** The enrichment `batch_runner`/`dispatcher.py` path applies Config B' (`think:false`, `json_object`, `max_tokens=4096`, `temp=0`) per-request via the LiteLLM gateway. The 21 production agents use the OpenCode path which has NO per-request hygiene overrides.

---

## 2. §4 Hygiene Audit Table — 21 Agents

Checklist columns:
- **think-sup**: `/no_think` token at AGENTS.md line 1
- **narrate-sup**: "Think silently; do not narrate" CRITICAL line present
- **temp-0**: temperature=0 set (for deterministic/structured-output tasks)
- **stop-tok**: stop tokens set in inference path
- **json-mode**: json_object / response_format set where structured output expected
- **strip-think**: post-process strip of `<think>…</think>` tags

Legend: ✅ pass | ❌ fail | N/A not applicable to this agent's task type | ⚠️ partial

| Agent | think-sup | narrate-sup | temp-0 | stop-tok | json-mode | strip-think | Notes |
|-------|-----------|-------------|--------|----------|-----------|-------------|-------|
| Reports & Audits Manager | ✅ | ✅ **FIXED** | ❌ | ❌ | N/A | ❌ | Narrate line added SAG-4192 |
| Local-AI Catalog Enricher | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | Structured JSON expected; no json_mode |
| Estimation Manager | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Operations Deputy | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| QA SSI Auditor | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Estimator of Cabinet | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Business Dev Deputy | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Editor/Proofreader | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Land Steward Deputy | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Labor Rate Analysis | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Research Assistant | ✅ **FIXED** | ✅ **FIXED** | ❌ | ❌ | N/A | ❌ | Both `/no_think` + narrate added SAG-4192 |
| Doc Extractor | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | Structured extraction; no json_mode |
| Code-Reviewer | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Project Manager | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Pricing Manager | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Cabinet Designer | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Ollama Canary | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | Has explicit `<think>` leak check in instructions |
| Paralegal | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | |
| Coder (DEPRECATED) | ✅ | ✅ | ❌ | ❌ | N/A | ❌ | Deprecated; low risk |
| Bookkeeper | ✅ | ✅ **FIXED** | ❌ | ❌ | N/A | ❌ | Narrate line added SAG-4192 |
| SKU Cataloger | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | Structured SKU output; no json_mode |

### Summary

| Check | Pass | Fail | Fixed this run |
|-------|------|------|---------------|
| think-suppression (`/no_think`) | 21/21 | 0/21 | 1 (Research Assistant) |
| narrate-suppression | 21/21 | 0/21 | 3 (RAM, Bookkeeper, Research Asst) |
| temp-0 (deterministic) | 0/21 | 21/21 | 0 — needs Modelfile change (CEO gate) |
| stop-tokens | 0/21 | 21/21 | 0 — needs Modelfile change (CEO gate) |
| json-mode (structured agents) | 0/3 | 3/3 | 0 — needs adapter or opencode config (CTO spike) |
| strip-think | 0/21 | 21/21 | 0 — needs platform adapter change (PR) |

---

## 3. Cheap/Safe Fixes Applied (this run)

All 3 are AGENTS.md prompt-level edits only; reversible; no shared-Modelfile or bulk-PATCH involved.

### Fix 1: Research Assistant (`1bb6be46`) — added `/no_think` + narrate line
**Before:** File started directly with HANDOFF PROTOCOL; no think suppression, no narrate suppression.  
**After:** Line 1 = `/no_think`, lines 3-4 = SAG-3326 CRITICAL narrate discipline block.

### Fix 2: Reports & Audits Manager (`dd1f4e1f`) — added narrate line
**Before:** Had `/no_think` at line 1 but narrate discipline block absent.  
**After:** SAG-3326 CRITICAL block inserted between `/no_think` and HANDOFF PROTOCOL.

### Fix 3: Bookkeeper (`4d86bdce`) — added narrate line
**Before:** Had `/no_think` at line 1 but narrate discipline block absent.  
**After:** SAG-3326 CRITICAL block inserted between `/no_think` and HANDOFF PROTOCOL.

---

## 4. Fixes Requiring CEO Approval (Shared Modelfile / Bulk)

These changes are safe in isolation but touch the SHARED Modelfile tag `gemma4:26b-a4b-it-q4_K_M`, affecting all 21 agents simultaneously. Per SAG-4192 HARD LIMITS, CEO approval required before applying.

### A. Temperature: `PARAMETER temperature 1` → `PARAMETER temperature 0`
- **Benefit:** Eliminates stochastic variance in tool-call JSON, status decisions, and structured outputs. Eliminates primary failure mode from SAG-2154 Phase A (thinking-token exhaustion compounded by temp=1 sampling).
- **Risk:** Reduces creativity for Editor/Proofreader; mild impact on Research Assistant summaries. Mitigated: those agents don't need stochastic variation; they need correctness.
- **Mechanism:** `ollama create gemma4:26b-a4b-it-q4_K_M -f <Modelfile-with-temp0>`
- **Reversibility:** Trivially reversible (re-run ollama create with old Modelfile).
- **Scope:** All 21 agents. Recommend canary on Ollama Canary agent first.

### B. Stop tokens
- **Benefit:** Prevents runaway completions if the model generates markdown fences or continuation tokens past the answer.
- **Current state:** No stop tokens. The Gemma4 model uses EOS token naturally; less critical than Qwen3 was.
- **Recommendation:** Low priority pending observation of actual output leakage. Add if Ollama Canary confirms stop-token bleed.

### C. json_mode for structured-output agents
- **Agents:** Local-AI Catalog Enricher, Doc Extractor, SKU Cataloger.
- **Mechanism:** Requires either (a) OpenCode `opencode.json` per-model format option or (b) adapter-level per-request override (not currently supported in opencode_local adapter config).
- **Path:** CTO spike to wire `response_format: { type: "json_object" }` into the per-request call for these 3 agents; or add a new `adapterConfig.jsonMode: true` field in the opencode_local adapter.
- **Reversibility:** Reversible per-agent.

### D. Strip-think post-process in opencode_local adapter
- **Current state:** The enrichment `batch_runner` strips `<think>…</think>` via regex. The opencode_local adapter has no equivalent.
- **Risk level:** LOW with `/no_think` in place — Gemma4 respects the token. But no safety net if model drifts.
- **Path:** PR to `packages/adapters/opencode-local/src/server/execute.ts` to strip think blocks from final output before returning to Paperclip. Scope = platform code (CTO review required).

---

## 5. Confidence Score (GOVERNANCE §3)

**Conf 8/10.** Inference path map confirmed via source inspection of `opencode_local` adapter source and live `~/.config/opencode/opencode.json`. Checklist findings verified via grep of all 21 AGENTS.md files. 3 cheap/safe fixes applied and verified. CEO-gate proposals are well-bounded with clear rollback paths.

Deductions: -1 adapter config not confirmed from DB (API returns empty); -1 json_mode mechanism for structured agents requires prototype before claiming safe.
