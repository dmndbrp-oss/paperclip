import { describe, it, expect } from 'vitest';
import {
  classifyStaleness,
  escalationStep,
  classifyBlockedSubtype,
  isWaitingOnBoard,
  shouldEmitMention,
  renderTicketHealthDigest,
  CEO_AGENT_ID,
  SLA_HOURS,
  MAX_MENTIONS,
  type HealthItem,
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
// renderTicketHealthDigest — free_text_blocked section (SAG-3082)
// ─────────────────────────────────────────────────────────────────────────────

function makeHealthItem(overrides: Partial<HealthItem>): HealthItem {
  return {
    id: 'test-id',
    identifier: 'SAG-999',
    title: 'Test issue',
    priority: 'medium',
    status: 'blocked',
    idleHours: 48,
    slaHours: 24,
    breached: true,
    multiplier: 2,
    isWaitingOnBoard: false,
    escalationStep: 0,
    assigneeAgentId: null,
    createdAt: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

const EMPTY_OPTS = {
  runAt: '2026-06-06T00:00:00Z',
  totalSwept: 10,
  escalationsEmitted: [],
  boardClassified: [],
  boardAgentClassified: [],
};

describe('renderTicketHealthDigest — free_text_blocked section', () => {
  it('no free_text_blocked items → section absent', () => {
    const items: HealthItem[] = [
      makeHealthItem({ blockedSubclass: 'genuinely_blocked', assigneeAgentId: 'agent-001' }),
    ];
    const digest = renderTicketHealthDigest(items, EMPTY_OPTS);
    expect(digest).not.toContain('No-blocker blocked');
    expect(digest).not.toContain('no blockedBy dependency');
  });

  it('free_text_blocked item → section appears with identifier', () => {
    const items: HealthItem[] = [
      makeHealthItem({
        identifier: 'SAG-100',
        title: 'Zombie blocked ticket',
        blockedSubclass: 'free_text_blocked',
        assigneeAgentId: 'agent-001-xyz',
      }),
    ];
    const digest = renderTicketHealthDigest(items, EMPTY_OPTS);
    expect(digest).toContain('No-blocker blocked');
    expect(digest).toContain('SAG-100');
    expect(digest).toContain('Zombie blocked ticket');
  });

  it('multiple free_text_blocked items → count in section header', () => {
    const items: HealthItem[] = [
      makeHealthItem({ identifier: 'SAG-101', blockedSubclass: 'free_text_blocked' }),
      makeHealthItem({ identifier: 'SAG-102', blockedSubclass: 'free_text_blocked' }),
      makeHealthItem({ identifier: 'SAG-103', blockedSubclass: 'free_text_blocked' }),
    ];
    const digest = renderTicketHealthDigest(items, EMPTY_OPTS);
    expect(digest).toContain('No-blocker blocked');
    expect(digest).toContain('(3)');
    expect(digest).toContain('SAG-101');
    expect(digest).toContain('SAG-102');
    expect(digest).toContain('SAG-103');
  });

  it('genuinely_blocked items do NOT appear in the no-blocker section', () => {
    const items: HealthItem[] = [
      makeHealthItem({ identifier: 'SAG-200', blockedSubclass: 'genuinely_blocked' }),
      makeHealthItem({ identifier: 'SAG-201', blockedSubclass: 'free_text_blocked' }),
    ];
    const digest = renderTicketHealthDigest(items, EMPTY_OPTS);
    // Only SAG-201 in the no-blocker section
    const sectionStart = digest.indexOf('No-blocker blocked');
    const sectionEnd = digest.indexOf('\n###', sectionStart + 1);
    const sectionText = sectionStart >= 0 ? digest.slice(sectionStart, sectionEnd > 0 ? sectionEnd : undefined) : '';
    expect(sectionText).toContain('SAG-201');
    expect(sectionText).not.toContain('SAG-200');
  });

  it('false_blocked items remain in their own section, not in no-blocker', () => {
    const items: HealthItem[] = [
      makeHealthItem({ identifier: 'SAG-300', blockedSubclass: 'false_blocked' }),
      makeHealthItem({ identifier: 'SAG-301', blockedSubclass: 'free_text_blocked' }),
    ];
    const digest = renderTicketHealthDigest(items, EMPTY_OPTS);
    expect(digest).toContain('False-blocked tickets');
    expect(digest).toContain('SAG-300');
    expect(digest).toContain('No-blocker blocked');
    expect(digest).toContain('SAG-301');
  });
});
