"""Installed SDK SQLite integration; real public mutations, no Provider/model.

Only registration issuance is a fixture: it binds the exact admitted Host Run
and CREATE operation below. Production source resolution remains a T3 gap.
"""

from dataclasses import replace

import pytest
from deskpet.memory.analysis_proposal import (
    admitted_item,
    compile_operation,
    derive_span,
)
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    HOST_PUBLIC_TURN_FILTER_POLICY,
    build_foreground_turn_evidence,
)
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_schema import initialize_s5c_state_db
from deskpet.memory.s5c_store import (
    HostProspectiveSignalAuthority,
    S5cStore,
    registration_signal_id,
)
from simple_harness.runtime import (
    EvidenceRef,
    MemoryMutationPlan,
    MemoryScopeRef,
    ProspectiveSignalIntent,
    ProspectiveTimeTrigger,
    issue_prospective_signal_authority,
)
from simple_harness_memory import MemoryScope
from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
from simple_harness_memory.core.mutations import InformationClassificationPolicy
from tests.memory.test_s5c_store import P


async def fixture(tmp_path, clock, fault=None, *, action_authority=None, return_plan=False):
    path = tmp_path / "state.db"
    program = HumanMemoryProgramStore(path)
    await program.initialize_subject(P.actor_id)
    text = "Remind me to send the report at the specified time."
    envelope, receipt = build_foreground_turn_evidence(
        subject=P.actor_id,
        authority_ref="host:test-local-owner",
        delivery_key="s5c-test-source",
        text=text,
    )
    await program.append_evidence(envelope, receipt)
    item = admitted_item(envelope, receipt)
    span = derive_span(item, text, span_id="s5c-test-span")
    operation = compile_operation(
        {
            "operation_id": "s5c-create",
            "memory_type": "prospective",
            "prospective": {
                "action": "send the report",
                "trigger_at_iso": "1970-01-01T00:00:30+00:00",
                "timezone": "UTC",
            },
        },
        span,
        item=item,
        now=20.0,
    )
    await initialize_s5c_state_db(path)
    backend = SQLiteHumanMemoryBackend(
        tmp_path / "memory.db",
        now=lambda: clock[0],
        fault_injector=fault,
        supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
        evidence_authority=HostEvidenceAuthority(path),
        prospective_signal_authority=HostProspectiveSignalAuthority(path, P),
        memory_action_authority=action_authority,
        classification_policy=InformationClassificationPolicy(
            policy_id="s5c-test-classification",
            policy_version="1",
            authority_ref="host:classification/v1",
            required_privacy_class="personal",
            required_information_attributes=(),
        ),
    )
    await backend.initialize()
    try:
        await backend.ingest_committed_evidence(envelope, receipt)
        plan = MemoryMutationPlan(
            plan_id="s5c-test-plan",
            run_id=envelope.run_id,
            turn_id="s5c-test-turn",
            subject=P.actor_id,
            base_revision=1,
            outcome="mutate",
            operations=(operation,),
            disclosure_context=envelope.disclosure_context,
            evidence_refs=(
                EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),
            ),
            idempotency_key="s5c-test-plan",
        )
        await backend.apply_memory_mutation_plan(
            principal=P, scope=MemoryScope.personal(P.actor_id), plan=plan
        )
    except BaseException:
        await backend.close()
        raise

    class ExactFixtureSource:
        calls = 0

        async def prepare_registration(self, *, principal, entry):
            assert principal == P
            self.calls += 1
            payload = entry.payload
            signal_id = registration_signal_id(
                P, entry.outbox_id, "registration_accepted"
            )
            intent = ProspectiveSignalIntent(
                signal_id=signal_id,
                subject=P.actor_id,
                scope=MemoryScopeRef.personal(P.actor_id),
                target_memory_id=payload["memory_id"],
                target_revision=payload["prospective_revision"],
                signal_kind="registration_accepted",
                trigger=ProspectiveTimeTrigger(30.0, "UTC"),
                scheduler_registration_ref="host:test-registration:" + entry.outbox_id,
                registration_revision=payload["registration_revision"],
                signal_receipt_id=entry.outbox_id,
                signal_receipt_hash=entry.payload_hash,
                observed_at=entry.created_at,
                transition_from="pending",
                transition_to="pending",
                outbox_id=entry.outbox_id,
                outbox_payload_hash=entry.payload_hash,
                run_id=envelope.run_id,
                operation_id=operation.operation_id,
            )
            return issue_prospective_signal_authority(
                intent,
                authority_id="host:test-authority:" + signal_id,
                issued_at=20.0,
                expires_at=100.0,
                nonce="test-fixed-" + signal_id,
                issuer_ref="host:prospective-signal/v1",
            )

    result = (path, backend, ExactFixtureSource())
    return (*result, plan) if return_plan else result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point", ["prospective.after_commit", "s5c.registration_result.before_commit"]
)
async def test_real_sdk_lost_ack_replays_same_result_after_authority_expiry(
    tmp_path, point
):
    clock, fired = [20.0], False

    def fault(p):
        nonlocal fired
        if p == point and not fired:
            fired = True
            raise ConnectionError(point)

    path, backend, source = await fixture(tmp_path, clock, fault)
    try:
        store = S5cStore(path, P, fault_inject=fault)
        with pytest.raises(ConnectionError):
            await ProspectiveRegistrationConsumer(store, backend, source).run_once()
        prepared = (await store.pending_registrations())[0]
        clock[0] = 101.0
        # Reopen both repositories and use the same canonical owner in a later
        # session. No raw SQL state updates, new grant, new signal, or LLM call.
        await backend.close()
        backend = SQLiteHumanMemoryBackend(
            tmp_path / "memory.db",
            now=lambda: clock[0],
            supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
            prospective_signal_authority=HostProspectiveSignalAuthority(path, P),
        )
        await backend.initialize()
        store = S5cStore(path, replace(P, session_id="later-session"))
        await ProspectiveRegistrationConsumer(store, backend, source).run_once()
        restored = await store.registration(prepared.entry.outbox_id)
        assert restored.result is not None
        assert restored.reference == prepared.reference
        assert restored.result.decided_at == 20.0
        assert source.calls == 1
        replay = await backend.apply_prospective_signal(
            principal=P,
            scope=MemoryScope.personal(P.actor_id),
            reference=prepared.reference,
        )
        assert replay == restored.result
        async with backend.connection.execute(
            "SELECT COUNT(*) FROM prospective_signal_results"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 1
        assert (await backend.read_occurrence_inbox(principal=P)).entries == ()
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_real_sdk_unconsumed_expired_grant_stays_prepared_without_reissue(
    tmp_path,
):
    clock = [20.0]
    path, backend, source = await fixture(tmp_path, clock)
    try:
        store = S5cStore(path, P)
        page = await backend.read_outbox(principal=P)
        entry = next(
            e
            for e in page.entries
            if e.topic == "memory.prospective.registration.requested"
        )
        authority = await source.prepare_registration(principal=P, entry=entry)
        await store.commit_registration(entry, authority, expected_cursor=None)
        clock[0] = 101.0
        for _ in range(2):
            with pytest.raises(
                ValueError, match="prospective_signal_authority_rejected"
            ):
                await ProspectiveRegistrationConsumer(
                    S5cStore(path, P), backend, source
                ).run_once()
        assert source.calls == 1
        assert (await store.registration(entry.outbox_id)).result is None
        async with backend.connection.execute(
            "SELECT COUNT(*) FROM prospective_signal_results"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await backend.close()
