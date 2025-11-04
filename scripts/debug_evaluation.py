#!/usr/bin/env python3
"""
Debug why evaluation predictions are empty
"""

import pandas as pd
import sys
import os
from cdi_llm_predictor import predict_missed_diagnoses

# Get API key
api_key = None
if len(sys.argv) > 1:
    api_key = sys.argv[1]
else:
    api_key = os.environ.get('STANFORD_API_KEY')

if not api_key:
    print("❌ No API key provided!")
    print("\nUsage: python3 scripts/debug_evaluation.py YOUR_API_KEY")
    sys.exit(1)

print("="*80)
print("DEBUGGING EVALUATION - Why are predictions empty?")
print("="*80)

# Load a test case
print("\n1. Loading test case...")
df = pd.read_csv('data/raw/642_CDI_queries_FIXED.csv')

# Find first case with valid data
for i in range(len(df)):
    if pd.notna(df.iloc[i]['discharge_summary']) and pd.notna(df.iloc[i]['cdi_query']):
        test_row = df.iloc[i]
        break

case_id = test_row['anon_id']
discharge_summary = test_row['discharge_summary']
cdi_query = test_row['cdi_query']

print(f"   Test case: {case_id}")
print(f"   Discharge summary length: {len(discharge_summary)} chars")
print(f"   CDI query: {cdi_query[:100]}...")

print("\n2. Calling LLM predictor...")
print("   (This may take 20-30 seconds)")

try:
    result = predict_missed_diagnoses(discharge_summary, api_key, model="gpt-4.1")

    print("\n3. LLM Result:")
    print(f"   Result type: {type(result)}")
    print(f"   Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")

    if 'missed_diagnoses' in result:
        missed = result['missed_diagnoses']
        print(f"   missed_diagnoses type: {type(missed)}")
        print(f"   Number of diagnoses: {len(missed) if isinstance(missed, list) else 'N/A'}")

        if isinstance(missed, list) and len(missed) > 0:
            print(f"\n   ✅ SUCCESS! Found {len(missed)} diagnoses:")
            for i, dx in enumerate(missed[:5], 1):
                print(f"      {i}. {dx.get('diagnosis', 'N/A')}")
        else:
            print(f"\n   ⚠️  missed_diagnoses is empty or not a list")
            print(f"   Full result: {result}")
    else:
        print(f"\n   ❌ 'missed_diagnoses' key not in result")
        print(f"   Full result: {result}")

    # Check for error
    if 'error' in result:
        print(f"\n   ❌ ERROR in result: {result['error']}")

    if 'raw_response' in result:
        print(f"\n   Raw LLM response preview:")
        print(f"   {result['raw_response'][:500]}")

except Exception as e:
    print(f"\n❌ EXCEPTION: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("DEBUG COMPLETE")
print("="*80)
