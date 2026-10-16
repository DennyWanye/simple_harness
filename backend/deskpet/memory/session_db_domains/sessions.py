# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Sessions, primary conversation, owner bindings and session titles."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import Any

import aiosqlite

from deskpet.companion.companion_message_projection import (
    OwnerMemoryReadScopeV1,
    TrustedCompanionOwner,
)
from deskpet.companion.companion_message_projection import (
    canonical_hash as companion_canonical_hash,
)
from deskpet.memory.primary_authority import (
    assert_not_primary_authority,
    assert_not_primary_authority_tx,
)

from .core import SessionDBCore
from .support import DEFAULT_MEMORY_USER_ID, _coerce_trusted_owner, log


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SessionMixin(SessionDBCore):
    """Sessions, primary conversation, owner bindings and session titles."""

    __slots__ = ()

    async def initialize_primary_conversation(self, subject: str):
        """Create/replay the subject's sole writable primary conversation.

        This entry does not initialize or inspect legacy Session state.  A v34
        or older database is rejected by the fresh-only epoch guard.
        """

        return await self._human_memory_program_store().initialize_subject(subject)

    async def open_primary_conversation(
        self,
        subject: str,
        *,
        requested_conversation_id: str | None = None,
    ):
        """Open only the canonical primary ID; legacy Session IDs fail closed."""

        return await self._human_memory_program_store().open_primary_conversation(
            subject,
            requested_conversation_id=requested_conversation_id,
        )

    async def append_primary_evidence(self, envelope: Any, receipt: Any):
        """Append evidence only after an accepted, hash-bound S1 receipt."""

        return await self._human_memory_program_store().append_evidence(envelope, receipt)

    async def list_primary_evidence(self, subject: str, *, limit: int = 1000):
        return await self._human_memory_program_store().list_evidence(subject, limit=limit)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        """新建会话，返回 UUID 字符串。"""
        session_id = str(uuid.uuid4())

        await assert_not_primary_authority(self._db_path, session_id)
        if not self._initialized:
            await self.initialize()
        await self.ensure_session(session_id, metadata)
        return session_id

    async def ensure_session(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        memory_user_id: str = DEFAULT_MEMORY_USER_ID,
    ) -> str:
        """Ensure a session row exists and return the normalized id.

        New companion task sessions already receive their UUID before the
        first message is written so frontend, receipts, and messages can all
        share one stable id. This method persists that caller-supplied id in
        the canonical ``sessions`` table without changing existing metadata.
        """
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id must be non-empty")

        await assert_not_primary_authority(self._db_path, sid)
        if not self._initialized:
            await self.initialize()
        meta_json = json.dumps(metadata) if metadata else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await assert_not_primary_authority_tx(db, (sid,))
                    await db.execute(
                        "INSERT INTO sessions(id, created_at, metadata) "
                        "VALUES (?, ?, ?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (sid, time.time(), meta_json),
                    )
                    await self._ensure_memory_binding_in_transaction(
                        db, session_id=sid, user_id=memory_user_id
                    )
                    await db.execute(
                        "INSERT INTO session_delivery_state("
                        "session_id, epoch, deleted_at, reason) "
                        "VALUES (?, 0, NULL, NULL) "
                        "ON CONFLICT(session_id) DO UPDATE SET "
                        "deleted_at=NULL, reason=NULL",
                        (sid,),
                    )
                    await db.commit()

        await self._with_retry(_do)
        return sid

    @staticmethod
    async def _ensure_memory_binding_in_transaction(
        db: aiosqlite.Connection,
        *,
        session_id: str,
        user_id: str,
    ) -> None:
        sid = str(session_id or "").strip()
        uid = str(user_id or "").strip()
        if not sid or not uid:
            raise ValueError("session_id and user_id are required")
        now = time.time()
        await db.execute(
            "INSERT INTO sessions(id,created_at,metadata) VALUES (?,?,NULL) "
            "ON CONFLICT(id) DO NOTHING",
            (sid, now),
        )
        await db.execute(
            "INSERT INTO memory_users(user_id,created_at) VALUES (?,?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (uid, now),
        )
        cursor = await db.execute(
            "SELECT user_id FROM memory_user_bindings WHERE session_id=?",
            (sid,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is not None and str(row[0]) != uid:
            raise RuntimeError("memory_session_user_conflict")
        await db.execute(
            "INSERT INTO memory_user_bindings(session_id,user_id,created_at) "
            "VALUES (?,?,?) ON CONFLICT(session_id) DO NOTHING",
            (sid, uid, now),
        )

    async def ensure_memory_user_binding(
        self,
        session_id: str,
        *,
        user_id: str = DEFAULT_MEMORY_USER_ID,
    ) -> str:
        """Create or verify the immutable product Memory owner for a session."""

        if not self._initialized:
            await self.initialize()

        async def _do() -> str:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("BEGIN IMMEDIATE")
                    await self._ensure_memory_binding_in_transaction(
                        db, session_id=session_id, user_id=user_id
                    )
                    await db.commit()
                    return str(user_id)

        return await self._with_retry(_do)

    async def memory_user_for_session(self, session_id: str) -> str:
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT user_id FROM memory_user_bindings WHERE session_id=?",
                (str(session_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise RuntimeError("memory_session_user_unbound")
        return str(row[0])

    async def bind_session_owner_if_absent(
        self,
        session_id: str,
        trusted_owner: TrustedCompanionOwner | dict[str, Any] | Any,
    ) -> dict[str, Any]:
        """Bind a new, empty session to one exact trusted profile generation."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id must be non-empty")
        owner = _coerce_trusted_owner(trusted_owner)
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT owner_kind,profile_id,profile_generation,binding_epoch,
                                  status,scope_version,created_at,updated_at
                           FROM companion_session_owners WHERE session_id=?""",
                        (sid,),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is not None:
                        # 属主一经绑定即不可改绑：身份本体或纪元任一不符都硬拒。
                        # （2026-08-09 前对保留会话 `default` 有两处自愈例外——
                        # 纪元接管与墓碑复活。保留会话已移除，会话一律新建，
                        # 不再需要"永远可复活"的兜底目标，例外随之删除。）
                        if (
                            str(row[0]) != owner.owner_kind
                            or str(row[1]) != owner.profile_id
                            or int(row[2]) != owner.profile_generation
                            or int(row[3]) != owner.binding_epoch
                        ):
                            await db.rollback()
                            raise RuntimeError("companion_session_owner_rebind_forbidden")
                        if str(row[4]) != "active":
                            await db.rollback()
                            raise RuntimeError("companion_session_owner_tombstoned")
                        # 参数完全一致的幂等重放：返回既有行。
                        await db.commit()
                        return {
                            "session_id": sid,
                            "owner_kind": str(row[0]),
                            "profile_id": str(row[1]),
                            "profile_generation": int(row[2]),
                            "binding_epoch": int(row[3]),
                            "status": str(row[4]),
                            "scope_version": int(row[5]),
                            "created_at": float(row[6]),
                            "updated_at": float(row[7]),
                        }
                    cursor = await db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,))
                    session_exists = await cursor.fetchone()
                    await cursor.close()
                    if session_exists is None:
                        await db.rollback()
                        raise RuntimeError("companion_session_missing")
                    cursor = await db.execute(
                        "SELECT count(*) FROM messages WHERE session_id=?", (sid,)
                    )
                    message_count = int((await cursor.fetchone())[0])
                    await cursor.close()
                    if message_count:
                        await db.rollback()
                        raise RuntimeError("companion_legacy_session_cannot_be_claimed")
                    await db.execute(
                        """INSERT INTO companion_session_owners(
                             session_id,owner_kind,profile_id,profile_generation,binding_epoch,
                             status,scope_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,'active',1,?,?)""",
                        (
                            sid,
                            owner.owner_kind,
                            owner.profile_id,
                            owner.profile_generation,
                            owner.binding_epoch,
                            now,
                            now,
                        ),
                    )
                    await db.execute(
                        """INSERT INTO companion_owner_scope_versions(
                             profile_id,profile_generation,scope_version,updated_at
                           ) VALUES (?,?,1,?)
                           ON CONFLICT(profile_id,profile_generation) DO UPDATE SET
                             scope_version=companion_owner_scope_versions.scope_version+1,
                             updated_at=excluded.updated_at""",
                        (owner.profile_id, owner.profile_generation, now),
                    )
                    await db.commit()
                    return {
                        "session_id": sid,
                        "owner_kind": owner.owner_kind,
                        "profile_id": owner.profile_id,
                        "profile_generation": owner.profile_generation,
                        "binding_epoch": owner.binding_epoch,
                        "status": "active",
                        "scope_version": 1,
                        "created_at": now,
                        "updated_at": now,
                    }

        return await self._with_retry(_do)

    async def capture_owner_memory_read_scope(
        self,
        profile_id: str,
        profile_generation: int,
        binding_epoch: int,
    ) -> OwnerMemoryReadScopeV1:
        """Freeze exact readable sessions and message high-water mark."""

        if not self._initialized:
            await self.initialize()
        profile = str(profile_id or "").strip()
        generation = int(profile_generation)
        epoch = int(binding_epoch)
        if not profile or generation < 1 or epoch < 1:
            raise ValueError("invalid owner memory scope")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN")
            cursor = await db.execute(
                """SELECT o.session_id
                   FROM companion_session_owners o
                   LEFT JOIN session_delivery_state d ON d.session_id=o.session_id
                   WHERE o.profile_id=? AND o.profile_generation=? AND o.binding_epoch=?
                     AND o.status='active' AND d.deleted_at IS NULL
                   ORDER BY o.session_id""",
                (profile, generation, epoch),
            )
            session_ids = tuple(str(row[0]) for row in await cursor.fetchall())
            await cursor.close()
            cursor = await db.execute(
                """SELECT scope_version FROM companion_owner_scope_versions
                   WHERE profile_id=? AND profile_generation=?""",
                (profile, generation),
            )
            version_row = await cursor.fetchone()
            await cursor.close()
            if version_row is None:
                await db.rollback()
                raise RuntimeError("companion_owner_scope_missing")
            session_set_version = int(version_row[0])
            if session_ids:
                placeholders = ",".join("?" for _ in session_ids)
                cursor = await db.execute(
                    f"""SELECT COALESCE(MAX(id),0) FROM messages
                        WHERE session_id IN ({placeholders})""",
                    session_ids,
                )
                as_of_message_id = int((await cursor.fetchone())[0])
                await cursor.close()
            else:
                as_of_message_id = 0
            session_set_hash = companion_canonical_hash(
                {
                    "profile_id": profile,
                    "profile_generation": generation,
                    "binding_epoch": epoch,
                    "session_set_version": session_set_version,
                    "session_ids": list(session_ids),
                }
            )
            scope_hash = companion_canonical_hash(
                {
                    "schema_version": 1,
                    "profile_id": profile,
                    "profile_generation": generation,
                    "binding_epoch": epoch,
                    "session_set_version": session_set_version,
                    "session_set_hash": session_set_hash,
                    "as_of_message_id": as_of_message_id,
                }
            )
            await db.commit()
        return OwnerMemoryReadScopeV1(
            profile_id=profile,
            profile_generation=generation,
            binding_epoch=epoch,
            session_ids=session_ids,
            session_set_version=session_set_version,
            session_set_hash=session_set_hash,
            as_of_message_id=as_of_message_id,
            scope_hash=scope_hash,
        )

    async def _repair_excluded_memory_artifacts(self) -> None:
        """Remove derived memory artifacts for excluded projection rows."""

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute(
                """UPDATE messages SET embedding=NULL
                   WHERE context_visibility='exclude' AND embedding IS NOT NULL"""
            )
            cursor = await db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='messages_chunks'"""
            )
            has_chunks = await cursor.fetchone() is not None
            await cursor.close()
            if has_chunks:
                await db.execute(
                    """DELETE FROM messages_chunks
                       WHERE message_id IN (
                         SELECT id FROM messages
                         WHERE context_visibility='exclude'
                       )"""
                )
            try:
                await db.execute(
                    """DELETE FROM messages_vec
                       WHERE message_id IN (
                         SELECT id FROM messages
                         WHERE context_visibility='exclude'
                       )"""
                )
            except (sqlite3.Error, aiosqlite.Error) as exc:
                log.debug("excluded messages_vec repair skipped: %s", exc)
            await db.commit()

    async def recall_owner_messages_readonly(
        self,
        scope: OwnerMemoryReadScopeV1,
        query: str,
        limit: int,
        *,
        connection_observer: Callable[[sqlite3.Connection], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Query the exact frozen owner scope through a physically read-only DB."""

        if not self._initialized:
            await self.initialize()
        if not isinstance(scope, OwnerMemoryReadScopeV1):
            raise TypeError("owner memory read requires OwnerMemoryReadScopeV1")
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("owner memory read limit must be between 1 and 20")
        if not scope.session_ids or scope.as_of_message_id == 0:
            return []

        def _query() -> list[dict[str, Any]]:
            db_uri = self._db_path.resolve().as_uri() + "?mode=ro"
            db = sqlite3.connect(db_uri, uri=True, isolation_level=None, timeout=5.0)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA busy_timeout=5000")
                if connection_observer is not None:
                    connection_observer(db)
                placeholders = ",".join("?" for _ in scope.session_ids)
                owner_fence = f"""
                    JOIN companion_session_owners o ON o.session_id=m.session_id
                    LEFT JOIN session_delivery_state d ON d.session_id=m.session_id
                    WHERE o.owner_kind='companion_profile'
                      AND o.profile_id=? AND o.profile_generation=?
                      AND o.binding_epoch=? AND o.status='active'
                      AND d.deleted_at IS NULL
                      AND m.session_id IN ({placeholders})
                      AND m.id<=?
                      AND m.context_visibility='conversation'
                """
                fts_join = f"""
                    FROM messages_fts
                    JOIN messages m ON m.id=messages_fts.rowid
                    {owner_fence}
                """
                recent_join = f"""
                    FROM messages m
                    {owner_fence}
                """
                params: tuple[Any, ...] = (
                    scope.profile_id,
                    scope.profile_generation,
                    scope.binding_epoch,
                    *scope.session_ids,
                    scope.as_of_message_id,
                )
                phrase = '"' + normalized_query.replace('"', '""') + '"'
                try:
                    rows = db.execute(
                        f"""SELECT m.id,m.session_id,m.content,m.created_at,
                                   messages_fts.rank AS rank,
                                   'readonly_fts' AS source
                            {fts_join}
                              AND messages_fts MATCH ?
                            ORDER BY rank,m.id DESC LIMIT ?""",
                        (*params, phrase, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = db.execute(
                        f"""SELECT m.id,m.session_id,m.content,m.created_at,
                                   0.0 AS rank,
                                   'readonly_like' AS source
                            {fts_join}
                              AND m.content LIKE ? ESCAPE '\\'
                            ORDER BY m.id DESC LIMIT ?""",
                        (*params, f"%{_escape_like(normalized_query)}%", limit),
                    ).fetchall()
                # The Skill query is often an intent phrase ("今天聊过的重点")
                # rather than a literal substring from the conversation.  A
                # physically read-only recall must still provide grounded
                # evidence when lexical FTS has zero overlap.  Degrade to the
                # newest user-authored messages inside the *same frozen owner
                # scope*; never widen owner/session/as-of/deletion fences.
                if not rows:
                    rows = db.execute(
                        f"""SELECT m.id,m.session_id,m.content,m.created_at,
                                   1000000.0 AS rank,
                                   'readonly_recency_fallback' AS source
                            {recent_join}
                              AND m.role='user' AND trim(m.content)<>''
                            ORDER BY m.id DESC LIMIT ?""",
                        (*params, limit),
                    ).fetchall()
                return [dict(row) for row in rows]
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def set_session_title(self, session_id: str, title: str) -> str:
        """Set or clear a user custom title for a companion session.

        - Empty/whitespace ``title`` clears the custom name (the list falls
          back to the auto preview). Idempotent.
        - Non-empty is trimmed + clamped to ``MAX_TITLE_LEN``.

        Returns the stored title ("" when cleared) so the caller can echo the
        canonical value back to the UI.
        """
        sid = (session_id or "").strip()
        if not sid:
            return ""

        await assert_not_primary_authority(self._db_path, sid)
        if not self._initialized:
            await self.initialize()
        clean = (title or "").strip()[: self.MAX_TITLE_LEN]

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await assert_not_primary_authority_tx(db, (sid,))
                    if clean:
                        await db.execute(
                            "INSERT INTO session_titles(session_id, title, updated_at) "
                            "VALUES(?, ?, ?) "
                            "ON CONFLICT(session_id) DO UPDATE SET "
                            "  title = excluded.title, updated_at = excluded.updated_at",
                            (sid, clean, time.time()),
                        )
                    else:
                        await db.execute("DELETE FROM session_titles WHERE session_id = ?", (sid,))
                    await db.execute(
                        "UPDATE project_session_catalog_state SET "
                        "catalog_revision=catalog_revision+1 WHERE singleton=1"
                    )
                    await db.commit()

        await self._with_retry(_do)
        return clean

    async def clear_all(self) -> int:
        """Delete all messages and return the number of removed rows."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute("DELETE FROM messages")
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    try:
                        await db.execute("DELETE FROM messages_vec")
                        await db.commit()
                    except Exception:
                        pass
                    return removed

        return await self._with_retry(_do)
