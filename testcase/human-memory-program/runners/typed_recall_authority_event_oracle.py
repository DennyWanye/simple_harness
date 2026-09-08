"""Independent authority-event oracle: standard library only, never SDK-produced gold.

Reason mapping, fixed from the pinned public contract before execution (Memory
authorize_recall_context_use raises one stable public code for every stale/expired
condition; the original per-event codes are distinguished by the witnessed condition):

| original expected_old_result_use | public code            | witnessed condition                                   |
|----------------------------------|------------------------|-------------------------------------------------------|
| RECALL_AUTHORITY_STALE           | RECALL_AUTHORITY_STALE | epoch +1 (after-before from the frozen row), policy unchanged, use before any expiry |
| RECALL_RESULT_EXPIRED            | RECALL_AUTHORITY_STALE | epoch unchanged; use_at == result.authority_expires_at < context.expires_at; control one margin earlier accepted |
| RECALL_CONTEXT_EXPIRED           | RECALL_AUTHORITY_STALE | epoch unchanged; use_at == context.expires_at == result.authority_expires_at; control one margin earlier accepted |
"""
import copy
import hashlib
import json
from datetime import datetime

VERSION = 1
STALE = {'type': 'MemoryValidationError', 'reason': 'RECALL_AUTHORITY_STALE'}
CONTROL_MARGIN = 0.000001


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def seconds(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def event_rows(fixture):
    return {row['event']: row for row in fixture['authority_event_cases']}


def inputs(fixture, base):
    """Construction inputs only. Times come from the frozen event rows / common binding."""
    common = fixture['authority_event_common_binding']
    rows = event_rows(fixture)
    payloads = fixture['conflict_write_oracle']['canonical_payloads']
    short_text = next(r for r in fixture['minimal_projection_oracle'] if r['memory_type'] == 'short_horizon')['source_record']['content']
    evaluated = seconds(common['evaluated_at'])
    default = dict(evaluated_at=evaluated, use_at=seconds(common['use_at']), next_use_at=base['next_use_at'],
                   context_expires_at=seconds(common['context_expires_at']), after_attempt='attempt-after-event',
                   short=False, valid_until=None)
    events = {
        'revoke': {**default},
        'supersede': {**default, 'replacement_value': payloads['replacement']['object_value']},
        'contest': {**default, 'challenger_value': payloads['challenger']['object_value']},
        'classification_change': {**default, 'reclassified_privacy': 'restricted'},
        'short_source_invalidation': {**default, 'short': True,
            'short_text': short_text + ' ' + base['query'], 'short_occurred_at': evaluated - 3600},
    }
    result_row, context_row = rows['result_expiry'], rows['context_expiry']
    result_boundary = seconds(result_row['authority_expires_at'])
    events['result_expiry'] = {**default, 'evaluated_at': seconds(result_row['evaluated_at']),
        'use_at': seconds(result_row['evaluated_at']) + 30, 'valid_until': result_boundary,
        # The result boundary is only observable when the context outlives it (Memory binds
        # authority_expires_at = min(context.expires_at, source valid_to)); runner choice, recorded.
        'context_expires_at': result_boundary + 60, 'boundary': result_boundary,
        'next_use_at': seconds(result_row['use_at']), 'control_margin': CONTROL_MARGIN}
    context_boundary = seconds(context_row['context_expires_at'])
    events['context_expiry'] = {**default, 'context_expires_at': context_boundary, 'boundary': context_boundary,
        'next_use_at': seconds(context_row['use_at']), 'control_margin': CONTROL_MARGIN}
    short_boundary = seconds(common['authority_expires_at'])
    events['short_source_expiry'] = {**default, 'short': True, 'short_text': short_text + ' ' + base['query'],
        # Short-horizon retention is the public fixed five-day TTL; the chunk therefore expires
        # exactly at the frozen authority_expires_at when it occurred five days before it.
        'short_occurred_at': short_boundary - 5 * 24 * 60 * 60, 'boundary': short_boundary,
        # Memory removes an expired chunk only strictly after its expiry; the public rebuild and
        # the fresh attempt are therefore taken one margin past the boundary (recall itself
        # already excludes the chunk at the boundary, witnessed by the result authority bound).
        'context_expires_at': short_boundary + 60, 'next_use_at': short_boundary + CONTROL_MARGIN,
        'rebuild_at': short_boundary + CONTROL_MARGIN, 'control_margin': CONTROL_MARGIN}
    for spec in events.values():
        require(spec['evaluated_at'] < spec['use_at'] < spec['next_use_at'], 'frozen event clock order invalid')
    return {'version': VERSION, 'events': events}


def check_bundle(bundle, shared, context_use):
    context_use.check_bundle(bundle, shared)


def assess(fixture, cell, shared):
    import importlib.util
    from pathlib import Path
    spec_module = importlib.util.spec_from_file_location('context_use_oracle', Path(__file__).with_name('typed_recall_context_use_oracle.py'))
    context_use = importlib.util.module_from_spec(spec_module); spec_module.loader.exec_module(context_use)
    o = cell['observations']; checks = []; name = cell['cell_id']
    try:
        require(o.get('authority_event_cell') == name and name.startswith('current-use/authority:'), 'authority-event cell identity differs')
        require(o.get('phase') == 'complete' and not o.get('exception'), 'public authority-event execution incomplete: ' + str(o.get('exception')))
        event = name.split(':', 1)[1]
        base = context_use.inputs(fixture)
        spec = inputs(fixture, base)['events'][event]
        require(o['input'] == spec and o['executor_version'] == VERSION, 'frozen construction input differs')
        row = event_rows(fixture)[event]
        epoch_delta = row['after_epoch'] - row['before_epoch']
        require(row['before_policy_hash'] == row['after_policy_hash'], 'original row expects a policy change; not this oracle')
        # Real sources
        seeds = [base['seeds'][0]] if spec['short'] else base['seeds']
        require(len(o['sources']) == len(seeds) + (1 if event in {'supersede', 'contest', 'classification_change'} else 0), 'source count differs')
        ids = []
        for i, seed in enumerate(seeds):
            shared['check_seed_authority'](o, {'seed': seed}, shared['semantic_source'](**seed['payload']), source_index=i, check_recall_refs=False)
            operation = o['sources'][i]['receipt']['operations'][0]
            ids.append(operation['memory_id']); require(operation['revision'] == 1, 'seed is not a real initial head')
        if spec['valid_until'] is not None:
            plan = next(e['plan'] for e in o['calls'] if e['call'] == 'apply_memory_mutation_plan' and e['plan']['plan_id'] == o['sources'][0]['receipt']['plan_id'])
            require(plan['operations'][0]['valid_time_interval']['valid_until'] == spec['valid_until'], 'frozen result boundary not bound to the source')
        short_ref = None
        if spec['short']:
            registrations = [e for e in o['calls'] if e['call'] == 'register_conversation_evidence']
            require(len(registrations) == 11 and registrations[0]['input_text'] == spec['short_text']
                    and registrations[0]['registration']['metadata']['occurred_at'] == spec['short_occurred_at'], 'short registration chain differs')
            require(o['projection_build'] == {'projected_chunk_count': 1, 'removed_chunk_count': 0, 'audit_id': o['projection_build'].get('audit_id')}
                    and isinstance(o['projection_build'].get('audit_id'), str), 'short projection did not project exactly the oldest target')
        initial = o['initial']; wire = initial['execution']; result = wire['result']
        shared['check_execution_wire'](wire, initial['context'], initial['plan'])
        require(len(result['items']) == 2 and not result['confirmation_groups'] and wire['decision']['outcome'] == 'recall', 'two ordinary items required')
        by_ref = {r['selected_item']['source_ref']: r for r in result['items']}
        for memory_id, seed in zip(ids, seeds, strict=True):
            item = by_ref[memory_id]
            require(item['selected_item']['source_kind'] == 'cognitive_memory' and item['selected_item']['source_revision'] == 1
                    and item['public_payload'] == seed['payload']
                    and item['selected_item']['source_content_hash'] == digest(shared['semantic_source'](**seed['payload'])), 'independent source payload/head differs')
        if spec['short']:
            short_items = [r for r in result['items'] if r['selected_item']['source_kind'] == 'short_horizon']
            require(len(short_items) == 1 and short_items[0]['selected_item']['memory_type'] is None
                    and short_items[0]['selected_item']['source_revision'] is None
                    and short_items[0]['public_payload'] == {'content': 'user: ' + spec['short_text'], 'occurred_at': spec['short_occurred_at']}, 'short item projection differs')
            short_ref = short_items[0]['selected_item']['source_ref']
        require(result['evaluated_at'] == spec['evaluated_at'] and initial['context']['expires_at'] == spec['context_expires_at']
                and initial['context']['run_id'] == base['run_id'] and initial['context']['turn_id'] == base['turn_id']
                and initial['context']['short_horizon_allowed'] is spec['short'], 'frozen clocks/run/turn/short carrier differ')
        epoch0, policy0 = result['authority_epoch'], result['policy_hash']
        require(type(epoch0) is int, 'authority epoch must be public int')
        checks.append('two real items (cognitive or cognitive+short) with independent source/projection bindings')
        first = o['uses']['first']
        require(first['recall'] == initial and first['request']['provider_attempt_id'] == base['attempt']
                and first['request']['requested_at'] == spec['use_at'] and 'receipt' in first and not first.get('exception'), 'first legal use differs')
        check_bundle(first, shared, context_use)
        require(first['receipt']['authority_epoch'] == epoch0 and first['receipt']['policy_hash'] == policy0, 'first receipt epoch/policy differs')
        after_use = o['uses']['after_event']
        require(after_use['recall'] == initial and after_use['request']['provider_attempt_id'] == spec['after_attempt']
                and after_use['request']['requested_at'] == spec['next_use_at'], 'fresh attempt substituted recall/attempt/time')
        check_bundle(after_use, shared, context_use)
        require(after_use.get('exception') == STALE and 'receipt' not in after_use, 'old result use after the event was not rejected with the public stale code')
        checks.append('first use receipt then exact stale rejection of a fresh attempt on the same result')
        post = o['after']; shared['check_execution_wire'](post['execution'], post['context'], post['plan'])
        after = post['execution']['result']; decision = post['execution']['decision']
        # The frozen row for revoke is relative to the preceding suppression epoch (41 -> 42).
        expected_epoch = epoch0 + epoch_delta + (1 if event == 'revoke' else 0)
        require(after['authority_epoch'] == expected_epoch and after['policy_hash'] == policy0, 'epoch delta/policy differs from the frozen event row')
        after_refs = [r['selected_item']['source_ref'] for r in after['items']]
        if event == 'revoke':
            s = o['suppression']
            require(s['request']['scope_kind'] == 'memory' and s['request']['scope_ref'] == ids[0] and s['request']['purpose'] is None
                    and s['request']['reason_code'] == 'user_forget' and s['decision']['action'] == 'directive' and s['decision']['scope_ref'] == ids[0], 'suppression target differs')
            mid = o['mid']; shared['check_execution_wire'](mid['execution'], mid['context'], mid['plan'])
            require(mid['execution']['result']['authority_epoch'] == epoch0 + 1 and [r['selected_item']['source_ref'] for r in mid['execution']['result']['items']] == [ids[1]], 'suppressed item still visible before revoke')
            r = o['revocation']
            require(r['request']['directive_id'] == s['decision']['directive_id'] and r['decision']['action'] == 'revoke'
                    and r['decision']['supersedes_directive_id'] == s['decision']['directive_id'] and r['decision']['scope_ref'] == ids[0], 'revocation does not target the exact directive')
            require(decision['outcome'] == 'recall' and sorted(after_refs) == sorted(ids), 'revoked suppression did not restore both items')
            require(o['order'].index('first') < o['order'].index('suppression_commit') < o['order'].index('revoke_commit') < o['order'].index('after_event'), 'event order differs')
            checks.append('suppress then public revoke: epoch advanced twice, old result stale, fresh recall restores the item')
        elif event in {'supersede', 'contest', 'classification_change'}:
            op = o['event_mutation']; source = o['sources'][-1]
            mutation = next(e['plan'] for e in o['calls'] if e['call'] == 'apply_memory_mutation_plan' and e['plan']['plan_id'] == source['receipt']['plan_id'])
            operation = mutation['operations'][0]
            require(op['memory_id'] == ids[0] and op['revision'] == 2 and operation['target'] == {'target_kind': 'existing_memory', 'memory_id': ids[0], 'revision': 1}, 'event mutation did not target the exact bound head')
            if event != 'contest':
                shared['check_action_grant'](o, mutation)
            else:
                require(operation['action_authority_ref'] is None, 'contest must not carry a correction grant')
            expected_payload = dict(base['seeds'][0]['payload'])
            if event == 'supersede':
                expected_payload['object_value'] = spec['replacement_value']
                require(operation['kind'] == 'supersede' and operation['lifecycle_state'] == 'superseded', 'supersede kind/state differs')
                require(decision['outcome'] == 'recall' and after_refs == [ids[1]], 'superseded head leaked or unaffected item hidden')
            elif event == 'contest':
                expected_payload['object_value'] = spec['challenger_value']
                require(operation['kind'] == 'contest' and operation['conflict_status'] == 'contested', 'contest kind/status differs')
                groups = decision['confirmation_groups']
                require(decision['outcome'] == 'needs_user_confirmation' and not decision['selected_items'] and len(groups) == 1
                        and [g['source_revision'] for g in groups[0]['members']] == [1, 2]
                        and all(g['source_ref'] == ids[0] for g in groups[0]['members']), 'contested head is not one atomic confirmation carrier')
            else:
                require(operation['kind'] == 'revise' and operation['proposed_privacy_class'] == spec['reclassified_privacy'], 'classification change kind/class differs')
                require(decision['outcome'] == 'recall' and after_refs == [ids[1]], 'reclassified restricted head leaked or unaffected item hidden')
            require(operation['payload'] == shared['semantic_source'](**expected_payload), 'event mutation payload differs from frozen input')
            shared['check_seed_authority'](o, {'seed': {'memory_type': 'semantic', 'payload': expected_payload}}, shared['semantic_source'](**expected_payload),
                                           source_index=len(o['sources']) - 1, check_recall_refs=False)
            require(o['order'].index('first') < o['order'].index(event.split('_')[0] + '_commit') < o['order'].index('after_event'), 'event order differs')
            checks.append('real authorized ' + event + ' on the bound head: epoch+1, old result stale, fresh recall reflects the new state')
        elif event == 'short_source_invalidation':
            s = o['suppression']
            require(s['request']['scope_kind'] == 'evidence' and s['request']['scope_ref'] == 'conversation-evidence-1' and s['request']['purpose'] is None
                    and s['decision']['action'] == 'directive' and s['decision']['scope_ref'] == 'conversation-evidence-1', 'short source suppression target differs')
            require(decision['outcome'] == 'recall' and after_refs == [ids[0]], 'invalidated short source leaked or cognitive item hidden')
            require(o['order'].index('first') < o['order'].index('suppression_commit') < o['order'].index('after_event'), 'event order differs')
            checks.append('short source evidence suppressed: epoch+1, old mixed result stale, fresh recall keeps only the cognitive item')
        else:
            boundary = spec['boundary']
            require(epoch_delta == 0 or event == 'short_source_expiry', 'expiry rows must not advance the epoch')
            require(spec['next_use_at'] == (spec['rebuild_at'] if event == 'short_source_expiry' else boundary), 'frozen use_at is not the boundary')
            if event == 'result_expiry':
                require(result['authority_expires_at'] == boundary == spec['valid_until'] < initial['context']['expires_at'], 'result boundary is not the source validity bound below the context bound')
            elif event == 'context_expiry':
                require(result['authority_expires_at'] == boundary == initial['context']['expires_at'], 'result boundary is not the context bound')
            else:
                require(result['authority_expires_at'] == boundary < initial['context']['expires_at'] and short_ref is not None, 'result boundary is not the short chunk expiry below the context bound')
                rebuild = o['projection_rebuild']
                require(rebuild['now'] == spec['rebuild_at'] and rebuild['result']['removed_chunk_count'] == 1 and rebuild['result']['projected_chunk_count'] == 0, 'expired chunk was not removed by the public projection rebuild past the boundary')
                require(decision['outcome'] == 'recall' and after_refs == [ids[0]], 'expired short source leaked or cognitive item hidden')
            control = o['uses']['control']
            require(control['recall'] == initial and control['request']['provider_attempt_id'] == 'attempt-control'
                    and control['request']['requested_at'] == boundary - spec['control_margin'] and 'receipt' in control and not control.get('exception')
                    and control['receipt']['authority_epoch'] == epoch0 and control['receipt']['expires_at'] <= boundary, 'control use one margin before the boundary was not accepted')
            check_bundle(control, shared, context_use)
            require(o['order'].index('first') < o['order'].index('control') < o['order'].index('after_event'), 'event order differs')
            checks.append(event + ': accepted one margin before the boundary, rejected exactly at it, epoch unchanged')
        replay = o['historical_replay']
        require(replay['replayed'] and replay['candidate_query_count'] == 0 and replay['result'] == result
                and replay['decision'] == initial['execution']['decision'], 'historical exact replay differs')
        checks.append('historical identical replay remains separate from the current authority')
        return dict(status='PASS', reason='original authority-event obligation verified through public operations',
                    business_assertions=checks, executor_version=VERSION)
    except (ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        return dict(status='FAIL', reason=str(exc), business_assertions=checks, executor_version=VERSION)
