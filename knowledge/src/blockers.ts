/**
 * Pure classification, ranking, rendering, and hashing functions for the
 * daily board-resolvable blockers routine (SAG-2572).
 *
 * No I/O here — all functions operate on plain data so they are unit-testable
 * without network or filesystem mocking.
 */

import crypto from 'node:crypto';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type Priority = 'critical' | 'high' | 'medium' | 'low';

export type BlockerAttentionState = 'none' | 'covered' | 'needs_attention' | 'stalled';

export interface BlockerCandidate {
  id: string;
  identifier: string;
  title: string;
  description?: string | null;
  priority: Priority;
  createdAt: string;
  assigneeAgentId: string | null;
  blockerAttention: {
    state: BlockerAttentionState;
    unresolvedBlockerCount: number;
    sampleBlockerIdentifier: string | null;
  };
}

export interface TerminalBlocker {
  identifier: string;
  status: string;
  assigneeAgentId: string | null;
  title?: string | null;
}

export interface BlockedByEntry {
  identifier: string;
  status: string;
  assigneeAgentId: string | null;
  terminalBlockers: TerminalBlocker[];
}

export interface BlocksEntry {
  identifier: string;
  status: string;
  title?: string | null;
}

export interface IssueDetail extends BlockerCandidate {
  blocks: BlocksEntry[];
  blockedBy: BlockedByEntry[];
}

export interface Interaction {
  kind: string;
  status: string;
}

export type Category =
  | 'pending_confirmation'
  | 'routine_cron'
  | 'oauth_reconnect'
  | 'credential_purchase'
  | 'infra_unreachable'
  | 'hire_or_model_signoff';

export type Classification = 'board' | 'agent';

export interface ClassifiedIssue {
  detail: IssueDetail;
  classification: Classification;
  category?: Category;
  matchedRule?: number;
  blastRadius: number;
  stale?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

const PRIORITY_RANK: Record<Priority, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function buildTextCorpus(candidate: BlockerCandidate, detail: IssueDetail): string {
  const parts: string[] = [
    candidate.title,
    candidate.description ?? '',
  ];
  for (const bb of detail.blockedBy ?? []) {
    for (const tb of bb.terminalBlockers ?? []) {
      if (tb) parts.push(tb.title ?? '', tb.identifier ?? '');
    }
  }
  return parts.join(' ').toLowerCase();
}

function hasPendingConfirmation(interactions: Interaction[]): boolean {
  return interactions.some(
    (i) => i.kind === 'request_confirmation' && i.status === 'pending',
  );
}

function areAllTerminalBlockersStale(detail: IssueDetail): boolean {
  const terminals = detail.blockedBy
    .flatMap((bb) => bb.terminalBlockers ?? [])
    .filter(Boolean);
  if (terminals.length === 0) return false;
  return terminals.every((tb) => tb.status === 'cancelled' || tb.status === 'done');
}

function matchBoardRule(
  corpus: string,
  pendingConfirm: boolean,
): { category: Category; matchedRule: number } | null {
  if (
    pendingConfirm ||
    /request_confirmation|\/accept|board must accept|pending board|awaiting board|confirmation .* pending/.test(corpus)
  ) {
    return { category: 'pending_confirmation', matchedRule: 1 };
  }

  if (
    /routine|unpause|cron|\b0 \/?[0-9*]|go-live/.test(corpus) &&
    /paused|board-only|enable/.test(corpus)
  ) {
    return { category: 'routine_cron', matchedRule: 2 };
  }

  if (/oauth|reconnect|claude\.ai|mcp .*disconnect|integration .*(disconnect|reconnect)/.test(corpus)) {
    return { category: 'oauth_reconnect', matchedRule: 3 };
  }

  if (/api[- ]?key|anthropic_api_key|provision .*(key|credential)|purchase|\bpat\b|github push cred|token .*provision/.test(corpus)) {
    return { category: 'credential_purchase', matchedRule: 4 };
  }

  if (/tailscale|tunnel|dns|provision .*environment|d365 .*provision|dynamics .*setup/.test(corpus)) {
    return { category: 'infra_unreachable', matchedRule: 5 };
  }

  if (/hire .*approval|model[- ]change|sign[- ]?off|board approval|approve .*(hire|model)/.test(corpus)) {
    return { category: 'hire_or_model_signoff', matchedRule: 6 };
  }

  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Exported pure functions
// ─────────────────────────────────────────────────────────────────────────────

/** Classify a single blocked issue as board-resolvable or agent-owned. */
export function classify(
  candidate: BlockerCandidate,
  detail: IssueDetail,
  interactions: Interaction[],
): { classification: Classification; category?: Category; matchedRule?: number; stale?: boolean } {
  const pendingConfirm = hasPendingConfirmation(interactions);

  // Pending confirmation on the issue itself → board regardless of blocker state
  if (pendingConfirm) {
    return { classification: 'board', category: 'pending_confirmation', matchedRule: 1 };
  }

  // Stale/phantom terminal blockers → agent (owner needs to clear stale link)
  if (areAllTerminalBlockersStale(detail)) {
    return { classification: 'agent', stale: true };
  }

  const corpus = buildTextCorpus(candidate, detail);
  const match = matchBoardRule(corpus, pendingConfirm);
  if (match) {
    return { classification: 'board', ...match };
  }

  return { classification: 'agent' };
}

/** Rank board-resolvable items: priority ASC, age DESC (oldest first), blast radius DESC. */
export function rank(items: ClassifiedIssue[]): ClassifiedIssue[] {
  return [...items].sort((a, b) => {
    const pa = PRIORITY_RANK[a.detail.priority] ?? 3;
    const pb = PRIORITY_RANK[b.detail.priority] ?? 3;
    if (pa !== pb) return pa - pb;

    const ta = new Date(a.detail.createdAt).getTime();
    const tb = new Date(b.detail.createdAt).getTime();
    if (ta !== tb) return ta - tb;

    return b.blastRadius - a.blastRadius;
  });
}

/** Stable SHA-256 hash of board item ids + categories. Stable across runs; changes on list change. */
export function contentHash(boardItems: ClassifiedIssue[]): string {
  const normalized = boardItems
    .map((item) => `${item.detail.id}:${item.category ?? 'none'}`)
    .sort()
    .join(',');
  return crypto.createHash('sha256').update(normalized).digest('hex');
}

// ─────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<Category, string> = {
  pending_confirmation: 'Pending board acceptance (request_confirmation)',
  routine_cron: 'Routine/cron — board action needed',
  oauth_reconnect: 'OAuth reconnect needed',
  credential_purchase: 'Credential/API key — board must provision',
  infra_unreachable: 'Infra unreachable',
  hire_or_model_signoff: 'Hire or model sign-off required',
};

function resolutionSteps(category: Category, identifier: string): string {
  switch (category) {
    case 'pending_confirmation':
      return [
        `1. Open [${identifier}](/SAG/issues/${identifier}) in the web UI.`,
        `2. Go to the Interactions panel.`,
        `3. Click **Accept** on the pending \`request_confirmation\` (agents get 403 — board-only).`,
        `4. The assignee wakes and continues automatically.`,
      ].join('\n');
    case 'routine_cron':
      return [
        `1. Web UI → **Routines**.`,
        `2. Find the routine linked to [${identifier}](/SAG/issues/${identifier}).`,
        `3. Create or unpause it with the stated cron schedule.`,
        `4. Confirm \`paused=false\`. (Routine management is board-only even for CEO.)`,
      ].join('\n');
    case 'oauth_reconnect':
      return [
        `1. Open **claude.ai → Settings → Connectors**.`,
        `2. Reconnect the named integration for [${identifier}](/SAG/issues/${identifier}).`,
        `3. Re-trigger the assignee's heartbeat.`,
        `(Locked OAuth disconnects need board action — ref [SAG-2337](/SAG/issues/SAG-2337).)`,
      ].join('\n');
    case 'credential_purchase':
      return [
        `1. Provision the named credential/key for [${identifier}](/SAG/issues/${identifier}).`,
        `2. Add it to the server \`.env\` or agent \`adapterConfig.env\`.`,
        `3. Notify the assignee to resume.`,
        `(No standalone Anthropic key — board must provision via billing account.)`,
      ].join('\n');
    case 'infra_unreachable':
      return [
        `1. Restore the named infrastructure for [${identifier}](/SAG/issues/${identifier}) (e.g. Tailscale tunnel / D365 environment).`,
        `2. Verify reachability from the agent host.`,
        `3. Re-trigger the assignee.`,
      ].join('\n');
    case 'hire_or_model_signoff':
      return [
        `1. Open [${identifier}](/SAG/issues/${identifier}).`,
        `2. Approve the hire or model-change sign-off.`,
        `3. Assignee proceeds automatically.`,
      ].join('\n');
  }
}

function ageDays(createdAt: string): number {
  return Math.floor((Date.now() - new Date(createdAt).getTime()) / 86_400_000);
}

function renderBoardItem(item: ClassifiedIssue, index: number): string {
  const { detail, category, blastRadius } = item;
  const cat = category ?? 'pending_confirmation';
  const catLabel = CATEGORY_LABELS[cat];
  const downstreamIds = detail.blocks
    .slice(0, 5)
    .map((b) => b.identifier)
    .join(', ');
  const downstreamNote =
    blastRadius > 0
      ? `${blastRadius} issue(s): ${downstreamIds}${blastRadius > 5 ? ` +${blastRadius - 5} more` : ''}`
      : 'none directly';

  return [
    `### ${index + 1}. [${detail.identifier}](/SAG/issues/${detail.identifier}) — ${detail.title}`,
    ``,
    `**Priority:** ${detail.priority} | **Category:** ${catLabel}`,
    `**Age:** ${ageDays(detail.createdAt)}d (created ${detail.createdAt.slice(0, 10)}) | **Downstream blocked:** ${downstreamNote}`,
    ``,
    `**Resolution steps:**`,
    resolutionSteps(cat, detail.identifier),
  ].join('\n');
}

function renderFyiTail(items: ClassifiedIssue[]): string {
  if (items.length === 0) return '';
  const top = items.slice(0, 5);
  const lines = top.map((item) => {
    const who = item.detail.assigneeAgentId
      ? `agent \`${item.detail.assigneeAgentId.slice(0, 8)}…\``
      : 'unassigned';
    const staleNote = item.stale ? ' *(stale blocker — agent should clear link)*' : '';
    return `- [${item.detail.identifier}](/SAG/issues/${item.detail.identifier}) — ${item.detail.title} (${item.detail.priority}, ${who})${staleNote}`;
  });
  return [
    `---`,
    ``,
    `<details>`,
    `<summary>Agent-owned blockers FYI (${Math.min(items.length, 5)} of ${items.length} shown)</summary>`,
    ``,
    ...lines,
    ``,
    `</details>`,
  ].join('\n');
}

export interface DigestRenderOpts {
  runAt: string;
  priorRunAt?: string | null;
}

/** Render the full markdown digest comment. */
export function renderDigest(
  boardItems: ClassifiedIssue[],
  agentItems: ClassifiedIssue[],
  opts: DigestRenderOpts,
): string {
  const header = [
    `## Daily Board-Resolvable Blockers Digest`,
    ``,
    `**Run:** ${opts.runAt} | **Board-resolvable:** ${boardItems.length} | **Agent-owned:** ${agentItems.length}`,
    ``,
  ].join('\n');

  if (boardItems.length === 0) {
    return (
      header +
      `*No board-resolvable blockers found this run — nothing to action.*\n\n` +
      renderFyiTail(agentItems)
    );
  }

  const itemsText = boardItems
    .map((item, i) => renderBoardItem(item, i))
    .join('\n\n---\n\n');

  const fyiSection = renderFyiTail(agentItems);

  return [
    header,
    itemsText,
    '',
    fyiSection ? fyiSection + '\n' : '',
    `---`,
    `*Generated by [SAG-2572](/SAG/issues/SAG-2572) blockers routine. Runner: Researcher \`1e0167fe\`.*`,
  ]
    .filter((s) => s !== undefined)
    .join('\n');
}
