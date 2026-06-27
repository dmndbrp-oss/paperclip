"""
SAG-2798 — Material Deduction & Job-Level Key Analysis
Board scope correction 2026-06-02:
  - Drop Lever 3 (slab scrap)
  - Add: material-deduction spread (what Sage pays vs deducts)
  - Add: job-level key for remake proxy ($4.32M / 620 lines)
"""

import pandas as pd
import os
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = "/home/gus-pinsoneault/paperclip-sage-surfaces/Finance & Accounting"
YEARS = [2023, 2024, 2025, 2026]

# ──────────────────────────────────────────────────────────────
# 0. Column inventory for all key files
# ──────────────────────────────────────────────────────────────
print("=" * 70)
print("SECTION 0: COLUMN INVENTORY")
print("=" * 70)

files_to_inspect = {
    "Dealers.xlsx": None,
    "Installers.xlsx": None,
    "Material_Orders_Closed-2023.xlsx": None,
    "Material_Orders_Closed-2024.xlsx": None,
    "Purchase_Orders_Closed-2023.xlsx": None,
    "Purchase_Orders_Closed-2024.xlsx": None,
}

for fname in files_to_inspect:
    path = os.path.join(DATA_DIR, fname)
    try:
        xl = pd.ExcelFile(path)
        print(f"\n--- {fname} ---")
        print(f"  Sheets: {xl.sheet_names}")
        for sheet in xl.sheet_names[:3]:  # First 3 sheets
            df = xl.parse(sheet, nrows=2)
            print(f"  Sheet '{sheet}' ({len(df.columns)} cols): {list(df.columns)}")
    except Exception as e:
        print(f"  ERROR: {e}")

# ──────────────────────────────────────────────────────────────
# 1. MATERIAL DEDUCTION MECHANISM
#    Material_Orders_Closed = what Sage pays to material vendors
#    Purchase_Orders_Closed = what Sage charges (deducts) from fabricator orders
#    Dealers.xlsx = dealer/fabricator remittances
# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SECTION 1: MATERIAL DEDUCTION MECHANISM")
print("=" * 70)

# Load Material Orders (what Sage pays)
mo_frames = []
for yr in YEARS:
    path = os.path.join(DATA_DIR, f"Material_Orders_Closed-{yr}.xlsx")
    if not os.path.exists(path):
        continue
    try:
        df = pd.read_excel(path)
        df['_year'] = yr
        mo_frames.append(df)
        print(f"  MO {yr}: {len(df)} rows, cols: {list(df.columns)}")
    except Exception as e:
        print(f"  MO {yr} ERROR: {e}")

if mo_frames:
    mo_all = pd.concat(mo_frames, ignore_index=True)
    print(f"\nTotal MO rows (2023-2026): {len(mo_all):,}")
    print("\nMO numeric column summary:")
    print(mo_all.select_dtypes(include='number').describe().to_string())

# Load Purchase Orders (what Sage charges/deducts)
po_frames = []
for yr in YEARS:
    path = os.path.join(DATA_DIR, f"Purchase_Orders_Closed-{yr}.xlsx")
    if not os.path.exists(path):
        continue
    try:
        df = pd.read_excel(path)
        df['_year'] = yr
        po_frames.append(df)
        print(f"\n  PO {yr}: {len(df)} rows, cols: {list(df.columns)}")
    except Exception as e:
        print(f"  PO {yr} ERROR: {e}")

if po_frames:
    po_all = pd.concat(po_frames, ignore_index=True)
    print(f"\nTotal PO rows (2023-2026): {len(po_all):,}")
    print("\nPO numeric column summary:")
    print(po_all.select_dtypes(include='number').describe().to_string())

# ──────────────────────────────────────────────────────────────
# 2. FIND JOIN KEYS between MO and PO
#    Looking for Retail Order / RO Number / common identifier
# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SECTION 2: JOIN KEY DISCOVERY")
print("=" * 70)

if mo_frames and po_frames:
    mo_cols_lower = {c.lower().replace(' ', '_').replace('-', '_'): c for c in mo_all.columns}
    po_cols_lower = {c.lower().replace(' ', '_').replace('-', '_'): c for c in po_all.columns}

    print("\nMO column names:", list(mo_all.columns))
    print("\nPO column names:", list(po_all.columns))

    # Find common columns
    common = set(mo_all.columns) & set(po_all.columns)
    print(f"\nShared column names: {sorted(common)}")

    # Look for RO-like columns in each
    ro_keywords = ['retail', 'ro', 'order', 'job', 'invoice', 'reference', 'ref']
    print("\nMO columns containing order/job/ro/retail keywords:")
    for c in mo_all.columns:
        if any(kw in c.lower() for kw in ro_keywords):
            print(f"  '{c}': sample={mo_all[c].dropna().head(3).tolist()}")

    print("\nPO columns containing order/job/ro/retail keywords:")
    for c in po_all.columns:
        if any(kw in c.lower() for kw in ro_keywords):
            print(f"  '{c}': sample={po_all[c].dropna().head(3).tolist()}")

# ──────────────────────────────────────────────────────────────
# 3. MATERIAL COST vs DEDUCTION SPREAD (if joinable)
#    - What MO Amount = Sage pays vendor
#    - PO Amount = what Sage charges to fabricator's retail order
#    - Spread = PO Amount - MO Amount
# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SECTION 3: MATERIAL COST vs DEDUCTION (SPREAD ANALYSIS)")
print("=" * 70)

if mo_frames and po_frames:
    # Identify amount columns
    mo_amt_cols = [c for c in mo_all.columns if 'amount' in c.lower() or 'cost' in c.lower() or 'price' in c.lower()]
    po_amt_cols = [c for c in po_all.columns if 'amount' in c.lower() or 'cost' in c.lower() or 'price' in c.lower()]

    print(f"\nMO amount-like cols: {mo_amt_cols}")
    print(f"PO amount-like cols: {po_amt_cols}")

    # Aggregate MO by product line
    pl_cols_mo = [c for c in mo_all.columns if 'product' in c.lower() or 'line' in c.lower() or 'type' in c.lower()]
    if pl_cols_mo and mo_amt_cols:
        print(f"\nMO by product line (col: '{pl_cols_mo[0]}', amt: '{mo_amt_cols[0]}'):")
        try:
            grp = mo_all.groupby(pl_cols_mo[0])[mo_amt_cols[0]].agg(['count', 'sum', 'mean'])
            grp.columns = ['rows', 'total_$', 'avg_$']
            grp['total_$'] = grp['total_$'].map('${:,.0f}'.format)
            grp['avg_$'] = grp['avg_$'].map('${:,.0f}'.format)
            print(grp.to_string())
        except Exception as e:
            print(f"  Groupby error: {e}")

    # Aggregate PO by product line
    pl_cols_po = [c for c in po_all.columns if 'product' in c.lower() or 'line' in c.lower() or 'type' in c.lower()]
    if pl_cols_po and po_amt_cols:
        print(f"\nPO by product line (col: '{pl_cols_po[0]}', amt: '{po_amt_cols[0]}'):")
        try:
            grp = po_all.groupby(pl_cols_po[0])[po_amt_cols[0]].agg(['count', 'sum', 'mean'])
            grp.columns = ['rows', 'total_$', 'avg_$']
            grp['total_$'] = grp['total_$'].map('${:,.0f}'.format)
            grp['avg_$'] = grp['avg_$'].map('${:,.0f}'.format)
            print(grp.to_string())
        except Exception as e:
            print(f"  Groupby error: {e}")

# ──────────────────────────────────────────────────────────────
# 4. DEALERS / INSTALLERS — structure & deduction fields
# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SECTION 4: DEALERS & INSTALLERS WORKBOOKS")
print("=" * 70)

for fname in ["Dealers.xlsx", "Installers.xlsx"]:
    path = os.path.join(DATA_DIR, fname)
    try:
        xl = pd.ExcelFile(path)
        print(f"\n--- {fname} ---")
        print(f"  Sheets: {xl.sheet_names}")
        for sheet in xl.sheet_names:
            try:
                df = xl.parse(sheet)
                print(f"\n  Sheet '{sheet}': {len(df):,} rows x {len(df.columns)} cols")
                print(f"  Columns: {list(df.columns)}")
                # Numeric summary
                num_cols = df.select_dtypes(include='number').columns.tolist()
                if num_cols:
                    print(f"  Numeric cols: {num_cols}")
                    print(df[num_cols].describe().to_string())
                # Sample RO / order-like columns
                for c in df.columns:
                    if any(kw in c.lower() for kw in ['ro', 'retail', 'order', 'job', 'invoice', 'deduct', 'credit', 'remake']):
                        print(f"  Key col '{c}' sample: {df[c].dropna().head(5).tolist()}")
            except Exception as e:
                print(f"  Sheet '{sheet}' ERROR: {e}")
    except Exception as e:
        print(f"  {fname} ERROR: {e}")

# ──────────────────────────────────────────────────────────────
# 5. AAA WORKBOOK — REMAKE / CREDIT LINE PROXY
#    Board says: 620 lines / -$4.32M → find job-level key
# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SECTION 5: AAA WORKBOOK — REMAKE / CREDIT ANALYSIS")
print("=" * 70)

aaa_path = os.path.join(DATA_DIR, "AAA Material Income Estimate - All Accounts.xlsx")
try:
    xl = pd.ExcelFile(aaa_path)
    print(f"Sheets: {xl.sheet_names}")

    # Check each sheet for credit/remake patterns
    for sheet in xl.sheet_names:
        try:
            # Read header only first
            df_head = xl.parse(sheet, nrows=3)
            cols = list(df_head.columns)
            credit_cols = [c for c in cols if any(kw in str(c).lower() for kw in
                           ['credit', 'remake', 'refund', 'adjustment', 'negative', 'return'])]
            if credit_cols:
                print(f"\nSheet '{sheet}' has credit-like cols: {credit_cols}")

            # Look for negative Invoice Amount rows on Lowe's sheet
            if 'Lowes' in sheet or 'Lowe' in sheet or sheet == "Lowe's":
                print(f"\nAnalyzing '{sheet}' for credit/remake lines...")
                df = xl.parse(sheet)
                print(f"  Shape: {df.shape}")
                print(f"  Columns: {list(df.columns)}")

                # Find Invoice Amount column
                inv_col = None
                for c in df.columns:
                    if 'invoice' in str(c).lower() and 'amount' in str(c).lower():
                        inv_col = c
                        break
                if not inv_col:
                    for c in df.columns:
                        if 'amount' in str(c).lower():
                            inv_col = c
                            break

                if inv_col:
                    print(f"  Invoice Amount column: '{inv_col}'")
                    df[inv_col] = pd.to_numeric(df[inv_col], errors='coerce')
                    neg = df[df[inv_col] < 0]
                    print(f"  Negative rows: {len(neg):,}")
                    print(f"  Negative sum: ${neg[inv_col].sum():,.0f}")
                    if len(neg) > 0:
                        print(f"  Negative row columns sample:")
                        print(neg.head(5).to_string())

                        # Check for job-level keys in negative rows
                        id_cols = [c for c in df.columns if any(kw in str(c).lower() for kw in
                                   ['ro', 'retail', 'order', 'job', 'invoice', 'check', 'ref', 'id', 'number', 'key'])]
                        print(f"\n  ID-like columns in this sheet: {id_cols}")
                        for c in id_cols[:8]:
                            print(f"    '{c}': neg sample = {neg[c].dropna().head(3).tolist()}")

        except Exception as e:
            print(f"  Sheet '{sheet}' ERROR: {e}")

except Exception as e:
    print(f"AAA file ERROR: {e}")

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
