# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Connection lifecycle, retries and the vector index."""

from __future__ import annotations

import asyncio
import random
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from deskpet.agent.context_usage import ContextUsageStateV2
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.memory_v2_schema import SESSION_TITLES_DDL
from deskpet.memory.schema import initialize_state_db

from .support import log

# P4-S2 hook 类型：(message_id, content) → awaitable None。
# MemoryManager / VectorWorker 在此接入 "消息落盘后异步跑 embedding"。
OnMessageWritten = Callable[[int, str], Awaitable[None]]


# 2026-08-09：保留会话 `default` 整套移除。原先它是"应用的兜底会话"——删除当前
# 会话后前端必然回落到它，因此不能按普通会话退役，代价是三处生命周期特判
# （clear 只清内容不退役 / 墓碑化自愈 / 纪元接管自愈，后者即 WBUI-DEF-COMP-01）。
# 现在所有会话一律用户新建、生命周期同构：没有兜底目标，就没有"必须永远可复活的
# 会话"，上述特判连同其缺陷面一并消失。空态由前端负责：无会话时发消息走
# `chat_v2 { session_id: "", new_session: true }`，由 task_scope 派一个 uuid 新会话。

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


class SessionDBCore:
    """Connection lifecycle, retries and the vector index."""

    if TYPE_CHECKING:

        @staticmethod
        async def _ensure_memory_binding_in_transaction(
            db: aiosqlite.Connection, *, session_id: str, user_id: str
        ) -> None: ...
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
        ) -> None: ...
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
            projection_event_id: str | None = ...,
            projection_owner_kind: str | None = ...,
            projection_owner_id: str | None = ...,
            projection_owner_generation: int | None = ...,
            projection_epoch: int | None = ...,
            projection_route_version: int | None = ...,
            projection_payload_hash: str | None = ...,
            user_id: str = ...,
            memory_authority: str = ...,
        ) -> tuple[int, bool]: ...
        async def _repair_excluded_memory_artifacts(self) -> None: ...
        async def advance_context_usage_binding_state_tx(
            self,
            db: aiosqlite.Connection,
            *,
            session_id: str,
            binding_epoch: int,
            provider_id: str | None,
            model_id: str | None,
            availability: str = ...,
            updated_at: float | None = ...,
            previous: ContextUsageStateV2 | None = ...,
        ) -> ContextUsageStateV2: ...
        async def append_message_if_epoch(
            self,
            session_id: str,
            role: str,
            content: str,
            *,
            expected_epoch: int,
            workflow_event_id: str,
            tool_call_id: str | None = ...,
            tool_calls: list[dict[str, Any]] | None = ...,
            reasoning_content: str | None = ...,
            skip_embed: bool = ...,
            projection_kind: str | None = ...,
            context_visibility: str | None = ...,
            root_run_id: str | None = ...,
            task_scope_id: str | None = ...,
            user_id: str = ...,
            memory_authority: str = ...,
        ) -> int | None: ...

    def __init__(
        self,
        db_path: str | Path,
        *,
        on_message_written: OnMessageWritten | None = None,
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
        self._on_message_written: OnMessageWritten | None = on_message_written
        # 2026-09-10：认知记忆 SDK（simple-harness-memory-sdk）整条移除。SessionDB
        # 回到「纯 Host 会话账本」的本职：只落 state.db，不再有可选记忆后端、
        # product_memory_outbox 投递器，也不再代理 recall/facts/twin。
        self._state_db_instance_id: str | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

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

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            cursor = await db.execute("SELECT instance_id FROM state_db_identity WHERE singleton=1")
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                self._state_db_instance_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO state_db_identity(singleton,instance_id,created_at) VALUES (1,?,?)",
                    (self._state_db_instance_id, time.time()),
                )
            else:
                self._state_db_instance_id = str(row[0])
            await db.commit()

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
            # 按需导入：可选依赖
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
                exc_info=True,
            )
            return False

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        """目前每次调用都是 short-lived connection，无持久 conn 可关。

        保留接口以便未来切 connection pool 时签名不变。
        """
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._initialized = False

    # ------------------------------------------------------------------
    # Human Memory Program v1 (fresh-only; independent from legacy Session)
    # ------------------------------------------------------------------

    def _human_memory_program_store(self):

        return HumanMemoryProgramStore(self._db_path)

    # ------------------------------------------------------------------
    # 记忆 SDK 委托（认知记忆；未接入时降级为空）
    # ------------------------------------------------------------------

    # 2026-09-10：``recall`` / ``get_facts`` / ``get_digital_twin`` 三个委托随
    # 认知记忆 SDK 一并移除——它们唯一的实现来自那个后端，留下只会返回空值的
    # 空壳等于假装还有记忆。

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

    #: Hard cap on a user-set session title (UI also enforces; this is the
    #: server-side backstop so a crafted ws message can't store a huge blob).
    MAX_TITLE_LEN = 80
