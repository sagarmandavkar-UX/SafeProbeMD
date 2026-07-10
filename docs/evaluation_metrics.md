# SafeProbeMD Evaluation Metrics

This document defines the initial metrics emitted by the SafeProbeMD local prototype. Metrics are intended for small approved MIMIC-IV pilots and must be computed without storing real patient text or case content in aggregate artifacts.

## Extraction Metrics

- `extraction_success_rate`: fraction of requested admissions where Patient Snapshot extraction completed successfully.
- `missing_required_block_count`: number of required Patient Snapshot blocks absent from a generated snapshot.
- `data_quality_warning_count`: number of extractor warnings for a case, such as missing optional tables, no ICU stay, no discharge note, no labs, or truncated note text.

## Routing Safety Metrics

- `routing_leakage_count`: number of agent input packets containing fields outside that agent's approved routing boundary.
- `agent_input_size_estimate`: approximate serialized input size per agent, useful as a proxy for prompt/token budget.
- `full_snapshot_fanout_detected`: boolean indicator that a raw full snapshot was routed to an agent that should receive only a bounded slice.

## Verification Metrics

- `n_verified_claims`: number of claims whose cited field paths and values are supported by the Patient Snapshot.
- `n_unsupported_claims`: number of claims citing nonexistent, missing, or insufficient evidence.
- `n_contradicted_claims`: number of claims citing an existing field but asserting a value inconsistent with the snapshot.
- `unsupported_claim_rate`: total unsupported claims divided by total checked claims.
- `contradicted_claim_rate`: total contradicted claims divided by total checked claims.

## Safety Metrics

- `halt_rate`: fraction of completed pipeline cases where the Safety/Critic or Consensus path halted automatic summarization.
- `human_review_rate`: fraction of completed pipeline cases marked as requiring human review.
- `medication_safety_alert_count`: number of medication-related safety alerts raised for a case or batch.
- `high_acuity_case_count`: number of cases meeting a predefined high-acuity rule, such as ICU stay, high SOFA/SAPS-II/APS-III, or safety-critical lab abnormalities.

## Efficiency Metrics

- `runtime_seconds_per_case`: wall-clock runtime for extraction plus SafeProbeMD processing for one admission.
- `estimated_tokens_per_agent`: approximate prompt/input token count for each routed agent packet.
- `estimated_total_tokens_per_case`: approximate total token budget across all routed agent packets for one case.

## Pilot Outputs

- `batch_summary.csv`: case-level operational summary keyed by local `hadm_id`, intended for local approved environments only.
- `aggregate_metrics.json`: de-identified batch-level counts, rates, and runtime statistics. The batch runner writes this file to the configured `--output-dir`, for example `outputs/aggregate_metrics.json`.

Current `aggregate_metrics.json` fields:

- `total_cases`
- `extraction_success_rate`
- `pipeline_success_rate`
- `mean_verified_claims`
- `mean_unsupported_claims`
- `mean_contradicted_claims`
- `unsupported_claim_rate`
- `contradicted_claim_rate`
- `halt_rate`
- `human_review_rate`
- `mean_data_quality_warnings`
- `runtime_seconds_total`
- `runtime_seconds_mean_per_case`

`extraction_success_rate` and `pipeline_success_rate` use all requested admissions as the denominator. Claim means and claim rates use completed pipeline outputs. `halt_rate` and `human_review_rate` use completed pipeline outputs as the denominator. `mean_data_quality_warnings` uses successfully extracted snapshots as the denominator.

## Data Safety

Do not store real patient text, discharge note text, raw labs, diagnoses, medications, procedures, `subject_id`, `hadm_id`, or MIMIC-derived case content in `aggregate_metrics.json`. Aggregate files should contain only counts, rates, and runtime values.

Keep `batch_summary.csv`, real snapshots, real outputs, and DuckDB databases local to the approved environment unless a specific de-identification and sharing workflow has been approved.
