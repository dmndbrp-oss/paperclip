/**
 * Production runner for routine c82813c2 ("SSI Director knowledge digest").
 *
 * Called by the Researcher agent (1e0167fe) when the routine fires.
 * Polls for new done issues assigned to the SSI Director since the last run,
 * digests them via the summarizer agent, and writes YAML entries to the
 * company knowledge base.
 *
 * Usage:
 *   npx tsx scripts/digester-run.ts
 *
 * Required env vars:
 *   PAPERCLIP_API_KEY
 * Optional env vars:
 *   PAPERCLIP_API_URL   (default: http://localhost:3100)
 *   PAPERCLIP_COMPANY_ID (default: 1dc911ed-ff05-4072-b2ae-a3e3177e3873)
 */

import path from 'node:path';
import { runDigester } from '../src/digester.js';
import type { DigesterConfig } from '../src/digester.js';

const companyId = process.env['PAPERCLIP_COMPANY_ID'] ?? '1dc911ed-ff05-4072-b2ae-a3e3177e3873';
const home = process.env['HOME'] ?? '/root';
const baseDir = path.join(home, '.paperclip', 'instances', 'default', 'companies', companyId, 'knowledge');

const config: DigesterConfig = {
  companyId,
  apiUrl: process.env['PAPERCLIP_API_URL'] ?? 'http://localhost:3100',
  apiKey: process.env['PAPERCLIP_API_KEY'] ?? '',
  summarizerAgentId: '11d0b5de-44a9-4f05-b130-846804e29955',
  targetAgentId: '7cc4dafd-b41f-469c-b8ea-7b4110a11fe8',
  targetAgentRole: 'Director of SSI (ssi-hp catalog work, staging audits, recovery triage)',
  baseDir,
  stateFile: path.join(baseDir, 'digester-state.json'),
  failureLogFile: path.join(baseDir, '_digestion_failures.jsonl'),
};

async function main(): Promise<void> {
  if (!config.apiKey) {
    console.error('ERROR: PAPERCLIP_API_KEY not set');
    process.exit(1);
  }

  await runDigester(config);
}

main().catch((err) => {
  console.error('Unhandled error:', err);
  process.exit(1);
});
