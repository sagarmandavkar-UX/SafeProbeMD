# Build a Local MIMIC-IV DuckDB Database

This guide explains how to build a local DuckDB database for SafeProbeMD from approved MIMIC-IV and MIMIC-IV-Note files.

Use this only inside an approved, access-controlled research environment. Do not copy real MIMIC data into this repository.

## 1. Required Approved Files

You need local approved copies of these source groups:

- MIMIC-IV hospital files, including:
  - `admissions`
  - `patients`
  - `diagnoses_icd`
  - `d_icd_diagnoses`
  - `procedures_icd`
  - `d_icd_procedures`
  - `labevents`
  - `d_labitems`
  - `prescriptions`
  - `pharmacy`
- MIMIC-IV ICU files:
  - `icustays`
- MIMIC-IV-Note discharge files:
  - `discharge`
- Derived concept tables if available:
  - `age`
  - `weight_durations`
  - `sofa`
  - `sapsii`
  - `apsiii`

SafeProbeMD can run without the optional derived tables, but ICU risk output will be less complete.

## 2. Recommended Local Folder Structure

```text
~/data/mimic/
~/data/mimic/hosp/
~/data/mimic/icu/
~/data/mimic/note/
~/data/mimic/derived/
~/data/mimic-iv.duckdb
```

Expected examples:

```text
~/data/mimic/hosp/admissions.csv.gz
~/data/mimic/hosp/patients.csv.gz
~/data/mimic/icu/icustays.csv.gz
~/data/mimic/note/discharge.csv.gz
~/data/mimic/derived/sofa.csv.gz
```

The build script supports `.csv`, `.csv.gz`, and `.parquet` layouts. Use `--format auto` when Safari or another tool has decompressed some files from `.csv.gz` to `.csv`.

## 3. Create DuckDB Schemas

Manual DuckDB SQL:

```sql
CREATE SCHEMA IF NOT EXISTS mimiciv_hosp;
CREATE SCHEMA IF NOT EXISTS mimiciv_icu;
CREATE SCHEMA IF NOT EXISTS mimiciv_note;
CREATE SCHEMA IF NOT EXISTS mimiciv_derived;
```

Python equivalent:

```python
import duckdb

con = duckdb.connect("~/data/mimic-iv.duckdb")
for schema in ["mimiciv_hosp", "mimiciv_icu", "mimiciv_note", "mimiciv_derived"]:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
```

## 4. Create Tables from CSV, CSV.GZ, or Parquet

CSV/CSV.GZ:

```sql
CREATE OR REPLACE TABLE mimiciv_hosp.admissions AS
SELECT *
FROM read_csv_auto('~/data/mimic/hosp/admissions.csv.gz', header=true);
```

Parquet:

```sql
CREATE OR REPLACE TABLE mimiciv_hosp.admissions AS
SELECT *
FROM read_parquet('~/data/mimic/hosp/admissions.parquet');
```

Views are also possible, but SafeProbeMD's local prototype is simplest when the DuckDB file contains persistent tables:

```sql
CREATE OR REPLACE VIEW mimiciv_hosp.admissions AS
SELECT *
FROM read_csv_auto('~/data/mimic/hosp/admissions.csv.gz', header=true);
```

## 5. Build With the SafeProbeMD Script

```bash
python3 scripts/build_mimic_duckdb_from_files.py \
  --mimic-root ~/data/mimic \
  --db-path ~/data/mimic-iv.duckdb \
  --format auto
```

Supported formats:

```text
auto
csv.gz
csv
parquet
```

With `--format auto`, each table is searched independently in this order:

```text
<table>.csv
<table>.csv.gz
<table>.parquet
```

This handles mixed folders where some files are still compressed and others were automatically decompressed by Safari.

The script creates these schemas:

- `mimiciv_hosp`
- `mimiciv_icu`
- `mimiciv_note`
- `mimiciv_derived`

It loads the required SafeProbeMD core tables and skips optional derived tables that are not present, while printing clear warnings.

## 6. Verify Required Tables

Run:

```bash
python3 - <<'PY'
import duckdb

con = duckdb.connect("~/data/mimic-iv.duckdb", read_only=True)
required = [
    ("mimiciv_hosp", "admissions"),
    ("mimiciv_hosp", "patients"),
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
]
rows = con.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
""").fetchall()
available = set(rows)
missing = [f"{schema}.{table}" for schema, table in required if (schema, table) not in available]
print("missing_required_tables =", missing)
PY
```

Expected:

```text
missing_required_tables = []
```

## 7. Verify Optional Derived Tables

Run:

```bash
python3 - <<'PY'
import duckdb

con = duckdb.connect("~/data/mimic-iv.duckdb", read_only=True)
optional = [
    ("mimiciv_derived", "age"),
    ("mimiciv_derived", "weight_durations"),
    ("mimiciv_derived", "sofa"),
    ("mimiciv_derived", "sapsii"),
    ("mimiciv_derived", "apsiii"),
]
available = set(con.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
""").fetchall())
missing = [f"{schema}.{table}" for schema, table in optional if (schema, table) not in available]
print("missing_optional_derived_tables =", missing)
PY
```

Missing optional tables are allowed, but the extractor will add warnings and leave related risk or weight fields null.

## 8. Run a One-Admission Extraction

```bash
python3 scripts/extract_patient_snapshot_duckdb.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-id <HADM_ID> \
  --output snapshots/<HADM_ID>.json
```

Then run SafeProbeMD:

```bash
python3 scripts/run_safeprobemd_case.py \
  --snapshot snapshots/<HADM_ID>.json \
  --output outputs/<HADM_ID>_safeprobemd_output.json
```

## 9. Data Safety Warning

Do not commit:

- Real MIMIC files
- The generated DuckDB database
- Real snapshots
- Real outputs
- Discharge note text
- CSV, CSV.GZ, or Parquet exports
- Any MIMIC-derived patient data

Keep real data local and access-controlled. The repository should contain only code, documentation, tests, and clearly labeled synthetic/mock examples.
