# Claude Code Instructions for This Project

## Project Overview

CDI (Clinical Documentation Improvement) diagnosis prediction system. Proactively identifies high-value diagnoses that physicians commonly miss in discharge summaries using GPT-5, a multi-note clinical pipeline, and LLM-as-Judge evaluation. Based on 688 actual CDI queries from Stanford Healthcare. Target: 20% reduction in manual CDI queries.

## Current Status (2026-04-22)

- **Dataset**: 1086 evaluation cases (expanded from the original 552), drawn from Stanford CDI queries
- **Recall**: 58.1% overall (locked 1086-case eval, 17 Apr — soft Phase D + multi-note + filter bypass)
- **Precision**: 53.0% LEGITIMATE_CDI on the latest LLM precision-judge run (300-sample, seed=42)
  - LEGITIMATE_CDI 53.0 / PARTIALLY_VALID 24.2 / ALREADY_CODED 12.4 / HALLUCINATION 10.4
- **Category recall** (current, softened Phase D):
  - Sepsis 77.6%, Respiratory ~77%, Anemia ~73%, Malnutrition 68.9%
  - Metabolic still weak (+0.5pp only from Phase D softening — candidate for Phase C.3)
  - "Other" bucket is the largest remaining recall lever (347 ground-truth queries)
- **Architecture**: `CDIEngine` (fast/balanced/high_recall modes) is now the primary pipeline. Legacy `cdi_llm_predictor.py` retained as fallback via `--no-engine`.
- **Multi-note pipeline**: 11 note types supported (discharge, H&P, ED, 3× progress, 2× consult, 2× procedure, IP consult)
- **Already-documented filter**: BYPASSED since 17 Apr — LLM judge showed it was suppressing legitimate queries for marginal precision gain. Extraction retained for summary stats; filter call commented out in `cdi_engine.py`.
- **Prompt**: v15 CDI-agent-style with softened Phase D wording that protects discharge A&P / nutrition / problem-list content while still directing attention to cross-note gaps.

## What Needs Doing Next

### Phase C — Category-specific prompt expansions (in flight)
1. **C.1 — Evaluator categorisation fixes**: expand `DIAGNOSIS_CATEGORIES` in `scripts/evaluate_cdi_accuracy.py` so HFrEF/HFpEF/ejection fraction/AF variants go to cardiac (not "other"); CKD/chronic kidney to renal; DKA/HHS/metabolic acidosis-alkalosis to metabolic; sarcopenia to malnutrition; plural "pressure injuries" to pressure_ulcer.
2. **C.2 — SYSTEM_PROMPT additions** after the Encephalopathy rule: pathology-confirmed diagnoses (cancers, adenomas, malignant effusions), surgical/post-op complications (intraoperative, post-op haematoma/ileus/dehiscence), fractures (bone/laterality/aetiology), shock states (cardiogenic/septic/hypovolaemic/distributive/obstructive).
3. **C.3 — Rewrite the Electrolytes rule**: drop the "explicit A/P entry" requirement; trigger on lab abnormality AND (treatment given OR persistent/recurrent >24h). Specify thresholds for Na, K, Mg, Ca, Phos.

After Phase C edits land, re-run the full 1086-case eval and the precision judge (seed=42) to produce a paired comparison against the 17 Apr baseline.

### Phase D — Shipped (softened)
Softened wording applied 17 Apr. Sepsis/malnutrition recovered; metabolic only +0.5pp (Phase C.3 targets this).

### Phase E — Pending
Cancer/pathology recall lift. Clustering work on 22 Apr identified `cancer_pathology` (88 queries, 19% recall, 71 TP lever) as the single biggest "other" bucket.

### Phase F — System hardening
Throughput, retry robustness, cost/latency profile.

### Phase G — Production prototype
Epic integration, clinical validation, pilot.

### Key Decisions Pending
- Accuracy target threshold (55%/60%/70% recall — current 58.1%)
- Sandy / clinical team involvement confirmation
- Timeline expectations for pilot

## Key Scripts

- `scripts/cdi_engine.py` — **Primary** CDIEngine (fast/balanced/high_recall modes, multi-note support)
- `scripts/cdi_llm_predictor.py` — Legacy LLM predictor (GPT-5). Retained as `--no-engine` fallback.
- `scripts/evaluate_cdi_accuracy.py` — Evaluation framework (supports `--engine-mode`, `--discharge-only`, `--prompt-variant`)
- `scripts/llm_judge.py` — LLM-as-Judge semantic matching (for recall scoring)
- `scripts/llm_precision_judge.py` — Precision judge (4 buckets: LEGITIMATE_CDI / ALREADY_CODED / PARTIALLY_VALID / HALLUCINATION)
- `scripts/hill_climb_eval.py`, `scripts/run_hill_climb.py` — Paired hill-climb optimisation framework (23 prompt variants tested)
- `run_evaluation.py` — Master runner (legacy wrapper)
- `web_demo/` — Web-based demo interface

## Key Data Files

- `data/training_dataset_parsed.csv` — expanded 1086-case evaluation dataset
- `results/` — evaluation run artefacts (per-run summaries + per-case CSVs)
- `results/other_category_cluster_20260422.csv` — "other" bucket clustered into clinical patterns (22 Apr analysis)
- `Context/` — CDI rules and reference materials
- `CDI data/` — Raw CDI query data
- `transcripts/` — Meeting transcripts (gitignored; may contain PII / colleague names)

## Environment

- Stanford PHI-safe API (GPT-5, GPT-4.1, GPT-5-nano)
- BigQuery access for data extraction
- Python: pandas, numpy, requests
- Stanford VPN required (drops will manifest as `API 403: Public access is disabled`)

## Project Plan & Docs

- `PROJECT_PLAN.md` — Full multi-phase project plan
- `EVALUATION_PLAN.md` — Detailed evaluation methodology
- `QUICK_START.md` — Getting started guide
- `DEID_WORKFLOW.md` — De-identification workflow
- `HILL_CLIMB_ARCHITECTURE.md`, `HILL_CLIMB_USAGE.md`, `HILL_CLIMB_QUICK_START.txt` — Hill-climb framework docs

## What Claude Code Can Do Autonomously

- Refine prediction prompts and re-run evaluations
- Analyse error patterns across evaluation results
- Improve the LLM-as-Judge matching logic
- Generate performance comparison reports
- Expand the CDI rules in `Context/` folder
- Refactor scripts for better modularity
- Build additional evaluation datasets from existing data
- Cluster ground-truth queries to identify recall levers

## What Requires Human Action

- BigQuery SQL access for dataset expansion
- Stanford API key provisioning
- Clinical review of false positives / negatives
- Epic integration setup
- Sandy / team availability decisions
- Running git commits from outside the sandbox when `.git/` is locked
