# SAG-2572 — Daily Blockers Routine: Implementation Spec (CTO)

**Author:** CTO (Opus 4.8) · **Parent:** SAG-2570 (CEO delegation) · **Date:** 2026-05-31
**Implementer:** Coder (Claude) `3ab7fa06-f831-4631-922a-2fe824005788`
**Status:** spec frozen — ready for build + canary

## 1. Goal
A **daily routine** that surfaces open blockers the **human board** can resolve, ranked
highest→lowest, each with numbered step-by-step resolution instructions, published as a single
deduped comment on **SAG-2570** (`279c6f12-5cc1-487b-ba8d-50a17caf9c4c`).

Key design decision: **fully deterministic, no LLM.** The Paperclip API exposes every field we
need. We do NOT reuse the digester's summarizer/worker-delegation path (that exists only because
*summarization* needs an LLM; classification + ranking here are rule-based). We DO reuse the
digester's harness conventions: env config, bare `fetch` API client, JSONL state file, the
`knowledge/` TS layout, and the Researcher runner.

## 2. Data model — verified against live API (2026-05-31)
Base: `http://localhost:3100` · Auth: `Authorization: Bearer $PAPERCLIP_API_KEY` · Company
`1dc911ed-ff05-4072-b2ae-a3e3177e3873`.

- **List candidates:** `GET /api/companies/{companyId}/issues?status=blocked` → array. Each item has:
  `identifier`, `id`, `title`, `description`, `priority` (critical|high|medium|low),
  `assigneeAgentId`, `createdAt`, `startedAt`, `updatedAt`, and
  `blockerAttention` = `{ state: none|covered|needs_attention|stalled, unresolvedBlockerCount,
  sampleBlockerIdentifier }`.
  - Live count today: **93** blocked issues.
  - Also sweep `blockerAttention.state != 'none'` items that may not be `status==blocked` — query is
    the union: for v1, `status=blocked` already carries `blockerAttention`; additionally pull
    `state in {needs_attention, stalled}` items if any surface outside `blocked` (none observed today,
    but keep the union to be safe — see §3 step 1).
- **Per-issue detail:** `GET /api/issues/{id}` → adds `blockedBy[]` and `blocks[]`.
  - `blocks[]`  = **downstream blast radius** (issues this one blocks). Use `len(blocks)` as the
    transitive-ish proxy (1 hop; good enough for v1 — do NOT walk the full graph).
  - `blockedBy[].terminalBlockers[]` = root blocker issues, each `{ identifier, status,
    assigneeAgentId, title }`. This is how we find the *actual* unblock owner.
- **Interactions:** `GET /api/issues/{id}/interactions` → array (200, `[]` when none). A pending
  `{ kind: 'request_confirmation', status: 'pending' }` ⇒ board-resolvable (board-only `/accept`).
- **Post digest:** `POST /api/issues/{id}/comments` body `{ "body": "<markdown>" }` (field is `body`,
  author resolves from the runner's token — ref memory: comment author field `authorAgentId`).

## 3. Algorithm (per fire)
1. **Gather candidates.** Page `GET .../issues?status=blocked`. Build a set keyed by `id`. (If a
   future status-union is needed, also fetch `?status=in_progress` and keep those with
   `blockerAttention.state in {needs_attention,stalled}`; dedupe by id.)
2. **Enrich.** For each candidate, `GET /api/issues/{id}` to get `blocks[]` + `blockedBy[]`
   (with `terminalBlockers[]`). For candidates whose `blockerAttention.unresolvedBlockerCount==0`
   AND `sampleBlockerIdentifier==null`, there is no first-class blocker issue — classify from the
   candidate's own title+description (the candidate *is* the board action, e.g. SAG-2337 OAuth
   disconnect). Also `GET .../interactions` for the candidate to detect pending confirmations.
   (Concurrency: cap at 5 in-flight requests; 93 issues × ~2 calls ≈ ok in <60s.)
3. **Classify** each candidate as `board` | `agent` via the signal table in §4. Only `board` items
   enter the digest body. `agent` items: keep up to 5 highest-priority for an "agent-owned, FYI"
   tail.
4. **Rank** board items: sort by (a) `priority` rank critical=0,high=1,medium=2,low=3 ASC, then
   (b) age = now − `createdAt` DESC (oldest first), then (c) `len(blocks)` DESC (blast radius).
5. **Emit** per item: `IDENTIFIER — title`, category, why board is needed, who/what is blocked
   downstream (`blocks[]` identifiers, count), and **numbered resolution steps** from the §5
   template for its category (interpolate identifiers/URLs).
6. **Dedup / publish.** Compute a stable content hash (sha256 of the normalized board-item id+category
   list, excluding timestamps). Load prior hash from state file. If unchanged ⇒ post a one-line
   "No change since {priorRunAt} — N board-resolvable blockers still open (see prior digest)."
   instead of the full digest. Else post the full digest. Always update state file with
   `{ lastRunAt, lastHash, lastDigestCommentId }`.

## 4. Classification signal table (deterministic)
A candidate is **board-resolvable** if ANY rule fires. Evaluate against the candidate's own
title+description AND its `terminalBlockers[]` titles. Each rule → a category (drives §5 template).

| # | Category | Fires when (case-insensitive) |
|---|----------|------------------------------|
| 1 | `pending_confirmation` | candidate or any blocker has a pending `request_confirmation` interaction; OR text matches `request_confirmation`, `/accept`, `board must accept`, `pending board`, `awaiting board`, `confirmation .* pending` |
| 2 | `routine_cron` | text matches `routine`, `unpause`, `cron`, `\b0 \*/?\d`, `go-live` AND (`paused`/`board-only`/`enable`) |
| 3 | `oauth_reconnect` | text matches `oauth`, `reconnect`, `claude\.ai`, `mcp .*disconnect`, `integration .*(disconnect|reconnect)` |
| 4 | `credential_purchase` | text matches `api[- ]?key`, `ANTHROPIC_API_KEY`, `provision .*(key|credential)`, `purchase`, `\bPAT\b`, `github push cred`, `token .*provision` |
| 5 | `infra_unreachable` | text matches `tailscale`, `tunnel`, `dns`, `provision .*environment`, `D365 .*provision`, `Dynamics .*setup` |
| 6 | `hire_or_model_signoff` | text matches `hire .*approval`, `model[- ]change`, `sign[- ]?off`, `board approval`, `approve .*(hire|model)` |

**Exclusion (agent-owned):** if NO board rule fires AND the terminal blocker (or candidate)
`assigneeAgentId` ∈ {internal agents: CTO `f3c48afc…`, Coder `3ab7fa06…`, DoE `b214c191…`,
any Director/QA}, classify `agent`. A blocker whose terminal blocker is `status==cancelled` or
`done` (stale/phantom, e.g. SAG-326→SAG-1093 cancelled) is `agent` (owner just needs to clear the
stale link) — note it in the FYI tail, never the board section.

> Precision note: this is heuristic v1. False-positives are cheap (board sees one extra line);
> false-negatives are the risk. The canary (§7) is how we tune the table before go-live. Document
> every candidate's classification + matched rule in a `--debug` JSON dump so CTO can audit the
> canary.

## 5. Resolution templates (interpolate {ID}, {downstream}, {owner})
- **pending_confirmation:** 1. Open {ID} in the web UI. 2. Go to the Interactions panel. 3. Accept
  the pending request_confirmation (agents get 403 on `/accept` — board-only). 4. The assignee wakes
  and continues automatically.
- **routine_cron:** 1. Web UI → Routines. 2. Find routine for {ID}. 3. Create/unpause and set the
  stated cron. 4. Confirm `paused=false`. (Routine management is board-only even for CEO.)
- **oauth_reconnect:** 1. Open claude.ai → Settings → Connectors. 2. Reconnect the named integration
  for {ID}. 3. Re-run {ID}'s assignee. (Locked OAuth disconnects need board action — ref SAG-2337.)
- **credential_purchase:** 1. Provision the named credential/key for {ID}. 2. Add it to the server
  `.env` or the agent `adapterConfig.env`. 3. Notify {owner}. (No standalone Anthropic key exists.)
- **infra_unreachable:** 1. Restore the named infra for {ID} (e.g. Tailscale tunnel / D365 env).
  2. Verify reachability. 3. Re-run {ID}.
- **hire_or_model_signoff:** 1. Open {ID}. 2. Approve the hire / model-change sign-off. 3. Assignee
  proceeds.

## 6. File layout (reuse digester conventions)
- `knowledge/src/api-client.ts` — extract shared client: `listIssuesByStatus()`, `getIssue(id)`,
  `getInteractions(id)`, `postComment(id, body)`. (Refactor digester's inline fetch into this; keep
  digester green.)
- `knowledge/src/blockers.ts` — pure functions: `classify(candidate, detail, interactions)`,
  `rank(items)`, `renderDigest(items, fyiTail, opts)`, `contentHash(items)`. **No I/O here** so it's
  unit-testable.
- `knowledge/scripts/blockers-run.ts` — runner: read env (`PAPERCLIP_API_KEY`, `PAPERCLIP_API_URL`,
  `PAPERCLIP_COMPANY_ID`, `BLOCKERS_PUBLISH_ISSUE_ID` default `279c6f12-…`), orchestrate §3,
  state file at `~/.paperclip/instances/default/companies/{companyId}/knowledge/blockers-state.json`.
  Flags: `--dry-run` (print, don't post), `--debug` (dump classification JSON), `--once`.
- `knowledge/scripts/__tests__/blockers.test.ts` — unit tests over fixtures (see §7 AC).

## 7. Acceptance criteria (Coder DoD)
1. `knowledge/src/blockers.ts` pure + unit-tested. Tests MUST cover: each of the 6 categories
   classifies `board`; an internal-agent-owned blocker classifies `agent`; a cancelled/done terminal
   blocker → `agent`; ranking orders critical>high and within a tier oldest-first then highest
   blast-radius; dedup hash is stable across runs with identical board-item sets and changes when an
   item is added/removed. Use checked-in JSON fixtures (capture 6–8 real issues incl. SAG-2337,
   SAG-1330, SAG-2170, SAG-326, SAG-2510). All tests pass — paste `vitest`/`node --test` output.
2. `npx tsx knowledge/scripts/blockers-run.ts --dry-run --debug` runs end-to-end against the live API
   and prints the ranked digest + classification dump. Paste the real output.
3. **Canary:** run once for real → posts the ranked board-resolvable digest as ONE comment on
   **SAG-2570** (`279c6f12-5cc1-487b-ba8d-50a17caf9c4c`). Paste the posted comment id + the digest
   markdown verbatim in the issue comment (verification-before-completion — show actual output).
4. State file written; a second immediate run posts the "No change" dedup line, not a full repost.
   Demonstrate both runs.
5. Commit on a `feature/SAG-2572-*` branch (do NOT touch main), conventional message
   `feat(SAG-2572): daily board-resolvable blockers routine + canary`. Report the SHA;
   CTO verifies via `git cat-file -e` + re-reads the canary comment before sign-off.
6. Do NOT create/unpause any cron — routine go-live is board-only and CTO routes the confirmation.

## 8. Routine spec (CTO-owned; for the board confirmation, fires AFTER canary passes)
- **Runner:** Researcher `1e0167fe-1f74-43ea-ad89-36fa724ab80a` (established routine runner; no new
  hire — spend not justified for a deterministic <60s job).
- **Task body:** `npx tsx knowledge/scripts/blockers-run.ts --once`
- **Schedule:** `0 13 * * *` (daily ~13:00Z / morning ET).
- **State:** created **paused**; board accepts the request_confirmation → unpause. Mirrors the
  digester go-live precedent (SAG-2510/2534).
- **Publish target:** SAG-2570 comment thread (standing digest home).

## 9. Sequence / dispositions
1. (this heartbeat) CTO freezes spec → files Coder child (build + canary) → SAG-2572 blocked on it.
2. Coder builds, runs canary, posts to SAG-2570, reports SHA.
3. CTO verifies canary (re-read comment, re-run dry-run, audit classification dump). Tune §4 table
   via a follow-up if mis-classifications appear.
4. CTO routes board `request_confirmation` for go-live (idempotencyKey
   `confirmation:279c6f12-5cc1-487b-ba8d-50a17caf9c4c:routine-golive`), continuationPolicy
   `wake_assignee`.
5. Board accepts → board (or CTO via board) creates the paused routine and unpauses. SAG-2572 done.
