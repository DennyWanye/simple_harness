import copy,json,re,unittest
from pathlib import Path
from reference.runtime_rules import RuleError,canonical,dependency_lock
from reference.schema_codec import decode
from reference.search_scan import ScoredRow,freeze,advance
from tests.sql_fixture import execution,partition
ROOT=Path(__file__).resolve().parents[1]
class AdditionalBoundaries(unittest.TestCase):
    def test_early_history_hit_on_second_scan_page(self):
        rows=(ScoredRow(1,'older-1',()),ScoredRow(2,'older-2',(('words',4.0),)))
        s=advance(freeze(rows),rows,page_rows=1,top_k=8)
        self.assertEqual(s.phase,'SCANNING');self.assertEqual(s.results,())
        s=advance(s,rows,page_rows=1,top_k=8)
        self.assertEqual(s.phase,'RESULTS');self.assertEqual(s.results[0][0],'older-2')
    def test_final_ranking_not_provisional(self):
        rows=(ScoredRow(1,'weak',(('vector',0.36),)),ScoredRow(2,'strong',(('vector',0.99),)))
        first=advance(freeze(rows),rows,page_rows=1,top_k=1)
        self.assertEqual(first.results,())
        final=advance(first,rows,page_rows=1,top_k=1)
        self.assertEqual(final.results[0][0],'strong')
        self.assertEqual(advance(final,rows,page_rows=1,top_k=1),final)
    def test_rank_scan_rejects_changed_frozen_input(self):
        rows=(ScoredRow(1,'a',()),)
        with self.assertRaisesRegex(RuleError,'CURSOR_STALE'):
            advance(freeze(rows),rows+(ScoredRow(2,'b',()),),page_rows=1,top_k=8)
    def test_profile_disallows_no_embedding_no_lexical(self):
        x=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']['RuntimeProfile']
        x['context_policy']['embedding_required_for_activation']=False;x['allow_lexical_degradation']=False
        with self.assertRaises(RuleError):decode('RuntimeProfile',canonical(x))
    def test_policy_chunk_overlap_not_equal_to_chunk(self):
        x=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']['Policy']
        x['embedding_overlap_tokens']=x['embedding_chunk_tokens']
        with self.assertRaises(RuleError):decode('Policy',canonical(x))
    def test_dependency_node_bound(self):
        with self.assertRaises(RuleError):dependency_lock({str(i):(1,'x')for i in range(129)},())
    def test_named_sql_queries_compile_against_assigned_fixture(self):
        a=execution();b=partition()
        partition_names={'index_chunk_page','index_vector_page','words_page','cursor_exact','cursor_materialize','query_progress','query_finalize'}
        q=json.loads((ROOT/'sql/queries.json').read_text())
        try:
            for name,statement in q.items():
                with self.subTest(query=name):
                    values={key:None for key in re.findall(r':([A-Za-z_][A-Za-z_0-9]*)',statement)}
                    (b if name in partition_names else a).execute('EXPLAIN '+statement,values).fetchall()
        finally:a.close();b.close()
