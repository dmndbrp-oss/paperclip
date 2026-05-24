export {
  KnowledgeEntrySchema,
  DomainEnum,
  SpecialtyEnum,
  OutcomeEnum,
  type KnowledgeEntry,
  type Domain,
  type Specialty,
  type Outcome,
  type IndexPointerRow,
  type ValidationResult,
} from './schema.js';

export {
  initDirectories,
  writeEntry,
  readEntriesBySpecialtyAndDomain,
  validateEntry,
} from './store.js';
