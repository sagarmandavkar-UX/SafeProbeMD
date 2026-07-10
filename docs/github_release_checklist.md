# GitHub Release Checklist

Use this checklist before creating or updating the GitHub repository.

## Data Safety

- Verify no real MIMIC-IV data is committed.
- Verify no real MIMIC-IV-Note data is committed.
- Verify no real discharge note text is committed.
- Verify no patient-derived snapshots or SafeProbeMD outputs are committed.
- Verify no DuckDB, SQLite, CSV, CSV.GZ, or Parquet files are committed.
- Confirm no `snapshots/`, `outputs/`, or `data/` files are staged.

## Local Checks

Run:

```bash
git status --short
python3 scripts/check_project_readiness.py
python3 -m compileall safeprobemd scripts tests
```

Confirm:

- `.gitignore` blocks MIMIC files and generated patient artifacts.
- Readiness checker reports `READY_FOR_LOCAL_MIMIC_TESTING = true`.
- Python files compile.
- Only code, docs, tests, README, and `.gitignore` are staged.

## GitHub Setup

- Create a private GitHub repository first.
- Push only code, documentation, and tests.
- Do not push generated real-data artifacts.
- Do not push local MIMIC file lists such as `hadm_ids.txt`.

## Final Pre-Push Review

Run:

```bash
git status --short
git diff --cached --name-only
```

Stop if any staged path includes:

- `snapshots/`
- `outputs/`
- `data/`
- `.duckdb`
- `.db`
- `.sqlite`
- `.csv`
- `.csv.gz`
- `.parquet`
- real MIMIC-derived JSON
