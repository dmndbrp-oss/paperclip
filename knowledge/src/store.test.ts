import { describe, it, expect, beforeEach } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { stringify as yamlStringify } from 'yaml';
import { validateEntry, writeEntry, readEntriesBySpecialtyAndDomain, initDirectories, entryExists } from './store.js';
import type { KnowledgeEntry } from './schema.js';

const GOLDEN: KnowledgeEntry = {
  task_id: 'SAG-1001',
  identifier: 'SAG-1001',
  title: 'Test vetting run',
  specialty: 'ssi_director',
  domain: 'security',
  outcome: 'done',
  summary: 'Completed security vetting of a candidate library.',
  decided_at: '2026-05-10T12:00:00.000Z',
  digest_model: 'claude-sonnet-4-6',
  digest_version: 1,
  source: 'digester',
  surprises: ['Library had hidden GPL dep'],
  anti_patterns: ['Do not trust star count without contributor audit'],
  files_touched: ['companies/x/knowledge/tasks/2026/05/SAG-1001.yaml'],
  duration_minutes: 30,
};

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'knowledge-test-'));
}

// ---------- validateEntry ----------

describe('validateEntry — golden input', () => {
  it('accepts a fully populated valid entry', () => {
    const result = validateEntry(GOLDEN);
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.task_id).toBe('SAG-1001');
  });

  it('accepts an entry with only required fields', () => {
    const minimal = {
      task_id: 'SAG-2', identifier: 'SAG-2', title: 'Min entry',
      specialty: 'coder', domain: 'runtime', outcome: 'done',
      summary: 'Short summary.',
      decided_at: '2026-01-01T00:00:00.000Z',
      digest_model: 'claude-haiku-4-5-20251001', digest_version: 1, source: 'manual',
    };
    const result = validateEntry(minimal);
    expect(result.success).toBe(true);
  });
});

describe('validateEntry — missing required fields', () => {
  it('rejects when task_id is missing', () => {
    const { task_id: _, ...bad } = GOLDEN;
    const result = validateEntry(bad);
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('task_id'))).toBe(true);
  });

  it('rejects when decided_at is missing', () => {
    const { decided_at: _, ...bad } = GOLDEN;
    const result = validateEntry(bad);
    expect(result.success).toBe(false);
  });

  it('rejects when digest_version is missing', () => {
    const { digest_version: _, ...bad } = GOLDEN;
    const result = validateEntry(bad);
    expect(result.success).toBe(false);
  });
});

describe('validateEntry — bad enums', () => {
  it('rejects unknown domain', () => {
    const result = validateEntry({ ...GOLDEN, domain: 'unknown_domain' });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('domain'))).toBe(true);
  });

  it('rejects unknown specialty', () => {
    const result = validateEntry({ ...GOLDEN, specialty: 'robot' });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('specialty'))).toBe(true);
  });

  it('rejects unknown outcome', () => {
    const result = validateEntry({ ...GOLDEN, outcome: 'partial' });
    expect(result.success).toBe(false);
  });
});

describe('validateEntry — hard caps', () => {
  it('rejects summary > 200 chars', () => {
    const result = validateEntry({ ...GOLDEN, summary: 'x'.repeat(201) });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('summary'))).toBe(true);
  });

  it('rejects surprises with 4 items', () => {
    const result = validateEntry({ ...GOLDEN, surprises: ['a', 'b', 'c', 'd'] });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('surprises'))).toBe(true);
  });

  it('rejects anti_patterns with 4 items', () => {
    const result = validateEntry({ ...GOLDEN, anti_patterns: ['a', 'b', 'c', 'd'] });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('anti_patterns'))).toBe(true);
  });

  it('rejects files_touched with 6 items', () => {
    const result = validateEntry({ ...GOLDEN, files_touched: ['a', 'b', 'c', 'd', 'e', 'f'] });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.errors.some(e => e.includes('files_touched'))).toBe(true);
  });

  it('accepts surprises at max of 3 items', () => {
    const result = validateEntry({ ...GOLDEN, surprises: ['a', 'b', 'c'] });
    expect(result.success).toBe(true);
  });

  it('accepts files_touched at max of 5 items', () => {
    const result = validateEntry({ ...GOLDEN, files_touched: ['a', 'b', 'c', 'd', 'e'] });
    expect(result.success).toBe(true);
  });
});

// ---------- writeEntry + readEntriesBySpecialtyAndDomain ----------

describe('writeEntry', () => {
  it('creates YAML file at correct year/month path', () => {
    const dir = makeTmpDir();
    writeEntry(dir, GOLDEN);
    const yamlPath = path.join(dir, 'tasks', '2026', '05', 'SAG-1001.yaml');
    expect(fs.existsSync(yamlPath)).toBe(true);
  });

  it('appends to by_domain index', () => {
    const dir = makeTmpDir();
    writeEntry(dir, GOLDEN);
    const indexPath = path.join(dir, 'index', 'by_domain', 'security.jsonl');
    expect(fs.existsSync(indexPath)).toBe(true);
    const line = fs.readFileSync(indexPath, 'utf8').trim();
    const row = JSON.parse(line);
    expect(row.task_id).toBe('SAG-1001');
    expect(row.domain).toBe('security');
  });

  it('appends to by_specialty index', () => {
    const dir = makeTmpDir();
    writeEntry(dir, GOLDEN);
    const indexPath = path.join(dir, 'index', 'by_specialty', 'ssi_director.jsonl');
    expect(fs.existsSync(indexPath)).toBe(true);
    const row = JSON.parse(fs.readFileSync(indexPath, 'utf8').trim());
    expect(row.specialty).toBe('ssi_director');
  });

  it('overwrites existing YAML (idempotent)', () => {
    const dir = makeTmpDir();
    writeEntry(dir, GOLDEN);
    const updated = { ...GOLDEN, summary: 'Updated summary here.' };
    writeEntry(dir, updated);
    const yamlPath = path.join(dir, 'tasks', '2026', '05', 'SAG-1001.yaml');
    const content = fs.readFileSync(yamlPath, 'utf8');
    expect(content).toContain('Updated summary here.');
  });
});

describe('readEntriesBySpecialtyAndDomain', () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTmpDir();
  });

  it('returns matching entries recency-ranked', () => {
    const older: KnowledgeEntry = { ...GOLDEN, task_id: 'SAG-1001', identifier: 'SAG-1001', decided_at: '2026-03-01T00:00:00.000Z' };
    const newer: KnowledgeEntry = { ...GOLDEN, task_id: 'SAG-1002', identifier: 'SAG-1002', decided_at: '2026-04-01T00:00:00.000Z' };
    writeEntry(dir, older);
    writeEntry(dir, newer);
    const results = readEntriesBySpecialtyAndDomain(dir, 'ssi_director', 'security', 10);
    expect(results).toHaveLength(2);
    expect(results[0].identifier).toBe('SAG-1002');
    expect(results[1].identifier).toBe('SAG-1001');
  });

  it('filters out different domain entries', () => {
    writeEntry(dir, GOLDEN);
    const other: KnowledgeEntry = { ...GOLDEN, task_id: 'SAG-2000', identifier: 'SAG-2000', domain: 'governance' };
    writeEntry(dir, other);
    const results = readEntriesBySpecialtyAndDomain(dir, 'ssi_director', 'security', 10);
    expect(results.every(e => e.domain === 'security')).toBe(true);
  });

  it('respects limit', () => {
    for (let i = 1; i <= 5; i++) {
      writeEntry(dir, { ...GOLDEN, task_id: `SAG-${1000 + i}`, identifier: `SAG-${1000 + i}`, decided_at: `2026-0${i}-01T00:00:00.000Z` });
    }
    const results = readEntriesBySpecialtyAndDomain(dir, 'ssi_director', 'security', 3);
    expect(results).toHaveLength(3);
  });

  it('returns empty array when no entries exist', () => {
    const results = readEntriesBySpecialtyAndDomain(dir, 'coder', 'runtime', 5);
    expect(results).toHaveLength(0);
  });
});

// ---------- initDirectories ----------

describe('initDirectories', () => {
  it('creates required directories idempotently', () => {
    const dir = makeTmpDir();
    initDirectories(dir);
    initDirectories(dir); // second call should not throw
    expect(fs.existsSync(path.join(dir, 'tasks'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'index', 'by_domain'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'index', 'by_specialty'))).toBe(true);
  });
});

// ---------- entryExists ----------

describe('entryExists', () => {
  it('returns false for an absent entry', () => {
    const dir = makeTmpDir();
    expect(entryExists(dir, 'SAG-9001', '2026-05-15T00:00:00.000Z')).toBe(false);
  });

  it('returns true after writeEntry for the same identifier + decided_at', () => {
    const dir = makeTmpDir();
    const entry: KnowledgeEntry = {
      ...GOLDEN,
      task_id: 'SAG-9001',
      identifier: 'SAG-9001',
      decided_at: '2026-05-15T00:00:00.000Z',
    };
    writeEntry(dir, entry);
    expect(entryExists(dir, 'SAG-9001', '2026-05-15T00:00:00.000Z')).toBe(true);
  });

  it('returns false for a different identifier even if decided_at matches', () => {
    const dir = makeTmpDir();
    writeEntry(dir, { ...GOLDEN, task_id: 'SAG-9001', identifier: 'SAG-9001', decided_at: '2026-05-15T00:00:00.000Z' });
    expect(entryExists(dir, 'SAG-9002', '2026-05-15T00:00:00.000Z')).toBe(false);
  });
});
