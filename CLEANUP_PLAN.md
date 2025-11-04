# New_CDI Cleanup Plan

## ✅ KEEP (Essential Files)

### Core CDI Predictor
- `scripts/cdi_llm_predictor.py` - Main predictor
- `scripts/evaluate_on_new_cdi_queries.py` - Evaluation script
- `scripts/analyze_cdi_gap_analysis.py` - Gap analysis (used to improve predictor)

### Web Demo (Complete)
- `web_demo/app.py`
- `web_demo/templates/index.html`
- `web_demo/static/` (empty but needed)
- `web_demo/README.md`
- `web_demo/DEPLOYMENT_GUIDE.md`

### Documentation
- `README.md` - Main project documentation
- `GAP_ANALYSIS_RESULTS.md` - Key findings that shaped the predictor
- `.gitignore` - Security (already created)

### Sample Data (Anonymized - No PHI)
- `data/CDI Sample.csv` - Sample queries for demo/testing (JC IDs only, no real patient data)

### Results (For Reference)
- `results/cdi_gap_analysis.csv` - Analysis results
- `results/new_cdi_queries_evaluation.csv` - Evaluation results

---

## ❌ DELETE (Legacy/Unnecessary)

### Old Scripts (Not Used by Current System)
- `scripts/01_explore_data.py` - Old RCC exploration
- `scripts/02_create_splits.py` - Old RCC data splitting
- `scripts/03_train_baseline_model.py` - Old RCC baseline
- `scripts/04_train_category_model.py` - Old RCC category model
- `scripts/analyze_rcc_vs_cdi.py` - Old comparison
- `scripts/create_combined_dataset.py` - Old data processing
- `scripts/debug_evaluation.py` - Old debugging
- `scripts/evaluate_llm_vs_baseline.py` - Old evaluation
- `scripts/export_clean_cdi_data.py` - One-time export tool
- `scripts/fix_cdi_queries_csv.py` - One-time fix tool
- `scripts/process_rcc_llm.py` - Old RCC processing
- `scripts/quick_test_llm.py` - Old test
- `scripts/rcc_replacement_llm.py` - Old RCC replacement
- `scripts/test_diagnosis_extraction.py` - Old test
- `scripts/test_llm_simple.py` - Old test
- `scripts/test_rcc_replacement.py` - Old test

### Old Results (Not Relevant)
- `results/category_test_predictions.csv` - Old RCC results
- `results/test_predictions.csv` - Old RCC results

### Old Documentation (Superseded)
- `COMPLETE_RCC_REPLACEMENT_SUMMARY.md` - Old RCC project
- `Complete Context for Claude Code - RCC Prediction with CDI Gold Standard.md` - Old context
- `ENHANCED_CDI_PREDICTOR_UPDATE.md` - Superseded by README
- `FIXES_APPLIED.md` - Old fixes log
- `ISSUE_RESOLVED.md` - Old issue log
- `READY_TO_RUN.md` - Superseded by README
- `VALIDATION_READY.md` - Old validation log
- `SQL queries for BigQuery.md` - Reference only, not needed

### Reference Materials (Keep Separate, Not in Main Repo)
- `FHIR/` folder - Reference for FHIR project (belongs in CDI_FHIR_Predictor)
- `medagentbrief/` folder - Reference code (belongs in CDI_FHIR_Predictor)
- `Meeting notes/` folder - Keep locally, not in GitHub

---

## Final Structure After Cleanup

```
New_CDI/
├── README.md                              # Main documentation
├── GAP_ANALYSIS_RESULTS.md               # Key findings
├── .gitignore                            # Security
├── requirements.txt                      # NEW - Python dependencies
├── scripts/
│   ├── cdi_llm_predictor.py             # Core predictor
│   ├── evaluate_on_new_cdi_queries.py   # Evaluation
│   └── analyze_cdi_gap_analysis.py      # Gap analysis
├── web_demo/
│   ├── app.py                           # Flask app
│   ├── templates/
│   │   └── index.html                   # Web UI
│   ├── static/                          # (empty but needed)
│   ├── README.md                        # Demo docs
│   └── DEPLOYMENT_GUIDE.md              # Deployment instructions
├── data/
│   └── CDI Sample.csv                   # Sample queries (anonymized)
└── results/
    ├── cdi_gap_analysis.csv             # Gap analysis output
    └── new_cdi_queries_evaluation.csv   # Evaluation output
```

Clean, focused, production-ready!
