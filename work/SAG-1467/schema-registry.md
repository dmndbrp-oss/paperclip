# Schema Registry — Lowe's IME / MSI (Phase 1A)

Static-analysis registry of every shared workbook, tab, and named column referenced by the 28 Cowork skill packages.

- **Source artifacts:** `work/SAG-1459/unpacked/` — 28 skill packages, 52 Python files, 35,416 lines, 28 `SKILL.md` files (Bash recipes ignored; only `.py` + `SKILL.md` parsed).
- **Method:** static extraction (`work/SAG-1467/extract_schema.py`). Pulls workbook filename literals (`*.xlsx`/`*.xlsm`/`*.csv`), tab strings (`sheet_name=`, `wb[...]`, `book[...]`, `.parse(...)`, `create_sheet(...)`, `ws.title = ...`), and column-header strings (`ws.cell(row=1, column=N, value="...")`, header-list constants, `cell.value == "..."`, `df["..."]`, `df.loc[..., "..."]`). All script literals only — **no live workbook was opened, no production data extracted**.
- **Denominator:** workbook → tab → column. Cross-skill counts pivot on uniquified basenames (dated suffixes collapsed where the prefix is stable).
- **Lens applied:** denominator discipline (BR-178 failure mode) + audit trail (file + line range) + survivorship (legacy-fallback aliases are first-class citizens of the registry, not edge cases).
- **Confidence:** workbook inventory (§2) and tab map (§3) are high. Column map (§4) is partial — header writes via `ws.cell(row=1, ...)` are reliable, but column reads via `_build_header_map(...)` lookups and openpyxl letter access (`ws['B'+str(row)].value`) carry the column letter, not the header literal. §5 collects the known-incomplete cases as open questions.
- **Hard rule compliance:** registry contains no customer names, employee names, vendor numbers, bank account numbers, or sample data values. Header *labels* are listed; row values are not.

---

## 1. Scope reminder

In scope (per issue description, Plan §6.1):

- **Lowe's IME weekly chain (23 skills):** 0, 1, 1.1, 2, 3, 4, 5, 6, 6.5, 7, 8, 9, 11, 12, 15, 16, 17, 18, 19, 20, 21, 22, 23.
- **MSI quarterly chain (3 skills):** 28, 29, 30.
- **Periodic utilities (2 skills):** 26 (CFI vendor mining), 27 (MSI invoice PDF parse).

Out of scope: SSI portal HTML, QB import format, OneDrive permissions, agent-runtime concerns (Phase Mapping / Phase 2).

Skills 0 and 1 ship with `SKILL.md` only (no `.py`) — they're Outlook/preflight orchestration. Skill 23 ships compiled to no callable `.py` for `wb.save` — it produces a view, not data. Reflected in §3 by "no scripted shared-workbook write".

---

## 2. Workbook inventory

OneDrive anchor (every skill): `~/Library/CloudStorage/OneDrive-SageSurfaces/Claude/`. Filenames are the OneDrive basenames the scripts open or write; dated variants (`MMDDYY`, `MMDDYYYY`) collapsed unless the script's literal carries the date.

| # | Workbook | Cadence | Created by | Mutated by | Read by | Notes |
|---|----------|---------|------------|------------|---------|-------|
| W1 | `Settlement_Customer.xlsx` | weekly (1, 1.1) | Skill 1 (Outlook APPEASE), Skill 1.1 (WorkOrder) | 1, 1.1 | 6, 7, 12, 15, 21 | Two named tabs (`APPEASE`, `WorkOrder`); read by 6 for appeasement cross-ref, by 7 for warranty filter, by 15 for Tab 3 enrichment, by 12 for vendor-credit lookups. |
| W2 | `Lowes_IME_CheckRemits<MMDDYY>.xlsx` | weekly | Skill 3 (Outlook → CheckRemits) | 3, 4, 6, 6.5, 7, 8, 11, 12, 15, 16, 17, 18, 19, 20, 22 | 21, 22, 26 | THE central weekly workbook. Skills 7+ build named tabs into it; Skill 22 SCD2-appends from it; Skill 20 reads it for tie-out. Date suffix is positional. |
| W3 | `LowesIme AR_AP Audit<MMDDYYYY>.xlsx` | weekly | Skill 7 (`1LowesIme AR_SSI ChkRemit`) | 7, 8, 9, 11, 12, 15, 18, 19, 20 | 22, 20, 9 | The ERP upload workbook. Each downstream skill writes its named tab into the same workbook (Skill 8 = Tab 2, Skill 15 = Tab 3, Skill 11 = Tab 4, Skill 12 = Tab 5, Skill 18 = Tab 6, Skill 19 = Tab 7). Skill 9 reads it back to drive uploads. |
| W4 | `LowesIme_AR_AP_Active.xlsx` | weekly | Skill 17 (SP freeze) + Skill 6.5 (Suffix Ledger) | 17, 6.5, 18 | 18, 20, 21 | Hot small workbook (<1MB) for live state. Tabs: `ServiceProvider AP Hold` / `SP AP Hold` (alias — see §6.A), `Suffix Ledger`, `_STATUS_`. |
| W5 | `LowesIme AR_AP Dbase.xlsx` | weekly write, historical | Skill 22 (SCD2 append) | 22 | 6 (suffix), 6.5 (suffix), 23 (operational view), 26 (CFI mining) | The SCD2 historical store. 11 named-tab targets driven by Skill 22 `TAB_MAP`; supersession enforced by Skill 22. ~60MB file (per Skill 17 commentary). |
| W6 | `CFI_Vendor_Reference.xlsx` | ad-hoc (Skill 26 refresh) | Skill 26 | 26 | 4, 6, 12, 15, 17, 18, 23 | Vendor mapping. One named tab (`Vendor Reference`); periodic refresh by Skill 26 from Dbase mining. Used as a lookup *only*. |
| W7 | `SSI_Lowes_Order_Details.xlsx` | weekly | Skill 2 (SSI portal extract) | 2 | 4 (Skill 4 reads `SSI RO DATA`) | Single named tab `SSI RO DATA`. |
| W8 | `SSI_AUDIT_Master.xlsx` | weekly snapshot | Skill 16 (snapshot from W2) | 16 | 21 (FILE ROADMAP only) | Historical archive; never read by the chain after snapshot. |
| W9 | `SSI_ID_Mappings.xlsx` | static lookup | (manual) | none | 2 | RO ↔ Project ID xref used by Skill 2 only. |
| W10 | `Retail_Orders*.xlsx` | static lookup (manual upload) | (manual) | none | 2 | Lowe's retail order index. |
| W11 | `MSI_Invoice_Master.xlsx` | ad-hoc (Skill 27 batch) | Skill 27 (PDF parse) | 27 | (downstream MSI chain — likely 28/30 via path, no literal `read_excel` found) | Output of MSI invoice ingest. |
| W12 | `Freight_Type_Assignments.xlsx` | quarterly | Skill 30 | 30 | 28 | MSI freight reference. |
| W13 | `Sales_Tax_Reference.xlsx` | static lookup | (manual) | none | 30 | Used by Skill 30 `Tax by State` builder. |
| W14 | `MSI_Audit_HAUSPRO_<MMDDYYYY>.xlsx` (and per-MFG fresh files) | quarterly | Skill 30 | 30 | (sign-off doc artifact) | Skill 30 produces one per manufacturer; final sign-off via `MSI_MASTER_SIGNOFF_PLAN.md`. |
| W15 | `LowesIme_Audit_Reconciliation_<MMDDYYYY>.xlsx` | weekly (Skill 20 output) | Skill 20 | 20 | (Controller — Anita) | Controller tie-out workbook with 8 indexed tabs (see §3). Sign-off artifact. |
| W16 | `Audit_Process*.xlsx` | weekly (Skill 9 working file) | Skill 9 | 9 | 9 | Single-tab temp files for the 3-step SSI portal upload — created in `BR-182` LOCKED LOCATION. |
| W17 | `*remittances*.csv` | weekly inbound | (Outlook) | none | 3 | Source CSVs Skill 3 parses into W2. |
| W18 | `*.xlsx` (glob — variable per session) | weekly | Skills 4, 5 | 4, 5 | 4, 5 | Skill 4 glob loads downstream of CheckRemits; Skill 5 closed-order lookups. Path-built; not a literal workbook target. |

**Workbook count: 18 distinct workbooks** (collapsing dated suffixes). Of these, **6 are mutated by ≥2 skills** (W2, W3, W4, W5 most acutely) — these are the BR-178 fragility surface and the registry's primary subject.

---

## 3. Per-workbook tab map

For each shared workbook, every observed tab, the skill that owns the write, and the skills that read it. Read/write classification is per-skill based on which `*.py` mentions the tab string in proximity to a read API (`read_excel`/`load_workbook`/`.parse`) vs a write API (`to_excel`/`ws.cell`/`wb.save`/`create_sheet`).

### W1 `Settlement_Customer.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `APPEASE` | 1 | 6 (warranty cross-ref), 12 (negative-appease lookup), 15 (Tab 3 audit) | weekly | Sole authority: Skill 1. |
| `WorkOrder` | 1.1 | 7 (Tab 1 exclusion filter — `--warranty`), 15 (Tab 3 audit) | weekly | Sole authority: Skill 1.1. |

### W2 `Lowes_IME_CheckRemits<MMDDYY>.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `IME LOWESLINK` | 3 (initial build), 4 (enrichment), 6 (Recon Status, Suggested Acctg Invoice cols X/Y/Z, Rule(s) Applied col AK) | 3, 4, 5, 6, 7, 8, 11, 15, 17, 18, 20, 26 | weekly | **The canonical line-item table** — touched by 12 skills. The single largest BR-178 surface in the chain. |
| `SSI AUDIT` | 3 (init), 4 (refresh) | 6, 7 (sub-row lookups), 8 (filter) | weekly | |
| `SSI ANALYSIS` | 3 | 4, 6, 8 | weekly | |
| `WARRANTY AUDIT` | 6 | 11 (CM lane), 12 (VC lane), 15 (Tab 3 audit), 18 (CFI bills), 20, 22 (SCD2 source) | weekly | Mutated only by Skill 6. Six sections inside it: APPEASEMENT, DEDUCT-APP, SHORT PAY, OVERPAID, RECONCILED CANCELLATION, PAYMENT EXCEPTIONS, + (v4.40.5) `POSSIBLE APPEASEMENT MATCH`. |
| `SP PAYMENT FREEZE` | 4 (write), 17 (consumer + writes back) | 11, 12, 15 (Tab 3 audit), 17 (read), 18 | weekly | Read by 11/12 to suppress frozen RefNumbers from CM/VC lanes. |
| `POSSIBLE APPEASE MATCH` | 6 (v4.40.5+ only; suppressed if zero hits) | 15 (Tab 3 audit) | weekly | Optional — not always present in the workbook. |
| `RECONCILED CANCELLATION` | 6 (v4.40.4+) | (consumer-side surfacing via WA section) | weekly | Surfacing-only; routing happens upstream. |
| `Claude Improve` | 3 | (manual) | weekly | Skill 3 internal flag tab; not consumed downstream. |
| `CLAUDE AUDIT` | 3, 8 | 8, 9 | weekly | Skills 3 + 8 both write. Cross-skill mutation — see §6.E. |
| `_STATUS_` | 11, 12, 17, 18 | 17, 18, 21 | weekly | Last-run timestamp + freeze count. Multiple writers; ad-hoc state file. |

### W3 `LowesIme AR_AP Audit<MMDDYYYY>.xlsx`

| Tab | Owner | Writers | Readers | Cadence | Notes |
|-----|-------|---------|---------|---------|-------|
| `1LowesIme AR_SSI ChkRemit` | Skill 7 | 7 | 9 (upload Step 1), 22 (SCD2 read), 20 (tie-out) | weekly | 6-col base + cols H–M audit (per Skill 7 SKILL.md §"Tab 1 right side"). |
| `2LowesIme SSIAuditRelease` (alias seen: `2LowesIme SSIAudit Release`, `2LowesIme AuditRelease`) | Skill 8 | 8 | 9 (upload Step 2), 15 (Tab 3 enrichment), 22 (SCD2 read) | weekly | 11-col template (see §4-T2). Alias `2LowesIme AuditRelease` is the **legacy short name** used in fallback paths in Skills 6, 6.5, 22. |
| `3LowesIME SSIAudit` (alias: `3LowesIme ArApAudit` per Skill 22 `TAB_MAP`) | Skill 15 / SSI portal | 15 | 9 (Tab 3 retrieval pulldown), 20 (tie-out) | weekly | Skill 15 enriches; Skill 22 reads it under the **legacy** name `3LowesIme ArApAudit`. **Active divergence** — see §6.B. |
| `4LowesIme CreditMemo` | Skill 11 | 11 | 12 (CM total in VC reconciliation), 19 (sales receipts offset), 22 | weekly | |
| `5LowesIme VendorCredit` | Skill 12 | 12 | 22 | weekly | |
| `6LowesIme CFIBills` (Skill 22 splits: `tab6_active` + `tab6_hold`) | Skill 18 | 18 | 20, 22 (SCD2 — splits into `6 LowesIme SPBill` + `SP AP Hold` per `TAB_MAP`) | weekly | Single weekly tab, splits into two Dbase tabs (live + hold). |
| `7LowesIme SalesReceipts` (Skill 22 alias: `7LowesIme SR`) | Skill 19 | 19 | 22 | weekly | |

### W4 `LowesIme_AR_AP_Active.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `ServiceProvider AP Hold` / `SP AP Hold` (alias — same logical tab) | 17 (append), 6.5 (read), 18 (read) | 6.5 (Suffix Source 3, Rule 161), 17 (refresh status), 18 (freeze suppression), 20 (tie-out) | weekly | **Renamed by Jen on 2026-04-29 (BR-190 Option C)**: `ServiceProvider AP Hold` → `SP AP Hold`. Skills 6.5 and 17 carry the legacy fallback (`AP_HOLD_TAB_CANDIDATES = ("SP AP Hold", "ServiceProvider AP Hold")`); **Skill 18 still hardcodes `ServiceProvider AP Hold` and skips freeze suppression on miss** — see §6.A. |
| `Suffix Ledger` | 6.5 (sole authority) | (controller-visible audit only) | weekly | Visible audit ledger; Brain Rule 161 surfaced this. |
| `_STATUS_` | 17, 18 | 17 | weekly | |

### W5 `LowesIme AR_AP Dbase.xlsx`

Authoritative target map from Skill 22 `dbase_append.py:387` (TAB_MAP). New tab names (post-BR-178) are the writers; legacy names are fallback readers retained for warranty-audit suffix calc.

| Dbase tab (current) | Writer | Readers | Source tab in W3/W2 | Legacy alias still queried? |
|---------------------|--------|---------|---------------------|-----------------------------|
| `2LowesIme AuditRelease` | 22 | 6 (suffix payment-side, primary), 23, 26 | W3 `2LowesIme SSIAuditRelease` | Yes — `DealerPaymentsMaster` / `Dealer Payments-Master` / `Dealer Payments Master` (Skill 6 fallback list, 3 spellings). |
| `3LowesIME SSIAudit` | 22 | 23 | W3 `3LowesIme ArApAudit` | n/a |
| `6 LowesIme SPBill` | 22 | 23 | W3 `6LowesIme CFIBills` (active rows) | n/a |
| `SP AP Hold` | 22 | 6.5, 23 | W3 `6LowesIme CFIBills` (hold rows) | Yes — `ServiceProvider AP Hold` on W4 alias. |
| `7LowesIme SR` | 22 | 23 | W3 `7LowesIme SalesReceipts` | n/a |
| `4LowesIME CM` | 22 | 6 (suffix CM-side, primary), 23 | W2 `WARRANTY AUDIT` (wa_deduct_cm filter) | Yes — `CreditMemo` / `Credit Memo` / `Dealer Deductions` (Skill 6 fallback list, 3 spellings). Skill 6 also handles `4LowesIME CM` vs `4LowesIme CM` casing variants. |
| `5LowesIme VC` | 22 | 23 | W2 `WARRANTY AUDIT` (wa_deduct_vc filter) | n/a |
| `Appeasements` | 22 | 26 | W2 `WARRANTY AUDIT` (wa_appeasement filter) | n/a (Skill 6 comment: "Appeasements tab removed — duplicate of DealerPaymentsMaster data"). |
| `Short Payments` | 22 | 23 | W2 `WARRANTY AUDIT` (wa_shortpay filter) | n/a |
| `Overpaid` (v1.0.2+) | 22 | 23 | W2 `WARRANTY AUDIT` (wa_overpaid filter) | n/a — newly added 2026-05-12. |
| `Payment Exceptions` (v1.0.2+) | 22 | 23 | W2 `WARRANTY AUDIT` (wa_payment_exc filter) | n/a — newly added 2026-05-12. |

### W6 `CFI_Vendor_Reference.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `Vendor Reference` | 26 | 4, 6, 12, 15, 17, 18, 23 | ad-hoc | The single-point-of-failure vendor mapping. SP Vendor Number propagates to every chain that pays. |
| `Appeasements` | 26 | (mining only) | ad-hoc | Skill 26 mining intermediate. |

### W7 `SSI_Lowes_Order_Details.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `SSI RO DATA` | 2 | 4 | weekly | Single tab. |
| `REVIEW` | 2 | (manual) | weekly | Internal QA tab. |

### W8 `SSI_AUDIT_Master.xlsx`

| Tab | Writers | Readers | Cadence | Notes |
|-----|---------|---------|---------|-------|
| `SSI AUDIT Master` | 16 | (none — archive) | weekly | Historical snapshot only. |

### W15 `LowesIme_Audit_Reconciliation_<MMDDYYYY>.xlsx`

Single-writer (Skill 20), Controller-facing tie-out. Eight sequentially numbered tabs — preserving the numeric prefix is load-bearing for the Controller's read order. Tab names are first-class identifiers in the script:

1. `1. EXEC SUMMARY`
2. `2. MONEY FLOW`
3. `3. TAB-BY-TAB TIES`
4. `4. DEDUCT-APP TRACE`
5. `5. APPEASEMENT TRACE`
6. `6. EXCLUSIONS WALK`
7. `7. VARIANCE DRILL-DOWN`
8. `8. SOURCE TRACEABILITY`

### W14 / Skill 30 MSI quarterly workbook (sample: `MSI_Audit_HAUSPRO_04062026.xlsx`)

Skill 30 emits a single workbook per manufacturer with these tabs (all single-writer, single-reader — sign-off chain `MSI_MASTER_SIGNOFF_PLAN.md`):

- `Executive Summary`
- `Audit Health & Data Gaps`
- `MSI Audit vs Sage Reality`
- `Branch Audit Report`
- `Unmatched POs`
- `MSI-Data`
- `CheckNumAmt`
- `DQ - SKU Cleanup`
- `Tax by State`
- `Freight Groups` (shared with Skill 28)
- `Freight Rate Gaps` (shared with Skill 28)
- `PO Audit Master` (shared with Skill 28)
- `Sales Tax` (referenced via W13 `Sales_Tax_Reference.xlsx`)

### W21 / Skill 21 `Weekly Output Map`

Skill 21 ships an operational doc workbook with `FILE ROADMAP`, `README`, `Weekly Review Checklist`. View only — never written by another skill.

### Other tabs surfacing in scripts but tied to one skill only

- `Suffix Ledger` (W4, Skill 6.5)
- `_INFO` (Skill 23 operational view)
- `Vendor Reference` already in W6
- `WorkOrder` already in W1

---

## 4. Per-tab column map

This section enumerates header literals captured from `ws.cell(row=1, column=N, value="...")` writes and header-equality compares in the source. Where the source uses positional column letters only (`ws['B'+str(row)]`), the column is captured as "Col `<letter>`" with the source description from `SKILL.md`. Data types are observed from the script (`int`, `str`, money/`Decimal`, date) — not from live workbook content.

### 4-T1 `IME LOWESLINK` (W2)

The widest cross-skill surface. Skill 4 builds the base 33-column unified layout; Skill 6 appends flag columns X–AK. Column letter positions are anchored — header writes happen at known column numbers.

| Col | Header | Produced by | Consumed by | Type | Notes |
|-----|--------|-------------|-------------|------|-------|
| (anchor) | `Project #` | 3 | 4, 6, 8, 11, 12, 15, 18, 20 | str | **Alias surface:** Skill 12 also references `Project Number` and Skill 4 references `Project_Number` — see §6.C. |
| (anchor) | `Invoice Number` | 3 | 4, 6, 8 | str | Used as join key; Skill 11/12 refer to it as `Invoice #` (alias §6.C). |
| (anchor) | `Net_Amount_Paid` | 3 | 4, 6, 18 (`Net Amount`) | money | Alias surface — see §6.C. |
| (anchor) | `Check Number` | 3 | 7, 8 (`Check #`) | str | Alias §6.C. |
| (anchor) | `Check_Date` | 3 | 7, 8 | date | |
| (anchor) | `Store Number` | 3 | 4, 8 | str | |
| (anchor) | `Service Provider` | 3 | 4, 6, 12, 17, 18 | str | |
| col 10 | `SP Vendor Number` | 4 (v4.44.0) | 6, 18 | str | v4.44.0 added; sourced from W6 `Vendor Reference`. |
| (anchor) | `Customer` | 3 | 7, 11 | str | |
| (anchor) | `IME Status` | 3 / 4 | 4 | str | |
| (anchor) | `Recon Status` | 4 | 6 (drives classify), 15, 18, 20 | str | Values include `Balanced`, `Overpaid`, `Underpaid`, `Reconciled - Sub Canceled`, `No Pay`. |
| (anchor) | `SSI Match Count` | 4 | 6 | int | |
| (anchor) | `Region` | 3 | 4 | str | |
| (anchor) | `Dealer City` | 3 | 4 | str | |
| (anchor) | `Dealer State` | 3 | 4 | str | |
| (anchor) | `Product Line` | 3 | 4 | str | |
| (anchor) | `Status` | (varies) | 4, 17 (`RO Status`) | str | Alias §6.C. |
| col X (24) | `Prior Entries` | 6 (blank pre-Skill-6.5), 6.5 (filled) | 11, 12 | int | Brain Rule 180 contract. |
| col Y (25) | `Suffix` | 6 (blank pre-6.5), 6.5 (filled) | 11, 12 | str | Brain Rule 180 contract. |
| col Z (26) | `Suggested Acctg Invoice` (also referenced as `Suggested Invoice`) | 6 (writes effective_invoice no suffix), 6.5 (appends suffix) | 7, 8, 11, 12, 15 | str | Alias §6.C — Skills 11/12 refer to it as `Suggested Invoice`. |
| col AA (27) | `Accounting` | 6 | 4 (`3Audit: Accounting`), 7, 8, 11, 12, 15, 18 | str | Values: `APPEASEMENT`, `DEDUCT-APP`, `SHORT PAY`, `OVERPAID`, `RECONCILED CANCELLATION`, `PAYMENT EXCEPTION`. |
| col AB (28) | `Investigation Category` | 6 | 11, 12, 15, 20 | str | 14 outcomes (per `RULE_FOR_INVEST_CAT`). |
| col AK (37) | `Rule(s) Applied` | 4 (initial), 6 (append, idempotent via `_apply_rule()`) | 6, 15, 20 | str | Comma-separated; Brain Rule 175. Append-only contract. |

(Additional columns present but not captured by header-write extraction — Skill 4 uses `_build_header_map(ws, required)` lookups; the column header literal is in the header-row of the live file. Items where script-side references reveal the label: `Hold Reason`, `Action Required` (Skill 4 / 6), `3Audit: Action Required`, `3Audit: Order Status`, `3Audit: Labor $` (Skill 18 cross-tab refs).)

### 4-T2 `2LowesIme SSIAuditRelease` / `2LowesIme AuditRelease` (W3 / W5)

11-column ERP upload template (Skill 8 `SKILL.md` §"Column Mapping"). High-confidence — these are the literal headers Skill 8 writes.

| Col | Header | Source (from IME LOWESLINK) | Type |
|-----|--------|-----------------------------|------|
| A | Invoice Number | Invoice Number | str |
| B | Invoice Date | Check_Date | date |
| C | Due Date | Check_Date | date |
| D | Invoice Amount | SSI Invoiced (split for I-/P-) | money |
| E | Store Number | Store Number | str |
| F | PO Number | Invoice Number (or `Suggested Acctg Invoice` for appeasements) | str |
| G | Check Number | Check Number | str |
| H | Check Amount | Net_Amount_Paid (split for I-/P-) | money |
| I | Check Date | Check_Date | date |
| J | Discount | const 0 | money |
| K | Comments | blank | str |

### 4-T3 `1LowesIme AR_SSI ChkRemit` (W3) — Tab 1

6-base columns + Skill 7 audit panel (cols H–M).

| Col | Header | Source | Type |
|-----|--------|--------|------|
| A | Customer | from IME LOWESLINK | str |
| B | Invoice # | Invoice Number | str |
| C | Date Pd | Check_Date | date |
| D | Check # | Check Number | str |
| E | Paid | Net_Amount_Paid | money |
| F | Accounting | from `Suggested Acctg Invoice` / Accounting (mode A or B per Skill 4 v3.29.1) | str |
| H | Check # (audit) | | str |
| I | IME Check Total | | money |
| J | DEDUCT-APP Excl | | money |
| K | APPEASE Excl | | money |
| L | BR-001 Frozen | | money |
| M | Expected / Tab 1 Posted / Variance / Status (per-row purpose varies by section) | | mixed |

Reconciliation panel structure documented in Skill 7 `SKILL.md` §"CHECK RECONCILIATION".

### 4-T4 `4LowesIme CreditMemo` (W3) — Tab 4 (Skill 11)

Captured from Skill 11 column writes / equality compares:

`RefNumber`, `Customer`, `Transaction Date`, `Item`, `Price`, `Class`, `Memo`, `PO Number`, `Project #`, `Invoice #`, `Check #`, `Check Date`, `Net Amount`, `Lowes IME`, `Suggested Invoice`, `Hold Reason`, `Investigation Category`, `Returns Dealer`, `Type`, `Credit Memo`, `Other`, `Other1`, `Other2`. (See §6.C for naming-divergence flags vs Tab 5 and IME LOWESLINK.)

### 4-T5 `5LowesIme VendorCredit` (W3) — Tab 5 (Skill 12)

Captured: `RefNumber`, `Vendor`, `Vendor Number`, `Transaction Date`, `Transaction Type`, `Items Item`, `Items Cost`, `Item Class`, `Memo`, `Memo 2`, `Net Amount`, `Check #`, `Check Date`, `Project Number` (note: `Project Number`, not `Project #` — divergence §6.C), `Invoice #`, `Suggested Invoice`, `Service Provider`, `Investigation Category`, `Return CFI Labor`, `Pending Reason`, `Vendor Credit`, `Other`, `-DBT`.

### 4-T6 `6LowesIme CFIBills` (W3) — Tab 6 (Skill 18)

Skill 18 `SKILL.md` §"Tab 6 layout" claims 16 columns. Captured from script + SKILL.md cross-ref:

`RefNumber`, `Vendor`, `Transaction Type`, `Transaction Date`, `Invoice Number`, `Items Item`, `Items Class`, `Items Cost`, `Bill Due`, `Memo`, `Project_Number`, `Net_Amount_Paid` (alias §6.C), `Check Number`, `Svc Provider Fee/Deduction`, `Accounting`, `Rule Tags`, + audit/derived columns. The "⚠  DO NOT UPLOAD — HOLD SECTION (audit-visible only) ⚠" banner sits in the same workbook (in-tab section divider).

Plus Skill 18 emits `3Audit: Accounting`, `3Audit: Action Required`, `3Audit: Labor $`, `3Audit: Order Status` columns into a cross-tab audit view (header names anchored by literal strings in the script).

### 4-T7 `7LowesIme SalesReceipts` (W3) — Tab 7 (Skill 19)

Skill 19 `SKILL.md` §"Tab 7 layout" — 15 columns matching the production template. Specific literals captured: `Customer`, `Memo`, `Class`, `Item`, `Rate`, `Tax Code`, `Date`, `Payment Method`, `Reference No`, `Deposit To`. (The remaining 5 columns are present in `SKILL.md` table but the script writes via header-map only — literal headers not captured by the static extractor; see §5 open question O3.)

### 4-T8 `WARRANTY AUDIT` (W2) — Skill 6 output

Section banners (col A row anchors): `APPEASEMENT`, `DEDUCT-APP`, `SHORT PAY`, `OVERPAID`, `RECONCILED CANCELLATION`, `PAYMENT EXCEPTIONS`, `POSSIBLE APPEASEMENT MATCH — Overpaid rows w/ APPEASE record (Jen R29 — match only, decide handling later)` (v4.40.5 banner; tab key `POSSIBLE APPEASE MATCH`).

Per-row columns (33-col unified layout, v4.44.0): includes `Service Provider`, `SP Vendor Number` (v4.44.0), `Project #`, `Invoice Number`, `Net Amount`, `Accounting`, `Investigation Category`, `Suggested Acctg Invoice`, `Rule(s) Applied`. Plus appeasement match columns (v4.40.5): `Match Type`, `Project`, `Master Net`, `Master Variance`, `Master SP`, `APPEASE Customer`, `CFI`, `Sage Cost`, `SP Fee`, `Lowes Retail`, `Date`, `Comments`.

### 4-T9 `ServiceProvider AP Hold` / `SP AP Hold` (W4)

Captured columns (positional from Skill 17 `_build_header_map`): `RefNumber`, `Vendor`, `Items Item`, `Items Class`, `Items Cost`, `Bill Due`, `Memo`, `Status`, `Status Date`, `Released For Payment`. Skill 17 `v1.4.0` "retroactively populate blank Items Class + Bill Due on existing SP AP Hold rows" confirms these are header-anchored.

### 4-T10 Dbase tabs (W5) — header reconciliation per Skill 22

Skill 22 explicitly handles two source schemas per tab (post-BR-178 "weekly audit pattern" + legacy). Common reconciliation columns observed: `RefNumber` (new) / `Inv_Description` (legacy `2LowesIme AuditRelease` vs `DealerPaymentsMaster`); `Invoice Number` (new) / col K legacy; `PO Number` / suffix-stripped invoice. Skill 22 `dbase_append.py` `reconcile_schema()` is the dry-run that surfaces mismatches.

---

## 5. Open questions

Each item names the source skill, the script-file + line context, and the specific ambiguity. **Do not resolve without consulting Jen / Frankie / the Controller** — every item below corresponds to a header that drives an accounting line.

| # | Question | Source | Why it matters |
|---|----------|--------|----------------|
| O1 | Is `Project #` (W2 IME LOWESLINK) the same data element as `Project Number` (Tab 5) and `Project_Number` (Tab 6)? | Skill 12 `vendor_credit_builder.py`, Skill 18 `cfi_bills_builder.py` | Three spellings on three sibling tabs of the same workbook (W3). High BR-178 risk if any consumer joins on a substring. |
| O2 | Is `Invoice Number` (Skill 8 Tab 2 / IME LOWESLINK) the same as `Invoice #` (Tabs 4 / 5)? | Skills 11, 12 | Same field, two name spellings. Skill 8 ERP-template hardcodes `Invoice Number`; QB upload templates may want `Invoice #`. |
| O3 | Tab 7 (`SalesReceipts`, Skill 19) `SKILL.md` lists "15 columns matching the production template" but the script writes via header-map. What are the 5 columns we have not captured statically? | Skill 19 `sales_receipts_builder.py` | If Tab 7 columns drift, Skill 19 silently writes blank cells. |
| O4 | Skill 18 `cfi_bills_builder.py:325` hardcodes `if "ServiceProvider AP Hold" not in wb.sheetnames` and skips freeze suppression on miss — **no fallback to `SP AP Hold`**. Is this intentional? | Skill 18 line 325 | If Jen renames again (or W4 was built from a fresh template using the new name only), CFI bills will silently include frozen vendors. This is the next BR-178. |
| O5 | `CLAUDE AUDIT` tab (W2) is written by both Skill 3 (init) and Skill 8 (audit append). Order of operations and merge contract? | Skill 3 `check_remits_builder.py`, Skill 8 `ssi_audit_release.py` | Multi-writer mutable state. If Skill 8 runs before Skill 3 (re-run, partial week), Skill 3 may overwrite Skill 8's audit log. |
| O6 | Skill 6 `read_ar_ap_dbase()` fallback list for the CM-side tries `4LowesIME CM` then `4LowesIme CM` (case variant) — was the CM tab also intentionally renamed (similar to BR-190) or is one a typo? | Skill 6 `warranty_audit.py:455` | The casing difference is not documented as a deliberate rename; if it's a typo, future Dbase rebuilds may consolidate on one. |
| O7 | `3LowesIME SSIAudit` (W3, Tab 3) is written by Skill 15 but Skill 22 `TAB_MAP` references the source as `3LowesIme ArApAudit` — same tab, different spellings in writer vs SCD2 source. Is this an active rename in flight, or are these two different tabs the chain mistakenly treats as one? | Skill 22 `dbase_append.py:390` | Could be silent data loss if the strings don't resolve to the same physical tab in `wb.sheetnames`. |
| O8 | W11 `MSI_Invoice_Master.xlsx` is written by Skill 27 — no other skill literal-references it for read. The MSI quarterly chain (28/29/30) reads MSI data, but the read happens via Skill 30's 22 scripts. Is W11 actually consumed downstream, or is it an orphan artifact? | Skill 27 `msi_invoice_master.py`, Skill 30 scripts | If orphan, retire it. If consumed, the consumer needs an explicit literal so future skill renames don't break it. |
| O9 | Skill 4 `_resolve_mapping_path()` (BR-361) globs across `/mnt/Claude/`, `/sessions/*/mnt/Claude/`, host Mac, `~`-relative for W6. Is there a canonical OneDrive path for W6, or is the glob the intentional contract because Frankie / Jen each mount differently? | Skill 4 / Skill 6 `_resolve_mapping_path()` | If we standardize on one OneDrive path, the glob fallback can be removed (and we lose a class of "works on Jen's laptop, not on Frankie's" bugs). |

---

## 6. Known cross-skill aliases (the BR-178 surface)

These are the active legacy/new name pairs that the scripts currently handle via fallback. Each pair is a candidate for canonicalization in Phase 2 (or — at minimum — a contract that every consumer must implement the fallback). **Number of skills that handle the alias correctly** is the failure-mode metric.

### §6.A `ServiceProvider AP Hold` ↔ `SP AP Hold` (W4)

- **Renamed:** 2026-04-29, BR-190 Option C.
- **Skills with documented fallback (both spellings accepted):** Skill 17 (`AP_HOLD_TAB_CANDIDATES`), Skill 6.5 (same constant), Skill 22 (`TAB_MAP` writes `SP AP Hold`).
- **Skills with NO fallback (hardcode `ServiceProvider AP Hold`):** **Skill 18** (`cfi_bills_builder.py:325`). Skill 18 silently skips freeze suppression on miss — this is the live BR-178 risk in the chain right now.
- **Recommendation (registry-level, not a decision):** standardize on `SP AP Hold` (the post-BR-190 canonical name) and patch Skill 18 to either accept the legacy alias or fail loud on miss. Flag for CFO.

### §6.B `3LowesIme ArApAudit` ↔ `3LowesIME SSIAudit` (W3)

- **Skill 15 writes:** `3LowesIME SSIAudit` (per surrounding context / Tab 3 naming convention).
- **Skill 22 reads (`TAB_MAP` source spec):** `3LowesIme ArApAudit`.
- **Status:** open question (§5 O7). Possibly a partial rename mid-chain. Verify before next Dbase append run.

### §6.C Column-name spellings on sibling tabs of W3

| Logical field | Skill 6 / 7 / 8 spelling | Skill 11 / 12 spelling | Skill 18 spelling |
|---------------|--------------------------|------------------------|-------------------|
| Project ID | `Project #` | `Project #` (T4), `Project Number` (T5) | `Project_Number` (T6) |
| Invoice ID | `Invoice Number` | `Invoice #` | `Invoice Number` |
| Money paid | `Net_Amount_Paid` | `Net Amount` | `Net_Amount_Paid` |
| Check ID | `Check Number` | `Check #` | `Check Number` |
| Posting-suggestion invoice | `Suggested Acctg Invoice` | `Suggested Invoice` | (not used) |
| Status field | `Status` / `IME Status` | (varies) | `RO Status` |

**No script currently joins across these without a `_build_header_map` lookup** — the script-side lookup is the silent normalizer. But any future script written to spec from one Tab's columns will pick one spelling and miss the other.

### §6.D Dbase payment-side tab rename (W5 ↔ W3 source)

- **New (post-BR-178, 2026-05-11 Dbase rewire):** `2LowesIme AuditRelease` (col A `Invoice Number`).
- **Legacy fallback (in Skill 6, Skill 22 reads):** `DealerPaymentsMaster`, `Dealer Payments-Master`, `Dealer Payments Master` (col K `Inv_Description`).
- **Skills with fallback:** Skill 6 (`warranty_audit.py:402`), Skill 22 (`TAB_MAP` writes new only).
- **Risk:** if a controller migration overwrites the Dbase without the legacy tabs, every Skill 6 run reverts to "zero prior entries" silently (this is exactly the v4.49.0 fix scenario).

### §6.E Dbase CM-side tab rename (W5 ↔ W2 source)

- **New:** `4LowesIME CM` (col D `RefNumber`, prefixed-with-suffix).
- **Casing variant:** `4LowesIme CM` (Skill 6 handles both casings).
- **Legacy fallback (in Skill 6 only):** `CreditMemo`, `Credit Memo`, `Dealer Deductions` (3 spellings).
- **Risk:** matches §6.D — silent zero-counts on Dbase rebuild.

### §6.F Tab 4 / Tab 5 column-naming inconsistency (`Credit Memo` value vs tab name)

Skill 11 outputs Tab 4 with header `Credit Memo` (a column). Skill 27 emits `Doc Type` cells with value `"Credit Memo"` (a value). Skill 23 uses `TYPE_DEDUCT_APP_CM = "DEDUCT_APP_CM"` as an internal constant. These are not aliases (different concepts) but they share the substring — any future grep-driven refactor needs to keep them separate.

---

## 7. Per-skill quick reference

Brief per-skill summary of script-extracted workbooks and tabs. Skill 0 and 1 omitted (no `.py`).

| Skill | Workbooks | Tabs (read/write) | Scripts | LOC |
|-------|-----------|-------------------|---------|-----|
| 1.1 | (path-built) | `WorkOrder` (write) | 1 | varies |
| 2 | W7, W9, W10, W18 | `SSI RO DATA` (W), `REVIEW` (W) | 1 | — |
| 3 | W2 | `IME LOWESLINK` (W), `SSI AUDIT` (W), `SSI ANALYSIS` (W), `Claude Improve` (W), `CLAUDE AUDIT` (W) | 1 | — |
| 4 | W2, W6, W7, W18 | `IME LOWESLINK` (R+W), `SSI AUDIT` (R+W), `SSI ANALYSIS` (R), `SSI RO DATA` (R), `SP PAYMENT FREEZE` (W) | 1 | — |
| 5 | W2, W18 | `IME LOWESLINK` (R+W) | 1 | — |
| 6 | W2, W1, W5, W6 | `IME LOWESLINK` (R+W), `SSI AUDIT` (R), `SSI ANALYSIS` (R), `WARRANTY AUDIT` (W), `APPEASE` (R), `POSSIBLE APPEASE MATCH` (W), `2LowesIme AuditRelease` (R, primary), `DealerPaymentsMaster`/legacy (R, fallback), `4LowesIME CM`/`4LowesIme CM` (R, primary), `CreditMemo`/`Credit Memo`/`Dealer Deductions` (R, fallback) | 1 | — |
| 6.5 | W4, W5 | `Suffix Ledger` (W), `SP AP Hold`/`ServiceProvider AP Hold` (R) | 1 | — |
| 7 | W2, W1, W3 | `IME LOWESLINK` (R), `SSI AUDIT` (R), `WorkOrder` (R), `1LowesIme AR_SSI ChkRemit` (W) | 1 | — |
| 8 | W2, W3 | `IME LOWESLINK` (R), `SSI AUDIT` (R), `SSI ANALYSIS` (R), `2LowesIme SSIAuditRelease` (W), `CLAUDE AUDIT` (W) | 1 | — |
| 9 | W3, W16 | Tabs 1, 2, 3 (R via single-tab temps in BR-182 LOCKED LOCATION) | 2 | — |
| 11 | W2, W3 | `IME LOWESLINK` (R), `WARRANTY AUDIT` (R), `_STATUS_` (R+W), `SP PAYMENT FREEZE` (R), `CreditMemo` (W) | 1 | — |
| 12 | W2, W3, W1, W6 | `IME LOWESLINK` (R), `WARRANTY AUDIT` (R), `APPEASE` (R), `_STATUS_` (R+W), `SP PAYMENT FREEZE` (R), `VendorCredit` (W), `Vendor Reference` (R), `Settlement` (R) | 1 | — |
| 15 | W2, W6, W1 | `IME LOWESLINK` (R), `WARRANTY AUDIT` (R), `Rule AUDIT` (R), `WorkOrder` (R), `APPEASE` (R), `2LowesIme SSIAuditRelease` (W, Tab 3 enrichment) | 1 | — |
| 16 | W2, W8 | `SSI AUDIT Master` (W) | 1 | — |
| 17 | W2, W4 | `SP PAYMENT FREEZE` (R), `SP AP Hold`/`ServiceProvider AP Hold` (W, append), `_STATUS_` (W) | 1 | — |
| 18 | W2, W3, W4, W6 | `WARRANTY AUDIT` (R), `IME LOWESLINK` (R), `SP PAYMENT FREEZE` (R), `_STATUS_` (R+W), `CFIBills` (W), `ServiceProvider AP Hold` (R, **no fallback — §6.A**) | 1 | — |
| 19 | W3, W2 | `SalesReceipts` (W) — references Tab 4 (`CreditMemo`) for offset | 1 | — |
| 20 | W2, W3, W15, W4 | `IME LOWESLINK` (R), `WARRANTY AUDIT` (R), `1LowesIme AR_SSI ChkRemit` (R), 8 numbered tabs `1. EXEC SUMMARY` … `8. SOURCE TRACEABILITY` (W) | 1 | — |
| 21 | W2, W15, W3, W4, W8, W6 | `FILE ROADMAP` (W), `README` (W), `Weekly Review Checklist` (W) | 1 | — |
| 22 | W2, W3, W5 | 11-tab SCD2 write to W5 per `TAB_MAP` (§3-W5 table) | 1 | — |
| 23 | W5, W6 | `_INFO` (W), one shared operational view tab (W) | 1 | — |
| 26 | W5, W6 | `Vendor Reference` (W), `Appeasements` (R), Dbase tabs (R) | 1 | — |
| 27 | W11 | `MSI_Invoice_Master` worksheet (W) | 1 | — |
| 28 | W12, W14 | `Freight Groups` (R), `Freight Rate Gaps` (R), `PO Audit Master` (R) | 1 | — |
| 29 | (path-built) | (4 scripts handle SSI bulk caches + per-MFG fresh files; Sev-1 postmortem 2026-05-12) | 5 | — |
| 30 | W12, W13, W14, + per-MFG | 13 tabs per quarterly workbook (§3-W14) | 22 | — |

---

## 8. Audit trail

Every entry above traces to:

- A script literal in `work/SAG-1459/unpacked/<skill>/<skill-name>/scripts/*.py` (extracted by `extract_schema.py`, output at `work/SAG-1467/scan/extract.json` and `work/SAG-1467/scan/pivots.json`), or
- A `SKILL.md` `## Column Mapping` / `## Tab structure` section in the same package, or
- A skill's `## Source-to-Target Tab Map` (Skill 22).

The two JSON artifacts under `work/SAG-1467/scan/` are the machine-readable companion to this registry. A re-run of `python3 work/SAG-1467/extract_schema.py` regenerates them from the current state of the unpacked packages.

---

## 9. What this registry does NOT cover (and why)

- **Live workbook content.** Per hard rule, no `.xlsx` was opened. The shape of header rows is taken from the source code, not from production cells.
- **Implicit columns inserted via openpyxl positional access.** Skills that write via `ws['B'+str(row)].value = ...` and then re-read the same column anchor expose the column letter in code but only the *purpose* of the column in `SKILL.md`. Where `SKILL.md` documents the header, it is in the table above; where it does not, the open question is in §5.
- **Per-row data types beyond what the script asserts.** "money" means a money-shaped field per the script (`Decimal`, `parse_money`, currency format). It does not document the actual numeric precision controllers see.
- **MSI script internals.** Skill 30 has 22 scripts; the registry captures the workbook tabs but not the internal `MSI-Data` sub-schema (that's Phase Mapping territory, per §4.4 of the parent plan).

---

*Generated by static-analysis pass on 2026-05-19 against `work/SAG-1459/unpacked/` symlink set (placed 2026-05-18). Re-run extractor and re-publish this document if any skill package changes.*
