# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Companion ingress messages, semantic intents and the ingress outbox."""

from __future__ import annotations

import hashlib
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
from .support import _coerce_trusted_owner, log


class CompanionIngressMixin(SessionDBCore):
    """Companion ingress messages, semantic intents and the ingress outbox."""

    __slots__ = ()

    async def append_user_message_with_growth_outbox(
        self,
        session_id: str,
        content: str,
        *,
        trusted_owner: TrustedCompanionOwner | dict[str, Any] | Any,
        request_id: str,
        turn_id: str,
        run_id: str | None = None,
        retry_of_request_id: str | None = None,
        retry_of_run_id: str | None = None,
        retry_of_turn_id: str | None = None,
        retry_of_message_id: int | None = None,
        priority: str = "normal",
    ) -> int:
        """Atomically append a user message and its durable GrowthEvent intent."""

        if not self._initialized:
            await self.initialize()
        owner = _coerce_trusted_owner(trusted_owner)
        if priority not in {"normal", "blocking"}:
            raise ValueError("invalid companion ingress priority")
        sid = str(session_id or "").strip()
        request = str(request_id or "").strip()
        turn = str(turn_id or "").strip()
        if not sid or not request or not turn:
            raise ValueError("session_id, request_id and turn_id are required")
        payload_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = time.time()

        async def _do() -> tuple[int, bool]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT message_id,payload_hash,owner_kind,binding_epoch,run_id,
                                  retry_of_request_id,retry_of_run_id,retry_of_turn_id,
                                  retry_of_message_id,session_id,priority
                           FROM companion_ingress_outbox
                           WHERE profile_id=? AND profile_generation=?
                             AND request_id=? AND turn_id=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            request,
                            turn,
                        ),
                    )
                    replay = await cursor.fetchone()
                    await cursor.close()
                    if replay is not None:
                        expected = (
                            payload_hash,
                            owner.owner_kind,
                            owner.binding_epoch,
                            run_id,
                            retry_of_request_id,
                            retry_of_run_id,
                            retry_of_turn_id,
                            retry_of_message_id,
                            sid,
                        )
                        actual = (
                            str(replay[1]),
                            str(replay[2]),
                            int(replay[3]),
                            replay[4],
                            replay[5],
                            replay[6],
                            replay[7],
                            replay[8],
                            str(replay[9]),
                        )
                        if actual != expected:
                            await db.rollback()
                            raise RuntimeError("companion_ingress_replay_conflict")
                        await db.commit()
                        return int(replay[0]), False
                    cursor = await db.execute(
                        """SELECT o.owner_kind,o.profile_id,o.profile_generation,
                                  o.binding_epoch,o.status,d.deleted_at
                           FROM companion_session_owners o
                           LEFT JOIN session_delivery_state d ON d.session_id=o.session_id
                           WHERE o.session_id=?""",
                        (sid,),
                    )
                    bound = await cursor.fetchone()
                    await cursor.close()
                    if (
                        bound is None
                        or str(bound[0]) != owner.owner_kind
                        or str(bound[1]) != owner.profile_id
                        or int(bound[2]) != owner.profile_generation
                        or int(bound[3]) != owner.binding_epoch
                        or str(bound[4]) != "active"
                        or bound[5] is not None
                    ):
                        await db.rollback()
                        raise RuntimeError("companion_session_owner_fence_mismatch")
                    msg_id, inserted = await self._insert_message_row(
                        db,
                        session_id=sid,
                        role="user",
                        content=content,
                        tool_call_id=None,
                        tool_calls_json=None,
                        reasoning=None,
                        workflow_event_id=None,
                        projection_kind="user_message",
                        context_visibility="conversation",
                        root_run_id=run_id,
                        task_scope_id=turn,
                        memory_authority="harness",
                    )
                    event_ref = f"message:{sid}:{msg_id}"
                    envelope = {
                        "schema_version": 1,
                        "event_id": event_ref,
                        "source_kind": "user_message",
                        "source_ref": event_ref,
                        "owner_kind": owner.owner_kind,
                        "profile_id": owner.profile_id,
                        "profile_generation": owner.profile_generation,
                        "binding_epoch": owner.binding_epoch,
                        "session_id": sid,
                        "message_id": msg_id,
                        "request_id": request,
                        "run_id": run_id,
                        "turn_id": turn,
                        "retry_of_request_id": retry_of_request_id,
                        "retry_of_run_id": retry_of_run_id,
                        "retry_of_turn_id": retry_of_turn_id,
                        "retry_of_message_id": retry_of_message_id,
                        "payload_hash": payload_hash,
                    }
                    envelope_json = companion_canonical_json(envelope)
                    event_hash = companion_canonical_hash(envelope)
                    await db.execute(
                        """INSERT INTO companion_ingress_outbox(
                             outbox_id,session_id,message_id,owner_kind,profile_id,
                             profile_generation,binding_epoch,request_id,run_id,turn_id,
                             retry_of_request_id,retry_of_run_id,retry_of_turn_id,
                             retry_of_message_id,event_ref,event_envelope_json,event_hash,
                             payload_hash,priority,status,claim_epoch,attempt,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                     'pending',0,0,?,?)""",
                        (
                            event_ref,
                            sid,
                            msg_id,
                            owner.owner_kind,
                            owner.profile_id,
                            owner.profile_generation,
                            owner.binding_epoch,
                            request,
                            run_id,
                            turn,
                            retry_of_request_id,
                            retry_of_run_id,
                            retry_of_turn_id,
                            retry_of_message_id,
                            event_ref,
                            envelope_json,
                            event_hash,
                            payload_hash,
                            priority,
                            now,
                            now,
                        ),
                    )
                    await db.commit()
                    return msg_id, inserted

        msg_id, inserted = await self._with_retry(_do)
        if inserted and self._on_message_written is not None:
            try:
                await self._on_message_written(msg_id, content)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "on_message_written hook failed for msg_id=%s: %s",
                    msg_id,
                    exc,
                    exc_info=True,
                )
        return msg_id

    async def settle_companion_ingress_semantic_intent(
        self,
        *,
        session_id: str,
        message_id: int,
        trusted_owner: TrustedCompanionOwner | dict[str, Any] | Any,
        request_id: str,
        turn_id: str,
        growth_signal_kind: str,
    ) -> dict[str, Any]:
        """Persist the model's growth classification before outbox delivery.

        Settlement is monotonic: a semantic growth signal can promote normal
        to blocking, while later fallback/replay calls can never downgrade it.
        """

        if not self._initialized:
            await self.initialize()
        owner = _coerce_trusted_owner(trusted_owner)
        kind = str(growth_signal_kind or "none").strip()
        allowed = {
            "none",
            "explicit_correction",
            "explicit_capability_request",
        }
        if kind not in allowed:
            raise ValueError("invalid companion growth signal kind")
        sid = str(session_id or "").strip()
        request = str(request_id or "").strip()
        turn = str(turn_id or "").strip()
        if not sid or not request or not turn or int(message_id) < 1:
            raise ValueError("session_id, message_id, request_id and turn_id are required")
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        """SELECT * FROM companion_ingress_outbox
                           WHERE session_id=? AND message_id=? AND request_id=?
                             AND turn_id=? AND owner_kind=? AND profile_id=?
                             AND profile_generation=? AND binding_epoch=?""",
                        (
                            sid,
                            int(message_id),
                            request,
                            turn,
                            owner.owner_kind,
                            owner.profile_id,
                            owner.profile_generation,
                            owner.binding_epoch,
                        ),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.rollback()
                        raise RuntimeError("companion_ingress_semantic_fence_mismatch")
                    envelope = json.loads(str(row["event_envelope_json"]))
                    existing_kind = str(envelope.get("semantic_growth_intent") or "none")
                    if existing_kind not in allowed:
                        await db.rollback()
                        raise RuntimeError("companion_ingress_semantic_state_invalid")
                    effective_kind = existing_kind if existing_kind != "none" else kind
                    if existing_kind != "none" and kind != "none" and existing_kind != kind:
                        await db.rollback()
                        raise RuntimeError("companion_ingress_semantic_replay_conflict")
                    priority = "blocking" if effective_kind != "none" else str(row["priority"])
                    if row["status"] == "pending":
                        envelope["semantic_growth_intent"] = effective_kind
                        envelope_json = companion_canonical_json(envelope)
                        event_hash = companion_canonical_hash(envelope)
                        await db.execute(
                            """UPDATE companion_ingress_outbox
                               SET event_envelope_json=?,event_hash=?,priority=?,
                                   updated_at=?
                               WHERE outbox_id=? AND status='pending'""",
                            (
                                envelope_json,
                                event_hash,
                                priority,
                                now,
                                row["outbox_id"],
                            ),
                        )
                    elif effective_kind != existing_kind or priority != str(row["priority"]):
                        await db.rollback()
                        raise RuntimeError("companion_ingress_semantic_settle_too_late")
                    cursor = await db.execute(
                        "SELECT * FROM companion_ingress_outbox WHERE outbox_id=?",
                        (row["outbox_id"],),
                    )
                    settled = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert settled is not None
                    result = dict(settled)
                    result["event_envelope"] = json.loads(str(result["event_envelope_json"]))
                    return result

        return await self._with_retry(_do)

    async def read_companion_ingress_message_content(
        self,
        *,
        session_id: str,
        message_id: int,
    ) -> str:
        """Read the exact committed user message referenced by growth outbox."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid or isinstance(message_id, bool) or int(message_id) < 1:
            raise ValueError("session_id and positive message_id are required")

        async def _read() -> str:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """SELECT content FROM messages
                       WHERE id=? AND session_id=? AND role='user'
                         AND projection_kind='user_message'
                         AND context_visibility='conversation'""",
                    (int(message_id), sid),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    raise RuntimeError("companion_ingress_message_missing")
                return str(row[0])

        return await self._with_retry(_read)

    async def claim_companion_ingress_outbox(
        self,
        claim_owner: str,
        lease_seconds: float,
        *,
        profile_id: str | None = None,
        profile_generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Claim one pending/expired ingress intent with a monotonic epoch."""

        if not self._initialized:
            await self.initialize()
        worker = str(claim_owner or "").strip()
        lease = float(lease_seconds)
        if not worker or lease <= 0:
            raise ValueError("claim_owner and a positive lease_seconds are required")
        now = time.time()
        expiry = now + lease

        async def _do() -> dict[str, Any] | None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    where = [
                        """((status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?))
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
                        f"""SELECT * FROM companion_ingress_outbox
                            WHERE {" AND ".join(where)}
                            ORDER BY CASE priority WHEN 'blocking' THEN 0 ELSE 1 END,
                                     created_at,outbox_id LIMIT 1""",
                        params,
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.commit()
                        return None
                    epoch = int(row["claim_epoch"]) + 1
                    attempt = int(row["attempt"]) + 1
                    await db.execute(
                        """UPDATE companion_ingress_outbox
                           SET status='claimed',claim_owner=?,claim_epoch=?,
                               lease_expires_at=?,attempt=?,updated_at=?
                           WHERE outbox_id=? AND claim_epoch=?""",
                        (
                            worker,
                            epoch,
                            expiry,
                            attempt,
                            now,
                            row["outbox_id"],
                            row["claim_epoch"],
                        ),
                    )
                    cursor = await db.execute(
                        "SELECT * FROM companion_ingress_outbox WHERE outbox_id=?",
                        (row["outbox_id"],),
                    )
                    claimed = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert claimed is not None
                    result = dict(claimed)
                    result["event_envelope"] = json.loads(str(result["event_envelope_json"]))
                    return result

        return await self._with_retry(_do)

    async def settle_companion_ingress_outbox(
        self,
        outbox_id: str,
        expected_attempt: int,
        *,
        delivered_hash: str | None = None,
        error: str | None = None,
        retry_at: float | None = None,
    ) -> dict[str, Any]:
        """Settle, retry, or dead-letter one exact claimed attempt."""

        if not self._initialized:
            await self.initialize()
        if delivered_hash is not None and (error is not None or retry_at is not None):
            raise ValueError("delivered settle cannot include error/retry")
        if delivered_hash is None and error is None:
            raise ValueError("settle requires delivered_hash or error")
        now = time.time()

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        "SELECT * FROM companion_ingress_outbox WHERE outbox_id=?",
                        (str(outbox_id),),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        await db.rollback()
                        raise RuntimeError("companion_ingress_outbox_missing")
                    if row["status"] == "delivered":
                        if int(row["attempt"]) != int(expected_attempt):
                            await db.rollback()
                            raise RuntimeError("companion_ingress_attempt_stale")
                        if delivered_hash != row["delivered_hash"]:
                            await db.rollback()
                            raise RuntimeError("companion_ingress_settle_conflict")
                        await db.commit()
                        return dict(row)
                    if row["status"] != "claimed" or int(row["attempt"]) != int(expected_attempt):
                        await db.rollback()
                        raise RuntimeError("companion_ingress_attempt_stale")
                    if delivered_hash is not None:
                        status = "delivered"
                        next_retry = None
                    elif retry_at is not None:
                        status = "pending"
                        next_retry = float(retry_at)
                    else:
                        status = "dead_letter"
                        next_retry = None
                    await db.execute(
                        """UPDATE companion_ingress_outbox
                           SET status=?,delivered_hash=?,last_error=?,next_retry_at=?,
                               lease_expires_at=NULL,updated_at=?
                           WHERE outbox_id=? AND status='claimed' AND attempt=?""",
                        (
                            status,
                            delivered_hash,
                            error,
                            next_retry,
                            now,
                            outbox_id,
                            int(expected_attempt),
                        ),
                    )
                    cursor = await db.execute(
                        "SELECT * FROM companion_ingress_outbox WHERE outbox_id=?",
                        (outbox_id,),
                    )
                    settled = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert settled is not None
                    return dict(settled)

        return await self._with_retry(_do)
