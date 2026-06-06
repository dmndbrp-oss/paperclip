import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { PaperclipTaskSummarizer } from './summarizer.js';

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────

const FAST_CFG = { pollIntervalMs: 10, timeoutMs: 500 };

const USER_PROMPT = [
  'task_id: aaaabbbb-0000-0000-0000-000000000001',
  'identifier: SAG-9999',
  'title: Test issue',
  'status: done',
  'completed_at: 2026-05-30T00:00:00.000Z',
  'assignee_role: Director of SSI',
].join('\n');

const CANNED_YAML = [
  'task_id: aaaabbbb-0000-0000-0000-000000000001',
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

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'summarizer-test-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
  vi.unstubAllGlobals();
});

function stubFetch(opts: {
  createId?: string;
  issueStatus?: string;
  comments?: Array<{ body: string }>;
} = {}) {
  const createId = opts.createId ?? 'delegated-id-001';
  const issueStatus = opts.issueStatus ?? 'done';
  const comments = opts.comments ?? [];

  vi.stubGlobal('fetch', (url: string, _init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.includes('/companies/') && urlStr.endsWith('/issues') && !urlStr.includes('delegated')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ id: createId }),
        text: () => Promise.resolve(''),
      });
    }
    if (urlStr.includes('/comments')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(comments) });
    }
    // poll: /api/issues/<id>
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: issueStatus }) });
  });
}

function writeKbFile(baseDir: string, identifier: string, decidedAt: string, content: string): string {
  const dt = new Date(decidedAt);
  const yyyy = dt.getUTCFullYear().toString();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const dir = path.join(baseDir, 'tasks', yyyy, mm);
  fs.mkdirSync(dir, { recursive: true });
  const filePath = path.join(dir, `${identifier}.yaml`);
  fs.writeFileSync(filePath, content, 'utf8');
  return filePath;
}

// ────────────────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────────────────

describe('PaperclipTaskSummarizer — comment-harvesting (existing contract)', () => {
  it('returns YAML comment body when worker posts task_id: comment', async () => {
    stubFetch({ comments: [{ body: CANNED_YAML }] });

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      ...FAST_CFG,
    });

    const result = await summarizer.summarize('system', USER_PROMPT);
    expect(result).toBe(CANNED_YAML);
  });

  it('throws when issue done, no YAML comment, and no kbBaseDir provided', async () => {
    stubFetch({ comments: [] });

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      ...FAST_CFG,
    });

    await expect(summarizer.summarize('system', USER_PROMPT)).rejects.toThrow(
      'done but no YAML comment found',
    );
  });
});

describe('PaperclipTaskSummarizer — KB-file fallback (SAG-2520 fix)', () => {
  it('returns KB file contents when worker wrote it instead of a YAML comment', async () => {
    stubFetch({ comments: [] });

    // Simulate the worker writing the KB file directly.
    writeKbFile(tmpDir, 'SAG-9999', '2026-05-30T00:00:00.000Z', CANNED_YAML);

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      kbBaseDir: tmpDir,
      ...FAST_CFG,
    });

    const result = await summarizer.summarize('system', USER_PROMPT);
    expect(result).toBe(CANNED_YAML);
  });

  it('prefers YAML comment over KB file when both are present', async () => {
    const commentYaml = CANNED_YAML + '\n# from-comment';
    stubFetch({ comments: [{ body: commentYaml }] });

    writeKbFile(tmpDir, 'SAG-9999', '2026-05-30T00:00:00.000Z', CANNED_YAML + '\n# from-file');

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      kbBaseDir: tmpDir,
      ...FAST_CFG,
    });

    const result = await summarizer.summarize('system', USER_PROMPT);
    expect(result).toContain('# from-comment');
  });

  it('throws when issue done, no YAML comment, and KB file also missing', async () => {
    stubFetch({ comments: [] });

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      kbBaseDir: tmpDir, // dir exists but no file inside
      ...FAST_CFG,
    });

    await expect(summarizer.summarize('system', USER_PROMPT)).rejects.toThrow(
      'done but no YAML comment found',
    );
  });

  it('correctly resolves path for different year/month from completed_at', async () => {
    stubFetch({ comments: [] });

    const identifier = 'SAG-8888';
    const decidedAt = '2025-11-15T12:00:00.000Z';
    const prompt = USER_PROMPT.replace('identifier: SAG-9999', `identifier: ${identifier}`)
      .replace('completed_at: 2026-05-30T00:00:00.000Z', `completed_at: ${decidedAt}`);

    writeKbFile(tmpDir, identifier, decidedAt, CANNED_YAML);

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      kbBaseDir: tmpDir,
      ...FAST_CFG,
    });

    const result = await summarizer.summarize('system', prompt);
    expect(result).toBe(CANNED_YAML);
  });
});

describe('PaperclipTaskSummarizer — terminal failure states', () => {
  it('throws immediately when delegated issue reaches cancelled state', async () => {
    stubFetch({ issueStatus: 'cancelled', comments: [] });

    const summarizer = new PaperclipTaskSummarizer({
      apiUrl: 'http://fake',
      apiKey: 'key',
      companyId: 'co',
      summarizerAgentId: 'agent-sum',
      ...FAST_CFG,
    });

    await expect(summarizer.summarize('system', USER_PROMPT)).rejects.toThrow(
      "reached terminal state 'cancelled'",
    );
  });
});
