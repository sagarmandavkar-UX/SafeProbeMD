"""SafeProbeMD deterministic multi-agent orchestration."""

from __future__ import annotations

from typing import Any

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
from scripts.build_agent_inputs import (
    build_consensus_input,
    build_diagnostic_input,
    build_intake_input,
    build_lab_input,
    build_medication_input,
    build_risk_input,
    build_safety_input,
    build_verification_input,
)


PIPELINE_VERSION = "0.1"


class SafeProbeMDOrchestrator:
    """Run the SafeProbeMD admission-level prototype on one patient snapshot."""

    def __init__(self) -> None:
        self.intake_agent = IntakeContextAgent()
        self.diagnostic_agent = DiagnosticReasoningAgent()
        self.lab_agent = LabInterpretationAgent()
        self.medication_agent = MedicationSafetyAgent()
        self.risk_agent = RiskAcuityAgent()
        self.verification_agent = VerificationAgent()
        self.safety_agent = SafetyCriticAgent()
        self.consensus_agent = ConsensusAgent()

    def run(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        ids = snapshot.get("ids", {})

        intake_output = self.intake_agent.run(build_intake_input(snapshot))

        stage1_inputs = {
            "diagnostic": build_diagnostic_input(snapshot, intake_output),
            "lab": build_lab_input(snapshot),
            "medication": build_medication_input(snapshot),
            "risk": build_risk_input(snapshot, intake_output),
        }
        stage1_outputs = {
            "diagnostic": self.diagnostic_agent.run(stage1_inputs["diagnostic"]),
            "lab": self.lab_agent.run(stage1_inputs["lab"]),
            "medication": self.medication_agent.run(stage1_inputs["medication"]),
            "risk": self.risk_agent.run(stage1_inputs["risk"]),
        }

        verification_input = build_verification_input(snapshot, stage1_outputs)
        verification_output = self.verification_agent.run(verification_input)

        safety_input = build_safety_input(intake_output, stage1_outputs, verification_output)
        safety_output = self.safety_agent.run(safety_input)

        consensus_input = build_consensus_input(
            intake_output,
            stage1_outputs,
            verification_output,
            safety_output,
        )
        consensus_output = self.consensus_agent.run(consensus_input)

        return {
            "case_id": str(snapshot.get("case_id") or ids.get("admission_id") or ""),
            "patient_id": str(ids.get("patient_id") or ""),
            "admission_id": str(ids.get("admission_id") or ""),
            "pipeline_version": PIPELINE_VERSION,
            "agent_outputs": {
                "intake": intake_output,
                **stage1_outputs,
                "consensus": consensus_output,
            },
            "verification_report": verification_output,
            "safety_report": safety_output,
            "final_output": consensus_output.get("final_output", {}),
        }


def run_safeprobemd_case(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Convenience wrapper for scripts and tests."""

    return SafeProbeMDOrchestrator().run(snapshot)
