# SAG-2819 — Denominator Source Verification
## Revenue + Job Count at territory × rsm × product_tier × window grain

**Date:** 2026-06-01 (finalized 2026-06-02 with CFO rulings)
**Analyst:** Data Analyst (fc67241d)
**Source data inspected:** AAA Material Income Estimate - All Accounts.xlsx
**Authority:** CFO denominator-discipline directive (comment 6c5f1a04 on SAG-2806)
**CTO acceptance:** comment baef4129 on SAG-2819
**CFO rulings incorporated:** comment 07a8cb28 on SAG-2819 → recorded on [SAG-2827](/SAG/issues/SAG-2827)

---

## Summary Verdict

**CONFIRMED — denominator is available at the required grain.**

The AAA Material Income Estimate workbook (Lowe's sheet) is a job-level source that carries all required dimensions. Revenue and job count can be aggregated at `territory_id × rsm_id × product_tier × daily/weekly window` directly from this file. The trailing-90-day baseline period is covered. Three gaps were identified; all are now resolved by CFO/CTO rulings or delegated as engineering pre-conditions on SAG-2806/SAG-2827.

---

## AC-1: Source Tables and Join-Capable Dimensions

**Primary denominator source (v1 scope — Lowe's-only per CFO ruling, see Gap 3):**
`/home/gus-pinsoneault/paperclip-sage-surfaces/Finance & Accounting/AAA Material Income Estimate - All Accounts.xlsx`

| Sheet | Rows | Date Range |
|-------|------|------------|
| Lowe's | 351,198 | 2020-09-19 → 2026-05-01 |
| All Accounts except Lowe's | 49,297 | 2019-01-02 → 2026-05-14 |
| RO-ALL | ~full fleet | 2025-10-29+ (ops tracking) |

**Dimension columns — all present in Lowe's sheet:**

| Required field | Column name in source | Cardinality |
|---|---|---|
| territory_id | `Territory Sales Manager` | 22 unique values (names) |
| rsm_id | `Regional Sales Manager` | 13 unique values (names) |
| product_tier | `Product Line` | 13 categories |
| date (for windowing) | `Invoice Date` | daily, 2020 → 2026-05 |

**Revenue + job count columns:**

| Measure | Column | Total (all years) |
|---|---|---|
| Revenue (net, as paid) | `Check Amount` | $610.7M |
| Revenue (invoiced) | `Invoice Amount` | $617.9M |
| Job count | `RO Number` (1 row = 1 job) | 349,692 unique ROs |
| Avg rows per RO | — | 1.00 (true job-level grain) |

**Recommendation:** Use `Check Amount` as revenue denominator (matches what Lowe's actually remits, consistent with the $752M/$131M reconciliation basis). Use `COUNT(RO Number)` for job count.

---

## AC-2: Windowing + 90-Day Baseline Coverage

- `Invoice Date` is daily granularity → supports daily aggregation natively.
- Weekly periods can be built via `ISO week` or `Monday-anchored week` on `Invoice Date`.
- **Trailing-90-day window (2026-03-03 → 2026-06-01):**
  - 6,662 rows in window
  - $15.8M in Check Amount
  - 17 of 22 territories have data (5 territories dormant in this window — valid NULL denominator cells)
  - 9 of 13 RSMs active
  - 12 of 13 Product Lines present
  - 685 unique `(Territory × RSM × Product Line × week)` cells with at least one job

**Coverage verdict: ✓ Sufficient for baseline.**

Note: 5 dormant territories and ~33% of rows with `territory = 0` or null are handled by CFO Gap A ruling below.

---

## AC-3: Join-Key Alignment — Credit Memo ↔ Denominator

**Check Number bridge:** The AAA Audit files (credit memo source) share `Check Number` with the denominator. Overlap is **100% confirmed** for 2023 (209/209 audit check numbers found in denominator source).

**Critical structure — AAA Audit files lack territory/RSM directly:**

| Field | AAA Audit file | AAA Material Income (Lowe's) |
|---|---|---|
| Check Number | ✓ | ✓ (join key) |
| RO Number | ✗ | ✓ |
| Territory Sales Manager | ✗ | ✓ |
| Regional Sales Manager | ✗ | ✓ |
| Product Line | ✓ | ✓ |

Join path to attribute credits to territory/RSM:
`credit.Check_Number` → `denominator.Check_Number` → `denominator.Territory_Sales_Manager`, `RSM`

Note: `Check Number` is batched — median 149 rows per check in the denominator (max 2,382). Attribution is only unambiguous if SAG-2805 captures territory/RSM directly at time of structured capture. This is engineering pre-condition PC-1 on SAG-2806.

---

## Gap Resolution (all closed)

### Gap 1 — Dimension coding: names not IDs → PC-1 on SAG-2806
- `Territory Sales Manager` and `Regional Sales Manager` are name strings, not numeric IDs.
- **Resolution:** Recorded as pre-condition PC-1 on [SAG-2806](/SAG/issues/SAG-2806) by CTO — two acceptable implementations: (a) names as ID domain (simplest), or (b) name→ID lookup table. Coder owns. Gated behind SAG-2805.

### Gap 2 — Product Line label mismatch → PC-2 on SAG-2806
- Audit 2023: `"Granite"` ≠ `"Natural Stone/Granite"`; `"Derivati Porcelain"` ≠ `"Porcelain"`
- Audit 2024: `"Granite"` ≠ `"Natural Stone/Granite"`
- **Resolution:** Recorded as pre-condition PC-2 on [SAG-2806](/SAG/issues/SAG-2806) by CTO — normalization map required before view goes live. Coder owns. Gated behind SAG-2805.

### Gap 3 — Non-Lowe's scope → **CFO ruling: Lowe's-only for v1** ✓
- **Decision (CFO, comment 07a8cb28, 2026-06-02):** v1 scope = Lowe's-only.
- Rationale: numerator↔denominator join is confirmed only for Lowe's; an all-accounts denominator against a Lowe's-confirmed numerator violates apples-to-apples.
- All-accounts is deferred to phase-2, contingent on confirming the AAA Audit numerator covers non-Lowe's at grain.
- Recorded on [SAG-2827](/SAG/issues/SAG-2827).

### Gap A — territory = 0 / null (~33% of rows) → **CFO ruling: Option A coalesce** ✓
- ~33% of Lowe's rows have `Territory Sales Manager = "0"` or null (RSM-direct assignments without a territory sub-assignment).
- **Decision (CFO, comment 07a8cb28, 2026-06-02):** Option A — coalesce `territory=0`/null to a named bucket `"RSM-direct (no territory)"`.
- The identical rule applies on the numerator side (credit memos with no territory attribution roll to the same bucket).
- Effect: 100% of revenue is preserved in the denominator; territory drill-through remains valid for the ~67% with explicit territory assignment.
- Recorded on [SAG-2827](/SAG/issues/SAG-2827).

---

## Final Data Source Verdict for SAG-2806 / SAG-2802

| AC | Status | Note |
|---|---|---|
| AC-1: Source at required grain | ✓ PASS | Lowe's sheet, job-level, all 4 dimensions present |
| AC-2: Windowing + 90-day baseline | ✓ PASS | Invoice Date daily; 6,662 rows in trailing 90d |
| AC-3: Join key alignment | ✓ RESOLVED | PC-1/PC-2 on SAG-2806; Gap A/3 ruled by CFO on SAG-2827 |

**The $1.2M/yr savings claim (SAG-2802) CAN be baselined from this source at the required grain.**
Denominator is not the falsifiability blocker. Pre-conditions PC-1 and PC-2 are engineering work gated behind SAG-2805.

---

## Appendix: Product Line Taxonomy (Lowe's denominator)

`Small Projects, Laminate-Product Only, Solid Surface, Quartz, Laminate-Install, Natural Stone/Granite, Porcelain, Wood, Duralosa, Samples & Literature, Thinscape, Plumbing and Appliances, - (unclassified)`

Normalization map (audit → denominator) required per PC-2:
- `"Granite"` → `"Natural Stone/Granite"`
- `"Derivati Porcelain"` → `"Porcelain"`
