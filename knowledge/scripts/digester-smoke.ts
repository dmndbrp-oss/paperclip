/**
 * SAG-2190 digester replay smoke.
 *
 * Replays the digestion pipeline against 3 historical SSI Director issues,
 * diffs the produced YAML against hand-written baselines, and exits non-zero
 * if any baseline diverges by > 1 field.
 *
 * Usage:
 *   npx tsx scripts/digester-smoke.ts
 *
 * Required env vars:
 *   PAPERCLIP_API_KEY, PAPERCLIP_API_URL
 *   PAPERCLIP_COMPANY_ID (optional, has a default)
 */

import path from 'node:path';
import fs from 'node:fs';
import { parse as yamlParse } from 'yaml';
import { digestIssue } from '../src/digester.js';
import type { DigesterConfig } from '../src/digester.js';
import { PaperclipTaskSummarizer } from '../src/summarizer.js';
import type { KnowledgeEntry } from '../src/index.js';
import { initDirectories } from '../src/index.js';

// ────────────────────────────────────────────────────────────────────────────
// Config
// ────────────────────────────────────────────────────────────────────────────

const SCRIPT_DIR = path.dirname(new URL(import.meta.url).pathname);
const SMOKE_DIR = path.resolve(SCRIPT_DIR, '../.smoke-output');
const BASELINES_DIR = path.resolve(SCRIPT_DIR, 'baselines');

const SUMMARIZER_AGENT_ID = '11d0b5de-44a9-4f05-b130-846804e29955';

const config: DigesterConfig = {
  baseDir: SMOKE_DIR,
  stateFile: path.join(SMOKE_DIR, 'state.json'),
  failureLogFile: path.join(SMOKE_DIR, '_digestion_failures.jsonl'),
  companyId: process.env['PAPERCLIP_COMPANY_ID'] ?? '1dc911ed-ff05-4072-b2ae-a3e3177e3873',
  apiUrl: process.env['PAPERCLIP_API_URL'] ?? 'http://127.0.0.1:3100',
  apiKey: process.env['PAPERCLIP_API_KEY'] ?? '',
  summarizerAgentId: SUMMARIZER_AGENT_ID,
  targetAgentId: '7cc4dafd-b41f-469c-b8ea-7b4110a11fe8',
  targetAgentRole: 'Director of SSI (ssi-hp catalog work, staging audits, recovery triage)',
};

// The 3 historical SSI Director issues to replay.
const REPLAY_ISSUES: Array<{ issueId: string; identifier: string }> = [
  { issueId: '9d26f9c1-de67-48c1-9da0-364806e19129', identifier: 'SAG-2152' },
  { issueId: 'f0520b79-76b2-45f5-9d21-bd7676b4500f', identifier: 'SAG-2067' },
  { issueId: '3ecf9af7-afa6-4b7e-ad63-d15531741e98', identifier: 'SAG-457' },
];

// Exact-match fields — any mismatch counts as drift.
const EXACT_FIELDS: Array<keyof KnowledgeEntry> = [
  'task_id',
  'identifier',
  'title',
  'specialty',
  'domain',
  'outcome',
  'decided_at',
  'digest_model',
  'digest_version',
  'source',
];

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────

function loadBaseline(identifier: string): Partial<KnowledgeEntry> {
  const p = path.join(BASELINES_DIR, `${identifier}.yaml`);
  if (!fs.existsSync(p)) throw new Error(`Baseline not found: ${p}`);
  return yamlParse(fs.readFileSync(p, 'utf8')) as Partial<KnowledgeEntry>;
}

function findWrittenEntry(identifier: string): KnowledgeEntry | null {
  // Walk smoke-output/tasks to find the written YAML.
  const tasksDir = path.join(SMOKE_DIR, 'tasks');
  if (!fs.existsSync(tasksDir)) return null;
  for (const year of fs.readdirSync(tasksDir)) {
    const yearDir = path.join(tasksDir, year);
    for (const month of fs.readdirSync(yearDir)) {
      const p = path.join(yearDir, month, `${identifier}.yaml`);
      if (fs.existsSync(p)) {
        return yamlParse(fs.readFileSync(p, 'utf8')) as KnowledgeEntry;
      }
    }
  }
  return null;
}

function diffEntry(
  identifier: string,
  produced: KnowledgeEntry,
  baseline: Partial<KnowledgeEntry>,
): string[] {
  const drifts: string[] = [];
  for (const field of EXACT_FIELDS) {
    const bVal = baseline[field];
    const pVal = produced[field];
    if (bVal !== undefined && String(bVal) !== String(pVal)) {
      drifts.push(`  ${field}: expected=${JSON.stringify(bVal)} got=${JSON.stringify(pVal)}`);
    }
  }
  // Summary must be present and within cap.
  if (!produced.summary || produced.summary.length === 0) {
    drifts.push(`  summary: missing`);
  } else if (produced.summary.length > 200) {
    drifts.push(`  summary: too long (${produced.summary.length} > 200)`);
  }
  return drifts;
}

// ────────────────────────────────────────────────────────────────────────────
// Main
// ────────────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  if (!config.apiKey) {
    console.error('ERROR: PAPERCLIP_API_KEY not set');
    process.exit(1);
  }

  initDirectories(SMOKE_DIR);
  const summarizer = new PaperclipTaskSummarizer({
    apiUrl: config.apiUrl,
    apiKey: config.apiKey,
    companyId: config.companyId,
    summarizerAgentId: config.summarizerAgentId,
    runId: process.env['PAPERCLIP_RUN_ID'],
  });

  let totalDrift = 0;
  const results: Array<{ identifier: string; drifts: string[]; success: boolean }> = [];

  for (const { issueId, identifier } of REPLAY_ISSUES) {
    console.log(`\n── ${identifier} (${issueId}) ──`);
    const result = await digestIssue(config, summarizer, issueId);

    if (!result.success) {
      console.log(`  ✗ digestion failed: ${result.reason}`);
      results.push({ identifier, drifts: [`digestion_failed: ${result.reason}`], success: false });
      totalDrift++;
      continue;
    }

    const produced = findWrittenEntry(identifier);
    if (!produced) {
      console.log(`  ✗ entry not found in smoke-output after successful digest`);
      results.push({ identifier, drifts: ['entry_not_found'], success: false });
      totalDrift++;
      continue;
    }

    const baseline = loadBaseline(identifier);
    const drifts = diffEntry(identifier, produced, baseline);
    results.push({ identifier, drifts, success: drifts.length === 0 });

    if (drifts.length === 0) {
      console.log(`  ✓ all exact-match fields pass, summary present`);
      console.log(`  summary: "${produced.summary}"`);
    } else {
      console.log(`  ✗ ${drifts.length} field(s) differ:`);
      for (const d of drifts) console.log(d);
      if (drifts.length > 1) totalDrift++;
    }
  }

  console.log('\n══ SMOKE SUMMARY ══');
  for (const r of results) {
    const icon = r.success ? '✓' : '✗';
    console.log(`  ${icon} ${r.identifier}: ${r.success ? 'PASS' : `FAIL (${r.drifts.length} drift(s))`}`);
  }

  if (totalDrift > 0) {
    console.error(`\nSMOKE FAILED — ${totalDrift} issue(s) drifted > 1 field. Tune the prompt before MVP ship.`);
    process.exit(1);
  } else {
    console.log('\nSMOKE PASSED');
  }
}

main().catch((err) => {
  console.error('Unhandled error:', err);
  process.exit(1);
});
