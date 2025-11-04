#!/usr/bin/env python3
"""
Fix the corrupted 642 CDI queries CSV file
Issue: Discharge summaries contain newlines and commas, causing Excel to split rows incorrectly
Solution: Re-export with proper quoting
"""

import pandas as pd

print("="*80)
print("FIXING 642 CDI QUERIES CSV")
print("="*80)

# Load the file (pandas handles it correctly)
print("\n1. Loading original CSV...")
df = pd.read_csv('data/raw/642 CDI queries.csv')

print(f"   ✓ Loaded {len(df)} rows")
print(f"   ✓ Found {len(df.columns)} columns (expected 7 + {len(df.columns)-7} unnamed)")

# Keep only the valid columns
valid_columns = ['anon_id', 'query_date', 'cdi_query', 'cdi_specialist_id',
                 'discharge_date', 'discharge_summary', 'days_after_discharge']

print(f"\n2. Keeping only valid columns...")
df_clean = df[valid_columns].copy()
print(f"   ✓ Kept {len(df_clean.columns)} columns")

# Check for issues
print(f"\n3. Data quality check...")
print(f"   - Rows: {len(df_clean)}")
print(f"   - Unique patients: {df_clean['anon_id'].nunique()}")
print(f"   - Non-null discharge summaries: {df_clean['discharge_summary'].notna().sum()}")
print(f"   - Non-null CDI queries: {df_clean['cdi_query'].notna().sum()}")

# Save with proper quoting
output_file = 'data/raw/642_CDI_queries_FIXED.csv'
print(f"\n4. Saving fixed CSV with proper quoting...")
df_clean.to_csv(output_file, index=False, quoting=1)  # quoting=1 = QUOTE_ALL
print(f"   ✓ Saved to: {output_file}")

# Verify the fix
print(f"\n5. Verifying fix...")
df_test = pd.read_csv(output_file)
print(f"   ✓ Re-loaded: {len(df_test)} rows, {len(df_test.columns)} columns")

if len(df_test) == len(df_clean) and len(df_test.columns) == len(valid_columns):
    print(f"\n✅ SUCCESS! CSV fixed and ready for Excel")
    print(f"   Original: 642 CDI queries.csv (corrupted)")
    print(f"   Fixed:    642_CDI_queries_FIXED.csv")
else:
    print(f"\n❌ ERROR: Verification failed")
    print(f"   Expected: {len(df_clean)} rows, {len(valid_columns)} cols")
    print(f"   Got: {len(df_test)} rows, {len(df_test.columns)} cols")

print("\n" + "="*80)
print("COMPLETE")
print("="*80)
