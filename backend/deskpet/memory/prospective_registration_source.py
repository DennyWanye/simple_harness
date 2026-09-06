"""Bind public Memory target facts and exact outbox commands to Host grants.

The target mutation receipt is a real receipt, not the outbox's generating
cause. The latter is explicitly unavailable in the current outbox wire.
No model, private Memory SQL, or reconstruction of SDK identifier algorithms.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

from simple_harness.runtime import (
    MemoryScopeRef,
    ProspectiveSignalIntent,
    issue_prospective_signal_authority,
)

from deskpet.memory.s5c_store import S5cConflict, S5cStore, registration_signal_id
from deskpet.task_scope.protocol import canonical_hash


class PublicRegistrationAuthoritySource:
    def __init__(self, *, store: S5cStore, memory, clock: Callable[[], float] = time.time,
                 lifetime_seconds: float = 300.0):
        if (not callable(clock) or type(lifetime_seconds) not in (int, float)
                or not math.isfinite(lifetime_seconds) or not 0 < lifetime_seconds <= 86400):
            raise ValueError("prospective_source_clock_or_lifetime_invalid")
        self.store, self.memory, self.clock = store, memory, clock
        self.lifetime_seconds = lifetime_seconds

    async def prepare_registration(self, *, principal, entry):
        from simple_harness_memory import MemoryPrincipal, ProspectiveOutboxSourceView

        if type(principal) is not MemoryPrincipal or principal != self.store.principal:
            raise S5cConflict("s5c_source_principal_differs")
        cursor = await self.store.cursor()
        existing = await self.store.registration(entry.outbox_id)
        if existing is not None:
            # Revalidate immutable entry bytes; never renew an expired grant.
            await self.store.commit_registration(entry, existing.authority, expected_cursor=cursor)
            return existing.authority
        source = await self.memory.read_prospective_outbox_source(
            principal=principal, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash,
        )
        if (type(source) is not ProspectiveOutboxSourceView
                or source.subject != principal.actor_id
                or source.outbox_id != entry.outbox_id
                or source.outbox_payload_hash != entry.payload_hash
                or source.outbox_cause_status != "not_persisted"):
            raise S5cConflict("s5c_public_target_source_differs")
        source.target_scope.authorize(principal)
        expected = dict(schema_version=1, command=source.command,
            memory_id=source.target_memory_id, prospective_revision=source.target_revision,
            registration_revision=source.registration_revision,
            trigger=source.trigger.to_json(), trigger_hash=source.trigger_hash)
        if (entry.payload is None or dict(entry.payload) != expected
                or canonical_hash(expected) != entry.payload_hash
                or entry.created_at != source.outbox_created_at
                or entry.idempotency_key != source.outbox_id
                or entry.topic != f"memory.prospective.{source.command}.requested"):
            raise S5cConflict("s5c_public_outbox_source_differs")
        if source.command == "registration":
            kind = "registration_accepted"
            registration_ref = "host:prospective-registration:" + canonical_hash(
                [self.store.owner, entry.outbox_id, entry.payload_hash])
        elif source.command == "invalidation":
            kind = "registration_invalidated"
            original = await self.store.accepted_registration(
                memory_id=source.target_memory_id, revision=source.target_revision)
            if (original is None or original.authority.intent.trigger_hash != source.trigger_hash
                    or original.authority.intent.registration_revision != source.registration_revision
                    or (original.authority.intent.scope.kind.value, original.authority.intent.scope.owner_id)
                        != (source.target_scope.kind.value, source.target_scope.owner_id)):
                raise S5cConflict("s5c_invalidation_registration_missing")
            registration_ref = original.authority.intent.scheduler_registration_ref
        else:
            raise S5cConflict("s5c_registration_topic_unknown")
        issued_at = float(self.clock())
        if (not math.isfinite(issued_at) or not math.isfinite(entry.created_at)
                or not 0 <= entry.created_at <= issued_at):
            raise S5cConflict("s5c_registration_observation_time_invalid")
        # The exact SDK target receipt and outbox commitment serve separate
        # roles. Invalidation still refers to the historical target revision.
        receipt = source.target_mutation_receipt_ref
        intent = ProspectiveSignalIntent(
            signal_id=registration_signal_id(principal, entry.outbox_id, kind),
            subject=principal.actor_id,
            scope=MemoryScopeRef(source.target_scope.kind.value, source.target_scope.owner_id),
            target_memory_id=source.target_memory_id, target_revision=source.target_revision,
            signal_kind=kind, trigger=source.trigger, scheduler_registration_ref=registration_ref,
            registration_revision=source.registration_revision,
            signal_receipt_id=receipt.receipt_id, signal_receipt_hash=receipt.receipt_hash,
            observed_at=entry.created_at, transition_from=source.target_lifecycle_state,
            transition_to=source.target_lifecycle_state, outbox_id=entry.outbox_id,
            outbox_payload_hash=entry.payload_hash, run_id=source.target_run_id,
            operation_id=source.target_operation_id,
        )
        identity = canonical_hash(["host:prospective-target-grant/v1", self.store.owner,
                                   entry.outbox_id, source.source_hash])
        authority = issue_prospective_signal_authority(intent,
            authority_id="host:prospective-authority:" + identity,
            issued_at=issued_at, expires_at=issued_at + self.lifetime_seconds,
            nonce=identity, issuer_ref="host:prospective-signal/v1")
        try:
            # Persist the first observation before returning. The consumer's
            # subsequent commit is the existing exact idempotent path.
            await self.store.commit_registration(entry, authority, expected_cursor=cursor)
        except S5cConflict as exc:
            if str(exc) != "s5c_registration_replay_differs":
                raise
            winner = await self.store.registration(entry.outbox_id)
            if (winner is None or winner.authority.intent != intent
                    or winner.authority.authority_id != authority.authority_id
                    or winner.authority.nonce != authority.nonce
                    or winner.authority.issuer_ref != authority.issuer_ref):
                raise S5cConflict("s5c_concurrent_source_differs") from exc
            authority = winner.authority
        return authority
