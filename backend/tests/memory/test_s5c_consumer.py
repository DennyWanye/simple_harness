"""T3 consumer contract tests. Scripted ports are explicit unit-test doubles."""

import asyncio
import hashlib
from dataclasses import replace

import pytest
from deskpet.execution.recovery_fence import HumanMemoryRecoveryCoordinator
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_schema import initialize_s5c_state_db
from deskpet.memory.s5c_store import S5cConflict, S5cStore, registration_signal_id
from simple_harness.contracts import canonical_json
from simple_harness_memory.core.lifecycle_results import ProspectiveSignalApplyResult
from simple_harness_memory.core.occurrence import OutboxPageV1
from tests.memory.test_s5c_store import P, counts, ready, registration


class Source:
    def __init__(self, *pairs):
        self.authorities = {e.outbox_id: a for e, a in pairs}
        self.calls = []

    async def prepare_registration(self, *, principal, entry):
        assert principal == P
        self.calls.append(entry.outbox_id)
        return self.authorities[entry.outbox_id]


class Memory:
    def __init__(self, source, entries):
        self.source, self.entries = source, entries
        self.calls = []
        self.results = {}
        self.lose_ack = False

    async def read_outbox(self, *, principal, states, after, limit):
        assert principal == P
        entries = [
            e
            for e in self.entries
            if after is None or (e.created_at, e.outbox_id) > after
        ]
        entries = entries[:limit]
        tail = (
            (entries[-1].created_at, entries[-1].outbox_id)
            if len(entries) == limit
            else None
        )
        return OutboxPageV1(tuple(entries), tail)

    async def apply_prospective_signal(self, *, principal, scope, reference):
        assert principal == P
        self.calls.append(reference)
        authority = next(
            a
            for a in self.source.authorities.values()
            if a.authority_id == reference.authority_id
        )
        i = authority.intent
        assert scope.owner_id == i.scope.owner_id
        result = self.results.setdefault(
            reference.authority_id,
            ProspectiveSignalApplyResult(
                "result-" + i.signal_id,
                i.signal_id,
                "decision-" + i.signal_id,
                i.target_memory_id,
                i.target_revision,
                i.target_revision,
                i.transition_to,
                "acknowledged",
                "prospective_registration_acknowledged",
                i.observed_at,
            ),
        )
        if self.lose_ack:
            self.lose_ack = False
            raise ConnectionError("unit-test-lost-ack")
        return result


@pytest.mark.asyncio
async def test_historical_pending_pagination_and_restart_skip_applied(tmp_path):
    path, store = await ready(tmp_path)
    pairs = [registration("outbox-1", 10), registration("outbox-2", 11)]
    source = Source(*pairs)
    memory = Memory(source, [e for e, _ in pairs])
    await ProspectiveRegistrationConsumer(store, memory, source).run_once(page_size=1)
    assert counts(path)[:2] == [4, 2]
    assert len(memory.calls) == 2
    # Memory may still report original outbox rows pending: the Host receipt
    # is the replay checkpoint, not a forged remote row-state transition.
    await ProspectiveRegistrationConsumer(S5cStore(path, P), memory, source).run_once()
    assert len(memory.calls) == 2
    assert len(source.calls) == 2
    assert (await store.registration("outbox-1")).result == memory.results[
        pairs[0][1].authority_id
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        "s5c.registration.after_commit",
        "s5c.registration_result.before_commit",
        "s5c.registration_result.after_commit",
        "memory_lost_ack",
    ],
)
async def test_recovery_uses_same_ref_without_reissuing_or_changing_cursor(
    tmp_path, point
):
    path, store = await ready(tmp_path)
    pair = registration()
    source, fired = Source(pair), False
    memory = Memory(source, [pair[0]])

    def fault(p):
        nonlocal fired
        if p == point and not fired:
            fired = True
            raise ConnectionError(point)

    memory.lose_ack = point == "memory_lost_ack"
    with pytest.raises(ConnectionError):
        await ProspectiveRegistrationConsumer(
            S5cStore(path, P, fault_inject=fault), memory, source
        ).run_once()
    before = await store.cursor()
    await ProspectiveRegistrationConsumer(S5cStore(path, P), memory, source).run_once()
    assert await store.cursor() == before == (10.0, "outbox-1")
    assert counts(path)[:2] == [2, 1]
    assert len(source.calls) == 1
    assert len(set(memory.calls)) == 1


@pytest.mark.asyncio
async def test_expired_unapplied_authority_is_not_renewed_or_skipped(tmp_path):
    path, store = await ready(tmp_path)
    pair = registration()
    source = Source(pair)
    memory = Memory(source, [pair[0]])

    async def expired(**kwargs):
        raise ValueError("prospective_signal_authority_expired")

    memory.apply_prospective_signal = expired
    for _ in range(2):
        with pytest.raises(ValueError, match="authority_expired"):
            await ProspectiveRegistrationConsumer(
                S5cStore(path, P), memory, source
            ).run_once()
    assert len(source.calls) == 1
    assert counts(path)[:2] == [1, 1]
    assert (await store.registration("outbox-1")).authority == pair[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        dict(signal_id="wrong"),
        dict(memory_id="wrong"),
        dict(committed_revision=2),
        dict(outcome="ignored"),
        dict(lifecycle_state="triggered"),
    ],
)
async def test_wrong_result_never_marks_registration_applied(tmp_path, drift):
    path, store = await ready(tmp_path)
    pair = registration()
    source = Source(pair)
    memory = Memory(source, [pair[0]])
    original = memory.apply_prospective_signal

    async def mismatched(**kwargs):
        return replace(await original(**kwargs), **drift)

    memory.apply_prospective_signal = mismatched
    with pytest.raises(S5cConflict, match="result_differs"):
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    assert counts(path)[:2] == [1, 1]


@pytest.mark.asyncio
async def test_malformed_or_reordered_page_cannot_advance_past_missing_source(tmp_path):
    path, store = await ready(tmp_path)
    first, second = registration(), registration("outbox-2", 11)
    source = Source(first, second)
    memory = Memory(source, [])

    async def reordered(**kwargs):
        return OutboxPageV1((second[0], first[0]), None)

    memory.read_outbox = reordered
    with pytest.raises(S5cConflict, match="page_order"):
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    assert counts(path)[:2] == [0, 0]
    assert source.calls == []
    memory = Memory(source, [replace(first[0], payload_hash="f" * 64), second[0]])
    with pytest.raises(S5cConflict, match="source_differs"):
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    assert counts(path)[:2] == [0, 0]


@pytest.mark.asyncio
async def test_unrelated_topics_do_not_starve_later_registration_or_get_acked(tmp_path):
    path, store = await ready(tmp_path)
    pair = registration("outbox-9", 20)
    source = Source(pair)
    unrelated = [
        replace(registration(f"other-{i}", i)[0], topic="memory.other.requested")
        for i in range(5)
    ]
    memory = Memory(source, unrelated + [pair[0]])
    consumer = ProspectiveRegistrationConsumer(store, memory, source)
    for _ in range(4):
        await consumer.run_once(page_size=2, max_pages=1)
    assert counts(path)[:2] == [2, 1]
    assert source.calls == ["outbox-9"]
    assert len(memory.calls) == 1


@pytest.mark.asyncio
async def test_receipt_replay_conflict_and_other_owner_are_rejected(tmp_path):
    path, store = await ready(tmp_path)
    pair = registration()
    source = Source(pair)
    memory = Memory(source, [pair[0]])
    await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    prepared = await store.registration("outbox-1")
    assert (
        await store.commit_registration_result(prepared.reference, prepared.result)
        == prepared.result
    )
    with pytest.raises(S5cConflict, match="result_replay_differs"):
        await store.commit_registration_result(
            prepared.reference, replace(prepared.result, decision_id="different")
        )
    other = S5cStore(path, replace(P, household_id="other"))
    assert await other.registration("outbox-1") is None
    with pytest.raises(S5cConflict, match="not_found"):
        await other.commit_registration_result(prepared.reference, prepared.result)


@pytest.mark.asyncio
async def test_concurrent_consumers_deliver_one_fixed_registration_result(tmp_path):
    path, store = await ready(tmp_path)
    pair = registration()
    source = Source(pair)
    memory = Memory(source, [pair[0]])
    # Two independent store/consumer objects contend on the same durable row.
    await asyncio.gather(
        *(
            ProspectiveRegistrationConsumer(
                S5cStore(path, P), memory, source
            ).run_once()
            for _ in range(2)
        )
    )
    assert counts(path)[:2] == [2, 1]
    assert len(set(memory.calls)) == 1
    assert (await store.registration(pair[0].outbox_id)).result is not None


@pytest.mark.asyncio
async def test_invalidation_is_delivered_in_source_order_and_never_claims(tmp_path):
    path, store = await ready(tmp_path)
    entry, authority = registration()
    payload = {**entry.payload, "command": "invalidation"}
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    invalidation = replace(
        entry,
        outbox_id="invalidate",
        idempotency_key="invalidate",
        topic="memory.prospective.invalidation.requested",
        payload=payload,
        payload_hash=digest,
        created_at=20.0,
    )
    intent = replace(
        authority.intent,
        signal_kind="registration_invalidated",
        signal_id=registration_signal_id(P, "invalidate", "registration_invalidated"),
        outbox_id="invalidate",
        outbox_payload_hash=digest,
        observed_at=20.0,
    )
    invalidated_authority = replace(
        authority, intent=intent, authority_id="invalidate-authority"
    )
    source = Source((entry, authority), (invalidation, invalidated_authority))
    memory = Memory(source, [entry, invalidation])
    await ProspectiveRegistrationConsumer(store, memory, source).run_once(page_size=1)
    assert [ref.authority_id for ref in memory.calls] == [
        authority.authority_id,
        invalidated_authority.authority_id,
    ]
    assert counts(path) == [4, 2, 0, 0]
    assert (
        await store.registration("invalidate")
    ).result.outcome.value == "acknowledged"


@pytest.mark.asyncio
async def test_missing_source_is_explicit_and_does_not_skip_cursor(tmp_path):
    path, store = await ready(tmp_path)
    pair = registration()
    source = Source()
    memory = Memory(source, [pair[0]])
    with pytest.raises(KeyError):
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    assert counts(path) == [0, 0, 0, 0]
    assert memory.calls == []


@pytest.mark.asyncio
async def test_closed_host_recovery_fence_prevents_remote_delivery(tmp_path):
    path = tmp_path / "state.db"
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        path, export_root=tmp_path / "recovery"
    )
    await initialize_s5c_state_db(path)
    store = S5cStore(path, P)
    pair = registration()
    await store.commit_registration(*pair, expected_cursor=None)
    await coordinator.begin_close()
    source = Source(pair)
    memory = Memory(source, [pair[0]])
    with pytest.raises(Exception, match="human_memory_ingress_fenced"):
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    assert source.calls == memory.calls == []
    assert counts(path)[:2] == [1, 1]
