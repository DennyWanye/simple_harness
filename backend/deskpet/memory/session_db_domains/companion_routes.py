# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Companion default routes and the projection route outbox."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from deskpet.companion.companion_message_projection import TrustedCompanionOwner
from deskpet.companion.companion_message_projection import (
    canonical_hash as companion_canonical_hash,
)
from deskpet.companion.companion_message_projection import (
    canonical_json as companion_canonical_json,
)

from .core import SessionDBCore


class CompanionRouteMixin(SessionDBCore):
    """Companion default routes and the projection route outbox."""

    __slots__ = ()

    async def set_companion_default_route(
        self,
        profile_id: str,
        profile_generation: int,
        binding_epoch: int,
        session_id: str,
        *,
        expected_route_version: int | None = None,
    ) -> dict[str, Any]:
        """CAS the current profile inbox route and append a wake outbox row."""

        if not self._initialized:
            await self.initialize()
        profile = str(profile_id)
        generation = int(profile_generation)
        epoch = int(binding_epoch)
        sid = str(session_id)
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT o.status,o.binding_epoch,d.epoch,d.deleted_at
                           FROM companion_session_owners o
                           LEFT JOIN session_delivery_state d ON d.session_id=o.session_id
                           WHERE o.session_id=? AND o.profile_id=? AND o.profile_generation=?""",
                        (sid, profile, generation),
                    )
                    target = await cursor.fetchone()
                    await cursor.close()
                    if (
                        target is None
                        or target["status"] != "active"
                        or int(target["binding_epoch"]) != epoch
                        or target["deleted_at"] is not None
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_route_target_fence_mismatch")
                    target_epoch = int(target["epoch"] or 0)
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_routes
                           WHERE profile_id=? AND profile_generation=?""",
                        (profile, generation),
                    )
                    existing = await cursor.fetchone()
                    await cursor.close()
                    current_version = int(existing["route_version"]) if existing else 0
                    if (
                        existing is not None
                        and existing["status"] == "active"
                        and existing["target_session_id"] == sid
                        and int(existing["target_epoch"]) == target_epoch
                        and int(existing["binding_epoch"]) == epoch
                    ):
                        await db.commit()
                        return dict(existing)
                    if (
                        expected_route_version is not None
                        and int(expected_route_version) != current_version
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_route_version_conflict")
                    route_version = current_version + 1
                    await db.execute(
                        """INSERT INTO companion_projection_routes(
                             profile_id,profile_generation,binding_epoch,target_session_id,
                             target_epoch,route_version,status,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,'active',?,?)
                           ON CONFLICT(profile_id,profile_generation) DO UPDATE SET
                             binding_epoch=excluded.binding_epoch,
                             target_session_id=excluded.target_session_id,
                             target_epoch=excluded.target_epoch,
                             route_version=excluded.route_version,
                             status='active',updated_at=excluded.updated_at""",
                        (
                            profile,
                            generation,
                            epoch,
                            sid,
                            target_epoch,
                            route_version,
                            now,
                            now,
                        ),
                    )
                    await self._insert_companion_route_outbox(
                        db,
                        profile_id=profile,
                        profile_generation=generation,
                        route_version=route_version,
                        event_kind="route_changed",
                        target_session_id=sid,
                        target_epoch=target_epoch,
                        now=now,
                    )
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_routes
                           WHERE profile_id=? AND profile_generation=?""",
                        (profile, generation),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert row is not None
                    return dict(row)

        return await self._with_retry(_do)

    async def get_companion_projection_route(
        self,
        profile_id: str,
        profile_generation: int,
        binding_epoch: int,
    ) -> dict[str, Any] | None:
        """Read the current active inbox route under the trusted identity fence."""

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM companion_projection_routes
                   WHERE profile_id=? AND profile_generation=? AND binding_epoch=?
                     AND status='active'""",
                (str(profile_id), int(profile_generation), int(binding_epoch)),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else dict(row)

    async def get_companion_projection(
        self,
        trusted_owner: TrustedCompanionOwner,
        projection_event_id: str,
    ) -> dict[str, Any] | None:
        """Read one excluded projection by its immutable owner/event identity."""

        if not self._initialized:
            await self.initialize()
        if not isinstance(trusted_owner, TrustedCompanionOwner):
            raise TypeError("trusted_owner must be TrustedCompanionOwner")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
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
                    str(projection_event_id),
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else dict(row)

    @staticmethod
    async def _insert_companion_route_outbox(
        db: aiosqlite.Connection,
        *,
        profile_id: str,
        profile_generation: int,
        route_version: int,
        event_kind: str,
        target_session_id: str,
        target_epoch: int,
        now: float,
    ) -> None:
        payload = {
            "schema_version": 1,
            "profile_id": profile_id,
            "profile_generation": profile_generation,
            "route_version": route_version,
            "event_kind": event_kind,
            "target_session_id": target_session_id,
            "target_epoch": target_epoch,
        }
        payload_json = companion_canonical_json(payload)
        payload_hash = companion_canonical_hash(payload)
        outbox_id = companion_canonical_hash(
            [
                "companion_projection_route",
                profile_id,
                profile_generation,
                route_version,
                event_kind,
            ]
        )
        await db.execute(
            """INSERT INTO companion_projection_route_outbox(
                 outbox_id,profile_id,profile_generation,route_version,event_kind,
                 target_session_id,target_epoch,payload_json,payload_hash,status,
                 claim_epoch,attempt,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,'pending',0,0,?,?)""",
            (
                outbox_id,
                profile_id,
                profile_generation,
                route_version,
                event_kind,
                target_session_id,
                target_epoch,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )

    async def claim_companion_projection_route_outbox(
        self,
        claim_owner: str,
        lease_seconds: float,
        *,
        profile_id: str | None = None,
        profile_generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Claim one route-change wakeup for cross-database reconciliation."""

        if not self._initialized:
            await self.initialize()
        worker = str(claim_owner or "").strip()
        if not worker or float(lease_seconds) <= 0:
            raise ValueError("claim owner and positive lease are required")
        now = time.time()

        async def _do() -> dict[str, Any] | None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    where = [
                        """((status='pending' AND
                              (next_retry_at IS NULL OR next_retry_at<=?))
                            OR (status='claimed' AND lease_expires_at<=?))"""
                    ]
                    params: list[Any] = [now, now]
                    if profile_id is not None:
                        where.append("profile_id=?")
                        params.append(str(profile_id))
                    if profile_generation is not None:
                        where.append("profile_generation=?")
                        params.append(int(profile_generation))
                    cursor = await db.execute(
                        f"""SELECT * FROM companion_projection_route_outbox
                            WHERE {" AND ".join(where)}
                            ORDER BY created_at,outbox_id LIMIT 1""",
                        params,
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.commit()
                        return None
                    claim_epoch = int(row["claim_epoch"]) + 1
                    attempt = int(row["attempt"]) + 1
                    await db.execute(
                        """UPDATE companion_projection_route_outbox
                           SET status='claimed',claim_owner=?,claim_epoch=?,
                               lease_expires_at=?,attempt=?,updated_at=?
                           WHERE outbox_id=? AND claim_epoch=?""",
                        (
                            worker,
                            claim_epoch,
                            now + float(lease_seconds),
                            attempt,
                            now,
                            row["outbox_id"],
                            row["claim_epoch"],
                        ),
                    )
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_route_outbox
                           WHERE outbox_id=?""",
                        (row["outbox_id"],),
                    )
                    claimed = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert claimed is not None
                    result = dict(claimed)
                    result["payload"] = json.loads(str(result["payload_json"]))
                    return result

        return await self._with_retry(_do)

    async def settle_companion_projection_route_outbox(
        self,
        outbox_id: str,
        expected_attempt: int,
        *,
        delivered: bool = False,
        retry_at: float | None = None,
    ) -> dict[str, Any]:
        """Settle one exact route reconciliation attempt."""

        if not self._initialized:
            await self.initialize()
        if delivered == (retry_at is not None):
            raise ValueError("settle requires exactly one of delivered or retry_at")
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_route_outbox
                           WHERE outbox_id=?""",
                        (str(outbox_id),),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.rollback()
                        raise RuntimeError("companion_route_outbox_missing")
                    if row["status"] == "delivered":
                        if not delivered or int(row["attempt"]) != int(expected_attempt):
                            await db.rollback()
                            raise RuntimeError("companion_route_outbox_attempt_stale")
                        await db.commit()
                        return dict(row)
                    if row["status"] != "claimed" or int(row["attempt"]) != int(expected_attempt):
                        await db.rollback()
                        raise RuntimeError("companion_route_outbox_attempt_stale")
                    status = "delivered" if delivered else "pending"
                    await db.execute(
                        """UPDATE companion_projection_route_outbox
                           SET status=?,next_retry_at=?,lease_expires_at=NULL,updated_at=?
                           WHERE outbox_id=? AND status='claimed' AND attempt=?""",
                        (
                            status,
                            None if delivered else float(retry_at),
                            now,
                            str(outbox_id),
                            int(expected_attempt),
                        ),
                    )
                    cursor = await db.execute(
                        """SELECT * FROM companion_projection_route_outbox
                           WHERE outbox_id=?""",
                        (str(outbox_id),),
                    )
                    settled = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert settled is not None
                    return dict(settled)

        return await self._with_retry(_do)
