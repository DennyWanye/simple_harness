# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Messages, turns, search and session listings."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from memory.base import ConversationTurn, SessionSummary, StoredTurn

from deskpet.memory.primary_authority import (
    assert_not_primary_authority,
    assert_not_primary_authority_tx,
)

from .core import SessionDBCore
from .support import DEFAULT_MEMORY_USER_ID, log, normalize_message_projection

# ----------------------------------------------------------------------
# Row mapping helpers
# ----------------------------------------------------------------------

_BASE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "created_at",
    "salience",
    "decay_last_touch",
    "user_emotion",
    "audio_file_path",
    "tool_call_id",
    "tool_calls",
    # P4-S24: chain-of-thought from thinking-mode LLMs (DeepSeek V4 Pro,
    # Qwen3 thinking, etc.). NULL for non-thinking models. Round-tripped
    # back into LLM payloads via MemoryComponent so the API doesn't 400.
    "reasoning_content",
)


def _row_to_dict(row: tuple, with_rank: bool = False) -> dict[str, Any]:
    """把 SELECT row 转成前端友好的 dict，tool_calls 反序列化成 list。"""
    d: dict[str, Any] = {k: row[i] for i, k in enumerate(_BASE_COLUMNS)}
    tc = d.get("tool_calls")
    if tc:
        try:
            d["tool_calls"] = json.loads(tc)
        except json.JSONDecodeError:
            # 保留原字符串，避免吞掉调试信号
            pass
    if with_rank and len(row) > len(_BASE_COLUMNS):
        d["rank"] = row[len(_BASE_COLUMNS)]
    return d


class MessageMixin(SessionDBCore):
    """Messages, turns, search and session listings."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        skip_embed: bool = False,
        workflow_event_id: str | None = None,
        projection_kind: str | None = None,
        context_visibility: str | None = None,
        root_run_id: str | None = None,
        task_scope_id: str | None = None,
        user_id: str = DEFAULT_MEMORY_USER_ID,
        memory_authority: str = "product",
    ) -> int:
        """写入一条 message。

        FTS5 同步通过 migration 里的 trigger 自动完成，无需额外 insert。
        返回新 message 的 id（lastrowid）。

        ``reasoning_content`` (P4-S24): 思考模式 LLM 的 chain-of-thought
        原文。仅 role='assistant' 行需要；为 None / 空字符串时落 NULL。
        多轮对话时会被读出来塞回 LLM payload，否则 DeepSeek V4 Pro
        / Qwen3 thinking 之类会以 HTTP 400 拒绝下一轮请求。
        """
        if not self._initialized:
            await self.initialize()

        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        reasoning = reasoning_content if reasoning_content else None
        projection, visibility = normalize_message_projection(
            role, projection_kind, context_visibility
        )
        effective_skip_embed = bool(skip_embed or visibility == "exclude")

        async def _do() -> tuple[int, bool]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if workflow_event_id is None:
                        await db.execute(
                            "INSERT INTO session_delivery_state("
                            "session_id, epoch, deleted_at, reason) "
                            "VALUES (?, 0, NULL, NULL) "
                            "ON CONFLICT(session_id) DO UPDATE SET "
                            "deleted_at=NULL, reason=NULL",
                            (session_id,),
                        )
                    result = await self._insert_message_row(
                        db,
                        session_id=session_id,
                        role=role,
                        content=content,
                        tool_call_id=tool_call_id,
                        tool_calls_json=tool_calls_json,
                        reasoning=reasoning,
                        workflow_event_id=workflow_event_id,
                        projection_kind=projection,
                        context_visibility=visibility,
                        root_run_id=root_run_id,
                        task_scope_id=task_scope_id,
                        user_id=user_id,
                        memory_authority=memory_authority,
                    )
                    await db.commit()
                    return result

        msg_id, inserted = await self._with_retry(_do)

        # P4-S2 hook：消息已落盘，异步通知订阅者（典型：VectorWorker）。
        # 契约：
        #   * hook 在写锁**之外**触发——避免 hook 阻塞主写路径
        #   * hook 抛异常只 log warn，不影响返回值
        #   * FP-4 WI-3.4: skip_embed=True 时跳过 hook（消息仍入 messages 表
        #     + FTS5 trigger 自动同步；仅 L3 向量 embedding 被跳过）。
        if inserted and self._on_message_written is not None and not effective_skip_embed:
            try:
                await self._on_message_written(msg_id, content)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "on_message_written hook failed for msg_id=%s: %s",
                    msg_id,
                    exc,
                )

        return msg_id

    async def _insert_message_row(
        self,
        db: aiosqlite.Connection,
        *,
        session_id: str,
        role: str,
        content: str,
        tool_call_id: str | None,
        tool_calls_json: str | None,
        reasoning: str | None,
        workflow_event_id: str | None,
        projection_kind: str,
        context_visibility: str,
        root_run_id: str | None,
        task_scope_id: str | None,
        projection_event_id: str | None = None,
        projection_owner_kind: str | None = None,
        projection_owner_id: str | None = None,
        projection_owner_generation: int | None = None,
        projection_epoch: int | None = None,
        projection_route_version: int | None = None,
        projection_payload_hash: str | None = None,
        user_id: str = DEFAULT_MEMORY_USER_ID,
        memory_authority: str = "product",
    ) -> tuple[int, bool]:
        generic_event_id = projection_event_id or workflow_event_id
        cursor = await db.execute(
            "INSERT INTO messages("
            "session_id, role, content, created_at, tool_call_id, tool_calls, "
            "reasoning_content, workflow_event_id, projection_kind, context_visibility, "
            "root_run_id, task_scope_id, projection_event_id, projection_owner_kind, "
            "projection_owner_id, projection_owner_generation, projection_epoch, "
            "projection_route_version, projection_payload_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workflow_event_id) "
            "WHERE workflow_event_id IS NOT NULL DO NOTHING "
            "ON CONFLICT("
            "projection_owner_kind,projection_owner_id,projection_owner_generation,"
            "projection_event_id"
            ") WHERE projection_kind='companion_event' DO NOTHING",
            (
                session_id,
                role,
                content,
                time.time(),
                tool_call_id,
                tool_calls_json,
                reasoning,
                workflow_event_id,
                projection_kind,
                context_visibility,
                root_run_id,
                task_scope_id,
                generic_event_id,
                projection_owner_kind,
                projection_owner_id,
                projection_owner_generation,
                projection_epoch,
                projection_route_version,
                projection_payload_hash,
            ),
        )
        inserted = int(cursor.rowcount or 0) > 0
        msg_id = int(cursor.lastrowid or 0) if inserted else 0
        await cursor.close()
        if inserted:
            authority = str(memory_authority or "").strip().lower()
            if authority not in {"product", "harness", "none"}:
                raise ValueError("invalid memory_authority")
            if authority == "product":
                await self._ensure_memory_binding_in_transaction(
                    db, session_id=session_id, user_id=user_id
                )
            # 2026-09-10：``product_memory_outbox`` 是 SessionDB 投喂认知记忆
            # SDK 的入口队列。SDK 移除后没有任何消费者，故不再产生新行（旧行
            # 由 project_session_reset 的清表清单照常清理）。
        if not inserted:
            if workflow_event_id is not None:
                conflict_where = "workflow_event_id = ?"
                conflict_params: tuple[Any, ...] = (workflow_event_id,)
            elif (
                projection_kind == "companion_event"
                and projection_owner_kind is not None
                and projection_owner_id is not None
                and projection_owner_generation is not None
                and generic_event_id is not None
            ):
                conflict_where = (
                    "projection_kind='companion_event' "
                    "AND projection_owner_kind=? AND projection_owner_id=? "
                    "AND projection_owner_generation=? AND projection_event_id=?"
                )
                conflict_params = (
                    projection_owner_kind,
                    projection_owner_id,
                    projection_owner_generation,
                    generic_event_id,
                )
            else:
                raise RuntimeError("message insert was ignored without an event id")
            cursor = await db.execute(
                "SELECT id, projection_kind, context_visibility, "
                "root_run_id, task_scope_id, projection_event_id, "
                "projection_owner_kind, projection_owner_id, "
                "projection_owner_generation, projection_epoch, "
                "projection_route_version, projection_payload_hash, "
                "session_id, role, content "
                f"FROM messages WHERE {conflict_where}",
                conflict_params,
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise RuntimeError(f"message event conflict has no row: {generic_event_id}")
            if str(row[1]) != projection_kind or str(row[2]) != context_visibility:
                raise RuntimeError(
                    "workflow_message_projection_conflict: "
                    f"event_id={workflow_event_id!r} existing=({row[1]!r},{row[2]!r}) "
                    f"requested=({projection_kind!r},{context_visibility!r})"
                )
            if (None if row[3] is None else str(row[3])) != root_run_id or (
                None if row[4] is None else str(row[4])
            ) != task_scope_id:
                raise RuntimeError(
                    f"workflow_message_task_scope_conflict: event_id={workflow_event_id!r}"
                )
            if projection_kind == "companion_event":
                expected_projection = (
                    generic_event_id,
                    projection_owner_kind,
                    projection_owner_id,
                    projection_owner_generation,
                    projection_epoch,
                    projection_route_version,
                    projection_payload_hash,
                    session_id,
                    role,
                    content,
                )
                actual_projection = (
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                )
            else:
                expected_projection = (
                    generic_event_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
                actual_projection = tuple(row[5:12])
            if actual_projection != expected_projection:
                raise RuntimeError(
                    f"message_event_projection_conflict: event_id={generic_event_id!r}"
                )
            msg_id = int(row[0])
        return msg_id, inserted

    async def append_message_if_epoch(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        expected_epoch: int,
        workflow_event_id: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        skip_embed: bool = False,
        projection_kind: str | None = None,
        context_visibility: str | None = None,
        root_run_id: str | None = None,
        task_scope_id: str | None = None,
        user_id: str = DEFAULT_MEMORY_USER_ID,
        memory_authority: str = "product",
    ) -> int | None:
        """Append a workflow delivery only while the session fence is live.

        ``None`` means the epoch changed or the session was tombstoned. A
        repeated ``workflow_event_id`` returns the original row and does not
        fire the embedding hook again.
        """
        if not self._initialized:
            await self.initialize()
        event_id = str(workflow_event_id or "").strip()
        if not event_id:
            raise ValueError("workflow_event_id must be non-empty")

        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        reasoning = reasoning_content if reasoning_content else None
        projection, visibility = normalize_message_projection(
            role, projection_kind, context_visibility
        )
        effective_skip_embed = bool(skip_embed or visibility == "exclude")

        async def _do() -> tuple[int | None, bool]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        "SELECT epoch, deleted_at FROM session_delivery_state WHERE session_id = ?",
                        (session_id,),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    current_epoch = int(row[0]) if row else 0
                    deleted_at = row[1] if row else None
                    if current_epoch != int(expected_epoch) or deleted_at is not None:
                        await db.rollback()
                        return None, False
                    if row is None:
                        await db.execute(
                            "INSERT INTO session_delivery_state("
                            "session_id, epoch, deleted_at, reason) "
                            "VALUES (?, 0, NULL, NULL)",
                            (session_id,),
                        )
                    msg_id, inserted = await self._insert_message_row(
                        db,
                        session_id=session_id,
                        role=role,
                        content=content,
                        tool_call_id=tool_call_id,
                        tool_calls_json=tool_calls_json,
                        reasoning=reasoning,
                        workflow_event_id=event_id,
                        projection_kind=projection,
                        context_visibility=visibility,
                        root_run_id=root_run_id,
                        task_scope_id=task_scope_id,
                        user_id=user_id,
                        memory_authority=memory_authority,
                    )
                    await db.commit()
                    return msg_id, inserted

        msg_id, inserted = await self._with_retry(_do)
        if (
            msg_id is not None
            and inserted
            and self._on_message_written is not None
            and not effective_skip_embed
        ):
            try:
                await self._on_message_written(msg_id, content)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "on_message_written hook failed for msg_id=%s: %s",
                    msg_id,
                    exc,
                )
        return msg_id

    async def get_session_delivery_state(self, session_id: str) -> dict[str, Any]:
        """Return the durable session epoch; absent rows mean live epoch zero."""
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT epoch, deleted_at, reason FROM session_delivery_state WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return {"session_id": session_id, "epoch": 0, "deleted_at": None, "reason": None}
        return {
            "session_id": session_id,
            "epoch": int(row[0]),
            "deleted_at": row[1],
            "reason": row[2],
        }

    async def tombstone_session(
        self,
        session_id: str,
        *,
        reason: str = "deleted",
        deleted_at: float | None = None,
    ) -> int:
        """Increment the delivery epoch and make late workflow appends fail."""
        if not self._initialized:
            await self.initialize()
        timestamp = time.time() if deleted_at is None else float(deleted_at)

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    await db.execute(
                        "INSERT INTO session_delivery_state("
                        "session_id, epoch, deleted_at, reason) "
                        "VALUES (?, 1, ?, ?) "
                        "ON CONFLICT(session_id) DO UPDATE SET "
                        "epoch=session_delivery_state.epoch + 1, "
                        "deleted_at=excluded.deleted_at, reason=excluded.reason",
                        (session_id, timestamp, str(reason)),
                    )
                    cursor = await db.execute(
                        "SELECT epoch FROM session_delivery_state WHERE session_id = ?",
                        (session_id,),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    await self._tombstone_companion_route(
                        db,
                        session_id=session_id,
                        new_epoch=int(row[0]),
                        reason=str(reason),
                        now=timestamp,
                    )
                    await db.commit()
                    return int(row[0])

        return await self._with_retry(_do)

    async def _tombstone_companion_route(
        self,
        db: aiosqlite.Connection,
        *,
        session_id: str,
        new_epoch: int,
        reason: str,
        now: float,
    ) -> None:
        """Tombstone one owner/route and advance its read scope atomically."""

        cursor = await db.execute(
            """SELECT profile_id,profile_generation,status
               FROM companion_session_owners WHERE session_id=?""",
            (session_id,),
        )
        owner = await cursor.fetchone()
        await cursor.close()
        if owner is None or str(owner[2]) != "active":
            return
        profile_id = str(owner[0])
        profile_generation = int(owner[1])
        await db.execute(
            """UPDATE companion_session_owners
               SET status='tombstoned',scope_version=scope_version+1,updated_at=?
               WHERE session_id=? AND status='active'""",
            (now, session_id),
        )
        await db.execute(
            """UPDATE companion_owner_scope_versions
               SET scope_version=scope_version+1,updated_at=?
               WHERE profile_id=? AND profile_generation=?""",
            (now, profile_id, profile_generation),
        )
        cursor = await db.execute(
            """SELECT route_version,status FROM companion_projection_routes
               WHERE profile_id=? AND profile_generation=?
                 AND target_session_id=?""",
            (profile_id, profile_generation, session_id),
        )
        route = await cursor.fetchone()
        await cursor.close()
        if route is None or str(route[1]) != "active":
            return
        route_version = int(route[0]) + 1
        await db.execute(
            """UPDATE companion_projection_routes
               SET target_epoch=?,route_version=?,status='tombstoned',updated_at=?
               WHERE profile_id=? AND profile_generation=?
                 AND target_session_id=? AND status='active'""",
            (
                int(new_epoch),
                route_version,
                now,
                profile_id,
                profile_generation,
                session_id,
            ),
        )
        await self._insert_companion_route_outbox(
            db,
            profile_id=profile_id,
            profile_generation=profile_generation,
            route_version=route_version,
            event_kind="session_tombstoned",
            target_session_id=session_id,
            target_epoch=int(new_epoch),
            now=now,
        )

    async def get_message_role(self, msg_id: int) -> str | None:
        """返回单条消息的 role（按主键查，O(1)）。msg 不存在 → None。

        记忆系统升级 WI-M1.2：`_on_message_written` hook 是 2 参数
        `(msg_id, content)`（保持向后兼容、不动既有 9 处 hook 调用点），
        facts 抽取需要 role —— fanout callable 用本方法按 msg_id 反查。
        """
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT role FROM messages WHERE id = ?", (int(msg_id),))
            row = await cursor.fetchone()
            await cursor.close()
        return str(row[0]) if row else None

    async def get_message_routing_context(self, msg_id: int) -> dict[str, Any] | None:
        """Read the immutable workload identity for one persisted message.

        The fanout hook intentionally receives only ``(message_id, content)``.
        This single indexed query recovers every routing field together so a
        background task cannot combine identity from different reads.
        """

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT session_id, root_run_id, task_scope_id, role,
                       projection_kind
                FROM messages
                WHERE id = ?
                """,
                (int(msg_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "root_run_id": (None if row["root_run_id"] is None else str(row["root_run_id"])),
            "task_scope_id": (None if row["task_scope_id"] is None else str(row["task_scope_id"])),
            "role": str(row["role"]),
            "projection_kind": (
                None if row["projection_kind"] is None else str(row["projection_kind"])
            ),
        }

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        *,
        root_run_id: str | None = None,
        task_scope_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 created_at ASC 返回 session 的消息。limit + offset 分页。"""
        if not self._initialized:
            await self.initialize()

        where = ["session_id = ?"]
        params: list[Any] = [session_id]
        if root_run_id is not None:
            where.append("root_run_id = ?")
            params.append(root_run_id)
        if task_scope_id is not None:
            where.append("task_scope_id = ?")
            params.append(task_scope_id)
        params.extend((limit, offset))
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "projection_kind, context_visibility, is_summary, summary_of, "
                "root_run_id, task_scope_id, projection_event_id, "
                "projection_owner_kind, projection_owner_id, "
                "projection_owner_generation, projection_epoch, "
                "projection_route_version, projection_payload_hash "
                f"FROM messages WHERE {' AND '.join(where)} "
                "ORDER BY created_at ASC, id ASC "
                "LIMIT ? OFFSET ?",
                tuple(params),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row[: len(_BASE_COLUMNS)])
            item["workflow_event_id"] = row[len(_BASE_COLUMNS)]
            item["projection_kind"] = row[len(_BASE_COLUMNS) + 1]
            item["context_visibility"] = row[len(_BASE_COLUMNS) + 2]
            item["is_summary"] = bool(row[len(_BASE_COLUMNS) + 3])
            item["summary_of"] = row[len(_BASE_COLUMNS) + 4]
            item["root_run_id"] = row[len(_BASE_COLUMNS) + 5]
            item["task_scope_id"] = row[len(_BASE_COLUMNS) + 6]
            item["projection_event_id"] = row[len(_BASE_COLUMNS) + 7]
            item["projection_owner_kind"] = row[len(_BASE_COLUMNS) + 8]
            item["projection_owner_id"] = row[len(_BASE_COLUMNS) + 9]
            item["projection_owner_generation"] = row[len(_BASE_COLUMNS) + 10]
            item["projection_epoch"] = row[len(_BASE_COLUMNS) + 11]
            item["projection_route_version"] = row[len(_BASE_COLUMNS) + 12]
            item["projection_payload_hash"] = row[len(_BASE_COLUMNS) + 13]
            result.append(item)
        return result

    async def get_recent_messages(
        self,
        session_id: str,
        limit: int = 10,
        *,
        root_run_id: str | None = None,
        task_scope_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the newest ``limit`` messages in chronological order.

        ``get_messages`` is the oldest-first paginated transcript API.  Memory
        assembly needs the opposite selection semantics (the contiguous tail),
        but still needs chronological ordering when the rows are sent to an
        LLM.  Keep those contracts separate so UI pagination does not silently
        change when L2 recall is fixed.
        """
        if not self._initialized:
            await self.initialize()
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []

        where = [
            "session_id = ?",
            "context_visibility = 'conversation'",
        ]
        where_params: list[Any] = [session_id]
        if root_run_id is not None:
            where.append("root_run_id = ?")
            where_params.append(root_run_id)
        if task_scope_id is not None:
            where.append("task_scope_id = ?")
            where_params.append(task_scope_id)

        # Fetch a small prefix beyond the requested window so a boundary that
        # lands on a tool result can be expanded to include its assistant call.
        fetch_limit = safe_limit + 20
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "projection_kind, context_visibility, root_run_id, task_scope_id "
                "FROM ("
                "  SELECT id, session_id, role, content, created_at, "
                "  salience, decay_last_touch, user_emotion, audio_file_path, "
                "  tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "  projection_kind, context_visibility, root_run_id, task_scope_id "
                f"  FROM messages WHERE {' AND '.join(where)} "
                "  ORDER BY created_at DESC, id DESC LIMIT ?"
                ") ORDER BY created_at ASC, id ASC",
                tuple((*where_params, safe_limit)),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        all_rows: list[dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row[: len(_BASE_COLUMNS)])
            item["workflow_event_id"] = row[len(_BASE_COLUMNS)]
            item["projection_kind"] = row[len(_BASE_COLUMNS) + 1]
            item["context_visibility"] = row[len(_BASE_COLUMNS) + 2]
            item["root_run_id"] = row[len(_BASE_COLUMNS) + 3]
            item["task_scope_id"] = row[len(_BASE_COLUMNS) + 4]
            all_rows.append(item)

        if not all_rows or all_rows[0].get("role") != "tool":
            return all_rows

        # The tail boundary landed inside a tool-call group. Resolve the exact
        # assistant by tool_call_id instead of using a fixed lookback window or
        # accepting an unrelated assistant row.
        boundary_call_id = str(all_rows[0].get("tool_call_id") or "")
        if not boundary_call_id:
            return all_rows
        boundary_id = int(all_rows[0]["id"])
        end_id = int(all_rows[-1]["id"])
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, tool_calls FROM messages "
                f"WHERE {' AND '.join(where)} AND role = 'assistant' "
                "AND id < ? AND tool_calls IS NOT NULL ORDER BY id DESC",
                tuple((*where_params, boundary_id)),
            )
            candidates = await cursor.fetchall()
            await cursor.close()
            assistant_id: int | None = None
            for candidate_id, raw_calls in candidates:
                try:
                    calls = json.loads(raw_calls or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if any(
                    str(call.get("id") or "") == boundary_call_id
                    for call in calls
                    if isinstance(call, dict)
                ):
                    assistant_id = int(candidate_id)
                    break
            if assistant_id is None:
                return all_rows
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "projection_kind, context_visibility, root_run_id, task_scope_id "
                f"FROM messages WHERE {' AND '.join(where)} "
                "AND id BETWEEN ? AND ? "
                "ORDER BY created_at ASC, id ASC",
                tuple((*where_params, assistant_id, end_id)),
            )
            expanded_rows = await cursor.fetchall()
            await cursor.close()

        expanded: list[dict[str, Any]] = []
        for row in expanded_rows:
            item = _row_to_dict(row[: len(_BASE_COLUMNS)])
            item["workflow_event_id"] = row[len(_BASE_COLUMNS)]
            item["projection_kind"] = row[len(_BASE_COLUMNS) + 1]
            item["context_visibility"] = row[len(_BASE_COLUMNS) + 2]
            item["root_run_id"] = row[len(_BASE_COLUMNS) + 3]
            item["task_scope_id"] = row[len(_BASE_COLUMNS) + 4]
            expanded.append(item)
        return expanded

    async def search_fts(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """FTS5 MATCH 查询；结果按 rank 升序（越小越相关）。

        query 按 FTS5 语法传入（空格 AND、``OR``、短语用双引号）。
        """
        if not self._initialized:
            await self.initialize()

        # rank 是 FTS5 内建列，ORDER BY rank 即按相关性升序
        if session_id:
            sql = (
                "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
                "m.salience, m.decay_last_touch, m.user_emotion, "
                "m.audio_file_path, m.tool_call_id, m.tool_calls, "
                "m.reasoning_content, "
                "messages_fts.rank AS rank "
                "FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? AND m.session_id = ? "
                "AND m.context_visibility = 'conversation' "
                "ORDER BY rank LIMIT ?"
            )
            params: tuple = (query, session_id, limit)
        else:
            sql = (
                "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
                "m.salience, m.decay_last_touch, m.user_emotion, "
                "m.audio_file_path, m.tool_call_id, m.tool_calls, "
                "m.reasoning_content, "
                "messages_fts.rank AS rank "
                "FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? AND m.context_visibility = 'conversation' "
                "ORDER BY rank LIMIT ?"
            )
            params = (query, limit)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()

        return [_row_to_dict(r, with_rank=True) for r in rows]

    async def update_salience(
        self,
        message_id: int,
        new_salience: float,
        touch: bool = True,
    ) -> None:
        """更新一条 message 的 salience（可选同时更新 decay_last_touch）.

        P4-S3 recall feedback 会以 +0.05 boost 调这个接口。
        """
        if not self._initialized:
            await self.initialize()

        now = time.time() if touch else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if touch:
                        await db.execute(
                            "UPDATE messages SET salience=?, decay_last_touch=? WHERE id=?",
                            (new_salience, now, message_id),
                        )
                    else:
                        await db.execute(
                            "UPDATE messages SET salience=? WHERE id=?",
                            (new_salience, message_id),
                        )
                    await db.commit()

        await self._with_retry(_do)

    # ---- P4-S17 MemoryStore Protocol compatibility --------------------

    async def get_recent(self, session_id: str, limit: int = 10) -> list[ConversationTurn]:
        """Implement ``MemoryStore.get_recent`` via ``get_messages``."""

        rows = await self.get_recent_messages(session_id, limit=limit)
        return [
            ConversationTurn(
                role=str(row.get("role", "")),
                content=str(row.get("content", "")),
                created_at=float(row.get("created_at") or 0.0),
            )
            for row in rows
        ]

    async def append(self, session_id: str, role: str, content: str) -> None:
        """Implement ``MemoryStore.append`` via ``append_message``."""
        await self.append_message(session_id=session_id, role=role, content=content)

    async def clear(self, session_id: str) -> None:
        """Implement ``MemoryStore.clear`` for one session."""

        await assert_not_primary_authority(self._db_path, session_id)
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    await assert_not_primary_authority_tx(db, (session_id,))
                    await db.execute(
                        "DELETE FROM companion_ingress_outbox WHERE session_id = ?",
                        (session_id,),
                    )
                    await db.execute(
                        "DELETE FROM messages WHERE session_id = ?",
                        (session_id,),
                    )
                    # 连带清掉自定义标题，避免删后重建同名会话残留旧名。
                    await db.execute(
                        "DELETE FROM session_titles WHERE session_id = ?",
                        (session_id,),
                    )
                    await db.execute(
                        "INSERT INTO session_delivery_state("
                        "session_id, epoch, deleted_at, reason) "
                        "VALUES (?, 1, ?, 'deleted') "
                        "ON CONFLICT(session_id) DO UPDATE SET "
                        "epoch=session_delivery_state.epoch + 1, "
                        "deleted_at=excluded.deleted_at, reason=excluded.reason",
                        (session_id, time.time()),
                    )
                    cursor = await db.execute(
                        "SELECT epoch FROM session_delivery_state WHERE session_id = ?",
                        (session_id,),
                    )
                    epoch_row = await cursor.fetchone()
                    await cursor.close()
                    # 所有会话同构退役：清空即墓碑化路由与属主，不再有"只清内容
                    # 不退役"的保留会话分支（会话一律用户新建，删完就是删完）。
                    await self._tombstone_companion_route(
                        db,
                        session_id=session_id,
                        new_epoch=int(epoch_row[0]),
                        reason="deleted",
                        now=time.time(),
                    )
                    await db.commit()

        await self._with_retry(_do)

    # ---- S14 admin surface --------------------------------------------

    async def list_turns(
        self,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> list[StoredTurn]:
        """List messages for one session, or all sessions, as StoredTurn."""

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            if session_id is None:
                sql = (
                    "SELECT id, session_id, role, content, created_at "
                    "FROM messages ORDER BY created_at ASC, id ASC"
                )
                params: tuple = ()
            else:
                sql = (
                    "SELECT id, session_id, role, content, created_at "
                    "FROM messages WHERE session_id = ? "
                    "ORDER BY created_at ASC, id ASC"
                )
                params = (session_id,)
            if limit is not None and limit > 0:
                sql += " LIMIT ?"
                params = (*params, int(limit))
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            StoredTurn(
                id=int(row[0]),
                session_id=str(row[1]),
                role=str(row[2]),
                content=str(row[3]),
                created_at=float(row[4] or 0.0),
            )
            for row in rows
        ]

    async def delete_turn(self, turn_id: int) -> bool:
        """Delete one message row and return whether a row was removed."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM messages WHERE id = ?",
                        (int(turn_id),),
                    )
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    try:
                        await db.execute(
                            "DELETE FROM messages_vec WHERE message_id = ?",
                            (int(turn_id),),
                        )
                        await db.commit()
                    except Exception:
                        pass
                    return removed

        return (await self._with_retry(_do)) > 0

    async def list_sessions(self) -> list[SessionSummary]:
        """List all sessions that have messages, newest first."""

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT session_id, COUNT(*), MAX(created_at) "
                "FROM messages GROUP BY session_id ORDER BY MAX(created_at) DESC"
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            SessionSummary(
                session_id=str(row[0]),
                turn_count=int(row[1] or 0),
                last_message_at=float(row[2] or 0.0),
            )
            for row in rows
        ]

    async def list_sessions_with_preview(self) -> list[dict[str, Any]]:
        """会话清单 + 每个会话首条 user 消息预览（一次查询）。

        用于消息面板「选择历史会话」下拉。preview = 该会话最早一条非空 user
        消息前 60 字（相关子查询）。newest-first。
        """
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT m.session_id, COUNT(*), MAX(m.created_at), "
                "  (SELECT content FROM messages "
                "     WHERE session_id = m.session_id AND role = 'user' "
                "       AND content IS NOT NULL AND content <> '' "
                "     ORDER BY created_at ASC LIMIT 1), "
                "  (SELECT title FROM session_titles "
                "     WHERE session_id = m.session_id) "
                "FROM messages m GROUP BY m.session_id "
                "ORDER BY MAX(m.created_at) DESC"
            )
            rows = await cursor.fetchall()
            await cursor.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            preview = row[3] or ""
            if len(preview) > 60:
                preview = preview[:60]
            out.append(
                {
                    "session_id": str(row[0]),
                    "turn_count": int(row[1] or 0),
                    "last_message_at": float(row[2] or 0.0),
                    "preview": preview,
                    # 用户自定义标题（空串=未命名，前端回退到 preview）。
                    "title": (row[4] or ""),
                }
            )
        return out
