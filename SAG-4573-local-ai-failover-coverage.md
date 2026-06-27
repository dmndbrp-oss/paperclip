# SAG-4573 — Local AI per Department: Coverage Audit + Failover Pairing

**Author:** CEO (Opus 4.8) · **Date:** 2026-06-22 · **Status:** audit complete; mechanism delegated to CTO

## What the board asked

1. Every department has **at least 1 local AI agent**.
2. When **cloud usage limits are hit**, auto-assign the in-flight work to the local AI agent to continue (if possible).
3. When the cloud agent is **back online**, it **reviews** the local agent's work and continues.

> Note on terms: in this fleet, `claude_local` adapters run **cloud** Claude models (Anthropic API — subject to session/usage limits, e.g. "resets 9pm"). Only `opencode_local`/`ollama` (Qwen3.6, qwen3:30b-a3b) are **true on-box local** with no cloud usage cap. The run that paused *this very issue* failed with `claude_transient_upstream: session limit · resets 9pm` — a textbook instance of the problem the board wants solved.

## Part 1 — Department coverage: ALREADY MET ✓

Grouped by top-level department head (43 agents total). LOCAL = on-box Ollama/Qwen.

| Department (head) | Local / total | Has local? |
|---|---|---|
| Engineering (CTO) | 5 / 12 | YES |
| Finance (CFO) | 3 / 5 | YES |
| Pricing (Pricing Director) | 5 / 7 | YES |
| SSI (SSI Director) | 2 / 3 | YES |
| Legal (Chief Legal Officer) | 1 / 2 | YES |
| Marketing (Marketing Director) | 1 / 2 | YES |
| Operations (Operations Director) | 1 / 2 | YES |
| Land Stewardship (Director) | 1 / 2 | YES |
| Business Development (BizDev Director) | 1 / 2 | YES |
| Research (Research Agent) | 1 / 2 | YES |

**Every functional department has ≥1 local agent. No new hires required.**

### Nuances (not department gaps)
- **QA sub-team** (QA Unit Tests, QA Regression, QA Integration — under Engineering) is **all cloud**. Engineering overall has local coverage (Code Reviewer Lite, local SWEs), and local QA-capable agents exist elsewhere (SKU QA Auditor, Pricing QA Auditor). Resolved via pairing below; a dedicated local QA agent can be hired later if the board wants QA isolated from Engineering's local pool.
- **Executive Assistant** and **Strategic Advisor / Mentor** are single-person CEO-direct staff roles (cloud), not departments — no subordinate to be "local". Out of scope for the per-department bar.

## Part 2/3 — Failover + review pairing (CEO-authored org map; CTO wires the mechanism)

Pairing is an **org decision (CEO)**; the detection/reassignment/review-handoff mechanism is **technical (CTO)**. Proposed understudy map (continuation of **IC work only** — see tier rule):

| Cloud agent (limit-prone) | Local understudy (continues work) |
|---|---|
| Senior Software Engineer (cloud) | Senior Software Engineer (local) / Junior Software Engineer |
| Knowledge Digester | Local-AI Catalog Enricher / Research Assistant |
| Data Analyst | Reports & Audits Manager / Bookkeeper |
| QA Unit / Regression / Integration | Code Reviewer Lite / SKU QA Auditor |
| Research Agent (IC tasks) | Research Assistant |

### Tier rule (governance guardrail)
- **IC / production tasks** → auto-failover to local understudy is appropriate.
- **C-suite & Director decisions (CEO, CTO, CFO, directors)** → do **NOT** auto-delegate strategic decisions to a local model. On limit, the issue waits or escalates; local agents may only continue bounded IC sub-work, never make director-level calls. This protects decision quality and the §1 trust framework.
- **Review-on-recovery is mandatory**: when the cloud agent returns, it reviews the local agent's output before the work is accepted (board requirement #3). This doubles as the quality gate for the lower-capability local model.

## Disposition
- Part 1: **closed** — coverage already met; documented above.
- Parts 2/3: **delegated to CTO** as a design-first, approval-gated child issue (investigate native Paperclip support for failover-on-`claude_transient_upstream`/session-limit vs. a routine/governance rule; design → CEO/board approval → build). SAG-4573 blocked on that child; CEO owns board closure.
