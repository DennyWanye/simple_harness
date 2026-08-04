"""Structured, presentation-only evidence for a durably blocked Root."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from deskpet.execution.contracts import (
    IdempotencyConflict,
    OutcomeStatus,
    RunEventCandidate,
    RunRecord,
    TerminalConflict,
    canonical_json,
    stable_event_id,
)


class RootBlockReasonV1(str, Enum):
    PROVIDER_BINDING_UNAVAILABLE = "provider_binding_unavailable"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    EXTERNAL_DEPENDENCY_UNAVAILABLE = "external_dependency_unavailable"
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"


_REASON_PRODUCERS = {
    RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE: "session_route_resolver",
    RootBlockReasonV1.CAPABILITY_UNAVAILABLE: "capability_admission",
    RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE: "external_dependency_retry",
    RootBlockReasonV1.WORKSPACE_UNAVAILABLE: "workspace_resolver",
}


class PreflightBlocked(RuntimeError):
    """Typed handoff from a trusted preflight to ``RunKernel.start_blocked``."""

    def __init__(
        self,
        reason_code: RootBlockReasonV1 | str,
        evidence_refs: Sequence[str],
    ) -> None:
        reason = RootBlockReasonV1(reason_code)
        if reason is RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE:
            raise ValueError("external dependency failures are not preflight blocks")
        refs = tuple(str(item).strip() for item in evidence_refs)
        if not refs or any(not item for item in refs):
            raise ValueError("preflight block requires evidence refs")
        self.reason_code = reason
        self.evidence_refs = refs
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class RunBlockSignalV1:
    root_run_id: str
    reason_code: RootBlockReasonV1 | str
    evidence_refs: Sequence[str]
    producer: str
    created_event_id: str
    created_at: float
    schema_version: int = 1

    def __post_init__(self) -> None:
        for field in ("root_run_id", "producer", "created_event_id"):
            value = str(getattr(self, field)).strip()
            if not value:
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, value)
        reason = RootBlockReasonV1(self.reason_code)
        object.__setattr__(self, "reason_code", reason)
        refs = tuple(str(item).strip() for item in self.evidence_refs)
        if not refs or any(not item for item in refs):
            raise ValueError("block signal requires non-empty evidence refs")
        if len(set(refs)) != len(refs):
            raise ValueError("block signal evidence refs must be unique")
        object.__setattr__(self, "evidence_refs", refs)
        if self.producer != _REASON_PRODUCERS[reason]:
            raise ValueError("block reason is not owned by this producer")
        if self.schema_version != 1:
            raise ValueError("unsupported RunBlockSignalV1 schema")
        if isinstance(self.created_at, bool):
            raise ValueError("created_at must be numeric")
        object.__setattr__(self, "created_at", float(self.created_at))

    @property
    def signal_id(self) -> str:
        return "run-block:" + hashlib.sha256(
            canonical_json(
                {
                    "root_run_id": self.root_run_id,
                    "schema_version": self.schema_version,
                }
            ).encode("utf-8")
        ).hexdigest()

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.public_payload()).encode("utf-8")
        ).hexdigest()

    def public_payload(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "reason_code": self.reason_code.value,
            "evidence_refs": list(self.evidence_refs),
            "producer": self.producer,
            "created_event_id": self.created_event_id,
            "schema_version": self.schema_version,
        }

    def store_values(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "root_run_id": self.root_run_id,
            "reason_code": self.reason_code.value,
            "evidence_refs_json": canonical_json(list(self.evidence_refs)),
            "producer": self.producer,
            "created_event_id": self.created_event_id,
            "payload_hash": self.payload_hash,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
        }


class RunBlockReporter:
    """Terminal commit extension that atomically records a block signal."""

    receipt_kind = "deskpet.run-block-signal/v1"

    def __init__(self, signal: RunBlockSignalV1) -> None:
        self.signal = signal

    @property
    def descriptor(self) -> Any:
        from deskpet.harness.contracts import HostExtensionRefV1

        return HostExtensionRefV1(
            kind=self.receipt_kind,
            ref=self.signal.signal_id,
            content_hash=self.signal.payload_hash,
        )

    def _validate_terminal(
        self, record: RunRecord, terminal_event: RunEventCandidate
    ) -> None:
        if record.run_id != record.context.root_run_id or record.context.parent_run_id is not None:
            raise TerminalConflict(
                "child_block_signal_forbidden",
                "only a Root may commit a run block signal",
            )
        if record.run_id != self.signal.root_run_id:
            raise TerminalConflict(
                "run_block_signal_scope_conflict",
                "block signal belongs to another Root",
            )
        if terminal_event.status is not OutcomeStatus.FAILED:
            raise TerminalConflict(
                "run_block_signal_terminal_conflict",
                "block signal requires a failed terminal",
            )
        expected_event_id = stable_event_id(record.run_id, terminal_event.event_key)
        if self.signal.created_event_id != expected_event_id:
            raise TerminalConflict(
                "run_block_signal_event_conflict",
                "block signal must name the atomic terminal event",
            )

    async def apply_terminal_commit(
        self,
        transaction: Any,
        *,
        record: RunRecord,
        terminal_event: RunEventCandidate,
    ) -> Mapping[str, str]:
        self._validate_terminal(record, terminal_event)
        row = await transaction.insert_or_verify_run_block_signal(
            self.signal.store_values()
        )
        if str(row["payload_hash"]) != self.signal.payload_hash:
            raise IdempotencyConflict(
                "run_block_signal_conflict",
                "stored run block signal differs from the terminal request",
            )
        return {
            "kind": self.receipt_kind,
            "ref": self.signal.signal_id,
            "content_hash": self.signal.payload_hash,
        }

    async def verify_terminal_replay(
        self,
        transaction: Any,
        *,
        record: RunRecord,
        terminal_event: RunEventCandidate,
    ) -> None:
        self._validate_terminal(record, terminal_event)
        row = await transaction.read_run_block_signal(record.run_id)
        if row is None or any(
            row[field] != value
            for field, value in self.signal.store_values().items()
        ):
            raise IdempotencyConflict(
                "run_block_signal_conflict",
                "terminal replay does not match the durable block signal",
            )


def block_signal_for_terminal(
    *,
    root_run_id: str,
    event_key: str,
    reason_code: RootBlockReasonV1 | str,
    evidence_refs: Sequence[str],
    created_at: float,
) -> RunBlockSignalV1:
    """Build a signal using the sole producer authorised for its reason."""

    reason = RootBlockReasonV1(reason_code)
    return RunBlockSignalV1(
        root_run_id=root_run_id,
        reason_code=reason,
        evidence_refs=evidence_refs,
        producer=_REASON_PRODUCERS[reason],
        created_event_id=stable_event_id(root_run_id, event_key),
        created_at=created_at,
    )


__all__ = [
    "PreflightBlocked",
    "RootBlockReasonV1",
    "RunBlockReporter",
    "RunBlockSignalV1",
    "block_signal_for_terminal",
]
