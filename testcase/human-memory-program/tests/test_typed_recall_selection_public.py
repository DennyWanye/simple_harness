"""Real public selection-lane executor plus independent oracle counterexamples; never 401 evidence."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def items_of(observations, variant):
    return observations['variants'][variant]['recall']['execution']['result']['items']


class SelectionPublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_selection_cells_pass_and_independent_negatives_fail(self):
        fixture = json.loads((ROOT / 'fixtures/typed-recall-v3.json').read_text())
        adapter = load(ROOT / 'adapters/typed_recall_selection_cases.py')
        oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        selection = load(ROOT / 'runners/typed_recall_selection_oracle.py')
        compiled = selection.inputs(fixture)
        # Inputs carry no outcome/order/byte gold.
        for spec in compiled.values():
            self.assertFalse({'expected', 'expected_order', 'outcome', 'truncated', 'expected_selected',
                              'max_bytes', 'item_bytes'} & set(spec))
            for variant in spec['variants']:
                self.assertFalse({'expected', 'expected_order', 'max_bytes'} & set(variant))
        cells = sorted(adapter.CELLS)
        with tempfile.TemporaryDirectory() as directory:
            rows = await adapter.run_cases({'cells': cells, 'selection': compiled,
                                            'version': selection.VERSION}, Path(directory))
        verdicts = {row['cell_id']: (row, oracle.assess_cell(fixture, row)) for row in rows}
        for name in cells:
            row, verdict = verdicts[name]
            self.assertEqual(row['status'], 'OBSERVED')
            self.assertEqual(verdict['status'], 'PASS', (name, verdict))
            self.assertGreaterEqual(len(verdict['business_assertions']), 2)

        kind = verdicts['selection-budget/tie-source-kind'][0]
        for attack in ('order', 'score', 'unequal_time', 'split_chunk', 'replay', 'filtered', 'input'):
            row = copy.deepcopy(kind)
            o = row['observations']
            variant = o['variants']['equal-source-time']
            if attack == 'order':
                variant['recall']['execution']['result']['items'].reverse()
            elif attack == 'score':
                items_of(o, 'equal-source-time')[1]['score'] = 0.001
            elif attack == 'unequal_time':
                variant['variant']['short_source_time'] += 60
            elif attack == 'split_chunk':
                variant['projection_build']['split_group_count'] = 1
            elif attack == 'replay':
                variant['replay']['replayed'] = False
            elif attack == 'filtered':
                variant['recall']['execution']['decision']['filtered_candidate_count'] = 1
            else:
                o['input']['query'] = 'other'
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)

        newer = verdicts['selection-budget/tie-newer-source-time'][0]
        for attack in ('invert_short_newer', 'invert_cognitive_newer', 'equal_times'):
            row = copy.deepcopy(newer)
            o = row['observations']
            if attack == 'invert_short_newer':
                # If the newer short candidate stopped preceding the cognitive one, source_kind
                # would be outranking -typed_source_time and the sealed precedence would be broken.
                o['variants']['short-newer']['recall']['execution']['result']['items'].reverse()
            elif attack == 'invert_cognitive_newer':
                o['variants']['cognitive-newer']['recall']['execution']['result']['items'].reverse()
            else:
                o['variants']['short-newer']['variant']['short_source_time'] = \
                    o['variants']['short-newer']['variant']['episode_source_time']
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)

        score = verdicts['selection-budget/tie-score'][0]
        for attack in ('order', 'equal_scores', 'lane_opened', 'equal_times', 'replay'):
            row = copy.deepcopy(score)
            o = row['observations']
            variant = o['variants']['low-newer']
            if attack == 'order':
                # If the newer lower-scored candidate moved first, -typed_source_time would be
                # outranking -rrf_score_12dp and the sealed precedence would be broken.
                variant['recall']['execution']['result']['items'].reverse()
            elif attack == 'equal_scores':
                items_of(o, 'low-newer')[1]['score'] = items_of(o, 'low-newer')[0]['score']
            elif attack == 'lane_opened':
                variant['recall']['plan']['retrieval_modes'] = ['full_text', 'vector']
            elif attack == 'equal_times':
                variant['variant']['low_source_time'] = variant['variant']['high_source_time']
            else:
                variant['replay']['replayed'] = False
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)

        greedy = verdicts['selection-budget/budget:greedy'][0]
        for attack in ('both_selected', 'not_truncated', 'budget_widened', 'oversize_selected', 'probe_order'):
            row = copy.deepcopy(greedy)
            o = row['observations']
            budgeted = o['variants']['budgeted']
            if attack == 'both_selected':
                budgeted['recall']['execution']['result']['items'] = copy.deepcopy(items_of(o, 'unbudgeted'))
            elif attack == 'not_truncated':
                budgeted['recall']['execution']['result']['truncated'] = False
            elif attack == 'budget_widened':
                budgeted['budget']['max_bytes'] += 1
            elif attack == 'oversize_selected':
                budgeted['recall']['execution']['result']['items'] = [copy.deepcopy(items_of(o, 'unbudgeted')[0])]
            else:
                o['variants']['unbudgeted']['recall']['execution']['result']['items'].reverse()
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)

    def test_inputs_are_derived_from_the_sealed_tie_break_rows(self):
        fixture = json.loads((ROOT / 'fixtures/typed-recall-v3.json').read_text())
        selection = load(ROOT / 'runners/typed_recall_selection_oracle.py')
        compiled = selection.inputs(fixture)
        rows = {row['id']: row for row in fixture['ranking_oracle']['tie_break_cases']}
        equal = {selection.seconds(c['typed_source_time']) for c in rows['tie-source-kind']['candidates']}
        self.assertEqual(len(equal), 1)
        variant = compiled['tie-source-kind']['variants'][0]
        self.assertEqual(variant['episode_source_time'], variant['short_source_time'])
        self.assertEqual(variant['episode_source_time'], equal.pop())
        times = {selection.seconds(c['typed_source_time']) for c in rows['tie-newer-source-time']['candidates']}
        for variant in compiled['tie-newer-source-time']['variants']:
            self.assertEqual({variant['episode_source_time'], variant['short_source_time']}, times)
        # tie-score is the two-episode construction: the sealed row's own two source times, run in
        # both directions, with the lower-scored title carrying the newer one first.
        score = rows['tie-score']
        score_times = {selection.seconds(c['typed_source_time']) for c in score['candidates']}
        compiled_score = compiled['tie-score']
        self.assertEqual(compiled_score['construction'], 'two-episode-lexical')
        self.assertGreater(compiled_score['high_title'].count(selection.QUERY_TOKEN),
                           compiled_score['low_title'].count(selection.QUERY_TOKEN))
        for variant in compiled_score['variants']:
            self.assertEqual({variant['high_source_time'], variant['low_source_time']}, score_times)
        first = compiled_score['variants'][0]
        self.assertGreater(first['low_source_time'], first['high_source_time'])
        # Every case is clocked strictly after the newest frozen source time.
        for spec in compiled.values():
            for variant in spec['variants']:
                times = [value for key, value in variant.items() if key.endswith('_source_time')]
                self.assertGreater(spec['now'], max(times))


if __name__ == '__main__':
    unittest.main()
