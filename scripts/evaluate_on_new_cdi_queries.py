#!/usr/bin/env python3
"""
Evaluate enhanced CDI predictor on 160 NEW CDI queries not in training data
This validates whether the LLM would have caught the diagnoses that CDI specialists queried
"""

import pandas as pd
import json
import re
import sys
import os
from typing import List, Dict, Set
from cdi_llm_predictor import predict_missed_diagnoses
from collections import Counter

def extract_cdi_diagnosis_from_query(query_text: str) -> List[str]:
    """
    Extract the diagnosis that CDI queried about from the query text.

    Patterns to look for:
    - [X] Diagnosis Name
    - [x] Diagnosis Name
    - Checkboxes with diagnosis
    """
    # Handle missing/NaN values
    if pd.isna(query_text) or not isinstance(query_text, str):
        return []

    diagnoses = []

    # Pattern 1: [X] or [x] followed by diagnosis text (more flexible)
    # Matches: [X] Diagnosis or [x] Diagnosis or [ X ] Diagnosis
    pattern1 = r'\[\s*[Xx]\s*\]\s*([^\n\[\]]+)'
    matches1 = re.findall(pattern1, query_text)
    diagnoses.extend([m.strip() for m in matches1 if m.strip()])

    # Pattern 2: Look after "clinically valid" phrase
    if 'clinically valid' in query_text.lower():
        # Get text after this phrase
        parts = re.split(r'clinically valid\.?', query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            after_phrase = parts[1]
            # Look for [X] patterns
            pattern2 = r'\[\s*[Xx]\s*\]\s*([^\n\[\]]+)'
            matches2 = re.findall(pattern2, after_phrase)
            diagnoses.extend([m.strip() for m in matches2 if m.strip()])

    # Pattern 3: Look after "indicated below" phrase
    if 'indicated below' in query_text.lower():
        parts = re.split(r'indicated below\.?', query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            after_phrase = parts[1]
            # Look for diagnosis text - might not have [X]
            # Try to extract meaningful diagnosis lines
            lines = after_phrase.split('\n')
            for line in lines[:10]:  # Check first 10 lines
                line = line.strip()
                # Skip common non-diagnosis lines
                if any(skip in line.lower() for skip in ['this documentation', 'provider response', 'medical record', '[]', 'ruled out']):
                    continue
                # Look for [X] patterns
                if '[x]' in line.lower() or '[ x ]' in line.lower():
                    match = re.search(r'\[\s*[Xx]\s*\]\s*(.+)', line)
                    if match:
                        diagnoses.append(match.group(1).strip())

    # Clean up diagnoses
    cleaned = []
    seen = set()
    for dx in diagnoses:
        # Remove common suffixes
        dx = re.sub(r'\s*This documentation.*$', '', dx, flags=re.IGNORECASE)
        dx = re.sub(r'\s*\[\s*\]\s*.*$', '', dx)  # Remove unchecked options
        dx = re.sub(r'\s*\(Provider response.*$', '', dx, flags=re.IGNORECASE)
        dx = dx.strip()

        # Skip if too short or already seen (case-insensitive)
        if dx and len(dx) > 3:
            dx_lower = dx.lower()
            if dx_lower not in seen:
                seen.add(dx_lower)
                cleaned.append(dx)

    return cleaned


def normalize_diagnosis(dx: str) -> str:
    """Normalize diagnosis string for matching"""
    dx = dx.lower().strip()
    # Remove common variations
    dx = re.sub(r'\s+', ' ', dx)  # Normalize whitespace
    dx = re.sub(r'[^\w\s]', '', dx)  # Remove punctuation
    return dx


def diagnoses_match(pred_dx: str, true_dx: str, threshold: float = 0.6) -> bool:
    """
    Check if predicted diagnosis matches true diagnosis.
    Uses fuzzy matching + clinical equivalents since exact strings rarely match.
    """
    pred_norm = normalize_diagnosis(pred_dx)
    true_norm = normalize_diagnosis(true_dx)

    # Exact match
    if pred_norm == true_norm:
        return True

    # Substring match (either direction)
    if pred_norm in true_norm or true_norm in pred_norm:
        return True

    # Clinical equivalents - recognize related diagnoses
    clinical_equivalents = {
        'pressure ulcer': ['decubitus ulcer', 'pressure injury', 'pressure sore'],
        'malnutrition': ['protein calorie malnutrition', 'hypoalbuminemia', 'cachexia'],
        'sepsis': ['severe sepsis', 'septic shock'],
        'thrombocytopenia': ['pancytopenia'],  # Pancytopenia includes thrombocytopenia
        'anemia': ['blood loss anemia', 'acute blood loss anemia', 'iron deficiency anemia'],
        'respiratory failure': ['hypoxic respiratory failure', 'acute respiratory failure'],
        'heart failure': ['chf', 'congestive heart failure', 'systolic heart failure', 'diastolic heart failure'],
        'acute kidney injury': ['aki', 'acute renal failure'],
        'hyperglycemia': ['diabetes with hyperglycemia', 'steroid induced hyperglycemia'],
        'hypoglycemia': ['diabetes with hypoglycemia'],
    }

    # Check if diagnoses are clinically equivalent
    for base_term, equivalent_terms in clinical_equivalents.items():
        # Check if both diagnoses relate to the same clinical concept
        pred_has_base = base_term in pred_norm or any(term in pred_norm for term in equivalent_terms)
        true_has_base = base_term in true_norm or any(term in true_norm for term in equivalent_terms)

        if pred_has_base and true_has_base:
            return True

    # Key word overlap
    pred_words = set(pred_norm.split())
    true_words = set(true_norm.split())

    # Remove common stop words
    stop_words = {'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to', 'of', 'in', 'on',
                  'confirmed', 'ruled', 'out', 'poa', 'present', 'admission', 'acute', 'chronic'}
    pred_words = pred_words - stop_words
    true_words = true_words - stop_words

    if not pred_words or not true_words:
        return False

    # Calculate overlap
    overlap = len(pred_words.intersection(true_words))
    max_len = max(len(pred_words), len(true_words))

    if max_len == 0:
        return False

    overlap_ratio = overlap / max_len
    return overlap_ratio >= threshold


def evaluate_single_case(discharge_summary: str, true_diagnoses: List[str],
                         api_key: str, case_id: str) -> Dict:
    """Evaluate LLM predictor on a single case"""

    print(f"\n{'='*80}")
    print(f"Evaluating Case: {case_id}")
    print(f"{'='*80}")
    print(f"CDI queried about: {', '.join(true_diagnoses)}")

    try:
        # Call LLM predictor
        result = predict_missed_diagnoses(discharge_summary, api_key, model="gpt-4.1")

        # Extract predicted diagnoses
        missed = result.get('missed_diagnoses', [])
        pred_diagnoses = [dx.get('diagnosis', '') for dx in missed]

        print(f"\nLLM predicted {len(pred_diagnoses)} diagnoses:")
        for i, dx in enumerate(pred_diagnoses[:10], 1):  # Show first 10
            print(f"  {i}. {dx}")
        if len(pred_diagnoses) > 10:
            print(f"  ... and {len(pred_diagnoses) - 10} more")

        # Check matches
        true_positives = []
        matched_true = set()

        for pred_dx in pred_diagnoses:
            for i, true_dx in enumerate(true_diagnoses):
                if i not in matched_true and diagnoses_match(pred_dx, true_dx):
                    true_positives.append({
                        'predicted': pred_dx,
                        'actual': true_dx
                    })
                    matched_true.add(i)
                    break

        false_negatives = [true_diagnoses[i] for i in range(len(true_diagnoses)) if i not in matched_true]

        recall = len(true_positives) / len(true_diagnoses) if true_diagnoses else 0

        print(f"\n✅ Matched: {len(true_positives)}/{len(true_diagnoses)} ({recall*100:.1f}% recall)")
        for tp in true_positives:
            print(f"  ✓ '{tp['actual']}' ~ '{tp['predicted']}'")

        if false_negatives:
            print(f"\n❌ Missed: {len(false_negatives)}")
            for fn in false_negatives:
                print(f"  ✗ {fn}")

        return {
            'case_id': case_id,
            'true_diagnoses': true_diagnoses,
            'predicted_diagnoses': pred_diagnoses,
            'true_positives': len(true_positives),
            'false_negatives': len(false_negatives),
            'recall': recall,
            'matches': true_positives,
            'success': recall > 0
        }

    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        return {
            'case_id': case_id,
            'error': str(e),
            'success': False
        }


def main():
    print("="*80)
    print("EVALUATING ENHANCED CDI PREDICTOR ON NEW TEST SET")
    print("="*80)

    # Get API key
    api_key = None
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        api_key = os.environ.get('STANFORD_API_KEY')

    if not api_key:
        print("\n❌ No API key provided!")
        print("\nUsage:")
        print("  python3 scripts/evaluate_on_new_cdi_queries.py YOUR_API_KEY")
        print("  OR: export STANFORD_API_KEY='your_key'")
        return 1

    # Load datasets
    print("\n1. Loading datasets...")
    old_df = pd.read_csv('data/raw/cdi_linked_clinical_discharge_fixed.csv')
    new_df = pd.read_csv('data/processed/cdi_queries_clean.csv')  # Use clean version

    # Find new cases
    old_ids = set(old_df['anon_id'].values)
    new_df['is_new'] = new_df['anon_id'].apply(lambda x: x not in old_ids)
    test_df = new_df[new_df['is_new']].copy()

    print(f"   ✓ Training set: {len(old_df)} CDI queries")
    print(f"   ✓ New dataset: {len(new_df)} CDI queries")
    print(f"   ✓ NEW test cases: {len(test_df)} queries not in training!")

    # Extract CDI diagnoses from queries
    print("\n2. Extracting CDI diagnoses from query text...")

    # Check for missing query text
    missing_queries = test_df['cdi_query'].isna().sum()
    if missing_queries > 0:
        print(f"   ⚠️  {missing_queries} cases have missing CDI query text (will be skipped)")

    test_df['cdi_diagnoses'] = test_df['cdi_query'].apply(extract_cdi_diagnosis_from_query)

    # Filter to cases with extracted diagnoses
    before_filter = len(test_df)
    test_df = test_df[test_df['cdi_diagnoses'].apply(len) > 0].copy()
    filtered_out = before_filter - len(test_df)

    print(f"   ✓ {len(test_df)} cases with identifiable CDI diagnoses")
    if filtered_out > 0:
        print(f"   ⚠️  {filtered_out} cases filtered out (no extractable diagnosis from query)")

    # Sample diagnosis distribution
    all_cdi_diagnoses = []
    for diagnoses_list in test_df['cdi_diagnoses']:
        all_cdi_diagnoses.extend(diagnoses_list)

    print(f"\n3. CDI Diagnosis Distribution (Test Set):")
    diagnosis_counts = Counter(all_cdi_diagnoses)
    for dx, count in diagnosis_counts.most_common(15):
        print(f"   {dx}: {count}x")

    # Get number to test from command line or default to 10
    print(f"\n4. Ready to test on {len(test_df)} cases")

    # Check if num_test provided as second argument
    if len(sys.argv) >= 3:
        num_test_arg = sys.argv[2]
        print(f"   Command line argument: '{num_test_arg}'")
        if num_test_arg.lower() == 'all':
            num_test = len(test_df)
            print(f"   Recognized 'all' - will test all {num_test} cases")
        else:
            try:
                num_test = int(num_test_arg)
                print(f"   Parsed as number: {num_test}")
            except:
                num_test = 10
                print(f"   Could not parse - defaulting to 10")
    else:
        num_test = 10
        print(f"   No argument provided - defaulting to 10")

    num_test = min(num_test, len(test_df))
    print(f"   Final: Testing on {num_test} cases")

    print(f"\n{'='*80}")
    print(f"TESTING ON {num_test} NEW CDI QUERIES")
    print(f"{'='*80}")

    # Evaluate
    results = []
    for idx in range(num_test):
        row = test_df.iloc[idx]
        case_id = row['anon_id']
        discharge_summary = row['discharge_summary']
        true_diagnoses = row['cdi_diagnoses']

        result = evaluate_single_case(
            discharge_summary=discharge_summary,
            true_diagnoses=true_diagnoses,
            api_key=api_key,
            case_id=case_id
        )
        results.append(result)

        # Progress
        print(f"\nProgress: {idx+1}/{num_test} cases evaluated")
        print("-"*80)

    # Summary statistics
    print(f"\n{'='*80}")
    print("EVALUATION SUMMARY")
    print(f"{'='*80}")

    successful = [r for r in results if r.get('success', False)]
    total_tp = sum(r.get('true_positives', 0) for r in results)
    total_fn = sum(r.get('false_negatives', 0) for r in results)
    total_true = total_tp + total_fn

    overall_recall = total_tp / total_true if total_true > 0 else 0

    print(f"\nCases evaluated: {len(results)}")
    print(f"Cases with matches: {len(successful)} ({len(successful)/len(results)*100:.1f}%)")
    print(f"\n📊 OVERALL PERFORMANCE:")
    print(f"  True Positives: {total_tp}")
    print(f"  False Negatives: {total_fn}")
    print(f"  Total CDI diagnoses: {total_true}")
    print(f"  Overall Recall: {overall_recall*100:.1f}%")

    if overall_recall >= 0.7:
        print(f"\n✅ EXCELLENT! Recall ≥70% - Meeting CDI expert target!")
    elif overall_recall >= 0.5:
        print(f"\n⚠️  GOOD! Recall ≥50% - Room for improvement")
    else:
        print(f"\n❌ NEEDS WORK: Recall <50% - Prompt tuning needed")

    # Save detailed results
    results_df = pd.DataFrame(results)
    results_df.to_csv('results/new_cdi_queries_evaluation.csv', index=False)
    print(f"\n✅ Detailed results saved to: results/new_cdi_queries_evaluation.csv")

    # Show example matches
    print(f"\n{'='*80}")
    print("EXAMPLE MATCHES (First 3 successful cases)")
    print(f"{'='*80}")
    for i, r in enumerate([r for r in results if r.get('success')][:3], 1):
        print(f"\nCase {i}: {r['case_id']}")
        for match in r.get('matches', []):
            print(f"  ✓ CDI: '{match['actual']}'")
            print(f"    LLM: '{match['predicted']}'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
