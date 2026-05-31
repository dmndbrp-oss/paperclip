/**
 * Unit tests for knowledge/src/blockers.ts — SAG-2572
 *
 * Covers: all 6 board categories, agent exclusion, stale-blocker exclusion,
 * ranking (priority > age > blast-radius), and content-hash stability.
 * All fixtures derived from live SAG issues captured 2026-05-31.
 */

import { describe, it, expect } from 'vitest';
import {
  classify,
  rank,
  contentHash,
  renderDigest,
  type IssueDetail,
  type BlockerCandidate,
  type Interaction,
  type ClassifiedIssue,
} from '../../src/blockers.js';

// ─────────────────────────────────────────────────────────────────────────────
// Fixture builders
// ─────────────────────────────────────────────────────────────────────────────

function makeDetail(overrides: Partial<IssueDetail> = {}): IssueDetail {
  return {
    id: 'aaaa-0001',
    identifier: 'SAG-9001',
    title: 'Generic blocked issue',
    description: '',
    priority: 'medium',
    createdAt: '2026-05-01T00:00:00.000Z',
    assigneeAgentId: null,
    blockerAttention: { state: 'needs_attention', unresolvedBlockerCount: 1, sampleBlockerIdentifier: null },
    blocks: [],
    blockedBy: [],
    ...overrides,
  };
}

function noInteractions(): Interaction[] {
  return [];
}

function pendingConfirmInteraction(): Interaction[] {
  return [{ kind: 'request_confirmation', status: 'pending' }];
}

// ─────────────────────────────────────────────────────────────────────────────
// Fixtures — real issue shapes (from live API 2026-05-31)
// ─────────────────────────────────────────────────────────────────────────────

// SAG-2337: oauth_reconnect — "Disconnect unused claude.ai OAuth integrations"
const SAG_2337: IssueDetail = {
  id: '76ae4796-0464-461e-920f-3e0b0f9d56fd',
  identifier: 'SAG-2337',
  title: 'Disconnect unused claude.ai OAuth integrations (no-code, operator action)',
  description: 'Reduce the 28-entry claudeAiMcpEverConnected set on the claude.ai OAuth account. Reconnect only engineering-org keepers.',
  priority: 'high',
  createdAt: '2026-05-25T23:01:52.422Z',
  assigneeAgentId: null,
  blockerAttention: { state: 'needs_attention', unresolvedBlockerCount: 0, sampleBlockerIdentifier: null },
  blocks: [],
  blockedBy: [],
};

// SAG-2170: credential_purchase — "Provide GitHub PAT"
const SAG_2170: IssueDetail = {
  id: 'd70c1bfa-7465-457f-b7c1-6755638dd694',
  identifier: 'SAG-2170',
  title: 'Provide GitHub PAT for upstream paperclip-create-agent PR',
  description: 'Coder has prepared all four required edits. Board must provide GitHub PAT to push.',
  priority: 'low',
  createdAt: '2026-05-24T07:39:57.430Z',
  assigneeAgentId: null,
  blockerAttention: { state: 'needs_attention', unresolvedBlockerCount: 0, sampleBlockerIdentifier: null },
  blocks: [{ identifier: 'SAG-2167', status: 'blocked' }],
  blockedBy: [],
};

// SAG-1330: credential_purchase — "Add ANTHROPIC_API_KEY"
const SAG_1330: IssueDetail = {
  id: '24c11c67-e3eb-4173-8960-4a7c1b59027d',
  identifier: 'SAG-1330',
  title: 'Add ANTHROPIC_API_KEY to Paperclip server .env and restart — unblocks SAG-1318 smoke test',
  description: 'CEO must provision the key value. Add ANTHROPIC_API_KEY to .env.',
  priority: 'high',
  createdAt: '2026-05-16T17:39:46.422Z',
  assigneeAgentId: '3ab7fa06-f831-4631-922a-2fe824005788', // Coder
  blockerAttention: { state: 'covered', unresolvedBlockerCount: 1, sampleBlockerIdentifier: 'SAG-1328' },
  blocks: [],
  blockedBy: [{ identifier: 'SAG-1328', status: 'in_review', assigneeAgentId: null, terminalBlockers: [] }],
};

// SAG-326: stale blocker — terminal blocker SAG-1093 is cancelled
const SAG_326: IssueDetail = {
  id: '417727d6-bdc7-4d38-8164-15c777819247',
  identifier: 'SAG-326',
  title: 'Phase 1: Claude-tiered routing dispatcher + audit/cost logging + 1-agent pilot',
  description: 'Long-running phase 1 implementation task.',
  priority: 'critical',
  createdAt: '2026-05-07T20:39:37.426Z',
  assigneeAgentId: 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89', // CEO
  blockerAttention: { state: 'needs_attention', unresolvedBlockerCount: 3, sampleBlockerIdentifier: 'SAG-1093' },
  blocks: [],
  blockedBy: [
    {
      identifier: 'SAG-1091',
      status: 'blocked',
      assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d',
      terminalBlockers: [
        { identifier: 'SAG-1093', status: 'cancelled', assigneeAgentId: 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89', title: 'Cancelled sub-task' },
      ],
    },
  ],
};

// Fake routine/cron issue
const ROUTINE_ISSUE: IssueDetail = makeDetail({
  id: 'aaaa-0010',
  identifier: 'SAG-2510',
  title: 'SAG-2510 digester routine go-live — create paused cron 0 */6 * * *',
  description: 'Routine c82813c2 needs to be unpaused. Board-only routine management. Schedule 0 */6 * * *.',
  priority: 'medium',
  createdAt: '2026-05-29T00:00:00.000Z',
});

// Fake tailscale/infra issue
const INFRA_ISSUE: IssueDetail = makeDetail({
  id: 'aaaa-0011',
  identifier: 'SAG-9011',
  title: 'Tailscale tunnel down — agent cannot reach external API',
  description: 'tailscale tunnel unreachable, DNS resolution failing.',
  priority: 'high',
  createdAt: '2026-05-28T00:00:00.000Z',
});

// Fake hire/model signoff issue
const HIRE_ISSUE: IssueDetail = makeDetail({
  id: 'aaaa-0012',
  identifier: 'SAG-9012',
  title: 'Hire approval for new QA agent',
  description: 'Board approval required for model-change sign-off on new QA Director hire.',
  priority: 'medium',
  createdAt: '2026-05-27T00:00:00.000Z',
});

// ─── Regression fixtures for SAG-2592 false-positive fixes ──────────────────

// SAG-316: PWA notifications button — "installed via Tailscale" is incidental;
// must classify agent (no action phrasing near tailscale).
const SAG_316: IssueDetail = makeDetail({
  id: 'aaaa-0316',
  identifier: 'SAG-316',
  title: "Enable Notifications button doesn't prompt for permission (PWA)",
  description: 'The notification permission prompt is not appearing. App was installed as PWA via Tailscale tunnel at pwa.internal. No infra action required.',
  priority: 'medium',
  createdAt: '2026-03-01T00:00:00.000Z',
  assigneeAgentId: 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89', // CEO
});

// SAG-865: upstream PR for notifications button — code work only. Terminal
// blocker title includes SAG-316 text which mentions tailscale incidentally.
const SAG_865: IssueDetail = makeDetail({
  id: 'aaaa-0865',
  identifier: 'SAG-865',
  title: 'Implement Notifications button (upstream PR)',
  description: 'Implement the PWA notification permission flow. Upstream PR for the button.',
  priority: 'medium',
  createdAt: '2026-04-01T00:00:00.000Z',
  assigneeAgentId: '3ab7fa06-f831-4631-922a-2fe824005788', // Coder
  blockedBy: [
    {
      identifier: 'SAG-316',
      status: 'blocked',
      assigneeAgentId: 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89',
      terminalBlockers: [
        {
          identifier: 'SAG-316',
          status: 'blocked',
          assigneeAgentId: 'b0f67cc2-259e-477b-ac89-d0ff4e7c8e89',
          title: "Enable Notifications button doesn't prompt for permission (PWA installed via Tailscale)",
        },
      ],
    },
  ],
});

// SAG-800: Bind HTTP server to all interfaces — CTO code task. Description
// mentions "Tailscale-routed 'API down'" as a symptom, not as board action.
// Must classify agent via the internal-owner override (CTO assignee).
const SAG_800: IssueDetail = makeDetail({
  id: 'aaaa-0800',
  identifier: 'SAG-800',
  title: 'Bind HTTP server to all interfaces (fix Tailscale-routed API down)',
  description: "Tailscale-routed 'API down' error appears when connecting from remote host. Fix: bind server to 0.0.0.0 instead of 127.0.0.1.",
  priority: 'high',
  createdAt: '2026-04-15T00:00:00.000Z',
  assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d', // CTO
});

// SAG-1272: D365 environment provisioning — genuine board action. Must remain board.
const SAG_1272: IssueDetail = makeDetail({
  id: 'aaaa-1272',
  identifier: 'SAG-1272',
  title: 'D365 environment provisioning for integration testing',
  description: 'Board must provision D365 environment. Dynamics setup requires board access.',
  priority: 'medium',
  createdAt: '2026-04-20T00:00:00.000Z',
  assigneeAgentId: null,
});

// Agent-owned issue (CTO owns the blocker, no board rule fires)
const AGENT_OWNED: IssueDetail = makeDetail({
  id: 'aaaa-0020',
  identifier: 'SAG-9020',
  title: 'Fix bug in opencode_local adapter timeout logic',
  description: 'CTO to patch the adapter timeout. No credentials or infra needed.',
  priority: 'high',
  createdAt: '2026-05-30T00:00:00.000Z',
  assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d', // CTO
  blockedBy: [
    {
      identifier: 'SAG-9019',
      status: 'in_progress',
      assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d',
      terminalBlockers: [
        { identifier: 'SAG-9018', status: 'in_progress', assigneeAgentId: 'f3c48afc-c339-4e43-b47b-a42a0891229d', title: 'CTO impl sub-task' },
      ],
    },
  ],
});

// ─────────────────────────────────────────────────────────────────────────────
// classify() — all 6 board categories
// ─────────────────────────────────────────────────────────────────────────────

describe('classify — pending_confirmation via interaction', () => {
  it('classifies board when pending request_confirmation interaction exists', () => {
    const detail = makeDetail({ id: 'aaaa-0030', identifier: 'SAG-9030', title: 'Some blocked issue' });
    const result = classify(detail, detail, pendingConfirmInteraction());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('pending_confirmation');
    expect(result.matchedRule).toBe(1);
  });

  it('classifies board even if terminal blockers are stale when confirmation is pending', () => {
    const result = classify(SAG_326, SAG_326, pendingConfirmInteraction());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('pending_confirmation');
  });
});

describe('classify — pending_confirmation via text', () => {
  it('classifies board when title contains request_confirmation', () => {
    const detail = makeDetail({
      title: 'Route request_confirmation to board for routine go-live',
      description: 'Awaiting board acceptance of the confirmation.',
    });
    const result = classify(detail, detail, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('pending_confirmation');
  });
});

describe('classify — routine_cron', () => {
  it('classifies board for routine cron/unpause issue', () => {
    const result = classify(ROUTINE_ISSUE, ROUTINE_ISSUE, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('routine_cron');
    expect(result.matchedRule).toBe(2);
  });
});

describe('classify — oauth_reconnect', () => {
  it('classifies SAG-2337 as board/oauth_reconnect', () => {
    const result = classify(SAG_2337, SAG_2337, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('oauth_reconnect');
    expect(result.matchedRule).toBe(3);
  });
});

describe('classify — credential_purchase', () => {
  it('classifies SAG-2170 (GitHub PAT) as board/credential_purchase', () => {
    const result = classify(SAG_2170, SAG_2170, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('credential_purchase');
    expect(result.matchedRule).toBe(4);
  });

  it('classifies SAG-1330 (ANTHROPIC_API_KEY) as board/credential_purchase', () => {
    const result = classify(SAG_1330, SAG_1330, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('credential_purchase');
  });
});

describe('classify — infra_unreachable', () => {
  it('classifies tailscale/tunnel issue as board/infra_unreachable', () => {
    const result = classify(INFRA_ISSUE, INFRA_ISSUE, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('infra_unreachable');
    expect(result.matchedRule).toBe(5);
  });
});

// ─── SAG-2592 regression: false-positive fixes ───────────────────────────────

describe('classify — SAG-2592 infra_unreachable false-positive regressions', () => {
  it('SAG-316: classifies agent — "installed via Tailscale" is incidental, no action phrasing', () => {
    const result = classify(SAG_316, SAG_316, noInteractions());
    expect(result.classification).toBe('agent');
    expect(result.category).toBeUndefined();
  });

  it('SAG-865: classifies agent — terminal blocker title mentions tailscale incidentally', () => {
    const result = classify(SAG_865, SAG_865, noInteractions());
    expect(result.classification).toBe('agent');
    expect(result.category).toBeUndefined();
  });

  it('SAG-800: classifies agent — CTO internal owner overrides infra_unreachable match', () => {
    const result = classify(SAG_800, SAG_800, noInteractions());
    expect(result.classification).toBe('agent');
  });

  it('SAG-1272: classifies board/infra_unreachable — genuine D365 provision action', () => {
    const result = classify(SAG_1272, SAG_1272, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('infra_unreachable');
  });
});

describe('classify — hire_or_model_signoff', () => {
  it('classifies hire approval issue as board/hire_or_model_signoff', () => {
    const result = classify(HIRE_ISSUE, HIRE_ISSUE, noInteractions());
    expect(result.classification).toBe('board');
    expect(result.category).toBe('hire_or_model_signoff');
    expect(result.matchedRule).toBe(6);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// classify() — agent exclusions
// ─────────────────────────────────────────────────────────────────────────────

describe('classify — stale terminal blockers → agent', () => {
  it('classifies SAG-326 as agent when all terminal blockers are cancelled', () => {
    const result = classify(SAG_326, SAG_326, noInteractions());
    expect(result.classification).toBe('agent');
    expect(result.stale).toBe(true);
  });

  it('classifies agent when terminal blocker is done', () => {
    const detail = makeDetail({
      blockedBy: [
        {
          identifier: 'SAG-DONE',
          status: 'done',
          assigneeAgentId: null,
          terminalBlockers: [{ identifier: 'SAG-DONE-ROOT', status: 'done', assigneeAgentId: null }],
        },
      ],
    });
    const result = classify(detail, detail, noInteractions());
    expect(result.classification).toBe('agent');
    expect(result.stale).toBe(true);
  });
});

describe('classify — internal agent blocker → agent', () => {
  it('classifies agent when no board rule fires (CTO-owned blocker)', () => {
    const result = classify(AGENT_OWNED, AGENT_OWNED, noInteractions());
    expect(result.classification).toBe('agent');
    expect(result.stale).toBeFalsy();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// rank()
// ─────────────────────────────────────────────────────────────────────────────

function makeClassified(overrides: {
  id?: string;
  priority?: 'critical' | 'high' | 'medium' | 'low';
  createdAt?: string;
  blastRadius?: number;
  category?: ClassifiedIssue['category'];
}): ClassifiedIssue {
  const detail = makeDetail({
    id: overrides.id ?? 'id-x',
    priority: overrides.priority ?? 'medium',
    createdAt: overrides.createdAt ?? '2026-05-15T00:00:00.000Z',
    blocks: Array.from({ length: overrides.blastRadius ?? 0 }, (_, i) => ({
      identifier: `SAG-${9900 + i}`,
      status: 'blocked',
    })),
  });
  return {
    detail,
    classification: 'board',
    category: overrides.category ?? 'oauth_reconnect',
    blastRadius: overrides.blastRadius ?? 0,
  };
}

describe('rank()', () => {
  it('sorts critical before high', () => {
    const high = makeClassified({ id: 'id-high', priority: 'high' });
    const critical = makeClassified({ id: 'id-crit', priority: 'critical' });
    const sorted = rank([high, critical]);
    expect(sorted[0]!.detail.priority).toBe('critical');
    expect(sorted[1]!.detail.priority).toBe('high');
  });

  it('within same priority, sorts oldest first (by createdAt ASC)', () => {
    const newer = makeClassified({ id: 'id-new', priority: 'high', createdAt: '2026-05-30T00:00:00.000Z' });
    const older = makeClassified({ id: 'id-old', priority: 'high', createdAt: '2026-05-01T00:00:00.000Z' });
    const sorted = rank([newer, older]);
    expect(sorted[0]!.detail.id).toBe('id-old');
    expect(sorted[1]!.detail.id).toBe('id-new');
  });

  it('within same priority+age, sorts higher blast radius first', () => {
    const sameTs = '2026-05-15T00:00:00.000Z';
    const lowBlast = makeClassified({ id: 'id-low-blast', priority: 'medium', createdAt: sameTs, blastRadius: 1 });
    const highBlast = makeClassified({ id: 'id-high-blast', priority: 'medium', createdAt: sameTs, blastRadius: 5 });
    const sorted = rank([lowBlast, highBlast]);
    expect(sorted[0]!.detail.id).toBe('id-high-blast');
    expect(sorted[1]!.detail.id).toBe('id-low-blast');
  });

  it('applies all three tiers together', () => {
    const critOld = makeClassified({ id: 'crit-old', priority: 'critical', createdAt: '2026-05-01T00:00:00.000Z' });
    const critNew = makeClassified({ id: 'crit-new', priority: 'critical', createdAt: '2026-05-28T00:00:00.000Z' });
    const highOld = makeClassified({ id: 'high-old', priority: 'high', createdAt: '2026-05-01T00:00:00.000Z' });
    const sorted = rank([highOld, critNew, critOld]);
    expect(sorted.map((s) => s.detail.id)).toEqual(['crit-old', 'crit-new', 'high-old']);
  });

  it('does not mutate the input array', () => {
    const items = [
      makeClassified({ id: 'b', priority: 'high' }),
      makeClassified({ id: 'a', priority: 'critical' }),
    ];
    const original = [...items];
    rank(items);
    expect(items[0]!.detail.id).toBe(original[0]!.detail.id);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// contentHash()
// ─────────────────────────────────────────────────────────────────────────────

describe('contentHash()', () => {
  it('is stable across calls with identical inputs', () => {
    const items = [
      makeClassified({ id: 'id-1', category: 'oauth_reconnect' }),
      makeClassified({ id: 'id-2', category: 'credential_purchase' }),
    ];
    expect(contentHash(items)).toBe(contentHash(items));
  });

  it('is order-independent (sorts before hashing)', () => {
    const a = makeClassified({ id: 'id-1', category: 'oauth_reconnect' });
    const b = makeClassified({ id: 'id-2', category: 'credential_purchase' });
    expect(contentHash([a, b])).toBe(contentHash([b, a]));
  });

  it('changes when an item is added', () => {
    const base = [makeClassified({ id: 'id-1' })];
    const extended = [...base, makeClassified({ id: 'id-2' })];
    expect(contentHash(base)).not.toBe(contentHash(extended));
  });

  it('changes when an item is removed', () => {
    const full = [makeClassified({ id: 'id-1' }), makeClassified({ id: 'id-2' })];
    const trimmed = [makeClassified({ id: 'id-1' })];
    expect(contentHash(full)).not.toBe(contentHash(trimmed));
  });

  it('returns a 64-char hex string (sha256)', () => {
    const hash = contentHash([makeClassified({ id: 'id-1' })]);
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('empty list is stable and valid', () => {
    expect(contentHash([])).toMatch(/^[0-9a-f]{64}$/);
    expect(contentHash([])).toBe(contentHash([]));
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// renderDigest() — smoke tests
// ─────────────────────────────────────────────────────────────────────────────

describe('renderDigest()', () => {
  it('renders board items with identifier and category label', () => {
    const item = makeClassified({ id: SAG_2337.id, priority: 'high', category: 'oauth_reconnect' });
    item.detail.identifier = 'SAG-2337';
    item.detail.title = 'Disconnect unused claude.ai OAuth integrations';
    const md = renderDigest([item], [], { runAt: '2026-05-31T13:00:00.000Z' });
    expect(md).toContain('SAG-2337');
    expect(md).toContain('OAuth reconnect');
    expect(md).toContain('Resolution steps');
  });

  it('renders "no board-resolvable" message when list is empty', () => {
    const md = renderDigest([], [], { runAt: '2026-05-31T13:00:00.000Z' });
    expect(md).toContain('No board-resolvable blockers');
  });

  it('renders FYI tail when agent items provided', () => {
    const agentItem = makeClassified({ id: 'aaaa-agent', priority: 'medium' });
    agentItem.detail.identifier = 'SAG-9020';
    agentItem.classification = 'agent';
    const md = renderDigest([], [agentItem], { runAt: '2026-05-31T13:00:00.000Z' });
    expect(md).toContain('SAG-9020');
  });
});
