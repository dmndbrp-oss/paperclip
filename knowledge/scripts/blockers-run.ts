/**
 * Daily board-resolvable blockers routine runner — SAG-2572.
 *
 * Queries all blocked issues, classifies board-resolvable vs agent-owned,
 * ranks by priority→age→blast-radius, and publishes a digest comment on
 * SAG-2570 (the standing digest home).
 *
 * Usage:
 *   npx tsx knowledge/scripts/blockers-run.ts [--dry-run] [--debug] [--once]
 *
 * Flags:
 *   --dry-run   Print the digest to stdout; do NOT post to SAG-2570.
 *   --debug     Dump full classification JSON alongside the digest.
 *   --once      Run exactly once and exit (default behaviour; flag is a no-op
 *               semantic marker for routine task body clarity).
 *
 * Required env:
 *   PAPERCLIP_API_KEY
 * Optional env:
 *   PAPERCLIP_API_URL        (default: http://localhost:3100)
 *   PAPERCLIP_COMPANY_ID     (default: 1dc911ed-ff05-4072-b2ae-a3e3177e3873)
 *   BLOCKERS_PUBLISH_ISSUE_ID (default: 279c6f12-5cc1-487b-ba8d-50a17caf9c4c — SAG-2570)
 */

import fs from 'node:fs';
import path from 'node:path';
import {
  classify,
  rank,
  contentHash,
  renderDigest,
  type IssueDetail,
  type Interaction,
  type ClassifiedIssue,
} from '../src/blockers.js';

// ─────────────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────────────

const COMPANY_ID = process.env['PAPERCLIP_COMPANY_ID'] ?? '1dc911ed-ff05-4072-b2ae-a3e3177e3873';
const API_URL = process.env['PAPERCLIP_API_URL'] ?? 'http://localhost:3100';
const API_KEY = process.env['PAPERCLIP_API_KEY'] ?? '';
const PUBLISH_ISSUE_ID =
  process.env['BLOCKERS_PUBLISH_ISSUE_ID'] ?? '279c6f12-5cc1-487b-ba8d-50a17caf9c4c';

const HOME = process.env['HOME'] ?? '/root';
const STATE_FILE = path.join(
  HOME, '.paperclip', 'instances', 'default', 'companies', COMPANY_ID, 'knowledge', 'blockers-state.json',
);

const CONCURRENCY = 5;

// ─────────────────────────────────────────────────────────────────────────────
// CLI flags
// ─────────────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const DRY_RUN = args.includes('--dry-run');
const DEBUG = args.includes('--debug');
// --once is a no-op (runner always fires once)

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

interface BlockersState {
  lastRunAt: string;
  lastHash: string;
  lastDigestCommentId: string | null;
}

function loadState(): BlockersState {
  if (!fs.existsSync(STATE_FILE)) {
    return { lastRunAt: new Date(0).toISOString(), lastHash: '', lastDigestCommentId: null };
  }
  return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')) as BlockersState;
}

function saveState(state: BlockersState): void {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
}

// ─────────────────────────────────────────────────────────────────────────────
// API helpers
// ─────────────────────────────────────────────────────────────────────────────

function headers(): Record<string, string> {
  return { Authorization: `Bearer ${API_KEY}`, 'Content-Type': 'application/json' };
}

async function listBlockedIssues(): Promise<IssueDetail[]> {
  const url = `${API_URL}/api/companies/${COMPANY_ID}/issues?status=blocked`;
  const res = await fetch(url, { headers: headers() });
  if (!res.ok) throw new Error(`listBlockedIssues: HTTP ${res.status}`);
  const data = await res.json();
  const arr: unknown[] = Array.isArray(data)
    ? data
    : ((data as { issues?: unknown[]; data?: unknown[] }).issues ??
       (data as { data?: unknown[] }).data ?? []);
  return arr as IssueDetail[];
}

async function getIssueDetail(id: string): Promise<IssueDetail> {
  const res = await fetch(`${API_URL}/api/issues/${id}`, { headers: headers() });
  if (!res.ok) throw new Error(`getIssueDetail(${id}): HTTP ${res.status}`);
  return res.json() as Promise<IssueDetail>;
}

async function getInteractions(id: string): Promise<Interaction[]> {
  const res = await fetch(`${API_URL}/api/issues/${id}/interactions`, { headers: headers() });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? (data as Interaction[]) : [];
}

async function postComment(issueId: string, body: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/issues/${issueId}/comments`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ body }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`postComment: HTTP ${res.status} — ${text.slice(0, 200)}`);
  }
  const data = await res.json() as { id: string };
  return data.id;
}

// ─────────────────────────────────────────────────────────────────────────────
// Concurrency limiter (avoid installing p-limit as a prod dep)
// ─────────────────────────────────────────────────────────────────────────────

async function runConcurrently<T, R>(
  items: T[],
  fn: (item: T) => Promise<R>,
  limit: number,
): Promise<(R | null)[]> {
  const results: (R | null)[] = new Array(items.length).fill(null);
  const queue = [...items.entries()]; // [index, item][]

  async function worker(): Promise<void> {
    while (queue.length > 0) {
      const entry = queue.shift();
      if (!entry) break;
      const [i, item] = entry;
      try {
        results[i] = await fn(item);
      } catch (err) {
        console.error(`  [blockers] error enriching ${(item as { identifier?: string }).identifier ?? i}: ${err}`);
        results[i] = null;
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

// ─────────────────────────────────────────────────────────────────────────────
// Enrich a candidate (detail + interactions)
// ─────────────────────────────────────────────────────────────────────────────

interface Enriched {
  detail: IssueDetail;
  interactions: Interaction[];
}

async function enrich(candidate: IssueDetail): Promise<Enriched> {
  const [detail, interactions] = await Promise.all([
    getIssueDetail(candidate.id),
    getInteractions(candidate.id),
  ]);
  return { detail, interactions };
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
  const state = loadState();

  console.log('[blockers] gathering candidates…');
  const candidates = await listBlockedIssues();
  console.log(`[blockers] ${candidates.length} blocked issues to evaluate`);

  console.log(`[blockers] enriching (concurrency=${CONCURRENCY})…`);
  const enrichedResults = await runConcurrently(candidates, enrich, CONCURRENCY);

  const boardItems: ClassifiedIssue[] = [];
  const agentItems: ClassifiedIssue[] = [];
  const debugDump: unknown[] = [];

  for (let i = 0; i < candidates.length; i++) {
    const e = enrichedResults[i];
    if (!e) continue; // enrichment failed — skip

    const { detail, interactions } = e;
    const result = classify(candidates[i]!, detail, interactions);
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

    if (DEBUG) {
      debugDump.push({
        identifier: detail.identifier,
        title: detail.title,
        priority: detail.priority,
        classification: result.classification,
        category: result.category ?? null,
        matchedRule: result.matchedRule ?? null,
        stale: result.stale ?? false,
        blastRadius,
        pendingInteractions: interactions.filter((i) => i.status === 'pending').length,
      });
    }
  }

  const ranked = rank(boardItems);
  const hash = contentHash(ranked);

  console.log(`[blockers] board=${ranked.length} agent=${agentItems.length} hash=${hash.slice(0, 16)}…`);

  if (DEBUG) {
    console.log('\n[blockers] === CLASSIFICATION DUMP ===');
    console.log(JSON.stringify(debugDump, null, 2));
    console.log('[blockers] === END DUMP ===\n');
  }

  let body: string;
  let isDedup = false;

  if (hash === state.lastHash && state.lastDigestCommentId) {
    isDedup = true;
    body = `*No change since ${state.lastRunAt} — ${ranked.length} board-resolvable blocker(s) still open. Prior digest: comment \`${state.lastDigestCommentId}\`.*`;
    console.log('[blockers] hash unchanged — posting dedup line');
  } else {
    body = renderDigest(ranked, agentItems, { runAt, priorRunAt: state.lastRunAt || null });
    console.log('[blockers] new or changed digest — rendering full report');
  }

  console.log('\n[blockers] === DIGEST ===');
  console.log(body);
  console.log('[blockers] === END DIGEST ===\n');

  if (DRY_RUN) {
    console.log('[blockers] --dry-run: skipping post');
    return;
  }

  console.log(`[blockers] posting to issue ${PUBLISH_ISSUE_ID}…`);
  const commentId = await postComment(PUBLISH_ISSUE_ID, body);
  console.log(`[blockers] posted comment ${commentId}`);

  saveState({
    lastRunAt: runAt,
    lastHash: isDedup ? state.lastHash : hash,
    lastDigestCommentId: isDedup ? state.lastDigestCommentId : commentId,
  });
  console.log('[blockers] state saved');
}

main().catch((err) => {
  console.error('[blockers] fatal:', err);
  process.exit(1);
});
