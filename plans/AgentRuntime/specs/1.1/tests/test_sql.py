import unittest,sqlite3,struct,tempfile
from pathlib import Path
from tests.sql_fixture import execution,partition,generation,group,chunk,apply,ROOT
class SQLContracts(unittest.TestCase):
    def test_execution_descriptor(self):
        c=execution();self.assertEqual(c.execute('pragma foreign_key_check').fetchall(),[]);self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok');c.close()
    def test_partition_descriptor(self):
        c=partition();self.assertEqual(c.execute('pragma foreign_key_check').fetchall(),[]);c.close()
    def test_same_span_different_index_generation(self):
        c=partition();generation(c,1);group(c,1);chunk(c,1,'c1');generation(c,2);group(c,2);chunk(c,2,'c2');self.assertEqual(c.execute('select count(*)from session_chunks').fetchone()[0],2);c.close()
    def test_closed_source_is_immutable(self):
        c=partition();generation(c);group(c)
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE group_sources SET source_hash=?",('f'*64,))
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT OR REPLACE INTO group_sources VALUES('g',?,1,1,'{}')",('f'*64,))
        c.close()
    def test_chunks_no_replace(self):
        c=partition();generation(c);group(c);chunk(c)
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE session_chunks SET text_view='replacement'")
        c.close()
    def test_vector_matches_generation(self):
        c=partition();generation(c);group(c);chunk(c)
        args=('c1',1,'x'*64,2,struct.pack('<ff',1,0),'d'*64,2,'{}')
        with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO session_vectors VALUES(?,?,?,?,?,?,?,?)',args)
        c.execute('INSERT INTO session_vectors VALUES(?,?,?,?,?,?,?,?)',('c1',1,'c'*64,2,struct.pack('<ff',1,0),'d'*64,2,'{}'));c.close()
    def test_snapshot_excludes_new_append(self):
        c=partition();generation(c);group(c);chunk(c,1,'early',seq=1);chunk(c,1,'later','不同新记录',seq=2)
        rows=c.execute('SELECT chunk_id FROM session_chunks WHERE index_generation=1 AND commit_seq<=1').fetchall();self.assertEqual(rows,[('early',)]);c.close()
    def test_fts_short_chinese_fallback(self):
        c=partition();generation(c);group(c);chunk(c)
        self.assertEqual(c.execute("SELECT rowid FROM session_trigrams WHERE session_trigrams MATCH ?",('"早期"',)).fetchall(),[])
        self.assertEqual(c.execute('SELECT chunk_id FROM session_chunks WHERE instr(text_view,?)>0',('早期',)).fetchone()[0],'c1');c.close()
    def test_fts_literal_escape(self):
        c=partition();generation(c);group(c);chunk(c,text='literal OR token')
        term='literal " OR';escaped='"'+term.replace('"','""')+'"'
        c.execute('SELECT rowid FROM session_words WHERE session_words MATCH ?',(escaped,)).fetchall();c.close()
    def test_whole_migration_rollback(self):
        c=sqlite3.connect(':memory:');c.execute('pragma foreign_keys=on');c.execute('begin')
        c.execute('CREATE TABLE base_agent_bindings_v1(agent_id TEXT PRIMARY KEY)');c.execute('CREATE TABLE base_agent_turns_v1(turn_id TEXT PRIMARY KEY)')
        apply(c,ROOT/'sql/execution_additive.sql')
        with self.assertRaises(sqlite3.OperationalError):c.execute('THIS IS INVALID')
        c.rollback();self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE name LIKE 'arp_%'").fetchall(),[]);c.close()
    def test_session_not_created_active(self):
        c=execution()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_agent_sessions SET state='PURGED',row_version=2")
        c.close()
    def test_session_transition(self):
        c=execution();c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s'")
        c.execute("UPDATE arp_agent_sessions SET state='DRAINING',generation=2,row_version=3,updated_at_ms=3 WHERE session_id='s'");self.assertEqual(c.execute('SELECT state,generation FROM arp_agent_sessions').fetchone(),('DRAINING',2));c.close()
    def test_profile_replace_guard_even_recursive_off(self):
        c=execution();c.execute('pragma recursive_triggers=off')
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT OR REPLACE INTO arp_profiles VALUES('p',1,?,'{}','{}')",('2'*64,))
        c.close()
    def test_future_hold_not_ignored_by_retention(self):
        c=execution()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("DELETE FROM arp_profiles WHERE profile_id='p'")
        c.close()
    def test_creation_intent_only_prepared(self):
        c=execution()
        with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO arp_creation_intents VALUES('ci','o','k',?,'a2','r2','{}','BOUND',1,'{}')",('a'*64,))
        c.execute("INSERT INTO arp_creation_intents VALUES('ci','o','k',?,'a2','r2','{}','PREPARED',1,'{}')",('a'*64,))
        c.execute("UPDATE arp_creation_intents SET state='BOUND',row_version=2");c.close()
    def test_catalogue_namespace_separation(self):
        c=execution()
        for n in ('n1','n2'):c.execute("INSERT INTO arp_catalog_revisions VALUES(?,'SKILL','sk',1,?,'{}','{}',NULL)",(n,'a'*64))
        self.assertEqual(c.execute('SELECT count(*)FROM arp_catalog_revisions').fetchone()[0],2);c.close()
    def test_two_connections_cas_only_one_wins(self):
        with tempfile.TemporaryDirectory()as td:
            p=str(Path(td)/'test.db');a=execution(p);b=sqlite3.connect(p);b.execute('pragma foreign_keys=on');b.execute('pragma recursive_triggers=on')
            a.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s' AND row_version=1");a.commit()
            count=b.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s' AND row_version=1").rowcount;self.assertEqual(count,0);b.rollback();a.close();b.close()
