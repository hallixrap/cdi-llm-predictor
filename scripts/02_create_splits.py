"""
Create train/val/test splits for the CDI prediction model
"""
import pandas as pd
from sklearn.model_selection import train_test_split
import json

# Load data
print("Loading training dataset...")
df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/training_dataset_compact.csv')

print(f"Total examples: {len(df)}")
print(f"Diagnosis distribution:\n{df['diagnosis_categories'].value_counts()}\n")

# Group rare categories (with < 10 examples) into "Other" for stratification
print("Grouping rare categories for stratification...")
category_counts = df['diagnosis_categories'].value_counts()
rare_categories = category_counts[category_counts < 10].index.tolist()
print(f"Rare categories (< 10 examples): {rare_categories}")

# Create a stratification column that groups rare categories
df['stratify_column'] = df['diagnosis_categories'].apply(
    lambda x: 'Other_Combined' if x in rare_categories else x
)

print(f"\nStratification column distribution:")
print(df['stratify_column'].value_counts())

# Create stratified splits
# 80% train, 10% val, 10% test
print("\nCreating stratified splits (80/10/10)...")

# First split: 80% train+val, 20% test
train_val_df, test_df = train_test_split(
    df,
    test_size=0.1,  # 10% for test
    stratify=df['stratify_column'],
    random_state=42
)

# Second split: split train_val into 89% train, 11% val (to get 80/10 split overall)
train_df, val_df = train_test_split(
    train_val_df,
    test_size=0.111,  # ~10% of total
    stratify=train_val_df['stratify_column'],
    random_state=42
)

# Drop the stratify_column as it's no longer needed
train_df = train_df.drop(columns=['stratify_column'])
val_df = val_df.drop(columns=['stratify_column'])
test_df = test_df.drop(columns=['stratify_column'])

print(f"\nSplit sizes:")
print(f"Train: {len(train_df)} ({len(train_df)/len(df)*100:.1f}%)")
print(f"Val: {len(val_df)} ({len(val_df)/len(df)*100:.1f}%)")
print(f"Test: {len(test_df)} ({len(test_df)/len(df)*100:.1f}%)")

# Verify stratification
print(f"\nTrain diagnosis distribution:")
print(train_df['diagnosis_categories'].value_counts().head(10))
print(f"\nVal diagnosis distribution:")
print(val_df['diagnosis_categories'].value_counts().head(10))
print(f"\nTest diagnosis distribution:")
print(test_df['diagnosis_categories'].value_counts().head(10))

# Save splits
print("\nSaving splits...")
train_df.to_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/train.csv', index=False)
val_df.to_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/val.csv', index=False)
test_df.to_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/test.csv', index=False)

# Save split summary
split_summary = {
    'total_examples': len(df),
    'train_size': len(train_df),
    'val_size': len(val_df),
    'test_size': len(test_df),
    'train_pct': float(len(train_df)/len(df)*100),
    'val_pct': float(len(val_df)/len(df)*100),
    'test_pct': float(len(test_df)/len(df)*100),
    'stratification_column': 'diagnosis_categories',
    'random_state': 42
}

with open('/Users/chukanya/Documents/Coding/New_CDI/results/split_summary.json', 'w') as f:
    json.dump(split_summary, f, indent=2)

print("\nSplits saved successfully!")
print("  - train.csv")
print("  - val.csv")
print("  - test.csv")
print("  - split_summary.json")
