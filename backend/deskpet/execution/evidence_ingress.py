# SPDX-License-Identifier: BUSL-1.1

"""Durable S1 ExecutionEvidence ingress, Harness evidence reservations and terminal drain.

S5b Task 2 (design-freeze §3) — one owner for every Harness fact that reaches the
TaskScope archive:

* ``reserve`` allocates ``source_sequence`` in ``harness_evidence_reservations``
  **before** a physical action whose fact lives in another database (SDK effect,
  Provider invocation); ``ingest`` later imports the fact under that sequence.
* state.db facts (snapshot receipt, route decision, route tool invocation) call
  ``ingest_ledger_fact_tx`` inside the ledger's own write transaction
  (reserve + ingest, one commit).
* ``next_sequence = MAX(reservations ∪ ingest receipts) + 1`` — a late fact can
  never be locked out by a terminal allocated from receipts alone (probe A9).
* ``drain_reservations`` is the terminal observer's single drain: a reserved row
  whose fact is readable is ingested through the same ``commit_fact`` path the
  hook uses (objective event included), otherwise a tombstone of the same kind
  (``{"status": "abandoned", "reservation_id": …}``) is ingested and the row is
  marked ``abandoned``.  ``authorize_terminal`` refuses while any row is still
  ``reserved``.  A crash mid-drain is replayed by the next owner — every step is
  idempotent on ``source_event_id``.
* ``commit_fact`` writes the Host objective event (``host.file``/``host.test``),
  its ``human_memory_evidence`` row (refs usable by closure plans) and the
  ``harness.tool_invocation`` import in **one** state.db transaction.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import (
    TaskScopeProtocolError,
    canonical_hash,
    identifier,
    reject_private_payload,
    validate_execution_evidence,
    validate_refs,
)
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskScopeConflict,
    TaskScopeNotFound,
    _uuid,
)

RESERVATION_KINDS: frozenset[str] = frozenset(
    {
        "provider_invocation",
        "tool_invocation",
        "context_snapshot",
        "route_decision",
        "run_terminal",
    }
)
RESERVATION_STATUSES: frozenset[str] = frozenset({"reserved", "ingested", "abandoned"})
# Tombstone ``public_payload.status`` values (the row itself always resolves as
# ``abandoned``): ``abandoned`` = fact unreadable at Run terminal (PROJECT_EFFECT
# stays material), ``rejected_fact`` = fact readable but rejected by the Host
# commit (material likewise), ``rejected`` = the SDK denied the effect after the
# reservation, nothing dispatched (trivial).
TOMBSTONE_STATUSES: frozenset[str] = frozenset({"abandoned", "rejected_fact", "rejected"})
MATERIAL_TOMBSTONE_STATUSES: frozenset[str] = frozenset({"abandoned", "rejected_fact"})


_STABLE_CODE = re.compile(r"[a-z][A-Za-z0-9_:.\[\]/-]{0,127}")


def _stable_code(exc: BaseException) -> str:
    """Public reason token of a Host rejection: the specific message when it is a
    token (``objective_evidence_hash_conflict``, ``credential_value_rejected:…``),
    else the class-level ``code``, never free text."""

    text = str(exc).strip()
    if text and _STABLE_CODE.fullmatch(text):
        return text[:128]
    code = getattr(exc, "code", None)
    return str(code or type(exc).__name__)[:128]
HOST_OBJECTIVE_AUTHORITY_REF = "host:objective-event-recorder:v1"
HOST_HARNESS_FACT_AUTHORITY_REF = "host:harness-fact-ingress:v1"


class TerminalWatermarkPending(RuntimeError):
    code = "task_scope_terminal_watermark_pending"


@dataclass(frozen=True, slots=True)
class ExecutionIngestReceipt:
    receipt_id: str
    task_scope_id: str
    run_id: str
    source_sequence: int
    source_event_id: str
    evidence_hash: str
    event_id: str
    durable_source_sequence: int
    terminal_source_sequence: int | None


@dataclass(frozen=True, slots=True)
class TerminalGateReceipt:
    gate_receipt_id: str
    task_scope_id: str
    run_id: str
    terminal_source_sequence: int
    durable_source_sequence: int


@dataclass(frozen=True, slots=True)
class EvidenceReservation:
    reservation_id: str
    run_id: str
    task_scope_id: str
    source_sequence: int
    source_event_id: str
    kind: str
    status: str
    reserved_at: float
    resolved_at: float | None
    tool_name: str | None = None


@dataclass(frozen=True, slots=True)
class RunScopeBinding:
    run_id: str
    host_run_id: str
    task_scope_id: str
    subject: str


@dataclass(frozen=True, slots=True)
class ObjectiveEventSpec:
    """One Host objective event (design-freeze §2): ``host.file`` or ``host.test``."""

    event_kind: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.event_kind not in {"host.file", "host.test"}:
            raise ValueError("objective_event_kind_invalid")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class ToolInvocationFact:
    """Settled SDK effect → ``harness.tool_invocation`` (+ objective event)."""

    run_id: str
    effect_id: str
    call_id: str
    tool_name: str
    effect_state: str
    outcome: str
    error_code: str | None
    objective: ObjectiveEventSpec | None

    kind = "tool_invocation"

    @property
    def source_event_id(self) -> str:
        return f"effect:{self.effect_id}"

    def public_payload(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "effect_id": self.effect_id,
            "call_id": self.call_id,
            "effect_state": self.effect_state,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "objective_event_kind": None if self.objective is None else self.objective.event_kind,
        }


@dataclass(frozen=True, slots=True)
class ProviderInvocationFact:
    """Settled Provider invocation → ``harness.provider_invocation``."""

    run_id: str
    request_id: str
    model: str | None
    finish_reason: str | None
    tool_call_count: int
    error_code: str | None
    usage: Mapping[str, object] | None

    kind = "provider_invocation"

    @property
    def source_event_id(self) -> str:
        return f"provider:{self.request_id}"

    def public_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "tool_call_count": int(self.tool_call_count),
            "error_code": self.error_code,
            "usage": None if self.usage is None else dict(self.usage),
        }


HarnessFact = ToolInvocationFact | ProviderInvocationFact


@dataclass(frozen=True, slots=True)
class DrainReport:
    run_id: str
    ingested: tuple[str, ...]
    abandoned: tuple[str, ...]


class ReservedFactReader(Protocol):
    """Resolve a still-reserved row from the SDK ledger / coordinator (or ``None``)."""

    async def read_reserved_fact(self, reservation: EvidenceReservation) -> HarnessFact | None: ...


def _disclosure(run_id: str, subject: str, authority_ref: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "subject": subject,
        "recipient": "user_self",
        "recipient_id": subject,
        "intended_audience": "user_self",
        "purpose": "task_execution",
        "source": "authenticated_host",
        "trust": "trusted_authority",
        "generation": "current",
        "authority_ref": authority_ref,
        "reason_codes": ["minimum_necessary"],
    }


class _HostExecutionEvidence:
    """Host-built S1 ExecutionEvidence surface (``to_json`` + attributes + hash).

    The SDK DTO would do the same; building the exact JSON here keeps the
    ingress independent from SDK import order and lets the tombstone /
    replayed facts hash deterministically.
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        for key, value in raw.items():
            setattr(self, key, value)
        self.evidence_hash = canonical_hash(raw)

    def to_json(self) -> dict[str, Any]:
        import json

        return json.loads(json.dumps(self._raw))


def _host_evidence(
    *,
    run_id: str,
    subject: str,
    kind: str,
    event_id: str,
    public_payload: Mapping[str, object],
    refs: list[dict[str, object]],
    idempotency_key: str,
    occurred_at: float,
    authority_ref: str,
) -> _HostExecutionEvidence:
    return _HostExecutionEvidence(
        {
            "schema_version": 1,
            "event_id": event_id,
            "run_id": run_id,
            "subject": subject,
            "kind": kind,
            "public_payload": dict(public_payload),
            "disclosure_context": _disclosure(run_id, subject, authority_ref),
            "evidence_refs": list(refs),
            "idempotency_key": idempotency_key,
            "occurred_at": float(occurred_at),
        }
    )


class ExecutionEvidenceIngress:
    """Imports public S1 facts exactly once and advances contiguous watermarks."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        fault_inject: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = Path(db_path)
        self._store = CanonicalTaskScopeStore(db_path)
        self._fault_inject = fault_inject
        self._clock = clock

    def _fault(self, point: str) -> None:
        if self._fault_inject is not None:
            self._fault_inject(point)

    # ------------------------------------------------------------------ reserve

    async def reserve(
        self,
        *,
        run_id: str,
        task_scope_id: str,
        kind: str,
        source_event_id: str,
        tool_name: str | None = None,
        pre_commit: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None,
    ) -> EvidenceReservation:
        """Allocate the Harness ``source_sequence`` before the physical action.

        ``pre_commit`` (S5b Task 6, EffectGate step-5 re-check) runs inside the
        same ``BEGIN IMMEDIATE`` after the row is written; if it raises, the
        reservation is rolled back and the exception propagates — nothing is
        dispatched and no sequence is consumed.
        """

        await self._store.initialize()
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                reservation = await self.reserve_tx(
                    db,
                    run_id=run_id,
                    task_scope_id=task_scope_id,
                    kind=kind,
                    source_event_id=source_event_id,
                    now=self._clock(),
                    tool_name=tool_name,
                )
                if pre_commit is not None:
                    await pre_commit(db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return reservation

    async def abandon_reservation(
        self,
        source_event_id: str,
        *,
        status: str = "abandoned",
        reason_code: str | None = None,
    ) -> bool:
        """Resolve one still-``reserved`` row right away with a same-kind tombstone.

        Used by the executor when the SDK denied the effect after the
        reservation (``status='rejected'``: nothing was dispatched, not
        material).  Returns ``False`` when the row is absent or already
        resolved.
        """

        reservation = await self.reservation(source_event_id)
        if reservation is None or reservation.status != "reserved":
            return False
        await self._tombstone(reservation, status=status, reason_code=reason_code)
        return True

    async def reserve_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        task_scope_id: str,
        kind: str,
        source_event_id: str,
        now: float,
        tool_name: str | None = None,
    ) -> EvidenceReservation:
        identifier(run_id, "run_id", 512)
        identifier(task_scope_id, "task_scope_id", 512)
        identifier(source_event_id, "source_event_id", 512)
        if kind not in RESERVATION_KINDS:
            raise ValueError("execution_evidence_kind_rejected")
        if tool_name is not None:
            identifier(tool_name, "tool_name", 512)
        existing = await self._reservation_tx(db, source_event_id)
        if existing is not None:
            if (
                existing.run_id != run_id
                or existing.task_scope_id != task_scope_id
                or existing.kind != kind
                or (tool_name is not None and existing.tool_name not in (None, tool_name))
            ):
                raise TaskScopeConflict("execution_reservation_identity_conflict")
            return existing
        scope = await self._store._fetchone(
            db, "SELECT 1 FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
        )
        if scope is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        await self._assert_run_scope_tx(db, run_id=run_id, task_scope_id=task_scope_id)
        watermark = await self._store._fetchone(
            db,
            "SELECT terminal_source_sequence FROM task_scope_run_watermarks WHERE run_id=?",
            (run_id,),
        )
        if watermark is not None and watermark["terminal_source_sequence"] is not None:
            raise TaskScopeConflict("execution_after_terminal_rejected")
        sequence = await self._next_sequence_tx(db, run_id)
        reservation_id = _uuid(f"harness-evidence-reservation:{source_event_id}")
        await db.execute(
            "INSERT INTO harness_evidence_reservations(reservation_id,run_id,task_scope_id,"
            "source_sequence,source_event_id,kind,status,reserved_at,resolved_at,tool_name) "
            "VALUES (?,?,?,?,?,?,'reserved',?,NULL,?)",
            (reservation_id, run_id, task_scope_id, sequence, source_event_id, kind, float(now), tool_name),
        )
        created = await self._reservation_tx(db, source_event_id)
        assert created is not None
        return created

    async def reservation(self, source_event_id: str) -> EvidenceReservation | None:
        async with self._store._connection() as db:
            return await self._reservation_tx(db, source_event_id)

    async def list_reservations(
        self, run_id: str, *, status: str | None = None
    ) -> tuple[EvidenceReservation, ...]:
        async with self._store._connection() as db:
            return await self._list_reservations_tx(db, run_id, status=status)

    async def _list_reservations_tx(
        self, db: aiosqlite.Connection, run_id: str, *, status: str | None = None
    ) -> tuple[EvidenceReservation, ...]:
        if status is None:
            cursor = await db.execute(
                "SELECT * FROM harness_evidence_reservations WHERE run_id=? ORDER BY source_sequence",
                (run_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM harness_evidence_reservations WHERE run_id=? AND status=? "
                "ORDER BY source_sequence",
                (run_id, status),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return tuple(self._reservation_from_row(row) for row in rows)

    async def _reservation_tx(
        self, db: aiosqlite.Connection, source_event_id: str
    ) -> EvidenceReservation | None:
        try:
            row = await self._store._fetchone(
                db,
                "SELECT * FROM harness_evidence_reservations WHERE source_event_id=?",
                (source_event_id,),
            )
        except sqlite3.OperationalError as exc:  # pre-v46 database
            raise TaskScopeConflict("harness_evidence_reservations_schema_missing") from exc
        return None if row is None else self._reservation_from_row(row)

    @staticmethod
    def _reservation_from_row(row: Any) -> EvidenceReservation:
        resolved = row["resolved_at"]
        return EvidenceReservation(
            str(row["reservation_id"]),
            str(row["run_id"]),
            str(row["task_scope_id"]),
            int(row["source_sequence"]),
            str(row["source_event_id"]),
            str(row["kind"]),
            str(row["status"]),
            float(row["reserved_at"]),
            None if resolved is None else float(resolved),
            None if row["tool_name"] is None else str(row["tool_name"]),
        )

    async def _assert_run_scope_tx(
        self, db: aiosqlite.Connection, *, run_id: str, task_scope_id: str
    ) -> None:
        for sql in (
            "SELECT task_scope_id FROM task_scope_run_watermarks WHERE run_id=?",
            "SELECT task_scope_id FROM harness_evidence_reservations WHERE run_id=? LIMIT 1",
        ):
            row = await self._store._fetchone(db, sql, (run_id,))
            if row is not None and str(row["task_scope_id"]) != task_scope_id:
                raise TaskScopeConflict("execution_run_scope_conflict")

    # ----------------------------------------------------------- sequence/scope

    async def next_sequence(self, run_id: str) -> int:
        async with self._store._connection() as db:
            return await self._next_sequence_tx(db, run_id)

    async def _next_sequence_tx(self, db: aiosqlite.Connection, run_id: str) -> int:
        row = await self._store._fetchone(
            db,
            "SELECT MAX(seq) AS head FROM ("
            "SELECT source_sequence AS seq FROM task_scope_execution_ingest_receipts WHERE run_id=? "
            "UNION ALL "
            "SELECT source_sequence AS seq FROM harness_evidence_reservations WHERE run_id=?)",
            (run_id, run_id),
        )
        head = 0 if row is None or row["head"] is None else int(row["head"])
        return head + 1

    async def resolve_run_scope(self, run_id: str) -> RunScopeBinding | None:
        async with self._store._connection() as db:
            return await self.resolve_run_scope_tx(db, run_id)

    async def resolve_run_scope_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> RunScopeBinding | None:
        """Admission scope of one foreground SDK Run (``None`` when unbound)."""

        try:
            row = await self._store._fetchone(
                db,
                "SELECT r.host_run_id,r.task_scope_id,s.subject "
                "FROM foreground_run_sdk_bindings b "
                "JOIN foreground_runs r ON r.host_run_id=b.host_run_id "
                "LEFT JOIN task_scopes s ON s.task_scope_id=r.task_scope_id "
                "WHERE b.sdk_run_id=?",
                (run_id,),
            )
        except sqlite3.OperationalError:  # pre-v41 database: no foreground queue
            return None
        if row is None or row["task_scope_id"] is None or row["subject"] is None:
            return None
        return RunScopeBinding(
            run_id, str(row["host_run_id"]), str(row["task_scope_id"]), str(row["subject"])
        )

    async def scope_subject(self, task_scope_id: str) -> str | None:
        async with self._store._connection() as db:
            row = await self._store._fetchone(
                db, "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
            )
        return None if row is None else str(row["subject"])

    # ------------------------------------------------------------------- ingest

    async def ingest(
        self,
        *,
        task_scope_id: str,
        evidence: object,
        source_sequence: int | None = None,
    ) -> ExecutionIngestReceipt:
        if source_sequence is not None and (
            isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 1
        ):
            raise ValueError("source_sequence_invalid")
        raw, evidence_hash = validate_execution_evidence(evidence)
        refs = validate_refs(raw["evidence_refs"])
        await self._store.initialize()
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                now = self._clock()
                row, watermark = await self._ingest_tx(
                    db,
                    task_scope_id=task_scope_id,
                    raw=raw,
                    evidence_hash=evidence_hash,
                    refs=refs,
                    source_sequence=source_sequence,
                    now=now,
                )
                from deskpet.task_scope.projection_sources import (
                    append_projection_source_tx,
                )

                await append_projection_source_tx(db, task_scope_id, now=now)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return self._receipt(row, watermark)

    async def ingest_ledger_fact_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        kind: str,
        source_event_id: str,
        public_payload: Mapping[str, object],
        occurred_at: float,
    ) -> ExecutionIngestReceipt | None:
        """state.db fact (snapshot / route decision / route tool invocation):
        reserve + ingest inside the caller's ledger transaction.

        Returns ``None`` (no rows) when the Run has no foreground admission
        scope — such Runs carry no TaskScope watermark at all.
        """

        binding = await self.resolve_run_scope_tx(db, run_id)
        if binding is None:
            return None
        reservation = await self.reserve_tx(
            db,
            run_id=run_id,
            task_scope_id=binding.task_scope_id,
            kind=kind,
            source_event_id=source_event_id,
            now=occurred_at,
        )
        if reservation.status != "reserved":
            row = await self._store._fetchone(
                db,
                "SELECT * FROM task_scope_execution_ingest_receipts WHERE source_event_id=?",
                (source_event_id,),
            )
            if row is None:
                raise TaskScopeConflict("execution_reservation_abandoned")
            return self._receipt(row, await self._watermark_tx(db, run_id))
        evidence = _host_evidence(
            run_id=run_id,
            subject=binding.subject,
            kind=kind,
            event_id=source_event_id,
            public_payload=public_payload,
            refs=[],
            idempotency_key=f"harness-fact:{source_event_id}",
            occurred_at=reservation.reserved_at,
            authority_ref=HOST_HARNESS_FACT_AUTHORITY_REF,
        )
        raw, evidence_hash = validate_execution_evidence(evidence)
        row, watermark = await self._ingest_tx(
            db,
            task_scope_id=binding.task_scope_id,
            raw=raw,
            evidence_hash=evidence_hash,
            refs=[],
            source_sequence=None,
            now=occurred_at,
        )
        from deskpet.task_scope.projection_sources import append_projection_source_tx

        await append_projection_source_tx(db, binding.task_scope_id, now=occurred_at)
        return self._receipt(row, watermark)

    async def commit_fact(
        self,
        *,
        task_scope_id: str,
        subject: str,
        fact: HarnessFact,
    ) -> ExecutionIngestReceipt:
        """Objective event + evidence row + Harness import, one transaction (idempotent)."""

        if not isinstance(fact, (ToolInvocationFact, ProviderInvocationFact)):
            raise TypeError("commit_fact requires ToolInvocationFact or ProviderInvocationFact")
        from deskpet.memory.human_memory_program import (
            HumanMemoryProgramConflict,
            HumanMemoryProgramStore,
        )
        from deskpet.memory.human_memory_service import build_host_typed_evidence

        program = HumanMemoryProgramStore(self._db_path)
        objective = getattr(fact, "objective", None)
        primary = None
        if objective is not None:
            reject_private_payload(dict(objective.payload), "objective_event")
            primary = await program.initialize_subject(subject)
        await self._store.initialize()
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                now = self._clock()
                reservation = await self.reserve_tx(
                    db,
                    run_id=fact.run_id,
                    task_scope_id=task_scope_id,
                    kind=fact.kind,
                    source_event_id=fact.source_event_id,
                    now=now,
                )
                refs: list[dict[str, object]] = []
                if objective is not None:
                    assert primary is not None
                    envelope, receipt = build_host_typed_evidence(
                        subject=subject,
                        authority_ref=HOST_OBJECTIVE_AUTHORITY_REF,
                        payload=objective.payload,
                        idempotency_key=f"objective:{fact.source_event_id}",
                        source_ref=f"host-objective:{fact.source_event_id}",
                        run_id=fact.run_id,
                    )
                    try:
                        committed = await program.append_evidence_tx(
                            db,
                            envelope,
                            receipt,
                            primary_conversation_id=primary.primary_conversation_id,
                            committed_at=now,
                        )
                    except HumanMemoryProgramConflict as exc:
                        # Same effect id, different objective payload: the
                        # durable row wins, the replay is rejected loudly.
                        raise TaskScopeConflict("objective_evidence_hash_conflict") from exc
                    refs = [
                        {
                            "evidence_id": committed.evidence_id,
                            "content_hash": committed.envelope_sha256,
                            "ordinal": 1,
                        }
                    ]
                    await self._append_objective_event_tx(
                        db,
                        task_scope_id=task_scope_id,
                        objective=objective,
                        source_event_id=fact.source_event_id,
                        refs=refs,
                        occurred_at=reservation.reserved_at,
                        now=now,
                    )
                evidence = _host_evidence(
                    run_id=fact.run_id,
                    subject=subject,
                    kind=fact.kind,
                    event_id=fact.source_event_id,
                    public_payload=fact.public_payload(),
                    refs=refs,
                    idempotency_key=f"harness-fact:{fact.source_event_id}",
                    occurred_at=reservation.reserved_at,
                    authority_ref=HOST_HARNESS_FACT_AUTHORITY_REF,
                )
                raw, evidence_hash = validate_execution_evidence(evidence)
                row, watermark = await self._ingest_tx(
                    db,
                    task_scope_id=task_scope_id,
                    raw=raw,
                    evidence_hash=evidence_hash,
                    refs=refs,
                    source_sequence=None,
                    now=now,
                )
                from deskpet.task_scope.projection_sources import (
                    append_projection_source_tx,
                )

                await append_projection_source_tx(db, task_scope_id, now=now)
                self._fault("objective-event-commit")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return self._receipt(row, watermark)

    async def _append_objective_event_tx(
        self,
        db: aiosqlite.Connection,
        *,
        task_scope_id: str,
        objective: ObjectiveEventSpec,
        source_event_id: str,
        refs: list[dict[str, object]],
        occurred_at: float,
        now: float,
    ) -> None:
        payload = dict(objective.payload)
        payload_hash = canonical_hash(payload)
        existing = await self._store._event_by_source_tx(db, source_event_id)
        if existing is not None:
            if (
                existing["payload_hash"] != payload_hash
                or existing["task_scope_id"] != task_scope_id
                or existing["event_kind"] != objective.event_kind
            ):
                raise TaskScopeConflict("source_event_payload_conflict")
            return
        event = await self._store._append_event_tx(
            db,
            task_scope_id=task_scope_id,
            event_kind=objective.event_kind,
            source_kind="host",
            source_event_id=source_event_id,
            payload=payload,
            payload_hash=payload_hash,
            occurred_at=float(occurred_at),
            reason_code=None,
        )
        await self._store._link_refs_tx(db, task_scope_id, event.event_id, refs, now)
        await db.execute(
            "UPDATE task_scope_heads SET event_watermark=?,updated_at=? "
            "WHERE task_scope_id=? AND event_watermark<?",
            (event.event_sequence, now, task_scope_id, event.event_sequence),
        )

    async def _ingest_tx(
        self,
        db: aiosqlite.Connection,
        *,
        task_scope_id: str,
        raw: Mapping[str, Any],
        evidence_hash: str,
        refs: list[dict[str, object]],
        source_sequence: int | None,
        now: float,
        reservation_status: str = "ingested",
    ) -> tuple[Any, Any]:
        source_event_id = str(raw["event_id"])
        run_id = str(raw["run_id"])
        existing = await self._store._fetchone(
            db,
            "SELECT * FROM task_scope_execution_ingest_receipts WHERE source_event_id=?",
            (source_event_id,),
        )
        if existing is not None:
            if (
                existing["evidence_hash"] != evidence_hash
                or existing["task_scope_id"] != task_scope_id
                or existing["run_id"] != run_id
                or (
                    source_sequence is not None
                    and int(existing["source_sequence"]) != source_sequence
                )
            ):
                raise TaskScopeConflict("execution_source_event_hash_conflict")
            return existing, await self._watermark_tx(db, run_id)
        scope = await self._store._fetchone(
            db, "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
        )
        if scope is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        if scope["subject"] != raw["subject"]:
            raise ValueError("execution_subject_binding_mismatch")
        reservation = await self._reservation_tx(db, source_event_id)
        if reservation is not None:
            if reservation.run_id != run_id or reservation.task_scope_id != task_scope_id:
                raise TaskScopeConflict("execution_reservation_identity_conflict")
            if reservation.status == "abandoned":
                raise TaskScopeConflict("execution_reservation_abandoned")
            if source_sequence is not None and source_sequence != reservation.source_sequence:
                raise TaskScopeConflict("execution_reservation_sequence_conflict")
            sequence = reservation.source_sequence
        else:
            if source_sequence is None:
                raise ValueError("source_sequence_required")
            sequence = source_sequence
            reserved_by_other = await self._store._fetchone(
                db,
                "SELECT 1 FROM harness_evidence_reservations WHERE run_id=? AND source_sequence=?",
                (run_id, sequence),
            )
            if reserved_by_other is not None:
                raise TaskScopeConflict("execution_source_sequence_conflict")
        collision = await self._store._fetchone(
            db,
            "SELECT source_event_id,evidence_hash FROM task_scope_execution_ingest_receipts "
            "WHERE run_id=? AND source_sequence=?",
            (run_id, sequence),
        )
        if collision is not None:
            raise TaskScopeConflict("execution_source_sequence_conflict")
        existing_watermark = await self._store._fetchone(
            db,
            "SELECT terminal_source_sequence FROM task_scope_run_watermarks WHERE run_id=?",
            (run_id,),
        )
        if (
            existing_watermark is not None
            and existing_watermark["terminal_source_sequence"] is not None
            and sequence > int(existing_watermark["terminal_source_sequence"])
        ):
            raise TaskScopeConflict("execution_after_terminal_rejected")
        if raw["kind"] == "run_terminal":
            later = await self._store._fetchone(
                db,
                "SELECT 1 FROM ("
                "SELECT source_sequence FROM task_scope_execution_ingest_receipts WHERE run_id=? "
                "UNION ALL SELECT source_sequence FROM harness_evidence_reservations WHERE run_id=?"
                ") WHERE source_sequence>? LIMIT 1",
                (run_id, run_id, sequence),
            )
            if later is not None:
                raise TaskScopeConflict("execution_terminal_not_last")
        await self._store._verify_refs_tx(db, refs)
        event = await self._store._append_event_tx(
            db,
            task_scope_id=task_scope_id,
            event_kind=f"harness.{raw['kind']}",
            source_kind="harness",
            source_event_id=f"execution:{source_event_id}",
            payload=raw,
            payload_hash=evidence_hash,
            occurred_at=float(raw["occurred_at"]),
            reason_code=None,
        )
        await self._store._link_refs_tx(db, task_scope_id, event.event_id, refs, now)
        receipt_id = _uuid(f"task-scope-execution-ingest:{source_event_id}")
        await db.execute(
            "INSERT INTO task_scope_execution_ingest_receipts(receipt_id,task_scope_id,run_id,"
            "source_sequence,source_event_id,evidence_hash,event_id,evidence_kind,committed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                receipt_id, task_scope_id, run_id, sequence, source_event_id, evidence_hash,
                event.event_id, raw["kind"], now,
            ),
        )
        if reservation is not None:
            await self._resolve_reservation_tx(db, reservation, status=reservation_status, now=now)
        watermark = await self._advance_watermark_tx(
            db,
            task_scope_id=task_scope_id,
            run_id=run_id,
            terminal_sequence=sequence if raw["kind"] == "run_terminal" else None,
            now=now,
        )
        await db.execute(
            "UPDATE task_scope_heads SET event_watermark=?,updated_at=? "
            "WHERE task_scope_id=? AND event_watermark<?",
            (event.event_sequence, now, task_scope_id, event.event_sequence),
        )
        row = await self._store._fetchone(
            db, "SELECT * FROM task_scope_execution_ingest_receipts WHERE receipt_id=?", (receipt_id,)
        )
        assert row is not None
        return row, watermark

    async def _resolve_reservation_tx(
        self,
        db: aiosqlite.Connection,
        reservation: EvidenceReservation,
        *,
        status: str,
        now: float,
    ) -> None:
        if status not in {"ingested", "abandoned"}:
            raise ValueError("reservation_status_invalid")
        await db.execute(
            "UPDATE harness_evidence_reservations SET status=?,resolved_at=? "
            "WHERE reservation_id=? AND status='reserved'",
            (status, now, reservation.reservation_id),
        )

    # -------------------------------------------------------------------- drain

    async def drain_reservations(
        self,
        run_id: str,
        *,
        fact_reader: ReservedFactReader | None,
    ) -> DrainReport:
        """Resolve every ``reserved`` row of one Run before its ``run_terminal``.

        Readable fact → ``commit_fact`` (same path as the live hook, objective
        event included); otherwise a tombstone of the same kind.  Idempotent:
        a crash between rows is replayed by the next owner.
        """

        await self._store.initialize()
        reserved = await self.list_reservations(run_id, status="reserved")
        ingested: list[str] = []
        abandoned: list[str] = []
        for reservation in reserved:
            fact = None
            if fact_reader is not None:
                fact = await fact_reader.read_reserved_fact(reservation)
            if fact is not None and (
                fact.source_event_id != reservation.source_event_id
                or fact.kind != reservation.kind
                or fact.run_id != reservation.run_id
            ):
                raise TaskScopeConflict("execution_reserved_fact_identity_mismatch")
            if fact is not None:
                subject = await self.scope_subject(reservation.task_scope_id)
                if subject is None:
                    raise TaskScopeNotFound(TaskScopeNotFound.code)
                try:
                    await self.commit_fact(
                        task_scope_id=reservation.task_scope_id, subject=subject, fact=fact
                    )
                except (TaskScopeConflict, TaskScopeProtocolError) as exc:
                    # Task 6 (Task 3 review F-8): a readable fact the Host
                    # rejects deterministically (objective_evidence_hash_conflict,
                    # private-payload rejection …) must not strand the Run —
                    # it is tombstoned as ``rejected_fact`` (same kind, still
                    # material for a PROJECT_EFFECT) so the terminal converges.
                    await self._tombstone(
                        reservation, status="rejected_fact", reason_code=_stable_code(exc)
                    )
                    abandoned.append(reservation.source_event_id)
                else:
                    ingested.append(reservation.source_event_id)
            else:
                await self._tombstone(reservation)
                abandoned.append(reservation.source_event_id)
            self._fault("terminal-watermark")
        return DrainReport(run_id, tuple(ingested), tuple(abandoned))

    async def _tombstone(
        self,
        reservation: EvidenceReservation,
        *,
        status: str = "abandoned",
        reason_code: str | None = None,
    ) -> None:
        if status not in TOMBSTONE_STATUSES:
            raise ValueError("reservation_tombstone_status_invalid")
        subject = await self.scope_subject(reservation.task_scope_id)
        if subject is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        public_payload: dict[str, object] = {
            "status": status,
            "reservation_id": reservation.reservation_id,
        }
        if reason_code:
            public_payload["reason_code"] = str(reason_code)[:128]
        if reservation.kind == "tool_invocation" and reservation.tool_name is not None:
            # Task 2 review F-2: keep the Tool identity so a PROJECT_EFFECT whose
            # outcome is unknown at Run terminal stays *material* (the file may
            # already be written) instead of degrading into a trivial tombstone.
            from deskpet.sdk_adapters.tool_authority import PROJECT_EFFECT_TOOL_NAMES

            public_payload["tool_name"] = reservation.tool_name
            public_payload["effect_class"] = (
                "project_effect"
                if reservation.tool_name in PROJECT_EFFECT_TOOL_NAMES
                else "non_project_effect"
            )
        evidence = _host_evidence(
            run_id=reservation.run_id,
            subject=subject,
            kind=reservation.kind,
            event_id=reservation.source_event_id,
            public_payload=public_payload,
            refs=[],
            idempotency_key=f"abandoned:{reservation.reservation_id}",
            occurred_at=reservation.reserved_at,
            authority_ref=HOST_HARNESS_FACT_AUTHORITY_REF,
        )
        raw, evidence_hash = validate_execution_evidence(evidence)
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                now = self._clock()
                current = await self._reservation_tx(db, reservation.source_event_id)
                if current is None or current.status != "reserved":
                    await db.commit()
                    return
                # The tombstone fills the sequence gap under the reservation's
                # own kind and resolves the row as ``abandoned`` (one commit).
                await self._ingest_tx(
                    db,
                    task_scope_id=reservation.task_scope_id,
                    raw=raw,
                    evidence_hash=evidence_hash,
                    refs=[],
                    source_sequence=None,
                    now=now,
                    reservation_status="abandoned",
                )
                from deskpet.task_scope.projection_sources import (
                    append_projection_source_tx,
                )

                await append_projection_source_tx(db, reservation.task_scope_id, now=now)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    # ----------------------------------------------------------------- terminal

    async def authorize_terminal(self, run_id: str) -> TerminalGateReceipt:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                watermark = await self._watermark_tx(db, run_id)
                terminal = watermark["terminal_source_sequence"]
                durable = int(watermark["durable_source_sequence"])
                if terminal is None or durable < int(terminal):
                    raise TerminalWatermarkPending(TerminalWatermarkPending.code)
                pending = await self._list_reservations_tx(db, run_id, status="reserved")
                if pending:
                    raise TerminalWatermarkPending(TerminalWatermarkPending.code)
                existing = await self._store._fetchone(
                    db, "SELECT * FROM task_scope_terminal_gate_receipts WHERE run_id=?", (run_id,)
                )
                if existing is None:
                    gate_id = _uuid(f"task-scope-terminal-gate:{run_id}")
                    await db.execute(
                        "INSERT INTO task_scope_terminal_gate_receipts(gate_receipt_id,task_scope_id,"
                        "run_id,terminal_source_sequence,durable_source_sequence,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (gate_id, watermark["task_scope_id"], run_id, terminal, durable, self._clock()),
                    )
                    existing = await self._store._fetchone(
                        db, "SELECT * FROM task_scope_terminal_gate_receipts WHERE run_id=?", (run_id,)
                    )
                assert existing is not None
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return TerminalGateReceipt(
            str(existing["gate_receipt_id"]), str(existing["task_scope_id"]), run_id,
            int(existing["terminal_source_sequence"]), int(existing["durable_source_sequence"]),
        )

    async def _advance_watermark_tx(self, db, *, task_scope_id: str, run_id: str, terminal_sequence: int | None, now: float):
        row = await self._store._fetchone(db, "SELECT * FROM task_scope_run_watermarks WHERE run_id=?", (run_id,))
        if row is None:
            await db.execute(
                "INSERT INTO task_scope_run_watermarks(run_id,task_scope_id,durable_source_sequence,terminal_source_sequence,updated_at) VALUES (?,?,0,?,?)",
                (run_id, task_scope_id, terminal_sequence, now),
            )
            durable = 0
            current_terminal = terminal_sequence
        else:
            if row["task_scope_id"] != task_scope_id:
                raise TaskScopeConflict("execution_run_scope_conflict")
            durable = int(row["durable_source_sequence"])
            current_terminal = row["terminal_source_sequence"]
            if terminal_sequence is not None and current_terminal not in (None, terminal_sequence):
                raise TaskScopeConflict("execution_terminal_sequence_conflict")
            current_terminal = terminal_sequence if terminal_sequence is not None else current_terminal
        while True:
            next_row = await self._store._fetchone(
                db,
                "SELECT 1 FROM task_scope_execution_ingest_receipts WHERE run_id=? AND source_sequence=?",
                (run_id, durable + 1),
            )
            if next_row is None:
                break
            durable += 1
        await db.execute(
            "UPDATE task_scope_run_watermarks SET durable_source_sequence=?,terminal_source_sequence=?,updated_at=? WHERE run_id=?",
            (durable, current_terminal, now, run_id),
        )
        return await self._watermark_tx(db, run_id)

    async def _watermark_tx(self, db, run_id: str):
        row = await self._store._fetchone(db, "SELECT * FROM task_scope_run_watermarks WHERE run_id=?", (run_id,))
        if row is None:
            raise TerminalWatermarkPending(TerminalWatermarkPending.code)
        return row

    @staticmethod
    def _receipt(row, watermark) -> ExecutionIngestReceipt:
        terminal = watermark["terminal_source_sequence"]
        return ExecutionIngestReceipt(
            str(row["receipt_id"]), str(row["task_scope_id"]), str(row["run_id"]),
            int(row["source_sequence"]), str(row["source_event_id"]), str(row["evidence_hash"]),
            str(row["event_id"]), int(watermark["durable_source_sequence"]),
            None if terminal is None else int(terminal),
        )


__all__ = [
    "HOST_HARNESS_FACT_AUTHORITY_REF",
    "HOST_OBJECTIVE_AUTHORITY_REF",
    "RESERVATION_KINDS",
    "DrainReport",
    "EvidenceReservation",
    "ExecutionEvidenceIngress",
    "ExecutionIngestReceipt",
    "HarnessFact",
    "ObjectiveEventSpec",
    "ProviderInvocationFact",
    "ReservedFactReader",
    "RunScopeBinding",
    "TerminalGateReceipt",
    "TerminalWatermarkPending",
    "ToolInvocationFact",
]
