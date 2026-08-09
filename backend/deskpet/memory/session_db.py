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
import hashlib
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

from deskpet.agent.context_usage import (
    ContextUsageSampleConflict,
    ContextUsageStateConflict,
    ContextUsageStateV2,
    binding_only_state,
    normalize_context_usage_sample,
    reduce_context_usage_state,
)
from deskpet.memory.schema import initialize_state_db
from deskpet.memory.memory_v2_schema import SESSION_TITLES_DDL
from deskpet.memory.companion_message_projection import (
    COMPANION_REDACTION_TOMBSTONE,
    COMPANION_REDACTION_TOMBSTONE_HASH,
    CurrentCompanionProjection,
    OwnerMemoryReadScopeV1,
    TrustedCompanionOwner,
    TrustedCompanionProjectionRoute,
    canonical_hash as companion_canonical_hash,
    canonical_json as companion_canonical_json,
)

log = logging.getLogger(__name__)

# P4-S2 hook 类型：(message_id, content) → awaitable None。
# MemoryManager / VectorWorker 在此接入 "消息落盘后异步跑 embedding"。
OnMessageWritten = Callable[[int, str], Awaitable[None]]


class ProviderBindingConflict(RuntimeError):
    """A binding mutation lost its compare-and-swap race."""

    code = "provider_binding_conflict"

    def __init__(self) -> None:
        super().__init__(self.code)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

MESSAGE_PROJECTION_KINDS = frozenset(
    {
        "legacy_message", "user_message", "assistant_message", "tool_message",
        "system_message", "final_assistant", "workflow_progress",
        "workflow_accepted", "workflow_decision", "workflow_final_status",
        "artifact_card", "companion_event",
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
        "workflow_final_status", "artifact_card", "companion_event",
    }
)


def _coerce_trusted_owner(value: Any) -> TrustedCompanionOwner:
    if isinstance(value, TrustedCompanionOwner):
        return value
    frozen_owner = getattr(value, "owner", None)
    if frozen_owner is not None and hasattr(value, "binding_epoch"):
        return TrustedCompanionOwner(
            profile_id=str(getattr(frozen_owner, "profile_id")),
            profile_generation=int(
                getattr(
                    frozen_owner,
                    "profile_generation",
                    getattr(frozen_owner, "generation", 0),
                )
            ),
            binding_epoch=int(getattr(value, "binding_epoch")),
            owner_kind="companion_profile",
        )
    if isinstance(value, dict):
        return TrustedCompanionOwner(
            profile_id=str(value["profile_id"]),
            profile_generation=int(
                value.get("profile_generation", value.get("generation"))
            ),
            binding_epoch=int(value["binding_epoch"]),
            owner_kind=str(value.get("owner_kind", "companion_profile")),
        )
    return TrustedCompanionOwner(
        profile_id=str(getattr(value, "profile_id")),
        profile_generation=int(
            getattr(value, "profile_generation", getattr(value, "generation", 0))
        ),
        binding_epoch=int(getattr(value, "binding_epoch")),
        owner_kind=str(getattr(value, "owner_kind", "companion_profile")),
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


#: 保留会话 id：companion 主线程，同时是应用的兜底会话——删除当前会话后
#: 前端必然回落到它。它与用户自建会话的生命周期语义不同：删除它等于"清空内容"，
#: 而不是"退役这个会话"。若按普通会话墓碑化其属主行，`bind_session_owner_if_absent`
#: 之后会一直抛 companion_session_owner_tombstoned，兜底目标就**永久不可用**
#: （r5 真机实测：删过一次 default 之后，任何删除当前会话的操作都会把用户丢进
#: 一个发什么都被拒的会话里，且报的是误导性的 companion_identity_not_ready）。
RESERVED_DEFAULT_SESSION_ID = "default"

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
        await self._repair_excluded_memory_artifacts()

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

    async def record_context_usage_sample(
        self,
        sample: dict[str, Any],
    ) -> str:
        """Append one immutable fact and advance ContextUsageStateV2.

        The durable source identity is idempotent.  Reusing it with a
        different canonical payload is corruption and never updates history.
        """

        if not self._initialized:
            await self.initialize()
        normalized = normalize_context_usage_sample(sample)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = await db.execute(
                            """INSERT INTO session_context_usage_history(
                            sample_id,session_id,source_event_id,payload_hash,
                            run_id,request_id,attempt_id,event_type,
                            tokens_before,tokens_after,prompt_tokens,
                            completion_tokens,cached_tokens,context_window,
                            effective_ceiling,estimate_method,provider_id,
                            model_id,binding_epoch,based_on_sample_id,
                            metadata_json,completed_at,created_at
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(session_id,source_event_id) DO NOTHING""",
                            (
                                normalized["sample_id"],
                                normalized["session_id"],
                                normalized["source_event_id"],
                                normalized["payload_hash"],
                                normalized["run_id"],
                                normalized["request_id"],
                                normalized["attempt_id"],
                                normalized["event_type"],
                                normalized["tokens_before"],
                                normalized["tokens_after"],
                                normalized["prompt_tokens"],
                                normalized["completion_tokens"],
                                normalized["cached_tokens"],
                                normalized["context_window"],
                                normalized["effective_ceiling"],
                                normalized["estimate_method"],
                                normalized["provider_id"],
                                normalized["model_id"],
                                normalized["binding_epoch"],
                                normalized["based_on_sample_id"],
                                json.dumps(
                                    normalized["metadata"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    default=str,
                                ),
                                normalized["completed_at"],
                                normalized["created_at"],
                            ),
                        )
                        inserted = int(cursor.rowcount or 0) == 1
                        await cursor.close()
                        existing_cursor = await db.execute(
                            """SELECT *
                               FROM session_context_usage_history
                               WHERE session_id=? AND source_event_id=?""",
                            (
                                normalized["session_id"],
                                normalized["source_event_id"],
                            ),
                        )
                        existing = await existing_cursor.fetchone()
                        await existing_cursor.close()
                        if existing is None:
                            raise RuntimeError("context usage history insert disappeared")
                        if str(existing["payload_hash"]) != normalized["payload_hash"]:
                            raise ContextUsageSampleConflict(
                                ContextUsageSampleConflict.code
                            )
                        normalized["sample_id"] = str(existing["sample_id"])
                        durable_fact = dict(normalized)
                        for key in (
                            "sample_id", "source_event_id", "event_type",
                            "tokens_before", "tokens_after", "prompt_tokens",
                            "completion_tokens", "cached_tokens", "context_window",
                            "effective_ceiling", "estimate_method", "provider_id",
                            "model_id", "binding_epoch", "based_on_sample_id",
                            "completed_at", "created_at",
                        ):
                            durable_fact[key] = existing[key]

                        state = await self._read_context_usage_state_tx(
                            db, normalized["session_id"]
                        )
                        had_state = state is not None
                        if state is None:
                            binding = await self._read_context_usage_binding_tx(
                                db, normalized["session_id"]
                            )
                            state = binding_only_state(
                                session_id=normalized["session_id"],
                                state_version=0,
                                binding_epoch=int(binding["binding_epoch"]),
                                provider_id=binding["provider_id"],
                                model_id=binding["preferred_model"],
                                availability=str(binding["availability"]),
                                updated_at=float(normalized["created_at"]),
                            )
                        reduced = reduce_context_usage_state(state, durable_fact)
                        if not had_state and reduced == state:
                            reduced = ContextUsageStateV2(
                                **{**state.__dict__, "state_version": 1}
                            )
                        if inserted or reduced != state:
                            await self._write_context_usage_state_tx(
                                db, state if had_state else None, reduced
                            )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        return str(normalized["sample_id"])

    async def list_context_usage_history(
        self,
        session_id: str,
        *,
        limit: int = 256,
    ) -> list[dict[str, Any]]:
        """Return oldest-to-newest durable Context history for one session."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            return []
        bounded = max(1, min(int(limit), 2_000))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM (
                SELECT * FROM session_context_usage_history
                WHERE session_id=?
                ORDER BY created_at DESC,sample_id DESC LIMIT ?
                ) ORDER BY created_at,sample_id""",
                (sid, bounded),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._context_usage_sample_from_row(row) for row in rows]

    @staticmethod
    def _context_usage_sample_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        event_type = str(row["event_type"])
        return {
            "sample_id": str(row["sample_id"]),
            "session_id": str(row["session_id"]),
            "source_event_id": str(row["source_event_id"]),
            "payload_hash": str(row["payload_hash"]),
            "run_id": row["run_id"],
            "request_id": row["request_id"],
            "attempt_id": row["attempt_id"],
            "event_type": event_type,
            "source": (
                "compacted" if event_type == "compaction" else "measured"
            ),
            "tokens_before": row["tokens_before"],
            "tokens_after": int(row["tokens_after"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "cached_tokens": int(row["cached_tokens"]),
            "context_window": int(row["context_window"]),
            "effective_ceiling": int(row["effective_ceiling"]),
            "estimate_method": str(row["estimate_method"]),
            "provider_id": row["provider_id"],
            "model_id": row["model_id"],
            "binding_epoch": int(row["binding_epoch"]),
            "based_on_sample_id": row["based_on_sample_id"],
            "metadata": metadata,
            "completed_at": float(row["completed_at"]),
            "created_at": float(row["created_at"]),
        }

    async def get_context_usage_sample(
        self, session_id: str, sample_id: str
    ) -> dict[str, Any] | None:
        """Read one exact immutable Context sample without history limits."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        exact_sample_id = str(sample_id or "").strip()
        if not sid or not exact_sample_id:
            return None
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM session_context_usage_history
                   WHERE session_id=? AND sample_id=?""",
                (sid, exact_sample_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return (
            self._context_usage_sample_from_row(row)
            if row is not None
            else None
        )

    @staticmethod
    def _context_usage_state_from_row(row: aiosqlite.Row) -> ContextUsageStateV2:
        return ContextUsageStateV2(
            session_id=str(row["session_id"]),
            source=str(row["source"]),
            sample_id=row["sample_id"],
            state_version=int(row["state_version"]),
            binding_epoch=int(row["binding_epoch"]),
            availability=str(row["availability"]),
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            context_window=int(row["context_window"]),
            tokens=int(row["tokens"]),
            effective_ceiling=int(row["effective_ceiling"]),
            completion_tokens=int(row["completion_tokens"]),
            cached_tokens=int(row["cached_tokens"]),
            based_on_sample_id=row["based_on_sample_id"],
            source_event_id=row["source_event_id"],
            source_completed_at=float(row["source_completed_at"]),
            has_measurement=bool(row["has_measurement"]),
            legacy_incomplete=bool(row["legacy_incomplete"]),
            updated_at=float(row["updated_at"]),
        )

    async def _read_context_usage_state_tx(
        self, db: aiosqlite.Connection, session_id: str
    ) -> ContextUsageStateV2 | None:
        cursor = await db.execute(
            "SELECT * FROM session_context_usage_state_v2 WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._context_usage_state_from_row(row) if row is not None else None

    async def _read_context_usage_binding_tx(
        self, db: aiosqlite.Connection, session_id: str
    ) -> dict[str, Any]:
        columns_cursor = await db.execute("PRAGMA table_info(code_session_provider)")
        columns = {str(row[1]) for row in await columns_cursor.fetchall()}
        await columns_cursor.close()
        has_epoch = "binding_epoch" in columns
        cursor = await db.execute(
            "SELECT provider_id,preferred_model"
            + (",binding_epoch" if has_epoch else "")
            + " FROM code_session_provider WHERE base_session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return {
                "provider_id": None,
                "preferred_model": None,
                "binding_epoch": 0,
                "availability": "unknown",
            }
        return {
            "provider_id": row[0],
            "preferred_model": row[1],
            "binding_epoch": int(row[2]) if has_epoch else 0,
            "availability": "available",
        }

    async def _write_context_usage_state_tx(
        self,
        db: aiosqlite.Connection,
        previous: ContextUsageStateV2 | None,
        state: ContextUsageStateV2,
    ) -> None:
        record = state.to_record()
        columns = (
            "session_id,source,sample_id,state_version,binding_epoch,availability,"
            "provider_id,model_id,context_window,tokens,effective_ceiling,"
            "completion_tokens,cached_tokens,based_on_sample_id,source_event_id,"
            "source_completed_at,has_measurement,legacy_incomplete,updated_at"
        )
        values = tuple(record[name] for name in columns.split(","))
        if previous is None or previous.state_version == 0:
            cursor = await db.execute(
                f"INSERT INTO session_context_usage_state_v2({columns}) "
                f"VALUES ({','.join('?' for _ in values)}) "
                "ON CONFLICT(session_id) DO NOTHING",
                values,
            )
        else:
            assignments = ",".join(
                f"{name}=?" for name in columns.split(",") if name != "session_id"
            )
            cursor = await db.execute(
                f"UPDATE session_context_usage_state_v2 SET {assignments} "
                "WHERE session_id=? AND state_version=?",
                tuple(values[1:]) + (state.session_id, previous.state_version),
            )
        if int(cursor.rowcount or 0) != 1:
            await cursor.close()
            raise ContextUsageStateConflict(ContextUsageStateConflict.code)
        await cursor.close()

    async def set_context_usage_binding_state(
        self,
        session_id: str,
        *,
        binding_epoch: int,
        provider_id: str | None,
        model_id: str | None,
        availability: str = "available",
        updated_at: float | None = None,
    ) -> dict[str, Any]:
        """Advance public authority after Task 1 commits a binding mutation."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        result: ContextUsageStateV2 | None = None

        async def _do() -> None:
            nonlocal result
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        previous = await self._read_context_usage_state_tx(db, sid)
                        result = await self.advance_context_usage_binding_state_tx(
                            db,
                            session_id=sid,
                            binding_epoch=binding_epoch,
                            provider_id=provider_id,
                            model_id=model_id,
                            availability=availability,
                            updated_at=updated_at,
                            previous=previous,
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        assert result is not None
        return result.to_public_payload()

    async def advance_context_usage_binding_state_tx(
        self,
        db: aiosqlite.Connection,
        *,
        session_id: str,
        binding_epoch: int,
        provider_id: str | None,
        model_id: str | None,
        availability: str = "available",
        updated_at: float | None = None,
        previous: ContextUsageStateV2 | None = None,
    ) -> ContextUsageStateV2:
        """Write binding-only authority inside the caller's binding txn.

        Task 1's set/clear/inherit paths call this helper before committing
        their ``code_session_provider`` mutation, so the new epoch and public
        Context state are observed atomically.
        """

        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        db.row_factory = aiosqlite.Row
        current = previous
        if current is None:
            current = await self._read_context_usage_state_tx(db, sid)
        state = binding_only_state(
            session_id=sid,
            state_version=(current.state_version + 1 if current else 1),
            binding_epoch=max(0, int(binding_epoch)),
            provider_id=provider_id,
            model_id=model_id,
            availability=availability,
            updated_at=float(updated_at or time.time()),
        )
        await self._write_context_usage_state_tx(db, current, state)
        return state

    async def get_context_usage_state(self, session_id: str) -> dict[str, Any]:
        """Read or atomically rebuild the V2 materialized authority."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        rebuilt: ContextUsageStateV2 | None = None

        async def _do() -> None:
            nonlocal rebuilt
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        current = await self._read_context_usage_state_tx(db, sid)
                        if current is not None:
                            rebuilt = current
                            await db.commit()
                            return
                        binding = await self._read_context_usage_binding_tx(db, sid)
                        current = binding_only_state(
                            session_id=sid,
                            state_version=0,
                            binding_epoch=int(binding["binding_epoch"]),
                            provider_id=binding["provider_id"],
                            model_id=binding["preferred_model"],
                            availability=str(binding["availability"]),
                            updated_at=time.time(),
                        )
                        legacy_incomplete = False
                        after_created = -1.0
                        after_sample = ""
                        while True:
                            cursor = await db.execute(
                                """SELECT * FROM session_context_usage_history
                                   WHERE session_id=? AND
                                     (created_at>? OR (created_at=? AND sample_id>?))
                                   ORDER BY created_at,sample_id LIMIT 256""",
                                (sid, after_created, after_created, after_sample),
                            )
                            rows = await cursor.fetchall()
                            await cursor.close()
                            if not rows:
                                break
                            for row in rows:
                                try:
                                    metadata = json.loads(
                                        row["metadata_json"] or "{}"
                                    )
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    metadata = {}
                                fact = {
                                    key: row[key]
                                    for key in (
                                        "sample_id", "source_event_id", "event_type",
                                        "tokens_after", "completion_tokens", "cached_tokens",
                                        "context_window", "effective_ceiling", "provider_id",
                                        "model_id", "binding_epoch", "based_on_sample_id",
                                        "completed_at",
                                    )
                                }
                                fact["model_id"] = fact["model_id"] or metadata.get("model")
                                if fact["event_type"] == "compaction" and not fact["based_on_sample_id"]:
                                    legacy_incomplete = True
                                    continue
                                current = reduce_context_usage_state(current, fact)
                            after_created = float(rows[-1]["created_at"])
                            after_sample = str(rows[-1]["sample_id"])
                        if legacy_incomplete:
                            current = ContextUsageStateV2(
                                **{
                                    **current.__dict__,
                                    "legacy_incomplete": True,
                                }
                            )
                        if current.state_version == 0:
                            current = ContextUsageStateV2(
                                **{**current.__dict__, "state_version": 1}
                            )
                        await self._write_context_usage_state_tx(db, None, current)
                        await db.commit()
                        rebuilt = current
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        assert rebuilt is not None
        return rebuilt.to_public_payload()

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
                    default_epoch_takeover = False
                    if row is not None:
                        if (
                            str(row[0]) != owner.owner_kind
                            or str(row[1]) != owner.profile_id
                            or int(row[2]) != owner.profile_generation
                            or int(row[3]) != owner.binding_epoch
                        ):
                            # WBUI-DEF-COMP-01：profile 纪元迁移（如 legacy↔relay
                            # 切换）只影响新会话的绑定参数，保留会话 default 的
                            # owner 行会永远停在旧纪元——之后每条进 default 的消息
                            # 都在这里被拒，且无 UI 恢复入口。对 default 做窄自愈：
                            # 仅当身份本体相同（owner_kind+profile_id 一致）且纪元/
                            # 世代只进不退时，删旧行落到重绑路径（触发器禁 UPDATE，
                            # DELETE+INSERT 是既有 tombstoned 自愈同款合法通道）。
                            # 普通会话与身份不同/纪元回退的情况维持硬拒绝。
                            if (
                                sid == RESERVED_DEFAULT_SESSION_ID
                                and str(row[0]) == owner.owner_kind
                                and str(row[1]) == owner.profile_id
                                and int(row[2]) <= owner.profile_generation
                                and int(row[3]) <= owner.binding_epoch
                            ):
                                await db.execute(
                                    "DELETE FROM companion_session_owners"
                                    " WHERE session_id=?",
                                    (sid,),
                                )
                                default_epoch_takeover = True
                                row = None
                            else:
                                await db.rollback()
                                raise RuntimeError(
                                    "companion_session_owner_rebind_forbidden"
                                )
                        if row is not None and str(row[4]) != "active":
                            if sid != RESERVED_DEFAULT_SESSION_ID:
                                await db.rollback()
                                raise RuntimeError(
                                    "companion_session_owner_tombstoned"
                                )
                            # 保留会话的存量自愈：早于本次修复的版本会把 default
                            # 当普通会话墓碑化，之后它永远绑不上（兜底目标死锁）。
                            # 触发器禁止 UPDATE 复活，但删掉旧行后重新插入是合法的，
                            # 且 clear() 早已把它的消息清空——没有历史被"复活"。
                            await db.execute(
                                "DELETE FROM companion_session_owners"
                                " WHERE session_id=?",
                                (sid,),
                            )
                            # 落到下方的新建绑定路径（不要走"返回既有行"分支）。
                            row = None
                    if row is not None:
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
                    cursor = await db.execute(
                        "SELECT 1 FROM sessions WHERE id=?", (sid,)
                    )
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
                    # default 纪元接管时旧消息属于同一 profile_id 的历史，不是
                    # "陌生 legacy 会话被抢注"——放行；其余路径维持零消息约束。
                    if message_count and not default_epoch_takeover:
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
            raise ValueError(
                "session_id, message_id, request_id and turn_id are required"
            )
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
                        raise RuntimeError(
                            "companion_ingress_semantic_fence_mismatch"
                        )
                    envelope = json.loads(str(row["event_envelope_json"]))
                    existing_kind = str(
                        envelope.get("semantic_growth_intent") or "none"
                    )
                    if existing_kind not in allowed:
                        await db.rollback()
                        raise RuntimeError(
                            "companion_ingress_semantic_state_invalid"
                        )
                    effective_kind = (
                        existing_kind if existing_kind != "none" else kind
                    )
                    if (
                        existing_kind != "none"
                        and kind != "none"
                        and existing_kind != kind
                    ):
                        await db.rollback()
                        raise RuntimeError(
                            "companion_ingress_semantic_replay_conflict"
                        )
                    priority = (
                        "blocking"
                        if effective_kind != "none"
                        else str(row["priority"])
                    )
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
                    elif (
                        effective_kind != existing_kind
                        or priority != str(row["priority"])
                    ):
                        await db.rollback()
                        raise RuntimeError(
                            "companion_ingress_semantic_settle_too_late"
                        )
                    cursor = await db.execute(
                        "SELECT * FROM companion_ingress_outbox WHERE outbox_id=?",
                        (row["outbox_id"],),
                    )
                    settled = await cursor.fetchone()
                    await cursor.close()
                    await db.commit()
                    assert settled is not None
                    result = dict(settled)
                    result["event_envelope"] = json.loads(
                        str(result["event_envelope_json"])
                    )
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
                        or int(row["projection_route_version"])
                        != int(expected_route_version)
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
                            or str(receipt["new_payload_hash"])
                            != current_projection.payload_hash
                            or str(receipt["redaction_outbox_id"])
                            != current_projection.redaction_id
                        ):
                            await db.rollback()
                            raise RuntimeError(
                                "companion_projection_redaction_replay_conflict"
                            )
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
                            raise RuntimeError(
                                "companion_projection_redaction_replay_conflict"
                            )
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
                            WHERE {' AND '.join(where)}
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
                    result["event_envelope"] = json.loads(
                        str(result["event_envelope_json"])
                    )
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
                    if (
                        row["status"] != "claimed"
                        or int(row["attempt"]) != int(expected_attempt)
                    ):
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
            ["companion_projection_route", profile_id, profile_generation, route_version, event_kind]
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
                            WHERE {' AND '.join(where)}
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
                    if (
                        row["status"] != "claimed"
                        or int(row["attempt"]) != int(expected_attempt)
                    ):
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
                raise RuntimeError(
                    f"message event conflict has no row: {generic_event_id}"
                )
            if str(row[1]) != projection_kind or str(row[2]) != context_visibility:
                raise RuntimeError(
                    "workflow_message_projection_conflict: "
                    f"event_id={workflow_event_id!r} existing=({row[1]!r},{row[2]!r}) "
                    f"requested=({projection_kind!r},{context_visibility!r})"
                )
            if (
                (None if row[3] is None else str(row[3])) != root_run_id
                or (None if row[4] is None else str(row[4])) != task_scope_id
            ):
                raise RuntimeError(
                    "workflow_message_task_scope_conflict: "
                    f"event_id={workflow_event_id!r}"
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
                    "message_event_projection_conflict: "
                    f"event_id={generic_event_id!r}"
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
                        root_run_id=root_run_id,
                        task_scope_id=task_scope_id,
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

    async def get_message_routing_context(
        self, msg_id: int
    ) -> Optional[dict[str, Any]]:
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
            "root_run_id": (
                None if row["root_run_id"] is None else str(row["root_run_id"])
            ),
            "task_scope_id": (
                None if row["task_scope_id"] is None else str(row["task_scope_id"])
            ),
            "role": str(row["role"]),
            "projection_kind": (
                None
                if row["projection_kind"] is None
                else str(row["projection_kind"])
            ),
        }

    @staticmethod
    async def read_public_message_projection_page_tx(
        db: aiosqlite.Connection,
        *,
        table: str,
        session_id: str,
        root_run_id: str,
        after_created_at: float,
        after_id: int,
        limit: int = 256,
    ) -> list[aiosqlite.Row]:
        """Read one keyset page inside the caller-owned state read cut.

        Only the two projection authorities are accepted as table names; all
        owner keys remain bound parameters.  Keeping the caller's transaction
        open lets Inspector read live messages, archive rows, and provider
        audit data from one explicit state.db cut.
        """

        if table not in {"messages", "messages_archive"}:
            raise ValueError("unsupported public message projection table")
        page_limit = min(512, max(1, int(limit)))
        cursor = await db.execute(
            f"SELECT id,role,content,created_at,workflow_event_id,projection_kind "
            f"FROM {table} WHERE session_id=? AND root_run_id=? "
            "AND (created_at>? OR (created_at=? AND id>?)) "
            "ORDER BY created_at,id LIMIT ?",
            (
                session_id,
                root_run_id,
                float(after_created_at),
                float(after_created_at),
                int(after_id),
                page_limit,
            ),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

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
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
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
                    if session_id == RESERVED_DEFAULT_SESSION_ID:
                        # 保留会话只清内容、不退役：仍推进 scope_version 把陈旧
                        # 投影围栏掉，但属主行保持 active，之后还能重新绑定。
                        await self._advance_reserved_session_scope(
                            db,
                            session_id=session_id,
                            new_epoch=int(epoch_row[0]),
                            now=time.time(),
                        )
                    else:
                        await self._tombstone_companion_route(
                            db,
                            session_id=session_id,
                            new_epoch=int(epoch_row[0]),
                            reason="deleted",
                            now=time.time(),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def _advance_reserved_session_scope(
        self,
        db: aiosqlite.Connection,
        *,
        session_id: str,
        new_epoch: int,
        now: float,
    ) -> None:
        """清空保留会话：推进读作用域与路由 epoch，但不退役属主/路由。"""

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
               SET scope_version=scope_version+1,updated_at=?
               WHERE session_id=? AND status='active'""",
            (now, session_id),
        )
        await db.execute(
            """UPDATE companion_owner_scope_versions
               SET scope_version=scope_version+1,updated_at=?
               WHERE profile_id=? AND profile_generation=?""",
            (now, profile_id, profile_generation),
        )
        await db.execute(
            """UPDATE companion_projection_routes
               SET target_epoch=?,updated_at=?
               WHERE profile_id=? AND profile_generation=?
                 AND target_session_id=? AND status='active'""",
            (int(new_epoch), now, profile_id, profile_generation, session_id),
        )

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

    async def get_session_provider_binding(
        self, session_id: str
    ) -> dict[str, Any]:
        """Return the provider/model override for an ordinary session.

        The underlying table keeps its historical name for an idempotent
        on-disk migration, but new production callers use this mode-neutral
        API.
        """

        return await self.get_code_session_provider_binding(session_id)

    async def get_session_provider_binding_authority(
        self, session_id: str
    ) -> dict[str, Any]:
        """Return incarnation/revision/epoch for trusted production callers."""
        return await self._get_provider_binding_authority(session_id)

    async def set_session_provider_binding(
        self,
        session_id: str,
        provider_id: str | None,
        preferred_model: str | None,
        model_params: dict[str, Any] | None = None,
        *,
        provider_incarnation_id: str | None = None,
        provider_config_revision: int | None = None,
        expected_binding_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Persist a mode-neutral per-session provider/model override."""

        return await self.set_code_session_provider_binding(
            session_id,
            provider_id,
            preferred_model,
            model_params,
            provider_incarnation_id=provider_incarnation_id,
            provider_config_revision=provider_config_revision,
            expected_binding_epoch=expected_binding_epoch,
        )

    async def inherit_session_provider_binding(
        self,
        source_session_id: str,
        target_session_id: str,
        *,
        expected_binding_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Atomically snapshot a session's model binding into a new session.

        A newly-created conversation is expected to keep the model the user
        was looking at when they clicked "new topic".  Copying all three
        fields in one SQLite transaction prevents a first Run from observing
        a half-copied provider/model/params tuple.  An unbound source clears
        the target, making retries deterministic and idempotent.
        """
        source = str(source_session_id or "").strip()
        target = str(target_session_id or "").strip()
        if not source or not target:
            raise ValueError("source_session_id and target_session_id are required")
        if source == target:
            raise ValueError("source and target session must differ")
        if not self._initialized:
            await self.initialize()

        result: dict[str, Any] | None = None

        async def _do() -> None:
            nonlocal result
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    source_cursor = await db.execute(
                        "SELECT provider_id, preferred_model, model_params, "
                        "provider_incarnation_id, provider_config_revision "
                        "FROM code_session_provider WHERE base_session_id = ?",
                        (source,),
                    )
                    source_row = await source_cursor.fetchone()
                    await source_cursor.close()
                    copied = tuple(source_row) if source_row is not None else (None,) * 5
                    target_cursor = await db.execute(
                        "SELECT binding_epoch FROM code_session_provider "
                        "WHERE base_session_id = ?",
                        (target,),
                    )
                    target_row = await target_cursor.fetchone()
                    await target_cursor.close()
                    current_epoch = int(target_row[0]) if target_row else 0
                    if (
                        expected_binding_epoch is not None
                        and expected_binding_epoch != current_epoch
                    ):
                        raise ProviderBindingConflict()
                    next_epoch = current_epoch + 1
                    await db.execute(
                        "INSERT INTO code_session_provider "
                        "(base_session_id, provider_id, preferred_model, model_params, "
                        " provider_incarnation_id, provider_config_revision, "
                        " binding_epoch, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, julianday('now')) "
                        "ON CONFLICT(base_session_id) DO UPDATE SET "
                        " provider_id=excluded.provider_id, "
                        " preferred_model=excluded.preferred_model, "
                        " model_params=excluded.model_params, "
                        " provider_incarnation_id=excluded.provider_incarnation_id, "
                        " provider_config_revision=excluded.provider_config_revision, "
                        " binding_epoch=excluded.binding_epoch, "
                        " updated_at=excluded.updated_at",
                        (target, *copied, next_epoch),
                    )
                    await self.advance_context_usage_binding_state_tx(
                        db,
                        session_id=target,
                        binding_epoch=next_epoch,
                        provider_id=copied[0],
                        model_id=copied[1],
                    )
                    await db.commit()
                    result = self._provider_binding_dict((*copied, next_epoch))

        await self._with_retry(_do)
        assert result is not None
        return (
            result
            if expected_binding_epoch is not None
            else self._legacy_provider_binding_dict(result)
        )

    async def get_code_session_provider_binding(
        self, base_session_id: str
    ) -> dict[str, Any]:
        authority = await self._get_provider_binding_authority(base_session_id)
        return self._legacy_provider_binding_dict(authority)

    async def _get_provider_binding_authority(
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
                "SELECT provider_id, preferred_model, model_params, "
                "provider_incarnation_id, provider_config_revision, binding_epoch "
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
                "provider_incarnation_id": None,
                "provider_config_revision": None,
                "binding_epoch": 0,
            }
        return self._provider_binding_dict(tuple(row))

    @staticmethod
    def _provider_binding_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        params: dict[str, Any] | None = None
        raw = row[2] if len(row) > 2 else None
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
            "provider_incarnation_id": row[3] if len(row) > 3 else None,
            "provider_config_revision": row[4] if len(row) > 4 else None,
            "binding_epoch": int(row[5] or 0) if len(row) > 5 else 0,
        }

    @staticmethod
    def _legacy_provider_binding_dict(binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": binding.get("provider_id"),
            "preferred_model": binding.get("preferred_model"),
            "model_params": binding.get("model_params"),
        }

    async def set_code_session_provider_binding(
        self,
        base_session_id: str,
        provider_id: str | None,
        preferred_model: str | None,
        model_params: dict[str, Any] | None = None,
        *,
        provider_incarnation_id: str | None = None,
        provider_config_revision: int | None = None,
        expected_binding_epoch: int | None = None,
    ) -> dict[str, Any]:
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

        if provider_id is None:
            provider_incarnation_id = None
            provider_config_revision = None
        params_json = (
            json.dumps(model_params, ensure_ascii=False)
            if model_params is not None
            else None
        )

        result: dict[str, Any] | None = None

        async def _do():
            nonlocal result
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        "SELECT binding_epoch FROM code_session_provider "
                        "WHERE base_session_id = ?",
                        (base_session_id,),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    current_epoch = int(row[0]) if row else 0
                    if (
                        expected_binding_epoch is not None
                        and current_epoch != expected_binding_epoch
                    ):
                        raise ProviderBindingConflict()
                    next_epoch = current_epoch + 1
                    await db.execute(
                        "INSERT INTO code_session_provider "
                        "(base_session_id, provider_id, preferred_model, model_params, "
                        " provider_incarnation_id, provider_config_revision, "
                        " binding_epoch, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, julianday('now')) "
                        "ON CONFLICT(base_session_id) DO UPDATE SET "
                        " provider_id=excluded.provider_id, "
                        " preferred_model=excluded.preferred_model, "
                        " model_params=excluded.model_params, "
                        " provider_incarnation_id=excluded.provider_incarnation_id, "
                        " provider_config_revision=excluded.provider_config_revision, "
                        " binding_epoch=excluded.binding_epoch, "
                        " updated_at=excluded.updated_at",
                        (
                            base_session_id,
                            provider_id,
                            preferred_model,
                            params_json,
                            provider_incarnation_id,
                            provider_config_revision,
                            next_epoch,
                        ),
                    )
                    await self.advance_context_usage_binding_state_tx(
                        db,
                        session_id=base_session_id,
                        binding_epoch=next_epoch,
                        provider_id=provider_id,
                        model_id=preferred_model,
                    )
                    await db.commit()
                    result = self._provider_binding_dict(
                        (
                            provider_id,
                            preferred_model,
                            params_json,
                            provider_incarnation_id,
                            provider_config_revision,
                            next_epoch,
                        )
                    )

        await self._with_retry(_do)
        assert result is not None
        return (
            result
            if expected_binding_epoch is not None
            else self._legacy_provider_binding_dict(result)
        )

    async def clear_bindings_for_provider(self, provider_id: str) -> int:
        """Deprecated: stale bindings are intentionally preserved.

        P5-S2 Phase 2 minor extension: 当 provider 被 IPC 删除时,孤儿绑定行
        (sid → 已删除的 provider) 需要清理,避免 resolution 拿到一个不存在的
        provider_id。返回删除的行数。

        Spec: frontend-ipc-surface Scenario "remove cleanup" + design.md
        "Resolution algorithm" 步骤 2.
        """
        return 0

    async def reconcile_provider_bindings(
        self,
        providers: dict[str, tuple[str, int]],
        *,
        registry_digest: str,
    ) -> None:
        """Attach identity to legacy rows, then durably mark reconciliation."""
        if not self._initialized:
            await self.initialize()
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                await db.execute("BEGIN IMMEDIATE")
                for provider_id, (incarnation_id, revision) in providers.items():
                    await db.execute(
                        "UPDATE code_session_provider SET "
                        "provider_incarnation_id=?, provider_config_revision=? "
                        "WHERE provider_id=? AND provider_incarnation_id IS NULL",
                        (incarnation_id, revision, provider_id),
                    )
                await db.execute(
                    "INSERT INTO provider_binding_reconcile_marker"
                    "(singleton, registry_digest, completed_at) VALUES(1, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "registry_digest=excluded.registry_digest, "
                    "completed_at=excluded.completed_at",
                    (registry_digest, time.time()),
                )
                await db.commit()

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
    # Todo list per session
    # ------------------------------------------------------------------

    async def replace_session_todos(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace an ordinary session's full todo projection.

        The on-disk table retains its historical name for migration
        compatibility; production callers use this mode-neutral API.
        """

        await self.replace_code_todos(session_id, items)

    async def get_session_todos(self, session_id: str) -> list[dict[str, Any]]:
        """Return an ordinary session's todo projection."""

        return await self.get_code_todos(session_id)

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

        Mirrors full-list TodoWrite semantics: the tool is
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
        *,
        target_directory: str | None = None,
        action_categories: list[str] | None = None,
        auto_confirmed: bool = False,
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

        steps_json = json.dumps(
            {
                "steps": steps or [],
                "target_directory": target_directory,
                "action_categories": action_categories or [],
                "auto_confirmed": bool(auto_confirmed),
            },
            ensure_ascii=False,
        )
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
                    decoded = json.loads(row[2]) if row[2] else []
                except (ValueError, TypeError):
                    decoded = []
                if isinstance(decoded, dict):
                    steps = decoded.get("steps")
                    if not isinstance(steps, list):
                        steps = []
                    target_directory = decoded.get("target_directory")
                    action_categories = decoded.get("action_categories")
                    if not isinstance(action_categories, list):
                        action_categories = []
                    auto_confirmed = bool(
                        decoded.get("auto_confirmed", False)
                    )
                else:
                    # Legacy rows stored a bare steps array.
                    steps = decoded if isinstance(decoded, list) else []
                    target_directory = None
                    action_categories = []
                    auto_confirmed = False
                return {
                    "session_id": row[0],
                    "rationale": row[1] or "",
                    "steps": steps,
                    "target_directory": target_directory,
                    "action_categories": action_categories,
                    "auto_confirmed": auto_confirmed,
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
