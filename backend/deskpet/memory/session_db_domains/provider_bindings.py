# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Session and code-session provider bindings."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from .core import SessionDBCore


class ProviderBindingConflict(RuntimeError):
    """A binding mutation lost its compare-and-swap race."""

    code = "provider_binding_conflict"

    def __init__(self) -> None:
        super().__init__(self.code)


class ProviderBindingMixin(SessionDBCore):
    """Session and code-session provider bindings."""

    __slots__ = ()

    # ---- P5-S2 code_session_provider binding -------------------------

    async def get_session_provider_binding(self, session_id: str) -> dict[str, Any]:
        """Return the provider/model override for an ordinary session.

        The underlying table keeps its historical name for an idempotent
        on-disk migration, but new production callers use this mode-neutral
        API.
        """

        return await self.get_code_session_provider_binding(session_id)

    async def get_session_provider_binding_authority(self, session_id: str) -> dict[str, Any]:
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
                        "SELECT binding_epoch FROM code_session_provider WHERE base_session_id = ?",
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

    async def get_code_session_provider_binding(self, base_session_id: str) -> dict[str, Any]:
        authority = await self._get_provider_binding_authority(base_session_id)
        return self._legacy_provider_binding_dict(authority)

    async def _get_provider_binding_authority(self, base_session_id: str) -> dict[str, Any]:
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
            json.dumps(model_params, ensure_ascii=False) if model_params is not None else None
        )

        result: dict[str, Any] | None = None

        async def _do():
            nonlocal result
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        "SELECT binding_epoch FROM code_session_provider WHERE base_session_id = ?",
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
