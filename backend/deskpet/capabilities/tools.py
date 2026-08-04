# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Model-visible tools for capability discovery and lifecycle operations.

The handlers are deliberately thin.  ``CapabilityHub`` remains the discovery
projection, ``CapabilityPackManager`` owns lifecycle transactions, and the
normal ToolRegistry permission/effect path owns authorization and receipts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from deskpet.types.task_grants import ResourceSelector
from deskpet.tools.capabilities import ToolExecutionContext

from .contracts import CapabilityDescriptor, CapabilityScope, CapabilityScopeKind
from .hub import CapabilityHub
from .manager import CapabilityPackManager
from .refresh_contracts import CapabilityOperationReceipt
from .search import CapabilitySearch
from .source import PackSourceRequest


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)


def _error(code: str, message: str) -> str:
    return _json({"ok": False, "error": {"code": code, "message": message}})


def _require_context(context: ToolExecutionContext | None) -> ToolExecutionContext:
    if context is None or not context.root_run_id or not context.run_id:
        raise ValueError("trusted root/run context is required")
    return context


def _scope_for(context: ToolExecutionContext) -> CapabilityScope:
    return CapabilityScope.for_run(
        context.root_run_id,
        project_root=context.workspace or context.write_scope_root,
    )


def _scope_key(
    context: ToolExecutionContext, scope_kind: CapabilityScopeKind
) -> str:
    key = _scope_for(context).key_for(scope_kind)
    if key is None:
        raise ValueError(
            f"{scope_kind} scope requires a committed project workspace"
        )
    return key


def _idempotency_key(
    operation: str,
    context: ToolExecutionContext,
    args: Mapping[str, Any],
) -> str:
    stable = context.effect_id or context.call_id
    payload = json.dumps(
        dict(args), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"capability:{operation}:{context.root_run_id}:{stable}:{digest}"


def _control_resource_resolver(
    service: "CapabilityToolService",
    action: str,
):
    """Resolve lifecycle authority from trusted layout facts and exact args."""

    def resolve(
        args: Mapping[str, Any],
        _context: ToolExecutionContext,
    ) -> tuple[ResourceSelector, ...]:
        layout = getattr(service.manager, "layout", None)
        managed_root = getattr(layout, "root", None)
        if managed_root is None:
            raise RuntimeError("capability managed root is unavailable")
        access = ("read",) if action in {"list", "search", "refresh"} else (
            "read",
            "write",
        )
        selectors: list[ResourceSelector] = [
            ResourceSelector(
                "capability_managed_root",
                str(managed_root),
                access,
            )
        ]
        if action in {"install", "update", "repair"}:
            source_type = str(args.get("source_type") or "local")
            uri = str(args.get("uri") or "").strip()
            if uri:
                request = PackSourceRequest(
                    source_type=source_type,
                    uri=uri,
                    revision=str(args.get("revision") or "latest"),
                    subdirectory=(
                        None
                        if args.get("subdirectory") in {None, ""}
                        else str(args["subdirectory"])
                    ),
                )
                source_resolver = getattr(
                    service.manager,
                    "source_resolver",
                    None,
                )
                resolve_declared = getattr(
                    source_resolver,
                    "resolve_declared_source",
                    None,
                )
                if callable(resolve_declared):
                    request = resolve_declared(request)
                source_type = request.source_type
                uri = request.uri
                parsed = urlsplit(uri)
                if parsed.scheme in {"http", "https"} and parsed.hostname:
                    selectors.append(
                        ResourceSelector("package_source", uri, ("read",))
                    )
                elif source_type in {"local", "builtin"}:
                    selectors.append(ResourceSelector.filesystem(uri, "read"))
                else:
                    selectors.append(
                        ResourceSelector("package_source", uri, ("read",))
                    )
        if action in {"install", "update", "repair", "rollback", "uninstall"}:
            pack_id = str(
                args.get("expected_pack_id") or args.get("pack_id") or "*"
            )
            selectors.append(
                ResourceSelector(
                    "system_change",
                    f"capability:{pack_id}",
                    (action,),
                )
            )
        return tuple(selectors)

    return resolve


def _descriptor_payload(descriptor: CapabilityDescriptor) -> dict[str, Any]:
    payload = {
        "version": descriptor.version.to_dict(),
        "visible_bindings": [
            binding.to_dict() for binding in descriptor.visible_bindings
        ],
        "executable": descriptor.executable,
        "installed": descriptor.installed,
        "tool_spec_fingerprints": list(descriptor.tool_spec_fingerprints),
        "catalog_stamp": descriptor.stamp.to_dict(),
    }
    source = descriptor.version.source
    if source.startswith("configured:"):
        alias_revision = source.removeprefix("configured:")
        alias, _separator, _revision = alias_revision.partition("@")
        payload["install_request"] = {
            "source_type": "configured",
            "uri": alias,
            "revision": "configured",
            "expected_pack_id": descriptor.version.capability_id,
        }
    return payload


def _search_descriptor_payload(
    descriptor: CapabilityDescriptor,
) -> dict[str, Any]:
    """Return the compact, model-actionable view used by search results.

    Full binding rows, immutable hashes, and catalog stamps remain available
    through ``capability_list``. Repeating them for every search hit can consume
    most of a local model's context before it has performed any useful work.
    """

    version = descriptor.version
    payload: dict[str, Any] = {
        "display_name": version.display_name,
        "description": version.description,
        "kind": version.kind,
        "source": version.source,
        "health": version.health,
        "aliases": list(version.aliases),
        "provider_tool_names": list(version.provider_tool_names),
        "permission_categories": list(version.permission_categories),
        "executable": descriptor.executable,
        "installed": descriptor.installed,
    }
    if descriptor.installed and not descriptor.executable:
        payload["next_action"] = {
            "tool": "tool_search",
            "query": version.capability_id,
            "instruction": (
                "This inventory result is not executable in the current "
                "request. Use tool_search, then tool_describe and "
                "tool_activate before calling it."
            ),
        }
    source = version.source
    if source.startswith("configured:"):
        alias_revision = source.removeprefix("configured:")
        alias, _separator, _revision = alias_revision.partition("@")
        payload["install_request"] = {
            "source_type": "configured",
            "uri": alias,
            "revision": "configured",
            "expected_pack_id": version.capability_id,
        }
    return payload


@dataclass(slots=True)
class CapabilityToolService:
    hub: CapabilityHub
    search: CapabilitySearch
    manager: CapabilityPackManager

    @staticmethod
    def _receipt_nonce(
        *,
        operation_id: str,
        context: ToolExecutionContext,
    ) -> str:
        return hashlib.sha256(
            (
                f"capability-refresh|{operation_id}|{context.root_run_id}|"
                f"{context.command_id}|{context.effect_id}"
            ).encode("utf-8")
        ).hexdigest()

    async def _record_operation_receipt(
        self,
        *,
        context: ToolExecutionContext,
        old_stamp: Any,
        action: str,
        operation_id: str,
        binding: Any,
        tool_spec_fingerprints: tuple[str, ...] = (),
    ) -> CapabilityOperationReceipt:
        version = await self.manager.store.get_version(
            binding.capability_id,
            binding.version,
            binding.manifest_hash,
        )
        fingerprints = (
            tuple(tool_spec_fingerprints)
            if tool_spec_fingerprints
            else (
                ()
                if version is None
                else tuple(version.expected_tool_fingerprints)
            )
        )
        command_id = context.command_id or (
            "command:"
            + hashlib.sha256(
                f"{context.run_id}|{context.call_id}".encode("utf-8")
            ).hexdigest()
        )
        receipt = CapabilityOperationReceipt(
            operation_id=operation_id,
            root_run_id=context.root_run_id,
            parent_command_id=command_id,
            parent_effect_id=context.effect_id,
            action=action,  # type: ignore[arg-type]
            refresh_nonce=self._receipt_nonce(
                operation_id=operation_id,
                context=context,
            ),
            old_stamp=old_stamp,
            published_binding_generation=int(binding.generation),
            affected_capability_ids=(binding.capability_id,),
            affected_version_refs=(
                f"{binding.capability_id}@{binding.version}:"
                f"{binding.manifest_hash}",
            ),
            affected_tool_spec_fingerprints=fingerprints,
            manifest_hashes=(binding.manifest_hash,),
        )
        return await self.manager.store.put_operation_receipt(receipt)

    async def list_visible(
        self, context: ToolExecutionContext | None
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        snapshot = await self.hub.snapshot(_scope_for(trusted))
        return {
            "ok": True,
            "snapshot_ref": snapshot.snapshot_ref,
            "catalog_stamp": snapshot.stamp.to_dict(),
            "capabilities": [
                _descriptor_payload(item) for item in snapshot.descriptors
            ],
        }

    async def search_visible(
        self, args: Mapping[str, Any], context: ToolExecutionContext | None
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        snapshot = await self.hub.snapshot(_scope_for(trusted))
        result = await self.search.search(
            snapshot=snapshot,
            root_run_id=trusted.root_run_id,
            query=str(args.get("query") or ""),
            limit=int(args.get("limit") or 10),
        )
        by_id = {
            item.version.capability_id: item for item in snapshot.descriptors
        }
        return {
            "ok": True,
            "snapshot_ref": result.snapshot_ref,
            "catalog_stamp": result.stamp.to_dict(),
            "semantic_used": result.semantic_used,
            "search_receipt_ref": result.receipt.receipt_id,
            "matches": [
                {
                    "capability_id": hit.capability_id,
                    "version": hit.version,
                    "score": hit.score,
                    "match_kind": hit.match_kind,
                    "executable": hit.executable,
                    "descriptor": (
                        _search_descriptor_payload(by_id[hit.capability_id])
                        if hit.capability_id in by_id
                        else None
                    ),
                }
                for hit in result.hits
            ],
        }

    async def refresh(
        self, context: ToolExecutionContext | None
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        snapshot = await self.hub.snapshot_and_acquire_lease(
            run_id=trusted.run_id,
            root_run_id=trusted.root_run_id,
            scope=_scope_for(trusted),
        )
        return {
            "ok": True,
            "status": "catalog_refreshed",
            "snapshot_ref": snapshot.snapshot_ref,
            "catalog_stamp": snapshot.stamp.to_dict(),
            "provider_tool_names": sorted(
                {
                    name
                    for descriptor in snapshot.descriptors
                    if descriptor.executable
                    for name in descriptor.version.provider_tool_names
                }
            ),
        }

    async def install(
        self,
        args: Mapping[str, Any],
        context: ToolExecutionContext | None,
        *,
        kind: str,
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        old_snapshot = await self.hub.snapshot(_scope_for(trusted))
        scope_kind = str(args.get("scope") or "run")
        if scope_kind not in {"run", "project", "user"}:
            raise ValueError("scope must be run, project, or user")
        request = PackSourceRequest(
            source_type=str(args.get("source_type") or "local"),
            uri=str(args.get("uri") or ""),
            revision=str(args.get("revision") or "local"),
            subdirectory=(
                str(args["subdirectory"]) if args.get("subdirectory") else None
            ),
        )
        kwargs = {
            "scope": scope_kind,
            "scope_key": _scope_key(
                trusted, scope_kind  # type: ignore[arg-type]
            ),
            "idempotency_key": _idempotency_key(kind, trusted, args),
            "root_run_id": trusted.root_run_id,
            "generated": bool(args.get("generated", False)),
            "expected_pack_id": (
                str(args["expected_pack_id"])
                if args.get("expected_pack_id")
                else None
            ),
        }
        if kind == "repair":
            kwargs.update(
                {
                    "parent_version": str(args.get("parent_version") or ""),
                    "parent_manifest_hash": str(
                        args.get("parent_manifest_hash") or ""
                    ),
                    "derived_from_receipt_ref": str(
                        args.get("failure_receipt_ref") or ""
                    ),
                }
            )
            result = await self.manager.repair(request, **kwargs)
        elif kind == "update":
            result = await self.manager.update(request, **kwargs)
        else:
            result = await self.manager.install(request, **kwargs)
        receipt = await self._record_operation_receipt(
            context=trusted,
            old_stamp=old_snapshot.stamp,
            action=kind,
            operation_id=result.operation.operation_id,
            binding=result.binding,
            tool_spec_fingerprints=tuple(result.tool_spec_fingerprints),
        )
        return {
            "ok": True,
            "operation_id": result.operation.operation_id,
            "status": result.operation.status,
            "phase": result.operation.phase,
            "pack_id": result.validation.manifest.id,
            "version": result.validation.manifest.version,
            "manifest_hash": result.validation.manifest.manifest_hash,
            "scope": result.binding.scope,
            "scope_key": result.binding.scope_key,
            "binding_generation": result.binding.generation,
            "install_path": str(result.install_path),
            "registry_revision": result.registry_revision,
            "tool_spec_fingerprints": list(result.tool_spec_fingerprints),
            "operation_receipt": receipt.to_dict(),
        }

    async def uninstall(
        self, args: Mapping[str, Any], context: ToolExecutionContext | None
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        old_snapshot = await self.hub.snapshot(_scope_for(trusted))
        scope_kind = str(args.get("scope") or "run")
        if scope_kind not in {"run", "project", "user"}:
            raise ValueError("scope must be run, project, or user")
        result = await self.manager.uninstall(
            pack_id=str(args.get("pack_id") or ""),
            scope=scope_kind,  # type: ignore[arg-type]
            scope_key=_scope_key(
                trusted, scope_kind  # type: ignore[arg-type]
            ),
            idempotency_key=_idempotency_key("uninstall", trusted, args),
            root_run_id=trusted.root_run_id,
        )
        receipt = await self._record_operation_receipt(
            context=trusted,
            old_stamp=old_snapshot.stamp,
            action="uninstall",
            operation_id=result.operation.operation_id,
            binding=result.binding,
        )
        return {
            "ok": True,
            "operation_id": result.operation.operation_id,
            "status": result.operation.status,
            "pack_id": result.binding.capability_id,
            "scope": result.binding.scope,
            "scope_key": result.binding.scope_key,
            "active": result.binding.active,
            "files_retained": result.files_retained,
            "operation_receipt": receipt.to_dict(),
        }

    async def rollback(
        self, args: Mapping[str, Any], context: ToolExecutionContext | None
    ) -> Mapping[str, Any]:
        trusted = _require_context(context)
        old_snapshot = await self.hub.snapshot(_scope_for(trusted))
        scope_kind = str(args.get("scope") or "run")
        if scope_kind not in {"run", "project", "user"}:
            raise ValueError("scope must be run, project, or user")
        result = await self.manager.rollback(
            pack_id=str(args.get("pack_id") or ""),
            target_version=str(args.get("target_version") or ""),
            target_manifest_hash=str(args.get("target_manifest_hash") or ""),
            scope=scope_kind,  # type: ignore[arg-type]
            scope_key=_scope_key(
                trusted, scope_kind  # type: ignore[arg-type]
            ),
            idempotency_key=_idempotency_key("rollback", trusted, args),
            root_run_id=trusted.root_run_id,
        )
        receipt = await self._record_operation_receipt(
            context=trusted,
            old_stamp=old_snapshot.stamp,
            action="rollback",
            operation_id=result.operation.operation_id,
            binding=result.binding,
        )
        return {
            "ok": True,
            "operation_id": result.operation.operation_id,
            "status": result.operation.status,
            "pack_id": result.binding.capability_id,
            "version": result.binding.version,
            "manifest_hash": result.binding.manifest_hash,
            "scope": result.binding.scope,
            "scope_key": result.binding.scope_key,
            "registry_revision": result.registry_revision,
            "operation_receipt": receipt.to_dict(),
        }


_LIFECYCLE_SOURCE_PROPERTIES = {
    "source_type": {
        "type": "string",
        "enum": ["builtin", "local", "configured", "git"],
        "default": "local",
    },
    "uri": {"type": "string"},
    "revision": {"type": "string", "default": "local"},
    "subdirectory": {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    },
    "scope": {
        "type": "string",
        "enum": ["run", "project", "user"],
        "default": "run",
    },
    "expected_pack_id": {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    },
    "generated": {"type": "boolean", "default": False},
}


def register_capability_tools(registry: Any, service: CapabilityToolService) -> None:
    """Register the model surface once; every mutation remains host-owned."""

    def unavailable(_args: dict[str, Any], _task_id: str) -> str:
        return _error(
            "trusted_context_required",
            "capability tools require prepared Harness execution",
        )

    async def list_handler(
        _args: dict[str, Any], context: ToolExecutionContext | None
    ) -> str:
        try:
            return _json(await service.list_visible(context))
        except Exception as exc:  # noqa: BLE001
            return _error(getattr(exc, "code", type(exc).__name__), str(exc))

    async def search_handler(
        args: dict[str, Any], context: ToolExecutionContext | None
    ) -> str:
        try:
            return _json(await service.search_visible(args, context))
        except Exception as exc:  # noqa: BLE001
            return _error(getattr(exc, "code", type(exc).__name__), str(exc))

    async def refresh_handler(
        _args: dict[str, Any], context: ToolExecutionContext | None
    ) -> str:
        try:
            return _json(await service.refresh(context))
        except Exception as exc:  # noqa: BLE001
            return _error(getattr(exc, "code", type(exc).__name__), str(exc))

    def lifecycle_handler(kind: str):
        async def invoke(
            args: dict[str, Any], context: ToolExecutionContext | None
        ) -> str:
            try:
                return _json(await service.install(args, context, kind=kind))
            except Exception as exc:  # noqa: BLE001
                return _error(getattr(exc, "code", type(exc).__name__), str(exc))

        return invoke

    async def uninstall_handler(
        args: dict[str, Any], context: ToolExecutionContext | None
    ) -> str:
        try:
            return _json(await service.uninstall(args, context))
        except Exception as exc:  # noqa: BLE001
            return _error(getattr(exc, "code", type(exc).__name__), str(exc))

    async def rollback_handler(
        args: dict[str, Any], context: ToolExecutionContext | None
    ) -> str:
        try:
            return _json(await service.rollback(args, context))
        except Exception as exc:  # noqa: BLE001
            return _error(getattr(exc, "code", type(exc).__name__), str(exc))

    registrations = (
        (
            "capability_list",
            "List the stamped capabilities actually visible to this task.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            list_handler,
            "read_file",
            True,
        ),
        (
            "capability_search",
            (
                "Search builtin tools, skills, MCP tools, and installed capability "
                "packs. Use this before claiming a required action capability is absent."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 10,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            search_handler,
            "read_file",
            True,
        ),
        (
            "capability_catalog_refresh",
            (
                "Refresh this run's stamped capability snapshot after a successful "
                "install or update, without asking the user to repeat the request."
            ),
            {"type": "object", "properties": {}, "additionalProperties": False},
            refresh_handler,
            "read_file",
            False,
        ),
    )
    lifecycle = (
        ("capability_install", "Install and atomically activate a capability pack.", "install"),
        ("capability_update", "Install a new immutable version and atomically switch the binding.", "update"),
    )
    for name, description, parameters, handler, permission, safe in registrations:
        action = {
            "capability_list": "list",
            "capability_search": "search",
            "capability_catalog_refresh": "refresh",
        }[name]
        registry.register(
            name=name,
            toolset="control",
            schema={
                "name": name,
                "description": description,
                "parameters": parameters,
            },
            handler=unavailable,
            context_handler=handler,
            permission_category=permission,
            source="builtin",
            concurrency_safe=safe,
            resource_scope_resolver=_control_resource_resolver(service, action),
            resource_scope_resolver_id=f"capability-control:{action}",
            resource_scope_resolver_version="v1",
        )
    for name, description, kind in lifecycle:
        properties = dict(_LIFECYCLE_SOURCE_PROPERTIES)
        required = ["uri"]
        registry.register(
            name=name,
            toolset="control",
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler=unavailable,
            context_handler=lifecycle_handler(kind),
            permission_category="skill_install",
            source="builtin",
            dangerous=True,
            concurrency_safe=False,
            resource_scope_resolver=_control_resource_resolver(service, kind),
            resource_scope_resolver_id=f"capability-lifecycle:{kind}",
            resource_scope_resolver_version="v1",
        )
    for name, description, handler, required in (
        (
            "capability_uninstall",
            "Disable one capability binding and stop its exact managed runtime.",
            uninstall_handler,
            ["pack_id"],
        ),
        (
            "capability_rollback",
            "Atomically switch a capability binding to a validated older version.",
            rollback_handler,
            ["pack_id", "target_version", "target_manifest_hash"],
        ),
    ):
        properties: dict[str, Any] = {
            "pack_id": {"type": "string"},
            "scope": {
                "type": "string",
                "enum": ["run", "project", "user"],
                "default": "run",
            },
        }
        if name == "capability_rollback":
            properties.update(
                {
                    "target_version": {"type": "string"},
                    "target_manifest_hash": {"type": "string"},
                }
            )
        registry.register(
            name=name,
            toolset="control",
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler=unavailable,
            context_handler=handler,
            permission_category="skill_install",
            source="builtin",
            dangerous=True,
            concurrency_safe=False,
            resource_scope_resolver=_control_resource_resolver(
                service,
                "rollback" if name == "capability_rollback" else "uninstall",
            ),
            resource_scope_resolver_id=f"capability-lifecycle:{name}",
            resource_scope_resolver_version="v1",
        )


__all__ = ["CapabilityToolService", "register_capability_tools"]
