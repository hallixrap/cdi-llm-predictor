"""
Train baseline models for CDI diagnosis prediction

This script implements multiple baseline models and compares their performance.
We use TF-IDF features with various classifiers as a strong baseline.
"""
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.naive_bayes import MultinomialNB
from sklearn.metrics import classification_report, f1_score, precision_recall_fscore_support
from sklearn.preprocessing import MultiLabelBinarizer
import pickle
import json
from collections import Counter

print("="*60)
print("CDI DIAGNOSIS PREDICTION - BASELINE MODEL TRAINING")
print("="*60)

# Load data
print("\nLoading data splits...")
train_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/train.csv')
val_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/val.csv')
test_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/test.csv')

print(f"Train: {len(train_df)} examples")
print(f"Val: {len(val_df)} examples")
print(f"Test: {len(test_df)} examples")

# Prepare features (discharge summaries) and targets (cdi_diagnoses)
X_train = train_df['discharge_summary'].fillna('')
X_val = val_df['discharge_summary'].fillna('')
X_test = test_df['discharge_summary'].fillna('')

y_train = train_df['cdi_diagnoses'].fillna('')
y_val = val_df['cdi_diagnoses'].fillna('')
y_test = test_df['cdi_diagnoses'].fillna('')

print("\n" + "="*60)
print("FEATURE EXTRACTION")
print("="*60)

# Create TF-IDF features
# Medical text typically benefits from:
# - Character n-grams (captures medical abbreviations)
# - Word n-grams (captures medical phrases)
# - Moderate max_features to avoid overfitting on small dataset

print("\nExtracting TF-IDF features...")
print("Configuration:")
print("  - analyzer: word")
print("  - ngram_range: (1, 3)  # unigrams, bigrams, trigrams")
print("  - max_features: 10000")
print("  - min_df: 2  # must appear in at least 2 documents")
print("  - max_df: 0.95  # remove very common terms")

vectorizer = TfidfVectorizer(
    analyzer='word',
    ngram_range=(1, 3),  # Capture phrases like "protein calorie malnutrition"
    max_features=10000,   # Limit features to avoid overfitting
    min_df=2,             # Minimum document frequency
    max_df=0.95,          # Maximum document frequency (remove very common words)
    sublinear_tf=True     # Use log-scaled term frequency
)

X_train_tfidf = vectorizer.fit_transform(X_train)
X_val_tfidf = vectorizer.transform(X_val)
X_test_tfidf = vectorizer.transform(X_test)

print(f"\nFeature matrix shape:")
print(f"  Train: {X_train_tfidf.shape}")
print(f"  Val: {X_val_tfidf.shape}")
print(f"  Test: {X_test_tfidf.shape}")

# Save vectorizer
with open('/Users/chukanya/Documents/Coding/New_CDI/models/tfidf_vectorizer.pkl', 'wb') as f:
    pickle.dump(vectorizer, f)
print("\nVectorizer saved to: models/tfidf_vectorizer.pkl")

print("\n" + "="*60)
print("MODEL TRAINING")
print("="*60)

models = {
    'Logistic Regression': LogisticRegression(
        max_iter=1000,
        C=1.0,
        class_weight='balanced',
        random_state=42
    ),
    'Random Forest': RandomForestClassifier(
        n_estimators=100,
        max_depth=20,
        min_samples_split=5,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    ),
    'Multinomial Naive Bayes': MultinomialNB(alpha=0.1)
}

results = {}

for model_name, model in models.items():
    print(f"\n{'='*60}")
    print(f"Training: {model_name}")
    print(f"{'='*60}")

    # Train model
    print(f"Fitting {model_name}...")
    model.fit(X_train_tfidf, y_train)

    # Validate
    print("Evaluating on validation set...")
    y_val_pred = model.predict(X_val_tfidf)

    # Calculate metrics
    val_f1_macro = f1_score(y_val, y_val_pred, average='macro', zero_division=0)
    val_f1_weighted = f1_score(y_val, y_val_pred, average='weighted', zero_division=0)

    # Exact match accuracy
    exact_match = np.mean(y_val == y_val_pred)

    print(f"\nValidation Results:")
    print(f"  Exact Match Accuracy: {exact_match:.4f}")
    print(f"  F1 (Macro): {val_f1_macro:.4f}")
    print(f"  F1 (Weighted): {val_f1_weighted:.4f}")

    results[model_name] = {
        'model': model,
        'val_exact_match': float(exact_match),
        'val_f1_macro': float(val_f1_macro),
        'val_f1_weighted': float(val_f1_weighted)
    }

    # Save model
    model_path = f'/Users/chukanya/Documents/Coding/New_CDI/models/{model_name.lower().replace(" ", "_")}.pkl'
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)
    print(f"Model saved to: {model_path}")

# Select best model based on weighted F1
print("\n" + "="*60)
print("MODEL SELECTION")
print("="*60)

best_model_name = max(results, key=lambda x: results[x]['val_f1_weighted'])
best_model = results[best_model_name]['model']

print(f"\nBest model (by val F1 weighted): {best_model_name}")
print(f"  Val F1 (Weighted): {results[best_model_name]['val_f1_weighted']:.4f}")
print(f"  Val Exact Match: {results[best_model_name]['val_exact_match']:.4f}")

print("\n" + "="*60)
print("TEST SET EVALUATION")
print("="*60)

# Evaluate on test set
print(f"\nEvaluating best model ({best_model_name}) on test set...")
y_test_pred = best_model.predict(X_test_tfidf)

# Calculate metrics
test_f1_macro = f1_score(y_test, y_test_pred, average='macro', zero_division=0)
test_f1_weighted = f1_score(y_test, y_test_pred, average='weighted', zero_division=0)
test_exact_match = np.mean(y_test == y_test_pred)

print(f"\nTest Set Results:")
print(f"  Exact Match Accuracy: {test_exact_match:.4f}")
print(f"  F1 (Macro): {test_f1_macro:.4f}")
print(f"  F1 (Weighted): {test_f1_weighted:.4f}")

# Detailed classification report
print(f"\nDetailed Classification Report:")
print(classification_report(y_test, y_test_pred, zero_division=0))

# Category-level analysis for priority diagnoses
print("\n" + "="*60)
print("PRIORITY DIAGNOSIS ANALYSIS")
print("="*60)

test_df_copy = test_df.copy()
test_df_copy['predicted_diagnosis'] = y_test_pred
test_df_copy['correct'] = test_df_copy['cdi_diagnoses'] == test_df_copy['predicted_diagnosis']

priority_categories = ['Sepsis', 'Malnutrition', 'Anemia', 'Respiratory Failure', 'Heart Failure']

for category in priority_categories:
    category_df = test_df_copy[test_df_copy['diagnosis_categories'] == category]
    if len(category_df) > 0:
        accuracy = category_df['correct'].mean()
        print(f"\n{category}:")
        print(f"  Test examples: {len(category_df)}")
        print(f"  Accuracy: {accuracy:.4f}")
    else:
        print(f"\n{category}: No examples in test set")

# Save evaluation results
print("\n" + "="*60)
print("SAVING RESULTS")
print("="*60)

evaluation_results = {
    'best_model': best_model_name,
    'validation_results': {
        model_name: {
            'exact_match': results[model_name]['val_exact_match'],
            'f1_macro': results[model_name]['val_f1_macro'],
            'f1_weighted': results[model_name]['val_f1_weighted']
        }
        for model_name in results
    },
    'test_results': {
        'exact_match': float(test_exact_match),
        'f1_macro': float(test_f1_macro),
        'f1_weighted': float(test_f1_weighted)
    },
    'priority_diagnosis_accuracy': {
        category: float(test_df_copy[test_df_copy['diagnosis_categories'] == category]['correct'].mean())
        if len(test_df_copy[test_df_copy['diagnosis_categories'] == category]) > 0 else 0.0
        for category in priority_categories
    },
    'v3_baseline_f1': 0.2875,
    'improvement_over_v3': float(test_f1_weighted - 0.2875)
}

with open('/Users/chukanya/Documents/Coding/New_CDI/results/baseline_evaluation.json', 'w') as f:
    json.dump(evaluation_results, f, indent=2)

# Save predictions
test_df_copy[['patient_id', 'discharge_date', 'cdi_diagnoses', 'predicted_diagnosis', 'diagnosis_categories', 'correct']].to_csv(
    '/Users/chukanya/Documents/Coding/New_CDI/results/test_predictions.csv',
    index=False
)

print("\nResults saved:")
print("  - results/baseline_evaluation.json")
print("  - results/test_predictions.csv")

print("\n" + "="*60)
print("COMPARISON WITH V3 BASELINE")
print("="*60)

print(f"\nv3 model (RCC checkboxes): F1 = 28.75%")
print(f"v5 model (CDI queries):    F1 = {test_f1_weighted*100:.2f}%")
print(f"Improvement:               {(test_f1_weighted - 0.2875)*100:+.2f}%")

if test_f1_weighted > 0.40:
    print("\n SUCCESS: Model exceeds 40% F1 target!")
else:
    print(f"\n Target F1: 40%, Current: {test_f1_weighted*100:.2f}%")

print("\n" + "="*60)
print("TRAINING COMPLETE")
print("="*60)
