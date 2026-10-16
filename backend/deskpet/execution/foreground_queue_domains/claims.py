# SPDX-License-Identifier: BUSL-1.1

"""Claims, execution preparation, start intents and start observations."""

from __future__ import annotations

from dataclasses import dataclass

from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, digest, identifier

from .core import ForegroundQueueCore
from .support import (
    AdmissionReceipt,
    ContextLineage,
    ExecutionAuditReceipt,
    ForegroundQueueError,
    PreparationCandidate,
    RunState,
    TurnState,
    _clock_value,
    _duration,
    _uuid,
)

FOREGROUND_SCHEDULER_KIND = "foreground_scheduler"


@dataclass(frozen=True, slots=True)
class ClaimedExecution:
    host_run_id: str
    owner_id: str
    generation: int
    draft_id: str
    draft_hash: str
    candidate: PreparationCandidate
    lineage_hash: str
    claimed_execution_hash: str
    admission_receipt_id: str
    admission_receipt_hash: str


class ClaimMixin(ForegroundQueueCore):
    """Claims, execution preparation, start intents and start observations."""

    __slots__ = ()

    async def claim_next(
        self,
        *,
        subject: str,
        owner_id: str,
        claim_idempotency_key: str,
        preparation_draft_id: str,
        preparation_draft_hash: str,
        lease_seconds: float,
        worker_kind: str = FOREGROUND_SCHEDULER_KIND,
    ) -> AdmissionReceipt | None:
        if worker_kind != FOREGROUND_SCHEDULER_KIND:
            raise ForegroundQueueError("foreground_background_claim_rejected")
        subject = identifier(subject, "subject", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        claim_idempotency_key = identifier(claim_idempotency_key, "claim_idempotency_key", 512)
        preparation_draft_id = identifier(preparation_draft_id, "preparation_draft_id", 512)
        preparation_draft_hash = digest(preparation_draft_hash, "preparation_draft_hash")
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                active = await self._active_head_tx(db, subject)
                if active is not None:
                    run = await self._run_tx(db, str(active["host_run_id"]))
                    preparation_binding = await self._fetchone(
                        db,
                        "SELECT draft_id,draft_hash FROM foreground_run_preparation_bindings "
                        "WHERE host_run_id=?",
                        (run["host_run_id"],),
                    )
                    if (
                        run["claim_idempotency_key"] == claim_idempotency_key
                        and active["owner_id"] == owner_id
                        and preparation_binding is not None
                        and preparation_binding["draft_id"] == preparation_draft_id
                        and preparation_binding["draft_hash"] == preparation_draft_hash
                    ):
                        await db.commit()
                        return self._admission_receipt(run, active, recovered=True)
                    raise ForegroundQueueError("foreground_run_already_active")
                candidate = await self._preparation_candidate_tx(db, subject)
                if candidate is None:
                    await db.commit()
                    return None
                draft = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_preparation_drafts WHERE draft_id=?",
                    (preparation_draft_id,),
                )
                if (
                    draft is None
                    or draft["subject"] != subject
                    or draft["draft_hash"] != preparation_draft_hash
                ):
                    raise ForegroundQueueError("foreground_preparation_draft_invalid")
                if (
                    draft["turn_id"] != candidate.turn_id
                    or draft["turn_hash"] != candidate.turn_hash
                    or draft["candidate_hash"] != candidate.candidate_hash
                    or draft["candidate_json"] != candidate.candidate_json
                ):
                    raise ForegroundQueueError("foreground_preparation_candidate_stale")
                context = ContextLineage(
                    str(draft["context_snapshot_id"]),
                    int(draft["context_snapshot_revision"]),
                    str(draft["context_snapshot_hash"]),
                )
                host_run_id = _uuid(f"foreground-run:{candidate.turn_id}")
                lineage = {
                    "task_scope_id": candidate.task_scope_id,
                    "binding_set_revision": candidate.binding_set_revision,
                    "binding_set_receipt_id": candidate.binding_set_receipt_id,
                    "binding_set_receipt_hash": candidate.binding_set_receipt_hash,
                    "context_snapshot_id": context.context_snapshot_id,
                    "context_snapshot_revision": context.context_snapshot_revision,
                    "context_snapshot_hash": context.context_snapshot_hash,
                    "preparation_draft_id": preparation_draft_id,
                    "preparation_draft_hash": preparation_draft_hash,
                    "candidate_hash": candidate.candidate_hash,
                }
                lineage_hash = canonical_hash(lineage)
                admission_id = _uuid(f"foreground-admission:{host_run_id}")
                admission = {
                    "schema_version": 1,
                    "admission_receipt_id": admission_id,
                    "host_run_id": host_run_id,
                    "turn_id": candidate.turn_id,
                    "subject": subject,
                    "primary_conversation_id": candidate.primary_conversation_id,
                    "claim_idempotency_key": claim_idempotency_key,
                    "lineage": lineage,
                    "lineage_hash": lineage_hash,
                    "owner_id": owner_id,
                    "generation": 1,
                    "lease_expires_at": expires_at,
                    "admitted_at": now,
                }
                admission_hash = canonical_hash(admission)
                await db.execute(
                    "INSERT INTO foreground_runs(host_run_id,subject,primary_conversation_id,turn_id,enqueue_sequence,task_scope_id,binding_set_revision,binding_set_receipt_id,binding_set_receipt_hash,context_snapshot_id,context_snapshot_revision,context_snapshot_hash,lineage_hash,claim_idempotency_key,admission_receipt_id,admission_receipt_hash,admission_json,admitted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        host_run_id,
                        subject,
                        candidate.primary_conversation_id,
                        candidate.turn_id,
                        candidate.enqueue_sequence,
                        candidate.task_scope_id,
                        candidate.binding_set_revision,
                        candidate.binding_set_receipt_id,
                        candidate.binding_set_receipt_hash,
                        context.context_snapshot_id,
                        context.context_snapshot_revision,
                        context.context_snapshot_hash,
                        lineage_hash,
                        claim_idempotency_key,
                        admission_id,
                        admission_hash,
                        canonical_json(admission),
                        now,
                    ),
                )
                preparation_binding = {
                    "schema_version": 1,
                    "host_run_id": host_run_id,
                    "draft_id": preparation_draft_id,
                    "draft_hash": preparation_draft_hash,
                    "candidate_hash": candidate.candidate_hash,
                }
                preparation_binding_hash = canonical_hash(preparation_binding)
                await db.execute(
                    "INSERT INTO foreground_run_preparation_bindings("
                    "binding_id,host_run_id,draft_id,draft_hash,candidate_hash,"
                    "binding_hash,binding_json,bound_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        _uuid(f"foreground-run-preparation:{host_run_id}"),
                        host_run_id,
                        preparation_draft_id,
                        preparation_draft_hash,
                        candidate.candidate_hash,
                        preparation_binding_hash,
                        canonical_json(preparation_binding),
                        now,
                    ),
                )
                run_transition_id, run_transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=subject,
                    from_state="ABSENT",
                    to_state=RunState.CLAIMED.value,
                    generation=1,
                    owner_id=owner_id,
                    sdk_event_id=None,
                    idempotency_key=f"admission:{claim_idempotency_key}",
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                await db.execute(
                    "INSERT INTO foreground_run_heads(host_run_id,subject,primary_conversation_id,turn_id,current_state,desired_control,sdk_run_id,owner_id,generation,lease_expires_at,last_transition_id,last_transition_hash,updated_at) VALUES (?,?,?,?,?,NULL,NULL,?,1,?,?,?,?)",
                    (
                        host_run_id,
                        subject,
                        candidate.primary_conversation_id,
                        candidate.turn_id,
                        RunState.CLAIMED.value,
                        owner_id,
                        expires_at,
                        run_transition_id,
                        run_transition_hash,
                        now,
                    ),
                )
                turn_transition_id, turn_transition_hash = await self._insert_turn_transition_tx(
                    db,
                    turn_id=candidate.turn_id,
                    subject=subject,
                    from_state=TurnState.QUEUED.value,
                    to_state=TurnState.CLAIMED.value,
                    host_run_id=host_run_id,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_turn_heads SET current_state='CLAIMED',host_run_id=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE turn_id=? AND current_state='QUEUED'",
                    (host_run_id, turn_transition_id, turn_transition_hash, now, candidate.turn_id),
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=1,
                    prior_generation=None,
                    action="acquire",
                    expires_at=expires_at,
                    idempotency_key=f"lease-acquire:{claim_idempotency_key}",
                    recorded_at=now,
                )
                self._fault("claim.before_commit")
                head = await self._head_tx(db, host_run_id)
                run = await self._run_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("claim.after_commit")
        return self._admission_receipt(run, head, recovered=False)

    async def read_claimed_execution(
        self, *, host_run_id: str, owner_id: str, generation: int
    ) -> ClaimedExecution:
        """Return the exact claimed payload only to the current lease generation."""

        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            run = await self._run_tx(db, host_run_id)
            await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
            binding = await self._fetchone(
                db,
                "SELECT b.*,d.*,t.evidence_id,t.evidence_hash "
                "FROM foreground_run_preparation_bindings b "
                "JOIN foreground_preparation_drafts d ON d.draft_id=b.draft_id "
                "JOIN foreground_turns t ON t.turn_id=d.turn_id "
                "WHERE b.host_run_id=?",
                (host_run_id,),
            )
            if binding is None:
                raise ForegroundQueueError("foreground_preparation_binding_missing")
            candidate = self._candidate_from_json(str(binding["candidate_json"]))
            if (
                candidate.candidate_hash != str(binding["candidate_hash"])
                or candidate.turn_id != str(run["turn_id"])
                or candidate.evidence_id != str(binding["evidence_id"])
                or candidate.evidence_hash != str(binding["evidence_hash"])
            ):
                raise ForegroundQueueError("foreground_claimed_execution_corrupt")
            payload = {
                "schema_version": 1,
                "host_run_id": host_run_id,
                "owner_id": owner_id,
                "generation": generation,
                "draft_id": binding["draft_id"],
                "draft_hash": binding["draft_hash"],
                "candidate_hash": candidate.candidate_hash,
                "lineage_hash": run["lineage_hash"],
            }
            return ClaimedExecution(
                host_run_id=host_run_id,
                owner_id=owner_id,
                generation=generation,
                draft_id=str(binding["draft_id"]),
                draft_hash=str(binding["draft_hash"]),
                candidate=candidate,
                lineage_hash=str(run["lineage_hash"]),
                claimed_execution_hash=canonical_hash(payload),
                admission_receipt_id=str(run["admission_receipt_id"]),
                admission_receipt_hash=str(run["admission_receipt_hash"]),
            )

    async def read_start_observation_outcomes(
        self, *, host_run_id: str, owner_id: str, generation: int
    ) -> tuple[str, ...]:
        """Read immutable start outcomes for exact crash-recovery decisions."""

        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
            cursor = await db.execute(
                "SELECT outcome FROM foreground_execution_start_observations "
                "WHERE host_run_id=? ORDER BY recorded_at,observation_id",
                (host_run_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return tuple(str(row["outcome"]) for row in rows)

    async def record_execution_preparation(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        generation: int,
        context_ref: str,
        context_hash: str,
        provider_ref: str,
        provider_hash: str,
        tool_ref: str,
        tool_hash: str,
        execution_request_hash: str,
        idempotency_key: str,
    ) -> ExecutionAuditReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        context_ref = identifier(context_ref, "context_ref", 1024)
        context_hash = digest(context_hash, "context_hash")
        provider_ref = identifier(provider_ref, "provider_ref", 1024)
        provider_hash = digest(provider_hash, "provider_hash")
        tool_ref = identifier(tool_ref, "tool_ref", 1024)
        tool_hash = digest(tool_hash, "tool_hash")
        execution_request_hash = digest(execution_request_hash, "execution_request_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                binding = await self._fetchone(
                    db,
                    "SELECT draft_id,draft_hash FROM foreground_run_preparation_bindings "
                    "WHERE host_run_id=?",
                    (host_run_id,),
                )
                if binding is None:
                    raise ForegroundQueueError("foreground_preparation_binding_missing")
                preparation_id = _uuid(f"foreground-execution-preparation:{host_run_id}")
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_execution_preparations WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if (
                        existing["draft_id"] != binding["draft_id"]
                        or existing["draft_hash"] != binding["draft_hash"]
                        or existing["context_ref"] != context_ref
                        or existing["context_hash"] != context_hash
                        or existing["provider_ref"] != provider_ref
                        or existing["provider_hash"] != provider_hash
                        or existing["tool_ref"] != tool_ref
                        or existing["tool_hash"] != tool_hash
                        or existing["execution_request_hash"] != execution_request_hash
                        or existing["idempotency_key"] != idempotency_key
                    ):
                        raise ForegroundQueueError("foreground_execution_preparation_immutable")
                    await db.commit()
                    return self._audit_receipt(
                        existing,
                        phase="preparation",
                        id_column="preparation_id",
                        hash_column="preparation_hash",
                    )
                payload = {
                    "schema_version": 1,
                    "preparation_id": preparation_id,
                    "host_run_id": host_run_id,
                    "owner_id": owner_id,
                    "generation": generation,
                    "draft_id": binding["draft_id"],
                    "draft_hash": binding["draft_hash"],
                    "context_ref": context_ref,
                    "context_hash": context_hash,
                    "provider_ref": provider_ref,
                    "provider_hash": provider_hash,
                    "tool_ref": tool_ref,
                    "tool_hash": tool_hash,
                    "execution_request_hash": execution_request_hash,
                    "idempotency_key": idempotency_key,
                }
                preparation_hash = canonical_hash(payload)
                await db.execute(
                    "INSERT INTO foreground_execution_preparations("
                    "preparation_id,host_run_id,owner_id,generation,draft_id,draft_hash,"
                    "context_ref,context_hash,provider_ref,provider_hash,tool_ref,tool_hash,"
                    "execution_request_hash,idempotency_key,preparation_hash,"
                    "preparation_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        preparation_id,
                        host_run_id,
                        owner_id,
                        generation,
                        binding["draft_id"],
                        binding["draft_hash"],
                        context_ref,
                        context_hash,
                        provider_ref,
                        provider_hash,
                        tool_ref,
                        tool_hash,
                        execution_request_hash,
                        idempotency_key,
                        preparation_hash,
                        canonical_json(payload),
                        now,
                    ),
                )
                self._fault("execution_preparation.before_commit")
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_execution_preparations WHERE preparation_id=?",
                    (preparation_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("execution_preparation.after_commit")
        assert row is not None
        return self._audit_receipt(
            row,
            phase="preparation",
            id_column="preparation_id",
            hash_column="preparation_hash",
        )

    async def record_start_intent(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        start_request_hash: str,
        idempotency_key: str,
    ) -> ExecutionAuditReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        start_request_hash = digest(start_request_hash, "start_request_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                preparation = await self._fetchone(
                    db,
                    "SELECT preparation_id,preparation_hash FROM foreground_execution_preparations "
                    "WHERE host_run_id=?",
                    (host_run_id,),
                )
                if preparation is None:
                    raise ForegroundQueueError("foreground_execution_preparation_missing")
                intent_id = _uuid(f"foreground-start-intent:{host_run_id}")
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_execution_start_intents WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if (
                        existing["sdk_run_id"] != sdk_run_id
                        or existing["preparation_id"] != preparation["preparation_id"]
                        or existing["preparation_hash"] != preparation["preparation_hash"]
                        or existing["start_request_hash"] != start_request_hash
                        or existing["idempotency_key"] != idempotency_key
                    ):
                        raise ForegroundQueueError("foreground_execution_start_intent_immutable")
                    await db.commit()
                    return self._audit_receipt(
                        existing,
                        phase="start_intent",
                        id_column="intent_id",
                        hash_column="intent_hash",
                    )
                payload = {
                    "schema_version": 1,
                    "intent_id": intent_id,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "owner_id": owner_id,
                    "generation": generation,
                    "preparation_id": preparation["preparation_id"],
                    "preparation_hash": preparation["preparation_hash"],
                    "start_request_hash": start_request_hash,
                    "idempotency_key": idempotency_key,
                }
                intent_hash = canonical_hash(payload)
                await db.execute(
                    "INSERT INTO foreground_execution_start_intents("
                    "intent_id,host_run_id,sdk_run_id,owner_id,generation,preparation_id,"
                    "preparation_hash,start_request_hash,idempotency_key,intent_hash,"
                    "intent_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        intent_id,
                        host_run_id,
                        sdk_run_id,
                        owner_id,
                        generation,
                        preparation["preparation_id"],
                        preparation["preparation_hash"],
                        start_request_hash,
                        idempotency_key,
                        intent_hash,
                        canonical_json(payload),
                        now,
                    ),
                )
                self._fault("start_intent.before_commit")
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_execution_start_intents WHERE intent_id=?",
                    (intent_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("start_intent.after_commit")
        assert row is not None
        return self._audit_receipt(
            row,
            phase="start_intent",
            id_column="intent_id",
            hash_column="intent_hash",
        )

    async def record_start_observation(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        outcome: str,
        idempotency_key: str,
        result_ref: str | None = None,
        result_hash: str | None = None,
        error_code: str | None = None,
    ) -> ExecutionAuditReceipt:
        if outcome not in {"RETURNED", "RAISED", "QUERY_FOUND", "QUERY_MISSING"}:
            raise ForegroundQueueError("foreground_start_observation_invalid")
        return await self._record_execution_event(
            table="foreground_execution_start_observations",
            phase="start_observation",
            id_prefix="foreground-start-observation",
            id_column="observation_id",
            hash_column="observation_hash",
            json_column="observation_json",
            state_column="outcome",
            state_value=outcome,
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            evidence_ref=result_ref,
            evidence_hash=result_hash,
            error_code=error_code,
            idempotency_key=idempotency_key,
        )

    async def record_reconciliation(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        observed_state: str,
        idempotency_key: str,
        evidence_ref: str | None = None,
        evidence_hash: str | None = None,
    ) -> ExecutionAuditReceipt:
        if observed_state not in {
            "BOUND_RUNNING",
            "BOUND_WAITING",
            "BOUND_TERMINAL",
            "UNBOUND_RETRY",
            "FAILED_CLOSED",
        }:
            raise ForegroundQueueError("foreground_reconciliation_state_invalid")
        return await self._record_execution_event(
            table="foreground_execution_reconciliations",
            phase="reconciliation",
            id_prefix="foreground-reconciliation",
            id_column="reconciliation_id",
            hash_column="reconciliation_hash",
            json_column="reconciliation_json",
            state_column="observed_state",
            state_value=observed_state,
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            evidence_ref=evidence_ref,
            evidence_hash=evidence_hash,
            error_code=None,
            idempotency_key=idempotency_key,
        )
