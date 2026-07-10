from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.extract_patient_snapshot_duckdb import (
    ExtractArgs,
    REQUIRED_TABLES,
    SNAPSHOT_TOP_LEVEL_KEYS,
    apply_max_labs_cap,
    build_snapshot,
    empty_derived_risk_scores,
    empty_icu_summary,
    optional_table_warnings,
    parse_discharge_note_sections,
    prepare_discharge_note,
    validate_snapshot,
    validate_snapshot_shape,
)


def test_json_top_level_schema_match() -> None:
    snapshot = minimal_snapshot()

    assert list(snapshot.keys()) == SNAPSHOT_TOP_LEVEL_KEYS
    assert validate_snapshot_shape(snapshot, strict=True) is True


def test_missing_icu_stay_sets_null_stay_and_warning() -> None:
    snapshot = minimal_snapshot(icu=empty_icu_summary(), risk=empty_derived_risk_scores("no ICU stay"))
    validation = validate_snapshot(snapshot)

    assert snapshot["ids"]["selected_stay_id"] is None
    assert snapshot["icu_summary"]["has_icu"] is False
    assert "icu_summary" in validation.missing_blocks
    assert warning_types(validation) >= {"no_icu_stay"}


def test_missing_discharge_note_warning() -> None:
    note_sections = parse_discharge_note_sections(None)
    snapshot = minimal_snapshot(note=None, note_sections=note_sections)
    validation = validate_snapshot(snapshot)

    assert snapshot["clinical_note"]["raw_text_available"] is False
    assert snapshot["clinical_note"]["section_parse_quality"] == "missing"
    assert "clinical_note" in validation.missing_blocks
    assert "missing_discharge_note" in warning_types(validation)


def test_missing_derived_score_tables_emit_warnings() -> None:
    warnings = optional_table_warnings(REQUIRED_TABLES)

    assert len(warnings) == 5
    assert {item["type"] for item in warnings} == {"missing_optional_derived_table"}


def test_no_diagnoses_warning() -> None:
    snapshot = minimal_snapshot(diagnoses=[])
    validation = validate_snapshot(snapshot)

    assert "diagnoses" in validation.missing_blocks
    assert "no_diagnosis_codes" in warning_types(validation)


def test_no_labs_warning() -> None:
    snapshot = minimal_snapshot(labs=[])
    validation = validate_snapshot(snapshot)

    assert "labs" in validation.missing_blocks
    assert "no_labs" in warning_types(validation)


def test_note_truncation_and_parse_quality() -> None:
    row = {
        "note_id": "n1",
        "note_type": "discharge",
        "text": (
            "Chief Complaint: Fever\n"
            "History of Present Illness: Progressive cough.\n"
            "Past Medical History: Diabetes.\n"
            "Brief Hospital Course: ICU monitoring.\n"
            "Discharge Diagnosis: Pneumonia.\n"
            "Follow-up: PCP.\n"
        ),
    }
    note = prepare_discharge_note(row, max_note_chars=120)
    parsed = parse_discharge_note_sections(note)

    assert note["text_truncated"] is True
    assert parsed["text_truncated"] is True
    assert parsed["sections"]["chief_complaint"] == "Fever"
    assert parsed["section_parse_quality"] in {"medium", "high"}


def test_max_labs_cap() -> None:
    rows = [{"itemid": itemid} for itemid in range(5)]
    capped, was_capped = apply_max_labs_cap(rows, max_labs=3)

    assert was_capped is True
    assert len(capped) == 3
    assert capped == rows[:3]


def minimal_snapshot(
    diagnoses: list[dict] | None = None,
    labs: list[dict] | None = None,
    note: dict | None | object = ...,
    note_sections: dict | None = None,
    icu: dict | None = None,
    risk: dict | None = None,
) -> dict:
    args = ExtractArgs(
        db_path=Path("/tmp/mock.duckdb"),
        hadm_id=20000001,
        output_path=Path("/tmp/mock.json"),
    )
    context = {
        "subject_id": 10000001,
        "hadm_id": 20000001,
        "gender": "F",
        "age": 68,
        "race": "WHITE",
        "language": "ENGLISH",
        "insurance": "Medicare",
        "marital_status": "MARRIED",
        "admit_time": "2182-01-01 00:00:00",
        "discharge_time": "2182-01-04 00:00:00",
        "admission_type": "EW EMER.",
        "admission_location": "EMERGENCY ROOM",
        "discharge_location": "HOME",
        "hospital_expire_flag": 0,
        "los_days": 3.0,
    }
    default_note = {
        "note_id": "note1",
        "note_type": "discharge",
        "raw_text_available": True,
        "text": (
            "Chief Complaint: Fever\n"
            "History of Present Illness: Cough.\n"
            "Past Medical History: Diabetes.\n"
            "Hospital Course: Improved.\n"
            "Discharge Diagnosis: Pneumonia.\n"
            "Discharge Medications: None listed.\n"
            "Follow-up: PCP.\n"
        ),
        "text_truncated": False,
    }
    if note is ...:
        note = default_note
    if note_sections is None:
        note_sections = parse_discharge_note_sections(note)
    default_icu = {
        "has_icu": True,
        "selected_stay_id": 30000001,
        "selection_rule": "highest_acuity",
        "n_icu_stays": 1,
        "first_careunit": "Medical Intensive Care Unit",
        "last_careunit": "Medical Intensive Care Unit",
        "intime": "2182-01-01 01:00:00",
        "outtime": "2182-01-02 01:00:00",
        "los_days": 1.0,
    }
    default_risk = {
        "has_icu_scores": True,
        "sofa_max": 6,
        "sofa_components": {
            "respiration": 2,
            "coagulation": 1,
            "liver": 0,
            "cardiovascular": 2,
            "cns": 0,
            "renal": 1,
        },
        "sapsii": 35,
        "sapsii_prob": 0.12,
        "apsiii": 48,
        "apsiii_prob": 0.10,
    }
    return build_snapshot(
        args=replace(args),
        context=context,
        icu=icu or default_icu,
        weight={"weight_kg": 82.0},
        diagnoses=diagnoses
        if diagnoses is not None
        else [
            {
                "icd_code": "J189",
                "icd_version": "10",
                "seq_num": 1,
                "description": "Pneumonia, unspecified organism",
            }
        ],
        procedures=[],
        labs=labs
        if labs is not None
        else [
            {
                "itemid": 50912,
                "label": "Creatinine",
                "category": "Chemistry",
                "charttime": "2182-01-01 02:00:00",
                "valuenum": 2.0,
                "value": "2.0",
                "valueuom": "mg/dL",
                "ref_range_lower": 0.6,
                "ref_range_upper": 1.2,
                "flag": "abnormal",
                "selection_reason": "renal_safety",
            }
        ],
        medications=[
            {
                "drug": "Vancomycin",
                "dose_val_rx": "1000",
                "dose_unit_rx": "mg",
                "route": "IV",
                "frequency": "Q24H",
                "starttime": "2182-01-01 03:00:00",
                "stoptime": "2182-01-02 03:00:00",
                "pharmacy_id": "1",
            }
        ],
        note=note,
        note_sections=note_sections,
        risk_scores=risk or default_risk,
    )


def warning_types(validation) -> set[str]:
    return {warning["type"] for warning in validation.warnings}
