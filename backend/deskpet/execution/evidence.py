"""Exact identity used to read completion evidence from the execution ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    run_id: str
    turn_id: str
    call_id: str
    effect_id: str
    target_digest: str | None = None
    artifact_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "turn_id", "call_id", "effect_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        for name in ("target_digest", "artifact_ref"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    context: EvidenceContext
    tool_name: str
    receipt_ref: str
    artifact_refs: tuple[str, ...]
    ok: bool = True
    phase: str = "executed"
    outcome: str = "success"


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    status: Literal["matched", "unknown"]
    records: tuple[CompletionEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "matched" and not self.records:
            raise ValueError("matched evidence requires at least one record")
        if self.status == "unknown" and self.records:
            raise ValueError("unknown evidence cannot contain records")


@runtime_checkable
class EvidenceResolver(Protocol):
    async def lookup_completion_evidence(
        self, context: EvidenceContext
    ) -> EvidenceSelection: ...


UNKNOWN_EVIDENCE = EvidenceSelection(status="unknown")
