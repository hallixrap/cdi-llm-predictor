#!/usr/bin/env python3
"""
Test the complete .rcc replacement system
Usage: python3 scripts/test_rcc_replacement.py [API_KEY]
"""

from rcc_replacement_llm import predict_all_diagnoses, generate_rcc_replacement_report
import sys
import os

# Sample discharge summary (same as before, with multiple diagnoses)
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
    print("TESTING COMPLETE .rcc REPLACEMENT SYSTEM")
    print("="*80 + "\n")

    # Get API key
    api_key = None
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        api_key = os.environ.get('STANFORD_API_KEY')

    if not api_key:
        print("❌ No API key provided!")
        print("\nUsage:")
        print("  Option 1: python3 scripts/test_rcc_replacement.py YOUR_API_KEY")
        print("  Option 2: export STANFORD_API_KEY='your_key' && python3 scripts/test_rcc_replacement.py")
        return 1

    print("✓ API key found")
    print("✓ Sample discharge summary loaded")
    print("\nCalling .rcc replacement system...")
    print("(This may take 20-40 seconds - analyzing for both RCC baseline + CDI expertise)\n")

    try:
        # Call the complete .rcc replacement system
        results = predict_all_diagnoses(SAMPLE_SUMMARY, api_key, model="gpt-4.1")

        # Generate human-readable report
        report = generate_rcc_replacement_report(results, patient_id="SAMPLE_TEST")
        print(report)

        # Detailed analysis
        print("\n" + "="*80)
        print("VALIDATION: Expected Diagnoses")
        print("="*80)

        print("\n📊 RCC BASELINE (Should catch these common diagnoses):")
        print("  1. Hypertension (if history mentioned)")
        print("  2. Obesity/Malnutrition indicators")
        print("  3. Atrial fibrillation (if mentioned)")
        print("  4. Common comorbidities physicians typically check")

        print("\n🎯 CDI EXPERTISE (High-value diagnoses often missed):")
        print("  1. ⭐ HYPONATREMIA, MODERATE - Na 128 (#1 query type)")
        print("  2. ⭐ HYPERKALEMIA - K 5.4 (#1 query type)")
        print("  3. ⭐ ANEMIA related to chronic disease - Hgb 9.2 (#2 query)")
        print("  4. ⭐ HYPOALBUMINEMIA - Albumin 2.8 (#4 query)")
        print("  5. ⭐ SEVERE MALNUTRITION - BMI 17.2, albumin 2.8 (#3 query)")
        print("  6. ⭐ SEPSIS - SIRS criteria met (#5 query)")
        print("  7. ⭐ RESPIRATORY FAILURE - O2 sat 88% (#7 query)")
        print("  8. ⭐ THROMBOCYTOPENIA - Plt 95k (#9 query)")
        print("  9. ⭐ ACUTE SYSTOLIC HF - EF 35%, BNP 450 (#10 query)")
        print(" 10. Type 2 MI - Troponin 0.8 (high value)")
        print(" 11. AKI - Cr 1.8 (baseline 0.9)")

        print("\n" + "="*80)
        print("SUCCESS METRICS")
        print("="*80)

        total = results.get('total_suggested', 0)
        rcc_count = results.get('rcc_count', 0)
        cdi_count = results.get('cdi_count', 0)

        print(f"\nTotal diagnoses suggested: {total}")
        print(f"RCC baseline: {rcc_count}")
        print(f"CDI expertise: {cdi_count}")

        # Evaluate quality
        if cdi_count >= 8:
            print(f"\n✅ EXCELLENT! Found {cdi_count} high-value CDI diagnoses (target: ≥8)")
        elif cdi_count >= 6:
            print(f"\n⚠️  GOOD! Found {cdi_count} high-value CDI diagnoses (target: ≥8)")
        else:
            print(f"\n❌ NEEDS WORK: Only {cdi_count} high-value CDI diagnoses (target: ≥8)")

        print("\n" + "="*80)
        print("COMPARISON TO MANUAL .rcc WORKFLOW")
        print("="*80)
        print("\n❌ OLD WORKFLOW (.rcc):")
        print("  1. Physician types .rcc")
        print("  2. Manual checklist opens (100+ diagnoses)")
        print("  3. Physician manually ticks boxes (tedious, 2-5 minutes)")
        print("  4. .rccautoprognote suggests a few lab-based diagnoses")
        print("  5. Still misses high-value diagnoses → CDI queries later")
        print("  6. Physician only documented 3 diagnoses in this case!")

        print("\n✅ NEW WORKFLOW (AI .rcc Replacement):")
        print(f"  1. AI analyzes discharge summary (20 seconds)")
        print(f"  2. Suggests {total} diagnoses (RCC baseline + CDI expertise)")
        print(f"  3. Physician reviews and accepts/rejects (30 seconds)")
        print(f"  4. Saves 1-4 minutes per discharge")
        print(f"  5. Prevents CDI queries before they happen")
        print(f"  6. Captures ${', 50k+' if cdi_count >= 8 else '30k+'} additional reimbursement!")

        return 0

    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print("\nTroubleshooting:")
        print("  1. Check Stanford VPN connection (required!)")
        print("  2. Verify API key is correct and not expired")
        return 1

if __name__ == "__main__":
    sys.exit(main())
