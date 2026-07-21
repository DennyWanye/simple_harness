# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S1 L2 session database — clean-room rewrite.

Attribution:
    Interface style 参考 Hermes AIAgent `SessionStore`（MIT license），但
    代码是 clean-room 重写，不含 Hermes 源码复制。设计依据在
    ``openspec/changes/p4-poseidon-agent-harness/design.md`` §D-ARCH-1 /
    §D-IMPL-2，spec Requirement "Session Database (L2) — SQLite with FTS5"。

Responsibilities:
    * 拉起 state.db（通过 ``schema.initialize_state_db`` 做迁移 + .bak 回滚）
    * WAL 模式 + 应用层 SQLITE_BUSY 重试（jitter exponential backoff，≤5 次）
    * 暴露 create_session / append_message / get_messages / search_fts /
      update_salience / close 六个 async API
    * 可选 sqlite-vec 虚拟表 ``messages_vec``（load_extension 失败 → warn + 降级）

Not here（留给后续 slice）：
    * L3 向量召回本体 → P4-S3 retriever.py
    * embedding 计算 → P4-S2 embedder.py
    * 文件记忆 / MemoryManager → P4-S4
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from typing import Awaitable, Callable, Optional

import aiosqlite

from deskpet.memory.schema import initialize_state_db
from deskpet.memory.memory_v2_schema import SESSION_TITLES_DDL

log = logging.getLogger(__name__)

# P4-S2 hook 类型：(message_id, content) → awaitable None。
# MemoryManager / VectorWorker 在此接入 "消息落盘后异步跑 embedding"。
OnMessageWritten = Callable[[int, str], Awaitable[None]]

MESSAGE_PROJECTION_KINDS = frozenset(
    {
        "legacy_message", "user_message", "assistant_message", "tool_message",
        "system_message", "final_assistant", "workflow_progress",
        "workflow_accepted", "workflow_decision", "workflow_final_status",
        "artifact_card",
    }
)
CONTEXT_VISIBILITIES = frozenset({"conversation", "exclude"})
_ROLE_DEFAULT_PROJECTION = {
    "user": "user_message",
    "assistant": "assistant_message",
    "tool": "tool_message",
    "system": "system_message",
}
_EXCLUDED_PROJECTIONS = frozenset(
    {
        "workflow_progress", "workflow_accepted", "workflow_decision",
        "workflow_final_status", "artifact_card",
    }
)


def normalize_message_projection(
    role: str,
    projection_kind: str | None = None,
    context_visibility: str | None = None,
) -> tuple[str, str]:
    """Return a validated, explicit message projection classification."""
    normalized_role = str(role or "").strip().lower()
    projection = str(
        projection_kind
        or _ROLE_DEFAULT_PROJECTION.get(normalized_role, "legacy_message")
    ).strip().lower()
    if projection not in MESSAGE_PROJECTION_KINDS:
        raise ValueError(f"invalid message projection_kind: {projection!r}")
    expected_visibility = (
        "exclude" if projection in _EXCLUDED_PROJECTIONS else "conversation"
    )
    visibility = str(context_visibility or expected_visibility).strip().lower()
    if visibility not in CONTEXT_VISIBILITIES:
        raise ValueError(f"invalid message context_visibility: {visibility!r}")
    if visibility != expected_visibility:
        raise ValueError(
            "message projection visibility mismatch: "
            f"{projection!r} requires {expected_visibility!r}"
        )
    return projection, visibility


# SQLITE_BUSY retry 参数（3.3 要求）
_MAX_RETRIES = 5
_BASE_DELAY_MS = 100
_JITTER_MS = 50
_MAX_DELAY_MS = 2000

# sqlite-vec 虚拟表 SQL —— 留在 Python 侧而非 migration SQL 里，
# 因为 load_extension 是 connection-scoped，不好在纯 .sql 里表达。
_MESSAGES_VEC_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_vec USING vec0("
    "message_id INTEGER PRIMARY KEY, "
    "embedding FLOAT[1024] distance_metric=cosine"
    ")"
)


def _is_busy_error(exc: BaseException) -> bool:
    """判定某个异常是否为 SQLITE_BUSY / database is locked —— 值得重试。"""
    if not isinstance(exc, (sqlite3.OperationalError, aiosqlite.OperationalError)):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "busy" in msg


def _backoff_delay_ms(attempt: int) -> float:
    """指数退避 + jitter（上限 _MAX_DELAY_MS ms）。

    attempt 从 0 开始：0 → ~100ms，1 → ~200ms，2 → ~400ms ... jitter 0..50ms。
    """
    base = min(_BASE_DELAY_MS * (2**attempt), _MAX_DELAY_MS)
    jitter = random.uniform(0, _JITTER_MS)
    return min(base + jitter, _MAX_DELAY_MS)


class SessionDB:
    """L2 会话存储 —— aiosqlite + WAL + FTS5（+ 可选 sqlite-vec）."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        on_message_written: Optional[OnMessageWritten] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._initialized = False
        self._vec_enabled = False
        # 写锁：WAL 允许并发读，但应用层保证自己的写是串行化的更稳
        # （避免 aiosqlite 同 connection 被多 task 抢）
        self._write_lock = asyncio.Lock()
        # P4-S2: append_message 落盘后的异步回调钩子。典型使用场景是
        # VectorWorker.enqueue —— 把新消息推进 embedding queue。
        # 设计约束（严格）：
        #   * 失败只 log，**不** re-raise 给 append_message 的调用方
        #   * 不得修改 append_message 的返回值（仍是 msg_id）
        #   * None → 老 S1 行为完全不变（零开销）
        self._on_message_written: Optional[OnMessageWritten] = on_message_written

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """启动初始化：迁移 → WAL → 尝试建 messages_vec。幂等。"""
        if self._initialized:
            return
        # 1. 让 schema.initialize_state_db 做迁移 + 备份 + 回滚
        await initialize_state_db(self._db_path)

        # 2. WAL 模式（3.3 要求：第一件事）
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            # 让并发写等 5s 再报 SQLITE_BUSY（SQLite 层第一道防线，
            # 应用层 retry 是第二道）
            await db.execute("PRAGMA busy_timeout=5000")
            # synchronous=NORMAL 在 WAL 下是常规选择：崩溃最多丢最后一个事务
            # 而非破坏数据库。full 对单用户桌宠过于保守。
            await db.execute("PRAGMA synchronous=NORMAL")
            # 用户自定义会话标题（消息面板「重命名话题」）。放侧表，不触碰
            # messages 派生的清单/preview 逻辑；v17 已正式迁移，
            # 这里保留幂等 ensure 兼容隔离调用。
            await db.executescript(SESSION_TITLES_DDL)
            await db.commit()

        # 3. 尝试加载 sqlite-vec 扩展并建 messages_vec 虚拟表
        #    spec 明确允许"降级启动"：失败只 warn，不抛。
        self._vec_enabled = await self._try_init_vec()

        self._initialized = True
        log.info(
            "SessionDB ready (db=%s, vec=%s)",
            self._db_path,
            "on" if self._vec_enabled else "off (degraded)",
        )

    async def _try_init_vec(self) -> bool:
        """加载 sqlite-vec + 建 messages_vec 虚拟表。失败返回 False。

        两步：
          1. `import sqlite_vec` 不可用 → 返回 False（最常见降级场景）
          2. 打开同步 sqlite3 connection，``enable_load_extension(True)`` +
             ``sqlite_vec.load(conn)`` + 建 ``messages_vec`` 虚拟表。同步
             路径是因为 sqlite_vec.load 要的是原生 sqlite3.Connection
             而非 aiosqlite 包装层；aiosqlite 自己也是通过 run_in_executor
             调同步 API，这里直接 run_in_executor 省了一层抽象。

        任何步骤失败都返回 False + log warning，**不抛**。spec 要求
        sqlite-vec 不可用时降级启动，L1+L2 继续工作。
        """
        try:
            import sqlite_vec  # type: ignore
        except ImportError:
            log.warning(
                "sqlite-vec not installed; L3 vector search disabled "
                "(pip install sqlite-vec to enable)"
            )
            return False

        def _sync_init() -> None:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute(_MESSAGES_VEC_DDL)
                conn.commit()
            finally:
                conn.close()

        try:
            await asyncio.get_running_loop().run_in_executor(None, _sync_init)
            return True
        except Exception as exc:  # noqa: BLE001
            # 包括：enable_load_extension 被禁用（极少）、DLL 缺失、vec0
            # 不被识别等。一律降级。
            log.warning(
                "sqlite-vec init failed (%s); L3 disabled, L1+L2 still work",
                exc,
            )
            return False

    async def close(self) -> None:
        """目前每次调用都是 short-lived connection，无持久 conn 可关。

        保留接口以便未来切 connection pool 时签名不变。
        """
        self._initialized = False

    # ------------------------------------------------------------------
    # Write path with retry
    # ------------------------------------------------------------------

    async def _with_retry(self, coro_factory):
        """通用重试包装：对 SQLITE_BUSY 最多重试 _MAX_RETRIES 次。

        ``coro_factory`` 是一个 async 零参 callable，每次 retry 会重新调用
        （不能传已 awaited 的协程，那种不可复用）。
        """
        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except Exception as exc:  # noqa: BLE001
                if not _is_busy_error(exc) or attempt == _MAX_RETRIES:
                    raise
                last_exc = exc
                delay = _backoff_delay_ms(attempt)
                log.debug(
                    "SQLITE_BUSY attempt=%d delay=%.0fms err=%s",
                    attempt,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay / 1000.0)
        # unreachable：循环要么 return 要么 raise
        raise RuntimeError("retry loop exited unexpectedly") from last_exc

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        """新建会话，返回 UUID 字符串。"""
        if not self._initialized:
            await self.initialize()
        session_id = str(uuid.uuid4())
        await self.ensure_session(session_id, metadata)
        return session_id

    async def ensure_session(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Ensure a session row exists and return the normalized id.

        New companion task sessions already receive their UUID before the
        first message is written so frontend, receipts, and messages can all
        share one stable id. This method persists that caller-supplied id in
        the canonical ``sessions`` table without changing existing metadata.
        """
        if not self._initialized:
            await self.initialize()
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id must be non-empty")
        meta_json = json.dumps(metadata) if metadata else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "INSERT INTO sessions(id, created_at, metadata) "
                        "VALUES (?, ?, ?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (sid, time.time(), meta_json),
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
    ) -> tuple[int, bool]:
        cursor = await db.execute(
            "INSERT INTO messages("
            "session_id, role, content, created_at, tool_call_id, tool_calls, "
            "reasoning_content, workflow_event_id, projection_kind, context_visibility"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workflow_event_id) "
            "WHERE workflow_event_id IS NOT NULL DO NOTHING",
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
            ),
        )
        inserted = int(cursor.rowcount or 0) > 0
        msg_id = int(cursor.lastrowid or 0) if inserted else 0
        await cursor.close()
        if not inserted:
            if workflow_event_id is None:
                raise RuntimeError("message insert was ignored without an event id")
            cursor = await db.execute(
                "SELECT id, projection_kind, context_visibility FROM messages "
                "WHERE workflow_event_id = ?",
                (workflow_event_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise RuntimeError(
                    f"workflow message conflict has no row: {workflow_event_id}"
                )
            if str(row[1]) != projection_kind or str(row[2]) != context_visibility:
                raise RuntimeError(
                    "workflow_message_projection_conflict: "
                    f"event_id={workflow_event_id!r} existing=({row[1]!r},{row[2]!r}) "
                    f"requested=({projection_kind!r},{context_visibility!r})"
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
                        "SELECT epoch, deleted_at FROM session_delivery_state "
                        "WHERE session_id = ?",
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
                "SELECT epoch, deleted_at, reason FROM session_delivery_state "
                "WHERE session_id = ?",
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
                    await db.commit()
                    return int(row[0])

        return await self._with_retry(_do)

    async def get_message_role(self, msg_id: int) -> Optional[str]:
        """返回单条消息的 role（按主键查，O(1)）。msg 不存在 → None。

        记忆系统升级 WI-M1.2：`_on_message_written` hook 是 2 参数
        `(msg_id, content)`（保持向后兼容、不动既有 9 处 hook 调用点），
        facts 抽取需要 role —— fanout callable 用本方法按 msg_id 反查。
        """
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT role FROM messages WHERE id = ?", (int(msg_id),)
            )
            row = await cursor.fetchone()
            await cursor.close()
        return str(row[0]) if row else None

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按 created_at ASC 返回 session 的消息。limit + offset 分页。"""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "projection_kind, context_visibility, is_summary, summary_of "
                "FROM messages WHERE session_id = ? "
                "ORDER BY created_at ASC, id ASC "
                "LIMIT ? OFFSET ?",
                (session_id, limit, offset),
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
            result.append(item)
        return result

    async def get_recent_messages(
        self,
        session_id: str,
        limit: int = 10,
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

        # Fetch a small prefix beyond the requested window so a boundary that
        # lands on a tool result can be expanded to include its assistant call.
        fetch_limit = safe_limit + 20
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "projection_kind, context_visibility "
                "FROM ("
                "  SELECT id, session_id, role, content, created_at, "
                "  salience, decay_last_touch, user_emotion, audio_file_path, "
                "  tool_call_id, tool_calls, reasoning_content, workflow_event_id, "
                "  projection_kind, context_visibility "
                "  FROM messages WHERE session_id = ? AND context_visibility = 'conversation' "
                "  ORDER BY created_at DESC, id DESC LIMIT ?"
                ") ORDER BY created_at ASC, id ASC",
                (session_id, safe_limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        all_rows: list[dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row[: len(_BASE_COLUMNS)])
            item["workflow_event_id"] = row[len(_BASE_COLUMNS)]
            item["projection_kind"] = row[len(_BASE_COLUMNS) + 1]
            item["context_visibility"] = row[len(_BASE_COLUMNS) + 2]
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
                "WHERE session_id = ? AND role = 'assistant' "
                "AND context_visibility = 'conversation' "
                "AND id < ? AND tool_calls IS NOT NULL ORDER BY id DESC",
                (session_id, boundary_id),
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
                "projection_kind, context_visibility "
                "FROM messages WHERE session_id = ? AND context_visibility = 'conversation' "
                "AND id BETWEEN ? AND ? "
                "ORDER BY created_at ASC, id ASC",
                (session_id, assistant_id, end_id),
            )
            expanded_rows = await cursor.fetchall()
            await cursor.close()

        expanded: list[dict[str, Any]] = []
        for row in expanded_rows:
            item = _row_to_dict(row[: len(_BASE_COLUMNS)])
            item["workflow_event_id"] = row[len(_BASE_COLUMNS)]
            item["projection_kind"] = row[len(_BASE_COLUMNS) + 1]
            item["context_visibility"] = row[len(_BASE_COLUMNS) + 2]
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
                            "UPDATE messages SET salience=?, decay_last_touch=? "
                            "WHERE id=?",
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

    async def get_recent(
        self, session_id: str, limit: int = 10
    ) -> list["ConversationTurn"]:
        """Implement ``MemoryStore.get_recent`` via ``get_messages``."""
        from memory.base import ConversationTurn

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
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
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
                    await db.commit()

        await self._with_retry(_do)

    # ---- S14 admin surface --------------------------------------------

    async def list_turns(
        self,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> list["StoredTurn"]:
        """List messages for one session, or all sessions, as StoredTurn."""
        from memory.base import StoredTurn

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

    async def list_sessions(self) -> list["SessionSummary"]:
        """List all sessions that have messages, newest first."""
        from memory.base import SessionSummary

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
            preview = (row[3] or "")
            if len(preview) > 60:
                preview = preview[:60]
            out.append({
                "session_id": str(row[0]),
                "turn_count": int(row[1] or 0),
                "last_message_at": float(row[2] or 0.0),
                "preview": preview,
                # 用户自定义标题（空串=未命名，前端回退到 preview）。
                "title": (row[4] or ""),
            })
        return out

    #: Hard cap on a user-set session title (UI also enforces; this is the
    #: server-side backstop so a crafted ws message can't store a huge blob).
    MAX_TITLE_LEN = 80

    async def set_session_title(self, session_id: str, title: str) -> str:
        """Set or clear a user custom title for a companion session.

        - Empty/whitespace ``title`` clears the custom name (the list falls
          back to the auto preview). Idempotent.
        - Non-empty is trimmed + clamped to ``MAX_TITLE_LEN``.

        Returns the stored title ("" when cleared) so the caller can echo the
        canonical value back to the UI.
        """
        if not self._initialized:
            await self.initialize()
        sid = (session_id or "").strip()
        if not sid:
            return ""
        clean = (title or "").strip()[: self.MAX_TITLE_LEN]

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if clean:
                        await db.execute(
                            "INSERT INTO session_titles(session_id, title, updated_at) "
                            "VALUES(?, ?, ?) "
                            "ON CONFLICT(session_id) DO UPDATE SET "
                            "  title = excluded.title, updated_at = excluded.updated_at",
                            (sid, clean, time.time()),
                        )
                    else:
                        await db.execute(
                            "DELETE FROM session_titles WHERE session_id = ?", (sid,)
                        )
                    await db.commit()

        await self._with_retry(_do)
        return clean

    # ---- P5-S2 code_session_provider binding -------------------------

    async def get_code_session_provider_binding(
        self, base_session_id: str
    ) -> dict[str, Any]:
        """读取 code 会话的 provider/model override 绑定.

        返回 ``{"provider_id": str|None, "preferred_model": str|None,
        "model_params": dict|None}``。没有 binding 行 → 三者全 None。
        code-session-model-params: model_params 是 008 列的 JSON 解析
        结果；007 旧行(无该列值) → None（resolution 走 provider 默认）。

        Spec: code-session-model-params → "Per-code-session model+params
        binding is persisted"（含 "Legacy row without params stays valid"）.
        """
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT provider_id, preferred_model, model_params "
                "FROM code_session_provider WHERE base_session_id = ?",
                (base_session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()

        if row is None:
            return {
                "provider_id": None,
                "preferred_model": None,
                "model_params": None,
            }
        params: dict[str, Any] | None = None
        raw = row[2]
        if raw:
            try:
                parsed = json.loads(raw)
                params = parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                params = None  # 损坏 JSON → 当作无参数，永不报错
        return {
            "provider_id": row[0],
            "preferred_model": row[1],
            "model_params": params,
        }

    async def set_code_session_provider_binding(
        self,
        base_session_id: str,
        provider_id: str | None,
        preferred_model: str | None,
        model_params: dict[str, Any] | None = None,
    ) -> None:
        """写入/更新/清除 code 会话的 provider/model(+params) override 绑定.

        语义：
          * 任一字段非 None → upsert 一行（model_params JSON 序列化）
          * 三字段都 None → 删除该 sid 的 binding 行（清除=回全局 chain）

        code-session-model-params: model_params 向后兼容——省略=旧行为。

        Spec: code-session-model-params → Scenarios
          "Set model + params round-trips"
          "Clear binding restores global chain"
        """
        if not self._initialized:
            await self.initialize()

        clearing = (
            provider_id is None
            and preferred_model is None
            and model_params is None
        )
        params_json = (
            json.dumps(model_params, ensure_ascii=False)
            if model_params is not None
            else None
        )

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if clearing:
                        await db.execute(
                            "DELETE FROM code_session_provider "
                            "WHERE base_session_id = ?",
                            (base_session_id,),
                        )
                    else:
                        # SQLite UPSERT —— ON CONFLICT(PK) DO UPDATE
                        await db.execute(
                            "INSERT INTO code_session_provider "
                            "(base_session_id, provider_id, preferred_model, "
                            " model_params, updated_at) "
                            "VALUES (?, ?, ?, ?, julianday('now')) "
                            "ON CONFLICT(base_session_id) DO UPDATE SET "
                            "  provider_id = excluded.provider_id, "
                            "  preferred_model = excluded.preferred_model, "
                            "  model_params = excluded.model_params, "
                            "  updated_at = excluded.updated_at",
                            (
                                base_session_id,
                                provider_id,
                                preferred_model,
                                params_json,
                            ),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def clear_bindings_for_provider(self, provider_id: str) -> int:
        """删除所有指向某 provider_id 的 code_session_provider 绑定行.

        P5-S2 Phase 2 minor extension: 当 provider 被 IPC 删除时,孤儿绑定行
        (sid → 已删除的 provider) 需要清理,避免 resolution 拿到一个不存在的
        provider_id。返回删除的行数。

        Spec: frontend-ipc-surface Scenario "remove cleanup" + design.md
        "Resolution algorithm" 步骤 2.
        """
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_session_provider WHERE provider_id = ?",
                        (provider_id,),
                    )
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    return removed

        return await self._with_retry(_do)

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

    # ------------------------------------------------------------------
    # P4-S22 Code Mode — todo list per session
    # ------------------------------------------------------------------

    async def replace_code_todos(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace the entire todo list for ``session_id`` atomically.

        ``items`` is a list of dicts shaped like::

            {
                "content": "Implement A",
                "activeForm": "Implementing A",
                "status": "pending" | "in_progress" | "completed",
            }

        Mirrors Claude Code's TodoWrite semantics: the tool is
        idempotent — every call replaces the full list, the LLM doesn't
        track diffs. Sort order is preserved by index in ``items``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "DELETE FROM code_todos "
                        "WHERE session_id = ? AND workflow_run_id IS NULL",
                        (session_id,),
                    )
                    for idx, item in enumerate(items):
                        status = item.get("status", "pending")
                        if status not in {"pending", "in_progress", "completed"}:
                            status = "pending"
                        await db.execute(
                            """
                            INSERT INTO code_todos
                              (session_id, content, active_form, status, sort_order)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                session_id,
                                str(item.get("content", ""))[:2000],
                                str(item.get("activeForm", ""))[:2000],
                                status,
                                idx,
                            ),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def get_code_todos(self, session_id: str) -> list[dict[str, Any]]:
        """Return the current todo list for ``session_id`` in render order."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                cursor = await db.execute(
                    """
                    SELECT content, active_form, status, sort_order,
                           workflow_run_id, workflow_step_id
                    FROM code_todos
                    WHERE session_id = ?
                    ORDER BY sort_order
                    """,
                    (session_id,),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "content": row[0],
                        "activeForm": row[1],
                        "status": row[2],
                        "sort_order": row[3],
                        "workflow_run_id": row[4],
                        "workflow_step_id": row[5],
                    }
                    for row in rows
                ]

        return await self._with_retry(_do)

    async def sync_workflow_code_todos(
        self,
        session_id: str,
        workflow_run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Project one workflow run's stable steps without touching legacy rows."""
        if not self._initialized:
            await self.initialize()
        run_id = str(workflow_run_id or "").strip()
        if not run_id:
            raise ValueError("workflow_run_id must be non-empty")

        normalized: list[tuple[str, str, str, str, int]] = []
        seen_steps: set[str] = set()
        for index, item in enumerate(items):
            step_id = str(item.get("workflow_step_id") or "").strip()
            if not step_id:
                raise ValueError(f"items[{index}].workflow_step_id is required")
            if step_id in seen_steps:
                raise ValueError(f"duplicate workflow_step_id: {step_id}")
            seen_steps.add(step_id)
            status = str(item.get("status", "pending"))
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"
            normalized.append(
                (
                    step_id,
                    str(item.get("content", ""))[:2000],
                    str(item.get("activeForm", item.get("active_form", "")))[:2000],
                    status,
                    int(item.get("sort_order", index)),
                )
            )

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    for step_id, content, active_form, status, sort_order in normalized:
                        await db.execute(
                            """
                            INSERT INTO code_todos(
                                session_id, content, active_form, status, sort_order,
                                workflow_run_id, workflow_step_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(workflow_run_id, workflow_step_id)
                            WHERE workflow_step_id IS NOT NULL DO UPDATE SET
                                session_id=excluded.session_id,
                                content=excluded.content,
                                active_form=excluded.active_form,
                                status=excluded.status,
                                sort_order=excluded.sort_order,
                                updated_at=julianday('now')
                            """,
                            (
                                session_id,
                                content,
                                active_form,
                                status,
                                sort_order,
                                run_id,
                                step_id,
                            ),
                        )
                    if seen_steps:
                        placeholders = ",".join("?" for _ in seen_steps)
                        await db.execute(
                            "DELETE FROM code_todos "
                            "WHERE session_id = ? AND workflow_run_id = ? "
                            f"AND workflow_step_id NOT IN ({placeholders})",
                            (session_id, run_id, *sorted(seen_steps)),
                        )
                    else:
                        await db.execute(
                            "DELETE FROM code_todos "
                            "WHERE session_id = ? AND workflow_run_id = ?",
                            (session_id, run_id),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def upsert_code_session(
        self,
        *,
        base_session_id: str,
        code_session_id: str,
        project_root: str,
        project_name: str,
    ) -> None:
        """P4-S25 B4: persist the project enrollment so it survives restart.

        Called from CodeModeManager.enter(). last_active_at refreshes on
        every call so newest projects rise to the top of dashboards.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    metadata = json.dumps(
                        {
                            "origin": "code_mode",
                            "project_root": project_root,
                            "project_name": project_name,
                        }
                    )
                    await db.execute(
                        "INSERT INTO sessions(id, created_at, metadata) "
                        "VALUES (?, ?, ?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (base_session_id, time.time(), metadata),
                    )
                    await db.execute(
                        "INSERT INTO session_delivery_state("
                        "session_id, epoch, deleted_at, reason) "
                        "VALUES (?, 0, NULL, NULL) "
                        "ON CONFLICT(session_id) DO UPDATE SET "
                        "deleted_at=NULL, reason=NULL",
                        (base_session_id,),
                    )
                    await db.execute(
                        """
                        INSERT INTO code_sessions(
                            base_session_id, code_session_id,
                            project_root, project_name,
                            created_at, last_active_at
                        ) VALUES (?, ?, ?, ?, julianday('now'), julianday('now'))
                        ON CONFLICT(base_session_id) DO UPDATE SET
                            code_session_id = excluded.code_session_id,
                            project_root    = excluded.project_root,
                            project_name    = excluded.project_name,
                            last_active_at  = julianday('now')
                        """,
                        (base_session_id, code_session_id, project_root, project_name),
                    )
                    await db.commit()

        await self._with_retry(_do)

    # ── FEAT-A4 (superpowers): plan-confirm 硬门 awaiting plan sidecar ──
    # F5/HMR rehydration 从 SessionDB 重载会丢前端临时的 awaiting plan
    # 消息（[执行]/[取消] 按钮消失）。这三个方法把 awaiting plan 持久化到
    # session_plans sidecar 表，使面板重载后可恢复。不走 append_message →
    # 不触发 VectorWorker embed / FTS5，plan JSON 不污染语义检索。

    async def upsert_session_plan(
        self,
        session_id: str,
        rationale: str,
        steps: list[dict[str, Any]],
        awaiting: bool,
    ) -> None:
        """记一条 awaiting plan（同 session 覆盖）。

        关键（SW-1）：先确保 DB 初始化 + session_plans 表就绪，使得在
        「从未调过 ensure 的全新 DB」上 upsert 也能自带建表，而不是静默
        被「no such table」吞掉。
        """
        if not self._initialized:
            await self.initialize()
        # 自带建表（幂等）— 全新 DB 上保证 session_plans 存在。
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        steps_json = json.dumps(steps or [], ensure_ascii=False)
        now = time.time()

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO session_plans(
                            session_id, rationale, steps_json, awaiting, ts
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            rationale  = excluded.rationale,
                            steps_json = excluded.steps_json,
                            awaiting   = excluded.awaiting,
                            ts         = excluded.ts
                        """,
                        (
                            session_id,
                            rationale or "",
                            steps_json,
                            1 if awaiting else 0,
                            now,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def get_session_plan(self, session_id: str) -> Optional[dict[str, Any]]:
        """读 awaiting plan；无行返 None。steps 已 json.loads，awaiting 已 bool。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> Optional[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT session_id, rationale, steps_json, awaiting, ts
                    FROM session_plans WHERE session_id = ?
                    """,
                    (session_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    return None
                try:
                    steps = json.loads(row[2]) if row[2] else []
                except (ValueError, TypeError):
                    steps = []
                return {
                    "session_id": row[0],
                    "rationale": row[1] or "",
                    "steps": steps,
                    "awaiting": bool(row[3]),
                    "ts": row[4],
                }

        return await self._with_retry(_do)

    async def clear_session_plan_awaiting(self, session_id: str) -> None:
        """清 awaiting 标记（UPDATE awaiting=0）。幂等，可重复调（无行也安全）。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
        await ensure_memory_v2_tables(self._db_path)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "UPDATE session_plans SET awaiting = 0 WHERE session_id = ?",
                        (session_id,),
                    )
                    await db.commit()

        await self._with_retry(_do)

    # ───────────────────── goal-completion FP-1 (WI-1.1) ──────────────
    async def upsert_session_goal(
        self,
        *,
        goal_id: str,
        session_id: str,
        text: str,
        status: str,
        progress: float,
        criteria: Optional[str],
        max_iterations: int,
        iterations_used: int,
        set_at: float,
        updated_at: float,
    ) -> None:
        """落一条 goal（同 goal_id 覆盖）。同构 upsert_session_plan：
        自带建表（全新 DB 也能 upsert）+ _write_lock + _with_retry。
        ⚠️ 用 goal 专用 ensure（非共享 ensure_memory_v2_tables），守 flag-OFF
        字节基线：goal_mode OFF 不落库 → session_goals 表永不建（R-T5）。
        """
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_session_goals_table
        await ensure_session_goals_table(self._db_path)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO session_goals(
                            goal_id, session_id, text, status, progress,
                            criteria, max_iterations, iterations_used,
                            set_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(goal_id) DO UPDATE SET
                            text            = excluded.text,
                            status          = excluded.status,
                            progress        = excluded.progress,
                            criteria        = excluded.criteria,
                            max_iterations  = excluded.max_iterations,
                            iterations_used = excluded.iterations_used,
                            updated_at      = excluded.updated_at
                        """,
                        (
                            goal_id, session_id, text, status, progress,
                            criteria, max_iterations, iterations_used,
                            set_at, updated_at,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def project_goal_terminal(self, goal_id: str, status: str) -> bool:
        """Strict, idempotent CAS used by durable Goal delivery sinks."""
        if status not in {"done", "abandoned"}:
            raise ValueError("invalid Goal terminal status")
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_session_goals_table
        await ensure_session_goals_table(self._db_path)

        async def _do() -> bool:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "UPDATE session_goals SET status=?,updated_at=? "
                        "WHERE goal_id=? AND status='active'",
                        (status, time.time(), goal_id),
                    )
                    row = await (await db.execute(
                        "SELECT status FROM session_goals WHERE goal_id=?",
                        (goal_id,),
                    )).fetchone()
                    await db.commit()
                    return row is not None and str(row[0]) == status

        return await self._with_retry(_do)

    async def get_active_goals(self, session_id: str) -> list[dict[str, Any]]:
        """读某 session 的 active 目标，updated_at 倒序（最新在前）。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_session_goals_table
        await ensure_session_goals_table(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT goal_id, session_id, text, status, progress,
                           criteria, max_iterations, iterations_used,
                           set_at, updated_at
                    FROM session_goals
                    WHERE session_id = ? AND status = 'active'
                    ORDER BY updated_at DESC
                    """,
                    (session_id,),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    async def list_active_goals(self) -> list[dict[str, Any]]:
        """启动恢复用：全库所有 active 目标。"""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_session_goals_table
        await ensure_session_goals_table(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT goal_id, session_id, text, status, progress,
                           criteria, max_iterations, iterations_used,
                           set_at, updated_at
                    FROM session_goals WHERE status = 'active'
                    ORDER BY updated_at DESC
                    """
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    @staticmethod
    def _goal_row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "goal_id": row[0],
            "session_id": row[1],
            "text": row[2],
            "status": row[3],
            "progress": row[4],
            "criteria": row[5],
            "max_iterations": row[6],
            "iterations_used": row[7],
            "set_at": row[8],
            "updated_at": row[9],
        }

    # ───────────────────── goal-completion FP-2 (WI-1.2) ─────────────
    # goal_tasks CRUD + atomic claim.
    # ⚠️ 用 goal_tasks 专用 ensure（非共享 ensure_memory_v2_tables），
    # 守 flag-OFF 字节基线：goal_mode OFF 不落库 → goal_tasks 表永不建（R-T5）。

    async def create_goal_task(
        self,
        *,
        task_id: str,
        goal_id: str,
        session_id: str,
        title: str,
        depends_on: list[str],
        created_at: float,
        updated_at: float,
    ) -> None:
        """Insert a new goal_task row. depends_on stored as JSON."""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_goal_tasks_table
        await ensure_goal_tasks_table(self._db_path)

        depends_json = json.dumps(depends_on, ensure_ascii=False)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO goal_tasks(
                            task_id, goal_id, session_id, title, status,
                            depends_on, claimed_by, result,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
                        """,
                        (
                            task_id, goal_id, session_id, title,
                            depends_json, created_at, updated_at,
                        ),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def get_goal_task(self, task_id: str) -> dict[str, Any] | None:
        """Read a single goal_task by task_id; None if not found."""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_goal_tasks_table
        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> dict[str, Any] | None:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT task_id, goal_id, session_id, title, status,
                           depends_on, claimed_by, result, created_at, updated_at
                    FROM goal_tasks WHERE task_id = ?
                    """,
                    (task_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                if row is None:
                    return None
                return self._goal_task_row_to_dict(row)

        return await self._with_retry(_do)

    async def list_goal_tasks(self, goal_id: str) -> list[dict[str, Any]]:
        """List all tasks for a goal, ordered by created_at ASC."""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_goal_tasks_table
        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """
                    SELECT task_id, goal_id, session_id, title, status,
                           depends_on, claimed_by, result, created_at, updated_at
                    FROM goal_tasks
                    WHERE goal_id = ?
                    ORDER BY created_at ASC
                    """,
                    (goal_id,),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [self._goal_task_row_to_dict(r) for r in rows]

        return await self._with_retry(_do)

    async def update_goal_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
        claimed_by: str | None = None,
        updated_at: float,
    ) -> None:
        """Partial update: only non-None kwargs are SET."""
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_goal_tasks_table
        await ensure_goal_tasks_table(self._db_path)

        # Build SET clause dynamically
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [updated_at]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        if claimed_by is not None:
            sets.append("claimed_by = ?")
            params.append(claimed_by)
        params.append(task_id)

        set_clause = ", ".join(sets)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        f"UPDATE goal_tasks SET {set_clause} WHERE task_id = ?",
                        tuple(params),
                    )
                    await db.commit()

        await self._with_retry(_do)

        # If marking done, backfill progress on session_goals
        if status == "done":
            await self._backfill_goal_progress(task_id, updated_at)

    async def _backfill_goal_progress(
        self, task_id: str, now: float
    ) -> None:
        """After a task is marked done, recompute and persist progress
        = done_count / total for the owning goal.
        """
        task_row = await self.get_goal_task(task_id)
        if task_row is None:
            return
        goal_id = task_row["goal_id"]
        session_id = task_row["session_id"]
        tasks = await self.list_goal_tasks(goal_id)
        if not tasks:
            return
        total = len(tasks)
        done = sum(1 for t in tasks if t["status"] == "done")
        progress = done / total

        # Read the current goal row to preserve its other fields
        goals = await self.get_active_goals(session_id)
        goal_row = next((g for g in goals if g["goal_id"] == goal_id), None)
        if goal_row is None:
            # Goal might not exist (no session_goals row) — skip safely
            return

        try:
            await self.upsert_session_goal(
                goal_id=goal_id,
                session_id=session_id,
                text=goal_row["text"],
                status=goal_row["status"],
                progress=progress,
                criteria=goal_row["criteria"],
                max_iterations=goal_row["max_iterations"],
                iterations_used=goal_row["iterations_used"],
                set_at=goal_row["set_at"],
                updated_at=now,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("goal progress backfill failed: %s", exc)

    async def claim_ready_goal_task(
        self,
        goal_id: str,
        agent_id: str,
        now: float,
    ) -> dict[str, Any] | None:
        """ATOMIC claim under _write_lock.

        (1) SELECT all tasks for goal_id
        (2) In Python, find first `status='pending'` whose `depends_on` are
            ALL `status='done'`
        (3) If found, UPDATE that row SET status='claimed', claimed_by=agent_id,
            updated_at=now WHERE task_id=? AND status='pending'
            (guard against race between coroutines)
        (4) Return the claimed row dict or None.

        Same-process asyncio.Lock (per T5 spike conclusion) ensures
        concurrent coroutines serialize.
        """
        if not self._initialized:
            await self.initialize()
        from deskpet.memory.memory_v2_schema import ensure_goal_tasks_table
        await ensure_goal_tasks_table(self._db_path)

        async def _do() -> dict[str, Any] | None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    # Load all tasks for this goal
                    cur = await db.execute(
                        """
                        SELECT task_id, goal_id, session_id, title, status,
                               depends_on, claimed_by, result, created_at, updated_at
                        FROM goal_tasks WHERE goal_id = ?
                        ORDER BY created_at ASC
                        """,
                        (goal_id,),
                    )
                    rows = await cur.fetchall()
                    await cur.close()

                    if not rows:
                        return None

                    # Build status index for dependency check
                    # Column indices: 0=task_id, 4=status, 5=depends_on
                    status_map: dict[str, str] = {}
                    for r in rows:
                        status_map[r[0]] = r[4]

                    # Find first pending task whose all deps are done
                    candidate = None
                    for r in rows:
                        if r[4] != "pending":
                            continue
                        try:
                            deps: list[str] = json.loads(r[5]) if r[5] else []
                        except (ValueError, TypeError):
                            deps = []
                        if all(status_map.get(d) == "done" for d in deps):
                            candidate = r
                            break

                    if candidate is None:
                        return None

                    # Attempt the claim with status='pending' guard
                    cur2 = await db.execute(
                        """
                        UPDATE goal_tasks
                        SET status='claimed', claimed_by=?, updated_at=?
                        WHERE task_id=? AND status='pending'
                        """,
                        (agent_id, now, candidate[0]),
                    )
                    await db.commit()
                    rows_affected = cur2.rowcount or 0
                    await cur2.close()

                    if rows_affected == 0:
                        # Another coroutine claimed it between our SELECT and UPDATE
                        return None

                    # Return the updated row
                    cur3 = await db.execute(
                        """
                        SELECT task_id, goal_id, session_id, title, status,
                               depends_on, claimed_by, result, created_at, updated_at
                        FROM goal_tasks WHERE task_id = ?
                        """,
                        (candidate[0],),
                    )
                    final_row = await cur3.fetchone()
                    await cur3.close()
                    return self._goal_task_row_to_dict(final_row) if final_row else None

        return await self._with_retry(_do)

    @staticmethod
    def _goal_task_row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a goal_tasks SELECT row to dict.
        Column order must match SELECT statement in all queries above:
          0=task_id, 1=goal_id, 2=session_id, 3=title, 4=status,
          5=depends_on, 6=claimed_by, 7=result, 8=created_at, 9=updated_at
        """
        try:
            depends_on: list[str] = json.loads(row[5]) if row[5] else []
        except (ValueError, TypeError):
            depends_on = []
        return {
            "task_id": row[0],
            "goal_id": row[1],
            "session_id": row[2],
            "title": row[3],
            "status": row[4],
            "depends_on": depends_on,
            "claimed_by": row[6],
            "result": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    async def list_code_sessions(self) -> list[dict[str, Any]]:
        """P4-S25 B4: read the persisted project list, newest first.

        Used by CodeModeManager.load_persisted() at startup to repopulate
        the in-memory state map. Order doesn't strictly matter (both
        sidebar and dashboard sort their own way) but newest-first is
        a sensible default if anyone iterates raw.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT base_session_id, code_session_id,
                           project_root, project_name,
                           created_at, last_active_at
                    FROM code_sessions
                    ORDER BY last_active_at DESC
                    """
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "base_session_id": r[0],
                        "code_session_id": r[1],
                        "project_root": r[2],
                        "project_name": r[3],
                        "created_at": r[4],
                        "last_active_at": r[5],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def delete_code_session(self, base_session_id: str) -> int:
        """P4-S25 B4: remove a persisted project enrollment.

        Called from the `code_session_delete` IPC handler alongside
        delete_code_todos. We do NOT cascade-delete `messages` rows —
        chat history for that code_session_id stays in the DB so re-
        adding the same project root later resumes the same thread.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_sessions WHERE base_session_id = ?",
                        (base_session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)

    # ─────────────────────────────────────────────────────────────────
    # P5-S1: supervisor_hints — audit log for the watchdog's interventions.
    # Every nudge / ask_user / cancel_coerced action emitted by the
    # supervisor LLM gets a row here, plus a follow-up ``user_choice``
    # row when the user clicks a bubble button. Used for: debugging
    # ("why did agent suddenly try a different approach?"), Settings UI
    # count badges, and a future cost-estimate feature.
    # ─────────────────────────────────────────────────────────────────

    async def append_supervisor_hint(
        self,
        *,
        session_id: str,
        alert_id: str,
        hint_text: str,
        action: str,
        severity: str,
        diagnosis: str = "",
        user_button: str | None = None,
        ts: int | None = None,
    ) -> int:
        """Insert one supervisor_hints row. Returns the new row id."""
        if not self._initialized:
            await self.initialize()
        import time as _time

        ts_val = int(ts if ts is not None else _time.time())

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        """
                        INSERT INTO supervisor_hints(
                            session_id, alert_id, hint_text, action, severity,
                            diagnosis, user_button, ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            alert_id,
                            hint_text,
                            action,
                            severity,
                            diagnosis,
                            user_button,
                            ts_val,
                        ),
                    )
                    new_id = int(cursor.lastrowid or 0)
                    await cursor.close()
                    await db.commit()
                    return new_id

        return await self._with_retry(_do)

    async def list_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read supervisor audit rows newest-first.

        Filter by ``session_id`` to scope to one session; ``since_ts``
        for "today's interventions" style queries; ``limit`` caps result
        set so a long-lived backend doesn't dump 10K rows over IPC.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(int(limit))
                cursor = await db.execute(
                    f"""
                    SELECT id, session_id, alert_id, hint_text, action,
                           severity, diagnosis, user_button, ts
                    FROM supervisor_hints
                    {where}
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "id": r[0],
                        "session_id": r[1],
                        "alert_id": r[2],
                        "hint_text": r[3],
                        "action": r[4],
                        "severity": r[5],
                        "diagnosis": r[6],
                        "user_button": r[7],
                        "ts": r[8],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def count_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
    ) -> int:
        """Count audit rows matching filter (used by Settings UI badge)."""
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM supervisor_hints {where}",
                    tuple(params),
                )
                row = await cursor.fetchone()
                await cursor.close()
                return int(row[0]) if row else 0

        return await self._with_retry(_do)

    async def delete_code_todos(self, session_id: str) -> int:
        """P4-S24 followup: wipe all todos for a code session.

        Used by ``code_session_delete`` IPC when the user removes a
        project from the code panel. Returns the number of rows deleted
        (0 if the session had none).

        We deliberately do NOT touch ``messages`` rows — chat history
        for the project survives so the user can resume the same
        ``code-<sha>`` session later if they re-add the same project
        root. Same philosophy as ``CodeModeManager.exit``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_todos WHERE session_id = ?",
                        (session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)


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
