#!/usr/bin/env python3
"""
Export clean CDI query data that works properly in Excel
Handles long discharge summaries by truncating or using Excel format
"""

import pandas as pd
import json

print("="*80)
print("EXPORTING CLEAN CDI QUERY DATA FOR EXCEL")
print("="*80)

# Load data (pandas handles it correctly)
print("\n1. Loading 642 CDI queries...")
df = pd.read_csv('data/raw/642 CDI queries.csv', low_memory=False)
print(f"   ✓ Loaded {len(df)} rows")

# Keep only valid columns
valid_columns = ['anon_id', 'query_date', 'cdi_query', 'cdi_specialist_id',
                 'discharge_date', 'discharge_summary', 'days_after_discharge']
df_clean = df[valid_columns].copy()

print(f"\n2. Data quality check...")
print(f"   - Unique patients: {df_clean['anon_id'].nunique()}")
print(f"   - Non-null discharge summaries: {df_clean['discharge_summary'].notna().sum()}")
print(f"   - Non-null CDI queries: {df_clean['cdi_query'].notna().sum()}")

# Check for very long summaries
print(f"\n3. Checking discharge summary lengths...")
df_clean['summary_length'] = df_clean['discharge_summary'].apply(lambda x: len(str(x)) if pd.notna(x) else 0)
print(f"   - Average length: {df_clean['summary_length'].mean():.0f} chars")
print(f"   - Max length: {df_clean['summary_length'].max()} chars")
print(f"   - Summaries >10k chars: {(df_clean['summary_length'] > 10000).sum()}")
print(f"   - Summaries >20k chars: {(df_clean['summary_length'] > 20000).sum()}")

# Export Option 1: CSV with aggressive quoting
print(f"\n4. Exporting CSV with aggressive quoting...")
output_csv = 'data/processed/cdi_queries_clean.csv'
df_clean.drop('summary_length', axis=1).to_csv(
    output_csv,
    index=False,
    quoting=1,  # QUOTE_ALL
    escapechar='\\',
    doublequote=True
)
print(f"   ✓ Saved to: {output_csv}")

# Export Option 2: Excel format (handles long text better)
print(f"\n5. Exporting to Excel format...")
output_excel = 'data/processed/cdi_queries_clean.xlsx'
try:
    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
        df_clean.drop('summary_length', axis=1).to_excel(writer, index=False, sheet_name='CDI_Queries')
    print(f"   ✓ Saved to: {output_excel}")
    print(f"   ✓ Excel format handles long text better!")
except Exception as e:
    print(f"   ⚠️  Excel export failed: {e}")
    print(f"   (openpyxl may need to be installed: pip install openpyxl)")

# Export Option 3: JSON for corrupted cases
print(f"\n6. Exporting corrupted cases to JSON...")
corrupted_ids = ['JC3181660', 'JC2500223', 'JC6421754', 'JC689001', 'JC2222851',
                 'JC825333', 'JC2785890', 'JC1546925', 'JC6547235', 'JC6556787',
                 'JC6488705', 'JC6537997', 'JC1077361']

corrupted_data = df_clean[df_clean['anon_id'].isin(corrupted_ids)].copy()
corrupted_data = corrupted_data.drop('summary_length', axis=1)

print(f"   Found {len(corrupted_data)} rows for {len(corrupted_ids)} patient IDs")

# Convert to dict for JSON export
corrupted_records = corrupted_data.to_dict('records')
output_json = 'data/processed/corrupted_cases.json'
with open(output_json, 'w') as f:
    json.dump(corrupted_records, f, indent=2, default=str)

print(f"   ✓ Saved to: {output_json}")
print(f"   ✓ Contains full discharge summaries for corrupted cases")

# Verify the exports
print(f"\n7. Verifying exports...")
df_test = pd.read_csv(output_csv)
print(f"   CSV: {len(df_test)} rows, {len(df_test.columns)} columns")

try:
    df_test_excel = pd.read_excel(output_excel)
    print(f"   Excel: {len(df_test_excel)} rows, {len(df_test_excel.columns)} columns")
except:
    pass

print(f"\n" + "="*80)
print("EXPORT OPTIONS CREATED")
print("="*80)
print(f"\nYou now have 3 options:")
print(f"1. CSV (all data):     {output_csv}")
print(f"2. Excel (all data):   {output_excel} ← BEST for viewing in Excel")
print(f"3. JSON (corrupted):   {output_json} ← Full data for problematic cases")
print(f"\nRecommendation: Use Excel file for viewing, CSV for code")
