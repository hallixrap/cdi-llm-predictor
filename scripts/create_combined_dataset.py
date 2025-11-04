#!/usr/bin/env python3
"""
Create combined training dataset: RCC baseline + CDI queries
This ensures model matches current physician performance AND adds CDI expertise
"""

import pandas as pd
import ast
from typing import List, Set

print("="*80)
print("CREATING COMBINED TRAINING DATASET (RCC + CDI)")
print("="*80)

# Load RCC baseline data (100 notes)
print("\n1. Loading RCC baseline data...")
rcc_df = pd.read_csv('data/processed/processed_rcc_data_LLM.csv', encoding='latin-1')
print(f"   ✓ {len(rcc_df)} discharge summaries with RCC sections")

# Load CDI query data (539 queries)
print("\n2. Loading CDI query data...")
cdi_df = pd.read_csv('data/processed/training_dataset_compact.csv')
print(f"   ✓ {len(cdi_df)} CDI queries")

# Check for overlap between datasets
print("\n3. Checking for overlap between RCC and CDI datasets...")
rcc_patient_ids = set(rcc_df['anon_id'].values)
cdi_patient_ids = set(cdi_df['patient_id'].values)
overlap = rcc_patient_ids.intersection(cdi_patient_ids)
print(f"   • RCC patient IDs: {len(rcc_patient_ids)}")
print(f"   • CDI patient IDs: {len(cdi_patient_ids)}")
print(f"   • Overlap: {len(overlap)} patients")

if len(overlap) > 0:
    print(f"\n   Found {len(overlap)} patients with BOTH RCC and CDI data!")
    print("   These are gold - physicians checked diagnoses BUT CDI still found misses")

# Strategy: Two training approaches
print("\n4. Creating combined dataset...")
print("\n   APPROACH 1: Use both datasets separately")
print("   --------------------------------------------")
print("   • RCC data (100 notes): Train to match physician performance")
print("   • CDI data (539 queries): Train to catch high-value misses")
print("   • Total: 639 training examples")
print("   • Loss function: Weighted by reimbursement impact")

# Prepare RCC data for training
print("\n   Preparing RCC baseline data...")
rcc_training = []
for idx, row in rcc_df.iterrows():
    try:
        diagnoses = ast.literal_eval(row['actual_diagnoses'])
        if isinstance(diagnoses, list) and len(diagnoses) > 0:
            rcc_training.append({
                'patient_id': row['anon_id'],
                'discharge_date': row['jittered_note_date'],
                'discharge_summary': row['deid_note_text'],
                'target_diagnoses': diagnoses,
                'diagnosis_categories': 'RCC_BASELINE',
                'source': 'RCC',
                'weight': 1.0  # Standard weight for baseline performance
            })
    except:
        continue

print(f"   ✓ {len(rcc_training)} RCC examples prepared")

# Prepare CDI data for training
print("\n   Preparing CDI query data...")
cdi_training = []
for idx, row in cdi_df.iterrows():
    cdi_diagnoses = row['cdi_diagnoses'].split('|') if '|' in str(row['cdi_diagnoses']) else [row['cdi_diagnoses']]
    cdi_training.append({
        'patient_id': row['patient_id'],
        'discharge_date': row['discharge_date'],
        'discharge_summary': row['discharge_summary'],
        'target_diagnoses': cdi_diagnoses,
        'diagnosis_categories': row['diagnosis_categories'],
        'source': 'CDI',
        'weight': 2.0  # Higher weight for high-value diagnoses
    })

print(f"   ✓ {len(cdi_training)} CDI examples prepared")

# Combine datasets
print("\n   Combining datasets...")
combined_data = rcc_training + cdi_training
combined_df = pd.DataFrame(combined_data)

print(f"\n5. COMBINED DATASET SUMMARY")
print("-"*80)
print(f"   Total examples: {len(combined_df)}")
print(f"   RCC baseline: {len(rcc_training)} ({len(rcc_training)/len(combined_df)*100:.1f}%)")
print(f"   CDI queries: {len(cdi_training)} ({len(cdi_training)/len(combined_df)*100:.1f}%)")
print(f"\n   Source breakdown:")
print(combined_df['source'].value_counts())

# Save combined dataset
output_file = 'data/processed/combined_rcc_cdi_training.csv'
combined_df.to_csv(output_file, index=False)
print(f"\n✅ Saved combined dataset to: {output_file}")

# Create analysis of diagnosis overlap
print("\n6. ANALYZING DIAGNOSIS OVERLAP")
print("-"*80)

# Get all unique diagnoses from each source
rcc_diagnoses = set()
for diagnoses_list in [r['target_diagnoses'] for r in rcc_training]:
    rcc_diagnoses.update([d.lower().strip() for d in diagnoses_list])

cdi_diagnoses_list = set()
for diagnoses_list in [r['target_diagnoses'] for r in cdi_training]:
    cdi_diagnoses_list.update([d.lower().strip() for d in diagnoses_list])

print(f"   Unique RCC diagnoses: {len(rcc_diagnoses)}")
print(f"   Unique CDI diagnoses: {len(cdi_diagnoses_list)}")
print(f"   Overlap: {len(rcc_diagnoses.intersection(cdi_diagnoses_list))}")
print(f"   RCC-only: {len(rcc_diagnoses - cdi_diagnoses_list)}")
print(f"   CDI-only: {len(cdi_diagnoses_list - rcc_diagnoses)}")

# Sample overlapping diagnoses
overlap_diagnoses = rcc_diagnoses.intersection(cdi_diagnoses_list)
if overlap_diagnoses:
    print(f"\n   Sample overlapping diagnoses (both RCC and CDI):")
    for dx in list(overlap_diagnoses)[:10]:
        print(f"     • {dx}")

print("\n" + "="*80)
print("COMBINED DATASET CREATED SUCCESSFULLY")
print("="*80)
print("\nNext steps:")
print("1. Update LLM prompt to handle both RCC baseline and CDI queries")
print("2. Implement weighted training (CDI higher weight)")
print("3. Evaluate on both metrics:")
print("   - RCC recall: ≥90% (don't regress)")
print("   - CDI recall: ≥70% (add expertise)")
