# SafeProbeMD Architecture

SafeProbeMD is a research prototype for safe, evidence-grounded multi-agent clinical reasoning over approved MIMIC-IV data. Real MIMIC-IV and MIMIC-IV-Note data are not included in this repository.

## Pipeline Flow

```text
MIMIC-IV tables
  -> DuckDB database
  -> Patient Snapshot JSON
  -> Bounded Agent Routing
  -> Intake Context Agent
  -> Diagnostic Reasoning Agent
  -> Lab Interpretation Agent
  -> Medication Safety Agent
  -> Risk/Acuity Agent
  -> Verification Agent
  -> Safety/Critic Agent
  -> Consensus Agent
  -> Final SafeProbeMD Output
```

## Core Design

The Patient Snapshot JSON is the admission-level data contract. It contains demographics, admission frame, diagnoses, procedures, curated labs, medications, ICU summary, section-parsed discharge note content, derived risk scores, and data-quality metadata.

The routing layer converts the snapshot into bounded packets. No agent receives the full patient record. For example, the Lab Interpretation Agent receives curated labs but not the discharge note, diagnoses, medications, or derived ICU risk scores. The Medication Safety Agent receives medications plus renal/hepatic safety context, not the full clinical note or full lab record.

Verification checks agent claims against cited source fields from the Patient Snapshot. Each grounded claim carries provenance paths such as `labs.0.valuenum` or `diagnoses.1.description`; unsupported paths and contradicted values are marked before consensus.

The Safety/Critic Agent reviews verification results, agent flags, data gaps, and minimal patient frame information. It can trigger human review when contradictions, missing safety-critical context, or high-acuity conditions make automatic summarization unsafe.

The Consensus Agent excludes unsupported and contradicted claims. If the safety path recommends halting, the final output is bounded and routes to human review rather than leaking partial clinical content.

Deterministic mode is the default for reproducibility. This allows the research team to validate extraction, routing, verification, safety fallback, and aggregate evaluation before introducing optional LLM-backed agents.

## Safety Boundary

SafeProbeMD operates on one approved admission snapshot at a time. It should never fan out raw MIMIC tables, full datasets, or unrestricted patient records to agents or external services.
