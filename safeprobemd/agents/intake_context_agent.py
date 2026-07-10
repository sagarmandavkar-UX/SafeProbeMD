"""Neutral intake/context framing for one SafeProbeMD patient snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from safeprobemd.agents.base import BaseAgent, compact_join, make_claim, nonempty, safe_float


class IntakeContextAgent(BaseAgent):
    """Create a lightweight, non-diagnostic context header."""

    name = "intake_context"
    required_fields = ("ids", "demographics", "admission", "icu_summary")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        demographics = input_packet.get("demographics", {})
        admission = input_packet.get("admission", {})
        icu_summary = input_packet.get("icu_summary", {})

        age = safe_float(demographics.get("age"))
        gender = str(demographics.get("gender") or "").strip()
        age_band = _age_band(age)
        has_icu = bool(icu_summary.get("has_icu"))
        is_deceased = bool(admission.get("hospital_expire_flag"))
        los_days = safe_float(admission.get("los_days"))
        careunit = str(icu_summary.get("first_careunit") or icu_summary.get("last_careunit") or "")
        admission_type = str(admission.get("admission_type") or "unknown admission type")

        flags: list[str] = []
        data_gaps: list[str] = []
        provenance: list[str] = []
        claims: list[dict[str, Any]] = []

        if age is None:
            data_gaps.append("missing_age")
        else:
            provenance.append("demographics.age")
            claims.append(
                make_claim(
                    "intake_age",
                    f"Admission age is {age:g}.",
                    ["demographics.age"],
                    claim_type="context",
                    evidence_values={"demographics.age": demographics.get("age")},
                )
            )

        if not gender:
            data_gaps.append("missing_gender")
        else:
            provenance.append("demographics.gender")

        admit_time = admission.get("admit_time")
        discharge_time = admission.get("discharge_time")
        if _discharge_precedes_admit(admit_time, discharge_time):
            flags.append("inconsistent_window")

        if has_icu:
            provenance.append("icu_summary.has_icu")
            claims.append(
                make_claim(
                    "intake_icu_status",
                    "Admission includes ICU-level care.",
                    ["icu_summary.has_icu"],
                    claim_type="context",
                    evidence_values={"icu_summary.has_icu": icu_summary.get("has_icu")},
                )
            )

        summary_parts = [
            _patient_phrase(age_band, gender),
            f"{admission_type} admission",
            "ICU stay present" if has_icu else "no ICU stay selected",
        ]
        if los_days is not None:
            summary_parts.append(f"hospital LOS {los_days:.1f} days")
        if is_deceased:
            summary_parts.append("hospital death flag present")

        context_summary = compact_join(summary_parts, sep=", ")
        context_summary = context_summary[:397] + "..." if len(context_summary) > 400 else context_summary

        abstained = bool(data_gaps)
        confidence = 0.9 if not abstained and not flags else 0.65

        return {
            "confidence": confidence,
            "abstained": abstained,
            "abstain_reason": "required demographic context missing" if abstained else None,
            "context_summary": context_summary,
            "normalized_demographics": {
                "age": age,
                "age_band": age_band,
                "gender": gender,
            },
            "acuity_flags": {
                "is_icu": has_icu,
                "is_deceased": is_deceased,
                "los_days": los_days,
                "first_careunit": careunit,
            },
            "provenance": provenance,
            "flags": flags,
            "data_gaps": data_gaps,
            "claims": claims,
        }


def _age_band(age: float | None) -> str:
    if age is None:
        return "unknown"
    if age < 18:
        return "pediatric"
    if age >= 65:
        return "geriatric"
    return "adult"


def _patient_phrase(age_band: str, gender: str) -> str:
    gender_phrase = gender if nonempty(gender) else "unknown gender"
    if age_band == "unknown":
        return f"{gender_phrase} patient"
    return f"{age_band} {gender_phrase} patient"


def _parse_datetime(value: Any) -> datetime | None:
    if not nonempty(value):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _discharge_precedes_admit(admit_time: Any, discharge_time: Any) -> bool:
    admit_dt = _parse_datetime(admit_time)
    discharge_dt = _parse_datetime(discharge_time)
    if admit_dt is None or discharge_dt is None:
        return False
    return discharge_dt < admit_dt
