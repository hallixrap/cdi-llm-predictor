# Phase E — Cancer/Pathology Recall Lift: Design Document

**Date:** 1 May 2026
**Status:** Draft, pre-implementation
**Author:** Claude (Cowork) + Chuk

## 1. Problem Statement

The "other" category is the largest remaining recall lever in the CDI pipeline (347 ground-truth queries on the 1009-case eval, ~35% recall). The 22 Apr cluster analysis decomposed "other" into ~40 clinical patterns; the largest sub-cluster is **`cancer_pathology`** with 88 ground-truth queries at 19% recall — i.e. **71 potential true positives** are being missed per eval cycle. This single cluster, if lifted from 19% to e.g. 60%, would add ~4pp to overall recall on its own.

The clinical reality: physicians frequently write the discharge summary before the pathology report finalises. Cancer diagnoses, polyp histology, malignant effusions, and other tissue-level findings end up in the pathology report but never make it into the discharge diagnoses — even though they materially change DRG and case-mix. CDI specialists routinely query these. The model currently misses them at scale.

## 2. Why the Phase C Prompt-Block Approach Failed

Phase C.2 added an "always query pathology-confirmed diagnoses" rule block to the system prompt. Paired LLM-judge results (28 Apr, n=222 valid):

| Bucket            | 17 Apr | Phase C | Δ        |
| ----------------- | ------ | ------- | -------- |
| LEGITIMATE_CDI    | 53.0%  | 45.9%   | −7.1pp   |
| **ALREADY_CODED** | 12.4%  | **21.6%** | **+9.2pp** |
| PARTIALLY_VALID   | 24.2%  | 23.9%   | flat     |
| HALLUCINATION     | 10.4%  | 8.6%    | −1.8pp   |

**Mechanism.** The "always query" framing pushed the model to flag pathology findings whenever it saw them — but with the already-documented filter bypassed, it had no way to check whether those findings were already in the discharge diagnoses (often via a different note or a free-text mention). Result: pathology findings did get flagged more, but a disproportionate share were already documented. The filter is load-bearing for any prompt expansion that increases the model's recall surface.

**Lesson.** Cancer/pathology recall is a *pipeline* problem, not a *prompt* problem. The signal (pathology findings present, but not in discharge diagnoses) lives in a specific pair of note types. Routing model attention there via prompt language is too blunt — it inflates ALREADY_CODED instead of LEGITIMATE_CDI. The right approach is to do the pathology-vs-discharge comparison structurally, then only surface the genuine gaps.

## 3. Proposed Approach — Pathology Scan Pass

A second LLM call scoped specifically to pathology/cytology findings, with a structured comparison against the discharge diagnoses already extracted by `_extract_documented_diagnoses`.

```
┌────────────────────────────────────────────────────────────┐
│              CDIEngine.predict()  (existing)               │
│                                                            │
│   Multi-note context  →  v15 SYSTEM_PROMPT  →  predictions │
└─────────────────────────┬──────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│         _pathology_scan_pass()  (NEW, this design)         │
│                                                            │
│   1. Gather candidate notes:                               │
│        • discharge summary                                 │
│        • all consult notes                                 │
│        • all procedure notes                               │
│        • progress notes mentioning "path" / "biopsy"       │
│   2. Filter to text segments that look like pathology      │
│      reports (headers: "FINAL PATHOLOGIC DIAGNOSIS",       │
│      "PATHOLOGY REPORT", "Specimen", "Microscopic exam")   │
│   3. Single focused LLM call:                              │
│        prompt = "Extract every tissue-confirmed diagnosis  │
│        from these pathology segments. Return JSON list."   │
│   4. For each pathology finding, check whether it appears  │
│      in `documented` (the discharge-diagnosis extraction). │
│   5. Surface only the GAPS (in path, not in discharge) as  │
│      additional predictions.                               │
└─────────────────────────┬──────────────────────────────────┘
                          │
                          ▼
        Merged predictions → existing _enrich() and filter
```

**Why this works where Phase C didn't:**
- Step 2 narrows the LLM's attention to actual pathology text, not the whole chart. Less prompt dilution.
- Step 4 does the documentation check structurally, not via the model. Each pathology finding is compared against the existing extracted-discharge-diagnoses list before being surfaced.
- Step 5 ensures only genuine gaps reach the predictions list. ALREADY_CODED inflation is impossible by construction.

## 4. Implementation Sketch

New module: `scripts/pathology_scanner.py`. Single public function:

```python
def scan_pathology_for_gaps(
    multi_note_context: dict,
    documented_diagnoses: list[str],
    api_key: str,
    model: str = "gpt-5",
) -> list[dict]:
    """
    Run a focused pathology-vs-discharge scan.

    Returns a list of prediction dicts in the same shape as
    CDIEngine._single_pass output:
      [{"diagnosis": ..., "icd10_code": ..., "category": ...,
        "confidence": ..., "evidence": ...}]
    """
```

Wired into `CDIEngine.predict()` as an additional pass:

```python
# Existing voting/single-pass logic produces `predictions`
# Existing _extract_documented_diagnoses produces `documented`

# NEW: pathology scan pass
path_gaps = scan_pathology_for_gaps(
    multi_note_context,
    documented,
    self.api_key,
    self.model,
)
predictions = _merge_dedupe(predictions, path_gaps)

# THEN the (now-restored) already-documented filter
predictions, filtered_out = _filter_already_documented(
    predictions, documented, full_text=discharge_summary
)
```

**Cost.** One additional GPT-5 call per case. At ~$0.05/case currently × 1009 cases = +$50 per full eval run. Acceptable for a +4pp recall lever.

**Latency.** +5-10s per case (one extra API round-trip). 1009 cases × 10s = ~3h additional clock time per full eval. Fine for batch eval; would need caching for any future real-time use.

## 5. Open Questions / Risks

1. **Pathology-segment detection.** Step 2 relies on regex/heuristic detection of pathology report sections inside the multi-note text. Failure mode: missing the segment entirely → no candidates → no lift. **Mitigation:** validate on a 50-case sample with manually-confirmed pathology reports before scaling. If the heuristic catches ≥80% of pathology-bearing cases, ship; otherwise iterate.

2. **Synonym matching in Step 4.** The discharge-diagnosis extraction may say "colon adenocarcinoma" while pathology says "moderately-differentiated adenocarcinoma of the sigmoid colon, Stage IIB." Naive string match will think the gap is real. **Mitigation:** use the existing `_normalise_diagnosis` helper plus a lightweight LLM equivalence check on candidate gaps (cheap — gpt-5-nano).

3. **Non-pathology MCC findings in pathology reports.** Some pathology reports also describe inflammation, infection, or other non-cancer findings. Should the scanner extract those too? **Initial decision:** yes — surface anything tissue-confirmed. Tighten scope only if precision suffers.

4. **Cytology vs surgical pathology.** Cytology (FNAs, fluid analysis) often has different report formats. **Initial decision:** include both; treat the segment-detection regex as a union of common headers.

5. **Interaction with the restored already-documented filter.** The pathology scanner already does a structural gap check. Running its output back through `_filter_already_documented` is belt-and-braces — should catch anything the structural check misses, but might also over-filter. **Mitigation:** instrument both filters; if they disagree more than 5% of the time, refactor to a unified comparison.

## 6. Validation Plan

Phased rollout to avoid another Phase-C-style regression:

**Stage 1 — Pathology-segment detection benchmark.** Build the regex set, run it on 50 hand-picked pathology-bearing cases, measure detection accuracy. Decision gate: ≥80% detection.

**Stage 2 — Cancer-only paired eval.** Run the full 1009-case eval with the new scanner enabled, scope: cancer/pathology cluster only. Compare cancer-cluster recall to baseline. Decision gate: ≥40% recall on the 88-query cluster (vs current 19%).

**Stage 3 — Full paired eval.** Re-run the full eval with the scanner active. Compare overall recall and the four LLM-judge precision buckets to the 17 Apr baseline (recall 58.1%, LEGITIMATE_CDI 53.0%). Decision gate: overall recall ≥58.1% AND LEGITIMATE_CDI ≥51%.

**Ship criteria:** all three gates green. Any regression on the LLM-judge buckets (especially ALREADY_CODED — same failure mode as Phase C) → halt and re-design before merging.

## 7. Out of Scope

- **Real-time / streaming integration.** This design assumes batch eval. Real-time integration with Epic is Phase G work.
- **Other "other" sub-clusters.** Post-op complications, fractures, shock — same general pattern (pipeline pass, not prompt block) but separate designs once Phase E is shipped and validated.
- **Filter restoration tuning.** Just-restored filter assumed to perform as it did pre-bypass. If LLM-judge shows it's still over-filtering, separate work item.

## 8. Roadmap Position

```
Phase A — Multi-note pipeline                 ✅ Shipped (17 Apr)
Phase B — Engine consolidation                ✅ Shipped
Phase C — Category prompt expansions          ⚠ Reverted (28 Apr); only C.1 kept
Phase D — Soft cross-note guidance            ✅ Shipped (17 Apr, softened)
Phase E — Cancer/pathology pipeline pass     →  THIS DESIGN
Phase F — System hardening (throughput, retry, cost profile)
Phase G — Production prototype (Epic integration, clinical pilot)
```

## 9. Next Concrete Steps

1. **Land the filter restoration commit** alongside this design doc (single commit — they're a pair).
2. **Re-run the 1009-case eval** with filter on, confirm we're back near the 17 Apr baseline (58.1% recall / 53.0% LEGITIMATE_CDI). If we are, the Phase C diagnosis is confirmed and the filter restoration is the right move. If we're below, we have a new puzzle.
3. **Implement Stage 1** (pathology-segment detection benchmark) — small, low-risk, yields a clear go/no-go for Stage 2.
4. **Drive through stages 2 and 3** sequentially, with paired comparisons at each step.
