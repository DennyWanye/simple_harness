import unittest,math,random,copy,json
from reference.runtime_rules import *
class ContextRules(unittest.TestCase):
    def b(self,n=100,prior=0):return Budget(n,None,n,20,10,0,0,20,30,prior)
    def anchor(self,seq=9,tokens=5):return Group('anchor','current',seq,tokens,'USER_ANCHOR',True)
    def test_prior_is_subtracted_once(self):self.assertEqual(self.b(100,20).capacity(),70)
    def test_wire_plus_prior_separate_input(self):
        self.assertEqual(Budget(1000,None,100,20,10,0,0,0,0,20,'WIRE_PLUS_PRIOR').capacity(),80)
        self.assertEqual(Budget(1000,None,100,20,10,0,0,0,0,20,'WIRE_ONLY').capacity(),100)
    def test_output_above_model_refuses(self):
        with self.assertRaises(RuleError):Budget(100,None,100,1,2,0,0,0,0).capacity()
    def test_no_skipping_big_group(self):
        g=(Group('old','old',1,1,'HISTORY_MESSAGE'),Group('big','old',2,1000,'HISTORY_MESSAGE'),self.anchor())
        r=allocate(self.b(),0,g,(),turns=(Turn('old',True,frozenset({'old','big'})),),current_turn_id='current')
        self.assertEqual([x.id for x in r.recent],['anchor']);self.assertEqual(r.recent_complete_turns,0)
    def test_long_current_anchor_survives_middle_eviction(self):
        g=(self.anchor(1),Group('big','current',2,1000,'CLOSED_TOOL'),Group('tail','current',3,5,'OPEN_TAIL',True,False))
        r=allocate(self.b(),0,g,(),turns=(),current_turn_id='current');self.assertEqual([x.id for x in r.recent],['anchor','tail'])
    def test_partial_old_turn_not_counted(self):
        g=(Group('first','old',1,500),Group('second','old',2,10),self.anchor())
        r=allocate(self.b(),0,g,(),turns=(Turn('old',True,frozenset({'first','second'})),),current_turn_id='current')
        self.assertIn('second',[x.id for x in r.recent]);self.assertEqual(r.recent_complete_turns,0)
    def test_complete_turn_once_and_current_never(self):
        g=(Group('first','old',1,5),Group('second','old',2,5),self.anchor())
        turns=(Turn('old',True,frozenset({'first','second'})),Turn('current',True,frozenset({'anchor'})))
        r=allocate(self.b(),0,g,(),turns=turns,current_turn_id='current');self.assertEqual(r.complete_turn_ids,('old',))
    def test_overlapping_recall_removed(self):
        g=(Group('old','old',1,20),self.anchor(tokens=10));rs=(Recall('r',frozenset({'old'}),10),)
        r=allocate(self.b(),0,g,rs,turns=(),current_turn_id='current');self.assertEqual(r.recalled,())
    def test_tool_call_pairs(self):
        with self.assertRaises(RuleError):validate_groups((Group('x','t',1,10,call_ids=('a',),result_call_ids=('b',)),))
    def test_required_overflow(self):
        with self.assertRaisesRegex(RuleError,'REQUIRED'):allocate(self.b(),0,(self.anchor(tokens=200),),(),turns=(),current_turn_id='current')
    def test_final_wire_guard(self):
        r=allocate(self.b(),0,(self.anchor(),),(),turns=(),current_turn_id='current')
        with self.assertRaises(RuleError):verify_final_count(r,91,10,20)
    def test_random_budget_invariants(self):
        rng=random.Random(23)
        for i in range(100):
            g=tuple(Group(str(j),'old',j+1,rng.randint(1,40))for j in range(12))+(self.anchor(20),)
            r=allocate(self.b(250),10,g,(),turns=(),current_turn_id='current')
            self.assertLessEqual(r.used,r.input_budget);ids=[x.id for x in r.recent if not x.mandatory]
            self.assertEqual(ids,[x.id for x in g[:-1]][12-len(ids):])
class NativeRules(unittest.TestCase):
    def test_huge_vector_not_zero(self):
        v=validate_vector([1e308,1e308],2);self.assertTrue(all(math.isfinite(x)for x in v));self.assertGreater(sum(x*x for x in v),0.99)
    def test_small_vector_not_underflow(self):self.assertGreater(sum(x*x for x in validate_vector([1e-308,1e-308],2)),0.99)
    def test_bad_vector_inputs(self):
        for x in ([0,0],[True,1],[float('nan'),1],[float('inf'),1]):
            with self.subTest(x=x),self.assertRaises(RuleError):validate_vector(x,2)
    def test_utf8_pages_no_loss(self):
        text='早期记录😊end';out='';start=0
        while start<len(text.encode()):
            t,start,done=utf8_slice(text,start,4);out+=t
        self.assertEqual(out,text);self.assertTrue(done)
    def test_utf8_invalid_offset(self):
        with self.assertRaises(RuleError):utf8_slice('早期',1,4)
    def test_rrf_tie_stable(self):self.assertEqual(rrf({'exact':[('b',1),('a',1)]},stable_keys={x:(x,'a'*64,0,1,x)for x in('a','b')})[0][0],'a')
    def cursor(self):
        a={'owner':'o','session':'s','control_generation':1,'purpose':'MODEL_SEARCH','query_hash':'q','request_hash':'r','expires_at_ms':100,'index_generation':1,'authority_hash':'h','root_incarnation':'root'};return a,dict(a,now_ms=1,append_commit=500)
    def test_append_keeps_snapshot_cursor(self):a,b=self.cursor();check_cursor(a,b)
    def test_cursor_owner_generation_expiry(self):
        for key,val in [('owner','other'),('index_generation',2),('now_ms',100),('authority_hash','new')]:
            a,b=self.cursor();b[key]=val
            with self.subTest(key=key),self.assertRaises(RuleError):check_cursor(a,b)
    def test_frozen_settings_kept(self):self.assertEqual(disposition('PREPARED',settings_changed=True),'KEEP_FROZEN')
    def test_unsent_requires_no_send_proof(self):self.assertEqual(disposition('RESERVED',safety_changed=True),'REQUEST_UNSENT_TERMINATION_REQUIRED')
    def test_unknown_never_replaced(self):self.assertEqual(disposition('UNKNOWN',safety_changed=True,no_send_final=True),'RECONCILE_ORIGINAL')
    def test_activation_truth_table(self):
        self.assertEqual(embedding_mode(True,True,creating=True,available=False),'REJECT_CREATION')
        self.assertEqual(embedding_mode(True,True,creating=False,available=False),'LEXICAL_ONLY')
        self.assertEqual(embedding_mode(True,False,creating=False,available=False),'UNAVAILABLE')
        self.assertEqual(embedding_mode(False,True,creating=True,available=False),'LEXICAL_ONLY')
        with self.assertRaises(RuleError):embedding_mode(False,False,creating=True,available=True)
    def test_permissions_intersection(self):self.assertEqual(effective_permissions(frozenset({'read','write'}),frozenset({'read'})),frozenset({'read'}))
    def test_provider_priority_actual_field(self):
        flags=dict(registered=True,configured=True,healthy=True,authorized=True,compatible=True,features=['x'])
        self.assertEqual(choose_provider([dict(flags,id='low',priority=1),dict(flags,id='high',priority=9)],frozenset({'x'})),'high')
    def test_effect_not_direct_tool(self):self.assertEqual(tool_route('EXTERNAL_EFFECT',True,True),'ORIGINAL_OPERATION_INTENT')
    def test_skill_paths(self):
        for x in ('../x','/x','a\\b','NUL.txt','a/','c:/x'):
            with self.subTest(x=x),self.assertRaises(RuleError):safe_skill_path(x)
        with self.assertRaises(RuleError):validate_bundle_paths(['A.md','a.md'])
    def test_dependency_cycle(self):
        with self.assertRaisesRegex(RuleError,'CYCLE'):dependency_lock({'a':(1,'x'),'b':(1,'y')},(('a','b'),('b','a')))
    def test_full_disposal_not_optional(self):
        cs=[{'kind':k,'complete':True,'count':0,'refs':[],'set_hash':digest([])}for k in ('TURNS','CALLS','UNKNOWN','PENDING_IMPORTS','INDEX_WRITERS','READ_HANDLES','TEMP_ROOTS')]
        self.assertTrue(complete_disposal(cs))
        with self.assertRaises(RuleError):complete_disposal(cs[:-1])
        cs[2].update(count=1,refs=['actualunknown'],set_hash=digest(['actualunknown']));self.assertFalse(complete_disposal(cs))
    def test_strict_json(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}'):
            with self.assertRaises(RuleError):parse_strict(raw)
    def test_trigger_statement_boundary(self):
        ddl="CREATE TABLE t(v); CREATE TRIGGER tt AFTER INSERT ON t BEGIN SELECT 'a;b'; SELECT 1; END;"
        self.assertEqual(len(list(sql_statements(ddl))),2)
