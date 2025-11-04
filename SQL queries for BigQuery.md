The columns in the table `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen`  
column\_name  
ambulatory  
anon\_id  
author\_prov\_map\_id  
deid\_note\_text  
effective\_dept\_id  
jittered\_note\_date  
jittered\_note\_date\_utc  
note\_source\_value  
note\_type  
note\_type\_desc  
offest\_csn

Get discharge summaries with completed .RCC sections 

\-- Fixed: removed problematic REGEXP\_EXTRACT with lookahead SELECT anon\_id, deid\_note\_text, jittered\_note\_date, note\_type, note\_type\_desc, \-- Simple approach: we'll extract RCC in Python instead LENGTH(deid\_note\_text) as note\_length FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` WHERE \-- Must have RCC section UPPER(deid\_note\_text) LIKE '%RELEVANT CLINICAL CONDITIONS%' \-- Must be a discharge-type note AND ( UPPER(note\_type\_desc) LIKE '%DISCHARGE%' OR UPPER(deid\_note\_text) LIKE '%DISCHARGE SUMMARY%' ) \-- Must have at least one checked diagnosis AND deid\_note\_text LIKE '%PRESENT on Admission%' \-- Recent data AND jittered\_note\_date \>= '2023-01-01' \-- Filter out very short notes AND LENGTH(deid\_note\_text) \> 1000 ORDER BY jittered\_note\_date DESC LIMIT 100;

Get CDI queries linked to discharge summaries  
`WITH discharge_summaries AS (`  
 `SELECT`  
   `anon_id,`  
   `offest_csn,`  
   `deid_note_text as discharge_summary,`  
   `jittered_note_date as discharge_date`  
 `` FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` ``  
 `WHERE`  
   `-- Must have RCC section (clinical discharge summary)`  
   `UPPER(deid_note_text) LIKE '%RELEVANT CLINICAL CONDITIONS%'`  
   `-- Must be discharge-type note`  
   `AND (`  
     `UPPER(note_type_desc) LIKE '%DISCHARGE%'`  
     `OR UPPER(deid_note_text) LIKE '%DISCHARGE SUMMARY%'`  
   `)`  
   `-- Must have checkbox format`  
   `AND deid_note_text LIKE '%PRESENT on Admission%'`  
   `-- Date range`  
   `AND jittered_note_date >= '2023-01-01'`  
   `AND jittered_note_date < '2025-01-01'`  
   `-- Inpatient only`  
   `AND ambulatory = 'N'`  
   `-- Must have CSN for linking`  
   `AND offest_csn IS NOT NULL`  
   `-- Substantial content`  
   `AND LENGTH(deid_note_text) > 1000`  
`),`

`cdi_queries AS (`  
 `SELECT`  
   `anon_id,`  
   `offest_csn,`  
   `deid_note_text as query_text,`  
   `jittered_note_date as query_date,`  
   `author_prov_map_id as cdi_specialist_id`  
 `` FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` ``  
 `WHERE`  
   `note_type_desc = 'Documentation Clarification'`  
   `AND jittered_note_date >= '2023-01-01'`  
   `AND jittered_note_date < '2025-01-01'`  
   `AND LOWER(deid_note_text) LIKE '%physician clarification%'`  
   `AND author_prov_map_id IS NOT NULL`  
   `AND offest_csn IS NOT NULL`  
`)`

`-- Link by encounter CSN`  
`SELECT`  
 `d.anon_id,`  
 `d.offest_csn as encounter_csn,`  
 `d.discharge_date,`  
 `d.discharge_summary,  -- Clinical note with RCC`  
 `c.query_date,`  
 `c.query_text,         -- What CDI queried about`  
 `c.cdi_specialist_id,`  
 `DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) as days_after_discharge`  
`FROM discharge_summaries d`  
`INNER JOIN cdi_queries c`  
 `ON d.anon_id = c.anon_id`  
 `AND d.offest_csn = c.offest_csn  -- Same encounter!`  
`WHERE`  
 `c.query_date >= d.discharge_date`  
 `AND DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) <= 30`  
`ORDER BY d.discharge_date DESC`  
`LIMIT 1000;`

Get extra CDI queries  
`WITH discharge_summaries AS (`  
 `SELECT`  
   `anon_id,`  
   `offest_csn,`  
   `deid_note_text as discharge_summary,`  
   `jittered_note_date as discharge_date`  
 `` FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` ``  
 `WHERE`  
   `-- Must have RCC section (clinical discharge summary)`  
   `UPPER(deid_note_text) LIKE '%RELEVANT CLINICAL CONDITIONS%'`  
   `-- Must be discharge-type note`  
   `AND (`  
     `UPPER(note_type_desc) LIKE '%DISCHARGE%'`  
     `OR UPPER(deid_note_text) LIKE '%DISCHARGE SUMMARY%'`  
   `)`  
   `-- Must have checkbox format`  
   `AND deid_note_text LIKE '%PRESENT on Admission%'`  
   `-- Date range`  
   `AND jittered_note_date >= '2023-01-01'`  
   `AND jittered_note_date < '2025-01-01'`  
   `-- Inpatient only`  
   `AND ambulatory = 'N'`  
   `-- Must have CSN for linking`  
   `AND offest_csn IS NOT NULL`  
   `-- Substantial content`  
   `AND LENGTH(deid_note_text) > 1000`  
`),`

`cdi_queries AS (`  
 `SELECT`  
   `anon_id,`  
   `offest_csn,`  
   `deid_note_text as query_text,`  
   `jittered_note_date as query_date,`  
   `author_prov_map_id as cdi_specialist_id`  
 `` FROM `som-nero-phi-jonc101.Deid_Notes_JChen.Deid_Notes_SHC_JChen` ``  
 `WHERE`  
   `note_type_desc = 'Documentation Clarification'`  
   `AND jittered_note_date >= '2023-01-01'`  
   `AND jittered_note_date < '2025-01-01'`  
   `AND LOWER(deid_note_text) LIKE '%physician clarification%'`  
   `AND author_prov_map_id IS NOT NULL`  
   `AND offest_csn IS NOT NULL`  
`)`

`-- Link by encounter CSN`  
`SELECT`  
 `d.anon_id,`  
 `d.offest_csn as encounter_csn,`  
 `d.discharge_date,`  
 `d.discharge_summary,  -- Clinical note with RCC`  
 `c.query_date,`  
 `c.query_text,         -- What CDI queried about`  
 `c.cdi_specialist_id,`  
 `DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) as days_after_discharge`  
`FROM discharge_summaries d`  
`INNER JOIN cdi_queries c`  
 `ON d.anon_id = c.anon_id`  
 `AND d.offest_csn = c.offest_csn  -- Same encounter!`  
`WHERE`  
 `c.query_date >= d.discharge_date`  
 `AND DATE_DIFF(DATE(c.query_date), DATE(d.discharge_date), DAY) <= 30`  
`ORDER BY d.discharge_date DESC`  
`LIMIT 1000;`  
 