"""Rule-based medication safety checks for the SafeProbeMD prototype."""

from __future__ import annotations

from typing import Any

from safeprobemd.agents.base import BaseAgent, make_claim, nonempty, safe_float


RENAL_REVIEW_TERMS = ("vancomycin", "gentamicin", "tobramycin", "amikacin", "enoxaparin", "digoxin")


class MedicationSafetyAgent(BaseAgent):
    """Flag dosing-information gaps and renal-context uncertainty."""

    name = "medication_safety"
    required_fields = ("ids", "medications", "age", "weight_kg", "renal_hepatic_labs", "diagnosis_subset")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        medications = input_packet.get("medications") or []
        renal_hepatic_labs = input_packet.get("renal_hepatic_labs") or []
        weight_kg = input_packet.get("weight_kg")

        alerts: list[dict[str, Any]] = []
        dosing_flags: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        provenance: list[str] = []
        data_gaps: list[str] = []
        flags: list[str] = []
        meds_with_alerts: set[str] = set()

        if medications and weight_kg in (None, ""):
            data_gaps.append("missing_weight_kg")
            alert, claim = _weight_missing_alert(medications[0])
            alerts.append(alert)
            claims.append(claim)
            provenance.extend(claim["provenance"])

        for input_idx, med in enumerate(medications):
            snapshot_idx = int(med.get("_snapshot_index", input_idx))
            drug = str(med.get("drug") or med.get("medication") or "").strip()
            if not drug:
                data_gaps.append(f"missing_drug_name:medications.{snapshot_idx}")
                continue

            dose_missing = not nonempty(med.get("dose_val_rx")) or not nonempty(med.get("dose_unit_rx"))
            if dose_missing:
                claim_id = f"med_missing_dose_{snapshot_idx}"
                dose_value_path = f"medications.{snapshot_idx}.dose_val_rx"
                dose_unit_path = f"medications.{snapshot_idx}.dose_unit_rx"
                drug_path = f"medications.{snapshot_idx}.drug"
                rationale = f"{drug} is missing dose value or dose unit in the medication order."
                alert = {
                    "claim_id": claim_id,
                    "type": "dosing",
                    "drugs": [drug],
                    "severity": "moderate",
                    "rationale": rationale,
                    "evidence_refs": [drug_path, dose_value_path, dose_unit_path],
                }
                alerts.append(alert)
                dosing_flags.append({"drug": drug, "issue": "missing_dose_or_unit", "claim_id": claim_id})
                meds_with_alerts.add(drug)
                provenance.extend(alert["evidence_refs"])
                claims.append(
                    make_claim(
                        claim_id,
                        rationale,
                        alert["evidence_refs"],
                        claim_type="medication_safety",
                        severity="moderate",
                        evidence_values={
                            drug_path: med.get("drug"),
                            dose_value_path: med.get("dose_val_rx"),
                            dose_unit_path: med.get("dose_unit_rx"),
                        },
                    )
                )

            renal_lab = _abnormal_renal_lab(renal_hepatic_labs)
            if renal_lab and _requires_renal_context(drug):
                lab_idx = int(renal_lab.get("_snapshot_index", 0))
                lab_label_path = f"labs.{lab_idx}.label"
                lab_value_path = f"labs.{lab_idx}.valuenum"
                drug_path = f"medications.{snapshot_idx}.drug"
                claim_id = f"med_renal_context_{snapshot_idx}_{_claim_safe(drug)}"
                rationale = (
                    f"{drug} has renal-dose uncertainty because "
                    f"{renal_lab.get('label')} is abnormal in the routed renal lab subset."
                )
                alert = {
                    "claim_id": claim_id,
                    "type": "dosing",
                    "drugs": [drug],
                    "severity": "moderate",
                    "rationale": rationale,
                    "evidence_refs": [drug_path, lab_label_path, lab_value_path],
                }
                alerts.append(alert)
                dosing_flags.append({"drug": drug, "issue": "renal_context_uncertainty", "claim_id": claim_id})
                meds_with_alerts.add(drug)
                provenance.extend(alert["evidence_refs"])
                claims.append(
                    make_claim(
                        claim_id,
                        rationale,
                        alert["evidence_refs"],
                        claim_type="medication_safety",
                        severity="moderate",
                        evidence_values={
                            drug_path: med.get("drug"),
                            lab_label_path: renal_lab.get("label"),
                            lab_value_path: renal_lab.get("valuenum"),
                        },
                    )
                )

        duplicates = _duplicate_drugs(medications)
        for drug, indexes in duplicates.items():
            claim_id = f"med_duplicate_{_claim_safe(drug)}"
            paths = [f"medications.{idx}.drug" for idx in indexes]
            rationale = f"{drug} appears more than once in the routed medication list."
            alerts.append(
                {
                    "claim_id": claim_id,
                    "type": "duplicate",
                    "drugs": [drug],
                    "severity": "low",
                    "rationale": rationale,
                    "evidence_refs": paths,
                }
            )
            flags.append(f"duplicate_medication:{drug}")
            provenance.extend(paths)
            claims.append(
                make_claim(
                    claim_id,
                    rationale,
                    paths,
                    claim_type="medication_safety",
                    severity="low",
                    evidence_values={path: drug for path in paths},
                )
            )

        no_concern_meds = []
        for med in medications:
            drug = str(med.get("drug") or med.get("medication") or "").strip()
            if drug and drug not in meds_with_alerts:
                no_concern_meds.append(drug)

        return {
            "confidence": 0.78 if medications else 0.25,
            "abstained": not medications,
            "abstain_reason": "no medication orders available" if not medications else None,
            "med_alerts": alerts,
            "dosing_flags": dosing_flags,
            "no_concern_meds": no_concern_meds,
            "provenance": provenance,
            "flags": flags,
            "data_gaps": data_gaps,
            "claims": claims,
        }


def _weight_missing_alert(first_medication: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot_idx = int(first_medication.get("_snapshot_index", 0))
    drug_path = f"medications.{snapshot_idx}.drug"
    weight_path = "demographics.weight_kg"
    drug = str(first_medication.get("drug") or "routed medication").strip()
    claim_id = "med_missing_weight"
    rationale = "Weight is missing, so weight-based medication safety checks require human review."
    alert = {
        "claim_id": claim_id,
        "type": "dosing",
        "drugs": [drug],
        "severity": "moderate",
        "rationale": rationale,
        "evidence_refs": [drug_path, weight_path],
    }
    claim = make_claim(
        claim_id,
        rationale,
        alert["evidence_refs"],
        claim_type="medication_safety",
        severity="moderate",
        evidence_values={drug_path: first_medication.get("drug"), weight_path: None},
    )
    return alert, claim


def _abnormal_renal_lab(labs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for lab in labs:
        label = str(lab.get("label") or "").lower()
        if not any(term in label for term in ("creatinine", "bun", "urea")):
            continue
        value = safe_float(lab.get("valuenum"))
        low = safe_float(lab.get("ref_range_lower"))
        high = safe_float(lab.get("ref_range_upper"))
        flag = str(lab.get("flag") or "").lower()
        if flag and flag not in {"normal", "none"}:
            return lab
        if value is not None and ((low is not None and value < low) or (high is not None and value > high)):
            return lab
    return None


def _requires_renal_context(drug: str) -> bool:
    lowered = drug.lower()
    return any(term in lowered for term in RENAL_REVIEW_TERMS)


def _duplicate_drugs(medications: list[dict[str, Any]]) -> dict[str, list[int]]:
    seen: dict[str, list[int]] = {}
    display: dict[str, str] = {}
    for input_idx, med in enumerate(medications):
        drug = str(med.get("drug") or med.get("medication") or "").strip()
        if not drug:
            continue
        normalized = drug.lower()
        snapshot_idx = int(med.get("_snapshot_index", input_idx))
        seen.setdefault(normalized, []).append(snapshot_idx)
        display[normalized] = drug
    return {display[key]: indexes for key, indexes in seen.items() if len(indexes) > 1}


def _claim_safe(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    return "_".join(part for part in cleaned.split("_") if part)[:40] or "drug"
