"""Counterexamples from F01–F15. Pure/SQL reference scope, NOT a local SDK execution."""
import copy,itertools,json,shutil,sqlite3,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools'),str(ROOT/'tests')]
from reference.protocol_v11 import (CheckResult,CriterionCheckPolicy,decide_review,evaluate_check_gate,
    evidence_label,build_catalogue,merge_catalogues,resolve_evidence_ids,canonical_read_set,
    determine_lane,may_disclose_restored,validate_ref)
from reference.semantics import ContractError,Grade,parse_expr,parse_review_reply,strict_json
from schema_support import validate
from test_sql import prepare,review,H


def ref(kind='artifact',revision=1,body='a',id='same'):
    return {'kind':kind,'pin':{'id':id,'revision':revision,'content_hash':body*64}}

class TypedCheckTests(unittest.TestCase):
    def data(self,grades):
        return {'verdict':'ACCEPT','assessments':[{'criterion_id':k,'verdict':v.value} for k,v in grades.items()],'findings':[]}
    def test_semantic_has_no_check_receipt(self):
        p={'a':CriterionCheckPolicy('a','SEMANTIC',())};e=parse_expr({'criterion':'a'},frozenset(p))
        d=decide_review(self.data({'a':Grade.PASS}),e,(),p,{})
        self.assertTrue(d.acceptable);self.assertEqual(d.consumed_receipts,())
        self.assertIsNone(evaluate_check_gate(p['a'],{}).grade)
    def test_all_nine_model_check_combinations(self):
        p={'a':CriterionCheckPolicy('a','CHECKED',(('check-a',),))};e=parse_expr({'criterion':'a'},frozenset(p))
        for model,check in itertools.product(Grade,repeat=2):
            with self.subTest(model=model,check=check):
                r={'check-a':CheckResult('check-a','SUCCEEDED',check,'rc',True)}
                d=decide_review(self.data({'a':model}),e,(),p,r)
                expected=Grade.FAIL if Grade.FAIL in (model,check) else Grade.PASS if model==check==Grade.PASS else Grade.UNKNOWN
                self.assertEqual(d.effective_grades['a'],expected)
                self.assertEqual(d.acceptable,expected is Grade.PASS)
    def test_error_missing_cancelled_not_run_all_unknown(self):
        p=CriterionCheckPolicy('a','CHECKED',(('c',),))
        for state in ['ERROR','CANCELLED','NOT_RUN','RUNNING']:
            with self.subTest(state=state):self.assertEqual(evaluate_check_gate(p,{'c':CheckResult('c',state,Grade.PASS,'rc',True)}).grade,Grade.UNKNOWN)
        self.assertEqual(evaluate_check_gate(p,{}).grade,Grade.UNKNOWN)
    def test_error_without_assertions_does_not_require_fabrication(self):
        from reference.protocol_v11 import normalize_check_result
        r=normalize_check_result('c','a',{'execution_state':'ERROR','assertions':[]},'real-shaped-receipt-fixture',True)
        self.assertEqual(r.effective(),Grade.UNKNOWN)
    def test_missing_or_duplicate_assertion(self):
        from reference.protocol_v11 import normalize_check_result
        r=normalize_check_result('c','wanted',{'execution_state':'SUCCEEDED','assertions':[]},'rc',True)
        self.assertEqual(r.effective(),Grade.UNKNOWN)
        with self.assertRaises(ContractError):normalize_check_result('c','a',{'execution_state':'SUCCEEDED','assertions':[{'assertion_key':'a','verdict':'PASS'},{'assertion_key':'a','verdict':'FAIL'}]},'rc',True)
    def test_actual_fail_not_unknown(self):
        p=CriterionCheckPolicy('a','CHECKED',(('c',),))
        self.assertEqual(evaluate_check_gate(p,{'c':CheckResult('c','SUCCEEDED',Grade.FAIL,'rc',True)}).grade,Grade.FAIL)
    def test_second_check_group_can_succeed(self):
        p=CriterionCheckPolicy('a','CHECKED',(('c1','c2'),('c3',)))
        r={'c1':CheckResult('c1','SUCCEEDED',Grade.FAIL,'bad',True),'c3':CheckResult('c3','SUCCEEDED',Grade.PASS,'good',True)}
        d=evaluate_check_gate(p,r);self.assertEqual(d.grade,Grade.PASS);self.assertEqual(d.consumed_receipts,('good',))
    def test_failed_group_and_unknown_group_unknown(self):
        p=CriterionCheckPolicy('a','CHECKED',(('c1',),('c2',)))
        self.assertEqual(evaluate_check_gate(p,{'c1':CheckResult('c1','SUCCEEDED',Grade.FAIL,'bad',True)}).grade,Grade.UNKNOWN)
    def test_plain_boolean_not_a_real_check_result(self):
        with self.assertRaises(ContractError):evaluate_check_gate(CriterionCheckPolicy('a','CHECKED',(('c',),)),{'c':True})
    def test_wrong_check_identity(self):
        with self.assertRaises(ContractError):evaluate_check_gate(CriterionCheckPolicy('a','CHECKED',(('c',),)),{'c':CheckResult('other','SUCCEEDED',Grade.PASS,'rc',True)})
    def test_unknown_policy_not_semantic(self):
        with self.assertRaises(ContractError):CriterionCheckPolicy('a','GUESSED',())
        with self.assertRaises(ContractError):CriterionCheckPolicy('a','CHECKED',())
        with self.assertRaises(ContractError):CriterionCheckPolicy('a','SEMANTIC',(('c',),))
    def test_stale_check_does_not_pass(self):
        d=evaluate_check_gate(CriterionCheckPolicy('a','CHECKED',(('c',),)),{'c':CheckResult('c','SUCCEEDED',Grade.PASS,'rc',False)})
        self.assertEqual(d.grade,Grade.UNKNOWN)

class CatalogueTests(unittest.TestCase):
    def test_same_id_kind_version_and_bytes_are_distinct(self):
        refs=[ref(),ref(revision=2),ref(kind='source'),ref(body='b')]
        self.assertEqual(len(set(evidence_label('r',x) for x in refs)),4)
    def test_review_scope_changes_label(self):self.assertNotEqual(evidence_label('r',ref()),evidence_label('r2',ref()))
    def test_reorder_and_replay_are_stable(self):
        a=build_catalogue('r',[ref(),ref(revision=2)]);b=build_catalogue('r',[ref(revision=2),ref(),ref()])
        self.assertEqual(a,b);self.assertEqual(merge_catalogues('r',[a,b]),a)
    def test_initial_catalogue_not_disclosure(self):
        c=build_catalogue('r',[ref()]);label=c[0]['label']
        with self.assertRaises(ContractError):resolve_evidence_ids('r',[label],c,frozenset())
    def test_disclosed_old_version_not_new(self):
        c=build_catalogue('r',[ref(),ref(revision=2)]);old=evidence_label('r',ref());new=evidence_label('r',ref(revision=2))
        self.assertEqual(resolve_evidence_ids('r',[old],c,frozenset([old])),(ref(),))
        with self.assertRaises(ContractError):resolve_evidence_ids('r',[new],c,frozenset([old]))
    def test_conflicting_append_rejected(self):
        c=list(build_catalogue('r',[ref()]));bad=copy.deepcopy(c);bad[0]['ref']=ref(body='c')
        with self.assertRaises(ContractError):merge_catalogues('r',[c,bad])
    def test_cold_raw_labels_restore_exact_ref(self):
        c=build_catalogue('r',[ref(),ref(revision=2)]);raw=json.dumps([x['label'] for x in c])
        stored=json.loads(json.dumps(c));self.assertEqual(resolve_evidence_ids('r',json.loads(raw),stored,frozenset(json.loads(raw))),tuple(x['ref'] for x in c))
    def test_unknown_ref_kind_rejected(self):
        with self.assertRaises(ContractError):evidence_label('r',ref(kind='some_guessed_execution'))
    def test_boolean_revision_rejected(self):
        with self.assertRaises(ContractError):validate_ref(ref(revision=True))

class ReadsetRestoreTests(unittest.TestCase):
    def row(self,channel='OBJECT',key='k',h='a'):return dict(channel=channel,key=key,fingerprint=h*64,coverage='COMPLETE')
    def test_duplicate_logical_key_rejected_even_different_hash(self):
        for second in [self.row(),self.row(h='b')]:
            with self.subTest(second=second),self.assertRaises(ContractError):canonical_read_set([self.row(),second])
    def test_complete_read_order_is_canonical(self):self.assertEqual(canonical_read_set([self.row(key='z'),self.row(key='a')]),canonical_read_set([self.row(key='a'),self.row(key='z')]))
    def test_incomplete_not_empty_complete(self):
        r=self.row();r['coverage']='INCOMPLETE'
        with self.assertRaises(ContractError):canonical_read_set([r])
    def test_missing_lane_never_legacy(self):
        with self.assertRaises(ContractError):determine_lane(None,False)
    def test_new_lane_missing_binding_rejects(self):
        with self.assertRaises(ContractError):determine_lane('ASSURANCE_1_1',False)
        self.assertEqual(determine_lane('COMPLETION_V1',False),'COMPLETION_V1')
    def grant(self):return {'root_incarnation_id':'new','mode':'READ_ONLY_REAUTHORIZED','not_before_ms':10,'expires_at_ms':20,'authorized_exact_refs':[ref()]}
    def test_t0_grant_cannot_authorize_t2_root(self):
        g=self.grant();g['root_incarnation_id']='backup-root'
        self.assertFalse(may_disclose_restored(root_incarnation='new',grant=g,ref=ref(),now_ms=15,current_authenticated=True))
    def test_without_current_authority_no_disclosure(self):
        self.assertFalse(may_disclose_restored(root_incarnation='new',grant=None,ref=ref(),now_ms=15,current_authenticated=True))
        self.assertFalse(may_disclose_restored(root_incarnation='new',grant=self.grant(),ref=ref(),now_ms=15,current_authenticated=False))
    def test_current_confirmation_scoped_and_expiring(self):
        args=dict(root_incarnation='new',grant=self.grant(),now_ms=15,current_authenticated=True)
        self.assertTrue(may_disclose_restored(ref=ref(),**args))
        self.assertFalse(may_disclose_restored(ref=ref(revision=2),**args))
        args['now_ms']=20;self.assertFalse(may_disclose_restored(ref=ref(),**args))

class SqlBypassTests(unittest.TestCase):
    def setUp(self):self.c=prepare()
    def tearDown(self):self.c.close()
    def close(self,state='NOT_READY',v=1):self.c.execute('INSERT INTO assurance_closeouts VALUES (?,?,?,?,?,?,?,?)',('m1','rm1',state,v,H,'{}','rcm1',1))
    def final(self):self.close();self.c.execute("UPDATE assurance_closeouts SET state='READY',row_version=2");self.c.execute("UPDATE assurance_closeouts SET state='FINALIZED',row_version=3")
    def test_initial_finalized_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):self.close('FINALIZED')
    def test_initial_ready_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):self.close('READY')
    def test_delete_finalized_rejected(self):
        self.final()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM assurance_closeouts')
    def test_replace_finalized_rejected_without_recursive_triggers(self):
        self.final();self.c.execute('PRAGMA recursive_triggers=OFF')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("INSERT OR REPLACE INTO assurance_closeouts VALUES ('m1','rm1','NOT_READY',1,?,'{}','rcm1',1)",(H,))
        self.assertEqual(self.c.execute('SELECT state FROM assurance_closeouts').fetchone()[0],'FINALIZED')
    def pin(self,state='PREPARING',job='missing',m='m1'):
        self.c.execute('INSERT INTO assurance_blob_pins VALUES (?,?,?,?,?,?,?,?,?,?,?)',('pin',m,job,H,'{}',state,1,1,None,'rc'+m,'rc'+m))
    def test_insert_bound_without_review_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):self.pin('BOUND')
    def test_transition_bound_requires_real_same_mission_review(self):
        self.pin()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_blob_pins SET state='BOUND',row_version=2")
        review(self.c,m='m2',job='missing')
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_blob_pins SET state='BOUND',row_version=2")
    def test_correct_bound_then_release(self):
        self.pin(job='j');review(self.c,job='j');self.c.execute("UPDATE assurance_blob_pins SET state='BOUND',row_version=2")
        self.c.execute("UPDATE assurance_blob_pins SET state='RELEASED',released_at_ms=5,row_version=3")
        self.assertEqual(self.c.execute('SELECT state FROM assurance_blob_pins').fetchone()[0],'RELEASED')
    def test_second_invocation_retains_round_no_subject_collision(self):
        review(self.c);self.c.execute("INSERT INTO dispatch_intents VALUES ('i2-real','m1','PENDING')")
        self.c.execute("INSERT INTO assurance_review_invocations VALUES ('j1',2,'m1','i2-real',?,'{}','reserve-m1','rcm1')",(H,))
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_review_bindings').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_review_invocations').fetchone()[0],2)
    def test_format_repair_cannot_exist_without_initial(self):
        # review binding exists without invocation only inside a staged fixture.
        self.c.execute("INSERT INTO assurance_review_bindings VALUES ('j','m1','pm1','tm1','cmd',1,1,?,?,?,?,'{}','rcm1',1)",(H,H,H,H))
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("INSERT INTO assurance_review_invocations VALUES ('j',2,'m1','im1',?,'{}','reserve-m1','rcm1')",(H,))

class CursorPendingTests(unittest.TestCase):
    def setUp(self):self.c=prepare();self.c.execute("INSERT INTO assurance_event_cursors VALUES ('m1','REVIEW',0,1,0)")
    def tearDown(self):self.c.close()
    def event(self,id):self.c.execute('INSERT INTO events VALUES (?,?,?,?)',(id,'m1','SourceUpdated','{}'))
    def enqueue(self,key='r',e='e1',seq=1):self.c.execute("INSERT INTO assurance_pending_work VALUES ('m1','REVIEW',?,?,?,?, 'PENDING',1,0,0,NULL,NULL,NULL)",(key,e,seq,H))
    def test_ingress_cursor_and_pending_rollback_together(self):
        self.event('e1');self.c.execute('BEGIN');self.enqueue();self.c.execute("UPDATE assurance_event_cursors SET last_event_seq=1,row_version=2");self.c.rollback()
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_pending_work').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT last_event_seq FROM assurance_event_cursors').fetchone()[0],0)
    def test_budget_wait_does_not_block_later_event_ingress(self):
        self.event('e1');self.enqueue();self.c.execute("UPDATE assurance_pending_work SET state='RUNNING',owner='w',lease_until_ms=10,row_version=2,tries=1")
        self.c.execute("UPDATE assurance_pending_work SET state='WAITING',owner=NULL,lease_until_ms=NULL,wait_reason='BUDGET',not_before_ms=20,row_version=3")
        self.event('e2');self.c.execute('BEGIN');self.enqueue('other','e2',2);self.c.execute("UPDATE assurance_event_cursors SET last_event_seq=2,row_version=2");self.c.commit()
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_pending_work').fetchone()[0],2)
    def test_old_preparation_cannot_ack_newer_target(self):
        self.event('e1');self.enqueue();self.c.execute("UPDATE assurance_pending_work SET state='RUNNING',owner='w',lease_until_ms=10,row_version=2,tries=1")
        self.event('e2');self.c.execute("UPDATE assurance_pending_work SET target_epoch=2,trigger_event_id='e2',state='PENDING',owner=NULL,lease_until_ms=NULL,row_version=3")
        n=self.c.execute("UPDATE assurance_pending_work SET state='DONE',owner=NULL,lease_until_ms=NULL,row_version=3 WHERE row_version=2 AND target_epoch=1").rowcount
        self.assertEqual(n,0)
    def test_done_same_target_not_reopened(self):
        self.event('e1');self.enqueue();self.c.execute("UPDATE assurance_pending_work SET state='RUNNING',owner='w',lease_until_ms=10,row_version=2,tries=1")
        self.c.execute("UPDATE assurance_pending_work SET state='DONE',owner=NULL,lease_until_ms=NULL,row_version=3")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_pending_work SET state='PENDING',row_version=4")

class AssetConsistencyTests(unittest.TestCase):
    def test_all_current_assets_checked(self):
        from check_plan import check
        self.assertEqual(check(ROOT),[])
    def test_nested_kind_mutation_not_a_count_check(self):
        from check_plan import check
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'kit';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__'))
            p=target/'contracts/common.schema.json';obj=json.loads(p.read_text());obj['$defs']['ref']['properties']['kind']['enum'].append('invented_fact');p.write_text(json.dumps(obj))
            errors=check(target);self.assertTrue(any('DRIFT' in x for x in errors))
    def test_field_removed_changes_projection_mapping(self):
        from check_plan import check
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'kit';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__'))
            p=target/'contracts/review-binding-v2.schema.json';obj=json.loads(p.read_text());del obj['properties']['catalogue_hash'];obj['required'].remove('catalogue_hash');p.write_text(json.dumps(obj))
            self.assertTrue(any('DRIFT' in x for x in check(target)))
    def test_empty_authors_schema_rejected(self):
        f=json.loads((ROOT/'contracts/six-purpose-fixtures.json').read_text())[0]['review_binding'];f['producer_agent_ids']=[]
        p=ROOT/'contracts/review-binding-v2.schema.json'
        with self.assertRaises(ValueError):validate(ROOT,p,json.loads(p.read_text()),f)
    def test_limits_are_first_reached_not_truncation(self):
        with self.assertRaises(ValueError):strict_json('"'+'x'*100+'"',max_bytes=10)
        with self.assertRaises(ValueError):strict_json('{"x":[[[1]]]}',max_depth=1)

if __name__=='__main__':unittest.main()
