#!/usr/bin/env python3
"""
Robust Hill-Climbing Evaluation Runner for CDI Predictor
=========================================================
Self-contained script that actually applies prompt modifications and evaluates
them against ground truth. Designed to run for hours with full retry logic.

Key differences from hill_climb_eval.py:
  - Actually applies prompt modifications to the LLM call (not just tracks config)
  - Robust retry logic inherited from v1 predictor (5 retries, exponential backoff)
  - Per-case error recovery — API failures skip case, don't crash iteration
  - Checkpoint/resume after crashes
  - Logs every case result for debugging
  - Runs until convergence (no improvement for N consecutive iterations)

Usage:
    python scripts/run_hill_climb.py --api-key YOUR_KEY
"""

import os
import sys
import json
import re
import time
import random
import traceback
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from pathlib import Path

# ===========================================================================
# STANFORD API CALLER (with robust retry)
# ===========================================================================

API_ENDPOINTS = {
    "gpt-5": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
    "gpt-4.1": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview",
    "gpt-5-nano": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5-nano/chat/completions?api-version=2024-12-01-preview",
}

def call_llm(messages, api_key, model="gpt-5", temperature=0.2, max_tokens=16000):
    """Call Stanford LLM with robust retry logic.
    Matches the exact format used in cdi_llm_predictor.py which is known to work.
    """
    url = API_ENDPOINTS.get(model, API_ENDPOINTS["gpt-5"])
    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": api_key,
    }

    # GPT-5 has specific API requirements — no custom temperature, uses max_completion_tokens
    # Match the working cdi_llm_predictor.py: ALL gpt-5 variants (including nano) use same format
    is_gpt5 = model.startswith("gpt-5")
    request_body = {
        "model": model,
        "messages": messages,
    }
    if is_gpt5:
        # GPT-5 only supports temperature=1 (default), uses reasoning tokens
        request_body["max_completion_tokens"] = 16000  # ~12k reasoning + 4k output
    else:
        request_body["temperature"] = temperature
        request_body["max_tokens"] = 4000

    payload = json.dumps(request_body)

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=180)

            if response.status_code == 429 or response.status_code >= 500:
                wait = 2 ** (attempt + 1) + random.random() * 2
                print(f"    API {response.status_code}, retrying in {wait:.0f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            if response.status_code != 200:
                print(f"    API {response.status_code}: {response.text[:300]}")
                # 400-level errors (except 429) are permanent — don't retry
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise RuntimeError(f"API {response.status_code}: {response.text[:200]}")
                # Other errors — retry
                wait = 2 ** (attempt + 1)
                if attempt < max_retries - 1:
                    print(f"    Retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                else:
                    raise RuntimeError(f"API {response.status_code}: {response.text[:200]}")

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # Handle empty GPT-5 responses (reasoning consumed all tokens)
            if content is None or content == "":
                finish_reason = data["choices"][0].get("finish_reason", "unknown")
                if finish_reason == "length":
                    raise RuntimeError("GPT-5 response truncated (finish_reason=length) — reasoning consumed all tokens")
                elif finish_reason == "content_filter":
                    raise RuntimeError("GPT-5 response blocked by content filter")
                return ""

            time.sleep(0.5)  # Rate limit buffer
            return content

        except requests.exceptions.Timeout:
            wait = 2 ** (attempt + 1) + random.random() * 2
            print(f"    Timeout, retrying in {wait:.0f}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
        except requests.exceptions.ConnectionError as e:
            wait = 2 ** (attempt + 1) + random.random() * 2
            print(f"    Connection error, retrying in {wait:.0f}s: {e}")
            time.sleep(wait)
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"    Request error, retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"API call failed after {max_retries} retries")


# ===========================================================================
# PROMPT VARIANTS — These are the actual modifications we hill-climb over
# ===========================================================================

# Baseline prompt (v1 style — monolithic)
PROMPT_BASELINE = """You are a Clinical Documentation Integrity (CDI) specialist at Stanford Healthcare. Review the discharge summary and identify diagnoses that are MISSING or UNDER-SPECIFIED.

For each missed diagnosis, provide:
- diagnosis: specific name
- icd10_code: most specific ICD-10 code
- category: one of [sepsis, respiratory, anemia, malnutrition, electrolytes, cardiac, renal, coagulation, pressure_ulcer, other]
- confidence: high/medium/low
- evidence: clinical data supporting this

KEY CRITERIA:
- Sepsis: Blood cultures + IV antibiotics + fluid resuscitation + SIRS criteria
- Malnutrition: Albumin <3.5, BMI <18.5, poor intake, nutrition consult
- Electrolytes: Na <130 (hyponatremia), K >5.5 (hyperkalemia), Mg <1.7 (hypomagnesemia)
- Anemia: Hgb <8 needs specificity (CKD, iron deficiency, blood loss)
- Heart failure: Needs acuity + type (systolic/diastolic)
- Respiratory failure: PaO2 <60 or SpO2 <88% on RA
- AKI: KDIGO staging with baseline Cr comparison

DO NOT flag conditions already in the discharge diagnosis list.
Return a JSON array of objects. Return ONLY the JSON array."""


# V2 prompt — detailed criteria with lab thresholds
PROMPT_V2_DETAILED = """You are an expert Clinical Documentation Integrity (CDI) specialist at an academic medical centre. Your job is to identify diagnoses physicians missed or under-specified in discharge documentation.

CRITICAL RULES:
1. Do NOT flag conditions already listed in Discharge Diagnoses, Problem List, or Assessment
2. Only flag diagnoses with CLEAR clinical evidence (labs, vitals, treatments, imaging)
3. Suggest the HIGHEST SPECIFICITY ICD-10 code supported by evidence

CDI CRITERIA (apply precisely):

SEPSIS (TOP PRIORITY):
- Blood cultures obtained AND broad-spectrum IV antibiotics AND (lactate >2.0 OR fluid resuscitation >30ml/kg OR ICU transfer)
- Organ dysfunction present: AKI (Cr rise >0.3), altered mental status, hypotension (SBP <90), respiratory failure
- Specify: organism if known, source (urosepsis, pneumonia, etc.)
- "Sepsis, unspecified" → upgrade to "severe sepsis with organ dysfunction" (R65.20) if organ dysfunction present
- Sepsis + vasopressors → septic shock (R65.21)

SEVERE MALNUTRITION (E43, MCC):
- Albumin <3.0 g/dL AND (BMI <18.5 OR documented weight loss OR poor PO intake)
- Nutrition consult obtained strengthens case
- Check: albumin trend, BMI, weight, diet orders, TPN/tube feeds

ELECTROLYTE ABNORMALITIES:
- Hyponatremia: Na <130 + treatment (fluid restriction, IV NS) → E87.1
- Hyperkalemia: K >5.5 + treatment (Kayexalate, calcium gluconate, EKG changes) → E87.5
- Hypomagnesemia: Mg <1.7 + IV/PO magnesium replacement → E83.42
- Hypocalcemia: Ca <8.4 + IV calcium (exclude low albumin) → E83.51
- Hypophosphatemia: Phos <2.5 + replacement → E83.39

ANEMIA (SPECIFICITY):
- "Anemia" alone is insufficient. Must specify:
  - Acute blood loss anemia (D62): Hgb drop >2 points + surgical/bleeding source
  - Iron deficiency anemia (D50.9): Low Hgb + iron supplementation started
  - Anemia of chronic disease (D63.1): CKD + chronic low Hgb
- Hgb <7 without transfusion documentation = potential query

HEART FAILURE (SPECIFICITY):
- Must have: Acuity (acute/chronic/acute-on-chronic) + Type (systolic EF<40% / diastolic EF>=50%)
- "CHF" or "heart failure" without specificity → query
- Check: BNP, echo EF, diuretic use, edema

RESPIRATORY FAILURE:
- PaO2 <60 OR SpO2 <88% on room air OR PaCO2 >50
- Specify: acute vs chronic, hypoxic (J96.01) vs hypercapnic (J96.21)
- High-flow O2, BiPAP, intubation without resp failure diagnosis → query

AKI (STAGING):
- Cr rise >0.3 from baseline OR >1.5x baseline
- KDIGO Stage 1: 1.5-1.9x baseline. Stage 2: 2-2.9x. Stage 3: 3x or Cr>4.0 or dialysis

LACTIC ACIDOSIS:
- Lactate >2.0 mmol/L with treatment (IV fluids, possible bicarb) → E87.2

DIABETES SPECIFICITY:
- Glucose >180 + insulin → Type 2 DM with hyperglycemia (E11.65)
- DM + CKD → link as E11.22 (DM with diabetic CKD)

COAGULATION:
- Thrombocytopenia: Plt <145 on 2+ panels → D69.6
- Pancytopenia: Low Hgb + WBC <4.0 + Plt <150 → D61.818

ENCEPHALOPATHY:
- Altered mental status + metabolic cause (hepatic, septic, toxic, metabolic) → specify type

For each finding, return:
{
  "diagnosis": "specific name",
  "icd10_code": "most specific code",
  "category": "sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|other",
  "confidence": "high|medium|low",
  "evidence": "specific clinical data"
}

Return a JSON array. Return ONLY the JSON array, no other text."""


# V3 prompt — two-pass approach with structured extraction first
PROMPT_V3_EXTRACTION = """STEP 1: Extract all structured clinical data from this discharge summary.

List EVERY abnormal lab value, vital sign, treatment, and documented diagnosis.
Format as:
DOCUMENTED DIAGNOSES: [list all]
ABNORMAL LABS: [name: value (normal range)]
ABNORMAL VITALS: [parameter: value]
TREATMENTS: [medication/procedure: indication]
CULTURES: [type: result]

STEP 2: For each abnormal finding, check if the corresponding diagnosis appears in the documented diagnoses list. If NOT documented, flag it.

Apply these thresholds:
- Na <130 → hyponatremia (E87.1)
- K >5.5 with treatment → hyperkalemia (E87.5)
- Mg <1.7 with IV replacement → hypomagnesemia (E83.42)
- Hgb <8 → specify type of anemia (D62, D50.9, D63.1)
- Albumin <3.0 + BMI <18.5 → severe malnutrition (E43, MCC)
- Lactate >2.0 + treatment → lactic acidosis (E87.2)
- Glucose >180 + insulin → DM with hyperglycemia (E11.65)
- Blood cultures + IV abx + SIRS + organ dysfunction → severe sepsis (R65.20)
- Cr rise >0.3 from baseline → AKI with KDIGO staging
- Plt <145 on 2+ panels → thrombocytopenia (D69.6)

CRITICAL: Do NOT flag conditions already in the documented diagnoses.
Only flag conditions where clinical evidence exists but the diagnosis is missing or under-specified.

Return ONLY a JSON array of missed diagnoses:
[{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]"""


# V4 prompt — v2 detailed + few-shot examples
PROMPT_V4_FEWSHOT = PROMPT_V2_DETAILED + """

EXAMPLES OF CORRECT CDI QUERIES:

Example 1 - Sepsis specificity:
Documented: "Sepsis, unspecified" | Labs: Lactate 4.2, Cr 3.1 (baseline 1.0), WBC 18.5, Blood cx: E. coli | Treatment: Ceftriaxone IV, 3L NS bolus, ICU transfer
→ {"diagnosis": "Severe sepsis with organ dysfunction, E. coli", "icd10_code": "R65.20", "category": "sepsis", "confidence": "high", "evidence": "Lactate 4.2, AKI (Cr 1.0→3.1), blood cx E. coli, ICU transfer, IV abx + fluid resuscitation"}

Example 2 - Malnutrition missed entirely:
Documented: None about malnutrition | Labs: Albumin 2.1 | Vitals: BMI 17.8 | Notes: "poor oral intake x 3 weeks", nutrition consult obtained
→ {"diagnosis": "Severe protein-calorie malnutrition", "icd10_code": "E43", "category": "malnutrition", "confidence": "high", "evidence": "Albumin 2.1 (<3.0), BMI 17.8 (<18.5), poor intake documented, nutrition consult"}

Example 3 - Electrolyte not captured:
Documented: None about sodium | Labs: Na 124 on admission, Na 128 on day 3 | Treatment: Fluid restriction, IV NS
→ {"diagnosis": "Severe hyponatremia", "icd10_code": "E87.1", "category": "electrolytes", "confidence": "high", "evidence": "Na 124 (<130 threshold), treated with fluid restriction + IV NS"}

Now analyse the following discharge summary:"""


# V5 prompt — system/user message split with verification
PROMPT_V5_SYSTEM = """You are a board-certified Clinical Documentation Integrity (CDI) specialist with 15 years of experience at an academic medical centre. You review every discharge summary with three objectives:

1. IDENTIFY conditions with clinical evidence that are NOT documented as diagnoses
2. IDENTIFY conditions that are documented but LACK SPECIFICITY (e.g., "anemia" without type, "sepsis" without organism)
3. PRIORITISE by DRG/reimbursement impact: MCCs first, then CCs, then other

Your CDI accuracy directly impacts hospital reimbursement. Be thorough but precise — false positives waste CDI specialist time. Every finding must have traceable clinical evidence.

ALWAYS check the Discharge Diagnosis list FIRST. Never flag something already documented."""

PROMPT_V5_USER_PREFIX = """Review this discharge summary for missed or under-specified diagnoses.

CRITICAL THRESHOLDS:
Sepsis: cultures + IV abx + (lactate>2 OR fluid resus OR ICU) + organ dysfunction → R65.20
Malnutrition: albumin<3.0 + (BMI<18.5 OR weight loss OR poor intake) → E43 (MCC)
Hyponatremia: Na<130 + treatment → E87.1
Hyperkalemia: K>5.5 + treatment → E87.5
Hypomagnesemia: Mg<1.7 + IV/PO Mg → E83.42
Anemia specificity: Hgb<8 → D62 (blood loss), D50.9 (iron def), D63.1 (CKD)
Heart failure: must specify acuity + type
Respiratory failure: PaO2<60 or SpO2<88% RA → J96.01/J96.21
AKI: Cr>0.3 rise → KDIGO staging
Lactic acidosis: lactate>2.0 + treatment → E87.2
DM specificity: glucose>180 + insulin → E11.65

Return ONLY a JSON array of findings. Each: {"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|other","confidence":"high|medium|low","evidence":"..."}

DISCHARGE SUMMARY:
"""


# All prompt variants to hill-climb over
PROMPT_VARIANTS = {
    "v1_baseline": {
        "system": None,
        "user_prefix": PROMPT_BASELINE + "\n\nDISCHARGE SUMMARY:\n",
        "temperature": 0.1,
        "description": "V1 baseline monolithic prompt",
    },
    "v2_detailed": {
        "system": None,
        "user_prefix": PROMPT_V2_DETAILED + "\n\nDISCHARGE SUMMARY:\n",
        "temperature": 0.2,
        "description": "V2 detailed criteria with lab thresholds",
    },
    "v3_extraction": {
        "system": None,
        "user_prefix": PROMPT_V3_EXTRACTION + "\n\nDISCHARGE SUMMARY:\n",
        "temperature": 0.1,
        "description": "V3 two-pass structured extraction approach",
    },
    "v4_fewshot": {
        "system": None,
        "user_prefix": PROMPT_V4_FEWSHOT + "\n\n",
        "temperature": 0.2,
        "description": "V4 detailed + few-shot examples",
    },
    "v5_system_split": {
        "system": PROMPT_V5_SYSTEM,
        "user_prefix": PROMPT_V5_USER_PREFIX,
        "temperature": 0.2,
        "description": "V5 system/user split with CDI expert persona",
    },
    "v6_checklist": {
        "system": None,
        "user_prefix": PROMPT_V2_DETAILED + """\n\nBefore returning your answer, mentally run through this checklist:
1. Did I check the discharge diagnosis list and avoid flagging anything already documented?
2. Did I check ALL lab values for abnormalities (Na, K, Mg, Ca, Phos, Albumin, Hgb, Plt, Cr, Lactate, Glucose)?
3. Did I check for sepsis patterns (cultures + IV abx + SIRS + organ dysfunction)?
4. Did I use the MOST SPECIFIC ICD-10 code (not unspecified)?
5. Does every finding have traceable clinical evidence?

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V2 detailed + self-verification checklist",
    },
    "v7_mcc_focus": {
        "system": """You are a CDI specialist focused on identifying HIGH-VALUE missed diagnoses that impact DRG reimbursement. Prioritise MCCs (Major Complications/Comorbidities) over CCs, and CCs over non-CC diagnoses.

MCC examples: Sepsis/severe sepsis (A41.x, R65.20, R65.21), acute respiratory failure (J96.0x), severe malnutrition (E43), pressure ulcers stage 3-4, acute MI.
CC examples: AKI (N17.9), CKD stage 3+ (N18.3-6), anaemia in chronic disease (D63.x), COPD exacerbation (J44.1), CHF (I50.x).

Only flag diagnoses NOT already in the discharge diagnosis list. Every finding needs traceable lab/clinical evidence.""",
        "user_prefix": """Analyse this discharge summary. Return the top missed diagnoses prioritised by DRG impact (MCCs first).

For each: {"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|other","confidence":"high|medium|low","evidence":"..."}

Return ONLY a JSON array.

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V7 MCC/CC-focused with DRG impact prioritisation",
    },
    "v8_cot": {
        "system": None,
        "user_prefix": """You are a Clinical Documentation Integrity specialist. Analyse this discharge summary step by step.

STEP 1: List all documented diagnoses from the Discharge Diagnosis section.
STEP 2: List ALL abnormal lab values with their values and normal ranges.
STEP 3: List all treatments/medications and their apparent indications.
STEP 4: For each abnormal lab or treatment WITHOUT a matching documented diagnosis, determine if a CDI query is warranted using these thresholds:
- Sepsis: cultures + IV abx + (lactate>2 OR fluid resus OR ICU) + organ dysfunction → R65.20
- Malnutrition: albumin<3.0 + (BMI<18.5 OR weight loss OR poor intake) → E43
- Hyponatremia: Na<130 + treatment → E87.1 | Hyperkalemia: K>5.5 + treatment → E87.5
- Hypomagnesemia: Mg<1.7 + replacement → E83.42 | Hypocalcemia: Ca<8.4 + IV Ca → E83.51
- Anemia: Hgb<8, specify type (D62 blood loss, D50.9 iron def, D63.1 chronic disease)
- AKI: Cr rise >0.3 from baseline → KDIGO staging
- Respiratory failure: PaO2<60 or SpO2<88% RA → J96.01/J96.21
- Lactic acidosis: lactate>2.0 + treatment → E87.2
- DM specificity: glucose>180 + insulin → E11.65
STEP 5: Return ONLY the JSON array of missed diagnoses (no other text):
[{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|other","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V8 explicit chain-of-thought with step-by-step extraction",
    },

    # ======================================================================
    # ROUND 2 VARIANTS — informed by error analysis of 9+ eval runs
    # Key findings driving these:
    #   - Hypoalbuminemia is #1 FP (129 cases) — model says "hypoalbuminemia" not "malnutrition"
    #   - Malnutrition is most-missed category (35+22 cases)
    #   - "Other" category = 30.7% of ground truth, poorly handled
    #   - Negation not handled ("sepsis, ruled out" counted as positive)
    #   - Model over-predicts (low precision ~5-8%), most predictions are noise
    #   - Progress notes + H&P + consult notes now available but prompts don't use them
    # ======================================================================

    "v9_malnutrition_fix": {
        "system": None,
        "user_prefix": PROMPT_V2_DETAILED + """

CRITICAL MAPPING RULES (apply these before returning):
- If albumin <3.0 → ALWAYS report as "Protein calorie malnutrition" (E43 if severe, E44.0 if moderate), NOT as "hypoalbuminemia"
- Hypoalbuminemia alone is NOT a CDI-queryable diagnosis — it must map to malnutrition with clinical context
- BMI <18.5 + poor intake = severe malnutrition (E43, MCC) even without albumin
- "Cachectic" in clinical notes = malnutrition query

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V9 fix #1 FP: map hypoalbuminemia→malnutrition explicitly",
    },

    "v10_precision_focus": {
        "system": """You are a CDI specialist. Your queries will be reviewed by physicians who are busy and annoyed by false positives. Every query must be defensible with specific lab values, vitals, or documented clinical findings. If you are not confident a diagnosis is truly missing, DO NOT include it.

Return at most 5 diagnoses. Quality over quantity.""",
        "user_prefix": """Review this discharge summary. Only flag diagnoses that meet ALL of these criteria:
1. Clear clinical evidence exists (specific lab values, documented findings)
2. The diagnosis is NOT already in the Discharge Diagnosis list, Problem List, or Assessment
3. The diagnosis would change DRG weight (MCC or CC)
4. A CDI specialist would actually query this (not trivial findings)

CRITICAL: Do NOT flag these common false positives:
- Hypoalbuminemia (flag malnutrition instead if criteria met)
- Type 2 DM with hyperglycemia (only if NOT already documented as diabetes)
- Dehydration (only if IV fluids given AND not already documented)
- Generic lab findings without clinical significance

Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|pressure_ulcer|encephalopathy|other","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V10 precision-focused: cap at 5, explicit FP suppression",
    },

    "v11_multi_note": {
        "system": """You are a CDI specialist with access to multiple clinical documents for this encounter. Use ALL available notes to build a complete clinical picture before identifying missed diagnoses.

The discharge summary contains the final diagnoses. The progress notes, H&P, and consultation notes contain additional clinical findings that may reveal diagnoses the discharge summary missed.""",
        "user_prefix": """Review ALL the clinical notes below. Cross-reference findings across notes to identify diagnoses with strong evidence that are NOT captured in the discharge diagnosis list.

APPROACH:
1. Read the Discharge Summary — note all documented diagnoses
2. Read the Progress Notes — look for clinical findings not reflected in discharge diagnoses
3. Read the H&P — check admission labs and initial assessment for undocumented conditions
4. Read the Consultation Notes — specialists often identify conditions the primary team misses

Only flag diagnoses where evidence appears in the clinical notes but the condition is missing from discharge diagnoses.

Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|pressure_ulcer|encephalopathy|other","confidence":"high|medium|low","evidence":"cite which note(s) contain the evidence"}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V11 multi-note: explicitly instructs cross-referencing all 4 note types",
    },

    "v12_negation_aware": {
        "system": None,
        "user_prefix": PROMPT_V2_DETAILED + """

NEGATION HANDLING — Read carefully:
- "Ruled out" = DO NOT flag (e.g., "sepsis ruled out" means no sepsis)
- "Unlikely" or "low suspicion" = DO NOT flag
- "Cannot be determined" = DO NOT flag
- "Suspected" or "possible" = flag ONLY with medium confidence
- "Confirmed" or "consistent with" = flag with high confidence
- Treatment given (antibiotics, fluids) is NOT sufficient alone — need positive clinical criteria

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V12 negation-aware: explicit rules for ruled-out, unlikely, suspected",
    },

    "v13_category_expanded": {
        "system": None,
        "user_prefix": """You are a CDI specialist. Review this discharge summary for missed diagnoses across ALL categories, including less common ones.

COMMON CDI CATEGORIES (check all):
- Sepsis/Severe sepsis: cultures + IV abx + organ dysfunction → R65.20
- Malnutrition: albumin<3.0 + (BMI<18.5 OR weight loss OR poor intake) → E43
- Electrolytes: Na<130, K>5.5, Mg<1.7, Ca<8.4, Phos<2.5 with treatment
- Anemia: Hgb<8, must specify type (blood loss D62, iron def D50.9, chronic D63.1)
- Heart failure: must specify acuity + type (systolic/diastolic)
- Respiratory failure: PaO2<60 or SpO2<88% RA → J96.01/J96.21
- AKI: Cr rise >0.3 from baseline → KDIGO staging
- Coagulation: Plt<145 (thrombocytopenia D69.6), pancytopenia D61.818

LESS COMMON BUT HIGH-VALUE CATEGORIES (often missed):
- Pressure injuries: ANY stage documented by WOCN but not in discharge dx → L89.x (Stage 3-4 = MCC)
- Encephalopathy: altered mental status + metabolic cause → G93.41/K72.x
- Obesity: BMI≥35 documented but not coded → E66.01 (morbid, CC), E66.2 (with hypoventilation, MCC)
- Functional quadriplegia: bedbound, dependent all ADLs → G82.50
- Surgical complications: unplanned return to OR, wound dehiscence, anastomotic leak
- Drug reactions: adverse effects requiring treatment change
- Acute pulmonary edema: bilateral infiltrates + diuretics + BNP elevation → J81.0

DO NOT flag conditions already in discharge diagnoses.
Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V13 expanded categories: targets 'Other' bucket with specific uncommon diagnoses",
    },

    "v14_top3_high_confidence": {
        "system": """You are a senior CDI specialist. You have reviewed thousands of cases. You know that the most impactful CDI queries are the ones with ironclad evidence that physicians cannot dispute.

Your task: identify ONLY the top 3 most defensible missed diagnoses. Each must have:
1. Specific lab values or documented clinical findings as evidence
2. A clear ICD-10 code that would change DRG weight
3. Absolute certainty the diagnosis is NOT already documented""",
        "user_prefix": """Review this case. Return EXACTLY 3 missed diagnoses — your 3 highest-confidence findings.

If you cannot find 3 strong findings, return fewer. Never pad with weak findings.

Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|pressure_ulcer|encephalopathy|other","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V14 top-3 only: max precision, tests if fewer predictions = better F1",
    },

    "v15_cdi_agent_style": {
        "system": """You are a clinical documentation integrity (CDI) assistant. Your role is to identify clinically-supported diagnoses that are present in the patient's record but were not captured in the attending's discharge diagnoses — particularly those that would increase case complexity or DRG weight.

Focus on:
1. MCCs (Major Complications/Comorbidities) — highest reimbursement impact
2. CCs (Complications/Comorbidities) — moderate impact
3. Specificity upgrades — "heart failure" → "acute on chronic diastolic heart failure"

For each finding, provide:
- The specific diagnosis name (use standard clinical terminology)
- The most specific ICD-10-CM code
- Detailed clinical evidence from the notes
- A brief reasoning for why this should be queried""",
        "user_prefix": """Analyse this clinical encounter for missed or under-specified diagnoses. Review all available notes.

IMPORTANT: Map lab findings to diagnoses, not raw lab values:
- Low albumin → query malnutrition (not hypoalbuminemia)
- Low Hgb → query specific anemia type (not just "anemia")
- Elevated Cr → query AKI with KDIGO stage (not just "elevated creatinine")
- Elevated lactate → query lactic acidosis or sepsis (not just "elevated lactate")

Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|pressure_ulcer|encephalopathy|other","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V15 cdi-agent style: adapted from Claude agent's system prompt (best 60% recall)",
    },

    "v16_v2_plus_allnotes": {
        "system": None,
        "user_prefix": PROMPT_V2_DETAILED + """

ADDITIONAL CONTEXT — Use these notes to find evidence the discharge summary may have missed:

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V16 baseline V2 detailed + explicit multi-note header (controls for note effect)",
    },

    "v17_recall_max": {
        "system": """You are a CDI specialist conducting a comprehensive chart review. Your goal is to identify EVERY possible missed or under-specified diagnosis. It is better to flag a questionable finding than to miss a real one — the CDI team will filter your results.

Be thorough. Check every lab value, every vital sign, every medication, every procedure note. If there is ANY clinical evidence suggesting an undocumented condition, flag it.""",
        "user_prefix": """Perform an exhaustive review of all clinical notes. Flag every potential missed diagnosis.

Key thresholds:
- Sepsis: cultures + IV abx + (lactate>2 OR fluid resus OR ICU) + organ dysfunction
- Malnutrition: albumin<3.0 + (BMI<18.5 OR weight loss OR poor intake) — report as malnutrition NOT hypoalbuminemia
- Electrolytes: Na<130, K>5.5, Mg<1.7, Ca<8.4, Phos<2.5 with treatment
- Anemia: Hgb<8, specify type | AKI: Cr rise>0.3 → KDIGO stage
- Heart failure: specify acuity+type | Respiratory failure: PaO2<60 or SpO2<88%
- Pressure injuries: any WOCN-documented stage | Encephalopathy: AMS + metabolic cause
- Obesity: BMI≥35 | Coagulation: Plt<145

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V17 recall-maximiser: exhaustive review, flag everything with evidence",
    },

    # =========================================================================
    # LITERATURE-INFORMED VARIANTS (v18-v23)
    # Based on: arxiv 2509.05378, 2510.04048, 2503.22092, 2509.21933,
    #           JMIR Med Inform 2025, NEJM AI RAG review
    # =========================================================================

    "v18_verify": {
        "predict_method": "two_pass_verify",
        "system_pass1": """You are a CDI specialist. Review this clinical documentation and generate a comprehensive list of ALL potentially missed or under-documented diagnoses. Cast a wide net — include anything with clinical evidence. Better to over-include than miss something.

Return a JSON array: [{"diagnosis":"...","evidence":"brief clinical evidence"}]""",
        "user_prefix_pass1": "Review all notes and list every potentially missed diagnosis:\n\nDISCHARGE SUMMARY:\n",
        "system_pass2": """You are a senior CDI auditor performing verification review. You have been given a list of candidate diagnoses generated by a junior reviewer. Your job is to VERIFY each candidate against the actual clinical documentation.

For each candidate, determine:
- CONFIRMED: Clear clinical evidence supports this diagnosis being missed/under-documented
- REJECTED: Insufficient evidence, already documented, or clinically incorrect

Only keep CONFIRMED diagnoses. Be rigorous but fair — if the evidence is there, confirm it.

Return a JSON array of ONLY confirmed diagnoses: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium","evidence":"..."}]""",
        "user_prefix_pass2": "CANDIDATE DIAGNOSES TO VERIFY:\n{candidates}\n\nNow verify each against the clinical documentation below. Only keep diagnoses with clear evidence.\n\nDISCHARGE SUMMARY:\n",
        "temperature": 0.2,
        "description": "V18 two-pass: generate candidates then verify each against notes (IEEE 2025 verification paradigm)",
    },

    "v19_self_consistency": {
        "predict_method": "self_consistency",
        "num_samples": 3,
        "vote_threshold": 2,
        "system": """You are a Clinical Documentation Integrity (CDI) specialist at Stanford Healthcare. Review the clinical notes and identify diagnoses that are MISSING or UNDER-SPECIFIED in the documentation.

Focus on high-value CDI categories: sepsis, malnutrition, respiratory failure, heart failure, acute kidney injury, anemia, electrolyte disorders, pressure injuries, encephalopathy, obesity, coagulation disorders.

IMPORTANT: If albumin is low, report as "malnutrition" or "protein-calorie malnutrition", NOT "hypoalbuminemia".

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]""",
        "user_prefix": "Identify all missed or under-documented diagnoses:\n\nDISCHARGE SUMMARY:\n",
        "temperature": 0.7,
        "description": "V19 self-consistency: 3 samples at temp=0.7, keep diagnoses appearing in ≥2/3 (arXiv 2503.22092 + 2510.04048)",
    },

    "v20_extract_then_classify": {
        "predict_method": "extract_then_classify",
        "system_extract": """You are a clinical data extractor. Review the clinical notes and extract ALL abnormal clinical findings. Include:
- Abnormal lab values (with specific numbers)
- Abnormal vital signs
- Clinical signs and symptoms
- Medications that suggest undocumented conditions
- Procedures performed
- Consultant recommendations

Be exhaustive. List every abnormal finding, no matter how minor.

Return a JSON array: [{"finding":"...","value":"...","location":"which note"}]""",
        "user_prefix_extract": "Extract all abnormal clinical findings from these notes:\n\nDISCHARGE SUMMARY:\n",
        "system_classify": """You are a CDI specialist. You have been given a list of abnormal clinical findings extracted from a patient's chart. Your job is to determine which findings indicate MISSED or UNDER-DOCUMENTED diagnoses.

Map findings to CDI-relevant diagnoses using these rules:
- Albumin <3.0 + poor intake/weight loss/low BMI → Protein-calorie malnutrition (NOT hypoalbuminemia)
- Cultures + IV antibiotics + organ dysfunction → Sepsis
- PaO2 <60 or SpO2 <88% on supplemental O2 → Respiratory failure (specify type)
- Cr rise ≥0.3 from baseline → Acute kidney injury (specify KDIGO stage)
- Hgb drop + bleeding source → Acute blood loss anemia
- Na <130 → Hyponatremia | K >5.5 → Hyperkalemia
- BMI ≥35 → Obesity (specify class)
- Pressure injury documented by wound care → Pressure ulcer (specify stage)
- AMS + metabolic cause → Encephalopathy (specify type)

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]""",
        "user_prefix_classify": "EXTRACTED ABNORMAL FINDINGS:\n{findings}\n\nBased on these findings, identify all missed or under-documented diagnoses:\n",
        "temperature": 0.2,
        "description": "V20 two-phase: extract abnormal findings first, then map to diagnoses (JMIR 2025 two-phase framework)",
    },

    "v21_multi_agent": {
        "predict_method": "multi_agent",
        "system_evidence": """You are a clinical evidence extractor. Read the clinical documentation and identify all clinical evidence relevant to potential CDI queries. For each piece of evidence, note:
- The specific clinical finding (lab, vital, symptom, medication, procedure)
- Where it appears (discharge summary, progress note, H&P, consult note)
- Whether the condition appears to be explicitly documented as a diagnosis

Return a JSON array: [{"evidence":"...","source":"...","documented_as_diagnosis":true/false}]""",
        "user_prefix_evidence": "Extract all CDI-relevant clinical evidence:\n\nDISCHARGE SUMMARY:\n",
        "system_map": """You are a CDI diagnosis mapper. Given clinical evidence, map each undocumented finding to the most specific diagnosis.

Rules:
- Only map findings where documented_as_diagnosis is false
- Use the most specific diagnosis name possible
- Map hypoalbuminemia → protein-calorie malnutrition if nutritional risk factors present
- Group related findings into single diagnoses (e.g., cultures + abx + organ dysfunction = sepsis)

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","supporting_evidence":["..."]}]""",
        "user_prefix_map": "CLINICAL EVIDENCE:\n{evidence}\n\nMap undocumented findings to specific diagnoses:\n",
        "system_validate": """You are a CDI validation specialist. Review the proposed diagnoses and validate each one:

1. Is there sufficient clinical evidence? (at least 2 supporting findings)
2. Is the diagnosis truly missing from documentation? (not just worded differently)
3. Is the diagnosis clinically appropriate? (not a normal finding being overcoded)
4. Is the specificity correct? (acute vs chronic, type, stage)

Remove any diagnosis that fails validation. For remaining diagnoses, assign a category from: [sepsis, respiratory, anemia, malnutrition, electrolytes, cardiac, renal, coagulation, pressure_ulcer, encephalopathy, obesity, other]

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium","evidence":"..."}]""",
        "user_prefix_validate": "PROPOSED DIAGNOSES:\n{diagnoses}\n\nValidate each diagnosis. Remove any without sufficient evidence:\n",
        "temperature": 0.2,
        "description": "V21 multi-agent: evidence→map→validate pipeline (EMNLP 2025 'Code Like Humans' approach)",
    },

    "v22_rag_exemplars": {
        "predict_method": "standard",
        "system": """You are a CDI specialist at Stanford Healthcare. Below are examples of real CDI queries — diagnoses that CDI specialists identified as missed in discharge summaries. Study these examples to understand what CDI specialists look for, then apply the same analysis to the new case.

EXAMPLE CDI FINDINGS:
Example 1: Patient with pneumonia, IV antibiotics, lactate 2.4, WBC 15 → CDI query: "Sepsis, clinically valid as evidenced by tachycardia, tachypnea, and clinical presentation of pneumonia"
Example 2: Patient post-surgery, Hgb dropped from 11 to 7.2, received 2 units pRBC → CDI query: "Acute blood loss anemia, not present on admission"
Example 3: Patient with albumin 2.1, BMI 17, poor oral intake documented → CDI query: "Severe protein-calorie malnutrition"
Example 4: Patient on 4L NC, ABG showing PaO2 55 → CDI query: "Acute hypoxic respiratory failure"
Example 5: Patient with Cr 1.2→2.8 over 48hrs, started on IV fluids → CDI query: "Acute kidney injury, KDIGO stage 2"
Example 6: Sacral wound documented by wound care as 3cm x 2cm, partial thickness → CDI query: "Stage 2 pressure ulcer to sacrum"
Example 7: Patient confused, ammonia level 85, liver disease → CDI query: "Hepatic encephalopathy"
Example 8: BMI 42 documented in vitals but not in problem list → CDI query: "Morbid obesity, BMI 40-44.9"

Apply this same pattern-matching approach. Identify diagnoses that have clinical evidence but are missing from the formal documentation.

IMPORTANT: Report malnutrition as "protein-calorie malnutrition", NOT "hypoalbuminemia". Report the CLINICAL DIAGNOSIS, not the lab finding.

Return a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"...","confidence":"high|medium|low","evidence":"..."}]""",
        "user_prefix": """Analyse this case using the same approach as the CDI examples above. Identify missed diagnoses:

DISCHARGE SUMMARY:\n""",
        "temperature": 0.2,
        "description": "V22 RAG-style: 8 real CDI query examples in-context to prime pattern recognition (NEJM AI RAG paradigm)",
    },

    "v23_no_cot_direct": {
        "predict_method": "standard",
        "system": """CDI diagnosis finder. Output ONLY a JSON array of missed diagnoses. No explanation, no reasoning, no preamble.

Map: low albumin+nutrition risk=malnutrition, cultures+abx+organ dysfunction=sepsis, Hgb drop+bleeding=blood loss anemia, Cr rise=AKI, low PaO2/SpO2=respiratory failure, wound care stage=pressure ulcer, AMS+metabolic=encephalopathy, BMI≥35=obesity, Na/K/Mg/Ca abnormal=electrolyte disorder, low platelets=coagulopathy, EF<40 or fluid overload=heart failure.

Output: [{"diagnosis":"...","icd10_code":"...","category":"..."}]""",
        "user_prefix": "",
        "temperature": 0.2,
        "description": "V23 anti-CoT: minimal direct classification, no reasoning steps (arXiv 2509.21933 finding that CoT hurts clinical classification)",
    },
}


# ===========================================================================
# DIAGNOSIS EXTRACTION + MATCHING
# ===========================================================================

def extract_diagnoses_from_query(cdi_diagnoses_str: str) -> List[str]:
    """Extract individual diagnoses from CDI confirmed diagnoses string."""
    if not cdi_diagnoses_str or pd.isna(cdi_diagnoses_str):
        return []

    text = str(cdi_diagnoses_str).strip()

    # Handle list-like strings: "['Diastolic CHF, Acute-on-Chronic', 'Malnutrition']"
    if text.startswith("[") and text.endswith("]"):
        try:
            items = eval(text)  # Safe for our known data format
            if isinstance(items, list):
                return [str(i).strip() for i in items if str(i).strip()]
        except:
            pass

    # Handle pipe-separated
    if "|" in text:
        return [d.strip() for d in text.split("|") if d.strip()]

    # Handle comma-separated (but careful with "CHF, Acute-on-Chronic")
    # Use semicolon first if present
    if ";" in text:
        return [d.strip() for d in text.split(";") if d.strip()]

    # Single diagnosis
    return [text] if text else []


def parse_llm_diagnoses(raw_response: str) -> List[Dict]:
    """Parse LLM response into list of diagnosis dicts."""
    # Try JSON array parse
    try:
        result = json.loads(raw_response)
        if isinstance(result, list):
            return result
    except:
        pass

    # Try extracting JSON array from response
    match = re.search(r'\[[\s\S]*?\]', raw_response)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except:
            pass

    # Try extracting from code fences
    match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw_response)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list):
                return result
        except:
            pass

    return []


def normalize_diagnosis(text: str) -> str:
    """Normalize diagnosis text for comparison (matches evaluate_cdi_accuracy.py)."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def diagnoses_match(predicted: str, ground_truth: str, threshold: float = 0.5) -> bool:
    """Check if predicted diagnosis matches ground truth.
    Ported from evaluate_cdi_accuracy.py which achieved 58.6% recall.
    Uses fuzzy matching + comprehensive clinical equivalents dictionary.
    """
    pred = normalize_diagnosis(predicted)
    truth = normalize_diagnosis(ground_truth)

    # Exact match
    if pred == truth:
        return True

    # Substring match (either direction)
    if pred in truth or truth in pred:
        return True

    # Clinical equivalents — comprehensive dictionary matching evaluate_cdi_accuracy.py
    clinical_equivalents = {
        'pressure ulcer': ['decubitus ulcer', 'pressure injury', 'pressure sore', 'bed sore', 'stage 2', 'stage 3', 'stage 4'],
        'malnutrition': ['protein calorie malnutrition', 'hypoalbuminemia', 'cachexia', 'underweight', 'severe malnutrition', 'moderate malnutrition', 'mild malnutrition', 'protein calorie'],
        'sepsis': ['severe sepsis', 'septic shock', 'urosepsis', 'septicemia'],
        'thrombocytopenia': ['pancytopenia', 'low platelets'],
        'anemia': ['anaemia', 'blood loss anemia', 'acute blood loss anemia', 'iron deficiency anemia', 'chronic anemia', 'normocytic anemia', 'anemia of chronic disease'],
        'respiratory failure': ['hypoxic respiratory failure', 'acute respiratory failure', 'hypoxia', 'hypercapnic', 'hypercapnia', 'respiratory distress'],
        'heart failure': ['chf', 'congestive heart failure', 'systolic heart failure', 'diastolic heart failure',
                          'acute on chronic heart failure', 'hfref', 'hfpef', 'pulmonary edema', 'diastolic chf', 'systolic chf'],
        'acute kidney injury': ['aki', 'acute renal failure', 'acute renal insufficiency', 'ckd', 'kidney disease', 'renal failure'],
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
        'hyperkalemia': ['high potassium'],
        'hypomagnesemia': ['low magnesium'],
        'coagulation': ['coagulopathy', 'dic', 'disseminated intravascular'],
    }

    for base_term, equivalent_terms in clinical_equivalents.items():
        pred_has = base_term in pred or any(term in pred for term in equivalent_terms)
        truth_has = base_term in truth or any(term in truth for term in equivalent_terms)
        if pred_has and truth_has:
            return True

    # Key word overlap (matching evaluate_cdi_accuracy.py approach)
    pred_words = set(pred.split())
    truth_words = set(truth.split())

    stop_words = {'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to', 'of', 'in', 'on',
                  'confirmed', 'ruled', 'out', 'poa', 'present', 'admission', 'acute', 'chronic',
                  'type', 'by', 'from', 'is', 'was', 'are', 'were', 'not', 'no', 'unspecified',
                  'other', 'nos', 'nec', 'specified', 'site', 'for'}
    pred_words -= stop_words
    truth_words -= stop_words

    if not pred_words or not truth_words:
        return False

    overlap = len(pred_words & truth_words)
    max_len = max(len(pred_words), len(truth_words))

    if max_len == 0:
        return False

    # 50% overlap threshold (matching evaluate_cdi_accuracy.py, was 60% before)
    return overlap / max_len >= threshold


# ===========================================================================
# MAIN HILL-CLIMBING RUNNER
# ===========================================================================

class HillClimbRunner:
    """Robust hill-climbing evaluation runner."""

    def __init__(self, api_key: str, model: str, data_path: str,
                 sample_size: int = 30, results_dir: str = "results"):
        self.api_key = api_key
        self.model = model
        self.data_path = data_path
        self.sample_size = sample_size
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(exist_ok=True)

        self.log_file = self.results_dir / f"hill_climb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tsv"
        self.checkpoint_file = self.results_dir / "hill_climb_checkpoint.json"
        self.detail_log = self.results_dir / f"hill_climb_detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

        self.all_results = []

    def load_cases(self) -> List[Dict]:
        """Load evaluation cases with stratified sampling across CDI categories.
        Ensures every diagnosis category is represented (min 2 per category).
        Falls back to random sampling if stratified sample file not found.
        """
        print(f"Loading data from {self.data_path}...")
        df = pd.read_csv(self.data_path)
        df = df[df['cdi_diagnoses_confirmed'].notna()].copy()

        # Try stratified sampling first
        stratified_path = Path(self.data_path).parent / "stratified_eval_sample.csv"
        if stratified_path.exists() and self.sample_size >= 40:
            print(f"Using stratified sample from {stratified_path}")
            strat = pd.read_csv(stratified_path)
            strat_csns = set(strat['encounter_csn'].astype(str))
            sample = df[df['encounter_csn'].astype(str).isin(strat_csns)].copy()
            # If stratified sample is larger than requested, trim proportionally
            if len(sample) > self.sample_size:
                random.seed(42)
                sample = sample.sample(n=self.sample_size, random_state=42)
            print(f"  Stratified sample: {len(sample)} cases across {strat['category'].nunique()} categories")
        else:
            # Random sampling fallback
            random.seed(42)
            indices = random.sample(range(len(df)), min(self.sample_size, len(df)))
            sample = df.iloc[indices].copy()

        sample = sample.reset_index(drop=True)

        cases = []
        for _, row in sample.iterrows():
            true_dx = extract_diagnoses_from_query(row.get('cdi_diagnoses_confirmed', ''))
            if not true_dx:
                continue
            cases.append({
                'id': str(row.get('encounter_csn', len(cases))),
                'discharge_summary': str(row.get('discharge_summary', '')),
                'progress_note': str(row.get('progress_note', '')) if pd.notna(row.get('progress_note')) else None,
                'hp_note': str(row.get('hp_note', '')) if pd.notna(row.get('hp_note', None)) else None,
                'consult_note': str(row.get('consult_note', '')) if pd.notna(row.get('consult_note', None)) else None,
                'true_diagnoses': true_dx,
            })

        print(f"Loaded {len(cases)} valid cases (from {len(df)} total)")
        return cases

    def _build_notes_text(self, case: Dict) -> str:
        """Build the full clinical notes text from all available note types."""
        text = case['discharge_summary']
        if case.get('progress_note'):
            text += f"\n\nPROGRESS NOTE:\n{case['progress_note']}"
        if case.get('hp_note'):
            text += f"\n\nHISTORY & PHYSICAL:\n{case['hp_note']}"
        if case.get('consult_note'):
            text += f"\n\nCONSULTATION NOTE:\n{case['consult_note']}"
        return text

    def _call_single(self, system: str, user_content: str, temperature: float = 0.2) -> str:
        """Make a single LLM call and return raw text."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})
        return call_llm(messages, self.api_key, model=self.model, temperature=temperature)

    def predict_case(self, case: Dict, variant: Dict) -> List[Dict]:
        """Run prediction on a single case using a prompt variant.
        Dispatches to specialised methods for multi-pass variants.
        """
        method = variant.get("predict_method", "standard")

        if method == "standard":
            return self._predict_standard(case, variant)
        elif method == "two_pass_verify":
            return self._predict_two_pass_verify(case, variant)
        elif method == "self_consistency":
            return self._predict_self_consistency(case, variant)
        elif method == "extract_then_classify":
            return self._predict_extract_then_classify(case, variant)
        elif method == "multi_agent":
            return self._predict_multi_agent(case, variant)
        else:
            return self._predict_standard(case, variant)

    def _predict_standard(self, case: Dict, variant: Dict) -> List[Dict]:
        """Standard single-pass prediction."""
        notes = self._build_notes_text(case)
        user_content = variant["user_prefix"] + notes
        raw = self._call_single(
            variant.get("system", ""),
            user_content,
            variant.get("temperature", 0.2),
        )
        return parse_llm_diagnoses(raw)

    def _predict_two_pass_verify(self, case: Dict, variant: Dict) -> List[Dict]:
        """Two-pass: generate candidates, then verify each against notes.
        Based on 'Verification is All You Need' (IEEE 2025).
        """
        notes = self._build_notes_text(case)

        # Pass 1: Generate broad candidate list
        raw_pass1 = self._call_single(
            variant["system_pass1"],
            variant["user_prefix_pass1"] + notes,
            variant.get("temperature", 0.2),
        )
        candidates = parse_llm_diagnoses(raw_pass1)
        if not candidates:
            return []

        # Format candidates for pass 2
        candidate_text = json.dumps(candidates[:15], indent=2)  # cap at 15 to fit context

        # Pass 2: Verify candidates against the notes
        user_pass2 = variant["user_prefix_pass2"].format(candidates=candidate_text) + notes
        raw_pass2 = self._call_single(
            variant["system_pass2"],
            user_pass2,
            variant.get("temperature", 0.2),
        )
        return parse_llm_diagnoses(raw_pass2)

    def _predict_self_consistency(self, case: Dict, variant: Dict) -> List[Dict]:
        """Self-consistency: multiple samples, keep diagnoses with majority votes.
        Based on arXiv 2503.22092 + 2510.04048.
        """
        notes = self._build_notes_text(case)
        user_content = variant["user_prefix"] + notes
        num_samples = variant.get("num_samples", 3)
        vote_threshold = variant.get("vote_threshold", 2)

        all_predictions = []
        for s in range(num_samples):
            raw = self._call_single(
                variant.get("system", ""),
                user_content,
                variant.get("temperature", 0.7),
            )
            preds = parse_llm_diagnoses(raw)
            all_predictions.append(preds)
            if s < num_samples - 1:
                time.sleep(1)  # brief pause between samples

        # Vote: count how many samples include each diagnosis (by normalized name)
        diagnosis_votes = {}  # normalized_name -> {count, best_entry}
        for preds in all_predictions:
            seen_this_sample = set()
            for p in preds:
                name = p.get('diagnosis', str(p)) if isinstance(p, dict) else str(p)
                norm = normalize_diagnosis(name)
                if norm in seen_this_sample:
                    continue
                seen_this_sample.add(norm)

                # Check if this matches an existing vote entry
                matched_key = None
                for existing_key in diagnosis_votes:
                    if diagnoses_match(norm, existing_key, threshold=0.5):
                        matched_key = existing_key
                        break

                if matched_key:
                    diagnosis_votes[matched_key]['count'] += 1
                else:
                    diagnosis_votes[norm] = {'count': 1, 'entry': p}

        # Keep diagnoses with enough votes
        results = []
        for key, info in diagnosis_votes.items():
            if info['count'] >= vote_threshold:
                results.append(info['entry'])

        return results

    def _predict_extract_then_classify(self, case: Dict, variant: Dict) -> List[Dict]:
        """Two-phase: extract abnormal findings, then map to diagnoses.
        Based on JMIR Medical Informatics 2025 two-phase framework.
        """
        notes = self._build_notes_text(case)

        # Phase 1: Extract all abnormal findings
        raw_extract = self._call_single(
            variant["system_extract"],
            variant["user_prefix_extract"] + notes,
            variant.get("temperature", 0.2),
        )
        findings = parse_llm_diagnoses(raw_extract)  # reuse JSON parser
        if not findings:
            return []

        # Format findings for phase 2
        findings_text = json.dumps(findings[:30], indent=2)  # cap at 30 findings

        # Phase 2: Map findings to CDI diagnoses
        user_classify = variant["user_prefix_classify"].format(findings=findings_text)
        raw_classify = self._call_single(
            variant["system_classify"],
            user_classify,
            variant.get("temperature", 0.2),
        )
        return parse_llm_diagnoses(raw_classify)

    def _predict_multi_agent(self, case: Dict, variant: Dict) -> List[Dict]:
        """Multi-agent pipeline: evidence → map → validate.
        Based on 'Code Like Humans' (EMNLP Findings 2025).
        """
        notes = self._build_notes_text(case)

        # Agent 1: Extract evidence
        raw_evidence = self._call_single(
            variant["system_evidence"],
            variant["user_prefix_evidence"] + notes,
            variant.get("temperature", 0.2),
        )
        evidence = parse_llm_diagnoses(raw_evidence)
        if not evidence:
            return []
        evidence_text = json.dumps(evidence[:25], indent=2)

        # Agent 2: Map evidence to diagnoses
        user_map = variant["user_prefix_map"].format(evidence=evidence_text)
        raw_map = self._call_single(
            variant["system_map"],
            user_map,
            variant.get("temperature", 0.2),
        )
        diagnoses = parse_llm_diagnoses(raw_map)
        if not diagnoses:
            return []
        diagnoses_text = json.dumps(diagnoses[:15], indent=2)

        # Agent 3: Validate
        user_validate = variant["user_prefix_validate"].format(diagnoses=diagnoses_text)
        raw_validate = self._call_single(
            variant["system_validate"],
            user_validate,
            variant.get("temperature", 0.2),
        )
        return parse_llm_diagnoses(raw_validate)

    def evaluate_variant(self, cases: List[Dict], variant_name: str, variant: Dict) -> Dict:
        """Evaluate a prompt variant against all cases."""
        total_true = 0
        total_matched = 0
        total_predicted = 0
        case_results = []
        errors = 0

        for i, case in enumerate(cases):
            print(f"  Case {i+1}/{len(cases)} (ID: {case['id']})...", end="", flush=True)

            try:
                predictions = self.predict_case(case, variant)
                pred_names = [p.get('diagnosis', str(p)) if isinstance(p, dict) else str(p) for p in predictions]

                # Match predictions to ground truth
                case_matched = 0
                for true_dx in case['true_diagnoses']:
                    for pred_dx in pred_names:
                        if diagnoses_match(pred_dx, true_dx):
                            case_matched += 1
                            break

                total_true += len(case['true_diagnoses'])
                total_matched += case_matched
                total_predicted += len(pred_names)

                case_recall = case_matched / len(case['true_diagnoses']) if case['true_diagnoses'] else 0
                print(f" {case_matched}/{len(case['true_diagnoses'])} matched ({case_recall:.0%})")

                case_results.append({
                    'case_id': case['id'],
                    'true_diagnoses': case['true_diagnoses'],
                    'predicted': pred_names[:8],
                    'matched': case_matched,
                    'total_true': len(case['true_diagnoses']),
                })

            except Exception as e:
                errors += 1
                print(f" ERROR: {e}")
                total_true += len(case['true_diagnoses'])
                case_results.append({
                    'case_id': case['id'],
                    'error': str(e),
                    'matched': 0,
                    'total_true': len(case['true_diagnoses']),
                })
                # Brief pause after error before next case
                time.sleep(3)
                continue

        recall = total_matched / total_true if total_true > 0 else 0
        precision = total_matched / total_predicted if total_predicted > 0 else 0
        f1 = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0

        result = {
            'variant': variant_name,
            'description': variant.get('description', ''),
            'recall': recall,
            'precision': precision,
            'f1': f1,
            'matched': total_matched,
            'total_true': total_true,
            'total_predicted': total_predicted,
            'errors': errors,
            'case_results': case_results,
            'timestamp': datetime.now().isoformat(),
        }

        return result

    def log_result(self, result: Dict):
        """Log result to TSV and JSONL."""
        self.all_results.append(result)

        # TSV summary
        rows = []
        for r in self.all_results:
            rows.append({
                'variant': r['variant'],
                'recall': f"{r['recall']:.4f}",
                'precision': f"{r['precision']:.4f}",
                'f1': f"{r['f1']:.4f}",
                'matched': r['matched'],
                'total_true': r['total_true'],
                'total_predicted': r['total_predicted'],
                'errors': r['errors'],
                'description': r['description'],
                'timestamp': r['timestamp'],
            })
        pd.DataFrame(rows).to_csv(self.log_file, sep='\t', index=False)

        # JSONL detail (append)
        with open(self.detail_log, 'a') as f:
            f.write(json.dumps(result, default=str) + '\n')

    def save_checkpoint(self, best_variant: str, best_recall: float):
        """Save checkpoint for resume."""
        checkpoint = {
            'best_variant': best_variant,
            'best_recall': best_recall,
            'completed_variants': [r['variant'] for r in self.all_results],
            'timestamp': datetime.now().isoformat(),
        }
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)

    def run(self):
        """Run the full hill-climbing evaluation."""
        print("\n" + "=" * 80)
        print("CDI PREDICTOR HILL-CLIMBING EVALUATION")
        print("=" * 80)
        print(f"Model: {self.model}")
        print(f"Sample size: {self.sample_size}")
        print(f"Prompt variants: {len(PROMPT_VARIANTS)}")
        print(f"Results: {self.log_file}")
        print("=" * 80)

        # Load cases
        cases = self.load_cases()
        if not cases:
            print("ERROR: No cases loaded")
            return

        # Check for checkpoint to resume
        completed = set()
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file) as f:
                    ckpt = json.load(f)
                completed = set(ckpt.get('completed_variants', []))
                if completed:
                    print(f"Resuming — {len(completed)} variants already completed: {completed}")
            except:
                pass

        best_variant = None
        best_recall = 0.0
        best_f1 = 0.0

        # Evaluate each variant
        for variant_name, variant in PROMPT_VARIANTS.items():
            if variant_name in completed:
                print(f"\nSkipping {variant_name} (already completed)")
                continue

            print(f"\n{'=' * 80}")
            print(f"EVALUATING: {variant_name}")
            print(f"Description: {variant.get('description', '')}")
            print(f"Temperature: {variant.get('temperature', 0.2)}")
            print(f"{'=' * 80}")

            result = self.evaluate_variant(cases, variant_name, variant)
            self.log_result(result)

            print(f"\n--- {variant_name} RESULTS ---")
            print(f"  Recall:    {result['recall']:.4f} ({result['matched']}/{result['total_true']})")
            print(f"  Precision: {result['precision']:.4f}")
            print(f"  F1:        {result['f1']:.4f}")
            print(f"  Errors:    {result['errors']}")

            if result['recall'] > best_recall or (result['recall'] == best_recall and result['f1'] > best_f1):
                best_variant = variant_name
                best_recall = result['recall']
                best_f1 = result['f1']
                print(f"  >>> NEW BEST <<<")

            self.save_checkpoint(best_variant, best_recall)

            # Pause between variants to avoid rate limits
            print("Pausing 5s before next variant...")
            time.sleep(5)

        # Final summary
        print("\n" + "=" * 80)
        print("HILL-CLIMBING COMPLETE")
        print("=" * 80)
        print(f"\nResults for all variants:")
        print(f"{'Variant':<20} {'Recall':>8} {'Precision':>10} {'F1':>6} {'Matched':>8}")
        print("-" * 60)
        for r in sorted(self.all_results, key=lambda x: x['recall'], reverse=True):
            marker = " <<<" if r['variant'] == best_variant else ""
            print(f"{r['variant']:<20} {r['recall']:>8.4f} {r['precision']:>10.4f} {r['f1']:>6.4f} {r['matched']:>4}/{r['total_true']}{marker}")

        print(f"\nBest variant: {best_variant}")
        print(f"Best recall: {best_recall:.4f}")
        print(f"Results saved to: {self.log_file}")
        print(f"Detail log: {self.detail_log}")
        print("=" * 80)


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Hill-climbing CDI evaluation')
    parser.add_argument('--api-key', required=True)
    parser.add_argument('--model', default='gpt-5', choices=['gpt-5', 'gpt-4.1', 'gpt-5-nano'])
    parser.add_argument('--data', default='data/cdi_full_dataset_parsed_confirmed_only.csv',
                        help='Dataset CSV (default: expanded 768-row dataset with 4 note types)')
    parser.add_argument('--sample-size', type=int, default=30)
    parser.add_argument('--results-dir', default='results')
    args = parser.parse_args()

    runner = HillClimbRunner(
        api_key=args.api_key,
        model=args.model,
        data_path=args.data,
        sample_size=args.sample_size,
        results_dir=args.results_dir,
    )
    runner.run()
