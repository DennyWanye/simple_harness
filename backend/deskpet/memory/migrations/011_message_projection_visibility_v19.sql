-- State DB v19: separate durable history projections from conversational input.
-- Transaction, schema_migrations marker, and PRAGMA user_version are owned by
-- deskpet.memory.migrator; this file must not BEGIN/COMMIT by itself.

ALTER TABLE messages ADD COLUMN projection_kind TEXT NOT NULL DEFAULT 'legacy_message'
  CHECK(projection_kind IN (
    'legacy_message','user_message','assistant_message','tool_message','system_message',
    'final_assistant','workflow_progress','workflow_accepted','workflow_decision',
    'workflow_final_status','artifact_card'
  ));

ALTER TABLE messages ADD COLUMN context_visibility TEXT NOT NULL DEFAULT 'conversation'
  CHECK(context_visibility IN ('conversation','exclude'));

UPDATE messages
SET projection_kind=CASE role
  WHEN 'user' THEN 'user_message'
  WHEN 'assistant' THEN 'assistant_message'
  WHEN 'tool' THEN 'tool_message'
  WHEN 'system' THEN 'system_message'
  ELSE 'legacy_message' END,
  context_visibility='conversation';

CREATE INDEX idx_messages_session_visibility_time
  ON messages(session_id,context_visibility,created_at DESC);
CREATE INDEX idx_messages_visibility_role_time
  ON messages(context_visibility,role,created_at DESC);
CREATE INDEX idx_messages_visibility_salience
  ON messages(context_visibility,salience DESC,id DESC);

DROP TRIGGER IF EXISTS messages_ai;
DROP TRIGGER IF EXISTS messages_ad;
DROP TRIGGER IF EXISTS messages_au;

CREATE TRIGGER messages_ai AFTER INSERT ON messages
WHEN new.context_visibility='conversation'
BEGIN
  INSERT INTO messages_fts(rowid,content) VALUES(new.id,new.content);
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages
WHEN old.context_visibility='conversation'
BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,content)
    VALUES('delete',old.id,old.content);
END;

CREATE TRIGGER messages_au AFTER UPDATE ON messages
BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,content)
    SELECT 'delete',old.id,old.content
    WHERE old.context_visibility='conversation';
  INSERT INTO messages_fts(rowid,content)
    SELECT new.id,new.content
    WHERE new.context_visibility='conversation';
END;
