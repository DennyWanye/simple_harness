"""Input-only real public two-candidate selection cases (rank tie-break and greedy budget).

Every candidate is produced by real public operations: one cognitive episode
(apply_memory_mutation_plan) plus one short-horizon chunk (11 public conversation
registrations + rebuild_short_horizon_projection). Both hit exactly the full_text lane
at rank 1 in their own collector, so the two candidates carry the identical public score
and the identical matched lane count - the only public construction on this candidate that
can tie the two highest-precedence ordering fields. Acceptance lives in
runners/typed_recall_selection_oracle.py; nothing here asserts an outcome.
"""
import dataclasses as dc
import importlib.util
from pathlib import Path

import simple_harness_memory as m

CELLS = {
    'selection-budget/tie-source-kind',
    'selection-budget/tie-newer-source-time',
    'selection-budget/tie-score',
    'selection-budget/budget:greedy',
    'selection-budget/ranking-order',
    'selection-budget/dedupe:cross-source-merge',
    'selection-budget/dedupe:cross-source-no-merge-evidence-differs',
    'selection-budget/budget:short-emoji',
    'selection-budget/budget:short-escaping',
    'selection-budget/budget:deadline',
}


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_envelope(items):
    helper = load('typed_recall_case_manager')
    return helper.canonical([
        {'source_kind': item['selected_item']['source_kind'],
         'memory_type': item['selected_item']['memory_type'],
         'payload': item['public_payload']}
        for item in items])


async def seed_variant(case, spec, variant, budget=None):
    """One real database: one episode + one short-horizon chunk, then one typed recall."""
    short = load('typed_recall_short_cases')
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    start = variant['episode_source_time']
    observed['episode_seed'] = {
        'memory_type': 'episode',
        'payload': {'title': spec['episode_title'], 'participants': ['user'], 'goals': ['recall'],
                    'actions': ['store'], 'results': ['stored'], 'impacts': ['none'],
                    'occurred_interval': {'start': start, 'end': start + spec['episode_duration']}},
    }
    await case.seed(observed['episode_seed'], operation_id='selection-create-1', evidence_id='selection-user-1')
    for sequence in range(1, 12):
        await short.register(case, sequence,
                             spec['short_text'] if sequence == 1 else f'unrelated filler {sequence}',
                             variant['short_source_time'] if sequence == 1 else case.now - 11 + sequence - 1)
    observed['projection_build'] = dc.asdict(
        await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now))
    arguments = dict(query=spec['query'], memory_types=('episode',), short=True, key=variant['key'])
    if budget is not None:
        arguments['budget'] = dict(budget)
        observed['budget'] = dict(budget)
    observed['recall'] = await case.recall(**arguments)
    observed['replay'] = (await case.recall(**arguments))['execution']
    return observed


async def seed_two_episode_variant(case, spec, variant):
    """One real database, two real cognitive episodes of the same memory type.

    The two differ only in how many times the query token occurs in the title, so the cognitive
    collector gives them full_text ranks 1 and 2 inside the SAME (memory_type, lane) namespace and
    therefore two different RRF scores, while every other ordering input stays equal. The variant
    decides which of the two carries the newer occurred_start. Nothing here asserts an outcome.
    """
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    for label, title in (('high', spec['high_title']), ('low', spec['low_title'])):
        start = variant[label + '_source_time']
        seed = {'memory_type': 'episode',
                'payload': {'title': title, 'participants': ['user'], 'goals': ['recall'],
                            'actions': ['store'], 'results': ['stored'], 'impacts': ['none'],
                            'occurred_interval': {'start': start, 'end': start + spec['episode_duration']}}}
        observed[label + '_seed'] = seed
        await case.seed(seed, operation_id='selection-' + label, evidence_id='selection-user-' + label)
    arguments = dict(query=spec['query'], memory_types=('episode',), key=variant['key'])
    observed['recall'] = await case.recall(**arguments)
    observed['replay'] = (await case.recall(**arguments))['execution']
    return observed


async def seed_four_candidate_order(case, spec, variant):
    """Four real candidates in one database: three cognitive heads plus one short chunk.

    Two semantic claims differ only in how often the query token occurs, so they land at
    full_text ranks 1 and 2 inside the semantic namespace (two different scores); the episode
    and the short chunk each take rank 1 in their own namespace and therefore carry the same
    score as the higher semantic claim. Every candidate is given the same source time, so the
    order below the score is decided by source_kind and then memory_type. Nothing here asserts
    an outcome.
    """
    short = load('typed_recall_short_cases')
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    at = variant['source_time']
    observed['episode_seed'] = {'memory_type': 'episode',
        'payload': {'title': spec['episode_title'], 'participants': ['user'], 'goals': ['recall'],
                    'actions': ['store'], 'results': ['stored'], 'impacts': ['none'],
                    'occurred_interval': {'start': at, 'end': at + spec['episode_duration']}}}
    await case.seed(observed['episode_seed'], operation_id='order-episode', evidence_id='order-user-episode')
    for label in ('high', 'low'):
        seed = {'memory_type': 'semantic', 'payload': {'subject_entity': spec['query'],
                'predicate': 'prefers', 'object_value': spec[label + '_object'], 'qualifiers': []}}
        observed[label + '_seed'] = seed
        await case.seed(seed, operation_id='order-' + label, evidence_id='order-user-' + label)
    for sequence in range(1, 12):
        await short.register(case, sequence,
                             spec['short_text'] if sequence == 1 else f'unrelated filler {sequence}',
                             at if sequence == 1 else case.now - 11 + sequence - 1)
    observed['projection_build'] = dc.asdict(
        await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now))
    arguments = dict(query=spec['query'], memory_types=('episode', 'semantic'), short=True,
                     key=variant['key'], budget=dict(spec['budget']))
    observed['recall'] = await case.recall(**arguments)
    observed['replay'] = (await case.recall(**arguments))['execution']
    return observed


async def seed_short_duplicates(case, spec, variant):
    """One or two conversation groups whose rendered line splits into two identical chunks.

    A registration line longer than the frozen chunk limit is cut into pieces that are each
    re-rendered with the role prefix; the input below is built so both pieces are byte
    identical. Two chunks of one group therefore carry the same public payload AND the same
    evidence manifest, while two chunks from two groups carry the same payload and different
    manifests - exactly the two sealed dedupe inputs. Nothing here asserts an outcome.
    """
    short = load('typed_recall_short_cases')
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    groups = variant['group_count']
    for sequence in range(1, groups + 1):
        await short.register(case, sequence, spec['split_text'], variant['source_time'])
    for sequence in range(groups + 1, groups + 11):
        await short.register(case, sequence, f'unrelated filler {sequence}',
                             case.now - 11 + sequence - 1)
    observed['projection_build'] = dc.asdict(
        await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now))
    hits = await case.manager.recall_short_horizon(principal=case.principal, query=spec['query'],
                                                   disclosure_context=case.disclosure, now=case.now)
    case.events.append({'call': 'recall_short_horizon', 'query': spec['query']})
    observed['chunk_hits'] = [{'chunk_ref': hit.chunk_ref, 'content': hit.content,
                               'content_hash': hit.content_hash, 'occurred_at': hit.occurred_at}
                              for hit in hits.hits]
    arguments = dict(query=spec['query'], memory_types=('semantic',), short=True,
                     key=variant['key'], budget=dict(spec['budget']))
    observed['recall'] = await case.recall(**arguments)
    observed['replay'] = (await case.recall(**arguments))['execution']
    return observed


async def seed_short_budget_literal(case, spec, variant):
    """One real short candidate carrying the sealed literal content, then the two limits.

    The unbudgeted probe fixes the exact canonical envelope of the real item; the sealed
    exact_limit / limit_plus_one relation is then replayed against those real sizes.
    """
    short = load('typed_recall_short_cases')
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    for sequence in range(1, 12):
        await short.register(case, sequence,
                             spec['short_text'] if sequence == 1 else f'unrelated filler {sequence}',
                             variant['source_time'] if sequence == 1 else case.now - 11 + sequence - 1)
    observed['projection_build'] = dc.asdict(
        await case.manager.rebuild_short_horizon_projection(principal=case.principal, now=case.now))
    arguments = dict(query=spec['query'], memory_types=('semantic',), short=True)
    observed['probe'] = await case.recall(**arguments, key=variant['key'] + '-probe')
    items = observed['probe']['execution']['result']['items']
    encoded = canonical_envelope(items)
    size = len(encoded)
    tokens = max(1, len(encoded.decode()), (size + 2) // 3)
    observed['measured'] = {'utf8_bytes': size, 'unicode_codepoints': len(encoded.decode()),
                            'token_estimate': tokens}
    base = dict(spec['budget_base'])
    observed['limits'] = {}
    for label, budget in (('exact', {'max_bytes': size, 'max_tokens': tokens}),
                          ('bytes_over', {'max_bytes': size - 1, 'max_tokens': tokens}),
                          ('tokens_over', {'max_bytes': size, 'max_tokens': tokens - 1})):
        limit = {**base, **budget}
        observed['limits'][label] = {'budget': limit,
            'recall': await case.recall(**arguments, key=variant['key'] + '-' + label, budget=limit)}
    return observed


async def seed_deadline(case, spec, variant):
    """One legal minimum-budget recall whose deadline is spent before any candidate is read.

    The sealed range admits deadline_ms=1, and the public deadline starts at API entry, so a
    request whose own validation/hashing/admission costs more than one millisecond must end as
    DEADLINE_EXCEEDED with no payload. The padding evidence envelopes are construction inputs
    that make that cost deterministic; the control repeats the identical request at the sealed
    public_deadline_ms and must return the real item.
    """
    observed = {'variant': dict(variant), 'calls': case.events, 'sources': case.sources}
    observed['registration'] = dc.asdict(await case.manager.register_principal_owner(
        case.principal, m.MemoryScope.personal(case.principal.actor_id)))
    seed = {'memory_type': 'semantic', 'payload': {'subject_entity': spec['query'],
            'predicate': 'prefers', 'object_value': spec['query'], 'qualifiers': []}}
    observed['seed'] = seed
    await case.seed(seed, operation_id='deadline-seed', evidence_id='deadline-user-1')
    for index in range(variant['padding_evidence']):
        await case.evidence(f'deadline padding evidence {index}', f'deadline-padding-{index}')
    tiny = {**spec['budget_base'], 'deadline_ms': spec['minimum_deadline_ms']}
    full = {**spec['budget_base'], 'deadline_ms': spec['public_deadline_ms']}
    observed['tiny_budget'], observed['full_budget'] = dict(tiny), dict(full)

    async def attempt(label, *, key, budget, query=None):
        record = {'key': key, 'budget': dict(budget), 'query': query or spec['query']}
        try:
            bundle = await case.recall(query=record['query'], key=key, budget=budget)
            record['execution'] = bundle['execution']
            record['item_count'] = len(bundle['execution']['result']['items'])
        except Exception as exc:  # noqa: BLE001 - the parent oracle owns the verdict
            record['exception'] = {'type': type(exc).__name__, 'reason': str(exc)}
            record.update(cases_module().rejection_wire(exc))
        observed[label] = record
        return record

    await attempt('first', key=variant['key'], budget=tiny)
    await attempt('retry', key=variant['key'], budget=tiny)
    await case.close()
    await case.open()
    observed['reopened'] = True
    await attempt('after_reopen', key=variant['key'], budget=tiny)
    await attempt('changed_body', key=variant['key'], budget=tiny, query=spec['query'] + ' changed')
    await attempt('control', key=variant['key'] + '-control', budget=full)
    return observed


def cases_module():
    return load('typed_recall_public_cases')


async def run_cases(request, workspace):
    helper = load('typed_recall_case_manager')
    rows = []
    for name in sorted(request['cells']):
        spec = request['selection'][name.split('/', 1)[1]]
        observed = {'selection_cell': name, 'input': spec, 'executor_version': request['version'],
                    'variants': {}, 'phase': 'start'}
        try:
            for variant in spec['variants']:
                case = helper.CaseManager(
                    workspace / ('selection-' + variant['key'] + '.sqlite'),
                    now=spec['now'])
                await case.open()
                try:
                    budget = None
                    if variant.get('budget_from') is not None:
                        # Construction-derived, not gold: the byte limit is the exact canonical
                        # size of the smaller item observed in the unbudgeted probe.
                        probe = observed['variants'][variant['budget_from']]
                        items = probe['recall']['execution']['result']['items']
                        budget = {**spec['budget_base'],
                                  'max_bytes': len(canonical_envelope(items[-1:]))}
                    builder = {'two-episode-lexical': seed_two_episode_variant,
                               'four-candidate-order': seed_four_candidate_order,
                               'short-duplicate-chunks': seed_short_duplicates,
                               'short-budget-literal': seed_short_budget_literal,
                               'deadline-minimum-budget': seed_deadline}.get(spec.get('construction'))
                    if builder is not None:
                        observed['variants'][variant['key']] = await builder(case, spec, variant)
                    else:
                        observed['variants'][variant['key']] = await seed_variant(case, spec, variant, budget)
                finally:
                    await case.close()
            observed['phase'] = 'complete'
        except Exception as exc:  # noqa: BLE001 - the parent oracle owns every verdict
            observed['exception'] = {'type': type(exc).__name__, 'reason': str(exc)}
        rows.append({'cell_id': name, 'status': 'OBSERVED', 'reason': '', 'observations': observed})
    return rows
