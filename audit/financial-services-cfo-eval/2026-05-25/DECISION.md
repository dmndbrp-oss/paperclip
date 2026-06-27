# Decision Artifact — anthropics/financial-services CFO eval

- Date: 2026-05-25
- Issue: SAG-2262 (parent SAG-2261, grandparent SAG-2258)
- Author: CFO (16373fdb-4cc1-4ab3-a4f3-4f75e928dd55)
- Catalog source: `__catalog__/anthropics-financial-services--sag2286/SKILL.md` (Apache-2.0, pinned commit `96bc9615`)
- Upstream: https://github.com/anthropics/financial-services @ `96bc9615bccdff61c190cc3e29687f5885bc3929`

## Question

Does adopting Anthropic's first-party FSI plugin agents (GL Reconciler, Statement Auditor, KYC Screener) materially augment the existing Sage Surfaces finance/reconciliation stack — specifically Skill 20 (Lowes IME Audit Reconciliation, BR-92 controller safety net)?

## Cohort / denominator / lens

- Cohort: three named agents in `plugins/agent-plugins/` of the FSI repo (KYC out of scope upfront).
- Denominator: existing Sage finance workflow surface = Skill chain `1 → 1.1 → 3 → 2 → 4 → 6 → 7 → 8 → 9 → 11 → 12 → 15 → 17 → 18 → 19 → 16 → 20` plus QB-import / Dbase-append downstreams.
- Lens applied: domain fit (materiality), unit economics of the workflow (per-run cost vs. value), audit trail discipline (the existing Brain Rules govern), and apples-to-apples (does the upstream agent answer a question we actually ask?).

## What Skill 20 already does

Sage's Skill 20 (`20-lowesime-audit-reconciliation_v1.3.0`) is the controller-grade weekly tie-out workbook (`LowesIme_Audit_Reconciliation_MMDDYYYY.xlsx`) covering:

- Bank deposits ↔ CheckRemits SSI ANALYSIS ↔ AR_AP Audit Tab 2
- Tab 1 = Bank − Excluded; WARRANTY AUDIT DEDUCT-APP ↔ Tab 4 CM ↔ Tab 5 VC
- APPEASEMENT trace (Lowe's gross → Sage Cost → margin)
- Tab 6 CFI Bills ≈ Tab 3 Labor; Tab 7 Sales Receipts internal tie
- 6 output sheets: EXEC SUMMARY, MONEY FLOW, TAB-BY-TAB TIES, DEDUCT-APP TRACE, APPEASEMENT TRACE, EXCLUSIONS WALK
- Variance color coding with $500 red-gate that blocks QB import
- Locked output path per BR-189; Brain Rule 175 governs cross-skill contract for Tab 3 duplicate handling
- Audience: CFO + Owner + Controller + Auditor

## Upstream agent surface

| Agent | Domain | Inputs | Outputs | Skills referenced |
|---|---|---|---|---|
| **GL Reconciler** | Fund accounting — GL ↔ subledger reconciliation per trade date and asset class | `mcp__internal-gl__*`, `mcp__subledger__*` (MCP servers, not files) | Break list, root-cause classification, exception report | `gl-recon`, `break-trace`, `audit-xls`, `xlsx-author` |
| **Statement Auditor** | Private-fund LP capital-account statement validation | NAV pack via `mcp__nav__*`, pre-generated LP statements | Tie-out table, exception list, pass/hold sign-off | `nav-tieout`, `audit-xls`, `xlsx-author` |
| **KYC Screener** | Onboarding KYC rules / counterparty doc parsing | Onboarding docs | Flagged gaps | `kyc-rules`, `kyc-doc-parse` |

All three are designed for **fund administrators / investment banks / wealth-management shops** — not for a manufacturer running a weekly bank-tie workbook into QuickBooks.

## Verdicts

### 1. GL Reconciler — **REJECT**

**Why reject:**

- **Domain mismatch (HARD).** Sage Surfaces operates one ledger (QuickBooks). There is no GL ↔ subledger reconciliation problem to solve. Our reconciliation surface is `Bank ↔ CheckRemits ↔ AR_AP Tabs 1–7`, which is a bank-tie pattern, not a GL-tie pattern.
- **MCP infrastructure missing.** The agent depends on `${GL_MCP_URL}` and `${SUBLEDGER_MCP_URL}` servers we do not run and would not build for this. Standing them up is an engineering project disproportionate to the value, with no driver behind it.
- **`gl-recon` skill is fund-accounting shaped.** Normalization to a "common key (lowest grain both sides share)," timing/FX/account-mapping bucket classification — none of these arise in our Lowes IME weekly chain. We tie bank deposits to a customer-specific deduction workflow, not trades to a back office.
- **`break-trace` skill lens — overlap already covered.** Our Brain Rules (BR-92, BR-189, BR-365/366/368) plus R7a's "33 duplicate ROs surfaced with 3 example numbers" already trace breaks to source in our domain. The "⟨side⟩ ⟨did what⟩ because ⟨reason⟩" sentence format is a nice writing standard but does not augment what Skill 20 already produces.
- **`audit-xls` skill — value lives outside our workflow.** It validates *hand-built* financial models (DCF, LBO, 3-statement). Skill 20 emits a workbook from a Python builder; there are no hand-keyed formulas to audit. The audit-xls value would land on board-facing models, which is rare and ad-hoc, not a stack we need wired in.

**Materiality check:** Building MCP servers + adopting the orchestrator/critic/resolver pattern just to gain `break-trace`'s sentence-template would be chasing a $50 line item in a $5M decision. Skill 20 with its $500 red-gate is already controller-grade for our scale.

**Re-trigger conditions:** Revisit only if Sage Surfaces (a) stands up a second ledger requiring inter-ledger reconciliation, or (b) takes on a workflow where the unit of work is "trades and breaks per trade date" rather than "weekly bank-deposit tie."

### 2. Statement Auditor — **REJECT**

**Why reject:**

- **Domain mismatch (HARD).** This agent's job is "last set of eyes on LP capital-account statements before they leave the firm." Sage Surfaces does not issue LP statements, does not manage outside investors' capital, and does not produce a NAV pack. The `nav-tieout` formula is literally `beginning capital + contributions − distributions + allocated P&L − carried interest` — not applicable.
- **No NAV MCP / no statement batch.** Inputs (`mcp__nav__*`, statement batch ID, pre-generated LP statements) do not exist in our environment.
- **Tie-out + pass/hold pattern already covered.** Skill 20's EXEC SUMMARY sheet plus variance color coding plus the $500 → block-QB-import gate IS the pass/hold pattern, in our domain. We do not need a second one in a domain we do not operate in.

**Re-trigger conditions:** Only if Sage Surfaces ever managed an outside fund / LP investor base (not on any roadmap I am aware of).

### 3. KYC Screener — **REJECT** (reaffirmed)

Already rejected on parent SAG-2261. Sage Surfaces is a surfaces/materials manufacturer with no KYC obligation. No re-trigger condition envisioned.

## Transferable architectural patterns (informational only — not a CFO ADOPT)

The agent-plugin write-isolation pattern (orchestrator + reader-with-no-write + critic + resolver-as-sole-writer) is a defensible engineering posture for any agent that emits financial artifacts. If the CTO ever rebuilds the Skill 20 chain as an agent-driven flow (instead of a pinned-Python builder), this is a sound reference architecture. That is a CTO/engineering reuse, not a finance-stack adoption, and does not warrant a child issue from this CFO eval. Filed here as an FYI only.

## Recommendation

Close SAG-2262 `done` with all three verdicts REJECT. No wiring child issues filed under Data Analyst. Record re-trigger conditions in this artifact so future repo sweeps do not re-surface these agents absent a real domain change.

## Audit trail

- Catalog SKILL.md: `__catalog__/anthropics-financial-services--sag2286/SKILL.md`
- Upstream commit: `96bc9615bccdff61c190cc3e29687f5885bc3929`
- Skill 20 SKILL.md: `agents/16373fdb-4cc1-4ab3-a4f3-4f75e928dd55/work/SAG-1459/unpacked/20-lowesime-audit-reconciliation_v1.3.0/20-lowesime-audit-reconciliation/SKILL.md`
- Skill 20 builder: same tree, `scripts/audit_reconciliation_builder.py`
- Parent context: SAG-2261, SAG-2258
