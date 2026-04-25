#!/usr/bin/env python3
"""
CDI Coding Intelligence Engine
================================
Production prediction module combining:
  - v15 system prompt (55.1% recall — best single variant)
  - v19 self-consistency voting (20.4% F1 — best precision/recall balance)
  - DRG impact classification (MCC/CC/non-CC)
  - Confidence scoring based on vote counts

Usage:
    from cdi_engine import CDIEngine

    engine = CDIEngine(api_key="...", model="gpt-5")
    results = engine.analyse(
        discharge_summary="...",
        progress_note="...",      # optional
        hp_note="...",            # optional
        consult_note="...",       # optional
        mode="balanced",          # "fast" | "balanced" | "high_recall"
    )

Modes:
    fast         — single pass with v15 prompt (1 API call, ~55% recall)
    balanced     — 3x self-consistency voting (3 API calls, best F1)
    high_recall  — 5x voting with lower threshold (5 API calls, max recall)
"""

import json
import re
import time
import random
import requests
from typing import Dict, List, Optional, Tuple
from datetime import datetime


# ===========================================================================
# STANFORD API
# ===========================================================================

API_ENDPOINTS = {
    "gpt-5": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
    "gpt-4.1": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview",
    "gpt-5-nano": "https://apim.stanfordhealthcare.org/openai-eastus2/deployments/gpt-5-nano/chat/completions?api-version=2024-12-01-preview",
}


def _call_llm(messages: list, api_key: str, model: str = "gpt-5",
              temperature: float = 0.2, max_tokens: int = 32000) -> str:
    """Call Stanford LLM with robust retry logic.

    GPT-5 note: max_completion_tokens covers BOTH reasoning tokens and output
    tokens. With 16k, reasoning often consumes everything. Default raised to 32k.
    If truncated, retries automatically with doubled budget (up to 65k).
    """
    url = API_ENDPOINTS.get(model, API_ENDPOINTS["gpt-5"])
    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": api_key,
    }

    is_gpt5 = model.startswith("gpt-5")
    current_max = max_tokens

    for attempt in range(5):
        body = {"model": model, "messages": messages}
        if is_gpt5:
            body["max_completion_tokens"] = current_max
        else:
            body["temperature"] = temperature
            body["max_tokens"] = 4000

        try:
            resp = requests.post(url, headers=headers,
                                 data=json.dumps(body), timeout=300)

            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** (attempt + 1) + random.random() * 2
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise RuntimeError(f"API {resp.status_code}: {resp.text[:300]}")
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                continue

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            if content is None or content == "":
                fr = data["choices"][0].get("finish_reason", "unknown")
                if fr == "length":
                    # Reasoning consumed all tokens — retry with doubled budget
                    if current_max < 65000:
                        current_max = min(current_max * 2, 65000)
                        print(f"    Reasoning consumed all tokens, retrying with max_completion_tokens={current_max}")
                        time.sleep(1)
                        continue
                    raise RuntimeError(
                        f"Response truncated even at {current_max} tokens — "
                        "reasoning consumed entire budget"
                    )
                return ""

            time.sleep(0.5)
            return content

        except requests.exceptions.Timeout:
            time.sleep(2 ** (attempt + 1) + random.random() * 2)
        except requests.exceptions.ConnectionError:
            time.sleep(2 ** (attempt + 1) + random.random() * 2)

    raise RuntimeError("API call failed after 5 retries")


# ===========================================================================
# V15 SYSTEM PROMPT — best single-variant recall (55.1%)
# ===========================================================================

SYSTEM_PROMPT = """You are a clinical documentation integrity (CDI) assistant. Your role is to identify clinically-supported diagnoses that are present in the patient's record but were not captured in the attending's discharge diagnoses — particularly those that would increase case complexity or DRG weight.

Focus on:
1. MCCs (Major Complications/Comorbidities) — highest reimbursement impact
2. CCs (Complications/Comorbidities) — moderate impact
3. Specificity upgrades — "heart failure" → "acute on chronic diastolic heart failure"

SCREEN EVERY CASE FOR THESE HIGH-VALUE CATEGORIES:

Sepsis: ANY infection + SIRS criteria (temp >38.3/<36, HR >90, RR >20, WBC >12/<4). Include confirmed, suspected, and "not severe sepsis". Specify organism source (UTI, pneumonia, cellulitis, etc).

Respiratory failure: Acute hypoxic (SpO2 <88% or PaO2 <60 on RA), chronic hypoxic, acute-on-chronic. Separate from pneumonia — code both if present. Include non-cardiogenic pulmonary edema.

Malnutrition: Albumin <2.5 + (BMI <18.5 OR poor intake OR weight loss OR muscle wasting) → severe protein-calorie malnutrition. Albumin 2.5-3.0 + clinical signs → moderate. Always specify severity.

Anemia: Specify type — acute blood loss (post-surgical, GI bleed), iron deficiency (low ferritin/TIBC), chronic disease. Note if present on admission.

Obesity: Map documented BMI to ICD-10 severity — BMI 30-34.9=Class I, 35-39.9=Class II, >=40=Class III (morbid).

Pressure injuries: Screen for ANY wound documentation — sacral, coccyx, heel, ischial. Include POA status and staging.

Electrolytes: ONLY include if clinically significant — documented in Assessment/Plan AND treatment ordered. Na <130=hyponatremia, K >5.5=hyperkalemia, Mg <1.5=hypomagnesemia.

Renal: AKI with KDIGO staging (based on Cr rise from baseline). CKD must be explicitly staged.

Encephalopathy: Altered mental status + metabolic cause (hepatic, septic, metabolic). Distinguish from delirium.

For each finding, provide:
- The specific diagnosis name (use standard clinical terminology)
- The most specific ICD-10-CM code
- Detailed clinical evidence from the notes
- A brief reasoning for why this should be queried"""

USER_PREFIX = """Analyse this clinical encounter for missed or under-specified diagnoses. Review all available notes.

Pay particular attention to conditions with clinical evidence in the progress, consult, or procedure notes (labs, vitals, specialist findings, intraoperative complications) that do NOT appear in the discharge diagnoses or problem list — these gaps are a common source of missed queries. Do not, however, overlook diagnoses named in the discharge Assessment/Plan, nutrition sections, or problem list: these still warrant querying when specificity or DRG impact is being lost (e.g. "sepsis" without organ dysfunction, "malnutrition" without severity, "CHF" without acuity/type).

IMPORTANT: Map lab findings to diagnoses, not raw lab values:
- Low albumin → query malnutrition (not hypoalbuminemia)
- Low Hgb → query specific anemia type (not just "anemia")
- Elevated Cr → query AKI with KDIGO stage (not just "elevated creatinine")
- Elevated lactate → query lactic acidosis or sepsis (not just "elevated lactate")

Return ONLY a JSON array: [{"diagnosis":"...","icd10_code":"...","category":"sepsis|respiratory|anemia|malnutrition|electrolytes|cardiac|renal|coagulation|pressure_ulcer|encephalopathy|obesity|other","confidence":"high|medium|low","evidence":"..."}]

"""


# ===========================================================================
# DRG IMPACT CLASSIFICATION
# ===========================================================================

# Common MCC ICD-10 codes (Major Complications/Comorbidities)
# These have the highest reimbursement impact
MCC_PATTERNS = {
    'sepsis': True,
    'severe sepsis': True,
    'septic shock': True,
    'acute respiratory failure': True,
    'severe malnutrition': True,
    'severe protein-calorie malnutrition': True,
    'protein-calorie malnutrition': True,  # E43 is MCC
    'acute kidney injury': True,
    'stage 3 pressure ulcer': True,
    'stage 4 pressure ulcer': True,
    'unstageable pressure ulcer': True,
    'pancytopenia': True,
    'hepatic encephalopathy': True,
    'metabolic encephalopathy': True,
    'lactic acidosis': True,
    'acute pulmonary edema': True,
    'acute on chronic heart failure': True,
    'acute on chronic systolic heart failure': True,
    'acute on chronic diastolic heart failure': True,
    'morbid obesity': True,
}

# CC codes (Complications/Comorbidities) — moderate impact
CC_PATTERNS = {
    'anemia': True,
    'acute blood loss anemia': True,
    'iron deficiency anemia': True,
    'hyponatremia': True,
    'hyperkalemia': True,
    'hypokalemia': True,
    'hypomagnesemia': True,
    'hypocalcemia': True,
    'hyperglycemia': True,
    'thrombocytopenia': True,
    'coagulopathy': True,
    'chronic respiratory failure': True,
    'chronic kidney disease': True,
    'heart failure': True,
    'chronic systolic heart failure': True,
    'chronic diastolic heart failure': True,
    'stage 2 pressure ulcer': True,
    'obesity': True,
    'delirium': True,
    'hypophosphatemia': True,
    'type 2 diabetes with hyperglycemia': True,
}


def classify_drg_impact(diagnosis: str) -> str:
    """Classify diagnosis as MCC, CC, or non-CC based on clinical terminology."""
    dx_lower = diagnosis.lower().strip()

    # Check MCC first (higher priority)
    for pattern in MCC_PATTERNS:
        if pattern in dx_lower:
            return "MCC"

    # Check CC
    for pattern in CC_PATTERNS:
        if pattern in dx_lower:
            return "CC"

    return "non-CC"


def estimate_revenue_impact(drg_class: str) -> str:
    """Rough revenue impact estimate for display purposes."""
    if drg_class == "MCC":
        return "$5,000–$15,000"
    elif drg_class == "CC":
        return "$2,000–$5,000"
    return "Minimal"


# ===========================================================================
# CATEGORY DISPLAY METADATA
# ===========================================================================

CATEGORY_META = {
    "sepsis":          {"icon": "🔴", "color": "#B71C1C", "label": "Sepsis"},
    "respiratory":     {"icon": "🫁", "color": "#1565C0", "label": "Respiratory"},
    "anemia":          {"icon": "🩸", "color": "#C62828", "label": "Anemia"},
    "malnutrition":    {"icon": "⚠️", "color": "#E65100", "label": "Malnutrition"},
    "electrolytes":    {"icon": "⚡", "color": "#F9A825", "label": "Electrolytes"},
    "cardiac":         {"icon": "❤️", "color": "#AD1457", "label": "Cardiac"},
    "renal":           {"icon": "🫘", "color": "#4527A0", "label": "Renal"},
    "coagulation":     {"icon": "🧬", "color": "#6A1B9A", "label": "Coagulation"},
    "pressure_ulcer":  {"icon": "🩹", "color": "#4E342E", "label": "Pressure Injury"},
    "encephalopathy":  {"icon": "🧠", "color": "#283593", "label": "Encephalopathy"},
    "obesity":         {"icon": "📊", "color": "#00695C", "label": "Obesity"},
    "other":           {"icon": "📋", "color": "#546E7A", "label": "Other"},
}


# ===========================================================================
# DOCUMENTED DIAGNOSIS FILTER (precision improvement)
# ===========================================================================

def _extract_documented_diagnoses(discharge_summary: str) -> List[str]:
    """Extract diagnoses already documented in structured sections of the note.
    Used to filter out LLM predictions that match already-documented conditions.

    Section coverage informed by the LLM-as-judge audit (April 2026): the biggest
    gaps in the previous version were Stanford-specific headers like
    "Relevant Clinical Conditions" and "Active Hospital Problems".
    """
    documented = []

    # Normalise: BigQuery CSVs often flatten newlines to double spaces
    text = re.sub(r'  +', '\n', discharge_summary)

    section_headers = [
        # Standard formal diagnosis sections
        r'Discharge\s+Diagnos[ei]s',
        r'Admitting\s+Diagnos[ei]s',
        r'Admission\s+Diagnos[ei]s',
        r'Principal\s+Diagnos[ei]s',
        r'Secondary\s+Diagnos[ei]s',
        r'Final\s+Diagnos[ei]s',
        r'Discharge\s+Dx',
        # Problem lists
        r'Active\s+Problems?',
        r'Active\s+Hospital\s+Problems?',
        r'Active\s+Issues?',
        r'Problem\s+List',
        r'Hospital\s+Problems?',
        # Stanford-specific sections (heavy CDI concentration)
        r'Relevant\s+Clinical\s+Conditions',
        r'Discharge\s+Teaching\s+Physician\s+Attestation',
        r'Hospital\s+Course',
    ]

    for header in section_headers:
        pattern = header + r'\s*:?\s*\n(.*?)(?=\n[A-Z][A-Za-z\s/]{3,}:|$)'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            for line in match.split('\n'):
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                # Skip treatment/management sub-bullets
                if line.startswith('-') and any(kw in line.lower() for kw in
                    ['continue', 'monitor', 'start', 'wean', 'switch', 'given',
                     'improved', 'stable', 'mg', 'iv', 'po', 'bid', 'tid',
                     'daily', 'prn', 'as needed', 'prophylaxis']):
                    continue
                cleaned = re.sub(r'^[\s\-\*\d\.)]+', '', line).strip()
                if cleaned and len(cleaned) > 3:
                    documented.append(cleaned)

    # Also extract #Problem formatted lines (Stanford EMR format)
    hash_problems = re.findall(r'^\s*#\s*(.+)', text, re.MULTILINE)
    for prob in hash_problems:
        cleaned = prob.strip()
        if cleaned and len(cleaned) > 3:
            documented.append(cleaned)

    return documented


# --------------------------------------------------------------------------
# Clinical synonym normalisation — applied before overlap comparison so that
# equivalent expressions collapse to the same canonical form.
# Informed by the LLM-as-judge audit.
# --------------------------------------------------------------------------

# Substring replacements (applied to lowercased text, in order)
_CLINICAL_SYNONYMS = [
    # Heart failure abbreviations
    (r'\bhfref\b', 'systolic heart failure'),
    (r'\bhfpef\b', 'diastolic heart failure'),
    (r'\bchf\b', 'heart failure'),
    (r'\bhf\b', 'heart failure'),
    # Kidney
    (r'\baki\b', 'acute kidney injury'),
    (r'\bckd\b', 'chronic kidney disease'),
    # Anemia
    (r'\bida\b', 'iron deficiency anemia'),
    (r'\bposthemorrhagic\b', 'blood loss'),
    (r'\bacute\s+blood\s+loss\s+anemia\b', 'acute blood loss anemia'),
    (r'\bpost-?op\s+acute\s+blood\s+loss\s+anemia\b', 'acute blood loss anemia'),
    (r'\banemia\s+of\s+malignancy\b', 'anemia in neoplastic disease'),
    (r'\banemia\s+due\s+to\s+malignancy\b', 'anemia in neoplastic disease'),
    (r'\banemia\s+of\s+chronic\s+disease\b', 'anemia chronic disease'),
    # Respiratory
    (r'\bars[fd]\b', 'acute respiratory distress'),
    # Cardiomyopathy
    (r'\bhocm\b', 'hypertrophic cardiomyopathy'),
    (r'\bhypertrophic\s+obstructive\s+cardiomyopathy\b', 'hypertrophic cardiomyopathy'),
    # Malnutrition
    (r'\bprotein[-\s]calorie\s+malnutrition\b', 'malnutrition'),
    (r'\bprotein[-\s]energy\s+malnutrition\b', 'malnutrition'),
    (r'\bcachexia\b', 'malnutrition'),
    # Diabetes
    (r'\bdm\s*type\s*2\b', 'type 2 diabetes mellitus'),
    (r'\bdm2\b', 'type 2 diabetes mellitus'),
    (r'\bt2dm\b', 'type 2 diabetes mellitus'),
    (r'\bdm1\b', 'type 1 diabetes mellitus'),
    (r'\bt1dm\b', 'type 1 diabetes mellitus'),
    # Infections
    (r'\buti\b', 'urinary tract infection'),
    (r'\bcauti\b', 'urinary tract infection'),
    # Acute MI
    (r'\bnstemi\b', 'non st elevation myocardial infarction'),
    (r'\bstemi\b', 'st elevation myocardial infarction'),
    # PE/DVT
    (r'\bpe\b', 'pulmonary embolism'),
    (r'\bdvt\b', 'deep vein thrombosis'),
    # Other common abbreviations
    (r'\bcopd\b', 'chronic obstructive pulmonary disease'),
    (r'\bpna\b', 'pneumonia'),
    (r'\bosa\b', 'obstructive sleep apnea'),
    (r'\bohs\b', 'obesity hypoventilation syndrome'),
]

# Markers that add no codable information and should be stripped before
# comparison (stage/grade/class/POA are flags, not new diagnoses)
_NON_CODABLE_MARKERS = [
    r'\bpresent\s+on\s+admission\b',
    r'\bpoa\b',
    r'\bnot\s+present\s+on\s+admission\b',
    r'\bnpoa\b',
    r'\bkdigo\s+stage\s+[1-3]\b',
    r'\bstage\s+[1-5]\b',
    r'\bstage\s+[ivx]+\b',
    r'\bclass\s+[i-v]+\b',
    r'\bclass\s+[1-4]\b',
    r'\bnyha\s+class\s+[i-v]+\b',
    r'\bg[1-5][ab]?\b',            # CKD grading (G3a, G4 etc)
    r'\(bmi\s+[\d.]+\s*(?:kg/m\^?2)?\)',
    r'\(.*?(?:kg/m\^?2|ef\s*[<>]?\s*\d+).*?\)',  # parenthetical qualifiers
    r'\bresolved\b',
    r'\bimproved\b',
    r'\bruled\s+out\b',
    r'\bexpected\b',
    r'\bunspecified\b',
]


def _normalise_diagnosis(text: str) -> str:
    """Canonicalise a diagnosis string for comparison.

    Steps:
      1. Lowercase
      2. Strip non-codable markers (POA, stages, BMI parentheticals, etc.)
      3. Expand/collapse clinical synonyms to canonical forms
      4. Collapse whitespace and punctuation
    """
    s = text.lower().strip()

    # Strip parentheticals that are pure qualifiers
    for marker in _NON_CODABLE_MARKERS:
        s = re.sub(marker, ' ', s)

    # Apply synonym normalisation
    for pattern, replacement in _CLINICAL_SYNONYMS:
        s = re.sub(pattern, replacement, s)

    # Collapse punctuation and whitespace
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _is_specificity_upgrade(pred_dx: str, doc_dx: str) -> bool:
    """Check if prediction adds clinically meaningful, SEPARATELY CODABLE
    specificity over the documented diagnosis.

    Returns True only when the prediction introduces a new ICD-10-codable
    qualifier (acuity, type/subtype, severity where it changes the code,
    or added comorbidity). Returns False for non-codable markers like:
      - Stage/grade/KDIGO qualifiers (same code applies)
      - NYHA / obesity class (same code)
      - Present on admission flags
      - BMI parentheticals
      - "Resolved" / "improved" / "ruled out" status markers

    Examples that ARE upgrades:
      "heart failure" → "acute on chronic diastolic heart failure"
      "Type 2 diabetes" → "Type 2 diabetes with hyperglycemia"
      "anemia" → "iron deficiency anemia"

    Examples that are NOT upgrades (previously over-preserved):
      "AKI" → "AKI, KDIGO stage 1" (stage doesn't change code)
      "obesity" → "Class I obesity" (class doesn't change code)
      "heart failure" → "heart failure, present on admission" (POA is a flag)
    """
    # Normalise both sides — this strips non-codable markers and aligns synonyms
    pred_n = _normalise_diagnosis(pred_dx)
    doc_n = _normalise_diagnosis(doc_dx)

    if not pred_n or not doc_n:
        return False

    # After normalisation, documented must appear in prediction for it to be
    # a candidate upgrade (otherwise they're just different diagnoses)
    if doc_n not in pred_n:
        return False

    # Extract what the prediction adds beyond the documented text
    extra_text = pred_n.replace(doc_n, '', 1).strip()
    extra_words = extra_text.split()
    stop = {'', 'and', 'or', 'the', 'a', 'an', 'in', 'of', 'at', 'by', 'to',
            'for', 'with', 'on', 'from', 'as', 'is', 'was', 'be', 'being',
            'due', 'secondary'}
    meaningful = [w for w in extra_words if w and w not in stop]

    if not meaningful:
        return False

    # Codable upgrade keywords — these change the ICD-10 code when present
    CODABLE_UPGRADE_KEYWORDS = {
        # Acuity (changes code)
        'acute', 'subacute', 'fulminant', 'chronic',
        # Anatomic/pathologic type (changes code)
        'systolic', 'diastolic', 'restrictive', 'hypertrophic',
        # Severity that changes coding (malnutrition, respiratory failure)
        'severe', 'moderate', 'mild',
        # Complications/added codable states
        'shock', 'failure', 'hemorrhagic', 'aspiration',
        # Specific etiologies that add a code
        'hyperglycemia', 'ketoacidosis', 'hyperosmolar',
        'hyponatremia', 'hyperkalemia', 'hypokalemia',
        'hypercalcemia', 'hypocalcemia',
        # Anemia specificity (codable subtypes)
        'iron', 'deficiency', 'pernicious',
    }

    # Non-codable "specificity" — explicitly do NOT treat as upgrades
    # (after normalisation these would usually be stripped, but guard here too)
    NON_CODABLE_EXTRAS = {
        'kdigo', 'stage', 'class', 'grade', 'poa', 'present', 'admission',
        'resolved', 'improved', 'unspecified', 'other', 'nos',
        'expected', 'ruled',
    }

    # If the only "extras" are non-codable markers, this is NOT an upgrade
    meaningful_filtered = [w for w in meaningful if w not in NON_CODABLE_EXTRAS]
    if not meaningful_filtered:
        return False

    # Must contain at least one codable upgrade keyword
    for keyword in CODABLE_UPGRADE_KEYWORDS:
        if keyword in meaningful_filtered:
            return True

    # Two or more meaningful (non-marker) words added = likely a real
    # new comorbidity / combined code (e.g. "heart failure" →
    # "diastolic heart failure with hypertension")
    if len(meaningful_filtered) >= 2:
        return True

    return False


def _filter_already_documented(predictions: List[Dict],
                                documented: List[str],
                                full_text: Optional[str] = None) -> Tuple[List[Dict], List[Dict]]:
    """Filter out predictions that match already-documented diagnoses.
    Returns (kept, filtered) so we can show what was removed.

    Approach (informed by LLM-as-judge audit):
      1. Normalise both sides with _normalise_diagnosis() — strips POA/stage/
         class/BMI markers and canonicalises synonyms (HFrEF → systolic heart
         failure, AKI → acute kidney injury, etc.).
      2. Check for specificity upgrade first — always keep these even if they
         match a documented diagnosis.
      3. Filter as duplicate when:
           a. Normalised pred == normalised doc (exact match after canon), or
           b. Normalised pred is a substring of doc (doc is more specific), or
           c. Normalised doc is a substring of pred AND _is_specificity_upgrade
              returned False (pred only adds non-codable markers), or
           d. Term-level Jaccard overlap >= 0.75 on normalised text.
       Threshold lowered from 0.90 → 0.75 because normalisation now does most
       of the equivalence work (synonym expansion, marker stripping).
      4. Fallback: if `full_text` is supplied (raw discharge summary), check
         whether the normalised prediction appears as a whole-word phrase
         anywhere in the normalised full text. Stanford discharge summaries
         carry diagnoses in Stanford-specific patterns ("#Nutrition: Severe
         Protein Calorie Malnutrition") that the section extractor can miss.
         This is a gated check — applies only when the normalised prediction
         is at least 2 tokens AND at least one token is a codable clinical
         keyword, to avoid filtering short generic strings.
    """
    if not documented and not full_text:
        return predictions, []

    # Pre-normalise documented set once
    doc_normalised = [(d, _normalise_diagnosis(d)) for d in documented]

    # Pre-normalise full discharge summary (if provided) for fallback substring check
    full_normalised = _normalise_diagnosis(full_text) if full_text else ""

    # Codable clinical keywords — gating for fallback check to ensure we only
    # filter on substantive diagnosis phrases, not generic fragments.
    CODABLE_TOKENS = {
        'sepsis', 'pneumonia', 'anemia', 'malnutrition', 'encephalopathy',
        'infection', 'obesity', 'hypertension', 'diabetes', 'cardiomyopathy',
        'ulcer', 'injury', 'failure', 'embolism', 'thrombosis', 'fibrillation',
        'edema', 'ischemia', 'infarction', 'hemorrhage', 'bleed', 'disease',
        'syndrome', 'insufficiency', 'hyperkalemia', 'hypokalemia',
        'hyponatremia', 'hypernatremia', 'hypercalcemia', 'hypocalcemia',
        'hypomagnesemia', 'hypermagnesemia', 'hypophosphatemia',
        'hyperglycemia', 'hypoglycemia', 'ketoacidosis', 'acidosis',
        'alkalosis', 'dehydration', 'hypovolemia', 'shock', 'delirium',
        'dementia', 'depression', 'psychosis', 'seizure', 'stroke',
        'dysphagia', 'bacteremia', 'fungemia', 'osteomyelitis', 'cellulitis',
        'cystitis', 'pyelonephritis', 'abscess', 'pancreatitis', 'hepatitis',
        'cirrhosis', 'ascites', 'obstruction', 'perforation', 'stenosis',
        'cachexia', 'debility', 'deconditioning', 'decubitus', 'deficiency',
        'neoplasm', 'cancer', 'lymphoma', 'leukemia', 'metastasis',
        'thrombocytopenia', 'neutropenia', 'pancytopenia', 'coagulopathy',
    }

    stop = {'and', 'or', 'the', 'a', 'an', 'with', 'without',
            'due', 'to', 'of', 'in', 'on', 'secondary',
            'other', 'not', 'no', 'at', 'by', 'from', 'for'}

    kept = []
    filtered = []
    for pred in predictions:
        dx_raw = pred.get('diagnosis', '').strip()
        if not dx_raw:
            continue
        dx_norm = _normalise_diagnosis(dx_raw)
        if not dx_norm:
            kept.append(pred)
            continue
        dx_terms = set(w for w in dx_norm.split() if w not in stop)

        is_processed = False
        for doc_raw, doc_norm in doc_normalised:
            if not doc_norm:
                continue

            # 1. Always preserve codable specificity upgrades
            if _is_specificity_upgrade(dx_raw, doc_raw):
                pred_copy = dict(pred)
                pred_copy['is_specificity_upgrade'] = True
                kept.append(pred_copy)
                is_processed = True
                break

            reason = None

            # 2a. Exact match after normalisation
            if dx_norm == doc_norm:
                reason = 'exact match after normalisation'
            # 2b. Documented is broader and contains prediction (pred less specific)
            elif dx_norm in doc_norm:
                reason = 'prediction subsumed by documented'
            # 2c. Prediction contains documented but only adds non-codable markers
            #     (specificity_upgrade check already returned False above)
            elif doc_norm in dx_norm:
                reason = 'non-codable elaboration of documented'
            else:
                # 2d. Jaccard overlap on normalised terms
                doc_terms = set(w for w in doc_norm.split() if w not in stop)
                if not dx_terms or not doc_terms:
                    continue
                inter = len(dx_terms & doc_terms)
                union = len(dx_terms | doc_terms)
                jaccard = inter / union if union else 0
                coverage = inter / min(len(dx_terms), len(doc_terms))
                # Filter when either Jaccard >= 0.75 OR when the smaller side
                # is fully contained (coverage == 1) AND the larger adds only
                # 1-2 tokens — catches "hf" vs "heart failure" post-expansion.
                if jaccard >= 0.75:
                    reason = f'duplicate (jaccard={jaccard:.0%})'
                elif coverage == 1.0 and abs(len(dx_terms) - len(doc_terms)) <= 1:
                    reason = f'duplicate (full coverage of smaller side)'

            if reason:
                pred_copy = dict(pred)
                pred_copy['filter_reason'] = reason
                pred_copy['filter_matched_doc'] = doc_raw
                filtered.append(pred_copy)
                is_processed = True
                break

        if is_processed:
            continue

        # ------------------------------------------------------------------
        # Full-text fallback DISABLED (2026-04-15).
        # Caused −21pp malnutrition recall regression: Stanford notes mention
        # malnutrition in narrative nutrition sections, which the fallback
        # can't distinguish from formal diagnosis coding.
        # The normalisation + Jaccard improvements above are sufficient.
        # ------------------------------------------------------------------
        kept.append(pred)

    return kept, filtered


# ===========================================================================
# RESPONSE PARSING
# ===========================================================================

def _parse_llm_response(raw: str) -> List[Dict]:
    """Parse LLM JSON response, handling common formatting issues."""
    if not raw:
        return []

    # Try direct JSON parse
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting JSON array from response text
    match = re.search(r'\[[\s\S]*?\]', raw)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Try from code fences
    match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []


def _normalize(text: str) -> str:
    """Normalize diagnosis text for comparison."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# Clinical equivalents for fuzzy matching during voting
CLINICAL_EQUIVALENTS = {
    'malnutrition': ['protein calorie malnutrition', 'severe malnutrition',
                     'moderate malnutrition', 'cachexia', 'underweight'],
    'sepsis': ['severe sepsis', 'septic shock', 'urosepsis', 'septicemia'],
    'anemia': ['blood loss anemia', 'acute blood loss anemia',
               'iron deficiency anemia', 'chronic anemia'],
    'respiratory failure': ['hypoxic respiratory failure',
                            'acute respiratory failure', 'hypercapnic'],
    'heart failure': ['chf', 'congestive heart failure', 'systolic heart failure',
                      'diastolic heart failure', 'hfref', 'hfpef'],
    'acute kidney injury': ['aki', 'acute renal failure', 'renal failure'],
    'encephalopathy': ['metabolic encephalopathy', 'hepatic encephalopathy',
                       'delirium', 'altered mental status'],
    'pressure ulcer': ['pressure injury', 'decubitus ulcer', 'bed sore'],
    'hyponatremia': ['low sodium'],
    'hyperkalemia': ['high potassium'],
    'obesity': ['morbid obesity', 'severe obesity', 'class iii obesity'],
    'lactic acidosis': ['elevated lactate', 'hyperlactatemia'],
    'thrombocytopenia': ['low platelets', 'pancytopenia'],
    'pulmonary edema': ['flash pulmonary edema', 'cardiogenic pulmonary edema'],
    'coagulopathy': ['dic', 'disseminated intravascular coagulation'],
}


def _diagnoses_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Check if two diagnoses are clinically similar (for vote aggregation)."""
    na, nb = _normalize(a), _normalize(b)

    if na == nb:
        return True
    if na in nb or nb in na:
        return True

    # Clinical equivalents
    for base, equivs in CLINICAL_EQUIVALENTS.items():
        a_match = base in na or any(e in na for e in equivs)
        b_match = base in nb or any(e in nb for e in equivs)
        if a_match and b_match:
            return True

    # Word overlap
    stop = {'and', 'or', 'the', 'a', 'an', 'with', 'without', 'due', 'to',
            'of', 'in', 'on', 'acute', 'chronic', 'type', 'by', 'from',
            'unspecified', 'other', 'not', 'no'}
    wa = set(na.split()) - stop
    wb = set(nb.split()) - stop
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(len(wa), len(wb))
    return overlap >= threshold


# ===========================================================================
# MAIN ENGINE
# ===========================================================================

class CDIEngine:
    """Production CDI prediction engine."""

    def __init__(self, api_key: str, model: str = "gpt-5"):
        self.api_key = api_key
        self.model = model

    def _build_user_content(self, discharge_summary: str,
                            progress_note: Optional[str] = None,
                            hp_note: Optional[str] = None,
                            consult_note: Optional[str] = None,
                            ed_note: Optional[str] = None,
                            progress_notes: Optional[List[str]] = None,
                            consult_notes: Optional[List[str]] = None,
                            procedure_notes: Optional[List[str]] = None,
                            ip_consult_note: Optional[str] = None) -> str:
        """Assemble all clinical notes into a single user prompt.

        Supports both legacy (single progress_note/consult_note) and expanded
        dataset (multiple notes of each type from cdi_expanded_notes.csv).
        """
        content = USER_PREFIX + "DISCHARGE SUMMARY:\n" + discharge_summary

        # H&P — admission workup, baseline labs, initial assessment
        if hp_note:
            content += f"\n\nHISTORY & PHYSICAL:\n{hp_note}"

        # ED note — presenting complaint, initial labs/imaging
        if ed_note:
            content += f"\n\nEMERGENCY DEPARTMENT NOTE:\n{ed_note}"

        # Progress notes — daily assessments with labs, vitals, clinical trajectory
        all_progress = []
        if progress_notes:
            all_progress.extend([n for n in progress_notes if n])
        elif progress_note:
            all_progress.append(progress_note)
        for i, pn in enumerate(all_progress, 1):
            label = "PROGRESS NOTE" if len(all_progress) == 1 else f"PROGRESS NOTE {i}"
            content += f"\n\n{label}:\n{pn}"

        # Consult notes — specialist consultations
        all_consults = []
        if consult_notes:
            all_consults.extend([n for n in consult_notes if n])
        elif consult_note:
            all_consults.append(consult_note)
        for i, cn in enumerate(all_consults, 1):
            label = "CONSULTATION NOTE" if len(all_consults) == 1 else f"CONSULTATION NOTE {i}"
            content += f"\n\n{label}:\n{cn}"

        # Procedure notes — operative/procedural details and complications
        if procedure_notes:
            for i, pn in enumerate([n for n in procedure_notes if n], 1):
                label = "PROCEDURE NOTE" if i == 1 and len(procedure_notes) == 1 else f"PROCEDURE NOTE {i}"
                content += f"\n\n{label}:\n{pn}"

        # Inpatient consult note
        if ip_consult_note:
            content += f"\n\nINPATIENT CONSULT NOTE:\n{ip_consult_note}"

        return content

    def _single_pass(self, user_content: str, temperature: float = 0.2,
                      raise_on_error: bool = True) -> List[Dict]:
        """Run a single LLM prediction pass.

        Args:
            raise_on_error: If False, returns empty list on failure (for voting).
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = _call_llm(messages, self.api_key, model=self.model,
                             temperature=temperature)
            return _parse_llm_response(raw)
        except Exception as e:
            if raise_on_error:
                raise
            print(f"    Voting pass failed (will skip): {e}")
            return []

    def _vote(self, all_runs: List[List[Dict]], threshold: int) -> List[Dict]:
        """Aggregate multiple prediction runs via majority voting.

        Returns diagnoses that appear in >= threshold runs, with:
        - confidence: high (all runs), medium (>=threshold), low (below)
        - vote_count: number of runs that included this diagnosis
        - best entry: the most detailed version from any run
        """
        # Group by normalised diagnosis
        buckets = []  # list of {norm, count, best_entry, evidence_set}

        for run_preds in all_runs:
            seen_this_run = set()
            for pred in run_preds:
                dx_name = pred.get("diagnosis", str(pred)) if isinstance(pred, dict) else str(pred)
                norm = _normalize(dx_name)

                if norm in seen_this_run:
                    continue
                seen_this_run.add(norm)

                # Find matching bucket
                matched_bucket = None
                for bucket in buckets:
                    if _diagnoses_similar(norm, bucket["norm"]):
                        matched_bucket = bucket
                        break

                if matched_bucket:
                    matched_bucket["count"] += 1
                    # Keep the entry with the longest evidence
                    if isinstance(pred, dict):
                        old_ev = matched_bucket["best_entry"].get("evidence", "")
                        new_ev = pred.get("evidence", "")
                        if len(new_ev) > len(old_ev):
                            matched_bucket["best_entry"] = pred
                else:
                    buckets.append({
                        "norm": norm,
                        "count": 1,
                        "best_entry": pred if isinstance(pred, dict) else {"diagnosis": str(pred)},
                    })

        # Filter by vote threshold and assign confidence
        num_runs = len(all_runs)
        results = []
        for bucket in sorted(buckets, key=lambda b: b["count"], reverse=True):
            if bucket["count"] < threshold:
                continue

            entry = dict(bucket["best_entry"])
            entry["vote_count"] = bucket["count"]
            entry["vote_total"] = num_runs

            if bucket["count"] == num_runs:
                entry["confidence"] = "high"
            elif bucket["count"] >= threshold:
                entry["confidence"] = "medium"
            else:
                entry["confidence"] = "low"

            results.append(entry)

        return results

    def _enrich(self, predictions: List[Dict]) -> List[Dict]:
        """Add DRG impact, revenue estimate, and category metadata."""
        enriched = []
        for pred in predictions:
            dx = pred.get("diagnosis", "")
            drg = classify_drg_impact(dx)
            cat = pred.get("category", "other").lower()
            meta = CATEGORY_META.get(cat, CATEGORY_META["other"])

            enriched.append({
                "diagnosis": dx,
                "icd10_code": pred.get("icd10_code", ""),
                "category": cat,
                "category_label": meta["label"],
                "category_icon": meta["icon"],
                "category_color": meta["color"],
                "drg_impact": drg,
                "revenue_impact": estimate_revenue_impact(drg),
                "confidence": pred.get("confidence", "medium"),
                "evidence": pred.get("evidence", ""),
                "reasoning": pred.get("reasoning", pred.get("query_reasoning", "")),
                "vote_count": pred.get("vote_count"),
                "vote_total": pred.get("vote_total"),
            })

        # Sort: MCC first, then CC, then non-CC; within each tier, high confidence first
        tier_order = {"MCC": 0, "CC": 1, "non-CC": 2}
        conf_order = {"high": 0, "medium": 1, "low": 2}
        enriched.sort(key=lambda x: (
            tier_order.get(x["drg_impact"], 3),
            conf_order.get(x["confidence"], 3),
        ))

        return enriched

    def analyse(self, discharge_summary: str,
                progress_note: Optional[str] = None,
                hp_note: Optional[str] = None,
                consult_note: Optional[str] = None,
                ed_note: Optional[str] = None,
                progress_notes: Optional[List[str]] = None,
                consult_notes: Optional[List[str]] = None,
                procedure_notes: Optional[List[str]] = None,
                ip_consult_note: Optional[str] = None,
                mode: str = "balanced") -> Dict:
        """
        Run CDI analysis on clinical notes.

        Args:
            discharge_summary: Required. The discharge summary text.
            progress_note: Optional single progress note (legacy compat).
            hp_note: Optional H&P note.
            consult_note: Optional single consultation note (legacy compat).
            ed_note: Optional Emergency Department note.
            progress_notes: List of up to 3 progress notes.
            consult_notes: List of up to 2 consult notes.
            procedure_notes: List of up to 2 procedure notes.
            ip_consult_note: Optional inpatient consult note.
            mode: "fast" (1 call), "balanced" (3 calls, voting), "high_recall" (5 calls).

        Returns:
            dict with keys:
                predictions: List of enriched diagnosis predictions
                summary: dict with counts by DRG tier and category
                metadata: timing, mode, model info
        """
        start_time = datetime.now()
        user_content = self._build_user_content(
            discharge_summary, progress_note, hp_note, consult_note,
            ed_note=ed_note,
            progress_notes=progress_notes,
            consult_notes=consult_notes,
            procedure_notes=procedure_notes,
            ip_consult_note=ip_consult_note,
        )

        if mode == "fast":
            raw_preds = self._single_pass(user_content, temperature=0.2)
            # Assign confidence based on LLM's own confidence field
            predictions = raw_preds

        elif mode == "balanced":
            # Self-consistency: 3 runs, keep ≥2/3 votes
            # Fault-tolerant — if a pass fails, vote with fewer runs
            runs = []
            for i in range(3):
                preds = self._single_pass(user_content, temperature=0.7,
                                           raise_on_error=False)
                if preds:  # only include successful runs
                    runs.append(preds)
                if i < 2:
                    time.sleep(1)
            if len(runs) >= 2:
                predictions = self._vote(runs, threshold=2)
            elif len(runs) == 1:
                predictions = runs[0]  # fallback to single pass
            else:
                predictions = []

        elif mode == "high_recall":
            # 5 runs, keep ≥2/5 votes (lower threshold = more recall)
            runs = []
            for i in range(5):
                preds = self._single_pass(user_content, temperature=0.7,
                                           raise_on_error=False)
                if preds:
                    runs.append(preds)
                if i < 4:
                    time.sleep(1)
            if len(runs) >= 2:
                predictions = self._vote(runs, threshold=2)
            elif len(runs) == 1:
                predictions = runs[0]
            else:
                predictions = []

        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'fast', 'balanced', or 'high_recall'.")

        # Filter BYPASSED (2026-04-17): LLM judge on 300-sample (17 Apr run)
        # showed LEGITIMATE_CDI=49.0%, ALREADY_CODED=16.3%. Filter was not
        # delivering the precision gain it was meant to — ALREADY_CODED is
        # still the second-largest bucket post-filter, and the filter cost
        # ~2.76pp recall vs 13 Apr baseline (paired 726-case subset).
        # Reverting to clean baseline before applying Phase D progress-note
        # prompt. Filter needs a structural rethink, not a tweak.
        # We still extract documented diagnoses for the summary stat, but
        # skip the filter call. Restore the filter by uncommenting below.
        documented = _extract_documented_diagnoses(discharge_summary)
        # predictions, filtered_out = _filter_already_documented(
        #     predictions, documented, full_text=discharge_summary)
        filtered_out = []

        # Enrich with DRG impact, categories, revenue estimates
        enriched = self._enrich(predictions)

        # Build summary
        mcc_count = sum(1 for p in enriched if p["drg_impact"] == "MCC")
        cc_count = sum(1 for p in enriched if p["drg_impact"] == "CC")
        non_cc_count = sum(1 for p in enriched if p["drg_impact"] == "non-CC")
        high_conf = sum(1 for p in enriched if p["confidence"] == "high")

        categories = {}
        for p in enriched:
            cat = p["category_label"]
            categories[cat] = categories.get(cat, 0) + 1

        elapsed = (datetime.now() - start_time).total_seconds()

        return {
            "predictions": enriched,
            "summary": {
                "total_findings": len(enriched),
                "mcc_count": mcc_count,
                "cc_count": cc_count,
                "non_cc_count": non_cc_count,
                "high_confidence_count": high_conf,
                "categories": categories,
                "estimated_revenue_impact": (
                    f"${mcc_count * 10000 + cc_count * 3500:,}"
                    if mcc_count + cc_count > 0 else "$0"
                ),
            },
            "metadata": {
                "model": self.model,
                "mode": mode,
                "api_calls": {"fast": 1, "balanced": 3, "high_recall": 5}[mode],
                "elapsed_seconds": round(elapsed, 1),
                "timestamp": datetime.now().isoformat(),
                "engine_version": "1.1.0",
                "prompt_variant": "v15_cdi_agent_style",
                "voting": mode != "fast",
                "filtered_already_documented": filtered_out,
                "filtered_count": len(filtered_out),
                "documented_diagnoses_found": len(documented),
            },
        }


# ===========================================================================
# CLI for quick testing
# ===========================================================================

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="CDI Coding Intelligence Engine")
    parser.add_argument("--api-key", required=True, help="Stanford SecureGPT API key")
    parser.add_argument("--model", default="gpt-5", choices=["gpt-5", "gpt-4.1", "gpt-5-nano"])
    parser.add_argument("--mode", default="fast", choices=["fast", "balanced", "high_recall"])
    parser.add_argument("--input", help="Path to discharge summary text file")
    args = parser.parse_args()

    if args.input:
        with open(args.input) as f:
            text = f.read()
    else:
        print("Enter discharge summary (Ctrl+D to finish):")
        text = sys.stdin.read()

    engine = CDIEngine(api_key=args.api_key, model=args.model)
    result = engine.analyse(discharge_summary=text, mode=args.mode)

    print(f"\n{'='*70}")
    print(f"CDI CODING INTELLIGENCE — {result['summary']['total_findings']} findings")
    print(f"Mode: {args.mode} | Model: {args.model} | Time: {result['metadata']['elapsed_seconds']}s")
    print(f"{'='*70}\n")

    for i, p in enumerate(result["predictions"], 1):
        conf_badge = {"high": "★★★", "medium": "★★☆", "low": "★☆☆"}.get(p["confidence"], "")
        vote_str = f" [{p['vote_count']}/{p['vote_total']} votes]" if p.get("vote_count") else ""
        print(f"{i}. [{p['drg_impact']}] {p['diagnosis']}")
        print(f"   ICD-10: {p['icd10_code']} | {p['category_label']} | {conf_badge}{vote_str}")
        print(f"   Revenue: {p['revenue_impact']}")
        if p.get("evidence"):
            ev = p["evidence"][:150] + "..." if len(p.get("evidence", "")) > 150 else p.get("evidence", "")
            print(f"   Evidence: {ev}")
        print()

    print(f"Summary: {result['summary']['mcc_count']} MCCs, {result['summary']['cc_count']} CCs")
    print(f"Estimated revenue impact: {result['summary']['estimated_revenue_impact']}")
