#!/usr/bin/env python3
"""
Simple LLM test - pass API key as argument or set STANFORD_API_KEY env variable
Usage: python3 scripts/test_llm_simple.py [API_KEY]
"""

from cdi_llm_predictor import predict_missed_diagnoses, generate_cdi_report
import sys
import os

# Sample discharge summary with multiple missed diagnoses
SAMPLE_SUMMARY = """
DISCHARGE SUMMARY

Patient: 78-year-old male

HOSPITAL COURSE:
Patient admitted with shortness of breath and fever. Initially presented to ED with temperature 38.9°C,
heart rate 102, respiratory rate 24, oxygen saturation 88% on room air.

LABS:
WBC 14,500 (elevated), Creatinine 1.8 (baseline 0.9), Hemoglobin 9.2 (baseline 12),
Albumin 2.8 g/dL, BNP 450, Sodium 128 mEq/L, Potassium 5.4 mEq/L,
Platelets 95,000, Troponin 0.8 ng/mL (elevated), Glucose 245 mg/dL.

IMAGING:
Chest X-ray showed bilateral infiltrates. Echocardiogram showed EF 35%.

HOSPITAL COURSE:
Started on oxygen 2L via nasal cannula. Blood cultures drawn and broad-spectrum antibiotics initiated.
Patient had hypotension requiring fluid resuscitation. Responded well to treatment.

Patient also noted to have poor oral intake, weight loss of 15 lbs over past 2 months,
temporal wasting noted on exam. BMI 17.2.

DISCHARGE DIAGNOSES:
1. Pneumonia
2. Hypoxia
3. Dehydration

MEDICATIONS:
Completed 7-day course of antibiotics.

DISPOSITION:
Discharged home in stable condition with primary care follow-up.
"""

def main():
    print("\n" + "="*80)
    print("SIMPLE LLM TEST - CDI PREDICTOR")
    print("="*80 + "\n")

    # Get API key from command line or environment
    api_key = None
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        api_key = os.environ.get('STANFORD_API_KEY')

    if not api_key:
        print("❌ No API key provided!")
        print("\nUsage:")
        print("  Option 1: python3 scripts/test_llm_simple.py YOUR_API_KEY")
        print("  Option 2: export STANFORD_API_KEY='your_key' && python3 scripts/test_llm_simple.py")
        print("\nTo get API key:")
        print("  1. Contact Fateme Nateghi")
        print("  2. Connect to Stanford VPN")
        return 1

    print("✓ API key found")
    print("✓ Sample discharge summary loaded")
    print("\nCalling Stanford LLM API (gpt-4.1)...")
    print("(This may take 10-30 seconds)\n")

    try:
        # Call LLM
        results = predict_missed_diagnoses(SAMPLE_SUMMARY, api_key, model="gpt-4.1")

        # Generate report
        report = generate_cdi_report(results, patient_id="SAMPLE_TEST")
        print(report)

        # Show detailed findings
        missed = results.get('missed_diagnoses', [])
        if missed:
            print("\n" + "="*80)
            print(f"DETAILED FINDINGS: {len(missed)} MISSED DIAGNOSES")
            print("="*80)
            for i, dx in enumerate(missed, 1):
                print(f"\n{i}. {dx.get('diagnosis', 'N/A')}")
                print(f"   Category: {dx.get('category', 'N/A')}")
                print(f"   Reimbursement: {dx.get('reimbursement_impact', 'N/A')}")
                print(f"   Evidence: {dx.get('clinical_evidence', 'N/A')}")

        # Show expected vs actual
        print("\n" + "="*80)
        print("EXPECTED FINDINGS (based on real Stanford CDI query volumes)")
        print("="*80)
        print("\n🔴 TOP PRIORITY (highest volume Stanford queries):")
        print("  1. HYPONATREMIA - Na 128 (#1 query: 4,527 cases)")
        print("  2. HYPERKALEMIA - K 5.4 (#1 query: Electrolytes)")
        print("  3. ANEMIA - Hgb 9.2 (#2 query: 2,528 cases)")
        print("  4. HYPOALBUMINEMIA - Albumin 2.8 (#4 query: 1,236 cases)")
        print("  5. SEVERE MALNUTRITION - BMI 17.2, albumin 2.8, wt loss (#3: 1,587)")
        print("  6. SEPSIS - SIRS criteria met (#5 query: 1,199 cases)")
        print("  7. RESPIRATORY FAILURE - O2 sat 88% (#7: 914 cases)")
        print("  8. THROMBOCYTOPENIA - Plt 95k (#9 Coagulation: 809 cases)")
        print("  9. ACUTE SYSTOLIC HF - EF 35%, BNP 450 (#10: 789 cases)")
        print("\n🟡 HIGH-VALUE ADDITIONAL:")
        print(" 10. TYPE 2 MI - Troponin 0.8 + sepsis/anemia")
        print(" 11. AKI - Cr 1.8 (baseline 0.9)")
        print(" 12. DIABETES WITH HYPERGLYCEMIA - Glucose 245")

        print("\n" + "="*80)
        print("VALIDATION CHECK")
        print("="*80)

        # Check if key diagnoses were found
        found_diagnoses = [dx.get('diagnosis', '').lower() for dx in missed]
        found_text = ' '.join(found_diagnoses)

        checks = {
            'Hyponatremia': 'sodium' in found_text or 'hyponatremia' in found_text,
            'Hyperkalemia': 'potassium' in found_text or 'hyperkalemia' in found_text,
            'Anemia': 'anemia' in found_text,
            'Hypoalbuminemia': 'albumin' in found_text or 'hypoalbuminemia' in found_text,
            'Malnutrition': 'malnutrition' in found_text,
            'Sepsis': 'sepsis' in found_text,
            'Respiratory Failure': 'respiratory' in found_text,
            'Heart Failure': 'heart' in found_text or 'cardiac' in found_text,
        }

        found_count = sum(checks.values())
        total_count = len(checks)

        print(f"\nKey diagnoses found: {found_count}/{total_count}")
        for dx, found in checks.items():
            status = "✓" if found else "✗"
            print(f"  {status} {dx}")

        if found_count >= 6:
            print(f"\n✅ SUCCESS! LLM found {found_count}/{total_count} expected high-priority diagnoses")
        elif found_count >= 4:
            print(f"\n⚠️  PARTIAL: LLM found {found_count}/{total_count} - may need prompt tuning")
        else:
            print(f"\n❌ NEEDS WORK: Only found {found_count}/{total_count} - prompt may need revision")

        return 0

    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print("\nTroubleshooting:")
        print("  1. Check Stanford VPN connection (required!)")
        print("  2. Verify API key is correct and not expired")
        print("  3. Try the other billing code project to verify API access")
        return 1

if __name__ == "__main__":
    sys.exit(main())
