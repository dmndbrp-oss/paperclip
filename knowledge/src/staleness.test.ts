import { describe, it, expect } from 'vitest';
import {
  classifyStaleness,
  escalationStep,
  classifyBlockedSubtype,
  classifyNoFirstClassBlocker,
  isWaitingOnBoard,
  shouldEmitMention,
  CEO_AGENT_ID,
  SLA_HOURS,
  MAX_MENTIONS,
} from './staleness.js';

const NOW = Date.now();

function nowMinus(hours: number): string {
  return new Date(NOW - hours * 3_600_000).toISOString();
}

// ─────────────────────────────────────────────────────────────────────────────
// classifyStaleness
// ─────────────────────────────────────────────────────────────────────────────

describe('classifyStaleness', () => {
  it('in_progress SLA is 24h', () => {
    expect(SLA_HOURS['in_progress']).toBe(24);
  });

  it('todo SLA is 48h', () => {
    expect(SLA_HOURS['todo']).toBe(48);
  });

  it('in_review SLA is 48h', () => {
    expect(SLA_HOURS['in_review']).toBe(48);
  });

  it('blocked SLA is 24h', () => {
    expect(SLA_HOURS['blocked']).toBe(24);
  });

  it('in_progress: idle 25h → breached, multiplier ~1.04', () => {
    const result = classifyStaleness({ status: 'in_progress', lastActivityAt: nowMinus(25) }, NOW);
    expect(result.status).toBe('in_progress');
    expect(result.slaHours).toBe(24);
    expect(result.breached).toBe(true);
    expect(result.idleHours).toBeCloseTo(25, 0);
    expect(result.multiplier).toBeCloseTo(25 / 24, 2);
  });

  it('in_progress: idle 23h → not breached', () => {
    const result = classifyStaleness({ status: 'in_progress', lastActivityAt: nowMinus(23) }, NOW);
    expect(result.breached).toBe(false);
    expect(result.multiplier).toBeLessThan(1);
  });

  it('todo: idle 47h → not breached', () => {
    const result = classifyStaleness({ status: 'todo', lastActivityAt: nowMinus(47) }, NOW);
    expect(result.slaHours).toBe(48);
    expect(result.breached).toBe(false);
  });

  it('todo: idle 49h → breached', () => {
    const result = classifyStaleness({ status: 'todo', lastActivityAt: nowMinus(49) }, NOW);
    expect(result.breached).toBe(true);
  });

  it('blocked: exactly 24h idle → breached (boundary inclusive)', () => {
    const result = classifyStaleness({ status: 'blocked', lastActivityAt: nowMinus(24) }, NOW);
    expect(result.slaHours).toBe(24);
    expect(result.breached).toBe(true);
  });

  it('blocked: 23.9h idle → not breached', () => {
    const result = classifyStaleness({ status: 'blocked', lastActivityAt: nowMinus(23.9) }, NOW);
    expect(result.breached).toBe(false);
  });

  it('falls back to updatedAt when lastActivityAt is null', () => {
    const result = classifyStaleness(
      { status: 'in_progress', lastActivityAt: null, updatedAt: nowMinus(30) },
      NOW,
    );
    expect(result.idleHours).toBeCloseTo(30, 0);
    expect(result.breached).toBe(true);
  });

  it('falls back to updatedAt when lastActivityAt is absent', () => {
    const result = classifyStaleness({ status: 'todo', updatedAt: nowMinus(50) }, NOW);
    expect(result.idleHours).toBeCloseTo(50, 0);
    expect(result.breached).toBe(true);
  });

  it('no timestamp at all → idleHours equals nowMs in hours (always breached)', () => {
    const result = classifyStaleness({ status: 'in_progress' }, NOW);
    expect(result.idleHours).toBeGreaterThan(0);
    expect(result.breached).toBe(true);
  });

  it('multiplier at exactly 2× SLA', () => {
    const result = classifyStaleness({ status: 'blocked', lastActivityAt: nowMinus(48) }, NOW);
    expect(result.multiplier).toBeCloseTo(2, 1);
    expect(result.breached).toBe(true);
  });

  it('multiplier at exactly 3× SLA', () => {
    const result = classifyStaleness({ status: 'blocked', lastActivityAt: nowMinus(72) }, NOW);
    expect(result.multiplier).toBeCloseTo(3, 1);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// escalationStep — 1×/2×/3× SLA boundaries
// ─────────────────────────────────────────────────────────────────────────────

describe('escalationStep', () => {
  const sla = 24;

  it('idle 0h → step 0', () => {
    expect(escalationStep(0, sla)).toBe(0);
  });

  it('idle 23.9h (< 1× SLA) → step 0', () => {
    expect(escalationStep(23.9, sla)).toBe(0);
  });

  it('idle 24h (= 1× SLA) → step 1', () => {
    expect(escalationStep(24, sla)).toBe(1);
  });

  it('idle 47.9h (< 2× SLA) → step 1', () => {
    expect(escalationStep(47.9, sla)).toBe(1);
  });

  it('idle 48h (= 2× SLA) → step 2', () => {
    expect(escalationStep(48, sla)).toBe(2);
  });

  it('idle 71.9h (< 3× SLA) → step 2', () => {
    expect(escalationStep(71.9, sla)).toBe(2);
  });

  it('idle 72h (= 3× SLA) → step 3 (digest only)', () => {
    expect(escalationStep(72, sla)).toBe(3);
  });

  it('idle 200h (>> 3× SLA) → step 3', () => {
    expect(escalationStep(200, sla)).toBe(3);
  });

  it('todo SLA 48h boundaries', () => {
    expect(escalationStep(47, 48)).toBe(0);
    expect(escalationStep(48, 48)).toBe(1);
    expect(escalationStep(96, 48)).toBe(2);
    expect(escalationStep(144, 48)).toBe(3);
  });

  it('sla=0 → step 0 (guard)', () => {
    expect(escalationStep(100, 0)).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// classifyBlockedSubtype
// ─────────────────────────────────────────────────────────────────────────────

describe('classifyBlockedSubtype', () => {
  it('empty blockedBy → free_text_blocked', () => {
    expect(classifyBlockedSubtype({ blockedBy: [] })).toBe('free_text_blocked');
  });

  it('absent blockedBy → free_text_blocked', () => {
    expect(classifyBlockedSubtype({})).toBe('free_text_blocked');
  });

  it('null blockedBy → free_text_blocked', () => {
    expect(classifyBlockedSubtype({ blockedBy: null })).toBe('free_text_blocked');
  });

  it('all terminal blockers done → false_blocked', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'done', terminalBlockers: [{ status: 'done' }] },
          { status: 'done', terminalBlockers: [{ status: 'done' }] },
        ],
      }),
    ).toBe('false_blocked');
  });

  it('any terminal blocker cancelled → cancelled_blocker', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'cancelled', terminalBlockers: [{ status: 'cancelled' }] },
          { status: 'done', terminalBlockers: [{ status: 'done' }] },
        ],
      }),
    ).toBe('cancelled_blocker');
  });

  it('mix of done and cancelled terminal → cancelled_blocker (cancelled takes priority)', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'done', terminalBlockers: [{ status: 'done' }, { status: 'cancelled' }] },
        ],
      }),
    ).toBe('cancelled_blocker');
  });

  it('all cancelled → cancelled_blocker', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'cancelled', terminalBlockers: [{ status: 'cancelled' }] },
          { status: 'cancelled', terminalBlockers: [{ status: 'cancelled' }] },
        ],
      }),
    ).toBe('cancelled_blocker');
  });

  it('blocker in_progress → genuinely_blocked', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [{ status: 'in_progress', terminalBlockers: [{ status: 'in_progress' }] }],
      }),
    ).toBe('genuinely_blocked');
  });

  it('mix done + in_progress → genuinely_blocked', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'done', terminalBlockers: [{ status: 'done' }] },
          { status: 'in_progress', terminalBlockers: [{ status: 'in_progress' }] },
        ],
      }),
    ).toBe('genuinely_blocked');
  });

  it('no terminal blocker data — direct status done → false_blocked', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [
          { status: 'done', terminalBlockers: [] },
          { status: 'done', terminalBlockers: [] },
        ],
      }),
    ).toBe('false_blocked');
  });

  it('no terminal data — direct status cancelled → cancelled_blocker', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [{ status: 'cancelled', terminalBlockers: [] }],
      }),
    ).toBe('cancelled_blocker');
  });

  it('no terminal data — direct status in_progress → genuinely_blocked', () => {
    expect(
      classifyBlockedSubtype({
        blockedBy: [{ status: 'in_progress', terminalBlockers: [] }],
      }),
    ).toBe('genuinely_blocked');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// isWaitingOnBoard — board/CEO reviewer → Waiting-on-board, NOT pinged
// ─────────────────────────────────────────────────────────────────────────────

describe('isWaitingOnBoard', () => {
  it('pending request_confirmation → waiting on board', () => {
    expect(
      isWaitingOnBoard({ assigneeAgentId: 'some-agent', assigneeUserId: null }, [
        { kind: 'request_confirmation', status: 'pending' },
      ]),
    ).toBe(true);
  });

  it('CEO as assignee → waiting on board', () => {
    expect(
      isWaitingOnBoard({ assigneeAgentId: CEO_AGENT_ID, assigneeUserId: null }, []),
    ).toBe(true);
  });

  it('human user assignee (userId set, no agentId) → waiting on board', () => {
    expect(
      isWaitingOnBoard({ assigneeAgentId: null, assigneeUserId: 'user-abc-123' }, []),
    ).toBe(true);
  });

  it('regular agent assignee, no pending interaction → not waiting on board', () => {
    expect(
      isWaitingOnBoard(
        { assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d', assigneeUserId: null },
        [],
      ),
    ).toBe(false);
  });

  it('accepted confirmation → not waiting on board', () => {
    expect(
      isWaitingOnBoard({ assigneeAgentId: null, assigneeUserId: null }, [
        { kind: 'request_confirmation', status: 'accepted' },
      ]),
    ).toBe(false);
  });

  it('unassigned (no agent, no user) with no pending interaction → not waiting on board', () => {
    expect(
      isWaitingOnBoard({ assigneeAgentId: null, assigneeUserId: null }, []),
    ).toBe(false);
  });

  it('unassigned (undefined fields) with pending interaction → waiting on board', () => {
    expect(
      isWaitingOnBoard({}, [{ kind: 'request_confirmation', status: 'pending' }]),
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// shouldEmitMention — idempotency
// ─────────────────────────────────────────────────────────────────────────────

describe('shouldEmitMention — idempotency', () => {
  it('step 1, prev 0 → emit (first crossing)', () => {
    expect(shouldEmitMention(1, 0)).toBe(true);
  });

  it('step 1, prev 1 → no emit (same step, idempotent)', () => {
    expect(shouldEmitMention(1, 1)).toBe(false);
  });

  it('step 2, prev 1 → emit (new step)', () => {
    expect(shouldEmitMention(2, 1)).toBe(true);
  });

  it('step 2, prev 2 → no emit (idempotent)', () => {
    expect(shouldEmitMention(2, 2)).toBe(false);
  });

  it('step 3 → never emit (digest-only cap)', () => {
    expect(shouldEmitMention(3, 0)).toBe(false);
    expect(shouldEmitMention(3, 2)).toBe(false);
  });

  it('step 0 → never emit', () => {
    expect(shouldEmitMention(0, 0)).toBe(false);
  });

  it('idempotency simulation: same issue processed twice → mention only on first crossing', () => {
    let emitCount = 0;
    let storedStep = 0;

    // Run 1: idle = 1× SLA → step 1
    const step1 = 1 as const;
    if (shouldEmitMention(step1, storedStep)) {
      emitCount++;
      storedStep = step1;
    }
    expect(emitCount).toBe(1);

    // Run 2: same idle, same step → no new emit
    if (shouldEmitMention(step1, storedStep)) {
      emitCount++;
    }
    expect(emitCount).toBe(1);

    // Run 3: idle crosses 2× SLA → step 2 → new emit
    const step2 = 2 as const;
    if (shouldEmitMention(step2, storedStep)) {
      emitCount++;
      storedStep = step2;
    }
    expect(emitCount).toBe(2);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Mention cap — >10 breaches → only 10 mentions emitted
// ─────────────────────────────────────────────────────────────────────────────

describe('mention cap', () => {
  it('MAX_MENTIONS constant is 10', () => {
    expect(MAX_MENTIONS).toBe(10);
  });

  it('>10 breached issues → only 10 @-mentions emitted (runner-pattern simulation)', () => {
    let mentionCount = 0;
    const emittedIndexes: number[] = [];

    for (let i = 0; i < 15; i++) {
      // Each issue: prev step 0, current step 1 → shouldEmitMention = true
      if (shouldEmitMention(1, 0) && mentionCount < MAX_MENTIONS) {
        mentionCount++;
        emittedIndexes.push(i);
      }
    }

    expect(emittedIndexes.length).toBe(10);
    expect(mentionCount).toBe(MAX_MENTIONS);
  });

  it('exactly 10 breaches → all 10 emitted', () => {
    let mentionCount = 0;
    for (let i = 0; i < 10; i++) {
      if (shouldEmitMention(1, 0) && mentionCount < MAX_MENTIONS) {
        mentionCount++;
      }
    }
    expect(mentionCount).toBe(10);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// classifyNoFirstClassBlocker — SAG-3082
// ─────────────────────────────────────────────────────────────────────────────

describe('classifyNoFirstClassBlocker', () => {
  // (a) blocked + empty blockedByIssueIds + active assignee → auto_resume
  it('blocked + empty ids + agent assignee → auto_resume', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: [],
        assigneeAgentId: 'agent-abc',
        assigneeUserId: null,
      }),
    ).toBe('auto_resume');
  });

  it('blocked + null ids + agent assignee → auto_resume', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: null,
        assigneeAgentId: 'agent-abc',
      }),
    ).toBe('auto_resume');
  });

  it('blocked + absent ids + agent assignee → auto_resume', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        assigneeAgentId: 'agent-abc',
      }),
    ).toBe('auto_resume');
  });

  it('blocked + empty ids + human user assignee → auto_resume', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: [],
        assigneeAgentId: null,
        assigneeUserId: 'user-xyz',
      }),
    ).toBe('auto_resume');
  });

  // (b) blocked + empty blockedByIssueIds + no assignee → digest_only
  it('blocked + empty ids + no assignee → digest_only', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: [],
        assigneeAgentId: null,
        assigneeUserId: null,
      }),
    ).toBe('digest_only');
  });

  it('blocked + empty ids + no assignee fields → digest_only', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: [],
      }),
    ).toBe('digest_only');
  });

  it('blocked + absent ids + no assignee → digest_only', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
      }),
    ).toBe('digest_only');
  });

  // (c) blocked WITH real blockers → has_blocker (untouched)
  it('blocked + one blockedByIssueId + agent assignee → has_blocker', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: ['issue-id-123'],
        assigneeAgentId: 'agent-abc',
      }),
    ).toBe('has_blocker');
  });

  it('blocked + multiple blockedByIssueIds + no assignee → has_blocker', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'blocked',
        blockedByIssueIds: ['issue-a', 'issue-b'],
        assigneeAgentId: null,
        assigneeUserId: null,
      }),
    ).toBe('has_blocker');
  });

  // Status guard — non-blocked issues must never be touched
  it('in_progress + empty ids + agent assignee → has_blocker (status guard)', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'in_progress',
        blockedByIssueIds: [],
        assigneeAgentId: 'agent-abc',
      }),
    ).toBe('has_blocker');
  });

  it('todo + absent ids → has_blocker (status guard)', () => {
    expect(
      classifyNoFirstClassBlocker({
        status: 'todo',
        assigneeAgentId: 'agent-abc',
      }),
    ).toBe('has_blocker');
  });
});
