"""Independent selection-lane oracle (rank tie-break and greedy budget). Standard library only.

Construction note, fixed from the pinned public contract before execution
(Memory core/recall.py::_stable_key and backends/sqlite_v5.py candidate collectors):

  score = round(sum(weight[lane] / (RRF_K + rank[lane])), 12) over the matched lanes, and the
  cognitive collector and the short-horizon collector assign their lane ranks in two independent
  namespaces. A cognitive candidate and a short-horizon candidate that each match only full_text
  therefore both carry rank 1 and the identical score 0.30/61 with matched_lane_count 1. Two
  candidates of the same source kind can never tie, because the RRF lane weights are pairwise
  distinct and two candidates in one lane always receive distinct ranks. The cognitive/short pair
  is thus the ONLY public construction that equalises the two highest-precedence fields of the
  sealed stable_tie_break_fields, which is what a tie-break cell has to do before it can observe
  the field under test.

  source_time is per memory type: a semantic claim reports created_at (write time, not steerable),
  an episode record reports occurred_start, which is a public payload field. Every case below
  therefore uses an episode so that the -typed_source_time field can be set on both sides.

Runner choices, recorded as inputs (no sealed value is rewritten):
  * tie-source-kind: the sealed pair shares source_ref "same" and gives the short-horizon member
    memory_type "semantic". Neither is publicly constructible (ids are content-addressed and a
    short chunk always projects memory_type null), and both fields sit BELOW source_kind in the
    sealed precedence list, so they are never read once source_kind decides. The construction
    equalises every field above source_kind and differs only on source_kind.
  * tie-newer-source-time: the sealed pair is two cognitive semantic memories, which cannot tie
    on score (see above). The obligation under test is that -typed_source_time outranks every
    field below it, so the case is run in both directions over the same frozen pair of times; the
    short_newer direction puts the short-horizon candidate first and therefore contradicts the
    source_kind order, which is a strictly stronger witness of the sealed precedence.
  * tie-score: the sealed pair is not a tie at all - it is "higher score wins even though the
    other candidate has more matched lanes and a newer source time". matched_lane_count cannot be
    inverted publicly (see the reachability note below), so the construction inverts the strongest
    field that can be: two cognitive episodes of the same memory type, one containing the query
    token twice and one once, take full_text ranks 1 and 2 in the same namespace and therefore
    carry 0.30/61 and 0.30/62; the lower-scored one is given the NEWER occurred_start. Both
    directions of that time pair are run, and the higher score must come first in both.
  * budget:greedy: the sealed item_bytes 200/120 with max_bytes 120 are pre-encoding literals; a
    single real public envelope already exceeds them. The sealed RELATION (first item over the
    limit, second item exactly at it, first skipped, second selected, truncated) is asserted
    against the real canonical byte sizes, recomputed here independently of the candidate.

Public tie reachability (the adjudication behind UNPAIRABLE_TIE_CELLS and behind the tie-score
construction above):

  Lane ranks live in per-namespace tables. The cognitive collector ranks per (memory_type, lane)
  and the short-horizon collector ranks in its own table, so two candidates in the SAME namespace
  always receive distinct ranks in every lane they share and therefore always carry distinct RRF
  scores. Two candidates can only tie on -rrf_score_12dp if they sit in different namespaces:
  they differ in source_kind, or (both cognitive) differ in memory_type - and both of those
  fields sit ABOVE source_ref/source_revision in the sealed precedence list. Furthermore every
  lane except full_text and vector is a hard FILTER on this contract (a candidate that does not
  match the entity constraint / task scope / time window is dropped, not merely unranked), so two
  surviving candidates always share the same entity/task_scope/temporal lane set and their
  matched_lane_count can differ only through the full_text lane, whose weight 0.30/(60+rank) also
  moves the score. Sources: Memory core/recall.py::_stable_key and RRF_WEIGHTS,
  backends/sqlite_v5.py::_collect_typed_recall_candidates (per-memory_type lane ranking, entity /
  task_scope / temporal drops) and ::_collect_typed_recall_short_candidates (own lane table).
"""
import hashlib
import json
from datetime import datetime

VERSION = 2
QUERY_TOKEN = 'zebrafish'
CELLS = ('selection-budget/budget:greedy', 'selection-budget/tie-newer-source-time',
         'selection-budget/tie-score', 'selection-budget/tie-source-kind')

# Adjudicated on the frozen public contract, not "not implemented yet": each of these sealed
# tie-break rows needs a public pair that the reachability argument above forbids. They stay
# BLOCKED with an exact reason instead of being weakened into a different obligation.
UNPAIRABLE_TIE_CELLS = {
    'selection-budget/tie-matched-lane-count':
        'PUBLIC_TIE_NOT_CONSTRUCTIBLE:an equal rrf score with an unequal matched_lane_count cannot be built. '
        'Every lane except full_text and vector filters out non-matching candidates, so two surviving '
        'candidates share the same entity/task_scope/temporal lane set; the only remaining differentiator is '
        'the full_text lane, whose weight 0.30/(60+rank) also changes the score, and the vector lane is '
        'unavailable on this candidate. The sealed pair therefore has no public construction.',
    'selection-budget/tie-memory-type-empty':
        'PUBLIC_TIE_NOT_CONSTRUCTIBLE:memory_type_or_empty is only read once source_kind ties, i.e. both '
        'candidates are cognitive_memory - and a cognitive candidate always carries its head memory_type; '
        'only a short_horizon candidate projects memory_type null, and source_kind decides before this field. '
        'The sealed "cognitive candidate with empty memory_type" row is vacuous on the public contract.',
    'selection-budget/tie-source-ref':
        'PUBLIC_TIE_NOT_CONSTRUCTIBLE:source_ref is only read once source_kind AND memory_type tie, which puts '
        'both candidates in one cognitive rank namespace, where every shared lane assigns them distinct ranks '
        'and therefore distinct rrf scores. The tie the sealed row needs cannot exist.',
    'selection-budget/tie-source-revision-or-zero':
        'PUBLIC_TIE_NOT_CONSTRUCTIBLE:source_revision_or_zero is only read once source_ref also ties, which on '
        'the public contract means one and the same current head; two distinct candidates can never reach this '
        'field, and same-namespace candidates cannot tie on score in the first place.',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def seconds(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tie_row(fixture, identifier):
    return next(row for row in fixture['ranking_oracle']['tie_break_cases'] if row['id'] == identifier)


def envelope_bytes(items):
    return len(canonical([{'source_kind': item['selected_item']['source_kind'],
                           'memory_type': item['selected_item']['memory_type'],
                           'payload': item['public_payload']} for item in items]))


def inputs(fixture):
    """Construction inputs only; every time comes from the frozen tie-break rows."""
    kind_row = tie_row(fixture, 'tie-source-kind')
    time_row = tie_row(fixture, 'tie-newer-source-time')
    equal_at = {seconds(candidate['typed_source_time']) for candidate in kind_row['candidates']}
    require(len(equal_at) == 1, 'frozen tie-source-kind row does not equalise typed_source_time')
    equal_at = equal_at.pop()
    newer = seconds(next(c for c in time_row['candidates'] if c['id'] == 'time-newer')['typed_source_time'])
    older = seconds(next(c for c in time_row['candidates'] if c['id'] == 'time-older')['typed_source_time'])
    require(newer > older, 'frozen tie-newer-source-time row is not ordered')
    now = newer + 3600
    score_row = tie_row(fixture, 'tie-score')
    score_high = next(c for c in score_row['candidates'] if c['id'] == 'score-high')
    score_low = next(c for c in score_row['candidates'] if c['id'] == 'score-low')
    high_at, low_at = seconds(score_high['typed_source_time']), seconds(score_low['typed_source_time'])
    require(low_at > high_at, 'frozen tie-score row does not give the lower score the newer source time')
    common = dict(query=QUERY_TOKEN, short_text=QUERY_TOKEN + ' short horizon line',
                  episode_duration=60.0, now=now)
    return {
        'tie-score': {'query': QUERY_TOKEN, 'now': now, 'episode_duration': 60.0,
            'construction': 'two-episode-lexical',
            'high_title': QUERY_TOKEN + ' ' + QUERY_TOKEN + ' cognitive episode',
            'low_title': QUERY_TOKEN + ' cognitive episode',
            'variants': [{'key': 'low-newer', 'high_source_time': high_at, 'low_source_time': low_at},
                         {'key': 'high-newer', 'high_source_time': low_at, 'low_source_time': high_at}]},
        'tie-source-kind': {**common, 'episode_title': QUERY_TOKEN + ' cognitive episode',
            'variants': [{'key': 'equal-source-time', 'episode_source_time': equal_at,
                          'short_source_time': equal_at, 'budget_from': None}]},
        'tie-newer-source-time': {**common, 'episode_title': QUERY_TOKEN + ' cognitive episode',
            'variants': [{'key': 'cognitive-newer', 'episode_source_time': newer,
                          'short_source_time': older, 'budget_from': None},
                         {'key': 'short-newer', 'episode_source_time': older,
                          'short_source_time': newer, 'budget_from': None}]},
        'budget:greedy': {**common, 'episode_title': QUERY_TOKEN + ' cognitive episode ' + 'X' * 160,
            'budget_base': {'max_items': 8, 'max_tokens': 2048, 'deadline_ms': 2000},
            'variants': [{'key': 'unbudgeted', 'episode_source_time': newer,
                          'short_source_time': older, 'budget_from': None},
                         {'key': 'budgeted', 'episode_source_time': newer,
                          'short_source_time': older, 'budget_from': 'unbudgeted'}]},
    }


def check_variant(fixture, spec, variant, shared, *, expect_two=True):
    """Every binding a two-candidate recall must carry, before any ordering is read."""
    recall = variant['recall']
    wire = recall['execution']
    shared['check_execution_wire'](wire, recall['context'], recall['plan'])
    require(variant['projection_build'] == {'projected_chunk_count': 1, 'removed_chunk_count': 0,
                                            'split_group_count': 0, 'truncated_group_count': 0,
                                            'audit_id': variant['projection_build'].get('audit_id')}
            and isinstance(variant['projection_build'].get('audit_id'), str),
            'short projection did not project exactly one unsegmented target chunk')
    decision, result = wire['decision'], wire['result']
    require(decision['outcome'] == 'recall' and decision['filtered_candidate_count'] == 2
            and not result['confirmation_groups'],
            'both public candidates did not reach the ordering stage as ordinary items')
    require(recall['context']['query'] == spec['query'] and recall['context']['short_horizon_allowed'] is True
            and recall['now'] == spec['now'], 'frozen query/short carrier/clock differ')
    ranking = fixture['ranking_oracle']
    expected_score = round(ranking['weights']['full_text'] / (ranking['rrf_k'] + 1), 12)
    items = result['items']
    if expect_two:
        require(len(items) == 2 and not result['truncated'], 'unbudgeted recall did not return both items')
    for item in items:
        selected = item['selected_item']
        require(item['score'] == expected_score,
                'candidate did not match exactly the full_text lane at rank 1 in its own collector')
        require(selected['public_payload_hash'] == digest(item['public_payload']),
                'public payload hash binding differs')
        if selected['source_kind'] == 'cognitive_memory':
            require(selected['memory_type'] == 'episode' and selected['source_revision'] == 1
                    and item['public_payload']['occurred_start'] == variant['variant']['episode_source_time'],
                    'cognitive candidate is not the frozen episode head at its frozen occurred_start')
        else:
            require(selected['source_kind'] == 'short_horizon' and selected['source_revision'] is None
                    and selected['memory_type'] is None
                    and item['public_payload'] == {'content': 'user: ' + spec['short_text'],
                                                   'occurred_at': variant['variant']['short_source_time']},
                    'short candidate is not the frozen projected chunk at its frozen occurred_at')
    require(variant['replay']['result'] == result and variant['replay']['decision'] == decision
            and variant['replay']['replayed'], 'durable exact replay of the same request differs')
    return [item['selected_item']['source_kind'] for item in items]


def check_two_episode_variant(fixture, spec, variant, shared, high_score, low_score):
    """Two real cognitive episodes in one rank namespace, differing only in lexical hit count."""
    recall = variant['recall']
    wire = recall['execution']
    shared['check_execution_wire'](wire, recall['context'], recall['plan'])
    plan, context = recall['plan'], recall['context']
    require(plan['retrieval_modes'] == ['full_text'] and not plan['entity_constraints']
            and not plan['task_scope_ids'] and plan['earliest_occurred_at'] is None
            and plan['latest_occurred_at'] is None and plan['include_short_horizon'] is False
            and context['query'] == spec['query'] and recall['now'] == spec['now'],
            'the plan opened a lane other than full_text; matched_lane_count would not be equalised')
    decision, result = wire['decision'], wire['result']
    require(decision['outcome'] == 'recall' and decision['filtered_candidate_count'] == 2
            and not result['confirmation_groups'] and not result['truncated']
            and len(result['items']) == 2,
            'both public candidates did not reach the ordering stage as ordinary items')
    order = []
    for item in result['items']:
        selected = item['selected_item']
        require(selected['source_kind'] == 'cognitive_memory' and selected['memory_type'] == 'episode'
                and selected['source_revision'] == 1
                and selected['public_payload_hash'] == digest(item['public_payload']),
                'candidate is not a real first-revision cognitive episode head')
        title = item['public_payload']['title']
        require(title in {spec['high_title'], spec['low_title']}, 'unexpected candidate title')
        label = 'high' if title == spec['high_title'] else 'low'
        require(item['public_payload']['occurred_start'] == variant['variant'][label + '_source_time'],
                'candidate is not at its frozen occurred_start')
        require(item['score'] == (high_score if label == 'high' else low_score),
                'candidate did not match exactly the full_text lane at its expected rank')
        order.append(label)
    require(order == ['high', 'low'],
            'the higher rrf score was not ordered first')
    require(variant['replay']['result'] == result and variant['replay']['decision'] == decision
            and variant['replay']['replayed'], 'durable exact replay of the same request differs')
    return order


def assess(fixture, cell, shared):
    o = cell['observations']
    checks = []
    name = cell['cell_id']
    try:
        require(o.get('selection_cell') == name and name in CELLS, 'selection cell identity differs')
        require(o.get('phase') == 'complete' and not o.get('exception'),
                'public selection execution incomplete: ' + str(o.get('exception')))
        key = name.split('/', 1)[1]
        spec = inputs(fixture)[key]
        require(o['input'] == spec and o['executor_version'] == VERSION, 'frozen construction input differs')
        fields = fixture['ranking_oracle']['stable_tie_break_fields']
        require(fields[:4] == ['-rrf_score_12dp', '-matched_lane_count', '-typed_source_time', 'source_kind'],
                'sealed stable tie-break precedence differs from the one this oracle proves')
        variants = o['variants']
        require(set(variants) == {v['key'] for v in spec['variants']}, 'variant set differs')

        if key == 'tie-source-kind':
            row = tie_row(fixture, 'tie-source-kind')
            expected = [next(c for c in row['candidates'] if c['id'] == i)['source_kind']
                        for i in row['expected_order']]
            require(expected == ['cognitive_memory', 'short_horizon'], 'sealed source_kind order differs')
            variant = variants['equal-source-time']
            require(variant['variant']['episode_source_time'] == variant['variant']['short_source_time'],
                    'the two candidates were not equalised on typed_source_time')
            order = check_variant(fixture, spec, variant, shared)
            checks.append('two real public candidates tied on rrf score and matched lane count')
            require(order == expected,
                    'source_kind did not break the tie in the sealed direction (cognitive before short)')
            checks.append('every field above source_kind equal, order decided by source_kind exactly as sealed')

        elif key == 'tie-score':
            row = tie_row(fixture, 'tie-score')
            high = next(c for c in row['candidates'] if c['id'] == 'score-high')
            low = next(c for c in row['candidates'] if c['id'] == 'score-low')
            require(row['expected_order'] == ['score-high', 'score-low']
                    and float(high['rrf_score_12dp']) > float(low['rrf_score_12dp'])
                    and low['matched_lane_count'] > high['matched_lane_count']
                    and seconds(low['typed_source_time']) > seconds(high['typed_source_time']),
                    'sealed tie-score row is not "the higher score wins against every field below it"')
            ranking = fixture['ranking_oracle']
            high_score = round(ranking['weights']['full_text'] / (ranking['rrf_k'] + 1), 12)
            low_score = round(ranking['weights']['full_text'] / (ranking['rrf_k'] + 2), 12)
            require(high_score > low_score, 'independent rank-1/rank-2 full_text scores are not ordered')
            for variant in variants.values():
                check_two_episode_variant(fixture, spec, variant, shared, high_score, low_score)
            checks.append('two real cognitive episodes in one rank namespace at full_text ranks 1 and 2, '
                          'equal matched lane count, both directions of the frozen source-time pair')
            inverted = variants['low-newer']['variant']
            require(inverted['low_source_time'] > inverted['high_source_time']
                    and variants['high-newer']['variant']['high_source_time'] > variants['high-newer']['variant']['low_source_time'],
                    'the two variants do not run the frozen time pair in both directions')
            items = variants['low-newer']['recall']['execution']['result']['items']
            require(items[0]['public_payload']['title'] == spec['high_title']
                    and items[1]['public_payload']['occurred_start'] > items[0]['public_payload']['occurred_start'],
                    '-rrf_score_12dp did not outrank -typed_source_time: the newer lower-scored candidate moved first')
            checks.append('-rrf_score_12dp outranks -typed_source_time: the strictly newer lower-scored '
                          'candidate still comes second (matched_lane_count is not publicly invertible, see module docstring)')

        elif key == 'tie-newer-source-time':
            row = tie_row(fixture, 'tie-newer-source-time')
            require(row['expected_order'] == ['time-newer', 'time-older'],
                    'sealed row does not order the newer typed_source_time first')
            for variant in variants.values():
                order = check_variant(fixture, spec, variant, shared)
                times = variant['variant']
                require(times['episode_source_time'] != times['short_source_time'],
                        'the tested field was not the only difference')
                newer_kind = ('cognitive_memory' if times['episode_source_time'] > times['short_source_time']
                              else 'short_horizon')
                require(order[0] == newer_kind,
                        'the newer typed_source_time was not ordered first')
            checks.append('both directions over the same frozen pair of times: the newer source time is always first')
            inverted = variants['short-newer']['recall']['execution']['result']['items']
            require([item['selected_item']['source_kind'] for item in inverted] == ['short_horizon', 'cognitive_memory'],
                    'the short-newer direction did not invert the source_kind order')
            checks.append('-typed_source_time outranks source_kind: the newer short candidate precedes the cognitive one')

        else:
            sealed = fixture['budget_oracle']['greedy_case']
            require(sealed['expected_selected'] == [sealed['ordered_item_ids'][1]] and sealed['truncated'] is True
                    and sealed['item_bytes'][0] > sealed['max_bytes'] == sealed['item_bytes'][1],
                    'sealed greedy relation differs from the one this oracle proves')
            probe = variants['unbudgeted']
            order = check_variant(fixture, spec, probe, shared)
            probe_items = probe['recall']['execution']['result']['items']
            first, second = probe_items[0], probe_items[1]
            require(order == ['cognitive_memory', 'short_horizon'],
                    'the oversize candidate was not ranked first')
            oversize, smaller = envelope_bytes([first]), envelope_bytes([second])
            checks.append('unbudgeted probe: both candidates selected in rank order, larger one first')
            budgeted = variants['budgeted']
            budget = budgeted['budget']
            require(budget == {**spec['budget_base'], 'max_bytes': smaller},
                    'budget was not the exact canonical size of the smaller later candidate')
            require(oversize > budget['max_bytes'] == smaller,
                    'the frozen greedy relation (first over the limit, second exactly at it) was not constructed')
            check_variant(fixture, spec, budgeted, shared, expect_two=False)
            result = budgeted['recall']['execution']['result']
            require(len(result['items']) == 1
                    and result['items'][0]['selected_item']['source_kind'] == 'short_horizon'
                    and result['items'][0]['public_payload'] == second['public_payload'],
                    'the smaller later candidate was not selected on its own')
            require(result['truncated'] is True, 'the skipped oversize candidate did not raise truncated')
            require(budgeted['recall']['plan']['budget']['max_bytes'] == smaller
                    and budgeted['recall']['plan']['budget']['max_items'] == spec['budget_base']['max_items'],
                    'the public plan did not carry the exact byte limit under an unconstrained item limit')
            checks.append('oversize first item skipped, later smaller item still selected, truncated raised')

        return dict(status='PASS', reason='original selection obligation verified through public operations',
                    business_assertions=checks, executor_version=VERSION)
    except (ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        return dict(status='FAIL', reason=str(exc), business_assertions=checks, executor_version=VERSION)
