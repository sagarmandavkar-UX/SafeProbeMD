"""Base classes and shared helpers for SafeProbeMD agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "0.1"


class AgentInputError(ValueError):
    """Raised when an agent input packet is structurally invalid."""


class BaseAgent(ABC):
    """Abstract base class for deterministic SafeProbeMD prototype agents.

    The public `run()` method validates input, creates the required output
    envelope, delegates agent-specific work to `_run_impl()`, and normalizes the
    final payload. Later LLM-backed agents can preserve this interface.
    """

    name = "base"
    input_schema_version = SCHEMA_VERSION
    required_fields: tuple[str, ...] = ("ids",)

    def run(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        """Run the agent and return a schema-versioned output envelope."""

        try:
            self.validate_input(input_packet)
        except AgentInputError as exc:
            ids = input_packet.get("ids", {}) if isinstance(input_packet, dict) else {}
            output = self.output_envelope(ids)
            output.update(
                {
                    "abstained": True,
                    "abstain_reason": str(exc),
                    "data_gaps": [str(exc)],
                }
            )
            return output

        output = self.output_envelope(input_packet.get("ids", {}))
        result = self._run_impl(deepcopy(input_packet)) or {}
        self._merge_result(output, result)
        self._stamp_claim_agents(output)
        return output

    def validate_input(self, input_packet: dict[str, Any]) -> None:
        """Validate that an input packet has the minimum required structure."""

        if not isinstance(input_packet, dict):
            raise AgentInputError("input_packet must be a dictionary")

        missing = [field for field in self.required_fields if field not in input_packet]
        if missing:
            raise AgentInputError(f"missing required input fields: {', '.join(missing)}")

        ids = input_packet.get("ids")
        if not isinstance(ids, dict):
            raise AgentInputError("ids must be a dictionary")

    def output_envelope(self, ids: dict[str, Any]) -> dict[str, Any]:
        """Create the required shared output envelope."""

        return {
            "agent": self.name,
            "schema_version": self.input_schema_version,
            "patient_id": str(ids.get("patient_id") or ""),
            "admission_id": str(ids.get("admission_id") or ""),
            "confidence": 0.0,
            "abstained": False,
            "abstain_reason": None,
            "provenance": [],
            "flags": [],
            "data_gaps": [],
            "claims": [],
        }

    @abstractmethod
    def _run_impl(self, input_packet: dict[str, Any]) -> dict[str, Any]:
        """Run agent-specific logic."""

    def _merge_result(self, output: dict[str, Any], result: dict[str, Any]) -> None:
        for key, value in result.items():
            if key in {"provenance", "flags", "data_gaps", "claims"}:
                output[key] = _dedupe_list(output.get(key, []) + _as_list(value))
            else:
                output[key] = value

    def _stamp_claim_agents(self, output: dict[str, Any]) -> None:
        for claim in output.get("claims", []):
            claim.setdefault("agent", self.name)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe_list(items: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in items:
        marker = repr(item)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def make_claim(
    claim_id: str,
    text: str,
    provenance: list[str],
    claim_type: str = "clinical",
    severity: str | None = None,
    evidence_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a machine-checkable claim."""

    claim: dict[str, Any] = {
        "claim_id": claim_id,
        "text": text,
        "claim_type": claim_type,
        "provenance": provenance,
    }
    if severity:
        claim["severity"] = severity
    if evidence_values is not None:
        claim["evidence_values"] = evidence_values
    return claim


def get_path(data: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path through dicts/lists.

    List indexes are represented as numeric path components, e.g.
    `labs.0.valuenum`.
    """

    current = data
    if not path:
        return False, None

    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return False, None
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return False, None
            current = current[idx]
        else:
            return False, None
    return True, current


def nonempty(value: Any) -> bool:
    """Return True when a value is present enough to support a claim."""

    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def safe_float(value: Any) -> float | None:
    """Convert numeric-ish values to float, returning None on failure."""

    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact_join(parts: list[Any], sep: str = "; ") -> str:
    """Join non-empty stringified values."""

    return sep.join(str(part) for part in parts if nonempty(part))
