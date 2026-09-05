"""Public installed Memory SQLite analysis correction; no private SDK SQL."""
import json
from pathlib import Path
import pytest
from simple_harness_memory import MemoryManager, MemoryPrincipal
from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
from deskpet.memory.semantic_correction import SemanticCorrectionAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from tests.sdk_adapters import s5b_memory_harness as mh
from tests.sdk_adapters import s5b_closure_harness as ch


def memory_env(env, adapter, *, fault=None):
    authority = SemanticCorrectionAuthority(env.db_path, manager_getter=lambda: runtime.manager(),
        principal_getter=lambda: runtime.principal(), clock=env.clock)
    class ObservedExecutor(HostMemoryAnalysisExecutor):
        async def analyze_memory(self, request):
            try:
                return await super().analyze_memory(request)
            except Exception:
                import traceback
                traceback.print_exc()
                raise
    executor = ObservedExecutor(env.db_path, adapter_factory=lambda _: adapter,
        clock=env.clock, semantic_correction_authority=authority, fault_inject=fault)
    async def public_builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs,
            memory_action_authority=authority, clock=env.clock, allow_development_embedder=True)
    runtime = HumanMemoryV7Runtime(env.db_path.parent / 'semantic.db',
        evidence_authority=HostEvidenceAuthority(env.db_path), analysis_authority=executor,
        backend_factory=public_builder,
        principal=MemoryPrincipal('deskpet-local','deskpet-local-household',mh.SUBJECT,'primary-conversation'))
    worker = MemoryIngestionOutboxWorker(env.db_path, runtime.manager, owner_id='correction-worker',
        clock=env.clock, lease_seconds=30.0, retry_delays=(1.0,2.0))
    config = mh.build_worker_config(provider_id=mh.BINDING['provider_id'],model_id=mh.BINDING['model_id'],
        model_config_hash=mh.expected_model_config_hash(mh.BINDING, endpoint=mh.ENDPOINT))
    return mh.MemoryEnv(executor=executor,runtime=runtime,worker=worker,config=config,
        adapter=adapter,clock=env.clock,worker_id='analysis-worker',runner=None,authority=authority)


async def recalled(menv, query, ordinal):
    lanes = await menv.runtime.typed_recall(query=query,run_id=f'verify-{ordinal}',turn_ordinal=ordinal,now=float(menv.clock()))
    return lanes.execution.result.items


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "recover", "unknown", "quote", "forget", "ambiguous", "no_intent", "quoted", "hypothetical", "negative", "historical", "qualifiers", "slot", "create_bypass", "zh_valid", "zh_quoted", "zh_negative", "zh_alias_collision", "zh_unknown_slot", "late_forget"])
async def test_actual_semantic_revise_and_reopen(tmp_path, mode):
    chinese = mode.startswith('zh_')
    ambiguous = mode in {'ambiguous', 'zh_alias_collision'}
    predicate = 'unregistered_beverage_slot' if mode == 'zh_unknown_slot' else ('drink_preference' if chinese else 'preferred_drink')
    old_value, new_value = ('无糖乌龙茶', '柠檬水') if chinese else ('coffee', 'tea')
    text = '我的默认饮品偏好是无糖乌龙茶' if chinese else 'My preferred drink is coffee.'
    env = await mh.bound_turn_run(tmp_path, 'origin-run', text=text)
    await mh.finish_clean_run(env)
    originals = [mh.semantic_op(mh.item_id(env),text,predicate=predicate,object_value=old_value)]
    if ambiguous:
        originals.append({**originals[0], 'operation_id':'duplicate-fact', 'semantic':{**originals[0]['semantic'], 'qualifiers':['evening']}})
    if mode == 'zh_alias_collision': originals[1]['semantic']['predicate'] = 'preferred_drink'
    adapter = ch.FakeAdapter([mh.proposal_call(originals)])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == 'delivered'
        status = await mh.run_job(menv)
        assert status == 'applied'
        initial = await recalled(menv, old_value, 1)
        assert len(initial) == (2 if ambiguous else 1)
        original = initial[0].selected_item
    finally:
        await mh.close(menv)
    newtext = 'Correct my preferred drink from coffee to tea.'
    if chinese: newtext = '请把记忆里的饮品偏好从无糖乌龙茶改成柠檬水。'
    unsupported = {
        'zh_quoted': '“请把记忆里的饮品偏好从无糖乌龙茶改成柠檬水。”',
        'zh_negative': '不要把记忆里的饮品偏好从无糖乌龙茶改成柠檬水。',
        'no_intent': 'Someone said that my preferred drink changed from coffee to tea.',
        'quoted': '"Correct my preferred drink from coffee to tea."',
        'hypothetical': 'If I said Correct my preferred drink from coffee to tea. would you do it?',
        'negative': 'Do not Correct my preferred drink from coffee to tea.',
        'historical': 'Yesterday I said Correct my preferred drink from coffee to tea.',
    }
    newtext = unsupported.get(mode, newtext)
    current = await mh.next_turn_run(env,'correction-run',text=newtext,delivery_key='correct-1')
    await mh.finish_clean_run(current)
    class CorrectionAdapter:
        def __init__(self): self.calls=[]
        async def invoke(self, request, *, cancel):
            self.calls.append(request)
            body = json.loads(request.messages[-1].content.split('\n',1)[1])
            candidates = body['semantic_candidates']
            assert len(candidates) == (2 if ambiguous else 1)
            op = mh.semantic_op(mh.item_id(current),newtext,predicate=predicate,object_value=new_value)
            op.update(action='revise_semantic',candidate_key=candidates[0]['candidate_key'])
            if mode == 'unknown': op['candidate_key'] = 'model-invented-target'
            if mode == 'create_bypass':
                op['action'] = 'create'
                del op['candidate_key']
            if mode == 'quote': op['exact_quote'] = 'I never actually said this.'
            if mode == 'qualifiers': op['semantic']['qualifiers'] = ['model-added']
            if mode == 'slot': op['semantic']['predicate'] = 'favorite_food'
            if mode == 'forget':
                from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
                manager = await menv.runtime.manager()
                await manager.backend.suppress(SuppressionRequest('forget-target', mh.SUBJECT,
                    SuppressionScopeKind.MEMORY, original.source_ref, 'user_forget', float(env.clock())), principal=menv.runtime.principal())
            return mh.proposal_call([op], provider_request_id='correction-provider')
    corrected = CorrectionAdapter()
    def interrupt(point):
        if mode == 'recover' and point == 'analysis-before-derive':
            raise RuntimeError('after durable response before envelope')
    menv = memory_env(current, corrected, fault=interrupt)
    if mode == 'late_forget':
        real_check = menv.authority.check
        checks = []
        async def suppress_after_first_check(request, snapshot):
            await real_check(request, snapshot)
            checks.append(True)
            if len(checks) == 1:
                from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
                manager = await menv.runtime.manager()
                await manager.suppress(principal=menv.runtime.principal(), request=SuppressionRequest(
                    'late-forget', mh.SUBJECT, SuppressionScopeKind.MEMORY,
                    original.source_ref, 'user_forget', float(env.clock())))
        menv.authority.check = suppress_after_first_check
    missing_authority_outcomes = []
    authorized_plans = []
    original_authorize = menv.authority.authorize_plan
    async def probe_missing_authority(request, snapshot, plan):
        from simple_harness_memory import MemoryScope
        manager = await menv.runtime.manager()
        outcome = await manager.apply_memory_mutation_plan(principal=menv.runtime.principal(), scope=MemoryScope.personal(mh.SUBJECT), plan=plan)
        missing_authority_outcomes.append(outcome)
        assert outcome.outcome.value == 'needs_user_confirmation'
        assert outcome.reason_code.value == 'memory_action_authority_required'
        authorized = await original_authorize(request, snapshot, plan)
        authorized_plans.append(authorized)
        return authorized
    if mode == 'valid': menv.authority.authorize_plan = probe_missing_authority
    try:
        assert await menv.worker.run_once() == 'delivered'
        result = await mh.run_job(menv)
        if mode == 'recover':
            assert result == 'retry_scheduled'
            assert len(corrected.calls) == 1
            await mh.close(menv)
            current.clock.now += 1000.0
            menv = memory_env(current, corrected)
            result = await mh.run_job(menv)
        if mode == 'late_forget':
            assert result == 'retry_scheduled'
            assert len(corrected.calls) == 0
            import sqlite3
            with sqlite3.connect(env.db_path) as host_db:
                    row = host_db.execute("SELECT status,unknown_class FROM post_turn_invocation_attempts WHERE purpose='analysis' AND sdk_run_id='correction-run'").fetchone()
            assert row == ('failed', None)
            return
        if mode == 'forget':
            assert result == 'retry_scheduled'
            assert len(corrected.calls) == 1
            assert not await recalled(menv, new_value, 2)
            return
        assert result == 'applied'
        if mode in {'unknown','quote','ambiguous','qualifiers','slot','create_bypass','zh_alias_collision','zh_unknown_slot', *unsupported}:
            import sqlite3
            with sqlite3.connect(env.db_path) as host_db:
                assert host_db.execute("SELECT count(*) FROM human_memory_evidence WHERE evidence_id LIKE 'semantic-action%'").fetchone()[0] == 0
            rows = await recalled(menv,old_value,2)
            assert len(rows) == (2 if ambiguous else 1)
            assert all(r.selected_item.source_revision == 1 and r.public_payload['object_value'] == old_value for r in rows)
            assert not await recalled(menv,new_value,3)
            assert len(corrected.calls) == 1
            return
        if mode == 'valid': assert len(missing_authority_outcomes) == 1
        rows = await recalled(menv,new_value,2)
        assert len(rows) == 1
        assert rows[0].selected_item.source_ref == original.source_ref
        assert rows[0].selected_item.source_revision == original.source_revision + 1
        assert rows[0].public_payload['object_value'] == new_value
        envelope,receipt = await HostEvidenceAuthority(env.db_path).read_admitted(env.evidence_id)
        assert envelope == env.envelope and receipt == env.receipt
        assert len(corrected.calls) == 1
    finally:
        await mh.close(menv)
    menv = memory_env(current, corrected)
    try:
        rows = await recalled(menv,new_value,3)
        assert rows[0].selected_item.source_revision == 2
        assert rows[0].public_payload['object_value'] == new_value
        assert await mh.run_job(menv) == 'idle'
        assert len(corrected.calls) == 1
        if mode == 'valid':
            from simple_harness_memory import MemoryScope
            manager = await menv.runtime.manager()
            # Public mutation replay after apply, reopen, and expired authority:
            # an existing receipt must win without consuming another authority.
            current.clock.now += 1000
            results = [await manager.apply_memory_mutation_plan(
                principal=menv.runtime.principal(), scope=MemoryScope.personal(mh.SUBJECT),
                plan=authorized_plans[0]) for _ in range(2)]
            assert all(r.outcome.value == 'committed' for r in results)
            assert results[0].receipt_ref == results[1].receipt_ref
            assert results[0].receipt_ref is not None
            view = await manager.get_memory_mutation_receipt_view(
                principal=menv.runtime.principal(), receipt_ref=results[0].receipt_ref)
            assert view is not None
            assert (await recalled(menv, new_value, 4))[0].selected_item.source_revision == 2
    finally:
        await mh.close(menv)
