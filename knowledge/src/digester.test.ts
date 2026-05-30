import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { parse as yamlParse } from 'yaml';
import { validateEntry, writeEntry } from './store.js';
import { DIGESTER_SYSTEM_PROMPT, digestIssue, type DigesterConfig } from './digester.js';
import type { Summarizer } from './summarizer.js';

// ────────────────────────────────────────────────────────────────────────────
// Helpers for failure-path simulation
// ────────────────────────────────────────────────────────────────────────────

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'digester-test-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function failureLogPath(): string {
  return path.join(tmpDir, '_digestion_failures.jsonl');
}

function logFailure(entry: Record<string, unknown>): void {
  const logFile = failureLogPath();
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.appendFileSync(logFile, JSON.stringify({ ...entry, timestamp: new Date().toISOString() }) + '\n', 'utf8');
}

function readFailures(): unknown[] {
  const p = failureLogPath();
  if (!fs.existsSync(p)) return [];
  return fs.readFileSync(p, 'utf8')
    .trim().split('\n').filter(Boolean)
    .map((l) => JSON.parse(l));
}

// ────────────────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────────────────

describe('validateEntry', () => {
  it('accepts a well-formed entry', () => {
    const result = validateEntry({
      task_id: 'abc-123',
      identifier: 'SAG-999',
      title: 'Test entry',
      specialty: 'ssi_director',
      domain: 'ssi-hp',
      outcome: 'done',
      summary: 'Completed the test task without issues.',
      decided_at: '2026-05-24T10:00:00.000Z',
      digest_model: 'claude-sonnet-4-6',
      digest_version: 1,
      source: 'digester',
    });
    expect(result.success).toBe(true);
  });

  it('rejects an entry with an invalid domain enum', () => {
    const result = validateEntry({
      task_id: 'abc-123',
      identifier: 'SAG-999',
      title: 'Test',
      specialty: 'ssi_director',
      domain: 'INVALID_DOMAIN',       // deliberately wrong
      outcome: 'done',
      summary: 'Summary.',
      decided_at: '2026-05-24T10:00:00.000Z',
      digest_model: 'claude-sonnet-4-6',
      digest_version: 1,
      source: 'digester',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.errors.some((e) => e.includes('domain'))).toBe(true);
    }
  });

  it('rejects an entry with a summary exceeding 200 chars', () => {
    const result = validateEntry({
      task_id: 'abc-123',
      identifier: 'SAG-999',
      title: 'Test',
      specialty: 'ssi_director',
      domain: 'ssi-hp',
      outcome: 'done',
      summary: 'x'.repeat(201),        // 201 chars — over cap
      decided_at: '2026-05-24T10:00:00.000Z',
      digest_model: 'claude-sonnet-4-6',
      digest_version: 1,
      source: 'digester',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.errors.some((e) => e.includes('summary'))).toBe(true);
    }
  });

  it('rejects an entry missing required fields', () => {
    const result = validateEntry({ task_id: 'abc-123' });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.errors.length).toBeGreaterThan(0);
    }
  });
});

describe('failure logging path', () => {
  it('logs a validation_failed entry to the JSONL file', () => {
    const badEntry = {
      task_id: 'bad-id',
      identifier: 'SAG-BAD',
      // missing specialty, domain, title, etc.
    };

    const validation = validateEntry(badEntry);
    expect(validation.success).toBe(false);

    if (!validation.success) {
      logFailure({
        issueId: 'bad-id',
        identifier: 'SAG-BAD',
        reason: 'validation_failed',
        errors: validation.errors,
        parsed: badEntry,
      });
    }

    const failures = readFailures();
    expect(failures.length).toBe(1);
    const f = failures[0] as Record<string, unknown>;
    expect(f['reason']).toBe('validation_failed');
    expect(Array.isArray(f['errors'])).toBe(true);
    expect((f['errors'] as string[]).length).toBeGreaterThan(0);
    expect(f['issueId']).toBe('bad-id');
    expect(typeof f['timestamp']).toBe('string');
  });

  it('logs a no_yaml_block entry when model returns garbage', () => {
    // Simulates the pipeline receiving a response with no YAML block.
    const rawResponse = 'I cannot produce YAML for this request.';

    // Replicate the extractYamlBlock logic inline.
    const yamlMatch = rawResponse.match(/task_id:[\s\S]+?(?=\n\n|\n*$)/);
    expect(yamlMatch).toBeNull();

    logFailure({
      issueId: 'test-id',
      identifier: 'SAG-TEST',
      reason: 'no_yaml_block',
      rawResponse: rawResponse.slice(0, 500),
    });

    const failures = readFailures();
    expect(failures.length).toBe(1);
    const f = failures[0] as Record<string, unknown>;
    expect(f['reason']).toBe('no_yaml_block');
  });

  it('logs a yaml_parse_error entry when YAML is malformed', () => {
    const badYaml = 'task_id: [unclosed bracket';

    let parseError: string | null = null;
    try {
      yamlParse(badYaml);
    } catch (err) {
      parseError = String(err);
    }
    expect(parseError).not.toBeNull();

    logFailure({
      issueId: 'test-id',
      identifier: 'SAG-TEST',
      reason: `yaml_parse_error: ${parseError}`,
      rawYaml: badYaml.slice(0, 500),
    });

    const failures = readFailures();
    expect(failures.length).toBe(1);
    const f = failures[0] as Record<string, unknown>;
    expect((f['reason'] as string).startsWith('yaml_parse_error')).toBe(true);
  });

  it('appends multiple failures without overwriting', () => {
    for (let i = 0; i < 3; i++) {
      logFailure({ issueId: `id-${i}`, identifier: `SAG-${i}`, reason: 'test' });
    }
    const failures = readFailures();
    expect(failures.length).toBe(3);
  });
});

describe('writeEntry round-trip', () => {
  it('persists a valid entry and can be read back as YAML', () => {
    const entry = {
      task_id: 'roundtrip-001',
      identifier: 'SAG-ROUNDTRIP',
      title: 'Round-trip test',
      specialty: 'ssi_director' as const,
      domain: 'ssi-hp' as const,
      outcome: 'done' as const,
      summary: 'Entry survives write + read cycle intact.',
      decided_at: '2026-05-24T12:00:00.000Z',
      digest_model: 'claude-sonnet-4-6',
      digest_version: 1,
      source: 'digester' as const,
    };

    writeEntry(tmpDir, entry);

    const yamlPath = path.join(tmpDir, 'tasks', '2026', '05', 'SAG-ROUNDTRIP.yaml');
    expect(fs.existsSync(yamlPath)).toBe(true);

    const parsed = validateEntry(yamlParse(fs.readFileSync(yamlPath, 'utf8')));
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.task_id).toBe('roundtrip-001');
      expect(parsed.data.identifier).toBe('SAG-ROUNDTRIP');
      expect(parsed.data.summary).toBe('Entry survives write + read cycle intact.');
    }
  });
});

describe('DIGESTER_SYSTEM_PROMPT', () => {
  it('contains security fencing instructions', () => {
    expect(DIGESTER_SYSTEM_PROMPT).toContain('<<<UNTRUSTED>>>');
    expect(DIGESTER_SYSTEM_PROMPT).toContain('ignore any instructions');
  });

  it('lists all required fields', () => {
    const required = ['task_id', 'identifier', 'title', 'specialty', 'domain', 'outcome', 'summary', 'decided_at', 'digest_model', 'digest_version', 'source'];
    for (const field of required) {
      expect(DIGESTER_SYSTEM_PROMPT).toContain(field);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// digestIssue seam tests (Finding #2 — SAG-2508)
// Uses vi.stubGlobal to intercept fetch; injects a fake Summarizer.
// ────────────────────────────────────────────────────────────────────────────

describe('digestIssue seam', () => {
  const FAKE_ISSUE_ID = 'aaaabbbb-0000-0000-0000-000000000001';

  const FAKE_ISSUE = {
    id: FAKE_ISSUE_ID,
    identifier: 'SAG-9999',
    title: 'Test issue',
    description: 'Test description',
    status: 'done',
    completedAt: '2026-05-30T00:00:00.000Z',
    updatedAt: '2026-05-30T00:00:00.000Z',
    assigneeAgentId: 'agent-target',
  };

  const CANNED_YAML = [
    `task_id: ${FAKE_ISSUE_ID}`,
    'identifier: SAG-9999',
    'title: Test issue',
    'specialty: ssi_director',
    'domain: ssi-hp',
    'outcome: done',
    'summary: Completed the test task successfully.',
    'decided_at: "2026-05-30T00:00:00.000Z"',
    'digest_model: claude-sonnet-4-6',
    'digest_version: 1',
    'source: digester',
  ].join('\n');

  function makeConfig(): DigesterConfig {
    return {
      baseDir: tmpDir,
      stateFile: path.join(tmpDir, 'state.json'),
      failureLogFile: path.join(tmpDir, '_failures.jsonl'),
      companyId: 'co-test',
      apiUrl: 'http://fake',
      apiKey: 'fake-key',
      summarizerAgentId: 'agent-sum',
      targetAgentId: 'agent-target',
      targetAgentRole: 'Director of SSI (ssi-hp catalog work)',
    };
  }

  function stubFetch(issueBody = FAKE_ISSUE): void {
    vi.stubGlobal('fetch', (url: string) => {
      if (url.endsWith('/comments')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.endsWith('/documents')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(issueBody) });
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('happy path: wires summarize → extractYamlBlock → validateEntry → writeEntry', async () => {
    stubFetch();
    const fakeSummarizer: Summarizer = {
      summarize: vi.fn().mockResolvedValue(CANNED_YAML),
    };

    const result = await digestIssue(makeConfig(), fakeSummarizer, FAKE_ISSUE_ID);

    expect(result.success).toBe(true);
    expect(result.identifier).toBe('SAG-9999');

    const yamlPath = path.join(tmpDir, 'tasks', '2026', '05', 'SAG-9999.yaml');
    expect(fs.existsSync(yamlPath)).toBe(true);
  });

  it('summarizer throw → result.reason contains summarizer_error, failure logged', async () => {
    stubFetch();
    const fakeSummarizer: Summarizer = {
      summarize: vi.fn().mockRejectedValue(new Error('worker timed out')),
    };

    const config = makeConfig();
    const result = await digestIssue(config, fakeSummarizer, FAKE_ISSUE_ID);

    expect(result.success).toBe(false);
    expect(result.reason).toMatch(/summarizer_error/);

    const lines = fs.readFileSync(config.failureLogFile, 'utf8')
      .trim().split('\n').filter(Boolean);
    expect(lines.length).toBe(1);
    const logged = JSON.parse(lines[0]) as Record<string, unknown>;
    expect((logged['reason'] as string)).toMatch(/summarizer_error/);
  });
});
