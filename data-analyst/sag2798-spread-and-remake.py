"""
SAG-2798 Part 2 — Material Deduction Spread + Remake Job Key
- Join PO.Material Order → MO.MO Number to compute per-order cost vs charge
- Analyze Credit Number field in MO (remakes/credits)
- Analyze Amount Difference in PO (invoice variance)
- Find the 620-line / -$4.32M credit-line remake proxy job key
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = "/home/gus-pinsoneault/paperclip-sage-surfaces/Finance & Accounting"
YEARS = [2023, 2024, 2025, 2026]

# ─────────────────────────────────────────────
# Load MO and PO (2023-2026)
# ─────────────────────────────────────────────
print("Loading MO files...")
mo_frames = []
for yr in YEARS:
    import os
    path = os.path.join(DATA_DIR, f"Material_Orders_Closed-{yr}.xlsx")
    if os.path.exists(path):
        df = pd.read_excel(path)
        df['_year'] = yr
        mo_frames.append(df)
mo_all = pd.concat(mo_frames, ignore_index=True)
mo_all['MO Amount'] = pd.to_numeric(mo_all['MO Amount'], errors='coerce').fillna(0)
mo_all['Amount Paid'] = pd.to_numeric(mo_all['Amount Paid'], errors='coerce').fillna(0)
print(f"  MO total: {len(mo_all):,} rows")

print("Loading PO files...")
po_frames = []
for yr in YEARS:
    path = os.path.join(DATA_DIR, f"Purchase_Orders_Closed-{yr}.xlsx")
    if os.path.exists(path):
        df = pd.read_excel(path)
        df['_year'] = yr
        po_frames.append(df)
po_all = pd.concat(po_frames, ignore_index=True)
po_all['PO Amount'] = pd.to_numeric(po_all['PO Amount'], errors='coerce').fillna(0)
po_all['Invoice Amount'] = pd.to_numeric(po_all['Invoice Amount'], errors='coerce').fillna(0)
po_all['Amount Difference'] = pd.to_numeric(po_all['Amount Difference'], errors='coerce').fillna(0)
po_all['Amount Paid'] = pd.to_numeric(po_all['Amount Paid'], errors='coerce').fillna(0)
print(f"  PO total: {len(po_all):,} rows")

# ═══════════════════════════════════════════════════════════════
# SECTION A: MATERIAL DEDUCTION SPREAD
# PO.Material Order → MO.MO Number
# Sage pays: MO.MO Amount
# Sage deducts: PO.PO Amount (what Sage billed distributor/charged on fabricator order)
# Spread = PO Amount - MO Amount (if positive: Sage marks up; if 0: pass-through)
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION A: MATERIAL DEDUCTION SPREAD (PO → MO JOIN)")
print("=" * 70)

# Normalize the MO Number for join
mo_for_join = mo_all[['MO Number', 'Product Line', 'Order Type', 'MO Amount', 'Amount Paid',
                        'Retail Order', 'CFI Name', 'Invoice Type', 'Credit Number', '_year']].copy()
mo_for_join = mo_for_join.rename(columns={'MO Amount': 'MO_Cost', 'Amount Paid': 'MO_Paid'})

po_for_join = po_all[['PO Number', 'Material Order', 'Retail Order', 'Product Line', 'Order Type',
                        'PO Amount', 'Invoice Amount', 'Amount Difference', 'Amount Paid',
                        'CFI Name', 'Distributor', '_year']].copy()
po_for_join = po_for_join.rename(columns={
    'PO Amount': 'PO_Amount',
    'Invoice Amount': 'PO_Invoice_Amount',
    'Amount Difference': 'PO_Amt_Diff',
    'Amount Paid': 'PO_Paid',
    'Material Order': 'MO_Number_ref'
})

# Join on Material Order ↔ MO Number
joined = po_for_join.merge(
    mo_for_join[['MO Number', 'MO_Cost', 'MO_Paid', 'Invoice Type', 'Credit Number']],
    left_on='MO_Number_ref',
    right_on='MO Number',
    how='left'
)
print(f"\nJoin coverage: {joined['MO Number'].notna().sum():,} of {len(joined):,} PO rows matched to an MO ({joined['MO Number'].notna().mean()*100:.1f}%)")

# Compute spread for matched rows
matched = joined[joined['MO Number'].notna()].copy()
matched['spread'] = matched['PO_Amount'] - matched['MO_Cost']
matched['spread_pct'] = np.where(matched['MO_Cost'] > 0,
                                  matched['spread'] / matched['MO_Cost'] * 100,
                                  np.nan)

print(f"\nMatched rows: {len(matched):,}")
print(f"  PO Amount (what Sage charges): ${matched['PO_Amount'].sum():,.0f}")
print(f"  MO Cost (what Sage pays):      ${matched['MO_Cost'].sum():,.0f}")
print(f"  Gross Spread:                  ${matched['spread'].sum():,.0f}")
print(f"  Spread %:                      {matched['spread'].sum()/matched['MO_Cost'].sum()*100:.2f}%")

# By Order Type (Material Order only for cleaner signal)
mat_orders = matched[matched['Order Type'] == 'Material Order']
print(f"\nMaterial Orders only ({len(mat_orders):,} rows):")
print(f"  PO Amount (charge): ${mat_orders['PO_Amount'].sum():,.0f}")
print(f"  MO Cost (pay):      ${mat_orders['MO_Cost'].sum():,.0f}")
print(f"  Spread:             ${mat_orders['spread'].sum():,.0f}")
spread_pct = mat_orders['spread'].sum() / mat_orders['MO_Cost'].sum() * 100
print(f"  Spread %:           {spread_pct:.2f}%")

# By Product Line
print("\nSpread by Product Line (Material Orders, 2023-2026):")
by_pl = mat_orders.groupby('Product Line').agg(
    jobs=('PO Number', 'count'),
    po_charge=('PO_Amount', 'sum'),
    mo_cost=('MO_Cost', 'sum'),
    spread=('spread', 'sum')
).reset_index()
by_pl['spread_pct'] = (by_pl['spread'] / by_pl['mo_cost'] * 100).round(2)
by_pl = by_pl.sort_values('po_charge', ascending=False)
for _, row in by_pl.iterrows():
    print(f"  {row['Product Line']:25s}  jobs={row['jobs']:6,}  PO=${row['po_charge']:>12,.0f}  "
          f"MO=${row['mo_cost']:>12,.0f}  spread=${row['spread']:>10,.0f}  ({row['spread_pct']:.1f}%)")

# Year-over-year spread trend
print("\nSpread by Year (Material Orders):")
by_yr = mat_orders.groupby('_year').agg(
    jobs=('PO Number', 'count'),
    po_charge=('PO_Amount', 'sum'),
    mo_cost=('MO_Cost', 'sum'),
    spread=('spread', 'sum')
).reset_index()
by_yr['spread_pct'] = (by_yr['spread'] / by_yr['mo_cost'] * 100).round(2)
for _, row in by_yr.iterrows():
    print(f"  {int(row['_year'])}: jobs={row['jobs']:6,}  PO=${row['po_charge']:>12,.0f}  "
          f"MO=${row['mo_cost']:>12,.0f}  spread=${row['spread']:>10,.0f}  ({row['spread_pct']:.1f}%)")

# Amount Difference distribution (PO Invoice Amount - PO Amount)
print("\nPO Amount Difference (Invoice Amount - PO Amount) distribution:")
po_nonzero_diff = po_all[po_all['Amount Difference'] != 0]
print(f"  Rows with nonzero difference: {len(po_nonzero_diff):,}")
print(f"  Total PO Amount Difference: ${po_all['Amount Difference'].sum():,.0f}")
print(f"  Of which negative (vendor invoiced less): ${po_all[po_all['Amount Difference']<0]['Amount Difference'].sum():,.0f}")
print(f"  Of which positive (vendor invoiced more): ${po_all[po_all['Amount Difference']>0]['Amount Difference'].sum():,.0f}")

# ═══════════════════════════════════════════════════════════════
# SECTION B: MO CREDIT NUMBERS — REMAKES / CREDITS FLOW
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION B: MO CREDIT NUMBERS (REMAKE / CREDIT TRACKING)")
print("=" * 70)

# Credit Number field
mo_with_credit = mo_all[mo_all['Credit Number'].notna() & (mo_all['Credit Number'] != '')].copy()
print(f"\nMO rows with a Credit Number: {len(mo_with_credit):,}")
print(f"Credit Number sample: {mo_with_credit['Credit Number'].dropna().head(10).tolist()}")

# Invoice Type breakdown (InvoiceRelease vs CreditRelease)
print("\nMO Invoice Type distribution:")
print(mo_all['Invoice Type'].value_counts().to_string())

credit_releases = mo_all[mo_all['Invoice Type'] == 'CreditRelease']
invoice_releases = mo_all[mo_all['Invoice Type'] == 'InvoiceRelease']
print(f"\nCreditRelease rows: {len(credit_releases):,}")
print(f"  MO Amount sum: ${credit_releases['MO Amount'].sum():,.0f}")
print(f"  MO Amount avg: ${credit_releases['MO Amount'].mean():,.0f}")
print(f"\nInvoiceRelease rows: {len(invoice_releases):,}")
print(f"  MO Amount sum: ${invoice_releases['MO Amount'].sum():,.0f}")

# Credit releases by Product Line
print("\nCreditRelease by Product Line:")
cr_pl = credit_releases.groupby('Product Line').agg(
    rows=('MO Number', 'count'),
    total_mo=('MO Amount', 'sum'),
    avg_mo=('MO Amount', 'mean')
).reset_index().sort_values('total_mo', ascending=False)
for _, row in cr_pl.iterrows():
    print(f"  {row['Product Line']:25s}  rows={row['rows']:6,}  total=${row['total_mo']:>12,.0f}  avg=${row['avg_mo']:>8,.0f}")

# Link CreditRelease MOs to their Retail Orders
print("\nCreditRelease linked to Retail Orders (job-level key check):")
cr_with_ro = credit_releases[credit_releases['Retail Order'].notna() & (credit_releases['Retail Order'] != '')]
print(f"  CreditRelease rows with Retail Order: {len(cr_with_ro):,} of {len(credit_releases):,}")
print(f"  Distinct Retail Orders with credits: {cr_with_ro['Retail Order'].nunique():,}")
print(f"  Sample Retail Orders: {cr_with_ro['Retail Order'].dropna().head(10).tolist()}")

# ═══════════════════════════════════════════════════════════════
# SECTION C: REMAKE JOB-LEVEL KEY
# CFO proxy: 620 credit lines / -$4.32M via payment IDs
# Can we map these to RO Numbers?
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION C: REMAKE PROXY — JOB-LEVEL KEY ANALYSIS")
print("=" * 70)

# Load AAA Lowe's sheet for check/invoice matching
print("\nLoading AAA Lowe's sheet (this may take a moment)...")
import os
aaa_path = os.path.join(DATA_DIR, "AAA Material Income Estimate - All Accounts.xlsx")
lowes = pd.read_excel(aaa_path, sheet_name="Lowe's")
lowes['Invoice Amount'] = pd.to_numeric(lowes['Invoice Amount'], errors='coerce')
lowes['Check Amount'] = pd.to_numeric(lowes['Check Amount'], errors='coerce')
lowes['MATERIAL'] = pd.to_numeric(lowes['MATERIAL'], errors='coerce').fillna(0)
lowes['OOI/VR'] = pd.to_numeric(lowes['OOI/VR'], errors='coerce').fillna(0)
print(f"  Lowe's sheet: {len(lowes):,} rows")

# The CFO mentioned 620 lines / -$4.32M — look for credit-like patterns
# Check Number patterns — is there a Check Number that looks like a remake/credit batch?
print("\nCheck Number value analysis (top 20 most frequent):")
check_vc = lowes['Check Number'].value_counts().head(20)
print(check_vc.to_string())

# Look at OOI/VR negative (credits/remakes typically appear as negative OOI or MATERIAL)
print(f"\nOOI/VR distribution:")
print(f"  Rows OOI<0: {(lowes['OOI/VR'] < 0).sum():,}")
print(f"  Rows OOI>0: {(lowes['OOI/VR'] > 0).sum():,}")
print(f"  Sum OOI<0: ${lowes[lowes['OOI/VR']<0]['OOI/VR'].sum():,.0f}")

# Look at MATERIAL negative
print(f"\nMATERIAL distribution:")
print(f"  Rows MATERIAL<0: {(lowes['MATERIAL'] < 0).sum():,}")
print(f"  Rows MATERIAL>0: {(lowes['MATERIAL'] > 0).sum():,}")
if (lowes['MATERIAL'] < 0).sum() > 0:
    print(f"  Sum MATERIAL<0: ${lowes[lowes['MATERIAL']<0]['MATERIAL'].sum():,.0f}")

# Check Amount as credit proxy — are there check amounts that are negative?
print(f"\nCheck Amount distribution:")
print(f"  Rows Check Amount<0: {(lowes['Check Amount'] < 0).sum():,}")
neg_checks = lowes[lowes['Check Amount'] < 0]
if len(neg_checks) > 0:
    print(f"  Negative check total: ${neg_checks['Check Amount'].sum():,.0f}")

# Look at Invoice Amount distribution to find the 620 lines / -$4.32M
# The credit-line proxy was described as "payment IDs" — maybe it's a subset of checks
# Filter for rows that could be the remake proxy
print(f"\nAll Lowe's Invoice Amount stats:")
print(lowes['Invoice Amount'].describe().to_string())

# Look for large negative OOI batches
neg_ooi = lowes[lowes['OOI/VR'] < 0].copy()
if len(neg_ooi) > 0:
    print(f"\nNegative OOI/VR rows: {len(neg_ooi):,}, total=${neg_ooi['OOI/VR'].sum():,.0f}")
    print(f"  RO Number sample: {neg_ooi['RO Number'].dropna().head(10).tolist()}")
    print(f"  Check Number sample: {neg_ooi['Check Number'].dropna().head(10).tolist()}")

# Could the 620 lines be in a specific Product Line?
print("\nInvoice Amount by Product Line (to find $4.32M remake bucket):")
pl_inv = lowes.groupby('Product Line').agg(
    rows=('Invoice Amount', 'count'),
    total=('Invoice Amount', 'sum'),
    avg=('Invoice Amount', 'mean'),
    neg_rows=('Invoice Amount', lambda x: (x < 0).sum()),
).reset_index()
pl_inv = pl_inv.sort_values('total', ascending=False)
for _, row in pl_inv.iterrows():
    print(f"  {str(row['Product Line']):30s}  rows={row['rows']:7,}  total=${row['total']:>14,.0f}  neg={row['neg_rows']:5,}")

# Search for rows where Invoice Amount ≈ -4,320,000 / 620 = -$6,968/ea or by check grouping
print("\nLooking for check clusters near -$4.32M total...")
check_totals = lowes.groupby('Check Number').agg(
    rows=('RO Number', 'count'),
    check_amt=('Check Amount', 'first'),
    inv_total=('Invoice Amount', 'sum'),
    ooi_total=('OOI/VR', 'sum'),
    mat_total=('MATERIAL', 'sum'),
).reset_index()
check_totals['check_amt'] = pd.to_numeric(check_totals['check_amt'], errors='coerce')

# Sort by invoice total to see most negative
neg_check_clusters = check_totals[check_totals['inv_total'] < -100000].sort_values('inv_total')
print(f"\nCheck clusters with Invoice total < -$100K: {len(neg_check_clusters)}")
print(neg_check_clusters.head(20).to_string())

# Now look at PO Amount Difference = negative (vendor undercharged vs expected)
print("\nPO rows where Invoice Amount < PO Amount (vendor underbilled — potential credit):")
po_under = po_all[(po_all['Amount Difference'] < -100)].copy()
print(f"  Rows: {len(po_under):,}")
print(f"  Total underbill: ${po_under['Amount Difference'].sum():,.0f}")
print(f"  By Order Type:")
print(po_under.groupby('Order Type')['Amount Difference'].agg(['count', 'sum']).to_string())

# ═══════════════════════════════════════════════════════════════
# SECTION D: FINANCIALLY LINKED FIELD — DEDUCTION MECHANISM
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION D: FINANCIALLY LINKED — DEDUCTION MECHANISM DETAIL")
print("=" * 70)

# MO Financially Linked
print("\nMO Financially Linked values:")
print(mo_all['Financially Linked'].value_counts(dropna=False).head(10).to_string())

linked_mo = mo_all[mo_all['Financially Linked'] == True] if True in mo_all['Financially Linked'].values else \
            mo_all[mo_all['Financially Linked'].astype(str).str.lower().isin(['true', '1', 'yes'])]
print(f"\nFinancially Linked MOs: {len(linked_mo):,}")
if len(linked_mo) > 0:
    print(f"  MO Amount sum: ${linked_mo['MO Amount'].sum():,.0f}")

# Lowe's workbook Financially Linked
print("\nLowe's Financially Linked values:")
print(lowes['Financially Linked'].value_counts(dropna=False).head(10).to_string())

# What does a linked row look like vs unlinked
try:
    fl_true = lowes['Financially Linked'].astype(str).str.lower().isin(['true', '1', 'yes', 'x'])
    lowes_linked = lowes[fl_true]
    lowes_unlinked = lowes[~fl_true]
    print(f"\nLowe's Linked rows: {len(lowes_linked):,}, Invoice Amount: ${lowes_linked['Invoice Amount'].sum():,.0f}")
    print(f"  MATERIAL: ${lowes_linked['MATERIAL'].sum():,.0f}, OOI: ${lowes_linked['OOI/VR'].sum():,.0f}")
    print(f"Lowe's UNlinked rows: {len(lowes_unlinked):,}, Invoice Amount: ${lowes_unlinked['Invoice Amount'].sum():,.0f}")
    print(f"  MATERIAL: ${lowes_unlinked['MATERIAL'].sum():,.0f}, OOI: ${lowes_unlinked['OOI/VR'].sum():,.0f}")
except Exception as e:
    print(f"  Error: {e}")

# ═══════════════════════════════════════════════════════════════
# SECTION E: RETAIL ORDER JOIN TEST
# Can Lowe's RO Numbers join to MO/PO Retail Order field?
# This is the job-level key for the remake proxy
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION E: RETAIL ORDER JOIN TEST (JOB-LEVEL KEY)")
print("=" * 70)

# Normalize RO formats
lowes_ros = set(str(r).strip() for r in lowes['RO Number'].dropna().unique() if str(r).strip())
mo_ros = set(str(r).strip() for r in mo_all['Retail Order'].dropna().unique() if str(r).strip())
po_ros = set(str(r).strip() for r in po_all['Retail Order'].dropna().unique() if str(r).strip())

print(f"\nDistinct RO Numbers:")
print(f"  Lowe's sheet: {len(lowes_ros):,}")
print(f"  Material Orders: {len(mo_ros):,}")
print(f"  Purchase Orders: {len(po_ros):,}")

overlap_lowes_mo = lowes_ros & mo_ros
overlap_lowes_po = lowes_ros & po_ros
overlap_all = lowes_ros & mo_ros & po_ros

print(f"\nOverlap (Lowe's ∩ MO): {len(overlap_lowes_mo):,} ({len(overlap_lowes_mo)/len(lowes_ros)*100:.1f}% of Lowe's ROs)")
print(f"Overlap (Lowe's ∩ PO): {len(overlap_lowes_po):,} ({len(overlap_lowes_po)/len(lowes_ros)*100:.1f}% of Lowe's ROs)")
print(f"Overlap (Lowe's ∩ MO ∩ PO): {len(overlap_all):,} ({len(overlap_all)/len(lowes_ros)*100:.1f}% of Lowe's ROs)")

# Sample format comparison
print(f"\nRO format samples:")
print(f"  Lowe's: {list(lowes_ros)[:8]}")
print(f"  MO:     {list(mo_ros)[:8]}")
print(f"  PO:     {list(po_ros)[:8]}")

# Test join on a specific set of MO Retail Orders with credits
if len(cr_with_ro) > 0:
    cr_ros = set(str(r).strip() for r in cr_with_ro['Retail Order'].dropna().unique())
    overlap_cr_lowes = cr_ros & lowes_ros
    print(f"\nCreditRelease ROs matching Lowe's RO Numbers: {len(overlap_cr_lowes):,} of {len(cr_ros):,}")
    if len(overlap_cr_lowes) > 0:
        # Pull a sample of matched rows from Lowe's
        sample_ros = list(overlap_cr_lowes)[:5]
        sample_rows = lowes[lowes['RO Number'].astype(str).isin(sample_ros)]
        print(f"  Sample matched Lowe's rows:")
        print(sample_rows[['RO Number', 'Invoice Amount', 'MATERIAL', 'OOI/VR', 'Product Line']].head(10).to_string())

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
