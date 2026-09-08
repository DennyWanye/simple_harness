"""Independent current-use oracle: standard library only, never SDK-produced gold."""
import copy
import hashlib
import json
from datetime import datetime

VERSION = 3
CELLS = {
    'current-use/context:receipt-first', 'current-use/context:duplicate-same-provider-attempt',
    'current-use/context:new-provider-attempt', 'current-use/context:new-continuation',
    'current-use/context:suppression-first',
    'current-use/context:wrong-snapshot', 'current-use/authority:suppression',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def domain(name, value):
    return digest({'domain': name, 'payload': value})


def inputs(fixture):
    """Only construction inputs, no outcome/hash/receipt gold goes to the consumer."""
    common = fixture['authority_event_common_binding']
    first = copy.deepcopy(fixture['conflict_write_oracle']['canonical_payloads']['incumbent'])
    first['qualifiers'] = []
    second = {**first, 'subject_entity': 'context-use-secondary'}
    seconds = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    return {
        'version': VERSION,
        'seeds': [{'memory_type': 'semantic', 'payload': p} for p in (first, second)],
        'query': first['predicate'], 'run_id': common['run_id'], 'turn_id': common['turn_id'],
        'evaluated_at': seconds(common['evaluated_at']), 'use_at': seconds(common['use_at']),
        'context_expires_at': seconds(common['context_expires_at']),
        'attempt': common['provider_attempt'], 'continuation': common['continuation_id'],
        'next_attempt': next(r['receipt']['provider_attempt'] for r in fixture['context_use_cases'] if r['id']=='new-provider-attempt'),
        'after_attempt': 'attempt-after-suppression',
        'next_use_at': seconds(next(r['receipt']['use_at'] for r in fixture['context_use_cases'] if r['id']=='new-provider-attempt')),
        'next_continuation': next(r['receipt']['continuation_id'] for r in fixture['context_use_cases'] if r['id']=='new-continuation'),
        # Two fresh provider attempts used only by the continuation cell, so that the refusal of
        # a changed continuation is attributable to the continuation axis and not to the
        # (principal, provider_attempt) use reservation the sealed attempt already holds.
        'continuation_control_attempt': common['provider_attempt'] + '-continuation-control',
        'continuation_probe_attempt': common['provider_attempt'] + '-continuation-probe',
    }


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_bundle(bundle, shared, allow_advanced_epoch=False, use_turn_id=None):
    # allow_advanced_epoch is only set by the authority-event oracle for the M0.6.29 use fence
    # (an epoch that advanced past the bound one while every bound source revalidated). The four
    # context-use cells keep the strict equality; nothing below is relaxed for them.
    # use_turn_id is only set by the new-continuation cell: the sealed continuation axis is the
    # use request's turn identity, so exactly one bundle is allowed to carry a turn that differs
    # from the recall context turn - and it must then be that exact value, never anything else.
    recall = bundle['recall']; wire = recall['execution']; result = wire['result']; request = bundle['request']
    shared['check_execution_wire'](wire, recall['context'], recall['plan'])
    require(len(result['items']) == 2 and len(bundle['pages']) == 2 and len(bundle['fragments']) == 2,
            'current-use requires two actual items/pages/fragments')
    bindings = []
    for i, (page_event, fragment_event, item) in enumerate(zip(bundle['pages'], bundle['fragments'], result['items'], strict=True)):
        page = page_event['page']; fragment = fragment_event['fragment']; selected = item['selected_item']
        require(page_event['request']['item_offset'] == i and page_event['request']['page_ordinal'] == i+1
                and page_event['request']['max_items'] == 1
                and page_event['request']['max_bytes'] == recall['plan']['budget']['max_bytes']
                and page_event['request']['requested_at'] == result['evaluated_at']
                and page_event['request']['result_id'] == result['result_id']
                and page_event['request']['result_hash'] == wire['result_hash'],
                'page request does not cover each whole item exactly once')
        page_hash = domain('simple-harness/recall-result-page/v1', page)
        page_bindings = [{'binding_kind':'selected_item','ordinal':i+1,
                          'item_id':selected['item_id'],'item_hash':wire['result_item_hashes'][i]}]
        require(page_event['page_hash'] == page_hash and page['bindings'] == page_bindings
                and page['result_id'] == result['result_id'] and page['result_hash'] == wire['result_hash']
                and page['page_ordinal'] == i+1 and page['item_offset'] == i
                and page['complete'] is (i == 1) and page['byte_count'] == sum(len(canonical(b)) for b in page_bindings),
                'actual whole-item page membership/offset/completion/bytes differ')
        expected = dict(decision_id=wire['decision']['decision_id'], decision_hash=wire['decision_hash'],
            result_id=result['result_id'], result_hash=wire['result_hash'], item_id=selected['item_id'],
            item_hash=wire['result_item_hashes'][i], conflict_group_id=None, confirmation_hash=None,
            result_group_hash=None, page_id=page['page_id'], page_hash=page_hash,
            use_receipt_id=None, use_receipt_hash=None, public_payload_hash=digest(item['public_payload']))
        short = selected['source_kind'] == 'short_horizon'
        require(fragment['recall_binding'] == expected and fragment['public_payload'] == item['public_payload']
                and fragment['public_payload_hash'] == expected['public_payload_hash']
                and fragment['source_ref'] == selected['source_ref'] and fragment['source_revision'] == selected['source_revision']
                and (fragment['source_revision'] is None) is short
                and fragment['fragment_type'] == ('short_horizon' if short else 'recalled_memory')
                and fragment['byte_estimate'] == len(canonical(item['public_payload']))
                and fragment['token_estimate'] == len(canonical(item['public_payload']))
                and all(fragment[k] == recall['context'][k] for k in ('subject', 'run_id', 'disclosure_context', 'evidence_refs')),
                'fragment source/page/identity binding differs')
        fragment_hash = domain('simple-harness/context-fragment/v2', fragment)
        require(fragment_event['fragment_hash'] == fragment_hash, 'fragment hash differs')
        bindings.append({'fragment_id': fragment['fragment_id'], 'fragment_hash': fragment_hash})
    require(len({r['fragment_id'] for r in bindings}) == 2, 'duplicate fragment identity')
    require(request['snapshot_fragment_bindings'] == bindings and request['snapshot_manifest_hash'] == digest(bindings)
            and request['item_bindings'] == [{'item_id': item['selected_item']['item_id'], 'item_hash': ih}
                for item, ih in zip(result['items'], wire['result_item_hashes'], strict=True)]
            and request['decision_id'] == wire['decision']['decision_id'] and request['decision_hash'] == wire['decision_hash']
            and request['result_id'] == result['result_id'] and request['result_hash'] == wire['result_hash']
            and all(request[k] == recall['context'][k] for k in ('subject', 'run_id'))
            and (request['turn_id'] == recall['context']['turn_id'] if use_turn_id is None
                 else request['turn_id'] == use_turn_id != recall['context']['turn_id']),
            'request two-item snapshot/identity binding differs')
    if 'receipt' in bundle:
        receipt = bundle['receipt']
        require('exception' not in bundle and bundle['receipt_hash'] == domain('simple-harness/recall-context-use-receipt/v1', receipt)
                and receipt['request_hash'] == domain('simple-harness/recall-context-use-request/v1', request)
                and all(receipt[k] == request[k] for k in ('subject','run_id','turn_id','provider_attempt_id',
                    'decision_id','decision_hash','result_id','result_hash','item_bindings','snapshot_manifest_hash'))
                and type(receipt['authority_epoch']) is int
                and (receipt['authority_epoch'] == result['authority_epoch']
                     or (allow_advanced_epoch and receipt['authority_epoch'] > result['authority_epoch']))
                and receipt['policy_hash'] == result['policy_hash']
                    and request['requested_at'] == receipt['authorized_at'] < receipt['expires_at'] <= result['authority_expires_at'],
                'receipt public hash/epoch/policy/request/time differs')


def check_new_continuation(fixture, o, recipe, checks, shared):
    """Sealed continuation axis, expressed on the public contract as the use-request turn id.

    RUN-07 recorded the sealed row as "no corresponding public field". That is wrong: Harness
    enqueues a user continuation with turn_id=continuation_id (runtime/kernel.py:1284) and
    carries it as context_use_turn_id (kernel.py:1299), so "same run, same provider attempt,
    different continuation" is exactly "same run, same provider attempt, different context-use
    turn". The sealed obligation NEW_AUTHORIZATION_REQUIRED holds on this contract in a strictly
    stronger form and is witnessed with a paired control, in one database at one clock:

      * sealed attempt + changed continuation -> MemoryIdempotencyConflict; the reserved receipt
        is not reusable and its own validate_request refuses the changed request;
      * never-used attempt + changed continuation -> typed_recall_context_use_invocation_binding
        _invalid: the old result cannot be used in another continuation at all, by any attempt
        (Memory backends/sqlite_v5.py compares typed_request.turn_id with the stored recall
        context turn before it ever reaches the epoch fence);
      * never-used attempt + ORIGINAL continuation -> a real receipt. The refusal above is
        therefore attributable to the continuation axis and to nothing else.

    expected_receipt_hash stays untouched (legacy literal, never compared here).
    """
    sealed = next(r for r in fixture['context_use_cases'] if r['id'] == 'new-continuation')
    common = fixture['authority_event_common_binding']
    require(sealed['expect'] == 'NEW_AUTHORIZATION_REQUIRED'
            and sealed['receipt']['outcome'] == 'NEW_AUTHORIZATION_REQUIRED'
            and sealed['receipt']['run_id'] == common['run_id']
            and sealed['receipt']['turn_id'] == common['turn_id']
            and sealed['receipt']['provider_attempt'] == common['provider_attempt']
            and sealed['receipt']['continuation_id'] == recipe['next_continuation'] != common['continuation_id'],
            'sealed continuation row is not the same-run/same-attempt/changed-continuation pair')
    first = o['uses']['first']
    probes = o['continuation']
    require(set(probes) == {'same_attempt', 'fresh_attempt', 'control'}, 'continuation probe coverage differs')
    stable = lambda request: {k: v for k, v in request.items()
                              if k not in {'turn_id', 'requested_at', 'provider_attempt_id'}}
    for label, bundle in probes.items():
        changed = label != 'control'
        require(bundle['recall'] == o['initial'] and bundle['request']['requested_at'] == recipe['next_use_at'],
                'continuation probe changed the original recall or clock')
        check_bundle(bundle, shared, use_turn_id=recipe['next_continuation'] if changed else None)
        require(stable(bundle['request']) == stable(first['request'])
                and bundle['request']['run_id'] == recipe['run_id']
                and bundle['request']['turn_id'] == (recipe['next_continuation'] if changed else recipe['turn_id']),
                'continuation probe changed more than the continuation identity')
    require(probes['same_attempt']['request']['provider_attempt_id'] == recipe['attempt']
            and probes['fresh_attempt']['request']['provider_attempt_id'] == recipe['continuation_probe_attempt']
            and probes['control']['request']['provider_attempt_id'] == recipe['continuation_control_attempt']
            and len({recipe['attempt'], recipe['continuation_probe_attempt'],
                     recipe['continuation_control_attempt']}) == 3,
            'continuation probe attempts are not the three distinct frozen identities')
    require('receipt' not in probes['same_attempt'] and probes['same_attempt'].get('exception') == {
                'type': 'MemoryIdempotencyConflict', 'reason': 'RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT'},
            'the reserved attempt accepted a changed continuation instead of requiring a new authorization')
    require('receipt' not in probes['fresh_attempt'] and probes['fresh_attempt'].get('exception') == {
                'type': 'MemoryValidationError', 'reason': 'typed_recall_context_use_invocation_binding_invalid'},
            'a never-used attempt authorized the old result inside a different continuation')
    control = probes['control']
    require('receipt' in control and not control.get('exception')
            and control['receipt']['receipt_id'] != first['receipt']['receipt_id']
            and control['receipt_hash'] != first['receipt_hash']
            and control['receipt']['result_hash'] == first['receipt']['result_hash']
            and control['receipt']['item_bindings'] == first['receipt']['item_bindings'],
            'the paired control (same never-used attempt family, original continuation) was refused')
    validation = o['receipt_validation']['different_continuation']
    require(validation['request'] == probes['same_attempt']['request'] and validation['accepted'] is False
            and validation['exception'] == {'type': 'ValueError', 'reason': 'receipt request_hash differs'},
            'the first receipt accepted a request carrying a different continuation')
    checks.append('changed context-use turn refused by both the reservation and the invocation binding, '
                  'while the same never-used attempt under the original continuation is authorized')


def assess(fixture, cell, shared):
    o = cell['observations']; checks = []
    try:
        require(cell['cell_id'] in CELLS and o['context_use_cell'] == cell['cell_id'], 'context-use cell identity differs')
        require(o.get('phase') == 'complete' and not o.get('exception'), 'public context-use execution incomplete: '+str(o.get('exception')))
        recipe = inputs(fixture)
        require(o['input'] == recipe and o['executor_version'] == VERSION, 'frozen construction input differs')
        require(len(o['sources']) == 2, 'exactly two real mutation sources required')
        ids = []
        for i, seed in enumerate(recipe['seeds']):
            shared['check_seed_authority'](o, {'seed': seed}, shared['semantic_source'](**seed['payload']),
                source_index=i, check_recall_refs=False)
            operation = o['sources'][i]['receipt']['operations'][0]
            ids.append(operation['memory_id'])
            require(operation['revision'] == 1, 'seed is not a real initial head')
        require(len(set(ids)) == 2, 'two independent memory IDs required')
        initial = o['initial']; result = initial['execution']['result']
        shared['check_execution_wire'](initial['execution'], initial['context'], initial['plan'])
        require(len(result['items']) == 2 and not result['confirmation_groups'], 'two ordinary items required')
        by_source = {r['selected_item']['source_ref']: r for r in result['items']}
        require(set(by_source) == set(ids), 'selected source membership differs')
        for memory_id, seed in zip(ids, recipe['seeds'], strict=True):
            item = by_source[memory_id]
            require(item['public_payload'] == seed['payload'] and item['selected_item']['source_revision'] == 1
                    and item['selected_item']['source_content_hash'] == digest(shared['semantic_source'](**seed['payload']))
                    and item['selected_item']['public_payload_hash'] == digest(seed['payload']), 'independent source payload/head differs')
        require(result['evaluated_at'] == recipe['evaluated_at'] and initial['context']['expires_at'] == recipe['context_expires_at']
                and initial['context']['run_id'] == recipe['run_id'] and initial['context']['turn_id'] == recipe['turn_id'],
                'frozen original clocks/run/turn differ')
        checks.append('two independently bound public admissions/mutations/recall items')
        before = cell['cell_id'] not in {'current-use/context:suppression-first','current-use/authority:suppression'}
        expected = {'after_suppression'} | ({'first','replay_after_reopen'} if before else set())
        if cell['cell_id'].endswith('duplicate-same-provider-attempt'): expected.add('duplicate')
        if cell['cell_id'].endswith('new-provider-attempt'): expected.add('new_before_suppression')
        require(set(o['uses']) == expected, 'scenario-specific operation coverage differs')
        for label, bundle in o['uses'].items():
            expected_time = recipe['next_use_at'] if label in {'after_suppression','new_before_suppression'} and before else recipe['use_at']
            require(bundle['recall'] == initial and bundle['request']['requested_at'] == expected_time, 'use changed original recall/time')
            check_bundle(bundle, shared)
            expected_attempt = (recipe['after_attempt'] if label == 'after_suppression' and before
                                else recipe['next_attempt'] if label == 'new_before_suppression' else recipe['attempt'])
            require(bundle['request']['provider_attempt_id'] == expected_attempt, 'provider attempt identity differs')
            if label == 'after_suppression':
                require(bundle.get('exception') == {'type':'MemoryValidationError','reason':'RECALL_AUTHORITY_STALE'}
                        and 'receipt' not in bundle, 'stale authority did not reject actual new use')
            else:
                require('receipt' in bundle and not bundle.get('exception'), 'legal public use rejected')
        if before:
            first = o['uses']['first']
            require(o['uses']['replay_after_reopen']['receipt'] == first['receipt']
                    and o['uses']['replay_after_reopen']['receipt_hash'] == first['receipt_hash'], 'durable use replay changed')
            if 'duplicate' in o['uses']:
                require(o['uses']['duplicate']['receipt'] == first['receipt'], 'same-attempt replay changed')
            if 'new_before_suppression' in o['uses']:
                require(o['uses']['new_before_suppression']['receipt']['receipt_id'] != first['receipt']['receipt_id'], 'new attempt reused grant')
            if 'continuation' in o:
                check_new_continuation(fixture, o, recipe, checks, shared)
            validation = o['receipt_validation']
            require(validation['same_request']['request'] == first['request']
                    and validation['different_attempt']['request'] == {**first['request'],'provider_attempt_id':recipe['next_attempt']}
                    and validation['same_request']['accepted'] is True and validation['different_attempt']['accepted'] is False
                    and validation['different_attempt']['exception'] == {'type':'ValueError','reason':'receipt request_hash differs'},
                    'public receipt attempt validation differs')
        checks.append('actual two-page/two-fragment snapshot, receipt hashes and attempt replay')
        suppression = o['suppression']; request = suppression['request']; decision = suppression['decision']
        require(request['scope_ref'] == ids[0] and request['scope_kind'] == 'memory' and request['purpose'] is None
                and request['subject'] == initial['context']['subject'] and request['reason_code'] == 'user_forget'
                and decision['scope_ref'] == ids[0] and decision['action'] == 'directive', 'suppression target/scope differs')
        post = o['after']; shared['check_execution_wire'](post['execution'], post['context'], post['plan'])
        after = post['execution']['result']
        require(type(result['authority_epoch']) is int and type(after['authority_epoch']) is int
                and after['authority_epoch'] == result['authority_epoch'] + 1
                and after['policy_hash'] == result['policy_hash'], 'suppression epoch/policy delta differs')
        require(len(after['items']) == 1 and after['items'][0]['selected_item']['source_ref'] == ids[1]
                and after['items'][0]['public_payload'] == recipe['seeds'][1]['payload'], 'suppression leaked target or hid unaffected item')
        replay = o['historical_replay']
        require(replay['replayed'] and replay['candidate_query_count'] == 0
                and replay['result'] == result and replay['decision'] == initial['execution']['decision'], 'historical exact replay differs')
        order = o['order']
        require(order.index('suppression_commit') < order.index('after_suppression'), 'suppression did not precede rejection')
        if before: require(order.index('first') < order.index('suppression_commit'), 'receipt did not commit first')
        checks.append('actual suppression commit order, epoch+1, unchanged policy and unaffected-item control')
        if cell['cell_id'].endswith('wrong-snapshot'):
            attack = o['wrong_snapshot']
            base = o['uses']['first']['request']
            require(attack['input'] == {**base,'snapshot_manifest_hash':'f'*64}
                    and attack.get('exception') == {'type':'ValueError','reason':'snapshot_manifest_hash differs from fragment bindings'},
                    'exact wrong manifest DTO rejection differs')
            changed = o['changed_snapshot']
            reversed_bindings = list(reversed(base['snapshot_fragment_bindings']))
            require(changed['request'] == {**base, 'snapshot_fragment_bindings':reversed_bindings,
                                           'snapshot_manifest_hash':digest(reversed_bindings)}
                    and 'receipt' not in changed and changed.get('exception') == {
                        'type':'MemoryIdempotencyConflict','reason':'RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT'},
                    'valid changed snapshot under same attempt did not reject at public use')
            checks.append('wrong-manifest DTO and changed-snapshot public operation reject independently')
        if cell['cell_id'].endswith(('receipt-first','duplicate-same-provider-attempt')):
            return dict(status='BLOCKED', reason='CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED',
                        business_assertions=checks, remaining_classification='harness_consumer_witness_unverified',
                        executor_version=VERSION)
        return dict(status='PASS', reason='original two-item Memory authority obligation verified',
                    business_assertions=checks, executor_version=VERSION)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks,executor_version=VERSION)
