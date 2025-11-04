# CDI Diagnosis Prediction System

**Identifies diagnoses that physicians commonly miss, based on 539 actual CDI specialist queries at Stanford Healthcare**

## Overview

This system helps capture diagnoses that physicians frequently forget to document, leaving money on the table for Stanford Health Care. It's based on analysis of real CDI (Clinical Documentation Integrity) queries showing what specialists look for that physicians miss.

### The Problem

- Physicians miss diagnoses due to time constraints, not lack of knowledge
- CDI specialists review discharge summaries and query for missed high-value diagnoses
- Current manual process: 2,000-4,000 queries per month at Stanford
- **Goal**: Proactively suggest these diagnoses to reduce queries by 20%

### The Breakthrough

Training on **CDI queries** (what specialists identified as missing) rather than **RCC checkboxes** (what physicians checked) gives us the "gold standard" of what's actually missed.

## Two Approaches

### 1. LLM-Based Approach (Recommended) 🌟

Uses Stanford's PHI-safe API with GPT-4.1/GPT-5 for intelligent diagnosis prediction.

**Advantages:**
- Understands medical context and nuance
- Can explain reasoning with clinical evidence
- Mirrors how CDI specialists actually think
- Better at handling complex cases

**Files:**
- `scripts/cdi_llm_predictor.py` - Main LLM predictor
- `scripts/evaluate_llm_vs_baseline.py` - Compare LLM vs classifier

**Usage:**
```bash
cd /Users/chukanya/Documents/Coding/New_CDI
source venv/bin/activate
python3 scripts/cdi_llm_predictor.py
```

### 2. ML Classifier Baseline ✅

Gradient Boosting classifier using TF-IDF features.

**Performance:**
- Test F1: 40.61% (exceeds 40% target!)
- Improvement over v3: +11.86%
- Best for: Heart Failure (66.7% F1), Respiratory Failure (40% F1)

**Files:**
- `scripts/04_train_category_model.py` - Training script
- `models/category_gradient_boosting.pkl` - Trained model
- `models/category_tfidf_vectorizer.pkl` - Feature extractor

## Data

### Training Dataset
- **539 examples** from actual CDI queries (Jan-Apr 2024)
- Gold standard: What CDI specialists identified as missing
- Priority categories:
  - Sepsis: 15.6% of queries
  - Malnutrition: 8.7%
  - Anemia: 7.6%
  - Respiratory Failure: 5.9%
  - Heart Failure: 5.0%

### Files
- `data/processed/training_dataset_compact.csv` - Full training set
- `data/processed/train.csv` - Training split (431 examples)
- `data/processed/val.csv` - Validation split (54 examples)
- `data/processed/test.csv` - Test split (54 examples)

## Quick Start

### 1. Setup Environment

```bash
cd /Users/chukanya/Documents/Coding/New_CDI
python3 -m venv venv
source venv/bin/activate
pip install pandas numpy scikit-learn requests
```

### 2. Use LLM Predictor (Recommended)

```python
from scripts.cdi_llm_predictor import predict_missed_diagnoses

# Get your API key from Fateme Nateghi
api_key = "your-stanford-api-key"

discharge_summary = """
[Your discharge summary text here]
"""

# Predict missed diagnoses
results = predict_missed_diagnoses(discharge_summary, api_key, model="gpt-4.1")

# Results include:
# - missed_diagnoses: List of diagnoses to query
# - clinical_evidence: Supporting facts from the note
# - reimbursement_impact: High/Medium/Low
```

### 3. Or Use Baseline Classifier

```python
import pickle
import pandas as pd

# Load model
with open('models/category_gradient_boosting.pkl', 'rb') as f:
    model = pickle.load(f)

with open('models/category_tfidf_vectorizer.pkl', 'rb') as f:
    vectorizer = pickle.load(f)

# Predict
discharge_summary = "..."
X = vectorizer.transform([discharge_summary])
predicted_category = model.predict(X)[0]
print(f"Predicted CDI query category: {predicted_category}")
```

## API Setup (for LLM Approach)

**Stanford PHI-Safe API:**
- Requires: Stanford VPN connection (full tunnel)
- Contact: Fateme Nateghi for API key
- Endpoint: `https://apim.stanfordhealthcare.org/openai-eastus2/`

**Available Models:**
- `gpt-4.1` - Recommended, balanced performance
- `gpt-5-nano` - Faster, good for high volume
- `gpt-4.1-mini` - Most cost-effective

**Test API:**
```bash
python3 scripts/test_stanford_api.py  # From CDI_Prototype directory
```

## Project Structure

```
New_CDI/
├── data/
│   ├── raw/                    # Original CDI query data
│   └── processed/              # Train/val/test splits
├── scripts/
│   ├── 01_explore_data.py             # Data exploration
│   ├── 02_create_splits.py            # Create train/val/test
│   ├── 03_train_baseline_model.py     # String matching baseline
│   ├── 04_train_category_model.py     # Category classifier (BEST)
│   ├── cdi_llm_predictor.py           # LLM-based predictor
│   └── evaluate_llm_vs_baseline.py    # Compare approaches
├── models/
│   ├── category_gradient_boosting.pkl     # Trained classifier
│   └── category_tfidf_vectorizer.pkl      # Feature extractor
├── results/
│   ├── category_model_evaluation.json     # Baseline metrics
│   ├── category_test_predictions.csv      # Test set predictions
│   └── confusion_matrix.png               # Visualization
└── README.md                   # This file
```

## Performance Comparison

| Approach | F1 Score | Strengths | Use Case |
|----------|----------|-----------|----------|
| **v3 (RCC checkboxes)** | 28.75% | Baseline | Reference only |
| **v5 Classifier** | 40.61% | Fast, offline | High-volume screening |
| **v5 LLM** | TBD | Best quality, explains reasoning | Final validation |

## Priority Diagnosis Performance (Baseline)

| Category | Test F1 | Examples |
|----------|---------|----------|
| Heart Failure | 66.7% | 2 |
| Respiratory Failure | 40.0% | 3 |
| Malnutrition | 28.6% | 5 |
| Sepsis | 15.4% | 8 (needs improvement) |
| Anemia | 0.0% | 4 (needs improvement) |

## Key Insights from CDI Query Analysis

1. **97.8% of queries are single diagnosis** - Don't overcomplicate multi-label prediction
2. **Queries happen fast** (median 0 days after discharge) - This enables prospective intervention
3. **Sepsis is #1 priority** (15.6%) but hardest to predict - Focus here for improvement
4. **Most queries are for high-value Major CC/MCC diagnoses** - Direct financial impact

## Next Steps to Improve

1. **Evaluate LLM approach** - Run `evaluate_llm_vs_baseline.py` to compare
2. **For Sepsis improvement**: Add SIRS criteria extraction, infection markers
3. **Feature engineering**: Extract structured data (labs, vitals, medications)
4. **Ensemble approach**: Combine LLM + classifier predictions
5. **More training data**: Current 539 examples limit performance

## Financial Impact

Based on Michelle McCormack (CDI Director):
- Current: 2,000-4,000 queries/month at Stanford
- Target: 20% query reduction
- Each missed MCC: ~$5,000-10,000 additional reimbursement
- **Potential annual impact: Millions in captured revenue**

## Contact

- API Access: Fateme Nateghi
- CDI Leadership: Michelle McCormack (Director), Jason Hom (Medical Director)
- Project Context: See `Complete Context for Claude Code - RCC Prediction with CDI Gold Standard.md`

## References

- Similar approach to billing code extractor in `/CDI_Prototype/`
- Uses same Stanford PHI-safe API infrastructure
- Based on real CDI practice patterns at Stanford Healthcare
