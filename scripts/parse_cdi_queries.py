#!/usr/bin/env python3
"""
Parse CDI Queries from BigQuery CSV Export

Handles three problems:
1. Broken CSV rows — discharge summary text contains newlines/commas that split rows
2. Trailing empty columns from BigQuery export
3. Extracting only [X]-confirmed diagnoses as the gold standard

Usage:
    python scripts/parse_cdi_queries.py [input_csv] [output_csv]

Defaults:
    input:  data/cdi_linked_discharge_6k.csv
    output: data/cdi_linked_discharge_cleaned.csv
"""

import pandas as pd
import re
import os
import sys
import csv


# --- Column definitions ---
# Query 6 format: discharge summary + progress note + CDI query
EXPECTED_COLUMNS_Q6 = [
    "anon_id", "encounter_csn", "discharge_date", "discharge_summary",
    "progress_note", "progress_note_date", "progress_note_type",
    "query_date", "query_text", "cdi_specialist_id", "days_after_discharge"
]

# Old Query 2 format (no progress notes)
EXPECTED_COLUMNS_Q2 = [
    "anon_id", "encounter_csn", "discharge_date", "discharge_summary",
    "query_date", "query_text", "cdi_specialist_id", "days_after_discharge"
]

# Old format (for backwards compatibility)
OLD_COLUMN_MAP = {
    "patient_id": "anon_id",
    "cdi_query_date": "query_date",
    "cdi_query_raw": "query_text",
}


def repair_csv(input_path: str) -> list[dict]:
    """
    Repair broken CSV rows from BigQuery export.

    Discharge summaries contain newlines and commas that break CSV parsing.
    Strategy: read raw lines, detect valid row starts (lines beginning with an
    anon_id pattern like JCxxxxx), and merge orphan lines back into the previous row.
    """
    print("Step 1: Repairing broken CSV rows...")

    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        raw_content = f.read()

    # Strip trailing empty columns from header and data
    # BigQuery exports often pad with extra commas
    lines = raw_content.split('\n')
    header_line = lines[0].rstrip(',').strip()
    headers = [h.strip().strip('"') for h in header_line.split(',')]

    # Auto-detect column format: Q6 (11 cols with progress notes) vs Q2 (8 cols)
    if 'progress_note' in headers:
        expected = EXPECTED_COLUMNS_Q6
    else:
        expected = EXPECTED_COLUMNS_Q2
    num_cols = len(expected)
    if len(headers) > num_cols:
        headers = headers[:num_cols]

    print(f"  Detected columns: {headers}")
    print(f"  Total raw lines: {len(lines)}")

    # Pattern for a valid row start: begins with an anon_id (e.g., JC1234567)
    # or a quoted anon_id (e.g., "JC1234567")
    row_start_pattern = re.compile(r'^"?JC\d+')

    # Merge broken lines back together
    merged_lines = []
    current_line = ""

    for i, line in enumerate(lines[1:], start=2):  # Skip header
        if not line.strip():
            continue

        if row_start_pattern.match(line):
            # This is a new row — save the previous one
            if current_line:
                merged_lines.append(current_line)
            current_line = line
        else:
            # This is a continuation of the previous row — merge
            # Replace the newline with a space to preserve text
            current_line += " " + line

    # Don't forget the last row
    if current_line:
        merged_lines.append(current_line)

    print(f"  Merged into {len(merged_lines)} rows (from {len(lines) - 1} raw lines)")
    print(f"  Repaired {len(lines) - 1 - len(merged_lines)} broken lines")

    # Parse merged lines into dicts
    rows = []
    parse_errors = 0
    for line in merged_lines:
        try:
            # Use csv reader to handle quoted fields properly
            parsed = list(csv.reader([line]))[0]
            if len(parsed) >= num_cols:
                row = {headers[j]: parsed[j] for j in range(num_cols)}
                rows.append(row)
            elif len(parsed) >= 4:
                # Partial row — pad with empty strings
                row = {}
                for j in range(num_cols):
                    row[headers[j]] = parsed[j] if j < len(parsed) else ""
                rows.append(row)
            else:
                parse_errors += 1
        except Exception:
            parse_errors += 1

    if parse_errors > 0:
        print(f"  Warning: {parse_errors} rows could not be parsed")

    return rows


def clean_diagnosis_text(text: str) -> str:
    """Remove boilerplate and clean up diagnosis text"""
    if not text:
        return ""

    boilerplate = [
        r"This documentation will become part of the patient's medical record\.?",
        r"This documentation will be.*$",
        r"Agree with WOCN\.?",
        r"Provider response.*$",
        r"Physician Clarification.*clinically valid\.?",
        r"After reviewing.*clinically valid\.?",
    ]

    for pattern in boilerplate:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_confirmed_diagnoses(query_raw: str) -> list:
    """
    Extract ONLY confirmed diagnoses (those with [X] checked).
    Returns empty list if no boxes are checked.
    """
    if not query_raw or pd.isna(query_raw):
        return []

    query_raw = str(query_raw)
    confirmed = []

    # Pattern: [X] or [x] followed by diagnosis text
    pattern = r'\[\s*[Xx]\s*\]\s*([^\[\]]+?)(?=\s*\[\s*[Xx\s]*\s*\]|\s*This documentation|\s*Provider|$)'
    matches = re.findall(pattern, query_raw, re.DOTALL)

    for match in matches:
        cleaned = clean_diagnosis_text(match)
        if cleaned and len(cleaned) > 3:
            confirmed.append(cleaned)

    # Fallback: if no checkboxes at all, extract after "clinically valid"
    if not confirmed:
        if '[X]' not in query_raw and '[x]' not in query_raw and '[]' not in query_raw:
            for marker in ['clinically valid', 'indicated below']:
                if marker in query_raw.lower():
                    parts = re.split(marker + r'\.?', query_raw, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        diagnosis_text = parts[1]
                        cleaned = clean_diagnosis_text(diagnosis_text)
                        if cleaned and len(cleaned) > 10:
                            confirmed.append(cleaned)
                        break

    return confirmed


def has_unchecked_only(query_raw: str) -> bool:
    """Check if query has only unchecked boxes []"""
    if not query_raw or pd.isna(query_raw):
        return False

    query_raw = str(query_raw)
    has_checked = bool(re.search(r'\[\s*[Xx]\s*\]', query_raw))
    has_unchecked = bool(re.search(r'\[\s*\]', query_raw))

    return has_unchecked and not has_checked


def main():
    # Parse arguments
    input_path = sys.argv[1] if len(sys.argv) > 1 else "data/cdi_discharge_progress.csv"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "data/cdi_linked_discharge_cleaned.csv"

    print("=" * 60)
    print("CDI QUERY PARSER")
    print("=" * 60)

    if not os.path.exists(input_path):
        print(f"File not found: {input_path}")
        return 1

    # Step 1: Repair broken CSV rows
    rows = repair_csv(input_path)
    df = pd.DataFrame(rows)

    # Handle old column names if needed
    for old_name, new_name in OLD_COLUMN_MAP.items():
        if old_name in df.columns and new_name not in df.columns:
            df.rename(columns={old_name: new_name}, inplace=True)

    print(f"\nStep 2: Parsing CDI queries for confirmed [X] diagnoses...")

    # Determine which column has the query text
    query_col = "query_text" if "query_text" in df.columns else "cdi_query_raw"
    id_col = "anon_id" if "anon_id" in df.columns else "patient_id"

    # Step 2: Parse each row's CDI query
    parsed_diagnoses = []
    skipped_unchecked = 0

    for idx, row in df.iterrows():
        query_raw = row.get(query_col, '')

        if has_unchecked_only(query_raw):
            parsed_diagnoses.append([])
            skipped_unchecked += 1
            continue

        diagnoses = extract_confirmed_diagnoses(query_raw)
        parsed_diagnoses.append(diagnoses)

    df['cdi_diagnoses_confirmed'] = parsed_diagnoses
    df['cdi_diagnoses_str'] = df['cdi_diagnoses_confirmed'].apply(lambda x: '; '.join(x) if x else '')
    df['num_confirmed_diagnoses'] = df['cdi_diagnoses_confirmed'].apply(len)

    # Stats
    total = len(df)
    with_confirmed = len(df[df['num_confirmed_diagnoses'] > 0])
    without = total - with_confirmed

    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total rows after repair:          {total}")
    print(f"  Rows with confirmed [X] diagnoses: {with_confirmed}")
    print(f"  Rows skipped (unchecked only):     {skipped_unchecked}")
    print(f"  Rows with no diagnoses extracted:  {without}")
    print(f"  Total confirmed diagnoses:         {df['num_confirmed_diagnoses'].sum()}")

    # Show diagnosis distribution
    if with_confirmed > 0:
        print(f"\n  Diagnoses per row:")
        print(f"    1 diagnosis:  {len(df[df['num_confirmed_diagnoses'] == 1])}")
        print(f"    2 diagnoses:  {len(df[df['num_confirmed_diagnoses'] == 2])}")
        print(f"    3+ diagnoses: {len(df[df['num_confirmed_diagnoses'] >= 3])}")

    # Progress note stats (if present)
    if 'progress_note' in df.columns:
        has_progress = df['progress_note'].notna() & (df['progress_note'].astype(str).str.len() > 10)
        print(f"\n  Progress notes available:           {has_progress.sum()} / {total} ({100*has_progress.sum()/total:.1f}%)")
        if 'progress_note_type' in df.columns:
            pn_types = df.loc[has_progress, 'progress_note_type'].value_counts()
            print(f"  Progress note types:")
            for pn_type, count in pn_types.head(5).items():
                print(f"    {pn_type}: {count}")

    # Show examples
    print(f"\n--- Examples of Confirmed Diagnoses ---")
    examples = df[df['num_confirmed_diagnoses'] > 0].head(5)
    for idx, row in examples.iterrows():
        print(f"\n  {row[id_col]} (CSN: {row.get('encounter_csn', 'N/A')}):")
        print(f"    Diagnoses: {row['cdi_diagnoses_confirmed']}")

    # Save full dataset
    print(f"\nStep 3: Saving full dataset to: {output_path}")
    df.to_csv(output_path, index=False)

    # Also save confirmed-only subset (the gold standard)
    confirmed_df = df[df['num_confirmed_diagnoses'] > 0].copy()
    confirmed_path = output_path.replace('.csv', '_confirmed_only.csv')
    print(f"  Saving confirmed-only subset to: {confirmed_path}")
    print(f"  ({len(confirmed_df)} rows with confirmed diagnoses)")
    confirmed_df.to_csv(confirmed_path, index=False)

    print(f"\nDone!")
    return 0


if __name__ == "__main__":
    exit(main())
