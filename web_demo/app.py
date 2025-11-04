"""
CDI LLM Predictor Web Demo
A web interface for demonstrating the CDI diagnosis predictor
"""

from flask import Flask, render_template, request, jsonify
import sys
import os

# Add parent directory to path to import cdi_llm_predictor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from cdi_llm_predictor import predict_missed_diagnoses

app = Flask(__name__)

# Store API key (in production, use environment variable)
API_KEY = None


@app.route('/')
def index():
    """Main page with input form"""
    return render_template('index.html')


@app.route('/api/predict', methods=['POST'])
def predict():
    """API endpoint to get CDI predictions"""
    global API_KEY

    try:
        data = request.get_json()
        discharge_summary = data.get('discharge_summary', '')
        api_key = data.get('api_key', API_KEY)

        if not discharge_summary:
            return jsonify({'error': 'No discharge summary provided'}), 400

        if not api_key:
            return jsonify({'error': 'No API key provided'}), 400

        # Store API key for subsequent requests
        API_KEY = api_key

        # Call the predictor
        result = predict_missed_diagnoses(
            discharge_summary=discharge_summary,
            api_key=api_key,
            model="gpt-4.1"
        )

        # Check for errors
        if 'error' in result:
            return jsonify({'error': result['error']}), 500

        # Format the response
        missed_diagnoses = result.get('missed_diagnoses', [])

        return jsonify({
            'success': True,
            'count': len(missed_diagnoses),
            'diagnoses': missed_diagnoses
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sample', methods=['GET'])
def get_sample():
    """Get a sample discharge summary for testing"""
    sample = """Stanford Hospital and Clinics
Discharge Summary

Today's Date: 04/07/2024
Medical Service: Lung Transplant
Patient's Name: [REDACTED]
Discharge Date: 04/07/2024
Admit Date: 03/09/2024

Principal Diagnosis at Discharge: Intra-abdominal abscess post-procedure, h/o ventral hernia repair

Additional Diagnoses at Discharge:
- S/P lung transplant
- Hyperthyroidism
- Acute post-operative pain
- Ventral hernia without obstruction or gangrene

Hospital Course:
Patient is a 32 year old male with a history of COVID-pneumonia with acute hypoxic respiratory failure requiring intubation, VV ECMO, tracheostomy, status post bilateral lung transplant 08/14/2021.

Admitted for elective ventral hernia repair. Post-operatively developed intra-abdominal abscess requiring drainage. Hospital course complicated by poor nutritional intake, albumin 2.1 g/dL. Patient required total parenteral nutrition.

Labs on discharge:
- Albumin: 2.1 g/dL (low)
- Hemoglobin: 8.5 g/dL
- Sodium: 128 mEq/L
- Magnesium: 1.4 mg/dL

Wound assessment by WOCN: Stage 2 pressure ulcer noted on sacrum, present on admission.

Patient is on chronic immunosuppression for lung transplant including tacrolimus and prednisone.

Discharge Plan:
- Continue immunosuppression
- Follow up with transplant surgery
- Nutrition consult for protein supplementation
"""

    return jsonify({
        'sample': sample
    })


if __name__ == '__main__':
    print("\n" + "="*80)
    print("CDI LLM PREDICTOR - WEB DEMO")
    print("="*80)
    print("\nStarting web server at http://localhost:5001")
    print("Open your browser and navigate to: http://localhost:5001")
    print("Press Ctrl+C to stop\n")

    app.run(debug=True, host='127.0.0.1', port=5001)
