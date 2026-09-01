-- Permission-filtered immutable search documents and rebuildable FTS5 locator.

CREATE TABLE task_scope_search_documents (
    document_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    source_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
    source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    project TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at REAL NOT NULL,
    document_text TEXT NOT NULL,
    document_hash TEXT NOT NULL UNIQUE CHECK(length(document_hash) = 64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(source_id) REFERENCES task_scope_projection_sources(source_id)
);

CREATE TABLE task_scope_search_heads (
    task_scope_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
    updated_at REAL NOT NULL,
    FOREIGN KEY(document_id) REFERENCES task_scope_search_documents(document_id)
);

CREATE VIRTUAL TABLE task_scope_search_fts USING fts5(
    document_id UNINDEXED,
    task_scope_id UNINDEXED,
    subject UNINDEXED,
    title,
    goal,
    project,
    status,
    content,
    tokenize='unicode61'
);

CREATE TABLE task_scope_search_rebuild_receipts (
    receipt_id TEXT PRIMARY KEY,
    rebuild_id TEXT NOT NULL,
    source_set_hash TEXT NOT NULL CHECK(length(source_set_hash) = 64),
    index_root_hash TEXT NOT NULL CHECK(length(index_root_hash) = 64),
    document_count INTEGER NOT NULL CHECK(document_count >= 0),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE task_scope_search_access_receipts (
    receipt_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK(operation IN ('search','open')),
    subject TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    result_hash TEXT NOT NULL CHECK(length(result_hash) = 64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TRIGGER task_scope_search_documents_no_update BEFORE UPDATE ON task_scope_search_documents BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
CREATE TRIGGER task_scope_search_documents_no_delete BEFORE DELETE ON task_scope_search_documents BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
CREATE TRIGGER task_scope_search_heads_no_delete BEFORE DELETE ON task_scope_search_heads BEGIN SELECT RAISE(ABORT,'task_scope_search_head_required'); END;
CREATE TRIGGER task_scope_search_rebuild_receipts_no_update BEFORE UPDATE ON task_scope_search_rebuild_receipts BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
CREATE TRIGGER task_scope_search_rebuild_receipts_no_delete BEFORE DELETE ON task_scope_search_rebuild_receipts BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
CREATE TRIGGER task_scope_search_access_receipts_no_update BEFORE UPDATE ON task_scope_search_access_receipts BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
CREATE TRIGGER task_scope_search_access_receipts_no_delete BEFORE DELETE ON task_scope_search_access_receipts BEGIN SELECT RAISE(ABORT,'task_scope_search_append_only'); END;
