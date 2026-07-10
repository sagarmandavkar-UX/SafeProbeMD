from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from safeprobemd.agents import (
    ConsensusAgent,
    DiagnosticReasoningAgent,
    IntakeContextAgent,
    LabInterpretationAgent,
    MedicationSafetyAgent,
    RiskAcuityAgent,
    SafetyCriticAgent,
    VerificationAgent,
)
from safeprobemd.orchestrator import run_safeprobemd_case
from scripts.build_agent_inputs import (
    build_consensus_input,
    build_intake_input,
    build_lab_input,
    build_medication_input,
    build_risk_input,
    build_safety_input,
    build_stage1_inputs,
    build_verification_input,
)


REQUIRED_ENVELOPE_KEYS = {
    "agent",
    "schema_version",
    "patient_id",
    "admission_id",
    "confidence",
    "abstained",
    "abstain_reason",
    "provenance",
    "flags",
    "data_gaps",
    "claims",
}


def test_lab_input_excludes_note_diagnoses_and_medications() -> None:
    packet = build_lab_input(sample_snapshot())

    assert "clinical_note" not in packet
    assert "diagnoses" not in packet
    assert "medications" not in packet
    assert "labs" in packet


def test_medication_input_excludes_full_note_full_labs_and_scores() -> None:
    packet = build_medication_input(sample_snapshot())

    assert "clinical_note" not in packet
    assert "labs" not in packet
    assert "derived_risk_scores" not in packet
    assert "renal_hepatic_labs" in packet
    assert "diagnosis_subset" in packet


def test_risk_input_excludes_medications_note_and_diagnoses() -> None:
    intake_output = IntakeContextAgent().run(build_intake_input(sample_snapshot()))
    packet = build_risk_input(sample_snapshot(), intake_output)

    assert "medications" not in packet
    assert "clinical_note" not in packet
    assert "diagnoses" not in packet
    assert "derived_risk_scores" in packet


def test_consensus_input_does_not_receive_raw_snapshot() -> None:
    snapshot = sample_snapshot()
    intake_output = IntakeContextAgent().run(build_intake_input(snapshot))
    stage1_outputs = _stage1_outputs(snapshot, intake_output)
    verification = VerificationAgent().run(build_verification_input(snapshot, stage1_outputs))
    safety = SafetyCriticAgent().run(build_safety_input(intake_output, stage1_outputs, verification))

    packet = build_consensus_input(intake_output, stage1_outputs, verification, safety)

    assert "labs" not in packet
    assert "clinical_note" not in packet
    assert "medications" not in packet
    assert "stage1_outputs" in packet


def test_all_agents_return_shared_envelope() -> None:
    snapshot = sample_snapshot()
    intake_input = build_intake_input(snapshot)
    intake = IntakeContextAgent().run(intake_input)
    stage1_inputs = build_stage1_inputs(snapshot, intake)
    stage1_outputs = {
        "diagnostic": DiagnosticReasoningAgent().run(stage1_inputs["diagnostic"]),
        "lab": LabInterpretationAgent().run(stage1_inputs["lab"]),
        "medication": MedicationSafetyAgent().run(stage1_inputs["medication"]),
        "risk": RiskAcuityAgent().run(stage1_inputs["risk"]),
    }
    verification = VerificationAgent().run(build_verification_input(snapshot, stage1_outputs))
    safety = SafetyCriticAgent().run(build_safety_input(intake, stage1_outputs, verification))
    consensus = ConsensusAgent().run(build_consensus_input(intake, stage1_outputs, verification, safety))

    for output in [intake, *stage1_outputs.values(), verification, safety, consensus]:
        assert REQUIRED_ENVELOPE_KEYS.issubset(output.keys())
        assert output["patient_id"] == "10000032"
        assert output["admission_id"] == "20000001"


def test_final_output_uses_only_verified_claims() -> None:
    result = run_safeprobemd_case(sample_snapshot())
    verified = set(result["verification_report"]["verified_claim_ids"])
    final = result["final_output"]

    final_claim_ids = []
    final_claim_ids.extend(item["claim_id"] for item in final["problem_list"])
    final_claim_ids.extend(item["claim_id"] for item in final["key_labs"])
    final_claim_ids.extend(item["claim_id"] for item in final["medication_safety_alerts"])

    assert final_claim_ids
    assert set(final_claim_ids).issubset(verified)
    assert "unsupported claim" not in final["final_summary"].lower()


def _stage1_outputs(snapshot: dict, intake_output: dict) -> dict:
    stage1_inputs = build_stage1_inputs(snapshot, intake_output)
    return {
        "diagnostic": DiagnosticReasoningAgent().run(stage1_inputs["diagnostic"]),
        "lab": LabInterpretationAgent().run(stage1_inputs["lab"]),
        "medication": MedicationSafetyAgent().run(stage1_inputs["medication"]),
        "risk": RiskAcuityAgent().run(stage1_inputs["risk"]),
    }


def sample_snapshot() -> dict:
    return {
        "schema_version": "0.1",
        "case_id": "case_20000001",
        "ids": {
            "patient_id": "10000032",
            "admission_id": "20000001",
            "selected_stay_id": "30000001",
        },
        "demographics": {
            "age": 72,
            "gender": "F",
            "race": "WHITE",
            "insurance": "Medicare",
            "language": "ENGLISH",
            "marital_status": "MARRIED",
            "weight_kg": None,
        },
        "admission": {
            "admit_time": "2180-01-01 08:00:00",
            "discharge_time": "2180-01-04 08:00:00",
            "admission_type": "EW EMER.",
            "admission_location": "EMERGENCY ROOM",
            "discharge_location": "HOME",
            "hospital_expire_flag": 0,
            "los_days": 3.0,
        },
        "diagnoses": [
            {
                "icd_code": "J189",
                "icd_version": "10",
                "seq_num": 1,
                "description": "Pneumonia, unspecified organism",
            },
            {
                "icd_code": "N179",
                "icd_version": "10",
                "seq_num": 2,
                "description": "Acute kidney failure, unspecified",
            },
        ],
        "procedures": [],
        "labs": [
            {
                "itemid": "50912",
                "label": "Creatinine",
                "category": "Chemistry",
                "charttime": "2180-01-01 10:00:00",
                "valuenum": 2.2,
                "value": "2.2",
                "valueuom": "mg/dL",
                "ref_range_lower": 0.6,
                "ref_range_upper": 1.2,
                "flag": "abnormal",
                "selection_reason": "renal_safety",
            },
            {
                "itemid": "51301",
                "label": "WBC",
                "category": "Hematology",
                "charttime": "2180-01-01 10:10:00",
                "valuenum": 14.0,
                "value": "14",
                "valueuom": "K/uL",
                "ref_range_lower": 4.0,
                "ref_range_upper": 11.0,
                "flag": "abnormal",
                "selection_reason": "first_24h",
            },
        ],
        "medications": [
            {
                "drug": "Vancomycin",
                "dose_val_rx": "",
                "dose_unit_rx": "",
                "route": "IV",
                "frequency": "",
                "starttime": "2180-01-01 12:00:00",
                "stoptime": "2180-01-02 12:00:00",
                "pharmacy_id": "pharm1",
            }
        ],
        "icu_summary": {
            "has_icu": True,
            "selected_stay_id": "30000001",
            "selection_rule": "highest_acuity",
            "n_icu_stays": 1,
            "first_careunit": "Medical Intensive Care Unit",
            "last_careunit": "Medical Intensive Care Unit",
            "icu_los_hours": 36.0,
        },
        "clinical_note": {
            "note_id": "note1",
            "note_type": "discharge",
            "sections": {
                "chief_complaint": "Fever and cough.",
                "discharge_diagnosis": "Pneumonia.",
                "brief_hospital_course": "Admitted for monitored care.",
            },
            "section_parse_quality": "medium",
            "text_truncated": False,
        },
        "derived_risk_scores": {
            "has_icu_scores": True,
            "sofa_max": 8,
            "sofa_components": {
                "respiration": 2,
                "coagulation": 1,
                "liver": 0,
                "cardiovascular": 3,
                "cns": 1,
                "renal": 2,
            },
            "sapsii": 40,
            "sapsii_prob": 0.2,
            "apsiii": 50,
            "apsiii_prob": 0.15,
        },
        "data_quality": {
            "warnings": [],
            "note_parse_quality": "medium",
        },
    }
