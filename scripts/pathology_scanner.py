#!/usr/bin/env python3
"""
pathology_scanner.py — Phase E v1: structural pathology gap detection.

The Anthropic-built cdi-agent's SKILL.md flagged pathology results as a
high-yield CDI category ("Physicians often write the discharge summary
before pathology returns"). The 22 Apr cluster analysis confirmed it:
the cancer_pathology cluster is 88 ground-truth queries with 19% recall
(largest single recall lever — ~71 potential TPs).

Phase C attempted this with a prompt block ("ALWAYS query pathology-
confirmed diagnoses") — failed badly, inflated ALREADY_CODED by 9pp.
The diagnosis: prompt-level "always query" rules can't check whether a
finding is already in the discharge diagnoses. That's a structural
problem, not a prompt problem.

Phase E v1 solution: pipeline pass that does the comparison in code,
not in prompt language:
    1. Detect pathology-report-style segments in available notes (regex)
    2. Send those segments (plus discharge dx for context) to a focused
       LLM call: "extract tissue-confirmed diagnoses NOT in discharge dx"
    3. Surface the gaps as additional predictions, merged with the
       engine's main output and deduped

Limitations (v1):
    - Operates on notes only. If pathology lives in a separate path report
      data feed (as SmarterDx ingests it), v1 misses those. v2 would need
      `pathology_results` as a dedicated column in the eval dataset.
    - The regex segment detector is heuristic. Validated on a hand-picked
      50-case sample is Stage 1 of the design doc (PHASE_E_PATHOLOGY_DESIGN.md).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from cdi_engine import _call_llm  # noqa: E402  (sibling import after path setup)


# ===========================================================================
# Segment detection — find pathology-report-style content in notes
# ===========================================================================

# Headers / phrases that mark the start of a pathology report section.
# Conservative — false negatives are cheaper than false positives here
# (false positive = LLM call on non-pathology text → mostly empty extraction;
#  false negative = miss a real pathology finding entirely).
PATH_HEADER_PATTERNS = [
    r"\bFINAL\s+(PATHOLOG(IC|ICAL|Y)\s+)?DIAGNOS(IS|ES)\b",
    r"\bPATHOLOG(Y|IC|ICAL)\s+(REPORT|RESULT|FINDINGS?|DIAGNOSIS|EXAMINATION)\b",
    r"\bSURGICAL\s+PATHOLOG(Y|IC)\b",
    r"\bMICROSCOPIC\s+(EXAMINATION|FINDINGS?|DESCRIPTION)\b",
    r"\bGROSS\s+(EXAMINATION|DESCRIPTION)\b",
    r"\bCYTOLOG(Y|IC|ICAL)\s+(REPORT|FINDINGS?|EXAMINATION)\b",
    r"\bSPECIMEN\s+(SOURCE|TYPE|RECEIVED|DESCRIPTION)\b",
    r"\bBIOPSY\s+(RESULT|REPORT|FINDINGS?|SHOW(ED|S))\b",
    r"\bIMMUNOHISTOCHEMISTRY\b",
    r"\bPATH\s+REPORT\b",
]

# Cancer/tissue-level terms — if any of these appear within ~500 chars of a
# pathology header, we're more confident this is a real pathology report.
# Used to score segments, not to gate.
PATH_DIAGNOSIS_TERMS = [
    "adenocarcinoma", "carcinoma", "sarcoma", "lymphoma", "leukemia",
    "melanoma", "glioma", "neoplasm", "malignant", "metastatic",
    "adenoma", "polyp", "dysplasia", "in-situ", "in situ",
    "atypia", "atypical", "hyperplasia", "neoplastic",
    "tubular adenoma", "tubulovillous", "sessile serrated",
    "malignant effusion", "malignant pleural", "malignant ascites",
    "well-differentiated", "moderately-differentiated", "poorly-differentiated",
    "low-grade", "high-grade", "stage", "grade",
]

PATH_HEADER_RE = re.compile("|".join(PATH_HEADER_PATTERNS), re.IGNORECASE)
PATH_DX_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in PATH_DIAGNOSIS_TERMS) + r")\b",
                         re.IGNORECASE)


def detect_pathology_segments(text: str, window: int = 2000) -> List[str]:
    """Return contiguous text segments that look like pathology reports.

    For each header match, take ±`window` characters around it and merge
    overlapping windows. Then keep only segments that contain at least
    one pathology-diagnosis term — filters out throwaway mentions like
    "pathology pending".
    """
    if not text or not isinstance(text, str):
        return []

    hits = []
    for m in PATH_HEADER_RE.finditer(text):
        start = max(0, m.start() - 200)  # small lead-in for context
        end = min(len(text), m.start() + window)
        hits.append((start, end))

    if not hits:
        return []

    # Merge overlapping windows
    hits.sort()
    merged = [hits[0]]
    for s, e in hits[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Keep only segments that contain at least one pathology-diagnosis term
    kept = []
    for s, e in merged:
        seg = text[s:e]
        if PATH_DX_RE.search(seg):
            kept.append(seg)
    return kept


# ===========================================================================
# Pathology scan — focused LLM extraction + structural gap check
# ===========================================================================

PATH_SCAN_SYSTEM = """You are a CDI specialist auditing pathology and cytology reports for missed diagnoses on the discharge documentation.

You will be given:
(A) Pathology / cytology report segments from a patient's chart
(B) The list of diagnoses ALREADY in the patient's discharge documentation

Your job: find tissue-confirmed diagnoses in (A) that are NOT already in (B). These are missed CDI queries — physicians often write the discharge summary before pathology returns, so pathology findings frequently aren't captured.

Rules:
- Only include diagnoses with explicit tissue-level / cytology confirmation in (A). Suspected or pending findings don't count.
- SKIP if the diagnosis (or a clinically equivalent one) is already in (B), even with different wording. "Adenocarcinoma of the colon" is the same as "Colon cancer" — skip.
- KEEP if the diagnosis adds important specificity beyond what's in (B). "Sigmoid adenocarcinoma, moderately differentiated" beats documented "colon mass" — keep.
- Use the most specific ICD-10-CM code supported by the pathology language (subsite, behavior, grade where stated).

Return JSON only — an array of missed pathology-confirmed diagnoses:
[{"diagnosis": "...", "icd10_code": "...", "category": "other", "confidence": "high|medium", "evidence": "quote from pathology segment"}]

Return [] (empty array) if nothing in (A) is missing from (B).
"""


def _build_user_prompt(segments: List[str], documented: List[str]) -> str:
    seg_text = "\n\n---\n\n".join(segments)
    doc_text = "\n".join(f"- {d}" for d in documented) if documented else "(none extracted)"
    return (
        f"(A) PATHOLOGY / CYTOLOGY REPORT SEGMENTS:\n"
        f"{seg_text}\n\n"
        f"(B) DIAGNOSES ALREADY IN DISCHARGE DOCUMENTATION:\n"
        f"{doc_text}\n\n"
        f"Return the JSON array of missed pathology-confirmed diagnoses now."
    )


def _parse_json_array(raw: str) -> List[Dict]:
    """Lenient JSON extraction — handles surrounding prose."""
    if not raw:
        return []
    # Find the outermost JSON array. Prefer ```json blocks, fall back to bare array.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if not m:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(1) if m.lastindex else m.group(0))
        return [it for it in items if isinstance(it, dict) and it.get("diagnosis")]
    except (json.JSONDecodeError, ValueError):
        return []


def scan_for_pathology_gaps(
    discharge_summary: str,
    api_key: str,
    documented_diagnoses: List[str],
    procedure_notes: Optional[List[str]] = None,
    consult_notes: Optional[List[str]] = None,
    progress_notes: Optional[List[str]] = None,
    ip_consult_note: Optional[str] = None,
    hp_note: Optional[str] = None,
    ed_note: Optional[str] = None,
    model: str = "gpt-5-4",
    max_segments: int = 6,
) -> Dict:
    """Run the Phase E v1 pathology gap scan.

    Returns a dict with:
        gaps: list of prediction dicts (engine-compatible shape)
        segments_found: number of pathology segments detected
        notes_with_pathology: list of note labels where we found pathology
        scan_called: bool — was the LLM invoked?
        skip_reason: str if scan was skipped
    """
    # Gather all candidate text with provenance labels. Ordered by yield from
    # the 1086-case dataset survey (11 May 2026): hp_note had the most
    # pathology segments (36/82), discharge_summary second (17), etc.
    sources = [("discharge_summary", discharge_summary or "")]
    if hp_note:
        sources.append(("hp_note", hp_note))
    if ed_note:
        sources.append(("ed_note", ed_note))
    for i, n in enumerate(procedure_notes or [], 1):
        if n:
            sources.append((f"procedure_note_{i}", n))
    for i, n in enumerate(consult_notes or [], 1):
        if n:
            sources.append((f"consult_note_{i}", n))
    for i, n in enumerate(progress_notes or [], 1):
        if n:
            sources.append((f"progress_note_{i}", n))
    if ip_consult_note:
        sources.append(("ip_consult_note", ip_consult_note))

    all_segments = []
    notes_with_path = []
    for label, text in sources:
        segs = detect_pathology_segments(text)
        if segs:
            notes_with_path.append(label)
            for seg in segs:
                all_segments.append(f"[from {label}]\n{seg}")

    if not all_segments:
        return {
            "gaps": [],
            "segments_found": 0,
            "notes_with_pathology": [],
            "scan_called": False,
            "skip_reason": "no pathology segments detected",
        }

    # Cap segments to keep prompt size sane (the model only needs a few examples)
    segments = all_segments[:max_segments]

    user = _build_user_prompt(segments, documented_diagnoses)
    msgs = [
        {"role": "system", "content": PATH_SCAN_SYSTEM},
        {"role": "user", "content": user},
    ]

    try:
        raw = _call_llm(msgs, api_key, model=model, max_tokens=4000)
    except Exception as e:
        return {
            "gaps": [],
            "segments_found": len(all_segments),
            "notes_with_pathology": notes_with_path,
            "scan_called": False,
            "skip_reason": f"LLM call failed: {e}",
        }

    gaps = _parse_json_array(raw)
    # Tag each gap as coming from the pathology scan so downstream merging
    # can dedupe against the main engine output.
    for g in gaps:
        g["source"] = "phase_e_pathology_scan"
        # Default category to "other" if missing — pathology findings live in
        # the "other" bucket in our DIAGNOSIS_CATEGORIES taxonomy
        g.setdefault("category", "other")
        g.setdefault("confidence", "medium")

    return {
        "gaps": gaps,
        "segments_found": len(all_segments),
        "notes_with_pathology": notes_with_path,
        "scan_called": True,
        "skip_reason": None,
    }


# ===========================================================================
# CLI for quick smoke testing
# ===========================================================================

if __name__ == "__main__":
    import argparse
    import os

    p = argparse.ArgumentParser(description="Phase E pathology scanner — smoke test")
    p.add_argument("input", help="Path to a .txt file with combined clinical notes")
    p.add_argument("--documented", default="",
                   help="Comma-separated list of already-documented diagnoses")
    p.add_argument("--model", default="gpt-5-4")
    args = p.parse_args()

    api_key = os.environ.get("STANFORD_API_KEY")
    if not api_key:
        print("STANFORD_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    with open(args.input) as f:
        text = f.read()

    documented = [d.strip() for d in args.documented.split(",") if d.strip()]

    # Treat the whole file as one big discharge_summary for the smoke test
    result = scan_for_pathology_gaps(
        discharge_summary=text,
        api_key=api_key,
        documented_diagnoses=documented,
        model=args.model,
    )
    print(json.dumps(result, indent=2))
