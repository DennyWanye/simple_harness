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
    'selection-budget/budget:greedy',
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
                    observed['variants'][variant['key']] = await seed_variant(case, spec, variant, budget)
                finally:
                    await case.close()
            observed['phase'] = 'complete'
        except Exception as exc:  # noqa: BLE001 - the parent oracle owns every verdict
            observed['exception'] = {'type': type(exc).__name__, 'reason': str(exc)}
        rows.append({'cell_id': name, 'status': 'OBSERVED', 'reason': '', 'observations': observed})
    return rows
