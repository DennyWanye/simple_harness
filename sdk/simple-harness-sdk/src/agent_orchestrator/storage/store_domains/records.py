# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Store records: errors, row types, state constants and row conversions."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...contracts import ContractError, Event, ResultEnvelope
from ...contracts.error_table import CodedFault, RoundFaultCode
from ...contracts.models import sha256_hex


class StoreError(RuntimeError):
    pass


class StoredResultCorrupt(StoreError, CodedFault):
    """A stored attempt result that no longer decodes: retrying reads the same bytes."""

    code = RoundFaultCode.STORED_RESULT_CORRUPT


class CodedStoreError(StoreError):
    """A refusal that carries its stable code as an attribute (第 1 批 T02).

    Callers across a boundary (the Host) read ``code``; nobody slices it out of the message.
    The message stays ``code`` or ``code: detail`` so logs read the same as before.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class StoreConflict(StoreError):
    """A CAS write found a different version than expected (§17.3)."""


class CodedStoreConflict(StoreConflict, CodedStoreError):
    """A CAS/ordering conflict with a typed code (the operator refusals the Host explains to people)."""


class StoreBusy(StoreError):
    """Another instance holds the SQLite write lock (D3-10'); skip this cycle and retry."""


class SchemaIncompatible(StoreError):
    pass


class InjectedCrash(RuntimeError):
    """Raised at an armed fault point; simulates the process dying right there."""

    def __init__(self, point: str) -> None:
        super().__init__(f"injected crash at {point}")
        self.point = point


@dataclass(frozen=True, slots=True)
class StoredResult:
    """A received Result Envelope plus its verification bookkeeping."""

    envelope: ResultEnvelope
    turn_id: str
    verification_state: str
    verdict: str | None
    received_at: float
    artifacts: tuple[str, ...]
    usage_refs: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_json(),
            "turn_id": self.turn_id,
            "verification_state": self.verification_state,
            "verdict": self.verdict,
            "received_at": self.received_at,
            "artifacts": list(self.artifacts),
            "usage_refs": list(self.usage_refs),
        }


@dataclass(frozen=True, slots=True)
class DispatchIntent:
    """Durable intent to call the SDK with a fixed identity (plan D5)."""

    intent_id: str
    kind: str
    subject_id: str
    mission_id: str
    state: str
    version: int
    creation_key: str
    input_id: str
    input_hash: str
    config: Mapping[str, Any]
    expected_turn_id: str | None
    agent_id: str | None
    receipt: Mapping[str, Any] | None
    lease_owner: str | None
    lease_expires_at: float | None
    replays: int
    created_at: float

    def to_json(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "mission_id": self.mission_id,
            "state": self.state,
            "version": self.version,
            "creation_key": self.creation_key,
            "input_id": self.input_id,
            "input_hash": self.input_hash,
            "config": dict(self.config),
            "expected_turn_id": self.expected_turn_id,
            "agent_id": self.agent_id,
            "receipt": None if self.receipt is None else dict(self.receipt),
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "replays": self.replays,
            "created_at": self.created_at,
        }


INTENT_STATES = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED")
VERIFICATION_STATES = ("PENDING", "RUNNING", "DONE", "REJECTED")
# P3.1 fix F-ORCH-3: an artifact is UNVERIFIED until its result is judged
ARTIFACT_VERIFICATION_STATES = ("UNVERIFIED", "VERIFIED", "REJECTED")
# P3.2 D4: a workspace directory is CREATING until it is fully built, ACTIVE while it may
# be used, CLEANED once its directory was removed (the row stays)
WORKSPACE_STATES = ("CREATING", "ACTIVE", "CLEANED")
_WORKSPACE_COLUMNS = (
    "workspace_id,kind,mission_id,attempt_id,base_snapshot,state,json,created_at,updated_at"
)


def _workspace_row(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": row[0],
        "kind": row[1],
        "mission_id": row[2],
        "attempt_id": row[3],
        "base_snapshot": row[4],
        "state": row[5],
        "detail": json.loads(row[6]),
        "created_at": row[7],
        "updated_at": row[8],
    }


def _loads(text: str) -> Any:
    return json.loads(text)


def _event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        id=row["event_id"],
        type=row["type"],
        trace_id=row["trace_id"],
        mission_id=row["mission_id"],
        task_id=row["task_id"],
        attempt_id=row["attempt_id"],
        actor_type=row["actor_type"],
        actor_id=row["actor_id"],
        payload=_loads(row["payload_json"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        schema_version=row["schema_version"],
        seq=row["seq"],
    )


def _intent_from_row(row: sqlite3.Row) -> DispatchIntent:
    return DispatchIntent(
        intent_id=row["intent_id"],
        kind=row["kind"],
        subject_id=row["subject_id"],
        mission_id=row["mission_id"],
        state=row["state"],
        version=row["version"],
        creation_key=row["creation_key"],
        input_id=row["input_id"],
        input_hash=row["input_hash"],
        config=_loads(row["config_json"]),
        expected_turn_id=row["expected_turn_id"],
        agent_id=row["agent_id"],
        receipt=None if row["receipt_json"] is None else _loads(row["receipt_json"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        replays=row["replays"],
        created_at=row["created_at"],
    )


def _stored_result(data: Mapping[str, Any]) -> StoredResult:
    try:
        return StoredResult(
            envelope=ResultEnvelope.from_json(data["envelope"], strict=False),
            turn_id=data["turn_id"],
            verification_state=data["verification_state"],
            verdict=data.get("verdict"),
            received_at=data["received_at"],
            artifacts=tuple(data.get("artifacts", ())),
            usage_refs=tuple(data.get("usage_refs", ())),
        )
    except (KeyError, ContractError) as error:
        raise StoredResultCorrupt(f"corrupt stored result: {error}") from error


def proposal_hash(proposal: object) -> str:
    return sha256_hex(proposal)


def library_copy(library: Path, into: Path) -> Path:
    """Copy ``orchestrator.db`` with its ``-wal`` / ``-shm`` into ``into`` (plan D8-1'):
    the library itself is never opened, so it cannot be written."""

    into.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        source = library.with_name(library.name + suffix)
        if source.is_file():
            shutil.copy2(source, into / (library.name + suffix))
    return into / library.name
