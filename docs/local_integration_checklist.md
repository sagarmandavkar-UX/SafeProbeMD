# Local Integration Checklist

This checklist is for running SafeProbeMD on real approved MIMIC-IV and MIMIC-IV-Note data in a local, access-controlled research environment.

Do not run these steps on unapproved data, shared machines, or directories that sync to cloud storage. Do not commit real MIMIC-derived artifacts.

## 1. Prerequisites

- `duckdb` is installed in the active Python environment:

```bash
python3 - <<'PY'
import duckdb
print(duckdb.__version__)
PY
```

- You have approved access to MIMIC-IV and MIMIC-IV-Note.
- You have a local DuckDB database containing the required schemas:
  - `mimiciv_hosp`
  - `mimiciv_icu`
  - `mimiciv_note`
  - `mimiciv_derived` when available
- You know the local database path, for example:

```text
~/data/mimic-iv.duckdb
```

- No real patient data is committed to Git. This includes snapshots, outputs, notes, CSV exports, database files, Parquet files, and any MIMIC-derived patient rows.

## 2. Choose 5 Test Admissions

Pick 5 `hadm_id` values that exercise different pipeline paths:

- One ward-only admission with `icu_summary.has_icu = false`.
- One ICU admission with available SOFA/SAPS-II/APS-III derived scores.
- One admission with a discharge note in `mimiciv_note.discharge`.
- One admission with both labs and medication orders.
- One admission with missing or partial data, such as no discharge note, no medications, no derived ICU scores, or sparse labs.

Keep the selected IDs in a local file that is not committed:

```text
hadm_ids.txt
```

## 3. Run One Extraction

```bash
python3 scripts/extract_patient_snapshot_duckdb.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-id <HADM_ID> \
  --output snapshots/<HADM_ID>.json
```

Inspect the generated snapshot locally only. If the snapshot contains real MIMIC-derived data, do not commit it.

## 4. Run One SafeProbeMD Case

```bash
python3 scripts/run_safeprobemd_case.py \
  --snapshot snapshots/<HADM_ID>.json \
  --output outputs/<HADM_ID>_safeprobemd_output.json
```

## 5. Inspect After Each Run

For each extracted admission, review:

- `data_quality.warnings` in the snapshot.
- `verification_report.summary` in the SafeProbeMD output.
- `safety_report`.
- Whether `final_output.halted` is `true`.
- Whether `final_output.requires_human_review` is `true`.
- Whether unsupported or contradicted claims appear in `final_output.unsupported_claims_excluded`.
- Whether unsupported or contradicted claims were excluded from `problem_list`, `key_labs`, and `medication_safety_alerts`.

## 6. Never Commit

Never commit files or content matching these categories:

- `*.duckdb`
- `*.db`
- `*.csv`
- `*.csv.gz`
- `*.parquet`
- Real `snapshots/`
- Real `outputs/`
- Discharge note text
- Any MIMIC-derived patient data

The checked-in mock case is synthetic. Real MIMIC-derived snapshots and outputs must remain local.

## 7. Batch Run

Once one-case extraction works, run a small batch:

```bash
python3 scripts/run_batch_safeprobemd.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-ids-file hadm_ids.txt \
  --snapshot-dir snapshots/ \
  --output-dir outputs/ \
  --summary-csv outputs/batch_summary.csv
```

The batch runner continues after individual case failures and writes one summary row per `hadm_id`.

## 8. Success Criteria Before Updating Dr. Al-Garadi

- 5-10 admissions run successfully.
- No routing leakage is detected.
- No crashes occur during extraction or SafeProbeMD execution.
- `verification_report.summary` is generated for each successful pipeline output.
- `safety_report` is generated for each successful pipeline output.
- `outputs/batch_summary.csv` is generated and reviewed.
- Real MIMIC-derived snapshots, outputs, notes, CSV files, and database files remain uncommitted.
