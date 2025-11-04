# CDI LLM Predictor - Web Demo

A web interface for demonstrating the Clinical Documentation Integrity (CDI) diagnosis predictor using Stanford's PHI-safe LLM API.

## Features

- 🏥 **Discharge Summary Input**: Paste any discharge summary to analyze
- 🎯 **CDI Query Detection**: Identifies missing or unclear diagnoses
- 📋 **Sample Data**: Preloaded example for quick demos
- 🔒 **PHI-Safe**: Uses Stanford's secure API for patient data
- ✨ **Modern UI**: Clean, professional interface with Stanford branding

## Quick Start

### 1. Activate Virtual Environment
```bash
cd /Users/chukanya/Documents/Coding/New_CDI
source venv/bin/activate
```

### 2. Start the Web Server
```bash
cd web_demo
python3 app.py
```

The server will start at: **http://localhost:8080**

### 3. Open in Browser
Navigate to http://localhost:8080 in your web browser

### 4. Use the Demo

**Option A: Use Sample Data**
1. Click "Load Sample" to populate with example discharge summary
2. Click "Analyze for CDI Opportunities"
3. View results on the right panel

**Option B: Use Your Own Data**
1. Ensure you're on Stanford VPN
2. Enter your Stanford API key (pre-filled with your key)
3. Paste a discharge summary
4. Click "Analyze"

## Requirements

- Stanford VPN connection (for API access)
- Valid Stanford PHI-safe API key
- Python 3.9+
- Flask (installed via venv)

## What It Does

The web demo:
1. Takes a discharge summary as input
2. Calls the enhanced `cdi_llm_predictor.py` with billing code optimization
3. Returns potential CDI queries with:
   - Diagnosis name (most specific billing code)
   - Clinical evidence from the note

## Current Performance

Based on 72-case validation:
- **31% overall recall** (22/71 matches)
- **39% adjusted recall** (excluding expected misses)

**Strengths**:
- ✅ Malnutrition (via hypoalbuminemia)
- ✅ Pressure ulcers
- ✅ Anemia
- ✅ Sepsis
- ✅ Pancytopenia (upgrades from thrombocytopenia)

**Limitations**:
- ❌ Lab-driven diagnoses (lactic acidosis, electrolytes) - requires structured lab data
- ❌ Specific conditions not in top 10 CDI queries (some CHF, diabetes, UTI cases)

## Demo Tips

**For Best Results**:
- Use discharge summaries with rich clinical narratives
- Focus on cases with malnutrition, pressure ulcers, anemia
- Expect 3-7 CDI opportunities per complex case

**Common Issues**:
- **401 Error**: Check Stanford VPN connection
- **No Results**: May indicate well-documented discharge summary or diagnoses outside current prompt scope
- **Slow Response**: LLM calls take 5-10 seconds

## Architecture

```
web_demo/
├── app.py                 # Flask backend
├── templates/
│   └── index.html        # Frontend UI
└── README.md             # This file

Connects to:
└── scripts/cdi_llm_predictor.py  # Core LLM prediction logic
```

## Stopping the Server

Press `Ctrl+C` in the terminal where the server is running

## Future Enhancements

Potential improvements:
1. Add structured lab data integration (→ 55-65% recall)
2. Expand prompt for more specific conditions
3. Add user feedback mechanism
4. Save/export CDI queries
5. Batch processing for multiple discharge summaries

## Contact

For API key issues, contact Fateme Nateghi at Stanford Healthcare IT.
