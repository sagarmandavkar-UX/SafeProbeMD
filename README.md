# SafeProbeMD

SafeProbeMD is a research prototype for safe, evidence-grounded multi-agent clinical reasoning over approved MIMIC-IV data. The repository contains code, tests, and documentation only; no patient data is included.

SafeProbeMD converts one approved MIMIC-IV hospital admission into a structured Patient Snapshot JSON, routes bounded slices of that snapshot to specialized agents, verifies claims against source fields, applies a safety critic, and produces a final consensus output for human review.

## Architecture

```text
Approved MIMIC-IV / MIMIC-IV-Note files
  -> local DuckDB database
  -> Patient Snapshot extraction by hadm_id
  -> bounded agent input routing
  -> Intake Context Agent
  -> Diagnostic, Lab, Medication, Risk agents
  -> Verification Agent
  -> Safety/Critic Agent
  -> Consensus Agent
  -> final SafeProbeMD JSON
```

The current prototype is deterministic. It does not call external LLMs, clinical APIs, or treatment recommendation services. This lets us test routing, grounding, verification, safety fallback, and batch evaluation before adding optional LLM-backed agents.

## Setup

Use Python 3. The local MIMIC extraction path also requires `duckdb` in the approved data environment.

```bash
python3 -m compileall safeprobemd scripts tests
python3 scripts/check_project_readiness.py
```

`pytest` can be used when installed:

```bash
python3 -m pytest tests/
```

## Run Synthetic Demo

The repository includes one clearly fake Patient Snapshot for GitHub-safe demonstration. It is fully synthetic and is not derived from MIMIC-IV, MIMIC-IV-Note, Vanderbilt data, or any patient record.

```bash
bash examples/run_demo.sh
```

Equivalent command:

```bash
python3 scripts/run_safeprobemd_case.py \
  --snapshot examples/mock_patient_snapshot.json \
  --output outputs/mock_case_safeprobemd_output.json
```

The demo output is written under `outputs/`, which remains ignored by Git.

## Data Safety

Never commit real MIMIC-IV data, MIMIC-IV-Note data, real snapshots, real SafeProbeMD outputs, discharge note text, DuckDB/database files, CSV exports, Parquet files, or any MIMIC-derived patient data.

Keep real data local, access-controlled, and inside the approved research environment. The repository is intended to contain code, documentation, tests, and clearly synthetic/mock examples only.

Real MIMIC data is not included in this repository.

## Build DuckDB From Approved Local Files

Expected local layout:

```text
~/data/mimic/
~/data/mimic/hosp/
~/data/mimic/icu/
~/data/mimic/note/
~/data/mimic/derived/
~/data/mimic-iv.duckdb
```

Build the local DuckDB database:

```bash
python3 scripts/build_mimic_duckdb_from_files.py \
  --mimic-root ~/data/mimic \
  --db-path ~/data/mimic-iv.duckdb \
  --format auto
```

`--format auto` searches each table as `.csv`, `.csv.gz`, then `.parquet`, which handles Safari-decompressed MIMIC files. See [docs/build_mimic_duckdb_local.md](docs/build_mimic_duckdb_local.md).

## Extract One Patient Snapshot

```bash
python3 scripts/extract_patient_snapshot_duckdb.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-id <HADM_ID> \
  --output snapshots/<HADM_ID>.json
```

Generated snapshots from real MIMIC data must remain local and uncommitted.

## Run One SafeProbeMD Case

```bash
python3 scripts/run_safeprobemd_case.py \
  --snapshot snapshots/<HADM_ID>.json \
  --output outputs/<HADM_ID>_safeprobemd_output.json
```

The output contains `agent_outputs`, `verification_report`, `safety_report`, and `final_output`. Consensus uses verified claims only; unsupported or contradicted claims are excluded, and unsafe cases can halt to human review.

## Batch Mode

```bash
python3 scripts/run_batch_safeprobemd.py \
  --db-path ~/data/mimic-iv.duckdb \
  --hadm-ids-file hadm_ids.txt \
  --snapshot-dir snapshots/ \
  --output-dir outputs/ \
  --summary-csv outputs/batch_summary.csv
```

The batch runner continues after individual failures and writes one summary row per `hadm_id`.
It also writes `aggregate_metrics.json` in the configured `--output-dir`. The aggregate file contains only counts, rates, and runtime metrics; it must not contain patient identifiers, note text, labs, diagnoses, medications, or other case content.

See [docs/evaluation_metrics.md](docs/evaluation_metrics.md) for metric definitions.

## Design Docs

- [Architecture](docs/architecture.md)
- [LLM integration plan](docs/llm_integration_plan.md)
- [Evaluation metrics](docs/evaluation_metrics.md)
- [GitHub release checklist](docs/github_release_checklist.md)

## Readiness Checker

```bash
python3 scripts/check_project_readiness.py
```

The checker verifies required docs/scripts/tests, `.gitignore` data-safety patterns, README command coverage, and Python compilation. It does not access real MIMIC data and does not require `duckdb`.

## Repository Status

Real MIMIC-IV data is not included. Real MIMIC-IV-Note discharge text is not included. Generated real patient snapshots, outputs, DuckDB databases, CSV files, CSV.GZ files, and Parquet files are intentionally ignored by `.gitignore`.

The current codebase includes:

- Deterministic multi-agent SafeProbeMD prototype
- DuckDB database builder for approved local files
- Patient Snapshot extractor
- Bounded agent routing
- Verification, Safety/Critic, and Consensus agents
- Batch runner
- Readiness checker
- Documentation and tests
