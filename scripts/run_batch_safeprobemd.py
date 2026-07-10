"""Run SafeProbeMD extraction and multi-agent processing for a hadm_id batch."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


SUMMARY_COLUMNS = [
    "hadm_id",
    "snapshot_path",
    "output_path",
    "extraction_succeeded",
    "pipeline_succeeded",
    "n_verified_claims",
    "n_unsupported_claims",
    "n_contradicted_claims",
    "halted",
    "requires_human_review",
    "n_data_quality_warnings",
    "error_message",
]

AGGREGATE_METRICS_FILENAME = "aggregate_metrics.json"


@dataclass(frozen=True)
class BatchArgs:
    db_path: Path
    hadm_ids_file: Path
    snapshot_dir: Path
    output_dir: Path
    summary_csv: Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str]], CommandResult]


def parse_args(argv: list[str] | None = None) -> BatchArgs:
    parser = argparse.ArgumentParser(description="Run SafeProbeMD over a local hadm_id batch.")
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--hadm-ids-file", required=True, type=Path)
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary-csv", required=True, type=Path)
    ns = parser.parse_args(argv)
    return BatchArgs(
        db_path=ns.db_path.expanduser(),
        hadm_ids_file=ns.hadm_ids_file,
        snapshot_dir=ns.snapshot_dir,
        output_dir=ns.output_dir,
        summary_csv=ns.summary_csv,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_batch(args)
    return 0


def run_batch(args: BatchArgs, runner: Runner | None = None) -> list[dict[str, Any]]:
    if runner is None:
        runner = run_command
    hadm_ids = read_hadm_ids(args.hadm_ids_file)
    args.snapshot_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.summary_csv.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.perf_counter()
    rows = [run_one_case(args, hadm_id, runner) for hadm_id in hadm_ids]
    runtime_seconds_total = time.perf_counter() - start_time
    write_summary_csv(rows, args.summary_csv)
    write_aggregate_metrics(
        compute_aggregate_metrics(rows, runtime_seconds_total),
        aggregate_metrics_path(args.output_dir),
    )
    return rows


def read_hadm_ids(path: Path) -> list[str]:
    hadm_ids: list[str] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            hadm_ids.append(stripped)
    return hadm_ids


def run_one_case(args: BatchArgs, hadm_id: str, runner: Runner) -> dict[str, Any]:
    snapshot_path = args.snapshot_dir / f"{hadm_id}.json"
    output_path = args.output_dir / f"{hadm_id}_safeprobemd_output.json"
    row = empty_summary_row(hadm_id, snapshot_path, output_path)
    errors: list[str] = []

    extract_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "extract_patient_snapshot_duckdb.py"),
        "--db-path",
        str(args.db_path),
        "--hadm-id",
        hadm_id,
        "--output",
        str(snapshot_path),
    ]
    extract_result = runner(extract_cmd)
    if extract_result.returncode == 0:
        row["extraction_succeeded"] = True
        row["n_data_quality_warnings"] = count_data_quality_warnings(snapshot_path)
    else:
        errors.append(command_error("extraction", extract_result))

    if row["extraction_succeeded"]:
        pipeline_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_safeprobemd_case.py"),
            "--snapshot",
            str(snapshot_path),
            "--output",
            str(output_path),
        ]
        pipeline_result = runner(pipeline_cmd)
        if pipeline_result.returncode == 0:
            row["pipeline_succeeded"] = True
            row.update(parse_pipeline_output(output_path))
        else:
            errors.append(command_error("pipeline", pipeline_result))
    else:
        row["pipeline_succeeded"] = False

    row["error_message"] = " | ".join(error for error in errors if error)
    return row


def empty_summary_row(hadm_id: str, snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    return {
        "hadm_id": hadm_id,
        "snapshot_path": str(snapshot_path),
        "output_path": str(output_path),
        "extraction_succeeded": False,
        "pipeline_succeeded": False,
        "n_verified_claims": "",
        "n_unsupported_claims": "",
        "n_contradicted_claims": "",
        "halted": "",
        "requires_human_review": "",
        "n_data_quality_warnings": "",
        "error_message": "",
    }


def run_command(cmd: list[str]) -> CommandResult:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def parse_pipeline_output(output_path: Path) -> dict[str, Any]:
    with output_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    summary = data.get("verification_report", {}).get("summary", {})
    final = data.get("final_output", {})
    return {
        "n_verified_claims": summary.get("n_verified", ""),
        "n_unsupported_claims": summary.get("n_unsupported", ""),
        "n_contradicted_claims": summary.get("n_contradicted", ""),
        "halted": final.get("halted", ""),
        "requires_human_review": final.get("requires_human_review", ""),
    }


def count_data_quality_warnings(snapshot_path: Path) -> int | str:
    try:
        with snapshot_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except FileNotFoundError:
        return ""
    warnings = data.get("data_quality", {}).get("warnings", [])
    return len(warnings) if isinstance(warnings, list) else ""


def write_summary_csv(rows: list[dict[str, Any]], summary_csv: Path) -> None:
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


def aggregate_metrics_path(output_dir: Path) -> Path:
    return output_dir / AGGREGATE_METRICS_FILENAME


def compute_aggregate_metrics(rows: list[dict[str, Any]], runtime_seconds_total: float) -> dict[str, Any]:
    total_cases = len(rows)
    extracted_rows = [row for row in rows if bool_value(row.get("extraction_succeeded"))]
    pipeline_rows = [row for row in rows if bool_value(row.get("pipeline_succeeded"))]

    verified_values = numeric_values(pipeline_rows, "n_verified_claims")
    unsupported_values = numeric_values(pipeline_rows, "n_unsupported_claims")
    contradicted_values = numeric_values(pipeline_rows, "n_contradicted_claims")
    warning_values = numeric_values(extracted_rows, "n_data_quality_warnings")

    total_verified = sum(verified_values)
    total_unsupported = sum(unsupported_values)
    total_contradicted = sum(contradicted_values)
    total_claims = total_verified + total_unsupported + total_contradicted
    bounded_runtime = max(float(runtime_seconds_total), 0.0)

    return {
        "total_cases": total_cases,
        "extraction_success_rate": round(rate(len(extracted_rows), total_cases), 6),
        "pipeline_success_rate": round(rate(len(pipeline_rows), total_cases), 6),
        "mean_verified_claims": round(mean(verified_values), 6),
        "mean_unsupported_claims": round(mean(unsupported_values), 6),
        "mean_contradicted_claims": round(mean(contradicted_values), 6),
        "unsupported_claim_rate": round(rate(total_unsupported, total_claims), 6),
        "contradicted_claim_rate": round(rate(total_contradicted, total_claims), 6),
        "halt_rate": round(
            rate(sum(1 for row in pipeline_rows if bool_value(row.get("halted"))), len(pipeline_rows)),
            6,
        ),
        "human_review_rate": round(
            rate(sum(1 for row in pipeline_rows if bool_value(row.get("requires_human_review"))), len(pipeline_rows)),
            6,
        ),
        "mean_data_quality_warnings": round(mean(warning_values), 6),
        "runtime_seconds_total": round(bounded_runtime, 6),
        "runtime_seconds_mean_per_case": round(rate(bounded_runtime, total_cases), 6),
    }


def write_aggregate_metrics(metrics: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(metrics, fp, indent=2, sort_keys=True)
        fp.write("\n")


def numeric_values(rows: list[dict[str, Any]], key: str) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = int_value(row.get(key))
        if value is not None:
            values.append(value)
    return values


def int_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def command_error(stage: str, result: CommandResult) -> str:
    message = (result.stderr or result.stdout or "").strip()
    if not message:
        message = f"returncode={result.returncode}"
    return f"{stage} failed: {message}"


if __name__ == "__main__":
    raise SystemExit(main())
