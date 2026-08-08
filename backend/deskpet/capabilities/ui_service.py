# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Capability Center application service.

The UI receives safe projections only.  Lifecycle mutations still go through
the transactional CapabilityPackManager; this service merely resolves a
visible binding, owns the exact background task started by the UI, and emits
authoritative operation projections.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .contracts import CapabilityBinding, CapabilityScope
from .manager import CapabilityPackManager
from .manifest import PACK_MANIFEST_NAME, parse_pack_manifest
from .source import PackSourceRequest
from .store import CapabilityOperationRecord, CapabilityStore
from .ui_projection import (
    project_capability_operation,
    project_capability_snapshot,
    project_pack_manifest,
)

CapabilityOperationNotifier = Callable[[Mapping[str, Any]], Awaitable[None]]
_SCOPE_ORDER = {"run": 0, "project": 1, "user": 2, "builtin": 3}
_RETRYABLE_KINDS = {"install", "update", "repair", "rollback", "uninstall"}
_UNSAFE_RETRY_PHASES = {"publish_intent", "catalog_swapped", "bound"}


class CapabilityCenterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _operation_id(idempotency_key: str) -> str:
    return hashlib.sha256(
        f"capability:{idempotency_key}".encode("utf-8")
    ).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CapabilityCenterError(
            f"missing_{field_name}", f"{field_name} is required"
        )
    return text


def _binding_from_projection(
    value: Mapping[str, Any] | None,
) -> CapabilityBinding | None:
    if value is None:
        return None
    try:
        return CapabilityBinding(
            binding_id=str(value["binding_id"]),
            capability_id=str(value["capability_id"]),
            version=str(value["version"]),
            manifest_hash=str(value["manifest_hash"]),
            scope=str(value["scope"]),  # type: ignore[arg-type]
            scope_key=str(value["scope_key"]),
            active=bool(value["active"]),
            generation=int(value["generation"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class CapabilityCenterService:
    """Read-model and exact task owner for the Capability Center."""

    def __init__(
        self,
        *,
        store: CapabilityStore,
        manager: CapabilityPackManager,
        hub: Any,
        notifier: CapabilityOperationNotifier | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.hub = hub
        self._notifier = notifier
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._task_lock = asyncio.Lock()

    @staticmethod
    def default_scope() -> CapabilityScope:
        # The Capability Center is session-global.  Run/project bindings are
        # shown only when the caller supplies their already-resolved scope.
        return CapabilityScope()

    async def list_capabilities(
        self, scope: CapabilityScope | None = None
    ) -> list[dict[str, Any]]:
        snapshot = await self.hub.snapshot(scope or self.default_scope())
        projected = project_capability_snapshot(snapshot)
        for descriptor, item in zip(
            snapshot.descriptors,
            projected,
            strict=True,
        ):
            record = await self.store.get_version(
                descriptor.version.capability_id,
                descriptor.version.version,
                descriptor.version.manifest_hash,
            )
            if record is None:
                continue
            manifest_path = record.install_path / PACK_MANIFEST_NAME
            try:
                raw = json.loads(
                    await asyncio.to_thread(
                        manifest_path.read_text,
                        encoding="utf-8",
                    )
                )
                manifest = parse_pack_manifest(raw)
            except (OSError, ValueError):
                continue
            if (
                manifest.id != descriptor.version.capability_id
                or manifest.version != descriptor.version.version
                or manifest.manifest_hash
                != descriptor.version.manifest_hash
            ):
                continue
            item["manifest"] = project_pack_manifest(manifest)
        return projected

    async def _rollback_target(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityBinding | None:
        if (
            operation.status != "succeeded"
            or operation.kind not in {"update", "repair", "rollback"}
        ):
            return None
        intent = await self.store.get_publish_intent_for_operation(
            operation.operation_id
        )
        if intent is None:
            return None
        target = _binding_from_projection(intent.old_binding)
        # 发布侧写入的 new_binding 投影不携带 binding_id/generation（只有
        # expected_generation，提交后实际 generation = expected_generation+1），
        # 不能走 _binding_from_projection 的整体解析，否则回滚入口结构性不可达。
        new_binding: Mapping[str, Any] = intent.new_binding or {}
        published_capability = str(new_binding.get("capability_id") or "")
        published_scope = str(new_binding.get("scope") or "")
        published_scope_key = str(new_binding.get("scope_key") or "")
        published_version = str(new_binding.get("version") or "")
        published_hash = str(new_binding.get("manifest_hash") or "")
        published_generation: int | None
        try:
            if new_binding.get("generation") is not None:
                published_generation = int(new_binding["generation"])
            elif new_binding.get("expected_generation") is not None:
                published_generation = int(new_binding["expected_generation"]) + 1
            else:
                published_generation = None
        except (TypeError, ValueError):
            published_generation = None
        if (
            target is None
            or not published_capability
            or not published_version
            or not published_hash
            or not target.active
            or target.scope == "builtin"
            or target.capability_id != published_capability
            or target.scope != published_scope
            or target.scope_key != published_scope_key
        ):
            return None
        current = await self.store.get_binding(
            published_scope,
            published_scope_key,
            published_capability,
        )
        if (
            current is None
            or not current.active
            or current.version != published_version
            or current.manifest_hash != published_hash
            or (
                published_generation is not None
                and current.generation != published_generation
            )
        ):
            return None
        version = await self.store.get_version(
            target.capability_id,
            target.version,
            target.manifest_hash,
        )
        if version is None or version.validation_status != "healthy":
            return None
        return target

    async def _uninstall_binding(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityBinding | None:
        if (
            operation.status != "succeeded"
            or not operation.pack_id
            or not operation.requested_scope
            or not operation.requested_scope_key
            or operation.requested_scope == "builtin"
            or operation.kind == "uninstall"
        ):
            return None
        binding = await self.store.get_binding(
            operation.requested_scope,
            operation.requested_scope_key,
            operation.pack_id,
        )
        return binding if binding is not None and binding.active else None

    @staticmethod
    def _retryable(operation: CapabilityOperationRecord) -> bool:
        if (
            operation.status not in {"failed", "cancelled"}
            or operation.kind not in _RETRYABLE_KINDS
            or operation.phase in _UNSAFE_RETRY_PHASES
        ):
            return False
        if operation.kind in {"install", "update", "repair"}:
            source = operation.request.get("source")
            return isinstance(source, Mapping)
        return bool(
            operation.pack_id
            and operation.requested_scope
            and operation.requested_scope_key
        )

    async def _project_operation(
        self,
        operation: CapabilityOperationRecord,
        *,
        authorization_mode: str,
    ) -> dict[str, Any]:
        validation = await self.store.list_validation_results(
            operation.operation_id
        )
        receipt = await self.store.get_operation_receipt(operation.operation_id)
        rollback_target = await self._rollback_target(operation)
        uninstall_binding = await self._uninstall_binding(operation)
        async with self._task_lock:
            task = self._tasks.get(operation.operation_id)
            cancellable = task is not None and not task.done()
        return project_capability_operation(
            operation,
            authorization_mode=authorization_mode,  # type: ignore[arg-type]
            verification_results=validation,
            receipt_ref=(
                receipt.operation_receipt_hash if receipt is not None else None
            ),
            cancellable=cancellable,
            retryable=self._retryable(operation),
            rollback_available=rollback_target is not None,
            uninstall_available=uninstall_binding is not None,
        )

    async def list_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        policy = await self.store.get_policy_state()
        operations = await self.store.list_operations(limit=limit)
        return [
            await self._project_operation(
                operation, authorization_mode=policy.mode
            )
            for operation in operations
        ]

    async def _notify_operation(self, operation_id: str) -> None:
        if self._notifier is None:
            return
        operation = await self.store.get_operation(operation_id)
        if operation is None:
            return
        policy = await self.store.get_policy_state()
        projection = await self._project_operation(
            operation, authorization_mode=policy.mode
        )
        try:
            await self._notifier(
                {
                    "type": "capability_operation_event",
                    "payload": {"operation": projection},
                }
            )
            if operation.status in {
                "succeeded",
                "failed",
                "cancelled",
                "unknown",
            }:
                await self._notifier(
                    {
                        "type": "capability_list_response",
                        "payload": {
                            "capabilities": await self.list_capabilities()
                        },
                    }
                )
        except Exception:
            # A disconnected UI must never change lifecycle settlement.
            return

    async def _drive_operation(
        self,
        operation_id: str,
        runner: Callable[[], Awaitable[Any]],
    ) -> None:
        try:
            await runner()
        except asyncio.CancelledError:
            raise
        except Exception:
            # CapabilityPackManager has already written the structured error.
            pass
        finally:
            current = asyncio.current_task()
            async with self._task_lock:
                if self._tasks.get(operation_id) is current:
                    self._tasks.pop(operation_id, None)
            await self._notify_operation(operation_id)

    async def _start_precreated(
        self,
        operation: CapabilityOperationRecord,
        runner: Callable[[], Awaitable[Any]],
    ) -> CapabilityOperationRecord:
        if operation.status == "running":
            async with self._task_lock:
                task = self._tasks.get(operation.operation_id)
                if task is None or task.done():
                    self._tasks[operation.operation_id] = asyncio.create_task(
                        self._drive_operation(operation.operation_id, runner),
                        name=f"capability-ui:{operation.operation_id[:12]}",
                    )
        await self._notify_operation(operation.operation_id)
        # Give the owned task one scheduling opportunity so a subsequent UI
        # cancel cannot strike before its coroutine enters the try/finally.
        await asyncio.sleep(0)
        latest = await self.store.get_operation(operation.operation_id)
        return latest or operation

    async def _visible_binding(
        self,
        capability_id: str,
        scope: CapabilityScope | None,
    ) -> CapabilityBinding:
        snapshot = await self.hub.snapshot(scope or self.default_scope())
        descriptor = snapshot.get(capability_id)
        if descriptor is None:
            raise CapabilityCenterError(
                "capability_not_visible",
                f"capability is not visible: {capability_id}",
            )
        bindings = sorted(
            (
                binding
                for binding in descriptor.visible_bindings
                if binding.active and binding.scope != "builtin"
            ),
            key=lambda item: (
                _SCOPE_ORDER.get(item.scope, 99),
                -item.generation,
            ),
        )
        if not bindings:
            raise CapabilityCenterError(
                "capability_not_mutable",
                "the visible capability has no mutable active binding",
            )
        return bindings[0]

    async def request_uninstall(
        self,
        *,
        capability_id: str,
        source_operation_id: str | None = None,
        scope: CapabilityScope | None = None,
    ) -> CapabilityOperationRecord:
        capability_id = _required_text(capability_id, "capability_id")
        binding: CapabilityBinding | None = None
        if source_operation_id:
            source_operation = await self.store.get_operation(source_operation_id)
            if (
                source_operation is None
                or source_operation.pack_id != capability_id
            ):
                raise CapabilityCenterError(
                    "operation_capability_mismatch",
                    "operation does not belong to the requested capability",
                )
            binding = await self._uninstall_binding(source_operation)
        if binding is None:
            binding = await self._visible_binding(capability_id, scope)
        idempotency_key = (
            "capability-center:uninstall:"
            f"{binding.capability_id}:{binding.scope}:{binding.scope_key}:"
            f"{binding.generation}"
        )
        operation_id = _operation_id(idempotency_key)
        request = {
            "pack_id": binding.capability_id,
            "scope": binding.scope,
            "scope_key": binding.scope_key,
        }
        operation = await self.store.create_operation(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            kind="uninstall",
            request=request,
            pack_id=binding.capability_id,
            requested_scope=binding.scope,
            requested_scope_key=binding.scope_key,
        )

        async def run() -> Any:
            return await self.manager.uninstall(
                pack_id=binding.capability_id,
                scope=binding.scope,
                scope_key=binding.scope_key,
                idempotency_key=idempotency_key,
            )

        return await self._start_precreated(operation, run)

    async def request_rollback(
        self,
        *,
        operation_id: str,
        capability_id: str | None = None,
    ) -> CapabilityOperationRecord:
        source = await self.store.get_operation(
            _required_text(operation_id, "operation_id")
        )
        if source is None:
            raise CapabilityCenterError(
                "operation_not_found", f"unknown operation: {operation_id}"
            )
        if capability_id and source.pack_id != capability_id:
            raise CapabilityCenterError(
                "operation_capability_mismatch",
                "operation does not belong to the requested capability",
            )
        target = await self._rollback_target(source)
        if target is None:
            raise CapabilityCenterError(
                "rollback_unavailable",
                "the exact previous healthy binding is no longer current",
            )
        current = await self.store.get_binding(
            target.scope, target.scope_key, target.capability_id
        )
        if current is None:
            raise CapabilityCenterError(
                "binding_not_found", "current binding is absent"
            )
        idempotency_key = (
            "capability-center:rollback:"
            f"{source.operation_id}:{current.generation}:"
            f"{target.version}:{target.manifest_hash}"
        )
        next_operation_id = _operation_id(idempotency_key)
        request = {
            "pack_id": target.capability_id,
            "target_version": target.version,
            "target_manifest_hash": target.manifest_hash,
            "scope": target.scope,
            "scope_key": target.scope_key,
        }
        operation = await self.store.create_operation(
            operation_id=next_operation_id,
            idempotency_key=idempotency_key,
            kind="rollback",
            request=request,
            pack_id=target.capability_id,
            requested_scope=target.scope,
            requested_scope_key=target.scope_key,
        )

        async def run() -> Any:
            return await self.manager.rollback(
                pack_id=target.capability_id,
                target_version=target.version,
                target_manifest_hash=target.manifest_hash,
                scope=target.scope,
                scope_key=target.scope_key,
                idempotency_key=idempotency_key,
            )

        return await self._start_precreated(operation, run)

    async def request_retry(
        self, *, operation_id: str
    ) -> CapabilityOperationRecord:
        source = await self.store.get_operation(
            _required_text(operation_id, "operation_id")
        )
        if source is None:
            raise CapabilityCenterError(
                "operation_not_found", f"unknown operation: {operation_id}"
            )
        if not self._retryable(source):
            raise CapabilityCenterError(
                "retry_unavailable",
                "this operation cannot be replayed safely",
            )
        idempotency_key = (
            "capability-center:retry:"
            f"{source.operation_id}:{int(source.updated_at * 1_000_000)}"
        )
        next_operation_id = _operation_id(idempotency_key)
        request = dict(source.request)
        operation = await self.store.create_operation(
            operation_id=next_operation_id,
            idempotency_key=idempotency_key,
            kind=source.kind,
            request=request,
            root_run_id=source.root_run_id,
            pack_id=source.pack_id,
            requested_scope=source.requested_scope,
            requested_scope_key=source.requested_scope_key,
        )

        async def run_install() -> Any:
            source_payload = request.get("source")
            if not isinstance(source_payload, Mapping):
                raise CapabilityCenterError(
                    "retry_source_missing",
                    "the preserved source request is unavailable",
                )
            pack_source = PackSourceRequest(
                source_type=str(source_payload.get("type") or ""),
                uri=str(source_payload.get("uri") or ""),
                revision=str(source_payload.get("revision") or ""),
                subdirectory=(
                    str(source_payload["subdirectory"])
                    if source_payload.get("subdirectory")
                    else None
                ),
            )
            kwargs: dict[str, Any] = {
                "scope": source.requested_scope,
                "scope_key": source.requested_scope_key,
                "idempotency_key": idempotency_key,
                "root_run_id": source.root_run_id,
                "generated": bool(request.get("generated", False)),
                "expected_pack_id": request.get("expected_pack_id"),
            }
            if source.kind == "repair":
                kwargs.update(
                    {
                        "parent_version": request.get("parent_version"),
                        "parent_manifest_hash": request.get(
                            "parent_manifest_hash"
                        ),
                        "derived_from_receipt_ref": request.get(
                            "derived_from_receipt_ref"
                        ),
                    }
                )
                return await self.manager.repair(pack_source, **kwargs)
            if source.kind == "update":
                return await self.manager.update(pack_source, **kwargs)
            return await self.manager.install(pack_source, **kwargs)

        async def run_rollback() -> Any:
            return await self.manager.rollback(
                pack_id=str(request.get("pack_id") or source.pack_id or ""),
                target_version=str(request.get("target_version") or ""),
                target_manifest_hash=str(
                    request.get("target_manifest_hash") or ""
                ),
                scope=str(
                    request.get("scope") or source.requested_scope or ""
                ),  # type: ignore[arg-type]
                scope_key=str(
                    request.get("scope_key")
                    or source.requested_scope_key
                    or ""
                ),
                idempotency_key=idempotency_key,
                root_run_id=source.root_run_id,
            )

        async def run_uninstall() -> Any:
            return await self.manager.uninstall(
                pack_id=str(request.get("pack_id") or source.pack_id or ""),
                scope=str(
                    request.get("scope") or source.requested_scope or ""
                ),  # type: ignore[arg-type]
                scope_key=str(
                    request.get("scope_key")
                    or source.requested_scope_key
                    or ""
                ),
                idempotency_key=idempotency_key,
                root_run_id=source.root_run_id,
            )

        runner = (
            run_install
            if source.kind in {"install", "update", "repair"}
            else run_rollback
            if source.kind == "rollback"
            else run_uninstall
        )
        return await self._start_precreated(operation, runner)

    async def request_cancel(
        self, *, operation_id: str
    ) -> CapabilityOperationRecord:
        operation_id = _required_text(operation_id, "operation_id")
        async with self._task_lock:
            task = self._tasks.get(operation_id)
        if task is None or task.done():
            raise CapabilityCenterError(
                "cancel_unavailable",
                "only an in-flight operation started by this UI can be cancelled",
            )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        operation = await self.store.get_operation(operation_id)
        if operation is not None and operation.status == "running":
            operation = await self.manager.cancel_operation(operation_id)
        if operation is None:
            raise CapabilityCenterError(
                "operation_not_found", f"unknown operation: {operation_id}"
            )
        await self._notify_operation(operation_id)
        return operation

    async def shutdown(self) -> None:
        """Cancel and reconcile only lifecycle tasks owned by this service."""

        async with self._task_lock:
            owned = list(self._tasks.items())
        for _operation_id_value, task in owned:
            if not task.done():
                task.cancel()
        if owned:
            await asyncio.gather(
                *(task for _operation_id_value, task in owned),
                return_exceptions=True,
            )
        for operation_id_value, _task in owned:
            operation = await self.store.get_operation(operation_id_value)
            if operation is not None and operation.status == "running":
                await self.manager.cancel_operation(operation_id_value)


__all__ = [
    "CapabilityCenterError",
    "CapabilityCenterService",
]
