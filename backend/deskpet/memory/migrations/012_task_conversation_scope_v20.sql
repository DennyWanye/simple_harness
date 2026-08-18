-- Root-local conversation identity under one product Session.
-- NULL preserves all pre-v20 transcript rows as stable session-level facts.
ALTER TABLE messages ADD COLUMN root_run_id TEXT;
ALTER TABLE messages ADD COLUMN task_scope_id TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_root_time
    ON messages(session_id, root_run_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_messages_task_scope_time
    ON messages(session_id, task_scope_id, created_at, id);

PRAGMA user_version = 20;
