"""Safety critic for vetted SafeProbeMD agent outputs."""

from __future__ import annotations

from typing import Any

from safeprobemd.agents.base import BaseAgent


class SafetyCriticAgent(BaseAgent):
    """Adversarially review agent outputs without reading the raw snapshot."""

    name = "safety_critic"
    required_fields = ("ids", "agent_outputs", "verification_report", "minimal_patient_frame")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        agent_outputs = input_packet.get("agent_outputs") or {}
        verification = input_packet.get("verification_report") or {}
        minimal_frame = input_packet.get("minimal_patient_frame") or {}
        verification_rows = verification.get("verification_report") or []
        blocking_issues = verification.get("blocking_issues") or []

        critiques: list[dict[str, Any]] = []
        harm_flags: list[str] = []

        for row in verification_rows:
            status = row.get("status")
            if status == "unsupported":
                critiques.append(
                    {
                        "target_agent": row.get("agent", "unknown"),
                        "issue_type": "unsupported_claim",
                        "severity": "moderate",
                        "recommendation": "Exclude unsupported claim from consensus output.",
                        "claim_id": row.get("claim_id"),
                    }
                )
            elif status == "contradicted":
                critiques.append(
                    {
                        "target_agent": row.get("agent", "unknown"),
                        "issue_type": "contradicted_claim",
                        "severity": "high",
                        "recommendation": "Halt automatic summarization and route to human review.",
                        "claim_id": row.get("claim_id"),
                    }
                )
                harm_flags.append("contradicted_grounding")

        for output in agent_outputs.values():
            agent = output.get("agent", "unknown")
            if output.get("abstained"):
                critiques.append(
                    {
                        "target_agent": agent,
                        "issue_type": "agent_abstained",
                        "severity": "low",
                        "recommendation": "Surface data gap in final output.",
                    }
                )
            for gap in output.get("data_gaps", []) or []:
                if gap in {"missing_weight_kg", "missing_icu_derived_scores"} or str(gap).startswith("missing_dose"):
                    critiques.append(
                        {
                            "target_agent": agent,
                            "issue_type": "data_gap",
                            "severity": "moderate",
                            "recommendation": "Require human review before using affected safety claims.",
                            "data_gap": gap,
                        }
                    )
                    harm_flags.append(str(gap))

        medication_output = agent_outputs.get("medication") or {}
        for alert in medication_output.get("med_alerts", []) or []:
            if alert.get("severity") in {"moderate", "high"}:
                critiques.append(
                    {
                        "target_agent": medication_output.get("agent", "medication_safety"),
                        "issue_type": "medication_safety_alert",
                        "severity": alert.get("severity"),
                        "recommendation": "Carry verified medication safety alert into human-review summary.",
                        "claim_id": alert.get("claim_id"),
                    }
                )

        acuity_flags = minimal_frame.get("acuity_flags") or {}
        risk_output = agent_outputs.get("risk") or {}
        if acuity_flags.get("is_icu") and risk_output.get("abstained"):
            critiques.append(
                {
                    "target_agent": risk_output.get("agent", "risk_acuity"),
                    "issue_type": "missing_risk_for_icu_case",
                    "severity": "moderate",
                    "recommendation": "Route to human review because ICU context lacks derived risk scoring.",
                }
            )
            harm_flags.append("icu_without_risk_score")

        halt_recommended = bool(blocking_issues) or any(item.get("severity") == "high" for item in critiques)
        escalate = halt_recommended or any(item.get("severity") == "moderate" for item in critiques)

        return {
            "confidence": 0.82,
            "critiques": critiques,
            "harm_flags": sorted(set(harm_flags)),
            "escalate": escalate,
            "halt_recommended": halt_recommended,
            "flags": ["halt_recommended"] if halt_recommended else [],
        }
