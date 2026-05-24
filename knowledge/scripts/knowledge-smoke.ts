/**
 * SAG-2187 smoke test — writes 3 fake knowledge entries, lists them by domain.
 * Usage: npx tsx scripts/knowledge-smoke.ts [--base-dir <path>]
 */
import path from 'node:path';
import fs from 'node:fs';
import { initDirectories, writeEntry, readEntriesBySpecialtyAndDomain } from '../src/index.js';
import type { KnowledgeEntry } from '../src/index.js';

const companyId = process.env['PAPERCLIP_COMPANY_ID'] ?? '1dc911ed-ff05-4072-b2ae-a3e3177e3873';
const instanceRoot = process.env['PAPERCLIP_INSTANCE_ROOT'] ?? path.join(
  process.env['HOME'] ?? '/home',
  '.paperclip', 'instances', 'default',
);

const argIdx = process.argv.indexOf('--base-dir');
const baseDir = argIdx !== -1 && process.argv[argIdx + 1]
  ? process.argv[argIdx + 1]!
  : path.join(instanceRoot, 'companies', companyId, 'knowledge');

console.log(`Knowledge base dir: ${baseDir}`);
initDirectories(baseDir);

const ENTRIES: KnowledgeEntry[] = [
  {
    task_id: 'SAG-SMOKE-001',
    identifier: 'SAG-SMOKE-001',
    title: 'Smoke: security vetting run',
    specialty: 'ssi_director',
    domain: 'security',
    outcome: 'done',
    summary: 'Vetted candidate library; rejected due to hidden GPL dep.',
    decided_at: '2026-05-20T10:00:00.000Z',
    digest_model: 'claude-sonnet-4-6',
    digest_version: 1,
    source: 'manual',
    surprises: ['Library had hidden GPL dep'],
    anti_patterns: ['Do not trust star count without contributor audit'],
    duration_minutes: 25,
  },
  {
    task_id: 'SAG-SMOKE-002',
    identifier: 'SAG-SMOKE-002',
    title: 'Smoke: governance policy update',
    specialty: 'ssi_director',
    domain: 'governance',
    outcome: 'done',
    summary: 'Updated tool-trust tiers; board adopted v1.3 ops guide.',
    decided_at: '2026-05-21T14:30:00.000Z',
    digest_model: 'claude-sonnet-4-6',
    digest_version: 1,
    source: 'manual',
    anti_patterns: ['Do not skip board sign-off for Tier 3 installs'],
    links: ['/SAG/approvals/b29048f1-d015-4ab5-b012-e5dc89462ea8'],
  },
  {
    task_id: 'SAG-SMOKE-003',
    identifier: 'SAG-SMOKE-003',
    title: 'Smoke: second security audit',
    specialty: 'ssi_director',
    domain: 'security',
    outcome: 'done',
    summary: 'Weekly audit pass; 2 new Tier-1 proposals accepted.',
    decided_at: '2026-05-22T09:00:00.000Z',
    digest_model: 'claude-sonnet-4-6',
    digest_version: 1,
    source: 'manual',
    duration_minutes: 40,
  },
];

console.log('\nWriting 3 fake entries...');
for (const entry of ENTRIES) {
  writeEntry(baseDir, entry);
  console.log(`  Wrote ${entry.identifier} → ${entry.domain}`);
}

console.log('\nReading back by specialty=ssi_director, domain=security (limit 5):');
const securityEntries = readEntriesBySpecialtyAndDomain(baseDir, 'ssi_director', 'security', 5);
for (const e of securityEntries) {
  console.log(`  [${e.decided_at}] ${e.identifier}: ${e.summary}`);
}
if (securityEntries.length !== 2) {
  console.error(`ERROR: expected 2 security entries, got ${securityEntries.length}`);
  process.exit(1);
}

console.log('\nReading back by specialty=ssi_director, domain=governance (limit 5):');
const govEntries = readEntriesBySpecialtyAndDomain(baseDir, 'ssi_director', 'governance', 5);
for (const e of govEntries) {
  console.log(`  [${e.decided_at}] ${e.identifier}: ${e.summary}`);
}
if (govEntries.length !== 1) {
  console.error(`ERROR: expected 1 governance entry, got ${govEntries.length}`);
  process.exit(1);
}

console.log('\nVerifying index files...');
const domainIdx = path.join(baseDir, 'index', 'by_domain', 'security.jsonl');
const secRows = fs.readFileSync(domainIdx, 'utf8').trim().split('\n').filter(Boolean);
console.log(`  by_domain/security.jsonl has ${secRows.length} pointer row(s)`);
if (secRows.length < 2) {
  console.error('ERROR: expected at least 2 rows in security domain index');
  process.exit(1);
}

console.log('\nSmoke PASSED');
