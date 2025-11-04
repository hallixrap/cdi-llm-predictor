#!/usr/bin/env python3
"""
CDI LLM Predictor - Identifies diagnoses that physicians often miss
Uses Stanford's PHI-safe API with GPT-4.1 or GPT-5

Based on actual CDI query patterns to capture what specialists look for
that physicians frequently forget to document, leaving money on the table.
"""

import json
import requests
import pandas as pd
from typing import Dict, List
from datetime import datetime

def call_stanford_llm(prompt: str, api_key: str, model: str = "gpt-4.1") -> str:
    """Call Stanford's PHI-safe LLM"""
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Content-Type': 'application/json'
    }

    # Model endpoints
    model_urls = {
        "gpt-4.1": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview",
        "gpt-5-nano": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5-nano/chat/completions?api-version=2024-12-01-preview",
        "gpt-4.1-mini": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1-mini/chat/completions?api-version=2025-01-01-preview"
    }

    url = model_urls.get(model, model_urls["gpt-4.1"])

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,  # Low temperature for consistency
        "max_tokens": 4000  # Increase to avoid truncation of JSON responses
    })

    response = requests.post(url, headers=headers, data=payload, timeout=60)

    # Check for HTTP errors
    if response.status_code != 200:
        error_msg = f"API Error {response.status_code}: {response.text}"
        if response.status_code == 401:
            error_msg += "\n\nPossible causes:"
            error_msg += "\n1. API key has expired - contact Fateme Nateghi for new credentials"
            error_msg += "\n2. Not connected to Stanford VPN (required for PHI-safe API access)"
            error_msg += "\n3. API key format is incorrect"
        raise Exception(error_msg)

    # Parse response
    try:
        return json.loads(response.text)['choices'][0]['message']['content']
    except (KeyError, json.JSONDecodeError):
        raise Exception(f"Unexpected API response format: {response.text[:500]}")


def predict_missed_diagnoses(discharge_summary: str, api_key: str, model: str = "gpt-4.1") -> Dict:
    """
    Predict diagnoses that CDI specialists would query about.

    This is based on analysis of 539 actual CDI queries showing what physicians
    commonly miss or fail to document adequately.
    """

    # Build prompt based on ACTUAL Stanford CDI query data + .rccautoprognote automation criteria
    prompt = f"""You are a Clinical Documentation Integrity (CDI) specialist at Stanford Healthcare reviewing a discharge summary using Stanford's .rccautoprognote automation criteria.

YOUR ROLE: Identify diagnoses that are clinically supported by evidence in the note but are MISSING or UNCLEAR in the physician's documentation. Use the SPECIFIC CLINICAL CRITERIA below - but remember discharge notes are messy, so use clinical judgment alongside the rules.

CONTEXT: Based on 4,527+ real CDI queries at Stanford (Sep 2024-May 2025) + Stanford's automated diagnosis criteria (.rccautoprognote).

**BILLING CODE OPTIMIZATION (IMPORTANT):**
CDI specialists look for the MOST SPECIFIC diagnosis that is clinically supported to maximize reimbursement accuracy:
- Always suggest the HIGHEST SPECIFICITY level when evidence supports it
- Examples of specific > general:
  * "Severe protein-calorie malnutrition" (E43) > "Malnutrition, unspecified" (E46.9)
  * "Pancytopenia" (D61.818) > "Thrombocytopenia" (D69.6) if ALL cell lines (RBC, WBC, platelets) are low
  * "Sepsis, [organism]" > "Sepsis, unspecified"
  * "Pressure ulcer, Stage 3, Sacral, POA" > "Pressure ulcer, unspecified stage"
  * "Acute hypoxic respiratory failure" (J96.01) > "Respiratory failure, unspecified" (J96.90)
  * "Type 2 diabetes with hyperglycemia" (E11.65) > "Type 2 diabetes without complications" (E11.9)

Always include when documentable:
- Specific STAGE for pressure ulcers (Stage 1, 2, 3, 4, Unstageable, Deep Tissue)
- LOCATION for pressure ulcers (sacral, heel, ischial, etc.)
- POA status (Present on Admission vs Hospital-Acquired)
- ETIOLOGY when clear (e.g., "due to chemotherapy", "due to sepsis", "secondary to CKD")
- SEVERITY qualifiers (severe, acute, chronic)

**TOP 10 MOST COMMON QUERIES (by volume with SPECIFIC CRITERIA):**

1. **ELECTROLYTE ABNORMALITIES** (4,527 queries - #1 PRIORITY!)

   a) **HYPOVOLEMIC HYPONATREMIA**:
      - Criteria: Sodium <130 mEq/L (normal 135-145) + IV 0.9% NS treatment
      - Statement: "Hypovolemic Hyponatremia evidenced by [Na value], requiring ongoing monitoring and treatment with IV NS"
      - Look for: Sodium labs, fluid resuscitation, dehydration signs

   b) **HYPERNATREMIA**:
      - Criteria: Sodium >145 mEq/L + (IV D5W OR 5% Dextrose OR 0.225% NaCl) OR two labs >145
      - Statement: "Hypernatremia evidenced by [Na value], requiring ongoing monitoring and treatment"

   c) **HYPOKALEMIA**:
      - Criteria: K <3.5 mEq/L + PO/IV Potassium (KCL)
      - Statement: "Hypokalemia evidenced by [K value], requiring ongoing monitoring and treatment with Potassium PO/IV"

   d) **HYPERKALEMIA**:
      - Criteria: K >5.5 mEq/L + Treatment (Calcium chloride/gluconate, NaHCO3, Kayexalate, Lokelma, dialysis)
      - Statement: "Hyperkalemia evidenced by [K value], requiring ongoing monitoring and treatment"

   e) **HYPOCALCEMIA**:
      - Criteria: Ca <8.4 mg/dL (or ionized Ca <1.12 mmol/L) + IV Calcium chloride/gluconate
      - Exclude if: Albumin <3 (not true hypocalcemia)
      - Statement: "Hypocalcemia evidenced by [Ca value], requiring ongoing monitoring and calcium PO/IV treatment"

   f) **HYPERCALCEMIA**:
      - Criteria: Ca >10.5 mg/dL + (Bisphosphonate OR Calcitonin OR Cinacalcet) OR two labs >10.5
      - Statement: "Hypercalcemia evidenced by [Ca value], requiring ongoing monitoring and treatment"

   g) **HYPOMAGNESEMIA**:
      - Criteria: Mg <1.6 mg/dL + IV/PO Magnesium
      - Statement: "Hypomagnesemia evidenced by [Mg value], requiring ongoing monitoring and treatment with PO/IV Magnesium"

   h) **HYPOPHOSPHATEMIA**:
      - Criteria: Phosphorus <2.5 mg/dL + IV/PO sodium/potassium phosphate
      - Statement: "Hypophosphatemia evidenced by [Phos value], requiring ongoing monitoring and treatment PO/IV phosphate"

   i) **HYPERPHOSPHATEMIA**:
      - Criteria: Phosphorus >4.5 mg/dL + Phosphate binders (Calcium acetate, Sevelamer, etc.) OR two labs >4.5
      - Statement: "Hyperphosphatemia evidenced by [Phos value], requiring ongoing monitoring and treatment"

2. **ANEMIA** (2,528 queries)

   a) **ACUTE BLOOD LOSS ANEMIA (in operative patients)**:
      - Criteria: 2-point Hgb drop from baseline + Hgb <13 (M) or <12 (F) + >250ml EBL during surgery + Hgb checked >2x/day
      - Look back 5 days for baseline
      - Statement: "Acute blood loss anemia evidenced by [Hgb value], with acute blood loss, requiring ongoing monitoring and treatment or transfusion"

   b) **IRON DEFICIENCY ANEMIA**:
      - Criteria: Hgb <12 (M) or <11.7 (F) + PO/IV Iron treatment (Ferrous sulfate/fumarate/gluconate, IV Iron, IV Ferric gluconate, IV Dextran)
      - Statement: "Iron Deficiency anemia evidenced by Hgb [value], requiring ongoing monitoring and treatment with PO/IV Iron"

   c) **ANEMIA RELATED TO CHRONIC DISEASE**:
      - Criteria: Chronic Hgb <13 (M) or <12 (F) with chronic disease (cancer, CKD, inflammatory conditions)
      - Pattern: Low Hgb noted but not coded as anemia type

3. **MALNUTRITION** (1,587 queries)
   - Criteria: BMI ≤18.5 OR documented weight loss OR Albumin <3.0-3.5 OR temporal wasting/muscle loss
   - **Underweight**: BMI ≤18.5 kg/m²
   - **Severe protein-calorie malnutrition**: BMI <18.5 + Albumin <3.0 + weight loss
   - **Continued malnutrition**: Ongoing malnutrition requiring additional time/resources
   - Statement: "Diagnosis [malnutrition type], BMI [value], increasing health risks, requiring additional time, resources, and/or education"
   - Look for: BMI, albumin, weight loss, nutritional support, dietician involvement

4. **HYPOALBUMINEMIA** (1,236 queries)
   - Criteria: Albumin <3.2 g/dL on at least TWO panels
   - Normal: 3.5-5.0 g/dL
   - Statement: "Hypoalbuminemia evidenced by minimum Albumin of [value] g/dL, requiring ongoing monitoring and/or treatment"
   - Pattern: Lab value present, diagnosis absent (often separate from malnutrition query)

5. **SEPSIS** (#5 Priority - 1,199 queries):
   - Criteria: SIRS (2+ of: Temp >38°C/<36°C, HR >90, RR >20, WBC >12k/<4k) + suspected/documented infection
   - Often clinically evident but not explicitly stated as "sepsis"
   - If organ dysfunction/hypotension → Severe sepsis
   - If vasopressors → Septic shock
   - Look for: Infection signs, SIRS criteria, antibiotics, blood cultures, organ dysfunction

6. **PATHOLOGY RESULTS** (#6 Priority - 937 queries):
   - Criteria: Biopsy/surgical pathology findings not incorporated into discharge diagnoses
   - Pattern: Path report shows specific diagnosis but not in discharge dx list
   - Example: "Path shows adenocarcinoma" but discharge only says "mass"
   - Look for: Pathology report mentions, biopsy results in note

7. **RESPIRATORY FAILURE** (#7 Priority - 914 queries):
   - Criteria: Oxygen requirement + (PaO2 <60 mmHg OR O2 sat <90% OR PaCO2 >45 mmHg)
   - Pattern: "Respiratory distress" documented but not "acute respiratory failure"
   - Look for: Oxygen requirement, mechanical ventilation, hypoxia, hypercapnia

7b. **PULMONARY EDEMA** (Gap Analysis - 9 queries):
    - **CARDIOGENIC** (due to heart failure): Fluid overload from cardiac dysfunction
      * Criteria: Pulmonary edema + heart failure + increased JVP/edema + BNP elevated
      * Statement: "Acute Pulmonary Edema, Cardiogenic, due to Heart Failure"
    - **NON-CARDIOGENIC** (ARDS, volume overload without HF): Not from heart
      * Criteria: Pulmonary edema WITHOUT heart failure as primary cause
      * Common causes: ARDS, sepsis, fluid overload post-op, transfusion-related (TRALI)
      * Statement: "Acute Pulmonary Edema, Non-Cardiogenic, due to [cause]"
    - Pattern: "Pulmonary edema" or "flash pulmonary edema" mentioned but etiology not specified
    - Look for: Chest X-ray findings, oxygen requirement, lasix use, heart failure vs other causes

8. **PRESSURE ULCER** (#8 Priority - 823 queries):
   - Criteria: Must specify Stage (1-4, unstageable, deep tissue injury) + Location + POA status
   - POA (Present on Admission) status CRITICAL for reimbursement
   - Pattern: Nursing notes it but physician doesn't code it
   - Look for: Wound descriptions, staging, location (sacral, heel, ischial)

9. **COAGULATION DISORDERS** (#9 Priority - 809 queries):

   a) **THROMBOCYTOPENIA**:
      - Criteria: Platelets <145 K/uL on at least TWO panels
      - Statement: "Thrombocytopenia evidenced by minimum platelet of [value] K/uL, requiring ongoing monitoring and/or treatment with platelet transfusion"
      - Pattern: Lab abnormality present but not diagnosed

   b) **PANCYTOPENIA**:
      - Criteria: Hgb <13 (M) or <12 (F) + WBC <4.0 + Platelets <150 on at least TWO panels
      - Statement: "Pancytopenia evidenced by all three blood cell lines below normal reference range, requiring ongoing monitoring and/or treatment"

   c) **PANCYTOPENIA DUE TO CHEMOTHERAPY**:
      - Criteria: Above + Chemotherapy administered
      - Statement: "Pancytopenia due to Chemotherapy evidenced by all three blood cell lines below normal, requiring ongoing monitoring and/or treatment"

10. **HEART FAILURE** (#10 Priority - 789 queries):
    - Criteria: Must specify Acute/Chronic/Acute on chronic + Systolic (EF <40%) vs Diastolic (EF ≥50%)
    - I50.23 (acute on chronic systolic) is high-value but often missing specificity
    - **Common CDI Query: "Diastolic CHF, Acute-on-Chronic"** - specify acuity when worsening
    - Pattern: "CHF" documented but lacks specificity, or acuity not specified
    - Look for: EF values, BNP, edema, dyspnea, heart failure treatment, acute decompensation
    - Statement: "Acute on Chronic Diastolic Heart Failure, evidenced by [EF value], [symptoms], requiring treatment"

10b. **TYPE 2 MYOCARDIAL INFARCTION / DEMAND ISCHEMIA** (Gap Analysis - 9 queries):
     - **NSTEMI-Type 2** (Demand Ischemia): MI from supply-demand mismatch, NOT plaque rupture
     - Criteria: Troponin elevation + ischemic cause (sepsis, hypotension, tachycardia, anemia) WITHOUT acute coronary syndrome
     - **HIGH VALUE**: Type 2 MI (I21.A1) is more specific than "demand ischemia" or "troponin elevation"
     - Pattern: Troponin elevated with clear stressor but not called "Type 2 MI"
     - Look for: Troponin rise + sepsis/shock/tachycardia/anemia + NO cardiac cath/PCI
     - Statement: "Type 2 Non-ST Elevation Myocardial Infarction (NSTEMI) due to demand ischemia from [cause]"
     - Common CDI query: "Demand Ischemia" or "Troponin elevation" → should be "Type 2 MI"

**ADDITIONAL HIGH-VALUE DIAGNOSES (from .rccautoprognote):**

11. **ACUTE KIDNEY INJURY/FAILURE** (500+ queries, Oct 2024):
    - Criteria: Creatinine change >0.3 mg/dL with abnormal Cr (M: 0.67-1.17, F: 0.51-0.95)
    - Exclude: CKD or Chronic Renal Disease in active problem list/PMH
    - Pattern: Cr elevation noted but "AKI" not documented
    - Look for: Rising creatinine, baseline comparison

12. **CACHEXIA** (Feb 2024):
    - Criteria: Wasting syndrome with weight loss, muscle atrophy, fatigue
    - Often seen with cancer, chronic disease
    - Pattern: Weight loss and wasting documented but not coded as cachexia
    - Look for: Significant weight loss, temporal wasting, muscle loss, chronic disease

13. **LACTIC ACIDOSIS** (Dec 2022):
    - Criteria: Lactate >4 mmol/L (whole blood or ISTAT) + IV fluids OR IV NaHCO3
    - Statement: "Lactic Acidosis evidenced by lactate [value], requiring ongoing monitoring and/or treatment"
    - Look for: Elevated lactate, fluid resuscitation, bicarbonate administration

14. **DIABETES WITH HYPERGLYCEMIA**:
    - Criteria: DM diagnosis + Glucose >180 + Diabetes medications (Insulin, Metformin, Glipizide, Glyburide, SGLT2i, GLP1-RA, etc.) OR two labs >180
    - Statement: "Diabetes Mellitus with Hyperglycemia evidenced by glucose [value], requiring ongoing monitoring and treatment"
    - Look for: Diabetes history, elevated glucose, insulin/oral hypoglycemics

15. **DIABETES WITH HYPOGLYCEMIA**:
    - Criteria: DM diagnosis + Glucose <70 + Treatment (IV/PO Glucose, IV Dextrose, Glucagon) OR two labs <70
    - Statement: "Diabetes Mellitus with Hypoglycemia evidenced by glucose [value], requiring ongoing monitoring and treatment"

16. **STEROID-INDUCED HYPERGLYCEMIA**:
    - Criteria: Glucose >180 + Steroid use (Prednisone, Dexamethasone, Hydrocortisone) WITHOUT prior DM diagnosis
    - Statement: "Drug induced Hyperglycemia evidenced by glucose [value], requiring ongoing monitoring and treatment"

17. **IMMUNOCOMPROMISED STATE** (In Progress):
    - Criteria: Malignancy under Chemotherapy/Radiotherapy OR Organ Transplant on Immunosuppressants
    - Statement: "Immunocompromised State due to chemotherapy/Radiotherapy for Malignancy OR Immunosuppressant for Transplant, requiring ongoing monitoring and treatment"
    - Look for: Active chemo, immunosuppressant medications, transplant history

18. **ENCEPHALOPATHY** (710+ queries):
    - **Delirium due to known physiological condition**
    - **Metabolic encephalopathy** (metabolic, toxic, hepatic, septic)
    - Criteria: Altered mental status + metabolic cause
    - Pattern: AMS noted but not formally diagnosed as encephalopathy
    - Look for: Confusion, altered mental status, metabolic derangements

19. **TYPE 2 MI (NSTEMI)** (743+ queries):
    - Criteria: Elevated troponin + supply/demand mismatch (anemia, hemorrhage, sepsis, shock, tachycardia)
    - Pattern: Troponin noted but not diagnosed as MI
    - Look for: Troponin elevation + clear precipitating cause

20. **DEHYDRATION/HYPOVOLEMIA**:
    - Criteria: Clinical dehydration signs + elevated BUN/Cr ratio + IV fluid resuscitation
    - Pattern: "Received fluids" but not diagnosed
    - Look for: Elevated BUN/Cr, fluid boluses, orthostatic hypotension

21. **BMI-RELATED DIAGNOSES** (Aug 2022):
    - **Overweight**: BMI 25.0-29.9 kg/m²
    - **Obesity**: BMI 30.0-39.9 kg/m²
    - **Severe (Morbid) Obesity**: BMI ≥40 OR BMI >35 with serious obesity-related condition (OSA, DM, CAD, HTN, GERD, etc.)
    - Statement: "Diagnosis [obesity type], BMI [value], increasing health risks for patient, requiring additional time, resources and/or education"

22. **RADIOLOGY FINDINGS** (In Progress/Pipeline):
    - **Cerebral Edema/Brain Herniation/Brain Compression**: From CT/MRI brain impression
    - **Hepatic Steatosis**: From CT/MRI/US abdomen impression
    - Statement: "[Finding] as evidenced by [imaging type] on [date], requiring ongoing monitoring, treatment and/or consideration in treatment/care plan"
    - Look for: Radiology impressions not incorporated into diagnoses

**CRITICAL QUERY APPROACH:**
Remember: Discharge notes are MESSY. The rules above are guidelines from .rccautoprognote, but:
1. Use clinical judgment - don't be rigid about exact lab values if clinical context supports diagnosis
2. Look for patterns even if exact criteria isn't met (e.g., treatment without documented lab)
3. Consider clinical significance - query high-value diagnoses with strong evidence
4. Be specific with evidence - cite actual values, medications, treatments from the note
5. Only query when evidence is clear and diagnosis is MISSING or UNCLEAR

DISCHARGE SUMMARY TO REVIEW:
{discharge_summary}

YOUR TASK:
1. Identify diagnoses with clinical evidence that should be queried
2. Focus on HIGH-VALUE diagnoses (those listed above)
3. Provide specific clinical evidence from the note
4. Be conservative - only query when evidence is clear

Return JSON format:
{{
    "missed_diagnoses": [
        {{
            "diagnosis": "Specific diagnosis name",
            "category": "Sepsis/Malnutrition/Anemia/etc",
            "icd10_code": "Suggested ICD-10 code",
            "clinical_evidence": "Specific evidence from note (labs, vitals, symptoms)",
            "query_reasoning": "Why this would be queried by CDI",
            "reimbursement_impact": "High/Medium/Low",
            "confidence": "High/Medium/Low"
        }}
    ],
    "query_count": "number of diagnoses to query",
    "total_potential_value": "Estimated $ impact"
}}

IMPORTANT:
- Only include diagnoses with clear clinical evidence
- Prioritize the top 8 categories above (they represent 60% of all queries)
- Be specific about evidence (cite actual values from the note)
- Don't query what's already well-documented
"""

    response = call_stanford_llm(prompt, api_key, model)

    try:
        # Try direct JSON parse first
        result = json.loads(response)
    except json.JSONDecodeError:
        # LLM often wraps JSON in markdown code blocks like ```json\n...\n```
        # Try to extract JSON from markdown
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                # Still failed - response might be truncated
                result = {
                    "missed_diagnoses": [],
                    "error": "JSON parse failed - Response may be truncated",
                    "raw_response": response[:1000]
                }
        else:
            # No markdown code block, try to find JSON object
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    result = {
                        "missed_diagnoses": [],
                        "error": "JSON parse failed - LLM did not return valid JSON",
                        "raw_response": response[:1000]
                    }
            else:
                result = {
                    "missed_diagnoses": [],
                    "error": "JSON parse failed - No JSON found in response",
                    "raw_response": response[:1000]
                }

    return result


def generate_cdi_report(results: Dict, patient_id: str = "Unknown") -> str:
    """Generate CDI query report"""
    report = "="*80 + "\n"
    report += "CDI DOCUMENTATION OPPORTUNITY REPORT\n"
    report += f"Patient ID: {patient_id}\n"
    report += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += "="*80 + "\n\n"

    missed = results.get('missed_diagnoses', [])

    if missed:
        # Sort by reimbursement impact
        high_impact = [d for d in missed if d.get('reimbursement_impact') == 'High']
        medium_impact = [d for d in missed if d.get('reimbursement_impact') == 'Medium']
        low_impact = [d for d in missed if d.get('reimbursement_impact') == 'Low']

        report += f"TOTAL QUERIES IDENTIFIED: {len(missed)}\n"
        report += f"  High-value (MCC): {len(high_impact)}\n"
        report += f"  Medium-value (CC): {len(medium_impact)}\n"
        report += f"  Low-value: {len(low_impact)}\n\n"

        if high_impact:
            report += "🔴 HIGH-VALUE DOCUMENTATION OPPORTUNITIES (Major CC/MCC):\n"
            report += "-"*80 + "\n"
            for i, dx in enumerate(high_impact, 1):
                report += f"\n{i}. {dx['diagnosis']} ({dx.get('icd10_code', 'N/A')})\n"
                report += f"   Category: {dx.get('category', 'N/A')}\n"
                report += f"   Clinical Evidence: {dx['clinical_evidence']}\n"
                report += f"   Query Reasoning: {dx['query_reasoning']}\n"
                report += f"   Confidence: {dx.get('confidence', 'N/A')}\n"

        if medium_impact:
            report += "\n🟡 MODERATE-VALUE DOCUMENTATION OPPORTUNITIES (CC):\n"
            report += "-"*80 + "\n"
            for i, dx in enumerate(medium_impact, 1):
                report += f"\n{i}. {dx['diagnosis']} ({dx.get('icd10_code', 'N/A')})\n"
                report += f"   Category: {dx.get('category', 'N/A')}\n"
                report += f"   Clinical Evidence: {dx['clinical_evidence']}\n"

        # Financial impact
        report += "\n" + "="*80 + "\n"
        report += "FINANCIAL IMPACT ESTIMATE:\n"
        impact_value = results.get('total_potential_value', 'Not calculated')
        report += f"Potential additional reimbursement: {impact_value}\n"
        report += f"Based on {len(high_impact)} high-value and {len(medium_impact)} moderate-value queries\n"

    else:
        report += "No additional documentation opportunities identified.\n"
        report += "Current documentation appears comprehensive.\n"

    report += "\n" + "="*80 + "\n"
    report += "This analysis is based on 539 actual CDI queries at Stanford Healthcare\n"
    report += "Identifies diagnoses physicians commonly miss, leaving money on the table\n"
    report += "="*80 + "\n"

    return report


def batch_process_summaries(csv_path: str, api_key: str, output_path: str, model: str = "gpt-4.1", limit: int = None):
    """
    Process multiple discharge summaries from CSV

    Args:
        csv_path: Path to CSV with discharge_summary column
        api_key: Stanford API key
        output_path: Where to save results
        model: Which LLM model to use
        limit: Optional limit on number of records to process
    """
    print(f"\n{'='*80}")
    print(f"CDI LLM BATCH PROCESSOR")
    print(f"Model: {model}")
    print(f"{'='*80}\n")

    # Load data
    df = pd.read_csv(csv_path)
    if limit:
        df = df.head(limit)

    print(f"Processing {len(df)} discharge summaries...")

    results = []

    for idx, row in df.iterrows():
        print(f"\nProcessing {idx+1}/{len(df)}...")

        try:
            patient_id = row.get('patient_id', f'Unknown_{idx}')
            discharge_summary = row['discharge_summary']

            # Get predictions
            prediction = predict_missed_diagnoses(discharge_summary, api_key, model)

            # Add metadata
            prediction['patient_id'] = patient_id
            prediction['discharge_date'] = row.get('discharge_date', 'Unknown')
            if 'cdi_diagnoses' in row:
                prediction['actual_cdi_query'] = row['cdi_diagnoses']
            if 'diagnosis_categories' in row:
                prediction['actual_category'] = row['diagnosis_categories']

            results.append(prediction)

            # Show summary
            num_queries = len(prediction.get('missed_diagnoses', []))
            print(f"  Found {num_queries} potential queries")

        except Exception as e:
            print(f"  Error: {str(e)}")
            results.append({
                'patient_id': row.get('patient_id', f'Unknown_{idx}'),
                'error': str(e)
            })

    # Save results
    output_df = pd.DataFrame(results)
    output_df.to_csv(output_path, index=False)

    print(f"\n{'='*80}")
    print(f"Results saved to: {output_path}")
    print(f"Processed: {len(results)} summaries")
    print(f"{'='*80}\n")

    return results


def main():
    """Interactive mode for testing"""
    print("\n" + "="*80)
    print("CDI DIAGNOSIS PREDICTOR - LLM-Based")
    print("Identifies diagnoses physicians commonly miss")
    print("="*80 + "\n")

    api_key = input("Enter Stanford API key: ").strip()

    # Model selection
    print("\nAvailable models:")
    print("1. gpt-4.1 (recommended)")
    print("2. gpt-5-nano")
    print("3. gpt-4.1-mini")
    model_choice = input("Select model (1-3, default 1): ").strip() or "1"

    models = {"1": "gpt-4.1", "2": "gpt-5-nano", "3": "gpt-4.1-mini"}
    model = models.get(model_choice, "gpt-4.1")

    print(f"\nUsing model: {model}")
    print("\nPaste discharge summary (Enter twice when done):\n")

    lines = []
    empty_count = 0
    while empty_count < 2:
        line = input()
        if line == "":
            empty_count += 1
        else:
            empty_count = 0
            lines.append(line)

    discharge_summary = "\n".join(lines)

    if discharge_summary.strip():
        print("\nAnalyzing for missed diagnoses...")
        results = predict_missed_diagnoses(discharge_summary, api_key, model)
        report = generate_cdi_report(results)

        print(report)

        # Save results
        with open("cdi_llm_prediction_report.txt", "w") as f:
            f.write(report)
            f.write("\n\nRAW DATA:\n")
            f.write(json.dumps(results, indent=2))

        print("\nReport saved to: cdi_llm_prediction_report.txt")


if __name__ == "__main__":
    main()
