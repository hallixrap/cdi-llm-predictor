# Claude Code Instructions for This Project

## Project Overview

CDI (Clinical Documentation Improvement) diagnosis prediction system. Proactively identifies high-value diagnoses that physicians commonly miss in discharge summaries using GPT-5, a multi-note clinical pipeline, and LLM-as-Judge evaluation. Based on 688 actual CDI queries from Stanford Healthcare. Target: 20% reduction in manual CDI queries.

## Current Status (2026-05-08)

- **Dataset**: 1086 evaluation cases — file is `data/cdi_expanded_notes_eval.csv` with 11 note types per case (discharge, H&P, ED, 3× progress, 2× consult, 2× procedure, IP consult). Always pass `--data data/cdi_expanded_notes_eval.csv` explicitly; the silent fall-through to `cdi_linked_discharge_cleaned_confirmed_only.csv` (1009 single-note cases) caused a methodologically unsound paired comparison on 28 Apr.
- **Recall reference**: 22 Apr fast-mode run on 1086-case multi-note set: **58.07%** overall. Treat this as the locked baseline for paired comparisons of new experiments.
- **Precision reference**: 53.0% LEGITIMATE_CDI on the 300-sample seed=42 precision judge from 22 Apr.
- **Already-documented filter**: RESTORED on 1 May after Phase C regression analysis showed it's load-bearing for prompt-recall-surface expansions. Without it, "always query X" prompt rules inflate ALREADY_CODED.
- **Gateway**: Migrated 8 May 2026 from `apim.stanfordhealthcare.org` (deprecated) to `aihubapi.stanfordhealthcare.org` (Stanford SecureGPT AI Hub). New auth header is **`api-key`** (NOT `Ocp-Apim-Subscription-Key`). Standard Product subscription covers both Azure AI Foundry (GPT-5/4.1 family) and AWS Bedrock (Claude family) under one key. BAA-covered for PHI/PII.
- **Models available** on Stanford gateway: full GPT-5 family including `gpt-5-4` (≈ "GPT-5.5"), Claude Opus/Sonnet/Haiku 4.x via Bedrock — including target model `claude-opus-4-7`.

## Production Baseline (locked 11 May 2026)

**gpt-5-4 + v15_cdi_agent_style + balanced (3-vote) voting + Jaccard already-documented filter.**

On 30-case paired sample (`data/cdi_expanded_notes_eval.csv`, seed=42):
- Recall: 53.1%
- LEGITIMATE_CDI: 53.3% (gpt-5 judge) / 30.0% (opus judge)
- Composite (Recall × LEG): 28.3% (gpt-5) / 15.9% (opus)

Six experimental variants tested against this baseline (Stage 2-5). All lost on Recall × LEG under both judges. See `NEGATIVE_RESULTS.md` for the detailed paired data.

## Experiment Track

| # | Architecture | Result | Notes |
|---|---|---|---|
| 1 | `CDIEngine` v15 + balanced | **Baseline winner** | Locked production setup |
| 2 | `CDIEngine` v13_category_expanded | LOSE | User-only prompt, voting consensus weaker |
| 3 | `CDIEngine` v17_recall_max | LOSE | +12pp recall bought with hallucination |
| 4 | `CDIEngine` v18_verify (two-pass) | LOSE | Pass 2 confirms already-coded findings |
| 5 | `CDIAgentRunner` on claude-opus-4-7 | LOSE | 30% hallucination — primer too aggressive |
| 6 | `CDIEngine` + `--llm-filter` gpt-5-nano | LOSE | Filter strips specificity upgrades |
| 7 | `CDIEngine` + `--pathology-scan` (Phase E v1) | **In flight** | Targets cancer/pathology cluster |

The agentic runner replicates the Anthropic-built `cdi-agent` shape (multi-turn tool use, structured `report_diagnoses` tool, ICD-10 format-validation step) on Stanford's PHI-safe Bedrock endpoint — built directly on `_call_bedrock` in `cdi_engine.py` rather than via the Claude Agent SDK (which hardcodes `api.anthropic.com`).

## Data-Type Gap

Currently we ingest one data type: **clinical notes** (11 sub-types via `cdi_expanded_notes_eval.csv`). SmarterDx ingests nine — including structured lab results, MAR (medication administration), imaging results, and **pathology results** as a dedicated feed. Many CDI rules (electrolyte triggers, sepsis criteria, blood-loss anemia thresholds) require structured data we don't have. **More data is likely the highest-EV next bet** — bigger than any further prompt or filter optimisation. See PHASE_E_PATHOLOGY_DESIGN.md §1.

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

- `scripts/cdi_engine.py` — **Primary** CDIEngine (fast/balanced/high_recall modes, multi-note support, OpenAI + Bedrock dispatch)
- `scripts/cdi_agent_runner.py` — **Experimental** agentic runner replicating the Anthropic-built cdi-agent on Stanford Bedrock (Claude-only)
- `scripts/cdi_llm_predictor.py` — Legacy LLM predictor (GPT-5). Retained as `--no-engine` fallback.
- `scripts/evaluate_cdi_accuracy.py` — Evaluation framework. Flags: `--use-engine` (default), `--use-agent`, `--engine-mode`, `--discharge-only`, `--model`
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
