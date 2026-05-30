#!/usr/bin/env tsx
import { createInterface } from 'node:readline';
import process from 'node:process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse as yamlParse } from 'yaml';
import { validateEntry, initDirectories, writeEntry } from '../src/index.js';

async function readStdin(): Promise<string> {
  return new Promise((resolve) => {
    const chunks: string[] = [];
    const rl = createInterface({ input: process.stdin, terminal: false });
    rl.on('line', (line) => chunks.push(line));
    rl.on('close', () => resolve(chunks.join('\n')));
  });
}

function resolveBaseDir(): string | null {
  if (process.env.KNOWLEDGE_BASE_DIR) return process.env.KNOWLEDGE_BASE_DIR;
  const companyId = process.env.PAPERCLIP_COMPANY_ID;
  const home = process.env.HOME;
  if (companyId && home) {
    return path.join(home, '.paperclip', 'instances', 'default', 'companies', companyId, 'knowledge');
  }
  return null;
}

function entryFilePath(baseDir: string, entry: { decided_at: string; identifier: string }): string {
  const dt = new Date(entry.decided_at);
  const yyyy = dt.getUTCFullYear().toString();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
  return path.join(baseDir, 'tasks', yyyy, mm, `${entry.identifier}.yaml`);
}

async function main(): Promise<void> {
  const subcommand = process.argv[2];

  if (subcommand !== 'write' && subcommand !== 'validate') {
    process.stderr.write('Usage: knowledge-cli <write|validate>\n');
    process.exit(1);
  }

  const stdin = await readStdin();
  let raw: unknown;

  try {
    raw = yamlParse(stdin);
  } catch (err) {
    process.stderr.write(`YAML parse error: ${err}\n`);
    process.exit(1);
  }

  const result = validateEntry(raw);

  if (!result.success) {
    for (const error of result.errors) {
      process.stderr.write(`${error}\n`);
    }
    process.exit(1);
  }

  if (subcommand === 'validate') {
    process.stdout.write('valid\n');
    process.exit(0);
  }

  // write
  const baseDir = resolveBaseDir();
  if (!baseDir) {
    process.stderr.write(
      'Error: set KNOWLEDGE_BASE_DIR or both HOME and PAPERCLIP_COMPANY_ID\n',
    );
    process.exit(1);
  }

  initDirectories(baseDir);
  writeEntry(baseDir, result.data);

  const writtenPath = entryFilePath(baseDir, result.data);
  process.stdout.write(`${writtenPath}\n`);
  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(`Unhandled error: ${err}\n`);
  process.exit(1);
});
