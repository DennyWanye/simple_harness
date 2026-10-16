# SPDX-License-Identifier: BUSL-1.1

"""SDK run binding, control requests, signals, heartbeats and lease recovery."""

from __future__ import annotations

from deskpet.execution.preparation_rejection import settle_preparation_rejection
from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier

from .core import ForegroundQueueCore
from .support import (
    TERMINAL_STATES,
    ControlKind,
    ControlReceipt,
    ForegroundQueueError,
    ForegroundRunSnapshot,
    LeaseReceipt,
    RunState,
    SdkRunBindingReceipt,
    SignalAckReceipt,
    SignalEnvelope,
    _clock_value,
    _duration,
    _uuid,
)


class DrivingMixin(ForegroundQueueCore):
    """SDK run binding, control requests, signals, heartbeats and lease recovery."""

    __slots__ = ()

    async def bind_sdk_run(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        idempotency_key: str,
    ) -> SdkRunBindingReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_sdk_bindings WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if existing["sdk_run_id"] != sdk_run_id:
                        raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                    await db.commit()
                    return self._binding_receipt(existing)
                sdk_owner = await self._fetchone(
                    db,
                    "SELECT host_run_id FROM foreground_run_sdk_bindings WHERE sdk_run_id=?",
                    (sdk_run_id,),
                )
                if sdk_owner is not None:
                    raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                intent = await self._fetchone(
                    db,
                    "SELECT sdk_run_id FROM foreground_execution_start_intents WHERE host_run_id=?",
                    (host_run_id,),
                )
                if intent is None or intent["sdk_run_id"] != sdk_run_id:
                    raise ForegroundQueueError("foreground_execution_start_intent_missing")
                if head["sdk_run_id"] is not None and head["sdk_run_id"] != sdk_run_id:
                    raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                payload = {
                    "schema_version": 1,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "idempotency_key": idempotency_key,
                    "bound_at": now,
                }
                binding_hash = canonical_hash(payload)
                binding_id = _uuid(f"foreground-sdk-binding:{host_run_id}")
                await db.execute(
                    "INSERT INTO foreground_run_sdk_bindings(binding_id,host_run_id,sdk_run_id,idempotency_key,binding_hash,binding_json,bound_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        binding_id,
                        host_run_id,
                        sdk_run_id,
                        idempotency_key,
                        binding_hash,
                        canonical_json(payload),
                        now,
                    ),
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET sdk_run_id=?,updated_at=? WHERE host_run_id=?",
                    (sdk_run_id, now, host_run_id),
                )
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_sdk_bindings WHERE host_run_id=?",
                    (host_run_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._binding_receipt(row)

    async def record_sdk_started(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_event_id: str,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        return await self._record_sdk_state(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            sdk_event_id=sdk_event_id,
            idempotency_key=idempotency_key,
            allowed_from={RunState.CLAIMED.value},
            to_state=RunState.RUNNING,
            clear_pause=False,
        )

    async def record_pause_outcome(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_event_id: str,
        paused: bool,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        return await self._record_sdk_state(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            sdk_event_id=sdk_event_id,
            idempotency_key=idempotency_key,
            allowed_from={RunState.PAUSE_REQUESTED.value},
            to_state=RunState.PAUSED if paused else RunState.RUNNING,
            clear_pause=not paused,
        )

    async def request_control(
        self,
        *,
        host_run_id: str,
        subject: str,
        generation: int,
        control_kind: ControlKind | str,
        reason: str,
        idempotency_key: str,
    ) -> ControlReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        subject = identifier(subject, "subject", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        reason = identifier(reason, "control_reason", 2048)
        try:
            kind = ControlKind(control_kind)
        except ValueError as exc:
            raise ForegroundQueueError("foreground_control_kind_invalid") from exc
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ForegroundQueueError("foreground_generation_invalid")
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_control_intents WHERE host_run_id=? AND idempotency_key=?",
                    (host_run_id, idempotency_key),
                )
                if existing is not None:
                    if (
                        existing["subject"] != subject
                        or existing["control_kind"] != kind.value
                        or existing["reason"] != reason
                        or int(existing["requested_generation"]) != generation
                    ):
                        raise ForegroundQueueError("foreground_control_idempotency_conflict")
                    await db.commit()
                    return await self._control_receipt_from_row(db, existing)
                head = await self._head_tx(db, host_run_id)
                if head["subject"] != subject:
                    raise ForegroundQueueError("foreground_run_subject_mismatch")
                if int(head["generation"]) != generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                current_state = str(head["current_state"])
                signal_id: str | None = None
                if current_state in TERMINAL_STATES:
                    outcome = "already_terminal"
                    reduced_state = current_state
                else:
                    current_kind = (
                        str(head["desired_control"])
                        if head["desired_control"] is not None
                        else None
                    )
                    current_priority = self._control_priority(current_kind)
                    requested_priority = self._control_priority(kind.value)
                    if requested_priority < current_priority:
                        outcome = "superseded"
                        reduced_state = current_state
                    elif requested_priority == current_priority and current_kind is not None:
                        outcome = "already_requested"
                        reduced_state = current_state
                    else:
                        outcome = "signalled"
                        reduced_state = {
                            ControlKind.PAUSE: RunState.PAUSE_REQUESTED.value,
                            ControlKind.STOP: RunState.STOP_REQUESTED.value,
                            ControlKind.CANCEL: RunState.CANCEL_REQUESTED.value,
                        }[kind]
                control_id = _uuid(f"foreground-control:{host_run_id}:{idempotency_key}")
                control_payload = {
                    "schema_version": 1,
                    "control_id": control_id,
                    "host_run_id": host_run_id,
                    "subject": subject,
                    "control_kind": kind.value,
                    "reason": reason,
                    "requested_generation": generation,
                    "outcome": outcome,
                    "reduced_state": reduced_state,
                    "idempotency_key": idempotency_key,
                    "recorded_at": now,
                }
                control_hash = canonical_hash(control_payload)
                if outcome == "signalled":
                    transition_id, transition_hash = await self._insert_run_transition_tx(
                        db,
                        host_run_id=host_run_id,
                        subject=subject,
                        from_state=current_state,
                        to_state=reduced_state,
                        generation=generation,
                        owner_id=str(head["owner_id"]),
                        sdk_event_id=None,
                        idempotency_key=f"control-transition:{control_id}",
                        causal_evidence_ref=None,
                        causal_evidence_hash=None,
                        recorded_at=now,
                    )
                    await db.execute(
                        "UPDATE foreground_run_heads SET current_state=?,desired_control=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                        (
                            reduced_state,
                            kind.value,
                            transition_id,
                            transition_hash,
                            now,
                            host_run_id,
                        ),
                    )
                await db.execute(
                    "INSERT INTO foreground_control_intents(control_id,host_run_id,subject,control_kind,reason,requested_generation,outcome,reduced_state,idempotency_key,control_hash,control_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        control_id,
                        host_run_id,
                        subject,
                        kind.value,
                        reason,
                        generation,
                        outcome,
                        reduced_state,
                        idempotency_key,
                        control_hash,
                        canonical_json(control_payload),
                        now,
                    ),
                )
                if outcome == "signalled":
                    signal_id = await self._insert_signal_tx(
                        db,
                        control_id=control_id,
                        host_run_id=host_run_id,
                        generation=generation,
                        control_kind=kind,
                        recorded_at=now,
                    )
                self._fault("control.before_commit")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("control.after_commit")
        return ControlReceipt(
            control_id,
            host_run_id,
            kind,
            outcome,
            RunState(reduced_state),
            generation,
            control_hash,
            signal_id,
        )

    async def pending_signals(self, host_run_id: str) -> tuple[SignalEnvelope, ...]:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        await self.initialize()
        async with self._connection() as db:
            head = await self._head_tx(db, host_run_id)
            if str(head["current_state"]) in TERMINAL_STATES or head["desired_control"] is None:
                return ()
            cursor = await db.execute(
                "SELECT s.*,b.sdk_run_id FROM foreground_signal_outbox s "
                "LEFT JOIN foreground_signal_acks a ON a.signal_id=s.signal_id "
                "LEFT JOIN foreground_run_sdk_bindings b ON b.host_run_id=s.host_run_id "
                "WHERE s.host_run_id=? AND s.generation=? AND s.control_kind=? "
                "AND a.signal_id IS NULL ORDER BY s.created_at,s.signal_id",
                (
                    host_run_id,
                    int(head["generation"]),
                    str(head["desired_control"]),
                ),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            visible_rows = []
            for row in rows:
                if not await is_human_memory_work_item_parked_tx(
                    db,
                    worker_kind="foreground-signal",
                    source_table="foreground_signal_outbox",
                    primary_key="signal_id",
                    item_pk=str(row["signal_id"]),
                ):
                    visible_rows.append(row)
        return tuple(self._signal(row) for row in visible_rows)

    async def acknowledge_signal(
        self,
        *,
        signal_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_signal_id: str,
    ) -> SignalAckReceipt:
        signal_id = identifier(signal_id, "signal_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_signal_id = identifier(sdk_signal_id, "sdk_signal_id", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                signal = await self._fetchone(
                    db, "SELECT * FROM foreground_signal_outbox WHERE signal_id=?", (signal_id,)
                )
                if signal is None:
                    raise ForegroundQueueError("foreground_signal_not_found")
                head = await self._head_tx(db, str(signal["host_run_id"]))
                if int(signal["generation"]) != generation or int(head["generation"]) != generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                if (
                    str(head["current_state"]) in TERMINAL_STATES
                    or head["desired_control"] != signal["control_kind"]
                ):
                    raise ForegroundQueueError("foreground_signal_superseded")
                existing = await self._fetchone(
                    db, "SELECT * FROM foreground_signal_acks WHERE signal_id=?", (signal_id,)
                )
                if existing is not None:
                    if (
                        existing["sdk_signal_id"] != sdk_signal_id
                        or existing["sdk_run_id"] != sdk_run_id
                    ):
                        raise ForegroundQueueError("foreground_signal_ack_conflict")
                    await db.commit()
                    return self._signal_ack(existing)
                await self._validate_lease_tx(
                    db, str(signal["host_run_id"]), owner_id, generation, now
                )
                await self._validate_sdk_binding_tx(db, str(signal["host_run_id"]), sdk_run_id)
                payload = {
                    "schema_version": 1,
                    "signal_id": signal_id,
                    "host_run_id": signal["host_run_id"],
                    "sdk_run_id": sdk_run_id,
                    "generation": generation,
                    "sdk_signal_id": sdk_signal_id,
                    "acknowledged_at": now,
                }
                ack_hash = canonical_hash(payload)
                ack_id = _uuid(f"foreground-signal-ack:{signal_id}")
                await db.execute(
                    "INSERT INTO foreground_signal_acks(ack_id,signal_id,host_run_id,sdk_run_id,generation,sdk_signal_id,ack_hash,ack_json,acknowledged_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ack_id,
                        signal_id,
                        signal["host_run_id"],
                        sdk_run_id,
                        generation,
                        sdk_signal_id,
                        ack_hash,
                        canonical_json(payload),
                        now,
                    ),
                )
                row = await self._fetchone(
                    db, "SELECT * FROM foreground_signal_acks WHERE ack_id=?", (ack_id,)
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._signal_ack(row)

    async def heartbeat(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> LeaseReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != owner_id
                        or int(existing["generation"]) != generation
                        or existing["action"] != "heartbeat"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    await db.commit()
                    return self._lease_receipt(existing)
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                receipt = await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=generation,
                    prior_generation=generation,
                    action="heartbeat",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET lease_expires_at=?,updated_at=? WHERE host_run_id=?",
                    (expires_at, now, host_run_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def close_current_lease(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        generation: int,
        idempotency_key: str,
    ) -> LeaseReceipt:
        """Expire the current lease during graceful scheduler shutdown."""

        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != owner_id
                        or int(existing["generation"]) != generation
                        or existing["action"] != "close"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    await db.commit()
                    return self._lease_receipt(existing)
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                receipt = await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=generation,
                    prior_generation=generation,
                    action="close",
                    expires_at=None,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET lease_expires_at=?,updated_at=? "
                    "WHERE host_run_id=?",
                    (now, now, host_run_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def reclaim_expired(
        self,
        *,
        host_run_id: str,
        new_owner_id: str,
        expected_generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> LeaseReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        new_owner_id = identifier(new_owner_id, "new_owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != new_owner_id
                        or int(existing["generation"]) != expected_generation + 1
                        or int(existing["prior_generation"]) != expected_generation
                        or existing["action"] != "reclaim"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    await db.commit()
                    return self._lease_receipt(existing)
                head = await self._head_tx(db, host_run_id)
                if str(head["current_state"]) in TERMINAL_STATES:
                    raise ForegroundQueueError("foreground_run_already_terminal")
                if int(head["generation"]) != expected_generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                if float(head["lease_expires_at"]) > now:
                    raise ForegroundQueueError("foreground_lease_not_expired")
                new_generation = expected_generation + 1
                receipt = await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=new_owner_id,
                    generation=new_generation,
                    prior_generation=expected_generation,
                    action="reclaim",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET owner_id=?,generation=?,lease_expires_at=?,updated_at=? WHERE host_run_id=?",
                    (new_owner_id, new_generation, expires_at, now, host_run_id),
                )
                await self._reissue_desired_control_tx(db, head, new_generation, now)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def resume_paused(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        expected_generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != owner_id
                        or int(existing["generation"]) != expected_generation + 1
                        or int(existing["prior_generation"]) != expected_generation
                        or existing["action"] != "resume"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    snapshot = await self._snapshot_tx(db, host_run_id)
                    await db.commit()
                    return snapshot
                head = await self._head_tx(db, host_run_id)
                if head["current_state"] != RunState.PAUSED.value:
                    raise ForegroundQueueError("foreground_resume_state_invalid")
                if head["desired_control"] not in (None, ControlKind.PAUSE.value):
                    raise ForegroundQueueError("foreground_resume_strong_control_pending")
                if int(head["generation"]) != expected_generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                if head["sdk_run_id"] is None:
                    raise ForegroundQueueError("foreground_sdk_run_binding_missing")
                new_generation = expected_generation + 1
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=RunState.PAUSED.value,
                    to_state=RunState.RUNNING.value,
                    generation=new_generation,
                    owner_id=owner_id,
                    sdk_event_id=None,
                    idempotency_key=f"resume-transition:{idempotency_key}",
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=new_generation,
                    prior_generation=expected_generation,
                    action="resume",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state='RUNNING',desired_control=NULL,owner_id=?,generation=?,lease_expires_at=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (
                        owner_id,
                        new_generation,
                        expires_at,
                        transition_id,
                        transition_hash,
                        now,
                        host_run_id,
                    ),
                )
                snapshot = await self._snapshot_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return snapshot

    async def settle_preparation_rejection(self, *, rejection, owner_id, generation):

        return await settle_preparation_rejection(
            self,
            rejection=rejection,
            owner_id=owner_id,
            generation=generation,
        )
