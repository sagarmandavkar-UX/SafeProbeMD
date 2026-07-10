"""Rule-based lab interpretation for curated MIMIC-IV lab packets."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from safeprobemd.agents.base import BaseAgent, make_claim, nonempty, safe_float


class LabInterpretationAgent(BaseAgent):
    """Summarize abnormal labs without seeing diagnoses, notes, or medications."""

    name = "lab_interpretation"
    required_fields = ("ids", "labs", "age", "gender")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        labs = input_packet.get("labs") or []
        findings: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        provenance: list[str] = []
        data_gaps: list[str] = []
        flags: list[str] = []

        for idx, lab in enumerate(labs):
            label = str(lab.get("label") or f"lab_{idx}").strip()
            value = lab.get("valuenum")
            display_value = lab.get("value") if nonempty(lab.get("value")) else value
            unit = str(lab.get("valueuom") or "").strip()
            low = safe_float(lab.get("ref_range_lower"))
            high = safe_float(lab.get("ref_range_upper"))
            flag = str(lab.get("flag") or "").strip()
            status = _classify_lab(lab)

            if status == "normal":
                continue

            if not unit:
                data_gaps.append(f"missing_unit:{label}")
            if low is None and high is None and not flag:
                data_gaps.append(f"missing_reference_range_or_flag:{label}")

            if _flag_value_mismatch(flag, safe_float(value), low, high):
                flags.append(f"flag_value_mismatch:{label}")

            value_path = f"labs.{idx}.valuenum"
            label_path = f"labs.{idx}.label"
            unit_path = f"labs.{idx}.valueuom"
            flag_path = f"labs.{idx}.flag"
            paths = [label_path, value_path, unit_path]
            if nonempty(flag):
                paths.append(flag_path)
            claim_id = f"lab_{idx}_{_claim_safe(label)}"
            interpretation = _interpretation(label, display_value, unit, status)
            finding = {
                "claim_id": claim_id,
                "label": label,
                "value": display_value,
                "uom": unit,
                "status": status,
                "interpretation": interpretation,
                "charttime": lab.get("charttime"),
                "category": lab.get("category"),
                "organ_system": _organ_system(label),
            }
            findings.append(finding)
            provenance.extend(paths)
            claims.append(
                make_claim(
                    claim_id,
                    interpretation,
                    paths,
                    claim_type="lab",
                    severity="high" if status == "critical" else "moderate",
                    evidence_values={
                        label_path: lab.get("label"),
                        value_path: lab.get("valuenum"),
                        unit_path: lab.get("valueuom"),
                        **({flag_path: lab.get("flag")} if nonempty(flag) else {}),
                    },
                )
            )

        trends = _detect_trends(labs)
        confidence = 0.86 if findings else 0.7 if labs else 0.2
        abstained = not labs

        return {
            "confidence": confidence,
            "abstained": abstained,
            "abstain_reason": "no curated labs available" if abstained else None,
            "lab_findings": findings,
            "salient_abnormalities": [item["label"] for item in findings[:12]],
            "trends": trends,
            "provenance": provenance,
            "flags": flags,
            "data_gaps": data_gaps,
            "claims": claims,
        }


def _classify_lab(lab: dict[str, Any]) -> str:
    flag = str(lab.get("flag") or "").strip().lower()
    if "critical" in flag or "panic" in flag:
        return "critical"

    value = safe_float(lab.get("valuenum"))
    low = safe_float(lab.get("ref_range_lower"))
    high = safe_float(lab.get("ref_range_upper"))
    if value is not None:
        if high is not None and value > high:
            if high > 0 and value >= high * 2:
                return "critical"
            return "abnormal"
        if low is not None and value < low:
            if low > 0 and value <= low * 0.5:
                return "critical"
            return "abnormal"

    if flag and flag not in {"normal", "none"}:
        return "abnormal"
    return "normal"


def _flag_value_mismatch(flag: str, value: float | None, low: float | None, high: float | None) -> bool:
    normalized = flag.strip().lower()
    if normalized not in {"abnormal", "high", "low"} or value is None:
        return False
    if low is None and high is None:
        return False
    in_range = (low is None or value >= low) and (high is None or value <= high)
    return in_range


def _interpretation(label: str, value: Any, unit: str, status: str) -> str:
    unit_text = f" {unit}" if unit else ""
    return f"{label} is {status} at {value}{unit_text}."


def _organ_system(label: str) -> str:
    lowered = label.lower()
    if any(term in lowered for term in ("creatinine", "bun", "urea")):
        return "renal"
    if any(term in lowered for term in ("bilirubin", "ast", "alt", "alkaline", "albumin", "inr", "ptt")):
        return "hepatic_coagulation"
    if any(term in lowered for term in ("wbc", "white", "hemoglobin", "hematocrit", "platelet")):
        return "hematology"
    if any(term in lowered for term in ("lactate", "ph", "po2", "pco2", "bicarbonate")):
        return "acid_base_respiratory"
    return "general"


def _detect_trends(labs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_label: dict[str, list[tuple[Any, float]]] = defaultdict(list)
    for lab in labs:
        value = safe_float(lab.get("valuenum"))
        label = str(lab.get("label") or "").strip()
        if value is None or not label:
            continue
        by_label[label].append((lab.get("charttime"), value))

    trends: list[dict[str, Any]] = []
    for label, values in by_label.items():
        if len(values) < 2:
            continue
        values.sort(key=lambda item: str(item[0] or ""))
        first = values[0][1]
        last = values[-1][1]
        if last > first:
            direction = "rising"
        elif last < first:
            direction = "falling"
        else:
            direction = "stable"
        trends.append({"label": label, "direction": direction})
    return trends


def _claim_safe(label: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in label)
    return "_".join(part for part in cleaned.split("_") if part)[:40] or "lab"
