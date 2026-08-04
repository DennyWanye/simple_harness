"""Authority-phase Reminder ToolRegistry composition and atomic cutover."""

from __future__ import annotations

import inspect
import json
from typing import Any, Mapping, Protocol

from deskpet.tools.build_identity import (
    EffectClass,
    IdempotencyClass,
    ToolEffectMetadata,
    validate_core_registry_handler_set,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.types.task_grants import ResourceSelector

from .reminders import ReminderRunAuthority, ReminderServicePort


REMINDER_CREATE_TOOL_NAME = "reminder_create"
REMINDER_LIST_TOOL_NAME = "reminder_list"
REMINDER_CANCEL_TOOL_NAME = "reminder_cancel"
LEGACY_REMINDER_LIST_TOOL_NAME = "list_reminders"


def _reminder_resource_scope(
    tool_name: str,
    access: str,
):
    """Bind local reminder mutations to a deterministic application resource."""

    def resolve(
        _params: Mapping[str, Any],
        _context: ToolExecutionContext,
    ) -> tuple[ResourceSelector, ...]:
        return (
            ResourceSelector(
                "application",
                f"deskpet:{tool_name}",
                (access,),
            ),
        )

    return resolve

REMINDER_CREATE_SCHEMA: dict[str, Any] = {
    "name": REMINDER_CREATE_TOOL_NAME,
    "description": "Create a durable reminder for the current user.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            "schedule": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "once"},
                            "at_utc": {"type": "string", "format": "date-time"},
                        },
                        "required": ["kind", "at_utc"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "weekly"},
                            "weekday": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 7,
                            },
                            "local_time": {
                                "type": "string",
                                "pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$",
                            },
                            "timezone": {
                                "type": "string",
                                "format": "iana-time-zone",
                            },
                        },
                        "required": ["kind", "weekday", "local_time", "timezone"],
                        "additionalProperties": False,
                    },
                ]
            },
            "prepare_draft": {"type": "boolean", "default": False},
        },
        "required": ["text", "schedule"],
        "additionalProperties": False,
    },
}

REMINDER_LIST_SCHEMA: dict[str, Any] = {
    "name": REMINDER_LIST_TOOL_NAME,
    "description": "List durable reminders for the current user.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["active", "all"],
                "default": "active",
            },
            "cursor": {
                "oneOf": [
                    {"type": "string", "maxLength": 256},
                    {"type": "null"},
                ],
                "default": None,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

REMINDER_CANCEL_SCHEMA: dict[str, Any] = {
    "name": REMINDER_CANCEL_TOOL_NAME,
    "description": "Cancel one durable reminder using its current schedule version.",
    "parameters": {
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "expected_schedule_version": {
                "type": "integer",
                "minimum": 1,
            },
        },
        "required": ["reminder_id", "expected_schedule_version"],
        "additionalProperties": False,
    },
}

LEGACY_REMINDER_LIST_SCHEMA: dict[str, Any] = {
    "name": LEGACY_REMINDER_LIST_TOOL_NAME,
    "description": "Returns the active legacy in-process reminder list.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}


class ReminderAuthoritySelectorPort(Protocol):
    def resolve(self, context: ToolExecutionContext) -> ReminderRunAuthority: ...


async def _resolve_authority(
    selector: ReminderAuthoritySelectorPort,
    context: ToolExecutionContext,
) -> ReminderRunAuthority:
    authority = selector.resolve(context)
    if inspect.isawaitable(authority):
        authority = await authority
    if not isinstance(authority, ReminderRunAuthority):
        raise RuntimeError("reminder_authority_unavailable")
    return authority


async def _call_service(call: object) -> Mapping[str, Any]:
    result = await call if inspect.isawaitable(call) else call
    if not isinstance(result, Mapping):
        raise RuntimeError("reminder_service_result_invalid")
    return result


def build_companion_reminder_handlers(
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> Mapping[str, tuple[Any, Any]]:
    async def reject_untrusted(_args: Mapping[str, Any]) -> str:
        raise RuntimeError("reminder_requires_trusted_run_context")

    async def create(
        args: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> str:
        if not context.effect_id:
            raise RuntimeError("reminder_effect_identity_missing")
        authority = await _resolve_authority(authority_selector, context)
        result = await _call_service(
            service.create(authority, effect_id=context.effect_id, args=args)
        )
        return json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def list_(
        args: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> str:
        authority = await _resolve_authority(authority_selector, context)
        result = await _call_service(service.list(authority, args=args))
        return json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def cancel(
        args: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> str:
        if not context.effect_id:
            raise RuntimeError("reminder_effect_identity_missing")
        authority = await _resolve_authority(authority_selector, context)
        result = await _call_service(
            service.cancel(authority, effect_id=context.effect_id, args=args)
        )
        return json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return {
        REMINDER_CREATE_TOOL_NAME: (reject_untrusted, create),
        REMINDER_LIST_TOOL_NAME: (reject_untrusted, list_),
        REMINDER_CANCEL_TOOL_NAME: (reject_untrusted, cancel),
    }


def register_companion_reminder_tools(
    registry: Any,
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> None:
    handlers = build_companion_reminder_handlers(service, authority_selector)
    definitions = (
        (
            REMINDER_CREATE_TOOL_NAME,
            REMINDER_CREATE_SCHEMA,
            "core.reminder_create.v2",
            EffectClass.REVERSIBLE_LOCAL,
            "profile_reminder_create_v1",
            False,
        ),
        (
            REMINDER_LIST_TOOL_NAME,
            REMINDER_LIST_SCHEMA,
            "core.reminder_list.v2",
            EffectClass.READ_ONLY,
            "profile_reminder_query_v1",
            True,
        ),
        (
            REMINDER_CANCEL_TOOL_NAME,
            REMINDER_CANCEL_SCHEMA,
            "core.reminder_cancel.v2",
            EffectClass.REVERSIBLE_LOCAL,
            "profile_reminder_cancel_v1",
            False,
        ),
    )
    for (
        name,
        schema,
        handler_id,
        effect_class,
        normalizer,
        concurrency_safe,
    ) in definitions:
        handler, context_handler = handlers[name]
        registry.register(
            name,
            "core",
            schema,
            handler,
            context_handler=context_handler,
            permission_category=(
                "read_file"
                if effect_class is EffectClass.READ_ONLY
                else "write_file"
            ),
            source="builtin",
            dangerous=False,
            concurrency_safe=concurrency_safe,
            spec_version=handler_id,
            permission_policy_version="v1",
            completion_semantics="sync",
            stable_handler_id=handler_id,
            effect_metadata=ToolEffectMetadata(
                effect_class,
                IdempotencyClass.IDEMPOTENT,
                normalizer,
            ),
            resource_scope_resolver=_reminder_resource_scope(
                name,
                "read" if effect_class is EffectClass.READ_ONLY else "write",
            ),
            resource_scope_resolver_id=f"builtin:companion:{name}",
            resource_scope_resolver_version="v1",
        )


def build_companion_reminder_specs(
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> tuple[Any, ...]:
    """Build the exact V2 replacement set without mutating production."""

    from deskpet.tools.registry import ToolRegistry

    staging = ToolRegistry()
    register_companion_reminder_tools(staging, service, authority_selector)
    return tuple(staging.all_specs())


def register_legacy_reminder_tool(registry: Any, legacy_tool: Any) -> None:
    """Project the pre-cutover legacy handler into the checked Registry."""

    async def invoke(args: Mapping[str, Any]) -> str:
        result = legacy_tool.invoke(**dict(args))
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    registry.register(
        LEGACY_REMINDER_LIST_TOOL_NAME,
        "core",
        LEGACY_REMINDER_LIST_SCHEMA,
        invoke,
        permission_category="read_file",
        source="builtin",
        dangerous=False,
        concurrency_safe=True,
        spec_version="legacy.list_reminders.v1",
        permission_policy_version="v1",
        completion_semantics="sync",
        stable_handler_id="legacy.list_reminders.v1",
        effect_metadata=ToolEffectMetadata(
            EffectClass.READ_ONLY,
            IdempotencyClass.IDEMPOTENT,
            "retired_legacy_reminder_projection_v1",
        ),
    )


def cutover_companion_reminder_tools(
    registry: Any,
    *,
    expected_revision: int,
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> Any:
    """Retire legacy list and publish all three V2 handlers atomically."""

    replacements = build_companion_reminder_specs(service, authority_selector)
    return registry.compare_and_swap_authority_phase(
        expected_revision=expected_revision,
        expected_phase="legacy",
        target_phase="companion",
        replacements=replacements,
    )


def restore_durable_companion_reminder_tools(
    registry: Any,
    *,
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> Any:
    """Restore the durable Companion phase using only locally built specs."""

    replacements = build_companion_reminder_specs(service, authority_selector)
    return registry._restore_durable_authority_phase(
        expected_revision=registry.catalog_snapshot().revision,
        target_phase="companion",
        replacements=replacements,
    )


def ensure_companion_reminder_tools(
    registry: Any,
    *,
    service: ReminderServicePort,
    authority_selector: ReminderAuthoritySelectorPort,
) -> Mapping[str, Any]:
    """Reconcile the process-local Registry with durable Companion authority.

    The durable Growth cutover can already be complete when a fresh backend
    process starts, while the module-level Registry always starts in its
    legacy phase.  Replaying the durable coordinator is then intentionally a
    no-op, so the process-local handler set must be reconciled explicitly.
    """

    if registry.authority_phase == "legacy":
        cutover_companion_reminder_tools(
            registry,
            expected_revision=registry.catalog_snapshot().revision,
            service=service,
            authority_selector=authority_selector,
        )
    if registry.authority_phase != "companion":
        raise RuntimeError("reminder_registry_cutover_incomplete")
    validate_core_registry_handler_set(
        registry.all_specs(),
        phase="companion",
        include_planned=False,
    )
    names = set(registry.list_tools())
    expected = {
        REMINDER_CREATE_TOOL_NAME,
        REMINDER_LIST_TOOL_NAME,
        REMINDER_CANCEL_TOOL_NAME,
    }
    if LEGACY_REMINDER_LIST_TOOL_NAME in names or not expected.issubset(names):
        raise RuntimeError("reminder_registry_handler_set_invalid")
    return {"authority_phase": "companion", "handlers": sorted(expected)}


__all__ = [
    "REMINDER_CANCEL_SCHEMA",
    "REMINDER_CANCEL_TOOL_NAME",
    "REMINDER_CREATE_SCHEMA",
    "REMINDER_CREATE_TOOL_NAME",
    "REMINDER_LIST_SCHEMA",
    "REMINDER_LIST_TOOL_NAME",
    "LEGACY_REMINDER_LIST_SCHEMA",
    "LEGACY_REMINDER_LIST_TOOL_NAME",
    "ReminderAuthoritySelectorPort",
    "build_companion_reminder_handlers",
    "build_companion_reminder_specs",
    "cutover_companion_reminder_tools",
    "ensure_companion_reminder_tools",
    "register_companion_reminder_tools",
    "register_legacy_reminder_tool",
    "restore_durable_companion_reminder_tools",
]
