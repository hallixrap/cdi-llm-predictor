#!/usr/bin/env python3
"""
Quick test of LLM CDI predictor with a sample discharge summary
"""

from cdi_llm_predictor import predict_missed_diagnoses, generate_cdi_report
import sys

# Sample discharge summary (you can replace with your own)
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
    print("QUICK TEST - LLM CDI PREDICTOR")
    print("="*80 + "\n")

    api_key = input("Enter Stanford API key (or press Enter to skip test): ").strip()

    if not api_key:
        print("\nNo API key provided. Exiting test.")
        print("\nTo run full test:")
        print("1. Get API key from Fateme Nateghi")
        print("2. Connect to Stanford VPN")
        print("3. Run: python3 scripts/cdi_llm_predictor.py")
        return

    print("\nUsing sample discharge summary...")
    print("(First 300 characters):")
    print(SAMPLE_SUMMARY[:300] + "...\n")

    print("Calling LLM to predict missed diagnoses...")
    print("(This may take 10-20 seconds)\n")

    try:
        # Call LLM
        results = predict_missed_diagnoses(SAMPLE_SUMMARY, api_key, model="gpt-4.1")

        # Generate report
        report = generate_cdi_report(results, patient_id="SAMPLE_TEST")

        print(report)

        # Show what was found
        missed = results.get('missed_diagnoses', [])
        if missed:
            print("\n" + "="*80)
            print("WHAT THE LLM FOUND:")
            print("="*80)
            for i, dx in enumerate(missed, 1):
                print(f"\n{i}. {dx.get('diagnosis', 'N/A')}")
                print(f"   Category: {dx.get('category', 'N/A')}")
                print(f"   Impact: {dx.get('reimbursement_impact', 'N/A')}")
                print(f"   Evidence: {dx.get('clinical_evidence', 'N/A')[:150]}...")

        # What should have been found (expected)
        print("\n" + "="*80)
        print("EXPECTED FINDINGS (based on the sample and real Stanford CDI data):")
        print("="*80)
        print("\nBased on the clinical evidence, we expect the LLM to identify:")
        print("\n🔴 TOP PRIORITY (matches highest volume Stanford queries):")
        print("1. HYPONATREMIA - Sodium 128 (#1 query type - 4,527 queries)")
        print("2. HYPERKALEMIA - Potassium 5.4 (#1 query type)")
        print("3. ANEMIA related to chronic disease - Hgb 9.2 (#2 query type - 2,528 queries)")
        print("4. HYPOALBUMINEMIA - Albumin 2.8 (#4 query type - 1,236 queries)")
        print("5. SEVERE MALNUTRITION - BMI 17.2, albumin 2.8, weight loss (#3 query - 1,587)")
        print("6. SEPSIS - SIRS criteria met (#5 query type - 1,199 queries)")
        print("7. RESPIRATORY FAILURE - O2 sat 88%, requiring O2 (#7 query - 914)")
        print("8. THROMBOCYTOPENIA - Platelets 95,000 (#9 query - 809)")
        print("9. ACUTE SYSTOLIC HEART FAILURE - EF 35%, BNP 450 (#10 query - 789)")
        print("\n🟡 ADDITIONAL HIGH-VALUE:")
        print("10. TYPE 2 MI (NSTEMI) - Elevated troponin 0.8 + sepsis/anemia (supply-demand)")
        print("11. ACUTE KIDNEY INJURY - Cr 1.8 (baseline 0.9) = +0.9 increase")
        print("12. DIABETES WITH HYPERGLYCEMIA - Glucose 245")
        print("\n💰 FINANCIAL IMPACT:")
        print("This single discharge summary has ~12 MISSED high-value diagnoses!")
        print("Physician documented: Pneumonia, Hypoxia, Dehydration (only 3 diagnoses)")
        print("Missed diagnoses could represent $50,000+ in additional reimbursement!")

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Check VPN connection (must be connected to Stanford VPN)")
        print("2. Verify API key is correct")
        print("3. Try: python3 ../CDI_Prototype/test_stanford_api.py")

if __name__ == "__main__":
    main()
