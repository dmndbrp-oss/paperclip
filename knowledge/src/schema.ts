import { z } from 'zod';

export const DomainEnum = z.enum([
  'ssi-hp', 'bd-intel', 'governance', 'runtime', 'pricing', 'ops',
  'hiring', 'security', 'marketing',
]);

export const SpecialtyEnum = z.enum([
  'ssi_director', 'cfo', 'cto', 'coder', 'ea', 'ops_director',
  'pricing_director', 'marketing_director', 'bd_director',
  'qa_unit', 'qa_regression', 'qa_integration', 'ssi_qa', 'pricing_qa',
]);

export const OutcomeEnum = z.enum(['done', 'cancelled', 'escalated']);

export const KnowledgeEntrySchema = z.object({
  task_id: z.string().min(1),
  identifier: z.string().min(1),
  title: z.string().min(1),
  specialty: SpecialtyEnum,
  domain: DomainEnum,
  outcome: OutcomeEnum,
  summary: z.string().min(1).max(200),
  decided_at: z.string().datetime(),
  digest_model: z.string().min(1),
  digest_version: z.number().int().positive(),
  source: z.enum(['digester', 'manual']),
  // optional
  surprises: z.array(z.string().min(1)).max(3).optional(),
  anti_patterns: z.array(z.string().min(1)).max(3).optional(),
  decisions: z.array(z.string().min(1)).optional(),
  files_touched: z.array(z.string().min(1)).max(5).optional(),
  duration_minutes: z.number().int().nonnegative().optional(),
  links: z.array(z.string().min(1)).optional(),
});

export type KnowledgeEntry = z.infer<typeof KnowledgeEntrySchema>;
export type Domain = z.infer<typeof DomainEnum>;
export type Specialty = z.infer<typeof SpecialtyEnum>;
export type Outcome = z.infer<typeof OutcomeEnum>;

export type IndexPointerRow = {
  task_id: string;
  identifier: string;
  specialty: Specialty;
  domain: Domain;
  summary: string;
  anti_patterns?: string[];
  decided_at: string;
};

export type ValidationResult =
  | { success: true; data: KnowledgeEntry }
  | { success: false; errors: string[] };
