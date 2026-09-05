"""Public SDK fact carriers backed by actual Host S1 and action transactions.

These tests validate the Host fact port, not SDK duplicate suppression. They use
the successor SDK protocol source overlay until its installed artifact is fixed.
"""

import sqlite3
from dataclasses import replace

import pytest
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.history_source_authority import (
    HostHistorySourceAuthority,
    HostHistorySourceError,
)
from deskpet.memory.human_memory_service import (
    QueueTurnRequest,
    build_foreground_turn_evidence,
)
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime, local_memory_principal
from tests.memory.test_primary_cognitive_evidence import PAYLOAD, fixture


async def pair(auth, service, text, delivery_key, *, enqueue=True):
    envelope, receipt = build_foreground_turn_evidence(
        subject=auth.subject, authority_ref=auth.authority_ref, delivery_key=delivery_key, text=text,
    )
    if enqueue:
        await service.enqueue_turn(QueueTurnRequest(None, delivery_key, text))
    else:
        await service._program.append_evidence(envelope, receipt)
    return envelope, receipt


async def suppress(runtime, action):
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    return await (await runtime.manager()).suppress(
        principal=runtime.principal(),
        request=SuppressionRequest(
            request_id=action.suppression_request_id, subject=runtime.principal().actor_id,
            scope_kind=SuppressionScopeKind.MEMORY, scope_ref=PAYLOAD["memory_id"],
            reason_code="user_forget", requested_at=action.committed_at,
        ),
    )


@pytest.mark.asyncio
async def test_first_cut_and_atomic_origin_survive_fresh_user_ack_replay_and_reopen(tmp_path):
    path, auth, service, actions = await fixture(tmp_path)
    authority = HostHistorySourceAuthority(path)
    principal = local_memory_principal()
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db", evidence_authority=HostEvidenceAuthority(path))
    try:
        old = await pair(auth, service, "Exact source", "first")
        origin = await authority.resolve_history_source(principal=principal, envelope=old[0], receipt=old[1])
        assert origin.proof_kind == "atomic" and origin.source_sequence == 1
        first = await actions.admit_action(payload=PAYLOAD, idempotency_key="forget")
        decision = await suppress(runtime, first)
        cut = await authority.resolve_history_forget_cut(principal=principal, decision=decision)
        assert cut.namespace == origin.namespace
        assert cut.through_sequence == 1
        assert cut.action_ref == first.evidence_id
        fresh = await pair(auth, service, "Exact source", "fresh")
        second = await authority.resolve_history_source(principal=principal, envelope=fresh[0], receipt=fresh[1])
        assert second.proof_kind == "atomic" and second.source_sequence == 2
        assert second.envelope_hash != origin.envelope_hash
        again = await actions.admit_action(payload=PAYLOAD, idempotency_key="forget")
        assert again == first
        assert await suppress(runtime, again) == decision
        reopened = HostHistorySourceAuthority(path)
        assert await reopened.resolve_history_forget_cut(principal=principal, decision=decision) == cut
        assert await reopened.resolve_history_source(principal=principal, envelope=fresh[0], receipt=fresh[1]) == second
        assert cut.action_hash == (await HostEvidenceAuthority(path).read_admitted(first.evidence_id))[0].envelope_hash
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_preexisting_s1_after_cut_queue_is_legacy_not_fresh_and_unqueued_is_unknown(tmp_path):
    path, auth, service, actions = await fixture(tmp_path)
    authority = HostHistorySourceAuthority(path)
    principal = local_memory_principal()
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db", evidence_authority=HostEvidenceAuthority(path))
    try:
        old = await pair(auth, service, "Old delayed source", "old", enqueue=False)
        assert await authority.resolve_history_source(principal=principal, envelope=old[0], receipt=old[1]) is None
        action = await actions.admit_action(payload=PAYLOAD, idempotency_key="forget")
        decision = await suppress(runtime, action)
        cut = await authority.resolve_history_forget_cut(principal=principal, decision=decision)
        assert cut.through_sequence == 0
        await service.enqueue_turn(QueueTurnRequest(None, "old", "Old delayed source"))
        origin = await authority.resolve_history_source(principal=principal, envelope=old[0], receipt=old[1])
        assert origin.source_sequence > cut.through_sequence
        assert origin.proof_kind == "legacy_before_only"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_legacy_action_does_not_acquire_a_cut_on_retry(tmp_path):
    path, auth, service, actions = await fixture(tmp_path)
    envelope, receipt = actions._pair(PAYLOAD, "old-action")
    await service._program.append_evidence(envelope, receipt)
    legacy = await actions.find_action(payload=PAYLOAD, idempotency_key="old-action")
    await pair(auth, service, "Later source", "later")
    assert await actions.admit_action(payload=PAYLOAD, idempotency_key="old-action") == legacy
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db", evidence_authority=HostEvidenceAuthority(path))
    try:
        decision = await suppress(runtime, legacy)
        assert await HostHistorySourceAuthority(path).resolve_history_forget_cut(
            principal=runtime.principal(), decision=decision,
        ) is None
        assert (await HostEvidenceAuthority(path).read_admitted(legacy.evidence_id))[0].envelope_hash == envelope.envelope_hash
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_public_origin_rejects_foreign_principal_and_different_admitted_receipt(tmp_path):
    path, auth, service, _ = await fixture(tmp_path)
    authority = HostHistorySourceAuthority(path)
    principal = local_memory_principal()
    first = await pair(auth, service, "Original", "first")
    other = await pair(auth, service, "Different", "other")
    with pytest.raises(HostHistorySourceError, match="host_history_subject_mismatch"):
        await authority.resolve_history_source(
            principal=replace(principal, actor_id="other-owner"), envelope=first[0], receipt=first[1],
        )
    with pytest.raises(HostHistorySourceError, match="host_history_pair_mismatch"):
        await authority.resolve_history_source(principal=principal, envelope=first[0], receipt=other[1])


@pytest.mark.asyncio
async def test_action_cut_rolls_back_with_failed_action_and_first_success_owns_cut(tmp_path, monkeypatch):
    path, auth, service, actions = await fixture(tmp_path)
    await pair(auth, service, "Before failed action", "first")
    original_append = actions._program.append_evidence_tx

    async def fail_after_insert(*args, **kwargs):
        await original_append(*args, **kwargs)
        raise RuntimeError("simulated_action_before_commit")

    monkeypatch.setattr(actions._program, "append_evidence_tx", fail_after_insert)
    with pytest.raises(RuntimeError, match="simulated_action_before_commit"):
        await actions.admit_action(payload=PAYLOAD, idempotency_key="forget")
    assert await actions.find_action(payload=PAYLOAD, idempotency_key="forget") is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 1
    monkeypatch.setattr(actions._program, "append_evidence_tx", original_append)
    await pair(auth, service, "Before first successful action", "second")
    action = await actions.admit_action(payload=PAYLOAD, idempotency_key="forget")
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db", evidence_authority=HostEvidenceAuthority(path))
    try:
        cut = await HostHistorySourceAuthority(path).resolve_history_forget_cut(
            principal=runtime.principal(), decision=await suppress(runtime, action),
        )
        assert cut.through_sequence == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_same_subject_and_primary_in_fresh_stores_do_not_share_source_epoch(tmp_path):
    origins = []
    for name in ("first-store", "second-store"):
        root = tmp_path / name
        root.mkdir()
        path, auth, service, _ = await fixture(root)
        envelope, receipt = await pair(auth, service, "Same source", "same-key")
        origins.append(await HostHistorySourceAuthority(path).resolve_history_source(
            principal=local_memory_principal(), envelope=envelope, receipt=receipt,
        ))
    assert origins[0].evidence_id == origins[1].evidence_id
    assert origins[0].namespace.source_stream == origins[1].namespace.source_stream
    assert origins[0].namespace.store_epoch != origins[1].namespace.store_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", [None, True])
async def test_composed_runtime_rejects_protocol_only_or_invalid_enforcement_capability(tmp_path, capability):
    class ProtocolOnlyManager:
        history_source_enforcement_version = capability
        closed = False

        async def close(self):
            self.closed = True

        async def register_principal_owner(self, *_):
            raise AssertionError("unsupported enforcement must reject before registration")

    manager = ProtocolOnlyManager()

    async def build(_path, **kwargs):
        assert kwargs["history_source_authority"] is authority
        return manager

    authority = HostHistorySourceAuthority(tmp_path / "state.db")
    runtime = HumanMemoryV7Runtime(
        tmp_path / "memory.db", backend_factory=build, history_source_authority=authority,
    )
    with pytest.raises(RuntimeError, match="memory_history_source_enforcement_unavailable"):
        await runtime.manager()
    assert manager.closed
