"""
Train CDI Category Prediction Model (Better Approach)

Instead of predicting exact diagnosis strings (100+ classes),
we predict diagnosis categories (10-15 classes), which is more practical
and aligns with how CDI specialists think.
"""
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.svm import LinearSVC
from sklearn.metrics import classification_report, f1_score, precision_recall_fscore_support, confusion_matrix
import pickle
import json
import seaborn as sns
import matplotlib.pyplot as plt

print("="*80)
print(" CDI DIAGNOSIS CATEGORY PREDICTION - IMPROVED MODEL TRAINING")
print("="*80)

# Load data
print("\nLoading data splits...")
train_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/train.csv')
val_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/val.csv')
test_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/test.csv')

print(f"Train: {len(train_df)} examples")
print(f"Val: {len(val_df)} examples")
print(f"Test: {len(test_df)} examples")

# Use diagnosis_categories as target (more practical)
X_train = train_df['discharge_summary'].fillna('')
X_val = val_df['discharge_summary'].fillna('')
X_test = test_df['discharge_summary'].fillna('')

y_train = train_df['diagnosis_categories'].fillna('Other')
y_val = val_df['diagnosis_categories'].fillna('Other')
y_test = test_df['diagnosis_categories'].fillna('Other')

print(f"\nTarget distribution (train):")
print(y_train.value_counts().head(10))

print("\n" + "="*80)
print("FEATURE EXTRACTION")
print("="*80)

print("\nExtracting TF-IDF features (optimized for medical text)...")
vectorizer = TfidfVectorizer(
    analyzer='word',
    ngram_range=(1, 4),  # Include 4-grams for medical phrases
    max_features=15000,   # More features for better signal
    min_df=3,             # Appear in at least 3 documents
    max_df=0.9,           # Remove very common words
    sublinear_tf=True,    # Log-scaled term frequency
    stop_words='english'  # Remove common English words
)

X_train_tfidf = vectorizer.fit_transform(X_train)
X_val_tfidf = vectorizer.transform(X_val)
X_test_tfidf = vectorizer.transform(X_test)

print(f"\nFeature matrix shape:")
print(f"  Train: {X_train_tfidf.shape}")
print(f"  Val: {X_val_tfidf.shape}")
print(f"  Test: {X_test_tfidf.shape}")

# Save vectorizer
with open('/Users/chukanya/Documents/Coding/New_CDI/models/category_tfidf_vectorizer.pkl', 'wb') as f:
    pickle.dump(vectorizer, f)

# Extract top features per category (for interpretability)
print("\n" + "="*80)
print("TOP PREDICTIVE FEATURES PER CATEGORY")
print("="*80)

# Train a simple model to get feature importances
temp_model = LogisticRegression(max_iter=1000, random_state=42)
temp_model.fit(X_train_tfidf, y_train)

feature_names = vectorizer.get_feature_names_out()
category_features = {}

for idx, category in enumerate(temp_model.classes_):
    if category in ['Sepsis', 'Malnutrition', 'Anemia', 'Respiratory Failure', 'Heart Failure']:
        coef = temp_model.coef_[idx]
        top_indices = np.argsort(coef)[-10:][::-1]
        top_features = [feature_names[i] for i in top_indices]
        category_features[category] = top_features
        print(f"\n{category}:")
        print(f"  Top features: {', '.join(top_features[:5])}")

print("\n" + "="*80)
print("MODEL TRAINING")
print("="*80)

models = {
    'Logistic Regression': LogisticRegression(
        max_iter=2000,
        C=0.5,  # Regularization
        class_weight='balanced',
        solver='liblinear',
        random_state=42
    ),
    'Linear SVM': LinearSVC(
        C=0.1,
        class_weight='balanced',
        max_iter=2000,
        random_state=42
    ),
    'Gradient Boosting': GradientBoostingClassifier(
        n_estimators=200,
        learning_rate=0.1,
        max_depth=5,
        random_state=42
    )
}

results = {}

for model_name, model in models.items():
    print(f"\n{'='*80}")
    print(f"Training: {model_name}")
    print(f"{'='*80}")

    # Train model
    print(f"Fitting {model_name}...")
    model.fit(X_train_tfidf, y_train)

    # Validate
    print("Evaluating on validation set...")
    y_val_pred = model.predict(X_val_tfidf)

    # Calculate metrics
    val_f1_macro = f1_score(y_val, y_val_pred, average='macro', zero_division=0)
    val_f1_weighted = f1_score(y_val, y_val_pred, average='weighted', zero_division=0)
    val_accuracy = np.mean(y_val == y_val_pred)

    # Per-class metrics for priority categories
    priority_cats = ['Sepsis', 'Malnutrition', 'Anemia', 'Respiratory Failure', 'Heart Failure']
    per_class_f1 = {}

    for cat in priority_cats:
        if cat in y_val.values:
            y_val_binary = (y_val == cat).astype(int)
            y_pred_binary = (y_val_pred == cat).astype(int)
            cat_f1 = f1_score(y_val_binary, y_pred_binary, zero_division=0)
            per_class_f1[cat] = cat_f1
        else:
            per_class_f1[cat] = 0.0

    print(f"\nValidation Results:")
    print(f"  Accuracy: {val_accuracy:.4f}")
    print(f"  F1 (Macro): {val_f1_macro:.4f}")
    print(f"  F1 (Weighted): {val_f1_weighted:.4f}")
    print(f"\nPriority Categories F1:")
    for cat in priority_cats:
        print(f"  {cat}: {per_class_f1[cat]:.4f}")

    results[model_name] = {
        'model': model,
        'val_accuracy': float(val_accuracy),
        'val_f1_macro': float(val_f1_macro),
        'val_f1_weighted': float(val_f1_weighted),
        'per_class_f1': per_class_f1
    }

    # Save model
    model_path = f'/Users/chukanya/Documents/Coding/New_CDI/models/category_{model_name.lower().replace(" ", "_")}.pkl'
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)

# Select best model
print("\n" + "="*80)
print("MODEL SELECTION")
print("="*80)

best_model_name = max(results, key=lambda x: results[x]['val_f1_weighted'])
best_model = results[best_model_name]['model']

print(f"\nBest model: {best_model_name}")
print(f"  Val F1 (Weighted): {results[best_model_name]['val_f1_weighted']:.4f}")
print(f"  Val Accuracy: {results[best_model_name]['val_accuracy']:.4f}")

print("\n" + "="*80)
print("TEST SET EVALUATION")
print("="*80)

# Evaluate on test set
print(f"\nEvaluating {best_model_name} on test set...")
y_test_pred = best_model.predict(X_test_tfidf)

# Metrics
test_f1_macro = f1_score(y_test, y_test_pred, average='macro', zero_division=0)
test_f1_weighted = f1_score(y_test, y_test_pred, average='weighted', zero_division=0)
test_accuracy = np.mean(y_test == y_test_pred)

print(f"\nTest Set Results:")
print(f"  Accuracy: {test_accuracy:.4f}")
print(f"  F1 (Macro): {test_f1_macro:.4f}")
print(f"  F1 (Weighted): {test_f1_weighted:.4f}")

print(f"\nDetailed Classification Report:")
print(classification_report(y_test, y_test_pred, zero_division=0))

# Priority category analysis
print("\n" + "="*80)
print("PRIORITY DIAGNOSIS PERFORMANCE")
print("="*80)

priority_categories = ['Sepsis', 'Malnutrition', 'Anemia', 'Respiratory Failure', 'Heart Failure']

for cat in priority_categories:
    y_test_binary = (y_test == cat).astype(int)
    y_pred_binary = (y_test_pred == cat).astype(int)

    if y_test_binary.sum() > 0:  # If category exists in test set
        precision = precision_recall_fscore_support(y_test_binary, y_pred_binary, average='binary', zero_division=0)[0]
        recall = precision_recall_fscore_support(y_test_binary, y_pred_binary, average='binary', zero_division=0)[1]
        f1 = f1_score(y_test_binary, y_pred_binary, zero_division=0)

        print(f"\n{cat}:")
        print(f"  Test examples: {y_test_binary.sum()}")
        print(f"  Precision: {precision:.4f}")
        print(f"  Recall: {recall:.4f}")
        print(f"  F1: {f1:.4f}")

# Confusion matrix for top categories
print("\n" + "="*80)
print("CONFUSION MATRIX (Top Categories)")
print("="*80)

top_categories = y_test.value_counts().head(8).index.tolist()
mask = y_test.isin(top_categories)
y_test_top = y_test[mask]
y_pred_top = pd.Series(y_test_pred)[mask]

cm = confusion_matrix(y_test_top, y_pred_top, labels=top_categories)
print(f"\nCategories: {top_categories}")
print(cm)

# Save confusion matrix plot
plt.figure(figsize=(12, 10))
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', xticklabels=top_categories, yticklabels=top_categories)
plt.xlabel('Predicted')
plt.ylabel('Actual')
plt.title(f'Confusion Matrix - {best_model_name}')
plt.tight_layout()
plt.savefig('/Users/chukanya/Documents/Coding/New_CDI/results/confusion_matrix.png', dpi=150)
print("\nConfusion matrix saved to: results/confusion_matrix.png")

# Save results
print("\n" + "="*80)
print("SAVING RESULTS")
print("="*80)

# Priority category F1 scores
priority_f1_scores = {}
for cat in priority_categories:
    y_test_binary = (y_test == cat).astype(int)
    y_pred_binary = (y_test_pred == cat).astype(int)
    if y_test_binary.sum() > 0:
        priority_f1_scores[cat] = float(f1_score(y_test_binary, y_pred_binary, zero_division=0))
    else:
        priority_f1_scores[cat] = 0.0

evaluation_results = {
    'model_type': 'Category Prediction',
    'best_model': best_model_name,
    'num_categories': len(y_train.unique()),
    'validation_results': {
        model_name: {
            'accuracy': results[model_name]['val_accuracy'],
            'f1_macro': results[model_name]['val_f1_macro'],
            'f1_weighted': results[model_name]['val_f1_weighted']
        }
        for model_name in results
    },
    'test_results': {
        'accuracy': float(test_accuracy),
        'f1_macro': float(test_f1_macro),
        'f1_weighted': float(test_f1_weighted)
    },
    'priority_categories_f1': priority_f1_scores,
    'v3_baseline_f1': 0.2875,
    'improvement_over_v3': float(test_f1_weighted - 0.2875),
    'exceeds_40pct_target': bool(test_f1_weighted > 0.40)
}

with open('/Users/chukanya/Documents/Coding/New_CDI/results/category_model_evaluation.json', 'w') as f:
    json.dump(evaluation_results, f, indent=2)

# Save predictions
test_results_df = test_df.copy()
test_results_df['predicted_category'] = y_test_pred
test_results_df['correct'] = test_results_df['diagnosis_categories'] == test_results_df['predicted_category']

test_results_df[['patient_id', 'discharge_date', 'diagnosis_categories', 'predicted_category', 'cdi_diagnoses', 'correct']].to_csv(
    '/Users/chukanya/Documents/Coding/New_CDI/results/category_test_predictions.csv',
    index=False
)

print("\nResults saved:")
print("  - results/category_model_evaluation.json")
print("  - results/category_test_predictions.csv")
print("  - results/confusion_matrix.png")

# Final summary
print("\n" + "="*80)
print("PERFORMANCE SUMMARY")
print("="*80)

print(f"\nApproach: Predict diagnosis CATEGORIES (not exact strings)")
print(f"Number of categories: {len(y_train.unique())}")
print(f"\nTest Performance:")
print(f"  Accuracy: {test_accuracy*100:.2f}%")
print(f"  F1 (Weighted): {test_f1_weighted*100:.2f}%")
print(f"  F1 (Macro): {test_f1_macro*100:.2f}%")

print(f"\nComparison with v3:")
print(f"  v3 (RCC checkboxes): F1 = 28.75%")
print(f"  v5 (CDI categories):  F1 = {test_f1_weighted*100:.2f}%")
print(f"  Improvement: {(test_f1_weighted - 0.2875)*100:+.2f}%")

if test_f1_weighted > 0.40:
    print(f"\n  SUCCESS: Model exceeds 40% F1 target!")
    print(f"  Target: 40.00%, Achieved: {test_f1_weighted*100:.2f}%")
else:
    print(f"\n  Progress towards 40% target:")
    print(f"  Current: {test_f1_weighted*100:.2f}% / Target: 40.00%")

print("\n" + "="*80)
print("TRAINING COMPLETE")
print("="*80)
