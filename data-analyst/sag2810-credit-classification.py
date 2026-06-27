"""
SAG-2810 — Cause classification of top 20-30 remake credit lines
Goal: firm Lever #1 (remakes) confidence from 7/10 → 9/10
Approach: pull top 25 highest-value negative-Check-Amount lines;
          surface every available field; attempt cause classification.
"""

import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

STORAGE = "/home/gus-pinsoneault/.paperclip/instances/default/data/storage/1dc911ed-ff05-4072-b2ae-a3e3177e3873/issues"

AUDIT_FILES = {
    "2023":    os.path.join(STORAGE, "04974e4f-40ae-469c-ad8b-e7dd93857a54/2026/05/18/8c029a4d-09ff-4211-9053-9ba94a1273c9-AAA_Lowe_s_Audit_2023_5.18.26.xlsx"),
    "2024":    os.path.join(STORAGE, "0be36824-4c76-4348-aba5-fff3b2095311/2026/05/18/e973cd75-3017-412f-ac23-2f9d72210ecf-AAA_Lowe_s_Audit_2024_5.18.26.xlsx"),
    "2025_V2": os.path.join(STORAGE, "3d853575-b76a-4b9a-8a4e-dede073ecfcd/2026/05/18/7267e3ae-92a0-4ffa-a1ce-f305931f85de-AAA_Lowe_s_Audit_2025_V2_5.18.26.xlsx"),
    "2026_V2": os.path.join(STORAGE, "b7299c0d-6b59-4572-8abd-d16f7e22fe7e/2026/05/18/8aeff3be-748c-4e97-b18c-e3d584679b9e-AAA_Lowe_s_Audit_2026_V2_5.18.26.xlsx"),
}

# ─────────────────────────────────────────────────────────────
# STEP 1: Load audit files, surface all columns per year
# ─────────────────────────────────────────────────────────────
print("=" * 72)
print("STEP 1: LOAD ALL AUDIT FILES — COLUMN INVENTORY")
print("=" * 72)

audit_frames = {}
for yr, path in AUDIT_FILES.items():
    if not os.path.exists(path):
        print(f"  {yr}: FILE NOT FOUND — {path}")
        continue
    xl = pd.ExcelFile(path)
    df = xl.parse(xl.sheet_names[0])
    df['_yr'] = yr
    audit_frames[yr] = df
    print(f"\n{yr} ({len(df):,} rows):")
    print(f"  Columns: {list(df.columns)}")

# ─────────────────────────────────────────────────────────────
# STEP 2: Isolate negative-Check-Amount rows across all years
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 2: ISOLATE NEGATIVE CHECK AMOUNT ROWS (620-LINE POPULATION)")
print("=" * 72)

neg_rows_by_year = {}
for yr, df in audit_frames.items():
    # Find the Check Amount column (exact or fuzzy)
    chk_col = None
    for c in df.columns:
        if 'check' in c.lower() and ('amount' in c.lower() or 'amt' in c.lower()):
            chk_col = c
            break
    if not chk_col:
        print(f"  {yr}: No Check Amount column found")
        continue
    df[chk_col] = pd.to_numeric(df[chk_col], errors='coerce')
    neg = df[df[chk_col] < 0].copy()
    neg['_chk_col'] = chk_col
    neg_rows_by_year[yr] = (neg, chk_col)
    print(f"  {yr}: {len(neg):,} negative-check rows, total=${neg[chk_col].sum():,.0f}")

# Stack all years
all_neg = []
for yr, (neg, chk_col) in neg_rows_by_year.items():
    neg = neg.copy()
    neg.rename(columns={chk_col: 'Check Amount'}, inplace=True)
    neg['_source_yr'] = yr
    all_neg.append(neg)

combined_neg = pd.concat(all_neg, ignore_index=True, sort=False)
print(f"\nTotal combined negative-check rows: {len(combined_neg):,}")
print(f"Total amount: ${combined_neg['Check Amount'].sum():,.0f}")

# ─────────────────────────────────────────────────────────────
# STEP 3: Full column dump for one row per year (field inventory)
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 3: FIELD INVENTORY — ALL NON-NULL FIELDS PER YEAR")
print("=" * 72)

for yr, (neg, chk_col) in neg_rows_by_year.items():
    if len(neg) == 0:
        continue
    sample_row = neg.sort_values(chk_col).iloc[0]  # most negative
    print(f"\n{yr} — largest credit row (all fields):")
    for col in neg.columns:
        val = sample_row[col]
        if pd.notna(val) and str(val).strip() not in ('', 'nan'):
            print(f"  {col:40s}: {val}")

# ─────────────────────────────────────────────────────────────
# STEP 4: TOP 25 CREDIT LINES — SORT BY ABSOLUTE VALUE
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 4: TOP 25 NEGATIVE CHECK LINES (HIGHEST ABSOLUTE VALUE)")
print("=" * 72)

combined_neg['_abs'] = combined_neg['Check Amount'].abs()
top25 = combined_neg.sort_values('_abs', ascending=False).head(25).copy()

# Identify all candidate signal columns across years
# Core signal fields we want to surface
SIGNAL_FIELDS = [
    'Invoice Number', 'Invoice Date', 'Store Number', 'RO Number',
    'Check Number', 'Check Amount',
    'Invoice Amount',
    'OOI/VR', 'MATERIAL',
    'Product Line', 'Product Type', 'Product',
    'Description', 'Memo', 'Notes', 'Comment',
    'Account', 'Account Name', 'Dealer', 'CFI', 'CFI Name',
    'Customer', 'Customer Name',
    'Order Type', 'Order Date',
    'Ref', 'Reference', 'Job',
    '_source_yr',
]

# Build the display table — show every field present with a non-null value
print("\nAll available columns in combined dataset:")
cols_present = [c for c in combined_neg.columns if c not in ('_abs', '_chk_col', '_yr')]
print(f"  {cols_present}")

# For each top-25 row, show all available fields
print("\n\nTOP-25 CREDIT ROWS — FULL FIELD DUMP:")
print("-" * 72)
for rank, (idx, row) in enumerate(top25.iterrows(), 1):
    print(f"\n  RANK {rank}: ${row['Check Amount']:,.0f}  (year={row.get('_source_yr','?')})")
    for col in cols_present:
        val = row.get(col)
        if pd.notna(val) and str(val).strip() not in ('', 'nan', '0', '0.0'):
            print(f"    {col:40s}: {val}")

# ─────────────────────────────────────────────────────────────
# STEP 5: STRUCTURED SUMMARY TABLE WITH CAUSE CLASSIFICATION
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 5: STRUCTURED CLASSIFICATION TABLE")
print("=" * 72)

# Cause classification taxonomy:
#   REM  = remake (measurement/template/cut error)
#   BRK  = breakage (damage in transit/install)
#   DEF  = material defect (vendor quality issue)
#   SVC  = service/install adjustment
#   PRC  = pricing adjustment / billing reversal
#   UNK  = unknown / insufficient signal
#
# Signals we can use from audit data:
#   - Product Line: laminate/postform → more likely measure error or cut; quartz → breakage or defect
#   - OOI/VR value: 0 = pure credit (no revenue recovery = likely remake/replacement, not a pricing adj)
#   - Invoice Amount: if = 0 or negative → pure credit note; if positive → partial adjustment
#   - Check Amount magnitude: very large = likely whole-job remake; very small = incidental adj
#   - Year: 2023/2024 have fewer fields — classification less reliable
#   - Store/Account: clustering may suggest a bad install team or store geography

# Map column names (may differ by year)
def get_val(row, *candidates):
    for c in candidates:
        if c in row.index and pd.notna(row[c]) and str(row[c]).strip() not in ('', 'nan'):
            return row[c]
    return None

print("\n")
print(f"{'Rank':>4}  {'Yr':7}  {'Check Amt':>10}  {'Inv Amt':>9}  {'OOI/VR':>8}  {'Product Line':25}  {'Store':7}  {'Cause':5}  {'Conf':5}  Notes")
print("-" * 130)

results = []
for rank, (idx, row) in enumerate(top25.iterrows(), 1):
    chk_amt    = row.get('Check Amount', 0)
    inv_amt    = pd.to_numeric(row.get('Invoice Amount', np.nan), errors='coerce')
    ooi_vr     = pd.to_numeric(row.get('OOI/VR', 0), errors='coerce') or 0
    material   = pd.to_numeric(row.get('MATERIAL', 0), errors='coerce') or 0
    product    = str(get_val(row, 'Product Line', 'Product Type', 'Product') or '').strip()
    store      = str(get_val(row, 'Store Number', 'Store') or '').strip()
    acct       = str(get_val(row, 'Account', 'Account Name', 'Dealer', 'CFI', 'CFI Name', 'Customer') or '').strip()
    desc       = str(get_val(row, 'Description', 'Memo', 'Notes', 'Comment') or '').strip()
    inv_num    = str(get_val(row, 'Invoice Number') or '').strip()
    yr         = row.get('_source_yr', '?')

    # ── Cause classification logic ──────────────────────────────────
    cause = 'UNK'
    conf  = 'LOW'
    notes = []

    # Check for textual memo signals first (strongest)
    desc_lower = desc.lower()
    if any(kw in desc_lower for kw in ['remake', 'remak', 're-make', 'recut', 're-cut', 'reorder', 're-order']):
        cause, conf = 'REM', 'HIGH'
        notes.append('memo=remake')
    elif any(kw in desc_lower for kw in ['break', 'broke', 'crack', 'crack', 'chip', 'damage', 'damagd']):
        cause, conf = 'BRK', 'HIGH'
        notes.append('memo=breakage')
    elif any(kw in desc_lower for kw in ['defect', 'defective', 'quality', 'warped', 'pitted', 'void']):
        cause, conf = 'DEF', 'HIGH'
        notes.append('memo=defect')
    elif any(kw in desc_lower for kw in ['install', 'service', 'callback', 'call back', 'trip']):
        cause, conf = 'SVC', 'HIGH'
        notes.append('memo=service')
    elif any(kw in desc_lower for kw in ['price', 'pricing', 'adjust', 'correction', 'correct', 'overcharge', 'credit memo', 'credit note']):
        cause, conf = 'PRC', 'HIGH'
        notes.append('memo=pricing')
    else:
        # No memo — fall back to structural signals
        notes.append('no-memo')

        # OOI/VR = 0 rules out pure pricing adjustment (pricing adj would retain/recover OOI)
        if abs(ooi_vr) < 0.01:
            notes.append('OOI=0→not-pricing-adj')
            # Pure credit with no OOI recovery = replaced/remade (Sage ate the cost)
            if abs(chk_amt) > 5000:
                # Large credit: likely full-job remake (not a $50 incidental)
                # Product-line refinement:
                if 'laminat' in product.lower() or 'postform' in product.lower():
                    cause, conf = 'REM', 'MED'
                    notes.append('product=laminate/postform→measure/cut-error')
                elif 'quartz' in product.lower() or 'granite' in product.lower() or 'stone' in product.lower() or 'natural' in product.lower():
                    cause, conf = 'REM', 'MED'
                    notes.append('product=stone→remake-or-breakage')
                elif 'porcelain' in product.lower() or 'ceramic' in product.lower():
                    cause, conf = 'REM', 'MED'
                    notes.append('product=porcelain→remake')
                else:
                    # Unknown product but large pure credit
                    cause, conf = 'REM', 'LOW'
                    notes.append('large-pure-credit→plausible-remake')
            elif abs(chk_amt) > 500:
                cause, conf = 'REM', 'LOW'
                notes.append('mid-size-pure-credit')
            else:
                cause, conf = 'UNK', 'LOW'
                notes.append('small-amount-no-signal')
        else:
            # OOI/VR ≠ 0: partial adjustment — pricing reversal or service credit more likely
            if ooi_vr < 0:
                cause, conf = 'PRC', 'MED'
                notes.append('OOI<0→pricing-or-billing-reversal')
            else:
                cause, conf = 'PRC', 'LOW'
                notes.append('OOI>0-unusual-partial')

    # Year-based confidence downgrade for 2023/2024 (fewer fields)
    if yr in ('2023', '2024') and conf == 'HIGH':
        conf = 'MED'
        notes.append('yr23/24-fewer-fields')

    inv_display = f'${inv_amt:,.0f}' if pd.notna(inv_amt) else 'n/a'
    ooi_display = f'${ooi_vr:,.0f}' if ooi_vr != 0 else '$0'
    store_display = store[:7] if store else 'n/a'
    product_display = product[:25] if product else 'n/a'

    results.append({
        'rank': rank,
        'yr': yr,
        'check_amt': chk_amt,
        'inv_amt': inv_amt,
        'ooi_vr': ooi_vr,
        'product': product,
        'store': store,
        'acct': acct,
        'desc': desc,
        'cause': cause,
        'conf': conf,
        'notes': ' | '.join(notes),
    })

    print(f"{rank:>4}  {yr:7}  {chk_amt:>10,.0f}  {inv_display:>9}  {ooi_display:>8}  "
          f"{product_display:25}  {store_display:7}  {cause:5}  {conf:5}  {' | '.join(notes)}")

# ─────────────────────────────────────────────────────────────
# STEP 6: AGGREGATE SUMMARY — % REMAKE vs NON-REMAKE CREDIT $
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 6: AGGREGATE — % OF TOP-25 CREDIT $ BY CAUSE")
print("=" * 72)

df_results = pd.DataFrame(results)
df_results['abs_amt'] = df_results['check_amt'].abs()

# Group by cause
by_cause = df_results.groupby('cause').agg(
    rows=('rank', 'count'),
    total_abs=('abs_amt', 'sum'),
    min_conf_high=('conf', lambda x: (x == 'HIGH').sum()),
    min_conf_med=('conf', lambda x: (x == 'MED').sum()),
    min_conf_low=('conf', lambda x: (x == 'LOW').sum()),
).reset_index()
total_top25 = df_results['abs_amt'].sum()
by_cause['pct_of_top25'] = (by_cause['total_abs'] / total_top25 * 100).round(1)
by_cause = by_cause.sort_values('total_abs', ascending=False)

print(f"\n  Top-25 total credit: ${total_top25:,.0f}")
print(f"\n{'Cause':6}  {'Lines':>5}  {'Total $':>12}  {'% of Top25':>10}  {'HIGH':>5}  {'MED':>5}  {'LOW':>5}  Description")
print("-" * 80)
CAUSE_DESC = {
    'REM': 'Remake (measurement/cut/template error)',
    'BRK': 'Breakage (transit/install damage)',
    'DEF': 'Material defect (vendor quality)',
    'SVC': 'Service/install callback',
    'PRC': 'Pricing adjustment / billing reversal',
    'UNK': 'Unknown / insufficient signal',
}
for _, row in by_cause.iterrows():
    c = row['cause']
    print(f"{c:6}  {row['rows']:>5,}  ${row['total_abs']:>11,.0f}  {row['pct_of_top25']:>10.1f}%  "
          f"{row['min_conf_high']:>5,}  {row['min_conf_med']:>5,}  {row['min_conf_low']:>5,}  "
          f"{CAUSE_DESC.get(c, c)}")

# Remake + Breakage + Defect = remake-related
remake_related_causes = {'REM', 'BRK', 'DEF'}
remake_total  = df_results[df_results['cause'].isin(remake_related_causes)]['abs_amt'].sum()
non_remake_total = df_results[~df_results['cause'].isin(remake_related_causes)]['abs_amt'].sum()

print(f"\n  ┌─────────────────────────────────────────────────────────────────")
print(f"  │  PLAUSIBLY REMAKE-RELATED (REM+BRK+DEF):  ${remake_total:>10,.0f}  ({remake_total/total_top25*100:.1f}%)")
print(f"  │  NON-REMAKE (PRC+SVC+UNK):                ${non_remake_total:>10,.0f}  ({non_remake_total/total_top25*100:.1f}%)")
print(f"  └─────────────────────────────────────────────────────────────────")

# ─────────────────────────────────────────────────────────────
# STEP 7: SCALE FROM TOP-25 TO FULL 620 POPULATION
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 7: SCALE-UP ESTIMATE — TOP-25 → FULL 620 POPULATION")
print("=" * 72)

all_neg_total = combined_neg['Check Amount'].abs().sum()
top25_pct_coverage = (total_top25 / all_neg_total * 100)
print(f"\n  Full 620-line population total: ${all_neg_total:,.0f}")
print(f"  Top-25 coverage of total $: ${total_top25:,.0f} ({top25_pct_coverage:.1f}%)")

if top25_pct_coverage > 50:
    print(f"\n  Top-25 represents >{top25_pct_coverage:.0f}% of total credit $ → scale-up is defensible")
    est_remake_pct = remake_total/total_top25
    est_remake_full = est_remake_pct * all_neg_total
    est_nonremake_full = all_neg_total - est_remake_full
    print(f"\n  Applied to full population at top-25 cause mix:")
    print(f"    Estimated remake-related: ${est_remake_full:,.0f} ({est_remake_pct*100:.1f}%)")
    print(f"    Estimated non-remake:     ${est_nonremake_full:,.0f} ({(1-est_remake_pct)*100:.1f}%)")
else:
    print(f"\n  WARNING: Top-25 covers only {top25_pct_coverage:.0f}% of total $ — scale-up less reliable")

# ─────────────────────────────────────────────────────────────
# STEP 8: MEMO/DESCRIPTION FIELD PRESENCE CHECK
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("STEP 8: MEMO/DESCRIPTION FIELD PRESENCE ACROSS ALL 620 ROWS")
print("=" * 72)

# Check which text-signal columns exist and how populated they are
text_cols = [c for c in combined_neg.columns if any(kw in c.lower() for kw in
             ['desc', 'memo', 'note', 'comment', 'reason', 'narr'])]
print(f"\nText-signal columns found: {text_cols}")
for c in text_cols:
    nonnull = combined_neg[c].astype(str).str.strip().replace('nan', '').str.len() > 0
    print(f"  {c:40s}: {nonnull.sum():,} of {len(combined_neg):,} rows non-empty ({nonnull.mean()*100:.1f}%)")

if not text_cols:
    print("  NO memo/description/narrative fields found in any audit file.")
    print("  Classification is structural-signal only (product line, OOI=0, amount magnitude).")

# Product line distribution across 620 rows
print("\nProduct Line distribution (all 620 neg-check rows):")
pl_col = None
for c in combined_neg.columns:
    if 'product' in c.lower() and ('line' in c.lower() or 'type' in c.lower()):
        pl_col = c
        break
if pl_col:
    pl_dist = combined_neg[pl_col].value_counts(dropna=False)
    print(pl_dist.to_string())
else:
    print("  No Product Line column found.")

# OOI/VR distribution across 620 rows
ooi_col = next((c for c in combined_neg.columns if 'ooi' in c.lower() or '/vr' in c.lower()), None)
if ooi_col:
    combined_neg[ooi_col] = pd.to_numeric(combined_neg[ooi_col], errors='coerce').fillna(0)
    print(f"\nOOI/VR breakdown (all 620 rows):")
    print(f"  OOI/VR = 0 (pure credit):   {(combined_neg[ooi_col] == 0).sum():,} rows")
    print(f"  OOI/VR < 0 (partial adj):   {(combined_neg[ooi_col] < 0).sum():,} rows")
    print(f"  OOI/VR > 0 (unusual):       {(combined_neg[ooi_col] > 0).sum():,} rows")

# Store number distribution (2025/2026 only)
store_col = next((c for c in combined_neg.columns if 'store' in c.lower() and 'number' in c.lower()), None)
if store_col:
    store_dist = combined_neg[combined_neg['_source_yr'].isin(['2025_V2', '2026_V2'])][store_col].value_counts()
    print(f"\nTop stores (2025/2026 negative-check rows — {store_dist.sum()} total):")
    print(store_dist.head(15).to_string())

print("\n" + "=" * 72)
print("ANALYSIS COMPLETE")
print("=" * 72)
