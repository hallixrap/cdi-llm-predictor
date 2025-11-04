# **Complete Context for Claude Code \- RCC Prediction with CDI Gold Standard**

## **📋 Project Context**

You're building an **automated RCC (Relevant Clinical Conditions) prediction system** at Stanford Healthcare to reduce manual clinical documentation work. The breakthrough discovery: **CDI queries are better training data than RCC checkboxes**.

## **🎯 The REAL Goal: Complete .rcc Replacement**

### **What is .rcc?**
In Epic, physicians type `.rcc` during discharge summary writing, which opens a manual checklist of ~100 diagnoses organized by organ system:
- Cardiac (septic shock, cardiogenic shock, etc.)
- Respiratory (acute respiratory failure, COPD exacerbation, etc.)
- Renal (AKI, CKD stages, etc.)
- Metabolic (malnutrition, electrolyte abnormalities, etc.)
- etc.

There's also `.rccautoprognote` - a rules-based engine that auto-suggests diagnoses based on:
- Lab values (creatinine → AKI, low albumin → malnutrition)
- Structured data (ICD codes, problem lists)
- Simple pattern matching

## The .rcc Workflow
Physicians currently:
1. Write discharge summary
2. Type `.rcc` → Opens manual checklist of 100+ diagnoses
3. Manually tick applicable boxes (tedious, time-consuming)
4. `.rccautoprognote` suggests additional diagnoses via simple rules (labs only)
5. Still miss diagnoses → CDI queries them later → SmarterDx finds more

## Our Goal
Replace the ENTIRE .rcc workflow with AI that:
- Automatically suggests ALL relevant diagnoses from discharge narrative
- Catches what physicians currently select manually (baseline)
- Catches what rules engine would flag (enhanced)
- Catches what they miss despite .rcc (expert/CDI level)
- Integrates into François' MedAgentBrief discharge summary generator

### **Why Replace It?**
- **Manual burden:** Physicians click through 100+ checkboxes per discharge
- **Incomplete coverage:** Rules engine only catches simple lab-based conditions
- **Still misses diagnoses:** CDI queries 2,000-4,000 times/month despite .rcc
- **Workflow friction:** Interrupts discharge note writing

### **Your Model Must:**
1. **Match .rcc performance:** Catch what physicians currently select via manual ticking
2. **Beat .rccautoprognote:** Catch complex diagnoses beyond simple lab rules
3. **Prevent CDI queries:** Catch what even .rcc users miss
4. **Integrate with MedAgentBrief:** François' discharge summary generator + your diagnosis suggester = complete workflow replacement

### **Training Data Strategy:**

**Dataset 1: RCC Checkbox Data (Baseline)**
- Source: `processed_rcc_data_LLM.csv` (100 discharge summaries)
- Contains: What physicians CHECKED in .rcc section
- Purpose: Ensure model matches current physician performance
- SQL: `WHERE deid_note_text LIKE '%RELEVANT CLINICAL CONDITIONS%'`

**Dataset 2: CDI Query Data (Gold Standard)**
- Source: `training_dataset_compact.csv` (539 examples)
- Contains: What CDI specialists QUERIED about (high-value misses)
- Purpose: Catch what physicians miss even with .rcc

**Combined Training Target:**
```
model_output = union(rcc_checkboxes, cdi_queries)
```

This ensures the model:
- Never regresses below current .rcc performance ✓
- Catches additional high-value diagnoses ✓
- Eliminates need for manual checklist ✓

### **The Key Insight**

* ❌ **Old approach (v3):** Trained on RCC checkboxes → 28.75% F1 score  
  * Problem: Physicians miss diagnoses due to time constraints, not lack of knowledge  
  * Training on this \= replicating human errors  
* ✅ **New approach (v5):** Train on CDI queries → Expected \>40% F1 score  
  * CDI specialists identify what physicians MISSED or need to clarify  
  * 539 expert-labeled examples of the actual documentation gap  
  * This is the gold standard

### **What are CDI Queries?**

CDI (Clinical Documentation Integrity) specialists review discharge summaries and query physicians when they find:

1. High-value diagnoses that are clinically evident but not documented  
2. Diagnoses needing clarification for accurate coding  
3. Specificity improvements (e.g., "acute on chronic" vs just "chronic")

**Format:**

Physician Clarification

After reviewing the provider documentation request...

\[X\] Severe protein calorie malnutrition  
\[\] Malnutrition ruled out

This documentation will become part of the patient's medical record.

## **🗂️ File Structure for Claude Code**

Create this directory structure:

/your/project/folder/  
├── data/  
│   ├── raw/  
│   │   └── cdi\_linked\_clinical\_discharge\_fixed.csv  \# Source data  
│   ├── processed/  
│   │   ├── training\_dataset\_compact.csv              \# USE THIS for training  
│   │   └── training\_dataset\_parsed.csv               \# Full version with metadata  
│   └── summary/  
│       └── dataset\_summary.csv  
│  
├── scripts/  
│   └── (your training script will go here)  
│  
├── models/  
│   └── (saved models will go here)  
│  
└── results/  
    └── (evaluation results will go here)

## **📊 Your Training Dataset**

**File:** `training_dataset_compact.csv` (8.5 MB, 539 examples)

**Columns:**

* `patient_id`: Anonymized patient ID  
* `discharge_date`: When patient was discharged  
* `discharge_summary`: **INPUT** \- Full clinical discharge summary with RCC section  
* `cdi_diagnoses`: **GOLD STANDARD OUTPUT** \- What CDI expert identified as missing  
* `diagnosis_categories`: High-level categories (Sepsis, Malnutrition, etc.)  
* `days_after_discharge`: Time between discharge and CDI query

**Example Row:**

patient\_id,discharge\_date,discharge\_summary,cdi\_diagnoses,diagnosis\_categories,days\_after\_discharge  
JC2045326,2024-04-15,"DISCHARGE SUMMARY

HOSPITAL COURSE:  
Patient admitted with...

RELEVANT CLINICAL CONDITIONS:  
☐ Severe Malnutrition  
☑ Heart Failure  
☑ Diabetes  
...","Severe protein calorie malnutrition",Malnutrition,5

## **📈 Dataset Statistics**

Total Examples: 539  
Date Range: Jan 1 \- Apr 11, 2024  
Extraction Success: 96.6%

Diagnosis Distribution:  
\- Sepsis: 84 (15.6%) ← HIGHEST PRIORITY  
\- Malnutrition: 47 (8.7%) ← Michelle's focus  
\- Anemia: 41 (7.6%)  
\- Respiratory Failure: 32 (5.9%)  
\- Heart Failure: 27 (5.0%)  
\- Pressure Injury: 26 (4.8%)  
\- Pulmonary Edema: 22 (4.1%)  
\- Encephalopathy: 19 (3.5%)  
\- Other: 241 (44.7%)

Query Timing:  
\- Median: 0 days (same day as discharge)  
\- Mean: 0.7 days  
\- Range: 0-28 days

## **🎯 Training Approach**

### **Input Format**

\# The discharge\_summary field contains:  
\# 1\. Full clinical narrative (History, Hospital Course, etc.)  
\# 2\. RCC section showing what physician already checked  
\# 3\. Lab values, procedures, medications

\# Example:  
input\_text \= row\['discharge\_summary'\]  
\# This is the full clinical discharge summary (2000-10000 words)

### **Output Format**

\# The cdi\_diagnoses field contains:  
\# Pipe-separated list of diagnoses CDI queried about

\# Example:  
target\_output \= "Severe protein calorie malnutrition"  
\# or  
target\_output \= "Pressure injury POA Right and Left Ischial | Functional Quadriplegia"

### **Recommended Model Architecture**

**Option 1: Use your existing pipeline structure**

\# Update your v3 pipeline to predict CDI queries instead of RCC checkboxes

\# OLD:  
\# Input: discharge\_summary  
\# Target: rcc\_checked\_boxes (from physician)  
\# Performance: 28.75% F1

\# NEW:  
\# Input: discharge\_summary    
\# Target: cdi\_diagnoses (from CDI specialist)  
\# Expected: \>40% F1 (significant improvement)

**Option 2: Fine-tune a medical LLM**

\# Use Stanford's PHI-safe API or local model  
\# Fine-tune on your 539 examples  
\# Prompt engineering focused on CDI specialist thinking

### **Recommended Prompt Structure**

You are a Clinical Documentation Integrity (CDI) specialist reviewing a discharge summary.

Your task: Identify diagnoses that should be queried because they are:  
1\. Clinically supported by evidence in the note  
2\. High-value for reimbursement (Major CC/MCC)  
3\. Missing from or unclear in the physician's documentation

The discharge summary includes a RELEVANT CLINICAL CONDITIONS section showing what the physician already checked. Focus on what they MISSED or needs clarification.

Priority diagnoses to consider:  
1\. Sepsis (15.6% of queries)  
2\. Malnutrition (8.7% of queries)  
3\. Anemia (7.6% of queries)  
4\. Respiratory Failure (5.9% of queries)  
5\. Heart Failure (5.0% of queries)

Discharge Summary:  
{discharge\_summary}

What diagnoses would you query about?

## **🔧 Key Technical Details**

### **Data Splits**

\# Recommended splits:  
train: 80% (431 examples)  
validation: 10% (54 examples)  
test: 10% (54 examples)

\# Stratify by diagnosis\_categories to ensure coverage

### **Evaluation Metrics**

\# Primary metrics:  
1\. Exact match accuracy: % of times model predicts exact CDI query  
2\. Category-level F1: By diagnosis category (Sepsis, Malnutrition, etc.)  
3\. Clinical value: Weighted by reimbursement impact

\# Compare to baseline:  
\# v3 performance: 28.75% F1 on RCC checkboxes  
\# Target: \>40% F1 on CDI queries

### **Important Notes**

1. **Most queries are single diagnosis** (97.8%)

   * Don't overcomplicate multi-label prediction  
   * Focus on getting the main diagnosis right  
2. **Queries happen fast** (median 0 days)

   * CDI reviews happen same day as discharge  
   * This is PROSPECTIVE prediction (can prevent queries)  
3. **Sepsis is \#1 priority** (15.6%)

   * Focus evaluation on this category  
   * Michelle will care most about sepsis capture  
4. **RCC section is visible in input**

   * Model can see what physician already documented  
   * Should learn to identify what's MISSING

## **🚀 Training Script Template**

import pandas as pd  
from sklearn.model\_selection import train\_test\_split  
import numpy as np

\# Load data  
df \= pd.read\_csv('data/processed/training\_dataset\_compact.csv')

print(f"Total examples: {len(df)}")  
print(f"\\nDiagnosis distribution:")  
print(df\['diagnosis\_categories'\].value\_counts().head(10))

\# Split data (stratified by category)  
train\_df, test\_df \= train\_test\_split(  
    df,   
    test\_size=0.2,   
    stratify=df\['diagnosis\_categories'\],  
    random\_state=42  
)

train\_df, val\_df \= train\_test\_split(  
    train\_df,  
    test\_size=0.125,  \# 10% of total  
    stratify=train\_df\['diagnosis\_categories'\],  
    random\_state=42  
)

print(f"\\nTrain: {len(train\_df)}")  
print(f"Val: {len(val\_df)}")  
print(f"Test: {len(test\_df)}")

\# Training loop  
\# (Use your existing v3 pipeline structure)  
\# Just change the target from RCC checkboxes to cdi\_diagnoses

for epoch in range(num\_epochs):  
    for batch in train\_loader:  
        inputs \= batch\['discharge\_summary'\]  
        targets \= batch\['cdi\_diagnoses'\]  
          
        \# Forward pass  
        predictions \= model(inputs)  
        loss \= criterion(predictions, targets)  
          
        \# Backward pass  
        optimizer.zero\_grad()  
        loss.backward()  
        optimizer.step()

## **📝 Complete Prompt for Claude Code**

Copy this into Claude Code:

---

**PROMPT FOR CLAUDE CODE:**

I'm training an RCC prediction model for Stanford Healthcare. I have a training dataset with 539 examples where CDI specialists identified diagnoses that physicians missed.

**Dataset location:** `data/processed/training_dataset_compact.csv`

**Columns:**

* `discharge_summary`: Input (full clinical note with RCC section)  
* `cdi_diagnoses`: Gold standard output (what CDI expert identified as missing)  
* `diagnosis_categories`: High-level categories  
* `days_after_discharge`: Timing info

**Key stats:**

* 539 examples  
* Sepsis: 84 (15.6%) \- highest priority  
* Malnutrition: 47 (8.7%)  
* Most queries are single diagnosis (97.8%)

**Previous baseline:**

* v3 pipeline: 28.75% F1 on RCC checkboxes  
* Goal: \>40% F1 on CDI queries

**Tasks:**

1. Load and explore the dataset  
2. Create train/val/test splits (80/10/10, stratified by category)  
3. Build a model to predict `cdi_diagnoses` from `discharge_summary`  
4. Evaluate on test set, especially sepsis/malnutrition categories  
5. Compare to baseline performance

The breakthrough is using CDI queries (expert-identified missed diagnoses) instead of RCC checkboxes (what physicians checked) as training labels.

Can you help me:

1. Set up the training pipeline  
2. Evaluate the model  
3. Generate results to show stakeholders

---

## **📂 Files You Need to Download**

From the outputs folder, download these 4 files:

1. **training\_dataset\_compact.csv** (8.5 MB) ⭐ PRIMARY TRAINING DATA  
   * Put in: `data/processed/training_dataset_compact.csv`  
2. **training\_dataset\_parsed.csv** (13 MB) \- Full version with metadata  
   * Put in: `data/processed/training_dataset_parsed.csv`  
3. **cdi\_linked\_clinical\_discharge\_fixed.csv** (9.1 MB) \- Raw source  
   * Put in: `data/raw/cdi_linked_clinical_discharge_fixed.csv`  
4. **dataset\_summary.csv** (168 bytes) \- Quick stats  
   * Put in: `data/summary/dataset_summary.csv`

## **🎓 Key Context from Meetings**

### **From Michelle McCormack (CDI Director):**

* Wants 20% query reduction  
* 2,000-4,000 queries per month at Stanford  
* Frame as "improving physician documentation" not "replacing CDI"

### **From Jason Hom (Medical Director CDI):**

* Malnutrition is highest priority  
* RCC checkboxes are "definitely not the gold standard"  
* Focus on high-value Major CC/MCC diagnoses

### **From TDS Meeting:**

* SmarterDx generates millions in reimbursement  
* Your prospective approach is better (catches before discharge)  
* Epic has competing solution coming

## **💡 Success Criteria**

You'll know it's working when:

1. ✅ Model F1 \> 40% (better than v3's 28.75%)  
2. ✅ Predicts 70%+ of sepsis queries  
3. ✅ Predicts 60%+ of malnutrition queries  
4. ✅ Lower false positive rate than RCC approach  
5. ✅ Michelle says "this could work"

## **🚨 Critical Reminders**

1. **This is PHI data** \- Use Stanford VPN, secure environment  
2. **CDI queries \= gold standard** \- Expert-identified gaps, not physician errors  
3. **Sepsis is \#1 priority** \- Focus evaluation here  
4. **Frame as augmentation** \- Not replacement of CDI team  
5. **Compare to v3 baseline** \- Show improvement over 28.75% F1

---

**You now have everything you need to train v5\!** Take these files and this context to Claude Code and start training. Good luck\! 🎯

