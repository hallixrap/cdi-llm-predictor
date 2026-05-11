#!/usr/bin/env python3
"""
cdi_agent_runner.py — Agentic CDI predictor on Stanford's PHI-safe gateway.

Replicates the Anthropic-built cdi-agent architecture (multi-turn tool use +
structured-output tool + format-validation step) but routed through
Stanford's AWS Bedrock endpoint at aihubapi.stanfordhealthcare.org so the
pipeline is BAA-covered for PHI/PII.

Design notes
------------
The Anthropic-built `cdi-agent` uses claude-agent-sdk → which wraps the
public Anthropic Python lib → which hits api.anthropic.com. Stanford's
Bedrock has a DIFFERENT request body shape:
  - anthropic_version: "bedrock-2023-05-31"
  - model is in the URL path, NOT in the body
  - tools/tool_choice live in the body just like Anthropic native
  - response wraps Anthropic-native content[] blocks

Rather than running a translation proxy, we drive the tool-use loop
directly with `_call_bedrock_tool_loop` (a small local helper that
implements the same multi-turn pattern as the Agent SDK).

What we replicate from the original cdi-agent
---------------------------------------------
1. System prompt + cdi-analysis SKILL.md content folded together.
2. Custom `report_diagnoses` tool — Claude must call this to surface results.
3. Format-validation step (regex check on ICD-10 codes) before the loop
   accepts the report. If invalid, send a "validation failed" tool_result
   back so Claude can correct.
4. Multi-note input — matches CDIEngine's input shape so the same evaluator
   row → prediction wiring applies.

What we drop (for v1, by design)
--------------------------------
- ICD-10 MCP (deepsense.ai) — adds a network dependency the sandbox can't
  test, and adds latency. We rely on Claude's internal ICD-10 knowledge
  for this experiment. If precision suffers vs the engine, add the MCP.
- Skill-loading semantics — we inline the SKILL.md content. Functionally
  equivalent for a single-skill agent.

Output shape
------------
predict() returns the same dict shape as CDIEngine.analyse(), so the
evaluator can ingest agent predictions via the same code path:
    {"predictions": [...], "summary": {...}, "metadata": {...}}

Usage
-----
    from cdi_agent_runner import CDIAgentRunner
    runner = CDIAgentRunner(api_key="...", model="claude-opus-4-7")
    result = runner.analyse(
        discharge_summary="...",
        hp_note="...",       # optional
        progress_notes=[...] # optional
    )
"""
from __future__ import annotations

import json
import re
import time
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from cdi_engine import (
    BEDROCK_MODEL_IDS,
    BEDROCK_BASE,
    classify_drg_impact,
    estimate_revenue_impact,
    CATEGORY_META,
)


# ===========================================================================
# AGENT SYSTEM PROMPT
# ===========================================================================
# Folds the original cdi-agent SYSTEM_PROMPT + the cdi-analysis SKILL.md
# content into a single system prompt (since we don't have skill-loading
# without the Agent SDK). Matches the spirit of the Anthropic-built agent.

AGENT_SYSTEM_PROMPT = """You are a clinical documentation integrity (CDI) assistant for Stanford Healthcare. Your role is to identify clinically-supported diagnoses that are present in the patient's record but were not captured in the attending's discharge diagnoses — particularly those that would increase case complexity or DRG weight.

# Critical Instructions

**DO NOT FLAG ALREADY-DOCUMENTED CONDITIONS.**
Before suggesting ANY diagnosis, check the Discharge Diagnoses, Problem List, and Assessment sections. If a condition is already listed (even with different wording), skip it. Only flag:
1. Diagnoses NOT in any diagnosis/problem list but with clinical evidence in labs, vitals, or treatments
2. Diagnoses listed but with INSUFFICIENT SPECIFICITY that would change the ICD-10 code

**BILLING CODE OPTIMIZATION:**
Always suggest the HIGHEST SPECIFICITY supported by evidence:
- "Severe protein-calorie malnutrition" (E43) > "Malnutrition, unspecified" (E46.9)
- "Sepsis, [organism]" > "Sepsis, unspecified"
- "Acute hypoxic respiratory failure" (J96.01) > "Respiratory failure, unspecified" (J96.90)
- "Pressure ulcer, Stage 3, Sacral, POA" > "Pressure ulcer, unspecified stage"
- "Acute on Chronic Diastolic Heart Failure" > "Heart failure"
- "Type 2 diabetes with hyperglycemia" (E11.65) > "Type 2 diabetes without complications" (E11.9)

# MCC/CC Hierarchy & DRG Impact

**MCCs (Major Complications/Comorbidities) = HIGH impact**
- Sepsis (A41.x), severe sepsis (R65.20), septic shock (R65.21)
- Acute kidney injury requiring dialysis (N17.2)
- Acute respiratory failure (J96.0x, J96.2x)
- Acute myocardial infarction (I21.x)
- Severe malnutrition (E43, E44.x)
- Pressure ulcers stage 3-4

**CCs (Complications/Comorbidities) = MEDIUM impact**
- Acute kidney injury, unspecified (N17.9)
- Chronic kidney disease stage 3+ (N18.3-N18.6)
- Anemia in chronic disease (D63.x), acute blood loss anemia (D62)
- COPD with exacerbation (J44.1)
- Congestive heart failure (I50.x)

# Top CDI Query Categories — Specific Criteria

## 1. Electrolyte Abnormalities (HIGH PRIORITY)
- Hypovolemic Hyponatremia: Na <130 + IV 0.9% NS treatment
- Hypernatremia: Na >145 + IV D5W or 0.225% NaCl, OR two labs >145
- Hypokalemia: K <3.5 + PO/IV Potassium
- Hyperkalemia: K >5.5 + Treatment (calcium gluconate, NaHCO3, Kayexalate, dialysis)
- Hypocalcemia: Ca <8.4 (or iCa <1.12) + IV Calcium. Exclude if albumin <3.
- Hypercalcemia: Ca >10.5 + Bisphosphonate/Calcitonin/Cinacalcet, OR two labs >10.5
- Hypomagnesemia: Mg <1.6 + IV/PO Magnesium
- Hypophosphatemia: Phos <2.5 + IV/PO phosphate

## 2. Anemia
- Acute Blood Loss Anemia (operative): 2-pt Hgb drop + Hgb <13(M)/<12(F) + EBL >250ml
- Iron Deficiency Anemia: Hgb <12(M)/<11.7(F) + PO/IV Iron treatment
- Anemia of Chronic Disease: Chronic low Hgb + chronic disease (cancer, CKD, inflammatory)

## 3. Malnutrition
- Severe protein-calorie (E43): BMI <18.5 + Albumin <3.0 + weight loss
- Moderate (E44.0), Mild (E44.1)
- Look for: BMI, albumin, weight loss, nutritional support, dietitian consults, TPN/tube feeds

## 4. Hypoalbuminemia
- Albumin <3.2 g/dL on at least TWO panels (normal 3.5-5.0)

## 5. Sepsis (HIGH PRIORITY — Often Missed)
Treatment patterns strongly suggesting sepsis:
- Blood cultures + broad-spectrum IV antibiotics
- "Sepsis protocol" initiated; lactate ordered (especially >2 mmol/L)
- Aggressive IV fluids (>30 mL/kg or "fluid bolus"); ICU transfer for infection
Common patterns: UTI + AMS + tachycardia → Urosepsis. Pneumonia + hypoxia + IV abx → Sepsis.
Severity: Sepsis + organ dysfunction → Severe sepsis. Sepsis + pressors → Septic shock.

## 6. Pathology Results (Often Missed)
Query when pathology/cytology/biopsy findings are NOT in discharge diagnoses:
- "Pathology shows..." or "Biopsy confirmed..." → Query if not in dx list
- Malignant findings, metastatic disease, specific tumor types
- "Malignant pleural effusion, confirmed, as noted in cytology report"

## 7. Respiratory Failure
- Criteria: O2 requirement + PaO2 <60 OR O2 sat <90% OR PaCO2 >45
- "Respiratory distress" but not "acute respiratory failure" → Query
- Specify: acute vs chronic, hypoxic (J96.01) vs hypercapnic (J96.21)

## 8. Pressure Ulcer/Injury
- MUST specify Stage (1-4, unstageable, deep tissue) + Location + POA status
- Nursing notes it but physician doesn't code it → Query

## 9. Coagulation Disorders
- Thrombocytopenia: Platelets <145 K/uL on at least TWO panels
- Pancytopenia: Hgb low + WBC <4.0 + Platelets <150 on TWO panels

## 10. Heart Failure (Specificity Required)
- Acuity (REQUIRED): Acute / Chronic / Acute on Chronic
- Type (REQUIRED): Systolic (HFrEF, EF <40%) / Diastolic (HFpEF, EF ≥50%) / Combined
- "CHF exacerbation" → Query for "Acute on Chronic [Systolic/Diastolic] Heart Failure"
- Cardiogenic Shock: SBP <90 + hypoperfusion + cardiac cause + pressors/inotropes
- Type 2 MI / Demand Ischemia: Trop elevated + clear stressor (sepsis, hypotension, anemia) WITHOUT ACS — I21.A1.

## 11. Other High-Value
- AKI: Cr change >0.3 mg/dL with abnormal Cr. Exclude if CKD in active problem list.
- Lactic Acidosis: Lactate >4 mmol/L + IV fluids OR IV NaHCO3
- DM with Hyperglycemia: DM + Glucose >180 + diabetes meds → E11.65
- Steroid-Induced Hyperglycemia: Glucose >180 + steroid use WITHOUT prior DM
- Encephalopathy: AMS + metabolic cause (metabolic, toxic, hepatic, septic)
- Cachexia: Wasting + weight loss + chronic disease
- Debridement: Specify depth (excisional to bone > muscle > subcutaneous)

# Workflow

1. Read ALL provided notes (discharge, H&P, ED, progress, consult, procedure).
2. Build a list of clinical evidence (labs, vitals, treatments, specialist findings) NOT yet captured in discharge diagnoses.
3. For each candidate, identify the most specific ICD-10 code supported by evidence.
4. Prioritize by DRG/reimbursement impact (MCCs > CCs > non-CCs).
5. When complete, call the `report_diagnoses` tool with ALL clinically-significant missed diagnoses (not capped at 5) — the evaluator scores against ground-truth queries.

Every diagnosis must be traceable to documented clinical evidence. Do not invent findings or rely on risk factors alone.
"""


# ===========================================================================
# TOOL DEFINITIONS (Anthropic native format — Bedrock-compatible)
# ===========================================================================

REPORT_DIAGNOSES_TOOL = {
    "name": "report_diagnoses",
    "description": (
        "Report all clinically-significant missed diagnoses found in the chart. "
        "Call this exactly once when your analysis is complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnoses": {
                "type": "array",
                "description": "List of missed diagnoses, each with the required fields below.",
                "items": {
                    "type": "object",
                    "properties": {
                        "diagnosis": {"type": "string", "description": "Specific clinical diagnosis name."},
                        "icd10_code": {"type": "string", "description": "Most specific ICD-10-CM code (format: A12.345)."},
                        "category": {
                            "type": "string",
                            "enum": [
                                "sepsis", "respiratory", "anemia", "malnutrition",
                                "electrolytes", "cardiac", "renal", "coagulation",
                                "pressure_ulcer", "encephalopathy", "obesity", "other",
                            ],
                        },
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "evidence": {"type": "string", "description": "Specific clinical evidence from the notes."},
                    },
                    "required": ["diagnosis", "icd10_code", "category", "confidence", "evidence"],
                },
            },
            "rationale": {
                "type": "string",
                "description": "Brief overall explanation of the prioritisation across the diagnoses.",
            },
        },
        "required": ["diagnoses", "rationale"],
    },
}

# Standard ICD-10-CM format: letter + 2 digits + optional . + 1-4 chars
# (chars after the dot can be digits or letters for some codes like I21.A1)
ICD10_REGEX = re.compile(r"^[A-Z]\d{2}(\.[A-Z0-9]{1,4})?$")


# ===========================================================================
# CDI AGENT RUNNER
# ===========================================================================

class CDIAgentRunner:
    """Agentic CDI predictor using Claude on Stanford's Bedrock gateway.

    Public API matches CDIEngine.analyse() return shape so the evaluator
    can ingest agent results via the same row → prediction code path.
    """

    def __init__(self, api_key: str, model: str = "claude-opus-4-7",
                 max_turns: int = 8, max_tokens: int = 8000):
        if not model.startswith("claude"):
            raise ValueError(f"CDIAgentRunner only supports Claude models, got {model!r}")
        if model not in BEDROCK_MODEL_IDS:
            raise ValueError(f"Unknown Claude model {model!r}. Known: {list(BEDROCK_MODEL_IDS)}")
        self.api_key = api_key
        self.model = model
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.bedrock_id = BEDROCK_MODEL_IDS[model]
        self.url = BEDROCK_BASE.format(self.bedrock_id)

    # --- Bedrock call --------------------------------------------------

    def _bedrock_call(self, system: str, messages: list, tools: list) -> dict:
        """Single Bedrock invocation. Returns parsed JSON response."""
        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        for attempt in range(5):
            try:
                resp = requests.post(self.url, headers=headers,
                                     data=json.dumps(body), timeout=300)
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(2 ** (attempt + 1) + random.random() * 2)
                    continue
                if resp.status_code != 200:
                    raise RuntimeError(f"Bedrock {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                time.sleep(2 ** (attempt + 1) + random.random() * 2)
        raise RuntimeError("Bedrock call failed after 5 retries")

    # --- Tool-use loop -------------------------------------------------

    def _run_tool_loop(self, user_content: str) -> Tuple[List[dict], str, int]:
        """Drive the multi-turn tool-use loop. Returns (diagnoses, rationale, turns_used).

        Loop terminates when:
          - Claude calls report_diagnoses with valid ICD-10 codes (success)
          - Claude calls report_diagnoses with INVALID codes — we send a
            tool_result with validation failure and let Claude retry.
          - max_turns reached (failure)
          - Claude stops without calling report_diagnoses (failure)
        """
        messages = [{"role": "user", "content": user_content}]
        tools = [REPORT_DIAGNOSES_TOOL]

        for turn in range(self.max_turns):
            data = self._bedrock_call(AGENT_SYSTEM_PROMPT, messages, tools)
            content_blocks = data.get("content", [])
            stop_reason = data.get("stop_reason", "unknown")

            # Append assistant turn to message history
            messages.append({"role": "assistant", "content": content_blocks})

            # Look for tool_use blocks (Claude calling report_diagnoses)
            tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]

            if not tool_uses:
                # Claude responded with text only — no tool call. Likely
                # finished without reporting. Push it to call the tool.
                if stop_reason == "end_turn":
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "text",
                            "text": "Please call the report_diagnoses tool now with your findings.",
                        }],
                    })
                    continue
                # Otherwise we keep looping (model may be reasoning)
                continue

            for tu in tool_uses:
                if tu["name"] != "report_diagnoses":
                    continue  # Unexpected tool — ignore
                args = tu.get("input", {})
                diagnoses = args.get("diagnoses", [])
                rationale = args.get("rationale", "")

                # Validate ICD-10 format on every code
                invalid = [d.get("icd10_code", "") for d in diagnoses
                           if not ICD10_REGEX.match(d.get("icd10_code", ""))]

                if invalid:
                    # Mirror the Anthropic agent's validation hook — send a
                    # tool_result back saying validation failed; let Claude retry.
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": (
                                f"Validation failed: invalid ICD-10 code format(s) "
                                f"detected: {invalid}. Standard format is letter + 2 digits + "
                                f"optional decimal (e.g. N17.9, J96.01, I21.A1). "
                                f"Please correct and resubmit by calling report_diagnoses again."
                            ),
                            "is_error": True,
                        }],
                    })
                    continue  # next turn

                # Valid — return successfully
                return diagnoses, rationale, turn + 1

        # Exhausted max turns without success
        raise RuntimeError(f"Agent did not produce valid report after {self.max_turns} turns")

    # --- User content assembly (matches CDIEngine pattern) -------------

    def _build_user_content(self, discharge_summary: str,
                            hp_note: Optional[str] = None,
                            ed_note: Optional[str] = None,
                            progress_notes: Optional[List[str]] = None,
                            consult_notes: Optional[List[str]] = None,
                            procedure_notes: Optional[List[str]] = None,
                            ip_consult_note: Optional[str] = None,
                            progress_note: Optional[str] = None,
                            consult_note: Optional[str] = None) -> str:
        parts = ["DISCHARGE SUMMARY:\n" + discharge_summary]

        if hp_note:
            parts.append("\n\nHISTORY & PHYSICAL:\n" + hp_note)
        if ed_note:
            parts.append("\n\nEMERGENCY DEPARTMENT NOTE:\n" + ed_note)

        all_progress = list(progress_notes or [])
        if not all_progress and progress_note:
            all_progress.append(progress_note)
        for i, n in enumerate([p for p in all_progress if p], 1):
            label = "PROGRESS NOTE" if len(all_progress) == 1 else f"PROGRESS NOTE {i}"
            parts.append(f"\n\n{label}:\n{n}")

        all_consults = list(consult_notes or [])
        if not all_consults and consult_note:
            all_consults.append(consult_note)
        for i, n in enumerate([c for c in all_consults if c], 1):
            label = "CONSULT NOTE" if len(all_consults) == 1 else f"CONSULT NOTE {i}"
            parts.append(f"\n\n{label}:\n{n}")

        for i, n in enumerate([p for p in (procedure_notes or []) if p], 1):
            label = "PROCEDURE NOTE" if i == 1 and len(procedure_notes) == 1 else f"PROCEDURE NOTE {i}"
            parts.append(f"\n\n{label}:\n{n}")

        if ip_consult_note:
            parts.append("\n\nINPATIENT CONSULT NOTE:\n" + ip_consult_note)

        body = "".join(parts)
        return (
            "Analyse this clinical encounter for missed or under-specified diagnoses. "
            "Review all available notes, then call the report_diagnoses tool with your findings.\n\n"
            + body
        )

    # --- Public API ----------------------------------------------------

    def analyse(self, discharge_summary: str, **note_kwargs) -> dict:
        """Drop-in replacement for CDIEngine.analyse(). Same return shape."""
        start = datetime.now()
        user_content = self._build_user_content(discharge_summary, **note_kwargs)

        try:
            diagnoses, rationale, turns_used = self._run_tool_loop(user_content)
        except RuntimeError as e:
            # Agent failed — return empty predictions with error metadata
            return {
                "predictions": [],
                "summary": {
                    "total_findings": 0,
                    "mcc_count": 0,
                    "cc_count": 0,
                    "non_cc_count": 0,
                    "high_confidence_count": 0,
                    "categories": {},
                    "estimated_revenue_impact": "$0",
                    "rationale": "",
                },
                "metadata": {
                    "model": self.model,
                    "mode": "agent",
                    "agent_error": str(e),
                    "elapsed_seconds": round((datetime.now() - start).total_seconds(), 1),
                    "timestamp": datetime.now().isoformat(),
                    "engine_version": "agent-1.0.0",
                    "turns_used": 0,
                },
            }

        # Enrich each prediction with DRG impact + revenue (matches CDIEngine output)
        enriched = []
        for d in diagnoses:
            dx = d.get("diagnosis", "")
            drg = classify_drg_impact(dx)
            cat = d.get("category", "other")
            cat_meta = CATEGORY_META.get(cat, CATEGORY_META["other"])
            enriched.append({
                "diagnosis": dx,
                "icd10_code": d.get("icd10_code", ""),
                "category": cat,
                "category_label": cat_meta["label"],
                "category_icon": cat_meta["icon"],
                "category_color": cat_meta["color"],
                "confidence": d.get("confidence", "medium"),
                "evidence": d.get("evidence", ""),
                "drg_impact": drg,
                "revenue_estimate": estimate_revenue_impact(drg),
            })

        mcc = sum(1 for p in enriched if p["drg_impact"] == "MCC")
        cc = sum(1 for p in enriched if p["drg_impact"] == "CC")
        non_cc = sum(1 for p in enriched if p["drg_impact"] == "non-CC")
        high_conf = sum(1 for p in enriched if p["confidence"] == "high")

        cats: Dict[str, int] = {}
        for p in enriched:
            cats[p["category_label"]] = cats.get(p["category_label"], 0) + 1

        elapsed = (datetime.now() - start).total_seconds()
        return {
            "predictions": enriched,
            "summary": {
                "total_findings": len(enriched),
                "mcc_count": mcc,
                "cc_count": cc,
                "non_cc_count": non_cc,
                "high_confidence_count": high_conf,
                "categories": cats,
                "estimated_revenue_impact": (
                    f"${mcc * 10000 + cc * 3500:,}"
                    if mcc + cc > 0 else "$0"
                ),
                "rationale": rationale,
            },
            "metadata": {
                "model": self.model,
                "mode": "agent",
                "elapsed_seconds": round(elapsed, 1),
                "timestamp": datetime.now().isoformat(),
                "engine_version": "agent-1.0.0",
                "turns_used": turns_used,
                "max_turns": self.max_turns,
            },
        }


# ===========================================================================
# CLI for quick testing — single discharge summary file
# ===========================================================================

def _cli():
    import argparse, os, sys
    p = argparse.ArgumentParser(description="CDI agent runner — single-case test")
    p.add_argument("summary", help="Path to discharge summary .txt file")
    p.add_argument("--model", default="claude-opus-4-7",
                   choices=list(BEDROCK_MODEL_IDS))
    p.add_argument("--output", help="Optional JSON output path")
    args = p.parse_args()

    api_key = os.environ.get("STANFORD_API_KEY")
    if not api_key:
        print("ERROR: STANFORD_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(2)

    with open(args.summary) as f:
        summary = f.read()

    print(f"Running CDI agent ({args.model}) on {args.summary}...")
    runner = CDIAgentRunner(api_key=api_key, model=args.model)
    result = runner.analyse(discharge_summary=summary)

    print(json.dumps(result, indent=2))
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    _cli()
