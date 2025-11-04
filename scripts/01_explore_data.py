"""
Explore the CDI training dataset to understand its structure and quality
"""
import pandas as pd
import numpy as np
from collections import Counter
import json

# Load the compact training dataset
print("Loading training dataset...")
df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/training_dataset_compact.csv')

print(f"\n{'='*60}")
print("DATASET OVERVIEW")
print(f"{'='*60}")
print(f"Total examples: {len(df)}")
print(f"Columns: {list(df.columns)}")
print(f"\nFirst few rows:")
print(df.head(2))

print(f"\n{'='*60}")
print("DATA QUALITY CHECKS")
print(f"{'='*60}")
print(f"Missing values:")
print(df.isnull().sum())

print(f"\n{'='*60}")
print("DIAGNOSIS DISTRIBUTION")
print(f"{'='*60}")
print(df['diagnosis_categories'].value_counts().head(15))

# Check multi-diagnosis cases
print(f"\n{'='*60}")
print("MULTI-DIAGNOSIS ANALYSIS")
print(f"{'='*60}")
multi_diagnosis = df['cdi_diagnoses'].str.contains('|', regex=False, na=False)
print(f"Single diagnosis: {(~multi_diagnosis).sum()} ({(~multi_diagnosis).sum()/len(df)*100:.1f}%)")
print(f"Multi diagnosis: {multi_diagnosis.sum()} ({multi_diagnosis.sum()/len(df)*100:.1f}%)")

print(f"\n{'='*60}")
print("QUERY TIMING STATISTICS")
print(f"{'='*60}")
print(f"Mean: {df['days_after_discharge'].mean():.2f} days")
print(f"Median: {df['days_after_discharge'].median():.0f} days")
print(f"Min: {df['days_after_discharge'].min():.0f} days")
print(f"Max: {df['days_after_discharge'].max():.0f} days")

print(f"\n{'='*60}")
print("DISCHARGE SUMMARY LENGTHS")
print(f"{'='*60}")
df['summary_length'] = df['discharge_summary'].str.len()
df['summary_word_count'] = df['discharge_summary'].str.split().str.len()
print(f"Average characters: {df['summary_length'].mean():.0f}")
print(f"Average words: {df['summary_word_count'].mean():.0f}")
print(f"Min words: {df['summary_word_count'].min():.0f}")
print(f"Max words: {df['summary_word_count'].max():.0f}")

print(f"\n{'='*60}")
print("EXAMPLE RECORD")
print(f"{'='*60}")
# Show a sepsis example (highest priority)
sepsis_example = df[df['diagnosis_categories'] == 'Sepsis'].iloc[0]
print(f"Patient ID: {sepsis_example['patient_id']}")
print(f"Discharge Date: {sepsis_example['discharge_date']}")
print(f"CDI Diagnosis: {sepsis_example['cdi_diagnoses']}")
print(f"Category: {sepsis_example['diagnosis_categories']}")
print(f"Days After Discharge: {sepsis_example['days_after_discharge']}")
print(f"\nDischarge Summary Preview (first 500 chars):")
print(sepsis_example['discharge_summary'][:500])
print("...")

# Save summary statistics
summary_stats = {
    'total_examples': len(df),
    'diagnosis_distribution': df['diagnosis_categories'].value_counts().to_dict(),
    'single_diagnosis_pct': float((~multi_diagnosis).sum()/len(df)*100),
    'avg_query_days': float(df['days_after_discharge'].mean()),
    'avg_summary_words': float(df['summary_word_count'].mean()),
}

with open('/Users/chukanya/Documents/Coding/New_CDI/results/exploration_summary.json', 'w') as f:
    json.dump(summary_stats, f, indent=2)

print(f"\n{'='*60}")
print("Summary statistics saved to results/exploration_summary.json")
print(f"{'='*60}")
