#!/usr/bin/env python3
"""
CDI LLM Predictor - Identifies diagnoses that physicians often miss
Uses Stanford's PHI-safe API with GPT-4.1 or GPT-5

Based on actual CDI query patterns to capture what specialists look for
that physicians frequently forget to document, leaving money on the table.
"""

import json
import re
import time
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

    # Model endpoints - Stanford SecureGPT API
    model_urls = {
        "gpt-5": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        "gpt-4.1": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview",
        "gpt-5-nano": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5-nano/chat/completions?api-version=2024-12-01-preview",
        "gpt-4.1-mini": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1-mini/chat/completions?api-version=2025-01-01-preview",
        # Claude models via Stanford Anthropic endpoint
        "claude-opus-4": "https://apim.stanfordhealthcare.org/anthropic/v1/messages",
        "claude-sonnet-4": "https://apim.stanfordhealthcare.org/anthropic/v1/messages",
    }

    url = model_urls.get(model, model_urls["gpt-4.1"])
    is_claude = model.startswith("claude")

    # Claude uses different request/response format
    if is_claude:
        claude_model_map = {
            "claude-opus-4": "claude-opus-4-20250514",
            "claude-sonnet-4": "claude-sonnet-4-20250514",
        }
        payload = json.dumps({
            "model": claude_model_map.get(model, model),
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}]
        })
    else:
        # OpenAI format - GPT-5 models have different API requirements
        is_gpt5 = model.startswith("gpt-5")
        request_body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        # GPT-5 doesn't support custom temperature, only uses max_completion_tokens
        # IMPORTANT: GPT-5 uses reasoning tokens BEFORE output tokens. With complex prompts,
        # it may use 4000+ reasoning tokens, leaving nothing for actual output.
        # We need to set max_completion_tokens high enough to cover reasoning + output.
        if is_gpt5:
            request_body["max_completion_tokens"] = 16000  # Allow ~12k reasoning + 4k output
            # GPT-5 only supports temperature=1 (default), so don't set it
        else:
            request_body["temperature"] = 0.1  # Low temperature for consistency
            request_body["max_tokens"] = 4000
        payload = json.dumps(request_body)

    # Retry with exponential backoff for transient errors (rate limits, timeouts)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=120)

            if response.status_code == 429 or response.status_code >= 500:
                # Rate limited or server error — retry with backoff
                wait = 2 ** attempt + 1  # 2, 3, 5, 9, 17 seconds
                print(f"  ⏳ API {response.status_code}, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            if response.status_code != 200:
                error_msg = f"API Error {response.status_code}: {response.text}"
                if response.status_code == 401:
                    error_msg += "\n\nPossible causes:"
                    error_msg += "\n1. API key has expired - contact Fateme Nateghi for new credentials"
                    error_msg += "\n2. Not connected to Stanford VPN (required for PHI-safe API access)"
                    error_msg += "\n3. API key format is incorrect"
                raise Exception(error_msg)

            break  # Success
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1
                print(f"  ⏳ Connection error, retrying in {wait}s (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(wait)
            else:
                raise

    # Throttle: brief pause between calls to avoid rate limits
    time.sleep(0.5)

    # Parse response - Claude vs OpenAI have different formats
    try:
        resp_json = json.loads(response.text)
        if is_claude:
            return resp_json['content'][0]['text']
        else:
            content = resp_json['choices'][0]['message']['content']
            # Debug: Check for empty content with GPT-5
            if content is None or content == "":
                # Log the full response structure for debugging
                import os
                debug_log = os.environ.get('CDI_DEBUG_LOG')
                if debug_log:
                    with open(debug_log, 'a') as f:
                        f.write(f"\n{'='*80}\n")
                        f.write(f"EMPTY CONTENT DETECTED for model: {model}\n")
                        f.write(f"Full API response:\n{json.dumps(resp_json, indent=2)[:3000]}\n")
                # Check for finish_reason
                finish_reason = resp_json['choices'][0].get('finish_reason', 'unknown')
                if finish_reason == 'length':
                    raise Exception(f"GPT-5 response truncated (finish_reason=length). Try shorter prompt.")
                elif finish_reason == 'content_filter':
                    raise Exception(f"GPT-5 content filtered. Response blocked by safety filter.")
            return content if content else ""
    except (KeyError, json.JSONDecodeError) as e:
        raise Exception(f"Unexpected API response format: {response.text[:500]}. Error: {e}")


def extract_documented_diagnoses(discharge_summary: str) -> List[str]:
    """
    Extract diagnoses already documented in structured sections of the discharge summary.
    Used to filter out LLM predictions that match already-documented conditions.

    Handles both newline-separated and multi-space-separated text formats
    (BigQuery CSV exports often flatten newlines to double spaces).
    """
    documented = []

    # Normalize: replace 2+ spaces with newlines so section parsing works uniformly
    text = re.sub(r'  +', '\n', discharge_summary)

    # Section headers that contain documented diagnoses
    section_headers = [
        r'Discharge\s+Diagnos[ei]s',
        r'Admitting\s+Diagnos[ei]s',
        r'Principal\s+Diagnos[ei]s',
        r'Secondary\s+Diagnos[ei]s',
        r'Active\s+Problems?',
        r'Problem\s+List',
    ]

    for header in section_headers:
        pattern = header + r'\s*:?\s*\n(.*?)(?=\n[A-Z][A-Za-z\s/]{3,}:|$)'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            for line in match.split('\n'):
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                # Skip treatment/management sub-bullets (start with -)
                if line.startswith('-') and any(kw in line.lower() for kw in
                    ['continue', 'monitor', 'start', 'wean', 'switch', 'given',
                     'improved', 'stable', 'mg', 'iv', 'po', 'bid', 'tid',
                     'daily', 'prn', 'as needed', 'prophylaxis', 'dispo',
                     'supplemental', 'oxygen', 'fluid', 'echo', 'lasix',
                     'trelegy', 'ipratropium', 'eliquis', 'ambien', 'xanax']):
                    continue
                # Clean up leading bullets/dashes/numbers
                cleaned = re.sub(r'^[\s\-\*\d\.]+', '', line).strip()
                if cleaned and len(cleaned) > 3:
                    documented.append(cleaned)

    # Also extract #Problem formatted lines (common in Stanford EMR notes)
    hash_problems = re.findall(r'^#\s*(.+)', text, re.MULTILINE)
    for prob in hash_problems:
        cleaned = prob.strip()
        if cleaned and len(cleaned) > 3:
            documented.append(cleaned)

    return documented


def filter_already_documented(predictions: List[Dict], documented_diagnoses: List[str]) -> List[Dict]:
    """
    Filter out LLM predictions that match already-documented diagnoses.
    Keeps predictions that represent specificity upgrades.
    """
    if not documented_diagnoses:
        return predictions

    doc_lower = [d.lower() for d in documented_diagnoses]

    filtered = []
    for pred in predictions:
        dx = pred.get('diagnosis', '').lower()
        if not dx:
            continue

        # Check if this diagnosis is already documented
        is_documented = False
        for doc_dx in doc_lower:
            # Direct substring match (either direction)
            if dx in doc_dx or doc_dx in dx:
                is_documented = True
                break
            # Key clinical term overlap
            dx_terms = set(re.sub(r'[^\w\s]', '', dx).split()) - {
                'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to',
                'of', 'in', 'on', 'acute', 'chronic', 'unspecified', 'secondary'}
            doc_terms = set(re.sub(r'[^\w\s]', '', doc_dx).split()) - {
                'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to',
                'of', 'in', 'on', 'acute', 'chronic', 'unspecified', 'secondary'}
            if dx_terms and doc_terms:
                overlap = len(dx_terms & doc_terms) / max(len(dx_terms), len(doc_terms))
                if overlap >= 0.5:
                    is_documented = True
                    break

        if not is_documented:
            filtered.append(pred)
        elif 'specificity' in pred.get('query_reasoning', '').lower() or \
             'upgrade' in pred.get('query_reasoning', '').lower():
            # Keep specificity upgrades even if base condition is documented
            filtered.append(pred)

    return filtered


def predict_missed_diagnoses(discharge_summary: str, api_key: str, model: str = "gpt-4.1",
                             filter_documented: bool = True,
                             progress_note: str = None) -> Dict:
    """
    Predict diagnoses that CDI specialists would query about.

    Identifies clinically supported diagnoses that are missing or unclear
    in physician documentation.

    Args:
        filter_documented: If True, post-filter predictions that match
            already-documented diagnoses in the discharge summary.
        progress_note: Optional latest progress note to provide additional
            clinical context (labs, vitals, assessments not in discharge summary).
    """

    progress_note_section = ""
    if progress_note:
        progress_note_section = (
            "\nLATEST PROGRESS NOTE (additional clinical context — labs, vitals, assessments):\n"
            + progress_note + "\n"
        )

    prompt = f"""You are a Clinical Documentation Integrity (CDI) specialist at Stanford Healthcare reviewing a discharge summary.

YOUR ROLE: Identify diagnoses that are clinically supported by evidence in the note but are MISSING or UNCLEAR in the physician's documentation. Use the SPECIFIC CLINICAL CRITERIA below - but remember discharge notes are messy, so use clinical judgment alongside the rules.

**CRITICAL — DO NOT FLAG ALREADY-DOCUMENTED CONDITIONS:**
Before suggesting ANY diagnosis, check the Discharge Diagnoses, Problem List, and Assessment sections of the note. If a condition is ALREADY LISTED there (even with slightly different wording), DO NOT include it in your output. CDI specialists will already see these. Only flag diagnoses that are:
1. NOT listed in any diagnosis/problem list section but have clinical evidence in the note body (labs, vitals, treatments)
2. Listed but with INSUFFICIENT SPECIFICITY that would change the ICD-10 code (e.g., "heart failure" documented but evidence supports "acute diastolic heart failure" — flag the specificity upgrade ONLY)

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

**TOP 10 MOST COMMON QUERIES:**

1. **ELECTROLYTE ABNORMALITIES** (#1 PRIORITY)

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

2. **ANEMIA**

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

3. **MALNUTRITION**
   - Criteria: BMI ≤18.5 OR documented weight loss OR Albumin <3.0-3.5 OR temporal wasting/muscle loss
   - **Underweight**: BMI ≤18.5 kg/m²
   - **Severe protein-calorie malnutrition**: BMI <18.5 + Albumin <3.0 + weight loss
   - **Continued malnutrition**: Ongoing malnutrition requiring additional time/resources
   - Statement: "Diagnosis [malnutrition type], BMI [value], increasing health risks, requiring additional time, resources, and/or education"
   - Look for: BMI, albumin, weight loss, nutritional support, dietician involvement

4. **HYPOALBUMINEMIA**
   - Criteria: Albumin <3.2 g/dL on at least TWO panels
   - Normal: 3.5-5.0 g/dL
   - Statement: "Hypoalbuminemia evidenced by minimum Albumin of [value] g/dL, requiring ongoing monitoring and/or treatment"
   - Pattern: Lab value present, diagnosis absent (often separate from malnutrition query)

5. **SEPSIS** (HIGH PRIORITY - Often Missed!)
   **CRITICAL**: CDI specialists query for sepsis based on CLINICAL PATTERN RECOGNITION, not just SIRS criteria.

   **Classic Criteria (SIRS + Infection):**
   - SIRS: 2+ of: Temp >38°C/<36°C, HR >90, RR >20, WBC >12k/<4k
   - PLUS suspected or documented infection

   **TREATMENT PATTERNS THAT STRONGLY SUGGEST SEPSIS (even if not explicitly stated):**
   - Blood cultures ordered AND broad-spectrum IV antibiotics started
   - "Sepsis protocol" initiated or mentioned
   - Lactate ordered (especially if elevated >2 mmol/L)
   - Aggressive IV fluid resuscitation (>30 mL/kg or "fluid bolus")
   - Antibiotics started urgently for suspected infection
   - Patient transferred to ICU for "infection" or "pneumonia" management

   **COMMON PATTERNS WHERE SEPSIS SHOULD BE QUERIED:**
   - UTI + altered mental status + tachycardia → Query Sepsis (urosepsis)
   - Pneumonia + hypoxia + IV antibiotics → Query Sepsis
   - Cellulitis/wound infection + systemic symptoms → Query Sepsis
   - Post-operative fever + infection signs + antibiotics → Query Sepsis
   - Any infection + organ dysfunction (AKI, confusion, hypotension) → Query Sepsis

   **SEVERITY ESCALATION:**
   - Sepsis + organ dysfunction or hypotension → Severe sepsis
   - Sepsis + vasopressors required → Septic shock

   **SEPSIS MUST BE QUERIED WHEN:**
   - Any documented infection with systemic response
   - Terms like "possible sepsis", "rule out sepsis", "sepsis workup"
   - Blood cultures + broad-spectrum antibiotics + fluid resuscitation
   - ICU admission for infection management

   **Statement Template:**
   "Sepsis evidenced by [infection source] with systemic response including [specific findings: tachycardia, fever, elevated WBC, etc.], treated with [antibiotics/fluids]. Consider severity: [sepsis vs severe sepsis vs septic shock]"

6. **PATHOLOGY RESULTS** (HIGH PRIORITY - Often in "Other" Category!)
   **CRITICAL**: CDI specialists query when pathology findings are NOT in discharge diagnoses.

   **Types of Pathology Queries:**
   - Malignant findings: "Adenocarcinoma confirmed" → must be in discharge dx
   - Metastatic disease: "Metastases to [organ]" → specific location matters
   - Specific tumor types: "High-grade neuroendocrine carcinoma" → exact pathology terminology
   - Pleural effusions: "Malignant pleural effusion" vs "pleural effusion"

   **Pattern Recognition:**
   - "Pathology shows..." or "Path report demonstrates..." → Query if not in dx list
   - "Cytology positive for..." → Query the specific finding
   - "Biopsy confirmed..." → Query if diagnosis not explicit
   - Surgical pathology mentions → Ensure captured in discharge diagnoses

   **Common Missed Pathology Queries:**
   - "Malignant pleural effusion, confirmed, as noted in cytology report"
   - "Adenocarcinoma with lymph node metastases, confirmed, as noted in pathology"
   - "High-grade [tumor type], perineural invasion, confirmed in pathology"

   **Statement Template:**
   "[Pathology finding], confirmed, as noted in [pathology/cytology/biopsy] report dated [date if available]"

6b. **DEBRIDEMENT SPECIFICITY** (Commonly Queried!)
    **CRITICAL**: CDI queries for SPECIFIC debridement depth/type.

    **Types (in order of reimbursement value):**
    - Excisional debridement to bone (highest value)
    - Excisional debridement to muscle
    - Excisional debridement to subcutaneous tissue
    - Non-excisional debridement

    **Pattern Recognition:**
    - "Debridement performed" without depth → Query for specificity
    - Wound care notes mentioning sharp debridement → Query type
    - Operative notes with debridement → Query depth (bone/muscle/subQ)

    **Statement Template:**
    "[Excisional/Non-excisional] Debridement to [bone/muscle/subcutaneous tissue], [location], [date]"

7. **RESPIRATORY FAILURE**:
   - Criteria: Oxygen requirement + (PaO2 <60 mmHg OR O2 sat <90% OR PaCO2 >45 mmHg)
   - Pattern: "Respiratory distress" documented but not "acute respiratory failure"
   - Look for: Oxygen requirement, mechanical ventilation, hypoxia, hypercapnia

7b. **PULMONARY EDEMA**:
    - **CARDIOGENIC** (due to heart failure): Fluid overload from cardiac dysfunction
      * Criteria: Pulmonary edema + heart failure + increased JVP/edema + BNP elevated
      * Statement: "Acute Pulmonary Edema, Cardiogenic, due to Heart Failure"
    - **NON-CARDIOGENIC** (ARDS, volume overload without HF): Not from heart
      * Criteria: Pulmonary edema WITHOUT heart failure as primary cause
      * Common causes: ARDS, sepsis, fluid overload post-op, transfusion-related (TRALI)
      * Statement: "Acute Pulmonary Edema, Non-Cardiogenic, due to [cause]"
    - Pattern: "Pulmonary edema" or "flash pulmonary edema" mentioned but etiology not specified
    - Look for: Chest X-ray findings, oxygen requirement, lasix use, heart failure vs other causes

8. **PRESSURE ULCER**:
   - Criteria: Must specify Stage (1-4, unstageable, deep tissue injury) + Location + POA status
   - POA (Present on Admission) status CRITICAL for reimbursement
   - Pattern: Nursing notes it but physician doesn't code it
   - Look for: Wound descriptions, staging, location (sacral, heel, ischial)

9. **COAGULATION DISORDERS**:

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

10. **HEART FAILURE** (HIGH PRIORITY - Specificity Required!)
    **CRITICAL**: CDI queries require SPECIFICITY on both ACUITY and TYPE:

    **Acuity (REQUIRED):**
    - Acute: New onset heart failure
    - Chronic: Stable, ongoing heart failure
    - Acute on Chronic: Exacerbation/decompensation of chronic HF (MOST COMMON)

    **Type (REQUIRED):**
    - Systolic (HFrEF): EF <40%
    - Diastolic (HFpEF): EF ≥50%
    - Combined: Both systolic and diastolic dysfunction

    **Pattern Recognition:**
    - "CHF exacerbation" → Query for "Acute on Chronic [type] Heart Failure"
    - BNP elevated + diuretics + edema → Query heart failure if not documented
    - Volume overload + cardiac history → Query heart failure
    - Echo showing EF% → Use to specify systolic vs diastolic

    **Statement Template:**
    "Acute on Chronic [Systolic/Diastolic] Heart Failure, with EF of [value]%, evidenced by [symptoms/BNP/edema], requiring [treatment]"

10b. **CARDIOGENIC SHOCK** (HIGH VALUE - Often Missed!)
     - Criteria: Hypotension (SBP <90) + signs of hypoperfusion + cardiac cause
     - Requires vasopressors or inotropes for cardiac failure
     - Pattern: "Shock" documented but etiology not specified as cardiogenic
     - Look for: Hypotension + low cardiac output + vasopressors + cardiac cause
     - Statement: "Cardiogenic Shock due to [cause: acute MI, decompensated HF, etc.]"

10c. **TYPE 2 MYOCARDIAL INFARCTION / DEMAND ISCHEMIA**:
     **CRITICAL DISTINCTION:**
     - "Demand Ischemia without MI": Troponin elevation from supply-demand mismatch, NOT meeting MI criteria
     - "Type 2 MI (NSTEMI)": Troponin elevation + ischemic symptoms + ECG changes → IS an MI

     **When to Query Type 2 MI:**
     - Troponin elevated with clear stressor (sepsis, hypotension, tachycardia, anemia, hypoxia)
     - WITHOUT acute coronary syndrome (no PCI, no coronary intervention)
     - **HIGH VALUE**: Type 2 MI (I21.A1) is more specific than "demand ischemia"

     **When to Query "Demand Ischemia without MI":**
     - Troponin mildly elevated (small delta)
     - Stressor present but explicitly stated "not consistent with MI"
     - CDI may query to clarify: "Is this Type 2 MI or demand ischemia without MI?"

     **Statement Templates:**
     - "Type 2 Non-ST Elevation Myocardial Infarction (NSTEMI) due to demand ischemia from [cause]"
     - "Demand ischemia without myocardial infarction, evidenced by [troponin/stressor]"

**ADDITIONAL HIGH-VALUE DIAGNOSES (from .rccautoprognote):**

11. **ACUTE KIDNEY INJURY/FAILURE**:
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

18. **ENCEPHALOPATHY**:
    - **Delirium due to known physiological condition**
    - **Metabolic encephalopathy** (metabolic, toxic, hepatic, septic)
    - Criteria: Altered mental status + metabolic cause
    - Pattern: AMS noted but not formally diagnosed as encephalopathy
    - Look for: Confusion, altered mental status, metabolic derangements

19. **TYPE 2 MI (NSTEMI)**:
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
{progress_note_section}
YOUR TASK:
1. First, READ the Discharge Diagnoses / Problem List sections and note what is ALREADY documented
2. Then, identify diagnoses with clinical evidence that are MISSING from that list or need specificity upgrades
3. Focus on HIGH-VALUE diagnoses (those listed above)
4. Provide specific clinical evidence from the note

If you find conditions that ARE documented but could be MORE SPECIFIC (e.g., "anemia" is listed but evidence supports "acute blood loss anemia"), flag the specificity upgrade only.

**CATEGORY-SPECIFIC CHECKS** (always verify these even if not immediately obvious):
- MALNUTRITION: Check albumin (<3.5), BMI (<18.5 or >30 with weight loss), dietitian consults, TPN/tube feeds, "poor PO intake", weight loss mentions. This is the #2 most queried category.
- ELECTROLYTES: Scan for ANY abnormal sodium, potassium, calcium, magnesium, phosphorus with treatment.
- SEPSIS: Any infection + SIRS criteria (temp, HR, WBC, lactate) + antibiotics = possible sepsis query.
- HEART FAILURE: BNP >400, diuretics, edema — check if specificity (systolic vs diastolic, acute vs chronic) is documented.
- PRESSURE INJURIES: Any wound care, wound consults, Braden score — check stage/location/POA specificity.

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
- ONLY flag diagnoses that are MISSING from the documented diagnosis list or need a specificity upgrade
- DO NOT flag conditions already listed in Discharge Diagnoses/Problem List — even if you can find clinical evidence for them, the physician already documented them
- Prioritize the top 8 categories above (they represent 60% of all queries)
- Be specific about evidence (cite actual values from the note)
- Quality over quantity: 2-3 high-confidence, truly undocumented findings are better than 8 that include already-documented conditions
"""

    response = call_stanford_llm(prompt, api_key, model)

    # DEBUG: Log raw response for GPT-5 investigation
    if model.startswith("gpt-5"):
        import os
        debug_log = os.environ.get('CDI_DEBUG_LOG')
        if debug_log:
            with open(debug_log, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Model: {model}\n")
                f.write(f"Prompt length: {len(prompt)} chars\n")
                f.write(f"Response length: {len(response)} chars\n")
                f.write(f"Response preview (first 2000 chars):\n{response[:2000]}\n")
                f.write(f"Response end (last 500 chars):\n{response[-500:]}\n")

    try:
        # Try direct JSON parse first
        result = json.loads(response)
    except json.JSONDecodeError as parse_error:
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
                    "error": f"JSON parse failed (markdown extract) - {str(parse_error)}",
                    "raw_response": response[:2000],
                    "response_length": len(response)
                }
        else:
            # No markdown code block, try to find JSON object
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group(0))
                except json.JSONDecodeError as inner_error:
                    result = {
                        "missed_diagnoses": [],
                        "error": f"JSON parse failed (regex extract) - {str(inner_error)}",
                        "raw_response": response[:2000],
                        "response_length": len(response)
                    }
            else:
                result = {
                    "missed_diagnoses": [],
                    "error": f"No JSON found in response - response starts with: {response[:200]}",
                    "raw_response": response[:2000],
                    "response_length": len(response)
                }

    # Post-processing: filter out predictions matching already-documented diagnoses
    if filter_documented and 'missed_diagnoses' in result:
        original_count = len(result['missed_diagnoses'])
        documented = extract_documented_diagnoses(discharge_summary)
        if documented:
            result['missed_diagnoses'] = filter_already_documented(
                result['missed_diagnoses'], documented)
            result['_documented_diagnoses_found'] = documented
            result['_filtered_count'] = original_count - len(result['missed_diagnoses'])

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
