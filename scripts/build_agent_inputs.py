"""Build SafeProbeMD agent-specific input packets from one patient snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from safeprobemd.agents.base import get_path, nonempty, safe_float


RENAL_LABEL_TERMS = ("creatinine", "bun", "urea", "potassium")
HEPATIC_LABEL_TERMS = ("bilirubin", "ast", "alt", "alkaline", "albumin", "inr", "pt", "ptt")


def load_snapshot(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)
        fp.write("\n")


def build_intake_input(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "ids": snapshot.get("ids", {}),
        "demographics": snapshot.get("demographics", {}),
        "admission": snapshot.get("admission", {}),
        "icu_summary": snapshot.get("icu_summary", {}),
    }


def build_context_header(intake_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_summary": intake_output.get("context_summary", ""),
        "normalized_demographics": intake_output.get("normalized_demographics", {}),
        "acuity_flags": intake_output.get("acuity_flags", {}),
    }


def build_diagnostic_input(
    snapshot: dict[str, Any], intake_output: dict[str, Any]
) -> dict[str, Any]:
    return {
        "ids": snapshot.get("ids", {}),
        "context_header": build_context_header(intake_output),
        "clinical_note": snapshot.get("clinical_note", {}),
        "diagnoses": snapshot.get("diagnoses", []),
        "procedures": snapshot.get("procedures", []),
    }


def build_lab_input(snapshot: dict[str, Any]) -> dict[str, Any]:
    demographics = snapshot.get("demographics", {})
    return {
        "ids": snapshot.get("ids", {}),
        "age": demographics.get("age"),
        "gender": demographics.get("gender", ""),
        "labs": snapshot.get("labs", []),
    }


def build_medication_input(snapshot: dict[str, Any]) -> dict[str, Any]:
    demographics = snapshot.get("demographics", {})
    medications = []
    for idx, med in enumerate(snapshot.get("medications", [])):
        item = dict(med)
        item["_snapshot_index"] = idx
        medications.append(item)

    return {
        "ids": snapshot.get("ids", {}),
        "age": demographics.get("age"),
        "weight_kg": demographics.get("weight_kg"),
        "medications": medications,
        "renal_hepatic_labs": _select_renal_hepatic_labs(snapshot.get("labs", [])),
        "diagnosis_subset": _select_diagnosis_subset(snapshot.get("diagnoses", [])),
    }


def build_risk_input(snapshot: dict[str, Any], intake_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "ids": snapshot.get("ids", {}),
        "icu_summary": snapshot.get("icu_summary", {}),
        "derived_risk_scores": snapshot.get("derived_risk_scores", {}),
        "abnormal_labs": _select_abnormal_labs(snapshot.get("labs", [])),
        "intake_acuity_flags": intake_output.get("acuity_flags", {}),
    }


def build_stage1_inputs(
    snapshot: dict[str, Any], intake_output: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return {
        "diagnostic": build_diagnostic_input(snapshot, intake_output),
        "lab": build_lab_input(snapshot),
        "medication": build_medication_input(snapshot),
        "risk": build_risk_input(snapshot, intake_output),
    }


def build_verification_input(
    snapshot: dict[str, Any], stage1_outputs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    cited_paths = sorted(_collect_provenance_paths(stage1_outputs))
    grounding_fields = {}
    for path in cited_paths:
        exists, value = get_path(snapshot, path)
        grounding_fields[path] = {"exists": exists, "value": value}

    return {
        "ids": snapshot.get("ids", {}),
        "agent_outputs": stage1_outputs,
        "grounding_fields": grounding_fields,
    }


def build_safety_input(
    intake_output: dict[str, Any],
    stage1_outputs: dict[str, dict[str, Any]],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    ids = {
        "patient_id": intake_output.get("patient_id", ""),
        "admission_id": intake_output.get("admission_id", ""),
    }
    return {
        "ids": ids,
        "agent_outputs": stage1_outputs,
        "verification_report": verification_report,
        "minimal_patient_frame": {
            "normalized_demographics": intake_output.get("normalized_demographics", {}),
            "acuity_flags": intake_output.get("acuity_flags", {}),
        },
    }


def build_consensus_input(
    intake_output: dict[str, Any],
    stage1_outputs: dict[str, dict[str, Any]],
    verification_report: dict[str, Any],
    safety_report: dict[str, Any],
) -> dict[str, Any]:
    ids = {
        "patient_id": intake_output.get("patient_id", ""),
        "admission_id": intake_output.get("admission_id", ""),
    }
    return {
        "ids": ids,
        "stage1_outputs": stage1_outputs,
        "verification_report": verification_report,
        "safety_report": safety_report,
        "intake_context_header": build_context_header(intake_output),
    }


def build_agent_inputs(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the initial routable packets available before agents run."""

    return {
        "intake": build_intake_input(snapshot),
        "lab": build_lab_input(snapshot),
        "medication": build_medication_input(snapshot),
    }


def _select_renal_hepatic_labs(labs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for idx, lab in enumerate(labs):
        label = str(lab.get("label", "")).lower()
        if any(term in label for term in RENAL_LABEL_TERMS + HEPATIC_LABEL_TERMS):
            item = dict(lab)
            item["_snapshot_index"] = idx
            selected.append(item)
    return selected


def _select_abnormal_labs(labs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for idx, lab in enumerate(labs):
        if _is_abnormal_lab(lab):
            item = dict(lab)
            item["_snapshot_index"] = idx
            selected.append(item)
    return selected


def _is_abnormal_lab(lab: dict[str, Any]) -> bool:
    flag = str(lab.get("flag", "")).strip().lower()
    if flag and flag not in {"normal", "none"}:
        return True

    value = safe_float(lab.get("valuenum"))
    low = safe_float(lab.get("ref_range_lower"))
    high = safe_float(lab.get("ref_range_upper"))
    if value is None:
        return False
    if low is not None and value < low:
        return True
    if high is not None and value > high:
        return True
    return False


def _select_diagnosis_subset(diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subset = []
    safety_terms = (
        "kidney",
        "renal",
        "liver",
        "hepatic",
        "heart failure",
        "bleed",
        "allergy",
        "diabetes",
    )
    for idx, diagnosis in enumerate(diagnoses):
        description = str(diagnosis.get("description", "")).lower()
        if any(term in description for term in safety_terms):
            item = dict(diagnosis)
            item["_snapshot_index"] = idx
            subset.append(item)
    return subset


def _collect_provenance_paths(outputs: dict[str, dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for output in outputs.values():
        for claim in output.get("claims", []):
            for path in claim.get("provenance", []):
                if nonempty(path):
                    paths.add(str(path))
    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build initial SafeProbeMD routed inputs.")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot = load_snapshot(args.snapshot)
    routed = build_agent_inputs(snapshot)
    write_json(routed, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
