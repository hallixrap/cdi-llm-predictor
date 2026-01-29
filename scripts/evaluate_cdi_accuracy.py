#!/usr/bin/env python3
"""
CDI Query Evaluation Script - Paper-Ready Analysis

Evaluates how well the LLM reproduces CDI specialist queries.
Reference Standard: Actual CDI queries from SmarterDx (known positives)

Metrics:
- Recall: % of CDI queries reproduced by model
- Discovery rate: Cases where model finds MORE than CDI queried
- Precision (manual sample): Are discoveries correct?

Usage:
    python scripts/evaluate_cdi_accuracy.py

    # With custom data path
    python scripts/evaluate_cdi_accuracy.py --data data/training_dataset_parsed.csv

    # Test mode (first 10 cases)
    python scripts/evaluate_cdi_accuracy.py --test
"""

import pandas as pd
import json
import re
import sys
import os
import argparse
from datetime import datetime
from typing import List, Dict, Tuple, Set
from collections import Counter
from cdi_llm_predictor import predict_missed_diagnoses

# Diagnosis categories for analysis
DIAGNOSIS_CATEGORIES = {
    'electrolytes': ['hyponatremia', 'hypernatremia', 'hypokalemia', 'hyperkalemia',
                     'hypocalcemia', 'hypercalcemia', 'hypomagnesemia', 'hypophosphatemia',
                     'hyperphosphatemia'],
    'anemia': ['anemia', 'blood loss', 'iron deficiency', 'hemoglobin'],
    'malnutrition': ['malnutrition', 'protein calorie', 'underweight', 'cachexia', 'bmi'],
    'sepsis': ['sepsis', 'septic'],
    'respiratory': ['respiratory failure', 'hypoxic', 'hypoxia', 'pulmonary edema'],
    'cardiac': ['heart failure', 'chf', 'myocardial infarction', 'mi', 'demand ischemia'],
    'renal': ['acute kidney injury', 'aki', 'renal failure'],
    'pressure_ulcer': ['pressure ulcer', 'decubitus', 'pressure injury'],
    'coagulation': ['thrombocytopenia', 'pancytopenia', 'coagulopathy'],
    'metabolic': ['lactic acidosis', 'hyperglycemia', 'hypoglycemia', 'encephalopathy'],
    'hypoalbuminemia': ['hypoalbuminemia', 'albumin'],
    'other': []  # Catch-all
}


def categorize_diagnosis(dx: str) -> str:
    """Assign a diagnosis to a category"""
    dx_lower = dx.lower()
    for category, keywords in DIAGNOSIS_CATEGORIES.items():
        if any(kw in dx_lower for kw in keywords):
            return category
    return 'other'


def extract_cdi_diagnosis_from_query(query_text: str) -> List[str]:
    """
    Extract the diagnosis that CDI queried about from the query text.
    Handles multiple query formats from SmarterDx.
    """
    if pd.isna(query_text) or not isinstance(query_text, str):
        return []

    diagnoses = []

    # Pattern 1: [X] or [x] followed by diagnosis text
    pattern1 = r'\[\s*[Xx]\s*\]\s*([^\n\[\]]+)'
    matches1 = re.findall(pattern1, query_text)
    diagnoses.extend([m.strip() for m in matches1 if m.strip()])

    # Pattern 2: After "clinically valid" phrase
    if 'clinically valid' in query_text.lower():
        parts = re.split(r'clinically valid\.?', query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            pattern2 = r'\[\s*[Xx]\s*\]\s*([^\n\[\]]+)'
            matches2 = re.findall(pattern2, parts[1])
            diagnoses.extend([m.strip() for m in matches2 if m.strip()])

    # Pattern 3: After "indicated below" phrase
    if 'indicated below' in query_text.lower():
        parts = re.split(r'indicated below\.?', query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            lines = parts[1].split('\n')
            for line in lines[:10]:
                line = line.strip()
                if any(skip in line.lower() for skip in ['this documentation', 'provider response', 'medical record', '[]', 'ruled out']):
                    continue
                if '[x]' in line.lower() or '[ x ]' in line.lower():
                    match = re.search(r'\[\s*[Xx]\s*\]\s*(.+)', line)
                    if match:
                        diagnoses.append(match.group(1).strip())

    # Clean up
    cleaned = []
    seen = set()
    for dx in diagnoses:
        dx = re.sub(r'\s*This documentation.*$', '', dx, flags=re.IGNORECASE)
        dx = re.sub(r'\s*\[\s*\]\s*.*$', '', dx)
        dx = re.sub(r'\s*\(Provider response.*$', '', dx, flags=re.IGNORECASE)
        dx = dx.strip()

        if dx and len(dx) > 3:
            dx_lower = dx.lower()
            if dx_lower not in seen:
                seen.add(dx_lower)
                cleaned.append(dx)

    return cleaned


def normalize_diagnosis(dx: str) -> str:
    """Normalize diagnosis string for matching"""
    dx = dx.lower().strip()
    dx = re.sub(r'\s+', ' ', dx)
    dx = re.sub(r'[^\w\s]', '', dx)
    return dx


def diagnoses_match(pred_dx: str, true_dx: str, threshold: float = 0.5) -> bool:
    """
    Check if predicted diagnosis matches true diagnosis.
    Uses fuzzy matching + clinical equivalents.
    """
    pred_norm = normalize_diagnosis(pred_dx)
    true_norm = normalize_diagnosis(true_dx)

    # Exact match
    if pred_norm == true_norm:
        return True

    # Substring match
    if pred_norm in true_norm or true_norm in pred_norm:
        return True

    # Clinical equivalents - expanded for better matching
    clinical_equivalents = {
        'pressure ulcer': ['decubitus ulcer', 'pressure injury', 'pressure sore', 'bed sore', 'stage 2', 'stage 3', 'stage 4'],
        'malnutrition': ['protein calorie malnutrition', 'hypoalbuminemia', 'cachexia', 'underweight', 'severe malnutrition', 'moderate malnutrition', 'mild malnutrition'],
        'sepsis': ['severe sepsis', 'septic shock', 'urosepsis', 'septicemia'],
        'thrombocytopenia': ['pancytopenia', 'low platelets'],
        'anemia': ['blood loss anemia', 'acute blood loss anemia', 'iron deficiency anemia', 'chronic anemia', 'normocytic anemia'],
        'respiratory failure': ['hypoxic respiratory failure', 'acute respiratory failure', 'hypoxia', 'hypercapnic', 'hypercapnia'],
        'heart failure': ['chf', 'congestive heart failure', 'systolic heart failure', 'diastolic heart failure',
                          'acute on chronic heart failure', 'hfref', 'hfpef', 'pulmonary edema'],
        'acute kidney injury': ['aki', 'acute renal failure', 'acute renal insufficiency', 'ckd', 'kidney disease'],
        'hyperglycemia': ['diabetes with hyperglycemia', 'steroid induced hyperglycemia', 'uncontrolled diabetes', 'diabetes mellitus'],
        'hypoglycemia': ['diabetes with hypoglycemia'],
        'hyponatremia': ['hypovolemic hyponatremia', 'euvolemic hyponatremia', 'hypervolemic hyponatremia', 'low sodium'],
        'encephalopathy': ['metabolic encephalopathy', 'hepatic encephalopathy', 'toxic encephalopathy', 'delirium', 'altered mental status'],
        'lactic acidosis': ['elevated lactate', 'hyperlactatemia'],
        'obesity': ['morbid obesity', 'severe obesity', 'class ii obesity', 'class iii obesity', 'class 2 obesity', 'class 3 obesity', 'bmi 35', 'bmi 40', 'bmi 45'],
        'hematoma': ['groin hematoma', 'postoperative hematoma', 'retroperitoneal hematoma'],
        'debridement': ['excisional debridement', 'surgical debridement', 'wound debridement'],
        'quadriplegia': ['functional quadriplegia', 'tetraplegia', 'paralysis'],
        'pulmonary edema': ['acute pulmonary edema', 'cardiogenic pulmonary edema', 'non cardiogenic pulmonary edema', 'flash pulmonary edema'],
    }

    for base_term, equivalent_terms in clinical_equivalents.items():
        pred_has_base = base_term in pred_norm or any(term in pred_norm for term in equivalent_terms)
        true_has_base = base_term in true_norm or any(term in true_norm for term in equivalent_terms)
        if pred_has_base and true_has_base:
            return True

    # Key word overlap
    pred_words = set(pred_norm.split())
    true_words = set(true_norm.split())

    stop_words = {'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to', 'of', 'in', 'on',
                  'confirmed', 'ruled', 'out', 'poa', 'present', 'admission', 'acute', 'chronic'}
    pred_words = pred_words - stop_words
    true_words = true_words - stop_words

    if not pred_words or not true_words:
        return False

    overlap = len(pred_words.intersection(true_words))
    max_len = max(len(pred_words), len(true_words))

    if max_len == 0:
        return False

    return overlap / max_len >= threshold


def evaluate_single_case(discharge_summary: str, true_diagnoses: List[str],
                         api_key: str, case_id: str, model: str = "gpt-4.1",
                         verbose: bool = False, use_llm_judge: bool = False,
                         llm_matcher=None) -> Dict:
    """
    Evaluate LLM predictor on a single case.

    Args:
        discharge_summary: The discharge summary text
        true_diagnoses: List of true CDI diagnoses
        api_key: Stanford API key
        case_id: Case identifier
        model: LLM model to use for prediction
        verbose: Print debug info
        use_llm_judge: Use LLM-based semantic matching (slower but more accurate)
        llm_matcher: Pre-initialized HybridMatcher instance (to share cache)
    """

    if verbose:
        print(f"\nEvaluating Case: {case_id}")
        print(f"CDI queried about: {', '.join(true_diagnoses)}")

    try:
        # Call LLM predictor
        result = predict_missed_diagnoses(discharge_summary, api_key, model=model)

        # Extract predicted diagnoses
        missed = result.get('missed_diagnoses', [])
        pred_diagnoses = [dx.get('diagnosis', '') for dx in missed]
        pred_categories = [dx.get('category', '') for dx in missed]

        if verbose:
            print(f"LLM predicted {len(pred_diagnoses)} diagnoses")

        # Check matches - which CDI queries did we reproduce?
        true_positives = []
        matched_true_idx = set()
        matched_pred_idx = set()

        for pred_idx, pred_dx in enumerate(pred_diagnoses):
            for true_idx, true_dx in enumerate(true_diagnoses):
                if true_idx not in matched_true_idx:
                    # Use LLM judge or rule-based matching
                    if use_llm_judge and llm_matcher:
                        is_match, confidence = llm_matcher.match(pred_dx, true_dx, verbose=verbose)
                    else:
                        is_match = diagnoses_match(pred_dx, true_dx)
                        confidence = 0.8 if is_match else 0.2

                    if is_match:
                        true_positives.append({
                            'predicted': pred_dx,
                            'actual': true_dx,
                            'pred_category': pred_categories[pred_idx] if pred_idx < len(pred_categories) else '',
                            'match_confidence': confidence
                        })
                        matched_true_idx.add(true_idx)
                        matched_pred_idx.add(pred_idx)
                        break

        # False negatives = CDI queries we didn't catch
        false_negatives = [true_diagnoses[i] for i in range(len(true_diagnoses)) if i not in matched_true_idx]

        # Extra discoveries = things we found that CDI didn't query (not false positives - may be correct!)
        extra_discoveries = [pred_diagnoses[i] for i in range(len(pred_diagnoses)) if i not in matched_pred_idx]

        recall = len(true_positives) / len(true_diagnoses) if true_diagnoses else 0

        return {
            'case_id': case_id,
            'num_cdi_queries': len(true_diagnoses),
            'num_llm_predictions': len(pred_diagnoses),
            'true_positives': len(true_positives),
            'false_negatives': len(false_negatives),
            'extra_discoveries': len(extra_discoveries),
            'recall': recall,
            'cdi_diagnoses': true_diagnoses,
            'llm_predictions': pred_diagnoses,
            'matches': true_positives,
            'missed': false_negatives,
            'discoveries': extra_discoveries,
            'success': True,
            'used_llm_judge': use_llm_judge
        }

    except Exception as e:
        if verbose:
            print(f"ERROR: {str(e)}")
        return {
            'case_id': case_id,
            'error': str(e),
            'success': False
        }


def run_evaluation(df: pd.DataFrame, api_key: str, model: str = "gpt-4.1",
                   limit: int = None, verbose: bool = False,
                   use_llm_judge: bool = False, judge_model: str = "gpt-5-nano") -> Tuple[List[Dict], Dict]:
    """
    Run full evaluation on dataset.

    Args:
        df: DataFrame with discharge summaries and CDI diagnoses
        api_key: Stanford API key
        model: LLM model to use for predictions
        limit: Optional limit on number of cases
        verbose: Print debug info
        use_llm_judge: Use LLM-as-judge for semantic matching
        judge_model: Model to use for LLM judge
    """

    print(f"\n{'='*80}")
    print(f"CDI QUERY REPRODUCTION EVALUATION")
    print(f"Model: {model}")
    if use_llm_judge:
        print(f"LLM Judge: {judge_model}")
    print(f"Dataset size: {len(df)} cases")
    print(f"{'='*80}")

    # Initialize LLM matcher if using LLM judge
    llm_matcher = None
    if use_llm_judge:
        try:
            from llm_judge import HybridMatcher
            llm_matcher = HybridMatcher(api_key, llm_model=judge_model)
            print("LLM-as-Judge enabled for semantic matching")
        except ImportError:
            print("Warning: Could not import llm_judge module, falling back to rule-based matching")
            use_llm_judge = False

    if limit:
        df = df.head(limit)
        print(f"(Limited to {limit} cases for testing)")

    results = []

    # Checkpoint file for resuming if interrupted
    checkpoint_file = None
    if limit is None:  # Only checkpoint for full runs
        import os
        checkpoint_file = '/tmp/cdi_evaluation_checkpoint.json'
        if os.path.exists(checkpoint_file):
            try:
                with open(checkpoint_file, 'r') as f:
                    checkpoint_data = json.load(f)
                    results = checkpoint_data.get('results', [])
                    start_idx = checkpoint_data.get('last_index', 0) + 1
                    print(f"\n📌 Resuming from checkpoint: starting at index {start_idx}")
                    df = df.iloc[start_idx:]
            except:
                print("⚠️  Checkpoint file corrupt, starting fresh")

    for idx, row in df.iterrows():
        # Support multiple ID column names
        case_id = row.get('patient_id', row.get('anon_id', f'case_{idx}'))
        discharge_summary = row['discharge_summary']

        # Extract CDI diagnoses - prefer the cleaned/parsed version
        true_diagnoses = []
        parsed_column_exists = False

        # First, check for cleaned cdi_diagnoses_parsed column (best quality)
        # If this column exists and was parsed, do NOT fall back to cdi_diagnoses
        # An empty list means "no confirmed diagnoses" - intentional skip
        if 'cdi_diagnoses_parsed' in row:
            parsed_val = row['cdi_diagnoses_parsed']
            if isinstance(parsed_val, list):
                true_diagnoses = parsed_val
                parsed_column_exists = True
            elif isinstance(parsed_val, str) and parsed_val:
                if parsed_val.startswith('['):
                    try:
                        true_diagnoses = eval(parsed_val)
                        parsed_column_exists = True  # Successfully parsed (even if empty)
                    except:
                        pass

        # Only fallback to cdi_diagnoses if parsed column doesn't exist
        if not parsed_column_exists and not true_diagnoses and 'cdi_diagnoses' in row:
            cdi_val = row['cdi_diagnoses']
            if isinstance(cdi_val, list):
                true_diagnoses = cdi_val
            elif isinstance(cdi_val, str) and cdi_val:
                if cdi_val.startswith('['):
                    try:
                        true_diagnoses = eval(cdi_val)
                    except:
                        true_diagnoses = [cdi_val]
                else:
                    # Don't split on comma - diagnoses often contain commas
                    true_diagnoses = [cdi_val]

        # Last resort: parse from raw query
        if not true_diagnoses:
            query_text = row.get('cdi_query_raw', row.get('query_text', ''))
            if query_text:
                true_diagnoses = extract_cdi_diagnosis_from_query(query_text)

        # Skip if no confirmed diagnoses (includes unchecked-only queries)
        if not true_diagnoses:
            continue

        print(f"Processing {idx+1}/{len(df)}: {case_id} ({len(true_diagnoses)} CDI queries)")

        result = evaluate_single_case(
            discharge_summary=discharge_summary,
            true_diagnoses=true_diagnoses,
            api_key=api_key,
            case_id=case_id,
            model=model,
            verbose=verbose,
            use_llm_judge=use_llm_judge,
            llm_matcher=llm_matcher
        )
        results.append(result)

        # Save checkpoint every 10 cases for full runs
        if checkpoint_file and len(results) % 10 == 0:
            try:
                with open(checkpoint_file, 'w') as f:
                    json.dump({'results': results, 'last_index': idx}, f)
            except:
                pass  # Don't fail if checkpoint save fails

    # Calculate aggregate metrics
    successful = [r for r in results if r.get('success', False)]

    total_cdi_queries = sum(r.get('num_cdi_queries', 0) for r in successful)
    total_tp = sum(r.get('true_positives', 0) for r in successful)
    total_fn = sum(r.get('false_negatives', 0) for r in successful)
    total_discoveries = sum(r.get('extra_discoveries', 0) for r in successful)

    overall_recall = total_tp / total_cdi_queries if total_cdi_queries > 0 else 0

    # Per-case recall distribution
    recalls = [r.get('recall', 0) for r in successful]
    mean_recall = sum(recalls) / len(recalls) if recalls else 0

    # Category breakdown
    category_stats = Counter()
    category_matched = Counter()

    for r in successful:
        for dx in r.get('cdi_diagnoses', []):
            cat = categorize_diagnosis(dx)
            category_stats[cat] += 1
        for match in r.get('matches', []):
            cat = categorize_diagnosis(match['actual'])
            category_matched[cat] += 1

    # Get LLM judge stats if used
    llm_judge_stats = None
    if use_llm_judge and llm_matcher:
        llm_judge_stats = llm_matcher.get_stats()
        print(f"\nLLM Judge Stats:")
        print(f"  Rule-based matches: {llm_judge_stats['rule_matches']}")
        print(f"  Rule-based non-matches: {llm_judge_stats['rule_non_matches']}")
        print(f"  LLM judge calls: {llm_judge_stats['llm_calls']}")
        print(f"  Cache hits: {llm_judge_stats['cache_hits']}")
        print(f"  LLM call rate: {llm_judge_stats['llm_call_rate']*100:.1f}%")

    summary = {
        'total_cases': len(df),
        'evaluated_cases': len(successful),
        'failed_cases': len(results) - len(successful),
        'total_cdi_queries': total_cdi_queries,
        'total_true_positives': total_tp,
        'total_false_negatives': total_fn,
        'total_discoveries': total_discoveries,
        'overall_recall': overall_recall,
        'mean_per_case_recall': mean_recall,
        'category_stats': dict(category_stats),
        'category_matched': dict(category_matched),
        'model': model,
        'use_llm_judge': use_llm_judge,
        'judge_model': judge_model if use_llm_judge else None,
        'llm_judge_stats': llm_judge_stats,
        'timestamp': datetime.now().isoformat()
    }

    return results, summary


def print_summary(summary: Dict, results: List[Dict]):
    """Print evaluation summary"""

    print(f"\n{'='*80}")
    print("EVALUATION RESULTS SUMMARY")
    print(f"{'='*80}")

    print(f"\nConfiguration:")
    print(f"  Model: {summary.get('model', 'unknown')}")
    if summary.get('use_llm_judge'):
        print(f"  LLM Judge: {summary.get('judge_model')} (semantic matching)")
    else:
        print(f"  Matching: Rule-based")

    print(f"\nDataset:")
    print(f"  Total cases: {summary['total_cases']}")
    print(f"  Successfully evaluated: {summary['evaluated_cases']}")
    print(f"  Failed: {summary['failed_cases']}")

    print(f"\n📊 REPRODUCTION METRICS (CDI Query Match):")
    print(f"  Total CDI queries in dataset: {summary['total_cdi_queries']}")
    print(f"  Queries reproduced by model: {summary['total_true_positives']}")
    print(f"  Queries missed by model: {summary['total_false_negatives']}")
    print(f"  Extra discoveries by model: {summary['total_discoveries']}")

    print(f"\n  OVERALL RECALL: {summary['overall_recall']*100:.1f}%")
    print(f"  Mean per-case recall: {summary['mean_per_case_recall']*100:.1f}%")

    # Category breakdown
    print(f"\n📈 PERFORMANCE BY CATEGORY:")
    for cat in sorted(summary['category_stats'].keys()):
        total = summary['category_stats'][cat]
        matched = summary['category_matched'].get(cat, 0)
        cat_recall = matched / total if total > 0 else 0
        print(f"  {cat}: {matched}/{total} ({cat_recall*100:.1f}%)")

    # Interpretation
    print(f"\n{'='*80}")
    print("INTERPRETATION")
    print(f"{'='*80}")

    recall = summary['overall_recall']
    if recall >= 0.7:
        print("✅ EXCELLENT: Model reproduces ≥70% of CDI queries")
        print("   Strong evidence that model captures CDI specialist patterns")
    elif recall >= 0.5:
        print("⚠️  GOOD: Model reproduces 50-70% of CDI queries")
        print("   Model captures majority of patterns but has gaps")
    elif recall >= 0.3:
        print("⚠️  MODERATE: Model reproduces 30-50% of CDI queries")
        print("   Model needs improvement to match CDI specialist performance")
    else:
        print("❌ NEEDS WORK: Model reproduces <30% of CDI queries")
        print("   Significant prompt/model tuning required")

    # Discoveries interpretation
    disc_rate = summary['total_discoveries'] / summary['evaluated_cases'] if summary['evaluated_cases'] > 0 else 0
    print(f"\n📌 DISCOVERY RATE: {disc_rate:.1f} extra predictions per case")
    print("   These need manual review to determine if they are:")
    print("   - True discoveries (CDI missed them)")
    print("   - False positives (model hallucinated)")


def save_results(results: List[Dict], summary: Dict, output_dir: str = "results"):
    """Save evaluation results"""

    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save detailed results
    results_df = pd.DataFrame(results)
    results_path = os.path.join(output_dir, f"cdi_evaluation_results_{timestamp}.csv")
    results_df.to_csv(results_path, index=False)

    # Save summary
    summary_path = os.path.join(output_dir, f"cdi_evaluation_summary_{timestamp}.json")
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    # Save discoveries for manual review
    discoveries_data = []
    for r in results:
        if r.get('success') and r.get('discoveries'):
            for disc in r['discoveries']:
                discoveries_data.append({
                    'case_id': r['case_id'],
                    'discovery': disc,
                    'cdi_queried': r.get('cdi_diagnoses', []),
                    'manual_review': '',  # To be filled in
                    'is_correct': ''  # To be filled in
                })

    if discoveries_data:
        disc_df = pd.DataFrame(discoveries_data)
        disc_path = os.path.join(output_dir, f"discoveries_for_review_{timestamp}.csv")
        disc_df.to_csv(disc_path, index=False)
        print(f"\n✅ Discoveries for manual review saved to: {disc_path}")

    print(f"✅ Results saved to: {results_path}")
    print(f"✅ Summary saved to: {summary_path}")

    # Clean up checkpoint file after successful completion
    if checkpoint_file and os.path.exists(checkpoint_file):
        try:
            os.remove(checkpoint_file)
        except:
            pass

    return results_path, summary_path


def main():
    parser = argparse.ArgumentParser(description='Evaluate CDI LLM predictor accuracy')
    parser.add_argument('--data', type=str, default='SQL queries/training_dataset_parsed_fixed.csv',
                        help='Path to evaluation dataset')
    parser.add_argument('--model', type=str, default='gpt-4.1',
                        choices=['gpt-4.1', 'gpt-5', 'gpt-5-nano', 'gpt-4.1-mini', 'claude-opus-4', 'claude-sonnet-4'],
                        help='LLM model to use')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limit number of cases to evaluate')
    parser.add_argument('--test', action='store_true',
                        help='Test mode - evaluate first 10 cases only')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output')
    parser.add_argument('--output', type=str, default='results',
                        help='Output directory')
    parser.add_argument('--llm-judge', action='store_true',
                        help='Use LLM-as-judge for semantic matching (slower but more accurate)')
    parser.add_argument('--judge-model', type=str, default='gpt-5-nano',
                        choices=['gpt-5-nano', 'gpt-4.1-mini'],
                        help='Model to use for LLM judge (default: gpt-5-nano)')

    args = parser.parse_args()

    # Get API key
    api_key = os.environ.get('STANFORD_API_KEY')
    if not api_key:
        print("❌ STANFORD_API_KEY environment variable not set!")
        print("   Run: export STANFORD_API_KEY='your_key'")
        return 1

    # Load data
    print(f"\nLoading data from: {args.data}")
    if not os.path.exists(args.data):
        # Try alternate paths
        alt_paths = [
            'SQL queries/training_dataset_parsed_fixed.csv',
            'SQL queries/training_dataset_parsed.csv',
            'SQL queries/training_dataset_compact.csv',
            'SQL queries/cdi_linked_clinical_discharge_fixed.csv'
        ]
        for alt_path in alt_paths:
            if os.path.exists(alt_path):
                args.data = alt_path
                print(f"  Found alternate: {alt_path}")
                break
        else:
            print(f"❌ Data file not found!")
            print(f"   Expected: {args.data}")
            print(f"   Place your CDI evaluation data in the data/ directory")
            return 1

    df = pd.read_csv(args.data)
    print(f"Loaded {len(df)} records")

    # Check required columns
    required_cols = ['discharge_summary']
    for col in required_cols:
        if col not in df.columns:
            print(f"❌ Missing required column: {col}")
            return 1

    # Set limit
    limit = args.limit
    if args.test:
        limit = 10
        print("\n🧪 TEST MODE: Evaluating first 10 cases only")

    # Run evaluation
    results, summary = run_evaluation(
        df=df,
        api_key=api_key,
        model=args.model,
        limit=limit,
        verbose=args.verbose,
        use_llm_judge=args.llm_judge,
        judge_model=args.judge_model
    )

    # Print summary
    print_summary(summary, results)

    # Save results
    save_results(results, summary, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
