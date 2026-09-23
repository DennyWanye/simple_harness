from __future__ import annotations
import json,math,random,sqlite3,tempfile,unittest
from pathlib import Path
from reference.runtime_rules import *
from reference.schema_codec import decode
ROOT=Path(__file__).resolve().parents[1]

def apply(conn,path):
    for q in sql_statements(path.read_text()):conn.execute(q)

def db():
    c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON')
    # Minimal key contract fixture, NEVER production migration evidence.
    c.executescript('CREATE TABLE base_agent_bindings_v1(agent_id TEXT PRIMARY KEY);CREATE TABLE base_agent_turns_v1(turn_id TEXT PRIMARY KEY,agent_id TEXT REFERENCES base_agent_bindings_v1);')
    apply(c,ROOT/'sql/execution_additive.sql')
    c.execute("INSERT INTO base_agent_bindings_v1 VALUES('a')")
    c.execute("INSERT INTO base_agent_turns_v1 VALUES('t','a')")
    c.execute("INSERT INTO arp_profiles VALUES('p',1,?,'{}','{}')",('1'*64,))
    c.execute("INSERT INTO arp_agent_protocols VALUES('a','ARP_V1','{}',?,0)",('1'*64,))
    c.execute("INSERT INTO arp_agent_sessions(session_id,agent_id,profile_id,profile_revision,profile_hash,creation_root_id,root_incarnation,creation_key,create_command_hash,relative_directory,state,generation,row_version,journal_seq_from,created_at_ms,updated_at_ms) VALUES('s','a','p',1,?,'root','root','create',?,'sessions/s','CREATING',1,1,1,1,1)",('1'*64,'2'*64))
    return c

class BudgetTests(unittest.TestCase):
    def b(self,cap=100,recall=30,floor=20):return Budget(cap,None,cap,20,10,0,0,floor,recall)
    def test_variable_n(self):
        r=allocate(self.b(130,0,0),29,tuple(Group(str(i),t) for i,t in enumerate([35,40,6,25,8,12])),())
        self.assertEqual([g.id for g in r.recent],['1','2','3','4','5']);self.assertEqual(r.used,120)
    def test_large_current_protected(self):
        r=allocate(self.b(),10,(Group('old',20),Group('cur',60)),(Recall('r',frozenset({'old'}),30),))
        self.assertIn('cur',[g.id for g in r.recent]);self.assertLessEqual(r.used,r.input_budget)
    def test_overlapping_recall_removed_and_not_refilled(self):
        r=allocate(self.b(120,30,20),10,(Group('old',30),Group('mid',30),Group('cur',20)),(Recall('r',frozenset({'mid'}),25),))
        self.assertEqual(len(r.recent),3);self.assertEqual(r.recalled,())
    def test_contiguous_no_skip(self):
        r=allocate(self.b(),10,(Group('older',1),Group('big',100),Group('cur',10)),())
        self.assertEqual([g.id for g in r.recent],['cur'])
    def test_required_too_large(self):
        with self.assertRaisesRegex(RuleError,'REQUIRED'):allocate(self.b(),20,(Group('cur',100),),())
    def test_zero_recall_legitimate(self):
        r=allocate(self.b(),10,(Group('cur',5),),());self.assertEqual(r.recalled,())
    def test_count_tools_and_final_bytes(self):
        r=allocate(self.b(),10,(Group('cur',5),),())
        with self.assertRaisesRegex(RuleError,'FINAL'):verify_final_count(r,100,100,200)
        with self.assertRaisesRegex(RuleError,'BYTES'):verify_final_count(r,15,201,200)
    def test_output_cap(self):
        with self.assertRaises(RuleError):Budget(100,None,100,5,10,0,0,20,30).capacity()
    def test_separate_model_caps(self):self.assertEqual(Budget(524288,400000,350000,10000,8192,2048,0,65536,98304).capacity(),347952)
    def test_bool_not_int(self):
        with self.assertRaises(RuleError):self.b(True).capacity()
    def test_real_512k_fixture(self):
        b=Budget(524288,524288,524288,32768,8192,2048,0,65536,98304)
        r=allocate(b,90000,tuple(Group(str(i),20000) for i in range(40)),tuple(Recall(str(i),frozenset({str(i)}),4000) for i in range(20)))
        self.assertLessEqual(r.used,514048);self.assertGreater(len(r.recent),10)
    def test_required_full_tail(self):
        r=allocate(self.b(),0,(Group('u',10),Group('call',20),Group('tool',15)),(),required_tail=3)
        self.assertEqual(len(r.recent),3)
    def test_random_invariants(self):
        rng=random.Random(11)
        for _ in range(500):
            g=tuple(Group(str(i),rng.randint(1,50)) for i in range(rng.randint(1,30)))
            rs=tuple(Recall(f'r{i}',frozenset({str(i)}),rng.randint(1,15)) for i in range(len(g)))
            r=allocate(self.b(250,60,40),10,g,rs)
            self.assertEqual(r.recent,g[len(g)-len(r.recent):]);self.assertLessEqual(r.used,r.input_budget)
            ids={x.id for x in r.recent}
            self.assertTrue(all(not x.groups&ids for x in r.recalled))
            self.assertEqual(r.used,10+sum(x.tokens for x in r.recent)+sum(x.tokens for x in r.recalled))

class IdentityTests(unittest.TestCase):
    def test_cross_agent(self):
        with self.assertRaises(RuleError):retrieval_scope('s',1,'other',1,'ACTIVE')
    def test_generation(self):
        with self.assertRaises(RuleError):retrieval_scope('s',2,'s',1,'ACTIVE')
    def test_closed_not_readable(self):
        with self.assertRaises(RuleError):retrieval_scope('s',1,'s',1,'DRAINING')
    def test_vector_mismatch(self):
        with self.assertRaises(RuleError):validate_vector([1,2],3)
    def test_nan_vector(self):
        with self.assertRaises(RuleError):validate_vector([1,float('nan')],2)
    def test_zero_vector(self):
        with self.assertRaises(RuleError):validate_vector([0,0],2)
    def test_normalized_vector(self):self.assertAlmostEqual(sum(x*x for x in validate_vector([3,4],2)),1)
    def test_permissions_intersect(self):self.assertEqual(effective_permissions(frozenset({'read','write'}),frozenset({'read'})),frozenset({'read'}))
    def test_no_authority_source(self):
        with self.assertRaises(RuleError):effective_permissions()
    def test_no_mcp_hint_trust(self):
        with self.assertRaises(RuleError):tool_route('UNVERIFIED_HINT',True,True)
    def test_unexposed_tool(self):
        with self.assertRaises(RuleError):tool_route('READ_ONLY',False,True)
    def test_effect_not_direct(self):self.assertEqual(tool_route('EXTERNAL_EFFECT',True,True),'ORIGINAL_OPERATION_INTENT')
    def test_no_grant(self):
        with self.assertRaises(RuleError):tool_route('READ_ONLY',True,False)
    def test_provider_known_capability(self):
        c=dict(id='p',priority=0,registered=True,configured=True,healthy=True,authorized=True,compatible=True,features=['x'])
        self.assertEqual(choose_provider([c],frozenset({'x'})),'p')
        with self.assertRaises(RuleError):choose_provider([c],frozenset({'y'}))
    def test_purge_blockers(self):
        args=dict(closed=True,active_calls=0,unknown_calls=0,pending_imports=0,live_roots=0,active_index_writers=0,read_leases=0)
        self.assertTrue(can_purge(**args))
        for k in set(args)-{'closed'}:
            self.assertFalse(can_purge(**{**args,k:1}))
    def test_purge_crash_not_close(self):self.assertFalse(can_purge(closed=False,active_calls=0,unknown_calls=0,pending_imports=0,live_roots=0,active_index_writers=0,read_leases=0))
    def test_skill_paths(self):
        for p in ['../x','/x','a//x','a/./x','C:/x','a\\x','CON.txt','a.','a\x00b']:
            with self.subTest(p=p),self.assertRaises(RuleError):safe_skill_path(p)
        self.assertEqual(safe_skill_path('scripts/task.py'),'scripts/task.py')
    def test_case_collision(self):
        with self.assertRaises(RuleError):validate_bundle_paths(['A.md','a.md'])
    def test_json_duplicates(self):
        with self.assertRaises(RuleError):parse_strict(b'{"a":1,"a":2}')
    def test_json_nonfinite(self):
        for x in [b'NaN',b'Infinity',b'1e9999']:
            with self.assertRaises(RuleError):parse_strict(x)

class SQLTests(unittest.TestCase):
    def test_whole_trigger_split(self):
        c=sqlite3.connect(':memory:');c.execute('BEGIN')
        ddl="CREATE TABLE a(v TEXT); CREATE TRIGGER t BEFORE INSERT ON a WHEN NEW.v='bad' BEGIN SELECT RAISE(ABORT,'bad; value'); END;"
        for q in sql_statements(ddl):c.execute(q)
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO a VALUES('bad')")
        self.assertTrue(c.in_transaction);c.rollback();self.assertFalse(c.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchall())
    def test_initial_state_guard(self):
        c=db()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO arp_agent_sessions SELECT 'x',agent_id,profile_id,profile_revision,profile_hash,creation_root_id,root_incarnation,'new',create_command_hash,'sessions/x','PURGED',generation,row_version,journal_seq_from,3,'d','h','{}',created_at_ms,updated_at_ms FROM arp_agent_sessions")
    def test_session_terminal_no_delete(self):
        c=db()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("DELETE FROM arp_agent_sessions")
    def test_no_two_live_sessions(self):
        c=db()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO arp_agent_sessions SELECT 'x',agent_id,profile_id,profile_revision,profile_hash,creation_root_id,root_incarnation,'new',create_command_hash,'sessions/x',state,generation,row_version,journal_seq_from,sealed_highwater,destroy_command_id,destroy_command_hash,delete_proof_ref_json,created_at_ms,updated_at_ms FROM arp_agent_sessions")
    def test_session_generation_fence(self):
        c=db();c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s'")
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_agent_sessions SET state='DRAINING',row_version=3,updated_at_ms=3 WHERE session_id='s'")
        c.execute("UPDATE arp_agent_sessions SET state='DRAINING',generation=2,row_version=3,updated_at_ms=3 WHERE session_id='s'")
    def test_profile_immutable(self):
        c=db()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_profiles SET body_json='[]'")
    def test_catalogue_no_direct_admitted(self):
        c=db();c.execute("INSERT INTO arp_catalog_revisions VALUES('SKILL','sk',1,?,'{}','{}',NULL)",('1'*64,))
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO arp_catalog_activation VALUES('SKILL','sk',1,?,'ADMITTED',1,'{}',NULL,'{}')",('1'*64,))
    def test_real_fts_chinese_and_old_history(self):
        c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON');apply(c,ROOT/'sql/session_partition.sql')
        c.execute("INSERT INTO indexed_groups VALUES('g',1,1,?,1,0)",('1'*64,))
        c.execute("INSERT INTO session_chunks(chunk_id,group_id,record_id,utf8_start,utf8_end,source_hash,view_hash,text_view,provenance,validity_epoch) VALUES('old','g','r',0,30,?,?,'最早关键限制XYZ','USER_INPUT',0)",('1'*64,'2'*64))
        self.assertEqual(c.execute("SELECT rowid FROM session_trigrams WHERE session_trigrams MATCH '关键限'").fetchone()[0],1)
        c.execute('DELETE FROM session_chunks');self.assertEqual(c.execute("SELECT count(*) FROM session_trigrams WHERE session_trigrams MATCH '关键限'").fetchone()[0],0)
    def test_vector_blob_shape(self):
        c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON');apply(c,ROOT/'sql/session_partition.sql')
        c.execute("INSERT INTO indexed_groups VALUES('g',1,1,?,1,0)",('1'*64,));c.execute("INSERT INTO session_chunks VALUES(1,'c','g','r',0,1,?,?,'x','RAW_DIALOGUE',0)",('1'*64,'1'*64))
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO session_vectors VALUES('c','e',3,?,?,1,'{}')",(b'1234','1'*64))
    def test_all_ddl_transaction_rollback(self):
        c=db();c.commit();c.execute('BEGIN');c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s'");c.rollback();self.assertEqual(c.execute("SELECT state FROM arp_agent_sessions").fetchone()[0],'CREATING')

class SchemaTests(unittest.TestCase):pass
for p in sorted((ROOT/'examples').glob('*.json')):
    def good(self,p=p):decode(p.stem,p.read_bytes())
    def bad(self,p=p):
        data=json.loads(p.read_text());data['forged_authority']='x'
        with self.assertRaises(RuleError):decode(p.stem,canonical(data))
    setattr(SchemaTests,'test_valid_'+p.stem,good)
    setattr(SchemaTests,'test_extra_'+p.stem,bad)

if __name__=='__main__':unittest.main()
