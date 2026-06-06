/**
 * Pure classification, ranking, rendering, and hashing functions for the
 * unified Ticket Health routine (SAG-2931).
 *
 * No I/O here — all functions operate on plain data so they are unit-testable
 * without network or filesystem mocking.
 */

import crypto from 'node:crypto';
import { type ClassifiedIssue, type Interaction, renderDigest } from './blockers.js';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type IssueStatus = 'todo' | 'in_progress' | 'in_review' | 'blocked';

export type BlockedSubclass =
  | 'false_blocked'
  | 'cancelled_blocker'
  | 'free_text_blocked'
  | 'genuinely_blocked';

export type EscalationStep = 0 | 1 | 2 | 3;

export interface StalenessResult {
  status: IssueStatus;
  idleHours: number;
  slaHours: number;
  breached: boolean;
  multiplier: number;
}

export interface StaleIssue {
  status: string;
  lastActivityAt?: string | null;
  updatedAt?: string | null;
  blockedBy?: Array<{
    status: string;
    terminalBlockers?: Array<{ status: string }> | null;
  }> | null;
  assigneeAgentId?: string | null;
  assigneeUserId?: string | null;
}

export interface HealthItem {
  id: string;
  identifier: string;
  title: string;
  priority: string;
  status: IssueStatus;
  idleHours: number;
  slaHours: number;
  breached: boolean;
  multiplier: number;
  blockedSubclass?: BlockedSubclass;
  isWaitingOnBoard: boolean;
  escalationStep: EscalationStep;
  assigneeAgentId: string | null;
  assigneeUserId?: string | null;
  createdAt: string;
}

export interface EscalationEmit {
  issueId: string;
  identifier: string;
  title: string;
  step: EscalationStep;
  targetAgentId: string;
  commentBody: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Constants (board-tunable named values)
// ─────────────────────────────────────────────────────────────────────────────

export const SLA_HOURS: Record<IssueStatus, number> = {
  in_progress: 24,
  todo: 48,
  in_review: 48,
  blocked: 24,
};

export const CEO_AGENT_ID = 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89';

export const BOARD_AGENT_IDS = new Set([CEO_AGENT_ID]);

export const MAX_MENTIONS = 10;

// ─────────────────────────────────────────────────────────────────────────────
// Pure classification functions
// ─────────────────────────────────────────────────────────────────────────────

/** Classify staleness for one issue. Idle = time since lastActivityAt (fall back to updatedAt). */
export function classifyStaleness(
  issue: Pick<StaleIssue, 'status' | 'lastActivityAt' | 'updatedAt'>,
  nowMs: number,
): StalenessResult {
  const status = issue.status as IssueStatus;
  const slaHours = SLA_HOURS[status] ?? 48;

  const activityTs = issue.lastActivityAt ?? issue.updatedAt ?? null;
  const idleMs = activityTs != null ? nowMs - new Date(activityTs).getTime() : nowMs;
  const idleHours = Math.max(0, idleMs / 3_600_000);

  const multiplier = slaHours > 0 ? idleHours / slaHours : 0;
  const breached = idleHours >= slaHours;

  return { status, idleHours, slaHours, breached, multiplier };
}

/** Map idle/SLA ratio to escalation step 0–3. Step 3 = digest-only, no @-mention. */
export function escalationStep(idleHours: number, slaHours: number): EscalationStep {
  if (slaHours <= 0) return 0;
  const ratio = idleHours / slaHours;
  if (ratio >= 3) return 3;
  if (ratio >= 2) return 2;
  if (ratio >= 1) return 1;
  return 0;
}

/**
 * Sub-classify a blocked issue.
 * Priority order: free_text_blocked → cancelled_blocker → false_blocked → genuinely_blocked
 */
export function classifyBlockedSubtype(
  issue: Pick<StaleIssue, 'blockedBy'>,
): BlockedSubclass {
  const blockedBy = issue.blockedBy ?? [];

  if (blockedBy.length === 0) return 'free_text_blocked';

  const terminals = blockedBy.flatMap((bb) => bb.terminalBlockers ?? []).filter(Boolean);

  if (terminals.length === 0) {
    // Fall back to direct blockedBy statuses when terminal data is absent
    if (blockedBy.some((bb) => bb.status === 'cancelled')) return 'cancelled_blocker';
    if (blockedBy.every((bb) => bb.status === 'done')) return 'false_blocked';
    return 'genuinely_blocked';
  }

  if (terminals.some((t) => t.status === 'cancelled')) return 'cancelled_blocker';
  if (terminals.every((t) => t.status === 'done')) return 'false_blocked';
  return 'genuinely_blocked';
}

/**
 * Returns true when an in_review ticket is parked on a board/human/CEO decision.
 * These go to the "Waiting on board" section — no per-ticket @-mention ping.
 */
export function isWaitingOnBoard(
  issue: Pick<StaleIssue, 'assigneeAgentId' | 'assigneeUserId'>,
  interactions: Interaction[],
): boolean {
  if (interactions.some((i) => i.kind === 'request_confirmation' && i.status === 'pending')) {
    return true;
  }
  if (issue.assigneeAgentId && BOARD_AGENT_IDS.has(issue.assigneeAgentId)) return true;
  // Human user assignee (no agent id, but has a user id)
  if (!issue.assigneeAgentId && issue.assigneeUserId) return true;
  return false;
}

/**
 * Whether to emit a new @-mention for this issue.
 * True only when crossing a NEW step boundary and not yet at step 3 (digest-only).
 */
export function shouldEmitMention(
  currentStep: EscalationStep,
  prevStep: number,
): boolean {
  return currentStep > prevStep && currentStep > 0 && currentStep < 3;
}

// ─────────────────────────────────────────────────────────────────────────────
// Content hash for dedup
// ─────────────────────────────────────────────────────────────────────────────

/** Stable SHA-256 hash of breached issues + board blocker ids. Changes when staleness or blockers change. */
export function ticketHealthHash(items: HealthItem[], boardBlockerIds: string[]): string {
  const breachedKeys = items
    .filter((i) => i.breached)
    .map((i) => `${i.id}:${i.status}:${i.blockedSubclass ?? ''}:${i.isWaitingOnBoard}`)
    .sort();
  const boardKeys = [...boardBlockerIds].sort().map((id) => `board:${id}`);
  const normalized = [...breachedKeys, ...boardKeys].join(',');
  return crypto.createHash('sha256').update(normalized).digest('hex');
}

// ─────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────

function idleLabel(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

export interface TicketHealthDigestOpts {
  runAt: string;
  priorRunAt?: string | null;
  totalSwept: number;
  escalationsEmitted: EscalationEmit[];
  boardClassified: ClassifiedIssue[];
  boardAgentClassified: ClassifiedIssue[];
}

/** Render the full unified Ticket Health markdown digest. */
export function renderTicketHealthDigest(
  items: HealthItem[],
  opts: TicketHealthDigestOpts,
): string {
  const byStatus: Record<IssueStatus, { total: number; breached: number }> = {
    in_progress: { total: 0, breached: 0 },
    todo: { total: 0, breached: 0 },
    in_review: { total: 0, breached: 0 },
    blocked: { total: 0, breached: 0 },
  };

  const falseBlocked: HealthItem[] = [];
  const cancelledBlocker: HealthItem[] = [];
  const freeTextBlocked: HealthItem[] = [];
  const waitingOnBoard: HealthItem[] = [];
  const allBreached: HealthItem[] = [];

  for (const item of items) {
    const bucket = byStatus[item.status];
    if (bucket) {
      bucket.total++;
      if (item.breached) {
        bucket.breached++;
        allBreached.push(item);
      }
    }
    if (item.blockedSubclass === 'false_blocked') falseBlocked.push(item);
    if (item.blockedSubclass === 'cancelled_blocker') cancelledBlocker.push(item);
    if (item.blockedSubclass === 'free_text_blocked') freeTextBlocked.push(item);
    if (item.isWaitingOnBoard) waitingOnBoard.push(item);
  }

  allBreached.sort((a, b) => b.idleHours - a.idleHours);
  const topStalest = allBreached.slice(0, 10);
  const totalBreached = allBreached.length;

  const lines: string[] = [
    `## Ticket Health Digest`,
    ``,
    `**Run:** ${opts.runAt} | **Swept:** ${opts.totalSwept} open issues | **SLA breached:** ${totalBreached}`,
    ``,
    `### Status breakdown`,
    `| Status | Total | SLA breached |`,
    `|--------|-------|-------------|`,
    ...(['in_progress', 'todo', 'in_review', 'blocked'] as IssueStatus[]).map((s) => {
      const b = byStatus[s];
      return `| \`${s}\` | ${b.total} | ${b.breached} |`;
    }),
    ``,
  ];

  // Waiting on board section
  if (waitingOnBoard.length > 0) {
    lines.push(`## Waiting on board (${waitingOnBoard.length})`);
    lines.push(`*These \`in_review\` tickets are parked on a board/human/CEO decision — no agent ping sent.*`);
    lines.push(``);
    for (const item of waitingOnBoard) {
      lines.push(
        `- [${item.identifier}](/SAG/issues/${item.identifier}) — ${item.title} (\`${item.priority}\`, idle ${idleLabel(item.idleHours)})`,
      );
    }
    lines.push(``);
  }

  // False-blocked
  if (falseBlocked.length > 0) {
    lines.push(`### False-blocked tickets — resume nudge needed (${falseBlocked.length})`);
    lines.push(`*All blockers are \`done\` — assignee should clear the stale link and resume work.*`);
    lines.push(``);
    for (const item of falseBlocked) {
      const who = item.assigneeAgentId
        ? `agent \`${item.assigneeAgentId.slice(0, 8)}…\``
        : 'unassigned';
      lines.push(
        `- [${item.identifier}](/SAG/issues/${item.identifier}) — ${item.title} (${who}, idle ${idleLabel(item.idleHours)})`,
      );
    }
    lines.push(``);
  }

  // Cancelled-blocker
  if (cancelledBlocker.length > 0) {
    lines.push(`### Cancelled-blocker tickets (${cancelledBlocker.length})`);
    lines.push(`*A blocking issue was cancelled — assignee should decide whether to unblock or close.*`);
    lines.push(``);
    for (const item of cancelledBlocker) {
      const who = item.assigneeAgentId
        ? `agent \`${item.assigneeAgentId.slice(0, 8)}…\``
        : 'unassigned';
      lines.push(
        `- [${item.identifier}](/SAG/issues/${item.identifier}) — ${item.title} (${who}, idle ${idleLabel(item.idleHours)})`,
      );
    }
    lines.push(``);
  }

  // No-blocker blocked — needs owner or cancel (SAG-3082)
  if (freeTextBlocked.length > 0) {
    lines.push(`### No-blocker blocked — needs owner or cancel (${freeTextBlocked.length})`);
    lines.push(
      `*These tickets are \`blocked\` but have no \`blockedBy\` dependency recorded. ` +
      `The blocker link may have been dropped by the platform or the ticket was set to blocked via free text. ` +
      `Board should add the correct blocker, assign an owner, or cancel.*`,
    );
    lines.push(``);
    for (const item of freeTextBlocked) {
      const who = item.assigneeAgentId
        ? `agent \`${item.assigneeAgentId.slice(0, 8)}…\``
        : 'unassigned';
      lines.push(
        `- [${item.identifier}](/SAG/issues/${item.identifier}) — ${item.title.slice(0, 60)} (${who}, idle ${idleLabel(item.idleHours)})`,
      );
    }
    lines.push(``);
  }

  // Escalations issued this run
  if (opts.escalationsEmitted.length > 0) {
    lines.push(`### Escalations issued this run (${opts.escalationsEmitted.length} of max ${MAX_MENTIONS})`);
    lines.push(``);
    for (const e of opts.escalationsEmitted) {
      const stepLabel =
        e.step === 1 ? 'Step 1 → assignee nudge' : 'Step 2 → manager escalation';
      lines.push(
        `- [${e.identifier}](/SAG/issues/${e.identifier}) — ${e.title.slice(0, 60)} (${stepLabel})`,
      );
    }
    lines.push(``);
  } else {
    lines.push(`*No new escalation @-mentions this run.*`);
    lines.push(``);
  }

  // Top stalest issues
  if (topStalest.length > 0) {
    lines.push(`### Top ${topStalest.length} stalest issues`);
    lines.push(``);
    for (const item of topStalest) {
      const mult = item.multiplier.toFixed(1);
      const who = item.assigneeAgentId
        ? `agent \`${item.assigneeAgentId.slice(0, 8)}…\``
        : 'unassigned';
      lines.push(
        `- [${item.identifier}](/SAG/issues/${item.identifier}) — ${item.title.slice(0, 60)} (\`${item.status}\`, ${idleLabel(item.idleHours)} idle, ${mult}× SLA, ${who})`,
      );
    }
    lines.push(``);
  }

  // Board-resolvable blockers section (reused from blockers.ts)
  lines.push(`---`);
  lines.push(``);
  const blockerSection = renderDigest(opts.boardClassified, opts.boardAgentClassified, {
    runAt: opts.runAt,
    priorRunAt: opts.priorRunAt,
  });
  lines.push(blockerSection);
  lines.push(``);
  lines.push(`---`);
  lines.push(
    `*Generated by ticket-health-run.ts ([SAG-2931](/SAG/issues/SAG-2931)). Runner: Coder (Sonnet 4.6) \`3ab7fa06\`.*`,
  );

  return lines.join('\n');
}
