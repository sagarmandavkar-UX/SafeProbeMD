"""Grounding verifier for SafeProbeMD agent claims."""

from __future__ import annotations

from typing import Any

from safeprobemd.agents.base import BaseAgent, nonempty, safe_float


class VerificationAgent(BaseAgent):
    """Check each claim against only its cited grounding fields."""

    name = "verification"
    required_fields = ("ids", "agent_outputs", "grounding_fields")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        agent_outputs = input_packet.get("agent_outputs") or {}
        grounding_fields = input_packet.get("grounding_fields") or {}
        report: list[dict[str, Any]] = []
        blocking_issues: list[str] = []

        for output in agent_outputs.values():
            agent_name = output.get("agent", "unknown")
            for claim in output.get("claims", []) or []:
                claim_id = str(claim.get("claim_id") or "")
                provenance = [str(path) for path in claim.get("provenance", []) if nonempty(path)]
                status = "verified"
                notes: list[str] = []

                if not claim_id:
                    claim_id = f"{agent_name}:missing_claim_id:{len(report)}"
                    status = "unsupported"
                    notes.append("claim has no claim_id")

                if not provenance:
                    status = "unsupported"
                    notes.append("claim has no provenance")

                evidence_values = claim.get("evidence_values") or {}
                for path in provenance:
                    grounded = grounding_fields.get(path)
                    if not grounded or not grounded.get("exists"):
                        status = "unsupported"
                        notes.append(f"missing grounding field: {path}")
                        continue

                    if path in evidence_values and not _values_match(evidence_values[path], grounded.get("value")):
                        status = "contradicted"
                        issue = f"fabricated_or_mismatched_value:{claim_id}:{path}"
                        notes.append(issue)
                        blocking_issues.append(issue)

                if claim.get("impossible"):
                    status = "contradicted"
                    issue = f"impossible_claim:{claim_id}"
                    notes.append(issue)
                    blocking_issues.append(issue)

                report.append(
                    {
                        "claim_id": claim_id,
                        "agent": agent_name,
                        "status": status,
                        "note": "; ".join(notes) if notes else "all cited fields present and matched",
                        "paths_checked": provenance,
                    }
                )

        summary = {
            "n_verified": sum(1 for item in report if item["status"] == "verified"),
            "n_unsupported": sum(1 for item in report if item["status"] == "unsupported"),
            "n_contradicted": sum(1 for item in report if item["status"] == "contradicted"),
        }
        total = len(report)
        confidence = summary["n_verified"] / total if total else 0.0

        return {
            "confidence": confidence,
            "verification_report": report,
            "summary": summary,
            "blocking_issues": blocking_issues,
            "flags": ["blocking_verification_issue"] if blocking_issues else [],
            "verified_claim_ids": [item["claim_id"] for item in report if item["status"] == "verified"],
            "unsupported_claim_ids": [item["claim_id"] for item in report if item["status"] == "unsupported"],
            "contradicted_claim_ids": [item["claim_id"] for item in report if item["status"] == "contradicted"],
        }


def _values_match(expected: Any, actual: Any) -> bool:
    expected_num = safe_float(expected)
    actual_num = safe_float(actual)
    if expected_num is not None and actual_num is not None:
        return abs(expected_num - actual_num) <= 1e-9
    return _normalize(expected) == _normalize(actual)


def _normalize(value: Any) -> str:
    if value is None:
        return "null"
    return str(value).strip()
