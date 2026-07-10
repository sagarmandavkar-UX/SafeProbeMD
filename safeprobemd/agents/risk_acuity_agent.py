"""Rule-based risk and acuity interpretation from MIMIC-IV derived scores."""

from __future__ import annotations

from typing import Any

from safeprobemd.agents.base import BaseAgent, make_claim, nonempty, safe_float


class RiskAcuityAgent(BaseAgent):
    """Translate ICU-derived scores into bounded acuity statements."""

    name = "risk_acuity"
    required_fields = ("ids", "icu_summary", "derived_risk_scores", "abnormal_labs", "intake_acuity_flags")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        icu_summary = input_packet.get("icu_summary") or {}
        scores = input_packet.get("derived_risk_scores") or {}

        if not icu_summary.get("has_icu") or not scores.get("has_icu_scores", True):
            return {
                "confidence": 0.15,
                "abstained": True,
                "abstain_reason": "no ICU-derived severity available",
                "acuity_tier": None,
                "mortality_risk_band": "unavailable",
                "organ_dysfunction": [],
                "driving_scores": {},
                "caveats": ["no ICU-derived severity available"],
                "data_gaps": ["missing_icu_derived_scores"],
            }

        sofa = safe_float(scores.get("sofa_max"))
        sapsii = safe_float(scores.get("sapsii"))
        apsiii = safe_float(scores.get("apsiii"))
        sapsii_prob = safe_float(scores.get("sapsii_prob"))
        apsiii_prob = safe_float(scores.get("apsiii_prob"))

        if sofa is None and sapsii is None and apsiii is None and sapsii_prob is None and apsiii_prob is None:
            return {
                "confidence": 0.15,
                "abstained": True,
                "abstain_reason": "no ICU-derived severity available",
                "acuity_tier": None,
                "mortality_risk_band": "unavailable",
                "organ_dysfunction": [],
                "driving_scores": {},
                "caveats": ["all severity score fields are missing"],
                "data_gaps": ["missing_icu_derived_scores"],
            }

        tier = _acuity_tier(sofa, sapsii_prob, apsiii_prob)
        mortality_band = _mortality_band(sapsii_prob, apsiii_prob)
        organ_dysfunction, organ_claims = _organ_dysfunction(scores.get("sofa_components") or {})

        claims: list[dict[str, Any]] = []
        provenance: list[str] = []
        if sofa is not None:
            path = "derived_risk_scores.sofa_max"
            provenance.append(path)
            claims.append(
                make_claim(
                    "risk_sofa_tier",
                    f"SOFA maximum {sofa:g} supports {tier} acuity tier.",
                    [path],
                    claim_type="risk",
                    severity=_risk_severity(tier),
                    evidence_values={path: scores.get("sofa_max")},
                )
            )
        elif sapsii_prob is not None or apsiii_prob is not None:
            path = "derived_risk_scores.sapsii_prob" if sapsii_prob is not None else "derived_risk_scores.apsiii_prob"
            provenance.append(path)
            claims.append(
                make_claim(
                    "risk_probability_tier",
                    f"Mortality probability score supports {tier} acuity tier.",
                    [path],
                    claim_type="risk",
                    severity=_risk_severity(tier),
                    evidence_values={path: scores.get(path.split(".")[-1])},
                )
            )

        if sapsii_prob is not None:
            path = "derived_risk_scores.sapsii_prob"
            provenance.append(path)
            claims.append(
                make_claim(
                    "risk_sapsii_probability",
                    f"SAPS-II mortality probability maps to {mortality_band} risk band.",
                    [path],
                    claim_type="risk",
                    evidence_values={path: scores.get("sapsii_prob")},
                )
            )
        elif apsiii_prob is not None:
            path = "derived_risk_scores.apsiii_prob"
            provenance.append(path)
            claims.append(
                make_claim(
                    "risk_apsiii_probability",
                    f"APS-III mortality probability maps to {mortality_band} risk band.",
                    [path],
                    claim_type="risk",
                    evidence_values={path: scores.get("apsiii_prob")},
                )
            )

        claims.extend(organ_claims)
        for claim in organ_claims:
            provenance.extend(claim.get("provenance", []))

        flags = []
        if _score_inconsistency(tier, sapsii_prob, apsiii_prob):
            flags.append("score_inconsistency")

        return {
            "confidence": 0.84,
            "acuity_tier": tier,
            "mortality_risk_band": mortality_band,
            "organ_dysfunction": organ_dysfunction,
            "driving_scores": {
                "sofa": sofa,
                "sapsii": sapsii,
                "apsiii": apsiii,
                "sapsii_prob": sapsii_prob,
                "apsiii_prob": apsiii_prob,
            },
            "caveats": [],
            "provenance": provenance,
            "flags": flags,
            "claims": claims,
        }


def _acuity_tier(sofa: float | None, sapsii_prob: float | None, apsiii_prob: float | None) -> str:
    if sofa is not None:
        if sofa >= 12:
            return "critical"
        if sofa >= 7:
            return "high"
        if sofa >= 3:
            return "moderate"
        return "low"
    probs = [value for value in (sapsii_prob, apsiii_prob) if value is not None]
    if not probs:
        return "moderate"
    prob = max(probs)
    if prob >= 0.4:
        return "critical"
    if prob >= 0.2:
        return "high"
    if prob >= 0.08:
        return "moderate"
    return "low"


def _mortality_band(sapsii_prob: float | None, apsiii_prob: float | None) -> str:
    probs = [value for value in (sapsii_prob, apsiii_prob) if value is not None]
    if not probs:
        return "unavailable"
    prob = max(probs)
    if prob >= 0.4:
        return "very_high"
    if prob >= 0.2:
        return "high"
    if prob >= 0.08:
        return "moderate"
    return "low"


def _risk_severity(tier: str) -> str:
    return "high" if tier in {"high", "critical"} else "moderate" if tier == "moderate" else "low"


def _organ_dysfunction(components: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    organ_items: list[str] = []
    claims: list[dict[str, Any]] = []
    for organ, raw_score in components.items():
        score = safe_float(raw_score)
        if score is None or score < 2:
            continue
        path = f"derived_risk_scores.sofa_components.{organ}"
        text = f"SOFA component {organ} score {score:g} indicates organ dysfunction signal."
        organ_items.append(f"{organ}: {score:g}")
        claims.append(
            make_claim(
                f"risk_organ_{_claim_safe(str(organ))}",
                text,
                [path],
                claim_type="risk",
                severity="moderate",
                evidence_values={path: raw_score},
            )
        )
    return organ_items, claims


def _score_inconsistency(tier: str, sapsii_prob: float | None, apsiii_prob: float | None) -> bool:
    probs = [value for value in (sapsii_prob, apsiii_prob) if value is not None]
    if len(probs) < 2:
        return False
    return max(probs) - min(probs) >= 0.35 and tier in {"low", "critical"}


def _claim_safe(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    return "_".join(part for part in cleaned.split("_") if part)[:40] or "component"
