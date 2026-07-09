/**
 * Unified Ticket Health routine runner — SAG-2931.
 *
 * Sweeps ALL open issues (todo, in_progress, in_review, blocked), classifies
 * staleness + blockers, emits capped escalation @-mentions, and publishes a
 * single unified digest comment to SAG-2591.
 *
 * Usage:
 *   npx tsx scripts/ticket-health-run.ts [--dry-run] [--debug] [--once]
 *
 * Flags:
 *   --dry-run   Print the digest to stdout; do NOT post any comment.
 *   --debug     Dump full per-issue classification alongside the digest.
 *   --once      No-op marker (runner always fires once).
 *
 * Required env:
 *   PAPERCLIP_API_KEY
 * Optional env:
 *   PAPERCLIP_API_URL              (default: http://localhost:3100)
 *   PAPERCLIP_COMPANY_ID           (default: 1dc911ed-ff05-4072-b2ae-a3e3177e3873)
 *   TICKET_HEALTH_PUBLISH_ISSUE_ID (default: 2fcd939a-b715-4980-8364-dd99c2f9d18b — SAG-2591)
 */

import fs from 'node:fs';
import path from 'node:path';
import {
  classify,
  rank,
  type IssueDetail,
  type Interaction,
  type ClassifiedIssue,
  type BlockerCandidate,
} from '../src/blockers.js';
import {
  classifyStaleness,
  escalationStep,
  classifyBlockedSubtype,
  isWaitingOnBoard,
  planEscalations,
  applyEscalationPosts,
  ticketHealthHash,
  renderTicketHealthDigest,
  MAX_MENTIONS,
  type HealthItem,
  type EscalationRecord,
  type IssueStatus,
} from '../src/staleness.js';

// ─────────────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────────────

const COMPANY_ID = process.env['PAPERCLIP_COMPANY_ID'] ?? '1dc911ed-ff05-4072-b2ae-a3e3177e3873';
const API_URL = process.env['PAPERCLIP_API_URL'] ?? 'http://localhost:3100';
const API_KEY = process.env['PAPERCLIP_API_KEY'] ?? '';
const PUBLISH_ISSUE_ID =
  process.env['TICKET_HEALTH_PUBLISH_ISSUE_ID'] ?? '2fcd939a-b715-4980-8364-dd99c2f9d18b';

const HOME = process.env['HOME'] ?? '/root';
const STATE_FILE = path.join(
  HOME,
  '.paperclip',
  'instances',
  'default',
  'companies',
  COMPANY_ID,
  'knowledge',
  'ticket-health-state.json',
);

const CONCURRENCY = 5;

// ─────────────────────────────────────────────────────────────────────────────
// CLI flags
// ─────────────────────────────────────────────────────────────────────────────

const cliArgs = process.argv.slice(2);
const DRY_RUN = cliArgs.includes('--dry-run');
const DEBUG = cliArgs.includes('--debug');

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

interface TicketHealthState {
  lastRunAt: string;
  lastHash: string;
  lastDigestCommentId: string | null;
  escalations: Record<string, EscalationRecord>;
}

function loadState(): TicketHealthState {
  if (!fs.existsSync(STATE_FILE)) {
    return { lastRunAt: new Date(0).toISOString(), lastHash: '', lastDigestCommentId: null, escalations: {} };
  }
  return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')) as TicketHealthState;
}

function saveState(state: TicketHealthState): void {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
}

// ─────────────────────────────────────────────────────────────────────────────
// API types and helpers
// ─────────────────────────────────────────────────────────────────────────────

interface RawIssue {
  id: string;
  identifier: string;
  title: string;
  description?: string | null;
  priority: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  lastActivityAt?: string | null;
  assigneeAgentId: string | null;
  assigneeUserId?: string | null;
  blockedBy?: Array<{
    identifier: string;
    status: string;
    assigneeAgentId: string | null;
    terminalBlockers?: Array<{ identifier?: string; status: string; assigneeAgentId?: string | null; title?: string | null }> | null;
  }> | null;
  blocks?: Array<{ identifier: string; status: string; title?: string | null }>;
  blockerAttention?: {
    state: string;
    unresolvedBlockerCount: number;
    sampleBlockerIdentifier: string | null;
  };
}

interface AgentInfo {
  id: string;
  name: string;
  reportsTo?: string | null;
}

function makeHeaders(): Record<string, string> {
  return { Authorization: `Bearer ${API_KEY}`, 'Content-Type': 'application/json' };
}

async function listIssuesByStatus(status: string): Promise<RawIssue[]> {
  const results: RawIssue[] = [];
  let offset = 0;
  while (true) {
    const url = `${API_URL}/api/companies/${COMPANY_ID}/issues?status=${status}&limit=200&offset=${offset}`;
    const res = await fetch(url, { headers: makeHeaders() });
    if (!res.ok) throw new Error(`listIssuesByStatus(${status}): HTTP ${res.status}`);
    const data = await res.json();
    const arr: RawIssue[] = Array.isArray(data)
      ? (data as RawIssue[])
      : ((((data as Record<string, unknown>)['issues'] ??
          (data as Record<string, unknown>)['data']) ?? []) as RawIssue[]);
    results.push(...arr);
    if (arr.length < 200) break;
    offset += 200;
  }
  return results;
}

async function getIssueDetail(id: string): Promise<IssueDetail> {
  const res = await fetch(`${API_URL}/api/issues/${id}`, { headers: makeHeaders() });
  if (!res.ok) throw new Error(`getIssueDetail(${id}): HTTP ${res.status}`);
  return res.json() as Promise<IssueDetail>;
}

async function getInteractions(id: string): Promise<Interaction[]> {
  const res = await fetch(`${API_URL}/api/issues/${id}/interactions`, { headers: makeHeaders() });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? (data as Interaction[]) : [];
}

async function postComment(issueId: string, body: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/issues/${issueId}/comments`, {
    method: 'POST',
    headers: makeHeaders(),
    body: JSON.stringify({ body }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`postComment: HTTP ${res.status} — ${text.slice(0, 200)}`);
  }
  const data = (await res.json()) as { id: string };
  return data.id;
}

async function listAgents(): Promise<AgentInfo[]> {
  const res = await fetch(`${API_URL}/api/companies/${COMPANY_ID}/agents`, {
    headers: makeHeaders(),
  });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? (data as AgentInfo[]) : [];
}

// ─────────────────────────────────────────────────────────────────────────────
// Concurrency limiter
// ─────────────────────────────────────────────────────────────────────────────

async function runConcurrently<T, R>(
  items: T[],
  fn: (item: T) => Promise<R>,
  limit: number,
): Promise<(R | null)[]> {
  const results: (R | null)[] = new Array(items.length).fill(null);
  const queue = [...items.entries()];

  async function worker(): Promise<void> {
    while (queue.length > 0) {
      const entry = queue.shift();
      if (!entry) break;
      const [i, item] = entry;
      try {
        results[i] = await fn(item);
      } catch (err) {
        console.error(
          `  [ticket-health] error enriching ${(item as { identifier?: string }).identifier ?? i}: ${err}`,
        );
        results[i] = null;
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  if (!API_KEY) {
    console.error('ERROR: PAPERCLIP_API_KEY not set');
    process.exit(1);
  }

  const runAt = new Date().toISOString();
  const nowMs = Date.now();
  const state = loadState();

  // ── 1. Fetch all open issues ──────────────────────────────────────────────

  console.log('[ticket-health] fetching all open issues…');
  const statusGroups = await Promise.all(
    ['todo', 'in_progress', 'in_review', 'blocked'].map((s) => listIssuesByStatus(s)),
  );
  const allIssues = statusGroups.flat();
  console.log(`[ticket-health] ${allIssues.length} open issues fetched`);

  // ── 2. Enrich blocked + in_review (detail + interactions) ─────────────────

  const needsEnrich = allIssues.filter(
    (i) => i.status === 'blocked' || i.status === 'in_review',
  );
  console.log(
    `[ticket-health] enriching ${needsEnrich.length} blocked/in_review issues (concurrency=${CONCURRENCY})…`,
  );

  const enrichedMap = new Map<string, { detail: IssueDetail; interactions: Interaction[] }>();

  await runConcurrently(
    needsEnrich,
    async (issue) => {
      const [detail, interactions] = await Promise.all([
        getIssueDetail(issue.id),
        getInteractions(issue.id),
      ]);
      enrichedMap.set(issue.id, { detail, interactions });
    },
    CONCURRENCY,
  );

  // ── 3. Load agent roster for manager lookup (Step 2 escalations) ──────────

  console.log('[ticket-health] loading agent roster…');
  const agents = await listAgents();

  // ── 4. Classify all issues ────────────────────────────────────────────────

  const healthItems: HealthItem[] = [];
  const blockedForBlockers: Array<{
    raw: RawIssue;
    detail: IssueDetail;
    interactions: Interaction[];
  }> = [];

  for (const raw of allIssues) {
    const enriched = enrichedMap.get(raw.id);
    const interactions: Interaction[] = enriched?.interactions ?? [];
    const detail = enriched?.detail;

    const staleness = classifyStaleness(raw, nowMs);
    const step = staleness.breached ? escalationStep(staleness.idleHours, staleness.slaHours) : 0;

    let blockedSubclass: HealthItem['blockedSubclass'];
    let waitingBoard = false;

    if (raw.status === 'blocked') {
      // Always use the single-GET `detail` for blocker classification — the list
      // endpoint omits the blockedBy join, so `raw.blockedBy` is always empty.
      // If enrichment failed, default to genuinely_blocked (conservative) rather
      // than free_text_blocked (which would be a false signal from missing join data).
      blockedSubclass = detail ? classifyBlockedSubtype(detail) : 'genuinely_blocked';
      if (detail) {
        blockedForBlockers.push({ raw, detail, interactions });
      }
    }

    if (raw.status === 'in_review') {
      waitingBoard = isWaitingOnBoard(raw, interactions);
    }

    healthItems.push({
      id: raw.id,
      identifier: raw.identifier,
      title: raw.title,
      priority: raw.priority,
      status: raw.status as IssueStatus,
      idleHours: staleness.idleHours,
      slaHours: staleness.slaHours,
      breached: staleness.breached,
      multiplier: staleness.multiplier,
      blockedSubclass,
      isWaitingOnBoard: waitingBoard,
      escalationStep: step,
      assigneeAgentId: raw.assigneeAgentId,
      assigneeUserId: raw.assigneeUserId,
      createdAt: raw.createdAt,
    });
  }

  // ── 5. Run blockers classifier (reuse blockers.ts) ────────────────────────

  const boardItems: ClassifiedIssue[] = [];
  const agentItems: ClassifiedIssue[] = [];

  for (const { raw, detail, interactions } of blockedForBlockers) {
    const candidate: BlockerCandidate = {
      id: raw.id,
      identifier: raw.identifier,
      title: raw.title,
      description: raw.description,
      priority: raw.priority as BlockerCandidate['priority'],
      createdAt: raw.createdAt,
      assigneeAgentId: raw.assigneeAgentId,
      blockerAttention: raw.blockerAttention ?? {
        state: 'none',
        unresolvedBlockerCount: 0,
        sampleBlockerIdentifier: null,
      },
    };
    const result = classify(candidate, detail, interactions);
    const blastRadius = detail.blocks?.length ?? 0;
    const classified: ClassifiedIssue = {
      detail,
      classification: result.classification,
      category: result.category,
      matchedRule: result.matchedRule,
      blastRadius,
      stale: result.stale,
    };
    if (result.classification === 'board') {
      boardItems.push(classified);
    } else {
      agentItems.push(classified);
    }
  }

  const rankedBoardItems = rank(boardItems);

  // ── 6. Compute escalations (idempotent, capped at MAX_MENTIONS) ───────────

  const escalationPlan = planEscalations({
    healthItems,
    priorEscalations: state.escalations,
    agents,
    runAt,
    maxMentions: MAX_MENTIONS,
  });
  const { escalationsEmitted } = escalationPlan;

  // ── 7. Classification summary ─────────────────────────────────────────────

  const statusCounts = {
    todo: healthItems.filter((i) => i.status === 'todo').length,
    in_progress: healthItems.filter((i) => i.status === 'in_progress').length,
    in_review: healthItems.filter((i) => i.status === 'in_review').length,
    blocked: healthItems.filter((i) => i.status === 'blocked').length,
  };
  const falseBlockedCount = healthItems.filter((i) => i.blockedSubclass === 'false_blocked').length;
  const waitingOnBoardCount = healthItems.filter((i) => i.isWaitingOnBoard).length;
  const totalBreached = healthItems.filter((i) => i.breached).length;
  const wouldEscalate = healthItems.filter(
    (i) => i.breached && i.escalationStep > 0 && i.escalationStep < 3,
  ).length;

  console.log(`[ticket-health] classification summary:`);
  console.log(
    `  todo=${statusCounts.todo} in_progress=${statusCounts.in_progress} in_review=${statusCounts.in_review} blocked=${statusCounts.blocked}`,
  );
  console.log(
    `  breached=${totalBreached} | false-blocked=${falseBlockedCount} | waiting-on-board=${waitingOnBoardCount}`,
  );
  console.log(
    `  would-escalate=${wouldEscalate} | escalations-emitted=${escalationsEmitted.length} (of max ${MAX_MENTIONS})`,
  );

  if (DEBUG) {
    console.log('\n[ticket-health] === PER-ISSUE DUMP (breached only) ===');
    for (const item of healthItems
      .filter((i) => i.breached)
      .sort((a, b) => b.idleHours - a.idleHours)) {
      console.log(
        `  ${item.identifier.padEnd(10)} [${item.status.padEnd(11)}] ` +
          `idle=${Math.round(item.idleHours).toString().padStart(4)}h ` +
          `step=${item.escalationStep} ` +
          `subclass=${(item.blockedSubclass ?? '-').padEnd(18)} ` +
          `board=${item.isWaitingOnBoard}`,
      );
    }
    console.log('[ticket-health] === END DUMP ===\n');
  }

  // ── 8. Build digest ───────────────────────────────────────────────────────

  const boardBlockerIds = rankedBoardItems.map((i) => i.detail.id);
  const hash = ticketHealthHash(healthItems, boardBlockerIds);

  let body: string;
  let isDedup = false;

  if (hash === state.lastHash && state.lastDigestCommentId) {
    isDedup = true;
    body = `*Ticket health unchanged since ${state.lastRunAt} — no new staleness or blocker changes. Prior digest: comment \`${state.lastDigestCommentId}\`.*`;
    console.log('[ticket-health] hash unchanged — posting dedup line');
  } else {
    body = renderTicketHealthDigest(healthItems, {
      runAt,
      priorRunAt: state.lastRunAt || null,
      totalSwept: allIssues.length,
      escalationsEmitted,
      boardClassified: rankedBoardItems,
      boardAgentClassified: agentItems,
    });
    console.log('[ticket-health] new or changed digest — rendering full report');
  }

  // Show digest preview
  console.log('\n[ticket-health] === DIGEST PREVIEW (first 3000 chars) ===');
  console.log(body.slice(0, 3000));
  if (body.length > 3000) console.log(`…(${body.length - 3000} more chars)…`);
  console.log('[ticket-health] === END PREVIEW ===\n');

  if (DRY_RUN) {
    console.log('[ticket-health] --dry-run: skipping all API writes');
    return;
  }

  // ── 9. Post escalation comments ───────────────────────────────────────────

  const newEscalationState = await applyEscalationPosts({
    escalationsEmitted,
    state: escalationPlan.newEscalationState,
    runAt,
    postFn: async (issueId, body) => {
      const esc = escalationsEmitted.find((item) => item.issueId === issueId);
      if (esc) {
        console.log(
          `[ticket-health] posting step-${esc.step} escalation for ${esc.identifier} → agent ${esc.targetAgentId.slice(0, 8)}…`,
        );
      }
      return postComment(issueId, body);
    },
    onPostError: (esc, err, permanent) => {
      const disposition = permanent ? 'folded to digest and recorded handled' : 'will retry next run';
      console.error(
        `[ticket-health] escalation comment failed for ${esc.identifier}: ${err} (${disposition})`,
      );
    },
  });

  // ── 10. Post digest ───────────────────────────────────────────────────────

  console.log(`[ticket-health] posting digest to issue ${PUBLISH_ISSUE_ID}…`);
  const commentId = await postComment(PUBLISH_ISSUE_ID, body);
  console.log(`[ticket-health] posted comment ${commentId}`);

  saveState({
    lastRunAt: runAt,
    lastHash: isDedup ? state.lastHash : hash,
    lastDigestCommentId: isDedup ? state.lastDigestCommentId : commentId,
    escalations: newEscalationState,
  });
  console.log('[ticket-health] state saved');
}

main().catch((err) => {
  console.error('[ticket-health] fatal:', err);
  process.exit(1);
});
