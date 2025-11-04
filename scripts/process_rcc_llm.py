#!/usr/bin/env python3
"""
LLM-based RCC extraction - uses Stanford's secure GPT API
Much more robust than regex for handling variance in clinical notes
"""

import pandas as pd
import json
import requests
import time
from typing import List, Dict

def call_stanford_gpt(prompt: str, api_key: str, model: str = "gpt-4.1") -> str:
    """Call Stanford's PHI-safe GPT API"""
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    url = f"https://apim.stanfordhealthcare.org/openai-eastus2/deployments/{model}/chat/completions?api-version=2025-01-01-preview"
    
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1  # Low temperature for consistent extraction
    })
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=30)
            response.raise_for_status()
            return json.loads(response.text)['choices'][0]['message']['content']
        except Exception as e:
            print(f"API call failed (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                return json.dumps({"diagnoses": [], "error": str(e)})

def split_note_at_rcc(full_note: str) -> Dict[str, str]:
    """Split discharge summary at RCC section"""
    marker = 'RELEVANT CLINICAL CONDITIONS'
    idx = full_note.find(marker)
    
    if idx > 0:
        return {
            'clinical_note': full_note[:idx].strip(),
            'rcc_section': full_note[idx:].strip()
        }
    else:
        return {
            'clinical_note': full_note,
            'rcc_section': ''
        }

def extract_checked_diagnoses_llm(rcc_section: str, api_key: str) -> List[str]:
    """
    Use LLM to extract checked/documented diagnoses from RCC section.
    Much more robust than regex for messy clinical text.
    """
    if not rcc_section or len(rcc_section) < 10:
        return []

    # Truncate at common end markers (ordered by priority - earliest first)
    # The goal is to isolate ONLY the diagnosis list in the RCC section
    end_markers = [
        'Disposition:',                            # MOST COMMON: Comes right after RCC
        'Consultants involved in patient care:',
        'Procedures Performed',
        'Notable Labs and Images:',
        'Complications:',
        'Discharge Medications:',
        'Follow Up:',
        'Emergency Contact:',                      # Sometimes appears after RCC
        'No Known Allergies',                      # Sometimes appears after RCC
        'What to do with your medications'         # Sometimes appears after RCC
    ]

    for end_marker in end_markers:
        idx = rcc_section.find(end_marker)
        if idx > 0:
            rcc_section = rcc_section[:idx]
            break
    
    extraction_prompt = f"""You are a medical coding expert. Extract ONLY diagnoses that are explicitly documented in the "RELEVANT CLINICAL CONDITIONS" section of this discharge summary.

The RCC section starts with "RELEVANT CLINICAL CONDITIONS:" and contains a list of diagnoses documented during the hospitalization.

RCC SECTION TEXT:
{rcc_section}

CRITICAL INSTRUCTIONS - ONLY EXTRACT FROM RCC SECTION:
1. ONLY extract diagnoses that appear in the "RELEVANT CLINICAL CONDITIONS" section
2. DO NOT extract diagnoses from other parts of the discharge summary (e.g., hospital course, secondary diagnoses, active problems, etc.)
3. The RCC section has a specific format - diagnoses are usually listed with markers like:
   - "PRESENT on Admission to hospital"
   - "NOT present on Admission to hospital"
   - "Confirmed"
   - "probable - PRESENT on Admission to hospital"
   - Or standalone (e.g., "Obesity", "BMI 34.1 Obesity")

WHAT TO EXTRACT:
1. Extract diagnoses marked as "PRESENT on Admission to hospital"
2. Extract diagnoses marked as "Confirmed"
3. Extract diagnoses marked as "probable - PRESENT on Admission to hospital"
4. Extract diagnoses marked as "NOT present on Admission to hospital" (still billable!)
5. Extract standalone diagnoses like "Obesity", "Adult failure to thrive", "Severe protein calorie malnutrition"
6. Extract diagnoses followed by explanatory text (e.g., "Iron Deficiency anemia evidence by minimum Hb of 8...")
7. Extract CKD stage descriptions (e.g., "CKD Stage 3b: est GFR 30-44")
8. Extract BMI-related diagnoses (e.g., "BMI 34.1 Obesity")
9. If a diagnosis appears multiple times, only include it once

WHAT TO EXCLUDE:
- Lab values that are just numbers (e.g., "Sodium, Ser/Plas 138")
- Date stamps (e.g., "12/27/2021 1041")
- The phrase "Recent Labs" by itself
- Metadata like "Condition of Patient at Discharge"
- Items that are just options/templates but NOT filled in
- Lab results embedded in the RCC section (e.g., "eGFR Refit Without Race (2021) 127")
- Headers like "Recent Labs" that appear between diagnoses
- Any content from "Disposition:", "Emergency Contact:", "Medications:", etc.

CLEANING:
- Remove trailing markers like "- PRESENT on Admission to hospital" or "- NOT present on Admission to hospital"
- Keep the core diagnosis name clean and concise
- Keep important qualifiers like "(> 1.5x baseline...)" for AKI or "Acute on chronic" for heart failure

Return ONLY a JSON array of diagnosis strings. If you cannot clearly identify the RCC section or if it contains no diagnoses, return an empty array.

Output format:
{{"diagnoses": ["diagnosis 1", "diagnosis 2", ...]}}

Example output:
{{"diagnoses": ["Sepsis (SIRS with suspected infection)", "Acute kidney injury (> 1.5x baseline or 0.3 mg/dl increase in serum creatinine)", "Severe protein calorie malnutrition", "BMI 34.1 Obesity", "Adult failure to thrive"]}}
"""
    
    response = call_stanford_gpt(extraction_prompt, api_key)
    
    try:
        result = json.loads(response)
        return result.get('diagnoses', [])
    except json.JSONDecodeError:
        # Try to extract diagnoses from text response
        print(f"Warning: Could not parse JSON from LLM response")
        return []

def batch_extract_with_progress(df: pd.DataFrame, api_key: str, batch_size: int = 10) -> pd.DataFrame:
    """
    Extract diagnoses for all notes with progress tracking.
    Processes in batches to be respectful of API limits.
    """
    print(f"\nProcessing {len(df)} notes in batches of {batch_size}...")
    
    all_diagnoses = []
    
    for i in range(0, len(df), batch_size):
        batch_end = min(i + batch_size, len(df))
        print(f"Processing notes {i+1} to {batch_end}...")
        
        batch_diagnoses = []
        for idx in range(i, batch_end):
            rcc_section = df.iloc[idx]['rcc_section']
            diagnoses = extract_checked_diagnoses_llm(rcc_section, api_key)
            batch_diagnoses.append(diagnoses)
            time.sleep(0.5)  # Rate limiting
        
        all_diagnoses.extend(batch_diagnoses)
        
        # Progress update
        progress_pct = (batch_end / len(df)) * 100
        print(f"  → {progress_pct:.1f}% complete ({batch_end}/{len(df)} notes)")
    
    df['actual_diagnoses'] = all_diagnoses
    df['num_actual_diagnoses'] = df['actual_diagnoses'].apply(len)
    
    return df

def main():
    print("="*70)
    print("LLM-BASED RCC EXTRACTOR")
    print("Using Stanford's secure GPT API for PHI-safe processing")
    print("="*70)
    
    # Get API key
    api_key = input("\nEnter Stanford API key: ").strip()
    if not api_key:
        print("Error: API key required")
        return
    
    # Load data
    print("\nLoading discharge summaries...")
    df = pd.read_csv('discharge_summaries_rcc.csv')
    print(f"Loaded {len(df)} notes")
    
    # Split notes
    print("\nSplitting notes at RCC section...")
    splits = df['deid_note_text'].apply(split_note_at_rcc)
    df['clinical_note'] = splits.apply(lambda x: x['clinical_note'])
    df['rcc_section'] = splits.apply(lambda x: x['rcc_section'])
    
    # Ask if user wants to test on a small subset first
    print("\n" + "="*70)
    choice = input("Test on first 10 notes before processing all? (y/n): ").strip().lower()
    
    if choice == 'y':
        print("\nTesting on first 10 notes...")
        test_df = df.head(10).copy()
        test_df = batch_extract_with_progress(test_df, api_key, batch_size=5)
        
        # Show results
        print("\n" + "="*70)
        print("TEST RESULTS (First 10 notes)")
        print("="*70)
        for idx in range(len(test_df)):
            print(f"\n[Note {idx+1}] Found {test_df.iloc[idx]['num_actual_diagnoses']} diagnoses:")
            for dx in test_df.iloc[idx]['actual_diagnoses']:
                print(f"  ✓ {dx}")
        
        # Ask if user wants to continue
        continue_choice = input("\nProceed with full dataset? (y/n): ").strip().lower()
        if continue_choice != 'y':
            print("Stopping. Test results saved to test_subset_10_LLM.csv")
            test_df.to_csv('test_subset_10_LLM.csv', index=False)
            return
    
    # Process full dataset
    print("\n" + "="*70)
    print("PROCESSING FULL DATASET")
    print("="*70)
    df = batch_extract_with_progress(df, api_key, batch_size=10)
    
    # Summary statistics
    print(f"\n" + "="*70)
    print("DATA SUMMARY")
    print("="*70)
    print(f"Total notes: {len(df)}")
    print(f"Avg diagnoses per note: {df['num_actual_diagnoses'].mean():.1f}")
    print(f"Median diagnoses per note: {df['num_actual_diagnoses'].median():.1f}")
    print(f"Notes with 0 diagnoses: {(df['num_actual_diagnoses'] == 0).sum()}")
    print(f"Max diagnoses in a note: {df['num_actual_diagnoses'].max()}")
    
    # Most common diagnoses
    print(f"\n" + "="*70)
    print("MOST COMMON DIAGNOSES")
    print("="*70)
    all_diagnoses = []
    for dx_list in df['actual_diagnoses']:
        all_diagnoses.extend(dx_list)
    
    if all_diagnoses:
        from collections import Counter
        top_diagnoses = Counter(all_diagnoses).most_common(20)
        for i, (dx, count) in enumerate(top_diagnoses, 1):
            pct = (count / len(df)) * 100
            print(f"{i:2d}. {dx:<65s} ({count}x, {pct:.1f}%)")
    
    # Save results
    output_file = 'processed_rcc_data_LLM.csv'
    df.to_csv(output_file, index=False)
    print(f"\n✅ Saved full results to {output_file}")
    
    # Also save a test subset
    test_subset = df.head(10)[['anon_id', 'jittered_note_date', 'clinical_note', 'actual_diagnoses', 'num_actual_diagnoses']]
    test_subset.to_csv('test_subset_10_LLM.csv', index=False)
    print(f"✅ Created test subset in test_subset_10_LLM.csv")
    
    print("\n" + "="*70)
    print("EXTRACTION COMPLETE!")
    print("="*70)

if __name__ == "__main__":
    main()