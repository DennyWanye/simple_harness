import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from reference.runtime_rules import RuleError,canonical,digest,Budget,Group,Turn,rrf
from reference.schema_codec import decode,validate,SCHEMA
from reference.retrieval_status import status_for
from reference.search_scan import ScoredRow,freeze,advance
from reference.purge_rules import recovery_action,can_rebuild
from reference.context_recall import (build_query,ReferenceOriginalCallPort,ReferenceRecallCoordinator,ReferenceComposer,RecallRow)
from tests.sql_fixture import execution,drain,ROOT
E=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']

def pin(i='inspection'):return {'kind':'receipt','id':i,'revision':1,'content_hash':'a'*64}

def store_progress(c,p,state=None):
    raw=canonical(p).decode()
    c.execute("UPDATE arp_agent_sessions SET state=COALESCE(?,state),purge_progress_json=?,purge_progress_hash=?,sealed_highwater=1,delete_proof_ref_json='{}',row_version=row_version+1 WHERE session_id='s'",(state,raw,digest(p)))

def purging(c):
    c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2 WHERE session_id='s'")
    p=drain(c);p['phase']='RENAME_PENDING';store_progress(c,p,'PURGING');return p

def observation(p,source,trash):
    return {'schema_version':1,'session_id':p['session_id'],'destroy_command_id':p['destroy_command_id'],
            'control_generation':p['control_generation'],'expected_marker_hash':p['expected_marker_hash'],
            'source_state':source,'trash_state':trash,'observed_at_ms':10,'receipt_ref':pin()}

class PurgeReview(unittest.TestCase):
    def test_R1_block_is_durable_without_reviving(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=str(Path(tmp)/'db');c=execution(path);p=purging(c)
            p['inspection_receipt_ref']=pin();p['blocking']=copy.deepcopy(E['PurgeBlock'])
            decode('PurgeProgress',canonical(p));store_progress(c,p);c.commit();c.close()
            c=sqlite3.connect(path)
            row=c.execute('SELECT state,generation,destroy_command_id,purge_progress_json FROM arp_agent_sessions').fetchone()
            self.assertEqual(row[:3],('PURGING',2,'destroy'));self.assertEqual(json.loads(row[3])['blocking']['code'],'DUAL_DIRECTORY')
            for target in ('ACTIVE','QUARANTINED'):
                with self.subTest(target=target),self.assertRaises(sqlite3.IntegrityError):
                    c.execute('UPDATE arp_agent_sessions SET state=?,row_version=row_version+1 WHERE session_id=\'s\'',(target,))
            self.assertFalse(can_rebuild({'state':'QUARANTINED','destroy_command_id':'destroy','purge_progress':p}))
            c.close()
    def test_R1_same_destroy_and_marker_cannot_change(self):
        c=execution();p=purging(c)
        for field,value in [('destroy_command_id','another'),('expected_marker_hash','f'*64),('trash_relative_directory','trash/other')]:
            q=copy.deepcopy(p);q[field]=value
            with self.subTest(field=field),self.assertRaises(sqlite3.IntegrityError):store_progress(c,q)
        c.close()
    def test_R1_recovery_matrix(self):
        p=copy.deepcopy(E['PurgeProgress']);p['phase']='RENAME_PENDING'
        self.assertEqual(recovery_action(p,observation(p,'MATCH','ABSENT')),'RENAME_EXACT')
        self.assertEqual(recovery_action(p,observation(p,'ABSENT','MATCH')),'RECORD_RECOVERED_RENAME')
        self.assertEqual(recovery_action(p,observation(p,'ABSENT','ABSENT')),'BLOCK_DELETE_STATE_AMBIGUOUS')
        self.assertEqual(recovery_action(p,observation(p,'MATCH','MATCH')),'BLOCK_DUAL_DIRECTORY')
        self.assertEqual(recovery_action(p,observation(p,'MISMATCH','ABSENT')),'BLOCK_MARKER_MISMATCH')
        p.update(phase='RENAMED',rename_receipt_ref=pin('rename'))
        self.assertEqual(recovery_action(p,observation(p,'ABSENT','MATCH')),'DELETE_EXACT')
        self.assertEqual(recovery_action(p,observation(p,'ABSENT','ABSENT')),'RECORD_DELETION_ABSENCE')
        self.assertEqual(recovery_action(p,observation(p,'MATCH','ABSENT')),'BLOCK_DELETE_STATE_AMBIGUOUS')
        p.update(phase='DELETE_CONFIRMED',delete_receipt_ref=pin('deleted'))
        self.assertEqual(recovery_action(p,observation(p,'ABSENT','ABSENT')),'FINALIZE_SAME_DESTROY')
    def test_R1_actual_rename_and_reopen_inspection(self):
        # Actual temp files exercise observed locations; no product directories used.
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);source=root/'sessions/s';trash=root/'trash/s-destroy';source.mkdir(parents=True);trash.parent.mkdir()
            marker=b'exact-original-marker';(source/'marker').write_bytes(marker)
            p=copy.deepcopy(E['PurgeProgress']);p['phase']='RENAME_PENDING'
            source.rename(trash) # emulate exit before recording the rename receipt
            self.assertFalse(source.exists());self.assertEqual((trash/'marker').read_bytes(),marker)
            self.assertEqual(recovery_action(p,observation(p,'ABSENT','MATCH')),'RECORD_RECOVERED_RENAME')
            source.mkdir();(source/'marker').write_bytes(marker)
            self.assertEqual(recovery_action(p,observation(p,'MATCH','MATCH')),'BLOCK_DUAL_DIRECTORY')
            self.assertTrue(source.exists()and trash.exists())
    def test_R1_delete_requires_receipts_and_terminal_is_irreversible(self):
        c=execution();p=purging(c)
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p,'PURGED')
        p.update(phase='RENAMED',rename_receipt_ref=pin('rename'));store_progress(c,p)
        p.update(phase='DELETE_CONFIRMED',delete_receipt_ref=pin('deleted'));store_progress(c,p)
        store_progress(c,p,'PURGED')
        self.assertEqual(c.execute('SELECT state FROM arp_agent_sessions').fetchone()[0],'PURGED')
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p,'ACTIVE')
        c.close()

class SearchReview(unittest.TestCase):
    def bad_a(self):
        x=copy.deepcopy(E['SearchPage']);x.update(has_more=True,next_cursor='c',page_semantics='PROGRESS')
        x['receipt'].update(query_result_count=None,has_more=True)
        x['receipt']['coverage'].update(phase='SCANNING',rank_scope='NONE',ranking_final=False,snapshot_chunks=1,scanned_chunks=0)
        return x
    def bad_b(self):
        x=copy.deepcopy(E['SearchPage']);x['receipt']['coverage'].update(index_coverage='PARTIAL',expected_groups=1,indexed_groups=0);return x
    def test_R2_A_and_B_rejected_receipt_page_and_management(self):
        for make in (self.bad_a,self.bad_b):
            for typ in ('RetrievalReceipt','SearchPage','ManagementSearchPage','ContextSearchPage'):
                x=make()
                if typ=='RetrievalReceipt':x=x['receipt']
                elif typ!='SearchPage':x['cursor_purpose']='MANAGEMENT_SEARCH'if typ=='ManagementSearchPage'else'CONTEXT_RECALL'
                with self.subTest(kind=typ,bad=make.__name__),self.assertRaisesRegex(RuleError,'RETRIEVAL_STATE_INVALID'):decode(typ,canonical(x))
    def test_R2_nested_host_response_cannot_bypass(self):
        x=copy.deepcopy(E['HostResponse']);x.update(request_verb='agent_context_history_search')
        verbs=json.loads((ROOT/'contracts/host-verbs.json').read_text())
        name=next(k for k,v in verbs.items()if v['response_type']=='ManagementSearchPage')
        x['request_verb']=name;x['error']=None;x['command_receipt']=None
        page=self.bad_a();page['cursor_purpose']='MANAGEMENT_SEARCH';x['items']=[page];x['next_cursor']=page['next_cursor']
        with self.assertRaisesRegex(RuleError,'RETRIEVAL_STATE_INVALID'):decode('HostResponse',canonical(x))
    def test_R2_valid_scanning_partial_and_lexical_zero(self):
        x=self.bad_a();x['receipt']['status']='PARTIAL';decode('SearchPage',canonical(x))
        x=self.bad_b();x['receipt']['status']='PARTIAL';decode('SearchPage',canonical(x))
        x=copy.deepcopy(E['SearchPage']);x['receipt']['coverage']['mode']='LEXICAL_ONLY';x['receipt']['status']='LEXICAL_ONLY';decode('SearchPage',canonical(x))
        decode('SearchPage',canonical(E['SearchPage']))
    def test_R2_lexical_scanning_priority(self):
        x=self.bad_a();x['receipt']['coverage']['mode']='LEXICAL_ONLY';x['receipt']['status']='PARTIAL';decode('SearchPage',canonical(x))
        x['receipt']['status']='LEXICAL_ONLY'
        with self.assertRaisesRegex(RuleError,'RETRIEVAL_STATE_INVALID'):decode('SearchPage',canonical(x))
    def test_R2_nonzero_query_may_not_emit_empty_page(self):
        x=copy.deepcopy(E['SearchPage']);x['receipt'].update(status='COMPLETE',query_result_count=1)
        with self.assertRaisesRegex(RuleError,'RETRIEVAL_STATE_INVALID'):decode('SearchPage',canonical(x))
    def test_R2_positive_pages_and_last_page(self):
        x=copy.deepcopy(E['SearchPage']);item=copy.deepcopy(E['RecallItem']);item['rank_ordinal']=1
        x['items']=[item];x.update(has_more=True,next_cursor='next');x['receipt'].update(status='COMPLETE',query_result_count=2,returned_count=1,has_more=True)
        decode('SearchPage',canonical(x))
        x['items'][0]['rank_ordinal']=2;x['receipt'].update(page_offset=1,has_more=False);x.update(has_more=False,next_cursor=None);decode('SearchPage',canonical(x))
        x['receipt']['status']='COMPLETE_EMPTY'
        with self.assertRaises(RuleError):decode('SearchPage',canonical(x))
    def test_R2_context_summary_uses_aggregate_status(self):
        x=copy.deepcopy(E['ContextSummaryView']);x.update(context_id='ctx',retrieval_summary=copy.deepcopy(E['RetrievalSummary']),recall_count=0,retrieval_status='COMPLETE_EMPTY')
        decode('ContextSummaryView',canonical(x))
        x['retrieval_summary']['coverage']['index_coverage']='PARTIAL';x['retrieval_summary']['coverage']['expected_groups']=1
        with self.assertRaisesRegex(RuleError,'RETRIEVAL_STATE_INVALID'):decode('ContextSummaryView',canonical(x))
    def test_R2_truth_table_enumeration(self):
        for phase in('SCANNING','RESULTS'):
            for cov in('PARTIAL','COMPLETE'):
                for mode in('HYBRID','LEXICAL_ONLY','EXACT_ONLY'):
                    for total in(0,1):
                        c=dict(E['SearchCoverage'],phase=phase,index_coverage=cov,mode=mode)
                        expected='PARTIAL'if phase=='SCANNING'or cov=='PARTIAL'else'LEXICAL_ONLY'if mode=='LEXICAL_ONLY'else'COMPLETE_EMPTY'if total==0 else'COMPLETE'
                        self.assertEqual(status_for(c,total),expected)

class RankAndLimitReview(unittest.TestCase):
    def test_R4_channel_top1_uses_full_key_not_chunk(self):
        rows=(ScoredRow(1,'a',(('words',1.0),),'z','a'*64,0,1),ScoredRow(2,'z',(('words',1.0),),'a','a'*64,0,1))
        s=advance(freeze(rows),rows,page_rows=1,top_k=1);s=advance(s,rows,page_rows=1,top_k=1)
        self.assertEqual(s.results[0][0],'z')
    def test_R4_final_rrf_tie_uses_full_key(self):
        keys={'a':('z','a'*64,0,1,'a'),'z':('a','a'*64,0,1,'z')}
        self.assertEqual(rrf({'words':[('a',1),('z',1)]},1,stable_keys=keys,channel_limit=1)[0][0],'z')
        from reference.runtime_rules import stable_fused_topk
        self.assertEqual(stable_fused_topk({'a':1.0,'z':1.0},keys,1)[0][0],'z')
    def test_R5_policy_upper_bounds_and_exact_boundary(self):
        for field,limit in [('max_scan_rows_per_page',256),('max_search_cursors',16),('max_retrieval_ms',500),('max_query_total_ms',30000)]:
            x=copy.deepcopy(E['Policy']);x[field]=limit;decode('Policy',canonical(x));x[field]=limit+1
            with self.subTest(field=field),self.assertRaises(RuleError):decode('Policy',canonical(x))
    def test_R5_channel_K_not_recapped_to_global_M(self):
        # X ranks second in two channels; it wins globally only if K=2 is kept while M=1.
        rows=(ScoredRow(1,'x',(('words',2),('trigram',2)),'x','a'*64,0,1),
              ScoredRow(2,'a',(('words',3),),'a','a'*64,0,1),ScoredRow(3,'b',(('trigram',3),),'b','a'*64,0,1))
        final=advance(freeze(rows),rows,page_rows=3,top_k=2,max_candidates=1)
        self.assertEqual(final.results[0][0],'x')

class RecallReview(unittest.TestCase):
    def setup_recall(self,status='SUCCEEDED',allow=True):
        budget=Budget(50,50,50,10,5,0,0,5,10)
        groups=(Group('anchor','cur',100,2,'USER_ANCHOR',True),Group('huge','old',99,100),Group('protected','old',98,2))
        # protected is explicitly excluded by the already-captured composer subset.
        def row(n,c,g,seq,score):return RecallRow(ScoredRow(n,c,(('words',score),),'r'+c,'a'*64,0,1),g,seq,2)
        rows=(row(1,'first','older',1,1),row(2,'target','old2',2,5),row(3,'mandatory','anchor',3,100),row(4,'protected-hit','protected',4,100),row(5,'future','later',101,200))
        request={'highwater':100,'exclusions':['anchor','protected'],'query_text':'test','disabled':False,
                 'source_hash':'source-1','deadline_ms':30000,'embedding_fingerprint':'emb','allow_lexical_degradation':allow,
                 'page_rows':1,'channel_k':8,'global_m':8}
        store={};calls=ReferenceOriginalCallPort(status=status);composer=ReferenceComposer(ReferenceRecallCoordinator(store,calls))
        params=dict(current_source_hash='source-1',budget=budget,fixed=1,groups=groups,turns=(Turn('old',True,frozenset({'huge','protected'})),),current_turn_id='cur')
        return rows,request,store,calls,composer,params
    def test_R3_composer_reaches_later_page_and_excludes_frozen_groups(self):
        rows,r,st,calls,c,kw=self.setup_recall()
        first=c.prepare('key',r,rows,now_ms=1,**kw);self.assertEqual(first['state'],'PREPARE_PENDING');self.assertIsNone(first['selection'])
        second=c.prepare('key',r,rows,now_ms=2,**kw)
        self.assertEqual(second['state'],'CANDIDATE');self.assertEqual(second['selection'].recalled[0].id,'target')
        self.assertEqual({x['id']for x in second['recall']['candidates']},{'first','target'})
        self.assertEqual(sum(x['submissions']for x in calls.records.values()),1)
    def test_R3_composer_restart_keeps_query_call_and_snapshot(self):
        rows,r,st,calls,c,kw=self.setup_recall();c.prepare('key',r,rows,now_ms=1,**kw)
        recovered=json.loads(json.dumps(st));c2=ReferenceComposer(ReferenceRecallCoordinator(recovered,calls))
        result=c2.prepare('key',r,rows+(RecallRow(ScoredRow(6,'added',(('words',1000),),'a','a'*64,0,1),'new',102,1),),now_ms=2,**kw)
        self.assertEqual(result['selection'].recalled[0].id,'target');self.assertEqual(len(calls.records),1)
    def test_R3_limit_not_empty_complete(self):
        rows,r,st,calls,c,kw=self.setup_recall();c.prepare('key',r,rows,now_ms=1,**kw)
        x=c.prepare('key',r,rows,now_ms=30000,**kw)
        self.assertEqual(x['recall']['status'],'SKIPPED');self.assertEqual(x['recall']['reason'],'INDEX_SCAN_LIMIT');self.assertEqual(x['selection'].recalled,())
    def test_R3_unknown_does_not_resubmit(self):
        rows,r,st,calls,c,kw=self.setup_recall('UNKNOWN',False)
        for now in(1,2,3):self.assertEqual(c.prepare('key',r,rows,now_ms=now,**kw)['state'],'PREPARE_PENDING')
        self.assertEqual(len(calls.records),1);self.assertEqual(next(iter(calls.records.values()))['submissions'],1)
    def test_R3_changed_source_rejects_not_requeries(self):
        rows,r,st,calls,c,kw=self.setup_recall();c.prepare('key',r,rows,now_ms=1,**kw);kw['current_source_hash']='changed'
        with self.assertRaisesRegex(RuleError,'RECALL_SOURCE_STALE'):c.prepare('key',r,rows,now_ms=2,**kw)
        self.assertEqual(len(calls.records),1)
    def test_R3_query_recipe_long_empty_and_deterministic(self):
        self.assertEqual(build_query({})[0],'')
        parts={'CURRENT_INPUT':'中'*10000,'TASK_GOAL':'goal','LATEST_FEEDBACK':'error'}
        a=build_query(parts);self.assertEqual(a,build_query(parts));self.assertLessEqual(len(a[0]),4096);self.assertTrue(a[1][0]['truncated'])
    def test_R3_empty_query_no_embedding(self):
        rows,r,st,calls,c,kw=self.setup_recall();r['query_text']=''
        x=c.prepare('key',r,rows,now_ms=1,**kw);self.assertEqual(x['phase'],'SKIPPED');self.assertEqual(calls.records,{})
    def test_R3_aggregate_hash_and_complete_count(self):
        x=copy.deepcopy(E['ContextRecallResult']);decode('ContextRecallResult',canonical(x))
        x['candidate_set_hash']='f'*64
        with self.assertRaisesRegex(RuleError,'RECALL_AGGREGATE_MISMATCH'):decode('ContextRecallResult',canonical(x))

class FinalCrossChecks(unittest.TestCase):
    def test_R1_missing_phase_cannot_bypass_sql(self):
        c=execution();p=purging(c);del p['phase']
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p)
        c.close()
    def test_R1_anomaly_after_delete_receipt_stays_blocked(self):
        c=execution();p=purging(c)
        p.update(phase='RENAMED',rename_receipt_ref=pin('ren'));store_progress(c,p)
        p.update(phase='DELETE_CONFIRMED',delete_receipt_ref=pin('del'));store_progress(c,p)
        p['blocking']=copy.deepcopy(E['PurgeBlock']);p['inspection_receipt_ref']=pin('new-inspection')
        decode('PurgeProgress',canonical(p));store_progress(c,p)
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p,'PURGED')
        p['blocking']=None;p['rename_receipt_ref']=pin('other-ren')
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p)
        c.close()
    def test_R1_drain_cannot_jump_phases(self):
        c=execution();c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2 WHERE session_id='s'")
        p=drain(c);p.update(phase='DELETE_CONFIRMED',rename_receipt_ref=pin('ren'),delete_receipt_ref=pin('del'))
        with self.assertRaises(sqlite3.IntegrityError):store_progress(c,p,'PURGING')
        c.close()
    def test_R3_deadline_does_not_override_no_degradation_policy(self):
        rows,r,st,calls,c,kw=RecallReview().setup_recall('UNKNOWN',False)
        c.prepare('key',r,rows,now_ms=1,**kw)
        with self.assertRaisesRegex(RuleError,'EMBEDDING_UNAVAILABLE'):c.prepare('key',r,rows,now_ms=30000,**kw)
        self.assertEqual(st['key']['status'],'BLOCKED');self.assertEqual(len(calls.records),1)
    def test_R3_checkpoint_strict_fields_and_counts(self):
        x=copy.deepcopy(E['ContextRecallCheckpoint']);decode('ContextRecallCheckpoint',canonical(x))
        x['scan_pages_committed']=1
        with self.assertRaisesRegex(RuleError,'RECALL_BINDING_INVALID'):decode('ContextRecallCheckpoint',canonical(x))
    def test_R3_page_aggregate_checks_identity_and_missing_pages(self):
        from reference.context_recall import collect_frozen_pages
        r=copy.deepcopy(E['ContextRecallRequest']);snap=copy.deepcopy(E['IndexSnapshot']);p=copy.deepcopy(E['ContextSearchPage']);rec=p['receipt']
        for f in ('session_id','query_hash','journal_highwater','source_snapshot_hash','policy_ref','authority_readset_hash'):rec[f]=r[f]
        rec.update(generation=r['control_generation'],excluded_group_ids=sorted(set(r['mandatory_group_ids'])|set(r['protected_group_ids'])),index_snapshot_id=snap['snapshot_id'],index_generation=snap['index_generation'],index_upper_commit=snap['upper_commit'])
        self.assertEqual(collect_frozen_pages(r,snap,[p]),())
        bad=copy.deepcopy(p);bad['receipt']['index_upper_commit']+=1
        with self.assertRaisesRegex(RuleError,'RECALL_AGGREGATE_MISMATCH'):collect_frozen_pages(r,snap,[bad])
        scan=copy.deepcopy(p);scan.update(page_semantics='PROGRESS',has_more=True,next_cursor='next')
        scan['receipt'].update(query_result_count=None,status='PARTIAL',has_more=True)
        scan['receipt']['coverage'].update(phase='SCANNING',ranking_final=False,rank_scope='NONE')
        with self.assertRaisesRegex(RuleError,'RECALL_AGGREGATE_MISMATCH'):collect_frozen_pages(r,snap,[scan])
        self.assertEqual(collect_frozen_pages(r,snap,[scan,p]),())
    def test_R3_central_coordination_has_no_terminal_rewrite(self):
        c=execution();c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2 WHERE session_id='s'")
        req=copy.deepcopy(E['ContextRecallRequest']);cp=copy.deepcopy(E['ContextRecallCheckpoint'])
        req.update(agent_id='a',session_id='s',turn_id='t');cp['recall_key']=req['recall_key'];cp['request_hash']=digest(req)
        c.execute('''INSERT INTO arp_context_recalls(recall_key,session_id,agent_id,turn_id,original_request_key,provider_request_ordinal,control_generation,request_hash,request_json,query_id,phase,progress_json,deadline_ms,next_wake_at_ms,row_version)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(req['recall_key'],'s','a','t',req['original_request_key'],req['provider_request_ordinal'],req['control_generation'],digest(req),canonical(req).decode(),'query-fixed','PREPARING',canonical(cp).decode(),req['deadline_ms'],req['created_at_ms'],1))
        c.execute("UPDATE arp_context_recalls SET phase='BLOCKED',row_version=2 WHERE recall_key=?",(req['recall_key'],))
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_context_recalls SET phase='PREPARING',row_version=3 WHERE recall_key=?",(req['recall_key'],))
        c.close()
