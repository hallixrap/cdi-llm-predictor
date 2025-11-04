#!/usr/bin/env python3
"""
Evaluate LLM-based CDI prediction vs baseline classifier

Compares:
1. GPT-4.1/GPT-5 LLM predictions (using Stanford API)
2. Gradient Boosting baseline classifier
3. Actual CDI specialist queries (gold standard)
"""

import pandas as pd
import json
from cdi_llm_predictor import predict_missed_diagnoses
from typing import List, Dict
import pickle

def load_baseline_model():
    """Load the trained baseline classifier"""
    with open('/Users/chukanya/Documents/Coding/New_CDI/models/category_gradient_boosting.pkl', 'rb') as f:
        model = pickle.load(f)

    with open('/Users/chukanya/Documents/Coding/New_CDI/models/category_tfidf_vectorizer.pkl', 'rb') as f:
        vectorizer = pickle.load(f)

    return model, vectorizer


def extract_predicted_categories(llm_result: Dict) -> List[str]:
    """Extract diagnosis categories from LLM result"""
    categories = []
    missed = llm_result.get('missed_diagnoses', [])

    for dx in missed:
        cat = dx.get('category', '')
        if cat:
            categories.append(cat)

    return categories


def calculate_match_score(predicted_categories: List[str], actual_category: str) -> Dict:
    """
    Calculate how well predictions match actual CDI query

    Returns:
        - exact_match: True if exact category match
        - category_found: True if category is in predictions
        - num_predictions: Total number of predictions
    """
    return {
        'exact_match': actual_category in predicted_categories if predicted_categories else False,
        'category_found': any(actual_category.lower() in cat.lower() or cat.lower() in actual_category.lower()
                             for cat in predicted_categories) if predicted_categories else False,
        'num_predictions': len(predicted_categories),
        'predicted_categories': predicted_categories
    }


def evaluate_llm_approach(api_key: str, model: str = "gpt-4.1", num_samples: int = 10):
    """
    Evaluate LLM approach on test set

    Args:
        api_key: Stanford API key
        model: LLM model to use
        num_samples: Number of test samples to evaluate (limited due to API costs)
    """
    print(f"\n{'='*80}")
    print(f"EVALUATING LLM APPROACH: {model}")
    print(f"{'='*80}\n")

    # Load test data
    test_df = pd.read_csv('/Users/chukanya/Documents/Coding/New_CDI/data/processed/test.csv')
    print(f"Test set size: {len(test_df)}")
    print(f"Evaluating on: {num_samples} samples (to manage API costs)\n")

    # Sample from different categories to ensure representation
    priority_cats = ['Sepsis', 'Malnutrition', 'Anemia', 'Respiratory Failure', 'Heart Failure']

    # Get samples from priority categories first
    priority_samples = pd.DataFrame()
    for cat in priority_cats:
        cat_samples = test_df[test_df['diagnosis_categories'] == cat].head(2)
        priority_samples = pd.concat([priority_samples, cat_samples])

    # Fill remaining with "Other"
    remaining = num_samples - len(priority_samples)
    if remaining > 0:
        other_samples = test_df[test_df['diagnosis_categories'] == 'Other'].head(remaining)
        samples = pd.concat([priority_samples, other_samples])
    else:
        samples = priority_samples.head(num_samples)

    print(f"Sample distribution:")
    print(samples['diagnosis_categories'].value_counts())
    print()

    # Load baseline model for comparison
    baseline_model, vectorizer = load_baseline_model()

    results = []

    for idx, row in samples.iterrows():
        print(f"\nProcessing sample {len(results)+1}/{len(samples)}...")
        print(f"  Patient: {row['patient_id']}")
        print(f"  Actual CDI Category: {row['diagnosis_categories']}")

        try:
            # LLM prediction
            llm_result = predict_missed_diagnoses(row['discharge_summary'], api_key, model)
            llm_categories = extract_predicted_categories(llm_result)

            # Baseline prediction
            X_tfidf = vectorizer.transform([row['discharge_summary']])
            baseline_pred = baseline_model.predict(X_tfidf)[0]

            # Evaluation
            llm_match = calculate_match_score(llm_categories, row['diagnosis_categories'])
            baseline_match = (baseline_pred == row['diagnosis_categories'])

            print(f"  LLM Predictions: {llm_categories}")
            print(f"  LLM Match: {llm_match['category_found']}")
            print(f"  Baseline Prediction: {baseline_pred}")
            print(f"  Baseline Match: {baseline_match}")

            results.append({
                'patient_id': row['patient_id'],
                'actual_category': row['diagnosis_categories'],
                'actual_diagnosis': row['cdi_diagnoses'],
                'llm_predictions': llm_categories,
                'llm_match': llm_match['category_found'],
                'llm_exact_match': llm_match['exact_match'],
                'baseline_prediction': baseline_pred,
                'baseline_match': baseline_match,
                'num_llm_predictions': llm_match['num_predictions']
            })

        except Exception as e:
            print(f"  Error: {str(e)}")
            results.append({
                'patient_id': row['patient_id'],
                'actual_category': row['diagnosis_categories'],
                'error': str(e)
            })

    # Calculate metrics
    print(f"\n{'='*80}")
    print("EVALUATION RESULTS")
    print(f"{'='*80}\n")

    valid_results = [r for r in results if 'error' not in r]

    if valid_results:
        # LLM metrics
        llm_matches = sum(1 for r in valid_results if r['llm_match'])
        llm_exact_matches = sum(1 for r in valid_results if r['llm_exact_match'])
        llm_accuracy = llm_matches / len(valid_results)
        llm_exact_accuracy = llm_exact_matches / len(valid_results)

        # Baseline metrics
        baseline_matches = sum(1 for r in valid_results if r['baseline_match'])
        baseline_accuracy = baseline_matches / len(valid_results)

        print(f"LLM Approach ({model}):")
        print(f"  Category Match Accuracy: {llm_accuracy:.2%} ({llm_matches}/{len(valid_results)})")
        print(f"  Exact Match Accuracy: {llm_exact_accuracy:.2%} ({llm_exact_matches}/{len(valid_results)})")
        print(f"  Avg predictions per case: {sum(r['num_llm_predictions'] for r in valid_results) / len(valid_results):.1f}")
        print()

        print(f"Baseline Classifier (Gradient Boosting):")
        print(f"  Accuracy: {baseline_accuracy:.2%} ({baseline_matches}/{len(valid_results)})")
        print()

        print(f"Comparison:")
        if llm_accuracy > baseline_accuracy:
            improvement = (llm_accuracy - baseline_accuracy) * 100
            print(f"  ✅ LLM outperforms baseline by {improvement:+.1f} percentage points")
        elif llm_accuracy < baseline_accuracy:
            difference = (baseline_accuracy - llm_accuracy) * 100
            print(f"  ⚠️  Baseline outperforms LLM by {difference:.1f} percentage points")
        else:
            print(f"  ➡️  LLM and baseline perform equally")

        # Save results
        results_df = pd.DataFrame(valid_results)
        results_df.to_csv('/Users/chukanya/Documents/Coding/New_CDI/results/llm_evaluation_results.csv', index=False)

        summary = {
            'model': model,
            'num_samples': len(valid_results),
            'llm_category_match_accuracy': float(llm_accuracy),
            'llm_exact_match_accuracy': float(llm_exact_accuracy),
            'baseline_accuracy': float(baseline_accuracy),
            'llm_vs_baseline_improvement': float(llm_accuracy - baseline_accuracy),
            'avg_predictions_per_case': float(sum(r['num_llm_predictions'] for r in valid_results) / len(valid_results))
        }

        with open('/Users/chukanya/Documents/Coding/New_CDI/results/llm_evaluation_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)

        print(f"\nResults saved:")
        print(f"  - results/llm_evaluation_results.csv")
        print(f"  - results/llm_evaluation_summary.json")

    else:
        print("No valid results to evaluate (all samples had errors)")

    print(f"\n{'='*80}\n")

    return results


def main():
    """Run evaluation"""
    api_key = input("Enter Stanford API key: ").strip()

    print("\nSelect model:")
    print("1. gpt-4.1 (recommended)")
    print("2. gpt-5-nano")
    model_choice = input("Choice (1-2, default 1): ").strip() or "1"

    models = {"1": "gpt-4.1", "2": "gpt-5-nano"}
    model = models.get(model_choice, "gpt-4.1")

    num_samples = input("\nNumber of test samples to evaluate (default 10): ").strip()
    num_samples = int(num_samples) if num_samples else 10

    evaluate_llm_approach(api_key, model, num_samples)


if __name__ == "__main__":
    main()
