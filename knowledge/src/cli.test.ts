import { describe, it, expect } from 'vitest';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TSX = path.resolve(__dirname, '../node_modules/.bin/tsx');
const CLI = path.resolve(__dirname, '../scripts/knowledge-cli.ts');

function runCli(args: string[], input: string, extraEnv?: Record<string, string>) {
  return spawnSync(TSX, [CLI, ...args], {
    input,
    encoding: 'utf8',
    env: { ...process.env, ...extraEnv },
    timeout: 15000,
    cwd: path.resolve(__dirname, '..'),
  });
}

const VALID_YAML = [
  'task_id: SAG-CLI-TEST-001',
  'identifier: SAG-CLI-TEST-001',
  'title: CLI integration test entry',
  'specialty: coder',
  'domain: runtime',
  'outcome: done',
  'summary: Short summary for CLI integration test.',
  'decided_at: 2026-05-30T10:00:00.000Z',
  'digest_model: claude-sonnet-4-6',
  'digest_version: 1',
  'source: manual',
].join('\n');

const INVALID_YAML = [
  'identifier: MISSING-REQUIRED',
  'title: Missing required fields entry',
].join('\n');

// ---------- write ----------

describe('knowledge-cli write', () => {
  it('valid YAML → exits 0, entry file created at expected path', () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'knowledge-cli-test-'));
    try {
      const result = runCli(['write'], VALID_YAML, { KNOWLEDGE_BASE_DIR: tmpDir });
      expect(result.status, `stderr: ${result.stderr}`).toBe(0);
      const expectedPath = path.join(tmpDir, 'tasks', '2026', '05', 'SAG-CLI-TEST-001.yaml');
      expect(fs.existsSync(expectedPath)).toBe(true);
      expect(result.stdout.trim()).toBe(expectedPath);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  it('invalid YAML → exits non-zero, stderr contains error', () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'knowledge-cli-test-'));
    try {
      const result = runCli(['write'], INVALID_YAML, { KNOWLEDGE_BASE_DIR: tmpDir });
      expect(result.status).not.toBe(0);
      expect(result.stderr.length).toBeGreaterThan(0);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});

// ---------- validate ----------

describe('knowledge-cli validate', () => {
  it('valid YAML → exits 0, stdout contains "valid"', () => {
    const result = runCli(['validate'], VALID_YAML);
    expect(result.status, `stderr: ${result.stderr}`).toBe(0);
    expect(result.stdout).toContain('valid');
  });

  it('invalid YAML → exits non-zero, stderr has errors', () => {
    const result = runCli(['validate'], INVALID_YAML);
    expect(result.status).not.toBe(0);
    expect(result.stderr.length).toBeGreaterThan(0);
  });
});

// ---------- no subcommand ----------

describe('knowledge-cli no subcommand', () => {
  it('no subcommand → exits non-zero with usage message on stderr', () => {
    const result = runCli([], '');
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('Usage');
  });
});
