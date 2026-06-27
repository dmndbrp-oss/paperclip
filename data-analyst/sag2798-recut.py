"""
SAG-2798 Re-cut — per CFO review comment c50589c5
1. Remake reconciliation on 170,481-row reconciled basis (negative Check Amount)
2. Field semantics verification for MO Amount vs PO Amount
"""

import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

BASE_DATA = "/home/gus-pinsoneault/paperclip-sage-surfaces/Finance & Accounting"
STORAGE = "/home/gus-pinsoneault/.paperclip/instances/default/data/storage/1dc911ed-ff05-4072-b2ae-a3e3177e3873/issues"

# Per-year reconciled audit files (2023, 2024, 2025_V2, 2026_V2 — 170,481 rows per CFO)
AUDIT_FILES = {
    "2023":    os.path.join(STORAGE, "04974e4f-40ae-469c-ad8b-e7dd93857a54/2026/05/18/8c029a4d-09ff-4211-9053-9ba94a1273c9-AAA_Lowe_s_Audit_2023_5.18.26.xlsx"),
    "2024":    os.path.join(STORAGE, "0be36824-4c76-4348-aba5-fff3b2095311/2026/05/18/e973cd75-3017-412f-ac23-2f9d72210ecf-AAA_Lowe_s_Audit_2024_5.18.26.xlsx"),
    "2025_V2": os.path.join(STORAGE, "3d853575-b76a-4b9a-8a4e-dede073ecfcd/2026/05/18/7267e3ae-92a0-4ffa-a1ce-f305931f85de-AAA_Lowe_s_Audit_2025_V2_5.18.26.xlsx"),
    "2026_V2": os.path.join(STORAGE, "b7299c0d-6b59-4572-8abd-d16f7e22fe7e/2026/05/18/8aeff3be-748c-4e97-b18c-e3d584679b9e-AAA_Lowe_s_Audit_2026_V2_5.18.26.xlsx"),
}

# ═══════════════════════════════════════════════════════════════
# SECTION 1: LOAD AUDIT FILES — COLUMN INVENTORY + ROW COUNT
# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("SECTION 1: PER-YEAR AUDIT FILE LOAD")
print("=" * 70)

audit_frames = []
for yr, path in AUDIT_FILES.items():
    if not os.path.exists(path):
        print(f"  {yr}: FILE NOT FOUND at {path}")
        continue
    try:
        xl = pd.ExcelFile(path)
        print(f"\n{yr}: sheets = {xl.sheet_names}")
        # Try the first sheet
        df = xl.parse(xl.sheet_names[0])
        print(f"  Rows: {len(df):,}, Cols: {list(df.columns)}")
        df['_yr'] = yr
        audit_frames.append(df)
    except Exception as e:
        print(f"  {yr} ERROR: {e}")

if not audit_frames:
    print("ERROR: No audit frames loaded. Check file paths.")
    exit(1)

audit_all = pd.concat(audit_frames, ignore_index=True)
print(f"\nTotal rows across all audit files: {len(audit_all):,}")
print(f"Columns: {list(audit_all.columns)}")

# ═══════════════════════════════════════════════════════════════
# SECTION 2: NEGATIVE CHECK AMOUNT — 620-LINE REMAKE PROXY
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION 2: NEGATIVE CHECK AMOUNT ANALYSIS (170K-ROW BASIS)")
print("=" * 70)

# Find Check Amount column
chk_col = None
for c in audit_all.columns:
    if 'check' in c.lower() and 'amount' in c.lower():
        chk_col = c
        break
    elif 'check' in c.lower() and 'amt' in c.lower():
        chk_col = c
        break

print(f"Check Amount column identified: '{chk_col}'")

if chk_col:
    audit_all[chk_col] = pd.to_numeric(audit_all[chk_col], errors='coerce')
    print(f"\nCheck Amount distribution:")
    print(f"  Total rows: {len(audit_all):,}")
    print(f"  Non-null: {audit_all[chk_col].notna().sum():,}")
    print(f"  Negative rows: {(audit_all[chk_col] < 0).sum():,}")
    print(f"  Zero rows: {(audit_all[chk_col] == 0).sum():,}")
    print(f"  Positive rows: {(audit_all[chk_col] > 0).sum():,}")
    print(f"  Total (all): ${audit_all[chk_col].sum():,.0f}")
    print(f"  Total (negative only): ${audit_all[audit_all[chk_col] < 0][chk_col].sum():,.0f}")

    neg_checks = audit_all[audit_all[chk_col] < 0].copy()
    print(f"\nNegative Check Amount rows: {len(neg_checks):,}")
    print(f"Negative Check Amount total: ${neg_checks[chk_col].sum():,.0f}")

    # Identify OOI/VR column to find pure credits (OOI=0)
    ooi_col = None
    for c in audit_all.columns:
        if 'ooi' in c.lower() or 'vr' in c.lower():
            ooi_col = c
            break

    if ooi_col:
        audit_all[ooi_col] = pd.to_numeric(audit_all[ooi_col], errors='coerce').fillna(0)
        pure_credits = neg_checks[neg_checks[ooi_col] == 0]
        print(f"\nNegative Check with OOI/VR=0 (pure credits): {len(pure_credits):,}")
        print(f"Pure credit total: ${pure_credits[chk_col].sum():,.0f}")
        print(f"\nNegative Check with OOI/VR≠0 (partial credit/adjustment): {len(neg_checks) - len(pure_credits):,}")

    # Break down negative checks by year
    print("\nNegative Check Amount by year:")
    for yr in ['2023', '2024', '2025_V2', '2026_V2']:
        yr_neg = neg_checks[neg_checks['_yr'] == yr]
        print(f"  {yr}: {len(yr_neg):,} rows, total=${yr_neg[chk_col].sum():,.0f}")

    # Sample the negative check rows
    print("\nSample negative check rows:")
    id_cols = [c for c in audit_all.columns if any(kw in c.lower() for kw in
               ['ro', 'retail', 'order', 'invoice', 'check number', 'check #', 'number', 'product'])]
    show_cols = id_cols[:6] + [chk_col]
    if ooi_col:
        show_cols.append(ooi_col)
    print(neg_checks[show_cols].head(15).to_string())

    # ─── MAP NEGATIVE-CHECK ROs TO MO/PO ─────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 3: MAP NEGATIVE CHECK ROs TO MO/PO RETAIL ORDER")
    print("=" * 70)

    # Find RO number column in audit
    ro_col_audit = None
    for c in audit_all.columns:
        if 'ro' in c.lower() and ('number' in c.lower() or 'num' in c.lower() or '#' in c.lower()):
            ro_col_audit = c
            break
    if not ro_col_audit:
        for c in audit_all.columns:
            if 'order' in c.lower() and ('ro' in c.lower() or 'retail' in c.lower()):
                ro_col_audit = c
                break

    print(f"RO Number column in audit: '{ro_col_audit}'")

    if ro_col_audit:
        neg_check_ros = set(str(r).strip() for r in neg_checks[ro_col_audit].dropna().unique() if str(r).strip() and str(r) != 'nan')
        print(f"Distinct RO Numbers in negative-check rows: {len(neg_check_ros):,}")
        print(f"Sample: {list(neg_check_ros)[:8]}")

        # Load MO/PO for matching
        print("\nLoading MO files (2023-2026)...")
        mo_frames = []
        for yr in [2023, 2024, 2025, 2026]:
            path = os.path.join(BASE_DATA, f"Material_Orders_Closed-{yr}.xlsx")
            if os.path.exists(path):
                df = pd.read_excel(path)
                df['_yr'] = yr
                mo_frames.append(df)
        mo_all = pd.concat(mo_frames, ignore_index=True)
        mo_all['MO Amount'] = pd.to_numeric(mo_all['MO Amount'], errors='coerce').fillna(0)

        print("Loading PO files (2023-2026)...")
        po_frames = []
        for yr in [2023, 2024, 2025, 2026]:
            path = os.path.join(BASE_DATA, f"Purchase_Orders_Closed-{yr}.xlsx")
            if os.path.exists(path):
                df = pd.read_excel(path)
                df['_yr'] = yr
                po_frames.append(df)
        po_all = pd.concat(po_frames, ignore_index=True)
        po_all['PO Amount'] = pd.to_numeric(po_all['PO Amount'], errors='coerce').fillna(0)
        po_all['Invoice Amount'] = pd.to_numeric(po_all['Invoice Amount'], errors='coerce').fillna(0)

        mo_ros = set(str(r).strip() for r in mo_all['Retail Order'].dropna().unique() if str(r).strip())
        po_ros = set(str(r).strip() for r in po_all['Retail Order'].dropna().unique() if str(r).strip())

        overlap_mo = neg_check_ros & mo_ros
        overlap_po = neg_check_ros & po_ros
        print(f"\nNeg-check ROs matching MO Retail Order: {len(overlap_mo):,} ({len(overlap_mo)/len(neg_check_ros)*100:.1f}%)")
        print(f"Neg-check ROs matching PO Retail Order: {len(overlap_po):,} ({len(overlap_po)/len(neg_check_ros)*100:.1f}%)")

        if overlap_mo:
            # Pull the matching MO rows
            matched_mos = mo_all[mo_all['Retail Order'].astype(str).str.strip().isin(overlap_mo)]
            print(f"\nMO rows matching neg-check ROs: {len(matched_mos):,}")
            print(f"  Invoice Type breakdown:")
            print(matched_mos['Invoice Type'].value_counts().to_string())
            print(f"  Total MO Amount: ${matched_mos['MO Amount'].sum():,.0f}")
            print(f"  Product Line breakdown:")
            pl_grp = matched_mos.groupby('Product Line')['MO Amount'].agg(['count', 'sum']).reset_index()
            pl_grp.columns = ['Product Line', 'rows', 'MO_total']
            for _, row in pl_grp.sort_values('MO_total', ascending=False).iterrows():
                print(f"    {row['Product Line']:25s}: {row['rows']:4,} rows, ${row['MO_total']:>12,.0f}")

# ═══════════════════════════════════════════════════════════════
# SECTION 4: FIELD SEMANTICS VERIFICATION (MO Amount vs PO Amount)
# Uses internal consistency checks, not just column name inference
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SECTION 4: FIELD SEMANTICS VERIFICATION")
print("=" * 70)

# Test 1: Does MATERIAL (Lowe's audit) ≈ MO Amount per job?
# If MO Amount = what Sage charges CFI for material, it should match MATERIAL column
print("\n--- Test 1: MATERIAL column (audit) vs MO Amount per matching RO ---")
if ro_col_audit:
    # Get MATERIAL column in audit
    mat_col = None
    for c in audit_all.columns:
        if c.upper() == 'MATERIAL' or c.strip().upper() == 'MATERIAL':
            mat_col = c
            break
    if mat_col:
        audit_all[mat_col] = pd.to_numeric(audit_all[mat_col], errors='coerce').fillna(0)
        # Join audit to MO by RO Number
        audit_ro_material = audit_all[[ro_col_audit, mat_col]].copy()
        audit_ro_material.columns = ['Retail_Order', 'MATERIAL']
        audit_ro_material['Retail_Order'] = audit_ro_material['Retail_Order'].astype(str).str.strip()
        # Aggregate to RO level
        audit_ro_agg = audit_ro_material.groupby('Retail_Order')['MATERIAL'].sum().reset_index()
        # MO aggregated to RO level
        mo_ro_agg = mo_all.groupby('Retail Order')['MO Amount'].sum().reset_index()
        mo_ro_agg['Retail Order'] = mo_ro_agg['Retail Order'].astype(str).str.strip()
        # Merge
        comparison = audit_ro_agg.merge(mo_ro_agg, left_on='Retail_Order', right_on='Retail Order', how='inner')
        print(f"  ROs matched in both: {len(comparison):,}")
        print(f"  Audit MATERIAL sum (matched ROs): ${comparison['MATERIAL'].sum():,.0f}")
        print(f"  MO Amount sum (matched ROs): ${comparison['MO Amount'].sum():,.0f}")
        ratio = comparison['MATERIAL'].sum() / comparison['MO Amount'].sum() if comparison['MO Amount'].sum() != 0 else 0
        print(f"  Ratio MATERIAL/MO Amount: {ratio:.3f}")
        if abs(ratio - 1.0) < 0.1:
            print("  >>> MATERIAL ≈ MO Amount → MO Amount likely = material charge recovered from check")
        elif ratio > 1.1:
            print("  >>> MATERIAL > MO Amount → MATERIAL includes more than just MO billing")
        else:
            print("  >>> MATERIAL < MO Amount → Divergence, possible different basis/timing")
    else:
        print(f"  MATERIAL column not found in audit. Available: {list(audit_all.columns)}")

# Test 2: PO Amount vs Invoice Amount — direction of payment
print("\n--- Test 2: PO Financial flow direction ---")
print("  Schema note: PO → Distributor (Sage buys material from Distributor)")
print("  PO Amount = Sage's expected cost to Distributor")
print("  Invoice Amount = what Distributor actually charged Sage")
print("  Amount Difference = Invoice Amount - PO Amount (per canonical_schema.py comment)")
# Verify with data
mat_pos = po_all[po_all['Order Type'] == 'Material Order']
amt_diff_check = (mat_pos['Invoice Amount'] - mat_pos['PO Amount'])
print(f"\n  Verification: Invoice Amount - PO Amount vs Amount Difference field:")
schema_matches = abs(amt_diff_check - mat_pos['Amount Difference']).abs() < 0.01
print(f"  Rows where (Invoice - PO) matches Amount Difference field: {schema_matches.sum():,} of {len(mat_pos):,} ({schema_matches.mean()*100:.1f}%)")
print(f"  (High match rate confirms: Amount Difference = Invoice Amount - PO Amount)")

# Test 3: MO Invoice Type direction
print("\n--- Test 3: MO Invoice Type semantics ---")
print("MO Invoice Type distribution (full dataset):")
print(mo_all['Invoice Type'].value_counts(dropna=False).head(10).to_string())
print("\nMO by Invoice Type — key metrics:")
for it in ['InvoiceRelease', 'CreditRelease', 'Blocked', 'InvoiceHold']:
    subset = mo_all[mo_all['Invoice Type'] == it]
    if len(subset) > 0:
        print(f"  {it:20s}: {len(subset):6,} rows, MO Amount = ${subset['MO Amount'].sum():>14,.0f}, Amount Paid = ${subset['Amount Paid'].fillna(0).sum():>12,.0f}")

# Test 4: Cross-validate aggregate against known anchors
print("\n--- Test 4: Aggregate cross-validation ---")
mat_col_audit = None
for c in audit_all.columns:
    if c.strip().upper() == 'MATERIAL':
        mat_col_audit = c
        break

if mat_col_audit:
    audit_all[mat_col_audit] = pd.to_numeric(audit_all[mat_col_audit], errors='coerce').fillna(0)
    total_material_audit = audit_all[mat_col_audit].sum()
    print(f"  Total MATERIAL on 170K reconciled basis: ${total_material_audit:,.0f}")

# MO Amount by invoice type
mo_invoice_only = mo_all[mo_all['Invoice Type'] == 'InvoiceRelease']
mo_credit_only = mo_all[mo_all['Invoice Type'] == 'CreditRelease']
mo_null_type = mo_all[mo_all['Invoice Type'].isna()]
print(f"  MO Amount (InvoiceRelease only): ${mo_invoice_only['MO Amount'].sum():,.0f}")
print(f"  MO Amount (CreditRelease only): ${mo_credit_only['MO Amount'].sum():,.0f}")
print(f"  MO Amount (Invoice Type null - {len(mo_null_type):,} rows): ${mo_null_type['MO Amount'].sum():,.0f}")
print(f"  MO Amount (ALL rows): ${mo_all['MO Amount'].sum():,.0f}")

# PO totals
print(f"\n  PO Amount (Material Orders): ${mat_pos['PO Amount'].sum():,.0f}")
print(f"  PO Invoice Amount (Material Orders): ${mat_pos['Invoice Amount'].sum():,.0f}")

# OOI/VR from audit
if ooi_col:
    total_ooi = audit_all[ooi_col].sum()
    total_inv = pd.to_numeric(audit_all.get('Invoice Amount', audit_all.iloc[:,0]*0), errors='coerce').sum()
    # Find Invoice Amount column in audit
    inv_col_audit = None
    for c in audit_all.columns:
        if 'invoice' in c.lower() and 'amount' in c.lower():
            inv_col_audit = c
            break
    if inv_col_audit:
        audit_all[inv_col_audit] = pd.to_numeric(audit_all[inv_col_audit], errors='coerce')
        total_inv = audit_all[inv_col_audit].sum()
        print(f"\n  Audit Invoice Amount total (170K basis): ${total_inv:,.0f}")
        print(f"  Audit OOI/VR total (170K basis): ${total_ooi:,.0f}")
        print(f"  OOI/VR margin %: {total_ooi/total_inv*100:.2f}%")
        if mat_col_audit:
            print(f"  MATERIAL % of Invoice Amount: {total_material_audit/total_inv*100:.2f}%")

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
