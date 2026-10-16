# SPDX-License-Identifier: BUSL-1.1

"""SDK terminal records, snapshots, follow-up attempts and effect authorization."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import aiosqlite

from deskpet.memory.analysis_lineage import analysis_lineage_payload
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, digest, identifier

from .core import ForegroundQueueCore
from .support import (
    TERMINAL_STATES,
    ForegroundQueueError,
    ForegroundRunSnapshot,
    RunState,
    TerminalReceipt,
    TurnState,
    _clock_value,
    _uuid,
)


class EffectBoundary(StrEnum):
    """Externally visible Runtime boundary admitted by the current Host lease."""

    SDK_START = "sdk_start"
    SDK_CONTROL = "sdk_control"
    TOOL = "tool"
    # S5b Task 3: the post-turn closure Provider call (after the SDK terminal,
    # before the Host terminal) is an external side effect of the current lease
    # owner only; same admitted states as TOOL.
    CLOSURE = "closure"


_EFFECT_BOUNDARY_ALLOWED_STATES: dict[EffectBoundary, frozenset[str]] = {
    EffectBoundary.SDK_START: frozenset({"CLAIMED"}),
    EffectBoundary.SDK_CONTROL: frozenset(
        {"PAUSE_REQUESTED", "STOP_REQUESTED", "CANCEL_REQUESTED"}
    ),
    EffectBoundary.CLOSURE: frozenset(
        {
            "RUNNING",
            "PAUSE_REQUESTED",
            "PAUSED",
            "STOP_REQUESTED",
            "CANCEL_REQUESTED",
        }
    ),
    # The TOOL fence exists to stop STALE WORKERS (lease/generation drift) and
    # terminal runs from producing external side effects.  Control-transition
    # states stay admitted: SDK 0.7 cannot halt a run mid-tool, so rejecting a
    # dispatch while the head sits in *_REQUESTED/PAUSED would convert a clean
    # pause/stop into a FAILED run (the SDK kernel terminalizes on the raised
    # admission error).  Terminal states are rejected by _validate_lease_tx.
    EffectBoundary.TOOL: frozenset(
        {
            "RUNNING",
            "PAUSE_REQUESTED",
            "PAUSED",
            "STOP_REQUESTED",
            "CANCEL_REQUESTED",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class EffectAdmissionReceipt:
    host_run_id: str
    sdk_run_id: str
    generation: int
    snapshot_hash: str


class ObservationMixin(ForegroundQueueCore):
    """SDK terminal records, snapshots, follow-up attempts and effect authorization."""

    __slots__ = ()

    async def record_sdk_terminal(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        terminal_state: RunState | str,
        sdk_event_id: str,
        sdk_event_hash: str,
        idempotency_key: str,
        run_binding: Mapping[str, object] | None = None,
        endpoint_identity: str | None = None,
        outbox_dead_letter_reason: str | None = None,
        terminal_observer_tx=None,
    ) -> TerminalReceipt:
        """Host terminal commit: three watermarks, run/turn transitions and — S5b Task 4 —
        the ``memory_ingestion_outbox`` row (+ evidence links) in the **same transaction**.

        ``run_binding`` is the durable ``SdkRunBindingV1`` record of the Run (the same
        record the post-turn invoker rebuilds the Provider adapter from); with it the
        outbox row carries ``model_config_hash`` / ``analysis_lineage_json`` derived from
        the binding.  ``None`` (unit callers without a binding) writes no outbox row —
        unless ``outbox_dead_letter_reason`` is given (S5b Task 6, Task 4 review F-5):
        the production runtime could not read the binding, so the terminal still
        commits and the turn's outbox row is written directly as ``dead_letter`` with
        that reason (raw evidence kept, FIFO never stalls on the Memory side).
        """

        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_event_id = identifier(sdk_event_id, "sdk_event_id", 512)
        sdk_event_hash = digest(sdk_event_hash, "sdk_event_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ForegroundQueueError("foreground_generation_invalid")
        try:
            terminal = RunState(terminal_state)
        except ValueError as exc:
            raise ForegroundQueueError("foreground_terminal_state_invalid") from exc
        if terminal.value not in TERMINAL_STATES:
            raise ForegroundQueueError("foreground_terminal_state_invalid")
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_terminal_receipts WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if (
                        existing["sdk_run_id"] != sdk_run_id
                        or existing["terminal_state"] != terminal.value
                        or int(existing["generation"]) != generation
                        or existing["sdk_event_id"] != sdk_event_id
                        or existing["sdk_event_hash"] != sdk_event_hash
                    ):
                        raise ForegroundQueueError("foreground_terminal_immutable")
                    if terminal_observer_tx is not None:
                        await terminal_observer_tx(
                            db, host_run_id=host_run_id, sdk_run_id=sdk_run_id
                        )
                    await db.commit()
                    return self._terminal_receipt(existing)
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
                run = await self._run_tx(db, host_run_id)
                # 按需导入：循环依赖（foreground_queue ↔ evidence_ingress）
                from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

                # 按需导入：循环依赖（foreground_queue ↔ primary_history）
                from deskpet.execution.primary_history import terminal_observation_tx

                effective_scope = await ExecutionEvidenceIngress(
                    self._db_path
                ).resolve_run_scope_tx(db, sdk_run_id)
                observed = await terminal_observation_tx(
                    db,
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    subject=str(head["subject"]),
                )
                if effective_scope is None:
                    residual = await self._fetchone(
                        db,
                        "SELECT source_event_id FROM task_scope_execution_ingest_receipts WHERE run_id=? "
                        "UNION ALL SELECT source_event_id FROM harness_evidence_reservations WHERE run_id=? LIMIT 1",
                        (sdk_run_id, sdk_run_id),
                    )
                    if residual is not None or run["task_scope_id"] is not None:
                        raise ForegroundQueueError("foreground_terminal_scope_mismatch")
                    if observed is None:
                        raise ForegroundQueueError("foreground_terminal_sdk_evidence_missing")
                    observation, public_payload = observed
                    if (
                        observation["evidence_id"] != sdk_event_id
                        or observation["envelope_sha256"] != sdk_event_hash
                        or public_payload.get("kind") != "primary_run_terminal"
                    ):
                        raise ForegroundQueueError("foreground_terminal_sdk_evidence_invalid")
                    gate = None
                else:
                    receipt = await self._fetchone(
                        db,
                        "SELECT r.task_scope_id,r.source_sequence,r.event_id,e.payload_json "
                        "FROM task_scope_execution_ingest_receipts r "
                        "JOIN task_scope_events e ON e.event_id=r.event_id "
                        "AND e.task_scope_id=r.task_scope_id AND e.payload_hash=r.evidence_hash "
                        "WHERE r.source_event_id=? AND r.run_id=? AND r.evidence_hash=? "
                        "AND r.evidence_kind='run_terminal'",
                        (sdk_event_id, sdk_run_id, sdk_event_hash),
                    )
                    if receipt is None:
                        raise ForegroundQueueError("foreground_terminal_sdk_evidence_missing")
                    if receipt["task_scope_id"] != effective_scope.task_scope_id:
                        raise ForegroundQueueError("foreground_terminal_scope_mismatch")
                    gate = await self._fetchone(
                        db,
                        "SELECT g.gate_receipt_id,g.terminal_source_sequence,"
                        "g.durable_source_sequence,w.durable_source_sequence AS current_durable_sequence,"
                        "w.terminal_source_sequence AS current_terminal_sequence "
                        "FROM task_scope_terminal_gate_receipts g "
                        "JOIN task_scope_run_watermarks w ON w.run_id=g.run_id "
                        "AND w.task_scope_id=g.task_scope_id "
                        "WHERE g.run_id=? AND g.task_scope_id=? "
                        "AND g.terminal_source_sequence=? "
                        "AND g.durable_source_sequence>=g.terminal_source_sequence "
                        "AND w.terminal_source_sequence=g.terminal_source_sequence "
                        "AND w.durable_source_sequence>=g.terminal_source_sequence",
                        (sdk_run_id, receipt["task_scope_id"], receipt["source_sequence"]),
                    )
                    if gate is None:
                        raise ForegroundQueueError("foreground_terminal_gate_pending")
                    # S5b Task 3 — third watermark (semantic closure): every material
                    # event of the admission scope must be covered by a closing
                    # receipt, or this Run must have recorded a `pending` receipt.
                    # Applies to COMPLETED / FAILED / CANCELLED / STOPPED alike.
                    # 按需导入：导入隔离
                    from deskpet.execution.semantic_closure import closure_coverage_tx

                    coverage = await closure_coverage_tx(
                        db, task_scope_id=str(receipt["task_scope_id"]), sdk_run_id=sdk_run_id
                    )
                    if not coverage.satisfied:
                        raise ForegroundQueueError("foreground_terminal_closure_pending")
                    try:
                        evidence = json.loads(str(receipt["payload_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise ForegroundQueueError(
                            "foreground_terminal_sdk_evidence_invalid"
                        ) from exc
                    if (
                        not isinstance(evidence, dict)
                        or evidence.get("event_id") != sdk_event_id
                        or evidence.get("run_id") != sdk_run_id
                        or evidence.get("subject") != head["subject"]
                        or evidence.get("kind") != "run_terminal"
                    ):
                        raise ForegroundQueueError("foreground_terminal_sdk_evidence_invalid")
                    public_payload = evidence.get("public_payload")
                    if not isinstance(public_payload, dict):
                        raise ForegroundQueueError("foreground_terminal_sdk_evidence_invalid")
                payload_generation = public_payload.get("generation")
                if (
                    isinstance(payload_generation, bool)
                    or not isinstance(payload_generation, int)
                    or payload_generation < 1
                    or payload_generation > generation
                ):
                    raise ForegroundQueueError("foreground_terminal_generation_mismatch")
                payload_terminal = public_payload.get("terminal_state")
                if (
                    not isinstance(payload_terminal, str)
                    or payload_terminal.upper() != terminal.value
                ):
                    raise ForegroundQueueError("foreground_terminal_state_mismatch")
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=str(head["current_state"]),
                    to_state=terminal.value,
                    generation=generation,
                    owner_id=owner_id,
                    sdk_event_id=sdk_event_id,
                    idempotency_key=idempotency_key,
                    causal_evidence_ref=sdk_event_id,
                    causal_evidence_hash=sdk_event_hash,
                    recorded_at=now,
                )
                terminal_id = _uuid(f"foreground-terminal:{host_run_id}")
                terminal_payload = {
                    "schema_version": 1,
                    "terminal_receipt_id": terminal_id,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "terminal_state": terminal.value,
                    "generation": generation,
                    "sdk_event_id": sdk_event_id,
                    "sdk_event_hash": sdk_event_hash,
                    "terminal_authority_kind": "primary_runtime_observation"
                    if gate is None
                    else "task_scope_watermarks",
                    "terminal_gate_receipt_id": None if gate is None else gate["gate_receipt_id"],
                    "terminal_source_sequence": None
                    if gate is None
                    else int(gate["terminal_source_sequence"]),
                    "terminal_gate_durable_source_sequence": None
                    if gate is None
                    else int(gate["durable_source_sequence"]),
                    "recorded_at": now,
                }
                if observed is not None:
                    terminal_payload["primary_observation_ref"] = observed[0]["evidence_id"]
                    terminal_payload["primary_observation_hash"] = observed[0]["envelope_sha256"]
                terminal_hash = canonical_hash(terminal_payload)
                await db.execute(
                    "INSERT INTO foreground_terminal_receipts(terminal_receipt_id,host_run_id,sdk_run_id,terminal_state,generation,sdk_event_id,sdk_event_hash,receipt_hash,receipt_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        terminal_id,
                        host_run_id,
                        sdk_run_id,
                        terminal.value,
                        generation,
                        sdk_event_id,
                        sdk_event_hash,
                        terminal_hash,
                        canonical_json(terminal_payload),
                        now,
                    ),
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=generation,
                    prior_generation=generation,
                    action="close",
                    expires_at=None,
                    idempotency_key=f"terminal-close:{sdk_event_id}",
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state=?,owner_id=NULL,lease_expires_at=NULL,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (terminal.value, transition_id, transition_hash, now, host_run_id),
                )
                turn_transition_id, turn_transition_hash = await self._insert_turn_transition_tx(
                    db,
                    turn_id=str(head["turn_id"]),
                    subject=str(head["subject"]),
                    from_state=TurnState.CLAIMED.value,
                    to_state=TurnState.SETTLED.value,
                    host_run_id=host_run_id,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_turn_heads SET current_state='SETTLED',last_transition_id=?,last_transition_hash=?,updated_at=? WHERE turn_id=? AND current_state='CLAIMED'",
                    (turn_transition_id, turn_transition_hash, now, head["turn_id"]),
                )
                if run_binding is not None or outbox_dead_letter_reason is not None:
                    await self._append_memory_ingestion_outbox_tx(
                        db,
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        turn_id=str(head["turn_id"]),
                        subject=str(head["subject"]),
                        run_binding=run_binding,
                        endpoint_identity=endpoint_identity,
                        now=now,
                        dead_letter_reason=None
                        if run_binding is not None
                        else outbox_dead_letter_reason,
                    )
                    self._fault("terminal.after_outbox")
                if terminal_observer_tx is not None:
                    await terminal_observer_tx(db, host_run_id=host_run_id, sdk_run_id=sdk_run_id)
                self._fault("terminal.before_commit")
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_terminal_receipts WHERE host_run_id=?",
                    (host_run_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("terminal.after_commit")
        assert row is not None
        return self._terminal_receipt(row)

    async def current_snapshot(self, subject: str) -> ForegroundRunSnapshot | None:
        subject = identifier(subject, "subject", 512)
        await self.initialize()
        async with self._connection() as db:
            head = await self._active_head_tx(db, subject)
            if head is None:
                return None
            return await self._snapshot_tx(db, str(head["host_run_id"]))

    async def reserve_post_turn_attempt(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        attempt: Mapping[str, object],
        members: Sequence[tuple[str, str, str]] = (),
        input_observer_tx=None,
    ) -> str:
        """Insert one ``reserved`` post-turn attempt row under the current lease (one transaction).

        S5b Task 3 (design-freeze §6): only the current lease owner may start a
        post-turn Provider call; the ``reserved`` row and ``_validate_lease_tx``
        share the transaction so a reclaimed lease can never reserve.
        """

        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                allowed_states = _EFFECT_BOUNDARY_ALLOWED_STATES[EffectBoundary.CLOSURE]
                if str(head["current_state"]) not in allowed_states:
                    raise ForegroundQueueError("foreground_effect_state_rejected")
                await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
                attempt_id = await self._insert_post_turn_attempt_tx(
                    db,
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    generation=generation,
                    attempt=attempt,
                    members=members,
                    now=now,
                )
                if input_observer_tx is not None:
                    await input_observer_tx(attempt, db)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return attempt_id

    async def _insert_post_turn_attempt_tx(
        self,
        db: aiosqlite.Connection,
        *,
        host_run_id: str,
        sdk_run_id: str,
        generation: int,
        attempt: Mapping[str, object],
        members: Sequence[tuple[str, str, str]],
        now: float,
    ) -> str:
        if attempt.get("host_run_id") != host_run_id or attempt.get("sdk_run_id") != sdk_run_id:
            raise ForegroundQueueError("foreground_post_turn_attempt_identity_mismatch")
        attempt_id = str(attempt["attempt_id"])
        await db.execute(
            "INSERT INTO post_turn_invocation_attempts(attempt_id,purpose,host_run_id,sdk_run_id,generation,"
            "task_scope_id,closure_watermark,request_hash,attempt_ordinal,evidence_set_key,status,unknown_class,"
            "provider_id,model_id,model_config_hash,provider_request_id,result_hash,plan_id,reserved_at,"
            "handed_off_at,settled_at,reason_code) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'reserved',NULL,?,?,?,NULL,NULL,?,?,NULL,NULL,NULL)",
            (
                attempt_id,
                str(attempt["purpose"]),
                host_run_id,
                sdk_run_id,
                int(generation),
                attempt.get("task_scope_id"),
                attempt.get("closure_watermark"),
                str(attempt["request_hash"]),
                int(attempt["attempt_ordinal"]),  # type: ignore[call-overload]
                str(attempt["evidence_set_key"]),
                str(attempt["provider_id"]),
                str(attempt["model_id"]),
                str(attempt["model_config_hash"]),
                attempt.get("plan_id"),
                float(attempt.get("reserved_at") or now),  # type: ignore[arg-type]
            ),
        )
        for subject, run_id, evidence_id in members:
            await db.execute(
                "INSERT OR IGNORE INTO post_turn_invocation_members(attempt_id,subject,run_id,evidence_id) "
                "VALUES (?,?,?,?)",
                (attempt_id, str(subject), str(run_id), str(evidence_id)),
            )
        return attempt_id

    async def _append_memory_ingestion_outbox_tx(
        self,
        db: aiosqlite.Connection,
        *,
        host_run_id: str,
        sdk_run_id: str,
        turn_id: str,
        subject: str,
        run_binding: Mapping[str, object] | None,
        endpoint_identity: str | None,
        now: float,
        dead_letter_reason: str | None = None,
    ) -> str:
        """S5b Task 4: ``memory_ingestion_outbox(pending)`` + ``memory_ingestion_evidence_links``.

        The turn group's sanitized evidence = the foreground turn's admitted user
        evidence (``foreground_turns.evidence_id``).  Objective ``host.*`` evidence
        of the Run stays in the TaskScope ledger (closure), it is not an analysis
        member: one turn → one Memory batch → one analysis attempt.

        ``run_binding is None`` requires ``dead_letter_reason`` (Task 6): the row is
        written as ``dead_letter`` right away with a ``binding_missing`` lineage.
        """

        turn = await self._fetchone(
            db,
            "SELECT t.evidence_id, e.envelope_sha256 FROM foreground_turns t "
            "JOIN human_memory_evidence e ON e.evidence_id=t.evidence_id WHERE t.turn_id=?",
            (turn_id,),
        )
        if turn is None:
            raise ForegroundQueueError("foreground_turn_evidence_missing")
        evidence_ids = [str(turn["evidence_id"])]
        envelope_hash = canonical_hash({"envelope_hashes": sorted([str(turn["envelope_sha256"])])})
        if run_binding is not None:
            lineage: Mapping[str, object] = analysis_lineage_payload(
                run_binding, endpoint_identity=endpoint_identity
            )
            state = "pending"
            last_error: str | None = None
        else:
            reason = str(dead_letter_reason or "").strip()
            if not reason:
                raise ForegroundQueueError("memory_ingestion_outbox_binding_missing")
            lineage = {
                "schema_version": 1,
                "binding_missing": True,
                "reason": reason,
                "model_config_hash": canonical_hash({"binding_missing": reason}),
            }
            state = "dead_letter"
            last_error = reason
        outbox_id = _uuid(f"memory-ingestion-outbox:{sdk_run_id}:{turn_id}")
        existing = await self._fetchone(
            db,
            "SELECT evidence_ids_json,envelope_hash FROM memory_ingestion_outbox WHERE outbox_id=?",
            (outbox_id,),
        )
        if existing is not None:
            if (
                json.loads(str(existing["evidence_ids_json"])) != evidence_ids
                or str(existing["envelope_hash"]) != envelope_hash
            ):
                raise ForegroundQueueError("memory_ingestion_outbox_conflict")
            return outbox_id
        await db.execute(
            "INSERT INTO memory_ingestion_outbox(outbox_id,host_run_id,sdk_run_id,turn_id,subject,evidence_ids_json,"
            "envelope_hash,model_config_hash,analysis_lineage_json,state,attempts,lease_owner,lease_expires_at,"
            "receipt_json,last_error,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,?,?,?)",
            (
                outbox_id,
                host_run_id,
                sdk_run_id,
                turn_id,
                subject,
                canonical_json(evidence_ids),
                envelope_hash,
                str(lineage["model_config_hash"]),
                canonical_json(lineage),
                state,
                last_error,
                now,
                now,
            ),
        )
        for evidence_id in evidence_ids:
            await db.execute(
                "INSERT OR IGNORE INTO memory_ingestion_evidence_links(outbox_id,evidence_id) VALUES (?,?)",
                (outbox_id, evidence_id),
            )
        return outbox_id

    async def authorize_effect(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        boundary: EffectBoundary | str = EffectBoundary.TOOL,
    ) -> EffectAdmissionReceipt:
        """Final current-generation admission before one external side effect.

        Every externally visible Runtime boundary — the sole SDK start, each
        control signal send, and each physical Tool effect dispatch — must call
        this immediately before acting so a reclaimed lease (stale owner or
        stale generation) can never produce an external side effect.
        """

        try:
            effect_boundary = EffectBoundary(boundary)
        except ValueError as exc:
            raise ForegroundQueueError("foreground_effect_boundary_invalid") from exc
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
            allowed_states = _EFFECT_BOUNDARY_ALLOWED_STATES[effect_boundary]
            if str(head["current_state"]) not in allowed_states:
                raise ForegroundQueueError("foreground_effect_state_rejected")
            await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
            snapshot = await self._snapshot_tx(db, host_run_id)
        return EffectAdmissionReceipt(host_run_id, sdk_run_id, generation, snapshot.snapshot_hash)

    async def _record_sdk_state(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_event_id: str,
        idempotency_key: str,
        allowed_from: set[str],
        to_state: RunState,
        clear_pause: bool,
    ) -> ForegroundRunSnapshot:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_event_id = identifier(sdk_event_id, "sdk_event_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                replay = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_transitions WHERE host_run_id=? AND idempotency_key=?",
                    (host_run_id, idempotency_key),
                )
                if replay is not None:
                    if (
                        replay["to_state"] != to_state.value
                        or replay["sdk_event_id"] != sdk_event_id
                    ):
                        raise ForegroundQueueError("foreground_state_idempotency_conflict")
                    snapshot = await self._snapshot_tx(db, host_run_id)
                    await db.commit()
                    return snapshot
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
                if str(head["current_state"]) not in allowed_from:
                    raise ForegroundQueueError("foreground_state_transition_invalid")
                if to_state is RunState.RUNNING:
                    observation = await self._fetchone(
                        db,
                        "SELECT observation_id FROM foreground_execution_start_observations "
                        "WHERE host_run_id=? AND sdk_run_id=? "
                        "AND outcome IN ('RETURNED','QUERY_FOUND') "
                        "ORDER BY recorded_at,observation_id LIMIT 1",
                        (host_run_id, sdk_run_id),
                    )
                    if observation is None:
                        raise ForegroundQueueError("foreground_execution_start_observation_missing")
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=str(head["current_state"]),
                    to_state=to_state.value,
                    generation=generation,
                    owner_id=owner_id,
                    sdk_event_id=sdk_event_id,
                    idempotency_key=idempotency_key,
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                desired = None if clear_pause else head["desired_control"]
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state=?,desired_control=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (to_state.value, desired, transition_id, transition_hash, now, host_run_id),
                )
                snapshot = await self._snapshot_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return snapshot
