"""Real public Memory consumer plus signed Host source/claim; no physical send claim."""
import asyncio
from dataclasses import replace

import pytest
import simple_harness_memory as m
from simple_harness.observability import RecordingSink
from deskpet.memory.current_input_authority import HostCurrentInputAuthority
from deskpet.memory.current_input_source import read_current_input_source
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.execution.foreground_queue import ForegroundQueueStore, ContextLineage
from deskpet.execution.foreground_runtime import _execution_session_id
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from tests.memory.test_current_input_source import env, configured, admit, ok


async def setup(env, kind="current_user", *, disclosure_selection=None):
    if disclosure_selection is None:
        config = await configured(env)
    else:
        from tests.memory.test_trusted_disclosure import command
        config = ok(await command(env, "disclosure.configure", disclosure_selection))
    turn = ok(await admit(env, config, kind=kind))
    store = ForegroundQueueStore(env.path)
    candidate = await store.read_next_preparation_candidate(env.auth.subject)
    # Only a real Host claim is needed at the preparation boundary; no fake SDK start.
    draft = await store.prepare_candidate(subject=env.auth.subject, expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("input-source-control", 1, "a" * 64), idempotency_key="prepare")
    claim = await store.claim_next(subject=env.auth.subject, owner_id="input-owner", claim_idempotency_key="claim",
        preparation_draft_id=draft.draft_id, preparation_draft_hash=draft.draft_hash, lease_seconds=60)
    request_id = f"foreground-request-{turn['turn_ref']}"
    run_id = SdkRuntimeIngress._compute_run_id(_execution_session_id(claim.host_run_id), request_id, turn["turn_ref"]).value
    context = await resolve_current_disclosure(db_path=env.path, subject=env.auth.subject,
        turn_id=turn["turn_ref"], run_id=run_id, request_id=request_id)
    fact = await read_current_input_source(db_path=env.path, subject=env.auth.subject, turn_id=turn["turn_ref"])
    binding = m.CurrentInputBindingV1(turn["turn_ref"], request_id, m.HistoryEvidenceBinding(fact["envelope"], fact["receipt"]))
    principal = m.MemoryPrincipal("input-deployment", "input-household", env.auth.subject, "input-session")
    env.memory_principal = principal
    env.claim = claim
    return principal, context, binding


async def manager(env, authority=None, sink=None, *, register=True):
    result = await m.build_human_memory_v7(env.path.with_name("memory.db"),
        current_input_authority=authority or HostCurrentInputAuthority(env.path, principal=env.memory_principal),
        history_source_authority=HostHistorySourceAuthority(env.path),
        supported_filter_policies=frozenset({"host-public-turn/v1"}))
    if register:
        await result.register_principal_owner(env.memory_principal, m.MemoryScope.personal(env.memory_principal.actor_id))
    return result if sink is None else m.MemoryManager(result.backend, None, observability_sink=sink)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current_user", "public_material"])
async def test_real_input_allowed_ordinary_history_denied_and_observed(env, kind):
    principal, context, binding = await setup(env, kind)
    sink = RecordingSink()
    sdk = await manager(env, sink=sink)
    try:
        view = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        assert view.invocation_input_allowed and view.reason == "current_input_visible"
        assert view.declaration_kind == kind
        assert view.final_audience_disclosure_authorized is False
        assert view.operation_observation.outcome == "input_usable"
        assert view.operation_observation.persistence_status == "host_persistence_unverified"
        ordinary = await sdk.check_history_visibility(principal=principal, disclosure_context=context, bindings=(binding.evidence,))
        assert not ordinary.items[0].visible  # unchanged ordinary recipient gate
        await sdk.close()  # public close drains the asynchronous observability sink
        records = [v for v in sink.events() if v.operation == "check_current_input_visibility"]
        assert len(records) == 1
        assert records[0].attributes["fingerprint"] == view.operation_observation.observation_hash
        assert binding.evidence.envelope.sanitized_payload["text"] not in str(records[0].to_dict())
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_current_input_suppression_reopen_and_foreign_run(env):
    principal, context, binding = await setup(env)
    sdk = await manager(env)
    try:
        first = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        assert first.invocation_input_allowed
        foreign = await sdk.check_current_input_visibility(principal=principal,
            disclosure_context=replace(context, run_id="different-runtime"), binding=binding)
        assert not foreign.invocation_input_allowed
        await sdk.suppress(principal=principal, request=m.SuppressionRequest(
            "actual-forget", principal.actor_id, m.SuppressionScopeKind.EVIDENCE,
            binding.evidence.envelope.evidence_id, "user_forget", 1.0))
    finally:
        await sdk.close()
    sdk = await manager(env)
    try:
        after = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        assert not after.invocation_input_allowed and after.reason == "history_suppressed"
        assert after.operation_observation.outcome == "input_denied"
        assert after.authority_epoch > first.authority_epoch
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_item_authority_tamper_is_rejected_and_observed(env):
    principal, context, binding = await setup(env)
    actual = HostCurrentInputAuthority(env.path, principal=env.memory_principal)
    class Tampered:
        async def resolve_current_input(self, **kwargs):
            proof = await actual.resolve_current_input(**kwargs)
            import simple_harness as h
            item = replace(proof.admitted.item_authority, actor_role=h.EvidenceActorRole.TOOL)
            return replace(proof, admitted=replace(proof.admitted, item_authority=item))
    sdk = await manager(env, Tampered())
    try:
        with pytest.raises(ValueError) as caught:
            await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        assert caught.value.operation_observation.outcome == "rejected"
        assert caught.value.operation_observation.snapshot_hash is None
    finally:
        await sdk.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,outcome", [(asyncio.CancelledError, "cancelled"), (OSError, "failed")])
async def test_actual_manager_cancel_and_source_read_error_have_safe_observation(env, error_type, outcome):
    principal, context, binding = await setup(env)
    class Failing:
        async def resolve_current_input(self, **kwargs):
            raise error_type("private-diagnostic-not-in-observation")
    sink = RecordingSink()
    sdk = await manager(env, Failing(), sink)
    try:
        with pytest.raises(error_type) as caught:
            await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        observation = caught.value.operation_observation
        assert observation.outcome == outcome
        assert "private-diagnostic" not in str(observation.to_json())
        await sdk.close()  # drain real async sink before inspecting delivery
        records = [v for v in sink.events() if v.operation == "check_current_input_visibility"]
        assert len(records) == 1
        assert records[0].attributes["fingerprint"] == observation.observation_hash
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_current_input_requires_full_registered_owner(env):
    principal, context, binding = await setup(env)
    sdk = await manager(env, register=False)
    try:
        with pytest.raises(m.MemoryOwnershipConflict):
            await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        await sdk.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
        for changed in (replace(principal, deployment_id="foreign-deployment"),
                        replace(principal, household_id="foreign-household")):
            with pytest.raises(m.MemoryOwnershipConflict) as caught:
                await sdk.check_current_input_visibility(principal=changed, disclosure_context=context, binding=binding)
            assert caught.value.operation_observation.outcome == "rejected"
        assert (await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)).invocation_input_allowed
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_claim_changes_during_real_origin_read_reject_input(env, monkeypatch):
    principal, context, binding = await setup(env)
    sdk = await manager(env)
    actual = HostHistorySourceAuthority.resolve_history_source
    async def changed(authority, **kwargs):
        origin = await actual(authority, **kwargs)
        await ForegroundQueueStore(env.path).request_control(host_run_id=env.claim.host_run_id,
            subject=principal.actor_id, generation=env.claim.generation,
            control_kind="stop", reason="actual-stop-during-read", idempotency_key="late-stop")
        return origin
    monkeypatch.setattr(HostHistorySourceAuthority, "resolve_history_source", changed)
    try:
        view = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)
        assert not view.invocation_input_allowed and view.reason == "current_input_authority_unverifiable"
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_ingested_placeholder_cannot_claim_current_input_namespace(env):
    principal, context, binding = await setup(env)
    sdk = await manager(env, register=False)
    try:
        admitted = await sdk.ingest_committed_evidence(binding.evidence.envelope, binding.evidence.receipt)
        assert admitted is not None  # real public ingest creates the legacy placeholder
        placeholder = m.MemoryPrincipal(principal.actor_id, principal.actor_id, principal.actor_id, principal.session_id)
        observed = await sdk.check_current_input_visibility(principal=placeholder, disclosure_context=context, binding=binding)
        assert not observed.invocation_input_allowed
        assert observed.reason == "current_input_authority_unverifiable"
        await sdk.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
        assert (await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding)).invocation_input_allowed
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_input_batch_exact_exception_does_not_spread_to_old_sources_or_unowned_refs(env):
    from deskpet.memory.human_memory_service import build_foreground_turn_evidence
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    from deskpet.operation_audit.current_inputs import request_hash
    principal, context, binding = await setup(env)
    old_pair = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=env.auth.authority_ref,
        delivery_key="old-private-source", text="Earlier private health and family facts.")
    await HumanMemoryProgramStore(env.path).append_evidence(*old_pair)
    # Unknown exact public recall/short tuples must not inherit an input grant.
    # These two negatives do not claim materialized typed/short product evidence.
    values = (binding.evidence, m.HistoryEvidenceBinding(*old_pair),
        m.HistoryRecallBinding("unowned-result", "a" * 64, "unowned-item", "b" * 64),
        m.HistoryShortHorizonBinding("unowned-audit", "unowned-chunk", "c" * 64))
    sdk = await manager(env)
    try:
        ordinary = await sdk.check_history_visibility(principal=principal, disclosure_context=context, bindings=values)
        assert all(not item.visible for item in ordinary.items)
        batch = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding, bindings=values)
        assert batch.invocation_input_allowed
        assert [i.visible for i in batch.history_visibility.items] == [True, False, False, False]
        assert [i.reason for i in batch.history_visibility.items[1:]] == [i.reason for i in ordinary.items[1:]]
        assert batch.request_hash == request_hash(principal, context, binding, values)
        reverse = await sdk.check_current_input_visibility(principal=principal, disclosure_context=context, binding=binding, bindings=values[::-1])
        assert reverse.request_hash != batch.request_hash
        assert reverse.operation_observation.request_hash == reverse.request_hash
        assert [i.visible for i in reverse.history_visibility.items] == [False, False, False, True]
    finally:
        await sdk.close()
