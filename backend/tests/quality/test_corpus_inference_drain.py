"""New multi-source drain controls; no quality Provider and no legacy reruns."""
import pytest
import asyncio
from dataclasses import replace
import simple_harness_memory as m
import simple_harness as h
import deskpet.quality.corpus_inference_drain as drain_module

from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import QueueTurnRequest, build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.quality.corpus_c03 import SETUPS
from deskpet.quality.corpus_c03_inference import prepare_c03_20_setup
from deskpet.quality.corpus_inference_drain import InferenceFixtureAuthority, drain_inference_setup
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_create_new_runtime import fixture
from tests.memory.test_primary_visibility import classification_policy, FILTERS
from tests.quality.test_corpus_c03_inference import InferenceProvider
import tests.execution.test_primary_foreground_runtime as runtime_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['accepted', 'invalid_reason', 'cancel_audit_pending'])
async def test_inference_two_actual_jobs_applied_and_reopen_exact_finalize(tmp_path, monkeypatch, mode):
    invalid_reason = mode == 'invalid_reason'
    if invalid_reason:
        original_result = drain_module._fixture_result
        def invalid_result(request, identity):
            result = original_result(request, identity)
            body = h.thaw_json(result.structured_result)
            body['reason'] = body.pop('closure_reason')
            return replace(result, structured_result=body)
        monkeypatch.setattr(drain_module, '_fixture_result', invalid_result)
    auth = local_owner_auth()
    source, _, service, _, _ = await fixture(tmp_path / 'source')
    text = SETUPS['C03-20'][0]
    key = 'inference-drain-source'
    await service.enqueue_turn(QueueTurnRequest(None, key, text))
    expected, _ = build_foreground_turn_evidence(subject=auth.subject,
        authority_ref=auth.authority_ref, delivery_key=key, text=text)
    original = await HostEvidenceAuthority(source).read_admitted(expected.evidence_id)
    provider = InferenceProvider()
    runtime, stack, _ = await runtime_fixture.build(tmp_path / 'source', source, provider)
    try:
        await runtime.after_enqueue(subject=auth.subject)
        await runtime.drain()
        assert runtime.last_error is None and len(provider.requests) == 1
        memory = runtime.history_memory
        source_manager = await memory.manager()
        await source_manager.register_principal_owner(memory.principal(), m.MemoryScope.personal(auth.subject))
        worker = MemoryIngestionOutboxWorker(source, memory.manager, owner_id='drain-source-ingestion')
        assert await worker.run_once() == 'delivered'
        run_ids = await PrimaryConversationAuthority(source, subject=auth.subject).completed_run_ids()
        assert len(run_ids) == 1
    finally:
        await runtime.close()
        await stack.close()

    scoring, _, _, _, _ = await fixture(tmp_path / 'scoring')
    principal = m.MemoryPrincipal('host', 'household', auth.subject, 'corpus-fixture')
    now = [1788660000.0]
    config = dict(classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(scoring), clock=lambda: now[0])
    authority = InferenceFixtureAuthority()
    manager = await m.build_human_memory_v7(tmp_path / 'memory.db',
        analysis_delivery_authority=authority, **config)
    try:
        seed = await prepare_c03_20_setup(source_path=source, scoring_path=scoring,
            manager=manager, principal=principal, host_run_id=run_ids[0],
            original_envelope=original[0], original_receipt=original[1], scenario_time=1788660000.0)
        graph = await manager.get_twin_graph_view(principal=principal)
        args = dict(case_id='C03-20', source_path=source, scoring_path=scoring,
            host_run_id=run_ids[0], principal=principal, group=seed['source_group'],
            plan=seed['plan'], applied=seed['applied'], clock=config['clock'])
        interrupted = []
        if mode == 'cancel_audit_pending':
            # Fault at the public finalize seam: result/application/audit are
            # already actually stored by SDK; no fake receipt or private SQL.
            async def cancel_finalize(claim, application):
                interrupted.append((claim, application))
                raise asyncio.CancelledError()
            with monkeypatch.context() as fault:
                fault.setattr(manager.backend, 'finalize_analysis_application', cancel_finalize)
                with pytest.raises(asyncio.CancelledError):
                    await drain_inference_setup(authority=authority, manager=manager, **args)
            assert len(interrupted) == 1
            assert interrupted[0][1].receipt.validation_status.value == 'accepted'
            assert authority.executor.executions == 1
            await manager.close()
            now[0] += 211.0  # Actual configured 210s lease, no wall-clock sleep.
            authority = InferenceFixtureAuthority()
            manager = await m.build_human_memory_v7(tmp_path / 'memory.db',
                analysis_delivery_authority=authority, **config)
        report = await drain_inference_setup(authority=authority, manager=manager, **args)
        if invalid_reason:
            # Actual SDK finalization of rejected analysis is still APPLIED.
            # It must not satisfy this fixture's legitimate no-op settlement.
            assert [o.value for o in report['outcomes']] == ['applied']
            assert not report['confirmed'] and report['applied'] == ()
            assert len(report['rejected_applications']) == 1
            assert report['rejected_applications'][0].receipt.validation_status.value == 'rejected'
            assert await manager.get_twin_graph_view(principal=principal) == graph
            return
        assert report['confirmed']
        assert [o.value for o in report['outcomes']] == ['applied', 'applied']
        assert report['fixture_executions'] == (1 if interrupted else 2)
        proofs = report['applied']
        if interrupted:
            recovered = next(p for p in proofs if p.request.request_hash == interrupted[0][0].request.request_hash)
            assert recovered.application == interrupted[0][1]
            assert recovered.claim.application == interrupted[0][1]
            assert recovered.claim.envelope is not None
        assert len({p.request.run_id for p in proofs}) == 2
        assert len({p.request.job_id for p in proofs}) == 2
        user = next(p for p in proofs if p.source_id == original[0].evidence_id)
        lineage = seed['source_group'].user_analysis_lineage
        assert (user.request.provider_id, user.request.model_id, user.request.model_config_hash) == (
            lineage.provider_id, lineage.model_id, lineage.model_config_hash)
        assert await manager.get_twin_graph_view(principal=principal) == graph
        assert await PrimaryConversationAuthority(scoring, subject=auth.subject).completed_run_ids() == ()
    finally:
        await manager.close()
    authority = InferenceFixtureAuthority()
    reopened = await m.build_human_memory_v7(tmp_path / 'memory.db',
        analysis_delivery_authority=authority, **config)
    try:
        restored = await drain_inference_setup(authority=authority, manager=reopened,
            prior_applied=proofs, **args)
        assert restored['confirmed'] and restored['outcomes'] == ()
        assert restored['fixture_executions'] == 0
        assert await reopened.get_twin_graph_view(principal=principal) == graph
        assert len(provider.requests) == 1
    finally:
        await reopened.close()
    if mode != 'accepted':
        return
    # Each negative enters the real public-builder/drain/recovery path. A
    # self-consistent replacement of claim.job_ids must not borrow APPLIED.
    proof = proofs[0]
    wrong_jobs = ('different-actual-job',)
    negatives = [
        (replace(proof, job_ids=wrong_jobs, claim=replace(proof.claim, job_ids=wrong_jobs)),
            'inference_fixture_claim_identity_differs'),
        (replace(proof, application=replace(proof.application,
            receipt=replace(proof.application.receipt, result_hash='0' * 64))),
            'inference_fixture_prior_application_not_accepted'),
        (replace(proof, application=proofs[1].application),
            'inference_fixture_prior_application_not_accepted'),
    ]
    for changed, reason in negatives:
        authority = InferenceFixtureAuthority()
        reopened = await m.build_human_memory_v7(tmp_path / 'memory.db',
            analysis_delivery_authority=authority, **config)
        try:
            with pytest.raises(ValueError, match=reason):
                await drain_inference_setup(authority=authority, manager=reopened,
                    prior_applied=(changed,), **args)
            assert authority.executor.executions == 0
            assert await reopened.get_twin_graph_view(principal=principal) == graph
        finally:
            await reopened.close()
    authority = InferenceFixtureAuthority()
    reopened = await m.build_human_memory_v7(tmp_path / 'memory.db',
        analysis_delivery_authority=authority, **config)
    try:
        absent_proof = await drain_inference_setup(authority=authority, manager=reopened, **args)
        assert [o.value for o in absent_proof['outcomes']] == ['idle']
        assert not absent_proof['confirmed'] and absent_proof['applied'] == ()
        assert absent_proof['fixture_executions'] == 0
    finally:
        await reopened.close()
