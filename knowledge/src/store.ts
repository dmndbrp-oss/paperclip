import fs from 'node:fs';
import path from 'node:path';
import { stringify as yamlStringify, parse as yamlParse } from 'yaml';
import {
  KnowledgeEntrySchema,
  type KnowledgeEntry,
  type Domain,
  type Specialty,
  type IndexPointerRow,
  type ValidationResult,
} from './schema.js';

function ensureDir(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
}

function entryPath(baseDir: string, entry: KnowledgeEntry): string {
  const dt = new Date(entry.decided_at);
  const yyyy = dt.getUTCFullYear().toString();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
  return path.join(baseDir, 'tasks', yyyy, mm, `${entry.identifier}.yaml`);
}

function domainIndexPath(baseDir: string, domain: Domain): string {
  return path.join(baseDir, 'index', 'by_domain', `${domain}.jsonl`);
}

function specialtyIndexPath(baseDir: string, specialty: Specialty): string {
  return path.join(baseDir, 'index', 'by_specialty', `${specialty}.jsonl`);
}

function toPointerRow(entry: KnowledgeEntry): IndexPointerRow {
  return {
    task_id: entry.task_id,
    identifier: entry.identifier,
    specialty: entry.specialty,
    domain: entry.domain,
    summary: entry.summary,
    ...(entry.anti_patterns ? { anti_patterns: entry.anti_patterns } : {}),
    decided_at: entry.decided_at,
  };
}

function appendJsonl(filePath: string, row: IndexPointerRow): void {
  ensureDir(path.dirname(filePath));
  fs.appendFileSync(filePath, JSON.stringify(row) + '\n', 'utf8');
}

function readJsonlRows(filePath: string): IndexPointerRow[] {
  if (!fs.existsSync(filePath)) return [];
  const lines = fs.readFileSync(filePath, 'utf8').trim().split('\n').filter(Boolean);
  return lines.map((l) => JSON.parse(l) as IndexPointerRow);
}

export function initDirectories(baseDir: string): void {
  ensureDir(path.join(baseDir, 'tasks'));
  ensureDir(path.join(baseDir, 'index', 'by_domain'));
  ensureDir(path.join(baseDir, 'index', 'by_specialty'));
}

export function entryExists(baseDir: string, identifier: string, decidedAt: string): boolean {
  const dt = new Date(decidedAt);
  const yyyy = dt.getUTCFullYear().toString();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const yamlPath = path.join(baseDir, 'tasks', yyyy, mm, `${identifier}.yaml`);
  return fs.existsSync(yamlPath);
}

export function writeEntry(baseDir: string, entry: KnowledgeEntry): void {
  const yamlPath = entryPath(baseDir, entry);
  ensureDir(path.dirname(yamlPath));
  fs.writeFileSync(yamlPath, yamlStringify(entry), 'utf8');

  appendJsonl(domainIndexPath(baseDir, entry.domain), toPointerRow(entry));
  appendJsonl(specialtyIndexPath(baseDir, entry.specialty), toPointerRow(entry));
}

export function readEntriesBySpecialtyAndDomain(
  baseDir: string,
  specialty: Specialty,
  domain: Domain,
  limit: number,
): KnowledgeEntry[] {
  const rows = readJsonlRows(specialtyIndexPath(baseDir, specialty))
    .filter((r) => r.domain === domain)
    .sort((a, b) => new Date(b.decided_at).getTime() - new Date(a.decided_at).getTime())
    .slice(0, limit);

  return rows.flatMap((row) => {
    // Find the YAML file: scan year/month dirs since we know decided_at
    const dt = new Date(row.decided_at);
    const yyyy = dt.getUTCFullYear().toString();
    const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
    const yamlPath = path.join(baseDir, 'tasks', yyyy, mm, `${row.identifier}.yaml`);
    if (!fs.existsSync(yamlPath)) return [];
    const raw = yamlParse(fs.readFileSync(yamlPath, 'utf8'));
    const parsed = KnowledgeEntrySchema.safeParse(raw);
    return parsed.success ? [parsed.data] : [];
  });
}

export function validateEntry(raw: unknown): ValidationResult {
  const result = KnowledgeEntrySchema.safeParse(raw);
  if (result.success) {
    return { success: true, data: result.data };
  }
  return {
    success: false,
    errors: result.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`),
  };
}
