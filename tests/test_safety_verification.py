from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

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
from safeprobemd.agents.base import make_claim
from safeprobemd.agents.consensus_agent import HALT_SUMMARY
from safeprobemd.orchestrator import run_safeprobemd_case
from scripts.build_agent_inputs import (
    build_consensus_input,
    build_intake_input,
    build_stage1_inputs,
    build_safety_input,
    build_verification_input,
)
from test_agent_routing import sample_snapshot


FINAL_OUTPUT_KEYS = {
    "final_summary",
    "problem_list",
    "key_labs",
    "medication_safety_alerts",
    "acuity_summary",
    "unsupported_claims_excluded",
    "overall_confidence",
    "requires_human_review",
}


def test_unsupported_claim_is_excluded_from_consensus() -> None:
    snapshot = sample_snapshot()
    intake_output, stage1_outputs = _stage1_outputs(snapshot)
    unsupported_claim_id = "unsupported_missing_lab_path"
    stage1_outputs["lab"]["claims"].append(
        make_claim(
            unsupported_claim_id,
            "Unsupported lab claim cites a nonexistent lab row.",
            ["labs.99.valuenum"],
            claim_type="lab",
            evidence_values={"labs.99.valuenum": 123.4},
        )
    )
    stage1_outputs["lab"].setdefault("lab_findings", []).append(
        {
            "claim_id": unsupported_claim_id,
            "label": "Imaginary lab",
            "value": 123.4,
            "uom": "mg/dL",
            "status": "critical",
            "interpretation": "Unsupported lab claim cites a nonexistent lab row.",
            "charttime": "2180-01-01 10:00:00",
        }
    )

    verification, safety, consensus = _review_outputs(snapshot, intake_output, stage1_outputs)
    final = consensus["final_output"]

    assert unsupported_claim_id in verification["unsupported_claim_ids"]
    assert not safety["halt_recommended"]
    assert all(item["claim_id"] != unsupported_claim_id for item in final["key_labs"])
    assert any(
        item["claim_id"] == unsupported_claim_id and item["status"] == "unsupported"
        for item in final["unsupported_claims_excluded"]
    )


def test_contradicted_evidence_value_triggers_safety_and_halt() -> None:
    snapshot = sample_snapshot()
    intake_output, stage1_outputs = _stage1_outputs(snapshot)
    contradicted_claim_id = "contradicted_creatinine_value"
    stage1_outputs["lab"]["claims"].append(
        make_claim(
            contradicted_claim_id,
            "Creatinine is critical at 9.9 mg/dL.",
            ["labs.0.label", "labs.0.valuenum", "labs.0.valueuom"],
            claim_type="lab",
            severity="high",
            evidence_values={
                "labs.0.label": "Creatinine",
                "labs.0.valuenum": 9.9,
                "labs.0.valueuom": "mg/dL",
            },
        )
    )
    stage1_outputs["lab"].setdefault("lab_findings", []).append(
        {
            "claim_id": contradicted_claim_id,
            "label": "Creatinine",
            "value": 9.9,
            "uom": "mg/dL",
            "status": "critical",
            "interpretation": "Creatinine is critical at 9.9 mg/dL.",
            "charttime": "2180-01-01 10:00:00",
        }
    )

    verification, safety, consensus = _review_outputs(snapshot, intake_output, stage1_outputs)
    final = consensus["final_output"]

    assert contradicted_claim_id in verification["contradicted_claim_ids"]
    assert verification["blocking_issues"]
    assert "contradicted_grounding" in safety["harm_flags"]
    assert safety["halt_recommended"]
    assert final["halted"] is True
    assert all(item["claim_id"] != contradicted_claim_id for item in final["key_labs"])


def test_forced_halt_output_uses_safe_message_and_leaks_no_content() -> None:
    snapshot = sample_snapshot()
    intake_output, stage1_outputs = _stage1_outputs(snapshot)
    verification = VerificationAgent().run(build_verification_input(snapshot, stage1_outputs))
    forced_safety = SafetyCriticAgent().output_envelope(snapshot["ids"])
    forced_safety.update(
        {
            "critiques": [
                {
                    "target_agent": "verification",
                    "issue_type": "forced_blocking_issue",
                    "severity": "high",
                    "recommendation": "Halt automatic summarization.",
                }
            ],
            "harm_flags": ["forced_halt"],
            "escalate": True,
            "halt_recommended": True,
        }
    )

    consensus = ConsensusAgent().run(
        build_consensus_input(intake_output, stage1_outputs, verification, forced_safety)
    )
    final = consensus["final_output"]
    rendered = json.dumps(final, ensure_ascii=False)

    assert final["halted"] is True
    assert final["final_summary"] == HALT_SUMMARY
    assert final["final_summary"] == "Insufficient or unsafe to auto-summarize \u2014 route to human review."
    assert final["problem_list"] == []
    assert final["key_labs"] == []
    assert final["medication_safety_alerts"] == []
    assert final["acuity_summary"] == ""
    for leaked_term in ("Pneumonia", "Creatinine", "Vancomycin", "SOFA"):
        assert leaked_term not in rendered


def test_negative_routing_leakage_boundaries() -> None:
    snapshot = sample_snapshot()
    intake_output, stage1_outputs = _stage1_outputs(snapshot)
    stage1_inputs = build_stage1_inputs(snapshot, intake_output)
    verification = VerificationAgent().run(build_verification_input(snapshot, stage1_outputs))
    safety = SafetyCriticAgent().run(build_safety_input(intake_output, stage1_outputs, verification))
    consensus_input = build_consensus_input(intake_output, stage1_outputs, verification, safety)

    assert not {"clinical_note", "diagnoses", "medications", "derived_risk_scores"}.intersection(
        stage1_inputs["lab"]
    )
    assert not {"clinical_note", "labs", "derived_risk_scores"}.intersection(stage1_inputs["medication"])
    assert not {"clinical_note", "medications", "diagnoses"}.intersection(stage1_inputs["risk"])
    assert not {
        "source",
        "demographics",
        "admission",
        "diagnoses",
        "procedures",
        "labs",
        "medications",
        "icu_summary",
        "clinical_note",
        "derived_risk_scores",
        "data_quality",
    }.intersection(consensus_input)


def test_null_icu_snapshot_abstains_risk_without_crashing() -> None:
    snapshot = _no_icu_snapshot()
    result = run_safeprobemd_case(snapshot)
    risk_output = result["agent_outputs"]["risk"]
    consensus_output = result["agent_outputs"]["consensus"]
    final = result["final_output"]

    assert risk_output["abstained"] is True
    assert risk_output["abstain_reason"] == "no ICU-derived severity available"
    assert "missing_icu_derived_scores" in risk_output["data_gaps"]
    assert "missing_icu_derived_scores" in consensus_output["data_gaps"]
    assert FINAL_OUTPUT_KEYS.issubset(final)
    assert isinstance(final["final_summary"], str)


def _stage1_outputs(snapshot: dict) -> tuple[dict, dict]:
    intake_output = IntakeContextAgent().run(build_intake_input(snapshot))
    stage1_inputs = build_stage1_inputs(snapshot, intake_output)
    stage1_outputs = {
        "diagnostic": DiagnosticReasoningAgent().run(stage1_inputs["diagnostic"]),
        "lab": LabInterpretationAgent().run(stage1_inputs["lab"]),
        "medication": MedicationSafetyAgent().run(stage1_inputs["medication"]),
        "risk": RiskAcuityAgent().run(stage1_inputs["risk"]),
    }
    return intake_output, stage1_outputs


def _review_outputs(
    snapshot: dict,
    intake_output: dict,
    stage1_outputs: dict,
) -> tuple[dict, dict, dict]:
    verification = VerificationAgent().run(build_verification_input(snapshot, stage1_outputs))
    safety = SafetyCriticAgent().run(build_safety_input(intake_output, stage1_outputs, verification))
    consensus = ConsensusAgent().run(
        build_consensus_input(intake_output, stage1_outputs, verification, safety)
    )
    return verification, safety, consensus


def _no_icu_snapshot() -> dict:
    snapshot = deepcopy(sample_snapshot())
    snapshot["ids"]["selected_stay_id"] = None
    snapshot["icu_summary"] = {
        "has_icu": False,
        "selected_stay_id": None,
        "selection_rule": "no_icu_stay",
        "n_icu_stays": 0,
        "first_careunit": "",
        "last_careunit": "",
        "intime": "",
        "outtime": "",
        "los_days": None,
    }
    snapshot["derived_risk_scores"] = {
        "has_icu_scores": False,
        "reason": "no ICU stay for this admission",
        "sofa_max": None,
        "sofa_components": {
            "respiration": None,
            "coagulation": None,
            "liver": None,
            "cardiovascular": None,
            "cns": None,
            "renal": None,
        },
        "sapsii": None,
        "sapsii_prob": None,
        "apsiii": None,
        "apsiii_prob": None,
    }
    return snapshot
