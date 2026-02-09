# SQL Queries for BigQuery Data Extraction

## Database
Table: `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`

### Columns Available
- `anon_id` - De-identified patient ID
- `deid_note_text` - De-identified note content
- `jittered_note_date` - Date (with jitter for de-identification)
- `note_type` - Note type code (**23 distinct values — USE THIS for filtering**)
- `note_type_desc` - Note type description (24,000+ values — too granular, avoid)
- `offest_csn` - Encounter CSN (for linking)
- `ambulatory` - Y/N for ambulatory status
- `author_prov_map_id` - Author ID

### Key Change (from Fateme)
**OLD:** Filtering on `note_type_desc` (LIKE '%DISCHARGE%') — misses many records because `note_type_desc` has thousands of variations.
**NEW:** Filter on `note_type` which only has 23 distinct values.

**CRITICAL:** Use ONLY `'Discharge/Transfer Summary'` for discharge summaries.
- `'Progress/Discharge/Transfer Summary'` is 76.7% nursing "End of Shift Plan of Care" notes — NOT real discharge summaries! This was the root cause of 18% recall on the 4,747-case eval.
- Progress notes use separate types: `'Progress Note, Inpatient'`, `'History and Physical'`, `'Consultation Note'`

---

## Query 0: Explore Available Note Types (Run First)

Understand what note types exist before extracting:

```sql
-- See all 23 note types and their counts
SELECT
    note_type,
    COUNT(*) as count
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE jittered_note_date >= '2023-01-01'
GROUP BY note_type
ORDER BY count DESC;
```

---

## Query 1: Discharge Summaries with .RCC Sections (UPDATED)

```sql
SELECT
    anon_id,
    deid_note_text,
    jittered_note_date,
    note_type,
    note_type_desc,
    LENGTH(deid_note_text) as note_length
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE
    -- Must have RCC section
    UPPER(deid_note_text) LIKE '%RELEVANT CLINICAL CONDITIONS%'
    -- UPDATED: Use note_type (23 values) instead of note_type_desc (24k+ values)
    -- CRITICAL: Only 'Discharge/Transfer Summary' — NOT 'Progress/Discharge/Transfer Summary' (76.7% nursing notes)
    AND note_type = 'Discharge/Transfer Summary'
    -- Must have at least one checked diagnosis (POA format)
    AND deid_note_text LIKE '%PRESENT on Admission%'
    -- Recent data
    AND jittered_note_date >= '2023-01-01'
    -- Filter out very short notes
    AND LENGTH(deid_note_text) > 1000
ORDER BY jittered_note_date DESC;
```

---

## Query 2: CDI Queries Linked to Discharge Summaries (UPDATED)

Main training data query — links discharge summaries to CDI queries by encounter.
**Result: 6,251 linked pairs** (up from 552 after dropping RCC/POA filters).

```sql
WITH discharge_summaries AS (
    SELECT
        anon_id,
        offest_csn,
        deid_note_text as discharge_summary,
        jittered_note_date as discharge_date
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
    WHERE
        -- CRITICAL: Only real discharge summaries (NOT 'Progress/Discharge/Transfer Summary')
        note_type = 'Discharge/Transfer Summary'
        -- Date range
        AND jittered_note_date >= '2023-01-01'
        AND jittered_note_date < '2025-01-01'
        -- Inpatient only
        AND ambulatory = 'N'
        -- Must have CSN for linking
        AND offest_csn IS NOT NULL
        -- Substantial content
        AND LENGTH(deid_note_text) > 1000
),

cdi_queries AS (
    SELECT
        anon_id,
        offest_csn,
        deid_note_text as query_text,
        jittered_note_date as query_date,
        author_prov_map_id as cdi_specialist_id
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
    WHERE
        note_type_desc = 'Documentation Clarification'
        AND jittered_note_date >= '2023-01-01'
        AND jittered_note_date < '2025-01-01'
        AND LOWER(deid_note_text) LIKE '%physician clarification%'
        AND author_prov_map_id IS NOT NULL
        AND offest_csn IS NOT NULL
)

-- Link by encounter CSN
SELECT
    d.anon_id,
    d.offest_csn as encounter_csn,
    d.discharge_date,
    d.discharge_summary,
    c.query_date,
    c.query_text,
    c.cdi_specialist_id,
    DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) as days_after_discharge
FROM discharge_summaries d
INNER JOIN cdi_queries c
    ON d.anon_id = c.anon_id
    AND d.offest_csn = c.offest_csn
WHERE
    c.query_date >= d.discharge_date
    AND DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) <= 30
ORDER BY d.discharge_date DESC;
```

---

## Query 3: CDI Queries Only (No Discharge Summary Link)

```sql
SELECT
    anon_id,
    offest_csn,
    deid_note_text as query_text,
    jittered_note_date as query_date,
    author_prov_map_id as cdi_specialist_id
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE
    note_type_desc = 'Documentation Clarification'
    AND jittered_note_date >= '2023-01-01'
    AND LOWER(deid_note_text) LIKE '%physician clarification%'
ORDER BY jittered_note_date DESC
LIMIT 5000;
```

---

## Query 4: Count Available Data (UPDATED)

Run this first to see how much the note_type fix expands your dataset:

```sql
-- Count discharge summaries: OLD filter vs NEW filter
SELECT
    'OLD: note_type_desc LIKE DISCHARGE' as filter_type,
    COUNT(*) as count
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE
    UPPER(deid_note_text) LIKE '%RELEVANT CLINICAL CONDITIONS%'
    AND (UPPER(note_type_desc) LIKE '%DISCHARGE%' OR UPPER(deid_note_text) LIKE '%DISCHARGE SUMMARY%')
    AND jittered_note_date >= '2023-01-01'

UNION ALL

SELECT
    'CORRECTED: note_type = Discharge/Transfer Summary ONLY' as filter_type,
    COUNT(*) as count
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE
    UPPER(deid_note_text) LIKE '%RELEVANT CLINICAL CONDITIONS%'
    AND note_type = 'Discharge/Transfer Summary'
    AND jittered_note_date >= '2023-01-01'

UNION ALL

-- Count CDI queries
SELECT
    'CDI Queries' as filter_type,
    COUNT(*) as count
FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
WHERE
    note_type_desc = 'Documentation Clarification'
    AND jittered_note_date >= '2023-01-01';
```

---

## Query 5 (NEW): Latest Progress Note Before Discharge

Pull the latest progress note for each encounter to use as additional LLM input.
This is the key accuracy improvement — CDI specialists see these notes but the current system doesn't.

```sql
WITH discharge_summaries AS (
    SELECT
        anon_id,
        offest_csn,
        jittered_note_date as discharge_date
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
    WHERE
        note_type = 'Discharge/Transfer Summary'
        AND jittered_note_date >= '2023-01-01'
        AND jittered_note_date < '2025-01-01'
        AND ambulatory = 'N'
        AND offest_csn IS NOT NULL
        AND LENGTH(deid_note_text) > 1000
),

-- Get all progress notes for the same encounters
progress_notes_ranked AS (
    SELECT
        n.anon_id,
        n.offest_csn,
        n.deid_note_text as progress_note,
        n.jittered_note_date as note_date,
        n.note_type,
        d.discharge_date,
        ROW_NUMBER() OVER (
            PARTITION BY n.anon_id, n.offest_csn
            ORDER BY n.jittered_note_date DESC
        ) as rn
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` n
    INNER JOIN discharge_summaries d
        ON n.anon_id = d.anon_id
        AND n.offest_csn = d.offest_csn
    WHERE
        -- Progress-type notes (adjust based on Query 0 results)
        n.note_type IN ('Progress Note, Inpatient', 'History and Physical', 'Consultation Note')
        -- Before or same day as discharge
        AND n.jittered_note_date <= d.discharge_date
        -- Not the discharge summary itself
        AND n.note_type NOT IN ('Discharge/Transfer Summary', 'Progress/Discharge/Transfer Summary')
        -- Substantial content
        AND LENGTH(n.deid_note_text) > 500
)

SELECT
    anon_id,
    offest_csn,
    progress_note,
    note_date,
    note_type,
    discharge_date
FROM progress_notes_ranked
WHERE rn = 1  -- Latest progress note only
ORDER BY discharge_date DESC;
```

---

## Query 6 (NEW): Full Training Dataset with Progress Notes

Combines everything: discharge summary + CDI query + latest progress note:

```sql
WITH discharge_summaries AS (
    SELECT
        anon_id,
        offest_csn,
        deid_note_text as discharge_summary,
        jittered_note_date as discharge_date
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
    WHERE
        note_type = 'Discharge/Transfer Summary'
        AND jittered_note_date >= '2023-01-01'
        AND jittered_note_date < '2025-01-01'
        AND ambulatory = 'N'
        AND offest_csn IS NOT NULL
        AND LENGTH(deid_note_text) > 1000
),

cdi_queries AS (
    SELECT
        anon_id,
        offest_csn,
        deid_note_text as query_text,
        jittered_note_date as query_date,
        author_prov_map_id as cdi_specialist_id
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`
    WHERE
        note_type_desc = 'Documentation Clarification'
        AND jittered_note_date >= '2023-01-01'
        AND jittered_note_date < '2025-01-01'
        AND LOWER(deid_note_text) LIKE '%physician clarification%'
        AND author_prov_map_id IS NOT NULL
        AND offest_csn IS NOT NULL
),

-- Latest progress note per encounter
progress_notes_ranked AS (
    SELECT
        n.anon_id,
        n.offest_csn,
        n.deid_note_text as progress_note,
        n.jittered_note_date as progress_note_date,
        n.note_type as progress_note_type,
        ROW_NUMBER() OVER (
            PARTITION BY n.anon_id, n.offest_csn
            ORDER BY n.jittered_note_date DESC
        ) as rn
    FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` n
    INNER JOIN discharge_summaries d
        ON n.anon_id = d.anon_id
        AND n.offest_csn = d.offest_csn
    WHERE
        n.note_type IN ('Progress Note, Inpatient', 'History and Physical', 'Consultation Note')
        AND n.jittered_note_date <= d.discharge_date
        AND n.note_type NOT IN ('Discharge/Transfer Summary', 'Progress/Discharge/Transfer Summary')
        AND LENGTH(n.deid_note_text) > 500
)

SELECT
    d.anon_id,
    d.offest_csn as encounter_csn,
    d.discharge_date,
    d.discharge_summary,
    p.progress_note,
    p.progress_note_date,
    p.progress_note_type,
    c.query_date,
    c.query_text,
    c.cdi_specialist_id,
    DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) as days_after_discharge
FROM discharge_summaries d
INNER JOIN cdi_queries c
    ON d.anon_id = c.anon_id
    AND d.offest_csn = c.offest_csn
LEFT JOIN progress_notes_ranked p
    ON d.anon_id = p.anon_id
    AND d.offest_csn = p.offest_csn
    AND p.rn = 1
WHERE
    c.query_date >= d.discharge_date
    AND DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) <= 30
ORDER BY d.discharge_date DESC;
```

---

## Notes

1. **Date Jitter**: Dates are jittered for de-identification but relative ordering is preserved
2. **CSN Linking**: `offest_csn` links notes from the same encounter
3. **RCC Section**: Look for "RELEVANT CLINICAL CONDITIONS" to find structured documentation
4. **CDI Queries**: `note_type_desc = 'Documentation Clarification'` identifies CDI queries
5. **note_type vs note_type_desc**: Always use `note_type` for filtering (23 values). `note_type_desc` has 24k+ variations and will miss records.
6. **Progress note types**: Query 5/6 use `'Progress Note, Inpatient'` (1.6M records), `'History and Physical'` (240K), and `'Consultation Note'` (1.3M). You could also consider adding `'IP Consult'` (100K) if you want specialist consultation notes.

## Output Files

Save query results as CSV with these names:
- Query 0 → Review in BigQuery console (exploratory)
- Query 1 → `data/rcc_evaluation_set.csv`
- Query 2 → `data/cdi_linked_discharge.csv`
- Query 3 → `data/cdi_queries_raw.csv`
- Query 4 → Review in BigQuery console (compare old vs new counts)
- Query 5 → `data/progress_notes.csv`
- Query 6 → `data/cdi_linked_discharge_with_progress.csv` (full training set)
