# Repository Structure

This repository is GitHub-safe by design: it should contain code, tests, and documentation only. Real MIMIC-IV data and generated patient artifacts are excluded.

## Included Folders

### `safeprobemd/`

Core SafeProbeMD Python package. It contains the deterministic orchestration layer and shared package metadata.

### `safeprobemd/agents/`

Agent implementations and shared agent utilities:

- Intake Context Agent
- Diagnostic Reasoning Agent
- Lab Interpretation Agent
- Medication Safety Agent
- Risk/Acuity Agent
- Verification Agent
- Safety/Critic Agent
- Consensus Agent

### `safeprobemd/llm/`

Not present yet. This is the intended future location for optional LLM provider interfaces once deterministic contracts are stable.

### `scripts/`

Command-line utilities:

- Build a local DuckDB database from approved MIMIC files.
- Extract one Patient Snapshot by `hadm_id`.
- Build inspectable initial routed agent inputs.
- Run one SafeProbeMD case.
- Run a batch of admissions.
- Check project readiness.

### `tests/`

Unit and integration-style tests using synthetic data only. Tests cover routing safety, verification behavior, extractor helper logic, batch summary behavior, and the DuckDB build script with tiny synthetic files.

### `docs/`

Design notes, extraction plans, local integration instructions, evaluation metrics, meeting updates, GitHub release checklist, and repository structure documentation.

## Excluded Folders

### `snapshots/`

Excluded by `.gitignore`. Real Patient Snapshot JSON files may contain MIMIC-derived patient data and must remain local.

### `outputs/`

Excluded by `.gitignore`. Real SafeProbeMD outputs may contain patient-derived summaries, verification reports, and safety reports.

### `data/`

Excluded by `.gitignore`. Real MIMIC-IV files, local DuckDB databases, CSV files, CSV.GZ files, and Parquet files must not be committed.
