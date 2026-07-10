# DuckDB Extraction Plan for One MIMIC-IV Admission Snapshot

**Status:** implementation plan only; do not implement the full script yet  
**Target future script:** `scripts/extract_patient_snapshot_duckdb.py`  
**Target output:** one JSON file matching the Patient Snapshot schema in `mimic_iv_data_map.md`  
**Extraction unit:** one `hadm_id`  
**Prototype backend:** DuckDB  

This document translates:

- `SafeProbeMD_MIMIC_IV_Integration_Report.md`
- `mimic_iv_data_map.md`
- `safeprobemd_agent_input_schema.md`

into a concrete extraction plan for producing one admission-level SafeProbeMD patient snapshot.

The first implementation should be intentionally narrow: given a DuckDB database and one `hadm_id`, extract demographics, admission frame, diagnoses, procedures, curated labs, medications, ICU summary, selected derived scores, and one section-parsed discharge note. The extractor should write exactly one JSON file.

---

## 1. Required Database Schemas

The DuckDB database should already contain official MIMIC-IV, MIMIC-IV-Note, and derived concept tables. This plan assumes fully qualified schema names and does not rely on DuckDB `search_path`.

### 1.1 Required Schemas

| Schema | Required | Purpose |
|---|---:|---|
| `mimiciv_hosp` | yes | Hospital admissions, demographics, diagnoses, procedures, labs, medications |
| `mimiciv_icu` | yes | ICU stay bridge and ICU stay metadata |
| `mimiciv_derived` | yes for ICU scores | Age, weight, SOFA, SAPS-II, APS-III |
| `mimiciv_note` | yes for notes | Discharge note text |

### 1.2 Required Tables

Minimum required:

- `mimiciv_hosp.patients`
- `mimiciv_hosp.admissions`
- `mimiciv_hosp.diagnoses_icd`
- `mimiciv_hosp.d_icd_diagnoses`
- `mimiciv_hosp.procedures_icd`
- `mimiciv_hosp.d_icd_procedures`
- `mimiciv_hosp.labevents`
- `mimiciv_hosp.d_labitems`
- `mimiciv_hosp.prescriptions`
- `mimiciv_hosp.pharmacy`
- `mimiciv_icu.icustays`
- `mimiciv_note.discharge`

Derived tables required for richer output:

- `mimiciv_derived.age`
- `mimiciv_derived.weight_durations`
- `mimiciv_derived.sofa`
- `mimiciv_derived.sapsii`
- `mimiciv_derived.apsiii`

The first prototype should continue when optional derived tables are missing, but it must add a `data_quality.warning` and set the relevant JSON fields to `null`.

### 1.3 Expected DuckDB Layout

The database may be created by the official MIMIC-IV DuckDB loader and then enriched with the official DuckDB concept scripts. The final database should expose tables like:

```text
mimiciv_hosp.admissions
mimiciv_hosp.patients
mimiciv_icu.icustays
mimiciv_derived.sofa
mimiciv_note.discharge
```

No patient data should be copied outside the approved MIMIC environment except controlled snapshot JSON artifacts created for approved research use.

---

## 2. Required Input Arguments

Future CLI:

```text
python scripts/extract_patient_snapshot_duckdb.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-id <HADM_ID> \
  --output snapshots/<HADM_ID>.json
```

Required arguments:

| Argument | Type | Required | Description |
|---|---|---:|---|
| `--db-path` | path | yes | DuckDB file containing MIMIC-IV, MIMIC-IV-Note, and derived schemas |
| `--hadm-id` | integer | yes | Hospital admission ID to extract |
| `--output` | path | yes | JSON output path |

Recommended optional arguments:

| Argument | Type | Default | Description |
|---|---|---:|---|
| `--max-note-chars` | integer | `60000` | Hard cap for discharge note text before parsing |
| `--max-labs` | integer | `500` | Safety cap for curated lab rows |
| `--strict` | boolean | `false` | If true, fail on missing required blocks instead of writing warnings |
| `--include-raw-note` | boolean | `false` | Keep raw note text in memory for parsing, but do not write full raw text unless later approved |
| `--pipeline-version` | string | `0.1` | Snapshot generation version |

Internal extraction arguments:

```python
class ExtractArgs:
    db_path: Path
    hadm_id: int
    output_path: Path
    max_note_chars: int = 60000
    max_labs: int = 500
    strict: bool = False
    include_raw_note: bool = False
    pipeline_version: str = "0.1"
```

---

## 3. Extraction Order

The future script should run in this order:

1. Connect to DuckDB in read-only mode.
2. Validate required schemas/tables.
3. Fetch admission and demographics for `hadm_id`.
4. Resolve ICU stays and choose one selected ICU stay.
5. Fetch weight for selected ICU stay if available.
6. Fetch diagnoses.
7. Fetch procedures.
8. Fetch curated labs.
9. Fetch medications.
10. Fetch discharge note.
11. Parse discharge note sections.
12. Fetch derived risk scores for selected ICU stay.
13. Build Patient Snapshot JSON.
14. Validate Patient Snapshot JSON.
15. Write JSON.

---

## 4. SQL Query Blocks

DuckDB Python supports parameterized queries with `?` placeholders. All blocks below should be run through bound parameters, not string interpolation.

### 4.1 Schema and Table Availability

Purpose:

- Check that required source tables exist before extraction.
- Optional derived tables can be marked as unavailable.

```sql
SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE (table_schema, table_name) IN (
    ('mimiciv_hosp', 'patients'),
    ('mimiciv_hosp', 'admissions'),
    ('mimiciv_hosp', 'diagnoses_icd'),
    ('mimiciv_hosp', 'd_icd_diagnoses'),
    ('mimiciv_hosp', 'procedures_icd'),
    ('mimiciv_hosp', 'd_icd_procedures'),
    ('mimiciv_hosp', 'labevents'),
    ('mimiciv_hosp', 'd_labitems'),
    ('mimiciv_hosp', 'prescriptions'),
    ('mimiciv_hosp', 'pharmacy'),
    ('mimiciv_icu', 'icustays'),
    ('mimiciv_note', 'discharge'),
    ('mimiciv_derived', 'age'),
    ('mimiciv_derived', 'weight_durations'),
    ('mimiciv_derived', 'sofa'),
    ('mimiciv_derived', 'sapsii'),
    ('mimiciv_derived', 'apsiii')
)
ORDER BY table_schema, table_name;
```

Python should compare the returned set to required and optional table sets.

### 4.2 Admission and Demographics

Purpose:

- One row per `hadm_id`.
- Supplies `ids`, `demographics`, and `admission` blocks.
- Uses `mimiciv_derived.age` if available.

```sql
SELECT
    ad.subject_id,
    ad.hadm_id,
    pa.gender,
    COALESCE(
        age.age,
        pa.anchor_age + DATE_DIFF(
            'year',
            CAST(CAST(pa.anchor_year AS VARCHAR) || '-01-01' AS TIMESTAMP),
            ad.admittime
        )
    ) AS age,
    ad.race,
    ad.language,
    ad.insurance,
    ad.marital_status,
    ad.admittime AS admit_time,
    ad.dischtime AS discharge_time,
    ad.admission_type,
    ad.admission_location,
    ad.discharge_location,
    ad.hospital_expire_flag,
    CASE
        WHEN ad.dischtime IS NULL THEN NULL
        ELSE DATE_DIFF('second', ad.admittime, ad.dischtime) / 86400.0
    END AS los_days
FROM mimiciv_hosp.admissions ad
INNER JOIN mimiciv_hosp.patients pa
    ON ad.subject_id = pa.subject_id
LEFT JOIN mimiciv_derived.age age
    ON ad.subject_id = age.subject_id
   AND ad.hadm_id = age.hadm_id
WHERE ad.hadm_id = ?;
```

Fallback if `mimiciv_derived.age` is unavailable:

- Run the same query without the `LEFT JOIN mimiciv_derived.age`.
- Keep the anchor-year age calculation.
- Add warning `derived_age_table_missing`.

### 4.3 ICU Stay Candidates and Selection

Purpose:

- Find all ICU stays for the admission.
- Select the highest-acuity ICU stay:
  1. Highest max SOFA
  2. Highest SAPS-II
  3. Highest APS-III
  4. Longest ICU LOS

```sql
WITH sofa_ranked AS (
    SELECT
        stay_id,
        sofa_24hours,
        respiration_24hours,
        coagulation_24hours,
        liver_24hours,
        cardiovascular_24hours,
        cns_24hours,
        renal_24hours,
        endtime,
        ROW_NUMBER() OVER (
            PARTITION BY stay_id
            ORDER BY sofa_24hours DESC NULLS LAST, endtime DESC NULLS LAST
        ) AS rn
    FROM mimiciv_derived.sofa
),
sofa_best AS (
    SELECT
        stay_id,
        sofa_24hours AS sofa_max,
        respiration_24hours AS respiration,
        coagulation_24hours AS coagulation,
        liver_24hours AS liver,
        cardiovascular_24hours AS cardiovascular,
        cns_24hours AS cns,
        renal_24hours AS renal,
        endtime AS sofa_time
    FROM sofa_ranked
    WHERE rn = 1
),
candidates AS (
    SELECT
        ie.subject_id,
        ie.hadm_id,
        ie.stay_id,
        ie.first_careunit,
        ie.last_careunit,
        ie.intime,
        ie.outtime,
        ie.los AS icu_los_days,
        sofa_best.sofa_max,
        sofa_best.respiration,
        sofa_best.coagulation,
        sofa_best.liver,
        sofa_best.cardiovascular,
        sofa_best.cns,
        sofa_best.renal,
        sofa_best.sofa_time,
        saps.sapsii,
        saps.sapsii_prob,
        aps.apsiii,
        aps.apsiii_prob,
        COUNT(*) OVER () AS n_icu_stays
    FROM mimiciv_icu.icustays ie
    LEFT JOIN sofa_best
        ON ie.stay_id = sofa_best.stay_id
    LEFT JOIN mimiciv_derived.sapsii saps
        ON ie.stay_id = saps.stay_id
    LEFT JOIN mimiciv_derived.apsiii aps
        ON ie.stay_id = aps.stay_id
    WHERE ie.hadm_id = ?
)
SELECT *
FROM candidates
ORDER BY
    sofa_max DESC NULLS LAST,
    sapsii DESC NULLS LAST,
    apsiii DESC NULLS LAST,
    icu_los_days DESC NULLS LAST,
    intime ASC NULLS LAST
LIMIT 1;
```

Fallback when derived score tables are unavailable:

```sql
SELECT
    ie.subject_id,
    ie.hadm_id,
    ie.stay_id,
    ie.first_careunit,
    ie.last_careunit,
    ie.intime,
    ie.outtime,
    ie.los AS icu_los_days,
    COUNT(*) OVER () AS n_icu_stays
FROM mimiciv_icu.icustays ie
WHERE ie.hadm_id = ?
ORDER BY ie.los DESC NULLS LAST, ie.intime ASC NULLS LAST
LIMIT 1;
```

If no rows return:

- `icu_summary.has_icu = false`
- `ids.selected_stay_id = null`
- `derived_risk_scores.has_icu_scores = false`
- Add warning `no_icu_stay`.

### 4.4 Weight for Selected ICU Stay

Purpose:

- Populate `demographics.weight_kg`.
- Use the earliest admission-type weight first; otherwise the earliest available ICU weight.

Run only when `selected_stay_id` is not null and `mimiciv_derived.weight_durations` exists.

```sql
SELECT
    stay_id,
    weight AS weight_kg,
    weight_type,
    starttime,
    endtime
FROM mimiciv_derived.weight_durations
WHERE stay_id = ?
ORDER BY
    CASE WHEN weight_type = 'admit' THEN 0 ELSE 1 END,
    starttime ASC NULLS LAST
LIMIT 1;
```

If missing:

- `demographics.weight_kg = null`
- Add warning `missing_weight_kg` if medications exist.

### 4.5 Diagnoses

Purpose:

- Populate `diagnoses[]`.
- ICD codes are retrospective labels and should later be routed according to the agent schema.

```sql
SELECT
    dx.icd_code,
    CAST(dx.icd_version AS VARCHAR) AS icd_version,
    dx.seq_num,
    dd.long_title AS description
FROM mimiciv_hosp.diagnoses_icd dx
LEFT JOIN mimiciv_hosp.d_icd_diagnoses dd
    ON dx.icd_code = dd.icd_code
   AND dx.icd_version = dd.icd_version
WHERE dx.hadm_id = ?
ORDER BY dx.seq_num ASC, dx.icd_version, dx.icd_code;
```

If no rows:

- Return empty list.
- Add warning `no_diagnosis_codes`.

### 4.6 Procedures

Purpose:

- Populate `procedures[]`.

```sql
SELECT
    pr.icd_code,
    CAST(pr.icd_version AS VARCHAR) AS icd_version,
    pr.seq_num,
    dp.long_title AS description
FROM mimiciv_hosp.procedures_icd pr
LEFT JOIN mimiciv_hosp.d_icd_procedures dp
    ON pr.icd_code = dp.icd_code
   AND pr.icd_version = dp.icd_version
WHERE pr.hadm_id = ?
ORDER BY pr.seq_num ASC, pr.chartdate ASC NULLS LAST, pr.icd_version, pr.icd_code;
```

If no rows:

- Return empty list.
- This is not a hard failure.

### 4.7 Curated Labs

Purpose:

- Populate `labs[]`.
- Include:
  - selected first-24h labs after admission
  - abnormal labs from full admission
  - renal and hepatic safety labs
  - organ dysfunction and severity-relevant labs

The first implementation should use a Python constant for `CORE_LAB_ITEMIDS`, then generate a DuckDB temporary table or `VALUES` CTE. Keep the list maintainable rather than embedding a giant SQL string everywhere.

Recommended first-pass item groups:

```python
CORE_LAB_ITEMIDS = {
    # Chemistry / renal / metabolic
    50868, 50882, 50893, 50902, 50912, 50931, 50971, 50983, 51006,
    # CBC
    51221, 51222, 51265, 51301,
    # Coagulation
    51237, 51274, 51275,
    # Liver / enzymes
    50861, 50863, 50878, 50885, 50883, 50960,
    # Blood gas / lactate
    50813, 50818, 50820, 50821, 50816, 50817,
    # Cardiac markers, first pass
    51002, 50889
}

RENAL_SAFETY_ITEMIDS = {50912, 51006, 50971}
HEPATIC_SAFETY_ITEMIDS = {50861, 50863, 50878, 50885, 50883, 50960}
```

The exact item list should be reviewed before publication. The official derived concept SQL is the source of truth for mature lab groupings.

Query shape:

```sql
WITH target_admission AS (
    SELECT
        hadm_id,
        subject_id,
        admittime,
        dischtime
    FROM mimiciv_hosp.admissions
    WHERE hadm_id = ?
),
selected_itemids(itemid) AS (
    VALUES
        -- populated by Python from CORE_LAB_ITEMIDS
        (50868), (50882), (50912)
),
candidate_labs AS (
    SELECT
        le.labevent_id,
        le.subject_id,
        le.hadm_id,
        le.itemid,
        dl.label,
        dl.category,
        dl.fluid,
        le.charttime,
        le.valuenum,
        le.value,
        le.valueuom,
        le.ref_range_lower,
        le.ref_range_upper,
        le.flag,
        CASE
            WHEN le.charttime >= ta.admittime
             AND le.charttime < ta.admittime + INTERVAL 24 HOUR
                THEN 'first_24h'
            WHEN LOWER(COALESCE(le.flag, '')) IN ('abnormal')
                THEN 'abnormal'
            WHEN le.itemid IN (50912, 51006, 50971)
                THEN 'renal_safety'
            WHEN le.itemid IN (50861, 50863, 50878, 50885, 50883, 50960)
                THEN 'hepatic_safety'
            ELSE 'core_lab'
        END AS selection_reason
    FROM target_admission ta
    INNER JOIN mimiciv_hosp.labevents le
        ON le.hadm_id = ta.hadm_id
    LEFT JOIN mimiciv_hosp.d_labitems dl
        ON le.itemid = dl.itemid
    INNER JOIN selected_itemids si
        ON le.itemid = si.itemid
    WHERE le.charttime IS NOT NULL
      AND (
            (le.charttime >= ta.admittime
             AND le.charttime < ta.admittime + INTERVAL 24 HOUR)
         OR LOWER(COALESCE(le.flag, '')) IN ('abnormal')
         OR le.itemid IN (50912, 51006, 50971, 50861, 50863, 50878, 50885, 50883, 50960)
      )
      AND (
            ta.dischtime IS NULL
         OR le.charttime <= ta.dischtime + INTERVAL 6 HOUR
      )
)
SELECT
    itemid,
    label,
    category,
    charttime,
    valuenum,
    value,
    valueuom,
    ref_range_lower,
    ref_range_upper,
    flag,
    selection_reason
FROM candidate_labs
ORDER BY charttime ASC, label ASC, itemid ASC
LIMIT ?;
```

Notes:

- `labevent_id` should be retained internally for provenance even though the current Patient Snapshot schema does not expose it.
- If the final schema is extended, add `source_table` and `source_key`.
- MIMIC `flag` mainly marks abnormality; it is not a complete critical-value flag.

If no rows:

- Return empty list.
- Add warning `no_labs`.

### 4.8 Medications

Purpose:

- Populate `medications[]`.
- Use `prescriptions` as the first prototype source.
- Join `pharmacy` for frequency enrichment when possible.

```sql
SELECT
    pr.drug,
    pr.dose_val_rx,
    pr.dose_unit_rx,
    pr.route,
    COALESCE(ph.frequency, pr.route) AS frequency,
    pr.starttime,
    pr.stoptime,
    CAST(pr.pharmacy_id AS VARCHAR) AS pharmacy_id
FROM mimiciv_hosp.prescriptions pr
LEFT JOIN mimiciv_hosp.pharmacy ph
    ON pr.pharmacy_id = ph.pharmacy_id
WHERE pr.hadm_id = ?
ORDER BY
    pr.starttime ASC NULLS LAST,
    pr.stoptime ASC NULLS LAST,
    pr.drug ASC;
```

If no rows:

- Return empty list.
- Add warning `no_medications`.

Known first-pass limitation:

- `prescriptions` are orders, not proof of administration.
- eMAR integration is intentionally deferred.

### 4.9 Discharge Note

Purpose:

- Populate `clinical_note`.
- Select the latest discharge note for the admission.
- Parse sections in Python.

```sql
SELECT
    note_id,
    subject_id,
    hadm_id,
    note_type,
    note_seq,
    charttime,
    storetime,
    text
FROM mimiciv_note.discharge
WHERE hadm_id = ?
ORDER BY charttime DESC NULLS LAST, note_seq DESC NULLS LAST, storetime DESC NULLS LAST
LIMIT 1;
```

If no row:

- `clinical_note.note_id = ""`
- All section strings empty.
- `clinical_note.section_parse_quality = "missing"`
- Add warning `missing_discharge_note`.

### 4.10 Derived Risk Scores for Selected ICU Stay

Purpose:

- Populate `derived_risk_scores`.
- Use the already selected ICU `stay_id`.

Run only when `selected_stay_id` is not null.

```sql
WITH sofa_ranked AS (
    SELECT
        stay_id,
        sofa_24hours,
        respiration_24hours,
        coagulation_24hours,
        liver_24hours,
        cardiovascular_24hours,
        cns_24hours,
        renal_24hours,
        endtime,
        ROW_NUMBER() OVER (
            PARTITION BY stay_id
            ORDER BY sofa_24hours DESC NULLS LAST, endtime DESC NULLS LAST
        ) AS rn
    FROM mimiciv_derived.sofa
    WHERE stay_id = ?
)
SELECT
    sr.sofa_24hours AS sofa_max,
    sr.respiration_24hours AS respiration,
    sr.coagulation_24hours AS coagulation,
    sr.liver_24hours AS liver,
    sr.cardiovascular_24hours AS cardiovascular,
    sr.cns_24hours AS cns,
    sr.renal_24hours AS renal,
    saps.sapsii,
    saps.sapsii_prob,
    aps.apsiii,
    aps.apsiii_prob
FROM sofa_ranked sr
LEFT JOIN mimiciv_derived.sapsii saps
    ON sr.stay_id = saps.stay_id
LEFT JOIN mimiciv_derived.apsiii aps
    ON sr.stay_id = aps.stay_id
WHERE sr.rn = 1;
```

Fallback if `sofa` is missing but SAPS-II and APS-III exist:

```sql
SELECT
    NULL AS sofa_max,
    NULL AS respiration,
    NULL AS coagulation,
    NULL AS liver,
    NULL AS cardiovascular,
    NULL AS cns,
    NULL AS renal,
    saps.sapsii,
    saps.sapsii_prob,
    aps.apsiii,
    aps.apsiii_prob
FROM mimiciv_icu.icustays ie
LEFT JOIN mimiciv_derived.sapsii saps
    ON ie.stay_id = saps.stay_id
LEFT JOIN mimiciv_derived.apsiii aps
    ON ie.stay_id = aps.stay_id
WHERE ie.stay_id = ?;
```

If no ICU:

- `derived_risk_scores.has_icu_scores = false`
- all score fields null.

### 4.11 Optional Block Counts for Data Quality

Purpose:

- Support warnings without extra Python loops over extracted lists.

```sql
SELECT
    (SELECT COUNT(*) FROM mimiciv_hosp.diagnoses_icd WHERE hadm_id = ?) AS diagnosis_count,
    (SELECT COUNT(*) FROM mimiciv_hosp.procedures_icd WHERE hadm_id = ?) AS procedure_count,
    (SELECT COUNT(*) FROM mimiciv_hosp.labevents WHERE hadm_id = ?) AS lab_count,
    (SELECT COUNT(*) FROM mimiciv_hosp.prescriptions WHERE hadm_id = ?) AS medication_count,
    (SELECT COUNT(*) FROM mimiciv_icu.icustays WHERE hadm_id = ?) AS icu_stay_count,
    (SELECT COUNT(*) FROM mimiciv_note.discharge WHERE hadm_id = ?) AS discharge_note_count;
```

Bind `hadm_id` six times.

---

## 5. Python Function Structure

No full script yet. The future implementation should use small extraction functions that return plain dictionaries/lists and never mutate the final snapshot directly until `build_snapshot()`.

### 5.1 Module-Level Constants

```python
SNAPSHOT_SCHEMA_VERSION = "0.1"

REQUIRED_TABLES = {
    ("mimiciv_hosp", "patients"),
    ("mimiciv_hosp", "admissions"),
    ("mimiciv_hosp", "diagnoses_icd"),
    ("mimiciv_hosp", "d_icd_diagnoses"),
    ("mimiciv_hosp", "procedures_icd"),
    ("mimiciv_hosp", "d_icd_procedures"),
    ("mimiciv_hosp", "labevents"),
    ("mimiciv_hosp", "d_labitems"),
    ("mimiciv_hosp", "prescriptions"),
    ("mimiciv_hosp", "pharmacy"),
    ("mimiciv_icu", "icustays"),
    ("mimiciv_note", "discharge"),
}

OPTIONAL_DERIVED_TABLES = {
    ("mimiciv_derived", "age"),
    ("mimiciv_derived", "weight_durations"),
    ("mimiciv_derived", "sofa"),
    ("mimiciv_derived", "sapsii"),
    ("mimiciv_derived", "apsiii"),
}
```

### 5.2 Main Orchestration

```python
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    con = connect_duckdb(args.db_path)

    available_tables = get_available_tables(con)
    validate_required_tables(available_tables, strict=args.strict)

    context = extract_admission_context(con, args.hadm_id, available_tables)
    icu = extract_selected_icu_stay(con, args.hadm_id, available_tables)
    weight = extract_weight_kg(con, icu["selected_stay_id"], available_tables)
    diagnoses = extract_diagnoses(con, args.hadm_id)
    procedures = extract_procedures(con, args.hadm_id)
    labs = extract_labs(con, args.hadm_id, max_labs=args.max_labs)
    medications = extract_medications(con, args.hadm_id)
    note = extract_discharge_note(con, args.hadm_id, args.max_note_chars)
    note_sections = parse_discharge_note_sections(note)
    risk_scores = extract_derived_risk_scores(con, icu["selected_stay_id"], available_tables)

    snapshot = build_snapshot(
        args=args,
        context=context,
        icu=icu,
        weight=weight,
        diagnoses=diagnoses,
        procedures=procedures,
        labs=labs,
        medications=medications,
        note=note,
        note_sections=note_sections,
        risk_scores=risk_scores,
    )

    validation = validate_snapshot(snapshot, strict=args.strict)
    snapshot["data_quality"]["warnings"].extend(validation.warnings)
    snapshot["data_quality"]["missing_blocks"].extend(validation.missing_blocks)

    write_json(snapshot, args.output_path)
    return 0
```

### 5.3 Connection and Table Validation

```python
def connect_duckdb(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open DuckDB in read-only mode."""

def get_available_tables(con) -> set[tuple[str, str]]:
    """Return available (schema, table) pairs from information_schema.tables."""

def validate_required_tables(
    available_tables: set[tuple[str, str]],
    strict: bool,
) -> None:
    """Raise for missing required tables; warn for missing optional derived tables."""
```

### 5.4 Extraction Functions

```python
def extract_admission_context(
    con,
    hadm_id: int,
    available_tables: set[tuple[str, str]],
) -> dict:
    """Return one admission/demographics row. Raise if hadm_id is not found."""

def extract_selected_icu_stay(
    con,
    hadm_id: int,
    available_tables: set[tuple[str, str]],
) -> dict:
    """Return selected ICU stay summary using highest-acuity rule, or has_icu=false."""

def extract_weight_kg(
    con,
    selected_stay_id: int | None,
    available_tables: set[tuple[str, str]],
) -> dict | None:
    """Return selected weight row for the selected ICU stay."""

def extract_diagnoses(con, hadm_id: int) -> list[dict]:
    """Return ICD diagnoses with descriptions ordered by seq_num."""

def extract_procedures(con, hadm_id: int) -> list[dict]:
    """Return ICD procedures with descriptions ordered by seq_num."""

def extract_labs(con, hadm_id: int, max_labs: int) -> list[dict]:
    """Return curated first-24h plus abnormal/safety labs."""

def extract_medications(con, hadm_id: int) -> list[dict]:
    """Return medication orders from prescriptions, enriched with pharmacy frequency."""

def extract_discharge_note(
    con,
    hadm_id: int,
    max_note_chars: int,
) -> dict | None:
    """Return latest discharge note for this admission, with text truncated in memory if needed."""

def extract_derived_risk_scores(
    con,
    selected_stay_id: int | None,
    available_tables: set[tuple[str, str]],
) -> dict:
    """Return max SOFA components, SAPS-II, and APS-III for selected stay."""
```

### 5.5 Note Parsing Functions

The first prototype should use deterministic section parsing. Do not call an LLM inside the extractor.

```python
SECTION_ALIASES = {
    "chief complaint": "chief_complaint",
    "history of present illness": "history_of_present_illness",
    "hpi": "history_of_present_illness",
    "past medical history": "past_medical_history",
    "pmh": "past_medical_history",
    "hospital course": "hospital_course",
    "brief hospital course": "hospital_course",
    "discharge diagnosis": "discharge_diagnosis",
    "discharge diagnoses": "discharge_diagnosis",
    "discharge medications": "discharge_medications",
    "medications on discharge": "discharge_medications",
    "followup instructions": "follow_up",
    "follow up": "follow_up",
    "follow-up": "follow_up",
}
```

```python
def parse_discharge_note_sections(note: dict | None) -> dict:
    """Return sections and parse metadata matching clinical_note.sections."""

def normalize_section_header(header: str) -> str:
    """Lowercase, strip punctuation, map through SECTION_ALIASES."""

def estimate_section_parse_quality(sections: dict[str, str], raw_text: str) -> str:
    """Return high, medium, low, or missing."""
```

Parse-quality recommendation:

- `high`: at least 5 target sections present, including `hospital_course` or `discharge_diagnosis`.
- `medium`: at least 2 target sections present.
- `low`: raw note exists but parser finds fewer than 2 target sections.
- `missing`: no discharge note.

### 5.6 Snapshot Assembly

```python
def build_snapshot(
    args: ExtractArgs,
    context: dict,
    icu: dict,
    weight: dict | None,
    diagnoses: list[dict],
    procedures: list[dict],
    labs: list[dict],
    medications: list[dict],
    note: dict | None,
    note_sections: dict,
    risk_scores: dict,
) -> dict:
    """Build the final Patient Snapshot JSON exactly as defined in mimic_iv_data_map.md."""
```

Assembly rules:

- Convert integer IDs to strings in JSON:
  - `ids.patient_id = str(subject_id)`
  - `ids.admission_id = str(hadm_id)`
  - `ids.selected_stay_id = selected_stay_id or null`
- Convert timestamps to ISO-like strings.
- Keep missing scalar values as `null` where schema uses `null`.
- Keep missing strings as `""` only where the schema shows an empty string.
- Preserve list order from SQL.
- Put all warnings in `data_quality.warnings`.
- Put missing high-level blocks in `data_quality.missing_blocks`.

### 5.7 Validation and Output

```python
def validate_snapshot(snapshot: dict, strict: bool) -> ValidationResult:
    """Check required fields, warning conditions, type shape, and date logic."""

def write_json(snapshot: dict, output_path: Path) -> None:
    """Create parent directory if needed and write pretty JSON with sorted keys disabled."""
```

---

## 6. Expected JSON Output

The output JSON file must match the Patient Snapshot schema in `mimic_iv_data_map.md`.

Path:

```text
snapshots/<HADM_ID>.json
```

Top-level shape:

```json
{
  "schema_version": "0.1",
  "source": {
    "dataset": "MIMIC-IV + MIMIC-IV-Note",
    "repo": "MIT-LCP/mimic-code",
    "extraction_timestamp": "2026-07-08T00:00:00-05:00",
    "extraction_unit": "hadm_id"
  },
  "ids": {
    "patient_id": "string",
    "admission_id": "string",
    "selected_stay_id": null
  },
  "demographics": {
    "age": null,
    "gender": "",
    "race": "",
    "language": "",
    "insurance": "",
    "marital_status": "",
    "weight_kg": null
  },
  "admission": {
    "admit_time": "",
    "discharge_time": "",
    "admission_type": "",
    "admission_location": "",
    "discharge_location": "",
    "hospital_expire_flag": null,
    "los_days": null
  },
  "diagnoses": [],
  "procedures": [],
  "labs": [],
  "medications": [],
  "icu_summary": {
    "has_icu": false,
    "selected_stay_id": null,
    "selection_rule": "highest_acuity",
    "n_icu_stays": 0,
    "first_careunit": "",
    "last_careunit": "",
    "intime": "",
    "outtime": "",
    "los_days": null
  },
  "clinical_note": {
    "note_id": "",
    "note_type": "discharge",
    "sections": {
      "chief_complaint": "",
      "history_of_present_illness": "",
      "past_medical_history": "",
      "hospital_course": "",
      "discharge_diagnosis": "",
      "discharge_medications": "",
      "follow_up": ""
    },
    "section_parse_quality": "",
    "text_truncated": false
  },
  "derived_risk_scores": {
    "has_icu_scores": false,
    "sofa_max": null,
    "sofa_components": {
      "respiration": null,
      "coagulation": null,
      "liver": null,
      "cardiovascular": null,
      "cns": null,
      "renal": null
    },
    "sapsii": null,
    "sapsii_prob": null,
    "apsiii": null,
    "apsiii_prob": null
  },
  "data_quality": {
    "missing_blocks": [],
    "warnings": [],
    "extraction_notes": []
  }
}
```

Example warning object:

```json
{
  "type": "missing_discharge_note",
  "severity": "high",
  "message": "No discharge note found for this hadm_id."
}
```

For consistency with `mimic_iv_data_map.md`, the first implementation may store warnings either as strings or objects, but object warnings are preferred because they are easier to route and audit.

---

## 7. Validation Checks

### 7.1 Hard Failures

The extractor should fail before writing JSON when:

- DuckDB file does not exist.
- Required schema/table is missing.
- `hadm_id` is not found in `mimiciv_hosp.admissions`.
- More than one admission row is returned for `hadm_id`.
- Output path is not writable.
- JSON serialization fails.

In `--strict` mode, also fail when:

- No clinical note and no diagnosis codes.
- Admission time is missing.
- Discharge time is missing.
- Age is missing.

### 7.2 Warning Conditions

Add `data_quality.warnings` for:

- `no_discharge_note`
- `no_diagnosis_codes`
- `no_labs`
- `no_medications`
- `no_icu_stay`
- `multiple_icu_stays`
- `missing_age`
- `missing_weight_kg`
- `missing_medication_dose`
- `missing_lab_units`
- `missing_derived_age_table`
- `missing_derived_score_table`
- `note_truncated`
- `low_note_parse_quality`
- `contradictory_death_outcome_fields`

### 7.3 Shape Checks

Validate:

- Top-level keys exactly match the Patient Snapshot schema.
- `ids.patient_id` and `ids.admission_id` are non-empty strings.
- `diagnoses`, `procedures`, `labs`, and `medications` are lists.
- `clinical_note.sections` includes all required section keys.
- `derived_risk_scores.sofa_components` includes all six SOFA components.
- All timestamps are serialized as strings or null before writing JSON.

### 7.4 Clinical Consistency Checks

Validate:

- `admission.discharge_time >= admission.admit_time` when both exist.
- ICU `intime` and `outtime` fall inside or near the hospital admission window.
- Selected ICU stay is one of the ICU stays for the target `hadm_id`.
- Labs included by first-24h rule satisfy `admit_time <= charttime < admit_time + 24h`.
- Full-admission abnormal labs occur no later than discharge plus a 6-hour tolerance.
- Medication `stoptime >= starttime` when both exist.
- `hospital_expire_flag` does not obviously contradict `patients.dod` if later added.

### 7.5 Agent-Routing Compatibility Checks

Because the snapshot feeds `safeprobemd_agent_input_schema.md`, validate:

- Intake can be built from `ids`, `demographics`, `admission`, `icu_summary`.
- Diagnostic can be built from `clinical_note`, `diagnoses`, `procedures`.
- Lab can be built from `labs`, `demographics.age`, `demographics.gender`.
- Medication can be built from `medications`, `demographics.weight_kg`, renal/hepatic lab subset, diagnoses subset.
- Risk can be built from `derived_risk_scores`, `icu_summary`, abnormal lab subset.
- Verification can resolve every field path cited by downstream agents.

The extractor does not perform routing yet, but it should preserve fields needed by the router.

---

## 8. Known Limitations

### 8.1 Data Coverage

- First prototype extracts one `hadm_id` only.
- ED data is not included.
- Radiology notes are not included.
- eMAR medication administration is not included.
- Microbiology/culture data is not included.
- ICU raw time series are not included except through derived concepts.

### 8.2 ICU Selection

- Only one ICU stay is selected for risk scores.
- Non-selected ICU stays are not represented beyond `n_icu_stays`.
- Highest-acuity selection depends on derived scores; if score tables are missing, the fallback is longest ICU LOS.

### 8.3 Labs

- Lab curation uses a maintained itemid list plus abnormal flags.
- MIMIC-IV `flag` does not fully encode critical values.
- Lab provenance keys are not currently represented in the Patient Snapshot schema.
- Exact lab item lists should be reviewed against official derived concept SQL before publication.

### 8.4 Medications

- `prescriptions` are medication orders, not confirmed administrations.
- Dose strings may be non-numeric and should not be parsed in the first prototype.
- Frequency comes from `pharmacy` when joinable; otherwise it may be blank or imperfect.
- Drug interaction checking is not part of the extractor.

### 8.5 Notes

- Discharge note section parsing is deterministic and approximate.
- Discharge summaries are retrospective and can leak final diagnoses.
- The snapshot schema stores section text, not detailed note-span offsets.
- Note truncation may cut sections unless handled carefully.

### 8.6 Derived Scores

- SOFA, SAPS-II, and APS-III are risk signals, not diagnoses.
- SOFA is hourly; this plan uses maximum SOFA for selected stay.
- Missing score components are not imputed by the extractor beyond what official concepts already did.

### 8.7 Security and Governance

- Snapshot JSON can contain clinical note text and should be treated as controlled MIMIC-derived data.
- Do not commit generated snapshots to public repositories.
- Do not route raw discharge notes to every agent; keep the routing policy from `safeprobemd_agent_input_schema.md`.

---

## 9. Implementation Checklist

Before writing `scripts/extract_patient_snapshot_duckdb.py`, confirm:

- DuckDB database has `mimiciv_hosp`, `mimiciv_icu`, `mimiciv_note`, and `mimiciv_derived`.
- Derived concepts were generated from the official MIMIC code release used for the study.
- The target `hadm_id` exists and has at least one diagnosis or discharge note.
- The first-pass `CORE_LAB_ITEMIDS` list is accepted by the research team.
- Warning object format is finalized.
- Generated JSON is allowed to contain sectioned note text in the approved storage location.

The implementation should then follow the function order in Section 5 and the query blocks in Section 4.
