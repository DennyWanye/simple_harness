-- State DB v26: preserve message projection identity when rows are archived.
-- The migration runner owns the atomic boundary, marker, and version pointer.

ALTER TABLE messages_archive ADD COLUMN workflow_event_id TEXT;
ALTER TABLE messages_archive ADD COLUMN projection_kind TEXT NOT NULL DEFAULT 'legacy_message';
ALTER TABLE messages_archive ADD COLUMN context_visibility TEXT NOT NULL DEFAULT 'conversation';
ALTER TABLE messages_archive ADD COLUMN root_run_id TEXT;
ALTER TABLE messages_archive ADD COLUMN task_scope_id TEXT;
ALTER TABLE messages_archive ADD COLUMN projection_event_id TEXT;
ALTER TABLE messages_archive ADD COLUMN projection_owner_kind TEXT;
ALTER TABLE messages_archive ADD COLUMN projection_owner_id TEXT;
ALTER TABLE messages_archive ADD COLUMN projection_owner_generation INTEGER;
ALTER TABLE messages_archive ADD COLUMN projection_epoch INTEGER;
ALTER TABLE messages_archive ADD COLUMN projection_route_version INTEGER;
ALTER TABLE messages_archive ADD COLUMN projection_payload_hash TEXT;

CREATE INDEX idx_archive_root_keyset
  ON messages_archive(session_id, root_run_id, created_at, id);
CREATE INDEX idx_archive_projection_event
  ON messages_archive(session_id, projection_event_id, id);
