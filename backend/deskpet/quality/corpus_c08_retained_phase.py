"""Retained setup phase over an already initialized actual main runtime.

Extracted from the four installed-main retained controls. This module does not
initialize main, start workers, admit a scoring Provider or accept the current
question/oracle. The caller owns overall runtime/registry cleanup.
"""
import asyncio

import simple_harness_memory as memory

from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.human_memory_v7 import host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES
from deskpet.quality.corpus_c08_retained import (
    FIXTURE_PROVIDER_ID, RetainedSummaryProvider, validate_retained_setup,
    execute_c08_retained_group, prepare_c08_retained_seed,
)
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1


async def execute_retained_phase(*, main, service, runtime, batch, worker, directory,
        provider, collect_turn, record):
    """Return (phase, seed) only after actual production-authority reopen.

    Preconditions: one isolated case, empty pre-seed Memory, dedicated local
    Provider already registered, actual main foreground ready, background
    analysis lane closed. Caller must not invoke concurrently with other Runs.
    """
    validate_retained_setup(batch)
    if type(provider) is not RetainedSummaryProvider or provider.batch != batch:
        raise ValueError('c08_retained_phase_provider_mapping_differs')
    cognitive = main.service_context.get('human_memory_v7_runtime')
    if cognitive is None or runtime is not main.service_context.get(
            'human_memory_foreground_runtime_execution_authority'):
        raise ValueError('c08_retained_phase_actual_main_runtime_required')
    principal = cognitive.principal()
    if runtime.subject != principal.actor_id or cognitive.semantic_clock() != batch.scenario_time:
        raise ValueError('c08_retained_phase_subject_or_clock_differs')
    phase_dir = directory / 'setup-retained'
    phase_dir.mkdir(exist_ok=False)
    phase = dict(kind='deterministic_retained_summary', provider_id=FIXTURE_PROVIDER_ID,
        case_id=batch.case_id, setup_hash=batch.setup_hash, manifest_hash=batch.manifest_hash,
        status='NOT_CONFIRMED', stage='old_group', cleanup_errors=[])
    observation_started = False
    fixture = None
    primary_error = None
    try:
        executed = await execute_c08_retained_group(path=main._state_db_path, subject=principal.actor_id,
            batch=batch, service=service, runtime=runtime, ingestion_worker=worker)
        phase['stage'] = 'actual_provider_trace'
        # Set before writing the first observation: a partial collection must
        # never be overwritten or repeated under the same artifact filenames.
        observation_started = True
        observations = await collect_turn(main, service, principal.actor_id,
            executed.queue_receipt, batch.messages[0][1], directory=phase_dir)
        binding = SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(
            observations['sdk_run_id']).binding_record)
        phase.update(provider.verify_completed_phase(observations=observations, binding=binding))
        await provider.close()
        phase['stage'] = 'close_owned_manager'
        original_manager = await cognitive.manager()
        original_analysis = cognitive.analysis_authority
        if original_analysis is None:
            raise ValueError('c08_retained_phase_production_analysis_authority_missing')
        await cognitive.close()
        phase['stage'] = 'fixture_seed_suppression'
        authority = PrimaryConversationAuthority(main._state_db_path, subject=principal.actor_id)
        delivery = SetupFixtureDeliveryAuthority()
        fixture = await memory.build_human_memory_v7(cognitive.db_path,
            classification_policy=host_classification_policy(), supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
            evidence_authority=HostEvidenceAuthority(main._state_db_path), analysis_delivery_authority=delivery,
            conversation_evidence_authority=authority,
            history_source_authority=HostHistorySourceAuthority(main._state_db_path), clock=cognitive.semantic_clock)
        seed = await prepare_c08_retained_seed(path=main._state_db_path, manager=fixture,
            principal=principal, batch=batch, executed=executed, delivery_authority=delivery)
        if not seed['setup_complete'] or seed['fixture_executions'] != 1:
            raise ValueError('c08_retained_phase_setup_not_complete')
        record(phase_dir / 'seed.json', seed)
        closed_fixture = fixture
        await fixture.close()
        fixture = None
        phase['stage'] = 'production_reopen'
        reopened = await cognitive.manager()
        if (reopened is original_manager or reopened is closed_fixture
                or cognitive.analysis_authority is not original_analysis
                or cognitive.build_kwargs()['analysis_delivery_authority'] is not original_analysis
                or cognitive.semantic_clock() != batch.scenario_time):
            raise ValueError('c08_retained_phase_original_production_binding_not_restored')
        graph = await reopened.get_twin_graph_view(principal=principal)
        group = await authority.registrations_for_run(executed.completed_group.host_run_id)
        if group != executed.completed_group:
            raise ValueError('c08_retained_phase_original_source_not_retained')
        visibility = await reopened.check_history_visibility(principal=principal,
            disclosure_context=seed['plan'].disclosure_context,
            bindings=tuple(memory.HistoryEvidenceBinding(r.envelope, r.admission_receipt)
                for r in group.registrations))
        record(phase_dir / 'production-reopen.json', dict(graph=graph, visibility=visibility,
            group_references=group.references))
        if (graph.nodes or graph.edges or len(visibility.items) != 2
                or any(item.visible or item.reason != 'history_suppressed' for item in visibility.items)):
            raise ValueError('c08_retained_phase_production_suppression_differs')
        phase.update(status='CONFIRMED', stage='ready_for_separate_scoring',
            production_manager_reopened=True, evidence_directory='setup-retained')
    except BaseException as exc:
        primary_error = exc
        phase.update(status='SETUP_FAILED', error_type=type(exc).__name__)
        if not observation_started:
            # Queue snapshot includes settled/rejected turns as well as pending
            # ones. Read identity only here; public SDK owns the actual trace.
            try:
                async with asyncio.timeout(5):
                    turns = (await service.queue_snapshot())['turns']
                    exact = [turn for turn in turns
                        if turn['delivery_key'] == 'c08-retained:' + batch.manifest_hash]
                    if len(exact) == 1:
                        observation_started = True
                        await collect_turn(main, service, principal.actor_id,
                            exact[0], batch.messages[0][1], directory=phase_dir)
                    else:
                        phase['observation_status'] = 'NO_EXACT_ADMITTED_TURN'
            except BaseException as observation_error:
                phase['observation_error_type'] = type(observation_error).__name__
        raise
    finally:
        # Never reopen production after a failed fixture close. On any failure
        # the caller aborts this isolated case and closes its remaining owners;
        # there is no implicit retry or admission of the real scoring Provider.
        cleanup_error = None
        for owner, name in ((fixture, 'fixture_manager'), (provider, 'retained_provider')):
            if owner is not None:
                try:
                    await owner.close()
                except BaseException as exc:
                    cleanup_error = exc
                    phase['cleanup_errors'].append(name + ':' + type(exc).__name__)
        if cleanup_error is not None:
            phase['status'] = 'CLEANUP_FAILED'
        phase.update(fixture_http_requests=provider.attempts,
            request_sha256=provider.accepted_request_hash)
        record(phase_dir / 'phase.json', phase)
        if cleanup_error is not None and primary_error is None:
            if isinstance(cleanup_error, asyncio.CancelledError):
                raise cleanup_error
            raise RuntimeError('c08_retained_phase_cleanup_failed') from cleanup_error
    return phase, seed
