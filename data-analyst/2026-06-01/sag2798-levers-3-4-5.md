# SAG-2798: Data Pull — Attach, Premium-Attribute, Material Deduction, and Remake Key
**Parent:** SAG-2797 (CFO lever sizing)
**Analyst:** Data Analyst (Sonnet 4.6)
**Date:** 2026-06-01 (revised 2026-06-02 per CFO review comment c50589c5)
**Cohort:** 170,481-row Lowe's reconciled basis (2023, 2024, 2025_V2, 2026_V2 per-year audit files, extract 2026-05-18) — unless otherwise stated
**Anchors:** $752M revenue / $131M material / 17.44% margin

> **Scope correction (board comment 7f6a532c):** Lever 3 (slab yield/scrap) dropped — fabricators bear yield; Sage doesn't warehouse remnants.
>
> **Re-cut (CFO review c50589c5):** (1) Remake reconciliation re-run on 170,481-row basis; (2) MO/PO field semantics assessment added; (3) cohort labels applied throughout.

---

## Sources and basis labels

| Label | Source | Rows |
|---|---|---|
| **170K basis** | Per-year audit files (2023, 2024, 2025_V2, 2026_V2), extract 2026-05-18 | 170,481 |
| **351K all-accounts** | AAA Material Income Estimate - All Accounts.xlsx → Lowe's sheet | 351,198 |
| **MO/PO 2023–2026** | Material_Orders_Closed and Purchase_Orders_Closed, 2023–2026 | 105,865 / 108,151 |

---

## Lever 4 — Attach (conf 4) — SIGNED OFF BY CFO

**Basis: 351K all-accounts Lowe's + MO/PO 2023–2026**

**Finding: $117K is real; true attach rate is 0.06%**

- 196 Lowe's rows with Product Line = "Plumbing and Appliances": Invoice Amount = **$119,786** — real, not a mapping gap
- These are **standalone plumbing orders (RO prefix "P-")**, not countertop jobs with attached sinks
- PO-based true attach rate: **0.06%** (24 of 43,011 slab ROs had a linked sink PO, 2023–2026)
- Sage is not the attach channel; Lowe's sells sinks/faucets direct

Recommendation: Pull Lowe's same-ticket cross-sell data from their IMS system to establish whether countertop and sink appear on the same project ticket. Current Sage data cannot establish this.

---

## Lever 5 — Premium-Attribute Elasticity (conf 3) — SIGNED OFF BY CFO (DATA-BLOCKED)

**Basis: 351K all-accounts + MO/PO 2023–2026**

**Finding: DATA-BLOCKED — no edge, thickness, or color tier fields anywhere**

- Zero fields for edge type, thickness, or color tier across all files
- Only proxy: Porcelain has highest avg RO Amount ($2,800 vs $1,874 Quartz) — product-line level only, not intra-line attribute model

Unblock: SSI material catalog API pull, joining SKU attributes (edge, thickness, color tier) to RO Numbers. Separate sourcing ask — not a gap in this pull.

---

## Material Deduction Mechanism — HELD FOR RE-CUT (semantics unverified)

**Basis: MO/PO 2023–2026**

### What the data shows

Joining Purchase_Orders_Closed (PO) to Material_Orders_Closed (MO) on `Material Order = MO Number` — 99.9% match rate, 73,494 matched Material Order pairs.

| | Amount |
|---|---|
| PO Amount (Sage's PO to Distributor) | $82,298,042 |
| MO Amount | $84,029,036 |
| Difference (MO − PO) | +$1,730,994 |

### Semantics assessment — unresolved

**What is confirmed:**
- `PO Amount` = Sage's expected cost to the Distributor (Sage issues PO to Distributor). Source: `canonical_schema.py` line 39 ("PO_AMOUNT = 20") plus 100% verification that `Amount Difference = Invoice Amount − PO Amount`.
- `PO Amount` is a Sage **outgoing** payment (cost side).

**What is NOT confirmed:**
- The direction of `MO Amount` — specifically whether it is (A) what Sage charges/deducts from the CFI's retail order, or (B) an internal accounting entry. The column name "Material Order" does not resolve this.

**Consistency check:**
- MATERIAL column on 170K reconciled basis: **$51.9M** (2023–2026)
- MO Amount, all types (2023–2026): **$99.7M** — nearly 2× MATERIAL; these are NOT the same construct
- MATERIAL/MO Amount per matched RO: ratio = 0.84 (not 1.0 — not directly equivalent)
- The large 84,424-row cohort with NULL `Invoice Type` ($61.4M MO Amount) suggests MO Amount tracks diverse transaction types, not a single billing-to-CFI construct

**Two interpretations, neither confirmed from data alone:**

| Interpretation | MO Amount = | Spread formula | NS/Granite reading | Overall reading |
|---|---|---|---|---|
| A (my original) | Sage's cost to vendor | PO − MO | +$3.7M "margin lever" | −$1.7M subsidy |
| B (corrected?) | Sage's charge to CFI | MO − PO | −$3.7M "cost overrun" | +$1.7M earned |

Interpretation B produces directional consistency with the positive OOI/VR retained margin (16–17%). Interpretation A is inconsistent — a net-negative material spread inside a positive-margin book would need explanation.

**CFO action required:** Confirm `MO Amount` field definition from an authoritative source (SSI data dictionary, accounting, or system config). Until confirmed, the NS/Granite spread figure cannot carry a board claim under either sign.

### Subsidiary finding: PO Invoice Variance

- 1,985 Material Order PO rows where vendor invoiced less than PO Amount
- Total underbill: **−$6.5M** (vendor sent smaller invoice than Sage's PO)
- CFO owns verification: recoverable credit vs write-off

---

## Remake Reconciliation — RE-CUT COMPLETE

**Basis: 170,481-row reconciled audit (2023, 2024, 2025_V2, 2026_V2)**

### Proxy confirmed exactly

| Metric | CFO proxy | Measured |
|---|---|---|
| Negative Check Amount lines | 620 | **620** ✓ |
| Negative Check Amount total | ~−$4.32M | **−$4,318,314** ✓ |
| Pure credits (OOI/VR = 0) | ~613 | **613** ✓ |
| Partial adjustments (OOI/VR ≠ 0) | — | **7** |

**By year (170K basis):**

| Year file | Neg Check rows | Total |
|---|---|---|
| 2023 | 75 | −$1,478,754 |
| 2024 | 180 | −$1,296,461 |
| 2025_V2 | 239 | −$703,502 |
| 2026_V2 | 126 | −$839,596 |
| **Total** | **620** | **−$4,318,314** |

### Job-level key — structural gap in 2023/2024 files

The per-year audit files have **two distinct structures**:
- **2023 and 2024**: contain only `Check Number`, `Check Amount`, and line-item amounts — **no RO Number, no Invoice Number, no Store Number**
- **2025_V2 and 2026_V2**: contain `Invoice Number`, `Invoice Date`, `Store Number`, `RO Number`, `Check Number`, `Check Amount`, and amounts

The `RO Number` column in the 2025/2026 files contains short integers (e.g., 88417, 43773, 0) — these are **not** Lowe's Retail Order identifiers (which appear as 9-digit numbers or I-XXXXXXXX format in the AAA all-accounts workbook). **0 negative-check rows matched MO/PO Retail Order on direct join.**

**Implication:** The audit file `RO Number` is an internal line-item reference, not the Lowe's job identifier. Mapping the 620 negative-check credits to MO/PO Retail Orders requires an intermediate join through the 351K all-accounts workbook (using `Check Number + Order Date + line amounts` to locate the matching rows, then pulling their `RO Number` in the all-accounts format, then joining to MO/PO). The 75 + 180 = 255 credits from 2023/2024 cannot be joined at all via the audit files alone (no Invoice Number or RO Number present).

### CreditRelease (MO) universe — kept as separate construct per CFO direction

The MO `Invoice Type = CreditRelease` rows are a **broader and different construct** from the Check-Amount credit proxy:
- 15,539 rows / $27.7M total / 6,483 matching Lowe's all-accounts RO Numbers
- These represent material-level credits (remakes, overcharges) tracked in the Material Order system
- Do NOT merge with the 620-line Check-Amount proxy in board copy — they are separate instruments

---

## Summary table

| Item | Basis | Status | Key number |
|---|---|---|---|
| Lever 4 — Attach $117K | 351K all-accounts | ✅ CFO signed off | $119,786 real standalone plumbing |
| Lever 4 — True attach rate | MO/PO 2023–2026 | ✅ CFO signed off | 0.06% (24 of 43,011 slab ROs) |
| Lever 5 — Premium attributes | All sources | ✅ CFO signed off (BLOCKED) | No edge/thickness/color fields — SSI API needed |
| Material deduction — aggregate | MO/PO 2023–2026 | ⏸ HELD — semantics unverified | MO−PO = +$1.7M or −$1.7M depending on interpretation |
| Material deduction — by product line | MO/PO 2023–2026 | ⏸ HELD — semantics unverified | NS/Granite ±$3.7M, Quartz ±$2.7M — sign depends on MO semantics |
| PO invoice variance | MO/PO 2023–2026 | CFO owns | −$6.5M Material Order underbill |
| Remake proxy | 170K basis | ✅ CONFIRMED | **620 rows / −$4,318,314** (613 pure credits + 7 partial) |
| Remake job-level key | 170K basis → MO/PO | ⚠ PARTIAL | 2023/2024 files: no RO Number / Invoice Number; 2025/2026 RO Number ≠ Lowe's job ID; multi-hop join needed |
| CreditRelease universe (separate) | MO/PO 2023–2026 + 351K | Labeled separately | 15,539 rows / $27.7M / 6,483 Lowe's jobs |

---

## Outstanding actions

| # | Action | Owner | What's needed |
|---|---|---|---|
| 1 | **MO Amount field definition** | CFO / accounting | Confirm whether MO Amount = Sage's charge to CFI or an internal cost entry; source: SSI data dictionary or accounting system config |
| 2 | **Remake job-level key** | Data Analyst (on request) | Multi-hop join: 620 check rows → 351K all-accounts (by Check Number + date + amount) → RO Number → MO/PO Retail Order; requires direction to proceed |
| 3 | **PO underbill verification** | CFO owns | Accounting confirm: is the −$6.5M MO underbill captured as credits or written off |
| 4 | **Attach / Premium attributes** | SSI API pull | New sourcing request — out of scope for this artifact |
