"""Durable SessionDB projection migration and owner-scoped contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrustedCompanionOwner:
    profile_id: str
    profile_generation: int
    binding_epoch: int
    owner_kind: str = "companion_profile"

    def __post_init__(self) -> None:
        if (
            self.owner_kind != "companion_profile"
            or not self.profile_id
            or self.profile_generation < 1
            or self.binding_epoch < 1
        ):
            raise ValueError("invalid trusted companion owner")


COMPANION_REDACTION_TOMBSTONE = canonical_json(
    {
        "schema_version": 1,
        "kind": "companion_projection_tombstone",
        "summary": "此成长记录已被遗忘。",
        "detail_ref": None,
        "available_actions": [],
    }
)
COMPANION_REDACTION_TOMBSTONE_HASH = canonical_hash(
    json.loads(COMPANION_REDACTION_TOMBSTONE)
)


@dataclass(frozen=True, slots=True)
class TrustedCompanionProjectionRoute:
    owner: TrustedCompanionOwner
    session_id: str
    projection_epoch: int
    route_version: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner, TrustedCompanionOwner)
            or not self.session_id.strip()
            or self.projection_epoch < 0
            or self.route_version < 1
        ):
            raise ValueError("invalid trusted companion projection route")


@dataclass(frozen=True, slots=True)
class CurrentCompanionProjection:
    owner: TrustedCompanionOwner
    event_id: str
    payload_hash: str
    content: str
    status: str = "current"
    redaction_id: str | None = None
    redaction_version: int | None = None
    redacted_from_payload_hash: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner, TrustedCompanionOwner)
            or not self.event_id.strip()
            or not self.payload_hash.strip()
            or self.status not in {"current", "redacted"}
        ):
            raise ValueError("invalid current companion projection")
        if self.status == "redacted":
            if (
                self.content != COMPANION_REDACTION_TOMBSTONE
                or self.payload_hash != COMPANION_REDACTION_TOMBSTONE_HASH
                or not str(self.redaction_id or "").strip()
                or int(self.redaction_version or 0) < 1
                or not str(self.redacted_from_payload_hash or "").strip()
            ):
                raise ValueError("invalid redacted current companion projection")
        elif any(
            value is not None
            for value in (
                self.redaction_id,
                self.redaction_version,
                self.redacted_from_payload_hash,
            )
        ):
            raise ValueError("current companion projection cannot carry redaction fields")


@dataclass(frozen=True, slots=True)
class OwnerMemoryReadScopeV1:
    profile_id: str
    profile_generation: int
    binding_epoch: int
    session_ids: tuple[str, ...]
    session_set_version: int
    session_set_hash: str
    as_of_message_id: int
    scope_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        session_ids = tuple(sorted(set(str(item).strip() for item in self.session_ids)))
        if (
            self.schema_version != 1
            or not self.profile_id.strip()
            or self.profile_generation < 1
            or self.binding_epoch < 1
            or self.session_set_version < 1
            or self.as_of_message_id < 0
            or any(not item for item in session_ids)
            or session_ids != self.session_ids
        ):
            raise ValueError("invalid owner memory read scope")
        expected_session_hash = canonical_hash(
            {
                "profile_id": self.profile_id,
                "profile_generation": self.profile_generation,
                "binding_epoch": self.binding_epoch,
                "session_set_version": self.session_set_version,
                "session_ids": list(session_ids),
            }
        )
        expected_scope_hash = canonical_hash(
            {
                "schema_version": 1,
                "profile_id": self.profile_id,
                "profile_generation": self.profile_generation,
                "binding_epoch": self.binding_epoch,
                "session_set_version": self.session_set_version,
                "session_set_hash": expected_session_hash,
                "as_of_message_id": self.as_of_message_id,
            }
        )
        if (
            self.session_set_hash != expected_session_hash
            or self.scope_hash != expected_scope_hash
        ):
            raise ValueError("owner memory read scope hash mismatch")

    @property
    def scope_ref(self) -> str:
        return f"owner-memory-scope-v1:{self.scope_hash}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_generation": self.profile_generation,
            "binding_epoch": self.binding_epoch,
            "session_ids": list(self.session_ids),
            "session_set_version": self.session_set_version,
            "session_set_hash": self.session_set_hash,
            "as_of_message_id": self.as_of_message_id,
            "scope_hash": self.scope_hash,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> OwnerMemoryReadScopeV1:
        return cls(
            profile_id=str(value["profile_id"]),
            profile_generation=int(value["profile_generation"]),
            binding_epoch=int(value["binding_epoch"]),
            session_ids=tuple(str(item) for item in value["session_ids"]),
            session_set_version=int(value["session_set_version"]),
            session_set_hash=str(value["session_set_hash"]),
            as_of_message_id=int(value["as_of_message_id"]),
            scope_hash=str(value["scope_hash"]),
            schema_version=int(value.get("schema_version", 1)),
        )


_MESSAGES_V21_DDL = """
CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    created_at       REAL    NOT NULL,
    embedding        BLOB,
    salience         REAL    DEFAULT 0.5,
    decay_last_touch REAL,
    user_emotion     TEXT,
    audio_file_path  TEXT,
    tool_call_id     TEXT,
    tool_calls       TEXT,
    is_summary       INTEGER DEFAULT 0,
    summary_of       TEXT,
    reasoning_content TEXT,
    workflow_event_id TEXT,
    projection_kind TEXT NOT NULL DEFAULT 'legacy_message'
      CHECK(projection_kind IN (
        'legacy_message','user_message','assistant_message','tool_message','system_message',
        'final_assistant','workflow_progress','workflow_accepted','workflow_decision',
        'workflow_final_status','artifact_card','companion_event'
      )),
    context_visibility TEXT NOT NULL DEFAULT 'conversation'
      CHECK(context_visibility IN ('conversation','exclude')),
    root_run_id TEXT,
    task_scope_id TEXT,
    projection_event_id TEXT,
    projection_owner_kind TEXT
      CHECK(projection_owner_kind IS NULL OR projection_owner_kind='companion_profile'),
    projection_owner_id TEXT,
    projection_owner_generation INTEGER,
    projection_epoch INTEGER,
    projection_route_version INTEGER,
    projection_payload_hash TEXT,
    CHECK(
      (projection_owner_kind IS NULL
       AND projection_owner_id IS NULL
       AND projection_owner_generation IS NULL
       AND projection_epoch IS NULL
       AND projection_route_version IS NULL)
      OR
      (projection_owner_kind IS NOT NULL
       AND projection_owner_kind='companion_profile'
       AND projection_owner_id IS NOT NULL
       AND projection_owner_generation IS NOT NULL
       AND projection_owner_generation>=1
       AND projection_epoch IS NOT NULL
       AND projection_epoch>=0
       AND projection_route_version IS NOT NULL
       AND projection_route_version>=1)
    ),
    CHECK(
      projection_kind<>'companion_event'
      OR (
        context_visibility='exclude'
        AND projection_event_id IS NOT NULL
        AND projection_owner_kind IS NOT NULL
        AND projection_owner_kind='companion_profile'
        AND projection_payload_hash IS NOT NULL
      )
    )
)
"""

_CONTROL_DDL = """
CREATE UNIQUE INDEX idx_messages_companion_projection_event
  ON messages(
    projection_owner_kind,projection_owner_id,projection_owner_generation,
    projection_event_id
  )
  WHERE projection_kind='companion_event';

CREATE TABLE companion_session_owners (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    owner_kind TEXT NOT NULL CHECK(owner_kind='companion_profile'),
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation>=1),
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch>=1),
    status TEXT NOT NULL CHECK(status IN ('active','tombstoned')),
    scope_version INTEGER NOT NULL DEFAULT 1 CHECK(scope_version>=1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id,profile_id,profile_generation)
);

CREATE TRIGGER companion_session_owner_identity_immutable
BEFORE UPDATE OF owner_kind,profile_id,profile_generation,binding_epoch
ON companion_session_owners
BEGIN
  SELECT RAISE(ABORT,'companion_session_owner_rebind_forbidden');
END;

CREATE TRIGGER companion_session_owner_no_reactivate
BEFORE UPDATE OF status ON companion_session_owners
WHEN OLD.status='tombstoned' AND NEW.status<>'tombstoned'
BEGIN
  SELECT RAISE(ABORT,'companion_session_owner_reactivate_forbidden');
END;

CREATE TABLE companion_owner_scope_versions (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation>=1),
    scope_version INTEGER NOT NULL CHECK(scope_version>=1),
    updated_at REAL NOT NULL,
    PRIMARY KEY(profile_id,profile_generation)
);

CREATE TABLE companion_ingress_outbox (
    outbox_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES companion_session_owners(session_id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    owner_kind TEXT NOT NULL CHECK(owner_kind='companion_profile'),
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation>=1),
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch>=1),
    request_id TEXT NOT NULL,
    run_id TEXT,
    turn_id TEXT NOT NULL,
    retry_of_request_id TEXT,
    retry_of_run_id TEXT,
    retry_of_turn_id TEXT,
    retry_of_message_id INTEGER,
    event_ref TEXT NOT NULL,
    event_envelope_json TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('normal','blocking')),
    status TEXT NOT NULL CHECK(status IN ('pending','claimed','delivered','dead_letter')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
    lease_expires_at REAL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    next_retry_at REAL,
    delivered_hash TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(profile_id,profile_generation,request_id,turn_id),
    UNIQUE(session_id,message_id),
    FOREIGN KEY(session_id,profile_id,profile_generation)
      REFERENCES companion_session_owners(session_id,profile_id,profile_generation)
);
CREATE INDEX idx_companion_ingress_claim
  ON companion_ingress_outbox(status,next_retry_at,lease_expires_at,created_at);

CREATE TABLE companion_projection_routes (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation>=1),
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch>=1),
    target_session_id TEXT NOT NULL,
    target_epoch INTEGER NOT NULL CHECK(target_epoch>=0),
    route_version INTEGER NOT NULL CHECK(route_version>=1),
    status TEXT NOT NULL CHECK(status IN ('active','tombstoned')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(profile_id,profile_generation),
    FOREIGN KEY(target_session_id,profile_id,profile_generation)
      REFERENCES companion_session_owners(session_id,profile_id,profile_generation)
);

CREATE TABLE companion_projection_route_outbox (
    outbox_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation>=1),
    route_version INTEGER NOT NULL CHECK(route_version>=1),
    event_kind TEXT NOT NULL CHECK(event_kind IN ('route_changed','session_tombstoned')),
    target_session_id TEXT NOT NULL,
    target_epoch INTEGER NOT NULL CHECK(target_epoch>=0),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','claimed','delivered','dead_letter')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
    lease_expires_at REAL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    next_retry_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(profile_id,profile_generation,route_version,event_kind),
    FOREIGN KEY(profile_id,profile_generation)
      REFERENCES companion_projection_routes(profile_id,profile_generation)
);

CREATE TABLE companion_projection_redaction_receipts (
    projection_owner_id TEXT NOT NULL,
    projection_owner_generation INTEGER NOT NULL CHECK(projection_owner_generation>=1),
    projection_event_id TEXT NOT NULL,
    redaction_version INTEGER NOT NULL CHECK(redaction_version>=1),
    expected_old_payload_hash TEXT NOT NULL,
    new_payload_hash TEXT NOT NULL,
    redaction_outbox_id TEXT NOT NULL UNIQUE,
    applied_at REAL NOT NULL,
    PRIMARY KEY(
      projection_owner_id,projection_owner_generation,projection_event_id,redaction_version
    )
);
"""


async def _fetchall(
    db: aiosqlite.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[tuple[Any, ...]]:
    cursor = await db.execute(sql, parameters)
    rows = await cursor.fetchall()
    await cursor.close()
    return [tuple(row) for row in rows]


async def _scalar(
    db: aiosqlite.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> Any:
    cursor = await db.execute(sql, parameters)
    row = await cursor.fetchone()
    await cursor.close()
    return None if row is None else row[0]


async def _execute_script(db: aiosqlite.Connection, sql: str) -> None:
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = pending.strip()
        pending = ""
        if statement:
            await db.execute(statement)
    if pending.strip():
        raise sqlite3.OperationalError("incomplete companion projection DDL")


async def migrate_companion_message_projection(db: aiosqlite.Connection) -> None:
    """Rebuild ``messages`` from v20 to v21 inside the caller transaction."""

    old_columns = [
        str(row[1]) for row in await _fetchall(db, "PRAGMA table_info(messages)")
    ]
    expected_columns = [
        "id",
        "session_id",
        "role",
        "content",
        "created_at",
        "embedding",
        "salience",
        "decay_last_touch",
        "user_emotion",
        "audio_file_path",
        "tool_call_id",
        "tool_calls",
        "is_summary",
        "summary_of",
        "reasoning_content",
        "workflow_event_id",
        "projection_kind",
        "context_visibility",
        "root_run_id",
        "task_scope_id",
    ]
    if old_columns != expected_columns:
        raise RuntimeError(
            f"companion_projection_unexpected_messages_columns:{old_columns!r}"
        )
    old_count = int(await _scalar(db, "SELECT count(*) FROM messages") or 0)
    old_ids = await _fetchall(db, "SELECT id FROM messages ORDER BY id")
    old_sequence = int(
        await _scalar(
            db, "SELECT seq FROM sqlite_sequence WHERE name='messages'"
        )
        or 0
    )
    old_indexes = [
        (str(row[0]), str(row[1]))
        for row in await _fetchall(
            db,
            """SELECT name,sql FROM sqlite_master
               WHERE type='index' AND tbl_name='messages' AND sql IS NOT NULL
               ORDER BY name""",
        )
    ]
    old_triggers = [
        (str(row[0]), str(row[1]))
        for row in await _fetchall(
            db,
            """SELECT name,sql FROM sqlite_master
               WHERE type='trigger' AND tbl_name='messages' AND sql IS NOT NULL
               ORDER BY name""",
        )
    ]
    fts_sql = await _scalar(
        db,
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'",
    )
    vector_ids: list[tuple[Any, ...]] | None = None
    if await _scalar(
        db,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_vec'",
    ):
        try:
            vector_ids = await _fetchall(
                db, "SELECT message_id FROM messages_vec ORDER BY message_id"
            )
        except (sqlite3.Error, aiosqlite.Error):
            # sqlite-vec is loaded later by SessionDB. The virtual table and
            # its shadows are deliberately untouched; preserved message ids
            # are the durable mapping proof in this degraded migration path.
            vector_ids = None

    for trigger_name, _ in old_triggers:
        await db.execute(f'DROP TRIGGER "{trigger_name.replace(chr(34), chr(34) * 2)}"')
    if fts_sql is not None:
        await db.execute("DROP TABLE messages_fts")
    await db.execute("ALTER TABLE messages RENAME TO messages_v20")
    await db.execute(_MESSAGES_V21_DDL)
    quoted = ",".join(f'"{name}"' for name in expected_columns)
    await db.execute(
        f"""INSERT INTO messages(
              {quoted},projection_event_id,projection_owner_kind,projection_owner_id,
              projection_owner_generation,projection_epoch,projection_route_version,
              projection_payload_hash
            )
            SELECT {quoted},workflow_event_id,NULL,NULL,NULL,NULL,NULL,NULL
            FROM messages_v20"""
    )
    await db.execute("DROP TABLE messages_v20")
    for _, index_sql in old_indexes:
        await db.execute(index_sql)
    await _execute_script(db, _CONTROL_DDL)
    if fts_sql is not None:
        await db.execute(str(fts_sql))
        await db.execute(
            """INSERT INTO messages_fts(rowid,content)
               SELECT id,content FROM messages
               WHERE context_visibility='conversation'"""
        )
    for _, trigger_sql in old_triggers:
        await db.execute(trigger_sql)

    new_count = int(await _scalar(db, "SELECT count(*) FROM messages") or 0)
    new_ids = await _fetchall(db, "SELECT id FROM messages ORDER BY id")
    if new_count != old_count or new_ids != old_ids:
        raise RuntimeError("companion_projection_messages_copy_mismatch")
    max_id = int(await _scalar(db, "SELECT COALESCE(MAX(id),0) FROM messages") or 0)
    restored_sequence = max(old_sequence, max_id)
    await db.execute("DELETE FROM sqlite_sequence WHERE name='messages'")
    await db.execute(
        "INSERT INTO sqlite_sequence(name,seq) VALUES ('messages',?)",
        (restored_sequence,),
    )
    sequence = int(
        await _scalar(db, "SELECT seq FROM sqlite_sequence WHERE name='messages'")
        or 0
    )
    if sequence < old_sequence or sequence < max_id:
        raise RuntimeError("companion_projection_sequence_regressed")
    if vector_ids is not None:
        new_vector_ids = await _fetchall(
            db, "SELECT message_id FROM messages_vec ORDER BY message_id"
        )
        if new_vector_ids != vector_ids:
            raise RuntimeError("companion_projection_vector_mapping_changed")
    if fts_sql is not None:
        fts_ids = await _fetchall(
            db,
            """SELECT rowid FROM messages_fts
               WHERE rowid IN (
                 SELECT id FROM messages WHERE context_visibility='conversation'
               )
               ORDER BY rowid""",
        )
        visible_ids = await _fetchall(
            db,
            """SELECT id FROM messages
               WHERE context_visibility='conversation' ORDER BY id""",
        )
        if fts_ids != visible_ids:
            raise RuntimeError("companion_projection_fts_mapping_changed")
    fk_errors = await _fetchall(db, "PRAGMA foreign_key_check")
    if fk_errors:
        raise RuntimeError(f"companion_projection_foreign_key_error:{fk_errors!r}")
