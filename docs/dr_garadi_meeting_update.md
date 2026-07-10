# SafeProbeMD MIMIC-IV Integration: Meeting Update

## What Has Been Built

- MIMIC-IV DuckDB build pipeline from approved local files, with support for mixed `.csv`, `.csv.gz`, and `.parquet` inputs.
- Admission-level Patient Snapshot extractor from DuckDB to SafeProbeMD JSON.
- Bounded agent input routing so agents do not receive the full patient record.
- Deterministic multi-agent SafeProbeMD prototype.
- Verification Agent that checks claims against cited source fields.
- Safety/Critic Agent that flags unsupported, contradicted, or unsafe outputs.
- Consensus Agent that uses verified claims only and can route unsafe cases to human review.
- Batch runner for extracting and processing multiple `hadm_id` values.
- Readiness checker for required files, docs, safety rules, and Python compilation.
- Data-safety guardrails in `.gitignore` and documentation to prevent committing MIMIC-derived artifacts.

## Current Status

- Code passes the local readiness checker:

```text
READY_FOR_LOCAL_MIMIC_TESTING = true
```

- Tests pass through direct test execution. `pytest` is not installed in the current local Python environment, so tests were executed directly by loading each test function.
- Real MIMIC validation is pending final large downloads:
  - `labevents`
  - MIMIC-IV-Note discharge note file

## What The Prototype Demonstrates

- Raw MIMIC-IV tables can be converted into structured admission-level Patient Snapshots.
- Each agent receives only the information needed for its role.
- Agent claims carry provenance and are checked against source fields.
- Unsupported or contradicted claims can be excluded from final output.
- Unsafe outputs can trigger a human-review fallback instead of producing an overconfident summary.

## Near-Term Next Steps

- Build local `mimic-iv.duckdb` from approved MIMIC-IV and MIMIC-IV-Note files.
- Run one admission end to end.
- Run a 5-10 admission pilot.
- Summarize `batch_summary.csv` for verification, safety, halt, and data-quality patterns.
- Add optional LLM provider interface behind the current deterministic agent contracts.
- Compare deterministic agents vs. LLM-backed agents under the same routing, verification, and consensus framework.

## Questions For Dr. Al-Garadi

- Should the first real-data run use local DuckDB, BigQuery, or VUMC-approved compute?
- Should discharge notes be used, or do they create label leakage for diagnostic reasoning?
- Which 5-10 admissions should we use for the first pilot?
- Should SOFA/SAPS-II/APS-III derived tables be required for inclusion?
- Should LLM agents use local open-source models first or API-backed models?
