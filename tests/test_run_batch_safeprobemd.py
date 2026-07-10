from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_batch_safeprobemd import (
    AGGREGATE_METRICS_FILENAME,
    BatchArgs,
    CommandResult,
    SUMMARY_COLUMNS,
    aggregate_metrics_path,
    compute_aggregate_metrics,
    parse_pipeline_output,
    read_hadm_ids,
    run_batch,
    write_summary_csv,
)


def test_read_hadm_ids_file_skips_blanks_and_comments() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "hadm_ids.txt"
        path.write_text("\n# comment\n100\n 200 \n\n300\n", encoding="utf-8")

        assert read_hadm_ids(path) == ["100", "200", "300"]


def test_batch_continues_after_one_failed_hadm_id() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        args = batch_args(root)
        args.hadm_ids_file.write_text("100\n200\n", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_runner(cmd: list[str]) -> CommandResult:
            calls.append(cmd)
            hadm_id = cmd[cmd.index("--hadm-id") + 1] if "--hadm-id" in cmd else ""
            if hadm_id == "100":
                return CommandResult(returncode=1, stderr="extract failed")
            if "extract_patient_snapshot_duckdb.py" in cmd[1]:
                output = Path(cmd[cmd.index("--output") + 1])
                write_snapshot(output)
                return CommandResult(returncode=0)
            output = Path(cmd[cmd.index("--output") + 1])
            write_pipeline_output(output, verified=3, unsupported=1, contradicted=0)
            return CommandResult(returncode=0)

        rows = run_batch(args, runner=fake_runner)

        assert len(rows) == 2
        assert rows[0]["hadm_id"] == "100"
        assert rows[0]["extraction_succeeded"] is False
        assert rows[0]["pipeline_succeeded"] is False
        assert "extract failed" in rows[0]["error_message"]
        assert rows[1]["hadm_id"] == "200"
        assert rows[1]["extraction_succeeded"] is True
        assert rows[1]["pipeline_succeeded"] is True
        assert rows[1]["n_verified_claims"] == 3
        assert len(calls) == 3
        assert aggregate_metrics_path(args.output_dir).exists()


def test_summary_csv_column_shape() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "summary.csv"
        rows = [
            {
                "hadm_id": "100",
                "snapshot_path": "snapshots/100.json",
                "output_path": "outputs/100_safeprobemd_output.json",
                "extraction_succeeded": True,
                "pipeline_succeeded": True,
            }
        ]

        write_summary_csv(rows, path)

        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            assert reader.fieldnames == SUMMARY_COLUMNS
            parsed = list(reader)
        assert parsed[0]["hadm_id"] == "100"
        assert parsed[0]["n_verified_claims"] == ""


def test_parse_verification_summary_from_output_json() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "output.json"
        write_pipeline_output(path, verified=7, unsupported=2, contradicted=1)

        parsed = parse_pipeline_output(path)

        assert parsed["n_verified_claims"] == 7
        assert parsed["n_unsupported_claims"] == 2
        assert parsed["n_contradicted_claims"] == 1
        assert parsed["halted"] is False
        assert parsed["requires_human_review"] is True


def test_compute_aggregate_metrics_counts_rates_without_identifiers() -> None:
    rows = [
        {
            "hadm_id": "111",
            "subject_id": "999",
            "snapshot_path": "snapshots/111.json",
            "output_path": "outputs/111_safeprobemd_output.json",
            "extraction_succeeded": True,
            "pipeline_succeeded": True,
            "n_verified_claims": 3,
            "n_unsupported_claims": 1,
            "n_contradicted_claims": 0,
            "halted": False,
            "requires_human_review": True,
            "n_data_quality_warnings": 2,
        },
        {
            "hadm_id": "222",
            "subject_id": "888",
            "snapshot_path": "snapshots/222.json",
            "output_path": "outputs/222_safeprobemd_output.json",
            "extraction_succeeded": True,
            "pipeline_succeeded": True,
            "n_verified_claims": 1,
            "n_unsupported_claims": 0,
            "n_contradicted_claims": 1,
            "halted": True,
            "requires_human_review": True,
            "n_data_quality_warnings": 0,
        },
        {
            "hadm_id": "333",
            "subject_id": "777",
            "snapshot_path": "snapshots/333.json",
            "output_path": "outputs/333_safeprobemd_output.json",
            "extraction_succeeded": False,
            "pipeline_succeeded": False,
            "n_verified_claims": "",
            "n_unsupported_claims": "",
            "n_contradicted_claims": "",
            "halted": "",
            "requires_human_review": "",
            "n_data_quality_warnings": "",
        },
    ]

    metrics = compute_aggregate_metrics(rows, runtime_seconds_total=12.0)

    assert metrics == {
        "total_cases": 3,
        "extraction_success_rate": 0.666667,
        "pipeline_success_rate": 0.666667,
        "mean_verified_claims": 2.0,
        "mean_unsupported_claims": 0.5,
        "mean_contradicted_claims": 0.5,
        "unsupported_claim_rate": 0.166667,
        "contradicted_claim_rate": 0.166667,
        "halt_rate": 0.5,
        "human_review_rate": 1.0,
        "mean_data_quality_warnings": 1.0,
        "runtime_seconds_total": 12.0,
        "runtime_seconds_mean_per_case": 4.0,
    }
    forbidden_keys = {"hadm_id", "subject_id", "snapshot_path", "output_path", "clinical_note", "labs", "diagnoses"}
    assert forbidden_keys.isdisjoint(metrics)


def test_run_batch_writes_aggregate_metrics_with_counts_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        args = batch_args(root)
        args.hadm_ids_file.write_text("123456789\n987654321\n", encoding="utf-8")

        def fake_runner(cmd: list[str]) -> CommandResult:
            if "extract_patient_snapshot_duckdb.py" in cmd[1]:
                output = Path(cmd[cmd.index("--output") + 1])
                write_snapshot(output)
                return CommandResult(returncode=0)
            output = Path(cmd[cmd.index("--output") + 1])
            if "123456789" in output.name:
                write_pipeline_output(output, verified=2, unsupported=1, contradicted=0, halted=False)
            else:
                write_pipeline_output(output, verified=1, unsupported=0, contradicted=1, halted=True)
            return CommandResult(returncode=0)

        run_batch(args, runner=fake_runner)

        aggregate_path = aggregate_metrics_path(args.output_dir)
        aggregate_text = aggregate_path.read_text(encoding="utf-8")
        aggregate = json.loads(aggregate_text)

        assert aggregate["total_cases"] == 2
        assert aggregate["extraction_success_rate"] == 1.0
        assert aggregate["pipeline_success_rate"] == 1.0
        assert aggregate["halt_rate"] == 0.5
        assert aggregate["mean_data_quality_warnings"] == 1.0
        for forbidden in (
            "123456789",
            "987654321",
            "hadm_id",
            "subject_id",
            "snapshot_path",
            "output_path",
            "clinical_note",
            "note_text",
            "labs",
            "diagnoses",
            "medications",
        ):
            assert forbidden not in aggregate_text


def batch_args(root: Path) -> BatchArgs:
    return BatchArgs(
        db_path=root / "mimic.duckdb",
        hadm_ids_file=root / "hadm_ids.txt",
        snapshot_dir=root / "snapshots",
        output_dir=root / "outputs",
        summary_csv=root / "outputs" / "batch_summary.csv",
    )


def write_snapshot(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"data_quality": {"warnings": [{"type": "mock", "severity": "low", "message": "x"}]}}),
        encoding="utf-8",
    )


def write_pipeline_output(
    path: Path,
    verified: int,
    unsupported: int,
    contradicted: int,
    halted: bool = False,
    requires_human_review: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "verification_report": {
                    "summary": {
                        "n_verified": verified,
                        "n_unsupported": unsupported,
                        "n_contradicted": contradicted,
                    }
                },
                "final_output": {
                    "halted": halted,
                    "requires_human_review": requires_human_review,
                },
            }
        ),
        encoding="utf-8",
    )
