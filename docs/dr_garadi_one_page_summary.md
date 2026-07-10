# SafeProbeMD Progress Update

## 1. Objective

Build a safe, evidence-grounded multi-agent clinical reasoning prototype over approved MIMIC-IV admissions, with bounded data routing, claim verification, and human-review fallback.

## 2. What Has Been Built

- Local MIMIC-IV to DuckDB build pipeline
- Admission-level Patient Snapshot extractor
- Bounded agent input routing
- Deterministic Intake, Diagnostic, Lab, Medication, Risk, Verification, Safety/Critic, and Consensus agents
- Batch runner with case-level summary CSV and aggregate metrics JSON
- Readiness checker, data-safety guards, synthetic demo fixture, and documentation

## 3. Current Pipeline

```text
Approved MIMIC-IV files -> DuckDB -> Patient Snapshot JSON
  -> bounded agent packets -> deterministic agents
  -> verification -> safety review -> consensus output
```

## 4. Safety Design

- No agent receives the full patient record
- Verification checks claims against cited source fields
- Unsupported and contradicted claims are excluded from consensus
- Safety/Critic can halt automatic summarization and route to human review
- Real MIMIC data, snapshots, outputs, notes, CSV files, Parquet files, and DuckDB databases are ignored by Git

## 5. Current Status

The codebase is GitHub-safe and contains code, docs, tests, and synthetic/mock examples only. Deterministic tests and readiness checks pass locally. Real MIMIC validation is pending completion of approved local downloads and database build.

## 6. Next 48-Hour Goals

- Build `mimic-iv.duckdb` from approved local files
- Run one admission end to end
- Run a 5-10 admission pilot
- Review `batch_summary.csv` and `aggregate_metrics.json`
- Confirm routing leakage remains zero
- Draft LLM provider interface for deterministic vs LLM-backed comparison

## 7. Questions For Dr. Al-Garadi

- Should first validation use local DuckDB, BigQuery, or VUMC-approved compute?
- Should discharge notes be included, or treated as potential label leakage?
- Which 5-10 admissions should define the first pilot cohort?
- Should SOFA/SAPS-II/APS-III derived tables be required or optional?
- Should initial LLM agents use local open-source models or API-backed models?
