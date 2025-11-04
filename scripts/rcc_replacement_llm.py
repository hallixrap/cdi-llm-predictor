#!/usr/bin/env python3
"""
Complete .rcc Replacement - LLM-based diagnosis suggester
Combines RCC baseline (what physicians check) + CDI expertise (what they miss)

This replaces the entire Epic .rcc workflow:
- Matches current physician performance (RCC baseline)
- Adds CDI-level expertise for high-value diagnoses
- Eliminates manual checklist burden
"""

import json
import requests
from typing import Dict, List

def call_stanford_llm(prompt: str, api_key: str, model: str = "gpt-4.1") -> str:
    """Call Stanford's PHI-safe LLM API"""
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Content-Type': 'application/json'
    }

    url = f"https://apim.stanfordhealthcare.org/openai-eastus2/deployments/{model}/chat/completions?api-version=2025-01-01-preview"

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1  # Low temperature for consistent clinical reasoning
    })

    response = requests.post(url, headers=headers, data=payload, timeout=60)

    if response.status_code != 200:
        error_msg = f"API Error {response.status_code}: {response.text}"
        if response.status_code == 401:
            error_msg += "\n\nPossible causes:"
            error_msg += "\n1. API key has expired - contact Fateme Nateghi for new credentials"
            error_msg += "\n2. Not connected to Stanford VPN (required for PHI-safe API access)"
            error_msg += "\n3. API key format is incorrect"
        raise Exception(error_msg)

    try:
        return json.loads(response.text)['choices'][0]['message']['content']
    except (KeyError, json.JSONDecodeError) as e:
        raise Exception(f"Unexpected API response format: {response.text[:500]}")


def predict_all_diagnoses(discharge_summary: str, api_key: str, model: str = "gpt-4.1") -> Dict:
    """
    Complete .rcc replacement: Predict ALL relevant diagnoses from discharge summary.

    Combines:
    1. RCC BASELINE (100 notes): Common diagnoses physicians typically check
    2. CDI EXPERTISE (539 queries): High-value diagnoses physicians commonly miss

    Goal: Match current physician performance + add CDI-level catches
    """

    prompt = f"""You are an AI system replacing the Epic .rcc workflow at Stanford Healthcare.

**YOUR TASK:** Analyze this discharge summary and suggest ALL relevant diagnoses that should be documented. Your suggestions must:
1. ✓ Match what physicians currently check in .rcc (baseline performance)
2. ✓ Catch high-value diagnoses that CDI specialists commonly query about (expert level)
3. ✓ Never miss common diagnoses (don't regress below current performance)

**CONTEXT - TWO DATA SOURCES:**

## PART 1: RCC BASELINE (What physicians commonly check - 640 diagnoses from 100 notes)

**TOP 20 MOST COMMONLY CHECKED DIAGNOSES:**
1. Severe protein calorie malnutrition (19% of notes)
2. Obesity (16%)
3. Acute kidney injury (14%)
4. Hypertension (14%)
5. Neoplastic (malignant) related fatigue (13%)
6. Other reduced mobility (12%)
7. Limitation of activities due to disability (11%)
8. Hyponatremia (10%)
9. Anemia due to chemotherapy or other medication (9%)
10. Anemia (8%)
11. Cachexia (8%)
12. Acute respiratory failure with hypoxia (7%)
13. Atrial fibrillation, paroxysmal (7%)
14. Weakness (7%)
15. Palliative care (6%)
16. Chronic anemia due to malignancy (6%)
17. Cancer pain (6%)
18. Chronic anemia due to renal disease (6%)
19. Atrial fibrillation, unspecified (6%)
20. Age-related physical debility (6%)

**RCC BASELINE PATTERNS:**
- Average 6.4 diagnoses per note
- Focus on common/routine diagnoses
- Physicians reliably catch these but need AI to save time
- YOU MUST match this performance (≥90% recall on RCC diagnoses)

## PART 2: CDI EXPERTISE (What physicians miss - Top 10 by query volume)

Based on 4,527+ actual Stanford CDI queries (Sep 2024 - May 2025):

**1. ELECTROLYTE ABNORMALITIES** (4,527 queries - #1 PRIORITY!)
   - Hyponatremia: Sodium <135 mEq/L (Mild 130-134, Moderate 125-129, Severe <125)
   - Hypernatremia: Sodium >145 mEq/L
   - Hypokalemia: Potassium <3.5 mEq/L
   - Hyperkalemia: Potassium >5.0 mEq/L
   - Hypocalcemia: Calcium <8.5 mg/dL
   - Pattern: Labs show abnormality but NOT documented as diagnosis

**2. ANEMIA** (2,528 queries)
   - Anemia related to chronic disease (very common, often missed)
   - Acute blood loss anemia: Hgb drop >2 g/dL + bleeding
   - Hgb <13 (men) or <12 (women)

**3. MALNUTRITION** (1,587 queries)
   - Severe protein-calorie malnutrition
   - Criteria: BMI <18.5, albumin <3.0, weight loss
   - Specify severity when possible

**4. HYPOALBUMINEMIA** (1,236 queries)
   - Albumin <3.5 g/dL (normal: 3.5-5.0)
   - Often in labs but NOT listed as diagnosis

**5. SEPSIS** (1,199 queries)
   - SIRS criteria (2+ of: Temp >38°C/<36°C, HR >90, RR >20, WBC >12k/<4k)
   - Plus suspected/documented infection
   - If organ dysfunction → Severe sepsis
   - If vasopressors → Septic shock

**6. PATHOLOGY RESULTS** (937 queries)
   - Biopsy/surgical path not incorporated into discharge dx

**7. RESPIRATORY FAILURE** (914 queries)
   - Acute hypoxic respiratory failure
   - Criteria: PaO2 <60 or O2 sat <90% or O2 requirement

**8. PRESSURE ULCER** (823 queries)
   - Must specify: Stage (1-4), Location, POA status

**9. COAGULATION DISORDERS** (809 queries)
   - Thrombocytopenia: Platelets <150k (especially <100k)
   - Coagulopathy: INR >1.5, PTT elevated

**10. HEART FAILURE** (789 queries)
    - Must specify: Acute/chronic/acute on chronic + systolic/diastolic
    - Systolic: EF <40%, Diastolic: EF ≥50%

**ADDITIONAL HIGH-VALUE DIAGNOSES:**
- **Type 2 MI (NSTEMI)**: Troponin elevated + supply/demand mismatch
- **Encephalopathy**: Altered mental status + metabolic cause
- **AKI**: Creatinine ↑ >0.3 mg/dL or >1.5x baseline
- **Dehydration**: Clinical dehydration + fluid-responsive

**DIAGNOSTIC APPROACH:**

1. **START with RCC baseline**: Scan for common diagnoses physicians typically check
   - Malnutrition, obesity, hypertension, atrial fib, anemia, AKI, etc.
   - These are your "floor" - must catch these to match current performance

2. **ADD CDI expertise**: Look for high-value diagnoses often missed
   - Electrolyte abnormalities (#1 - check ALL lab values!)
   - Specific anemia types (not just "anemia")
   - Sepsis criteria (even if not explicitly stated)
   - Specificity (acute vs chronic, systolic vs diastolic, etc.)

3. **PRIORITIZE by reimbursement impact**:
   - Major CC/MCC diagnoses (sepsis, malnutrition, respiratory failure)
   - Electrolyte abnormalities (very high volume)
   - Specificity improvements (acute on chronic HF vs just CHF)

**OUTPUT FORMAT:**

Return a JSON object with:
{{
  "rcc_baseline_diagnoses": [
    {{"diagnosis": "Severe protein calorie malnutrition", "evidence": "BMI 17.2, albumin 2.8, weight loss 15 lbs", "category": "RCC_BASELINE"}},
    ...
  ],
  "cdi_expert_diagnoses": [
    {{"diagnosis": "Hyponatremia, moderate", "evidence": "Sodium 128 mEq/L (normal 135-145)", "category": "ELECTROLYTES", "priority": "HIGH"}},
    ...
  ],
  "total_suggested": 12,
  "rcc_count": 6,
  "cdi_count": 6,
  "summary": "Found 6 baseline diagnoses physicians typically check + 6 additional high-value diagnoses often missed"
}}

**DISCHARGE SUMMARY TO ANALYZE:**

{discharge_summary}

**CRITICAL REQUIREMENTS:**
1. Check EVERY lab value for electrolyte abnormalities (highest volume query!)
2. Match RCC baseline performance (common diagnoses)
3. Add CDI-level catches (high-value diagnoses)
4. Provide specific evidence from the note for each diagnosis
5. Prioritize Major CC/MCC diagnoses for reimbursement impact

Return ONLY valid JSON, no additional text."""

    # Call LLM
    response_text = call_stanford_llm(prompt, api_key, model)

    # Parse JSON response
    try:
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError:
        # Try to extract JSON from response if it contains extra text
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
            return result
        else:
            raise Exception(f"Could not parse JSON from LLM response: {response_text[:500]}")


def generate_rcc_replacement_report(results: Dict, patient_id: str = "UNKNOWN") -> str:
    """Generate a human-readable report of .rcc replacement suggestions"""

    report = []
    report.append("="*80)
    report.append("COMPLETE .rcc REPLACEMENT - AI DIAGNOSIS SUGGESTIONS")
    report.append("="*80)
    report.append(f"Patient ID: {patient_id}")
    report.append("")

    # Summary
    total = results.get('total_suggested', 0)
    rcc_count = results.get('rcc_count', 0)
    cdi_count = results.get('cdi_count', 0)

    report.append(f"SUMMARY: {total} diagnoses suggested")
    report.append(f"  • RCC Baseline: {rcc_count} diagnoses (matches current physician performance)")
    report.append(f"  • CDI Expertise: {cdi_count} diagnoses (additional high-value catches)")
    report.append("")
    report.append(results.get('summary', ''))
    report.append("")

    # RCC Baseline diagnoses
    rcc_diagnoses = results.get('rcc_baseline_diagnoses', [])
    if rcc_diagnoses:
        report.append("="*80)
        report.append("PART 1: RCC BASELINE DIAGNOSES (Physicians typically check these)")
        report.append("="*80)
        for i, dx in enumerate(rcc_diagnoses, 1):
            report.append(f"\n{i}. {dx.get('diagnosis', 'N/A')}")
            report.append(f"   Evidence: {dx.get('evidence', 'N/A')}")

    # CDI Expert diagnoses
    cdi_diagnoses = results.get('cdi_expert_diagnoses', [])
    if cdi_diagnoses:
        report.append("\n" + "="*80)
        report.append("PART 2: CDI EXPERT DIAGNOSES (High-value diagnoses often missed)")
        report.append("="*80)
        for i, dx in enumerate(cdi_diagnoses, 1):
            priority = dx.get('priority', 'MEDIUM')
            priority_marker = "🔴" if priority == "HIGH" else "🟡"
            report.append(f"\n{i}. {priority_marker} {dx.get('diagnosis', 'N/A')} [{dx.get('category', 'N/A')}]")
            report.append(f"   Evidence: {dx.get('evidence', 'N/A')}")
            report.append(f"   Priority: {priority}")

    report.append("\n" + "="*80)
    report.append("WORKFLOW INTEGRATION")
    report.append("="*80)
    report.append("This replaces typing .rcc and manually checking 100+ boxes.")
    report.append("Physician reviews AI suggestions and accepts/rejects in one click.")
    report.append("="*80)

    return "\n".join(report)


if __name__ == "__main__":
    print("\n.rcc Replacement System - Test Mode")
    print("="*80)
    print("\nThis system combines:")
    print("  1. RCC baseline (what physicians currently check)")
    print("  2. CDI expertise (high-value diagnoses they miss)")
    print("\nResult: Complete .rcc workflow replacement")
    print("="*80)
