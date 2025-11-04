"""
CDI Gap Analysis
Analyze all 642 CDI queries to identify high-value, high-frequency diagnoses
that are missing from the current cdi_llm_predictor.py prompt
"""

import pandas as pd
import re
from collections import Counter, defaultdict

def extract_all_cdi_diagnoses(query_text: str) -> list:
    """
    Extract all diagnoses that CDI queried about from the query text.
    Enhanced to capture ALL CDI query patterns seen in the data.
    """
    if pd.isna(query_text) or not isinstance(query_text, str):
        return []

    diagnoses = []

    # Pattern 1: [X] or [x] followed by diagnosis text (most common)
    pattern1 = r'\[\s*[Xx]\s*\]\s*:?\s*([^\n\[\]]+)'
    matches1 = re.findall(pattern1, query_text)
    for m in matches1:
        # Clean up the match
        diagnosis = m.strip()
        # Remove trailing "This documentation..." text
        diagnosis = re.sub(r'\s*This documentation.*$', '', diagnosis, flags=re.IGNORECASE)
        # Remove "(Provider response..." text
        diagnosis = re.sub(r'\s*\(Provider response.*$', '', diagnosis, flags=re.IGNORECASE)
        if diagnosis and len(diagnosis) > 3:
            diagnoses.append(diagnosis)

    # Pattern 2: After "clinically valid" without checkboxes
    # Example: "...clinically valid.  Protein calorie malnutrition, Severe"
    if 'clinically valid' in query_text.lower():
        parts = re.split(r'clinically valid\.?\s+', query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            after_phrase = parts[1]
            # Take the first sentence/clause after "clinically valid"
            # Stop at common delimiters
            first_part = re.split(r'(?:This documentation|Provider response|\[\])', after_phrase, flags=re.IGNORECASE)[0]
            first_part = first_part.strip()

            # If it's not empty and doesn't contain checkboxes, it's likely a diagnosis
            if first_part and len(first_part) > 3 and '[' not in first_part:
                # Clean up
                diagnosis = re.sub(r'\s*,?\s*$', '', first_part)
                # Remove trailing punctuation
                diagnosis = re.sub(r'\s+$', '', diagnosis)
                if len(diagnosis) > 3:
                    diagnoses.append(diagnosis)

    # Pattern 3: "ADDENDUM:" followed by diagnosis
    # Example: "ADDENDUM:  Acute on chronic ID anemia."
    if 'addendum' in query_text.lower():
        addendum_match = re.search(r'ADDENDUM:\s+([^.]+)', query_text, re.IGNORECASE)
        if addendum_match:
            diagnosis = addendum_match.group(1).strip()
            if len(diagnosis) > 3:
                diagnoses.append(diagnosis)

    # Pattern 4: Direct statements between "clinically valid" sections
    # For cases like: "Protein calorie malnutrition, Severe    This documentation..."
    # Look for capitalized phrases before "This documentation"
    doc_pattern = r'([A-Z][^.]+?)(?:\s{2,}This documentation|$)'
    doc_matches = re.findall(doc_pattern, query_text)
    for match in doc_matches:
        # Skip if it's part of the header
        if 'Physician Clarification' not in match and 'After reviewing' not in match:
            diagnosis = match.strip()
            # Must be reasonable length
            if 10 < len(diagnosis) < 200 and not diagnosis.startswith('[]'):
                diagnoses.append(diagnosis)

    # Remove duplicates while preserving order
    seen = set()
    unique_diagnoses = []
    for dx in diagnoses:
        dx_lower = dx.lower()
        if dx_lower not in seen:
            seen.add(dx_lower)
            unique_diagnoses.append(dx)

    return unique_diagnoses


def normalize_diagnosis(dx: str) -> str:
    """Normalize diagnosis for grouping similar items"""
    dx = dx.lower().strip()

    # Remove common modifiers that don't change the core diagnosis
    dx = re.sub(r'\s*,?\s*(?:ruled out|confirmed|present on admission|poa|not present on admission|agree with wocn)\s*$', '', dx, flags=re.IGNORECASE)
    dx = re.sub(r'^\s*(?:acute|chronic|acute on chronic)\s+', '', dx, flags=re.IGNORECASE)

    # Normalize common variations
    replacements = {
        'protein calorie malnutrition': 'malnutrition',
        'protein-calorie malnutrition': 'malnutrition',
        'severe protein calorie malnutrition': 'malnutrition',
        'moderate protein calorie malnutrition': 'malnutrition',
        'pressure ulcer': 'pressure injury',
        'decubitus ulcer': 'pressure injury',
        'diabetes mellitus': 'diabetes',
        'diabetes type ii': 'diabetes',
        'diabetes type 2': 'diabetes',
        'respiratory failure': 'respiratory failure',
        'heart failure': 'heart failure',
        'chf': 'heart failure',
        'aki': 'acute kidney injury',
    }

    for old, new in replacements.items():
        if old in dx:
            dx = dx.replace(old, new)

    return dx


def categorize_diagnosis(dx: str) -> tuple:
    """
    Categorize diagnosis and determine if it's:
    1. Lab-driven (needs structured data)
    2. Documentation-only (ruled out, unable to determine)
    3. Narrative-based (can be detected from discharge summary text)

    Returns: (category, is_detectable_from_text)
    """
    dx_lower = dx.lower()

    # Documentation queries (not actual diagnoses to suggest)
    if any(phrase in dx_lower for phrase in ['ruled out', 'unable to determine', 'clinically unable']):
        return ('documentation', False)

    # Lab-driven (requires structured lab values)
    lab_keywords = [
        'lactic acidosis', 'lactate',
        'hypokalemia', 'hyperkalemia', 'potassium',
        'hyponatremia', 'hypernatremia', 'sodium',
        'hypocalcemia', 'hypercalcemia', 'calcium',
        'hypomagnesemia', 'hypermagnesemia', 'magnesium',
        'hypophosphatemia', 'hyperphosphatemia', 'phosphate',
        'anion gap'
    ]
    if any(kw in dx_lower for kw in lab_keywords):
        return ('lab_driven', False)

    # Narrative-based (can detect from discharge summary)
    return ('narrative', True)


def main():
    print("="*80)
    print("CDI GAP ANALYSIS - Identifying High-Value Missing Diagnoses")
    print("="*80)

    # Load data
    df = pd.read_csv('data/processed/cdi_queries_clean.csv')
    print(f"\n1. Loaded {len(df)} CDI queries")

    # Extract all diagnoses
    print("\n2. Extracting all CDI diagnoses from queries...")
    all_diagnoses = []
    diagnosis_details = []  # Store with metadata

    for idx, row in df.iterrows():
        query_text = row['cdi_query']
        diagnoses = extract_all_cdi_diagnoses(query_text)

        for dx in diagnoses:
            normalized = normalize_diagnosis(dx)
            category, detectable = categorize_diagnosis(dx)

            all_diagnoses.append(normalized)
            diagnosis_details.append({
                'original': dx,
                'normalized': normalized,
                'category': category,
                'detectable': detectable,
                'anon_id': row['anon_id']
            })

    print(f"   Extracted {len(diagnosis_details)} total diagnosis mentions")
    print(f"   Unique normalized diagnoses: {len(set(all_diagnoses))}")

    # Count frequency
    diagnosis_counts = Counter(all_diagnoses)

    # Categorize by type
    by_category = defaultdict(list)
    for detail in diagnosis_details:
        by_category[detail['category']].append(detail['normalized'])

    print(f"\n3. Breakdown by category:")
    print(f"   - Narrative-based (detectable): {len([d for d in diagnosis_details if d['detectable']])} mentions")
    print(f"   - Lab-driven (needs structured data): {len([d for d in diagnosis_details if d['category'] == 'lab_driven'])} mentions")
    print(f"   - Documentation queries: {len([d for d in diagnosis_details if d['category'] == 'documentation'])} mentions")

    # Get current coverage from cdi_llm_predictor.py
    print(f"\n4. Analyzing current coverage in cdi_llm_predictor.py...")

    # Read current prompt to see what's covered
    with open('scripts/cdi_llm_predictor.py', 'r') as f:
        prompt_content = f.read()

    currently_covered = {
        'electrolyte abnormalities': ['hyponatremia', 'hypernatremia', 'hypokalemia', 'hyperkalemia',
                                      'hypocalcemia', 'hypercalcemia', 'hypomagnesemia', 'hypermagnesemia',
                                      'hypophosphatemia', 'hyperphosphatemia'],
        'malnutrition': ['malnutrition', 'protein calorie malnutrition', 'cachexia', 'hypoalbuminemia'],
        'pressure injuries': ['pressure injury', 'pressure ulcer', 'decubitus ulcer'],
        'anemia': ['anemia', 'blood loss anemia', 'iron deficiency anemia'],
        'pancytopenia': ['pancytopenia', 'thrombocytopenia', 'leukopenia'],
        'sepsis': ['sepsis', 'severe sepsis', 'septic shock'],
        'aki': ['acute kidney injury', 'aki', 'acute renal failure'],
        'diabetes': ['diabetes', 'hyperglycemia', 'hypoglycemia', 'steroid induced hyperglycemia'],
        'lactic acidosis': ['lactic acidosis'],
        'immunocompromised': ['immunocompromised'],
        'bmi': ['obesity', 'overweight', 'underweight'],
        'respiratory failure': ['respiratory failure'],
    }

    # Flatten covered diagnoses
    covered_flat = set()
    for dx_list in currently_covered.values():
        covered_flat.update(dx_list)

    # Find gaps - high-frequency narrative diagnoses not currently covered
    print(f"\n5. Identifying HIGH-VALUE GAPS...")
    print(f"   (Narrative-based diagnoses with frequency ≥3 that aren't covered)\n")

    gaps = []
    for dx, count in diagnosis_counts.most_common(100):
        # Check if it's narrative-based (detectable from text)
        is_narrative = any(d['normalized'] == dx and d['detectable'] for d in diagnosis_details)

        if not is_narrative:
            continue

        # Check if already covered
        is_covered = any(dx in covered or covered in dx for covered in covered_flat)

        if not is_covered and count >= 3:  # High frequency threshold
            gaps.append((dx, count))

    print("   TOP 20 HIGH-VALUE GAPS:")
    print("   " + "-"*76)
    print(f"   {'Diagnosis':<50} {'Count':>10} {'Priority':>10}")
    print("   " + "-"*76)

    for idx, (dx, count) in enumerate(gaps[:20], 1):
        priority = 'HIGH' if count >= 10 else 'MEDIUM' if count >= 5 else 'LOW'
        print(f"   {dx:<50} {count:>10} {priority:>10}")

    # Show examples of each gap
    print(f"\n6. DETAILED EXAMPLES OF TOP GAPS:\n")
    for dx, count in gaps[:10]:
        print(f"   {dx.upper()} (n={count})")
        # Get original examples
        examples = [d['original'] for d in diagnosis_details if d['normalized'] == dx][:3]
        for ex in examples:
            print(f"      - {ex}")
        print()

    # Summary by priority
    print("="*80)
    print("SUMMARY & RECOMMENDATIONS")
    print("="*80)

    high_priority = [g for g in gaps if g[1] >= 10]
    medium_priority = [g for g in gaps if 5 <= g[1] < 10]
    low_priority = [g for g in gaps if 3 <= g[1] < 5]

    print(f"\nHIGH PRIORITY (≥10 occurrences): {len(high_priority)} diagnoses")
    print(f"MEDIUM PRIORITY (5-9 occurrences): {len(medium_priority)} diagnoses")
    print(f"LOW PRIORITY (3-4 occurrences): {len(low_priority)} diagnoses")

    print(f"\nRECOMMENDATION:")
    print(f"Add HIGH + MEDIUM priority diagnoses to cdi_llm_predictor.py")
    print(f"This would cover {sum(g[1] for g in high_priority + medium_priority)} additional CDI queries")
    print(f"Expected recall improvement: ~{(sum(g[1] for g in high_priority + medium_priority) / len(diagnosis_details) * 100):.1f}% of all queries")

    # Save detailed results
    gap_df = pd.DataFrame(gaps, columns=['diagnosis', 'count'])
    gap_df['priority'] = gap_df['count'].apply(
        lambda x: 'HIGH' if x >= 10 else 'MEDIUM' if x >= 5 else 'LOW'
    )
    gap_df.to_csv('results/cdi_gap_analysis.csv', index=False)
    print(f"\n✅ Detailed gap analysis saved to: results/cdi_gap_analysis.csv")


if __name__ == '__main__':
    main()
