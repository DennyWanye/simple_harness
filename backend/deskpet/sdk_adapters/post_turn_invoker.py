# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 3 ``RunBoundInvoker`` — Run-bound post-turn main-model call with a durable
five-state attempt ledger (design-freeze §6; precedent ``execution/provider_invocations.py``).

Consumers: closure fallback (Task 3), memory analysis executor (Task 4).  One
invocation = at most one Provider call, recorded in v46
``post_turn_invocation_attempts`` (+ ``_members``):

* three-key lookup before any call — ``(request_hash, attempt_ordinal)`` hit
  on ``succeeded`` → the same row (``reused``, zero calls); any *member*
  evidence with an open ``handed_off`` / ``unknown(sent_unknown)`` attempt →
  ``blocked`` (zero calls); otherwise a new attempt;
* the ``reserved`` row is inserted in the **same transaction** as the
  foreground lease validation (only the current lease owner may call);
* ``reserved → handed_off → succeeded | failed | unknown``;
* unknown taxonomy: ``not_sent`` (request never entered transport: connection
  refused, binding unrebuildable) may be retried with ``attempt_ordinal + 1``;
  ``sent_unknown`` (sent, no usable response: cancel, read timeout, transport
  drop) is **never** resent; ``sent_confirmed`` only through an injected
  reconciliation observer, which returns the same row;
* after the response the lease is validated again; a lost lease yields
  ``lease_lost`` and the caller must not apply the result.

The success settle (``handed_off → succeeded``) is exposed as a ``*_tx``
helper so the caller commits it in the same transaction as the apply.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from deskpet.execution.foreground_queue import EffectBoundary, ForegroundQueueStore
from deskpet.memory.analysis_lineage import binding_model_config_hash
from deskpet.task_scope.protocol import canonical_json

PURPOSES: frozenset[str] = frozenset({"closure", "analysis"})
NOT_SENT_ERROR_TYPES: tuple[str, ...] = ("ConnectError", "ConnectTimeout", "UnsupportedProtocol", "InvalidURL")
# Task 3 review F-3: the frozen SDK provider re-raises transport failures as
# ``ProviderTransportError(private_cause=SecretRedactor.exception(exc)) from None``
# and the redactor keeps only ``RuntimeError(str(exc))`` — the httpx type is
# erased.  The connect phase (nothing was ever sent) is therefore recognised by
# the exact httpx/httpcore connect-failure messages; anything else stays the
# conservative ``sent_unknown``.  (SDK 0.8 obligation: keep the failure phase
# in the public error — recorded in PROJECT_STATUS.)
NOT_SENT_MESSAGE_MARKERS: tuple[str, ...] = (
    "All connection attempts failed",
    "Connection refused",
    "nodename nor servname provided",
    "Name or service not known",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "No route to host",
    "Address family for hostname not supported",
)


def _not_sent(exc: BaseException) -> str | None:
    """``not_sent`` reason for a transport failure, else ``None`` (→ sent_unknown)."""

    root = _root_cause(exc)
    name = type(root).__name__
    if name in NOT_SENT_ERROR_TYPES:
        return f"provider_not_sent:{name}"
    text = str(root)
    for marker in NOT_SENT_MESSAGE_MARKERS:
        if marker in text:
            return "provider_not_sent:ConnectError"
    return None


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))




@dataclass(frozen=True, slots=True)
class AttemptRow:
    attempt_id: str
    purpose: str
    host_run_id: str
    sdk_run_id: str
    generation: int
    task_scope_id: str | None
    closure_watermark: int | None
    request_hash: str
    attempt_ordinal: int
    evidence_set_key: str
    status: str
    unknown_class: str | None
    provider_id: str
    model_id: str
    model_config_hash: str
    provider_request_id: str | None
    result_hash: str | None
    plan_id: str | None
    reason_code: str | None


def _row(row: Any) -> AttemptRow:
    return AttemptRow(
        str(row["attempt_id"]), str(row["purpose"]), str(row["host_run_id"]), str(row["sdk_run_id"]),
        int(row["generation"]), None if row["task_scope_id"] is None else str(row["task_scope_id"]),
        None if row["closure_watermark"] is None else int(row["closure_watermark"]),
        str(row["request_hash"]), int(row["attempt_ordinal"]), str(row["evidence_set_key"]),
        str(row["status"]), None if row["unknown_class"] is None else str(row["unknown_class"]),
        str(row["provider_id"]), str(row["model_id"]), str(row["model_config_hash"]),
        None if row["provider_request_id"] is None else str(row["provider_request_id"]),
        None if row["result_hash"] is None else str(row["result_hash"]),
        None if row["plan_id"] is None else str(row["plan_id"]),
        None if row["reason_code"] is None else str(row["reason_code"]),
    )


@dataclass(frozen=True, slots=True)
class InvocationOutcome:
    status: str  # succeeded | reused | blocked | failed | unknown | lease_lost
    attempt_id: str | None
    attempt_ordinal: int | None
    response: Any = None
    reason_code: str | None = None
    unknown_class: str | None = None
    provider_calls: int = 0
    # ``lease_lost``: the Provider answered but the caller must not apply it;
    # the response is exposed here only so the caller may persist it as a
    # ledger fact (Task 4 analysis replay), never through ``response``.
    ledger_response: Any = None


class LeaseFence(Protocol):
    async def reserve_attempt(self, row: Mapping[str, Any], members: Sequence[tuple[str, str, str]]) -> None: ...

    async def revalidate(self) -> None: ...


class ForegroundLeaseFence:
    """Lease fence over ``ForegroundQueueStore`` for one (Run, owner, generation)."""

    def __init__(
        self, store: ForegroundQueueStore, *, host_run_id: str, sdk_run_id: str, owner_id: str, generation: int
    ) -> None:
        self._store = store
        self.host_run_id = host_run_id
        self.sdk_run_id = sdk_run_id
        self.owner_id = owner_id
        self.generation = int(generation)

    async def reserve_attempt(self, row: Mapping[str, Any], members: Sequence[tuple[str, str, str]]) -> None:
        await self._store.reserve_post_turn_attempt(
            host_run_id=self.host_run_id,
            sdk_run_id=self.sdk_run_id,
            owner_id=self.owner_id,
            generation=self.generation,
            attempt=row,
            members=members,
        )

    async def revalidate(self) -> None:
        await self._store.authorize_effect(
            host_run_id=self.host_run_id,
            sdk_run_id=self.sdk_run_id,
            owner_id=self.owner_id,
            generation=self.generation,
            boundary=EffectBoundary.CLOSURE,
        )

    async def reserve_attempt_with_input(self, row, members, observer):
        await self._store.reserve_post_turn_attempt(
            host_run_id=self.host_run_id, sdk_run_id=self.sdk_run_id, owner_id=self.owner_id,
            generation=self.generation, attempt=row, members=members, input_observer_tx=observer)


class RunBoundInvoker:
    def __init__(
        self,
        db_path: str | Path,
        *,
        fence: LeaseFence,
        adapter_factory: Callable[[Mapping[str, Any]], Any],
        clock: Callable[[], float] = time.time,
        fault_inject: Callable[[str], None] | None = None,
        reconciliation_observer: Callable[[AttemptRow], Any] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._fence = fence
        self.adapter_factory = adapter_factory
        self._clock = clock
        self._fault_inject = fault_inject
        # Test-only injection: confirms a sent_unknown attempt (``sent_confirmed``).
        self.reconciliation_observer = reconciliation_observer

    # -- db helpers --------------------------------------------------------

    def transaction(self):  # type: ignore[no-untyped-def]
        return _Transaction(self._db_path)

    async def _latest_attempt(self, db: aiosqlite.Connection, request_hash: str) -> AttemptRow | None:
        cursor = await db.execute(
            "SELECT * FROM post_turn_invocation_attempts WHERE request_hash=? ORDER BY attempt_ordinal DESC LIMIT 1",
            (request_hash,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return None if row is None else _row(row)

    async def _durable_evidence_set_result(
        self, db: aiosqlite.Connection, purpose: str, evidence_set_key: str
    ) -> tuple[AttemptRow, Any] | None:
        """Attempt-independent reuse (design-freeze §6 / AC-2③): a settled attempt over the **same
        evidence set** whose durable result carries the Provider response → replayable with 0 calls."""

        cursor = await db.execute(
            "SELECT * FROM post_turn_invocation_attempts WHERE purpose=? AND evidence_set_key=? "
            "AND result_envelope_json IS NOT NULL AND (status='succeeded' OR (status='unknown' AND unknown_class='sent_confirmed')) "
            "ORDER BY settled_at DESC, attempt_ordinal DESC LIMIT 1",
            (purpose, evidence_set_key),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        response = durable_response(row["result_envelope_json"])
        if response is None:
            return None
        return _row(row), response

    async def _open_member_attempt(
        self, db: aiosqlite.Connection, members: Sequence[tuple[str, str, str]], *, exclude: str | None
    ) -> AttemptRow | None:
        for subject, run_id, evidence_id in members:
            cursor = await db.execute(
                "SELECT a.* FROM post_turn_invocation_members m JOIN post_turn_invocation_attempts a "
                "ON a.attempt_id=m.attempt_id WHERE m.subject=? AND m.run_id=? AND m.evidence_id=? "
                "AND (a.status='handed_off' OR (a.status='unknown' AND a.unknown_class='sent_unknown')) "
                "ORDER BY a.reserved_at LIMIT 1",
                (subject, run_id, evidence_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None and str(row["attempt_id"]) != exclude:
                return _row(row)
        return None

    async def _update(self, attempt_id: str, sql: str, params: tuple[object, ...]) -> int:
        """One guarded UPDATE on the attempt row; returns the affected row count.

        Task 3 review F-4: every state transition is conditional on the current
        status, so ``0`` means another owner already moved the row — the caller
        must not act (in particular, never call the Provider) on a row it no
        longer owns.
        """

        async with self.transaction() as db:
            cursor = await db.execute(sql, (*params, attempt_id))
            return int(cursor.rowcount)

    async def _settle_failed(
        self, attempt_id: str, *, unknown_class: str | None, reason: str,
        expected_status: str = "handed_off",
    ) -> int:
        return await self._update(
            attempt_id,
            "UPDATE post_turn_invocation_attempts SET status='failed',unknown_class=?,reason_code=?,settled_at=? "
            "WHERE status=? AND attempt_id=?",
            (unknown_class, reason, self._clock(), expected_status),
        )

    async def _settle_unknown(self, attempt_id: str, *, reason: str) -> None:
        await self._update(
            attempt_id,
            "UPDATE post_turn_invocation_attempts SET status='unknown',unknown_class='sent_unknown',reason_code=?,"
            "settled_at=? WHERE attempt_id=? AND status='handed_off'",
            (reason, self._clock()),
        )

    async def settle_succeeded_tx(
        self,
        db: aiosqlite.Connection,
        attempt_id: str,
        *,
        response: Any,
        plan_id: str | None,
        result_hash: str | None = None,
        result_envelope_json: str | None = None,
    ) -> None:
        """``handed_off → succeeded`` inside the caller's apply transaction.

        Task 4 (analysis): ``result_hash`` is the Memory ``MemoryAnalysisResult.result_hash``
        and ``result_envelope_json`` the durable delivery envelope, so a later reclaim
        returns the same delivery without a Provider call.
        """

        provider_request_id = getattr(response, "provider_request_id", None)
        await db.execute(
            "UPDATE post_turn_invocation_attempts SET status='succeeded',result_hash=?,provider_request_id=?,"
            "plan_id=?,settled_at=?,result_envelope_json=? WHERE attempt_id=? AND status='handed_off'",
            (
                result_hash or _response_hash(response),
                None if provider_request_id is None else str(provider_request_id),
                plan_id,
                self._clock(),
                result_envelope_json,
                attempt_id,
            ),
        )

    async def settle_succeeded(
        self,
        attempt_id: str,
        *,
        response: Any,
        plan_id: str | None,
        result_hash: str | None = None,
        result_envelope_json: str | None = None,
    ) -> None:
        async with self.transaction() as db:
            await self.settle_succeeded_tx(
                db, attempt_id, response=response, plan_id=plan_id,
                result_hash=result_hash, result_envelope_json=result_envelope_json,
            )

    async def read_attempt(self, attempt_id: str) -> tuple[AttemptRow, str | None] | None:
        """One durable attempt row plus its result envelope JSON (analysis replay)."""

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM post_turn_invocation_attempts WHERE attempt_id=?", (attempt_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return None
        envelope = row["result_envelope_json"]
        return _row(row), None if envelope is None else str(envelope)

    # -- invoke --------------------------------------------------------------

    async def invoke(
        self,
        *,
        purpose: str,
        host_run_id: str,
        sdk_run_id: str,
        generation: int,
        task_scope_id: str | None,
        closure_watermark: int | None,
        request_hash: str,
        evidence_set_key: str,
        members: Sequence[tuple[str, str, str]],
        binding_record: Mapping[str, Any] | None,
        build_request: Callable[[AttemptRow], Any],
        plan_id: str | None = None,
        deadline_seconds: float | None = 60.0,
        prepare_attempt=None,
    ) -> InvocationOutcome:
        if purpose not in PURPOSES:
            raise ValueError("post_turn_purpose_invalid")
        members = tuple((str(s), str(r), str(e)) for s, r, e in members)
        async with self.transaction() as db:
            latest = await self._latest_attempt(db, request_hash)
            open_member = await self._open_member_attempt(
                db, members, exclude=None if latest is None else latest.attempt_id
            )
            durable = await self._durable_evidence_set_result(db, purpose, evidence_set_key)
        reuse_response: Any = None
        reuse_source: str | None = None
        if latest is not None:
            if latest.status == "succeeded":
                return InvocationOutcome("reused", latest.attempt_id, latest.attempt_ordinal)
            if latest.status == "handed_off" or (latest.status == "unknown" and latest.unknown_class == "sent_unknown"):
                observed = await self._observe(latest)
                if observed is not None:
                    return observed
                return InvocationOutcome(
                    "blocked", latest.attempt_id, latest.attempt_ordinal,
                    reason_code=f"{purpose}_attempt_unknown", unknown_class=latest.unknown_class,
                )
            if latest.status == "unknown" and latest.unknown_class == "sent_confirmed":
                return InvocationOutcome("blocked", latest.attempt_id, latest.attempt_ordinal, reason_code=f"{purpose}_attempt_confirmed")
            if latest.status == "reserved":
                # A sender can hand off after the recovery read. Only winning
                # the reserved CAS proves nothing was sent and permits retry.
                moved = await self._settle_failed(
                    latest.attempt_id, unknown_class="not_sent", reason="reserved_abandoned",
                    expected_status="reserved",
                )
                if moved != 1:
                    return InvocationOutcome(
                        "blocked", latest.attempt_id, latest.attempt_ordinal,
                        reason_code=f"{purpose}_attempt_unknown",
                    )
            elif latest.status == "failed" and latest.unknown_class != "not_sent":
                return InvocationOutcome(
                    "blocked", latest.attempt_id, latest.attempt_ordinal, reason_code=f"{purpose}_attempt_failed"
                )
        if open_member is not None:
            observed = await self._observe(open_member)
            if observed is None:
                return InvocationOutcome(
                    "blocked", open_member.attempt_id, open_member.attempt_ordinal,
                    reason_code=f"{purpose}_attempt_unknown", unknown_class=open_member.unknown_class,
                )
            # The injected observer confirmed the in-flight member attempt: reuse
            # its response for this request (0 calls) — never resend.
            reuse_response, reuse_source = observed.response, open_member.attempt_id
        elif durable is not None and (latest is None or latest.status == "failed"):
            # Same evidence set already analysed by a settled attempt (a Memory
            # retry after a stale lease / new batch id): replay its response.
            reuse_response, reuse_source = durable[1], durable[0].attempt_id
        ordinal = 1 if latest is None else latest.attempt_ordinal + 1
        record = dict(binding_record or {})
        provider_id = str(record.get("provider_id") or "").strip()
        model_id = str(record.get("model_id") or "").strip()
        if not provider_id or not model_id:
            return InvocationOutcome(
                "failed", None, None, reason_code="binding_unrebuildable:missing", unknown_class="not_sent"
            )
        adapter = None
        adapter_error: str | None = None
        try:
            adapter = self.adapter_factory(record)
        except Exception as exc:  # noqa: BLE001 - not_sent taxonomy
            adapter_error = f"binding_unrebuildable:{type(exc).__name__}"
        endpoint = getattr(getattr(adapter, "target", None), "endpoint_identity", None)
        attempt_id = _uuid(f"post-turn-attempt:{request_hash}:{ordinal}")
        row = {
            "attempt_id": attempt_id,
            "purpose": purpose,
            "host_run_id": host_run_id,
            "sdk_run_id": sdk_run_id,
            "generation": int(generation),
            "task_scope_id": task_scope_id,
            "closure_watermark": closure_watermark,
            "request_hash": request_hash,
            "attempt_ordinal": ordinal,
            "evidence_set_key": evidence_set_key,
            "provider_id": provider_id,
            "model_id": model_id,
            "model_config_hash": binding_model_config_hash(record, endpoint_identity=endpoint),
            "plan_id": plan_id,
            "reserved_at": self._clock(),
        }
        # reserved row + lease validation in one store transaction (owner only).
        if prepare_attempt is not None and reuse_response is None and adapter is not None:
            # Existing success/UNKNOWN/reconciled results above never need a new
            # carrier. Only a genuinely new physical attempt prepares sources.
            members, observer = await prepare_attempt(row)
            await self._fence.reserve_attempt_with_input(row, members, observer)
        else:
            await self._fence.reserve_attempt(row, members)
        self._fault("attempt-reserved")
        if adapter is None:
            await self._settle_failed(
                attempt_id, unknown_class="not_sent", reason=str(adapter_error), expected_status="reserved",
            )
            return InvocationOutcome("failed", attempt_id, ordinal, reason_code=adapter_error, unknown_class="not_sent")
        attempt = AttemptRow(
            attempt_id, purpose, host_run_id, sdk_run_id, int(generation), task_scope_id, closure_watermark,
            request_hash, ordinal, evidence_set_key, "reserved", None, provider_id, model_id,
            str(row["model_config_hash"]), None, None, plan_id, None,
        )
        if reuse_response is not None:
            # Zero-call replay: the row still walks reserved → handed_off so the
            # caller settles it (with its durable result) exactly like a fresh call.
            moved = await self._update(
                attempt_id,
                "UPDATE post_turn_invocation_attempts SET status='handed_off',handed_off_at=?,reason_code=? "
                "WHERE attempt_id=? AND status='reserved'",
                (self._clock(), f"reused:{reuse_source}"),
            )
            if moved != 1:
                return InvocationOutcome(
                    "lease_lost", attempt_id, ordinal, reason_code="attempt_reserved_row_lost", provider_calls=0,
                )
            return InvocationOutcome(
                "succeeded", attempt_id, ordinal, response=reuse_response,
                reason_code=f"reused:{reuse_source}", provider_calls=0,
            )
        request = build_request(attempt)
        moved = await self._update(
            attempt_id,
            "UPDATE post_turn_invocation_attempts SET status='handed_off',handed_off_at=? WHERE attempt_id=? AND status='reserved'",
            (self._clock(),),
        )
        if moved != 1:
            # Task 3 review F-4: a new owner already reconciled this reserved row
            # (reserved → failed(not_sent) + its own attempt).  Calling the
            # Provider now would be a call no ledger row represents.
            return InvocationOutcome(
                "lease_lost", attempt_id, ordinal, reason_code="attempt_reserved_row_lost", provider_calls=0,
            )
        self._fault("attempt-handed-off")
        from simple_harness.providers import CancelToken
        from simple_harness.providers.errors import (
            ProviderCancelledError,
            ProviderError,
            ProviderTimeoutError,
            ProviderTransportError,
        )

        try:
            coroutine = adapter.invoke(request, cancel=CancelToken())
            response = await (asyncio.wait_for(coroutine, timeout=deadline_seconds) if deadline_seconds else coroutine)
        except asyncio.CancelledError:
            # Task 3 review F-6: the request may have been sent, so the ledger
            # settles ``unknown(sent_unknown)`` (never resent) — but task
            # cancellation must keep propagating (Python ≥3.11 cooperative
            # cancel); the caller must not continue with durable writes.
            await self._settle_unknown(attempt_id, reason=f"{purpose}_attempt_unknown")
            raise
        except ProviderCancelledError:
            await self._settle_unknown(attempt_id, reason=f"{purpose}_attempt_unknown")
            return InvocationOutcome(
                "unknown", attempt_id, ordinal, reason_code=f"{purpose}_attempt_unknown",
                unknown_class="sent_unknown", provider_calls=1,
            )
        except (TimeoutError, ProviderTimeoutError):
            await self._settle_unknown(attempt_id, reason=f"{purpose}_timeout")
            return InvocationOutcome(
                "unknown", attempt_id, ordinal, reason_code=f"{purpose}_timeout",
                unknown_class="sent_unknown", provider_calls=1,
            )
        except ProviderTransportError as exc:
            reason = _not_sent(exc)
            if reason is not None:
                await self._settle_failed(attempt_id, unknown_class="not_sent", reason=reason)
                return InvocationOutcome("failed", attempt_id, ordinal, reason_code=reason, unknown_class="not_sent", provider_calls=1)
            await self._settle_unknown(attempt_id, reason=f"{purpose}_attempt_unknown")
            return InvocationOutcome(
                "unknown", attempt_id, ordinal, reason_code=f"{purpose}_attempt_unknown",
                unknown_class="sent_unknown", provider_calls=1,
            )
        except ProviderError as exc:
            from deskpet.execution.closure_request_guard import ClosurePhysicalRequestRejected
            if purpose == "closure" and isinstance(exc, ClosurePhysicalRequestRejected):
                reason = "closure_request_disclosure_rejected"
                await self._settle_failed(attempt_id, unknown_class="not_sent", reason=reason)
                return InvocationOutcome("failed", attempt_id, ordinal, reason_code=reason,
                                         unknown_class="not_sent", provider_calls=0)
            reason = f"provider_{_error_code(exc)}"
            await self._settle_failed(attempt_id, unknown_class=None, reason=reason)
            return InvocationOutcome("failed", attempt_id, ordinal, reason_code=reason, provider_calls=1)
        except Exception as exc:  # noqa: BLE001 - transport taxonomy
            reason = _not_sent(exc)
            if reason is not None:
                await self._settle_failed(attempt_id, unknown_class="not_sent", reason=reason)
                return InvocationOutcome("failed", attempt_id, ordinal, reason_code=reason, unknown_class="not_sent", provider_calls=1)
            await self._settle_unknown(attempt_id, reason=f"{purpose}_attempt_unknown")
            return InvocationOutcome(
                "unknown", attempt_id, ordinal, reason_code=f"{purpose}_attempt_unknown",
                unknown_class="sent_unknown", provider_calls=1,
            )
        # Response received: the lease must still be ours before any apply.
        try:
            await self._fence.revalidate()
        except Exception as exc:  # noqa: BLE001 - lease errors carry a stable code
            await self.settle_succeeded(attempt_id, response=response, plan_id=plan_id)
            # The response is a ledger fact the caller may persist (never apply).
            return InvocationOutcome(
                "lease_lost", attempt_id, ordinal,
                reason_code=str(getattr(exc, "code", type(exc).__name__)), provider_calls=1,
                ledger_response=response,
            )
        return InvocationOutcome("succeeded", attempt_id, ordinal, response=response, provider_calls=1)

    async def _observe(self, latest: AttemptRow) -> InvocationOutcome | None:
        """``sent_confirmed``: only an injected observer over a still-open ``handed_off`` row.

        A row already settled ``unknown(sent_unknown)`` is immutable (v46 monotonic
        guard), so confirmation is only possible before the Host settled it.
        """

        observer = self.reconciliation_observer
        if observer is None or latest.status != "handed_off":
            return None
        response = await observer(latest)
        if response is None:
            return None
        await self._update(
            latest.attempt_id,
            "UPDATE post_turn_invocation_attempts SET status='unknown',unknown_class='sent_confirmed',"
            "result_hash=?,reason_code='reconciliation_observer',settled_at=? WHERE attempt_id=? AND status='handed_off'",
            (_response_hash(response), self._clock()),
        )
        return InvocationOutcome(
            "succeeded", latest.attempt_id, latest.attempt_ordinal, response=response,
            unknown_class="sent_confirmed", provider_calls=0,
        )

    def _fault(self, point: str) -> None:
        if self._fault_inject is not None:
            self._fault_inject(point)


class _Transaction:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx

        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        self._db = db
        return db

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        db = self._db
        assert db is not None
        try:
            if exc_type is None:
                await db.commit()
            else:
                await db.rollback()
        finally:
            await db.close()


DURABLE_RESULT_SCHEMA = "post-turn-durable-result/v1"


def durable_result_json(*, response: Any, envelope_json: Mapping[str, Any] | None) -> str:
    """``result_envelope_json`` payload: the public Provider response (+ the delivery envelope)."""

    from simple_harness.execution.provider_invocations import provider_response_json

    return canonical_json(
        {
            "schema_version": DURABLE_RESULT_SCHEMA,
            "response": json.loads(canonical_json(provider_response_json(response))),
            "envelope": None if envelope_json is None else json.loads(canonical_json(dict(envelope_json))),
        }
    )


def durable_response(raw: object) -> Any | None:
    """Provider response stored by :func:`durable_result_json` (``None`` if absent/legacy)."""

    if raw is None:
        return None
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema_version") != DURABLE_RESULT_SCHEMA:
        return None
    response = payload.get("response")
    if not isinstance(response, Mapping):
        return None
    from simple_harness.execution.provider_invocations import (
        provider_response_from_json,
    )

    try:
        return provider_response_from_json(response)
    except Exception:  # noqa: BLE001 - a malformed durable copy is treated as absent
        return None


def durable_envelope_json(raw: object) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema_version") != DURABLE_RESULT_SCHEMA:
        return None
    envelope = payload.get("envelope")
    return envelope if isinstance(envelope, Mapping) else None


def _response_hash(response: Any) -> str:
    message = getattr(response, "message", None)
    calls = tuple(getattr(response, "tool_calls", ()) or ())
    payload = {
        "content": str(getattr(message, "content", "")),
        "tool_calls": [
            {"name": call.name, "arguments": json.loads(canonical_json(_thaw(call.arguments)))}
            for call in calls
        ],
        "finish_reason": getattr(response, "finish_reason", None),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _thaw(value: Any) -> Any:
    from simple_harness import thaw_json

    return thaw_json(value)


def _root_cause(exc: BaseException) -> BaseException:
    """Innermost cause: ``__cause__`` chain first, then the SDK's private cause.

    Task 3 review F-3: the real SDK provider wraps transport failures as
    ``ProviderTransportError(private_cause=…) from None`` — ``__cause__`` is
    deliberately severed, the httpx exception only survives as the private
    cause.  Without it ``not_sent`` (connection refused / DNS) could never be
    classified in production and every transport failure became
    ``sent_unknown``.
    """

    seen: set[int] = set()
    root = exc
    while id(root) not in seen:
        seen.add(id(root))
        next_cause = root.__cause__
        if next_cause is None:
            next_cause = getattr(root, "_private_cause", None)
        if not isinstance(next_cause, BaseException):
            break
        root = next_cause
    return root


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    return str(code) if code else type(exc).__name__


__all__ = [
    "DURABLE_RESULT_SCHEMA",
    "AttemptRow",
    "ForegroundLeaseFence",
    "InvocationOutcome",
    "LeaseFence",
    "RunBoundInvoker",
    "binding_model_config_hash",
    "durable_envelope_json",
    "durable_response",
    "durable_result_json",
]
