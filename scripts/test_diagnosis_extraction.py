#!/usr/bin/env python3
"""
Quick test of CDI diagnosis extraction from query text
"""

import pandas as pd
import sys
sys.path.insert(0, '/Users/chukanya/Documents/Coding/New_CDI/scripts')
from evaluate_on_new_cdi_queries import extract_cdi_diagnosis_from_query

print("="*80)
print("TESTING CDI DIAGNOSIS EXTRACTION")
print("="*80)

# Load new dataset
df = pd.read_csv('data/raw/642 CDI queries.csv')

print(f"\nDataset: {len(df)} CDI queries")
print(f"Non-null query text: {df['cdi_query'].notna().sum()}")

# Test extraction on first 10 cases
print("\n" + "="*80)
print("TESTING ON FIRST 10 CASES")
print("="*80)

for i in range(min(10, len(df))):
    query_text = df.iloc[i]['cdi_query']

    if pd.isna(query_text):
        print(f"\nCase {i+1}: [MISSING QUERY TEXT]")
        continue

    diagnoses = extract_cdi_diagnosis_from_query(query_text)

    print(f"\nCase {i+1}:")
    print(f"  Query length: {len(query_text)} chars")

    if diagnoses:
        print(f"  ✓ Extracted {len(diagnoses)} diagnosis(es):")
        for dx in diagnoses:
            print(f"    - {dx}")
    else:
        print(f"  ✗ No diagnoses extracted")
        # Show snippet to debug
        print(f"  Query snippet: {query_text[:200]}...")

# Overall statistics
print("\n" + "="*80)
print("EXTRACTION STATISTICS")
print("="*80)

df['extracted_diagnoses'] = df['cdi_query'].apply(extract_cdi_diagnosis_from_query)
df['num_extracted'] = df['extracted_diagnoses'].apply(len)

print(f"\nTotal queries: {len(df)}")
print(f"Queries with extracted diagnoses: {(df['num_extracted'] > 0).sum()}")
print(f"Queries with no extraction: {(df['num_extracted'] == 0).sum()}")
print(f"\nAverage diagnoses per query: {df['num_extracted'].mean():.2f}")
print(f"Max diagnoses in a query: {df['num_extracted'].max()}")

# Show most common diagnoses
print("\n" + "="*80)
print("MOST COMMON EXTRACTED DIAGNOSES (Top 20)")
print("="*80)

all_diagnoses = []
for dx_list in df['extracted_diagnoses']:
    all_diagnoses.extend(dx_list)

from collections import Counter
diagnosis_counts = Counter(all_diagnoses)

for i, (dx, count) in enumerate(diagnosis_counts.most_common(20), 1):
    pct = count / len(df) * 100
    print(f"{i:2d}. {dx:<60s} ({count:3d}x, {pct:4.1f}%)")

print("\n✅ Extraction test complete!")
