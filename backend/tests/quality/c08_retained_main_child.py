"""Fresh-process C08 main control, mirroring C07's actual initialization order.

Only test orchestration: main factory/resolver/adapter/SDK/Host sources are real.
Setup uses bounded loopback HTTP; the sole next-response HTTP is intercepted.
No backend server, Tauri, external network, model load or installed modification.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import httpx
import simple_harness_memory as memory

from deskpet.quality.corpus_c08_retained import (
    FIXTURE_PROVIDER_ID, RetainedSummaryProvider, compile_c08_retained_setup,
    validate_retained_setup, execute_c08_retained_group, prepare_c08_retained_seed,
)
from deskpet.quality.corpus_scoring_session import configure_process, collect_turn, write_result


async def run_control(directory, host, control, key):
    from deskpet.memory.schema import dispatch_startup_epoch, StartupCompositionMode
    state = directory/'runtime/userdata/data/state.db'
    epoch = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    import main
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.human_memory_v7 import (
        host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES, local_memory_principal,
    )
    from deskpet.memory.conversation_registration import PrimaryConversationAuthority
    from deskpet.memory.history_source_authority import HostHistorySourceAuthority
    from deskpet.memory.evidence_authority import HostEvidenceAuthority
    from deskpet.memory.display_invalidation import MemoryDisplayInvalidation
    from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
    from deskpet.memory.wemm_embedder import WeMMEmbedder
    from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
    from deskpet.quality.corpus_c07_phase import admit_scoring_provider
    from deskpet.quality.corpus_runtime import execute_scoring_turn
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
    from deskpet.workflows.bootstrap import build_workflow_service
    from deskpet.retrieval.runtime import build_search_gateway, set_default_gateway, shutdown_default_gateway
    from llm.provider_registry import LLMProviderRegistry
    from llm.resolution import ProviderRoutingReadiness
    from simple_harness_memory.core.jobs import DurableMemoryJobRunner

    batch = compile_c08_retained_setup(control['case_id'], control['setup'], scenario_clock=control['scenario_clock'])
    clock = lambda:batch.scenario_time
    result = dict(status='NOT_COMPLETED', seed_started=False, mapping_rejected=False,
        remote_calls=0, scoring_posts=0, cleanup_errors=[])
    # Negative mapping must fail before any source/HTTP/SDK operation. Do not
    # alter the shared RETAINED map or original case to make a legal fixture.
    try:
        validate_retained_setup(replace(batch, messages=(batch.messages[0], ('assistant', 'wrong mapping'))))
    except ValueError as exc:
        assert str(exc) == 'c08_retained_exact_manifest_required'
        result['mapping_rejected'] = True
    assert result['mapping_rejected']
    provider = RetainedSummaryProvider(batch, model=main.config.llm.local.model)
    blocked_models, scoring_bodies = [], []
    phase = None
    original_send = httpx.AsyncClient.send
    original_reply = provider._reply

    def forbid_model(embedder):
        blocked_models.append(embedder)
        raise RuntimeError('c08_control_optional_embedding_unavailable')

    async def reply(writer, status, payload):
        if status == 200 and control['mode'] == 'wrong-assistant':
            payload = dict(payload, choices=[dict(index=0,
                message=dict(role='assistant', content='WRONG_ASSISTANT_LOCAL_CONTROL'), finish_reason='stop')])
        await original_reply(writer, status, payload)

    async def send(client, request, *args, **kwargs):
        if request.url.host == '127.0.0.1':
            assert provider.base_url is not None
            assert request.url.port == int(provider.base_url.split(':')[2].split('/')[0])
            return await original_send(client, request, *args, **kwargs)
        assert request.url.host == 'c08-scoring.invalid', 'unexpected external request'
        assert provider._server is None and phase is not None
        assert result['production_manager_reopened'] and result['seed_started']
        body = json.loads(request.content)
        current = [row for row in body['messages'] if row['role'] != 'system']
        assert current == [{'role':'user', 'content':control['current_user_message']}]
        visible = json.dumps(body['messages'], ensure_ascii=False)
        for forbidden in (batch.value, batch.messages[0][1], batch.messages[1][1], batch.setup_text):
            assert forbidden not in visible
        scoring_bodies.append(body)
        assert len(scoring_bodies) == 1
        result['current_messages'] = current
        write_result(directory/'current-wire.json', body)
        return httpx.Response(200, request=request, json=dict(id='c08-local-current-response',
            model=body['model'], choices=[dict(index=0,
                message=dict(role='assistant', content='本地来源隔离控制完成。'), finish_reason='stop')],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))

    async def binding_for(observation):
        return SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(
            observation['sdk_run_id']).binding_record)

    with patch.object(WeMMEmbedder, '_load_sync', forbid_model), \
            patch.object(httpx.AsyncClient, 'send', send), patch.object(provider, '_reply', reply):
        try:
            assert main._state_db_path.resolve() == state.resolve()
            assert epoch.composition_mode is StartupCompositionMode.HUMAN
            # Same owning activation sequence as corpus_scoring_session/C07.
            await main._initialize_product_memory()
            await main._session_db.initialize()
            readiness = ProviderRoutingReadiness()
            main.service_context.register('provider_routing_readiness', readiness)
            main._provider_registry = LLMProviderRegistry(main._CONFIG_PATH)
            assert not main._provider_registry.list_providers()
            await provider.start()
            await main._provider_registry.add_ephemeral_provider(provider.registration())
            main.service_context.register('provider_registry', main._provider_registry)
            await main._session_db.reconcile_provider_bindings({entry['id']:
                (str(entry['incarnation_id']), int(entry['config_revision']))
                for entry in main._provider_registry.list_providers()},
                registry_digest=main._provider_registry.snapshot_digest())
            readiness.mark_ready()
            workflow = await build_workflow_service(main._paths.user_data_dir(), activate=False,
                session_delivery_state_reader=main._session_db.get_session_delivery_state)
            main.service_context.register('workflow_service', workflow)
            main._workflow_service = workflow
            gateway = build_search_gateway(main.config)
            set_default_gateway(gateway)
            main.service_context.register('search_gateway', gateway)
            await main._initialize_capability_runtime()
            await main._initialize_growth_authority()
            async def suppression(candidate, purpose):
                owned = main.service_context.get('human_memory_v7_runtime')
                return await (await owned.manager()).backend.resolve_suppression(candidate, purpose,
                    principal=owned.principal())
            factory = HumanMemoryHostServiceFactory(state, epoch,
                settled_run_reader=lambda run_id, **kwargs: main._sdk_runtime_stack.read_settled_primary_run(run_id, **kwargs),
                suppression_resolver=suppression, history_visibility_checker=main._primary_history_visibility_checker,
                run_binding_reader=lambda run_id: main._sdk_runtime_stack.read_closure_run_facts(run_id).binding_record,
                decision_ingress_getter=lambda:main._sdk_ingress,
                cognitive_runtime_getter=lambda:main.service_context.get('human_memory_v7_runtime'),
                display_invalidation=MemoryDisplayInvalidation(main._broadcast_control))
            main.service_context.register('human_memory_host_service_factory', factory)
            service = factory.bind(local_owner_auth())
            await service.open_primary()
            # No unrelated scalar pre-seed; A must derive from the old real USER.
            await main._activate_product_sdk_runtime(clock=clock)
            await main._memory_analysis_lane.close()
            await main._activate_human_memory_host_ports(epoch)
            await main._activate_companion_runtime_adapter_and_open_ingress()
            runtime = main.service_context.get('human_memory_foreground_runtime_execution_authority')
            cognitive = main.service_context.get('human_memory_v7_runtime')
            assert runtime is not None and cognitive is not None
            authority = PrimaryConversationAuthority(state, subject=local_memory_principal().actor_id)
            worker = MemoryIngestionOutboxWorker(state, cognitive.manager, owner_id='c08-retained-control-ingestion')
            phase_dir = directory/'retained-phase'
            phase_dir.mkdir()
            executed = None
            try:
                executed = await execute_c08_retained_group(path=state, subject=cognitive.principal().actor_id,
                    batch=batch, service=service, runtime=runtime, ingestion_worker=worker)
            except ValueError as exc:
                if control['mode'] != 'wrong-assistant' or str(exc) != 'c08_retained_actual_terminal_or_fixture_binding_differs':
                    raise
                runs = await authority.completed_run_ids()
                assert len(runs) == 1
                actual = await authority.registrations_for_run(runs[0])
                terminal = actual.terminal_source[0].sanitized_payload
                assert terminal['messages'][1]['content'] == 'WRONG_ASSISTANT_LOCAL_CONTROL'
                observation = await collect_turn(main, service, cognitive.principal().actor_id,
                    {'turn_ref':terminal['turn_id']}, batch.messages[0][1], directory=phase_dir)
                phase = provider.verify_completed_phase(observations=observation, binding=await binding_for(observation))
                write_result(phase_dir/'phase.json', dict(status='REJECTED_WRONG_ASSISTANT', **phase))
                result.update(status='WRONG_ASSISTANT_REJECTED', actual_wrong_group_completed=True)
                assert not result['seed_started']
            if control['mode'] != 'wrong-assistant':
                assert executed is not None
                observation = await collect_turn(main, service, cognitive.principal().actor_id,
                    executed.queue_receipt, batch.messages[0][1], directory=phase_dir)
                phase = provider.verify_completed_phase(observations=observation, binding=await binding_for(observation))
                assert phase['provider_id'] == FIXTURE_PROVIDER_ID and phase['fixture_http_requests'] == 1
                write_result(phase_dir/'phase.json', dict(status='CONFIRMED', **phase))
                await provider.close()
                original_manager = await cognitive.manager()
                original_analysis = cognitive.analysis_authority
                assert original_analysis is not None
                await cognitive.close()
                delivery = SetupFixtureDeliveryAuthority()
                fixture = await memory.build_human_memory_v7(cognitive.db_path,
                    classification_policy=host_classification_policy(), supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
                    evidence_authority=HostEvidenceAuthority(state), analysis_delivery_authority=delivery,
                    conversation_evidence_authority=authority, history_source_authority=HostHistorySourceAuthority(state), clock=clock)
                job_outcomes = []
                run_job = DurableMemoryJobRunner.run_once
                async def observe_job(runner):
                    outcome = await run_job(runner)  # observation only; do not replace any result
                    job_outcomes.append(outcome.value)
                    return outcome
                try:
                    result['seed_started'] = True
                    with patch.object(DurableMemoryJobRunner, 'run_once', observe_job):
                        seed = await prepare_c08_retained_seed(path=state, manager=fixture,
                            principal=cognitive.principal(), batch=batch, executed=executed, delivery_authority=delivery)
                    assert job_outcomes == ['applied', 'idle']
                    assert seed['setup_complete'] and seed['fixture_executions'] == 1
                    assert [item.visible for item in seed['visibility_before'].items] == [True, True]
                    assert [item.reason for item in seed['visibility_after'].items] == ['history_suppressed'] * 2
                    write_result(directory/'seed.json', seed)
                finally:
                    await fixture.close()
                reopened = await cognitive.manager()
                assert reopened is not fixture and reopened is not original_manager
                assert cognitive.analysis_authority is original_analysis
                assert cognitive.build_kwargs()['analysis_delivery_authority'] is original_analysis
                assert not (await reopened.get_twin_graph_view(principal=cognitive.principal())).nodes
                group = await authority.registrations_for_run(executed.completed_group.host_run_id)
                assert group == executed.completed_group
                visibility = await reopened.check_history_visibility(principal=cognitive.principal(),
                    disclosure_context=seed['plan'].disclosure_context,
                    bindings=tuple(memory.HistoryEvidenceBinding(r.envelope, r.admission_receipt) for r in group.registrations))
                assert [item.reason for item in visibility.items] == ['history_suppressed'] * 2
                result.update(production_manager_reopened=True, original_source_retained=True,
                    job_outcomes_before_current=job_outcomes)
                await admit_scoring_provider(registry=main._provider_registry,
                    resolver=main._sdk_provider_binding_resolver, session_db=main._session_db,
                    base_url='https://c08-scoring.invalid/v1', key=key, model=main.config.llm.local.model)
                current = await execute_scoring_turn(service=service, runtime=runtime, scoring_path=state,
                    subject=cognitive.principal().actor_id, text=control['current_user_message'],
                    delivery_key='c08-current-control', ingestion_worker=worker)
                current_dir = directory/'current-phase'
                current_dir.mkdir()
                actual = await collect_turn(main, service, cognitive.principal().actor_id,
                    current.queue_receipt, control['current_user_message'], directory=current_dir)
                assert not actual['observation_errors'] and actual['trace']['trace_status'] == 'COMPLETE'
                assert actual['trace']['terminal_status'] == 'TERMINAL'
                assert actual['trace']['provider_observation_complete'] is True
                assert len(actual['trace']['providers']) == 1
                binding = await binding_for(actual)
                assert binding.provider_id == 'corpus-real-provider'
                result.update(status='RETAINED_THEN_CURRENT_COMPLETED', setup_sdk_run_id=phase['sdk_run_id'],
                    current_sdk_run_id=actual['sdk_run_id'], setup_provider_invocation_id=phase['provider_invocation_id'],
                    current_provider_invocation_id=actual['trace']['providers'][0]['invocation_id'])
            else:
                assert executed is None, 'wrong assistant unexpectedly accepted'
        except BaseException as exc:
            result.update(status='CONTROL_FAILED', error_type=type(exc).__name__)
            traceback.print_exc()
        finally:
            try:
                await provider.close()
            except Exception as exc:
                result['cleanup_errors'].append('retained_provider:' + type(exc).__name__)
            if main._sdk_ingress is not None:
                try:
                    main._sdk_ingress.close()
                except Exception as exc:
                    result['cleanup_errors'].append('ingress:' + type(exc).__name__)
            owners = (
                (main.service_context.get('human_memory_foreground_runtime_execution_authority'), 'close', {}),
                (main.service_context.get('terminal_operation_audit'), 'close', {}),
                (main._memory_analysis_lane, 'close', {}),
                (main.service_context.get('companion_runtime'), 'close', {'timeout':5.0}),
                (main._sdk_runtime_stack, 'close', {}),
                (main.service_context.get('human_memory_v7_runtime'), 'close', {}),
                (main._session_db, 'close', {}),
                (main.service_context.get('capability_center'), 'shutdown', {}),
                (main.service_context.get('capability_platform'), 'shutdown', {}),
                (main.service_context.get('workflow_service'), 'close', {}),
            )
            for owner, method, kwargs in owners:
                if owner is not None:
                    try:
                        await getattr(owner, method)(**kwargs)
                    except Exception as exc:
                        result['cleanup_errors'].append(type(owner).__name__ + ':' + type(exc).__name__)
            try:
                await shutdown_default_gateway()
            except Exception as exc:
                result['cleanup_errors'].append('gateway:' + type(exc).__name__)
            result.update(fixture_posts=provider.attempts, scoring_posts=len(scoring_bodies),
                local_provider_closed=provider._server is None and not provider._tasks,
                models_loaded=sum(embedder._model is not None for embedder in blocked_models),
                blocked_model_load_attempts=len(blocked_models))
            write_result(directory/'result.json', result)
    return 0 if result['status'] in {'RETAINED_THEN_CURRENT_COMPLETED', 'WRONG_ASSISTANT_REJECTED'} \
        and not result['cleanup_errors'] else 1


def main():
    directory, host = map(Path, sys.argv[1:])
    control = json.loads((directory/'control.json').read_text())
    read_text = Path.read_text
    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json', '.env'}
        return read_text(path, *args, **kwargs)
    with patch.object(Path, 'read_text', isolated_read):
        key, _ = configure_process(directory, host, initialize_only=True)
        return asyncio.run(run_control(directory, host, control, key))


if __name__ == '__main__':
    raise SystemExit(main())
