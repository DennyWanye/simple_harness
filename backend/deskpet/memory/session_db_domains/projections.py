# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Epoch-checked projections, relocation and redaction."""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from deskpet.companion.companion_message_projection import (
    COMPANION_REDACTION_TOMBSTONE,
    COMPANION_REDACTION_TOMBSTONE_HASH,
    CurrentCompanionProjection,
    TrustedCompanionOwner,
    TrustedCompanionProjectionRoute,
)

from .core import SessionDBCore
from .support import _coerce_trusted_owner, normalize_message_projection


class ProjectionMixin(SessionDBCore):
    """Epoch-checked projections, relocation and redaction."""

    __slots__ = ()

    async def append_companion_projection_if_epoch(
        self,
        session_id: str,
        content: str,
        *,
        trusted_owner: TrustedCompanionOwner | dict[str, Any] | Any,
        expected_epoch: int,
        route_version: int,
        projection_event_id: str,
        projection_payload_hash: str,
        role: str = "assistant",
    ) -> int | None:
        """Append one excluded Companion event under exact owner and route fences."""

        if not self._initialized:
            await self.initialize()
        owner = _coerce_trusted_owner(trusted_owner)
        sid = str(session_id or "").strip()
        event_id = str(projection_event_id or "").strip()
        payload_hash = str(projection_payload_hash or "").strip()
        if not sid or not event_id or not payload_hash:
            raise ValueError(
                "session_id, projection_event_id and projection_payload_hash are required"
            )

        async def _do() -> tuple[int | None, bool]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT o.owner_kind,o.profile_id,o.profile_generation,
                                  o.binding_epoch,o.status,d.epoch,d.deleted_at,
                                  r.target_session_id,r.target_epoch,r.route_version,r.status
                           FROM companion_session_owners o
                           LEFT JOIN session_delivery_state d
                             ON d.session_id=o.session_id
                           LEFT JOIN companion_projection_routes r
                             ON r.profile_id=o.profile_id
                            AND r.profile_generation=o.profile_generation
                           WHERE o.session_id=?""",
                        (sid,),
                    )
                    fence = await cursor.fetchone()
                    await cursor.close()
                    if (
                        fence is None
                        or str(fence[0]) != owner.owner_kind
                        or str(fence[1]) != owner.profile_id
                        or int(fence[2]) != owner.profile_generation
                        or int(fence[3]) != owner.binding_epoch
                        or str(fence[4]) != "active"
                        or int(fence[5] or 0) != int(expected_epoch)
                        or fence[6] is not None
                        or str(fence[7] or "") != sid
                        or fence[8] is None
                        or int(fence[8]) != int(expected_epoch)
                        or int(fence[9] or 0) != int(route_version)
                        or str(fence[10] or "") != "active"
                    ):
                        await db.rollback()
                        return None, False
                    msg_id, inserted = await self._insert_message_row(
                        db,
                        session_id=sid,
                        role=str(role),
                        content=content,
                        tool_call_id=None,
                        tool_calls_json=None,
                        reasoning=None,
                        workflow_event_id=None,
                        projection_kind="companion_event",
                        context_visibility="exclude",
                        root_run_id=None,
                        task_scope_id=None,
                        projection_event_id=event_id,
                        projection_owner_kind=owner.owner_kind,
                        projection_owner_id=owner.profile_id,
                        projection_owner_generation=owner.profile_generation,
                        projection_epoch=int(expected_epoch),
                        projection_route_version=int(route_version),
                        projection_payload_hash=payload_hash,
                    )
                    await db.commit()
                    return msg_id, inserted

        msg_id, _inserted = await self._with_retry(_do)
        return msg_id

    async def append_projection_if_epoch(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        expected_epoch: int,
        projection_event_id: str,
        projection_kind: str,
        context_visibility: str | None = None,
        trusted_owner: TrustedCompanionOwner | dict[str, Any] | Any | None = None,
        route_version: int | None = None,
        projection_payload_hash: str | None = None,
        workflow_event_id: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        root_run_id: str | None = None,
        task_scope_id: str | None = None,
    ) -> int | None:
        """Append any durable projection under its exact delivery fences."""

        projection, visibility = normalize_message_projection(
            role, projection_kind, context_visibility
        )
        event_id = str(projection_event_id or "").strip()
        if not event_id:
            raise ValueError("projection_event_id must be non-empty")
        if projection == "companion_event":
            if (
                trusted_owner is None
                or route_version is None
                or not str(projection_payload_hash or "").strip()
                or workflow_event_id is not None
            ):
                raise ValueError(
                    "companion projection requires trusted owner, route and payload hash"
                )
            return await self.append_companion_projection_if_epoch(
                session_id,
                content,
                trusted_owner=trusted_owner,
                expected_epoch=expected_epoch,
                route_version=int(route_version),
                projection_event_id=event_id,
                projection_payload_hash=str(projection_payload_hash),
                role=role,
            )
        if trusted_owner is not None or route_version is not None:
            raise ValueError("non-companion projection cannot carry companion fences")
        durable_workflow_event_id = str(workflow_event_id or event_id).strip()
        if durable_workflow_event_id != event_id:
            raise ValueError("workflow_event_id must equal projection_event_id")
        return await self.append_message_if_epoch(
            session_id,
            role,
            content,
            expected_epoch=expected_epoch,
            workflow_event_id=durable_workflow_event_id,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            skip_embed=visibility == "exclude",
            projection_kind=projection,
            context_visibility=visibility,
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
        )

    async def relocate_projection_if_epoch(
        self,
        *,
        trusted_owner: TrustedCompanionOwner,
        projection_event_id: str,
        expected_payload_hash: str,
        expected_session_id: str,
        expected_epoch: int,
        expected_route_version: int,
        new_session_id: str,
        new_epoch: int,
        new_route_version: int,
    ) -> dict[str, Any]:
        """Move one Companion projection in place under owner/hash/route CAS."""

        if not self._initialized:
            await self.initialize()
        if not isinstance(trusted_owner, TrustedCompanionOwner):
            raise TypeError("trusted_owner must be TrustedCompanionOwner")
        event_id = str(projection_event_id or "").strip()
        payload_hash = str(expected_payload_hash or "").strip()
        old_session = str(expected_session_id or "").strip()
        target_session = str(new_session_id or "").strip()
        if (
            not event_id
            or not payload_hash
            or not old_session
            or not target_session
            or int(expected_epoch) < 0
            or int(expected_route_version) < 1
            or int(new_epoch) < 0
            or int(new_route_version) <= int(expected_route_version)
        ):
            raise ValueError("invalid companion projection relocation")

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT * FROM messages
                           WHERE projection_kind='companion_event'
                             AND projection_owner_kind=?
                             AND projection_owner_id=?
                             AND projection_owner_generation=?
                             AND projection_event_id=?""",
                        (
                            trusted_owner.owner_kind,
                            trusted_owner.profile_id,
                            trusted_owner.profile_generation,
                            event_id,
                        ),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.rollback()
                        raise RuntimeError("companion_projection_missing")
                    if str(row["projection_payload_hash"]) != payload_hash:
                        await db.rollback()
                        raise RuntimeError("companion_projection_payload_hash_conflict")
                    if (
                        str(row["session_id"]) == target_session
                        and int(row["projection_epoch"]) == int(new_epoch)
                        and int(row["projection_route_version"]) == int(new_route_version)
                    ):
                        await db.commit()
                        return {"status": "already_relocated", **dict(row)}
                    if (
                        str(row["session_id"]) != old_session
                        or int(row["projection_epoch"]) != int(expected_epoch)
                        or int(row["projection_route_version"]) != int(expected_route_version)
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_projection_route_conflict")
                    cursor = await db.execute(
                        """SELECT o.owner_kind,o.profile_id,o.profile_generation,
                                  o.binding_epoch,o.status,d.epoch,d.deleted_at,
                                  r.target_session_id,r.target_epoch,r.route_version,r.status
                           FROM companion_session_owners o
                           JOIN session_delivery_state d ON d.session_id=o.session_id
                           JOIN companion_projection_routes r
                             ON r.profile_id=o.profile_id
                            AND r.profile_generation=o.profile_generation
                           WHERE o.session_id=?""",
                        (target_session,),
                    )
                    fence = await cursor.fetchone()
                    await cursor.close()
                    if (
                        fence is None
                        or str(fence[0]) != trusted_owner.owner_kind
                        or str(fence[1]) != trusted_owner.profile_id
                        or int(fence[2]) != trusted_owner.profile_generation
                        or int(fence[3]) != trusted_owner.binding_epoch
                        or str(fence[4]) != "active"
                        or int(fence[5]) != int(new_epoch)
                        or fence[6] is not None
                        or str(fence[7]) != target_session
                        or int(fence[8]) != int(new_epoch)
                        or int(fence[9]) != int(new_route_version)
                        or str(fence[10]) != "active"
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_projection_target_fence_mismatch")
                    cursor = await db.execute(
                        """UPDATE messages
                           SET session_id=?,projection_epoch=?,
                               projection_route_version=?
                           WHERE id=? AND session_id=? AND projection_epoch=?
                             AND projection_route_version=?
                             AND projection_payload_hash=?""",
                        (
                            target_session,
                            int(new_epoch),
                            int(new_route_version),
                            int(row["id"]),
                            old_session,
                            int(expected_epoch),
                            int(expected_route_version),
                            payload_hash,
                        ),
                    )
                    if int(cursor.rowcount or 0) != 1:
                        await cursor.close()
                        await db.rollback()
                        raise RuntimeError("companion_projection_relocation_lost_cas")
                    await cursor.close()
                    cursor = await db.execute(
                        "SELECT * FROM messages WHERE id=?", (int(row["id"]),)
                    )
                    moved = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert moved is not None
                    return {"status": "relocated", **dict(moved)}

        return await self._with_retry(_do)

    async def append_current_projection_if_absent(
        self,
        current_projection: CurrentCompanionProjection,
        trusted_route: TrustedCompanionProjectionRoute,
    ) -> dict[str, Any]:
        """Insert only a freshly verified current Companion notification."""

        if not self._initialized:
            await self.initialize()
        if not isinstance(current_projection, CurrentCompanionProjection):
            raise TypeError("current_projection must be CurrentCompanionProjection")
        if not isinstance(trusted_route, TrustedCompanionProjectionRoute):
            raise TypeError("trusted_route must be TrustedCompanionProjectionRoute")
        if current_projection.owner != trusted_route.owner:
            raise ValueError("current projection owner does not match trusted route")

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    owner = current_projection.owner
                    route = trusted_route
                    cursor = await db.execute(
                        """SELECT o.owner_kind,o.profile_id,o.profile_generation,
                                  o.binding_epoch,o.status,d.epoch,d.deleted_at,
                                  r.target_session_id,r.target_epoch,r.route_version,r.status
                           FROM companion_session_owners o
                           JOIN session_delivery_state d ON d.session_id=o.session_id
                           JOIN companion_projection_routes r
                             ON r.profile_id=o.profile_id
                            AND r.profile_generation=o.profile_generation
                           WHERE o.session_id=?""",
                        (route.session_id,),
                    )
                    fence = await cursor.fetchone()
                    await cursor.close()
                    if (
                        fence is None
                        or str(fence[0]) != owner.owner_kind
                        or str(fence[1]) != owner.profile_id
                        or int(fence[2]) != owner.profile_generation
                        or int(fence[3]) != owner.binding_epoch
                        or str(fence[4]) != "active"
                        or int(fence[5]) != route.projection_epoch
                        or fence[6] is not None
                        or str(fence[7]) != route.session_id
                        or int(fence[8]) != route.projection_epoch
                        or int(fence[9]) != route.route_version
                        or str(fence[10]) != "active"
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_projection_target_fence_mismatch")
                    cursor = await db.execute(
                        """SELECT * FROM messages
                           WHERE projection_kind='companion_event'
                             AND projection_owner_kind=?
                             AND projection_owner_id=?
                             AND projection_owner_generation=?
                             AND projection_event_id=?""",
                        (
                            owner.owner_kind,
                            owner.profile_id,
                            owner.profile_generation,
                            current_projection.event_id,
                        ),
                    )
                    existing = await cursor.fetchone()
                    await cursor.close()
                    if existing is not None:
                        await db.commit()
                        return {"status": "already_exists", **dict(existing)}
                    message_id, inserted = await self._insert_message_row(
                        db,
                        session_id=route.session_id,
                        role="assistant",
                        content=current_projection.content,
                        tool_call_id=None,
                        tool_calls_json=None,
                        reasoning=None,
                        workflow_event_id=None,
                        projection_kind="companion_event",
                        context_visibility="exclude",
                        root_run_id=None,
                        task_scope_id=None,
                        projection_event_id=current_projection.event_id,
                        projection_owner_kind=owner.owner_kind,
                        projection_owner_id=owner.profile_id,
                        projection_owner_generation=owner.profile_generation,
                        projection_epoch=route.projection_epoch,
                        projection_route_version=route.route_version,
                        projection_payload_hash=current_projection.payload_hash,
                    )
                    if not inserted:
                        await db.rollback()
                        raise RuntimeError("companion_projection_insert_lost_cas")
                    if current_projection.status == "redacted":
                        cursor = await db.execute(
                            """SELECT * FROM companion_projection_redaction_receipts
                               WHERE projection_owner_id=?
                                 AND projection_owner_generation=?
                                 AND projection_event_id=? AND redaction_version=?""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                current_projection.event_id,
                                current_projection.redaction_version,
                            ),
                        )
                        receipt = await cursor.fetchone()
                        await cursor.close()
                        if receipt is None:
                            await db.execute(
                                """INSERT INTO companion_projection_redaction_receipts(
                                     projection_owner_id,projection_owner_generation,
                                     projection_event_id,redaction_version,
                                     expected_old_payload_hash,new_payload_hash,
                                     redaction_outbox_id,applied_at
                                   ) VALUES (?,?,?,?,?,?,?,?)""",
                                (
                                    owner.profile_id,
                                    owner.profile_generation,
                                    current_projection.event_id,
                                    current_projection.redaction_version,
                                    current_projection.redacted_from_payload_hash,
                                    current_projection.payload_hash,
                                    current_projection.redaction_id,
                                    time.time(),
                                ),
                            )
                        elif (
                            str(receipt["expected_old_payload_hash"])
                            != current_projection.redacted_from_payload_hash
                            or str(receipt["new_payload_hash"]) != current_projection.payload_hash
                            or str(receipt["redaction_outbox_id"])
                            != current_projection.redaction_id
                        ):
                            await db.rollback()
                            raise RuntimeError("companion_projection_redaction_replay_conflict")
                    await db.commit()
                    return {"status": "inserted", "id": message_id}

        return await self._with_retry(_do)

    async def redact_projection_if_hash(
        self,
        *,
        owner: TrustedCompanionOwner,
        event_id: str,
        expected_payload_hash: str,
        tombstone_payload_hash: str,
        redaction_id: str,
        redaction_version: int,
    ) -> dict[str, Any]:
        """Authorize the sole content-changing transition for a projection."""

        if not isinstance(owner, TrustedCompanionOwner):
            raise TypeError("owner must be TrustedCompanionOwner")
        if str(tombstone_payload_hash) != COMPANION_REDACTION_TOMBSTONE_HASH:
            raise RuntimeError("companion_projection_tombstone_hash_conflict")
        return await self.record_companion_projection_redaction(
            projection_owner_id=owner.profile_id,
            projection_owner_generation=owner.profile_generation,
            projection_event_id=event_id,
            redaction_version=redaction_version,
            expected_old_payload_hash=expected_payload_hash,
            new_payload_hash=tombstone_payload_hash,
            redaction_outbox_id=redaction_id,
            redacted_content=COMPANION_REDACTION_TOMBSTONE,
        )

    async def record_companion_projection_redaction(
        self,
        *,
        projection_owner_id: str,
        projection_owner_generation: int,
        projection_event_id: str,
        redaction_version: int,
        expected_old_payload_hash: str,
        new_payload_hash: str,
        redaction_outbox_id: str,
        redacted_content: str,
    ) -> dict[str, Any]:
        """Apply or verify one exact redaction receipt and payload replacement."""

        if not self._initialized:
            await self.initialize()
        owner_id = str(projection_owner_id or "").strip()
        event_id = str(projection_event_id or "").strip()
        old_hash = str(expected_old_payload_hash or "").strip()
        replacement_hash = str(new_payload_hash or "").strip()
        outbox_id = str(redaction_outbox_id or "").strip()
        generation = int(projection_owner_generation)
        version = int(redaction_version)
        if (
            not owner_id
            or not event_id
            or not old_hash
            or not replacement_hash
            or not outbox_id
            or generation < 1
            or version < 1
        ):
            raise ValueError("invalid companion projection redaction")
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_redaction_receipts
                           WHERE projection_owner_id=?
                             AND projection_owner_generation=?
                             AND projection_event_id=?
                             AND redaction_version=?""",
                        (owner_id, generation, event_id, version),
                    )
                    receipt = await cursor.fetchone()
                    await cursor.close()
                    if receipt is not None:
                        expected = (
                            old_hash,
                            replacement_hash,
                            outbox_id,
                            redacted_content,
                        )
                        cursor = await db.execute(
                            """SELECT content FROM messages
                               WHERE projection_kind='companion_event'
                                 AND projection_owner_id=?
                                 AND projection_owner_generation=?
                                 AND projection_event_id=?""",
                            (owner_id, generation, event_id),
                        )
                        current = await cursor.fetchone()
                        await cursor.close()
                        actual = (
                            str(receipt["expected_old_payload_hash"]),
                            str(receipt["new_payload_hash"]),
                            str(receipt["redaction_outbox_id"]),
                            None if current is None else str(current[0]),
                        )
                        if actual != expected:
                            await db.rollback()
                            raise RuntimeError("companion_projection_redaction_replay_conflict")
                        await db.commit()
                        return dict(receipt)
                    cursor = await db.execute(
                        """SELECT id,projection_payload_hash FROM messages
                           WHERE projection_kind='companion_event'
                             AND projection_owner_id=?
                             AND projection_owner_generation=?
                             AND projection_event_id=?""",
                        (owner_id, generation, event_id),
                    )
                    projection = await cursor.fetchone()
                    await cursor.close()
                    if projection is None:
                        await db.rollback()
                        raise RuntimeError("companion_projection_redaction_target_missing")
                    if str(projection["projection_payload_hash"]) != old_hash:
                        await db.rollback()
                        raise RuntimeError("companion_projection_redaction_hash_conflict")
                    await db.execute(
                        """UPDATE messages SET content=?,projection_payload_hash=?
                           WHERE id=? AND projection_payload_hash=?""",
                        (redacted_content, replacement_hash, projection["id"], old_hash),
                    )
                    await db.execute(
                        """INSERT INTO companion_projection_redaction_receipts(
                             projection_owner_id,projection_owner_generation,
                             projection_event_id,redaction_version,
                             expected_old_payload_hash,new_payload_hash,
                             redaction_outbox_id,applied_at
                           ) VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            owner_id,
                            generation,
                            event_id,
                            version,
                            old_hash,
                            replacement_hash,
                            outbox_id,
                            now,
                        ),
                    )
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_redaction_receipts
                           WHERE projection_owner_id=?
                             AND projection_owner_generation=?
                             AND projection_event_id=?
                             AND redaction_version=?""",
                        (owner_id, generation, event_id, version),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert row is not None
                    return dict(row)

        return await self._with_retry(_do)
