# CDI Diagnosis Prediction System

**Identifies diagnoses that physicians commonly miss, based on actual CDI specialist queries at Stanford Healthcare**

## Overview

This system helps capture diagnoses that physicians frequently forget to document, leaving money on the table for Stanford Health Care. It's based on analysis of real CDI (Clinical Documentation Integrity) queries showing what specialists look for that physicians miss.

### The Problem

- Physicians miss diagnoses due to time constraints, not lack of knowledge
- CDI specialists review discharge summaries and query for missed high-value diagnoses
- Current manual process: 2,000-4,000 queries per month at Stanford
- **Goal**: Proactively suggest these diagnoses to reduce queries by 20%

### The Solution

Uses Stanford's PHI-safe API with GPT-5 for intelligent diagnosis prediction, combined with LLM-as-Judge semantic matching for evaluation.

**Key Results:**
- **58.6% recall** on 688 CDI queries
- **Sepsis: 90.9%** (up from 24.6% baseline)
- **Respiratory: 78.7%**
- **Anemia: 73.3%**
- **Estimated PPV: ~76%** (discoveries are clinically valid)

## How It Works

1. **Input**: Discharge summary text
2. **Processing**: GPT-5 analyzes against 22 CDI query patterns
3. **Output**: JSON with missed diagnoses, ICD-10 codes, clinical evidence

The system is trained on what CDI specialists actually query (the "gold standard") rather than what physicians document.

## Files

- `scripts/cdi_llm_predictor.py` - Main LLM predictor with GPT-5 support
- `scripts/evaluate_cdi_accuracy.py` - Evaluation framework with metrics
- `scripts/llm_judge.py` - LLM-as-Judge for semantic diagnosis matching

## Quick Start

### 1. Setup Environment

```bash
cd /path/to/New_CDI
python3 -m venv venv
source venv/bin/activate
pip install pandas numpy requests
```

### 2. Use the Predictor

```python
from scripts.cdi_llm_predictor import predict_missed_diagnoses

# Get your API key from Fateme Nateghi
api_key = "your-stanford-api-key"

discharge_summary = """
[Your discharge summary text here]
"""

# Predict missed diagnoses
results = predict_missed_diagnoses(discharge_summary, api_key, model="gpt-5")

# Results include:
# - missed_diagnoses: List of diagnoses to query
# - clinical_evidence: Supporting facts from the note
# - reimbursement_impact: High/Medium/Low
# - icd10_code: Suggested billing code
```

## API Setup

**Stanford PHI-Safe API:**
- Requires: Stanford VPN connection (full tunnel)
- Contact: Fateme Nateghi for API key
- Endpoint: `https://apim.stanfordhealthcare.org/openai-eastus2/`

**Available Models:**
- `gpt-5` - Best performance, uses reasoning tokens
- `gpt-4.1` - Faster, good baseline
- `gpt-5-nano` - Fast, used for LLM-as-Judge matching

## Performance by Category

| Category | Recall | Notes |
|----------|--------|-------|
| Sepsis | 90.9% | Major improvement from prompt tuning |
| Respiratory | 78.7% | Strong performance |
| Anemia | 73.3% | Reliable detection |
| Malnutrition | 58.6% | Good |
| Pressure Ulcer | 58.1% | POA/staging queries |
| Cardiac | 57.7% | Improved from baseline |
| Electrolytes | 52.2% | Lab-based queries |
| Coagulation | 50.0% | Thrombocytopenia, etc. |
| Other | 41.5% | Pathology, debridement |
| Renal | 33.3% | Needs improvement |

## Key Technical Details

### GPT-5 Reasoning Tokens

GPT-5 uses "reasoning tokens" that count against `max_completion_tokens` but don't appear in output. The system sets `max_completion_tokens: 16000` to allow ~12k reasoning + ~4k output.

### LLM-as-Judge Matching

Uses `gpt-5-nano` for semantic matching between predicted and actual diagnoses. This improves recall by catching clinical equivalents (e.g., "Sepsis due to pneumonia" matches "Sepsis, clinically valid").

## Priority Diagnosis Categories

Based on 539 actual CDI queries:
1. **Sepsis**: 15.6% of queries - highest priority
2. **Malnutrition**: 8.7%
3. **Anemia**: 7.6%
4. **Respiratory Failure**: 5.9%
5. **Heart Failure**: 5.0%

## Financial Impact

Based on Michelle McCormack (CDI Director):
- Current: 2,000-4,000 queries/month at Stanford
- Target: 20% query reduction
- Each missed MCC: ~$5,000-10,000 additional reimbursement
- **Potential annual impact: Millions in captured revenue**

## Contact

- API Access: Fateme Nateghi
- CDI Leadership: Michelle McCormack (Director), Jason Hom (Medical Director)
