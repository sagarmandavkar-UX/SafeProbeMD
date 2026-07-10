"""Deterministic diagnostic summarization from codes and discharge-note sections."""

from __future__ import annotations

import re
from typing import Any

from safeprobemd.agents.base import BaseAgent, make_claim, nonempty


class DiagnosticReasoningAgent(BaseAgent):
    """Build a grounded problem list without reading labs or medications."""

    name = "diagnostic_reasoning"
    required_fields = ("ids", "context_header", "clinical_note", "diagnoses", "procedures")

    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        diagnoses = input_packet.get("diagnoses") or []
        clinical_note = input_packet.get("clinical_note") or {}
        sections = clinical_note.get("sections") or {}

        problems: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        provenance: list[str] = []
        seen: set[str] = set()

        sorted_diagnoses = sorted(
            enumerate(diagnoses),
            key=lambda item: _sort_seq(item[1].get("seq_num"), item[0]),
        )
        for _, (idx, diagnosis) in enumerate(sorted_diagnoses[:15]):
            description = str(diagnosis.get("description") or "").strip()
            code = str(diagnosis.get("icd_code") or "").strip()
            version = str(diagnosis.get("icd_version") or "").strip()
            if not description and not code:
                continue

            display = description or f"ICD-{version} {code}".strip()
            key = _normalize_problem(display)
            if key in seen:
                continue
            seen.add(key)

            seq_num = diagnosis.get("seq_num")
            primary = _is_primary(seq_num, len(problems))
            paths = [f"diagnoses.{idx}.description", f"diagnoses.{idx}.icd_code"]
            evidence_values = {
                f"diagnoses.{idx}.description": diagnosis.get("description"),
                f"diagnoses.{idx}.icd_code": diagnosis.get("icd_code"),
            }
            claim_id = f"diagnostic_problem_{len(problems) + 1}"
            problems.append(
                {
                    "claim_id": claim_id,
                    "problem": display,
                    "icd_refs": [code] if code else [],
                    "icd_version": version,
                    "evidence": _code_evidence(code, version, description),
                    "primary": primary,
                    "confidence": 0.86 if description else 0.7,
                }
            )
            provenance.extend(paths)
            claims.append(
                make_claim(
                    claim_id,
                    f"ICD diagnosis evidence supports problem: {display}.",
                    paths,
                    claim_type="diagnosis",
                    evidence_values=evidence_values,
                )
            )

        note_section_summaries: list[dict[str, Any]] = []
        discharge_dx = _first_present_section(sections, ("discharge_diagnosis", "discharge_diagnoses"))
        if discharge_dx:
            path, text = discharge_dx
            excerpt = _excerpt(text)
            claim_id = "diagnostic_note_discharge_diagnosis"
            note_section_summaries.append(
                {
                    "claim_id": claim_id,
                    "section": path.split(".")[-1],
                    "summary": excerpt,
                    "evidence": path,
                }
            )
            provenance.append(path)
            claims.append(
                make_claim(
                    claim_id,
                    f"Discharge diagnosis section contains: {excerpt}",
                    [path],
                    claim_type="note_section",
                    evidence_values={path: text},
                )
            )

        data_gaps = []
        if not diagnoses:
            data_gaps.append("missing_diagnoses")
        if not clinical_note:
            data_gaps.append("missing_clinical_note")

        abstained = not problems and not note_section_summaries

        return {
            "confidence": 0.84 if problems else 0.35,
            "abstained": abstained,
            "abstain_reason": "no diagnostic codes or discharge-note sections available" if abstained else None,
            "problems": problems,
            "note_section_summaries": note_section_summaries,
            "differential_notes": [],
            "unresolved_questions": data_gaps,
            "provenance": provenance,
            "data_gaps": data_gaps,
            "claims": claims,
        }


def _sort_seq(seq_num: Any, fallback: int) -> tuple[int, int]:
    try:
        return int(seq_num), fallback
    except (TypeError, ValueError):
        return 9999, fallback


def _is_primary(seq_num: Any, position: int) -> bool:
    try:
        return int(seq_num) == 1
    except (TypeError, ValueError):
        return position == 0


def _normalize_problem(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _code_evidence(code: str, version: str, description: str) -> str:
    code_text = f"ICD-{version} {code}".strip() if code else "ICD code"
    if description:
        return f"{code_text}: {description}"
    return code_text


def _first_present_section(sections: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str] | None:
    for key in keys:
        value = sections.get(key)
        if nonempty(value):
            return f"clinical_note.sections.{key}", str(value)
    return None


def _excerpt(text: Any, max_chars: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."
