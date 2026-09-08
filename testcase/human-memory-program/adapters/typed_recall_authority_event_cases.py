"""Original authority-event cells over public APIs: two-item base plus one real event.

Every cell re-uses the reviewed two-item Context-use base (real admissions, mutations,
recall, whole-item pages, fragments, first use receipt). Only the event between the first
use and the fresh attempt differs, and it is always a public Manager operation. Inputs carry
no expected outcome, epoch, hash or receipt. Events the pinned public API cannot express are
reported BLOCKED with the exact contract reason instead of being simulated.
"""
import dataclasses as dc
import importlib.util
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m

EVENT_CELLS = {
    'current-use/authority:revoke', 'current-use/authority:supersede', 'current-use/authority:contest',
    'current-use/authority:classification_change', 'current-use/authority:result_expiry',
    'current-use/authority:context_expiry', 'current-use/authority:short_source_invalidation',
    'current-use/authority:short_source_expiry',
}
NO_PUBLIC_INPUT = {
    # RUN-08 adjudication (b): the sealed obligation stands and the SDK is missing the capability
    # that would make it testable. The fixture is NOT amended; the required increment is named.
    'current-use/authority:policy_hash_change':
        'SDK_INCREMENT_REQUIRED:recall policy_hash is a Memory constant derived from the frozen eligibility policy '
        '(simple_harness_memory/backends/sqlite_v5.py:300-315) and no public input can version it, so the sealed '
        '"a policy change stales every prior authorization" obligation is untestable; required increment: a public '
        'recall-policy version input or a policy-record API on MemoryManager. Sealed row kept unchanged',
    'current-use/authority:short_source_cleanup':
        'SDK_INCREMENT_REQUIRED:cleanup_short_horizon exists only on the backend port '
        '(simple_harness_memory/core/port.py:465-467); MemoryManager exposes only cleanup_recall_results '
        '(simple_harness_memory/core/manager.py:1245), so the sealed cleanup event has no public entry point. '
        'Required increment: MemoryManager.cleanup_short_horizon. short_source_expiry is a different event and is '
        'not accepted as a substitute. Sealed row kept unchanged',
    # RUN-09: current-use/context:new-continuation used to sit here as "executor not implemented".
    # It is now executed by adapters/typed_recall_context_use_cases.py (same run and provider
    # attempt, changed context-use turn), so it must NOT be declared here as well - two adapters
    # emitting the same cell id is a duplicate-cell bridge failure.
}
CELLS = EVENT_CELLS | set(NO_PUBLIC_INPUT)


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def error(exc):
    return {'type': type(exc).__name__, 'reason': str(exc)}


async def seed_short(case, base, spec):
    short = load('typed_recall_short_cases')
    for sequence in range(1, 12):
        await short.register(case, sequence, spec['short_text'] if sequence == 1 else f'unrelated filler {sequence}',
                             spec['short_occurred_at'] if sequence == 1 else case.now - 11 + sequence - 1)
    built = await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now)
    case.events.append({'call': 'rebuild_short_horizon_projection', 'now': case.now, 'result': dc.asdict(built)})
    return dc.asdict(built)


async def run_event(case, base, name, spec, o):
    context_use = load('typed_recall_context_use_cases')
    event = name.split(':', 1)[1]
    recipe = {**base, 'evaluated_at': spec['evaluated_at'], 'use_at': spec['use_at'],
              'context_expires_at': spec['context_expires_at']}
    o['registration'] = dc.asdict(await case.manager.register_principal_owner(case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    o['phase'] = 'seeds'
    first_seed = dict(base['seeds'][0])
    if spec.get('valid_until') is not None:
        first_seed['valid_until'] = spec['valid_until']
    created = [await case.seed(first_seed, operation_id='context-create-1', evidence_id='context-user-1')]
    if spec['short']:
        o['projection_build'] = await seed_short(case, base, spec)
    else:
        created.append(await case.seed(base['seeds'][1], operation_id='context-create-2', evidence_id='context-user-2'))
    o['phase'] = 'two_item_recall'
    context, plan = case.request(query=recipe['query'], key='context-initial', short=spec['short'])
    disclosure = dc.replace(context.disclosure_context, run_id=recipe['run_id'])
    context = dc.replace(context, run_id=recipe['run_id'], turn_id=recipe['turn_id'],
                         expires_at=recipe['context_expires_at'], disclosure_context=disclosure)
    plan = dc.replace(plan, run_id=context.run_id, context_hash=context.context_hash, disclosure_context=disclosure)
    case.events.append({'call': 'execute_typed_recall', 'context': context.to_json(), 'plan': plan.to_json(), 'now': case.now})
    execution = await case.manager.execute_typed_recall(principal=case.principal, context=context, plan=plan, now=case.now)
    o['initial'] = {'context': context.to_json(), 'plan': plan.to_json(), 'execution': case.cases.execution_wire(execution), 'now': case.now}
    o['phase'] = 'two_actual_pages'
    bundle = await context_use.context_bundle(case, o['initial'])
    case.now = spec['use_at']
    o['phase'] = 'first'
    o['uses']['first'] = await context_use.authorize(case, bundle, context_use.use_request(case, bundle, recipe['attempt'], spec['use_at']))
    o['order'].append('first')
    if 'receipt' not in o['uses']['first']:
        raise ValueError('initial legal authorization rejected')
    o['phase'] = 'event:' + event
    if event == 'revoke':
        request = m.SuppressionRequest('context-forget', case.principal.actor_id, m.SuppressionScopeKind.MEMORY,
                                       created[0].memory_id, 'user_forget', case.now, purpose=None)
        decision = await case.manager.suppress(principal=case.principal, request=request)
        o['suppression'] = {'request': request.to_json(), 'decision': decision.to_json()}
        case.events.append({'call': 'suppress', **o['suppression']}); o['order'].append('suppression_commit')
        o['mid'] = await context_use.recall(case, recipe, 'context-mid')
        revoke = m.SuppressionRevokeRequest('context-restore', case.principal.actor_id, decision.directive_id,
                                            'user_restore', case.now)
        revoked = await case.manager.revoke_suppression(principal=case.principal, request=revoke)
        o['revocation'] = {'request': revoke.to_json(), 'decision': revoked.to_json()}
        case.events.append({'call': 'revoke_suppression', **o['revocation']}); o['order'].append('revoke_commit')
    elif event == 'supersede':
        payload = {**base['seeds'][0]['payload'], 'object_value': spec['replacement_value']}
        op = await case.seed(dict(memory_type='semantic', payload=payload, state='superseded'), operation_id='context-supersede',
                             evidence_id='context-user-supersede', kind='supersede',
                             target=h.ExistingMemoryTarget(created[0].memory_id, created[0].revision))
        o['event_mutation'] = op.to_json(); o['order'].append('supersede_commit')
    elif event == 'contest':
        payload = {**base['seeds'][0]['payload'], 'object_value': spec['challenger_value']}
        op = await case.seed(dict(memory_type='semantic', payload=payload, conflict_status='contested'), operation_id='context-contest',
                             evidence_id='context-user-contest', kind='contest',
                             target=h.ExistingMemoryTarget(created[0].memory_id, created[0].revision))
        o['event_mutation'] = op.to_json(); o['order'].append('contest_commit')
    elif event == 'classification_change':
        original_privacy = case.privacy
        case.privacy = h.PrivacyClass(spec['reclassified_privacy'])
        try:
            op = await case.seed(dict(memory_type='semantic', payload=base['seeds'][0]['payload']), operation_id='context-reclassify',
                                 evidence_id='context-user-reclassify', kind='revise',
                                 target=h.ExistingMemoryTarget(created[0].memory_id, created[0].revision))
        finally:
            case.privacy = original_privacy
        o['event_mutation'] = op.to_json(); o['order'].append('classification_commit')
    elif event == 'short_source_invalidation':
        request = m.SuppressionRequest('short-forget', case.principal.actor_id, m.SuppressionScopeKind.EVIDENCE,
                                       'conversation-evidence-1', 'user_forget', case.now, purpose=None)
        decision = await case.manager.suppress(principal=case.principal, request=request)
        o['suppression'] = {'request': request.to_json(), 'decision': decision.to_json()}
        case.events.append({'call': 'suppress', **o['suppression']}); o['order'].append('suppression_commit')
    elif event in {'result_expiry', 'context_expiry', 'short_source_expiry'}:
        case.now = spec['boundary'] - spec['control_margin']
        o['uses']['control'] = await context_use.authorize(case, bundle, context_use.use_request(case, bundle, 'attempt-control', case.now))
        o['order'].append('control')
        # The original context may expire at the boundary itself; the historical exact replay is
        # therefore taken while the context is still valid (same durable result, zero reads).
        o['historical_replay_now'] = case.now
        o['historical_replay'] = case.cases.execution_wire(await case.manager.execute_typed_recall(principal=case.principal,
            context=h.RecallContext.from_json(o['initial']['context']), plan=h.RecallPlan.from_json(o['initial']['plan']), now=case.now))
        case.now = spec['boundary']
        if event == 'short_source_expiry':
            case.now = spec['rebuild_at']
            built = await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now)
            o['projection_rebuild'] = {'now': case.now, 'result': dc.asdict(built)}
            case.events.append({'call': 'rebuild_short_horizon_projection', 'now': case.now, 'result': dc.asdict(built)})
            o['order'].append('projection_rebuild')
    else:
        raise ValueError('unknown authority event: ' + event)
    o['phase'] = 'after_event_use'
    case.now = spec['next_use_at']
    o['uses']['after_event'] = await context_use.authorize(case, bundle, context_use.use_request(case, bundle, spec['after_attempt'], case.now))
    o['order'].append('after_event')
    o['phase'] = 'current_epoch_and_unaffected_control'
    after_recipe = {**recipe, 'context_expires_at': max(recipe['context_expires_at'], case.now + 300)}
    o['after'] = await context_use.recall(case, after_recipe, 'context-after')
    if 'historical_replay' not in o:
        o['phase'] = 'historical_replay'
        o['historical_replay_now'] = case.now
        o['historical_replay'] = case.cases.execution_wire(await case.manager.execute_typed_recall(principal=case.principal,
            context=h.RecallContext.from_json(o['initial']['context']), plan=h.RecallPlan.from_json(o['initial']['plan']), now=case.now))
    o['phase'] = 'complete'


async def run_cases(inputs, workspace):
    helper = load('typed_recall_case_manager')
    base, events, rows = inputs['recipe'], inputs['events'], []
    for index, name in enumerate(inputs['cells']):
        if name in NO_PUBLIC_INPUT:
            rows.append(dict(cell_id=name, status='BLOCKED', reason=NO_PUBLIC_INPUT[name], observations={}))
            continue
        spec = events[name.split(':', 1)[1]]
        case = await helper.CaseManager(workspace / f'authority-event-{index}.sqlite', now=spec['evaluated_at']).open()
        o = dict(authority_event_cell=name, executor_version=1, input=spec, calls=case.events, sources=case.sources,
                 uses={}, order=[], phase='register')
        try:
            await run_event(case, base, name, spec, o)
        except Exception as exc:
            o['exception'] = error(exc)
        finally:
            await case.close()
        rows.append(dict(cell_id=name, status='OBSERVED', reason='', observations=o))
    return rows
