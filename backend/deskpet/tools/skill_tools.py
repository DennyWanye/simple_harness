"""Frozen Skill instruction activation through trusted Run context."""

from __future__ import annotations

import inspect
import hashlib
import json
import logging
import re
from typing import Any, Mapping, Protocol

from deskpet.tools.capabilities import ToolExecutionContext, canonical_hash

log = logging.getLogger(__name__)

_SAFE_RESOLUTION_ERROR = re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")


def instruction_content_hash(instruction: str) -> str:
    """SHA-256 of exact instruction bytes without loading capability authority."""

    if not isinstance(instruction, str):
        raise TypeError("instruction must be a string")
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()


class FrozenSkillInstructionResolver(Protocol):
    def resolve_frozen_instruction(
        self,
        *,
        run_id: str,
        skill_name: str,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]: ...


_resolver: FrozenSkillInstructionResolver | None = None


def bind(
    *,
    skill_resolver: FrozenSkillInstructionResolver | None = None,
    skill_loader: Any = None,
) -> None:
    """Bind only the Capability snapshot resolver; legacy Loader is ignored."""

    global _resolver
    _resolver = skill_resolver
    if skill_loader is not None and skill_resolver is None:
        log.warning("skill_tools.bind rejected legacy SkillLoader execution")


def is_bound() -> bool:
    return _resolver is not None


def _reject_resolution(*, run_id: str, skill_name: str, reason: str) -> str:
    log.warning(
        "sdk_skill_instruction_rejected run_id=%s skill=%s reason=%s",
        run_id,
        skill_name,
        reason,
    )
    return json.dumps({"ok": False, "error": reason}, ensure_ascii=False)


_SCHEMA: dict[str, Any] = {
    "name": "skill_invoke",
    "description": "Activate one Skill instruction from this Run's frozen catalog.",
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "minLength": 1},
            "arguments": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    },
}


async def _reject_untrusted(_args: dict[str, Any], _task_id: str = "") -> str:
    return json.dumps(
        {"ok": False, "error": "skill_invoke_requires_trusted_run_context"},
        ensure_ascii=False,
    )


async def _handle(
    args: dict[str, Any],
    context: ToolExecutionContext | None = None,
    task_id: str = "",
) -> str:
    del task_id
    if set(args) - {"skill_name", "arguments"}:
        return json.dumps(
            {"ok": False, "error": "skill_invoke_unknown_argument"},
            ensure_ascii=False,
        )
    name = args.get("skill_name")
    arguments = args.get("arguments", [])
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(arguments, list)
        or any(not isinstance(item, str) for item in arguments)
    ):
        return json.dumps(
            {"ok": False, "error": "skill_name or arguments are invalid"},
            ensure_ascii=False,
        )
    if _resolver is None:
        return json.dumps(
            {"ok": False, "error": "frozen skill resolver not bound"},
            ensure_ascii=False,
        )
    if context is None:
        return json.dumps(
            {"ok": False, "error": "skill_invoke_requires_trusted_run_context"},
            ensure_ascii=False,
        )
    run_id = str(context.run_id or context.root_run_id or "").strip()
    if not run_id:
        return json.dumps(
            {"ok": False, "error": "skill_invoke_run_identity_missing"},
            ensure_ascii=False,
        )
    snapshot_ref = str(context.capability_snapshot_ref or "").strip()
    if not snapshot_ref:
        return json.dumps(
            {"ok": False, "error": "skill_invoke_capability_snapshot_missing"},
            ensure_ascii=False,
        )
    try:
        result = _resolver.resolve_frozen_instruction(
            run_id=run_id,
            skill_name=name.strip(),
            arguments=tuple(arguments),
        )
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        raw_code = str(exc).strip()
        stable_code = (
            raw_code if _SAFE_RESOLUTION_ERROR.fullmatch(raw_code) else "unclassified"
        )
        log.exception(
            "sdk_skill_instruction_load_failed run_id=%s skill=%s "
            "error_type=%s stable_code=%s",
            run_id,
            name.strip(),
            type(exc).__name__,
            stable_code,
        )
        raise
    payload = dict(result)
    required = {
        "owner_key",
        "pack_id",
        "skill_id",
        "version",
        "manifest_hash",
        "content_hash",
        "instruction",
        "allowed_tools",
        "scope_id",
        "scope_hash",
        "capability_snapshot_ref",
        "run_catalog_content_stamp",
        "allowed_tool_refs",
        "effective_tool_ref_hashes",
        "effective_tool_refs_hash",
    }
    if set(payload) != required:
        return _reject_resolution(
            run_id=run_id,
            skill_name=name.strip(),
            reason="frozen_skill_resolution_invalid",
        )
    if (
        str(payload["capability_snapshot_ref"]) != snapshot_ref
        or str(payload["skill_id"]) != name.strip()
        or str(payload["scope_id"]) != f"skill-scope:{payload['scope_hash']}"
        or len(str(payload["scope_hash"])) != 64
        or not isinstance(payload["allowed_tools"], list)
        or any(not isinstance(item, str) for item in payload["allowed_tools"])
        or not isinstance(payload["allowed_tool_refs"], list)
        or not isinstance(payload["effective_tool_ref_hashes"], list)
        or any(not isinstance(item, Mapping) for item in payload["allowed_tool_refs"])
        or any(
            not isinstance(item, str) or len(item) != 64
            for item in payload["effective_tool_ref_hashes"]
        )
        or len(str(payload["effective_tool_refs_hash"])) != 64
    ):
        return _reject_resolution(
            run_id=run_id,
            skill_name=name.strip(),
            reason="frozen_skill_scope_identity_invalid",
        )
    allowed_ref_hashes = sorted(
        canonical_hash(dict(item)) for item in payload["allowed_tool_refs"]
    )
    exact_fact_fields = {
        "name",
        "stable_handler_id",
        "tool_spec_fingerprint",
        "schema_hash",
        "execution_build_identity",
        "dispatch_adapter_id",
        "dispatch_adapter_version",
        "dispatch_adapter_fingerprint",
        "effect_policy",
        "idempotency",
    }
    if any(set(item) != exact_fact_fields for item in payload["allowed_tool_refs"]):
        return _reject_resolution(
            run_id=run_id,
            skill_name=name.strip(),
            reason="frozen_skill_exact_tool_facts_invalid",
        )
    if allowed_ref_hashes != sorted(payload["effective_tool_ref_hashes"]):
        return _reject_resolution(
            run_id=run_id,
            skill_name=name.strip(),
            reason="frozen_skill_exact_tool_refs_invalid",
        )
    widening_controls = {
        "skill_invoke",
        "workflow_spawn",
        "decision",
        "capability_activate",
        "capability_install",
        "capability_update",
        "capability_build",
        "capability_repair",
        "capability_rollback",
        "capability_uninstall",
    }
    if widening_controls.intersection(payload["allowed_tools"]):
        return _reject_resolution(
            run_id=run_id,
            skill_name=name.strip(),
            reason="frozen_skill_scope_widening_control",
        )
    frozen_instruction_hash = instruction_content_hash(
        str(payload["instruction"])
    )
    activation_identity = {
        "schema": "skill-scope-activation/v1",
        "run_id": run_id,
        "scope_id": str(payload["scope_id"]),
        "scope_hash": str(payload["scope_hash"]),
        "capability_snapshot_ref": snapshot_ref,
        "instruction_content_hash": frozen_instruction_hash,
        "effective_tool_refs_hash": str(
            payload["effective_tool_refs_hash"]
        ),
    }
    activation = {
        "activation_id": (
            "skill-scope-activation:" + canonical_hash(activation_identity)
        ),
        "run_id": run_id,
        "root_run_id": str(context.root_run_id or run_id),
        "scope_id": str(payload["scope_id"]),
        "scope_hash": str(payload["scope_hash"]),
        "capability_snapshot_ref": snapshot_ref,
        "run_catalog_content_stamp": str(payload["run_catalog_content_stamp"]),
        "allowed_tool_names": sorted(set(payload["allowed_tools"])),
        "allowed_tool_refs": sorted(
            (dict(item) for item in payload["allowed_tool_refs"]),
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
        "effective_tool_ref_hashes": sorted(
            set(payload["effective_tool_ref_hashes"])
        ),
        "effective_tool_refs_hash": str(
            payload["effective_tool_refs_hash"]
        ),
        "instruction_content_hash": frozen_instruction_hash,
    }
    log.info(
        "sdk_skill_instruction_loaded run_id=%s skill=%s "
        "loaded_skill_body_count=1 instruction_content_hash=%s",
        run_id,
        name.strip(),
        frozen_instruction_hash,
    )
    return json.dumps(
        {
            "ok": True,
            "skill": name.strip(),
            **payload,
            "scope_activation": activation,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def register_static_tools(registry) -> None:
    registry.register(
        name="skill_invoke", toolset="control", schema=_SCHEMA,
        handler=_reject_untrusted, context_handler=_handle,
        permission_category="read_file", source="builtin",
        spec_version="core.skill_invoke.v2",
        permission_policy_version="v1",
    )


__all__ = [
    "FrozenSkillInstructionResolver",
    "bind",
    "is_bound",
    "register_static_tools",
]
