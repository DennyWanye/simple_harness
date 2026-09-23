-- One DB per logical session. Derived retrieval index only; Journal remains original.
CREATE TABLE session_partition (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),session_id TEXT NOT NULL,agent_id TEXT NOT NULL,
 root_id TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),
 source_from INTEGER NOT NULL CHECK(source_from>0), schema_version INTEGER NOT NULL CHECK(schema_version=1)
) STRICT;
CREATE TABLE indexed_groups (
 group_id TEXT PRIMARY KEY NOT NULL,seq_from INTEGER NOT NULL,seq_to INTEGER NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),chunk_count INTEGER NOT NULL CHECK(chunk_count>=0),
 source_epoch INTEGER NOT NULL CHECK(source_epoch>=0),
 CHECK(seq_from>0 AND seq_to>=seq_from), UNIQUE(seq_from,seq_to)
) STRICT;
CREATE TABLE session_chunks (
 rowid INTEGER PRIMARY KEY,chunk_id TEXT NOT NULL UNIQUE,
 group_id TEXT NOT NULL REFERENCES indexed_groups(group_id),record_id TEXT NOT NULL,
 utf8_start INTEGER NOT NULL CHECK(utf8_start>=0),utf8_end INTEGER NOT NULL CHECK(utf8_end>utf8_start),
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),view_hash TEXT NOT NULL CHECK(length(view_hash)=64),
 text_view TEXT NOT NULL,provenance TEXT NOT NULL CHECK(provenance IN ('USER_INPUT','RAW_DIALOGUE','AGENT_CLAIM','TOOL_RESULT','VERIFIER_FEEDBACK')),
 validity_epoch INTEGER NOT NULL CHECK(validity_epoch>=0),
 UNIQUE(record_id,utf8_start,utf8_end,source_hash)
) STRICT;
CREATE TABLE session_vectors (
 chunk_id TEXT NOT NULL REFERENCES session_chunks(chunk_id),embedding_fingerprint TEXT NOT NULL,
 dim INTEGER NOT NULL CHECK(dim>0 AND dim<=65536),vector_le_f32 BLOB NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),generation INTEGER NOT NULL CHECK(generation>0),
 embedding_receipt_ref_json TEXT NOT NULL CHECK(json_valid(embedding_receipt_ref_json)),
 PRIMARY KEY(chunk_id,embedding_fingerprint),CHECK(length(vector_le_f32)=4*dim)
) STRICT;
CREATE TABLE session_index_progress (
 embedding_fingerprint TEXT PRIMARY KEY,covered_until_group_seq INTEGER NOT NULL CHECK(covered_until_group_seq>=0),
 target_highwater INTEGER NOT NULL CHECK(target_highwater>=covered_until_group_seq),
 last_receipt_ref_json TEXT NOT NULL CHECK(json_valid(last_receipt_ref_json))
) STRICT;
CREATE VIRTUAL TABLE session_words USING fts5(text_view,content='session_chunks',content_rowid='rowid',tokenize='unicode61');
CREATE VIRTUAL TABLE session_trigrams USING fts5(text_view,content='session_chunks',content_rowid='rowid',tokenize='trigram');
CREATE TRIGGER session_chunks_ai AFTER INSERT ON session_chunks BEGIN
 INSERT INTO session_words(rowid,text_view) VALUES(new.rowid,new.text_view);
 INSERT INTO session_trigrams(rowid,text_view) VALUES(new.rowid,new.text_view);
END;
CREATE TRIGGER session_chunks_ad AFTER DELETE ON session_chunks BEGIN
 INSERT INTO session_words(session_words,rowid,text_view) VALUES('delete',old.rowid,old.text_view);
 INSERT INTO session_trigrams(session_trigrams,rowid,text_view) VALUES('delete',old.rowid,old.text_view);
END;
CREATE TRIGGER session_chunks_no_update BEFORE UPDATE ON session_chunks
BEGIN SELECT RAISE(ABORT,'derived chunk replace through explicit delete+insert only'); END;
CREATE TRIGGER session_partition_identity BEFORE UPDATE ON session_partition
WHEN NEW.session_id!=OLD.session_id OR NEW.agent_id!=OLD.agent_id OR NEW.root_id!=OLD.root_id OR NEW.source_from!=OLD.source_from OR NEW.generation<OLD.generation
BEGIN SELECT RAISE(ABORT,'partition identity conflict'); END;
