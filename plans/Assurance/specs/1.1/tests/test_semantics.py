from pathlib import Path
import sys, json, unittest, itertools
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reference.semantics import *

from review_test_fixture import fixture_review_can_accept

class WireTests(unittest.TestCase):
    def test_duplicate_json_keys(self):
        with self.assertRaises(ContractError): strict_json('{"x":1,"x":2}')
    def test_bad_utf8(self):
        with self.assertRaises(ContractError): strict_json(b'\xff')
    def test_surrogate(self):
        with self.assertRaises(ContractError): strict_json('"\\ud800"')
    def test_nan_inf_overflow(self):
        for x in ['NaN','Infinity','-Infinity','1e9999']:
            with self.subTest(x=x),self.assertRaises(ContractError): strict_json(x)
    def test_depth(self):
        with self.assertRaises(ContractError): strict_json('[[[[0]]]]',max_depth=2)
    def test_bytes_limit(self):
        with self.assertRaises(ContractError): strict_json('"中文"',max_bytes=5)
    def test_large_int(self):
        with self.assertRaises(ContractError): strict_json('9007199254740992')
    def test_bool_not_integer(self):
        with self.assertRaises(ContractError): integer(True)
    def test_unknown_fields(self):
        with self.assertRaises(ContractError): exact_fields({'a':1,'official':True},{'a'})
    def test_null_not_missing(self):
        self.assertEqual(exact_fields({'a':None},{'a'}),{'a':None})
        with self.assertRaises(ContractError): exact_fields({},{'a'})
    def test_duplicate_ids(self):
        with self.assertRaises(ContractError): unique_strings(['x','x'])
    def test_canonical_key_order(self):
        self.assertEqual(digest({'a':1,'b':2}),digest({'b':2,'a':1}))
    def test_array_order_matters(self):
        self.assertNotEqual(digest([1,2]),digest([2,1]))

class FormulaTests(unittest.TestCase):
    cat=frozenset({'a','b','privacy'})
    def test_no_empty(self):
        for op in ('all','any'):
            with self.subTest(op=op),self.assertRaises(ContractError):parse_expr({op:[]},self.cat)
    def test_unknown_criterion(self):
        with self.assertRaises(ContractError):parse_expr({'criterion':'missing'},self.cat)
    def test_no_extra(self):
        with self.assertRaises(ContractError):parse_expr({'criterion':'a','true':True},self.cat)
    def test_node_limit(self):
        with self.assertRaises(ContractError):parse_expr({'all':[{'criterion':'a'}]*4},self.cat,node_limit=3)
    def test_depth_limit(self):
        with self.assertRaises(ContractError):parse_expr({'all':[{'all':[{'criterion':'a'}]}]},self.cat,depth_limit=1)
    def test_all_truth_table(self):
        e=parse_expr({'all':[{'criterion':'a'},{'criterion':'b'}]},self.cat)
        for a,b in itertools.product(Grade,repeat=2):
            expected=Grade.FAIL if Grade.FAIL in (a,b) else Grade.PASS if a==b==Grade.PASS else Grade.UNKNOWN
            self.assertEqual(evaluate_expr(e,{'a':a,'b':b})[0],expected)
    def test_any_truth_table(self):
        e=parse_expr({'any':[{'criterion':'a'},{'criterion':'b'}]},self.cat)
        for a,b in itertools.product(Grade,repeat=2):
            expected=Grade.PASS if Grade.PASS in (a,b) else Grade.FAIL if a==b==Grade.FAIL else Grade.UNKNOWN
            self.assertEqual(evaluate_expr(e,{'a':a,'b':b})[0],expected)
    def test_mandatory_cannot_be_bypassed(self):
        e=parse_expr({'any':[{'criterion':'a'},{'criterion':'b'}]},self.cat)
        for a,b,p in itertools.product(Grade,repeat=3):
            g,w=evaluate_success(e,{'a':a,'b':b,'privacy':p},('privacy',))
            if p is not Grade.PASS:self.assertIsNot(g,Grade.PASS)
    def test_absent_is_unknown(self):
        self.assertEqual(evaluate_expr(parse_expr({'criterion':'a'},self.cat),{})[0],Grade.UNKNOWN)
    def test_any_witness_stable(self):
        e=parse_expr({'any':[{'criterion':'b'},{'criterion':'a'}]},self.cat)
        self.assertEqual(evaluate_expr(e,{'a':Grade.PASS,'b':Grade.PASS})[1],frozenset({'b'}))

class ReviewTests(unittest.TestCase):
    def reply(self):
        return {'schema_version':2,'verdict':'ACCEPT','assessments':[
            {'criterion_id':'a','verdict':'PASS','evidence_ids':['e1'],'reason':'matched scope','limitations':[]}], 'findings':[]}
    def parse(self,obj):return parse_review_reply(json.dumps(obj),catalog=frozenset({'a'}),visible_evidence=frozenset({'e1'}))
    def test_valid(self):self.assertEqual(self.parse(self.reply())['verdict'],'ACCEPT')
    def test_official_forbidden(self):
        d=self.reply();d['official']=True
        with self.assertRaises(ContractError):self.parse(d)
    def test_missing_criterion(self):
        d=self.reply();d['assessments']=[]
        with self.assertRaises(ContractError):self.parse(d)
    def test_duplicate_criterion(self):
        d=self.reply();d['assessments']*=2
        with self.assertRaises(ContractError):self.parse(d)
    def test_unexposed(self):
        d=self.reply();d['assessments'][0]['evidence_ids']=['real_but_not_exposed']
        with self.assertRaises(ContractError):self.parse(d)
    def test_reply_bool_version(self):
        d=self.reply();d['schema_version']=True
        with self.assertRaises(ContractError):self.parse(d)
    def test_foreign_finding(self):
        d=self.reply();d['findings']=[{'criterion_id':'foreign','severity':'BLOCKER','reason':'x'}]
        with self.assertRaises(ContractError):self.parse(d)
    def test_blocker_in_success_path(self):
        d=self.reply();d['findings']=[{'criterion_id':'a','severity':'BLOCKER','reason':'x'}]
        d=self.parse(d);e=parse_expr({'criterion':'a'},frozenset({'a'}))
        self.assertFalse(fixture_review_can_accept(d,e,(),{'a':True}))
    def test_unrun_check_no_pass(self):
        d=self.parse(self.reply());e=parse_expr({'criterion':'a'},frozenset({'a'}))
        self.assertFalse(fixture_review_can_accept(d,e,(),{}))
    def test_unselected_any_failure(self):
        d={'verdict':'ACCEPT','assessments':[{'criterion_id':'a','verdict':'FAIL'},{'criterion_id':'b','verdict':'PASS'}], 'findings':[{'criterion_id':'a','severity':'BLOCKER'}]}
        e=parse_expr({'any':[{'criterion':'a'},{'criterion':'b'}]},frozenset({'a','b'}))
        self.assertTrue(fixture_review_can_accept(d,e,(),{'b':True}))

class EvidenceTests(unittest.TestCase):
    def test_four_truths(self):
        for p,n,t in [(False,False,Truth.UNKNOWN),(True,False,Truth.TRUE),(False,True,Truth.FALSE),(True,True,Truth.CONFLICT)]:
            a=frozenset(([('x',True)]if p else [])+([('x',False)]if n else []))
            self.assertEqual(derive(a,()).truth('x'),t)
    def test_cycle_no_anchor(self):
        rs=(Rule('1',(('a',True),),('b',True)),Rule('2',(('b',True),),('a',True)))
        self.assertFalse(derive(frozenset(),rs).usable('a'))
    def test_partial_self_support_not_anchor(self):
        self.assertEqual(derive(frozenset({('e',True)}),(Rule('r',(('a',True),('e',True)),('a',True)),)).truth('a'),Truth.UNKNOWN)
    def test_anchor_removal(self):
        rs=(Rule('1',(('a',True),),('b',True)),Rule('2',(('b',True),),('a',True)))
        self.assertTrue(derive(frozenset({('a',True)}),rs).usable('b'))
        self.assertFalse(derive(frozenset(),rs).usable('b'))
    def test_alternate_support(self):
        rs=(Rule('1',(('a',True),('b',True)),('k',True)),Rule('2',(('c',True),),('k',True)))
        self.assertTrue(derive(frozenset({('c',True)}),rs).usable('k'))
    def test_conflict_not_explosion(self):
        c=derive(frozenset({('a',True),('a',False)}),())
        self.assertFalse(c.usable('a'));self.assertEqual(c.truth('unrelated'),Truth.UNKNOWN)
    def test_conflict_path_not_clean(self):
        c=derive(frozenset({('a',True),('a',False)}),(Rule('r',(('a',True),),('k',True)),))
        self.assertEqual(c.truth('k'),Truth.TRUE);self.assertFalse(c.usable('k'))
    def test_clean_alternative(self):
        c=derive(frozenset({('a',True),('a',False),('e',True)}),(Rule('r',(('a',True),),('k',True)),Rule('s',(('e',True),),('k',True))))
        self.assertTrue(c.usable('k'))
    def test_conclusion_conflict(self):
        c=derive(frozenset({('e',True),('k',False)}),(Rule('s',(('e',True),),('k',True)),))
        self.assertFalse(c.usable('k'))
    def test_empty_rule_rejected(self):
        with self.assertRaises(ContractError):Rule('r',(),('k',True))
    def test_bound_not_partial_success(self):
        with self.assertRaises(IncompleteEvidence):derive(frozenset({('a',True),('b',True)}),(),max_literals=1)
    def test_rule_duplicate_rejected(self):
        r=Rule('r',(('a',True),),('k',True))
        with self.assertRaises(ContractError):derive(frozenset(),(r,r))
    def test_order_independent(self):
        rs=(Rule('1',(('a',True),),('b',True)),Rule('2',(('b',True),),('c',True)))
        self.assertEqual(derive(frozenset({('a',True)}),rs),derive(frozenset({('a',True)}),rs[::-1]))

class ProofTests(unittest.TestCase):
    def c(self,**kw):
        d=dict(mission_id='m',consumer_id='c',purpose='ACCEPT',scope_id='s',issued_at_ms=100,not_after_ms=200,
               object_versions=(('e1','h1'),),set_digests=(('facts','set1'),),complete=True,usable=True)
        d.update(kw);return ReadCertificate(**d)
    def check(self,c,**kw):
        d=dict(context=('m','c','ACCEPT','s'),now_ms=150,objects={'e1':'h1'},sets={'facts':'set1'});d.update(kw);return c.verify(**d)
    def test_valid(self):self.check(self.c())
    def test_new_counterevidence(self):
        with self.assertRaises(StaleProof):self.check(self.c(),sets={'facts':'set2'})
    def test_changed_object(self):
        with self.assertRaises(StaleProof):self.check(self.c(),objects={'e1':'h2'})
    def test_context_axes(self):
        for i in range(4):
            ctx=list(('m','c','ACCEPT','s'));ctx[i]='other'
            with self.subTest(i=i),self.assertRaises(StaleProof):self.check(self.c(),context=tuple(ctx))
    def test_expiry_exclusive(self):
        with self.assertRaises(StaleProof):self.check(self.c(),now_ms=200)
    def test_clock_backwards(self):
        with self.assertRaises(StaleProof):self.check(self.c(),now_ms=99)
    def test_incomplete(self):
        with self.assertRaises(IncompleteEvidence):self.check(self.c(complete=False))
    def test_missing_object(self):
        with self.assertRaises(StaleProof):self.check(self.c(),objects={})
    def test_missing_set(self):
        with self.assertRaises(StaleProof):self.check(self.c(),sets={})
    def test_not_usable(self):
        with self.assertRaises(StaleProof):self.check(self.c(usable=False))
    def test_duplicate_read_pin(self):
        with self.assertRaises(ContractError):self.check(self.c(set_digests=(('facts','set1'),('facts','set1'))))
    def test_unrelated_objects_do_not_invalidate(self):self.check(self.c(),objects={'e1':'h1','extra':'new'})

class EffectTests(unittest.TestCase):
    def p(self,key='send',op='op1',binding='b1',**kw):
        d=dict(effect_key=key,spec_hash='spec',outcome_binding_id=binding,operation_id=op,criterion_ids=frozenset({'d'}),milestone='delivered',milestone_policy_hash='policy',chain_checked=True,current=True);d.update(kw);return EffectProof(**d)
    def test_no_intent_does_not_satisfy(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'spec',()))
    def test_exact(self):self.assertTrue(effects_ready({'send':('delivered','policy')},'spec',(self.p(),)))
    def test_wrong_milestone(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'spec',(self.p(milestone='received'),)))
    def test_old_spec(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'new-spec',(self.p(),)))
    def test_wrong_policy(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'spec',(self.p(milestone_policy_hash='old'),)))
    def test_not_current(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'spec',(self.p(current=False),)))
    def test_no_chain(self):self.assertFalse(effects_ready({'send':('delivered','policy')},'spec',(self.p(chain_checked=False),)))
    def test_multiple_slots(self):self.assertFalse(effects_ready({'send':('delivered','policy'),'other':('delivered','policy')},'spec',(self.p(),)))
    def test_one_action_cannot_fill_two_slots(self):
        with self.assertRaises(ContractError):effects_ready({'send':('delivered','policy'),'other':('delivered','policy')},'spec',(self.p(),self.p('other',binding='b2')))
    def test_distinct_effects(self):self.assertTrue(effects_ready({'send':('delivered','policy'),'other':('delivered','policy')},'spec',(self.p(),self.p('other','op2','b2'))))
    def test_content_only_normalized_empty(self):self.assertTrue(effects_ready({},'spec',()))  # caller MUST have an approved CONTENT_ONLY Spec.

if __name__=='__main__':unittest.main()
