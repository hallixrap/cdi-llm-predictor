#!/usr/bin/env python3
"""
Analyze RCC baseline vs CDI query data to understand the gap
"""

import pandas as pd
import json
from collections import Counter
import ast

print("="*80)
print("RCC BASELINE vs CDI QUERY ANALYSIS")
print("="*80)

# Load RCC baseline data (100 notes with what physicians checked)
print("\n1. Loading RCC baseline data...")
rcc_df = pd.read_csv('data/processed/processed_rcc_data_LLM.csv', encoding='latin-1')
print(f"   ✓ Loaded {len(rcc_df)} discharge summaries with RCC sections")
print(f"   ✓ Total diagnoses checked by physicians: {rcc_df['num_actual_diagnoses'].sum()}")
print(f"   ✓ Average diagnoses per note: {rcc_df['num_actual_diagnoses'].mean():.1f}")

# Extract all RCC diagnoses
print("\n2. Extracting RCC diagnosis patterns...")
all_rcc_diagnoses = []
for idx, row in rcc_df.iterrows():
    try:
        diagnoses = ast.literal_eval(row['actual_diagnoses'])
        if isinstance(diagnoses, list):
            all_rcc_diagnoses.extend(diagnoses)
    except:
        continue

rcc_diagnosis_counts = Counter(all_rcc_diagnoses)
print(f"   ✓ Total RCC diagnoses: {len(all_rcc_diagnoses)}")
print(f"   ✓ Unique RCC diagnoses: {len(rcc_diagnosis_counts)}")

print("\n3. TOP 20 RCC DIAGNOSES (What physicians commonly check)")
print("-"*80)
for i, (dx, count) in enumerate(rcc_diagnosis_counts.most_common(20), 1):
    pct = (count / len(rcc_df)) * 100
    print(f"{i:2d}. {dx:<65s} ({count:2d}x, {pct:4.1f}%)")

# Load CDI query data (539 examples of what specialists found missing)
print("\n\n4. Loading CDI query data...")
cdi_df = pd.read_csv('data/processed/training_dataset_compact.csv')
print(f"   ✓ Loaded {len(cdi_df)} CDI queries")

print("\n5. CDI QUERY CATEGORIES (What CDI specialists commonly query about)")
print("-"*80)
cdi_categories = cdi_df['diagnosis_categories'].value_counts()
for i, (cat, count) in enumerate(cdi_categories.head(15).items(), 1):
    pct = (count / len(cdi_df)) * 100
    print(f"{i:2d}. {cat:<40s} ({count:3d}x, {pct:5.1f}%)")

# Key insights
print("\n\n6. KEY INSIGHTS")
print("="*80)

print("\n📊 RCC BASELINE (What physicians check):")
print(f"   • 100 discharge summaries")
print(f"   • 640 total diagnoses (6.4 per note)")
print(f"   • {len(rcc_diagnosis_counts)} unique diagnosis strings")
print(f"   • Covers common/routine diagnoses")

print("\n🎯 CDI QUERIES (What specialists find missing):")
print(f"   • 539 CDI queries")
print(f"   • Top categories: Sepsis (15.6%), Malnutrition (8.7%), Anemia (7.6%)")
print(f"   • High-value diagnoses (Major CC/MCC)")
print(f"   • What physicians MISSED despite .rcc")

print("\n💡 THE GAP:")
print("   RCC captures: Common, routine diagnoses physicians remember")
print("   CDI captures: High-value diagnoses physicians FORGET or miss")
print("   ")
print("   Combined model must:")
print("   ✓ Match RCC performance (≥90% recall on RCC diagnoses)")
print("   ✓ Add CDI expertise (≥70% recall on CDI queries)")
print("   ✓ Never regress below current physician performance")

# Sample comparison
print("\n\n7. EXAMPLE COMPARISON")
print("="*80)
print("\nRCC Top 5 (Common, physicians remember):")
for i, (dx, count) in enumerate(rcc_diagnosis_counts.most_common(5), 1):
    print(f"   {i}. {dx}")

print("\nCDI Top 5 Categories (High-value, physicians forget):")
for i, (cat, count) in enumerate(cdi_categories.head(5).items(), 1):
    print(f"   {i}. {cat} ({count} queries)")

print("\n" + "="*80)
print("ANALYSIS COMPLETE")
print("="*80)
print("\nNext step: Create combined training dataset (RCC + CDI)")
