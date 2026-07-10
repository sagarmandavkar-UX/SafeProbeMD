"""Consensus assembly for SafeProbeMD verified outputs."""

from __future__ import annotations

from typing import Any

from safeprobemd.agents.base import BaseAgent


HALT_SUMMARY = "Insufficient or unsafe to auto-summarize \u2014 route to human review."


class ConsensusAgent(BaseAgent):
    """Create the final patient snapshot summary from verified claims only."""

    name = "consensus"
    required_fields = ("ids", "stage1_outputs", "verification_report", "safety_report", "intake_context_header")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        stage1_outputs = input_packet.get("stage1_outputs") or {}
        verification = input_packet.get("verification_report") or {}
        safety = input_packet.get("safety_report") or {}
        intake_header = input_packet.get("intake_context_header") or {}

        if safety.get("halt_recommended") or verification.get("blocking_issues"):
            return {
                "confidence": 0.0,
                "final_output": {
                    "halted": True,
                    "final_summary": HALT_SUMMARY,
                    "problem_list": [],
                    "key_labs": [],
                    "medication_safety_alerts": [],
                    "acuity_summary": "",
                    "unsupported_claims_excluded": _excluded_claims(verification),
                    "overall_confidence": 0.0,
                    "requires_human_review": True,
                },
                "flags": ["halted"],
            }

        verified_ids = set(verification.get("verified_claim_ids") or _verified_from_rows(verification))
        diagnostic = stage1_outputs.get("diagnostic") or {}
        lab = stage1_outputs.get("lab") or {}
        medication = stage1_outputs.get("medication") or {}
        risk = stage1_outputs.get("risk") or {}

        problems = [
            item for item in diagnostic.get("problems", []) or [] if item.get("claim_id") in verified_ids
        ][:10]
        key_labs = [
            item for item in lab.get("lab_findings", []) or [] if item.get("claim_id") in verified_ids
        ][:12]
        med_alerts = [
            item for item in medication.get("med_alerts", []) or [] if item.get("claim_id") in verified_ids
        ][:12]

        acuity_summary = ""
        risk_claim_ids = {claim.get("claim_id") for claim in risk.get("claims", []) or []}
        if verified_ids.intersection(risk_claim_ids) and risk.get("acuity_tier"):
            acuity_summary = (
                f"Acuity tier: {risk.get('acuity_tier')}; "
                f"mortality risk band: {risk.get('mortality_risk_band')}."
            )

        final_summary = _build_summary(
            intake_header.get("context_summary", ""),
            problems,
            key_labs,
            med_alerts,
            acuity_summary,
        )
        excluded = _excluded_claims(verification)
        overall_confidence = _overall_confidence(stage1_outputs, verification, safety)

        return {
            "confidence": overall_confidence,
            "final_output": {
                "halted": False,
                "final_summary": final_summary,
                "problem_list": problems,
                "key_labs": key_labs,
                "medication_safety_alerts": med_alerts,
                "acuity_summary": acuity_summary,
                "unsupported_claims_excluded": excluded,
                "overall_confidence": overall_confidence,
                "requires_human_review": True,
            },
            "provenance": sorted(verified_ids),
            "flags": ["human_review_required"],
            "data_gaps": _stage_data_gaps(stage1_outputs),
        }


def _verified_from_rows(verification: dict[str, Any]) -> list[str]:
    return [
        row.get("claim_id")
        for row in verification.get("verification_report", []) or []
        if row.get("status") == "verified"
    ]


def _excluded_claims(verification: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = []
    for row in verification.get("verification_report", []) or []:
        if row.get("status") != "verified":
            excluded.append(
                {
                    "claim_id": row.get("claim_id"),
                    "agent": row.get("agent"),
                    "status": row.get("status"),
                    "note": row.get("note"),
                }
            )
    return excluded


def _build_summary(
    context_summary: str,
    problems: list[dict[str, Any]],
    labs: list[dict[str, Any]],
    med_alerts: list[dict[str, Any]],
    acuity_summary: str,
) -> str:
    parts = []
    if context_summary:
        parts.append(context_summary)
    if problems:
        parts.append("Verified problems include " + ", ".join(item["problem"] for item in problems[:5]) + ".")
    if labs:
        parts.append("Key verified lab abnormalities include " + ", ".join(item["label"] for item in labs[:5]) + ".")
    if med_alerts:
        parts.append(
            "Verified medication safety alerts include "
            + ", ".join(alert["rationale"] for alert in med_alerts[:3])
        )
    if acuity_summary:
        parts.append(acuity_summary)
    if not parts:
        return "No verified clinical claims available; route to human review."
    return " ".join(parts)


def _overall_confidence(
    stage1_outputs: dict[str, dict[str, Any]],
    verification: dict[str, Any],
    safety: dict[str, Any],
) -> float:
    confidences = [float(output.get("confidence") or 0.0) for output in stage1_outputs.values()]
    verification_conf = float(verification.get("confidence") or 0.0)
    if verification_conf:
        confidences.append(verification_conf)
    if safety.get("escalate"):
        penalty = 0.15
    else:
        penalty = 0.0
    if not confidences:
        return 0.0
    return max(0.0, min(1.0, round((sum(confidences) / len(confidences)) - penalty, 3)))


def _stage_data_gaps(stage1_outputs: dict[str, dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    for output in stage1_outputs.values():
        for gap in output.get("data_gaps", []) or []:
            gaps.append(str(gap))
    return sorted(set(gaps))
