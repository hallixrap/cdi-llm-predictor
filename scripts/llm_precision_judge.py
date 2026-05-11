#!/usr/bin/env python3
"""
LLM-as-judge precision evaluator for CDI predictions.

The naive "exact match to CDI ground truth" precision is misleading. It treats
every non-matched prediction as a false positive, even clinically valid findings
the CDI specialist chose not to query.

This judge takes a sample of unmatched predictions and classifies each into:
  A. LEGITIMATE_CDI     — defensible CDI query (supportable, documentable, not
                           already coded, adds clinical/reimbursement value)
  B. ALREADY_CODED      — already in the discharge summary's formal diagnoses
  C. PARTIALLY_VALID    — some clinical basis but too speculative or unclear
  D. HALLUCINATION      — no supporting evidence in any note

Output: decomposed precision with confidence intervals.

Usage:
    export STANFORD_API_KEY=<key>
    python3 scripts/llm_precision_judge.py \\
        --results results/cdi_evaluation_results_20260413_132940.csv \\
        --data data/cdi_expanded_notes_eval.csv \\
        --sample 300 \\
        --judge-model gpt-5
"""
import argparse
import ast
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# Import _call_llm from cdi_engine for consistent API handling
sys.path.insert(0, str(Path(__file__).parent))
from cdi_engine import _call_llm


JUDGE_SYSTEM_PROMPT = """You are a senior Clinical Documentation Integrity (CDI) specialist.

Your job: grade a candidate diagnosis that an LLM produced against a real patient's notes. Decide whether the diagnosis is a legitimate CDI query opportunity.

Return JSON only. Classify into exactly ONE of:

"LEGITIMATE_CDI" — diagnosis is clinically supportable from the notes AND is not already in the formal Discharge Diagnoses / Principal Diagnosis / Secondary Diagnoses list. A CDI specialist could defensibly query the physician to add this.

"ALREADY_CODED" — diagnosis (or a clinically equivalent one) is already captured in the formal diagnosis section of the discharge summary. No CDI opportunity.

"PARTIALLY_VALID" — diagnosis has some clinical basis (e.g., one lab value, one symptom) but is too speculative, not documented with treatment, or would not meet CDI query criteria.

"HALLUCINATION" — no supporting evidence in any of the notes provided. The diagnosis does not appear and is not clinically inferable.

Criteria for LEGITIMATE_CDI (strict):
- Clinical evidence in the notes: lab values, medications, symptoms, or explicit mention
- Not already in formal diagnosis sections (narrative mention is NOT formal coding)
- Adds specificity or a new codable condition
- Defensible — a physician would accept or could validate the query

Respond with JSON in this exact format:
{
  "verdict": "LEGITIMATE_CDI" | "ALREADY_CODED" | "PARTIALLY_VALID" | "HALLUCINATION",
  "confidence": "high" | "medium" | "low",
  "rationale": "1-2 sentence explanation citing the evidence"
}
"""


def safe_eval(x):
    try:
        return ast.literal_eval(x) if isinstance(x, str) else []
    except Exception:
        return []


def truncate_note(text: str, max_chars: int = 6000) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= max_chars:
        return text
    # Keep the start (usually includes diagnoses/problem list) and the end
    half = max_chars // 2
    return text[:half] + "\n\n... [truncated] ...\n\n" + text[-half:]


def assemble_notes(row) -> str:
    sections = []
    mapping = [
        ("DISCHARGE SUMMARY", "discharge_summary"),
        ("HISTORY & PHYSICAL", "hp_note"),
        ("EMERGENCY DEPARTMENT NOTE", "ed_note"),
        ("PROGRESS NOTE 1", "progress_note_1"),
        ("PROGRESS NOTE 2", "progress_note_2"),
        ("PROGRESS NOTE 3", "progress_note_3"),
        ("CONSULT NOTE 1", "consult_note_1"),
        ("CONSULT NOTE 2", "consult_note_2"),
        ("PROCEDURE NOTE 1", "procedure_note_1"),
        ("PROCEDURE NOTE 2", "procedure_note_2"),
        ("INPATIENT CONSULT NOTE", "ip_consult_note"),
    ]
    # Keep discharge summary fully; truncate the rest more aggressively
    for label, col in mapping:
        v = row.get(col)
        if isinstance(v, str) and v.strip():
            limit = 10000 if col == "discharge_summary" else 3500
            sections.append(f"=== {label} ===\n{truncate_note(v, limit)}")
    return "\n\n".join(sections)


def build_judge_message(prediction: str, notes: str, cdi_ground_truth: list) -> list:
    gt_str = "; ".join(cdi_ground_truth) if cdi_ground_truth else "(none)"
    user_content = f"""CANDIDATE PREDICTION (the LLM produced this as a CDI query candidate):
"{prediction}"

CDI SPECIALIST'S ACTUAL QUERIES FOR THIS PATIENT (for context, not a label):
{gt_str}

PATIENT CLINICAL NOTES:
{notes}

Grade the candidate prediction. Return JSON only."""
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_verdict(response: str) -> dict:
    """Extract JSON verdict from LLM response."""
    if not response:
        return {"verdict": "ERROR", "confidence": "low", "rationale": "empty response"}
    # Try to find JSON block
    m = re.search(r"\{.*?\}", response, re.DOTALL)
    if not m:
        return {"verdict": "ERROR", "confidence": "low", "rationale": f"no JSON in: {response[:200]}"}
    try:
        parsed = json.loads(m.group(0))
        verdict = parsed.get("verdict", "").strip().upper()
        if verdict not in {"LEGITIMATE_CDI", "ALREADY_CODED", "PARTIALLY_VALID", "HALLUCINATION"}:
            return {"verdict": "ERROR", "confidence": "low", "rationale": f"bad verdict: {verdict}"}
        return {
            "verdict": verdict,
            "confidence": parsed.get("confidence", "medium"),
            "rationale": parsed.get("rationale", ""),
        }
    except json.JSONDecodeError as e:
        return {"verdict": "ERROR", "confidence": "low", "rationale": f"parse error: {e}"}


def sample_unmatched_predictions(results_df, data_df, n_sample: int, seed: int = 42):
    """Build a stratified sample of unmatched predictions.

    Stratify on per-case match status to get a mix of:
      - cases where model matched some ground truth
      - cases where model matched none
    """
    random.seed(seed)
    rows = []
    for _, r in results_df.iterrows():
        preds = safe_eval(r["llm_predictions"])
        matches = safe_eval(r["matches"])
        matched = {m.get("predicted", "") for m in matches if isinstance(m, dict)}
        truths = safe_eval(r["cdi_diagnoses"])
        unmatched = [p for p in preds if p not in matched]
        for pred in unmatched:
            rows.append({
                "case_id": r["case_id"],
                "prediction": pred,
                "cdi_ground_truth": truths,
                "case_had_match": len(matched) > 0,
                "n_preds": len(preds),
            })

    print(f"Total unmatched predictions available: {len(rows)}")
    if len(rows) <= n_sample:
        return rows

    # 70/30 stratification: 70% from cases with any match, 30% from cases without
    matched_cases = [r for r in rows if r["case_had_match"]]
    unmatched_cases = [r for r in rows if not r["case_had_match"]]
    n_from_matched = int(n_sample * 0.7)
    n_from_unmatched = n_sample - n_from_matched
    n_from_matched = min(n_from_matched, len(matched_cases))
    n_from_unmatched = min(n_from_unmatched, len(unmatched_cases))
    # Fill the remainder if one stratum is small
    deficit = n_sample - (n_from_matched + n_from_unmatched)
    if deficit > 0:
        remaining = matched_cases if len(matched_cases) > len(unmatched_cases) else unmatched_cases
        # don't double-count — we'll just draw from the larger pool
    picked = random.sample(matched_cases, n_from_matched) + random.sample(unmatched_cases, n_from_unmatched)
    random.shuffle(picked)
    print(f"Sampled {len(picked)} unmatched predictions ({n_from_matched} from cases with matches, {n_from_unmatched} from cases without)")
    return picked


def load_eval_metadata(results_csv: str) -> dict:
    """Load the eval summary JSON that accompanies a results CSV.

    The evaluator saves both alongside each other:
        results/cdi_evaluation_results_<ts>.csv
        results/cdi_evaluation_summary_<ts>.json
    so we can recover what model/architecture was being evaluated.
    Returns {} if the summary can't be found.
    """
    p = Path(results_csv)
    # cdi_evaluation_results_<ts>.csv → cdi_evaluation_summary_<ts>.json
    summary_name = p.name.replace(
        "cdi_evaluation_results_", "cdi_evaluation_summary_"
    ).replace(".csv", ".json")
    summary_path = p.parent / summary_name
    if not summary_path.exists():
        return {}
    try:
        with open(summary_path) as f:
            return json.load(f)
    except Exception:
        return {}


def describe_architecture(meta: dict) -> str:
    """One-line description of which pipeline produced the evaluated CSV."""
    if not meta:
        return "unknown architecture"
    use_engine = meta.get("use_engine", False)
    mode = meta.get("engine_mode")
    # The evaluator sets use_engine=False internally when use_agent is True,
    # and writes mode=None in that case. Use that combination to detect agent.
    if use_engine is False and (mode is None or mode == "None"):
        return "CDIAgentRunner (agentic loop)"
    if use_engine and mode:
        return f"CDIEngine ({mode} mode)"
    if use_engine:
        return "CDIEngine"
    return "legacy cdi_llm_predictor"


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge precision evaluator")
    parser.add_argument("--results", required=True, help="Path to evaluation results CSV")
    parser.add_argument("--data", default="data/cdi_expanded_notes_eval.csv",
                        help="Path to evaluation dataset with notes")
    parser.add_argument("--sample", type=int, default=300, help="Number of predictions to grade")
    parser.add_argument("--judge-model", default="gpt-5",
                        help=("Model to use as judge. Examples: gpt-5 (default), "
                              "gpt-5-4, gpt-5-nano, claude-opus-4-7, claude-sonnet-4-6. "
                              "Use a different family from the evaluated config to "
                              "minimise self-evaluation bias."))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="results", help="Where to write judge output")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint if present")
    args = parser.parse_args()

    api_key = os.environ.get("STANFORD_API_KEY")
    if not api_key:
        print("ERROR: STANFORD_API_KEY not set")
        return 1

    # Identify what we're judging upfront so the report header is unambiguous
    eval_meta = load_eval_metadata(args.results)
    evaluated_model = eval_meta.get("model", "?")
    architecture = describe_architecture(eval_meta)
    evaluated_recall = eval_meta.get("overall_recall")

    print(f"\nLoading results: {args.results}")
    print(f"  Evaluated config: {evaluated_model} via {architecture}")
    if evaluated_recall is not None:
        print(f"  Eval-reported recall: {evaluated_recall * 100:.1f}%")
    print(f"  Judge model: {args.judge_model}")
    if args.judge_model == evaluated_model:
        print(f"  WARNING: judge model matches evaluated model — "
              f"results may be inflated by self-evaluation bias.")
    results = pd.read_csv(args.results)
    print(f"Loaded {len(results)} case results")

    print(f"Loading notes data: {args.data}")
    data = pd.read_csv(args.data)
    if "anon_id" in data.columns and "case_id" not in data.columns:
        data = data.rename(columns={"anon_id": "case_id"})
    note_cols = ["case_id", "discharge_summary", "hp_note", "ed_note",
                 "progress_note_1", "progress_note_2", "progress_note_3",
                 "consult_note_1", "consult_note_2",
                 "procedure_note_1", "procedure_note_2", "ip_consult_note"]
    keep = [c for c in note_cols if c in data.columns]
    data = data[keep].drop_duplicates(subset=["case_id"])
    print(f"Loaded notes for {len(data)} cases")

    # Sample
    sample = sample_unmatched_predictions(results, data, args.sample, args.seed)
    if not sample:
        print("No unmatched predictions to grade")
        return 0

    # Resume from checkpoint
    checkpoint = f"/tmp/cdi_judge_checkpoint_{args.seed}.json"
    graded = []
    start_idx = 0
    if args.resume and os.path.exists(checkpoint):
        with open(checkpoint) as f:
            graded = json.load(f)
        start_idx = len(graded)
        print(f"Resuming from checkpoint: {start_idx} already graded")

    # Build case -> notes map for quick lookup
    data_by_case = data.set_index("case_id").to_dict(orient="index")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output_dir) / f"llm_judge_precision_{ts}.json"

    try:
        for i, item in enumerate(sample[start_idx:], start=start_idx):
            case_id = item["case_id"]
            prediction = item["prediction"]
            print(f"[{i+1}/{len(sample)}] {case_id}: {prediction[:80]}")

            if case_id not in data_by_case:
                graded.append({**item, "verdict": "ERROR", "rationale": "case notes not found"})
                continue

            notes = assemble_notes(data_by_case[case_id])
            msgs = build_judge_message(prediction, notes, item["cdi_ground_truth"])
            try:
                response = _call_llm(msgs, api_key=api_key, model=args.judge_model,
                                     max_tokens=4000)
                verdict_obj = parse_verdict(response)
            except Exception as e:
                verdict_obj = {"verdict": "ERROR", "confidence": "low",
                               "rationale": f"API error: {str(e)[:200]}"}

            graded.append({
                **item,
                **verdict_obj,
            })

            # Checkpoint every 20
            if (i + 1) % 20 == 0:
                with open(checkpoint, "w") as f:
                    json.dump(graded, f, default=str)
                # Running tally
                verdicts = [g.get("verdict", "ERROR") for g in graded]
                from collections import Counter
                tally = Counter(verdicts)
                print(f"  Running tally: {dict(tally)}")

    except KeyboardInterrupt:
        print("\nInterrupted. Saving partial results.")
    finally:
        with open(checkpoint, "w") as f:
            json.dump(graded, f, default=str)

    # Final report
    from collections import Counter
    verdicts = [g.get("verdict", "ERROR") for g in graded]
    tally = Counter(verdicts)
    valid_total = sum(v for k, v in tally.items() if k != "ERROR")

    print("\n" + "=" * 60)
    print("LLM-AS-JUDGE PRECISION REPORT")
    print("=" * 60)
    print(f"Evaluated config: {evaluated_model} via {architecture}")
    if evaluated_recall is not None:
        print(f"Eval-reported recall: {evaluated_recall * 100:.1f}%")
    print(f"Judge model: {args.judge_model}")
    if args.judge_model == evaluated_model:
        print(f"⚠️  Self-evaluation: judge and evaluated model are the same.")
    print(f"Sample size: {len(graded)} (valid: {valid_total}, errors: {tally.get('ERROR', 0)})")
    print()
    for v in ["LEGITIMATE_CDI", "ALREADY_CODED", "PARTIALLY_VALID", "HALLUCINATION"]:
        n = tally.get(v, 0)
        pct = n / valid_total * 100 if valid_total else 0
        print(f"  {v:<20}{n:>5} ({pct:>5.1f}%)")

    # Save final JSON — include eval metadata so future runs can never be
    # confused about which config was being judged.
    out_obj = {
        "timestamp": ts,
        "results_csv": args.results,
        "judge_model": args.judge_model,
        "evaluated_model": evaluated_model,
        "evaluated_architecture": architecture,
        "evaluated_recall": evaluated_recall,
        "self_evaluation": args.judge_model == evaluated_model,
        "sample_size": len(graded),
        "tally": dict(tally),
        "graded": graded,
    }
    with open(out_path, "w") as f:
        json.dump(out_obj, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")

    # Clean up checkpoint
    if os.path.exists(checkpoint) and tally.get("ERROR", 0) < valid_total * 0.1:
        try: os.remove(checkpoint)
        except Exception: pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
